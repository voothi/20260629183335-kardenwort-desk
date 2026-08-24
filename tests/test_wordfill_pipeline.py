"""
tests/test_wordfill_pipeline.py - Integration and Unit tests for Wordfill pipeline restoration.
Verifies auto-resolution in controller/render/lookup, progressive worker bypass, and reprocess filtering.
"""
import sys
import csv
import json
import configparser
from pathlib import Path
import pytest

import kardenwort_desk as desk
from kardenwort_controller import SessionArbiter


def _create_test_config(tmp_path, enabled=True, target_quality='any', target_fallback=True):
    config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    config.add_section('settings')
    config.set('settings', 'default_language', 'de')
    config.set('settings', 'default_target_language', 'ru')
    config.set('settings', 'anki_mapping_file', str(tmp_path / 'anki-mapping.ini'))
    config.set('settings', 'favorites_output_dir', str(tmp_path / 'favorites'))

    config.add_section('languages')
    config.set('languages', 'de_prompt', 'de_prompt.txt')
    config.set('languages', 'en_prompt', 'en_prompt.txt')

    config.add_section('pipeline')
    config.set('pipeline', 'lemma_base_provider', 'google')
    config.set('pipeline', 'lemma_reprocess_provider', 'intellifiller')
    config.set('pipeline', 'text_base_provider', 'google')
    config.set('pipeline', 'run_lemmatizer', 'true')

    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    config.set('triggers', 'run_text_translation', 'auto')
    config.set('triggers', 'run_lemma_enrichment', 'auto')

    config.add_section('translation')
    config.set('translation', 'translation_order', 'top_to_bottom')
    config.set('translation', 'translation_chunk_size', '15')

    config.add_section('goldendict')
    config.set('goldendict', 'format', 'html')
    config.set('goldendict', 'lookup_ttl_seconds', '300')
    config.set('goldendict', 'theme', 'dark')

    config.add_section('storage')
    config.set('storage', 'backend', 'tsv')
    config.set('storage', 'sqlite_db_path', str(tmp_path / 'kardenwort.db'))

    config.add_section('wordfill')
    config.set('wordfill', 'enabled', 'true' if enabled else 'false')
    config.set('wordfill', 'scan_roots', str(tmp_path / 'corpus'))
    config.set('wordfill', 'scan_depth', '1')
    config.set('wordfill', 'scan_scope', 'all')
    config.set('wordfill', 'scan_sort_order', 'chronological')
    config.set('wordfill', 'scan_match_language', 'true')
    config.set('wordfill', 'scan_max_files', '50')
    config.set('wordfill', 'target_quality', target_quality)
    config.set('wordfill', 'target_fallback', 'true' if target_fallback else 'false')

    # Write dummy anki mapping
    mapping_file = tmp_path / 'anki-mapping.ini'
    mapping_file.write_text(
        "[desk_columns]\n"
        "WordSource = lemma\n"
        "WordDestination = word_translation\n"
        "WordSourceIPA = ipa\n"
        "WordSourceMorphologyAI = morphology\n"
        "SentenceSource = sentence_source\n"
        "SentenceDestination = sentence_destination\n"
        "SentenceDestination2 = sentence_destination\n"
        "SentenceSourceIndex = sentence_index\n"
        "DeskSelected = selected\n",
        encoding='utf-8'
    )

    resolved_paths = {
        'base_dir': tmp_path,
        'anki_mapping_file': mapping_file,
        'favorites_output_dir': tmp_path / 'favorites',
        'generated_results_dir': tmp_path / 'results',
        'kardenwort_workspace': tmp_path,
        'sqlite_db_path': tmp_path / 'kardenwort.db',
        'storage_backend': 'tsv',
    }
    (tmp_path / 'results').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'corpus').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'favorites').mkdir(parents=True, exist_ok=True)

    return config, resolved_paths


def test_resolve_wordfill_config(tmp_path):
    """Verifies resolve_wordfill_config correctly parses config and resolved_paths."""
    config, resolved_paths = _create_test_config(tmp_path, enabled=True, target_quality='full')
    wf = desk.resolve_wordfill_config(config, resolved_paths)

    assert wf['enabled'] is True
    assert wf['target_quality'] == 'full'
    assert wf['scan_depth'] == 1
    assert len(wf['scan_roots']) == 1
    assert wf['scan_roots'][0] == (tmp_path / 'corpus').resolve()
    assert wf['sqlite_db_path'] == (tmp_path / 'kardenwort.db').resolve()


