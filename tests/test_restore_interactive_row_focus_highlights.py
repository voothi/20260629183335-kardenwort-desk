"""Acceptance and control tests for restoring interactive row focus highlights.

Exercises real owned SQLite storage, real ControllerRequestHandler HTTP/SSE endpoints,
real ClientViewAdapter focus persistence, real rendered Desk documents, and real
Playwright browser clients without database mocks or fixed sleeps.
"""
import json
import re

import pytest

import kardenwort_desk as desk
from kardenwort_state_http import config_snapshot
from kardenwort_state_runtime import preference_store
from tests.render_test_support import seed_render_rows
from tests.sqlite_document_support import island, replace_island, wait_state
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_same_lemma_toggle import _assert_source_roles, _source_words


ZID = "20261119085331"


def _render_sqlite_workspace_html(text, rows, *, zid=ZID, theme="dark", text_mode="multi"):
    config, paths, _, _ = desk.load_config()
    config.read_dict({
        "sentences_mode": {"enabled": "true", "delivery_mode": "container"},
        "settings": {"unify_article_pronoun_lemmas": "false", "deduplicate_pos_aware": "true"},
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


def _mount_workspace(page, world, html, stored, *, route_on_context=False):
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
        text_mode="multi",
        headers=session["headers"],
        data_rows=session["data_rows"],
    )

    session.update(server_url=url, api_token=server.api_key, server_enabled=True)
    boot.update(server_url=url, server_enabled=True)
    boot["ui_config"].update(config_snapshot(server, {})["ui"])
    html = replace_island(html, "session-data", session)
    html = replace_island(html, "ui-config", boot["ui_config"])
    html = replace_island(html, "kardenwort-bootstrap", boot)

    page.context.set_extra_http_headers({"X-API-Token": server.api_key})

    def document(route):
        boot["ui_config"].update(config_snapshot(server, {})["ui"])
        current = replace_island(html, "ui-config", boot["ui_config"])
        current = replace_island(current, "kardenwort-bootstrap", boot)
        route.fulfill(content_type="text/html", body=current)

    target = page.context if route_on_context else page
    target.route(url + "/", document)
    page.goto(url + "/")
    wait_state(page)
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")
    return adapter


def _client_id_from_url(page):
    match = re.search(r"kw-client=([0-9a-f]{32})", page.url)
    assert match, f"Expected kw-client slot in URL, got: {page.url}"
    return match.group(1)


def _client_view_rows(adapter, zid):
    with adapter.db.get_connection(read_only=True) as connection:
        rows = connection.execute(
            "SELECT client_id, active_card, active_tokens, revision "
            "FROM client_view_state WHERE session_zid=? ORDER BY client_id",
            (zid,),
        ).fetchall()
    return {
        row[0]: {"active_card": row[1], "active_tokens": json.loads(row[2]), "revision": row[3]}
        for row in rows
    }


def test_select_all_from_keyboard_highlights_primary_occurrences_and_isolates_unrelated_clients(
    page, preference_world
):
    """Delta Scenario: Select all from the keyboard in a ready SQLite workspace."""
    url, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus bleibt Haus und Baum bleibt Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Baum", "Baum", "noun", "tree", 1, "0"),
        ],
    )
    adapter = _mount_workspace(page, preference_world, html, stored, route_on_context=True)

    other = page.context.new_page()
    try:
        other.goto(url + "/")
        wait_state(other)
        other.wait_for_selector("#lemma-table tbody tr[data-row-id]")
        other.wait_for_function(
            "document.querySelector('#sse-status-indicator[data-status=\"connected\"]') !== null"
        )
        other_client_id = _client_id_from_url(other)
        views_before = _client_view_rows(adapter, ZID)

        assert page.evaluate("window.__CONFIG__.state_authority") == "sqlite"
        assert page.evaluate("getSelectedRowsArray()") == []
        _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])
        _assert_source_roles(other, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])

        with page.expect_response(lambda r: "/session/save" in r.url) as save_resp:
            page.keyboard.press("Control+a")
        assert save_resp.value.ok
        page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")

        assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
        assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 1]
        _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "primary", "baseline"])

        # Toggling Lemma ON projects peers coherently while preserving primary occurrences.
        with page.expect_response("**/api/v1/config") as cfg_resp:
            page.locator("#kw-btn-same-lemma").click()
        assert cfg_resp.value.ok
        _assert_source_roles(page, ["haus", "baum"], ["primary", "peer", "primary", "peer"])

        # Unrelated client receives shared membership without inventing client focus or highlights.
        other.wait_for_function("getSelectedRowsArray().length === 2")
        assert other.evaluate("AppState.activeTokenSelections") == []
        assert other.evaluate("getFocusedRowId()") is None
        _assert_source_roles(other, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])
        views_after = _client_view_rows(adapter, ZID)
        assert views_after.get(other_client_id) == views_before.get(other_client_id)
        assert config_path.read_bytes() == initial_config
    finally:
        other.close()


