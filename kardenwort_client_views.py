"""Client/session view state is independent of shared vocabulary membership."""
import json
import time

from kardenwort_state_contract import client_identity, occurrence_identity


RETENTION_SECONDS = 30 * 24 * 60 * 60
TOUCH_RENEWAL_SECONDS = 3600


def empty_view(browser_imported=None):
    result = dict(active_card=1, active_tokens=[], revision=0, cleared=False)
    return with_import_flag(result, browser_imported)


def with_import_flag(result, imported):
    if imported is not None:
        result['browser_imported'] = imported
    return result


def require_session(connection, zid):
    row = connection.execute('SELECT 1 FROM sessions WHERE zid=? AND deleted_at IS NULL', (zid,)).fetchone()
    if row is None:
        raise ValueError('View session is unavailable')


def session_occurrences(connection, zid):
    rows = connection.execute(
        'SELECT sentence_index, token_order FROM words WHERE session_zid=? '
        'GROUP BY sentence_index, token_order HAVING COUNT(*) = 1',
        (zid,),
    ).fetchall()
    return {(row['sentence_index'], row['token_order']) for row in rows}


def occurrence_exists(connection, zid, item):
    sentence, token = occurrence_identity(item)
    count = connection.execute('SELECT COUNT(*) FROM words WHERE session_zid=? AND sentence_index=? AND token_order=?',
                               (zid, sentence, token)).fetchone()[0]
    return count == 1


def validate_view_inputs(active_card, tokens):
    if type(active_card) is not int or active_card < 1:
        raise ValueError('Invalid active card')
    if not isinstance(tokens, list) or len(tokens) > 10000:
        raise ValueError('Invalid active occurrences')


def checked_view(connection, zid, active_card, tokens):
    require_session(connection, zid)
    validate_view_inputs(active_card, tokens)
    valid = session_occurrences(connection, zid)
    for item in tokens:
        if occurrence_identity(item) not in valid:
            raise ValueError('Focused occurrence is missing or ambiguous')
    return active_card, json.dumps(tokens)


def _require_expected_revision(expected):
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError('Invalid client view revision')


def write_view(connection, zid, client, active_card, tokens, expected=None, now=None):
    client = client_identity(client)
    _require_expected_revision(expected)
    active_card, serialized = checked_view(connection, zid, active_card, tokens)
    row = connection.execute('SELECT revision FROM client_view_state WHERE session_zid=? AND client_id=?', (zid, client)).fetchone()
    revision = row['revision'] if row else 0
    if expected is not None and expected != revision:
        raise ValueError('Client view revision changed')
    stamp = int(time.time() if now is None else now)
    connection.execute('INSERT INTO client_view_state(session_zid,client_id,active_card,active_tokens,revision,touched_at) '
        'VALUES(?,?,?,?,1,?) ON CONFLICT(session_zid,client_id) DO UPDATE SET active_card=excluded.active_card,'
        'active_tokens=excluded.active_tokens,revision=client_view_state.revision+1,touched_at=excluded.touched_at',
        (zid, client, active_card, serialized, stamp))
    return revision + 1


def fetch_browser_imported(connection, client, include_import):
    if not include_import:
        return None
    row = connection.execute('SELECT 1 FROM browser_state_imports WHERE client_id=?', (client,)).fetchone()
    return row is not None


def fetch_view_row(connection, zid, client):
    return connection.execute(
        'SELECT active_card, active_tokens, revision, touched_at '
        'FROM client_view_state WHERE session_zid=? AND client_id=?',
        (zid, client),
    ).fetchone()


def _observed_state(row, now, touch_interval, cleared):
    if cleared or (now - row['touched_at']) >= touch_interval:
        return 'write'
    return 'clean'


def inspect_view(connection, zid, client, now, touch_interval, include_import=False):
    require_session(connection, zid)
    imported = fetch_browser_imported(connection, client, include_import)
    row = fetch_view_row(connection, zid, client)
    if row is None or row['touched_at'] < now - RETENTION_SECONDS:
        state = 'missing' if row is None else 'expired'
        return dict(state=state, row=row, result=empty_view(imported), valid=[])
    valid = session_occurrences(connection, zid)
    tokens = json.loads(row['active_tokens'])
    kept = [item for item in tokens if occurrence_identity(item) in valid]
    cleared = len(kept) != len(tokens)
    result = with_import_flag(dict(active_card=row['active_card'], active_tokens=kept,
                                   revision=row['revision'] + int(cleared), cleared=cleared), imported)
    return dict(state=_observed_state(row, now, touch_interval, cleared), row=row, result=result, valid=kept)


def read_view(connection, zid, client):
    now = int(time.time())
    return _maintain_view(connection, zid, client, now, TOUCH_RENEWAL_SECONDS, False)


def _maintain_view(connection, zid, client, now, touch_interval, include_import):
    observed = inspect_view(connection, zid, client, now, touch_interval, include_import)
    state = observed['state']
    if state in ('missing', 'clean'):
        return observed['result']
    if state == 'expired':
        connection.execute('DELETE FROM client_view_state WHERE touched_at < ?', (now - RETENTION_SECONDS,))
        return observed['result']
    if observed['result']['cleared']:
        connection.execute(
            'UPDATE client_view_state SET active_tokens=?, revision=revision+1, touched_at=? '
            'WHERE session_zid=? AND client_id=?',
            (json.dumps(observed['valid']), now, zid, client),
        )
    else:
        connection.execute(
            'UPDATE client_view_state SET touched_at=? WHERE session_zid=? AND client_id=?',
            (now, zid, client),
        )
    return observed['result']


class ClientViewAdapter:
    def __init__(self, db, *, clock=time.time, touch_interval=TOUCH_RENEWAL_SECONDS):
        self.db = db
        self.clock = clock
        self.touch_interval = touch_interval

    def _now(self):
        return int(self.clock())

    def get(self, zid, client, *, include_import=False):
        client = client_identity(client)
        now = self._now()
        with self.db.get_connection(read_only=True) as connection:
            observed = inspect_view(connection, zid, client, now, self.touch_interval, include_import)
        if observed['state'] in ('missing', 'clean'):
            return observed['result']
        now = self._now()
        with self.db.get_connection() as connection:
            return _maintain_view(connection, zid, client, now, self.touch_interval, include_import)

    def set(self, zid, client, active_card, tokens, expected_revision=None, *, include_import=False):
        client = client_identity(client)
        now = self._now()
        with self.db.get_connection() as connection:
            require_session(connection, zid)
            connection.execute('DELETE FROM client_view_state WHERE touched_at < ?', (now - RETENTION_SECONDS,))
            revision = write_view(connection, zid, client, active_card, tokens, expected_revision, now=now)
            imported = fetch_browser_imported(connection, client, include_import)
        return with_import_flag(dict(active_card=active_card, active_tokens=list(tokens),
                                     revision=revision, cleared=False), imported)

    def dispose(self, zid, client):
        client = client_identity(client)
        with self.db.get_connection() as connection:
            require_session(connection, zid)
            connection.execute('DELETE FROM client_view_state WHERE session_zid=? AND client_id=?', (zid, client))
