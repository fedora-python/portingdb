from collections import OrderedDict

from flask import Flask, render_template, current_app
from sqlalchemy import func, or_
from sqlalchemy.orm import subqueryload
from jinja2 import StrictUndefined

from . import tables
from . import queries


def hello():
    db = current_app.config['DB']

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

    # Summary query

    query = db.query(tables.Status)
    query = query.join(tables.Package, tables.Status.packages)
    query = query.add_column(func.count(tables.Package.name))
    query = query.group_by(tables.Package.status)
    query = query.order_by(-tables.Status.rank)
    status_summary = query

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
    )

def package(pkg):
    db = current_app.config['DB']
    collections = list(queries.collections(db))

    package = db.query(tables.Package).get(pkg)

    dependencies = queries.dependencies(db, package)

    dependents = queries.dependents(db, package)

    return render_template(
        'package.html',
        collections=collections,
        pkg=package,
        dependencies=list(dependencies),
        dependents=list(dependents),
    )


def create_app(db):
    app = Flask(__name__)
    app.config['DB'] = db
    app.add_template_global(db, name='db')
    app.route("/")(hello)
    app.route("/pkg/<pkg>/")(package)
    app.jinja_env.undefined = StrictUndefined

    return app


def main(db, debug=False):
    app = create_app(db)
    app.run(debug=debug)
