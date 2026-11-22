"""Acceptance tests for coalescing equivalent in-flight progressive Stage 3 enrichment.

Spec: specs/enrichment-deduplication/spec.md
Requirement: Coalesce equivalent in-flight progressive enrichment
"""
import ast
import concurrent.futures
import configparser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import threading

import pytest

import kardenwort_desk as desk
import kardenwort_enrichment as enrichment
import kardenwort_reword as reword
from kardenwort_controller import SessionArbiter
from tests.browser_test_server import create_browser_test_server
from tests.controller_test_support import owned_configuration
from tests.enrichment_test_support import enrichment_engine
from tests.test_intent_architecture import complexity, functions
from tests.test_structured_enrichment import control_writes, prohibited


SIBLING_A = "20261122110001"
SIBLING_B = "20261122110002"
HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
]


def _build_progressive_env(tmp_path: Path, *, max_workers: int = 2):
    config, paths, _ = owned_configuration(tmp_path, "", "sqlite")
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        + "\n".join(HEADERS)
        + "\n[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "WordSourceIPA=ipa\n"
        "WordSourceMorphologyAI=morphology\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n"
        "SentenceDestination=sentence_destination\n"
        "[desk_editable]\n"
        "editable_columns=WordDestination\n",
        encoding="utf-8",
    )
    config.read_dict({
        "settings": {
            "default_language": "de",
            "default_target_language": "ru",
            "anki_mapping_file": str(mapping_file),
        },
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "auto",
        },
        "pipeline": {
            "lemma_reprocess_provider": "intellifiller",
        },
        "languages": {
            "de_prompt": "fixture",
        },
        "intellifiller": {
            "max_workers": str(max_workers),
        },
        "classification": {
            "enabled": "false",
        },
        "wordfill": {
            "enabled": "false",
        },
    })
    paths["anki_mapping_file"] = mapping_file
    arbiter = SessionArbiter(config=config, resolved_paths=paths, wordfill_cfg={"enabled": False})
    adapter = desk.get_storage_adapter(config, paths)
    return config, paths, arbiter, adapter


def _seed_session(
    arbiter,
    adapter,
    tmp_path: Path,
    session_zid: str,
    rows,
    *,
    text: str,
    language: str = "de",
    target_lang: str = "ru",
    provenances=None,
):
    sent_idx_col = HEADERS.index("SentenceSourceIndex")
    sent_src_col = HEADERS.index("SentenceSource")
    sent_dst_col = HEADERS.index("SentenceDestination")
    sentences = []
    seen = set()
    for row in rows:
        s_idx = int(row[sent_idx_col])
        if s_idx not in seen:
            seen.add(s_idx)
            sentences.append({
                "sentence_index": s_idx,
                "sentence_source": row[sent_src_col],
                "sentence_destination": row[sent_dst_col],
                "text_provenance": "cached:sqlite",
            })
    adapter.save_session(
        session_zid=session_zid,
        slug=f"coalesce-{session_zid}",
        source_language=language,
        target_language=target_lang,
        text_mode="single",
        source_raw_text=text,
        headers=list(HEADERS),
        data_rows=[list(r) for r in rows],
        comments=[],
        working_tsv_path=tmp_path / f"{session_zid}.{language}.tsv",
        row_provenances=provenances or {i: "live:google" for i in range(len(rows))},
        sentences=sentences,
    )
    restored = adapter.restore_session(session_zid)
    arbiter.sessions[session_zid] = {
        "text": text,
        "comments": [],
        "headers": list(restored["headers"]),
        "data_rows": [list(r) for r in restored["data_rows"]],
        "language": language,
        "target_lang": target_lang,
        "text_mode": "single",
        "tsv_path": session_zid,
        "row_provenances": dict(provenances or {i: "live:google" for i in range(len(rows))}),
    }


def _cold_words_by_key(config, paths, session_zid: str):
    cold = desk.SqliteStorageAdapter(config, paths)
    words = cold.db.get_words_by_session(session_zid)
    return {(int(w["sentence_index"]), int(w["token_order"])): w for w in words}


