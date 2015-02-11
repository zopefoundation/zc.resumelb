"""Nagios monitor for resumelb
"""
from __future__ import print_function
import argparse
import gevent.socket
import json
import socket
import sys
import time


parser = argparse.ArgumentParser()

parser.add_argument('socket',
                    help='Path to a load-balancer status socket')

parser.add_argument('--worker-mean-backlog-warn', '-b', type=int,
                    help='Mean worker backlog at which we warn.')
parser.add_argument('--worker-mean-backlog-error', '-B', type=int,
                    help='Mean worker backlog at which we error.')

parser.add_argument('--worker-max-backlog-warn', '-x', type=int,
                    help='Maximum worker backlog at which we warn.')
parser.add_argument('--worker-max-backlog-error', '-X', type=int,
                    help='Maximim worker backlog at which we error.')

parser.add_argument('--worker-request-age-warn', '-a', type=int,
                    help='Maximum request age at which we warn.')
parser.add_argument('--worker-request-age-error', '-A', type=int,
                    help='Maximim request age at which we error.')

parser.add_argument('--minimum-worker-warn', '-w', type=int,
                    help='Maximum request age at which we warn.')
parser.add_argument('--minimum-worker-error', '-W', type=int,
                    help='Maximim request age at which we error.')

parser.add_argument('--metrics', '-m', action="store_true",
                    help='Output metrics.')


def _check(value, warn, error, format, message, severity, sign=1):
    if error is not None and sign*value >= sign*error:
        message.append(format % abs(value))
        return 2

    if warn is not None and sign*value >= sign*warn:
        message.append(format % abs(value))
        return max(1, severity)

    return severity

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    args = parser.parse_args(args)
    for o in (args.worker_mean_backlog_warn, args.worker_mean_backlog_error,
              args.worker_max_backlog_warn, args.worker_max_backlog_error,
              args.minimum_worker_warn, args.minimum_worker_error):
        if o is not None:
            break
    else:
        if not args.metrics:
            print("You need to request metrics and/or alert settings")
            return 3

    now = time.time()
    status_socket = gevent.socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    status_socket.connect(args.socket)
    status_file = status_socket.makefile()
    status = status_file.read()
    status = json.loads(status)
    status_file.close()
    status_socket.close()

    nworkers = len(status['workers'])
    mbacklog = status["mean_backlog"]
    max_backlog = max(w[1] for w in status['workers'])
    max_age = max((now-w[3] if w[3] else -1) for w in status['workers'])

    severity = 0
    message = []
    severity = _check(
        mbacklog, args.worker_mean_backlog_warn, args.worker_mean_backlog_error,
        "mean backlog high (%s)", message, severity)
    severity = _check(
        max_backlog,
        args.worker_max_backlog_warn, args.worker_max_backlog_error,
        "max backlog high (%s)", message, severity)
    severity = _check(
        max_age, args.worker_request_age_warn, args.worker_request_age_error,
        "max age too high (%.1f)", message, severity)
    severity = _check(
        nworkers, args.minimum_worker_warn, args.minimum_worker_error,
        "too few workers (%s)", message, severity, -1)
    if message:
        message = ' '.join(message)
    else:
        message = "OK %s %s %s %s" % (
            nworkers, mbacklog, max_backlog,
            int(round(max_age)) if max_age >= 0 else '-')

    if args.metrics:
        message += (
            '|workers=%s mean_backlog=%s max_backlog=%s max_age=%.1fseconds'
            % (nworkers, mbacklog, max_backlog, max_age))

    print(message)
    return severity or None


if __name__ == '__main__':
    main()
