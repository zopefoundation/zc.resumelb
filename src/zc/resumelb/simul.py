##############################################################################
#
# Copyright Zope Foundation and Contributors.
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
"""Simulation

Client makes requests.  It has some number of outstanding requests. It
accomplishes this through a pool of greenlets.

Have some number of workers.

Each worker app has an lru cache of a given size.

There are some number of "sites".  Each site has a number of objects.

A request "requests" a set of objects for a site.  The worker app
sleeps .01 for every object not in it's cache.

The whole thing is controlled from a zookeeper node with properties:

- history lb worker history, which controls how many requests to
  perform between resume updates.
- cache_size Size of client lru cache
- clients # concurrent requests
- lambda exponential distribution parameter for selecting sites (.1)
- objects_per_site average number of objects per site.
- objects_per_request
- sites number of sites
- workers number of workers

"""
from pprint import pprint
import json
import logging
import os
import pylru
import random
import sys
import time
import threading
import zc.mappingobject
import zc.parse_addr
import zc.thread
import zc.zk
import zookeeper
import zope.testing.wait

logger = logging.getLogger(__name__)

class Sample:

    def __init__(self, size=1000, data=None):
        self.size = size
        self.data = data or []
        self.n = len(self.data)

    def add(self, v):
        self.n += 1
        try:
            self.data[self.n % self.size] = v
        except IndexError:
            self.data.append(v)

    def stats(self, prefix=None):
        data = sorted(self.data)
        return {
            'n': self.n,
            'mean': float(sum(data))/len(data),
            'min': data[0],
            '10': data[len(data)/10],
            '50': data[len(data)/2],
            '90': data[9*len(data)/10],
            'max': data[-1],
            }

    def __repr__(self):
        return ("%(n)s %(min)s %(10)s %(50)s(%(mean)s) %(90)s %(max)s" %
                self.stats(''))

class App:

    def __init__(self, properties):
        settings = zc.mappingobject.mappingobject(properties)
        self.cache_size = settings.cache_size
        self.cache = pylru.lrucache(self.cache_size)
        self.hitrates = Sample()

        @properties
        def changed(*a):
            if settings.cache_size != self.cache_size:
                self.cache_size = settings.cache_size
                self.cache.size(self.cache_size)

    def __call__(self, environ, start_response):
        n = nhit = nmiss = nevict = 0
        for oid in environ['PATH_INFO'].rsplit('/', 1)[1].split('_'):
            n += 1
            key = environ['HTTP_HOST'], oid
            if key in self.cache:
                nhit += 1
            else:
                nmiss += 1
                if len(self.cache) >= self.cache_size:
                    nevict += 1
                self.cache[key] = 1

                time.sleep(.01)

        result = ' '.join(map(str, (os.getpid(), n, nhit, nmiss, nevict)))+'\n'
        response_headers = [
            ('Content-type', 'text/plain'),
            ('Content-Length', str(len(result))),
            ]
        start_response('200 OK', response_headers)
        if n:
            self.hitrates.add(100.0*nhit/n)
            if self.hitrates.n % 1000 == 0:
                print os.getpid(), 'hitrate', self.hitrates

        return [result]

def worker(path):
    import logging
    logging.basicConfig()
    logger = logging.getLogger(__name__+'-worker')
    try:
        import zc.resumelb.zk
        zk = zc.zk.ZooKeeper()
        properties = zk.properties(path)
        app = App(properties)
        resume_file = 'resume%s.mar' % os.getpid()
        if os.path.exists(resume_file):
            os.remove(resume_file)
        zc.resumelb.zk.worker(
            app, {},
            zookeeper='127.0.0.1:2181',
            path=path+'/lb/workers',
            address='127.0.0.1:0',
            resume_file=resume_file,
            )
    except:
        logger.exception('worker')

