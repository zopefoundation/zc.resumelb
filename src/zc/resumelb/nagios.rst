Nagios monitor for the resumelb load balancer
=============================================

The monitor provides basic monitoring and optional metrics.

It's provided as a Nagios plugin and, therefore, a script that can be
run form the command line. To find out what options are available, use
the -h option:

  $ rlb-nagios -h

.. -> src

    >>> args =  src.strip().split()[1:]
    >>> entry = args.pop(0)

    >>> import pkg_resources, os
    >>> monitor = pkg_resources.load_entry_point(
    ...     'zc.resumelb', 'console_scripts', entry)
    >>> os.environ['COLUMNS'] = '72'

    >>> try: monitor(args)
    ... except SystemExit: pass
    ... else: print 'should have exited'
    ... # doctest: +NORMALIZE_WHITESPACE
    usage: test [-h] [--worker-mean-backlog-warn WORKER_MEAN_BACKLOG_WARN]
                [--worker-mean-backlog-error WORKER_MEAN_BACKLOG_ERROR]
                [--worker-max-backlog-warn WORKER_MAX_BACKLOG_WARN]
                [--worker-max-backlog-error WORKER_MAX_BACKLOG_ERROR]
                [--worker-request-age-warn WORKER_REQUEST_AGE_WARN]
                [--worker-request-age-error WORKER_REQUEST_AGE_ERROR]
                [--minimum-worker-warn MINIMUM_WORKER_WARN]
                [--minimum-worker-error MINIMUM_WORKER_ERROR] [--metrics]
                socket
    <BLANKLINE>
    positional arguments:
      socket                Path to a load-balancer status socket
    <BLANKLINE>
    optional arguments:
      -h, --help            show this help message and exit
      --worker-mean-backlog-warn WORKER_MEAN_BACKLOG_WARN,
       -b WORKER_MEAN_BACKLOG_WARN
                            Mean worker backlog at which we warn.
      --worker-mean-backlog-error WORKER_MEAN_BACKLOG_ERROR,
       -B WORKER_MEAN_BACKLOG_ERROR
                            Mean worker backlog at which we error.
      --worker-max-backlog-warn WORKER_MAX_BACKLOG_WARN,
       -x WORKER_MAX_BACKLOG_WARN
                            Maximum worker backlog at which we warn.
      --worker-max-backlog-error WORKER_MAX_BACKLOG_ERROR,
       -X WORKER_MAX_BACKLOG_ERROR
                            Maximim worker backlog at which we error.
      --worker-request-age-warn WORKER_REQUEST_AGE_WARN,
       -a WORKER_REQUEST_AGE_WARN
                            Maximum request age at which we warn.
      --worker-request-age-error WORKER_REQUEST_AGE_ERROR,
       -A WORKER_REQUEST_AGE_ERROR
                            Maximim request age at which we error.
      --minimum-worker-warn MINIMUM_WORKER_WARN, -w MINIMUM_WORKER_WARN
                            Maximum request age at which we warn.
      --minimum-worker-error MINIMUM_WORKER_ERROR, -W MINIMUM_WORKER_ERROR
                            Maximim request age at which we error.
      --metrics, -m         Output metrics.

Faux status server:

    >>> import zc.resumelb.nagiosfauxstatus
    >>> pool = zc.resumelb.nagiosfauxstatus.Pool()

    >>> import gevent.server, gevent.socket, socket
    >>> server = gevent.socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    >>> server.bind('status.sock')
    >>> server.listen(1)
    >>> server = gevent.server.StreamServer(server, pool.handle)
    >>> server.start()

