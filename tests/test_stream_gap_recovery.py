"""Bounded gaps recover real committed storage without discarding local intent."""
import json
import threading

import pytest
from playwright.sync_api import expect

from kardenwort_events import EventDispatcher
from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import ZID, TOKEN, boundary_html, open_browser, seed_session as seed_storage_session, use_backend, wait_for_held
from tests.test_release_save_convergence import native_http_window as native_storage_window


def seed_session(server, adapter):
    """This scenario requires an active cached session as well as durable rows."""
    path = seed_storage_session(server, adapter)
    comments, headers, rows = adapter.load_tsv_rows(path)
    server.arbiter.sessions[ZID] = dict(session_zid=ZID, tsv_path=str(path), language='de',
        headers=headers, data_rows=rows, comments=comments, translatedText='The house. The tree.', lock=threading.Lock())


def native_http_window(browser, html, url, consumer):
    context, page = native_storage_window(browser, html, url, consumer)
    page.on('console', lambda message: print('Owned native console:', message.type, message.text))
    page.evaluate('consumer => { stopSelectionBridge(); startSelectionBridge(consumer,200); }', consumer)
    return context, page


def set_committed_translation(adapter, server, value):
    if adapter.backend_name == 'sqlite':
        adapter.batch_update_words(ZID, [dict(sentence_index=1, token_order=0,
            updates={'WordDestination': value, 'word_provenance': 'fixture:gap'})])
    else:
        memo = server.arbiter.sessions[ZID]
        path = memo['tsv_path']
        from pathlib import Path
        path = Path(path)
        with adapter.file_lock(path):
            comments, headers, rows = adapter.load_tsv_rows(path)
            rows[0][headers.index('WordDestination')] = value
            adapter.save_tsv_rows_safely(path, comments, headers, rows)


def gap():
    EventDispatcher.get_instance().publish(ZID, {'type': 'resync_required'})


def test_repeated_overflow_coalesces_until_gap_is_consumed():
    dispatcher = EventDispatcher()
    slow, healthy = dispatcher.register('owned-session'), dispatcher.register('owned-session')
    for index in range(10000):
        dispatcher.publish('owned-session', dict(sequence=index))
        assert healthy.get_nowait()['sequence'] == index
    assert slow.qsize() == 1
    assert slow.get_nowait()['type'] == 'resync_required'
    dispatcher.publish('owned-session', {'stage': 'finished'})
    assert slow.get_nowait()['stage'] == 'finished'


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('draft', ['active', 'pending'])
def test_browser_gap_refreshes_committed_rows_and_preserves_editor(page, tmp_path, running_controller, backend, draft):
    url, server, _ = running_controller
    adapter = use_backend(server, backend)
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    edited = page.locator('#lemma-table tr[data-row-id="1"] td[data-col="WordDestination"]')
    edited.dblclick()
    editor = edited.locator('input.cell-editor')
    editor.fill('local draft during gap')
    if draft == 'pending':
        editor.press('Enter')
    before = page.evaluate('getDeltas()')
    set_committed_translation(adapter, server, 'fresh committed after gap')
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    connection = page.evaluate('SSEBridge.getActiveConnectionId()')
    gap()
    page.wait_for_function('old => SSEBridge.getActiveConnectionId() !== old', arg=connection)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    fresh = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')
    expect(fresh).to_have_text('fresh committed after gap')
    if draft == 'active':
        expect(editor).to_have_value('local draft during gap')
    else:
        expect(edited.locator('.scrollable-cell')).to_have_text('local draft during gap')
    assert page.evaluate('getDeltas()') == before
    assert any('committed=1' in request for request in requests)
    assert not any(path in request for request in requests for path in ('/session/reword', '/session/retext', '/session/create'))
    memo = server.arbiter.sessions[ZID]
    assert memo['data_rows'][0][memo['headers'].index('WordDestination')] == 'fresh committed after gap'


@pytest.mark.parametrize('response', ['offline', 'unauthorized'])
def test_gap_status_failure_stays_unsynchronized_and_retries_full_state(page, tmp_path, running_controller, response):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    adapter = server.arbiter.storage_adapter
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    set_committed_translation(adapter, server, 'retry must read this value')
    held = []
    page.route('**/session/status?*committed=1', lambda route: held.append(route))
    gap()
    for count in range(1, 4):
        wait_for_held(page, held, count)
        if response == 'offline':
            held[-1].abort('failed')
        else:
            held[-1].fulfill(status=401, json={'status': 'error', 'error_code': 'UNAUTHORIZED'})
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'failed'")
    page.unroute('**/session/status?*committed=1')
    page.evaluate('''() => {
        window.__retryResult = 'pending';
        reconcileStreamMembership({session:SSEBridge.getActiveZid(),connectionId:SSEBridge.getActiveConnectionId()})
            .then(()=>__retryResult='done',()=>__retryResult='failed');
    }''')
    page.wait_for_function("__retryResult === 'done'")
    expect(page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')).to_have_text('retry must read this value')


def test_old_gap_status_cannot_override_new_connection(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    adapter = server.arbiter.storage_adapter
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    held = []
    page.route('**/session/status?*committed=1', lambda route: held.append(route))
    set_committed_translation(adapter, server, 'old snapshot')
    gap()
    wait_for_held(page, held, 1)
    old = held[0].fetch()
    set_committed_translation(adapter, server, 'new snapshot')
    gap()
    wait_for_held(page, held, 2)
    held[1].fulfill(response=held[1].fetch())
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    held[0].fulfill(response=old)
    page.evaluate('Boolean(AppState)')
    expect(page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')).to_have_text('new snapshot')


@pytest.mark.parametrize('unauthorized_retry', [False, True])
def test_native_paused_overflow_recovers_through_existing_bridge(browser, tmp_path, running_controller, unauthorized_retry):
    url, server, _ = running_controller
    adapter = use_backend(server, 'sqlite')
    seed_session(server, adapter)
    context, page = native_http_window(browser, boundary_html(tmp_path), url, 'gap-native')
    try:
        set_committed_translation(adapter, server, 'latest native committed')
        dispatcher = EventDispatcher.get_instance()
        for index in range(1100):
            dispatcher.publish(ZID, dict(stage='enrichment', sequence=index))
        page.clock.run_for(200)
        assert page.locator('#sse-status-indicator').get_attribute('data-sync') == 'syncing'
        if unauthorized_retry:
            server.api_key = 'temporary-fixture-key'
            page.clock.run_for(200)
            assert page.locator('#sse-status-indicator').get_attribute('data-sync') == 'failed'
            server.api_key = TOKEN
        page.clock.run_for(200)
        expect(page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')).to_have_text('latest native committed')
        assert page.locator('#sse-status-indicator').get_attribute('data-sync') == 'synced'
        assert any(request['body'].get('recover') for request in page.evaluate('__nativeRequests'))
        assert not page.evaluate('isDirty()')
    finally:
        context.close()
