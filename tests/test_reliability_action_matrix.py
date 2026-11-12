"""Owned browser/provider/SQLite actions for the frozen reliability matrix.

Engine errors are deliberate external-port outcomes, never audible certification.
Subtoken gestures and the complete control register have separate contracts.
"""
import itertools
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.controller_test_support import controller_world
from tests.enrichment_test_support import enrichment_engine
from tests.reword_contract_support import HEADERS, ROWS, TOKEN, ZID
from tests.test_release_boundary_contracts import post, wait_for_held
from tests.test_preference_js_structure import function_body
from kardenwort_events import EventDispatcher


MATRIX = json.loads((Path(__file__).parent/'fixtures/reliability-action-matrix.json').read_text())


def test_frozen_action_matrix_contains_every_axis_pair():
    axes = MATRIX['axes']
    for first, second in itertools.combinations(axes, 2):
        assert {(row[first], row[second]) for row in MATRIX['rows']} == set(
            itertools.product(axes[first], axes[second]))


def test_occurrence_projection_branch_upper_bound():
    source = (Path(__file__).resolve().parents[1]/'web/js/app.js').read_text(encoding='utf-8')
    measured = {name: 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', function_body(source, name)))
        for name in ['occurrenceRowIds', 'normalizeOccurrenceRowDeltas', 'projectCellValue',
                     'projectEditedValue', 'projectPendingEdits', 'deletedRowIds',
                     'committedSelectionRows', 'resetDeletionSelection', 'selectedTokenOrder',
                     'getSelectedRowsArray', 'getSelectedTokenOrdersArray', 'applyAction',
                     'scalarDeletionTarget', 'deletionRowTargets', 'collectDeletionTargets',
                     'exportCardCount', 'exportFeedback', 'rowRetryPayload']}
    assert max(measured.values()) <= 7, measured