This results in output like the following::

    usage: test [-h] [--worker-mean-backlog-warn WORKER_MEAN_BACKLOG_WARN]
                [--worker-mean-backlog-error WORKER_MEAN_BACKLOG_ERROR]
                [--worker-max-backlog-warn WORKER_MAX_BACKLOG_WARN]
                [--worker-max-backlog-error WORKER_MAX_BACKLOG_ERROR]
                [--worker-request-age-warn WORKER_REQUEST_AGE_WARN]
                [--worker-request-age-error WORKER_REQUEST_AGE_ERROR]
                [--minimum-worker-warn MINIMUM_WORKER_WARN]
                [--minimum-worker-error MINIMUM_WORKER_ERROR] [--metrics]
                socket
    <BLANKLINE>
    positional arguments:
      socket                Path to a load-balancer status socket
    <BLANKLINE>
    optional arguments:
      -h, --help            show this help message and exit
      --worker-mean-backlog-warn WORKER_MEAN_BACKLOG_WARN,
       -b WORKER_MEAN_BACKLOG_WARN
                            Mean worker backlog at which we warn.
      --worker-mean-backlog-error WORKER_MEAN_BACKLOG_ERROR,
       -B WORKER_MEAN_BACKLOG_ERROR
                            Mean worker backlog at which we error.
      --worker-max-backlog-warn WORKER_MAX_BACKLOG_WARN,
       -x WORKER_MAX_BACKLOG_WARN
                            Maximum worker backlog at which we warn.
      --worker-max-backlog-error WORKER_MAX_BACKLOG_ERROR,
       -X WORKER_MAX_BACKLOG_ERROR
                            Maximim worker backlog at which we error.
      --worker-request-age-warn WORKER_REQUEST_AGE_WARN,
       -a WORKER_REQUEST_AGE_WARN
                            Maximum request age at which we warn.
      --worker-request-age-error WORKER_REQUEST_AGE_ERROR,
       -A WORKER_REQUEST_AGE_ERROR
                            Maximim request age at which we error.
      --minimum-worker-warn MINIMUM_WORKER_WARN, -w MINIMUM_WORKER_WARN
                            Maximum request age at which we warn.
      --minimum-worker-error MINIMUM_WORKER_ERROR, -W MINIMUM_WORKER_ERROR
                            Maximim request age at which we error.
      --metrics, -m         Output metrics.

None of the options have defaults.  If you run the monitor without
options, you'll get an error reminding you that you need to specify
metrics output or thresholds.

.. Now test :)

    We have three idle workers.

    By default, all is well:

    >>> m = lambda s: monitor(s.split() + ['status.sock'])
    >>> m('')
    You need to request metrics and/or alert settings
    3

    >>> m('-b3 -B9 -x9 -X19 -a1 -A2 -w2 -W1')
    OK 3 0 0 -

    >>> pool.get(1)
    >>> m('-b3 -B9 -x9 -X19 -a1 -A2 -w2 -W1')
    OK 3 0 1 0

    >>> _ = [pool.get(0) for i in range(8)]
    >>> m('-b3 -B9 -x9 -X19 -a1 -A2 -w2 -W1')
    mean backlog high (3)
    1

    >>> _ = [pool.get(0) for i in range(10)]
    >>> m('-b3 -B9 -x9 -X19 -a1 -A2 -w2 -W1')
    mean backlog high (6) max backlog high (18) max age too high (1.8)
    1

    >>> _ = [pool.get(2) for i in range(10)]
    >>> m('-b3 -B9 -x9 -X19 -a1 -A2 -w2 -W1')
    mean backlog high (9) max backlog high (18) max age too high (1.8)
    2

    >>> _ = [pool.put(2) for i in range(10)]
    >>> m('-b3 -B9 -x9 -X19 -a1 -A2 -w2 -W1')
    mean backlog high (6) max backlog high (18) max age too high (1.8)
    1

    >>> _ = [pool.get(0) for i in range(10)]
    >>> m('-b333 -B99 -x9 -X19 -a1 -A22 -w2 -W1')
    max backlog high (28) max age too high (2.8)
    2
    >>> m('-b333 -B99 -x9 -X199 -a1 -A2 -w2 -W1')
    max backlog high (28) max age too high (2.8)
    2

    >>> m('-w3 -W1')
    too few workers (3)
    1

    >>> m('-w8 -W3')
    too few workers (3)
    2

    Metrics:

    >>> m('-w8 -W3 -m') # doctest: +NORMALIZE_WHITESPACE
    too few workers (3)|workers=3 mean_backlog=9 max_backlog=28
        max_age=2.8seconds
    2
