import logging
import os
import urllib.parse
import json
from pathlib import Path

import click
from sqlalchemy import create_engine, func, or_, and_
from sqlalchemy.orm import eagerload, subqueryload

from portingdb import tables
from portingdb.load import get_db, load_from_directories
from portingdb.load_data import get_data
from portingdb.check_drops import check_drops

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])

# Default '--datadir' is the `data` directory from this repo
DEFAULT_DATADIR = Path(__file__).parent / '../data'

def main():
    return cli(obj={})


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--datadir', envvar='PORTINGDB_DATA', multiple=True,
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
    if not datadir:
        datadir = [DEFAULT_DATADIR]
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
    datadirs = ctx.obj['datadirs']
    from . import htmlreport

    if cache is None:
        cache_config = None
    else:
        cache_config = json.loads(cache)

    htmlreport.main(db_url=db_url, debug=debug, cache_config=cache_config,
                    directories=datadirs,
                    port=port)


@cli.command()
@click.pass_context
def update(ctx):
    """Update the calculated data in thre database"""
    db = ctx.obj['db']

    from . import queries
    queries.update_group_closures(db)


@cli.command('bugless-mispackaged')
@click.pass_context
def bugless_mispackaged(ctx):
    """List mispackaged packages that do not have a BugZilla report yet.

    Exits with error code 1 if such packages are found.

    Use the --verbose flag to get the output pretty-printed for humans.
    """
    data = get_data(*ctx.obj['datadirs'])

    results = []
    for package in data['packages'].values():
        if package['status'] == 'mispackaged':
            if not any(link['type'] == 'bug' for link in package['links']):
                results.append(package)

    if ctx.obj['verbose'] > 0:
        if results:
            print("\nThe following packages are both 'mispackaged' and "
                    "do not have an associated Bugzilla report:\n")
            for p in results:
                print("\t{}".format(p['name']))
            print()
        else:
            print("\nThere are no packages both 'mispackaged' and "
                    "not having an associated Bugzilla report.\n")
    else:
        for p in results:
            print("{}".format(p['name']))

    if results:
        exit(1)

@cli.command('closed-mispackaged')
@click.pass_context
def closed_mispackaged(ctx):
    """List mispackaged packages whose BugZilla report is closed.

    Exits with error code 1 if such packages are found.

    Use the --verbose flag to get the output pretty-printed for humans.
    """
    data = get_data(*ctx.obj['datadirs'])

    results = []
    for package in data['packages'].values():
        if package['status'] == 'mispackaged':
            for link in package['links']:
                if link['type'] == 'bug' and link['note'].startswith('CLOSED'):
                    results.append(package)

    if ctx.obj['verbose'] > 0:
        if results:
            print("\nThe following packages are both 'mispackaged' and "
                    "their associated Bugzilla report is CLOSED:\n")
            for p in results:
                print("\t{}".format(p['name']))
            print()
        else:
            print("\nThere are no packages both 'mispackaged' and "
                    "having the associated Bugzilla report CLOSED.\n")
    else:
        for p in results:
            print("{}".format(p['name']))

    if results:
        exit(1)

@cli.command()
@click.argument(
    'category',
    type=click.Choice(['misnamed-subpackage', 'ambiguous-requires', 'blocked']))
@click.pass_context
def naming(ctx, category):
    """List packages with selected naming scheme issue."""
    data = get_data(*ctx.obj['datadirs'])
    for package in data['packages'].values():
        if category == 'misnamed-subpackage' and package['is_misnamed']:
            print(package['name'])
        if category == 'ambiguous-requires' and package['unversioned_requires']:
            print(package['name'])
        if category == 'blocked' and package['blocked_requires']:
            print(package['name'])


cli.add_command(check_drops)