def _drain_until_finished(subscriber, timeout: float = 15.0):
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    while not any(e.get("stage") == "finished" for e in events):
        events.append(subscriber.get(timeout=timeout))
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    return events


def _drain_available(subscriber):
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    return events


def _cleanup_arbiter(arbiter):
    try:
        arbiter.enrichment_queue.shutdown(wait=True)
    finally:
        desk.unregister_failover_callback(arbiter._on_global_failover)


def _observe_progressive_future_waits(monkeypatch, condition):
    """Observe real Future.result waits from ProgressiveWorker threads without altering behavior."""
    waiters = set()
    original = concurrent.futures.Future.result

    def observed(future, *args, **kwargs):
        thread = threading.current_thread()
        if thread.name.startswith("ProgressiveWorker"):
            with condition:
                waiters.add(thread.ident)
                condition.notify_all()
        return original(future, *args, **kwargs)

    monkeypatch.setattr(concurrent.futures.Future, "result", observed)
    return waiters


class _HeldErrorEnrichmentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/enrich"
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.server.changed:
            self.server.requests.append(payload)
            self.server.changed.notify_all()
        assert self.server.release.wait(5), "Error enrichment engine was not released"
        body = json.dumps({"status": "error", "code": "ERR_LLM_REQUEST", "message": "fixture failure"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def _held_failing_enrichment_engine(config):
    server = create_browser_test_server(_HeldErrorEnrichmentHandler)
    server.requests = []
    server.changed = threading.Condition()
    server.release = threading.Event()
    config.set("services", "intellifiller_server_url", f"http://127.0.0.1:{server.server_port}")
    try:
        server.start()
        yield server
    finally:
        server.release.set()
        server.server_close()


def test_equivalent_siblings_coalesce_in_flight_stage3_enrichment(tmp_path, monkeypatch):
    """Scenario: Equivalent siblings — two automatic sibling workers share one in-flight provider execution."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.")

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    enq_a = arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    assert enq_a["status"] == "queued"
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)

                    # Deterministically hold sibling A's real HTTP request in flight before sibling B starts.
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    enq_b = arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    assert enq_b["status"] == "queued"
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)

                    # Rendezvous: either unrepaired duplicate dispatch (2 HTTP requests) or coalesced wait (2 waiters).
                    with engine.changed:
                        assert engine.changed.wait_for(
                            lambda: len(engine.requests) >= 2
                            or (len(engine.requests) == 1 and len(progressive_waiters) >= 2),
                            5,
                        ), "Second sibling worker did not reach Stage 3 in-flight rendezvous"
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)

                events_a = _drain_until_finished(sub_a)
                events_b = _drain_until_finished(sub_b)

            assert len(engine.requests) == 1, (
                f"Expected 1 coalesced provider execution for equivalent siblings, got {len(engine.requests)}"
            )
            assert [row["WordSource"] for row in engine.requests[0]["rows"]] == ["Haus"]

        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        fin_b = next(e for e in reversed(events_b) if e.get("stage") == "finished")
        assert fin_a["status"] == "success" and fin_a["error"] is None
        assert fin_b["status"] == "success" and fin_b["error"] is None

        cold_a = _cold_words_by_key(config, paths, SIBLING_A)
        cold_b = _cold_words_by_key(config, paths, SIBLING_B)
        for cold in (cold_a, cold_b):
            assert cold[(1, 0)]["word_destination"] == "trans_Haus"
            assert cold[(1, 0)]["ipa"] == "/ipa_Haus/"
            assert cold[(1, 0)]["morphology"] == "morph_Haus"
            assert cold[(1, 0)]["word_provenance"] == "live:intellifiller"

        assert adapter.db.get_worker_status(SIBLING_A)["worker_status"] == "finished"
        assert adapter.db.get_worker_status(SIBLING_B)["worker_status"] == "finished"
        assert arbiter.enrichment_queue._inflight_progressive_batches == {}
        assert arbiter.enrichment_queue._cache == {}
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_equivalent_siblings_fan_out_to_distinct_exact_occurrences_and_preserve_unrelated_rows(tmp_path, monkeypatch):
    """Equivalent batches map validated semantic fields onto each subscriber's own exact (sentence, token) coordinates."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows_a = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
        ["1", "Baum", "Baum", "tree", "/baum/", "noun", "1", "Das Haus.", "Дом."],
    ]
    rows_b = [
        ["0", "Wald", "Wald", "forest", "/wald/", "noun", "1", "Der Wald.", "Лес."],
        ["3", "Haus", "Haus", "дом", "", "", "2", "Das Haus.", "Дом."],
    ]
    _seed_session(
        arbiter, adapter, tmp_path, SIBLING_A, rows_a, text="Das Haus.",
        provenances={0: "live:google", 1: "fixture:initial"},
    )
    _seed_session(
        arbiter, adapter, tmp_path, SIBLING_B, rows_b, text="Der Wald. Das Haus.",
        provenances={0: "fixture:initial", 1: "live:google"},
    )
    before_a_baum = _cold_words_by_key(config, paths, SIBLING_A)[(1, 1)]
    before_b_wald = _cold_words_by_key(config, paths, SIBLING_B)[(1, 0)]

    def echo_leader_identity_response(batch_rows):
        return [
            {
                "row_id": row["row_id"],
                "TokenOrder": str(row["TokenOrder"]),
                "SentenceSourceIndex": str(row["SentenceSourceIndex"]),
                "WordDestination": "trans_" + row["WordSource"],
                "WordSourceIPA": "/ipa_" + row["WordSource"] + "/",
                "WordSourceMorphologyAI": "morph_" + row["WordSource"],
            }
            for row in batch_rows
        ]

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config, response=echo_leader_identity_response) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(
                            lambda: len(engine.requests) == 1 and len(progressive_waiters) >= 2, 5,
                        )
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)
                _drain_until_finished(sub_a)
                _drain_until_finished(sub_b)

            assert len(engine.requests) == 1

        cold_a = _cold_words_by_key(config, paths, SIBLING_A)
        cold_b = _cold_words_by_key(config, paths, SIBLING_B)
        assert cold_a[(1, 0)]["word_destination"] == "trans_Haus"
        assert cold_a[(1, 0)]["ipa"] == "/ipa_Haus/"
        assert cold_a[(1, 0)]["word_provenance"] == "live:intellifiller"
        assert cold_a[(1, 1)] == before_a_baum

        assert cold_b[(2, 3)]["word_destination"] == "trans_Haus"
        assert cold_b[(2, 3)]["ipa"] == "/ipa_Haus/"
        assert cold_b[(2, 3)]["word_provenance"] == "live:intellifiller"
        assert cold_b[(1, 0)] == before_b_wald
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_different_context_requests_stay_separate_without_cross_context_leak(tmp_path):
    """Scenario: Different context — equal lemmas in distinct sentences never coalesce or leak translations."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows_a = [
        ["0", "Bank", "Bank", "", "", "", "1", "Ich sitze auf der Bank.", "I sit on the bench."],
    ]
    rows_b = [
        ["0", "Bank", "Bank", "", "", "", "1", "Ich gehe zur Bank.", "I go to the bank."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows_a, text="Ich sitze auf der Bank.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows_b, text="Ich gehe zur Bank.")

    def context_sensitive_response(batch_rows):
        out = []
        for row in batch_rows:
            if "sitze" in row["SentenceSource"]:
                out.append({
                    "row_id": row["row_id"],
                    "WordDestination": "bench",
                    "WordSourceIPA": "/baŋk_seat/",
                    "WordSourceMorphologyAI": "noun_bench",
                })
            else:
                out.append({
                    "row_id": row["row_id"],
                    "WordDestination": "financial_bank",
                    "WordSourceIPA": "/baŋk_fin/",
                    "WordSourceMorphologyAI": "noun_finance",
                })
        return out

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config, response=context_sensitive_response) as engine:
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 2, 5), (
                            "Distinct source contexts must remain separate provider requests"
                        )
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)
                _drain_until_finished(sub_a)
                _drain_until_finished(sub_b)

            assert len(engine.requests) == 2

        cold_a = _cold_words_by_key(config, paths, SIBLING_A)
        cold_b = _cold_words_by_key(config, paths, SIBLING_B)
        assert cold_a[(1, 0)]["word_destination"] == "bench"
        assert cold_a[(1, 0)]["morphology"] == "noun_bench"
        assert cold_b[(1, 0)]["word_destination"] == "financial_bank"
        assert cold_b[(1, 0)]["morphology"] == "noun_finance"
        assert arbiter.enrichment_queue._cache == {}
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_different_prompt_or_target_language_requests_stay_separate(tmp_path):
    """Scenario: Different context — equal lemma and sentence with different prompt or target language do not merge."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "", "", "", "1", "Das Haus.", ""],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.", target_lang="ru")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.", target_lang="en")

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config) as engine:
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="prompt_ru",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="en", prompt_name="prompt_en",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 2, 5)
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)
                _drain_until_finished(sub_a)
                _drain_until_finished(sub_b)

            assert {req["prompt"] for req in engine.requests} == {"prompt_ru", "prompt_en"}
            assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


