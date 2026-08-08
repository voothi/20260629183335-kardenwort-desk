import time
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
