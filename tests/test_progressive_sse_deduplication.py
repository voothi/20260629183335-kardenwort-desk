"""Real HTTP + Playwright evidence and contract controls for progressive SSE stream ownership.

Change: 20261119102726-deduplicate-progressive-sse-streams
Spec: specs/client-sse-bridge/spec.md (Single SSE stream ownership per progressive session)

Every test runs against the real controller HTTP server, real SQLite storage,
real EventDispatcher, and real Playwright browser contexts with deterministic
synchronization barriers (no sleep-based polling).
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from tests.release_projection_support import checked_controller as running_controller  # noqa: F401
from tests.toolbar_test_support import with_translation_skeleton


TOKEN = "test-controller-api-key"
ZID_PRIMARY = "20261122152001"
ZID_SECONDARY = "20261122152002"
ZID_STANDALONE = "20261122152003"
ZID_TWO_CLIENTS = "20261122152004"
ZID_RECONNECT_BEFORE = "20261122152005"
ZID_RECONNECT_AFTER = "20261122152006"
ZID_LIFECYCLE = "20261122152007"
ZID_REPLACEMENT = "20261122152008"

HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "WordDestination",
    "DeskSelected",
]


class _SubscriberBarrier:
    """Observes real EventDispatcher register/unregister calls with a condition barrier."""

    def __init__(self, dispatcher: EventDispatcher):
        self.dispatcher = dispatcher
        self.cond = threading.Condition()
        self._orig_register = dispatcher.register
        self._orig_unregister = dispatcher.unregister
        self.registrations: list[str] = []
        self.unregistrations: list[str] = []

        def tracked_register(session_zid: str):
            q = self._orig_register(session_zid)
            with self.cond:
                self.registrations.append(session_zid)
                self.cond.notify_all()
            return q

        def tracked_unregister(session_zid: str, q):
            try:
                return self._orig_unregister(session_zid, q)
            finally:
                with self.cond:
                    self.unregistrations.append(session_zid)
                    self.cond.notify_all()

        dispatcher.register = tracked_register
        dispatcher.unregister = tracked_unregister

    def close(self):
        self.dispatcher.register = self._orig_register
        self.dispatcher.unregister = self._orig_unregister

    def count(self, zid: str) -> int:
        with self.dispatcher._lock:
            return len(self.dispatcher._subscribers.get(zid, []))

    def wait_for_count(self, zid: str, predicate, timeout: float = 5.0) -> int:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                current = self.count(zid)
                if predicate(current):
                    return current
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return current
                self.cond.wait(timeout=min(remaining, 0.25))

    def flush_closed_peers(self, zid: str, expected: int, timeout: float = 5.0) -> int:
        """Wake server SSE threads blocked on q.get(timeout=10) via harmless ping events."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.count(zid)
            if current == expected:
                return current
            self.dispatcher.publish(zid, {"type": "ping"})
            with self.cond:
                self.cond.wait(timeout=0.1)
        return self.count(zid)


@pytest.fixture
def subscriber_barrier():
    barrier = _SubscriberBarrier(EventDispatcher.get_instance())
    try:
        yield barrier
    finally:
        barrier.close()


def _configure_sqlite_world(server, adapter, tmp_path):
    server.config.set("storage", "backend", "sqlite")
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("rendering", "filter_selected_only", "false")
    server.config.set("rendering", "highlight_same_lemma", "false")
    server.config.set("rendering", "auto_save_on_edit", "false")
    server.config.set("sentences_mode", "enabled", "false")
    server.config.set("wordfill", "enabled", "false")
    server.resolved_paths["storage_backend"] = "sqlite"
    server.resolved_paths["generated_results_dir"] = server.resolved_paths["results_dir"]
    gd = dict(server.goldendict) if getattr(server, "goldendict", None) else {}
    gd["server_enabled"] = True
    gd["server_api_key"] = server.api_key
    gd["server_host"] = "127.0.0.1"
    gd["server_port"] = server.server_port
    server.goldendict = gd
    server.arbiter.goldendict = gd
    server.arbiter.storage_adapter = adapter


