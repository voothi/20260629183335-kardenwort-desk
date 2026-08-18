import sys
import time
import json
import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import kardenwort_desk
from kardenwort_desk import (
    run_render_flow,
    SEC_PIPELINE,
    SEC_SETTINGS,
    SEC_SENTENCES_MODE,
    TraceTimer,
)

DEEP_TRANSLATOR_DIR = Path(__file__).resolve().parent.parent.parent / "20241122093311-deep-translator"
if str(DEEP_TRANSLATOR_DIR) not in sys.path:
    sys.path.insert(0, str(DEEP_TRANSLATOR_DIR))

def setup_test_config(tmp_path):
    config = configparser.ConfigParser()
    config.read_string(f"""
[settings]
default_target_language=ru
enable_performance_tracing=true

[languages]
en_prompt=default
de_prompt=default

[pipeline]
parallelize_core_and_translation=true
text_base_provider=google

[sentences_mode]
enabled=true
parent_mode=table
min_sentences=1
""")
    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': Path('dummy.py'),
        'kardenwort_python': Path('python'),
        'anki_mapping_file': tmp_path / "anki_mapping.ini",
        'kardenwort_workspace': tmp_path,
        'base_dir': tmp_path,
        'deep_translator_python': Path('python'),
        'translate_google_script': Path('translate_google.py'),
        'translate_deepl_script': Path('translate_deepl.py'),
    }
    
    # Create minimal anki_mapping.ini
    mapping_ini = tmp_path / "anki_mapping.ini"
    mapping_ini.write_text("""[mapping]
WordSource = 1
WordDestination = 2
SentenceSource = 3
SentenceDestination = 4
SentenceSourceIndex = 5
""", encoding='utf-8')
    return config, resolved_paths

def test_google_session_pooling_contract():
    """Verify that translate_google uses persistent requests.Session with connection pooling."""
    import translate_google
    session = translate_google.get_global_session()
    assert session is not None
    assert "https://" in session.adapters
    adapter = session.adapters["https://"]
    assert adapter._pool_connections >= 10
    assert adapter._pool_maxsize >= 10

def test_deepl_session_pooling_contract():
    """Verify that translate_deepl uses persistent requests.Session with connection pooling."""
    import translate_deepl
    session = translate_deepl.get_global_session()
    assert session is not None
    assert "https://" in session.adapters
    adapter = session.adapters["https://"]
    assert adapter._pool_connections >= 10
    assert adapter._pool_maxsize >= 10

def test_concurrent_execution_overlap(tmp_path, monkeypatch):
    """Assert that core TSV generation and translation run concurrently and reduce wall-clock time."""
    config, resolved_paths = setup_test_config(tmp_path)
    
    tsv_file = tmp_path / "20260819000000-sample.en.tsv"
    tsv_file.write_text("# comments\nWordSource\tWordDestination\tSentenceSource\tSentenceDestination\tSentenceSourceIndex\nhello\t\tHello world.\t\t1\n", encoding='utf-8')
    
    def mock_prepare_lookup(*args, **kwargs):
        time.sleep(0.4)
        return tsv_file
        
    def mock_translate(*args, **kwargs):
        time.sleep(0.4)
        return "Привет мир."
        
    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', mock_prepare_lookup)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_translate)
    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', lambda *args, **kwargs: None)
    
    start_time = time.perf_counter()
    run_render_flow("Hello world.", "en", "20260819000000", "single", config, resolved_paths)
    elapsed = time.perf_counter() - start_time
    
    # Sequential would be >= 0.8s. Parallel should be around ~0.4s-0.6s.
    assert elapsed < 0.75, f"Execution was too slow ({elapsed:.3f}s), expected concurrent overlap (<0.75s)"

def test_parallel_thread_exception_safety_and_zid(tmp_path, monkeypatch, caplog):
    """Assert that exceptions in parallel workers capture the ZID and do not hang."""
    config, resolved_paths = setup_test_config(tmp_path)
    test_zid = "20260819099999"
    
    def mock_prepare_lookup(*args, **kwargs):
        raise RuntimeError("Synthetic core worker crash")
        
    def mock_translate(*args, **kwargs):
        return "Translated"
        
    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', mock_prepare_lookup)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_translate)
    
    with pytest.raises(RuntimeError, match="Synthetic core worker crash"):
        run_render_flow("Hello world.", "en", test_zid, "single", config, resolved_paths)
        
    # Verify ZID was logged in the error message
    assert any(test_zid in record.message for record in caplog.records if record.levelname == "ERROR")

def test_partial_translation_persistence_under_concurrency(tmp_path, monkeypatch):
    """Verify that translation persistence contracts work seamlessly after concurrent join."""
    config, resolved_paths = setup_test_config(tmp_path)
    test_zid = "20260819055555"
    
    tsv_file = tmp_path / f"{test_zid}-sample.en.tsv"
    tsv_content = "# comments\nWordSource\tWordDestination\tSentenceSource\tSentenceDestination\tSentenceSourceIndex\napple\t\tI eat an apple.\t\t1\n"
    tsv_file.write_text(tsv_content, encoding='utf-8')
    
    monkeypatch.setattr(kardenwort_desk, 'resolve_results_dir', lambda *a, **kw: tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', lambda *a, **kw: tsv_file)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', lambda *a, **kw: "Я ем яблоко.")
    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', lambda *args, **kwargs: None)
    
    run_render_flow("I eat an apple.", "en", test_zid, "single", config, resolved_paths)
    
    # Verify master translation text file is persisted
    trans_files = list(tmp_path.glob(f"{test_zid}*.ru.txt"))
    assert len(trans_files) >= 1
    assert "Я ем яблоко." in trans_files[0].read_text(encoding='utf-8')
