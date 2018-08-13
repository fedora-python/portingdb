import logging
import os
import urllib.parse
import json

import click
from sqlalchemy import create_engine, func, or_, and_
from sqlalchemy.orm import eagerload, subqueryload

from portingdb import tables
from portingdb.htmlreport import get_naming_policy_progress
from portingdb.load import get_db, load_from_directories
from portingdb.check_drops import check_drops

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


def main():
    return cli(obj={})


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--datadir', default='.', envvar='PORTINGDB_DATA', multiple=True,
              help="Data directory. If given multiple times, the directories "
                "are searched in order: files in directories that appear "
                "earlier on the command line shadow the later ones.")
@click.option('--db', help="Database file", default='portingdb.sqlite', envvar='PORTINGDB_FILE')
@click.option('-v', '--verbose', help="Output lots of information", count=True)
@click.option('-q', '--quiet', help="Output less information", count=True)
@click.pass_context
def cli(ctx, datadir, db, verbose, quiet):
    """Manipulate and query a package porting database.
    """
    verbose -= quiet
    ctx.obj['verbose'] = verbose
    if verbose >= 2:
        if verbose >= 3:
            level = logging.DEBUG
        else:
            level = logging.INFO
        logging.basicConfig(level=level)
        logging.getLogger('sqlalchemy.engine').setLevel(level)
    ctx.obj['datadirs'] = [os.path.abspath(d) for d in datadir]

    if 'db' in ctx.obj:
        url = '<passed in>'
    else:
        if db is None:
            url = 'sqlite:///'
        else:
            parsed = urllib.parse.urlparse(db)
            if parsed.scheme:
                url = db
            else:
                url = 'sqlite:///' + os.path.abspath(db)
        engine = create_engine(url)
        ctx.obj['db'] = get_db(None, engine=engine)
    ctx.obj['db_url'] = url


@cli.command()
@click.pass_context
def status(ctx):
    """Print database info and high-level stats"""
    print_status(ctx)

def print_status(ctx):
    datadirs = ctx.obj['datadirs']
    db_url = ctx.obj['db_url']
    db = ctx.obj['db']

    pkg_count = db.query(tables.Package).count()
    if ctx.obj['verbose']:
        print('Data directory: {}'.format(':'.join(datadirs)))
        print('Database: {}'.format(db_url))

        print('Package count: {}'.format(pkg_count))
    if not pkg_count:
        click.secho('Database not filled; please run portingdb load', fg='red')

    collections = list(db.query(tables.Collection).order_by(tables.Collection.order))
    if collections:
        max_name_len = max(len(c.name) for c in collections)
        for i, collection in enumerate(collections):
            query = db.query(tables.CollectionPackage.status,
                             func.count(tables.CollectionPackage.id))
            query = query.filter(tables.CollectionPackage.collection == collection)
            query = query.join(tables.CollectionPackage.status_obj)
            query = query.group_by(tables.CollectionPackage.status)
            query = query.order_by(tables.Status.order)
            data = dict(query)
            total = sum(v for k, v in data.items())
            detail = ', '.join('{1} {0}'.format(k, v) for k, v in data.items())
            if total:
                num_done = sum(data.get(s, 0)
                               for s in ('released', 'dropped', 'legacy-leaf', 'py3-only'))
                print('{score:5.1f}% {name}  ({detail}) / {total}'.format(
                    name=collection.name,
                    max_name_len=max_name_len,
                    score=num_done / total * 100,
                    detail=detail,
                    total=total
                ))
            else:
                print('  ???% {name:>{max_name_len}}'.format(
                    name=collection.name,
                    max_name_len=max_name_len,
                ))


@cli.command()
@click.pass_context
def load(ctx):
    """Load the database from JSON/YAML data"""
    datadirs = ctx.obj['datadirs']
    db = ctx.obj['db']

    db.execute('PRAGMA journal_mode=WAL')

    if ctx.obj['verbose']:
        click.secho('Before load:', fg='cyan')
        print_status(ctx)

    for table in tables.metadata.sorted_tables:
        db.execute(table.delete())

    warnings = load_from_directories(db, datadirs)
    for warning in warnings:
        click.secho(warning, fg='red')
    db.commit()

    if ctx.obj['verbose']:
        click.secho('After load:', fg='cyan')
        print_status(ctx)


def print_collection_header(collections, foot=False):
    if not foot:
        for i, collection in enumerate(collections):
            print('│  ' * i + '┌╴' + collection.name)
    else:
        for i, collection in enumerate(reversed(collections)):
            print('│  ' * (len(collections)-i-1) + '└╴' + collection.name)


