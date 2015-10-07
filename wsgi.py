#!/usr/bin/env python3

import os
import logging

from sqlalchemy import create_engine
from dogpile.cache import make_region

from portingdb import load
from portingdb import htmlreport

level = logging.INFO
logging.basicConfig(level=level)
logging.getLogger('sqlalchemy.engine').setLevel(level)

sqlite_path = os.path.join(os.environ['OPENSHIFT_TMP_DIR'], 'portingdb.sqlite')

db_url = 'sqlite:///' + sqlite_path

cache_config = {
    'backend': 'dogpile.cache.redis',
    'expiration_time': 3600,  # 1h
    'arguments': {
        'host': os.environ['OPENSHIFT_REDIS_HOST'],
        'port': os.environ['OPENSHIFT_REDIS_PORT'],
        'password': os.environ['REDIS_PASSWORD'],
        'db': 0,
        'redis_expiration_time': 3600 + 600,  # 1h 10min
        'distributed_lock': True
    }
}

application = htmlreport.create_app(db_url=db_url, cache_config=cache_config)

import pprint
pprint.pprint(os.environ)

# For testing only
if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    httpd = make_server('localhost', 8051, application)
    # Wait for requests, stop with Ctrl+C.
    while True:
        httpd.handle_request()
