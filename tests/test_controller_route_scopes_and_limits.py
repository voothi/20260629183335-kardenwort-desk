"""Real HTTP tests for controller route preference scopes, error mapping, and body limits."""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import sqlite3
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from kardenwort_sqlite_preferences import PreferencesAdapter
from kardenwort_state_runtime import preference_store, project_preferences
from tests.controller_test_support import controller_lifetime


TOKEN = 'test-controller-api-key'
ZID = '20261122170001'


def _http_request(url, path, *, method='GET', body=None, token=TOKEN):
    headers = {}
    if token is not None:
        headers['X-API-Token'] = token
    payload = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        payload = json.dumps(body).encode('utf-8')
    req = Request(url + path, data=payload, headers=headers, method=method)
    try:
        resp = urlopen(req, timeout=15)
    except HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode('utf-8', errors='replace')
        ctype = resp.headers.get('Content-Type', '')
        parsed = json.loads(raw) if 'application/json' in ctype else raw
        return resp.status, parsed


def _data(payload):
    if isinstance(payload, dict) and payload.get('status') == 'success' and 'data' in payload:
        return payload['data']
    return payload


def _independent_commit(server, **updates):
    """Commit SQLite preferences through an independent DB connection without mutating server.config."""
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    independent = PreferencesAdapter(db)
    independent.initialize(server.config)
    for key, value in updates.items():
        independent.set(key, value)
    return independent


@pytest.fixture
def scope_world(tmp_path):
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999), backend='sqlite') as (url, server):
        fav_dir = tmp_path / 'favorites'
        fav_dir.mkdir()
        server.resolved_paths['favorites_output_dir'] = fav_dir
        server.config.set('ui', 'theme', 'dark')
        server.config.set('rendering', 'default_zoom', '100')
        server.config.set('rendering', 'highlight_same_lemma', 'false')
        server.config.set('settings', 'default_language', 'de')
        server.config.set('settings', 'send_to_anki_after_export', 'false')
        server.goldendict.pop('zoom', None)
        server.goldendict.pop('default_zoom', None)
        server.goldendict.pop('defaultzoom', None)
        yield url, server, tmp_path


def _seed_session(server, zid=ZID, source_language='de', words=None):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    rows = words or [['Haus', 'Haus', '0', '1', '1']]
    adapter.save_session(
        session_zid=zid,
        source_language=source_language,
        target_language='ru',
        source_raw_text=' '.join(r[0] for r in rows) + '.',
        headers=['WordSource', 'WordSourceInflectedForm', 'TokenOrder', 'SentenceSourceIndex', 'DeskSelected'],
        data_rows=rows,
    )
    return adapter


def test_render_alias_observes_independent_sqlite_preferences(scope_world):
    url, server, _ = scope_world
    _seed_session(server)
    _independent_commit(server, theme='light', default_zoom=125)
    assert server.config.get('ui', 'theme') == 'dark'
    assert server.config.get('rendering', 'default_zoom') == '100'

    # Alias control: /api/v1/render is already in request_preference_scope.
    status_v1, body_v1 = _http_request(url, '/api/v1/render', method='POST', body={'session_zid': ZID})
    assert status_v1 == 200, body_v1
    html_v1 = _data(body_v1)['html']
    assert 'theme-light' in html_v1.split('<body', 1)[1].split('>', 1)[0]
    assert 'zoom: 125%' in html_v1

    # Excluded alias: /render must observe the same committed SQLite preferences.
    status_alias, body_alias = _http_request(url, '/render', method='POST', body={'session_zid': ZID})
    assert status_alias == 200, body_alias
    html_alias = _data(body_alias)['html']
    assert 'theme-light' in html_alias.split('<body', 1)[1].split('>', 1)[0]
    assert 'zoom: 125%' in html_alias

    # Auth control: unauthenticated /render remains rejected.
    unauth_status, _ = _http_request(url, '/render', method='POST', body={'session_zid': ZID}, token='wrong')
    assert unauth_status == 403


