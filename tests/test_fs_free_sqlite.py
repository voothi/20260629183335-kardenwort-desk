"""
Unit and integration tests verifying the FS-Free SQLite-First Architecture (20260822110235-fs-free-sqlite-first-architecture).
Verifies that single and multi-window lookups, progressive pipelines, and rendering flows create 0 persistent files in results/
and operate 100% through SQLite tables.
"""

import configparser
import csv
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import kardenwort_desk as desk
from kardenwort_desk import (
    get_storage_adapter,
    SqliteStorageAdapter,
    prepare_lookup_tsv,
    run_lookup_flow,
)
from tests.test_lookup import setup_test_env


def test_fs_free_single_lookup_zero_disk_writes(monkeypatch, tmp_path):
    """
    Verifies that a single-window lookup flow backed by SQLite creates 0 files in results/
    and stores 100% of session, sentence, and word records in SQLite.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    db_file = tmp_path / "kardenwort_fs_free.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = results_dir

    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_file))
    config.set("storage", "fallback_to_tsv", "true")
    resolved_paths["storage_backend"] = "sqlite"
    resolved_paths["sqlite_db_path"] = db_file.resolve()

    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            "WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI",
            "SentenceSourceIndex", "SentenceSourceContextLeft", "SentenceSource", "SentenceSourceContextRight",
            "SentenceDestinationContextLeft", "SentenceDestination", "SentenceDestinationContextRight",
        ]
        rows = [
            ["apple", "", "ˈæp.əl", "NOUN", "1", "", "I eat an apple.", ""],
        ]
        with open(out_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Я ем яблоко."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: f"{l}_ru" for l in lemmas})

    test_zid = "20260822120000"
    res = run_lookup_flow(
        text="I eat an apple.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid=test_zid,
        text_mode="single",
    )

    # Verify 0 TSV/TXT/marker files in results_dir
    created_files = [f for f in results_dir.iterdir() if f.is_file() and not f.name.endswith(".log")]
    assert len(created_files) == 0, f"Expected 0 persistent files in results/, found: {created_files}"

    # Verify complete SQLite persistence
    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, SqliteStorageAdapter)

    with adapter.db.get_connection(zid=test_zid) as conn:
        sess = conn.execute("SELECT * FROM sessions WHERE zid = ?", (test_zid,)).fetchone()
        assert sess is not None
        assert sess["source_language"] == "en"
        assert sess["target_language"] == "ru"

        sents = conn.execute("SELECT * FROM sentences WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(sents) == 1
        assert sents[0]["sentence_source"] == "I eat an apple."
        assert sents[0]["sentence_destination"] == "Я ем яблоко."

        words = conn.execute("SELECT * FROM words WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(words) == 1
        assert words[0]["lemma"] == "apple"
        assert words[0]["word_destination"] == "apple_ru"


def test_fs_free_multi_window_sentences_mode_zero_disk_writes(monkeypatch, tmp_path):
    """
    Verifies that multi-window / sentences mode decomposition creates child sessions
    directly in SQLite without writing intermediate .txt, .tsv, or .cut_done marker files.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    db_file = tmp_path / "kardenwort_fs_multi.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = results_dir

    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_file))
    config.set("storage", "fallback_to_tsv", "true")
    resolved_paths["storage_backend"] = "sqlite"
    resolved_paths["sqlite_db_path"] = db_file.resolve()

    # Configure Sentences Mode with split
    if not config.has_section("sentences_mode"):
        config.add_section("sentences_mode")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "min_sentences", "2")
    config.set("sentences_mode", "parent_mode", "none")

    spawned_processes = []
    def mock_spawn(*args, **kwargs):
        spawned_processes.append(args)
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        return mock_proc

    monkeypatch.setattr(subprocess, "Popen", mock_spawn)

    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource",
        ]
        rows = [
            ["first", "", "1", "First sentence."],
            ["second", "", "2", "Second sentence."],
        ]
        with open(out_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Первое предложение.", 1: "Второе предложение."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: f"{l}_ru" for l in lemmas})

    test_zid = "20260822123000"
    res = desk.run_render_flow(
        text="First sentence. Second sentence.",
        language="en",
        zid=test_zid,
        text_mode="sentences",
        config=config,
        resolved_paths=resolved_paths,
    )

    # Verify no .tsv, .txt, or .cut_done files in results_dir
    created_files = [f for f in results_dir.iterdir() if f.is_file() and not f.name.endswith(".log")]
    assert len(created_files) == 0, f"Expected 0 persistent files in results/, found: {created_files}"

    # Verify child sessions were saved in SQLite
    adapter = get_storage_adapter(config, resolved_paths)
    with adapter.db.get_connection(zid=test_zid) as conn:
        sessions = conn.execute("SELECT * FROM sessions ORDER BY zid ASC").fetchall()
        assert len(sessions) >= 2

    # Verify AHK child process was spawned with --restore commands
    assert len(spawned_processes) >= 1
    spawned_cmds = [p[0] for p in spawned_processes if len(p) > 0 and isinstance(p[0], list)]
    has_restore = any("--restore" in cmd for cmd in spawned_cmds)
    assert has_restore, f"Expected --restore in spawned child commands: {spawned_cmds}"


def test_fs_free_progressive_worker_in_memory_enrichment(monkeypatch, tmp_path):
    """
    Verifies that cmd_progressive_worker runs translation and enrichment in-memory
    directly against SQLite without requiring TSV files or creating marker files.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    db_file = tmp_path / "kardenwort_fs_worker.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = results_dir

    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_file))
    config.set("storage", "fallback_to_tsv", "true")
    resolved_paths["storage_backend"] = "sqlite"
    resolved_paths["sqlite_db_path"] = db_file.resolve()

    anki_mapping_path = tmp_path / "anki-mapping.ini"
    anki_mapping_path.write_text("""[fields]
WordSource = lemma
WordDestination = word_translation
SentenceSourceIndex = sentence_index
SentenceSource = sentence_source
SentenceDestination = sentence_destination
""", encoding="utf-8")
    config.set("settings", "anki_mapping_file", str(anki_mapping_path))

    cfg_file = tmp_path / "test_config.ini"
    with open(cfg_file, "w", encoding="utf-8") as cf:
        config.write(cf)

    adapter = get_storage_adapter(config, resolved_paths)
    test_zid = "20260822130000"

    # Pre-populate session in SQLite with missing translations
    adapter.save_session(
        session_zid=test_zid,
        slug="progressive-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="The fox jumps.",
        sentences=[{"sentence_index": 1, "sentence_source": "The fox jumps.", "sentence_destination": None}],
        words=[{"sentence_index": 1, "token_order": 0, "quotation": "fox", "lemma": "fox", "word_destination": None}],
    )

    # Mock translation provider
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Лиса прыгает."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: f"{l}_ru" for l in lemmas})
    monkeypatch.setattr(desk, "run_headless_intellifiller", lambda *a, **kw: None)

    # Execute cmd_progressive_worker stage=all
    class Args:
        tsv = str(results_dir / f"{test_zid}-progressive-test.tsv")
        zid = test_zid
        stage = "all"
        target_lang = "ru"
        config = str(cfg_file)
        text_mode = "single"
        skip_intellifiller = True

    desk.cmd_progressive_worker(Args())

    # Verify no marker files on disk
    marker_files = list(results_dir.glob("*.done")) + list(results_dir.glob("*.txt"))
    assert len(marker_files) == 0

    # Verify SQLite database contains updated translation
    with adapter.db.get_connection(zid=test_zid) as conn:
        sents = conn.execute("SELECT * FROM sentences WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(sents) == 1
        assert sents[0]["sentence_destination"] == "Лиса прыгает."

        words = conn.execute("SELECT * FROM words WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(words) == 1
        assert words[0]["word_destination"] == "fox_ru"


def test_fs_free_repeat_lookup_fast_cache_hit_zero_disk_writes(monkeypatch, tmp_path):
    """
    Verifies that launching the same text a second time hits the SQLite cache,
    bypasses spaCy tokenization subprocess, and produces 0 persistent TSV files in results/.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    db_file = tmp_path / "kardenwort_fs_repeat.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = results_dir

    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_file))
    config.set("storage", "fallback_to_tsv", "true")
    config.set("storage", "cache_ttl_seconds", "86400")
    resolved_paths["storage_backend"] = "sqlite"
    resolved_paths["sqlite_db_path"] = db_file.resolve()

    # Configure wordfill enabled with dummy rule
    wordfill_cfg = {"enabled": True, "rules": []}

    spawned_processes = []
    def mock_spawn(*args, **kwargs):
        spawned_processes.append(args)
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        return mock_proc

    monkeypatch.setattr(subprocess, "Popen", mock_spawn)

    core_subprocesses = []
    def mock_run(*args, **kwargs):
        core_subprocesses.append(args)
        cmd = args[0]
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            "Quotation", "WordSource", "WordSource2", "WordSourceInflectedForm", "WordSourceInflectedForm2",
            "WordDestination", "SentenceSourceIndex", "SentenceSource", "SentenceDestination",
            "WordSourceMorphologyAI", "WordSourceIPA", "DeskSelected", "LeitnerBox",
        ]
        rows = [
            ["Werde", "Werden", "Werden", "Werde", "Werde", "Становиться", "1", "Werde Fahrer.", "Стань водителем.", "", "", "0", "1"],
            ["Fahrer", "Fahrer", "Fahrer", "Fahrer", "Fahrer", "Водитель", "1", "Werde Fahrer.", "Стань водителем.", "", "", "0", "1"],
        ]
        with open(out_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Стань водителем."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: f"{l}_ru" for l in lemmas})
    monkeypatch.setattr(desk, "run_headless_intellifiller", lambda *a, **kw: None)

    # 1. First launch
    test_zid_1 = "20260822140001"
    html_1 = desk.run_render_flow(
        text="The quick fox.",
        language="en",
        zid=test_zid_1,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        wordfill_cfg=wordfill_cfg,
    )
    assert len(html_1) > 0
    assert len(core_subprocesses) == 1

    # 2. Second launch of the exact same text
    test_zid_2 = "20260822140002"
    html_2 = desk.run_render_flow(
        text="The quick fox.",
        language="en",
        zid=test_zid_2,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        wordfill_cfg=wordfill_cfg,
    )
    assert len(html_2) > 0
    # Core spaCy subprocess must NOT have been called again (cache hit!)
    assert len(core_subprocesses) == 1, "Expected second launch to hit SQLite cache without running spaCy subprocess"

    # Verify zero TSV, TXT, or lock files on disk in results/
    created_files = [f for f in results_dir.iterdir() if f.is_file() and not f.name.endswith(".log")]
    assert len(created_files) == 0, f"Expected 0 persistent files in results/, found: {created_files}"


def test_fs_free_reprocess_and_retext_sqlite_mode(monkeypatch, tmp_path):
    """
    Verifies that cmd_reprocess, cmd_reprocess_worker, cmd_retext, cmd_retext_worker,
    and cmd_export function cleanly in SQLite mode without existing TSV files in results/.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    db_file = tmp_path / "kardenwort_fs_reprocess.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = results_dir
    resolved_paths["favorites_output_dir"] = fav_dir

    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_file))
    config.set("storage", "fallback_to_tsv", "true")
    config.set("storage", "cache_ttl_seconds", "86400")
    config.set("settings", "favorites_output_dir", str(fav_dir))
    config.set("settings", "send_to_anki_after_export", "false")
    config.set("settings", "save_to_favorites_on_export", "true")
    config.set("pipeline", "lemma_reprocess_provider", "google")
    resolved_paths["storage_backend"] = "sqlite"
    resolved_paths["sqlite_db_path"] = db_file.resolve()

    anki_mapping_path = tmp_path / "anki-mapping.ini"
    anki_mapping_path.write_text("""[fields]
WordSource = lemma
WordDestination = word_translation
SentenceSourceIndex = sentence_index
SentenceSource = sentence_source
SentenceDestination = sentence_destination
DeskSelected = selected
""", encoding="utf-8")
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(exist_ok=True)
    (ws_dir / "config.ini").write_text("[settings]\n", encoding="utf-8")

    if not config.has_section("environment"):
        config.add_section("environment")
    config.set("environment", "kardenwort_workspace", str(ws_dir))
    config.set("environment", "kardenwort_python", sys.executable)
    resolved_paths["kardenwort_workspace"] = ws_dir
    resolved_paths["kardenwort_python"] = sys.executable

    cfg_file = tmp_path / "reprocess_config.ini"
    with open(cfg_file, "w", encoding="utf-8") as f:
        config.write(f)

    test_zid = "20260822150000"
    adapter = get_storage_adapter(config, resolved_paths)

    # 1. Populate initial SQLite session
    adapter.save_session(
        session_zid=test_zid,
        slug="reprocess-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="The quick fox.",
        sentences=[{"sentence_index": 1, "sentence_source": "The quick fox.", "sentence_destination": "Быстрая лиса."}],
        words=[
            {"sentence_index": 1, "token_order": 0, "quotation": "quick", "lemma": "quick", "word_destination": "быстрый", "selected": 0},
            {"sentence_index": 1, "token_order": 1, "quotation": "fox", "lemma": "fox", "word_destination": "лиса", "selected": 1},
        ],
    )

    # 2. Test cmd_reprocess with selection manifest
    manifest_file = tmp_path / "manifest_reproc.json"
    manifest_data = {
        "selected_row_ids": [0],
        "zid": test_zid,
        "tsv_path": str(results_dir / f"{test_zid}-reprocess-test.en.tsv"),
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    class ReprocessArgs:
        selection_manifest = str(manifest_file)
        language = "en"
        config = str(cfg_file)
        trace_id = f"{test_zid}:reprocess:test"

    desk.cmd_reprocess(ReprocessArgs())

    # 3. Test cmd_reprocess_worker fast path
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: f"{l}_reprocessed" for l in lemmas})
    class BatchWorkerArgs:
        tsv = str(results_dir / f"{test_zid}-reprocess-test.en.tsv")
        prompt = "en_prompt"
        rows = "0"
        zid = test_zid
        trace_id = f"{test_zid}:reprocess:worker"
        config = str(cfg_file)

    desk.cmd_reprocess_worker(BatchWorkerArgs())

    # Check updated word in SQLite
    with adapter.db.get_connection(zid=test_zid) as conn:
        words = conn.execute("SELECT * FROM words WHERE session_zid = ? ORDER BY token_order", (test_zid,)).fetchall()
        assert words[0]["word_destination"] == "quick_reprocessed"

    # 4. Test cmd_retext_worker
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Очень быстрая лиса."})
    class RetextWorkerArgs:
        tsv = str(results_dir / f"{test_zid}-reprocess-test.en.tsv")
        language = "en"
        text_mode = "single"
        zid = test_zid
        trace_id = f"{test_zid}:retext:worker"
        config = str(cfg_file)

    desk.cmd_retext_worker(RetextWorkerArgs())

    # Check updated sentence in SQLite
    with adapter.db.get_connection(zid=test_zid) as conn:
        sents = conn.execute("SELECT * FROM sentences WHERE session_zid = ?", (test_zid,)).fetchall()
        assert sents[0]["sentence_destination"] == "Очень быстрая лиса."

    # 5. Test cmd_export via selection manifest
    export_manifest_file = tmp_path / "manifest_export.json"
    export_manifest_data = {
        "selected_row_ids": [1],
        "zid": test_zid,
        "tsv_path": str(results_dir / f"{test_zid}-reprocess-test.en.tsv"),
    }
    export_manifest_file.write_text(json.dumps(export_manifest_data), encoding="utf-8")

    class ExportArgs:
        selection_manifest = str(export_manifest_file)
        language = "en"
        config = str(cfg_file)
        zid = test_zid
        trace_id = f"{test_zid}:export:test"

    desk.cmd_export(ExportArgs())

    # Verify favorites file was exported to favorites/
    fav_files = list(fav_dir.glob("*.tsv"))
    assert len(fav_files) == 1

    # Verify zero TSV files were left in results/
    results_tsvs = list(results_dir.glob("*.tsv"))
    assert len(results_tsvs) == 0
