.. caution:: 

    This repository has been archived. If you want to work on it please open a ticket in https://github.com/zopefoundation/meta/issues requesting its unarchival.

===============================
Résumé-based WSGI load balancer
===============================

This package provides a load balancer for WSGI applications that sorts
requests into request classes and assigns requests of a given class to
the same workers.

The load balancer can benefit you if you have an application that:

- has too much load (or is too slow) to be handled by a single
  process,

- has a working set that is too large to fit in the caches
  used by your process, and

- there is a way to classify requests so that there is little overlap
  in the working sets of the various classes.

If what's above applies to you (or if you're curious), read on.

.. contents::

Architecture
============

An application deployed using the load balancer consistes of one or
more load balancers, and multiple workers.  Web requests come into the
load balancers, are converted to WSGI environments and requests, in
environment form, are handed over to workers over long-lived
multi-plexed connections.

Workers compute résumés, which are dictionaries mapping request
classes to scores, which are average requests per second. Workers send
load balancers their résumés periodically, and when load balancers
connect to them.

Multiple load balancers can be used for redundancy or load
distribution.  Résumés are managed by workers to assure that load
balancer's have the same information about worker skills.

Status
======

The current version of the load-balancer should be considered
experimental.  We're currently testing it in production.

The documentation is a bit thin, but there are extensive doctests.

Request Classification
======================

You need to provide a request-classification function that takes a
WSGI environment and returns a request class string.

Two classifiers are built-in:

host
  The host classifier uses HTTP Host header values, normalized by
  removing leading "www." prefixes, if present.

re_classifier
  A general classifier (factory) that applies a regular expression
  with a ``class`` group to an environment value.

  For example, to use the first step in a request URL path, you'd use
  the following request-classifier option to one of the load-balancer
  scripts described below::

    -r 'zc.resumelb.lb:re_classifier("PATH_INFO",r"/(?P<class>[^/]+)")'

Deployment
==========

Deploying the load balancer requires deploying each of the workers,
and deploying the load balancer(s) itself.  The workers are deployed much
like any WSGI stack. The workers serve as WSGI servers, even though
they don't accept HTTP requests directly.

There are two built-in strategies for deploying applications,
depending on whether you're willing to drink some ZooKeeper kool-aid.

If you use ZooKeeper, workers can bind to ephemeral ports and register
them with ZooKeeper.  The load balancer monitors ZooKeeper and adds
and removes workers to it's pool as worker processes are worker
processes are started and stopped.

Basic deployment
----------------

The basic deployment is the easiest to get up and running quickly.

Basic worker deployment
~~~~~~~~~~~~~~~~~~~~~~~

In the basic deployment, you deploy each worker as you would any WSGI
application.  A Paste Deployment server runner is provided by the
``paste.server_runner`` ``main`` entry point.  The runner accepts
parameters:

use egg:zc.resumelb
   This selects the basic worker runner.

address HOST:PORT
   The address to listen on, in the form HOST:PORT

history SIZE
   Roughly, the number of requests to consider when computing a
   worker's résumé.  This defaults to 9999.

max_skill_age SIZE
   The maximum number of requests without a request in a request class
   before a request class is dropped from a worker's résumé.

   If not specified, this defaults to 10 times the history.

threads NTHREADS
   If specified with a number greater than zero, then a thread pool of
   the given size is used to call the underlying WSGI stack.

resume_file PATH
   The path to a résumé file.  Periodically, the worker's résumé is
   saved to this file and the file is read on startup to initialize
   the worker's résumé.

tracelog LOGGER
   Request trace logging and specify the name of the Python logger to
   use.

Basic load-balancer deployment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The load balancer is a program that should be run with a daemonizer,
like zdaemon, or supervisor.  It get's it's configuration by way of
command-line arguments.  Run it with ``-h`` to get a list of options.

The basic load-balancer is provided by the ``resumelb`` script
provided by the package.

Basic Example
~~~~~~~~~~~~~

Here's a sample ``paste.ini`` file defining a WSGI stack::

  [app:main]
  use = egg:bobo
  bobo_resources = zc.resumelb.tests

  [server:main]
  use = egg:zc.resumelb
  address = 127.0.0.1:8000

And here's a load-balancer command you'd use with this worker::

  resumelb -LINFO -a :8080 127.0.0.1:8000

In this example, the load balancer listens on port 8080 and connects
to the worker on port 8000.

