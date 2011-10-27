import bobo
import os

@bobo.query
def hi(bobo_request):
    return "\n\n%s -> %s\n\n" % (
        bobo_request.url, os.getpid())

