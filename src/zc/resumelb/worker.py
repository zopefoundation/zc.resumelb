import cStringIO
import errno
import gevent
import gevent.hub
import gevent.socket
import logging
import socket
import sys
import time
import zc.resumelb.util

logger = logging.getLogger(__name__)

class Worker(zc.resumelb.util.Worker):

    def __init__(self, app, addr, history):
        self.app = app
        self.resume = {}
        self.time_ring_size = history
        self.time_ring = []
        self.time_ring_pos = 0
        while 1:
            try:
                self.connect(addr)
            except socket.error, err:
                if err.args[0] == errno.ECONNREFUSED:
                    gevent.sleep(1)
                else:
                    raise

    def connect(self, addr):
        socket = gevent.socket.create_connection(addr)
        readers = self.connected(socket)

        while self.connected:
            try:
                rno, data = zc.resumelb.util.read_message(socket)
            except gevent.GreenletExit:
                self.disconnected()
                return

            rput = readers.get(rno)
            if rput is None:
                env = data
                env['zc.resumelb.time'] = time.time()
                env['zc.resumelb.lb_addr'] = self.addr
                gevent.Greenlet.spawn(self.handle, rno, self.start(rno), env)
            else:
                rput(data)

    def handle(self, rno, get, env):
        f = cStringIO.StringIO()
        env['wsgi.input'] = f
        env['wsgi.errors'] = sys.stderr

        # XXX We're buffering input.  It maybe should to have option not to.
        while 1:
            data = get()
            if data:
                f.write(data)
            elif data is None:
                # Request cancelled (or worker disconnected)
                self.end(rno)
                return
            else:
                break

        def start_response(status, headers, exc_info=None):
            assert not exc_info # XXX
            self.put((rno, (status, headers)))

        try:
            for data in self.app(env, start_response):
                self.put((rno, data))

            self.put((rno, ''))

            elapsed = time.time() - env['zc.resumelb.time']
            time_ring = self.time_ring
            time_ring_pos = rno % self.time_ring_size
            rclass = env['zc.resumelb.request_class']
            try:
                time_ring[time_ring_pos] = rclass, elapsed
            except IndexError:
                while len(time_ring) <= time_ring_pos:
                    time_ring.append((rclass, elapsed))

            if rno % self.time_ring_size == 0:
                byrclass = {}
                for rclass, elapsed in time_ring:
                    sumn = byrclass.get(rclass)
                    if sumn:
                        sumn[0] += elapsed
                        sumn[1] += 1
                    else:
                        byrclass[rclass] = [elapsed, 1]
                self.resume = dict(
                    (rclass, n/sum)
                    for (rclass, (sum, n)) in byrclass.iteritems()
                    )
                self.put((0, self.resume))

        except self.Disconnected:
            return # whatever

def server_runner(app, global_conf, lb, history=500): # paste deploy hook
    logging.basicConfig(level=logging.INFO)
    host, port = lb.split(':')
    Worker(app, (host, int(port)), history)
    gevent.hub.get_hub().switch()

