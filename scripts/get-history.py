#! /usr/bin/env python3
"""Get historical status data as a CSV file

Walks the Git history of the repo, loading each revision where data/ changed,
and recording the number of packages for each status.

When an existing data file is specified with --update, the file is repeated,
with commits that aren't in it appended.

Outputs to stdout.
"""

import collections
import subprocess
import tempfile
import shutil
import csv
import sys
import os

import click

from portingdb import tables
from portingdb.load_data import get_data


HISTORY_END_COMMIT = '9c4e924da9ede05b4d8903a622240259dfa0e2e5'
HISTORY_NAMING_END_COMMIT = 'e88b40f45cde37c6956b8ef088f195766f454c0e'


def run(args, **kwargs):
    kwargs.setdefault('universal_newlines', True)
    return subprocess.check_output(args, **kwargs)


def git_history(start='HEAD', end=HISTORY_END_COMMIT):
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


def get_history_package_numbers(data, commit, date):
    """Get number of packages for each status.
    """
    result = []
    all_statuses = [
        "blocked", "py3-only", "dropped", "idle", "in-progress",
        "released", "legacy-leaf", "mispackaged"]

    package_numbers = collections.Counter(
        package['status'] for package in data['packages'].values()
    )
    if 'released' not in package_numbers and date < '2018':
        # With data from before April 2017, "released" (dual-support) packages
        # are counted as py3-only
        package_numbers['released'] = package_numbers.pop('py3-only')
    for status in all_statuses:
        row = {
            'commit': commit,
            'date': date,
            'status': status,
            'num_packages': package_numbers.get(status, 0),
        }
        result.append(row)
    return result


def get_history_naming_package_numbers(data, commit, date):
    """Get number of packages for each naming policy violation.
    """
    result = []

    progress = collections.Counter(
        'Misnamed Subpackage' if package['is_misnamed'] else
        'Blocked' if package['blocked_requires'] else
        'Ambiguous Requires' if package['unversioned_requires'] else
        'OK'
        for package in data['packages'].values()
    )
    for status_name in 'Misnamed Subpackage', 'Ambiguous Requires', 'Blocked':
        row = {
            'commit': commit,
            'date': date,
            'status': status_name,
            'num_packages': progress[status_name],
        }
        result.append(row)
    return result


@click.command(help=__doc__)
@click.option('-u', '--update', help='CSV file with existing data')
@click.option('-n', '--naming', is_flag=True,
              help='The CSV file provided is for naming history')
def main(update, naming):
    excluded = set(BAD_COMMITS)
    tmpdir = tempfile.mkdtemp()
    writer = csv.DictWriter(sys.stdout,
                            ['commit', 'date', 'status', 'num_packages'],
                            lineterminator='\n')
    writer.writeheader()

    prev_date = None
    prev_commit = None
    if update:
        with open(update) as f:
            for row in csv.DictReader(f):
                excluded.add(row['commit'])
                prev_date = row['date']
                prev_commit = row['commit']
                writer.writerow(row)

    try:
        tmpclone = os.path.join(tmpdir, 'tmp_clone')
        tmpdata = os.path.join(tmpclone, 'data')
        run(['git', 'clone', '.', tmpclone])
        prev_data_hash = None
        prev_batch = []

        end_commit = HISTORY_NAMING_END_COMMIT if naming else HISTORY_END_COMMIT
        for commit in reversed(git_history(end=end_commit)):
            date = run(['git', 'log', '-n1', '--pretty=%ci', commit]).strip()
            if prev_date and prev_date > date:
                continue
            data_hash = run(['git', 'rev-parse', commit + ':' + 'data'])
            if (commit in excluded) or (data_hash == prev_data_hash):
                prev_data_hash = data_hash
                continue
            if prev_date and prev_date[:11] != date[:11]:
                prev_date = date
                prev_commit = commit
                for row in prev_batch:
                    writer.writerow(row)
            elif not prev_date:
                prev_date = date
            else:
                prev_commit = commit
                print('{},{} - skipping'.format(prev_commit, prev_date),
                      file=sys.stderr)
                continue
            prev_batch = []

            # Note: we don't remove files that didn't exist in the old
            # version.
            run(['git', 'checkout', commit, '--', 'data'], cwd=tmpclone)

            data = get_data(tmpdata)

            if naming:
                prev_batch = get_history_naming_package_numbers(data, commit, date)
            else:
                prev_batch = get_history_package_numbers(data, commit, date)

            prev_data_hash = data_hash
        for row in prev_batch:
            writer.writerow(row)
    finally:
        shutil.rmtree(tmpdir)
    return


if __name__ == '__main__':
    main()
