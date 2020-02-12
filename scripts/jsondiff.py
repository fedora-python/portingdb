#! /usr/bin/env python3
"""Compares statuses of packages across two JSON files"""

import json

import blessings
import click


def compare_statuses(first, second):
    first_status = first.get('status', 'missing')
    second_status = second.get('status', 'missing')

    if first_status != second_status:
        return first_status, second_status


def compare_files(first_data, second_data):
    all_keys = set(first_data).union(second_data)

    status_changes = {}

    for key in sorted(all_keys):
        first = first_data.get(key, {})
        second = second_data.get(key, {})

        change = compare_statuses(first, second)
        if change:
            status_changes.setdefault(change, []).append(key)

    return status_changes


@click.command(help=__doc__)
@click.argument('first_filename', nargs=1)
@click.argument('second_filename', nargs=1)
def main(first_filename, second_filename):
    term = blessings.Terminal()

    with open(first_filename) as f:
        first_data = json.load(f)

    with open(second_filename) as f:
        second_data = json.load(f)

    status_changes = compare_files(first_data, second_data)

    # Print changes.
    print(term.green('Update Fedora data'))
    print(term.green('=================='))
    print()

    for change, packages in sorted(status_changes.items()):
        change_from, change_to = change
        if (
            change_from in {'idle', 'mispackaged'}
            and change_to in {'released', 'legacy-leaf', 'py3-only'}
        ):
            badge_marker = '♥'
        else:
            badge_marker = ''
        print(term.blue('**{}** → **{}** ({}) {}'.format(
            *change, len(packages), badge_marker,
        )))
        for package in packages:
            print('-', package)
        print()


if __name__ == '__main__':
    main()
