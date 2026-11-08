"""Client/session view state is independent of shared vocabulary membership."""
import json
import time

from kardenwort_state_contract import client_identity, occurrence_identity


RETENTION_SECONDS = 30*24*60*60


def require_session(connection, zid):
    row = connection.execute('SELECT 1 FROM sessions WHERE zid=? AND deleted_at IS NULL', (zid,)).fetchone()
    if row is None:
        raise ValueError('View session is unavailable')


def occurrence_exists(connection, zid, item):
    sentence, token = occurrence_identity(item)
    count = connection.execute('SELECT COUNT(*) FROM words WHERE session_zid=? AND sentence_index=? AND token_order=?',
                               (zid, sentence, token)).fetchone()[0]
    return count == 1


def checked_view(connection, zid, active_card, tokens):
    require_session(connection, zid)
    if type(active_card) is not int or active_card < 1:
        raise ValueError('Invalid active card')
    if not isinstance(tokens, list) or len(tokens) > 10000:
        raise ValueError('Invalid active occurrences')
    for item in tokens:
        if not occurrence_exists(connection, zid, item):
            raise ValueError('Focused occurrence is missing or ambiguous')
    return active_card, json.dumps(tokens)


def write_view(connection, zid, client, active_card, tokens, expected=None):
    client = client_identity(client)
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError('Invalid client view revision')
    active_card, serialized = checked_view(connection, zid, active_card, tokens)
    row = connection.execute('SELECT revision FROM client_view_state WHERE session_zid=? AND client_id=?', (zid, client)).fetchone()
    revision = row['revision'] if row else 0
    if expected is not None and expected != revision:
        raise ValueError('Client view revision changed')
    connection.execute('INSERT INTO client_view_state(session_zid,client_id,active_card,active_tokens,revision,touched_at) '
        'VALUES(?,?,?,?,1,?) ON CONFLICT(session_zid,client_id) DO UPDATE SET active_card=excluded.active_card,'
        'active_tokens=excluded.active_tokens,revision=client_view_state.revision+1,touched_at=excluded.touched_at',
        (zid, client, active_card, serialized, int(time.time())))


def read_view(connection, zid, client):
    require_session(connection, zid)
    row = connection.execute('SELECT active_card,active_tokens,revision FROM client_view_state WHERE session_zid=? AND client_id=?',
                             (zid, client)).fetchone()
    if row is None:
        return dict(active_card=1, active_tokens=[], revision=0, cleared=False)
    tokens = json.loads(row['active_tokens'])
    valid = [item for item in tokens if occurrence_exists(connection, zid, item)]
    cleared = len(valid) != len(tokens)
    if cleared:
        write_view(connection, zid, client, row['active_card'], valid, row['revision'])
    return dict(active_card=row['active_card'], active_tokens=valid,
                revision=row['revision']+int(cleared), cleared=cleared)


class ClientViewAdapter:
    def __init__(self, db):
        self.db = db

    def get(self, zid, client):
        client = client_identity(client)
        with self.db.get_connection() as connection:
            connection.execute('DELETE FROM client_view_state WHERE touched_at < ?', (int(time.time())-RETENTION_SECONDS,))
            result = read_view(connection, zid, client)
            connection.execute('UPDATE client_view_state SET touched_at=? WHERE session_zid=? AND client_id=?',
                               (int(time.time()), zid, client))
        return result

    def set(self, zid, client, active_card, tokens, expected_revision=None):
        with self.db.get_connection() as connection:
            write_view(connection, zid, client, active_card, tokens, expected_revision)
            result = read_view(connection, zid, client)
        return result

    def dispose(self, zid, client):
        client = client_identity(client)
        with self.db.get_connection() as connection:
            require_session(connection, zid)
            connection.execute('DELETE FROM client_view_state WHERE session_zid=? AND client_id=?', (zid, client))