def print_collection_info(package, collections):
    if package:
        for collection in collections:
            cp = package.by_collection.get(collection.ident)
            if cp:
                state = cp.status_obj.term
                prio = cp.priority_obj.term
                click.echo('├{}{}'.format(state, prio), nl=False)
            else:
                print('│  ', end='')
    else:
        for collection in collections:
            print('│  ', end='')

@cli.command()
@click.pass_context
def report(ctx):
    """Print out a report of all packages"""
    db = ctx.obj['db']
    columns, rows = click.get_terminal_size()
    collections = list(db.query(tables.Collection).order_by(tables.Collection.order))

    print_collection_header(collections)

    print('│  ' * len(collections))
    query = db.query(tables.Package)
    query = query.order_by(func.lower(tables.Package.name))
    query = query.options(eagerload(tables.Package.by_collection))
    query = query.options(subqueryload(tables.Package.run_time_requirements))
    for package in query:
        print_collection_info(package, collections)
        print(' ' + package.name, end=' ')
        reqs = []
        for req in package.run_time_requirements:
            for cp in req.by_collection.values():
                if cp.status not in ('released', 'legacy-leaf', 'py3-only'):
                    reqs.append(req.name)
                    break
        if reqs:
            print('({})'.format(', '.join(reqs)), end='')
        print()

    print('│  ' * len(collections))

    print_collection_header(collections, foot=True)


@cli.command()
@click.option('--debug/--no-debug', help="Run in debug mode")
@click.option('--cache',
              help="""JSON-formatted dogpile.cache configuration, for example '{"backend": "'dogpile.cache.memory'"}'""")
@click.option('--port', type=int, default=5000,
              help="""Port to listen on (default: 5000)'""")
@click.pass_context
def serve(ctx, debug, cache, port):
    """Serve HTML reports via a HTTP server"""
    db_url = ctx.obj['db_url']
    from . import htmlreport

    if cache is None:
        cache_config = None
    else:
        cache_config = json.loads(cache)

    htmlreport.main(db_url=db_url, debug=debug, cache_config=cache_config,
                    port=port)


@cli.command()
@click.option('-x', '-exclude', help="Package(s) to exclude", multiple=True)
@click.option('-t/-T', '--trim/--no-trim', help="Don't recurse into ported packages (default: on)", default=True)
@click.option('-s/-S', '--skip/--no-skip', help="Don't show ported packages at all")
@click.option('-g/-G', '--graph/--no-graph', help="Show graph (default: on)", default=True)
@click.argument('package', nargs=-1)
@click.pass_context
def deps(ctx, package, exclude, trim, skip, graph):
    """Print a dependency graph of the given package(s)"""
    db = ctx.obj['db']

    collections = list(db.query(tables.Collection).order_by(tables.Collection.order))

    query = db.query(tables.Package)

    seen = set()
    exclude = set(exclude)
    seen.update(query.get(n) for n in exclude)

    print_collection_header(collections)

    if graph:
        print_collection_info(None, collections)
        print('[{}]'.format(','.join(package)))

    def can_ignore(pkg):
        if not trim:
            return False
        result = False
        for col in collections:
            cp = pkg.by_collection.get(col.ident)
            if cp:
                if cp.status not in ('released', 'legacy-leaf', 'dropped'):
                    return False
                result = True
        return result

    pkgs = [None] + [query.get(n) for n in package]
    while pkgs:
        pkg = pkgs.pop()
        if pkg is None:
            continue
        if pkg in seen:
            if not graph:
                continue
            reqs = []
            e = '*'
        elif can_ignore(pkg):
            reqs = []
            e = '✔'
        else:
            seen.add(pkg)
            reqs = [p for p in pkg.run_time_requirements if p is not pkg]
            if skip:
                reqs = [r for r in reqs
                        if not (can_ignore(r) or r.name in exclude)]
            e = ''
        if reqs:
            c = '┬'
        else:
            c = '─'
        lines = []
        found = False
        for p in pkgs:
            if p is None:
                if found:
                    lines.append('─')
                else:
                    lines.append(' ')
            elif p is pkg:
                if found:
                    lines.append('┴')
                else:
                    lines.append('└')
                found = True
            else:
                if found:
                    lines.append('┼')
                else:
                    lines.append('│')
        if found:
            l = '┴'
        else:
            l = '└'
        print_collection_info(pkg, collections)
        if graph:
            print(''.join(lines) + l + '╴', end='')
        else:
            print(' ', end='')
        print(pkg.name + e)
        pkgs = [None if p is pkg else p for p in pkgs]
        if graph and len(reqs) > 1:
            print_collection_info(None, collections)
            print(''.join('│' if n else ' ' for n in pkgs), end='  ')
            print('├' + '┬' * (len(reqs) - 2) + '┐')
        pkgs.extend([None, None])
        pkgs.extend(reqs)

    print_collection_header(collections, foot=True)


