from tests.browser_test_server import create_browser_test_server, _BrowserTestHTTPServer
from tests.thread_support import Worker, wait_for_http, finish_workers
import sys
import os
import json
import time
import socket
import threading
import urllib.request
import urllib.error
import configparser
import pytest
from pathlib import Path

import kardenwort_desk
from kardenwort_controller import (
    ProcessSupervisor,
    SidecarService,
    WindowsJobObject,
)
from http_server import (
    APIRequestHandler,
    cmd_server,
)




# ---------------------------------------------------------------------------
# 1. Dynamic Port Parsing Unit Tests
# ---------------------------------------------------------------------------
def test_supervisor_dynamic_port_parsing_default():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    sup = ProcessSupervisor(config, resolved_paths, enabled=False)

    status = sup.get_service_status()
    assert "spacy" in status
    assert "translation" in status
    assert "intellifiller" in status
    assert status["spacy"]["port"] == 8081
    assert status["translation"]["port"] == 8082
    assert status["intellifiller"]["port"] == 8083


def test_supervisor_dynamic_port_parsing_custom():
    desk_dir = Path(__file__).resolve().parent.parent
    config = configparser.ConfigParser()
    config.add_section("services")
    config.set("services", "spacy_server_url", "http://127.0.0.1:9081")
    config.set("services", "translation_server_url", "http://127.0.0.1:9082")
    config.set("services", "intellifiller_server_url", "http://127.0.0.1:9083")

    sup = ProcessSupervisor(config, {}, enabled=False)
    status = sup.get_service_status()
    assert status["spacy"]["port"] == 9081
    assert status["translation"]["port"] == 9082
    assert status["intellifiller"]["port"] == 9083


# ---------------------------------------------------------------------------
# 2. HTTP Server Sidecar Health Integration Tests
# ---------------------------------------------------------------------------
class DummyArgs:
    def __init__(self, port, no_sidecars=True, config=None):
        self.port = port
        self.host = "127.0.0.1"
        self.no_sidecars = no_sidecars
        self.config = config


def test_http_server_health_with_supervisor():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    server = create_browser_test_server(APIRequestHandler)
    port = server.server_port
    server.allow_reuse_address = True
    server.daemon_threads = False
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "test-api-key"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()

    # Supervisor initialized but disabled for unit probe
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    server_thread = server.start()

    try:
        url = f"http://127.0.0.1:{port}/api/v1/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode('utf-8'))
            assert data["status"] == "success"
            assert data["data"]["ok"] is True
            assert data["data"]["status"] == "running"
            assert "services" in data["data"]
            services = data["data"]["services"]
            assert "spacy" in services
            assert "translation" in services
            assert "intellifiller" in services
            assert services["spacy"]["port"] == 8081
            assert services["translation"]["port"] == 8082
            assert services["intellifiller"]["port"] == 8083
    finally:
        if hasattr(server, 'supervisor') and server.supervisor:
            server.supervisor.stop()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 3. Shutdown & Clean Teardown Lifecycle Tests
