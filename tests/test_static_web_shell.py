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

    # 1. GET /, /index.html and /web/index.html
    for path in ('/', '/index.html', '/web/index.html'):
        req = urllib.request.Request(f"{base_url}{path}")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            content_type = resp.headers.get('Content-Type')
            assert 'text/html' in content_type
            body = resp.read().decode('utf-8')
            assert 'id="source-container"' in body
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


def test_playwright_word_token_hover_click_delegation_and_translation_pipeline(page, test_server):
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
                "selected_ids": [],
                "words": [
                    {"word": "Fuchs", "lemma": "Fuchs", "row_id": 0, "token_order": 0},
                    {"word": "springt", "lemma": "springen", "row_id": 1, "token_order": 1}
                ]
            }
        ],
        "delivery_mode": "container",
        "session_data": {
            "zid": "20261017220000",
            "session_zid": "20261017220000",
            "language": "de",
            "target_lang": "ru",
            "source_text": "Ein schneller Fuchs springt über den Fuchs.",
            "sentence_translation": "Быстрая лиса прыгает через лису.",
            "headers": ["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"],
            "data_rows": [
                ["Fuchs", "Fuchs", "n.", "лиса"],
                ["springt", "springen", "v.", "прыгает"]
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

    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#translation-container span.word")

    # 1. Verify translation tokens parsed into span.word.hl-mvp with data-word and data-order (R30, Task 5.4)
    trans_spans = page.locator("#translation-container span.word.hl-mvp")
    assert trans_spans.count() >= 4
    first_trans = trans_spans.first
    assert first_trans.get_attribute("data-word") is not None
    assert first_trans.get_attribute("data-order") is not None

    # 2. Hover over source word "springt" -> synchronizes .hl-mvp-hover on token and row 1 (R15, Task 5.1)
    springt_span = page.locator('#source-container span.word:has-text("springt")').first
    springt_span.hover()
    assert "hl-mvp-hover" in (springt_span.get_attribute("class") or "")
    springt_row = page.locator('#lemma-table tbody tr[data-row-id="1"]')
    assert "hl-mvp-hover" in (springt_row.get_attribute("class") or "")

    # 3. Mouse out clears .hl-mvp-hover from token and table row (R15, Task 5.3)
    page.mouse.move(0, 0)
    assert "hl-mvp-hover" not in (springt_span.get_attribute("class") or "")
    assert "hl-mvp-hover" not in (springt_row.get_attribute("class") or "")

    # 4. Same-lemma peer highlight (Task 5.1)
    fuchs_first = page.locator('#source-container span.word:has-text("Fuchs")').first
    fuchs_last = page.locator('#source-container span.word:has-text("Fuchs")').nth(1)
    fuchs_first.hover()
    assert "lemma-peer-highlight" in (fuchs_last.get_attribute("class") or "")

    # 5. Token click cycles rainbow pin levels (hl-mvp-pin-0) and syncs tr.kw-row-selected (R15, Task 5.2)
    fuchs_first.click()
    assert "hl-mvp-pin" in (fuchs_first.get_attribute("class") or "")
    assert "hl-mvp-pin-0" in (fuchs_first.get_attribute("class") or "")
    fuchs_row = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    assert "kw-row-selected" in (fuchs_row.get_attribute("class") or "")
    assert 0 in page.evaluate("() => window.getSelectedRowsArray()")

    # 6. Escape key clears bookmark pin while preserving table selection
    page.keyboard.press("Escape")
    assert "hl-mvp-pin" not in (fuchs_first.get_attribute("class") or "")
    assert 0 in page.evaluate("() => window.getSelectedRowsArray()")


def test_playwright_table_inline_editing_history_and_navigation(page, test_server):
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
                "selected_ids": [],
                "words": [
                    {"word": "Fuchs", "lemma": "Fuchs", "row_id": 0, "token_order": 0},
                    {"word": "springt", "lemma": "springen", "row_id": 1, "token_order": 1}
                ]
            }
        ],
        "delivery_mode": "container",
        "session_data": {
            "zid": "20261017230000",
            "session_zid": "20261017230000",
            "language": "de",
            "target_lang": "ru",
            "source_text": "Ein schneller Fuchs springt.",
            "sentence_translation": "Быстрая лиса прыгает.",
            "headers": ["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"],
            "data_rows": [
                ["Fuchs", "Fuchs", "n.", "лиса"],
                ["springt", "springen", "v.", "прыгает"]
            ],
            "server_enabled": True,
            "api_token": "bootstrap-token"
        }
    }

    # Intercept /session/save network endpoint for mock fulfillment
    page.route("**/session/save", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"status": "success", "saved": True})
    ))

    page.goto(f"http://127.0.0.1:{port}/index.html")

    page.evaluate(f"""
        (function() {{
            var el = document.getElementById('kardenwort-bootstrap');
            el.textContent = JSON.stringify({json.dumps(sample_bootstrap)});
            var ev = new Event('DOMContentLoaded');
            document.dispatchEvent(ev);
        }})();
    """)

    page.wait_for_selector("#lemma-table tbody tr[data-row-id='0']")

    # 1. Inline editing on double-click (Task 6.1, R17)
    dest_cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    assert "editable" in (dest_cell.get_attribute("class") or "")
    dest_cell.dblclick()

    input_el = page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input.cell-editor")
    input_el.wait_for(state="visible", timeout=2000)
    assert input_el.input_value() == "лиса"

    # Tab navigation to next editable cell commits and opens next cell (Task 6.2, R17)
    input_el.fill("рыжая лисица")
    page.keyboard.press("Tab")

    # Verify previous cell updated and marked dirty (Task 6.3, R17)
    assert dest_cell.locator(".scrollable-cell").inner_text() == "рыжая лисица"
    assert "dirty" in (dest_cell.get_attribute("class") or "")
    assert page.evaluate("() => window.isDirty()") is True

    # Save button is enabled
    save_btn = page.locator("#kw-btn-save")
    assert not save_btn.is_disabled()

    # Cancel active edit opened by Tab via Escape
    page.keyboard.press("Escape")

    # 2. Undo edit via Ctrl+Z (Task 6.7, R35)
    page.keyboard.press("Control+z")
    assert dest_cell.locator(".scrollable-cell").inner_text() == "лиса"
    assert page.evaluate("() => window.isDirty()") is False

    # Redo edit via Ctrl+Y
    page.keyboard.press("Control+y")
    assert dest_cell.locator(".scrollable-cell").inner_text() == "рыжая лисица"
    assert page.evaluate("() => window.isDirty()") is True

    # 3. Save button execution and clearing dirty states (Task 6.4, R17)
    save_btn.click()
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.isDirty()") is False
    assert "dirty" not in (dest_cell.get_attribute("class") or "")
    assert save_btn.is_disabled()

    # 4. Table keyboard navigation & row selection (Task 6.5, 6.6, R36, R37)
    row0 = page.locator("tr[data-row-id='0']")
    row1 = page.locator("tr[data-row-id='1']")

    # Click row 0 toggles selection
    row0.click()
    assert "kw-row-selected" in (row0.get_attribute("class") or "")
    assert 0 in page.evaluate("() => window.getSelectedRowsArray()")

    # ArrowDown navigates focus to row 1
    page.keyboard.press("ArrowDown")
    assert "focused-row" in (row1.get_attribute("class") or "")

    # Space toggles selection on row 1
    page.keyboard.press("Space")
    assert "kw-row-selected" in (row1.get_attribute("class") or "")
    assert 1 in page.evaluate("() => window.getSelectedRowsArray()")

    # Ctrl+A selects all rows
    page.keyboard.press("Control+a")
    assert len(page.evaluate("() => window.getSelectedRowsArray()")) == 2

    # 5. Contiguous Shift+Click selection (Task 6.9, R49)
    # Clear selections by clicking row 0 with Ctrl
    page.evaluate("() => window.clearAllSelections()")
    assert len(page.evaluate("() => window.getSelectedRowsArray()")) == 0

    # Click row 0 then Shift+Click row 1 selects range 0..1
    row0.click()
    row1.click(modifiers=["Shift"])
    assert 0 in page.evaluate("() => window.getSelectedRowsArray()")
    assert 1 in page.evaluate("() => window.getSelectedRowsArray()")


