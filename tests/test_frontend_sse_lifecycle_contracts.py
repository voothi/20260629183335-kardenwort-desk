"""Phase 1 real-boundary reproduction & contract tests for SSE lifecycle and credentials.

Change: 20261122213501-stabilize-frontend-sse-focus-columns-and-js-hygiene (Task 1.1)
Spec: specs/client-sse-bridge/spec.md
"""
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from tests.release_projection_support import checked_controller as running_controller  # noqa: F401
from tests.test_progressive_sse_deduplication import (
    TOKEN,
    _SubscriberBarrier,
    _cleanup_page_streams,
    _configure_sqlite_world,
    _render_session_html,
    _seed_session,
    subscriber_barrier,  # noqa: F401
)


ROOT = Path(__file__).resolve().parents[1]

ZID_STARTUP = "20261124213001"
ZID_IDENTITY = "20261124213002"
ZID_CLEANUP = "20261124213003"
ZID_RETRY = "20261124213004"
ZID_TERMINAL = "20261124213005"
ZID_POISON = "20261124213006"


def _serve_minimal_sse_harness(page, base_url: str, extra_head: str = ""):
    """Load real web/js/services/sse.js on the controller origin without modifying source."""
    sse_js = (ROOT / "web/js/services/sse.js").read_text(encoding="utf-8")
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        + extra_head
        + "</head><body>"
        "<div id='session-header'></div>"
        "<script id='session-zid' type='text/plain'></script>"
        "<script>\n"
        + sse_js
        + "\n</script></body></html>"
    )
    route_url = f"{base_url}/sse-harness"
    page.route(route_url, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html))
    page.goto(route_url, wait_until="domcontentloaded")
    page.wait_for_function("typeof window.SSEBridge === 'object'")


