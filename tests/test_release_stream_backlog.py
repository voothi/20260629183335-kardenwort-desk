"""Observable final-state delivery contracts, including bounded backlog gaps."""
import queue

import pytest

from kardenwort_events import EventDispatcher
from tests.test_release_save_convergence import hold_stream_recovery
from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import wait_for_held


def drain(channel):
    events = []
    while True:
        try:
            events.append(channel.get_nowait())
        except queue.Empty:
            return events


def indicates_gap(events):
    return any(event.get('type') in ('resync_required', 'disconnected') or event.get('resync_required')
               for event in events)


def test_full_queue_cannot_silently_lose_final_committed_stage():
    dispatcher = EventDispatcher()
    slow, healthy = dispatcher.register('session'), dispatcher.register('session')
    capacity = slow.maxsize
    for index in range(capacity):
        dispatcher.publish('session', dict(stage='enrichment', sequence=index))
        healthy.get_nowait()
    dispatcher.publish('session', dict(stage='finished', translatedText='final committed value'))
    healthy_events, slow_events = drain(healthy), drain(slow)
    assert healthy_events[-1]['translatedText'] == 'final committed value'
    assert len(slow_events) <= capacity
    assert any(event.get('stage') == 'finished' for event in slow_events) or indicates_gap(slow_events), (
        'The slow consumer lost the final stage with no client-visible recovery transition')


def test_paused_polling_retains_bounded_observable_recovery():
    dispatcher = EventDispatcher()
    assert dispatcher.poll_events('session', 'slow', accept=False) == []
    dispatcher.poll_events('session', 'healthy', accept=False)
    for index in range(1020):
        dispatcher.publish('session', dict(stage='enrichment', sequence=index))
        dispatcher.poll_events('session', 'healthy')
    dispatcher.publish('session', dict(stage='finished', translatedText='latest committed value'))
    assert dispatcher.poll_events('session', 'slow', accept=False) == []
    events = dispatcher.poll_events('session', 'slow')
    assert len(events) <= 1000
    assert any(event.get('stage') == 'finished' for event in events) or indicates_gap(events), (
        'Paused consumer has no way to discover the lost final committed stage')
    assert dispatcher.poll_events('session', 'healthy')[-1]['translatedText'] == 'latest committed value'


def test_closing_slow_consumer_does_not_remove_healthy_subscription():
    dispatcher = EventDispatcher()
    dispatcher.poll_events('session', 'slow', accept=False)
    dispatcher.poll_events('session', 'healthy', accept=False)
    dispatcher.poll_events('session', 'slow', close=True)
    dispatcher.publish('session', dict(stage='finished'))
    assert dispatcher.poll_events('session', 'healthy')[0]['stage'] == 'finished'


@pytest.mark.parametrize('response', ['offline', 'unauthorized'])
def test_recovery_failure_never_claims_synced_or_discards_pending_input(page, tmp_path, running_controller, response):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    page.evaluate('toggleRowSelection(1,true)')
    before = page.evaluate('getDeltas()')
    for count in range(1, 4):
        wait_for_held(page, reads, count)
        if response == 'offline':
            reads[count - 1].abort('failed')
        else:
            reads[count - 1].fulfill(status=401, json={'status': 'error', 'error_code': 'UNAUTHORIZED'})
    page.wait_for_function('__recoveryResult !== "pending"')
    assert page.evaluate('__recoveryResult') == 'rejected'
    assert page.locator('#sse-status-indicator').get_attribute('data-sync') == 'failed'
    assert page.evaluate('getDeltas()') == before
    assert page.evaluate('isDirty()')


@pytest.mark.parametrize('lifecycle', ['disconnect', 'remount', 'session'])
def test_obsolete_membership_recovery_cannot_mutate_current_page(page, tmp_path, running_controller, lifecycle):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    response = reads[0].fetch()
    if lifecycle == 'disconnect':
        page.evaluate('SSEBridge.disconnect()')
    elif lifecycle == 'remount':
        from pathlib import Path
        page.add_script_tag(content=Path('web/js/app.js').read_text(encoding='utf-8-sig'))
        wait_for_held(page, reads, 2)
    else:
        page.evaluate('''() => {
            document.getElementById('session-zid').textContent='20261101191228';
            WorkspaceTabs.getCards().forEach(card=>card.zid='20261101191228');
        }''')
    before = page.evaluate('JSON.stringify(AppState.selectedLemmas)')
    reads[0].fulfill(response=response)
    page.evaluate('Boolean(AppState)')
    assert page.evaluate('JSON.stringify(AppState.selectedLemmas)') == before
