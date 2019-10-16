from setuptools import setup, find_packages


setup(
    name="uengine",
    version="3.1.8",
    description="a micro webframework based on flask and pymongo",
    url="https://github.com/viert/uengine",
    author="Pavel Vorobyov",
    author_email="aquavitale@yandex.ru",
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "jinja2",
        "flask",
        "pymongo",
        "lazy_object_proxy",
        "ipython",
        "pylint",
        "mongomock"
    ],
    entry_points={
        "console_scripts": [
            "uengine=uengine.__main__:main",
        ]
    }
)