def test_render_alias_isolates_snapshot_across_concurrent_preference_commit(scope_world, monkeypatch):
    url, server, _ = scope_world
    _seed_session(server)
    store = preference_store(server.config, server.resolved_paths)
    project_preferences(server.config, store.set('theme', 'dark')['snapshot'])

    entered, release = threading.Event(), threading.Event()
    observed = {}
    real_render = controller.run_render_flow

    def intercepted_render(*args, **kwargs):
        cfg = kwargs['config']
        observed['before'] = cfg.get('ui', 'theme')
        entered.set()
        assert release.wait(5), 'Concurrent commit test did not release'
        observed['after'] = cfg.get('ui', 'theme')
        return real_render(*args, **kwargs)

    monkeypatch.setattr(controller, 'run_render_flow', intercepted_render)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_http_request, url, '/render', method='POST', body={'session_zid': ZID})
        try:
            assert entered.wait(5), 'Render flow was not entered'
            project_preferences(server.config, store.set('theme', 'light')['snapshot'])
        finally:
            release.set()
        status, body = future.result(timeout=15)

    assert status == 200, body
    assert observed == {'before': 'dark', 'after': 'dark'}


def test_api_v1_lookup_observes_independent_sqlite_language_preference(scope_world):
    url, server, _ = scope_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    project_id = db.create_project('Lookup Scope Project')
    _independent_commit(server, default_language='fr')
    assert server.config.get('settings', 'default_language') == 'de'

    # Alias control: /lookup is already in request_preference_scope.
    status_alias, html_alias = _http_request(url, f'/lookup?project_id={project_id}')
    assert status_alias == 200
    assert '<script id="session-lang" type="text/plain">fr</script>' in html_alias

    # Repaired route: /api/v1/lookup must observe the same committed default_language='fr'.
    status_v1, body_v1 = _http_request(url, f'/api/v1/lookup?project_id={project_id}')
    assert status_v1 == 200, body_v1
    assert _data(body_v1)['language'] == 'fr'
    assert '<script id="session-lang" type="text/plain">fr</script>' in _data(body_v1)['html']

    # Auth control remains enforced.
    assert _http_request(url, f'/api/v1/lookup?project_id={project_id}', token='wrong')[0] == 403


def test_api_v1_export_observes_independent_sqlite_default_language(scope_world):
    url, server, tmp_path = scope_world
    # Create a real frequency index for 'fr' ranking 'beta' before 'alpha', while 'de' has no index.
    freq_dir = tmp_path / 'freq'
    freq_dir.mkdir()
    fr_index = freq_dir / 'fr-freq.csv'
    fr_index.write_text('beta\nalpha\n', encoding='utf-8')
    server.resolved_paths['kardenwort_workspace'] = tmp_path
    if not server.config.has_section('languages'):
        server.config.add_section('languages')
    server.config.set('languages', 'fr_lemma_index', str(fr_index))
    server.config.set('languages', 'de_lemma_index', '')

    adapter = _seed_session(
        server,
        source_language='',
        words=[
            ['alpha', 'alpha', '0', '1', '0'],
            ['beta', 'beta', '1', '1', '0'],
        ],
    )
    restored = adapter.restore_session(ZID)
    with server.arbiter._lock:
        server.arbiter.sessions[ZID] = {
            'headers': list(restored['headers']),
            'data_rows': [list(r) for r in restored['data_rows']],
            'language': '',
            'role_fields': {'lemma': 'WordSource'},
        }

    _independent_commit(server, default_language='fr')
    assert server.config.get('settings', 'default_language') == 'de'

    # Alias control: /session/export is in request_preference_scope -> sorts using fr -> row 0 is 'beta'.
    status_sess, body_sess = _http_request(
        url, '/session/export', method='POST', body={'session_zid': ZID, 'selected_row_ids': [0]}
    )
    assert status_sess == 200, body_sess
    sess_lines = (tmp_path / 'favorites' / f'{ZID}.en.tsv').read_text(encoding='utf-8').splitlines()
    sess_headers = sess_lines[0].split('\t')
    sess_word = sess_lines[1].split('\t')[sess_headers.index('WordSource')]
    assert sess_word == 'beta'

    # Excluded route: /api/v1/export must match /session/export and export 'beta', not stale 'alpha'.
    status_v1, body_v1 = _http_request(
        url, '/api/v1/export', method='POST', body={'session_zid': ZID, 'selected_row_ids': [0]}
    )
    assert status_v1 == 200, body_v1
    v1_lines = (tmp_path / 'favorites' / f'{ZID}.en.tsv').read_text(encoding='utf-8').splitlines()
    v1_headers = v1_lines[0].split('\t')
    v1_word = v1_lines[1].split('\t')[v1_headers.index('WordSource')]
    assert v1_word == 'beta'


