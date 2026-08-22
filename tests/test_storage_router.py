import tempfile
import configparser
from pathlib import Path
import pytest
import kardenwort_desk as desk
from kardenwort_desk import (
    get_storage_adapter,
    StorageAdapter,
    TsvStorageAdapter,
    SqliteStorageAdapter,
    SEC_STORAGE,
)


def test_config_storage_section_defaults(tmp_path):
    """Verifies that config loading provides correct storage defaults when [storage] is omitted."""
    (tmp_path / "anki-mapping.ini").write_text("[fields]\nQuotation\n", encoding="utf-8")
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text("""
[settings]
default_target_language = ru
anki_mapping_file = ./anki-mapping.ini
""", encoding="utf-8")

    config, resolved_paths, gd, wf = desk.load_config(cfg_path)
    assert resolved_paths.get("storage_backend") == "tsv"
    assert resolved_paths.get("storage_fallback_to_tsv") is True
    assert resolved_paths.get("sqlite_db_path") == (tmp_path / "data" / "kardenwort.db").resolve()

    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, TsvStorageAdapter)
    assert adapter.backend_name == "tsv"


def test_config_storage_section_sqlite(tmp_path):
    """Verifies that [storage] backend = sqlite config is parsed and instantiates SqliteStorageAdapter."""
    (tmp_path / "anki-mapping.ini").write_text("[fields]\nQuotation\n", encoding="utf-8")
    db_file = tmp_path / "custom.db"
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(f"""
[settings]
default_target_language = ru
anki_mapping_file = ./anki-mapping.ini

[storage]
backend = sqlite
sqlite_db_path = {db_file.name}
fallback_to_tsv = false
""", encoding="utf-8")


    config, resolved_paths, gd, wf = desk.load_config(cfg_path)
    assert resolved_paths.get("storage_backend") == "sqlite"
    assert resolved_paths.get("storage_fallback_to_tsv") is False
    assert resolved_paths.get("sqlite_db_path") == db_file.resolve()

    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, SqliteStorageAdapter)
    assert adapter.backend_name == "sqlite"


def test_cli_storage_override(tmp_path):
    """Verifies that CLI storage_override overrides the backend configured in config.ini."""
    (tmp_path / "anki-mapping.ini").write_text("[fields]\nQuotation\n", encoding="utf-8")
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text("""
[settings]
default_target_language = ru
anki_mapping_file = ./anki-mapping.ini

[storage]
backend = tsv
""", encoding="utf-8")

    config, resolved_paths, gd, wf = desk.load_config(cfg_path)
    
    # CLI override with sqlite
    adapter_sqlite = get_storage_adapter(config, resolved_paths, storage_override="sqlite")
    assert isinstance(adapter_sqlite, SqliteStorageAdapter)


    # CLI override with tsv
    adapter_tsv = get_storage_adapter(config, resolved_paths, storage_override="tsv")
    assert isinstance(adapter_tsv, TsvStorageAdapter)


def test_tsv_storage_adapter_file_operations(tmp_path):
    """Verifies TsvStorageAdapter row save/load and file lock operations."""
    adapter = TsvStorageAdapter()
    tsv_file = tmp_path / "test.tsv"
    
    comments = ["# test comment"]
    headers = ["Quotation", "WordSource", "SentenceSource"]
    data_rows = [["running", "run", "He was running fast."]]

    adapter.save_tsv_rows_safely(tsv_file, comments, headers, data_rows)
    assert tsv_file.exists()

    loaded_comments, loaded_headers, loaded_rows = adapter.load_tsv_rows(tsv_file)
    assert loaded_comments == comments
    assert loaded_headers == headers
    assert loaded_rows == data_rows

    # Verify file_lock context manager
    with adapter.file_lock(tsv_file):
        pass
    assert not tsv_file.with_suffix(".lock").exists()


def test_tsv_storage_adapter_session_operations(tmp_path):
    """Verifies TsvStorageAdapter save_session, load_session, and get_cached_session."""
    adapter = TsvStorageAdapter()
    tsv_file = tmp_path / "20260821120000-running-fast.en.tsv"
    
    comments = ["# session comment"]
    headers = ["Quotation", "WordSource", "SentenceSource"]
    data_rows = [["running", "run", "He was running fast."]]

    res = adapter.save_session(
        session_zid="20260821120000",
        slug="running-fast",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="He was running fast.",
        comments=comments,
        headers=headers,
        data_rows=data_rows,
        working_tsv_path=tsv_file,
    )
    assert res == tsv_file
    assert tsv_file.exists()

    # Load session
    session_data = adapter.load_session("20260821120000", working_tsv_path=tsv_file)
    assert session_data is not None
    assert session_data["headers"] == headers
    assert session_data["data_rows"] == data_rows

    # Cached session within TTL
    cached = adapter.get_cached_session("running-fast", "en", lookup_ttl_seconds=300, results_dir=tmp_path)
    assert cached == tsv_file

    # Expired TTL
    expired = adapter.get_cached_session("running-fast", "en", lookup_ttl_seconds=0, results_dir=tmp_path)
    assert expired is None


