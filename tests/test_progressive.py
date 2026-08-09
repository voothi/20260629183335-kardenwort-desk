import time
import types
import pytest
import configparser
from pathlib import Path
import kardenwort_desk as desk

def setup_test_env(tmp_path):
    config = configparser.ConfigParser()
    config.read_string(f"""
[settings]
default_target_language=ru
[pipeline]
parallelize_core_and_translation=false
progressive_text_translation=true
progressive_timeout_seconds=15
[triggers]
run_text_translation=auto
run_lemma_base_translation=auto
run_lemma_enrichment=auto
[sentences_mode]
enabled=true
parent_mode=table
""")
    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': Path('dummy.py'),
        'kardenwort_python': Path('python'),
        'anki_mapping_file': tmp_path / 'mapping.ini',
        'kardenwort_workspace': tmp_path,
        'settings_file': tmp_path / 'settings.ini'
    }
    
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text("[fields]\nWordSource=\nSentenceDestination=\nSentenceSourceIndex=\n[fields_mapping.word]\nWordSource=lemma\n[fields_mapping.sentence]\nSentenceDestination=sentence_destination\nSentenceSourceIndex=sentence_index\n")
    
    return config, resolved_paths

def test_progressive_bypassed(monkeypatch, tmp_path):
    config, resolved_paths = setup_test_env(tmp_path)
    
    # Mock prepare_lookup_tsv
    def mock_prepare_lookup_tsv(*args, **kwargs):
        p = tmp_path / "mock.tsv"
        p.write_text("SentenceSourceIndex\tSentenceDestination\n1\t\n", encoding='utf-8')
        return p
        
    # Mock translate_text with a slow delay
    def mock_translate_text(*args, **kwargs):
        time.sleep(3.0)
        return "Translated Text"
        
    monkeypatch.setattr(desk, 'prepare_lookup_tsv', mock_prepare_lookup_tsv)
    monkeypatch.setattr(desk, 'translate_text', mock_translate_text)
    monkeypatch.setattr(desk, 'load_anki_mapping', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'get_role_fields', lambda m, h: {'sentence_destination': 'SentenceDestination', 'sentence_index': 'SentenceSourceIndex'})
    monkeypatch.setattr(desk, 'load_tsv_rows', lambda p: ([], ["SentenceSourceIndex", "SentenceDestination"], [["1", ""]]))
    monkeypatch.setattr(desk, 'resolve_translations', lambda *args, **kwargs: None)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)
    monkeypatch.setattr(desk, 'write_update_js', lambda *args, **kwargs: None)
    monkeypatch.setattr(desk, 'load_kardenwort_config', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'resolve_results_dir', lambda a, b: tmp_path)
    monkeypatch.setattr(desk, 'spawn_ahk', lambda *args, **kwargs: None)

    start_time = time.time()
    html_out = desk.run_render_flow("Hello", "en", "123", "single", config, resolved_paths)
    end_time = time.time()
    
    duration = end_time - start_time
    
    # Should be instant, bypassing the 3-second delay
    assert duration < 1.0, f"Execution was too slow: {duration}s, progressive bypass failed"
    
    # Should contain the skeleton loader
    assert "skeleton-loader" in html_out
    assert "data-pending=\"true\"" in html_out
    assert "Timeout: Background Process Failed" in html_out # from the JS watchdog


# ---------------------------------------------------------------------------
# Task 1.1: cmd_progressive_worker lifecycle — happy path marker ordering
# ---------------------------------------------------------------------------

def _make_worker_args(tmp_path, config_path):
    """Return a minimal argparse-compatible namespace for cmd_progressive_worker."""
    args = types.SimpleNamespace()
    args.tsv = str(tmp_path / "20260809190000-test.en.tsv")
    args.config = str(config_path)
    args.text_mode = "single"
    args.skip_intellifiller = True  # keep unit test isolated from enrichment
    return args


