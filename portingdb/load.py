import os
import json

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.expression import select, and_

from . import tables
from . import queries

try:
    SafeLoader = yaml.CSafeLoader
except AttributeError:
    SafeLoader = yaml.SafeLoader


def get_db(directory, engine=None):
    if engine is None:
        engine = create_engine('sqlite://')
    tables.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    if directory is not None:
        load_from_directory(db, directory)
    return db


def data_from_file(directory, basename):
    for ext in '.yaml', '.json':
        filename = os.path.join(directory, basename + ext)
        if os.path.exists(filename):
            return decode_file(filename)
    raise FileNotFoundError(filename)


def decode_file(filename):
    with open(filename) as f:
        if filename.endswith('.json'):
            return json.load(f)
        else:
            return yaml.load(f, Loader=SafeLoader)


def _get_pkg(name, collection, info):
    return {
        'package_name': name,
        'collection_ident': collection,
        'name': info.get('aka') or name,
        'status': info.get('status') or 'unknown',
        'priority': info.get('priority') or 'unknown',
        'deadline': info.get('deadline', None),
        'note': info.get('note', None),
        'nonblocking': info.get('nonblocking', False),
    }


def _get_repolink(name, col_package_map, collection, info, field_name, type_name):
    url = info.get(field_name)
    if url is None:
        return None
    return {
        'collection_package_id': col_package_map[name, collection],
        'url': url,
        'type': type_name,
    }

def _add_order(rows):
    for i, row in enumerate(rows):
        row['order'] = i
    return rows

def _prepare_enum(rows):
    _add_order(rows)
    for row in rows:
        row['term'] = row['term'].replace('\\e', '\x1b')
    return rows


def _merge_updates(base, updates):
    for key, new_value in updates.items():
        if (key in base and
                isinstance(base[key], dict) and
                isinstance(new_value, dict)):
            _merge_updates(base[key], new_value)
        else:
            base[key] = new_value


def _strip_key(values, key):
    def gen():
        for value in values:
            value = dict(value)
            del value[key]
            yield value
    return list(gen())


def load_from_directory(db, directory):
    """Add data from a directory to a database
    """
    values = _prepare_enum(data_from_file(directory, 'statuses'))
    bulk_load(db, values, tables.Status.__table__, id_column="ident")

    values = _prepare_enum(data_from_file(directory, 'priorities'))
    bulk_load(db, values, tables.Priority.__table__, id_column="ident")

    col_values = _add_order(data_from_file(directory, 'collections'))
    values = _strip_key(col_values, 'statuses')
    col_map = bulk_load(db, values, tables.Collection.__table__, id_column="ident")

    values = [{
        'collection_ident': c['ident'],
        'status': s,
        'description': d,
    } for c in col_values for s, d in c['statuses'].items()]
    bulk_load(db, values, tables.CollectionStatus.__table__,
              key_columns=["collection_ident", "status"])

    for collection in col_map.values():
        package_infos = data_from_file(directory, collection)
        try:
            more_infos = data_from_file(directory, collection + '-update')
        except FileNotFoundError:
            pass
        else:
            _merge_updates(package_infos, more_infos)

        # Base packages
        values = [{
            'name': k,
            'status': 'unknown',
        } for k, v in package_infos.items()]
        bulk_load(db, values, tables.Package.__table__, id_column="name")

        # Dependencies
        values = [{'requirer_name': a, 'requirement_name': b}
                  for a, v in package_infos.items() for b in v.get('deps', ())
                  if a != b]
        bulk_load(db, values, tables.Dependency.__table__,
                  key_columns=['requirer_name', 'requirement_name'])

        # CollectionPackages
        values = [_get_pkg(k, collection, v) for k, v in package_infos.items()]
        col_package_map = bulk_load(
            db, values, tables.CollectionPackage.__table__,
            key_columns=["package_name", "collection_ident"])

        # Repo links
        values = []
        values.extend(
            [_get_repolink(k, col_package_map, collection, v, 'link_to_repo', 'repo')
            for k, v in package_infos.items()])
        values.extend(
            [_get_repolink(k, col_package_map, collection, v, 'link_to_bug', 'bug')
            for k, v in package_infos.items()])
        values.extend({
            'collection_package_id': col_package_map[n, collection],
            'url': v,
            'type': k,
        } for n, m in package_infos.items() for k, v in m.get('links', {}).items())
        values = [v for v in values if v]
        bulk_load(db, values, tables.Link.__table__,
                  key_columns=['collection_package_id', 'url'])

        # RPMs
        values = [{'collection_package_id': col_package_map[k, collection],
                   'rpm_name': n}
                  for k, v in package_infos.items() for n in v.get('rpms', ())]
        rpm_ids = bulk_load(db, values, tables.RPM.__table__,
                  key_columns=['collection_package_id', 'rpm_name'])

        # PyDependencies
        values = [(('name', n), ('py_version', p))
                  for k, v in package_infos.items()
                  for r in v.get('rpms', {}).values()
                  for n, p in r.items()]
        values = list(dict(p) for p in set(values))
        bulk_load(db, values, tables.PyDependency.__table__,
                  id_column='name')

        # RPMPyDependencies
        values = [{'rpm_id': rpm_ids[col_package_map[k, collection], rn],
                   'py_dependency_name': n}
                  for k, v in package_infos.items()
                  for rn, r in v.get('rpms', {}).items()
                  for n in r]
        bulk_load(db, values, tables.RPMPyDependency.__table__,
                  key_columns=['rpm_id', 'py_dependency_name'])

        # TODO: Contacts

    group_values = data_from_file(directory, 'groups')
    values = [{
        'ident': k,
        'name': v['name'],
    } for k, v in group_values.items()]
    bulk_load(db, values, tables.Group.__table__, id_column='ident')

    values = [{
        'group_ident': k,
        'package_name': p,
        'is_seed': True,
    } for k, v in group_values.items() for p in v['packages']]
    bulk_load(db, values, tables.GroupPackage.__table__,
              key_columns=['group_ident', 'package_name'])

    queries.update_status_summaries(db)
    queries.update_group_closures(db)
    db.commit()