def _seed_session(server, adapter, zid: str, *, pending: bool = True):
    rows = [
        ["0", "Haus", "Haus", "1", "Das Haus.", "The house." if not pending else "", "house" if not pending else "", "0"],
        ["1", "Baum", "Baum", "2", "Der Baum.", "The tree." if not pending else "", "tree" if not pending else "", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug=f"sse-{zid[-4:]}",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Das Haus. Der Baum.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[
            {
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": "Das Haus.",
                "sentence_destination": "" if pending else "The house.",
                "sentence_destination2": "" if pending else "The house.",
                "text_provenance": "" if pending else "live:test",
            },
            {
                "session_zid": zid,
                "sentence_index": 2,
                "sentence_source": "Der Baum.",
                "sentence_destination": "" if pending else "The tree.",
                "sentence_destination2": "" if pending else "The tree.",
                "text_provenance": "" if pending else "live:test",
            },
        ],
    )
    server.arbiter.sessions[zid] = {
        "session_zid": zid,
        "language": "de",
        "headers": list(HEADERS),
        "data_rows": [list(r) for r in rows],
        "comments": [],
        "translatedText": "" if pending else "The house. The tree.",
        "stage": "translating" if pending else "finished",
        "is_finished": not pending,
        "worker_status": "running" if pending else "finished",
        "lock": threading.Lock(),
    }


def _render_session_html(server, zid: str, *, pending: bool = True) -> str:
    html = desk.run_render_flow(
        text="Das Haus. Der Baum.",
        language="de",
        zid=zid,
        text_mode="single",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=zid,
        spawn_children=False,
        api_token=server.api_key,
        server_enabled=True,
    )
    if pending:
        html = with_translation_skeleton(html)
        html = re.sub(
            r'(<td\b[^>]*data-col="WordDestination"[^>]*>\s*<div class="scrollable-cell">)(.*?)(</div>)',
            r'\1<span class="skeleton-loader" data-pending="true">Translating...</span>\3',
            html,
            count=1,
            flags=re.DOTALL,
        )
    return html


def _post_json(url: str, path: str, body: dict, token: str = TOKEN):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Token": token},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


DISPATCH_OBSERVER_SCRIPT = """<script>
(() => {
    window.__sseDispatches = [];
    let currentReceive = undefined;
    Object.defineProperty(window, 'receiveUpdate', {
        configurable: true,
        enumerable: true,
        get() { return currentReceive; },
        set(fn) {
            if (typeof fn !== 'function') {
                currentReceive = fn;
                return;
            }
            currentReceive = function(payload) {
                if (payload && typeof payload === 'object') {
                    window.__sseDispatches.push({
                        stage: payload.stage || null,
                        marker: payload.marker || null,
                        is_finished: Boolean(payload.is_finished),
                        hasSharedSelection: Boolean(payload.shared_selection),
                    });
                }
                return fn.apply(this, arguments);
            };
        }
    });
})();
</script>"""


def _open_served_page(page, url: str, route_path: str, html: str):
    sse_requests = []
    sse_responses = []
    status_requests = []

    def on_request(request):
        parsed = urllib.parse.urlsplit(request.url)
        if parsed.path in ("/events", "/api/v1/events"):
            sse_requests.append(parsed.path + "?" + parsed.query)
        elif parsed.path == "/session/status":
            status_requests.append(parsed.path + "?" + parsed.query)

    def on_response(response):
        parsed = urllib.parse.urlsplit(response.url)
        if parsed.path in ("/events", "/api/v1/events"):
            sse_responses.append((parsed.path + "?" + parsed.query, response.status))

    page.on("request", on_request)
    page.on("response", on_response)
    instrumented = html.replace("<head>", "<head>\n" + DISPATCH_OBSERVER_SCRIPT, 1)
    page.route(url + route_path, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=instrumented))
    page.goto(url + route_path, wait_until="domcontentloaded")
    page.wait_for_function("typeof window.receiveUpdate === 'function' && typeof window.SSEBridge === 'object'")
    return sse_requests, sse_responses, status_requests


