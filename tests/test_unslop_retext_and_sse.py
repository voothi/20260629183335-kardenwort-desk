import json
import queue
import socket
import sqlite3
import threading
import time
import urllib.request
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import kardenwort_desk
from http_server import ControllerRequestHandler, EventDispatcher, ThreadingHTTPServer
from kardenwort_controller import SessionArbiter, ProcessSupervisor
from scripts.cleanup_blended_translations import cleanup_blended_translations


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def controller_server():
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
    server.api_key = "test-api-key"
    server.server_port = port
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()

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


def test_session_arbiter_delegates_to_event_dispatcher():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    arbiter = SessionArbiter(config, resolved_paths)
    zid = "20261023153523"
    dispatcher = EventDispatcher.get_instance()

    # Precondition: verify arbiter delegates subscriber management to EventDispatcher
    q = arbiter.register_subscriber(zid)
    try:
        assert dispatcher.has_subscribers(zid) is True
        assert not hasattr(arbiter, "_subscribers") or arbiter._subscribers is None

        test_ev = {"type": "retext_test", "data": "hello"}
        if hasattr(arbiter, "_publish_event"):
            arbiter._publish_event(zid, test_ev)
        else:
            arbiter.emit_event(zid, test_ev)

        received = q.get(timeout=1.0)
        assert received["type"] == "retext_test"
        assert received["session_zid"] == zid
    finally:
        arbiter.unregister_subscriber(zid, q)
        assert dispatcher.has_subscribers(zid) is False


def test_persist_retext_direct_provenance_call(tmp_path):
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, _, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    arbiter = SessionArbiter(config, resolved_paths)

    mock_adapter = MagicMock()
    mock_adapter.backend_name = "sqlite"
    mock_adapter.file_lock.return_value = nullcontext()
    tsv_file = tmp_path / "test.tsv"
    tsv_file.write_text("# comment\nWord\tSentenceDestination\tSentenceSourceIndex\n", encoding="utf-8")
    mock_adapter.load_tsv_rows.return_value = (["# comment"], ["Word", "SentenceDestination", "SentenceSourceIndex"], [["w1", "dest1", "1"]])

    arbiter.storage_adapter = mock_adapter
    session_zid = "20261023153523"
    arbiter.sessions[session_zid] = {
        "tsv_path": str(tsv_file),
        "headers": ["Word", "SentenceDestination", "SentenceSourceIndex"],
        "data_rows": [["w1", "dest1", "1"]],
    }

    # 1. Verify direct call passes provenance
    arbiter._persist_retext_updates(
        session_zid=session_zid,
        sentence_trans={1: "translated sentence"},
        active_text_prov="live:deepl",
        tsv_path=tsv_file,
    )
    assert mock_adapter.update_sentence_translation.called
    _, kwargs = mock_adapter.update_sentence_translation.call_args
    assert kwargs.get("provenance") == "live:deepl"

    # 2. Verify that TypeError fails fast and is not retried defensively without provenance
    mock_adapter.reset_mock()
    mock_adapter.update_sentence_translation.side_effect = TypeError("Invalid argument")
    with pytest.raises(TypeError, match="Invalid argument"):
        arbiter._persist_retext_updates(
            session_zid=session_zid,
            sentence_trans={1: "translated sentence"},
            active_text_prov="live:deepl",
            tsv_path=tsv_file,
        )
    assert mock_adapter.update_sentence_translation.call_count == 1


def test_get_session_status_cqrs_no_mutation(controller_server):
    server_url, server = controller_server
    session_zid = "20261023153523"

    server.arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "text": "Hello world.",
        "language": "en",
        "data_rows": [["Hello", "world"]],
        "headers": ["WordSource", "SentenceSource"],
    }

    mock_storage = MagicMock()
    mock_storage.backend_name = "sqlite"
    mock_storage.db.get_sentences_by_session.return_value = [
        {"sentence_index": 1, "sentence_source": "Hello world.", "sentence_destination": "Hallo Welt.", "text_provenance": "live:google"}
    ]

    with patch("kardenwort_controller.get_storage_adapter", return_value=mock_storage):
        req = urllib.request.Request(
            f"{server_url}/session/status?zid={session_zid}",
            headers={"X-API-Key": "test-api-key"},
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            sess_data = data.get("data", {})
            # The projected response should contain the provenance from SQLite
            assert sess_data.get("text_provenance") == "live:google"

    # CQRS Invariant: GET request must not mutate in-memory arbiter session state
    assert server.arbiter.sessions[session_zid].get("text_provenance") is None
    assert server.arbiter.sessions[session_zid].get("textProvenance") is None


def test_strict_asset_loading_raises_filenotfound(tmp_path):
    empty_web = tmp_path / "web"
    empty_web.mkdir()

    with pytest.raises(FileNotFoundError):
        kardenwort_desk.get_bundled_css(web_dir=empty_web)

    with pytest.raises(FileNotFoundError):
        kardenwort_desk.get_workspace_tabs_js(web_dir=empty_web, reload=True)

    with pytest.raises(FileNotFoundError):
        kardenwort_desk.get_lemma_table_js(web_dir=empty_web, reload=True)

    with pytest.raises(FileNotFoundError):
        kardenwort_desk.get_source_viewer_js(web_dir=empty_web, reload=True)

    with pytest.raises(FileNotFoundError):
        kardenwort_desk.get_translation_view_js(web_dir=empty_web, reload=True)


def test_cleanup_script_skips_missing_source(tmp_path):
    db_path = tmp_path / "test_cleanup.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE words (
            id INTEGER PRIMARY KEY,
            session_zid TEXT,
            sentence_index INTEGER,
            lemma TEXT,
            quotation TEXT,
            word_destination TEXT,
            extra_fields TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE sentences (
            session_zid TEXT,
            sentence_index INTEGER,
            sentence_source TEXT
        )
    """)
    conn.execute("""
        INSERT INTO words (id, session_zid, sentence_index, lemma, quotation, word_destination, extra_fields)
        VALUES (1, 'sess_1', 1, 'haus', 'haus', 'дом, здание', '{"ru": "дом"}')
    """)
    conn.execute("""
        INSERT INTO sentences (session_zid, sentence_index, sentence_source)
        VALUES ('sess_1', 1, '')
    """)
    conn.commit()
    conn.close()

    affected = cleanup_blended_translations(db_path, apply_changes=False)
    # Must skip because sentence_source is empty (cannot verify single occurrence)
    assert len(affected) == 0
