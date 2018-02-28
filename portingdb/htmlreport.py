from collections import OrderedDict, Counter, namedtuple
import random
import functools
import json
import math
import uuid
import io
import csv
import datetime

from flask import Flask, render_template, current_app, Markup, abort, url_for
from flask import make_response, request
from flask.json import jsonify
from sqlalchemy import func, and_, or_, create_engine
from sqlalchemy.orm import subqueryload, eagerload, sessionmaker, joinedload
from jinja2 import StrictUndefined
import markdown
from dogpile.cache import make_region
from dogpile.cache.api import NO_VALUE

from . import tables
from . import queries

tau = 2 * math.pi

DONE_STATUSES = {'released', 'dropped', 'py3-only'}


def hello():
    db = current_app.config['DB']()

    query = queries.collections(db)
    query = query.options(subqueryload('collection_statuses'))
    collections = list(query)

    coll_info = {}
    for i, collection in enumerate(collections):
        query = db.query(tables.CollectionPackage.status,
                         func.count(tables.CollectionPackage.id))
        query = query.filter(tables.CollectionPackage.collection == collection)
        query = query.join(tables.CollectionPackage.status_obj)
        query = query.group_by(tables.CollectionPackage.status)
        query = query.order_by(tables.Status.order)
        data = OrderedDict(query)
        total = sum(v for k, v in data.items())
        coll_info[collection] = {
            'total': total,
            'data': data,
        }

    # Main package query

    query = queries.packages(db)
    total_pkg_count = query.count()

    py3_only = query.filter(tables.Package.status == 'py3-only')
    py3_only = queries.order_by_name(db, py3_only)

    released = query.filter(tables.Package.status == 'released')
    released = queries.order_by_name(db, released)

    dropped = query.filter(tables.Package.status == 'dropped')
    dropped = queries.order_by_name(db, dropped)

    mispackaged = query.filter(tables.Package.status == 'mispackaged')
    mispackaged = mispackaged.options(subqueryload('collection_packages'))
    mispackaged = mispackaged.options(subqueryload('collection_packages.tracking_bugs'))
    mispackaged = mispackaged.join(tables.CollectionPackage)
    mispackaged = mispackaged.outerjoin(
        tables.Link,
        and_(tables.Link.type == 'bug',
             tables.Link.collection_package_id == tables.CollectionPackage.id))
    mispackaged = mispackaged.order_by(func.ifnull(tables.Link.last_update, '9999'))
    mispackaged = queries.order_by_name(db, mispackaged)

    blocked = query.filter(tables.Package.status == 'blocked')
    blocked = blocked.options(subqueryload('requirements'))
    blocked = queries.order_by_name(db, blocked)

    ready = query.filter(tables.Package.status == 'idle')
    ready = ready.options(subqueryload('requirers'))
    ready = queries.order_by_name(db, ready)

    # Naming policy tracking.
    naming_progress, _ = get_naming_policy_progress(db)

    py3_only = list(py3_only)
    released = list(released)
    ready = list(ready)
    blocked = list(blocked)
    mispackaged = list(mispackaged)
    dropped = list(dropped)
    random_mispackaged = random.choice(mispackaged)

    # Check we account for all the packages
    sum_by_status = sum(len(x) for x in (released, py3_only, ready,
                                         blocked, mispackaged, dropped))
    assert sum_by_status == total_pkg_count

    the_score = (len(py3_only) + len(released) + len(dropped)) / total_pkg_count

    # Nonbolocking set query
    query = db.query(tables.Package)
    query = query.outerjoin(tables.Package.collection_packages)
    query = query.filter(tables.CollectionPackage.nonblocking)
    nonblocking = set(query)

    # Group query

    query = db.query(tables.Group)
    query = query.join(tables.Group.packages)
    query = query.join(tables.Package.status_obj)
    query = query.group_by(tables.Group.ident)
    query = query.group_by(tables.Package.status)
    query = query.order_by(tables.Status.order)
    query = query.order_by(tables.Group.name)
    query = query.add_columns(tables.Package.status,
                              func.count(tables.Package.name))
    groups = get_groups(db, query.filter(~tables.Group.hidden))
    hidden_groups = get_groups(db, query.filter(tables.Group.hidden))

    # Statuses with no. of packages
    statuses = OrderedDict(
        db.query(tables.Status, func.count(tables.Package.name))
        .outerjoin(tables.Status.packages)
        .group_by(tables.Status.ident)
        .order_by(tables.Status.order))

    return render_template(
        'index.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
        ),
        collections=collections,
        coll_info=coll_info,
        statuses=statuses,
        priorities=list(db.query(tables.Priority).order_by(tables.Priority.order)),
        total_pkg_count=total_pkg_count,
        status_summary=get_status_summary(db),
        ready_packages=ready,
        blocked_packages=blocked,
        py3_only_packages=py3_only,
        released_packages=released,
        dropped_packages=dropped,
        mispackaged_packages=mispackaged,
        random_mispackaged=random_mispackaged,
        groups=groups,
        hidden_groups=hidden_groups,
        nonblocking=nonblocking,
        the_score=the_score,
        naming_progress=naming_progress,
    )