def test_admin_project_export_deck_observes_independent_sqlite_language(scope_world):
    url, server, _ = scope_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    project_id = db.create_project('Scope Audit Export Deck Project')
    _independent_commit(server, default_language='fr')
    assert server.config.get('settings', 'default_language') == 'de'

    # Explicit language override control remains honored.
    st_exp_ov, body_exp_ov = _http_request(
        url, '/api/v1/admin/projects/export-deck', method='POST', body={'project_id': project_id, 'language': 'es'}
    )
    assert st_exp_ov == 200 and _data(body_exp_ov)['language'] == 'es'

    # Auth control remains enforced.
    assert _http_request(url, '/api/v1/admin/projects/export-deck', method='POST', body={'project_id': project_id}, token='wrong')[0] == 403

    # Omitted language must observe committed SQLite default_language='fr', not stale 'de'.
    st_exp, body_exp = _http_request(
        url, '/api/v1/admin/projects/export-deck', method='POST', body={'project_id': project_id}
    )
    assert st_exp == 200, body_exp
    assert _data(body_exp)['language'] == 'fr'


def test_admin_project_synthesize_observes_independent_sqlite_language(scope_world):
    url, server, _ = scope_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    project_id = db.create_project('Scope Audit Synthesize Project')
    _independent_commit(server, default_language='fr')
    assert server.config.get('settings', 'default_language') == 'de'

    # Explicit language override control remains honored.
    st_syn_ov, body_syn_ov = _http_request(
        url, f'/api/v1/admin/projects/synthesize?project_id={project_id}&language=es'
    )
    assert st_syn_ov == 200 and _data(body_syn_ov)['source_language'] == 'es'

    # Auth control remains enforced.
    assert _http_request(url, f'/api/v1/admin/projects/synthesize?project_id={project_id}', token='wrong')[0] == 403

    # Omitted language must observe committed SQLite default_language='fr', not stale 'de'.
    st_syn, body_syn = _http_request(
        url, f'/api/v1/admin/projects/synthesize?project_id={project_id}'
    )
    assert st_syn == 200, body_syn
    assert _data(body_syn)['source_language'] == 'fr'


def test_confirm_language_observes_independent_preferences_and_preserves_newly_committed_language(scope_world, monkeypatch):
    url, server, _ = scope_world
    # Remove duplicated storage_backend hint so SQLite ownership comes from config['storage']['backend'].
    server.resolved_paths.pop('storage_backend', None)
    _independent_commit(server, highlight_same_lemma=True)
    assert server.config.getboolean('rendering', 'highlight_same_lemma') is False

    observed_configs = []
    monkeypatch.setattr(controller, 'spawn_ahk', lambda *args, **kwargs: None)
    monkeypatch.setattr(controller.webbrowser, 'open_new_tab', lambda *args, **kwargs: True)

    def spy_render_flow(*args, **kwargs):
        cfg = kwargs['config']
        observed_configs.append({
            'highlight_same_lemma': cfg.getboolean('rendering', 'highlight_same_lemma'),
            'default_language': cfg.get('settings', 'default_language'),
            'is_shared': cfg is server.config,
        })
        return '<html><body>ok</body></html>', []

    monkeypatch.setattr(controller, 'run_render_flow', spy_render_flow)

    draft_keep = {'text': 'Haus.', 'language': 'de', 'text_mode': 'single'}
    draft_switch = {'text': 'Maison.', 'language': 'de', 'text_mode': 'single'}
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS['20261122170010'] = draft_keep
        controller._DRAFT_SESSIONS['20261122170011'] = draft_switch

    try:
        st_keep, body_keep = _http_request(
            url, '/api/v1/confirm-language', method='POST',
            body={'session_zid': '20261122170010', 'action': 'keep'},
        )
        assert st_keep == 200, body_keep

        st_proc, body_proc = _http_request(
            url, '/api/v1/confirm-language', method='POST',
            body={'session_zid': '20261122170011', 'action': 'process', 'chosen_source_lang': 'fr'},
        )
        assert st_proc == 200, body_proc
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop('20261122170010', None)
            controller._DRAFT_SESSIONS.pop('20261122170011', None)

    assert len(observed_configs) == 2
    # Both requests must run on a captured request-scoped config observing highlight_same_lemma=True.
    assert observed_configs[0] == {'highlight_same_lemma': True, 'default_language': 'de', 'is_shared': False}
    # After set_language commits 'fr', render_flow_fn must see the newly committed 'fr', not pre-commit 'de'.
    assert observed_configs[1] == {'highlight_same_lemma': True, 'default_language': 'fr', 'is_shared': False}


