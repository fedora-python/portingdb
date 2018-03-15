#!/usr/bin/env python3

import os
import logging

from portingdb import htmlreport

level = logging.INFO
logging.basicConfig(level=level)
logging.getLogger('sqlalchemy.engine').setLevel(level)

sqlite_path = os.getcwd() + '/portingdb.sqlite'

db_url = 'sqlite:///' + sqlite_path

redis_configured = all(
    var in os.environ for var in
    ('REDIS_SERVICE_HOST', 'REDIS_SERVICE_PORT', 'REDIS_PASSWORD'))

cache_config = {
    'backend': 'dogpile.cache.redis',
    'expiration_time': 3600,  # 1h
    'arguments': {
        'host': os.environ['REDIS_SERVICE_HOST'],
        'port': os.environ['REDIS_SERVICE_PORT'],
        'password': os.environ['REDIS_PASSWORD'],
        'db': 0,
        'redis_expiration_time': 3600 + 600,  # 1h 10min
        'distributed_lock': True
    }
} if redis_configured else None

application = htmlreport.create_app(db_url=db_url, cache_config=cache_config)


if __name__ == '__main__':
    application.run(host='0.0.0.0', port=8080)
