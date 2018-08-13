"""
Check which packages should be dropped for the Mass Python 2 Package Removal:
https://fedoraproject.org/wiki/Changes/Mass_Python_2_Package_Removal

To run this:
- Install portingdb in a virtualenv:
  python -m pip install -e.

- Load portingdb data:
    python3 -m portingdb --datadir data/ load

- Run this script:
  python -m portingdb check-drops > drops.json

The script creates the directory `_check_drops`, containing cached stuff and
some result files.

"""

from pathlib import Path
import xml.sax
import gzip
import sys
import time
import json
import subprocess
import configparser
import shutil
import collections

import click

from portingdb import tables


cache_dir = Path('./_check_drops')

default_filelist = list(Path('/var/cache/dnf').glob(
    'rawhide-????????????????/repodata/*-filelists.xml.gz'))

if len(default_filelist) == 1:
    filelist_option_args = {'default': default_filelist[0]}
else:
    filelist_option_args = {'required': True}


def log(*args, **kwargs):
    """Print to stderr"""
    kwargs.setdefault('file', sys.stderr)
    print(*args, **kwargs)


def handle_filename(result, filename):
    """Look at a filename a RPM installs, and update "result" accordingly"""
    if filename.endswith('info/entry_points.txt'):
        result['notes'].add('Entrypoint')
        result.setdefault('entrypoints', []).append(filename)
    elif filename.startswith((
        '/usr/lib/python2.7/',
        '/usr/lib64/python2.7/',
    )):
        # Importable module; consider this for dropping
        result['notes'].add('Python 2 module')
        result['ignore'] = False
    elif filename.startswith((
        '/usr/share/doc/',
        '/usr/share/man/',
        '/usr/share/licenses/',
    )):
        # Doc/licence; doesn't block dropping
        result['notes'].add('Docs/Licences')
    elif filename.startswith((
        '/usr/lib/.build-id/',
    )) or filename == '/usr/lib/.build-id':
        # Build ID; doesn't block dropping
        result['notes'].add('Build ID')
    elif filename.startswith((
        '/usr/bin/',
        '/usr/sbin/',
    )):
        # Command; might be needed
        result['notes'].add('Command')
        result['keep'] = True
        result['filename_command'] = filename
    else:
        # Something else; might be needed
        result['notes'].add('Unknown file')
        result['keep'] = True
        result['filename_unknown'] = filename


def handle_entrypoints(result, config):
    """Look at a parsed entrypoints config, update "result" accordingly"""
    for section in config.sections():
        if section in ('console_scripts', 'gui_scripts'):
            # Checked as commands in /usr/bin
            pass
        elif section in ('pytest11', ):
            result['notes'].append('Pytest plugin')
            result['plugin_pytest'] = section
        else:
            # Possibly a plugin
            result['keep'] = True
            result['needs_investigation'] = True
            result['plugin_unknown'] = section



class SaxFilesHandler(xml.sax.ContentHandler):
    def __init__(self):
        super().__init__()
        self.results = {}
        self.filename_parts = None

    def startElement(self, name, attrs):
        if name == 'package':
            name = attrs['name']
            self.current_result = {
                'name': name,
                'arch': attrs['arch'],
                'notes': set(),
                'ignore': True,
            }
        elif name == 'version':
            _cp = self.current_result
            _cp['nevra'] = [
                _cp['name'], attrs['epoch'], attrs['ver'], attrs['rel'],
                _cp.pop('arch')]
        elif name == 'file':
            self.filename_parts = []

    def endElement(self, name):
        if name == 'package':
            if not self.current_result.pop('ignore'):
                self.current_result['notes'] = sorted(self.current_result['notes'])
                log(self.current_result)
                self.results[self.current_result['name']] = self.current_result
            del self.current_result
        elif name == 'file':
            filename = ''.join(self.filename_parts)
            handle_filename(self.current_result, filename)
            self.filename_parts = None

    def characters(self, content):
        if self.filename_parts is not None:
            self.filename_parts.append(content)


@click.command(name='check-drops')
@click.option('-f', '--filelist', type=click.File('rb'),
              **filelist_option_args,
              help='Location of the filelist xml.gz file '
              '(required if not found automatically)')
@click.option('--cache-sax/--no-cache-sax',
              help='Use cached results of filelist parsing, if available '
              '(crude; use when hacking on other parts of the code)')
@click.option('--cache-rpms/--no-cache-rpms',
              help='Use previously downloaded RPMs '
              '(crude; use when hacking on other parts of the code)')
