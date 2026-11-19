"""Phase 1 acceptance and control tests for unconfigured mutation auth rejection (F6).

Verifies that absent, empty, and whitespace-only runtime API keys fail closed
with HTTP 403 TOKEN_NOT_CONFIGURED before SQLite, file, event, draft, or process
effects across protected mutations and effectful GET render commands, while
preserving read-only GET routes, Origin: null health readiness, and configured-key
credential carriers.
"""
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import kardenwort_controller as controller
from kardenwort_events import EventDispatcher
from kardenwort_state_runtime import preference_store
from tests.test_controller_integration import running_controller  # noqa: F401


SESSION_ZID = "20261118201433"
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


def _seed_session(server, adapter, tmp_path):
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    server.resolved_paths["favorites_output_dir"] = str(fav_dir)
    adapter.save_session(
        session_zid=SESSION_ZID,
        slug="f6-auth-session",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus ist alt.",
        headers=HEADERS,
        data_rows=[["0", "Haus", "Haus", "1", "Das Haus ist alt.", "house", "house", "0"]],
        sentences=[{
            "session_zid": SESSION_ZID,
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


def _http_json(url, *, method="GET", headers=None, body=None):
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


def _config_file_digest(server):
    cfg_path = Path(server.resolved_paths.get("base_dir", ".")) / "config.ini"
    if not cfg_path.is_file():
        return None
    return hashlib.sha256(cfg_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
@pytest.mark.parametrize("supplied_headers,supplied_body_token", [
    ({}, None),
    ({"X-API-Token": ""}, None),
    ({"X-API-Token": "   "}, None),
    ({}, ""),
    ({}, "   "),
])
def test_unconfigured_key_rejects_config_mutation_without_effects(
    tmp_path, running_controller, key_mode, supplied_headers, supplied_body_token
):
    """Audit F6: absent/empty/whitespace runtime key must reject /api/v1/config with 403 TOKEN_NOT_CONFIGURED."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter, tmp_path)
    _set_unconfigured_key(server, key_mode)

    store = preference_store(server.config, server.resolved_paths)
    before_snapshot = store.snapshot()
    before_cfg_hash = _config_file_digest(server)

    dispatcher = EventDispatcher.get_instance()
    channel = dispatcher.register(SESSION_ZID)
    try:
        payload = {"key": "filter_selected_only", "value": "true"}
        if supplied_body_token is not None:
            payload["token"] = supplied_body_token
        status, resp = _http_json(
            f"{server_url}/api/v1/config",
            method="POST",
            headers=supplied_headers,
            body=payload,
        )
        assert status == 403, f"Expected 403 for key_mode={key_mode}, got {status}: {resp}"
        assert resp.get("error_code") == "TOKEN_NOT_CONFIGURED"

        assert store.snapshot() == before_snapshot, "SQLite preferences/revisions must remain unchanged"
        assert _config_file_digest(server) == before_cfg_hash, "Static config.ini must remain unchanged"
        assert channel.empty(), "Rejected config mutation must not emit preference update events"
        assert str(getattr(server, "api_key", "") or "").strip() == "", "Controller must not generate an ephemeral key"
    finally:
        dispatcher.unregister(SESSION_ZID, channel)


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
@pytest.mark.parametrize("shutdown_path", ["/api/v1/shutdown", "/admin/shutdown"])
def test_unconfigured_key_rejects_shutdown_without_process_effect(
    tmp_path, running_controller, monkeypatch, key_mode, shutdown_path
):
    """Shutdown routes must not bypass empty-key rejection via 'if api_key:' guard."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter, tmp_path)
    _set_unconfigured_key(server, key_mode)

    shutdown_called = []
    monkeypatch.setattr(server, "request_shutdown", lambda: shutdown_called.append(True))

    status, resp = _http_json(f"{server_url}{shutdown_path}", method="POST", body={})
    assert status == 403, f"{shutdown_path} ({key_mode}): expected 403, got {status}"
    assert resp.get("error_code") == "TOKEN_NOT_CONFIGURED"
    assert shutdown_called == [], "Rejected shutdown must not trigger request_shutdown()"


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
def test_unconfigured_key_rejects_protected_mutations_without_state_or_file_effects(
    tmp_path, running_controller, monkeypatch, key_mode
):
    """Protected state, session, tag, export, and admin mutations must all return 403 TOKEN_NOT_CONFIGURED."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter, tmp_path)
    _set_unconfigured_key(server, key_mode)

    ahk_calls = []
    monkeypatch.setattr(controller, "spawn_ahk", lambda *a, **kw: ahk_calls.append(a))

    store = preference_store(server.config, server.resolved_paths)
    before_lang = store.get("default_language")
    before_rows = adapter.restore_session(SESSION_ZID)["data_rows"]
    fav_dir = Path(server.resolved_paths["favorites_output_dir"])
    before_favs = list(fav_dir.rglob("*"))

    cases = [
        ("/api/v1/client-view", {"session_zid": SESSION_ZID, "client_id": "a" * 32, "active_card": 1, "active_tokens": []}),
        ("/api/v1/set-language", {"language": "de"}),
        ("/session/save", {
            "session_zid": SESSION_ZID,
            "language": "de",
            "deltas": [{"row_id": 0, "token_order": 0, "sentence_idx": 1, "column": "DeskSelected", "value": "1"}],
        }),
        ("/api/v1/tag", {
            "session_zid": SESSION_ZID,
            "language": "de",
            "row_id": 0,
            "token_order": 0,
            "sentence_idx": 1,
            "status": True,
            "fingerprint": "",
        }),
        ("/api/v1/export", {
            "session_zid": SESSION_ZID,
            "language": "de",
            "selected_row_ids": [0],
            "fingerprint": "",
        }),
        ("/api/v1/admin/projects", {"title": "Unauthorized Project"}),
    ]

    for path, body in cases:
        status, resp = _http_json(f"{server_url}{path}", method="POST", body=body)
        assert status == 403, f"{path} ({key_mode}): expected 403, got {status}: {resp}"
        assert resp.get("error_code") == "TOKEN_NOT_CONFIGURED", f"{path} ({key_mode}): got {resp}"

    assert store.get("default_language") == before_lang
    assert ahk_calls == []
    assert adapter.restore_session(SESSION_ZID)["data_rows"] == before_rows
    assert list(fav_dir.rglob("*")) == before_favs
    with adapter.db.get_connection(read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM client_view_state").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
@pytest.mark.parametrize("render_path", [
    "/api/v1/render?text=Ein+neues+Haus.&language=de&bypass_lang_check=true",
    "/render?text=Ein+neues+Haus.&language=de&bypass_lang_check=true",
])
def test_unconfigured_key_rejects_effectful_get_render_even_with_navigation_headers(
    tmp_path, running_controller, key_mode, render_path
):
    """Method-independent guard: GET /api/v1/render and /render must return 403 TOKEN_NOT_CONFIGURED before effects."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter, tmp_path)
    _set_unconfigured_key(server, key_mode)

    with adapter.db.get_connection(read_only=True) as conn:
        before_sessions = {row[0] for row in conn.execute("SELECT zid FROM sessions").fetchall()}
    with controller._DRAFT_SESSIONS_LOCK:
        before_drafts = set(controller._DRAFT_SESSIONS.keys())

    nav_headers = {
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Site": "none",
    }
    status, resp = _http_json(f"{server_url}{render_path}", method="GET", headers=nav_headers)
    assert status == 403
    assert resp.get("error_code") == "TOKEN_NOT_CONFIGURED"

    with adapter.db.get_connection(read_only=True) as conn:
        after_sessions = {row[0] for row in conn.execute("SELECT zid FROM sessions").fetchall()}
    with controller._DRAFT_SESSIONS_LOCK:
        after_drafts = set(controller._DRAFT_SESSIONS.keys())
    assert after_sessions == before_sessions
    assert after_drafts == before_drafts


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
def test_unconfigured_key_preserves_readonly_get_and_opaque_origin_health(
    tmp_path, running_controller, key_mode
):
    """Read-only GET endpoints and Origin: null /api/v1/health must retain existing contracts without enabling mutation."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter, tmp_path)
    _set_unconfigured_key(server, key_mode)

    # 1. Health without Origin and with Origin: null
    for headers in ({}, {"Origin": "null"}):
        status, resp = _http_json(f"{server_url}/api/v1/health", method="GET", headers=headers)
        assert status == 200
        assert resp["data"]["ok"] is True

    # 2. Read-only GET routes
    for path in (
        "/api/v1/config",
        "/api/v1/queue/status",
        f"/session/status?zid={SESSION_ZID}",
        "/api/v1/admin/projects",
    ):
        status, resp = _http_json(f"{server_url}{path}", method="GET")
        assert status == 200, f"Read-only GET {path} ({key_mode}) failed with {status}: {resp}"

    # 3. Readiness does not unlock subsequent mutation
    mut_status, mut_resp = _http_json(
        f"{server_url}/api/v1/config",
        method="POST",
        body={"key": "filter_selected_only", "value": "true"},
    )
    assert mut_status == 403
    assert mut_resp.get("error_code") == "TOKEN_NOT_CONFIGURED"


@pytest.mark.parametrize("carrier", ["x_api_token", "x_api_key", "bearer", "body_token"])
def test_configured_key_accepts_valid_carriers_and_rejects_missing_or_wrong_token(
    tmp_path, running_controller, carrier
):
    """Control: configured key accepts all supported carriers and rejects missing/wrong tokens with UNAUTHORIZED."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter, tmp_path)
    configured_key = server.api_key
    store = preference_store(server.config, server.resolved_paths)
    before = store.snapshot()

    # Missing token -> 403 UNAUTHORIZED
    s_miss, r_miss = _http_json(
        f"{server_url}/api/v1/config",
        method="POST",
        body={"key": "filter_selected_only", "value": "true"},
    )
    assert s_miss == 403 and r_miss.get("error_code") == "UNAUTHORIZED"
    assert store.snapshot() == before

    # Wrong token -> 403 UNAUTHORIZED
    s_wrong, r_wrong = _http_json(
        f"{server_url}/api/v1/config",
        method="POST",
        headers={"X-API-Token": "wrong-key-value"},
        body={"key": "filter_selected_only", "value": "true"},
    )
    assert s_wrong == 403 and r_wrong.get("error_code") == "UNAUTHORIZED"
    assert store.snapshot() == before

    # Valid carrier -> 200 OK and commits value
    headers = {}
    body = {"key": "filter_selected_only", "value": "true"}
    if carrier == "x_api_token":
        headers["X-API-Token"] = configured_key
    elif carrier == "x_api_key":
        headers["X-API-Key"] = configured_key
    elif carrier == "bearer":
        headers["Authorization"] = f"Bearer {configured_key}"
    elif carrier == "body_token":
        body["token"] = configured_key

    s_ok, r_ok = _http_json(f"{server_url}/api/v1/config", method="POST", headers=headers, body=body)
    assert s_ok == 200 and r_ok["data"]["ok"] is True
    assert r_ok["data"]["committed_value"] is True
    assert store.get("filter_selected_only") is True
