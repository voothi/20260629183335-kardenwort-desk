"""Retry actions cross the real browser/controller/provider/SQLite boundary."""
import pytest
from playwright.sync_api import expect

from tests.controller_test_support import controller_world, DeterministicTranslationHandler
from tests.test_daily_control_persistence import daily_page
from tests.test_reliability_action_matrix import flush
from tests.sqlite_document_support import wait_state
from tests.reword_contract_support import ZID


@pytest.mark.parametrize('action', ['row', 'session'])
@pytest.mark.parametrize('failure', [False, True], ids=['success', 'engine-failure-and-retry'])
def test_retry_commits_only_targeted_unresolved_occurrence(daily_page, monkeypatch, action, failure):
    page, server, adapter = daily_page
    server.config.set('pipeline', 'provider_cooldown_seconds', '0')
    cell = page.locator('#lemma-table td[data-col="WordDestination"]')
    cell.dblclick()
    editor = cell.locator('input.cell-editor')
    editor.fill('')
    editor.press('Enter')
    with page.expect_response('**/session/save') as cleared:
        page.locator('#kw-btn-save').click()
    assert cleared.value.ok, cleared.value.text()
    flush(page)
    before = adapter.db.get_session_bundle(ZID)
    assert before['words'][0]['word_destination'] == ''

    def invoke():
        with page.expect_response('**/session/retry') as response:
            if action == 'row':
                page.evaluate('retryRow(0, null)')
            else:
                page.evaluate('zid => retrySession(zid)', ZID)
        return response.value

    if failure:
        def unavailable(handler, provider, text, source, target):
            raise RuntimeError('Owned engine failure')
        with monkeypatch.context() as external:
            external.setattr(DeterministicTranslationHandler, 'translate', unavailable)
            failed = invoke()
            assert failed.status == 500, failed.text()
            expect(page.locator('#kw-toast-container')).to_contain_text('Retry failed')
            assert adapter.db.get_session_bundle(ZID) == before
    response = invoke()
    assert response.status == 200, response.text()
    words = adapter.db.get_words_by_session(ZID)
    assert words[0]['word_destination'] == 'google: Haus'
    assert words[0]['word_provenance'] == 'live:google'
    assert words[1] == before['words'][1]
    expect(cell.locator('.scrollable-cell')).to_have_text('google: Haus')
    page.reload()
    wait_state(page)
    expect(cell.locator('.scrollable-cell')).to_have_text('google: Haus')
    assert adapter.db.get_words_by_session(ZID) == words


def test_row_retry_second_sentence_keeps_duplicate_local_token_separate(daily_page):
    page, server, adapter = daily_page
    page.evaluate('WorkspaceTabs.switchToTab(3)')
    row = page.locator('#lemma-table tbody tr[data-row-id]')
    row_id = row.get_attribute('data-row-id')
    before = adapter.db.get_words_by_session(ZID)
    assert [(word['sentence_index'], word['token_order']) for word in before] == [(1, 0), (2, 0)]
    with page.expect_response('**/session/retry') as response:
        page.evaluate('row => retryRow(row, null)', row_id)
    assert response.value.status == 200, response.value.text()
    words = adapter.db.get_words_by_session(ZID)
    assert words[0] == before[0]
    assert words[1]['word_destination'] == 'google: Baum'
    assert words[1]['word_provenance'] == 'live:google'
    payload = response.value.json()
    result = payload.get('data', payload)
    assert result['rows']['0'].get('provenance') == before[0]['word_provenance']
    expect(row.locator('td[data-col="WordDestination"] .scrollable-cell')).to_have_text('google: Baum')
    page.reload()
    wait_state(page)
    expect(row.locator('td[data-col="WordDestination"] .scrollable-cell')).to_have_text('google: Baum')
    assert adapter.db.get_words_by_session(ZID) == words


