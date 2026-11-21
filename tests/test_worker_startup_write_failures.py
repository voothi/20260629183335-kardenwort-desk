"""Acceptance and control tests for worker startup persistence failures.

Exercises real owned SQLite storage, real SQLite triggers, real SessionArbiter /
EnrichmentQueue synchronous and queued execution, and real EventDispatcher SSE
delivery without database mocks or arbitrary sleeps.
"""
import configparser
import queue
import sqlite3
import threading
from pathlib import Path

import pytest

import kardenwort_desk as desk
from kardenwort_controller import SessionArbiter
from kardenwort_db import KardenwortDB
from kardenwort_events import EventDispatcher


PRIMARY_ZID = "20261119085333"
SIBLING_ZID = "20261119085334"


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for child in exc.exceptions:
            leaves.extend(_flatten_exceptions(child))
        return leaves
    return [exc]


def _drain_events(channel: queue.Queue) -> list[dict]:
    events = []
    while True:
        try:
            events.append(channel.get_nowait())
        except queue.Empty:
            return events


def _seed_worker_world(tmp_path: Path, name: str, zid: str = PRIMARY_ZID, sibling_zid: str = SIBLING_ZID):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    db_file = root / f"{name}.db"
    results_dir = root / "results"
    results_dir.mkdir(exist_ok=True)
    mapping_file = root / f"{name}-mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        "TokenOrder\n"
        "Quotation\n"
        "WordSource\n"
        "WordDestination\n"
        "WordSourceIPA\n"
        "WordSourceMorphologyAI\n"
        "WordSourcePOS\n"
        "WordSourceGender\n"
        "DeskSelected\n"
        "SentenceSourceIndex\n"
        "SentenceSource\n"
        "SentenceDestination\n",
        encoding="utf-8",
    )

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
    cfg.set("triggers", "run_lemma_enrichment", "manual")

    resolved_paths = {
        "sqlite_db_path": str(db_file),
        "anki_mapping_file": str(mapping_file),
        "results_dir": results_dir,
        "kardenwort_workspace": str(root),
    }

    db = KardenwortDB(db_path=db_file)
    db.run_migrations()
    db.save_session_bundle(
        {
            "zid": zid,
            "slug": name,
            "source_language": "de",
            "target_language": "ru",
            "text_mode": "single",
            "source_raw_text": "Das Haus.",
        },
        [
            {
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": "Das Haus.",
                "sentence_destination": "Дом.",
                "text_provenance": "cached:sqlite",
            }
        ],
        [
            {
                "session_zid": zid,
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Haus",
                "lemma": "Haus",
                "word_destination": "дом",
                "ipa": None,
                "morphology": None,
                "pos": None,
                "gender": None,
                "word_provenance": "live:google",
                "selected": 0,
            }
        ],
    )
    db.save_session_bundle(
        {
            "zid": sibling_zid,
            "slug": f"{name}-sibling",
            "source_language": "de",
            "target_language": "ru",
            "text_mode": "single",
            "source_raw_text": "Der Baum.",
        },
        [
            {
                "session_zid": sibling_zid,
                "sentence_index": 1,
                "sentence_source": "Der Baum.",
                "sentence_destination": "Дерево.",
                "text_provenance": "cached:sqlite",
            }
        ],
        [
            {
                "session_zid": sibling_zid,
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Baum",
                "lemma": "Baum",
                "word_destination": "дерево",
                "ipa": None,
                "morphology": None,
                "pos": None,
                "gender": None,
                "word_provenance": "live:deepl",
                "selected": 1,
            }
        ],
    )
    arbiter = SessionArbiter(config=cfg, resolved_paths=resolved_paths, wordfill_cfg={"enabled": False})
    return db, cfg, resolved_paths, arbiter, zid, sibling_zid


def _install_heartbeat_rejection_trigger(db: KardenwortDB, message: str = "owned heartbeat startup failure"):
    with db.get_connection() as connection:
        connection.execute(
            "CREATE TRIGGER reject_worker_heartbeat BEFORE UPDATE OF worker_heartbeat_at ON sessions "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )


