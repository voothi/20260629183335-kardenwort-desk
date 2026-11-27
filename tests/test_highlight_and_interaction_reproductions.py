"""Phase 1 Playwright reproduction tests for highlight projection, hover/click, drag, keyboard, Hand Tool, and CSS."""
import json
import pytest

import kardenwort_desk as desk
from tests.render_test_support import seed_render_rows
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_same_lemma_toggle import (
    _assert_source_roles,
    _open_row_highlight_document,
    _row_highlight_document,
    _source_words,
    _switch_highlight_sentence,
    inject_mock_fetch,
)


HEADERS = [
    "Quotation",
    "WordSource",
    "WordSourceInflectedForm",
    "WordSourcePOS",
    "WordDestination",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "DeskSelected",
]


def _seed_sqlite_multi(server, zid, text, rows, language="de", text_mode="multi"):
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    server.config.read_dict({
        "sentences_mode": {"enabled": "true", "delivery_mode": "container"},
        "settings": {"unify_article_pronoun_lemmas": "false", "deduplicate_pos_aware": "true"},
    })
    adapter.save_session(
        session_zid=zid,
        source_language=language,
        target_language="ru",
        source_raw_text=text,
        text_mode=text_mode,
        headers=HEADERS,
        data_rows=rows,
    )
    return adapter


def _ready(page):
    page.wait_for_function("window.__stateReady !== undefined")
    page.evaluate("window.__stateReady")


def _open_session(page, url, server, zid, seq_num=1):
    page.context.set_extra_http_headers({"X-API-Token": server.api_key})
    page.goto(f"{url}/?session_zid={zid}&seq_num={seq_num}")
    _ready(page)


def _render_custom_doc(
    tmp_path,
    text,
    rows,
    *,
    zid="20261127190001",
    language="de",
    theme="dark",
    text_mode="single",
    highlight_same_lemma=False,
    read_only_preview=False,
    mismatch_info=None,
    extra_config=None,
):
    config, paths, _, _ = desk.load_config()
    config.read_dict({
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
        "rendering": {
            "display_mode": "monolithic",
            "hover_highlight": "true",
            "hover_highlight_bookmarks": "3",
            "hover_highlight_rainbow": "true",
            "highlight_same_lemma": "true" if highlight_same_lemma else "false",
        },
        "pipeline": {"progressive_text_translation": "false"},
        "settings": {"unify_article_pronoun_lemmas": "false", "deduplicate_pos_aware": "true"},
    })
    if extra_config:
        config.read_dict(extra_config)
    from kardenwort_state_runtime import preference_store

    preference_store(config, paths).set("highlight_same_lemma", highlight_same_lemma)
    seed_render_rows(config, paths, zid, text, language, HEADERS, rows, text_mode=text_mode)
    return desk.run_render_flow(
        text=text,
        language=language,
        zid=zid,
        text_mode=text_mode,
        config=config,
        resolved_paths=paths,
        theme=theme,
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
        read_only_preview=read_only_preview,
        mismatch_info=mismatch_info,
    )


def _active_or_peer_classes(locator):
    return locator.evaluate_all(
        """els => els.map(el =>
            ['highlight-orange-active', 'highlight-purple-active', 'lemma-peer-highlight']
                .filter(c => el.classList.contains(c))
        )"""
    )


