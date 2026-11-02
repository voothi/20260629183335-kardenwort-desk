"""Hydrated mode follows the current session through a remount."""
import json
from pathlib import Path

import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture, TRANSPORT


def test_lookup_mode_does_not_leak_into_later_desktop_session(page):
    html = render_fixture(desk, 2, 'container')
    page.set_content(html.replace('<head>', '<head>' + TRANSPORT))
    page.wait_for_function('typeof isLookupMode === "function"')
    assert page.evaluate('isLookupMode()') is False
    bootstrap = page.locator('#kardenwort-bootstrap').text_content()
    for lookup in (True, False):
        data = json.loads(bootstrap)
        data['is_lookup'] = lookup
        page.locator('#kardenwort-bootstrap').evaluate('(el,data)=>el.textContent=JSON.stringify(data)', data)
        page.add_script_tag(content=Path('web/js/app.js').read_text(encoding='utf-8'))
        assert page.evaluate('isLookupMode()') is lookup
    assert page.locator('#lemma-table tbody tr[data-row-id]').count() == 2
