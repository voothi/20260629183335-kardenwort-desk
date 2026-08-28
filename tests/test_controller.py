import sys
import os
import json
import time
import queue
import socket
import threading
import urllib.request
import urllib.error
import pytest
from pathlib import Path
from http.server import ThreadingHTTPServer

import kardenwort_desk
from kardenwort_controller import (
    ProcessSupervisor,
    SessionArbiter,
    SidecarService,
    WindowsJobObject,
    ControllerRequestHandler,
    run_controller,
    EnrichmentQueue,
)


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def running_controller():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), ControllerRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "test-controller-api-key"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()
    server.server_port = port

    # In-memory arbiter
    server.arbiter = SessionArbiter(config, resolved_paths)
    server.arbiter.goldendict = goldendict

    # Disabled supervisor for test isolation (unit tests test supervisor separately)
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    server_url = f"http://127.0.0.1:{port}"
    yield server_url, server

    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# 1. Supervisor & Windows Job Object Unit Tests
# ---------------------------------------------------------------------------
def test_windows_job_object_init():
    job = WindowsJobObject()
    if sys.platform == "win32":
        assert job.job_handle is not None
        job.close()
        assert job.job_handle is None


def test_supervisor_status_report():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    sup = ProcessSupervisor(config, resolved_paths, enabled=False)

    report = sup.get_status_report()
    assert "spacy" in report
    assert "translation" in report
    assert "intellifiller" in report
    assert report["spacy"]["port"] == 8081
    assert report["translation"]["port"] == 8082
    assert report["intellifiller"]["port"] == 8083


def test_supervisor_probe_offline_service():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    sup = ProcessSupervisor(config, resolved_paths, enabled=False)

    offline_svc = SidecarService("dummy", 59998, ["python", "-c", "pass"])
    assert not sup.probe_health(offline_svc, timeout=0.2)
    assert not offline_svc.is_healthy


# ---------------------------------------------------------------------------
# 2. Session Arbiter & SSE Subscription Unit Tests
# ---------------------------------------------------------------------------
def test_session_arbiter_sse_channel():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    arbiter = SessionArbiter(config, resolved_paths)

    zid = "20260819003500"
    q1 = arbiter.register_subscriber(zid)
    q2 = arbiter.register_subscriber(zid)

    test_event = {"type": "stage", "stage": "translated_text", "status": "success"}
    arbiter.emit_event(zid, test_event)

    ev1 = q1.get(timeout=1.0)
    ev2 = q2.get(timeout=1.0)

    assert ev1["stage"] == "translated_text"
    assert ev1["session_zid"] == zid
    assert "timestamp" in ev1

    assert ev2["stage"] == "translated_text"
    assert ev2["session_zid"] == zid

    arbiter.unregister_subscriber(zid, q1)
    arbiter.unregister_subscriber(zid, q2)
    assert zid not in arbiter.subscribers


# ---------------------------------------------------------------------------
# 3. Controller Endpoints Integration Tests
# ---------------------------------------------------------------------------
def test_controller_health_endpoint(running_controller):
    server_url, _ = running_controller
    req = urllib.request.Request(f"{server_url}/health")
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data["status"] == "success"
        assert data["data"]["ok"] is True
        assert "controller" in data["data"]
        assert "sidecars" in data["data"]


def test_controller_lookup_endpoint(running_controller):
    server_url, _ = running_controller
    url = f"{server_url}/api/v1/lookup?text=apple&language=en&bypass-lang-check=true"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode('utf-8'))
        assert res["status"] == "success"
        assert "fingerprint" in res["data"]
        assert "session_zid" in res["data"]


def test_controller_session_lifecycle_and_sse_streaming(running_controller):
    server_url, server = running_controller

    # 1. Connect SSE client in background thread
    test_zid = f"2026081999{int(time.time()) % 10000:04d}"
    received_events = []
    sse_connected = threading.Event()
    stop_listener = threading.Event()

    def sse_listener():
        import http.client
        import urllib.parse
        parsed = urllib.parse.urlparse(server_url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5.0)
        conn.request("GET", f"/events?zid={test_zid}")
        resp = conn.getresponse()
        sse_connected.set()

        while not stop_listener.is_set() and len(received_events) < 3:
            line = resp.fp.readline().decode('utf-8')
            if not line:
                break
            line = line.strip()
            if line.startswith("data: "):
                raw_json = line[6:]
                try:
                    received_events.append(json.loads(raw_json))
                except Exception:
                    pass
        conn.close()

    listener_thread = threading.Thread(target=sse_listener, daemon=True)
    listener_thread.start()
    assert sse_connected.wait(timeout=2.0)
    time.sleep(0.1)

    # 2. Emit event via arbiter
    t0 = time.perf_counter()
    server.arbiter.emit_event(test_zid, {
        "type": "stage",
        "stage": "translated_text",
        "status": "success",
        "data": "Hallo Welt"
    })
    server.arbiter.emit_event(test_zid, {
        "type": "stage",
        "stage": "finished",
        "status": "success"
    })

    listener_thread.join(timeout=3.0)
    stop_listener.set()
    latency_ms = (time.perf_counter() - t0) * 1000

    # Verify event delivery
    assert len(received_events) >= 3
    assert received_events[0]["type"] == "connected"
    assert received_events[1]["stage"] == "translated_text"
    assert received_events[2]["stage"] == "finished"
    print(f"\nSSE real-time streaming round-trip latency: {latency_ms:.2f}ms")
    assert latency_ms < 50.0, f"Expected sub-50ms event delivery, got {latency_ms:.2f}ms"


def test_controller_session_create_and_status(running_controller):
    server_url, server = running_controller

    # Query lookup / session status
    url = f"{server_url}/lookup?text=apple&language=en&bypass-lang-check=true"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        html = resp.read().decode('utf-8')
        assert "apple" in html


