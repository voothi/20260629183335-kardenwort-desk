"""Phase 1 real-boundary reproduction & contract tests for focus, keyboard, drag, columns, and shells.

Change: 20261122213501-stabilize-frontend-sse-focus-columns-and-js-hygiene (Task 1.2)
Specs:
  - specs/table-selection-model/spec.md
  - specs/static-web-shell/spec.md
"""
import json
import re
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_client_views import ClientViewAdapter
from tests.test_drag_view_persistence import (
    active_token_indices,
    active_visual_indices,
    client_id,
    drag_sqlite_page,  # noqa: F401
    ready,
    word_center,
)
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_same_lemma_toggle import (
    _assert_source_roles,
    _open_row_highlight_document,
    _row_highlight_document,
    get_multi_occurrence_desk_page_html,
)
from tests.test_static_web_shell import test_server  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]

HEADERS_COMPOUND = [
    "WordSourceInflectedForm",
    "WordSource",
    "WordDestination",
    "DeskSelected",
    "SentenceSource",
    "SentenceSourceIndex",
    "TokenOrder",
]


def _seed_container_compound_session(server, zid: str):
    server.config.set("sentences_mode", "enabled", "true")
    server.config.set("sentences_mode", "delivery_mode", "container")
    server.config.set("rendering", "highlight_same_lemma", "false")
    server.config.set("rendering", "filter_selected_only", "false")
    server.config.set("rendering", "auto_save_on_edit", "false")
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    source_text = "multi-token here. Haus dort."
    rows = [
        ["multi-token", "multi-token", "мульти-токен", "0", "multi-token here.", "1", "0"],
        ["Haus", "Haus", "дом", "0", "Haus dort.", "2", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        source_language="de",
        target_language="ru",
        source_raw_text=source_text,
        text_mode="single",
        headers=HEADERS_COMPOUND,
        data_rows=rows,
    )
    return adapter


def _active_visual_idxs(page):
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('#source-container span.word'))
        .filter(el => el.classList.contains('highlight-orange-active')
            || el.classList.contains('highlight-purple-active')
            || el.classList.contains('active-subtoken'))
        .map(el => Number(el.getAttribute('data-word-idx')))"""
    )


def test_1_2_orphan_focus_across_tab_round_trip_rebind_undo_redo_and_reload(page, preference_world):
    """Derived orphan focus must stay synchronized across tab switches, rebind, Delete/Undo/Redo, and reload."""
    url, server, _ = preference_world
    zid = "20261124213101"
    adapter = _seed_container_compound_session(server, zid)

    page.context.set_extra_http_headers({"X-API-Token": server.api_key})
    page.goto(f"{url}/?session_zid={zid}&seq_num=1")
    ready(page)

    manifest = page.evaluate('() => JSON.parse(document.getElementById("token-map").textContent)')
    multi_tok = next(t for t in manifest if t.get("lower_clean") == "multi")
    haus_tok = next(t for t in manifest if t.get("lower_clean") == "haus")
    assert multi_tok["atomic_row_ids"] == []
    assert len(haus_tok["atomic_row_ids"]) == 1

    # 1. Click compound-only 'multi' -> focused visually without shared row membership.
    multi_span = page.locator(f'#source-container span.word[data-word-idx="{multi_tok["visual_idx"]}"]')
    multi_span.click()
    page.evaluate("flushPendingSaves()")
    assert _active_visual_idxs(page) == [multi_tok["visual_idx"]]
    assert not any(adapter.get_shared_selection(zid).values())

    # 2. Switch to Tab 2 (Sentence 1, contains 'multi') -> focus survives.
    page.evaluate("WorkspaceTabs.switchToTab(2)")
    page.evaluate("flushPendingSaves()")
    assert [t["visual_idx"] for t in page.evaluate("AppState.activeTokenSelections")] == [multi_tok["visual_idx"]]
    assert _active_visual_idxs(page) == [multi_tok["visual_idx"]]

    # 3. Switch to Tab 3 (Sentence 2, does NOT contain 'multi') -> activeTokenSelections becomes [],
    #    so derived orphan map must ALSO clear (no phantom highlight when returning to Tab 1!).
    page.evaluate("WorkspaceTabs.switchToTab(3)")
    page.evaluate("flushPendingSaves()")
    assert page.evaluate("AppState.activeTokenSelections") == []
    assert _active_visual_idxs(page) == []

    page.evaluate("WorkspaceTabs.switchToTab(1)")
    page.evaluate("flushPendingSaves()")
    assert page.evaluate("AppState.activeTokenSelections") == []
    assert _active_visual_idxs(page) == [], (
        "Stale selectedOrphanWordIdxsMap survived tab switch after activeTokenSelections was cleared"
    )

    # 4. Re-select compound-only 'multi' + atomic 'Haus', test rebindTableRows(), Delete -> Undo -> Redo -> Undo.
    multi_span.click()
    haus_span = page.locator(f'#source-container span.word[data-word-idx="{haus_tok["visual_idx"]}"]')
    haus_span.click()
    page.evaluate("flushPendingSaves()")
    expected_pair = sorted([multi_tok["visual_idx"], haus_tok["visual_idx"]])
    assert sorted(_active_visual_idxs(page)) == expected_pair

    page.evaluate("rebindTableRows()")
    assert sorted(_active_visual_idxs(page)) == expected_pair

    # Delete selected row ('Haus') -> clears focus, then Ctrl+Z (Undo) must restore BOTH 'multi' and 'Haus' visually!
    page.evaluate("window.onDeleteClick()")
    assert page.evaluate("AppState.activeTokenSelections") == []
    assert _active_visual_idxs(page) == []

    page.keyboard.press("Control+z")
    assert sorted([t["visual_idx"] for t in page.evaluate("AppState.activeTokenSelections")]) == expected_pair
    assert sorted(_active_visual_idxs(page)) == expected_pair, (
        "Undo after Delete restored AppState.activeTokenSelections but lost compound-only visual highlight"
    )

    # Redo removes again, Undo restores again.
    page.keyboard.press("Control+y")
    assert _active_visual_idxs(page) == []
    page.keyboard.press("Control+z")
    assert sorted(_active_visual_idxs(page)) == expected_pair

    # 5. Cold reload retains restored focus without synthesizing shared membership for multi-token.
    page.evaluate("flushPendingSaves()")
    page.reload()
    ready(page)
    assert sorted([t["visual_idx"] for t in page.evaluate("AppState.activeTokenSelections")]) == expected_pair
    assert sorted(_active_visual_idxs(page)) == expected_pair
    assert adapter.get_shared_selection(zid).get("multi-token") is False


def test_1_2_merged_row_space_toggles_group_and_escape_clears_actual_focus(page, tmp_path):
    """Space must toggle merged group IDs in container mode and Escape must clear actual row focus."""
    html = get_multi_occurrence_desk_page_html(tmp_path)
    _open_row_highlight_document(page, html)

    row = page.locator("#lemma-table tbody tr").first
    assert row.get_attribute("data-all-row-ids") == "0,1"
    assert page.evaluate("Boolean(window.WorkspaceTabs && window.WorkspaceTabs.getCards && window.WorkspaceTabs.getCards().length >= 1)") is True

    # Focus the merged row with ArrowDown, then press Space to select full merged group.
    page.keyboard.press("ArrowDown")
    assert "focused-row" in (row.get_attribute("class") or "")
    page.keyboard.press("Space")
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1], (
        "Space on merged row in container mode failed to toggle all constituent IDs"
    )
    assert page.locator("#lemma-table tbody tr.focused-row").count() == 1

    # Press Escape -> must clear selections AND clear actual focused-row marker!
    page.keyboard.press("Escape")
    assert page.evaluate("getSelectedRowsArray()") == []
    assert page.locator("#lemma-table tbody tr.focused-row").count() == 0, (
        "Escape cleared selection but left stale .focused-row highlight active"
    )

    # Space again after re-focusing toggles full group on and then off cleanly.
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Space")
    assert sorted(page.evaluate("getSelectedRowsArray()")) == [0, 1]
    page.keyboard.press("Space")
    assert page.evaluate("getSelectedRowsArray()") == []


@pytest.mark.parametrize("interrupt_event", ["blur", "pointercancel"])
def test_1_2_drag_blur_or_pointercancel_settles_once_without_audio(drag_sqlite_page, interrupt_event):
    """Blur or pointercancel during drag must clear dragging classes, save view once, and trigger zero audio."""
    page, url, server, adapter, view_writes, view_requests = drag_sqlite_page
    page.goto(url + "/")
    ready(page)
    view_writes.clear()
    view_requests.clear()

    # Enable LMB audio and spy on playAudio to prove blur/cancel never plays accidental audio.
    page.evaluate(
        """() => {
            window.__CONFIG__ = window.__CONFIG__ || {};
            window.__CONFIG__.audio_lmb_play = true;
            window.__audioCalls = [];
            const origPlay = window.playAudio;
            window.playAudio = function(text, lang) {
                window.__audioCalls.push({text, lang});
                return origPlay ? origPlay.apply(this, arguments) : undefined;
            };
        }"""
    )

    # Start source drag across words 0 -> 2 (no mouseup yet).
    page.mouse.move(*word_center(page, 0))
    page.mouse.down(button="left")
    page.mouse.move(*word_center(page, 1))
    page.mouse.move(*word_center(page, 2))
    assert active_token_indices(page) == [0, 1, 2]
    assert len(view_requests) == 0

    # Interrupt drag via blur or pointercancel.
    if interrupt_event == "blur":
        page.evaluate("window.dispatchEvent(new Event('blur'))")
    else:
        page.evaluate("document.dispatchEvent(new Event('pointercancel', {bubbles: true}))")

    page.evaluate("flushPendingSaves()")
    assert len(view_writes) == 1, (
        f"Expected exactly 1 settled client-view write on {interrupt_event}, got {len(view_writes)}"
    )
    assert page.evaluate("document.getElementById('lemma-table').classList.contains('kw-table-dragging')") is False
    assert page.evaluate("window.__audioCalls") == [], (
        f"Unexpected audio triggered during drag {interrupt_event}: {page.evaluate('window.__audioCalls')}"
    )

    # Subsequent mouseup must not emit a duplicate write or trigger audio.
    page.mouse.up(button="left")
    page.evaluate("flushPendingSaves()")
    assert len(view_writes) == 1
    assert page.evaluate("window.__audioCalls") == []

    # Verify table drag .kw-table-dragging also clears on blur/pointercancel without audio.
    row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]').bounding_box()
    row1 = page.locator('#lemma-table tbody tr[data-row-id="1"]').bounding_box()
    page.mouse.move(row0["x"] + 12, row0["y"] + row0["height"] / 2)
    page.mouse.down(button="left")
    page.mouse.move(row1["x"] + 12, row1["y"] + row1["height"] / 2)
    assert page.evaluate("document.getElementById('lemma-table').classList.contains('kw-table-dragging')") is True
    if interrupt_event == "blur":
        page.evaluate("window.dispatchEvent(new Event('blur'))")
    else:
        page.evaluate("document.dispatchEvent(new Event('pointercancel', {bubbles: true}))")
    assert page.evaluate("document.getElementById('lemma-table').classList.contains('kw-table-dragging')") is False
    assert page.evaluate("window.__audioCalls") == []
    page.mouse.up(button="left")


def test_1_2_semantic_custom_hidden_reordered_columns_never_use_positional_fallbacks(page, test_server):
    """Custom, reordered, checkbox-prefixed, and hidden optional columns must resolve semantically."""
    port = test_server
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("typeof window.AppView === 'object'")

    result = page.evaluate(
        """() => {
            window.__CONFIG__ = Object.assign(window.__CONFIG__ || {}, {
                lemma_col_name: 'CustomLemma',
                inflected_col_name: 'CustomInflected',
                trans_col_name: 'CustomTranslation'
            });
            const tbody = document.querySelector('#lemma-table tbody');
            // Leading tag checkbox + reordered custom columns + NO optional IPA or Morphology columns.
            tbody.innerHTML = `
                <tr data-row-id="0" data-token-order="0" data-sentence-idx="1">
                    <td class="kw-tag-control"><input type="checkbox" class="kw-tag-checkbox"></td>
                    <td data-col="CustomLemma"><div class="scrollable-cell">Haus</div></td>
                    <td data-col="CustomInflected"><div class="scrollable-cell">Häuser</div></td>
                    <td data-col="CustomTranslation"><div class="scrollable-cell"><span class="skeleton-loader" data-pending="true">Google...</span></div></td>
                    <td data-col="WordSourcePOS"><div class="scrollable-cell">n.</div></td>
                </tr>
                <tr data-row-id="1" data-token-order="1" data-sentence-idx="1">
                    <td class="kw-tag-control"><input type="checkbox" class="kw-tag-checkbox"></td>
                    <td data-col="WordSource"><div class="scrollable-cell">Baum</div></td>
                    <td data-col="WordDestination"><div class="scrollable-cell">tree</div></td>
                    <td data-col="WordSourceIPA"><div class="scrollable-cell"></div></td>
                    <td data-col="WordSourcePOS"><div class="scrollable-cell">n.</div></td>
                </tr>
            `;
            window.AppState = window.AppState || {rows: {}};
            window.AppState.rows = {
                '0': {
                    row_id: 0, token_order: 0, sentence_idx: 1,
                    lemma: 'Haus', inflected: 'Häuser', trans: 'house',
                    ipa: '/haʊs/', morph: 'Nom.Pl', provenance: 'live:google'
                },
                '1': {
                    row_id: 1, token_order: 1, sentence_idx: 1,
                    lemma: 'Baum', trans: 'tree', ipa: '/baʊm/', pos: 'n.', provenance: 'live:google'
                }
            };
            window.AppView.renderRow('0', 'finished');
            window.AppView.renderRow('1', 'finished');
            const tr0 = tbody.querySelector('tr[data-row-id="0"]');
            const tr1 = tbody.querySelector('tr[data-row-id="1"]');
            return {
                r0_lemma: tr0.querySelector('td[data-col="CustomLemma"]').textContent.trim(),
                r0_inflected: tr0.querySelector('td[data-col="CustomInflected"]').textContent.trim(),
                r0_trans: tr0.querySelector('td[data-col="CustomTranslation"]').textContent.trim(),
                r0_pos: tr0.querySelector('td[data-col="WordSourcePOS"]').textContent.trim(),
                r0_checkbox_present: Boolean(tr0.querySelector('td.kw-tag-control input[type="checkbox"]')),
                r1_ipa: tr1.querySelector('td[data-col="WordSourceIPA"]').textContent.trim(),
                r1_pos: tr1.querySelector('td[data-col="WordSourcePOS"]').textContent.trim(),
            };
        }"""
    )

    assert result["r0_checkbox_present"] is True
    assert result["r0_lemma"] == "Haus", f"Lemma cell was corrupted by positional fallback: {result}"
    assert result["r0_inflected"] == "Häuser", f"Inflected cell was corrupted by positional fallback: {result}"
    assert result["r0_trans"] == "house", f"Custom translation column was not updated semantically: {result}"
    assert result["r0_pos"] == "n.", f"Hidden optional IPA/Morphology corrupted POS cell via positional index: {result}"
    assert result["r1_ipa"] == "/baʊm/", f"Canonical WordSourceIPA spelling failed to update: {result}"
    assert result["r1_pos"] == "n."


def test_1_2_static_ssr_and_lookup_shell_assets_order_and_non_element_guards(page, test_server):
    """Static/SSR/lookup shells must include workspace presentation assets in order and guard non-Element targets."""
    port = test_server
    base_url = f"http://127.0.0.1:{port}"

    # 1. Static web/index.html must include workspace.css and workspace-presentation.js in valid order.
    with urllib.request.urlopen(f"{base_url}/index.html") as resp:
        static_html = resp.read().decode("utf-8")
    assert '/css/workspace.css' in static_html, "Static web/index.html is missing /css/workspace.css"
    assert '/js/features/workspace-presentation.js' in static_html, (
        "Static web/index.html is missing /js/features/workspace-presentation.js"
    )
    assert static_html.index('/js/features/sqlite-state.js') < static_html.index('/js/features/workspace-presentation.js')
    assert static_html.index('/js/features/workspace-presentation.js') < static_html.index('/js/app.js')

    # 2. Check WorkspacePresentation non-Element event target and missing-node guards directly.
    page_errors = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    page.goto(f"{base_url}/index.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.SqliteState === 'object'")

    # Load workspace-presentation.js if not yet in static shell, and exercise missing retry node & Text/document target.
    guard_result = page.evaluate(
        """() => {
            if (!window.WorkspacePresentation) return {present: false};
            const retryBtn = document.getElementById('kw-workspace-retry');
            const retryParent = retryBtn ? retryBtn.parentNode : null;
            if (retryBtn) retryBtn.remove();
            let bindError = null;
            try {
                window.WorkspacePresentation.bind({ start: () => Promise.resolve(), dispose: () => {} });
            } catch (e) {
                bindError = String(e);
            }
            window.WorkspacePresentation.project('failed', new Error('owned test error'));
            let textTargetError = null;
            try {
                const txt = document.createTextNode('hello');
                document.getElementById('source-container').appendChild(txt);
                txt.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                document.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true, cancelable: true}));
            } catch (e) {
                textTargetError = String(e);
            }
            if (retryBtn && retryParent) retryParent.appendChild(retryBtn);
            return {present: true, bindError, textTargetError};
        }"""
    )
    assert guard_result["present"] is True, "WorkspacePresentation was not loaded by static shell"
    assert guard_result["bindError"] is None, f"Missing #kw-workspace-retry threw: {guard_result['bindError']}"
    assert guard_result["textTargetError"] is None, f"Non-Element target threw: {guard_result['textTargetError']}"
    assert page_errors == [], f"Unexpected page errors during shell presentation guards: {page_errors}"

    # 3. Lookup HTML startup must not emit uncaught page errors.
    config, resolved_paths, goldendict, _ = desk.load_config(ROOT / "config.ini")
    lookup_html = desk.render_lookup_html(
        text="Fuchs springt.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=resolved_paths,
        zid="20261124213199",
        goldendict=goldendict,
        comments=[],
        headers=["WordSourceInflectedForm", "WordSource", "WordSourcePOS", "WordDestination"],
        data_rows=[["Fuchs", "Fuchs", "n.", "лиса"]],
        sentence_translation="Лиса прыгает.",
    )
    lookup_errors = []
    lookup_page = page.context.new_page()
    try:
        lookup_page.on("pageerror", lambda err: lookup_errors.append(str(err)))
        lookup_page.set_content(lookup_html)
        lookup_page.wait_for_selector("#lemma-table tbody tr")
        assert lookup_errors == [], f"Lookup HTML startup emitted errors: {lookup_errors}"
    finally:
        lookup_page.close()
