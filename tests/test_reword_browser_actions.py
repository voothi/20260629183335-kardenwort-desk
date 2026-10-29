"""The generated source-token action reaches the real controller and storage."""
import pytest
from playwright.sync_api import expect

from tests.reword_contract_support import ZID, backend, provider_name, world
from tests.test_reword_desktop_persistence import open_generated_desktop


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_generated_source_token_reword_uses_original_sentence_identity(page, world):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(3)')
        token = page.locator(f'#source-container .word[data-word-idx="{world.visual("Baum")}"]')
        token.click()
        button = page.locator('#kw-btn-reword')
        expect(button).to_be_enabled()
        with page.expect_response(lambda response: '/session/reword' in response.url) as response:
            button.click()
        assert response.value.ok, response.value.text()
        assert world.values() == {(1, 0): 'house', (2, 0): 'fresh-Baum'}
        body = response.value.request.post_data_json
        assert body['sentence_idx'] == 2
        assert body['row_ids'] == [1]
        assert len(world.provider.calls) == 1
        assert errors == []
    finally:
        page.close()
