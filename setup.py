from setuptools import setup, find_packages
from pathlib import Path

setup(
    name="qt-repo-cache",
    version="0.1.0",
    author="Dave Dalcino",
    author_email="ddalcino@gmail.com",
    description="Maintains a JSON copy of the Updates.xml files at download.qt.io",
    long_description=(Path(__file__).parent / "README.md").read_text(),
    long_description_content_type="text/markdown",
    url="https://github.com/ddalcino/qt-repo-cache/",
    packages=find_packages(),
    install_requires=["aqtinstall>=2.1.0"],
    classifiers=[
        "Programming Language :: Python :: 3.7",
        "License :: OSI Approved :: MIT",
    ],
)