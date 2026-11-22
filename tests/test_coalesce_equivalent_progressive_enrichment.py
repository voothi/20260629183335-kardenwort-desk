"""Acceptance tests for coalescing equivalent in-flight progressive Stage 3 enrichment.

Spec: specs/enrichment-deduplication/spec.md
Requirement: Coalesce equivalent in-flight progressive enrichment
"""
import concurrent.futures
from pathlib import Path
import threading

import kardenwort_desk as desk
from kardenwort_controller import SessionArbiter
from tests.controller_test_support import owned_configuration
from tests.enrichment_test_support import enrichment_engine
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
