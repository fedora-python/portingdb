"""
DNF plugin for getting the Python 3 porting status

Put this in your DNF plugin directory, then run:

    $ dnf --enable=rawhide --enable=rawhide-source py3query -o fedora.json

This will give you a file "fedora.json" with information for portingdb.
"""

from __future__ import print_function

import operator
import sys
import json
import collections

import hawkey
import dnf
import dnf.cli
import dnf.subject
import dnfpluginscore
from dnfpluginscore import _
import bugzilla  # python-bugzilla

BUGZILLA_URL = 'bugzilla.redhat.com'
TRACKER_BUG_IDS = [
    1014209,  # (F23PYTHON3) Replace Python 2 with Python 3 in default installation
    1024796,  # (PY3PACKAGER) fedora-packager: Use Python 3 instead of Python 2
    1282146,  # Packages with outdated spec (not using python-provides macro, not python2 subpackages)
    1285816,  # The Python 3 tracking bug
]

SEED_PACKAGES = {
    2: [
        'python-devel', 'python2-devel', 'python', 'python-libs',
        'python(abi) = 2.7', '/usr/bin/python', '/usr/bin/python2',
        '/usr/bin/python2.7', 'libpython2.7.so.1.0',
        'pygtk2', 'pygobject2', 'pycairo',
    ],
    3: [
        'python3-devel', 'python3', 'python3-libs', 'python(abi) = 3.4',
        '/usr/bin/python3', '/usr/bin/python3.4', 'libpython3.4m.so.1.0',
        'libpython3.so', 'python3-cairo',
        'python(abi) = 3.5', '/usr/bin/python3.5', 'libpython3.5m.so.1.0',
    ]
}

PROVIDES_BLACKLIST = (
    'postscriptdriver(', 'pkgconfig(', 'perl(', 'mvn(', 'mimehandler(',
    'config(', 'bundled(', 'application(', 'appdata(',
)


class Py3Query(dnf.Plugin):

    name = 'py3query'

    def __init__(self, base, cli):
        super(Py3Query, self).__init__(base, cli)
        self.base = base
        self.cli = cli
        if self.cli is not None:
            self.cli.register_command(Py3QueryCommand)


def parse_arguments(args):
    parser = dnfpluginscore.ArgumentParser(Py3QueryCommand.aliases[0])

    parser.add_argument('--output', '-o', metavar='FILE', action='store',
                        help=_('write output to the given file'))

    parser.add_argument('--no-bz', dest='fetch_bugzilla', action='store_false',
                        default=True, help=_("Don't get Bugzilla links"))

    return parser.parse_args(args), parser


