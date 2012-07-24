from bisect import bisect_left, insort
import collections
import gevent
import gevent.hub
import gevent.pywsgi
import gevent.socket
import llist
import logging
import re
import sys
import time
import webob
import zc.parse_addr
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
                 pool_factory=None,
                 **pool_settings):
        self.classifier = classifier
        self.disconnect_message = disconnect_message
        if pool_factory is None:
            pool_factory = Pool
        self.pool = pool_factory(**pool_settings)
        self.update_settings = self.pool.update_settings
        self.workletts = {}
        self.set_worker_addrs(worker_addrs)

    def set_worker_addrs(self, addrs):
        # addrs can be an iterable of addresses, or a dict {addr -> version}
        if not isinstance(addrs, dict):
            addrs = dict((addr, None) for addr in addrs)

        workletts = self.workletts
        old = list(workletts)
        for addr in addrs:
            if addr not in workletts:
                workletts[addr] = gevent.spawn(
                    self.connect, addr, workletts, addrs[addr])

        for addr in old:
            if addr not in addrs:
                workletts.pop(addr)

    connect_sleep = 1.0
    def connect(self, addr, workletts, version):
        while addr in workletts:
            try:
                socket = gevent.socket.create_connection(addr)
                Worker(self.pool, socket, addr, version)
            except gevent.GreenletExit, v:
                try:
                    socket.close()
                except:
                    pass
                raise
            except Exception, v:
                logger.exception('lb connecting to %r', addr)
                gevent.sleep(self.connect_sleep)

    def stop(self):
        for g in self.workletts.values():
            g.kill()
        self.workletts.clear()

    def shutdown(self):
        while self.pool.backlog:
            gevent.sleep(.01)
        self.stop()

    def handle_wsgi(self, env, start_response):
        rclass = self.classifier(env)
        #logger.debug('wsgi: %s', rclass)

        while 1:
            worker = self.pool.get(rclass)
            try:
                result = worker.handle(rclass, env, start_response)
                self.pool.put(worker)
                return result
            except zc.resumelb.util.Disconnected:
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

    def __init__(self,
                 unskilled_score=None, variance=None, backlog_history=None,
                 single_version=False):
        self.workers = set()
        self.nworkers = 0
        self.unskilled = llist.dllist()
        self.skilled = {}   # rclass -> {[(score, workers)]}
        self.event = gevent.event.Event()
        _init_backlog(self)
        self.single_version = single_version
        if single_version:
            # {version -> {worker}}
            self.byversion = collections.defaultdict(set)
            self.version = None

        self.update_settings(dict(
            unskilled_score=unskilled_score,
            variance=variance,
            backlog_history=backlog_history))


    _meta_settings = dict(
        unskilled_score=1.0,
        variance=1.0,
        backlog_history=9
        )

    def update_settings(self, settings):
        for name, default in self._meta_settings.iteritems():
            setting = settings.get(name)
            if setting is None:
                setting = default
            else:
                setting = type(default)(setting)
            setattr(self, name, setting)

        self._update_worker_decay()
        self._update_decay()

    def _update_decay(self):
        if self.nworkers:
            self.decay = 1.0 - 1.0/(self.backlog_history*2*self.nworkers)

    def _update_worker_decay(self):
        self.worker_decay = 1.0 - 1.0/(self.backlog_history*2)

    def __repr__(self):
        outl = []
        out = outl.append
        if self.single_version:
            out('Version: %s' % self.version)
            for v in self.byversion:
                if v != self.version and self.byversion[v]:
                    out('  Inactive: %s: %r' % (v, self.byversion[v]))

        out('Request classes:')
        for (rclass, skilled) in sorted(self.skilled.items()):
            out('  %s: %s'
                % (rclass,
                   ', '.join(
                       '%s(%s,%s)' %
                       (worker, score, worker.mbacklog)
                       for (score, worker) in sorted(skilled)
                   ))
                )
        out('Backlogs:')
        out("  overall backlog: %s Decayed: %s Avg: %s" % (
            self.backlog, self.mbacklog,
            (self.mbacklog / self.nworkers) if self.nworkers else None
            ))
        backlogs = {}
        for worker in self.workers:
            backlogs.setdefault(worker.backlog, []).append(worker)
        for backlog, workers in sorted(backlogs.items()):
            out('  %s: %r' % (backlog, sorted(workers)))
        return '\n'.join(outl)

    def _new_resume(self, worker, resume):
        skilled = self.skilled
        workers = self.workers

        if worker in workers:
            for rclass, score in worker.resume.iteritems():
                skilled[rclass].remove((score, worker))
        else:
            _init_backlog(worker)
            workers.add(worker)
            self.nworkers = len(self.workers)
            self._update_decay()
            worker.lnode = self.unskilled.appendleft(worker)
            self.event.set()

        worker.resume = resume
        for rclass, score in resume.iteritems():
            try:
                skilled[rclass].add((score, worker))
            except KeyError:
                skilled[rclass] = set(((score, worker), ))

        logger.info('new resume: %s', worker)

    def new_resume(self, worker, resume):
        if self.single_version:
            version = worker.version
            self.byversion[version].add(worker)
            if self.version is None:
                self.version = version
            if version == self.version:
                # Adding a worker to the quorum will always preserve the quorum
                self._new_resume(worker, resume)
            else:
                # Since the worker wasn't in the quorum, we don't call
                # _new_resume, so we need to update it's resume ourselves:
                worker.resume = resume

                # Adding this worker might have created a new quorum
                self._update_quorum()
        else:
            self._new_resume(worker, resume)

    def _remove(self, worker):
        skilled = self.skilled
        for rclass, score in worker.resume.iteritems():
            skilled[rclass].remove((score, worker))
        if getattr(worker, 'lnode', None) is not None:
            self.unskilled.remove(worker.lnode)
            worker.lnode = None
        self.workers.remove(worker)

        self.nworkers = len(self.workers)
        if self.nworkers:
            self._update_decay()
        else:
            self.event.clear()

    def remove(self, worker):

        self.backlog -= worker.backlog
        assert self.backlog >= 0, self.backlog
        _decay_backlog(self, self.decay)

        if self.single_version:
            self.byversion[worker.version].remove(worker)
            if worker.version == self.version:
                self._remove(worker)
                self._update_quorum()
            # Note if the worker's version isn't self.version, it's
            # not in the quorum, and it's removal can't cause the
            # quorum to change.
        else:
            self._remove(worker)

    def _update_quorum(self):
        byversion = self.byversion
        version = sorted(byversion, key=lambda v: -len(byversion[v]))[0]
        if (version == self.version or
            len(byversion[version]) == len(byversion[self.version])
            ):
            return # No change

        for worker in byversion[self.version]:
            self._remove(worker)
        self.version = version
        for worker in byversion[version]:
            self._new_resume(worker, worker.resume)

    def get(self, rclass, timeout=None):
        """Get a worker to handle a request class
        """
        unskilled = self.unskilled
        if not unskilled:
            self.event.wait(timeout)
            if not unskilled:
                return None

        # Look for a skilled worker
        best_score = 0
        best_worker = None
        skilled = self.skilled.get(rclass)
        if skilled is None:
            skilled = self.skilled[rclass] = set()

        max_backlog = max(self.variance * self.mbacklog / self.nworkers, 1)
        min_backlog = unskilled.first.value.mbacklog + 1
        for score, worker in skilled:
            if (worker.mbacklog - min_backlog) > max_backlog:
                continue
            backlog = worker.backlog
            if backlog > 1:
                score /= backlog
            if (score > best_score):
                best_score = score
                best_worker = worker

        if not best_score:
            best_worker = unskilled.first.value
            if rclass not in best_worker.resume:
                # We now have an unskilled worker and we need to
                # assign it a score.
                score = self.unskilled_score
                best_worker.resume[rclass] = score
                skilled.add((score, best_worker))

        # Move worker from lru to mru end of queue
        unskilled.remove(best_worker.lnode)
        best_worker.lnode = unskilled.append(best_worker)

        best_worker.backlog += 1
        _decay_backlog(best_worker, self.worker_decay)

        self.backlog += 1
        _decay_backlog(self, self.decay)

        return best_worker

    def put(self, worker):
        self.backlog -= 1
        assert self.backlog >= 0, self.backlog
        _decay_backlog(self, self.decay)
        if worker.backlog > 0:
            worker.backlog -= 1
            _decay_backlog(worker, self.worker_decay)

    def status(self):
        return dict(
                backlog = self.backlog,
                mean_backlog = self.mbacklog,
                workers = [
                    dict(name=worker.__name__,
                        backlog=worker.backlog,
                        mbacklog=worker.mbacklog,
                        oldest_time=(int(worker.oldest_time)
                                        if worker.oldest_time else None),
                        version=worker.version
                        )
                    for worker in sorted(
                        self.workers, key=lambda w: w.__name__)
                    ])

