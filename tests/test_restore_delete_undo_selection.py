"""Acceptance and control tests for restoring selection and focus after Delete and Undo.

Exercises real owned SQLite storage, real ControllerRequestHandler HTTP/SSE endpoints,
real ClientViewAdapter focus persistence, real rendered Desk documents, and real
Playwright browser clients without database mocks or fixed sleeps.
"""
import json

import pytest

import kardenwort_desk as desk
from kardenwort_state_http import config_snapshot
from kardenwort_state_runtime import preference_store
from tests.render_test_support import seed_render_rows
from tests.sqlite_document_support import island, replace_island, wait_state
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_same_lemma_toggle import _assert_source_roles, _source_words


ZID = "20261119085332"


def _render_sqlite_workspace_html(text, rows, *, zid=ZID, theme="dark", text_mode="multi"):
    config, paths, _, _ = desk.load_config()
    config.read_dict({
        "sentences_mode": {"enabled": "true", "delivery_mode": "container"},
        "settings": {"unify_article_pronoun_lemmas": "false", "deduplicate_pos_aware": "true"},
        "rendering": {"auto_save_on_edit": "false", "filter_selected_only": "false"},
    })
    preference_store(config, paths).set("highlight_same_lemma", False)
    headers = [
        "Quotation",
        "WordSource",
        "WordSourcePOS",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "SentenceDestination",
        "DeskSelected",
    ]
    sentences = text.split("\n")
    data = [
        [quotation, lemma, pos, meaning, str(sentence), sentences[sentence - 1], "Translation.", selected]
        for quotation, lemma, pos, meaning, sentence, selected in rows
    ]
    seed_render_rows(config, paths, zid, text, "de", headers, data, text_mode=text_mode)
    html = desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode=text_mode,
        config=config,
        resolved_paths=paths,
        theme=theme,
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
    )
    return html, {"headers": headers, "data_rows": data}


def _mount_workspace(page, world, html, stored, *, text_mode="multi"):
    url, server, _ = world
    store = preference_store(server.config, server.resolved_paths)
    store.set("auto_save_on_close", False)
    store.set("highlight_same_lemma", False)

    boot = island(html, "kardenwort-bootstrap")
    session = boot.get("session_data")
    if session is None:
        session = dict(boot, headers=stored["headers"], data_rows=stored["data_rows"])
    boot["session_data"] = session
    zid = session.get("session_zid") or session["zid"]

    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    adapter.save_session(
        session_zid=zid,
        source_language=session.get("language", "de"),
        target_language=session.get("target_lang", "en"),
        source_raw_text=session.get("source_text", ""),
        text_mode=text_mode,
        headers=session["headers"],
        data_rows=session["data_rows"],
    )

    session.update(server_url=url, api_token=server.api_key, server_enabled=True)
    boot.update(server_url=url, server_enabled=True)
    boot["ui_config"].update(config_snapshot(server, {})["ui"])
    boot["ui_config"]["auto_save_on_edit"] = False
    html = replace_island(html, "session-data", session)
    html = replace_island(html, "ui-config", boot["ui_config"])
    html = replace_island(html, "kardenwort-bootstrap", boot)

    page.context.set_extra_http_headers({"X-API-Token": server.api_key})

    def document(route):
        boot["ui_config"].update(config_snapshot(server, {})["ui"])
        boot["ui_config"]["auto_save_on_edit"] = False
        current = replace_island(html, "ui-config", boot["ui_config"])
        current = replace_island(current, "kardenwort-bootstrap", boot)
        route.fulfill(content_type="text/html", body=current)

    page.route(url + "/", document)
    page.goto(url + "/")
    wait_state(page)
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")
    return adapter


def _flush_saves(page):
    page.evaluate(
        """() => {
            window.__deleteUndoFlush = null;
            window.flushPendingSaves().then(
                () => { window.__deleteUndoFlush = 'saved'; },
                err => { window.__deleteUndoFlush = 'error:' + err.message; }
            );
        }"""
    )
    page.wait_for_function("window.__deleteUndoFlush !== null", timeout=6000)
    assert page.evaluate("window.__deleteUndoFlush") == "saved"


def _delete_deltas(page):
    return [d for d in json.loads(page.evaluate("window.getDeltas()")) if d.get("column") == "_delete"]


