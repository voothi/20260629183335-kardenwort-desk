"""Phase 1 deterministic reproductions for controller lock cycles, cache publication,
batch retirement/replacement, and delete-versus-worker/save races.

All tests use real SQLite/TSV storage, real threading barriers/events, and bounded joins
without arbitrary sleeps or leaked non-daemon threads.
"""
import concurrent.futures
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
import kardenwort_enrichment as enrichment
import kardenwort_progressive_completion as progressive
from kardenwort_controller import SessionArbiter, _InFlightProgressiveBatch
from kardenwort_db import KardenwortDB
from tests.controller_test_support import controller_lifetime, owned_configuration
from tests.enrichment_test_support import enrichment_engine
from tests.thread_support import Worker


TOKEN = "test-controller-api-key"
HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "DeskSelected",
]


def _http_json(url, path, *, method="GET", body=None, token=TOKEN):
    headers = {}
    if token is not None:
        headers["X-API-Token"] = token
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    req = Request(url + path, data=payload, headers=headers, method=method)
    try:
        resp = urlopen(req, timeout=10)
    except HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(raw) if raw else {}


def _build_sqlite_arbiter(tmp_path: Path, *, busy_timeout_ms: int = 250):
    config, paths, goldendict = owned_configuration(tmp_path, "", "sqlite")
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        + "\n".join(HEADERS)
        + "\n[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "WordSourceIPA=ipa\n"
        "WordSourceMorphologyAI=morphology\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n"
        "SentenceDestination=sentence_destination\n"
        "DeskSelected=selected\n"
        "[desk_editable]\n"
        "editable_columns=WordDestination,DeskSelected\n",
        encoding="utf-8",
    )
    config.set("settings", "anki_mapping_file", str(mapping_file))
    paths["anki_mapping_file"] = mapping_file
    arbiter = SessionArbiter(config=config, resolved_paths=paths, wordfill_cfg={"enabled": False})
    arbiter.goldendict = goldendict
    adapter = desk.SqliteStorageAdapter(config, paths)
    adapter.db.busy_timeout_ms = busy_timeout_ms
    arbiter.storage_adapter = adapter
    return config, paths, arbiter, adapter, mapping_file


def _seed_sqlite_session(adapter, tmp_path: Path, zid: str, *, word_dest: str = "дом", language: str = "de"):
    rows = [["0", "Haus", "Haus", word_dest, "", "", "1", "Das Haus.", "Дом.", "0"]]
    adapter.save_session(
        session_zid=zid,
        slug=f"repro-{zid}",
        source_language=language,
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus.",
        headers=list(HEADERS),
        data_rows=rows,
        comments=[],
        working_tsv_path=tmp_path / f"{zid}.{language}.tsv",
        row_provenances={0: "live:google"},
        sentences=[{
            "sentence_index": 1,
            "sentence_source": "Das Haus.",
            "sentence_destination": "Дом.",
            "text_provenance": "live:google",
        }],
    )
    return adapter.restore_session(zid)


def _warm_cache_entry(arbiter, restored, zid: str, *, language: str = "de"):
    headers = list(restored["headers"])
    rows = [list(r) for r in restored["data_rows"]]
    arbiter.sessions[zid] = {
        "session_zid": zid,
        "text": "Das Haus.",
        "comments": [],
        "headers": headers,
        "data_rows": rows,
        "language": language,
        "target_lang": "ru",
        "text_mode": "single",
        "tsv_path": zid,
        "fingerprint": desk.compute_content_fingerprint(rows),
        "row_provenances": {0: "live:google"},
        "text_provenance": "live:google",
        "textProvenance": "live:google",
        "lock": threading.Lock(),
    }


def _cleanup_arbiter(arbiter):
    try:
        arbiter.enrichment_queue.shutdown(wait=True)
    finally:
        desk.unregister_failover_callback(arbiter._on_global_failover)


