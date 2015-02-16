import zope.interfaces

class IPool(zope.interface.Interface):
    """Manage and assign work to workers.
    """
    # This was added to define how the lb uses a pool, now that
    # alternate pool implementations can be provided.

    def __init__(single_version=False):
        """Initializd the pool.

        The ``single_version`` argument is supplied by name.
        If true, then the pool should only use workers of the same
        version for which the highest number of workers are running
        that version.
        """

    backlog = zope.interface.Attribute("number of active requests")

    def get(request_class):
        """Get a worker to handle the given request class (string).
        """

    mbacklog = zope.interface.Attribute(
        "(possibly time-weighted) mean worker backlog for the load balancer")

    def new_resume(worker, data):
        """Update the resume for a worker.

        If the worker isn't in the pool, add it.
        """

    def put(worker):
        """Notify the pool that the worker has completed a request.
        """

    def remove(worker):
        """Remove the worker from the pool.

        It shouldn't get any more work and should be forgotten.
        """

    def update_settings(settings):
        """Update the pool with the given settings (mapping).

        Extra keys in the settings should be ignored.

        The settings argument should be used once and not modified.
        """

    workers = zope.interface.Attribute("Iterable of workers.")