@pytest.mark.parametrize("delete_mode", ["soft", "hard"])
def test_one_subscriber_deleted_mid_flight_remaining_sibling_commits_and_deleted_cannot_write(
    tmp_path, monkeypatch, delete_mode,
):
    """Scenario: One subscriber disappears (deleted) — survivor commits; deleted leader cannot write or succeed."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.")

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(
                            lambda: len(engine.requests) == 1 and len(progressive_waiters) >= 2, 5,
                        )

                    if delete_mode == "soft":
                        assert adapter.db.soft_delete_session(SIBLING_A) is True
                    else:
                        assert adapter.db.delete_session(SIBLING_A) is True
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)

                events_a = _drain_until_finished(sub_a)
                events_b = _drain_until_finished(sub_b)

            assert len(engine.requests) == 1

        # Remaining subscriber B receives and commits the shared result cleanly.
        fin_b = next(e for e in reversed(events_b) if e.get("stage") == "finished")
        assert fin_b["status"] == "success" and fin_b["error"] is None
        cold_b = _cold_words_by_key(config, paths, SIBLING_B)
        assert cold_b[(1, 0)]["word_destination"] == "trans_Haus"
        assert cold_b[(1, 0)]["ipa"] == "/ipa_Haus/"
        assert cold_b[(1, 0)]["morphology"] == "morph_Haus"
        assert cold_b[(1, 0)]["word_provenance"] == "live:intellifiller"
        assert adapter.db.get_worker_status(SIBLING_B)["worker_status"] == "finished"

        # Obsolete deleted subscriber A cannot write enrichment or publish success.
        assert not [e for e in events_a if e.get("stage") == "enrichment"]
        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        assert fin_a["status"] == "failed"
        assert fin_a["rows"] == {}
        assert SIBLING_A not in arbiter.sessions
        if delete_mode == "soft":
            cold_a = _cold_words_by_key(config, paths, SIBLING_A)
            assert cold_a[(1, 0)]["word_destination"] == "дом"
            assert not (cold_a[(1, 0)]["ipa"] or "")
            assert not (cold_a[(1, 0)]["morphology"] or "")
            assert cold_a[(1, 0)]["word_provenance"] == "live:google"
            assert adapter.db.get_worker_status(SIBLING_A)["worker_status"] == "failed"
        else:
            assert adapter.db.get_session(SIBLING_A, include_deleted=True) is None
        assert arbiter.enrichment_queue._inflight_progressive_batches == {}
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_one_subscriber_superseded_mid_flight_remaining_sibling_commits_and_superseded_is_silent(tmp_path, monkeypatch):
    """Scenario: One subscriber disappears (superseded) — survivor commits; superseded leader writes/publishes nothing."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.")

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(
                            lambda: len(engine.requests) == 1 and len(progressive_waiters) >= 2, 5,
                        )

                    # Supersede leader A's generation while the shared provider call is still in flight.
                    with arbiter.enrichment_queue._lock:
                        arbiter.enrichment_queue._progressive_generations[SIBLING_A] += 1
                    _drain_available(sub_a)
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)

                events_b = _drain_until_finished(sub_b)
                events_a_after = _drain_available(sub_a)

            assert len(engine.requests) == 1

        # Survivor B succeeds and commits cleanly.
        fin_b = next(e for e in reversed(events_b) if e.get("stage") == "finished")
        assert fin_b["status"] == "success" and fin_b["error"] is None
        cold_b = _cold_words_by_key(config, paths, SIBLING_B)
        assert cold_b[(1, 0)]["word_destination"] == "trans_Haus"
        assert cold_b[(1, 0)]["ipa"] == "/ipa_Haus/"
        assert cold_b[(1, 0)]["word_provenance"] == "live:intellifiller"

        # Superseded A neither commits to SQLite nor publishes stale enrichment/finished events.
        assert not [e for e in events_a_after if e.get("stage") in ("enrichment", "finished")]
        cold_a = _cold_words_by_key(config, paths, SIBLING_A)
        assert cold_a[(1, 0)]["word_destination"] == "дом"
        assert not (cold_a[(1, 0)]["ipa"] or "")
        assert not (cold_a[(1, 0)]["morphology"] or "")
        assert cold_a[(1, 0)]["word_provenance"] == "live:google"
        assert arbiter.enrichment_queue._inflight_progressive_batches == {}
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_per_session_commit_failure_is_isolated_from_remaining_sibling(tmp_path, monkeypatch):
    """Per-session SQLite commit failure in sibling A fails A explicitly without impairing sibling B."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.")

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(
                            lambda: len(engine.requests) == 1 and len(progressive_waiters) >= 2, 5,
                        )

                    # Remove sibling A's word row while keeping its session active so A's commit fails explicitly.
                    with adapter.db.transaction(zid=SIBLING_A) as conn:
                        conn.execute("DELETE FROM words WHERE session_zid = ?", (SIBLING_A,))
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)

                events_a = _drain_until_finished(sub_a)
                events_b = _drain_until_finished(sub_b)

            assert len(engine.requests) == 1

        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        assert fin_a["status"] == "failed"
        assert isinstance(fin_a.get("error"), dict)
        assert adapter.db.get_worker_status(SIBLING_A)["worker_status"] == "failed"

        fin_b = next(e for e in reversed(events_b) if e.get("stage") == "finished")
        assert fin_b["status"] == "success" and fin_b["error"] is None
        cold_b = _cold_words_by_key(config, paths, SIBLING_B)
        assert cold_b[(1, 0)]["word_destination"] == "trans_Haus"
        assert cold_b[(1, 0)]["ipa"] == "/ipa_Haus/"
        assert cold_b[(1, 0)]["word_provenance"] == "live:intellifiller"
        assert adapter.db.get_worker_status(SIBLING_B)["worker_status"] == "finished"
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_shared_provider_failure_retires_inflight_entry_and_preserves_state(tmp_path, monkeypatch):
    """Provider failure fails both coalesced subscribers explicitly, preserves SQLite, and retires the entry."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.")
    before_a = _cold_words_by_key(config, paths, SIBLING_A)
    before_b = _cold_words_by_key(config, paths, SIBLING_B)

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with _held_failing_enrichment_engine(config) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(
                            lambda: len(engine.requests) == 1 and len(progressive_waiters) >= 2, 5,
                        )
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)

                events_a = _drain_until_finished(sub_a)
                events_b = _drain_until_finished(sub_b)

            assert len(engine.requests) == 1

        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        fin_b = next(e for e in reversed(events_b) if e.get("stage") == "finished")
        assert fin_a["status"] == "failed" and isinstance(fin_a.get("error"), dict)
        assert fin_b["status"] == "failed" and isinstance(fin_b.get("error"), dict)
        assert _cold_words_by_key(config, paths, SIBLING_A) == before_a
        assert _cold_words_by_key(config, paths, SIBLING_B) == before_b
        assert arbiter.enrichment_queue._inflight_progressive_batches == {}

        # Retired failed entry allows a subsequent retry against a healthy provider to succeed.
        with enrichment_engine(config) as healthy:
            healthy.release.set()
            arbiter.enrichment_queue.enqueue_progressive_task(
                SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
            )
            with arbiter.enrichment_queue._lock:
                fut_retry = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
            if fut_retry is not None:
                fut_retry.result(timeout=15)
            retry_events = _drain_until_finished(sub_a)
            assert len(healthy.requests) == 1
        fin_retry = next(e for e in reversed(retry_events) if e.get("stage") == "finished")
        assert fin_retry["status"] == "success" and fin_retry["error"] is None
        assert _cold_words_by_key(config, paths, SIBLING_A)[(1, 0)]["word_destination"] == "trans_Haus"
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_bounded_concurrency_and_queued_superseded_batch_cancellation(tmp_path, monkeypatch):
    """Queue max_workers=1 bounds concurrent Stage 3 execution and cancels queued batch if sole subscriber is superseded."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=1)
    rows_a = [["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."]]
    rows_b = [["0", "Baum", "Baum", "дерево", "", "", "1", "Der Baum.", "Дерево."]]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows_a, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows_b, text="Der Baum.")

    sub_a = arbiter.register_subscriber(SIBLING_A)
    sub_b = arbiter.register_subscriber(SIBLING_B)
    try:
        with enrichment_engine(config) as engine:
            progressive_waiters = _observe_progressive_future_waits(monkeypatch, engine.changed)
            with control_writes() as writes:
                try:
                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_B, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    with arbiter.enrichment_queue._lock:
                        fut_b = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_B)
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(progressive_waiters) >= 2, 5)

                    # max_workers=1 holds B queued behind A (only 1 active HTTP call).
                    assert engine.maximum_active == 1
                    assert len(engine.requests) == 1

                    # Supersede B before its queued batch starts executing on _executor.
                    with arbiter.enrichment_queue._lock:
                        arbiter.enrichment_queue._progressive_generations[SIBLING_B] += 1
                    _drain_available(sub_b)
                finally:
                    engine.release.set()

                if fut_a is not None:
                    fut_a.result(timeout=15)
                if fut_b is not None:
                    fut_b.result(timeout=15)

                events_a = _drain_until_finished(sub_a)
                events_b_after = _drain_available(sub_b)

            assert engine.maximum_active == 1
            assert len(engine.requests) == 1, "Superseded queued batch must cancel before calling provider"

        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        assert fin_a["status"] == "success"
        assert _cold_words_by_key(config, paths, SIBLING_A)[(1, 0)]["word_destination"] == "trans_Haus"
        assert not [e for e in events_b_after if e.get("stage") in ("enrichment", "finished")]
        assert _cold_words_by_key(config, paths, SIBLING_B)[(1, 0)]["word_destination"] == "дерево"
        assert arbiter.enrichment_queue._inflight_progressive_batches == {}
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        arbiter.unregister_subscriber(SIBLING_B, sub_b)
        _cleanup_arbiter(arbiter)


def test_single_automatic_progressive_stage3_enrichment_control(tmp_path):
    """Valid adjacent control: single automatic Stage 3 session enriches cleanly and commits exact occurrence."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=1)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    sub_a = arbiter.register_subscriber(SIBLING_A)
    try:
        with enrichment_engine(config) as engine:
            engine.release.set()
            with control_writes() as writes:
                enq_a = arbiter.enrichment_queue.enqueue_progressive_task(
                    SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                )
                assert enq_a["status"] == "queued"
                with arbiter.enrichment_queue._lock:
                    fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)
                if fut_a is not None:
                    fut_a.result(timeout=15)
                events_a = _drain_until_finished(sub_a)

            assert len(engine.requests) == 1
            assert [row["WordSource"] for row in engine.requests[0]["rows"]] == ["Haus"]

        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        assert fin_a["status"] == "success" and fin_a["error"] is None
        cold_a = _cold_words_by_key(config, paths, SIBLING_A)
        assert cold_a[(1, 0)]["word_destination"] == "trans_Haus"
        assert cold_a[(1, 0)]["ipa"] == "/ipa_Haus/"
        assert cold_a[(1, 0)]["morphology"] == "morph_Haus"
        assert cold_a[(1, 0)]["word_provenance"] == "live:intellifiller"
        assert adapter.db.get_worker_status(SIBLING_A)["worker_status"] == "finished"
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        _cleanup_arbiter(arbiter)


