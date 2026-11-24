"""Phase 1 reproduction and contract tests for Task 1.3:
Sorted partial/final provenance, CLI wordfill same-value/supplementary/translation parity,
child-card attribution, SQLite sentence persistence failure, empty-word sentence lifecycle
including startup/terminal/provider/deleted failures, extraction bool/nonfinite scalars,
grouped anchors, text offline_fallback policy, and TSV prompt-target/reprocess parity.
"""
import configparser
import json
import math
import re
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import kardenwort_desk as desk
import kardenwort_extraction as extraction
import kardenwort_reword as reword
from kardenwort_db import KardenwortDB
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    owned_configuration,
    translation_service,
)
from tests.enrichment_test_support import enrichment_engine


TARGET_ZID = "20261124062001"
NEIGHBOR_ZID = "20261124062002"
CORPUS_ZID = "20261124062003"

HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "WordSourcePOS",
    "WordSourceGender",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
]

ROLE_FIELDS = {
    "lemma": "WordSource",
    "word_translation": "WordDestination",
    "ipa": "WordSourceIPA",
    "morphology": "WordSourceMorphologyAI",
    "pos": "WordSourcePOS",
    "gender": "WordSourceGender",
    "sentence_index": "SentenceSourceIndex",
    "sentence_source": "SentenceSource",
    "sentence_destination": "SentenceDestination",
}


class SemicolonTranslationHandler(DeterministicTranslationHandler):
    def translate(self, provider, text, source, target):
        self.server.provider_calls.append((text, source, target))
        lines = []
        for line in text.splitlines():
            if "; " in line:
                lines.append("; ".join(f"{provider}: {p}" for p in line.split("; ")))
            else:
                lines.append(f"{provider}: {line}")
        return "\n".join(lines)


def _read_update_payloads(tsv_path: Path):
    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    if not updates_dir.exists():
        return []
    payloads = []
    for path in sorted(updates_dir.glob("*.js")):
        raw = path.read_text(encoding="utf-8")
        if "window.receiveUpdate(" in raw:
            body = raw.split("window.receiveUpdate(", 1)[1].rsplit(");", 1)[0]
            payloads.append(json.loads(body))
    return payloads


def _cold_bundle(db_path: Path, zid: str):
    return KardenwortDB(db_path=db_path).get_session_bundle(zid, parse_json=True)


def _setup_sqlite_cli_env(tmp_path: Path, translator_url: str = "", *, wordfill_enabled: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config, paths, _ = owned_configuration(tmp_path, translator_url, "sqlite")
    cfg_path = tmp_path / "config.ini"
    freq_file = tmp_path / "de_lemma_index.txt"
    # German lemma index is case-sensitive, one word per line (lower line number = higher frequency).
    # Baum at line 0 and Haus at line 1 forces frequency sorting to reorder [Haus, Baum] -> [Baum, Haus].
    freq_file.write_text("Baum\nHaus\n", encoding="utf-8")
    config.read_dict({
        "settings": {
            "default_language": "de",
            "default_target_language": "en",
            "table_sort_mode": "frequency",
        },
        "languages": {
            "de_lemma_index": str(freq_file),
        },
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "auto",
            "run_lemma_enrichment": "manual",
        },
        "translation": {
            "translation_order": "top_to_bottom",
            "translation_chunk_size": "1",
            "translation_max_retries": "1",
            "translation_retry_backoff": "0",
        },
        "wordfill": {
            "enabled": "true" if wordfill_enabled else "false",
            "scan_roots": str(tmp_path / "empty-corpus"),
            "scan_depth": "1",
            "scan_scope": "all",
            "scan_sort_order": "chronological",
            "scan_match_language": "true",
            "target_quality": "any",
            "target_fallback": "true",
        },
        "server": {"update_delivery": "desktop_file"},
    })
    with cfg_path.open("w", encoding="utf-8") as stream:
        config.write(stream)
    db = KardenwortDB(db_path=paths["sqlite_db_path"])
    db.run_migrations()
    return config, paths, db, cfg_path


def _seed_neighbor(db: KardenwortDB):
    db.save_session_bundle(
        {
            "zid": NEIGHBOR_ZID,
            "slug": "neighbor",
            "source_language": "de",
            "target_language": "en",
            "text_mode": "single",
            "source_raw_text": "Der Stein.",
        },
        [
            {
                "session_zid": NEIGHBOR_ZID,
                "sentence_index": 1,
                "sentence_source": "Der Stein.",
                "sentence_destination": "The stone.",
                "text_provenance": "live:deepl",
            }
        ],
        [
            {
                "session_zid": NEIGHBOR_ZID,
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Stein",
                "lemma": "Stein",
                "word_destination": "stone",
                "word_provenance": "live:deepl",
            }
        ],
    )


