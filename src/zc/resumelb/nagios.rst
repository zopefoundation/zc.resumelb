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
                [--minimim-worker-error MINIMIM_WORKER_ERROR] [--metrics]
                status-socket
    <BLANKLINE>
    positional arguments:
      status-socket         The name of the lb socket.
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
      --minimim-worker-error MINIMIM_WORKER_ERROR, -W MINIMIM_WORKER_ERROR
                            Maximim request age at which we error.
      --metrics, -m         Output metrics.