def progressbar(seq, text, namegetter=str):
    total = len(seq)
    prev_len = 0

    def printer(i, name):
        pad_len = prev_len - len(name)
        total_len = 20
        progress = ('=' * (total_len * i // total)).ljust(total_len)
        if i == 0:
            r = ''
        else:
            r = '\r'
        line = '{}[{}] {}/{} {}: {}{} '.format(
            r, progress, i, total,text, name, ' ' * pad_len)
        print(line, end='', file=sys.stderr)
        sys.stderr.flush()
        return len(name)

    try:
        for i, item in enumerate(seq):
            prev_len = printer(i, str(namegetter(item)))
            yield item
    except GeneratorExit:
        pass
    except:
        printer(i, 'Error!')
        print(file=sys.stderr)
        raise

    printer(total, 'Done!')
    print(file=sys.stderr)


def is_ported(pkgs, python_versions):
    # Look at the Python dependencies of given packages, based on the
    # name only (this means different arches are grouped into one)
    name_by_version = collections.defaultdict(set)
    for p in pkgs:
        for v in python_versions[p]:
            name_by_version[v].add(p.name)
    if name_by_version[2] & name_by_version[3]:
        # If a package depends on *both* py2 and py3, it's not ported
        return False
    # Otherwise, a srpm isn't ported if it has more packages that need py2
    # than those that need py3
    return len(name_by_version[3]) >= len(name_by_version[2])


def format_rpm_name(pkg):
    if pkg.epoch:
        epoch = '{}:'.format(pkg.epoch)
    else:
        epoch = ''
    return '{pkg.name}-{epoch}{pkg.version}-{pkg.release}'.format(
        pkg=pkg, epoch=epoch)


class Py3QueryCommand(dnf.cli.Command):

    """The util command there is extending the dnf command line."""
    aliases = ('py3query',)
    summary = _('query the python3 porting status')
    usage = _('[OPTIONS] [KEYWORDS]')

    def configure(self, args):
        self.opts, self.parser = parse_arguments(args)

        if self.opts.help_cmd:
            return

        demands = self.cli.demands
        demands.sack_activation = True
        demands.available_repos = True

    def run(self, args):
        if self.opts.help_cmd:
            print(self.parser.format_help())
            return

        self.base_query = self.base.sack.query()
        self.pkg_query = self.base_query.filter(reponame='rawhide')
        self.src_query = self.base_query.filter(reponame='rawhide-source').filter(arch=['src'])

        # python_versions: {package: set of Python versions}
        python_versions = collections.defaultdict(set)
        # rpm_pydeps: {package: set of dep names}
        rpm_pydeps = collections.defaultdict(set)
        # dep_versions: {dep name: Python version}
        dep_versions = collections.defaultdict(set)
        for n, seeds in SEED_PACKAGES.items():
            provides = sorted(self.all_provides(seeds), key=str)

            # This effectively includes packages that still need
            # Python 3.4 while Rawhide only provides Python 3.5
            provides += sorted(seeds)

            for dep in progressbar(provides, 'Getting py{} requires'.format(n)):
                dep_versions[str(dep)] = n
                for pkg in self.whatrequires(dep):
                    python_versions[pkg].add(n)
                    rpm_pydeps[pkg].add(str(dep))

        # srpm_names: {package: srpm name}
        # by_srpm_name: {srpm name: set of packages}
        srpm_names = {}
        by_srpm_name = collections.defaultdict(set)
        for pkg in progressbar(python_versions.keys(), 'Getting SRPMs'):
            srpm_name = hawkey.split_nevra(pkg.sourcerpm).name
            srpm_names[pkg] = srpm_name
            by_srpm_name[srpm_name].add(pkg)

        # deps_of_pkg: {package: set of packages}
        deps_of_pkg = collections.defaultdict(set)
        all_provides = {str(r): r for p in python_versions for r in p.provides
                        if not str(r).startswith(PROVIDES_BLACKLIST)}
        for pkg in progressbar(sorted(python_versions.keys()), 'Getting requirements'):
            reqs = set()
            for provide in pkg.provides:
                reqs.update(self.whatrequires(provide))
            for req in reqs:
                if req in python_versions.keys():
                    deps_of_pkg[req].add(pkg)

        # deps_of_pkg: {srpm name: info}
        json_output = dict()
        for name in progressbar(by_srpm_name, 'Generating output'):
            pkgs = sorted(by_srpm_name[name])
            r = json_output[name] = {}
            if is_ported(pkgs, python_versions):
                r['status'] = 'released'
            else:
                r['status'] = 'idle'
            r['rpms'] = {format_rpm_name(p):
                         {str(d): dep_versions[d] for d in rpm_pydeps[p]}
                        for p in pkgs}
            r['deps'] = sorted(set(srpm_names[d]
                                   for p in pkgs
                                   for d in deps_of_pkg.get(p, '')
                                   if srpm_names[d] != name))

        # add Bugzilla links
        if self.opts.fetch_bugzilla:
            bar = iter(progressbar(['connecting', 'tracker', 'individual'],
                                   'Getting bugs'))

            next(bar)
            bz = bugzilla.RHBugzilla(BUGZILLA_URL)

            next(bar)
            include_fields = ['id', 'depends_on', 'component', 'status',
                              'resolution']
            trackers = bz.getbugs(TRACKER_BUG_IDS,
                                  include_fields=include_fields)
            all_ids = [b for t in trackers for b in t.depends_on]

            next(bar)
            bugs = bz.getbugs(all_ids, include_fields=include_fields)
            bar.close()

            def bug_namegetter(bug):
                return '{bug.id} {bug.status} {bug.component}'.format(bug=bug)

            for bug in progressbar(bugs, 'Merging bugs',
                                   namegetter=bug_namegetter):
                r = json_output.get(bug.component, {})
                url = '{bug.weburl}#{bug.status}'.format(bug=bug)
                status = bug.status
                if bug.resolution:
                    status += ' ' + bug.resolution
                r.setdefault('links', {})['bug'] = [bug.weburl, status]
                inprogress_statuses = ('ASSIGNED', 'POST', 'MODIFIED', 'ON_QA')
                inprogress_resolutions = ('CURRENTRELEASE', 'RAWHIDE',
                                          'ERRATA', 'NEXTRELEASE')
                if r.get('status') == 'idle' and bug.status != 'NEW':
                    r['status'] = 'in-progress'

        # Print out output

        if self.opts.output:
            with open(self.opts.output, 'w') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)
        else:
            json.dump(json_output, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.flush()

    def all_provides(self, seeds):
        pkgs = set()
        for seed in seeds:
            query = dnf.subject.Subject(seed, ignore_case=True).get_best_query(
                self.base.sack, with_provides=False)
            query = query.filter(reponame='rawhide')
            pkgs.update(query.run())
        provides = set()
        for pkg in sorted(pkgs):
            provides.update(pkg.provides)
        return provides

    def whatrequires(self, dep):
        query = self.pkg_query
        query = query.filter(requires=dep)
        return set(query)
