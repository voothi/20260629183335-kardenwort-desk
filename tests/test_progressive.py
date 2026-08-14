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


# ---------------------------------------------------------------------------
# Task 2.3: Master window in Sentences Mode streams from children and must
# emit skeleton loaders in pending translation cells (but NOT for IPA/Morph
# when run_lemma_enrichment = manual).
# ---------------------------------------------------------------------------

def test_master_window_sentences_mode_translation_skeleton(monkeypatch, tmp_path):
    """
    Reproduces the Sentences Mode master window: it is rendered monolithic
    (progressive text streaming is incompatible with the multi-window
    architecture) but still receives lemma data progressively from children.

    Before the fix, skeleton generation was gated solely by `is_progressive`,
    so the master's empty translation cells never shimmered. After the fix the
    decision is decoupled from display mode and tied to provider expectation,
    so a pending translation cell (run_lemma_base_translation = auto) emits a
    skeleton loader, while IPA/Morphology stay empty (run_lemma_enrichment = manual).
    """
    config = configparser.ConfigParser()
    config.read_string(f"""
[settings]
default_target_language=ru
[rendering]
display_mode=progressive
[translation]
translation_wrap_max_chars=90
[pipeline]
progressive_text_translation=true
progressive_timeout_seconds=15
lemma_base_provider=google
lemma_reprocess_provider=intellifiller
[triggers]
run_text_translation=auto
run_lemma_base_translation=auto
run_lemma_enrichment=manual
[sentences_mode]
enabled=true
parent_mode=table
[languages]
de_prompt=
[fields]
""")

    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        "WordSource=\nWordDestination=\nWordSourceIPA=\nWordSourceMorphology=\n"
        "SentenceSourceIndex=\nSentenceDestination=\nDeskSelected=\nWordSourceInflectedForm=\n"
        "[fields_mapping.word]\n"
        "WordSource=lemma\nWordDestination=word_translation\nWordSourceIPA=word_ipa\n"
        "WordSourceMorphology=word_morphology\nSentenceSourceIndex=sentence_index\n"
        "SentenceDestination=sentence_destination\nDeskSelected=selected\n"
        "WordSourceInflectedForm=inflected\n",
        encoding="utf-8",
    )

    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': tmp_path / 'dummy.py',
        'kardenwort_python': tmp_path / 'python',
        'anki_mapping_file': mapping_file,
        'kardenwort_workspace': tmp_path,
        'settings_file': tmp_path / 'settings.ini',
    }

    headers = [
        "WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphology",
        "SentenceSourceIndex", "SentenceDestination", "DeskSelected", "WordSourceInflectedForm",
    ]
    data_rows = [["Haus", "", "", "", "1", "", "0", ""]]

    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "word_ipa": "WordSourceIPA",
        "word_morphology": "WordSourceMorphology",
        "sentence_index": "SentenceSourceIndex",
        "sentence_destination": "SentenceDestination",
        "selected": "DeskSelected",
        "inflected": "WordSourceInflectedForm",
    }

    # Master TSV with a 14-digit ZID prefix...
    master_tsv = tmp_path / "20260810153000-master.en.tsv"
    master_tsv.write_text("\t".join(headers) + "\n" + "\t".join(data_rows[0]) + "\n", encoding="utf-8")
    # ...and a child TSV a few seconds later, so the master is detected as a
    # parent window with active children (forces monolithic display + worker).
    child_tsv = tmp_path / "20260810153005-child.en.tsv"
    child_tsv.write_text("", encoding="utf-8")

    monkeypatch.setattr(desk, 'load_anki_mapping', lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'load_tsv_rows', lambda p: ([""], headers, [list(r) for r in data_rows]))
    monkeypatch.setattr(desk, 'is_tsv_llm_filled', lambda *a, **kw: False)
    monkeypatch.setattr(desk, 'get_role_fields', lambda m, h: role_fields)
    monkeypatch.setattr(desk, 'load_kardenwort_config', lambda w: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'resolve_results_dir', lambda rp, kw: tmp_path)
    monkeypatch.setattr(desk, 'prepare_lookup_tsv', lambda *a, **kw: master_tsv)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'write_update_js', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'spawn_ahk', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {0: "Haus-DE"})
    monkeypatch.setattr(desk, 'resolve_translations', lambda *a, **kw: None)

    html_out = desk.run_render_flow(
        "Haus ist gut.\nDas Buch ist rot.",
        "de",
        "20260810153010",
        "multi",
        config,
        resolved_paths,
        tsv_path=str(master_tsv),
    )

    # Translation cell is pending and a provider is expected to fill it -> skeleton.
    assert "skeleton-loader" in html_out, "Master window translation cell must emit a skeleton loader"
    assert 'width: 60px' in html_out, "Translation skeleton (width 60px) must be present"

    # IPA / Morphology are manual -> must NOT shimmer.
    assert 'width: 50px' not in html_out, "IPA skeleton must not appear when run_lemma_enrichment = manual"
    assert 'width: 80px' not in html_out, "Morphology skeleton must not appear when run_lemma_enrichment = manual"


