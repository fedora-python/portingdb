#! /usr/bin/env python3
"""Compares statuses of packages across two JSON files"""

import json

import blessings
import click


def has_misnamed(rpms):
    for rpm in rpms:
        if rpms[rpm].get('is_misnamed'):
            return True


def compare_statuses(first, second):
    first_status = first.get('status', 'missing')
    second_status = second.get('status', 'missing')

    if first_status != second_status:
        return first_status, second_status


def compare_naming_statuses(first, second):
    first_rpms = first.get('rpms', {})
    second_rpms = second.get('rpms', {})

    def _name_status(rpms):
        if not rpms:
            return 'missing'
        if has_misnamed(rpms):
            return 'misnamed'
        return 'named correctly'

    first_misnamed = _name_status(first_rpms)
    second_misnamed = _name_status(second_rpms)

    if (first_misnamed != second_misnamed and
            # No need to report those, they're fine.
            (first_misnamed, second_misnamed) !=
            ('missing', 'named correctly')):
        return first_misnamed, second_misnamed


def set_requires_state(package, packages_data, requires):
    for pkg in package.get('unversioned_requirers', []):
        require = packages_data.get(pkg, {})
        if not has_misnamed(require.get('rpms', {})):
            status = requires.setdefault(pkg, 'requires misnamed')
            if status != 'requires blocked':
                if has_misnamed(package.get('rpms', {})):
                    requires[pkg] = 'requires blocked'


def compare_requires(first_requires, second_requires,
                     first_data, second_data):
    changes = {}
    for key, first_state in first_requires.items():
        try:
            second_state = second_requires.pop(key)
        except KeyError as err:
            second_state = 'requires ok' if second_data.get(key) else 'missing'
        if first_state != second_state:
            change = (first_state, second_state)
            changes.setdefault(change, []).append(key)

    for key, second_state in second_requires.items():
        first_state = 'requires ok' if first_data.get(key) else 'missing'
        change = (first_state, second_state)
        changes.setdefault(change, []).append(key)

    return changes


def compare_files(first_data, second_data):
    all_keys = set(first_data).union(second_data)

    status_changes = {}
    naming_status_changes = {}

    first_requires = {}
    second_requires = {}

    for key in sorted(all_keys):
        first = first_data.get(key, {})
        second = second_data.get(key, {})

        change = compare_statuses(first, second)
        if change:
            status_changes.setdefault(change, []).append(key)

        change = compare_naming_statuses(first, second)
        if change:
            naming_status_changes.setdefault(change, []).append(key)

        set_requires_state(first, first_data, first_requires)
        set_requires_state(second, second_data, second_requires)

    naming_status_changes.update(
        compare_requires(
            first_requires, second_requires,
            first_data, second_data))

    return status_changes, naming_status_changes


@click.command(help=__doc__)
@click.argument('first_filename', nargs=1)
@click.argument('second_filename', nargs=1)
def main(first_filename, second_filename):
    term = blessings.Terminal()

    with open(first_filename) as f:
        first_data = json.load(f)

    with open(second_filename) as f:
        second_data = json.load(f)

    status_changes, naming_status_changes = compare_files(
        first_data, second_data)

    # Print changes.
    print(term.green('Update Fedora data'))
    print()

    for change, packages in sorted(status_changes.items()):
        print(term.blue('**{}** → **{}** ({})'.format(*change, len(packages))))
        for package in packages:
            print('-', package)
        print()

    print(term.green('## Naming status changes'))
    print()

    for change, packages in sorted(naming_status_changes.items()):
        print(term.blue('**{}** → **{}** ({})'.format(*change, len(packages))))
        for package in packages:
            print('-', package)
        print()


if __name__ == '__main__':
    main()