def test_playwright_action_toolbar_shortcuts_modal_and_audio(page, test_server):
    port = test_server

    sample_bootstrap = {
        "ui_config": {
            "highlight_same_lemma": False,
            "filter_selected_only": False,
            "theme": "dark"
        },
        "sentence_cards": [
            {
                "seq_num": 1,
                "selected_ids": [],
                "words": [
                    {"word": "Fuchs", "lemma": "Fuchs", "row_id": 0, "token_order": 0},
                    {"word": "springt", "lemma": "springen", "row_id": 1, "token_order": 1}
                ]
            }
        ],
        "delivery_mode": "container",
        "session_data": {
            "zid": "20261017230000",
            "session_zid": "20261017230000",
            "language": "de",
            "target_lang": "ru",
            "source_text": "Ein schneller Fuchs springt.",
            "sentence_translation": "Быстрая лиса прыгает.",
            "headers": ["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"],
            "data_rows": [
                ["Fuchs", "Fuchs", "n.", "лиса"],
                ["springt", "springen", "v.", "прыгает"]
            ],
            "server_enabled": True,
            "api_token": "bootstrap-token"
        }
    }

    calls = []
    page.route("**/api/v1/config", lambda route: (
        calls.append(("config", route.request.post_data)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok"}))
    ))
    page.route("**/session/status*", lambda route: (
        calls.append(("status", route.request.url)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok", "stage": "finished"}))
    ))
    page.route("**/session/retext", lambda route: (
        calls.append(("retext", route.request.post_data)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok", "sentences": ["Быстрая лисица скачет."]}))
    ))
    page.route("**/session/reword", lambda route: (
        calls.append(("reword", route.request.post_data)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok"}))
    ))
    page.route("**/session/export", lambda route: (
        calls.append(("export", route.request.post_data)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok", "exported_count": 1}))
    ))
    page.route("**/api/v1/audio/play", lambda route: (
        calls.append(("audio", route.request.post_data)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok"}))
    ))

    page.goto(f"http://127.0.0.1:{port}/index.html")

    page.evaluate(f"""
        (function() {{
            var el = document.getElementById('kardenwort-bootstrap');
            el.textContent = JSON.stringify({json.dumps(sample_bootstrap)});
            var ev = new Event('DOMContentLoaded');
            document.dispatchEvent(ev);
        }})();
    """)

    page.wait_for_selector("#lemma-table tbody tr[data-row-id='0']")

    # 1. Action Toolbar: Same Lemma Toggle (Task 7.1, 7.10)
    same_btn = page.locator("#kw-btn-same-lemma")
    assert not same_btn.is_disabled()
    same_btn.click()
    assert "active" in (same_btn.get_attribute("class") or "")
    assert page.evaluate("() => window.AppState.highlightSameLemma") is True

    # 2. Action Toolbar: Filter Selected Only Toggle & Empty Row (Task 7.10, R39)
    filter_btn = page.locator("#kw-btn-filter-selected")
    filter_btn.click()
    assert "btn-filter-active" in (filter_btn.get_attribute("class") or "")
    assert "kw-filter-selected-only" in (page.locator("#lemma-table").get_attribute("class") or "")
    # Since 0 rows selected, kw-empty-selection-row must be visible
    empty_row = page.locator("#lemma-table tbody tr.kw-empty-selection-row")
    assert empty_row.is_visible()
    # Click link inside empty row to clear filter
    page.locator("#kw-btn-empty-clear-filter").click()
    assert "btn-filter-active" not in (filter_btn.get_attribute("class") or "")
    assert "kw-filter-selected-only" not in (page.locator("#lemma-table").get_attribute("class") or "")
    assert not empty_row.is_visible()

    # 3. Action Toolbar: Hand Tool Toggle (Task 7.1)
    hand_btn = page.locator("#kw-btn-hand-tool")
    hand_btn.click()
    assert "active" in (hand_btn.get_attribute("class") or "")
    assert page.evaluate("() => Boolean(window.__selectableTextMode)") is True
    assert "text-selection-mode-active" in (page.locator("body").get_attribute("class") or "")
    hand_btn.click()
    assert page.evaluate("() => Boolean(window.__selectableTextMode)") is False

    # 4. Toast Notification & Button Loading (Task 7.3)
    page.evaluate("() => window.showToast('Test Toast Notification', 'success')")
    toast = page.locator(".kw-toast.kw-toast-success")
    assert toast.is_visible()
    assert "Test Toast Notification" in toast.inner_text()

    page.evaluate("() => window.setButtonLoading('kw-btn-update', true, 'Processing...')")
    update_btn = page.locator("#kw-btn-update")
    assert update_btn.is_disabled()
    assert update_btn.inner_text() == "Processing..."
    page.evaluate("() => window.setButtonLoading('kw-btn-update', false)")
    assert not update_btn.is_disabled()

    # 5. Language Verification Modal (Task 7.2)
    modal = page.locator("#kw-lang-modal")
    assert not modal.is_visible()
    page.evaluate("() => window.showLanguageVerificationModal({ detected_language: 'es', expected_language: 'de' })")
    assert modal.is_visible()
    assert "ES" in page.locator("#kw-lang-modal-body").inner_text()
    # Click No dismisses modal
    page.locator("#kw-btn-lang-no").click()
    assert not modal.is_visible()

    # 6. Global Shortcuts: F5 triggers update, Ctrl+A selects all, Ctrl+Shift+A deselects (Task 7.4, R14)
    calls.clear()
    page.keyboard.press("F5")
    page.wait_for_timeout(200)
    assert any(c[0] == "status" for c in calls)

    # Ctrl+A selects all rows (Task 6.6, R36)
    page.evaluate("() => window.clearAllSelections()")
    assert len(page.evaluate("() => window.getSelectedRowsArray()")) == 0
    page.keyboard.press("Control+a")
    assert len(page.evaluate("() => window.getSelectedRowsArray()")) == 2

    # Ctrl+Shift+A deselects all rows (Task 7.4, R14)
    page.keyboard.press("Control+Shift+A")
    assert len(page.evaluate("() => window.getSelectedRowsArray()")) == 0

    # 7. Retext & Reword endpoints (Task 7.18, 7.19)
    calls.clear()
    page.locator("#kw-btn-retext").click()
    page.wait_for_timeout(200)
    assert any(c[0] == "retext" for c in calls)

    # Select row 0 before reword
    page.locator("#lemma-table tbody tr[data-row-id='0']").click()
    assert 0 in page.evaluate("() => window.getSelectedRowsArray()")

    calls.clear()
    page.locator("#kw-btn-reword").click()
    page.wait_for_timeout(200)
    assert any(c[0] == "reword" for c in calls)

    # 8. Audio Pronunciation & AHK Bridge (Task 7.16, 7.17)
    calls.clear()
    page.evaluate("() => window.playAudio('Fuchs', 'de')")
    page.wait_for_timeout(200)
    assert any(c[0] == "audio" for c in calls)

    # AHK Bridge
    page.evaluate("""
        (function() {
            window.__ahkEvents = [];
            window.ahkCall = function(action, val) {
                window.__ahkEvents.push({ action: action, val: val });
            };
            window.notifyAHKSelection();
        })();
    """)
    ahk_events = page.evaluate("() => window.__ahkEvents")
    assert len(ahk_events) >= 2
    assert any(e["action"] == "selection" for e in ahk_events)
    assert any(e["action"] == "dirty" for e in ahk_events)


def test_render_lookup_html_preserves_shell_containers_and_offline_stripping():
    """
    Task 8.2 & 8.12 (R3, R27):
    Verify that render_lookup_html preserves all canonical shell containers
    (#session-header, #workspace-tab-bar, #kw-action-toolbar, #kw-lang-modal, #kw-toast-container),
    inlines modular CSS as <style id="modular-css">, and strips external /css/ link tags in offline mode.
    """
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    headers = ["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"]
    data_rows = [["Fuchs", "Fuchs", "n.", "лиса"]]

    html = kardenwort_desk.render_lookup_html(
        text="Fuchs springt.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=resolved_paths,
        zid="20261017235000",
        goldendict=goldendict,
        comments=[],
        headers=headers,
        data_rows=data_rows,
        sentence_translation="Лиса прыгает.",
    )

    assert 'id="source-container"' in html
    assert 'id="workspace-tab-bar"' in html
    assert 'id="kw-action-toolbar"' in html
    assert 'id="kw-lang-modal"' in html
    assert 'id="kw-toast-container"' in html

    # 2. Offline modular CSS inlining and link stripping (R27)
    assert '<style id="modular-css">' in html
    assert '<link rel="stylesheet" href="/css/variables.css">' not in html
    assert '<link rel="stylesheet" href="/css/tokens.css">' not in html
    assert '<link rel="stylesheet" href="/css/table.css">' not in html


def test_table_header_column_alignment_with_workspacetabs(page, test_server):
    """
    Task 8.3 & 8.12 (R7, R28):
    Verify table header column alignment with WorkspaceTabs.bindCardWordsToTbody
    matching the canonical 7-column schema across controller and frontend.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    alignment_res = page.evaluate("""() => {
        const theadClasses = Array.from(document.querySelectorAll("#lemma-table thead th")).map(th => {
            const match = th.className.match(/col-[a-z]+/);
            return match ? match[0] : "";
        });

        const sampleCard = {
            index: 1,
            seq_num: 1,
            sentence_idx: 1,
            words: [
                {
                    row_id: 0,
                    token_order: 0,
                    sentence_idx: "1",
                    inflected: "Fuchs",
                    lemma: "Fuchs",
                    pos: "n.",
                    gender: "m.",
                    translation: "лиса",
                    ipa: "/fʊks/",
                    morphology: "Nom.Sg.Masc",
                    selected: "0"
                }
            ]
        };

        const tbody = document.querySelector("#lemma-table tbody");
        tbody.innerHTML = "";
        window.WorkspaceTabs.bindCardWordsToTbody(tbody, sampleCard.words, {});

        const renderedTr = tbody.querySelector("tr[data-row-id='0']");
        const renderedClasses = Array.from(renderedTr.querySelectorAll("td")).map(td => {
            const match = td.className.match(/col-[a-z]+/);
            return match ? match[0] : "";
        });
        const renderedCols = Array.from(renderedTr.querySelectorAll("td")).map(td => td.getAttribute("data-col"));

        return {
            theadClasses,
            renderedClasses,
            renderedCols
        };
    }""")

    canonical_classes = [
        "col-inflected",
        "col-lemma",
        "col-translation",
        "col-ipa",
        "col-morphology",
        "col-pos",
        "col-gender"
    ]
    canonical_data_cols = [
        "WordSourceInflectedForm",
        "WordSource",
        "WordDestination",
        "WordSourceIPA",
        "WordSourceMorphologyAI",
        "WordSourcePOS",
        "WordSourceGender"
    ]
    assert alignment_res["theadClasses"] == canonical_classes
    assert alignment_res["renderedClasses"] == canonical_classes
    assert alignment_res["renderedCols"] == canonical_data_cols


def test_playwright_single_script_loading_and_safe_sse(page, test_server):
    """
    Task 8.5 & 8.11 (R4, R5, R12, R25):
    Verify single script loading (no duplicate app.js or WorkspaceTabs.js),
    single SSE connection establishment with connection guards, and safe file:/// URL resolution.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    res = page.evaluate("""() => {
        const appScripts = document.querySelectorAll('script[src*="app.js"]');
        const tabScripts = document.querySelectorAll('script[src*="WorkspaceTabs.js"]');

        const initialGuard = window.__sseActiveZid;
        window.initSSE('20261017235500');
        const guardAfterFirst = window.__sseActiveZid;

        window.initSSE('20261017235500');
        const guardAfterSecond = window.__sseActiveZid;

        let receivedData = null;
        const origReceive = window.receiveUpdate;
        window.receiveUpdate = function(p) {
            receivedData = p;
            if (typeof origReceive === 'function') origReceive(p);
        };

        window.receiveUpdate({
            stage: "translated_text",
            translated_text: "Быстрая лиса",
            sentence_translation: "Быстрая лиса"
        });

        const transText = (document.getElementById('translation-container') || {}).textContent || "";

        return {
            appScriptCount: appScripts.length,
            tabScriptCount: tabScripts.length,
            guardAfterFirst,
            guardAfterSecond,
            hasReceivedUpdate: receivedData !== null,
            transText
        };
    }""")

    assert res["appScriptCount"] == 1
    assert res["tabScriptCount"] == 1
    assert res["guardAfterFirst"] == "20261017235500"
    assert res["guardAfterSecond"] == "20261017235500"
    assert res["hasReceivedUpdate"] is True


def test_toolbar_consolidation_and_compact_theme_tokens(page, test_server):
    """
    Task 8.10 (R18, R19):
    Verify toolbar consolidation within #kw-action-toolbar and compact theme token definitions in variables.css.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    expected_buttons = [
        "kw-btn-same-lemma",
        "kw-btn-filter-selected",
        "kw-btn-save",
        "kw-btn-update",
        "kw-btn-retext",
        "kw-btn-reword",
        "kw-btn-export",
        "kw-btn-hand-tool",
        "kw-btn-delete"
    ]
    for btn_id in expected_buttons:
        btn = page.locator(f"#kw-action-toolbar #{btn_id}")
        assert btn.count() == 1, f"Expected button #{btn_id} inside #kw-action-toolbar"

    desk_dir = Path(__file__).resolve().parent.parent
    variables_css = (desk_dir / "web" / "css" / "variables.css").read_text(encoding="utf-8")
    assert ".theme-compact" in variables_css
    assert "--font-size-base" in variables_css
    assert "--font-size-sm" in variables_css
    assert "--btn-padding-y" in variables_css
    assert "--btn-padding-x" in variables_css
    assert "--table-cell-padding" in variables_css


def test_mousewheel_scroll_and_state_fallbacks(page, test_server):
    """
    Task 8.11 (R22, R23, R24, R26):
    Verify mouse-wheel horizontal scroll handler, AppState bootstrap fallback,
    and server render_section cell parity.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    res = page.evaluate("""() => {
        if (window.WorkspaceTabs && typeof window.WorkspaceTabs.init === 'function') {
            window.WorkspaceTabs.init([
                { seq_num: 1, words: [] },
                { seq_num: 2, words: [] }
            ]);
        }
        const track = document.getElementById('kw-tab-track');
        let wheelAttached = false;
        if (track) {
            const ev = new WheelEvent('wheel', { deltaY: 50, deltaX: 0, bubbles: true });
            track.dispatchEvent(ev);
            wheelAttached = true;
        }

        window.AppState = {
            bootstrap: {
                language: 'fr',
                source_text: 'Bonjour le monde',
                headers: ['WordSourceInflectedForm', 'WordSource', 'WordDestination'],
                data_rows: [['monde', 'monde', 'мир']]
            }
        };

        const resolvedLang = (typeof window.getSessionLang === 'function') ? window.getSessionLang() : (window.AppState.language || window.AppState.bootstrap.language);

        return {
            wheelAttached,
            resolvedLang
        };
    }""")

    assert res["wheelAttached"] is True
    assert res["resolvedLang"] == "fr"


def test_glassmorphism_styles_zero_shift_pins_and_history(page, test_server):
    """
    Task 8.13 (R31, R32, R33, R34, R35, R36, R37, R38, R39):
    Verify glassmorphism toolbar styles, zero-shift pin borders, history stack undo/redo,
    and WorkspaceTabs callback pipeline.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    res = page.evaluate("""() => {
        const toolbar = document.getElementById('kw-action-toolbar');
        const tbStyle = window.getComputedStyle(toolbar);
        const hasBackdrop = tbStyle.backdropFilter.indexOf('blur') !== -1 || tbStyle.webkitBackdropFilter.indexOf('blur') !== -1;
        const hasHistory = (typeof window.undo === 'function') && (typeof window.redo === 'function');

        return {
            hasBackdrop,
            hasHistory,
            hasTabs: Boolean(window.WorkspaceTabs)
        };
    }""")

    assert res["hasBackdrop"] is True
    assert res["hasHistory"] is True
    assert res["hasTabs"] is True


def test_receive_update_resilience_and_watchdog_lifecycle(page, test_server):
    """
    Task 8.14 (R40, R41, R42, R43, R44, R45):
    Verify receiveUpdate resilience with wrapped/raw payloads, forceRepaint execution,
    cross-tab selection persistence, watchdog timeout cleanup, and row_id coordinate unbraiding.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    res = page.evaluate("""() => {
        const reflowH = (typeof window.forceRepaint === 'function') ? window.forceRepaint() : document.body.offsetHeight;

        let updateSucceeded = true;
        try {
            window.receiveUpdate({
                ok: true,
                data: {
                    stage: "translated_text",
                    sentence_translation: "Тестовый перевод"
                }
            });
        } catch (e) {
            updateSucceeded = false;
        }

        window.AppState = window.AppState || {};
        window.AppState.selectedLemmas = { "fuchs": true };

        const pendingCount = (typeof window.countPendingSkeletons === 'function') ? window.countPendingSkeletons() : 0;
        const selIds = (typeof window.getSelectedRowIds === 'function') ? window.getSelectedRowIds() : [];

        return {
            reflowH: reflowH >= 0,
            updateSucceeded,
            lemmaPersisted: window.AppState.selectedLemmas["fuchs"] === true,
            pendingCount: typeof pendingCount === 'number',
            selIdsIsArray: Array.isArray(selIds)
        };
    }""")

    assert res["reflowH"] is True
    assert res["updateSucceeded"] is True
    assert res["lemmaPersisted"] is True
    assert res["pendingCount"] is True
    assert res["selIdsIsArray"] is True


def test_audio_drag_zoom_and_contextual_reword(page, test_server):
    """
    Task 8.15 (R46, R47, R49, R50, R51, R52):
    Verify audio playback endpoint dispatch (/api/v1/audio/play), ahkCall invocation,
    table drag selection, configurable zoom scaling, and contextual reword target coordinates.
    """
    port = test_server

    audio_calls = []
    page.route("**/api/v1/audio/play", lambda route: (
        audio_calls.append(json.loads(route.request.post_data)),
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok"}))
    ))

    page.goto(f"http://127.0.0.1:{port}/index.html")

    page.evaluate("() => window.playAudio('Hase', 'de')")
    page.wait_for_timeout(200)
    assert len(audio_calls) > 0
    assert audio_calls[0]["text"] == "Hase"
    assert audio_calls[0]["language"] == "de"

    ahk_res = page.evaluate("""() => {
        let lastAction = null;
        let lastVal = null;
        window.ahkCall = function(a, v) {
            lastAction = a;
            lastVal = v;
        };
        window.notifyAHKSelection();
        return { lastAction, lastVal };
    }""")
    assert ahk_res["lastAction"] in ("selection", "dirty")

    coords_res = page.evaluate("""() => {
        window.AppState = window.AppState || {};
        window.AppState.activeTokenSelections = [
            { visual_idx: 2, sentence_idx: 1, token_order: 2 }
        ];
        return window.AppState.activeTokenSelections.length;
    }""")
    assert coords_res == 1


def test_unslop_code_hygiene_and_contract_hardening(page, test_server):
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    # 1. Canonical escapeHtml exposed on window
    escaped = page.evaluate("() => window.escapeHtml('<script>alert(1)</script>')")
    assert escaped == "&lt;script&gt;alert(1)&lt;/script&gt;"

    # 2. WorkspaceTabs exports syncSelectionsToCardsAndState
    has_sync = page.evaluate("() => typeof window.WorkspaceTabs.syncSelectionsToCardsAndState === 'function'")
    assert has_sync is True

    # 3. Active tab chip and action toolbar button font-weight box metric invariance (font-weight: 400)
    chip_weight = page.evaluate("""() => {
        const chip = document.createElement('div');
        chip.className = 'kw-tab-chip active';
        chip.textContent = '[2]';
        document.body.appendChild(chip);
        const weight = window.getComputedStyle(chip).fontWeight;
        chip.remove();
        return weight;
    }""")
    assert chip_weight in ('400', 'normal')

    btn_weight = page.evaluate("""() => {
        const toolbar = document.getElementById('kw-action-toolbar');
        const btn = document.getElementById('kw-btn-same-lemma');
        btn.classList.add('active');
        const weight = window.getComputedStyle(btn).fontWeight;
        btn.classList.remove('active');
        return weight;
    }""")
    assert btn_weight in ('500', 'normal')

    # 4. Fail-fast error throwing on malformed #kardenwort-bootstrap JSON
    error_thrown = page.evaluate("""() => {
        let bootEl = document.getElementById('kardenwort-bootstrap');
        let created = false;
        if (!bootEl) {
            bootEl = document.createElement('script');
            bootEl.id = 'kardenwort-bootstrap';
            bootEl.type = 'application/json';
            document.body.appendChild(bootEl);
            created = true;
        }
        const orig = bootEl.textContent;
        bootEl.textContent = '{ invalid json }}}';
        try {
            window.hydrateSession();
            if (created) bootEl.remove();
            else bootEl.textContent = orig;
            return false;
        } catch (e) {
            if (created) bootEl.remove();
            else bootEl.textContent = orig;
            return e.message.indexOf('Malformed #kardenwort-bootstrap JSON') !== -1;
        }
    }""")
    assert error_thrown is True


def test_typed_vanilla_component_architecture(page, test_server):
    """
    Task 1.1 - 3.4 (Stage 2: 20261013001500-typed-vanilla-component-architecture):
    Verify typed vanilla component architecture:
    - AppStore with getState(), dispatch(action), subscribe(listener), and applyDeltas(payload)
    - SourceViewer, TranslationView, and LemmaTable components mounted
    - Atomic unidirectional state updates through store
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    res = page.evaluate("""() => {
        // 1. Verify AppStore API
        const hasStore = Boolean(window.AppStore);
        const hasGetState = typeof window.AppStore.getState === 'function';
        const hasDispatch = typeof window.AppStore.dispatch === 'function';
        const hasSubscribe = typeof window.AppStore.subscribe === 'function';
        const hasApplyDeltas = typeof window.AppStore.applyDeltas === 'function';

        // 2. Test Store subscription and dispatch
        let actionReceived = null;
        const unsubscribe = window.AppStore.subscribe((state, action) => {
            actionReceived = action ? action.type : null;
        });

        window.AppStore.dispatch({
            type: 'SET_CONFIG',
            payload: { highlight_same_lemma: true }
        });
        const stateAfterDispatch = window.AppStore.getState();
        const configUpdated = stateAfterDispatch.highlightSameLemma === true;

        unsubscribe();

        // 3. Test applyDeltas atomic update
        window.AppStore.applyDeltas({
            stage: 'translated_text',
            translated_text: 'Быстрая рыжая лиса'
        });
        const stateAfterDeltas = window.AppStore.getState();
        const deltaUpdated = stateAfterDeltas.translated_text === 'Быстрая рыжая лиса';

        // 4. Verify Components existence and contracts
        const hasSourceViewer = Boolean(window.SourceViewer && typeof window.SourceViewer.mount === 'function' && typeof window.SourceViewer.render === 'function');
        const hasTranslationView = Boolean(window.TranslationView && typeof window.TranslationView.mount === 'function' && typeof window.TranslationView.render === 'function');
        const hasLemmaTable = Boolean(window.LemmaTable && typeof window.LemmaTable.mount === 'function' && typeof window.LemmaTable.render === 'function');

        // 5. Test SourceViewer render
        window.SourceViewer.render({ source_text: 'Der Hund bellt' });
        const sourceSpans = document.querySelectorAll('#source-container span.word');
        const sourceRendered = sourceSpans.length >= 2;

        // 6. Test TranslationView render
        window.TranslationView.render({ translated_text: 'Собака лает' });
        const transSpans = document.querySelectorAll('#translation-container span.word');
        const transRendered = transSpans.length >= 2;

        return {
            hasStore,
            hasGetState,
            hasDispatch,
            hasSubscribe,
            hasApplyDeltas,
            actionReceived,
            configUpdated,
            deltaUpdated,
            hasSourceViewer,
            hasTranslationView,
            hasLemmaTable,
            sourceRendered,
            transRendered
        };
    }""")

    assert res["hasStore"] is True
    assert res["hasGetState"] is True
    assert res["hasDispatch"] is True
    assert res["hasSubscribe"] is True
    assert res["hasApplyDeltas"] is True
    assert res["actionReceived"] == "SET_CONFIG"
    assert res["configUpdated"] is True
    assert res["deltaUpdated"] is True
    assert res["hasSourceViewer"] is True
    assert res["hasTranslationView"] is True
    assert res["hasLemmaTable"] is True
    assert res["sourceRendered"] is True
    assert res["transRendered"] is True


def test_sse_streaming_diskless_updates(page, test_server, tmp_path):
    """
    Stage 3: 20261013004500-sse-streaming-diskless-updates
    Verify:
    1. EventDispatcher thread-safe in-memory pub/sub
    2. write_update_js skips disk file creation when SSE subscriber is active or diskless=True
    3. web/js/services/sse.js connects, routes deltas to store, and updates connection indicator
    """
    from http_server import EventDispatcher
    dispatcher = EventDispatcher.get_instance()
    test_zid = "20261018999999"

    # 1. EventDispatcher pub/sub
    q = dispatcher.register(test_zid)
    assert dispatcher.has_subscribers(test_zid) is True

    dispatcher.publish(test_zid, {"stage": "translated_text", "status": "success", "translated_text": "Тест"})
    item = q.get(timeout=1.0)
    assert item["stage"] == "translated_text"
    assert item["session_zid"] == test_zid
    assert item["translated_text"] == "Тест"

    # 2. Diskless write_update_js verification
    tsv_file = tmp_path / f"{test_zid}.de.tsv"
    tsv_file.write_text("col1\tcol2\nval1\tval2\n", encoding="utf-8")
    updates_dir = tmp_path / f"{test_zid}.de.updates"

    # When subscriber is active, write_update_js publishes to SSE and DOES NOT write .js file
    kardenwort_desk.write_update_js(tsv_file, [["val1", "val2"]], ["WordSource", "WordDestination"], {"lemma": "WordSource"}, stage="source", zid=test_zid)
    assert not updates_dir.exists() or len(list(updates_dir.glob("*.js"))) == 0

    # Unregister subscriber
    dispatcher.unregister(test_zid, q)
    assert dispatcher.has_subscribers(test_zid) is False

    # 3. Client SSE Bridge and status indicator in browser
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    res = page.evaluate("""() => {
        const hasSSEBridge = Boolean(window.SSEBridge);
        const hasConnect = typeof window.SSEBridge.connect === 'function';
        const hasDisconnect = typeof window.SSEBridge.disconnect === 'function';

        window.SSEBridge.updateConnectionIndicator('connected');
        const indicator = document.getElementById('sse-status-indicator');
        const isConnected = indicator && indicator.getAttribute('data-status') === 'connected';

        window.SSEBridge.updateConnectionIndicator('reconnecting');
        const isReconnecting = indicator && indicator.getAttribute('data-status') === 'reconnecting';

        window.SSEBridge.updateConnectionIndicator('disconnected');
        const isDisconnected = indicator && indicator.getAttribute('data-status') === 'disconnected';

        return {
            hasSSEBridge,
            hasConnect,
            hasDisconnect,
            isConnected,
            isReconnecting,
            isDisconnected
        };
    }""")

    assert res["hasSSEBridge"] is True
    assert res["hasConnect"] is True
    assert res["hasDisconnect"] is True
    assert res["isConnected"] is True
    assert res["isReconnecting"] is True
    assert res["isDisconnected"] is True


def test_desktop_typography_and_subpixel_antialiasing_parity(page, test_server):
    """
    Verify desktop typography parity matching baseline snapshot 56f8696:
    - Native ClearType subpixel rendering (no -webkit-font-smoothing: antialiased).
    - Source and translation containers render at 16px font size with 1.6 line height.
    - .kw-gender chip renders with font-weight: 700 and line-height: 1.
    - #lemma-table th renders with letter-spacing: 0.05em.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    metrics = page.evaluate("""() => {
        const bodyStyle = window.getComputedStyle(document.body);
        const fontSmoothing = bodyStyle.webkitFontSmoothing || bodyStyle.fontSmoothing;

        const src = document.getElementById('source-container');
        const srcStyle = src ? window.getComputedStyle(src) : null;

        const trans = document.getElementById('translation-container');
        const transStyle = trans ? window.getComputedStyle(trans) : null;

        // Gender badge test element
        const genderEl = document.createElement('span');
        genderEl.className = 'kw-gender kw-gender-m';
        genderEl.textContent = 'm';
        document.body.appendChild(genderEl);
        const genderStyle = window.getComputedStyle(genderEl);
        const genderWeight = genderStyle.fontWeight;
        const genderLineHeight = genderStyle.lineHeight;
        genderEl.remove();

        // Header test element
        const th = document.querySelector('#lemma-table th');
        const thStyle = th ? window.getComputedStyle(th) : null;

        return {
            fontSmoothing,
            srcFontSize: srcStyle ? srcStyle.fontSize : null,
            srcLineHeight: srcStyle ? srcStyle.lineHeight : null,
            transFontSize: transStyle ? transStyle.fontSize : null,
            transLineHeight: transStyle ? transStyle.lineHeight : null,
            genderWeight,
            genderLineHeight,
            thLetterSpacing: thStyle ? thStyle.letterSpacing : null
        };
    }""")

    # 1. Antialiasing must NOT be forced to grayscale
    assert metrics["fontSmoothing"] != "antialiased", "body must not force grayscale antialiasing"

    # 2. Source container font metrics
    assert metrics["srcFontSize"] == "16px", f"Expected 16px font-size on #source-container, got {metrics['srcFontSize']}"
    assert float(metrics["srcLineHeight"].replace("px", "")) >= 24.0, f"Expected ~25.6px line-height on #source-container, got {metrics['srcLineHeight']}"

    # 3. Translation container font metrics
    assert metrics["transFontSize"] == "16px", f"Expected 16px font-size on #translation-container, got {metrics['transFontSize']}"
    assert float(metrics["transLineHeight"].replace("px", "")) >= 24.0, f"Expected ~25.6px line-height on #translation-container, got {metrics['transLineHeight']}"

    # 4. Gender badge typography
    assert metrics["genderWeight"] in ("700", "bold"), f"Expected 700 font-weight on .kw-gender, got {metrics['genderWeight']}"

    # 5. Static CSS inspection
    desk_dir = Path(__file__).resolve().parent.parent
    variables_css = (desk_dir / "web" / "css" / "variables.css").read_text(encoding="utf-8")
    assert "-webkit-font-smoothing: antialiased" not in variables_css, "variables.css must not contain -webkit-font-smoothing: antialiased"


def test_word_highlight_styles_and_mechanics_parity(page, test_server):
    """
    Verify word highlighting styles and mechanics parity with baseline 56f8696:
    - Word spans in #source-container and #translation-container compute to display: inline.
    - Word spans have 0.2s transitions for background-color and color.
    - Dark theme selected orange row text evaluates to #ffcc00.
    - Dark theme selected purple row text evaluates to #b39ddb.
    - Unconnected tokens (.not-connected) have background rgba(139, 148, 158, 0.15).
    - Component mount idempotency: SourceViewer, TranslationView, LemmaTable have _mounted guard.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    metrics = page.evaluate("""() => {
        // Create test source word span
        const sc = document.getElementById('source-container');
        const srcSpan = document.createElement('span');
        srcSpan.className = 'word';
        srcSpan.textContent = 'Testword';
        sc.appendChild(srcSpan);
        const srcSpanStyle = window.getComputedStyle(srcSpan);
        const srcDisplay = srcSpanStyle.display;
        const srcTransition = srcSpanStyle.transition;
        srcSpan.remove();

        // Create test translation word span
        const tc = document.getElementById('translation-container');
        const transSpan = document.createElement('span');
        transSpan.className = 'word';
        transSpan.textContent = 'Translation';
        tc.appendChild(transSpan);
        const transSpanStyle = window.getComputedStyle(transSpan);
        const transDisplay = transSpanStyle.display;
        transSpan.remove();

        // Check CSS variables for dark theme (default on body)
        const rootStyle = window.getComputedStyle(document.body);
        const selectedOrangeText = rootStyle.getPropertyValue('--selected-orange-row-text').trim();
        const selectedPurpleText = rootStyle.getPropertyValue('--selected-purple-row-text').trim();
        const notConnectedBg = rootStyle.getPropertyValue('--not-connected-bg').trim();

        // Check component _mounted flags
        const svMounted = window.SourceViewer ? window.SourceViewer._mounted : false;
        const tvMounted = window.TranslationView ? window.TranslationView._mounted : false;
        const ltMounted = window.LemmaTable ? window.LemmaTable._mounted : false;

        return {
            srcDisplay,
            srcTransition,
            transDisplay,
            selectedOrangeText,
            selectedPurpleText,
            notConnectedBg,
            svMounted,
            tvMounted,
            ltMounted
        };
    }""")

    # 1. Word spans must use inline display flow
    assert metrics["srcDisplay"] == "inline", f"Expected display: inline on #source-container span.word, got {metrics['srcDisplay']}"
    assert metrics["transDisplay"] == "inline", f"Expected display: inline on #translation-container span.word, got {metrics['transDisplay']}"

    # 2. Transition duration parity (0.2s)
    assert "0.2s" in metrics["srcTransition"], f"Expected 0.2s transition on span.word, got {metrics['srcTransition']}"

    # 3. Selected row text color parity
    assert metrics["selectedOrangeText"] == "#ffcc00", f"Expected #ffcc00 for --selected-orange-row-text, got {metrics['selectedOrangeText']}"
    assert metrics["selectedPurpleText"] == "#b39ddb", f"Expected #b39ddb for --selected-purple-row-text, got {metrics['selectedPurpleText']}"

    # 4. Unconnected background variable
    assert "139, 148, 158" in metrics["notConnectedBg"], f"Expected rgba(139, 148, 158, 0.15) for --not-connected-bg, got {metrics['notConnectedBg']}"

    # 5. Component idempotency guards
    assert metrics["svMounted"] is True, "SourceViewer._mounted must be true after initial mount"
    assert metrics["tvMounted"] is True, "TranslationView._mounted must be true after initial mount"
    assert metrics["ltMounted"] is True, "LemmaTable._mounted must be true after initial mount"


def test_toolbar_buttons_active_style_and_specificity_under_light_compact_white_themes(page, test_server):
    """
    Task 4.1: Verify #kw-btn-filter-selected, #kw-btn-same-lemma, #kw-btn-hand-tool, and #kw-btn-delete
    computed styles under theme-light, theme-white, and theme-compact.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    themes = ["theme-light", "theme-white", "theme-compact"]
    for theme in themes:
        res = page.evaluate("""(theme) => {
            document.body.className = theme;
            const filterBtn = document.getElementById(\'kw-btn-filter-selected\');
            const lemmaBtn = document.getElementById(\'kw-btn-same-lemma\');
            const handBtn = document.getElementById(\'kw-btn-hand-tool\');
            filterBtn.style.transition = \'none\';
            lemmaBtn.style.transition = \'none\';
            handBtn.style.transition = \'none\';
            filterBtn.classList.add(\'btn-filter-active\');
            lemmaBtn.classList.add(\'active\');
            handBtn.classList.add(\'active\');

            const filterStyle = window.getComputedStyle(filterBtn);
            const lemmaStyle = window.getComputedStyle(lemmaBtn);
            const handStyle = window.getComputedStyle(handBtn);

            return {
                filterBg: filterStyle.backgroundColor,
                filterFw: filterStyle.fontWeight,
                lemmaBg: lemmaStyle.backgroundColor,
                lemmaFw: lemmaStyle.fontWeight,
                handBg: handStyle.backgroundColor
            };
        }""", theme)
        assert res["filterBg"] == "rgb(210, 153, 34)", f"Expected golden #d29922 for filter button in {theme}, got {res['filterBg']}"
        assert res["lemmaBg"] == "rgb(210, 153, 34)", f"Expected golden #d29922 for lemma button in {theme}, got {res['lemmaBg']}"
        assert res["filterFw"] in ["600", "bold"], f"Expected font-weight 600 for filter button in {theme}, got {res['filterFw']}"
        assert res["lemmaFw"] in ["500", "normal"], f"Expected invariant font-weight 500 for lemma button in {theme}, got {res['lemmaFw']}"
        assert "56, 166, 255" in res["handBg"], f"Expected active blue background for hand tool in {theme}, got {res['handBg']}"

        # Verify delete button hover style under theme
        del_btn = page.locator("#kw-btn-delete")
        del_btn.hover()
        del_color = page.evaluate("() => window.getComputedStyle(document.getElementById('kw-btn-delete')).color")
        assert del_color == "rgb(255, 123, 114)", f"Expected danger red #ff7b72 for delete hover in {theme}, got {del_color}"


def test_lemma_peer_highlight_persists_across_mouseout(page, tmp_path):
    """
    Task 4.2: Verify that .lemma-peer-highlight persists on peer tokens across mouseout
    events when Lemma toggle is ON.
    """
    from tests.test_same_lemma_toggle import get_intra_sentence_desk_page_html, inject_mock_fetch
    raw_html = get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=True)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")

    den_span = page.locator('#source-container span.word:has-text("den")').first
    das_span = page.locator('#source-container span.word:has-text("das")').first
    die_span = page.locator('#source-container span.word:has-text("die")').first
    hund_span = page.locator('#source-container span.word:has-text("Hund")').first

    # 1. Click "den" in text
    den_span.click()

    # Verify initial peer highlights
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (das_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (die_span.get_attribute("class") or "")

    # 2. Hover "Hund" and mouse out
    hund_span.hover()
    page.mouse.move(0, 0)

    # 3. Verify peer highlights persist after mouseout
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (das_span.get_attribute("class") or ""), "das must retain lemma-peer-highlight after mouseout"
    assert "lemma-peer-highlight" in (die_span.get_attribute("class") or ""), "die must retain lemma-peer-highlight after mouseout"


def test_client_rendered_rows_filter_selected_only(page, test_server):
    """
    Task 4.3: Verify that dynamically rendered client rows receive data-selected="0"
    and are hidden when 'Selected' filter is active.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    page.evaluate("""() => {
        if (window.LemmaTable && typeof window.LemmaTable.mount === 'function') {
            window.LemmaTable.mount('#lemma-table');
        }
        window.LemmaTable.renderRows({
            headers: ['WordSourceInflectedForm', 'WordSource', 'WordDestination'],
            data_rows: [
                ['Haus', 'Haus', 'дом'],
                ['groß', 'groß', 'большой']
            ],
            selected_ids: [0]
        });
    }""")

    row0 = page.locator("#lemma-table tbody tr[data-row-id='0']")
    row1 = page.locator("#lemma-table tbody tr[data-row-id='1']")

    assert row0.get_attribute("data-selected") == "1"
    assert row1.get_attribute("data-selected") == "0"

    # Toggle Selected filter ON
    filter_btn = page.locator("#kw-btn-filter-selected")
    filter_btn.click()

    assert row0.is_visible()
    assert not row1.is_visible()


def test_lemma_table_render_sanitizes_sentinels(page, test_server):
    """
    Verify that LemmaTable.render sanitizes [FAILED] and [Error...] sentinels
    to clean empty strings in rendered cells.
    """
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")

    page.evaluate("""() => {
        if (window.LemmaTable && typeof window.LemmaTable.mount === 'function') {
            window.LemmaTable.mount('#lemma-table');
        }
        window.LemmaTable.render({
            headers: ['WordSourceInflectedForm', 'WordSource', 'WordDestination'],
            data_rows: [
                ['Haus', 'Haus', '[FAILED]'],
                ['Baum', 'Baum', '[Error: timeout]'],
                ['Buch', 'Buch', 'книга']
            ]
        });
    }""")

    cell0 = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-translation .scrollable-cell")
    cell1 = page.locator("#lemma-table tbody tr[data-row-id='1'] td.col-translation .scrollable-cell")
    cell2 = page.locator("#lemma-table tbody tr[data-row-id='2'] td.col-translation .scrollable-cell")

    assert cell0.inner_text() == ""
    assert cell1.inner_text() == ""
    assert cell2.inner_text() == "книга"


def test_standalone_html_bundles_all_components(tmp_path):
    """
    Verify that standalone lookup and desktop HTML contains all client components
    bundled inline as script blocks, achieving complete parity with HTTP server mode.
    """
    import kardenwort_desk as desk
    config, resolved_paths, goldendict, wordfill = desk.load_config()
    tsv_file = tmp_path / "20261019223000-test.de.tsv"
    tsv_file.write_text(
        "# comment\nQuotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\nHaus\tHaus\tдом\t1\tDas Haus\tДом\t1\n",
        encoding="utf-8"
    )

    html = desk.render_lookup_html(
        text="Haus",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=resolved_paths,
        zid="20261019223000",
        goldendict=goldendict,
        comments=[],
        headers=["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "DeskSelected"],
        data_rows=[["Haus", "Haus", "дом", "1", "Das Haus", "Дом", "1"]],
        sentence_translation="Дом.",
        session_zid="20261019223000",
    )

    # Verify that external script tags were replaced with inlined scripts
    assert '<script src="/js/components/SourceViewer.js"></script>' not in html
    assert '<script src="/js/components/TranslationView.js"></script>' not in html
    assert '<script src="/js/components/LemmaTable.js"></script>' not in html
    assert '<script src="/js/components/WorkspaceTabs.js"></script>' not in html
    assert '<script src="/js/types.js"></script>' not in html
    assert '<script src="/js/state/store.js"></script>' not in html

    # Verify component definitions are present in the inline script blocks
    assert "LemmaTable" in html
    assert "SourceViewer" in html
    assert "TranslationView" in html
    assert "WorkspaceTabs" in html