ZooKeeper-based deployment
---------------------------

In a ZooKeeper-based deployment, workers register with ZooKeeper and
the load balancer gets worker addresses from ZooKeeper. As workers are
started and stopped, they're automatically added to and removed from
the load-balancer pool.  In addition, most configuration parameters are
read from ZooKeeper and are updated at run time when they are changed
in ZooKeeper.  To learn more about ZooKeeper and how to build and
maintain a ZooKeeper tree, see  http://pypi.python.org/pypi/zc.zk.

ZooKeeoper-based worker deployment
----------------------------------

As with the basic deployment, you deploy ZooKeeoper-based workers as
servers in a WSGI stack.  A Paste Deployment server runner is provided by the
``paste.server_runner`` ``zk`` entry point.  The runner accepts
parameters:

use egg:zc.resumelb#zk
   This selects the ZooKeeoper-based worker runner.

zookeeper CONNECTION
   A ZooKeeoper connection string.

path PATH
   The path to a ZooKeeper node where the worker should get
   configuration and register it's address.  The node should have a
   ``providers`` subnode where address is is published.

address HOST:PORT
   The address to listen on, in the form HOST:PORT

threads NTHREADS
   If specified with a number greater than zero, then a thread pool of
   the given size is used to call the underlying WSGI stack.

resume_file PATH
   The path to a résumé file.  Periodically, the worker's résumé is
   saved to this file and the file is read on startup to initialize
   the worker's résumé.

tracelog LOGGER
   Request trace logging and specify the name of the Python logger to
   use.

ZooKeeper-based load-balancer deployment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The load balancer is a program that should be run with a daemonizer,
like zdaemon, or supervisor.  It get's it's configuration by way of
command-line arguments.   Run it with ``-h`` to get a list of options.

ZooKeeper Example
~~~~~~~~~~~~~~~~~

Here's a sample ``paste.ini`` file defining a WSGI stack::

  [app:main]
  use = egg:bobo
  bobo_resources = zc.resumelb.tests

  [server:main]
  use = egg:zc.resumelb#zk
  zookeeper = 127.0.0.1:2181
  path = /lb/workers

And here's a load-balancer command you'd use with this worker::

  zkresumelb -LINFO 127.0.0.1:2181 /lb

The above example assumes you have a ZooKeeper server running on port
2181 and that it includes a tree that looks like::

  /lb
    /providers
    /workers
      /providers

See http://pypi.python.org/pypi/zc.zk to learn more about building and
maintaining ZooKeeper trees.

Change History
==============

1.0.2 (2015-03-11)
------------------

- Fixed: the nagios monitor metric for max request age showed -1 when
  there were no outstanding requests. This was silly.

- Fixed a packaging bug.

1.0.1 (2015-03-03)
------------------

- Fixed: uncaught applications exceptions were mishandled for HEAD
  requests.

- Fixed: LB worker paths couldn't be links in single-version mode, or
  when using alternate pool implementations.

1.0.0 (2015-02-19)
------------------

- Nagios monitoring plugin. See src/zc/resumelb/nagios.rst.

- You can now supply alternative pool implementations.

  Thanks to: https://github.com/zopefoundation/zc.resumelb/pull/3

- There's a new pool implementation
  ``zc.resumelb.classlesspool.ClasslessPool`` that allocates work
  solely based on backlogs, ignoring resumes.  This is useful for
  smaller applications that don't have large resident sets or a good
  way to segregate requests, but that can benefit from ZooKeeper-aware
  load balancing.

0.7.5 (2014-11-18)
------------------

- Fixed: Tracelogs didn't include start and stop records.

0.7.4 (2014-10-29)
------------------

- Fixed: Applications or middleware that didn't call the WSGI
  start_response function before returning an iterator weren't handled
  properly.

- Fixed: File-descriptors leaked when load balancers disconnected from
  workers.

0.7.3 (2014-06-04)
------------------

- Added some optimizations to reduce latency between load balancers
  and workers.

0.7.2 (2014-06-02)
------------------

