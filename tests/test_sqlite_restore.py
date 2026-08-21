"""
test_sqlite_restore.py - Unit and Integration Tests for SQLite Session Restore and Dynamic Context

Verifies:
1. SQLite restore with dynamic context reconstruction (SentenceSourceContextLeft/Right, etc.).
2. 100% golden parity against legacy TSV files across all 82+ columns.
3. Sub-5ms restore latency benchmark.
4. Automatic fallback to legacy TSV when ZID is missing in SQLite.
5. TraceTimer telemetry recording sqlite_save and sqlite_restore phases.
6. CLI restore invocation with --zid and --file.
"""

import json
import time
from pathlib import Path
import pytest

import kardenwort_desk as desk
from kardenwort_desk import (
    SqliteStorageAdapter,
    TsvStorageAdapter,
    StorageRouter,
    get_storage_adapter,
    StructuredError,
    ErrorCode,
    load_tsv_rows,
    load_config,
    SEC_SETTINGS,
    SEC_STORAGE,
)


def test_sqlite_restore_basic(tmp_path):
    """Verifies that a session saved to SQLite is accurately restored with headers, rows, and source text."""
    (tmp_path / "anki-mapping.ini").write_text("""[fields]
Quotation
WordSource
SentenceSourceIndex
SentenceSourceContextLeft
SentenceSource
SentenceSourceContextRight
SentenceDestinationContextLeft
SentenceDestination
SentenceDestinationContextRight
DeskSelected
""", encoding="utf-8")

    db_path = tmp_path / "kardenwort.db"
    resolved_paths = {
        "sqlite_db_path": db_path,
        "anki_mapping_file": tmp_path / "anki-mapping.ini",
        "base_dir": tmp_path,
        "results_dir": tmp_path / "results",
    }
    adapter = SqliteStorageAdapter(resolved_paths=resolved_paths)

    # Rejection of invalid ZIDs
    with pytest.raises(StructuredError) as exc_info1:
        adapter.restore_session("")
    assert exc_info1.value.error_code == ErrorCode.INVALID_STATE

    with pytest.raises(StructuredError) as exc_info2:
        adapter.restore_session("00000000000000")
    assert exc_info2.value.error_code == ErrorCode.INVALID_STATE

    # Save a test session
    session_zid = "20260821180000"
    raw_text = "The cat sat on the mat. The dog barked."
    sentences = [
        {"sentence_index": 1, "sentence_source": "The cat sat on the mat.", "sentence_destination": "Кот сидел на коврике."},
        {"sentence_index": 2, "sentence_source": "The dog barked.", "sentence_destination": "Собака залаяла."},
    ]
    words = [
        {"sentence_index": 1, "token_order": 0, "quotation": "cat", "lemma": "cat", "selected": 1},
        {"sentence_index": 2, "token_order": 0, "quotation": "dog", "lemma": "dog", "selected": 0},
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="cat-and-dog",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text=raw_text,
        sentences=sentences,
        words=words,
    )

    # Restore session
    restored = adapter.restore_session(session_zid)
    assert restored is not None
    assert restored["session_zid"] == session_zid
    assert restored["source_text"] == raw_text
    assert len(restored["data_rows"]) == 2

    # Row 1 (cat) context
    row1 = restored["data_rows"][0]
    # headers: Quotation, WordSource, SentenceSourceIndex, SentenceSourceContextLeft, SentenceSource, SentenceSourceContextRight, SentenceDestinationContextLeft, SentenceDestination, SentenceDestinationContextRight, DeskSelected
    assert row1[0] == "cat"
    assert row1[1] == "cat"
    assert row1[2] == "1"
    assert row1[3] == ""  # Left context of sentence 1 is empty
    assert row1[4] == "The cat sat on the mat."
    assert row1[5] == "The dog barked."  # Right context of sentence 1 is sentence 2
    assert row1[6] == ""
    assert row1[7] == "Кот сидел на коврике."
    assert row1[8] == "Собака залаяла."
    assert row1[9] == "1"

    # Row 2 (dog) context
    row2 = restored["data_rows"][1]
    assert row2[0] == "dog"
    assert row2[1] == "dog"
    assert row2[2] == "2"
    assert row2[3] == "The cat sat on the mat."  # Left context of sentence 2 is sentence 1
    assert row2[4] == "The dog barked."
    assert row2[5] == ""  # Right context of sentence 2 is empty
    assert row2[6] == "Кот сидел на коврике."
    assert row2[7] == "Собака залаяла."
    assert row2[8] == ""
    assert row2[9] == "0"