def _init_backlog(worker):
    worker.backlog = worker.nbacklog = worker.dbacklog = worker.mbacklog = 0

def _decay_backlog(worker, decay):
    worker.dbacklog = worker.dbacklog*decay + worker.backlog
    worker.nbacklog = worker.nbacklog*decay + 1
    worker.mbacklog = worker.dbacklog / worker.nbacklog

class Worker(zc.resumelb.util.LBWorker):

    maxrno = (1<<32) - 1

    def __init__(self, pool, socket, addr, version):
        self.pool = pool
        self.nrequest = 0
        self.requests = {}
        self.__name__ = '%s:%s' % addr
        self.version = version

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
                try:
                    reader = readers[rno]
                except KeyError:
                    pass
                else:
                    reader(data)

    @property
    def oldest_time(self):
        if self.requests:
            return min(self.requests.itervalues())

    def __repr__(self):
        return self.__name__

    def handle(self, rclass, env, start_response):
        #logger.debug('handled by %s', self.addr)

        env = env.copy()
        err = env.pop('wsgi.errors')
        input = env.pop('wsgi.input')
        env['zc.resumelb.request_class'] = rclass

        rno = self.nrequest + 1
        self.nrequest = rno % self.maxrno
        self.requests[rno] = time.time()
        try:
            get = self.start(rno).get
            put = self.put
            try:
                put((rno, env))
                content_length = int(env.get('CONTENT_LENGTH', 0))
                while content_length > 0:
                    data = input.read(min(content_length, block_size))
                    if not data:
                        # Browser disconnected, cancel the request
                        put((rno, None))
                        self.end(rno)
                        return
                    content_length -= len(data)
                    put((rno, data))
                put((rno, ''))

                data = get()
                if data is None:
                    raise zc.resumelb.util.Disconnected()
                #logger.debug('start_response %r', data)
                start_response(*data)
            except:
                # not using finally here, because we only want to end on error
                self.end(rno)
                raise

            def content():
                try:
                    # We yield a first value to get into the try so
                    # the generator close will execute thf finally block. :(
                    yield 1
                    while 1:
                        data = get()
                        if data:
                            yield data
                        else:
                            if data is None:
                                logger.warning(
                                    'Disconnected while returning body')
                            break
                finally:
                    self.end(rno)

            # See the "yield 1" comment above. :(
            content = content()
            content.next()
            return content
        finally:
            del self.requests[rno]

    def disconnected(self):
        self.pool.remove(self)
        zc.resumelb.util.Worker.disconnected(self)

