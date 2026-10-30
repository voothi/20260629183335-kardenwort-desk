from playwright.sync_api import expect
from tests.reword_contract_support import backend, provider_name, world
from tests.test_reword_desktop_persistence import open_generated_desktop


def test_redo_resolves_current_cell_after_projection(page, world):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]')
        cell.dblclick()
        editor = cell.locator('input.cell-editor')
        editor.fill('edited-house')
        editor.press('Enter')
        cell.evaluate("cell => window.__oldCell = cell")
        page.keyboard.press('Control+z')
        expect(cell.locator('.scrollable-cell')).to_have_text('house')
        page.evaluate('WorkspaceTabs.rebindActiveTab()')
        assert page.evaluate('!document.documentElement.contains(__oldCell)')
        page.keyboard.press('Control+y')
        expect(cell.locator('.scrollable-cell')).to_have_text('edited-house')
        assert page.evaluate('isDirty()')
        assert errors == []
    finally:
        page.close()