def _install_terminal_status_rejection_trigger(db: KardenwortDB, message: str = "owned terminal status failure"):
    with db.get_connection() as connection:
        connection.execute(
            "CREATE TRIGGER reject_terminal_worker_status BEFORE UPDATE OF worker_status ON sessions "
            "WHEN NEW.worker_status IN ('finished', 'failed') "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )


def _snapshot_session_facts(db: KardenwortDB, zid: str) -> dict:
    return {
        "worker_status": db.get_worker_status(zid),
        "sentences": db.get_sentences_by_session(zid),
        "words": db.get_words_by_session(zid),
    }


def _wait_for_queued_task(enrichment_queue, session_zid: str, timeout: float = 5.0):
    done = threading.Event()
    with enrichment_queue._lock:
        fut = enrichment_queue._active_progressive_sessions.get(session_zid)
    if fut is None:
        return
    fut.add_done_callback(lambda _completed: done.set())
    assert done.wait(timeout=timeout), f"Queued progressive task for {session_zid} did not finish within {timeout}s"


# ---------------------------------------------------------------------------
# Scenario 1: Heartbeat startup failure (synchronous and queued)
# ---------------------------------------------------------------------------


def test_worker_running_write_then_heartbeat_failure_sync(tmp_path):
    """When running commits but initial heartbeat fails, finalization records failed + timestamp and keeps error observable."""
    db, _cfg, _paths, arbiter, zid, sibling_zid = _seed_worker_world(tmp_path, "hb_fail_sync")
    sibling_before = _snapshot_session_facts(db, sibling_zid)
    primary_words_before = db.get_words_by_session(zid)
    _install_heartbeat_rejection_trigger(db)

    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="owned heartbeat startup failure"):
            arbiter.enrichment_queue._execute_progressive_task(
                zid, arbiter, "de", "ru", skip_intellifiller=True
            )

        status = db.get_worker_status(zid)
        assert status["worker_status"] == "failed"
        assert status["worker_started_at"] is not None
        assert status["worker_finished_at"] is not None
        assert status["worker_heartbeat_at"] is None

        emitted = _drain_events(events)
        finished = [e for e in emitted if e.get("stage") == "finished"]
        assert finished
        terminal = finished[-1]
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "ERR_PROGRESSIVE_STARTUP_FAILED"
        assert terminal["error"]["provider"] == "storage"
        assert terminal["error"]["details"].get("phase") == "startup"
        assert "owned heartbeat startup failure" in terminal["error"]["message"]

        assert db.get_words_by_session(zid) == primary_words_before
        assert _snapshot_session_facts(db, sibling_zid) == sibling_before
    finally:
        dispatcher.unregister(zid, events)
        arbiter.enrichment_queue.shutdown(wait=True)
        desk.unregister_failover_callback(arbiter._on_global_failover)


def test_worker_running_write_then_heartbeat_failure_queued(tmp_path):
    """Queued invocation records failed terminal status and surfaces the original startup error on shutdown."""
    db, _cfg, _paths, arbiter, zid, sibling_zid = _seed_worker_world(tmp_path, "hb_fail_queued")
    sibling_before = _snapshot_session_facts(db, sibling_zid)
    _install_heartbeat_rejection_trigger(db)

    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        res = arbiter.enrichment_queue.enqueue_progressive_task(
            zid, arbiter, "de", "ru", skip_intellifiller=True
        )
        assert res == {"status": "queued", "session_zid": zid}
        _wait_for_queued_task(arbiter.enrichment_queue, zid)

        status = db.get_worker_status(zid)
        assert status["worker_status"] == "failed"
        assert status["worker_started_at"] is not None
        assert status["worker_finished_at"] is not None
        assert status["worker_heartbeat_at"] is None

        finished = [e for e in _drain_events(events) if e.get("stage") == "finished"]
        assert finished
        terminal = finished[-1]
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "ERR_PROGRESSIVE_STARTUP_FAILED"
        assert terminal["error"]["provider"] == "storage"

        with pytest.raises(BaseExceptionGroup, match="Controller queue disposal failed") as caught:
            arbiter.enrichment_queue.shutdown(wait=True)
        leaves = _flatten_exceptions(caught.value)
        assert any(
            isinstance(err, sqlite3.IntegrityError) and "owned heartbeat startup failure" in str(err)
            for err in leaves
        ), leaves
        assert not arbiter.enrichment_queue._active_progressive_sessions
        assert _snapshot_session_facts(db, sibling_zid) == sibling_before
    finally:
        dispatcher.unregister(zid, events)
        desk.unregister_failover_callback(arbiter._on_global_failover)


