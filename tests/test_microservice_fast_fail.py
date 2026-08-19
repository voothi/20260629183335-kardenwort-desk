import time
import json
import socket
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import pytest

from kardenwort_desk import (
    query_spacy_server,
    query_translation_server,
    query_intellifiller_server,
    is_endpoint_available,
    record_endpoint_failure,
    record_endpoint_success,
    reset_microservice_circuit_breaker,
    check_endpoint_reachable,
    _MICROSERVICE_CIRCUIT_BREAKER,
)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def clean_circuit_breaker():
    reset_microservice_circuit_breaker()
    yield
    reset_microservice_circuit_breaker()


def test_circuit_breaker_record_and_cooldown():
    url = "http://127.0.0.1:54321"
    assert is_endpoint_available(url) is True

    record_endpoint_failure(url)
    assert is_endpoint_available(url, cooldown=5.0) is False

    # Immediate success clears failure
    record_endpoint_success(url)
    assert is_endpoint_available(url, cooldown=5.0) is True


def test_circuit_breaker_expiration():
    url = "http://127.0.0.1:54322"
    record_endpoint_failure(url)
    assert is_endpoint_available(url, cooldown=0.1) is False
    time.sleep(0.15)
    # After cooldown expiry, endpoint becomes available for probing
    assert is_endpoint_available(url, cooldown=0.1) is True


def test_fast_connection_probe_offline_port():
    free_port = get_free_port()
    url = f"http://127.0.0.1:{free_port}"

    t0 = time.perf_counter()
    reachable = check_endpoint_reachable(url, connect_timeout=0.2)
    duration = time.perf_counter() - t0

    assert reachable is False
    assert duration < 0.25, f"Probe took too long: {duration:.3f}s"


def test_query_spacy_fast_fail_and_cooldown():
    free_port = get_free_port()
    url = f"http://127.0.0.1:{free_port}"

    # First call: detects offline in <= 250ms and sets circuit breaker
    t0 = time.perf_counter()
    res1 = query_spacy_server("Haus", language="de", server_url=url)
    d1 = time.perf_counter() - t0
    assert res1 is None
    assert d1 < 0.25, f"First offline query took {d1:.3f}s"

    # Second call: circuit breaker is tripped, must return None instantly (< 10ms)
    t1 = time.perf_counter()
    res2 = query_spacy_server("Haus", language="de", server_url=url)
    d2 = time.perf_counter() - t1
    assert res2 is None
    assert d2 < 0.01, f"Cached offline query took {d2:.4f}s"


def test_query_translation_fast_fail_and_cooldown():
    free_port = get_free_port()
    url = f"http://127.0.0.1:{free_port}"

    t0 = time.perf_counter()
    res1 = query_translation_server("Haus", "de", "en", server_url=url)
    d1 = time.perf_counter() - t0
    assert res1 is None
    assert d1 < 0.25, f"First offline translation query took {d1:.3f}s"

    t1 = time.perf_counter()
    res2 = query_translation_server("Baum", "de", "en", server_url=url)
    d2 = time.perf_counter() - t1
    assert res2 is None
    assert d2 < 0.01, f"Cached offline translation query took {d2:.4f}s"


def test_query_intellifiller_fast_fail_and_cooldown():
    free_port = get_free_port()
    url = f"http://127.0.0.1:{free_port}"

    t0 = time.perf_counter()
    res1 = query_intellifiller_server([{"WordSource": "test"}], "prompt", server_url=url)
    d1 = time.perf_counter() - t0
    assert res1 is None
    assert d1 < 0.25, f"First offline intellifiller query took {d1:.3f}s"

    t1 = time.perf_counter()
    res2 = query_intellifiller_server([{"WordSource": "test2"}], "prompt", server_url=url)
    d2 = time.perf_counter() - t1
    assert res2 is None
    assert d2 < 0.01, f"Cached offline intellifiller query took {d2:.4f}s"


class SlowComputationServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        # Connected immediately, but simulate 0.5s computation time
        time.sleep(0.4)
        resp = {"status": "success", "translated_text": "Slow House"}
        resp_bytes = json.dumps(resp).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)


def test_connect_timeout_does_not_abort_slow_computation():
    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), SlowComputationServer)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    url = f"http://127.0.0.1:{port}"
    try:
        # connect_timeout is 0.2s, but total request timeout is 5.0s.
        # The request takes ~0.4s to compute, so it should succeed.
        t0 = time.perf_counter()
        res = query_translation_server("Haus", "de", "en", server_url=url, timeout=5.0, connect_timeout=0.2)
        duration = time.perf_counter() - t0
        assert res is not None
        assert res.get("status") == "success"
        assert res.get("translated_text") == "Slow House"
        assert duration >= 0.35
    finally:
        server.shutdown()
        server.server_close()
