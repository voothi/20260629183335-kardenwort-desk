"""Daily toolbar actions preserve real relational effects across regenerated reloads."""
import json
import csv
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.controller_test_support import controller_world
from tests.test_reliability_action_matrix import MATRIX, configure, render_document, open_card, flush
from tests.reword_contract_support import HEADERS, TOKEN, ZID


@pytest.fixture
def daily_page(page, controller_world, request):
    url, server = controller_world
    case = next(row for row in MATRIX['rows'] if row['audio'] == 'disabled')
    adapter, world = configure(server, case)
    repeated = getattr(request, 'param', '') == 'repeated-lemma'
    text = 'Haus. Haus.' if repeated else 'Haus. Baum.'
    if repeated:
        adapter.save_session(session_zid=ZID, source_language='de', target_language='en',
            text_mode='single', source_raw_text=text, headers=HEADERS+['SentenceDestination'],
            data_rows=[['0', 'Haus', 'Haus', 'house', str(index), 'Haus.', '/haus/', 'noun', 'House.']
                       for index in [1, 2]],
            sentences=[dict(sentence_index=index, sentence_source='Haus.', sentence_destination='House.')
                       for index in [1, 2]])
    if getattr(request, 'param', '') in ['retry-text', 'retry-text-second']:
        for index in ([2] if request.param == 'retry-text-second' else [1, 2]):
            adapter.update_sentence_translation(ZID, index, '')
    server.config.set('pipeline', 'text_reprocess_provider', 'google')
    page.route(url+'/', lambda route: route.fulfill(content_type='text/html', body=render_document(world, case, text)))
    page.set_extra_http_headers({'X-API-Token': TOKEN})
    page.set_default_timeout(6000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url+'/')
    open_card(page)
    page.evaluate('args => SSEBridge.connect(args.zid,args.url,args.token)', dict(zid=ZID,url=url,token=TOKEN))
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    try:
        yield page, server, adapter
    finally:
        page.context.close()
        assert errors == [], errors


def test_hand_delete_history_save_and_reload_restore_exact_record(daily_page):
    page, server, adapter = daily_page
    before = adapter.db.get_words_by_session(ZID)
    token = page.locator('#source-container .word:visible').first
    geometry = token.bounding_box()
    page.locator('#kw-btn-hand-tool').click()
    assert page.locator('body').evaluate("body => body.classList.contains('text-selection-mode-active')")
    page.locator('#kw-btn-hand-tool').click()
    assert not page.locator('body').evaluate("body => body.classList.contains('text-selection-mode-active')")
    assert token.bounding_box() == geometry
    assert adapter.db.get_words_by_session(ZID) == before
    page.evaluate('WorkspaceTabs.switchToTab(3)')
    page.locator('#source-container .word:visible').first.click()
    flush(page)
    page.evaluate('WorkspaceTabs.switchToTab(2)')
    token.click()
    flush(page)
    selected = adapter.db.get_words_by_session(ZID)
    assert adapter.get_shared_selection(ZID)['haus'] is True
    assert not page.evaluate('isDirty()'), page.evaluate('getDeltas()')
    page.locator('#kw-btn-delete').click()
    assert json.loads(page.evaluate('window.getSelectedRows()')) == []
    assert page.evaluate('isDirty()')
    assert adapter.db.get_words_by_session(ZID) == selected
    page.keyboard.press('Control+z')
    assert not page.evaluate('isDirty()'), page.evaluate('getDeltas()')
    page.keyboard.press('Control+y')
    assert page.evaluate('isDirty()')
    with page.expect_response('**/session/save') as deleted:
        page.locator('#kw-btn-save').click()
    assert deleted.value.ok, deleted.value.text()
    flush(page)
    cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    assert cold.db.get_words_by_session(ZID) == selected[1:]
    page.keyboard.press('Control+z')
    with page.expect_response('**/session/save') as restored:
        page.locator('#kw-btn-save').click()
    assert restored.value.ok, restored.value.text()
    flush(page)
    assert cold.db.get_words_by_session(ZID) == selected
    page.reload()
    open_card(page)
    expect(page.locator('#lemma-table td[data-col="WordDestination"] .scrollable-cell')).to_have_text('house')
    assert cold.db.get_words_by_session(ZID) == selected


@pytest.mark.parametrize('identity', ['missing', 'zero-sentence', 'invalid-token'])
def test_server_delete_rejects_missing_or_invalid_occurrence_before_effects(daily_page, identity):
    page, server, adapter = daily_page
    page.locator('#source-container .word:visible').first.click()
    flush(page)
    before = adapter.db.get_session_bundle(ZID)
    result = page.evaluate('''identity => {
        const row = document.querySelector('#lemma-table tbody tr[data-row-id]');
        row.removeAttribute('data-occurrence-targets');
        if (identity === 'missing') {
            row.removeAttribute('data-sentence-idx');
            row.removeAttribute('data-token-order');
        } else if (identity === 'zero-sentence') {
            row.setAttribute('data-sentence-idx', '0');
        } else {
            row.setAttribute('data-token-order', 'invalid');
        }
        try { window.deleteSelectedRows(); return 'accepted'; }
        catch (error) { return error.message; }
    }''', identity)
    assert result == 'Deletion requires an occurrence identity'
    assert not page.evaluate('isDirty()')
    expect(page.locator('#lemma-table tbody tr[data-row-id]').first).to_be_visible()
    assert adapter.db.get_session_bundle(ZID) == before


