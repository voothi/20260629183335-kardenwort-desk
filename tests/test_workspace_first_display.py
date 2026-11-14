"""First visible projection uses the selected card, including before app hydration."""
import json
import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.render_test_support import seed_render_rows
from tests.toolbar_test_support import component_configuration  # noqa: F401
from tests.test_sqlite_state_browser import sqlite_page, ready  # noqa: F401
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.controller_test_support import controller_world  # noqa: F401
from tests.toolbar_test_support import controlled_event_stream_script
from tests.test_release_boundary_contracts import post
from scripts.benchmark_release_interactions import ZID


@pytest.fixture
def marked_page(sqlite_page, request):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    boot = json.loads(page.locator('#kardenwort-bootstrap').text_content())
    theme, zoom, dock, disable_css = getattr(request, 'param', ('dark', '100', 'top', False))
    boot['tab_bar_position'] = dock
    words = page.locator('#source-container .word').evaluate_all('(items) => items.map(el => el.outerHTML)')
    source = ''.join(f'<span class="kw-sentence-chunk" data-sentence-idx="{i+1}">{word}.</span>'
                     for i, word in enumerate(words))
    for card in boot['sentence_cards']:
        card['translated_text'] = 'OVERVIEW TRANSLATION' if card['seq_num'] == 1 else f'CARD {card["seq_num"]} TRANSLATION'
        card['tsv_filename'] = f'card-{card["seq_num"]}.de.tsv'
    adapter.db.insert_sentences([dict(session_zid=ZID, sentence_index=i, sentence_source=f'wort{i}.',
        sentence_destination=f'CARD {i+1} TRANSLATION') for i in range(1, 4)])
    chips = ''.join(f'<button type="button" class="kw-tab-chip{" active" if card["seq_num"] == 1 else ""}" '
        f'data-tab-seq="{card["seq_num"]}" data-sentence-idx="{card["sentence_idx"]}">{card["seq_num"]}</button>'
        for card in boot['sentence_cards'])
    tabs = f'<div class="kw-workspace-tab-bar kw-tab-dock-{dock}" id="kw-workspace-tab-bar">' \
        '<button type="button" id="kw-tab-prev">&lt;</button>' \
        f'<div class="kw-tab-track" id="kw-tab-track">{chips}</div>' \
        '<button type="button" id="kw-tab-next">&gt;</button></div>'
    def document(route):
        route.fulfill(content_type='text/html', body=desk.render_declarative_web_shell(boot, source_html=source,
            theme=theme, zoom_level=zoom, disable_css=disable_css, workspace_tab_bar_html=tabs,
            dock_body_class=' has-dock-'+dock if dock != 'inline' else ''))
    page.context.route(url+'/', document)
    return sqlite_page


@pytest.mark.parametrize('order', ['normal', 'reverse'])
@pytest.mark.parametrize('seq', [None, 1, 3])
@pytest.mark.parametrize('translation_state', ['ready', 'pending', 'failed', 'mixed_pending', 'mixed_failed'])
def test_server_card_before_javascript(browser, component_configuration, order, seq, translation_state):
    config, paths, _, _ = desk.load_config()
    config.read_dict({'sentences_mode': {'enabled': 'true', 'delivery_mode': 'container', 'spawn_order': order}})
    config.read_dict({'rendering': {'display_mode': 'progressive'}, 'pipeline': {'progressive_text_translation': 'true'},
        'triggers': {'run_text_translation': 'auto' if translation_state.endswith('pending') else 'manual'}})
    text = 'Alpha first!\n\nBeta second?'
    headers = ['Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination']
    rows = [['Alpha', 'Alpha', 'uno', '1', 'Alpha first!', 'FIRST TRANSLATION'],
            ['Beta', 'Beta', 'dos', '2', 'Beta second?', 'SECOND TRANSLATION']]
    if translation_state.startswith('mixed_'):
        rows[0][-1] = '' if translation_state.endswith('pending') else '[FAILED]'
    elif translation_state != 'ready':
        for row in rows:
            row[-1] = '' if translation_state == 'pending' else '[FAILED]'
    seed_render_rows(config, paths, '20261114091621', text, 'en', headers, rows, text_mode='multi')
    html = desk.run_render_flow(text=text, language='en', zid='20261114091621', text_mode='multi',
        config=config, resolved_paths=paths, spawn_children=False, seq_num=seq)
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.set_content(html)
        active = seq or 2
        active_state = translation_state.removeprefix('mixed_') if active == 2 else translation_state
        if translation_state.startswith('mixed_') and active == 3:
            active_state = 'ready'
        assert page.locator('.kw-tab-chip.active').get_attribute('data-tab-seq') == str(active)
        cards = json.loads(page.locator('#sentence-cards').text_content())
        assert next(card['tsv_filename'] for card in cards if card['seq_num'] == active) in page.title()
        table = page.locator('#lemma-table').inner_text().lower()
        assert ('alpha' in table) == (active in (1, 2))
        assert ('beta' in table) == (active in (1, 3))
        source = page.locator('#source-container').inner_text().strip()
        translation = page.locator('#translation-container').inner_text()
        if active == 1:
            assert 'Alpha first!' in source and 'Beta second?' in source
            if translation_state == 'ready':
                assert 'FIRST TRANSLATION' in translation and 'SECOND TRANSLATION' in translation
            elif translation_state.startswith('mixed_'):
                assert translation.strip() == 'SECOND TRANSLATION'
        else:
            expected = ('Alpha first!', 'FIRST TRANSLATION') if active == 2 else ('Beta second?', 'SECOND TRANSLATION')
            assert source == expected[0]
            if active_state == 'ready':
                assert translation.strip() == expected[1]
        if active_state == 'pending':
            assert page.locator('#translation-container [data-pending="true"]').count() == 1
        if active_state == 'failed':
            assert translation.strip() == ''
        assert page.locator('.kw-sentence-chunk').count() == 2
        assert page.locator('link[rel=icon]').get_attribute('href').endswith(f'/{active}.ico')
    finally:
        context.close()


