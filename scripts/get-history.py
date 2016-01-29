#! /usr/bin/env python3
"""Get historical status data as a CSV file

Walks the Git history of the repo, loading each revision where data/ changed,
and recording the number of packages for each status.

When an existing data file is specified with --update, the file is repeated,
with commits that aren't in it appended.

Outputs to stdout.
"""

import subprocess
import functools
import tempfile
import shutil
import csv
import sys
import os

from sqlalchemy import create_engine, select, func
import click

from portingdb import tables
from portingdb.load import get_db


def run(args, **kwargs):
    kwargs.setdefault('universal_newlines', True)
    return subprocess.check_output(args, **kwargs)


def git_history(start='HEAD', end='9c4e924da9ede05b4d8903a622240259dfa0e2e5'):
    """Yield commit IDs along the "main" branch of a project"""
    args = ['git', 'log', '--pretty=%H', '--first-parent', start, '^' + end]
    return run(args).strip().splitlines()


# Not every commit was tested. Those that wouldn't load
# even with the corresponding library versions are here.
# (If it did load back then, it should load now as well.)
BAD_COMMITS = set("""
cabd81961c9d7b5f00ea525d290117176cb16dce
d854079db4d805c4f4f07ad5a4a7c94811030979
130e48367d333f1c3cb0fe89bcea6f576edb9354
""".splitlines())


@click.command(help=__doc__)
@click.option('-u', '--update', help='CSV file with existing data')
def main(update):
    excluded = set(BAD_COMMITS)
    tmpdir = tempfile.mkdtemp()
    writer = csv.DictWriter(sys.stdout,
                            ['commit', 'date', 'status', 'num_packages'],
                            lineterminator='\n')
    writer.writeheader()

    all_statuses = [
        "blocked", "dropped", "idle", "in-progress", "released", "mispackaged"]

    if update:
        with open(update) as f:
            for row in csv.DictReader(f):
                excluded.add(row['commit'])
                writer.writerow(row)

    try:
        tmpclone = os.path.join(tmpdir, 'tmp_clone')
        tmpdata = os.path.join(tmpclone, 'data')
        tmpdb = os.path.join(tmpclone, 'tmp-portingdb.sqlite')
        run(['git', 'clone', '.', tmpclone])
        prev_data_hash = None
        for commit in reversed(git_history()):
            data_hash = run(['git', 'rev-parse', commit + ':' + 'data'])
            if (commit not in excluded) and (data_hash != prev_data_hash):
                # Note: we don't remove files that didn't exist in the old
                # version.
                run(['git', 'checkout', commit, '--', 'data'], cwd=tmpclone)
                run(['python3', '-m', 'portingdb',
                     '--datadir', tmpdata,
                     '--db', tmpdb,
                     'load'])

                engine = create_engine('sqlite:///' + os.path.abspath(tmpdb))
                db = get_db(engine=engine)
                columns = [tables.Package.status, func.count()]
                query = select(columns).select_from(tables.Package.__table__)
                query = query.group_by(tables.Package.status)

                date = run(['git', 'log', '-n1', '--pretty=%ci', commit]).strip()
                package_numbers = {status: num_packages
                                   for status, num_packages
                                   in db.execute(query)}
                for status in all_statuses:
                    row = {
                        'commit': commit,
                        'date': date,
                        'status': status,
                        'num_packages': package_numbers.get(status, 0),
                    }
                    writer.writerow(row)

                os.unlink(tmpdb)
            prev_data_hash = data_hash
    finally:
        shutil.rmtree(tmpdir)
    return


if __name__ == '__main__':
    main()