def _write_worker_tsv(path):
    path.write_text(
        "WordSource\tWordDestination\tWordSourceIPA\n"
        "Haus\t\t\n",
        encoding="utf-8",
    )


def test_progressive_worker_lifecycle(monkeypatch, tmp_path):
    """
    Verifies that cmd_progressive_worker:
    1. Calls the translation stage.
    2. Touches .base_translation_done BEFORE attempting enrichment.
    3. Touches .enrichment_done at the very end (in finally).
    """
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[settings]\ndefault_language=en\n"
        "[pipeline]\nlemma_base_provider=google\nlemma_reprocess_provider=intellifiller\n"
        "[triggers]\nrun_lemma_base_translation=auto\nrun_text_translation=manual\nrun_lemma_enrichment=manual\n"
        "[fields]\n",
        encoding="utf-8",
    )
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[fields_mapping.word]\nWordSource=lemma\nWordDestination=word_translation\nWordSourceIPA=word_ipa\n",
        encoding="utf-8",
    )

    tsv_path = tmp_path / "20260809190000-test.en.tsv"
    _write_worker_tsv(tsv_path)

    translation_order = []

    def mock_load_config(cfg_path):
        config = configparser.ConfigParser()
        config.read_string(
            "[settings]\ndefault_language=en\n"
            "[pipeline]\nlemma_base_provider=google\nlemma_reprocess_provider=intellifiller\n"
            "[triggers]\nrun_lemma_base_translation=auto\nrun_text_translation=manual\nrun_lemma_enrichment=manual\n"
            "[fields]\n"
        )
        resolved = {
            "results_dir": tmp_path,
            "anki_mapping_file": mapping_path,
            "kardenwort_workspace": tmp_path,
            "settings_file": tmp_path / "settings.ini",
        }
        return config, resolved, None, None

    def mock_translation_stage(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields):
        translation_order.append("translation_called")
        # Simulate: translation fills WordDestination
        for row in data_rows:
            if len(row) > 0 and row[0].strip() and len(row) < 2:
                row.append("Haus-DE")
            elif len(row) > 1 and not row[1].strip():
                row[1] = "house"
        return data_rows

    monkeypatch.setattr(desk, "load_config", mock_load_config)
    monkeypatch.setattr(desk, "_progressive_worker_stage_translation", mock_translation_stage)
    monkeypatch.setattr(desk, "write_update_js", lambda *a, **kw: None)
    monkeypatch.setattr(desk, "load_anki_mapping", lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, "get_role_fields", lambda m, h: {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "word_ipa": "WordSourceIPA",
    })

    args = _make_worker_args(tmp_path, config_path)
    desk.cmd_progressive_worker(args)

    assert "translation_called" in translation_order, "Translation stage was not called"

    base_done = tsv_path.with_suffix(".base_translation_done")
    enrich_done = tsv_path.with_suffix(".enrichment_done")

    assert base_done.exists(), ".base_translation_done marker was not created"
    assert enrich_done.exists(), ".enrichment_done marker was not created"

    # Ensure base comes before or at the same time as enrichment
    base_mtime = base_done.stat().st_mtime
    enrich_mtime = enrich_done.stat().st_mtime
    assert base_mtime <= enrich_mtime, ".base_translation_done must be created before .enrichment_done"


# ---------------------------------------------------------------------------
# Task 1.2: cmd_progressive_worker exception fallback — markers written on crash
# ---------------------------------------------------------------------------

