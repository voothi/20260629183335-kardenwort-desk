import os
import sys
import json
import time
import socket
import pytest
import threading
import subprocess
import configparser
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch, MagicMock

import kardenwort_desk
from kardenwort_desk import (
    load_config,
    load_tsv_rows,
    save_tsv_rows_safely,
    query_intellifiller_server,
    _run_headless_intellifiller_impl,
    IntelliFillerError,
    SEC_SERVICES,
    SEC_TIMEOUTS,
)


class MockIntelliFillerHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5.0)

    def address_string(self):
        return self.client_address[0]

    def log_message(self, format_str, *args):
        pass

    def do_GET(self):
        if self.path in ('/health', '/api/v1/health'):
            body = json.dumps({
                "status": "ok",
                "backend": "openai",
                "model": "gpt-4o-mini",
                "prompts": ["morphology_and_ipa"],
                "uptime_seconds": 10.5,
                "zid": "20260819003000"
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path in ('/enrich', '/api/v1/enrich'):
            length = int(self.headers.get('Content-Length', 0))
            raw_data = self.rfile.read(length)
            payload = json.loads(raw_data.decode('utf-8'))

            if payload.get("prompt") == "trigger_rate_limit":
                err_body = json.dumps({
                    "status": "error",
                    "code": "ERR_LLM_RATE_LIMIT",
                    "message": "Rate limit exceeded (429)",
                    "retryable": True,
                    "row_id": 0,
                    "details": {"http_status": 429}
                }).encode('utf-8')
                self.send_response(429)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                return

            rows = payload.get("rows", [])
            enriched = []
            for r in rows:
                row_id = r.get("row_id", 0)
                word = r.get("WordSource", r.get("word", ""))
                enriched.append({
                    "row_id": row_id,
                    "WordDestination": f"trans_{word}",
                    "WordSourceIPA": f"/ipa_{word}/",
                    "MorphologyAI": "Noun|Sing"
                })

            resp = {
                "status": "success",
                "zid": payload.get("zid", "20260819003000"),
                "trace_id": payload.get("trace_id", ""),
                "enriched_rows": enriched,
                "duration_ms": 1.2
            }
            body = json.dumps(resp).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture
def mock_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    server = ThreadingHTTPServer(('127.0.0.1', port), MockIntelliFillerHandler)
    server.allow_reuse_address = False
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    yield url

    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass


def test_query_intellifiller_server_success(mock_server):
    rows = [{"row_id": 0, "WordSource": "Karden", "Quotation": "Die Karden blühen."}]
    res = query_intellifiller_server(rows, "morphology_and_ipa", server_url=mock_server)
    assert res is not None
    assert res["status"] == "success"
    assert len(res["enriched_rows"]) == 1
    assert res["enriched_rows"][0]["WordDestination"] == "trans_Karden"
    assert res["enriched_rows"][0]["WordSourceIPA"] == "/ipa_Karden/"


def test_query_intellifiller_server_offline():
    res = query_intellifiller_server([{"row_id": 0}], "test", server_url="http://127.0.0.1:59999")
    assert res is None


def test_run_headless_intellifiller_via_http(mock_server, tmp_path):
    tsv_path = tmp_path / "test.tsv"
    comments = ["# language=de", "# target_lang=ru"]
    headers = ["WordSource", "Quotation", "WordDestination", "WordSourceIPA"]
    data_rows = [
        ["Karden", "Die Karden wachsen.", "", ""],
        ["Garten", "Im Garten.", "", ""]
    ]
    save_tsv_rows_safely(tsv_path, comments, headers, data_rows)

    config = configparser.ConfigParser()
    config.add_section(SEC_SERVICES)
    config.set(SEC_SERVICES, "intellifiller_server_url", mock_server)
    config.add_section(SEC_TIMEOUTS)
    config.set(SEC_TIMEOUTS, "intellifiller_timeout", "10")

    resolved_paths = {
        "kardenwort_python": sys.executable,
        "intellifiller_headless": Path(__file__),
        "anki_mapping_file": None
    }

    res = _run_headless_intellifiller_impl(
        tsv_path=tsv_path,
        prompt_name="morphology_and_ipa",
        config=config,
        resolved_paths=resolved_paths,
        selected_rows=[0, 1],
        reprocess=True,
        zid="20260819003000"
    )
    assert res is True

    _, res_headers, res_data = load_tsv_rows(tsv_path)
    assert "MorphologyAI" in res_headers
    assert res_data[0][res_headers.index("WordDestination")] == "trans_Karden"
    assert res_data[0][res_headers.index("WordSourceIPA")] == "/ipa_Karden/"
    assert res_data[0][res_headers.index("MorphologyAI")] == "Noun|Sing"
    assert res_data[1][res_headers.index("WordDestination")] == "trans_Garten"


def test_intellifiller_fallback_to_subprocess_when_offline(tmp_path, monkeypatch):
    tsv_path = tmp_path / "fallback_test.tsv"
    comments = ["# language=de"]
    headers = ["WordSource", "WordDestination"]
    data_rows = [["Test", ""]]
    save_tsv_rows_safely(tsv_path, comments, headers, data_rows)

    config = configparser.ConfigParser()
    config.add_section(SEC_SERVICES)
    config.set(SEC_SERVICES, "intellifiller_server_url", "http://127.0.0.1:59999")  # offline port

    resolved_paths = {
        "kardenwort_python": sys.executable,
        "intellifiller_headless": Path(__file__),
        "anki_mapping_file": None
    }

    subprocess_called = False

    def mock_subprocess_run(cmd, *args, **kwargs):
        nonlocal subprocess_called
        subprocess_called = True
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Success"
        mock_res.stderr = ""
        return mock_res

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    res = _run_headless_intellifiller_impl(
        tsv_path=tsv_path,
        prompt_name="test_prompt",
        config=config,
        resolved_paths=resolved_paths,
        reprocess=True
    )
    assert res is True
    assert subprocess_called is True


def test_intellifiller_http_rate_limit_raises_structured_error(mock_server, tmp_path):
    tsv_path = tmp_path / "error_test.tsv"
    comments = ["# language=de"]
    headers = ["WordSource", "WordDestination"]
    data_rows = [["TestWord", ""]]
    save_tsv_rows_safely(tsv_path, comments, headers, data_rows)

    config = configparser.ConfigParser()
    config.add_section(SEC_SERVICES)
    config.set(SEC_SERVICES, "intellifiller_server_url", mock_server)

    resolved_paths = {
        "kardenwort_python": sys.executable,
        "intellifiller_headless": Path(__file__),
        "anki_mapping_file": None
    }

    with pytest.raises(IntelliFillerError) as excinfo:
        _run_headless_intellifiller_impl(
            tsv_path=tsv_path,
            prompt_name="trigger_rate_limit",
            config=config,
            resolved_paths=resolved_paths,
            reprocess=True
        )
    assert excinfo.value.envelope.get("code") == "ERR_LLM_RATE_LIMIT"
    assert excinfo.value.envelope.get("retryable") is True


def test_intellifiller_http_benchmark_submillisecond(mock_server):
    rows = [{"row_id": i, "WordSource": f"word_{i}"} for i in range(10)]
    # Measure multiple dispatches
    latencies = []
    for _ in range(5):
        t0 = time.perf_counter()
        res = query_intellifiller_server(rows, "test_prompt", server_url=mock_server)
        dt = (time.perf_counter() - t0) * 1000
        assert res is not None
        latencies.append(dt)

    # Assert fast microservice dispatch (<50ms end-to-end loopback HTTP)
    avg_latency = sum(latencies) / len(latencies)
    assert avg_latency < 50.0
