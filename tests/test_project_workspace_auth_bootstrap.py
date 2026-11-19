"""Phase 1 acceptance and control tests for project/draft/session auth bootstrap.

These tests exercise real HTTP controller routes, real isolated SQLite state,
and fresh Chromium browser contexts with no injected authentication headers.
Tests covering unrepaired defects deliberately fail until Phase 2 repairs the
bootstrap and coordinated admission boundary.
"""
import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401


SESSION_ZID_1 = "20261118201401"
SESSION_ZID_2 = "20261118201402"
DRAFT_ZID = "20261118201403"
SPECIAL_API_KEY = 'owned-key-"\\</script><img src=x onerror=window.__xss=1>'

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


def _seed_owned_session(adapter, zid, slug, sentence, quotation, lemma, translation):
    rows = [[
        "0",
        quotation,
        lemma,
        "1",
        sentence,
        translation,
        translation,
        "1",
    ]]
    adapter.save_session(
        session_zid=zid,
        slug=slug,
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text=sentence,
        headers=HEADERS,
        data_rows=rows,
        sentences=[{
            "session_zid": zid,
            "sentence_index": 1,
            "sentence_source": sentence,
            "sentence_destination": translation,
            "sentence_destination2": translation,
            "text_provenance": "live:test",
        }],
    )


def _seed_owned_project(server, adapter, tmp_path):
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    server.resolved_paths["favorites_output_dir"] = str(fav_dir)
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "true")
    server.config.set("sentences_mode", "delivery_mode", "container")

    _seed_owned_session(
        adapter,
        SESSION_ZID_1,
        "projekt-eins",
        "Das Haus ist alt.",
        "Haus",
        "Haus",
        "house",
    )
    _seed_owned_session(
        adapter,
        SESSION_ZID_2,
        "projekt-zwei",
        "Der Baum ist hoch.",
        "Baum",
        "Baum",
        "tree",
    )
    project_id = adapter.db.create_project(title="Bootstrap Audit Project", slug="bootstrap-audit")
    adapter.db.link_session_to_project(project_id, SESSION_ZID_1, order_index=0)
    adapter.db.link_session_to_project(project_id, SESSION_ZID_2, order_index=1)
    return project_id


def _extract_json_island(html_text, script_id):
    pattern = rf'<script\s+id="{re.escape(script_id)}"\s+type="application/json">(.*?)</script>'
    match = re.search(pattern, html_text, flags=re.DOTALL)
    assert match is not None, f"Missing JSON island #{script_id}"
    return json.loads(match.group(1).strip() or "{}")