def test_cold_save_and_completion_settle_without_lock_cycle(tmp_path, monkeypatch):
    """Cold save_session must not hold arbiter._lock across SQLite writer acquisition
    while _accept_completion holds SQLite writer and acquires queue -> arbiter._lock.
    """
    config, paths, arbiter, adapter, mapping_file = _build_sqlite_arbiter(tmp_path, busy_timeout_ms=250)
    zid = "20261123050001"
    try:
        restored = _seed_sqlite_session(adapter, tmp_path, zid, word_dest="дом")
        assert zid not in arbiter.sessions

        mapping = desk.load_anki_mapping(mapping_file)
        headers = list(restored["headers"])
        rows = [list(r) for r in restored["data_rows"]]
        roles = desk.get_role_fields(mapping, headers)
        request = progressive.CompletionRequest(
            zid, "de", True, zid, mapping, headers, rows, roles, "live:google", None, f"{zid}:trace", {}, 1,
        )
        with arbiter.enrichment_queue._lock:
            arbiter.enrichment_queue._progressive_generations[zid] = 1
        snapshot = arbiter.enrichment_queue._completion_snapshot(arbiter, adapter, request)

        barrier = threading.Barrier(2, timeout=5)
        real_core_edit_save = controller.core_edit_save
        real_publish_locked = arbiter.enrichment_queue._publish_completion_locked
        real_get_storage_adapter = controller.get_storage_adapter
        real_desk_get_storage_adapter = desk.get_storage_adapter

        def shared_adapter(*args, **kwargs):
            return adapter

        def synced_core_edit_save(*args, **kwargs):
            barrier.wait()
            return real_core_edit_save(*args, **kwargs)

        def synced_publish_locked(*args, **kwargs):
            barrier.wait()
            return real_publish_locked(*args, **kwargs)

        monkeypatch.setattr(controller, "get_storage_adapter", shared_adapter)
        monkeypatch.setattr(desk, "get_storage_adapter", shared_adapter)
        monkeypatch.setattr(controller, "core_edit_save", synced_core_edit_save)
        monkeypatch.setattr(arbiter.enrichment_queue, "_publish_completion_locked", synced_publish_locked)

        save_worker = Worker(lambda: arbiter.save_session(
            session_zid=zid,
            deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "жилище"}],
            language="de",
        ))
        completion_worker = Worker(lambda: arbiter.enrichment_queue._accept_completion(
            arbiter, adapter, request, snapshot,
        ))

        save_err = None
        comp_err = None
        save_res = None
        comp_res = None
        try:
            save_res = save_worker.result(timeout=5)
        except BaseException as exc:
            save_err = exc
        try:
            comp_res = completion_worker.result(timeout=5)
        except BaseException as exc:
            comp_err = exc

        assert not save_worker.thread.is_alive()
        assert not completion_worker.thread.is_alive()
        assert save_err is None, f"Cold save failed under completion overlap (lock cycle): {save_err!r}"
        assert comp_err is None, f"Completion failed under cold save overlap: {comp_err!r}"
        assert save_res["status"] == "success"
        assert comp_res in ("published", "changed")

        cold_words = adapter.db.get_words_by_session(zid)
        assert cold_words[0]["word_destination"] == "жилище"
    finally:
        _cleanup_arbiter(arbiter)


