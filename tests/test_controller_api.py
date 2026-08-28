import sys
import os
import json
import time
import socket
import threading
import urllib.request
import urllib.error
import pytest
from pathlib import Path
from http.server import ThreadingHTTPServer

import kardenwort_desk
from kardenwort_controller import (
    ProcessSupervisor,
    SessionArbiter,
    ControllerRequestHandler,
)


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def running_controller():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), ControllerRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "test-controller-api-key"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()
    server.server_port = port

    # In-memory arbiter
    server.arbiter = SessionArbiter(config, resolved_paths)
    server.arbiter.goldendict = goldendict

    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    server_url = f"http://127.0.0.1:{port}"
    yield server_url, server

    server.shutdown()
    server.server_close()


def test_spawn_tabs_endpoint(running_controller, monkeypatch):
    """
    Verify POST /api/v1/spawn-tabs opens URLs via webbrowser.open_new_tab and returns 200 with spawned list.
    """
    server_url, server = running_controller
    opened_urls = []

    import webbrowser
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_urls.append(url))

    req_body = {
        "urls": [
            "/session/render?session_zid=20260827010001&seq_num=1&bypass_lang_check=true",
            "/session/render?session_zid=20260827010002&seq_num=2&bypass_lang_check=true",
            "http://127.0.0.1:18335/custom"
        ]
    }
    req_data = json.dumps(req_body).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/api/v1/spawn-tabs",
        data=req_data,
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") == "success"
        res_data = data.get("data", {})
        assert res_data.get("ok") is True
        assert res_data.get("spawned") == 3
        assert len(res_data.get("urls")) == 3

    assert len(opened_urls) == 3
    assert f"{server_url}/session/render?session_zid=20260827010001&seq_num=1&bypass_lang_check=true" in opened_urls[0]
    assert opened_urls[2] == "http://127.0.0.1:18335/custom"