def frames(page):
    return page.evaluate('''() => new Promise(resolve => {
        const values = [];
        function sample() {
            const source = document.getElementById('source-container');
            const translation = document.getElementById('translation-container');
            const rows = document.getElementById('lemma-table');
            values.push({phase:document.documentElement.getAttribute('data-workspace-phase'),
                visible:document.getElementById('source-container').checkVisibility({visibilityProperty:true}),
                translationVisible:translation.checkVisibility({visibilityProperty:true}),
                rowsVisible:rows.checkVisibility({visibilityProperty:true}),
                source:source.innerText, translation:translation.innerText, rows:rows.innerText,
                title:document.title, icon:document.querySelector('link[rel="icon"]').getAttribute('href'),
                chip:document.querySelector('.kw-tab-chip.active').getAttribute('data-tab-seq'),
                box:JSON.stringify(source.getBoundingClientRect()),
                translationBox:JSON.stringify(translation.getBoundingClientRect()),
                toolbar:JSON.stringify(document.getElementById('kw-action-toolbar').getBoundingClientRect()),
                card:WorkspaceTabs.getActiveTabSeq()});
            if (values.length === 3) resolve(values); else requestAnimationFrame(sample);
        }
        requestAnimationFrame(sample);
    })''')


@pytest.mark.parametrize('marked_page', [('dark', '100', 'top', False), ('light', '80', 'bottom', False),
    ('dark', '125', 'inline', False), ('light', '125', 'top', False), ('dark', '100', 'top', True)], indirect=True)
def test_reload_never_exposes_provisional_card(marked_page, record_property):
    page, url, server, adapter, path = marked_page
    page.goto(url+'/'); ready(page)
    page.evaluate('WorkspaceTabs.switchToTab(3); flushPendingSaves()')
    baseline = frames(page)
    assert page.locator('.kw-workspace-tab-bar').get_attribute('class').endswith('kw-tab-dock-'+page.locator('#tab-bar-position').text_content().strip())
    held = []
    def hold_once(route):
        if not held:
            held.append(route)
        else:
            route.continue_()
    page.route('**/api/v1/client-view', hold_once)
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('window.__stateReady !== undefined')
    before = frames(page)
    assert all(not frame['visible'] for frame in before), before
    assert all(not frame['translationVisible'] and not frame['rowsVisible'] for frame in before), before
    assert all(frame['title'] == 'Kardenwort - Loading workspace' and frame['icon'] == '/favicon.ico' for frame in before)
    assert page.get_by_role('status', name='Workspace readiness').is_visible()
    held[0].continue_()
    page.unroute('**/api/v1/client-view')
    ready(page)
    after = frames(page)
    assert page.evaluate('WorkspaceTabs.getActiveCard().translated_text') == 'CARD 3 TRANSLATION'
    assert after[0]['translation'] == 'CARD 3 TRANSLATION'
    assert all(frame['visible'] and frame['card'] == 3 for frame in after), after
    assert all(frame['translation'] == 'CARD 3 TRANSLATION' and frame['chip'] == '3' for frame in after), after
    assert all(frame['source'] == 'wortbaaa.' and 'wortaaaa' not in frame['rows'] for frame in after), after
    assert all('card-3.de.tsv' in frame['title'] and frame['icon'].endswith('/3.ico') for frame in after)
    assert len({frame['box'] for frame in after}) == 1
    assert after[0]['box'] == baseline[0]['box']
    assert after[0]['translationBox'] == baseline[0]['translationBox']
    assert after[0]['toolbar'] == baseline[0]['toolbar']
    assert page.locator('#kw-btn-update').is_visible() and page.locator('#kw-btn-update').is_enabled()
    record_property('frames', json.dumps({'baseline':baseline,'before':before,'after':after}))


