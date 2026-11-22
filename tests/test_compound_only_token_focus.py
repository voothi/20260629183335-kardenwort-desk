"""Real-browser, HTTP, and owned-SQLite acceptance tests for compound-only token focus."""
import json
import re

import pytest

import kardenwort_desk as desk
from kardenwort_client_views import ClientViewAdapter
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_release_boundary_contracts import post


HEADERS = [
    'WordSourceInflectedForm',
    'WordSource',
    'WordDestination',
    'DeskSelected',
    'SentenceSource',
    'SentenceSourceIndex',
    'TokenOrder',
]


def _ready(page):
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate('window.__stateReady')


def _client_id(page):
    match = re.search(r'kw-client=([0-9a-f]{32})', page.url)
    assert match, f'Missing kw-client handle in {page.url}'
    return match.group(1)


def _span_center(locator):
    box = locator.bounding_box()
    assert box is not None, 'Target token span has no bounding box'
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def _active_visual_indices(page):
    return page.evaluate(
        '''() => Array.from(document.querySelectorAll('#source-container span.word'))
        .filter(el => el.classList.contains('highlight-orange-active')
            || el.classList.contains('highlight-purple-active')
            || el.classList.contains('active-subtoken'))
        .map(el => Number(el.getAttribute('data-word-idx')))'''
    )


def _selected_rows(page):
    return json.loads(page.evaluate('window.getSelectedRows()'))


def _token_map(page):
    return page.evaluate('() => JSON.parse(document.getElementById("token-map").textContent)')


def _is_client_view_write(response):
    req = response.request
    if req.method != 'POST' or '/api/v1/client-view' not in req.url:
        return False
    body = json.loads(req.post_data or '{}')
    return 'active_card' in body


def _response_record(response):
    body = json.loads(response.request.post_data or '{}')
    try:
        payload = response.json()
    except Exception:
        payload = {}
    return dict(status=response.status, body=body, response=payload)


def _seed_sqlite_session(server, zid, source_text, rows):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    adapter.save_session(
        session_zid=zid,
        source_language='en',
        target_language='ru',
        source_raw_text=source_text,
        text_mode='single',
        headers=HEADERS,
        data_rows=rows,
    )
    return adapter


def _open_rendered_session(page, url, server, zid):
    page.context.set_extra_http_headers({'X-API-Token': server.api_key})
    page.goto(f'{url}/?session_zid={zid}&seq_num=1')
    _ready(page)


def test_compound_only_subtoken_click_persists_and_reloads_visual_focus_without_membership(page, preference_world):
    url, server, _ = preference_world
    zid = '20261119102701'
    source_text = 'A multi-token test.'
    rows = [
        ['multi-token', 'multi-token', 'мульти-токен', '0', source_text, '1', '1'],
        ['test', 'test', 'тест', '0', source_text, '1', '2'],
    ]
    adapter = _seed_sqlite_session(server, zid, source_text, rows)
    _open_rendered_session(page, url, server, zid)

    manifest = _token_map(page)
    multi_tok = next(t for t in manifest if t.get('lower_clean') == 'multi')
    token_tok = next(t for t in manifest if t.get('lower_clean') == 'token')
    test_tok = next(t for t in manifest if t.get('lower_clean') == 'test')

    assert multi_tok['atomic_row_ids'] == []
    assert len(multi_tok['compound_row_ids']) == 1
    assert token_tok['atomic_row_ids'] == []
    assert len(token_tok['compound_row_ids']) == 1
    assert len(test_tok['atomic_row_ids']) == 1

    multi_span = page.locator(f'#source-container span.word[data-word-idx="{multi_tok["visual_idx"]}"]')
    with page.expect_response(_is_client_view_write) as resp_info:
        multi_span.click()
    write = _response_record(resp_info.value)
    page.evaluate('flushPendingSaves().catch(() => null)')

    assert write['status'] == 200, f'Compound-only subtoken click failed client-view save: {write}'
    toast_text = page.locator('#kw-toast-container').inner_text()
    assert 'View state not saved' not in toast_text, f'Unexpected error toast: {toast_text}'

    active_before = page.evaluate('AppState.activeTokenSelections')
    assert [item['visual_idx'] for item in active_before] == [multi_tok['visual_idx']]
    assert active_before[0]['atomic_row_ids'] == []
    assert active_before[0]['atomic_id'] is None
    assert _active_visual_indices(page) == [multi_tok['visual_idx']]
    assert _selected_rows(page) == []
    assert not any(adapter.get_shared_selection(zid).values())

    stored = ClientViewAdapter(adapter.db).get(zid, _client_id(page))
    assert [item['visual_idx'] for item in stored['active_tokens']] == [multi_tok['visual_idx']]

    page.reload()
    _ready(page)

    reload_toast = page.locator('#kw-toast-container').inner_text()
    assert 'Unavailable view focus was cleared' not in reload_toast
    active_after = page.evaluate('AppState.activeTokenSelections')
    assert [item['visual_idx'] for item in active_after] == [multi_tok['visual_idx']]
    assert active_after[0]['atomic_row_ids'] == []
    assert active_after[0]['atomic_id'] is None
    assert _active_visual_indices(page) == [multi_tok['visual_idx']]
    assert _selected_rows(page) == []
    assert not any(adapter.get_shared_selection(zid).values())

    # Atomic token alongside compound-only tokens keeps exact existing focus behavior.
    page.evaluate('clearTokenSelections()')
    page.evaluate('flushPendingSaves()')
    test_span = page.locator(f'#source-container span.word[data-word-idx="{test_tok["visual_idx"]}"]')
    with page.expect_response(_is_client_view_write) as atomic_resp:
        test_span.click()
    assert atomic_resp.value.status == 200
    page.evaluate('flushPendingSaves()')
    assert _active_visual_indices(page) == [test_tok['visual_idx']]
    assert adapter.get_shared_selection(zid)['test'] is True
    assert adapter.get_shared_selection(zid)['multi-token'] is False

    page.reload()
    _ready(page)
    assert [item['visual_idx'] for item in page.evaluate('AppState.activeTokenSelections')] == [test_tok['visual_idx']]
    assert _active_visual_indices(page) == [test_tok['visual_idx']]


