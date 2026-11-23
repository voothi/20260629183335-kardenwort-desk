"""Phase 1 real HTTP boundary reproductions for content-only TSV imports,
post-auth preference capture, role-aware tag + SSE, framing/type/range/method errors,
corrupt persisted client-view JSON, idempotent import notifications, and coherent
status/retry snapshots.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
import kardenwort_state_runtime as state_runtime
from kardenwort_client_views import ClientViewAdapter
from kardenwort_db import KardenwortDB
from kardenwort_events import EventDispatcher
from kardenwort_sqlite_preferences import PreferencesAdapter
from kardenwort_state_runtime import preference_store
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    translation_service,
)
from tests.thread_support import Worker, sse_events


TOKEN = "test-controller-api-key"
SAMPLE_RAW_TSV = (
    "Quotation\tWordSource\tWordDestination\tSentenceSource\tDeskSelected\n"
    "Vogel\tVogel\tптица\tEin Vogel singt.\t1\n"
)


def _http_request(url, path, *, method="GET", body=None, raw_body=None, content_type="application/json", token=TOKEN, extra_headers=None):
    headers = dict(extra_headers or {})
    if token is not None:
        headers["X-API-Token"] = token
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    elif raw_body is not None:
        headers["Content-Type"] = content_type
        payload = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
    req = Request(url + path, data=payload, headers=headers, method=method)
    try:
        resp = urlopen(req, timeout=15)
    except HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8", errors="replace")
        ctype = resp.headers.get("Content-Type", "")
        parsed = json.loads(raw) if "application/json" in ctype and raw else raw
        return resp.status, parsed, dict(resp.headers.items())


def _raw_socket_exchange(port, raw_bytes: bytes, *, shutdown_write: bool = True):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        try:
            sock.sendall(raw_bytes)
            if shutdown_write:
                sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks = []
        while True:
            try:
                chunk = sock.recv(65536)
            except (ConnectionAbortedError, ConnectionResetError, TimeoutError):
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _parse_first_http_response(raw: bytes):
    head, _, rest = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, rest


def _independent_commit(server, **updates):
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    independent = PreferencesAdapter(db)
    independent.initialize(server.config)
    for key, value in updates.items():
        independent.set(key, value)
    return independent


@pytest.fixture
def http_world(tmp_path):
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend="sqlite") as (url, server):
            server.config.set("settings", "default_language", "de")
            server.config.set("ui", "theme", "dark")
            yield url, server, tmp_path


@pytest.mark.parametrize("route", ["/api/v1/sessions/import-tsv", "/api/v1/admin/sessions/import-tsv"])
@pytest.mark.parametrize("mode", ["raw", "json"])
def test_import_tsv_local_path_sentinel_is_not_probed_or_imported(http_world, route, mode):
    """Posting an existing local TSV file path to either HTTP import alias must never read/import it."""
    url, server, tmp_path = http_world
    sentinel_tsv = tmp_path / "secret-local-sentinel.tsv"
    sentinel_tsv.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSource\tDeskSelected\n"
        "SentinelSecretWord\tSentinelSecretWord\tтайна\tGeheimnis.\t1\n",
        encoding="utf-8",
    )
    zid = f"202611230510{1 if route.startswith('/api/v1/sessions') else 2}{1 if mode == 'raw' else 2}"
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()

    if mode == "raw":
        status, body, _ = _http_request(
            url, f"{route}?session_zid={zid}&language=de",
            method="POST", raw_body=str(sentinel_tsv), content_type="text/tab-separated-values",
        )
    else:
        status, body, _ = _http_request(
            url, route,
            method="POST", body={"tsv_content": str(sentinel_tsv), "session_zid": zid, "language": "de"},
        )

    assert status == 400, f"HTTP import probed/accepted local file path on {route} ({mode}): {status} {body}"
    assert isinstance(body, dict) and body.get("error_code") == "INVALID_PAYLOAD"
    assert db.get_session_bundle(zid) is None


@pytest.mark.parametrize("route,zid", [
    ("/api/v1/sessions/import-tsv", "20261123051021"),
    ("/api/v1/admin/sessions/import-tsv", "20261123051022"),
])
def test_import_tsv_both_aliases_observe_committed_language_preference(http_world, route, zid):
    """Both import aliases must capture current SQLite default_language when language is omitted."""
    url, server, _ = http_world
    _independent_commit(server, default_language="fr")
    assert server.config.get("settings", "default_language") == "de"

    status, body, _ = _http_request(
        url, f"{route}?session_zid={zid}",
        method="POST", raw_body=SAMPLE_RAW_TSV, content_type="text/tab-separated-values",
    )
    assert status == 200, body
    assert body["data"]["source_language"] == "fr"
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    bundle = db.get_session_bundle(zid)
    assert bundle is not None and bundle["session"]["source_language"] == "fr"


def test_rejected_and_public_requests_do_not_open_preference_storage(http_world, monkeypatch):
    """Unauthenticated, disallowed-origin, and public requests must not read SQLite preferences."""
    url, server, _ = http_world
    _independent_commit(server, default_language="fr")

    reads = []
    real_read = state_runtime._read_operation_preferences

    def spy_read(*args, **kwargs):
        reads.append(True)
        return real_read(*args, **kwargs)

    monkeypatch.setattr(state_runtime, "_read_operation_preferences", spy_read)

    # Rejected requests on preference-consuming routes
    assert _http_request(url, "/render", method="POST", body={"session_zid": "20261123051030"}, token="wrong")[0] == 403
    assert _http_request(url, "/api/v1/render", method="POST", body={"session_zid": "20261123051030"}, token="wrong")[0] == 403
    assert _http_request(
        url, "/session/status?zid=20261123051030", method="GET",
        extra_headers={"Origin": "https://evil.example"},
    )[0] == 403
    assert _http_request(
        url, "/api/v1/sessions/import-tsv?session_zid=20261123051031",
        method="POST", raw_body=SAMPLE_RAW_TSV, content_type="text/tab-separated-values", token="wrong",
    )[0] == 403
    assert _http_request(
        url, "/api/v1/admin/sessions/import-tsv?session_zid=20261123051032",
        method="POST", raw_body=SAMPLE_RAW_TSV, content_type="text/tab-separated-values", token="wrong",
    )[0] == 403
    assert _http_request(
        url, "/api/v1/tag", method="POST",
        body={"session_zid": "20261123051030", "language": "de", "row_id": 0, "status": True, "fingerprint": "x"},
        token="wrong",
    )[0] == 403

    # Public routes / preflight
    assert _http_request(url, "/health", method="GET", token=None)[0] == 200
    assert _http_request(url, "/session/save", method="OPTIONS", token=None)[0] == 204

    assert reads == [], f"Rejected or public requests accessed SQLite preferences {len(reads)} time(s)"


def test_tag_custom_selected_role_exact_coordinates_arbiter_cache_and_sse(http_world):
    """POST /api/v1/tag must resolve custom roles['selected'], require exact coordinates in multi-sentence
    sessions, reject non-bool status, save via arbiter, update warm cache, and emit one coherent SSE event.
    """
    url, server, tmp_path = http_world
    custom_headers = [
        "TokenOrder", "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "CustomPicked",
    ]
    mapping_file = tmp_path / "custom-mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        + "\n".join(custom_headers)
        + "\n[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n"
        "SentenceDestination=sentence_destination\n"
        "CustomPicked=selected\n"
        "[desk_editable]\n"
        "editable_columns=WordDestination,CustomPicked\n",
        encoding="utf-8",
    )
    server.config.set("settings", "anki_mapping_file", str(mapping_file))
    server.resolved_paths["anki_mapping_file"] = mapping_file

    zid = "20261123051040"
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    rows = [
        ["0", "Haus", "Haus", "дом", "1", "Das Haus.", "Дом.", "0"],
        ["0", "Haus", "Haus", "дом", "2", "Noch ein Haus.", "Ещё дом.", "0"],
        ["1", "Baum", "Baum", "дерево", "2", "Noch ein Haus.", "Ещё дом.", "0"],
    ]
    adapter.save_session(
        session_zid=zid, slug="custom-tag", source_language="de", target_language="ru",
        text_mode="multi", source_raw_text="Das Haus.\nNoch ein Haus.",
        headers=list(custom_headers), data_rows=rows,
    )
    restored = adapter.restore_session(zid)
    fp = desk.compute_content_fingerprint(restored["data_rows"])
    with server.arbiter._lock:
        server.arbiter.sessions[zid] = {
            "session_zid": zid, "language": "de", "headers": list(restored["headers"]),
            "data_rows": [list(r) for r in restored["data_rows"]], "fingerprint": fp,
            "lock": threading.Lock(),
        }

    # 1. Non-boolean status rejected with 400 INVALID_PAYLOAD.
    for bad_status in ("false", 1, "1"):
        st_bad, body_bad, _ = _http_request(
            url, "/api/v1/tag", method="POST",
            body={"session_zid": zid, "language": "de", "row_id": 1, "sentence_idx": 2, "token_order": 0, "status": bad_status, "fingerprint": fp},
        )
        assert st_bad == 400 and body_bad["error_code"] == "INVALID_PAYLOAD", (bad_status, st_bad, body_bad)

    # 2. Ambiguous multi-sentence tag missing sentence_idx rejected (no implicit sentence 1 targeting).
    st_amb, body_amb, _ = _http_request(
        url, "/api/v1/tag", method="POST",
        body={"session_zid": zid, "language": "de", "row_id": 1, "token_order": 0, "status": True, "fingerprint": fp},
    )
    assert st_amb == 400 and body_amb["error_code"] == "INVALID_PAYLOAD", (st_amb, body_amb)

    # 3. Exact-coordinate custom-role tag persists, updates warm cache, and emits one SSE event.
    with sse_events(url, zid) as stream:
        st_ok, body_ok, _ = _http_request(
            url, "/api/v1/tag", method="POST",
            body={"session_zid": zid, "language": "de", "row_id": 1, "sentence_idx": 2, "token_order": 0, "status": True, "fingerprint": fp},
        )
        assert st_ok == 200, body_ok
        new_fp = body_ok["data"]["fingerprint"]

        stream.wait(lambda evs: any(e.get("stage") == "saved" for e in evs), timeout=2)
        saved_events = [e for e in stream.events if e.get("stage") == "saved"]
        assert len(saved_events) == 1
        assert saved_events[0]["fingerprint"] == new_fp
        assert saved_events[0]["shared_selection"]["haus"] is True

    words = {(w["sentence_index"], w["token_order"]): w for w in adapter.db.get_words_by_session(zid)}
    assert words[(1, 0)]["selected"] == 0
    assert words[(2, 0)]["selected"] == 1
    assert words[(2, 1)]["selected"] == 0
    with server.arbiter._lock:
        assert server.arbiter.sessions[zid]["fingerprint"] == new_fp


def test_http_framing_duplicate_signed_te_and_connection_close(http_world):
    """Unambiguous bounded framing across JSON and TSV routes must reject at the framing layer
    before any domain effect, and close desynchronized connections.
    """
    _, server, _ = http_world
    port = server.server_port
    store = preference_store(server.config, server.resolved_paths)
    assert store.get("filter_selected_only") is False

    valid_json = json.dumps({"filter_selected_only": True}).encode("utf-8")
    valid_len = len(valid_json)

    def send_raw(path, extra_headers, body=b"", smuggled=b"", content_type="application/json"):
        lines = [
            f"POST {path} HTTP/1.1",
            f"Host: 127.0.0.1:{port}",
            f"Content-Type: {content_type}",
            f"X-API-Token: {TOKEN}",
            "Connection: close",
            *extra_headers,
        ]
        wire = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body + smuggled
        return _raw_socket_exchange(port, wire, shutdown_write=True)

    # 1. Duplicate Content-Length on JSON route with valid mutation payload -> 400, zero mutation.
    raw_dup = send_raw(
        "/api/v1/config",
        [f"Content-Length: {valid_len}", f"Content-Length: {valid_len}"],
        valid_json,
    )
    st_dup, _, _ = _parse_first_http_response(raw_dup)
    assert st_dup == 400, f"Duplicate Content-Length accepted on JSON route: {st_dup}"
    assert store.get("filter_selected_only") is False

    # 2. Signed (+N), negative (-5), and non-ASCII digit Content-Length on JSON route -> 400, zero mutation.
    for bad_cl in (f"+{valid_len}", "-5", "\u0661\u0660"):
        raw_bad = send_raw("/api/v1/config", [f"Content-Length: {bad_cl}"], valid_json)
        st_bad, _, _ = _parse_first_http_response(raw_bad)
        assert st_bad == 400, f"Accepted invalid Content-Length {bad_cl!r} with status {st_bad}"
        assert store.get("filter_selected_only") is False

    # 3. Transfer-Encoding + Content-Length (TE+CL) on JSON and TSV routes -> 400, zero mutation/import.
    raw_te_cl_json = send_raw(
        "/api/v1/config",
        ["Transfer-Encoding: chunked", f"Content-Length: {valid_len}"],
        valid_json,
    )
    st_te_cl_json, _, _ = _parse_first_http_response(raw_te_cl_json)
    assert st_te_cl_json == 400, f"Accepted TE+CL on JSON route: {st_te_cl_json}"
    assert store.get("filter_selected_only") is False

    tsv_bytes = SAMPLE_RAW_TSV.encode("utf-8")
    raw_te_cl_tsv = send_raw(
        "/api/v1/sessions/import-tsv?session_zid=20261123051050&language=de",
        ["Transfer-Encoding: chunked", f"Content-Length: {len(tsv_bytes)}"],
        tsv_bytes,
        content_type="text/tab-separated-values",
    )
    st_te_cl_tsv, _, _ = _parse_first_http_response(raw_te_cl_tsv)
    assert st_te_cl_tsv == 400, f"Accepted TE+CL on TSV import route: {st_te_cl_tsv}"
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    assert db.get_session_bundle("20261123051050") is None


def test_non_object_json_rejected_with_400_invalid_payload(http_world):
    """Non-object JSON bodies ([], null, string, number) must return 400 INVALID_PAYLOAD, not 500."""
    url, _, _ = http_world
    for raw_json in (b"[]", b"null", b'"hello"', b"123"):
        st, body, _ = _http_request(url, "/session/save", method="POST", raw_body=raw_json, content_type="application/json")
        assert st == 400 and body["error_code"] == "INVALID_PAYLOAD", (raw_json, st, body)


def test_duplicate_origin_header_rejected_with_403(http_world):
    """Duplicate Origin headers (even when the first is loopback) must be rejected with 403."""
    _, server, _ = http_world
    port = server.server_port
    dup_origin_wire = (
        f"GET /health HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Origin: http://127.0.0.1:{port}\r\n"
        f"Origin: https://evil.example\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("ascii")
    st_orig, _, _ = _parse_first_http_response(_raw_socket_exchange(port, dup_origin_wire))
    assert st_orig == 403, f"Duplicate Origin header was not rejected: {st_orig}"


def test_bool_non_integer_and_out_of_range_fields_rejected_with_400(http_world):
    """Boolean, non-integer, and negative field values must return 400 INVALID_PAYLOAD without mutating state."""
    url, server, _ = http_world
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    pid = db.create_project("Bool Target Project")
    assert pid == 1

    for payload in ({"project_id": True}, {"project_id": "abc"}, {"project_id": -1}):
        st_p, body_p, _ = _http_request(url, "/api/v1/admin/projects/delete", method="POST", body=payload)
        assert st_p == 400 and body_p["error_code"] == "INVALID_PAYLOAD", (payload, st_p, body_p)
    assert db.get_project(pid) is not None

    for qs_bad in ("limit=-5", "offset=-1", "limit=abc", "project_id=abc"):
        st_q, body_q, _ = _http_request(url, f"/api/v1/admin/sessions?{qs_bad}", method="GET")
        assert st_q == 400 and body_q["error_code"] == "INVALID_PAYLOAD", (qs_bad, st_q, body_q)

    for purge_bad in ({"older_than_days": True}, {"older_than_days": -1}):
        st_pg, body_pg, _ = _http_request(url, "/api/v1/admin/trash/purge", method="POST", body=purge_bad)
        assert st_pg == 400 and body_pg["error_code"] == "INVALID_PAYLOAD", (purge_bad, st_pg, body_pg)


def test_recognized_unsupported_methods_return_405_with_allow_header(http_world):
    """Recognized routes called with unsupported HTTP methods must return 405 METHOD_NOT_ALLOWED and Allow."""
    url, _, _ = http_world
    for method, path in (
        ("GET", "/session/save"),
        ("POST", "/health"),
        ("PUT", "/session/save"),
        ("DELETE", "/api/v1/config"),
        ("GET", "/api/v1/client-view"),
    ):
        st_m, body_m, hdrs_m = _http_request(url, path, method=method)
        assert st_m == 405, (method, path, st_m, body_m)
        assert isinstance(body_m, dict) and body_m.get("error_code") == "METHOD_NOT_ALLOWED"
        allow_val = next((v for k, v in hdrs_m.items() if k.lower() == "allow"), None)
        assert allow_val, f"Missing Allow header on 405 for {method} {path}: {hdrs_m}"


def test_corrupt_persisted_client_view_json_returns_sanitized_500_configuration_error(http_world):
    """Corrupt persisted client_view_state.active_tokens must return sanitized 500 CONFIGURATION_ERROR."""
    url, server, _ = http_world
    zid = "20261123051060"
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    adapter.save_session(
        session_zid=zid, source_language="de", source_raw_text="Haus.",
        headers=["WordSource", "TokenOrder", "SentenceSourceIndex"], data_rows=[["Haus", "0", "1"]],
    )
    store = preference_store(server.config, server.resolved_paths)
    client_id = "c" * 32
    ClientViewAdapter(store.db).set(zid, client_id, 1, [{"sentence_idx": 1, "token_order": 0}])

    sentinel_corrupt = "{broken_secret_marker"
    with store.db.get_connection() as conn:
        conn.execute(
            "UPDATE client_view_state SET active_tokens=? WHERE session_zid=? AND client_id=?",
            (sentinel_corrupt, zid, client_id),
        )

    status, body, _ = _http_request(
        url, "/api/v1/client-view", method="POST",
        body={"session_zid": zid, "client_id": client_id},
    )
    assert status == 500, body
    assert body["error_code"] == "CONFIGURATION_ERROR"
    assert sentinel_corrupt not in json.dumps(body)

    # Row must not be silently reset.
    with store.db.get_connection(read_only=True) as conn:
        raw_stored = conn.execute(
            "SELECT active_tokens FROM client_view_state WHERE session_zid=? AND client_id=?",
            (zid, client_id),
        ).fetchone()[0]
    assert raw_stored == sentinel_corrupt


def test_idempotent_browser_state_import_does_not_broadcast_preferences_updated(http_world):
    """Re-posting an already imported browser-state client_id (imported=False) must not emit SSE."""
    url, server, _ = http_world
    zid = "20261123051070"
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    adapter.save_session(
        session_zid=zid, source_language="de", source_raw_text="Haus.",
        headers=["WordSource", "TokenOrder", "SentenceSourceIndex"], data_rows=[["Haus", "0", "1"]],
    )
    st_cfg, cfg_body, _ = _http_request(url, "/api/v1/config", method="GET")
    assert st_cfg == 200
    revs = cfg_body["data"]["preferences"]["revisions"]

    q = EventDispatcher.get_instance().register(zid)
    try:
        payload = {
            "session_zid": zid,
            "client_id": "d" * 32,
            "preferences": {"filter_selected_only": True},
            "revisions": revs,
        }
        st1, b1, _ = _http_request(url, "/api/v1/browser-state/import", method="POST", body=payload)
        assert st1 == 200 and b1["data"]["imported"] is True
        first_event = q.get(timeout=2)
        assert first_event["type"] == "preferences_updated"

        # Second identical import -> imported=False -> MUST NOT emit another preferences_updated event.
        st2, b2, _ = _http_request(url, "/api/v1/browser-state/import", method="POST", body=payload)
        assert st2 == 200 and b2["data"]["imported"] is False
        assert q.empty(), f"Idempotent import emitted unwanted event: {q.get_nowait()}"
    finally:
        EventDispatcher.get_instance().unregister(zid, q)


def test_cold_sqlite_status_uses_single_snapshot_across_concurrent_writer(http_world, monkeypatch):
    """Cold SQLite GET /session/status must derive all fields from one read-only transaction snapshot."""
    url, server, _ = http_world
    zid = "20261123051080"
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    headers = [
        "TokenOrder", "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "DeskSelected",
    ]
    adapter.save_session(
        session_zid=zid, slug="snap", source_language="de", target_language="ru",
        text_mode="single", source_raw_text="Das Haus.",
        headers=headers,
        data_rows=[["0", "Haus", "Haus", "v1_word", "1", "Das Haus.", "v1_sent", "0"]],
        row_provenances={0: "live:v1"},
        sentences=[{"sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "v1_sent", "text_provenance": "live:v1"}],
    )
    server.arbiter.sessions.pop(zid, None)

    entered_second_read = threading.Event()
    writer_committed = threading.Event()
    real_get_sents = KardenwortDB.get_sentences_by_session
    triggered = {"done": False}

    def intercepted_get_sents(self, session_zid, *args, **kwargs):
        if session_zid == zid and not triggered["done"]:
            triggered["done"] = True
            entered_second_read.set()
            writer_committed.wait(5)
        return real_get_sents(self, session_zid, *args, **kwargs)

    monkeypatch.setattr(KardenwortDB, "get_sentences_by_session", intercepted_get_sents)

    def external_atomic_v2_commit():
        if not entered_second_read.wait(2):
            return
        with adapter.db.transaction() as conn:
            adapter.db.batch_update_words(
                zid,
                [{"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "v2_word", "word_provenance": "live:v2"}}],
                connection=conn,
            )
            adapter.persist_retext(zid, {1: "v2_sent"}, {}, provenance="live:v2", connection=conn)
        writer_committed.set()

    writer = Worker(external_atomic_v2_commit)
    try:
        st, body, _ = _http_request(url, f"/session/status?zid={zid}", method="GET")
    finally:
        writer_committed.set()
        writer.result(timeout=5)

    assert st == 200, body
    data = body["data"]
    row_trans = next(iter(data["rows"].values()))["trans"]
    sent_dest = data["sentences"][0]["sentence_destination"]
    text_prov = data["text_provenance"]

    # Either entire snapshot is V1 or entire snapshot is V2 — never torn V1 rows + V2 sentences/provenance!
    coherent_v1 = (row_trans == "v1_word" and sent_dest == "v1_sent" and text_prov == "live:v1")
    coherent_v2 = (row_trans == "v2_word" and sent_dest == "v2_sent" and text_prov == "live:v2")
    assert coherent_v1 or coherent_v2, (
        f"Torn SQLite status snapshot: row_trans={row_trans!r}, sent_dest={sent_dest!r}, text_prov={text_prov!r}"
    )


def test_warm_retry_refreshes_cache_translated_text_sentences_and_provenance_coherently(http_world):
    """Warm POST /session/retry translating missing sentence and word must update warm arbiter cache coherently."""
    url, server, tmp_path = http_world
    zid = "20261123051090"
    headers = [
        "TokenOrder", "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "DeskSelected",
    ]
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    rows = [["0", "Haus", "Haus", "", "1", "Das Haus.", "", "0"]]
    adapter.save_session(
        session_zid=zid, slug="warm-retry", source_language="de", target_language="ru",
        text_mode="single", source_raw_text="Das Haus.",
        headers=list(headers), data_rows=rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "", "text_provenance": None}],
    )
    restored = adapter.restore_session(zid)
    with server.arbiter._lock:
        server.arbiter.sessions[zid] = {
            "session_zid": zid, "language": "de", "target_lang": "ru", "text_mode": "single",
            "text": "Das Haus.", "headers": list(restored["headers"]),
            "data_rows": [list(r) for r in restored["data_rows"]],
            "fingerprint": desk.compute_content_fingerprint(restored["data_rows"]),
            "row_provenances": {}, "text_provenance": None, "textProvenance": None,
            "translatedText": "", "sentences": [{"sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": ""}],
            "lock": threading.Lock(),
        }

    st_retry, body_retry, _ = _http_request(url, "/session/retry", method="POST", body={"session_zid": zid})
    assert st_retry == 200, body_retry

    st_status, body_status, _ = _http_request(url, f"/session/status?zid={zid}", method="GET")
    assert st_status == 200, body_status
    sdata = body_status["data"]
    assert "google: Das Haus." in sdata["translatedText"], f"Warm cache retained stale translatedText: {sdata['translatedText']!r}"
    assert sdata["text_provenance"] == "live:google", f"Warm cache retained stale text_provenance: {sdata['text_provenance']!r}"
    assert sdata["sentences"][0]["sentence_destination"] == "google: Das Haus."
