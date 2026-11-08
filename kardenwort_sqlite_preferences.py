"""Transactional preference ownership in the daily-use database."""
import json
import sqlite3

from kardenwort_preference_contract import PREFERENCES, configured_preferences, preference_value
from kardenwort_state_contract import client_identity


class PreferenceConflict(ValueError):
    def __init__(self, current):
        super().__init__('Committed preferences changed; reconcile explicitly')
        self.current = current


def read_snapshot(connection):
    rows = connection.execute('SELECT key,value,revision FROM preferences').fetchall()
    values = {key: definition[1] for key, definition in PREFERENCES.items()}
    revisions = dict.fromkeys(values, 0)
    for row in rows:
        values[row['key']] = stored_value(row['key'], row['value'])
        revisions[row['key']] = row['revision']
    return dict(values=values, revisions=revisions)


def stored_value(key, serialized):
    try:
        value = json.loads(serialized)
        normalized = preference_value(key, value)
        if type(value) is not type(normalized) or value != normalized:
            raise ValueError('Stored preference is not typed')
        return value
    except ValueError as error:
        raise sqlite3.DatabaseError('Committed preference is invalid') from error


def require_revision(connection, key, expected):
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError('Invalid preference revision')
    row = connection.execute('SELECT revision FROM preferences WHERE key=?', (key,)).fetchone()
    revision = row['revision'] if row else 0
    if expected is not None and expected != revision:
        raise PreferenceConflict(read_snapshot(connection))


def write_preference(connection, key, value, expected=None):
    require_revision(connection, key, expected)
    connection.execute('INSERT INTO preferences(key,value,revision) VALUES(?,?,1) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value,revision=preferences.revision+1',
        (key, json.dumps(value)))
    row = connection.execute('SELECT revision FROM preferences WHERE key=?', (key,)).fetchone()
    return dict(key=key, value=value, revision=row['revision'])


class PreferencesAdapter:
    def __init__(self, db):
        self.db = db

    def initialize(self, config):
        self.db.run_migrations()
        with self.db.get_connection() as connection:
            marker = connection.execute("SELECT 1 FROM preference_metadata WHERE name='static_import_v1'").fetchone()
            if marker:
                return
            existing = {row[0] for row in connection.execute('SELECT key FROM preferences')}
            values = configured_preferences(config, PREFERENCES.keys()-existing)
            connection.executemany('INSERT OR IGNORE INTO preferences(key,value,revision) VALUES(?,?,0)',
                [(key, json.dumps(value)) for key, value in values.items()])
            connection.execute("INSERT INTO preference_metadata(name,value) VALUES('static_import_v1','complete')")

    def snapshot(self):
        with self.db.get_connection(read_only=True) as connection:
            return read_snapshot(connection)

    def set(self, key, value, expected_revision=None):
        value = preference_value(key, value)
        with self.db.get_connection() as connection:
            result = write_preference(connection, key, value, expected_revision)
            result['snapshot'] = read_snapshot(connection)
        return result

    def get(self, key, default=None):
        with self.db.get_connection(read_only=True) as connection:
            row = connection.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()
        return stored_value(key, row['value']) if row else default

    def import_browser(self, client_id, values, revisions, write_view=None):
        client_id = client_identity(client_id)
        if not isinstance(values, dict) or not isinstance(revisions, dict):
            raise ValueError('Browser preferences and revisions must be objects')
        normalized = {key: preference_value(key, value) for key, value in values.items()}
        with self.db.get_connection() as connection:
            done = connection.execute('SELECT 1 FROM browser_state_imports WHERE client_id=?', (client_id,)).fetchone()
            if done:
                return dict(imported=False, **read_snapshot(connection))
            self._import_values(connection, normalized, revisions)
            if write_view is not None:
                write_view(connection)
            connection.execute('INSERT INTO browser_state_imports(client_id) VALUES(?)', (client_id,))
            result = dict(imported=True, **read_snapshot(connection))
        return result

    def _import_values(self, connection, values, revisions):
        for key, value in values.items():
            if key not in revisions:
                raise ValueError('Browser import requires committed revisions')
            write_preference(connection, key, value, revisions[key])