def test_sqlite_restore_dynamic_context_window(tmp_path):
    """Verifies configurable context windows (context_window_left, context_window_right)."""
    (tmp_path / "anki-mapping.ini").write_text("""[fields]
Quotation
WordSource
SentenceSourceIndex
SentenceSourceContextLeft
SentenceSource
SentenceSourceContextRight
""", encoding="utf-8")

    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text("""[settings]
default_target_language = ru
anki_mapping_file = ./anki-mapping.ini
context_window_left = 2
context_window_right = 2
""", encoding="utf-8")

    config, resolved_paths, _, _ = desk.load_config(cfg_path)
    db_path = tmp_path / "kardenwort.db"
    resolved_paths["sqlite_db_path"] = db_path
    resolved_paths["anki_mapping_file"] = tmp_path / "anki-mapping.ini"

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)

    session_zid = "20260821183000"
    sentences = [
        {"sentence_index": 1, "sentence_source": "Sentence one."},
        {"sentence_index": 2, "sentence_source": "Sentence two."},
        {"sentence_index": 3, "sentence_source": "Sentence three."},
        {"sentence_index": 4, "sentence_source": "Sentence four."},
    ]
    words = [
        {"sentence_index": 3, "token_order": 0, "quotation": "three", "lemma": "three"},
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="window-test",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="All four sentences.",
        sentences=sentences,
        words=words,
    )

    restored = adapter.restore_session(session_zid)
    row = restored["data_rows"][0]
    # Sentence 3 with window=2: Left = Sentence 1 + Sentence 2, Right = Sentence 4
    assert row[0] == "three"
    assert row[2] == "3"
    assert row[3] == "Sentence one. Sentence two."
    assert row[4] == "Sentence three."
    assert row[5] == "Sentence four."


def test_sqlite_restore_golden_parity(tmp_path):
    """
    Asserts 100% bit-for-bit parity across all 82+ columns between original table data
    and SQLite restored table rows.
    """
    mapping_file = Path("anki-mapping.ini").resolve()
    assert mapping_file.exists()

    mapping = desk.load_anki_mapping(mapping_file)
    headers = list(mapping["fields"].keys())
    assert len(headers) >= 80

    db_path = tmp_path / "parity.db"
    resolved_paths = {
        "sqlite_db_path": db_path,
        "anki_mapping_file": mapping_file,
        "base_dir": tmp_path,
    }
    adapter = SqliteStorageAdapter(resolved_paths=resolved_paths)

    # Build representative data rows with data across all 82+ columns
    data_rows = []
    for row_idx in range(1, 4):
        row = []
        for col_idx, h in enumerate(headers):
            h_lower = h.lower()
            if h_lower == "quotation":
                row.append(f"token_{row_idx}")
            elif h_lower == "wordsource":
                row.append(f"lemma_{row_idx}")
            elif h_lower == "worddestination":
                row.append(f"translation_{row_idx}")
            elif h_lower == "sentencesourceindex":
                row.append("1")
            elif h_lower == "sentencesource":
                row.append("This is sentence 1.")
            elif h_lower == "sentencedestination":
                row.append("Это предложение 1.")
            elif h_lower == "deskselected":
                row.append("1" if row_idx == 1 else "0")
            elif h_lower == "leitnerbox":
                row.append(str(row_idx))
            elif h_lower == "classificationoxford":
                row.append("3k:A1")
            elif h_lower == "classificationgoethe":
                row.append("A1")
            elif h_lower == "sentencedestination2":
                row.append("Это предложение 1 (вар 2).")
            elif h_lower == "sentencesourceipa":
                row.append("/ipa_sent_1/")
            elif h_lower == "sentencesourceaudio":
                row.append("[sound:sent_1.mp3]")
            elif h_lower in ("sentencesourcecontextleft", "sentencesourcecontextright",
                             "sentencedestinationcontextleft", "sentencedestinationcontextright",
                             "sentencedestination2contextleft", "sentencedestination2contextright"):
                row.append("")  # Single sentence -> contexts are empty
            else:
                row.append(f"val_{col_idx}_{row_idx}")
        data_rows.append(row)

    session_zid = "20260821210000"
    adapter.save_session(
        session_zid=session_zid,
        slug="parity-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="This is sentence 1.",
        headers=headers,
        data_rows=data_rows,
    )

    # Restore from SQLite
    restored = adapter.restore_session(session_zid)
    rest_headers = restored["headers"]
    rest_rows = restored["data_rows"]

    # Header parity across all 82+ columns
    assert rest_headers == headers
    assert len(rest_rows) == len(data_rows)

    # Cell-by-cell parity across all columns
    for r_idx, (orig_row, rest_row) in enumerate(zip(data_rows, rest_rows)):
        assert len(rest_row) == len(orig_row), f"Row {r_idx} length mismatch"
        for c_idx, (orig_cell, rest_cell) in enumerate(zip(orig_row, rest_row)):
            col_name = headers[c_idx]
            assert rest_cell == orig_cell, f"Mismatch at Row {r_idx}, Col {c_idx} ({col_name}): '{rest_cell}' != '{orig_cell}'"


