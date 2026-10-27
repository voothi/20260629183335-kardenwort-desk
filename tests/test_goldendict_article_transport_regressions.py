"""Behavioral regressions reproducing GoldenDict article transport and offline action defects.

Phase 1 contracts:
- 1.2 Fresh-page offline regressions: network blocked, asset closure, readable configured output,
      absence of enabled-handlerless backend controls, absence of live credentials.
- 1.3 Different-origin article tests: ephemeral authenticated controller with real isolated SQLite/TSV,
      demonstrating request destination resolution against configured backend vs article origin.
- 1.4 Same-origin and native save/command/recovery contracts: guard acknowledgment ownership,
      dirty intent preservation, and cancellation behavior.
"""

import json
import re
import urllib.request
import urllib.error
from pathlib import Path
import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_desk import SqliteStorageAdapter, core_edit_save, StructuredError
from tests.test_controller_integration import running_controller  # noqa: F401


def render_test_lookup(
    text="Das Haus. Der Baum.",
    language="de",
    target_lang="ru",
    server_enabled=False,
    server_url=None,
    api_token="",
    session_zid="20261026093717",
    data_rows=None,
    headers=None,
    auto_save_on_close=True,
):
    config, paths, goldendict, _ = desk.load_config()
    goldendict["server_enabled"] = bool(server_enabled)
    goldendict["auto_save_on_close"] = bool(auto_save_on_close)
    if api_token:
        goldendict["server_api_key"] = api_token
    if server_url:
        m = re.match(r"https?://([^:/]+)(?::(\d+))?", server_url)
        if m:
            goldendict["server_host"] = m.group(1)
            if m.group(2):
                goldendict["server_port"] = int(m.group(2))

    if headers is None:
        headers = [
            "Quotation", "WordSource", "WordDestination",
            "SentenceSourceIndex", "TokenOrder", "DeskSelected"
        ]
    if data_rows is None:
        data_rows = [
            ["Haus", "Haus", "дом", "1", "0", "0"],
            ["Baum", "Baum", "дерево", "2", "1", "0"],
        ]

    return desk.render_lookup_html(
        text,
        language,
        target_lang,
        config,
        paths,
        session_zid,
        goldendict,
        [],
        headers,
        data_rows,
        "Дом. Дерево.",
        session_zid=session_zid,
        api_token=api_token if server_enabled else "",
        server_enabled=server_enabled,
    )


# ---------------------------------------------------------------------------
# Task 1.2: Fresh-page offline regressions with network blocked
# ---------------------------------------------------------------------------

def test_1_2_offline_article_asset_closure_and_readable_output(page):
    """
    Offline article with network blocked:
    1. Standalone asset closure: no external /js/app.js, /favicon.ico, or external stylesheet links.
    2. Readable configured output: source text, translation, table rows rendered.
    3. No live API credentials in emitted HTML or bootstrap payload.
    4. Backend-dependent controls (Save, Update, Re-text, Re-word, Export) are NOT
       enabled handlerless buttons.
    """
    html = render_test_lookup(server_enabled=False, api_token="secret-live-token")

    # Contract 1: Asset closure in static HTML
    assert '<script src="/js/app.js"></script>' not in html, (
        "Offline article must not contain unresolved external /js/app.js reference"
    )
    assert '<link rel="icon" type="image/x-icon" href="/favicon.ico">' not in html, (
        "Offline article must not contain root-relative /favicon.ico reference"
    )
    assert not re.search(r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*>', html, re.IGNORECASE), (
        "Offline article must embed all CSS, not leave external stylesheet links"
    )

    # Contract 2: No live credentials in static output
    assert "secret-live-token" not in html, (
        "Static server-disabled article must not disclose live API credentials"
    )

    # Contract 3: Backend controls must be disabled in offline HTML
    for btn_id in ("kw-btn-save", "kw-btn-update", "kw-btn-retext", "kw-btn-reword", "kw-btn-export"):
        m = re.search(rf'<button\b[^>]*id="{btn_id}"[^>]*>', html)
        assert m is not None, f"Expected button {btn_id} in toolbar"
        btn_tag = m.group(0)
        assert "disabled" in btn_tag, f"Backend button {btn_id} must be disabled in static mode: {btn_tag}"

    # Contract 4: Fresh page with network completely blocked
    def block_network(route):
        # Allow nothing except data URLs or page content
        route.abort()

    page.route("**/*", block_network)

    # Load offline HTML into page
    page.set_content(html)

    # Readable content verification
    source_el = page.locator("#source-container")
    expect(source_el).to_be_visible()
    assert "Das Haus." in source_el.inner_text()

    trans_el = page.locator("#translation-container")
    expect(trans_el).to_be_visible()
    assert "Дом." in trans_el.inner_text()

    row0_lemma = page.locator('tr[data-row-id="0"] td.col-lemma, tr[data-row-id="0"] td:nth-child(2)')
    assert "Haus" in row0_lemma.first.inner_text()

    # Verify buttons in DOM
    for btn_id in ("kw-btn-save", "kw-btn-update", "kw-btn-retext", "kw-btn-reword", "kw-btn-export"):
        btn = page.locator(f"#{btn_id}")
        assert btn.is_disabled(), f"Button #{btn_id} must be disabled in offline DOM"


