import time
import types
import pytest
import configparser
import json
from pathlib import Path
import kardenwort_desk as desk
from tests.controller_test_support import controller_world

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

def test_progressive_bypassed(monkeypatch, controller_world):
    """Initial real SQLite render returns while its owned provider is still pending."""
    import threading
    from tests.controller_test_support import DeterministicTranslationHandler
    from tests.render_test_support import seed_render_rows

    _, server = controller_world
    config, resolved_paths = server.config, server.resolved_paths
    config.read_dict({'rendering': {'display_mode': 'progressive'},
                      'pipeline': {'progressive_text_translation': 'true'},
                      'triggers': {'run_text_translation': 'auto', 'run_lemma_base_translation': 'manual',
                                   'run_lemma_enrichment': 'manual'}})
    zid = '20261111132200'
    seed_render_rows(config, resolved_paths, zid, 'Hello', 'en',
                     ['TokenOrder', 'WordSource', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination'],
                     [['0', 'hello', '1', 'Hello', '']])
    entered, release = threading.Event(), threading.Event()
    original = DeterministicTranslationHandler.translate

    def held(handler, provider, text, source, target):
        entered.set()
        assert release.wait(5), 'Progressive engine was not released'
        return original(handler, provider, text, source, target)

    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', held)
    future = None
    try:
        start_time = time.time()
        html_out = desk.run_render_flow('Hello', 'en', zid, 'single', config, resolved_paths,
                                        wordfill_cfg={'enabled': False}, spawn_children=False)
        end_time = time.time()
        duration = end_time - start_time
        assert duration < 1.0, f"Execution was too slow: {duration}s, progressive bypass failed"
        assert "skeleton-loader" in html_out
        assert "data-pending=\"true\"" in html_out
        assert "cleanupOrphanSkeletons" in html_out # from the JS watchdog
        assert entered.wait(5), 'Real progressive provider was not contacted'
        with server.arbiter.enrichment_queue._lock:
            future = server.arbiter.enrichment_queue._active_progressive_sessions[zid]
        assert not future.done()
        adapter = desk.get_storage_adapter(config, resolved_paths)
        assert not adapter.db.get_sentences_by_session(zid)[0]['sentence_destination']
        release.set()
        future.result(timeout=5)
        cold = desk.SqliteStorageAdapter(config, resolved_paths)
        assert cold.db.get_sentences_by_session(zid)[0]['sentence_destination'] == 'google: Hello'
    finally:
        release.set()
        if future is not None:
            future.result(timeout=5)


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


def run_tsv_cli_worker(owner, invalid_chunk=False):
    """Actual deprecated TSV CLI inputs retain marker-order and failure contracts."""
    from tests.controller_test_support import owned_configuration, translation_service, DeterministicTranslationHandler
    translation_order = []

    class Engine(DeterministicTranslationHandler):
        def translate(self, provider, text, source, target):
            translation_order.append('translation_called')
            return super().translate(provider, text, source, target)

    with translation_service(Engine) as translator:
        config, paths, _ = owned_configuration(owner, f'http://127.0.0.1:{translator.server_port}', 'tsv')
        config.read_dict({'settings': {'default_language': 'en', 'default_target_language': 'ru',
                                      'anki_mapping_file': str(paths['anki_mapping_file'])},
                         'server': {'update_delivery': 'desktop_file'},
                         'wordfill': {'enabled': 'false'},
                         'translation': {'translation_chunk_size': 'invalid' if invalid_chunk else '15'},
                         'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'auto',
                                      'run_lemma_enrichment': 'manual'}})
        configuration = owner/'config.ini'
        with configuration.open('w', encoding='utf-8') as stream:
            config.write(stream)
        args = _make_worker_args(owner, configuration)
        args.language, args.target_lang = 'en', 'ru'
        tsv_path = Path(args.tsv)
        _write_worker_tsv(tsv_path)
        desk.cmd_progressive_worker(args)
        updates = sorted(tsv_path.with_suffix('.updates').glob('*.js'))
        payloads = [json.loads(path.read_text(encoding='utf-8').split('window.receiveUpdate(', 1)[1][:-4])
                    for path in updates]
        finished = next(payload for payload in reversed(payloads) if payload['stage'] == 'finished')
        assert finished['status'] == ('failed' if invalid_chunk else 'success')
        _, headers, rows = desk.load_tsv_rows(tsv_path)
        assert rows[0][headers.index('WordSource')] == 'Haus'
        assert rows[0][headers.index('WordDestination')] == ('' if invalid_chunk else 'google: Haus')
        if invalid_chunk:
            assert finished['error'] and translation_order == []
        return tsv_path, translation_order


