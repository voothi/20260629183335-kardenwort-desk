from kardenwort_desk import (
    SEC_SETTINGS, SEC_TOKEN_MAPPINGS, SEC_MERGE, SEC_SENTENCES_MODE,
    SEC_CLASSIFICATION, SEC_TIMEOUTS, SEC_PIPELINE, SEC_TRIGGERS,
    SEC_TRANSLATION, SEC_TRANSLATION_PROVIDERS, SEC_RENDERING,
    SEC_ENVIRONMENT, SEC_LANGUAGES, SEC_LANGUAGE_RESOURCES,
    SEC_PROJECT_STRUCTURE, SEC_AUDIO, SEC_GOLDENDICT, SEC_WORDFILL
)
import pytest
import json
import subprocess
import configparser
import sys
import os
import time
from pathlib import Path
from unittest.mock import MagicMock
import kardenwort_desk
from tests.controller_test_support import controller_world, DeterministicTranslationHandler
from tests.test_lookup_cli import lookup_cli_world

def setup_test_env(tmp_path):
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
save_source_text=true
use_simplemma_correction=true
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
        assert "--use-simplemma-correction" in cmd, "Expected --use-simplemma-correction to be passed to subprocess"
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

def test_lookup_utf8_stdout(monkeypatch, lookup_cli_world):
    import io
    server, configuration = lookup_cli_world

    def engine(handler, provider, text, source, target):
        handler.server.provider_calls.append((text, source, target))
        return '\u0442\u0435\u0441\u0442'

    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', engine)

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
        config = str(configuration)
        zid = "20261111203200"
        verbose = False
        debug = False
        disable_css = False
        
    with pytest.raises(SystemExit):
        kardenwort_desk.cmd_lookup(Args())
        
    mock_stdout.seek(0)
    decoded = mock_stdout.read()
    assert "тест" in decoded
    cold = kardenwort_desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    assert cold.db.get_sentences_by_session(Args.zid)[0]['sentence_destination'] == '\u0442\u0435\u0441\u0442'
    assert cold.db.get_words_by_session(Args.zid)
    assert server.translation_service.provider_calls


