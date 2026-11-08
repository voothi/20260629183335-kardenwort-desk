"""Public retext actions retain real persistence and occurrence isolation."""
import json
import copy
import http.client
import urllib.error
import urllib.request

import pytest

import kardenwort_desk as desk
from tests.controller_test_support import controller_world, translation_service, TranslationRequestHandler


def post(base, path, payload, token='test-controller-api-key'):
    request = urllib.request.Request(base + path, json.dumps(payload).encode('utf-8'),
        {'Content-Type': 'application/json', 'X-API-Token': token})
    return urllib.request.urlopen(request, timeout=5)


def seed(server, zid):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    path = server.resolved_paths['results_dir'] / f'{zid}-owned.en.tsv'
    headers = ['TokenOrder', 'Quotation', 'WordSource', 'SentenceSourceIndex',
               'SentenceSource', 'SentenceDestination', 'WordDestination']
    rows = [['0', 'House', 'house', '1', 'House one.', 'Old one.', 'word one'],
            ['0', 'House', 'house', '2', 'House two.', 'Old two.', 'word two']]
    adapter.save_session(session_zid=zid, slug='owned', source_language='en',
        target_language='ru', text_mode='multi', source_raw_text='House one.\nHouse two.',
        headers=headers, data_rows=rows, working_tsv_path=path, tsv_path=path)
    if adapter.backend_name == 'tsv':
        path.with_suffix('.txt').write_text('House one.\nHouse two.', encoding='utf-8')
    return adapter, path, headers, rows


def snapshot(adapter, path, zid):
    if adapter.backend_name == 'sqlite':
        return (adapter.db.get_words_by_session(zid), adapter.db.get_sentences_by_session(zid))
    return path.read_bytes()


def assert_sqlite_persistence(adapter, zid, before):
    words, sentences = snapshot(adapter, None, zid)
    expected_words = copy.deepcopy(before[0])
    destinations = {1: 'google: House one.', 2: 'google: House two.'}
    for word in expected_words:
        word['extra_fields']['SentenceDestination'] = destinations[word['sentence_index']]
    assert words == expected_words
    assert [(word['sentence_index'], word['token_order'], word['word_destination']) for word in words] == [
        (1, 0, 'word one'), (2, 0, 'word two')]
    assert [(record['sentence_index'], record['sentence_source'], record['sentence_destination'],
             record['text_provenance']) for record in sentences] == [
        (1, 'House one.', 'google: House one.', 'live:google'),
        (2, 'House two.', 'google: House two.', 'live:google')]


def assert_tsv_persistence(path, headers, rows):
    _, stored_headers, stored = desk.load_tsv_rows(path)
    assert stored_headers == headers
    destination = headers.index('SentenceDestination')
    assert [row[destination] for row in stored] == ['google: House one.', 'google: House two.']
    assert [row[:destination] + row[destination + 1:] for row in stored] == [
        row[:destination] + row[destination + 1:] for row in rows]
    assert path.is_file()


def assert_cold_recovery(base, server, zid):
    server.arbiter.sessions.clear()
    request = urllib.request.Request(base + f'/session/status?zid={zid}',
                                    headers={'X-API-Token': 'test-controller-api-key'})
    with urllib.request.urlopen(request, timeout=5) as response:
        recovered = json.load(response)['data']
    assert recovered['text_provenance'] == 'live:google'
    assert 'google: House one.' in recovered['translatedText']
    assert 'google: House two.' in recovered['translatedText']


@pytest.mark.parametrize('controller_world', ['sqlite', 'tsv'], indirect=True)
def test_provider_http_retext_persists_without_touching_other_occurrences(controller_world):
    base, server = controller_world
    zid, other = '20261108003601', '20261108003602'
    adapter, path, headers, rows = seed(server, zid)
    _, other_path, _, _ = seed(server, other)
    before, before_other = snapshot(adapter, path, zid), snapshot(adapter, other_path, other)
    subscriber = server.arbiter.register_subscriber(zid)
    try:
        payload = {'session_zid': zid, 'language': 'en', 'text_mode': 'multi'}
        with pytest.raises(urllib.error.HTTPError) as rejected:
            post(base, '/session/retext', payload, token='wrong-token')
        assert rejected.value.code == 403
        assert server.translation_service.provider_calls == []
        assert snapshot(adapter, path, zid) == before
        assert subscriber.empty()

        with post(base, '/session/retext', payload) as response:
            assert response.status == 200
            result = json.load(response)['data']
        assert result['sentences'] == {'1': 'google: House one.', '2': 'google: House two.'}
        assert result['text_provenance'] == 'live:google'
        assert result['active_text_provider'] == 'google'
        assert server.translation_service.provider_calls
        assert snapshot(adapter, other_path, other) == before_other
        events = [subscriber.get(timeout=1), subscriber.get(timeout=1)]
        committed = next(event for event in events if event['stage'] == 'translated_text')
        assert committed['sentences'] == {1: 'google: House one.', 2: 'google: House two.'}
        if adapter.backend_name == 'sqlite':
            assert_sqlite_persistence(adapter, zid, before)
            assert_cold_recovery(base, server, zid)
        else:
            assert_tsv_persistence(path, headers, rows)
    finally:
        server.arbiter.unregister_subscriber(zid, subscriber)


def test_translation_service_worker_failure_reaches_owner():
    class BrokenHandler(TranslationRequestHandler):
        def do_GET(self):
            raise RuntimeError('Owned translation worker failed')

    with pytest.raises(BaseExceptionGroup) as failure:
        with translation_service(BrokenHandler) as service:
            with pytest.raises(http.client.RemoteDisconnected):
                urllib.request.urlopen(f'http://127.0.0.1:{service.server_port}/health', timeout=2)
    assert len(failure.value.exceptions) == 1
    assert isinstance(failure.value.exceptions[0], RuntimeError)
    assert str(failure.value.exceptions[0]) == 'Owned translation worker failed'
    assert service.resources_stopped()


def test_second_listener_setup_failure_disposes_first_owner(tmp_path, monkeypatch):
    from tests import test_controller_integration as integration
    from tests.test_browser_server_lifetime import controllers, assert_drained

    opened = []
    create = integration.create_browser_test_server

    def fail_second(handler):
        if opened:
            raise PermissionError('Second listener allocation denied')
        server = create(handler)
        opened.append(server)
        return server

    monkeypatch.setattr(integration, 'create_browser_test_server', fail_second)
    lifetime = controllers.__wrapped__(tmp_path)
    with pytest.raises(PermissionError, match='Second listener allocation denied'):
        next(lifetime)
    assert len(opened) == 1
    assert_drained(opened[0])
