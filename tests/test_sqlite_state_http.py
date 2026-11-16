"""Real state commands preserve configuration bytes and relational ownership."""
import sqlite3

import pytest

import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from kardenwort_state_runtime import preference_store
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_release_boundary_contracts import post, data


ZID = '20261108202000'


def snapshot(url):
    from urllib.request import urlopen
    import json
    with urlopen(url+'/api/v1/config', timeout=5) as response:
        return data(json.load(response))['preferences']


def seed(server):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    adapter.save_session(session_zid=ZID, source_language='de', source_raw_text='Haus.',
        headers=['WordSource', 'TokenOrder', 'SentenceSourceIndex'], data_rows=[['Haus', '0', '1']])
    return adapter


def test_preference_commit_revision_restart_and_ini_integrity(preference_world):
    url, server, path = preference_world
    before = path.read_bytes(), path.stat().st_mtime_ns
    initial = snapshot(url)
    status, result = post(url, '/api/v1/config', dict(filter_selected_only=True,
        expected_revision=initial['revisions']['filter_selected_only']))
    assert status == 200, result
    assert data(result)['committed_value'] is True
    status, conflict = post(url, '/api/v1/config', dict(filter_selected_only=False,
        expected_revision=initial['revisions']['filter_selected_only']))
    assert status == 409, conflict
    assert data(conflict)['current']['values']['filter_selected_only'] is True
    server.config._preference_store = None
    server.config.set('rendering', 'filter_selected_only', 'false')
    assert snapshot(url)['values']['filter_selected_only'] is True
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_busy_write_returns_error_without_success_event(preference_world):
    url, server, path = preference_world
    initial = snapshot(url)
    store = preference_store(server.config, server.resolved_paths)
    store.db.busy_timeout_ms = 10
    events = EventDispatcher.get_instance()
    queue = events.register(ZID)
    blocker = sqlite3.connect(store.db.db_path, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        status, result = post(url, '/api/v1/config', {'filter_selected_only': True})
        assert status == 500, result
        assert queue.empty()
        assert snapshot(url) == initial
    finally:
        blocker.rollback(); blocker.close(); events.unregister(ZID, queue)


def test_client_focus_is_isolated_and_does_not_change_membership(preference_world):
    url, server, path = preference_world
    adapter = seed(server)
    snapshot(url)
    identity = dict(session_zid=ZID, client_id='a'*32)
    focus = dict(sentence_idx=1, token_order=0, visual_idx=1, atomic_id=0, row_ids=[0], lemma='Haus')
    status, result = post(url, '/api/v1/client-view', dict(**identity, active_card=2, active_tokens=[focus]))
    assert status == 200, result
    status, other = post(url, '/api/v1/client-view', dict(session_zid=ZID, client_id='b'*32))
    assert status == 200 and data(other)['active_tokens'] == []
    assert adapter.get_shared_selection(ZID)['haus'] is False
    status, invalid = post(url, '/api/v1/client-view', dict(**identity, active_card=2,
        active_tokens=[dict(sentence_idx=1, token_order=999)]))
    assert status == 400, invalid
    status, current = post(url, '/api/v1/client-view', identity)
    assert data(current)['active_tokens'] == [focus]
    with adapter.db.get_connection() as connection:
        connection.execute('DELETE FROM sessions WHERE zid=?', (ZID,))
        assert connection.execute('SELECT COUNT(*) FROM client_view_state').fetchone()[0] == 0


def test_browser_import_is_atomic_one_time_and_conflict_checked(preference_world):
    url, server, path = preference_world
    seed(server)
    initial = snapshot(url)
    body = dict(session_zid=ZID, client_id='a'*32, preferences={'filter_selected_only': True},
        revisions=initial['revisions'], view=dict(active_card=2, active_tokens=[dict(sentence_idx=1, token_order=0)]))
    status, result = post(url, '/api/v1/browser-state/import', body)
    assert status == 200 and data(result)['imported'] is True, result
    body['preferences']['filter_selected_only'] = False
    status, result = post(url, '/api/v1/browser-state/import', body)
    assert status == 200 and data(result)['imported'] is False
    assert snapshot(url)['values']['filter_selected_only'] is True
    body['client_id'] = 'b'*32
    status, result = post(url, '/api/v1/browser-state/import', body)
    assert status == 409, result
    store = preference_store(server.config, server.resolved_paths)
    with store.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM browser_state_imports').fetchone()[0] == 1


def test_private_view_and_import_require_authentication(preference_world):
    url, server, path = preference_world
    for route in ('/api/v1/client-view', '/api/v1/browser-state/import'):
        status, result = post(url, route, {}, token='invalid')
        assert status == 403, result


def test_view_expiry_disposal_and_stale_coordinates_preserve_membership(preference_world):
    import time
    from kardenwort_client_views import RETENTION_SECONDS
    url, server, path = preference_world
    adapter = seed(server)
    snapshot(url)
    focus = [dict(sentence_idx=1, token_order=0)]
    def identity(client):
        return dict(session_zid=ZID, client_id=client*32)
    for client in 'abc':
        assert post(url, '/api/v1/client-view', dict(**identity(client), active_card=2, active_tokens=focus))[0] == 200
    with adapter.db.get_connection() as connection:
        connection.execute('UPDATE client_view_state SET touched_at=? WHERE client_id=?', (int(time.time())-RETENTION_SECONDS-1, 'a'*32))
        connection.execute('UPDATE client_view_state SET touched_at=? WHERE client_id=?', (int(time.time())-RETENTION_SECONDS+3600, 'b'*32))
    assert data(post(url, '/api/v1/client-view', identity('a'))[1])['active_tokens'] == []
    assert data(post(url, '/api/v1/client-view', identity('b'))[1])['active_tokens'] == focus
    with adapter.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT touched_at FROM client_view_state WHERE client_id=?', ('b'*32,)).fetchone()[0] > int(time.time())-5
    assert post(url, '/api/v1/client-view', dict(**identity('c'), dispose=True))[0] == 200
    assert data(post(url, '/api/v1/client-view', identity('c'))[1])['active_tokens'] == []
    with adapter.db.get_connection() as connection:
        connection.execute('DELETE FROM words WHERE session_zid=?', (ZID,))
    cleared = data(post(url, '/api/v1/client-view', identity('b'))[1])
    assert cleared['cleared'] is True and cleared['active_tokens'] == []
    assert adapter.get_shared_selection(ZID) == {}


def test_language_failure_emits_no_event_or_host_effect_and_keeps_retryable_draft(preference_world, monkeypatch):
    import kardenwort_controller as controller
    url, server, path = preference_world
    initial = snapshot(url)
    store = preference_store(server.config, server.resolved_paths)
    store.db.busy_timeout_ms = 10
    host_calls = []
    monkeypatch.setattr(controller, 'spawn_ahk', lambda *args, **kwargs: host_calls.append(args))
    queue = EventDispatcher.get_instance().register(ZID)
    blocker = sqlite3.connect(store.db.db_path, isolation_level=None)
    draft = dict(text='Owned text', language='de', text_mode='single')
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[ZID] = draft
    try:
        blocker.execute('BEGIN IMMEDIATE')
        assert post(url, '/api/v1/set-language', {'language': 'fr'})[0] == 500
        assert post(url, '/api/v1/confirm-language', dict(session_zid=ZID, action='process', chosen_source_lang='fr'))[0] == 500
        assert not host_calls and queue.empty()
        assert snapshot(url) == initial
        with controller._DRAFT_SESSIONS_LOCK:
            assert controller._DRAFT_SESSIONS[ZID] == draft
    finally:
        blocker.rollback(); blocker.close()
        EventDispatcher.get_instance().unregister(ZID, queue)
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(ZID, None)
    assert post(url, '/api/v1/set-language', {'language': 'fr'})[0] == 200
    assert store.get('default_language') == 'fr' and len(host_calls) == 1


@pytest.mark.parametrize('value', [-1, True, '0', 0.0])
def test_invalid_revisions_do_not_mutate_preferences(preference_world, value):
    url, server, path = preference_world
    initial = snapshot(url)
    assert post(url, '/api/v1/config', dict(filter_selected_only=True, expected_revision=value))[0] == 400
    assert snapshot(url) == initial


def test_corrupt_committed_value_is_an_error_instead_of_a_default(preference_world):
    from urllib.request import urlopen
    from urllib.error import HTTPError
    url, server, path = preference_world
    snapshot(url)
    store = preference_store(server.config, server.resolved_paths)
    with store.db.get_connection() as connection:
        connection.execute("UPDATE preferences SET value='invalid' WHERE key='theme'")
    with pytest.raises(HTTPError) as error:
        urlopen(url+'/api/v1/config', timeout=5)
    assert error.value.code == 500
    assert post(url, '/api/v1/config', {'filter_selected_only': True})[0] == 500
    assert store.get('filter_selected_only') is False


@pytest.mark.parametrize('kind', ['missing', 'directory'])
def test_missing_static_file_does_not_reactivate_file_persistence(preference_world, kind):
    url, server, path = preference_world
    snapshot(url)
    path.unlink()
    if kind == 'directory':
        path.mkdir()
    assert post(url, '/api/v1/config', {'filter_selected_only': True})[0] == 200
    assert snapshot(url)['values']['filter_selected_only'] is True
    assert path.is_dir() if kind == 'directory' else not path.exists()


@pytest.mark.parametrize('batch', [False, True])
def test_soft_delete_discards_client_focus_in_the_same_transaction(preference_world, batch):
    from kardenwort_client_views import ClientViewAdapter
    url, server, path = preference_world
    adapter = seed(server)
    snapshot(url)
    views = ClientViewAdapter(adapter.db)
    views.set(ZID, 'a'*32, 2, [dict(sentence_idx=1, token_order=0)])
    if batch:
        assert adapter.db.soft_delete_sessions_batch(session_zids=[ZID]) == 1
    else:
        assert adapter.db.soft_delete_session(ZID) is True
    with adapter.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM client_view_state').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM preferences').fetchone()[0] == 8
    adapter.db.restore_session(ZID)
    assert views.get(ZID, 'a'*32)['active_tokens'] == []