def _selection_deltas(page):
    return [d for d in json.loads(page.evaluate("window.getDeltas()")) if d.get("column") == "DeskSelected"]


def _start_projection_observer(page, row_selector='#lemma-table tbody tr[data-row-id="0"]'):
    page.evaluate(
        """selector => {
            window.__rowSelectedMutations = [];
            window.__sourceHighlightMutations = [];
            const row = document.querySelector(selector);
            const source = document.getElementById('source-container');
            window.__rowObserver = new MutationObserver(records => {
                records.forEach(rec => {
                    if (rec.attributeName === 'data-selected') {
                        window.__rowSelectedMutations.push(rec.target.getAttribute('data-selected'));
                    }
                });
            });
            if (row) {
                window.__rowObserver.observe(row, {attributes: true, attributeFilter: ['data-selected']});
            }
            window.__sourceObserver = new MutationObserver(records => {
                records.forEach(rec => {
                    if (rec.attributeName === 'class') {
                        window.__sourceHighlightMutations.push(rec.target.className);
                    }
                });
            });
            if (source) {
                window.__sourceObserver.observe(source, {subtree: true, attributes: true, attributeFilter: ['class']});
            }
        }""",
        row_selector,
    )


def _stop_projection_observer(page):
    return page.evaluate(
        """() => {
            if (window.__rowObserver) window.__rowObserver.disconnect();
            if (window.__sourceObserver) window.__sourceObserver.disconnect();
            return {
                rowSelected: window.__rowSelectedMutations || [],
                sourceClasses: window.__sourceHighlightMutations || [],
            };
        }"""
    )


def test_repeated_word_merged_delete_undo_restores_both_rows_selection_and_focus(
    page, preference_world
):
    """Delta Scenario: Repeated-word undo (uncommitted merged same-lemma selection [0,1] -> [] -> [0,1])."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus.\nHaus Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Haus", "Haus", "noun", "house", 2, "0"),
            ("Baum", "Baum", "noun", "tree", 2, "0"),
        ],
        text_mode="multi",
    )
    adapter = _mount_workspace(page, preference_world, html, stored, text_mode="multi")
    words_before = adapter.db.get_words_by_session(ZID)

    merged_row = page.locator('#lemma-table tbody tr[data-row-id="0"][data-all-row-ids="0,1"]')
    assert merged_row.count() == 1

    # Select the merged repeated-word row (before selection autosave commits it).
    merged_row.click()
    before_sel = sorted(page.evaluate("getSelectedRowsArray()"))
    assert before_sel == [0, 1]
    assert page.evaluate("!!AppState.selectedLemmas.haus") is True
    assert page.evaluate("getFocusedRowId()") == 0
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline"])

    # Delete the selected repeated-word row.
    page.evaluate("window.deleteSelectedRows()")
    during_sel = sorted(page.evaluate("getSelectedRowsArray()"))
    during_deleted_attr = merged_row.get_attribute("data-deleted")
    during_selected_attr = merged_row.get_attribute("data-selected")
    during_focus = page.evaluate("getFocusedRowId()")
    during_delete_deltas = _delete_deltas(page)
    during_sel_deltas = _selection_deltas(page)
    assert adapter.db.get_words_by_session(ZID) == words_before

    # Undo must restore both row IDs [0, 1], shared lemma membership, client focus, and source highlight once.
    _start_projection_observer(page, '#lemma-table tbody tr[data-row-id="0"]')
    page.keyboard.press("Control+z")
    observed = _stop_projection_observer(page)
    after_sel = sorted(page.evaluate("getSelectedRowsArray()"))

    # Exact reproduced sequence: [0, 1] -> [] -> [0, 1] (currently fails as [0, 1] -> [] -> [0]).
    assert [before_sel, during_sel, after_sel] == [[0, 1], [], [0, 1]]
    assert during_deleted_attr == "true"
    assert during_selected_attr == "0"
    assert during_focus is None
    assert {(d["sentence_idx"], d["token_order"]) for d in during_delete_deltas} == {(1, 0), (2, 1)}
    assert during_sel_deltas == []

    assert page.evaluate("!!AppState.selectedLemmas.haus") is True
    assert merged_row.get_attribute("data-deleted") is None
    assert merged_row.get_attribute("data-selected") == "1"
    assert page.evaluate("getFocusedRowId()") == 0
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline"])
    assert _delete_deltas(page) == []
    assert observed["rowSelected"] == ["1"]
    assert adapter.db.get_words_by_session(ZID) == words_before
    assert config_path.read_bytes() == initial_config


def test_repeated_word_merged_committed_delete_undo_restores_selection_and_focus(
    page, preference_world
):
    """Delta Scenario: Repeated-word undo with committed merged same-lemma selection."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus.\nHaus Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Haus", "Haus", "noun", "house", 2, "0"),
            ("Baum", "Baum", "noun", "tree", 2, "0"),
        ],
        text_mode="multi",
    )
    adapter = _mount_workspace(page, preference_world, html, stored, text_mode="multi")
    merged_row = page.locator('#lemma-table tbody tr[data-row-id="0"][data-all-row-ids="0,1"]')
    assert merged_row.count() == 1

    merged_row.click()
    _flush_saves(page)
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 1, 0]
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    assert page.evaluate("isDirty()") is False

    page.locator("#kw-btn-delete").click()
    assert page.evaluate("getSelectedRowsArray()") == []
    assert merged_row.get_attribute("data-deleted") == "true"
    assert merged_row.get_attribute("data-selected") == "0"
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline"])
    assert {(d["sentence_idx"], d["token_order"]) for d in _delete_deltas(page)} == {(1, 0), (2, 1)}
    assert _selection_deltas(page) == []

    _start_projection_observer(page, '#lemma-table tbody tr[data-row-id="0"]')
    page.keyboard.press("Control+z")
    observed = _stop_projection_observer(page)

    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    assert page.evaluate("!!AppState.selectedLemmas.haus") is True
    assert merged_row.get_attribute("data-deleted") is None
    assert merged_row.get_attribute("data-selected") == "1"
    assert page.evaluate("getFocusedRowId()") == 0
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline"])
    assert page.evaluate("isDirty()") is False
    assert _delete_deltas(page) == []
    assert _selection_deltas(page) == []
    assert observed["rowSelected"] == ["1"]
    assert config_path.read_bytes() == initial_config


