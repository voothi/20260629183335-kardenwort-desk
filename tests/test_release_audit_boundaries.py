"""Real filesystem/SQLite and browser contracts for the release repairs."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

import kardenwort_desk as desk
import kardenwort_grammar as grammar
from kardenwort_theme import THEME_COLOR_PROPERTIES, resolve_theme_colors, theme_style
from tests.test_controller_integration import running_controller
from tests.test_release_audit_regressions import audit_html, TRANSPORT
from scripts.release_audit_baseline import pairwise_matrix


@pytest.mark.parametrize('warm', [False, True])
@pytest.mark.parametrize('asset', ['index.html', 'css/table.css', 'js/app.js', 'js/components/WorkspaceTabs.js'])
@pytest.mark.parametrize('renderer', ['desktop', 'lookup'])
def test_required_assets_cold_and_warm(tmp_path, monkeypatch, asset, warm, renderer):
    root = Path(desk.__file__).parent
    shutil.copytree(root / 'web', tmp_path / 'web')
    monkeypatch.setattr(desk, '__file__', str(tmp_path / 'kardenwort_desk.py'))
    config, paths, goldendict, _ = desk.load_config(root / 'config.ini')

    def render():
        if renderer == 'desktop':
            return desk.render_declarative_web_shell({'ui_config': {}})
        return desk.render_lookup_html('Haus', 'de', 'en', config, paths, '20261023223509', goldendict,
                                       [], ['WordSource', 'WordDestination'], [['Haus', 'house']], 'house')

    if warm:
        assert render().startswith('<!DOCTYPE html>')
    (tmp_path / 'web' / asset).unlink()
    with pytest.raises(FileNotFoundError, match=Path(asset).name.replace('.', r'\.')):
        render()


@pytest.mark.parametrize('theme', ['dark', 'light', 'white', 'compact'])
def test_theme_overrides_are_shared_and_color_only(page, theme):
    config, _, _, _ = desk.load_config()
    for section in ('theme_dark', 'theme_light'):
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, 'bg_color', '#123456')
        config.set(section, 'text_color', '#abcdef')
        config.set(section, 'not_a_property', '</style>')
    colors = resolve_theme_colors(config, theme)
    shell = desk.render_declarative_web_shell({'theme_colors': colors}, theme=theme, source_html='Haus')
    page.set_content(shell.replace('<head>', '<head>' + TRANSPORT))
    if theme == 'compact':
        assert colors == {}
    else:
        assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(18, 52, 86)'
        assert page.evaluate('getComputedStyle(document.body).color') == 'rgb(171, 205, 239)'
    assert all('font' not in name and 'height' not in name for name in colors)
    assert 'not_a_property' not in theme_style(colors, theme)


@pytest.mark.parametrize('value', ['red;display:none', '</style><script>bad()</script>', 'url(secret)', 'nonsense'])
def test_unsafe_theme_colors_fail_before_render(value):
    config, _, _, _ = desk.load_config()
    if not config.has_section('theme_dark'):
        config.add_section('theme_dark')
    config.set('theme_dark', 'bg_color', value)
    with pytest.raises(ValueError, match='theme color'):
        resolve_theme_colors(config, 'dark')


def test_theme_key_set_matches_baseline():
    assert len(THEME_COLOR_PROPERTIES) == 37


@pytest.mark.parametrize('lemma,gender,pos', [('der', '', 'art.'), ('Moment', 'm.', 'n.'), ('Student', 'm.', 'NOUN')])
def test_canonical_grammar_browser_server_parity(page, tmp_path, lemma, gender, pos):
    page.set_content(audit_html(tmp_path).replace('<head>', '<head>' + TRANSPORT))
    payload = grammar.get_grammar_payload()
    assert set(payload) == {'pos_full_name_map', 'gender_full_name_map', 'german_articles', 'german_pronouns',
                            'noun_tags', 'non_noun_tags', 'suffixes_masculine', 'suffixes_feminine',
                            'suffixes_neuter', 'suffix_excluded_in', 'suffix_excluded_ent'}
    assert page.evaluate('args => formatLemmaTooltip(...args)', [lemma, gender, 'de', pos]) == grammar.format_lemma_article_tooltip(lemma, gender, 'de', pos)
    assert page.evaluate('args => formatGenderTooltip(...args)', [gender, lemma, None]) == grammar.format_gender_tooltip(gender, lemma)
    assert page.evaluate('pos => formatPosTooltip(pos)', pos) == grammar.format_pos_tooltip(pos)


def test_retext_sentence_read_failure_has_no_side_effects(running_controller, monkeypatch):
    url, server, adapter = running_controller
    zid = '20261023223509'
    tsv = Path(server.resolved_paths['results_dir']) / f'{zid}.de.tsv'
    headers = ['WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'DeskSelected']
    rows = [['Haus', 'house', '1', 'Das Haus.', '']]
    adapter.save_session(session_zid=zid, slug='read-failure', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Das Haus.', headers=headers, data_rows=rows, tsv_path=str(tsv))
    server.arbiter.sessions[zid] = {'text': 'Das Haus.', 'headers': headers, 'data_rows': rows, 'tsv_path': str(tsv)}
    before = copy.deepcopy(server.arbiter.sessions)
    provider_calls = []
    monkeypatch.setattr('kardenwort_controller.translate_source_text', lambda *a, **k: provider_calls.append(a))
    subscriber = server.arbiter.register_subscriber(zid)
    # A real storage failure, not a mocked adapter or sentence collection.
    with adapter.db.get_connection() as conn:
        conn.execute('ALTER TABLE sentences RENAME TO unavailable_sentences')
    with adapter.db.get_connection(read_only=True) as conn:
        database_before = '\n'.join(conn.iterdump())
    request = urllib.request.Request(url + '/session/retext', data=json.dumps({'session_zid': zid, 'language': 'de'}).encode(),
                                     headers={'Content-Type': 'application/json', 'X-API-Token': 'test-controller-api-key'})
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        body = error.value.read().decode()
        assert 'DESK_FAILED' in body
        assert 'sentence read failed' in body
        assert provider_calls == []
        assert server.arbiter.sessions == before
        assert not tsv.exists()
        assert subscriber.empty()
        with adapter.db.get_connection(read_only=True) as conn:
            assert '\n'.join(conn.iterdump()) == database_before
    finally:
        server.arbiter.unregister_subscriber(zid, subscriber)


@pytest.mark.parametrize('row', pairwise_matrix()['rows'], ids=lambda row: '-'.join(row.values()))
def test_pairwise_shell_preferences(page, tmp_path, row):
    # Native rows verify the shared HTML projection in Chromium; actual native
    # engine/bridge execution is reported separately, never claimed here.
    html = audit_html(tmp_path, theme=row['theme'], container=row['cards'] == 'container')
    page.route('http://audit.local/**', lambda route: route.fulfill(content_type='text/html', body=html.replace('<head>', '<head>' + TRANSPORT)))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://audit.local/')
    enabled = row['filter'] == 'on'
    page.evaluate('enabled => {setFilterSelectedOnly(enabled);setHighlightSameLemma(true)}', enabled)
    if row['transport'] == 'reload':
        page.reload()
    elif row['transport'] in ('sse', 'overlap'):
        page.evaluate("SSEBridge.connect('20261023221555'); __eventSource.onmessage({data: JSON.stringify({stage:'translating'})})")
    if row['transport'] in ('status', 'overlap'):
        page.keyboard.press('F5')
        page.wait_for_function("__requests.some(r => r.url.includes('/session/status'))")
    assert page.evaluate('AppState.filterSelectedOnly') == enabled
    assert page.evaluate('AppState.highlightSameLemma') is True
    assert page.locator('#kw-btn-filter-selected').evaluate('el => getComputedStyle(el).fontWeight') == '500'
    assert errors == []


@pytest.mark.parametrize('theme', ['dark', 'light', 'white', 'compact'])
@pytest.mark.parametrize('width,zoom', [(1280, 100), (620, 125)])
def test_long_translation_update_preserves_active_layout(page, tmp_path, theme, width, zoom):
    page.set_viewport_size({'width': width, 'height': 900})
    page.set_content(audit_html(tmp_path, theme=theme).replace('<head>', '<head>' + TRANSPORT))
    page.locator('.kw-lookup-container').evaluate('(el, zoom) => {el.style.zoom = zoom + "%"}', zoom)
    page.evaluate('reserveTextRegions(true)')
    measure = """() => ['source-container','translation-container','lemma-table','kw-action-toolbar'].map(id => {
        const r = document.getElementById(id).getBoundingClientRect(); return [r.x,r.y,r.width,r.height];
    })"""
    before = page.evaluate(measure)
    page.evaluate("receiveUpdate({translatedText: Array(40).fill('A complete multiline translated sentence.').join('\\n')})")
    after = page.evaluate(measure)
    assert all(abs(a-b) < .01 for left, right in zip(before, after) for a, b in zip(left, right))
    assert page.locator('#translation-container').evaluate('el => el.scrollHeight > el.clientHeight')
    assert 'A complete multiline translated sentence.' in page.locator('#translation-container').inner_text()


@pytest.mark.parametrize('gesture', ['click', 'select_all', 'checkbox', 'drag'])
def test_browser_selection_autosave_roundtrip_to_real_sqlite(page, tmp_path, running_controller, gesture):
    url, server, adapter = running_controller
    zid = '20261023221555'
    server.config.set('storage', 'backend', 'sqlite')
    server.resolved_paths['storage_backend'] = 'sqlite'
    headers = ['Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'DeskSelected', 'TokenOrder']
    rows = [['Haus', 'Haus', 'house', '1', 'Das Haus.', '1', '0'],
            ['Baum', 'Baum', 'tree', '2', 'Der Baum.', '', '1']]
    adapter.save_session(session_zid=zid, slug='selection-browser', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Das Haus. Der Baum.', headers=headers, data_rows=rows)
    html = audit_html(tmp_path)
    page.route(url + '/', lambda route: route.fulfill(content_type='text/html', body=html))
    page.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
    page.goto(url + '/')
    # A cell edit stays uncommitted while selection saves independently.
    cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]')
    cell.dblclick()
    cell.locator('input').fill('uncommitted text')
    cell.locator('input').press('Enter')
    with page.expect_response(lambda response: '/session/save' in response.url) as saved:
        if gesture == 'click':
            page.locator('#lemma-table tr[data-row-id="1"] td[data-col="WordSource"]').click()
        elif gesture == 'select_all':
            page.evaluate('selectAllRows()')
        elif gesture == 'checkbox':
            page.evaluate("kwToggleTag({checked:true, closest:() => document.querySelector('tr[data-row-id=\"1\"]')}, 1)")
        else:
            page.evaluate('clearAllSelections()')
            start = page.locator('#lemma-table tr[data-row-id="0"]').bounding_box()
            end = page.locator('#lemma-table tr[data-row-id="1"]').bounding_box()
            page.mouse.move(start['x'] + 10, start['y'] + 10)
            page.mouse.down()
            page.mouse.move(end['x'] + 10, end['y'] + 10, steps=5)
            page.mouse.up()
    assert saved.value.ok, saved.value.text()
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    bundle = adapter.restore_session(zid)
    cols = bundle['headers']
    by_lemma = {row[cols.index('WordSource')]: row for row in bundle['data_rows']}
    assert by_lemma['Baum'][cols.index('DeskSelected')] == '1'
    assert by_lemma['Haus'][cols.index('WordDestination')] == 'house'
    assert any(delta['column'] == 'WordDestination' for delta in json.loads(page.evaluate('getDeltas()')))
    with page.expect_response(lambda response: '/session/save' in response.url) as cleared:
        page.evaluate('clearAllSelectionsAndNotify()')
    assert cleared.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    restored = adapter.restore_session(zid)
    selected_column = restored['headers'].index('DeskSelected')
    assert all(row[selected_column] != '1' for row in restored['data_rows'])


def test_failed_save_prevents_f5_and_keeps_dirty_input(page, tmp_path):
    page.set_content(audit_html(tmp_path).replace('<head>', '<head>' + TRANSPORT))
    page.evaluate("__requests=[]; window.fetch = async function(url) { __requests.push({url:String(url)}); return {ok:false, json:async()=>({ok:false,error:'offline'})}; }")
    page.locator('#lemma-table tr[data-row-id="1"]').click()
    page.keyboard.press('F5')
    page.wait_for_function("document.querySelector('.kw-toast-error') !== null")
    assert page.evaluate('isDirty()') is True
    assert not page.evaluate("__requests.some(r => r.url.includes('/session/status'))")
    assert page.evaluate('__reloads') == 0


def test_native_snapshot_ack_keeps_later_selection(page, tmp_path):
    page.set_content(audit_html(tmp_path).replace('<head>', '<head>' + TRANSPORT))
    page.locator('#lemma-table tr[data-row-id="1"]').click()
    snapshot = page.evaluate('captureSaveSnapshot()')
    page.locator('#lemma-table tr[data-row-id="0"]').click()
    assert page.evaluate('snapshot => acknowledgeSaveSnapshot(snapshot)', snapshot) is True
    assert json.loads(page.evaluate('getDeltas()'))


@pytest.mark.parametrize('view', ['', '&view=goldendict'])
@pytest.mark.parametrize('asset', ['index.html', 'css/table.css', 'js/app.js', 'js/components/WorkspaceTabs.js'])
@pytest.mark.parametrize('warm', [False, True])
def test_http_required_asset_failure_is_json_without_html(running_controller, tmp_path, monkeypatch, view, asset, warm):
    url, server, adapter = running_controller
    root = Path(desk.__file__).parent
    shutil.copytree(root / 'web', tmp_path / 'web')
    monkeypatch.setattr(desk, '__file__', str(tmp_path / 'kardenwort_desk.py'))
    server.config.set('storage', 'backend', 'sqlite')
    server.resolved_paths.update(storage_backend='sqlite', kardenwort_workspace=tmp_path)
    zid = '20261023232442'
    adapter.save_session(session_zid=zid, slug='asset-http', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Haus',
                         headers=['WordSource', 'WordDestination'], data_rows=[['Haus', 'house']])
    request = urllib.request.Request(url + '/session/render?session_zid=' + zid + view,
                                     headers={'X-API-Token': 'test-controller-api-key'})
    if warm:
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert '<html' in response.read().decode().lower()
    html_before = {p: p.read_bytes() for p in tmp_path.rglob('*.html')}
    (tmp_path / 'web' / asset).unlink()
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=10)
    assert error.value.code == 500
    assert error.value.headers.get_content_type() == 'application/json'
    body = error.value.read().decode()
    assert Path(asset).name in body
    assert '<html' not in body.lower()
    assert json.loads(body)['status'] == 'error'
    assert json.loads(body)['error_code'] == 'SERVER_ERROR'
    assert {p: p.read_bytes() for p in tmp_path.rglob('*.html')} == {
        p: contents for p, contents in html_before.items() if p.exists()}


@pytest.mark.parametrize('theme', ['dark', 'light', 'white', 'compact'])
@pytest.mark.parametrize('width,zoom', [(1280, 100), (620, 125)])
def test_loading_lifecycle_preserves_rows_wrapping_and_scroll(page, tmp_path, theme, width, zoom):
    page.set_viewport_size({'width': width, 'height': 900})
    page.set_content(audit_html(tmp_path, theme=theme).replace('<head>', '<head>' + TRANSPORT))
    page.locator('.kw-lookup-container').evaluate('(el, zoom) => {el.style.zoom = zoom + "%"}', zoom)
    page.evaluate('reserveTextRegions(true)')
    measure = """() => {
        const bounds = selector => Array.from(document.querySelectorAll(selector)).map(el => {
            const r = el.getBoundingClientRect(); return [r.x,r.y,r.width,r.height];
        });
        return {boxes: bounds('#source-container,#translation-container,#lemma-table,#lemma-table tr,#kw-action-toolbar button'),
            lines: bounds('#source-container .word'), scroll: [scrollX,scrollY,
                document.querySelector('#source-container').scrollTop,
                document.querySelector('#translation-container').scrollTop]};
    }"""
    before = page.evaluate(measure)
    transitions = [
        {'stage': 'translating', 'active_text_provider': 'google', 'text_translation_status': 'pending'},
        {'stage': 'translating', 'active_text_provider': 'deepl', 'text_translation_status': 'pending'},
        {'stage': 'translating', 'text_translation_status': 'failed', 'text_translation_failed': True},
        {'stage': 'translating', 'active_text_provider': 'google', 'text_translation_status': 'pending', 'text_translation_failed': False},
        {'stage': 'finished', 'text_translation_status': 'finished', 'translatedText': '\n'.join(['Complete long translation.'] * 40)},
    ]
    for delta in transitions:
        page.evaluate('receiveUpdate', delta)
        after = page.evaluate(measure)
        for kind in ('boxes', 'lines'):
            assert len(before[kind]) == len(after[kind]), (delta, kind)
            assert all(abs(a-b) < .01 for left, right in zip(before[kind], after[kind]) for a, b in zip(left, right)), (delta, kind)
        assert before['scroll'] == after['scroll'], delta
    assert page.locator('#translation-container').evaluate('el => el.scrollHeight > el.clientHeight')


def test_edit_during_selection_save_survives_ack_and_offline_retry(page, tmp_path):
    page.set_content(audit_html(tmp_path).replace('<head>', '<head>' + TRANSPORT))
    page.evaluate('__holdSaves = true')
    page.locator('#lemma-table tr[data-row-id="1"]').click()
    page.wait_for_function('__pendingSaves.length === 1')
    cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]')
    cell.dblclick()
    cell.locator('input').fill('newer edit')
    cell.locator('input').press('Enter')
    page.evaluate('__pendingSaves.shift()()')
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    assert any(d['value'] == 'newer edit' for d in json.loads(page.evaluate('getDeltas()')))
    page.evaluate("window.__workingFetch=fetch; window.fetch=async()=>({ok:false,json:async()=>({ok:false,error:'offline'})})")
    page.evaluate('void onSaveClick()')
    page.wait_for_function("document.querySelector('.kw-toast-error') !== null")
    assert page.evaluate('isDirty()')
    page.evaluate('window.fetch=__workingFetch; __holdSaves=false; void onSaveClick()')
    page.wait_for_function('!isDirty()')
    assert cell.inner_text() == 'newer edit'


@pytest.mark.parametrize('authorized', [True, False])
def test_native_production_bridge_to_real_sqlite(running_controller, tmp_path, authorized):
    """Execute the real AHK/WinHTTP bridge without opening or automating a GUI."""
    url, server, adapter = running_controller
    executable = desk.get_ahk_executable()
    if not executable:
        executable = next(Path('C:/AHK').glob('AutoHotkey_*/AutoHotkey64.exe'), None)
    configured_repo = os.environ.get('KARDENWORT_NATIVE_REPO')
    native_repo = Path(configured_repo) if configured_repo else next(Path(desk.__file__).parent.parent.glob('*-autohotkey'), None)
    if not executable or not native_repo:
        pytest.skip('Native AHK runtime/repository unavailable')
    zid = '20261023233139'
    server.config.set('storage', 'backend', 'sqlite')
    server.resolved_paths['storage_backend'] = 'sqlite'
    adapter.save_session(session_zid=zid, slug='native-bridge', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Haus',
                         headers=['TokenOrder', 'SentenceSourceIndex', 'WordSource', 'DeskSelected'],
                         data_rows=[['0', '1', 'Haus', '0']])
    payload = tmp_path / 'native-save.json'
    payload.write_text(json.dumps({'session_zid': zid, 'deltas': [
        {'row_id': 0, 'token_order': 0, 'sentence_idx': 1, 'column': 'DeskSelected', 'value': '1'}]}), encoding='utf-8')
    harness = tmp_path / 'native-bridge.ahk'
    harness.write_text(f'''#Requires AutoHotkey v2.0
global G_FsmTestMode := true
#Include "{native_repo / 'Lib' / 'B64Util.ahk'}"
#Include "{native_repo / 'kardenwort-window' / 'kardenwort-window.ahk'}"
G_ServerHost := "127.0.0.1"
G_ControllerPort := {server.server_port}
G_ServerApiKey := "{'test-controller-api-key' if authorized else 'invalid-test-key'}"
response := OnAhkCall({{}}, "persist_selection", FileRead(A_Args[1], "UTF-8"))
FileAppend(response, "*")
ExitApp(0)
''', encoding='utf-8')
    result = subprocess.run([str(executable), '/ErrorStdOut', str(harness), str(payload)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    restored = adapter.restore_session(zid)
    selected = restored['data_rows'][0][restored['headers'].index('DeskSelected')]
    if authorized:
        assert response.get('ok') or response.get('status') == 'success'
        assert selected == '1'
    else:
        assert response['ok'] is False
        assert 'changes retained' in response['error']
        assert selected != '1'


def test_select_switch_update_delayed_ack_reload_with_sqlite(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    zid = '20261023223509'
    config, paths = server.config, server.resolved_paths
    config.set('storage', 'backend', 'sqlite')
    paths['storage_backend'] = 'sqlite'
    config.set('sentences_mode', 'enabled', 'true')
    config.set('sentences_mode', 'delivery_mode', 'container')
    config.set('sentences_mode', 'spawn_order', 'normal')
    config.set('server', 'enabled', 'false')
    config.set('rendering', 'filter_selected_only', 'false')
    headers = ['Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination', 'DeskSelected', 'TokenOrder']
    rows = [['Haus', 'Haus', 'house', '1', 'Das Haus.', 'The house.', '', '0'],
            ['Baum', 'Baum', 'tree', '2', 'Der Baum.', 'The tree.', '', '1']]
    adapter.save_session(session_zid=zid, slug='race', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Das Haus. Der Baum.', headers=headers, data_rows=rows)

    def render(route):
        bundle = adapter.restore_session(zid)
        tsv = tmp_path / f'{zid}-race.de.tsv'
        desk.save_tsv_rows_safely(tsv, [], bundle['headers'], bundle['data_rows'])
        html = desk.run_render_flow(text='Das Haus. Der Baum.', language='de', zid=zid, text_mode='single',
                                    config=config, resolved_paths=paths, tsv_path=str(tsv), spawn_children=False, seq_num=1)
        route.fulfill(content_type='text/html', body=html)

    pending, status = [], []
    page.route(url + '/', render)

    def hold_save(route):
        pending.append(route)
        page.evaluate('window.__heldSaveCount = (window.__heldSaveCount || 0) + 1')

    page.route('**/session/save', hold_save)

    def complete_status(route):
        updating = page.locator('#kw-btn-update').is_disabled()
        status.append({'url': route.request.url, 'updating': updating})
        if updating:
            page.evaluate('window.__updateStatusServed = true')
        route.fulfill(json={'ok': True, 'stage': 'finished'})

    page.route('**/session/status?*', complete_status)
    page.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
    page.add_init_script("window.EventSource = class {constructor(){window.__raceSSE=this} close(){}}")
    page.goto(url + '/')
    assert page.locator('#lemma-table tr[data-row-id]').count() == 2, page.locator('body').inner_text()
    with page.expect_request('**/session/save'):
        page.locator('#lemma-table tr[data-row-id="1"]').click()
    page.wait_for_function('window.__heldSaveCount === 1')
    assert pending[0].request.post_data_json['deltas'][0]['sentence_idx'] == 0
    page.locator('.kw-tab-chip[data-tab-seq="2"]').click()
    page.locator('#lemma-table tr[data-row-id="0"]').click()
    page.evaluate("SSEBridge.connect('20261023223509'); __raceSSE.onmessage({data:JSON.stringify({stage:'translating'})})")
    page.keyboard.press('F5')
    assert not any(request['updating'] for request in status)
    with page.expect_request('**/session/save'):
        first = pending.pop(0)
        first.fulfill(response=first.fetch())
    page.wait_for_function('window.__heldSaveCount === 2')
    assert not any(request['updating'] for request in status)
    # Shared selection writes overview and child identities (September 28 contract).
    assert {delta['sentence_idx'] for delta in pending[0].request.post_data_json['deltas']} == {0, 1}
    with page.expect_request('**/session/status?*'):
        second = pending.pop(0)
        second.fulfill(response=second.fetch())
    page.wait_for_function('!isDirty()')
    page.wait_for_function('window.__updateStatusServed === true')
    assert any(request['updating'] for request in status)
    assert adapter.get_overview_selections(zid) == {0, 1}
    restored = adapter.restore_session(zid)
    source_column = restored['headers'].index('WordSource')
    selected_column = restored['headers'].index('DeskSelected')
    assert {row[source_column]: row[selected_column] for row in restored['data_rows']} == {'Haus': '1', 'Baum': '1'}
    page.reload()
    assert page.evaluate('WorkspaceTabs.getActiveCard().sentence_idx') == 1
    assert page.locator('#lemma-table tr[data-row-id="0"]').get_attribute('data-selected') == '1', page.evaluate("WorkspaceTabs.getCards().map(c=>({idx:c.sentence_idx,ids:c.selected_ids,words:c.words.map(w=>({id:w.row_id,selected:w.selected}))}))")
    page.locator('.kw-tab-chip[data-tab-seq="1"]').click()
    assert page.locator('#lemma-table tr[data-row-id="1"]').get_attribute('data-selected') == '1'
    assert page.locator('#lemma-table tr[data-row-id="0"]').get_attribute('data-selected') == '1'
