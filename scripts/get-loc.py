#! /usr/bin/env python3
"""Counts some the relevant metrics for a package

Works on a fedpkg clone, so typical usage is like this:

    fedpkg clone $PACKAGE
    python3 get-loc PACKAGE

but it's really geared towards analyzing more packages at once:

    python3 get-loc PACKAGE1 PACKAGE2 PACKAGE3 ...

And you can update an existing JSON file: stats for new packages will be added,
or overwritten:

    python3 get-loc --update FILE  PACKAGE1 PACKAGE2 PACKAGE3 ...

The script runs `fedpkg prep`, `grep`, and `cloc`, so those commands need to be
installed.
"""

import sys
import subprocess
import csv
import json
import pathlib
import asyncio
import multiprocessing

import click

# Using asyncio to let the child processes run in parallel

@asyncio.coroutine
def get_process_output(args, good_results=(0,)):
    proc = yield from asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE)
    stdout, stderr = yield from proc.communicate()
    if proc.returncode not in good_results:
        raise ValueError('process failed with {}: {}'.format(proc.returncode, args))
    return stdout.decode('utf-8')

@asyncio.coroutine
def read_cloc(args):
    out = (yield from get_process_output(args)).splitlines()
    return list(csv.DictReader(l for l in out if l.strip()))

@asyncio.coroutine
def process_package(directory):
    yield from get_process_output(['fedpkg', '--path', directory, 'prep'])

    total_code_lines = 0
    python_code_lines = 0
    for data in (yield from read_cloc(['cloc', '--csv', '--quiet', directory])):
        total_code_lines += int(data['code'])
        if data['language'].lower() == 'python':
            python_code_lines += int(data['code'])

    out = yield from get_process_output(
        ['grep', '-ril', r'#include.*Python\|PyObject', directory],
        good_results=(0, 1))
    capi_files = list(l.strip() for l in out.splitlines())

    capi_code_lines = 0
    if capi_files:
        for data in (yield from read_cloc(['cloc', '--csv', '--quiet'] + capi_files)):
            if data['language'].lower() in ('c', 'c++', 'c/c++ header'):
                capi_code_lines += int(data['code'])

    spec_filenames = pathlib.Path(directory).glob('*.spec')
    args = ['rpmspec', '--query', '--srpm', '--qf', '%{name} %{version}-%{release}']
    args += [str(s) for s in spec_filenames]
    out = (yield from get_process_output(args)).strip()
    name, version = out.split()

    result = {
        'name': name,
        'version': version,
        'total': total_code_lines,
        'python': python_code_lines,
        'capi': capi_code_lines,
    }

    print(json.dumps(result), file=sys.stderr)
    return result

@asyncio.coroutine
def process_packages(directories, initial=None):
    # maybe asyncio.gather would do here, but let's process the packages
    # alphabetically, "cpu_count()" packages at a time.
    directories = list(directories)
    if initial is None:
        result = {}
    else:
        result = initial

    semaphore = asyncio.Semaphore(multiprocessing.cpu_count()+1)
    running = set()
    done = set()
    futures = []

    for directory in directories:

        @asyncio.coroutine
        def _run(directory):
            running.add(directory)
            try:
                print(
                    '{}/{}; Running: {}'.format(
                        len(done),
                        len(directories),
                        ' '.join(sorted(running))),
                    file=sys.stderr)
                dct = (yield from process_package(directory))
                result[dct['name']] = dct
            except Exception:
                # Quietly ignore packages we can't process :(
                pass
            finally:
                running.remove(directory)
                done.add(directory)
                semaphore.release()

        yield from semaphore.acquire()
        futures.append(asyncio.Task(_run(directory)))

    for fut in futures:
        yield from fut

    return result

@click.command(help=__doc__)
@click.option('-u', '--update', help='JSON file with existing data')
@click.argument('directories', nargs=-1)
def main(update, directories):
    if update:
        with open(update) as f:
            initial = json.load(f)
    else:
        initial = None

    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(process_packages(directories, initial))

    print(json.dumps(result, indent=4, sort_keys=True))

if __name__ == '__main__':
    main()