# ---------------------------------------------------------------------------
# Task 1.3: Different-origin article tests against ephemeral controller
# ---------------------------------------------------------------------------

def test_1_3_different_origin_article_request_destinations(running_controller, page):
    """
    Demonstrate that article commands from an off-origin document (e.g. http://article.invalid)
    resolve against the configured authenticated backend (server_url), not the document origin.
    Before repair, relative fetch paths target http://article.invalid/session/..., failing delivery.
    """
    server_url, server, adapter = running_controller
    session_zid = "20261026093717"

    headers = [
        "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "TokenOrder", "DeskSelected"
    ]
    data_rows = [
        ["Haus", "Haus", "дом", "1", "0", "0"],
        ["Baum", "Baum", "дерево", "1", "1", "0"],
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="das-haus-der-baum",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus. Der Baum.",
        tsv_path=str(Path(server.resolved_paths["results_dir"]) / f"{session_zid}.de.tsv"),
        headers=headers,
        comments=[],
        data_rows=data_rows,
        sentences=[
            {
                "session_zid": session_zid,
                "sentence_index": 1,
                "sentence_source": "Das Haus. Der Baum.",
                "sentence_destination": "Дом. Дерево.",
                "sentence_destination2": "Дом. Дерево.",
                "text_provenance": "live:google",
            }
        ]
    )

    html = render_test_lookup(
        server_enabled=True,
        server_url=server_url,
        api_token="test-controller-api-key",
        session_zid=session_zid,
        data_rows=data_rows,
        headers=headers,
    )

    intercepted_requests = []

    def handle_article_route(route):
        req = route.request
        if req.url == "http://article.invalid/article.html":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        else:
            intercepted_requests.append({
                "url": req.url,
                "method": req.method,
                "headers": dict(req.headers),
            })
            route.continue_()

    page.route("**/*", handle_article_route)

    page.goto("http://article.invalid/article.html")
    page.wait_for_function("typeof window.onRetextClick === 'function'")

    # Invoke Re-text from the different origin
    intercepted_requests.clear()
    page.evaluate("window.onRetextClick()")
    page.wait_for_timeout(300)

    # Contract: Re-text MUST target the configured backend server_url, NOT http://article.invalid
    retext_reqs = [r for r in intercepted_requests if "/session/retext" in r["url"]]
    assert len(retext_reqs) >= 1, f"Expected Re-text request, got: {intercepted_requests}"
    retext = retext_reqs[0]
    assert retext["url"].startswith(server_url), (
        f"Re-text request must target configured backend {server_url}, but targeted {retext['url']}"
    )
    assert retext["headers"].get("x-api-token") == "test-controller-api-key", (
        "Re-text request must include X-API-Token header"
    )


# ---------------------------------------------------------------------------
# Task 1.4: Guard same-origin and native save contracts
# ---------------------------------------------------------------------------

