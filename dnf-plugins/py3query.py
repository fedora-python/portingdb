"""
DNF plugin for getting the Python 3 porting status.

Put this in your DNF plugin directory, then run:

    $ dnf --enablerepo=rawhide --enablerepo=rawhide-source py3query -o fedora.json

This will give you a file "fedora.json" with information for portingdb.
"""

from __future__ import print_function

import sys
import json
import collections
import time

import hawkey
import dnf
import dnf.cli
import dnf.subject
from dnfpluginscore import _
import bugzilla  # python-bugzilla
import yaml


BUGZILLA_URL = 'bugzilla.redhat.com'
# Tracker bugs which are used to find all relevant package bugs
TRACKER_BUGS = {
    1698500: "F31_PY2REMOVAL",
    1625773: "PY2REMOVAL",
    1690439: "PY2FTBI",
    1285816: "PYTHON3",
    1700324: "F31FailsToInstall",
    1700317: "F31FTBFS",
    1750909: "F32FailsToInstall",
    1750908: "F32FTBFS",
    1708725: "PYTHON2_EOL",
    1803205: "BRPY27",
}
# Bugzilla statuses that indicate the bug was filed in error
NOTABUG_STATUSES = {'CLOSED NOTABUG', 'CLOSED WONTFIX', 'CLOSED CANTFIX'}

# Template URL to which you can add the bug ID and get a working URL
BUGZILLA_BUG_URL = "https://bugzilla.redhat.com/show_bug.cgi?id={}"