# ---------------------------------------------------------------------------
# 1. Sorted partial/final provenance across CLI stages & cmd_progressive_worker
# ---------------------------------------------------------------------------


def test_cmd_progressive_worker_projects_exact_occurrence_provenance_in_sorted_order(tmp_path):
    """Repeated token_order=0 across two sentences sorted by frequency must preserve exact per-occurrence provenance."""
    with translation_service(SemicolonTranslationHandler) as translator:
        config, paths, db, cfg_path = _setup_sqlite_cli_env(
            tmp_path, f"http://127.0.0.1:{translator.server_port}"
        )
        _seed_neighbor(db)
        neighbor_before = _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID)

        db.save_session_bundle(
            {
                "zid": TARGET_ZID,
                "slug": "sorted-prov",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "multi",
                "source_raw_text": "Das Haus.\nDer Baum.",
            },
            [
                {"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "House.", "text_provenance": "live:deepl"},
                {"session_zid": TARGET_ZID, "sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "Tree.", "text_provenance": "live:google"},
            ],
            [
                {"session_zid": TARGET_ZID, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house", "word_provenance": "live:deepl"},
                {"session_zid": TARGET_ZID, "sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": "tree", "word_provenance": "live:google"},
            ],
        )

        tsv_path = Path(paths["results_dir"]) / f"{TARGET_ZID}-sorted-prov.de.tsv"
        args = SimpleNamespace(
            config=str(cfg_path),
            tsv=str(tsv_path),
            language="de",
            target_lang="en",
            text_mode="multi",
            zid=TARGET_ZID,
            trace_id=f"{TARGET_ZID}:sorted",
            skip_intellifiller=True,
            provider=None,
        )
        desk.cmd_progressive_worker(args)

        payloads = _read_update_payloads(tsv_path)
        finished = [p for p in payloads if p.get("stage") == "finished"]
        assert finished
        rows = finished[-1]["rows"]
        # Frequency sorting puts Baum (rank 1000) before Haus (rank 10)
        assert rows["0"]["lemma"] == "Baum"
        assert rows["1"]["lemma"] == "Haus"
        assert rows["0"]["provenance"] == "live:google", rows
        assert rows["1"]["provenance"] == "live:deepl", rows
        assert _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID) == neighbor_before


def test_cli_stage_translation_partial_failure_emits_sorted_rows_and_preserves_committed_provenance(tmp_path):
    """When chunk 2 fails after chunk 1 commits, failure emission stays sorted and keeps chunk 1 provenance."""
    class SecondChunkFailHandler(SemicolonTranslationHandler):
        def translate(self, provider, text, source, target):
            self.server.provider_calls.append((text, source, target))
            if "Baum" in text:
                raise RuntimeError("simulated chunk-2 failure")
            return f"{provider}: {text}"

    with translation_service(SecondChunkFailHandler) as translator:
        config, paths, db, _ = _setup_sqlite_cli_env(
            tmp_path, f"http://127.0.0.1:{translator.server_port}"
        )
        db.save_session_bundle(
            {
                "zid": TARGET_ZID,
                "slug": "partial-fail",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "multi",
                "source_raw_text": "Das Haus.\nDer Baum.",
            },
            [
                {"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "House."},
                {"session_zid": TARGET_ZID, "sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "Tree."},
            ],
            [
                {"session_zid": TARGET_ZID, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": ""},
                {"session_zid": TARGET_ZID, "sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": ""},
            ],
        )
        tsv_path = Path(paths["results_dir"]) / f"{TARGET_ZID}-partial-fail.de.tsv"
        data_rows = [
            ["0", "Haus", "Haus", "", "", "", "", "", "1", "Das Haus.", "House."],
            ["0", "Baum", "Baum", "", "", "", "", "", "2", "Der Baum.", "Tree."],
        ]
        args = SimpleNamespace(language="de", target_lang="en", text_mode="multi", zid=TARGET_ZID, trace_id=f"{TARGET_ZID}:partial")
        res = desk._progressive_worker_stage_translation_impl(
            tsv_path, args, config, paths, data_rows, list(HEADERS), dict(ROLE_FIELDS), TARGET_ZID, trace_id=args.trace_id, row_provenances={}
        )
        assert res.error is not None

        payloads = _read_update_payloads(tsv_path)
        failed_events = [p for p in payloads if p.get("stage") == "translated" and p.get("status") == "failed"]
        assert failed_events
        rows = failed_events[-1]["rows"]
        # Must remain frequency-sorted (Baum at 0, Haus at 1) and carry Haus's committed provenance
        assert rows["0"]["lemma"] == "Baum"
        assert rows["1"]["lemma"] == "Haus"
        assert rows["1"]["trans"] == "google: Haus"
        assert rows["1"]["provenance"] == "live:google", rows
        assert not rows["0"].get("provenance"), rows

        cold_words = {(w["sentence_index"], w["token_order"]): w for w in _cold_bundle(paths["sqlite_db_path"], TARGET_ZID)["words"]}
        assert cold_words[(1, 0)]["word_destination"] == "google: Haus"
        assert cold_words[(1, 0)]["word_provenance"] == "live:google"
        assert not cold_words[(2, 0)]["word_destination"]


# ---------------------------------------------------------------------------
# 2. CLI wordfill same-value, supplementary-only, translation-changing & reload parity
# ---------------------------------------------------------------------------


def test_cli_wordfill_publishes_only_persisted_fields_with_truthful_provenance_and_reload_parity(tmp_path):
    """CLI wordfill must not leak unpersisted fields or overwrite existing translation provenance on supplementary/same-value patches."""
    with translation_service(SemicolonTranslationHandler) as translator:
        config, paths, db, _ = _setup_sqlite_cli_env(
            tmp_path, f"http://127.0.0.1:{translator.server_port}", wordfill_enabled=True
        )
        config.set("settings", "table_sort_mode", "appearance")
        db.save_session_bundle(
            {
                "zid": CORPUS_ZID,
                "slug": "corpus",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "multi",
                "source_raw_text": "Haus. Baum. Apfel.",
            },
            [{"session_zid": CORPUS_ZID, "sentence_index": 1, "sentence_source": "Haus. Baum. Apfel.", "sentence_destination": "House. Tree. Apple."}],
            [
                {
                    "session_zid": CORPUS_ZID, "sentence_index": 1, "token_order": 0,
                    "quotation": "Haus", "lemma": "Haus", "word_destination": "corpus-house",
                    "ipa": "/haʊ̯s/", "morphology": "Subst.Neut", "pos": "NOUN", "gender": "n",
                    "extra_fields": {"ClassificationOxford": "A1"},
                },
                {
                    "session_zid": CORPUS_ZID, "sentence_index": 1, "token_order": 1,
                    "quotation": "Baum", "lemma": "Baum", "word_destination": "tree",
                    "ipa": "/baʊ̯m/", "morphology": "Subst.Masc", "pos": "NOUN", "gender": "m",
                    "extra_fields": {"ClassificationOxford": "A2"},
                },
                {
                    "session_zid": CORPUS_ZID, "sentence_index": 1, "token_order": 2,
                    "quotation": "Apfel", "lemma": "Apfel", "word_destination": "apple",
                    "ipa": "/ˈapfəl/", "morphology": "Subst.Masc", "pos": "NOUN", "gender": "m",
                    "extra_fields": {"ClassificationOxford": "B1"},
                },
            ],
        )

        # Target session has:
        # (1, 0) Haus: existing destination 'house' ('live:google'), missing IPA/morph -> supplementary-only
        # (2, 0) Baum: existing destination 'tree' ('live:deepl') and existing IPA/morph -> same-value
        # (3, 0) Apfel: empty destination & empty IPA/morph -> translation-changing
        db.save_session_bundle(
            {
                "zid": TARGET_ZID,
                "slug": "wf-parity",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "multi",
                "source_raw_text": "Haus.\nBaum.\nApfel.",
            },
            [
                {"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "House."},
                {"session_zid": TARGET_ZID, "sentence_index": 2, "sentence_source": "Baum.", "sentence_destination": "Tree."},
                {"session_zid": TARGET_ZID, "sentence_index": 3, "sentence_source": "Apfel.", "sentence_destination": "Apple."},
            ],
            [
                {
                    "session_zid": TARGET_ZID, "sentence_index": 1, "token_order": 0,
                    "quotation": "Haus", "lemma": "Haus", "word_destination": "house",
                    "ipa": None, "morphology": None, "pos": None, "gender": None,
                    "word_provenance": "live:google",
                },
                {
                    "session_zid": TARGET_ZID, "sentence_index": 2, "token_order": 0,
                    "quotation": "Baum", "lemma": "Baum", "word_destination": "tree",
                    "ipa": "/baʊ̯m/", "morphology": "Subst.Masc", "pos": "NOUN", "gender": "m",
                    "word_provenance": "live:deepl",
                },
                {
                    "session_zid": TARGET_ZID, "sentence_index": 3, "token_order": 0,
                    "quotation": "Apfel", "lemma": "Apfel", "word_destination": "",
                    "ipa": None, "morphology": None, "pos": None, "gender": None,
                    "word_provenance": None,
                },
            ],
        )

        wf_headers = list(HEADERS) + ["WordDestinationInflectedForm"]
        data_rows = [
            ["0", "Haus", "Haus", "house", "", "", "", "", "1", "Haus.", "House.", ""],
            ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "2", "Baum.", "Tree.", ""],
            ["0", "Apfel", "Apfel", "", "", "", "", "", "3", "Apfel.", "Apple.", ""],
        ]
        tsv_path = Path(paths["results_dir"]) / f"{TARGET_ZID}-wf-parity.de.tsv"
        args = SimpleNamespace(language="de", target_lang="en", text_mode="multi", zid=TARGET_ZID, trace_id=f"{TARGET_ZID}:wf")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path, args, config, paths, data_rows, wf_headers, dict(ROLE_FIELDS), TARGET_ZID, trace_id=args.trace_id, row_provenances={}
        )
        assert res.error is None
        assert translator.provider_calls == []

        cold = {(w["sentence_index"], w["token_order"]): w for w in _cold_bundle(paths["sqlite_db_path"], TARGET_ZID)["words"]}
        # Haus: existing translation 'house' and provenance 'live:google' preserved; supplementary IPA/morph persisted
        assert cold[(1, 0)]["word_destination"] == "house"
        assert cold[(1, 0)]["word_provenance"] == "live:google"
        assert cold[(1, 0)]["ipa"] == "/haʊ̯s/"
        # Baum: same-value preserved with original 'live:deepl'
        assert cold[(2, 0)]["word_destination"] == "tree"
        assert cold[(2, 0)]["word_provenance"] == "live:deepl"
        # Apfel: newly supplied destination receives 'corpus:wordfill'
        assert cold[(3, 0)]["word_destination"] == "apple"
        assert cold[(3, 0)]["word_provenance"] == "corpus:wordfill"
        assert cold[(3, 0)]["ipa"] == "/ˈapfəl/"

        # Live emitted payload must exactly match cold SQLite reload
        payloads = _read_update_payloads(tsv_path)
        translated = [p for p in payloads if p.get("stage") == "translated"][-1]["rows"]
        by_lemma = {entry["lemma"]: entry for entry in translated.values()}
        assert by_lemma["Haus"]["trans"] == cold[(1, 0)]["word_destination"]
        assert by_lemma["Haus"]["provenance"] == cold[(1, 0)]["word_provenance"]
        assert by_lemma["Haus"]["ipa"] == (cold[(1, 0)]["ipa"] or "")
        assert by_lemma["Baum"]["provenance"] == cold[(2, 0)]["word_provenance"]
        assert by_lemma["Apfel"]["trans"] == cold[(3, 0)]["word_destination"]
        assert by_lemma["Apfel"]["provenance"] == cold[(3, 0)]["word_provenance"]


# ---------------------------------------------------------------------------
# 3. Child-card attribution & explicit sentence persistence failure
# ---------------------------------------------------------------------------


def test_child_card_attribution_does_not_inherit_other_sentence_provider(tmp_path):
    """Unattributed sentence 2 must not inherit sentence 1's text_provenance on child cards."""
    config, paths, _ = owned_configuration(tmp_path, "", "sqlite")
    config.read_dict({
        "rendering": {"display_mode": "monolithic"},
        "sentences_mode": {"enabled": "true", "delivery_mode": "container", "spawn_order": "normal"},
        "wordfill": {"enabled": "false"},
        "server": {"enabled": "false"},
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
    })
    db = KardenwortDB(db_path=paths["sqlite_db_path"])
    db.run_migrations()
    source_text = "Alpha eins.\nBeta zwei."
    db.save_session_bundle(
        {
            "zid": TARGET_ZID,
            "slug": "child-attr",
            "source_language": "de",
            "target_language": "en",
            "text_mode": "multi",
            "source_raw_text": source_text,
        },
        [
            {"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "Alpha eins.", "sentence_destination": "Alpha one.", "text_provenance": "live:google"},
            {"session_zid": TARGET_ZID, "sentence_index": 2, "sentence_source": "Beta zwei.", "sentence_destination": "Beta two.", "text_provenance": None},
        ],
        [
            {"session_zid": TARGET_ZID, "sentence_index": 1, "token_order": 0, "quotation": "Alpha", "lemma": "Alpha", "word_destination": "alpha"},
            {"session_zid": TARGET_ZID, "sentence_index": 2, "token_order": 0, "quotation": "Beta", "lemma": "Beta", "word_destination": "beta"},
        ],
    )

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=TARGET_ZID,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        spawn_children=False,
        wordfill_cfg={"enabled": False},
        seq_num=1,
    )
    match = re.search(r'<script id="sentence-cards" type="application/json">\s*(.*?)\s*</script>', html, re.DOTALL)
    assert match is not None
    cards = json.loads(match.group(1))
    child_by_idx = {c["sentence_idx"]: c for c in cards if c.get("sentence_idx", 0) > 0}
    assert child_by_idx[1]["text_provenance"] == "live:google"
    assert child_by_idx[2]["text_provenance"] == "", f"Unattributed child card inherited provider: {child_by_idx[2]}"


def test_sqlite_sentence_persistence_failure_is_not_swallowed_in_cli_translation_stage(tmp_path):
    """When SQLite rejects update_sentence_translation, the translation stage must surface failure explicitly."""
    with translation_service(SemicolonTranslationHandler) as translator:
        config, paths, db, _ = _setup_sqlite_cli_env(
            tmp_path, f"http://127.0.0.1:{translator.server_port}"
        )
        config.set("triggers", "run_text_translation", "auto")
        config.set("triggers", "run_lemma_base_translation", "manual")
        db.save_session_bundle(
            {
                "zid": TARGET_ZID,
                "slug": "sent-fail",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "single",
                "source_raw_text": "Das Haus.",
            },
            [{"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": ""}],
            [{"session_zid": TARGET_ZID, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"}],
        )
        with db.get_connection() as conn:
            conn.execute(
                "CREATE TRIGGER reject_sent_update BEFORE UPDATE OF sentence_destination ON sentences "
                "BEGIN SELECT RAISE(ABORT, 'owned sentence persistence failure'); END"
            )

        tsv_path = Path(paths["results_dir"]) / f"{TARGET_ZID}-sent-fail.de.tsv"
        data_rows = [["0", "Haus", "Haus", "house", "", "", "", "", "1", "Das Haus.", ""]]
        args = SimpleNamespace(language="de", target_lang="en", text_mode="single", zid=TARGET_ZID, trace_id=f"{TARGET_ZID}:sfail")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path, args, config, paths, data_rows, list(HEADERS), dict(ROLE_FIELDS), TARGET_ZID, trace_id=args.trace_id, row_provenances={}
        )
        assert res.error is not None, "SQLite sentence persistence failure was silently swallowed"
        payloads = _read_update_payloads(tsv_path)
        text_events = [p for p in payloads if p.get("stage") == "translated_text"]
        assert text_events
        assert text_events[-1]["text_translation_status"] == "failed"


# ---------------------------------------------------------------------------
# 4. Empty-word sentence lifecycle (monolithic, cmd_progressive_worker, failures)
# ---------------------------------------------------------------------------


def test_monolithic_empty_extraction_persists_and_translates_authoritative_sentences(tmp_path, monkeypatch):
    """Monolithic run_text=auto with zero extracted words persists and translates authoritative sentences."""
    with translation_service(SemicolonTranslationHandler) as translator:
        config, paths, db, _ = _setup_sqlite_cli_env(
            tmp_path, f"http://127.0.0.1:{translator.server_port}"
        )
        config.read_dict({
            "rendering": {"display_mode": "monolithic"},
            "sentences_mode": {"enabled": "false"},
            "triggers": {
                "run_text_translation": "auto",
                "run_lemma_base_translation": "auto",
                "run_lemma_enrichment": "manual",
            },
        })
        _seed_neighbor(db)
        neighbor_before = _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID)

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""),
        )

        desk.run_render_flow(
            text="!!!",
            language="de",
            zid=TARGET_ZID,
            text_mode="single",
            config=config,
            resolved_paths=paths,
            spawn_children=False,
            wordfill_cfg={"enabled": False},
        )

        bundle = _cold_bundle(paths["sqlite_db_path"], TARGET_ZID)
        assert bundle is not None
        assert bundle["words"] == []
        assert len(bundle["sentences"]) == 1
        assert bundle["sentences"][0]["sentence_source"] == "!!!"
        assert bundle["sentences"][0]["sentence_destination"] == "google: !!!"
        assert bundle["sentences"][0]["text_provenance"] == "live:google"
        assert len(translator.provider_calls) == 1
        assert _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID) == neighbor_before


def test_cmd_progressive_worker_empty_word_session_translates_sentences_and_finalizes(tmp_path):
    """cmd_progressive_worker on a sentence-only session translates pending sentences and finalizes worker_status."""
    with translation_service(SemicolonTranslationHandler) as translator:
        config, paths, db, cfg_path = _setup_sqlite_cli_env(
            tmp_path, f"http://127.0.0.1:{translator.server_port}"
        )
        config.read_dict({
            "triggers": {
                "run_text_translation": "auto",
                "run_lemma_base_translation": "auto",
                "run_lemma_enrichment": "manual",
            },
        })
        with cfg_path.open("w", encoding="utf-8") as stream:
            config.write(stream)
        _seed_neighbor(db)
        neighbor_before = _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID)

        db.save_session_bundle(
            {
                "zid": TARGET_ZID,
                "slug": "empty-worker",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "single",
                "source_raw_text": "!!!",
            },
            [{"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "!!!", "sentence_destination": ""}],
            [],
        )

        tsv_path = Path(paths["results_dir"]) / f"{TARGET_ZID}-empty-worker.de.tsv"
        args = SimpleNamespace(
            config=str(cfg_path),
            tsv=str(tsv_path),
            language="de",
            target_lang="en",
            text_mode="single",
            zid=TARGET_ZID,
            trace_id=f"{TARGET_ZID}:empty",
            skip_intellifiller=True,
            provider=None,
        )
        desk.cmd_progressive_worker(args)

        bundle = _cold_bundle(paths["sqlite_db_path"], TARGET_ZID)
        assert bundle["words"] == []
        assert bundle["sentences"][0]["sentence_destination"] == "google: !!!"
        assert bundle["sentences"][0]["text_provenance"] == "live:google"
        assert len(translator.provider_calls) == 1

        status = db.get_worker_status(TARGET_ZID)
        assert status["worker_status"] == "finished", status
        assert status["worker_finished_at"] is not None
        payloads = _read_update_payloads(tsv_path)
        finished = [p for p in payloads if p.get("stage") == "finished"]
        assert finished and finished[-1]["status"] == "success"
        assert _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID) == neighbor_before


@pytest.mark.parametrize("failure_mode", ["startup_heartbeat", "dual_startup_and_terminal", "provider_outage", "deleted_session"])
def test_cmd_progressive_worker_finalizes_startup_terminal_provider_and_deleted_failures(tmp_path, failure_mode):
    """cmd_progressive_worker must finalize all failure paths without leaving worker_status='running' or falsely succeeding."""
    class FailingHandler(SemicolonTranslationHandler):
        def translate(self, provider, text, source, target):
            raise RuntimeError("simulated sentence outage")

    handler = FailingHandler if failure_mode == "provider_outage" else SemicolonTranslationHandler
    with translation_service(handler) as translator:
        config, paths, db, cfg_path = _setup_sqlite_cli_env(
            tmp_path / failure_mode, f"http://127.0.0.1:{translator.server_port}"
        )
        config.set("triggers", "run_text_translation", "auto")
        config.set("triggers", "run_lemma_base_translation", "manual")
        with cfg_path.open("w", encoding="utf-8") as stream:
            config.write(stream)
        _seed_neighbor(db)
        neighbor_before = _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID)

        db.save_session_bundle(
            {
                "zid": TARGET_ZID,
                "slug": f"fail-{failure_mode}",
                "source_language": "de",
                "target_language": "en",
                "text_mode": "single",
                "source_raw_text": "!!!",
            },
            [{"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "!!!", "sentence_destination": ""}],
            [],
        )

        if failure_mode in ("startup_heartbeat", "dual_startup_and_terminal"):
            with db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_hb BEFORE UPDATE OF worker_heartbeat_at ON sessions "
                    "BEGIN SELECT RAISE(ABORT, 'owned cli heartbeat startup failure'); END"
                )
        if failure_mode == "dual_startup_and_terminal":
            with db.get_connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_term BEFORE UPDATE OF worker_status ON sessions "
                    "WHEN NEW.worker_status IN ('finished', 'failed') "
                    "BEGIN SELECT RAISE(ABORT, 'owned cli terminal status failure'); END"
                )
        if failure_mode == "deleted_session":
            assert db.soft_delete_session(TARGET_ZID)

        tsv_path = Path(paths["results_dir"]) / f"{TARGET_ZID}-fail.de.tsv"
        args = SimpleNamespace(
            config=str(cfg_path),
            tsv=str(tsv_path),
            language="de",
            target_lang="en",
            text_mode="single",
            zid=TARGET_ZID,
            trace_id=f"{TARGET_ZID}:{failure_mode}",
            skip_intellifiller=True,
            provider=None,
        )

        caught = None
        try:
            desk.cmd_progressive_worker(args)
        except Exception as exc:
            caught = exc

        status = db.get_worker_status(TARGET_ZID)
        payloads = _read_update_payloads(tsv_path)
        if failure_mode == "startup_heartbeat":
            assert status["worker_status"] == "failed", status
            assert status["worker_finished_at"] is not None
            assert caught is not None or any(p.get("status") == "failed" for p in payloads)
            assert not any(p.get("stage") == "finished" and p.get("status") == "success" for p in payloads)
        elif failure_mode == "dual_startup_and_terminal":
            assert caught is not None, "Dual startup + terminal persistence failure must remain observable"
            assert not any(p.get("stage") == "finished" and p.get("status") == "success" for p in payloads)
        elif failure_mode == "provider_outage":
            assert status["worker_status"] == "failed", status
            assert status["worker_finished_at"] is not None
            finished = [p for p in payloads if p.get("stage") == "finished"]
            assert finished and finished[-1]["status"] == "failed"
        elif failure_mode == "deleted_session":
            assert status["worker_status"] != "running", status
            assert not any(p.get("stage") == "finished" and p.get("status") == "success" for p in payloads)
            with db.get_connection(read_only=True) as conn:
                row = conn.execute("SELECT deleted_at FROM sessions WHERE zid = ?", (TARGET_ZID,)).fetchone()
                assert row is not None and row[0] is not None

        assert _cold_bundle(paths["sqlite_db_path"], NEIGHBOR_ZID) == neighbor_before


