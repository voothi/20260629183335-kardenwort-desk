"""Release boundary contracts use real SQLite, controller, and browser projections."""
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from kardenwort_client_views import ClientViewAdapter
from kardenwort_state_runtime import preference_store
from scripts.benchmark_release_interactions import ZID
from tests.test_preference_commands import preference_world, command  # noqa: F401
from tests.test_preference_acknowledgments import preference_page  # noqa: F401
from tests.test_sqlite_state_browser import sqlite_page, ready  # noqa: F401


TOGGLES = [
    ('highlight_same_lemma', 'kw-btn-same-lemma', 'highlightSameLemma'),
    ('filter_selected_only', 'kw-btn-filter-selected', 'filterSelectedOnly'),
]


def observe_preferences(page):
    page.evaluate('''() => {
        const receive = window.receivePreferenceSnapshot;
        window.preferenceDeliveries = [];
        window.receivePreferenceSnapshot = snapshot => {
            const result = receive(snapshot);
            preferenceDeliveries.push(snapshot);
            return result;
        };
    }''')


def wait_revision(page, key, revision):
    page.wait_for_function('''([key, revision]) => preferenceDeliveries.some(
        snapshot => snapshot.revisions[key] === revision)''', arg=[key, revision])


def origin(page):
    address = urlsplit(page.url)
    return address.scheme+'://'+address.netloc


def remote_write(page, key, value, revision):
    status, result = command(origin(page), dict(section='rendering', key=key,
        value=value, expected_revision=revision))
    assert status == 200, result
    wait_revision(page, key, result['revision'])
    return result['revision']


@pytest.mark.parametrize('key,button,field', TOGGLES)
def test_newer_committed_sse_survives_older_ack(preference_page, key, button, field):
    page, path, held, server = preference_page
    observe_preferences(page)
    control = page.locator('#'+button)
    before = control.bounding_box(), path.read_bytes()
    with page.expect_request('**/api/v1/config'):
        control.click()
    route = held.pop(0)
    response = route.fetch()
    first = response.json()['data'] if 'data' in response.json() else response.json()
    wait_revision(page, key, first['revision'])
    status, second = command(origin(page),
        dict(section='rendering', key=key, value=False, expected_revision=first['revision']))
    assert status == 200, second
    wait_revision(page, key, second['revision'])
    assert page.evaluate('field => AppState[field]', field) is True
    expect(control).to_have_attribute('data-preference-state', 'pending')
    route.fulfill(response=response)
    expect(control).to_have_attribute('data-preference-state', 'saved')
    assert page.evaluate('field => AppState[field]', field) is False
    expect(control).not_to_have_class(re.compile(r'\bactive\b'))
    assert preference_store(server.config, server.resolved_paths).get(key) is False
    with page.expect_request('**/api/v1/config'):
        control.click()
    following = held.pop(0)
    assert following.request.post_data_json['expected_revision'] == second['revision']
    following.fulfill(response=following.fetch())
    expect(control).to_have_attribute('data-preference-state', 'saved')
    page.evaluate('flushPendingSaves()')
    assert control.bounding_box() == before[0]
    assert path.read_bytes() == before[1]
    assert preference_store(server.config, server.resolved_paths).snapshot()['revisions'][key] == second['revision']+1


@pytest.mark.parametrize('first_status', [200, 500])
@pytest.mark.parametrize('key,button,field', TOGGLES)
def test_generations_flush_and_independent_keys_keep_latest_commit(preference_page, first_status, key, button, field):
    page, path, held, server = preference_page
    observe_preferences(page)
    control = page.locator('#'+button)
    with page.expect_request('**/api/v1/config'):
        control.click()
    control.click()
    assert len(held) == 1
    first = held.pop(0)
    first_response = first.fetch()
    wait_revision(page, key, 1)
    remote_write(page, key, False, 1)
    other_key, other_button, other_field = next(item for item in TOGGLES if item[0] != key)
    with page.expect_request('**/api/v1/config'):
        page.locator('#'+other_button).click()
    other = held.pop(0)
    other.fulfill(response=other.fetch())
    expect(page.locator('#'+other_button)).to_have_attribute('data-preference-state', 'saved')
    assert page.evaluate('field => AppState[field]', other_field) is True
    assert page.evaluate('field => AppState[field]', field) is False
    page.evaluate('''() => {
        window.flushOutcome = 'pending';
        flushPendingSaves().then(() => flushOutcome = 'saved', () => flushOutcome = 'failed');
    }''')
    assert page.evaluate('flushOutcome') == 'pending'
    with page.expect_request(lambda request: request.url.endswith('/api/v1/config') and request.method == 'POST'):
        first.fulfill(response=first_response, status=first_status)
    expect(control).to_have_attribute('data-preference-state', 'pending')
    assert page.evaluate('field => AppState[field]', field) is False
    second = held.pop(0)
    assert second.request.post_data_json['expected_revision'] == 2
    response = second.fetch()
    wait_revision(page, key, 3)
    remote_write(page, key, True, 3)
    assert page.evaluate('field => AppState[field]', field) is False
    second.fulfill(response=response)
    expect(control).to_have_attribute('data-preference-state', 'saved')
    page.wait_for_function("flushOutcome === 'saved'")
    assert page.evaluate('field => AppState[field]', field) is True
    assert page.evaluate('field => AppState[field]', other_field) is True
    assert held == []
    snapshot = preference_store(server.config, server.resolved_paths).snapshot()
    assert snapshot['revisions'][key] == 4 and snapshot['revisions'][other_key] == 1


