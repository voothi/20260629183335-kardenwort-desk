"""Real-SQLite + owned-HTTP-provider acceptance tests for cold Stage 3 enrichment targeting.

Spec: specs/reword-target-disambiguation/spec.md
Requirement: Cold progressive Stage 3 enrichment uses immutable occurrence coordinates
"""
from pathlib import Path

import pytest

import kardenwort_desk as desk
import kardenwort_reword as reword
from kardenwort_controller import SessionArbiter
from tests.controller_test_support import owned_configuration
from tests.enrichment_test_support import enrichment_engine
from tests.test_structured_enrichment import control_writes, prohibited


COLD_ZID = "20261122141501"
MULTI_SENT_ZID = "20261122141502"
RESOLVER_ZID = "20261122141503"

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


def _build_stage3_env(tmp_path: Path, frequency_words: tuple[str, ...] = ("Alpha", "Zebra")):
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
    freq_file = tmp_path / "de_frequency.txt"
    freq_file.write_text("\n".join(frequency_words) + "\n", encoding="utf-8")

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
            "de_lemma_index": freq_file.name,
        },
        "intellifiller": {
            "max_workers": "1",
        },
        "classification": {
            "enabled": "false",
        },
        "wordfill": {
            "enabled": "false",
        },
        "environment": {
            "kardenwort_workspace": str(tmp_path),
        },
    })
    paths["anki_mapping_file"] = mapping_file
    paths["kardenwort_workspace"] = tmp_path

    arbiter = SessionArbiter(config=config, resolved_paths=paths, wordfill_cfg={"enabled": False})
    adapter = desk.get_storage_adapter(config, paths)
    return config, paths, arbiter, adapter


def _seed_sqlite_session(
    adapter,
    tmp_path: Path,
    session_zid: str,
    rows: list[list[str]],
    *,
    text: str,
    text_mode: str = "single",
    provenances: dict[int, str] | None = None,
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
        slug=f"cold-stage3-{session_zid}",
        source_language="de",
        target_language="ru",
        text_mode=text_mode,
        source_raw_text=text,
        headers=list(HEADERS),
        data_rows=[list(r) for r in rows],
        comments=[],
        working_tsv_path=tmp_path / f"{session_zid}.de.tsv",
        row_provenances=provenances or {i: "live:google" for i in range(len(rows))},
        sentences=sentences,
    )


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


def _run_progressive_and_wait(arbiter, session_zid: str, *, text_mode: str = "single"):
    subscriber = arbiter.register_subscriber(session_zid)
    try:
        enqueued = arbiter.enrichment_queue.enqueue_progressive_task(
            session_zid,
            arbiter,
            language="de",
            target_lang="ru",
            text_mode=text_mode,
            prompt_name="fixture",
        )
        assert enqueued["status"] == "queued"
        with arbiter.enrichment_queue._lock:
            fut = arbiter.enrichment_queue._active_progressive_sessions.get(session_zid)
        if fut is not None:
            fut.result(timeout=15)
        return _drain_until_finished(subscriber)
    finally:
        arbiter.unregister_subscriber(session_zid, subscriber)