def _http_request(url, *, method="GET", headers=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", errors="replace")


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
    serialized = json.dumps(storage_dump)
    assert secret not in serialized, "Credential must never be persisted in browser storage"


def test_fresh_project_render_bootstraps_authenticated_workspace(browser, tmp_path, running_controller):
    """F1 reproduction: fresh project navigation must authenticate /api/v1/client-view without injected headers."""
    server_url, server, adapter = running_controller
    project_id = _seed_owned_project(server, adapter, tmp_path)
    expected_key = server.api_key

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        page_errors = []
        client_view_exchanges = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        def record_response(resp):
            if "/api/v1/client-view" in resp.url:
                req_token = resp.request.headers.get("x-api-token", "")
                client_view_exchanges.append({
                    "status": resp.status,
                    "has_expected_token": req_token == expected_key,
                })

        page.on("response", record_response)

        doc_response = page.goto(f"{server_url}/session/render?project_id={project_id}", wait_until="domcontentloaded")
        assert doc_response is not None and doc_response.status == 200
        doc_headers = {k.lower(): v for k, v in doc_response.headers.items()}
        assert doc_headers.get("cache-control") == "no-store"
        assert doc_headers.get("content-security-policy") == "frame-ancestors 'none'"
        assert doc_headers.get("referrer-policy") == "no-referrer"
        assert "access-control-allow-origin" not in doc_headers
        raw_html = doc_response.text()

        assert "<script>window.API_TOKEN" not in raw_html, "Raw script token injection must not be emitted"

        # Wait for workspace initialization to settle (either ready or failed)
        page.wait_for_function(
            "() => ['ready', 'failed'].includes(document.documentElement.getAttribute('data-workspace-phase'))"
        )

        assert len(client_view_exchanges) >= 1, "Expected /api/v1/client-view initialization request"
        assert client_view_exchanges[0]["has_expected_token"] is True, (
            "Project page must bootstrap the configured credential before first /api/v1/client-view"
        )
        assert client_view_exchanges[0]["status"] == 200, (
            f"Expected 200 from /api/v1/client-view, observed {client_view_exchanges[0]['status']}"
        )
        assert page.get_attribute("html", "data-workspace-phase") == "ready"
        assert page_errors == []

        session_island = _extract_json_island(raw_html, "session-data")
        assert (session_island.get("api_token") == expected_key) is True
        _assert_no_credential_in_url_or_storage(page, expected_key)

        rendered_zid = page.evaluate("() => window.getSessionZid()")
        client_id = _client_id_from_page(page)
        assert rendered_zid and client_id
        with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as save_view_info:
            page.evaluate("() => window.saveClientView()")
        assert save_view_info.value.status == 200
        with adapter.db.get_connection(read_only=True) as conn:
            sess_row = conn.execute("SELECT zid FROM sessions WHERE zid=?", (rendered_zid,)).fetchone()
            assert sess_row is not None
            row = conn.execute(
                "SELECT session_zid, client_id, revision FROM client_view_state WHERE session_zid=? AND client_id=?",
                (rendered_zid, client_id),
            ).fetchone()
            assert row is not None and row["revision"] >= 1, (
                "Expected real SQLite client_view_state identity after ready project restore"
            )
    finally:
        context.close()


def test_project_render_retry_causal_control_and_explicit_auth_failure(browser, tmp_path, running_controller):
    """Causal control (Audit F1 & explicit auth failure): missing auth stays failed; Retry with owned token reaches ready."""
    server_url, server, adapter = running_controller
    project_id = _seed_owned_project(server, adapter, tmp_path)
    expected_key = server.api_key

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        exchanges = []

        def strip_first_auth(route):
            headers = {k: v for k, v in route.request.headers.items() if k.lower() != "x-api-token"}
            route.continue_(headers=headers)

        page.route("**/api/v1/client-view", strip_first_auth)
        page.on(
            "response",
            lambda resp: exchanges.append(resp.status) if "/api/v1/client-view" in resp.url else None,
        )

        doc_response = page.goto(f"{server_url}/session/render?project_id={project_id}", wait_until="domcontentloaded")
        assert doc_response is not None and doc_response.status == 200

        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "failed")
        assert exchanges and exchanges[0] == 403
        assert page.evaluate("() => window.WorkspacePresentation.isReady()") is False
        assert page.locator("#kw-workspace-message").inner_text().startswith("Workspace unavailable:")

        rendered_zid = page.evaluate("() => window.getSessionZid()")
        client_id = _client_id_from_page(page)
        with adapter.db.get_connection(read_only=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM client_view_state WHERE session_zid=? AND client_id=?",
                (rendered_zid, client_id),
            ).fetchone()
            assert row is None, "Failed unauthenticated startup must not persist a guessed client_view_state"

        # Restore authorization (supplying owned token if page lacks bootstrap in Phase 1) and click Retry
        page.unroute("**/api/v1/client-view", strip_first_auth)
        page.evaluate("tok => { if (!window.getApiToken()) window.API_TOKEN = tok; }", expected_key)
        page.locator("#kw-workspace-retry").click()

        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
        assert exchanges[-1] == 200
        with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as save_view_info:
            page.evaluate("() => window.saveClientView()")
        assert save_view_info.value.status == 200
        with adapter.db.get_connection(read_only=True) as conn:
            row = conn.execute(
                "SELECT session_zid, client_id, revision FROM client_view_state WHERE session_zid=? AND client_id=?",
                (rendered_zid, client_id),
            ).fetchone()
            assert row is not None and row["revision"] >= 1, (
                "Retry with valid credential must restore real SQLite client_view_state"
            )
    finally:
        context.close()


