"""Compares statuses of packages across two JSON files"""

import sys
import json

import blessings

term = blessings.Terminal()

_, first_filename, second_filename = sys.argv

with open(first_filename) as f:
    first_data = json.load(f)

with open(second_filename) as f:
    second_data = json.load(f)

all_keys = set(first_data).union(second_data)

status_changes = {}

for key in sorted(all_keys):
    first = first_data.get(key, {})
    second = second_data.get(key, {})

    first_status = first.get('status', 'missing')
    second_status = second.get('status', 'missing')

    if first.get('status') != second.get('status'):
        change = first_status, second_status
        status_changes.setdefault(change, []).append(key)

for change, packages in sorted(status_changes.items()):
    print(term.blue('{} -> {} ({})'.format(*change, len(packages))))
    for package in packages:
        print('   ', package)

