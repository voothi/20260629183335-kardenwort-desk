"""Acceptance and control tests for authenticated browser tab spawning.

Verifies POST /api/v1/spawn-tabs against the real ControllerRequestHandler,
real isolated SQLite state, and fresh Playwright Chromium contexts, intercepting
only external host effect ports (webbrowser.open_new_tab and spawn_ahk).
"""
import json
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
from kardenwort_state_runtime import preference_store
from tests.test_controller_integration import running_controller  # noqa: F401


CONTROL_ZID = "20261119085301"
DRAFT_MULTI_ZID = "20261119085310"
HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "WordDestination",
    "DeskSelected",
]


def _seed_session(adapter, zid=CONTROL_ZID):
    adapter.save_session(
        session_zid=zid,
        slug="spawn-tabs-auth-control",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus ist alt.",
        headers=HEADERS,
        data_rows=[["0", "Haus", "Haus", "1", "Das Haus ist alt.", "house", "house", "0"]],
        sentences=[{
            "session_zid": zid,
            "sentence_index": 1,
            "sentence_source": "Das Haus ist alt.",
            "sentence_destination": "house",
            "sentence_destination2": "house",
            "text_provenance": "live:test",
        }],
    )


def _set_unconfigured_key(server, mode):
    if mode == "absent":
        if hasattr(server, "api_key"):
            delattr(server, "api_key")
        if server.config.has_option("server", "api_key"):
            server.config.remove_option("server", "api_key")
    elif mode == "empty":
        server.api_key = ""
        server.config.set("server", "api_key", "")
    elif mode == "whitespace":
        server.api_key = "   \t\n  "
        server.config.set("server", "api_key", "   \t\n  ")
    else:
        raise ValueError(f"Unknown key mode: {mode}")


def _http_json(url, *, method="POST", headers=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=8)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(raw)


def _client_id_from_page(page):
    return page.evaluate("""() => {
        const m = location.hash.match(/(?:^#|&)kw-client=([0-9a-f]{32})(?:&|$)/);
        return m ? m[1] : null;
    }""")


def _assert_no_credential_in_url_or_storage(page, secret):
    assert secret not in page.url, "Credential must never appear in page URL"
    storage_dump = page.evaluate("""() => ({
        local: Object.assign({}, localStorage),
        session: Object.assign({}, sessionStorage),
    })""")
    assert secret not in json.dumps(storage_dump), "Credential must never be persisted in browser storage"


@pytest.mark.parametrize("case_name,extra_headers,body_token", [
    ("missing_token", {}, None),
    ("loopback_origin_missing_token", {"Origin": "__SAME_ORIGIN__"}, None),
    ("wrong_x_api_token", {"X-API-Token": "wrong-controller-key"}, None),
    ("wrong_x_api_key", {"X-API-Key": "wrong-controller-key"}, None),
    ("wrong_bearer", {"Authorization": "Bearer wrong-controller-key"}, None),
    ("wrong_body_token", {}, "wrong-controller-key"),
    ("empty_x_api_token", {"X-API-Token": ""}, None),
])
def test_spawn_tabs_rejects_unauthenticated_and_wrong_credentials_without_opening_tabs(
    running_controller, monkeypatch, case_name, extra_headers, body_token
):
    """Delta scenario 'Unauthenticated command': reject missing/wrong credentials before host browser effects."""
    server_url, server, adapter = running_controller
    _seed_session(adapter)
    before_prefs = preference_store(server.config, server.resolved_paths).snapshot()
    before_rows = adapter.restore_session(CONTROL_ZID)["data_rows"]

    opened_urls = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_urls.append(url))

    headers = {
        k: (server_url if v == "__SAME_ORIGIN__" else v)
        for k, v in extra_headers.items()
    }
    payload = {
        "urls": [f"/session/render?session_zid={CONTROL_ZID}&seq_num=1&bypass_lang_check=true"],
    }
    if body_token is not None:
        payload["token"] = body_token

    status, resp = _http_json(
        f"{server_url}/api/v1/spawn-tabs",
        method="POST",
        headers=headers,
        body=payload,
    )

    assert status == 403, f"[{case_name}] expected HTTP 403, observed {status}: {resp}"
    assert resp.get("status") == "error"
    assert resp.get("error_code") == "UNAUTHORIZED", f"[{case_name}] expected UNAUTHORIZED, got {resp}"
    assert opened_urls == [], f"[{case_name}] host browser must receive zero open requests, got {opened_urls}"
    assert preference_store(server.config, server.resolved_paths).snapshot() == before_prefs
    assert adapter.restore_session(CONTROL_ZID)["data_rows"] == before_rows


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
@pytest.mark.parametrize("extra_headers", [{}, {"X-API-Token": ""}, {"Origin": "null"}])
def test_spawn_tabs_rejects_unconfigured_runtime_key_before_host_effects(
    running_controller, monkeypatch, key_mode, extra_headers
):
    """Delta scenario 'Unconfigured controller': reject explicitly with TOKEN_NOT_CONFIGURED before host effects."""
    server_url, server, adapter = running_controller
    _seed_session(adapter)
    _set_unconfigured_key(server, key_mode)

    opened_urls = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_urls.append(url))

    status, resp = _http_json(
        f"{server_url}/api/v1/spawn-tabs",
        method="POST",
        headers=extra_headers,
        body={"urls": [f"/session/render?session_zid={CONTROL_ZID}&seq_num=1&bypass_lang_check=true"]},
    )

    assert status == 403, f"[{key_mode}] expected HTTP 403, observed {status}: {resp}"
    assert resp.get("status") == "error"
    assert resp.get("error_code") == "TOKEN_NOT_CONFIGURED", f"[{key_mode}] expected TOKEN_NOT_CONFIGURED, got {resp}"
    assert opened_urls == [], f"[{key_mode}] host browser must receive zero open requests, got {opened_urls}"
    assert str(getattr(server, "api_key", "") or "").strip() == ""


