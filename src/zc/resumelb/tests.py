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
import gevent.server
import gevent.socket
import hashlib
import manuel.capture
import manuel.doctest
import manuel.testing
import marshal
import mock
import os
import pprint
import re
import time
import traceback
import unittest
import webob
import webtest
import zc.resumelb.lb
import zc.resumelb.util
import zc.resumelb.worker
import zc.zk.testing
import zope.testing.loggingsupport
import zope.testing.setupstack
import zope.testing.wait
import zope.testing.renormalizing

pid = os.getpid()

###############################################################################
# Bobo test app:

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

@bobo.query('/sneaky.html')
def sneaky():
    # The app_iter has empty strings!
    return webob.Response(
        app_iter=['', 'hello world\n'],
        content_length=12)

@bobo.query('/sleep.html')
def sleep(bobo_request, dur=0, size=1):
    time.sleep(float(dur))
    if 'tracelog' in bobo_request.environ:
        bobo_request.environ['tracelog'].log('test', 'T')
        bobo_request.environ['tracelog'].log('test2')

    size = int(size)
    if size > 1:
        r = webob.Response()
        r.app_iter = ('hello world\n' for i in range(size))
        r.content_length = 12*size
        r.content_type = 'text/html'
        return r
    else:
        return 'hello world\n'

@bobo.query('/gsleep.html')
def gsleep(dur=0):
    gevent.sleep(float(dur))
    return 'hello world\n'

def app():
    return bobo.Application(bobo_resources=__name__)

#
###############################################################################

def newenv(rclass, *a, **kw):
    r = webob.Request.blank(*a, **kw)
    env = r.environ.copy()
    inp = env.pop('wsgi.input')
    del env['wsgi.errors']
    env['zc.resumelb.request_class'] = rclass
    return env

def print_response(worker_socket, rno, size_only=False):
    d = zc.resumelb.util.read_message(worker_socket)
    try: rn, (status, headers) = d
    except:
      print 'wtf', `d`
      return
    #rn, (status, headers) = read_message(worker_socket)
    if rn != rno:
       raise AssertionError("Bad request numbers", rno, rn)
    print rno, status
    for h in sorted(headers):
        print "%s: %s" % h
    print
    size = 0
    while 1:
        rn, data = zc.resumelb.util.read_message(worker_socket)
        if rn != rno:
           raise AssertionError("Bad request numbers", rno, rn)
        if data:
            if size_only:
                size += len(data)
            else:
                print data,
        else:
            break
    if size_only:
       print size

def spawn(func, *a, **kw):
    def run_func():
        try:
            return func(*a, **kw)
        except Exception:
            traceback.print_exc()
            raise
    return gevent.spawn(run_func)

class FauxWorker:
    def __init__(self):
        self.server = server = gevent.server.StreamServer(
            ('127.0.0.1', 0), self.handle)
        server.start()
        self.addr = '127.0.0.1', server.server_port

    def handle(self, socket, addr):
        if not hasattr(self, 'socket'):
            self.socket = socket
        else:
            raise AssertionError("worker got too many connections", self.addr)

    def close(self):
        socket = self.socket
        del self.socket
        socket.close()

def test_loading_recipes_with_no_history_argument():
    """A bug as introduced that caused resumes to be loaded
    incorrectly when no history was given to the constructor.  It
    cause invalif perf_data to be initialized.

    >>> with open('resume.mar', 'w') as f:
    ...     marshal.dump(dict(a=1.0, b=2.0), f)

    >>> worker = zc.resumelb.worker.Worker(
    ...   zc.resumelb.tests.app(), ('127.0.0.1', 0),
    ...   resume_file='resume.mar')

    >>> pprint.pprint(worker.perf_data)
    {'a': (0, 1.0, 9999), 'b': (0, 0.5, 9999)}

    >>> worker.stop()
    """

def workers_generate_500s_for_bad_apps():
    """If an app is poorly behaved and raises exceptions, a worker
    will generate a 500.

    >>> def baddapp(*args):
    ...     raise Exception("I'm a bad-app")

    >>> worker = zc.resumelb.worker.Worker(baddapp, ('127.0.0.1', 0))
    >>> worker_socket = gevent.socket.create_connection(worker.addr)
    >>> zc.resumelb.util.read_message(worker_socket)
    (0, {})

    >>> env = newenv('', '/hi.html')
    >>> handler = zope.testing.loggingsupport.InstalledHandler(
    ...     'zc.resumelb.worker')
    >>> zc.resumelb.util.write_message(worker_socket, 1, env, '')
    >>> print_response(worker_socket, 1)
    1 500 Internal Server Error
    Content-Length: 23
    Content-Type: text/html; charset=UTF-8
    <BLANKLINE>
    A system error occurred

    >>> for record in handler.records:
    ...     print record.name, record.levelname
    ...     print record.getMessage()
    ...     if record.exc_info:
    ...         traceback.print_exception(*record.exc_info)
    ... # doctest: +ELLIPSIS
    zc.resumelb.worker ERROR
    Uncaught application exception for 1
    Traceback (most recent call last):
    ...
    Exception: I'm a bad-app

    >>> handler.uninstall()
    >>> worker.stop()
    """ #"

