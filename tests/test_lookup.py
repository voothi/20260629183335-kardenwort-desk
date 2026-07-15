import pytest
import subprocess
import configparser
import sys
import os
import time
from pathlib import Path
from unittest.mock import MagicMock
import kardenwort_desk

@pytest.fixture(autouse=True)
def mock_progressive_worker(monkeypatch):
    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

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
[pipeline]
base_provider=combined
enrichment_provider=combined
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
    
    return config, resolved_paths, goldendict, {}

def test_lookup_cache_behavior(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    
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
    assert files[0].name.startswith("zid1-")
    
    # 2. Cache hit
    kardenwort_desk.run_lookup_flow("test text", "en", "ru", "html", config, resolved_paths, goldendict, "zid2")
    assert mock_subprocess_run.call_count == 1 # still 1!
    
    # 3. Cache TTL expiry
    cache_file = files[0]
    current_time = time.time()
    os.utime(cache_file, (current_time - 4000, current_time - 4000))
    kardenwort_desk.run_lookup_flow("test text", "en", "ru", "html", config, resolved_paths, goldendict, "zid3")
    assert mock_subprocess_run.call_count == 2

def test_lookup_translation_failure(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
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
    assert sent_trans.startswith("[Translation Error:")
    assert data_rows[0][headers.index('WordDestination')] == ""

def test_lookup_intellifiller(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    
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
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    
    def mock_load_config(*args, **kwargs):
        return config, resolved_paths, goldendict, {}
        
    monkeypatch.setattr(kardenwort_desk, 'load_config', mock_load_config)
    
    monkeypatch.setattr(kardenwort_desk, 'run_lookup_flow', lambda *a, **kw: ([], ['WordSource'], [['test']], "тест"))
    
    # Mock sys.__stdout__ directly as it's what emit_payload uses
    mock_stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
    monkeypatch.setattr(sys, '__stdout__', mock_stdout)
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
        disable_css = False
        
    with pytest.raises(SystemExit):
        kardenwort_desk.cmd_lookup(Args())
        
    mock_stdout.seek(0)
    decoded = mock_stdout.read()
    assert "тест" in decoded

def test_progressive_worker_stages(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'load_config', lambda *a, **kw: (config, resolved_paths, goldendict, {}))
    
    # Enable new triggers
    config.set('pipeline', 'lemma_base_provider', 'google')
    config.set('pipeline', 'lemma_reprocess_provider', 'intellifiller')
    
    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    config.set('triggers', 'run_lemma_enrichment', 'auto')
    
    config.add_section('rendering')
    config.set('rendering', 'display_mode', 'progressive')
    
    config.set('settings', 'intellifiller_batch_size', '2')
    
    # Create working TSV
    tsv_path = resolved_paths['kardenwort_workspace'] / "results" / "test.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\nword1\t\t\t\nword2\t\t\t\nword3\t\t\t\n", encoding='utf-8')
    
    # Sibling text file
    txt_path = tsv_path.with_suffix('.txt')
    txt_path.write_text("word1 word2 word3", encoding='utf-8')
    
    # Record write_update_js calls
    write_calls = []
    def mock_write_update_js(tsv_p, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None):
        write_calls.append((stage, status, len(data_rows)))
        
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', mock_write_update_js)
    
    # Mock translation calls
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', lambda *a, **kw: {0: "trans_sentence"})
    monkeypatch.setattr(kardenwort_desk, 'translate_lemmas_fast_path', lambda *a, **kw: {"word1": "trans1", "word2": "trans2", "word3": "trans3"})
    
    # Mock IntelliFiller - it will write filled fields to tsv
    def mock_run_headless_intellifiller(tsv_p, prompt, conf, res_paths, selected_rows=None):
        # fill morphology and ipa for selected_rows
        comments, headers, data_rows = kardenwort_desk.load_tsv_rows(tsv_p)
        col_morph = headers.index('WordSourceMorphologyAI')
        col_ipa = headers.index('WordSourceIPA')
        for r_idx in selected_rows:
            data_rows[r_idx][col_morph] = f"morph{r_idx}"
            data_rows[r_idx][col_ipa] = f"ipa{r_idx}"
        kardenwort_desk.save_tsv_rows_safely(tsv_p, comments, headers, data_rows)
        
    monkeypatch.setattr(kardenwort_desk, 'run_headless_intellifiller', mock_run_headless_intellifiller)
    
    class Args:
        config = None
        tsv = str(tsv_path)
        language = "en"
        target_lang = "ru"
        prompt = "en_prompt"
        provider = "google"
        word_empty = "true"
        text_mode = "single"
        skip_intellifiller = False
        
    kardenwort_desk.cmd_progressive_worker(Args())
    
    # Stage emission order and tags:
    # 1. source (status success)
    # 2. translated_text (status success)
    # 3. translated (status success)
    # 4. enrichment (batch 1, rows 0-1)
    # 5. enrichment (batch 2, row 2)
    # 6. finished
    assert len(write_calls) == 7
    assert write_calls[0] == ('source', 'success', 3)
    assert write_calls[1] == ('translated_text', 'success', 3)
    assert write_calls[2] == (None, 'success', 3)
    assert write_calls[3] == ('translated', 'success', 3)
    assert write_calls[4] == ('enrichment', 'success', 3)
    assert write_calls[5] == ('enrichment', 'success', 3)
    assert write_calls[6] == ('finished', 'success', 3)

def test_progressive_worker_failure_isolation(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'load_config', lambda *a, **kw: (config, resolved_paths, goldendict, {}))
    
    config.set('pipeline', 'lemma_base_provider', 'google')
    config.set('pipeline', 'lemma_reprocess_provider', 'intellifiller')
    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    config.set('triggers', 'run_lemma_enrichment', 'auto')
    config.add_section('rendering')
    config.set('rendering', 'display_mode', 'progressive')
    
    tsv_path = resolved_paths['kardenwort_workspace'] / "results" / "test2.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\nword1\t\t\t\n", encoding='utf-8')
    txt_path = tsv_path.with_suffix('.txt')
    txt_path.write_text("word1", encoding='utf-8')
    
    write_calls = []
    def mock_write_update_js(tsv_p, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None):
        write_calls.append((stage, status))
        
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', mock_write_update_js)
    
    # Inject translation failure
    def failing_translate(*args, **kwargs):
        raise RuntimeError("Translation error")
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', failing_translate)
    
    # Enrichment succeeds
    monkeypatch.setattr(kardenwort_desk, 'run_headless_intellifiller', lambda *a, **kw: None)
    
    class Args:
        config = None
        tsv = str(tsv_path)
        language = "en"
        target_lang = "ru"
        prompt = "en_prompt"
        provider = "google"
        word_empty = "true"
        text_mode = "single"
        skip_intellifiller = False
        
    kardenwort_desk.cmd_progressive_worker(Args())
    
    # Even though base translation failed, enrichment and finished still ran!
    assert len(write_calls) == 4
    assert write_calls[0] == ('source', 'success')
    assert write_calls[1] == ('translated', 'failed')
    assert write_calls[2] == ('enrichment', 'success')
    assert write_calls[3] == ('finished', 'success')

def test_progressive_worker_d3_enrichment_only(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'load_config', lambda *a, **kw: (config, resolved_paths, goldendict, {}))
    
    config.set('pipeline', 'lemma_base_provider', 'google')
    config.set('pipeline', 'lemma_reprocess_provider', 'intellifiller')
    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'manual')
    config.set('triggers', 'run_text_translation', 'manual')
    config.set('triggers', 'run_lemma_enrichment', 'auto')
    
    tsv_path = resolved_paths['kardenwort_workspace'] / "results" / "test.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\nword1\t\t\t\n", encoding='utf-8')
    tsv_path.with_suffix('.txt').write_text("word1", encoding='utf-8')
    
    write_calls = []
    def mock_write_update_js(tsv_p, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None):
        write_calls.append(stage)
        
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', mock_write_update_js)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', lambda *a, **kw: {})
    monkeypatch.setattr(kardenwort_desk, 'translate_lemmas_fast_path', lambda *a, **kw: {})
    monkeypatch.setattr(kardenwort_desk, 'run_headless_intellifiller', lambda *a, **kw: None)
    
    args = MagicMock()
    args.tsv = str(tsv_path)
    args.language = 'en'
    args.target_lang = 'ru'
    args.prompt = 'default'
    args.provider = 'google'
    args.word_empty = 'True'
    args.text_mode = 'single'
    args.skip_intellifiller = False
    
    kardenwort_desk.cmd_progressive_worker(args)
    
    assert "translated" not in write_calls
    assert "enrichment" in write_calls