@cli.command()
@click.pass_context
def update(ctx):
    """Update the calculated data in thre database"""
    db = ctx.obj['db']

    from . import queries
    queries.update_group_closures(db)

@cli.command('upstream-idle')
@click.pass_context
def upstream_idle(ctx):
    """List packages marked as released upstream which are currently idle.

    Exits with error code 1 if such packages are found.

    Use the --verbose flag to get the output pretty-printed for humans.
    """
    db = ctx.obj['db']

    query = db.query(tables.CollectionPackage)
    query = query.join(tables.Package,
            tables.Package.name == tables.CollectionPackage.name)
    query = query.filter(tables.Package.status == 'idle')
    query = query.filter(
            tables.CollectionPackage.collection_ident == 'upstream')
    query = query.filter(tables.CollectionPackage.status == 'released')

    results = list(query)
    if ctx.obj['verbose'] > 0:
        if results:
            print("\nThe following packages are both 'idle' and marked as "
                    "'released' upstream:\n")
            for r in results:
                print("\t{}".format(r.name))
            print()
        else:
            print("\nThere are no packages both 'idle' and marked as "
                    " 'released' upstream.\n")
    else:
        for r in results:
            print("{}".format(r.name))
    if results:
        exit(1)

@cli.command('bugless-mispackaged')
@click.pass_context
def bugless_mispackaged(ctx):
    """List mispackaged packages that do not have a BugZilla report yet.

    Exits with error code 1 if such packages are found.

    Use the --verbose flag to get the output pretty-printed for humans.
    """
    db = ctx.obj['db']

    query = db.query(tables.Package)
    query = query.filter(tables.Package.status == 'mispackaged')
    query = query.join(tables.CollectionPackage)
    query = query.filter(
            tables.CollectionPackage.collection_ident == 'fedora')

    # Do an outer join with Links, but ONLY with rows of type 'bug' so that if
    #   a package has only e.g. a 'repo' link, it won't affect the results.
    query = query.outerjoin(tables.Link, and_(tables.Link.type == 'bug',
        tables.Link.collection_package_id == tables.CollectionPackage.id))

    # Only show packages that do NOT have a corresponding 'bug' link, or
    #   packages whese bug link is not automatically generated but comes from
    #   fedora-update.yaml instead.
    query = query.filter(or_(tables.Link.id == None, tables.Link.note == None))

    results = list(query)
    if ctx.obj['verbose'] > 0:
        if results:
            print("\nThe following packages are both 'mispackaged' and "
                    "do not have an associated Bugzilla report:\n")
            for p in results:
                print("\t{}".format(p.name))
            print()
        else:
            print("\nThere are no packages both 'mispackaged' and "
                    "not having an associated Bugzilla report.\n")
    else:
        for p in results:
            print("{}".format(p.name))

    if results:
        exit(1)

@cli.command('closed-mispackaged')
@click.pass_context
def closed_mispackaged(ctx):
    """List mispackaged packages whose BugZilla report is closed.

    Exits with error code 1 if such packages are found.

    Use the --verbose flag to get the output pretty-printed for humans.
    """
    db = ctx.obj['db']

    query = db.query(tables.Package)
    query = query.filter(tables.Package.status == 'mispackaged')
    query = query.join(tables.CollectionPackage)
    query = query.filter(
            tables.CollectionPackage.collection_ident == 'fedora')
    query = query.join(tables.Link)
    query = query.filter(tables.Link.type == 'bug')
    query = query.filter(tables.Link.note.like("CLOSED %"))

    results = list(query)
    if ctx.obj['verbose'] > 0:
        if results:
            print("\nThe following packages are both 'mispackaged' and "
                    "their associated Bugzilla report is CLOSED:\n")
            for p in results:
                print("\t{}".format(p.name))
            print()
        else:
            print("\nThere are no packages both 'mispackaged' and "
                    "having the associated Bugzilla report CLOSED.\n")
    else:
        for p in results:
            print("{}".format(p.name))

    if results:
        exit(1)


@cli.command()
@click.argument(
    'category',
    type=click.Choice(['misnamed-subpackage', 'ambiguous-requires', 'blocked']))
@click.pass_context
def naming(ctx, category):
    """List packages with selected naming scheme issue."""
    db = ctx.obj['db']
    _, data = get_naming_policy_progress(db)
    data_map = {
        'misnamed-subpackage': 0,
        'ambiguous-requires': 1,
        'blocked': 2
    }
    packages_index = data_map.get(category)
    require_misnamed = [pkg.name for pkg in data[packages_index][1]]
    for pkg_name in require_misnamed:
        print(pkg_name)


cli.add_command(check_drops)