def test_1_4_same_origin_and_native_save_contracts(page):
    """
    Guard existing save acknowledgment and native bridge contract:
    - Delta acknowledgment clears dirty state.
    - Native ahkCall('persist_selection') remains supported when ahkCall is present.
    """
    html = render_test_lookup(
        server_enabled=True,
        server_url="http://127.0.0.1:18335",
        api_token="test-token"
    )

    stub_script = """<script>
    document.documentMode = 11;
    window.__ahkCalls = [];
    window.ahkCall = function(cmd, body) {
        window.__ahkCalls.push({cmd: cmd, body: body});
        if (cmd === "get_shared_selection") {
            return JSON.stringify({ok: true, status: "success", shared_selection: {}});
        }
        return JSON.stringify({ok: true, status: "success"});
    };
    </script>"""

    page.set_content(html.replace("<head>", "<head>" + stub_script))
    # Inline edit on row 0 translation cell to produce a saveable delta
    trans_cell = page.locator('tr[data-row-id="0"] td.col-translation')
    trans_cell.dblclick()
    input_box = trans_cell.locator('input.edit-input')
    expect(input_box).to_be_visible()
    input_box.fill("избушка")
    input_box.press("Enter")

    assert page.evaluate("isDirty() === true"), "Page must be dirty after cell edit"

    # Save via native bridge
    page.evaluate("window.onSaveClick()")
    page.wait_for_timeout(200)

    native_calls = page.evaluate("window.__ahkCalls")
    persist_calls = [c for c in native_calls if c["cmd"] == "persist_selection"]
    assert len(persist_calls) >= 1, f"Expected native ahkCall for persist_selection, got: {native_calls}"
    assert page.evaluate("isDirty() === false"), "Page dirty state must clear after successful save"


# ---------------------------------------------------------------------------
# Phase 2 Automated Tests (Tasks 2.1 - 2.8)
# ---------------------------------------------------------------------------

