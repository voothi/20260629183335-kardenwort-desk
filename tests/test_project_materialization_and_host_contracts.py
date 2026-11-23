"""Phase 1 reproduction and contract tests for Task 1.4:
Lock down read-only project preview, authenticated admin materialization,
popup failure cleanup, draft confirmation separation, supported TSV backend,
and older-host authenticated entry points.
"""
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401


ROOT = Path(__file__).resolve().parent.parent
SESSION_ZID_1 = "20261123061401"
SESSION_ZID_2 = "20261123061402"
MATERIALIZE_ZID = "20261123061410"
DRAFT_ZID = "20261123061403"
TSV_ZID = "20261123061404"

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

BROWSER_NAV_HEADERS = {
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_request(url, *, method="GET", headers=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        resp = opener.open(req, timeout=10.0)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, resp_headers, resp.read().decode("utf-8", errors="replace")


def _seed_project(server, adapter, tmp_path):
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "true")
    server.config.set("sentences_mode", "delivery_mode", "container")
    server.config.set("wordfill", "enabled", "false")
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    server.resolved_paths["favorites_output_dir"] = str(fav_dir)

    for zid, slug, sent, word, trans in (
        (SESSION_ZID_1, "mat-s1", "Das Haus ist alt.", "Haus", "дом"),
        (SESSION_ZID_2, "mat-s2", "Der Baum ist hoch.", "Baum", "дерево"),
    ):
        adapter.save_session(
            session_zid=zid,
            slug=slug,
            source_language="de",
            target_language="ru",
            text_mode="single",
            source_raw_text=sent,
            headers=HEADERS,
            data_rows=[["0", word, word, "1", sent, trans, trans, "1"]],
            sentences=[{
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": sent,
                "sentence_destination": trans,
                "text_provenance": "live:test",
            }],
        )
    pid = adapter.db.create_project(title="Materialize Book", slug="materialize-book")
    adapter.db.link_session_to_project(pid, SESSION_ZID_1, order_index=0)
    adapter.db.link_session_to_project(pid, SESSION_ZID_2, order_index=1)
    empty_pid = adapter.db.create_project(title="Empty Book", slug="empty-book")
    return pid, empty_pid


def _session_zids(adapter):
    with adapter.db.get_connection(read_only=True) as conn:
        return [r[0] for r in conn.execute("SELECT zid FROM sessions ORDER BY zid").fetchall()]


def _session_snapshot(adapter, zid):
    with adapter.db.get_connection(read_only=True) as conn:
        sess = dict(conn.execute("SELECT * FROM sessions WHERE zid=?", (zid,)).fetchone())
        sents = [dict(r) for r in conn.execute("SELECT * FROM sentences WHERE session_zid=? ORDER BY sentence_index", (zid,)).fetchall()]
        words = [dict(r) for r in conn.execute("SELECT * FROM words WHERE session_zid=? ORDER BY sentence_index, token_order", (zid,)).fetchall()]
    return {"session": sess, "sentences": sents, "words": words}


def test_1_4_direct_project_preview_is_readonly_and_handles_empty_project(
    browser, tmp_path, running_controller
):
    """Task 1.4: Direct GET /?project_id=N renders read-only preview with mutating controls disabled and zero DB writes."""
    server_url, server, adapter = running_controller
    pid, empty_pid = _seed_project(server, adapter, tmp_path)
    before_zids = _session_zids(adapter)
    s1_before = _session_snapshot(adapter, SESSION_ZID_1)
    s2_before = _session_snapshot(adapter, SESSION_ZID_2)

    for target_pid in (pid, empty_pid):
        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(10000)
            resp = page.goto(f"{server_url}/?project_id={target_pid}", wait_until="domcontentloaded")
            assert resp is not None and resp.status == 200
            # Mutating controls must be disabled in read-only project preview (no authoritative persisted session)
            for btn_id in ("#kw-btn-save", "#kw-btn-retext", "#kw-btn-reword", "#kw-btn-delete"):
                btn = page.locator(btn_id)
                if btn.count() > 0:
                    assert btn.is_disabled(), f"Project preview must disable mutating button {btn_id}"
        finally:
            context.close()

    assert _session_zids(adapter) == before_zids, "Direct project preview GET must never create sessions"
    assert _session_snapshot(adapter, SESSION_ZID_1) == s1_before
    assert _session_snapshot(adapter, SESSION_ZID_2) == s2_before


def test_1_4_authenticated_post_synthesize_preserves_shape_materializes_atomically_and_handles_retry(
    tmp_path, running_controller, monkeypatch
):
    """Task 1.4: POST /api/v1/admin/projects/synthesize preserves non-materializing shape and supports materialize=True."""
    server_url, server, adapter = running_controller
    pid, _ = _seed_project(server, adapter, tmp_path)
    secret = server.api_key
    before_zids = _session_zids(adapter)
    s1_before = _session_snapshot(adapter, SESSION_ZID_1)
    s2_before = _session_snapshot(adapter, SESSION_ZID_2)

    # 1. Existing non-materializing POST preserves response shape and creates zero sessions
    st_nomat, _, body_nomat = _http_request(
        f"{server_url}/api/v1/admin/projects/synthesize",
        method="POST",
        headers={"X-API-Token": secret},
        body={"project_id": pid},
    )
    assert st_nomat == 200
    d_nomat = json.loads(body_nomat)["data"]
    assert d_nomat["ok"] is True
    assert d_nomat["project_id"] == pid
    assert d_nomat["total_sessions"] == 2
    assert _session_zids(adapter) == before_zids

    # 2. Unauthenticated materialization -> 403
    st_unauth, _, _ = _http_request(
        f"{server_url}/api/v1/admin/projects/synthesize",
        method="POST",
        headers=dict(BROWSER_NAV_HEADERS),
        body={"project_id": pid, "materialize": True, "session_zid": MATERIALIZE_ZID},
    )
    assert st_unauth == 403
    assert _session_zids(adapter) == before_zids

    # 3. Authenticated materialization -> atomically persists returned session_zid, source sessions unchanged
    st_mat, _, body_mat = _http_request(
        f"{server_url}/api/v1/admin/projects/synthesize",
        method="POST",
        headers={"X-API-Token": secret},
        body={"project_id": pid, "materialize": True, "session_zid": MATERIALIZE_ZID},
    )
    assert st_mat == 200, f"Expected 200 from materialize POST, got {st_mat}: {body_mat[:200]}"
    d_mat = json.loads(body_mat)["data"]
    assert d_mat["ok"] is True
    assert d_mat.get("materialized") is True
    persisted_zid = d_mat["session_zid"]
    assert persisted_zid == MATERIALIZE_ZID
    assert persisted_zid in _session_zids(adapter), "Materialized session_zid must actually exist in SQLite"
    assert _session_snapshot(adapter, SESSION_ZID_1) == s1_before
    assert _session_snapshot(adapter, SESSION_ZID_2) == s2_before

    # 4. Retry with same session_zid must NOT create hidden duplicate sessions
    zids_after_first = _session_zids(adapter)
    st_retry, _, body_retry = _http_request(
        f"{server_url}/api/v1/admin/projects/synthesize",
        method="POST",
        headers={"X-API-Token": secret},
        body={"project_id": pid, "materialize": True, "session_zid": MATERIALIZE_ZID},
    )
    assert st_retry == 200
    assert json.loads(body_retry)["data"]["session_zid"] == MATERIALIZE_ZID
    assert _session_zids(adapter) == zids_after_first

    # 5. Blank configured key rejects materialization with TOKEN_NOT_CONFIGURED
    server.api_key = ""
    server.config.set("server", "api_key", "")
    st_blank, _, body_blank = _http_request(
        f"{server_url}/api/v1/admin/projects/synthesize",
        method="POST",
        headers={},
        body={"project_id": pid, "materialize": True, "session_zid": "20261123061499"},
    )
    assert st_blank == 403
    assert json.loads(body_blank).get("error_code") == "TOKEN_NOT_CONFIGURED"
    assert "20261123061499" not in _session_zids(adapter)


def test_1_4_admin_open_project_in_desk_materializes_and_cleans_popup_on_failure(browser):
    """Task 1.4: Admin Open Project in Desk opens popup in gesture, POSTs materialize, navigates token-free, closes on error."""
    html_content = (ROOT / "assets" / "admin.html").read_text(encoding="utf-8")
    js_content = (ROOT / "assets" / "admin.js").read_text(encoding="utf-8")
    css_content = (ROOT / "assets" / "admin.css").read_text(encoding="utf-8")
    js_no_auto = js_content.replace("document.addEventListener('DOMContentLoaded',", "window.__initApp = (")
    body_inner = html_content.split('<body class="theme-dark">')[1].split('<script src="/assets/admin.js"></script>')[0]
    full_html = (
        f'<!DOCTYPE html><html><head><style>{css_content}</style></head>'
        f'<body class="theme-dark">{body_inner}<script>{js_no_auto}</script></body></html>'
    )

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_content(full_html, wait_until="domcontentloaded")
        res = page.evaluate("""async () => {
            window.state.token = 'admin-mat-secret';
            const popups = [];
            const calls = [];
            let shouldFail = false;

            window.open = (url, target) => {
                const handle = {
                    initialUrl: url,
                    closed: false,
                    location: { href: url || '' },
                    close() { this.closed = true; },
                };
                popups.push(handle);
                return handle;
            };
            window.fetch = async (url, opts) => {
                calls.push({
                    url: String(url),
                    method: (opts && opts.method) || 'GET',
                    headers: Object.assign({}, (opts && opts.headers) || {}),
                    body: opts && opts.body ? JSON.parse(opts.body) : null,
                });
                if (shouldFail) {
                    return {
                        ok: false, status: 500,
                        headers: new Headers({'content-type': 'application/json'}),
                        text: async () => JSON.stringify({ status: 'error', message: 'Synthesis failed' }),
                        json: async () => ({ status: 'error', message: 'Synthesis failed' }),
                    };
                }
                return {
                    ok: true, status: 200,
                    headers: new Headers({'content-type': 'application/json'}),
                    text: async () => JSON.stringify({ status: 'success', data: { ok: true, materialized: true, session_zid: '20261123061455' } }),
                    json: async () => ({ status: 'success', data: { ok: true, materialized: true, session_zid: '20261123061455' } }),
                };
            };

            // 1. Success path
            await openProjectInDesk(7);
            // 2. Failure path -> popup must close
            shouldFail = true;
            await openProjectInDesk(7);
            // 3. Dict path -> read-only project_id URL, no synthesize POST
            const callsBeforeDict = calls.length;
            openProjectInDict(7);

            return {
                calls,
                callsBeforeDict,
                callsAfterDict: calls.length,
                popupSuccessHref: popups[0] ? popups[0].location.href : '',
                popupSuccessClosed: popups[0] ? popups[0].closed : null,
                popupFailClosed: popups[1] ? popups[1].closed : null,
                popupDictUrl: popups[2] ? popups[2].initialUrl : '',
            };
        }""")
        assert len(res["calls"]) >= 2, f"Expected POST materialize calls, got {res['calls']}"
        first_call = res["calls"][0]
        assert first_call["method"] == "POST"
        assert "/api/v1/admin/projects/synthesize" in first_call["url"]
        assert "token=" not in first_call["url"]
        assert first_call["headers"].get("X-API-Token") == "admin-mat-secret"
        assert (first_call["body"] or {}).get("materialize") is True
        assert "session_zid=20261123061455" in res["popupSuccessHref"]
        assert "token=" not in res["popupSuccessHref"]
        assert res["popupSuccessClosed"] is False
        assert res["popupFailClosed"] is True, "Failed materialization must close pre-opened popup"
        assert res["callsAfterDict"] == res["callsBeforeDict"], "Open in Dict must not trigger materialization"
        assert "project_id=7" in res["popupDictUrl"] and "view=goldendict" in res["popupDictUrl"]
        assert "token=" not in res["popupDictUrl"]
    finally:
        context.close()


def test_1_4_draft_confirmation_requires_authenticated_post(running_controller):
    """Task 1.4: Draft confirmation endpoints reject GET and require configured auth on POST."""
    server_url, server, _ = running_controller
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
                "session_zid": DRAFT_ZID,
            },
        }
    try:
        st_get, _, _ = _http_request(
            f"{server_url}/api/v1/confirm-language?session_zid={DRAFT_ZID}&language=de",
            method="GET",
            headers={"X-API-Token": server.api_key},
        )
        assert st_get == 405

        st_unauth, _, _ = _http_request(
            f"{server_url}/api/v1/confirm-language",
            method="POST",
            headers=dict(BROWSER_NAV_HEADERS),
            body={"session_zid": DRAFT_ZID, "language": "de"},
        )
        assert st_unauth == 403

        with controller._DRAFT_SESSIONS_LOCK:
            assert DRAFT_ZID in controller._DRAFT_SESSIONS
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(DRAFT_ZID, None)