def test_explicit_reword_does_not_coalesce_with_in_flight_automatic_stage3_control(tmp_path):
    """Valid adjacent control: explicit user Re-word stays separate from in-flight automatic Stage 3."""
    config, paths, arbiter, adapter = _build_progressive_env(tmp_path, max_workers=2)
    rows = [
        ["0", "Haus", "Haus", "дом", "", "", "1", "Das Haus.", "Дом."],
    ]
    _seed_session(arbiter, adapter, tmp_path, SIBLING_A, rows, text="Das Haus.")
    _seed_session(arbiter, adapter, tmp_path, SIBLING_B, rows, text="Das Haus.")
    sub_a = arbiter.register_subscriber(SIBLING_A)
    try:
        with enrichment_engine(config) as engine:
            with control_writes() as writes:
                reword_errors = []
                reword_result = []

                def run_explicit_reword():
                    try:
                        reword_result.append(
                            arbiter.reword_session(
                                session_zid=SIBLING_B,
                                selected_rows=[0],
                                prompt="fixture",
                                language="de",
                                sentence_idx=1,
                            )
                        )
                    except BaseException as exc:
                        reword_errors.append(exc)

                reword_thread = threading.Thread(target=run_explicit_reword)
                try:
                    enq_a = arbiter.enrichment_queue.enqueue_progressive_task(
                        SIBLING_A, arbiter, language="de", target_lang="ru", prompt_name="fixture",
                    )
                    assert enq_a["status"] == "queued"
                    with arbiter.enrichment_queue._lock:
                        fut_a = arbiter.enrichment_queue._active_progressive_sessions.get(SIBLING_A)

                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)

                    reword_thread.start()
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 2, 5), (
                            "Explicit Re-word must issue its own provider request instead of merging with automatic Stage 3"
                        )
                finally:
                    engine.release.set()
                    reword_thread.join(timeout=15)

                if fut_a is not None:
                    fut_a.result(timeout=15)
                events_a = _drain_until_finished(sub_a)

            assert reword_errors == []
            assert len(reword_result) == 1
            assert len(engine.requests) == 2

        fin_a = next(e for e in reversed(events_a) if e.get("stage") == "finished")
        assert fin_a["status"] == "success" and fin_a["error"] is None
        for zid in (SIBLING_A, SIBLING_B):
            cold = _cold_words_by_key(config, paths, zid)
            assert cold[(1, 0)]["word_destination"] == "trans_Haus"
            assert cold[(1, 0)]["ipa"] == "/ipa_Haus/"
            assert cold[(1, 0)]["morphology"] == "morph_Haus"
            assert cold[(1, 0)]["word_provenance"] == "live:intellifiller"
        assert prohibited(writes) == []
    finally:
        arbiter.unregister_subscriber(SIBLING_A, sub_a)
        _cleanup_arbiter(arbiter)


