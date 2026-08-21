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