def test_session_status_fallback_to_persistent_storage(running_controller):
    """
    Verify GET /session/status falls back to persistent storage (SQLite/TSV)
    when not in arbiter memory and reports is_finished=True along with hydrated rows and translatedText.
    """
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)

    sess_zid = kardenwort_desk.generate_unique_zid()

    # 1. Non-existent ZID returns 404
    req_404 = urllib.request.Request(f"{server_url}/session/status?zid=99999999999999")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_404, timeout=5.0)
    assert exc_info.value.code == 404

    # 2. Persist session directly in storage adapter
    storage_adapter.save_session(
        session_zid=sess_zid,
        slug="test-status-slug",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="The quick brown fox",
        sentences=[{"sentence_index": 0, "sentence_source": "The quick brown fox", "sentence_destination": "Быстрая бурая лиса"}],
        headers=["WordSource", "WordDestination", "TokenOrder"],
        data_rows=[["quick", "быстрый", "0"], ["fox", "лиса", "1"]],
    )

    try:
        req_status = urllib.request.Request(f"{server_url}/session/status?zid={sess_zid}")
        with urllib.request.urlopen(req_status, timeout=5.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("status") == "success"
            res_data = data.get("data", {})
            assert res_data.get("ok") is True
            assert res_data.get("is_finished") is True
            assert res_data.get("stage") == "finished"
            assert "rows" in res_data
            assert isinstance(res_data["rows"], dict)
            assert len(res_data["rows"]) == 2
            row0 = res_data["rows"].get("0") or res_data["rows"].get(0)
            assert row0 is not None
            assert row0.get("lemma") == "quick"
            assert row0.get("trans") == "быстрый"
            assert "translatedText" in res_data
            assert "Быстрая бурая лиса" in res_data["translatedText"]
    finally:
        try:
            storage_adapter.delete_session(sess_zid)
        except Exception:
            pass


def test_session_status_tsv_fallback(running_controller):
    """
    Verify GET /session/status falls back to TSV session file when present
    and correctly hydrates rows dictionary and translatedText.
    """
    server_url, server = running_controller
    results_dir = kardenwort_desk.resolve_results_dir(server.resolved_paths, server.config)
    results_dir.mkdir(parents=True, exist_ok=True)

    tsv_zid = kardenwort_desk.generate_unique_zid()
    tsv_file = results_dir / f"{tsv_zid}-test-status.tsv"
    txt_file = results_dir / f"{tsv_zid}-test-status.txt"

    txt_file.write_text("Hello world", encoding="utf-8")
    headers = ["WordSource", "WordDestination", "TokenOrder", "SentenceSourceIndex", "SentenceDestination"]
    data_rows = [
        ["hello", "привет", "0", "1", "Привет мир"],
        ["world", "мир", "1", "1", "Привет мир"],
    ]
    kardenwort_desk.save_tsv_rows_safely(tsv_file, ["# test TSV"], headers, data_rows)

    try:
        req_status = urllib.request.Request(f"{server_url}/session/status?zid={tsv_zid}")
        with urllib.request.urlopen(req_status, timeout=5.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("status") == "success"
            res_data = data.get("data", {})
            assert res_data.get("ok") is True
            assert res_data.get("is_finished") is True
            assert res_data.get("stage") == "finished"
            assert "rows" in res_data
            assert isinstance(res_data["rows"], dict)
            assert len(res_data["rows"]) == 2
            row0 = res_data["rows"].get("0") or res_data["rows"].get(0)
            assert row0 is not None
            assert row0.get("lemma") == "hello"
            assert row0.get("trans") == "привет"
            assert "translatedText" in res_data
            assert "Привет мир" in res_data["translatedText"]
    finally:
        try:
            if tsv_file.exists():
                tsv_file.unlink()
            if txt_file.exists():
                txt_file.unlink()
        except Exception:
            pass


def test_set_language_endpoint(running_controller, monkeypatch, tmp_path):
    """
    Verify POST /api/v1/set-language updates in-memory config, invokes spawn_ahk IPC,
    and returns 200 with updated language status.
    """
    server_url, server = running_controller
    spawn_calls = []

    monkeypatch.setattr(kardenwort_desk, "spawn_ahk", lambda args, base_dir=None: spawn_calls.append((args, base_dir)))
    monkeypatch.setattr("kardenwort_controller.spawn_ahk", lambda args, base_dir=None: spawn_calls.append((args, base_dir)))
    monkeypatch.setattr("kardenwort_controller.persist_default_language", lambda language, base_dir=None: True)

    req_body = {"language": "de"}
    req_data = json.dumps(req_body).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/api/v1/set-language",
        data=req_data,
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") == "success"
        res_data = data.get("data", {})
        assert res_data.get("ok") is True
        assert res_data.get("language") == "de"

    # Verify in-memory config updated
    assert server.config.get("settings", "default_language") == "de"

    # Verify spawn_ahk was called with --set-language
    assert len(spawn_calls) >= 1
    assert spawn_calls[-1][0] == ["--set-language", "de"]


def test_set_language_missing_field(running_controller):
    """
    Verify POST /api/v1/set-language returns 400 when language is missing or empty.
    """
    server_url, server = running_controller

    req_body = {}
    req_data = json.dumps(req_body).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/api/v1/set-language",
        data=req_data,
        headers={"Content-Type": "application/json"}
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5.0)
    assert exc_info.value.code == 400


def test_verify_language_get_endpoint(running_controller):
    """
    Verify GET /verify-language serves standalone verification page populated from draft session.
    """
    from kardenwort_controller import _DRAFT_SESSIONS, _DRAFT_SESSIONS_LOCK
    server_url, server = running_controller

    session_zid = "20260827010099"
    with _DRAFT_SESSIONS_LOCK:
        _DRAFT_SESSIONS[session_zid] = {
            "text": "Das ist ein deutsches Haus.",
            "language": "en",
            "text_mode": "single",
            "theme": "dark",
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": "de",
                "expected_language": "en",
                "detected_name": "German",
                "expected_name": "English",
                "session_zid": session_zid,
            }
        }

    url = f"{server_url}/verify-language?session_zid={session_zid}&theme=dark"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type")
        assert "text/html" in content_type
        body = resp.read().decode("utf-8")
        assert "Language Verification" in body
        assert "The text appears to be German (de), but the active profile is English (en)." in body
        assert "Switch language to German?" in body
        assert 'id="kw-btn-lang-yes"' in body
        assert 'id="kw-btn-lang-no"' in body
        assert 'id="kw-btn-lang-cancel"' in body
        assert 'id="kw-status-msg"' in body


