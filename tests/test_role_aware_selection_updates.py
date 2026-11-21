"""Acceptance and control tests for role-aware selection update publication.

Exercises real owned SQLite storage, real owned anki-mapping.ini role mappings,
real SessionArbiter / ControllerRequestHandler HTTP + SSE endpoints, real
ClientViewAdapter focus persistence, real independent SQLite connections, and
real Playwright browser clients without database mocks or arbitrary sleeps.
"""
import json
import queue
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import kardenwort_desk as desk
from kardenwort_client_views import ClientViewAdapter
from kardenwort_db import KardenwortConnection
from kardenwort_events import EventDispatcher
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    translation_service,
)


TOKEN = "test-controller-api-key"
ZID = "20261119085330"
OTHER_ZID = "20261119085331"
CLIENT_A = "a" * 32
CLIENT_B = "b" * 32


def _write_mapping(path: Path, selected_field: str = "DeskSelected") -> Path:
    mapping_path = path / f"anki-mapping-{selected_field}.ini"
    mapping_path.write_text(
        f"""[fields]
Quotation
WordSource
WordSourceInflectedForm
WordDestination
SentenceSourceIndex
SentenceSource
SentenceDestination
{selected_field}

[desk_columns]
Quotation=quotation
WordSource=lemma
WordSourceInflectedForm=inflected
WordDestination=word_translation
SentenceSource=sentence_source
SentenceDestination=sentence_destination
{selected_field}=selected

[desk_editable]
editable_columns=WordSource,WordDestination,WordSourceInflectedForm,{selected_field}
""",
        encoding="utf-8",
    )
    return mapping_path


def _http_json(url: str, path: str, *, method: str = "GET", body=None, token: str = TOKEN):
    data_bytes = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-API-Token": token}
    if data_bytes is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url + path, data=data_bytes, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        return resp.status, json.load(resp)


def _payload_data(payload: dict) -> dict:
    return payload.get("data", payload)


def _configure_world(owner: Path, server, selected_field: str):
    mapping_path = _write_mapping(owner, selected_field=selected_field)
    server.resolved_paths["anki_mapping_file"] = str(mapping_path)
    server.config.set("settings", "anki_mapping_file", str(mapping_path))
    server.config.set("storage", "backend", "sqlite")
    server.resolved_paths["storage_backend"] = "sqlite"
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    return adapter, mapping_path


def _seed_sessions(adapter: desk.SqliteStorageAdapter, selected_field: str):
    headers = [
        "TokenOrder",
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "SentenceDestination",
        selected_field,
    ]
    rows = [
        ["0", "Haus", "Haus", "Haus", "house", "1", "Das Haus.", "The house.", "0"],
        ["0", "Haus", "Haus", "Haus", "house-two", "2", "Haus und Baum.", "House and tree.", "0"],
        ["1", "Baum", "Baum", "Baum", "tree", "2", "Haus und Baum.", "House and tree.", "0"],
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="role-selection-test",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nHaus und Baum.",
        headers=headers,
        data_rows=rows,
        text_provenance="fixture:test",
    )
    other_rows = [
        ["0", "Stern", "Stern", "Stern", "star", "1", "Der Stern.", "The star.", "0"],
    ]
    adapter.save_session(
        session_zid=OTHER_ZID,
        slug="other-session",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Der Stern.",
        headers=headers,
        data_rows=other_rows,
        text_provenance="fixture:test",
    )
    return headers, rows


def _seed_distinct_client_focus(adapter: desk.SqliteStorageAdapter):
    views = ClientViewAdapter(adapter.db)
    focus_a = [dict(sentence_idx=1, token_order=0, visual_idx=1, atomic_id=0, row_ids=[0], lemma="Haus")]
    focus_b = [dict(sentence_idx=2, token_order=1, visual_idx=3, atomic_id=2, row_ids=[2], lemma="Baum")]
    views.set(ZID, CLIENT_A, 2, focus_a)
    views.set(ZID, CLIENT_B, 3, focus_b)
    return views, focus_a, focus_b


def _drain_update_event(sub_queue: queue.Queue, timeout: float = 3.0) -> dict:
    event = sub_queue.get(timeout=timeout)
    assert event["type"] == "update" and event["stage"] == "saved", event
    return event


@pytest.fixture
def role_world(tmp_path):
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend="sqlite") as (url, server):
            yield tmp_path, url, server