def test_full_compound_drag_gesture_retains_group_and_composite_row_across_sqlite_reload(page, preference_world):
    url, server, _ = preference_world
    zid = '20261119102702'
    source_text = 'A multi-token test.'
    rows = [
        ['multi-token', 'multi-token', 'мульти-токен', '0', source_text, '1', '1'],
        ['test', 'test', 'тест', '0', source_text, '1', '2'],
    ]
    adapter = _seed_sqlite_session(server, zid, source_text, rows)
    other = page.context.new_page()
    try:
        _open_rendered_session(page, url, server, zid)
        other.goto(f'{url}/?session_zid={zid}&seq_num=1')
        _ready(other)
        page.bring_to_front()

        manifest = _token_map(page)
        multi_tok = next(t for t in manifest if t.get('lower_clean') == 'multi')
        token_tok = next(t for t in manifest if t.get('lower_clean') == 'token')
        compound_row_id = multi_tok['compound_row_ids'][0]

        multi_span = page.locator(f'#source-container span.word[data-word-idx="{multi_tok["visual_idx"]}"]')
        token_span = page.locator(f'#source-container span.word[data-word-idx="{token_tok["visual_idx"]}"]')

        x0, y0 = _span_center(multi_span)
        x1, y1 = _span_center(token_span)
        page.mouse.move(x0, y0)
        page.mouse.down(button='left')
        page.mouse.move(x1, y1)
        with page.expect_response(_is_client_view_write) as resp_info:
            page.mouse.up(button='left')
        write = _response_record(resp_info.value)
        page.evaluate('flushPendingSaves().catch(() => null)')

        assert write['status'] == 200, f'Full-compound drag failed client-view save: {write}'
        expected_visuals = [multi_tok['visual_idx'], token_tok['visual_idx']]
        assert _active_visual_indices(page) == expected_visuals
        assert _selected_rows(page) == [compound_row_id]
        assert adapter.get_shared_selection(zid)['multi-token'] is True
        assert adapter.get_shared_selection(zid)['test'] is False

        page.reload()
        _ready(page)

        assert [item['visual_idx'] for item in page.evaluate('AppState.activeTokenSelections')] == expected_visuals
        assert _active_visual_indices(page) == expected_visuals
        assert _selected_rows(page) == [compound_row_id]

        # Another client window keeps its own empty focus.
        other.bring_to_front()
        other.reload()
        _ready(other)
        assert other.evaluate('AppState.activeTokenSelections') == []
        assert _active_visual_indices(other) == []
    finally:
        other.close()