def test_success_event_is_a_committed_readable_snapshot(preference_world):
    url, server, path = preference_world
    snapshot(url)
    events = EventDispatcher.get_instance()
    queue = events.register(ZID)
    try:
        status, result = post(url, '/api/v1/config', {'filter_selected_only': True})
        assert status == 200
        event = queue.get(timeout=2)
        assert event['type'] == 'preferences_updated'
        assert event['preferences'] == snapshot(url)
        assert event['preferences']['revisions']['filter_selected_only'] == data(result)['revision']
    finally:
        events.unregister(ZID, queue)


def test_canonical_render_uses_committed_theme_and_zoom_without_rewriting_ini(preference_world):
    from urllib.request import Request, urlopen
    url, server, path = preference_world
    seed(server)
    before = path.read_bytes(), path.stat().st_mtime_ns
    for section, key, value in [('ui', 'theme', 'light'), ('rendering', 'default_zoom', 125)]:
        assert post(url, '/api/v1/config', dict(section=section, key=key, value=value))[0] == 200
    request = Request(url+'/?session_zid='+ZID+'&seq_num=1', headers={'X-API-Token': server.api_key})
    with urlopen(request, timeout=10) as response:
        html = response.read().decode()
    assert 'theme-light' in html.split('<body', 1)[1].split('>', 1)[0]
    assert 'zoom: 125%' in html
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_concurrent_preference_revisions_and_view_updates(preference_world):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from urllib.request import Request, urlopen

    url, server, path = preference_world
    seed(server)
    before = path.read_bytes(), path.stat().st_mtime_ns
    initial = snapshot(url)
    expected_rev = initial['revisions']['filter_selected_only']
    barrier = threading.Barrier(2)

    def race_pref(target_val):
        barrier.wait(timeout=5)
        return post(url, '/api/v1/config', dict(filter_selected_only=target_val, expected_revision=expected_rev))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(race_pref, True)
        f2 = pool.submit(race_pref, False)
        r1 = f1.result(timeout=10)
        r2 = f2.result(timeout=10)

    statuses = sorted([r1[0], r2[0]])
    assert statuses == [200, 409], (r1, r2)
    winner_val = True if r1[0] == 200 else False
    assert snapshot(url)['values']['filter_selected_only'] is winner_val

    view_barrier = threading.Barrier(3)

    def write_view(client_char, card):
        view_barrier.wait(timeout=5)
        return post(url, '/api/v1/client-view', dict(
            session_zid=ZID,
            client_id=client_char * 32,
            active_card=card,
            active_tokens=[dict(sentence_idx=1, token_order=0)],
        ))

    def read_render():
        view_barrier.wait(timeout=5)
        req = Request(url + '/?session_zid=' + ZID + '&seq_num=1', headers={'X-API-Token': server.api_key})
        with urlopen(req, timeout=10) as resp:
            return resp.status

    with ThreadPoolExecutor(max_workers=3) as pool:
        vc1 = pool.submit(write_view, 'd', 1)
        vc2 = pool.submit(write_view, 'e', 1)
        vr = pool.submit(read_render)
        assert vc1.result(timeout=10)[0] == 200
        assert vc2.result(timeout=10)[0] == 200
        assert vr.result(timeout=10) == 200

    assert data(post(url, '/api/v1/client-view', dict(session_zid=ZID, client_id='d' * 32))[1])['active_card'] == 1
    assert data(post(url, '/api/v1/client-view', dict(session_zid=ZID, client_id='e' * 32))[1])['active_card'] == 1
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_clean_view_read_uses_single_readonly_connection_and_zero_writes(preference_world):
    from kardenwort_client_views import ClientViewAdapter
    from kardenwort_state_runtime import preference_store
    url, server, _ = preference_world
    adapter = seed(server)
    store = preference_store(server.config, server.resolved_paths)
    client = 'f' * 32
    focus = [dict(sentence_idx=1, token_order=0)]
    views = ClientViewAdapter(store.db)
    views.set(ZID, client, 2, focus)

    modes = []
    statements = []
    real_get_connection = store.db.get_connection

    def traced_connection(*args, **kwargs):
        modes.append(bool(kwargs.get('read_only', False)))
        cm = real_get_connection(*args, **kwargs)
        orig_enter = cm.__enter__

        def tracked_enter():
            conn = orig_enter()
            conn.set_trace_callback(statements.append)
            return conn

        cm.__enter__ = tracked_enter
        return cm

    store.db.get_connection = traced_connection
    try:
        res_status, res_body = post(url, '/api/v1/client-view', dict(session_zid=ZID, client_id=client))
        assert res_status == 200
        payload = data(res_body)
        assert payload['active_card'] == 2 and payload['active_tokens'] == focus
        assert payload['revision'] == 1 and payload['browser_imported'] is False
        assert modes == [True]
        assert not any(stmt.lstrip().upper().startswith(('BEGIN IMMEDIATE', 'INSERT', 'UPDATE', 'DELETE')) for stmt in statements), statements
        assert len([stmt for stmt in statements if stmt.lstrip().upper().startswith('SELECT')]) <= 5, statements
    finally:
        store.db.get_connection = real_get_connection