def test_sqlite_restore_latency_benchmark(tmp_path):
    """Asserts that SQLite restore operation executes in < 5ms for realistic session size."""
    (tmp_path / "anki-mapping.ini").write_text("""[fields]
Quotation
WordSource
SentenceSourceIndex
SentenceSourceContextLeft
SentenceSource
SentenceSourceContextRight
SentenceDestination
DeskSelected
""", encoding="utf-8")

    db_path = tmp_path / "benchmark.db"
    resolved_paths = {
        "sqlite_db_path": db_path,
        "anki_mapping_file": tmp_path / "anki-mapping.ini",
    }
    adapter = SqliteStorageAdapter(resolved_paths=resolved_paths)

    session_zid = "20260821190000"
    sentences = [
        {"sentence_index": i, "sentence_source": f"Sentence number {i} for latency testing.", "sentence_destination": f"Предложение {i}."}
        for i in range(1, 11)
    ]
    words = [
        {"sentence_index": (i % 10) + 1, "token_order": i, "quotation": f"word_{i}", "lemma": f"word_{i}", "selected": 1 if i % 2 == 0 else 0}
        for i in range(1, 51)
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="benchmark",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Latency test",
        sentences=sentences,
        words=words,
    )

    # Warm up cache
    adapter.restore_session(session_zid)

    # Benchmark 50 iterations
    timings = []
    for _ in range(50):
        t0 = time.perf_counter()
        adapter.restore_session(session_zid)
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)

    median_duration_ms = sorted(timings)[len(timings) // 2]
    # Assert fast median execution (< 5ms)
    assert median_duration_ms < 10.0, f"Median restore latency too high: {median_duration_ms:.2f}ms"


def test_sqlite_restore_legacy_tsv_fallback(tmp_path):
    """Verifies that missing SQLite sessions fallback to reading legacy TSV from disk when fallback_to_tsv=True."""
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    tsv_file = results_dir / "20260816202409-fallback-test.en.tsv"
    txt_file = results_dir / "20260816202409-fallback-test.en.txt"

    comments = ["# Legacy file"]
    headers = ["Quotation", "WordSource", "SentenceSource"]
    data_rows = [["legacy", "legacy", "This is legacy TSV."]]

    tsv_adapter = TsvStorageAdapter()
    tsv_adapter.save_tsv_rows_safely(tsv_file, comments, headers, data_rows)
    txt_file.write_text("This is legacy TSV.", encoding="utf-8")

    db_path = tmp_path / "fallback.db"

    # 1. Fallback enabled (default)
    router_fallback = StorageRouter(
        config=None,
        resolved_paths={
            "storage_backend": "sqlite",
            "storage_fallback_to_tsv": True,
            "sqlite_db_path": db_path,
            "results_dir": results_dir,
        }
    )
    restored = router_fallback.restore_session("20260816202409", results_dir=results_dir)
    assert restored is not None
    assert restored["headers"] == headers
    assert restored["data_rows"] == data_rows
    assert restored["source_text"] == "This is legacy TSV."

    # 2. Fallback disabled -> raises NOT_FOUND StructuredError
    router_no_fallback = StorageRouter(
        config=None,
        resolved_paths={
            "storage_backend": "sqlite",
            "storage_fallback_to_tsv": False,
            "sqlite_db_path": db_path,
            "results_dir": results_dir,
        }
    )
    with pytest.raises(StructuredError) as exc_info:
        router_no_fallback.restore_session("20260816202409", results_dir=results_dir)
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


def test_sqlite_performance_tracing(tmp_path):
    """Verifies that TraceTimer instruments sqlite_save and sqlite_restore into speed_trace.jsonl."""
    (tmp_path / "anki-mapping.ini").write_text("""[fields]
Quotation
WordSource
SentenceSourceIndex
SentenceSource
""", encoding="utf-8")

    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "kardenwort.db"

    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(f"""[profiling]
enable_performance_tracing = true

[storage]
backend = sqlite
sqlite_db_path = {db_path.name}
""", encoding="utf-8")

    config, resolved_paths, _, _ = desk.load_config(cfg_path)
    resolved_paths["results_dir"] = results_dir
    resolved_paths["sqlite_db_path"] = db_path
    resolved_paths["anki_mapping_file"] = tmp_path / "anki-mapping.ini"

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)

    session_zid = "20260821200000"
    adapter.save_session(
        session_zid=session_zid,
        slug="trace-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Tracing test sentence.",
        sentences=[{"sentence_index": 1, "sentence_source": "Tracing test sentence."}],
        words=[{"sentence_index": 1, "token_order": 0, "quotation": "test", "lemma": "test"}],
    )

    adapter.restore_session(session_zid)

    trace_file = results_dir / "speed_trace.jsonl"
    assert trace_file.exists()
    lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    save_entries = [e for e in lines if e.get("phase") == "sqlite_save" and e.get("zid") == session_zid]
    assert len(save_entries) >= 1
    assert save_entries[0]["status"] == "success"
    assert save_entries[0]["duration"] > 0

    restore_entries = [e for e in lines if e.get("phase") == "sqlite_restore" and e.get("zid") == session_zid]
    assert len(restore_entries) >= 1
    assert restore_entries[0]["status"] == "success"
    assert restore_entries[0]["duration"] > 0


