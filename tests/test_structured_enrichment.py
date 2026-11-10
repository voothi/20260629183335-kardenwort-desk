"""Canonical enrichment: real HTTP command/SQLite effects, external provider port only."""
import os
import sys
from contextlib import contextmanager

import pytest

import kardenwort_desk as desk
from tests.reword_contract_support import ZID, backend, provider_name, drain
from tests.release_projection_support import checked_world as world


RUN_PROVIDER = desk.run_headless_intellifiller
_observers = []


def _observe_writes(event, args):
    if event in ('os.mkdir', 'os.rename') and _observers:
        path = args[1] if event == 'os.rename' else args[0]
        for observed in _observers:
            observed.append(os.fsdecode(path))
        return
    if event != 'open' or not _observers:
        return
    path, mode, flags = args
    if isinstance(path, (str, bytes)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
        for observed in _observers:
            observed.append(os.fsdecode(path))


sys.addaudithook(_observe_writes)


@contextmanager
def control_writes():
    observed = []
    _observers.append(observed)
    try:
        yield observed
    finally:
        _observers.remove(observed)


def prohibited(paths):
    return [path for path in paths if path.lower().endswith(('.tsv', '.txt', '.lock', '.js'))
            or '.updates' in path or 'done' in path.rsplit(os.sep, 1)[-1]]


def configure(world, monkeypatch, response):
    if not world.config.has_section('services'):
        world.config.add_section('services')
    world.config.set('services', 'intellifiller_server_url', 'http://provider.invalid')
    monkeypatch.setattr(desk, 'run_headless_intellifiller', RUN_PROVIDER)
    calls = []

    def respond(rows, **kwargs):
        calls.append((rows, kwargs))
        return response(rows)

    monkeypatch.setattr(desk, 'query_intellifiller_server', respond)
    return calls


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_reword_structured_batch_has_no_control_files_and_survives_cold_restore(world, monkeypatch):
    calls = configure(world, monkeypatch, lambda rows: dict(status='success', enriched_rows=[
        dict(row_id=row['row_id'], WordDestination='fresh-' + row['WordSource']) for row in reversed(rows)]))
    unrelated = world.adapter.db.get_words_by_session(ZID)[1]
    with control_writes() as writes:
        status, result = world.post(world.payload(target_coordinates=[dict(sentence_idx=1, token_order=0, visual_idx=world.visual('Haus'))]))
    assert status == 200, result
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}
    assert world.adapter.db.get_words_by_session(ZID)[1] == unrelated
    cold = desk.SqliteStorageAdapter(world.config, world.paths)
    assert cold.db.get_words_by_session(ZID)[0]['word_provenance'] == 'live:intellifiller'
    assert cold.db.get_words_by_session(ZID)[0]['word_destination'] == 'fresh-Haus'
    assert len(calls) == 1
    assert '[Haus]' in calls[0][0][0]['SentenceSource']
    assert prohibited(writes) == []


@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('damage', ['offline', 'missing', 'duplicate', 'unknown', 'malformed', 'identity', 'empty',
                                  'failed-sentinel', 'error-sentinel', 'row-error'])
def test_rejected_structured_batch_has_no_effects_events_or_control_files(world, monkeypatch, damage):
    def response(rows):
        item = dict(row_id=rows[0]['row_id'], WordDestination='fresh-Haus')
        items = {
            'missing': [], 'duplicate': [item, item], 'unknown': [dict(item, row_id=99)],
            'malformed': [dict(item, WordDestination=['wrong'])],
            'identity': [dict(item, TokenOrder='99')], 'empty': [dict(item, WordDestination='')],
            'failed-sentinel': [dict(item, WordDestination='[FAILED]')],
            'error-sentinel': [dict(item, WordDestination='[Error: provider failed]')],
            'row-error': [dict(item, status='error')],
        }
        return None if damage == 'offline' else dict(status='success', enriched_rows=items[damage])

    configure(world, monkeypatch, response)
    before, channel = world.snapshot(), world.subscribe(ZID)
    with control_writes() as writes:
        status, result = world.post(world.payload())
    assert status >= 400, result
    assert world.snapshot() == before
    assert drain(channel) == []
    assert prohibited(writes) == []
