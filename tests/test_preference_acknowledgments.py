"""Hold only transport acknowledgments; controller and persistence remain real."""
import json
import re
import sqlite3

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture, HEADERS, payload, ZID
from kardenwort_state_http import config_snapshot
from kardenwort_state_runtime import preference_store
from tests.test_preference_commands import preference_world  # noqa: F401


@pytest.fixture
def preference_page(page, preference_world):
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
    boot['ui_config'].update(config_snapshot(server, {})['ui'])
    page.context.set_extra_http_headers({'X-API-Token': 'test-controller-api-key'})
    page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=desk.render_declarative_web_shell(boot)))
    page.goto(url+'/')
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate('window.__stateReady')
    held = []
    page.route('**/api/v1/config', lambda route: held.append(route) if route.request.method == 'POST' else route.continue_())
    try:
        yield page, path, held, server
    finally:
        while held:
            held.pop().abort()
        page.unroute_all(behavior='ignoreErrors')


@pytest.mark.parametrize('first_status', [200, 500])
@pytest.mark.parametrize('key,button,field', [
    ('highlight_same_lemma', 'kw-btn-same-lemma', 'highlightSameLemma'),
    ('filter_selected_only', 'kw-btn-filter-selected', 'filterSelectedOnly'),
])
def test_old_acknowledgment_cannot_settle_newer_toggle(preference_page, first_status, key, button, field):
    page, path, held, server = preference_page
    control = page.locator('#'+button)
    before = control.bounding_box()
    with page.expect_request(lambda request: request.url.endswith('/api/v1/config') and request.method == 'POST'):
        control.click()
    expect(control).to_have_attribute('data-preference-state', 'pending')
    assert control.bounding_box() == before
    control.click()
    assert page.evaluate('field => AppState[field]', field) is False
    assert len(held) == 1  # Same-key writes must commit in intent order.
    response = held[0].fetch()
    assert response.status == 200
    with page.expect_request(lambda request: request.url.endswith('/api/v1/config') and request.method == 'POST'):
        held.pop(0).fulfill(response=response, status=first_status)
    expect(control).to_have_attribute('data-preference-state', 'pending')
    assert page.evaluate('field => AppState[field]', field) is False
    route = held.pop(0)
    route.fulfill(response=route.fetch())
    expect(control).to_have_attribute('data-preference-state', 'saved')
    assert control.bounding_box() == before
    assert page.evaluate('key => localStorage.getItem("kw_"+key)', key) is None
    assert preference_store(server.config, server.resolved_paths).get(key) is False
    assert (key+' = false').encode() in path.read_bytes()


@pytest.mark.parametrize('mode', ['server_failure', 'malformed_success', 'malformed_committed_ack', 'network_failure'])
def test_current_failure_keeps_unsaved_intent_visible(preference_page, mode):
    page, path, held, server = preference_page
    original = path.read_bytes()
    control = page.locator('#kw-btn-same-lemma')
    with page.expect_request(lambda request: request.url.endswith('/api/v1/config') and request.method == 'POST'):
        control.click()
    if mode == 'server_failure':
        store = preference_store(server.config, server.resolved_paths)
        store.db.busy_timeout_ms = 10
        blocker = sqlite3.connect(store.db.db_path, isolation_level=None)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            response = held[0].fetch()
            assert response.status == 500
            held.pop(0).fulfill(response=response)
        finally:
            blocker.rollback(); blocker.close()
    elif mode == 'malformed_success':
        held.pop(0).fulfill(status=200, content_type='application/json', body='{"ok":false}')
    elif mode == 'malformed_committed_ack':
        held.pop(0).fulfill(status=200, content_type='application/json', body='{"ok":true}')
    else:
        held.pop(0).abort('failed')
    expect(control).to_have_attribute('data-preference-state', 'failed')
    expect(control).to_have_attribute('title', re.compile('not saved'))
    assert page.evaluate('AppState.highlightSameLemma') is True
    assert page.evaluate('localStorage.getItem("kw_highlight_same_lemma")') is None
    expect(page.locator('#kw-toast-container')).to_contain_text('not saved')
    assert path.read_bytes() == original