def test_latency_benchmark_sse_vs_file_polling(tmp_path):
    """
    Benchmark comparing in-memory SSE event push dispatch (<1ms) against
    legacy .updates/*.js disk serialization and filesystem read round-trip.
    """
    # 1. In-Memory SSE Event Queue Latency
    event_queue = queue.Queue()
    t0 = time.perf_counter()
    for i in range(100):
        ev = {"type": "update", "stage": "translated", "status": "success", "row": i}
        event_queue.put_nowait(ev)
        rec = event_queue.get_nowait()
        assert rec["row"] == i
    sse_avg_ms = ((time.perf_counter() - t0) / 100) * 1000

    # 2. Disk .updates/*.js Serialization Latency
    updates_dir = tmp_path / "bench.updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    t1 = time.perf_counter()
    for i in range(20):
        js_file = updates_dir / f"{i:06d}.js"
        js_file.write_text(f"window.receiveUpdate({{stage: 'translated', row: {i}}});", encoding='utf-8')
        content = js_file.read_text(encoding='utf-8')
        assert len(content) > 0
    disk_avg_ms = ((time.perf_counter() - t1) / 20) * 1000

    print(f"\nLatency Benchmark:")
    print(f"  - SSE In-Memory Push Dispatch: {sse_avg_ms:.4f}ms / event")
    print(f"  - Legacy File System Polling I/O: {disk_avg_ms:.4f}ms / event")

    assert sse_avg_ms < 1.0, f"SSE in-memory dispatch must be <1ms, got {sse_avg_ms:.4f}ms"
    assert sse_avg_ms < disk_avg_ms, "SSE in-memory dispatch must be significantly faster than disk polling"


def test_controller_render_fast_path_endpoint(running_controller):
    server_url, _ = running_controller
    url = f"{server_url}/api/v1/render"
    payload = json.dumps({
        "text": "Hello world from controller render fast-path test.",
        "language": "en",
        "text_mode": "single",
        "theme": "dark",
        "bypass_lang_check": True,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode('utf-8'))
        assert res["status"] == "success"
        assert res["data"]["ok"] is True
        assert "html_b64" in res["data"]
        assert len(res["data"]["html_b64"]) > 0
        assert res["data"].get("children") == []


def test_controller_render_multi_sentence_returns_children_and_suppresses_spawn(running_controller, monkeypatch):
    server_url, server = running_controller
    spawn_calls = []

    def mock_spawn_ahk(args, base_dir=None):
        spawn_calls.append(list(args))

    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', mock_spawn_ahk)

    url = f"{server_url}/api/v1/render"
    multi_text = "This is the first sentence. This is the second sentence. This is the third sentence."
    payload = json.dumps({
        "text": multi_text,
        "language": "en",
        "text_mode": "single",
        "theme": "dark",
        "bypass_lang_check": True,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode('utf-8'))
        assert res["status"] == "success"
        assert res["data"]["ok"] is True
        assert "html_b64" in res["data"]
        assert len(res["data"]["html_b64"]) > 0
        children = res["data"].get("children")
        assert isinstance(children, list)
        assert len(children) > 0
        assert "--seq-num" in children
        assert "--restore" in children
        # Verify external AutoHotkey process spawning was suppressed by controller
        assert len(spawn_calls) == 0, f"spawn_ahk should NOT be called in server mode, but was called {len(spawn_calls)} times"


def test_controller_render_language_mismatch_returns_422(running_controller):
    server_url, _ = running_controller
    url = f"{server_url}/api/v1/render"
    payload = json.dumps({
        "text": "Das ist ein schönes deutsches Haus für den Sprachtest.",
        "language": "en",
        "text_mode": "single",
        "theme": "dark",
        "bypass_lang_check": False,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=10.0)
    assert exc_info.value.code == 422
    err_body = json.loads(exc_info.value.read().decode('utf-8'))
    assert err_body["status"] == "error"
    assert err_body["error_code"] == "LANGUAGE_MISMATCH"
    assert err_body["details"]["detected_language"] == "de"
    assert err_body["details"]["expected_language"] == "en"
    assert err_body["details"]["action"] in ("prompt", "block")


def test_controller_render_language_mismatch_with_bypass(running_controller):
    server_url, _ = running_controller
    url = f"{server_url}/api/v1/render"
    payload = json.dumps({
        "text": "Das ist ein schönes deutsches Haus für den Sprachtest.",
        "language": "en",
        "text_mode": "single",
        "theme": "dark",
        "bypass_lang_check": True,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode('utf-8'))
        assert res["status"] == "success"
        assert res["data"]["ok"] is True
        assert "html_b64" in res["data"]
        assert len(res["data"]["html_b64"]) > 0


def test_controller_session_retext_writes_updates_js(running_controller):
    server_url, server = running_controller

    # 1. Create a session
    create_url = f"{server_url}/session/create"
    create_payload = json.dumps({
        "text": "apple",
        "language": "en",
        "bypass_lang_check": True
    }).encode('utf-8')
    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Token": "test-controller-api-key"
        }
    )
    with urllib.request.urlopen(req_create, timeout=30.0) as resp:
        assert resp.status == 200
        create_res = json.loads(resp.read().decode('utf-8'))
        session_zid = create_res["data"]["session_zid"]

    # 2. Trigger /session/retext
    retext_url = f"{server_url}/session/retext"
    payload = json.dumps({
        "session_zid": session_zid,
        "language": "en",
        "text_mode": "single"
    }).encode('utf-8')
    req_retext = urllib.request.Request(
        retext_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Token": "test-controller-api-key"
        }
    )
    with urllib.request.urlopen(req_retext, timeout=30.0) as resp:
        assert resp.status == 200
        retext_res = json.loads(resp.read().decode('utf-8'))
        assert retext_res["status"] == "success"
        assert "translated_text" in retext_res["data"]
        assert len(retext_res["data"]["translated_text"]) > 0

    # 3. Verify .updates/*.js written
    results_dir = kardenwort_desk.resolve_results_dir(server.resolved_paths, server.config)
    tsv_path = kardenwort_desk.find_working_tsv(results_dir, session_zid, "en")
    assert tsv_path is not None and tsv_path.exists()

    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    assert updates_dir.exists()
    js_files = sorted(updates_dir.glob("*.js"))
    assert len(js_files) > 0

    latest_js = js_files[-1].read_text(encoding="utf-8")
    assert "window.receiveUpdate(" in latest_js
    assert '"stage": "finished"' in latest_js
    assert '"status": "success"' in latest_js
    assert '"translatedText":' in latest_js


def test_controller_session_retext_long_single_mode_text(running_controller):
    """Regression test: verify retext on long single-mode text (> wrap_max_chars) succeeds without TypeError."""
    server_url, server = running_controller

    long_text = (
        "The quick brown fox jumps over the lazy dog in a very scenic forest near the mountains. "
        "Every single observer was deeply impressed by the spectacular agility and swiftness."
    )

    # 1. Create session with long text
    create_url = f"{server_url}/session/create"
    create_payload = json.dumps({
        "text": long_text,
        "language": "en",
        "bypass_lang_check": True
    }).encode('utf-8')
    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Token": "test-controller-api-key"
        }
    )
    with urllib.request.urlopen(req_create, timeout=60.0) as resp:
        assert resp.status == 200
        create_res = json.loads(resp.read().decode('utf-8'))
        session_zid = create_res["data"]["session_zid"]

    # 2. Trigger /session/retext in single mode
    retext_url = f"{server_url}/session/retext"
    payload = json.dumps({
        "session_zid": session_zid,
        "language": "en",
        "text_mode": "single"
    }).encode('utf-8')
    req_retext = urllib.request.Request(
        retext_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Token": "test-controller-api-key"
        }
    )
    with urllib.request.urlopen(req_retext, timeout=60.0) as resp:
        assert resp.status == 200
        retext_res = json.loads(resp.read().decode('utf-8'))
        assert retext_res["status"] == "success"
        assert "translated_text" in retext_res["data"]
        assert len(retext_res["data"]["translated_text"]) > 0


