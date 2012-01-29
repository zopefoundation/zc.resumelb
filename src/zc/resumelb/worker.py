import cStringIO
import errno
import gevent
import gevent.hub
import gevent.server
import gevent.threadpool
import logging
import sys
import time
import zc.mappingobject
import zc.resumelb.util

logger = logging.getLogger(__name__)

def error(mess):
    logger.exception(mess)

import traceback
def error(mess):
    print >>sys.stderr, mess
    traceback.print_exc()

class Worker:

    def __init__(self, app, addr, settings):
        self.app = app
        self.settings = zc.mappingobject.mappingobject(settings)
        self.worker_request_number = 0
        self.resume = {}
        self.time_ring = []
        self.time_ring_pos = 0
        self.connections = set()

        if settings.get('threads'):
            pool = gevent.threadpool.ThreadPool(settings['threads'])
            self.apply = pool.apply
        else:
            self.apply = apply

        self.server = gevent.server.StreamServer(addr, self.handle_connection)
        self.server.start()
        self.addr = addr[0], self.server.server_port

    def stop(self):
        self.server.stop()

    def handle_connection(self, sock, addr):
        try:
            conn = zc.resumelb.util.Worker()
            self.connections.add(conn)
            readers = conn.connected(sock, addr)
            conn.put((0, self.resume))
            while conn.is_connected:
                try:
                    rno, data = zc.resumelb.util.read_message(sock)
                except zc.resumelb.util.Disconnected:
                    conn.disconnected()
                    self.connections.remove(conn)
                    return

                rput = readers.get(rno)
                if rput is None:
                    env = data
                    env['zc.resumelb.time'] = time.time()
                    env['zc.resumelb.lb_addr'] = addr
                    gevent.spawn(self.handle, conn, rno, conn.start(rno), env)
                else:
                    rput(data)
        except:
            error('handle_connection')

    def handle(self, conn, rno, get, env):
        try:
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
                    return
                else:
                    break
            f.seek(0)

            response = [0]
            def start_response(status, headers, exc_info=None):
                assert not exc_info # XXX
                response[0] = (status, headers)

            try:
                body = self.apply(self.app, (env, start_response))
                conn.put((rno, response[0]))
                for data in body:
                    conn.put((rno, data))

                conn.put((rno, ''))

                elapsed = max(time.time() - env['zc.resumelb.time'], 1e-9)
                time_ring = self.time_ring
                time_ring_pos = rno % self.settings.history
                rclass = env['zc.resumelb.request_class']
                try:
                    time_ring[time_ring_pos] = rclass, elapsed
                except IndexError:
                    while len(time_ring) <= time_ring_pos:
                        time_ring.append((rclass, elapsed))

                worker_request_number = self.worker_request_number + 1
                self.worker_request_number = worker_request_number
                if worker_request_number % self.settings.history == 0:
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

            except zc.resumelb.util.Disconnected:
                return # whatever
        except:
            error('handle_connection')
        finally:
            conn.end(rno)

    def new_resume(self, resume):
        self.resume = resume
        for conn in self.connections:
            if conn.is_connected:
                try:
                    conn.put((0, resume))
                except zc.resumelb.util.Disconnected:
                    pass


def server_runner(app, global_conf, address, history=500, threads=1):
    # paste deploy hook
    logging.basicConfig(level=logging.INFO)
    host, port = address.split(':')
    Worker(app, (host, int(port)), dict(history=history, threads=threads)
           ).server.serve_forever()

