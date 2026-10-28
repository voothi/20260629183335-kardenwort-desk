"""Phase 3: real storage and controller; only transport completion is gated."""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from kardenwort_events import EventDispatcher
from kardenwort_controller import ControllerHTTPServer
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import (
    ZID, OTHER_ZID, TOKEN, boundary_html, data, native_window, drain_native,
    open_browser, post, restored_membership, save_selection, seed_session,
    use_backend, wait_for_held, wait_for_membership,
)


@pytest.mark.parametrize('mode', ['desktop', 'container'])
@pytest.mark.parametrize('local_selected', [False, True])
def test_membership_with_many_pending_selections_has_linear_dom_work(page, mode, local_selected):
    from scripts.benchmark_release_interactions import render_fixture, TRANSPORT
    import kardenwort_desk as desk

    count = 256
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content(render_fixture(desk, count, mode).replace('<head>', '<head>' + TRANSPORT))
    page.wait_for_function('typeof applySharedSelection === "function"')
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    result = page.evaluate('''localSelected => {
        const membership = {};
        document.querySelectorAll('#lemma-table tbody tr[data-row-id] .col-lemma').forEach(
            cell => membership[cell.textContent.trim().toLowerCase()] = !localSelected);
        applySharedSelection(membership);
        if(localSelected) selectAllRows(); else clearAllSelections();
        const boxes = () => Array.from(document.querySelectorAll('#source-container .word, #lemma-table tbody tr[data-row-id]'))
            .map(el => {const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];});
        const before = boxes();
        const original = Element.prototype.getAttribute;
        let reads = 0;
        Element.prototype.getAttribute = function(name) {
            reads++;
            return original.call(this, name);
        };
        try { applySharedSelection(membership); }
        finally { Element.prototype.getAttribute = original; }
        return {reads, selected:Object.keys(AppState.selectedLemmas).length,
                pending:JSON.parse(getDeltas()), dirty:isDirty(), requests:__requests,
                before, after:boxes()};
    }''', local_selected)
    assert result['selected'] == (count if local_selected else 0)
    assert result['dirty']
    assert len(result['pending']) == count
    assert all(delta['column'] == 'DeskSelected' and delta['value'] == ('1' if local_selected else '')
               for delta in result['pending'])
    assert not any('/session/save' in url for url in result['requests'])
    assert errors == []
    assert result['before'] == result['after']
    assert result['reads'] < count * 48, result['reads']


def test_membership_preserves_pending_selection_on_inactive_card(page, tmp_path):
    from tests.test_release_audit_regressions import audit_html, TRANSPORT

    page.set_content(audit_html(tmp_path, container=True).replace('<head>', '<head>' + TRANSPORT))
    page.wait_for_function('typeof applySharedSelection === "function"')
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.evaluate('''() => {
        clearAllSelections(); clearDirty();
        const cards = WorkspaceTabs.getCards();
        const first = cards.find(card => card.sentence_idx === 1);
        const second = cards.find(card => card.sentence_idx === 2);
        WorkspaceTabs.switchToTab(second.seq_num);
        selectAllRows();
        WorkspaceTabs.switchToTab(first.seq_num);
        window.__pendingBefore = JSON.parse(getDeltas());
        applySharedSelection({haus:true,baum:false});
    }''')
    assert page.evaluate('!!AppState.selectedLemmas.haus && !!AppState.selectedLemmas.baum && isDirty()')
    pending = page.evaluate('JSON.parse(getDeltas())')
    assert pending == page.evaluate('__pendingBefore')
    assert all(delta['value'] == '1' for delta in pending)
    assert any(delta['sentence_idx'] == 2 for delta in pending)


