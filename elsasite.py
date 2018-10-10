#!/usr/bin/env python3
import logging

from portingdb import htmlreport

level = logging.INFO
logging.basicConfig(level=level)

sqlite_path = 'portingdb.sqlite'

db_url = 'sqlite:///' + sqlite_path

application = htmlreport.create_app(
    db_url=db_url, directories=['data'], cache_config=None
)

if __name__ == '__main__':
    from elsa import cli
    cli(application, base_url='http://fedora.portingdb.xyz/')
