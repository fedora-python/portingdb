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

from taskotron_python_versions.executables import have_binaries
from taskotron_python_versions.naming_scheme import check_naming_policy, is_unversioned


BUGZILLA_URL = 'bugzilla.redhat.com'
# Tracker bugs which are used to find all relevant package bugs
TRACKER_BUG_IDS = [
    1285816,  # The Python 3 tracking bug
    1322027,  # The Python 3 Upstream Porting tracking bug
    1432186,  # Missing PY3-EXECUTABLES
]
# Trackers of bugs whose presence indicates the given package is mispackaged.
MISPACKAGED_TRACKER_BUG_IDS = [
    1285816,  # The Python 3 tracking bug
]
# Trackers of bugs whose presence gives us additional information about the
# status quo.
ADDITIONAL_TRACKER_BUGS = [
    1333765,  # PY3PATCH-REQUESTED
    1312032,  # PY3PATCH-AVAILABLE
    1333770,  # PY3PATCH-PUSH
    1432186,  # Missing PY3-EXECUTABLES
]

# Template URL to which you can add the bug ID and get a working URL
BUGZILLA_BUG_URL = "https://bugzilla.redhat.com/show_bug.cgi?id={}"

SEED_PACKAGES = {
    2: [
        'python-devel', 'python2-devel', 'python', 'python2', 'python-libs',
        'python(abi) = 2.7', '/usr/bin/python', '/usr/bin/python2',
        '/usr/bin/python2.7', 'libpython2.7.so.1.0', 'libpython2.7.so.1.0()(64bit)',
        'pygtk2', 'pygobject2', 'pycairo',
    ],
    3: [
        'python3-devel', 'python3', 'python3-libs', 'python(abi) = 3.4',
        'system-python', '/usr/libexec/system-python',
        '/usr/bin/python3', '/usr/bin/python3.4', 'libpython3.4m.so.1.0',
        'libpython3.so', 'python3-cairo',
        'python(abi) = 3.5', '/usr/bin/python3.5', 'libpython3.5m.so.1.0',
        "libpython3.5m.so.1.0()(64bit)",
        'python(abi) = 3.6', '/usr/bin/python3.6', 'libpython3.6m.so.1.0',
        "libpython3.6m.so.1.0()(64bit)",
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
        progress = ('=' * (total_len * i // total)).ljust(total_len)
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


def set_status(result, pkgs, python_versions):
    # Look at the Python dependencies of given packages, based on the
    # name only (this means different arches are grouped into one)
    name_by_version = collections.defaultdict(set)
    pkg_by_version = collections.defaultdict(set)
    for p in pkgs:
        for v in python_versions[p]:
            name_by_version[v].add(p.name)
            pkg_by_version[v].add(p)

    if name_by_version[2] & name_by_version[3]:
        # If a package depends on *both* py2 and py3, it's not ported
        result['status'] = 'mispackaged'
        result['note'] = (
            'A single package depends on both Python 2 and '
            'Python 3.\n'
            'It should be split into a python2 and python3 subpackages '
            'to prevent it from always dragging the py2 dependency in.')
    elif not name_by_version[2]:
        # Hooray!
        result['status'] = 'released'
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
            elif all(result['rpms'][format_rpm_name(pkg)]['legacy_leaf']
                     for pkg in pkg_by_version[2]):
                # Packages with py2 subpackages not required by anything.
                result['status'] = 'legacy-leaf'
            else:
                result['status'] = 'released'
        else:
            result['status'] = 'idle'

    for pkg in pkg_by_version[2]:
        is_misnamed = check_naming_policy(pkg, name_by_version)
        if is_misnamed and pkg.name != 'python-unversioned-command':
            rpm_name = format_rpm_name(pkg)
            result['rpms'].get(rpm_name, {})['is_misnamed'] = is_misnamed


def format_rpm_name(pkg):
    if pkg.epoch:
        epoch = '{}:'.format(pkg.epoch)
    else:
        epoch = ''
    return '{pkg.name}-{epoch}{pkg.version}-{pkg.release}'.format(
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

        parser.add_argument('--qrepo', dest='py3query_repo', action='append',
                            help=_("Repo(s) to use for the query"))

        parser.add_argument('--repo-groups', dest='repo_groups_file',
                            default=None, metavar='FILE', action='store',
                            help=_("Optional filename of a 'groups.json' file "
                                   "that will record which package comes from "
                                   "which repositories"))

    def run(self):
        reponames = self.opts.py3query_repo
        if not reponames:
            reponames = ['rawhide']
        self.base_query = self.base.sack.query()
        self.pkg_query = self.base_query.filter(reponame=list(reponames))
        source_reponames = [n + '-source' for n in reponames]
        self.src_query = self.base_query.filter(reponame=source_reponames).filter(arch=['src'])

        # python_versions: {package: set of Python versions}
        python_versions = collections.defaultdict(set)
        # rpm_pydeps: {package: set of dep names}
        rpm_pydeps = collections.defaultdict(set)
        # dep_versions: {dep name: Python version}
        dep_versions = collections.defaultdict(set)
        for n, seeds in SEED_PACKAGES.items():
            provides = sorted(self.all_provides(reponames, seeds), key=str)

            # This effectively includes packages that still need
            # Python 3.4 while Rawhide only provides Python 3.5
            provides += sorted(seeds)

            for dep in progressbar(provides, 'Getting py{} requires'.format(n)):
                dep_versions[str(dep)] = n
                for pkg in self.whatrequires(dep, self.pkg_query):
                    python_versions[pkg].add(n)
                    rpm_pydeps[pkg].add(str(dep))

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
            reqs = set()
            build_reqs = set()
            for provide in pkg.provides:
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
                requirement = all_provides.get(require)
                if (is_unversioned(require) and requirement and not
                        (require.endswith('-doc') or python_versions.get(requirement) == {3})):
                    requirement_srpm_name = get_srpm_name(requirement)
                    requirer_srpm_name = get_srpm_name(pkg)
                    unversioned_requirers[requirement_srpm_name].add(requirer_srpm_name)

        # deps_of_pkg: {srpm name: info}
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
                    'legacy_leaf': (
                        # is Python 2 and is not required by anything
                        2 in python_versions[p] and
                        not get_srpm_names(build_requirers_of_pkg[p] | requirers_of_pkg[p]) - {name}
                    ),
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
                              'status', 'resolution', 'last_change_time']
            trackers = bz.getbugs(TRACKER_BUG_IDS,
                                  include_fields=include_fields)
            all_ids = [b for t in trackers for b in t.depends_on]

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
                if 'bug' in r.get('links', {}):
                    continue
                status = bug.status
                if bug.resolution:
                    status += ' ' + bug.resolution
                # Let's get the datetime of the last comment and convert to string
                last_change_datetime = time.strftime(
                    '%Y-%m-%d %H:%M:%S',
                    bug.last_change_time.timetuple())
                r.setdefault('links', {})['bug'] = [
                    bug.weburl, status, last_change_datetime]

                for tb in bug.blocks:
                    if tb in ADDITIONAL_TRACKER_BUGS:
                        r.setdefault('tracking_bugs', []).append(
                            BUGZILLA_BUG_URL.format(tb))

                if (any(tb in bug.blocks for tb in MISPACKAGED_TRACKER_BUG_IDS) and
                        r.get('status') == 'idle'):
                    r['status'] = "mispackaged"
                    r['note'] = ('There is a problem in Fedora packaging, '
                                 'not necessarily with the software itself. '
                                 'See the linked Fedora bug.')

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


    def all_provides(self, reponames, seeds):
        pkgs = set()
        for seed in seeds:
            query = dnf.subject.Subject(seed, ignore_case=True).get_best_query(
                self.base.sack, with_provides=False)
            query = query.filter(reponame=list(reponames))
            pkgs.update(query.run())
        provides = set()
        for pkg in sorted(pkgs):
            provides.update(pkg.provides)
        return provides

    def whatrequires(self, dep, query):
        query = query.filter(requires=dep)
        return set(query)
