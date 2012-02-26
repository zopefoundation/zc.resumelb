##############################################################################
#
# Copyright Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""ZooKeeper integration
"""
import gevent
import gevent.pool
import logging
import os
import sys
import zc.parse_addr
import zc.zk

def worker(app, global_conf, zookeeper, path, loggers=None, address=':0',
           threads=None, run=True, **kw):
    """Paste deploy server runner
    """
    if loggers:
        import ZConfig
        ZConfig.configureLoggers(loggers)

    zk = zc.zk.ZooKeeper(zookeeper)
    address = zc.parse_addr.parse_addr(address)
    from zc.resumelb.worker import Worker

    worker = Worker(app, address, threads=threads and int(threads),
                    **kw)

    # Set up notification of settings changes.
    settings = zk.properties(path)
    watcher = gevent.get_hub().loop.async()
    watcher.start(lambda : worker.update_settings(settings))
    settings(lambda _: watcher.send())

    zk.register_server(path+'/providers', worker.addr)
    worker.zk = zk
    worker.__zksettings = settings

    if run:
        try:
            worker.server.serve_forever()
        finally:
            logging.getLogger(__name__+'.worker').info('exiting')
            zk.close()
    else:
        gevent.sleep(.01)
        return worker

def lbmain(args=None, run=True):
    """%prog [options] zookeeper_connection path

    Run a resume-based load balancer on addr.
    """
    if args is None:
        args = sys.argv[1:]
    elif isinstance(args, str):
        args = args.split()
        run = False
    import optparse
    parser = optparse.OptionParser(lbmain.__doc__)
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
        '--logger-configuration',
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

    try:
        options, args = parser.parse_args(args)
        if len(args) != 2:
            print 'Error: must supply a zookeeper connection string and path.'
            parser.parse_args(['-h'])
        zookeeper, path = args
    except SystemExit:
        if run:
            raise
        else:
            return

    if options.logger_configuration:
        import ZConfig
        with open(options.logger_configuration) as f:
            ZConfig.configureLoggers(f.read())


    zk = zc.zk.ZooKeeper(zookeeper)
    addrs = zk.children(path+'/workers/providers')
    rcmod, rcexpr = options.request_classifier.split(':')
    __import__(rcmod)
    rcmod = sys.modules[rcmod]
    request_classifier = eval(rcexpr, rcmod.__dict__)

    disconnect_message = options.disconnect_message
    if disconnect_message:
        with open(disconnect_message) as f:
            disconnect_message = f.read()
    else:
        disconnect_message = zc.resumelb.lb.default_disconnect_message

    from zc.resumelb.lb import LB
    lb = LB(map(zc.parse_addr.parse_addr, addrs),
            request_classifier, disconnect_message)

    # Set up notification of address changes.
    awatcher = gevent.get_hub().loop.async()
    @awatcher.start
    def _():
        lb.set_worker_addrs(map(zc.parse_addr.parse_addr, addrs))
    addrs(lambda a: awatcher.send())

    # Set up notification of address changes.
    settings = zk.properties(path)
    swatcher = gevent.get_hub().loop.async()
    swatcher.start(lambda : lb.update_settings(settings))
    settings(lambda a: swatcher.send())

    lb.zk = zk
    lb.__zk = addrs, settings

    # Now, start a wsgi server
    addr = zc.parse_addr.parse_addr(options.address)
    if options.max_connections:
        spawn= gevent.pool.Pool(options.max_connections)
    else:
        spawn = 'default'

    accesslog = options.access_log
    if isinstance(accesslog, str):
        accesslog = sys.stdout if accesslog == '-' else open(accesslog, 'a')

    server = gevent.pywsgi.WSGIServer(
        addr, lb.handle_wsgi, backlog = options.backlog,
        spawn = spawn, log = accesslog)
    server.start()
    zk.register_server(path+'/providers', (addr[0], server.server_port))

    if run:
        try:
            server.serve_forever()
        finally:
            logging.getLogger(__name__+'.lbmain').info('exiting')
            zk.close()
    else:
        gevent.sleep(.01)
        return lb, server, accesslog