SEED_PACKAGES = {
    2: [
        'python2-devel', 'python2', 'python2-libs', 'python2-tkinter',
        'python(abi) = 2.7', '/usr/bin/python2', 'python27', 'python2.7',
        '/usr/bin/python2.7', 'libpython2.7.so.1.0', 'libpython2.7.so.1.0()(64bit)',
        'pygtk2', 'pygobject2',
    ],
    3: [
        'python3-devel', 'python3', 'python3-libs', 'python3-tkinter',
        'python-devel', 'python', 'python-libs',
        '/usr/bin/python', '/usr/bin/python3',
        'python(abi) = 3.4', '/usr/bin/python3.4', 'libpython3.4m.so.1.0',
        'libpython3.so', 'python3-cairo',
        'python(abi) = 3.5', '/usr/bin/python3.5', 'libpython3.5m.so.1.0',
        'libpython3.5m.so.1.0()(64bit)',
        'python(abi) = 3.6', '/usr/bin/python3.6', 'libpython3.6m.so.1.0',
        'libpython3.6m.so.1.0()(64bit)',
        'python(abi) = 3.7', '/usr/bin/python3.7', 'libpython3.7m.so.1.0',
        'libpython3.7m.so.1.0()(64bit)',
        'python(abi) = 3.8', '/usr/bin/python3.8', 'libpython3.8.so.1.0',
        'libpython3.8.so.1.0()(64bit)',
        'python(abi) = 3.9', '/usr/bin/python3.9', 'libpython3.9.so.1.0',
        'libpython3.9.so.1.0()(64bit)',
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


def progressbar(seq, text, namegetter=str):
    total = len(seq)
    prev_len = 0

    def printer(i, name):
        pad_len = prev_len - len(name)
        total_len = 20
        if total:
            progress = ('=' * (total_len * i // total)).ljust(total_len)
        else:
            progress = '=' * total_len
        if i == 0:
            r = ''
        else:
            r = '\r'
        line = '{}[{}] {}/{} {}: {}{} '.format(
            r, progress, i, total, text, name, ' ' * pad_len)
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


def have_binaries(packages):
    """Check if there are any binaries (executables) in the packages.
    Return: (bool) True if packages have any binaries, False otherwise
    """
    for pkg in packages:
        for filepath in pkg.files:
            if filepath.startswith(('/usr/bin', '/usr/sbin')):
                return True
    return False


def set_status(result, pkgs, python_versions):
    # Look at the Python dependencies of given packages, based on the
    # name only (this means different arches are grouped into one)
    name_by_version = collections.defaultdict(set)
    pkg_by_version = collections.defaultdict(set)
    name_by_arch = collections.defaultdict(set)
    for p in pkgs:
        name_by_arch[p.arch].add(f'{p.name}.{p.arch}')
        for v in python_versions[p]:
            name_by_version[v].add(f'{p.name}.{p.arch}')
            pkg_by_version[v].add(p)

    if (name_by_version[2] & name_by_version[3]) - name_by_arch['src']:
        # If a package depends on *both* py2 and py3, it's not ported
        result['status'] = 'mispackaged'
        result['note'] = (
            'A single package depends on both Python 2 and '
            'Python 3.\n'
            'It should be split into a python2 and python3 subpackages '
            'to prevent it from always dragging the py2 dependency in.')
    elif not name_by_version[2]:
        # Hooray!
        result['status'] = 'py3-only'
    else:
        # Otherwise, a srpm isn't ported if it has more packages that need py2
        # than those that need py3
        if len(name_by_version[3]) >= len(name_by_version[2]):
            if have_binaries(pkg_by_version[2]) and not have_binaries(pkg_by_version[3]):
                # Identify packages with py2 only binaries.
                result['status'] = 'mispackaged'
                result['nonblocking'] = True
                result['note'] = (
                    'The Python 3 package is missing binaries available '
                    'in a Python 2 package.\n')
            elif all(
                result['rpms'][format_rpm_name(pkg)]['almost_leaf']
                or result['rpms'][format_rpm_name(pkg)]['arch'] == 'src'
                for pkg in pkg_by_version[2]
            ) and any(
                result['rpms'][format_rpm_name(pkg)]['arch'] != 'src'
                for pkg in pkg_by_version[2]
            ):
                # Packages with py2 subpackages not required by anything.
                # (source packages don't count)
                result['status'] = 'legacy-leaf'
            else:
                result['status'] = 'released'
        else:
            result['status'] = 'idle'


def format_rpm_name(pkg):
    if pkg.epoch:
        epoch = '{}:'.format(pkg.epoch)
    else:
        epoch = ''
    return '{pkg.name}-{epoch}{pkg.version}-{pkg.release}.{pkg.arch}'.format(
        pkg=pkg, epoch=epoch)


def get_srpm_name(pkg):
    return hawkey.split_nevra(pkg.sourcerpm).name if pkg.sourcerpm else pkg.name


def get_srpm_names(pkgs):
    return {get_srpm_name(pkg) for pkg in pkgs}


class Py3QueryCommand(dnf.cli.Command):

    """The util command there is extending the dnf command line."""
    aliases = ('py3query',)
    summary = _('query the python3 porting status')
    usage = _('[OPTIONS] [KEYWORDS]')

    def configure(self):
        """Setup the demands."""
        demands = self.cli.demands
        demands.sack_activation = True
        demands.available_repos = True

    @staticmethod
    def set_argparser(parser):
        """Parse command line arguments."""
        parser.add_argument('--output', '-o', metavar='FILE', action='store',
                            help=_('write output to the given file'))

        parser.add_argument('--no-bz', dest='fetch_bugzilla', action='store_false',
                            default=True, help=_("Don't get Bugzilla links"))

        parser.add_argument('--misnamed', dest='py3query_misnamed', action='store',
                            help=_("YAML file with old misnamed packages"))

        parser.add_argument('--repo-groups', dest='repo_groups_file',
                            default=None, metavar='FILE', action='store',
                            help=_("Optional filename of a 'groups.json' file "
                                   "that will record which package comes from "
                                   "which repositories"))

    def run(self):
        self.base_query = self.base.sack.query()
        self.pkg_query = self.base_query.filter(arch__neq=['src'])
        self.src_query = self.base_query.filter(arch=['src'])

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
                for pkg in self.whatrequires(dep, self.base_query):
                    python_versions[pkg].add(n)
                    rpm_pydeps[pkg].add(str(dep))

        # Add packages with 'python?' as a component of their name, if they
        # haven't been added as dependencies
        for name, version in {
            'python': 0,
            'python2': 2,
            'python3': 3,
        }.items():
            for pattern in '{}-*', '*-{}', '*-{}-*':
                name_glob = pattern.format(name)
                query = self.base_query.filter(name__glob=name_glob)
                message = 'Getting {} packages'.format(name_glob)
                for pkg in progressbar(query, message):
                    if pkg.sourcerpm and pkg.sourcerpm.startswith('mingw-'):
                        # Ignore mingw packages
                        continue
                    if pkg not in python_versions:
                        python_versions[pkg].add(version)

        # add python2.7 package manually, it doesn't require Python 2, but it is
        for py2name in 'python27', 'python2.7':
            query = self.pkg_query.filter(name=py2name)
            for pkg in query:
                python_versions[pkg].add(2)

        # srpm_names: {package: srpm name}
        # by_srpm_name: {srpm name: set of packages}
        srpm_names = {}
        by_srpm_name = collections.defaultdict(set)
        # repo_srpms: {repo name: set of srpm names}
        repo_srpms = {}
        for pkg in progressbar(python_versions.keys(), 'Getting SRPMs'):
            srpm_name = get_srpm_name(pkg)
            srpm_names[pkg] = srpm_name
            by_srpm_name[srpm_name].add(pkg)
            repo_srpms.setdefault(pkg.reponame, set()).add(srpm_name)

        old_misnamed = {}
        old_misnamed_flat = {}
        if self.opts.py3query_misnamed:
            with open(self.opts.py3query_misnamed) as f:
                old_misnamed = yaml.safe_load(f)
            old_misnamed_flat = {pk: pr for pkg in old_misnamed
                                        for pr, pk in old_misnamed[pkg].items()}

        # deps_of_pkg: {package: set of packages}
        deps_of_pkg = collections.defaultdict(set)
        # build_deps_of_srpm: {srpm: set of packages}
        build_deps_of_srpm = collections.defaultdict(set)
        # requirers_of_pkg: {package: set of srpm}
        requirers_of_pkg = collections.defaultdict(set)
        # build_requirers_of_pkg: {pkg: set of srpm}
        build_requirers_of_pkg = collections.defaultdict(set)
        # all_provides: {provide_name: package}
        all_provides = {str(r).split()[0]: p for p in python_versions for r in p.provides
                        if not str(r).startswith(PROVIDES_BLACKLIST)}
        for pkg in progressbar(sorted(python_versions.keys()), 'Getting requirements'):
            if python_versions[pkg] == {3}:
                continue
            if pkg.arch == 'src':
                continue
            reqs = set()
            build_reqs = set()
            provides = set(pkg.provides)
            for provide in pkg.provides:
                str_provide = str(provide).split(' ')[0]
                if str_provide in old_misnamed_flat:
                    provides.add(old_misnamed_flat[str_provide])

            for provide in provides:
                reqs.update(self.whatrequires(provide, self.pkg_query))
                build_reqs.update(self.whatrequires(provide, self.src_query))

            for req in reqs:
                if req in python_versions.keys():
                    deps_of_pkg[req].add(pkg)
                # Both Python and non-Python packages here.
                requirers_of_pkg[pkg].add(req)

            for req in build_reqs:
                if req.name in by_srpm_name.keys():
                    build_deps_of_srpm[req.name].add(pkg)
                # Both Python and non-Python packages here.
                build_requirers_of_pkg[pkg].add(req)

        # unversioned_requirers: {srpm_name: set of srpm_names}
        unversioned_requirers = collections.defaultdict(set)
        for pkg in progressbar(set.union(*requirers_of_pkg.values(), *build_requirers_of_pkg.values()),
                               'Processing packages with ambiguous dependencies'):
            # Ignore packages that are:
            if (python_versions.get(pkg) == {3} or  # Python 3 only
                    pkg.name.endswith('-doc')):  # Documentation
                continue
            for require in (pkg.requires + pkg.requires_pre + pkg.recommends +
                            pkg.suggests + pkg.supplements + pkg.enhances):
                require = str(require).split()[0]

                real_require = require
                try:
                    require = old_misnamed[pkg.name][real_require]
                except KeyError:
                    pass

                requirement = all_provides.get(require)

        # json_output: {srpm name: info}
        json_output = dict()
        for name in progressbar(by_srpm_name, 'Generating output'):
            pkgs = sorted(by_srpm_name[name])
            r = json_output[name] = {}
            r['rpms'] = {
                format_rpm_name(p): {
                    'py_deps': {str(d): dep_versions[d] for d in rpm_pydeps[p]},
                    'non_python_requirers': {
                        'build_time': sorted(get_srpm_names(build_requirers_of_pkg[p]) - by_srpm_name.keys()),
                        'run_time': sorted(get_srpm_names(requirers_of_pkg[p]) - by_srpm_name.keys()),
                    },
                    'almost_leaf': (
                        # not SRPM and is Python 2 and is not required by anything EXCEPT
                        # sibling subpackages
                        p.arch != 'src' and
                        2 in python_versions[p] and
                        not get_srpm_names(build_requirers_of_pkg[p] | requirers_of_pkg[p]) - {name}
                    ),
                    'legacy_leaf': (
                        # not SRPM and is Python 2 and is not required by anything
                        p.arch != 'src' and
                        2 in python_versions[p] and
                        not get_srpm_names(build_requirers_of_pkg[p] | requirers_of_pkg[p])
                    ),
                    'arch': p.arch,
                } for p in pkgs}
            set_status(r, pkgs, python_versions)

            r['deps'] = sorted(set(srpm_names[d]
                                   for p in pkgs
                                   for d in deps_of_pkg.get(p, '')
                                   if srpm_names[d] != name))
            r['build_deps'] = sorted(set(srpm_names[d]
                                         for d in build_deps_of_srpm.get(name, '')
                                         if srpm_names[d] != name))
            if unversioned_requirers.get(name):
                r['unversioned_requirers'] = sorted(unversioned_requirers[name])

        # add Bugzilla links
        if self.opts.fetch_bugzilla:
            bar = iter(progressbar(['connecting', 'tracker', 'individual'],
                                   'Getting bugs'))

            next(bar)
            bz = bugzilla.RHBugzilla(BUGZILLA_URL)

            next(bar)
            include_fields = ['id', 'depends_on', 'blocks', 'component',
                              'status', 'resolution', 'last_change_time',
                              'short_desc']
            trackers = bz.getbugs(TRACKER_BUGS,
                                  include_fields=include_fields)
            all_ids = set(b for t in trackers for b in t.depends_on)

            next(bar)
            bugs = bz.getbugs(all_ids, include_fields=include_fields)
            bar.close()

            def bug_namegetter(bug):
                return '{bug.id} {bug.status} {bug.component}'.format(bug=bug)

            rank = ['NEW', 'ASSIGNED', 'POST', 'MODIFIED', 'ON_QA', 'VERIFIED',
                    'RELEASE_PENDING', 'CLOSED']

            def key(bug):
                return rank.index(bug.status), bug.last_change_time

            bugs = sorted(bugs, key=key)

            for bug in progressbar(bugs, 'Merging bugs',
                                   namegetter=bug_namegetter):
                r = json_output.get(bug.component, {})
                bugs = r.setdefault('bugs', {})
                entry = bugs.get(bug.id)
                if not entry:
                    entry = {
                        'url': bug.weburl,
                        'short_desc': bug.short_desc,
                        'status': bug.status,
                        'resolution': bug.resolution,
                        'last_change': time.strftime(
                            '%Y-%m-%d %H:%M:%S',
                            bug.last_change_time.timetuple()),
                        'trackers': [],
                    }
                    for tb in bug.blocks:
                        alias = TRACKER_BUGS.get(tb)
                        if alias:
                            entry['trackers'].append(alias)
                    bugs[bug.id] = entry

        # Print out output

        if self.opts.output:
            with open(self.opts.output, 'w') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)
        else:
            json.dump(json_output, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.flush()

        # Write out a groups.json
        if self.opts.repo_groups_file:
            output = {repo_name: {'name': repo_name,
                                  'packages': sorted(srpm_names)}
                      for repo_name, srpm_names in repo_srpms.items()}
            with open(self.opts.repo_groups_file, 'w') as f:
                json.dump(output, f, indent=2, sort_keys=True)


    def all_provides(self, seeds):
        pkgs = set()
        for seed in seeds:
            query = dnf.subject.Subject(seed, ignore_case=True).get_best_query(
                self.base.sack, with_provides=False)
            pkgs.update(query.run())
        provides = set()
        for pkg in sorted(pkgs):
            provides.update(pkg.provides)
        return provides

    def whatrequires(self, dep, query):
        query = query.filter(requires=dep)
        return set(query)
