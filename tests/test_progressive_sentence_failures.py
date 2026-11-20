"""Real controller, owned HTTP provider, and SQLite trigger acceptance for progressive sentence failures."""
import json
import urllib.request

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


def _configure_progressive(server, *, padded=False):
    server.config.read_dict({
        'triggers': {
            'run_text_translation': 'auto',
            'run_lemma_base_translation': 'auto',
            'run_lemma_enrichment': 'manual',
        },
        'translation': {
            'translation_max_retries': '1',
            'translation_retry_backoff': '0',
        },
        'settings': {
            'anki_context_mode': 'single',
            'anki_context_words_before': '0',
            'anki_context_words_after': '0',
            'anki_translated_context_words_before': '2' if padded else '0',
            'anki_translated_context_words_after': '2' if padded else '0',
        },
    })


def _seed_session(server, zid, *, text, rows, text_mode='single', language='de', target_lang='ru'):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    adapter.save_session(
        session_zid=zid,
        slug='progressive-sentence-failure',
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


def _get_status(base_url, server, zid):
    request = urllib.request.Request(
        f'{base_url}/session/status?zid={zid}',
        headers={'X-API-Token': server.api_key},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)['data']


def test_progressive_sentence_success_control(tmp_path):
    """Baseline control: owned provider + real SQLite commit both sentence and lemma cleanly."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050001'
            adapter = _seed_session(
                server,
                zid,
                text='Das Haus.',
                rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']],
            )

            events = _run_progressive(server, zid)

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = adapter.db.get_sentences_by_session(zid)
            words = adapter.db.get_words_by_session(zid)
            worker = adapter.db.get_worker_status(zid)

            assert sentences[0]['sentence_destination'] == 'google: Das Haus.'
            assert sentences[0]['text_provenance'] == 'live:google'
            assert words[0]['word_destination'] == 'google: Haus'
            assert words[0]['word_provenance'] == 'live:google'
            assert translated_text['status'] == 'success'
            assert translated_text['text_translation_failed'] is False
            assert worker['worker_status'] == 'finished'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'success'
            assert finished['error'] is None


def test_progressive_sentence_provider_failure_retains_error_and_continues_lemmas(tmp_path):
    """F3: Owned sentence provider failure continues lemma work and ends failed with retained error."""

    class SentenceFailingHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if text.strip() == 'Das Haus.':
                self.server.provider_calls.append((text, source, target))
                raise TranslationServerException(
                    503,
                    'ERR_NETWORK_UNREACHABLE',
                    'Owned sentence provider unavailable',
                )
            return super().translate(provider, text, source, target)

    with translation_service(SentenceFailingHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050002'
            adapter = _seed_session(
                server,
                zid,
                text='Das Haus.',
                rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']],
            )

            events = _run_progressive(server, zid)

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = adapter.db.get_sentences_by_session(zid)
            words = adapter.db.get_words_by_session(zid)
            worker = adapter.db.get_worker_status(zid)

            # Independent lemma work must continue and persist cleanly.
            assert words[0]['word_destination'] == 'google: Haus'
            assert words[0]['word_provenance'] == 'live:google'
            assert (sentences[0]['sentence_destination'] or '') == ''

            # Stage 1 and terminal completion must truthfully report failure.
            assert translated_text['status'] == 'failed'
            assert translated_text['text_translation_status'] == 'failed'
            assert translated_text['text_translation_failed'] is True
            assert isinstance(translated_text.get('error'), dict)
            assert translated_text['error'].get('message')

            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert finished['error'].get('code')
            assert finished['error'].get('message')
            assert finished['rows']['0']['trans'] == 'google: Haus'


def test_progressive_sentence_sqlite_trigger_rejection_retains_error_and_prevents_false_success(tmp_path):
    """F4: Real SQLite trigger rejecting sentence_destination cannot report success or leak uncommitted text."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (base_url, server):
            _configure_progressive(server)
            zid = '20261120050003'
            adapter = _seed_session(
                server,
                zid,
                text='Das Haus.',
                rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']],
            )
            with adapter.db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_sentence_destination_update "
                    "BEFORE UPDATE OF sentence_destination ON sentences "
                    "BEGIN SELECT RAISE(ABORT, 'owned trigger rejected sentence write'); END"
                )

            events = _run_progressive(server, zid)

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = adapter.db.get_sentences_by_session(zid)
            words = adapter.db.get_words_by_session(zid)
            worker = adapter.db.get_worker_status(zid)

            # Independent lemma work continues; rejected sentence remains uncommitted in SQLite.
            assert words[0]['word_destination'] == 'google: Haus'
            assert words[0]['word_provenance'] == 'live:google'
            assert (sentences[0]['sentence_destination'] or '') == ''

            # Uncommitted sentence translation must not be published or cached as persisted.
            assert translated_text['status'] == 'failed'
            assert translated_text['text_translation_status'] == 'failed'
            assert translated_text['text_translation_failed'] is True
            assert isinstance(translated_text.get('error'), dict)
            assert 'google: Das Haus.' not in (translated_text.get('translated_text') or '')
            assert 'google: Das Haus.' not in json.dumps(translated_text.get('sentences') or [])

            warm_status = _get_status(base_url, server, zid)
            assert 'google: Das Haus.' not in (warm_status.get('translatedText') or '')

            server.arbiter.sessions.clear()
            cold_status = _get_status(base_url, server, zid)
            assert 'google: Das Haus.' not in (cold_status.get('translatedText') or '')

            # Terminal worker status and finished SSE must retain the persistence failure.
            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert finished['error'].get('code')
            assert 'rejected sentence write' in finished['error'].get('message', '')


