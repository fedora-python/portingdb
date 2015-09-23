from collections import OrderedDict

from flask import Flask, render_template, current_app
from sqlalchemy import func
from sqlalchemy.orm import eagerload, subqueryload

from . import tables


def hello():
    db = current_app.config['DB']

    collections = list(db.query(tables.Collection).order_by(tables.Collection.order))

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

    query = db.query(tables.Package)
    query = query.order_by(func.lower(tables.Package.name))
    query = query.options(eagerload(tables.Package.collection_packages))
    query = query.options(subqueryload(tables.Package.requirements))
    packages = query

    return render_template(
        'index.html',
        collections=collections,
        coll_info=coll_info,
        statuses=list(db.query(tables.Status).order_by(tables.Status.order)),
        priorities=list(db.query(tables.Priority).order_by(tables.Priority.order)),
        packages=packages,
    )


def create_app(db):
    app = Flask(__name__)
    app.config['DB'] = db
    app.add_template_global(db, name='db')
    app.route("/")(hello)

    return app


def main(db, debug=False):
    app = create_app(db)
    app.run(debug=debug)
