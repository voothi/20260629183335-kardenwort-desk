"""Real controller, owned HTTP provider, and SQLite trigger acceptance for progressive sentence failures."""
import ast
import json
from pathlib import Path
import threading
import urllib.request

import pytest

import kardenwort_desk as desk
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    translation_service,
)
from tests.test_intent_architecture import complexity, functions
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


def test_progressive_sentence_padded_success_commits_both_fields(tmp_path):
    """Padded sentence path commits both sentence_destination and sentence_destination2 with provenance."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server, padded=True)
            zid = '20261120050007'
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

            events = _run_progressive(server, zid, language='en', text_mode='single')

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = {s['sentence_index']: s for s in adapter.db.get_sentences_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            assert sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert sentences[2]['sentence_destination'] == 'google: Second sentence.'
            assert sentences[1]['sentence_destination2']
            assert sentences[2]['sentence_destination2']
            assert sentences[1]['text_provenance'] == 'live:google'
            assert sentences[2]['text_provenance'] == 'live:google'
            assert translated_text['status'] == 'success'
            assert translated_text['text_translation_failed'] is False
            assert worker['worker_status'] == 'finished'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'success'
            assert finished['error'] is None


def test_progressive_sentence_partial_provider_failure_preserves_sentence1_and_fails_worker(tmp_path):
    """F3 partial: Provider succeeds on sentence 1 and fails on sentence 2; sentence 1 + lemmas stay committed."""

    class SecondSentenceFailingHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if 'Second sentence.' in text:
                self.server.provider_calls.append((text, source, target))
                raise TranslationServerException(
                    503,
                    'ERR_NETWORK_UNREACHABLE',
                    'Owned provider failed on sentence 2',
                )
            return super().translate(provider, text, source, target)

    with translation_service(SecondSentenceFailingHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            server.config.set('translation', 'translation_split_mode', 'line_by_line')
            zid = '20261120050008'
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

            events = _run_progressive(server, zid, language='en', text_mode='multi')

            translated_text = next(e for e in events if e.get('stage') == 'translated_text')
            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            sentences = {s['sentence_index']: s for s in adapter.db.get_sentences_by_session(zid)}
            words = {w['lemma']: w for w in adapter.db.get_words_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            assert sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert sentences[1]['text_provenance'] == 'live:google'
            assert (sentences[2]['sentence_destination'] or '') == ''
            assert words['first']['word_destination'] == 'google: first'
            assert words['second']['word_destination'] == 'google: second'
            assert translated_text['status'] == 'failed'
            assert translated_text['text_translation_failed'] is True
            assert worker['worker_status'] == 'failed'
            assert worker['worker_finished_at'] is not None
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert finished['error'].get('message')
            fin_sentences = {s['sentence_index']: s for s in finished.get('sentences') or []}
            assert fin_sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert fin_sentences[2]['sentence_destination'] == ''


def test_progressive_sentence_later_retry_establishes_truthful_success(tmp_path):
    """After a partial failed run, a later current run commits the remaining sentence and finishes cleanly."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050009'
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
                    "CREATE TRIGGER reject_second_sentence_once "
                    "BEFORE UPDATE OF sentence_destination ON sentences "
                    "WHEN NEW.sentence_index = 2 "
                    "BEGIN SELECT RAISE(ABORT, 'transient sentence 2 rejection'); END"
                )

            first_events = _run_progressive(server, zid, language='en', text_mode='multi')
            first_finished = next(e for e in reversed(first_events) if e.get('stage') == 'finished')
            assert first_finished['status'] == 'failed'
            assert adapter.db.get_worker_status(zid)['worker_status'] == 'failed'

            with adapter.db.get_connection() as conn:
                conn.execute('DROP TRIGGER reject_second_sentence_once')

            retry_events = _run_progressive(server, zid, language='en', text_mode='multi')
            retry_finished = next(e for e in reversed(retry_events) if e.get('stage') == 'finished')
            sentences = {s['sentence_index']: s for s in adapter.db.get_sentences_by_session(zid)}
            worker = adapter.db.get_worker_status(zid)

            assert sentences[1]['sentence_destination'] == 'google: First sentence.'
            assert sentences[2]['sentence_destination'] == 'google: Second sentence.'
            assert worker['worker_status'] == 'finished'
            assert worker['worker_finished_at'] is not None
            assert retry_finished['status'] == 'success'
            assert retry_finished['error'] is None


