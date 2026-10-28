import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import kardenwort_desk
from kardenwort_controller import ControllerRequestHandler, ProcessSupervisor, SessionArbiter
from kardenwort_desk import SqliteStorageAdapter


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def running_controller(tmp_path):
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    db_file = tmp_path / "integration_test.db"
    resolved_paths["sqlite_db_path"] = str(db_file)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=str(db_file))

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

    server.arbiter = SessionArbiter(config, resolved_paths)
    server.arbiter.goldendict = goldendict
    server.arbiter.storage_adapter = adapter

    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=False)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    server_url = f"http://127.0.0.1:{port}"
    yield server_url, server, adapter

    server.shutdown()
    server.server_close()


def test_retext_payload_structure_and_multidiv_separation(running_controller, monkeypatch):
    """
    Verify /session/retext returns normalized 1-indexed sentences dictionary,
    preserves discrete <div> wrappers per sentence (not single-wrapper collapse),
    and exposes live provider provenance.
    """
    server_url, server, adapter = running_controller
    session_zid = "20261022095501"

    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "SentenceDestination2", "WordDestination"
    ]
    initial_rows = [
        ["0", "and", "and", "1", "and mounting child nodes.", "и монтирование дочерних узлов.", "и монтирование дочерних узлов.", "и"],
        ["1", "mounting", "mount", "1", "and mounting child nodes.", "и монтирование дочерних узлов.", "и монтирование дочерних узлов.", "монтировать"],
        ["2", "Applied", "apply", "2", "Applied matching type casting", "Применено литье соответствующего типа", "Применено литье соответствующего типа", "применять"],
    ]
    source_raw_text = "and mounting child nodes. Applied matching type casting"
    results_dir = Path(server.resolved_paths["results_dir"])
    tsv_path = str(results_dir / f"{session_zid}.en.tsv")

    adapter.save_session(
        session_zid=session_zid,
        slug="and-mounting-child-nodes",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text=source_raw_text,
        tsv_path=tsv_path,
        headers=headers,
        comments=[],
        data_rows=initial_rows,
        sentences=[
            {
                "session_zid": session_zid,
                "sentence_index": 1,
                "sentence_source": "and mounting child nodes.",
                "sentence_destination": "и монтирование дочерних узлов.",
                "sentence_destination2": "и монтирование дочерних узлов.",
                "text_provenance": "live:google",
            },
            {
                "session_zid": session_zid,
                "sentence_index": 2,
                "sentence_source": "Applied matching type casting",
                "sentence_destination": "Применено литье соответствующего типа",
                "sentence_destination2": "Применено литье соответствующего типа",
                "text_provenance": "live:google",
            }
        ]
    )

    server.arbiter.sessions[session_zid] = {
        "text": source_raw_text,
        "language": "en",
        "data_rows": initial_rows,
        "headers": headers,
        "comments": [],
        "tsv_path": tsv_path,
        "role_fields": {
            "sentence_index": "SentenceSourceIndex",
            "sentence_source": "SentenceSource",
            "sentence_destination": "SentenceDestination",
            "sentence_destination2": "SentenceDestination2",
        }
    }

    # Mock translation to simulate DeepL live translation
    class MockResult(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.provenance = "live:deepl"

    def mock_translate_text(text, src, tgt, *a, **kw):
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return MockResult({
            0: f"DEEPL:{lines[0]}" if len(lines) > 0 else "DEEPL:1",
            1: f"DEEPL:{lines[1]}" if len(lines) > 1 else "DEEPL:2"
        })

    monkeypatch.setattr("kardenwort_controller.translate_source_text", mock_translate_text)

    # 1. POST /session/retext
    retext_url = f"{server_url}/session/retext"
    req_body = json.dumps({
        "session_zid": session_zid,
        "language": "en",
        "text_mode": "single"
    }).encode("utf-8")
    req = urllib.request.Request(
        retext_url,
        data=req_body,
        headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"}
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        retext_res = json.loads(resp.read().decode("utf-8"))

    payload = retext_res.get("data", retext_res)
    assert retext_res["status"] == "success"
    assert "sentences" in payload
    assert payload["sentences"]["1"] == "DEEPL:and mounting child nodes."
    assert payload["sentences"]["2"] == "DEEPL:Applied matching type casting"

    # Multi-div separation invariant: when >= 2 sentences exist, discrete <div> per sentence
    translated_html = payload.get("translatedText") or payload.get("translated_text")
    assert translated_html is not None
    assert translated_html.count("<div>") == 2
    assert "<div>DEEPL:and mounting child nodes.</div>" in translated_html
    assert "<div>DEEPL:Applied matching type casting</div>" in translated_html

    # Provenance attribution invariant
    assert payload["text_provenance"] == "live:deepl"
    assert payload["textProvenance"] == "live:deepl"


def test_subsequent_status_provenance_and_cold_cache_retention(running_controller, monkeypatch):
    """
    Verify GET /session/status retains live text_provenance and discrete sentences,
    even after the session is evicted from in-memory cache (cold-cache recovery).
    """
    server_url, server, adapter = running_controller
    session_zid = "20261022095502"

    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "SentenceDestination2", "WordDestination"
    ]
    initial_rows = [
        ["0", "and", "and", "1", "and mounting child nodes.", "и монтирование дочерних узлов.", "и монтирование дочерних узлов.", "и"],
        ["1", "Applied", "apply", "2", "Applied matching type casting", "Применено литье соответствующего типа", "Применено литье соответствующего типа", "применять"],
    ]
    source_raw_text = "and mounting child nodes. Applied matching type casting"
    results_dir = Path(server.resolved_paths["results_dir"])
    tsv_path = str(results_dir / f"{session_zid}.en.tsv")

    adapter.save_session(
        session_zid=session_zid,
        slug="and-mounting-child-nodes",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text=source_raw_text,
        tsv_path=tsv_path,
        headers=headers,
        comments=[],
        data_rows=initial_rows,
        sentences=[
            {
                "session_zid": session_zid,
                "sentence_index": 1,
                "sentence_source": "and mounting child nodes.",
                "sentence_destination": "DEEPL:и монтирование дочерних узлов.",
                "sentence_destination2": "DEEPL:и монтирование дочерних узлов.",
                "text_provenance": "live:deepl",
            },
            {
                "session_zid": session_zid,
                "sentence_index": 2,
                "sentence_source": "Applied matching type casting",
                "sentence_destination": "DEEPL:Применено литье соответствующего типа",
                "sentence_destination2": "DEEPL:Применено литье соответствующего типа",
                "text_provenance": "live:deepl",
            }
        ]
    )

    # In-memory session initialized without explicit text_provenance to test cold-cache extraction
    server.arbiter.sessions[session_zid] = {
        "text": source_raw_text,
        "language": "en",
        "data_rows": initial_rows,
        "headers": headers,
        "comments": [],
        "tsv_path": tsv_path,
        "role_fields": {
            "sentence_index": "SentenceSourceIndex",
            "sentence_source": "SentenceSource",
            "sentence_destination": "SentenceDestination",
            "sentence_destination2": "SentenceDestination2",
        }
    }

    # 1. GET /session/status while in memory without text_provenance
    status_url = f"{server_url}/session/status?zid={session_zid}"
    req = urllib.request.Request(status_url, headers={"X-API-Token": "test-controller-api-key"})
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        raw_resp = json.loads(resp.read().decode("utf-8"))
        st_data = raw_resp.get("data", raw_resp)

    # Provenance recovered from SQLite sentences
    assert st_data.get("text_provenance") == "live:deepl"
    assert st_data.get("textProvenance") == "live:deepl"
    assert st_data.get("active_text_provider") == "deepl"
    assert len(st_data.get("sentences", [])) == 2
    assert st_data["sentences"][0]["sentence_destination"] == "DEEPL:и монтирование дочерних узлов."
    assert "DEEPL:Применено литье соответствующего типа" in st_data["sentences"][1]["sentence_destination"]

    # 2. Completely evict from in-memory cache to test persistent storage recovery
    with server.arbiter._lock:
        del server.arbiter.sessions[session_zid]

    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        raw_cold = json.loads(resp.read().decode("utf-8"))
        cold_data = raw_cold.get("data", raw_cold)

    assert cold_data.get("text_provenance") == "live:deepl"
    assert cold_data.get("textProvenance") == "live:deepl"
    assert cold_data.get("active_text_provider") == "deepl"
    assert len(cold_data.get("sentences", [])) == 2


def seed_provenance_endpoint(adapter):
    sources = ['and mounting child nodes.', 'Applied matching type casting']
    translations = ['и монтирование дочерних узлов.', 'Применено литье соответствующего типа']
    headers = ['TokenOrder', 'Quotation', 'WordSource', 'SentenceSourceIndex',
               'SentenceSource', 'SentenceDestination', 'SentenceDestination2', 'WordDestination']
    rows = [[str(index), source.split()[0], source.split()[0], str(index + 1), source,
             translation, translation, translation]
            for index, (source, translation) in enumerate(zip(sources, translations))]
    adapter.save_session(session_zid='20261019235609', source_language='en', target_language='ru',
        text_mode='single', source_raw_text=' '.join(sources), headers=headers, data_rows=rows,
        text_provenance='live:deepl', sentences=[
            dict(sentence_index=index + 1, sentence_source=source, sentence_destination=translation,
                 sentence_destination2=translation, text_provenance='live:deepl')
            for index, (source, translation) in enumerate(zip(sources, translations))])


def test_live_session_20261019235609_endpoint_verification(tmp_path):
    """
    Execute endpoint verification on an isolated real SQLite session,
    confirming discrete sentence translations and verifying tooltip attribution reflects 'Translated via DeepL'.
    """
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")
    db_file = tmp_path / "endpoint.db"
    config.set('storage', 'backend', 'sqlite')
    config.set('storage', 'sqlite_db_path', str(db_file))
    resolved_paths["sqlite_db_path"] = str(db_file)
    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=str(db_file))
    assert db_file.exists(), "Production sqlite db must exist"
    seed_provenance_endpoint(adapter)

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

    server.arbiter = SessionArbiter(config, resolved_paths)
    server.arbiter.goldendict = goldendict
    server.arbiter.storage_adapter = adapter

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        session_zid = "20261019235609"
        status_url = f"http://127.0.0.1:{port}/session/status?zid={session_zid}"
        req = urllib.request.Request(status_url, headers={"X-API-Token": "test-controller-api-key"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            assert resp.status == 200
            raw_data = json.loads(resp.read().decode("utf-8"))
            st_data = raw_data.get("data", raw_data)

        # 1. Verify provenance retention reflects live:deepl
        assert st_data.get("text_provenance") == "live:deepl"
        assert st_data.get("textProvenance") == "live:deepl"
        assert st_data.get("active_text_provider") == "deepl"

        # 2. Verify discrete sentence translations
        sentences = st_data.get("sentences", [])
        assert len(sentences) >= 2
        s1 = next(s for s in sentences if s.get("sentence_index") == 1)
        s2 = next(s for s in sentences if s.get("sentence_index") == 2)
        assert s1.get("sentence_destination") == "и монтирование дочерних узлов."
        assert s2.get("sentence_destination") == "Применено литье соответствующего типа"

        # 3. Verify HTML rendering formats discrete <div> wrappers
        rendered_tt = st_data.get("translatedText") or ""
        assert "и монтирование дочерних узлов." in rendered_tt
        assert "Применено литье соответствующего типа" in rendered_tt
        assert rendered_tt.count("<div>") >= 2

        # 4. Verify tooltip helper attribution reflects Translated via DeepL
        tooltip = kardenwort_desk.format_provenance_tooltip(st_data["text_provenance"])
        assert tooltip == "Translated via DeepL"
    finally:
        server.shutdown()
        server.server_close()
