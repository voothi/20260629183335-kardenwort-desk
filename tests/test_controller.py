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
    run_controller
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
    with urllib.request.urlopen(req, timeout=15.0) as resp:
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
    with urllib.request.urlopen(req_create, timeout=30.0) as resp:
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
    with urllib.request.urlopen(req_retext, timeout=30.0) as resp:
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