def configure(server, case):
    config = server.config
    server.api_key = TOKEN
    config.read_dict({
        'server': {'api_key': TOKEN, 'enabled': 'true'},
        'pipeline': {'lemma_reprocess_provider': case['provider'],
                     'lemma_provider_chain': case['provider']},
        'rendering': {'theme': case['theme'], 'highlight_same_lemma': 'false',
                      'filter_selected_only': 'false', 'auto_save_on_edit': 'false'},
        'audio': {'lmb_play': str(case['audio'] == 'configured-engine').lower(),
                  'rmb_play': 'false'},
        'sentences_mode': {'enabled': 'true', 'delivery_mode': 'container'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'},
        'wordfill': {'enabled': 'false'},
    })
    adapter = desk.get_storage_adapter(config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    path = server.resolved_paths['results_dir']/(ZID+'.de.tsv')
    adapter.save_session(session_zid=ZID, source_language='de', target_language='en',
        text_mode='single', source_raw_text='Haus. Baum.', headers=HEADERS+['SentenceDestination'],
        data_rows=[row+[destination] for row, destination in zip(ROWS, ['House.', 'Tree.'])],
        sentences=[dict(sentence_index=index, sentence_source=source, sentence_destination=destination)
            for index, source, destination in [(1, 'Haus.', 'House.'), (2, 'Baum.', 'Tree.')]],
        working_tsv_path=path)
    return adapter, SimpleNamespace(config=config, paths=server.resolved_paths,
        port=server.server_port, path=lambda zid: path)


def open_card(page, seq=2):
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate('() => { window.__matrixStateReady=false; window.__stateReady.then(() => window.__matrixStateReady=true); }')
    page.wait_for_function('window.__matrixStateReady')
    page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
    page.evaluate('seq => WorkspaceTabs.switchToTab(seq)', seq)


def flush(page):
    page.evaluate('''() => {
        window.__matrixFlush = null;
        flushPendingSaves().then(() => window.__matrixFlush = 'saved',
            error => window.__matrixFlush = error.message);
    }''')
    page.wait_for_function('window.__matrixFlush !== null', timeout=6000)
    assert page.evaluate('window.__matrixFlush') == 'saved'


def render_document(world, case, text='Haus. Baum.'):
    return desk.run_render_flow(text=text, language='de', zid=ZID,
        text_mode='single', config=world.config, resolved_paths=world.paths,
        theme=case['theme'], tsv_path=ZID, spawn_children=False)


@pytest.mark.parametrize('case', MATRIX['rows'], ids=lambda row: '-'.join(row.values()))
def test_provider_action_preserves_selection_preferences_and_cold_reload(page, controller_world, case):
    url, server = controller_world
    adapter, world = configure(server, case)
    audio = []

    def unavailable_engine(route):
        audio.append(route.request.post_data_json)
        route.fulfill(status=503, content_type='application/json',
            body='{"message":"Owned configured engine unavailable"}')

    page.route('**/api/v1/audio/synthesize', unavailable_engine)
    with enrichment_engine(server.config) as engine:
        engine.release.set()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        document = [render_document(world, case)]
        page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=document[0]))
        page.set_extra_http_headers({'X-API-Token': TOKEN})
        page.set_default_timeout(6000)
        page.goto(url+'/', wait_until='domcontentloaded')
        try:
            open_card(page)
            page.evaluate('args => SSEBridge.connect(args.zid,args.url,args.token)',
                dict(zid=ZID, url=url, token=TOKEN))
            page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
            assert page.locator('body').evaluate('(body, theme) => body.classList.contains("theme-"+theme)', case['theme'])
            if case['highlight'] == 'lemma-on':
                page.locator('#kw-btn-same-lemma').click()
                page.wait_for_function('AppState.highlightSameLemma === true')
            token = page.locator('#source-container .word[data-sentence-idx="1"]').first
            geometry = token.bounding_box()
            token.click()
            flush(page)
            assert token.bounding_box() == geometry
            assert adapter.get_shared_selection(ZID)['haus'] is True
            if case['audio'] == 'configured-engine':
                expect(page.locator('#kw-toast-container')).to_contain_text('Audio failed')
                assert audio == [{'text': 'Haus', 'language': 'de'}]
            else:
                assert audio == []
            if case['filter'] == 'on':
                page.locator('#kw-btn-filter-selected').click()
                page.wait_for_function('AppState.filterSelectedOnly === true')
            flush(page)
            held = []
            if case['transport'] == 'overlap':
                page.route('**/session/status?*', lambda route: held.append(route))
                page.evaluate('onUpdateClick()')
                wait_for_held(page, held, 1)
                stale = held[0].fetch()
            before_other = adapter.db.get_words_by_session(ZID)[1]
            with page.expect_response('**/session/reword') as response:
                page.locator('#kw-btn-reword').click()
            assert response.value.ok, response.value.text()
            expected = 'trans_Haus' if case['provider'] == 'intellifiller' else case['provider']+': Haus'
            cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
            words = cold.db.get_words_by_session(ZID)
            assert words[0]['word_destination'] == expected
            assert words[0]['word_provenance'] == 'live:'+case['provider']
            assert words[1] == before_other
            cell = page.locator('#lemma-table tr[data-sentence-idx="1"] td[data-col="WordDestination"] .scrollable-cell')
            expect(cell).to_have_text(expected)
            if case['transport'] == 'overlap':
                held.pop().fulfill(response=stale)
                page.unroute('**/session/status?*')
                expect(page.locator('#kw-btn-update')).to_be_enabled()
            elif case['transport'] == 'status':
                with page.expect_response('**/session/status?*') as status:
                    page.locator('#kw-btn-update').click()
                assert status.value.ok
            expect(cell).to_have_text(expected)
            document[0] = render_document(world, case)
            page.reload()
            open_card(page)
            expect(cell).to_have_text(expected)
            assert page.evaluate('AppState.filterSelectedOnly') is (case['filter'] == 'on')
            assert page.evaluate('AppState.highlightSameLemma') is (case['highlight'] == 'lemma-on')
            assert cold.get_shared_selection(ZID)['haus'] is True
            before = cold.db.get_session_bundle(ZID)
            status, _ = post(url, '/session/reword', dict(session_zid=ZID, language='de',
                target_coordinates=[dict(sentence_idx=999, token_order=0)]), token=TOKEN)
            assert status == 400
            assert cold.db.get_session_bundle(ZID) == before
            assert errors == []
        finally:
            page.close()


