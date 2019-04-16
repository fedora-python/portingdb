import json
import pathlib
import re
import shlex
import subprocess
import sys
from functools import partial

import click

from portingdb.load_data import get_data


ROOT_LOG = pathlib.Path('/var/lib/mock/fedora-rawhide-x86_64/result/root.log')
RE_INSTALLED = re.compile(r'DEBUG util.py:\d+:\s+Installed:')
RE_COMPLETE = re.compile(r'DEBUG util.py:\d+:\s+Complete!')
RE_ERROR = re.compile(r'DEBUG util.py:\d+:\s+BUILDSTDERR: Error:\s*')
RE_SKIP_BROKEN = re.compile(r"DEBUG util.py:\d+:\s+\(try to add.*")


def mock(*command, **kwargs):
    command = ('mock', '-r', 'fedora-rawhide-x86_64') + command
    print(f'$ {" ".join(shlex.quote(p) for p in command)}')
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def pkg_names(data):
    for srpm in data['packages'].values():
        for nevr, rpm in srpm['rpms'].items():
            if 2 in rpm['py_deps'].values():
                yield nevr.rsplit('-', 2)[0]


def was_installed():
    capturing = False
    packages = set()
    for line in ROOT_LOG.read_text().splitlines():
        if not capturing:
            if RE_INSTALLED.match(line):
                capturing = True
            continue
        else:
            if RE_COMPLETE.match(line):
                return packages
            line = line.strip().split()
            assert line[0] == 'DEBUG'
            assert line[1].startswith('util.py:')
            for nevra in line[2:]:
                packages.add(nevra.rsplit('-', 2)[0])
    else:
        raise AssertionError('No start or end matched in log')


def eprint_install_error():
    capturing = False
    for line in ROOT_LOG.read_text().splitlines():
        if not capturing:
            if RE_ERROR.match(line):
                if 'Unable to find a match' in line:
                    print(line)
                    print()
                    return
                capturing = True
            continue
        else:
            if RE_SKIP_BROKEN.match(line):
                print()
                return
            if 'BUILDSTDERR:' in line:
                line = line[line.index('BUILDSTDERR:')+14:]
                print(line)


@click.command(name='check-fti')
@click.argument('results', type=click.Path(writable=True),
                default='installable.json')
@click.pass_context
def check_fti(ctx, results):
    """Check all Python 2 packages to whether they install"""
    data = get_data(*ctx.obj['datadirs'])
    results = pathlib.Path(results)

    if results.exists():
        installable = json.loads(results.read_text())
    else:
        installable = {}

    investigate = set()

    PKG_NAMES = set(pkg_names(data))

    for pkg in PKG_NAMES:
        if pkg in installable:
            continue
        mock('init', check=True)
        cp = mock('install', pkg)
        if cp.returncode == 0:
            installed_pkgs = was_installed()
            if pkg not in installed_pkgs:
                investigate.add(pkg)
            installed_pkgs &= PKG_NAMES
            print('Installed', ', '.join(installed_pkgs), '\n')
            for p in installed_pkgs:
                installable[p] = True
        elif cp.returncode == 30:
            installable[pkg] = False
            eprint_install_error()
        else:
            investigate.add(pkg)
        results.write_text(json.dumps(installable))

    if investigate:
        print('Investigate', ', '.join(investigate))
