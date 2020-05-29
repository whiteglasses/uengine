import socket
import random
from pymongo import ASCENDING
from datetime import timedelta
from time import time, sleep
from flask import json

from .abstract_queue import AbstractQueue
from .task import BaseTask

from uengine.utils import now
from uengine import ctx


class MongoQueue(AbstractQueue):

    def __init__(self, qcfq):
        super(MongoQueue, self).__init__(qcfq)
        self.task_collection = self.cfg.get("collection", "mq_tasks")
        self.subs_collection = self.task_collection + "_subs"
        self.prefix = self.cfg.get("channel", "ueq")
        self.channel_ttl = self.cfg.get("channel_ttl", 30)
        self.channel_ttl = timedelta(seconds=self.channel_ttl)
        self.ack_timeout = self.cfg.get("ack_timeout", 1)
        self.retries = self.cfg.get("retries", 5)

        fqdn = socket.getfqdn()
        rand = random.randint(0, 10000)
        self.msgchannel = f"{self.prefix}:{fqdn}:{rand}"
        self.ackchannel = f"{self.prefix}:{fqdn}:{rand}:ack"

        self.ensure_indexes()
        self.cleanup_channels()

    def ensure_indexes(self):
        self.coll_subs.ensure_index("updated_at")
        self.coll_subs.ensure_index("chan")
        self.coll_tasks.ensure_index([("chan", ASCENDING), ("created_at", ASCENDING)])

    def cleanup_channels(self):
        min_date = now() - self.channel_ttl
        self.coll_subs.delete_many({"updated_at": {"$lt": min_date}})

    @property
    def coll_subs(self):
        return ctx.db.meta.conn[self.subs_collection]

    @property
    def coll_tasks(self):
        return ctx.db.meta.conn[self.task_collection]

    def subscribe(self):
        self.coll_subs.replace_one({"chan": self.msgchannel},
                                   {"chan": self.msgchannel, "updated_at": now()}, upsert=True)

    def get_random_channel(self):
        min_date = now() - self.channel_ttl
        channels = list(self.coll_subs.find({"updated_at": {"$gt": min_date}}))
        if len(channels) == 0:
            return None, None
        rand = random.randrange(0, len(channels))
        chan_name = channels[rand]["chan"]
        ack_chan_name = chan_name + self.ACK_POSTFIX
        return chan_name, ack_chan_name

    def wait_ack(self, ins_id, chan):
        cancel_at = time() + self.ack_timeout
        query = {"ins_id": ins_id, "chan": chan}
        while time() < cancel_at:
            c = self.coll_tasks.find_one(query)
            if c:
                self.coll_tasks.delete_one(query)
                return c
            sleep(0.01)
        return None

    def ack(self, task_id):
        # generate ack
        ctx.log.debug("ACK {ins_id: %s, chan: %s}", task_id, self.ackchannel)
        self.coll_tasks.insert_one({"ins_id": task_id, "chan": self.ackchannel})
        # remove task doc
        ctx.log.debug("DELETE {_id: %s}", task_id)
        self.coll_tasks.delete_one({"_id": task_id})

    def publish(self, chan, data):
        res = self.coll_tasks.insert_one({"chan": chan, "data": data, "created_at": now()})
        return res.inserted_id

    def enqueue(self, task):
        if not isinstance(task, BaseTask):
            raise TypeError("only instances of Task are allowed")

        ack = None
        retries = self.retries
        while retries > 0:
            chan, ackchan = self.get_random_channel()
            if chan is None:
                return None
            ins_id = self.publish(chan, task.to_message())
            ack = self.wait_ack(ins_id, ackchan)
            if ack:
                break
            retries -= 1

        if ack:
            recvchan = ack["chan"]
            receiver = recvchan[len(self.prefix) + 1:-len(self.ACK_POSTFIX)]
            task.set_recv_by(receiver)

        return ack

    @property
    def tasks(self):
        self.subscribe()
        resub_at = now() + timedelta(seconds=10)
        while True:
            msgs = self.coll_tasks.find({"chan": self.msgchannel}).sort("created_at", ASCENDING)
            if msgs.count() > 0:
                for msg in msgs:
                    task = BaseTask.from_message(msg)
                    self.ack(task.id)
                    task.set_recv_by(self.msgchannel[len(self.prefix) + 1:])
                    yield task
            else:
                sleep(0.1)
            if now() > resub_at:
                self.subscribe()
                resub_at = now() + timedelta(seconds=10)