def get_groups(db, query):
    groups = OrderedDict()
    for group, status_ident, count in query:
        status = db.query(tables.Status).get(status_ident)
        pd = groups.setdefault(group, OrderedDict())
        pd[status] = pd.get(status, 0) + count
    return groups


def jsonstats():
    db = current_app.config['DB']()

    query = queries.packages(db)
    released = query.filter(tables.Package.status == 'released')
    py3_only = query.filter(tables.Package.status == 'py3-only')
    dropped = query.filter(tables.Package.status == 'dropped')
    mispackaged = query.filter(tables.Package.status == 'mispackaged')
    blocked = query.filter(tables.Package.status == 'blocked')
    ready = query.filter(tables.Package.status == 'idle')

    stats = {
        'released': released.count(),
        'py3-only': py3_only.count(),
        'dropped': dropped.count(),
        'mispackaged': mispackaged.count(),
        'blocked': blocked.count(),
        'idle': ready.count(),
    }

    return jsonify(**stats)


def get_status_summary(db, filter=None):
    query = db.query(tables.Status)
    query = query.join(tables.Package, tables.Status.packages)
    query = query.add_column(func.count(tables.Package.name))
    if filter:
        query = filter(query)
    query = query.group_by(tables.Package.status)
    query = query.order_by(tables.Status.order)
    return list(query)


def get_status_counts(pkgs):
    counted = Counter(p.status_obj for p in pkgs)
    ordered = OrderedDict(sorted(counted.items(),
                                 key=lambda s_n: s_n[0].order))
    return ordered


def package(pkg):
    db = current_app.config['DB']()
    collections = list(queries.collections(db))

    query = db.query(tables.Package)
    query = query.options(eagerload('status_obj'))
    query = query.options(subqueryload('collection_packages'))
    query = query.options(subqueryload('collection_packages.links'))
    query = query.options(eagerload('collection_packages.status_obj'))
    query = query.options(subqueryload('collection_packages.rpms'))
    query = query.options(eagerload('collection_packages.rpms.py_dependencies'))
    package = query.get(pkg)
    if package is None:
        abort(404)

    query = queries.dependencies(db, package)
    query = query.options(eagerload('status_obj'))
    query = query.options(subqueryload('collection_packages'))
    query = query.options(subqueryload('collection_packages.links'))
    query = query.options(eagerload('collection_packages.status_obj'))
    dependencies = list(query)

    dependents = list(queries.dependents(db, package))

    return render_template(
        'package.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('package', pkg=pkg), pkg),
        ),
        collections=collections,
        pkg=package,
        dependencies=dependencies,
        dependents=dependents,
        deptree=[(package, gen_deptree(dependencies))],
        len_dependencies=len(dependencies),
        dependencies_status_counts=get_status_counts(dependencies),
    )

def group(grp):
    db = current_app.config['DB']()
    collections = list(queries.collections(db))

    group = db.query(tables.Group).get(grp)
    if group is None:
        abort(404)

    query = db.query(tables.Package)
    query = query.join(tables.Package.group_packages)
    query = query.join(tables.GroupPackage.group)
    query = query.join(tables.Package.status_obj)
    query = query.filter(tables.Group.ident == grp)
    query = query.order_by(-tables.Status.weight)
    query = queries.order_by_name(db, query)
    query = query.options(subqueryload('collection_packages'))
    query = query.options(subqueryload('collection_packages.links'))
    packages = list(query)

    query = query.filter(tables.GroupPackage.is_seed)
    seed_groups = query

    return render_template(
        'group.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('group', grp=grp), group.name),
        ),
        collections=collections,
        grp=group,
        packages=packages,
        len_packages=len(packages),
        deptree=list(gen_deptree(seed_groups)),
        status_counts=get_status_counts(packages),
    )


def gen_deptree(base, *, seen=None):
    seen = seen or set()
    base = tuple(base)
    for pkg in base:
        if pkg in seen or pkg.status in {'idle'} | DONE_STATUSES:
            yield pkg, []
        else:
            reqs = sorted(pkg.requirements,
                          key=lambda p: (-p.status_obj.weight, p.name))
            yield pkg, gen_deptree(reqs, seen=seen|{pkg})
        seen.add(pkg)