def held_selection_save(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    saves = []
    page.route('**/session/save', lambda route: saves.append(route))
    page.evaluate('toggleRowSelection(1, true); void onSaveClick(true)')
    wait_for_held(page, saves, 1)
    save_selection(url, 0, True)
    response = saves[0].fetch()
    wait_for_membership(page, 2)
    page.evaluate('__deliverMembership()')
    return saves, response


def test_watchdog_cleanup_keeps_persistent_membership_stream(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.evaluate('void _kwPollSessionStatus()')
    page.wait_for_function('_kwWatchdogResolved === true')
    assert page.evaluate('__testStream.source.readyState') == 1
    save_selection(url, 0, True)
    wait_for_membership(page, 1)
    page.evaluate('__deliverMembership()')
    assert page.evaluate('!!AppState.selectedLemmas.haus && !isDirty()')
    assert restored_membership(adapter) == {'haus': True, 'baum': False}


def test_disconnected_stream_callbacks_cannot_change_current_session(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.evaluate('args => { window.__oldStream=__testStream; SSEBridge.disconnect(); SSEBridge.connect(args.zid,args.url); }',
                  dict(zid=ZID, url=url))
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.status === 'connected'")
    page.evaluate("""() => {
        __oldStream.onmessage({data:JSON.stringify({shared_selection:{haus:true,baum:true}})});
        __oldStream.onerror({});
        __oldStream.onopen({});
    }""")
    assert page.evaluate('!AppState.selectedLemmas.haus && !AppState.selectedLemmas.baum && !isDirty()')
    assert page.locator('#sse-status-indicator').get_attribute('data-status') == 'connected'


@pytest.mark.parametrize('remote_after_commit', [False, True])
@pytest.mark.parametrize('reversal', [False, True])
@pytest.mark.parametrize('event_first', [False, True])
def test_same_lemma_commit_event_and_reversal_orderings(
        page, tmp_path, running_controller, remote_after_commit, reversal, event_first):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    saves = []
    page.route('**/session/save', lambda route: saves.append(route))
    page.evaluate('toggleRowSelection(0, true); void onSaveClick(true)')
    wait_for_held(page, saves, 1)
    if remote_after_commit:
        response = saves[0].fetch()
        save_selection(url, 0, False)
    else:
        save_selection(url, 0, False)
        response = saves[0].fetch()
    wait_for_membership(page, 2)
    if reversal:
        page.evaluate('toggleRowSelection(0, false)')
    if event_first:
        page.evaluate('__deliverMembership()')
    saves[0].fulfill(response=response)
    page.wait_for_function("document.getElementById('kw-btn-save').textContent.indexOf('Saving') < 0")
    if not event_first:
        page.evaluate('__deliverMembership()')
    if reversal:
        assert page.evaluate('isDirty() && !AppState.selectedLemmas.haus')
        page.evaluate('void onSaveClick(true)')
        wait_for_held(page, saves, 2)
        response = saves[1].fetch()
        wait_for_membership(page, 3)
        page.evaluate('__deliverMembership()')
        saves[1].fulfill(response=response)
    page.wait_for_function('!isDirty()')
    expected = not (remote_after_commit or reversal)
    assert restored_membership(adapter)['haus'] is expected
    assert page.evaluate('!!AppState.selectedLemmas.haus') is expected
    page.clock.run_for(1000)
    assert len(saves) == (2 if reversal else 1)


@pytest.mark.parametrize('failure', ['http', 'invalid-payload'])
def test_reconciliation_failure_retains_dirty_state_and_retries_only_read(
        page, tmp_path, running_controller, failure):
    saves, response = held_selection_save(page, tmp_path, running_controller)
    reads = []

    def fail_read(route):
        reads.append(route.request.post_data_json)
        route.fulfill(status=503 if failure == 'http' else 200, json={'ok': failure != 'http'})

    page.route('**/internal/selection', fail_read)
    saves[0].fulfill(response=response)
    page.wait_for_function("document.querySelector('.kw-toast-error') !== null")
    assert page.evaluate('isDirty()')
    assert len(reads) == 1
    assert 'client_id' not in reads[0]
    page.unroute('**/internal/selection', fail_read)
    page.evaluate('void onSaveClick(true)')
    page.wait_for_function('!isDirty()')
    assert len(saves) == 1, 'Reconciliation retry must not repeat the committed save'
    assert restored_membership(running_controller[2]) == {'haus': True, 'baum': True}
    assert page.evaluate('!!AppState.selectedLemmas.haus && !!AppState.selectedLemmas.baum')


@pytest.mark.parametrize('stale', ['save', 'read'])
@pytest.mark.parametrize('lifecycle', ['remount', 'session'])
def test_stale_save_or_read_cannot_change_current_intent(page, tmp_path, running_controller, stale, lifecycle):
    saves, response = held_selection_save(page, tmp_path, running_controller)
    reads = []
    page.route('**/internal/selection', lambda route: reads.append(route))
    if stale == 'read':
        saves[0].fulfill(response=response)
        wait_for_held(page, reads, 1)
        response = reads[0].fetch()
    if lifecycle == 'remount':
        page.add_script_tag(content=Path('web/js/app.js').read_text(encoding='utf-8-sig'))
    else:
        page.evaluate("zid => { document.getElementById('session-zid').textContent=zid; WorkspaceTabs.getCards().forEach(c=>c.zid=zid); }", OTHER_ZID)
    page.evaluate('toggleRowSelection(0, false); toggleRowSelection(1, false)')
    before = page.evaluate('({selected:AppState.selectedLemmas, deltas:getDeltas()})')
    (saves if stale == 'save' else reads)[0].fulfill(response=response)
    page.wait_for_function("document.querySelector('.kw-toast-error') !== null")
    assert page.evaluate('({selected:AppState.selectedLemmas, deltas:getDeltas()})') == before
    assert len(saves) == 1


def test_new_reversal_and_cell_edit_during_reconciliation_remain_pending(page, tmp_path, running_controller):
    saves, response = held_selection_save(page, tmp_path, running_controller)
    reads = []
    page.route('**/internal/selection', lambda route: reads.append(route))
    saves[0].fulfill(response=response)
    wait_for_held(page, reads, 1)
    page.evaluate('toggleRowSelection(1, false)')
    cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]')
    cell.dblclick()
    cell.locator('input').fill('new local edit')
    cell.locator('input').press('Enter')
    reads[0].fulfill(response=reads[0].fetch())
    page.wait_for_function("document.getElementById('kw-btn-save').textContent.indexOf('Saving') < 0")
    deltas = json.loads(page.evaluate('getDeltas()'))
    assert any(d['column'] == 'DeskSelected' and d['value'] == '' for d in deltas)
    assert any(d['column'] == 'WordDestination' and d['value'] == 'new local edit' for d in deltas)
    assert page.evaluate('!!AppState.selectedLemmas.haus && !AppState.selectedLemmas.baum')
    page.evaluate('undo(); redo()')
    assert any(d['value'] == 'new local edit' for d in json.loads(page.evaluate('getDeltas()')))
    assert len(saves) == 1


def test_remote_membership_preserves_active_edit_filter_and_empty_action(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter, selected=True)
    open_browser(page, tmp_path, url, selected=True)
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.evaluate('setFilterSelectedOnly(true)')
    cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]')
    cell.evaluate('cell => makeEditable(cell)')
    cell.locator('input').fill('editing during remote update')
    page.evaluate('window.__focusNode = document.activeElement; __focusNode.setSelectionRange(2,7)')
    save_selection(url, 1, True)
    wait_for_membership(page, 1)
    page.evaluate('__deliverMembership()')
    assert page.evaluate('document.activeElement === __focusNode')
    assert page.evaluate('[__focusNode.selectionStart, __focusNode.selectionEnd]') == [2, 7]
    assert cell.locator('input').input_value() == 'editing during remote update'
    assert page.evaluate('AppState.filterSelectedOnly && !!AppState.selectedLemmas.baum')
    cell.locator('input').press('Enter')
    page.evaluate('clearAllSelectionsAndNotify()')
    action = page.get_by_role('button', name='show all words', exact=True)
    assert action.is_visible()
    action.click()
    assert page.evaluate('!AppState.filterSelectedOnly')
    assert any(delta['column'] == 'WordDestination' and delta['value'] == 'editing during remote update'
               for delta in json.loads(page.evaluate('getDeltas()')))
    restored = adapter.restore_session(ZID)
    assert restored['data_rows'][0][restored['headers'].index('WordDestination')] == 'house'


def test_native_idle_ticks_preserve_nodes_and_configured_cadence(browser, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    context, page, requests = native_window(browser, boundary_html(tmp_path), url, 'idle-window')
    try:
        drain_native(page, url, requests)
        page.clock.pause_at(page.evaluate('Date.now()') + 1000)
        page.evaluate("""() => {
            stopSelectionBridge();
            startSelectionBridge('idle-window', 750);
            window.__idleMutations = 0;
            window.__idleNode = document.querySelector('#lemma-table tr[data-row-id]');
            window.__observer = new MutationObserver(changes => __idleMutations += changes.length);
            __observer.observe(document.getElementById('lemma-table'), {subtree:true,childList:true,attributes:true});
        }""")
        requests.clear()  # The explicit stop belongs to setup, before measuring idle ticks.
        started = page.evaluate('performance.now()')
        page.clock.run_for(2999)
        assert len(requests) == 3
        page.clock.run_for(1)
        assert len(requests) == 4
        assert all(body['client_id'] == 'idle-window' for body in requests)
        assert page.evaluate('__idleMutations') == 0
        assert page.evaluate("__idleNode === document.querySelector('#lemma-table tr[data-row-id]')")
        assert page.evaluate('performance.now()') - started == 3000
    finally:
        context.close()


def test_close_disposes_unavailable_session_without_opening_database(running_controller):
    url, server, adapter = running_controller
    dispatcher = EventDispatcher.get_instance()
    dispatcher.poll_events(ZID, 'missing-session')
    assert (ZID, 'missing-session') in dispatcher._polling_clients
    status, payload = post(url, '/internal/selection', dict(session_zid=ZID, client_id='missing-session', close=True))
    assert status == 200, payload
    assert (ZID, 'missing-session') not in dispatcher._polling_clients
    assert data(payload)['events'] == []


def test_server_maintenance_expires_abandoned_consumer_without_another_poll(monkeypatch):
    dispatcher = EventDispatcher.get_instance()
    dispatcher.poll_events(ZID, 'abandoned')
    dispatcher.poll_events(ZID, 'live')
    before = time.monotonic()
    # Only the nondeterministic clock is controlled; queues and maintenance are real.
    monkeypatch.setattr('kardenwort_events.time.monotonic', lambda: before + 59)
    dispatcher.poll_events(ZID, 'live', accept=False)
    monkeypatch.setattr('kardenwort_events.time.monotonic', lambda: before + 61)
    ControllerHTTPServer.service_actions(None)
    assert (ZID, 'abandoned') not in dispatcher._polling_clients
    assert (ZID, 'live') in dispatcher._polling_clients
    dispatcher.poll_events(ZID, 'live', close=True)


def native_http_window(browser, html, url, consumer):
    """A synchronous HTTP transport runs the shared JS against the real controller."""
    context = browser.new_context()
    page = context.new_page()
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.add_init_script('window.EventSource = undefined')
    page.route(url + '/native-http', lambda route: route.fulfill(body=html, content_type='text/html'))
    page.goto(url + '/native-http')
    page.evaluate("""args => {
        window.__nativeRequests = [];
        window.__receivedStages = [];
        const receive = window.receiveUpdate;
        window.receiveUpdate = event => {
            if (event.probe_id === 'native-queue-probe') __receivedStages.push(event.stage);
            return receive(event);
        };
        window.ahkCall = (action, body) => {
            if (action !== 'get_shared_selection' && action !== 'persist_selection') return '';
            const path = action === 'get_shared_selection' ? '/internal/selection' : '/session/save';
            const request = new XMLHttpRequest();
            request.open('POST', path, false);
            request.setRequestHeader('Content-Type', 'application/json');
            request.setRequestHeader('X-API-Token', args.token);
            __nativeRequests.push({path, body:JSON.parse(body)});
            request.send(body);
            return request.responseText;
        };
        startSelectionBridge(args.consumer, 500);
    }""", dict(consumer=consumer, token=TOKEN))
    page.clock.run_for(500)
    return context, page


def test_two_native_http_consumers_hold_and_recover_independent_queues(browser, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    html = boundary_html(tmp_path)
    windows = [native_http_window(browser, html, url, identity)
               for identity in ('http-window-A', 'http-window-B')]
    try:
        first, second = [window[1] for window in windows]
        first.evaluate('toggleRowSelection(1, true); window.__submitted = captureSaveSnapshot()')
        for stage in ('translated_text', 'finished'):
            server.arbiter.emit_event(ZID, {'stage': stage, 'probe_id': 'native-queue-probe'})
        first.clock.run_for(500)
        second.clock.run_for(500)
        dispatcher = EventDispatcher.get_instance()
        assert dispatcher._polling_clients[(ZID, 'http-window-A')][0].qsize() == 2
        assert dispatcher._polling_clients[(ZID, 'http-window-B')][0].qsize() == 0
        assert second.evaluate('AppState.stage') == 'finished'
        response = save_selection(url, 1, True)
        first.evaluate('response => acknowledgeSaveSnapshot(__submitted, JSON.stringify(response))', response)
        assert first.evaluate('!isDirty() && !!AppState.selectedLemmas.baum')
        first.clock.run_for(500)
        assert dispatcher._polling_clients[(ZID, 'http-window-A')][0].qsize() == 0
        assert first.evaluate('__receivedStages.slice(0,2)') == ['translated_text', 'finished']
        assert first.evaluate('AppState.isFinished')
        assert first.evaluate("__nativeRequests.every(r=>r.path !== '/session/save')"), 'Reads must not echo a save'
        first.evaluate('stopSelectionBridge(); stopSelectionBridge()')
        assert (ZID, 'http-window-A') not in dispatcher._polling_clients
        assert (ZID, 'http-window-B') in dispatcher._polling_clients
    finally:
        for context, _ in windows:
            context.close()


def test_native_ack_read_failure_retries_without_repeating_committed_save(browser, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    context, page = native_http_window(browser, boundary_html(tmp_path), url, 'native-retry')
    try:
        page.evaluate('toggleRowSelection(1, true); window.__submitted = captureSaveSnapshot()')
        response = save_selection(url, 1, True)
        save_selection(url, 0, True)
        page.clock.run_for(500)
        # Use a real unauthorized response for the synchronous reconciliation read.
        _, denied = post(url, '/internal/selection', dict(session_zid=ZID), token='invalid')
        page.evaluate('denied => { window.__realAhkCall = ahkCall; window.ahkCall = () => JSON.stringify(denied); }', denied)
        with pytest.raises(Exception, match='reconciliation|authorized|token|authentication'):
            page.evaluate('response => acknowledgeSaveSnapshot(__submitted, JSON.stringify(response))', response)
        assert page.evaluate('isDirty()')
        page.evaluate("ahkCall=__realAhkCall; window.__retry=captureSaveSnapshot(); acknowledgeSaveSnapshot(__retry, '{\"ok\":true}')")
        assert page.evaluate('!isDirty() && !!AppState.selectedLemmas.haus && !!AppState.selectedLemmas.baum')
        assert page.evaluate("__nativeRequests.every(r=>r.path !== '/session/save')")
    finally:
        context.close()


def test_native_http_windows_in_different_sessions_remain_isolated(browser, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    seed_session(server, adapter, zid=OTHER_ZID)
    html = boundary_html(tmp_path)
    windows = [native_http_window(browser, html, url, 'different-A'),
               native_http_window(browser, html.replace(ZID, OTHER_ZID), url, 'different-B')]
    try:
        first, second = [window[1] for window in windows]
        server.arbiter.emit_event(ZID, {'stage': 'finished', 'probe_id': 'native-queue-probe'})
        first.clock.run_for(500)
        second.clock.run_for(500)
        assert first.evaluate('__receivedStages') == ['finished']
        assert second.evaluate('__receivedStages') == []
        save_selection(url, 0, True)
        first.clock.run_for(500)
        second.clock.run_for(500)
        assert first.evaluate('!!AppState.selectedLemmas.haus && !isDirty()')
        assert second.evaluate('!AppState.selectedLemmas.haus && !isDirty()')
        first.evaluate('stopSelectionBridge()')
        assert (OTHER_ZID, 'different-B') in EventDispatcher.get_instance()._polling_clients
        assert restored_membership(adapter, OTHER_ZID) == {'haus': False, 'baum': False}
    finally:
        for context, _ in windows:
            context.close()


def test_native_host_allocates_unique_ids_and_disposes_without_document(running_controller, tmp_path):
    _, server, _ = running_controller
    native = Path(os.environ.get('KARDENWORT_NATIVE_REPO',
                                 Path(__file__).resolve().parents[2] / '20240411110510-autohotkey'))
    executable = next(Path('C:/AHK').glob('AutoHotkey_*/AutoHotkey64.exe'))
    dispatcher = EventDispatcher.get_instance()
    for consumer in ('host-current', 'host-other'):
        dispatcher.poll_events(ZID, consumer)
    harness = tmp_path / 'native-lifecycle.ahk'
    harness.write_text(f'''#Requires AutoHotkey v2.0
global G_FsmTestMode := true
#Include "{native / 'Lib/B64Util.ahk'}"
#Include "{native / 'kardenwort-window/kardenwort-window.ahk'}"
G_ServerHost := "127.0.0.1"
G_ControllerPort := {server.server_port}
G_ServerApiKey := "{TOKEN}"
first := CreateSelectionConsumerID(1001)
second := CreateSelectionConsumerID(1002)
reusedHwnd := CreateSelectionConsumerID(1001)
StopNativeSelectionSubscription({{ZID:"{ZID}", SelectionConsumerID:"host-current", wb:""}})
FileAppend(first "`n" second "`n" reusedHwnd, "*")
ExitApp(0)
''', encoding='utf-8')
    result = subprocess.run([str(executable), '/ErrorStdOut', str(harness)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    identities = result.stdout.splitlines()
    assert len(identities) == 3 and len(set(identities)) == 3
    assert all(identity.split(':')[1] == hwnd for identity, hwnd in zip(identities, ('1001', '1002', '1001')))
    assert (ZID, 'host-current') not in dispatcher._polling_clients
    assert (ZID, 'host-other') in dispatcher._polling_clients
    dispatcher.poll_events(ZID, 'host-other', close=True)


@pytest.mark.parametrize('command,path', [
    ('onUpdateClick', '/session/status'), ('onRetextClick', '/session/retext'),
    ('onRewordClick', '/session/reword'), ('onSendToAnkiClick', '/session/export'),
])
def test_command_waits_for_save_and_reconciliation(page, tmp_path, running_controller, command, path):
    saves, response = held_selection_save(page, tmp_path, running_controller)
    reads, commands = [], []
    page.route('**/internal/selection', lambda route: reads.append(route))

    def receive_command(route):
        commands.append(route.request)
        route.fulfill(json={'ok': True, 'stage': 'finished'})

    page.route('**' + path + '*', receive_command)
    page.evaluate('command => { void window[command](); }', command)
    assert not commands
    saves[0].fulfill(response=response)
    wait_for_held(page, reads, 1)
    assert not commands
    reads[0].fulfill(response=reads[0].fetch())
    deadline = time.monotonic() + 5
    while not commands and time.monotonic() < deadline:
        page.evaluate('Boolean(window.getDeltas)')
    assert len(commands) == 1
    assert restored_membership(running_controller[2]) == {'haus': True, 'baum': True}
    assert page.evaluate('!isDirty()')


def hold_stream_recovery(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter, selected=True)
    open_browser(page, tmp_path, url, selected=True)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    reads = []
    page.route('**/internal/selection', lambda route: reads.append(route))
    page.evaluate("""() => {
        window.__recoveryResult = 'pending';
        window.__recovery = reconcileStreamMembership({
            session: SSEBridge.getActiveZid(), connectionId: SSEBridge.getActiveConnectionId()
        }).then(() => { __recoveryResult = 'resolved'; }, () => { __recoveryResult = 'rejected'; });
    }""")
    wait_for_held(page, reads, 1)
    return reads


@pytest.mark.parametrize('command,path', [
    ('onUpdateClick', '/session/status'), ('onRetextClick', '/session/retext'),
    ('onRewordClick', '/session/reword'), ('onSendToAnkiClick', '/session/export'),
])
def test_stream_recovery_blocks_clean_commands(page, tmp_path, running_controller, command, path):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    url, _, adapter = running_controller
    save_selection(url, 0, False)
    save_selection(url, 1, True)
    commands, saves = [], []

    def receive_command(route):
        commands.append(route.request)
        route.fulfill(json={'ok': True, 'stage': 'finished'})

    page.route('**' + path + '*', receive_command)
    page.route('**/session/save', lambda route: saves.append(route))
    page.evaluate('command => { void window[command](); }', command)
    page.evaluate('Boolean(window.getDeltas)')
    assert not commands, 'A clean page still needs its stream recovery barrier'
    reads[0].fulfill(response=reads[0].fetch())
    wait_for_held(page, commands, 1)
    assert page.evaluate('!AppState.selectedLemmas.haus && !!AppState.selectedLemmas.baum && !isDirty()')
    if path == '/session/export':
        assert commands[0].post_data_json['selected_row_ids'] == [1]
    assert not saves, 'Authoritative recovery must not echo a save'
    assert restored_membership(adapter) == {'haus': False, 'baum': True}


@pytest.mark.parametrize('lifecycle', ['disconnect', 'reconnect', 'error', 'remount', 'session'])
def test_stream_recovery_rejects_inactive_completion(page, tmp_path, running_controller, lifecycle):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    save_selection(running_controller[0], 0, False)
    # Capture the newer authoritative response, then make its lifecycle obsolete.
    response = reads[0].fetch()
    if lifecycle == 'disconnect':
        page.evaluate('SSEBridge.disconnect()')
    elif lifecycle == 'reconnect':
        page.evaluate('url => { SSEBridge.disconnect(); SSEBridge.connect("' + ZID + '",url); }', running_controller[0])
        wait_for_held(page, reads, 2)
    elif lifecycle == 'remount':
        page.add_script_tag(content=Path('web/js/app.js').read_text(encoding='utf-8-sig'))
        wait_for_held(page, reads, 2)
    elif lifecycle == 'error':
        page.evaluate('__testStream.onerror({})')
    else:
        page.evaluate("zid => { document.getElementById('session-zid').textContent=zid; WorkspaceTabs.getCards().forEach(c=>c.zid=zid); }", OTHER_ZID)
    before = page.evaluate("""() => ({selected:!!AppState.selectedLemmas.haus,
        sync:document.getElementById('sse-status-indicator').getAttribute('data-sync')})""")
    reads[0].fulfill(response=response)
    page.wait_for_function("__recoveryResult !== 'pending'")
    assert page.evaluate('__recoveryResult') == 'rejected'
    assert page.evaluate("""() => ({selected:!!AppState.selectedLemmas.haus,
        sync:document.getElementById('sse-status-indicator').getAttribute('data-sync')})""") == before
    assert len(reads) == (2 if lifecycle in ('reconnect', 'remount') else 1)
    if lifecycle in ('reconnect', 'remount'):
        reads[1].fulfill(response=reads[1].fetch())
        page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
        assert page.evaluate('!AppState.selectedLemmas.haus')


def test_stream_recovery_exhaustion_rejects_command_barrier(page, tmp_path, running_controller):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    commands = []
    page.route('**/session/export', lambda route: (commands.append(route.request), route.fulfill(json={'ok': True})))
    page.evaluate('void onSendToAnkiClick()')
    for count in range(1, 4):
        wait_for_held(page, reads, count)
        reads[count - 1].fulfill(status=503, json={'ok': False})
    page.wait_for_function("__recoveryResult !== 'pending'")
    assert page.evaluate('__recoveryResult') == 'rejected'
    assert page.locator('#sse-status-indicator').get_attribute('data-sync') == 'failed'
    error = page.evaluate('flushPendingSaves().then(() => null, e => e.message)')
    assert 'Selection reconciliation failed' in error
    assert not commands
    assert len(reads) == 3
    assert page.evaluate('!!AppState.selectedLemmas.haus')


def test_stream_recovery_and_membership_keep_save_disabled(page, tmp_path, running_controller):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    saves = []
    page.route('**/session/save', lambda route: saves.append(route))
    page.evaluate('toggleRowSelection(1,true); void onSaveClick(true)')
    page.evaluate('Boolean(window.getDeltas)')
    assert not saves, 'A new save must await the authoritative read'
    reads[0].fulfill(response=reads[0].fetch())
    wait_for_held(page, saves, 1)
    assert page.locator('#kw-btn-save').is_disabled()
    page.clock.run_for(26000)
    assert page.locator('#kw-btn-save').is_disabled()
    assert 'Saving' in page.locator('#kw-btn-save').text_content()
    page.clock.resume()
    page.evaluate('applySharedSelection({haus:true,baum:false})')
    assert page.locator('#kw-btn-save').is_disabled(), 'Membership projection cannot release an unacknowledged save'
    saves[0].fulfill(response=saves[0].fetch())
    wait_for_held(page, reads, 2)
    reads[1].fulfill(response=reads[1].fetch())
    page.wait_for_function('!isDirty()')
    assert page.locator('#kw-btn-save').is_disabled()


@pytest.mark.parametrize('reversal', [False, True])
def test_stream_recovery_after_pending_save_converges_without_cycle(page, tmp_path, running_controller, reversal):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    reads[0].fulfill(response=reads[0].fetch())
    page.wait_for_function("__recoveryResult === 'resolved'")
    saves, commands = [], []
    page.route('**/session/save', lambda route: saves.append(route))

    def receive_export(route):
        commands.append(route.request.post_data_json)
        route.fulfill(json={'ok': True})

    page.route('**/session/export', receive_export)
    page.evaluate('toggleRowSelection(1,true); void onSaveClick(true)')
    wait_for_held(page, saves, 1)
    page.evaluate("""() => {
        void reconcileStreamMembership({session:SSEBridge.getActiveZid(),connectionId:SSEBridge.getActiveConnectionId()});
        void onSendToAnkiClick();
    }""")
    if reversal:
        page.evaluate('toggleRowSelection(1,false)')
    assert len(reads) == 1
    assert not commands
    saves[0].fulfill(response=saves[0].fetch())
    wait_for_held(page, reads, 2)
    assert not commands
    reads[1].fulfill(response=reads[1].fetch())
    if reversal:
        wait_for_held(page, saves, 2)
        saves[1].fulfill(response=saves[1].fetch())
    wait_for_held(page, commands, 1)
    assert commands[0]['selected_row_ids'] == ([0] if reversal else [0, 1])
    assert page.evaluate('!isDirty()')
    assert restored_membership(running_controller[2]) == {'haus': True, 'baum': not reversal}


def test_stream_recovery_coalesces_pending_reads(page, tmp_path, running_controller):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    assert page.evaluate("""() => {
        const context={session:SSEBridge.getActiveZid(),connectionId:SSEBridge.getActiveConnectionId()};
        return reconcileStreamMembership(context) === reconcileStreamMembership(context);
    }""")
    assert len(reads) == 1
    reads[0].fulfill(response=reads[0].fetch())
    page.wait_for_function("__recoveryResult === 'resolved'")


def test_stream_recovery_revision_churn_fails_without_claiming_synced(page, tmp_path, running_controller):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    for count in range(1, 4):
        wait_for_held(page, reads, count)
        response = reads[count - 1].fetch()
        page.evaluate('applySharedSelection({haus:true,baum:false})')
        reads[count - 1].fulfill(response=response)
    page.wait_for_function("__recoveryResult !== 'pending'")
    assert page.evaluate('__recoveryResult') == 'rejected'
    assert page.locator('#sse-status-indicator').get_attribute('data-sync') == 'failed'
    assert len(reads) == 3


def test_stream_recovery_disconnect_after_sync_rejects_commands(page, tmp_path, running_controller):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    reads[0].fulfill(response=reads[0].fetch())
    page.wait_for_function("__recoveryResult === 'resolved'")
    page.evaluate('SSEBridge.disconnect()')
    error = page.evaluate('flushPendingSaves().then(() => null, e => e.message)')
    assert 'inactive connection' in error
    assert page.locator('#sse-status-indicator').get_attribute('data-sync') is None