# ---------------------------------------------------------------------------
def test_http_server_shutdown_lifecycle(monkeypatch):
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    server = create_browser_test_server(APIRequestHandler)
    port = server.server_port
    server.allow_reuse_address = True
    server.daemon_threads = False
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "test-key"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()

    stopped_flag = [False]
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)
    original_close = WindowsJobObject.close

    def observed_close(job):
        result = original_close(job)
        if job is server.supervisor.job:
            stopped_flag[0] = True
        return result

    monkeypatch.setattr(WindowsJobObject, 'close', observed_close)

    server_thread = server.start()

    # Issue POST /api/v1/shutdown
    url = f"http://127.0.0.1:{port}/api/v1/shutdown"
    payload = json.dumps({"token": "test-key"}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data["status"] == "success"
        assert "zid" in data["data"]

    # Observe successful completion of the shutdown callback and owned disposal.
    assert server.closed_event.wait(3), server.errors
    assert stopped_flag[0] is True


# ---------------------------------------------------------------------------
# 4. AHK Reload Simulation Test
# ---------------------------------------------------------------------------
def test_ahk_reload_lifecycle_simulation():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    # 1. Start the owned first listener.
    server1 = create_browser_test_server(APIRequestHandler)
    port = server1.server_port
    server1.allow_reuse_address = True
    server1.daemon_threads = False
    server1.config = config
    server1.resolved_paths = resolved_paths
    server1.goldendict = goldendict
    server1.api_key = "reload-key"
    server1.seq_counter = 0
    server1.seq_lock = threading.Lock()
    server1.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    t1 = server1.start()

    # Verify server1 is responding
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200

    # 2. Simulate AHK reload: Trigger POST /api/v1/shutdown
    shut_req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/shutdown",
        data=json.dumps({"token": "reload-key"}).encode('utf-8'),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(shut_req) as resp:
        assert resp.status == 200

    t1.join(timeout=2.0)
    assert not t1.is_alive()
    assert server1.closed_event.wait(3), server1.errors

    # 3. Start 2nd server instance on the exact same port (simulating new process boot)
    server2 = _BrowserTestHTTPServer(('127.0.0.1', port), APIRequestHandler)
    server2.allow_reuse_address = True
    server2.daemon_threads = False
    server2.config = config
    server2.resolved_paths = resolved_paths
    server2.goldendict = goldendict
    server2.api_key = "reload-key"
    server2.seq_counter = 0
    server2.seq_lock = threading.Lock()
    server2.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    t2 = server2.start()

    try:
        # Verify server2 is running cleanly on the same port
        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health")
        with urllib.request.urlopen(req2) as resp2:
            assert resp2.status == 200
            data2 = json.loads(resp2.read().decode('utf-8'))
            assert data2["data"]["ok"] is True
    finally:
        server2.shutdown()
        server2.server_close()
        t2.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 5. Interpreter Prioritization & Resolution Tests
# ---------------------------------------------------------------------------
def test_supervisor_interpreter_prioritization_intellifiller():
    desk_dir = Path(__file__).resolve().parent.parent
    config = configparser.ConfigParser()
    
    # 1. When intellifiller_python is provided, it must be prioritized over kardenwort_python
    resolved_paths_priority = {
        'intellifiller_python': Path('C:/Custom/PythonIntelli/python.exe'),
        'kardenwort_python': Path('C:/Custom/PythonSpacy/python.exe'),
        'deep_translator_python': Path('C:/Custom/PythonTrans/python.exe'),
    }
    sup_priority = ProcessSupervisor(config, resolved_paths_priority, enabled=False)
    assert sup_priority.services["intellifiller"].launch_cmd[0] == str(Path('C:/Custom/PythonIntelli/python.exe'))
    assert sup_priority.services["spacy"].launch_cmd[0] == str(Path('C:/Custom/PythonSpacy/python.exe'))
    assert sup_priority.services["translation"].launch_cmd[0] == str(Path('C:/Custom/PythonTrans/python.exe'))

    # 2. When intellifiller_python is omitted, fallback to kardenwort_python
    resolved_paths_fallback = {
        'kardenwort_python': Path('C:/Custom/PythonSpacy/python.exe'),
    }
    sup_fallback = ProcessSupervisor(config, resolved_paths_fallback, enabled=False)
    assert sup_fallback.services["intellifiller"].launch_cmd[0] == str(Path('C:/Custom/PythonSpacy/python.exe'))
    assert sup_fallback.services["spacy"].launch_cmd[0] == str(Path('C:/Custom/PythonSpacy/python.exe'))
    assert sup_fallback.services["translation"].launch_cmd[0] == sys.executable

    # 3. When no interpreter is configured, fallback to sys.executable
    sup_empty = ProcessSupervisor(config, {}, enabled=False)
    assert sup_empty.services["intellifiller"].launch_cmd[0] == sys.executable
    assert sup_empty.services["spacy"].launch_cmd[0] == sys.executable
    assert sup_empty.services["translation"].launch_cmd[0] == sys.executable


# ---------------------------------------------------------------------------
# 6. Translation Supervisor CLI Args & Rate Limiter / Cache Integration Tests
# ---------------------------------------------------------------------------
def test_supervisor_translation_cli_args_parsing():
    config = configparser.ConfigParser()
    config.add_section("services")
    config.set("services", "translation_server_url", "http://127.0.0.1:8082")
    config.add_section("translation")
    config.set("translation", "google_max_concurrency", "2")
    config.set("translation", "google_request_delay", "0.5")
    config.set("translation", "enable_translation_cache", "false")
    config.set("translation", "cache_size", "5000")
    config.set("translation", "auto_provider_failover", "true")

    sup = ProcessSupervisor(config, {}, enabled=False)
    cmd = sup.services["translation"].launch_cmd
    assert "--google-concurrency" in cmd
    assert cmd[cmd.index("--google-concurrency") + 1] == "2"
    assert "--google-delay" in cmd
    assert cmd[cmd.index("--google-delay") + 1] == "0.5"
    assert "--no-cache" in cmd
    assert "--cache-size" in cmd
    assert cmd[cmd.index("--cache-size") + 1] == "5000"
    assert "--auto-failover" in cmd


def test_supervisor_translation_cli_args_argos_warmup_and_concurrency():
    config = configparser.ConfigParser()
    config.add_section("services")
    config.set("services", "translation_server_url", "http://127.0.0.1:8082")
    config.add_section("translation")
    config.set("translation", "warmup_argos", "true")
    config.set("translation", "argos_concurrency", "4")

    sup = ProcessSupervisor(config, {}, enabled=False)
    cmd = sup.services["translation"].launch_cmd
    assert "--warmup-argos" in cmd
    assert "--argos-concurrency" in cmd
    assert cmd[cmd.index("--argos-concurrency") + 1] == "4"

    # Test opt-out with warmup_argos = false
    config_nowarm = configparser.ConfigParser()
    config_nowarm.add_section("services")
    config_nowarm.set("services", "translation_server_url", "http://127.0.0.1:8082")
    config_nowarm.add_section("translation")
    config_nowarm.set("translation", "warmup_argos", "false")

    sup_nowarm = ProcessSupervisor(config_nowarm, {}, enabled=False)
    cmd_nowarm = sup_nowarm.services["translation"].launch_cmd
    assert "--no-warmup-argos" in cmd_nowarm
    assert "--warmup-argos" not in cmd_nowarm


def test_simulated_swarm_concurrent_translation_requests():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "20241122093311-deep-translator"))
    import translate_server
    from translate_server import TranslationHTTPServer, TranslationRequestHandler
    from unittest.mock import patch

    from tests.controller_test_support import OwnedTranslationServer
    server = create_browser_test_server(TranslationRequestHandler, OwnedTranslationServer,
        google_delay=0.01, google_concurrency=2, cache_size=1000, enable_cache=True)
    port = server.server_port
    server.start()

    server_url = f"http://127.0.0.1:{port}"

    try:
        # Simulate 20 concurrent requests across worker threads translating repeated lemmas
        num_requests = 20
        results = []
        errors = []

        with patch.object(TranslationRequestHandler, "_translate_google", side_effect=lambda text, *args, **kwargs: f"[TR] {text}"):
            def worker_task(idx):
                word = "Baum" if idx % 2 == 0 else "Himmel"
                payload = {
                    "text": word,
                    "source": "de",
                    "target": "en",
                    "provider": "google",
                    "zid": f"2026082412000{idx:02d}",
                    "trace_id": f"trace-{idx}"
                }
                try:
                    req = urllib.request.Request(
                        f"{server_url}/translate",
                        data=json.dumps(payload).encode('utf-8'),
                        headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        results.append(data)
                except Exception as e:
                    errors.append(e)

            workers = [Worker(lambda index=index: worker_task(index)) for index in range(num_requests)]
            finish_workers(workers)

        assert len(errors) == 0
        assert len(results) == num_requests
        cached_results = [r for r in results if r.get("cached") is True]
        assert len(cached_results) > 0
        for r in results:
            assert r["status"] == "success"
            assert r["translated_text"] in ("[TR] Baum", "[TR] Himmel")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 7. Unified Controller Delegation & Session Status Test (Task 3.2)
# ---------------------------------------------------------------------------
def test_cmd_server_launches_supervisor_and_serves_session_endpoints(tmp_path, monkeypatch):
    """
    Verify cmd_server starts the unified controller daemon with ProcessSupervisor,
    registers /session/status and /session/queue/status, and shuts down cleanly.
    """
    from tests.test_controller_lifetime import real_controller
    with real_controller(tmp_path, monkeypatch, entrypoint=cmd_server) as (server, server_worker, port):
        server_thread = server_worker.thread
        wait_for_http(f"http://127.0.0.1:{port}/api/v1/health", server_thread.is_alive)
        # 1. Health probe includes status, services map, and controller info
        health_req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health")
        with urllib.request.urlopen(health_req, timeout=5.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode('utf-8'))
            assert data.get("status") == "success"
            res = data.get("data", {})
            assert res.get("ok") is True
            assert res.get("status") == "running"
            assert "services" in res
            assert "controller" in res
            assert res["controller"]["port"] == port

        # 2. Session queue status is available on port 18335 / unified port
        queue_req = urllib.request.Request(f"http://127.0.0.1:{port}/session/queue/status")
        with urllib.request.urlopen(queue_req, timeout=5.0) as resp:
            assert resp.status == 200
            qdata = json.loads(resp.read().decode('utf-8'))
            assert qdata.get("status") == "success"

        # 3. Session status query is active (returns 404 for unknown ZID rather than unknown route)
        api_token = server.api_key
        non_existent_zid = f"999999{kardenwort_desk.generate_unique_zid()[6:]}"
        status_req = urllib.request.Request(f"http://127.0.0.1:{port}/session/status?zid={non_existent_zid}",
                                          headers={"X-API-Token": api_token})
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(status_req, timeout=5.0)
        assert exc_info.value.code == 404

        # 4. Clean shutdown via POST /api/v1/shutdown
        shut_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/shutdown",
            data=json.dumps({"token": api_token}).encode('utf-8'),
            headers={"Content-Type": "application/json", "X-API-Token": api_token}
        )
        with urllib.request.urlopen(shut_req, timeout=5.0) as resp:
            assert resp.status == 200

        server_worker.result(timeout=3.0)