def markdown_filter(text):
    return Markup(markdown.markdown(text))

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
    return render_template(
        'graph.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('graph'), 'Graph'),
        ),
        grp=grp,
        pkg=pkg,
    )


def graph_grp(grp):
    return graph(grp=grp)


def graph_pkg(pkg):
    return graph(pkg=pkg)


def graph_json(grp=None, pkg=None):
    db = current_app.config['DB']()
    if pkg is None:
        db = current_app.config['DB']()
        query = queries.packages(db)
        query = query.filter(~tables.Package.status.in_(DONE_STATUSES))
        if grp:
            query = query.join(tables.GroupPackage)
            query = query.filter(tables.GroupPackage.group_ident == grp)
        query = query.options(joinedload(tables.Package.requirers))
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
                todo.update(p for p in package.requirements
                            if p.status not in DONE_STATUSES)
        todo = {root_package}
        requirers = set()
        while todo:
            package = todo.pop()
            if package not in requirers:
                requirers.add(package)
                todo.update(p for p in package.requirers
                            if p.status not in DONE_STATUSES)
        packages = list(requirements | requirers | {root_package})

    package_names = {p.name for p in packages}
    query = db.query(tables.Dependency)
    linked_pairs = {(d.requirer_name, d.requirement_name)
                    for d in query
                    if d.requirer_name in package_names
                        and d.requirement_name in package_names
                        and not d.requirement.nonblocking}
    linked_names = (set(p[0] for p in linked_pairs) |
                    set(p[1] for p in linked_pairs))
    if pkg:
        linked_names.add(pkg)
    not_included = [p for p in packages if p.name not in linked_names]

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


def graph_json_pkg(pkg):
    return graph_json(pkg=pkg)


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


def piechart_svg():
    db = current_app.config['DB']()

    return _piechart(get_status_summary(db))


def piechart_grp(grp):
    db = current_app.config['DB']()

    group = db.query(tables.Group).get(grp)
    if group is None:
        abort(404)

    def filter(query):
        query = query.join(tables.Package.group_packages)
        query = query.join(tables.GroupPackage.group)
        query = query.filter(tables.Group.ident == grp)
        return query

    return _piechart(get_status_summary(db, filter=filter))


def piechart_pkg(pkg):
    db = current_app.config['DB']()

    package = db.query(tables.Package).get(pkg)
    if package is None:
        abort(404)

    return _piechart([], package.status_obj)


def howto():
    db = current_app.config['DB']()
    query = queries.packages(db)

    # Count the blocked packages
    blocked_query = query.filter(tables.Package.status == 'blocked')
    blocked_len = blocked_query.count()

    # Get all the idle packages
    idle_query = query.filter(tables.Package.status == 'idle')
    idle = list(idle_query)
    idle_len = len(idle)

    # Get all the mispackaged packages
    mispackaged_query = query.filter(tables.Package.status == 'mispackaged')
    mispackaged = list(mispackaged_query)

    # Pick an idle package at random
    random_idle = random.choice(idle)

    # Pick a mispackaged package at random
    random_mispackaged = random.choice(mispackaged)

    # Status objects
    query = db.query(tables.Status)
    mispackaged_status = query.get('mispackaged')
    released_status = query.get('released')

    return render_template(
        'howto.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('howto'), 'So you want to contribute?'),
        ),
        idle_len=idle_len,
        blocked_len=blocked_len,
        mispackaged=mispackaged,
        random_idle=random_idle,
        random_mispackaged=random_mispackaged,
        mispackaged_status=mispackaged_status,
        released_status=released_status,
    )


def history():
    expand = request.args.get('expand', None)
    if expand not in ('1', None):
        abort(400)  # Bad request
    return render_template(
        'history.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('history'), 'History'),
        ),
        expand=bool(expand),
    )


def history_csv():
    db = current_app.config['DB']()

    query = db.query(tables.HistoryEntry)
    query = query.order_by(tables.HistoryEntry.date)
    sio = io.StringIO()
    w = csv.DictWriter(sio, ['commit', 'date', 'status', 'num_packages'])
    w.writeheader()
    for row in query:
        w.writerow({
            'commit': row.commit,
            'date': row.date,
            'status': row.status,
            'num_packages': row.num_packages,
        })
    return sio.getvalue()


