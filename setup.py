##############################################################################
#
# Copyright (c) Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
name, version = 'zc.resumelb', '0'

install_requires = [
    'setuptools', 'gevent >=1.0b1', 'WebOb', 'zc.thread', 'zc.parse_addr',
    'zc.mappingobject', 'llist']
extras_require = dict(
    test=['zope.testing', 'bobo', 'manuel', 'WebTest', 'zc.zk [test]',
          'ZConfig', 'mock'])

entry_points = """
[console_scripts]
resumelb = zc.resumelb.lb:main

[paste.server_runner]
main = zc.resumelb.worker:server_runner
"""

from setuptools import setup

setup(
    author = 'Jim Fulton',
    author_email = 'jim@zope.com',
    license = 'ZPL 2.1',

    name = name, version = version,
    long_description=open('README.txt').read(),
    description = open('README.txt').read().strip().split('\n')[0],
    packages = [name.split('.')[0], name],
    namespace_packages = [name.split('.')[0]],
    package_dir = {'': 'src'},
    install_requires = install_requires,
    zip_safe = False,
    entry_points=entry_points,
    package_data = {name: ['*.txt', '*.test', '*.html']},
    extras_require = extras_require,
    tests_require = extras_require['test'],
    test_suite = name+'.tests.test_suite',
    )
