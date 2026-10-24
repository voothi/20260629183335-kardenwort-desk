"""Accepted successor contracts; exercise real storage and browser commands."""
import copy
import sqlite3
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.test_sentence_idx_contract import _make_persist_fixture
from tests.test_release_audit_regressions import audit_page  # noqa: F401
from tests.test_controller_integration import running_controller  # noqa: F401


def database_snapshot(adapter):
    with adapter.db.get_connection(read_only=True) as conn:
        return list(conn.iterdump())


def test_shared_lemma_membership_crosses_pos_only_within_session(tmp_path):
    _, adapter, zid, _ = _make_persist_fixture(tmp_path)
    with adapter.db.get_connection() as conn:
        conn.execute("UPDATE words SET lemma = ' Mixed ', pos = 'NOUN' WHERE session_zid = ? AND token_order = 0", (zid,))
        conn.execute("UPDATE words SET lemma = 'mixed', pos = 'VERB' WHERE session_zid = ? AND token_order = 1", (zid,))
    adapter.save_session(session_zid='20261025003900', slug='other', source_language='de',
                         target_language='ru', text_mode='single', source_raw_text='Mixed.',
                         headers=['TokenOrder', 'WordSource', 'SentenceSourceIndex', 'SentenceSource'],
                         data_rows=[['0', 'mixed', '1', 'Mixed.']])
    for selected in (True, False):
        assert adapter.update_word_selection(zid, 1, 0, selected)
        with adapter.db.get_connection(read_only=True) as conn:
            words = conn.execute('SELECT pos, selected FROM words WHERE session_zid = ? ORDER BY token_order', (zid,)).fetchall()
            assert [(row['pos'], row['selected']) for row in words] == [('NOUN', int(selected)), ('VERB', int(selected))]
            assert conn.execute('SELECT selected FROM words WHERE session_zid = ?', ('20261025003900',)).fetchone()['selected'] == 0
        assert adapter.get_overview_selections(zid) == ({0, 1} if selected else set())


@pytest.mark.parametrize('result', [
    {'FULL_TEXT': 'Entire paragraph'}, 'Entire paragraph',
    {0: 'Only first'}, {0: 'First', 2: 'Third'},
])
def test_full_retext_rejects_unaligned_output_without_side_effects(tmp_path, monkeypatch, result):
    arbiter, adapter, zid, tsv = _make_persist_fixture(tmp_path)
    before = database_snapshot(adapter)
    cache = copy.deepcopy(arbiter.sessions[zid])
    artifacts = {p.name: p.read_bytes() for p in tsv.parent.iterdir() if p.is_file() and p.suffix != '.log'}
    monkeypatch.setattr(controller, 'translate_source_text', lambda *a, **kw: result)
    with pytest.raises(desk.StructuredError):
        arbiter.retext_session(zid, language='de')
    assert database_snapshot(adapter) == before
    assert arbiter.sessions[zid] == cache
    assert {p.name: p.read_bytes() for p in tsv.parent.iterdir() if p.is_file() and p.suffix != '.log'} == artifacts


def test_second_sentence_sql_failure_rolls_back_complete_command(tmp_path):
    arbiter, adapter, zid, tsv = _make_persist_fixture(tmp_path)
    with adapter.db.get_connection() as conn:
        conn.execute("""CREATE TRIGGER reject_second BEFORE UPDATE ON sentences
            WHEN NEW.sentence_index = 2 AND NEW.sentence_destination = 'New two'
            BEGIN SELECT RAISE(ABORT, 'second sentence rejected'); END""")
    before = database_snapshot(adapter)
    cache = copy.deepcopy(arbiter.sessions[zid])
    artifact = tsv.read_bytes()
    with pytest.raises((sqlite3.DatabaseError, desk.StructuredError)):
        arbiter._persist_retext_updates(zid, {1: 'New one', 2: 'New two'},
                                        tsv_path=tsv, active_text_prov='live:test')
    assert database_snapshot(adapter) == before
    assert arbiter.sessions[zid] == cache
    assert tsv.read_bytes() == artifact


def test_explicit_diskless_creates_no_directory(tmp_path):
    config, _, _, _ = desk.load_config()
    path = tmp_path / '20261024225159.de.tsv'
    desk.write_update_js(path, [], [], {}, stage='finished',
                         zid='20261024225159', config=config, diskless=True)
    assert list(tmp_path.iterdir()) == []


