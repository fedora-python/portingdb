import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime

import click

from portingdb.load_data import get_data

CANNOT_RE = re.compile(r"can't install ((.+)-[^-]+-[^-]+):")


def pkg_names(data):
    for srpm in data['packages'].values():
        for nevr, rpm in srpm['rpms'].items():
            if 2 in rpm['py_deps'].values():
                yield nevr.rsplit('-', 2)[0]


def installcheck(repo, arch):
    solvecache = pathlib.Path(f'/var/cache/dnf/{repo}.solv')
    filenames = pathlib.Path(f'/var/cache/dnf/{repo}-filenames.solvx')
    if not (solvecache.exists() and filenames.exists()):
        print('Fetch the cache first:\n'
              f'  $ sudo dnf --repo={repo} makecache', file=sys.stderr)
        sys.exit(1)
    today = datetime.today()
    age = max(
        today - datetime.fromtimestamp(solvecache.stat().st_mtime),
        today - datetime.fromtimestamp(filenames.stat().st_mtime)
    )
    if age.days > 1:
        print('Cache older than a day, update it via:\n'
              f'  $ sudo dnf --repo={repo} makecache', file=sys.stderr)

    try:
        cmd = ('installcheck', arch, str(solvecache), str(filenames))
        cp = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        print('Install /usr/bin/installcheck first:\n'
              '  $ sudo dnf install /usr/bin/installcheck', file=sys.stderr)
        sys.exit(1)

    packages = {}

    for line in cp.stdout.splitlines():
        if line.startswith("can't install "):
            nevra, name = CANNOT_RE.match(line).groups()
            packages[name] = {
                'nevra': nevra,
                'problems': [],
            }
        else:
            packages[name]['problems'].append(line)

    return packages


@click.command(name='check-fti')
@click.option('--repo', default='rawhide', show_default=True, metavar='REPO',
              help='What repository to check')
@click.option('--arch', default='x86_64', show_default=True, metavar='ARCH',
              help='What architecture to check')
@click.argument('results', type=click.Path(writable=True),
                default='noninstallable.json')
@click.pass_context
def check_fti(ctx, repo, arch, results):
    """Check all Python 2 packages to whether they install"""
    data = get_data(*ctx.obj['datadirs'])
    PKG_NAMES = set(pkg_names(data))
    results = pathlib.Path(results)

    filtered = {k: v for k, v in installcheck(repo, arch).items()
                if k in PKG_NAMES}

    results.write_text(json.dumps(filtered,  indent=2))

    print(f'Results in {results}\n'
          f'There are {len(filtered)} noninstallable Python 2 packages.')
