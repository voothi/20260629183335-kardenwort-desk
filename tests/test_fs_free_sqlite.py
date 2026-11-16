"""
Unit and integration tests verifying the FS-Free SQLite-First Architecture (20260822110235-fs-free-sqlite-first-architecture).
Verifies that single and multi-window lookups, progressive pipelines, and rendering flows create 0 persistent files in results/
and operate 100% through SQLite tables.
"""

import json
import subprocess
import pytest

import kardenwort_desk as desk
from kardenwort_desk import (
    get_storage_adapter,
    SqliteStorageAdapter,
    run_lookup_flow,
)
from tests.test_db_first_lookup import lookup_world
from tests.controller_test_support import controller_world
from tests.render_pipeline_support import configure_render_pipeline
from tests.test_structured_enrichment import control_writes, prohibited


@pytest.fixture
def legacy_worker_processes(monkeypatch):
    """Observe and reap actual project-owned children, including failed test bodies."""
    processes = []
    original = subprocess.Popen

    def observe(args, *positional, **kwargs):
        process = original(args, *positional, **kwargs)
        if isinstance(args, (list, tuple)) and any(command in args for command in ('batch-worker', 'retext-worker')):
            processes.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', observe)
    try:
        yield processes
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_fs_free_single_lookup_zero_disk_writes(lookup_world, monkeypatch):
    """
    Verifies that a single-window lookup flow backed by SQLite creates 0 files in results/
    and stores 100% of session, sentence, and word records in SQLite.
    """
    server = lookup_world
    config, resolved_paths, goldendict = server.config, server.resolved_paths, server.goldendict
    results_dir = resolved_paths['results_dir']
    extracted = []
    original = subprocess.run

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        if '--anki-csv-header' in args[0]:
            extracted.extend(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{'))
        return result

    monkeypatch.setattr(subprocess, 'run', observe)

    test_zid = "20260822120000"
    with control_writes() as writes:
        res = run_lookup_flow(
            text="I eat an apple.",
            language="en",
            target_lang="ru",
            fmt="html",
            config=config,
            resolved_paths=resolved_paths,
            goldendict=goldendict,
            zid=test_zid,
            text_mode="single",
        )

    assert prohibited(writes) == []
    assert server.translation_service.provider_calls
    assert [(row.get('WordSource'), row.get('SentenceSource')) for row in extracted] == [
        ('apple', 'I eat an apple.')]

    # Verify 0 TSV/TXT/marker files in results_dir
    created_files = [f for f in results_dir.iterdir() if f.is_file() and not f.name.endswith(".log")]
    assert len(created_files) == 0, f"Expected 0 persistent files in results/, found: {created_files}"

    # Verify complete SQLite persistence
    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, SqliteStorageAdapter)

    with adapter.db.get_connection(zid=test_zid) as conn:
        sess = conn.execute("SELECT * FROM sessions WHERE zid = ?", (test_zid,)).fetchone()
        assert sess is not None
        assert sess["source_language"] == "en"
        assert sess["target_language"] == "ru"

        sents = conn.execute("SELECT * FROM sentences WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(sents) == 1
        assert sents[0]["sentence_source"] == "I eat an apple."
        assert sents[0]["sentence_destination"] == "Я ем яблоко."

        words = conn.execute("SELECT * FROM words WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(words) == 1
        assert words[0]["lemma"] == "apple"
        assert words[0]["word_destination"] == "apple_ru"


@pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)
def test_fs_free_multi_window_sentences_mode_zero_disk_writes(monkeypatch, lookup_world):
    """
    Verifies that multi-window / sentences mode decomposition creates child sessions
    directly in SQLite without writing intermediate .txt, .tsv, or .cut_done marker files.
    """
    server = lookup_world
    config, resolved_paths = server.config, server.resolved_paths
    results_dir = resolved_paths['results_dir']
    configure_render_pipeline(server)
    config.read_dict({'sentences_mode': {'enabled': 'true', 'min_sentences': '2',
        'delivery_mode': 'multi_window', 'parent_mode': 'none'}})
    spawned_processes = []
    monkeypatch.setattr(desk, 'spawn_ahk', lambda args, base_dir: spawned_processes.append((list(args),)))

    test_zid = "20260822123000"
    with control_writes() as writes:
        res = desk.run_render_flow(
            text="First sentence. Second sentence.",
            language="en",
            zid=test_zid,
            text_mode="sentences",
            config=config,
            resolved_paths=resolved_paths,
        )
    assert prohibited(writes) == []

    # Verify no .tsv, .txt, or .cut_done files in results_dir
    created_files = [f for f in results_dir.iterdir() if f.is_file() and not f.name.endswith(".log")]
    assert len(created_files) == 0, f"Expected 0 persistent files in results/, found: {created_files}"

    # Verify child sessions were saved in SQLite
    adapter = get_storage_adapter(config, resolved_paths)
    with adapter.db.get_connection(zid=test_zid) as conn:
        sessions = conn.execute("SELECT * FROM sessions ORDER BY zid ASC").fetchall()
        assert len(sessions) >= 2

    # Verify AHK child process was spawned with --restore commands
    assert len(spawned_processes) >= 1
    spawned_cmds = [p[0] for p in spawned_processes if len(p) > 0 and isinstance(p[0], list)]
    has_restore = any("--restore" in cmd for cmd in spawned_cmds)
    assert has_restore, f"Expected --restore in spawned child commands: {spawned_cmds}"


def test_fs_free_progressive_worker_in_memory_enrichment(monkeypatch, lookup_world):
    """
    Verifies that the canonical HTTP worker runs translation and enrichment in-memory
    directly against SQLite without requiring TSV files or creating marker files.
    """
    from tests.test_release_boundary_contracts import post
    from tests.thread_support import executor_tasks
    server = lookup_world
    config, resolved_paths = server.config, server.resolved_paths
    results_dir = resolved_paths['results_dir']
    config.read_dict({'triggers': {'run_text_translation': 'auto', 'run_lemma_base_translation': 'auto',
                                   'run_lemma_enrichment': 'manual'}})

    adapter = get_storage_adapter(config, resolved_paths)
    test_zid = "20260822130000"

    # Pre-populate session in SQLite with missing translations
    adapter.save_session(
        session_zid=test_zid,
        slug="progressive-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="The fox jumps.",
        sentences=[{"sentence_index": 1, "sentence_source": "The fox jumps.", "sentence_destination": None}],
        words=[{"sentence_index": 1, "token_order": 0, "quotation": "fox", "lemma": "fox", "word_destination": None}],
    )

    with control_writes() as writes, executor_tasks(monkeypatch):
        status, payload = post(f'http://127.0.0.1:{server.server_port}', '/session/progressive/enqueue',
            {'session_zid': test_zid, 'language': 'en', 'target_lang': 'ru'})
        assert status == 200, payload
    assert prohibited(writes) == []
    cold = desk.SqliteStorageAdapter(config, resolved_paths)
    assert cold.db.get_words_by_session(test_zid)[0]['word_provenance'] == 'live:google'
    assert cold.db.get_sentences_by_session(test_zid)[0]['text_provenance'] == 'live:google'

    # Verify no marker files on disk
    marker_files = list(results_dir.glob("*.done")) + list(results_dir.glob("*.txt"))
    assert len(marker_files) == 0

    # Verify SQLite database contains updated translation
    with adapter.db.get_connection(zid=test_zid) as conn:
        sents = conn.execute("SELECT * FROM sentences WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(sents) == 1
        assert sents[0]["sentence_destination"] == "Лиса прыгает."

        words = conn.execute("SELECT * FROM words WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(words) == 1
        assert words[0]["word_destination"] == "fox_ru"


def test_fs_free_repeat_lookup_fast_cache_hit_zero_disk_writes(monkeypatch, lookup_world):
    """
    Verifies that launching the same text a second time hits the SQLite cache,
    bypasses spaCy tokenization subprocess, and produces 0 persistent TSV files in results/.
    """
    server = lookup_world
    config, resolved_paths = server.config, server.resolved_paths
    results_dir = resolved_paths['results_dir']
    configure_render_pipeline(server)
    config.set('storage', 'cache_ttl_seconds', '86400')
    config.set('wordfill', 'enabled', 'true')
    wordfill_cfg = desk.resolve_wordfill_config(config, resolved_paths)
    core_subprocesses = []
    original = subprocess.run

    def observe(*args, **kwargs):
        if '--anki-csv-header' in args[0]:
            core_subprocesses.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', observe)

    with control_writes() as writes:
        # 1. First launch
        test_zid_1 = "20260822140001"
        html_1 = desk.run_render_flow(
            text="The quick fox.",
            language="en",
            zid=test_zid_1,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            wordfill_cfg=wordfill_cfg,
        )
        assert len(html_1) > 0
        assert len(core_subprocesses) == 1

        # 2. Second launch of the exact same text
        test_zid_2 = "20260822140002"
        html_2 = desk.run_render_flow(
            text="The quick fox.",
            language="en",
            zid=test_zid_2,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            wordfill_cfg=wordfill_cfg,
        )
        assert len(html_2) > 0
        # Core spaCy subprocess must NOT have been called again (cache hit!)
        assert len(core_subprocesses) == 1, "Expected second launch to hit SQLite cache without running spaCy subprocess"

        # Verify zero TSV, TXT, or lock files on disk in results/
        created_files = [f for f in results_dir.iterdir() if f.is_file() and not f.name.endswith(".log")]
        assert len(created_files) == 0, f"Expected 0 persistent files in results/, found: {created_files}"
    assert prohibited(writes) == []
    cold = desk.SqliteStorageAdapter(config, resolved_paths)
    assert cold.db.get_session(test_zid_1)['source_raw_text'] == 'The quick fox.'
    assert [row['lemma'] for row in cold.db.get_words_by_session(test_zid_1)] == ['fox']
    assert len(core_subprocesses) == 1


def test_fs_free_reprocess_and_retext_sqlite_mode(tmp_path, lookup_world, legacy_worker_processes):
    """
    Verifies that cmd_reprocess, cmd_reprocess_worker, cmd_retext, cmd_retext_worker,
    and cmd_export function cleanly in SQLite mode without existing TSV files in results/.
    """
    server = lookup_world
    config, resolved_paths = server.config, server.resolved_paths
    results_dir = resolved_paths['results_dir']
    fav_dir = tmp_path/'favorites'
    fav_dir.mkdir()
    resolved_paths['favorites_output_dir'] = fav_dir
    config.read_dict({'settings': {'default_language': 'en', 'favorites_output_dir': str(fav_dir),
        'anki_mapping_file': str(resolved_paths['anki_mapping_file']),
        'send_to_anki_after_export': 'false', 'save_to_favorites_on_export': 'true'},
        'pipeline': {'lemma_reprocess_provider': 'google', 'run_lemmatizer': 'false'},
        'triggers': {'run_lemma_enrichment': 'manual'},
        'languages': {'en_lemma_index': ''}})
    cfg_file = tmp_path/'reprocess_config.ini'
    with cfg_file.open('w', encoding='utf-8') as stream:
        config.write(stream)

    test_zid = "20260822150000"
    adapter = get_storage_adapter(config, resolved_paths)

    # 1. Populate initial SQLite session
    adapter.save_session(
        session_zid=test_zid,
        slug="reprocess-test",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="The quick fox.",
        sentences=[{"sentence_index": 1, "sentence_source": "The quick fox.", "sentence_destination": "Быстрая лиса."}],
        words=[
            {"sentence_index": 1, "token_order": 0, "quotation": "quick", "lemma": "quick", "word_destination": "быстрый", "selected": 0},
            {"sentence_index": 1, "token_order": 1, "quotation": "fox", "lemma": "fox", "word_destination": "лиса", "selected": 1},
        ],
    )

    # 2. Test cmd_reprocess with selection manifest
    manifest_file = tmp_path / "manifest_reproc.json"
    manifest_data = {
        "selected_row_ids": [0],
        "zid": test_zid,
        "tsv_path": str(results_dir / f"{test_zid}-reprocess-test.en.tsv"),
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    class ReprocessArgs:
        selection_manifest = str(manifest_file)
        language = "en"
        config = str(cfg_file)
        trace_id = f"{test_zid}:reprocess:test"

    desk.cmd_reprocess(ReprocessArgs())
    assert len(legacy_worker_processes) == 1
    assert legacy_worker_processes[0].wait(timeout=10) == 0
    cold = SqliteStorageAdapter(config, resolved_paths)
    child_words = cold.db.get_words_by_session(test_zid)
    assert child_words[0]['word_destination'] == 'quick_reprocessed'
    assert child_words[1]['word_destination'] == 'лиса'

    # 3. Test the same actual worker entry point directly.
    class BatchWorkerArgs:
        tsv = str(results_dir / f"{test_zid}-reprocess-test.en.tsv")
        prompt = "en_prompt"
        rows = "0"
        zid = test_zid
        trace_id = f"{test_zid}:reprocess:worker"
        config = str(cfg_file)

    desk.cmd_reprocess_worker(BatchWorkerArgs())

    # Check updated word in SQLite
    with adapter.db.get_connection(zid=test_zid) as conn:
        words = conn.execute("SELECT * FROM words WHERE session_zid = ? ORDER BY token_order", (test_zid,)).fetchall()
        assert words[0]["word_destination"] == "quick_reprocessed"

    # Exercise actual Re-text CLI dispatch before its direct worker alias.
    class RetextArgs(ReprocessArgs):
        text_mode = 'single'

    desk.cmd_retext(RetextArgs())
    assert len(legacy_worker_processes) == 2
    assert legacy_worker_processes[1].wait(timeout=10) == 0
    cold = SqliteStorageAdapter(config, resolved_paths)
    child_sentence = cold.db.get_sentences_by_session(test_zid)[0]
    assert child_sentence['sentence_destination'] == 'Очень быстрая лиса.'
    assert child_sentence['sentence_destination2'] == 'Очень быстрая лиса.'
    assert child_sentence['text_provenance'] == 'live:google'

    # 4. Re-text executes the owned HTTP provider.
    class RetextWorkerArgs:
        tsv = str(results_dir / f"{test_zid}-reprocess-test.en.tsv")
        language = "en"
        text_mode = "single"
        zid = test_zid
        trace_id = f"{test_zid}:retext:worker"
        config = str(cfg_file)

    desk.cmd_retext_worker(RetextWorkerArgs())
    assert ('The quick fox.', 'en', 'ru') in server.translation_service.provider_calls

    # Check updated sentence in SQLite
    with adapter.db.get_connection(zid=test_zid) as conn:
        sents = conn.execute("SELECT * FROM sentences WHERE session_zid = ?", (test_zid,)).fetchall()
        assert sents[0]["sentence_destination"] == "Очень быстрая лиса."
        assert sents[0]['sentence_destination2'] == 'Очень быстрая лиса.'
        assert sents[0]['text_provenance'] == 'live:google'

    # 5. Test cmd_export via selection manifest
    export_manifest_file = tmp_path / "manifest_export.json"
    export_manifest_data = {
        "selected_row_ids": [1],
        "zid": test_zid,
        "tsv_path": str(results_dir / f"{test_zid}-reprocess-test.en.tsv"),
    }
    export_manifest_file.write_text(json.dumps(export_manifest_data), encoding="utf-8")

    class ExportArgs:
        selection_manifest = str(export_manifest_file)
        language = "en"
        config = str(cfg_file)
        zid = test_zid
        trace_id = f"{test_zid}:export:test"

    desk.cmd_export(ExportArgs())

    # Verify favorites file was exported to favorites/
    fav_files = list(fav_dir.glob("*.tsv"))
    assert len(fav_files) == 1

    # Verify zero TSV files were left in results/
    results_tsvs = list(results_dir.glob("*.tsv"))
    assert len(results_tsvs) == 0


@pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)
def test_clean_sentence_translation_view_in_sentences_mode(monkeypatch, lookup_world):
    """
    Verifies that sentences_mode stores clean unpadded sentence translation in sentences.sentence_destination
    for both master and child sessions, while keeping padded sentences in sentence_destination2 / card fields.
    """
    server = lookup_world
    config, resolved_paths = server.config, server.resolved_paths
    results_dir = resolved_paths['results_dir']
    configure_render_pipeline(server)
    config.read_dict({'sentences_mode': {'enabled': 'true', 'min_sentences': '2',
        'delivery_mode': 'multi_window', 'parent_mode': 'none'}})
    config.read_dict({'settings': {'anki_context_mode': 'single', 'anki_context_words_after': '5',
                                      'anki_translated_context_words_after': '5'}})

    test_zid = "20260822143000"
    with control_writes() as writes:
        desk.run_render_flow(
            text="First sentence. Second sentence.",
            language="en",
            zid=test_zid,
            text_mode="sentences",
            config=config,
            resolved_paths=resolved_paths,
            spawn_children=False,
        )
    assert prohibited(writes) == []

    adapter = get_storage_adapter(config, resolved_paths)
    with adapter.db.get_connection(zid=test_zid) as conn:
        child_sents = conn.execute("SELECT session_zid, sentence_index, sentence_source, sentence_destination, sentence_destination2 FROM sentences ORDER BY session_zid ASC, sentence_index ASC").fetchall()
        assert len(child_sents) >= 2
        for s in child_sents:
            clean_dst = s["sentence_destination"]
            assert clean_dst in ("Первое предложение.", "Второе предложение."), f"Expected clean sentence_destination, got: {clean_dst}"

        # Master session (multi) has joined sentences
        restored_master = adapter.restore_session(test_zid)
        assert restored_master["sentence_translation"] == "Первое предложение.\nВторое предложение."

        # Child session 1 (single) has single clean sentence
        restored_child1 = adapter.restore_session("20260822143001")
        assert restored_child1["sentence_translation"] == "Первое предложение."

        # Child session 2 (single) has single clean sentence
        restored_child2 = adapter.restore_session("20260822143002")
        assert restored_child2["sentence_translation"] == "Второе предложение."


@pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)
def test_controller_get_session_passes_relational_zid_not_synthetic_tsv(controller_world, monkeypatch):
    from urllib.request import Request, urlopen
    import kardenwort_controller as controller

    base_url, server = controller_world
    adapter = get_storage_adapter(server.config, server.resolved_paths)
    zid = "20261116210401"
    adapter.save_session(
        session_zid=zid,
        slug="rel-route",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="House.",
        headers=["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource"],
        data_rows=[["House", "house", "дом", "1", "House."]],
    )

    seen_tsv_args = []

    def fake_render(**kwargs):
        seen_tsv_args.append(kwargs.get("tsv_path"))
        return "<html><head></head><body>ok</body></html>"

    monkeypatch.setattr(controller, "run_render_flow", fake_render)
    url = f"{base_url}/?session_zid={zid}&seq_num=1"
    req = Request(url, headers={"X-API-Token": server.api_key})
    with urlopen(req, timeout=5) as resp:
        assert resp.status == 200
    assert seen_tsv_args == [zid]


