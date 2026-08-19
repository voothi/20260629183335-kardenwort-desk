import re
import json
import time
import socket
import threading
import configparser
import pytest
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

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


class MockSpacyRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok","models":{"de":true,"en":true}}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/tokenize':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8'))
            text = body.get('text', '')
            zid = body.get('zid', '')
            words = re.findall(r'\b\w+\b', text)
            tokens = [{"word": w, "lemma": w.lower(), "pos": "NOUN", "morph": ""} for w in words]
            resp = {"status": "success", "zid": zid, "tokens": tokens}
            resp_bytes = json.dumps(resp).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def background_spacy_server():
    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), MockSpacyRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)
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
    assert len(tokens) == 4 or len(tokens) == 5
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