def test_controller_and_render_auto_resolve_wordfill(tmp_path, monkeypatch):
    """
    Task 3.1: Verifies controller SessionArbiter and run_render_flow automatically
    resolve and apply wordfill to data_rows without requiring explicit wordfill_cfg passing.
    """
    config, resolved_paths = _create_test_config(tmp_path, enabled=True)

    # Populate corpus TSV with "haus" -> "дом"
    corpus_tsv = tmp_path / 'corpus' / '20260801120000-corpus.de.tsv'
    headers = [
        'WordSource', 'WordDestination', 'WordSourceIPA', 'WordSourceMorphologyAI',
        'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination'
    ]
    rows = [['haus', 'дом', '/haʊs/', 'Substantiv, Neutrum', '0', 'Das Haus ist groß.', 'Дом большой.']]
    with open(corpus_tsv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for r in rows:
            writer.writerow(r)

    # Mock sentence translation
    monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {0: 'Дом большой.', 'FULL_TEXT': 'Дом большой.'})
    monkeypatch.setattr(desk, 'verify_language', lambda *a, **kw: type('LangRes', (), {'is_match': True, 'action': 'allow'})())

    # Mock tokenize/prepare_lookup_tsv to produce empty word_destination
    def mock_prepare_lookup_tsv(text, lang, target_lang, cfg, res_paths, zid, **kw):
        out_tsv = res_paths['generated_results_dir'] / f"{zid}-haus.de.tsv"
        with open(out_tsv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter='\t', lineterminator='\n')
            writer.writerow(headers)
            writer.writerow(['haus', '', '', '', '0', 'Das Haus.', ''])
        return out_tsv

    monkeypatch.setattr(desk, 'prepare_lookup_tsv', mock_prepare_lookup_tsv)

    # 1. Test SessionArbiter.create_session (controller)
    arbiter = SessionArbiter(config, resolved_paths)
    res = arbiter.create_session("haus", "de", bypass_lang_check=True)

    data_rows = res["data_rows"]
    col_lemma = res["headers"].index("WordSource")
    col_dest = res["headers"].index("WordDestination")
    col_ipa = res["headers"].index("WordSourceIPA")

    assert data_rows[0][col_lemma] == "haus"
    assert data_rows[0][col_dest] == "дом"
    assert data_rows[0][col_ipa] == "/haʊs/"

    # 2. Test run_render_flow with wordfill_cfg=None
    html = desk.run_render_flow(
        text="haus",
        language="de",
        zid="20260824120000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        wordfill_cfg=None
    )
    assert "дом" in html


