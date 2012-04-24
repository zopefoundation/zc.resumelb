import cStringIO
import datetime
import errno
import gevent
import gevent.hub
import gevent.server
import gevent.socket
import gevent.threadpool
import logging
import marshal
import os
import sys
import tempfile
import time
import zc.parse_addr
import zc.resumelb.util

logger = logging.getLogger(__name__)

def error(mess):
    logger.exception(mess)

import traceback
def error(mess):
    print >>sys.stderr, mess
    traceback.print_exc()

STRING_BUFFER_SIZE = 9999999

class Worker:

    def __init__(self, app, addr,
                 history=None, max_skill_age=None,
                 resume_file=None, threads=None, tracelog=None,
                 tracelog_key='tracelog'):
        self.app = app
        self.update_settings(dict(history=history, max_skill_age=max_skill_age))
        self.resume_file = resume_file
        self.perf_data = {} # rclass -> (gen, decayed times, decayed counts)
        self.generation = 0
        self.resume = {}
        if self.resume_file and os.path.exists(self.resume_file):
            try:
                with open(self.resume_file) as f:
                    self.resume = marshal.load(f)
            except Exception:
                logger.exception('reading resume file')
            else:
                for rclass, rpm in self.resume.iteritems():
                    if rpm > 0:
                        self.perf_data[rclass] = 0, 1.0/rpm, history

        self.connections = set()

        if threads:
            self.threadpool = gevent.threadpool.ThreadPool(int(threads))
            pool_apply = self.threadpool.apply
        else:
            pool_apply = None

        self.trno = 0
        def call_app(trno, env):
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
            def log(trno, code, message=None):
                if message:
                    info(message_format, code, trno, now(), message)
                else:
                    info(no_message_format, code, trno, now())
            tracelog = log

            class ApplicationTraceLog(object):

                def __init__(self, trno):
                    self.trno = trno

                def log(self, msg=None, code='-'):
                    log(self.trno, code, msg)


            def call_app_w_tracelog(trno, env):
                log(trno, 'C')
                env[tracelog_key] = ApplicationTraceLog(trno)
                response, body = call_app(trno, env)
                content_length = [v for (h, v) in response[1]
                                  if h.lower() == 'content-length']
                content_length = content_length[-1] if content_length else '?'
                log(trno, 'A', "%s %s" % (response[0], content_length))
                def body_iter():
                    try:
                        for data in body:
                            yield data
                    finally:
                        if hasattr(body, 'close'):
                            body.close()
                        log(trno, 'E')
                return response, body_iter()

            if threads:
                def call_app_w_threads(trno, env):
                    log(trno, 'I', env.get('CONTENT_LENGTH', 0))
                    return pool_apply(call_app_w_tracelog, (trno, env))
                self.call_app = call_app_w_threads
            else:
                self.call_app = call_app_w_tracelog
        elif threads:
            self.call_app = lambda trno, env: pool_apply(call_app, (trno, env))
        else:
            self.call_app = call_app

        self.tracelog = tracelog

        self.server = gevent.server.StreamServer(addr, self.handle_connection)
        self.server.start()
        self.addr = addr[0], self.server.server_port

    def update_settings(self, data):
        history = data.get('history')
        if history is None:
            history = 9999
        else:
            history = int(history)
        self.history = history

        max_skill_age = data.get('max_skill_age')
        if max_skill_age is None:
            max_skill_age = history * 10
        else:
            max_skill_age = int(max_skill_age)
        self.max_skill_age = max_skill_age

        self.decay = 1 - 1.0/history

    def stop(self):
        self.server.stop()
        if hasattr(self, 'threadpool'):
            self.threadpool.kill()

    def shutdown(self):
        self.server.close()
        while 1:
            if [conn for conn in self.connections if conn.readers]:
                gevent.sleep(.01)
            else:
                break
        self.stop()

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
                self.trno += 1
                trno = self.trno
                query_string = env.get('QUERY_STRING')
                url = env['PATH_INFO']
                if query_string:
                    url += '?' + query_string
                self.tracelog(trno, 'B', '%s %s' % (env['REQUEST_METHOD'], url))
            else:
                trno = 0

            env['wsgi.errors'] = sys.stderr

            # XXX We're buffering input. Maybe should to have option not to.
            content_length = int(env.get('CONTENT_LENGTH', 0))
            if content_length > STRING_BUFFER_SIZE:
                f = tempfile.TemporaryFile(suffix='.rlbwi')
            else:
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

            response, body = self.call_app(trno, env)
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

                # Update resume
                elapsed = max(time.time() - env['zc.resumelb.time'], 1e-9)
                rclass = env['zc.resumelb.request_class']
                generation = self.generation + 1
                perf_data = self.perf_data.get(rclass)
                if perf_data:
                    rgen, rtime, rcount = perf_data
                else:
                    rgen = generation
                    rtime = rcount = 0

                decay = self.decay ** (generation - rgen)
                rgen = generation
                rtime = rtime * decay + elapsed
                rcount = rcount * decay + 1

                self.generation = generation
                self.perf_data[rclass] = rgen, rtime, rcount
                self.resume[rclass] = rcount / rtime

                if generation % self.history == 0:
                    min_gen = generation - self.max_skill_age
                    for rclass in [r for (r, d) in self.perf_data.iteritems()
                                   if d[0] < min_gen]:
                        del self.perf_data[rclass]
                        del self.resume[rclass]

                    self.new_resume(self.resume)

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

def get_resume(addr):
    socket = gevent.socket.create_connection(addr)
    rno, data = zc.resumelb.util.read_message(socket)
    socket.close()
    assert rno == 0, rno
    return data

def get_resume_main(args=None):
    if args is None:
        args = sys.argv[1:]
    from pprint import pprint
    for arg in args:
        print arg
        pprint(get_resume(zc.parse_addr.parse_addr(arg)), width=1)

def server_runner(app, global_conf, address, **kw):
    # paste deploy hook
    logging.basicConfig(level=logging.INFO)
    host, port = address.split(':')
    Worker(app, (host, int(port)), **kw).server.serve_forever()