def test_undo_during_save_retains_reversal_and_history(audit_page):
    page = audit_page
    page.evaluate("__holdSaves = true")
    cell = page.locator('td[data-col="WordDestination"]').first
    cell.dblclick()
    editor = cell.locator('input')
    editor.fill('Saved new value')
    editor.press('Enter')
    page.evaluate('void (window.__save = onSaveClick(true))')
    page.wait_for_function('__pendingSaves.length === 1')
    page.evaluate('undo()')
    page.evaluate('__pendingSaves.shift()()')
    page.evaluate('window.__save')
    assert page.evaluate('isDirty()')
    pending = page.evaluate('JSON.parse(getDeltas())')
    assert any(d['column'] == 'WordDestination' and d['value'] == 'house' for d in pending)
    page.evaluate('redo()')
    assert not page.evaluate('isDirty()')


@pytest.mark.parametrize('result', [
    {0: 'A', '0': 'B'}, {-1: 'A'}, {True: 'A'}, {'bad': 'A'},
    {0: ''}, {0: None}, {0: ['A']}, {0: 'A', 'PADDED': {1: 'B'}},
])
def test_provider_identity_validation(result):
    with pytest.raises(desk.StructuredError):
        controller.to_one_based_sentence_map(result)


def test_valid_retext_persists_clean_and_padded_with_provenance(tmp_path, monkeypatch):
    arbiter, adapter, zid, tsv = _make_persist_fixture(tmp_path)
    result = {0: 'New one', 1: 'New two', 'FULL_TEXT': 'New one New two',
              'PADDED': {0: 'Padded one', 1: 'Padded two'}}
    monkeypatch.setattr(controller, 'translate_source_text', lambda *a, **kw: result)
    queue = arbiter.register_subscriber(zid)
    try:
        response = arbiter.retext_session(zid, language='de')
        assert response['status'] == 'success'
        sentences = adapter.db.get_sentences_by_session(zid)
        assert [s['sentence_destination'] for s in sentences] == ['New one', 'New two']
        assert [s['sentence_destination2'] for s in sentences] == ['Padded one', 'Padded two']
        assert all(s['text_provenance'].startswith('live:') for s in sentences)
        assert queue.get(timeout=1)['stage'] == 'finished'
    finally:
        arbiter.unregister_subscriber(zid, queue)


PRODUCER = '''
import configparser, sys
from pathlib import Path
from kardenwort_desk import write_update_js
from kardenwort_delivery import controller_producer
config = configparser.ConfigParser()
config.read(sys.argv[1])
with controller_producer():
    write_update_js(Path(sys.argv[2]), [], [], {}, stage=sys.argv[3],
                    zid='20261024225159', config=config, empty_payload=True)
'''


