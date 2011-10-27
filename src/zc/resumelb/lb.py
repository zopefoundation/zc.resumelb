import bisect
import gevent
import gevent.hub
import gevent.pywsgi
import gevent.server
import logging
import sys
import zc.resumelb.util

block_size = 1<<16

logger = logging.getLogger(__name__)

class Server:

    def __init__(self, wsgi_addr, worker_addr, classifier):
        self.workers = {None: []}
        self.workers_event = gevent.event.Event()
        self.wsgi_addr = wsgi_addr
        self.worker_addr = worker_addr
        self.classifier = classifier
        self.worker_server = gevent.server.StreamServer(
            worker_addr, self.handle_worker)
        self.worker_server.start()
        self.wsgi_server = gevent.pywsgi.WSGIServer(wsgi_addr, self.handle_wsgi)
        self.wsgi_server.start()

    def handle_worker(self, socket, addr):
        logger.info('new worker')
        Worker(self, socket, addr)

    def handle_wsgi(self, env, start_response):
        rclass = self.classifier(env)
        logger.debug('wsgi: %s', rclass)
        env['zc.resumelb.request_class'] = rclass

        while 1:
            workers = self.workers.get(rclass)
            if workers:
                worker = workers[-1][1]
            else:
                workers = self.workers.get(None)
                if workers:
                    worker = workers[-1][1]
                    assert rclass not in worker.resume
                    worker.unregister()
                    worker.resume[rclass] = 1
                    worker.register()
                else:
                    self.workers_event.clear()
                    self.workers_event.wait()
                    continue

            try:
                return worker.handle(env, start_response)
            except worker.Disconnected:
                # XXX need to be more careful about whether
                # start_response was called.
                if int(env.get(CONTENT_LENGTH, 0)) == 0:
                    logger.info("retrying %s", env)
                else:
                    raise

class Worker(zc.resumelb.util.Worker):

    def __init__(self, server, socket, addr):
        self.server = server
        self.nrequest = 0
        self.resume = {}

        readers = self.connected(socket, addr)
        self.register()

        while self.connected:
            try:
                rno, data = zc.resumelb.util.read_message(socket)
            except gevent.GreenletExit:
                self.disconnected()
                return

            if rno == 0:
                self.unregister()
                self.resume = data
                self.register()
            else:
                readers[rno](data)

    def handle(self, env, start_response):
        logger.debug('handled by %s', self.addr)

        env = env.copy()
        err = env.pop('wsgi.errors')
        input = env.pop('wsgi.input')

        rno = self.nrequest = self.nrequest + 1
        get = self.start(rno)

        self.put((rno, env))
        content_length = int(env.get('CONTENT_LENGTH', 0))
        while content_length > 0:
            data = input.read(min(content_length, block_size))
            if not data:
                # Browser disconnected, cancel the request
                self.put((rno, None))
                self.end(rno)
                return
            content_length -= len(data)
            self.put((rno, data))
        self.put((rno, ''))

        data = get()
        if data is None:
            raise self.Disconnected()
        logger.debug('start_response %r', data)
        start_response(*data)

        def content():
            while 1:
                data = get()
                if data:
                    logger.debug('yield %r', data)
                    yield data
                elif data is None:
                    raise self.Disconnected()
                else:
                    self.end(rno)
                    break

        return content()

    def register(self):
        resume = self.resume
        self.score = - sum(resume.itervalues())
        workers_by = self.server.workers

        def _register(workers, score):
            item = score, self
            index = bisect.bisect_left(workers, item)
            workers.insert(index, item)

        _register(workers_by[None], self.score)

        for site, score in resume.iteritems():
            workers = workers_by.get(site)
            if workers is None:
                workers = workers_by[site] = []
            _register(workers, score)

        self.server.workers_event.set()

    def unregister(self):
        workers_by = self.server.workers

        def _unregister(workers, score):
            index = bisect.bisect_left(workers, (score, self))
            del workers[index]

        _unregister(workers_by[None], self.score)
        for site, score in self.resume.iteritems():
            _unregister(workers_by[site], score)

    def disconnected(self):
        self.unregister()
        zc.resumelb.util.Worker.disconnected(self)

def parse_addr(addr):
    host, port = addr.split(':')
    return host, int(port)

def host_classifier(env):
    return env.get("HTTP_HOST", '')

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    logging.basicConfig(level=logging.INFO)
    laddr, waddr = args
    Server(parse_addr(laddr), parse_addr(waddr), host_classifier)

    gevent.hub.get_hub().switch()