@pytest.mark.parametrize('key,button,field', TOGGLES)
def test_current_conflict_keeps_unsaved_intent_until_explicit_retry(preference_page, key, button, field):
    page, path, held, server = preference_page
    observe_preferences(page)
    control = page.locator('#'+button)
    with page.expect_request('**/api/v1/config'):
        control.click()
    remote_write(page, key, True, 0)
    remote_write(page, key, False, 1)
    route = held.pop(0)
    response = route.fetch()
    assert response.status == 409
    route.fulfill(response=response)
    expect(control).to_have_attribute('data-preference-state', 'failed')
    assert page.evaluate('field => AppState[field]', field) is True
    assert page.evaluate("flushPendingSaves().then(() => 'saved', error => error.message)") == 'Preferences remain unsaved'
    assert preference_store(server.config, server.resolved_paths).get(key) is False
    with page.expect_request('**/api/v1/config'):
        control.click()
    retry = held.pop(0)
    assert retry.request.post_data_json['expected_revision'] == 2
    retry.fulfill(response=retry.fetch())
    expect(control).to_have_attribute('data-preference-state', 'saved')
    page.evaluate('flushPendingSaves()')
    assert page.evaluate('field => AppState[field]', field) is False
    assert preference_store(server.config, server.resolved_paths).snapshot()['revisions'][key] == 3


def test_duplicate_lower_and_conflicting_equal_snapshots(preference_page):
    page, path, held, server = preference_page
    observe_preferences(page)
    key = 'highlight_same_lemma'
    remote_write(page, key, True, 0)
    older = preference_store(server.config, server.resolved_paths).snapshot()
    remote_write(page, key, False, 1)
    latest = preference_store(server.config, server.resolved_paths).snapshot()
    page.evaluate('snapshot => { receivePreferenceSnapshot(snapshot); receivePreferenceSnapshot(snapshot); }', latest)
    page.evaluate('snapshot => receivePreferenceSnapshot(snapshot)', older)
    assert page.evaluate('AppState.highlightSameLemma') is False
    conflicting = dict(values={key: True}, revisions={key: 2})
    assert page.evaluate('''snapshot => {
        try { receivePreferenceSnapshot(snapshot); return 'accepted'; }
        catch (error) { return error.message; }
    }''', conflicting) == 'Conflicting committed preference revision'
    assert page.evaluate('AppState.highlightSameLemma') is False
    assert held == []
    assert preference_store(server.config, server.resolved_paths).snapshot() == latest


def focus_projection(page):
    return page.evaluate(r'''() => ({tokens: AppState.activeTokenSelections,
        active: [...document.querySelectorAll('#source-container .word, #translation-container .word, #lemma-table tr')]
            .filter(el => /(?:active-subtoken|state-\S*active|active-token)/.test(el.className))
            .map(el => el.outerHTML)})''')


