"""Phase 1 admission, caller-matrix, and passive-GET evidence tests.

Covers Tasks 1.3 and 1.4 of `20261119102725-harden-render-auth-and-html-escaping`:
- Task 1.3: Real HTTP negative controls across every credential-bearing route,
  alias, output encoding (HTML, JSON HTML, JSON HTML-base64, redirects, errors)
  for no token/no metadata, wrong token, blank configuration, hostile
  localhost/opaque/remote origins, embedding/subresource requests, and
  inconsistent/duplicate Host/Origin headers.
- Task 1.4: Real fresh-browser navigation positive controls (zero injected
  credentials) for ordinary session, project, draft mismatch, and standalone
  `/verify-language`; authenticated native AHK, GoldenDict (`Origin: null`),
  and file-host positive controls; and passive-admission non-effectful GET
  verification.

Credentials are never printed in assertions; checks record only boolean
presence.
"""
import base64
import http.client
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from tests.test_controller_integration import running_controller  # noqa: F401


SESSION_ZID = "20261119102701"
SESSION_ZID_2 = "20261119102702"
DRAFT_ZID = "20261119102703"
DRAFT_VERIFY_ZID = "20261119102704"
ALIAS_ZID = "20261119102705"

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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _seed_world(server, adapter, tmp_path):
    """Seed owned SQLite sessions, project, and isolated goldendict key."""
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    server.resolved_paths["favorites_output_dir"] = str(fav_dir)
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "true")
    server.config.set("sentences_mode", "delivery_mode", "container")
    server.config.set("wordfill", "enabled", "false")
    gd = dict(server.goldendict) if getattr(server, "goldendict", None) else {}
    gd["server_enabled"] = True
    gd["server_api_key"] = server.api_key
    gd["server_host"] = "127.0.0.1"
    gd["server_port"] = server.server_port
    server.goldendict = gd
    if hasattr(server, "arbiter") and server.arbiter:
        server.arbiter.goldendict = gd

    for zid, slug, sent, word, trans in (
        (SESSION_ZID, "harden-eins", "Das Haus ist alt.", "Haus", "house"),
        (SESSION_ZID_2, "harden-zwei", "Der Baum ist hoch.", "Baum", "tree"),
    ):
        adapter.save_session(
            session_zid=zid,
            slug=slug,
            source_language="de",
            target_language="ru",
            text_mode="single",
            source_raw_text=sent,
            headers=HEADERS,
            data_rows=[["0", word, word, "1", sent, trans, trans, "0"]],
            sentences=[{
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": sent,
                "sentence_destination": trans,
                "sentence_destination2": trans,
                "text_provenance": "live:test",
            }],
        )
    project_id = adapter.db.create_project(title="Harden Auth Project", slug="harden-auth-proj")
    adapter.db.link_session_to_project(project_id, SESSION_ZID, order_index=0)
    adapter.db.link_session_to_project(project_id, SESSION_ZID_2, order_index=1)
    return project_id


def _register_draft(zid, *, text="Das Haus ist alt.", language="en", detected="de"):
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[zid] = {
            "text": text,
            "language": language,
            "text_mode": "single",
            "theme": "dark",
            "zoom": "100",
            "tsv_path": None,
            "seq_num": None,
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": detected,
                "expected_language": language,
                "detected_name": "German",
                "expected_name": "English",
                "confidence": 0.99,
                "action": "prompt",
                "text": text,
                "session_zid": zid,
            },
        }


def _unregister_draft(zid):
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS.pop(zid, None)


def _set_unconfigured_key(server, mode):
    if mode == "absent":
        if hasattr(server, "api_key"):
            delattr(server, "api_key")
        if server.config.has_option("server", "api_key"):
            server.config.remove_option("server", "api_key")
        if getattr(server, "goldendict", None):
            server.goldendict["server_api_key"] = ""
    elif mode == "empty":
        server.api_key = ""
        server.config.set("server", "api_key", "")
        if getattr(server, "goldendict", None):
            server.goldendict["server_api_key"] = ""
    elif mode == "whitespace":
        server.api_key = "   \t\n  "
        server.config.set("server", "api_key", "   \t\n  ")
        if getattr(server, "goldendict", None):
            server.goldendict["server_api_key"] = "   \t\n  "
    else:
        raise ValueError(f"Unknown key mode: {mode}")