@pytest.mark.parametrize("carrier", ["x_api_token", "x_api_key", "bearer", "body_token"])
def test_spawn_tabs_authenticated_command_and_origin_controls(
    running_controller, monkeypatch, carrier
):
    """Delta scenario 'Authenticated command' + adjacent origin/method/children-payload controls."""
    server_url, server, adapter = running_controller
    _seed_session(adapter)
    configured_key = server.api_key

    opened_urls = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_urls.append(url))

    # 1. Supported authenticated carriers preserve relative and absolute HTTP(S) URL behavior
    headers = {}
    body = {
        "urls": [
            f"/session/render?session_zid={CONTROL_ZID}&seq_num=1&bypass_lang_check=true",
            f"{server_url}/session/render?session_zid={CONTROL_ZID}&seq_num=2&bypass_lang_check=true",
            "https://example.invalid/reference",
        ]
    }
    if carrier == "x_api_token":
        headers["X-API-Token"] = configured_key
    elif carrier == "x_api_key":
        headers["X-API-Key"] = configured_key
    elif carrier == "bearer":
        headers["Authorization"] = f"Bearer {configured_key}"
    elif carrier == "body_token":
        body["token"] = configured_key

    status, resp = _http_json(f"{server_url}/api/v1/spawn-tabs", method="POST", headers=headers, body=body)
    assert status == 200, f"[{carrier}] expected 200, got {status}: {resp}"
    assert resp.get("status") == "success"
    data = resp.get("data", {})
    expected_urls = [
        f"{server_url}/session/render?session_zid={CONTROL_ZID}&seq_num=1&bypass_lang_check=true",
        f"{server_url}/session/render?session_zid={CONTROL_ZID}&seq_num=2&bypass_lang_check=true",
        "https://example.invalid/reference",
    ]
    assert data == {"ok": True, "spawned": 3, "urls": expected_urls}
    assert opened_urls == expected_urls
    assert all(configured_key not in u and "token=" not in u for u in opened_urls)

    # 2. Children payload fallback compatibility without leaking global key in constructed URL
    opened_urls.clear()
    status_ch, resp_ch = _http_json(
        f"{server_url}/api/v1/spawn-tabs",
        method="POST",
        headers={"X-API-Token": configured_key},
        body={"children": [{"zid": CONTROL_ZID, "seq_num": "2"}]},
    )
    assert status_ch == 200
    expected_child_url = f"{server_url}/session/render?session_zid={CONTROL_ZID}&seq_num=2&bypass_lang_check=true"
    assert resp_ch["data"] == {"ok": True, "spawned": 1, "urls": [expected_child_url]}
    assert opened_urls == [expected_child_url]
    assert configured_key not in expected_child_url and "token=" not in expected_child_url

    # 3. Existing origin validator remains active even when a valid credential is supplied
    opened_urls.clear()
    status_origin, resp_origin = _http_json(
        f"{server_url}/api/v1/spawn-tabs",
        method="POST",
        headers={"X-API-Token": configured_key, "Origin": "https://evil.example"},
        body={"urls": [f"/session/render?session_zid={CONTROL_ZID}&seq_num=1&bypass_lang_check=true"]},
    )
    assert status_origin == 403
    assert resp_origin.get("error_code") == "UNAUTHORIZED"
    assert opened_urls == []

    # 4. Non-POST method remains rejected before host effects
    status_get, resp_get = _http_json(
        f"{server_url}/api/v1/spawn-tabs",
        method="GET",
        headers={"X-API-Token": configured_key},
    )
    assert status_get == 405
    assert resp_get.get("error_code") == "METHOD_NOT_ALLOWED"
    assert opened_urls == []