def test_warm_save_delayed_publication_cannot_overwrite_newer_completion_cache(tmp_path, monkeypatch):
    """A delayed warm save_session that committed E1 must not overwrite newer committed E2
    published into arbiter.sessions by completion between save commit and save publication.
    """
    config, paths, arbiter, adapter, mapping_file = _build_sqlite_arbiter(tmp_path, busy_timeout_ms=500)
    zid = "20261123050002"
    try:
        restored = _seed_sqlite_session(adapter, tmp_path, zid, word_dest="начало")
        _warm_cache_entry(arbiter, restored, zid)

        save_committed = threading.Event()
        completion_published = threading.Event()
        real_core_edit_save = controller.core_edit_save

        def paused_after_commit(*args, **kwargs):
            result = real_core_edit_save(*args, **kwargs)
            save_committed.set()
            assert completion_published.wait(5), "Completion did not publish newer snapshot"
            return result

        monkeypatch.setattr(controller, "core_edit_save", paused_after_commit)

        def run_save():
            return arbiter.save_session(
                session_zid=zid,
                deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "старое_E1"}],
                language="de",
            )

        def run_newer_completion():
            assert save_committed.wait(5), "Warm save did not commit E1"
            adapter.batch_update_words(
                zid,
                [{"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "новое_E2", "word_provenance": "live:intellifiller"}}],
            )
            mapping = desk.load_anki_mapping(mapping_file)
            latest = adapter.restore_session(zid)
            headers = list(latest["headers"])
            rows = [list(r) for r in latest["data_rows"]]
            roles = desk.get_role_fields(mapping, headers)
            request = progressive.CompletionRequest(
                zid, "de", True, zid, mapping, headers, rows, roles, "live:google", None, f"{zid}:trace", {}, 1,
            )
            with arbiter.enrichment_queue._lock:
                arbiter.enrichment_queue._progressive_generations[zid] = 1
            snapshot = arbiter.enrichment_queue._completion_snapshot(arbiter, adapter, request)
            outcome = arbiter.enrichment_queue._accept_completion(arbiter, adapter, request, snapshot)
            completion_published.set()
            return outcome

        save_worker = Worker(run_save)
        comp_worker = Worker(run_newer_completion)
        try:
            comp_outcome = comp_worker.result(timeout=5)
            save_result = save_worker.result(timeout=5)
        finally:
            completion_published.set()
            save_worker.thread.join(5)
            comp_worker.thread.join(5)

        assert not save_worker.thread.is_alive()
        assert not comp_worker.thread.is_alive()
        assert comp_outcome == "published"
        assert save_result["status"] == "success"

        authoritative = adapter.restore_session(zid)
        auth_rows = authoritative["data_rows"]
        auth_fp = desk.compute_content_fingerprint(auth_rows)
        col_dest = authoritative["headers"].index("WordDestination")
        assert auth_rows[0][col_dest] == "новое_E2"

        with arbiter._lock:
            cached = dict(arbiter.sessions[zid])
        cached_col_dest = cached["headers"].index("WordDestination")
        assert cached["data_rows"][0][cached_col_dest] == "новое_E2", (
            f"Stale save E1 overwrote newer completion E2 in cache: {cached['data_rows']}"
        )
        assert cached["fingerprint"] == auth_fp
    finally:
        _cleanup_arbiter(arbiter)


def test_tsv_cold_save_does_not_block_unrelated_session_on_global_arbiter_lock(tmp_path, monkeypatch):
    """A cold TSV save must not hold arbiter._lock across filesystem I/O and block unrelated sessions."""
    config, paths, goldendict = owned_configuration(tmp_path, "", "tsv")
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n" + "\n".join(HEADERS) + "\n[desk_columns]\nWordDestination=word_translation\nDeskSelected=selected\n"
        "[desk_editable]\neditable_columns=WordDestination,DeskSelected\n",
        encoding="utf-8",
    )
    config.set("settings", "anki_mapping_file", str(mapping_file))
    paths["anki_mapping_file"] = mapping_file
    arbiter = SessionArbiter(config=config, resolved_paths=paths, wordfill_cfg={"enabled": False})
    arbiter.goldendict = goldendict

    zid_a = "20261123050003"
    zid_b = "20261123050004"
    tsv_a = paths["results_dir"] / f"{zid_a}.de.tsv"
    tsv_b = paths["results_dir"] / f"{zid_b}.de.tsv"
    for p, word in ((tsv_a, "Haus"), (tsv_b, "Baum")):
        desk.save_tsv_rows_safely(
            p, [], list(HEADERS), [["0", word, word, "old", "", "", "1", f"Das {word}.", "old", "0"]],
        )

    entered_a = threading.Event()
    release_a = threading.Event()
    real_core_edit_save = controller.core_edit_save

    def slow_save_a(tsv_path_or_session, *args, **kwargs):
        if str(tsv_path_or_session) == zid_a:
            entered_a.set()
            assert release_a.wait(5), "Cold TSV save A was not released"
        return real_core_edit_save(tsv_path_or_session, *args, **kwargs)

    monkeypatch.setattr(controller, "core_edit_save", slow_save_a)
    try:
        worker_a = Worker(lambda: arbiter.save_session(
            session_zid=zid_a,
            deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "A_new"}],
            language="de",
        ))
        assert entered_a.wait(5), "Cold TSV save A never entered I/O"

        worker_b = Worker(lambda: arbiter.save_session(
            session_zid=zid_b,
            deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "B_new"}],
            language="de",
        ))
        try:
            b_finished = worker_b.thread.join(1.0) or not worker_b.thread.is_alive()
            assert not worker_b.thread.is_alive(), "Unrelated TSV session B was blocked by arbiter._lock held during A's I/O"
            res_b = worker_b.result(timeout=1)
            assert res_b["status"] == "success"
        finally:
            release_a.set()
            worker_a.result(timeout=5)
            worker_b.thread.join(5)
    finally:
        release_a.set()
        _cleanup_arbiter(arbiter)


