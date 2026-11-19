"""Real-browser and owned-SQLite acceptance for settled source-drag view persistence."""
import copy
import json
import re

import pytest

import kardenwort_desk as desk
from kardenwort_client_views import ClientViewAdapter
from kardenwort_state_http import config_snapshot
from scripts.benchmark_release_interactions import HEADERS, ZID, payload, render_fixture
from tests.test_preference_commands import preference_world  # noqa: F401


WORD_COUNT = 8


@pytest.fixture
def drag_sqlite_page(page, preference_world):
    url, server, path = preference_world
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    _, rows = payload(WORD_COUNT)
    adapter.save_session(
        session_zid=ZID,
        source_language='de',
        target_language='en',
        source_raw_text=' '.join(row[0] for row in rows),
        text_mode='single',
        headers=HEADERS,
        data_rows=rows,
    )
    html = render_fixture(desk, WORD_COUNT, 'container')
    boot = json.loads(re.search(r'<script[^>]*id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S).group(1))
    boot['server_url'] = url
    boot['session_data'].update(server_url=url, api_token='test-controller-api-key')
    boot['session_data']['source_text'] = ' '.join(row[4] for row in rows)
    boot['token_manifest'] = [
        dict(visual_idx=i, sentence_idx=i + 1, token_order=i, atomic_id=i, row_ids=[i], lemma=row[1])
        for i, row in enumerate(rows)
    ]
    source_html = ' '.join(
        '<span class="word highlight-orange" data-word="{0}" data-word-idx="{1}" '
        'data-token-order="{1}" data-sentence-idx="{2}">{0}</span>.'.format(row[0], i, i + 1)
        for i, row in enumerate(rows)
    )
    for index in range(2):
        child = copy.deepcopy(boot['sentence_cards'][0])
        child.update(
            seq_num=index + 2,
            sentence_idx=index + 1,
            words=[copy.deepcopy(child['words'][index])],
            source_text=rows[index][0] + '.',
        )
        boot['sentence_cards'].append(child)

    def document(route):
        current = copy.deepcopy(boot)
        current['ui_config'].update(config_snapshot(server, {})['ui'])
        membership = adapter.get_shared_selection(ZID)
        for card in current['sentence_cards']:
            for word in card['words']:
                word['selected'] = '1' if membership.get(word['lemma'], False) else '0'
            card['selected_ids'] = [word['row_id'] for word in card['words'] if word['selected'] == '1']
        route.fulfill(content_type='text/html', body=desk.render_declarative_web_shell(current, source_html=source_html))

    view_requests = []
    view_writes = []

    def record_request(req):
        if req.method != 'POST' or '/api/v1/client-view' not in req.url:
            return
        body = json.loads(req.post_data or '{}')
        if 'active_card' not in body:
            return
        view_requests.append(body)

    def record_response(response):
        req = response.request
        if req.method != 'POST' or '/api/v1/client-view' not in req.url:
            return
        body = json.loads(req.post_data or '{}')
        if 'active_card' not in body:
            return
        view_writes.append(dict(status=response.status, body=body))

    page.on('request', record_request)
    page.on('response', record_response)
    page.context.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
    page.context.route(url + '/', document)
    yield page, url, server, adapter, view_writes, view_requests
    page.remove_listener('request', record_request)
    page.remove_listener('response', record_response)
    page.context.unroute_all(behavior='ignoreErrors')


def ready(page):
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate('window.__stateReady')


def client_id(page):
    match = re.search(r'kw-client=([0-9a-f]{32})', page.url)
    assert match, f'Missing kw-client handle in {page.url}'
    return match.group(1)


def word_center(page, index):
    box = page.locator(f'#source-container span.word[data-word-idx="{index}"]').bounding_box()
    assert box is not None, f'Word {index} has no bounding box'
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def active_visual_indices(page):
    return page.evaluate('''() => Array.from(document.querySelectorAll('#source-container span.word'))
        .filter(el => el.classList.contains('highlight-orange-active')
            || el.classList.contains('highlight-purple-active')
            || el.classList.contains('active-subtoken'))
        .map(el => Number(el.getAttribute('data-word-idx')))''')


def active_token_indices(page):
    return [item['visual_idx'] for item in page.evaluate('AppState.activeTokenSelections')]


@pytest.mark.parametrize('drag_words', [2, 4, 6])
def test_isolated_source_drag_persists_once_at_settlement(drag_sqlite_page, drag_words):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()
    view_requests.clear()

    x0, y0 = word_center(page, 0)
    page.mouse.move(x0, y0)
    page.mouse.down(button='left')

    # Immediate visual feedback on mousedown before pointer movement, zero premature writes.
    assert active_visual_indices(page) == [0]
    assert len(view_requests) == 0

    for idx in range(1, drag_words):
        xk, yk = word_center(page, idx)
        page.mouse.move(xk, yk)
        # Immediate local visual feedback and in-memory state during drag, zero intermediate writes.
        expected_so_far = list(range(idx + 1))
        assert active_visual_indices(page) == expected_so_far
        assert active_token_indices(page) == expected_so_far
        assert len(view_requests) == 0

    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')

    expected = list(range(drag_words))
    assert all(entry['status'] == 200 for entry in view_writes)
    assert len(view_writes) == 1, (
        f'Expected 1 settled client-view write for {drag_words}-word drag, got {len(view_writes)}'
    )

    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert stored['active_card'] == 1
    assert [item['visual_idx'] for item in stored['active_tokens']] == expected
    assert [(item['sentence_idx'], item['token_order']) for item in stored['active_tokens']] == [
        (i + 1, i) for i in expected
    ]

    page.reload()
    ready(page)
    assert active_token_indices(page) == expected
    assert active_visual_indices(page) == expected


def test_unchanged_final_focus_drag_avoids_redundant_view_write(drag_sqlite_page):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)

    # Select table row 0 so word 0 is table-selected while client focus remains empty [].
    page.locator('#lemma-table tbody tr[data-row-id="0"]').click()
    page.evaluate('flushPendingSaves()')
    assert page.evaluate('AppState.activeTokenSelections') == []
    before_stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert before_stored['active_tokens'] == []
    view_writes.clear()
    view_requests.clear()

    # Deselect-drag across words 0..1 leaves final client focus [] unchanged.
    x0, y0 = word_center(page, 0)
    x1, y1 = word_center(page, 1)
    page.mouse.move(x0, y0)
    page.mouse.down(button='left')
    page.mouse.move(x1, y1)
    assert page.evaluate('AppState.activeTokenSelections') == []
    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')

    assert len(view_writes) == 0, (
        f'Expected 0 redundant client-view writes for unchanged final focus, got {len(view_writes)}'
    )
    after_stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert after_stored['active_tokens'] == []
    assert after_stored['revision'] == before_stored['revision']

    page.reload()
    ready(page)
    assert page.evaluate('AppState.activeTokenSelections') == []


def test_reverse_drag_select_and_deselect_restore_exact_final_occurrences(drag_sqlite_page):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()
    view_requests.clear()

    # Forward drag 0 -> 4, then reverse back to 1 before mouseup.
    x0, y0 = word_center(page, 0)
    page.mouse.move(x0, y0)
    page.mouse.down(button='left')
    for idx in (1, 2, 3, 4):
        xk, yk = word_center(page, idx)
        page.mouse.move(xk, yk)
    assert active_token_indices(page) == [0, 1, 2, 3, 4]
    assert len(view_requests) == 0

    for idx in (3, 2, 1):
        xk, yk = word_center(page, idx)
        page.mouse.move(xk, yk)
    assert active_token_indices(page) == [0, 1]
    assert len(view_requests) == 0

    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')
    assert len(view_writes) == 1

    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert [item['visual_idx'] for item in stored['active_tokens']] == [0, 1]

    # Extend to 0..3 cleanly, then reverse-deselect 0 -> 2 -> back to 0 (leaving [1, 2, 3]).
    x2, y2 = word_center(page, 2)
    x3, y3 = word_center(page, 3)
    page.mouse.move(x2, y2)
    page.mouse.down(button='left')
    page.mouse.move(x3, y3)
    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')
    assert active_token_indices(page) == [0, 1, 2, 3]
    view_writes.clear()
    view_requests.clear()

    page.mouse.move(x0, y0)
    page.mouse.down(button='left')
    page.mouse.move(*word_center(page, 1))
    page.mouse.move(x2, y2)
    assert active_token_indices(page) == [3]
    page.mouse.move(*word_center(page, 1))
    page.mouse.move(x0, y0)
    assert active_token_indices(page) == [1, 2, 3]
    assert len(view_requests) == 0
    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')

    assert len(view_writes) == 1
    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert [item['visual_idx'] for item in stored['active_tokens']] == [1, 2, 3]

    page.reload()
    ready(page)
    assert active_token_indices(page) == [1, 2, 3]


def test_lost_button_bailout_settles_and_flushes_once(drag_sqlite_page):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()
    view_requests.clear()

    page.mouse.move(*word_center(page, 0))
    page.mouse.down(button='left')
    page.mouse.move(*word_center(page, 1))
    page.mouse.move(*word_center(page, 2))
    assert active_token_indices(page) == [0, 1, 2]
    assert len(view_requests) == 0

    # Simulate mouseover with LMB already released outside the window (buttons === 0).
    page.evaluate('''() => {
        const span = document.querySelector('#source-container span.word[data-word-idx="3"]');
        span.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, cancelable: true, buttons: 0}));
    }''')
    page.evaluate('flushPendingSaves()')

    assert len(view_writes) == 1
    assert active_token_indices(page) == [0, 1, 2]
    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert [item['visual_idx'] for item in stored['active_tokens']] == [0, 1, 2]

    # Clean up mouse button state for subsequent interactions and confirm reload restores [0, 1, 2].
    page.mouse.up(button='left')
    page.reload()
    ready(page)
    assert active_token_indices(page) == [0, 1, 2]


def test_held_earlier_ack_cannot_overwrite_newer_drag_and_coalesces_unsent_intent(drag_sqlite_page):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()
    view_requests.clear()

    held_routes = []

    def hold_first_write(route):
        body = json.loads(route.request.post_data or '{}')
        if 'active_card' in body and not held_routes:
            held_routes.append(route)
            return
        route.continue_()

    page.route('**/api/v1/client-view', hold_first_write)
    try:
        # Gesture 1: drag 0..1 -> first write is sent and held before ack.
        with page.expect_request(lambda r: r.method == 'POST' and '/api/v1/client-view' in r.url):
            page.mouse.move(*word_center(page, 0))
            page.mouse.down(button='left')
            page.mouse.move(*word_center(page, 1))
            page.mouse.up(button='left')

        # Pump Playwright's event dispatcher until the route handler captures Write #1.
        for _ in range(50):
            if held_routes:
                break
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(resolve))')

        assert len(held_routes) == 1
        assert len(view_requests) == 1
        assert view_requests[0]['expected_revision'] == 0
        assert [t['visual_idx'] for t in view_requests[0]['active_tokens']] == [0, 1]

        # Gesture 2 (unsent while Write #1 is held): drag 2..3.
        page.mouse.move(*word_center(page, 2))
        page.mouse.down(button='left')
        page.mouse.move(*word_center(page, 3))
        page.mouse.up(button='left')
        assert active_token_indices(page) == [0, 1, 2, 3]

        # Gesture 3 (supersedes unsent Gesture 2 while Write #1 is still held): drag 4..5.
        page.mouse.move(*word_center(page, 4))
        page.mouse.down(button='left')
        page.mouse.move(*word_center(page, 5))
        page.mouse.up(button='left')
        assert active_token_indices(page) == [0, 1, 2, 3, 4, 5]
        # Only Write #1 has been dispatched so far.
        assert len(view_requests) == 1

        # Release Write #1's held acknowledgement and wait for serial drain.
        held_routes[0].continue_()
        page.evaluate('flushPendingSaves()')
    finally:
        page.unroute('**/api/v1/client-view', hold_first_write)

    # Local focus must keep Gesture 3; Write #1's late ack must not restore [0, 1].
    assert active_token_indices(page) == [0, 1, 2, 3, 4, 5]
    assert active_visual_indices(page) == [0, 1, 2, 3, 4, 5]

    # Exactly 2 writes total: Write #1 ([0, 1], rev 0) and coalesced Write #2 ([0..5], rev 1).
    assert len(view_writes) == 2
    assert all(entry['status'] == 200 for entry in view_writes)
    assert view_writes[0]['body']['expected_revision'] == 0
    assert [t['visual_idx'] for t in view_writes[0]['body']['active_tokens']] == [0, 1]
    assert view_writes[1]['body']['expected_revision'] == 1
    assert [t['visual_idx'] for t in view_writes[1]['body']['active_tokens']] == [0, 1, 2, 3, 4, 5]

    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert stored['revision'] == 2
    assert [item['visual_idx'] for item in stored['active_tokens']] == [0, 1, 2, 3, 4, 5]

    page.reload()
    ready(page)
    assert active_token_indices(page) == [0, 1, 2, 3, 4, 5]