def test_progressive_sentence_provenance_trigger_not_bypassed_by_retry_without_provenance(tmp_path):
    """F4: A DB rejection on provenance must fail truthfully, never silently retry without provenance."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050004'
            adapter = _seed_session(
                server,
                zid,
                text='Das Haus.',
                rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']],
            )
            with adapter.db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_sentence_provenance_update "
                    "BEFORE UPDATE OF text_provenance ON sentences "
                    "BEGIN SELECT RAISE(ABORT, 'owned trigger rejected provenance write'); END"
                )

            events = _run_progressive(server, zid)

            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = adapter.db.get_sentences_by_session(zid)
            words = adapter.db.get_words_by_session(zid)
            worker = adapter.db.get_worker_status(zid)

            assert words[0]['word_destination'] == 'google: Haus'
            assert (sentences[0]['sentence_destination'] or '') == ''
            assert not sentences[0]['text_provenance']
            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert 'rejected provenance write' in finished['error'].get('message', '')


def test_progressive_sentence_padded_trigger_rejection_retains_error(tmp_path):
    """F4: Real SQLite trigger rejecting padded sentence_destination2 preserves primary commits and fails worker."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server, padded=True)
            zid = '20261120050005'
            text = 'First sentence. Second sentence.'
            adapter = _seed_session(
                server,
                zid,
                text=text,
                text_mode='single',
                language='en',
                rows=[
                    ['0', 'first', 'first', '1', 'First sentence.', '', ''],
                    ['1', 'second', 'second', '2', 'Second sentence.', '', ''],
                ],
            )
            with adapter.db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_padded_sentence_update "
                    "BEFORE UPDATE OF sentence_destination2 ON sentences "
                    "BEGIN SELECT RAISE(ABORT, 'owned trigger rejected padded write'); END"
                )

            events = _run_progressive(server, zid, language='en', text_mode='single')

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = {s['sentence_index']: s for s in adapter.db.get_sentences_by_session(zid)}
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            # Successful primary sentence commits and independent lemma work remain intact.
            assert sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert sentences[2]['sentence_destination'] == 'google: Second sentence.'
            assert (sentences[1]['sentence_destination2'] or '') == ''
            assert (sentences[2]['sentence_destination2'] or '') == ''
            assert words['first']['word_destination'] == 'google: first'
            assert words['second']['word_destination'] == 'google: second'

            # Rejected padded persistence must surface through translated_text, SQLite status, and finished event.
            assert translated_text['status'] == 'failed'
            assert translated_text['text_translation_failed'] is True
            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert 'rejected padded write' in finished['error'].get('message', '')


def test_progressive_sentence_partial_commit_preserves_first_sentence_and_fails_worker(tmp_path):
    """F4 partial: Sentence 1 commits while Sentence 2 is rejected; Sentence 1 and lemmas stay intact, worker fails."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050006'
            text = 'First sentence.\nSecond sentence.'
            adapter = _seed_session(
                server,
                zid,
                text=text,
                text_mode='multi',
                language='en',
                rows=[
                    ['0', 'first', 'first', '1', 'First sentence.', '', ''],
                    ['0', 'second', 'second', '2', 'Second sentence.', '', ''],
                ],
            )
            with adapter.db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_second_sentence_update "
                    "BEFORE UPDATE OF sentence_destination ON sentences "
                    "WHEN NEW.sentence_index = 2 "
                    "BEGIN SELECT RAISE(ABORT, 'owned trigger rejected sentence 2'); END"
                )

            events = _run_progressive(server, zid, language='en', text_mode='multi')

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = {s['sentence_index']: s for s in adapter.db.get_sentences_by_session(zid)}
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            # Committed sentence 1 and independent lemmas stay intact; sentence 2 stays empty without error text.
            assert sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert sentences[1]['text_provenance'] == 'live:google'
            assert (sentences[2]['sentence_destination'] or '') == ''
            assert words['first']['word_destination'] == 'google: first'
            assert words['second']['word_destination'] == 'google: second'

            # Stage 1 and terminal outcomes truthfully report failure without leaking uncommitted sentence 2.
            assert translated_text['status'] == 'failed'
            assert translated_text['text_translation_failed'] is True
            assert 'google: Second sentence.' not in (translated_text.get('translated_text') or '')

            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert 'rejected sentence 2' in finished['error'].get('message', '')
            fin_sentences = {s['sentence_index']: s for s in finished.get('sentences') or []}
            assert fin_sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert fin_sentences[2]['sentence_destination'] == ''