def test_1_1_progressive_startup_observes_bootstrap_credential_before_first_sse_attach(
    page, tmp_path, running_controller, subscriber_barrier
):
    """First SSE attach on a progressive page must carry the admitted bootstrap credential."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_STARTUP, pending=True)
    html = _render_session_html(server, ZID_STARTUP, pending=True)

    # Ensure credentials exist only in #kardenwort-bootstrap (no #session-data, no window.API_TOKEN).
    html = re.sub(
        r'<script id="session-data"[^>]*>.*?</script>',
        '<script id="session-data" type="application/json">{}</script>',
        html,
        flags=re.DOTALL,
    )
    # Move api_token to top-level bootstrap only, simulating progressive shell where initWatchdog
    # runs before bootstrapScript parsing near the bottom of init().
    match = re.search(
        r'(<script id="kardenwort-bootstrap" type="application/json">)(.*?)(</script>)',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    boot = json.loads(match.group(2))
    boot.setdefault("session_data", {})
    boot["session_data"].pop("api_token", None)
    boot["api_token"] = server.api_key
    html = html[: match.start(2)] + json.dumps(boot) + html[match.end(2) :]

    observed_sse = []

    def on_request(req):
        parsed = urllib.parse.urlsplit(req.url)
        if parsed.path in ("/events", "/api/v1/events"):
            observed_sse.append(
                {
                    "path": parsed.path,
                    "query": parsed.query,
                    "token": req.headers.get("x-api-token", ""),
                }
            )

    observed_statuses = []

    def on_response(resp):
        parsed = urllib.parse.urlsplit(resp.url)
        if parsed.path in ("/events", "/api/v1/events"):
            observed_statuses.append(resp.status)

    page.on("request", on_request)
    page.on("response", on_response)
    route_url = f"{url}/progressive-startup/{ZID_STARTUP}"
    page.route(route_url, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html))
    try:
        page.goto(route_url, wait_until="domcontentloaded")
        page.wait_for_function("typeof window.SSEBridge === 'object' && window.__stateReady !== undefined", timeout=5000)
        deadline = time.monotonic() + 3.0
        while not observed_sse and time.monotonic() < deadline:
            page.evaluate("Boolean(window.SSEBridge)")
        assert observed_sse, "Expected at least one SSE request during progressive startup"
        assert observed_sse[0]["token"] == server.api_key, (
            f"First SSE startup request missed bootstrap credential: {observed_sse}"
        )
        page.wait_for_function(
            "() => document.getElementById('sse-status-indicator')?.dataset.status === 'connected'",
            timeout=5000,
        )
        assert observed_statuses and observed_statuses[0] == 200, (
            f"First SSE startup response was not HTTP 200: {observed_statuses}"
        )
        assert len(observed_sse) == 1, f"Expected single startup SSE request, observed {observed_sse}"
        assert subscriber_barrier.wait_for_count(ZID_STARTUP, lambda n: n == 1) == 1
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_STARTUP, 0)


def test_1_1_sse_connection_identity_upgrade_rotation_server_change_and_normalized_reuse(
    page, tmp_path, running_controller, subscriber_barrier
):
    """connect() must reuse only equal (session, normalized server, credential) and replace on changes."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_IDENTITY, pending=False)

    _serve_minimal_sse_harness(page, url)
    try:
        # 1. Start with empty token (EventSource), then upgrade to authenticated token for same ZID.
        page.evaluate(
            """([zid, baseUrl, token]) => {
                window.__sseFetchCalls = [];
                const origFetch = window.fetch;
                window.fetch = function(input, init) {
                    const headers = (init && init.headers) || {};
                    window.__sseFetchCalls.push({
                        url: String(input),
                        token: headers['X-API-Token'] || '',
                        signal: init && init.signal
                    });
                    return origFetch.apply(this, arguments);
                };
                window.__esInstances = [];
                const OrigES = window.EventSource;
                window.EventSource = function(u) {
                    const inst = {
                        url: u,
                        readyState: 1,
                        closed: false,
                        close() { this.closed = true; this.readyState = 2; }
                    };
                    window.__esInstances.push(inst);
                    return inst;
                };
                window.EventSource.CONNECTING = 0;
                window.EventSource.OPEN = 1;
                window.EventSource.CLOSED = 2;

                window.SSEBridge.connect(zid, baseUrl, '');
                window.__connAfterBlank = window.SSEBridge.getActiveConnectionId();
                window.SSEBridge.connect(zid, baseUrl, token);
                window.__connAfterUpgrade = window.SSEBridge.getActiveConnectionId();
            }""",
            [ZID_IDENTITY, url, server.api_key],
        )
        assert page.evaluate("window.__esInstances[0].closed") is True, (
            "Upgrading empty-token connection to authenticated token failed to close old EventSource"
        )
        assert page.evaluate("window.__connAfterUpgrade !== window.__connAfterBlank") is True
        assert page.evaluate("Boolean(window.SSEBridge.getActiveSource().__isAuthenticatedSource)") is True
        page.wait_for_function(
            "document.getElementById('sse-status-indicator')?.dataset.status === 'connected'",
            timeout=3000,
        )
        assert subscriber_barrier.wait_for_count(ZID_IDENTITY, lambda n: n == 1) == 1

        # 2. Normalized server URL equivalence (trailing slash) with identical ZID & token MUST reuse.
        conn_before_reuse = page.evaluate("window.SSEBridge.getActiveConnectionId()")
        page.evaluate(
            "([zid, baseUrl, token]) => window.SSEBridge.connect(zid, baseUrl + '///', token)",
            [ZID_IDENTITY, url, server.api_key],
        )
        assert page.evaluate("window.SSEBridge.getActiveConnectionId()") == conn_before_reuse
        assert page.evaluate("window.__sseFetchCalls.length") == 1

        # 3. Token rotation for same ZID must abort old transport and connect with the rotated token.
        rotated_token = "rotated-api-token-2026"
        server.api_key = rotated_token
        page.evaluate(
            "([zid, baseUrl, tok]) => window.SSEBridge.connect(zid, baseUrl, tok)",
            [ZID_IDENTITY, url, rotated_token],
        )
        page.wait_for_function("window.__sseFetchCalls.length === 2", timeout=5000)
        assert page.evaluate("window.__sseFetchCalls[0].signal.aborted") is True, (
            "Token rotation failed to abort previous authenticated fetch"
        )
        assert page.evaluate("window.__sseFetchCalls[1].token") == rotated_token
        assert page.evaluate("window.SSEBridge.getActiveConnectionId()") != conn_before_reuse
        assert subscriber_barrier.flush_closed_peers(ZID_IDENTITY, 1) == 1

        # 4. Server URL change for same ZID must dispose old owner and target the new normalized server.
        conn_before_server_change = page.evaluate("window.SSEBridge.getActiveConnectionId()")
        alt_origin = "http://localhost:" + str(server.server_port)
        page.evaluate(
            "([zid, altUrl, tok]) => window.SSEBridge.connect(zid, altUrl, tok)",
            [ZID_IDENTITY, alt_origin, rotated_token],
        )
        page.wait_for_function("window.__sseFetchCalls.length === 3", timeout=5000)
        assert page.evaluate("window.__sseFetchCalls[1].signal.aborted") is True, (
            "Server URL change failed to abort previous authenticated fetch"
        )
        assert page.evaluate("alt => window.__sseFetchCalls[2].url.startsWith(alt)", alt_origin) is True
        assert page.evaluate("window.SSEBridge.getActiveConnectionId()") != conn_before_server_change
    finally:
        server.api_key = TOKEN
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_IDENTITY, 0)