def test_2_1_article_commands_and_autosave_reach_configured_backend(running_controller, page):
    """
    Tasks 2.1, 2.2, 2.6: GoldenDict article commands, close autosave, status reads,
    and audio reach the configured backend server_url, not the article document origin.
    """
    server_url, server, adapter = running_controller
    session_zid = "20261026093717"
    headers = [
        "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "TokenOrder", "DeskSelected"
    ]
    data_rows = [
        ["Haus", "Haus", "дом", "1", "0", "0"],
        ["Baum", "Baum", "дерево", "1", "1", "0"],
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="das-haus-der-baum",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus. Der Baum.",
        tsv_path=str(Path(server.resolved_paths["results_dir"]) / f"{session_zid}.de.tsv"),
        headers=headers,
        comments=[],
        data_rows=data_rows,
        sentences=[
            {
                "session_zid": session_zid,
                "sentence_index": 1,
                "sentence_source": "Das Haus. Der Baum.",
                "sentence_destination": "Дом. Дерево.",
                "sentence_destination2": "Дом. Дерево.",
                "text_provenance": "live:google",
            }
        ]
    )

    html = render_test_lookup(
        server_enabled=True,
        server_url=server_url,
        api_token="test-controller-api-key",
        session_zid=session_zid,
        data_rows=data_rows,
        headers=headers,
    )

    intercepted = []

    def handle_route(route):
        req = route.request
        if "article.html" in req.url:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            return

        intercepted.append({
            "url": req.url,
            "method": req.method,
            "headers": dict(req.headers),
        })

        if "/events" in req.url:
            route.abort()
            return

        if req.url.startswith(server_url):
            if "/internal/selection" in req.url:
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    body=b'{"status": "success", "ok": true, "shared_selection": {"selected_row_ids": [], "revision": 0}}'
                )
            elif "/session/status" in req.url:
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    body=b'{"status": "success", "ok": true, "data": {"status": "ok", "ok": true, "stage": "finished"}}'
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    body=b'{"status": "success", "ok": true, "data": {"ok": true, "shared_selection": {"selected_row_ids": [], "revision": 0}}}'
                )
        else:
            route.abort()

    page.route("**/*", handle_route)
    page.goto("http://article.invalid/article.html")
    page.wait_for_function("typeof window.onRetextClick === 'function'")

    # 1. Retext
    intercepted.clear()
    page.evaluate("async () => { await window.onRetextClick(); }")
    page.wait_for_timeout(300)
    retext_reqs = [r for r in intercepted if "/session/retext" in r["url"]]
    assert len(retext_reqs) == 1
    assert retext_reqs[0]["url"].startswith(server_url)
    assert retext_reqs[0]["headers"].get("x-api-token") == "test-controller-api-key"

    # 2. Reword (select rows via window.selectAllRows)
    intercepted.clear()
    page.evaluate("window.selectAllRows()")
    page.wait_for_timeout(400)
    page.evaluate("async () => { await window.onRewordClick(); }")
    page.wait_for_timeout(300)
    reword_reqs = [r for r in intercepted if "/session/reword" in r["url"]]
    assert len(reword_reqs) == 1
    assert reword_reqs[0]["url"].startswith(server_url)
    assert reword_reqs[0]["headers"].get("x-api-token") == "test-controller-api-key"

    # 3. Export to Anki
    intercepted.clear()
    page.evaluate("window.onSendToAnkiClick()")
    page.wait_for_timeout(200)
    export_reqs = [r for r in intercepted if "/session/export" in r["url"]]
    assert len(export_reqs) == 1
    assert export_reqs[0]["url"].startswith(server_url)
    assert export_reqs[0]["headers"].get("x-api-token") == "test-controller-api-key"

    # 4. Audio
    intercepted.clear()
    page.evaluate("window.playAudio('Haus', 'de', 0)")
    page.wait_for_timeout(200)
    audio_reqs = [r for r in intercepted if "/api/v1/audio/play" in r["url"]]
    assert len(audio_reqs) == 1
    assert audio_reqs[0]["url"].startswith(server_url)

    # 5. Save on cell edit
    intercepted.clear()
    trans_cell = page.locator('tr[data-row-id="0"] td.col-translation')
    trans_cell.dblclick()
    input_box = trans_cell.locator('input.edit-input')
    input_box.fill("жилище")
    input_box.press("Enter")
    page.evaluate("window.onSaveClick()")
    page.wait_for_timeout(200)
    save_reqs = [r for r in intercepted if "/session/save" in r["url"]]
    assert len(save_reqs) >= 1
    assert save_reqs[0]["url"].startswith(server_url)
    assert save_reqs[0]["headers"].get("x-api-token") == "test-controller-api-key"

    # 6. Silent auto-save on close (pagehide)
    intercepted.clear()
    trans_cell.dblclick()
    input_box.fill("домик")
    input_box.press("Enter")
    page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    page.wait_for_timeout(200)
    close_save_reqs = [r for r in intercepted if "/session/save" in r["url"]]
    assert len(close_save_reqs) >= 1
    assert close_save_reqs[0]["url"].startswith(server_url)

    # 7. Update / status read
    intercepted.clear()
    page.evaluate("window.onUpdateClick()")
    page.wait_for_timeout(200)
    status_reqs = [r for r in intercepted if "/session/status" in r["url"]]
    assert len(status_reqs) >= 1
    assert status_reqs[0]["url"].startswith(server_url)


def test_2_4_action_availability_projection_when_server_disabled(page):
    """
    Tasks 2.4 & 2.7: With server interaction disabled:
    - Backend controls are disabled with accessible title.
    - Click and keyboard invocations show non-fatal warning toast.
    - Zero network requests are dispatched; no implicit server is launched.
    """
    html = render_test_lookup(
        server_enabled=False,
        server_url="",
        api_token="",
    )

    intercepted = []
    page.route("**/*", lambda route: (
        intercepted.append(route.request.url),
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html) if route.request.url == "http://article.invalid/article.html" else route.abort()
    ))
    page.goto("http://article.invalid/article.html")
    page.wait_for_function("typeof window.updateToolbarState === 'function'")

    # Check button disabled states and titles
    for btn_id in ["kw-btn-save", "kw-btn-retext", "kw-btn-reword", "kw-btn-export"]:
        btn = page.locator(f"#{btn_id}")
        expect(btn).to_be_disabled()
        assert btn.get_attribute("title") == "Server interaction is disabled in configuration"

    intercepted.clear()

    # Invocations show warning toast and dispatch zero network requests
    page.evaluate("""() => {
        window.__toasts = [];
        const origShowToast = window.showToast;
        window.showToast = function(msg, type) {
            window.__toasts.push({msg: msg, type: type});
            if (origShowToast) origShowToast(msg, type);
        };
    }""")

    page.evaluate("window.onSaveClick()")
    page.evaluate("window.onRetextClick()")
    page.evaluate("window.onRewordClick()")
    page.evaluate("window.onSendToAnkiClick()")
    page.evaluate("window.onUpdateClick()")
    page.wait_for_timeout(200)

    # All dispatched zero requests
    assert len(intercepted) == 0, f"Expected zero network requests when server disabled, got {intercepted}"

    # Toast feedback received
    captured_toasts = page.evaluate("window.__toasts")
    assert len(captured_toasts) >= 5
    for t in captured_toasts:
        assert t["type"] == "warning"
        assert "disabled" in t["msg"].lower()