def parse_addr(addr):
    host, port = addr.split(':')
    return host, int(port)

def host_classifier(env):
    host = env.get("HTTP_HOST", '')
    if host.startswith('www.'):
        return host[4:]
    return host

def re_classifier(name, regex):
    search = re.compile(regex).search

    def classifier(env):
        value = env.get(name)
        if value is not None:
            match = search(value)
            if match:
                return match.group('class')
        return ''

    return classifier

def main(args=None):
    """%prog [options] worker_addresses

    Run a resume-based load balancer on the give addresses.
    """
    if args is None:
        args = sys.argv[1:]

    import optparse
    parser = optparse.OptionParser(main.__doc__)
    parser.add_option(
        '-a', '--address', default=':0',
        help="Address to listed on for web requests"
        )
    parser.add_option(
        '-l', '--access-log', default='-',
        help='Access-log path.\n\n'
        'Use - (default) for standard output.\n'
        )
    parser.add_option(
        '-b', '--backlog', type='int',
        help="Server backlog setting.")
    parser.add_option(
        '-m', '--max-connections', type='int',
        help="Maximum number of simultanious accepted connections.")
    parser.add_option(
        '-L', '--logger-configuration',
        help=
        "Read logger configuration from the given configuration file path.\n"
        "\n"
        "The configuration file must be in ZConfig logger configuration syntax."
        )
    parser.add_option(
        '-r', '--request-classifier', default='zc.resumelb.lb:host_classifier',
        help="Request classification function (module:expr)"
        )
    parser.add_option(
        '-e', '--disconnect-message',
        help="Path to error page to use when a request is lost due to "
        "worker disconnection"
        )
    parser.add_option(
        '-v', '--variance', type='float', default=4.0,
        help="Maximum ration of a worker backlog to the mean backlog"
        )
    parser.add_option(
        '--backlog-history', type='int', default=9,
        help="Rough numner of requests to average worker backlogs over"
        )

    parser.add_option(
        '--unskilled-score', type='float', default=1.0,
        help="Score (requests/second) to assign to new workers."
        )

    options, args = parser.parse_args(args)
    if not args:
        print 'Error: must supply one or more worker addresses.'
        parser.parse_args(['-h'])

    if options.logger_configuration:
        logger_config = options.logger_configuration
        if re.match(r'\d+$', logger_config):
            logging.basicConfig(level=int(logger_config))
        elif logger_config in ('CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'):
            logging.basicConfig(level=getattr(logging, logger_config))
        else:
            import ZConfig
            with open(logger_config) as f:
                ZConfig.configureLoggers(f.read())

    addrs = map(parse_addr, args)
    wsgi_addr = parse_addr(options.address)

    lb = LB(addrs, host_classifier,
            variance=options.variance,
            backlog_history=options.backlog_history,
            unskilled_score=options.unskilled_score)

    # Now, start a wsgi server
    addr = zc.parse_addr.parse_addr(options.address)
    if options.max_connections:
        spawn= gevent.pool.Pool(options.max_connections)
    else:
        spawn = 'default'

    accesslog = options.access_log
    if isinstance(accesslog, str):
        accesslog = sys.stdout if accesslog == '-' else open(accesslog, 'a')

    gevent.pywsgi.WSGIServer(
        wsgi_addr, lb.handle_wsgi, backlog = options.backlog,
        spawn = spawn, log = accesslog)

    gevent.pywsgi.WSGIServer(wsgi_addr, lb.handle_wsgi).serve_forever()


