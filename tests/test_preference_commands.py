"""Preference acknowledgments follow real owned persistence, never memory alone."""
from concurrent.futures import ThreadPoolExecutor
import configparser
import json
import stat
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import kardenwort_desk as desk
from tests.controller_test_support import controller_lifetime


ORIGINAL = (b'; retain this comment\r\n[rendering]\r\nhighlight_same_lemma = false\r\n'
            b'filter_selected_only = false\r\n[unrelated]\r\nvalue = untouched\r\n')


def command(url, body, token='test-controller-api-key'):
    request = Request(url+'/api/v1/config', json.dumps(body).encode(),
        {'Content-Type': 'application/json', 'X-API-Token': token})
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        data = json.load(response)
        return response.status, data.get('data', data)


@pytest.fixture
def preference_world(tmp_path):
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999)) as (url, server):
        for key in ('highlight_same_lemma', 'filter_selected_only'):
            server.config.set('rendering', key, 'false')
        server.ui_cfg = desk.resolve_ui_config(server.config)
        path = server.resolved_paths['base_dir']/'config.ini'
        path.write_bytes(ORIGINAL)
        yield url, server, path


def test_failed_preference_write_never_acknowledges_or_publishes(preference_world):
    # Phase 3: SQLite replaces the preference-file owner; a real write lock fails commit.
    import sqlite3
    from kardenwort_state_runtime import preference_store
    url, server, path = preference_world
    store = preference_store(server.config, server.resolved_paths)
    store.db.busy_timeout_ms = 10
    blocker = sqlite3.connect(store.db.db_path, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        status, data = command(url, {'filter_selected_only': True})
        assert status == 500, data
        assert data['error_code'] == 'CONFIGURATION_ERROR'
        assert server.config.getboolean('rendering', 'filter_selected_only') is False
        assert server.ui_cfg['filter_selected_only'] is False
        with urlopen(url+'/api/v1/config', timeout=10) as response:
            current = json.load(response)
        assert current.get('data', current)['ui']['filter_selected_only'] is False
        assert path.read_bytes() == ORIGINAL
    finally:
        blocker.rollback(); blocker.close()


@pytest.mark.parametrize('value', [None, '', 'garbage', 2, -1, [], {}, 1.0])
def test_invalid_boolean_has_no_effect(preference_world, value):
    url, server, path = preference_world
    status, data = command(url, dict(section='rendering', key='filter_selected_only', value=value))
    assert status == 400, data
    assert data['error_code'] == 'INVALID_PAYLOAD'
    assert path.read_bytes() == ORIGINAL
    assert server.config.getboolean('rendering', 'filter_selected_only') is False


@pytest.mark.parametrize('value', [True, 'true', 'TRUE', 'yes', 'on', '1', 1])
def test_typed_boolean_and_alias_commit_before_success(preference_world, value):
    url, server, path = preference_world
    status, data = command(url, dict(section='Rendering', key='FILTER_SELECTED_ONLY', value=value))
    assert status == 200, data
    assert data['value'] == 'true'
    assert path.read_bytes() == ORIGINAL
    from kardenwort_state_runtime import preference_store
    assert preference_store(server.config, server.resolved_paths).get('filter_selected_only') is True
    assert server.config.getboolean('rendering', 'filter_selected_only') is True
    assert server.ui_cfg['filter_selected_only'] is True
    status, data = command(url, {'filter_selected_only': False})
    assert status == 200, data
    assert path.read_bytes() == ORIGINAL


def test_concurrent_independent_preferences_preserve_both_commits(preference_world):
    url, server, path = preference_world
    keys = ['highlight_same_lemma', 'filter_selected_only'] * 10
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda key: command(url, {key: True}), keys))
    assert all(status == 200 for status, data in results), results
    from kardenwort_state_runtime import preference_store
    stored = preference_store(server.config, server.resolved_paths)
    assert all(stored.get(key) for key in set(keys))
    assert path.read_bytes() == ORIGINAL
    assert all(server.config.getboolean('rendering', key) for key in set(keys))
    assert b'; retain this comment\r\n' in path.read_bytes()
    assert b'[unrelated]\r\nvalue = untouched\r\n' in path.read_bytes()


def test_unauthorized_and_unlisted_commands_have_no_effect(preference_world):
    url, server, path = preference_world
    status, data = command(url, {'filter_selected_only': True}, token='invalid')
    assert status == 403, data
    status, data = command(url, dict(section='settings', key='api_key', value='invalid'))
    assert status == 400, data
    assert path.read_bytes() == ORIGINAL


def test_language_and_preference_writes_share_serialized_owner(tmp_path):
    path = tmp_path/'config.ini'
    path.write_bytes(ORIGINAL+b'[settings]\r\ndefault_language = en\r\n')
    operations = [lambda: desk.persist_default_language('de', base_dir=tmp_path),
                  lambda: desk.persist_config_value('rendering', 'filter_selected_only', 'true', base_dir=tmp_path)] * 10
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda operation: operation(), operations))
    config = configparser.ConfigParser()
    config.read(path, encoding='utf-8')
    assert config.get('settings', 'default_language') == 'de'
    assert config.getboolean('rendering', 'filter_selected_only') is True
    assert list(tmp_path.glob('.config-*.tmp')) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows read-only destination replacement contract')
def test_readonly_static_configuration_does_not_block_sqlite_commit(preference_world):
    url, server, path = preference_world
    path.chmod(stat.S_IREAD)
    try:
        status, data = command(url, {'filter_selected_only': True})
        assert status == 200, data
        assert data['committed_value'] is True
        assert path.read_bytes() == ORIGINAL
        assert server.config.getboolean('rendering', 'filter_selected_only') is True
        assert server.ui_cfg['filter_selected_only'] is True
        assert list(path.parent.glob('.config-*.tmp')) == []
    finally:
        path.chmod(stat.S_IWRITE)


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'])
@pytest.mark.parametrize('terminated', [False, True])
def test_serialized_language_writer_preserves_unrelated_bytes(tmp_path, newline, terminated):
    original = newline.join([b'; keep this', b'[settings]', b'default_language = en',
                             b'[rendering]', b'filter_selected_only = false'])
    if terminated:
        original += newline
    path = tmp_path/'config.ini'
    path.write_bytes(original)
    assert desk.persist_default_language('de', base_dir=tmp_path)
    assert path.read_bytes() == original.replace(b'default_language = en', b'default_language = de')
