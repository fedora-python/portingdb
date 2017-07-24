#! /usr/bin/env python3
"""Get number of specs which are also used for rhel.

Check how many spec files among Fedora packages with naming issues
are cross platform, meaning that the same spec file is also used
for rhel/epel.

This check is pretty naive and gives just general information.
It checks spec files for using `%if %{?rhel}` conditionals
and may have false positives.

Usage: ./scripts/check-specs.py
"""
import click
import json
import logging
import os
import urllib.request
import urllib.error

from multiprocessing import Pool

from sqlalchemy import create_engine

from portingdb.htmlreport import get_naming_policy_progress
from portingdb.load import get_db


logging.basicConfig(format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MARKERS = ('%{?rhel}',)
SPEC_URL = 'http://pkgs.fedoraproject.org/cgit/rpms/{0}.git/plain/{0}.spec'
PKGDB_API = 'https://admin.fedoraproject.org/pkgdb/api/package/{}?acls=false'


def get_portingdb(db):
    """Return session object for portingdb."""
    url = 'sqlite:///' + db
    engine = create_engine(url)
    return get_db(None, engine=engine)


def check_spec(package):
    """Given the package, check its spec file for cross platform MARKERS.

    Arguments:
        - package (Package object)

    Return: package if MARKERS used in spec file, None otherwise
    """
    spec_url = SPEC_URL.format(package.name)
    try:
        response = urllib.request.urlopen(spec_url)
    except urllib.error.URLError as err:
        logger.error('Failed to get spec %s. Error: %s', spec_url, err)
    else:
        spec = str(response.read())
        for marker in MARKERS:
            if marker in spec:
                logger.warning('%s: %s', package.name, spec_url)
                return package
    logger.debug('Checked spec for %s: OK', package.name)


def check_branches(package):
    """Given the package, check if it is built for el6 or epel7.

    Arguments:
        - package (Package object)

    Return: package if built for el6 or epel7, None otherwise
    """
    api_url = PKGDB_API.format(package.name)
    try:
        response = urllib.request.urlopen(api_url)
    except urllib.error.URLError as err:
        logger.error('Failed to get package info %s. Error: %s', api_url, err)
    else:
        response = response.read()
        result = json.loads(response.decode())
        branches = [pkg['collection']['branchname'] for pkg in result['packages']]
        if 'el6' in branches or 'epel7' in branches:
            logger.debug('%s has epel branch', package.name)
            return package


def check_packages(packages, check_function):
    """Given the list of packages and a check_function,
    call check_function on each of the package and return result.

    Arguments:
        - packages (iterable)
        - check_function (function)

    Return: list if packages filtered by check_function
    """
    pool = Pool(processes=10)
    result = pool.map_async(check_function, packages)
    result.wait()
    cross_platform_packages = [pkg for pkg in result.get() if pkg]
    return cross_platform_packages


@click.command(help=__doc__)
@click.option('--db', help="Database file path.",
              default=os.path.abspath('portingdb.sqlite'))
def main(db):
    db = get_portingdb(db)
    _, data = get_naming_policy_progress(db)

    logger.info('Checking spec files for using %s...', ', '.join(MARKERS))
    result = {}
    for key, packages in data:
        result[key[0].ident] = check_packages(packages, check_spec)

    total = sum(packages.count() for _, packages in data)
    total_cross_platform = sum(len(packages) for packages in result.values())
    percentage = total_cross_platform * 100 / total

    logger.info('Checking for epel branches...')
    pkgdb_result = {}
    for category, packages in result.items():
        pkgdb_result[category] = check_packages(packages, check_branches)

    print('\nPackages that use {} in spec files: {} of {} ({:.2f}%)'.format(
        ', '.join(MARKERS), total_cross_platform, total, percentage))
    for category, packages in result.items():
        print('  {}: {}'.format(category, len(packages)))

    total_epel = sum(len(packages) for packages in pkgdb_result.values())
    print('From those {}, have el6 or epel7 branch: {}'.format(
        total_cross_platform, total_epel))


if __name__ == '__main__':
    main()