def test_pure_progressive_request_key_distinguishes_all_meaning_affecting_inputs():
    """Pure functional core: equivalence key includes context, prompt, provider config, roles, and languages."""
    def make_config(**overrides):
        cfg = configparser.ConfigParser()
        cfg.read_dict({
            "services": {"intellifiller_server_url": overrides.get("url", "http://127.0.0.1:8083")},
            "intellifiller": {
                "model": overrides.get("model", "gpt-4o-mini"),
                "base_url": overrides.get("base_url", "https://api.example/v1"),
                "api_key": overrides.get("api_key", "key-1"),
                "prompt_template": overrides.get("prompt_template", "tmpl-1"),
                "temperature": overrides.get("temperature", "0.2"),
            },
        })
        return cfg

    roles = {"lemma": "WordSource", "word_translation": "WordDestination", "sentence_index": "SentenceSourceIndex"}
    row_a = {"row_id": 0, "TokenOrder": "0", "SentenceSourceIndex": "1", "WordSource": "Bank", "SentenceSource": "Auf der Bank."}
    row_b = {"row_id": 4, "TokenOrder": "9", "SentenceSourceIndex": "3", "WordSource": "Bank", "SentenceSource": "Auf der Bank."}

    base = enrichment.progressive_request_key([row_a], "p1", "de", "ru", make_config(), roles)
    same_different_coords = enrichment.progressive_request_key([row_b], "p1", "de", "ru", make_config(), roles)
    assert base == same_different_coords

    assert base != enrichment.progressive_request_key(
        [dict(row_a, SentenceSource="Zur Bank.")], "p1", "de", "ru", make_config(), roles,
    )
    assert base != enrichment.progressive_request_key([row_a], "p2", "de", "ru", make_config(), roles)
    assert base != enrichment.progressive_request_key([row_a], "p1", "en", "ru", make_config(), roles)
    assert base != enrichment.progressive_request_key([row_a], "p1", "de", "en", make_config(), roles)
    assert base != enrichment.progressive_request_key([row_a], "p1", "de", "ru", make_config(url="http://127.0.0.1:9999"), roles)
    assert base != enrichment.progressive_request_key([row_a], "p1", "de", "ru", make_config(model="other-model"), roles)
    assert base != enrichment.progressive_request_key([row_a], "p1", "de", "ru", make_config(temperature="0.7"), roles)
    assert base != enrichment.progressive_request_key([row_a], "p1", "de", "ru", make_config(prompt_template="tmpl-2"), roles)
    assert base != enrichment.progressive_request_key(
        [row_a], "p1", "de", "ru", make_config(), dict(roles, word_translation="WordRussian"),
    )