def test_1_1_attempt_throw_eof_cancel_cleanup_and_delayed_stale_callback_isolation(
    page, tmp_path, running_controller, subscriber_barrier
):
    """Handler throw, EOF, cancel, and delayed stale callbacks must dispose old readers without hurting replacements."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_CLEANUP, pending=False)

    _serve_minimal_sse_harness(page, url)
    try:
        # Install a deterministic mock fetch stream to observe reader.cancel(), signal.aborted, and delayed callbacks.
        page.evaluate(
            """() => {
                window.__attempts = [];
                window.__received = [];
                window.__throwNext = false;
                window.receiveUpdate = function(payload) {
                    if (window.__throwNext) {
                        window.__throwNext = false;
                        throw new Error('Synthetic handler throw');
                    }
                    window.__received.push(payload);
                };
                const encoder = new TextEncoder();
                window.fetch = function(url, init) {
                    const attempt = {
                        url,
                        signal: init.signal,
                        cancelled: 0,
                        pendingReads: [],
                        resolveFetch: null,
                        rejectFetch: null,
                    };
                    window.__attempts.push(attempt);
                    return new Promise((resolve, reject) => {
                        attempt.resolveFetch = () => {
                            resolve({
                                ok: true,
                                status: 200,
                                headers: new Headers({'Content-Type': 'text/event-stream'}),
                                body: {
                                    getReader() {
                                        return {
                                            read() {
                                                return new Promise((res, rej) => {
                                                    attempt.pendingReads.push({resolve: res, reject: rej});
                                                });
                                            },
                                            cancel() {
                                                attempt.cancelled += 1;
                                                return Promise.resolve();
                                            }
                                        };
                                    }
                                }
                            });
                        };
                        attempt.rejectFetch = reject;
                        attempt.pushEvent = (obj) => {
                            const next = attempt.pendingReads.shift();
                            if (!next) throw new Error('No pending read for pushEvent');
                            next.resolve({done: false, value: encoder.encode('data: ' + JSON.stringify(obj) + '\\n\\n')});
                        };
                        attempt.pushEof = () => {
                            const next = attempt.pendingReads.shift();
                            if (!next) throw new Error('No pending read for pushEof');
                            next.resolve({done: true, value: undefined});
                        };
                    });
                };
            }"""
        )

        # Case A: Handler throw must cancel reader AND abort fetch before any retry.
        page.evaluate("([zid, u]) => window.SSEBridge.connect(zid, u, 'tok-a')", [ZID_CLEANUP, url])
        page.wait_for_function("window.__attempts.length === 1")
        page.evaluate("window.__attempts[0].resolveFetch()")
        page.wait_for_function("window.__attempts[0].pendingReads.length === 1")
        page.evaluate("() => { window.__throwNext = true; window.__attempts[0].pushEvent({marker: 'boom'}); }")
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.status !== 'connected'")
        assert page.evaluate("window.__attempts[0].cancelled >= 1") is True, (
            "Handler throw failed to cancel active stream reader"
        )
        assert page.evaluate("window.__attempts[0].signal.aborted") is True, (
            "Handler throw failed to abort active fetch controller"
        )

        # Case B: Replacement stream opens, THEN stale attempt 0 resolves a delayed read/error callback.
        page.evaluate("([zid, u]) => window.SSEBridge.connect(zid, u, 'tok-b')", [ZID_CLEANUP, url])
        page.wait_for_function("window.__attempts.length === 2")
        page.evaluate("window.__attempts[1].resolveFetch()")
        page.wait_for_function("window.__attempts[1].pendingReads.length === 1")
        assert page.evaluate("document.getElementById('sse-status-indicator').dataset.status") == "connected"

        # Deliver stale EOF/event on attempt 0 after attempt 1 is live.
        page.evaluate(
            """() => {
                if (window.__attempts[0].pendingReads.length) {
                    window.__attempts[0].pendingReads.shift().reject(new Error('late stale rejection'));
                }
            }"""
        )
        page.evaluate("window.__attempts[1].pushEvent({marker: 'live-replacement'})")
        page.wait_for_function("window.__received.some(x => x.marker === 'live-replacement')")
        assert page.evaluate("window.__attempts[1].signal.aborted") is False
        assert page.evaluate("document.getElementById('sse-status-indicator').dataset.status") == "connected"

        # Case C: EOF on attempt 1 disposes attempt 1's reader/controller cleanly.
        page.evaluate("window.__attempts[1].pushEof()")
        page.wait_for_function("document.getElementById('sse-status-indicator').dataset.status === 'reconnecting'")
        assert page.evaluate("window.__attempts[1].cancelled >= 1 || window.__attempts[1].signal.aborted") is True

        # Case D: Idempotent close cancels reader/controller cleanly.
        page.evaluate("() => { window.SSEBridge.disconnect(); window.SSEBridge.disconnect(); }")
        assert page.evaluate("window.SSEBridge.getActiveZid()") is None
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_CLEANUP, 0)


@pytest.mark.parametrize("status_code,retry_after", [(503, None), (429, "2")])
def test_1_1_transient_5xx_and_429_retry_with_retry_after_and_focus_acceleration(
    page, tmp_path, running_controller, status_code, retry_after
):
    """503 and 429 must be retried with bounded backoff/Retry-After, and focus accelerates transient recovery once."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_RETRY, pending=False)

    page.clock.install()
    _serve_minimal_sse_harness(page, url)
    try:
        page.evaluate(
            """([statusCode, retryAfter]) => {
                window.__fetchAttempts = 0;
                window.__reconcileCalls = [];
                window.reconcileStreamMembership = function(ctx) {
                    window.__reconcileCalls.push(ctx);
                    return Promise.resolve();
                };
                window.fetch = function(u, init) {
                    window.__fetchAttempts += 1;
                    if (window.__fetchAttempts === 1) {
                        const hdrs = new Headers();
                        if (retryAfter) hdrs.set('Retry-After', retryAfter);
                        return Promise.resolve({
                            ok: false,
                            status: statusCode,
                            headers: hdrs,
                            body: null,
                        });
                    }
                    return Promise.resolve({
                        ok: true,
                        status: 200,
                        headers: new Headers({'Content-Type': 'text/event-stream'}),
                        body: {
                            getReader() {
                                return {
                                    read() { return new Promise(() => {}); },
                                    cancel() { return Promise.resolve(); }
                                };
                            }
                        }
                    });
                };
            }""",
            [status_code, retry_after],
        )

        page.evaluate("([zid, u]) => window.SSEBridge.connect(zid, u, 'tok-retry')", [ZID_RETRY, url])
        page.clock.run_for(50)
        assert page.evaluate("window.__fetchAttempts") == 1
        assert page.evaluate("document.getElementById('sse-status-indicator').dataset.status") == "reconnecting", (
            f"HTTP {status_code} was treated as terminal instead of transient reconnecting"
        )
        assert page.evaluate("window.SSEBridge.hasReconnectTimer()") is True, (
            f"HTTP {status_code} did not schedule a retry timer"
        )

        if retry_after:
            # Must honor Retry-After (2s): advancing 500ms must NOT have retried yet.
            page.clock.run_for(500)
            assert page.evaluate("window.__fetchAttempts") == 1

        # Focus-style non-forced reconnect(false) accelerates pending transient recovery once.
        page.evaluate("window.SSEBridge.reconnect(false)")
        page.clock.run_for(50)
        assert page.evaluate("window.__fetchAttempts") == 2, (
            "reconnect(false) failed to accelerate pending transient authenticated recovery"
        )
        assert page.evaluate("document.getElementById('sse-status-indicator').dataset.status") == "connected"
        assert page.evaluate("window.__reconcileCalls.length") == 1
    finally:
        _cleanup_page_streams(page)


