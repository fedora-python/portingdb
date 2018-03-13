#!/usr/bin/env python3

import os
import logging

from portingdb import htmlreport

level = logging.INFO
logging.basicConfig(level=level)
logging.getLogger('sqlalchemy.engine').setLevel(level)

# sqlite_path = os.path.join(os.environ['OPENSHIFT_TMP_DIR'], 'portingdb.sqlite')

db_url = 'sqlite:///' + os.getcwd() + '/portingdb.sqlite'

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
}
application = htmlreport.create_app(db_url=db_url, cache_config=cache_config)


if __name__ == '__main__':
    application.run(host='0.0.0.0', port=8080)