# ---------------------------------------------------------------------------
# Scenario 2: Finalization also fails (heartbeat + terminal status rejected)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "queued"])
def test_worker_heartbeat_and_terminal_status_both_rejected(tmp_path, mode):
    """When storage rejects both heartbeat and terminal status, combined failure is reported without claiming completion."""
    db, _cfg, _paths, arbiter, zid, sibling_zid = _seed_worker_world(tmp_path, f"dual_fail_{mode}")
    sibling_before = _snapshot_session_facts(db, sibling_zid)
    _install_heartbeat_rejection_trigger(db, "owned heartbeat startup failure")
    _install_terminal_status_rejection_trigger(db, "owned terminal status failure")

    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        if mode == "sync":
            with pytest.raises(BaseExceptionGroup) as caught:
                arbiter.enrichment_queue._execute_progressive_task(
                    zid, arbiter, "de", "ru", skip_intellifiller=True
                )
            leaves = _flatten_exceptions(caught.value)
        else:
            res = arbiter.enrichment_queue.enqueue_progressive_task(
                zid, arbiter, "de", "ru", skip_intellifiller=True
            )
            assert res == {"status": "queued", "session_zid": zid}
            _wait_for_queued_task(arbiter.enrichment_queue, zid)
            with pytest.raises(BaseExceptionGroup, match="Controller queue disposal failed") as caught:
                arbiter.enrichment_queue.shutdown(wait=True)
            leaves = _flatten_exceptions(caught.value)

        assert any(
            isinstance(err, sqlite3.IntegrityError) and "owned heartbeat startup failure" in str(err)
            for err in leaves
        ), leaves
        assert any(
            isinstance(err, sqlite3.IntegrityError) and "owned terminal status failure" in str(err)
            for err in leaves
        ), leaves

        status = db.get_worker_status(zid)
        assert status["worker_status"] == "running"
        assert status["worker_finished_at"] is None

        emitted = _drain_events(events)
        assert not any(e.get("status") == "success" for e in emitted), emitted
        finished = [e for e in emitted if e.get("stage") == "finished"]
        assert all(e.get("status") == "failed" for e in finished)
        assert _snapshot_session_facts(db, sibling_zid) == sibling_before
    finally:
        dispatcher.unregister(zid, events)
        if mode == "sync":
            arbiter.enrichment_queue.shutdown(wait=True)
        desk.unregister_failover_callback(arbiter._on_global_failover)


# ---------------------------------------------------------------------------
# Scenario 3: Successful startup (valid adjacent control)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "queued"])
def test_worker_successful_startup_control(tmp_path, mode):
    """Valid adjacent control: ordinary worker status, generation ownership, and terminal delivery remain unchanged."""
    db, _cfg, _paths, arbiter, zid, sibling_zid = _seed_worker_world(tmp_path, f"ok_control_{mode}")
    sibling_before = _snapshot_session_facts(db, sibling_zid)

    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        if mode == "sync":
            arbiter.enrichment_queue._execute_progressive_task(
                zid, arbiter, "de", "ru", skip_intellifiller=True
            )
        else:
            res = arbiter.enrichment_queue.enqueue_progressive_task(
                zid, arbiter, "de", "ru", skip_intellifiller=True
            )
            assert res == {"status": "queued", "session_zid": zid}
            _wait_for_queued_task(arbiter.enrichment_queue, zid)
            assert arbiter.enrichment_queue._progressive_generations[zid] == 1

        status = db.get_worker_status(zid)
        assert status["worker_status"] == "finished"
        assert status["worker_started_at"] is not None
        assert status["worker_heartbeat_at"] is not None
        assert status["worker_finished_at"] is not None

        finished = [e for e in _drain_events(events) if e.get("stage") == "finished"]
        assert finished
        terminal = finished[-1]
        assert terminal["status"] == "success"
        assert terminal["error"] is None
        assert terminal["rows"]["0"]["trans"] == "дом"
        assert terminal["rows"]["0"]["provenance"] == "live:google"

        arbiter.enrichment_queue.shutdown(wait=True)
        assert not arbiter.enrichment_queue._unobserved_progressive_failures
        assert not arbiter.enrichment_queue._active_progressive_sessions
        assert _snapshot_session_facts(db, sibling_zid) == sibling_before
    finally:
        dispatcher.unregister(zid, events)
        desk.unregister_failover_callback(arbiter._on_global_failover)