def Buffering_Temporary_Files_are_closed():
    """
    When a worker sends data to an lb faster than it can send it to a
    browser, the data gets buffered in a temporary file.  When the
    request is done, the tempirary fileis explicitly closed.

    >>> worker = FauxWorker()
    >>> lb = zc.resumelb.lb.LB([worker.addr], zc.resumelb.lb.host_classifier)
    >>> wait(lambda : hasattr(worker, 'socket'))
    >>> zc.resumelb.util.write_message(worker.socket, 0, {})
    >>> wait(lambda : lb.pool.workers)

Now make a request that doesn't read data, but waits until we tell it
to close it's iterator:

    >>> event = gevent.event.Event()
    >>> @spawn
    ... def client():
    ...     def start(*a):
    ...         print 'start_response', a
    ...     body = lb.handle_wsgi(
    ...         webob.Request.blank('/hi.html').environ, start)
    ...     event.wait()
    ...     body.close()
    ...     print 'closed body'

Now, we'll send it enough data to make it ise a temporary file:

    >>> [lbworker] = list(lb.pool.workers)
    >>> wait(lambda : lbworker.queues)

    >>> zc.resumelb.util.write_message(
    ...     worker.socket, 1, ('200 OK', []), 'x'*10000, 'x'*10000)

    >>> wait(lambda : hasattr(lbworker.queues[1].queue, 'file'))
    start_response ('200 OK', [])

    >>> f = lbworker.queues[1].queue.file
    >>> event.set()
    >>> wait(lambda : f.closed)
    closed body

    """

def zk_wsgi_server_output_timeout():
    r"""

    >>> import zc.resumelb.zk, zc.resumelb.tests, zc.zk
    >>> zk = zc.zk.ZooKeeper('zookeeper.example.com:2181')
    >>> zk.import_tree('''
    ... /test
    ...   /lb
    ...     /providers
    ...     /workers
    ...       /providers
    ... ''')

    >>> app = zc.resumelb.tests.app()
    >>> worker = zc.resumelb.zk.worker(
    ...     app, None, address='127.0.0.1:0', run=False,
    ...     zookeeper='zookeeper.example.com:2181', path='/test/lb/workers')

    >>> lb, server = zc.resumelb.zk.lbmain(
    ...     'zookeeper.example.com:2181 /test/lb -t.2')

    >>> [addr] = map(zc.parse_addr.parse_addr,
    ...              zk.get_children('/test/lb/providers'))

    Now we'll make a request, but not consume output.  It should
    timeout after .2 seconds:

    >>> with mock.patch('sys.stderr'):
    ...     with mock.patch('sys.stdout'):
    ...         sock = gevent.socket.create_connection(addr)
    ...         sock.sendall('GET /gen.html?size=999 HTTP/1.0\r\n\r\n')
    ...         gevent.sleep(1)

    The various output that get's generated in the timeout case is
    sub-optimal and, hopefully, likely to change, so we won't show it
    here.  The only clue we have that the timeout worked is that the
    output is less than we expect:

    >>> f = sock.makefile()
    >>> data = f.read()
    >>> len(data) < 999*12000
    True

    >>> worker.stop()
    >>> server.stop()
    >>> lb.stop()
    >>> zk.close()
    """

