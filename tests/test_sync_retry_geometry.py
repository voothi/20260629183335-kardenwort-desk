"""A failed transport cannot cover navigation or move the rendered content."""
import pytest

from tests.test_release_audit_regressions import audit_html, TRANSPORT


PROFILES = [(theme, dock, 180 if (theme_index + dock_index) % 2 == 0 else 1280)
            for theme_index, theme in enumerate(('dark', 'light', 'white', 'compact'))
            for dock_index, dock in enumerate(('top', 'bottom', 'inline'))]

TRANSPORT_FAILURE = """<script>
window.__selectionFails = true;
window.__selectionReads = 0;
const originalTransport = window.fetch;
window.fetch = function(url, options) {
    if (String(url).includes('/internal/selection')) {
        window.__selectionReads++;
        return Promise.resolve({ok:!window.__selectionFails, status:window.__selectionFails ? 503 : 200,
            json:async () => ({ok:true, shared_selection:{haus:true, baum:false}})});
    }
    if (String(url).includes('/session/status')) {
        return Promise.resolve({ok:true, status:200,
            json:async () => ({ok:true, stage:'finished', rows:{}, is_finished:true})});
    }
    return originalTransport(url, options);
};
</script>"""

BOXES = """() => Array.from(document.querySelectorAll(
    '#source-container .word, #lemma-table tbody tr[data-row-id], .kw-tab-nav, .kw-tab-chip'))
    .map(el => {const r=el.getBoundingClientRect(); return [r.x,r.y,r.width,r.height];})"""


@pytest.mark.parametrize('theme,dock,width', PROFILES)
def test_retry_and_tab_controls_remain_reachable_without_reflow(page, tmp_path, theme, dock, width):
    page.set_viewport_size({'width': width, 'height': 600})
    html = audit_html(tmp_path, theme=theme, container=True, dock=dock)
    page.set_content(html.replace('<head>', '<head>' + TRANSPORT + TRANSPORT_FAILURE))
    page.wait_for_function('typeof reconcileStreamMembership === "function"')
    before = page.evaluate(BOXES)
    page.evaluate("""() => {
        SSEBridge.connect('20261023221555');
        __eventSource.onopen({});
    }""")
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'failed'")
    indicator = page.get_by_role('button', name='Retry session synchronization', exact=True)
    assert indicator.is_visible()
    assert indicator.inner_text() == '!'
    assert page.evaluate(BOXES) == before
    assert page.locator('.kw-tab-nav:not(:disabled)').evaluate_all("""buttons => buttons.every(button => {
        const r=button.getBoundingClientRect();
        return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2) === button;
    })"""), page.locator('.kw-tab-nav:not(:disabled)').evaluate_all("""buttons => buttons.map(button => {
        const r=button.getBoundingClientRect();
        const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        return {button:button.id, rect:[r.x,r.y,r.width,r.height], hit:hit && hit.outerHTML};
    })""")
    assert indicator.evaluate("""indicator => {
        const badge=indicator.getBoundingClientRect();
        return Array.from(document.querySelectorAll('.kw-tab-nav')).every(button => {
            const r=button.getBoundingClientRect();
            return badge.right <= r.left || badge.left >= r.right || badge.bottom <= r.top || badge.top >= r.bottom;
        });
    }""")
    failed_reads = page.evaluate('__selectionReads')
    assert failed_reads >= 3
    page.evaluate('__selectionFails = false')
    if dock == 'top':
        indicator.click()
    else:
        indicator.press('Enter' if dock == 'bottom' else 'Space')
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    assert page.evaluate('__selectionReads') > failed_reads
    assert not indicator.count()
    assert page.evaluate(BOXES) == before
