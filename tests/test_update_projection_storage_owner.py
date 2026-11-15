"""Stage projection reads its explicit owner without initializing another store."""
import configparser
import json
from pathlib import Path
import re
import sqlite3
import sys

import pytest

import kardenwort_desk as desk
from tests.controller_test_support import controller_world


def payload(path):
    match = re.search(r'window.receiveUpdate\((\{.*\})\);', path.read_text(), re.S)
    return json.loads(match.group(1))


@pytest.fixture
def observe_sqlite_opens():
    attempts, phase = [], ['observing']

    def observe(event, args):
        if phase[0] != 'observing' or event != 'sqlite3.connect':
            return
        raw = str(args[0])
        attempts.append(raw)
        if '?mode=rwc' in raw:
            raise AssertionError('Projection attempted an initializing database connection')

    sys.addaudithook(observe)
    try:
        yield attempts
    finally:
        phase[0] = 'finished'


@pytest.mark.parametrize('context', ['paths', 'adapter'])
def test_update_projection_hydrates_only_its_readonly_owner(controller_world, tmp_path, request, context):
    _, server = controller_world
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    zid = '20261115102500'
    headers = ['TokenOrder', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination']
    rows = [['0', 'owned', 'word', '1', 'Owned source.', 'OWNED TRANSLATION']]
    adapter.save_session(session_zid=zid, slug='owned', source_language='en', target_language='ru',
        text_mode='single', source_raw_text='Owned source.', headers=headers, data_rows=rows)
    assert adapter.db.get_sentences_by_session(zid)[0]['sentence_destination'] == 'OWNED TRANSLATION'
    before = adapter.db.db_path.read_bytes()
    config = configparser.ConfigParser(interpolation=None)
    config.read_dict({'storage': {'backend': 'sqlite'}, 'server': {'update_delivery': 'desktop_file'}})
    options = {'resolved_paths': server.resolved_paths} if context == 'paths' else {'storage_adapter': adapter}
    attempts = request.getfixturevalue('observe_sqlite_opens')
    path = desk.write_update_js(tmp_path/(zid+'-owned.tsv'), rows, headers,
        {'lemma': 'WordSource', 'word_translation': 'WordDestination'},
        stage='finished', zid=zid, config=config, **options)
    result = payload(path)
    assert result['translatedText'] == 'OWNED TRANSLATION'
    assert result['sentences'][0]['sentence_destination'] == 'OWNED TRANSLATION'
    assert attempts and all('?mode=rw' in path and '?mode=rwc' not in path for path in attempts)
    assert adapter.db.db_path.read_bytes() == before


def test_partial_projection_without_storage_owner_does_not_open_database(tmp_path, observe_sqlite_opens):
    config = configparser.ConfigParser()
    config.read_dict({'storage': {'backend': 'sqlite'}, 'server': {'update_delivery': 'desktop_file'}})
    path = desk.write_update_js(tmp_path/'20261115102600-partial.tsv', [['owned', 'value']],
        ['WordSource', 'WordDestination'], {'lemma': 'WordSource', 'word_translation': 'WordDestination'},
        stage='translated', zid='20261115102600', config=config)
    assert payload(path)['rows']['0']['trans'] == 'value'
    assert observe_sqlite_opens == []


def test_explicit_unavailable_projection_owner_fails_before_delivery(tmp_path):
    config = configparser.ConfigParser()
    config.read_dict({'storage': {'backend': 'sqlite'}, 'server': {'update_delivery': 'desktop_file'}})
    source = tmp_path/'20261115102700-partial.tsv'
    with pytest.raises(sqlite3.OperationalError):
        desk.write_update_js(source, [['owned', 'value']], ['WordSource', 'WordDestination'],
            {'lemma': 'WordSource', 'word_translation': 'WordDestination'}, stage='finished',
            zid='20261115102700', config=config,
            resolved_paths={'sqlite_db_path': tmp_path/'missing.db', 'storage_backend': 'sqlite'})
    assert not source.with_name(source.stem+'.updates').exists()
    assert not (tmp_path/'missing.db').exists()
