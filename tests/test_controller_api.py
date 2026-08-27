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
    when not in arbiter memory and reports is_finished=True.
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
        sentences=[{"sentence_index": 0, "sentence_source": "The quick brown fox"}],
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
    finally:
        try:
            storage_adapter.delete_session(sess_zid)
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
    import webbrowser
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened_urls.append(url))

    # Mock run_render_flow to simulate reverse order child generation
    mock_child_args = [
        "--seq-num", "4", "--restore", "results/20260827010101-03.de.tsv",
        "--seq-num", "3", "--restore", "results/20260827010101-02.de.tsv",
        "--seq-num", "2", "--restore", "results/20260827010101-01.de.tsv",
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


