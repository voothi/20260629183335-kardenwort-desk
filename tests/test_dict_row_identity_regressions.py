"""Behavioral regressions reproducing Dict row identity and selection defects.

Phase 1 contracts:
- 1.2 Checkbox display row ID vs token order mismatch: missing selection and deltas before fix.
- 1.3 SQLite sentence-2 token 0 edit: missing sentence metadata writes to sentence 1 before fix.
- 1.4 Checkbox projection: local deselection, keyboard/Undo, remote membership, and reversal during held save.
"""

import json
from pathlib import Path
import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from kardenwort_desk import SqliteStorageAdapter, core_edit_save, StructuredError
from tests.test_controller_integration import running_controller  # noqa: F401


TRANSPORT = """<script>
window.__requests = [];
window.__pendingSaves = [];
window.__pendingStatus = [];
window.__reloads = 0;
window.onSessionReload = function() { window.__reloads++; };
window.EventSource = class {
    constructor() { window.__eventSource = this; }
    close() {}
};
window.fetch = function(url, options) {
    const body = options && options.body ? JSON.parse(options.body) : null;
    const headers = options && options.headers ? options.headers : {};
    window.__requests.push({url: String(url), body, headers});
    const response = data => ({ok: true, status: 200, json: async () => data});
    if (String(url).includes('/session/save') && window.__holdSaves) {
        return new Promise(resolve => window.__pendingSaves.push(
            () => resolve(response({ok: true, status: 'success'}))));
    }
    if (String(url).includes('/session/status') && window.__holdStatus) {
        return new Promise(resolve => window.__pendingStatus.push(
            () => resolve(response({ok: true, stage: 'finished'}))));
    }
    return Promise.resolve(response({ok: true, status: 'success', stage: 'finished'}));
};
</script>"""


def make_lookup_page(page, data_rows, headers=None, session_zid="20261026093717", text="Das Haus. Der Baum.", language="de", target_lang="ru"):
    config, paths, goldendict, _ = desk.load_config()
    goldendict["server_enabled"] = True
    goldendict["server_api_key"] = "test-controller-api-key"
    if headers is None:
        headers = [
            "Quotation", "WordSource", "WordDestination",
            "SentenceSourceIndex", "TokenOrder", "DeskSelected"
        ]
    html = desk.render_lookup_html(
        text, language, target_lang, config, paths, session_zid, goldendict,
        [], headers, data_rows, "The house. The tree.",
        session_zid=session_zid, api_token="test-controller-api-key", server_enabled=True,
    )
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    page.wait_for_function("typeof window.toggleRowSelection === 'function'")
    return html


# ---------------------------------------------------------------------------
# Task 1.2: Checkbox row ID vs token order mismatch
# ---------------------------------------------------------------------------

def test_1_2_checkbox_row_id_differs_from_token_order(page):
    """
    Demonstrate that when display row ID differs from token order
    (e.g. after frequency sorting, display row 1 has token order 9):
    the clicked checkbox must resolve the clicked row ID, select the row,
    and generate a persistence delta containing token order 9 and its sentence.
    """
    # Display row 0: token_order 5, lemma "Haus"
    # Display row 1: token_order 9, lemma "Baum"
    headers = [
        "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "TokenOrder", "DeskSelected"
    ]
    data_rows = [
        ["Haus", "Haus", "house", "1", "5", "0"],
        ["Baum", "Baum", "tree", "1", "9", "0"],
    ]
    make_lookup_page(page, data_rows, headers=headers)

    # Click the checkbox on display row 1 (Haus, token order 9)
    cb_row1 = page.locator('tr[data-row-id="1"] input.kw-tag-checkbox')
    expect(cb_row1).to_be_visible()
    cb_row1.check()

    # The checkbox should be checked
    assert cb_row1.is_checked()

    # Authoritative contract:
    # 1. Row 1 must have selection classes
    row1 = page.locator('tr[data-row-id="1"]')
    assert row1.evaluate("el => el.classList.contains('kw-row-selected')"), (
        "Row 1 must acquire kw-row-selected class when its checkbox is checked"
    )
    assert row1.evaluate("el => el.getAttribute('data-selected') === '1'"), (
        "Row 1 must acquire data-selected='1'"
    )

    # 2. Delta must be produced targeting row 1 with token_order 9 and sentence_idx 1
    deltas = json.loads(page.evaluate("getDeltas()"))
    matching = [d for d in deltas if d.get("column") == "DeskSelected"]
    assert len(matching) >= 1, f"Expected DeskSelected delta, got deltas: {deltas}"
    d = matching[0]
    assert str(d.get("row_id")) == "1"
    assert int(d.get("token_order")) == 9
    assert int(d.get("sentence_idx")) == 1
    assert str(d.get("value")) == "1"


# ---------------------------------------------------------------------------
# Task 1.3: End-to-end isolated SQLite regression for repeated token orders
# ---------------------------------------------------------------------------