def test_controlled_clock_touch_renewal_stale_repair_and_expiry(preference_world):
    from kardenwort_client_views import ClientViewAdapter, RETENTION_SECONDS, TOUCH_RENEWAL_SECONDS
    _, server, _ = preference_world
    adapter = seed(server)
    now = [1_700_000_000]
    views = ClientViewAdapter(adapter.db, clock=lambda: now[0])
    client = 'e' * 32
    focus = [dict(sentence_idx=1, token_order=0)]

    created = views.set(ZID, client, 2, focus)
    assert created['revision'] == 1

    now[0] += TOUCH_RENEWAL_SECONDS - 1
    assert views.get(ZID, client)['revision'] == 1
    with adapter.db.get_connection(read_only=True) as conn:
        assert conn.execute('SELECT touched_at FROM client_view_state WHERE client_id=?', (client,)).fetchone()[0] == 1_700_000_000

    now[0] += 1
    renewed = views.get(ZID, client)
    assert renewed['revision'] == 1 and renewed['cleared'] is False
    with adapter.db.get_connection(read_only=True) as conn:
        assert conn.execute('SELECT touched_at FROM client_view_state WHERE client_id=?', (client,)).fetchone()[0] == now[0]

    with adapter.db.get_connection() as conn:
        conn.execute('DELETE FROM words WHERE session_zid=? AND sentence_index=1 AND token_order=0', (ZID,))
    repaired = views.get(ZID, client)
    assert repaired['cleared'] is True and repaired['active_tokens'] == [] and repaired['revision'] == 2

    now[0] += RETENTION_SECONDS + 1
    expired = views.get(ZID, client)
    assert expired == dict(active_card=1, active_tokens=[], revision=0, cleared=False)


