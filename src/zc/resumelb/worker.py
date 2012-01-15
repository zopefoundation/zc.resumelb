import cStringIO
import errno
import gevent
import gevent.hub
import gevent.socket
import logging
import socket
import sys
import time
import zc.mappingobject
import zc.resumelb.util
import zc.resumelb.thread

logger = logging.getLogger(__name__)

class Worker(zc.resumelb.util.Worker):

    def __init__(self, app, addr, settings):
        self.app = app
        self.settings = zc.mappingobject.mappingobject(settings)
        self.resume = {}
        self.time_ring = []
        self.time_ring_pos = 0

        if settings.get('threads'):
            pool = zc.resumelb.thread.Pool(self.settings.threads)
            self.apply = pool.apply
        else:
            self.apply = lambda f, *a: f(*a)

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
        self.put((0, self.resume))

        while self.is_connected:
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
                gevent.spawn(self.handle, rno, self.start(rno), env)
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
        f.seek(0)

        def start_response(status, headers, exc_info=None):
            assert not exc_info # XXX
            self.put((rno, (status, headers)))

        try:
            for data in self.apply(self.app, env, start_response):
                self.put((rno, data))

            self.put((rno, ''))
            self.readers.pop(rno)

            elapsed = max(time.time() - env['zc.resumelb.time'], 1e-9)
            time_ring = self.time_ring
            time_ring_pos = rno % self.settings.history
            rclass = env['zc.resumelb.request_class']
            try:
                time_ring[time_ring_pos] = rclass, elapsed
            except IndexError:
                while len(time_ring) <= time_ring_pos:
                    time_ring.append((rclass, elapsed))

            if rno % self.settings.history == 0:
                byrclass = {}
                for rclass, elapsed in time_ring:
                    sumn = byrclass.get(rclass)
                    if sumn:
                        sumn[0] += elapsed
                        sumn[1] += 1
                    else:
                        byrclass[rclass] = [elapsed, 1]
                self.new_resume(dict(
                    (rclass, n/sum)
                    for (rclass, (sum, n)) in byrclass.iteritems()
                    ))

        except self.Disconnected:
            return # whatever

    def new_resume(self, resume):
        self.resume = resume
        self.put((0, resume))


def server_runner(app, global_conf, lb, history=500): # paste deploy hook
    logging.basicConfig(level=logging.INFO)
    host, port = lb.split(':')
    Worker(app, (host, int(port)), dict(history=history))