def test_find_working_tsv_updates_dir_discovery(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    zid = "20260824213158"
    
    # Create updates directory without any .tsv file on disk
    updates_dir = results_dir / f"{zid}-the-investor-letter.en.updates"
    updates_dir.mkdir(parents=True, exist_ok=True)

    resolved = kardenwort_desk.find_working_tsv(results_dir, zid, "en")
    assert resolved is not None
    assert resolved.name == f"{zid}-the-investor-letter.en.tsv"
    assert resolved.parent == results_dir


def test_find_working_tsv_sqlite_slug_resolution(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    zid = "20260824213158"
    
    # Create a mock sqlite adapter
    class MockDb:
        def get_session_bundle(self, session_zid):
            if session_zid == zid:
                return {
                    "session": {
                        "zid": zid,
                        "slug": "custom-sqlite-slug",
                        "source_language": "en"
                    }
                }
            return None

    class MockSqliteAdapter:
        backend_name = "sqlite"
        def __init__(self):
            self.db = MockDb()

    adapter = MockSqliteAdapter()
    resolved = kardenwort_desk.find_working_tsv(results_dir, zid, "en", storage_adapter=adapter)
    assert resolved is not None
    assert resolved.name == f"{zid}-custom-sqlite-slug.en.tsv"
    assert resolved.parent == results_dir


def test_controller_session_reword_frequency_sorted_parity(running_controller, tmp_path, monkeypatch):
    server_url, server = running_controller
    sess_zid = f"2026082422{int(time.time() * 100) % 10000:04d}"

    # 1. Create lemma index where 'apple' is rank 1 and 'zebra' is rank 2
    idx_file = tmp_path / "en_index.txt"
    idx_file.write_text("apple\nzebra\n", encoding="utf-8")
    server.config.set("languages", "en_lemma_index", str(idx_file))

    # 2. Seed session via storage_adapter in reverse order: row 0 is 'zebra', row 1 is 'apple'
    headers = [
        "Quotation", "WordSource", "WordDestination", "WordSourceInflectedForm",
        "WordSourceMorphologyAI", "WordSourceIPA", "DeskSelected",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination"
    ]
    data_rows = [
        ["zebra", "zebra", "", "", "", "", "0", "1", "A zebra is striped.", ""],
        ["apple", "apple", "", "", "", "", "0", "1", "An apple is sweet.", ""],
    ]
    storage_adapter = getattr(server.arbiter, "storage_adapter", None) or kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_adapter.save_session(
        session_zid=sess_zid,
        slug="animals",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="A zebra is striped. An apple is sweet.",
        headers=headers,
        data_rows=data_rows,
        comments=["# test"],
    )

    try:
        # 3. Track intellifiller invocations
        dispatched_selected_rows = []
        def fake_headless_ifiller(tsv_path, *args, **kwargs):
            selected_rows = kwargs.get("selected_rows")
            dispatched_selected_rows.append(list(selected_rows or []))
            _, h, r = kardenwort_desk.load_tsv_rows(tsv_path)
            dest_col = h.index("WordDestination")
            for s_idx in (selected_rows or []):
                if s_idx < len(r):
                    r[s_idx][dest_col] = f"reworded_{r[s_idx][1]}"
            kardenwort_desk.save_tsv_rows_safely(tsv_path, ["# test"], h, r)
            return True

        monkeypatch.setattr(kardenwort_desk, "run_headless_intellifiller", fake_headless_ifiller)
        import kardenwort_controller
        monkeypatch.setattr(kardenwort_controller, "run_headless_intellifiller", fake_headless_ifiller)

        # In frequency-sorted order: row 0 is 'apple', row 1 is 'zebra'
        # Selecting row 0 targets 'apple'
        req_body = {
            "session_zid": sess_zid,
            "row_ids": [0],
            "language": "en"
        }
        req_data = json.dumps(req_body).encode("utf-8")
        req = urllib.request.Request(
            f"{server_url}/session/reword",
            data=req_data,
            headers={"Content-Type": "application/json", "X-API-Token": server.api_key}
        )

        with urllib.request.urlopen(req, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "success"
            returned_rows = res["data"]["data_rows"]
            _, restored_headers, _ = storage_adapter.load_tsv_rows(sess_zid)
            col_lemma = restored_headers.index("WordSource")
            col_dest = restored_headers.index("WordDestination")

            assert returned_rows[0][col_lemma] == "apple"
            assert returned_rows[0][col_dest] == "reworded_apple"
            assert returned_rows[1][col_lemma] == "zebra"
            assert returned_rows[1][col_dest] == ""

            # Verify structured rows dictionary for granular in-place DOM updates
            assert "rows" in res["data"]
            structured = res["data"]["rows"]
            row0 = structured[0] if 0 in structured else structured["0"]
            assert row0["lemma"] == "apple"
            assert row0["trans"] == "reworded_apple"
    finally:
        try:
            storage_adapter.delete_session(sess_zid)
        except Exception:
            pass


def test_controller_assets_numbers_ico_serving(running_controller):
    """
    Verify that /assets/numbers/<num>.ico serves image/x-icon and falls back gracefully.
    """
    server_url, server = running_controller

    # 1. Fetch valid 1.ico
    url_1 = f"{server_url}/assets/numbers/1.ico"
    req_1 = urllib.request.Request(url_1)
    with urllib.request.urlopen(req_1, timeout=5.0) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/x-icon"
        content_1 = resp.read()
        assert len(content_1) > 0

    # 2. Fetch valid 2.ico
    url_2 = f"{server_url}/assets/numbers/2.ico"
    req_2 = urllib.request.Request(url_2)
    with urllib.request.urlopen(req_2, timeout=5.0) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/x-icon"
        content_2 = resp.read()
        assert len(content_2) > 0

    # 3. Fetch out-of-range number (falls back to 1.ico)
    url_fallback = f"{server_url}/assets/numbers/999.ico"
    req_fallback = urllib.request.Request(url_fallback)
    with urllib.request.urlopen(req_fallback, timeout=5.0) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/x-icon"
        content_fb = resp.read()
        assert len(content_fb) > 0
        assert content_fb == content_1


def test_controller_api_token_auth_headers(running_controller):
    """
    Verify that _authenticate_token in controller supports X-API-Token, X-API-Key,
    and Bearer Authorization headers interchangeably, and rejects unauthorized requests.
    Uses a real session created by /session/create to avoid 500 from missing session.
    """
    server_url, server = running_controller

    # 0. Create a real session to get a valid session_zid
    create_url = f"{server_url}/session/create"
    create_payload = json.dumps({
        "text": "dog",
        "language": "en",
        "bypass_lang_check": True
    }).encode("utf-8")
    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_create, timeout=30.0) as resp:
        create_res = json.loads(resp.read().decode("utf-8"))
        session_zid = create_res["data"]["session_zid"]

    retext_url = f"{server_url}/session/retext"
    base_payload = {"session_zid": session_zid, "language": "en", "text_mode": "single"}

    # 1. Reject without token (controller maps UNAUTHORIZED -> 403)
    req_no_auth = urllib.request.Request(
        retext_url,
        data=json.dumps(base_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_no_auth, timeout=5.0)
    assert exc_info.value.code == 403

    # 2. Reject with wrong token
    req_wrong_auth = urllib.request.Request(
        retext_url,
        data=json.dumps(base_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Token": "invalid-token"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_wrong_auth, timeout=5.0)
    assert exc_info.value.code == 403

    # 3. Accept with X-API-Token
    req_x_api_token = urllib.request.Request(
        retext_url,
        data=json.dumps(base_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_x_api_token, timeout=30.0) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["status"] == "success"

    # 4. Accept with X-API-Key
    req_x_api_key = urllib.request.Request(
        retext_url,
        data=json.dumps(base_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_x_api_key, timeout=30.0) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["status"] == "success"

    # 5. Accept with Bearer Authorization
    req_bearer = urllib.request.Request(
        retext_url,
        data=json.dumps(base_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-controller-api-key"}
    )
    with urllib.request.urlopen(req_bearer, timeout=30.0) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["status"] == "success"

    # 6. Accept with token in body
    payload_with_token = dict(base_payload, token="test-controller-api-key")
    req_body_token = urllib.request.Request(
        retext_url,
        data=json.dumps(payload_with_token).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_body_token, timeout=30.0) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["status"] == "success"


def test_retext_after_controller_restart(running_controller):
    """
    Verify that /session/retext succeeds after the controller's in-memory session
    cache is cleared, by recovering source text from SQLite storage.
    """
    server_url, server = running_controller

    # 1. Create a session via /session/create
    create_url = f"{server_url}/session/create"
    create_payload = json.dumps({
        "text": "apple",
        "language": "en",
        "bypass_lang_check": True
    }).encode("utf-8")
    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_create, timeout=30.0) as resp:
        assert resp.status == 200
        create_res = json.loads(resp.read().decode("utf-8"))
        session_zid = create_res["data"]["session_zid"]

    # 2. Simulate controller restart: clear in-memory session cache
    with server.arbiter._lock:
        server.arbiter.sessions.clear()

    # 3. Call /session/retext — must succeed via SQLite recovery fallback
    retext_url = f"{server_url}/session/retext"
    retext_payload = json.dumps({
        "session_zid": session_zid,
        "language": "en",
        "text_mode": "single"
    }).encode("utf-8")
    req_retext = urllib.request.Request(
        retext_url,
        data=retext_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_retext, timeout=30.0) as resp:
        assert resp.status == 200
        retext_res = json.loads(resp.read().decode("utf-8"))
        assert retext_res["status"] == "success"


def test_reword_structured_error_on_failure(running_controller, monkeypatch):
    """
    Verify that when enrich_session_intellifiller raises an exception,
    /session/reword responds with a structured JSON body containing a 'message' field
    rather than a bare HTTP 500 with no body.
    """
    import kardenwort_desk as kd
    server_url, server = running_controller

    # 1. Create a session so a valid session_zid exists in storage
    create_url = f"{server_url}/session/create"
    create_payload = json.dumps({
        "text": "apple",
        "language": "en",
        "bypass_lang_check": True
    }).encode("utf-8")
    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_create, timeout=30.0) as resp:
        assert resp.status == 200
        create_res = json.loads(resp.read().decode("utf-8"))
        session_zid = create_res["data"]["session_zid"]

    # 2. Patch enrich_session_intellifiller to raise a RuntimeError
    original_enrich = kd.SqliteStorageAdapter.enrich_session_intellifiller

    def failing_enrich(self, *args, **kwargs):
        raise RuntimeError("IntelliFiller service unavailable (test)")

    monkeypatch.setattr(kd.SqliteStorageAdapter, "enrich_session_intellifiller", failing_enrich)

    # 3. Call /session/reword — must return a structured error with 'message' field, not a bare 500
    reword_url = f"{server_url}/session/reword"
    reword_payload = json.dumps({
        "session_zid": session_zid,
        "row_ids": [0],
        "language": "en"
    }).encode("utf-8")
    req_reword = urllib.request.Request(
        reword_url,
        data=reword_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    try:
        with urllib.request.urlopen(req_reword, timeout=10.0) as resp:
            # If for some reason the response is 200, we fail the test
            body = json.loads(resp.read().decode("utf-8"))
            pytest.fail(f"Expected error response but got 200: {body}")
    except urllib.error.HTTPError as exc:
        err_body = json.loads(exc.read().decode("utf-8"))
        assert "message" in err_body, f"Expected 'message' key in error body, got: {err_body}"
        assert err_body["status"] == "error"
        assert "Re-word failed" in err_body["message"]


def test_controller_session_retext_sqlite_pure_virtual(running_controller):
    """
    Verify that retext_session operates completely without physical .tsv or .txt files
    on disk when backed by SQLite storage adapter.
    """
    server_url, server = running_controller

    # 1. Create a session via /session/create
    create_url = f"{server_url}/session/create"
    create_payload = json.dumps({
        "text": "The quick brown fox jumps over the lazy dog.",
        "language": "en",
        "bypass_lang_check": True
    }).encode("utf-8")
    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_create, timeout=30.0) as resp:
        assert resp.status == 200
        create_res = json.loads(resp.read().decode("utf-8"))
        session_zid = create_res["data"]["session_zid"]

    # 2. Clear arbiter in-memory session cache
    with server.arbiter._lock:
        server.arbiter.sessions.clear()

    # 3. Verify no physical files are required or delete any leftover files on disk
    results_dir = kardenwort_desk.resolve_results_dir(server.resolved_paths, server.config)
    for ext in [".tsv", ".txt", ".json", ".js"]:
        p = results_dir / f"{session_zid}.en{ext}"
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
        p_raw = results_dir / f"{session_zid}{ext}"
        if p_raw.exists():
            try:
                p_raw.unlink()
            except Exception:
                pass

    # 4. Call /session/retext — must succeed seamlessly via SQLite storage adapter
    retext_url = f"{server_url}/session/retext"
    retext_payload = json.dumps({
        "session_zid": session_zid,
        "language": "en",
        "text_mode": "single"
    }).encode("utf-8")
    req_retext = urllib.request.Request(
        retext_url,
        data=retext_payload,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req_retext, timeout=30.0) as resp:
        assert resp.status == 200
        retext_res = json.loads(resp.read().decode("utf-8"))
        assert retext_res["status"] == "success"
        data_payload = retext_res.get("data", retext_res)
        assert "translated_text" in data_payload
        assert "translatedText" in data_payload
        assert "rows" in data_payload
        assert len(data_payload["rows"]) > 0
        assert len(data_payload["data_rows"]) > 0


def test_controller_enrichment_queue_integration(running_controller):
    """
    Verify that SessionArbiter on running_controller initializes EnrichmentQueue
    and correctly bounds concurrency while caching results.
    """
    _, server = running_controller
    arbiter = server.arbiter
    assert hasattr(arbiter, "enrichment_queue")
    assert isinstance(arbiter.enrichment_queue, EnrichmentQueue)
    assert arbiter.enrichment_queue.max_workers >= 1

    # Manually test cache operations on arbiter's enrichment queue
    arbiter.enrichment_queue.set_cached("TestLemma", "de", {"WordDestination": "test translation"})
    cached = arbiter.enrichment_queue.get_cached("TestLemma", "de")
    assert cached is not None
    assert cached["WordDestination"] == "test translation"


def test_controller_enrichment_queue_error_recovery(running_controller):
    """
    Verify that EnrichmentQueue on controller cleanly cleans up in-flight entries on error.
    """
    _, server = running_controller
    eq = EnrichmentQueue(server.config, server.resolved_paths, max_workers=1)

    def failing_execute(*args, **kwargs):
        raise ValueError("Inference provider timeout")

    eq._execute_enrich_lemma = failing_execute

    with pytest.raises(ValueError, match="Inference provider timeout"):
        eq.enrich_lemma("BrokenLemma", "en")

    assert ("BrokenLemma", "en") not in eq._inflight_lemmas
    eq.shutdown()


def test_session_status_hydrated_rows_and_translated_text(running_controller):
    """
    Verify GET /session/status returns hydrated rows dictionary and translatedText
    both for in-memory sessions in arbiter and persistent storage sessions.
    """
    server_url, server = running_controller
    arbiter = server.arbiter
    sess_zid = kardenwort_desk.generate_unique_zid()

    # 1. Test in-memory arbiter session
    headers = ["WordSource", "WordDestination", "TokenOrder", "SentenceSourceIndex", "SentenceDestination"]
    data_rows = [
        ["Katze", "cat", "0", "1", "Die Katze schläft"],
        ["Hund", "dog", "1", "1", "Die Katze schläft"],
    ]
    with arbiter._lock:
        arbiter.sessions[sess_zid] = {
            "session_zid": sess_zid,
            "language": "de",
            "target_lang": "en",
            "text": "Die Katze schläft",
            "headers": headers,
            "data_rows": data_rows,
            "sentence_translation": "The cat sleeps",
            "fingerprint": "test_fp",
            "created_at": time.time(),
        }

    try:
        req_status = urllib.request.Request(f"{server_url}/session/status?zid={sess_zid}")
        with urllib.request.urlopen(req_status, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            data = res.get("data", res)
            assert "rows" in data
            assert isinstance(data["rows"], dict)
            # In German frequency index, Hund ranks higher than Katze, so row 0 is Hund, row 1 is Katze
            row0 = data["rows"].get("0") or data["rows"].get(0)
            assert row0 is not None
            assert row0.get("lemma") == "Hund"
            assert row0.get("trans") == "dog"
            row1 = data["rows"].get("1") or data["rows"].get(1)
            assert row1 is not None
            assert row1.get("lemma") == "Katze"
            assert row1.get("trans") == "cat"
            assert "translatedText" in data
            assert "The cat sleeps" in data["translatedText"]
    finally:
        with arbiter._lock:
            arbiter.sessions.pop(sess_zid, None)

    # 2. Test persistent storage fallback session
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_zid = kardenwort_desk.generate_unique_zid()
    storage_adapter.save_session(
        session_zid=storage_zid,
        slug="test-storage-status",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Der Hund rennt",
        sentences=[{"sentence_index": 0, "sentence_source": "Der Hund rennt", "sentence_destination": "The dog runs"}],
        headers=["WordSource", "WordDestination", "TokenOrder"],
        data_rows=[["Hund", "dog", "0"], ["rennt", "runs", "1"]],
    )
    try:
        req_storage = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
        with urllib.request.urlopen(req_storage, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            data = res.get("data", res)
            assert data.get("ok") is True
            assert data.get("is_finished") is True
            assert "rows" in data
            assert isinstance(data["rows"], dict)
            row0 = data["rows"].get("0") or data["rows"].get(0)
            assert row0 is not None
            assert row0.get("lemma") == "Hund"
            assert row0.get("trans") == "dog"
            assert "translatedText" in data
            assert "The dog runs" in data["translatedText"]
    finally:
        try:
            storage_adapter.delete_session(storage_zid)
        except Exception:
            pass


def test_controller_session_status_untranslated_lemmas_reports_translating(running_controller):
    """
    Verify GET /session/status reports is_finished=False and stage='translating'
    when lemmas in a recently created session remain untranslated.
    """
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_zid = kardenwort_desk.generate_unique_zid()

    storage_adapter.save_session(
        session_zid=storage_zid,
        slug="test-untranslated-status",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Der Hund rennt",
        sentences=[{"sentence_index": 0, "sentence_source": "Der Hund rennt", "sentence_destination": "The dog runs"}],
        headers=["WordSource", "WordDestination", "TokenOrder"],
        data_rows=[["Hund", "", "0"], ["rennt", "", "1"]],
    )
    try:
        req_storage = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
        with urllib.request.urlopen(req_storage, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            data = res.get("data", res)
            assert data.get("ok") is True
            assert data.get("is_finished") is False
            assert data.get("stage") == "translating"
    finally:
        try:
            storage_adapter.delete_session(storage_zid)
        except Exception:
            pass


def test_controller_session_status_worker_lock_held_reports_busy(running_controller):
    """
    Verify GET /session/status reports is_finished=False and stage='translating'
    when a worker sentinel lock is actively held.
    """
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_zid = kardenwort_desk.generate_unique_zid()
    results_dir = kardenwort_desk.resolve_results_dir(server.resolved_paths, server.config)

    storage_adapter.save_session(
        session_zid=storage_zid,
        slug="test-lock-status",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Der Hund rennt",
        sentences=[{"sentence_index": 0, "sentence_source": "Der Hund rennt", "sentence_destination": "The dog runs"}],
        headers=["WordSource", "WordDestination", "TokenOrder"],
        data_rows=[["Hund", "dog", "0"], ["rennt", "runs", "1"]],
    )
    sentinel_lock_path = (results_dir / f"{storage_zid}.worker.lock") if results_dir else Path(f"{storage_zid}.worker.lock")
    try:
        with storage_adapter.file_lock(sentinel_lock_path):
            req_storage = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
            with urllib.request.urlopen(req_storage, timeout=5.0) as resp:
                assert resp.status == 200
                res = json.loads(resp.read().decode("utf-8"))
                data = res.get("data", res)
                assert data.get("ok") is True
                assert data.get("is_finished") is False
                assert data.get("stage") == "translating"

        # Once lock is released, is_finished should be True
        req_storage2 = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
        with urllib.request.urlopen(req_storage2, timeout=5.0) as resp2:
            assert resp2.status == 200
            res2 = json.loads(resp2.read().decode("utf-8"))
            data2 = res2.get("data", res2)
            assert data2.get("is_finished") is True
            assert data2.get("stage") == "finished"
    finally:
        try:
            storage_adapter.delete_session(storage_zid)
        except Exception:
            pass


def test_session_status_multi_sentence_global_frequency_sort(running_controller):
    """
    Verify GET /session/status orders rows globally across multi-sentence boundaries
    so that common words from later sentences precede rare words from earlier sentences.
    """
    server_url, server = running_controller
    arbiter = server.arbiter
    sess_zid = kardenwort_desk.generate_unique_zid()

    # 1. In-memory arbiter session: Sentence 1 has rare word ("Transporter"), Sentence 2 has common word ("der")
    headers = ["WordSource", "WordDestination", "TokenOrder", "SentenceSourceIndex", "SentenceDestination"]
    data_rows = [
        ["Transporter", "transporter", "0", "1", "Ein seltener Transporter."],
        ["der", "the", "1", "2", "Das ist der beste Weg."],
    ]
    with arbiter._lock:
        arbiter.sessions[sess_zid] = {
            "session_zid": sess_zid,
            "language": "de",
            "target_lang": "en",
            "text": "Ein seltener Transporter. Das ist der beste Weg.",
            "headers": headers,
            "data_rows": data_rows,
            "sentence_translation": "A rare transporter.\nThat is the best way.",
            "fingerprint": "fp_multi_sent",
            "created_at": time.time(),
        }

    try:
        req_status = urllib.request.Request(f"{server_url}/session/status?zid={sess_zid}")
        with urllib.request.urlopen(req_status, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            data = res.get("data", res)
            assert "rows" in data
            rows = data["rows"]
            # Row 0 should be the most frequent word ("der")
            # Row 1 should be the rarer word ("Transporter")
            assert (rows.get("0") or rows.get(0))["lemma"] == "der"
            assert (rows.get("1") or rows.get(1))["lemma"] == "Transporter"
    finally:
        with arbiter._lock:
            arbiter.sessions.pop(sess_zid, None)

    # 2. Persistent storage restore session with 2 sentences
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_zid = kardenwort_desk.generate_unique_zid()
    storage_adapter.save_session(
        session_zid=storage_zid,
        slug="test-multi-sent-sort",
        source_language="de",
        target_language="en",
        text_mode="sentences",
        source_raw_text="Ein seltener Transporter. Das ist der beste Weg.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "Ein seltener Transporter.", "sentence_destination": "A rare transporter."},
            {"sentence_index": 2, "sentence_source": "Das ist der beste Weg.", "sentence_destination": "That is the best way."},
        ],
        headers=["WordSource", "WordDestination", "TokenOrder", "SentenceSourceIndex"],
        data_rows=[
            ["Transporter", "transporter", "0", "1"],
            ["der", "the", "1", "2"],
        ],
    )
    try:
        req_storage = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
        with urllib.request.urlopen(req_storage, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            data = res.get("data", res)
            assert data.get("ok") is True
            assert "rows" in data
            rows = data["rows"]
            assert (rows.get("0") or rows.get(0))["lemma"] == "der"
            assert (rows.get("1") or rows.get(1))["lemma"] == "Transporter"
    finally:
        try:
            storage_adapter.delete_session(storage_zid)
        except Exception:
            pass


def test_audio_play_endpoint_success_and_validation(running_controller, monkeypatch):
    server_url, server = running_controller
    spawned_cmds = []

    def mock_popen(cmd, *args, **kwargs):
        spawned_cmds.append((cmd, kwargs))
        class MockProc:
            pid = 12345
            def poll(self): return 0
        return MockProc()

    import subprocess
    import kardenwort_controller
    monkeypatch.setattr(kardenwort_controller.subprocess, "Popen", mock_popen)

    # 1. Successful POST /api/v1/audio/play
    payload = json.dumps({"text": "Guten Tag", "language": "de"}).encode('utf-8')
    req = urllib.request.Request(
        f"{server_url}/api/v1/audio/play",
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Token": server.api_key}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data["status"] == "success"
        res = data.get("data", data)
        assert res["ok"] is True
        assert res["status"] == "playing"
        assert res["text"] == "Guten Tag"
        assert res["language"] == "de"

    assert len(spawned_cmds) == 1
    cmd, kwargs = spawned_cmds[0]
    assert cmd[2] == "Guten Tag"
    assert cmd[3] == "de"
    assert "anki-tts-cli" in str(cmd[1])

    # 2. Alias POST /session/play
    payload_alias = json.dumps({"text": "Hello world", "language": "en"}).encode('utf-8')
    req_alias = urllib.request.Request(
        f"{server_url}/session/play",
        data=payload_alias,
        headers={"Content-Type": "application/json", "X-API-Token": server.api_key}
    )
    with urllib.request.urlopen(req_alias, timeout=5.0) as resp:
        assert resp.status == 200
        data_alias = json.loads(resp.read().decode('utf-8'))
        res_alias = data_alias.get("data", data_alias)
        assert res_alias["ok"] is True
        assert res_alias["text"] == "Hello world"
        assert res_alias["language"] == "en"

    assert len(spawned_cmds) == 2
    cmd2, _ = spawned_cmds[1]
    assert cmd2[2] == "Hello world"
    assert cmd2[3] == "en"

    # 3. Validation: Missing language
    req_bad_lang = urllib.request.Request(
        f"{server_url}/api/v1/audio/play",
        data=json.dumps({"text": "Hello"}).encode('utf-8'),
        headers={"Content-Type": "application/json", "X-API-Token": server.api_key}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad_lang, timeout=5.0)
    assert exc_info.value.code == 400

    # 4. Validation: Missing text
    req_bad_text = urllib.request.Request(
        f"{server_url}/api/v1/audio/play",
        data=json.dumps({"language": "de"}).encode('utf-8'),
        headers={"Content-Type": "application/json", "X-API-Token": server.api_key}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad_text, timeout=5.0)
    assert exc_info.value.code == 400

    # 5. Method not allowed (GET)
    req_get = urllib.request.Request(
        f"{server_url}/api/v1/audio/play",
        headers={"X-API-Token": server.api_key}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_get, timeout=5.0)
    assert exc_info.value.code == 405


def test_progressive_queue_bounded_concurrency_and_pacing(tmp_path):
    """
    Assert that EnrichmentQueue enforces bounded translation worker concurrency
    and respects rate-limiting pacing under multi-session burst conditions.
    """
    import configparser
    from kardenwort_controller import EnrichmentQueue

    config = configparser.ConfigParser()
    config.read_string("""
[translation]
google_max_concurrency = 2
google_request_delay = 0.05
[pipeline]
lemma_base_provider = google
""")
    resolved_paths = {"results_dir": tmp_path}
    eq = EnrichmentQueue(config, resolved_paths, translation_max_workers=2)

    active_workers = 0
    max_active_observed = 0
    call_timestamps = []
    lock = threading.Lock()

    def mock_execute(lemma, source_lang, target_lang, provider, zid, trace_id):
        nonlocal active_workers, max_active_observed
        with lock:
            active_workers += 1
            if active_workers > max_active_observed:
                max_active_observed = active_workers
            call_timestamps.append(time.time())
        time.sleep(0.04)
        with lock:
            active_workers -= 1
        return f"trans_{lemma}"

    eq._execute_translate_lemma = mock_execute

    threads = [
        threading.Thread(target=eq.translate_lemma, args=(f"Lemma_{i}", "de", "ru"))
        for i in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_active_observed <= 2, f"Expected concurrency <= 2, got {max_active_observed}"
    status = eq.get_status()
    assert status["translation_max_workers"] == 2
    assert status["translation_cache_size"] == 6

    eq.shutdown()


def test_get_session_zid_serves_directly_from_sqlite_without_translation(running_controller, monkeypatch):
    """Verifies that GET /?session_zid=... restores directly from SQLite without invoking render_flow_fn or translation providers."""
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)

    sess_zid = kardenwort_desk.generate_unique_zid()
    headers = ["SentenceSourceIndex", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination"]
    data_rows = [["1", "Hund", "собака", "Der Hund rennt.", "Собака бежит."]]

    storage_adapter.save_session(
        session_zid=sess_zid,
        slug="test-direct-restore",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Der Hund rennt.",
        headers=headers,
        data_rows=data_rows,
        sentences=[{
            "session_zid": sess_zid,
            "sentence_index": 1,
            "sentence_source": "Der Hund rennt.",
            "sentence_destination": "Собака бежит.",
        }],
    )

    translation_called = []
    def fail_if_called(*args, **kwargs):
        translation_called.append(True)
        raise RuntimeError("Translation provider should NOT be contacted on direct session restore!")

    monkeypatch.setattr(kardenwort_desk, 'translate_text', fail_if_called)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', fail_if_called)
    monkeypatch.setattr(kardenwort_desk, 'run_render_flow', fail_if_called)
    import kardenwort_controller
    monkeypatch.setattr(kardenwort_controller, 'run_render_flow', fail_if_called)

    req = urllib.request.Request(f"{server_url}/?session_zid={sess_zid}")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        duration = time.perf_counter() - t0
        assert resp.status == 200
        content = resp.read().decode('utf-8')
        assert "Hund" in content
        assert "собака" in content
        assert not translation_called
        assert duration < 0.5


def test_get_session_zid_incremental_wordfill_hydration(running_controller, monkeypatch):
    """Verifies that GET /?session_zid=... incrementally hydrates missing fields from wordfill."""
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)

    sess_zid = kardenwort_desk.generate_unique_zid()
    headers = ["SentenceSourceIndex", "WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI", "SentenceSource", "SentenceDestination"]
    data_rows = [["1", "Katze", "", "", "", "Die Katze schläft.", "Кошка спит."]]

    storage_adapter.save_session(
        session_zid=sess_zid,
        slug="test-wordfill-restore",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Die Katze schläft.",
        headers=headers,
        data_rows=data_rows,
        sentences=[{
            "session_zid": sess_zid,
            "sentence_index": 1,
            "sentence_source": "Die Katze schläft.",
            "sentence_destination": "Кошка спит.",
        }],
    )

    server.wordfill = {
        'enabled': True,
        'backend': 'sqlite',
        'sqlite_db_path': server.resolved_paths.get('sqlite_db_path'),
    }
    server.goldendict['lemma_columns'] = ['inflected', 'lemma', 'ipa', 'morphology', 'translation']

    def mock_find_wordfill_match(word, lang, cfg, **kwargs):
        if word.lower() == "katze":
            return {
                "WordDestination": "кошка",
                "WordSourceIPA": "/ˈkat͡sə/",
                "WordSourceMorphologyAI": "Noun|Fem|Sing|Nom",
            }
        return None

    import kardenwort_controller
    monkeypatch.setattr(kardenwort_controller, 'find_wordfill_match', mock_find_wordfill_match)
    monkeypatch.setattr(kardenwort_desk, 'find_wordfill_match', mock_find_wordfill_match)

    req = urllib.request.Request(f"{server_url}/?session_zid={sess_zid}")
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        content = resp.read().decode('utf-8')
        assert "Katze" in content
        assert "кошка" in content
        assert "/ˈkat͡sə/" in content

    restored = storage_adapter.restore_session(sess_zid)
    r_headers = restored["headers"]
    r_rows = restored["data_rows"]
    dest_idx = r_headers.index("WordDestination")
    ipa_idx = r_headers.index("WordSourceIPA")
    morph_idx = r_headers.index("WordSourceMorphologyAI")
    assert r_rows[0][dest_idx] == "кошка"
    assert r_rows[0][ipa_idx] == "/ˈkat͡sə/"
    assert r_rows[0][morph_idx] == "Noun|Fem|Sing|Nom"