def test_select_all_includes_merged_row_group_ids_and_primary_highlights(page, preference_world):
    """Decision 1: selectAllRows includes merged getRowGroupIds and projects primary highlights."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Am Morgen.\nBeim Spiel.",
        [
            ("Am", "der", "art", "at", 1, "0"),
            ("Beim", "der", "art", "during", 2, "0"),
            ("Spiel", "Spiel", "noun", "game", 2, "0"),
        ],
    )
    adapter = _mount_workspace(page, preference_world, html, stored)
    merged_row = page.locator('#lemma-table tbody tr[data-all-row-ids="0,1"]')
    assert merged_row.count() == 1

    with page.expect_response(lambda r: "/session/save" in r.url) as save_resp:
        page.keyboard.press("Control+a")
    assert save_resp.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")

    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1, 2]
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 1, 1]
    _assert_source_roles(page, ["am", "beim", "spiel"], ["primary", "baseline", "primary"])

    with page.expect_response("**/api/v1/config") as cfg_resp:
        page.locator("#kw-btn-same-lemma").click()
    assert cfg_resp.value.ok
    _assert_source_roles(page, ["am", "beim", "spiel"], ["primary", "peer", "primary"])
    assert config_path.read_bytes() == initial_config


def test_toggle_row_selection_and_checkbox_host_update_interactive_focus(page, preference_world):
    """Decision 1: canonical toggleRowSelection and checkbox kwToggleTag update interactive focus."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus bleibt Haus und Baum bleibt Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Baum", "Baum", "noun", "tree", 1, "0"),
        ],
    )
    adapter = _mount_workspace(page, preference_world, html, stored)

    # Canonical toggleRowSelection entry selects row 0 and establishes interactive focus.
    page.evaluate("window.toggleRowSelection(0, true)")
    assert page.evaluate("getSelectedRowsArray()") == [0]
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline", "baseline"])

    # Checkbox host (kwToggleTag) selects row 1 and projects both primary occurrences.
    with page.expect_response(lambda r: "/session/save" in r.url) as save_resp:
        page.evaluate(
            """() => {
                const row = document.querySelector('#lemma-table tbody tr[data-row-id="1"]');
                const td = document.createElement('td');
                td.className = 'kw-tag-control';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'kw-tag-checkbox';
                cb.setAttribute('data-row-id', '1');
                cb.checked = true;
                td.appendChild(cb);
                row.insertBefore(td, row.firstChild);
                window.kwToggleTag(cb, 1);
            }"""
        )
    assert save_resp.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 1]
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "primary", "baseline"])

    # Deselecting row 1 through toggleRowSelection retains row 0's highlight.
    page.evaluate("window.toggleRowSelection(1, false)")
    assert page.evaluate("getSelectedRowsArray()") == [0]
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline", "baseline"])

    # Deselecting the last remaining row clears highlights and focus.
    page.evaluate("window.toggleRowSelection(0, false)")
    assert page.evaluate("getSelectedRowsArray()") == []
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])
    assert config_path.read_bytes() == initial_config


