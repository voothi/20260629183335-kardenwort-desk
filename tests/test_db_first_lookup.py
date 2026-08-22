import subprocess
import configparser
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import kardenwort_desk as desk
from kardenwort_desk import (
    get_storage_adapter,
    SqliteStorageAdapter,
    TsvStorageAdapter,
)
from tests.test_lookup import setup_test_env


def test_db_first_lookup_flow_sqlite_persistence(monkeypatch, tmp_path):
    """
    Verifies that running run_lookup_flow with SQLite storage backend persists
    the session directly into SQLite database tables (sessions, sentences, words)
    without intermediate TSV disk writes.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    
    db_file = tmp_path / "kardenwort_test.db"
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_file))
    config.set("storage", "fallback_to_tsv", "true")
    resolved_paths["storage_backend"] = "sqlite"
    resolved_paths["sqlite_db_path"] = db_file.resolve()

    # Track save_tsv_rows_safely calls
    saved_tsv_calls = []
    orig_save_tsv = desk.save_tsv_rows_safely

    def spy_save_tsv(path, comments, headers, data_rows):
        saved_tsv_calls.append(str(path))
        return orig_save_tsv(path, comments, headers, data_rows)

    monkeypatch.setattr(desk, "save_tsv_rows_safely", spy_save_tsv)

    # Mock subprocess.run for core lemmatization
    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            "WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI",
            "SentenceSourceIndex", "SentenceSourceContextLeft", "SentenceSource", "SentenceSourceContextRight",
            "SentenceDestinationContextLeft", "SentenceDestination", "SentenceDestinationContextRight",
            "SentenceDestination2ContextLeft", "SentenceDestination2", "SentenceDestination2ContextRight",
            "SentenceSourceWordlist", "SentenceSourceCloze", "SentenceSourceRewriteAISentenceSource",
            "SentenceSourceRewriteAISentenceDestination"
        ]
        rows = [
            ["apple", "", "", "", "1", "", "I like apples.", "", "", "", "", "", "", "", "", "", "", ""],
            ["banana", "", "", "", "2", "", "Bananas are yellow.", "", "", "", "", "", "", "", "", "", "", ""]
        ]
        import csv
        with open(out_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Я люблю яблоки.", 1: "Бананы желтые."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: f"{l}_ru" for l in lemmas})

    test_zid = "20260822100000"
    res = desk.run_lookup_flow(
        text="I like apples. Bananas are yellow.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid=test_zid,
        text_mode="sentences"
    )

    # In-memory execution: No intermediate save_tsv_rows_safely calls during translation/wordfill stages
    assert len(saved_tsv_calls) == 0

    # Verify directly stored in SQLite DB
    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, SqliteStorageAdapter)
    
    with adapter.db.get_connection(zid=test_zid) as conn:
        session_row = conn.execute("SELECT * FROM sessions WHERE zid = ?", (test_zid,)).fetchone()
        assert session_row is not None
        assert session_row["source_language"] == "en"
        assert session_row["target_language"] == "ru"
        assert session_row["text_mode"] == "sentences"

        sentence_rows = conn.execute("SELECT * FROM sentences WHERE session_zid = ? ORDER BY sentence_index ASC", (test_zid,)).fetchall()
        assert len(sentence_rows) == 2
        assert sentence_rows[0]["sentence_source"] == "I like apples."
        assert sentence_rows[0]["sentence_destination"] == "Я люблю яблоки."
        assert sentence_rows[1]["sentence_source"] == "Bananas are yellow."
        assert sentence_rows[1]["sentence_destination"] == "Бананы желтые."

        word_rows = conn.execute("SELECT * FROM words WHERE session_zid = ? ORDER BY token_order ASC", (test_zid,)).fetchall()
        assert len(word_rows) == 2
        assert word_rows[0]["lemma"] == "apple"
        assert word_rows[0]["word_destination"] == "apple_ru"
        assert word_rows[1]["lemma"] == "banana"
        assert word_rows[1]["word_destination"] == "banana_ru"

    # Verify session restore reconstructs full headers and data
    restored = adapter.restore_session(test_zid)
    assert restored is not None
    assert len(restored["data_rows"]) == 2
    headers_lower = [h.lower() for h in restored["headers"]]
    lemma_col = headers_lower.index("wordsource")
    trans_col = headers_lower.index("worddestination")
    assert restored["data_rows"][0][lemma_col] == "apple"
    assert restored["data_rows"][0][trans_col] == "apple_ru"
    assert restored["data_rows"][1][lemma_col] == "banana"
    assert restored["data_rows"][1][trans_col] == "banana_ru"


def test_db_first_lookup_flow_tsv_fallback(monkeypatch, tmp_path):
    """
    Verifies that when storage backend is explicitly set to tsv,
    run_lookup_flow works with TsvStorageAdapter without error.
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    config.add_section("storage")
    config.set("storage", "backend", "tsv")
    resolved_paths["storage_backend"] = "tsv"

    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        headers = ["WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "SentenceDestination"]
        rows = [["orange", "", "1", "Oranges are sweet.", ""]]
        import csv
        with open(out_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Апельсины сладкие."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {"orange": "апельсин"})

    test_zid = "20260822100001"
    res = desk.run_lookup_flow(
        text="Oranges are sweet.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid=test_zid,
        text_mode="single"
    )

    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, TsvStorageAdapter)
    
    comments, headers, data_rows, sent_trans = res
    assert data_rows[0][0] == "orange"
    assert data_rows[0][1] == "апельсин"


def test_db_first_lookup_flow_export_format(monkeypatch, tmp_path):
    """
    Verifies that dynamic TSV export from a session persisted directly to SQLite
    matches expected full column format (82+ columns when standard mapping is used).
    """
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    
    # Use real anki-mapping.ini from workspace
    repo_mapping = Path(__file__).resolve().parent.parent / "anki-mapping.ini"
    resolved_paths["anki_mapping_file"] = str(repo_mapping)
    
    db_file = tmp_path / "kardenwort_export_test.db"
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
        headers = ["WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "SentenceDestination"]
        rows = [["cherry", "", "1", "Cherries are red.", ""]]
        import csv
        with open(out_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Вишни красные."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {"cherry": "вишня"})

    test_zid = "20260822100002"
    desk.run_lookup_flow(
        text="Cherries are red.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid=test_zid,
        text_mode="single"
    )

    adapter = get_storage_adapter(config, resolved_paths)
    restored = adapter.restore_session(test_zid)
    assert restored is not None
    # Verify 82+ standard columns reconstructed
    assert len(restored["headers"]) >= 82
    assert "Quotation" in restored["headers"]
    assert "WordSource" in restored["headers"]
    assert "SentenceSource" in restored["headers"]
    assert "SentenceDestination" in restored["headers"]
    assert len(restored["data_rows"]) == 1
    
    ws_idx = restored["headers"].index("WordSource")
    wd_idx = restored["headers"].index("WordDestination")
    assert restored["data_rows"][0][ws_idx] == "cherry"
    assert restored["data_rows"][0][wd_idx] == "вишня"

