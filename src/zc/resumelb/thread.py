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
#
# Thread pool implementation based on: https://bitbucket.org/denis/
#   gevent-playground/src/49d1cdcdf643/geventutil/threadpool.py

import fcntl
import gevent.core
import gevent.event
import os
import Queue
import threading
import zc.thread

###############################################################################
# The following code is from the above URL:

# Simple wrapper to os.pipe() - but sets to non-block
def _pipe():
    r, w = os.pipe()
    fcntl.fcntl(r, fcntl.F_SETFL, os.O_NONBLOCK)
    fcntl.fcntl(w, fcntl.F_SETFL, os.O_NONBLOCK)
    return r, w

_core_pipe_read, _core_pipe_write = _pipe()

def _core_pipe_read_callback(event, evtype):
    try:
        os.read(event.fd, 1)
    except EnvironmentError:
        pass

gevent.core.event(gevent.core.EV_READ|gevent.core.EV_PERSIST,
                  _core_pipe_read, _core_pipe_read_callback).add()

def wake_gevent():
    os.write(_core_pipe_write, '\0')

# MTAsyncResult is greatly simplified from version in https://bitbucket.org/
#   denis/gevent-playground/src/49d1cdcdf643/geventutil/threadpool.py
class MTAsyncResult(gevent.event.AsyncResult):

    def set_exception(self, exception):
        gevent.event.AsyncResult.set_exception(self, exception)
        wake_gevent()

    def set(self, value=None):
        gevent.event.AsyncResult.set(self, value)
        wake_gevent()

#
###############################################################################

class Pool:

    def __init__(self, size):
        self.size = size
        self.queue = queue = Queue.Queue()

        def run():
            while 1:
                result, job, args = queue.get()
                try:
                    result.set(job(*args))
                except Exception, v:
                    if result is None:
                        return #closes
                    result.set_exception(v)

        run.__name__ = __name__

        self.threads = [zc.thread.Thread(run) for i in range(size)]

    def result(self, job, *args):
        result = MTAsyncResult()
        self.queue.put((result, job, args))
        return result

    def apply(self, job, *args):
        result = self.result(job, *args)
        return result.get()

    def close(self, timeout=1):
        for thread in self.threads:
            self.queue.put((None, None, None))
        for thread in self.threads:
            thread.join(timeout)

