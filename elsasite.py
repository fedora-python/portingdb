#!/usr/bin/env python3
import logging

from portingdb import htmlreport

level = logging.INFO
logging.basicConfig(level=level)

sqlite_path = 'portingdb.sqlite'

application = htmlreport.create_app(directories=['data'], cache_config=None)

if __name__ == '__main__':
    from elsa import cli
    cli(application, base_url='https://fedora.portingdb.xyz/')