def test_1_3_isolated_sqlite_sentence_duplicate_token_order(tmp_path, page):
    """
    Demonstrate that editing sentence-2 token 0 when sentence 1 also has token 0
    must persist the edit to sentence 2 only.
    Missing sentence metadata must not default to sentence 1 or write to the wrong row.
    """
    db_path = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    cfg, paths, goldendict, _ = desk.load_config()
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(db_path))
    paths["sqlite_db_path"] = str(db_path)
    paths["storage_backend"] = "sqlite"

    adapter = SqliteStorageAdapter(config=cfg, resolved_paths=paths, db_path=str(db_path))
    session_zid = "20261026093717"

    headers = [
        "Quotation", "WordSource", "WordDestination",
        "SentenceSourceIndex", "TokenOrder", "DeskSelected"
    ]
    # Sentence 1 has token 0 ("Haus" -> "house")
    # Sentence 2 has token 0 ("Baum" -> "tree")
    initial_rows = [
        ["Haus", "Haus", "house", "1", "0", "0"],
        ["Baum", "Baum", "tree", "2", "0", "0"],
    ]
    initial_sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "The house."},
        {"session_zid": session_zid, "sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "The tree."},
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="dup-token-order",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Das Haus. Der Baum.",
        headers=headers,
        data_rows=initial_rows,
        sentences=initial_sentences,
        zid=session_zid,
    )

    # Verify initial database state
    with db.get_connection(read_only=True) as conn:
        words_before = conn.execute(
            "SELECT sentence_index, token_order, lemma, word_destination FROM words WHERE session_zid = ? ORDER BY sentence_index, token_order",
            (session_zid,)
        ).fetchall()
    assert len(words_before) == 2
    assert (words_before[0]["sentence_index"], words_before[0]["token_order"], words_before[0]["word_destination"]) == (1, 0, "house")
    assert (words_before[1]["sentence_index"], words_before[1]["token_order"], words_before[1]["word_destination"]) == (2, 0, "tree")

    # Render lookup HTML
    html = desk.render_lookup_html(
        "Das Haus. Der Baum.", "de", "en", cfg, paths, session_zid, goldendict,
        [], headers, initial_rows, "The house. The tree.",
        session_zid=session_zid, api_token="test-controller-api-key", server_enabled=True,
    )
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    page.wait_for_function("typeof window.toggleRowSelection === 'function'")

    # Row 1 corresponds to sentence 2, token 0 ("Baum" -> "tree")
    # Verify that row 1 has explicit data-sentence-idx="2" and data-token-order="0"
    tr_row1 = page.locator('tr[data-row-id="1"]')
    assert tr_row1.get_attribute("data-sentence-idx") == "2", (
        "Lookup row 1 must embed authoritative data-sentence-idx='2'"
    )
    assert tr_row1.get_attribute("data-token-order") == "0"

    # Edit row 1 translation cell: double click, type new translation, commit
    trans_cell = page.locator('tr[data-row-id="1"] td.col-translation')
    trans_cell.dblclick()
    input_box = trans_cell.locator('input.edit-input')
    expect(input_box).to_be_visible()
    input_box.fill("wood-tree")
    input_box.press("Enter")

    # Deltas must have sentence_idx=2
    deltas = json.loads(page.evaluate("getDeltas()"))
    edit_deltas = [d for d in deltas if d.get("column") in ("WordDestination", "word_destination")]
    assert len(edit_deltas) == 1, f"Expected 1 WordDestination delta, got: {deltas}"
    assert int(edit_deltas[0]["sentence_idx"]) == 2, (
        f"Delta must carry sentence_idx=2, got {edit_deltas[0]}"
    )
    assert int(edit_deltas[0]["token_order"]) == 0

    # Save to SQLite through core_edit_save
    res = core_edit_save(
        tsv_path_or_session=session_zid,
        deltas=deltas,
        config=cfg,
        resolved_paths=paths,
        zid=session_zid,
        language="de",
    )
    assert res["status"] == "success"

    # Verify SQLite contents after save:
    # Sentence 2 token 0 MUST be "wood-tree"
    # Sentence 1 token 0 MUST REMAIN "house" (NOT overwritten!)
    with db.get_connection(read_only=True) as conn:
        words_after = conn.execute(
            "SELECT sentence_index, token_order, lemma, word_destination FROM words WHERE session_zid = ? ORDER BY sentence_index, token_order",
            (session_zid,)
        ).fetchall()
    assert words_after[0]["sentence_index"] == 1
    assert words_after[0]["token_order"] == 0
    assert words_after[0]["word_destination"] == "house", (
        f"Sentence 1 must retain 'house', but was overwritten with '{words_after[0]['word_destination']}'!"
    )

    assert words_after[1]["sentence_index"] == 2
    assert words_after[1]["token_order"] == 0
    assert words_after[1]["word_destination"] == "wood-tree", (
        f"Sentence 2 must receive 'wood-tree', got '{words_after[1]['word_destination']}'"
    )