def _cleanup_page_streams(page):
    if not page.is_closed():
        page.evaluate("""() => {
            if (window.SSEBridge) window.SSEBridge.disconnect();
            if (window._kwEvtSource) {
                try { window._kwEvtSource.close(); } catch (e) {}
                window._kwEvtSource = null;
            }
            if (window._kwSkeletonPollTimer) {
                clearInterval(window._kwSkeletonPollTimer);
                window._kwSkeletonPollTimer = null;
            }
            if (window._kwWatchdogMaxTimer) {
                clearTimeout(window._kwWatchdogMaxTimer);
                window._kwWatchdogMaxTimer = null;
            }
            if (window._kwSseReconnectTimer) {
                clearTimeout(window._kwSseReconnectTimer);
                window._kwSseReconnectTimer = null;
            }
        }""")


def test_1_2_progressive_startup_single_subscriber_and_single_dispatch(
    page, tmp_path, running_controller, subscriber_barrier
):
    """Progressive startup (watchdog + hydrateSession) must share one subscriber and dispatch once."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_PRIMARY, pending=True)
    html = _render_session_html(server, ZID_PRIMARY, pending=True)

    sse_requests, sse_responses, _ = _open_served_page(page, url, f"/progressive/{ZID_PRIMARY}", html)
    try:
        # Wait deterministically for SSEBridge connected + watchdog active + all startup SSE HTTP responses ready.
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.status === 'connected'")
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        page.wait_for_function("() => window._kwSkeletonPollTimer !== null")
        # Wait until every SSE request initiated during startup has received its HTTP 200 stream header.
        deadline = time.monotonic() + 5.0
        while len(sse_responses) < len(sse_requests) and time.monotonic() < deadline:
            page.evaluate("Boolean(window.SSEBridge)")

        active_subs = subscriber_barrier.count(ZID_PRIMARY)

        # Publish a single non-replayed progressive update followed by a FIFO barrier event.
        dispatcher = EventDispatcher.get_instance()
        dispatcher.publish(
            ZID_PRIMARY,
            {
                "stage": "enrichment",
                "marker": "single-probe",
                "rows": {"0": {"WordDestination": "house-progress", "token_order": 0, "sentence_idx": 1}},
            },
        )
        dispatcher.publish(
            ZID_PRIMARY,
            {
                "stage": "enrichment",
                "marker": "fifo-barrier",
                "rows": {"1": {"WordDestination": "tree-progress", "token_order": 1, "sentence_idx": 2}},
            },
        )
        # Wait until at least one fifo-barrier arrives; if duplicate streams are open, wait for all of them.
        page.wait_for_function(
            "() => window.__sseDispatches.some(d => d.marker === 'fifo-barrier')",
            timeout=5000,
        )
        if active_subs > 1:
            try:
                page.wait_for_function(
                    "expected => window.__sseDispatches.filter(d => d.marker === 'fifo-barrier').length >= expected",
                    arg=active_subs,
                    timeout=2000,
                )
            except Exception:
                pass

        dispatches = page.evaluate("() => window.__sseDispatches")
        probe_dispatches = sum(1 for d in dispatches if d.get("marker") == "single-probe")
        barrier_dispatches = sum(1 for d in dispatches if d.get("marker") == "fifo-barrier")

        assert active_subs == 1, (
            f"Duplicate SSE ownership reproduced: controller observed {active_subs} active subscribers "
            f"for one progressive document; HTTP SSE requests={sse_requests}; "
            f"receiveUpdate dispatches: single-probe={probe_dispatches}, fifo-barrier={barrier_dispatches}"
        )
        assert len(sse_requests) == 1, f"Expected 1 startup SSE request, observed {sse_requests}"
        assert probe_dispatches == 1, (
            f"Expected 1 receiveUpdate dispatch for single emitted event, observed {probe_dispatches}"
        )
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_PRIMARY, 0)


def test_1_2_standalone_hydrated_page_and_static_shell_single_owner(
    browser, tmp_path, running_controller, subscriber_barrier
):
    """Standalone hydrated pages (no watchdog) and static web/index.html retain one live owner."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_STANDALONE, pending=False)
    html = _render_session_html(server, ZID_STANDALONE, pending=False)

    context = browser.new_context()
    page = context.new_page()
    try:
        sse_requests, _, _ = _open_served_page(page, url, f"/hydrated/{ZID_STANDALONE}", html)
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        assert page.evaluate("() => !window._kwSkeletonPollTimer") is True
        assert subscriber_barrier.wait_for_count(ZID_STANDALONE, lambda n: n == 1) == 1
        assert len(sse_requests) == 1

        dispatcher = EventDispatcher.get_instance()
        dispatcher.publish(
            ZID_STANDALONE,
            {
                "stage": "translated_text",
                "marker": "standalone-probe",
                "translatedText": "Standalone updated text.",
            },
        )
        page.wait_for_function("() => window.__sseDispatches.some(d => d.marker === 'standalone-probe')")
        assert page.evaluate("() => window.__sseDispatches.filter(d => d.marker === 'standalone-probe').length") == 1

        # Calling initSSE again for the same session must not open a second connection.
        page.evaluate("zid => window.initSSE(zid)", ZID_STANDALONE)
        assert subscriber_barrier.count(ZID_STANDALONE) == 1
        assert len(sse_requests) == 1
    finally:
        _cleanup_page_streams(page)
        context.close()
        subscriber_barrier.flush_closed_peers(ZID_STANDALONE, 0)

    # Also verify standalone web/index.html static shell calling window.initSSE(zid) directly.
    shell_context = browser.new_context()
    shell_page = shell_context.new_page()
    try:
        web_dir = Path(__file__).resolve().parent.parent / "web"
        index_html = (web_dir / "index.html").read_text(encoding="utf-8")

        def serve_web(route):
            parsed = urllib.parse.urlsplit(route.request.url)
            rel = parsed.path.lstrip("/") or "index.html"
            target = (web_dir / rel).resolve()
            if target.is_file() and str(target).startswith(str(web_dir.resolve())):
                ctype = "application/javascript" if target.suffix == ".js" else (
                    "text/css" if target.suffix == ".css" else "text/html; charset=utf-8"
                )
                body = target.read_bytes()
                if rel == "index.html":
                    text = index_html.replace("<head>", f"<head><script>window.API_TOKEN={json.dumps(TOKEN)};</script>", 1)
                    body = text.encode("utf-8")
                route.fulfill(content_type=ctype, body=body)
                return
            route.continue_()

        shell_page.route(f"{url}/**", serve_web)
        shell_page.goto(f"{url}/index.html", wait_until="domcontentloaded")
        shell_page.wait_for_function("typeof window.initSSE === 'function' && typeof window.SSEBridge === 'object'")
        shell_page.evaluate("zid => { window.initSSE(zid); window.initSSE(zid); }", ZID_STANDALONE)
        shell_page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.status === 'connected'")
        assert subscriber_barrier.wait_for_count(ZID_STANDALONE, lambda n: n == 1) == 1
    finally:
        _cleanup_page_streams(shell_page)
        shell_context.close()
        subscriber_barrier.flush_closed_peers(ZID_STANDALONE, 0)