def test_progressive_worker_lifecycle(tmp_path):
    """
    Verifies that cmd_progressive_worker:
    1. Calls the translation stage.
    2. Touches .base_translation_done BEFORE attempting enrichment.
    3. Touches .enrichment_done at the very end (in finally).
    """
    tsv_path, translation_order = run_tsv_cli_worker(tmp_path)

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

def test_progressive_worker_exception_fallback(tmp_path):
    """
    Verifies that when cmd_progressive_worker crashes during translation,
    the finally block still creates both .base_translation_done and .enrichment_done
    so that waiting child windows are never left deadlocked.
    """
    tsv_path, _ = run_tsv_cli_worker(tmp_path, invalid_chunk=True)

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

def render_legacy_master(server, monkeypatch, zid, text_mode):
    """Retained TSV hierarchy with real controller enqueue, HTTP and worker effects."""
    import threading
    from tests.controller_test_support import DeterministicTranslationHandler
    from tests.thread_support import executor_tasks
    config, paths = server.config, server.resolved_paths
    config.read_dict({'rendering': {'display_mode': 'progressive'},
        'sentences_mode': {'enabled': 'true', 'parent_mode': 'table', 'delivery_mode': 'multi_window'},
        'pipeline': {'progressive_text_translation': 'true'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'auto',
                     'run_lemma_enrichment': 'manual'},
        'wordfill': {'enabled': 'false'}})
    headers = ['TokenOrder', 'WordSource', 'WordDestination', 'WordSourceIPA',
        'WordSourceMorphology', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination', 'DeskSelected']
    text = 'Haus ist gut.\nDas Buch ist rot.' if text_mode == 'multi' else 'Haus ist gut. Das Buch ist rot.'
    master_tsv = paths['results_dir']/f'{zid}-master.de.tsv'
    child_tsv = paths['results_dir']/f'{int(zid)+5}-child.de.tsv'
    desk.save_tsv_rows_safely(master_tsv, [], headers, [['0', 'Haus', '', '', '', '1', text, '', '0']])
    desk.save_tsv_rows_safely(child_tsv, [], headers, [['0', 'Buch', 'book', '', '', '1', 'Das Buch ist rot.', '', '0']])
    master_tsv.with_suffix('.txt').write_text(text, encoding='utf-8')
    entered, release = threading.Event(), threading.Event()
    original_engine = DeterministicTranslationHandler.translate
    original_dispatch = desk.run_progressive_worker_async
    worker_calls = []

    def held_engine(handler, *args, **kwargs):
        entered.set()
        assert release.wait(5), 'Master translation engine was not released'
        return original_engine(handler, *args, **kwargs)

    def observe_dispatch(*args, **kwargs):
        worker_calls.append((args, kwargs))
        return original_dispatch(*args, **kwargs)

    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', held_engine)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', observe_dispatch)
    with executor_tasks(monkeypatch):
        try:
            html_out = desk.run_render_flow(text, 'de', zid, text_mode, config, paths,
                tsv_path=str(master_tsv), spawn_children=False, wordfill_cfg={'enabled': False})
            assert entered.wait(5), 'Actual queued master worker did not contact the engine'
            _, stored_headers, pending_rows = desk.load_tsv_rows(master_tsv)
            assert pending_rows[0][stored_headers.index('WordDestination')] == ''
        finally:
            release.set()
    _, stored_headers, rows = desk.load_tsv_rows(master_tsv)
    assert rows[0][stored_headers.index('WordDestination')] == 'google: Haus'
    _, child_headers, child_rows = desk.load_tsv_rows(child_tsv)
    assert child_rows[0][child_headers.index('WordDestination')] == 'book'
    assert server.translation_service.provider_calls == [('Haus', 'de', 'ru')]
    return html_out, worker_calls


@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_master_window_sentences_mode_translation_skeleton(monkeypatch, controller_world):
    """Pending master lemma shimmers; manual IPA/morphology remain plain."""
    _, server = controller_world
    html_out, _ = render_legacy_master(server, monkeypatch, '20260810153000', 'multi')

    # Translation cell is pending and a provider is expected to fill it -> skeleton.
    assert "<tbody>" in html_out and "</tbody>" in html_out
    table_body = html_out[html_out.index("<tbody"):html_out.index("</tbody>")]
    assert "skeleton-loader" in table_body, "Master window translation cell must emit a skeleton loader"
    assert "kw-skeleton-cell" in table_body, "Translation skeleton (kw-skeleton-cell) must be present"
    assert table_body.count("skeleton-loader") == 1, "Only translation cell must shimmer; IPA/Morphology must not shimmer when manual"


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


@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_single_mode_master_window_launches_progressive_worker(monkeypatch, controller_world):
    """Retained single-mode TSV master dispatch executes and persists real work."""
    _, server = controller_world
    html_out, worker_calls = render_legacy_master(server, monkeypatch, '20260814120000', 'single')

    assert len(worker_calls) == 1, "run_progressive_worker_async must be called for master window in single mode"
    assert "skeleton-loader" in html_out, "Master window in single mode must render skeleton loader for pending lemmas"
    assert 'id="kardenwort-children"' in html_out, "Master window must contain children div for AHK hierarchy tracking"


