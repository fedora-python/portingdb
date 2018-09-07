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

xml_default = {}
xml_option_args = {}

for x in 'filelists', 'primary':
    xml_default[x] = list(Path('/var/cache/dnf').glob(
        f'rawhide-????????????????/repodata/*-{x}.xml.gz'))

    if len(xml_default[x]) == 1:
        xml_option_args[x] = {'default': xml_default[x][0]}
    else:
        xml_option_args[x] = {'required': True}


def log(*args, **kwargs):
    """Print to stderr"""
    kwargs.setdefault('file', sys.stderr)
    print(*args, **kwargs)


def handle_filename(result, filename):
    """Look at a filename a RPM installs, and update "result" accordingly"""
    if filename.startswith((
        '/usr/lib/python2.7/',
        '/usr/lib64/python2.7/',
    )):
        # Importable module; consider this for dropping
        result['notes'].add('Python 2 module')
        result['ignore'] = False

    if filename.endswith('info/entry_points.txt'):
        result['notes'].add('Entrypoint')
        result.setdefault('entrypoints', []).append(filename)
    elif filename.startswith((
        '/usr/lib/python2.7/site-packages/libtaskotron/ext/',
    )):
        # Taskotron extension
        result['notes'].add('Taskotron extension')
        result['keep'] = True
    elif filename.startswith((
        '/usr/lib/python3.7/',
        '/usr/lib64/python3.7/',
    )):
        # Python 3 module; ignore here, but freak out
        result['notes'].add('Python 3 module')
    elif filename.startswith((
        '/usr/share/doc/',
        '/usr/share/gtk-doc/',
        '/usr/share/man/',
        '/usr/share/licenses/',
    )):
        # Doc/licence; doesn't block dropping
        result['notes'].add('Docs/Licences')
    elif filename.startswith((
        '/usr/share/locale/',
    )) or '/LC_MESSAGES/' in filename:
        # Locales; doesn't block dropping
        result['notes'].add('Locales')
    elif filename.startswith((
        '/usr/share/icons/',
        '/usr/share/pixmaps/',
    )):
        # Icons; doesn't block dropping
        result['notes'].add('Icons')
    elif dir_or_exact(filename, (
        '/usr/share/pygtk/2.0/defs',
        '/usr/share/gst-python/0.10/defs',
        '/usr/share/pygtk/2.0/argtypes',
    )) or filename.endswith((
        '.glade',
        '.ui',
    )):
        # UIs; doesn't block dropping
        result['notes'].add('UIs')
    elif filename.endswith((
        '.html',
        '.jinja2',
    )) or 'templates' in filename:
        # Templates; doesn't block dropping
        result['notes'].add('Templates')
    elif filename.startswith((
        '/usr/lib/tmpfiles.d/',
        '/usr/lib/udev/rules.d/',
        '/usr/lib/pkgconfig/',
        '/usr/lib64/pkgconfig/',
        '/usr/share/bash-completion/',
        '/usr/src/',
        '/var/cache/',
        '/var/lib/',
        '/var/log/',
        '/var/run/',
        '/var/spool/',
        '/var/tmp/',
        '/etc/',
    )):
        # Logs/Cache/Config; doesn't block dropping
        result['notes'].add('Logs/Cache/Config')
    elif dir_or_exact(filename, (
        '/usr/lib/.build-id',
    )):
        # Build ID; doesn't block dropping
        result['notes'].add('Build ID')
    elif dir_or_exact(filename, (
        '/usr/lib/qt4/plugins/designer',
        '/usr/lib64/qt4/plugins/designer',
        '/usr/share/autocloud',
        '/usr/share/conda',
        '/usr/share/fmn.web',
        '/usr/share/genmsg',
        '/usr/share/gst-python',
        '/usr/share/libavogadro',
        '/usr/share/myhdl',
        '/usr/share/ocio',
        '/usr/share/os-brick',
        '/usr/share/pgu',
        '/usr/share/pygtk',
        '/usr/share/pygtkchart',
        '/usr/share/python-dmidecode',
        '/usr/share/python-ldaptor',
        '/usr/share/tomoe',
        '/usr/share/viewvc',
        '/usr/share/pygtk/2.0',
    )):
        # Various self contained files
        result['notes'].add('Self Contained Files')
    elif filename in (
        '/usr/bin/tg-admin', # self contained for the module (TurboGears)
    ):
        # Those are hardcoded commands we don't care about
        result['notes'].add('Ignored command')
        result['filename_command_ignored'] = filename
    elif filename.startswith((
        '/usr/bin/',
        '/usr/sbin/',
        '/usr/libexec/',
        '/usr/lib/systemd/system/',
    )):
        # Command; might be needed
        result['notes'].add('Command')
        result['keep'] = True
        result['filename_command'] = filename
    elif filename.startswith((
        '/usr/share/appdata/',
        '/usr/share/applications/',
        '/usr/share/metainfo/',
    )):
        # Application; might be needed
        result['notes'].add('Application')
        result['keep'] = True
        result['filename_application'] = filename
    elif not filename.startswith((
        '/usr/lib/python2.7/',
        '/usr/lib64/python2.7/',
    )):
        # Something else; might be needed
        result['notes'].add('Unknown file')
        result['filename_unknown'] = filename