def test_view_writer_escalation_revalidates_against_dispose_and_set_races(preference_world):
    import kardenwort_client_views as cv
    _, server, _ = preference_world
    adapter = seed(server)
    now = [1_700_000_000]
    views = cv.ClientViewAdapter(adapter.db, clock=lambda: now[0])
    client = 'c' * 32
    focus = [dict(sentence_idx=1, token_order=0)]
    views.set(ZID, client, 1, focus)

    now[0] += cv.TOUCH_RENEWAL_SECONDS + 10
    real_inspect = cv.inspect_view
    action = []

    def racing_inspect(*args, **kwargs):
        result = real_inspect(*args, **kwargs)
        if action:
            action.pop(0)()
        return result

    orig_inspect = cv.inspect_view
    cv.inspect_view = racing_inspect
    try:
        # Race 1: dispose happens right after read observation, before writer escalation.
        action.append(lambda: views.dispose(ZID, client))
        disposed = views.get(ZID, client)
        assert disposed == dict(active_card=1, active_tokens=[], revision=0, cleared=False)
        with adapter.db.get_connection(read_only=True) as conn:
            assert conn.execute('SELECT 1 FROM client_view_state WHERE client_id=?', (client,)).fetchone() is None

        # Race 2: concurrent set happens right after read observation; writer keeps newer view.
        views.set(ZID, client, 1, focus)
        now[0] += cv.TOUCH_RENEWAL_SECONDS + 10
        action.append(lambda: views.set(ZID, client, 3, focus))
        merged = views.get(ZID, client)
        assert merged['active_card'] == 3 and merged['revision'] == 2 and merged['cleared'] is False
    finally:
        cv.inspect_view = orig_inspect

