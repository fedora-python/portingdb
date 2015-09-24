from sqlalchemy import func
from sqlalchemy.orm import eagerload, subqueryload, aliased

from . import tables


def collections(db):
    query = db.query(tables.Collection)
    query = query.order_by(tables.Collection.order)
    return query


def packages(db):
    subquery = db.query(
        tables.CollectionPackage,
        (func.sum(tables.Status.weight) + func.sum(tables.Priority.weight)).label('weight')
    )
    subquery = subquery.join(tables.CollectionPackage.status_obj)
    subquery = subquery.join(tables.CollectionPackage.priority_obj)
    subquery = subquery.group_by(tables.CollectionPackage.package_name)
    subquery = subquery.subquery()

    query = db.query(tables.Package)
    query = query.join(subquery, subquery.c.package_name == tables.Package.name)
    query = query.order_by(-subquery.c.weight)
    query = query.order_by(func.lower(tables.Package.name))
    query = query.options(eagerload(tables.Package.by_collection))
    query = query.options(subqueryload(tables.Package.requirements))
    return query


def _deps(db, package, forward=True):
    query = packages(db)
    package_table = aliased(tables.Package)

    if forward:
        col_a = tables.Dependency.requirer_name
        col_b = tables.Dependency.requirement_name
    else:
        col_a = tables.Dependency.requirement_name
        col_b = tables.Dependency.requirer_name
    query = query.join(tables.Dependency, col_a == tables.Package.name)
    query = query.join(package_table, col_b == package_table.name)

    query = query.filter(package_table.name == package.name)

    return query


def dependencies(db, package):
    return _deps(db, package, True)


def dependents(db, package):
    return _deps(db, package, False)