@pytest.mark.parametrize('mode', ['controller', 'desktop_file', 'mixed'])
@pytest.mark.parametrize('stage', ['source', 'translated_text', 'finished'])
def test_separate_producer_delivers_through_controller(running_controller, tmp_path, mode, stage):
    _, server, _ = running_controller
    config = tmp_path / 'producer.ini'
    config.write_text(f'[storage]\nbackend=sqlite\n[server]\nupdate_delivery={mode}\n'
                      f'port={server.server_port}\napi_key=test-controller-api-key\n')
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    queue = server.arbiter.register_subscriber('20261024225159')
    try:
        result = subprocess.run([sys.executable, '-c', PRODUCER, str(config),
                                 str(artifacts / '20261024225159.de.tsv'), stage],
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        if mode != 'desktop_file':
            assert queue.get(timeout=2)['stage'] == stage
        if mode == 'controller':
            assert list(artifacts.iterdir()) == []
        else:
            assert len(list(artifacts.rglob('*.js'))) == 1
    finally:
        server.arbiter.unregister_subscriber('20261024225159', queue)


def test_sse_parse_and_application_failures_are_distinct(audit_page):
    page = audit_page
    page.evaluate("SSEBridge.connect('20261024225159')")
    page.evaluate("__eventSource.onmessage({data:'invalid json'})")
    assert page.locator('#sse-status-indicator').get_attribute('data-status') == 'error'
    page.evaluate("() => { receiveUpdate = function(){throw new Error('renderer contract failure')}; }")
    with pytest.raises(Exception, match='renderer contract failure'):
        page.evaluate("__eventSource.onmessage({data:JSON.stringify({stage:'finished'})})")


def test_component_remount_retains_one_subscription_and_unchanged_nodes(audit_page):
    page = audit_page
    result = page.evaluate('''() => {
        let calls = 0;
        const original = SourceViewer.project;
        SourceViewer.project = function(state, action) { calls++; return original.call(this,state,action); };
        SourceViewer.dispose();
        SourceViewer.mount('#source-container');
        SourceViewer.mount('#source-container');
        const node = document.querySelector('#source-container span');
        AppStore.applyDeltas({stage:'translating'});
        return {calls, same:node === document.querySelector('#source-container span')};
    }''')
    assert result == {'calls': 1, 'same': True}


def test_edit_revert_and_new_branch_during_ack_preserve_current_values(audit_page):
    page = audit_page
    cell = page.locator('td[data-col="WordDestination"]').first
    def edit(value):
        cell.dblclick()
        cell.locator('input').fill(value)
        cell.locator('input').press('Enter')
    edit('new value')
    edit('house')
    assert not page.evaluate('isDirty()')
    edit('submitted')
    page.evaluate('__holdSaves = true; void onSaveClick(true)')
    page.wait_for_function('__pendingSaves.length === 1')
    page.evaluate('undo()')
    other = page.locator('td[data-col="WordDestination"]').nth(1)
    other.dblclick()
    other.locator('input').fill('other pending value')
    other.locator('input').press('Enter')
    page.evaluate('__pendingSaves.shift()()')
    page.wait_for_function("JSON.parse(getDeltas()).some(d=>d.value === 'house')")
    assert {d['value'] for d in page.evaluate('JSON.parse(getDeltas())')} >= {'house', 'other pending value'}


def test_sqlite_delete_undo_restores_exact_record(tmp_path):
    arbiter, adapter, zid, _ = _make_persist_fixture(tmp_path)
    before = adapter.db.get_words_by_session(zid)
    removed = adapter.persist_word_deletion(zid, 1, 0, True)
    assert len(adapter.db.get_words_by_session(zid)) == len(before) - 1
    adapter.persist_word_deletion(zid, 1, 0, False, removed)
    assert adapter.db.get_words_by_session(zid) == before


def test_delete_undo_during_save_produces_inverse_delta(audit_page):
    page = audit_page
    page.evaluate('__holdSaves = true; deleteSelectedRows(); void onSaveClick(true)')
    page.wait_for_function('__pendingSaves.length === 1')
    page.evaluate('undo(); __pendingSaves.shift()()')
    page.wait_for_function("JSON.parse(getDeltas()).some(d=>d.column === '_delete' && d.value === false)")


def test_native_snapshot_rejects_stale_and_allows_retry(audit_page):
    page = audit_page
    page.evaluate('window.__native = captureSaveSnapshot()')
    with pytest.raises(Exception, match='does not match'):
        page.evaluate("acknowledgeSaveSnapshot('invalid')")
    page.evaluate('abortSaveSnapshot(); window.__retry = captureSaveSnapshot(); acknowledgeSaveSnapshot(__retry)')
    with pytest.raises(Exception, match='does not match'):
        page.evaluate('acknowledgeSaveSnapshot(__retry)')


def test_concurrent_independent_session_patches(tmp_path):
    first, adapter, zid, _ = _make_persist_fixture(tmp_path)
    second = zid[:-1] + '1'
    bundle = adapter.db.get_session_bundle(zid)
    session = dict(bundle['session'], zid=second)
    sentences = [dict(s, session_zid=second) for s in bundle['sentences']]
    words = [dict(w, session_zid=second) for w in bundle['words']]
    adapter.db.save_session_bundle(session, sentences, words)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda identity: adapter.persist_retext(identity, {1: identity}, {}), [zid, second]))
    assert adapter.db.get_sentences_by_session(zid)[0]['sentence_destination'] == zid
    assert adapter.db.get_sentences_by_session(second)[0]['sentence_destination'] == second


def test_browser_windows_sync_without_echoes(page, running_controller, tmp_path):
    url, server, adapter = running_controller
    zid = '20261024225159'
    server.config.set('storage', 'sqlite_db_path', str(adapter.db.db_path))
    server.resolved_paths['storage_backend'] = 'sqlite'
    server.config.set('server', 'port', str(server.server_port))
    server.config.set('server', 'controller_port', str(server.server_port))
    headers = ['TokenOrder', 'SentenceSourceIndex', 'Quotation', 'WordSource', 'WordDestination', 'DeskSelected']
    rows = [['0', '1', 'Haus', 'Haus', 'house', '0'], ['1', '2', 'Haus', 'Haus', 'house', '0']]
    adapter.save_session(session_zid=zid, source_language='de', source_raw_text='Das Haus. Ein Haus.', headers=headers, data_rows=rows)
    html = desk.run_render_flow('Das Haus. Ein Haus.', 'de', zid, 'single', server.config,
                               server.resolved_paths, tsv_path=str(tmp_path / f'{zid}.de.tsv'), spawn_children=False)
    pages = [page, page.context.new_page()]
    requests = []
    for current in pages:
        current.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
        current.on('request', lambda req: requests.append(req.url) if '/session/save' in req.url else None)
        current.route(url + '/', lambda route: route.fulfill(body=html, content_type='text/html'))
        current.goto(url + '/')
        current.evaluate('(args) => SSEBridge.connect(args.zid, args.url)', {'zid': zid, 'url': url})
        current.wait_for_function("document.querySelector('#sse-status-indicator[data-status=connected]') !== null")
    pages[0].locator('#lemma-table tr[data-row-id]').first.click()
    pages[1].wait_for_function("Object.keys(AppState.selectedLemmas).includes('haus')")
    pages[1].locator('#lemma-table tr[data-row-id]').first.click()
    pages[0].wait_for_function("!Object.keys(AppState.selectedLemmas).includes('haus')")
    for current in pages:
        current.wait_for_function('!isDirty()')
    assert len(requests) == 2
    assert all(not word['selected'] for word in adapter.db.get_words_by_session(zid))
    pages[1].close()


