import os
import json
import datetime
import csv

import yaml
import click

from . import tables
from . import queries
from .load import _merge_updates


DONE_STATUSES = {'released', 'dropped', 'legacy-leaf', 'py3-only'}

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

    naming = data.setdefault('naming', {})
    naming.update({s['ident']: s
                   for s in data_from_file(directories, 'naming')})

    collection_name = config['collection']
    packages = data.setdefault('packages', {})
    _pkgs = data_from_file(directories, collection_name)
    _merge_updates(
        _pkgs,
        data_from_file(directories, collection_name + '-update')
    )
    packages.update(_pkgs)

    groups = data.setdefault('groups', {})
    groups.update(data_from_file(directories, 'groups'))

    data['history'] = data_from_csv(directories, 'history')
    data['history-naming'] = data_from_csv(directories, 'history-naming')

    for name, package in packages.items():
        package['name'] = name
        package.setdefault('nonblocking', False)

        if 'rpms' not in package:
            print('WARNING: no RPMs in package', name)

        package.setdefault('rpms', {})
        package.setdefault('deps', ())
        package.setdefault('build_deps', ())

        package['is_misnamed'] = any(rpm.get('is_misnamed')
                                     for rpm in package['rpms'].values())

        package.setdefault('dependents', {})
        package.setdefault('build_dependents', {})
        package.setdefault('groups', {})
        package.setdefault('tracking_bugs', ())
        package.setdefault('last_link_update', None)

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
                if (
                    dpackage['status'] not in DONE_STATUSES
                    and not dpackage['nonblocking']
                ):
                    package['status'] = 'blocked'
                    break

    # Add `status_obj`
    for name, package in packages.items():
        package['status_obj'] = statuses[package['status']]

    # Convert bug info
    for name, package in packages.items():
        links = []
        link_updates = []
        for link_type, link_info in package.get('links', {}).items():
            if isinstance(link_info, str):
                url = link_info
                note = None
                time = None
            else:
                url, note, time = link_info
                time = datetime.datetime.strptime(time, '%Y-%m-%d %H:%M:%S')
                link_updates.append(time)
            links.append({
                'url': url,
                'type': link_type,
                'note': note,
                'last_update': time,
            })
        package['links'] = links
        if link_updates:
            package['last_link_update'] = max(link_updates)

    # Add dependent packages
    for name, package in packages.items():
        for pkg in package['deps'].values():
            pkg['dependents'][package['name']] = package
        for pkg in package['build_deps'].values():
            pkg['build_dependents'][package['name']] = package

    # Add pending requirers/requirements
    for name, package in packages.items():
        for src, dest in ('dependents', 'pending_dependents'), ('deps', 'pending_deps'):
            package[dest] = {
                d['name']: d
                for d in package[src].values()
                if d['status'] not in DONE_STATUSES
            }

    # Update groups
    for ident, group in groups.items():
        group['ident'] = ident
        group.setdefault('hidden', False)
        group['seed_packages'] = {
            n: packages[n] for n in group['packages'] if n in packages}
        names_to_add = set(group['seed_packages'])
        names_added = set()
        group['packages'] = pkgs = {}
        while names_to_add:
            name = names_to_add.pop()
            if name in names_added:
                continue
            names_added.add(name)
            package = packages[name]
            pkgs[name] = package
            package['groups'][ident] = group
            if package['status'] != 'dropped':
                for dep in package['deps']:
                    names_to_add.add(dep)
                for dep in packages[name]['build_deps']:
                    names_to_add.add(dep)