def _http_request(url, *, method="GET", headers=None, body=None, follow_redirects=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(_NoRedirectHandler())
    try:
        resp = opener.open(req, timeout=10)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, resp_headers, resp.read().decode("utf-8", errors="replace")


def _http_raw_host_request(port, path, *, method="GET", host_header=None, headers=None, body=None):
    """Send an HTTP/1.1 request with an explicit Host header override."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        req_headers = dict(headers or {})
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        if payload is not None and "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/json"
        conn.putrequest(method, path, skip_host=True)
        if host_header is not None:
            conn.putheader("Host", host_header)
        for k, v in req_headers.items():
            conn.putheader(k, v)
        if payload is not None:
            conn.putheader("Content-Length", str(len(payload)))
        conn.endheaders(payload)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        raw_body = resp.read().decode("utf-8", errors="replace")
        return resp.status, resp_headers, raw_body
    finally:
        conn.close()


def _http_duplicate_host_request(port, path, host_a, host_b, extra_headers=None):
    """Send a raw HTTP/1.1 request carrying two Host headers."""
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host_a}",
        f"Host: {host_b}",
        "Connection: close",
    ]
    for k, v in (extra_headers or {}).items():
        lines.append(f"{k}: {v}")
    raw_req = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(raw_req)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    raw_resp = b"".join(chunks).decode("utf-8", errors="replace")
    header_part, _, body_part = raw_resp.partition("\r\n\r\n")
    status_line = header_part.splitlines()[0] if header_part else ""
    status_code = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
    headers = {}
    for line in header_part.splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status_code, headers, body_part


def _snapshot_state(adapter):
    with adapter.db.get_connection(read_only=True) as conn:
        sessions = {r[0] for r in conn.execute("SELECT zid FROM sessions").fetchall()}
        sentence_count = conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        word_count = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        client_view_count = conn.execute("SELECT COUNT(*) FROM client_view_state").fetchone()[0]
    with controller._DRAFT_SESSIONS_LOCK:
        drafts = set(controller._DRAFT_SESSIONS.keys())
    return {
        "sessions": sessions,
        "sentence_count": sentence_count,
        "word_count": word_count,
        "client_view_count": client_view_count,
        "drafts": drafts,
    }


def _contains_credential(secret: str, headers: dict, body: str) -> bool:
    """Boolean-only credential detector across plain text, headers, and base64."""
    if not secret:
        return False
    if secret in body:
        return True
    if any(secret in str(v) for v in (headers or {}).values()):
        return True
    b64_secret = base64.b64encode(secret.encode("utf-8")).decode("ascii")
    if b64_secret in body:
        return True
    try:
        parsed = json.loads(body)
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if isinstance(data, dict) and isinstance(data.get("html_b64"), str) and data["html_b64"]:
            decoded = base64.b64decode(data["html_b64"]).decode("utf-8", errors="replace")
            if secret in decoded:
                return True
    except Exception:
        pass
    return False


def _extract_json_island(html_text, script_id):
    pattern = rf'<script\s+id="{re.escape(script_id)}"\s+type="application/json">(.*?)</script>'
    match = re.search(pattern, html_text, flags=re.DOTALL)
    assert match is not None, f"Missing JSON island #{script_id}"
    return json.loads(match.group(1).strip() or "{}")


def _assert_no_credential_in_url_or_storage(page, secret):
    assert (secret in page.url) is False, "Credential must never appear in browser URL"
    assert ("token=" in page.url) is False, "Query token parameter must not appear in browser URL"
    storage_dump = page.evaluate("""() => ({
        local: Object.assign({}, localStorage),
        session: Object.assign({}, sessionStorage),
    })""")
    assert (secret in json.dumps(storage_dump)) is False, "Credential must not be persisted in browser storage"


# ===========================================================================
# Task 1.3: Real HTTP Negative Controls
# ===========================================================================


@pytest.mark.parametrize(
    "case_name,method,path_template,body",
    [
        ("api_render_get_session", "GET", "/api/v1/render?session_zid={session_zid}&language=de", None),
        ("api_render_get_text", "GET", "/api/v1/render?text=Ein+neues+Haus.&language=de&bypass_lang_check=true", None),
        ("api_render_post_session", "POST", "/api/v1/render", {"session_zid": SESSION_ZID, "language": "de"}),
        ("api_render_post_text", "POST", "/api/v1/render", {"text": "Ein neues Haus.", "language": "de", "bypass_lang_check": True}),
        ("render_alias_get_text", "GET", "/render?text=Ein+neues+Haus.&language=de&bypass_lang_check=true", None),
        ("render_alias_post_text", "POST", "/render", {"text": "Ein neues Haus.", "language": "de", "bypass_lang_check": True}),
        ("verify_language_draft", "GET", "/verify-language?session_zid={draft_zid}&theme=dark", None),
        ("api_lookup_session", "GET", "/api/v1/lookup?session_zid={session_zid}&language=de", None),
        ("api_lookup_project", "GET", "/api/v1/lookup?project_id={project_id}", None),
        ("api_lookup_text", "GET", "/api/v1/lookup?text=Ein+neues+Haus.&language=de&bypass-lang-check=true", None),
        ("session_render_session", "GET", "/session/render?session_zid={session_zid}", None),
        ("session_render_project", "GET", "/session/render?project_id={project_id}", None),
        ("session_render_draft", "GET", "/session/render?session_zid={draft_zid}", None),
        ("session_render_text", "GET", "/session/render?text=Ein+neues+Haus.&language=de&bypass-lang-check=true", None),
        ("lookup_alias_session", "GET", "/lookup?session_zid={session_zid}", None),
        ("lookup_alias_project", "GET", "/lookup?project_id={project_id}", None),
        ("lookup_alias_text", "GET", "/lookup?text=Ein+neues+Haus.&language=de&bypass-lang-check=true", None),
        ("root_alias_session_zid", "GET", "/?session_zid={session_zid}", None),
        ("root_alias_zid", "GET", "/?zid={session_zid}", None),
        ("root_alias_project_id", "GET", "/?project_id={project_id}", None),
        ("root_alias_project", "GET", "/?project={project_id}", None),
        ("root_alias_text", "GET", "/?text=Ein+neues+Haus.&language=de&bypass-lang-check=true", None),
    ],
)
def test_1_3_negative_no_token_no_metadata_rejects_every_route_without_key_or_effects(
    tmp_path, running_controller, case_name, method, path_template, body
):
    """Task 1.3: Raw programmatic requests without credential or browser metadata must reject 403 with zero effects."""
    server_url, server, adapter = running_controller
    project_id = _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_ZID)
    expected_key = server.api_key
    before = _snapshot_state(adapter)

    try:
        path = path_template.format(
            session_zid=SESSION_ZID,
            project_id=project_id,
            draft_zid=DRAFT_ZID,
        )
        status, resp_headers, raw_body = _http_request(
            f"{server_url}{path}", method=method, headers={}, body=body
        )
        has_key = _contains_credential(expected_key, resp_headers, raw_body)
        assert status == 403, f"[{case_name}] expected 403, got {status}"
        assert has_key is False, f"[{case_name}] credential_present={has_key} (must be False)"
        assert _snapshot_state(adapter) == before, f"[{case_name}] rejected request mutated protected state"
    finally:
        _unregister_draft(DRAFT_ZID)


@pytest.mark.parametrize(
    "carrier_name,headers,query_suffix,body_token",
    [
        ("wrong_x_api_token", {"X-API-Token": "wrong-key"}, "", None),
        ("wrong_x_api_key", {"X-API-Key": "wrong-key"}, "", None),
        ("wrong_bearer", {"Authorization": "Bearer wrong-key"}, "", None),
        ("wrong_query_token", {}, "&token=wrong-key", None),
        ("wrong_query_api_token", {}, "&api_token=wrong-key", None),
        ("wrong_x_api_token_with_nav_headers", {**BROWSER_NAV_HEADERS, "X-API-Token": "wrong-key"}, "", None),
    ],
)
@pytest.mark.parametrize(
    "route_name,method,path_template,body",
    [
        ("api_render_post", "POST", "/api/v1/render", {"text": "Neuer Satz.", "language": "de", "bypass_lang_check": True}),
        ("api_render_get", "GET", "/api/v1/render?text=Neuer+Satz.&language=de&bypass_lang_check=true", None),
        ("verify_language", "GET", "/verify-language?session_zid={draft_zid}&theme=dark", None),
        ("api_lookup_session", "GET", "/api/v1/lookup?session_zid={session_zid}&language=de", None),
        ("session_render", "GET", "/session/render?session_zid={session_zid}", None),
        ("lookup_alias", "GET", "/lookup?session_zid={session_zid}", None),
        ("root_alias", "GET", "/?session_zid={session_zid}", None),
    ],
)
def test_1_3_negative_wrong_token_rejects_without_key_or_effects(
    tmp_path, running_controller, carrier_name, headers, query_suffix, body_token, route_name, method, path_template, body
):
    """Task 1.3: Wrong credential on any carrier rejects 403 without key disclosure or effects."""
    server_url, server, adapter = running_controller
    project_id = _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_ZID)
    expected_key = server.api_key
    before = _snapshot_state(adapter)

    try:
        path = path_template.format(session_zid=SESSION_ZID, project_id=project_id, draft_zid=DRAFT_ZID)
        if query_suffix:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}{query_suffix.lstrip('&')}"
        req_body = dict(body) if isinstance(body, dict) else body
        if body_token is not None and isinstance(req_body, dict):
            req_body["token"] = body_token

        status, resp_headers, raw_body = _http_request(
            f"{server_url}{path}", method=method, headers=headers, body=req_body
        )
        has_key = _contains_credential(expected_key, resp_headers, raw_body)
        assert status == 403, f"[{route_name}/{carrier_name}] expected 403, got {status}"
        assert has_key is False, f"[{route_name}/{carrier_name}] credential_present={has_key}"
        assert _snapshot_state(adapter) == before, f"[{route_name}/{carrier_name}] state changed on rejected request"
    finally:
        _unregister_draft(DRAFT_ZID)


@pytest.mark.parametrize("key_mode", ["absent", "empty", "whitespace"])
@pytest.mark.parametrize(
    "case_name,method,path_template,headers,body,expected_code",
    [
        ("api_render_post_blank", "POST", "/api/v1/render", {}, {"text": "Neuer Satz.", "language": "de", "bypass_lang_check": True}, "TOKEN_NOT_CONFIGURED"),
        ("api_render_get_blank", "GET", "/api/v1/render?text=Neuer+Satz.&language=de&bypass_lang_check=true", BROWSER_NAV_HEADERS, None, "TOKEN_NOT_CONFIGURED"),
        ("render_alias_get_blank", "GET", "/render?text=Neuer+Satz.&language=de&bypass_lang_check=true", BROWSER_NAV_HEADERS, None, "TOKEN_NOT_CONFIGURED"),
        ("api_lookup_text_blank", "GET", "/api/v1/lookup?text=Neuer+Satz.&language=de&bypass-lang-check=true", BROWSER_NAV_HEADERS, None, "TOKEN_NOT_CONFIGURED"),
        ("api_lookup_session_blank", "GET", "/api/v1/lookup?session_zid={session_zid}", {}, None, "TOKEN_NOT_CONFIGURED"),
        ("lookup_text_blank", "GET", "/lookup?text=Neuer+Satz.&language=de&bypass-lang-check=true", BROWSER_NAV_HEADERS, None, "TOKEN_NOT_CONFIGURED"),
        ("session_render_text_blank", "GET", "/session/render?text=Neuer+Satz.&language=de&bypass-lang-check=true", BROWSER_NAV_HEADERS, None, "TOKEN_NOT_CONFIGURED"),
        ("verify_language_no_nav_blank", "GET", "/verify-language?session_zid={draft_zid}", {}, None, "UNAUTHORIZED"),
    ],
)
def test_1_3_negative_blank_configuration_fails_closed_before_effects(
    tmp_path, running_controller, key_mode, case_name, method, path_template, headers, body, expected_code
):
    """Task 1.3: Absent/empty/whitespace configured keys must fail closed before command effects."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_ZID)
    _set_unconfigured_key(server, key_mode)
    before = _snapshot_state(adapter)

    try:
        path = path_template.format(session_zid=SESSION_ZID, draft_zid=DRAFT_ZID)
        status, _, raw_body = _http_request(
            f"{server_url}{path}", method=method, headers=headers, body=body
        )
        assert status == 403, f"[{case_name}/{key_mode}] expected 403, got {status}"
        parsed = json.loads(raw_body)
        assert parsed.get("error_code") == expected_code, (
            f"[{case_name}/{key_mode}] expected {expected_code}, got {parsed.get('error_code')}"
        )
        assert _snapshot_state(adapter) == before, f"[{case_name}/{key_mode}] state changed under blank key"
    finally:
        _unregister_draft(DRAFT_ZID)


@pytest.mark.parametrize(
    "origin_name,origin_value",
    [
        ("other_loopback_ip_port", "http://127.0.0.1:59998"),
        ("other_localhost_port", "http://localhost:59998"),
        ("opaque_null", "null"),
        ("remote_origin", "https://evil.example"),
    ],
)
@pytest.mark.parametrize(
    "route_name,path_template",
    [
        ("api_render", "/api/v1/render?session_zid={session_zid}&language=de"),
        ("verify_language", "/verify-language?session_zid={draft_zid}&theme=dark"),
        ("api_lookup", "/api/v1/lookup?session_zid={session_zid}&language=de"),
        ("session_render", "/session/render?session_zid={session_zid}"),
        ("lookup_alias", "/lookup?session_zid={session_zid}"),
        ("root_alias", "/?session_zid={session_zid}"),
    ],
)
def test_1_3_negative_hostile_origins_rejected_without_key_or_cors_reflection(
    tmp_path, running_controller, origin_name, origin_value, route_name, path_template
):
    """Task 1.3: Hostile localhost/opaque/remote origins must reject 403 with no key and no reflected ACAO."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_ZID)
    expected_key = server.api_key

    try:
        path = path_template.format(session_zid=SESSION_ZID, draft_zid=DRAFT_ZID)
        status, resp_headers, raw_body = _http_request(
            f"{server_url}{path}",
            method="GET",
            headers={"Origin": origin_value},
        )
        has_key = _contains_credential(expected_key, resp_headers, raw_body)
        assert status == 403, f"[{route_name}/{origin_name}] expected 403, got {status}"
        assert has_key is False, f"[{route_name}/{origin_name}] credential_present={has_key}"
        assert resp_headers.get("access-control-allow-origin") != origin_value, (
            f"[{route_name}/{origin_name}] reflected hostile ACAO on rejected response"
        )

        # Preflight OPTIONS on credential-bearing routes must not reflect other localhost ports or remote origins
        if origin_name in ("other_loopback_ip_port", "other_localhost_port", "remote_origin"):
            _, opt_headers, _ = _http_request(
                f"{server_url}{path}",
                method="OPTIONS",
                headers={"Origin": origin_value, "Access-Control-Request-Method": "GET"},
            )
            assert opt_headers.get("access-control-allow-origin") != origin_value, (
                f"[{route_name}/{origin_name}] OPTIONS preflight reflected hostile ACAO"
            )
    finally:
        _unregister_draft(DRAFT_ZID)


@pytest.mark.parametrize(
    "meta_name,headers",
    [
        ("iframe_embed", {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-origin"}),
        ("same_origin_cors_fetch", {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin"}),
        ("subresource_image", {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image", "Sec-Fetch-Site": "same-origin"}),
        ("cross_site_navigation", {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "cross-site"}),
        ("same_site_navigation", {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "same-site"}),
    ],
)
@pytest.mark.parametrize(
    "route_name,path_template",
    [
        ("verify_language", "/verify-language?session_zid={draft_zid}&theme=dark"),
        ("api_lookup", "/api/v1/lookup?session_zid={session_zid}&language=de"),
        ("session_render", "/session/render?session_zid={session_zid}"),
        ("lookup_alias", "/lookup?session_zid={session_zid}"),
        ("root_alias", "/?session_zid={session_zid}"),
    ],
)
def test_1_3_negative_embedding_subresource_and_cross_site_metadata_rejected(
    tmp_path, running_controller, meta_name, headers, route_name, path_template
):
    """Task 1.3: Embedded iframes, subresources, script fetches, and cross/same-site navs reject without key."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_ZID)
    expected_key = server.api_key

    try:
        path = path_template.format(session_zid=SESSION_ZID, draft_zid=DRAFT_ZID)
        status, resp_headers, raw_body = _http_request(
            f"{server_url}{path}", method="GET", headers=headers
        )
        has_key = _contains_credential(expected_key, resp_headers, raw_body)
        assert status == 403, f"[{route_name}/{meta_name}] expected 403, got {status}"
        assert has_key is False, f"[{route_name}/{meta_name}] credential_present={has_key}"
    finally:
        _unregister_draft(DRAFT_ZID)


@pytest.mark.parametrize(
    "route_name,path_template",
    [
        ("session_render", "/session/render?session_zid={session_zid}"),
        ("verify_language", "/verify-language?session_zid={draft_zid}&theme=dark"),
        ("api_lookup", "/api/v1/lookup?session_zid={session_zid}&language=de"),
        ("api_render", "/api/v1/render?session_zid={session_zid}&language=de"),
    ],
)
def test_1_3_negative_inconsistent_host_origin_dns_rebinding_and_duplicate_host(
    tmp_path, running_controller, route_name, path_template
):
    """Task 1.3: Forged DNS-rebinding Host, wrong port, inconsistent Host/Origin, and duplicate Host must reject."""
    _, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_ZID)
    port = server.server_port
    expected_key = server.api_key
    path = path_template.format(session_zid=SESSION_ZID, draft_zid=DRAFT_ZID)

    cases = [
        ("dns_rebinding_host", f"evil.example:{port}", dict(BROWSER_NAV_HEADERS)),
        ("wrong_port_host", "127.0.0.1:59999", dict(BROWSER_NAV_HEADERS)),
        (
            "host_origin_hostname_mismatch",
            f"127.0.0.1:{port}",
            {**BROWSER_NAV_HEADERS, "Sec-Fetch-Site": "same-origin", "Origin": f"http://localhost:{port}"},
        ),
        (
            "site_none_conflicting_origin",
            f"127.0.0.1:{port}",
            {**BROWSER_NAV_HEADERS, "Origin": "http://127.0.0.1:59998"},
        ),
    ]

    try:
        for case_name, host_hdr, hdrs in cases:
            status, resp_headers, raw_body = _http_raw_host_request(
                port, path, method="GET", host_header=host_hdr, headers=hdrs
            )
            has_key = _contains_credential(expected_key, resp_headers, raw_body)
            assert status == 403, f"[{route_name}/{case_name}] expected 403, got {status}"
            assert has_key is False, f"[{route_name}/{case_name}] credential_present={has_key}"

        dup_status, dup_headers, dup_body = _http_duplicate_host_request(
            port, path, f"127.0.0.1:{port}", f"evil.example:{port}", extra_headers=BROWSER_NAV_HEADERS
        )
        has_dup_key = _contains_credential(expected_key, dup_headers, dup_body)
        assert dup_status == 403, f"[{route_name}/duplicate_host] expected 403, got {dup_status}"
        assert has_dup_key is False, f"[{route_name}/duplicate_host] credential_present={has_dup_key}"
    finally:
        _unregister_draft(DRAFT_ZID)


