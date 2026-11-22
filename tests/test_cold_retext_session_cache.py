"""Real HTTP/SQLite acceptance tests for cold POST /session/retext session-cache invariants."""
import json
import queue
import time
import urllib.error
import urllib.request

import kardenwort_desk as desk
from tests.controller_test_support import controller_world  # noqa: F401


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


def _post(url, path, body, token):
    req = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Token": token},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw


def _get(url, path, token):
    req = urllib.request.Request(
        f"{url}{path}",
        headers={"X-API-Token": token},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw


def _payload(body):
    return body.get("data", body) if isinstance(body, dict) else body


def _seed_cold_session(
    server,
    zid,
    *,
    language="de",
    target_lang="ru",
    sentences=("Ein Hund bellt.", "Eine Katze schlaeft."),
    lemmas=("Hund", "Katze"),
    word_destinations=("собака", "кошка"),
):
    server.config.set("pipeline", "text_reprocess_provider", "deepl")
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    rows = [
        [
            "0",
            lemmas[0],
            lemmas[0],
            "1",
            sentences[0],
            "Old translation 1",
            word_destinations[0],
            "0",
        ],
        [
            "1",
            lemmas[1],
            lemmas[1],
            "2",
            sentences[1],
            "Old translation 2",
            word_destinations[1],
            "0",
        ],
    ]
    adapter.save_session(
        session_zid=zid,
        slug=f"cold-retext-{zid}",
        source_language=language,
        target_language=target_lang,
        text_mode="multi",
        source_raw_text="\n".join(sentences),
        headers=HEADERS,
        data_rows=rows,
        text_provenance="live:google",
        working_tsv_path=server.resolved_paths["results_dir"] / f"{zid}.{language}.tsv",
    )
    server.arbiter.sessions.pop(zid, None)
    assert zid not in server.arbiter.sessions


def test_cold_retext_then_save_succeeds_without_keyerror(controller_world):
    """Cold POST /session/retext followed by POST /session/save returns 200 without KeyError: 'lock'."""
    server_url, server = controller_world
    zid = "20261122130001"
    _seed_cold_session(server, zid)

    retext_status, retext_body = _post(
        server_url,
        "/session/retext",
        {"session_zid": zid, "language": "de", "text_mode": "multi"},
        server.api_key,
    )
    assert retext_status == 200, retext_body
    retext_data = _payload(retext_body)
    assert retext_data["text_provenance"] == "live:deepl"
    assert retext_data["sentences"]["1"] == "deepl: Ein Hund bellt."
    assert retext_data["sentences"]["2"] == "deepl: Eine Katze schlaeft."

    save_status, save_body = _post(
        server_url,
        "/session/save",
        {
            "session_zid": zid,
            "language": "de",
            "fingerprint": retext_data["fingerprint"],
            "deltas": [
                {
                    "row_id": 0,
                    "token_order": 0,
                    "sentence_idx": 1,
                    "column": "WordDestination",
                    "value": "пёс",
                }
            ],
        },
        server.api_key,
    )
    assert save_status == 200, save_body
    assert save_body["status"] == "success"

    cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    sents = {s["sentence_index"]: s for s in cold.db.get_sentences_by_session(zid)}
    assert sents[1]["sentence_destination"] == "deepl: Ein Hund bellt."
    assert sents[1]["text_provenance"] == "live:deepl"
    assert sents[2]["sentence_destination"] == "deepl: Eine Katze schlaeft."
    assert sents[2]["text_provenance"] == "live:deepl"

    words = {(w["sentence_index"], w["token_order"]): w for w in cold.db.get_words_by_session(zid)}
    assert words[(1, 0)]["word_destination"] == "пёс"
    assert words[(2, 1)]["word_destination"] == "кошка"


def test_cold_retext_then_status_returns_complete_rows_and_headers(controller_world):
    """Cold POST /session/retext followed by GET /session/status returns complete row fields."""
    server_url, server = controller_world
    zid = "20261122130002"
    _seed_cold_session(server, zid)

    retext_status, retext_body = _post(
        server_url,
        "/session/retext",
        {"session_zid": zid, "language": "de", "text_mode": "multi"},
        server.api_key,
    )
    assert retext_status == 200, retext_body

    status_code, status_body = _get(
        server_url,
        f"/session/status?zid={zid}",
        server.api_key,
    )
    assert status_code == 200, status_body
    status_data = _payload(status_body)

    assert status_data["text_provenance"] == "live:deepl"
    assert status_data["textProvenance"] == "live:deepl"
    assert "deepl: Ein Hund bellt." in status_data["translatedText"]
    assert "deepl: Eine Katze schlaeft." in status_data["translatedText"]

    sent_map = {s["sentence_index"]: s["sentence_destination"] for s in status_data["sentences"]}
    assert sent_map == {
        1: "deepl: Ein Hund bellt.",
        2: "deepl: Eine Katze schlaeft.",
    }

    rows = status_data["rows"]
    assert isinstance(rows, dict) and len(rows) == 2
    row_values = list(rows.values())
    assert {row.get("lemma") for row in row_values} == {"Hund", "Katze"}
    assert {row.get("trans") for row in row_values} == {"собака", "кошка"}
    assert {row.get("sentence_source") for row in row_values} == {
        "Ein Hund bellt.",
        "Eine Katze schlaeft.",
    }
    assert {row.get("sentence_idx") for row in row_values} == {"1", "2"}


def test_cold_retext_then_progressive_worker_retains_headers_text_and_language(controller_world):
    """Cold POST /session/retext followed by progressive enqueue resolves complete headers, text, and language."""
    server_url, server = controller_world
    zid = "20261122130003"
    _seed_cold_session(
        server,
        zid,
        language="en",
        target_lang="ru",
        sentences=("The fox runs.", "The owl sleeps."),
        lemmas=("fox", "owl"),
        word_destinations=("", ""),
    )
    server.config.read_dict({
        "triggers": {
            "run_text_translation": "auto",
            "run_lemma_base_translation": "auto",
            "run_lemma_enrichment": "manual",
        }
    })

    retext_status, retext_body = _post(
        server_url,
        "/session/retext",
        {"session_zid": zid, "language": "en", "text_mode": "multi"},
        server.api_key,
    )
    assert retext_status == 200, retext_body

    server.translation_service.provider_calls.clear()
    events_queue = server.arbiter.register_subscriber(zid)
    observed_events = []
    try:
        enq_status, enq_body = _post(
            server_url,
            "/session/progressive/enqueue",
            {"session_zid": zid, "target_lang": "ru", "token": server.api_key},
            server.api_key,
        )
        assert enq_status == 200, enq_body
        enq_data = _payload(enq_body)
        assert enq_data["status"] in ("queued", "in_progress")

        deadline = time.monotonic() + 10.0
        finished = None
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                event = events_queue.get(timeout=remaining)
            except queue.Empty:
                break
            observed_events.append(event)
            if event.get("stage") == "finished":
                finished = event
                break

        eq = server.arbiter.enrichment_queue
        with eq._lock:
            active_future = eq._active_progressive_sessions.get(zid)
        if active_future is not None:
            active_future.result(timeout=10)
    finally:
        server.arbiter.unregister_subscriber(zid, events_queue)

    assert finished is not None, f"Progressive worker never emitted finished event; saw: {observed_events}"

    source_events = [ev for ev in observed_events if ev.get("stage") == "source"]
    assert source_events, f"Missing source stage event; saw: {observed_events}"
    source_rows = source_events[0].get("rows", {})
    assert isinstance(source_rows, dict) and source_rows
    assert {row.get("token_order") for row in source_rows.values()} == {"0", "1"}
    assert {row.get("lemma") for row in source_rows.values()} == {"fox", "owl"}
    assert {row.get("sentence_source") for row in source_rows.values()} == {
        "The fox runs.",
        "The owl sleeps.",
    }

    assert server.translation_service.provider_calls, "Expected progressive lemma translation provider calls"
    assert all(source_lang == "en" for _, source_lang, _ in server.translation_service.provider_calls)

    cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    words = {w["lemma"]: w["word_destination"] for w in cold.db.get_words_by_session(zid)}
    assert words == {"fox": "google: fox", "owl": "google: owl"}

    sents = {s["sentence_index"]: s for s in cold.db.get_sentences_by_session(zid)}
    assert sents[1]["sentence_destination"] == "deepl: The fox runs."
    assert sents[1]["text_provenance"] == "live:deepl"
    assert sents[2]["sentence_destination"] == "deepl: The owl sleeps."
    assert sents[2]["text_provenance"] == "live:deepl"
