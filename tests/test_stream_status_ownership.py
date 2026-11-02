"""Superseded watchdog outcomes cannot undo authoritative recovery."""
import pytest
from playwright.sync_api import expect

from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import open_browser, use_backend, wait_for_held
from tests.test_stream_gap_recovery import seed_session, set_committed_translation, gap


@pytest.mark.parametrize('source', ['poll', 'cleanup'])
@pytest.mark.parametrize('outcome', ['offline', 'stale'])
def test_superseded_watchdog_cannot_overwrite_gap_success(page, tmp_path, running_controller, source, outcome):
    url, server, _ = running_controller
    adapter = use_backend(server, 'sqlite')
    seed_session(server, adapter)
    page.add_init_script('''const originalFetch=window.fetch;
        window.fetch=function(url,options){
            const cached=String(url).includes('/session/status?') && !String(url).includes('committed=1');
            return originalFetch(url,options).then(response=>{
                if(cached){
                    const json=response.json.bind(response);
                    response.json=()=>json().then(data=>{window.__oldStatusSettled=true;return data;});
                }
                return response;
            },error=>{if(cached) window.__oldStatusSettled=true;throw error;});
        };''')
    held = []
    page.route('**/session/status?*', lambda route: route.continue_() if 'committed=1' in route.request.url else held.append(route))
    open_browser(page, tmp_path, url)
    indicator = page.locator('#sse-status-indicator')
    expect(indicator).to_have_attribute('data-sync', 'synced')
    page.evaluate("source => source === 'poll' ? _kwPollSessionStatus() : cleanupOrphanSkeletons()", source)
    wait_for_held(page, held, 1)
    old_response = held[0].fetch() if outcome == 'stale' else None
    set_committed_translation(adapter, server, 'authoritative gap success')
    old_connection = page.evaluate('SSEBridge.getActiveConnectionId()')
    gap()
    page.wait_for_function('old=>SSEBridge.getActiveConnectionId()!==old', arg=old_connection)
    expect(indicator).to_have_attribute('data-sync', 'synced')
    if outcome == 'offline':
        held[0].abort('failed')
    else:
        held[0].fulfill(response=old_response)
    page.wait_for_function('window.__oldStatusSettled===true')
    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert indicator.get_attribute('data-sync') == 'synced'
    expect(page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordDestination"] .scrollable-cell')).to_have_text('authoritative gap success')
