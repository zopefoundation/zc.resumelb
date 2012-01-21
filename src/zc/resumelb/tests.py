##############################################################################
#
# Copyright (c) Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.0 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
import bobo
import doctest
import gevent
import hashlib
import manuel.capture
import manuel.doctest
import manuel.testing
import os
import time
import unittest
import webob

pid = os.getpid()

@bobo.resource
def hi(request):
    body = request.environ['wsgi.input'].read()
    return "\n\n%s -> %s %s %s\n\n" % (
        request.url, pid, len(body), hashlib.sha1(body).hexdigest())

@bobo.query('/gen.html')
def gen(size=0):
    size = int(size)
    return webob.Response(
        app_iter=['hello world\n'*1000]*size,
        content_length=12000*size)

@bobo.query('/sleep.html')
def sleep(dur=0):
    time.sleep(float(dur))
    return 'hello world\n'

def app():
    return bobo.Application(bobo_resources=__name__)

def wait_until(func=None, timeout=9):
    if func is None:
        return lambda f: wait_until(f, timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if func():
            return
        gevent.sleep(.01)
    raise ValueError('timeout')

def setUp(test):
    global pid
    pid = 6115
    test.globs['wait_until'] = wait_until

def test_suite():
    return unittest.TestSuite((
        manuel.testing.TestSuite(
            manuel.doctest.Manuel() + manuel.capture.Manuel(),
            *(sorted(name for name in os.listdir(os.path.dirname(__file__))
                     if name.endswith('.test')
                     )),
            setUp=setUp),
        ))

