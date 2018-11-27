from collections import OrderedDict, Counter, defaultdict
import random
import functools
import json
import math
import uuid
import io
import csv
import datetime

from flask import Flask, render_template, current_app, Markup, abort, url_for
from flask import make_response, request, Response
from flask.json import jsonify
from sqlalchemy import func, and_, create_engine
from sqlalchemy.orm import subqueryload, eagerload, sessionmaker, joinedload
from jinja2 import StrictUndefined
import markdown

from . import tables
from . import queries
from .history_graph import history_graph
from .load_data import get_data, DONE_STATUSES, PY2_STATUSES

tau = 2 * math.pi


def group_by_status(package_list):
    by_status = defaultdict(list)
    for package in package_list:
        by_status[package['status']].append(package)
    return by_status


def summarize_statuses(statuses, package_list):
    by_status = group_by_status(package_list)
    return [
        (status, len(by_status[name]))
        for name, status in statuses.items()
        if len(by_status[name])
    ]


def summarize_2_dual_3(package_list):
    """
    Given list of packages, return counts of (py3-only, dual-support, py2-only)
    """
    by_status = group_by_status(package_list)
    py3 = 0
    dual = 0
    py2 = 0
    for pkg in package_list:
        if pkg['status'] == 'py3-only':
            py3 += 1
        elif pkg['status'] in PY2_STATUSES:
            dual += 1
        else:
            py2 += 1
    return py3, dual, py2


def last_link_update_sort_key(package):
    return (
        -bool(package['last_link_update']),
        package['last_link_update'],
    )


def hello():
    db = current_app.config['DB']()
    data = current_app.config['data']

    statuses = data['statuses']
    packages = data['packages']

    by_status = group_by_status(packages.values())

    the_score = len(by_status['py3-only']) / len(packages)
    py2_score = sum(len(by_status[s]) for s in PY2_STATUSES) / len(packages)

    status_summary = summarize_statuses(statuses, packages.values())

    def sort_key(item):
        return item[0]['hidden'], item[0]['name']
    groups = sorted((
        (grp, summarize_2_dual_3(grp['packages'].values()))
        for grp in data['groups'].values()
    ), key=sort_key)

    naming_progress, _ = get_naming_policy_progress(db)

    return render_template(
        'index.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
        ),
        statuses=statuses,
        total_pkg_count=len(packages),
        status_summary=status_summary,
        ready_packages=by_status.get('idle', ()),
        blocked_packages=by_status.get('blocked', ()),
        py3_only_packages=by_status.get('py3-only', ()),
        legacy_leaf_packages=by_status.get('legacy-leaf', ()),
        released_packages=by_status.get('released', ()),
        dropped_packages=by_status.get('dropped', ()),
        mispackaged_packages=sorted(
            by_status.get('mispackaged', ()),
            key=last_link_update_sort_key),
        groups=groups,
        the_score=the_score,
        py2_score=py2_score,
        naming_progress=naming_progress,
    )


def jsonstats():
    data = current_app.config['data']

    statuses = data['statuses']
    packages = data['packages']
    grouped = group_by_status(packages.values())

    stats = {
        status: len(packages)
        for status, packages in grouped.items()
    }

    return jsonify(stats)


def get_status_counts(pkgs):
    counted = Counter(p.status_obj for p in pkgs)
    ordered = OrderedDict(sorted(counted.items(),
                                 key=lambda s_n: s_n[0].order))
    return ordered


def generate_deptree(
    package, *, keys=('deps', 'build_deps'),
    skip_statuses=frozenset({'idle'} | DONE_STATUSES),
    max_depth=3,
):
    seen = set()
    def generate_subtree(pkg, depth):
        run_names = set(pkg[keys[0]])
        build_names = set(pkg[keys[1]])
        packages = {
            pkg['name']: pkg
            for pkg in list(pkg[keys[0]].values()) + list(pkg[keys[1]].values())
        }
        children = sorted(
            packages.values(),
            key=status_sort_key,
        )
        for child in children:
            kinds = set()
            if child['name'] in run_names:
                kinds.add('run')
            if child['name'] in build_names:
                kinds.add('build')

            was_seen = (child['name'] in seen)
            seen.add(child['name'])

            if child['status'] in skip_statuses:
                tree = ()
            elif was_seen or depth >= max_depth:
                tree = ()
                kinds.add('elided')
            else:
                tree = list(generate_subtree(child, depth+1))

            yield child, kinds, tree

    return list(generate_subtree(package, 0))

