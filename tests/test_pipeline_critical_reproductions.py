"""Phase 1 isolated reproductions for PIPE-C3, PIPE-C4, and PIPE-C5."""
from argparse import Namespace
from pathlib import Path
import json

import pytest

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from tests.controller_test_support import owned_configuration
from tests.enrichment_test_support import enrichment_engine
from tests.sqlite_document_support import island


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
    "DeskSelected",
]


def _build_env(tmp_path: Path, frequency_words=("Alpha", "Zebra")):
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
        "DeskSelected=selected\n"
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
            "progressive_text_translation": "false",
        },
        "rendering": {
            "display_mode": "monolithic",
        },
        "languages": {
            "de_prompt": "fixture",
            "de_lemma_index": freq_file.name,
        },
        "classification": {"enabled": "false"},
        "wordfill": {"enabled": "false"},
        "environment": {"kardenwort_workspace": str(tmp_path)},
    })
    paths["anki_mapping_file"] = mapping_file
    paths["kardenwort_workspace"] = tmp_path
    adapter = desk.SqliteStorageAdapter(config, paths)
    return config, paths, adapter


def _words_by_coord(db_path: Path, session_zid: str):
    db = KardenwortDB(db_path=db_path)
    return {(int(w["sentence_index"]), int(w["token_order"])): w for w in db.get_words_by_session(session_zid)}