def test_1_3_core_edit_save_rejects_missing_sentence_coordinates(tmp_path):
    """
    Word-field edit with missing or malformed sentence coordinate must fail fast
    with StructuredError rather than defaulting to sentence 1 or updating all sentences.
    """
    db_path = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    cfg, paths, _, _ = desk.load_config()
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(db_path))
    paths["sqlite_db_path"] = str(db_path)
    paths["storage_backend"] = "sqlite"

    adapter = SqliteStorageAdapter(config=cfg, resolved_paths=paths, db_path=str(db_path))
    session_zid = "20261026093717"

    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
    rows = [
        ["Haus", "Haus", "house", "1", "0", "0"],
        ["Baum", "Baum", "tree", "2", "0", "0"],
    ]
    adapter.save_session(
        session_zid=session_zid, slug="reject-test", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.", headers=headers, data_rows=rows,
        sentences=[{"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Das Haus."},
                   {"session_zid": session_zid, "sentence_index": 2, "sentence_source": "Der Baum."}],
        zid=session_zid,
    )

    # Missing sentence_idx delta
    invalid_deltas = [
        {"row_id": 1, "token_order": 0, "column": "WordDestination", "value": "corrupted"}
    ]
    with pytest.raises(StructuredError):
        core_edit_save(session_zid, invalid_deltas, cfg, paths, zid=session_zid, language="de")

    # Verify no writes occurred
    with db.get_connection(read_only=True) as conn:
        words = conn.execute("SELECT word_destination FROM words WHERE session_zid = ? ORDER BY sentence_index", (session_zid,)).fetchall()
    assert words[0]["word_destination"] == "house"
    assert words[1]["word_destination"] == "tree"


# ---------------------------------------------------------------------------
# Task 1.4: Checkbox projection regressions (deselection, keyboard, Undo, remote, reversal)
# ---------------------------------------------------------------------------

def test_1_4_checkbox_synchronizes_on_local_deselection(page):
    """
    Deselecting a row via toggleRowSelection or clicking must uncheck its checkbox.
    """
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
    data_rows = [
        ["Haus", "Haus", "house", "1", "0", "1"],
        ["Baum", "Baum", "tree", "1", "1", "0"],
    ]
    make_lookup_page(page, data_rows, headers=headers)

    cb_row0 = page.locator('tr[data-row-id="0"] input.kw-tag-checkbox')
    expect(cb_row0).to_be_checked()

    # Deselect row 0
    page.evaluate("toggleRowSelection(0, false)")

    # Checkbox must become unchecked
    expect(cb_row0).not_to_be_checked()


def test_1_4_checkbox_synchronizes_on_undo(page):
    """
    Checking a checkbox then performing Undo must revert the checkbox to unchecked.
    """
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
    data_rows = [
        ["Haus", "Haus", "house", "1", "0", "0"],
    ]
    make_lookup_page(page, data_rows, headers=headers)

    cb_row0 = page.locator('tr[data-row-id="0"] input.kw-tag-checkbox')
    expect(cb_row0).not_to_be_checked()

    # Check the checkbox
    cb_row0.check()
    expect(cb_row0).to_be_checked()

    # Undo the action
    page.evaluate("window.undo()")
    expect(cb_row0).not_to_be_checked()


def test_1_4_checkbox_synchronizes_on_remote_membership(page):
    """
    Remote membership update via applySharedSelection must update checkbox state.
    """
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
    data_rows = [
        ["Haus", "Haus", "house", "1", "0", "0"],
        ["Baum", "Baum", "tree", "1", "1", "1"],
    ]
    make_lookup_page(page, data_rows, headers=headers)

    cb_row0 = page.locator('tr[data-row-id="0"] input.kw-tag-checkbox')
    cb_row1 = page.locator('tr[data-row-id="1"] input.kw-tag-checkbox')
    expect(cb_row0).not_to_be_checked()
    expect(cb_row1).to_be_checked()

    # Apply remote update: haus is selected, baum is unselected
    page.evaluate("applySharedSelection({haus: true, baum: false})")

    expect(cb_row0).to_be_checked()
    expect(cb_row1).not_to_be_checked()


def test_1_4_checkbox_reversal_during_held_save_retains_latest_intent(page):
    """
    Checking a row starts a save. While save is held, unchecking (reversal)
    must keep the checkbox unchecked and canonical selection false after the
    held save acknowledges.
    """
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
    data_rows = [
        ["Haus", "Haus", "house", "1", "0", "0"],
    ]
    make_lookup_page(page, data_rows, headers=headers)

    page.evaluate("window.__holdSaves = true")

    cb_row0 = page.locator('tr[data-row-id="0"] input.kw-tag-checkbox')
    cb_row0.check()

    # Wait for save to be in flight
    page.wait_for_function("__pendingSaves.length === 1")

    # While save is in flight, user unchecks (reverses)
    cb_row0.uncheck()
    expect(cb_row0).not_to_be_checked()

    # Now fulfill the held save
    page.evaluate("__pendingSaves.shift()()")

    # Newer local reversal MUST remain visible: checkbox must still be unchecked
    expect(cb_row0).not_to_be_checked()
    assert page.evaluate("!AppState.selectedLemmas.haus"), (
        "Canonical membership must retain the reversal (haus unselected)"
    )
