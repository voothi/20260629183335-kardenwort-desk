"""Phase 2 membership contracts using real adapters, files and authenticated HTTP."""
import configparser
import json
import sqlite3
from pathlib import Path

import pytest

import kardenwort_desk as desk
from kardenwort_delivery import delivery_sinks
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import (
    ZID, OTHER_ZID, boundary_html, data, post, seed_session, snapshot, use_backend,
)


@pytest.mark.parametrize('optional_host', ['absent', 'null', 'missing-api', 'null-card'])
def test_legacy_overview_optional_host_returns_false(page, tmp_path, optional_host):
    page.set_content(boundary_html(tmp_path))
    host = {'absent': 'delete window.WorkspaceTabs', 'null': 'window.WorkspaceTabs = null',
            'missing-api': 'window.WorkspaceTabs = {}',
            'null-card': 'window.WorkspaceTabs = {getActiveCard: function() { return null; }}'}[optional_host]
    assert page.evaluate('() => {' + host + '; return isLegacyOverviewContext();}') is False


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('delivery,diskless', [('controller', False), ('desktop_file', False),
                                            ('mixed', False), ('mixed', True)])
def test_cold_membership_read_preserves_delivery_policy_and_files(running_controller, tmp_path, backend, delivery, diskless):
    url, server, _ = running_controller
    adapter = use_backend(server, backend)
    seed_session(server, adapter, selected=True)
    server.config.set('server', 'update_delivery', delivery)
    server.config.set('server', 'diskless_updates', str(diskless).lower())
    expected = ('controller',) if diskless else {'controller': ('controller',), 'desktop_file': ('desktop_file',),
                                               'mixed': ('controller', 'desktop_file')}[delivery]
    before = snapshot(tmp_path)
    del server.arbiter.storage_adapter
    response_status, response = post(url, '/internal/selection', dict(session_zid=ZID, client_id='cold-policy'))
    assert response_status == 200, response
    assert data(response) == dict(ok=True, shared_selection={'haus': True, 'baum': False}, events=[])
    assert delivery_sinks(server.config) == expected
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_unavailable_storage_is_structured_and_creates_nothing(running_controller, tmp_path, backend):
    url, server, _ = running_controller
    use_backend(server, backend)
    del server.arbiter.storage_adapter
    missing = tmp_path / 'unavailable' / 'session.db'
    server.resolved_paths.update(sqlite_db_path=missing, results_dir=tmp_path / 'unavailable-results',
                                 generated_results_dir=tmp_path / 'unavailable-results')
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status in (404, 500), response
    assert response['status'] == 'error'
    assert response['error_code'] in ('NOT_FOUND', 'QUERY_FAILED')
    assert 'shared_selection' not in data(response)
    assert snapshot(tmp_path) == before


def test_cold_sqlite_read_skips_migrations_and_legacy_data_sanitization(running_controller, tmp_path):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter, selected=True)
    # If the read constructs a writable adapter, its automatic sanitization
    # changes this pre-existing legacy payload. No internal migration mock.
    with adapter.db.get_connection() as conn:
        conn.execute('UPDATE words SET extra_fields = ? WHERE session_zid = ?',
                     (json.dumps({'extra_fields': {'custom': 'untouched'}}), ZID))
    del server.arbiter.storage_adapter
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 200, response
    assert data(response)['shared_selection'] == {'haus': True, 'baum': False}
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_membership_normalizes_lemmas_and_selection_without_changing_occurrences(running_controller, tmp_path, backend):
    url, server, _ = running_controller
    adapter = use_backend(server, backend)
    headers = ['TokenOrder', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'DeskSelected']
    rows = [['10', ' Haus ', 'one', '1', 'First Haus.', ' true '],
            ['11', 'hAUS', 'two', '2', 'Second Haus.', '0'],
            ['12', 'Baum', 'tree', '2', 'Second Haus.', '0']]
    path = Path(server.resolved_paths['results_dir']) / (ZID + '-normalized.de.tsv')
    adapter.save_session(session_zid=ZID, slug='normalized', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='First Haus. Second Haus.',
                         headers=headers, data_rows=rows, working_tsv_path=path)
    seed_session(server, adapter, zid=OTHER_ZID)
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 200, response
    assert data(response)['shared_selection'] == {'haus': True, 'baum': False}
    assert snapshot(tmp_path) == before
    other_status, other = post(url, '/internal/selection', dict(session_zid=OTHER_ZID))
    assert other_status == 200
    assert data(other)['shared_selection'] == {'haus': False, 'baum': False}
    restored = adapter.restore_session(ZID)
    columns = restored['headers']
    assert [(row[columns.index('TokenOrder')], row[columns.index('SentenceSourceIndex')], row[columns.index('WordDestination')])
            for row in restored['data_rows']] == [('10', '1', 'one'), ('11', '2', 'two'), ('12', '2', 'tree')]