- Added keep-alive messages from load balancers to workers to detect
  workers that have gone away uncleanly.

  (Note that workers don't have to be updated.)

0.7.1 (2012-10-17)
------------------

- Fixed: When used with ZooKeeper, a load balancer could end up with
  multiple connections to the same worker due to ZooKeeper
  "flapping".  (ZooKeeper might report that workers had gone away and
  come back without the workers actually going away.)

- Fixed: When using single-version mode, flapping between versions
  could cause worker and book backlogs to be computed concorrectly,
  causing assertion errors.

- In single-version mode, log version changes.

0.7.0 (2012-07-05)
------------------

- Added support in the load balancer for applications that can't have
  multiple worker versions.  You can upgrade workers
  gradually. Workers with the new version will be ignored until
  they're in the majority, at which time the lb will stop using
  workers with the old version.

0.6.2 (2012-06-15)
------------------

- Fixed: a lack of socket timeout could cause requests to leak.

0.6.0 (2012-05-11)
------------------

- Added a command-line script to fetch lb status data, assuming you're
  using the ZooKeeper-aware load-balancer script and have requested a
  status server.  (Also updated the status output to show request
  start times as integer seconds.)

0.5.2 (2012-05-09)
------------------

- Fixed: Temporary files created when buffering data in the load
  balancers weren't closed explicitly.  Generally, they were closed
  through garbage collection, but in certain situations, their numbers
  could build quickly, leading to file-descriptor exhaustion.

- Fixed: Tracelog 'I' records didn't always contain input length information.

- Fixed: Tracelog 'I' records were only included when using thread pools.

0.5.1 (2012-05-07)
------------------

- Fixed: Worker resume data wasn't initialized correctly when no
  parameters are passed to the constructor and when reading a resume
  file, causing resmes not not to update.

- Fixed: worker errors were written to standard out rather than being
  logged.

- Fixed: Poorly-behaved WSGI applications that fail to catch errors
  caused requests to hang rather than return 500 responses.

0.5.0 (2012-05-03)
------------------

- Changed the way tracelog records are identified to reflect lb
  request numbers.  Records are disambiguated by including an lb
  identifier as a prefix.  For example "1.22" indicated request number
  22 from lb 1.

- When defining workers that register with ZooKeeper, you can now
  supply a description in the paste.ini file that shows up in
  ZooKeeper.  While the pid alone provides enough information to find
  a worker, often a description (e.g. instance name or path) can make
  it easier.

0.4.0 (2012-04-27)
------------------

- Change the load-balancing algorithm to take backlogs of
  underutilized workers into account to allow a lower variance
  parameter to be used, which allows new workers to be better
  utilized.

- Changed the load-balancing algorithm to try just a little bit harder
  to keep work with skilled workers by not penalizing workers for
  their first outstanding request. (In other words, when adjusting
  worker scrores chacking a maximum backlog, we subtract 1 from the
  worker's backlog if it's non-zero.

- The status server provided when using ZooKeeper now listens on a
  unix-domain socket.

- The status server provided when using ZooKeeper now includes the
  start time of the oldest request for each worker, to be used for
  monitoring.

- Fixed: Workers buffered large request bodies in memory.  Now large
  request bodies are buffered to disk.

- Internal optimizations, especially writh regard to handling large
  request and response bodies.

0.3.0 (2012-03-28)
------------------

Changed the way the zkresumelb (load-balancer program that works with
ZooKeeper) handles access logs. Now, you pass a Python logging logger
name.  If you don't pass anything, then nothing will be logged.

0.2.0 (2012-03-27)
------------------

- There's a new API for getting worker résumés, typically from
  monitoring code::

    >>> import zc.resume.worker
    >>> print zc.resume.worker.get_resume(addr)

  This is useful both for getting a worker's résumé and for making
  sure that the worker is accepting load-balancer connections.

  There's also a scriot version of this::

    bin/get-worker-resume 192.168.24.60:33161

- When using ZooKeeper, you can request an lb status server.  The
  address gets registered with ZooKeeper. When you connect to it, you
  get back a json string containing the overall lb backlog and
  addresses and backlogs of each worker.

- The update settings methods were changed to revert settings to
  default when not provided.  This is especially important when used
  with ZooKeeper, so you can look at a tree and know what settings are
  without knowing the change history.

- Added graceful load-balancer and worker shutdown on SIGTERM.

- Fixed: trace log request ids weren't assigned correctly when using
  multiple load balancers.

- Added packaging meta data to help find gevent 1.0b1
  (which is at http://code.google.com/p/gevent/downloads/list)

- Updated the API for application trace logging to match that of
  zc.zservertracelog, mainly to get database logging for ZTK
  applications.

0.1.0 (2012-03-09)
------------------

Initial release.