def test_h1_tab_switch_and_sse_display_active_focus(page, preference_world):
    url, server, _ = preference_world
    zid = "20261127190101"
    s1 = "Der Mann geht."
    s2 = "Der Hund läuft."
    text = f"{s1}\n{s2}"
    rows = [
        ["Der", "der", "Der", "ART", "этот", "1", s1, "Мужчина идет.", "0"],
        ["Mann", "Mann", "Mann", "NOUN", "мужчина", "1", s1, "Мужчина идет.", "0"],
        ["Der", "der", "Der", "ART", "этот", "2", s2, "Собака бежит.", "0"],
        ["Hund", "Hund", "Hund", "NOUN", "собака", "2", s2, "Собака бежит.", "0"],
    ]
    _seed_sqlite_multi(server, zid, text, rows)
    _open_session(page, url, server, zid)

    # Part A: Click source token 'Der' on Card 2 (sent 1), then switch to Card 3 (sent 2).
    _switch_highlight_sentence(page, 1)
    page.locator('#source-container .kw-sentence-chunk[data-sentence-idx="1"] span.word[data-lower-clean="der"]').first.click()
    page.evaluate("flushPendingSaves()")

    _switch_highlight_sentence(page, 2)
    card3_der = page.locator('#source-container .kw-sentence-chunk[data-sentence-idx="2"] span.word[data-lower-clean="der"]').first
    assert "highlight-orange-active" in (card3_der.get_attribute("class") or ""), (
        "H1: Switching to Card 3 after clicking a token on Card 2 suppressed selected-row highlight"
    )

    # Part B: Fresh page receiving remote SSE shared_selection must project highlights without prior clicks.
    other = page.context.new_page()
    try:
        _open_session(other, url, server, zid)
        other.evaluate("window.applySharedSelection({der: true, mann: true})")
        der_spans = other.locator('#source-container span.word[data-lower-clean="der"]')
        classes = _active_or_peer_classes(der_spans)
        assert any("highlight-orange-active" in c for c in classes), (
            f"H1: Remote SSE applySharedSelection did not project highlights on fresh client: {classes}"
        )
    finally:
        other.close()


def test_h2_mixed_table_row_and_unrelated_token_click(page, tmp_path):
    text = "Der Mann sieht der Frau bei der Arbeit zu."
    html, _ = _row_highlight_document(
        tmp_path,
        text,
        [
            ("Der, der", "der", "ART", 1),
            ("Arbeit", "Arbeit", "NOUN", 1),
        ],
    )
    _open_row_highlight_document(page, html)

    # Lemma is OFF. Select 'der' row in table -> only primary (1st) 'der' is highlighted.
    der_row = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    der_row.click()
    _assert_source_roles(page, ["der"], ["primary", "baseline", "baseline"])

    # Click unrelated source word 'Arbeit' -> 'der' must keep ONLY its primary occurrence active.
    _source_words(page, ["arbeit"]).first.click()
    _assert_source_roles(page, ["arbeit"], ["primary"])
    _assert_source_roles(page, ["der"], ["primary", "baseline", "baseline"])

    # Toggle Lemma ON -> secondary 'der' occurrences become muted peers, NOT solid primary active.
    page.locator("#kw-btn-same-lemma").click()
    _assert_source_roles(page, ["der"], ["primary", "peer", "peer"])


def test_h3_compound_subtoken_standalone_and_duplicate_subtokens(page, tmp_path):
    # Part A: E-Mail-Postfach co-occurring with E-Mail and standalone Postfach.
    text_a = "Das E-Mail-Postfach und die E-Mail im Postfach."
    rows_a = [
        ["E-Mail-Postfach", "E-Mail-Postfach", "E-Mail-Postfach", "NOUN", "почтовый ящик", "1", text_a, "T", "0"],
        ["E-Mail", "E-Mail", "E-Mail", "NOUN", "письмо", "1", text_a, "T", "0"],
        ["Mail", "Mail", "Mail", "NOUN", "почта", "1", text_a, "T", "0"],
        ["Postfach", "Postfach", "Postfach", "NOUN", "ящик", "1", text_a, "T", "0"],
    ]
    html_a = _render_custom_doc(tmp_path, text_a, rows_a, zid="20261127190301", highlight_same_lemma=False)
    page.set_content(inject_mock_fetch(html_a))
    page.wait_for_selector("#source-container span.word")

    # Click ONLY 'Mail' inside E-Mail-Postfach (first occurrence of 'mail').
    _source_words(page, ["mail"]).nth(0).click()
    _assert_source_roles(page, ["mail"], ["primary", "baseline"])

    # Toggle Lemma ON -> second 'Mail' (inside E-Mail) must be peer, NOT solid primary active.
    page.locator("#kw-btn-same-lemma").click()
    _assert_source_roles(page, ["mail"], ["primary", "peer"])

    # Clear, turn Lemma OFF, click 'Postfach' inside E-Mail-Postfach -> standalone Postfach must not become primary.
    page.keyboard.press("Escape")
    page.locator("#kw-btn-same-lemma").click()
    _source_words(page, ["postfach"]).nth(0).click()
    _assert_source_roles(page, ["postfach"], ["primary", "baseline"])
    page.locator("#kw-btn-same-lemma").click()
    _assert_source_roles(page, ["postfach"], ["primary", "peer"])

    # Part B: Duplicate subtokens inside one compound (Hand-in-Hand).
    text_b = "Sie gehen Hand-in-Hand weiter."
    rows_b = [
        ["Hand-in-Hand", "Hand-in-Hand", "Hand-in-Hand", "ADV", "рука об руку", "1", text_b, "T", "0"],
        ["Hand", "Hand", "Hand", "NOUN", "рука", "1", text_b, "T", "0"],
    ]
    html_b = _render_custom_doc(tmp_path, text_b, rows_b, zid="20261127190302", highlight_same_lemma=False)
    page.set_content(inject_mock_fetch(html_b))
    page.wait_for_selector("#source-container span.word")

    _source_words(page, ["hand"]).nth(0).click()
    _assert_source_roles(page, ["hand"], ["primary", "baseline"])
    page.locator("#kw-btn-same-lemma").click()
    _assert_source_roles(page, ["hand"], ["primary", "peer"])


