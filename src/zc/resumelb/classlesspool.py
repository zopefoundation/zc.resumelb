"""Load balancer pool that ignores request class and worker skill.

It allocates work by least backlog.
"""
import gevent.event
import llist
import zc.resumelb.lb

class ClasslessPool(zc.resumelb.lb.PoolBase):

    def __init__(self, single_version=False):
        super(ClasslessPool, self).__init__(single_version)
        self.backlog = 0
        self.workers = set()
        self.line = llist.dllist()
        self.event = gevent.event.Event()

    def get(self, request_class, timeout=None):
        line = self.line
        if not line:
            self.event.wait(timeout)
            if not line:
                return None

        node = self.line.first
        best_worker = None
        best_backlog = 999999999
        while node is not None:
            worker = node.value
            backlog = worker.backlog
            if backlog == 0:
                best_worker = worker
                break
            if backlog < best_backlog:
                best_backlog = backlog
                best_worker = worker
            node = node.next

        # Move worker from lru to mru end of queue
        line.remove(best_worker.lnode)
        best_worker.lnode = line.append(best_worker)

        best_worker.backlog += 1
        self.backlog += 1

        return best_worker

    @property
    def mbacklog(self):
        nworkers = len(self.workers)
        if nworkers:
            return self.backlog / nworkers
        else:
            return None

    def _new_resume(self, worker, resume):
        if worker not in self.workers:
            self.workers.add(worker)
            worker.lnode = self.line.appendleft(worker)
            worker.backlog = 0
            self.event.set()

    def put(self, worker):
        self.backlog -= 1
        assert self.backlog >= 0, self.backlog
        worker.backlog -= 1
        assert worker.backlog >= 0, worker.backlog

    def _remove(self, worker):
        if getattr(worker, 'lnode', None) is not None:
            self.line.remove(worker.lnode)
            worker.lnode = None
        self.workers.remove(worker)
        if not self.workers:
            self.event.clear()

    def update_settings(self, settings):
        pass

def classifier(_):
    return ''
