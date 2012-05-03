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

0.5.0 (2012-05-??)
------------------

- Changed the way tracelog records are identified to reflect lb
  request numbers.  Records are disambiguated by including an lb
  identifier as a prefix.  For example "1.22" indicated request number
  22 from lb 1.

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
