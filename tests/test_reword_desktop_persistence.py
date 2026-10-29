"""Generated desktop editing and live exact-identity updates use real storage."""
import re

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.reword_contract_support import ZID, TOKEN, backend, provider_name, world


def open_generated_desktop(page, world, text):
    url = f'http://127.0.0.1:{world.port}'
    world.config.set('server', 'enabled', 'true')
    world.config.set('server', 'port', str(world.port))
    world.config.add_section('rendering')
    world.config.set('rendering', 'auto_save_on_edit', 'false')
    world.config.set('rendering', 'filter_selected_only', 'false')
    world.config.set('sentences_mode', 'enabled', 'true')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def generated_page(route):
        rendered = desk.run_render_flow(text=text, language='de', zid=ZID,
            text_mode='single', config=world.config, resolved_paths=world.paths,
            tsv_path=str(world.path(ZID)), spawn_children=False)
        route.fulfill(content_type='text/html', body=rendered)

    page.route(url + '/', generated_page)
    page.set_extra_http_headers({'X-API-Token': TOKEN})
    page.set_default_timeout(6000)
    page.goto(url + '/')
    page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
    page.wait_for_function('window.AppState && AppState.isFinished')
    return errors


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('active_seq', [1, 2], ids=['overview', 'child'])
def test_generated_desktop_edit_history_save_reload(page, world, active_seq):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('seq => WorkspaceTabs.switchToTab(seq)', active_seq)
        cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]')
        expect(cell).to_have_class(re.compile(r'.*editable.*'))
        expect(cell.locator('.scrollable-cell')).to_have_text('house')
        cell.dblclick()
        editor = cell.locator('input.cell-editor')
        expect(editor).to_be_visible()
        editor.fill('edited-house')
        editor.press('Enter')
        expect(cell.locator('.scrollable-cell')).to_have_text('edited-house')
        assert page.evaluate('isDirty()')
        page.keyboard.press('Control+z')
        expect(cell.locator('.scrollable-cell')).to_have_text('house')
        assert not page.evaluate('isDirty()')
        page.keyboard.press('Control+y')
        expect(cell.locator('.scrollable-cell')).to_have_text('edited-house')
        with page.expect_response(lambda response: '/session/save' in response.url) as saved:
            page.locator('#kw-btn-save').click()
        assert saved.value.ok, saved.value.text()
        payload = saved.value.request.post_data_json
        changes = [item for item in payload['deltas'] if item['column'] == 'WordDestination']
        assert len(changes) == 1
        assert (str(changes[0]['sentence_idx']), str(changes[0]['token_order'])) == ('1', '0')
        page.wait_for_function('!isDirty()')
        assert world.values() == {(1, 0): 'edited-house', (2, 0): 'tree'}
        page.reload()
        page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
        page.evaluate('seq => WorkspaceTabs.switchToTab(seq)', active_seq)
        expect(cell.locator('.scrollable-cell')).to_have_text('edited-house')
        page.evaluate('WorkspaceTabs.switchToTab(3)')
        other = page.locator('#lemma-table tr[data-sentence-idx="2"][data-token-order="0"] td[data-col="WordDestination"]')
        expect(other.locator('.scrollable-cell')).to_have_text('tree')
        assert errors == []
    finally:
        page.close()


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_generated_merged_overview_rejects_ambiguity_and_child_saves_exactly(page, world):
    world.seed(ZID, [['0', 'Haus', 'Haus', 'house', '1', 'Haus.', '', ''],
                     ['0', 'Haus', 'Haus', 'home', '2', 'Haus.', '', '']], text='Haus. Haus.')
    errors = open_generated_desktop(page, world, 'Haus. Haus.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(1)')
        row = page.locator('#lemma-table tbody tr[data-row-id]')
        expect(row).to_have_count(1)
        cell = row.locator('td[data-col="WordDestination"]')
        initial = cell.locator('.scrollable-cell').inner_text()
        assert 'house' in initial and 'home' in initial
        assert row.get_attribute('data-sentence-idx') == '0'
        targets = page.evaluate("JSON.parse(document.querySelector('#lemma-table tbody tr').dataset.occurrenceTargets)")
        assert targets == [dict(sentence_idx=1, token_order=0), dict(sentence_idx=2, token_order=0)]
        before = world.snapshot()
        cell.dblclick()
        expect(cell.locator('input.cell-editor')).to_have_count(0)
        assert not page.evaluate('isDirty()')
        assert world.snapshot() == before
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        cell = page.locator('#lemma-table tr[data-sentence-idx="1"] td[data-col="WordDestination"]')
        initial = 'house'
        cell.dblclick()
        editor = cell.locator('input.cell-editor')
        editor.fill('shared-edit')
        editor.press('Enter')
        page.keyboard.press('Control+z')
        expect(cell.locator('.scrollable-cell')).to_have_text(initial)
        page.keyboard.press('Control+y')
        expect(cell.locator('.scrollable-cell')).to_have_text('shared-edit')
        with page.expect_response(lambda response: '/session/save' in response.url) as saved:
            page.locator('#kw-btn-save').click()
        assert saved.value.ok, saved.value.text()
        page.wait_for_function('!isDirty()')
        assert world.values() == {(1, 0): 'shared-edit', (2, 0): 'home'}
        page.reload()
        page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length >= 3')
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        expect(cell.locator('.scrollable-cell')).to_have_text('shared-edit')
        assert errors == []
    finally:
        page.close()