SUBTOKEN_CASES = [case for case in MATRIX['rows'] if case['highlight'] == 'subtoken']


@pytest.mark.parametrize('case', SUBTOKEN_CASES, ids=lambda row: '-'.join(row.values()))
def test_compound_subtoken_action_keeps_composite_and_sibling_unchanged(page, controller_world, case):
    url, server = controller_world
    adapter, world = configure(server, case)
    source = 'Haus_Baum Haus Baum.'
    text = source+' Baum.'
    rows = [
        ['0', 'Haus_Baum', 'Haus_Baum', 'compound', '1', source, '', 'noun', 'House tree.'],
        ['1', 'Haus', 'Haus', 'house', '1', source, '', 'noun', 'House tree.'],
        ['2', 'Baum', 'Baum', 'tree', '1', source, '', 'noun', 'House tree.'],
        ['0', 'Baum', 'Baum', 'other tree', '2', 'Baum.', '', 'noun', 'Other tree.'],
    ]
    adapter.save_session(session_zid=ZID, source_language='de', target_language='en',
        source_raw_text=text, text_mode='single', headers=HEADERS+['SentenceDestination'],
        data_rows=rows, sentences=[dict(sentence_index=1, sentence_source=source,
            sentence_destination='House tree.'), dict(sentence_index=2,
            sentence_source='Baum.', sentence_destination='Other tree.')])
    audio, held = [], []

    def unavailable_engine(route):
        audio.append(route.request.post_data_json)
        route.fulfill(status=503, content_type='application/json',
            body='{"message":"Owned configured engine unavailable"}')

    page.route('**/api/v1/audio/synthesize', unavailable_engine)
    with enrichment_engine(server.config) as engine:
        engine.release.set()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        document = [render_document(world, case, text)]
        page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=document[0]))
        page.set_extra_http_headers({'X-API-Token': TOKEN})
        page.set_default_timeout(6000)
        page.goto(url+'/', wait_until='domcontentloaded')
        try:
            open_card(page)
            page.evaluate('args => SSEBridge.connect(args.zid,args.url,args.token)',
                dict(zid=ZID, url=url, token=TOKEN))
            page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
            token = page.locator('#source-container .word[data-lower-clean="haus"][data-sentence-idx="1"]').first
            sibling = page.locator('#source-container .word[data-lower-clean="baum"][data-sentence-idx="1"]').first
            assert token.count() == 1, page.locator('#source-container').inner_html()
            geometry = [token.bounding_box(), sibling.bounding_box()]
            token.click()
            flush(page)
            assert [token.bounding_box(), sibling.bounding_box()] == geometry
            assert adapter.get_shared_selection(ZID) == {'haus_baum': False, 'haus': True, 'baum': False}
            assert 'highlight-orange-active' in (token.get_attribute('class') or '')
            assert 'highlight-orange-active' not in (sibling.get_attribute('class') or '')
            if case['audio'] == 'configured-engine':
                expect(page.locator('#kw-toast-container')).to_contain_text('Audio failed')
                assert audio == [{'text': 'Haus', 'language': 'de'}]
            else:
                assert audio == []
            if case['filter'] == 'on':
                page.locator('#kw-btn-filter-selected').click()
                page.wait_for_function('AppState.filterSelectedOnly === true')
            flush(page)
            before = adapter.db.get_words_by_session(ZID)
            if case['transport'] == 'overlap':
                page.route('**/session/status?*', lambda route: held.append(route))
                page.evaluate('onUpdateClick()')
                wait_for_held(page, held, 1)
                stale = held[0].fetch()
            with page.expect_response('**/session/reword') as response:
                page.locator('#kw-btn-reword').click()
            assert response.value.ok, (response.value.text(), response.value.request.post_data_json,
                page.evaluate('AppState.activeTokenSelections'))
            targets = response.value.request.post_data_json['target_coordinates']
            assert [(int(target['sentence_idx']), int(target['token_order'])) for target in targets] == [(1, 1)]
            expected = 'trans_Haus' if case['provider'] == 'intellifiller' else case['provider']+': Haus'
            cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
            after = cold.db.get_words_by_session(ZID)
            assert after[1]['word_destination'] == expected
            assert after[1]['word_provenance'] == 'live:'+case['provider']
            assert after[0] == before[0]
            assert after[2] == before[2]
            assert after[3] == before[3]
            cell = page.locator('#lemma-table tr[data-token-order="1"] td[data-col="WordDestination"] .scrollable-cell')
            expect(cell).to_have_text(expected)
            if case['transport'] == 'overlap':
                held.pop().fulfill(response=stale)
                page.unroute('**/session/status?*')
                expect(page.locator('#kw-btn-update')).to_be_enabled()
            elif case['transport'] == 'status':
                with page.expect_response('**/session/status?*') as status:
                    page.locator('#kw-btn-update').click()
                assert status.value.ok
            expect(cell).to_have_text(expected)
            assert not page.evaluate('isDirty()'), page.evaluate('({deltas:getDeltas(), selected:AppState.selectedLemmas, cards:WorkspaceTabs.getCards().map(c=>({seq:c.seq_num,selected:c.selected_ids,words:c.words.map(w=>({id:w.row_id,sentence:w.sentence_idx,token:w.token_order,lemma:w.lemma,selected:w.selected}))}))})')
            document[0] = render_document(world, case, text)
            page.reload()
            open_card(page)
            page.evaluate('args => SSEBridge.connect(args.zid,args.url,args.token)',
                dict(zid=ZID, url=url, token=TOKEN))
            page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
            expect(cell).to_have_text(expected)
            assert cold.get_shared_selection(ZID) == {'haus_baum': False, 'haus': True, 'baum': False}
            assert page.evaluate('AppState.filterSelectedOnly') is (case['filter'] == 'on')
            flush(page)
            with page.expect_response('**/session/reword') as restored:
                page.locator('#kw-btn-reword').click()
            assert restored.value.ok, restored.value.text()
            assert len(restored.value.request.post_data_json['row_ids']) == 1
            targets = restored.value.request.post_data_json['target_coordinates']
            assert [(int(target['sentence_idx']), int(target['token_order'])) for target in targets] == [(1, 1)]
            assert cold.db.get_words_by_session(ZID) == after
            assert errors == []
        finally:
            for route in held:
                route.abort()
            page.close()


