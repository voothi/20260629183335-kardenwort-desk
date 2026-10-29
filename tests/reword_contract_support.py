"""Explicit two-sentence fixtures; only unmanaged provider responses are replaced."""
import configparser
import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import queue
import tempfile
import threading
import time

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk

ZID = '20261029222449'
SIBLING = '20261029222450'
TOKEN = 'reword-contract-test'
HEADERS = ['TokenOrder', 'Quotation', 'WordSource', 'WordDestination',
           'SentenceSourceIndex', 'SentenceSource', 'WordSourceIPA', 'WordSourceMorphologyAI']
ROWS = [['0', 'Haus', 'Haus', 'house', '1', 'Haus.', '/haus/', 'noun'],
        ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun']]


class RewordRequestHandler(controller.ControllerRequestHandler):
    # Each owned HTTP transport finishes before the fixture removes its database.
    protocol_version = 'HTTP/1.0'


class ProviderResponse:
    def __init__(self):
        self.calls = []
        self.reverse = False

    def translate(self, lemmas, **kwargs):
        self.calls.append(list(lemmas))
        return {lemma: 'fresh-' + lemma for lemma in lemmas}

    def enrich(self, tsv_path, *args, selected_rows=None, **kwargs):
        comments, headers, rows = desk.load_tsv_rows(tsv_path)
        selected = list(range(len(rows))) if selected_rows is None else list(selected_rows)
        self.calls.append([dict(zip(headers, rows[index])) for index in selected])
        for index in selected:
            rows[index][headers.index('WordDestination')] = 'fresh-' + rows[index][headers.index('WordSource')]
        if self.reverse:
            rows.reverse()
        desk.save_tsv_rows_safely(tsv_path, comments, headers, rows)
        return True


class RewordWorld:
    def __init__(self, owner, backend, provider_name):
        self.owner, self.backend = owner, backend
        self.provider = ProviderResponse()
        self.subscriptions = []
        self.zids = []
        mapping = owner / 'mapping.ini'
        mapping.write_text('[desk_columns]\nWordSource=lemma\nWordDestination=word_translation\n'
                           'SentenceSourceIndex=sentence_index\nSentenceSource=sentence\n'
                           'WordSourceIPA=ipa\nWordSourceMorphologyAI=morphology\n'
                           '[desk_editable]\neditable_columns=WordDestination\n', encoding='utf-8')
        self.config = configparser.ConfigParser()
        self.config.read_dict({
            'storage': {'backend': backend, 'sqlite_db_path': str(owner / 'fixture.db'), 'fallback_to_tsv': 'false'},
            'settings': {'default_language': 'de', 'default_target_language': 'en', 'anki_mapping_file': str(mapping)},
            'pipeline': {'lemma_reprocess_provider': provider_name},
            'languages': {'de_prompt': 'fixture'}, 'classification': {'enabled': 'false'},
            'wordfill': {'enabled': 'false'}, 'sentences_mode': {'delivery_mode': 'container'},
            'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                         'run_lemma_enrichment': 'manual'},
            'server': {'api_key': TOKEN}, 'environment': {'kardenwort_workspace': str(owner)},
        })
        self.paths = dict(sqlite_db_path=owner / 'fixture.db', storage_backend=backend,
                          results_dir=owner, generated_results_dir=owner, base_dir=owner,
                          anki_mapping_file=mapping, kardenwort_workspace=owner)
        adapter_type = desk.SqliteStorageAdapter if backend == 'sqlite' else desk.TsvStorageAdapter
        self.adapter = adapter_type(self.config, self.paths)
        self.arbiter = controller.SessionArbiter(self.config, self.paths, wordfill_cfg={'enabled': False})
        self.arbiter.storage_adapter = self.adapter
        self.seed(ZID, ROWS, text='Haus. Baum.')

    def path(self, zid):
        return self.owner / (zid + '.de.tsv')

    def seed(self, zid, rows, text='Haus.', language='de'):
        rows = [list(row) for row in rows]
        self.adapter.save_session(session_zid=zid, slug='contract', source_language=language,
            target_language='en', text_mode='single', source_raw_text=text, headers=HEADERS,
            data_rows=rows, comments=[], working_tsv_path=self.path(zid),
            row_provenances={index: 'fixture:initial' for index in range(len(rows))},
            sentences=[dict(sentence_index=int(row[4]), sentence_source=row[5]) for row in rows])
        comments, cached_headers, cached_rows = self.adapter.load_tsv_rows(self.path(zid))
        self.arbiter.sessions[zid] = dict(session_zid=zid, language=language, target_lang='en',
            text=text, headers=cached_headers, data_rows=cached_rows, comments=comments, tsv_path=str(self.path(zid)),
            role_fields=desk.get_role_fields(desk.load_anki_mapping(self.paths['anki_mapping_file']), cached_headers),
            row_provenances={index: 'fixture:initial' for index in range(len(rows))}, lock=threading.RLock())
        self.zids.append(zid)

    def values(self, zid=ZID):
        _, headers, rows = self.adapter.load_tsv_rows(self.path(zid))
        return {(int(row[headers.index('SentenceSourceIndex')]), int(row[headers.index('TokenOrder')])):
                row[headers.index('WordDestination')] for row in rows}

    def snapshot(self):
        if self.backend == 'sqlite':
            return {zid: self.adapter.db.get_session_bundle(zid) for zid in self.zids}
        return {path.name: path.read_bytes() for path in self.owner.glob('*.tsv')}

    def visual(self, word):
        tokens = desk.tok.build_word_list_internal('Haus. Baum.', keep_spaces=True)
        return next(token['visual_idx'] for token in tokens if token.get('text') == word)

    def targets(self, shape):
        if shape == 'visual':
            return [{'visual_idx': self.visual('Haus'), 'sentence_idx': 99}]
        if shape == 'complete':
            return [dict(sentence_idx=1, token_order=0)]
        if shape == 'legacy':
            return None
        raise AssertionError('Unknown fixture target shape: ' + shape)

    def post(self, body):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request('POST', '/session/reword', body=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json', 'X-API-Token': TOKEN})
            response = connection.getresponse()
            return response.status, json.load(response)
        finally:
            connection.close()

    def payload(self, shape='complete', **kwargs):
        body = dict(session_zid=ZID, language='de', row_ids=[0], target_coordinates=self.targets(shape))
        body.update(kwargs)
        return body

    def subscribe(self, zid):
        channel = self.arbiter.register_subscriber(zid)
        self.subscriptions.append((zid, channel))
        return channel


def drain(channel):
    events = []
    while True:
        try:
            events.append(channel.get_nowait())
        except queue.Empty:
            return events


@pytest.fixture
def backend():
    return 'sqlite'


@pytest.fixture
def provider_name():
    return 'google'


@pytest.fixture
def world(tmp_path, monkeypatch, backend, provider_name):
    # The directory belongs to this fixture and is removed after server/DB users stop.
    temporary = tempfile.TemporaryDirectory(prefix='reword-', dir=tmp_path)
    owner = Path(temporary.name).resolve()
    assert owner.is_relative_to(tmp_path.resolve())
    world = RewordWorld(owner, backend, provider_name)
    if provider_name == 'google':
        monkeypatch.setattr(controller, 'translate_lemmas_fast_path', world.provider.translate)
    else:
        module = desk if backend == 'sqlite' else controller
        monkeypatch.setattr(module, 'run_headless_intellifiller', world.provider.enrich)
    server = ThreadingHTTPServer(('127.0.0.1', 0), RewordRequestHandler)
    server.daemon_threads = False
    server.config, server.resolved_paths = world.config, world.paths
    server.goldendict, server.api_key = {}, TOKEN
    server.seq_counter, server.seq_lock = 0, threading.Lock()
    server.start_time, server.arbiter = time.time(), world.arbiter
    server.supervisor = controller.ProcessSupervisor(world.config, world.paths, enabled=False)
    world.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02})
    thread.start()
    try:
        yield world
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), 'Owned HTTP server did not stop'
        for zid, channel in world.subscriptions:
            world.arbiter.unregister_subscriber(zid, channel)
        world.arbiter.enrichment_queue.shutdown(wait=True)
        temporary.cleanup()
