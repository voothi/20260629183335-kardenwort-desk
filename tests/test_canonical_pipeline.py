"""Public commands and real SQLite effects at configured external engine ports."""
import json
import subprocess
from http.client import HTTPConnection

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.reword_contract_support import ZID, TOKEN, ROWS, backend, provider_name
from tests.release_projection_support import checked_world as world
from tests.test_lookup import setup_test_env
from tests.test_structured_enrichment import control_writes, prohibited


def post(world, endpoint, payload):
    connection = HTTPConnection('127.0.0.1', world.port, timeout=10)
    try:
        connection.request('POST', endpoint, json.dumps(payload),
                           {'Content-Type': 'application/json', 'X-API-Token': TOKEN})
        response = connection.getresponse()
        return response.status, json.load(response)
    finally:
        connection.close()


@pytest.mark.parametrize('target', [dict(sentence_idx=1, token_order=0), dict(sentence_idx=2, token_order=0)])
def test_retry_exact_duplicate_token_identity_and_cold_restore(world, target):
    before = world.adapter.db.get_words_by_session(ZID)
    with control_writes() as writes:
        status, payload = post(world, '/session/retry', dict(session_zid=ZID, target_coordinates=[target]))
    assert status == 200, payload
    cold = desk.SqliteStorageAdapter(world.config, world.paths).db.get_words_by_session(ZID)
    selected = target['sentence_idx'] - 1
    assert cold[selected]['word_destination'] == 'fresh-' + cold[selected]['lemma']
    assert cold[selected]['word_provenance'] == 'live:google'
    assert cold[1-selected] == before[1-selected]
    assert prohibited(writes) == []


@pytest.mark.parametrize('payload', [dict(token_orders=[0]), dict(row_ids=False),
    dict(target_coordinates=[dict(sentence_idx=99, token_order=0)]),
    dict(target_coordinates=[dict(session_zid='foreign', sentence_idx=1, token_order=0)])])
def test_retry_rejects_ambiguous_or_malformed_selection_before_provider(world, payload):
    before = world.snapshot()
    status, result = post(world, '/session/retry', dict(session_zid=ZID, **payload))
    assert status >= 400, result
    assert world.provider.calls == []
    assert world.snapshot() == before


def test_progressive_cold_session_keeps_duplicate_token_targets_and_has_no_control_files(world):
    rows = [list(row) for row in ROWS]
    for row in rows:
        row[3] = ''
    world.seed(ZID, rows, text='Haus. Baum.')
    for word in world.adapter.db.get_words_by_session(ZID):
        world.adapter.db.update_word(word['id'], {'word_destination': ''})
    world.arbiter.sessions.pop(ZID)
    world.config.set('triggers', 'run_lemma_base_translation', 'auto')
    channel = world.subscribe(ZID)
    with control_writes() as writes:
        world.arbiter.enrichment_queue._execute_progressive_task(ZID, world.arbiter, 'de', 'en', skip_intellifiller=True)
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'fresh-Baum'}
    assert {word['word_provenance'] for word in world.adapter.db.get_words_by_session(ZID)} == {'live:google'}
    events = []
    while not channel.empty():
        events.append(channel.get_nowait())
    assert any(event.get('stage') == 'finished' and event.get('status') == 'success' for event in events)
    assert prohibited(writes) == []