@pytest.mark.parametrize('failure', ['http', 'revision', 'invalid_card'])
def test_restore_failure_is_explicit_and_retryable(sqlite_page, failure):
    page, url, server, adapter, path = sqlite_page
    status, body, message = {
        'http': (503, {'ok':False,'message':'owned unavailable'}, 'owned unavailable'),
        'revision': (200, {'ok':True,'active_card':1,'active_tokens':[]}, 'Invalid committed view revision'),
        'invalid_card': (200, {'ok':True,'revision':0,'active_card':0,'active_tokens':[]}, 'Invalid committed view state'),
    }[failure]
    page.route('**/api/v1/client-view', lambda route: route.fulfill(status=status,
        content_type='application/json', body=json.dumps(body)))
    page.goto(url+'/')
    expect(page.get_by_role('status', name='Workspace readiness')).to_contain_text(message)
    assert all(not frame['visible'] for frame in frames(page))
    page.unroute('**/api/v1/client-view')
    page.get_by_role('button', name='Retry workspace').click()
    ready(page)
    assert all(frame['visible'] for frame in frames(page))


def test_completion_during_restore_reveals_latest_card_without_view_write(marked_page):
    page, url, server, adapter, path = marked_page
    page.goto(url+'/'); ready(page)
    page.evaluate('WorkspaceTabs.switchToTab(3); flushPendingSaves()')
    script = controlled_event_stream_script().removeprefix('<script>').removesuffix('</script>').replace("!== '/events'", "!== '/api/v1/events'")
    page.add_init_script(script)
    held, writes, stale_status = [], [], []
    def view_boundary(route):
        body = route.request.post_data_json
        if 'expected_revision' in body:
            writes.append(body); route.continue_()
        elif not held:
            held.append(route)
        else:
            route.continue_()
    page.route('**/api/v1/client-view', view_boundary)
    def hold_status(route):
        if not stale_status:
            stale_status.append(route)
            page.evaluate('window.__statusHeld = true')
        else:
            route.continue_()
    page.route('**/session/status?*', hold_status)
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('window.__stateReady !== undefined && window.__sseInstances.length > 0')
    page.wait_for_function("document.getElementById('sse-status-indicator').getAttribute('data-status') === 'connected'")
    page.wait_for_function('window.__statusHeld === true')
    old_response = stale_status[0].fetch()
    # Publish through the real DB before delivering its stream notification.
    adapter.db.insert_sentences([dict(session_zid=ZID, sentence_index=2, sentence_source='wortbaaa.',
        sentence_destination='LATEST CARD 3')])
    page.evaluate('''() => __sseInstances[__sseInstances.length-1].send({stage:'finished',
        sentences:[{sentence_index:2,sentence_destination:'LATEST CARD 3'}],text_provenance:'live:fixture'})''')
    page.wait_for_function("WorkspaceTabs.getCards().find(c=>c.seq_num===3).translated_text === 'LATEST CARD 3'")
    assert stale_status
    stale_status[0].fulfill(response=old_response)
    assert all(not frame['visible'] for frame in frames(page))
    page.keyboard.press('Control+s')
    page.evaluate('saveClientView() && undefined')
    held[0].continue_()
    ready(page)
    assert page.evaluate('WorkspaceTabs.getActiveCard().translated_text') == 'LATEST CARD 3'
    assert all(frame['translation'] == 'LATEST CARD 3' and frame['card'] == 3 for frame in frames(page))
    assert writes == []