def test_batch_retirement_atomic_sibling_join_never_inherits_cancelled_error(tmp_path, monkeypatch):
    """Deterministic barrier reproduction: sibling B arrives after _has_active_batch_subscriber
    returns False and before _retire_progressive_batch runs; B must receive a valid result, not CancelledError.
    """
    config, paths, arbiter, adapter, _ = _build_sqlite_arbiter(tmp_path)
    eq = arbiter.enrichment_queue
    zid_a = "20261123050007"
    zid_b = "20261123050008"
    try:
        with enrichment_engine(config) as engine:
            engine.release.set()
            batch = [{"row_id": 0, "WordSource": "Haus", "SentenceSource": "Das Haus."}]
            request_key = enrichment.progressive_request_key(batch, "fixture", "de", "ru", config, roles={})

            with eq._lock:
                eq._progressive_generations[zid_a] = 1
                eq._progressive_generations[zid_b] = 1

            check_returned_false = threading.Event()
            sibling_joined = threading.Event()
            real_has_active = eq._has_active_batch_subscriber

            def paused_has_active(subscribers):
                active = real_has_active(subscribers)
                if not active:
                    check_returned_false.set()
                    sibling_joined.wait(5)
                return active

            monkeypatch.setattr(eq, "_has_active_batch_subscriber", paused_has_active)

            gate_started = threading.Event()
            gate_release = threading.Event()
            blocker = eq._executor.submit(lambda: (gate_started.set(), gate_release.wait(5)))
            assert gate_started.wait(5)
            try:
                fut_a = eq._acquire_progressive_batch_future(
                    request_key, batch, "fixture", "de", config, zid_a, 1, zid_a, f"{zid_a}:t",
                )
                with eq._lock:
                    eq._progressive_generations[zid_a] = 2
            finally:
                gate_release.set()

            blocker.result(timeout=5)

            # If fixed atomically, _has_active_batch_subscriber may not be called outside lock at all;
            # wait for either the check hook OR fut_a finishing (which means atomic retirement happened).
            deadline_event = threading.Event()
            def wake_on_done(_f):
                deadline_event.set()
            fut_a.add_done_callback(wake_on_done)
            while not check_returned_false.is_set() and not deadline_event.is_set():
                check_returned_false.wait(0.01)

            def sibling_b_call():
                fut_b = eq._acquire_progressive_batch_future(
                    request_key, batch, "fixture", "de", config, zid_b, 1, zid_b, f"{zid_b}:t",
                )
                sibling_joined.set()
                return fut_b.result(timeout=5)

            worker_b = Worker(sibling_b_call)
            try:
                res_b = worker_b.result(timeout=5)
            finally:
                sibling_joined.set()
                worker_b.thread.join(5)

            assert not worker_b.thread.is_alive()
            assert isinstance(res_b, dict) and 0 in res_b
            assert res_b[0]["WordDestination"] == "trans_Haus"
            assert eq._inflight_progressive_batches == {}
    finally:
        _cleanup_arbiter(arbiter)


def test_old_batch_retirement_preserves_replacement_entry(tmp_path):
    """Retiring an old or unspecified future must never delete a replacement batch for the same request_key."""
    _, _, arbiter, _, _ = _build_sqlite_arbiter(tmp_path)
    eq = arbiter.enrichment_queue
    try:
        key = ("provider", "fixture", "de", "ru", (), ())
        old_fut = concurrent.futures.Future()
        replacement_fut = concurrent.futures.Future()
        replacement_entry = _InFlightProgressiveBatch(replacement_fut, [("20261123050009", 1)])
        with eq._lock:
            eq._inflight_progressive_batches[key] = replacement_entry

        # Old future callback must not disturb replacement.
        eq._retire_progressive_batch(key, old_fut)
        assert eq._inflight_progressive_batches.get(key) is replacement_entry

        # Key-only retirement (future=None) must ALSO not blindly wipe replacement.
        eq._retire_progressive_batch(key, None)
        assert eq._inflight_progressive_batches.get(key) is replacement_entry
    finally:
        _cleanup_arbiter(arbiter)


