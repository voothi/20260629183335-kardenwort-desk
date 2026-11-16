"""Compatibility view support remains independent of SQLite preferences."""
import json
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import expect

from tests.reword_contract_support import world, backend, provider_name, ZID, TOKEN  # noqa: F401
from tests.test_reword_desktop_persistence import open_generated_desktop
from tests.sqlite_document_support import wait_state
from kardenwort_state_runtime import preference_store


pytestmark = pytest.mark.parametrize('backend', ['tsv'])


def prime_sqlite_preferences(world):
    url = f'http://127.0.0.1:{world.port}'
    with urlopen(Request(url+'/api/v1/config', headers={'X-API-Token': TOKEN}), timeout=10) as response:
        assert response.status == 200
    return url


def test_tsv_navigation_keeps_sqlite_preferences_without_client_views(page, world):
    prime_sqlite_preferences(world)
    before = world.path(ZID).read_bytes()
    calls = []
    page.on('request', lambda request: calls.append(request.post_data_json)
            if request.url.endswith('/api/v1/client-view') else None)
    page.add_init_script("sessionStorage.setItem('kw_active_card_"+ZID+"','3')")
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    wait_state(page)
    boot = json.loads(page.locator('#kardenwort-bootstrap').text_content())
    assert boot['ui_config']['state_authority'] == 'sqlite'
    assert boot['ui_config']['client_view_enabled'] is False
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == 2
    assert page.locator('#source-container').is_visible()
    with page.expect_response(lambda response: response.url.endswith('/api/v1/config') and response.request.method == 'POST') as saved:
        page.locator('#kw-btn-same-lemma').click()
    assert saved.value.ok
    page.evaluate('flushPendingSaves()')
    store = preference_store(world.config, world.paths)
    assert store.snapshot()['values']['highlight_same_lemma'] is True
    assert world.path(ZID).read_bytes() == before
    with store.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM sessions WHERE zid=?', (ZID,)).fetchone()[0] == 0
    page.reload(); wait_state(page)
    assert page.evaluate('AppState.highlightSameLemma') is True
    page.evaluate('WorkspaceTabs.switchToTab(3)')
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == 3
    assert page.locator('#source-container').inner_text().strip() == 'Baum.'
    assert page.evaluate("sessionStorage.getItem('kw_active_card_"+ZID+"')") == '3'
    assert calls == []
    assert errors == []


def test_viewless_compatibility_still_requires_typed_preference_ack(page, world):
    prime_sqlite_preferences(world)
    calls = []
    page.on('request', lambda request: calls.append(request.post_data_json)
            if request.url.endswith('/api/v1/client-view') else None)
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    wait_state(page)
    def malformed_ack(route):
        if route.request.method != 'POST':
            route.continue_(); return
        response = route.fetch()
        payload = response.json()
        payload.get('data', payload).pop('revision')
        route.fulfill(response=response, json=payload)
    page.route('**/api/v1/config', malformed_ack)
    button = page.locator('#kw-btn-same-lemma')
    button.click()
    expect(button).to_have_attribute('data-preference-state', 'failed')
    assert preference_store(world.config, world.paths).snapshot()['values']['highlight_same_lemma'] is True
    page.unroute('**/api/v1/config')
    button.click()
    expect(button).to_have_attribute('data-preference-state', 'saved')
    assert preference_store(world.config, world.paths).snapshot()['values']['highlight_same_lemma'] is False
    assert calls == []
    assert errors == []