def test_repeated_compound_occurrences_and_partial_range_drag_preserve_exact_visual_focus(page, preference_world):
    url, server, _ = preference_world
    zid = '20261119102703'
    source_text = 'multi-token and multi-token plus split_camel_case.'
    rows = [
        ['multi-token', 'multi-token', 'мульти-токен', '0', source_text, '1', '0'],
        ['split_camel_case', 'split_camel_case', 'разделить_кэмел_кейс', '0', source_text, '1', '3'],
    ]
    adapter = _seed_sqlite_session(server, zid, source_text, rows)
    other = page.context.new_page()
    try:
        _open_rendered_session(page, url, server, zid)
        other.goto(f'{url}/?session_zid={zid}&seq_num=1')
        _ready(other)
        page.bring_to_front()

        manifest = _token_map(page)
        multi_toks = [t for t in manifest if t.get('lower_clean') == 'multi']
        token_toks = [t for t in manifest if t.get('lower_clean') == 'token']
        assert len(multi_toks) == 2 and len(token_toks) == 2
        second_multi = multi_toks[1]

        # A. Repeated occurrences sharing one stored anchor: clicking only 2nd 'multi' must not activate 1st occurrence or siblings.
        second_multi_span = page.locator(f'#source-container span.word[data-word-idx="{second_multi["visual_idx"]}"]')
        with page.expect_response(_is_client_view_write) as rep_resp:
            second_multi_span.click()
        rep_write = _response_record(rep_resp.value)
        page.evaluate('flushPendingSaves().catch(() => null)')

        assert rep_write['status'] == 200, f'Repeated occurrence click failed: {rep_write}'
        assert _active_visual_indices(page) == [second_multi['visual_idx']]
        assert _selected_rows(page) == []
        assert not any(adapter.get_shared_selection(zid).values())

        page.reload()
        _ready(page)
        assert [item['visual_idx'] for item in page.evaluate('AppState.activeTokenSelections')] == [second_multi['visual_idx']]
        assert _active_visual_indices(page) == [second_multi['visual_idx']]
        assert _selected_rows(page) == []
        assert not any(adapter.get_shared_selection(zid).values())
        assert other.evaluate('AppState.activeTokenSelections') == []

        # B. Partial range/drag gesture across 2 of 3 subtokens of 'split_camel_case' ('split' -> 'camel').
        page.evaluate('clearTokenSelections()')
        page.evaluate('flushPendingSaves()')

        split_tok = next(t for t in manifest if t.get('lower_clean') == 'split')
        camel_tok = next(t for t in manifest if t.get('lower_clean') == 'camel')
        case_tok = next(t for t in manifest if t.get('lower_clean') == 'case')
        assert split_tok['atomic_row_ids'] == [] and camel_tok['atomic_row_ids'] == [] and case_tok['atomic_row_ids'] == []

        split_span = page.locator(f'#source-container span.word[data-word-idx="{split_tok["visual_idx"]}"]')
        camel_span = page.locator(f'#source-container span.word[data-word-idx="{camel_tok["visual_idx"]}"]')

        x0, y0 = _span_center(split_span)
        x1, y1 = _span_center(camel_span)
        page.mouse.move(x0, y0)
        page.mouse.down(button='left')
        page.mouse.move(x1, y1)
        with page.expect_response(_is_client_view_write) as drag_resp:
            page.mouse.up(button='left')
        drag_write = _response_record(drag_resp.value)
        page.evaluate('flushPendingSaves().catch(() => null)')

        expected_partial = [split_tok['visual_idx'], camel_tok['visual_idx']]
        assert drag_write['status'] == 200, f'Partial compound drag failed: {drag_write}'
        assert _active_visual_indices(page) == expected_partial
        assert _selected_rows(page) == []
        assert all(item['atomic_row_ids'] == [] for item in page.evaluate('AppState.activeTokenSelections'))
        assert not any(adapter.get_shared_selection(zid).values())

        page.reload()
        _ready(page)
        reloaded_tokens = page.evaluate('AppState.activeTokenSelections')
        assert [item['visual_idx'] for item in reloaded_tokens] == expected_partial
        assert all(item['atomic_row_ids'] == [] for item in reloaded_tokens)
        assert _active_visual_indices(page) == expected_partial
        assert _selected_rows(page) == []
        assert not any(adapter.get_shared_selection(zid).values())

        other.bring_to_front()
        other.reload()
        _ready(other)
        assert other.evaluate('AppState.activeTokenSelections') == []
        assert _active_visual_indices(other) == []
    finally:
        other.close()