def handle_entrypoints(result, config):
    """Look at a parsed entrypoints config, update "result" accordingly"""
    for section in config.sections():
        if section in ('console_scripts', 'gui_scripts'):
            # Checked as commands in /usr/bin
            pass
        elif section in ('distutils.setup_keywords',
                         'distutils.commands',
                         'cliff.formatter.show',
                         'openstack.congressclient.v1',
                         'fiona.fio_commands',
                         'python.templating.engines',
                         'turbomail.transports',
                         'twine.registered_commands',
                         'paste.app_factory',
                         'buildutils.optional_commands',
                         'zaqarclient.transport',
                         'apscheduler.triggers',
                         'zest.releaser.releaser.middle',
                         'babel.extractors',  # babel has this +1 unused pkg
                         'babel.checkers',
                         'moksha.consumer',
                         'cliff.formatter.list',
                         'openstack.cli.extension', # the CLI should use py3
                         'beaker.backends', # only beaker has this
                         'sphinx_themes', # we only keep non leafs
                         'sphinx.html_themes',
                         'tw2.widgets', # plugins for a framework, not app
                        ) or section.startswith((
                         'turbogears.',  # plugins for a framework, not app
                        )):
            # Decided to be useless and/or self contained
            pass
        elif section.startswith('paste.'):
            pass
        elif (section == 'envisage.plugins'
              and result['name'] == 'python2-envisage'):
            pass
        elif section in ('pytest11', ):
            result['keep'] = True
            result['notes'].append('Pytest plugin')
            result['plugin_pytest'] = section
        elif section in ('trac.plugins', ):
            result['keep'] = True
            result['notes'].append('Trac plugin')
            result['plugin_trac'] = section
        elif section.startswith('avocado.plugins'):
            result['keep'] = True
            result['notes'].append('Avocado plugin')
            result['plugin_avocado'] = section
        elif section.startswith(('pylama.linter', 'flake8')):
            result['keep'] = True
            result['notes'].append('Flake 8 / PyLama plugin')
            result['plugin_pylama'] = section
        elif section.startswith('pulp.extensions'):
            result['keep'] = True
            result['notes'].append('Pulp plugin')
            result['plugin_pulp'] = section
        elif section == 'certbot.plugins':
            result['keep'] = True
            result['notes'].append('Certobot plugin')
            result['plugin_certbot'] = section
        else:
            # Possibly a plugin
            result['needs_investigation'] = True
            result['plugin_unknown'] = section


def dir_or_exact(filename, patterns):
    patterns = tuple(p[:-1] if p.endswith('/') else p for p in patterns)
    dirs = tuple(p + '/' for p in patterns)
    return filename.startswith(dirs) or filename in patterns


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


class SaxPrimaryHandler(xml.sax.ContentHandler):
    def __init__(self):
        super().__init__()
        self._sources = collections.defaultdict(set)
        self.name_parts = None
        self.source_parts = None

    @property
    def sources(self):
        return {k: list(v) for k, v in self._sources.items()}

    def startElement(self, name, attrs):
        if name == 'package' and attrs['type'] == 'rpm':
            self.current_result = {}
        elif name == 'name':
            self.name_parts = []
        elif name == 'rpm:sourcerpm':
            self.source_parts = []

    def endElement(self, name):
        if name == 'package':
            log(self.current_result)
            source = self.current_result['source'].rsplit('-', 2)[0]
            self._sources[source].add(self.current_result['name'])
            del self.current_result
        elif name == 'name':
            self.current_result['name'] = ''.join(self.name_parts)
            self.name_parts = None
        elif name == 'rpm:sourcerpm':
            self.current_result['source'] = ''.join(self.source_parts)
            self.source_parts = None

    def characters(self, content):
        if self.name_parts is not None:
            self.name_parts.append(content)
        elif self.source_parts is not None:
            self.source_parts.append(content)


