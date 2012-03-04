from bisect import bisect_left, insort
import gevent
import gevent.hub
import gevent.pywsgi
import gevent.socket
import llist
import logging
import sys
import webob
import zc.resumelb.util

block_size = 1<<16

logger = logging.getLogger(__name__)

retry_methods = set(('GET', 'HEAD'))

default_disconnect_message = '''
<html><meta http-equiv="refresh" content="1"><body>
The server was unable to handle your request due to a transient failure.
Please try again.
</body></html>
'''

class LB:

    def __init__(self, worker_addrs, classifier,
                 disconnect_message=default_disconnect_message,
                 **pool_settings):
        self.classifier = classifier
        self.disconnect_message = disconnect_message
        self.pool = Pool(**pool_settings)
        self.update_settings = self.pool.update_settings
        self.workletts = {}
        self.set_worker_addrs(worker_addrs)

    def set_worker_addrs(self, addrs):
        addrs = set(addrs)
        workletts = self.workletts
        old = list(workletts)
        for addr in addrs:
            if addr not in workletts:
                workletts[addr] = gevent.spawn(self.connect, addr, workletts)

        for addr in old:
            if addr not in addrs:
                workletts.pop(addr)

    connect_sleep = 1.0
    def connect(self, addr, workletts):
        while addr in workletts:
            try:
                socket = gevent.socket.create_connection(addr)
                Worker(self.pool, socket, addr)
            except Exception:
                logger.exception('lb connecting to %r', addr)
                gevent.sleep(self.connect_sleep)

    def stop(self):
        for g in self.workletts.values():
            g.kill()
        self.workletts.clear()

    def handle_wsgi(self, env, start_response):
        rclass = self.classifier(env)
        logger.debug('wsgi: %s', rclass)

        while 1:
            worker = self.pool.get(rclass)
            try:
                result = worker.handle(rclass, env, start_response)
                self.pool.put(worker)
                return result
            except zc.resumelb.util.Disconnected:
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
                        body = self.disconnect_message
                        )(env, start_response)

class Pool:

    def __init__(self, max_backlog=40, unskilled_score=1.0):
        self.max_backlog = max_backlog
        self.unskilled_score = unskilled_score

        self.workers = set()
        self.unskilled = llist.dllist()
        self.skilled = {}   # rclass -> {(score, workers)}
        self.event = gevent.event.Event()

    def update_settings(self, settings):
        for name in ('max_backlog', 'unskilled_score'):
            if name in settings:
                setattr(self, name, settings[name])

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
        workers = self.workers

        if worker in workers:
            for rclass, score in worker.resume.iteritems():
                skilled[rclass].remove((score, worker))
            if resume is None:
                workers.remove(worker)
                if worker.lnode is not None:
                    unskilled.remove(worker.lnode)
                    worker.lnode = None
                return
        else:
            worker.backlog = 0
            workers.add(worker)
            worker.lnode = unskilled.appendleft(worker)

        worker.resume = resume
        for rclass, score in resume.iteritems():
            try:
                skilled[rclass].add((score, worker))
            except KeyError:
                skilled[rclass] = set(((score, worker), ))

        if unskilled:
            self.event.set()

    def remove(self, worker):
        self.new_resume(worker)

    def get(self, rclass, timeout=None):
        """Get a worker to handle a request class
        """
        unskilled = self.unskilled
        if not unskilled:
            self.event.wait(timeout)
            if not unskilled:
                return None

        # Look for a skilled worker
        max_backlog = self.max_backlog
        best_score = 0
        best_worker = None
        skilled = self.skilled.get(rclass)
        if skilled is None:
            skilled = self.skilled[rclass] = set()
        for score, worker in skilled:
            backlog = worker.backlog + 1
            if backlog > max_backlog:
                continue
            score /= backlog
            if (score > best_score):
                best_score = score
                best_worker = worker

        if not best_score:
            while unskilled.first.value.backlog >= max_backlog:
                # Edge case.  max_backlog was reduced after a worker
                # with a larger backlog was added.
                #import pdb; pdb.set_trace()
                unskilled.first.value.lnode = None
                unskilled.popleft()
                if not unskilled:
                    # OK, now we need to wait. Just start over.
                    return self.get(rclass, timeout)

            best_worker = unskilled.first.value
            if rclass not in best_worker.resume:

                # We now have an unskilled worker and we need to
                # assign it a score.
                # - We want to give it work somewhat gradually.
                # - We got here because:
                #   - there are no skilled workers,
                #   - The skilled workers have all reached their max backlog
                score = self.unskilled_score
                best_worker.resume[rclass] = score
                skilled.add((score, best_worker))

        unskilled.remove(best_worker.lnode)
        best_worker.backlog += 1
        if best_worker.backlog < max_backlog:
            best_worker.lnode = unskilled.append(best_worker)
        else:
            best_worker.lnode = None

        return best_worker

    def put(self, worker):
        if worker.lnode is None:
            worker.lnode = self.unskilled.append(worker)
            self.event.set()
        if worker.backlog:
            worker.backlog -= 1

class Worker(zc.resumelb.util.Worker):

    maxrno = (1<<32) - 1

    def __init__(self, pool, socket, addr):
        self.pool = pool
        self.nrequest = 0

        readers = self.connected(socket, addr)

        while self.is_connected:
            try:
                rno, data = zc.resumelb.util.read_message(socket)
            except zc.resumelb.util.Disconnected:
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
        try:
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
                raise zc.resumelb.util.Disconnected()
            logger.debug('start_response %r', data)
            start_response(*data)
        except:
            # not using finally here, because we only want to end on error
            self.end(rno)
            raise

        def content():
            try:
                while 1:
                    data = get()
                    if data:
                        logger.debug('yield %r', data)
                        yield data
                    elif data is None:
                        raise zc.resumelb.util.Disconnected()
                    else:
                        break
            finally:
                self.end(rno)

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
    addrs = map(parse_addr, args)
    wsgi_addr = addrs.pop(0)

    lb = LB(addrs, host_classifier)
    gevent.pywsgi.WSGIServer(wsgi_addr, lb.handle_wsgi).serve_forever()