def test_1_3_negative_session_alias_redirect_contains_no_credential(tmp_path, running_controller):
    """Task 1.3: Error and 302 redirect responses must never echo or leak the configured key."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    expected_key = server.api_key
    controller._remember_session_alias(ALIAS_ZID, SESSION_ZID)

    url = f"{server_url}/session/render?session_zid={ALIAS_ZID}&token={urllib.parse.quote(expected_key)}"
    status, resp_headers, raw_body = _http_request(url, method="GET", headers={}, follow_redirects=False)
    assert status == 302, f"Expected 302 alias redirect, got {status}"
    location = resp_headers.get("location", "")
    assert (expected_key in location) is False, "Redirect Location must never contain configured key"
    assert ("token=" in location) is False, "Redirect Location must strip query token parameter"
    assert _contains_credential(expected_key, resp_headers, raw_body) is False


# ===========================================================================
# Task 1.4: Fresh Browser, Native Positive Controls & Passive-GET Guard
# ===========================================================================


def test_1_4_positive_fresh_browser_ordinary_project_and_draft_navigation(
    browser, tmp_path, running_controller, monkeypatch
):
    """Task 1.4: Fresh browser navigation (no injected creds) works across ordinary, project, and draft pages."""
    server_url, server, adapter = running_controller
    project_id = _seed_world(server, adapter, tmp_path)
    expected_key = server.api_key
    monkeypatch.setattr(controller, "spawn_ahk", lambda *a, **kw: None)

    for variant, path in (
        ("ordinary_session_render", f"/session/render?session_zid={SESSION_ZID}"),
        ("ordinary_root_alias", f"/?session_zid={SESSION_ZID}"),
        ("project_session_render", f"/session/render?project_id={project_id}"),
        ("project_root_alias", f"/?project_id={project_id}"),
    ):
        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(10000)
            page_errors = []
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            doc_resp = page.goto(f"{server_url}{path}", wait_until="domcontentloaded")
            assert doc_resp is not None and doc_resp.status == 200, f"[{variant}] status={getattr(doc_resp, 'status', None)}"
            doc_headers = {k.lower(): v for k, v in doc_resp.headers.items()}
            assert doc_headers.get("cache-control") == "no-store", f"[{variant}] missing no-store"
            assert doc_headers.get("content-security-policy") == "frame-ancestors 'none'", f"[{variant}] missing frame-ancestors"
            assert doc_headers.get("referrer-policy") == "no-referrer", f"[{variant}] missing no-referrer"
            assert "access-control-allow-origin" not in doc_headers, f"[{variant}] unexpected ACAO on document"

            expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
            assert page_errors == [], f"[{variant}] page errors: {page_errors}"
            assert (page.evaluate("() => window.getApiToken()") == expected_key) is True
            _assert_no_credential_in_url_or_storage(page, expected_key)
        finally:
            context.close()

    # Draft mismatch page in fresh browser
    _register_draft(DRAFT_ZID)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(12000)
        doc_resp = page.goto(f"{server_url}/session/render?session_zid={DRAFT_ZID}", wait_until="domcontentloaded")
        assert doc_resp is not None and doc_resp.status == 200
        doc_headers = {k.lower(): v for k, v in doc_resp.headers.items()}
        assert doc_headers.get("cache-control") == "no-store"
        assert doc_headers.get("content-security-policy") == "frame-ancestors 'none'"
        assert doc_headers.get("referrer-policy") == "no-referrer"
        expect(page.locator("#kw-btn-lang-yes")).to_be_visible()
        with page.expect_response(lambda r: "/api/v1/set-language" in r.url) as lang_resp:
            page.locator("#kw-btn-lang-yes").click()
        assert lang_resp.value.status == 200
        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
        _assert_no_credential_in_url_or_storage(page, expected_key)
    finally:
        context.close()
        _unregister_draft(DRAFT_ZID)


def test_1_4_positive_fresh_browser_verify_language_and_no_url_secret_on_spawn(
    browser, tmp_path, running_controller, monkeypatch
):
    """Task 1.4: Fresh browser /verify-language (no URL token) enforces security headers and spawns secret-free URLs."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    _register_draft(DRAFT_VERIFY_ZID)
    expected_key = server.api_key
    monkeypatch.setattr(controller, "spawn_ahk", lambda *a, **kw: None)
    opened_urls = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda u: opened_urls.append(u))

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(12000)
        doc_resp = page.goto(
            f"{server_url}/verify-language?session_zid={DRAFT_VERIFY_ZID}&theme=dark",
            wait_until="domcontentloaded",
        )
        assert doc_resp is not None and doc_resp.status == 200
        doc_headers = {k.lower(): v for k, v in doc_resp.headers.items()}
        assert doc_headers.get("cache-control") == "no-store", "/verify-language must set Cache-Control: no-store"
        assert doc_headers.get("content-security-policy") == "frame-ancestors 'none'", (
            "/verify-language must set CSP frame-ancestors 'none'"
        )
        assert doc_headers.get("referrer-policy") == "no-referrer", "/verify-language must set Referrer-Policy: no-referrer"
        assert "access-control-allow-origin" not in doc_headers
        _assert_no_credential_in_url_or_storage(page, expected_key)

        with page.expect_response(lambda r: "/api/v1/confirm-language" in r.url) as confirm_info:
            page.locator("#kw-btn-verify-process").click()
        confirm_resp = confirm_info.value
        assert confirm_resp.status == 200
        confirm_payload = confirm_resp.json()["data"]
        returned_urls = confirm_payload.get("urls", [])
        assert len(returned_urls) >= 1 and len(opened_urls) >= 1
        for u in returned_urls + opened_urls:
            assert (expected_key in u) is False, "Spawned navigation URL must never contain configured key"
            assert ("token=" in u and "api_token=" in u) is False and ("token=" in u) is False, (
                "Spawned navigation URL must omit token/api_token query parameters"
            )
    finally:
        context.close()
        _unregister_draft(DRAFT_VERIFY_ZID)


