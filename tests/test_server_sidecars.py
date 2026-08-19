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
from http.server import ThreadingHTTPServer

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


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


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

    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), APIRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "test-api-key"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()

    # Supervisor initialized but disabled for unit probe
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

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
def test_http_server_shutdown_lifecycle():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), APIRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "test-key"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()

    stopped_flag = [False]
    class MockSupervisor:
        def get_service_status(self):
            return {"mock": {"port": 1234, "healthy": True}}
        def stop(self):
            stopped_flag[0] = True

    server.supervisor = MockSupervisor()

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    # Issue POST /api/v1/shutdown
    url = f"http://127.0.0.1:{port}/api/v1/shutdown"
    payload = json.dumps({"token": "test-key"}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data["status"] == "success"
        assert "zid" in data["data"]

    # Give server time to execute shutdown callback
    time.sleep(0.3)
    assert stopped_flag[0] is True


# ---------------------------------------------------------------------------
# 4. AHK Reload Simulation Test
# ---------------------------------------------------------------------------
def test_ahk_reload_lifecycle_simulation():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    port = get_free_port()

    # 1. Start 1st server instance
    server1 = ThreadingHTTPServer(('127.0.0.1', port), APIRequestHandler)
    server1.allow_reuse_address = True
    server1.daemon_threads = True
    server1.config = config
    server1.resolved_paths = resolved_paths
    server1.goldendict = goldendict
    server1.api_key = "reload-key"
    server1.seq_counter = 0
    server1.seq_lock = threading.Lock()
    server1.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    t1 = threading.Thread(target=server1.serve_forever, daemon=True)
    t1.start()
    time.sleep(0.1)

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
    time.sleep(0.1)

    # 3. Start 2nd server instance on the exact same port (simulating new process boot)
    server2 = ThreadingHTTPServer(('127.0.0.1', port), APIRequestHandler)
    server2.allow_reuse_address = True
    server2.daemon_threads = True
    server2.config = config
    server2.resolved_paths = resolved_paths
    server2.goldendict = goldendict
    server2.api_key = "reload-key"
    server2.seq_counter = 0
    server2.seq_lock = threading.Lock()
    server2.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    t2 = threading.Thread(target=server2.serve_forever, daemon=True)
    t2.start()
    time.sleep(0.1)

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