def test_1_2_two_clients_remote_selection_and_preferences_after_finished_and_idle(
    browser, tmp_path, running_controller, subscriber_barrier
):
    """Two independent documents keep 1 stream each; after finished & idle, remote selection & prefs sync."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_TWO_CLIENTS, pending=True)
    html = _render_session_html(server, ZID_TWO_CLIENTS, pending=True)

    ctx_a = browser.new_context()
    ctx_b = browser.new_context()
    page_a = ctx_a.new_page()
    page_b = ctx_b.new_page()
    try:
        sse_a, resp_a, status_a = _open_served_page(page_a, url, f"/doc-a/{ZID_TWO_CLIENTS}", html)
        sse_b, resp_b, status_b = _open_served_page(page_b, url, f"/doc-b/{ZID_TWO_CLIENTS}", html)

        for p, reqs, resps in ((page_a, sse_a, resp_a), (page_b, sse_b, resp_b)):
            p.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
            p.wait_for_function("() => window._kwSkeletonPollTimer !== null")
            deadline = time.monotonic() + 5.0
            while len(resps) < len(reqs) and time.monotonic() < deadline:
                p.evaluate("Boolean(window.SSEBridge)")

        pre_finish_subs = subscriber_barrier.count(ZID_TWO_CLIENTS)
        assert pre_finish_subs == 2, (
            f"Expected 2 total subscribers for 2 progressive documents (1 each), observed {pre_finish_subs}; "
            f"client_a_sse={sse_a}, client_b_sse={sse_b}"
        )

        # Complete translation via server event.
        server.arbiter.sessions[ZID_TWO_CLIENTS]["stage"] = "finished"
        server.arbiter.sessions[ZID_TWO_CLIENTS]["is_finished"] = True
        server.arbiter.sessions[ZID_TWO_CLIENTS]["worker_status"] = "finished"
        EventDispatcher.get_instance().publish(
            ZID_TWO_CLIENTS,
            {
                "stage": "finished",
                "is_finished": True,
                "marker": "finish-event",
                "translatedText": "The house. The tree.",
                "rows": {
                    "0": {"WordDestination": "house", "token_order": 0, "sentence_idx": 1},
                    "1": {"WordDestination": "tree", "token_order": 1, "sentence_idx": 2},
                },
            },
        )

        for p in (page_a, page_b):
            p.wait_for_function("() => window._kwWatchdogResolved === true")
            p.wait_for_function("() => !window._kwSkeletonPollTimer && !window._kwWatchdogMaxTimer")
            assert p.locator(".skeleton-loader").count() == 0
            assert p.locator("#sse-status-indicator").get_attribute("data-status") == "connected"

        # Both persistent streams MUST stay open after finished!
        assert subscriber_barrier.flush_closed_peers(ZID_TWO_CLIENTS, 2) == 2

        # Verify no new /session/status polling fires while idle.
        status_count_b_after_finish = len(status_b)

        # Commit a remote selection change from Client A through real /session/save.
        save_status, save_body = _post_json(
            url,
            "/session/save",
            {
                "session_zid": ZID_TWO_CLIENTS,
                "language": "de",
                "deltas": [
                    {"row_id": 0, "token_order": 0, "sentence_idx": 1, "column": "DeskSelected", "value": "1"}
                ],
            },
        )
        assert save_status == 200, save_body

        # Client B must receive and project the remote selection change without reload.
        expect(page_b.locator('#lemma-table tbody tr[data-row-id="0"]')).to_have_attribute("data-selected", "1")
        assert page_b.evaluate("() => Boolean(window.AppState.selectedLemmas && window.AppState.selectedLemmas['haus'])") is True

        # Commit a remote preference change through real /api/v1/config (highlight_same_lemma -> true).
        pref_status, pref_body = _post_json(
            url,
            "/api/v1/config",
            {"key": "highlight_same_lemma", "value": "true", "expected_revision": 0},
        )
        assert pref_status == 200, pref_body

        # Client B must receive and project the remote preference change without reload.
        page_b.wait_for_function("() => Boolean(window.__CONFIG__ && window.__CONFIG__.highlight_same_lemma === true)")
        expect(page_b.locator("#kw-btn-same-lemma")).to_have_class(re.compile(r"\bactive\b"))

        # Confirm zero extra watchdog status polls occurred during idle remote sync.
        assert len(status_b) == status_count_b_after_finish
    finally:
        _cleanup_page_streams(page_a)
        _cleanup_page_streams(page_b)
        ctx_a.close()
        ctx_b.close()
        subscriber_barrier.flush_closed_peers(ZID_TWO_CLIENTS, 0)


@pytest.mark.parametrize("phase", ["before_finished", "after_finished"])
def test_1_2_drop_and_reconnect_single_owner_and_replay_idempotency(
    page, tmp_path, running_controller, subscriber_barrier, phase
):
    """Stream drop before or after finished reconnects via one owner without duplicate listeners or state regression."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    zid = ZID_RECONNECT_BEFORE if phase == "before_finished" else ZID_RECONNECT_AFTER
    pending = phase == "before_finished"
    _seed_session(server, adapter, zid, pending=pending)
    html = _render_session_html(server, zid, pending=pending)

    sse_requests, sse_responses, _ = _open_served_page(page, url, f"/reconnect/{zid}", html)
    try:
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        deadline = time.monotonic() + 5.0
        while len(sse_responses) < len(sse_requests) and time.monotonic() < deadline:
            page.evaluate("Boolean(window.SSEBridge)")

        initial_subs = subscriber_barrier.count(zid)
        assert initial_subs == 1, f"Expected 1 initial subscriber ({phase}), observed {initial_subs}: {sse_requests}"

        # Commit baseline selection + preference before disconnect.
        save_status, _ = _post_json(
            url,
            "/session/save",
            {
                "session_zid": zid,
                "language": "de",
                "deltas": [
                    {"row_id": 0, "token_order": 0, "sentence_idx": 1, "column": "DeskSelected", "value": "1"}
                ],
            },
        )
        assert save_status == 200
        expect(page.locator('#lemma-table tbody tr[data-row-id="0"]')).to_have_attribute("data-selected", "1")

        pref_status, pref_body = _post_json(
            url,
            "/api/v1/config",
            {"key": "highlight_same_lemma", "value": "true", "expected_revision": 0},
        )
        assert pref_status == 200, pref_body
        pref_data = pref_body.get("data", pref_body)
        committed_pref_rev = pref_data["revision"]
        page.wait_for_function("() => window.__CONFIG__?.highlight_same_lemma === true")

        # Trigger deterministic stream drop/recovery via resync_required (forces SSEBridge disconnect + reconnect).
        old_conn_id = page.evaluate("() => window.SSEBridge.getActiveConnectionId()")
        dispatcher = EventDispatcher.get_instance()
        dispatcher.publish(zid, {"type": "resync_required"})

        page.wait_for_function("old => window.SSEBridge.getActiveConnectionId() !== old", arg=old_conn_id)
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        assert subscriber_barrier.flush_closed_peers(zid, 1) == 1

        # Verify single dispatch after reconnect (no duplicate listeners).
        dispatcher.publish(
            zid,
            {
                "stage": "enrichment",
                "marker": "post-reconnect-probe",
                "rows": {"0": {"WordDestination": "house-after-reconnect", "token_order": 0, "sentence_idx": 1}},
            },
        )
        dispatcher.publish(
            zid,
            {
                "stage": "enrichment",
                "marker": "post-reconnect-barrier",
                "rows": {"1": {"WordDestination": "tree-after-reconnect", "token_order": 1, "sentence_idx": 2}},
            },
        )
        page.wait_for_function("() => window.__sseDispatches.some(d => d.marker === 'post-reconnect-barrier')")
        assert page.evaluate(
            "() => window.__sseDispatches.filter(d => d.marker === 'post-reconnect-probe').length"
        ) == 1

        # Replaying an older preference revision must NOT regress committed preference state.
        dispatcher.publish(
            zid,
            {
                "type": "preferences_updated",
                "preferences": {
                    "values": {"highlight_same_lemma": False},
                    "revisions": {"highlight_same_lemma": max(0, committed_pref_rev - 1)},
                },
            },
        )
        dispatcher.publish(
            zid,
            {
                "stage": "enrichment",
                "marker": "replay-check-barrier",
                "rows": {"0": {"WordDestination": "house-after-reconnect", "token_order": 0, "sentence_idx": 1}},
            },
        )
        page.wait_for_function("() => window.__sseDispatches.some(d => d.marker === 'replay-check-barrier')")
        assert page.evaluate("() => window.__CONFIG__?.highlight_same_lemma === true") is True
        expect(page.locator('#lemma-table tbody tr[data-row-id="0"]')).to_have_attribute("data-selected", "1")
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(zid, 0)


