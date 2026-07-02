import pytest
import subprocess
import configparser
import sys
import os
import time
from pathlib import Path
from unittest.mock import MagicMock
import kardenwort_desk

def setup_test_env(tmp_path):
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
save_source_text=true
[project_structure]
generated_results_dir=results
[languages]
en_prompt=en_prompt
en_lemma_index=en_idx
en_lemma_override=en_over
[translation_providers]
main_text_translation=combined
lemmas_translation=combined
[goldendict]
""")
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    resolved_paths = {
        'kardenwort_workspace': workspace,
        'kardenwort_python': 'python',
        'anki_mapping_file': str(tmp_path / "anki_mapping.ini")
    }
    
    mapping = configparser.ConfigParser()
    mapping.read_string("""
[fields]
WordSource=lemma
WordDestination=word_translation
WordSourceMorphologyAI=morphology
WordSourceIPA=ipa
[desk_columns]
WordSource=lemma
WordDestination=word_translation
WordSourceMorphologyAI=morphology
WordSourceIPA=ipa
[fields_mapping.word]
WordSource=lemma
WordDestination=translation

""")
    with open(tmp_path / "anki_mapping.ini", 'w') as f:
        mapping.write(f)
        
    goldendict = {
        'format': 'html',
        'run_intellifiller': False,
        'lookup_ttl_seconds': 3600,
        'sections': ['source', 'translation', 'lemmas'],
        'lemma_columns': ['lemma', 'translation']
    }
    
    return config, resolved_paths, goldendict

def test_lookup_cache_behavior(monkeypatch, tmp_path):
    config, resolved_paths, goldendict = setup_test_env(tmp_path)
    
    mock_subprocess_run = MagicMock()
    
    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("WordSource\tWordDestination\ntest\t\n", encoding='utf-8')
        mock_subprocess_run(*args, **kwargs)
        
    monkeypatch.setattr(subprocess, 'run', mock_run)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', lambda *a, **kw: {0: "working"})
    monkeypatch.setattr(kardenwort_desk, 'translate_lemmas_fast_path', lambda *a, **kw: {'test': "test_transl"})
    
    # 1. Cache miss
    kardenwort_desk.run_lookup_flow("test text", "en", "ru", "html", config, resolved_paths, goldendict, "zid1")
    assert mock_subprocess_run.call_count == 1
    
    # Verify prefix
    results_dir = resolved_paths['kardenwort_workspace'] / "results"
    files = list(results_dir.glob("*.tsv"))
    assert len(files) == 1
    assert files[0].name.startswith("lookup-en-")
    assert not files[0].name.startswith("zid1-")
    
    # 2. Cache hit
    kardenwort_desk.run_lookup_flow("test text", "en", "ru", "html", config, resolved_paths, goldendict, "zid2")
    assert mock_subprocess_run.call_count == 1 # still 1!
    
    # 3. Cache TTL expiry
    cache_file = files[0]
    current_time = time.time()
    os.utime(cache_file, (current_time - 4000, current_time - 4000))
    kardenwort_desk.run_lookup_flow("test text", "en", "ru", "html", config, resolved_paths, goldendict, "zid3")
    assert mock_subprocess_run.call_count == 2

def test_lookup_translation_failure(monkeypatch, capsys, tmp_path):
    config, resolved_paths, goldendict = setup_test_env(tmp_path)
    goldendict['format'] = 'text'
    
    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_file = Path(cmd[cmd.index("--output-file") + 1])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("WordSource\tWordDestination\ntest\t\n", encoding='utf-8')
        
    monkeypatch.setattr(subprocess, 'run', mock_run)
    
    def raising_translator(*args, **kwargs):
        raise RuntimeError("Translation API offline")
        
    # We must mock translate_text which is used by translate_source_text and translate_lemmas_fast_path
    monkeypatch.setattr(kardenwort_desk, 'translate_text', raising_translator)
    
    # Should still succeed and exit 0
    comments, headers, data_rows, sent_trans = kardenwort_desk.run_lookup_flow("test text", "en", "ru", "text", config, resolved_paths, goldendict, "zid1")
    assert sent_trans == ""
    assert data_rows[0][headers.index('WordDestination')] == ""

def test_lookup_intellifiller(monkeypatch, tmp_path):
    config, resolved_paths, goldendict = setup_test_env(tmp_path)
    
    mock_run_headless_intellifiller = MagicMock()
    
    def mock_run(*args, **kwargs):
        cmd = args[0]
        out_file = Path(cmd[cmd.index("--output-file") + 1])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        # Note: missing MorphologyAI initially
        out_file.write_text("WordSource\tWordSourceMorphologyAI\ntest\t\n", encoding='utf-8')
        
    monkeypatch.setattr(subprocess, 'run', mock_run)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', lambda *a, **kw: {0: "working"})
    monkeypatch.setattr(kardenwort_desk, 'translate_lemmas_fast_path', lambda *a, **kw: {'test': "test_transl"})
    
    # Test True
    goldendict['run_intellifiller'] = True
    def side_effect_ifiller(tsv_path, *args, **kwargs):
        # mock it filling the data
        tsv_path.write_text("WordSource\tWordSourceMorphologyAI\ntest\tverb\n", encoding='utf-8')
        mock_run_headless_intellifiller()
        
    monkeypatch.setattr(kardenwort_desk, 'run_headless_intellifiller', side_effect_ifiller)
    
    comments, headers, data_rows, sent = kardenwort_desk.run_lookup_flow("test text", "en", "ru", "html", config, resolved_paths, goldendict, "zid1")
    assert mock_run_headless_intellifiller.call_count == 1
    assert data_rows[0][headers.index('WordSourceMorphologyAI')] == 'verb'
    
    # Test False
    goldendict['run_intellifiller'] = False
    
    def mock_run2(*args, **kwargs):
        cmd = args[0]
        out_file = Path(cmd[cmd.index("--output-file") + 1])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        # It comes with some data
        out_file.write_text("WordSource\tWordSourceMorphologyAI\ntest2\tadj\n", encoding='utf-8')
        
    monkeypatch.setattr(subprocess, 'run', mock_run2)
    comments, headers, data_rows, sent = kardenwort_desk.run_lookup_flow("test text 2", "en", "ru", "html", config, resolved_paths, goldendict, "zid2")
    assert mock_run_headless_intellifiller.call_count == 1 # unchanged
    # Should clear morphology data
    assert data_rows[0][headers.index('WordSourceMorphologyAI')] == ''

def test_lookup_utf8_stdout(monkeypatch, tmp_path):
    import io
    config, resolved_paths, goldendict = setup_test_env(tmp_path)
    
    def mock_load_config(*args, **kwargs):
        return config, resolved_paths, goldendict
        
    monkeypatch.setattr(kardenwort_desk, 'load_config', mock_load_config)
    
    monkeypatch.setattr(kardenwort_desk, 'run_lookup_flow', lambda *a, **kw: ([], ['WordSource'], [['test']], "тест"))
    
    # We test cmd_lookup directly by capturing stdout as bytes
    mock_stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
    monkeypatch.setattr(sys, 'stdout', mock_stdout)
    
    class Args:
        text = "test"
        language = "en"
        target_lang = "ru"
        format = "text"
        sections = None
        lemma_columns = None
        no_headings = False
        theme = "dark"
        config = None
        verbose = False
        debug = False
        
    with pytest.raises(SystemExit):
        kardenwort_desk.cmd_lookup(Args())
        
    mock_stdout.seek(0)
    out = mock_stdout.read()
    assert "тест" in out
    
    # verify underlying bytes
    raw_bytes = mock_stdout.buffer.getvalue()
    # It must be decodable via utf-8
    decoded = raw_bytes.decode('utf-8')
    assert "тест" in decoded
