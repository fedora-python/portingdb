from collections import OrderedDict, Counter
import random
import functools
import json
import math
import uuid
import io
import csv

from flask import Flask, render_template, current_app, Markup, abort, url_for
from flask import make_response, request
from flask.json import jsonify
from sqlalchemy import func, or_, create_engine
from sqlalchemy.orm import subqueryload, eagerload, sessionmaker, joinedload
from jinja2 import StrictUndefined
import markdown
from dogpile.cache import make_region
from dogpile.cache.api import NO_VALUE

from . import tables
from . import queries

tau = 2 * math.pi


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

    total_pkg_count = queries.packages(db).count()

    # Main package query

    query = queries.packages(db)

    active, query = queries.split(query, tables.Package.status == 'in-progress')
    active = queries.order_by_weight(db, active)
    active = queries.order_by_name(db, active)
    active = active.options(subqueryload('collection_packages'))
    active = active.options(subqueryload('collection_packages.links'))

    done, query = queries.split(query, tables.Package.status == 'released')
    done = queries.order_by_name(db, done)

    dropped, query = queries.split(query, tables.Package.status == 'dropped')
    dropped = queries.order_by_name(db, dropped)

    mispackaged, query = queries.split(query, tables.Package.status == 'mispackaged')
    mispackaged = queries.order_by_name(db, mispackaged)

    blocked, query = queries.split(query, tables.Package.status == 'blocked')
    blocked = blocked.options(subqueryload('requirements'))
    blocked = queries.order_by_name(db, blocked)

    ready, query = queries.split(query, tables.Package.status == 'idle')
    ready = ready.options(subqueryload('requirers'))
    ready = queries.order_by_name(db, ready)

    assert query.count() == 0

    active = list(active)
    done = list(done)
    ready = list(ready)
    blocked = list(blocked)
    mispackaged = list(mispackaged)
    dropped = list(dropped)
    random_ready = random.choice(ready)

    the_score = (len(done) + len(dropped)) / total_pkg_count

    # Nonbolocking set query
    query = db.query(tables.Package)
    query = query.outerjoin(tables.Package.collection_packages)
    query = query.filter(tables.CollectionPackage.nonblocking)
    nonblocking = set(query)

    # Group query

    query = db.query(tables.Group)
    query = query.filter(~tables.Group.hidden)
    query = query.join(tables.Group.packages)
    query = query.join(tables.Package.status_obj)
    query = query.group_by(tables.Group.ident)
    query = query.group_by(tables.Package.status)
    query = query.order_by(tables.Status.order)
    query = query.order_by(tables.Group.name)
    query = query.add_columns(tables.Package.status,
                              func.count(tables.Package.name))
    groups = OrderedDict()
    for group, status_ident, count in query:
        status = db.query(tables.Status).get(status_ident)
        pd = groups.setdefault(group, OrderedDict())
        pd[status] = pd.get(status, 0) + count

    return render_template(
        'index.html',
        breadcrumbs=(
            (url_for('hello'), 'Python 3 Porting Database'),
        ),
        collections=collections,
        coll_info=coll_info,
        statuses=list(db.query(tables.Status).order_by(tables.Status.order)),
        priorities=list(db.query(tables.Priority).order_by(tables.Priority.order)),
        total_pkg_count=total_pkg_count,
        status_summary=get_status_summary(db),
        active_packages=active,
        ready_packages=ready,
        blocked_packages=blocked,
        done_packages=done,
        dropped_packages=dropped,
        mispackaged_packages=mispackaged,
        random_ready=random_ready,
        groups=groups,
        nonblocking=nonblocking,
        the_score=the_score,
    )


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

    in_progress_deps = [p for p in dependencies if p.status == 'in-progress']

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
        in_progress_deps=in_progress_deps,
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
        if pkg in seen or pkg.status in ('released', 'dropped', 'idle'):
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
        query = query.filter(tables.Package.status != 'released')
        query = query.filter(tables.Package.status != 'dropped')
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
                            if p.status not in {'released', 'dropped'})
        todo = {root_package}
        requirers = set()
        while todo:
            package = todo.pop()
            if package not in requirers:
                requirers.add(package)
                todo.update(p for p in package.requirers
                            if p.status not in {'released', 'dropped'})
        packages = list(requirements | requirers | {root_package})
    nodes = [{'name': p.name,
              'status': p.status,
              'color': graph_color(p),
              'status_color': '#' + p.status_obj.color,
              'size': 3.5+math.log((p.loc_python or 1)+(p.loc_capi or 1), 50),
              'num_requirers': len(p.pending_requirers),
              'num_requirements': len(p.pending_requirements),
             }
             for p in packages]
    names = [p.name for p in packages]

    query = db.query(tables.Dependency)

    links = [{"source": names.index(d.requirer_name),
              "target": names.index(d.requirement_name),
             }
             for d in query
             if d.requirer_name in names and d.requirement_name in names]
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

    query = query.filter(tables.Package.status.in_(('idle', 'in-progress', 'blocked')))
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
    _add_route("/history/", history, get_keys={'expand'})
    _add_route("/history/data.csv", history_csv)

    return app


def main(db_url, cache_config=None, debug=False, port=5000):
    app = create_app(db_url, cache_config=cache_config)
    app.run(debug=debug, port=port)
