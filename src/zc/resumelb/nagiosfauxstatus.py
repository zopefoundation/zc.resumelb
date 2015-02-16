"""Faux status server for testing the nagios
"""
import json
import gevent.queue
import sys
import time
import traceback
import zc.resumelb.lb

class Worker:
    backlog = 0

    def __init__(self, number):
        self.number = number
        self.write_queue = gevent.queue.Queue()

    @property
    def __name__(self):
        return "192.168.42.%s" % (self.number + 2)

    @property
    def mbacklog(self):
        return self.backlog

    @property
    def oldest_time(self):
        if self.backlog:
            return time.time() - self.backlog * .1

class Pool(zc.resumelb.lb.PoolBase):

    backlog = 0

    def __init__(self, nworkers=3):
        self.workers = [Worker(i) for i in range(nworkers)]

    def get(self, i):
        self.workers[i].backlog += 1
        self.backlog += 1

    def put(self, i):
        self.workers[i].backlog -= 1
        self.backlog -= 1

    @property
    def mbacklog(self):
        if len(self.workers):
            return self.backlog / len(self.workers)

    def handle(self, socket, _):
        try:
            status = json.dumps(self.status())+'\n'
            socket.sendall(status)
        except Exception:
            traceback.print_exc(sys.stderr)
