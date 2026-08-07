from kardenwort_desk import run_render_flow, TranslationAlignmentError, SEC_PIPELINE
import pytest
import configparser
from pathlib import Path
import time
import kardenwort_desk

def setup_test_env(tmp_path):
    config = configparser.ConfigParser()
    config.read_string(f"""
[settings]
default_target_language=ru
[pipeline]
parallelize_core_and_translation=true
[sentences_mode]
enabled=true
parent_mode=table
""")
    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': Path('dummy.py'),
        'kardenwort_python': Path('python'),
        'anki_mapping_file': Path('dummy.json'),
        'kardenwort_workspace': tmp_path
    }
    return config, resolved_paths

def test_parallel_execution_time(tmp_path, monkeypatch):
    config, resolved_paths = setup_test_env(tmp_path)
    
    # Mock prepare_lookup_tsv
    def mock_prepare_lookup_tsv(*args, **kwargs):
        time.sleep(0.3)
        p = tmp_path / "mock.tsv"
        p.write_text("Row1\nRow2", encoding='utf-8')
        return p
        
    # Mock translate_text
    def mock_translate_text(*args, **kwargs):
        time.sleep(0.3)
        return "Translated Text"
        
    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', mock_prepare_lookup_tsv)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_translate_text)
    monkeypatch.setattr(kardenwort_desk, 'load_anki_mapping', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'get_role_fields', lambda m, h: {})
    monkeypatch.setattr(kardenwort_desk, 'load_tsv_rows', lambda p: ([], [], []))
    monkeypatch.setattr(kardenwort_desk, 'resolve_translations', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'load_kardenwort_config', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'resolve_results_dir', lambda a, b: tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', lambda *args, **kwargs: None)

    start_time = time.time()
    run_render_flow("Hello", "en", "123", "single", config, resolved_paths)
    end_time = time.time()
    
    duration = end_time - start_time
    
    # If sequential, it would be 0.6s. If parallel, it should be ~0.3s.
    assert duration < 0.5, f"Execution was too slow: {duration}s, expected parallel execution (~0.3s)"

def test_parallel_execution_exception_handling(tmp_path, monkeypatch):
    config, resolved_paths = setup_test_env(tmp_path)
    
    def mock_prepare_lookup_tsv(*args, **kwargs):
        p = tmp_path / "mock.tsv"
        p.write_text("Row1\nRow2", encoding='utf-8')
        return p
        
    def mock_translate_text(*args, **kwargs):
        raise ValueError("Network error simulated")
        
    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', mock_prepare_lookup_tsv)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_translate_text)
    monkeypatch.setattr(kardenwort_desk, 'load_anki_mapping', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'get_role_fields', lambda m, h: {})
    monkeypatch.setattr(kardenwort_desk, 'load_tsv_rows', lambda p: ([], [], []))
    monkeypatch.setattr(kardenwort_desk, 'resolve_translations', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'load_kardenwort_config', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'resolve_results_dir', lambda a, b: tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', lambda *args, **kwargs: None)

    # Should gracefully catch and fallback without crashing
    run_render_flow("Hello", "en", "123", "single", config, resolved_paths)
