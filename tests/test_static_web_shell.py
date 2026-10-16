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
            assert '<section id="source-section"' in body
            assert 'id="source-container"' in body
            assert '<section id="translation-section"' in body
            assert 'id="translation-container"' in body
            assert '<table id="lemma-table"' in body
            assert 'id="kw-action-toolbar"' in body
            assert '<script id="session-data" type="application/json">' in body
            assert '<script src="/js/components/WorkspaceTabs.js"></script>' in body

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

    # 3.1 GET /js/components/WorkspaceTabs.js
    req = urllib.request.Request(f"{base_url}/js/components/WorkspaceTabs.js")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content_type = resp.headers.get('Content-Type')
        assert 'application/javascript' in content_type
        body = resp.read().decode('utf-8')
        assert 'WorkspaceTabs' in body
        assert 'bindCardWordsToTbody' in body

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
    assert page.locator("#kw-action-toolbar").is_visible()


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


def test_playwright_client_hydration_from_kardenwort_bootstrap(page, test_server):
    port = test_server

    sample_bootstrap = {
        "ui_config": {
            "highlight_same_lemma": True,
            "filter_selected_only": False,
            "theme": "dark"
        },
        "sentence_cards": [
            {
                "seq_num": 1,
                "selected_ids": [0],
                "words": [{"word": "Fuchs", "lemma": "Fuchs"}]
            }
        ],
        "delivery_mode": "container",
        "web_tab_mode": "tabs",
        "tab_bar_position": "top",
        "session_data": {
            "zid": "20261014210000",
            "session_zid": "20261014210000",
            "language": "de",
            "target_lang": "ru",
            "source_text": "Ein treuer Hund bewacht das Haus.",
            "sentence_translation": "Верный пес охраняет дом.",
            "headers": ["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"],
            "data_rows": [
                ["Hund", "Hund", "n.", "пес"],
                ["bewacht", "bewachen", "v.", "охраняет"]
            ],
            "server_enabled": True,
            "api_token": "bootstrap-token"
        }
    }

    page.goto(f"http://127.0.0.1:{port}/index.html")

    page.evaluate(f"""
        (function() {{
            var el = document.getElementById('kardenwort-bootstrap');
            el.textContent = JSON.stringify({json.dumps(sample_bootstrap)});
            var ev = new Event('DOMContentLoaded');
            document.dispatchEvent(ev);
        }})();
    """)

    page.wait_for_selector("#source-container")
    assert "Ein treuer Hund bewacht das Haus." in page.locator("#source-container").inner_text()
    assert "Верный пес охраняет дом." in page.locator("#translation-container").inner_text()
    assert page.locator("td:has-text('пес')").is_visible()
    assert page.locator("td:has-text('bewachen')").is_visible()

    # Verify window globals and AppState hydration
    config_val = page.evaluate("() => window.__CONFIG__")
    assert config_val.get("highlight_same_lemma") is True

    app_state = page.evaluate("() => ({ hasState: !!window.AppState, hasCards: !!(window.AppState && window.AppState.sentenceCards), delivery: window.AppState && window.AppState.deliveryMode })")
    assert app_state["hasState"] is True
    assert app_state["hasCards"] is True
    assert app_state["delivery"] == "container"


def test_standalone_index_html_syntax_validity():
    import re
    desk_dir = Path(__file__).resolve().parent.parent
    index_path = desk_dir / "web" / "index.html"
    assert index_path.is_file()
    content = index_path.read_text(encoding="utf-8")

    # Document must contain kardenwort-bootstrap script tag
    assert '<script id="kardenwort-bootstrap" type="application/json">' in content

    # Zero unescaped Python template replacement tags ({...})
    # Exclude normal CSS/JS braces by checking for standalone word identifiers like {title}, {lang_mismatch_body}
    unescaped_python_placeholders = re.findall(r'\{[a-zA-Z0-9_]+\}', content)
    assert unescaped_python_placeholders == [], f"Found unescaped template placeholders: {unescaped_python_placeholders}"


def test_run_render_flow_contains_kardenwort_bootstrap(tmp_path):
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    tsv_file = tmp_path / "20261014210000-test.de.tsv"
    tsv_file.write_text("WordSource\tWordDestination\nKatze\tкошка\n", encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Die Katze schläft.",
        language="de",
        zid="20261014210000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    import re
    bootstrap_match = re.search(r'<script id="kardenwort-bootstrap" type="application/json">\s*([\s\S]*?)\s*</script>', html)
    assert bootstrap_match is not None, "kardenwort-bootstrap script tag not found in rendered HTML"

    payload = json.loads(bootstrap_match.group(1))
    assert "ui_config" in payload
    assert "sentence_cards" in payload
    assert "delivery_mode" in payload
    assert payload["zid"] == "20261014210000"
    assert payload["language"] == "de"