def flappy_set_worker_addrs_doesnt_cause_duplicate_connections():
    """
    >>> workers = [Worker() for i in range(2)]
    >>> import zc.resumelb.lb
    >>> lb = zc.resumelb.lb.LB([w.addr for w in workers],
    ...                        zc.resumelb.lb.host_classifier, variance=4)

    >>> wait(
    ...     lambda :
    ...     len([w for w in workers if hasattr(w, 'socket')]) == len(workers)
    ...     )
    >>> worker1, worker2 = [w.socket for w in workers]
    >>> from zc.resumelb.util import read_message, write_message

    >>> write_message(worker1, 0, {'h1.com': 10.0})
    >>> write_message(worker2, 0, {'h2.com': 10.0})

    >>> import gevent
    >>> gevent.sleep(.01) # Give resumes time to arrive

    >>> print lb.pool
    Request classes:
      h1.com: 127.0.0.1:49927(10.0,0)
      h2.com: 127.0.0.1:36316(10.0,0)
    Backlogs:
      overall backlog: 0 Decayed: 0 Avg: 0
      0: [127.0.0.1:49927, 127.0.0.1:36316]

    Now, we'll flap the worker addrs:

    >>> lb.set_worker_addrs([])
    >>> lb.set_worker_addrs([w.addr for w in workers])
    >>> gevent.sleep(.01) # Give resumes time to arrive

    >>> print lb.pool
    Request classes:
      h1.com: 127.0.0.1:49927(10.0,0)
      h2.com: 127.0.0.1:36316(10.0,0)
    Backlogs:
      overall backlog: 0 Decayed: 0 Avg: 0
      0: [127.0.0.1:49927, 127.0.0.1:36316]

    >>> for w in workers:
    ...     w.server.stop()
    >>> lb.stop()
    """

def test_classifier(env):
    return "yup, it's a test"

def setUp(test):
    zope.testing.setupstack.setUpDirectory(test)
    zope.testing.setupstack.context_manager(test, mock.patch('gevent.signal'))
    global pid
    pid = 6115
    test.globs['wait'] = zope.testing.wait.Wait(getsleep=lambda : gevent.sleep)
    old = zc.resumelb.worker.STRING_BUFFER_SIZE
    zope.testing.setupstack.register(
        test, setattr, zc.resumelb.worker, 'STRING_BUFFER_SIZE', old)
    zc.resumelb.worker.STRING_BUFFER_SIZE = 9999

    old = zc.resumelb.util.queue_size_bytes
    zope.testing.setupstack.register(
        test, setattr, zc.resumelb.util, 'queue_size_bytes', old)
    zc.resumelb.util.queue_size_bytes = 999
    test.globs['newenv'] = newenv
    test.globs['print_response'] = print_response
    test.globs['spawn'] = spawn
    test.globs['Worker'] = FauxWorker

def zkSetUp(test):
    setUp(test)
    zc.zk.testing.setUp(test)
    os.environ['COLUMNS'] = '70'

def zkTearDown(test):
    zc.zk.testing.tearDown(test)
    zope.testing.setupstack.tearDown(test)

def test_suite():
    e1 = r'127.0.0.1:\d+\s+1\s+0.7\s+[01]'
    e2 = r'127.0.0.1:\d+\s+0\s+0.0\s+-'
    return unittest.TestSuite((
        manuel.testing.TestSuite(
            manuel.doctest.Manuel(
                checker = zope.testing.renormalizing.OutputChecker([
                    (re.compile(r'127.0.0.1:\d+'), '127.0.0.1:P'),
                    (re.compile(r"'127.0.0.1', \d+"), "'127.0.0.1', P'"),
                    (re.compile(r"<socket fileno=\d+"), "<socket fileno=F"),
                    ])
                ) + manuel.capture.Manuel(),
            'lb.test', 'pool.test', 'worker.test', 'bytesizedqueue.test',
            'bufferedqueue.test', 'single_version.test',
            setUp=setUp, tearDown=zope.testing.setupstack.tearDown),

        manuel.testing.TestSuite(
            manuel.doctest.Manuel(
                checker = zope.testing.renormalizing.OutputChecker([
                    (re.compile(
                        r'\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] "(.+) \d+\.\d+'
                        ),
                     'ACCESS'),
                    (re.compile(r"u'pid': \d+"), "u'pid': PID"),
                    (re.compile(
                        '(' +
                        e1 + r'\s*\n\s*' + e2
                        + '|' +
                        e2 + r'\s*\n\s*' + e1
                        + ')\s*'
                        ), 'WORKERDETAILS'),
                    (re.compile(r"127.0.0.1:\d+"), "127.0.0.1:DDDD"),
                    ])
                ) + manuel.capture.Manuel(),
            'zk.test',
            setUp=zkSetUp, tearDown=zkTearDown),
        doctest.DocTestSuite(
            setUp=zkSetUp, tearDown=zope.testing.setupstack.tearDown,
            checker = zope.testing.renormalizing.OutputChecker([
                    (re.compile(r'127.0.0.1:\d+'), '127.0.0.1:P'),
                    ])),
        ))