def test_pipe_c3_progressive_stage_enrichment_uses_coordinates_and_reprocess_false_under_frequency_sort(tmp_path):
    """PIPE-C3: _progressive_worker_stage_enrichment must target (1, 1) Alpha via target_coordinates with reprocess=False."""
    config, paths, adapter = _build_env(tmp_path, frequency_words=("Alpha", "Zebra"))
    zid = "20261127200101"
    rows = [
        ["0", "Zebra", "Zebra", "зебра", "/z/", "Subst.", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
        ["1", "Alpha", "Alpha", "", "", "", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="zebra-alpha",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Zebra Alpha.", "sentence_destination": "Зебра альфа."}],
        row_provenances={(1, 0): "fixture:initial"},
        zid=zid,
    )

    seen_lemmas = []

    def provider(req_rows):
        enriched = []
        for item in req_rows:
            seen_lemmas.append(item["WordSource"])
            enriched.append({
                "row_id": item["row_id"],
                "WordDestination": f"ai:{item['WordSource']}",
                "WordSourceIPA": f"/{item['WordSource'].lower()}/",
                "WordSourceMorphologyAI": "AI-morph",
            })
        return enriched

    with enrichment_engine(config, provider) as server:
        server.release.set()
        mapping = desk.load_anki_mapping(paths["anki_mapping_file"])
        role_fields = desk.get_role_fields(mapping, HEADERS)
        args = Namespace(zid=zid, language="de", prompt="fixture", trace_id=f"{zid}:test")
        tsv_path = tmp_path / f"{zid}-zebra-alpha.de.tsv"
        desk._progressive_worker_stage_enrichment(
            tsv_path, args, config, paths, [list(r) for r in rows], list(HEADERS), role_fields
        )

    assert seen_lemmas == ["Alpha"]
    words = _words_by_coord(paths["sqlite_db_path"], zid)
    assert words[(1, 0)]["word_destination"] == "зебра"
    assert words[(1, 0)]["word_provenance"] == "fixture:initial"
    assert words[(1, 1)]["word_destination"] == "ai:Alpha"
    assert words[(1, 1)]["ipa"] == "/alpha/"


def test_pipe_c3_progressive_stage_enrichment_detects_stored_failure_and_propagates_worker_error(
    tmp_path, monkeypatch
):
    """PIPE-C3: stored failure cells must trigger enrichment and enrichment failure must fail worker_status + finished event."""
    config, paths, adapter = _build_env(tmp_path)
    zid = "20261127200102"
    rows = [
        ["0", "Alpha", "Alpha", "HTTP 429 Too Many Requests", "/a/", "Subst.", "1", "Alpha.", "Альфа.", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="alpha",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Alpha.", "sentence_destination": "Альфа."}],
        zid=zid,
    )
    # Put raw stored failure in DB to simulate legacy/uncleaned failure cell in Stage 3
    with adapter.db.get_connection() as conn:
        conn.execute(
            "UPDATE words SET word_destination = 'HTTP 429 Too Many Requests' WHERE session_zid = ?",
            (zid,),
        )

    def failing_provider(req_rows):
        return [{"row_id": item["row_id"], "status": "error", "WordDestination": "[FAILED]"} for item in req_rows]

    captured_events = []
    orig_write = desk.safe_write_update_js

    def spy_write(*args, **kwargs):
        captured_events.append((kwargs.get("stage"), kwargs.get("status"), kwargs.get("error")))
        return orig_write(*args, **kwargs)

    monkeypatch.setattr(desk, "safe_write_update_js", spy_write)

    with enrichment_engine(config, failing_provider) as server:
        server.release.set()
        monkeypatch.setattr(desk, "progressive_runtime_inputs", lambda _args: (config, paths, {}, {"enabled": False}))
        tsv_path = tmp_path / f"{zid}-alpha.de.tsv"
        args = Namespace(
            config=None,
            tsv=str(tsv_path),
            language="de",
            target_lang="ru",
            prompt="fixture",
            provider="google",
            text_mode="single",
            skip_intellifiller=False,
            zid=zid,
            trace_id=f"{zid}:worker",
        )
        desk.cmd_progressive_worker(args)

    worker_state = adapter.db.get_worker_status(zid)
    assert worker_state["worker_status"] == "failed"
    finished = [ev for ev in captured_events if ev[0] == "finished"]
    assert finished, "Expected a terminal finished event"
    assert finished[-1][1] == "failed"
    assert finished[-1][2] is not None


def test_pipe_c3_monolithic_enrichment_uses_coordinates_and_reprocess_false(tmp_path):
    """PIPE-C3: monolithic run_render_flow must not force reprocess=True onto already-translated rows."""
    config, paths, adapter = _build_env(tmp_path, frequency_words=("Alpha", "Zebra"))
    zid = "20261127200103"
    rows = [
        ["0", "Zebra", "Zebra", "зебра", "/z/", "Subst.", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
        ["1", "Alpha", "Alpha", "", "", "", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="zebra-alpha",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Zebra Alpha.", "sentence_destination": "Зебра альфа."}],
        row_provenances={(1, 0): "fixture:initial"},
        zid=zid,
    )

    seen_lemmas = []

    def provider(req_rows):
        enriched = []
        for item in req_rows:
            seen_lemmas.append(item["WordSource"])
            enriched.append({
                "row_id": item["row_id"],
                "WordDestination": f"ai:{item['WordSource']}",
                "WordSourceIPA": f"/{item['WordSource'].lower()}/",
                "WordSourceMorphologyAI": "AI-morph",
            })
        return enriched

    with enrichment_engine(config, provider) as server:
        server.release.set()
        config.set("triggers", "run_lemma_base_translation", "auto")
        config.set("pipeline", "lemma_base_provider", "intellifiller")
        config.set("pipeline", "lemma_provider_chain", "intellifiller")
        desk.run_render_flow(
            text="Zebra Alpha.",
            language="de",
            zid=zid,
            text_mode="single",
            config=config,
            resolved_paths=paths,
            theme="dark",
            tsv_path=zid,
            spawn_children=False,
            seq_num=1,
            wordfill_cfg={"enabled": False},
        )

    assert seen_lemmas == ["Alpha"]
    words = _words_by_coord(paths["sqlite_db_path"], zid)
    assert words[(1, 0)]["word_destination"] == "зебра"
    assert words[(1, 0)]["word_provenance"] == "fixture:initial"
    assert words[(1, 1)]["word_destination"] == "ai:Alpha"


def test_pipe_c4_save_session_preserves_sanitized_sentences_and_text_provenance(tmp_path):
    """PIPE-C4: tabular save_session with explicit sentences must preserve sanitized destinations and text_provenance."""
    _, paths, adapter = _build_env(tmp_path)
    zid = "20261127200104"
    rows = [
        ["0", "Hello", "Hello", "привет", "", "", "1", "Hello.", "HTTP 429 Too Many Requests", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="hello",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hello.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[{
            "sentence_index": 1,
            "sentence_source": "Hello.",
            "sentence_destination": "HTTP 429 Too Many Requests",
            "sentence_destination2": "[FAILED]",
        }],
        text_provenance="live:google",
        zid=zid,
    )

    sents = KardenwortDB(db_path=paths["sqlite_db_path"]).get_sentences_by_session(zid)
    assert len(sents) == 1
    assert sents[0]["sentence_destination"] == ""
    assert sents[0]["sentence_destination2"] == ""
    assert sents[0]["text_provenance"] == "live:google"


def test_pipe_c5_single_sentence_sorted_save_does_not_swap_provenances(tmp_path):
    """PIPE-C5: single-sentence frequency-sorted save must not swap positional row_provenances against TokenOrder."""
    _, paths, adapter = _build_env(tmp_path)
    zid = "20261127200105"
    # Frequency-sorted rows: row_idx=0 has TokenOrder=1 (Alpha), row_idx=1 has TokenOrder=0 (Zebra)
    sorted_rows = [
        ["1", "Alpha", "Alpha", "альфа", "", "", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
        ["0", "Zebra", "Zebra", "зебра", "", "", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="sorted-prov",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra Alpha.",
        headers=HEADERS,
        data_rows=sorted_rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Zebra Alpha.", "sentence_destination": "Зебра альфа."}],
        row_provenances={0: "live:deepl", 1: "live:google"},
        zid=zid,
    )

    words = _words_by_coord(paths["sqlite_db_path"], zid)
    assert words[(1, 1)]["word_provenance"] == "live:deepl"
    assert words[(1, 0)]["word_provenance"] == "live:google"


def test_pipe_c5_monolithic_render_does_not_mix_row_id_and_token_order_provenance_keys(tmp_path, monkeypatch):
    """PIPE-C5: monolithic fast-path must not collide sorted row_id with TokenOrder in row_provenances."""
    config, paths, adapter = _build_env(tmp_path, frequency_words=("Alpha", "Zebra"))
    zid = "20261127200106"
    # Zebra (TokenOrder=0) is untranslated; Alpha (TokenOrder=1) is already translated via live:deepl.
    # Frequency sort places Alpha at row_id=0 (TokenOrder=1) and Zebra at row_id=1 (TokenOrder=0).
    rows = [
        ["0", "Zebra", "Zebra", "", "", "", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
        ["1", "Alpha", "Alpha", "альфа", "", "", "1", "Zebra Alpha.", "Зебра альфа.", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="mixed-prov",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Zebra Alpha.", "sentence_destination": "Зебра альфа."}],
        row_provenances={(1, 1): "live:deepl"},
        zid=zid,
    )

    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "manual")
    config.set("pipeline", "lemma_base_provider", "google")
    config.set("pipeline", "lemma_provider_chain", "google")

    monkeypatch.setattr(
        desk,
        "translate_lemmas_fast_path",
        lambda lemmas, *a, **kw: desk.ProvenanceDict({lem: "зебра" for lem in lemmas}, provenance="live:google"),
    )

    html_out = desk.run_render_flow(
        text="Zebra Alpha.",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
    )

    words = _words_by_coord(paths["sqlite_db_path"], zid)
    assert words[(1, 1)]["word_provenance"] == "live:deepl"
    assert words[(1, 0)]["word_provenance"] == "live:google"
    assert 'data-provenance="live:deepl"' in html_out
    assert 'data-provenance="live:google"' in html_out
