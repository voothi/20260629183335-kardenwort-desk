import json
import time
import socket
import threading
import configparser
import pytest
from pathlib import Path

from kardenwort_desk import (
    load_config,
    query_spacy_server,
    tokenize_text_with_fallback,
    SEC_SERVICES,
)


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def background_spacy_server():
    # Import SpacyHTTPServer from kardenwort core
    import sys
    kardenwort_ws = Path(__file__).resolve().parent.parent.parent / "20241223170748-kardenwort"
    src_dir = kardenwort_ws / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from kardenwort.server.spacy_server import SpacyHTTPServer, SpacyRequestHandler

    port = get_free_port()
    server = SpacyHTTPServer(('127.0.0.1', port), SpacyRequestHandler, preload_models=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.2)
    server_url = f"http://127.0.0.1:{port}"

    yield server_url, server

    server.shutdown()
    server.server_close()


def test_spacy_service_query(background_spacy_server):
    server_url, _ = background_spacy_server
    text = "Das ist ein schneller Test für Kardenwort Desk."
    res = query_spacy_server(text, language="de", server_url=server_url, zid="20260819002800", trace_id="20260819002800:test")
    assert res is not None
    assert res.get("status") == "success"
    assert res.get("zid") == "20260819002800"
    assert "tokens" in res
    assert len(res["tokens"]) > 0
    words = [t["word"] for t in res["tokens"]]
    assert "Test" in words


def test_spacy_service_fallback():
    # Test fallback behavior when server is offline
    offline_url = "http://127.0.0.1:59999"
    res = query_spacy_server("Sample text", language="de", server_url=offline_url, timeout=0.5)
    assert res is None


def test_tokenize_text_with_fallback(background_spacy_server):
    server_url, _ = background_spacy_server
    config = configparser.ConfigParser()
    config.add_section(SEC_SERVICES)
    config.set(SEC_SERVICES, 'spacy_server_url', server_url)

    resolved_paths = {}
    tokens = tokenize_text_with_fallback("The brown fox jumps.", "en", config, resolved_paths, zid="20260819002800")
    assert len(tokens) == 5
    words = [t["word"] for t in tokens]
    assert "fox" in words


def test_spacy_service_warm_latency_benchmark(background_spacy_server):
    server_url, _ = background_spacy_server
    text = "Der schnelle braune Fuchs springt über den faulen Hund. Ein weiterer Satz folgt hier."

    # Warm-up request
    query_spacy_server(text, language="de", server_url=server_url)

    # Benchmark 10 consecutive requests
    latencies = []
    for i in range(10):
        t0 = time.perf_counter()
        res = query_spacy_server(text, language="de", server_url=server_url, zid=f"202608190028{i:02d}")
        lat_ms = (time.perf_counter() - t0) * 1000
        latencies.append(lat_ms)
        assert res is not None
        assert res["status"] == "success"

    avg_latency = sum(latencies) / len(latencies)
    print(f"\nAverage warm SpaCy HTTP server latency: {avg_latency:.2f}ms (min: {min(latencies):.2f}ms, max: {max(latencies):.2f}ms)")
    # Assert sub-50ms latency SLA
    assert avg_latency < 50.0, f"Expected sub-50ms latency, got {avg_latency:.2f}ms"
