import logging
import os
import urllib.parse
import json
from pathlib import Path

import click

from portingdb.load_data import get_data
from portingdb.check_drops import check_drops
from portingdb.check_fti import check_fti

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
@click.option('-v', '--verbose', help="Output lots of information", count=True)
@click.option('-q', '--quiet', help="Output less information", count=True)
@click.pass_context
def cli(ctx, datadir, verbose, quiet):
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
    if not datadir:
        datadir = [DEFAULT_DATADIR]
    ctx.obj['datadirs'] = [os.path.abspath(d) for d in datadir]


@cli.command()
@click.option('--debug/--no-debug', help="Run in debug mode")
@click.option('--cache',
              help="""JSON-formatted dogpile.cache configuration, for example '{"backend": "'dogpile.cache.memory'"}'""")
@click.option('--port', type=int, default=5000,
              help="""Port to listen on (default: 5000)'""")
@click.pass_context
def serve(ctx, debug, cache, port):
    """Serve HTML reports via a HTTP server"""
    datadirs = ctx.obj['datadirs']
    from . import htmlreport

    if cache is None:
        cache_config = None
    else:
        cache_config = json.loads(cache)

    htmlreport.main(debug=debug, cache_config=cache_config,
                    directories=datadirs,
                    port=port)


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
cli.add_command(check_fti)