def test_single_click_escape_cancel_and_tab_switch_retain_semantics(drag_sqlite_page):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()
    view_requests.clear()

    # 1. Single-click on word 0 persists once and updates shared membership.
    page.locator('#source-container span.word[data-word-idx="0"]').click()
    page.evaluate('flushPendingSaves()')
    assert len(view_writes) == 1
    assert active_token_indices(page) == [0]
    assert sum(adapter.get_shared_selection(ZID).values()) == 1

    # 2. Two-step Escape mid-drag:
    #    1st Escape clears the pinned MVP bookmark and settles the active drag without continuing to word 3.
    view_writes.clear()
    page.mouse.move(*word_center(page, 1))
    page.mouse.down(button='left')
    page.mouse.move(*word_center(page, 2))
    assert active_token_indices(page) == [0, 1, 2]
    page.keyboard.press('Escape')
    page.mouse.move(*word_center(page, 3))
    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')
    assert active_token_indices(page) == [0, 1, 2]

    #    Next Escape (with no MVP bookmarks) mid-drag cancels the drag and clears all selections to [].
    page.mouse.move(*word_center(page, 3))
    page.mouse.down(button='left')
    page.mouse.move(*word_center(page, 4))
    assert active_token_indices(page) == [0, 1, 2, 3, 4]
    page.keyboard.press('Escape')
    page.mouse.move(*word_center(page, 5))
    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')
    assert active_token_indices(page) == []
    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert stored['active_tokens'] == []

    # 3. Controlled tab navigation mid-drag settles cleanly without duplicate writes.
    view_writes.clear()
    page.mouse.move(*word_center(page, 0))
    page.mouse.down(button='left')
    page.mouse.move(*word_center(page, 1))
    page.evaluate('WorkspaceTabs.switchToTab(2)')
    page.mouse.up(button='left')
    page.evaluate('flushPendingSaves()')
    assert len(view_writes) == 1
    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert stored['active_card'] == 2
    assert [item['visual_idx'] for item in stored['active_tokens']] == [0]

    page.reload()
    ready(page)
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == 2
    assert active_token_indices(page) == [0]


