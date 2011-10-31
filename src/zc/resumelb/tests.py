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
        request.url, pid, len(body), hash(body))

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

def setUp(test):
    global pid
    pid = 6115

def test_suite():
    return unittest.TestSuite((
        manuel.testing.TestSuite(
            manuel.doctest.Manuel() + manuel.capture.Manuel(),
            'worker.test',
            setUp=setUp),
        ))