@pytest.mark.parametrize('stale_outcome', ['stale_success', 'stale_failure'])
def test_progressive_superseded_generation_cannot_overwrite_newer_accepted_success(tmp_path, stale_outcome):
    """A superseded generation finishing with stale success or failure cannot overwrite a newer accepted run."""
    entered = threading.Event()
    release = threading.Event()
    call_count = {'sentence': 0}

    class StaleFirstGenerationHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if text.strip() == 'Das Haus.':
                call_count['sentence'] += 1
                if call_count['sentence'] == 1:
                    entered.set()
                    assert release.wait(10), 'Stale generation was not released'
                    if stale_outcome == 'stale_failure':
                        raise TranslationServerException(503, 'ERR_STALE', 'Stale generation provider failure')
                    return 'stale: Das Haus.'
                return 'newer: Das Haus.'
            return super().translate(provider, text, source, target)

    with translation_service(StaleFirstGenerationHandler, google_concurrency=2) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050010'
            adapter = _seed_session(
                server,
                zid,
                text='Das Haus.',
                rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']],
            )
            queue = server.arbiter.enrichment_queue
            with queue._lock:
                queue._progressive_generations[zid] = 1
            stale_error = []

            def run_stale():
                try:
                    queue._execute_progressive_task(
                        zid, server.arbiter, language='de', target_lang='ru',
                        text_mode='single', skip_intellifiller=True, generation=1,
                    )
                except Exception as exc:
                    stale_error.append(exc)

            worker_thread = threading.Thread(target=run_stale)
            worker_thread.start()
            try:
                assert entered.wait(10), 'Stale generation did not reach owned provider'
                with queue._lock:
                    queue._progressive_generations[zid] = 2
                queue._execute_progressive_task(
                    zid, server.arbiter, language='de', target_lang='ru',
                    text_mode='single', skip_intellifiller=True, generation=2,
                )
                assert adapter.db.get_sentences_by_session(zid)[0]['sentence_destination'] == 'newer: Das Haus.'
                assert adapter.db.get_worker_status(zid)['worker_status'] == 'finished'

                subscriber = server.arbiter.register_subscriber(zid)
                try:
                    release.set()
                    worker_thread.join(timeout=10)
                    assert not worker_thread.is_alive()
                    assert stale_error == []
                    stale_events = []
                    while not subscriber.empty():
                        stale_events.append(subscriber.get_nowait())
                    assert not [e for e in stale_events if e.get('stage') in ('translated_text', 'finished')]
                finally:
                    server.arbiter.unregister_subscriber(zid, subscriber)

                assert adapter.db.get_sentences_by_session(zid)[0]['sentence_destination'] == 'newer: Das Haus.'
                assert adapter.db.get_worker_status(zid)['worker_status'] == 'finished'
            finally:
                release.set()
                worker_thread.join(timeout=10)


