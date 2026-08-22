import os
import sys
import json
import time
import socket
import threading
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
import pytest

import kardenwort_desk
from kardenwort_db import KardenwortDB
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
def admin_controller_server(tmp_path_factory):
    test_dir = tmp_path_factory.mktemp("kardenwort_admin_test")
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    # Point db to isolated test db
    test_db_path = test_dir / "kardenwort_test.db"
    resolved_paths["sqlite_db_path"] = str(test_db_path)
    resolved_paths["db_path"] = str(test_db_path)
    if "storage" not in config:
        config.add_section("storage")
    config["storage"]["sqlite_db_path"] = str(test_db_path)
    if "db" not in config:
        config.add_section("db")
    config["db"]["path"] = str(test_db_path)

    # Initialize DB & migrations
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    db.run_migrations()

    # Pre-populate sample session & projects
    sess_zid = "20260821200001"
    db.insert_session({
        "zid": sess_zid,
        "raw_text": "Hallo Welt",
        "clean_text": "Hallo Welt",
        "source_language": "de",
        "target_language": "en",
        "text_mode": "single",
        "slug": "sample-session"
    })

    root_proj_id = db.create_project(title="Test Book", slug="test-book", description="A test book")
    child_proj_id = db.create_project(title="Chapter 1", slug="chapter-1", parent_id=root_proj_id)
    db.link_session_to_project(child_proj_id, sess_zid)

    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), ControllerRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "admin-secret-token"
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()
    server.server_port = port

    server.arbiter = SessionArbiter(config, resolved_paths)
    server.arbiter.goldendict = goldendict
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    server_url = f"http://127.0.0.1:{port}"
    yield server_url, server, db, root_proj_id, child_proj_id, sess_zid

    server.shutdown()
    server.server_close()


