"""Actual worker-lock failures remain explicit at the article boundary."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_dict_article_actions import article_session, article_page, selected, change, ZID  # noqa: F401


@pytest.mark.parametrize('article_session', ['tsv'], indirect=True)
@pytest.mark.parametrize('action', ['tag', 'export'])
def test_worker_lock_blocks_article_action_without_false_acknowledgment(page, article_session, article_page, running_controller, action):
    render, restored, favorites = article_session
    _, server, _ = running_controller
    article_page(render())
    if action == 'export':
        change(page, 0, True)
    before = selected(restored)
    path = Path(server.resolved_paths['results_dir']) / (ZID + '-article.de.tsv.worker.lock')
    with server.arbiter.storage_adapter.file_lock(path):
        if action == 'tag':
            page.locator('tr[data-row-id="0"] input').check()
        else:
            page.get_by_role('button', name='Export to Anki').click()
        expect(page.locator('.kw-dict-article')).to_have_attribute('data-state', 'failed')
        assert 'ROW_BUSY' in page.get_by_role('status').inner_text()
        assert page.locator('tr[data-row-id="0"] input').is_checked()
        assert selected(restored) == before
        assert not list(favorites.glob('*.tsv'))
