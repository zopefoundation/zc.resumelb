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

class LB:

    def __init__(self, worker_addr, classifier):
        self.classifier = classifier
        self.pool = Pool()
        gevent.server.StreamServer(worker_addr, self.handle_worker).start()

    def handle_worker(self, socket, addr):
        logger.info('new worker')
        Worker(self.pool, socket, addr)

    def handle_wsgi(self, env, start_response):
        rclass = self.classifier(env)
        logger.debug('wsgi: %s', rclass)

        while 1:
            worker = self.pool.get(rclass)
            try:
                return worker.handle(rclass, env, start_response)
            except worker.Disconnected:
                # XXX need to be more careful about whether
                # start_response was called.
                if int(env.get(CONTENT_LENGTH, None)) == 0:
                    logger.info("retrying %s", env)
                else:
                    raise
            finally:
                self.pool.put(worker)

class Pool:

    def __init__(self, max_backlog=40):
        self.max_backlog = max_backlog
        self.unskilled = [set() for i in range(max_backlog+1)]
        self.skilled = {}
        self.resumes = {}
        self.backlogs = {}
        self.event = gevent.event.Event()

    def __repr__(self):
        skilled = self.skilled
        backlogs = self.backlogs
        outl = []
        out = outl.append
        out('Request classes:')
        for rclass in sorted(skilled):
            out('  %s: %s'
                % (rclass,
                   ', '.join('%s(%s,%s)' % (worker, score, backlogs[worker])
                             for (score, worker) in skilled[rclass])
                   ))
        out('Backlogs:')
        for backlog, workers in enumerate(self.unskilled):
            if workers:
                out('  %s: %s' % (backlog, sorted(workers)))
        return '\n'.join(outl)

    def new_resume(self, worker, resume):
        skilled = self.skilled
        resumes = self.resumes
        try:
            old = resumes[worker]
        except KeyError:
            self.backlogs[worker] = 0
            self.unskilled[0].add(worker)
            self.event.set()
        else:
            for rclass, score in old.iteritems():
                workers = skilled[rclass]
                del workers[bisect.bisect_left(workers, (score, worker))]

        for rclass, score in resume.iteritems():
            bisect.insort(skilled.setdefault(rclass, []), (score, worker))

        resumes[worker] = resume

    def remove(self, worker):
        self.new_resume(worker, {})
        backlog = self.backlogs.pop(worker)
        self.unskilled[backlog].remove(worker)
        del self.resumes[worker]

    def get(self, rclass, timeout=None):
        """Get a worker to handle a request class
        """
        max_backlog = self.max_backlog
        backlogs = self.backlogs
        unskilled = self.unskilled
        while 1:

            # Look for a skilled worker
            best_score = 0
            for score, worker in reversed(self.skilled.get(rclass, ())):
                backlog = backlogs[worker] + 1
                if backlog > max_backlog:
                    continue
                score /= backlog
                if score <= best_score:
                    break
                best_score = score
                best_backlog = backlog
                best_worker = worker

            if best_score:
                unskilled[best_backlog-1].remove(best_worker)
                unskilled[best_backlog].add(best_worker)
                backlogs[best_worker] = best_backlog
                return best_worker

            # Look for an unskilled worker
            for backlog, workers in enumerate(unskilled):
                if workers:
                    worker = workers.pop()
                    backlog += 1
                    try:
                        unskilled[backlog].add(worker)
                    except IndexError:
                        workers.add(worker)
                    else:
                        backlogs[worker] = backlog
                        resume = self.resumes[worker]
                        if rclass not in resume:
                            self.resumes[worker][rclass] = 1.0
                            bisect.insort(self.skilled.setdefault(rclass, []),
                                          (1.0, worker))
                        return worker

            # Dang. Couldn't find a worker, either because we don't
            # have any yet, or because they're all too busy.
            self.event.clear()
            self.event.wait(timeout)
            if timeout is not None and not self.event.is_set():
                return None

    def put(self, worker):
        backlogs = self.backlogs
        unskilled = self.unskilled
        backlog = backlogs[worker]
        unskilled[backlog].remove(worker)
        backlog -= 1
        unskilled[backlog].add(worker)
        backlogs[worker] = backlog
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