def test_cold_stage3_frequency_reordered_enriches_alpha_and_leaves_zebra_unchanged(tmp_path):
    """Cold frequency-reordered session: Stage 3 must enrich Alpha (1, 1) and never Zebra (1, 0)."""
    config, paths, arbiter, adapter = _build_stage3_env(tmp_path, frequency_words=("Alpha", "Zebra"))
    rows = [
        ["0", "Zebra", "Zebra", "зебра", "/ˈtseːbʁa/", "noun_zebra", "1", "Zebra und Alpha.", "Зебра и Альфа."],
        ["1", "Alpha", "Alpha", "альфа", "", "", "1", "Zebra und Alpha.", "Зебра и Альфа."],
    ]
    _seed_sqlite_session(
        adapter,
        tmp_path,
        COLD_ZID,
        rows,
        text="Zebra und Alpha.",
        provenances={0: "fixture:initial", 1: "live:google"},
    )
    assert COLD_ZID not in arbiter.sessions

    # Confirm precondition: SQLite storage order is [Zebra, Alpha], frequency order is [Alpha, Zebra].
    restored = adapter.restore_session(COLD_ZID)
    restored_headers, restored_rows = reword.restored_manifest_rows(restored)
    mapping = desk.load_anki_mapping(paths["anki_mapping_file"])
    roles = desk.get_role_fields(mapping, restored_headers)
    lemma_col = restored_headers.index(roles["lemma"])
    assert [r[lemma_col] for r in restored_rows] == ["Zebra", "Alpha"]
    sorted_rows = desk.sort_session_data_rows(restored_rows, restored_headers, "de", config, paths, role_fields=roles)
    assert [r[lemma_col] for r in sorted_rows] == ["Alpha", "Zebra"]

    before = _cold_words_by_key(config, paths, COLD_ZID)
    try:
        with enrichment_engine(config) as engine:
            engine.release.set()
            with control_writes() as writes:
                events = _run_progressive_and_wait(arbiter, COLD_ZID)

        finished = next(e for e in reversed(events) if e.get("stage") == "finished")
        assert finished["status"] == "success" and finished["error"] is None
        assert len(engine.requests) == 1
        dispatched = [row["WordSource"] for row in engine.requests[0]["rows"]]
        assert dispatched == ["Alpha"], (
            f"Cold Stage 3 dispatched wrong row(s) under frequency reordering: {dispatched}"
        )

        after = _cold_words_by_key(config, paths, COLD_ZID)
        assert after[(1, 1)]["word_destination"] == "trans_Alpha"
        assert after[(1, 1)]["ipa"] == "/ipa_Alpha/"
        assert after[(1, 1)]["morphology"] == "morph_Alpha"
        assert after[(1, 1)]["word_provenance"] == "live:intellifiller"
        assert after[(1, 0)] == before[(1, 0)]
        assert prohibited(writes) == []
    finally:
        _cleanup_arbiter(arbiter)


def test_cold_stage3_multi_sentence_repeated_token_order_enriches_only_sentence_two(tmp_path):
    """Cold multi-sentence session with repeated local token_order=0 enriches only (2, 0)."""
    config, paths, arbiter, adapter = _build_stage3_env(tmp_path, frequency_words=("Alpha", "Zebra"))
    rows = [
        ["0", "Zebra", "Zebra", "зебра", "/ˈtseːbʁa/", "noun_zebra", "1", "Zebra.", "Зебра."],
        ["0", "Alpha", "Alpha", "альфа", "", "", "2", "Alpha.", "Альфа."],
    ]
    _seed_sqlite_session(
        adapter,
        tmp_path,
        MULTI_SENT_ZID,
        rows,
        text="Zebra.\nAlpha.",
        text_mode="multi",
        provenances={0: "fixture:initial", 1: "live:google"},
    )
    assert MULTI_SENT_ZID not in arbiter.sessions

    before = _cold_words_by_key(config, paths, MULTI_SENT_ZID)
    try:
        with enrichment_engine(config) as engine:
            engine.release.set()
            with control_writes() as writes:
                events = _run_progressive_and_wait(arbiter, MULTI_SENT_ZID, text_mode="multi")

        finished = next(e for e in reversed(events) if e.get("stage") == "finished")
        assert finished["status"] == "success" and finished["error"] is None
        assert len(engine.requests) == 1
        dispatched = [row["WordSource"] for row in engine.requests[0]["rows"]]
        assert dispatched == ["Alpha"], (
            f"Cold multi-sentence Stage 3 dispatched wrong row(s): {dispatched}"
        )

        after = _cold_words_by_key(config, paths, MULTI_SENT_ZID)
        assert after[(2, 0)]["word_destination"] == "trans_Alpha"
        assert after[(2, 0)]["ipa"] == "/ipa_Alpha/"
        assert after[(2, 0)]["morphology"] == "morph_Alpha"
        assert after[(2, 0)]["word_provenance"] == "live:intellifiller"
        assert after[(1, 0)] == before[(1, 0)]
        assert prohibited(writes) == []
    finally:
        _cleanup_arbiter(arbiter)