def test_ctrl_deselect_one_row_retains_remaining_row_source_highlight(page, preference_world):
    """Delta Scenario: Deselect one row via Ctrl-click retains the remaining row's source highlight."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus bleibt Haus und Baum bleibt Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "0"),
            ("Baum", "Baum", "noun", "tree", 1, "0"),
        ],
    )
    adapter = _mount_workspace(page, preference_world, html, stored)
    row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    row1 = page.locator('#lemma-table tbody tr[data-row-id="1"]')

    # Select both rows interactively.
    row0.click()
    with page.expect_response(lambda r: "/session/save" in r.url) as save_both:
        row1.click(modifiers=["Control"])
    assert save_both.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "primary", "baseline"])

    # Ctrl-deselect row 1 -> row 0 remains selected and keeps its primary source highlight.
    with page.expect_response(lambda r: "/session/save" in r.url) as save_one:
        row1.click(modifiers=["Control"])
    assert save_one.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    assert page.evaluate("getSelectedRowsArray()") == [0]
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 0]
    _assert_source_roles(page, ["haus", "baum"], ["primary", "baseline", "baseline", "baseline"])

    # Toggling Lemma ON projects row 0's peer occurrence and keeps row 1 baseline.
    with page.expect_response("**/api/v1/config") as cfg_resp:
        page.locator("#kw-btn-same-lemma").click()
    assert cfg_resp.value.ok
    _assert_source_roles(page, ["haus", "baum"], ["primary", "peer", "baseline", "baseline"])

    # Ctrl-deselecting the last remaining row clears highlights and row focus.
    with page.expect_response(lambda r: "/session/save" in r.url) as save_none:
        row0.click(modifiers=["Control"])
    assert save_none.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    assert page.evaluate("getSelectedRowsArray()") == []
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [0, 0]
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])
    assert config_path.read_bytes() == initial_config


def test_cold_membership_reload_preserves_empty_client_focus_without_inventing_highlight(
    page, preference_world
):
    """Delta Scenario & Adjacent Control: Cold membership reload does not invent source focus."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus bleibt Haus und Baum bleibt Baum.",
        [
            ("Haus", "Haus", "noun", "house", 1, "1"),
            ("Baum", "Baum", "noun", "tree", 1, "0"),
        ],
    )
    adapter = _mount_workspace(page, preference_world, html, stored)
    client_id = _client_id_from_url(page)

    # Initial cold load: committed membership exists, client focus is empty -> no active highlights.
    assert page.evaluate("getSelectedRowsArray()") == [0]
    assert page.evaluate("AppState.activeTokenSelections") == []
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])

    # Reload preserves committed membership and empty client focus without inventing highlights.
    page.reload()
    wait_state(page)
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")
    assert _client_id_from_url(page) == client_id
    assert page.evaluate("getSelectedRowsArray()") == [0]
    assert page.evaluate("AppState.activeTokenSelections") == []
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus", "baum"], ["baseline", "baseline", "baseline", "baseline"])
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 0]
    assert config_path.read_bytes() == initial_config