@pytest.mark.parametrize("status_code", [401, 403, 404])
def test_1_1_terminal_auth_and_missing_resource_stop_and_ignore_focus_reconnect(
    page, tmp_path, running_controller, status_code
):
    """401/403/404 must stop automatically without retry timer, and focus must not reopen the stream."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_TERMINAL, pending=False)

    page.clock.install()
    _serve_minimal_sse_harness(page, url)
    try:
        page.evaluate(
            """(statusCode) => {
                window.__fetchAttempts = 0;
                window.fetch = function() {
                    window.__fetchAttempts += 1;
                    return Promise.resolve({
                        ok: false,
                        status: statusCode,
                        headers: new Headers(),
                        body: null,
                    });
                };
            }""",
            status_code,
        )

        page.evaluate("([zid, u]) => window.SSEBridge.connect(zid, u, 'bad-token')", [ZID_TERMINAL, url])
        page.clock.run_for(50)
        assert page.evaluate("window.__fetchAttempts") == 1
        assert page.evaluate("document.getElementById('sse-status-indicator').dataset.status") == "disconnected"
        assert page.evaluate("window.SSEBridge.hasReconnectTimer()") is False

        # Advancing clock and triggering focus / non-forced reconnect must NOT reopen a terminal stream.
        page.clock.run_for(20000)
        page.evaluate("() => { window.dispatchEvent(new Event('focus')); window.SSEBridge.reconnect(false); }")
        page.clock.run_for(500)
        assert page.evaluate("window.__fetchAttempts") == 1, (
            f"Terminal HTTP {status_code} stream was reopened without changed credentials/session"
        )
    finally:
        _cleanup_page_streams(page)


def test_1_1_poison_event_surfaces_error_via_resync_without_unbounded_retry_loop(
    page, tmp_path, running_controller, subscriber_barrier
):
    """Repeated poison event application failure must close transport and stop spinning while allowing status resync."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_POISON, pending=False)

    page.clock.install()
    _serve_minimal_sse_harness(page, url)
    try:
        page.evaluate(
            """() => {
                window.__fetchAttempts = 0;
                window.__applyCalls = 0;
                window.__cancelledReaders = 0;
                window.receiveUpdate = function() {
                    window.__applyCalls += 1;
                    throw new Error('Poison event payload');
                };
                const encoder = new TextEncoder();
                window.fetch = function() {
                    window.__fetchAttempts += 1;
                    let delivered = false;
                    return Promise.resolve({
                        ok: true,
                        status: 200,
                        headers: new Headers({'Content-Type': 'text/event-stream'}),
                        body: {
                            getReader() {
                                return {
                                    read() {
                                        if (!delivered) {
                                            delivered = true;
                                            return Promise.resolve({
                                                done: false,
                                                value: encoder.encode('data: {"stage":"enrichment","marker":"poison"}\\n\\n')
                                            });
                                        }
                                        return new Promise(() => {});
                                    },
                                    cancel() {
                                        window.__cancelledReaders += 1;
                                        return Promise.resolve();
                                    }
                                };
                            }
                        }
                    });
                };
            }"""
        )

        page.evaluate("([zid, u]) => window.SSEBridge.connect(zid, u, 'tok-poison')", [ZID_POISON, url])
        # Fast-forward far enough that an unbounded retry loop would spin many times.
        for _ in range(10):
            page.clock.run_for(20000)

        attempts = page.evaluate("window.__fetchAttempts")
        cancelled = page.evaluate("window.__cancelledReaders")
        status = page.evaluate("document.getElementById('sse-status-indicator').dataset.status")
        has_timer = page.evaluate("window.SSEBridge.hasReconnectTimer()")

        assert cancelled >= 1, "Poison event failure did not dispose stream reader"
        assert attempts <= 3, f"Poison event caused unbounded retry loop ({attempts} attempts)"
        assert has_timer is False, "Poison event left an active infinite retry timer"
        assert status in ("error", "disconnected"), f"Expected observable error/disconnected status, got {status}"
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_POISON, 0)