@pytest.mark.parametrize('delete_mode', ['soft', 'hard'])
def test_progressive_deleted_session_during_sentence_stage_cannot_recreate_or_succeed(tmp_path, delete_mode):
    """A session deleted mid-Stage-1 cannot be recreated, mutated with stale success, or published as success."""
    entered = threading.Event()
    release = threading.Event()

    class PausedProviderHandler(ItemAwareTranslationHandler):
        def translate(self, provider, text, source, target):
            if text.strip() == 'Das Haus.':
                entered.set()
                assert release.wait(10), 'Provider was not released'
            return super().translate(provider, text, source, target)

    with translation_service(PausedProviderHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            zid = '20261120050011'
            adapter = _seed_session(
                server,
                zid,
                text='Das Haus.',
                rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']],
            )
            subscriber = server.arbiter.register_subscriber(zid)
            try:
                enqueued = server.arbiter.enrichment_queue.enqueue_progressive_task(
                    zid, server.arbiter, language='de', target_lang='ru',
                    text_mode='single', skip_intellifiller=True,
                )
                assert enqueued['status'] == 'queued'
                with server.arbiter.enrichment_queue._lock:
                    future = server.arbiter.enrichment_queue._active_progressive_sessions.get(zid)
                assert entered.wait(10), 'Worker did not reach owned provider'
                if delete_mode == 'soft':
                    assert adapter.db.soft_delete_session(zid)
                else:
                    assert adapter.db.delete_session(zid)
                release.set()
                if future is not None:
                    future.result(timeout=15)
                events = []
                while not subscriber.empty():
                    events.append(subscriber.get_nowait())
            finally:
                release.set()
                server.arbiter.unregister_subscriber(zid, subscriber)

            finished = next(e for e in reversed(events) if e.get('stage') == 'finished')
            assert finished['status'] == 'failed'
            assert isinstance(finished.get('error'), dict)
            assert finished['rows'] == {}
            assert zid not in server.arbiter.sessions
            if delete_mode == 'soft':
                assert adapter.db.get_session(zid, include_deleted=True)['deleted_at'] is not None
                assert (adapter.db.get_sentences_by_session(zid)[0]['sentence_destination'] or '') == ''
                assert adapter.db.get_worker_status(zid)['worker_status'] == 'failed'
            else:
                assert adapter.db.get_session(zid, include_deleted=True) is None


def test_legacy_adapter_signature_boundary_handles_missing_provenance_without_swallowing_errors(tmp_path):
    """Explicit signature inspection adapts a legacy adapter without provenance and never masks DB errors."""

    class LegacyNoProvenanceAdapter(desk.SqliteStorageAdapter):
        def update_sentence_translation(
            self,
            session_zid,
            sentence_index,
            text,
            target_field='sentence_destination',
            zid=None,
        ):
            return super().update_sentence_translation(
                session_zid, sentence_index, text, target_field=target_field, zid=zid, provenance=None
            )

    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (_, server):
            _configure_progressive(server)
            legacy_adapter = LegacyNoProvenanceAdapter(server.config, server.resolved_paths)
            server.arbiter.storage_adapter = legacy_adapter

            zid_ok = '20261120050012'
            _seed_session(server, zid_ok, text='Das Haus.', rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']])
            ok_events = _run_progressive(server, zid_ok)
            ok_finished = next(e for e in reversed(ok_events) if e.get('stage') == 'finished')
            assert ok_finished['status'] == 'success'
            assert legacy_adapter.db.get_sentences_by_session(zid_ok)[0]['sentence_destination'] == 'google: Das Haus.'
            assert legacy_adapter.db.get_worker_status(zid_ok)['worker_status'] == 'finished'

            zid_fail = '20261120050013'
            _seed_session(server, zid_fail, text='Das Haus.', rows=[['0', 'Haus', 'Haus', '1', 'Das Haus.', '', '']])
            with legacy_adapter.db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_legacy_sentence "
                    "BEFORE UPDATE OF sentence_destination ON sentences "
                    "WHEN NEW.session_zid = '20261120050013' "
                    "BEGIN SELECT RAISE(ABORT, 'legacy adapter trigger rejection'); END"
                )
            fail_events = _run_progressive(server, zid_fail)
            fail_finished = next(e for e in reversed(fail_events) if e.get('stage') == 'finished')
            assert fail_finished['status'] == 'failed'
            assert 'legacy adapter trigger rejection' in fail_finished['error']['message']
            assert legacy_adapter.db.get_worker_status(zid_fail)['worker_status'] == 'failed'
            assert (legacy_adapter.db.get_sentences_by_session(zid_fail)[0]['sentence_destination'] or '') == ''


def test_progressive_sentence_helpers_meet_complexity_and_purity_contract():
    """New pure completion and controller Stage 1 helpers enforce CC <= 7 and pure-core isolation."""
    root = Path(desk.__file__).resolve().parent
    comp_tree = ast.parse((root / 'kardenwort_progressive_completion.py').read_text(encoding='utf-8'))
    comp_imports = set()
    for node in ast.walk(comp_tree):
        if isinstance(node, ast.Import):
            comp_imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            comp_imports.add(node.module.split('.')[0])
    assert not comp_imports.intersection({'kardenwort_desk', 'kardenwort_controller', 'http_server', 'kardenwort_db'})
    comp_violations = {node.name: complexity(node) for node in functions(comp_tree) if complexity(node) > 7}
    assert comp_violations == {}

    ctrl_tree = ast.parse((root / 'kardenwort_controller.py').read_text(encoding='utf-8'))
    checked = {
        '_sentence_writer_target',
        '_supports_keyword_arg',
        '_sentence_write_kwargs',
        '_persist_sentence_field',
        '_sqlite_session_active',
        '_try_persist_sentence',
        '_sentence_persistence_error',
        '_persist_sentence_entry',
        '_persist_sqlite_sentences',
        '_translate_and_persist_sentences',
        '_persist_partial_sentences',
        '_read_committed_sqlite_sentences',
        '_materialize_sentence_stage',
        '_cache_sentence_stage',
        '_emit_translated_text_stage',
        '_run_sentence_stage',
    }
    measured = {node.name: complexity(node) for node in functions(ctrl_tree) if node.name in checked}
    assert set(measured) == checked
    assert max(measured.values()) <= 7, measured
