"""Operation validation and active edits remain intact across real Re-word effects."""
import pytest
from playwright.sync_api import expect

from tests.reword_contract_support import ZID, backend, provider_name, world
from tests.test_reword_desktop_persistence import open_generated_desktop


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('invalid_scope', ['²', True, 0.5])
def test_invalid_operation_scope_is_an_explicit_payload_error(world, invalid_scope):
    before = world.snapshot()
    status, payload = world.post(world.payload(sentence_idx=invalid_scope))
    assert (status, payload.get('error_code')) == (400, 'INVALID_PAYLOAD')
    assert (world.provider.calls, world.snapshot()) == ([], before)


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_visual_sentence_hint_may_not_be_a_boolean(world):
    before = world.snapshot()
    status, payload = world.post(world.payload(target_coordinates=[
        dict(visual_idx=world.visual('Haus'), sentence_idx=False)]))
    assert (status, payload.get('error_code')) == (400, 'INVALID_PAYLOAD')
    assert (world.provider.calls, world.snapshot()) == ([], before)


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_occurrence_update_preserves_active_editor_and_pending_value(page, world):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        page.evaluate('visual => { AppState.activeTokenSelections = [{visual_idx:visual}]; }', world.visual('Haus'))
        geometry = '''() => Array.from(document.querySelectorAll('#source-container .word')).map(el => {
            const r = el.getBoundingClientRect(); return [el.textContent,r.x,r.y,r.width,r.height];
        })'''
        before = page.evaluate(geometry)
        cell = page.locator('#lemma-table tr[data-sentence-idx="1"] td[data-col="WordDestination"]')
        cell.dblclick()
        editor = cell.locator('input.cell-editor')
        editor.fill('local-edit')
        status, payload = world.post(world.payload('visual', sentence_idx=1))
        assert status == 200, payload
        page.evaluate('payload => receiveUpdate(payload)', payload)
        expect(editor).to_have_value('local-edit')
        editor.press('Enter')
        assert page.evaluate('isDirty()')
        page.evaluate('payload => receiveUpdate(payload)', payload)
        expect(cell.locator('.scrollable-cell')).to_have_text('local-edit')
        assert page.evaluate(geometry) == before
        assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}
        assert errors == []
    finally:
        page.close()