def test_browser_confirm_language_spawns_tabs_with_bootstrapped_credential_and_fresh_browser_followthrough(
    browser, running_controller, monkeypatch
):
    """Coordinated real-browser acceptance (Tasks 2.2 & 4.1):
    1. Fresh browser renders draft mismatch page without a global-key URL.
    2. Clicking 'Yes' commits language, renders multi-sentence children, and calls
       POST /api/v1/spawn-tabs with the bootstrapped X-API-Token header.
    3. Opening a generated child controller URL in a fresh browser proves admitted
       bootstrap and authenticated /api/v1/client-view commands work without a global-key URL.
    """
    server_url, server, adapter = running_controller
    expected_key = server.api_key
    server.config.read_dict({
        "rendering": {"display_mode": "monolithic"},
        "sentences_mode": {"enabled": "true", "delivery_mode": "multi_window", "parent_mode": "full"},
        "wordfill": {"enabled": "false"},
        "triggers": {
            "run_text_translation": "auto",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
        "settings": {"default_language": "en"},
    })
    monkeypatch.setattr(controller, "spawn_ahk", lambda *a, **kw: None)

    opened_urls = []
    spawn_done = threading.Event()

    def record_open(url):
        opened_urls.append(url)
        if len(opened_urls) >= 2:
            spawn_done.set()

    monkeypatch.setattr(webbrowser, "open_new_tab", record_open)

    draft_entry = {
        "text": "Das Haus ist alt. Der Baum ist hoch.",
        "language": "en",
        "text_mode": "multi",
        "theme": "dark",
        "zoom": "100",
        "tsv_path": None,
        "seq_num": None,
        "mismatch_info": {
            "is_mismatch": True,
            "detected_language": "de",
            "expected_language": "en",
            "detected_name": "German",
            "expected_name": "English",
            "confidence": 0.99,
            "action": "prompt",
            "text": "Das Haus ist alt. Der Baum ist hoch.",
            "session_zid": DRAFT_MULTI_ZID,
        },
    }
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[DRAFT_MULTI_ZID] = draft_entry

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(30000)

        doc_resp = page.goto(f"{server_url}/session/render?session_zid={DRAFT_MULTI_ZID}", wait_until="domcontentloaded")
        assert doc_resp is not None and doc_resp.status == 200
        _assert_no_credential_in_url_or_storage(page, expected_key)

        yes_btn = page.locator("#kw-btn-lang-yes")
        expect(yes_btn).to_be_visible()
        with page.expect_request(lambda req: "/api/v1/spawn-tabs" in req.url, timeout=30000) as spawn_req_info:
            yes_btn.click()

        spawn_req = spawn_req_info.value
        assert spawn_req.method == "POST"
        assert (spawn_req.headers.get("x-api-token", "") == expected_key) is True
        assert spawn_done.wait(timeout=10.0), (
            f"Expected host webbrowser.open_new_tab for child sessions; opened={opened_urls}"
        )
        assert len(opened_urls) == 2
        assert all(expected_key not in u and "token=" not in u for u in opened_urls)
        assert all(u.startswith(f"{server_url}/session/render?session_zid=") for u in opened_urls)
    finally:
        context.close()
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(DRAFT_MULTI_ZID, None)

    # Follow the generated controller URL in a brand-new fresh browser context (zero injected credentials)
    child_url = opened_urls[0]
    fresh_context = browser.new_context()
    try:
        child_page = fresh_context.new_page()
        child_page.set_default_timeout(10000)
        page_errors = []
        child_page.on("pageerror", lambda err: page_errors.append(str(err)))

        child_resp = child_page.goto(child_url, wait_until="domcontentloaded")
        assert child_resp is not None and child_resp.status == 200
        expect(child_page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
        assert page_errors == []
        assert (child_page.evaluate("() => window.getApiToken()") == expected_key) is True
        _assert_no_credential_in_url_or_storage(child_page, expected_key)

        rendered_zid = child_page.evaluate("() => window.getSessionZid()")
        client_id = _client_id_from_page(child_page)
        assert rendered_zid and client_id
        with child_page.expect_response(lambda r: "/api/v1/client-view" in r.url) as save_info:
            child_page.evaluate("() => window.saveClientView()")
        assert save_info.value.status == 200
        with adapter.db.get_connection(read_only=True) as conn:
            row = conn.execute(
                "SELECT session_zid, client_id, revision FROM client_view_state WHERE session_zid=? AND client_id=?",
                (rendered_zid, client_id),
            ).fetchone()
            assert row is not None and row["revision"] >= 1
    finally:
        fresh_context.close()


def test_browser_invalid_spawn_request_opens_no_host_or_fallback_tabs(
    browser, running_controller, monkeypatch
):
    """Task 4.1: an unauthenticated/invalid spawn-tabs request from a browser opens zero host and fallback tabs."""
    server_url, server, adapter = running_controller
    _seed_session(adapter)
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "true")
    server.config.set("sentences_mode", "delivery_mode", "multi_window")

    opened_host_urls = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_host_urls.append(url))

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(10000)

        # Strip X-API-Token from /api/v1/spawn-tabs at the browser network boundary to simulate an invalid request
        def strip_spawn_auth(route):
            headers = {k: v for k, v in route.request.headers.items() if k.lower() != "x-api-token"}
            route.continue_(headers=headers)

        page.route("**/api/v1/spawn-tabs", strip_spawn_auth)

        doc_resp = page.goto(f"{server_url}/session/render?session_zid={CONTROL_ZID}", wait_until="domcontentloaded")
        assert doc_resp is not None and doc_resp.status == 200
        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")

        # Track the actual /api/v1/spawn-tabs fetch promise deterministically without timers
        page.evaluate("""() => {
            window.__fallbackOpened = [];
            window.__spawnSettled = false;
            window.open = function(url, target) {
                window.__fallbackOpened.push({url: url, target: target});
                return null;
            };
        }""")

        with page.expect_response(lambda r: "/api/v1/spawn-tabs" in r.url) as spawn_resp_info:
            page.evaluate(
                """(zid) => {
                    window.__langMismatchInfo = {
                        is_mismatch: true,
                        detected_language: 'de',
                        expected_language: 'en',
                        text: 'Das Haus ist alt.',
                        session_zid: zid,
                    };
                    const origFetch = window.fetch.bind(window);
                    window.fetch = function(url, opts) {
                        if (String(url).indexOf('/api/v1/render') !== -1) {
                            return Promise.resolve({
                                ok: true,
                                status: 200,
                                json: function() {
                                    return Promise.resolve({
                                        status: 'success',
                                        data: {
                                            html: null,
                                            children: [{zid: zid, seq_num: 2}]
                                        }
                                    });
                                }
                            });
                        }
                        if (String(url).indexOf('/api/v1/spawn-tabs') !== -1) {
                            const p = origFetch(url, opts);
                            p.finally(() => {
                                Promise.resolve().then(() => { window.__spawnSettled = true; });
                            });
                            return p;
                        }
                        return origFetch(url, opts);
                    };
                    window.onLangNo();
                }""",
                CONTROL_ZID,
            )

        assert spawn_resp_info.value.status == 403, (
            f"Expected 403 for unauthenticated browser spawn-tabs, got {spawn_resp_info.value.status}"
        )
        page.wait_for_function("() => window.__spawnSettled === true")
        fallback_opened = page.evaluate("() => window.__fallbackOpened")
        assert opened_host_urls == [], f"Host browser must receive zero open requests, got {opened_host_urls}"
        assert fallback_opened == [], f"Auth-rejected spawn-tabs must not trigger window.open fallback, got {fallback_opened}"
    finally:
        context.close()