def generate_deptrees(
    packages, skip_statuses=frozenset({'idle'} | DONE_STATUSES), **kwargs
):
    packages = sorted(packages, key=status_sort_key)
    for pkg in packages:
        if pkg['status'] in skip_statuses:
            tree = ()
        else:
            tree = generate_deptree(pkg, skip_statuses=skip_statuses, **kwargs)
        yield pkg, set(), tree


def package(pkg):
    data = current_app.config['data']
    statuses = data['statuses']

    try:
        package = data['packages'][pkg]
    except KeyError:
        abort(404)

    return render_template(
        'package.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('package', pkg=pkg), pkg),
        ),
        pkg=package,
        deptree=generate_deptree(package),
        dependencies_status_counts=summarize_statuses(statuses, package['deps'].values()),
        build_dependencies_status_counts=summarize_statuses(
            statuses, package['build_deps'].values()),
        dependent_tree=generate_deptree(
            package,
            keys=('dependents', 'build_dependents'),
            skip_statuses=frozenset(('py3-only', 'dropped')),
        ),
    )


def group(grp):
    data = current_app.config['data']

    try:
        group = data['groups'][grp]
    except KeyError:
        abort(404)

    statuses = data['statuses']
    status_summary = summarize_statuses(statuses, group['packages'].values())

    return render_template(
        'group.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('group', grp=grp), group['name']),
        ),
        grp=group,
        deptree=generate_deptrees(
            group['seed_packages'].values(),
            skip_statuses=set(),
        ),
        status_summary=status_summary,
    )


def markdown_filter(text):
    return Markup(markdown.markdown(text))


def status_sort_key(package):
    return -package['status_obj']['weight'], package['name']

def sort_by_status(packages):
    return sorted(packages, key=status_sort_key)


def format_rpm_name(text):
    name, version, release = text.rsplit('-', 2)
    return Markup('<span class="rpm-name">{}</span>-{}-{}'.format(
        name, version, release))


def format_time_ago(date):
    """Displays roughly how long ago the date was in a human readable format"""
    now = datetime.datetime.utcnow()
    diff = now - date

    # Years
    if diff.days >= 365:
        if diff.days >= 2 * 365:
            return "{} years ago".format(math.floor(diff.days / 365))
        else:
            return "a year ago"
    # Months
    elif diff.days >= 31:
        if diff.days >= 2 * 30:
            return "{} months ago".format(math.floor(diff.days / 30))
        else:
            return "a month ago"
    # Weeks
    elif diff.days >= 7:
        if diff.days >= 2 * 7:
            return "{} weeks ago".format(math.floor(diff.days / 7))
        else:
            return "a week ago"
    # Days
    elif diff.days >= 2:
        return "{} days ago".format(diff.days)
    elif diff.days == 1:
        return "yesterday"
    else:
        return "today"


def graph(grp=None, pkg=None):
    # Parameters
    all_deps = request.args.get('all_deps', None)
    if all_deps not in ('1', None):
        abort(400)  # Bad request

    return render_template(
        'graph.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('graph'), 'Graph'),
        ),
        grp=grp,
        pkg=pkg,
        all_deps=all_deps,
    )


def graph_grp(grp):
    return graph(grp=grp)