@pytest.mark.parametrize('provider', ['google', 'intellifiller'])
def test_provider_completion_and_old_status_preserve_unrelated_pending_edit(page, controller_world, provider):
    case = next(row for row in MATRIX['rows'] if row['provider'] == provider and row['audio'] == 'disabled')
    url, server = controller_world
    adapter, world = configure(server, case)
    held, errors = [], []
    with enrichment_engine(server.config) as engine:
        engine.release.set()
        document = [render_document(world, case)]
        page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=document[0]))
        page.set_extra_http_headers({'X-API-Token': TOKEN})
        page.set_default_timeout(6000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url+'/')
        try:
            open_card(page)
            page.evaluate('args => SSEBridge.connect(args.zid,args.url,args.token)',
                dict(zid=ZID, url=url, token=TOKEN))
            page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
            page.locator('#source-container .word[data-sentence-idx="1"]').first.click()
            flush(page)
            page.route('**/session/status?*', lambda route: held.append(route))
            page.locator('#kw-btn-update').click()
            wait_for_held(page, held, 1)
            stale = held[0].fetch()
            page.evaluate('WorkspaceTabs.switchToTab(3)')
            cell = page.locator('#lemma-table tr[data-sentence-idx="2"] td[data-col="WordDestination"]')
            cell.dblclick()
            cell.locator('input.cell-editor').fill('pending tree edit')
            cell.locator('input.cell-editor').press('Enter')
            pending = page.evaluate('getDeltas()')
            assert page.evaluate('isDirty()')
            before_other = adapter.db.get_words_by_session(ZID)[1]
            status, result = post(url, '/session/reword', dict(session_zid=ZID, language='de', row_ids=[0],
                target_coordinates=[dict(sentence_idx=1, token_order=0)], sentence_idx=1), token=TOKEN)
            assert status == 200, result
            expected = 'trans_Haus' if provider == 'intellifiller' else 'google: Haus'
            page.evaluate('WorkspaceTabs.switchToTab(2)')
            house = page.locator('#lemma-table tr[data-sentence-idx="1"] td[data-col="WordDestination"] .scrollable-cell')
            expect(house).to_have_text(expected)
            held.pop().fulfill(response=stale)
            page.unroute('**/session/status?*')
            expect(page.locator('#kw-btn-update')).to_be_enabled()
            expect(house).to_have_text(expected)
            assert page.evaluate('getDeltas()') == pending
            page.evaluate('WorkspaceTabs.switchToTab(3)')
            expect(cell.locator('.scrollable-cell')).to_have_text('pending tree edit')
            cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
            committed = cold.db.get_words_by_session(ZID)
            assert (committed[0]['word_destination'], committed[0]['word_provenance']) == (expected, 'live:'+provider)
            assert committed[1] == before_other
            with page.expect_response('**/session/save') as saved:
                page.locator('#kw-btn-save').click()
            assert saved.value.ok, saved.value.text()
            flush(page)
            assert [word['word_destination'] for word in cold.db.get_words_by_session(ZID)] == [expected, 'pending tree edit']
            document[0] = render_document(world, case)
            page.reload()
            open_card(page, 3)
            expect(cell.locator('.scrollable-cell')).to_have_text('pending tree edit')
            assert not page.evaluate('isDirty()')
            assert errors == []
        finally:
            for route in held:
                route.abort()
            page.close()