def test_custom_selected_role_publishes_shared_selection_and_cold_status(role_world):
    """Scenario: Custom selected role (MySelected=selected) publishes shared_selection and preserves focus."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field="MySelected")
    _seed_sessions(adapter, selected_field="MySelected")
    views, focus_a, focus_b = _seed_distinct_client_focus(adapter)

    dispatcher = EventDispatcher.get_instance()
    sub_1 = dispatcher.register(ZID)
    sub_2 = dispatcher.register(ZID)
    try:
        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {
                        "row_id": 0,
                        "sentence_idx": 1,
                        "token_order": 0,
                        "column": "MySelected",
                        "value": "1",
                    }
                ],
            },
        )
        assert status == 200, res
        payload = _payload_data(res)
        assert payload["status"] == "success"

        # SQLite persisted the selection for all occurrences of 'Haus' and kept OTHER_ZID untouched.
        words = adapter.db.get_words_by_session(ZID)
        assert [(w["sentence_index"], w["token_order"], w["lemma"], w["selected"]) for w in words] == [
            (1, 0, "Haus", 1),
            (2, 0, "Haus", 1),
            (2, 1, "Baum", 0),
        ]
        assert adapter.get_shared_selection(OTHER_ZID) == {"stern": False}

        # Both live subscribers must receive the committed shared_selection snapshot with matching fingerprint.
        ev_1 = _drain_update_event(sub_1)
        ev_2 = _drain_update_event(sub_2)
        expected_membership = {"haus": True, "baum": False}
        assert ev_1["shared_selection"] == expected_membership
        assert ev_2["shared_selection"] == expected_membership
        assert ev_1["fingerprint"] == payload["fingerprint"]
        assert ev_2["fingerprint"] == payload["fingerprint"]

        # Client-scoped focus remains isolated and untouched.
        assert views.get(ZID, CLIENT_A)["active_tokens"] == focus_a
        assert views.get(ZID, CLIENT_B)["active_tokens"] == focus_b

        # Cold status and cold read-only adapter agree with the committed save.
        server.arbiter.sessions.clear()
        st_status, cold_status = _http_json(url, f"/session/status?zid={ZID}")
        assert st_status == 200, cold_status
        st_rec, committed_status = _http_json(url, f"/session/status?zid={ZID}&committed=1")
        assert st_rec == 200, committed_status
        assert _payload_data(committed_status)["fingerprint"] == payload["fingerprint"]
        cold_adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths, read_only=True)
        assert cold_adapter.get_shared_selection(ZID) == expected_membership
    finally:
        dispatcher.unregister(ZID, sub_1)
        dispatcher.unregister(ZID, sub_2)


@pytest.mark.parametrize("selected_field", ["DeskSelected", "MySelected"])
def test_supported_selected_alias_publishes_shared_selection(role_world, selected_field):
    """Scenario: Supported alias ('selected') produces the same shared membership notification."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field=selected_field)
    _seed_sessions(adapter, selected_field=selected_field)
    views, focus_a, focus_b = _seed_distinct_client_focus(adapter)

    dispatcher = EventDispatcher.get_instance()
    sub = dispatcher.register(ZID)
    try:
        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {
                        "row_id": 0,
                        "sentence_idx": 1,
                        "token_order": 0,
                        "column": "selected",
                        "value": "1",
                    }
                ],
            },
        )
        assert status == 200, res
        payload = _payload_data(res)
        assert adapter.get_shared_selection(ZID) == {"haus": True, "baum": False}
        ev = _drain_update_event(sub)
        assert ev["shared_selection"] == {"haus": True, "baum": False}
        assert ev["fingerprint"] == payload["fingerprint"]
        assert views.get(ZID, CLIENT_A)["active_tokens"] == focus_a
        assert views.get(ZID, CLIENT_B)["active_tokens"] == focus_b
    finally:
        dispatcher.unregister(ZID, sub)