def test_1_4_supported_tsv_backend_passive_get_is_readonly(tmp_path, running_controller):
    """Task 1.4: Supported TSV storage backend renders passively without modifying TSV or creating SQLite/update files."""
    server_url, server, _ = running_controller
    tsv_dir = tmp_path / "tsv_ws" / "results"
    tsv_dir.mkdir(parents=True, exist_ok=True)
    tsv_file = tsv_dir / f"{TSV_ZID}-tsv-readonly.de.tsv"
    tsv_file.write_text(
        "\t".join(HEADERS) + "\n"
        + "\t".join(["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", "дом", "1"]) + "\n",
        encoding="utf-8",
    )
    before_sha = hashlib.sha256(tsv_file.read_bytes()).hexdigest()

    server.config.set("storage", "backend", "tsv")
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "false")
    server.config.set("wordfill", "enabled", "false")
    server.resolved_paths["storage_backend"] = "tsv"
    server.resolved_paths["kardenwort_workspace"] = str(tmp_path / "tsv_ws")
    server.resolved_paths["results_dir"] = str(tsv_dir)
    server.resolved_paths["generated_results_dir"] = str(tsv_dir)
    missing_db = tmp_path / "tsv_should_not_create.db"
    server.resolved_paths["sqlite_db_path"] = str(missing_db)

    status, _, body = _http_request(
        f"{server_url}/session/render?session_zid={TSV_ZID}",
        method="GET",
        headers=dict(BROWSER_NAV_HEADERS),
    )
    assert status == 200, f"TSV passive render failed with {status}: {body[:200]}"
    assert "Haus" in body
    assert hashlib.sha256(tsv_file.read_bytes()).hexdigest() == before_sha
    assert missing_db.exists() is False
    assert list(tsv_dir.glob("*.update.js")) == []


