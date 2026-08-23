import os
import time
from pathlib import Path
import configparser
import pytest
from kardenwort_desk import (
    sort_rows_by_frequency,
    load_lemma_frequency_index_cached,
    get_lemma_sort_key,
    _LEMMA_FREQUENCY_INDEX_CACHE,
    SEC_LANGUAGES,
)


def test_load_lemma_frequency_index_cached_and_mtime_invalidation(tmp_path):
    idx_file = tmp_path / "freq_index.txt"
    idx_file.write_text("the\nbe\nto\nof\nand\na\nin\nthat\nhave\nI\n", encoding="utf-8")

    # Initial load
    idx1 = load_lemma_frequency_index_cached(idx_file)
    assert idx1["the"] == 0
    assert idx1["I"] == 9
    assert len(idx1) == 10

    # Cache hit check
    assert str(idx_file.resolve()) in _LEMMA_FREQUENCY_INDEX_CACHE

    # Modify file and advance mtime
    time.sleep(0.02)
    idx_file.write_text("apple\nbanana\ncherry\n", encoding="utf-8")
    os.utime(idx_file, (time.time() + 1, time.time() + 1))

    idx2 = load_lemma_frequency_index_cached(idx_file)
    assert idx2["apple"] == 0
    assert idx2["banana"] == 1
    assert "the" not in idx2


def test_sort_rows_by_frequency_zero_subprocess_and_speed(tmp_path, monkeypatch):
    idx_file = tmp_path / "en_index.txt"
    # Write 1000 lemmas
    lemmas = [f"word_{i}" for i in range(1000)]
    idx_file.write_text("\n".join(lemmas), encoding="utf-8")

    cfg = configparser.ConfigParser()
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_lemma_index", str(idx_file))

    resolved_paths = {
        "kardenwort_workspace": str(tmp_path),
    }

    # Prepare rows in reverse order + unknown words
    data_rows = [[f"word_{999 - i}", f"sent_{i}"] for i in range(100)]
    data_rows.append(["unknown_word_zzz", "sent_unknown"])
    data_rows.append(["unknown_word_aaa", "sent_unknown"])

    headers = ["WordSource", "SentenceSource"]

    # Intercept subprocess.run to guarantee zero subprocess calls
    def forbidden_subprocess(*args, **kwargs):
        pytest.fail("subprocess.run was called when in-memory sorting should be used!")

    monkeypatch.setattr("subprocess.run", forbidden_subprocess)

    # Warm-up cache
    load_lemma_frequency_index_cached(idx_file)

    # Measure sorting time
    t0 = time.perf_counter()
    sorted_rows = sort_rows_by_frequency(
        data_rows=data_rows,
        headers=headers,
        lang="en",
        config=cfg,
        resolved_paths=resolved_paths,
        role_fields={"lemma": "WordSource"}
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert sorted_rows[0][0] == "word_900"  # lowest index among the 100
    assert sorted_rows[99][0] == "word_999"
    # Unranked words placed at the end sorted alphabetically
    assert sorted_rows[100][0] == "unknown_word_aaa"
    assert sorted_rows[101][0] == "unknown_word_zzz"

    # Execution should be sub-millisecond (< 5ms on slow CI)
    assert elapsed_ms < 10.0


def test_sort_rows_by_frequency_graceful_on_missing_index(tmp_path):
    cfg = configparser.ConfigParser()
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_lemma_index", "non_existent_file.txt")

    resolved_paths = {"kardenwort_workspace": str(tmp_path)}
    headers = ["WordSource", "SentenceSource"]
    data_rows = [["zebra", "s1"], ["apple", "s2"]]

    # Should safely return rows unmodified
    result = sort_rows_by_frequency(
        data_rows=data_rows,
        headers=headers,
        lang="en",
        config=cfg,
        resolved_paths=resolved_paths
    )
    assert result == data_rows


def test_the_cut_preserves_pre_computed_frequency_order_and_restores_instantly(tmp_path):
    from kardenwort_db import KardenwortDB
    from kardenwort_desk import SqliteStorageAdapter

    db_path = tmp_path / "kardenwort.db"
    idx_file = tmp_path / "en_index.txt"
    idx_file.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    cfg = configparser.ConfigParser()
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_lemma_index", str(idx_file))
    cfg.add_section("storage")
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(db_path))

    resolved_paths = {
        "sqlite_db_path": str(db_path),
        "kardenwort_workspace": str(tmp_path),
    }

    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    adapter = SqliteStorageAdapter(config=cfg, resolved_paths=resolved_paths)

    headers = [
        "Quotation", "WordSource", "WordSourceInflectedForm", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "DeskSelected"
    ]
    # Unsorted rows for the child window: gamma, zebra, alpha, beta
    sub_rows = [
        ["gamma", "gamma", "gamma", "1", "Sentence", "Sent Trans", "1"],
        ["zebra", "zebra", "zebra", "1", "Sentence", "Sent Trans", "1"],
        ["alpha", "alpha", "alpha", "1", "Sentence", "Sent Trans", "1"],
        ["beta", "beta", "beta", "1", "Sentence", "Sent Trans", "1"],
    ]
    role_fields = {
        "lemma": "WordSource",
        "sentence_source": "SentenceSource",
        "sentence_destination": "SentenceDestination",
    }

    # Pre-sort as done in the_cut
    sorted_sub_rows = sort_rows_by_frequency(
        data_rows=sub_rows,
        headers=headers,
        lang="en",
        config=cfg,
        resolved_paths=resolved_paths,
        role_fields=role_fields,
    )

    child_zid = "20260823021700"
    child_sentences = [{
        "session_zid": child_zid,
        "sentence_index": 1,
        "sentence_source": "Alpha beta gamma zebra sentence.",
        "sentence_destination": "Alpha beta gamma zebra translation.",
    }]

    adapter.save_session(
        session_zid=child_zid,
        slug="child-sentence",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Alpha beta gamma zebra sentence.",
        headers=headers,
        data_rows=sorted_sub_rows,
        sentences=child_sentences,
        zid=child_zid,
    )

    # Measure restore_session timing and verify order preservation
    t0 = time.perf_counter()
    restored = adapter.restore_session(child_zid)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert restored is not None
    restored_lemmas = [r[headers.index("WordSource")] for r in restored["data_rows"]]
    assert restored_lemmas == ["alpha", "beta", "gamma", "zebra"]
    assert elapsed_ms < 10.0

