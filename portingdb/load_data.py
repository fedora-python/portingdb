import os
import json
import datetime
import csv

import yaml

from . import tables
from . import queries

try:
    SafeLoader = yaml.CSafeLoader
except AttributeError:
    SafeLoader = yaml.SafeLoader


def get_data(*directories, engine=None):
    data = {}
    if any(directories):
        load_from_directories(data, directories)
    return data


def data_from_file(directories, basename):
    for directory in directories:
        for ext in '.yaml', '.json':
            filename = os.path.join(directory, basename + ext)
            if os.path.exists(filename):
                return decode_file(filename)
    raise FileNotFoundError(filename)


def data_from_csv(directories, basename):
    for directory in directories:
        filename = os.path.join(directory, basename + '.csv')
        if os.path.exists(filename):
            with open(filename) as f:
                return(list(csv.DictReader(f)))
    raise FileNotFoundError(filename)


def decode_file(filename):
    with open(filename) as f:
        if filename.endswith('.json'):
            return json.load(f)
        else:
            return yaml.load(f, Loader=SafeLoader)


def load_from_directories(data, directories):
    config = data.setdefault('config', {})
    config.update(data_from_file(directories, 'config'))

    statuses = data.setdefault('statuses', {})
    statuses.update({s['ident']: s
                     for s in data_from_file(directories, 'statuses')})

    collection_name = config['collection']
    packages = data.setdefault('packages', {})
    packages.update(data_from_file(directories, collection_name))

    for name, package in packages.items():
        package['name'] = name
        package['status_obj'] = statuses[package['status']]
        package.setdefault('tracking_bugs', ())
        package.setdefault('nonblocking', False)
        package.setdefault('pending_requirers', [])
        package.setdefault('last_link_update', None)
