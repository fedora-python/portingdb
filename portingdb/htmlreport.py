from collections import OrderedDict
import random

from flask import Flask, render_template, current_app, Markup, abort
from sqlalchemy import func, or_
from sqlalchemy.orm import subqueryload
from jinja2 import StrictUndefined
import markdown

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

    ready = list(ready)
    random_ready = random.choice(ready)

    # Summary query

    query = db.query(tables.Status)
    query = query.join(tables.Package, tables.Status.packages)
    query = query.add_column(func.count(tables.Package.name))
    query = query.group_by(tables.Package.status)
    query = query.order_by(tables.Status.order)
    status_summary = query

    # Product query

    query = db.query(tables.Product)
    query = query.join(tables.Product.packages)
    query = query.join(tables.Package.status_obj)
    query = query.group_by(tables.Product.ident)
    query = query.group_by(tables.Package.status)
    query = query.order_by(tables.Status.order)
    query = query.order_by(tables.Product.name)
    query = query.add_columns(tables.Package.status,
                              func.count(tables.Package.name))
    products = {}
    for product, status_ident, count in query:
        status = db.query(tables.Status).get(status_ident)
        pd = products.setdefault(product, OrderedDict())
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
        products=products,
    )

def package(pkg):
    db = current_app.config['DB']
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

def product(prod):
    db = current_app.config['DB']
    collections = list(queries.collections(db))

    product = db.query(tables.Product).get(prod)
    if product is None:
        abort(404)

    query = db.query(tables.Package)
    query = query.join(tables.Package.product_packages)
    query = query.join(tables.ProductPackage.product)
    query = query.join(tables.Package.status_obj)
    query = query.filter(tables.Product.ident == prod)
    query = query.order_by(tables.Status.order)
    query = queries.order_by_name(db, query)
    packages = list(query)

    query = query.filter(tables.ProductPackage.is_seed)
    seed_packages = query

    return render_template(
        'product.html',
        collections=collections,
        prod=product,
        packages=packages,
        deptree=list(gen_deptree(seed_packages)),
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


def create_app(db):
    app = Flask(__name__)
    app.config['DB'] = db
    app.add_template_global(db, name='db')
    app.jinja_env.undefined = StrictUndefined
    app.jinja_env.filters['md'] = markdown_filter
    app.route("/")(hello)
    app.route("/pkg/<pkg>/")(package)
    app.route("/prod/<prod>/")(product)

    return app


def main(db, debug=False):
    app = create_app(db)
    app.run(debug=debug)