def test_adjacent_control_desk_selected_publishes_shared_selection(role_world):
    """Valid adjacent control: standard DeskSelected role publishes shared_selection and preserves focus."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field="DeskSelected")
    _seed_sessions(adapter, selected_field="DeskSelected")
    views, focus_a, focus_b = _seed_distinct_client_focus(adapter)

    dispatcher = EventDispatcher.get_instance()
    sub = dispatcher.register(ZID)
    try:
        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {
                        "row_id": 0,
                        "sentence_idx": 1,
                        "token_order": 0,
                        "column": "DeskSelected",
                        "value": "1",
                    }
                ],
            },
        )
        assert status == 200, res
        payload = _payload_data(res)
        ev = _drain_update_event(sub)
        assert ev["shared_selection"] == {"haus": True, "baum": False}
        assert ev["fingerprint"] == payload["fingerprint"]
        assert views.get(ZID, CLIENT_A)["active_tokens"] == focus_a
        assert views.get(ZID, CLIENT_B)["active_tokens"] == focus_b
    finally:
        dispatcher.unregister(ZID, sub)


def test_non_selection_translation_edit_preserves_membership_and_focus(role_world):
    """Scenario: Non-selection edit commits translation without synthetic shared_selection or focus mutation."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field="MySelected")
    _seed_sessions(adapter, selected_field="MySelected")
    views, focus_a, focus_b = _seed_distinct_client_focus(adapter)

    dispatcher = EventDispatcher.get_instance()
    sub = dispatcher.register(ZID)
    try:
        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {
                        "row_id": 0,
                        "sentence_idx": 1,
                        "token_order": 0,
                        "column": "WordDestination",
                        "value": "cottage",
                    }
                ],
            },
        )
        assert status == 200, res
        payload = _payload_data(res)
        words = adapter.db.get_words_by_session(ZID)
        assert [(w["sentence_index"], w["token_order"], w["word_destination"], w["selected"]) for w in words] == [
            (1, 0, "cottage", 0),
            (2, 0, "house-two", 0),
            (2, 1, "tree", 0),
        ]
        ev = _drain_update_event(sub)
        assert ev["shared_selection"] is None
        assert ev["fingerprint"] == payload["fingerprint"]
        assert views.get(ZID, CLIENT_A)["active_tokens"] == focus_a
        assert views.get(ZID, CLIENT_B)["active_tokens"] == focus_b

        # Exact-occurrence validation remains strict for non-selection edits.
        bad_status, bad_res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [{"row_id": 0, "column": "WordDestination", "value": "missing-coords"}],
            },
        )
        assert bad_status >= 400, bad_res
        assert sub.empty()
    finally:
        dispatcher.unregister(ZID, sub)


@pytest.mark.parametrize("selected_field", ["DeskSelected", "MySelected"])
def test_independent_sqlite_writer_between_commit_and_delivery_preserves_save_snapshot(
    role_world, monkeypatch, selected_field
):
    """Scenario 4 & Task 4.1: Independent SQLite writer between save commit and delivery cannot mix snapshots.

    Uses deterministic threading.Event coordination around the real SQLite write commit
    and a real independent sqlite3.connect writer (zero sleeps, zero database mocks).
    """
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field=selected_field)
    _seed_sessions(adapter, selected_field=selected_field)
    views, focus_a, focus_b = _seed_distinct_client_focus(adapter)

    # Pre-compute the exact authoritative fingerprint of Save 1's committed state (Haus=1, Baum=0).
    before_restored = adapter.restore_session(ZID)
    headers = before_restored["headers"]
    lemma_idx = headers.index("WordSource")
    sel_idx = headers.index(selected_field)
    expected_save1_rows = [list(row) for row in before_restored["data_rows"]]
    for row in expected_save1_rows:
        row[sel_idx] = "1" if row[lemma_idx].strip().lower() == "haus" else "0"
    expected_save1_fp = desk.compute_content_fingerprint(expected_save1_rows)

    db_path = Path(adapter.db.db_path)
    save1_committed = threading.Event()
    writer_done = threading.Event()
    writer_error = []
    hook_state = {"armed": True}
    original_exit = KardenwortConnection.__exit__

    def _haus_is_committed() -> bool:
        probe = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = probe.execute(
                "SELECT selected FROM words WHERE session_zid = ? AND sentence_index = 1 AND token_order = 0;",
                (ZID,),
            ).fetchone()
            return bool(row and row[0] == 1)
        finally:
            probe.close()

    def independent_writer():
        try:
            assert save1_committed.wait(timeout=5.0), "Timed out waiting for Save 1 SQLite commit"
            ext_conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
            try:
                ext_conn.execute("BEGIN IMMEDIATE;")
                before_rows = ext_conn.execute(
                    "SELECT lemma, selected FROM words WHERE session_zid = ? ORDER BY sentence_index, token_order;",
                    (ZID,),
                ).fetchall()
                assert before_rows == [("Haus", 1), ("Haus", 1), ("Baum", 0)], before_rows
                ext_conn.execute(
                    "UPDATE words SET selected = 1 WHERE session_zid = ? AND lemma = 'Baum';",
                    (ZID,),
                )
                ext_conn.execute("COMMIT;")
            finally:
                ext_conn.close()
        except BaseException as exc:  # pragma: no cover - surfaced via assertion below
            writer_error.append(exc)
        finally:
            writer_done.set()

    def observed_exit(self_conn, exc_type, exc_val, exc_tb):
        was_write = exc_type is None and not getattr(self_conn, "read_only", False)
        result = original_exit(self_conn, exc_type, exc_val, exc_tb)
        if was_write and hook_state["armed"] and _haus_is_committed():
            hook_state["armed"] = False
            # Real SQLite COMMIT of Save 1 has just completed (Haus=1 is visible to independent
            # connections); now interleave the independent writer BEFORE event delivery.
            save1_committed.set()
            assert writer_done.wait(timeout=5.0), "Timed out waiting for independent SQLite writer"
        return result

    writer_thread = threading.Thread(target=independent_writer, daemon=True)
    dispatcher = EventDispatcher.get_instance()
    sub = dispatcher.register(ZID)
    try:
        writer_thread.start()
        with monkeypatch.context() as ctx:
            ctx.setattr(KardenwortConnection, "__exit__", observed_exit)
            status, res = _http_json(
                url,
                "/session/save",
                method="POST",
                body={
                    "session_zid": ZID,
                    "language": "de",
                    "deltas": [
                        {
                            "row_id": 0,
                            "sentence_idx": 1,
                            "token_order": 0,
                            "column": selected_field,
                            "value": "1",
                        }
                    ],
                },
            )
        writer_thread.join(timeout=5.0)
        assert not writer_error, f"Independent SQLite writer failed: {writer_error[0]!r}"
        assert save1_committed.is_set() and writer_done.is_set()
        assert status == 200, res
        payload = _payload_data(res)

        ev = _drain_update_event(sub)
        # Save 1's ack and event MUST carry coherent membership and fingerprint from Save 1's transaction.
        assert payload["fingerprint"] == expected_save1_fp
        assert ev["fingerprint"] == expected_save1_fp
        assert ev["shared_selection"] == {"haus": True, "baum": False}

        # Meanwhile SQLite and committed recovery reflect the independent writer's later state under its own revision.
        assert adapter.get_shared_selection(ZID) == {"haus": True, "baum": True}
        st_rec, recovered = _http_json(url, f"/session/status?zid={ZID}&committed=1")
        assert st_rec == 200, recovered
        assert _payload_data(recovered)["fingerprint"] != expected_save1_fp

        # Client focus remains untouched.
        assert views.get(ZID, CLIENT_A)["active_tokens"] == focus_a
        assert views.get(ZID, CLIENT_B)["active_tokens"] == focus_b
    finally:
        dispatcher.unregister(ZID, sub)


