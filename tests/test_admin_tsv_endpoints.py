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
def tsv_test_controller_server(tmp_path_factory):
    test_dir = tmp_path_factory.mktemp("kardenwort_tsv_test")
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, _ = kardenwort_desk.load_config(desk_dir / "config.ini")

    test_db_path = test_dir / "kardenwort_tsv.db"
    resolved_paths["sqlite_db_path"] = str(test_db_path)
    resolved_paths["db_path"] = str(test_db_path)
    if "storage" not in config:
        config.add_section("storage")
    config["storage"]["sqlite_db_path"] = str(test_db_path)
    if "db" not in config:
        config.add_section("db")
    config["db"]["path"] = str(test_db_path)

    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    db.run_migrations()

    # Create a rich sample session bundle
    sample_zid = "20260822102500"
    session_data = {
        "zid": sample_zid,
        "slug": "goldene-gans",
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "multi",
        "source_raw_text": "Es war einmal ein Mann. Er hatte drei Söhne.",
    }
    sentences_data = [
        {
            "session_zid": sample_zid,
            "sentence_index": 1,
            "sentence_source": "Es war einmal ein Mann.",
            "sentence_destination": "Жил-был однажды человек.",
            "sentence_destination2": "Once upon a time there was a man.",
            "sentence_source_ipa": "ɛs vaːɐ̯ ˈaɪ̯nmaːl aɪ̯n man",
            "sentence_source_audio": None,
        },
        {
            "session_zid": sample_zid,
            "sentence_index": 2,
            "sentence_source": "Er hatte drei Söhne.",
            "sentence_destination": "У него было три сына.",
            "sentence_destination2": "He had three sons.",
            "sentence_source_ipa": "eːɐ̯ ˈhatə dʁaɪ̯ ˈzøːnə",
            "sentence_source_audio": None,
        }
    ]
    words_data = [
        {
            "session_zid": sample_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "Mann",
            "inflected_form": "Mann",
            "lemma": "Mann",
            "pos": "NOUN",
            "morphology": "Gender=Masc|Number=Sing",
            "ipa": "man",
            "word_destination": "человек / мужчина",
            "word_destination_inflected": None,
            "selected": 1,
            "leitner_box": 2,
            "leitner_due": "2026-08-25",
            "deck": "Default",
            "classification_oxford": "A1",
            "classification_goethe": "A1",
            "extra_fields": {"CustomNote": "Famous fairytale noun"},
        },
        {
            "session_zid": sample_zid,
            "sentence_index": 2,
            "token_order": 1,
            "quotation": "Söhne",
            "inflected_form": "Söhne",
            "lemma": "Sohn",
            "pos": "NOUN",
            "morphology": "Gender=Masc|Number=Plur",
            "ipa": "ˈzøːnə",
            "word_destination": "сыновья",
            "word_destination_inflected": None,
            "selected": 1,
            "leitner_box": 1,
            "leitner_due": None,
            "deck": "Default",
            "classification_oxford": "A1",
            "classification_goethe": "A1",
            "extra_fields": None,
        }
    ]
    db.save_session_bundle(session_data, sentences_data, words_data)

    port = get_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), ControllerRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = "admin-test-token"
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
    yield server_url, server, db, sample_zid

    server.shutdown()
    server.server_close()


