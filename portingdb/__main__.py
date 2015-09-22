import logging

from portingdb import tables
from portingdb.load import get_db

logging.basicConfig(level=logging.INFO)
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

db = get_db('data')