def test_h4_repeated_separable_verb_in_one_sentence(page, tmp_path):
    text = "Er fängt heute an und er fängt morgen an."
    html, _ = _row_highlight_document(tmp_path, text, [("fängt + an", "anfangen", "VERB", 1)])
    _open_row_highlight_document(page, html)

    # Click first 'fängt' with Lemma OFF -> ONLY first pair (fängt#1, an#1) gets highlight-purple-active.
    _source_words(page, ["fängt"]).nth(0).click()
    _assert_source_roles(page, ["fängt", "an"], ["primary", "primary", "baseline", "baseline"], primary="highlight-purple-active")

    # Toggle Lemma ON -> second pair gets muted peer highlight, NOT solid purple active.
    page.locator("#kw-btn-same-lemma").click()
    _assert_source_roles(page, ["fängt", "an"], ["primary", "primary", "peer", "peer"], primary="highlight-purple-active")


def test_h5_h6_translation_hover_flipped_lemma_alt_mouseout_and_click_persistence(page, tmp_path):
    text = "Der Mann sieht der Frau."
    rows = [
        ["Mann", "Mann", "Mann", "NOUN", "мужчина", "1", text, "Женщину видит этот мужчина.", "0"],
        ["Der, der", "der", "Der, der", "ART", "этот", "1", text, "Женщину видит этот мужчина.", "0"],
        ["Frau", "Frau", "Frau", "NOUN", "женщина", "1", text, "Женщину видит этот мужчина.", "0"],
    ]
    html = _render_custom_doc(tmp_path, text, rows, zid="20261127190501", highlight_same_lemma=True)
    page.set_content(inject_mock_fetch(html))
    page.evaluate(
        """() => {
            window.AppState.applyDeltas({
                translatedText: '<div data-sentence-idx="1" data-line-idx="0">Женщину видит этот мужчина.</div>',
                stage: 'finished'
            });
        }"""
    )

    # H5a: Hovering translation span 0 ('Женщину', which aligns to source 0 'Der', NOT row 0 'Mann')
    # must NOT index translation span directly into source tokenMap by data-order/data-word-idx.
    trans_first = page.locator("#translation-container span.word.hl-mvp").nth(0)
    trans_first.evaluate("el => el.setAttribute('data-word-idx', '2')")
    trans_first.hover()
    row_mann_hovered = page.locator('#lemma-table tbody tr[data-row-id="0"]').evaluate("el => el.classList.contains('hl-mvp-hover')")
    assert row_mann_hovered is False, "H5: Translation span hover indexed source tokenMap via data-word-idx and highlighted wrong table row"
    page.mouse.move(0, 0)

    # H5b: Releasing hover while Alt is held must clear both .hl-mvp-hover and transient .lemma-peer-highlight.
    der_first = _source_words(page, ["der"]).nth(0)
    der_first.hover()
    assert page.locator("#source-container span.word.lemma-peer-highlight").count() >= 1
    page.keyboard.down("Alt")
    page.mouse.move(0, 0)
    page.keyboard.up("Alt")
    assert page.locator(".hl-mvp-hover").count() == 0, "H5: Alt-held mouseout leaked .hl-mvp-hover"
    assert page.locator("#source-container span.word.lemma-peer-highlight").count() == 0, (
        "H5: Alt-held mouseout leaked transient .lemma-peer-highlight"
    )

    # H5c: RMB-flipping a source span must still resolve peers via canonical data-lower-clean/manifest, not flipped textContent.
    der_first.click(button="right")
    assert "flipped" in (der_first.get_attribute("class") or "")
    der_first.hover()
    assert "lemma-peer-highlight" in (_source_words(page, ["der"]).nth(1).get_attribute("class") or ""), (
        "H5: Hovering RMB-flipped source span lost canonical lemma peer lookup"
    )
    page.mouse.move(0, 0)
    der_first.click(button="right")

    # H6: Clicking translation span corresponding to 2nd 'der' persists via notifyAHKSelection, highlights 2nd 'der',
    # and deselecting while 1st 'der' is also active keeps row 'der' selected.
    page.evaluate("window.__fetches = []")
    trans_for_second_der = page.locator("#translation-container span.word.hl-mvp").nth(2)
    trans_for_second_der.click()
    assert "highlight-orange-active" in (_source_words(page, ["der"]).nth(1).get_attribute("class") or ""), (
        "H6: Clicking translation span did not apply primary highlight to mapped second occurrence"
    )
    assert "hl-mvp-pin" in (_source_words(page, ["der"]).nth(1).get_attribute("class") or "")
    fetches = page.evaluate("() => window.__fetches")
    assert len(fetches) >= 1, "H6: Translation-span click did not persist selection via notifyAHKSelection()"

    # Also select first 'der' in source, then click translation span again to deselect 2nd 'der':
    # row 'der' must remain selected because 1st 'der' still needs it.
    _source_words(page, ["der"]).nth(0).click()
    trans_for_second_der.click()
    selected_ids = json.loads(page.evaluate("window.getSelectedRows()"))
    assert 1 in selected_ids, f"H6: Deselecting translation span wiped row 1 still needed by active 1st 'der': {selected_ids}"