def test_save_and_action_barriers_settle_pending_drag_and_surface_failure(drag_sqlite_page):
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()
    view_requests.clear()

    # 1. Explicit save barrier (onSaveClick) during an active drag settles and flushes final intent.
    page.mouse.move(*word_center(page, 0))
    page.mouse.down(button='left')
    page.mouse.move(*word_center(page, 1))
    page.mouse.move(*word_center(page, 2))
    assert len(view_requests) == 0

    saved_ok = page.evaluate('window.onSaveClick(true)')
    page.mouse.up(button='left')
    assert saved_ok is True
    assert len(view_writes) == 1
    stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert [item['visual_idx'] for item in stored['active_tokens']] == [0, 1, 2]
    assert sum(adapter.get_shared_selection(ZID).values()) == 3

    # 2. Failed view write surfaces a truthful failure and stops gated actions before /session/reword.
    reword_requests = []
    page.on('request', lambda req: reword_requests.append(req.url) if '/session/reword' in req.url else None)

    def fail_view_write(route):
        body = json.loads(route.request.post_data or '{}')
        if 'active_card' in body:
            route.fulfill(
                status=500,
                content_type='application/json',
                body=json.dumps({'ok': False, 'message': 'Owned view write failure'}),
            )
            return
        route.continue_()

    page.route('**/api/v1/client-view', fail_view_write)
    try:
        page.mouse.move(*word_center(page, 3))
        page.mouse.down(button='left')
        page.mouse.move(*word_center(page, 4))
        page.mouse.up(button='left')

        save_result = page.evaluate('window.onSaveClick(true)')
        assert save_result is False

        page.evaluate('window.onRewordClick()')
        assert reword_requests == []
        toast_text = page.locator('#kw-toast-container').inner_text()
        assert 'Owned view write failure' in toast_text
    finally:
        page.unroute('**/api/v1/client-view', fail_view_write)

    # SQLite still holds the last acknowledged view [0, 1, 2] without corrupted partial state.
    stored_after_fail = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert [item['visual_idx'] for item in stored_after_fail['active_tokens']] == [0, 1, 2]