def test_progressive_worker_exception_fallback(monkeypatch, tmp_path):
    """
    Verifies that when cmd_progressive_worker crashes during translation,
    the finally block still creates both .base_translation_done and .enrichment_done
    so that waiting child windows are never left deadlocked.
    """
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[settings]\ndefault_language=en\n"
        "[pipeline]\nlemma_base_provider=google\nlemma_reprocess_provider=intellifiller\n"
        "[triggers]\nrun_lemma_base_translation=auto\nrun_text_translation=manual\nrun_lemma_enrichment=manual\n"
        "[fields]\n",
        encoding="utf-8",
    )
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[fields_mapping.word]\nWordSource=lemma\nWordDestination=word_translation\n",
        encoding="utf-8",
    )

    tsv_path = tmp_path / "20260809190000-test.en.tsv"
    _write_worker_tsv(tsv_path)

    def mock_load_config(cfg_path):
        config = configparser.ConfigParser()
        config.read_string(
            "[settings]\ndefault_language=en\n"
            "[pipeline]\nlemma_base_provider=google\nlemma_reprocess_provider=intellifiller\n"
            "[triggers]\nrun_lemma_base_translation=auto\nrun_text_translation=manual\nrun_lemma_enrichment=manual\n"
            "[fields]\n"
        )
        resolved = {
            "results_dir": tmp_path,
            "anki_mapping_file": mapping_path,
            "kardenwort_workspace": tmp_path,
            "settings_file": tmp_path / "settings.ini",
        }
        return config, resolved, None, None

    def mock_crashing_translation_stage(*args, **kwargs):
        raise RuntimeError("Simulated crash in translation stage")

    monkeypatch.setattr(desk, "load_config", mock_load_config)
    monkeypatch.setattr(desk, "_progressive_worker_stage_translation", mock_crashing_translation_stage)
    monkeypatch.setattr(desk, "write_update_js", lambda *a, **kw: None)
    monkeypatch.setattr(desk, "load_anki_mapping", lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, "get_role_fields", lambda m, h: {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
    })

    args = _make_worker_args(tmp_path, config_path)

    # Must NOT propagate — the inner exception is caught by the worker's except block
    desk.cmd_progressive_worker(args)

    base_done = tsv_path.with_suffix(".base_translation_done")
    enrich_done = tsv_path.with_suffix(".enrichment_done")

    assert base_done.exists(), (
        ".base_translation_done MUST be created in the finally block even on crash — "
        "child windows will deadlock otherwise"
    )
    assert enrich_done.exists(), (
        ".enrichment_done MUST be created in the finally block even on crash — "
        "child windows will deadlock otherwise"
    )


def test_sibling_wait_watchdog_missing(monkeypatch, tmp_path):
    """
    Simulate ImportError when importing watchdog to ensure _wait_for_sibling_sync_impl
    gracefully degrades to the time.sleep() polling loop.
    """
    tsv_path = tmp_path / "20260809190000-test.en.tsv"
    tsv_path.write_text("WordSource\tWordDestination\napple\t\n", encoding="utf-8")
    
    sibling = tmp_path / "20260809185959-sib.en.tsv"
    sibling.write_text("WordSource\tWordDestination\napple\t\n", encoding="utf-8")
    
    # We want to mock _wait_for_sibling_sync_impl's internal check_func to return True
    # so it exits immediately after 1 poll, but we must verify it took the polling path.
    # To do this, we can mock time.sleep to record calls.
    sleep_calls = []
    
    original_sleep = time.sleep
    def mock_sleep(secs):
        sleep_calls.append(secs)
        # On first sleep, create the marker so next poll succeeds
        sibling.with_suffix(".base_translation_done").touch()
        
    monkeypatch.setattr(time, "sleep", mock_sleep)
    
    # Mock __import__ to raise ImportError for watchdog
    import builtins
    real_import = builtins.__import__
    def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith('watchdog'):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, globals, locals, fromlist, level)
        
    monkeypatch.setattr(builtins, "__import__", mock_import)
    
    desk.wait_for_older_siblings_in_batch(tsv_path, mapping={'fields': {}, 'fields_mapping.word': {'WordSource': 'WordSource', 'WordDestination': 'WordDestination'}}, lemma_base_provider='google', data_rows_count=1)
    
    # Assert sleep was called at least once, proving we fell back to the polling loop
    assert len(sleep_calls) > 0