def test_repeated_word_separate_rows_delete_undo_restores_both_rows_and_token_focus(
    page, preference_world
):
    """Delta Scenario: Repeated-word undo with two separate same-lemma table rows and token focus."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus Haus Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Baum", "Baum", "noun", "tree", 1, "0"),
        ],
        text_mode="single",
    )
    adapter = _mount_workspace(page, preference_world, html, stored, text_mode="single")
    row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    row1 = page.locator('#lemma-table tbody tr[data-row-id="1"]')
    row2 = page.locator('#lemma-table tbody tr[data-row-id="2"]')
    assert row0.count() == 1 and row1.count() == 1 and row2.count() == 1

    # Click the first source occurrence to establish exact token-scoped focus + shared lemma selection.
    with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as view_set:
        _source_words(page, ["haus"]).nth(0).click()
    assert view_set.value.ok
    _flush_saves(page)

    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 1, 0]
    tokens_before = page.evaluate("AppState.activeTokenSelections")
    assert len(tokens_before) == 1
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline"])

    # Delete both same-lemma rows.
    with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as view_clear:
        page.locator("#kw-btn-delete").click()
    assert view_clear.value.ok

    assert page.evaluate("getSelectedRowsArray()") == []
    assert row0.get_attribute("data-deleted") == "true"
    assert row1.get_attribute("data-deleted") == "true"
    assert row0.get_attribute("data-selected") == "0"
    assert row1.get_attribute("data-selected") == "0"
    assert row2.get_attribute("data-deleted") is None
    assert page.evaluate("AppState.activeTokenSelections") == []
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline"])
    assert {(d["sentence_idx"], d["token_order"]) for d in _delete_deltas(page)} == {(1, 0), (1, 1)}
    assert _selection_deltas(page) == []

    # Undo must restore both rows, selection, exact token focus, and source highlights.
    with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as view_restore:
        page.keyboard.press("Control+z")
    assert view_restore.value.ok

    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    assert row0.get_attribute("data-deleted") is None
    assert row1.get_attribute("data-deleted") is None
    assert row0.get_attribute("data-selected") == "1"
    assert row1.get_attribute("data-selected") == "1"
    assert page.evaluate("!!AppState.selectedLemmas.haus") is True
    assert page.evaluate("AppState.activeTokenSelections") == tokens_before
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline"])
    assert page.evaluate("isDirty()") is False
    assert _delete_deltas(page) == []
    assert config_path.read_bytes() == initial_config


@pytest.mark.parametrize(
    "case,expected_error",
    [
        ("missing-selection-identity", "Missing deletion selection identity"),
        ("ambiguous-selection-identity", "Ambiguous deletion selection identity"),
    ],
)
def test_rejected_delete_leaves_history_intent_selection_and_dom_unchanged(
    page, preference_world, case, expected_error
):
    """Delta Scenario: Rejected deletion leaves history, pending intent, selection, and DOM unchanged."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus.\nBaum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Baum", "Baum", "noun", "tree", 2, "0"),
        ],
        text_mode="multi",
    )
    adapter = _mount_workspace(page, preference_world, html, stored, text_mode="multi")
    row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    baum_cell = page.locator('#lemma-table tbody tr[data-row-id="1"] td[data-col="WordDestination"]')
    row0.click()
    _flush_saves(page)

    # Record a valid prior history entry so we can verify rejected delete does not corrupt historyStack.
    baum_cell.dblclick()
    editor = baum_cell.locator("input")
    editor.fill("oak")
    editor.press("Enter")
    assert baum_cell.locator(".scrollable-cell").inner_text() == "oak"

    bundle_before = adapter.db.get_session_bundle(ZID)
    assert page.evaluate("getSelectedRowsArray()") == [0]
    assert page.evaluate("getFocusedRowId()") == 0
    assert _delete_deltas(page) == []
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline"])

    result = page.evaluate(
        """caseName => {
            const card = window.WorkspaceTabs.getActiveCard();
            const savedWords = card.words.slice();
            if (caseName === 'missing-selection-identity') {
                card.words = savedWords.filter(w => String(w.row_id) !== '0');
            } else {
                const first = Object.assign({}, savedWords[0], {selected: '1'});
                const conflicting = Object.assign({}, savedWords[0], {selected: '0'});
                card.words = [first, conflicting].concat(savedWords.slice(1));
            }
            let outcome = 'accepted';
            try {
                window.deleteSelectedRows();
            } catch (err) {
                outcome = err.message;
            } finally {
                card.words = savedWords;
            }
            return outcome;
        }""",
        case,
    )
    assert result == expected_error

    # Rebuilding deltas from acknowledgedValues must reveal ZERO leaked _delete intent.
    page.evaluate("window.projectPendingEdits()")
    assert _delete_deltas(page) == []
    assert _selection_deltas(page) == []
    assert row0.get_attribute("data-deleted") is None
    assert row0.is_visible()
    assert page.evaluate("getSelectedRowsArray()") == [0]
    assert page.evaluate("!!AppState.selectedLemmas.haus") is True
    assert page.evaluate("getFocusedRowId()") == 0
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline"])

    # History stack must NOT contain the rejected delete: one Undo must revert the prior Baum edit directly.
    page.evaluate("window.undo()")
    assert baum_cell.locator(".scrollable-cell").inner_text() == "tree"
    assert page.evaluate("isDirty()") is False
    assert _delete_deltas(page) == []
    assert row0.get_attribute("data-deleted") is None
    assert row0.is_visible()
    assert page.evaluate("getSelectedRowsArray()") == [0]

    # Redo must re-apply only the Baum edit, never a rejected delete.
    page.evaluate("window.redo()")
    assert baum_cell.locator(".scrollable-cell").inner_text() == "oak"
    assert _delete_deltas(page) == []
    assert row0.get_attribute("data-deleted") is None
    assert row0.is_visible()
    assert adapter.db.get_session_bundle(ZID) == bundle_before
    assert config_path.read_bytes() == initial_config


