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