def test_child_window_does_not_claim_younger_siblings_as_children(controller_world):
    """Stored child restoration must not infer ownership from temporal neighbors."""
    from tests.render_test_support import seed_render_rows
    from tests.test_structured_enrichment import control_writes, prohibited
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    config.read_dict({'rendering': {'display_mode': 'progressive'},
        'sentences_mode': {'enabled': 'true', 'parent_mode': 'table'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'}})
    headers = ['WordSource', 'WordDestination', 'SentenceSourceIndex',
               'SentenceSource', 'SentenceDestination', 'DeskSelected']
    identities = ['20260814120000', '20260814120001', '20260814120002']
    for identity, text in zip(identities, ['Older master.', 'Haus ist gut.', 'Younger sibling.']):
        seed_render_rows(config, paths, identity, text, 'de', headers,
            [['Haus', 'house', '1', text, 'Stored translation.', '0']])
        (paths['results_dir']/f'{identity}-neighbor.de.tsv').write_text(
            '\t'.join(headers)+'\n', encoding='utf-8')
    cold = desk.SqliteStorageAdapter(config, paths)
    before = {identity: cold.db.get_session_bundle(identity) for identity in identities}
    with control_writes() as writes:
        html_out = desk.run_render_flow('Haus ist gut.', 'de', identities[1], 'single',
            config, paths, spawn_children=False, wordfill_cfg={'enabled': False})

    assert 'id="kardenwort-children"' not in html_out, "Child window must NOT contain kardenwort-children div"
    assert 'Stored translation.' in html_out
    assert server.translation_service.provider_calls == []
    cold = desk.SqliteStorageAdapter(config, paths)
    assert {identity: cold.db.get_session_bundle(identity) for identity in identities} == before
    assert prohibited(writes) == []


@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_the_cut_respects_sentence_source_index_no_word_bleeding(monkeypatch, controller_world):
    """Explicit stored sentence indices outrank misleading inflected substrings."""
    from tests.thread_support import executor_tasks
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    config.read_dict({'rendering': {'display_mode': 'progressive'},
        'sentences_mode': {'enabled': 'true', 'parent_mode': 'table', 'delivery_mode': 'multi_window',
                           'multi_mode_sentence_decomposition': 'false'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'}})
    text = 'E. Scalability & Edge Cases.\nSecond sentence e.g. for something.'
    zid = '20260814120000'
    tmp_path = paths['results_dir']
    headers = ['WordSource', 'WordDestination', 'WordSourceInflectedForm',
               'SentenceSourceIndex', 'SentenceDestination', 'DeskSelected']
    data_rows = [['scalability', '', 'Scalability', '1', '', '0'],
                 ['for', '', 'e.g.', '2', '', '0']]
    # A real retained cache input lets the cut read declared rows without replacing extraction.
    master_tsv = tmp_path/f'{zid}-{desk.generate_slug(text)}.en.tsv'
    desk.save_tsv_rows_safely(master_tsv, [], headers, data_rows)
    with executor_tasks(monkeypatch):
        desk.run_render_flow(text, 'en', zid, 'multi', config, paths,
                             spawn_children=False, wordfill_cfg={'enabled': False})
    assert server.translation_service.provider_calls == []

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


def test_progressive_worker_sqlite_mode_exception_non_destructive(monkeypatch, controller_world):
    """Real failed provider work preserves both pending and translated SQL records."""
    from tests.controller_test_support import DeterministicTranslationHandler
    from tests.render_test_support import seed_render_rows
    from translate_server import TranslationServerException
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    config.read_dict({'settings': {'default_language': 'de',
        'anki_mapping_file': str(paths['anki_mapping_file'])},
        'server': {'update_delivery': 'desktop_file'},
        'wordfill': {'enabled': 'false'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'auto',
                     'run_lemma_enrichment': 'manual'}})
    zid = '20260828114000'
    seed_render_rows(config, paths, zid, 'Apfel Birne.', 'de',
        ['WordSource', 'WordDestination', 'TokenOrder', 'SentenceSourceIndex', 'SentenceSource'],
        [['Apfel', '', '0', '1', 'Apfel Birne.'], ['Birne', 'pear', '1', '1', 'Apfel Birne.']])
    cold = desk.SqliteStorageAdapter(config, paths)
    before = cold.db.get_words_by_session(zid)
    unrelated = cold.db.get_session_bundle('20261108113001')
    configuration = paths['base_dir']/'failure-config.ini'
    with configuration.open('w', encoding='utf-8') as stream:
        config.write(stream)

    def unavailable(handler, provider, text, source, target):
        handler.server.provider_calls.append((text, source, target))
        raise TranslationServerException(503, 'ERR_NETWORK_UNREACHABLE', 'Owned provider unavailable')

    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', unavailable)
    tsv_file = paths['results_dir']/f'{zid}-failtest.de.tsv'
    args = types.SimpleNamespace(tsv=str(tsv_file), config=str(configuration),
        language='de', target_lang='ru', prompt='', provider='google', text_mode='single',
        skip_intellifiller=True, zid=zid, trace_id=f'{zid}:test')
    desk.cmd_progressive_worker(args)
    written_events = [json.loads(path.read_text(encoding='utf-8').split('window.receiveUpdate(', 1)[1][:-4])
                      for path in sorted(tsv_file.with_suffix('.updates').glob('*.js'))]
    cold = desk.SqliteStorageAdapter(config, paths)
    assert cold.db.get_words_by_session(zid) == before
    assert cold.db.get_session_bundle('20261108113001') == unrelated
    assert cold.db.get_worker_status(zid)['worker_status'] == 'failed'
    assert server.translation_service.provider_calls == [('Apfel', 'de', 'ru')]

    failed_events = [e for e in written_events if e.get("status") == "failed"]
    assert len(failed_events) >= 1
    assert failed_events[0]["error"]["code"] == "ERR_TRANSLATION_FAILED"
    assert failed_events[0]["error"]["retryable"] is True


def test_ephemeral_progressive_updates_sqlite_emits_ipc_files(tmp_path):
    """Verify that write_update_js emits ephemeral .updates/*.js files when backend is sqlite for desktop IPC."""
    import configparser
    import json
    sqlite_cfg = configparser.ConfigParser()
    sqlite_cfg.read_string("[storage]\nbackend=sqlite\n[server]\nupdate_delivery=desktop_file\n")

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


def test_progressive_worker_text_translation_failure_continues_to_lemmas(monkeypatch, tmp_path):
    """
    Verifies that when sentence text translation fails with an exception,
    the progressive worker catches the error, emits empty translated_text stage,
    and successfully continues with lemma translation.
    """
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[settings]\ndefault_language=en\ndefault_target_language=ru\nanki_mapping_file=./mapping.ini\n"
        "[pipeline]\nlemma_base_provider=google\ntext_base_provider=deepl\n"
        "[triggers]\nrun_lemma_base_translation=auto\nrun_text_translation=auto\nrun_lemma_enrichment=manual\n"
        "[fields]\n",
        encoding="utf-8",
    )
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[fields_mapping.word]\nWordSource=lemma\nWordDestination=word_translation\n"
        "[fields_mapping.sentence]\nSentenceDestination=sentence_destination\nSentenceSourceIndex=sentence_index\n",
        encoding="utf-8",
    )

    tsv_path = tmp_path / "20260829093000-resilience.en.tsv"
    tsv_path.write_text(
        "WordSource\tWordDestination\tSentenceDestination\tSentenceSourceIndex\n"
        "Hund\t\t\t1\n",
        encoding="utf-8",
    )
    txt_path = tmp_path / "20260829093000-resilience.en.txt"
    txt_path.write_text("Der Hund bellt.", encoding="utf-8")

    args = types.SimpleNamespace(
        tsv=str(tsv_path),
        config=str(config_path),
        language="de",
        target_lang="ru",
        prompt_name="",
        lemmas_provider="google",
        word_translations_empty=True,
        skip_intellifiller=True,
        text_mode="single",
        zid="20260829093000",
        trace_id="test:trace:resilience",
    )

    def mock_failing_text_translate(*args, **kwargs):
        raise RuntimeError("DeepL 429 Too Many Requests: Rate limit reached")

    lemma_translated = []
    def mock_translate_lemmas(lemmas, *args, **kwargs):
        lemma_translated.extend(lemmas)
        return {l: f"{l}_RU" for l in lemmas}

    written_stages = []
    def mock_safe_write_update(path, r_rows, h_headers, r_fields, stage=None, status=None, error=None, translated_text=None, **kw):
        written_stages.append((stage, status, translated_text))

    monkeypatch.setattr(desk, "translate_source_text", mock_failing_text_translate)
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", mock_translate_lemmas)
    monkeypatch.setattr(desk, "safe_write_update_js", mock_safe_write_update)

    desk.cmd_progressive_worker(args)

    assert "Hund" in lemma_translated, "Lemma translation must execute even if text translation failed"
    stages = [s[0] for s in written_stages]
    assert "translated_text" in stages, "translated_text stage must be emitted to clear loader"
    assert "translated" in stages, "translated lemma stage must be emitted"
    assert (tmp_path / "20260829093000-resilience.en.base_translation_done").exists()


def test_translate_text_deepl_failover_to_google(monkeypatch, tmp_path):
    """
    Verifies that translate_text fails over to Google if DeepL raises an error.
    """
    config = configparser.ConfigParser()
    config.read_string("[settings]\n[pipeline]\nauto_offline_fallback=false\n")
    resolved_paths = {}

    def mock_failing_deepl(*args, **kwargs):
        raise ConnectionError("DeepL API timeout")

    def mock_google(text, *args, **kwargs):
        return f"Google({text})"

    monkeypatch.setattr(desk, "run_deepl_translation", mock_failing_deepl)
    monkeypatch.setattr(desk, "run_google_translation", mock_google)

    result = desk.translate_text("Hello world", "en", "ru", config, resolved_paths, "deepl")
    assert result == "Google(Hello world)"


def run_sqlite_cli_worker(owner, sess_zid, fail_provider=False, task='lemma'):
    """Retained CLI edge; provider HTTP, configuration reload and SQLite are real."""
    from tests.controller_test_support import owned_configuration, translation_service, DeterministicTranslationHandler
    from tests.render_test_support import seed_render_rows
    from translate_server import TranslationServerException

    class Engine(DeterministicTranslationHandler):
        def translate(self, provider, text, source, target):
            if fail_provider and (fail_provider != 'text' or text != 'sample'):
                self.server.provider_calls.append((text, source, target))
                raise TranslationServerException(503, 'ERR_NETWORK_UNREACHABLE', 'Owned provider unavailable')
            return super().translate(provider, text, source, target)

    with translation_service(Engine) as translator:
        config, paths, _ = owned_configuration(owner, f'http://127.0.0.1:{translator.server_port}', 'sqlite')
        config.read_dict({'settings': {'default_language': 'en', 'default_target_language': 'ru',
                                      'anki_mapping_file': str(paths['anki_mapping_file'])},
                         'server': {'update_delivery': 'desktop_file'},
                         'wordfill': {'enabled': 'false'},
                         'triggers': {'run_text_translation': 'auto' if task in ('text', 'both') else 'manual',
                                      'run_lemma_base_translation': 'auto' if task in ('lemma', 'both') else 'manual',
                                      'run_lemma_enrichment': 'manual'}})
        configuration = owner/'config.ini'
        with configuration.open('w', encoding='utf-8') as stream:
            config.write(stream)
        seed_render_rows(config, paths, sess_zid, 'Sample text for worker test.', 'en',
            ['TokenOrder', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination'],
            [['0', 'sample', '', '1', 'Sample text for worker test.', '']])
        tsv_path = paths['results_dir']/f'{sess_zid}-worker-test.en.tsv'
        args = types.SimpleNamespace(tsv=str(tsv_path), config=str(configuration), text_mode='single',
            skip_intellifiller=True, zid=sess_zid, trace_id=f'{sess_zid}:test', language='en', target_lang='ru')
        desk.cmd_progressive_worker(args)
        requested = ['Sample text for worker test.', 'sample'] if task == 'both' else [
            'Sample text for worker test.' if task == 'text' else 'sample']
        assert translator.provider_calls == [(value, 'en', 'ru') for value in requested]
        cold = desk.SqliteStorageAdapter(config, paths)
        words = cold.db.get_words_by_session(sess_zid)
        assert len(words) == 1 and words[0]['lemma'] == 'sample'
        expected_word = 'google: sample' if not fail_provider or (fail_provider == 'text' and task == 'both') else ''
        assert (words[0]['word_destination'] or '') == expected_word
        if expected_word:
            assert words[0]['word_provenance'] == 'live:google'
        assert cold.db.get_sentences_by_session(sess_zid)[0]['sentence_source'] == 'Sample text for worker test.'
        updates = sorted(tsv_path.with_suffix('.updates').glob('*.js'))
        payloads = [json.loads(path.read_text(encoding='utf-8').split('window.receiveUpdate(', 1)[1][:-4])
                    for path in updates]
        finished = next(payload for payload in reversed(payloads) if payload['stage'] == 'finished')
        assert finished['status'] == ('failed' if fail_provider else 'success')
        if fail_provider:
            assert finished['error']
        return cold.db


def test_progressive_worker_lifecycle_db_status_finished(tmp_path):
    """
    Verifies that cmd_progressive_worker records worker_status = 'finished' in SQLite upon successful completion.
    """
    sess_zid = '20260830234001'
    db = run_sqlite_cli_worker(tmp_path, sess_zid, fail_provider=False)

    st = db.get_worker_status(sess_zid)
    assert st["worker_status"] == "finished"
    assert st["worker_started_at"] is not None
    assert st["worker_finished_at"] is not None


@pytest.mark.parametrize('task,fail_provider', [('lemma', True), ('text', True), ('both', True), ('both', 'text')])
def test_progressive_worker_lifecycle_db_status_failed(tmp_path, task, fail_provider):
    """
    The retained worker records a real failed provider stage in SQLite and its final event.
    """
    sess_zid = '20260830234002'
    db = run_sqlite_cli_worker(tmp_path, sess_zid, fail_provider=fail_provider, task=task)

    st = db.get_worker_status(sess_zid)
    assert st["worker_status"] == "failed"
    assert st["worker_started_at"] is not None
    assert st["worker_finished_at"] is not None


def test_record_worker_heartbeat_throttle_verification(tmp_path):
    """
    Verifies that record_worker_heartbeat does not write to DB more than once per interval in a fast loop.
    """
    from kardenwort_db import KardenwortDB
    db_file = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_file)
    db.run_migrations()

    sess_zid = "20260830234003"
    db.insert_session({
        "zid": sess_zid,
        "slug": "throttle-test",
        "source_language": "en",
        "source_raw_text": "Throttle test.",
    })
    db.set_worker_status(sess_zid, "running")

    # Clear memory cache for this ZID
    with desk._WORKER_HB_LOCK:
        desk._LAST_WORKER_HEARTBEATS.pop(sess_zid, None)

    # First call must succeed (write to DB)
    res1 = desk.record_worker_heartbeat(sess_zid, db, min_interval_seconds=3.0)
    assert res1 is True

    # Immediate subsequent calls within 3s must be throttled (return False)
    for _ in range(50):
        res_fast = desk.record_worker_heartbeat(sess_zid, db, min_interval_seconds=3.0)
        assert res_fast is False


def test_run_progressive_worker_async_configured_interpreter_and_credentials(monkeypatch, tmp_path):
    """
    Verifies that run_progressive_worker_async passes configured controller port & token,
    and uses resolved_paths['kardenwort_python'] when falling back to detached subprocess.
    """
    import json
    from unittest.mock import MagicMock

    cfg = configparser.ConfigParser()
    cfg.read_string("""
[server]
controller_port = 19999
api_key = test-secret-token-123
""")
    custom_py = tmp_path / "venv" / "Scripts" / "python.exe"
    resolved_paths = {
        'kardenwort_python': custom_py,
        'config': cfg
    }
    tsv_path = tmp_path / "20260908220000-test.en.tsv"
    tsv_path.write_text("Quotation\tWordSource\tWordDestination\n", encoding="utf-8")

    sent_requests = []

    class MockHTTPResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def mock_urlopen(req, timeout=0.5):
        sent_requests.append(req)
        return MockHTTPResponse()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # 1. Enqueue path with controller daemon
    desk.run_progressive_worker_async(
        tsv_path=str(tsv_path),
        language="en",
        target_lang="ru",
        prompt_name="default",
        lemmas_provider="google",
        word_translations_empty="True",
        resolved_paths=resolved_paths,
        config=cfg
    )

    assert len(sent_requests) == 1
    req = sent_requests[0]
    assert req.full_url == "http://127.0.0.1:19999/session/progressive/enqueue"
    assert req.headers.get("X-api-token") == "test-secret-token-123"
    body_data = json.loads(req.data.decode("utf-8"))
    assert body_data["token"] == "test-secret-token-123"
    assert body_data["session_zid"] == "20260908220000"

    # 2. Subprocess fallback path (controller daemon offline)
    def mock_urlopen_fail(req, timeout=0.5):
        raise ConnectionRefusedError("Offline")

    popen_cmds = []
    def mock_popen(cmd, *a, **kw):
        popen_cmds.append(cmd)
        mock_proc = MagicMock()
        return mock_proc

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    desk.run_progressive_worker_async(
        tsv_path=str(tsv_path),
        language="en",
        target_lang="ru",
        prompt_name="default",
        lemmas_provider="google",
        word_translations_empty="True",
        resolved_paths=resolved_paths,
        config=cfg
    )

    assert len(popen_cmds) == 1
    assert popen_cmds[0][0] == str(custom_py)
    assert popen_cmds[0][2] == "progressive-worker"


def _seed_stage3_session(tmp_path, name, zid="20261116212101", use_default_morph_header=True):
    from kardenwort_db import KardenwortDB
    from kardenwort_controller import SessionArbiter

    db_file = tmp_path / f"{name}.db"
    cfg = configparser.ConfigParser()
    cfg.add_section("storage")
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(db_file))
    cfg.add_section("settings")
    cfg.set("settings", "default_language", "de")
    cfg.set("settings", "default_target_language", "ru")
    cfg.add_section("triggers")
    cfg.set("triggers", "run_text_translation", "manual")
    cfg.set("triggers", "run_lemma_base_translation", "manual")
    cfg.set("triggers", "run_lemma_enrichment", "auto")
    cfg.add_section("pipeline")
    cfg.set("pipeline", "lemma_reprocess_provider", "intellifiller")
    cfg.add_section("services")
    cfg.set("services", "intellifiller_server_url", "http://127.0.0.1:18339/enrich")

    mapping_file = tmp_path / f"{name}-mapping.ini"
    mapping_file.write_text(
        "[fields]\nTokenOrder\nQuotation\nWordSource\nWordDestination\nWordSourceIPA\nWordSourceMorphologyAI\nWordSourcePOS\nWordSourceGender\nDeskSelected\nSentenceSourceIndex\nSentenceSource\nSentenceDestination\n"
        + ("" if use_default_morph_header else "[roles]\nmorphology=WordSourceMorphologyAI\n"),
        encoding="utf-8",
    )
    results_dir = tmp_path / f"{name}_results"
    results_dir.mkdir(exist_ok=True)
    resolved_paths = {
        "sqlite_db_path": str(db_file),
        "anki_mapping_file": str(mapping_file),
        "results_dir": results_dir,
        "kardenwort_workspace": str(tmp_path),
    }
    db = KardenwortDB(db_path=db_file)
    db.run_migrations()
    db.save_session_bundle(
        {"zid": zid, "slug": name, "source_language": "de", "target_language": "ru", "text_mode": "single", "source_raw_text": "Das Haus."},
        [{"session_zid": zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "Дом.", "text_provenance": "cached:sqlite"}],
        [{"session_zid": zid, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus",
          "word_destination": "дом", "ipa": None, "morphology": None, "pos": None, "gender": None, "word_provenance": "live:google", "selected": 0}],
    )
    arbiter = SessionArbiter(config=cfg, resolved_paths=resolved_paths, wordfill_cfg={"enabled": False})
    return db, cfg, resolved_paths, arbiter, zid


def test_stage3_canonical_morphology_role_and_authoritative_snapshot(tmp_path, monkeypatch):
    """Verify Stage 3 detects WordSourceMorphologyAI role and publishes authoritative post-Stage-3 finished event."""
    from kardenwort_events import EventDispatcher

    db, cfg, paths, arbiter, zid = _seed_stage3_session(tmp_path, "stage3_snap")
    intellifiller_calls = []

    def stub_intellifiller(rows=None, **kwargs):
        intellifiller_calls.append(rows)
        return {
            "status": "success",
            "enriched_rows": [{
                "row_id": rows[0]["row_id"],
                "WordSource": "Haus",
                "WordDestination": "дом",
                "WordSourceIPA": "haʊ̯s",
                "WordSourceMorphologyAI": "Subst.Neut.Nom.Sg",
                "WordSourcePOS": "NOUN",
                "WordSourceGender": "n",
            }],
        }

    monkeypatch.setattr(desk, "query_intellifiller_server", stub_intellifiller)
    events = EventDispatcher.get_instance().register(zid)
    try:
        arbiter.enrichment_queue._execute_progressive_task(zid, arbiter, "de", "ru", skip_intellifiller=False)
        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        finished = next(e for e in reversed(emitted) if e.get("stage") == "finished")
        assert intellifiller_calls, "Stage 3 must trigger when WordSourceMorphologyAI is empty"
        assert finished["status"] == "success"
        assert finished["rows"]["0"]["ipa"] == "haʊ̯s"
        assert finished["rows"]["0"]["morphology"] == "Subst.Neut.Nom.Sg"
        adapter = desk.SqliteStorageAdapter(config=cfg, resolved_paths=paths, db_path=db.db_path)
        restored = adapter.restore_session(zid)
        expected_fp = desk.compute_content_fingerprint(
            desk.sort_session_data_rows(restored["data_rows"], restored["headers"], "de", cfg, paths)
        )
        assert finished["fingerprint"] == expected_fp
    finally:
        EventDispatcher.get_instance().unregister(zid, events)
        arbiter.enrichment_queue.shutdown()
        desk.unregister_failover_callback(arbiter._on_global_failover)


def test_stage3_races_edit_supersession_deletion_and_delivery_failure(tmp_path, monkeypatch):
    """Verify Stage 3 handles concurrent edit, supersession, deletion, and delivery failures truthfully."""
    from kardenwort_events import EventDispatcher

    # Subcase A: concurrent edit commits between Stage 3 and final progressive publication
    db_a, cfg_a, paths_a, arbiter_a, zid_a = _seed_stage3_session(tmp_path, "race_edit", "20261116212102")
    orig_reword = arbiter_a.reword_session

    def reword_then_concurrent_edit(*args, **kwargs):
        db_a.batch_update_words(zid_a, [{"sentence_index": 1, "token_order": 0, "updates": {"ipa": "haʊ̯s", "morphology": "Subst"}}])
        db_a.batch_update_words(zid_a, [{"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "новейший", "word_provenance": "live:deepl"}}])
        return {}

    monkeypatch.setattr(arbiter_a, "reword_session", reword_then_concurrent_edit)
    q_a = EventDispatcher.get_instance().register(zid_a)
    try:
        arbiter_a.enrichment_queue._execute_progressive_task(zid_a, arbiter_a, "de", "ru", skip_intellifiller=False)
        ev_a = []
        while not q_a.empty():
            ev_a.append(q_a.get_nowait())
        fin_a = next(e for e in reversed(ev_a) if e.get("stage") == "finished")
        assert fin_a["status"] == "success"
        assert fin_a["rows"]["0"]["trans"] == "новейший"
        assert fin_a["rows"]["0"]["provenance"] == "live:deepl"
    finally:
        EventDispatcher.get_instance().unregister(zid_a, q_a)
        arbiter_a.enrichment_queue.shutdown()
        desk.unregister_failover_callback(arbiter_a._on_global_failover)

    # Subcase B: superseded generation does not publish stale finished event
    db_b, cfg_b, paths_b, arbiter_b, zid_b = _seed_stage3_session(tmp_path, "race_superseded", "20261116212103")
    with arbiter_b.enrichment_queue._lock:
        arbiter_b.enrichment_queue._progressive_generations[zid_b] = 99
    q_b = EventDispatcher.get_instance().register(zid_b)
    try:
        arbiter_b.enrichment_queue._execute_progressive_task(zid_b, arbiter_b, "de", "ru", skip_intellifiller=True, generation=1)
        ev_b = []
        while not q_b.empty():
            ev_b.append(q_b.get_nowait())
        assert not [e for e in ev_b if e.get("stage") == "finished"]
    finally:
        EventDispatcher.get_instance().unregister(zid_b, q_b)
        arbiter_b.enrichment_queue.shutdown()
        desk.unregister_failover_callback(arbiter_b._on_global_failover)

    # Subcase C: session deleted mid-task cannot reconstruct deleted cache or publish success
    db_c, cfg_c, paths_c, arbiter_c, zid_c = _seed_stage3_session(tmp_path, "race_deleted", "20261116212104")

    def delete_during_stage3(*args, **kwargs):
        db_c.delete_session(zid_c)
        return {}

    monkeypatch.setattr(arbiter_c, "reword_session", delete_during_stage3)
    q_c = EventDispatcher.get_instance().register(zid_c)
    try:
        arbiter_c.enrichment_queue._execute_progressive_task(zid_c, arbiter_c, "de", "ru", skip_intellifiller=False)
        ev_c = []
        while not q_c.empty():
            ev_c.append(q_c.get_nowait())
        fin_c = next(e for e in reversed(ev_c) if e.get("stage") == "finished")
        assert fin_c["status"] == "failed"
        assert zid_c not in arbiter_c.sessions
        assert db_c.get_session(zid_c) is None
    finally:
        EventDispatcher.get_instance().unregister(zid_c, q_c)
        arbiter_c.enrichment_queue.shutdown()
        desk.unregister_failover_callback(arbiter_c._on_global_failover)

    # Subcase D: committed Stage 3 followed by delivery failure reports recovery details without repeating provider
    db_d, cfg_d, paths_d, arbiter_d, zid_d = _seed_stage3_session(tmp_path, "race_delivery", "20261116212105")
    stage3_calls = []

    def commit_once(*args, **kwargs):
        stage3_calls.append(1)
        db_d.batch_update_words(zid_d, [{"sentence_index": 1, "token_order": 0, "updates": {"ipa": "haʊ̯s", "morphology": "Subst"}}])
        return {}

    def broken_emit(target_zid, payload):
        if payload.get("stage") == "finished":
            raise ConnectionError("SSE transport lost")

    monkeypatch.setattr(arbiter_d, "reword_session", commit_once)
    monkeypatch.setattr(arbiter_d, "emit_event", broken_emit)
    try:
        res = arbiter_d.enrichment_queue.enqueue_progressive_task(zid_d, arbiter_d, "de", "ru", skip_intellifiller=False)
        assert res["status"] == "queued"
        with pytest.raises(ExceptionGroup, match="Controller queue disposal failed") as caught:
            arbiter_d.enrichment_queue.shutdown(wait=True)
        err = next(e for e in caught.value.exceptions if isinstance(e, desk.StructuredError))
        assert err.details["committed"] is True
        assert err.details["recovery_url"] == f"/session/status?zid={zid_d}&committed=1"
        assert len(stage3_calls) == 1
        assert db_d.get_worker_status(zid_d)["worker_status"] == "finished"
        assert db_d.get_words_by_session(zid_d)[0]["ipa"] == "haʊ̯s"
    finally:
        desk.unregister_failover_callback(arbiter_d._on_global_failover)

