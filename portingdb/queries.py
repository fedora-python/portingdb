from collections import OrderedDict

from sqlalchemy import func, not_, or_
from sqlalchemy.types import Integer
from sqlalchemy.orm import eagerload, subqueryload, lazyload, aliased, join
from sqlalchemy.sql.expression import cast

from . import tables


def collections(db):
    query = db.query(tables.Collection)
    query = query.order_by(tables.Collection.order)
    return query


def packages(db):
    query = db.query(tables.Package)
    query = query.options(eagerload(tables.Package.by_collection))
    #query = query.options(subqueryload(tables.Package.requirements))
    return query


def _order_by_weight(db, query):
    subquery = db.query(
        tables.CollectionPackage,
        (func.sum(tables.Status.weight) + func.sum(tables.Priority.weight)).label('weight')
    )
    subquery = subquery.join(tables.CollectionPackage.status_obj)
    subquery = subquery.join(tables.CollectionPackage.priority_obj)
    subquery = subquery.group_by(tables.CollectionPackage.package_name)
    subquery = subquery.subquery()

    query = query.outerjoin(subquery, subquery.c.package_name == tables.Package.name)
    query = query.order_by(-subquery.c.weight)
    query = query.order_by(func.lower(tables.Package.name))
    return query


def _deps(db, package, forward=True):
    query = packages(db)
    package_table = aliased(tables.Package)

    if forward:
        col_a = tables.Dependency.requirement_name
        col_b = tables.Dependency.requirer_name
    else:
        col_a = tables.Dependency.requirer_name
        col_b = tables.Dependency.requirement_name
    query = query.outerjoin(tables.Dependency, col_a == tables.Package.name)
    query = query.outerjoin(package_table, col_b == package_table.name)

    query = query.filter(package_table.name == package.name)

    return query


def dependencies(db, package):
    return _deps(db, package, True)


def dependents(db, package):
    return _deps(db, package, False)


def _split(query, condition):
   return query.filter(condition), query.filter(or_(not_(condition), condition == None))


def _status_cols(db):
    cp = aliased(tables.CollectionPackage)

    def _st_cmps(val):
        return func.sum(cast(cp.status == val, Integer))

    cols = [
        func.count(cp.id).label('total'),
        (_st_cmps('dropped')).label('dropped'),
        (_st_cmps('dropped') + _st_cmps('released')).label('done'),
        _st_cmps('idle').label('idle'),
    ]
    return cp, cols


def _order_by_name(query):
    return query.order_by(func.lower(tables.Package.name))

def split_packages(db, query):
    parent_query = query

    cp, cols = _status_cols(db)
    subquery = db.query(*cols)
    subquery = subquery.add_column(cp.package_name.label('package_name'))
    subquery = subquery.group_by(cp.package_name).subquery()

    query = query.outerjoin(subquery, subquery.c.package_name == tables.Package.name)

    done, query = _split(query, subquery.c.done == subquery.c.total)
    done = done.order_by(subquery.c.dropped)
    done = _order_by_name(done)

    query, active = _split(query, subquery.c.done + subquery.c.idle == subquery.c.total)
    active = _order_by_weight(db, active)

    cp, cols = _status_cols(db)
    dt = aliased(tables.Dependency)
    q = db.query(dt)
    req_name = dt.requirer_name.label('requirer_name')
    subquery = q.outerjoin(cp, dt.requirement_name == cp.package_name)
    subquery = subquery.add_columns(req_name, *cols)
    subquery = subquery.group_by(req_name)
    subquery = subquery.subquery()
    query = query.outerjoin(subquery, subquery.c.requirer_name == tables.Package.name)

    query, ready = _split(query, subquery.c.done != subquery.c.total)
    ready = ready.order_by(-subquery.c.total)
    ready = _order_by_name(ready)

    blocked = query

    blocked = blocked.order_by(subquery.c.total - subquery.c.done)
    blocked = blocked.order_by(-subquery.c.total)
    blocked = _order_by_name(blocked)

    rv = OrderedDict([
        ("active", active),
        ("ready", ready),
        ("blocked", blocked),
        ("done", done),
    ])
    return rv
