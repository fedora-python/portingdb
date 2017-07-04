#! /usr/bin/env python3
"""Get number of specs which are also used for rhel.

Check how many spec files among Fedora packages with naming issues
are cross platform, meaning that the same spec file is also used
for rhel/epel.

This check is pretty naive and gives just general information.
It checks spec files for using `%if %{?rhel}` conditionals
and may have false positives.
"""
import click
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
        logger.error('Failed to get spec {}. Error: {}'.format(spec_url, err))
    else:
        spec = str(response.read())
        for marker in MARKERS:
            if marker in spec:
                logger.warning('{} uses {}: {}'.format(
                    package.name, ', '.join(MARKERS), spec_url))
                return package
    logger.debug('Checked spec for {}: OK'.format(package.name))


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

    result = {}
    for key, packages in data:
        result[key[0].ident] = check_packages(packages, check_spec)

    total = sum(packages.count() for _, packages in data)
    total_cross_platform = sum(len(packages) for packages in result.values())
    percentage = total_cross_platform * 100 / total

    logger.info('Checking spec files')
    print('\nPackages that use {} in spec files: {} of {} ({:.2f}%)'.format(
        ', '.join(MARKERS), total_cross_platform, total, percentage))
    for category, packages in result.items():
        print('  {}: {}'.format(category, len(packages)))


if __name__ == '__main__':
    main()