@pytest.mark.parametrize('lifecycle', ['resume', 'retry'])
@pytest.mark.parametrize('cleared', ['empty', 'removed', 'card'])
def test_committed_empty_focus_replaces_suspended_focus(sqlite_page, lifecycle, cleared):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    client = page.url.split('kw-client=')[1]
    views = ClientViewAdapter(adapter.db)
    other = 'a'*32
    views.set(ZID, other, 3, [])
    page.locator('#source-container .word').first.click()
    page.evaluate('flushPendingSaves()')
    before = focus_projection(page)
    assert len(before['tokens']) == 1
    membership = adapter.get_shared_selection(ZID)
    other_view = views.get(ZID, other)
    original = path.read_bytes()
    writes = []
    page.on('request', lambda request: writes.append(request.post_data_json)
        if request.url.endswith('/api/v1/client-view') and 'expected_revision' in request.post_data_json else None)
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
    if cleared == 'empty':
        views.set(ZID, client, 1, [])
    elif cleared == 'removed':
        token = before['tokens'][0]
        with adapter.db.get_connection() as connection:
            connection.execute('DELETE FROM words WHERE session_zid=? AND sentence_index=? AND token_order=?',
                (ZID, token['sentence_idx'], token['token_order']))
        membership = adapter.get_shared_selection(ZID)
    else:
        views.set(ZID, client, 99, before['tokens'])
    if lifecycle == 'retry':
        page.route('**/api/v1/client-view', lambda route: route.fulfill(
            status=500, content_type='application/json', body='{"ok":false,"message":"owned read failure"}'))
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        expect(page.locator('html')).to_have_attribute('data-workspace-phase', 'failed')
        page.unroute('**/api/v1/client-view')
    page.evaluate('''() => {
        window.readyFocus = [];
        new MutationObserver(records => {
            if (document.documentElement.dataset.workspacePhase === 'ready') readyFocus.push({
                tokens: JSON.parse(JSON.stringify(AppState.activeTokenSelections)),
                active: document.querySelectorAll('#source-container .active-subtoken, #source-container [class*="state-"][class*="active"]').length
            });
        }).observe(document.documentElement, {attributes:true, attributeFilter:['data-workspace-phase']});
    }''')
    if lifecycle == 'retry':
        page.get_by_role('button', name='Retry workspace').click()
    else:
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    ready(page)
    assert focus_projection(page) == dict(tokens=[], active=[])
    assert page.evaluate('readyFocus') and all(item == dict(tokens=[], active=0) for item in page.evaluate('readyFocus'))
    saved = views.get(ZID, client)
    assert saved['active_tokens'] == [] and saved['active_card'] == 1
    assert len(writes) == int(cleared == 'card')
    assert adapter.get_shared_selection(ZID) == membership
    assert views.get(ZID, other) == other_view
    assert path.read_bytes() == original


def test_rebind_without_restore_keeps_focus_without_synthesizing_membership(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    page.locator('#source-container .word').first.click()
    page.evaluate('flushPendingSaves()')
    before = focus_projection(page)['tokens']
    assert before
    membership = adapter.get_shared_selection(ZID)
    page.evaluate('rebindTableRows(); WorkspaceTabs.switchToTab(2); WorkspaceTabs.switchToTab(1); flushPendingSaves()')
    assert focus_projection(page)['tokens'] == before
    assert adapter.get_shared_selection(ZID) == membership


def test_nonempty_restored_focus_does_not_create_shared_membership(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    page.goto(url+'/'); ready(page)
    client = page.url.split('kw-client=')[1]
    views = ClientViewAdapter(adapter.db)
    focus = dict(sentence_idx=1, token_order=0, visual_idx=0)
    views.set(ZID, client, 1, [focus])
    membership = adapter.get_shared_selection(ZID)
    assert not any(membership.values())
    page.reload(); ready(page)
    tokens = focus_projection(page)['tokens']
    assert len(tokens) == 1 and all(tokens[0][key] == value for key, value in focus.items())
    # Existing nonempty restoration retains local pending selection intent.
    assert page.evaluate('isDirty()') is True
    assert adapter.get_shared_selection(ZID) == membership
    assert views.get(ZID, client)['active_tokens'] == [focus]


def test_release_boundary_functions_keep_branch_ceiling():
    from pathlib import Path
    from tests.test_preference_js_structure import function_body
    root = Path(__file__).resolve().parents[1]/'web/js'
    for path, names in [('features/sqlite-state.js', ['acceptPreference', 'projectPreference']),
                        ('app.js', ['restoreActiveTokenFocus', 'restorePendingTokenSelection',
                                    'markRestoredSelection', 'projectPreferenceStatus'])]:
        source = (root/path).read_text(encoding='utf-8')
        for name in names:
            body = function_body(source, name)
            assert 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', body)) <= 7, name


def test_wrapped_focus_resume_preserves_text_geometry(sqlite_page):
    page, url, server, adapter, path = sqlite_page
    page.set_viewport_size(dict(width=180, height=900))
    page.goto(url+'/'); ready(page)
    page.locator('#source-container .word').first.click()
    page.evaluate('flushPendingSaves()')
    geometry = '''() => {
        const rect = box => [box.x, box.y, box.width, box.height];
        return ['source-container', 'translation-container'].map(id => {
            const element = document.getElementById(id), range = document.createRange();
            range.selectNodeContents(element);
            return {box: rect(element.getBoundingClientRect()),
                lines: [...range.getClientRects()].map(rect),
                words: [...element.querySelectorAll('.word')].map(word => rect(word.getBoundingClientRect()))};
        });
    }'''
    before = page.evaluate(geometry)
    assert len({word[1] for word in before[0]['words']}) > 1, before
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
    client = page.url.split('kw-client=')[1]
    ClientViewAdapter(adapter.db).set(ZID, client, 1, [])
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    ready(page)
    assert page.evaluate('AppState.activeTokenSelections') == []
    assert page.evaluate(geometry) == before