def test_1_2_lifecycle_pagehide_persisted_pageshow_and_session_replacement(
    page, tmp_path, running_controller, subscriber_barrier
):
    """pagehide disposes the stream, persisted pageshow resumes once, and session switch releases the old ZID."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_LIFECYCLE, pending=False)
    _seed_session(server, adapter, ZID_REPLACEMENT, pending=False)
    html = _render_session_html(server, ZID_LIFECYCLE, pending=False)

    _, _, _ = _open_served_page(page, url, f"/lifecycle/{ZID_LIFECYCLE}", html)
    try:
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        assert subscriber_barrier.wait_for_count(ZID_LIFECYCLE, lambda n: n == 1) == 1
        conn_before_hide = page.evaluate("() => window.SSEBridge.getActiveConnectionId()")

        # 1. Persisted pagehide must dispose the active stream and leave 0 controller subscribers.
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
        assert page.evaluate("() => window.SSEBridge.getActiveZid()") is None, (
            "pagehide did not dispose active SSEBridge stream"
        )
        assert subscriber_barrier.flush_closed_peers(ZID_LIFECYCLE, 0) == 0

        # Events published while hidden/disposed must not dispatch.
        before_hidden_count = page.evaluate("() => window.__sseDispatches.length")
        dispatcher = EventDispatcher.get_instance()
        dispatcher.publish(ZID_LIFECYCLE, {"stage": "enrichment", "marker": "while-hidden"})

        # 2. Persisted pageshow must resume through the same lifecycle owner with exactly 1 stream.
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        assert subscriber_barrier.wait_for_count(ZID_LIFECYCLE, lambda n: n == 1) == 1
        assert page.evaluate("() => window.SSEBridge.getActiveConnectionId()") != conn_before_hide

        dispatcher.publish(
            ZID_LIFECYCLE,
            {
                "stage": "enrichment",
                "marker": "after-pageshow",
                "rows": {"0": {"WordDestination": "resumed-house", "token_order": 0, "sentence_idx": 1}},
            },
        )
        page.wait_for_function("() => window.__sseDispatches.some(d => d.marker === 'after-pageshow')")
        assert page.evaluate("() => window.__sseDispatches.filter(d => d.marker === 'after-pageshow').length") == 1
        assert page.evaluate("() => window.__sseDispatches.filter(d => d.marker === 'while-hidden').length") == 0
        assert page.evaluate("() => window.__sseDispatches.length") >= before_hidden_count + 1

        # 3. Session replacement (ZID_LIFECYCLE -> ZID_REPLACEMENT) must release ZID_LIFECYCLE completely.
        page.evaluate(
            "args => { document.getElementById('session-zid').textContent = args.zid; window.initSSE(args.zid); }",
            {"zid": ZID_REPLACEMENT},
        )
        page.wait_for_function(
            "zid => window.SSEBridge.getActiveZid() === zid && document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'",
            arg=ZID_REPLACEMENT,
        )
        assert subscriber_barrier.flush_closed_peers(ZID_LIFECYCLE, 0) == 0
        assert subscriber_barrier.wait_for_count(ZID_REPLACEMENT, lambda n: n == 1) == 1

        dispatcher.publish(ZID_LIFECYCLE, {"stage": "enrichment", "marker": "obsolete-session-event"})
        dispatcher.publish(
            ZID_REPLACEMENT,
            {
                "stage": "enrichment",
                "marker": "replacement-session-event",
                "rows": {"0": {"WordDestination": "replacement-house", "token_order": 0, "sentence_idx": 1}},
            },
        )
        page.wait_for_function("() => window.__sseDispatches.some(d => d.marker === 'replacement-session-event')")
        assert page.evaluate(
            "() => window.__sseDispatches.filter(d => d.marker === 'obsolete-session-event').length"
        ) == 0
        assert page.evaluate(
            "() => window.__sseDispatches.filter(d => d.marker === 'replacement-session-event').length"
        ) == 1

        # 4. Non-persisted pagehide (final disposal) must leave 0 subscribers.
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false}))")
        assert page.evaluate("() => window.SSEBridge.getActiveZid()") is None
        assert subscriber_barrier.flush_closed_peers(ZID_REPLACEMENT, 0) == 0
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_LIFECYCLE, 0)
        subscriber_barrier.flush_closed_peers(ZID_REPLACEMENT, 0)


def test_1_2_two_clients_idle_remote_selection_and_preferences_control(
    browser, tmp_path, running_controller, subscriber_barrier
):
    """Control: two idle documents on the same session sync remote selection & preference commits with zero polling."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    zid = ZID_SECONDARY
    _seed_session(server, adapter, zid, pending=False)
    html = _render_session_html(server, zid, pending=False)

    ctx_a = browser.new_context()
    ctx_b = browser.new_context()
    page_a = ctx_a.new_page()
    page_b = ctx_b.new_page()
    try:
        sse_a, _, status_a = _open_served_page(page_a, url, f"/idle-a/{zid}", html)
        sse_b, _, status_b = _open_served_page(page_b, url, f"/idle-b/{zid}", html)

        for p in (page_a, page_b):
            p.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
            assert p.evaluate("() => !window._kwSkeletonPollTimer") is True

        assert subscriber_barrier.wait_for_count(zid, lambda n: n == 2) == 2
        assert len(sse_a) == 1 and len(sse_b) == 1
        status_count_b_before = len(status_b)

        save_status, save_body = _post_json(
            url,
            "/session/save",
            {
                "session_zid": zid,
                "language": "de",
                "deltas": [
                    {"row_id": 0, "token_order": 0, "sentence_idx": 1, "column": "DeskSelected", "value": "1"}
                ],
            },
        )
        assert save_status == 200, save_body
        expect(page_b.locator('#lemma-table tbody tr[data-row-id="0"]')).to_have_attribute("data-selected", "1")
        assert page_b.evaluate("() => Boolean(window.AppState.selectedLemmas && window.AppState.selectedLemmas['haus'])") is True

        pref_status, pref_body = _post_json(
            url,
            "/api/v1/config",
            {"key": "highlight_same_lemma", "value": "true", "expected_revision": 0},
        )
        assert pref_status == 200, pref_body
        page_b.wait_for_function("() => Boolean(window.__CONFIG__ && window.__CONFIG__.highlight_same_lemma === true)")
        expect(page_b.locator("#kw-btn-same-lemma")).to_have_class(re.compile(r"\bactive\b"))

        assert len(status_b) == status_count_b_before
        assert subscriber_barrier.count(zid) == 2
    finally:
        _cleanup_page_streams(page_a)
        _cleanup_page_streams(page_b)
        ctx_a.close()
        ctx_b.close()
        subscriber_barrier.flush_closed_peers(zid, 0)