def graph_json(grp=None, pkg=None):
    # Parameters
    all_deps = request.args.get('all_deps', None)
    if all_deps not in ('1', None):
        abort(400)  # Bad request

    db = current_app.config['DB']()
    if pkg is None:
        db = current_app.config['DB']()
        query = queries.packages(db)
        query = query.filter(~tables.Package.status.in_(DONE_STATUSES))
        if grp:
            query = query.join(tables.GroupPackage)
            query = query.filter(tables.GroupPackage.group_ident == grp)
        query = query.options(joinedload(
            tables.Package.requirers if all_deps else tables.Package.run_time_requirers))
        packages = list(query)
    else:
        query = db.query(tables.Package)
        root_package = query.get(pkg)
        if root_package is None:
            abort(404)
        todo = {root_package}
        requirements = set()
        while todo:
            package = todo.pop()
            if package not in requirements:
                requirements.add(package)
                pkg_requirements = package.requirements if all_deps else package.run_time_requirements
                todo.update(p for p in pkg_requirements
                            if p.status not in DONE_STATUSES and
                            not p.nonblocking)
        todo = {root_package}
        requirers = set()
        while todo:
            package = todo.pop()
            if package not in requirers:
                requirers.add(package)
                pkg_requirers = package.requirers if all_deps else package.run_time_requirers
                todo.update(p for p in pkg_requirers
                            if p.status not in DONE_STATUSES and
                            not p.nonblocking)
        packages = list(requirements | requirers | {root_package})

    package_names = {p.name for p in packages}
    query = db.query(tables.Dependency)
    if not all_deps:
        query = query.filter(tables.Dependency.run_time)
    linked_pairs = {(d.requirer_name, d.requirement_name)
                    for d in query
                    if d.requirer_name in package_names
                        and d.requirement_name in package_names
                        and not d.requirement.nonblocking}
    linked_names = (set(p[0] for p in linked_pairs) |
                    set(p[1] for p in linked_pairs))
    if pkg:
        linked_names.add(pkg)

    nodes = [{'name': p.name,
              'status': p.status,
              'color': graph_color(p),
              'status_color': '#' + p.status_obj.color,
              'size': 3.5+math.log((p.loc_python or 1)+(p.loc_capi or 1), 50),
              'num_requirers': len(p.pending_requirers),
              'num_requirements': len(p.pending_requirements),
             }
             for p in packages
             if p.name in linked_names and p.name in package_names]
    names = [n['name'] for n in nodes]


    links = [{"source": names.index(d.requirer_name),
              "target": names.index(d.requirement_name),
             }
             for d in query
             if d.requirer_name in names and d.requirement_name in names
                 and not d.requirement.nonblocking]

    nodes_in_links = (set(l['source'] for l in links) |
                      set(l['target'] for l in links))

    nodes = [n for i, n in enumerate(nodes) if i in nodes_in_links]

    return jsonify(nodes=nodes, links=links)


def graph_json_grp(grp):
    return graph_json(grp=grp)


def graph_color(package):
    def component_color(c):
        c /= 255
        c = c / 2
        c = c ** 0.2
        c = c ** (1.1 ** len(package.pending_requirers))
        c *= 255
        return '{0:02x}'.format(int(c))

    sc = package.status_obj.color
    return '#' + ''.join(component_color(int(sc[x:x+2], 16))
                         for x in (0, 2, 4))


def _piechart(status_summary, bg=None):
    total_pkg_count = sum(c for s, c in status_summary)
    resp = make_response(render_template(
        'piechart.svg',
        status_summary=status_summary,
        total_pkg_count=total_pkg_count or 1,
        sin=math.sin, cos=math.cos, tau=tau,
        bg=bg,
    ))
    resp.headers['Content-type'] = 'image/svg+xml'
    return resp


def status_svg(status):
    data = current_app.config['data']
    try:
        status = data['statuses'][status]
    except KeyError:
        abort(404)

    return _piechart([], status)


def piechart_svg():
    data = current_app.config['data']
    statuses = data['statuses']
    packages = data['packages']

    status_summary = summarize_statuses(statuses, packages.values())

    return _piechart(status_summary)


def piechart_grp(grp):
    data = current_app.config['data']
    statuses = data['statuses']

    try:
        group = data['groups'][grp]
    except KeyError:
        abort(404)

    status_summary = summarize_statuses(statuses, group['packages'].values())

    return _piechart(status_summary)


def howto():
    data = current_app.config['data']
    statuses = data['statuses']
    packages = data['packages']

    by_status = group_by_status(packages.values())

    return render_template(
        'howto.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('howto'), 'So you want to contribute?'),
        ),
        idle_len=len(by_status['idle']),
        blocked_len=len(by_status['blocked']),
        mispackaged=len(by_status['mispackaged']),
        by_status=by_status,
        statuses=statuses,
        random_mispackaged=random.choice(by_status['mispackaged']),
        random_idle=random.choice(by_status['idle']),
    )