def test_1_4_positive_authenticated_native_ahk_goldendict_and_file_host_handoffs(
    tmp_path, running_controller
):
    """Task 1.4: Authenticated AHK, GoldenDict (native CLI + Origin: null), file-host, and health controls work."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    expected_key = server.api_key

    # 1. AHK / native POST /api/v1/render with X-API-Token and zero Fetch Metadata
    status, headers, body = _http_request(
        f"{server_url}/api/v1/render",
        method="POST",
        headers={"X-API-Token": expected_key},
        body={"session_zid": SESSION_ZID, "language": "de"},
    )
    assert status == 200
    assert headers.get("cache-control") == "no-store"
    assert headers.get("referrer-policy") == "no-referrer"
    data = json.loads(body)["data"]
    assert data["html"] == base64.b64decode(data["html_b64"]).decode("utf-8")
    assert ("<script>window.API_TOKEN" in data["html"]) is False
    assert (_extract_json_island(data["html"], "session-data").get("api_token") == expected_key) is True

    # 2. GoldenDict native CLI handoff (no HTTP fetch metadata):
    #    render_lookup_html embeds token when server_enabled=True, empty when server_enabled=False,
    #    and Origin: null commands succeed ONLY with valid X-API-Token.
    gd_online = dict(server.goldendict)
    gd_online["server_enabled"] = True
    gd_online["server_api_key"] = expected_key
    online_html = desk.render_lookup_html(
        "Das Haus ist alt.", "de", "ru", server.config, server.resolved_paths,
        SESSION_ZID, gd_online, [], HEADERS,
        [["0", "Haus", "Haus", "1", "Das Haus ist alt.", "house", "house", "0"]],
        "house", session_zid=SESSION_ZID, api_token=expected_key, server_enabled=True,
    )
    assert (_extract_json_island(online_html, "session-data").get("api_token") == expected_key) is True

    gd_offline = dict(gd_online)
    gd_offline["server_enabled"] = False
    offline_html = desk.render_lookup_html(
        "Das Haus ist alt.", "de", "ru", server.config, server.resolved_paths,
        SESSION_ZID, gd_offline, [], HEADERS,
        [["0", "Haus", "Haus", "1", "Das Haus ist alt.", "house", "house", "0"]],
        "house", session_zid=SESSION_ZID, api_token=expected_key, server_enabled=False,
    )
    assert (expected_key in offline_html) is False
    assert _extract_json_island(offline_html, "session-data").get("api_token", "") == ""

    # Origin: null authenticated native GoldenDict command -> 200 OK
    tag_status, tag_headers, _ = _http_request(
        f"{server_url}/api/v1/tag",
        method="POST",
        headers={"Origin": "null", "X-API-Token": expected_key},
        body={
            "session_zid": SESSION_ZID,
            "language": "de",
            "row_id": 0,
            "token_order": 0,
            "sentence_idx": 1,
            "status": True,
            "fingerprint": "",
        },
    )
    assert tag_status == 200
    assert tag_headers.get("access-control-allow-origin") == "null"

    # Origin: null unauthenticated -> 403
    unauth_null_status, _, _ = _http_request(
        f"{server_url}/api/v1/tag",
        method="POST",
        headers={"Origin": "null"},
        body={
            "session_zid": SESSION_ZID,
            "language": "de",
            "row_id": 0,
            "status": True,
            "fingerprint": "",
        },
    )
    assert unauth_null_status == 403

    # 3. Authenticated GET /api/v1/lookup (no Fetch Metadata) must succeed with credential-bearing headers
    lk_status, lk_headers, lk_body = _http_request(
        f"{server_url}/api/v1/lookup?session_zid={SESSION_ZID}&language=de",
        method="GET",
        headers={"X-API-Token": expected_key},
    )
    assert lk_status == 200
    assert lk_headers.get("cache-control") == "no-store", "/api/v1/lookup must set Cache-Control: no-store"
    assert lk_headers.get("referrer-policy") == "no-referrer", "/api/v1/lookup must set Referrer-Policy: no-referrer"
    lk_html = json.loads(lk_body)["data"]["html"]
    assert (_extract_json_island(lk_html, "session-data").get("api_token") == expected_key) is True

    # 4. Configured file-host / internal producer bridge (/internal/stage)
    stage_ok, _, _ = _http_request(
        f"{server_url}/internal/stage",
        method="POST",
        headers={"X-API-Token": expected_key},
        body={"session_zid": SESSION_ZID, "event": {"type": "stage", "stage": "test", "session_zid": SESSION_ZID}},
    )
    assert stage_ok == 200
    stage_denied, _, _ = _http_request(
        f"{server_url}/internal/stage",
        method="POST",
        headers={},
        body={"session_zid": SESSION_ZID, "event": {"type": "stage", "stage": "test", "session_zid": SESSION_ZID}},
    )
    assert stage_denied == 403

    # 5. Documented read-only health readiness (bare & Origin: null) without key disclosure
    for hdrs in ({}, {"Origin": "null"}):
        h_status, h_headers, h_body = _http_request(f"{server_url}/api/v1/health", method="GET", headers=hdrs)
        assert h_status == 200
        assert json.loads(h_body)["data"]["ok"] is True
        assert _contains_credential(expected_key, h_headers, h_body) is False


@pytest.mark.parametrize(
    "case_name,path",
    [
        ("api_v1_render_text_get", "/api/v1/render?text=Ein+neues+Haus.&language=de&bypass_lang_check=true"),
        ("render_alias_text_get", "/render?text=Ein+neues+Haus.&language=de&bypass_lang_check=true"),
        ("lookup_text_get", "/lookup?text=Ein+neues+Haus.&language=de&bypass-lang-check=true"),
        ("session_render_text_get", "/session/render?text=Ein+neues+Haus.&language=de&bypass-lang-check=true"),
        ("root_alias_text_get", "/?text=Ein+neues+Haus.&language=de&bypass-lang-check=true"),
        ("api_v1_lookup_text_get", "/api/v1/lookup?text=Ein+neues+Haus.&language=de&bypass-lang-check=true"),
    ],
)
def test_1_4_passive_browser_admission_never_authorizes_effectful_get(
    tmp_path, running_controller, case_name, path
):
    """Task 1.4: Passive browser navigation headers must NEVER authorize effectful text-to-session GET commands."""
    server_url, server, adapter = running_controller
    _seed_world(server, adapter, tmp_path)
    expected_key = server.api_key
    before = _snapshot_state(adapter)

    dispatcher = EventDispatcher.get_instance()
    channel = dispatcher.register(SESSION_ZID)
    try:
        status, resp_headers, raw_body = _http_request(
            f"{server_url}{path}",
            method="GET",
            headers=BROWSER_NAV_HEADERS,
        )
        has_key = _contains_credential(expected_key, resp_headers, raw_body)
        assert status == 403, f"[{case_name}] passive browser nav must not authorize effectful GET, got {status}"
        assert has_key is False, f"[{case_name}] credential_present={has_key}"
        assert _snapshot_state(adapter) == before, f"[{case_name}] effectful GET mutated SQLite/draft state"
        assert channel.empty(), f"[{case_name}] rejected effectful GET emitted SSE events"
    finally:
        dispatcher.unregister(SESSION_ZID, channel)
