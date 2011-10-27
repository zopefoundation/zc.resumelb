from struct import pack, unpack
import errno
import gevent.queue
import logging
import marshal
import socket

logger = logging.getLogger(__name__)

disconnected_errors = (errno.EPIPE, errno.ECONNRESET, errno.ENOTCONN,
                 errno.ESHUTDOWN, errno.ECONNABORTED)

def read_message(sock):
    data = ''
    while len(data) < 8:
        recieved = sock.recv(8-len(data))
        if not recieved:
            logger.info("read_message disconnected %s", sock)
            raise gevent.GreenletExit()
        data += recieved

    rno, l = unpack(">II", data)

    data = ''
    while len(data) < l:
        recieved = sock.recv(l-len(data))
        if not recieved:
            logger.info("read_message disconnected %s", sock)
            raise gevent.GreenletExit()
        data += recieved

    return rno, marshal.loads(data)

def write_message(sock, rno, data):
    data = marshal.dumps(data)
    data = pack(">II", rno, len(data))+data
    while data:
        try:
            sent = sock.send(data)
        except socket.error, err:
            if err.args[0] in disconnected_errors:
                logger.debug("write_message disconnected %s", sock)
                raise gevent.GreenletExit()
            else:
                raise
        data = data[sent:]

def writer(writeq, sock, multiplexer):
    while 1:
        rno, data = writeq.get()
        try:
            write_message(sock, rno, data)
        except gevent.GreenletExit:
            multiplexer.disconnected()
            return

class Worker:

    def connected(self, socket, addr=None):
        if addr is None:
            addr = socket.getpeername()
        logger.info('worker connected %s', addr)
        self.addr = addr
        self.readers = {}
        writeq = gevent.queue.Queue()
        gevent.Greenlet.spawn(writer, writeq, socket, self)
        self.put = writeq.put
        self.connected = True
        return self.readers

    def __len__(self):
        return len(self.readers)

    def start(self, rno):
        readq = gevent.queue.Queue()
        self.readers[rno] = readq.put
        return readq.get

    def end(self, rno):
        del self.readers[rno]

    class Disconnected(Exception):
        pass

    def put_disconnected(self, *a, **k):
        raise self.Disconnected()

    def disconnected(self):
        logger.info('worker disconnected %s', self.addr)
        self.connected = False
        for put in self.readers.itervalues():
            put(None)

        self.put = self.put_disconnected