def test_fresh_draft_language_mismatch_render_bootstraps_and_switches_language(
    browser, tmp_path, running_controller, monkeypatch
):
    """Fresh draft mismatch render must bootstrap credentials so clicking 'Yes' commits language and renders ready."""
    server_url, server, adapter = running_controller
    _seed_owned_project(server, adapter, tmp_path)
    expected_key = server.api_key
    monkeypatch.setattr(controller, "spawn_ahk", lambda *a, **kw: None)

    draft_entry = {
        "text": "Das Haus ist alt.",
        "language": "en",
        "text_mode": "single",
        "theme": "dark",
        "zoom": "100",
        "tsv_path": None,
        "seq_num": None,
        "mismatch_info": {
            "is_mismatch": True,
            "detected_language": "de",
            "expected_language": "en",
            "confidence": 0.99,
            "action": "prompt",
            "text": "Das Haus ist alt.",
            "session_zid": DRAFT_ZID,
        },
    }
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[DRAFT_ZID] = draft_entry

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(10000)
        set_lang_exchanges = []
        render_exchanges = []

        def on_response(resp):
            if "/api/v1/set-language" in resp.url:
                set_lang_exchanges.append({
                    "status": resp.status,
                    "has_expected_token": resp.request.headers.get("x-api-token", "") == expected_key,
                })
            elif "/api/v1/render" in resp.url:
                render_exchanges.append({
                    "status": resp.status,
                    "has_expected_token": resp.request.headers.get("x-api-token", "") == expected_key,
                })

        page.on("response", on_response)

        doc_response = page.goto(f"{server_url}/session/render?session_zid={DRAFT_ZID}", wait_until="domcontentloaded")
        assert doc_response is not None and doc_response.status == 200
        raw_html = doc_response.text()
        assert "<script>window.API_TOKEN" not in raw_html

        yes_btn = page.locator("#kw-btn-lang-yes")
        expect(yes_btn).to_be_visible()
        yes_btn.click()

        page.wait_for_function(
            "() => document.querySelectorAll('#kw-btn-lang-yes').length === 0 || "
            "document.getElementById('kw-btn-lang-yes').disabled === false"
        )
        assert len(set_lang_exchanges) >= 1, "Clicking Yes must call /api/v1/set-language"
        assert set_lang_exchanges[0]["has_expected_token"] is True, (
            "Draft mismatch page must carry bootstrapped credential on /api/v1/set-language"
        )
        assert set_lang_exchanges[0]["status"] == 200, (
            f"Expected 200 from /api/v1/set-language, observed {set_lang_exchanges[0]['status']}"
        )
        assert len(render_exchanges) >= 1 and render_exchanges[0]["status"] == 200
        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")

        from kardenwort_state_runtime import preference_store
        assert preference_store(server.config, server.resolved_paths).get("default_language") == "de"
        _assert_no_credential_in_url_or_storage(page, expected_key)
    finally:
        context.close()
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(DRAFT_ZID, None)


def test_ordinary_session_render_uses_safe_json_island_bootstrap_for_special_token(
    browser, tmp_path, running_controller
):
    """Ordinary session render must serialize special-char credentials via JSON island without raw script injection."""
    server_url, server, adapter = running_controller
    _seed_owned_project(server, adapter, tmp_path)
    server.api_key = SPECIAL_API_KEY
    server.config.set("server", "api_key", SPECIAL_API_KEY)

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        page_errors = []
        client_view_statuses = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on(
            "response",
            lambda resp: client_view_statuses.append(resp.status) if "/api/v1/client-view" in resp.url else None,
        )

        doc_response = page.goto(
            f"{server_url}/session/render?session_zid={SESSION_ZID_1}", wait_until="domcontentloaded"
        )
        assert doc_response is not None and doc_response.status == 200
        raw_html = doc_response.text()

        assert ("<script>window.API_TOKEN" in raw_html) is False, "Ordinary session render must not inject raw script token"
        session_island = _extract_json_island(raw_html, "session-data")
        assert (session_island.get("api_token") == SPECIAL_API_KEY) is True

        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
        assert page.evaluate("() => window.__xss || false") is False
        assert page.locator("img[src='x']").count() == 0
        assert (page.evaluate("() => window.getApiToken()") == SPECIAL_API_KEY) is True
        assert client_view_statuses and client_view_statuses[0] == 200
        assert page_errors == []
        _assert_no_credential_in_url_or_storage(page, SPECIAL_API_KEY)
    finally:
        context.close()