def make_admin_request(url, path, method="GET", body=None, token="admin-secret-token"):
    full_url = f"{url}{path}"
    headers = {}
    if token:
        headers["X-API-Token"] = token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(full_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp_body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                parsed = json.loads(resp_body.decode("utf-8"))
                if isinstance(parsed, dict) and "data" in parsed:
                    return resp.status, parsed["data"]
                return resp.status, parsed
            return resp.status, resp_body.decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, err_body


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------
def test_admin_html_and_assets_serving(admin_controller_server):
    url, _, _, _, _, _ = admin_controller_server

    # 1. GET /admin with token
    status, html_content = make_admin_request(url, "/admin")
    assert status == 200
    assert "Kardenwort Desk Admin" in html_content
    assert "<title>Kardenwort Desk Admin</title>" in html_content
    assert 'id="confirm-modal"' in html_content
    assert 'id="btn-confirm-ok"' in html_content

    # 2. GET /assets/admin.css
    status, css_content = make_admin_request(url, "/assets/admin.css")
    assert status == 200
    assert "--bg-primary" in css_content
    assert ".modal-card-sm" in css_content

    # 3. GET /assets/admin.js
    status, js_content = make_admin_request(url, "/assets/admin.js")
    assert status == 200
    assert "loadProjectTree" in js_content
    assert "showConfirmDialog" in js_content


def test_admin_auth_enforcement(admin_controller_server):
    url, _, _, _, _, _ = admin_controller_server

    # Missing token -> 403
    status, resp = make_admin_request(url, "/api/v1/admin/projects", token="")
    assert status == 403
    assert resp["error_code"] == "UNAUTHORIZED"

    # Invalid token -> 403
    status, resp = make_admin_request(url, "/api/v1/admin/projects", token="wrong-token")
    assert status == 403
    assert resp["error_code"] == "UNAUTHORIZED"

    # Query param token -> 200
    status, resp = make_admin_request(url, "/api/v1/admin/projects?token=admin-secret-token", token="")
    assert status == 200
    assert resp["ok"] is True


def test_admin_project_tree_crud(admin_controller_server):
    url, _, db, root_id, child_id, sess_zid = admin_controller_server

    # 1. GET project tree
    status, resp = make_admin_request(url, "/api/v1/admin/projects")
    assert status == 200
    projects = resp["projects"]
    assert len(projects) >= 1
    root = next(p for p in projects if p["id"] == root_id)
    assert root["title"] == "Test Book"
    assert len(root["children"]) >= 1
    assert root["children"][0]["id"] == child_id

    # 2. POST create new project
    status, resp = make_admin_request(url, "/api/v1/admin/projects", method="POST", body={
        "title": "New Series",
        "description": "A new novel series"
    })
    assert status == 200
    new_proj_id = resp["project_id"]

    # 3. POST update project
    status, resp = make_admin_request(url, "/api/v1/admin/projects/update", method="POST", body={
        "project_id": new_proj_id,
        "title": "Updated Series Title"
    })
    assert status == 200
    assert resp["ok"] is True

    # 4. POST reorder project sessions
    status, resp = make_admin_request(url, "/api/v1/admin/projects/reorder", method="POST", body={
        "project_id": child_id,
        "session_zids": [sess_zid]
    })
    assert status == 200
    assert resp["ok"] is True


def test_admin_trash_lifecycle(admin_controller_server):
    url, _, db, _, _, _ = admin_controller_server

    # Create temporary session to soft-delete
    temp_zid = "20260821200099"
    db.insert_session({
        "zid": temp_zid,
        "raw_text": "Trash Me",
        "clean_text": "Trash Me",
        "source_language": "de",
        "target_language": "en",
        "text_mode": "single"
    })
    db.soft_delete_session(temp_zid)

    # 1. GET /api/v1/admin/trash
    status, resp = make_admin_request(url, "/api/v1/admin/trash")
    assert status == 200
    del_zids = [s["zid"] for s in resp["sessions"]]
    assert temp_zid in del_zids

    # 2. POST /api/v1/admin/trash/restore
    status, resp = make_admin_request(url, "/api/v1/admin/trash/restore", method="POST", body={
        "zid": temp_zid
    })
    assert status == 200
    assert resp["ok"] is True
    assert resp["restored_type"] == "session"

    # Soft-delete again to test purge
    db.soft_delete_session(temp_zid)
    status, resp = make_admin_request(url, "/api/v1/admin/trash/purge", method="POST", body={})
    assert status == 200
    assert resp["purged_sessions"] >= 1


def test_admin_database_maintenance_suite(admin_controller_server):
    url, _, db, _, _, _ = admin_controller_server

    # 1. Physical Snapshot Backup
    status, resp = make_admin_request(url, "/api/v1/admin/backup/snapshot", method="POST", body={})
    assert status == 200
    assert resp["ok"] is True
    assert resp["filename"].endswith("-kardenwort.db")
    assert resp["bytes"] > 0
    assert os.path.exists(resp["path"])

    # 2. Logical SQL Dump Streaming
    status, sql_dump = make_admin_request(url, "/api/v1/admin/backup/dump.sql")
    assert status == 200
    assert "CREATE TABLE" in sql_dump
    assert "projects" in sql_dump
    assert "sessions" in sql_dump

    # 3. Asynchronous Database Vacuum
    status, resp = make_admin_request(url, "/api/v1/admin/db/vacuum", method="POST", body={})
    assert status == 200
    assert resp["ok"] is True
    assert resp["status"] == "dispatched"

    # 4. Health & Telemetry Metrics
    status, resp = make_admin_request(url, "/api/v1/admin/telemetry")
    assert status == 200
    assert resp["ok"] is True
    db_telemetry = resp["database"]
    assert "size_bytes" in db_telemetry
    assert "active_sessions" in db_telemetry
    assert "total_projects" in db_telemetry
    assert db_telemetry["integrity_ok"] is True


def test_admin_sessions_explorer_api(admin_controller_server):
    url, _, db, root_id, child_id, sess_zid = admin_controller_server

    # Create unassigned session for testing filters
    unassigned_zid = "20260821200088"
    db.insert_session({
        "zid": unassigned_zid,
        "raw_text": "Ein unassigned session text",
        "clean_text": "Ein unassigned session text",
        "source_language": "de",
        "target_language": "en",
        "text_mode": "single",
        "slug": "standalone-test"
    })
    db.insert_sentence({
        "session_zid": unassigned_zid,
        "sentence_index": 0,
        "sentence_source": "Ein unassigned session text",
        "sentence_destination": "An unassigned session text"
    })
    db.insert_word({
        "session_zid": unassigned_zid,
        "sentence_index": 0,
        "token_order": 0,
        "quotation": "Ein",
        "lemma": "ein"
    })

    # 1. GET /api/v1/admin/sessions (default limit)
    status, resp = make_admin_request(url, "/api/v1/admin/sessions")
    assert status == 200
    assert resp["ok"] is True
    assert "sessions" in resp
    assert "total_count" in resp
    assert resp["total_count"] >= 2
    
    # Check session structure
    sess_item = next(s for s in resp["sessions"] if s["zid"] == unassigned_zid)
    assert sess_item["slug"] == "standalone-test"
    assert sess_item["source_language"] == "de"
    assert sess_item["sentence_count"] == 1
    assert sess_item["word_count"] == 1
    assert sess_item["projects"] == []

    assigned_item = next(s for s in resp["sessions"] if s["zid"] == sess_zid)
    assert len(assigned_item["projects"]) >= 1
    assert assigned_item["projects"][0]["id"] == child_id

    # 2. Search query filter
    status, resp = make_admin_request(url, "/api/v1/admin/sessions?query=standalone-test")
    assert status == 200
    assert resp["total_count"] == 1
    assert resp["sessions"][0]["zid"] == unassigned_zid

    # 3. Language filter
    status, resp = make_admin_request(url, "/api/v1/admin/sessions?language=de")
    assert status == 200
    assert all(s["source_language"] == "de" for s in resp["sessions"])

    # 4. Assigned filter
    status, resp = make_admin_request(url, "/api/v1/admin/sessions?assigned=unassigned")
    assert status == 200
    zids = [s["zid"] for s in resp["sessions"]]
    assert unassigned_zid in zids
    assert sess_zid not in zids

    status, resp = make_admin_request(url, "/api/v1/admin/sessions?assigned=assigned")
    assert status == 200
    zids = [s["zid"] for s in resp["sessions"]]
    assert sess_zid in zids
    assert unassigned_zid not in zids

    # 5. POST /api/v1/admin/sessions/delete
    status, resp = make_admin_request(url, "/api/v1/admin/sessions/delete", method="POST", body={
        "session_zid": unassigned_zid
    })
    assert status == 200
    assert resp["ok"] is True

    # Confirm soft-deleted
    status, resp = make_admin_request(url, f"/api/v1/admin/sessions?query={unassigned_zid}")
    assert status == 200
    assert resp["total_count"] == 0

    # 6. Reader Launch / Rendering Endpoints
    # GET /?session_zid=sess_zid (Full Kardenwort-Desk Window view)
    status, html_content = make_admin_request(url, f"/?session_zid={sess_zid}")
    assert status == 200
    assert ("lemma-table" in html_content or "source-container" in html_content)
    assert sess_zid in html_content

    # GET /?session_zid=sess_zid&view=goldendict (Compact GoldenDict view)
    status, html_content = make_admin_request(url, f"/?session_zid={sess_zid}&view=goldendict")
    assert status == 200
    assert "kw-lookup-container" in html_content

    # 7. Project Reader View & Export Endpoints
    # GET /?project_id=root_id
    status, html_content = make_admin_request(url, f"/?project_id={root_id}")
    assert status == 200
    assert ("lemma-table" in html_content or "kw-lookup-container" in html_content)

    # GET /api/v1/lookup?project_id=root_id
    status, resp = make_admin_request(url, f"/api/v1/lookup?project_id={root_id}")
    assert status == 200
    assert resp["ok"] is True
    assert resp["project_id"] == root_id
    assert resp["total_sessions"] >= 1

    # POST /api/v1/admin/projects/export-deck
    status, resp = make_admin_request(url, "/api/v1/admin/projects/export-deck", method="POST", body={
        "project_id": root_id,
        "language": "German",
    })
    assert status == 200
    assert resp["ok"] is True
    assert resp["project_id"] == root_id
    assert resp["total_sessions"] >= 1


def test_admin_api_batch_delete_sessions(admin_controller_server):
    url, server, db, root_proj_id, child_proj_id, sess_zid = admin_controller_server

    # Create 3 temporary sessions
    zids = ["20260822990001", "20260822990002", "20260822990003"]
    for z in zids:
        db.insert_session({
            "zid": z,
            "raw_text": "Bonjour le monde",
            "clean_text": "Bonjour le monde",
            "source_language": "fr",
            "target_language": "ru",
            "text_mode": "single",
            "slug": f"batch-test-{z}"
        })

    # 1. Batch delete explicit list with exclusion
    status, resp = make_admin_request(url, "/api/v1/admin/sessions/batch-delete", method="POST", body={
        "mode": "explicit",
        "session_zids": [zids[0], zids[1]],
        "excluded_zids": [zids[1]],
    })
    assert status == 200
    assert resp["ok"] is True
    assert resp["deleted_count"] == 1

    # Verify zids[0] is deleted, zids[1] is NOT deleted
    status, resp = make_admin_request(url, f"/api/v1/admin/sessions?query={zids[0]}")
    assert status == 200
    assert resp["total_count"] == 0

    status, resp = make_admin_request(url, f"/api/v1/admin/sessions?query={zids[1]}")
    assert status == 200
    assert resp["total_count"] == 1

    # 2. Batch delete with all_matching filter for French language
    status, resp = make_admin_request(url, "/api/v1/admin/sessions/batch-delete", method="POST", body={
        "mode": "all_matching",
        "filter": {"language": "fr"},
    })
    assert status == 200
    assert resp["ok"] is True
    assert resp["deleted_count"] == 2  # zids[1] and zids[2]

    # Verify all French sessions are soft deleted
    status, resp = make_admin_request(url, "/api/v1/admin/sessions?language=fr")
    assert status == 200
    assert resp["total_count"] == 0
