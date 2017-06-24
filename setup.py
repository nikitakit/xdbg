from setuptools import setup, find_packages
import sys

if sys.version_info < (3, 5):
    print("Error: xdbg requires Python 3.5 or later")
    sys.exit(1)

setup(
    name = "xdbg",
    version = "0.2",
    packages = find_packages(),
    install_requires=[
        "ipython",
        "byteplay3",
    ]
)