def test_authenticated_api_render_uses_safe_json_island_without_raw_token_script(
    tmp_path, running_controller
):
    """Authenticated native/AHK POST /api/v1/render must emit safe JSON-island bootstrap, never raw script injection."""
    server_url, server, adapter = running_controller
    _seed_owned_project(server, adapter, tmp_path)
    server.api_key = SPECIAL_API_KEY
    server.config.set("server", "api_key", SPECIAL_API_KEY)

    status, _, raw_body = _http_request(
        f"{server_url}/api/v1/render",
        method="POST",
        headers={"X-API-Token": SPECIAL_API_KEY},
        body={"session_zid": SESSION_ZID_1, "language": "de"},
    )
    assert status == 200
    payload = json.loads(raw_body)["data"]
    html = payload["html"]
    decoded_b64 = base64.b64decode(payload["html_b64"]).decode("utf-8")
    assert html == decoded_b64
    assert ("<script>window.API_TOKEN" in html) is False, "API render must not inject raw script token"
    assert ("</script><img" in html) is False, "API render must not break script tag boundaries"
    session_island = _extract_json_island(html, "session-data")
    assert (session_island.get("api_token") == SPECIAL_API_KEY) is True


def test_tag_and_export_consumers_authenticate_via_get_api_token(browser, tmp_path, running_controller):
    """kwToggleTag and kwExportSelected must send X-API-Token via getApiToken() rather than empty closure API_TOKEN."""
    server_url, server, adapter = running_controller
    _seed_owned_project(server, adapter, tmp_path)
    expected_key = server.api_key

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        doc_response = page.goto(
            f"{server_url}/session/render?session_zid={SESSION_ZID_1}", wait_until="domcontentloaded"
        )
        assert doc_response is not None and doc_response.status == 200
        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")

        # 1. Exercise kwExportSelected -> POST /api/v1/export
        with page.expect_response(lambda r: "/api/v1/export" in r.url) as export_info:
            page.evaluate("() => window.kwExportSelected()")
        export_resp = export_info.value
        assert (export_resp.request.headers.get("x-api-token", "") == expected_key) is True, (
            "kwExportSelected must send the bootstrapped credential on X-API-Token"
        )
        assert export_resp.status == 200

        # 2. Exercise kwToggleTag direct /api/v1/tag path
        with page.expect_response(lambda r: "/api/v1/tag" in r.url) as tag_info:
            page.evaluate("""() => {
                window.notifyAHKSelection = undefined;
                const row = document.querySelector('#lemma-table tbody tr[data-row-id]');
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = true;
                cb.setAttribute('data-row-id', row.getAttribute('data-row-id'));
                cb.setAttribute('data-token-order', row.getAttribute('data-token-order') || '0');
                cb.setAttribute('data-sentence-idx', row.getAttribute('data-sentence-idx') || '1');
                window.kwToggleTag(cb, row.getAttribute('data-row-id'));
            }""")
        tag_resp = tag_info.value
        assert (tag_resp.request.headers.get("x-api-token", "") == expected_key) is True, (
            "kwToggleTag must send the bootstrapped credential on X-API-Token"
        )
        assert tag_resp.status == 200
    finally:
        context.close()


def test_offline_static_render_keeps_server_disabled_and_empty_token(tmp_path):
    """Offline/static file rendering must never embed a configured controller key."""
    config, paths, _, _ = desk.load_config()
    secret = "offline-secret-must-never-leak"
    config.set("server", "api_key", secret)
    config.set("storage", "backend", "tsv")
    config.set("rendering", "display_mode", "monolithic")
    config.set("sentences_mode", "enabled", "false")
    paths.update(
        storage_backend="tsv",
        sqlite_db_path=tmp_path / "offline.db",
        results_dir=tmp_path,
        generated_results_dir=tmp_path,
    )
    tsv = tmp_path / f"{SESSION_ZID_1}-offline.de.tsv"
    tsv.write_text(
        "\t".join(HEADERS) + "\n" + "\t".join(["0", "Haus", "Haus", "1", "Das Haus.", "house", "house", "1"]) + "\n",
        encoding="utf-8",
    )

    html = desk.run_render_flow(
        text="Das Haus.",
        language="de",
        zid=SESSION_ZID_1,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        tsv_path=str(tsv),
        spawn_children=False,
    )

    assert (secret in html) is False, "Offline static render must never embed configured API key"
    assert ("<script>window.API_TOKEN" in html) is False
    session_island = _extract_json_island(html, "session-data")
    bootstrap_island = _extract_json_island(html, "kardenwort-bootstrap")
    assert session_island.get("api_token", "") == ""
    assert bool(session_island.get("server_enabled", False)) is False
    assert (bootstrap_island.get("session_data") or {}).get("api_token", "") == ""
    assert bool((bootstrap_island.get("session_data") or {}).get("server_enabled", False)) is False


