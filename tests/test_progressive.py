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
    assert 'id="kardenwort-children"' in html_out, "Master window must contain children div for AHK hierarchy tracking"


def test_child_window_does_not_claim_younger_siblings_as_children(monkeypatch, tmp_path):
    """
    Verify that a child window (which has older siblings in the temporal cluster)
    does NOT claim younger siblings as its children, does NOT set is_master_window=True,
    and does NOT inject <div id='kardenwort-children'>.
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

    headers = ["WordSource", "WordDestination", "SentenceSourceIndex", "SentenceDestination", "DeskSelected"]
    data_rows = [["Haus", "", "1", "", "0"]]

    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "sentence_index": "SentenceSourceIndex",
        "sentence_destination": "SentenceDestination",
        "selected": "DeskSelected",
    }

    # Sibling files in the same batch
    master_tsv = tmp_path / "20260814120000-master.de.tsv"
    master_tsv.write_text("\t".join(headers) + "\n", encoding="utf-8")
    child1_tsv = tmp_path / "20260814120001-child1.de.tsv"
    child1_tsv.write_text("\t".join(headers) + "\n" + "\t".join(data_rows[0]) + "\n", encoding="utf-8")
    child2_tsv = tmp_path / "20260814120002-child2.de.tsv"
    child2_tsv.write_text("\t".join(headers) + "\n", encoding="utf-8")

    monkeypatch.setattr(desk, 'load_anki_mapping', lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'load_tsv_rows', lambda p: ([""], headers, [list(r) for r in data_rows]))
    monkeypatch.setattr(desk, 'is_tsv_llm_filled', lambda *a, **kw: False)
    monkeypatch.setattr(desk, 'get_role_fields', lambda m, h: role_fields)
    monkeypatch.setattr(desk, 'load_kardenwort_config', lambda w: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'resolve_results_dir', lambda rp, kw: tmp_path)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'write_update_js', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'spawn_ahk', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {0: "Haus-DE"})
    monkeypatch.setattr(desk, 'resolve_translations', lambda *a, **kw: None)

    # Render child1
    html_out = desk.run_render_flow(
        "Haus ist gut.",
        "de",
        "20260814120001",
        "single",
        config,
        resolved_paths,
        tsv_path=str(child1_tsv),
    )

    # Child 1 must NOT have kardenwort-children div containing child 2
    assert 'id="kardenwort-children"' not in html_out, "Child window must NOT contain kardenwort-children div"


def test_the_cut_respects_sentence_source_index_no_word_bleeding(monkeypatch, tmp_path):
    """
    Verify that during 'the cut' in Sentences Mode, rows with an explicit SentenceSourceIndex
    are assigned strictly to their matching sentence index and not leaked into other sentences
    via fallback substring matching.
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
en_prompt=
[fields]
""")

    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        "WordSource=\nWordDestination=\nWordSourceInflectedForm=\n"
        "SentenceSourceIndex=\nSentenceDestination=\nDeskSelected=\n"
        "[fields_mapping.word]\n"
        "WordSource=lemma\nWordDestination=word_translation\n"
        "WordSourceInflectedForm=inflected\n"
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
        'base_dir': tmp_path,
    }

    headers = [
        "WordSource", "WordDestination", "WordSourceInflectedForm",
        "SentenceSourceIndex", "SentenceDestination", "DeskSelected"
    ]
    # Row 1 belongs to Sentence 1 (SentenceSourceIndex=1)
    # Row 2 belongs to Sentence 2 (SentenceSourceIndex=2), with inflected="e.g."
    # Sentence 1 starts with "E." (so 'e' might match 'e.g.' in buggy fuzzy matching)
    data_rows = [
        ["scalability", "", "Scalability", "1", "", "0"],
        ["for", "", "e.g.", "2", "", "0"],
    ]

    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "inflected": "WordSourceInflectedForm",
        "sentence_index": "SentenceSourceIndex",
        "sentence_destination": "SentenceDestination",
        "selected": "DeskSelected",
    }

    master_tsv = tmp_path / "20260814120000-master.en.tsv"
    master_tsv.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in data_rows) + "\n", encoding="utf-8")

    spawned_children = []

    def mock_spawn_ahk(args, base_dir):
        spawned_children.extend(args)

    monkeypatch.setattr(desk, 'load_anki_mapping', lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'is_tsv_llm_filled', lambda *a, **kw: False)
    monkeypatch.setattr(desk, 'get_role_fields', lambda m, h: role_fields)
    monkeypatch.setattr(desk, 'load_kardenwort_config', lambda w: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'resolve_results_dir', lambda rp, kw: tmp_path)
    monkeypatch.setattr(desk, 'prepare_lookup_tsv', lambda *a, **kw: master_tsv)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'write_update_js', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'spawn_ahk', mock_spawn_ahk)
    monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {0: "Масштабируемость", 1: "Например"})
    monkeypatch.setattr(desk, 'resolve_translations', lambda *a, **kw: None)

    desk.run_render_flow(
        "E. Scalability & Edge Cases.\nSecond sentence e.g. for something.",
        "en",
        "20260814120000",
        "multi",
        config,
        resolved_paths,
        tsv_path=None,
    )

    # Check child 1 TSV generated on disk
    child1_tsv = tmp_path / "20260814120001-e-scalability-edge-cases.en.tsv"
    assert child1_tsv.exists(), "Child 1 TSV must be created on disk"
    _, _, child1_rows = desk.load_tsv_rows(child1_tsv)

    child1_lemmas = [r[0] for r in child1_rows]
    assert "scalability" in child1_lemmas, "Child 1 should contain scalability"
    assert "for" not in child1_lemmas, "Child 1 must NOT contain 'for' from sentence 2"


def test_wait_for_older_siblings_zero_timeout_returns_immediately(tmp_path):
    """
    Verify that wait_for_older_siblings_in_batch and wait_for_older_siblings_enrichment_in_batch
    return immediately (< 0.1s) when timeout <= 0.0, even if older sibling markers do not exist.
    """
    import time

    # Setup older incomplete sibling TSV without markers
    sibling_tsv = tmp_path / "20260809190000-sibling.en.tsv"
    sibling_tsv.write_text("WordSource\tWordDestination\nHaus\t\n", encoding="utf-8")

    working_tsv = tmp_path / "20260809190005-working.en.tsv"
    working_tsv.write_text("WordSource\tWordDestination\nAuto\t\n", encoding="utf-8")

    mapping = {"desk_columns": {"WordSource": "lemma", "WordDestination": "word_translation"}}

    start = time.time()
    desk.wait_for_older_siblings_in_batch(working_tsv, mapping, timeout=0.0)
    duration_base = time.time() - start
    assert duration_base < 0.2, f"Base sibling wait with timeout=0 took {duration_base}s, expected instant return"

    start = time.time()
    desk.wait_for_older_siblings_enrichment_in_batch(working_tsv, timeout=0.0)
    duration_enrich = time.time() - start
    assert duration_enrich < 0.2, f"Enrichment sibling wait with timeout=0 took {duration_enrich}s, expected instant return"


def test_progressive_worker_nonblocking_zero_timeout(monkeypatch, tmp_path):
    """
    Verify that cmd_progressive_worker with sibling_coordination_timeout = 0.0
    executes immediately without blocking on older siblings, translating its own words.
    """
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[settings]\ndefault_language=en\n"
        "[pipeline]\nsibling_coordination_timeout=0.0\nlemma_base_provider=google\nlemma_reprocess_provider=intellifiller\n"
        "[triggers]\nrun_lemma_base_translation=auto\nrun_text_translation=manual\nrun_lemma_enrichment=manual\n"
        "[fields]\n",
        encoding="utf-8",
    )
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[fields_mapping.word]\nWordSource=lemma\nWordDestination=word_translation\nWordSourceIPA=word_ipa\n",
        encoding="utf-8",
    )

    # Older sibling exists but is NOT finished (no .base_translation_done marker)
    sibling_tsv = tmp_path / "20260809190000-older.en.tsv"
    sibling_tsv.write_text("WordSource\tWordDestination\nHaus\t\n", encoding="utf-8")

    # Child TSV: single text-mode with 'Auto' untranslated
    child_tsv = tmp_path / "20260809190005-child.en.tsv"
    child_tsv.write_text("WordSource\tWordDestination\nAuto\t\n", encoding="utf-8")

    args = types.SimpleNamespace()
    args.tsv = str(child_tsv)
    args.config = str(config_path)
    args.text_mode = "single"
    args.skip_intellifiller = True

    def mock_load_config(cfg_path):
        config = configparser.ConfigParser()
        config.read_string(
            "[settings]\ndefault_language=en\n"
            "[pipeline]\nsibling_coordination_timeout=0.0\nlemma_base_provider=google\nlemma_reprocess_provider=intellifiller\n"
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
    monkeypatch.setattr(desk, 'translate_lemmas_fast_path', lambda lemmas, *a, **kw: {l: "car" if l == "Auto" else "trans" for l in lemmas})

    import time
    start = time.time()
    desk.cmd_progressive_worker(args)
    duration = time.time() - start

    assert duration < 2.0, f"cmd_progressive_worker took {duration}s; should be immediate and non-blocking"
    _, headers, rows = desk.load_tsv_rows(child_tsv)
    assert rows[0][1] == "car", "Child worker should have independently translated its own lemma"


def test_progressive_translation_failure_preserves_cell_state(monkeypatch, tmp_path):
    """
    Verify that when translation encounters an exception or failure,
    pending cells and existing translations are preserved and not wiped out to empty strings.
    """
    config, resolved_paths = setup_test_env(tmp_path)
    tsv_file = tmp_path / "20260828113000-failtest.de.tsv"
    headers = ["WordSource", "WordDestination", "TokenOrder"]
    rows = [["Testwort", "", "0"], ["ZweitesWort", "preserved_translation", "1"]]
    desk.save_tsv_rows_safely(tsv_file, [], headers, rows)

    args = types.SimpleNamespace(
        tsv=str(tsv_file),
        config=str(tmp_path / "config.ini"),
        language="de",
        target_lang="ru",
        prompt="",
        provider="google",
        text_mode="single",
        skip_intellifiller=True,
        zid="20260828113000",
        trace_id="test:trace",
    )

    written_events = []
    def mock_safe_write_update_js(path, r_rows, h_headers, r_fields, stage=None, status=None, error=None, **kw):
        written_events.append({"stage": stage, "status": status, "error": error, "rows": r_rows})

    def mock_failing_fast_path(*a, **kw):
        raise RuntimeError("External translation service timeout")

    monkeypatch.setattr(desk, 'load_config', lambda *a, **kw: (config, resolved_paths, {}, {}))
    monkeypatch.setattr(desk, 'translate_lemmas_fast_path', mock_failing_fast_path)
    monkeypatch.setattr(desk, 'safe_write_update_js', mock_safe_write_update_js)

    desk.cmd_progressive_worker(args)

    _, updated_headers, updated_rows = desk.load_tsv_rows(tsv_file)
    # Existing cell must NOT have been wiped to empty string ""
    assert updated_rows[1][1] == "preserved_translation"
    # A failure event should be captured with failed status
    failed_event = [e for e in written_events if e.get("status") == "failed"]
    assert len(failed_event) >= 1
    assert failed_event[0]["stage"] == "translated"
    assert failed_event[0]["error"]["code"] == "ERR_TRANSLATION_FAILED"
    assert failed_event[0]["error"]["retryable"] is True
    assert "failed_lemmas" in failed_event[0]["error"]["details"]


def test_progressive_worker_sqlite_mode_exception_non_destructive(monkeypatch, tmp_path):
    """
    Verify that in SQLite mode, when an exception occurs in progressive worker translation,
    existing word records are preserved and not wiped to empty strings.
    """
    config, resolved_paths = setup_test_env(tmp_path)
    tsv_file = tmp_path / "20260828114000-failtest.de.tsv"
    headers = ["WordSource", "WordDestination", "TokenOrder"]
    rows = [["Apfel", "", "0"], ["Birne", "груша", "1"]]
    desk.save_tsv_rows_safely(tsv_file, [], headers, rows)

    args = types.SimpleNamespace(
        tsv=str(tsv_file),
        config=str(tmp_path / "config.ini"),
        language="de",
        target_lang="ru",
        prompt="",
        provider="google",
        text_mode="single",
        skip_intellifiller=True,
        zid="20260828114000",
        trace_id="test:trace:sqlite",
    )

    written_events = []
    def mock_safe_write_update_js(path, r_rows, h_headers, r_fields, stage=None, status=None, error=None, **kw):
        written_events.append({"stage": stage, "status": status, "error": error, "rows": r_rows})

    class MockSqliteStorageAdapter:
        backend_name = "sqlite"
        def file_lock(self, path):
            import contextlib
            return contextlib.nullcontext()
        def load_tsv_rows(self, path):
            return [], headers, [list(r) for r in rows]
        def batch_update_words(self, session_zid, updates_list, zid=None):
            pass

    monkeypatch.setattr(desk, 'load_config', lambda *a, **kw: (config, resolved_paths, {}, {}))
    monkeypatch.setattr(desk, 'get_storage_adapter', lambda *a, **kw: MockSqliteStorageAdapter())
    monkeypatch.setattr(desk, 'translate_lemmas_fast_path', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("Network failure")))
    monkeypatch.setattr(desk, 'safe_write_update_js', mock_safe_write_update_js)

    desk.cmd_progressive_worker(args)

    failed_events = [e for e in written_events if e.get("status") == "failed"]
    assert len(failed_events) >= 1
    assert failed_events[0]["error"]["code"] == "ERR_TRANSLATION_FAILED"
    assert failed_events[0]["error"]["retryable"] is True


def test_ephemeral_progressive_updates_sqlite_emits_ipc_files(tmp_path):
    """Verify that write_update_js emits ephemeral .updates/*.js files when backend is sqlite for desktop IPC."""
    import configparser
    import json
    sqlite_cfg = configparser.ConfigParser()
    sqlite_cfg.read_string("[storage]\nbackend=sqlite\n")

    tsv_file = tmp_path / "20260829020000-session.tsv"
    tsv_file.write_text("WordSource\tWordDestination\nApfel\t\n", encoding="utf-8")
    headers = ["WordSource", "WordDestination"]
    data_rows = [["Apfel", ""]]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination"}

    res = desk.write_update_js(
        tsv_file,
        data_rows,
        headers,
        role_fields,
        stage="translated",
        zid="20260829020000",
        config=sqlite_cfg
    )

    assert res is not None and res.exists()
    updates_dir = tmp_path / f"{tsv_file.stem}.updates"
    assert updates_dir.exists(), ".updates directory must be created for desktop IPC"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1
    content = js_files[0].read_text(encoding="utf-8")
    assert "window.receiveUpdate" in content


def test_prune_stale_results_artifacts(tmp_path):
    """Verify safe removal of stale .updates directories, zero-byte logs, and .done markers."""
    import os
    now = time.time()
    stale_time = now - 600 # 10 minutes ago
    fresh_time = now

    # 1. Stale .updates directory
    stale_updates = tmp_path / "20260829010000-old.updates"
    stale_updates.mkdir()
    (stale_updates / "000001.js").write_text("update()", encoding="utf-8")
    os.utime(stale_updates, (stale_time, stale_time))

    # 2. Fresh .updates directory
    fresh_updates = tmp_path / "20260829020000-new.updates"
    fresh_updates.mkdir()
    (fresh_updates / "000001.js").write_text("update()", encoding="utf-8")
    os.utime(fresh_updates, (fresh_time, fresh_time))

    # 3. Stale done markers
    stale_base_done = tmp_path / "20260829010000-old.base_translation_done"
    stale_base_done.touch()
    os.utime(stale_base_done, (stale_time, stale_time))

    stale_enrich_done = tmp_path / "20260829010000-old.enrichment_done"
    stale_enrich_done.touch()
    os.utime(stale_enrich_done, (stale_time, stale_time))

    stale_cut_done = tmp_path / "20260829010000-old.the_cut_done"
    stale_cut_done.touch()
    os.utime(stale_cut_done, (stale_time, stale_time))

    # 4. Fresh done marker
    fresh_base_done = tmp_path / "20260829020000-new.base_translation_done"
    fresh_base_done.touch()
    os.utime(fresh_base_done, (fresh_time, fresh_time))

    # 5. Stale 0-byte log
    stale_zero_log = tmp_path / "20260829010000-old.log"
    stale_zero_log.touch()
    os.utime(stale_zero_log, (stale_time, stale_time))

    # 6. Stale non-empty log (should be preserved)
    stale_nonempty_log = tmp_path / "20260829010000-data.log"
    stale_nonempty_log.write_text("Important session log\n", encoding="utf-8")
    os.utime(stale_nonempty_log, (stale_time, stale_time))

    # 7. Persistent TSV / TXT data files (should never be pruned)
    data_tsv = tmp_path / "20260829010000-old.tsv"
    data_tsv.write_text("a\tb\n", encoding="utf-8")
    os.utime(data_tsv, (stale_time, stale_time))

    data_txt = tmp_path / "20260829010000-old.txt"
    data_txt.write_text("Source Text", encoding="utf-8")
    os.utime(data_txt, (stale_time, stale_time))

    # Execute pruning with 300s threshold
    pruned_count = desk.prune_stale_results_artifacts(tmp_path, max_age_seconds=300)

    # 5 items should have been pruned: stale_updates, 3 stale done markers, stale_zero_log
    assert pruned_count == 5
    assert not stale_updates.exists()
    assert not stale_base_done.exists()
    assert not stale_enrich_done.exists()
    assert not stale_cut_done.exists()
    assert not stale_zero_log.exists()

    # Preserved items
    assert fresh_updates.exists()
    assert fresh_base_done.exists()
    assert stale_nonempty_log.exists()
    assert data_tsv.exists()
    assert data_txt.exists()






