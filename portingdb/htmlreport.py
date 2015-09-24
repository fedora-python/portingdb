from collections import OrderedDict

from flask import Flask, render_template, current_app
from sqlalchemy import func
from jinja2 import StrictUndefined

from . import tables
from . import queries


def hello():
    db = current_app.config['DB']
    collections = list(queries.collections(db))

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

    return render_template(
        'index.html',
        collections=collections,
        coll_info=coll_info,
        statuses=list(db.query(tables.Status).order_by(tables.Status.order)),
        priorities=list(db.query(tables.Priority).order_by(tables.Priority.order)),
        packages=queries.split_packages(db, queries.packages(db)),
    )

def package(pkg):
    db = current_app.config['DB']
    collections = list(queries.collections(db))

    package = db.query(tables.Package).get(pkg)

    dependencies = queries.dependencies(db, package)

    return render_template(
        'package.html',
        collections=collections,
        pkg=package,
        dependencies=queries.split_packages(db, queries.dependencies(db, package)),
        dependents=queries.split_packages(db, queries.dependents(db, package)),
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