@click.command(name='check-drops')
@click.option('-f', '--filelist', type=click.File('rb'),
              **xml_option_args['filelists'],
              help='Location of the filelist xml.gz file '
              '(required if not found automatically)')
@click.option('-p', '--primary', type=click.File('rb'),
              **xml_option_args['primary'],
              help='Location of the primary xml.gz file '
              '(required if not found automatically)')
@click.option('--cache-sax/--no-cache-sax',
              help='Use cached results of filelist parsing, if available '
              '(crude; use when hacking on other parts of the code)')
@click.option('--cache-rpms/--no-cache-rpms',
              help='Use previously downloaded RPMs '
              '(crude; use when hacking on other parts of the code)')
@click.pass_context
def check_drops(ctx, filelist, primary, cache_sax, cache_rpms):
    """Check packages that should be dropped from the distribution."""
    db = ctx.obj['db']

    cache_dir.mkdir(exist_ok=True)

    # Analyze filelists.xml.gz and primary.xml.gz

    cache_path = cache_dir / 'sax_results.json'

    if (cache_sax and cache_path.exists()):
        with cache_path.open('r') as f:
            results, sources = json.load(f)
    else:
        filelist = gzip.GzipFile(fileobj=filelist, mode='r')

        handler = SaxFilesHandler()
        xml.sax.parse(filelist, handler)

        results = handler.results

        primary = gzip.GzipFile(fileobj=primary, mode='r')

        handler = SaxPrimaryHandler()
        xml.sax.parse(primary, handler)

        sources = handler.sources

        with cache_path.open('w') as f:
            json.dump([results, sources], f)

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

    # hardcoded packages

    # catfish is seriously mispackaged,
    # see https://src.fedoraproject.org/rpms/catfish/pull-request/1
    if 'catfish' in results:
        results['catfish']['needs_investigation'] = True

    # rpkg needs to stay for 3rd party consumers
    results['python2-rpkg']['keep'] = True

    for result in results.values():
        if result.get('needs_investigation'):
            result['verdict'] = 'investigate'
        elif result.get('keep'):
            result['verdict'] = 'keep'
        elif result.get('legacy_leaf'):
            result['verdict'] = 'drop_now'
        else:
            result['verdict'] = 'drop_later'

    # Set sources and determine retirement action
    for name, result in results.items():
        result['source'], *_ = (s for s, p in sources.items() if name in p)
    for source, pkgs in sources.items():
        local_results = [r for r in results.values() if r['name'] in pkgs]
        if len(local_results) < len(pkgs):
            # subpackages we know nothing about
            source_verdict = 'keep'
        elif all(r['verdict'] == 'drop_now' for r in local_results):
            source_verdict = 'retire_now'
        elif all(r['verdict'].startswith('drop_') for r in local_results):
            source_verdict = 'retire_later'
        else:
            source_verdict = 'keep'

        for result in local_results:
            result['source_verdict'] = source_verdict

    # Output it all

    print(json.dumps(results, indent=2))

    with open(cache_dir / ('results.json'), 'w') as f:
        json.dump(results, f, indent=2)

    with open(cache_dir / ('results-sources.json'), 'w') as f:
        json.dump(sources, f, indent=2)

    log('\nBinary packages:')
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

    log('\nSource packages:')
    # we will loose some information here, but that is OK for stats
    source_results = {result['source']: result for result in results.values()}
    stats_counter = collections.Counter(r['source_verdict'] for r in source_results.values())
    for package, number in stats_counter.most_common():
        log('{}: {}'.format(number, package))

    for verdict in stats_counter:
        if verdict == 'keep':
            continue
        filtered = {n: r for n, r in results.items() if r['source_verdict'] == verdict}
        with open(cache_dir / ('results-' + verdict + '-srpms.json'), 'w') as f:
            json.dump(filtered, f, indent=2)
        with open(cache_dir / ('results-' + verdict + '-srpms.txt'), 'w') as f:
            for name in set(r['source'] for r in filtered.values()):
                print(name, file=f)