def test_redo_and_save_persists_exact_deleted_occurrences_once_and_preserves_unrelated_rows(
    page, preference_world
):
    """Delta Scenario: Redo and save persists exact deleted occurrences once and leaves unrelated rows unchanged."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus.\nHaus Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Haus", "Haus", "noun", "house", 2, "0"),
            ("Baum", "Baum", "noun", "tree", 2, "0"),
        ],
        text_mode="multi",
    )
    adapter = _mount_workspace(page, preference_world, html, stored, text_mode="multi")
    merged_row = page.locator('#lemma-table tbody tr[data-row-id="0"][data-all-row-ids="0,1"]')
    assert merged_row.count() == 1

    merged_row.click()
    _flush_saves(page)
    words_selected = adapter.db.get_words_by_session(ZID)
    unrelated_before = [w for w in words_selected if w["lemma"] == "Baum"]
    assert len(unrelated_before) == 1

    # Delete -> Undo -> verify coherent restoration -> Redo.
    page.locator("#kw-btn-delete").click()
    page.keyboard.press("Control+z")
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    assert page.evaluate("isDirty()") is False

    _start_projection_observer(page, '#lemma-table tbody tr[data-row-id="0"]')
    page.keyboard.press("Control+y")
    observed = _stop_projection_observer(page)

    assert page.evaluate("getSelectedRowsArray()") == []
    assert merged_row.get_attribute("data-deleted") == "true"
    assert merged_row.get_attribute("data-selected") == "0"
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline"])
    assert observed["rowSelected"] == ["0"]

    pending_deletes = _delete_deltas(page)
    assert len(pending_deletes) == 2
    assert {(d["sentence_idx"], d["token_order"]) for d in pending_deletes} == {(1, 0), (2, 1)}
    assert all(d["value"] is True for d in pending_deletes)

    # Save persists the exact two deleted occurrences once and leaves unrelated Baum untouched.
    with page.expect_response("**/session/save") as save_resp:
        page.locator("#kw-btn-save").click()
    assert save_resp.value.ok, save_resp.value.text()
    _flush_saves(page)

    remaining = adapter.db.get_words_by_session(ZID)
    assert remaining == unrelated_before
    assert page.evaluate("isDirty()") is False

    # Undo after Save + Save restores exact records in SQLite.
    page.keyboard.press("Control+z")
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    with page.expect_response("**/session/save") as restore_resp:
        page.locator("#kw-btn-save").click()
    assert restore_resp.value.ok, restore_resp.value.text()
    _flush_saves(page)

    assert adapter.db.get_words_by_session(ZID) == words_selected
    assert config_path.read_bytes() == initial_config


def test_single_row_delete_undo_and_missing_occurrence_rejection_adjacent_control(
    page, preference_world
):
    """Valid adjacent control: single non-repeated row Delete/Undo/Save and missing occurrence rejection."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus.\nBaum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Baum", "Baum", "noun", "tree", 2, "0"),
        ],
        text_mode="multi",
    )
    adapter = _mount_workspace(page, preference_world, html, stored, text_mode="multi")
    row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    row0.click()
    _flush_saves(page)
    selected_words = adapter.db.get_words_by_session(ZID)
    assert [w["selected"] for w in selected_words] == [1, 0]

    # Missing occurrence identity rejects before pushHistory / effects.
    outcome = page.evaluate(
        """() => {
            const row = document.querySelector('#lemma-table tbody tr[data-row-id="0"]');
            const prevTargets = row.getAttribute('data-occurrence-targets');
            const prevSent = row.getAttribute('data-sentence-idx');
            row.removeAttribute('data-occurrence-targets');
            row.removeAttribute('data-sentence-idx');
            try {
                window.deleteSelectedRows();
                return 'accepted';
            } catch (err) {
                return err.message;
            } finally {
                if (prevTargets !== null) row.setAttribute('data-occurrence-targets', prevTargets);
                if (prevSent !== null) row.setAttribute('data-sentence-idx', prevSent);
            }
        }"""
    )
    assert outcome == "Deletion requires an occurrence identity"
    assert page.evaluate("isDirty()") is False
    assert row0.is_visible()

    # Ordinary one-row Delete -> Undo -> Redo -> Save control.
    page.locator("#kw-btn-delete").click()
    assert page.evaluate("getSelectedRowsArray()") == []
    assert page.evaluate("isDirty()") is True

    page.keyboard.press("Control+z")
    assert row0.get_attribute("data-deleted") is None
    assert page.evaluate("isDirty()") is False

    page.keyboard.press("Control+y")
    assert page.evaluate("isDirty()") is True
    with page.expect_response("**/session/save") as save_resp:
        page.locator("#kw-btn-save").click()
    assert save_resp.value.ok, save_resp.value.text()
    _flush_saves(page)

    assert adapter.db.get_words_by_session(ZID) == selected_words[1:]
    assert config_path.read_bytes() == initial_config
