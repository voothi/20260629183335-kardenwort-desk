"""Regression contracts for Phase 2 controller, auth, cache, audio runtime, and backup fixes."""
import configparser
import errno
import json
import os
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

import kardenwort_audio_runtime as audio_rt
import kardenwort_controller as controller
import kardenwort_desk as desk
from kardenwort_audio import AudioError
from kardenwort_controller_lifetime import ControllerHTTPServer
from kardenwort_db import KardenwortDB
from kardenwort_state_backup import MIGRATION, backup_preference_migration
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN


def _http_req(url, *, method="GET", headers=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None:
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers.items()), err.read()


def test_enrichment_queue_ttl_enforced_on_translate_and_enrich_hit_paths(tmp_path):
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "translation": {"cache_ttl_seconds": "10", "cache_size": "8"},
        "intellifiller": {"enrichment_cache_ttl_seconds": "10", "enrichment_cache_max": "8"},
    })
    clock = [100.0]
    with patch("kardenwort_controller._monotonic_now", side_effect=lambda: clock[0]):
        eq = controller.EnrichmentQueue(cfg, {"base_dir": tmp_path})
        eq.set_cached_translation("Haus", "de", "ru", "старый")
        eq.set_cached("Haus", "de", {"WordDestination": "старый-ai"})

        # Advance past TTL -> must NOT return stale cached entry
        clock[0] = 200.0
        with patch.object(eq, "_execute_translate_lemma", return_value="новый") as m_trans:
            assert eq.translate_lemma("Haus", "de", "ru") == "новый"
            m_trans.assert_called_once()

        with patch.object(eq, "_execute_enrich_lemma", return_value={"WordDestination": "новый-ai"}) as m_enrich:
            assert eq.enrich_lemma("Haus", "de") == {"WordDestination": "новый-ai"}
            m_enrich.assert_called_once()


def test_web_route_blocks_traversal_into_assets_and_queue_status_requires_token(running_controller):
    url, server, _ = running_controller
    server.api_key = TOKEN

    # /web/../assets/admin.html must be rejected as path traversal
    status, _, _ = _http_req(f"{url}/web/../assets/admin.html")
    assert status in (401, 403)

    # /api/v1/queue/status redacts active_progressive_sessions when unauthenticated
    status_no_tok, _, raw_no_tok = _http_req(f"{url}/api/v1/queue/status")
    assert status_no_tok == 200
    payload_no_tok = json.loads(raw_no_tok.decode("utf-8"))
    data_no_tok = payload_no_tok.get("data", payload_no_tok)
    assert "active_progressive_sessions" not in data_no_tok

    status_ok, _, raw = _http_req(f"{url}/api/v1/queue/status", headers={"X-API-Token": TOKEN})
    assert status_ok == 200
    payload_ok = json.loads(raw.decode("utf-8"))
    data_ok = payload_ok.get("data", payload_ok)
    assert "active_progressive_sessions" in data_ok