def history(expand=False):
    data = current_app.config['data']

    graph = history_graph(
        entries=data['history'],
        statuses=data['statuses'],
        title='portingdb history',
        expand=bool(expand),
    )

    return render_template(
        'history.html',
        graph=graph,
        expand=bool(expand),
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('history'), 'History'),
        ),
    )


def mispackaged():
    # List of mispackaged packages. This page is not very useful any more,
    # but left for URL stability.
    data = current_app.config['data']

    mispackaged = [p for p in data['packages'].values()
                   if p['status'] == 'mispackaged']
    mispackaged.sort(key=last_link_update_sort_key)

    return render_template(
        'mispackaged.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('mispackaged', requested=1), 'Mispackaged'),
        ),
        mispackaged=mispackaged,
    )


def namingpolicy():
    """Naming policy tracking.
    """
    db = current_app.config['DB']()
    misnamed_package_names = (
        db.query(tables.Package.name)
        .join(tables.CollectionPackage)
        .filter(tables.CollectionPackage.collection_ident == 'fedora',
                tables.CollectionPackage.is_misnamed.is_(True)))
    progress, data = get_naming_policy_progress(db)
    total = sum(dict(progress).values())

    # Unversioned requirers within non Python Packages.
    require_misnamed_all = (
        db.query(tables.Dependency.requirer_name)
        .filter(tables.Dependency.unversioned.is_(True))
        .outerjoin(tables.Dependency.requirer)
        .filter(tables.Package.name.is_(None)).distinct())
    blocked = (
        require_misnamed_all
        .filter(tables.Dependency.requirement_name.in_(misnamed_package_names)))
    require_misnamed = sorted(set(require_misnamed_all) - set(blocked))
    naming_data = dict(db.query(tables.NamingData.ident, tables.NamingData))
    data_outside_portingdb = (
        (naming_data['require-misnamed'], len(require_misnamed), require_misnamed),
        (naming_data['require-blocked'], blocked.count(), blocked))

    return render_template(
        'namingpolicy.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('namingpolicy'), 'Naming Policy'),
        ),
        total_packages=total,
        progress=progress,
        data=data,
        data_outside_portingdb=data_outside_portingdb,
    )


def get_naming_policy_progress(db):
    misnamed_package_names = (
        db.query(tables.Package.name)
        .join(tables.CollectionPackage)
        .filter(tables.CollectionPackage.collection_ident == 'fedora',
                tables.CollectionPackage.is_misnamed.is_(True)))

    all_packages = db.query(tables.Package).order_by(tables.Package.name)
    misnamed_packages = all_packages.filter(
        tables.Package.name.in_(misnamed_package_names))

    require_misnamed_all = (
        all_packages
        .filter(tables.Package.requirement_dependencies.any(
            tables.Dependency.unversioned.is_(True)),
            ~tables.Package.name.in_(misnamed_package_names)))

    requires_misnamed = tables.Package.requirement_dependencies.any(
        tables.Dependency.requirement_name.in_(misnamed_package_names))
    blocked = require_misnamed_all.filter(requires_misnamed)
    require_misnamed = require_misnamed_all.filter(~requires_misnamed)

    # Naming policy in numbers.
    total_packages = all_packages.count()
    total_misnamed = misnamed_package_names.count()
    total_blocked = blocked.count()
    total_require_misnamed = require_misnamed.count()

    # Misnamed packages progress bar info.
    naming_data = dict(db.query(tables.NamingData.ident, tables.NamingData))
    progress = (
        (naming_data['name-correct'], total_packages - (
            total_misnamed + total_blocked + total_require_misnamed)),
        (naming_data['name-misnamed'], total_misnamed),
        (naming_data['require-misnamed'], total_require_misnamed),
        (naming_data['require-blocked'], total_blocked))

    data = list(zip(progress[1:], (misnamed_packages, require_misnamed, blocked)))
    return progress, data


def piechart_namingpolicy():
    db = current_app.config['DB']()
    summary, _ = get_naming_policy_progress(db)
    return _piechart(summary)


