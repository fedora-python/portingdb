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

SEED_PACKAGES = {
    2: [
        'python-devel', 'python2-devel', 'python', 'python-libs',
        'python(abi) = 2.7', '/usr/bin/python', '/usr/bin/python2',
        '/usr/bin/python2.7', 'libpython2.7.so.1.0',
        'pygtk2', 'pygobject2', 'pycairo', 'pygobject3',
    ],
    3: [
        'python3-devel', 'python3', 'python3-libs', 'python(abi) = 3.4',
        '/usr/bin/python3', '/usr/bin/python3.4', 'libpython3.4m.so.1.0',
        'libpython3.so', 'python3-cairo',
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
    except:
        printer(i, 'Error!')
        print(file=sys.stderr)
        raise
    else:
        printer(total, 'Done!')
        print(file=sys.stderr)


def is_ported(pkgs, python_versions):
    num_all = len(pkgs)
    num2 = len([p for p in pkgs if python_versions[p] == {2}])
    num3 = len([p for p in pkgs if python_versions[p] == {3}])
    if num2 + num3 != num_all:
        return False
    return num3 >= num2


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
        for n, seeds in SEED_PACKAGES.items():
            provides = sorted(self.all_provides(seeds), key=str)
            for dep in progressbar(provides, 'Getting py{} requires'.format(n)):
                for pkg in self.whatrequires(dep):
                    python_versions[pkg].add(n)

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
            pkgs = by_srpm_name[name]
            r = json_output[name] = {}
            if is_ported(pkgs, python_versions):
                r['status'] = 'released'
            else:
                r['status'] = 'idle'
            r['rpms'] = sorted(format_rpm_name(p) for p in pkgs)
            r['deps'] = sorted(set(srpm_names[d]
                                   for p in pkgs
                                   for d in deps_of_pkg.get(p, '')
                                   if srpm_names[d] != name))

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