def test_tsv_download_sanitizes_unicode_and_quoted_content_disposition(running_controller):
    url, server, adapter = running_controller
    server.api_key = TOKEN
    zid = "20261126190001"
    adapter.save_session(
        session_zid=zid,
        slug='привет"мир',
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Haus.",
        headers=["TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "WordDestination"],
        data_rows=[["0", "Haus", "Haus", "1", "Haus.", "Дом.", "дом"]],
    )
    status, headers, body = _http_req(f"{url}/api/v1/sessions/{zid}/tsv", headers={"X-API-Token": TOKEN})
    assert status == 200
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    assert "attachment; filename=" in cd
    assert "filename*=UTF-8''" in cd
    assert "\r" not in cd and "\n" not in cd
    assert b"Haus" in body


def test_render_fallback_preserves_split_gap_zero_and_fails_fast_on_non_not_found(running_controller):
    url, server, _ = running_controller
    server.api_key = TOKEN
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        return "<html>ok</html>", []

    with patch("kardenwort_controller.run_render_flow", side_effect=fake_render):
        status, _, _ = _http_req(
            f"{url}/api/v1/render",
            method="POST",
            headers={"X-API-Token": TOKEN},
            body={"session_zid": "20261126199999", "text": "Haus.", "language": "de", "split_gap_limit": 0, "bypass_lang_check": True},
        )
    assert status == 200
    assert captured["split_gap_limit"] == 0

    # Non-NOT_FOUND restore error must fail fast rather than silently falling back
    with patch.object(desk.SqliteStorageAdapter, "restore_session", side_effect=RuntimeError("db corrupt")):
        status_err, _, _ = _http_req(
            f"{url}/api/v1/render",
            method="POST",
            headers={"X-API-Token": TOKEN},
            body={"session_zid": "20261126199998", "text": "Haus.", "language": "de", "bypass_lang_check": True},
        )
    assert status_err == 500


def test_server_close_does_not_double_raise_after_shutdown_and_close():
    from http.server import BaseHTTPRequestHandler
    server = ControllerHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    server.errors.append(RuntimeError("boom"))
    with pytest.raises(BaseExceptionGroup, match="Controller HTTP disposal failed"):
        server.server_close()
    # Second call (e.g. from _controller_cleanup after shutdown_and_close) must NOT re-raise
    server.server_close()


def test_audio_runtime_reaps_process_on_job_drain_error_and_rejects_browser_tts():
    proc = MagicMock()
    proc.poll.return_value = None
    job = MagicMock()
    with patch.object(audio_rt, "drain_windows_job", side_effect=AudioError("job failed")):
        with pytest.raises(AudioError, match="job failed"):
            audio_rt.close_process(proc, job)
    proc.kill.assert_called_once()
    proc.wait.assert_called_once_with(timeout=5)

    assert audio_rt.execution_timeout(None) == 30.0
    assert "[REDACTED_PATH]" in audio_rt.redact_diagnostic("error at C:/Users/me/file.mp3")

    cfg = configparser.ConfigParser()
    cfg.read_dict({"audio": {"playback_engine": "browser_tts"}})
    with pytest.raises(AudioError, match="browser_tts"):
        audio_rt.execute_audio({"text": "Haus", "language": "de"}, {}, cfg, MagicMock(), MagicMock(), synthesize=True)


def test_backup_preference_migration_streams_and_falls_back_when_hardlink_unsupported(tmp_path):
    db_path = tmp_path / "state.db"
    db = KardenwortDB(db_path=db_path)
    # Create pre-DDL schema table state
    with db.get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS _migrations (filename TEXT PRIMARY KEY, applied_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (zid TEXT PRIMARY KEY, slug TEXT)")
        conn.execute("INSERT OR REPLACE INTO sessions(zid, slug) VALUES ('z1', 's1')")

    exdev = OSError(errno.EXDEV, "Invalid cross-device link")
    with patch("kardenwort_state_backup.os.link", side_effect=exdev):
        target = backup_preference_migration(db, MIGRATION)
    assert target is not None and target.exists()
    # Second call verifies existing backup via streaming comparison
    target2 = backup_preference_migration(db, MIGRATION)
    assert target2 == target


def test_create_session_stores_role_fields_and_sibling_propagation_skips_deleted_sqlite(running_controller):
    _, server, adapter = running_controller
    arbiter = server.arbiter
    headers = ["TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "WordDestination"]
    rows = [["0", "Haus", "Haus", "1", "Haus.", "Дом.", ""]]
    adapter.save_session(
        session_zid="20261126190010", slug="live-sib", source_language="de", target_language="ru",
        text_mode="single", source_raw_text="Haus.", headers=headers, data_rows=rows,
    )
    adapter.save_session(
        session_zid="20261126190011", slug="deleted-sib", source_language="de", target_language="ru",
        text_mode="single", source_raw_text="Haus.", headers=headers, data_rows=rows,
    )
    adapter.db.soft_delete_session("20261126190011")
    arbiter.sessions["20261126190010"] = {
        "language": "de", "headers": list(headers), "data_rows": [list(r) for r in rows],
        "role_fields": {}, "row_provenances": {},
    }
    arbiter.sessions["20261126190011"] = {
        "language": "de", "headers": list(headers), "data_rows": [list(r) for r in rows],
        "role_fields": {"lemma": "WordSource"}, "row_provenances": {},
    }
    arbiter.propagate_enrichment_to_siblings(
        {"Haus": {"WordDestination": "дом (AI)", "word_provenance": "live:intellifiller"}},
        exclude_session_zid="20261126190099",
        language="de",
    )
    # Live sibling updated even when role_fields lacked 'lemma' (WordSource fallback)
    assert arbiter.sessions["20261126190010"]["data_rows"][0][6] == "дом (AI)"
    # Soft-deleted SQLite sibling evicted instead of mutated/broadcast
    assert "20261126190011" not in arbiter.sessions
