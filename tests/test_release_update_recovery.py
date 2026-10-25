"""Update recovery must not hide status behind a provider response."""
import pytest

from kardenwort_events import EventDispatcher
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_save_convergence import held_selection_save
from tests.test_release_boundary_contracts import ZID, restored_membership, wait_for_held


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