def history_naming():
    data = current_app.config['data']

    graph = history_graph(
        entries=data['history-naming'],
        statuses=data['naming'],
        title='portingdb naming history',
        show_percent=False,
    )

    return render_template(
        'history-naming.html',
        graph=graph,
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('namingpolicy'), 'Naming Policy'),
            (url_for('history'), 'History'),
        )
    )


def maintainer(name):
    data = current_app.config['data']
    try:
        maintainer = data['maintainers'][name]
    except KeyError:
        abort(404)

    # dict of {comaintainer name: (comaintainer, dict of {pkg name: package})}
    comaintainers = {}
    for pkg in maintainer['packages'].values():
        for comaintainer in pkg['maintainers'].values():
            if comaintainer['name'] != name:
                _c, pkgs = comaintainers.setdefault(
                    comaintainer['name'], (comaintainer, {}))
                pkgs[pkg['name']] = pkg
    return render_template(
        'maintainer.html',
        maintainer=maintainer,
        comaintainers=comaintainers,
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('maintainer', name=name), name),
        )
    )


def format_quantity(num):
    for prefix in ' KMGT':
        if num > 1000:
            num /= 1000
        else:
            break
    if num > 100:
        num = round(num)
    elif num > 10:
        num = round(num, 1)
    else:
        num = round(num, 2)
    if abs(num - int(num)) < 0.01:
        num = int(num)
    return str(num) + prefix


def format_percent(num):
    num *= 100
    if num > 10:
        num = round(num)
    if num > 1:
        num = round(num, 1)
        if abs(num - int(num)) < 0.01:
            num = int(num)
    else:
        for digits in range(1, 3):
            rounded = round(num, digits)
            if rounded != 0:
                break
        num = rounded
    return str(num) + '%'


def first_decimal(number, digits=1):
    """Return the first `digits` digit after the decimal point"""
    number = abs(number)
    number = number - int(number)
    return int(round(number * 10**digits))


def create_app(db_url, directories, cache_config=None):
    app = Flask(__name__)
    app.config['DB'] = sessionmaker(bind=create_engine(db_url))
    db = app.config['DB']()
    app.config['data'] = data = get_data('data/')
    app.config['CONFIG'] = data['config']
    app.jinja_env.undefined = StrictUndefined
    app.jinja_env.filters['md'] = markdown_filter
    app.jinja_env.filters['format_rpm_name'] = format_rpm_name
    app.jinja_env.filters['format_quantity'] = format_quantity
    app.jinja_env.filters['format_percent'] = format_percent
    app.jinja_env.filters['format_time_ago'] = format_time_ago
    app.jinja_env.filters['sort_by_status'] = sort_by_status
    app.jinja_env.filters['first_decimal'] = first_decimal
    app.jinja_env.filters['summarize_statuses'] = (
        lambda p: summarize_statuses(data['statuses'], p))

    @app.context_processor
    def add_template_globals():
        return {
            'cache_tag': uuid.uuid4(),
            'len': len,
            'log': math.log,
            'config': app.config['CONFIG'],
        }

    def _add_route(url, func, **kwargs):
        app.route(url, **kwargs)(func)

    _add_route("/", hello)
    _add_route("/stats.json", jsonstats)
    _add_route("/pkg/<pkg>/", package)
    _add_route("/grp/<grp>/", group)
    _add_route("/graph/", graph)
    _add_route("/graph/portingdb.json", graph_json)
    _add_route("/piechart.svg", piechart_svg)
    _add_route("/status/<status>.svg", status_svg)
    _add_route("/grp/<grp>/piechart.svg", piechart_grp)
    _add_route("/grp/<grp>/graph/", graph_grp)
    _add_route("/grp/<grp>/graph/data.json", graph_json_grp)
    _add_route("/mispackaged/", mispackaged)
    _add_route("/namingpolicy/", namingpolicy)
    _add_route("/namingpolicy/piechart.svg", piechart_namingpolicy)
    _add_route("/namingpolicy/history/", history_naming)
    _add_route("/history/", history, defaults={'expand': False})
    _add_route("/history/expanded/", history, defaults={'expand': True})
    _add_route("/howto/", howto)
    _add_route("/maintainer/<name>/", maintainer)

    return app


def main(db_url, directories, cache_config=None, debug=False, port=5000):
    app = create_app(db_url, directories)
    app.run(debug=debug, port=port)