def test_sqlite_storage_adapter_save_session_normalization(tmp_path):
    """Verifies that SqliteStorageAdapter saves normalized 1-copy sentences and words in an atomic transaction."""
    db_path = tmp_path / "test_norm.db"
    adapter = SqliteStorageAdapter(db_path=db_path)

    # Rejection of sentinel/empty ZID
    with pytest.raises(desk.StructuredError) as exc_info:
        adapter.save_session(
            session_zid="00000000000000",
            slug="test",
            source_language="en",
            target_language="ru",
            text_mode="single",
            source_raw_text="Test",
        )
    assert exc_info.value.error_code == desk.ErrorCode.INVALID_STATE

    with pytest.raises(desk.StructuredError) as exc_info2:
        adapter.save_session(
            session_zid="",
            slug="test",
            source_language="en",
            target_language="ru",
            text_mode="single",
            source_raw_text="Test",
        )
    assert exc_info2.value.error_code == desk.ErrorCode.INVALID_STATE

    # Prepare 3 sentences with 5 tokens each = 15 token rows
    headers = [
        "Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination",
        "WordSourceMorphologyAI", "WordSourceIPA", "DeskSelected", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "CustomNote"
    ]

    data_rows = []
    for s_idx in range(1, 4):
        s_src = f"This is sentence {s_idx}."
        s_dst = f"Это предложение {s_idx}."
        for t_idx in range(1, 6):
            data_rows.append([
                f"token_{s_idx}_{t_idx}",
                f"lemma_{t_idx}",
                f"token_{s_idx}_{t_idx}",
                f"trans_{t_idx}",
                "Noun",
                "/ipa/",
                "1" if t_idx == 1 else "0",
                str(s_idx),
                s_src,
                s_dst,
                f"note_{s_idx}_{t_idx}",
            ])

    session_zid = "20260821153000"
    tsv_out = tmp_path / f"{session_zid}-multi-sent.en.tsv"

    res_zid = adapter.save_session(
        session_zid=session_zid,
        slug="multi-sent",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Full text with 3 sentences.",
        headers=headers,
        data_rows=data_rows,
        working_tsv_path=tsv_out,
    )
    assert res_zid == session_zid
    assert not tsv_out.exists()

    # Query SQLite database to verify normalization
    bundle = adapter.db.get_session_bundle(session_zid)
    assert bundle is not None
    assert bundle["session"]["zid"] == session_zid
    assert bundle["session"]["slug"] == "multi-sent"
    assert bundle["session"]["source_language"] == "en"

    # Exactly 3 sentences deduplicated (1 row per sentence index)
    sentences = bundle["sentences"]
    assert len(sentences) == 3
    for s in sentences:
        assert s["sentence_source"] == f"This is sentence {s['sentence_index']}."
        assert s["sentence_destination"] == f"Это предложение {s['sentence_index']}."

    # Exactly 15 words
    words = bundle["words"]
    assert len(words) == 15
    first_word = words[0]
    assert first_word["quotation"] == "token_1_1"
    assert first_word["lemma"] == "lemma_1"
    assert first_word["morphology"] == "Noun"
    assert first_word["ipa"] == "/ipa/"
    assert first_word["selected"] == 1
    assert first_word["extra_fields"] == {"CustomNote": "note_1_1"}


def test_sqlite_storage_adapter_ttl_caching(tmp_path):
    """Verifies SQLite lookup TTL caching hits within TTL and misses upon TTL expiry."""
    db_path = tmp_path / "test_ttl.db"
    adapter = SqliteStorageAdapter(db_path=db_path)

    session_zid = "20260821160000"
    adapter.save_session(
        session_zid=session_zid,
        slug="running-fast",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="He was running fast.",
        headers=["Quotation", "WordSource", "SentenceSourceIndex", "SentenceSource"],
        data_rows=[["running", "run", "1", "He was running fast."]],
    )

    # 1. Immediate lookup with TTL > 0 -> cache hit
    hit = adapter.get_cached_session("running-fast", "en", lookup_ttl_seconds=300)
    assert hit is not None
    assert hit["session"]["zid"] == session_zid
    assert len(hit["words"]) == 1

    # 2. Lookup with TTL = 0 -> miss
    miss_zero_ttl = adapter.get_cached_session("running-fast", "en", lookup_ttl_seconds=0)
    assert miss_zero_ttl is None

    # 3. Lookup for different language -> miss
    miss_lang = adapter.get_cached_session("running-fast", "de", lookup_ttl_seconds=300)
    assert miss_lang is None

    # 4. Invalidate cache by setting created_at to 1 hour ago
    with adapter.db.get_connection() as conn:
        conn.execute("UPDATE sessions SET created_at = datetime('now', '-3600 seconds') WHERE zid = ?;", (session_zid,))

    miss_expired = adapter.get_cached_session("running-fast", "en", lookup_ttl_seconds=300)
    assert miss_expired is None


def test_tsv_and_sqlite_backend_regression_parity(tmp_path):
    """Verifies that TSV backend output format remains 100% byte-for-byte identical to legacy TSV."""
    tsv_adapter = TsvStorageAdapter()
    tsv_file = tmp_path / "legacy.tsv"

    comments = ["# Generated by Kardenwort", "# Session ZID: 20260821170000"]
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource"]
    data_rows = [
        ["running", "run", "бег", "1", "He was running."],
        ["fast", "fast", "быстро", "1", "He was running."],
    ]

    tsv_adapter.save_tsv_rows_safely(tsv_file, comments, headers, data_rows)

    # Read raw content directly from disk
    content = tsv_file.read_text(encoding="utf-8")
    expected_lines = [
        "# Generated by Kardenwort",
        "# Session ZID: 20260821170000",
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource",
        "running\trun\tбег\t1\tHe was running.",
        "fast\tfast\tбыстро\t1\tHe was running.\n"
    ]
    assert content == "\n".join(expected_lines)