def test_confirm_language_cancel(running_controller):
    """
    Verify POST /api/v1/confirm-language with action=cancel discards draft session and returns 200.
    """
    from kardenwort_controller import _DRAFT_SESSIONS, _DRAFT_SESSIONS_LOCK
    server_url, server = running_controller

    session_zid = "20260827010100"
    with _DRAFT_SESSIONS_LOCK:
        _DRAFT_SESSIONS[session_zid] = {
            "text": "Das ist ein Haus.",
            "language": "en",
            "text_mode": "single",
            "theme": "dark",
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": "de",
                "expected_language": "en",
                "session_zid": session_zid,
            }
        }

    req_body = {"session_zid": session_zid, "action": "cancel"}
    req_data = json.dumps(req_body).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/api/v1/confirm-language",
        data=req_data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") == "success"
        res = data.get("data", {})
        assert res.get("ok") is True
        assert res.get("action") == "cancel"

    with _DRAFT_SESSIONS_LOCK:
        assert session_zid not in _DRAFT_SESSIONS


def test_confirm_language_switch_and_reverse_tab_spawning(running_controller, monkeypatch):
    """
    Verify POST /api/v1/confirm-language with action=switch:
    1. Updates runtime config to detected language
    2. Signals AHK tray via IPC
    3. Spawns tabs with reverse order focus guarantee (Sentence 1 opened last).
    """
    from kardenwort_controller import _DRAFT_SESSIONS, _DRAFT_SESSIONS_LOCK
    server_url, server = running_controller
    server.config.set("sentences_mode", "delivery_mode", "multi_window")

    session_zid = "20260827010101"
    with _DRAFT_SESSIONS_LOCK:
        _DRAFT_SESSIONS[session_zid] = {
            "text": "Satz eins. Satz zwei. Satz drei.",
            "language": "en",
            "text_mode": "multi",
            "theme": "dark",
            "zoom": 100,
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": "de",
                "expected_language": "en",
                "detected_name": "German",
                "expected_name": "English",
                "session_zid": session_zid,
            }
        }

    spawn_calls = []
    opened_urls = []
    monkeypatch.setattr(kardenwort_desk, "spawn_ahk", lambda args, base_dir=None: spawn_calls.append((args, base_dir)))
    monkeypatch.setattr("kardenwort_controller.spawn_ahk", lambda args, base_dir=None: spawn_calls.append((args, base_dir)))
    monkeypatch.setattr("kardenwort_controller.persist_default_language", lambda language, base_dir=None: True)
    import webbrowser
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_urls.append(url))

    # Mock run_render_flow to simulate reverse order child generation with absolute paths
    mock_child_args = [
        "--seq-num", "4", "--restore", r"U:\voothi\20260629183335-kardenwort-desk\results\20260827010101-03.de.tsv",
        "--seq-num", "3", "--restore", r"U:\voothi\20260629183335-kardenwort-desk\results\20260827010101-02.de.tsv",
        "--seq-num", "2", "--restore", r"U:\voothi\20260629183335-kardenwort-desk\results\20260827010101-01.de.tsv",
    ]
    monkeypatch.setattr(
        "kardenwort_controller.run_render_flow",
        lambda **kwargs: ("<html>mock</html>", mock_child_args)
    )

    req_body = {"session_zid": session_zid, "action": "switch"}
    req_data = json.dumps(req_body).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/api/v1/confirm-language",
        data=req_data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        res = data.get("data", {})
        assert res.get("ok") is True
        assert res.get("action") == "switch"
        assert res.get("language") == "de"
        assert len(res.get("urls", [])) == 4

    # 1. Check in-memory config updated to 'de'
    assert server.config.get("settings", "default_language") == "de"

    # 2. Check spawn_ahk signaled with --set-language de
    assert len(spawn_calls) >= 1
    assert spawn_calls[-1][0] == ["--set-language", "de"]

    # 3. Check opened tab order: Master tab first, then Child 3, Child 2, Child 1 LAST
    assert len(opened_urls) == 4
    assert f"session_zid={session_zid}" in opened_urls[0] and "seq_num=1" in opened_urls[0]
    assert "20260827010101-03" in opened_urls[1] and "seq_num=4" in opened_urls[1]
    assert "20260827010101-02" in opened_urls[2] and "seq_num=3" in opened_urls[2]
    assert "20260827010101-01" in opened_urls[3] and "seq_num=2" in opened_urls[3]  # Opened LAST -> Active focus!