@click.pass_context
def check_drops(ctx, filelist, cache_sax, cache_rpms):
    """Check packages that should be dropped from the distribution."""
    db = ctx.obj['db']

    cache_dir.mkdir(exist_ok=True)

    # Analyze filelists.xml.gz

    cache_path = cache_dir / 'sax_results.json'

    if (cache_sax and cache_path.exists()):
        with cache_path.open('r') as f:
            results = json.load(f)
    else:
        filelist = gzip.GzipFile(fileobj=filelist, mode='r')

        handler = SaxFilesHandler()
        xml.sax.parse(filelist, handler)

        results = handler.results

        with cache_path.open('w') as f:
            json.dump(results, f)

    log('Packages considered: ', len(results))

    # For packages with entrypoints, download the corresponding RPM

    entrypoint_packages = []
    for name, result in results.items():
        entrypoints = result.get('entrypoints')
        if entrypoints and not result.get('keep'):
            entrypoint_packages.append(name)

    log('Packages with interesting entrypoints: ', len(entrypoint_packages))

    rpm_dl_path = cache_dir / 'rpm_cache'
    if rpm_dl_path.exists() and not cache_rpms:
        shutil.rmtree(rpm_dl_path)
    rpm_dl_path.mkdir(exist_ok=True)

    subprocess.run(
        ['dnf', 'download', '--repo=rawhide', '--',
         *entrypoint_packages],
        cwd=rpm_dl_path,
        stdout=sys.stderr,
        check=True)

    # Analyze entrypoints from downloaded RPMs

    for rpm_path in rpm_dl_path.iterdir():
        proc = subprocess.run(
            ['rpm', '-q', '--qf', '%{name}', '-p', rpm_path],
            stdout=subprocess.PIPE,
            check=True)
        name = proc.stdout.decode('utf-8')
        result = results.get(name)
        if result:
            for entrypoint in result.get('entrypoints'):
                rmp2cpio_proc = subprocess.Popen(
                    ['rpm2cpio', rpm_path],
                    stdout=subprocess.PIPE)
                cpio_proc = subprocess.run(
                    ['cpio', '-i', '--to-stdout', '.' + entrypoint],
                    stdout=subprocess.PIPE,
                    stdin=rmp2cpio_proc.stdout,
                    check=True)
                if rmp2cpio_proc.wait() != 0:
                    raise Exception()
                config = configparser.ConfigParser()
                if not cpio_proc.stdout:
                    result.setdefault('empty_entrypoints', []).append(entrypoint)
                    result['needs_investigation'] = True
                    result['keep'] = True
                    continue
                try:
                    config.read_string(cpio_proc.stdout.decode('utf-8'))
                except configparser.Error as e:
                    result.setdefault('bad_entrypoints', {})[entrypoint] = str(e)
                    result['needs_investigation'] = True
                    result['keep'] = True
                    continue
                handle_entrypoints(result, config)
                result['entrypoints_handled'] = True

    # Adjust "needs_investigation" for unknown files and unhandled entrypoints

    for name, result in results.items():
        if not result.get('keep'):
            entrypoints = result.get('entrypoints')
            if result.get('entrypoints'):
                if not result.pop('entrypoints_handled'):
                    result.notes.append('Entrypoints not handled')
                    result['needs_investigation'] = True
            if result.get('filename_unknown'):
                result['needs_investigation'] = True

    # Set legacy_leaf flags

    query = db.query(tables.RPM)
    for rpm in query:
        # TODO: better way to match portingdb entry to package name
        name = rpm.rpm_name.rsplit('-', 2)[0]
        result = results.get(name)
        if result:
            result['legacy_leaf'] = rpm.legacy_leaf

    for result in results.values():
        if result.get('needs_investigation'):
            result['verdict'] = 'investigate'
        elif result.get('keep'):
            result['verdict'] = 'keep'
        elif result.get('legacy_leaf'):
            result['verdict'] = 'drop_now'
        else:
            result['verdict'] = 'drop_later'

    # Output it all

    print(json.dumps(results, indent=2))

    with open(cache_dir / ('results.json'), 'w') as f:
        json.dump(results, f, indent=2)

    stats_counter = collections.Counter(r['verdict'] for r in results.values())
    for package, number in stats_counter.most_common():
        log('{}: {}'.format(number, package))

    for verdict in stats_counter:
        filtered = {n: r for n, r in results.items() if r['verdict'] == verdict}
        with open(cache_dir / ('results-' + verdict + '.json'), 'w') as f:
            json.dump(filtered, f, indent=2)
        with open(cache_dir / ('results-' + verdict + '.txt'), 'w') as f:
            for name in filtered:
                print(name, file=f)