def test_h7_reversible_table_marquee_drag_and_container_constituents(page, tmp_path):
    text = "Eins zwei drei vier."
    rows = [
        ["Eins", "eins", "Eins", "NUM", "один", "1", text, "T", "0"],
        ["zwei", "zwei", "zwei", "NUM", "два", "1", text, "T", "0"],
        ["drei", "drei", "drei", "NUM", "три", "1", text, "T", "0"],
        ["vier", "vier", "vier", "NUM", "четыре", "1", text, "T", "0"],
    ]
    html = _render_custom_doc(
        tmp_path,
        text,
        rows,
        zid="20261127190701",
        text_mode="multi",
        extra_config={"sentences_mode": {"enabled": "true", "delivery_mode": "container"}},
    )
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")

    # Inject a merged constituent ID on row 1 to verify container mode preserves data-all-row-ids during drag.
    page.evaluate(
        """() => {
            const tr1 = document.querySelectorAll('#lemma-table tbody tr[data-row-id]')[1];
            tr1.setAttribute('data-all-row-ids', tr1.getAttribute('data-row-id') + ',99');
        }"""
    )

    tr_boxes = [
        page.locator("#lemma-table tbody tr[data-row-id]").nth(i).bounding_box()
        for i in range(4)
    ]
    centers = [(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2) for b in tr_boxes]

    # Drag down across rows 0..3, then reverse back up to row 1 before releasing.
    page.mouse.move(*centers[0])
    page.mouse.down(button="left")
    page.mouse.move(*centers[1])
    page.mouse.move(*centers[2])
    page.mouse.move(*centers[3])
    page.mouse.move(*centers[1])
    page.mouse.up(button="left")

    selected_lemmas = page.evaluate("() => Object.keys(window.AppState.selectedLemmas).filter(k => window.AppState.selectedLemmas[k]).sort()")
    assert selected_lemmas == ["eins", "zwei"], (
        f"H7: Reversing table marquee drag leaked swept lemmas in AppState.selectedLemmas: {selected_lemmas}"
    )
    selected_ids = json.loads(page.evaluate("window.getSelectedRows()"))
    assert 99 in selected_ids, f"H7: Container-mode table drag dropped merged data-all-row-ids constituent 99: {selected_ids}"


