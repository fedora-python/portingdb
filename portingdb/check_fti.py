import json
import pathlib
import re
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from urllib.parse import urlencode

import bugzilla
import click

from portingdb.load_data import get_data

CANNOT_RE = re.compile(r"can't install ((.+)-[^-]+-[^-]+):")
BUGZILLA = 'bugzilla.redhat.com'
FTI_TRACKER = 1750909  # F32FailsToInstall
PY2_TRACKER = 1690439  # PY2FTBI


def pkgs_srpm(data):
    sources = {}
    for srpm in data['packages'].values():
        for nevr, rpm in srpm['rpms'].items():
            if 2 in rpm['py_deps'].values():
                pkg_name = nevr.rsplit('-', 2)[0]
                sources[pkg_name] = srpm['name']
    return sources


def bugzillas():
    bzapi = bugzilla.Bugzilla(BUGZILLA)
    query = bzapi.build_query(product='Fedora')
    query['blocks'] = FTI_TRACKER
    bugz = {}
    for bug in sorted(bzapi.query(query), key=lambda b: -b.id):
        if bug.resolution == 'DUPLICATE':
            continue
        if bug.component not in bugz:
            bugz[bug.component] = bug
    return bugz


def open_bz(name, *, source, nevra, problems):
    summary = f"{name} fails to install in Fedora rawhide"

    description = f'{nevra} fails to install in Fedora rawhide:\n\n'
    description += '\n'.join(problems)
    description += ('\n\nThis is most likely caused by a dependency that was '
                    'retired.\nPlease drop the dependency, unretire the '
                    'dependency or remove the package. Thanks')

    url_prefix = 'https://bugzilla.redhat.com/enter_bug.cgi?'
    params = {
        'short_desc': summary,
        'comment': description,
        'component': source,
        'blocked': f'{PY2_TRACKER}, {FTI_TRACKER}',
        'product': 'Fedora',
        'version': 'rawhide',
        'bug_severity': 'high',
    }

    # Rate-limit opening browser tabs
    webbrowser.open(url_prefix + urlencode(params))
    time.sleep(1)


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
@click.option('--open-bug-reports/--no-open-bug-reports',
              help=('Open a browser page (!) with a bug report template for '
                    'each package that seems to need a bug report'))
@click.pass_context
def check_fti(ctx, repo, arch, results, open_bug_reports):
    """Check all Python 2 packages to whether they install"""
    data = get_data(*ctx.obj['datadirs'])
    rpms_srpms = pkgs_srpm(data)
    results = pathlib.Path(results)
    filtered = {}
    print('Querying Bugzilla...')
    bugz = bugzillas()

    sources_reported_now = set()

    print('Running installcheck...')
    for name, info in installcheck(repo, arch).items():
        if name not in rpms_srpms.keys():
            continue
        source = rpms_srpms[name]
        filtered[name] = info
        filtered[name]['source'] = source
        if source.startswith('sugar-'):
            # too many broken sugars to file separately
            continue

        will_file = False

        if source not in bugz:
            print(f'{source}: {name} has no bug')
            will_file = True
        elif (bugz[source].status == 'CLOSED' and
              bugz[source].resolution != 'EOL'):
            print(f'{source}: {name} has CLOSED bug: {bugz[source].id}')
            will_file = True

        if source in sources_reported_now:
            will_file = False

        if will_file and open_bug_reports:
            sources_reported_now.add(source)
            open_bz(name, **filtered[name])

    results.write_text(json.dumps(filtered,  indent=2))

    print(f'\nResults in {results}\n'
          f'There are {len(filtered)} noninstallable Python 2 packages.')
