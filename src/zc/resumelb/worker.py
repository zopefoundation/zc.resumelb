import cStringIO
import datetime
import errno
import gevent
import gevent.hub
import gevent.server
import gevent.threadpool
import logging
import marshal
import os
import sys
import time
import zc.resumelb.util

logger = logging.getLogger(__name__)

def error(mess):
    logger.exception(mess)

import traceback
def error(mess):
    print >>sys.stderr, mess
    traceback.print_exc()

class Worker:

    def __init__(self, app, addr, history=999,
                 resume_file=None, threads=None, tracelog=None):
        self.app = app
        self.history = history
        self.worker_request_number = 0
        self.resume_file = resume_file
        self.resume = {}
        if self.resume_file and os.path.exists(self.resume_file):
            try:
                with open(self.resume_file) as f:
                    self.resume = marshal.load(f)
            except Exception:
                logger.exception('reading resume file')
        self.time_ring = []
        self.time_ring_pos = 0
        self.connections = set()

        if threads:
            self.threadpool = gevent.threadpool.ThreadPool(threads)
            pool_apply = self.threadpool.apply
        else:
            pool_apply = None

        def call_app(rno, env):
            response = [0]
            env['zc.resumelb.time'] = time.time()
            def start_response(status, headers, exc_info=None):
                assert not exc_info # XXX
                response[0] = (status, headers)
            body = app(env, start_response)
            return response[0], body

        if tracelog:
            info = logging.getLogger(tracelog).info
            no_message_format = '%s %s %s'
            message_format = '%s %s %s %s'
            now = datetime.datetime.now
            def log(rno, code, message=None):
                if message:
                    info(message_format, code, rno, now(), message)
                else:
                    info(no_message_format, code, rno, now())
            tracelog = log

            def call_app_w_tracelog(rno, env):
                log(rno, 'C')
                env['tracelog'] = (
                    lambda code, message=None: log(rno, code, message)
                    )
                response, body = call_app(rno, env)
                content_length = [v for (h, v) in response[1]
                                  if h.lower() == 'content-length']
                content_length = content_length[-1] if content_length else '?'
                log(rno, 'A', "%s %s" % (response[0], content_length))
                def body_iter():
                    try:
                        for data in body:
                            yield data
                    finally:
                        if hasattr(body, 'close'):
                            body.close()
                        log(rno, 'E')
                return response, body_iter()

            if threads:
                def call_app_w_threads(rno, env):
                    log(rno, 'I', env.get('CONTENT_LENGTH', 0))
                    return pool_apply(call_app_w_tracelog, (rno, env))
                self.call_app = call_app_w_threads
            else:
                self.call_app = call_app_w_tracelog
        elif threads:
            self.call_app = lambda rno, env: pool_apply(call_app, (rno, env))
        else:
            self.call_app = call_app

        self.tracelog = tracelog

        self.server = gevent.server.StreamServer(addr, self.handle_connection)
        self.server.start()
        self.addr = addr[0], self.server.server_port

    def update_settings(self, data):
        if 'history' in data:
            self.history = data['history']

    def stop(self):
        self.server.stop()
        if hasattr(self, 'threadpool'):
            self.threadpool.kill()

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
                    if data:
                        env = data
                        env['zc.resumelb.lb_addr'] = addr
                        gevent.spawn(
                            self.handle, conn, rno, conn.start(rno), env)
                else:
                    rput(data)
                    if data is None:
                        del readers[rno]
        except:
            error('handle_connection')

    def handle(self, conn, rno, get, env):
        try:
            if self.tracelog:
                query_string = env.get('QUERY_STRING')
                url = env['PATH_INFO']
                if query_string:
                    url += '?' + query_string
                self.tracelog(rno, 'B', '%s %s' % (env['REQUEST_METHOD'], url))

            env['wsgi.errors'] = sys.stderr

            # XXX We're buffering input. Maybe should to have option not to.
            f = cStringIO.StringIO()
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
            env['wsgi.input'] = f

            response, body = self.call_app(rno, env)
            try:
                requests = conn.readers
                if rno not in requests:
                    return # cancelled
                conn.put((rno, response))
                for data in body:
                    if rno not in requests:
                        return # cancelled
                    if data:
                        conn.put((rno, data))

                conn.put((rno, ''))

                elapsed = max(time.time() - env['zc.resumelb.time'], 1e-9)
                time_ring = self.time_ring
                time_ring_pos = rno % self.history
                rclass = env['zc.resumelb.request_class']
                try:
                    time_ring[time_ring_pos] = rclass, elapsed
                except IndexError:
                    while len(time_ring) <= time_ring_pos:
                        time_ring.append((rclass, elapsed))

                worker_request_number = self.worker_request_number + 1
                self.worker_request_number = worker_request_number
                if worker_request_number % self.history == 0:
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
            finally:
                if hasattr(body, 'close'):
                    body.close()
        except:
            error('handle_connection')
        finally:
            conn.end(rno)

    def new_resume(self, resume):
        self.resume = resume

        if self.resume_file:
            try:
                with open(self.resume_file, 'w') as f:
                    marshal.dump(resume, f)
            except Exception:
                logger.exception('reading resume file')

        for conn in self.connections:
            if conn.is_connected:
                try:
                    conn.put((0, resume))
                except zc.resumelb.util.Disconnected:
                    pass


def server_runner(app, global_conf, address, history=500, threads=0, **kw):
    # paste deploy hook
    logging.basicConfig(level=logging.INFO)
    host, port = address.split(':')
    Worker(app, (host, int(port)), dict(history=history),
           threads=threads and int(threads),
           **kw).server.serve_forever()