def test_custom_selected_role_excluded_from_progressive_request_key(tmp_path):
    """Configured roles['selected'] must not split equivalent progressive enrichment batches,
    while context/provider/target differences remain significant.
    """
    config, _, _ = owned_configuration(tmp_path, "", "sqlite")
    roles = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "sentence_index": "SentenceSourceIndex",
        "selected": "CustomPicked",
    }
    batch_selected = [{"row_id": 0, "WordSource": "Haus", "SentenceSource": "Das Haus.", "CustomPicked": "1"}]
    batch_unselected = [{"row_id": 0, "WordSource": "Haus", "SentenceSource": "Das Haus.", "CustomPicked": ""}]
    batch_diff_ctx = [{"row_id": 0, "WordSource": "Haus", "SentenceSource": "Ein anderes Haus.", "CustomPicked": "1"}]

    k1 = enrichment.progressive_request_key(batch_selected, "fixture", "de", "ru", config, roles=roles)
    k2 = enrichment.progressive_request_key(batch_unselected, "fixture", "de", "ru", config, roles=roles)
    k_diff_ctx = enrichment.progressive_request_key(batch_diff_ctx, "fixture", "de", "ru", config, roles=roles)
    k_diff_target = enrichment.progressive_request_key(batch_selected, "fixture", "de", "en", config, roles=roles)

    assert k1 == k2, "Custom roles['selected'] value split equivalent progressive enrichment request keys"
    assert k1 != k_diff_ctx
    assert k1 != k_diff_target


