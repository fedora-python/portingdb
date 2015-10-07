#!/usr/bin/env python3

import os
import logging

from sqlalchemy import create_engine

from portingdb import load
from portingdb import htmlreport

level = logging.DEBUG
logging.basicConfig(level=level)
logging.getLogger('sqlalchemy.engine').setLevel(level)

sqlite_path = os.path.join(os.environ['OPENSHIFT_TMP_DIR'], 'portingdb.sqlite')

db_url = 'sqlite:///' + sqlite_path

application = htmlreport.create_app(db_url=db_url)

# For testing only
if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    httpd = make_server('localhost', 8051, application)
    # Wait for a single request, serve it and quit.
    httpd.handle_request()