def _get_idmap(rows, key_columns, id_column, keys):
    key_col_count = len(key_columns)

    if id_column in key_columns:
        key_col_count = len(key_columns)
        idx = key_columns.index(id_column)
        kv = ((tuple(row[:key_col_count]), row[idx]) for row in rows)
    else:
        kv = ((tuple(row[:key_col_count]), row[key_col_count]) for row in rows)
    return {k: v for k, v in kv if k in keys}


def _yaml_dump(obj):
    return yaml.safe_dump(obj, default_flow_style=False, allow_unicode=True)


def _check_entry(expected, got, ignored_keys=()):
    for key in (expected.keys() | got.keys()).difference(ignored_keys):
        if expected[key] != got[key]:
            print(_yaml_dump(expected))
            print(_yaml_dump(got))
            raise ValueError('Attempting to overwrite existing entry')


MAX_ADD_COUNT = 500

def bulk_load(db, sources, table, key_columns=None, id_column='id',
              no_existing=False, ignored_columns=(), initial=False):
    """Load data into the database

    :param db: The SQLAlchemy session
    :param sources: A list of dictionaries containing the values to insert
    :param table: The SQLAlchemy table to operate on
    :param key_columns: Names of unique-key columns. If None, [id_column] is used
    :param id_column: Name of the surrogate primary key column
    :param no_existing: If true, disallow duplicates of entries already in the DB
    :param ignored_columns: Columns ignored in duplicate checking

    Returns an ID map: a dictionary of keys (values from columns given by
    key_columns) to IDs.
    """
    if len(sources) > MAX_ADD_COUNT:
        id_map = {}
        sources = list(sources)
        while sources:
            now, sources = sources[:MAX_ADD_COUNT], sources[MAX_ADD_COUNT:]
            id_map.update(bulk_load(
                db, now, table, key_columns=key_columns,
                id_column=id_column, no_existing=no_existing,
                ignored_columns=ignored_columns))
        return id_map

    if key_columns is None:
        key_columns = [id_column]
    if id_column in key_columns:
        id_columns = key_columns
    else:
        id_columns = key_columns + [id_column]

    # Get a dict of key -> source, while checking that sources with duplicate
    # keys also have duplicate data

    source_dict = {}
    for source in sources:
        key = tuple(source[k] for k in key_columns)
        if key in source_dict:
            _check_entry(source_dict[key], source, ignored_columns)
        else:
            source_dict[key] = source
    if not source_dict:
        return
    keys = set(source_dict)

    # List of column objects (key + id)
    col_list = [table.c[k] for k in id_columns]

    def get_whereclause(keys):
        """WHERE clause that selects all given keys
        (may give some extra ones)
        """
        if len(key_columns) == 1:
            return table.c[key_columns[0]].in_(k for [k] in keys)
        else:
            return and_(table.c[c].in_(set(k[i] for k in keys))
                        for i, c in enumerate(key_columns))

    if initial:
        id_map = {}
    else:
        # Non-key & non-id column names
        check_columns = [c for c in sources[0] if c not in key_columns]

        # Get existing entries, construct initial ID map
        sel = select(col_list + [table.c[k] for k in check_columns],
                     whereclause=get_whereclause(keys))
        existing_rows = list(db.execute(sel))
        if id_column in key_columns:
            key_col_count = len(key_columns)
            idx = key_columns.index(id_column)
            id_map = {tuple(k[:key_col_count]): k[key_col_count-1]
                      for k in existing_rows}
        else:
            id_map = _get_idmap(existing_rows, key_columns, id_column, keys)

        # Check existing entries are OK
        if no_existing and id_map:
            raise ValueError('Attempting to overwrite existing entry')
        if check_columns:
            for row in existing_rows:
                key = tuple(row[:len(key_columns)])
                source = source_dict[key]
                for n, v in zip(check_columns, row[len(id_columns):]):
                    if n not in ignored_columns:
                        if source[n] != v:
                            raise ValueError('Attempting to overwrite existing entry: %s[%s]; %r!=%r' % (table, source, source[n], v))

    # Insert the missing rows into the DB; then select them back to read the IDs
    values = []
    missing = set()
    for key, source in source_dict.items():
        if key not in id_map and key not in missing:
            values.append(source)
            missing.add(key)
    if values:
        db.execute(table.insert(), values)
        if id_column in key_columns:
            rows = keys
        else:
            sel = select(col_list, whereclause=get_whereclause(missing))
            rows = db.execute(sel)
        id_map.update(_get_idmap(rows, key_columns, id_column, keys))

    return id_map