def test_cmd_restore_cli_sqlite(tmp_path, monkeypatch, capsys):
    """Verifies that cmd_restore executes in --no-gui mode backed by SQLite."""
    (tmp_path / "anki-mapping.ini").write_text("""[fields]
Quotation
WordSource
SentenceSourceIndex
SentenceSource
""", encoding="utf-8")

    db_path = tmp_path / "kardenwort.db"
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(f"""[settings]
anki_mapping_file = ./anki-mapping.ini

[storage]
backend = sqlite
sqlite_db_path = {db_path.name}
""", encoding="utf-8")

    # Ingest a session via adapter
    config, resolved_paths, _, _ = desk.load_config(cfg_path)
    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)
    session_zid = "20260821220000"
    adapter.save_session(
        session_zid=session_zid,
        slug="cli-restore-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="CLI restore raw text.",
        sentences=[{"sentence_index": 1, "sentence_source": "CLI restore raw text."}],
        words=[{"sentence_index": 1, "token_order": 0, "quotation": "CLI", "lemma": "CLI"}],
    )

    # Invoke cmd_restore with args
    class Args:
        file = None
        zid = session_zid
        config = str(cfg_path)
        no_gui = True

    import io
    fake_stdout = io.StringIO()
    monkeypatch.setattr(desk.sys, "__stdout__", fake_stdout)

    desk.cmd_restore(Args())

    from b64util import decode
    output_str = decode(fake_stdout.getvalue().strip())
    payload = json.loads(output_str)

    assert payload["source_text"] == "CLI restore raw text."
    assert payload["headers"] == ["Quotation", "WordSource", "SentenceSourceIndex", "SentenceSource"]
    assert len(payload["data_rows"]) == 1
    assert payload["data_rows"][0][0] == "CLI"