@pytest.mark.parametrize('manifest', ['[]', 'invalid-json', '[{"sentence_idx":0,"token_order":0}]'])
def test_row_retry_invalid_manifest_has_no_request_or_storage_effect(daily_page, manifest):
    page, server, adapter = daily_page
    flush(page)
    before = adapter.db.get_session_bundle(ZID)
    calls = []
    page.on('request', lambda request: calls.append(request.url) if '/session/retry' in request.url else None)
    row = page.locator('#lemma-table tbody tr[data-row-id]')
    row.evaluate('(row, value) => row.setAttribute("data-occurrence-targets", value)', manifest)
    page.evaluate('row => retryRow(row, null)', row.get_attribute('data-row-id'))
    expect(page.locator('#kw-toast-container')).to_contain_text('Retry unavailable')
    assert calls == []
    assert adapter.db.get_session_bundle(ZID) == before
    expect(row.locator('td[data-col="WordDestination"] .scrollable-cell')).to_have_text('house')


@pytest.mark.parametrize('daily_page', ['repeated-lemma'], indirect=True)
def test_row_retry_merged_group_updates_both_declared_occurrences(daily_page):
    page, server, adapter = daily_page
    page.evaluate('WorkspaceTabs.switchToTab(1)')
    flush(page)
    row = page.locator('#lemma-table tbody tr[data-row-id]')
    expect(row).to_have_count(1)
    before = adapter.db.get_words_by_session(ZID)
    with page.expect_response('**/session/retry') as response:
        page.evaluate('row => retryRow(row, null)', row.get_attribute('data-row-id'))
    assert response.value.status == 200, response.value.text()
    payload = response.value.request.post_data_json
    assert payload['target_coordinates'] == [dict(sentence_idx=1, token_order=0), dict(sentence_idx=2, token_order=0)]
    assert 'row_ids' not in payload
    words = adapter.db.get_words_by_session(ZID)
    assert [(word['sentence_index'], word['token_order']) for word in words] == [(1, 0), (2, 0)]
    assert [word['word_destination'] for word in words] == ['google: Haus', 'google: Haus']
    assert [word['word_provenance'] for word in words] == ['live:google', 'live:google']
    assert [word['selected'] for word in words] == [word['selected'] for word in before]
    expect(row.locator('td[data-col="WordDestination"] .scrollable-cell')).to_have_text('google: Haus')
    page.reload()
    wait_state(page)
    expect(row.locator('td[data-col="WordDestination"] .scrollable-cell')).to_have_text('google: Haus')
    assert adapter.db.get_words_by_session(ZID) == words


@pytest.mark.parametrize('daily_page', ['retry-text'], indirect=True)
@pytest.mark.parametrize('mixed', [False, True], ids=['sentences-only', 'lemma-and-sentences'])
def test_retry_sentence_failure_has_no_partial_commit_and_can_recover(daily_page, monkeypatch, mixed):
    page, server, adapter = daily_page
    server.config.set('pipeline', 'provider_cooldown_seconds', '0')
    if mixed:
        cell = page.locator('#lemma-table td[data-col="WordDestination"]')
        cell.dblclick()
        editor = cell.locator('input.cell-editor')
        editor.fill('')
        editor.press('Enter')
        with page.expect_response('**/session/save') as cleared:
            page.locator('#kw-btn-save').click()
        assert cleared.value.ok, cleared.value.text()
        flush(page)
    before = adapter.db.get_session_bundle(ZID)
    assert all(not sentence['sentence_destination'] for sentence in before['sentences'])
    original = DeterministicTranslationHandler.translate

    def unavailable_text(handler, provider, text, source, target):
        if '.' in text:
            raise RuntimeError('Owned sentence engine failure')
        return original(handler, provider, text, source, target)

    with monkeypatch.context() as external:
        external.setattr(DeterministicTranslationHandler, 'translate', unavailable_text)
        with page.expect_response('**/session/retry') as failed:
            page.evaluate('zid => retrySession(zid)', ZID)
        assert failed.value.status == 500, failed.value.text()
        expect(page.locator('#kw-toast-container')).to_contain_text('Retry failed')
        assert adapter.db.get_session_bundle(ZID) == before
    with page.expect_response('**/session/retry') as retried:
        page.evaluate('zid => retrySession(zid)', ZID)
    assert retried.value.status == 200, retried.value.text()
    bundle = adapter.db.get_session_bundle(ZID)
    assert [sentence['sentence_destination'] for sentence in bundle['sentences']] == ['google: Haus.', 'google: Baum.']
    assert all(sentence['text_provenance'] == 'live:google' for sentence in bundle['sentences'])
    assert bundle['words'][0]['word_destination'] == ('google: Haus' if mixed else 'house')
    assert bundle['words'][1]['word_destination'] == 'tree'
    assert [word['id'] for word in bundle['words']] == [word['id'] for word in before['words']]
    expect(page.locator('#translation-container')).to_contain_text('google: Haus.')
    page.reload()
    wait_state(page)
    expect(page.locator('#translation-container')).to_contain_text('google: Haus.')