def test_progressive_worker_child_single_mode_syncs_with_siblings(monkeypatch, tmp_path):
    """
    Verify that cmd_progressive_worker in single text-mode synchronizes with older
    siblings and cross-pollinates translations when sibling batch files exist on disk.
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

    # Older sibling TSV: completed base translation for 'Haus'
    sibling_tsv = tmp_path / "20260809190000-master.en.tsv"
    sibling_tsv.write_text("WordSource\tWordDestination\tWordSourceIPA\nHaus\thouse\t/haʊs/\n", encoding="utf-8")
    sibling_tsv.with_suffix('.base_translation_done').touch()

    # Child TSV: single text-mode with 'Haus' untranslated
    child_tsv = tmp_path / "20260809190005-child.en.tsv"
    child_tsv.write_text("WordSource\tWordDestination\tWordSourceIPA\nHaus\t\t\n", encoding="utf-8")

    args = types.SimpleNamespace()
    args.tsv = str(child_tsv)
    args.config = str(config_path)
    args.text_mode = "single"
    args.skip_intellifiller = True

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

    monkeypatch.setattr(desk, 'load_config', mock_load_config)
    monkeypatch.setattr(desk, 'write_update_js', lambda *a, **kw: None)

    # Run the progressive worker for child in single mode
    desk.cmd_progressive_worker(args)

    # Verify that child TSV inherited "house" via cross-pollination without redundant API calls
    _, headers, rows = desk.load_tsv_rows(child_tsv)
    assert rows[0][1] == "house", "Child worker in single mode should cross-pollinate WordDestination from sibling"


def test_single_mode_master_window_launches_progressive_worker(monkeypatch, tmp_path):
    """
    Verify that when text_mode is 'single' and children TSVs exist on disk,
    run_render_flow recognizes the master window and launches run_progressive_worker_async.
    """
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[rendering]
display_mode=progressive
[pipeline]
progressive_text_translation=true
progressive_timeout_seconds=15
lemma_base_provider=google
lemma_reprocess_provider=intellifiller
[triggers]
run_text_translation=auto
run_lemma_base_translation=auto
run_lemma_enrichment=manual
[sentences_mode]
enabled=true
parent_mode=table
[languages]
de_prompt=
[fields]
""")

    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        "WordSource=\nWordDestination=\n"
        "SentenceSourceIndex=\nSentenceDestination=\nDeskSelected=\n"
        "[fields_mapping.word]\n"
        "WordSource=lemma\nWordDestination=word_translation\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceDestination=sentence_destination\nDeskSelected=selected\n",
        encoding="utf-8",
    )

    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': tmp_path / 'dummy.py',
        'kardenwort_python': tmp_path / 'python',
        'anki_mapping_file': mapping_file,
        'kardenwort_workspace': tmp_path,
        'settings_file': tmp_path / 'settings.ini',
    }

    headers = [
        "WordSource", "WordDestination",
        "SentenceSourceIndex", "SentenceDestination", "DeskSelected",
    ]
    data_rows = [["Haus", "", "1", "", "0"]]

    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "sentence_index": "SentenceSourceIndex",
        "sentence_destination": "SentenceDestination",
        "selected": "DeskSelected",
    }

    master_tsv = tmp_path / "20260814120000-master.de.tsv"
    master_tsv.write_text("\t".join(headers) + "\n" + "\t".join(data_rows[0]) + "\n", encoding="utf-8")
    child_tsv = tmp_path / "20260814120005-child.de.tsv"
    child_tsv.write_text("", encoding="utf-8")

    worker_calls = []

    monkeypatch.setattr(desk, 'load_anki_mapping', lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'load_tsv_rows', lambda p: ([""], headers, [list(r) for r in data_rows]))
    monkeypatch.setattr(desk, 'is_tsv_llm_filled', lambda *a, **kw: False)
    monkeypatch.setattr(desk, 'get_role_fields', lambda m, h: role_fields)
    monkeypatch.setattr(desk, 'load_kardenwort_config', lambda w: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'resolve_results_dir', lambda rp, kw: tmp_path)
    monkeypatch.setattr(desk, 'prepare_lookup_tsv', lambda *a, **kw: master_tsv)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', lambda *a, **kw: worker_calls.append((a, kw)))
    monkeypatch.setattr(desk, 'write_update_js', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'spawn_ahk', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {0: "Haus-DE"})
    monkeypatch.setattr(desk, 'resolve_translations', lambda *a, **kw: None)

    html_out = desk.run_render_flow(
        "Haus ist gut. Das Buch ist rot.",
        "de",
        "20260814120000",
        "single",
        config,
        resolved_paths,
        tsv_path=str(master_tsv),
    )

    assert len(worker_calls) == 1, "run_progressive_worker_async must be called for master window in single mode"
    assert "skeleton-loader" in html_out, "Master window in single mode must render skeleton loader for pending lemmas"

