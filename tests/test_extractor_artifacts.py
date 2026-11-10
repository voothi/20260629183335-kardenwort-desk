"""Observe both parent and actual extractor-child working directories on Windows."""
import os
import subprocess
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import kardenwort_desk as desk
from tests.controller_test_support import (
    DeterministicTranslationHandler, owned_configuration, token_service, translation_service,
)
from tests.test_structured_enrichment import prohibited


class CreatedPaths(FileSystemEventHandler):
    def __init__(self, barrier):
        self.barrier = str(barrier)
        self.observed = []
        self.drained = threading.Event()

    def on_created(self, event):
        self.observed.append(event.src_path)
        if event.src_path == self.barrier:
            self.drained.set()

    def on_moved(self, event):
        self.observed.append(event.dest_path)


def test_actual_structured_extractor_child_creates_no_control_artifacts(tmp_path, monkeypatch):
    with translation_service(DeterministicTranslationHandler) as translator, token_service() as tokenizer:
        config, paths, _ = owned_configuration(tmp_path, f'http://127.0.0.1:{translator.server_port}', 'sqlite')
        config.set('services', 'spacy_server_url', f'http://127.0.0.1:{tokenizer.server_port}')
        temporary = tmp_path/'temporary'
        temporary.mkdir()
        monkeypatch.setenv('TEMP', str(temporary))
        monkeypatch.setenv('TMP', str(temporary))
        original = subprocess.run
        calls = []

        def execute(command, **kwargs):
            assert '--structured-output' in command
            assert '--output-file' not in command and '--text1-file' not in command
            calls.append(command)
            return original(command, cwd=tmp_path, **kwargs)

        monkeypatch.setattr(subprocess, 'run', execute)
        barrier = tmp_path/'observed.barrier'
        events = CreatedPaths(barrier)
        observer = Observer()
        observer.schedule(events, str(tmp_path), recursive=True)
        observer.start()
        try:
            identity = desk.prepare_lookup_tsv('One house.', 'en', 'ru', config, paths,
                '20261110195600', ttl_seconds=0, cache_key='20261110195600-one-house.en.tsv')
            barrier.write_bytes(b'complete')
            assert events.drained.wait(10), 'Filesystem observer did not drain through the owned barrier'
            assert len(calls) == 1 and identity == '20261110195600'
            restored = desk.SqliteStorageAdapter(config, paths).restore_session(identity)
            assert {word['lemma'] for word in restored['words']} == {'one', 'house'}
            assert prohibited(events.observed) == []
        finally:
            observer.stop()
            observer.join(timeout=10)
            assert not observer.is_alive()