def test_structured_extraction_uses_stdin_and_jsonl_without_working_files(tmp_path, monkeypatch):
    config, paths, _, _ = setup_test_env(tmp_path)
    config.read_dict({'storage': {'backend': 'sqlite', 'fallback_to_tsv': 'false'}})
    paths.update(storage_backend='sqlite', sqlite_db_path=tmp_path/'fixture.db', results_dir=tmp_path/'results')
    fields = ['TokenOrder', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource']
    mapping = tmp_path/'anki_mapping.ini'
    mapping.write_text('[fields]\n'+'\n'.join(fields)+'\n[desk_columns]\nWordSource=lemma\n'
                       'WordDestination=word_translation\nSentenceSourceIndex=sentence_index\nSentenceSource=sentence\n'
                       '[fields_mapping.word]\nlemma=WordSource\nsentence_index=SentenceSourceIndex\n', encoding='utf-8')
    calls = []

    def extractor(command, **kwargs):
        calls.append(command)
        assert '--structured-output' in command
        assert '--output-file' not in command and '--text1-file' not in command
        assert kwargs['input'] == 'Haus.'
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(zip(fields, ['0', 'Haus', '', '1', 'Haus.']))), '')

    monkeypatch.setattr(subprocess, 'run', extractor)
    with control_writes() as writes:
        identity = desk.prepare_lookup_tsv('Haus.', 'en', 'de', config, paths, ZID,
            ttl_seconds=0, cache_key=f'{ZID}-haus.en.tsv')
    assert identity == ZID
    restored = desk.SqliteStorageAdapter(config, paths).restore_session(ZID)
    assert restored['words'][0]['lemma'] == 'Haus'
    assert len(calls) == 1
    assert prohibited(writes) == []


def test_provider_policy_state_survives_restart_without_json_files(world):
    with control_writes() as writes:
        desk.record_provider_cooldown('google', config=world.config, resolved_paths=world.paths, task_type='lemma')
        desk.record_session_active_provider(ZID, task='lemma', provider='google', config=world.config, resolved_paths=world.paths)
        desk._provider_cooldowns.clear()
        desk._active_session_providers.clear()
        assert desk.is_provider_cooled_down('google', config=world.config, resolved_paths=world.paths, task_type='lemma')
        assert not desk.is_provider_cooled_down('google', config=world.config, resolved_paths=world.paths, task_type='text')
        assert desk.get_session_active_providers(ZID, config=world.config, resolved_paths=world.paths)['active_lemma_provider'] == 'google'
    assert not [path for path in writes if path.endswith('.json')]
    assert prohibited(writes) == []


def test_queue_sqlite_profile_without_duplicated_backend_flag_never_falls_back_to_files(world, monkeypatch):
    world.paths.pop('storage_backend')
    monkeypatch.setattr(controller, 'query_intellifiller_server', lambda **kwargs: None)
    with control_writes() as writes:
        with pytest.raises(desk.StructuredError, match='structured request failed'):
            world.arbiter.enrichment_queue._execute_enrich_lemma('Haus', 'de', 'fixture', ZID)
    assert prohibited(writes) == []


def test_project_reader_uses_relational_materials_without_mirror_tsv(world):
    from tests.controller_test_support import token_service
    project = world.adapter.db.create_project(title='Book', slug='book')
    world.adapter.db.link_session_to_project(project, ZID)
    original = world.snapshot()
    sessions_before = {row['zid'] for row in world.adapter.db.list_sessions_with_counts()}
    with token_service() as tokenizer:
        world.config.set('services', 'spacy_server_url', f'http://127.0.0.1:{tokenizer.server_port}')
        connection = HTTPConnection('127.0.0.1', world.port, timeout=10)
        try:
            with control_writes() as writes:
                connection.request('GET', f'/?project_id={project}&language=de', headers={'X-API-Token': TOKEN})
                response = connection.getresponse()
                body = response.read().decode('utf-8')
            assert response.status == 200, body
            assert 'Book' in body and 'Haus' in body and 'Baum' in body
            assert prohibited(writes) == []
        finally:
            connection.close()
    assert world.snapshot() == original
    cold = desk.SqliteStorageAdapter(world.config, world.paths)
    created = {row['zid'] for row in cold.db.list_sessions_with_counts()} - sessions_before
    assert len(created) == 1
    assert {word['lemma'] for word in cold.db.get_words_by_session(created.pop())} >= {'Haus', 'Baum'}