def test_two_clients_keep_distinct_focus_through_shared_selection_and_stream_gap(page, controller_world):
    case = next(row for row in MATRIX['rows'] if row['audio'] == 'disabled')
    url, server = controller_world
    adapter, world = configure(server, case)
    page.context.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=render_document(world, case)))
    page.context.set_extra_http_headers({'X-API-Token': TOKEN})
    other = page.context.new_page()
    clients = [page, other]
    try:
        for client, seq in zip(clients, [2, 3]):
            client.set_default_timeout(6000)
            client.goto(url+'/')
            open_card(client, seq)
            client.evaluate('args => SSEBridge.connect(args.zid,args.url,args.token)',
                dict(zid=ZID, url=url, token=TOKEN))
            client.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
            client.locator('#source-container .word:visible').first.click()
            flush(client)
        focus = [client.evaluate('AppState.activeTokenSelections') for client in clients]
        assert focus[0] and focus[1] and focus[0] != focus[1]
        assert adapter.get_shared_selection(ZID) == {'haus': True, 'baum': True}
        connections = [client.evaluate('SSEBridge.getActiveConnectionId()') for client in clients]
        EventDispatcher.get_instance().publish(ZID, {'type': 'resync_required'})
        for client, old, expected in zip(clients, connections, focus):
            client.wait_for_function('old => SSEBridge.getActiveConnectionId() !== old', arg=old)
            client.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
            assert client.evaluate('AppState.activeTokenSelections') == expected
            assert client.evaluate('Object.values(AppState.selectedLemmas).filter(Boolean).length') == 2
            flush(client)
        for client, seq, expected in zip(clients, [2, 3], focus):
            client.reload()
            open_card(client, seq)
            assert client.evaluate('WorkspaceTabs.getActiveTabSeq()') == seq
            assert client.evaluate('AppState.activeTokenSelections') == expected
        cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
        assert cold.get_shared_selection(ZID) == {'haus': True, 'baum': True}
    finally:
        other.close()
        page.close()


