"""Owned browser/provider/SQLite actions for the frozen reliability matrix.

Engine errors are deliberate external-port outcomes, never audible certification.
Subtoken gestures and the complete control register have separate contracts.
"""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.controller_test_support import controller_world
from tests.enrichment_test_support import enrichment_engine
from tests.reword_contract_support import HEADERS, ROWS, TOKEN, ZID
from tests.test_release_boundary_contracts import post, wait_for_held


MATRIX = json.loads((Path(__file__).parent/'fixtures/reliability-action-matrix.json').read_text())


def test_frozen_action_matrix_contains_every_axis_pair():
    axes = MATRIX['axes']
    for first, second in itertools.combinations(axes, 2):
        assert {(row[first], row[second]) for row in MATRIX['rows']} == set(
            itertools.product(axes[first], axes[second]))


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


def render_document(world, case):
    return desk.run_render_flow(text='Haus. Baum.', language='de', zid=ZID,
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