# ---------------------------------------------------------------------------
# 5. Extraction bool / non-finite scalar validation & grouped anchors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [True, False, float("nan"), float("inf"), float("-inf"), None, ["a"], {"k": "v"}])
def test_extraction_row_rejects_bool_nonfinite_and_non_scalar_values(bad_value):
    """Extractor boundary rejects bools, NaN/Inf floats, None, and structured containers."""
    headers = ["TokenOrder", "WordSource"]
    with pytest.raises(ValueError):
        extraction.extraction_row({"TokenOrder": "0", "WordSource": bad_value}, headers)


def test_extraction_row_accepts_valid_scalars_and_exact_manifest():
    """Valid str, int, and finite float values pass and preserve header order."""
    headers = ["TokenOrder", "WordSource", "Score"]
    assert extraction.extraction_row({"TokenOrder": 0, "WordSource": "Haus", "Score": 1.25}, headers) == [
        "0", "Haus", "1.25"
    ]
    assert math.isfinite(1.25)


def test_grouped_anchor_preserved_for_display_and_rejected_at_write_boundaries():
    """Composite TokenOrder '1+5' is rejected at write boundaries while grouped display targets resolve cleanly."""
    with pytest.raises(desk.StructuredError):
        desk.edit_occurrence_coordinates("1", "1+5")
    with pytest.raises(reword.InvalidRewordTarget):
        reword.index_value("1+5")

    # Existing grouped display representation uses atomic occurrence targets via row IDs
    headers = ["TokenOrder", "SentenceSourceIndex", "WordSource", "all_row_ids"]
    rows = [
        ["1", "1", "Haus", "0,1"],
        ["5", "1", "Haus", "1"],
    ]
    targets = desk.lookup_occurrence_targets(headers, rows, {"sentence_index": "SentenceSourceIndex"})
    assert [targets[id(r)] for r in rows] == [
        [{"sentence_idx": 1, "token_order": 1}, {"sentence_idx": 1, "token_order": 5}],
        [{"sentence_idx": 1, "token_order": 5}],
    ]