def test_progressive_worker_skips_base_provider_on_wordfill_match(tmp_path, monkeypatch):
    """
    Task 3.2: Verifies progressive translation worker stage queries find_wordfill_match
    and skips lemma_base_provider when lemmas match wordfill.
    """
    config, resolved_paths = _create_test_config(tmp_path, enabled=True)

    # Populate corpus TSV with "baum" -> "дерево"
    corpus_tsv = tmp_path / 'corpus' / '20260801120000-corpus.de.tsv'
    headers = [
        'WordSource', 'WordDestination', 'WordSourceIPA', 'WordSourceMorphologyAI',
        'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination', 'TokenOrder'
    ]
    rows = [['baum', 'дерево', '/baʊm/', 'Substantiv, Maskulinum', '0', 'Der Baum.', 'Дерево.', '0']]
    with open(corpus_tsv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for r in rows:
            writer.writerow(r)

    # Create session TSV on disk with empty WordDestination for "baum"
    sess_zid = "20260824130000"
    sess_tsv = resolved_paths['generated_results_dir'] / f"{sess_zid}-baum.de.tsv"
    sess_rows = [['baum', '', '', '', '0', 'Der Baum.', 'Дерево.', '0']]
    with open(sess_tsv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for r in sess_rows:
            writer.writerow(r)

    # Track calls to fast-path translation provider
    fast_path_calls = []
    def mock_translate_lemmas(chunk, lang, target_lang, cfg, paths, provider):
        fast_path_calls.append(chunk)
        return {w: 'translated_' + w for w in chunk}

    monkeypatch.setattr(desk, 'translate_lemmas_fast_path', mock_translate_lemmas)
    monkeypatch.setattr(desk, 'safe_write_update_js', lambda *a, **kw: None)

    class MockArgs:
        config = None
        tsv = str(sess_tsv)
        language = 'de'
        target_lang = 'ru'
        text_mode = 'single'
        zid = sess_zid

    mapping = desk.load_anki_mapping(resolved_paths['anki_mapping_file'])
    role_fields = desk.get_role_fields(mapping, headers)

    out_rows = desk._progressive_worker_stage_translation_impl(
        sess_tsv, MockArgs(), config, resolved_paths, sess_rows, headers, role_fields, sess_zid
    )

    # Verify translate_lemmas_fast_path was NOT called because 'baum' was resolved via wordfill
    assert len(fast_path_calls) == 0, f"Expected fast path to be bypassed, but was called with: {fast_path_calls}"
    assert out_rows[0][1] == "дерево"
    assert out_rows[0][2] == "/baʊm/"


def test_reword_and_reprocess_prefill_and_filter_intellifiller(tmp_path, monkeypatch):
    """
    Task 3.3: Verifies Re-word / reprocess pre-fills from wordfill and skips
    IntelliFiller for matching high-quality rows.
    """
    config, resolved_paths = _create_test_config(tmp_path, enabled=True, target_quality='full')

    # Corpus has full quality match for "apfel", but no match for "birne"
    corpus_tsv = tmp_path / 'corpus' / '20260801120000-corpus.de.tsv'
    headers = [
        'WordSource', 'WordDestination', 'WordSourceIPA', 'WordSourceMorphologyAI',
        'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination', 'TokenOrder'
    ]
    corpus_rows = [['apfel', 'яблоко', '/ˈapfəl/', 'Substantiv, Maskulinum', '0', 'Der Apfel.', 'Яблоко.', '0']]
    with open(corpus_tsv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for r in corpus_rows:
            writer.writerow(r)

    # Session TSV with 2 rows
    sess_zid = "20260824140000"
    sess_tsv = resolved_paths['generated_results_dir'] / f"{sess_zid}-fruits.de.tsv"
    sess_rows = [
        ['apfel', '', '', '', '0', 'Der Apfel.', '', '0'],
        ['birne', '', '', '', '0', 'Die Birne.', '', '1']
    ]
    with open(sess_tsv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for r in sess_rows:
            writer.writerow(r)

    # Track IntelliFiller dispatches
    intellifiller_calls = []
    def mock_headless_intellifiller(tsv, prompt, cfg, paths, selected_rows=None, **kw):
        intellifiller_calls.append(list(selected_rows or []))

    monkeypatch.setattr(desk, 'run_headless_intellifiller', mock_headless_intellifiller)
    import kardenwort_controller
    monkeypatch.setattr(kardenwort_controller, 'run_headless_intellifiller', mock_headless_intellifiller)
    monkeypatch.setattr(desk, 'safe_write_update_js', lambda *a, **kw: None)

    # 1. Test cmd_reprocess_worker with rows="0,1"
    class ReprocessArgs:
        config = None
        tsv = str(sess_tsv)
        rows = "0,1"
        zid = sess_zid
        prompt = None

    # Save initial config to load_config mock
    monkeypatch.setattr(desk, 'load_config', lambda *a, **kw: (config, resolved_paths, {}, desk.resolve_wordfill_config(config, resolved_paths)))
    desk.cmd_reprocess_worker(ReprocessArgs())

    # Row 0 ("apfel") was pre-filled from wordfill; IntelliFiller was only dispatched for row 1 ("birne")!
    assert len(intellifiller_calls) == 1
    assert intellifiller_calls[0] == [1]

    # Verify disk content has pre-filled "apfel"
    _, _, updated_rows = desk.load_tsv_rows(sess_tsv)
    assert updated_rows[0][0] == 'apfel'
    assert updated_rows[0][1] == 'яблоко'
    assert updated_rows[0][2] == '/ˈapfəl/'
    assert updated_rows[0][3] == 'Substantiv, Maskulinum'

    # 2. Test SessionArbiter.reword_session
    intellifiller_calls.clear()
    arbiter = SessionArbiter(config, resolved_paths)
    arbiter.reword_session(session_zid=sess_zid, selected_rows=[0, 1], language='de')

    # In reword_session, row 0 meets target_quality and is excluded; only row 1 is sent to intellifiller
    assert len(intellifiller_calls) == 1
    assert intellifiller_calls[0] == [1]
