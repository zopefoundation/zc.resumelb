from bisect import bisect_left, insort
import gevent
import gevent.hub
import gevent.pywsgi
import gevent.server
import logging
import sys
import webob
import zc.mappingobject
import zc.resumelb.util

block_size = 1<<16

logger = logging.getLogger(__name__)

retry_methods = set(('GET', 'HEAD'))

default_disconnect_message = """
The server was unable to handle your request due to a transient failure.
Please try again.
"""

class LB:

    def __init__(self, worker_addr, classifier,
                 settings=None,
                 disconnect_message=default_disconnect_message,
                 ):
        self.classifier = classifier
        self.disconnect_message = disconnect_message
        self.pool = Pool(settings)
        self.worker_server = gevent.server.StreamServer(
            worker_addr, self.handle_worker)
        self.worker_server.start()

    def handle_worker(self, socket, addr):
        logger.info('new worker')
        Worker(self.pool, socket, addr)

    def handle_wsgi(self, env, start_response):
        rclass = self.classifier(env)
        logger.debug('wsgi: %s', rclass)

        while 1:
            worker = self.pool.get(rclass)
            try:
                result = worker.handle(rclass, env, start_response)
                self.pool.put(worker)
                return result
            except worker.Disconnected:
                # XXX need to be more careful about whether
                # start_response was called.
                if (int(env.get('CONTENT_LENGTH', 0)) == 0 and
                    env.get('REQUEST_METHOD') in retry_methods
                    ):
                    logger.info("retrying %s", env)
                else:
                    return webob.Response(
                        status = '502 Bad Gateway',
                        content_type= 'text/html',
                        body = ("<html><body>%s</body></html>"
                                % self.disconnect_message)
                        )(env, start_response)

class Pool:

    def __init__(self, settings=None):
        if settings is None:
            settings = dict(
                max_backlog = 40,
                unskilled_score = 1.0,
                )
        self.settings = settings
        self.workers = set()
        self.unskilled = [] # sorted([(uscore, poolworker)])
        self.skilled = {}   # rclass -> {(score, workers)}
        self.nskills = 0    # sum of resume lengths
        self.event = gevent.event.Event()

    def __repr__(self):
        outl = []
        out = outl.append
        out('Request classes:')
        for (rclass, skilled) in sorted(self.skilled.items()):
            out('  %s: %s'
                % (rclass,
                   ', '.join(
                       '%s(%s,%s)' %
                       (worker, score, worker.backlog)
                       for (score, worker) in sorted(skilled)
                   ))
                )
        out('Backlogs:')
        backlogs = {}
        for worker in self.workers:
            backlogs.setdefault(worker.backlog, []).append(worker)
        for backlog, workers in sorted(backlogs.items()):
            out('  %s: %r' % (backlog, sorted(workers)))
        return '\n'.join(outl)

    def new_resume(self, worker, resume=None):
        skilled = self.skilled
        unskilled = self.unskilled
        if worker in self.workers:
            if worker.backlog < self.settings['max_backlog']:
                del unskilled[bisect_left(unskilled, (worker.uscore, worker))]
            for rclass, score in worker.resume.iteritems():
                skilled[rclass].remove((score, worker))
            self.nskills -= len(worker.resume)
        else:
            self.workers.add(worker)
            worker.backlog = 0

        if resume is None:
            self.workers.remove(worker)
        else:
            worker.resume = resume
            self.nskills += len(resume)
            if resume:
                scores = sorted(resume.values())
                worker.unskilled_score = max(
                    self.settings['unskilled_score'],
                    scores[
                        min(
                            max(3, len(scores)/4),
                            len(scores)-1,
                            )
                        ] / 10.0
                    )
            else:
                worker.unskilled_score = (
                    self.settings['unskilled_score'] * (1.0 + self.nskills) /
                    len(self.workers))

            uscore = (
                worker.unskilled_score /
                (1.0 + worker.backlog)
                )
            worker.uscore = uscore
            insort(unskilled, (uscore, worker))
            for rclass, score in resume.iteritems():
                try:
                    skilled[rclass].add((score, worker))
                except KeyError:
                    skilled[rclass] = set(((score, worker), ))


        if self.unskilled:
            self.event.set()

    def remove(self, worker):
        self.new_resume(worker)

    def get(self, rclass, timeout=None):
        """Get a worker to handle a request class
        """

        unskilled = self.unskilled
        if not unskilled:
            self.event.wait(timeout)
            if not self.unskilled:
                return None

        # Look for a skilled worker
        best_score, unskilled_worker = unskilled[-1]
        best_worker = best_backlog = None
        max_backlog = self.settings['max_backlog']
        skilled = self.skilled.get(rclass, ())
        for score, worker in skilled:
            backlog = worker.backlog + 1
            if backlog > max_backlog:
                continue
            score /= backlog
            if (score > best_score
                or
                (best_worker is None and worker is unskilled_worker)
                ):
                best_score = score
                best_worker = worker
                best_backlog = backlog

        if best_worker is not None:
            uscore = best_worker.uscore
            del unskilled[bisect_left(unskilled, (uscore, best_worker))]
        else:
            uscore, best_worker = unskilled.pop()
            best_backlog = best_worker.backlog + 1
            self.nskills += 1
            resume = best_worker.resume
            score = max(uscore, self.settings['unskilled_score'] * 10)
            best_worker.resume[rclass] = score
            if skilled == ():
                self.skilled[rclass] = set(((score, best_worker),))
            else:
                skilled.add((score, best_worker))
            lresume = len(resume)
            uscore *= lresume/(lresume + 1.0)

        uscore *= best_backlog / (1.0 + best_backlog)
        best_worker.uscore = uscore
        best_worker.backlog = best_backlog
        if best_backlog < max_backlog:
            insort(unskilled, (uscore, best_worker))
        return best_worker

    def put(self, worker):
        backlog = worker.backlog
        if backlog < 1:
            return
        unskilled = self.unskilled
        max_backlog = self.settings['max_backlog']
        uscore = worker.uscore
        if backlog < max_backlog:
            del unskilled[bisect_left(unskilled, (uscore, worker))]

        uscore *= (backlog + 1.0) / backlog
        worker.uscore = uscore

        backlog -= 1
        worker.backlog = backlog

        if backlog < max_backlog:
            insort(unskilled, (uscore, worker))

        self.event.set()

class Worker(zc.resumelb.util.Worker):

    maxrno = (1<<32) - 1

    def __init__(self, pool, socket, addr):
        self.pool = pool
        self.nrequest = 0

        readers = self.connected(socket, addr)

        while self.is_connected:
            try:
                rno, data = zc.resumelb.util.read_message(socket)
            except gevent.GreenletExit:
                self.disconnected()
                return

            if rno == 0:
                pool.new_resume(self, data)
            else:
                readers[rno](data)

    def __repr__(self):
        return "worker-%s" % id(self)

    def handle(self, rclass, env, start_response):
        logger.debug('handled by %s', self.addr)

        env = env.copy()
        err = env.pop('wsgi.errors')
        input = env.pop('wsgi.input')
        env['zc.resumelb.request_class'] = rclass

        rno = self.nrequest + 1
        self.nrequest = rno % self.maxrno
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

    def disconnected(self):
        self.pool.remove(self)
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
    wsgi_addr, lb_addr = map(parse_addr, args)

    lb = LB(lb_addr, host_classifier)
    gevent.pywsgi.WSGIServer(wsgi_addr, lb.handle_wsgi).serve_forever()