def test_sibling_wait_timeout(monkeypatch, tmp_path):
    """
    Verify that if sibling never finishes, the wait function times out
    and breaks the deadlock.
    """
    tsv_path = tmp_path / "20260809190000-test.en.tsv"
    tsv_path.write_text("dummy", encoding="utf-8")
    
    sibling = tmp_path / "20260809185959-sib.en.tsv"
    sibling.write_text("dummy", encoding="utf-8")
    
    # Patch the timeout constant
    monkeypatch.setattr(desk, "SYNC_TIMEOUT_SEC", 0.1)
    
    start = time.time()
    desk.wait_for_older_siblings_enrichment_in_batch(tsv_path, data_rows_count=1)
    duration = time.time() - start
    
    # Should exit after approximately 0.1 seconds, not 3600
    assert duration < 1.5

def test_cross_pollinate_corrupted_sibling(tmp_path):
    """
    Ensure that a corrupted sibling TSV doesn't crash cross_pollinate_from_siblings.
    """
    working_tsv = tmp_path / "20260809190000-test.en.tsv"
    working_tsv.write_text("WordSource\tWordDestination\napple\t\n", encoding="utf-8")
    
    sibling_tsv = tmp_path / "20260809185959-sib.en.tsv"
    # Write garbage bytes that will raise UnicodeDecodeError or CSV parsing error
    with open(sibling_tsv, "wb") as f:
        f.write(b'\x80\x81\x82corrupted')
        
    headers = ["WordSource", "WordDestination"]
    data_rows = [["apple", ""]]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination"}
    
    # Should not crash, should just return the unmodified data_rows
    result = desk.cross_pollinate_from_siblings(working_tsv, data_rows, headers, role_fields)
    assert result == data_rows
    assert result[0][1] == ""

def test_is_field_empty_evaluations():
    """
    Validate that is_field_empty handles short arrays, blank strings, 
    skeleton loaders, and API error strings correctly.
    """
    assert desk.is_field_empty([], 1) == True, "Out of bounds should be empty"
    assert desk.is_field_empty(["apple"], -1) == True, "Column -1 should be empty"
    
    row = ["", "  ", "apple", "skeleton-loader", "Error calling Gemini API: HTTP 429"]
    assert desk.is_field_empty(row, 0) == True, "Empty string is empty"
    assert desk.is_field_empty(row, 1) == True, "Whitespace is empty"
    assert desk.is_field_empty(row, 2) == False, "Valid string is NOT empty"
    assert desk.is_field_empty(row, 3) == True, "Skeleton loader is empty"
    assert desk.is_field_empty(row, 4) == True, "API error string is empty"

def test_cross_pollinate_api_error_handling(tmp_path):
    """
    Ensure that if an older sibling crashed and left an API error in WordDestination,
    it is NOT copied over to the younger sibling.
    """
    working_tsv = tmp_path / "20260809190000-test.en.tsv"
    # Target row is completely empty for destination and IPA
    working_tsv.write_text("WordSource\tWordDestination\tWordSourceIPA\napple\t\t\n", encoding="utf-8")
    
    sibling_tsv = tmp_path / "20260809185959-sib.en.tsv"
    # Sibling row has API errors
    sibling_tsv.write_text("WordSource\tWordDestination\tWordSourceIPA\napple\tError calling Gemini API\tError calling Gemini API\n", encoding="utf-8")
        
    headers = ["WordSource", "WordDestination", "WordSourceIPA"]
    data_rows = [["apple", "", ""]]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA"}
    
    # Should not copy the error text!
    result = desk.cross_pollinate_from_siblings(working_tsv, data_rows, headers, role_fields)
    assert result == [["apple", "", ""]]
    assert desk.is_field_empty(result[0], 1) == True
    assert desk.is_field_empty(result[0], 2) == True
