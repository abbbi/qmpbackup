import os
from setuptools import setup

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name = "qmpbackup",
    version = "0.1",
    author = "Michael Ablassmeier",
    author_email = "abi@",
    description = ("Qemu incremental backup via QMP"),
    license = "GPL",
    keywords = "qemu incremental backup",
    url = "https://github.com/abbbi/qmpbackup",
    packages=['libqmpbackup'],
    long_description=read('README.md'),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Topic :: Utilities",
    ],
    scripts=['qmpbackup','qmprebase'],
)
