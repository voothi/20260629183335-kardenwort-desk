"""A failed committed-state recovery exposes an accessible, bounded retry."""
import pytest
from playwright.sync_api import expect

from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import ZID, open_browser, use_backend, wait_for_held
from tests.test_stream_gap_recovery import gap, seed_session, set_committed_translation


@pytest.mark.parametrize('activation', ['click', 'keyboard'])
def test_failed_gap_indicator_retries_committed_state(page, tmp_path, running_controller, activation):
    url, server, _ = running_controller
    adapter = use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    indicator = page.locator('#sse-status-indicator')
    expect(indicator).to_have_attribute('data-sync', 'synced')
    table = page.locator('#lemma-table')
    before = table.bounding_box()
    set_committed_translation(adapter, server, 'accessible retry committed value')
    held = []
    page.route('**/session/status?*committed=1', lambda route: held.append(route))
    gap()
    for count in range(1, 4):
        wait_for_held(page, held, count)
        held[-1].fulfill(status=401, json={'status': 'error', 'error_code': 'UNAUTHORIZED'})
    retry = page.get_by_role('button', name='Retry session synchronization')
    expect(retry).to_have_attribute('data-sync', 'failed')
    expect(retry).to_be_visible()
    assert table.bounding_box() == before
    page.unroute('**/session/status?*committed=1')
    if activation == 'click':
        retry.click()
    else:
        retry.focus()
        retry.press('Enter')
    expect(indicator).to_have_attribute('data-sync', 'synced')
    expect(indicator).not_to_have_attribute('role', 'button')
    expect(page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')).to_have_text('accessible retry committed value')


def test_live_commit_supersedes_held_gap_status_without_stale_projection(page, tmp_path, running_controller):
    from kardenwort_controller import committed_recovery_snapshot
    from kardenwort_events import EventDispatcher
    url, server, _ = running_controller
    adapter = use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    expect(page.locator('#sse-status-indicator')).to_have_attribute('data-sync', 'synced')
    held = []
    page.route('**/session/status?*committed=1', lambda route: held.append(route))
    set_committed_translation(adapter, server, 'obsolete held snapshot')
    gap()
    wait_for_held(page, held, 1)
    old = held[0].fetch()
    page.evaluate('''() => {
        window.__projectedValues = [];
        const cell = document.querySelector('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"]');
        new MutationObserver(()=>__projectedValues.push(cell.textContent.trim()))
            .observe(cell,{subtree:true,childList:true,characterData:true});
    }''')
    set_committed_translation(adapter, server, 'newer live commit')
    EventDispatcher.get_instance().publish(ZID, committed_recovery_snapshot(server.arbiter, ZID))
    cell = page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')
    expect(cell).to_have_text('newer live commit')
    held[0].fulfill(response=old)
    wait_for_held(page, held, 2)
    held[1].fulfill(response=held[1].fetch())
    expect(page.locator('#sse-status-indicator')).to_have_attribute('data-sync', 'synced')
    expect(cell).to_have_text('newer live commit')
    assert 'obsolete held snapshot' not in page.evaluate('__projectedValues')
