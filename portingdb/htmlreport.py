from collections import OrderedDict
import random

from flask import Flask, render_template, current_app, Markup, abort
from sqlalchemy import func, or_, create_engine
from sqlalchemy.orm import subqueryload, sessionmaker
from jinja2 import StrictUndefined
import markdown

from . import tables
from . import queries


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

    all_pkg_query = queries.packages(db)

    # Main package query

    query = queries.packages(db)

    active, query = queries.split(query, tables.Package.status == 'in-progress')
    active = queries.order_by_weight(db, active)
    active = queries.order_by_name(db, active)

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

    ready = list(ready)
    random_ready = random.choice(ready)

    # Summary query

    query = db.query(tables.Status)
    query = query.join(tables.Package, tables.Status.packages)
    query = query.add_column(func.count(tables.Package.name))
    query = query.group_by(tables.Package.status)
    query = query.order_by(tables.Status.order)
    status_summary = query

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
    groups = {}
    for group, status_ident, count in query:
        status = db.query(tables.Status).get(status_ident)
        pd = groups.setdefault(group, OrderedDict())
        pd[status] = pd.get(status, 0) + count

    return render_template(
        'index.html',
        collections=collections,
        coll_info=coll_info,
        statuses=list(db.query(tables.Status).order_by(tables.Status.order)),
        priorities=list(db.query(tables.Priority).order_by(tables.Priority.order)),
        all_pkg_query=all_pkg_query,
        status_summary=status_summary,
        active_packages=active,
        ready_packages=ready,
        blocked_packages=blocked,
        done_packages=done,
        dropped_packages=dropped,
        random_ready=random_ready,
        len=len,
        groups=groups,
    )

def package(pkg):
    db = current_app.config['DB']()
    collections = list(queries.collections(db))

    package = db.query(tables.Package).get(pkg)
    if package is None:
        abort(404)

    dependencies = list(queries.dependencies(db, package))

    dependents = list(queries.dependents(db, package))

    return render_template(
        'package.html',
        collections=collections,
        pkg=package,
        dependencies=list(dependencies),
        dependents=list(dependents),
        deptree=[(package, gen_deptree(dependencies))],
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
    query = query.order_by(tables.Status.order)
    query = queries.order_by_name(db, query)
    packages = list(query)

    query = query.filter(tables.GroupPackage.is_seed)
    seed_groups = query

    return render_template(
        'group.html',
        collections=collections,
        grp=group,
        packages=packages,
        deptree=list(gen_deptree(seed_groups)),
    )


def gen_deptree(base, *, seen=None):
    seen = seen or set()
    base = tuple(base)
    for pkg in base:
        if pkg in seen or pkg.status in ('released', 'dropped', 'idle'):
            yield pkg, []
        else:
            reqs = sorted(pkg.requirements,
                          key=lambda p: (-p.status_obj.rank, p.name))
            yield pkg, gen_deptree(reqs, seen=seen)
        seen.add(pkg)


def markdown_filter(text):
    return Markup(markdown.markdown(text))


def create_app(db_url):
    app = Flask(__name__)
    app.config['DB'] = sessionmaker(bind=create_engine(db_url))
    app.jinja_env.undefined = StrictUndefined
    app.jinja_env.filters['md'] = markdown_filter
    app.route("/")(hello)
    app.route("/pkg/<pkg>/")(package)
    app.route("/grp/<grp>/")(group)

    return app


def main(db_url, debug=False):
    app = create_app(db_url)
    app.run(debug=debug)