@pytest.mark.parametrize('daily_page', ['retry-text-second'], indirect=True)
def test_retry_partial_sentence_set_preserves_completed_sentence_and_identity(daily_page):
    page, server, adapter = daily_page
    page.evaluate('WorkspaceTabs.switchToTab(3)')
    flush(page)
    before = adapter.db.get_session_bundle(ZID)
    assert before['sentences'][0]['sentence_destination'] == 'House.'
    assert not before['sentences'][1]['sentence_destination']
    with page.expect_response('**/session/retry') as retried:
        page.evaluate('zid => retrySession(zid)', ZID)
    assert retried.value.status == 200, retried.value.text()
    payload = retried.value.json()
    result = payload.get('data', payload)
    assert result['sentences'] == {'2': 'google: Baum.'}
    after = adapter.db.get_session_bundle(ZID)
    assert after['sentences'][0] == before['sentences'][0]
    assert after['words'][0] == before['words'][0]
    assert after['sentences'][1]['sentence_destination'] == 'google: Baum.'
    expect(page.locator('#translation-container')).to_contain_text('google: Baum.')
    page.reload()
    wait_state(page)
    expect(page.locator('#translation-container')).to_contain_text('google: Baum.')


@pytest.mark.parametrize('daily_page', ['retry-text'], indirect=True)
def test_retry_sentence_write_failure_rolls_back_prepared_lemma_updates(daily_page):
    page, server, adapter = daily_page
    cell = page.locator('#lemma-table td[data-col="WordDestination"]')
    cell.dblclick()
    editor = cell.locator('input.cell-editor')
    editor.fill('')
    editor.press('Enter')
    with page.expect_response('**/session/save') as cleared:
        page.locator('#kw-btn-save').click()
    assert cleared.value.ok, cleared.value.text()
    flush(page)
    before = adapter.db.get_session_bundle(ZID)
    with adapter.db.get_connection() as connection:
        connection.execute("CREATE TRIGGER owned_retry_failure BEFORE UPDATE ON sentences "
            "BEGIN SELECT RAISE(ABORT, 'Owned sentence write failure'); END")
    try:
        with page.expect_response('**/session/retry') as failed:
            page.evaluate('zid => retrySession(zid)', ZID)
        assert failed.value.status == 500, failed.value.text()
        expect(page.locator('#kw-toast-container')).to_contain_text('Retry failed')
        assert adapter.db.get_session_bundle(ZID) == before
    finally:
        with adapter.db.get_connection() as connection:
            connection.execute('DROP TRIGGER owned_retry_failure')
    with page.expect_response('**/session/retry') as retried:
        page.evaluate('zid => retrySession(zid)', ZID)
    assert retried.value.status == 200, retried.value.text()
    after = adapter.db.get_session_bundle(ZID)
    assert after['words'][0]['word_destination'] == 'google: Haus'
    assert [sentence['sentence_destination'] for sentence in after['sentences']] == ['google: Haus.', 'google: Baum.']
    page.reload()
    wait_state(page)
    expect(page.locator('#translation-container')).to_contain_text('google: Haus.')
