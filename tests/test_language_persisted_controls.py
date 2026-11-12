"""Language confirmation starts from a real rejected draft request."""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_state_runtime import preference_store
from tests.controller_test_support import controller_world
from tests.test_release_boundary_contracts import post
from tests.test_preference_js_structure import function_body

ZID = '20261112090001'
TEXT = 'Das ist ein deutsches Haus mit einem Garten und einer grossen alten Eiche.'


@pytest.fixture
def language_draft(page, controller_world):
    url, server = controller_world
    server.config.read_dict({
        'settings': {'default_language': 'en', 'default_target_language': 'de'},
        'language_check': {'enabled': 'true', 'languages': 'en, de', 'min_char_length': '4',
                           'confidence_threshold': '0.6', 'action_on_mismatch': 'prompt'},
        'wordfill': {'enabled': 'false'},
        'audio': {'lmb_play': 'false', 'rmb_play': 'false'},
        'sentences_mode': {'enabled': 'true', 'delivery_mode': 'container'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'},
    })
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    adapter.db.run_migrations()
    store = preference_store(server.config, server.resolved_paths)
    status, rejection = post(url, '/api/v1/render', dict(session_zid=ZID, text=TEXT, language='en'), token=server.api_key)
    assert status == 422, rejection
    assert rejection['error_code'] == 'LANGUAGE_MISMATCH'
    page.context.set_extra_http_headers({'X-API-Token': server.api_key})
    page.goto(url+'/session/render?session_zid='+ZID)
    expect(page.locator('#kw-lang-modal')).to_be_visible()
    try:
        yield page, server, adapter, store
    finally:
        page.context.close()


@pytest.mark.parametrize('choice', ['yes', 'no', 'cancel'])
@pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)
def test_language_choice_has_real_persisted_effect(language_draft, choice):
    page, server, adapter, store = language_draft
    before = adapter.db.get_session_bundle(ZID)
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    if choice == 'cancel':
        page.locator('#kw-btn-lang-cancel').click()
        expect(page.locator('#kw-lang-modal')).to_be_hidden()
        assert store.get('default_language') == 'en'
        assert adapter.db.get_session_bundle(ZID) == before
        assert not any('/api/v1/render' in url or '/api/v1/set-language' in url for url in requests)
        return
    with page.expect_response(lambda response: '/api/v1/render' in response.url) as rendered:
        page.locator('#kw-btn-lang-'+choice).click()
    assert rendered.value.status == 200, rendered.value.text()
    expected = 'de' if choice == 'yes' else 'en'
    assert rendered.value.request.post_data_json['language'] == expected
    page.wait_for_function('''() => {
        const node = document.getElementById('kardenwort-bootstrap');
        if (!node) return false;
        const boot = JSON.parse(node.textContent);
        return !boot.mismatch_info || !boot.mismatch_info.is_mismatch;
    }''')
    expect(page.locator('#source-container')).to_contain_text('Haus')
    expect(page.locator('#kw-lang-modal')).to_be_hidden()
    assert store.get('default_language') == expected
    bundle = adapter.db.get_session_bundle(ZID)
    assert bundle['session']['source_language'] == expected
    assert bundle['session']['source_raw_text'] == TEXT
    assert bundle['words']
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('#source-container')).to_contain_text('Haus')
    assert adapter.db.get_session_bundle(ZID)['session']['source_language'] == expected


@pytest.mark.parametrize('failure', ['http', 'malformed', 'network', 'busy'])
@pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)
def test_failed_language_preference_does_not_start_render(language_draft, failure):
    page, server, adapter, store = language_draft
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    def reject_commit(route):
        if failure == 'network':
            route.abort()
        else:
            route.fulfill(status=503 if failure == 'http' else 200,
                json={'message': 'Owned acknowledgment failure', 'ok': True})
    blocker = None
    if failure == 'busy':
        store.db.busy_timeout_ms = 10
        blocker = sqlite3.connect(store.db.db_path, isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
    else:
        page.route('**/api/v1/set-language', reject_commit)
    try:
        page.locator('#kw-btn-lang-yes').click()
        expect(page.locator('#kw-toast-container')).to_contain_text('Language switch failed')
        expect(page.locator('#kw-lang-modal')).to_be_visible()
        assert not any('/api/v1/render' in url for url in requests)
    finally:
        if blocker is not None:
            blocker.rollback()
            blocker.close()
    assert store.get('default_language') == 'en'
    expect(page.locator('#kw-btn-lang-yes')).to_be_enabled()
    page.unroute('**/api/v1/set-language')
    with page.expect_response(lambda response: '/api/v1/render' in response.url) as retried:
        page.locator('#kw-btn-lang-yes').click()
    assert retried.value.status == 200, retried.value.text()
    page.wait_for_function('''() => {
        const node = document.getElementById('kardenwort-bootstrap');
        if (!node) return false;
        const boot = JSON.parse(node.textContent);
        return !boot.mismatch_info || !boot.mismatch_info.is_mismatch;
    }''')
    expect(page.locator('#source-container')).to_contain_text('Haus')
    assert store.get('default_language') == 'de'
    assert adapter.db.get_session_bundle(ZID)['session']['source_language'] == 'de'


@pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)
def test_unconfirmed_language_draft_has_no_missing_sqlite_view_failure(language_draft):
    page, server, adapter, store = language_draft
    result = page.evaluate('window.__stateReady.then(() => ({ready:true}), error => ({ready:false,message:error.message}))')
    assert result == {'ready': True}
    expect(page.locator('#kw-toast-container')).not_to_contain_text('View state not saved')
    assert adapter.db.get_session_bundle(ZID) is None


def test_language_commit_command_branch_upper_bound():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]/'web/js/app.js').read_text(encoding='utf-8')
    counts = {name: 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', function_body(source, name)))
              for name in ['isCommittedLanguage', 'requireLanguageCommit', 'renderWithCommittedLanguage']}
    assert max(counts.values()) <= 7, counts