def test_h8_sse_remote_deselect_and_restore_client_view_sqlite_authority(page, preference_world):
    url, server, _ = preference_world
    zid = "20261127190801"
    text = "Der Mann sieht der Frau."
    rows = [
        ["Der, der", "der", "Der, der", "ART", "этот", "1", text, "T", "0"],
        ["Mann", "Mann", "Mann", "NOUN", "мужчина", "1", text, "T", "0"],
    ]
    from kardenwort_state_runtime import preference_store

    preference_store(server.config, server.resolved_paths).set("highlight_same_lemma", True)
    adapter = _seed_sqlite_multi(server, zid, text, rows, text_mode="single")
    _open_session(page, url, server, zid)

    # Tab A clicks first 'Der' with Lemma ON.
    _source_words(page, ["der"]).nth(0).click()
    page.evaluate("flushPendingSaves()")
    assert page.evaluate("window.AppState.activeTokenSelections.length") == 1
    assert "highlight-orange-active" in (_source_words(page, ["der"]).nth(0).get_attribute("class") or "")
    assert "lemma-peer-highlight" in (_source_words(page, ["der"]).nth(1).get_attribute("class") or "")

    # Simulate remote Tab B deselecting 'der' in SQLite and broadcasting SSE shared_selection.
    adapter.update_word_selection(zid, 1, 0, False)
    assert adapter.get_shared_selection(zid).get("der") is False
    page.evaluate("window.applySharedSelection({der: false})")

    assert page.evaluate("window.AppState.activeTokenSelections.length") == 0, (
        "H8: applySharedSelection did not prune activeTokenSelections for remotely deselected lemma"
    )
    assert page.locator("#source-container span.word.highlight-orange-active").count() == 0
    assert page.locator("#source-container span.word.lemma-peer-highlight").count() == 0
    assert page.locator("#source-container span.word.hl-mvp-pin").count() == 0

    # Reload Tab A: SQLite has der=False, so restoreClientView must NOT resurrect 'der'.
    # Re-seed a stale active_tokens entry in client_view_state directly to test reload authority too.
    from kardenwort_client_views import ClientViewAdapter
    import re

    client_id = re.search(r"kw-client=([0-9a-f]{32})", page.url).group(1)
    manifest = page.evaluate('() => JSON.parse(document.getElementById("token-map").textContent)')
    der_tok = next(t for t in manifest if t.get("lower_clean") == "der")
    ClientViewAdapter(adapter.db).save(
        zid,
        client_id,
        active_card=1,
        active_tokens=[{
            "visual_idx": der_tok["visual_idx"],
            "lemma": "der",
            "atomic_id": 0,
            "atomic_row_ids": [0],
            "row_ids": [0],
            "sentence_idx": 1,
        }],
    )
    page.reload()
    _ready(page)

    assert page.evaluate("Boolean(window.AppState.selectedLemmas['der'])") is False, (
        "H8: restoreClientView resurrected 'der' deselected in SQLite"
    )
    assert page.locator("#source-container span.word.highlight-orange-active").count() == 0
    assert page.locator("#source-container span.word.lemma-peer-highlight").count() == 0