def test_progressive_worker_d4_text_mode(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'load_config', lambda *a, **kw: (config, resolved_paths, goldendict, {}))
    
    config.set('pipeline', 'lemma_base_provider', 'google')
    config.set('pipeline', 'lemma_reprocess_provider', 'intellifiller')
    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    
    tsv_path = resolved_paths['kardenwort_workspace'] / "results" / "test.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\nword1\t\t\t\n", encoding='utf-8')
    tsv_path.with_suffix('.txt').write_text("word1", encoding='utf-8')
    
    passed_text_mode = None
    def mock_translate_source_text(text, source_lang, target_lang, text_mode, config, resolved_paths, provider, **kwargs):
        nonlocal passed_text_mode
        passed_text_mode = text_mode
        return {}
        
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', mock_translate_source_text)
    monkeypatch.setattr(kardenwort_desk, 'translate_lemmas_fast_path', lambda *a, **kw: {})
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', MagicMock())
    
    args = MagicMock()
    args.tsv = str(tsv_path)
    args.language = 'en'
    args.target_lang = 'ru'
    args.prompt = 'default'
    args.provider = 'google'
    args.word_empty = 'True'
    args.text_mode = 'multi_line'
    args.skip_intellifiller = True
    
    kardenwort_desk.cmd_progressive_worker(args)
    
    assert passed_text_mode == 'multi_line'