@pytest.mark.parametrize("selected_field", ["DeskSelected", "MySelected"])
def test_blank_lemma_sibling_row_does_not_crash_selection_event_construction(role_world, selected_field):
    """Decision 2 / Task 2.2: A blank-lemma sibling row does not crash selection save or event construction."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field=selected_field)
    _seed_sessions(adapter, selected_field=selected_field)

    # Insert a sibling row with blank lemma in the real SQLite database (words.lemma is NOT NULL).
    with adapter.db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO words (session_zid, sentence_index, token_order, quotation, inflected_form, lemma, word_destination, selected)
            VALUES (?, 2, 2, '—', '—', '', '', 0);
            """,
            (ZID,),
        )

    dispatcher = EventDispatcher.get_instance()
    sub = dispatcher.register(ZID)
    try:
        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {
                        "row_id": 0,
                        "sentence_idx": 1,
                        "token_order": 0,
                        "column": selected_field,
                        "value": "1",
                    }
                ],
            },
        )
        assert status == 200, res
        payload = _payload_data(res)
        ev = _drain_update_event(sub)
        assert ev["fingerprint"] == payload["fingerprint"]
        assert ev["shared_selection"] == {"haus": True, "baum": False}
    finally:
        dispatcher.unregister(ZID, sub)


def test_two_live_browser_clients_sync_custom_selected_role_without_focus_mutation(role_world, page):
    """Decision 3: Two live browser clients synchronize MySelected updates without changing each other's focus."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field="MySelected")
    _seed_sessions(adapter, selected_field="MySelected")

    page.context.set_extra_http_headers({"X-API-Token": TOKEN})
    other = page.context.new_page()
    try:
        for client, focus_row in ((page, 0), (other, 2)):
            client.set_default_timeout(6000)
            client.goto(f"{url}/?session_zid={ZID}&seq_num=1")
            client.wait_for_function("typeof window.SSEBridge !== 'undefined' && typeof window.AppState !== 'undefined'")
            client.evaluate(
                """() => {
                    window.__savedEvents = [];
                    const origApply = window.AppState.applyDeltas;
                    window.AppState.applyDeltas = function(data) {
                        if (data && data.type === 'update' && data.stage === 'saved') {
                            window.__savedEvents.push(data);
                        }
                        return origApply.call(this, data);
                    };
                }"""
            )
            client.evaluate(
                "args => SSEBridge.connect(args.zid, args.url, args.token)",
                {"zid": ZID, "url": url, "token": TOKEN},
            )
            client.wait_for_function("document.querySelector('#sse-status-indicator[data-sync=synced]') !== null")
            client.evaluate(
                "idx => { window.AppState.activeTokenSelections = [{rowId: idx, sentenceIdx: idx === 0 ? 1 : 2, tokenOrder: idx === 0 ? 0 : 1}]; }",
                focus_row,
            )

        focus_before_1 = page.evaluate("window.AppState.activeTokenSelections")
        focus_before_2 = other.evaluate("window.AppState.activeTokenSelections")
        assert focus_before_1 != focus_before_2

        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {
                        "row_id": 0,
                        "sentence_idx": 1,
                        "token_order": 0,
                        "column": "MySelected",
                        "value": "1",
                    }
                ],
            },
        )
        assert status == 200, res

        for client, expected_focus in ((page, focus_before_1), (other, focus_before_2)):
            client.wait_for_function("window.__savedEvents.length >= 1")
            saved_event = client.evaluate("window.__savedEvents[0]")
            assert saved_event["shared_selection"] == {"haus": True, "baum": False}
            assert client.evaluate("window.AppState.selectedLemmas.haus === true") is True
            assert client.evaluate("!!window.AppState.selectedLemmas.baum") is False
            assert client.evaluate("window.AppState.activeTokenSelections") == expected_focus

        # Cold status after clearing in-memory session cache matches committed state.
        server.arbiter.sessions.clear()
        st_rec, committed_status = _http_json(url, f"/session/status?zid={ZID}&committed=1")
        assert st_rec == 200, committed_status
        assert _payload_data(committed_status)["fingerprint"] == _payload_data(res)["fingerprint"]
    finally:
        other.close()


def test_build_committed_shared_selection_guards_null_and_blank_lemmas():
    """Pure functional core: null/blank lemmas never crash and multi-occurrence selection is OR-ed."""
    words = [
        {"lemma": None, "selected": 1},
        {"lemma": "   ", "selected": 1},
        {"lemma": " Haus ", "selected": 0},
        {"lemma": "haus", "selected": 1},
        {"lemma": "Baum", "selected": 0},
    ]
    assert desk.build_committed_shared_selection(words) == {"haus": True, "baum": False}


def test_failed_selection_save_rolls_back_and_emits_no_event(role_world):
    """Publish only after commit: a rolled-back save emits zero events and leaves SQLite untouched."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field="MySelected")
    _seed_sessions(adapter, selected_field="MySelected")

    dispatcher = EventDispatcher.get_instance()
    sub = dispatcher.register(ZID)
    try:
        status, res = _http_json(
            url,
            "/session/save",
            method="POST",
            body={
                "session_zid": ZID,
                "language": "de",
                "deltas": [
                    {"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "MySelected", "value": "1"},
                    {"row_id": 99, "sentence_idx": 99, "token_order": 99, "column": "MySelected", "value": "1"},
                ],
            },
        )
        assert status >= 400, res
        assert adapter.get_shared_selection(ZID) == {"haus": False, "baum": False}
        assert sub.empty()
    finally:
        dispatcher.unregister(ZID, sub)


def test_committed_delivery_barrier_preserved_for_custom_selected_role(role_world, monkeypatch):
    """Preserves committed_delivery structured recovery when event delivery fails after MySelected commit."""
    tmp_path, url, server = role_world
    adapter, _ = _configure_world(tmp_path, server, selected_field="MySelected")
    _seed_sessions(adapter, selected_field="MySelected")

    def broken_delivery(*args, **kwargs):
        raise ConnectionError("simulated delivery failure")

    monkeypatch.setattr(server.arbiter, "emit_event", broken_delivery)
    status, res = _http_json(
        url,
        "/session/save",
        method="POST",
        body={
            "session_zid": ZID,
            "language": "de",
            "deltas": [
                {"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": "MySelected", "value": "1"},
            ],
        },
    )
    assert status >= 400, res
    details = res["details"]
    assert details["committed"] is True
    assert details["session_zid"] == ZID
    assert details["fingerprint"]
    assert details["recovery_url"] == f"/session/status?zid={ZID}&committed=1"
    assert adapter.get_shared_selection(ZID) == {"haus": True, "baum": False}

    st_rec, recovered = _http_json(url, details["recovery_url"])
    assert st_rec == 200, recovered
    assert _payload_data(recovered)["fingerprint"] == details["fingerprint"]