@pytest.mark.parametrize(
    'bad_descriptor',
    [
        dict(visual_idx=2, sentence_idx=1),
        dict(visual_idx=2, sentence_idx=1, token_order=''),
        dict(visual_idx=2, sentence_idx=0, token_order='1'),
        dict(visual_idx=2, sentence_idx=1, token_order='-1'),
    ],
)
def test_malformed_client_view_descriptor_remains_fail_fast_http_400(preference_world, bad_descriptor):
    url, server, _ = preference_world
    zid = '20261119102704'
    source_text = 'A multi-token test.'
    rows = [
        ['multi-token', 'multi-token', 'мульти-токен', '0', source_text, '1', '1'],
    ]
    adapter = _seed_sqlite_session(server, zid, source_text, rows)
    client = 'c' * 32
    status, payload = post(
        url,
        '/api/v1/client-view',
        dict(session_zid=zid, client_id=client, active_card=1, active_tokens=[bad_descriptor]),
    )
    assert status == 400, payload
    assert payload.get('error_code') == 'INVALID_PAYLOAD', payload
    stored = ClientViewAdapter(adapter.db).get(zid, client)
    assert stored['active_tokens'] == []
    assert stored['revision'] == 0


def test_ambiguous_different_occurrence_compound_candidates_rejected_without_first_row_guessing():
    rows = [
        ['multi-token', 'multi-token', 'мульти-токен', '0', 'multi-token and multi-token.', '1', '0'],
        ['multi-token', 'multi-token', 'мульти-токен', '0', 'multi-token and multi-token.', '1', '5'],
        ['multi-token', 'multi-token', 'мульти-токен', '0', 'multi-token and multi-token.', '1', '0'],
        ['multi-token', 'multi-token', 'мульти-токен', '0', 'multi-token second sentence.', '2', '0'],
    ]

    # Same canonical occurrence across multiple candidates succeeds.
    assert desk.resolve_compound_only_occurrence_anchor(
        [0, 2], rows, col_index=5, col_token_order=6, source_sentence_idx=1, visual_idx=0
    ) == (1, '0')

    # Unambiguous sentence-local binding resolves cleanly.
    assert desk.resolve_compound_only_occurrence_anchor(
        [0, 3], rows, col_index=5, col_token_order=6, source_sentence_idx=2, visual_idx=10
    ) == (2, '0')

    # Unambiguous visual binding resolves cleanly even within the same sentence.
    assert desk.resolve_compound_only_occurrence_anchor(
        [0, 1],
        rows,
        col_index=5,
        col_token_order=6,
        source_sentence_idx=1,
        bound_visual_by_row={'1': [6, 8]},
        visual_idx=6,
    ) == (1, '5')

    # Different canonical occurrences with no unambiguous local binding must fail fast, never guessing row 0.
    with pytest.raises(ValueError, match='Ambiguous compound occurrence anchor'):
        desk.resolve_compound_only_occurrence_anchor(
            [0, 1], rows, col_index=5, col_token_order=6, source_sentence_idx=1, visual_idx=0
        )


def test_ambiguous_compound_anchor_fails_render_before_any_view_persistence(preference_world):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    url, server, _ = preference_world
    zid = '20261119102705'
    source_text = 'multi-token and multi-token.'
    rows = [
        ['multi-token', 'multi-token', 'мульти-токен', '0', source_text, '1', '0'],
        ['multi-token', 'multi-token', 'мульти-токен', '0', source_text, '1', '5'],
    ]
    adapter = _seed_sqlite_session(server, zid, source_text, rows)

    request = Request(f'{url}/?session_zid={zid}&seq_num=1', headers={'X-API-Token': server.api_key})
    with pytest.raises(HTTPError) as exc_info:
        urlopen(request, timeout=10)
    assert exc_info.value.code >= 400
    body = exc_info.value.read().decode('utf-8', errors='replace')
    assert 'token-map' not in body
    assert 'Ambiguous compound occurrence anchor' in body

    with adapter.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM client_view_state WHERE session_zid=?', (zid,)).fetchone()[0] == 0
    assert not any(adapter.get_shared_selection(zid).values())