def test_c2_ctrl_shift_a_toggles_hand_tool_without_clearing_selections(page, tmp_path):
    text = "Eins zwei drei."
    rows = [
        ["Eins", "eins", "Eins", "NUM", "один", "1", text, "T", "1"],
        ["zwei", "zwei", "zwei", "NUM", "два", "1", text, "T", "1"],
        ["drei", "drei", "drei", "NUM", "три", "1", text, "T", "1"],
    ]
    html = _render_custom_doc(tmp_path, text, rows, zid="20261127190901")
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")

    assert len(json.loads(page.evaluate("window.getSelectedRows()"))) == 3
    page.keyboard.press("Control+Shift+A")

    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')") is True, (
        "C2: Ctrl+Shift+A did not activate Hand Tool (text-selection-mode-active)"
    )
    assert page.evaluate("document.getElementById('kw-btn-hand-tool').classList.contains('active')") is True
    assert len(json.loads(page.evaluate("window.getSelectedRows()"))) == 3, (
        "C2: Ctrl+Shift+A destroyed user selections instead of toggling Hand Tool"
    )


def test_h11_read_only_preview_blocks_selection_mutations(page, tmp_path):
    text = "Das Haus."
    rows = [
        ["Das", "der", "Das", "ART", "этот", "1", text, "Этот дом.", "0"],
        ["Haus", "Haus", "Haus", "NOUN", "дом", "1", text, "Этот дом.", "0"],
    ]
    html = _render_custom_doc(tmp_path, text, rows, zid="20261127191101", read_only_preview=True)
    page.set_content(inject_mock_fetch(html))
    page.evaluate(
        """() => {
            window.AppState.applyDeltas({
                translatedText: '<div data-sentence-idx="1" data-line-idx="0">Этот дом.</div>',
                stage: 'finished'
            });
        }"""
    )

    page.locator("#lemma-table tbody tr[data-row-id]").first.click()
    assert json.loads(page.evaluate("window.getSelectedRows()")) == [], (
        "H11: read_only_preview allowed table row click to mutate selections"
    )
    assert page.locator("#lemma-table tbody tr.selected, #lemma-table tbody tr.kw-row-selected").count() == 0

    page.locator("#source-container span.word").first.click()
    assert json.loads(page.evaluate("window.getSelectedRows()")) == [], (
        "H11: read_only_preview allowed source span click to mutate selections"
    )
    assert page.evaluate("window.AppState.activeTokenSelections.length") == 0

    page.locator("#translation-container span.word.hl-mvp").first.click()
    assert json.loads(page.evaluate("window.getSelectedRows()")) == [], (
        "H11: read_only_preview allowed translation span click to mutate selections"
    )
    assert page.evaluate("window.AppState.activeTokenSelections.length") == 0