@pytest.mark.parametrize('action', ['save', 'reword', 'retry'])
def test_committed_delivery_failure_exposes_read_recovery_without_duplicate_mutation(world, monkeypatch, action):
    def broken_delivery(*args, **kwargs):
        raise ConnectionError('owned delivery interruption')

    monkeypatch.setattr(world.arbiter, 'emit_event', broken_delivery)
    payload = dict(session_zid=ZID)
    if action == 'save':
        payload['deltas'] = [dict(row_id=0, sentence_idx=1, token_order=0,
                                  column='WordDestination', value='manual-committed')]
        expected = 'manual-committed'
    else:
        payload.update(row_ids=[0], target_coordinates=[dict(sentence_idx=1, token_order=0)], sentence_idx=1)
        expected = 'fresh-Haus'
    with control_writes() as writes:
        status, result = post(world, '/session/' + action, payload)
    assert status >= 400, result
    details = result['details']
    assert details['committed'] is True
    assert details['session_zid'] == ZID and details['fingerprint']
    assert world.values() == {(1, 0): expected, (2, 0): 'tree'}
    calls = list(world.provider.calls)
    connection = HTTPConnection('127.0.0.1', world.port, timeout=10)
    try:
        connection.request('GET', details['recovery_url'], headers={'X-API-Token': TOKEN})
        response = connection.getresponse()
        recovered = json.load(response)
        assert response.status == 200, recovered
    finally:
        connection.close()
    assert world.provider.calls == calls
    assert world.values() == {(1, 0): expected, (2, 0): 'tree'}
    assert prohibited(writes) == []


def test_retry_token_orders_empty_selectors_and_ambiguity_do_not_trigger_sentence_retry(world, monkeypatch):
    """Verify token_orders and empty selectors never trigger full-session sentence retry, and duplicate tokens fail cleanly."""
    rows = [
        ['0', 'Haus', 'Haus', '', '1', 'Das Haus.', '', ''],
        ['0', 'Baum', 'Baum', '', '2', 'Der Baum.', '', ''],
        ['1', 'Himmel', 'Himmel', '', '2', 'Der Baum.', '', ''],
    ]
    world.seed(ZID, rows, text='Das Haus. Der Baum.')
    with world.adapter.db.get_connection() as conn:
        conn.execute("UPDATE sentences SET sentence_destination = '' WHERE session_zid = ?", (ZID,))
        conn.execute("UPDATE words SET word_destination = '' WHERE session_zid = ?", (ZID,))
    world.arbiter.sessions.pop(ZID, None)
    world.provider.calls.clear()
    sentence_calls = []
    monkeypatch.setattr(controller, 'translate_source_text', lambda *a, **kw: sentence_calls.append(a))

    # 1. token_orders=[1] retries ONLY (2, 1), never calling sentence translation
    status, res = post(world, '/session/retry', dict(session_zid=ZID, token_orders=[1]))
    assert status == 200, res
    assert world.provider.calls == [['Himmel']]
    assert sentence_calls == []
    assert world.values() == {(1, 0): '', (2, 0): '', (2, 1): 'fresh-Himmel'}
    sents = {s['sentence_index']: s['sentence_destination'] for s in world.adapter.db.get_sentences_by_session(ZID)}
    assert sents == {1: '', 2: ''}

    # 2. Explicit empty selectors perform zero provider calls and zero sentence retry
    world.provider.calls.clear()
    for empty_payload in (dict(token_orders=[]), dict(row_ids=[]), dict(target_coordinates=[])):
        st, out = post(world, '/session/retry', dict(session_zid=ZID, **empty_payload))
        assert st == 200, out
        payload_data = out.get('data', out)
        assert payload_data['retried_rows'] == []
        assert world.provider.calls == []
        assert sentence_calls == []

    # 3. Repeated local token_order=0 in different sentences rejects ambiguity cleanly before provider calls
    st_amb, out_amb = post(world, '/session/retry', dict(session_zid=ZID, token_orders=[0]))
    assert st_amb >= 400, out_amb
    assert world.provider.calls == []
    assert sentence_calls == []
    assert world.values() == {(1, 0): '', (2, 0): '', (2, 1): 'fresh-Himmel'}

