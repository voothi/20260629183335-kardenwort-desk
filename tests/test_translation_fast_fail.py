import configparser
import time
from pathlib import Path
import pytest
import kardenwort_desk
from kardenwort_desk import run_render_flow, load_config, SEC_PIPELINE, _migrate_config


def test_config_fast_fail_timeout_parsing(tmp_path):
    # Test default fallback when omitted
    cfg = configparser.ConfigParser()
    cfg.read_string("[pipeline]\n")
    _migrate_config(cfg)
    assert cfg.getfloat(SEC_PIPELINE, "translation_fast_fail_timeout") == 3.0

    # Test explicit configuration
    cfg2 = configparser.ConfigParser()
    cfg2.read_string("[pipeline]\ntranslation_fast_fail_timeout = 1.5\n")
    _migrate_config(cfg2)
    assert cfg2.getfloat(SEC_PIPELINE, "translation_fast_fail_timeout") == 1.5


def test_slow_translation_triggers_fast_fail(tmp_path, monkeypatch):
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[pipeline]
parallelize_core_and_translation=true
translation_fast_fail_timeout=0.2
text_base_provider=deepl
[sentences_mode]
enabled=true
parent_mode=table
""")
    _migrate_config(config)

    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': Path('dummy.py'),
        'kardenwort_python': Path('python'),
        'anki_mapping_file': Path('dummy.json'),
        'kardenwort_workspace': tmp_path
    }

    def mock_prepare_lookup_tsv(*args, **kwargs):
        p = tmp_path / "mock.tsv"
        p.write_text("SentenceSourceIndex\tWordSource\tWordDestination\n1\tapple\tyabloko", encoding='utf-8')
        return p

    def mock_slow_translate_text(*args, **kwargs):
        # Simulate slow network translation
        time.sleep(3.0)
        return "Slow translation that exceeds timeout"

    fallback_called = []
    def mock_translate_source_text(*args, **kwargs):
        fallback_called.append(True)
        return {0: "Fallback translation"}

    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', mock_prepare_lookup_tsv)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_slow_translate_text)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', mock_translate_source_text)
    monkeypatch.setattr(kardenwort_desk, 'load_anki_mapping', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'get_role_fields', lambda m, h: {})
    monkeypatch.setattr(kardenwort_desk, 'load_tsv_rows', lambda p: ([], ["SentenceSourceIndex", "WordSource"], [["1", "apple"]]))
    monkeypatch.setattr(kardenwort_desk, 'resolve_translations', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'load_kardenwort_config', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'resolve_results_dir', lambda a, b: tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', lambda *args, **kwargs: None)

    start_time = time.time()
    # "Sentence one. Sentence two." triggers splitting
    html = run_render_flow("First sentence. Second sentence.", "en", "20260829013000", "single", config, resolved_paths)
    elapsed = time.time() - start_time

    # Must finish around 0.2s fast-fail timeout, well under the 3.0s sleep
    assert elapsed < 1.0, f"Render flow took {elapsed}s; expected fast-fail under 1.0s"
    assert not fallback_called, "Fallback translation should not have been invoked after fast-fail"
    assert isinstance(html, str)
    assert len(html) > 0


def test_fast_translation_completes_within_timeout(tmp_path, monkeypatch):
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[pipeline]
parallelize_core_and_translation=true
translation_fast_fail_timeout=2.0
text_base_provider=deepl
[sentences_mode]
enabled=true
parent_mode=table
""")
    _migrate_config(config)

    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': Path('dummy.py'),
        'kardenwort_python': Path('python'),
        'anki_mapping_file': Path('dummy.json'),
        'kardenwort_workspace': tmp_path
    }

    def mock_prepare_lookup_tsv(*args, **kwargs):
        p = tmp_path / "mock.tsv"
        p.write_text("SentenceSourceIndex\tWordSource\tWordDestination\n1\tapple\tyabloko", encoding='utf-8')
        return p

    def mock_fast_translate_text(*args, **kwargs):
        return "Первое предложение. Второе предложение."

    monkeypatch.setattr(kardenwort_desk, 'prepare_lookup_tsv', mock_prepare_lookup_tsv)
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_fast_translate_text)
    monkeypatch.setattr(kardenwort_desk, 'load_anki_mapping', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'get_role_fields', lambda m, h: {})
    monkeypatch.setattr(kardenwort_desk, 'load_tsv_rows', lambda p: ([], ["SentenceSourceIndex", "WordSource"], [["1", "apple"]]))
    monkeypatch.setattr(kardenwort_desk, 'resolve_translations', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', lambda *args, **kwargs: None)
    monkeypatch.setattr(kardenwort_desk, 'load_kardenwort_config', lambda x: configparser.ConfigParser())
    monkeypatch.setattr(kardenwort_desk, 'resolve_results_dir', lambda a, b: tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'spawn_ahk', lambda *args, **kwargs: None)

    start_time = time.time()
    html = run_render_flow("First sentence. Second sentence.", "en", "20260829013001", "single", config, resolved_paths)
    elapsed = time.time() - start_time

    assert elapsed < 1.0
    assert "Первое предложение" in html