def test_delete_all_matching_purge_restore_and_failed_delete_cache_and_generation_fences(tmp_path):
    """Single delete, all_matching batch-delete, purge, failed delete, and restore must
    coordinate SQLite mutations with arbiter cache eviction and progressive generation fences.
    """
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999), backend="sqlite") as (url, server):
        adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
        zid_single = "20261123050011"
        zid_match = "20261123050012"
        zid_keep = "20261123050013"

        for zid, lang in ((zid_single, "de"), (zid_match, "de"), (zid_keep, "fr")):
            restored = _seed_sqlite_session(adapter, tmp_path, zid, word_dest="alt", language=lang)
            _warm_cache_entry(server.arbiter, restored, zid, language=lang)
            with server.arbiter.enrichment_queue._lock:
                server.arbiter.enrichment_queue._progressive_generations[zid] = 1

        # 1. Single delete evicts cache, bumps generation, and makes status return 404.
        st_del, body_del = _http_json(url, "/api/v1/admin/sessions/delete", method="POST", body={"session_zid": zid_single})
        assert st_del == 200 and body_del["data"]["ok"] is True
        assert zid_single not in server.arbiter.sessions, "Single delete left deleted session in warm arbiter cache"
        with server.arbiter.enrichment_queue._lock:
            assert server.arbiter.enrichment_queue._progressive_generations.get(zid_single, 0) > 1
        st_status_single, _ = _http_json(url, f"/session/status?zid={zid_single}")
        assert st_status_single == 404

        # 2. all_matching batch-delete evicts all actually deleted ZIDs, fences workers, keeps untouched ZID.
        st_batch, body_batch = _http_json(
            url, "/api/v1/admin/sessions/batch-delete", method="POST",
            body={"mode": "all_matching", "filter": {"language": "de"}},
        )
        assert st_batch == 200 and body_batch["data"]["deleted_count"] == 1
        assert zid_match not in server.arbiter.sessions, "all_matching batch-delete left deleted session in warm cache"
        with server.arbiter.enrichment_queue._lock:
            assert server.arbiter.enrichment_queue._progressive_generations.get(zid_match, 0) > 1
        assert _http_json(url, f"/session/status?zid={zid_match}")[0] == 404
        assert zid_keep in server.arbiter.sessions
        assert _http_json(url, f"/session/status?zid={zid_keep}")[0] == 200

        # 3. Failed delete preserves live cache and generation intact.
        blocker = sqlite3.connect(server.resolved_paths["sqlite_db_path"], isolation_level=None)
        server.config.set("storage", "busy_timeout_ms", "10") if server.config.has_section("storage") else None
        try:
            blocker.execute("BEGIN IMMEDIATE")
            # Force fast busy timeout on DB connections used by controller route
            orig_init = KardenwortDB.__init__
            def short_busy(self, *args, **kwargs):
                orig_init(self, *args, **kwargs)
                self.busy_timeout_ms = 10
            KardenwortDB.__init__ = short_busy
            try:
                st_fail, _ = _http_json(url, "/api/v1/admin/sessions/delete", method="POST", body={"session_zid": zid_keep})
            finally:
                KardenwortDB.__init__ = orig_init
            assert st_fail >= 500
            assert zid_keep in server.arbiter.sessions
        finally:
            blocker.rollback()
            blocker.close()

        # 4. Soft-delete then external SQLite edit then restore must read fresh storage, not pre-delete cache.
        assert _http_json(url, "/api/v1/admin/sessions/delete", method="POST", body={"session_zid": zid_keep})[0] == 200
        assert zid_keep not in server.arbiter.sessions
        adapter.batch_update_words(
            zid_keep,
            [{"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "restored_fresh"}}],
        )
        assert _http_json(url, "/api/v1/admin/trash/restore", method="POST", body={"zid": zid_keep})[0] == 200
        st_restored, body_restored = _http_json(url, f"/session/status?zid={zid_keep}")
        assert st_restored == 200
        rows_map = body_restored["data"]["rows"]
        assert any(r.get("trans") == "restored_fresh" for r in rows_map.values())

        # 5. Purge evicts any cached entry and bumps generation fence for purged ZIDs.
        assert _http_json(url, "/api/v1/admin/sessions/delete", method="POST", body={"session_zid": zid_keep})[0] == 200
        with server.arbiter._lock:
            server.arbiter.sessions[zid_keep] = {"session_zid": zid_keep}
        with server.arbiter.enrichment_queue._lock:
            gen_before_purge = server.arbiter.enrichment_queue._progressive_generations.get(zid_keep, 0)
        st_purge, body_purge = _http_json(url, "/api/v1/admin/trash/purge", method="POST", body={})
        assert st_purge == 200 and body_purge["data"]["purged_sessions"] >= 1
        assert zid_keep not in server.arbiter.sessions
        with server.arbiter.enrichment_queue._lock:
            assert server.arbiter.enrichment_queue._progressive_generations.get(zid_keep, 0) > gen_before_purge


def test_save_preserves_fingerprint_conflict_eviction_and_committed_delivery_recovery(tmp_path, monkeypatch):
    """Save preserves ROW_STALE fingerprint conflicts, does not resurrect evicted sessions,
    and retains committed recovery metadata when event delivery fails after SQLite commit.
    """
    config, paths, arbiter, adapter, _ = _build_sqlite_arbiter(tmp_path)
    zid = "20261123050020"
    try:
        restored = _seed_sqlite_session(adapter, tmp_path, zid, word_dest="initial")
        _warm_cache_entry(arbiter, restored, zid)

        # 1. Stale fingerprint conflict rejected cleanly without modifying SQLite or cache.
        with pytest.raises(desk.StructuredError) as stale_err:
            arbiter.save_session(
                session_zid=zid,
                deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "bad"}],
                fingerprint="stale-wrong-fingerprint",
                language="de",
            )
        assert stale_err.value.error_code == desk.ErrorCode.ROW_STALE
        assert adapter.db.get_words_by_session(zid)[0]["word_destination"] == "initial"

        # 2. Cache eviction during save does not resurrect evicted session in arbiter.sessions.
        real_core_edit_save = controller.core_edit_save

        def evicting_save(*args, **kwargs):
            res = real_core_edit_save(*args, **kwargs)
            arbiter.invalidate_deleted_sessions([zid])
            return res

        monkeypatch.setattr(controller, "core_edit_save", evicting_save)
        res_evict = arbiter.save_session(
            session_zid=zid,
            deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "after_evict"}],
            language="de",
        )
        assert res_evict["status"] == "success"
        assert zid not in arbiter.sessions

        # 3. Delivery failure after SQLite commit preserves committed recovery details.
        monkeypatch.setattr(controller, "core_edit_save", real_core_edit_save)
        monkeypatch.setattr(arbiter, "emit_event", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("sse broken")))
        with pytest.raises(desk.StructuredError) as deliv_err:
            arbiter.save_session(
                session_zid=zid,
                deltas=[{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "WordDestination", "value": "committed_ok"}],
                language="de",
            )
        assert deliv_err.value.details.get("committed") is True
        assert deliv_err.value.details.get("session_zid") == zid
        assert deliv_err.value.details.get("fingerprint")
        assert adapter.db.get_words_by_session(zid)[0]["word_destination"] == "committed_ok"
    finally:
        _cleanup_arbiter(arbiter)


