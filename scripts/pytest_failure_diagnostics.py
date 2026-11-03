"""Opt-in browser failure evidence: python -m pytest -p scripts.pytest_failure_diagnostics."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


SNAPSHOT = """() => {
    const state = window.AppState || {};
    const indicator = document.getElementById('sse-status-indicator');
    return {
        readyState: document.readyState,
        hidden: document.hidden,
        visibility: document.visibilityState,
        finished: state.isFinished,
        stage: state.stage,
        workerLaunched: state.bootstrap && state.bootstrap.worker_launched,
        workerStatus: document.body && document.body.getAttribute('data-worker-status'),
        pending: typeof window.countPendingSkeletons === 'function' ? countPendingSkeletons() : null,
        watchdogPolling: !!window._kwSkeletonPollTimer,
        watchdogResolved: window._kwWatchdogResolved,
        synchronization: indicator && indicator.getAttribute('data-sync'),
        resources: performance.getEntriesByType('resource').map(entry => ({
            path: new URL(entry.name).pathname,
            start: entry.startTime,
            duration: entry.duration,
            responseStatus: entry.responseStatus
        }))
    };
}"""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item):
    outcome = yield
    report = outcome.get_result()
    if report.when != 'call' or not report.failed:
        return
    page = item.funcargs.get('page') or item.funcargs.get('audit_page')
    if page is None or page.is_closed():
        return
    try:
        state = page.evaluate(SNAPSHOT)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
        owner = Path(item.config.rootpath) / 'results' / (stamp + '-pytest-failure')
        owner.mkdir(parents=True, exist_ok=False)
        path = owner / 'browser-state.json'
        path.write_text(json.dumps({'nodeid': item.nodeid, 'state': state}, indent=2), encoding='utf-8')
        report.sections.append(('Browser failure diagnostics', str(path)))
    except Exception as error:
        # Diagnostic failure must remain visible without replacing the test failure.
        report.sections.append(('Browser failure diagnostics unavailable', f'{type(error).__name__}: {error}'))
