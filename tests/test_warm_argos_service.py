import sys
import time
import json
import threading
import urllib.request
import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock
from http.server import ThreadingHTTPServer
import pytest

import kardenwort_desk
from kardenwort_desk import (
    query_translation_server,
    run_argos_translation,
)
from kardenwort_controller import (
    ProcessSupervisor,
    SidecarService,
    ControllerRequestHandler,
)

DEEP_TRANSLATOR_DIR = Path(__file__).resolve().parent.parent.parent / "20241122093311-deep-translator"
if str(DEEP_TRANSLATOR_DIR) not in sys.path:
    sys.path.insert(0, str(DEEP_TRANSLATOR_DIR))
FORK_DIR = DEEP_TRANSLATOR_DIR / "20260209094544-deep-translator"
if str(FORK_DIR) not in sys.path:
    sys.path.insert(0, str(FORK_DIR))

import translate_server
from translate_server import TranslationHTTPServer, TranslationRequestHandler


@pytest.fixture(scope="module")
def warm_argos_test_server():
    server = TranslationHTTPServer(("127.0.0.1", 0), TranslationRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    server_url = f"http://127.0.0.1:{port}"
    yield server_url

    server.shutdown()
    server.server_close()


def test_warm_argos_sub_50ms_query_latency(warm_argos_test_server):
    """
    Assert that warm in-memory Argos translations via persistent HTTP microservice
    execute and return within sub-50ms without reloading model weights.
    """
    with patch.object(TranslationRequestHandler, "_translate_argos", return_value="Das Haus ist rot."):
        # Pre-warm connection pool
        query_translation_server(
            text="Warmup phrase",
            source="en",
            target="de",
            provider="argos",
            server_url=warm_argos_test_server,
        )

        # Measure query latency
        t0 = time.perf_counter()
        resp = query_translation_server(
            text="The house is red.",
            source="en",
            target="de",
            provider="argos",
            server_url=warm_argos_test_server,
            zid="20260830180800",
            trace_id="trace-warm-argos-1"
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert resp is not None
        assert resp["status"] == "success"
        assert resp["translated_text"] == "Das Haus ist rot."
        assert resp["provider"] == "argos"
        assert elapsed_ms < 50.0, f"Query took {elapsed_ms:.2f}ms, expected sub-50ms"
        assert resp.get("duration_ms", 0) < 50.0


def test_consecutive_keep_alive_argos_queries_maintain_low_latency(warm_argos_test_server):
    """
    Assert that consecutive queries through the keep-alive connection pool
    consistently achieve low latency (<50ms average).
    """
    with patch.object(TranslationRequestHandler, "_translate_argos", side_effect=lambda text, *args, **kwargs: f"[DE] {text}"):
        latencies = []
        for i in range(10):
            t0 = time.perf_counter()
            resp = query_translation_server(
                text=f"Sentence number {i}",
                source="en",
                target="de",
                provider="argos",
                server_url=warm_argos_test_server,
                zid=f"202608301808{i:02d}",
                trace_id=f"trace-loop-{i}"
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            assert resp is not None
            assert resp["status"] == "success"
            assert resp["translated_text"] == f"[DE] Sentence number {i}"

        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency < 50.0, f"Average latency {avg_latency:.2f}ms exceeded 50ms"


def test_translation_server_health_readiness_reporting(warm_argos_test_server):
    """
    Assert that the translation microservice /health endpoint exposes
    the status of the Argos model warmup.
    """
    parsed = urllib.parse.urlparse(warm_argos_test_server)
    health_url = f"{warm_argos_test_server}/health"
    req = urllib.request.Request(health_url)
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data.get("status") == "healthy"
        assert "providers" in data
        assert "argos" in data["providers"]
        assert data["providers"]["argos"] in ("warm", "warming", "available")


def test_controller_health_endpoint_aggregates_argos_warmup(warm_argos_test_server):
    """
    Assert that the unified controller's /api/v1/health endpoint aggregates
    and reflects the Argos warmup readiness status from the translation microservice.
    """
    parsed = urllib.parse.urlparse(warm_argos_test_server)
    port = parsed.port

    config = configparser.ConfigParser()
    config.add_section("services")
    config.set("services", "translation_server_url", warm_argos_test_server)

    desk_dir = Path(__file__).resolve().parent.parent
    cfg, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    ctrl_server = ThreadingHTTPServer(('127.0.0.1', 0), ControllerRequestHandler)
    ctrl_server.allow_reuse_address = True
    ctrl_server.daemon_threads = True
    ctrl_server.config = cfg
    ctrl_server.resolved_paths = resolved_paths
    ctrl_server.goldendict = goldendict
    ctrl_server.api_key = "test-warm-key"
    ctrl_server.seq_counter = 0
    ctrl_server.seq_lock = threading.Lock()
    ctrl_port = ctrl_server.server_address[1]

    # Create supervisor configured with the running test translation server port
    supervisor = ProcessSupervisor(cfg, resolved_paths, enabled=False)
    # Point supervisor's translation service to our active test server
    supervisor.services["translation"].port = port
    supervisor.services["translation"].host = "127.0.0.1"

    # Probe the test server to populate health_data
    probed = supervisor.probe_health(supervisor.services["translation"], timeout=2.0)
    assert probed is True
    assert supervisor.services["translation"].is_healthy is True

    ctrl_server.supervisor = supervisor
    ctrl_thread = threading.Thread(target=ctrl_server.serve_forever, daemon=True)
    ctrl_thread.start()
    time.sleep(0.1)

    try:
        url = f"http://127.0.0.1:{ctrl_port}/api/v1/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode('utf-8'))
            assert body["status"] == "success"
            data = body["data"]
            assert data["ok"] is True
            assert "services" in data
            assert "translation" in data["services"]
            assert data["services"]["translation"]["healthy"] is True
            assert "argos" in data["services"]["translation"]
            assert data["services"]["translation"]["argos"] in ("warm", "warming", "available")
            assert "models" in data
            assert data["models"]["argos"] in ("warm", "warming", "available")
    finally:
        ctrl_server.shutdown()
        ctrl_server.server_close()