def test_bounded_idle_retention_preserves_active_workers_tombstones_and_claimed_drafts(tmp_path):
    """Configured size/TTL retention evicts idle recomputable entries only while preserving
    active sessions, pinned generation fences, and claimed drafts.
    """
    config, paths, arbiter, _, _ = _build_sqlite_arbiter(tmp_path)
    eq = arbiter.enrichment_queue
    try:
        # 1. SessionArbiter idle session eviction vs active progressive worker protection.
        arbiter._session_cache_max = 2
        arbiter._session_cache_idle_ttl = 100.0
        with arbiter._lock:
            for idx, zid in enumerate(("20261123050031", "20261123050032", "20261123050033")):
                arbiter.sessions[zid] = {"session_zid": zid, "lock": threading.Lock()}
                arbiter._session_last_used[zid] = 1000.0 + idx * 10.0
        active_fut = concurrent.futures.Future()
        with eq._lock:
            eq._active_progressive_sessions["20261123050031"] = active_fut
        arbiter.prune_idle_caches(now=1050.0)
        assert "20261123050031" in arbiter.sessions, "Active progressive worker session was evicted"
        assert "20261123050032" not in arbiter.sessions, "Oldest idle session was not evicted at size limit"
        assert "20261123050033" in arbiter.sessions

        # TTL expiry evicts remaining idle session while keeping active worker session.
        arbiter.prune_idle_caches(now=1200.0)
        assert "20261123050031" in arbiter.sessions
        assert "20261123050033" not in arbiter.sessions

        # 2. EnrichmentQueue memo LRU/TTL & generation tombstone pinning.
        eq._enrichment_cache_max = 2
        eq._enrichment_cache_ttl = 50.0
        eq._generation_fence_max = 2
        with eq._lock:
            eq._put_memo_locked(eq._cache, eq._cache_times, ("a", "de"), {"v": 1}, 2, 50.0, now=100.0)
            eq._put_memo_locked(eq._cache, eq._cache_times, ("b", "de"), {"v": 2}, 2, 50.0, now=110.0)
            eq._put_memo_locked(eq._cache, eq._cache_times, ("c", "de"), {"v": 3}, 2, 50.0, now=120.0)
            assert set(eq._cache.keys()) == {("b", "de"), ("c", "de")}
            eq._progressive_generations["20261123050031"] = 5
            eq._progressive_generations["idle_1"] = 1
            eq._progressive_generations["idle_2"] = 1
            eq._progressive_generations["idle_3"] = 1
            eq._prune_generations_locked()
            assert "20261123050031" in eq._progressive_generations, "Pinned active generation fence was dropped"
            assert len(eq._progressive_generations) == 2
            eq._active_progressive_sessions.pop("20261123050031", None)

        # 3. Draft retention evicts idle/expired drafts while preserving a claimed draft.
        z_claimed, z_idle1, z_idle2 = "20261123050041", "20261123050042", "20261123050043"
        try:
            controller._store_draft_session(z_claimed, {"text": "c"}, max_entries=1, ttl_seconds=60.0, now=100.0)
            draft, token, _, busy = controller._claim_draft_session(z_claimed, ttl_seconds=60.0, now=105.0)
            assert draft is not None and token is not None and not busy
            controller._store_draft_session(z_idle1, {"text": "i1"}, max_entries=1, ttl_seconds=60.0, now=110.0)
            controller._store_draft_session(z_idle2, {"text": "i2"}, max_entries=1, ttl_seconds=60.0, now=120.0)
            with controller._DRAFT_SESSIONS_LOCK:
                assert z_claimed in controller._DRAFT_SESSIONS, "Claimed draft was evicted"
                assert z_idle1 not in controller._DRAFT_SESSIONS, "Excess idle draft was not evicted"
                assert z_idle2 in controller._DRAFT_SESSIONS
            controller._complete_draft_session(z_claimed, token, draft)
        finally:
            for z in (z_claimed, z_idle1, z_idle2):
                controller._cancel_draft_session(z)
    finally:
        _cleanup_arbiter(arbiter)