# ---------------------------------------------------------------------------
# Guard checks: generation supersession and session deletion during startup
# ---------------------------------------------------------------------------


def test_worker_startup_failure_preserves_generation_and_deletion_guards(tmp_path):
    """Superseded generations do not overwrite newer status/events; soft-deleted sessions never resurrect or publish success."""
    dispatcher = EventDispatcher.get_instance()

    # Subcase A: superseded before startup must not touch SQLite or emit events
    db_a, _cfg_a, _paths_a, arbiter_a, zid_a, _ = _seed_worker_world(tmp_path, "guard_superseded_pre", "20261119085341", "20261119085342")
    _install_heartbeat_rejection_trigger(db_a)
    with arbiter_a.enrichment_queue._lock:
        arbiter_a.enrichment_queue._progressive_generations[zid_a] = 2
    ev_a = dispatcher.register(zid_a)
    try:
        arbiter_a.enrichment_queue._execute_progressive_task(
            zid_a, arbiter_a, "de", "ru", skip_intellifiller=True, generation=1
        )
        assert db_a.get_worker_status(zid_a)["worker_status"] is None
        assert _drain_events(ev_a) == []
    finally:
        dispatcher.unregister(zid_a, ev_a)
        arbiter_a.enrichment_queue.shutdown(wait=True)
        desk.unregister_failover_callback(arbiter_a._on_global_failover)

    # Subcase B: superseded between running write and heartbeat failure must not clobber newer generation status/events
    db_b, cfg_b, paths_b, arbiter_b, zid_b, _ = _seed_worker_world(tmp_path, "guard_superseded_mid", "20261119085343", "20261119085344")
    _install_heartbeat_rejection_trigger(db_b)
    with arbiter_b.enrichment_queue._lock:
        arbiter_b.enrichment_queue._progressive_generations[zid_b] = 1

    class SupersedeBeforeHeartbeatDB(KardenwortDB):
        def update_worker_heartbeat(self, session_zid, heartbeat_at=None, zid=None):
            with arbiter_b.enrichment_queue._lock:
                arbiter_b.enrichment_queue._progressive_generations[session_zid] = 2
            return super().update_worker_heartbeat(session_zid, heartbeat_at=heartbeat_at, zid=zid)

    adapter_b = desk.SqliteStorageAdapter(config=cfg_b, resolved_paths=paths_b, db_path=db_b.db_path)
    adapter_b.db = SupersedeBeforeHeartbeatDB(db_path=db_b.db_path)
    arbiter_b.storage_adapter = adapter_b

    ev_b = dispatcher.register(zid_b)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="owned heartbeat startup failure"):
            arbiter_b.enrichment_queue._execute_progressive_task(
                zid_b, arbiter_b, "de", "ru", skip_intellifiller=True, generation=1
            )
        # Generation 1 must not clobber SQLite terminal status or emit a stale finished event for generation 2
        assert db_b.get_worker_status(zid_b)["worker_status"] == "running"
        assert db_b.get_worker_status(zid_b)["worker_finished_at"] is None
        assert _drain_events(ev_b) == []
    finally:
        dispatcher.unregister(zid_b, ev_b)
        arbiter_b.enrichment_queue.shutdown(wait=True)
        desk.unregister_failover_callback(arbiter_b._on_global_failover)

    # Subcase C: soft-deleted session with heartbeat startup failure records failed + finished_at and removes cache
    db_c, _cfg_c, _paths_c, arbiter_c, zid_c, _ = _seed_worker_world(tmp_path, "guard_deleted", "20261119085345", "20261119085346")
    arbiter_c.sessions[zid_c] = {"headers": ["WordSource"], "data_rows": [["Haus"]]}
    assert db_c.soft_delete_session(zid_c)
    _install_heartbeat_rejection_trigger(db_c)
    ev_c = dispatcher.register(zid_c)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="owned heartbeat startup failure"):
            arbiter_c.enrichment_queue._execute_progressive_task(
                zid_c, arbiter_c, "de", "ru", skip_intellifiller=True
            )
        status_c = db_c.get_worker_status(zid_c)
        assert status_c["worker_status"] == "failed"
        assert status_c["worker_finished_at"] is not None
        assert zid_c not in arbiter_c.sessions
        with db_c.get_connection(read_only=True) as conn:
            row = conn.execute("SELECT deleted_at FROM sessions WHERE zid = ?", (zid_c,)).fetchone()
            assert row is not None and row[0] is not None
        finished_c = [e for e in _drain_events(ev_c) if e.get("stage") == "finished"]
        assert len(finished_c) == 1
        assert finished_c[0]["status"] == "failed"
        assert finished_c[0]["rows"] == {}
    finally:
        dispatcher.unregister(zid_c, ev_c)
        arbiter_c.enrichment_queue.shutdown(wait=True)
        desk.unregister_failover_callback(arbiter_c._on_global_failover)