def run_lookup_worker_profile(server, monkeypatch, profile):
    """Actual deprecated worker stages with owned TSV inputs and unmanaged HTTP engines."""
    import types
    from tests.enrichment_test_support import enrichment_engine
    from translate_server import TranslationServerException
    config, paths = server.config, server.resolved_paths
    count = 3 if profile == 'stages' else 1
    fail_text = profile == 'failure'
    config.read_dict({'settings': {'default_language': 'en', 'default_target_language': 'ru',
        'anki_mapping_file': str(paths['anki_mapping_file']), 'intellifiller_batch_size': '2'},
        'server': {'update_delivery': 'desktop_file'}, 'wordfill': {'enabled': 'false'},
        'pipeline': {'text_base_provider': 'deepl' if fail_text else 'google',
                     'text_provider_chain': 'deepl' if fail_text else 'google',
                     'lemma_reprocess_provider': 'intellifiller'},
        'triggers': {'run_text_translation': 'manual' if profile == 'enrichment' else 'auto',
                     'run_lemma_base_translation': 'manual' if profile == 'enrichment' else 'auto',
                     'run_lemma_enrichment': 'manual' if profile == 'mode' else 'auto'}})
    identity = '20261111203500'
    tsv_path = paths['results_dir']/f'{identity}-worker.en.tsv'
    headers = ['WordSource', 'WordSourceMorphologyAI', 'WordSourceIPA', 'WordDestination',
               'TokenOrder', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination']
    text = ' '.join(f'word{i}' for i in range(1, count+1))
    rows = [[f'word{i}', '', '', '', str(i-1), '1', text, ''] for i in range(1, count+1)]
    kardenwort_desk.save_tsv_rows_safely(tsv_path, ['# language=en'], headers, rows)
    tsv_path.with_suffix('.txt').write_text(text, encoding='utf-8')
    write_calls, text_modes = [], []
    original_write = kardenwort_desk.write_update_js
    original_translate = kardenwort_desk.translate_source_text
    original_engine = DeterministicTranslationHandler.translate

    def observe_write(path, data_rows, headers, roles, *args, **kwargs):
        write_calls.append((kwargs.get('stage'), kwargs.get('status', 'success'), len(data_rows)))
        return original_write(path, data_rows, headers, roles, *args, **kwargs)

    def observe_mode(text, source, target, text_mode, *args, **kwargs):
        text_modes.append(text_mode)
        return original_translate(text, source, target, text_mode, *args, **kwargs)

    def engine(handler, provider, text, source, target):
        if fail_text and provider == 'deepl':
            handler.server.provider_calls.append((text, source, target))
            raise TranslationServerException(503, 'ERR_NETWORK_UNREACHABLE', 'Owned text provider unavailable')
        return original_engine(handler, provider, text, source, target)

    monkeypatch.setattr(kardenwort_desk, 'write_update_js', observe_write)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', observe_mode)
    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', engine)
    with enrichment_engine(config) as enrichment:
        enrichment.release.set()
        configuration = paths['base_dir']/'worker-config.ini'
        with configuration.open('w', encoding='utf-8') as stream:
            config.write(stream)
        args = types.SimpleNamespace(config=str(configuration), tsv=str(tsv_path), language='en',
            target_lang='ru', prompt='en_prompt', provider='google', word_empty='true',
            text_mode='multi_line' if profile == 'mode' else 'single',
            skip_intellifiller=profile == 'mode', zid=identity, trace_id=f'{identity}:test')
        kardenwort_desk.cmd_progressive_worker(args)
        _, stored_headers, stored = kardenwort_desk.load_tsv_rows(tsv_path)
        assert len(stored) == count
        if profile != 'mode':
            assert len(enrichment.requests) == (2 if count == 3 else 1)
            assert all(row[stored_headers.index('WordSourceMorphologyAI')].startswith('morph_word') for row in stored)
            assert all(row[stored_headers.index('WordSourceIPA')].startswith('/ipa_word') for row in stored)
        if profile == 'enrichment':
            assert server.translation_service.provider_calls == []
        else:
            assert server.translation_service.provider_calls
        payloads = [json.loads(path.read_text(encoding='utf-8').split('window.receiveUpdate(', 1)[1][:-4])
                    for path in sorted(tsv_path.with_suffix('.updates').glob('*.js'))]
        final = payloads[-1]
        assert final['stage'] == 'finished'
        assert final['status'] == ('failed' if fail_text else 'success')
    return write_calls, text_modes


@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_progressive_worker_stages(monkeypatch, controller_world):
    _, server = controller_world
    write_calls, _ = run_lookup_worker_profile(server, monkeypatch, 'stages')

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

@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_progressive_worker_failure_isolation(monkeypatch, controller_world):
    _, server = controller_world
    calls, _ = run_lookup_worker_profile(server, monkeypatch, 'failure')
    write_calls = [(stage, status) for stage, status, count in calls]

    # Even though text translation failed non-fatally, lemma translation, enrichment, and finished still ran!
    assert len(write_calls) == 6
    assert write_calls[0] == ('source', 'success')
    assert write_calls[1] == ('translated_text', 'success')
    assert write_calls[3] == ('translated', 'success')
    assert write_calls[4] == ('enrichment', 'success')
    assert write_calls[5] == ('finished', 'failed')

@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_progressive_worker_d3_enrichment_only(monkeypatch, controller_world):
    _, server = controller_world
    calls, _ = run_lookup_worker_profile(server, monkeypatch, 'enrichment')
    write_calls = [stage for stage, status, count in calls]

    assert "translated" not in write_calls
    assert "enrichment" in write_calls

@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_progressive_worker_d4_text_mode(monkeypatch, controller_world):
    _, server = controller_world
    _, modes = run_lookup_worker_profile(server, monkeypatch, 'mode')
    passed_text_mode = modes[0]

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
    config.add_section(SEC_CLASSIFICATION)
    config.set(SEC_CLASSIFICATION, 'enabled', 'true')
    
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


def test_lookup_simplemma_new_flags_forwarding(monkeypatch, tmp_path):
    import subprocess
    from pathlib import Path
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    config.set('settings', 'simplemma_after_spacy', 'true')
    config.set('settings', 'simplemma_pos_aware', 'true')
    config.set('settings', 'simplemma_smart_fallback', 'true')
    
    mock_cmds = []
    def mock_run(*args, **kwargs):
        cmd = args[0]
        mock_cmds.append(cmd)
        out_idx = cmd.index("--output-file") + 1
        out_file = Path(cmd[out_idx])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("WordSource\tWordDestination\ntest\t\n", encoding='utf-8')
        class MockProc:
            returncode = 0
            stdout = ""
            stderr = ""
        return MockProc()
        
    monkeypatch.setattr(subprocess, 'run', mock_run)
    monkeypatch.setattr(kardenwort_desk, 'translate_source_text', lambda *a, **kw: {0: "working"})
    monkeypatch.setattr(kardenwort_desk, 'translate_lemmas_fast_path', lambda *a, **kw: {'test': "test_transl"})
    
    kardenwort_desk.run_lookup_flow("test text simplemma", "en", "ru", "html", config, resolved_paths, goldendict, "zid_simplemma")
    assert len(mock_cmds) >= 1
    assert "--simplemma-after-spacy" in mock_cmds[0]
    assert "--simplemma-pos-aware" in mock_cmds[0]
    assert "--simplemma-smart-fallback" in mock_cmds[0]

def test_pad_translated_sentences():
    from kardenwort_desk import pad_translated_sentences
    sentences = [
        "Он вошел в темную комнату.",
        "Он включил свет.",
        "Лампочка слегка мигнула.",
        "Внутри было очень холодно."
    ]
    
    padded = pad_translated_sentences(sentences, words_before=5, words_after=5)
    
    # 2nd sentence: takes 5 from end of 1st, 5 from start of 3rd
    assert padded[1] == "Он вошел в темную комнату. Он включил свет. Лампочка слегка мигнула."
    
    # 3rd sentence
    assert padded[2] == "Он включил свет. Лампочка слегка мигнула. Внутри было очень холодно."
    
    # 1st sentence (no before)
    assert padded[0] == "Он вошел в темную комнату. Он включил свет."
    
    # 4th sentence (no after)
    assert padded[3] == "Лампочка слегка мигнула. Внутри было очень холодно."

def test_translate_source_text_native_padding(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    config.set('settings', 'anki_translated_context_words_before', '5')
    config.set('settings', 'anki_translated_context_words_after', '5')
    config.set('settings', 'anki_context_words_before', '0')
    config.set('settings', 'anki_context_words_after', '0')
    if not config.has_section('translation'):
        config.add_section('translation')
    config.set('translation', 'translation_wrap_max_chars', '90')
    
    # Must NOT have newlines, otherwise _effective_text_mode forces 'multi' and bypasses padding!
    text = "He walked into the dark room. He turned on the light. The bulb flickered slightly. It was very cold inside."
    
    mock_calls = []
    def mock_translate_text(txt, source_lang, target_lang, config, resolved_paths, provider, **kwargs):
        mock_calls.append(txt)
        if "He walked into the dark room." in txt:
            return "Он вошел в темную комнату.\nОн включил свет.\nЛампочка слегка мигнула.\nВнутри было очень холодно."
        return "fallback"
        
    monkeypatch.setattr(kardenwort_desk, 'translate_text', mock_translate_text)
    
    res = kardenwort_desk.translate_source_text(text, "en", "ru", "single", config, resolved_paths, "google")
    
    # Assert API was called exactly ONCE for the block
    assert len(mock_calls) == 1
    assert "He walked into the dark room." in mock_calls[0]
    
    # Assert padding was natively applied in the returned dictionary under 'PADDED' while integer keys stay clean
    assert "FULL_TEXT" in res
    assert res[1] == "Он включил свет."
    assert "PADDED" in res
    assert res["PADDED"][1] == "Он вошел в темную комнату. Он включил свет. Лампочка слегка мигнула."


def test_cmd_reprocess_worker_handles_intellifiller_error(monkeypatch, tmp_path):
    config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)
    monkeypatch.setattr(kardenwort_desk, 'load_config', lambda *a, **kw: (config, resolved_paths, goldendict, {}))
    
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    
    mapping = configparser.ConfigParser()
    mapping.read_string("""
[fields]
WordSource=lemma
WordDestination=word_translation
[desk_columns]
WordSource=lemma
WordDestination=word_translation
""")
    with open(resolved_paths['anki_mapping_file'], 'w') as f:
        mapping.write(f)
        
    config.set(SEC_PIPELINE, 'lemma_reprocess_provider', 'intellifiller')
    
    tsv_path = kardenwort_workspace / "results" / "20260818190200-test.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordDestination\napple\t\n", encoding='utf-8')
    tsv_path.with_suffix('.txt').write_text("apple", encoding='utf-8')
    
    error_envelope = {
        "status": "error",
        "zid": "20260818190200",
        "trace_id": "20260818190200:reprocess:worker",
        "code": "ERR_LLM_RATE_LIMIT",
        "message": "OpenAI API rate limit exceeded (HTTP 429)",
        "row_id": 0,
        "retryable": True,
        "details": {"http_status": 429}
    }
    
    def mock_run_intellifiller(*a, **kw):
        raise kardenwort_desk.IntelliFillerError("Rate limit exceeded", envelope=error_envelope)
        
    monkeypatch.setattr(kardenwort_desk, 'run_headless_intellifiller', mock_run_intellifiller)
    
    captured_updates = []
    def mock_write_update_js(*args, **kwargs):
        captured_updates.append(kwargs)
        
    monkeypatch.setattr(kardenwort_desk, 'write_update_js', mock_write_update_js)
    
    args = MagicMock()
    args.tsv = str(tsv_path)
    args.rows = "0"
    args.config = None
    args.prompt = "English"
    args.zid = "20260818190200"
    args.trace_id = "20260818190200:reprocess:worker"
    
    # Execution should not raise exception
    kardenwort_desk.cmd_reprocess_worker(args)
    
    # Verify update JS was written with stage="finished" and status="failed" and structured error
    finished_updates = [u for u in captured_updates if u.get('stage') == 'finished']
    assert len(finished_updates) >= 1
    last_update = finished_updates[-1]
    assert last_update["status"] == "failed"
    assert last_update["error"]["code"] == "ERR_LLM_RATE_LIMIT"
    assert last_update["error"]["retryable"] is True