def test_1_2_session_replacement_releases_old_subscriber_control(
    page, tmp_path, running_controller, subscriber_barrier
):
    """Control: switching active session via initSSE releases old ZID subscription and callbacks."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    _seed_session(server, adapter, ZID_LIFECYCLE, pending=False)
    _seed_session(server, adapter, ZID_REPLACEMENT, pending=False)
    html = _render_session_html(server, ZID_LIFECYCLE, pending=False)

    _open_served_page(page, url, f"/replace/{ZID_LIFECYCLE}", html)
    try:
        page.wait_for_function("document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'")
        assert subscriber_barrier.wait_for_count(ZID_LIFECYCLE, lambda n: n == 1) == 1

        page.evaluate(
            "args => { document.getElementById('session-zid').textContent = args.zid; window.initSSE(args.zid); }",
            {"zid": ZID_REPLACEMENT},
        )
        page.wait_for_function(
            "zid => window.SSEBridge.getActiveZid() === zid && document.getElementById('sse-status-indicator')?.dataset.sync === 'synced'",
            arg=ZID_REPLACEMENT,
        )
        assert subscriber_barrier.flush_closed_peers(ZID_LIFECYCLE, 0) == 0
        assert subscriber_barrier.wait_for_count(ZID_REPLACEMENT, lambda n: n == 1) == 1

        dispatcher = EventDispatcher.get_instance()
        dispatcher.publish(ZID_LIFECYCLE, {"stage": "enrichment", "marker": "old-session-must-not-fire"})
        dispatcher.publish(
            ZID_REPLACEMENT,
            {
                "stage": "enrichment",
                "marker": "new-session-must-fire",
                "rows": {"0": {"WordDestination": "replaced-house", "token_order": 0, "sentence_idx": 1}},
            },
        )
        page.wait_for_function("() => window.__sseDispatches.some(d => d.marker === 'new-session-must-fire')")
        assert page.evaluate(
            "() => window.__sseDispatches.filter(d => d.marker === 'old-session-must-not-fire').length"
        ) == 0
        assert page.evaluate(
            "() => window.__sseDispatches.filter(d => d.marker === 'new-session-must-fire').length"
        ) == 1
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(ZID_LIFECYCLE, 0)
        subscriber_barrier.flush_closed_peers(ZID_REPLACEMENT, 0)