@pytest.mark.parametrize(
    "case_name,method,path_template,headers,body",
    [
        ("project_no_metadata", "GET", "/session/render?project_id={project_id}", {}, None),
        ("project_other_localhost_port", "GET", "/session/render?project_id={project_id}",
         {"Origin": "http://127.0.0.1:59998", "Sec-Fetch-Site": "cross-site"}, None),
        ("session_no_metadata", "GET", "/session/render?session_zid={session_zid}", {}, None),
        ("session_other_localhost_port", "GET", "/session/render?session_zid={session_zid}",
         {"Origin": "http://127.0.0.1:59998", "Sec-Fetch-Site": "cross-site"}, None),
        ("session_remote_origin", "GET", "/session/render?session_zid={session_zid}",
         {"Origin": "https://evil.example"}, None),
        ("session_opaque_origin_no_token", "GET", "/session/render?session_zid={session_zid}",
         {"Origin": "null"}, None),
        ("draft_no_metadata", "GET", "/session/render?session_zid={draft_zid}", {}, None),
        ("api_render_post_no_token", "POST", "/api/v1/render", {},
         {"session_zid": SESSION_ZID_1, "language": "de"}),
        ("api_render_get_no_token", "GET", "/api/v1/render?session_zid={session_zid}&language=de", {}, None),
    ],
)
def test_unauthenticated_programmatic_and_hostile_origin_renders_never_disclose_key(
    tmp_path, running_controller, case_name, method, path_template, headers, body
):
    """Coordinated admission gate: metadata-free and hostile-origin callers must be rejected without key disclosure."""
    server_url, server, adapter = running_controller
    project_id = _seed_owned_project(server, adapter, tmp_path)
    expected_key = server.api_key

    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[DRAFT_ZID] = {
            "text": "Das Haus ist alt.",
            "language": "en",
            "text_mode": "single",
            "theme": "dark",
            "zoom": "100",
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": "de",
                "expected_language": "en",
                "action": "prompt",
                "text": "Das Haus ist alt.",
                "session_zid": DRAFT_ZID,
            },
        }
    try:
        path = path_template.format(
            project_id=project_id,
            session_zid=SESSION_ZID_1,
            draft_zid=DRAFT_ZID,
        )
        status, _, raw_body = _http_request(
            f"{server_url}{path}",
            method=method,
            headers=headers,
            body=body,
        )
        assert status == 403, f"{case_name}: expected 403 rejection, got {status}"
        assert (expected_key in raw_body) is False, f"{case_name}: rejected response must never disclose credential"
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(DRAFT_ZID, None)


@pytest.mark.parametrize("blank_key", ["", "   "])
def test_blank_configured_key_does_not_become_usable_credential(browser, tmp_path, running_controller, blank_key):
    """Genuine missing configuration must reject /api/v1/render with TOKEN_NOT_CONFIGURED and leave bootstrap empty."""
    server_url, server, adapter = running_controller
    project_id = _seed_owned_project(server, adapter, tmp_path)
    server.api_key = blank_key
    server.config.set("server", "api_key", blank_key)

    status, _, raw_body = _http_request(
        f"{server_url}/api/v1/render",
        method="POST",
        headers={},
        body={"session_zid": SESSION_ZID_1, "language": "de"},
    )
    assert status == 403
    err = json.loads(raw_body)
    assert err.get("error_code") == "TOKEN_NOT_CONFIGURED"

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        doc_response = page.goto(f"{server_url}/session/render?project_id={project_id}", wait_until="domcontentloaded")
        assert doc_response is not None and doc_response.status == 200
        session_island = _extract_json_island(doc_response.text(), "session-data")
        assert session_island.get("api_token", "") == ""
        assert page.evaluate("() => window.getApiToken()") == ""
    finally:
        context.close()

