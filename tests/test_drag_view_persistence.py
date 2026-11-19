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

    view_writes = []

    def record_response(response):
        req = response.request
        if req.method != 'POST' or '/api/v1/client-view' not in req.url:
            return
        body = json.loads(req.post_data or '{}')
        if 'active_card' not in body:
            return
        view_writes.append(dict(status=response.status, body=body))

    page.on('response', record_response)
    page.context.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
    page.context.route(url + '/', document)
    yield page, url, server, adapter, view_writes
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


@pytest.mark.parametrize('drag_words', [2, 4, 6])
def test_isolated_source_drag_persists_once_at_settlement(drag_sqlite_page, drag_words):
    page, url, server, adapter, view_writes = drag_sqlite_page
    page.goto(url + '/')
    ready(page)
    view_writes.clear()

    x0, y0 = word_center(page, 0)
    page.mouse.move(x0, y0)
    page.mouse.down(button='left')

    # Immediate visual feedback on mousedown before pointer movement.
    assert active_visual_indices(page) == [0]

    for idx in range(1, drag_words):
        xk, yk = word_center(page, idx)
        page.mouse.move(xk, yk)
        # Immediate local visual feedback and in-memory state during drag.
        expected_so_far = list(range(idx + 1))
        assert active_visual_indices(page) == expected_so_far
        assert [item['visual_idx'] for item in page.evaluate('AppState.activeTokenSelections')] == expected_so_far

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
    assert [item['visual_idx'] for item in page.evaluate('AppState.activeTokenSelections')] == expected
    assert active_visual_indices(page) == expected


def test_unchanged_final_focus_drag_avoids_redundant_view_write(drag_sqlite_page):
    page, url, server, adapter, view_writes = drag_sqlite_page
    page.goto(url + '/')
    ready(page)

    # Select table row 0 so word 0 is table-selected while client focus remains empty [].
    page.locator('#lemma-table tbody tr[data-row-id="0"]').click()
    page.evaluate('flushPendingSaves()')
    assert page.evaluate('AppState.activeTokenSelections') == []
    before_stored = ClientViewAdapter(adapter.db).get(ZID, client_id(page))
    assert before_stored['active_tokens'] == []
    view_writes.clear()

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
