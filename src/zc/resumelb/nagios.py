"""Nagios monitor for resumelb
"""
import argparse
import socket

parser = argparse.ArgumentParser()

parser.add_argument('status-socket',
                    help='The name of the lb socket.')

parser.add_argument('--worker-mean-backlog-warn', '-b', type=int, default=3,
                    help='Mean worker backlog at which we warn.')
parser.add_argument('--worker-mean-backlog-error', '-B', type=int, default=9,
                    help='Mean worker backlog at which we error.')

parser.add_argument('--worker-max-backlog-warn', '-x', type=int, default=9,
                    help='Maximum worker backlog at which we warn.')
parser.add_argument('--worker-max-backlog-error', '-X', type=int, default=18,
                    help='Maximim worker backlog at which we error.')

parser.add_argument('--worker-request-age-warn', '-a', type=int, default=9,
                    help='Maximum request age at which we warn.')
parser.add_argument('--worker-request-age-error', '-A', type=int, default=30,
                    help='Maximim request age at which we error.')

parser.add_argument('--minimum-worker-warn', '-w', type=int,
                    help='Maximum request age at which we warn.')
parser.add_argument('--minimim-worker-error', '-W', type=int, default=0,
                    help='Maximim request age at which we error.')

parser.add_argument('--metrics', '-m', action="store_true",
                    help='Output metrics.')

import sys

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    args = parser.parse_args(args)


if __name__ == '__main__':
    main()
