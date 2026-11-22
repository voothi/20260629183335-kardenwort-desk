"""Canonical browser import/reload uses real controller and owned SQLite."""
import copy
import json
import re

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_state_http import config_snapshot
from scripts.benchmark_release_interactions import render_fixture, payload, HEADERS, ZID
from tests.test_preference_acknowledgments import preference_page  # noqa: F401
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_release_boundary_contracts import post, data


@pytest.fixture
def sqlite_page(page, preference_world):
    url, server, path = preference_world
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    _, rows = payload(3)
    adapter.save_session(session_zid=ZID, source_language='de', target_language='en',
        source_raw_text=' '.join(row[0] for row in rows), text_mode='single', headers=HEADERS, data_rows=rows)
    html = render_fixture(desk, 3, 'container')
    boot = json.loads(re.search(r'<script[^>]*id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S).group(1))
    boot['server_url'] = url
    boot['session_data'].update(server_url=url, api_token='test-controller-api-key')
    boot['session_data']['source_text'] = ' '.join(row[4] for row in rows)
    boot['token_manifest'] = [dict(visual_idx=i, sentence_idx=i+1, token_order=i,
        atomic_id=i, row_ids=[i], lemma=row[1]) for i, row in enumerate(rows)]
    source_html = ' '.join('<span class="word" data-word="{0}" data-word-idx="{1}" '
        'data-token-order="{1}" data-sentence-idx="{2}">{0}</span>.'.format(row[0], i, i+1)
        for i, row in enumerate(rows))
    for index in range(2):
        child = copy.deepcopy(boot['sentence_cards'][0])
        child.update(seq_num=index+2, sentence_idx=index+1,
                     words=[copy.deepcopy(child['words'][index])], source_text=rows[index][0]+'.')
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
    page.context.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
    page.context.route(url+'/', document)
    yield page, url, server, adapter, path
    page.context.unroute_all(behavior='ignoreErrors')


def ready(page):
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate('window.__stateReady')


def test_browser_import_requires_choice_and_deletes_keys_after_commit(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    before = path.read_bytes(), path.stat().st_mtime_ns
    page.add_init_script("localStorage.setItem('kw_highlight_same_lemma','true')")
    page.goto(url+'/')
    dialog = page.get_by_test_id('legacy-state-import')
    expect(dialog).to_be_visible()
    assert page.evaluate('AppState.highlightSameLemma') is False
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") == 'true'
    dialog.get_by_role('button', name='Import browser settings').click()
    ready(page)
    assert page.evaluate('AppState.highlightSameLemma') is True
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") is None
    assert config_snapshot(server, {})['preferences']['values']['highlight_same_lemma'] is True
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert post(url, '/api/v1/config', {'highlight_same_lemma': False})[0] == 200
    page.reload(); ready(page)
    assert page.evaluate('AppState.highlightSameLemma') is False
    expect(page.get_by_test_id('legacy-state-import')).to_have_count(0)
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") is None


def test_two_windows_reload_their_own_card_and_focus(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    other = page.context.new_page()
    try:
        other.goto(url+'/'); ready(other)
        assert page.url != other.url
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        page.locator('#source-container .word').first.click()
        page.evaluate('flushPendingSaves()')
        other.evaluate('WorkspaceTabs.switchToTab(3)')
        assert other.locator('#source-container .word:visible').count(), other.locator('#source-container').inner_html()
        other.locator('#source-container .word:visible').first.click()
        other.evaluate('flushPendingSaves()')
        left = page.evaluate('AppState.activeTokenSelections')
        right = other.evaluate('AppState.activeTokenSelections')
        assert left and right and left != right
        page.reload(); ready(page)
        other.reload(); ready(other)
        assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == 2
        assert other.evaluate('WorkspaceTabs.getActiveTabSeq()') == 3
        assert page.evaluate('AppState.activeTokenSelections') == left
        assert other.evaluate('AppState.activeTokenSelections') == right
        assert sum(adapter.get_shared_selection(ZID).values()) == 2
        page.evaluate('clearTokenSelections(); WorkspaceTabs.switchToTab(3); WorkspaceTabs.switchToTab(2)')
        page.evaluate('flushPendingSaves()')
        assert page.evaluate('AppState.activeTokenSelections') == []
        assert sum(adapter.get_shared_selection(ZID).values()) == 2
    finally:
        other.close()


def test_new_window_does_not_reuse_a_copied_navigation_handle(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    page.evaluate('WorkspaceTabs.switchToTab(2)')
    page.evaluate('flushPendingSaves()')
    other = page.context.new_page()
    try:
        other.evaluate('name => window.name = name', 'kw-client-'+page.url.split('kw-client=')[1])
        other.goto(page.url); ready(other)
        assert page.url != other.url
        assert other.evaluate('WorkspaceTabs.getActiveTabSeq()') == 1
        page.reload(); ready(page)
        assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == 2
    finally:
        other.close()


@pytest.mark.parametrize('failure', ['transaction', 'conflict', 'lost_acknowledgment'])
def test_browser_import_failure_retains_keys_and_requires_explicit_retry(sqlite_page, failure):
    page, url, server, adapter, path = sqlite_page
    page.add_init_script("localStorage.setItem('kw_filter_selected_only','true')")
    page.goto(url+'/')
    dialog = page.get_by_test_id('legacy-state-import')
    expect(dialog).to_be_visible()
    if failure == 'transaction':
        with adapter.db.get_connection() as connection:
            connection.execute("CREATE TRIGGER reject_browser_import BEFORE INSERT ON browser_state_imports BEGIN SELECT RAISE(ABORT,'owned interruption'); END")
    elif failure == 'conflict':
        assert post(url, '/api/v1/config', {'filter_selected_only': False})[0] == 200
    else:
        held = []
        page.route('**/api/v1/browser-state/import', lambda route: held.append(route))
    with page.expect_request('**/api/v1/browser-state/import'):
        dialog.get_by_role('button', name='Import browser settings').click()
    if failure == 'lost_acknowledgment':
        response = held[0].fetch()
        assert response.status == 200
        assert page.evaluate("localStorage.getItem('kw_filter_selected_only')") == 'true'
        held.pop().fulfill(status=500, content_type='application/json', body='{"ok":false}')
        page.unroute('**/api/v1/browser-state/import')
    expect(dialog).to_contain_text('Import not saved')
    assert page.evaluate("localStorage.getItem('kw_filter_selected_only')") == 'true'
    if failure != 'lost_acknowledgment':
        assert config_snapshot(server, {})['preferences']['values']['filter_selected_only'] is False
        with adapter.db.get_connection(read_only=True) as connection:
            assert connection.execute('SELECT COUNT(*) FROM browser_state_imports').fetchone()[0] == 0
    if failure == 'transaction':
        with adapter.db.get_connection() as connection:
            connection.execute('DROP TRIGGER reject_browser_import')
    choice = 'Keep database settings' if failure == 'conflict' else 'Import browser settings'
    dialog.get_by_role('button', name=choice).click()
    ready(page)
    assert page.evaluate("localStorage.getItem('kw_filter_selected_only')") is None
    assert config_snapshot(server, {})['preferences']['values']['filter_selected_only'] is (failure != 'conflict')


@pytest.mark.parametrize('status', [200, 500, 'malformed'])
def test_explicit_save_waits_for_view_commit_and_reports_failure(sqlite_page, status):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    held = []
    page.route('**/api/v1/client-view', lambda route: held.append(route))
    with page.expect_request('**/api/v1/client-view'):
        page.locator('#source-container .word').first.click()
    page.evaluate('() => { window.__saveFinished = null; onSaveClick().then(value => window.__saveFinished = value); }')
    assert page.evaluate('window.__saveFinished') is None
    route = held.pop(0)
    if status == 200:
        route.fulfill(response=route.fetch())
    elif status == 500:
        route.fulfill(status=500, content_type='application/json', body='{"ok":false}')
    else:
        route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
    page.wait_for_function('window.__saveFinished !== null')
    assert page.evaluate('window.__saveFinished') is (status == 200)
    assert adapter.get_shared_selection(ZID)[rows_lemma()] is (status == 200)
    expect(page.locator('#kw-toast-container')).to_contain_text('saved successfully' if status == 200 else 'Save failed')


def rows_lemma():
    return payload(3)[0][0]


def test_client_slot_uses_available_crypto_in_non_secure_document(page):
    from pathlib import Path
    import re
    page.set_content('<html><body></body></html>')
    assert page.evaluate('typeof crypto.randomUUID') == 'undefined'
    page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'web/js/features/sqlite-state.js'))
    page.evaluate("SqliteState.create({ui:{state_authority:'sqlite'}})")
    first = page.evaluate('location.hash')
    assert re.fullmatch(r'#kw-client=[0-9a-f]{32}',first)
    page.evaluate("SqliteState.create({ui:{state_authority:'sqlite'}})")
    assert page.evaluate('location.hash') != first


@pytest.mark.parametrize('key,button,field,active_class', [
    ('highlight_same_lemma', 'kw-btn-same-lemma', 'highlightSameLemma', 'active'),
    ('filter_selected_only', 'kw-btn-filter-selected', 'filterSelectedOnly', 'btn-filter-active'),
])
def test_409_preference_retry_submits_intended_value_and_resumes_sse(
    preference_page, key, button, field, active_class
):
    from kardenwort_state_runtime import preference_store
    from tests.test_release_state_convergence import observe_preferences, remote_write

    page, path, held, server = preference_page
    observe_preferences(page)
    control = page.locator('#' + button)

    # User toggles false -> true; independent writer advances revision before commit.
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        control.click()
    remote_write(page, key, True, 0)
    remote_write(page, key, False, 1)

    first = held.pop(0)
    first_resp = first.fetch()
    assert first_resp.status == 409
    first.fulfill(response=first_resp)

    expect(control).to_have_attribute('data-preference-state', 'failed')
    assert page.evaluate('field => AppState[field]', field) is True

    # Newer SSE committed revisions arrive while intent is unresolved:
    # metadata advances monotonically without discarding unresolved intent.
    remote_write(page, key, True, 2)
    remote_write(page, key, False, 3)
    expect(control).to_have_attribute('data-preference-state', 'failed')
    assert page.evaluate('field => AppState[field]', field) is True

    # Clicking the failed button retries the user's intended boolean ('true')
    # against the latest SSE committed revision (4), NOT the inverted boolean.
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        control.click()
    expect(control).to_have_attribute('data-preference-state', 'pending')
    retry = held.pop(0)
    assert retry.request.post_data_json['value'] == 'true', retry.request.post_data_json
    assert retry.request.post_data_json['expected_revision'] == 4, retry.request.post_data_json
    retry.fulfill(response=retry.fetch())

    expect(control).to_have_attribute('data-preference-state', 'saved')
    page.evaluate('flushPendingSaves()')
    assert page.evaluate('field => AppState[field]', field) is True
    assert preference_store(server.config, server.resolved_paths).get(key) is True
    assert preference_store(server.config, server.resolved_paths).snapshot()['revisions'][key] == 5

    # Once settled, cross-tab SSE broadcasts converge normally.
    remote_write(page, key, False, 5)
    assert page.evaluate('field => AppState[field]', field) is False
    expect(control).not_to_have_class(re.compile(r'\b' + active_class + r'\b'))

    # A new click after settlement behaves as a normal toggle (false -> true).
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        control.click()
    expect(control).to_have_attribute('data-preference-state', 'pending')
    follow_up = held.pop(0)
    assert follow_up.request.post_data_json['value'] == 'true'
    assert follow_up.request.post_data_json['expected_revision'] == 6
    follow_up.fulfill(response=follow_up.fetch())
    expect(control).to_have_attribute('data-preference-state', 'saved')
    assert preference_store(server.config, server.resolved_paths).get(key) is True


@pytest.mark.parametrize('key,button,field,setter', [
    ('highlight_same_lemma', 'kw-btn-same-lemma', 'highlightSameLemma', 'setHighlightSameLemma'),
    ('filter_selected_only', 'kw-btn-filter-selected', 'filterSelectedOnly', 'setFilterSelectedOnly'),
])
def test_409_explicit_reconciliation_and_newer_generation_isolation(
    preference_page, key, button, field, setter
):
    from kardenwort_state_runtime import preference_store
    from tests.test_release_state_convergence import observe_preferences, remote_write

    page, path, held, server = preference_page
    observe_preferences(page)
    control = page.locator('#' + button)

    # Part A: older 409 response must never clobber a newer in-flight generation.
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        control.click()  # gen 1: true
    expect(control).to_have_attribute('data-preference-state', 'pending')
    remote_write(page, key, True, 0)
    remote_write(page, key, False, 1)
    control.click()  # gen 2: false (queued while gen 1 is in flight)
    assert len(held) == 1

    gen1 = held.pop(0)
    gen1_resp = gen1.fetch()
    assert gen1_resp.status == 409
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        gen1.fulfill(response=gen1_resp)

    expect(control).to_have_attribute('data-preference-state', 'pending')
    assert page.evaluate('field => AppState[field]', field) is False
    gen2 = held.pop(0)
    assert gen2.request.post_data_json['value'] == 'false'
    assert gen2.request.post_data_json['expected_revision'] == 2
    gen2.fulfill(response=gen2.fetch())
    expect(control).to_have_attribute('data-preference-state', 'saved')
    assert preference_store(server.config, server.resolved_paths).snapshot()['revisions'][key] == 3

    # Part B: explicit reconciliation to committed value settles intent and resumes SSE without retry loop.
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        control.click()  # gen 3: true against rev 3
    expect(control).to_have_attribute('data-preference-state', 'pending')
    remote_write(page, key, True, 3)
    remote_write(page, key, False, 4)
    gen3 = held.pop(0)
    gen3_resp = gen3.fetch()
    assert gen3_resp.status == 409
    gen3.fulfill(response=gen3_resp)
    expect(control).to_have_attribute('data-preference-state', 'failed')
    assert page.evaluate('field => AppState[field]', field) is True

    # SSE arrives while unresolved (rev 6 = false): metadata advances, unresolved intent retained.
    remote_write(page, key, False, 5)
    expect(control).to_have_attribute('data-preference-state', 'failed')
    assert page.evaluate('field => AppState[field]', field) is True

    # User explicitly chooses committed value (false): settles without automatic retry loop.
    with page.expect_request(lambda req: req.url.endswith('/api/v1/config') and req.method == 'POST'):
        page.evaluate('setter => window[setter](false)', setter)
    expect(control).to_have_attribute('data-preference-state', 'pending')
    reconcile = held.pop(0)
    assert reconcile.request.post_data_json['value'] == 'false'
    assert reconcile.request.post_data_json['expected_revision'] == 6
    reconcile.fulfill(response=reconcile.fetch())
    expect(control).to_have_attribute('data-preference-state', 'saved')
    page.evaluate('flushPendingSaves()')
    assert held == []
    assert page.evaluate('field => AppState[field]', field) is False

    # Subsequent SSE broadcast projects normally now that intent is settled.
    remote_write(page, key, True, 7)
    assert page.evaluate('field => AppState[field]', field) is True