def clients(path):
    logging.basicConfig()
    random.seed(0)

    import zc.zk
    zk = zc.zk.ZooKeeper()

    properties = zk.properties(path)
    settings = zc.mappingobject.mappingobject(properties)

    siteids = []

    @properties
    def _(*a):
        n = settings.sites
        siteids[:] = [0]
        for i in range(4):
            if n:
                siteids.extend(range(n))
            n /= 2

    wpath = path + '/lb/providers'
    zope.testing.wait.wait(lambda : zk.get_children(wpath))

    [waddr] = zk.get_children(wpath)
    waddr = zc.parse_addr.parse_addr(waddr)

    stats = zc.mappingobject.mappingobject(dict(
        truncated = 0,
        requests = 0,
        bypid = {},
        nobs = 0,
        nhits = 0,
        ))

    spath = path + '/stats'
    if not zk.exists(spath):
        zk.create(spath, '', zc.zk.OPEN_ACL_UNSAFE)

    import gevent.socket

    def do_request():
        siteid = random.choice(siteids)
        oids = set(
            int(random.gauss(0, settings.objects_per_site/4))
            for i in range(settings.objects_per_request)
            )
        socket = gevent.socket.create_connection(waddr)
        try:
            socket.sendall(
                request_template % dict(
                    data='_'.join(map(str, oids)),
                    host='h%s' % siteid,
                    )
                )
            response = ''
            while '\r\n\r\n' not in response:
                data = socket.recv(9999)
                if not data:
                    stats.truncated += 1
                    return
                response += data
            headers, body = response.split('\r\n\r\n')
            headers = headers.split('\r\n')
            status = headers.pop(0)
            headers = dict(l.strip().lower().split(':', 1)
                           for l in headers if ':' in l)
            content_length = int(headers['content-length'])
            while len(body) < content_length:
                data = socket.recv(9999)
                if not data:
                    stats.truncated += 1
                    return
                body += data

            pid, n, nhit, nmiss, nevict = map(int, body.strip().split())
            stats.requests += 1
            stats.nobs += n
            stats.nhits += nhit
            bypid = stats.bypid.get(pid)
            if bypid is None:
                bypid = stats.bypid[pid] = dict(nr=0, n=0, nhit=0)
            bypid['nr'] += 1
            bypid['n'] += n
            bypid['nhit'] += nhit
            logger.info(' '.join(map(str, (
                100*stats.nhits/stats.nobs,
                pid, n, nhit, 100*nhit/n,
                ))))
        finally:
            socket.close()

    def client():
        try:
            while 1:
                do_request()
        except:
            print 'client error'
            logging.getLogger(__name__+'-client').exception('client')

    greenlets = [gevent.spawn(client) for i in range(settings.clients)]


    # Set up notification of address changes.
    watcher = gevent.get_hub().loop.async()
    @watcher.start
    def _():
        print 'got update event'
        while settings.clients > len(greenlets):
            greenlets.append(gevent.spawn(client))
        while settings.clients < len(greenlets):
            greenlets.pop().kill()

    properties(lambda a: watcher.send())

    while 1:
        gevent.sleep(60.0)

request_template = """GET /%(data)s HTTP/1.1\r
Host: %(host)s\r
\r
"""

class LBLogger:

    def __init__(self):
        self.requests = Sample()
        self.nr = self.requests.n
        self.then = time.time()

    def write(self, line):
        status, _, t = line.split()[-3:]
        if status != '200':
            print 'error', line
        self.requests.add(float(t))
        if ((time.time() - self.then > 30)
            #or self.nr < 30
            ):
            pool = self.lb.pool
            self.then = time.time()
            print
            print time.ctime()
            print 'requests', self.requests.n-self.nr, self.requests
            self.nr = self.requests.n

            # print pool
            print 'backlogs', str(Sample(data=[
                worker.backlog for worker in pool.workers]))
            print 'resumes', str(Sample(data=[
                len(worker.resume) for worker in pool.workers]))
            print 'skilled', str(Sample(
                data=map(len, pool.skilled.values())))

            for rclass, skilled in sorted(pool.skilled.items()):
                if (len(skilled) > len(pool.workers) or
                    len(set(i[1] for i in skilled)) != len(skilled)
                    ):
                    print 'bad skilled', sorted(skilled, key=lambda i: i[1])

def main(args=None):
    if args is None:
        args = sys.argv[1:]
    [path] = args
    logging.basicConfig()

    zk = zc.zk.ZooKeeper()
    properties = zk.properties(path)
    settings = zc.mappingobject.mappingobject(properties)

    workers = [zc.thread.Process(worker, args=(path,))
               for i in range(settings.workers)]

    @zc.thread.Process(args=(path,))
    def lb(path):
        import logging
        logging.basicConfig()
        logger = logging.getLogger(__name__+'-lb')
        try:
            import zc.resumelb.zk
            lb, server, logger = zc.resumelb.zk.lbmain(
                ['-a127.0.0.1:0', '-l', LBLogger(),
                 '127.0.0.1:2181', '/simul/lb'],
                run=False)
            logger.lb = lb
            server.serve_forever()
        except:
            logger.exception('lb')

    clients_process = zc.thread.Process(clients, args=(path,))

    @properties
    def update(*a):
        while settings.workers > len(workers):
            workers.append(zc.thread.Process(worker, args=(path,)))
        while settings.workers < len(workers):
            workers.pop().terminate()

    threading.Event().wait() # sleep forever
