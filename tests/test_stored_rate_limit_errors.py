"""Real SQLite -> cold /session/status -> public window.receiveUpdate acceptance for stored HTTP 429 errors."""
import ast
import json
from pathlib import Path
import urllib.request

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
import kardenwort_enrichment as enrichment
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    translation_service,
)
from tests.reword_contract_support import HEADERS, TOKEN
from tests.test_intent_architecture import complexity, functions
from tests.test_reliability_action_matrix import MATRIX, configure


COLD_ZID = '20261118201436'

CONFIRMED_RATE_LIMIT_FAILURES = [
    pytest.param('HTTP 429 Too Many Requests', id='exact-http-429'),
    pytest.param('  HTTP 429 Too Many Requests  ', id='whitespace-http-429'),
    pytest.param(
        'Error calling Gemini API: HTTP 429 Too Many Requests',
        id='provider-error-calling-http-429-full',
    ),
    pytest.param(
        'Error calling provider: HTTP 429',
        id='provider-error-calling-http-429-short',
    ),
]

EXISTING_FAILURE_CONTROLS = [
    pytest.param('[FAILED]', id='failed-marker'),
    pytest.param('[Error: provider timeout]', id='error-prefix-marker'),
]


def _cold_status(base_url, server, zid):
    request = urllib.request.Request(
        f'{base_url}/session/status?zid={zid}',
        headers={'X-API-Token': server.api_key},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.load(response)
    assert payload.get('status') == 'success'
    assert payload['data'].get('ok') is True
    return payload['data']


def _prepare_cold_workspace(server, zid, *, text, rows, sentences):
    case = next(row for row in MATRIX['rows'] if row['audio'] == 'disabled')
    adapter, world = configure(server, case)
    path = server.resolved_paths['results_dir'] / f'{zid}.de.tsv'
    adapter.save_session(
        session_zid=zid,
        source_language='de',
        target_language='en',
        text_mode='single',
        source_raw_text=text,
        headers=HEADERS + ['SentenceDestination'],
        data_rows=rows,
        sentences=sentences,
        working_tsv_path=path,
    )
    html = desk.run_render_flow(
        text=text,
        language='de',
        zid=zid,
        text_mode='single',
        config=world.config,
        resolved_paths=world.paths,
        theme=case['theme'],
        tsv_path=zid,
        spawn_children=False,
    )
    return adapter, html


def _open_rendered_page(page, url, html, *, min_cards=1, seq=1):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route(url + '/', lambda route: route.fulfill(content_type='text/html', body=html))
    page.set_extra_http_headers({'X-API-Token': TOKEN})
    page.set_default_timeout(6000)
    page.goto(url + '/')
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate(
        '() => { window.__storedRateLimitStateReady = false; '
        'window.__stateReady.then(() => { window.__storedRateLimitStateReady = true; }); }'
    )
    page.wait_for_function('window.__storedRateLimitStateReady === true')
    page.wait_for_function(
        '(minCards) => Boolean(window.WorkspaceTabs && WorkspaceTabs.getCards().length >= minCards)',
        arg=min_cards,
    )
    page.evaluate('(tabSeq) => WorkspaceTabs.switchToTab(tabSeq)', seq)
    return errors


def _seed_raw_word_destination(adapter, zid, *, sentence_index, token_order, value, extra_fields=None):
    updates = [
        {
            'sentence_index': sentence_index,
            'token_order': token_order,
            'field': 'word_destination',
            'value': value,
        }
    ]
    if extra_fields is not None:
        updates.append(
            {
                'sentence_index': sentence_index,
                'token_order': token_order,
                'field': 'extra_fields',
                'value': json.dumps(extra_fields),
            }
        )
    adapter.db.batch_update_words(zid, updates, zid=zid)


@pytest.mark.parametrize('stored_error', CONFIRMED_RATE_LIMIT_FAILURES)
def test_cold_status_and_browser_receive_update_hide_stored_rate_limit_error(
    page, tmp_path, stored_error
):
    """F5 acceptance: stored whole-field HTTP 429 errors must not surface as vocabulary meaning."""
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            initial_rows = [
                ['0', 'Haus', 'Haus', '', '1', 'Haus.', '/haus/', 'noun', 'House.'],
                ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun', 'Tree.'],
            ]
            sentences = [
                {'sentence_index': 1, 'sentence_source': 'Haus.', 'sentence_destination': 'House.'},
                {'sentence_index': 2, 'sentence_source': 'Baum.', 'sentence_destination': 'Tree.'},
            ]
            adapter, html = _prepare_cold_workspace(
                server, COLD_ZID, text='Haus. Baum.', rows=initial_rows, sentences=sentences
            )
            _seed_raw_word_destination(
                adapter, COLD_ZID, sentence_index=1, token_order=0, value=stored_error
            )
            server.arbiter.sessions.clear()

            before_bundle = adapter.db.get_session_bundle(COLD_ZID)
            assert before_bundle['words'][0]['word_destination'] == stored_error

            status = _cold_status(url, server, COLD_ZID)
            assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
            assert translator.provider_calls == []
            assert status['rows']['1']['trans'] == 'tree'

            errors = _open_rendered_page(page, url, html)
            try:
                page.evaluate('(payload) => window.receiveUpdate(payload)', status)
                row0_cell = page.locator(
                    '#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell'
                )
                row1_cell = page.locator(
                    '#lemma-table tr[data-row-id="1"] td[data-col="WordDestination"] .scrollable-cell'
                )
                expect(row1_cell).to_have_text('tree')
                rendered_row0 = (row0_cell.text_content() or '').strip()
                assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
                assert translator.provider_calls == []
                assert errors == []
                assert status['rows']['0']['trans'] == '', (
                    f'Cold status leaked stored rate-limit error as translation: '
                    f'status={status["rows"]["0"]["trans"]!r}, rendered={rendered_row0!r}'
                )
                expect(row0_cell).to_have_text('')
            finally:
                page.context.close()


def test_cold_status_hides_stored_rate_limit_error_after_occurrence_overlay(tmp_path):
    """Post-project_occurrences overlay in format_update_rows_dict must not reintroduce HTTP 429."""
    stored_error = 'HTTP 429 Too Many Requests'
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            initial_rows = [
                ['0', 'Haus', 'Haus', '', '1', 'Haus.', '/haus/', 'noun', 'House.'],
                ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun', 'Tree.'],
            ]
            sentences = [
                {'sentence_index': 1, 'sentence_source': 'Haus.', 'sentence_destination': 'House.'},
                {'sentence_index': 2, 'sentence_source': 'Baum.', 'sentence_destination': 'Tree.'},
            ]
            adapter, _ = _prepare_cold_workspace(
                server, COLD_ZID, text='Haus. Baum.', rows=initial_rows, sentences=sentences
            )
            _seed_raw_word_destination(
                adapter,
                COLD_ZID,
                sentence_index=1,
                token_order=0,
                value=stored_error,
                extra_fields={'occurrence_data': {'0': {'trans': stored_error}}},
            )
            server.arbiter.sessions.clear()

            before_bundle = adapter.db.get_session_bundle(COLD_ZID)
            status = _cold_status(url, server, COLD_ZID)

            assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
            assert status['rows']['0']['trans'] == ''
            assert status['rows']['1']['trans'] == 'tree'


@pytest.mark.parametrize('stored_marker', EXISTING_FAILURE_CONTROLS)
def test_cold_status_and_browser_receive_update_existing_failed_and_error_controls(
    page, tmp_path, stored_marker
):
    """Control: existing [FAILED] and [Error...] markers already clear to empty on cold status."""
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            initial_rows = [
                ['0', 'Haus', 'Haus', '', '1', 'Haus.', '/haus/', 'noun', 'House.'],
                ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun', 'Tree.'],
            ]
            sentences = [
                {'sentence_index': 1, 'sentence_source': 'Haus.', 'sentence_destination': 'House.'},
                {'sentence_index': 2, 'sentence_source': 'Baum.', 'sentence_destination': 'Tree.'},
            ]
            adapter, html = _prepare_cold_workspace(
                server, COLD_ZID, text='Haus. Baum.', rows=initial_rows, sentences=sentences
            )
            _seed_raw_word_destination(
                adapter, COLD_ZID, sentence_index=1, token_order=0, value=stored_marker
            )
            server.arbiter.sessions.clear()

            before_bundle = adapter.db.get_session_bundle(COLD_ZID)
            assert before_bundle['words'][0]['word_destination'] == stored_marker

            status = _cold_status(url, server, COLD_ZID)
            assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
            assert status['rows']['0']['trans'] == ''
            assert status['rows']['1']['trans'] == 'tree'

            errors = _open_rendered_page(page, url, html)
            try:
                page.evaluate('(payload) => window.receiveUpdate(payload)', status)
                row0_cell = page.locator(
                    '#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell'
                )
                row1_cell = page.locator(
                    '#lemma-table tr[data-row-id="1"] td[data-col="WordDestination"] .scrollable-cell'
                )
                expect(row0_cell).to_have_text('')
                expect(row1_cell).to_have_text('tree')
                assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
                assert translator.provider_calls == []
                assert errors == []
            finally:
                page.context.close()


def test_cold_status_and_browser_receive_update_preserve_literal_lesson_and_translation_content(
    page, tmp_path
):
    """Control: legitimate source/quotation/translation content mentioning HTTP or 429 stays intact."""
    sentence_1 = 'Der Server meldet HTTP 429 Too Many Requests bei Last.'
    sentence_2 = 'Ein Fehler beim Anruf beschreibt HTTP.'
    full_text = f'{sentence_1} {sentence_2}'
    initial_rows = [
        ['0', 'HTTP 429 Too Many Requests', 'HTTP', '', '1', sentence_1, '', 'noun', 'The server reports HTTP 429 Too Many Requests under load.'],
        ['1', '429', '429', '', '1', sentence_1, '', 'num', 'The server reports HTTP 429 Too Many Requests under load.'],
        ['0', 'Fehler', 'Fehler', '', '2', sentence_2, '', 'noun', 'An error during a call describes HTTP.'],
        ['1', 'Anruf', 'Anruf', '', '2', sentence_2, '', 'noun', 'An error during a call describes HTTP.'],
        ['2', 'Protokoll', 'Protokoll', '', '2', sentence_2, '', 'noun', 'An error during a call describes HTTP.'],
    ]
    literal_targets = [
        (1, 0, 'HTTP 429 error code'),
        (1, 1, '429'),
        (2, 0, 'See HTTP 429 Too Many Requests in the manual'),
        (2, 1, 'Error calling a friend'),
        (2, 2, 'HTTP'),
    ]
    sentences = [
        {
            'sentence_index': 1,
            'sentence_source': sentence_1,
            'sentence_destination': 'The server reports HTTP 429 Too Many Requests under load.',
        },
        {
            'sentence_index': 2,
            'sentence_source': sentence_2,
            'sentence_destination': 'An error during a call describes HTTP.',
        },
    ]
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            adapter, html = _prepare_cold_workspace(
                server, COLD_ZID, text=full_text, rows=initial_rows, sentences=sentences
            )
            for s_idx, t_ord, trans_value in literal_targets:
                _seed_raw_word_destination(
                    adapter, COLD_ZID, sentence_index=s_idx, token_order=t_ord, value=trans_value
                )
            server.arbiter.sessions.clear()

            before_bundle = adapter.db.get_session_bundle(COLD_ZID)
            status = _cold_status(url, server, COLD_ZID)

            assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
            assert status['sentences'][0]['sentence_source'] == sentence_1
            assert status['sentences'][1]['sentence_source'] == sentence_2
            expected_by_coord = {(str(s), str(t)): val for s, t, val in literal_targets}
            for idx in range(len(literal_targets)):
                row_entry = status['rows'][str(idx)]
                coord = (str(row_entry['sentence_idx']), str(row_entry['token_order']))
                assert row_entry['trans'] == expected_by_coord[coord]
                assert row_entry['sentence_source'] == (sentence_1 if coord[0] == '1' else sentence_2)

            errors = _open_rendered_page(page, url, html, min_cards=3, seq=2)
            try:
                page.evaluate('(payload) => window.receiveUpdate(payload)', status)
                expect(page.locator('#source-container')).to_contain_text(sentence_1)
                for s_idx, t_ord, expected in literal_targets:
                    page.evaluate('(tabSeq) => WorkspaceTabs.switchToTab(tabSeq)', s_idx + 1)
                    cell = page.locator(
                        f'#lemma-table tr[data-sentence-idx="{s_idx}"][data-token-order="{t_ord}"] '
                        'td[data-col="WordDestination"] .scrollable-cell'
                    )
                    expect(cell).to_have_text(expected)
                assert errors == []
            finally:
                page.context.close()


@pytest.mark.parametrize(
    'value,expected_invalid',
    [
        ('', True),
        ('   ', True),
        ('[FAILED]', True),
        ('[Error: upstream]', True),
        ('HTTP 429 Too Many Requests', True),
        ('  HTTP 429 Too Many Requests  ', True),
        ('Error calling Gemini API: HTTP 429 Too Many Requests', True),
        ('Error calling provider: HTTP 429', True),
        ('house', False),
        ('HTTP', False),
        ('429', False),
        ('HTTP 429', False),
        ('HTTP 429 error code', False),
        ('See HTTP 429 Too Many Requests in the manual', False),
        ('Error calling a friend', False),
        ('HTTP 404 Not Found', False),
    ],
)
def test_enrichment_invalid_translation_classification_controls(value, expected_invalid):
    """Classifier contract for enrichment validation without broad substring matching."""
    assert enrichment.invalid_translation(value) is expected_invalid


def _post_retry(base_url, server, body):
    request = urllib.request.Request(
        f'{base_url}/session/retry',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-API-Token': server.api_key,
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.load(response)
    assert payload.get('status') == 'success'
    return payload['data']


@pytest.mark.parametrize('action', ['session', 'row'])
def test_explicit_retry_replaces_only_failed_rate_limit_occurrence_and_survives_reload(
    page, tmp_path, action
):
    """Task 2.2: explicit retry replaces only the failed 429 occurrence and survives cold reload."""
    stored_error = 'HTTP 429 Too Many Requests'
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            case = next(row for row in MATRIX['rows'] if row['audio'] == 'disabled')
            adapter, world = configure(server, case)
            server.config.set('pipeline', 'provider_cooldown_seconds', '0')
            initial_rows = [
                ['0', 'Haus', 'Haus', '', '1', 'Haus.', '/haus/', 'noun', 'House.'],
                ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun', 'Tree.'],
            ]
            sentences = [
                {'sentence_index': 1, 'sentence_source': 'Haus.', 'sentence_destination': 'House.'},
                {'sentence_index': 2, 'sentence_source': 'Baum.', 'sentence_destination': 'Tree.'},
            ]
            path = server.resolved_paths['results_dir'] / f'{COLD_ZID}.de.tsv'
            adapter.save_session(
                session_zid=COLD_ZID,
                source_language='de',
                target_language='en',
                text_mode='single',
                source_raw_text='Haus. Baum.',
                headers=HEADERS + ['SentenceDestination'],
                data_rows=initial_rows,
                sentences=sentences,
                working_tsv_path=path,
            )
            _seed_raw_word_destination(
                adapter, COLD_ZID, sentence_index=1, token_order=0, value=stored_error
            )
            server.arbiter.sessions.clear()

            before_bundle = adapter.db.get_session_bundle(COLD_ZID)
            assert before_bundle['words'][0]['word_destination'] == stored_error
            assert before_bundle['words'][1]['word_destination'] == 'tree'

            cold_before = _cold_status(url, server, COLD_ZID)
            assert cold_before['rows']['0']['trans'] == ''
            assert cold_before['rows']['1']['trans'] == 'tree'
            assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle
            assert translator.provider_calls == []

            def render_current():
                return desk.run_render_flow(
                    text='Haus. Baum.',
                    language='de',
                    zid=COLD_ZID,
                    text_mode='single',
                    config=world.config,
                    resolved_paths=world.paths,
                    theme=case['theme'],
                    tsv_path=COLD_ZID,
                    spawn_children=False,
                )

            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route(
                url + '/',
                lambda route: route.fulfill(content_type='text/html', body=render_current()),
            )
            page.set_extra_http_headers({'X-API-Token': TOKEN})
            page.set_default_timeout(6000)
            try:
                page.goto(url + '/')
                page.wait_for_function('window.__stateReady !== undefined')
                page.evaluate(
                    '() => { window.__retryReady = false; '
                    'window.__stateReady.then(() => { window.__retryReady = true; }); }'
                )
                page.wait_for_function('window.__retryReady === true')
                page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
                page.evaluate('WorkspaceTabs.switchToTab(2)')
                page.evaluate('(payload) => window.receiveUpdate(payload)', cold_before)

                row0_cell = page.locator(
                    '#lemma-table tr[data-sentence-idx="1"][data-token-order="0"] '
                    'td[data-col="WordDestination"] .scrollable-cell'
                )
                expect(row0_cell).to_have_text('')
                assert translator.provider_calls == []
                assert adapter.db.get_session_bundle(COLD_ZID) == before_bundle

                with page.expect_response('**/session/retry') as retry_response:
                    if action == 'row':
                        page.evaluate('retryRow(0, null)')
                    else:
                        page.evaluate('(zid) => retrySession(zid)', COLD_ZID)
                assert retry_response.value.status == 200, retry_response.value.text()

                expect(row0_cell).to_have_text('google: Haus')
                assert len(translator.provider_calls) == 1
                assert translator.provider_calls[0][0].strip() == 'Haus'

                words_after = adapter.db.get_words_by_session(COLD_ZID)
                assert words_after[0]['word_destination'] == 'google: Haus'
                assert words_after[0]['word_provenance'] == 'live:google'
                assert words_after[1] == before_bundle['words'][1]

                server.arbiter.sessions.clear()
                page.reload()
                page.wait_for_function('window.__stateReady !== undefined')
                page.evaluate(
                    '() => { window.__retryReloadReady = false; '
                    'window.__stateReady.then(() => { window.__retryReloadReady = true; }); }'
                )
                page.wait_for_function('window.__retryReloadReady === true')
                page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
                page.evaluate('WorkspaceTabs.switchToTab(2)')
                expect(row0_cell).to_have_text('google: Haus')

                page.evaluate('WorkspaceTabs.switchToTab(3)')
                row1_cell = page.locator(
                    '#lemma-table tr[data-sentence-idx="2"][data-token-order="0"] '
                    'td[data-col="WordDestination"] .scrollable-cell'
                )
                expect(row1_cell).to_have_text('tree')

                cold_after = _cold_status(url, server, COLD_ZID)
                assert cold_after['rows']['0']['trans'] == 'google: Haus'
                assert cold_after['rows']['1']['trans'] == 'tree'
                assert len(translator.provider_calls) == 1
                assert errors == []
            finally:
                page.context.close()


def test_warm_session_implicit_retry_targets_stored_rate_limit_and_clears_overlay(tmp_path):
    """Warm session implicit retry targets only the 429 row and stale occurrence_data never shadows retry."""
    stored_error = 'Error calling Gemini API: HTTP 429 Too Many Requests'
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            initial_rows = [
                ['0', 'Haus', 'Haus', '', '1', 'Haus.', '/haus/', 'noun', 'House.'],
                ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun', 'Tree.'],
            ]
            sentences = [
                {'sentence_index': 1, 'sentence_source': 'Haus.', 'sentence_destination': 'House.'},
                {'sentence_index': 2, 'sentence_source': 'Baum.', 'sentence_destination': 'Tree.'},
            ]
            adapter, _ = _prepare_cold_workspace(
                server, COLD_ZID, text='Haus. Baum.', rows=initial_rows, sentences=sentences
            )
            _seed_raw_word_destination(
                adapter,
                COLD_ZID,
                sentence_index=1,
                token_order=0,
                value=stored_error,
                extra_fields={'occurrence_data': {'0': {'trans': 'HTTP 429 Too Many Requests'}}},
            )
            # Warm in-memory session uses the real restored column schema with raw stored error text
            _, warm_headers, warm_rows = adapter.load_tsv_rows(COLD_ZID)
            warm_rows[0][warm_headers.index('WordDestination')] = stored_error
            server.arbiter.sessions[COLD_ZID] = {
                'session_zid': COLD_ZID,
                'language': 'de',
                'target_lang': 'en',
                'text': 'Haus. Baum.',
                'headers': warm_headers,
                'data_rows': warm_rows,
                'tsv_path': COLD_ZID,
            }
            before_words = adapter.db.get_words_by_session(COLD_ZID)

            retry_result = _post_retry(url, server, {'session_zid': COLD_ZID})
            assert len(translator.provider_calls) == 1
            assert translator.provider_calls[0][0].strip() == 'Haus'
            assert retry_result['rows']['0']['trans'] == 'google: Haus'
            assert retry_result['rows']['1']['trans'] == 'tree'

            after_words = adapter.db.get_words_by_session(COLD_ZID)
            assert after_words[0]['word_destination'] == 'google: Haus'
            assert after_words[1] == before_words[1]

            server.arbiter.sessions.clear()
            cold_after = _cold_status(url, server, COLD_ZID)
            assert cold_after['rows']['0']['trans'] == 'google: Haus'
            assert cold_after['rows']['1']['trans'] == 'tree'


def test_cold_status_marks_recent_rate_limit_session_unfinished_without_auto_retry(tmp_path):
    """A recent session with stored HTTP 429 is not counted finished and triggers zero auto-retries."""
    recent_zid = desk.generate_unique_zid()
    stored_error = 'HTTP 429 Too Many Requests'
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend='sqlite') as (url, server):
            initial_rows = [
                ['0', 'Haus', 'Haus', '', '1', 'Haus.', '/haus/', 'noun', 'House.'],
            ]
            sentences = [
                {'sentence_index': 1, 'sentence_source': 'Haus.', 'sentence_destination': 'House.'},
            ]
            adapter, _ = _prepare_cold_workspace(
                server, recent_zid, text='Haus.', rows=initial_rows, sentences=sentences
            )
            _seed_raw_word_destination(
                adapter, recent_zid, sentence_index=1, token_order=0, value=stored_error
            )
            server.arbiter.sessions.clear()

            before_bundle = adapter.db.get_session_bundle(recent_zid)
            status = _cold_status(url, server, recent_zid)

            assert status['is_finished'] is False
            assert status['stage'] == 'translating'
            assert status['rows']['0']['trans'] == ''
            assert adapter.db.get_session_bundle(recent_zid) == before_bundle
            assert translator.provider_calls == []


def test_translation_validation_core_is_pure_and_within_complexity_ceiling():
    """Architectural fitness: centralized classifier module stays pure with CC <= 7."""
    path = Path(__file__).resolve().parents[1] / 'kardenwort_translation_validation.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert imported == {'re'}
    forbidden_calls = {'open', 'exec', 'eval', '__import__', 'print', 'input'}
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls.intersection(forbidden_calls)
    measured = {node.name: complexity(node) for node in functions(tree)}
    assert measured, 'Expected classifier functions in kardenwort_translation_validation.py'
    assert max(measured.values()) <= 7, measured