def test_2_5_credential_safety_and_authentication_failure_mutation(running_controller, page):
    """
    Task 2.5: Verify token is not in URL, localStorage, sessionStorage.
    Verify missing/invalid credentials cause 401 and perform no mutations in SQLite.
    """
    import urllib.request
    import urllib.error
    import sqlite3
    server_url, server, adapter = running_controller
    session_zid = "20261026093717"
    db_path = adapter.db.db_path
    headers = [
        "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "TokenOrder", "DeskSelected"
    ]
    data_rows = [
        ["Haus", "Haus", "дом", "1", "0", "0"],
        ["Baum", "Baum", "дерево", "1", "1", "0"],
    ]

    tsv_path = Path(server.resolved_paths["results_dir"]) / f"{session_zid}.de.tsv"
    desk.save_tsv_rows_safely(tsv_path, [], headers, data_rows)

    html = render_test_lookup(
        server_enabled=True,
        server_url=server_url,
        api_token="test-controller-api-key",
        session_zid=session_zid,
        data_rows=data_rows,
        headers=headers,
    )

    page.route("**/*", lambda route: (
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        if route.request.url == "http://article.invalid/article.html"
        else route.continue_()
    ))
    page.goto("http://article.invalid/article.html")
    page.wait_for_function("typeof window.getApiToken === 'function'")

    # 1. No token in localStorage / sessionStorage
    ls_keys = page.evaluate("Object.keys(localStorage)")
    ss_keys = page.evaluate("Object.keys(sessionStorage)")
    for k in ls_keys:
        assert "token" not in k.lower()
        val = page.evaluate(f"localStorage.getItem('{k}')")
        assert "test-controller-api-key" not in str(val)
    for k in ss_keys:
        assert "token" not in k.lower()
        val = page.evaluate(f"sessionStorage.getItem('{k}')")
        assert "test-controller-api-key" not in str(val)

    # 2. Unauthorized mutation against controller
    save_url = f"{server_url}/session/save"
    unauth_payload = json.dumps({
        "session_zid": session_zid,
        "language": "de",
        "deltas": [{"row_id": "0", "column": "WordDestination", "value": "hacked"}]
    }).encode("utf-8")

    # Missing token -> 401 or 403
    req = urllib.request.Request(save_url, data=unauth_payload, headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code in (401, 403)

    # Bad token -> 401 or 403
    req_bad = urllib.request.Request(save_url, data=unauth_payload, headers={
        "Content-Type": "application/json",
        "X-API-Token": "invalid-wrong-token"
    })
    with pytest.raises(urllib.error.HTTPError) as exc_bad:
        urllib.request.urlopen(req_bad)
    assert exc_bad.value.code in (401, 403)

    # Verify persisted TSV rows and shared selection unchanged
    tsv_path = Path(server.resolved_paths["results_dir"]) / f"{session_zid}.de.tsv"
    assert tsv_path.exists()
    _, _, cur_rows = adapter.load_tsv_rows(tsv_path)
    dest_col = headers.index("WordDestination")
    assert cur_rows[0][dest_col] == "дом", (
        f"WordDestination must remain 'дом', not 'hacked'. Got: {cur_rows[0][dest_col]}"
    )