def test_existing_multi_source_initialization_is_preserved(page, tmp_path):
    from tests.test_same_lemma_toggle import get_multi_occurrence_desk_page_html, inject_mock_fetch
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content(inject_mock_fetch(get_multi_occurrence_desk_page_html(tmp_path)))
    assert page.locator('#source-container span.word').count() > 0, {
        'errors': errors, 'source': page.locator('#source-container').inner_html(),
        'state': page.evaluate('({source:AppState.sourceText,selected:AppState.selectedLemmas})')}


@pytest.mark.parametrize('theme', ['dark', 'light', 'white', 'compact'])
@pytest.mark.parametrize('width,zoom', [(1280, 100), (620, 125)])
def test_layout_evidence_and_scoped_update_cost(page, tmp_path, theme, width, zoom):
    from tests.test_release_audit_regressions import audit_html, TRANSPORT
    from pathlib import Path
    page.set_viewport_size({'width': width, 'height': 900})
    page.set_content(audit_html(tmp_path, theme=theme).replace('<head>', '<head>' + TRANSPORT))
    page.locator('.kw-lookup-container').evaluate('(el,z)=>el.style.zoom=z+"%"',zoom)
    page.evaluate('reserveTextRegions(true)')
    measure = """() => ['source-container','translation-container','lemma-table','kw-action-toolbar'].map(id=>{
        const r=document.getElementById(id).getBoundingClientRect();return [r.x,r.y,r.width,r.height];})"""
    before=page.evaluate(measure)
    page.evaluate("receiveUpdate({translatedText:Array(40).fill('Complete multiline translated sentence.').join('\\n')})")
    after=page.evaluate(measure)
    shift=max(abs(a-b) for left,right in zip(before,after) for a,b in zip(left,right))
    assert shift < .01
    result=page.evaluate("""() => {
        const node=document.querySelector('#source-container span');
        const start=performance.now();
        for(let i=0;i<100;i++) AppStore.applyDeltas({stage:'translating'});
        return {milliseconds:performance.now()-start,same:node===document.querySelector('#source-container span'),
            toolbarWeight:getComputedStyle(document.getElementById('kw-btn-save')).fontWeight};
    }""")
    assert result['same']
    assert result['toolbarWeight']=='500'
    destination=Path('results/20261024225159-layout')
    destination.mkdir(exist_ok=True)
    name=f'{theme}-{width}-{zoom}'
    page.screenshot(path=str(destination/(name+'.png')),full_page=True)
    (destination/(name+'.json')).write_text(json.dumps(dict(result,max_shift_px=shift)))



def test_native_polling_retains_pending_events_and_disposes(running_controller):
    from urllib.request import Request, urlopen
    url, server, adapter = running_controller
    zid='20261024225159'
    def poll(**extra):
        body=dict(session_zid=zid,client_id='headless-native',**extra)
        request=Request(url+'/internal/selection',data=json.dumps(body).encode(),
                        headers={'Content-Type':'application/json','X-API-Token':'test-controller-api-key'})
        with urlopen(request,timeout=3) as response:
            return json.load(response)['data']['events']
    assert poll()==[]
    server.arbiter.emit_event(zid,{'stage':'translated_text','translatedText':'First stage'})
    assert poll(accept_updates=False)==[]
    assert [event['translatedText'] for event in poll()]==['First stage']
    server.arbiter.emit_event(zid,{'stage':'finished'})
    assert poll(close=True)==[]
    assert poll()==[]
    poll(close=True)



def test_non_latin_save_and_f5_execute_once(audit_page):
    page=audit_page
    cell=page.locator('td[data-col="WordDestination"]').first
    cell.dblclick()
    cell.locator('input').fill('pending keyboard edit')
    cell.locator('input').press('Enter')
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'\u044b',code:'KeyS',keyCode:83,which:83,ctrlKey:true,bubbles:true,cancelable:true}))")
    page.wait_for_function('!isDirty()')
    assert page.evaluate("__requests.filter(r=>r.url.includes('/session/save')).length")==1
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'F5',code:'F5',keyCode:116,which:116,bubbles:true,cancelable:true}))")
    page.wait_for_function("__requests.some(r=>r.url.includes('/session/status'))")
    assert page.evaluate("__requests.filter(r=>r.url.includes('/session/status')).length")==1