def group_by_loc(grp):
    db = current_app.config['DB']()

    group = db.query(tables.Group).get(grp)
    if group is None:
        abort(404)

    query = queries.packages(db)
    query = query.join(tables.Package.group_packages)
    query = query.filter(tables.GroupPackage.group_ident == grp)

    extra_breadcrumbs=(
        (url_for('group_by_loc', grp=grp), group.name),
    )

    return by_loc(query=query, extra_breadcrumbs=extra_breadcrumbs,
                  extra_args={'grp': group})

def by_loc(query=None, extra_breadcrumbs=(), extra_args=None):
    db = current_app.config['DB']()

    sort_key = request.args.get('sort', None)
    sort_reverse = request.args.get('reverse', None)
    print(sort_key)
    print(sort_reverse)
    if sort_reverse is None:
        def descending(p):
            return p
        def ascending(p):
            return p.desc()
    elif sort_reverse == '1':
        def descending(p):
            return p.desc()
        def ascending(p):
            return p
    else:
        abort(400)  # Bad request

    if query is None:
        query = queries.packages(db)

    query = query.filter(tables.Package.status.in_(('idle', 'blocked')))
    saved = query
    query = query.filter(tables.Package.loc_total)
    if sort_key == 'name':
        query = query.order_by(ascending(func.lower(tables.Package.name)))
    elif sort_key == 'loc':
        query = query.order_by(descending(tables.Package.loc_total))
    elif sort_key == 'python':
        query = query.order_by(descending(tables.Package.loc_python))
    elif sort_key == 'capi':
        query = query.order_by(descending(tables.Package.loc_capi))
    elif sort_key == 'py-percent':
        query = query.order_by(descending((0.1+tables.Package.loc_python)/tables.Package.loc_total))
    elif sort_key == 'capi-percent':
        query = query.order_by(descending((0.1+tables.Package.loc_capi)/tables.Package.loc_total))
    elif sort_key == 'py-small':
        query = query.order_by(ascending(
            tables.Package.loc_total - tables.Package.loc_python/1.5))
    elif sort_key == 'capi-small':
        query = query.order_by(descending(tables.Package.loc_capi>0))
        query = query.order_by(ascending(
            tables.Package.loc_total -
            tables.Package.loc_capi/1.5 +
            tables.Package.loc_python/9.9))
    elif sort_key == 'py-big':
        query = query.order_by(descending(
            tables.Package.loc_python * tables.Package.loc_python /
            (1.0+tables.Package.loc_total-tables.Package.loc_python)))
    elif sort_key == 'capi-big':
        query = query.order_by(descending(
            tables.Package.loc_capi * tables.Package.loc_capi /
            (1.0+tables.Package.loc_total-tables.Package.loc_capi)))
    elif sort_key == 'no-py':
        query = query.order_by(ascending(
            (tables.Package.loc_python + tables.Package.loc_capi + 0.0) /
            tables.Package.loc_total))
    elif sort_key is None:
        query = query.order_by(descending(tables.Package.loc_python +
                                          tables.Package.loc_capi))
    else:
        abort(400)  # Bad request
    query = query.order_by(tables.Package.loc_total)
    query = query.order_by(func.lower(tables.Package.name))

    packages = list(query)

    by_name = saved.order_by(func.lower(tables.Package.name))

    query = by_name.filter(tables.Package.loc_total == None)
    missing_packages = list(query)

    query = by_name.filter(tables.Package.loc_total == 0)
    no_code_packages = list(query)

    if extra_args is None:
        extra_args = {}

    return render_template(
        'by_loc.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('by_loc'), 'Packages by Code Stats'),
        ) + extra_breadcrumbs,
        packages=packages,
        sort_key=sort_key,
        sort_reverse=sort_reverse,
        missing_packages=missing_packages,
        no_code_packages=no_code_packages,
        grp=extra_args.get('grp')
    )