def test_confirm_language_validation_errors(running_controller):
    """
    Verify POST /api/v1/confirm-language returns proper error codes on bad input or missing draft.
    """
    server_url, server = running_controller

    # Missing session_zid
    req = urllib.request.Request(
        f"{server_url}/api/v1/confirm-language",
        data=json.dumps({"action": "switch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5.0)
    assert exc_info.value.code == 400

    # Invalid action
    req = urllib.request.Request(
        f"{server_url}/api/v1/confirm-language",
        data=json.dumps({"session_zid": "123", "action": "invalid_action"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5.0)
    assert exc_info.value.code == 400

    # Non-existent draft
    req = urllib.request.Request(
        f"{server_url}/api/v1/confirm-language",
        data=json.dumps({"session_zid": "99999999999999", "action": "switch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5.0)
    assert exc_info.value.code == 404


def test_session_status_worker_sentinel_lock_busy(running_controller):
    """
    Assert that active worker sentinel locks report is_busy = True (is_finished = False, stage = 'translating').
    """
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_zid = kardenwort_desk.generate_unique_zid()
    results_dir = kardenwort_desk.resolve_results_dir(server.resolved_paths, server.config)

    storage_adapter.save_session(
        session_zid=storage_zid,
        slug="test-sentinel-api",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Das Buch liegt hier.",
        sentences=[{"sentence_index": 0, "sentence_source": "Das Buch liegt hier.", "sentence_destination": "The book lies here."}],
        headers=["WordSource", "WordDestination", "TokenOrder"],
        data_rows=[["Buch", "book", "0"], ["liegt", "lies", "1"]],
    )
    sentinel_path = (results_dir / f"{storage_zid}.worker.lock") if results_dir else Path(f"{storage_zid}.worker.lock")
    try:
        with storage_adapter.file_lock(sentinel_path):
            req = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                assert resp.status == 200
                res = json.loads(resp.read().decode("utf-8"))
                data = res.get("data", res)
                assert data.get("ok") is True
                assert data.get("is_finished") is False
                assert data.get("stage") == "translating"
    finally:
        try:
            storage_adapter.delete_session(storage_zid)
        except Exception:
            pass


def test_session_status_untranslated_lemmas_reports_translating(running_controller):
    """
    Assert that recently created session with untranslated lemmas reports is_finished = False.
    """
    server_url, server = running_controller
    storage_adapter = kardenwort_desk.get_storage_adapter(server.config, server.resolved_paths)
    storage_zid = kardenwort_desk.generate_unique_zid()

    storage_adapter.save_session(
        session_zid=storage_zid,
        slug="test-pending-lemmas-api",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Das Buch liegt hier.",
        sentences=[{"sentence_index": 0, "sentence_source": "Das Buch liegt hier.", "sentence_destination": "The book lies here."}],
        headers=["WordSource", "WordDestination", "TokenOrder"],
        data_rows=[["Buch", "", "0"], ["liegt", "", "1"]],
    )
    try:
        req = urllib.request.Request(f"{server_url}/session/status?zid={storage_zid}")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            data = res.get("data", res)
            assert data.get("ok") is True
            assert data.get("is_finished") is False
            assert data.get("stage") == "translating"
    finally:
        try:
            storage_adapter.delete_session(storage_zid)
        except Exception:
            pass


def test_progressive_enqueue_endpoint_and_queue_status(running_controller, monkeypatch):
    """
    Verify POST /session/progressive/enqueue enqueues tasks and GET /api/v1/queue/status reports queue metrics.
    """
    server_url, server = running_controller
    sess_zid = kardenwort_desk.generate_unique_zid()

    monkeypatch.setattr(server.arbiter.enrichment_queue, "_execute_progressive_task", lambda *a, **kw: None)

    # Pre-create session in arbiter
    server.arbiter.sessions[sess_zid] = {
        "session_zid": sess_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Das ist ein Test.",
        "tsv_path": None,
        "comments": [],
        "headers": ["WordSource", "WordDestination", "TokenOrder"],
        "data_rows": [["Test", "", "0"]],
        "sentence_translation": "",
        "fingerprint": "fp1",
        "lock": threading.Lock(),
        "created_at": time.time(),
    }

    # Test enqueue
    req_body = {
        "session_zid": sess_zid,
        "language": "de",
        "target_lang": "ru",
        "token": server.api_key,
    }
    req = urllib.request.Request(
        f"{server_url}/session/progressive/enqueue",
        data=json.dumps(req_body).encode('utf-8'),
        headers={"Content-Type": "application/json", "X-API-Token": server.api_key}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode('utf-8'))
        data = res.get("data", res)
        assert data.get("status") in ("queued", "in_progress")
        assert data.get("session_zid") == sess_zid

    # Test queue status endpoint
    req_status = urllib.request.Request(
        f"{server_url}/api/v1/queue/status",
        headers={"X-API-Token": server.api_key}
    )
    with urllib.request.urlopen(req_status, timeout=5.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode('utf-8'))
        data = res.get("data", res)
        assert "max_workers" in data
        assert "translation_max_workers" in data
        assert "translation_cache_size" in data


def test_progressive_in_flight_deduplication_and_sibling_sse_broadcast(running_controller):
    """
    Verify in-flight deduplication coalescing and SSE broadcast across sibling sessions.
    """
    server_url, server = running_controller
    sess_a = kardenwort_desk.generate_unique_zid()
    sess_b = kardenwort_desk.generate_unique_zid()

    headers = ["WordSource", "WordDestination", "TokenOrder"]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination"}

    server.arbiter.sessions[sess_a] = {
        "session_zid": sess_a,
        "language": "de",
        "target_lang": "ru",
        "text": "Flugzeug",
        "tsv_path": None,
        "comments": [],
        "headers": headers,
        "data_rows": [["Flugzeug", "", "0"]],
        "role_fields": role_fields,
        "sentence_translation": "",
        "fingerprint": "fpa",
        "lock": threading.Lock(),
        "created_at": time.time(),
    }

    server.arbiter.sessions[sess_b] = {
        "session_zid": sess_b,
        "language": "de",
        "target_lang": "ru",
        "text": "Flugzeug",
        "tsv_path": None,
        "comments": [],
        "headers": headers,
        "data_rows": [["Flugzeug", "", "0"]],
        "role_fields": role_fields,
        "sentence_translation": "",
        "fingerprint": "fpb",
        "lock": threading.Lock(),
        "created_at": time.time(),
    }

    sub_b = server.arbiter.register_subscriber(sess_b)

    # Propagate translation to sibling sessions
    translations = {"Flugzeug": "самолёт"}
    server.arbiter.propagate_translations_to_siblings(translations, exclude_session_zid=sess_a, language="de")

    assert server.arbiter.sessions[sess_b]["data_rows"][0][1] == "самолёт"
    event = sub_b.get(timeout=2.0)
    assert event["type"] == "update"
    assert event["stage"] == "translated"
    server.arbiter.unregister_subscriber(sess_b, sub_b)



