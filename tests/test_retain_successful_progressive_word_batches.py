"""Acceptance tests for retaining successful progressive word batches.

Spec: specs/progressive-worker-resilience/spec.md
Requirement: Retain successful word batches when a later batch fails
"""
import pytest

import kardenwort_desk as desk
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    translation_service,
)
from translate_server import TranslationServerException


HEADERS = [
    'TokenOrder',
    'Quotation',
    'WordSource',
    'SentenceSourceIndex',
    'SentenceSource',
    'SentenceDestination',
    'WordDestination',
]


def _configure_progressive(server, *, batch_size=1):
    server.config.read_dict({
        'triggers': {
            'run_text_translation': 'manual',
            'run_lemma_base_translation': 'auto',
            'run_lemma_enrichment': 'manual',
        },
        'translation': {
            'translation_max_retries': '1',
            'translation_retry_backoff': '0',
            'lemma_batch_size': str(batch_size),
        },
        'pipeline': {
            'lemma_reprocess_provider': 'google',
        },
        'settings': {
            'anki_context_mode': 'single',
            'anki_context_words_before': '0',
            'anki_context_words_after': '0',
            'anki_translated_context_words_before': '0',
            'anki_translated_context_words_after': '0',
        },
    })


def _seed_session(server, zid, *, text, rows, text_mode='single', language='de', target_lang='ru'):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    adapter.save_session(
        session_zid=zid,
        slug='progressive-word-batch',
        source_language=language,
        target_language=target_lang,
        text_mode=text_mode,
        source_raw_text=text,
        headers=HEADERS,
        data_rows=rows,
    )
    server.arbiter.sessions[zid] = {
        'text': text,
        'comments': [],
        'headers': list(HEADERS),
        'data_rows': [list(row) for row in rows],
        'language': language,
        'target_lang': target_lang,
        'text_mode': text_mode,
        'tsv_path': zid,
    }
    return adapter


class ItemAwareTranslationHandler(DeterministicTranslationHandler):
    """Owned HTTP translation handler that prefixes both newline- and semicolon-joined segments."""

    def translate(self, provider, text, source, target):
        self.server.provider_calls.append((text, source, target))
        lines = []
        for line in text.splitlines():
            if '; ' in line:
                lines.append('; '.join(f'{provider}: {part}' for part in line.split('; ')))
            else:
                lines.append(f'{provider}: {line}')
        return '\n'.join(lines)


def _run_progressive(server, zid, *, language='de', target_lang='ru', text_mode='single'):
    subscriber = server.arbiter.register_subscriber(zid)
    events = []
    try:
        enqueued = server.arbiter.enrichment_queue.enqueue_progressive_task(
            zid,
            server.arbiter,
            language=language,
            target_lang=target_lang,
            text_mode=text_mode,
            skip_intellifiller=True,
        )
        assert enqueued['status'] == 'queued'
        with server.arbiter.enrichment_queue._lock:
            future = server.arbiter.enrichment_queue._active_progressive_sessions.get(zid)
        if future is not None:
            future.result(timeout=15)
        while not subscriber.empty():
            events.append(subscriber.get_nowait())
        if not any(e.get('stage') == 'finished' for e in events):
            while True:
                event = subscriber.get(timeout=15)
                events.append(event)
                if event.get('stage') == 'finished':
                    break
            while not subscriber.empty():
                events.append(subscriber.get_nowait())
    finally:
        server.arbiter.unregister_subscriber(zid, subscriber)
    return events