def test_h12_hand_tool_css_specificity_and_hover_suppression(page, tmp_path):
    text = "Der Mann sieht der Frau."
    rows = [
        ["Der, der", "der", "Der, der", "ART", "этот", "1", text, "T", "0"],
        ["Mann", "Mann", "Mann", "NOUN", "мужчина", "1", text, "T", "0"],
    ]
    html = _render_custom_doc(tmp_path, text, rows, zid="20261127191201", theme="dark", highlight_same_lemma=True)
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector("#source-container span.word")

    first_der = _source_words(page, ["der"]).nth(0)
    second_der = _source_words(page, ["der"]).nth(1)
    first_der.click()
    assert "highlight-orange-active" in (first_der.get_attribute("class") or "")
    assert "hl-mvp-pin-0" in (first_der.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (second_der.get_attribute("class") or "")

    # Enable Hand Tool.
    page.evaluate("window.setSelectableTextMode(true, true)")

    def _styles(loc):
        return loc.evaluate(
            """el => {
                const cs = getComputedStyle(el);
                return { bg: cs.backgroundColor, shadow: cs.boxShadow, outlineStyle: cs.outlineStyle };
            }"""
        )

    before_active = _styles(first_der)
    before_peer = _styles(second_der)
    assert before_active["bg"] == "rgba(0, 0, 0, 0)", f"H12: Active highlight leaked bg in Hand Tool: {before_active}"
    assert before_active["shadow"] == "none", f"H12: Pin boxShadow leaked in Hand Tool: {before_active}"
    assert before_peer["bg"] == "rgba(0, 0, 0, 0)", f"H12: Peer highlight leaked bg in Hand Tool: {before_peer}"
    assert before_peer["outlineStyle"] == "none", f"H12: Peer outline leaked in Hand Tool: {before_peer}"

    # Hovering while Hand Tool is active must not flicker background/shadow back on.
    first_der.hover()
    hover_active = _styles(first_der)
    assert hover_active["bg"] == "rgba(0, 0, 0, 0)", f"H12: Active span flickered bg on hover in Hand Tool: {hover_active}"
    assert hover_active["shadow"] == "none", f"H12: Active span flickered shadow on hover in Hand Tool: {hover_active}"


def test_m1_modal_escape_preserves_selection_and_delete_routes_on_delete_click(page, tmp_path):
    text = "Das Haus ist groß."
    rows = [
        ["Haus", "Haus", "Haus", "NOUN", "дом", "1", text, "T", "1"],
        ["groß", "groß", "groß", "ADJ", "большой", "1", text, "T", "1"],
    ]
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": text,
        "session_zid": "20261127191301",
    }
    html = _render_custom_doc(tmp_path, text, rows, zid="20261127191301")
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")

    assert len(json.loads(page.evaluate("window.getSelectedRows()"))) == 2
    page.evaluate("info => window.showLanguageVerificationModal(info)", mismatch)
    assert page.locator("#kw-lang-modal").is_visible()

    page.keyboard.press("Escape")
    assert not page.locator("#kw-lang-modal").is_visible()
    assert len(json.loads(page.evaluate("window.getSelectedRows()"))) == 2, (
        "M1: Pressing Escape to close #kw-lang-modal wiped user row selections"
    )

    # With modal closed, Delete key must route through window.onDeleteClick (not bypass it).
    page.evaluate(
        """() => {
            window.__onDeleteCalls = 0;
            window.__deleteSelectedCalls = 0;
            const origOnDelete = window.onDeleteClick;
            const origDelSel = window.deleteSelectedRows;
            window.onDeleteClick = function() { window.__onDeleteCalls++; return origOnDelete && origOnDelete.apply(this, arguments); };
            window.deleteSelectedRows = function() { window.__deleteSelectedCalls++; return origDelSel && origDelSel.apply(this, arguments); };
        }"""
    )
    page.keyboard.press("Delete")
    assert page.evaluate("window.__onDeleteCalls") == 1, "M1: Delete key did not invoke window.onDeleteClick()"
    assert page.evaluate("window.__deleteSelectedCalls") == 1, "M1: Delete key invoked deleteSelectedRows multiple times or bypassed onDeleteClick"


def test_m5_table_css_chip_weight_pin_border_toolbar_var_and_compact_dock(page, tmp_path):
    text = "Erster Satz.\nZweiter Satz."
    rows = [
        ["Erster", "erste", "Erster", "ADJ", "первый", "1", "Erster Satz.", "T1", "0"],
        ["Zweiter", "zweite", "Zweiter", "ADJ", "второй", "2", "Zweiter Satz.", "T2", "0"],
    ]
    html = _render_custom_doc(
        tmp_path,
        text,
        rows,
        zid="20261127191501",
        theme="compact",
        text_mode="multi",
        extra_config={"sentences_mode": {"enabled": "true", "delivery_mode": "container"}},
    )
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector(".kw-tab-chip.active")

    # 1. Chip font-weight invariance (500 on both inactive and active).
    weights = page.evaluate(
        """() => {
            const active = document.querySelector('.kw-tab-chip.active');
            const inactive = document.querySelector('.kw-tab-chip:not(.active)');
            return {
                activeWeight: getComputedStyle(active).fontWeight,
                inactiveWeight: getComputedStyle(inactive).fontWeight,
            };
        }"""
    )
    assert weights["activeWeight"] == "500" and weights["inactiveWeight"] == "500", (
        f"M5: Tab chip font-weight diverges between active and inactive: {weights}"
    )

    # 2. First cell transparent pin border must be 4px.
    border_left = page.evaluate(
        """() => getComputedStyle(document.querySelector('#lemma-table tbody tr td:first-child')).borderLeftWidth"""
    )
    assert border_left == "4px", f"M5: Expected 4px left pin border, got {border_left}"

    # 3. theme-compact docked tab bar must apply light dock styling (not dark fallback).
    dock_bg = page.evaluate(
        """() => {
            const bar = document.querySelector('.kw-workspace-tab-bar');
            bar.classList.add('kw-tab-dock-top');
            return getComputedStyle(bar).backgroundColor;
        }"""
    )
    assert dock_bg == "rgba(255, 255, 255, 0.82)", f"M5: theme-compact docked tab bar fell back to dark background: {dock_bg}"

    # 4. Dark theme .kw-tab-dock-top:hover must honor --toolbar-bg instead of hardcoded #161b22.
    page.evaluate(
        """() => {
            document.body.classList.remove('theme-compact');
            document.body.classList.add('theme-dark');
            document.body.style.setProperty('--toolbar-bg', 'rgb(12, 34, 56)');
        }"""
    )
    page.locator(".kw-workspace-tab-bar").hover()
    hover_bg = page.evaluate("() => getComputedStyle(document.querySelector('.kw-workspace-tab-bar')).backgroundColor")
    assert hover_bg == "rgb(12, 34, 56)", f"M5: .kw-tab-dock-top:hover ignored --toolbar-bg: {hover_bg}"