# ---------------------------------------------------------------------------
# 6. Text offline_fallback policy & TSV prompt-target/reprocess parity
# ---------------------------------------------------------------------------


def test_text_offline_fallback_online_rate_limit_does_not_silently_use_argos(tmp_path):
    """Text implicit offline_fallback must require confirmed offline, matching lemma policy, while explicit Argos stays allowed."""
    import sys
    root = Path(__file__).resolve().parent.parent
    translator_root = root.parent / "20241122093311-deep-translator"
    for p in (str(translator_root / "20260209094544-deep-translator"), str(translator_root)):
        if p not in sys.path:
            sys.path.append(p)
    import translate_server
    from translate_server import TranslationRequestHandler

    calls = []
    behavior = {}

    def _make_engine(prov):
        def _tr(self, text, source, target, *args):
            calls.append((prov, text))
            fn = behavior.get(prov)
            if callable(fn):
                return fn(text, source, target)
            return f"{prov}:{text}"
        return _tr

    with patch.object(TranslationRequestHandler, "_translate_google", _make_engine("google")), \
         patch.object(TranslationRequestHandler, "_translate_argos", _make_engine("argos")), \
         translation_service(TranslationRequestHandler, auto_failover=False) as server:
        config, paths, _ = owned_configuration(tmp_path, f"http://127.0.0.1:{server.server_port}", "sqlite")
        config.read_dict({
            "pipeline": {
                "text_provider_chain": "google",
                "failover_strategy": "offline_fallback",
                "fast_connectivity_check_ips": "8.8.8.8",
            },
        })
        desk.clear_provider_cooldowns(config=config, resolved_paths=paths)

        def _rate_limit(*_args):
            raise translate_server.TranslationServerException(429, "ERR_GOOGLE_RATE_LIMIT", "HTTP 429 Too Many Requests")

        behavior["google"] = _rate_limit

        # Online probe: implicit offline_fallback must NOT fall back to Argos on rate limit
        with patch("kardenwort_desk.is_network_online_multi", return_value=True):
            with pytest.raises(Exception):
                desk.translate_text("Haus.", "de", "en", config, paths, task_type="text")
        assert calls == [("google", "Haus.")], f"Online rate limit unexpectedly invoked Argos: {calls}"

        # Explicit online Argos pin remains allowed
        calls.clear()
        with patch("kardenwort_desk.is_network_online_multi", return_value=True):
            explicit = desk.translate_text("Haus.", "de", "en", config, paths, provider="argos", task_type="text")
        assert str(explicit) == "argos:Haus."
        assert calls == [("argos", "Haus.")]

        # Confirmed offline permits Argos fallback
        calls.clear()
        desk.clear_provider_cooldowns(config=config, resolved_paths=paths)
        with patch("kardenwort_desk.is_network_online_multi", return_value=False):
            offline_res = desk.translate_text("Haus.", "de", "en", config, paths, task_type="text")
        assert str(offline_res) == "argos:Haus."
        assert calls == [("argos", "Haus.")]