def test_pure_subscriber_changes_strips_identity_fields_and_sanitizes():
    """Pure functional core: subscriber_changes maps patches onto subscriber keys without leaking coordinates."""
    selection = reword.RewordSelection(keys=((2, 7),), rows=(1,), visuals=())
    roles = {"sentence_index": "SentenceSourceIndex", "sentence": "SentenceSource"}
    patches = {
        0: {
            "TokenOrder": "0",
            "SentenceSourceIndex": "1",
            "SentenceSource": "Injected",
            "WordDestination": "[house]",
            "WordSourceIPA": "/haus/",
            "extra_fields": "ignored",
        }
    }
    changes = enrichment.subscriber_changes(selection, roles, patches, sanitize=desk.sanitize_bracketed_field)
    assert changes == {
        (2, 7): {
            "WordDestination": "house",
            "WordSourceIPA": "/haus/",
            "word_provenance": "live:intellifiller",
        }
    }


def test_coalescing_boundary_architecture_and_complexity_ceiling():
    """Architectural fitness: pure enrichment core has zero shell imports and all new helpers meet CC <= 7."""
    root = Path(__file__).resolve().parents[1]

    enrichment_tree = ast.parse((root / "kardenwort_enrichment.py").read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(enrichment_tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not imports.intersection({"kardenwort_desk", "kardenwort_controller", "http_server", "kardenwort_db"})
    enrichment_violations = {node.name: complexity(node) for node in functions(enrichment_tree) if complexity(node) > 7}
    assert enrichment_violations == {}

    controller_tree = ast.parse((root / "kardenwort_controller.py").read_text(encoding="utf-8"))
    new_names = {
        "_has_active_batch_subscriber",
        "_retire_progressive_batch",
        "_acquire_progressive_batch_future",
        "_execute_progressive_batch",
        "enrich_progressive_batch",
        "_commit_progressive_enrichment_sqlite",
        "_prepare_progressive_sqlite_batch",
        "_reword_progressive_sqlite",
        "_can_publish_progressive_reword",
        "_cache_reword_session_state",
        "_store_reword_session_locked",
    }
    measured = {node.name: complexity(node) for node in functions(controller_tree) if node.name in new_names}
    assert set(measured) == new_names
    assert max(measured.values()) <= 7, measured