def test_progressive_word_batches_success_control(tmp_path):
    """Adjacent control: all progressive word batches succeed, commit cleanly to SQLite, and finish with success."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server, batch_size=1)
            zid = '20261121085301'
            text = 'Das Haus und der Baum.'
            rows = [
                ['0', 'Haus', 'Haus', '1', text, 'google: Das Haus und der Baum.', ''],
                ['1', 'Baum', 'Baum', '1', text, 'google: Das Haus und der Baum.', ''],
            ]
            adapter = _seed_session(server, zid, text=text, rows=rows)

            events = _run_progressive(server, zid)

            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            assert words['Haus']['word_destination'] == 'google: Haus'
            assert words['Haus']['word_provenance'] == 'live:google'
            assert words['Baum']['word_destination'] == 'google: Baum'
            assert words['Baum']['word_provenance'] == 'live:google'
            assert worker['worker_status'] == 'finished'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'success'
            assert finished['error'] is None


def test_progressive_word_batches_later_batch_fails_retains_first_batch(tmp_path):
    """Scenario: Later batch fails.

    Batch 1 validates successfully and batch 2 fails.
    Batch 1 remains in SQLite with exact occurrence identities and provenance,
    batch 2 remains unchanged, and final worker status truthfully reports failure.
    """

    class SecondBatchFailingHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if 'Baum' in text:
                self.server.provider_calls.append((text, source, target))
                raise TranslationServerException(
                    503,
                    'ERR_NETWORK_UNREACHABLE',
                    'Owned provider failed on batch 2',
                )
            return super().translate(provider, text, source, target)

    with translation_service(SecondBatchFailingHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server, batch_size=1)
            zid = '20261121085302'
            text = 'Das Haus und der Baum.'
            rows = [
                ['0', 'Haus', 'Haus', '1', text, 'google: Das Haus und der Baum.', ''],
                ['1', 'Baum', 'Baum', '1', text, 'google: Das Haus und der Baum.', ''],
            ]
            adapter = _seed_session(server, zid, text=text, rows=rows)

            events = _run_progressive(server, zid)

            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            # Prior successful batch 1 facts SHALL be retained in SQLite
            assert words['Haus']['word_destination'] == 'google: Haus'
            assert words['Haus']['word_provenance'] == 'live:google'

            # Rejected batch 2 facts remain unchanged/empty
            assert (words['Baum']['word_destination'] or '') == ''

            # Final worker status truthfully reports failure
            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert finished['error'].get('message')


def test_progressive_word_batches_malformed_batch_retains_first_batch(tmp_path):
    """Scenario: Malformed batch.

    When a batch omits or mismatches a requested occurrence, none of that batch is
    committed and earlier committed batches remain intact.
    """

    class MalformedSecondBatchHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if 'Baum' in text:
                self.server.provider_calls.append((text, source, target))
                # Return empty translation causing fast_provider_results to reject the batch
                return ''
            return super().translate(provider, text, source, target)

    with translation_service(MalformedSecondBatchHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server, batch_size=1)
            zid = '20261121085303'
            text = 'Das Haus und der Baum.'
            rows = [
                ['0', 'Haus', 'Haus', '1', text, 'google: Das Haus und der Baum.', ''],
                ['1', 'Baum', 'Baum', '1', text, 'google: Das Haus und der Baum.', ''],
            ]
            adapter = _seed_session(server, zid, text=text, rows=rows)

            events = _run_progressive(server, zid)

            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            # Prior successful batch 1 facts SHALL be retained in SQLite
            assert words['Haus']['word_destination'] == 'google: Haus'
            assert words['Haus']['word_provenance'] == 'live:google'

            # Malformed batch 2 was not committed
            assert (words['Baum']['word_destination'] or '') == ''

            # Final worker status truthfully reports failure
            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert finished['error'].get('message')


def test_explicit_reword_remains_all_or_nothing_control(tmp_path):
    """Adjacent control: explicit user Re-word remains separate and all-or-nothing.

    If a chunk fails during explicit reword, no partial reword commit occurs.
    """

    class SecondBatchFailingHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if 'Baum' in text:
                self.server.provider_calls.append((text, source, target))
                raise TranslationServerException(
                    503,
                    'ERR_NETWORK_UNREACHABLE',
                    'Owned provider failed on batch 2',
                )
            return super().translate(provider, text, source, target)

    with translation_service(SecondBatchFailingHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server, batch_size=1)
            zid = '20261121085304'
            text = 'Das Haus und der Baum.'
            rows = [
                ['0', 'Haus', 'Haus', '1', text, 'google: Das Haus und der Baum.', ''],
                ['1', 'Baum', 'Baum', '1', text, 'google: Das Haus und der Baum.', ''],
            ]
            adapter = _seed_session(server, zid, text=text, rows=rows)

            # User triggers explicit reword on both rows [0, 1]
            with pytest.raises(desk.StructuredError) as exc_info:
                server.arbiter.reword_session(
                    session_zid=zid,
                    selected_rows=[0, 1],
                    language='de',
                )

            # Reword raises StructuredError on failure
            assert 'Re-word failed' in str(exc_info.value)

            # All-or-nothing: NEITHER word was committed in SQLite
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            assert (words['Haus']['word_destination'] or '') == ''
            assert (words['Baum']['word_destination'] or '') == ''