def test_m6_tab_switch_resets_shift_click_anchor(page, tmp_path):
    s1 = "Eins zwei drei vier."
    s2 = "Fünf sechs sieben acht."
    text = f"{s1}\n{s2}"
    rows = [
        ["Eins", "eins", "Eins", "NUM", "1", "1", s1, "T1", "0"],
        ["zwei", "zwei", "zwei", "NUM", "2", "1", s1, "T1", "0"],
        ["drei", "drei", "drei", "NUM", "3", "1", s1, "T1", "0"],
        ["vier", "vier", "vier", "NUM", "4", "1", s1, "T1", "0"],
        ["Fünf", "fünf", "Fünf", "NUM", "5", "2", s2, "T2", "0"],
        ["sechs", "sechs", "sechs", "NUM", "6", "2", s2, "T2", "0"],
        ["sieben", "sieben", "sieben", "NUM", "7", "2", s2, "T2", "0"],
        ["acht", "acht", "acht", "NUM", "8", "2", s2, "T2", "0"],
    ]
    html = _render_custom_doc(
        tmp_path,
        text,
        rows,
        zid="20261127191601",
        text_mode="multi",
        extra_config={"sentences_mode": {"enabled": "true", "delivery_mode": "container"}},
    )
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector("#lemma-table tbody tr[data-row-id]")

    # On Sentence 1 (Card 2), click row index 3 ('vier') -> sets lastClickedVisualIdx = 3.
    _switch_highlight_sentence(page, 1)
    page.locator("#lemma-table tbody tr[data-row-id]").nth(3).click()

    # Switch to Sentence 2 (Card 3) and Shift+Click row index 0 ('Fünf').
    _switch_highlight_sentence(page, 2)
    page.locator("#lemma-table tbody tr[data-row-id]").nth(0).click(modifiers=["Shift"])

    selected_on_card3 = page.locator("#lemma-table tbody tr.selected, #lemma-table tbody tr.kw-row-selected").count()
    assert selected_on_card3 == 1, (
        f"M6: Shift+Click on new tab used stale anchor from previous tab and selected {selected_on_card3} rows"
    )


def test_m7_missing_manifest_token_does_not_leak_kw_table_dragging(page, tmp_path):
    text = "Das Haus."
    rows = [
        ["Haus", "Haus", "Haus", "NOUN", "дом", "1", text, "T", "0"],
    ]
    html = _render_custom_doc(tmp_path, text, rows, zid="20261127191701")
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector("#source-container span.word")

    # Point a source span at an index missing from tokenMap so findTokenData(span) returns null.
    page.evaluate(
        """() => {
            const span = document.querySelector('#source-container span.word');
            span.setAttribute('data-word-idx', '99999');
            span.dispatchEvent(new MouseEvent('mousedown', { button: 0, bubbles: true, cancelable: true }));
        }"""
    )
    has_dragging = page.evaluate("() => document.getElementById('lemma-table').classList.contains('kw-table-dragging')")
    assert has_dragging is False, "M7: mousedown on span with missing manifest data leaked kw-table-dragging"