def test_tag_route_reads_no_sqlite_preferences_control(scope_world):
    url, server, _ = scope_world
    adapter = _seed_session(server, words=[['Haus', 'Haus', '0', '1', '']])
    restored = adapter.restore_session(ZID)
    fp = desk.compute_content_fingerprint(restored['data_rows'])

    _independent_commit(server, theme='light', default_zoom=140, default_language='fr', highlight_same_lemma=True)
    status, body = _http_request(
        url, '/api/v1/tag', method='POST',
        body={'session_zid': ZID, 'language': 'de', 'row_id': 0, 'token_order': 0, 'sentence_idx': 1, 'status': True, 'fingerprint': fp},
    )
    assert status == 200, body
    assert adapter.get_shared_selection(ZID)['haus'] is True
    assert _http_request(
        url, '/api/v1/tag', method='POST',
        body={'session_zid': ZID, 'language': 'de', 'row_id': 0, 'status': True, 'fingerprint': fp},
        token='wrong',
    )[0] == 403


def test_render_alias_retains_legacy_tsv_backend_without_sqlite_file(tmp_path):
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999), backend='tsv') as (url, server):
        server.config.set('ui', 'theme', 'light')
        server.config.set('rendering', 'default_zoom', '115')
        tsv_path = server.resolved_paths['results_dir'] / f'{ZID}-haus.de.tsv'
        tsv_path.write_text(
            'WordSource\tWordSourceInflectedForm\tTokenOrder\tSentenceSourceIndex\tSentenceSource\n'
            'Haus\tHaus\t0\t1\tHaus.\n',
            encoding='utf-8',
        )
        assert not server.resolved_paths['sqlite_db_path'].exists()
        status, body = _http_request(
            url, '/render', method='POST',
            body={'session_zid': ZID, 'language': 'de', 'text': 'Haus.', 'tsv': str(tsv_path), 'bypass_lang_check': True},
        )
        assert status == 200, body
        html = _data(body)['html']
        assert 'theme-light' in html.split('<body', 1)[1].split('>', 1)[0]
        assert 'zoom: 115%' in html
        assert not server.resolved_paths['sqlite_db_path'].exists()


