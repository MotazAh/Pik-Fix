# -*- coding: utf-8 -*-
# Author: Runsheng Xu <rxx3386@ucla.edu>
# License: TDG-Attribution-NonCommercial-NoDistrib


from os.path import dirname, realpath
from setuptools import setup, find_packages, Distribution


def _read_requirements_file():
    """Return the elements in requirements.txt."""
    req_file_path = '%s/requirements.txt' % dirname(realpath(__file__))
    with open(req_file_path) as f:
        return [line.strip() for line in f]


setup(
    name='PikFix',
    version='0.1.0',
    packages=find_packages(),
    url='https://github.com/DerrickXuNu/Pik-Fix',
    license='MIT',
    author='Runsheng Xu',
    author_email='rxx3386@ucla.edu',
    description='Pik-Fix Implementation',
    long_description=open("README.md").read(),
    install_requires=_read_requirements_file(),
)