def test_disposed_read_cannot_overwrite_resumed_document(marked_page):
    page, url, server, adapter, path = marked_page
    page.add_init_script('''(() => {
        const send = fetch.bind(window); let held = false;
        window.fetch = function(url, options) {
            if (String(url).includes('/api/v1/client-view') && !held) {
                held = true;
                return send(url, {...options, signal:undefined}).then(response => new Promise(resolve => {
                    window.releaseObsoleteView = () => resolve(response);
                }));
            }
            return send(url, options);
        };
    })()''')
    page.goto(url+'/')
    page.wait_for_function('window.releaseObsoleteView !== undefined')
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    assert all(not frame['visible'] for frame in frames(page))
    client = page.url.split('kw-client=')[1]
    assert post(url, '/api/v1/client-view', dict(session_zid=ZID, client_id=client,
        active_card=3, active_tokens=[], expected_revision=0))[0] == 200
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
    ready(page)
    page.evaluate('releaseObsoleteView()')
    assert all(frame['card'] == 3 and frame['translation'] == 'CARD 3 TRANSLATION' for frame in frames(page))


@pytest.mark.parametrize('missing', ['card', 'occurrence', 'visual'])
def test_unavailable_focus_is_reconciled_without_membership_change(marked_page, missing):
    from kardenwort_client_views import ClientViewAdapter
    page, url, server, adapter, path = marked_page
    page.goto(url+'/'); ready(page)
    client = page.url.split('kw-client=')[1]
    focus = dict(sentence_idx=1, token_order=0, visual_idx=999 if missing == 'visual' else 0)
    views = ClientViewAdapter(adapter.db)
    views.set(ZID, client, 99 if missing == 'card' else 2, [focus])
    if missing == 'occurrence':
        with adapter.db.get_connection() as connection:
            connection.execute('UPDATE client_view_state SET active_tokens=? WHERE client_id=?',
                (json.dumps([dict(sentence_idx=1,token_order=999,visual_idx=0)]),client))
    before = adapter.get_shared_selection(ZID)
    page.reload(); ready(page)
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == (1 if missing == 'card' else 2)
    assert page.evaluate('AppState.activeTokenSelections') == []
    saved = views.get(ZID, client)
    assert saved['active_card'] == (1 if missing == 'card' else 2) and saved['active_tokens'] == []
    assert adapter.get_shared_selection(ZID) == before


def test_import_choice_stays_accessible_and_cancels_with_document(marked_page):
    page, url, server, adapter, path = marked_page
    page.add_init_script("localStorage.setItem('kw_highlight_same_lemma','true')")
    page.goto(url+'/')
    dialog = page.get_by_test_id('legacy-state-import')
    expect(dialog).to_be_visible()
    assert all(not frame['visible'] for frame in frames(page))
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    expect(dialog).to_have_count(0)
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") == 'true'
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
    expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Keep database settings').click()
    ready(page)
    assert all(frame['visible'] for frame in frames(page))


def test_back_forward_reuses_committed_client_view(marked_page):
    page, url, server, adapter, path = marked_page
    page.goto(url+'/'); ready(page)
    page.evaluate('WorkspaceTabs.switchToTab(3); flushPendingSaves()')
    identity = page.url
    page.goto(url+'/other')
    page.go_back(); ready(page)
    assert page.url == identity
    assert all(frame['card'] == 3 and frame['translation'] == 'CARD 3 TRANSLATION' for frame in frames(page))


@pytest.mark.parametrize('seq', [None, 3])
def test_controller_launch_and_restored_card_precedence(sqlite_page, seq):
    page, url, server, adapter, path = sqlite_page
    config = server.config
    config.read_dict({'sentences_mode': {'enabled':'true','delivery_mode':'container'},
        'rendering': {'display_mode':'monolithic'}, 'wordfill': {'enabled':'false'},
        'triggers': {'run_text_translation':'manual','run_lemma_base_translation':'manual','run_lemma_enrichment':'manual'}})
    zid, text = '20261114222955', 'Alpha first! Beta second?'
    seed_render_rows(config, server.resolved_paths, zid, text, 'en',
        ['Quotation','WordSource','WordDestination','SentenceSourceIndex','SentenceSource','SentenceDestination'],
        [['Alpha','alpha','uno','1','Alpha first!','FIRST TRANSLATION'],
         ['Beta','beta','dos','2','Beta second?','SECOND TRANSLATION']])
    address = url+'/?session_zid='+zid+('&seq_num='+str(seq) if seq else '')
    page.goto(address); ready(page)
    initial = seq or 2
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == initial
    assert page.locator('#source-container').inner_text().strip() == ('Alpha first!' if initial == 2 else 'Beta second?')
    client = page.url.split('kw-client=')[1]
    with adapter.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM client_view_state WHERE client_id=?',(client,)).fetchone()[0] == 0
    target = 3 if initial == 2 else 2
    page.evaluate('(target) => { WorkspaceTabs.switchToTab(target); return flushPendingSaves(); }',target)
    held = []
    def hold_once(route):
        if not held: held.append(route)
        else: route.continue_()
    page.route('**/api/v1/client-view',hold_once)
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('window.__stateReady !== undefined')
    assert all(not frame['visible'] for frame in frames(page))
    held[0].continue_(); ready(page)
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == target
    assert page.locator('#source-container').inner_text().strip() == ('Alpha first!' if target == 2 else 'Beta second?')