def test_tsv_headless_intellifiller_honors_prompt_target_role_and_unfinished_429_policy(tmp_path):
    """TSV run_headless_intellifiller must match SQLite target-role/reprocess and unfinished-429 semantics."""
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "WordSourceIPA=ipa\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n",
        encoding="utf-8",
    )
    config = configparser.ConfigParser()
    config.read_dict({
        "storage": {"backend": "tsv"},
        "settings": {"default_language": "de", "anki_mapping_file": str(mapping_file)},
        "services": {"intellifiller_server_url": ""},
    })
    paths = {"anki_mapping_file": mapping_file, "base_dir": tmp_path, "results_dir": tmp_path}
    tsv_path = tmp_path / f"{TARGET_ZID}-tsv-parity.de.tsv"
    tsv_path.write_text(
        "TokenOrder\tWordSource\tWordDestination\tWordSourceIPA\tSentenceSourceIndex\tSentenceSource\n"
        "0\tHaus\thouse\t\t1\tHaus.\n"
        "0\tBaum\tHTTP 429 Too Many Requests\t/baum/\t2\tBaum.\n",
        encoding="utf-8",
    )

    def respond_ipa(rows):
        return [{"row_id": r["row_id"], "WordSourceIPA": "/ipa_" + r["WordSource"] + "/"} for r in rows]

    with enrichment_engine(config, response=respond_ipa) as engine:
        engine.release.set()
        ok = desk.run_headless_intellifiller(tsv_path, "ipa_extraction", config, paths, reprocess=False)

    assert ok
    assert len(engine.requests) == 1, "ipa_extraction with empty IPA should dispatch Haus even though WordDestination is filled"
    assert [r["WordSource"] for r in engine.requests[0]["rows"]] == ["Haus"]
    _, out_headers, out_rows = desk.load_tsv_rows(tsv_path)
    assert out_rows[0][out_headers.index("WordSourceIPA")] == "/ipa_Haus/"
    assert out_rows[0][out_headers.index("WordDestination")] == "house"
    # Baum already had IPA '/baum/' so it remained untouched under ipa_extraction
    assert out_rows[1][out_headers.index("WordSourceIPA")] == "/baum/"

    # Now ordinary translation prompt with reprocess=False must dispatch Baum because WordDestination is HTTP 429
    def respond_trans(rows):
        return [{"row_id": r["row_id"], "WordDestination": "trans_" + r["WordSource"]} for r in rows]

    with enrichment_engine(config, response=respond_trans) as engine2:
        engine2.release.set()
        ok2 = desk.run_headless_intellifiller(tsv_path, "fixture", config, paths, reprocess=False)

    assert ok2
    assert len(engine2.requests) == 1, "Ordinary enrichment with reprocess=False must retry HTTP 429 target row"
    assert [r["WordSource"] for r in engine2.requests[0]["rows"]] == ["Baum"]
    _, out_headers2, out_rows2 = desk.load_tsv_rows(tsv_path)
    assert out_rows2[1][out_headers2.index("WordDestination")] == "trans_Baum"
    assert out_rows2[0][out_headers2.index("WordDestination")] == "house"