def test_1_4_older_host_authenticated_entry_points_and_no_credentialless_exemption(
    tmp_path, running_controller
):
    """Task 1.4: Older-host/GoldenDict Origin: null works ONLY with valid credential; never credentialless."""
    server_url, server, adapter = running_controller
    _seed_project(server, adapter, tmp_path)
    secret = server.api_key

    # Authenticated Origin: null on /api/v1/lookup and /session/create succeeds with ACAO: null and no-store
    st_lk, hdrs_lk, _ = _http_request(
        f"{server_url}/api/v1/lookup?session_zid={SESSION_ZID_1}&language=de",
        method="GET",
        headers={"Origin": "null", "X-API-Token": secret},
    )
    assert st_lk == 200
    assert hdrs_lk.get("access-control-allow-origin") == "null"
    assert hdrs_lk.get("cache-control") == "no-store"
    assert hdrs_lk.get("referrer-policy") == "no-referrer"

    st_cr, hdrs_cr, _ = _http_request(
        f"{server_url}/session/create",
        method="POST",
        headers={"Origin": "null", "X-API-Token": secret},
        body={"text": "Das Haus ist alt.", "language": "de", "bypass_lang_check": True},
    )
    assert st_cr == 200
    assert hdrs_cr.get("access-control-allow-origin") == "null"
    assert hdrs_cr.get("cache-control") == "no-store"
    assert hdrs_cr.get("referrer-policy") == "no-referrer"

    # Unauthenticated Origin: null MUST be rejected 403 (no credentialless older-host exemption)
    for path, method, body in (
        (f"/api/v1/lookup?session_zid={SESSION_ZID_1}&language=de", "GET", None),
        (f"/lookup?session_zid={SESSION_ZID_1}", "GET", None),
        ("/session/create", "POST", {"text": "Das Haus.", "language": "de", "bypass_lang_check": True}),
    ):
        st_no, _, raw_no = _http_request(
            f"{server_url}{path}", method=method, headers={"Origin": "null"}, body=body
        )
        assert st_no == 403, f"[{path}] unauthenticated Origin: null must reject 403, got {st_no}"
        assert (secret in raw_no) is False
