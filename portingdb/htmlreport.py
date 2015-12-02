from collections import OrderedDict, Counter
import random
import functools
import json
import math
import uuid

from flask import Flask, render_template, current_app, Markup, abort, url_for
from flask import make_response
from flask.json import jsonify
from sqlalchemy import func, or_, create_engine
from sqlalchemy.orm import subqueryload, eagerload, sessionmaker
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
        random_ready=random_ready,
        len=len,
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


def graph_json():
    db = current_app.config['DB']()
    query = queries.packages(db)
    query = query.filter(tables.Package.status != 'released')
    query = query.filter(tables.Package.status != 'dropped')
    packages = list(query)
    nodes = [{'name': p.name, 'status': p.status, 'color': p.status_obj.color,
              'pending_requirers': len(p.pending_requirers)}
             for p in packages]
    names = [p.name for p in packages]

    query = db.query(tables.Dependency)

    links = [{"source": names.index(d.requirer_name),
              "target": names.index(d.requirement_name),
             }
             for d in query
             if d.requirer_name in names and d.requirement_name in names]
    return jsonify(nodes=nodes, links=links)


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


def create_app(db_url, cache_config=None):
    if cache_config is None:
        cache_config = {'backend': 'dogpile.cache.null'}
    cache = make_region().configure(**cache_config)
    app = Flask(__name__)
    app.config['DB'] = sessionmaker(bind=create_engine(db_url))
    app.config['Cache'] = cache
    app.jinja_env.undefined = StrictUndefined
    app.jinja_env.filters['md'] = markdown_filter
    app.jinja_env.filters['format_rpm_name'] = format_rpm_name

    @app.context_processor
    def add_cache_tag():
        return {'cache_tag': uuid.uuid4()}

    def _add_route(url, func):
        @functools.wraps(func)
        def decorated(*args, **kwargs):
            creator = functools.partial(func, *args, **kwargs)
            key = json.dumps({'url': url, 'args': args, 'kwargs': kwargs},
                             sort_keys=True)
            print(key)
            return cache.get_or_create(key, creator)
        app.route(url)(decorated)

    _add_route("/", hello)
    _add_route("/pkg/<pkg>/", package)
    _add_route("/grp/<grp>/", group)
    _add_route("/graph/", lambda: render_template('graph.html'))
    _add_route("/graph/portingdb.json", graph_json)
    _add_route("/piechart.svg", piechart_svg)
    _add_route("/grp/<grp>/piechart.svg", piechart_grp)
    _add_route("/pkg/<pkg>/piechart.svg", piechart_pkg)

    return app


def main(db_url, cache_config=None, debug=False):
    app = create_app(db_url, cache_config=cache_config)
    app.run(debug=debug)