def test_reprocess_worker_classification_update(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'load_config', lambda *a, **kw: (config, resolved_paths, goldendict, {}))
    
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    
    # Overwrite the anki mapping file with oxford role
    mapping = configparser.ConfigParser()
    mapping.read_string("""
[fields]
WordSource=lemma
WordDestination=word_translation
WordSourceMorphologyAI=morphology
WordSourceIPA=ipa
ClassificationOxford=oxford
[desk_columns]
WordSource=lemma
WordDestination=word_translation
WordSourceMorphologyAI=morphology
WordSourceIPA=ipa
ClassificationOxford=oxford
[fields_mapping.word]
WordSource=lemma
WordDestination=translation
ClassificationOxford=oxford
""")
    with open(resolved_paths['anki_mapping_file'], 'w') as f:
        mapping.write(f)
        
    # Enable classification section in local config and mock load_kardenwort_config
    config.add_section('classification')
    config.set('classification', 'enabled', 'true')
    
    kw_config = MagicMock()
    kw_config.has_section.return_value = True
    kw_config.getboolean.return_value = True
    kw_config.get.return_value = "oxford=data/en/oxford.tsv"
    monkeypatch.setattr(kardenwort_desk, 'load_kardenwort_config', lambda *a: kw_config)
    
    # Mock load_classification_dictionaries
    mock_classifications = {"oxford": {"word1": "3k:A1", "word2": "5k:B2"}}
    
    # Mock import from core kardenwort
    import sys
    class MockKardenwortCore:
        @staticmethod
        def load_classification_dictionaries(classify_args):
            return mock_classifications
            
    sys.modules['kardenwort'] = MagicMock()
    sys.modules['kardenwort.core'] = MagicMock()
    sys.modules['kardenwort.core.kardenwort'] = MockKardenwortCore
    
    # Create working TSV containing columns
    tsv_path = kardenwort_workspace / "results" / "test_reproc.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\tClassificationOxford\nword1\t\t\t\t\nword2\t\t\t\t\n", encoding='utf-8')
    
    # Sibling text file
    tsv_path.with_suffix('.txt').write_text("word1 word2", encoding='utf-8')
    
    # Mock _reprocess_worker_stage_intellifiller to do nothing
    monkeypatch.setattr(kardenwort_desk, '_reprocess_worker_stage_intellifiller', lambda *a, **kw: a[4])
    
    # Mock write_update_js
    write_calls = []
    def mock_write_update_js(tsv_p, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None, class_cols=None):
        write_calls.append(class_cols)
        
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', mock_write_update_js)
    
    mock_logger = MagicMock()
    monkeypatch.setattr(kardenwort_desk, 'logger', mock_logger)
    
    args = MagicMock()
    args.tsv = str(tsv_path)
    args.rows = "0,1"
    args.config = None
    args.language = 'en'
    
    kardenwort_desk.cmd_reprocess_worker(args)
    
    # Print logger errors if any
    for call in mock_logger.error.call_args_list:
        print(f"LOGGER ERROR: {call}")
        
    # Verify TSV was updated with classification values
    comments, headers, data_rows = kardenwort_desk.load_tsv_rows(tsv_path)
    col_idx = headers.index("ClassificationOxford")
    assert data_rows[0][col_idx] == "3k:A1"
    assert data_rows[1][col_idx] == "5k:B2"
    
    # Verify write_update_js received the classification columns
    assert len(write_calls) == 1
    assert write_calls[0] == [("oxford", col_idx)]