def test_tsv_membership_uses_configured_roles_and_results_directory(running_controller, tmp_path):
    url, server, _ = running_controller
    adapter = use_backend(server, 'tsv')
    actual = tmp_path / 'configured-results'
    actual.mkdir()
    mapping = tmp_path / 'membership-mapping.ini'
    mapping.write_text('[desk_columns]\nBaseForm=lemma\nChosen=selected\n', encoding='utf-8')
    server.resolved_paths.update(generated_results_dir=actual, anki_mapping_file=mapping)
    path = actual / (ZID + '-configured.de.tsv')
    adapter.save_session(session_zid=ZID, slug='configured', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Haus Baum', working_tsv_path=path, headers=['BaseForm', 'Chosen'],
                         data_rows=[[' Haus ', 'True'], ['Baum', ''], ['haus', '0']])
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 200, response
    assert data(response)['shared_selection'] == {'haus': True, 'baum': False}
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('malformed', ['missing-lemma', 'missing-selection', 'short-row', 'empty-lemma'])
def test_invalid_tsv_records_fail_explicitly_without_fabricated_membership(running_controller, tmp_path, malformed):
    url, server, _ = running_controller
    use_backend(server, 'tsv')
    texts = {'missing-lemma': 'DeskSelected\n1\n', 'missing-selection': 'WordSource\nHaus\n',
             'short-row': 'WordSource\tDeskSelected\nHaus\n', 'empty-lemma': 'WordSource\tDeskSelected\n\t1\n'}
    path = Path(server.resolved_paths['results_dir']) / (ZID + '-invalid.de.tsv')
    path.write_text(texts[malformed], encoding='utf-8')
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 500, response
    assert response['error_code'] == 'QUERY_FAILED'
    assert 'shared_selection' not in data(response)
    assert snapshot(tmp_path) == before


def test_deleted_sqlite_session_is_unavailable_without_tsv_fallback(running_controller, tmp_path):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter, selected=True)
    with adapter.db.get_connection() as conn:
        conn.execute('UPDATE sessions SET deleted_at = ? WHERE zid = ?', ('2026-10-25', ZID))
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 404, response
    assert response['error_code'] == 'NOT_FOUND'
    assert snapshot(tmp_path) == before


def test_read_only_factory_rejects_writes_and_preserves_missing_parent(tmp_path):
    config = configparser.ConfigParser()
    config['storage'] = {'backend': 'sqlite'}
    adapter = desk.get_storage_adapter(config, dict(sqlite_db_path=tmp_path / 'missing' / 'db.sqlite'), read_only=True)
    with pytest.raises(ValueError, match='Read-only database'):
        adapter.db.get_connection()
    with pytest.raises(desk.StructuredError) as error:
        adapter.get_shared_selection(ZID)
    assert error.value.error_code == 'QUERY_FAILED'
    assert list(tmp_path.iterdir()) == []


def test_cold_read_observes_committed_wal_and_rejects_sql_mutations(running_controller, tmp_path):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    writer = adapter.db.get_connection()
    try:
        writer.execute('UPDATE words SET selected = 1 WHERE session_zid = ? AND token_order = 0', (ZID,))
        writer.commit()
        assert adapter.db.wal_path.stat().st_size > 0
        before = snapshot(tmp_path)
        del server.arbiter.storage_adapter
        status, response = post(url, '/internal/selection', dict(session_zid=ZID))
        assert status == 200, response
        assert data(response)['shared_selection'] == {'haus': True, 'baum': False}
        after = snapshot(tmp_path)
        # SQLite changes the existing SHM reader bookkeeping with a live WAL.
        # Require identical data/WAL bytes and paths, and unchanged SHM size.
        shm = adapter.db.shm_path.relative_to(tmp_path).as_posix()
        assert before.keys() == after.keys()
        assert before[shm][0] == after[shm][0]
        assert {k: v for k, v in after.items() if k != shm} == {k: v for k, v in before.items() if k != shm}
        readonly = desk.get_storage_adapter(server.config, server.resolved_paths, read_only=True)
        with readonly.db.get_connection(read_only=True) as conn:
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                conn.execute('UPDATE words SET selected = 0 WHERE session_zid = ?', (ZID,))
        assert readonly.get_shared_selection(ZID) == {'haus': True, 'baum': False}
    finally:
        writer.close()


def test_missing_sqlite_schema_is_not_initialized_by_poll(running_controller, tmp_path):
    url, server, _ = running_controller
    use_backend(server, 'sqlite')
    path = tmp_path / 'uninitialized.sqlite'
    sqlite3.connect(path).close()
    server.resolved_paths['sqlite_db_path'] = path
    del server.arbiter.storage_adapter
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 500, response
    assert response['error_code'] == 'QUERY_FAILED'
    assert snapshot(tmp_path) == before


def test_missing_database_is_not_recreated_by_an_existing_adapter(running_controller, tmp_path):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    adapter.db.db_path.unlink()
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 500, response
    assert response['error_code'] == 'QUERY_FAILED'
    assert snapshot(tmp_path) == before


def test_unsupported_backend_is_a_configuration_failure(running_controller, tmp_path):
    url, server, _ = running_controller
    server.resolved_paths['storage_backend'] = 'unsupported'
    del server.arbiter.storage_adapter
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', dict(session_zid=ZID))
    assert status == 500, response
    assert response['error_code'] == 'CONFIGURATION_ERROR'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('payload', [[], 'session', {'session_zid': True}, {'session_zid': ['20261023221555']},
                                   {'session_zid': '../20261023221555'}, {'session_zid': '00000000000000'}])
def test_invalid_request_shapes_fail_before_storage_mutation(running_controller, tmp_path, payload):
    url, server, _ = running_controller
    before = snapshot(tmp_path)
    status, response = post(url, '/internal/selection', payload)
    assert status == 400, response
    assert response['error_code'] == 'INVALID_PAYLOAD'
    assert snapshot(tmp_path) == before