@pytest.mark.parametrize('bad_lang', ['english', '123'])
def test_confirm_language_invalid_language_returns_400_without_committing(scope_world, monkeypatch, bad_lang):
    url, server, _ = scope_world
    store = preference_store(server.config, server.resolved_paths)
    before_snapshot = store.snapshot()
    host_calls = []
    monkeypatch.setattr(controller, 'spawn_ahk', lambda *args, **kwargs: host_calls.append(args))
    monkeypatch.setattr(controller.webbrowser, 'open_new_tab', lambda *args, **kwargs: True)
    monkeypatch.setattr(controller, 'run_render_flow', lambda *args, **kwargs: ('<html><body>ok</body></html>', []))

    draft_zid = '20261122172001'
    draft = {'text': 'Hello world.', 'language': 'de', 'text_mode': 'single'}
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[draft_zid] = draft
    try:
        # Auth control: unauthenticated request still rejected before validation.
        unauth_status, _ = _http_request(
            url, '/api/v1/confirm-language', method='POST',
            body={'session_zid': draft_zid, 'action': 'process', 'chosen_source_lang': bad_lang},
            token='wrong',
        )
        assert unauth_status == 403

        status, body = _http_request(
            url, '/api/v1/confirm-language', method='POST',
            body={'session_zid': draft_zid, 'action': 'process', 'chosen_source_lang': bad_lang},
        )
        assert status == 400, body
        assert body['error_code'] == 'INVALID_PAYLOAD'
        assert store.snapshot() == before_snapshot
        assert not host_calls
        with controller._DRAFT_SESSIONS_LOCK:
            assert controller._DRAFT_SESSIONS.get(draft_zid) == draft
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(draft_zid, None)


