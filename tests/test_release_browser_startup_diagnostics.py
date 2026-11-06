"""Startup failures retain transport evidence without changing readiness checks."""
import json

import pytest
from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import Error as BrowserError

from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN, ZID, open_browser


def test_stream_startup_failure_reports_transport_and_connection_state(page, tmp_path, running_controller):
    url, _, _ = running_controller
    page.route('**/api/v1/events?*', lambda route: route.abort('connectionrefused'))

    with pytest.raises(BrowserTimeout) as failure:
        open_browser(page, tmp_path, url)

    diagnostic = str(failure.value).split('SSE startup diagnostics: ', 1)[1]
    evidence = json.loads(diagnostic)
    assert evidence['state']['session'] == ZID
    assert evidence['state']['connection'] > 0
    assert evidence['state']['status'] != 'connected'
    assert any(event['event'] == 'failed' and 'CONNECTION_REFUSED' in event['error']
               for event in evidence['events'])
    assert TOKEN not in diagnostic


def test_startup_diagnostic_context_loss_preserves_original_timeout(page, tmp_path, running_controller, monkeypatch):
    url, _, _ = running_controller
    page.route('**/api/v1/events?*', lambda route: route.abort('connectionrefused'))
    evaluate = page.evaluate

    def context_lost(expression, *args, **kwargs):
        if 'const indicator =' in expression:
            raise BrowserError('Execution context was destroyed: ' + TOKEN)
        return evaluate(expression, *args, **kwargs)

    monkeypatch.setattr(page, 'evaluate', context_lost)
    with pytest.raises(BrowserTimeout) as failure:
        open_browser(page, tmp_path, url)
    message = str(failure.value)
    assert 'Timeout 5000ms exceeded' in message
    evidence = json.loads(message.split('SSE startup diagnostics: ', 1)[1])
    assert evidence['state'] == {'unavailable': 'Error'}
    assert any(event['event'] == 'failed' for event in evidence['events'])
    assert TOKEN not in message
