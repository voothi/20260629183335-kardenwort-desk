"""Documented keyboard aliases through generated UI, real HTTP and owned SQLite."""
import pytest
from playwright.sync_api import expect

from tests.controller_test_support import controller_world
from tests.test_daily_control_persistence import daily_page
from tests.test_reliability_action_matrix import flush
from tests.sqlite_document_support import wait_state
from tests.reword_contract_support import ZID


NAVIGATION = [(modifier+'+'+str(digit), digit if digit < 4 else 2)
              for modifier in ['Control', 'Alt', 'Meta'] for digit in [1, 3, 9]]
NAVIGATION += [(modifier+key, destination)
               for modifier in ['Control+', 'Meta+']
               for key, destination in [('PageDown', 1), ('PageUp', 3), (']', 1), ('[', 3)]]
NAVIGATION += [(']', 1), ('[', 3)]


@pytest.mark.parametrize('shortcut,destination', NAVIGATION)
def test_tab_keyboard_alias_preserves_membership_filter_and_reload(daily_page, shortcut, destination):
    page, server, adapter = daily_page
    assert page.evaluate('WorkspaceTabs.getCards().map(card => card.seq_num)') == [1, 3, 2]
    page.locator('#source-container .word:visible').first.click()
    flush(page)
    with page.expect_response('**/api/v1/config') as preference:
        page.locator('#kw-btn-filter-selected').click()
    assert preference.value.ok, preference.value.text()
    before = adapter.db.get_words_by_session(ZID)
    page.keyboard.press(shortcut)
    page.wait_for_function('seq => WorkspaceTabs.getActiveTabSeq() === seq', arg=destination)
    flush(page)
    assert adapter.db.get_words_by_session(ZID) == before
    assert page.evaluate('AppState.filterSelectedOnly') is True
    page.reload()
    wait_state(page)
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == destination
    assert page.evaluate('AppState.filterSelectedOnly') is True
    assert adapter.db.get_words_by_session(ZID) == before


@pytest.mark.parametrize('shortcut', ['Control+3', 'Alt+3', 'Meta+3', ']'])
def test_tab_keyboard_alias_does_not_navigate_from_active_editor(daily_page, shortcut):
    page, server, adapter = daily_page
    before = adapter.db.get_words_by_session(ZID)
    cell = page.locator('#lemma-table td[data-col="WordDestination"]')
    cell.click()
    page.keyboard.press('F2')
    editor = cell.locator('input.cell-editor')
    expect(editor).to_be_visible()
    editor.fill('owned draft')
    page.keyboard.press(shortcut)
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()') == 2
    expect(editor).to_be_visible()
    page.keyboard.press('Escape')
    expect(editor).to_have_count(0)
    expect(cell.locator('.scrollable-cell')).to_have_text('house')
    assert adapter.db.get_words_by_session(ZID) == before


@pytest.mark.parametrize('undo,redo,save', [
    ('Control+z', 'Control+y', 'Control+s'),
    ('Control+z', 'Control+Shift+z', 'Control+s'),
    ('Meta+z', 'Meta+y', 'Meta+s'),
    ('Meta+z', 'Meta+Shift+z', 'Meta+s'),
])
def test_history_keyboard_alias_commits_exact_edit_and_reloads(daily_page, undo, redo, save):
    page, server, adapter = daily_page
    before = adapter.db.get_words_by_session(ZID)
    cell = page.locator('#lemma-table td[data-col="WordDestination"]')
    cell.dblclick()
    editor = cell.locator('input.cell-editor')
    editor.fill('keyboard house')
    editor.press('Enter')
    page.keyboard.press(undo)
    expect(cell.locator('.scrollable-cell')).to_have_text('house')
    assert not page.evaluate('isDirty()')
    page.keyboard.press(redo)
    expect(cell.locator('.scrollable-cell')).to_have_text('keyboard house')
    with page.expect_response('**/session/save') as saved:
        page.keyboard.press(save)
    assert saved.value.ok, saved.value.text()
    flush(page)
    words = adapter.db.get_words_by_session(ZID)
    assert words[0]['word_destination'] == 'keyboard house'
    assert words[1] == before[1]
    page.reload()
    wait_state(page)
    expect(cell.locator('.scrollable-cell')).to_have_text('keyboard house')
    assert adapter.db.get_words_by_session(ZID) == words