def test_confirm_language_storage_failure_and_valid_controls(scope_world, monkeypatch):
    url, server, _ = scope_world
    store = preference_store(server.config, server.resolved_paths)
    store.db.busy_timeout_ms = 10
    before_snapshot = store.snapshot()
    host_calls = []
    monkeypatch.setattr(controller, 'spawn_ahk', lambda *args, **kwargs: host_calls.append(args))
    monkeypatch.setattr(controller.webbrowser, 'open_new_tab', lambda *args, **kwargs: True)
    monkeypatch.setattr(controller, 'run_render_flow', lambda *args, **kwargs: ('<html><body>ok</body></html>', []))

    draft_zid = '20261122172002'
    draft = {'text': 'Bonjour.', 'language': 'de', 'text_mode': 'single'}
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[draft_zid] = draft

    blocker = sqlite3.connect(store.db.db_path, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        fail_status, fail_body = _http_request(
            url, '/api/v1/confirm-language', method='POST',
            body={'session_zid': draft_zid, 'action': 'process', 'chosen_source_lang': 'fr'},
        )
        assert fail_status == 500, fail_body
        assert fail_body['error_code'] == 'CONFIGURATION_ERROR'
        assert not host_calls
        with controller._DRAFT_SESSIONS_LOCK:
            assert controller._DRAFT_SESSIONS.get(draft_zid) == draft
    finally:
        blocker.rollback()
        blocker.close()

    try:
        assert store.snapshot() == before_snapshot
        ok_status, ok_body = _http_request(
            url, '/api/v1/confirm-language', method='POST',
            body={'session_zid': draft_zid, 'action': 'process', 'chosen_source_lang': 'fr'},
        )
        assert ok_status == 200, ok_body
        assert _data(ok_body)['language'] == 'fr'
        assert store.get('default_language') == 'fr'
        assert len(host_calls) == 1
        with controller._DRAFT_SESSIONS_LOCK:
            assert draft_zid not in controller._DRAFT_SESSIONS
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(draft_zid, None)


MAX_IMPORT_TSV_BYTES = 10 * 1024 * 1024
SAMPLE_RAW_TSV = (
    b'Quotation\tWordSource\tWordDestination\tSentenceSource\tDeskSelected\n'
    b'Vogel\tVogel\t\xd0\xbf\xd1\x82\xd0\xb8\xd1\x86\xd0\xb0\tEin Vogel singt.\t1\n'
)


def _raw_http_post(port, path, *, content_length=None, body_bytes=b'', token=TOKEN, content_type='text/tab-separated-values', shutdown_write=True):
    header_lines = [
        f'POST {path} HTTP/1.1',
        f'Host: 127.0.0.1:{port}',
        f'Content-Type: {content_type}',
        'Connection: close',
    ]
    if token is not None:
        header_lines.append(f'X-API-Token: {token}')
    if content_length is not None:
        header_lines.append(f'Content-Length: {content_length}')
    wire = ('\r\n'.join(header_lines) + '\r\n\r\n').encode('ascii')
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        try:
            sock.sendall(wire + body_bytes)
            if shutdown_write:
                sock.shutdown(socket.SHUT_WR)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass
        chunks = []
        while True:
            try:
                chunk = sock.recv(65536)
            except (ConnectionAbortedError, ConnectionResetError):
                break
            if not chunk:
                break
            chunks.append(chunk)
    raw = b''.join(chunks)
    head, _, payload = raw.partition(b'\r\n\r\n')
    status = int(head.split(b'\r\n', 1)[0].split(b' ')[1])
    parsed = json.loads(payload.decode('utf-8')) if payload else {}
    return status, parsed


@pytest.mark.parametrize('bad_len,zid', [
    ('abc', '20261122173001'),
    ('-5', '20261122173002'),
    ('-1', '20261122173004'),
    (str(MAX_IMPORT_TSV_BYTES + 1), '20261122173003'),
])
def test_import_tsv_raw_invalid_content_lengths_rejected_before_read(scope_world, bad_len, zid):
    _, server, _ = scope_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    path = f'/api/v1/sessions/import-tsv?session_zid={zid}&language=de'

    # Auth control: unauthenticated requests are rejected before body length validation.
    unauth_status, unauth_body = _raw_http_post(
        server.server_port, path, content_length=bad_len, body_bytes=SAMPLE_RAW_TSV, token='wrong',
    )
    assert unauth_status == 403, unauth_body
    assert db.get_session_bundle(zid) is None

    status, body = _raw_http_post(
        server.server_port, path, content_length=bad_len, body_bytes=SAMPLE_RAW_TSV,
    )
    assert status == 400, body
    assert body['error_code'] == 'INVALID_PAYLOAD'
    assert db.get_session_bundle(zid) is None


def test_import_tsv_raw_truncated_body_rejected_without_partial_import(scope_world):
    _, server, _ = scope_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    st_trunc, body_trunc = _raw_http_post(
        server.server_port, '/api/v1/sessions/import-tsv?session_zid=20261122173012&language=de',
        content_length='300', body_bytes=SAMPLE_RAW_TSV,
    )
    assert st_trunc == 400, body_trunc
    assert body_trunc['error_code'] == 'INVALID_PAYLOAD'
    assert db.get_session_bundle('20261122173012') is None


def test_import_tsv_raw_absent_zero_and_exact_limit_controls(scope_world):
    _, server, _ = scope_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()

    # Absent Content-Length -> 400 MISSING_FIELD, zero DB write.
    st_abs, body_abs = _raw_http_post(
        server.server_port, '/api/v1/sessions/import-tsv?session_zid=20261122173010&language=de',
        content_length=None, body_bytes=b'',
    )
    assert st_abs == 400 and body_abs['error_code'] == 'MISSING_FIELD'
    assert db.get_session_bundle('20261122173010') is None

    # Zero Content-Length -> 400 MISSING_FIELD, zero DB write.
    st_zero, body_zero = _raw_http_post(
        server.server_port, '/api/v1/sessions/import-tsv?session_zid=20261122173011&language=de',
        content_length='0', body_bytes=b'',
    )
    assert st_zero == 400 and body_zero['error_code'] == 'MISSING_FIELD'
    assert db.get_session_bundle('20261122173011') is None

    # Exact 10 MiB limit -> accepted (200 OK) and persisted cleanly.
    pad_len = MAX_IMPORT_TSV_BYTES - len(SAMPLE_RAW_TSV) - 3  # '# ' + pad + '\n'
    exact_payload = b'# ' + (b'x' * pad_len) + b'\n' + SAMPLE_RAW_TSV
    assert len(exact_payload) == MAX_IMPORT_TSV_BYTES
    st_exact, body_exact = _raw_http_post(
        server.server_port, '/api/v1/sessions/import-tsv?session_zid=20261122173013&language=de',
        content_length=str(MAX_IMPORT_TSV_BYTES), body_bytes=exact_payload,
    )
    assert st_exact == 200, body_exact
    assert _data(body_exact)['words_count'] == 1
    bundle = db.get_session_bundle('20261122173013')
    assert bundle is not None and bundle['words'][0]['lemma'] == 'Vogel'