def test_real_descriptor_resolver_accepts_sentence_idx_and_rejects_sentence_index(tmp_path):
    """Receiver contract control: stored SQLite column is sentence_index; transport requires sentence_idx."""
    config, paths, arbiter, adapter = _build_stage3_env(tmp_path, frequency_words=("Alpha", "Zebra"))
    rows = [
        ["0", "Zebra", "Zebra", "зебра", "/ˈtseːbʁa/", "noun_zebra", "1", "Zebra.", "Зебра."],
        ["0", "Alpha", "Alpha", "альфа", "", "", "2", "Alpha.", "Альфа."],
    ]
    _seed_sqlite_session(
        adapter,
        tmp_path,
        RESOLVER_ZID,
        rows,
        text="Zebra.\nAlpha.",
        text_mode="multi",
        provenances={0: "fixture:initial", 1: "live:google"},
    )
    try:
        # 1. Confirm SQLite persistence schema retains internal column name `sentence_index`.
        stored_words = adapter.db.get_words_by_session(RESOLVER_ZID)
        assert all("sentence_index" in w and "sentence_idx" not in w for w in stored_words)
        assert [(w["sentence_index"], w["token_order"], w["lemma"]) for w in stored_words] == [
            (1, 0, "Zebra"),
            (2, 0, "Alpha"),
        ]

        restored = adapter.restore_session(RESOLVER_ZID)
        headers, manifest_rows = reword.restored_manifest_rows(restored)
        mapping = desk.load_anki_mapping(paths["anki_mapping_file"])
        roles = desk.get_role_fields(mapping, headers)
        sorted_rows = desk.sort_session_data_rows(manifest_rows, headers, "de", config, paths, role_fields=roles)
        source_text = restored.get("source_text", "")
        lemma_col = headers.index(roles["lemma"])
        assert [r[lemma_col] for r in sorted_rows] == ["Alpha", "Zebra"]

        # 2. Adapted transport descriptor (`sentence_idx`) is accepted by the real receiver & resolver.
        valid_target = [{"session_zid": RESOLVER_ZID, "sentence_idx": 2, "token_order": 0}]
        reword.validate_session_targets(RESOLVER_ZID, valid_target)
        selection = desk.resolve_reword_request(
            sorted_rows, headers, roles, source_text, config, targets=valid_target
        )
        assert selection.keys == ((2, 0),)
        assert [sorted_rows[idx][lemma_col] for idx in selection.rows] == ["Alpha"]

        # 3. Retained negative control: unadapted internal field name (`sentence_index`) is rejected.
        rejected_target = [{"session_zid": RESOLVER_ZID, "sentence_index": 2, "token_order": 0}]
        with pytest.raises(reword.InvalidRewordTarget):
            reword.validate_session_targets(RESOLVER_ZID, rejected_target)
        with pytest.raises(desk.StructuredError) as exc_info:
            desk.resolve_reword_request(
                sorted_rows, headers, roles, source_text, config, targets=rejected_target
            )
        assert exc_info.value.error_code == desk.ErrorCode.INVALID_PAYLOAD

        # 4. Foreign session_zid is also rejected by the real target validator.
        foreign_target = [{"session_zid": "20261122999999", "sentence_idx": 2, "token_order": 0}]
        with pytest.raises(reword.InvalidRewordTarget):
            reword.validate_session_targets(RESOLVER_ZID, foreign_target)
    finally:
        _cleanup_arbiter(arbiter)
