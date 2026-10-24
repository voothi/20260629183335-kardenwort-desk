"""Real process, filesystem, and database failure boundaries."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_sentence_idx_contract import _make_persist_fixture
from tests.test_intent_contract_integrity import PRODUCER


def run_producer(tmp_path, port, token):
    config = tmp_path / 'producer.ini'
    config.write_text(f'[storage]\nbackend=sqlite\n[server]\nport={port}\napi_key={token}\n')
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    result = subprocess.run([sys.executable, '-c', PRODUCER, str(config),
                             str(artifacts / '20261024225159.de.tsv'), 'finished'],
                            capture_output=True, text=True, timeout=20)
    return result, artifacts


def test_unauthorized_producer_has_no_fallback_artifact(running_controller, tmp_path):
    _, server, _ = running_controller
    result, artifacts = run_producer(tmp_path, server.server_port, 'invalid-test-token')
    assert result.returncode != 0
    assert 'could not be delivered' in result.stderr
    assert list(artifacts.iterdir()) == []


def test_offline_producer_has_no_fallback_artifact(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        result, artifacts = run_producer(tmp_path, reserved.getsockname()[1], 'test-token')
    assert result.returncode != 0
    assert 'could not be delivered' in result.stderr
    assert list(artifacts.iterdir()) == []


def test_maintenance_preserves_active_and_non_artifact_files(tmp_path):
    old = tmp_path / 'old.updates'
    active = tmp_path / 'active.updates'
    old.mkdir()
    active.mkdir()
    export = tmp_path / 'user-export.tsv'
    export.write_text('user data')
    os.utime(old, (1, 1))
    os.utime(export, (1, 1))
    assert desk.prune_stale_results_artifacts(tmp_path) == 1
    assert active.exists() and export.read_text() == 'user data'
    assert not old.exists()


def test_diskless_leaves_existing_legacy_artifacts_untouched(tmp_path):
    config, _, _, _ = desk.load_config()
    config.set('server', 'diskless_updates', 'true')
    path = tmp_path / '20261024225159.de.tsv'
    directory = tmp_path / '20261024225159.de.updates'
    directory.mkdir()
    chunk = directory / 'old.js'
    chunk.write_text('historical chunk')
    marker = path.with_suffix('.base_translation_done')
    marker.write_text('old marker')
    desk._remove_compatibility_updates(path, config)
    desk._touch_compatibility_marker(path, '.enrichment_done', config)
    desk.write_update_js(path, [], [], {}, config=config, empty_payload=True, zid='20261024225159')
    assert chunk.read_text() == 'historical chunk'
    assert marker.read_text() == 'old marker'
    assert not path.with_suffix('.enrichment_done').exists()


def test_late_status_recovery_uses_committed_sqlite(running_controller, monkeypatch):
    url, server, adapter = running_controller
    zid = '20261024225159'
    headers = ['TokenOrder', 'WordSource', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination']
    rows = [['0', 'one', '1', 'One.', 'Old one'], ['1', 'two', '2', 'Two.', 'Old two']]
    adapter.save_session(session_zid=zid, source_language='en', source_raw_text='One. Two.', headers=headers, data_rows=rows)
    monkeypatch.setattr(controller, 'translate_source_text', lambda *a, **kw: {0: 'New one', 1: 'New two'})
    server.arbiter.retext_session(zid, language='en')
    server.arbiter.sessions.clear()  # A newly connected client must survive cold controller projections.
    request = Request(url + '/session/status?zid=' + zid, headers={'X-API-Token': 'test-controller-api-key'})
    with urlopen(request, timeout=5) as response:
        payload = json.load(response)
    serialized = json.dumps(payload)
    assert 'New one' in serialized and 'New two' in serialized


def test_confirmed_single_sentence_scalar_command(tmp_path, monkeypatch):
    row = [['0', 'Eins', 'eins', '1', 'Satz eins.', 'Old', '', 'one']]
    arbiter, adapter, zid, _ = _make_persist_fixture(tmp_path, initial_rows=row)
    monkeypatch.setattr(controller, 'translate_source_text', lambda *a, **kw: 'New single translation')
    result = arbiter.retext_session(zid, language='de')
    assert result['sentences'] == {1: 'New single translation'}
    assert adapter.db.get_sentences_by_session(zid)[0]['sentence_destination'] == 'New single translation'


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'])
@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'])
def test_ini_preference_preserves_unrelated_bytes(tmp_path, newline, bom):
    original=bom+newline.join([b'; keep comment',b'[rendering]',b'highlight_same_lemma = false',
                              b'',b'[settings]',b'default_language = en',b''])
    target=tmp_path/'config.ini'
    target.write_bytes(original)
    assert desk.persist_config_value('rendering','highlight_same_lemma','true',base_dir=tmp_path)
    assert target.read_bytes()==original.replace(b'highlight_same_lemma = false',b'highlight_same_lemma = true')


def test_delivery_failure_reports_committed_sqlite_without_retranslation(tmp_path):
    arbiter, adapter, zid, tsv = _make_persist_fixture(tmp_path)
    before_artifact = tsv.read_bytes()
    script = '''
import json, sys
from pathlib import Path
import kardenwort_controller as controller
import kardenwort_desk as desk
from kardenwort_delivery import controller_producer
config, paths, _, _ = desk.load_config()
config.set('storage','backend','sqlite')
config.set('server','controller_port',sys.argv[3])
paths['sqlite_db_path']=sys.argv[1]
paths['results_dir']=str(Path(sys.argv[1]).parent/'results')
arbiter=controller.SessionArbiter(config,paths)
arbiter.storage_adapter=desk.SqliteStorageAdapter(config,paths,db_path=sys.argv[1])
calls=[]
def provider(*args,**kwargs):
    calls.append(1)
    return {0:'Committed one',1:'Committed two'}
controller.translate_source_text=provider
with controller_producer():
    try:
        arbiter.retext_session(sys.argv[2],language='de')
    except desk.StructuredError as error:
        assert error.details['committed'] is True
        assert 'committed' in error.message
    else:
        raise AssertionError('Expected delivery failure')
assert len(calls)==1
print('committed-with-delivery-failure')
'''
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        result = subprocess.run([sys.executable, '-c', script, str(adapter.db.db_path), zid,
                                 str(reserved.getsockname()[1])], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'committed-with-delivery-failure' in result.stdout
    assert [record['sentence_destination'] for record in adapter.db.get_sentences_by_session(zid)] == ['Committed one', 'Committed two']
    assert tsv.read_bytes() == before_artifact


def test_full_subscriber_queue_is_diagnostic(caplog):
    from kardenwort_events import EventDispatcher
    dispatcher = EventDispatcher()
    subscriber = dispatcher.register('20261024225159')
    for index in range(1000):
        subscriber.put_nowait({'index': index})
    dispatcher.publish('20261024225159', {'stage': 'finished'})
    assert subscriber.qsize() == 1000
    assert 'recover committed state through status' in caplog.text