def test_select_all_clears_stale_token_focus_and_preserves_bound_partners_and_hover(
    page, preference_world
):
    """Tasks 2.2 & 2.3: Select All clears prior exact-token focus via view save barrier,
    preserves primary/partner/peer identity and hover invariance, and leaves unrelated data intact."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Er fängt heute an.\nEr fängt morgen an.",
        [
            ("fängt + an", "anfangen", "verb", "begin", 1, "0"),
            ("fängt + an", "anfangen", "verb", "begin", 2, "0"),
            ("heute", "heute", "adv", "today", 1, "0"),
        ],
    )
    adapter = _mount_workspace(page, preference_world, html, stored)
    before_translations = [
        (w["lemma"], w["word_destination"], w.get("metadata_json"))
        for w in adapter.db.get_words_by_session(ZID)
    ]

    # Establish exact-token focus on the second separable occurrence first.
    with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as view_set:
        _source_words(page, ["fängt"]).nth(1).click()
    assert view_set.value.ok
    page.evaluate("flushPendingSaves()")
    assert len(page.evaluate("AppState.activeTokenSelections")) > 0

    # Select All transitions cleanly to row-scoped primary highlights and persists cleared token focus.
    with page.expect_response(lambda r: "/api/v1/client-view" in r.url) as view_clear:
        page.keyboard.press("Control+a")
    assert view_clear.value.ok
    page.evaluate("flushPendingSaves()")
    assert page.evaluate("AppState.activeTokenSelections") == []
    assert [w["selected"] for w in adapter.db.get_words_by_session(ZID)] == [1, 1, 1]
    _assert_source_roles(
        page, ["fängt", "an"], ["primary", "primary", "baseline", "baseline"], primary="highlight-purple-active"
    )
    _assert_source_roles(page, ["heute"], ["primary"])

    # Enable Lemma -> second separable pair becomes peer; hovering preserves primary/partner/peer roles.
    with page.expect_response("**/api/v1/config") as cfg_resp:
        page.locator("#kw-btn-same-lemma").click()
    assert cfg_resp.value.ok
    _assert_source_roles(
        page, ["fängt", "an"], ["primary", "primary", "peer", "peer"], primary="highlight-purple-active"
    )
    _source_words(page, ["fängt"]).nth(1).hover()
    page.mouse.move(0, 0)
    _assert_source_roles(
        page, ["fängt", "an"], ["primary", "primary", "peer", "peer"], primary="highlight-purple-active"
    )

    # Ctrl-deselecting 'heute' keeps 'anfangen' primary+partner+peer highlights intact.
    heute_row = page.locator("#lemma-table tbody tr", has_text="heute").first
    with page.expect_response(lambda r: "/session/save" in r.url) as save_deselect:
        heute_row.click(modifiers=["Control"])
    assert save_deselect.value.ok
    page.wait_for_function("JSON.parse(getDeltas()).every(d => d.column !== 'DeskSelected')")
    _assert_source_roles(
        page, ["fängt", "an"], ["primary", "primary", "peer", "peer"], primary="highlight-purple-active"
    )
    _assert_source_roles(page, ["heute"], ["baseline"])

    after_translations = [
        (w["lemma"], w["word_destination"], w.get("metadata_json"))
        for w in adapter.db.get_words_by_session(ZID)
    ]
    assert after_translations == before_translations
    assert config_path.read_bytes() == initial_config


def test_lookup_mode_missing_occurrence_identity_fails_explicitly_without_focus_mutation(
    page, preference_world
):
    """Task 2.3: Explicit failure behavior in toggleRowSelection leaves selection and focus untouched."""
    _, _, config_path = preference_world
    initial_config = config_path.read_bytes()
    html, stored = _render_sqlite_workspace_html(
        "Haus bleibt Haus.",
        [("Haus", "Haus", "noun", "house", 1, "0")],
    )
    _mount_workspace(page, preference_world, html, stored)

    page.evaluate(
        """() => {
            window.AppState.is_lookup = true;
            const row = document.querySelector('#lemma-table tbody tr[data-row-id="0"]');
            row.setAttribute('data-sentence-idx', '');
            row.setAttribute('data-token-order', '');
            window.toggleRowSelection(0, true);
        }"""
    )
    assert "Selection unavailable: missing occurrence identity" in page.locator("#kw-toast-container").inner_text()
    assert page.evaluate("getSelectedRowsArray()") == []
    assert page.evaluate("getFocusedRowId()") is None
    _assert_source_roles(page, ["haus"], ["baseline", "baseline"])
    assert config_path.read_bytes() == initial_config

