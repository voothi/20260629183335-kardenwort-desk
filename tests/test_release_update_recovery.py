"""Update recovery must not hide status behind a provider response."""
import pytest

from kardenwort_events import EventDispatcher
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_save_convergence import held_selection_save
from tests.test_release_boundary_contracts import ZID, restored_membership, wait_for_held
from tests.test_release_audit_regressions import audit_page  # noqa: F401


@pytest.mark.parametrize('recovery_result', ['finished', 'network_failure'])
def test_update_status_does_not_wait_for_provider_after_save(page, tmp_path, running_controller, recovery_result):
    saves, response = held_selection_save(page, tmp_path, running_controller)
    reads, recovery, statuses = [], [], []
    page.route('**/internal/selection', lambda route: reads.append(route))
    page.route('**/session/retext', lambda route: recovery.append(route))

    def status_request(route):
        statuses.append(route.request)
        route.continue_()

    page.route('**/session/status?*', status_request)
    page.evaluate('void onUpdateClick()')
    assert not recovery and not statuses
    saves[0].fulfill(response=response)
    wait_for_held(page, reads, 1)
    assert not recovery and not statuses
    reads[0].fulfill(response=reads[0].fetch())
    wait_for_held(page, recovery, 1)
    # The provider response is deliberately still held. Status remains usable.
    wait_for_held(page, statuses, 1)
    page.wait_for_function("!document.getElementById('kw-btn-update').disabled")
    assert restored_membership(running_controller[2]) == {'haus': True, 'baum': True}
    assert page.evaluate('!isDirty()')
    if recovery_result == 'network_failure':
        recovery[0].abort('failed')
    else:
        recovery[0].fulfill(json={'ok': True})
        # Deliver the eventual pipeline event through the actual controller stream.
        EventDispatcher.get_instance().publish(ZID, {'stage': 'translated_text', 'translatedText': 'The house. The tree.'})
        page.wait_for_function("document.getElementById('translation-container').textContent.includes('The house.')")
    assert page.evaluate('!isDirty()')


@pytest.mark.parametrize('first_reader', ['manual', 'watchdog'])
@pytest.mark.parametrize('status_result', ['success', 'timeout'])
def test_update_status_owns_current_watchdog_poll_interval(audit_page, first_reader, status_result):
    page = audit_page
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.evaluate("""() => {
        window.__statusReads = [];
        window.__providerReads = [];
        window.__statusReloads = [];
        window.onSessionReload = () => __statusReloads.push(new Error().stack);
        window.fetch = function(url) {
            const response = data => ({ok:true,status:200,json:()=>Promise.resolve(data)});
            if (String(url).includes('/session/status')) {
                return new Promise(resolve => __statusReads.push(data => resolve(response(data))));
            }
            if (String(url).includes('/session/retext')) {
                return new Promise(resolve => __providerReads.push(() => resolve(response({ok:true}))));
            }
            return Promise.resolve({ok:true,text:()=>Promise.resolve('skeleton-loader')});
        };
        document.getElementById('translation-container').innerHTML =
            '<span class="skeleton-loader" data-pending="true">Working...</span>';
        initWatchdog();
    }""")
    if first_reader == 'watchdog':
        page.evaluate('void _kwPollSessionStatus()')
    page.evaluate('void onUpdateClick()')
    page.wait_for_function('__statusReads.length > 0 && __providerReads.length === 1')
    page.evaluate('void _kwPollSessionStatus()')
    assert page.evaluate('__statusReads.length') == 1, 'The manual status read owns this interval while pending'
    if status_result == 'timeout':
        page.clock.run_for(4100)
        page.wait_for_function("!document.getElementById('kw-btn-update').disabled")
        assert len(page.evaluate('__statusReloads')) == 1, page.evaluate('__statusReloads')
        assert page.evaluate('__statusReads.length') == 1
        page.clock.run_for(1000)
        page.evaluate('void _kwPollSessionStatus()')
        assert page.evaluate('__statusReads.length') == 2
        page.evaluate('__statusReads[1]({ok:true,status:"ok",stage:"finished",is_finished:true}); __providerReads[0]()')
        return
    page.evaluate('__statusReads[0]({ok:true,status:"ok",stage:"translating",is_finished:false})')
    page.wait_for_function("!document.getElementById('kw-btn-update').disabled")
    page.evaluate('void _kwPollSessionStatus()')
    assert page.evaluate('__statusReads.length') == 1, 'A fresh manual status response prevents an immediate duplicate'
    page.clock.run_for(1000)
    page.evaluate('void _kwPollSessionStatus()')
    assert page.evaluate('__statusReads.length') == 2
    page.evaluate('__statusReads[1]({ok:true,status:"ok",stage:"finished",is_finished:true}); __providerReads[0]()')


def test_watchdog_status_waits_for_pending_save(audit_page):
    page = audit_page
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.evaluate("""() => {
        window.__holdSaves = true;
        toggleRowSelection(1, true);
        void onSaveClick(true);
        document.getElementById('translation-container').innerHTML =
            '<span class="skeleton-loader" data-pending="true">Working...</span>';
        initWatchdog();
    }""")
    page.wait_for_function('__pendingSaves.length === 1')
    page.evaluate('__requests=[]; void _kwPollSessionStatus()')
    assert not page.evaluate('__requests.some(r=>r.url.includes("/session/status"))')
    assert page.locator('#kw-btn-save').is_disabled()
    page.evaluate('__pendingSaves.shift()()')
    page.wait_for_function('!isDirty()')
    page.evaluate('void _kwPollSessionStatus()')
    assert page.evaluate('__requests.filter(r=>r.url.includes("/session/status")).length') == 1
