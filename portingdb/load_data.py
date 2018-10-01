import os
import json
import datetime
import csv

import yaml
import click

from . import tables
from . import queries
from .load import _merge_updates

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
    _pkgs = data_from_file(directories, collection_name)
    _merge_updates(
        _pkgs,
        data_from_file(directories, collection_name + '-update')
    )
    packages.update(_pkgs)

    for name, package in packages.items():
        package['name'] = name
        package.setdefault('nonblocking', False)

        # XXX: Bugs
        package.setdefault('tracking_bugs', ())
        package.setdefault('last_link_update', None)

        # XXX: Pending requirements
        package.setdefault('pending_requirers', [])
        package.setdefault('pending_requirements', [])

    # Convert lists of dependency names to dicts of the package entries
    for name, package in packages.items():
        for attr in 'deps', 'build_deps':
            package[attr] = {name: packages[name] for name in package[attr]}

    # Convert "released" packages with all ported RPMs to "py3-only"
    for name, package in packages.items():
        if package['status'] == 'released':
            for rpm in package['rpms'].values():
                if any(version == 2 for version in rpm['py_deps'].values()):
                    break
            else:
                package['status'] = 'py3-only'

    # Convert "idle" packages with un-ported dependencies to "blocked"
    for name, package in packages.items():
        if package['status'] == 'idle':
            for dname, dpackage in package['deps'].items():
                if dpackage['status'] not in ('py3-only', 'legacy-leaf',
                                              'released', 'dropped',
                                              'unknown'):
                    package['status'] = 'blocked'
                    break

    # Add `status_obj`
    for name, package in packages.items():
        package['status_obj'] = statuses[package['status']]
