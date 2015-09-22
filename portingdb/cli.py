import logging
import os
import urllib.parse

import click
from sqlalchemy import create_engine, func
from sqlalchemy.orm import eagerload, subqueryload

from portingdb import tables
from portingdb.load import get_db, load_from_directory

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


def main():
    return cli(obj={})


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--datadir', help="Data directory", default='.', envvar='PORTINGDB_DATA', multiple=True)
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
        logging.basicConfig(level=logging.INFO)
        logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
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

    collections = list(db.query(tables.Collection))
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
                print('{percent_released:5.1f}% {name}  ({detail})'.format(
                    name=collection.name,
                    max_name_len=max_name_len,
                    percent_released=data.get('released', 0) / total * 100,
                    detail=detail,
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
    if ctx.obj['verbose']:
        click.secho('Before load:', fg='cyan')
        print_status(ctx)

    datadirs = ctx.obj['datadirs']
    db = ctx.obj['db']
    for datadir in datadirs:
        load_from_directory(db, datadir)
    db.commit()

    if ctx.obj['verbose']:
        click.secho('After load:', fg='cyan')
        print_status(ctx)


@cli.command()
@click.pass_context
def report(ctx):
    """Print out a report of all packages"""
    db = ctx.obj['db']
    columns, rows = click.get_terminal_size()
    collections = list(db.query(tables.Collection))

    for i, collection in enumerate(collections):
        print('│  ' * i + '┌╴' + collection.name)

    print('│  ' * len(collections))
    query = db.query(tables.Package)
    query = query.order_by(func.lower(tables.Package.name))
    query = query.options(eagerload(tables.Package.collection_packages))
    query = query.options(subqueryload(tables.Package.requirements))
    for package in query:
        for collection in collections:
            for cp in package.collection_packages:
                if cp.collection is collection:
                    state = cp.status_obj.term
                    prio = cp.priority_obj.term
                    click.echo('├{}{}'.format(state, prio), nl=False)
                    break
            else:
                print('│  ', end='')
        print(' ' + package.name, end=' ')
        reqs = []
        for req in package.requirements:
            for cp in req.collection_packages:
                if cp.status != 'released':
                    reqs.append(req.name)
                    break
        if reqs:
            print('({})'.format(', '.join(reqs)), end='')
        print()

    print('│  ' * len(collections))

    for i, collection in enumerate(reversed(collections)):
        print('│  ' * (len(collections)-i-1) + '└╴' + collection.name)


@cli.command()
@click.option('--debug/--no-debug', help="Run in debug mode")
@click.pass_context
def serve(ctx, debug):
    """Serve HTML reports via a HTTP server"""
    db = ctx.obj['db']
    from . import htmlreport

    htmlreport.main(db, debug=debug)