def test_worker_distinguishes_initialization_failure_from_provider_failure(tmp_path, monkeypatch):
    """Startup persistence failure reports storage/startup identity and stays observable; external provider failure remains a provider stage failure."""
    import kardenwort_controller as controller

    db, cfg, paths, arbiter, zid, _ = _seed_worker_world(tmp_path, "provider_vs_init", "20261119085347", "20261119085348")
    cfg.set("triggers", "run_text_translation", "auto")
    db.save_session_bundle(
        {
            "zid": zid,
            "slug": "provider_vs_init",
            "source_language": "de",
            "target_language": "ru",
            "text_mode": "single",
            "source_raw_text": "Das Haus.",
        },
        [
            {
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": "Das Haus.",
                "sentence_destination": "",
                "text_provenance": None,
            }
        ],
        [
            {
                "session_zid": zid,
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Haus",
                "lemma": "Haus",
                "word_destination": "дом",
                "ipa": None,
                "morphology": None,
                "pos": None,
                "gender": None,
                "word_provenance": "live:google",
                "selected": 0,
            }
        ],
    )

    def failing_provider(*_args, **_kwargs):
        raise RuntimeError("simulated external provider outage")

    monkeypatch.setattr(controller, "translate_source_text", failing_provider)
    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        arbiter.enrichment_queue._execute_progressive_task(
            zid, arbiter, "de", "ru", skip_intellifiller=True
        )
        status = db.get_worker_status(zid)
        assert status["worker_status"] == "failed"
        assert status["worker_heartbeat_at"] is not None
        assert status["worker_finished_at"] is not None

        finished = [e for e in _drain_events(events) if e.get("stage") == "finished"]
        assert finished
        terminal = finished[-1]
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "ERR_SENTENCE_TRANSLATION_FAILED"
        assert terminal["error"]["provider"] != "storage"
        assert terminal["error"]["details"].get("phase") != "startup"

        arbiter.enrichment_queue.shutdown(wait=True)
        assert not arbiter.enrichment_queue._unobserved_progressive_failures
    finally:
        dispatcher.unregister(zid, events)
        desk.unregister_failover_callback(arbiter._on_global_failover)


def test_startup_finalization_helpers_meet_complexity_ceiling():
    """All new and edited startup/finalization helpers stay pure and within CC <= 7."""
    import ast
    from tests.test_intent_architecture import complexity, functions

    root = Path(__file__).resolve().parents[1]
    ctrl_tree = ast.parse((root / "kardenwort_controller.py").read_text(encoding="utf-8-sig"))
    ctrl_names = {
        "_flatten_finalization_errors",
        "raise_progressive_finalization_errors",
        "_start_progressive_worker_state",
        "_finalize_progressive_execution",
    }
    measured = {node.name: complexity(node) for node in functions(ctrl_tree) if node.name in ctrl_names}
    assert set(measured) == ctrl_names
    assert max(measured.values()) <= 7, measured

    core_tree = ast.parse((root / "kardenwort_progressive_completion.py").read_text(encoding="utf-8"))
    core_measured = {node.name: complexity(node) for node in functions(core_tree)}
    assert "startup_persistence_error" in core_measured
    assert max(core_measured.values()) <= 7, core_measured