@pytest.mark.parametrize('key,button,field', [
    ('highlight_same_lemma', 'kw-btn-same-lemma', 'highlightSameLemma'),
    ('filter_selected_only', 'kw-btn-filter-selected', 'filterSelectedOnly'),
])
@pytest.mark.parametrize('first_status', [200, 503])
def test_rapid_preference_intents_survive_old_ack_and_cold_reload(page, controller_world, key, button, field, first_status):
    case = next(row for row in MATRIX['rows'] if row['audio'] == 'disabled')
    url, server = controller_world
    adapter, world = configure(server, case)
    page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=render_document(world, case)))
    page.set_extra_http_headers({'X-API-Token': TOKEN})
    page.goto(url+'/')
    open_card(page)
    held = []
    page.route('**/api/v1/config', lambda route: held.append(route)
               if route.request.method == 'POST' else route.continue_())
    try:
        control = page.locator('#'+button)
        before = control.bounding_box()
        control.click()
        wait_for_held(page, held, 1)
        control.click()
        assert page.evaluate('field => AppState[field]', field) is False
        assert len(held) == 1
        first = held.pop(0)
        committed = first.fetch()
        assert committed.ok
        first.fulfill(response=committed, status=first_status)
        wait_for_held(page, held, 1)
        expect(control).to_have_attribute('data-preference-state', 'pending')
        second = held.pop(0)
        second.fulfill(response=second.fetch())
        expect(control).to_have_attribute('data-preference-state', 'saved')
        assert control.bounding_box() == before
        flush(page)
        with adapter.db.get_connection(read_only=True) as connection:
            assert json.loads(connection.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()[0]) is False
        page.reload()
        open_card(page)
        assert page.evaluate('field => AppState[field]', field) is False
        assert control.get_attribute('data-preference-state') not in ('pending', 'failed')
        assert not any(adapter.get_shared_selection(ZID).values())
    finally:
        for route in held:
            route.abort()
        page.close()


@pytest.mark.parametrize('theme', ['dark', 'light', 'white', 'compact'])
@pytest.mark.parametrize('zoom', [1, 1.25])
def test_selected_drag_and_deselect_keep_source_geometry_with_empty_filter(page, controller_world, theme, zoom):
    case = dict(next(row for row in MATRIX['rows'] if row['audio'] == 'disabled'), theme=theme)
    url, server = controller_world
    adapter, world = configure(server, case)
    page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=render_document(world, case)))
    page.set_extra_http_headers({'X-API-Token': TOKEN})
    page.goto(url+'/')
    open_card(page, 1)
    page.evaluate('zoom => document.body.style.zoom = zoom', zoom)
    page.locator('#kw-btn-filter-selected').click()
    flush(page)
    tokens = page.locator('#source-container .word:visible')
    expect(tokens).to_have_count(2)
    boxes = tokens.evaluate_all('tokens => tokens.map(token => {const r=token.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})')
    first, last = tokens.first.bounding_box(), tokens.last.bounding_box()
    page.mouse.move(first['x']+first['width']/2, first['y']+first['height']/2)
    page.mouse.down()
    page.mouse.move(last['x']+last['width']/2, last['y']+last['height']/2, steps=4)
    page.mouse.up()
    flush(page)
    assert adapter.get_shared_selection(ZID) == {'haus': True, 'baum': True}
    assert tokens.evaluate_all('tokens => tokens.map(token => {const r=token.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})') == boxes
    expect(page.locator('#lemma-table tbody tr[data-row-id]:visible')).to_have_count(2)
    tokens.first.click()
    tokens.last.click()
    flush(page)
    assert adapter.get_shared_selection(ZID) == {'haus': False, 'baum': False}
    expect(page.locator('#lemma-table tbody tr[data-row-id]:visible')).to_have_count(0)
    assert tokens.evaluate_all('tokens => tokens.map(token => {const r=token.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})') == boxes
    page.reload()
    open_card(page, 1)
    assert page.evaluate('AppState.filterSelectedOnly') is True
    expect(page.locator('#lemma-table tbody tr[data-row-id]:visible')).to_have_count(0)
    assert not any(adapter.get_shared_selection(ZID).values())