def test_view_read_deadline_offers_retry_without_guessing_a_card(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    page.clock.install()
    held = []
    page.route('**/api/v1/client-view', lambda route: held.append(route))
    page.goto(url+'/')
    page.wait_for_function('window.__stateReady !== undefined')
    page.clock.fast_forward(30001)
    expect(page.get_by_role('status', name='Workspace readiness')).to_contain_text('Workspace read timed out')
    assert all(not frame['visible'] for frame in frames(page))
    held[0].abort()
    page.unroute('**/api/v1/client-view')
    page.get_by_role('button', name='Retry workspace').click()
    ready(page)
    assert page.locator('#source-container').is_visible()


def test_initialization_functions_retain_the_branch_ceiling():
    import ast
    import inspect
    from pathlib import Path
    import re
    from tests.test_preference_js_structure import function_body
    from tests.test_intent_architecture import complexity
    assert complexity(ast.parse(inspect.getsource(desk.format_initial_card_translations)).body[0]) <= 7
    root = Path(__file__).resolve().parents[1]
    sources = {
        'features/workspace-presentation.js': ['readyMetadata','project','isSelectionKey','blockPendingKey','retry','bind','resume','suspend','dispose'],
        'features/sqlite-state.js': ['checkRun','readView','migrate','start','commitView','enqueueView','dispose','present'],
        'app.js': ['initialWorkspaceBusy','applyInitialWorkspaceStatus','probeInitialWorkspace','restoreClientView'],
        'components/WorkspaceTabs.js': ['metadataReady','projectActiveMetadata'],
    }
    for path, names in sources.items():
        source = (root/'web/js'/path).read_text(encoding='utf-8')
        for name in names:
            body = function_body(source,name)
            assert 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?',body)) <= 7, (path,name)


@pytest.mark.parametrize('host', ['browser', 'native'])
def test_single_card_keeps_safe_content_and_metadata_during_focus_restore(sqlite_page, host):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    boot = json.loads(page.locator('#kardenwort-bootstrap').text_content())
    boot['sentence_cards'] = []
    source = page.locator('#source-container').inner_html()
    html = desk.render_declarative_web_shell(boot, source_html=source,
        page_title='Authoritative single-card title', favicon_href='/assets/numbers/3.ico')
    if host == 'native':
        page = page.context.new_page()
        html = html.replace('<head>', '<head><script>window.ahkCall=function(){};</script>')
    else:
        page.context.route(url+'/', lambda route: route.fulfill(content_type='text/html',body=html))
    held, writes = [], []
    gate_phase = 'held'
    def boundary(route):
        if 'expected_revision' in route.request.post_data_json:
            writes.append(route.request.post_data_json); route.continue_()
        elif not held: held.append(route)
        else: route.continue_()
    page.route('**/api/v1/client-view', boundary)
    try:
        if host == 'native': page.set_content(html)
        else: page.goto(url+'/')
        page.wait_for_function('window.__stateReady !== undefined')
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.title() == 'Authoritative single-card title'
        assert page.locator("link[rel='icon']").get_attribute('href') == '/assets/numbers/3.ico'
        assert page.locator('#source-container').is_visible()
        assert page.locator('#translation-container').is_visible()
        assert page.locator('#kw-action-toolbar').is_visible()
        assert page.locator('.kw-lookup-container').evaluate('(element) => element.inert')
        page.keyboard.down('Alt')
        assert page.evaluate("document.body.classList.contains('text-selection-mode-active')")
        page.keyboard.up('Alt')
        page.keyboard.press('Control+s')
        held[0].continue_(); gate_phase = 'released'; ready(page)
        assert not page.locator('.kw-lookup-container').evaluate('(element) => element.inert')
        assert writes == []
    finally:
        if gate_phase == 'held':
            for route in held: route.abort()
        page.unroute_all(behavior='ignoreErrors')
        if host == 'native': page.close()