def test_retext_button_commits_provider_projection_and_reloads(daily_page):
    page, server, adapter = daily_page
    before = adapter.db.get_words_by_session(ZID)
    with page.expect_response('**/session/retext') as translated:
        page.locator('#kw-btn-retext').click()
    assert translated.value.ok, translated.value.text()
    payload = translated.value.json()
    result = payload.get('data', payload)
    assert result['active_text_provider'] == 'google'
    assert result['text_provenance'] == 'live:google'
    cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    sentences = cold.db.get_session_bundle(ZID)['sentences']
    assert {str(sentence['sentence_index']): sentence['sentence_destination'] for sentence in sentences} == result['sentences']
    assert all(sentence['sentence_destination'].startswith('google:') for sentence in sentences)
    assert [(word['lemma'], word['word_destination'], word['selected']) for word in cold.db.get_words_by_session(ZID)] == [
        (word['lemma'], word['word_destination'], word['selected']) for word in before]
    expect(page.locator('#translation-container')).to_contain_text(result['sentences']['1'])
    page.reload()
    open_card(page)
    expect(page.locator('#translation-container')).to_contain_text(result['sentences']['1'])


@pytest.mark.parametrize('daily_page', ['repeated-lemma'], indirect=True)
def test_delete_merged_overview_preserves_both_occurrence_identities(daily_page):
    page, server, adapter = daily_page
    page.evaluate('WorkspaceTabs.switchToTab(1)')
    row = page.locator('#lemma-table tbody tr[data-row-id]:visible').first
    row.click()
    flush(page)
    before = adapter.db.get_words_by_session(ZID)
    assert [(word['sentence_index'], word['token_order']) for word in before] == [(1, 0), (2, 0)]
    assert all(word['selected'] == 1 for word in before)
    page.locator('#kw-btn-delete').click()
    assert page.evaluate('isDirty()')
    with page.expect_response('**/session/save') as deleted:
        page.locator('#kw-btn-save').click()
    assert deleted.value.ok, (deleted.value.text(), deleted.value.request.post_data_json)
    targets = [delta for delta in deleted.value.request.post_data_json['deltas'] if delta['column'] == '_delete']
    assert {(int(delta['sentence_idx']), int(delta['token_order'])) for delta in targets} == {(1, 0), (2, 0)}
    flush(page)
    assert adapter.db.get_words_by_session(ZID) == []
    page.keyboard.press('Control+z')
    with page.expect_response('**/session/save') as restored:
        page.locator('#kw-btn-save').click()
    assert restored.value.ok, restored.value.text()
    flush(page)
    assert adapter.db.get_words_by_session(ZID) == before
    page.reload()
    open_card(page, 1)
    expect(page.locator('#source-container')).to_contain_text('Haus')
    assert adapter.db.get_words_by_session(ZID) == before


@pytest.mark.parametrize('failure', [False, True], ids=['favorites', 'unwritable-favorites'])
def test_export_button_reports_owned_favorites_result_without_claiming_anki(daily_page, failure):
    page, server, adapter = daily_page
    destination = Path(server.resolved_paths['base_dir'])/'favorites'
    if failure:
        destination.write_text('Owned path obstruction', encoding='utf-8')
    server.resolved_paths['favorites_output_dir'] = destination
    server.config.read_dict({'settings': {'save_to_favorites_on_export': 'true',
        'send_to_anki_after_export': 'false', 'favorites_output_dir': str(destination),
        'export_selection_mode': 'selected'}})
    page.locator('#source-container .word:visible').first.click()
    flush(page)
    before = adapter.db.get_words_by_session(ZID)
    with page.expect_response('**/session/export') as exported:
        page.locator('#kw-btn-export').click()
    assert exported.value.status == (500 if failure else 200), exported.value.text()
    if failure:
        expect(page.locator('.kw-toast-error')).to_contain_text('Export failed')
        assert destination.read_text(encoding='utf-8') == 'Owned path obstruction'
    else:
        expect(page.locator('.kw-toast-success')).to_contain_text('favorites')
        expect(page.locator('.kw-toast-success')).not_to_contain_text('Anki')
        files = list(destination.glob('*.tsv'))
        assert len(files) == 1
        lines = [line for line in files[0].read_text(encoding='utf-8').splitlines() if not line.startswith('#')]
        rows = list(csv.DictReader(lines, delimiter='\t'))
        assert len(rows) == 1
        assert (rows[0]['WordSource'], rows[0]['WordDestination']) == ('Haus', 'house')
    assert adapter.db.get_words_by_session(ZID)[1] == before[1]
    page.reload()
    open_card(page, 1)
    expect(page.locator('#source-container')).to_contain_text('Haus')
    assert adapter.db.get_words_by_session(ZID) == before