def make_request(url, path, method="GET", body=None, token="admin-test-token", raw_body=None, content_type="application/json"):
    full_url = f"{url}{path}"
    headers = {}
    if token:
        headers["X-API-Token"] = token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    elif raw_body is not None:
        headers["Content-Type"] = content_type
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")

    req = urllib.request.Request(full_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            resp_bytes = resp.read()
            c_type = resp.headers.get("Content-Type", "")
            return resp.status, resp_bytes, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_get_session_tsv_dynamic_streaming(tsv_test_controller_server):
    server_url, server, db, sample_zid = tsv_test_controller_server

    # 1. Download dynamic TSV via GET /api/v1/sessions/<zid>/tsv
    status, body_bytes, headers = make_request(server_url, f"/api/v1/sessions/{sample_zid}/tsv")
    assert status == 200
    assert "text/tab-separated-values" in headers.get("Content-Type", "")
    assert f'filename="{sample_zid}-goldene-gans.de.tsv"' in headers.get("Content-Disposition", "")

    tsv_text = body_bytes.decode("utf-8")
    lines = tsv_text.strip().split("\n")
    assert len(lines) >= 3  # Comments + Header + Data rows

    # Verify header contains standard column names
    header_line = [l for l in lines if not l.startswith("#")][0]
    headers_list = header_line.split("\t")
    headers_lower = [h.lower() for h in headers_list]
    assert "wordsource" in headers_lower
    assert "quotation" in headers_lower
    assert "sentencesource" in headers_lower
    assert "sentencedestination" in headers_lower

    # Verify data contains our words
    assert "Mann" in tsv_text
    assert "Söhne" in tsv_text
    assert "Es war einmal ein Mann." in tsv_text
    assert "Er hatte drei Söhne." in tsv_text


def test_get_session_tsv_not_found(tsv_test_controller_server):
    server_url, server, db, _ = tsv_test_controller_server
    status, body_bytes, _ = make_request(server_url, "/api/v1/sessions/99999999999999/tsv")
    assert status == 404


def test_post_import_tsv_json_payload(tsv_test_controller_server):
    server_url, server, db, _ = tsv_test_controller_server

    tsv_content = (
        "# Exported from external workflow\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Katze\tKatze\tкошка\t1\tDie Katze schläft auf dem Sofa.\tКошка спит на диване.\t1\n"
        "Hund\tHund\tсобака\t2\tDer Hund bellt laut im Garten.\tСобака громко лает в саду.\t0\n"
    )

    imported_zid = "20260822120000"
    payload = {
        "tsv_content": tsv_content,
        "filename": f"{imported_zid}-haustiere.de.tsv",
        "session_zid": imported_zid,
        "language": "de"
    }

    status, body_bytes, _ = make_request(server_url, "/api/v1/sessions/import-tsv", method="POST", body=payload)
    assert status == 200
    raw_res = json.loads(body_bytes.decode("utf-8"))
    res = raw_res.get("data", raw_res)
    assert res.get("ok") is True
    assert res.get("session_zid") == imported_zid
    assert res.get("sentences_count") == 2
    assert res.get("words_count") == 2

    # Verify in SQLite database
    bundle = db.get_session_bundle(imported_zid)
    assert bundle is not None
    assert bundle["session"]["zid"] == imported_zid
    assert bundle["session"]["slug"] == "haustiere"
    assert bundle["session"]["source_language"] == "de"
    assert len(bundle["sentences"]) == 2
    assert len(bundle["words"]) == 2

    words = {w["lemma"]: w for w in bundle["words"]}
    assert "Katze" in words
    assert words["Katze"]["word_destination"] == "кошка"
    assert words["Katze"]["selected"] == 1
    assert "Hund" in words
    assert words["Hund"]["word_destination"] == "собака"
    assert words["Hund"]["selected"] == 0


def test_post_import_tsv_raw_text(tsv_test_controller_server):
    server_url, server, db, _ = tsv_test_controller_server

    raw_tsv = (
        "Quotation\tWordSource\tWordDestination\tSentenceSource\tDeskSelected\n"
        "Vogel\tVogel\tптица\tEin Vogel singt.\t1\n"
    )
    imported_zid = "20260822130000"

    status, body_bytes, _ = make_request(
        server_url,
        f"/api/v1/sessions/import-tsv?session_zid={imported_zid}&slug=vogel-session&language=de",
        method="POST",
        raw_body=raw_tsv,
        content_type="text/tab-separated-values"
    )
    assert status == 200
    raw_res = json.loads(body_bytes.decode("utf-8"))
    res = raw_res.get("data", raw_res)
    assert res.get("ok") is True
    assert res.get("session_zid") == imported_zid

    bundle = db.get_session_bundle(imported_zid)
    assert bundle is not None
    assert len(bundle["words"]) == 1
    assert bundle["words"][0]["lemma"] == "Vogel"
    assert bundle["words"][0]["word_destination"] == "птица"


def test_tsv_export_import_roundtrip(tsv_test_controller_server):
    server_url, server, db, sample_zid = tsv_test_controller_server

    # 1. Download dynamic TSV
    status, tsv_bytes, _ = make_request(server_url, f"/api/v1/sessions/{sample_zid}/tsv")
    assert status == 200
    tsv_text = tsv_bytes.decode("utf-8")

    # 2. Re-import under a clone ZID
    clone_zid = "20260822140000"
    payload = {
        "tsv_content": tsv_text,
        "filename": f"{clone_zid}-clone.de.tsv",
        "session_zid": clone_zid
    }
    status, body_bytes, _ = make_request(server_url, "/api/v1/sessions/import-tsv", method="POST", body=payload)
    assert status == 200

    # 3. Compare SQLite bundles
    orig_bundle = db.get_session_bundle(sample_zid)
    clone_bundle = db.get_session_bundle(clone_zid)

    assert len(clone_bundle["sentences"]) == len(orig_bundle["sentences"])
    assert len(clone_bundle["words"]) == len(orig_bundle["words"])

    for s1, s2 in zip(orig_bundle["sentences"], clone_bundle["sentences"]):
        assert s1["sentence_source"] == s2["sentence_source"]
        assert s1["sentence_destination"] == s2["sentence_destination"]

    for w1, w2 in zip(orig_bundle["words"], clone_bundle["words"]):
        assert w1["lemma"] == w2["lemma"]
        assert w1["quotation"] == w2["quotation"]
        assert w1["word_destination"] == w2["word_destination"]
        assert w1["selected"] == w2["selected"]
        assert w1["leitner_box"] == w2["leitner_box"]