def mispackaged():
    # Parameters
    requested = request.args.get('requested', None)
    if requested not in ('1', None):
        abort(400)  # Bad request

    db = current_app.config['DB']()
    query = db.query(tables.Package)
    query = query.filter(tables.Package.status == 'mispackaged')
    query = query.join(tables.CollectionPackage)
    query = query.filter(
            tables.CollectionPackage.collection_ident == 'fedora')

    # Do an outer join with Links, but ONLY with rows of type 'bug' so that if
    #   a package has only e.g. a 'repo' link, it won't affect the results.
    query = query.outerjoin(tables.Link, and_(tables.Link.type == 'bug',
            tables.Link.collection_package_id == tables.CollectionPackage.id))

    # If appropriate: Filter only to packages where maintainer requested a patch
    if requested:
        query = query.join(tables.TrackingBug)
        query = query.filter(tables.TrackingBug.url ==
                             "https://bugzilla.redhat.com/show_bug.cgi?id=1333765")

    # Order by the last_update field, and if it's null, substitute it with the
    # year 9999 so it's very last. (Note: sqlite does not support NULLS LAST)
    query = query.order_by(func.ifnull(tables.Link.last_update, '9999'))

    # Speedup: Prevent starting subqueries for each package.
    query = query.options(subqueryload('collection_packages'))
    query = query.options(subqueryload('collection_packages.links'))
    query = query.options(subqueryload('collection_packages.tracking_bugs'))

    mispackaged = list(query)

    # Render the page, pass the data
    return render_template(
        'mispackaged.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('mispackaged', requested=1), 'Mispackaged'),
        ),
        requested=bool(requested),
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
    return render_template(
        'history-naming.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
            (url_for('namingpolicy'), 'Naming Policy'),
            (url_for('history'), 'History'),
        )
    )


def history_naming_csv():
    db = current_app.config['DB']()

    query = db.query(tables.HistoryNamingEntry)
    query = query.order_by(tables.HistoryNamingEntry.date)
    sio = io.StringIO()
    writer = csv.DictWriter(sio, ['commit', 'date', 'status', 'num_packages'])
    writer.writeheader()
    for row in query:
        writer.writerow({
            'commit': row.commit,
            'date': row.date,
            'status': row.status,
            'num_packages': row.num_packages,
        })
    return sio.getvalue()


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

def create_app(db_url, cache_config=None):
    if cache_config is None:
        cache_config = {'backend': 'dogpile.cache.null'}
    cache = make_region().configure(**cache_config)
    app = Flask(__name__)
    app.config['DB'] = sessionmaker(bind=create_engine(db_url))
    db = app.config['DB']()
    app.config['Cache'] = cache
    app.config['CONFIG'] = {c.key: json.loads(c.value)
                            for c in db.query(tables.Config)}
    app.jinja_env.undefined = StrictUndefined
    app.jinja_env.filters['md'] = markdown_filter
    app.jinja_env.filters['format_rpm_name'] = format_rpm_name
    app.jinja_env.filters['format_quantity'] = format_quantity
    app.jinja_env.filters['format_percent'] = format_percent
    app.jinja_env.filters['format_time_ago'] = format_time_ago

    @app.context_processor
    def add_template_globals():
        return {
            'cache_tag': uuid.uuid4(),
            'len': len,
            'log': math.log,
            'config': app.config['CONFIG'],
        }

    def _add_route(url, func, get_keys=()):
        @functools.wraps(func)
        def decorated(*args, **kwargs):
            creator = functools.partial(func, *args, **kwargs)
            key_dict = {'url': url,
                        'args': args,
                        'kwargs': kwargs,
                        'get': {k: request.args.get(k) for k in get_keys}}
            key = json.dumps(key_dict, sort_keys=True)
            print(key)
            return cache.get_or_create(key, creator)
        app.route(url)(decorated)

    _add_route("/", hello)
    _add_route("/stats.json", jsonstats)
    _add_route("/pkg/<pkg>/", package)
    _add_route("/grp/<grp>/", group)
    _add_route("/graph/", graph)
    _add_route("/graph/portingdb.json", graph_json)
    _add_route("/piechart.svg", piechart_svg)
    _add_route("/grp/<grp>/piechart.svg", piechart_grp)
    _add_route("/pkg/<pkg>/piechart.svg", piechart_pkg)
    _add_route("/grp/<grp>/graph/", graph_grp)
    _add_route("/grp/<grp>/graph/data.json", graph_json_grp)
    _add_route("/pkg/<pkg>/graph/", graph_pkg)
    _add_route("/pkg/<pkg>/graph/data.json", graph_json_pkg)
    _add_route("/by_loc/", by_loc, get_keys={'sort', 'reverse'})
    _add_route("/by_loc/grp/<grp>/", group_by_loc, get_keys={'sort', 'reverse'})
    _add_route("/mispackaged/", mispackaged, get_keys={'requested'})
    _add_route("/namingpolicy/", namingpolicy)
    _add_route("/namingpolicy/piechart.svg", piechart_namingpolicy)
    _add_route("/namingpolicy/history/", history_naming)
    _add_route("/namingpolicy/history/data.csv", history_naming_csv)
    _add_route("/history/", history, get_keys={'expand'})
    _add_route("/history/data.csv", history_csv)
    _add_route("/howto/", howto)

    return app


def main(db_url, cache_config=None, debug=False, port=5000):
    app = create_app(db_url, cache_config=cache_config)
    app.run(debug=debug, port=port)
