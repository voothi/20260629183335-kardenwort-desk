"""Execute valid recorded configuration pairs through real HTTP and shared JS."""
import itertools
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_controller import committed_recovery_snapshot
from kardenwort_events import EventDispatcher
from scripts.release_audit_baseline import pairwise_matrix
from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import ZID, TOKEN, save_selection, use_backend, wait_for_held
from tests.test_release_save_convergence import native_http_window
from tests.test_stream_gap_recovery import seed_session, set_committed_translation


def pairs(row):
    return {(a, row[a], b, row[b]) for a, b in itertools.combinations(row, 2)}


def valid(row):
    # The frozen lookup renderer has no sentence cards; do not invent that mode.
    return row['host'] != 'lookup' or row['cards'] == 'single'


def matrix():
    recorded = pairwise_matrix()
    axes = recorded['axes']
    candidates = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    candidates = [row for row in candidates if valid(row)]
    rows = [row for row in recorded['rows'] if valid(row)]
    required = set().union(*(pairs(row) for row in candidates))
    uncovered = required - set().union(*(pairs(row) for row in rows))
    while uncovered:
        row = max(candidates, key=lambda row: len(pairs(row) & uncovered))
        rows.append(row)
        uncovered -= pairs(row)
    return rows, required, [row for row in recorded['rows'] if not valid(row)]


CASES, REQUIRED, UNSUPPORTED = matrix()


def test_recorded_matrix_covers_every_valid_pair():
    assert set().union(*(pairs(row) for row in CASES)) == REQUIRED
    assert all(row['host'] == 'lookup' and row['cards'] == 'container' for row in UNSUPPORTED)


def render_matrix_html(server, adapter, case):
    config, paths = server.config, server.resolved_paths
    config.set('server', 'enabled', 'true')
    config.set('server', 'port', str(server.server_port))
    config.set('server', 'api_key', TOKEN)
    config.set('rendering', 'filter_selected_only', str(case['filter'] == 'on').lower())
    config.set('rendering', 'auto_save_on_edit', 'false')
    config.set('sentences_mode', 'enabled', str(case['cards'] == 'container').lower())
    config.set('sentences_mode', 'delivery_mode', 'container')
    path = server.arbiter.sessions[ZID]['tsv_path']
    if case['host'] != 'lookup':
        return desk.run_render_flow(text='Das Haus. Der Baum.', language='de', zid=ZID,
            text_mode='single', config=config, resolved_paths=paths, theme=case['theme'],
            tsv_path=path, spawn_children=False)
    comments, headers, rows = adapter.load_tsv_rows(path)
    gd = dict(theme=case['theme'], sections=['source', 'translation', 'lemmas'],
        lemma_columns=['lemma', 'translation'], filter_selected_only=case['filter'] == 'on',
        server_host='127.0.0.1', server_port=server.server_port, server_enabled=True)
    return desk.render_lookup_html('Das Haus. Der Baum.', 'de', 'en', config, paths, ZID,
        gd, comments, headers, rows, 'The house. The tree.', session_zid=ZID,
        api_token=TOKEN, server_enabled=True)


def active_matrix_card(page, case):
    if case['cards'] == 'container':
        page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
        page.evaluate('WorkspaceTabs.switchToTab(1)')
    page.evaluate('enabled => setFilterSelectedOnly(enabled)', case['filter'] == 'on')
    assert page.evaluate('isLookupMode()') == (case['host'] == 'lookup')
    assert not page.evaluate('isDirty()')
    if case['host'] == 'native':
        page.evaluate("startSelectionBridge('matrix-native',500)")


def publish_committed(server, adapter, value):
    set_committed_translation(adapter, server, value)
    snapshot = committed_recovery_snapshot(server.arbiter, ZID)
    EventDispatcher.get_instance().publish(ZID, snapshot)


@pytest.mark.parametrize('case', CASES, ids=lambda row: '-'.join(row.values()))
def test_configuration_pair_converges_without_losing_filter(browser, page, tmp_path, running_controller, case):
    url, server, _ = running_controller
    adapter = use_backend(server, 'tsv')
    seed_session(server, adapter)
    memo = server.arbiter.sessions[ZID]
    comments, headers, rows = adapter.load_tsv_rows(memo['tsv_path'])
    headers.append('SentenceDestination')
    for row, translation in zip(rows, ['The house.', 'The tree.']):
        row.append(translation)
    adapter.save_tsv_rows_safely(Path(memo['tsv_path']), comments, headers, rows)
    memo.update(headers=headers, data_rows=rows)
    save_selection(url, 0, True)
    requests, errors = [], []
    native = case['host'] == 'native'
    context = None
    if native:
        context, page = native_http_window(browser, render_matrix_html(server, adapter, case), url, 'matrix-native')
        context.set_extra_http_headers({'X-API-Token': TOKEN})
        page.add_init_script('''window.EventSource=undefined;
            window.ahkCall=function(action,body){
                if(action!=='get_shared_selection' && action!=='persist_selection') return '';
                const request=new XMLHttpRequest();
                request.open('POST',action==='get_shared_selection'?'/internal/selection':'/session/save',false);
                request.setRequestHeader('Content-Type','application/json');
                request.setRequestHeader('X-API-Token','test-controller-api-key');
                request.send(body);return request.responseText;
            };''')
    else:
        page.route(url + '/', lambda route: route.fulfill(content_type='text/html', body=render_matrix_html(server, adapter, case)))
        page.set_extra_http_headers({'X-API-Token': TOKEN})
        page.goto(url + '/')
        page.evaluate('args=>SSEBridge.connect(args.zid,args.url,args.token)', dict(zid=ZID,url=url,token=TOKEN))
        page.wait_for_function("document.getElementById('sse-status-indicator') && document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    page.on('request', lambda request: requests.append(request.url))
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        active_matrix_card(page, case)
        cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')
        expected = 'matrix-' + case['transport']
        if case['transport'] == 'overlap':
            held = []
            page.route('**/session/status?*', lambda route: held.append(route))
            page.evaluate('onUpdateClick()')
            wait_for_held(page, held, 1)
            old = held[0].fetch()
            publish_committed(server, adapter, expected)
            if native:
                page.clock.run_for(500)
            expect(cell).to_have_text(expected)
            held[0].fulfill(response=old)
            expect(page.locator('#kw-btn-update')).to_be_enabled()
            page.unroute('**/session/status?*')
        elif case['transport'] == 'sse':
            publish_committed(server, adapter, expected)
            if native:
                page.clock.run_for(500)
        else:
            set_committed_translation(adapter, server, expected)
            committed_recovery_snapshot(server.arbiter, ZID)
            if case['transport'] == 'reload':
                if native:
                    page.route(url + '/native-http', lambda route: route.fulfill(content_type='text/html', body=render_matrix_html(server, adapter, case)))
                page.reload()
                active_matrix_card(page, case)
            else:
                page.evaluate('onUpdateClick()')
        expect(cell).to_have_text(expected)
        assert page.evaluate('AppState.filterSelectedOnly') == (case['filter'] == 'on')
        assert not page.evaluate('isDirty()')
        assert errors == []
        assert not any(path in request for request in requests for path in ('/session/reword', '/session/retext', '/session/create'))
    finally:
        if context:
            context.close()
