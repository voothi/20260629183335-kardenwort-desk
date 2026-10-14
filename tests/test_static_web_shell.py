import json
import socket
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
import pytest

import kardenwort_desk
from http_server import APIRequestHandler, ThreadingHTTPServer


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def test_server():
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
    server.server_port = port
    server.seq_counter = 0
    server.seq_lock = threading.Lock()

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.15)

    yield port

    server.shutdown()
    server.server_close()


def test_static_routes_http_delivery(test_server):
    port = test_server
    base_url = f"http://127.0.0.1:{port}"

    # 1. GET /index.html and /web/index.html
    for path in ('/index.html', '/web/index.html'):
        req = urllib.request.Request(f"{base_url}{path}")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            content_type = resp.headers.get('Content-Type')
            assert 'text/html' in content_type
            body = resp.read().decode('utf-8')
            assert '<header id="session-header"' in body
            assert '<section id="source-container"' in body
            assert '<section id="translation-container"' in body
            assert '<table id="lemma-table"' in body
            assert '<div id="action-toolbar"' in body
            assert '<script id="session-data" type="application/json">' in body

    # 2. GET /css/*
    for css_file in ('variables.css', 'tokens.css', 'table.css', 'main.css'):
        req = urllib.request.Request(f"{base_url}/css/{css_file}")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            content_type = resp.headers.get('Content-Type')
            assert 'text/css' in content_type
            body = resp.read().decode('utf-8')
            assert len(body) > 0

    # 3. GET /js/app.js
    req = urllib.request.Request(f"{base_url}/js/app.js")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content_type = resp.headers.get('Content-Type')
        assert 'application/javascript' in content_type
        body = resp.read().decode('utf-8')
        assert 'kwToggleTag' in body
        assert 'kwExportSelected' in body

    # 4. GET /favicon.ico
    req = urllib.request.Request(f"{base_url}/favicon.ico")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content_type = resp.headers.get('Content-Type')
        assert 'image/' in content_type


def test_static_path_traversal_forbidden(test_server):
    port = test_server
    url = f"http://127.0.0.1:{port}/css/../../config.ini"
    try:
        urllib.request.urlopen(url)
        assert False, "Expected 403 or 404 for path traversal attempt"
    except urllib.error.HTTPError as e:
        assert e.code in (401, 403, 404)


def test_playwright_standalone_index_loads_containers(page, test_server):
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    assert page.locator("#session-header").is_visible()
    assert page.locator("#source-container").is_visible()
    assert page.locator("#translation-container").is_visible()
    assert page.locator("#lemma-table").is_visible()
    assert page.locator("#action-toolbar").is_visible()


def test_playwright_client_hydration_from_session_data(page, test_server):
    port = test_server

    sample_session = {
        "zid": "20261014120000",
        "session_zid": "20261014120000",
        "language": "de",
        "target_lang": "ru",
        "source_text": "Der schnelle braune Fuchs springt über den faulen Hund.",
        "sentence_translation": "Быстрая коричневая лиса прыгает через ленивую собаку.",
        "headers": ["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"],
        "data_rows": [
            ["Fuchs", "Fuchs", "n.", "лиса"],
            ["springt", "springen", "v.", "прыгает"]
        ],
        "server_enabled": True,
        "api_token": "test-token"
    }

    # Load page via HTTP server so scripts and stylesheets resolve
    page.goto(f"http://127.0.0.1:{port}/index.html")

    # Inject data into #session-data and trigger DOMContentLoaded event handler
    page.evaluate(f"""
        (function() {{
            var el = document.getElementById('session-data');
            el.textContent = JSON.stringify({json.dumps(sample_session)});
            var ev = new Event('DOMContentLoaded');
            document.dispatchEvent(ev);
        }})();
    """)

    # Assert hydrated text is present in DOM
    page.wait_for_selector("#source-container")
    assert "Der schnelle braune Fuchs" in page.locator("#source-container").inner_text()
    assert "Быстрая коричневая лиса" in page.locator("#translation-container").inner_text()
    assert page.locator("td:has-text('лиса')").is_visible()
    assert page.locator("td:has-text('springen')").is_visible()
