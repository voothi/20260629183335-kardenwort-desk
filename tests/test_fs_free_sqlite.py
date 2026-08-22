"""
Unit and integration tests verifying the FS-Free SQLite-First Architecture (20260822110235-fs-free-sqlite-first-architecture).
Verifies that single and multi-window lookups, progressive pipelines, and rendering flows create 0 persistent files in results/
and operate 100% through SQLite tables.
"""

import configparser
import csv
import json
import subprocess
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
