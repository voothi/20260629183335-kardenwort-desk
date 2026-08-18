import pytest
import sys
import argparse
from unittest.mock import MagicMock
import kardenwort_desk
from kardenwort_desk import main, cmd_lookup

def test_lookup_cli_smoke(monkeypatch, capfd, tmp_path):
    import subprocess
    import configparser
    
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[project_structure]
generated_results_dir=results
[languages]
en_prompt=en_prompt
de_prompt=de_prompt
en_lemma_index=en_idx
en_lemma_override=en_over
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
    mapping.add_section('fields')
    mapping.add_section('desk_columns')
    with open(tmp_path / "anki_mapping.ini", 'w') as f:
        mapping.write(f)
        
    def mock_load_config(*args, **kwargs):
        return config, resolved_paths, {
            'format': 'html',
            'run_intellifiller': False,
            'lookup_ttl_seconds': 3600,
            'sections': ['source', 'translation', 'lemmas'],
            'lemma_columns': ['inflected', 'lemma', 'translation']
        }, {}
        
    monkeypatch.setattr(kardenwort_desk, 'load_config', mock_load_config)
    
    def mock_translate_source_text(*args, **kwargs):
        return {0: "working"}
        
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', mock_translate_source_text)
    
    def mock_run_lookup_flow(*args, **kwargs):
        return [], ['WordSource'], [['running']], "working"
        
    monkeypatch.setattr(kardenwort_desk, 'run_lookup_flow', mock_run_lookup_flow)
    
    monkeypatch.setattr(sys, 'argv', ['kardenwort_desk.py', 'lookup', '--text', 'running', '--language', 'en', '--format', 'text'])
    
    # Mock sys.__stdout__ and sys.stderr to avoid [WinError 6] under pytest capture on Windows
    import io
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_out)
    monkeypatch.setattr(sys, 'stdout', mock_out)
    monkeypatch.setattr(sys, 'stderr', mock_out)
    
    with pytest.raises(SystemExit) as excinfo:
        main()
        
    assert excinfo.value.code == 0
    out_str = mock_out.getvalue()
    assert "working" in out_str
        
def test_lookup_cli_overrides(monkeypatch, capfd, tmp_path):
    import configparser
    
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[project_structure]
generated_results_dir=results
[languages]
en_prompt=en_prompt
de_prompt=de_prompt
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
    mapping.add_section('fields')
    mapping.add_section('desk_columns')
    with open(tmp_path / "anki_mapping.ini", 'w') as f:
        mapping.write(f)
        
    def mock_load_config(*args, **kwargs):
        return config, resolved_paths, {
            'format': 'html',
            'run_intellifiller': False,
            'lookup_ttl_seconds': 3600,
            'sections': ['source', 'translation', 'lemmas'],
            'lemma_columns': ['inflected', 'lemma', 'translation']
        }, {}
    monkeypatch.setattr(kardenwort_desk, 'load_config', mock_load_config)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', lambda *a, **kw: {0: "test"})
    monkeypatch.setattr(kardenwort_desk, 'run_lookup_flow', lambda *a, **kw: ([], ['WordSource', 'WordDestination'], [['running', 'test']], "test"))
    
    monkeypatch.setattr(sys, 'argv', ['kardenwort_desk.py', 'lookup', '--text', 'running', '--language', 'en', '--sections', 'lemmas', '--lemma-columns', 'lemma,translation', '--no-headings'])
    
    # Mock sys.__stdout__ and sys.stderr
    import io
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_out)
    monkeypatch.setattr(sys, 'stdout', mock_out)
    monkeypatch.setattr(sys, 'stderr', mock_out)
    
    with pytest.raises(SystemExit):
        main()
        
    out_str = mock_out.getvalue()
    assert '<div class="kw-translation"' not in out_str
    assert '<div class="kw-source-text"' not in out_str
    assert "<h3>" not in out_str
    assert "Lemma" in out_str


def test_cmd_merge_cli(tmp_path, monkeypatch):
    tsv1 = tmp_path / "20260729000000-file1.en.tsv"
    tsv1.write_text("WordSource\tWordDestination\nHaus\thouse\nAuto\tcar\n", encoding="utf-8")
    
    tsv2 = tmp_path / "20260729000001-file2.en.tsv"
    tsv2.write_text("WordSource\tWordDestination\nAuto\tcar\nHund\tdog\n", encoding="utf-8")
    
    monkeypatch.setattr(sys, 'argv', ['kardenwort_desk.py', 'merge', '--files', str(tsv1), str(tsv2), '--deduplicate'])
    
    import io
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_out)
    monkeypatch.setattr(sys, 'stdout', mock_out)
    
    main()
    out_str = mock_out.getvalue()
    assert "SUCCESS: Merged Files" in out_str
