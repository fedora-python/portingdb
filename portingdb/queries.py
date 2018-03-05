from collections import OrderedDict

from sqlalchemy import func, not_, or_
from sqlalchemy.types import Integer
from sqlalchemy.orm import eagerload, subqueryload, lazyload, aliased, join
from sqlalchemy.sql import select
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


def order_by_weight(db, query):
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


def _deps(db, package, forward=True, run_time=True, build_time=False):
    query = packages(db)

    if forward:
        col_a = tables.Dependency.requirement_name
        col_b = tables.Dependency.requirer_name
    else:
        col_a = tables.Dependency.requirer_name
        col_b = tables.Dependency.requirement_name
    query = query.outerjoin(tables.Dependency, col_a == tables.Package.name)
    query = query.filter(col_b == package.name)

    query = query.filter(
        or_(tables.Dependency.run_time == run_time,
            tables.Dependency.build_time == build_time))

    query = query.join(tables.Status, tables.Status.ident == tables.Package.status)
    query = query.order_by(-tables.Status.weight)
    query = order_by_name(db, query)

    return query


def dependencies(db, package):
    return _deps(db, package, True)


def dependents(db, package):
    return _deps(db, package, False)


def build_dependencies(db, package):
    return _deps(db, package, True, False, True)


def build_dependents(db, package):
    return _deps(db, package, False, False, True)


def order_by_name(db, query):
    return query.order_by(func.lower(tables.Package.name))


def update_status_summaries(db):
    """Update per-package status"""
    main_update = tables.Package.__table__.update()

    cp = tables.CollectionPackage
    sti = aliased(tables.Status)
    sto = aliased(tables.Status)
    subquery = db.query(func.max(sti.rank).label('max'))
    subquery = subquery.join(cp, cp.status == sti.ident)
    subquery = subquery.add_column(cp.package_name.label('package_name'))
    subquery = subquery.group_by('package_name')
    subquery = subquery.subquery()

    query = db.query(tables.Package.name.label('package_name'))
    query = query.outerjoin(subquery, subquery.c.package_name == tables.Package.name)
    query = query.filter(subquery.c.max == sto.rank)
    query = query.add_column(sto.ident.label('status'))
    query = query.order_by(subquery.c.max)
    query = query.subquery()

    update = main_update.values(
        status=select([query.c.status]).where(query.c.package_name == tables.Package.name))
    db.execute(update)

    # Get rid of "unknown" status

    update = main_update.values(
        status='idle').where(tables.Package.status == 'unknown')
    db.execute(update)

    # Convert "idle" packages with un-ported dependencies to "blocked"

    dep = aliased(tables.Package)
    depcp = aliased(tables.CollectionPackage)
    subquery = db.query(depcp)
    subquery = subquery.join(dep, depcp.package_name == dep.name)
    subquery = subquery.filter(depcp.status != 'py3-only')
    subquery = subquery.filter(depcp.status != 'legacy-leaf')
    subquery = subquery.filter(depcp.status != 'released')
    subquery = subquery.filter(depcp.status != 'dropped')
    subquery = subquery.filter(depcp.status != 'unknown')
    subquery = subquery.filter(~depcp.nonblocking)
    subquery = subquery.join(tables.Dependency, tables.Dependency.requirement_name == dep.name)
    subquery = subquery.filter(tables.Dependency.requirer_name == tables.Package.name)
    subquery = subquery.filter(tables.Dependency.requirer_name != tables.Dependency.requirement_name)
    subquery = subquery.filter(tables.Dependency.run_time)

    update = main_update
    update = update.where(subquery.exists())
    update = update.where(tables.Package.status == 'idle')
    update = update.values(status='blocked')
    rv = db.execute(update)
    print(update, rv.rowcount)


def update_naming_summaries(db):
    """Update per-package is_misnamed attribute."""

    collection_package = tables.CollectionPackage

    cp_update = collection_package.__table__.update()
    update = cp_update.values(
        is_misnamed=select(
            [collection_package.rpms.any(is_misnamed=True)])
    ).where(
        collection_package.is_misnamed.is_(None))
    db.execute(update)


def update_group_closures(db):
    values = []
    for group in db.query(tables.Group):
        if group.ident is None:
            continue
        waiting = set(group.packages)
        pkgs = set()
        while waiting:
            pkg = waiting.pop()
            if pkg not in pkgs:
                pkgs.add(pkg)
                if pkg.status not in ('dropped', ):
                    waiting.update(pkg.run_time_requirements)
        pkgs.difference_update(group.packages)
        values.extend({'group_ident': group.ident, 'package_name': p.name}
                      for p in pkgs)

    update = tables.GroupPackage.__table__.insert()
    if values:
        db.execute(update, values)
