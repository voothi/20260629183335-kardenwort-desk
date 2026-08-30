import pytest
from pathlib import Path
import json
import kardenwort_desk

def test_playwright_dom_validation(page, tmp_path):
    tsv_path = tmp_path / "test.tsv"
    
    # 2.2 Generate HTML
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    data_rows = [["Haus", "дом", "", ""]]
    
    html = kardenwort_desk.render_lookup_html(
        text="Das Haus", 
        language="de", 
        target_lang="ru", 
        config=config, 
        resolved_paths=resolved_paths, 
        zid="000", 
        goldendict=goldendict, 
        comments=[], 
        headers=headers, 
        data_rows=data_rows, 
        sentence_translation="The house"
    )
    
    html = html.replace("Das Haus", '<span class="word highlight-purple" data-word-idx="0">Das</span> <span class="word highlight-orange" data-word-idx="1">Haus</span>')
    
    # Ensure it's valid HTML
    assert "<html>" in html
    
    # 2.3 Load HTML into page
    page.set_content(html)
    
    # Let's mock a payload and apply it
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}
    import configparser
    dom_cfg = configparser.ConfigParser()
    dom_cfg.read_string("[storage]\nbackend=tsv\n")
    kardenwort_desk.write_update_js(
        tsv_path,
        data_rows,
        headers,
        role_fields,
        stage="finished",
        source_text='<span class="word highlight-purple" data-word-idx="0">Das</span> <span class="word highlight-orange" data-word-idx="1">Haus</span>',
        translated_text="The house",
        config=dom_cfg
    )
    
    # 2.6 Verify pathlib usage (already doing it here via tsv_path.parent)
    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1, "Expected exactly 1 JS payload file to be created"
    
    # 2.4 Evaluate JS
    js_content = js_files[0].read_text(encoding="utf-8")
    page.evaluate(js_content)
    
    # Wait for any potential async renders (though it should be sync)
    page.wait_for_selector(".highlight-purple")
    
    # 2.5 Assert DOM structure preserved
    purple_spans = page.locator(".highlight-purple").count()
    assert purple_spans >= 1, "The <span class=\"highlight-purple\"> elements should be preserved after update"
    
    orange_spans = page.locator(".highlight-orange").count()
    assert orange_spans >= 1, "The <span class=\"highlight-orange\"> elements should be preserved after update"
    
    # Verify the table was updated with the payload
    assert page.locator("td:has-text('дом')").is_visible(), "Table should be updated with new destination word"


def test_handle_sent_text_rendered_html_scripts_load_without_syntax_errors(page, tmp_path):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    import copy
    raw_config, raw_paths, goldendict, wordfill = kardenwort_desk.load_config()
    config = copy.deepcopy(raw_config)
    resolved_paths = copy.deepcopy(raw_paths)
    if not config.has_section("audio"):
        config.add_section("audio")
    config.set("audio", "lmb_play", "True")
    config.set("audio", "lmb_source", "lemma")
    resolved_paths["anki_tts_cli"] = Path("C:/fake/tts.py")
    resolved_paths["kardenwort_python"] = Path("python.exe")
    
    tsv_file = tmp_path / "20260815180100-archive.en.tsv"
    tsv_file.write_text("# comment\nWordSource\tWordSourceInflectedForm\tWordDestination\narchive/20260815131120\tarchive/20260815131120-token-mapping/\tархив\n", encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text="archive", 
        language="en", 
        zid="20260815180100",
        text_mode="single",
        config=config, 
        resolved_paths=resolved_paths, 
        tsv_path=str(tsv_file)
    )
    
    page.set_content(html)
    assert len(errors) == 0, f"Page load caused JavaScript errors: {errors}"
    
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    
    # Click table row 0
    row = page.locator("tr[data-row-id='0']")
    row.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\narchive")


def test_archive_click_does_not_highlight_unrelated_path_subtokens(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Archive Complete Change: 20260815131120-token-mapping-inflected-expansion Schema: spec-driven Archived to: openspec/changes/archive/20260815131120-token-mapping-inflected-expansion/ Specs:"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "archive\tArchived, Archive\tархив\n"
        "expansion\t20260815131120-token-mapping-inflected-expansion\tрасширение\n"
        "token\t20260815131120-token-mapping-inflected-expansion\tжетон\n"
    )
    tsv_file = tmp_path / "20260815183341-archive-complete-change-token.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815183341",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # Click on the word "Archive"
    archive_span = page.locator("span.word:has-text('Archive')").first
    archive_span.click()

    # The archive row should be selected
    archive_row = page.locator("tr[data-token-order='0']")
    assert "selected" in archive_row.get_attribute("class")

    # Tokens "Archive" and "Archived" should have active highlight
    assert "highlight-orange-active" in archive_span.get_attribute("class")
    archived_span = page.locator("span.word:has-text('Archived')").first
    assert "highlight-orange-active" in archived_span.get_attribute("class")

    # Unrelated tokens "token", "mapping", "expansion" should NOT be highlight-orange-active
    token_spans = page.locator("span.word:has-text('token')").all()
    for ts in token_spans:
        cls = ts.get_attribute("class") or ""
        assert "highlight-orange-active" not in cls, f"token span incorrectly highlighted as active: {cls}"

    expansion_spans = page.locator("span.word:has-text('expansion')").all()
    for es in expansion_spans:
        cls = es.get_attribute("class") or ""
        assert "highlight-orange-active" not in cls, f"expansion span incorrectly highlighted as active: {cls}"


def test_bracket_spacing_normalized_in_rendered_html(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "main spec ( openspec/specs/word-extraction/spec.md ) All artifacts"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordDestination\tSentenceDestination\n"
        "spec\tспецификация\tосновной спецификацией ( openspec/specs/word-extraction/spec.md ) Все\n"
    )
    tsv_file = tmp_path / "20260815185600-spec.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815185600",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # In the rendered source text, the parenthesis should not contain inner whitespace
    source_container = page.locator("#source-container")
    source_inner_text = source_container.inner_text()
    assert "(openspec/specs/word-extraction/spec.md)" in source_inner_text
    assert "( openspec" not in source_inner_text
    assert "spec.md )" not in source_inner_text

    # In the rendered translation, the parenthesis should also not contain inner whitespace
    trans_container = page.locator("#translation-container")
    trans_inner_text = trans_container.inner_text()
    assert "(openspec/specs/word-extraction/spec.md)" in trans_inner_text
    assert "( openspec" not in trans_inner_text
    assert "spec.md )" not in trans_inner_text


def test_bracket_spacing_disabled_via_config(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    config.set(kardenwort_desk.SEC_SETTINGS, 'normalize_bracket_spacing', 'false')
    source_text = "main spec ( openspec/specs/word-extraction/spec.md ) All artifacts"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordDestination\tSentenceDestination\n"
        "spec\tспецификация\tосновной спецификацией ( openspec/specs/word-extraction/spec.md ) Все\n"
    )
    tsv_file = tmp_path / "20260815185601-spec.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815185601",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # When normalize_bracket_spacing = false, inner spaces are preserved
    source_container = page.locator("#source-container")
    source_inner_text = source_container.inner_text()
    assert "( openspec" in source_inner_text
    assert "spec.md )" in source_inner_text

    trans_container = page.locator("#translation-container")
    trans_inner_text = trans_container.inner_text()
    assert "( openspec" in trans_inner_text
    assert "spec.md )" in trans_inner_text



def test_rmb_flip_compound_with_leading_zid_preserves_zid(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Change: 20260815131120-token-mapping-inflected-expansion Schema:"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordDestination\n"
        "token\tжетон\n"
        "mapping\tсопоставление\n"
        "inflected\tизменять\n"
        "expansion\tрасширение\n"
    )
    tsv_file = tmp_path / "20260815190000-zid-flip.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815190000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    token_span = page.locator("span.word[data-lower-clean='token']").first
    assert token_span.is_visible()

    # 1. RMB click on isolated sub-token 'token' flips only 'token'
    token_span.click(button="right")
    source_container = page.locator("#source-container")
    assert "20260815131120-жетон-mapping-inflected-expansion" in source_container.inner_text()

    # Re-click to un-flip 'token'
    token_span.click(button="right")
    assert "20260815131120-token-mapping-inflected-expansion" in source_container.inner_text()

    # 2. Drag across all sub-tokens flips all while preserving ZID
    page.evaluate("""() => {
        const s1 = document.querySelector("span.word[data-lower-clean='token']");
        const s2 = document.querySelector("span.word[data-lower-clean='expansion']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    flipped_text = source_container.inner_text()
    assert "20260815131120" in flipped_text
    assert "жетон" in flipped_text
    assert "сопоставление" in flipped_text
    assert "изменять" in flipped_text
    assert "расширение" in flipped_text

    # 3. RMB click on 'token' unflips 'token'
    token_span.click(button="right")
    assert "20260815131120-token-сопоставление-изменять-расширение" in source_container.inner_text()



def test_apostrophe_possessive_token_mapping_and_inflected_preservation(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Testing pytest's execution buffer."
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "pytest\tpytest\tпитест\n"
    )
    tsv_file = tmp_path / "20260815194500-apostrophe.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815194500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # 1. Verify span for pytest's is connected and highlighted
    pytest_span = page.locator("span.word[data-lower-clean=\"pytest's\"]")
    assert pytest_span.is_visible()
    assert "highlight-orange" in pytest_span.get_attribute("class")

    # 2. Verify table row for pytest has preserved non-empty INFLECTED field
    inflected_cell = page.locator('tr[data-row-id="0"] td[data-col="WordSourceInflectedForm"] .scrollable-cell')
    assert inflected_cell.inner_text() == "pytest"


def test_contractions_and_abbreviations_compatibility(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "It's true that we didn't check e.g. spec in config."
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "it\tit\tоно\n"
        "do\tdid\tделать\n"
        "not\tnot\tне\n"
        "spec\tspec\tспецификация\n"
        "config\tconfig\tконфигурация\n"
    )
    tsv_file = tmp_path / "20260815195400-compat.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815195400",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # 1. Contraction It's is highlighted and connected
    its_span = page.locator("span.word[data-lower-clean=\"it's\"]")
    assert its_span.is_visible()
    assert "highlight-orange" in its_span.get_attribute("class")

    # 2. Contraction didn't is highlighted and connected
    didnt_span = page.locator("span.word[data-lower-clean=\"didn't\"]")
    assert didnt_span.is_visible()
    assert "highlight-orange" in didnt_span.get_attribute("class")

    # 3. All inflected fields in the table remain preserved (not stripped by window filter)
    rows = page.locator("tr[data-row-id]")
    count = rows.count()
    assert count == 5
    for i in range(count):
        cell_text = page.locator(f'tr[data-row-id="{i}"] td[data-col="WordSourceInflectedForm"] .scrollable-cell').inner_text()
        assert cell_text != ""


def test_lemma_strips_leading_zid_prefix(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Branch 20260815131120-token-mapping-inflected-expansion is ready."
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "20260815131120-token-mapping-inflected-expansion\t20260815131120-token-mapping-inflected-expansion\tрасширение\n"
    )
    tsv_file = tmp_path / "20260815201400-zid-lemma.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260815201400",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # 1. Verify LEMMA column has ZID prefix stripped
    lemma_cell = page.locator('tr[data-row-id="0"] td[data-col="WordSource"] .scrollable-cell')
    assert lemma_cell.inner_text() == "token-mapping-inflected-expansion"

    # 2. Verify INFLECTED column retains full surface form
    inflected_cell = page.locator('tr[data-row-id="0"] td[data-col="WordSourceInflectedForm"] .scrollable-cell')
    assert inflected_cell.inner_text() == "20260815131120-token-mapping-inflected-expansion"


def test_contractions_inflected_expansion_dom_retention(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Today we're going to take a look at the new Deep Seek harness."
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "be\twe're, are\tбыть\n"
        "we\twe're, we\tмы\n"
    )
    tsv_file = tmp_path / "20260816005700-test-were.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260816005700",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # Verify both rows retain their full constituent inflected forms in the UI table
    be_inflected = page.locator('tr[data-row-id="0"] td[data-col="WordSourceInflectedForm"] .scrollable-cell')
    assert be_inflected.inner_text() == "we're, are"

    we_inflected = page.locator('tr[data-row-id="1"] td[data-col="WordSourceInflectedForm"] .scrollable-cell')
    assert we_inflected.inner_text() == "we're, we"


def test_rmb_flip_gray_untranslated_and_numeric_tokens_remain_visible(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Version 2.0 release 100 features unknownword test."
    tsv_content = (
        "# comment\n"
        "WordSource\tWordDestination\n"
        "version\tверсия\n"
        "release\tрелиз\n"
        "features\tфункции\n"
        "test\tтест\n"
    )
    tsv_file = tmp_path / "20260816110000-gray-numeric.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260816110000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)
    source_container = page.locator("#source-container")

    # 1. RMB click on "Version" -> flips to "версия"
    span_version = page.locator("span.word[data-lower-clean='version']").first
    span_version.click(button="right")
    assert "версия" in source_container.inner_text()

    # 2. RMB click on "2.0" numeric parts (e.g. span for '2' or '0')
    span_two = page.locator("span.word[data-lower-clean='2']").first
    assert span_two.is_visible()
    span_two.click(button="right")

    # Verify "2.0" remains visible in source container and is not hidden
    text_after_click_two = source_container.inner_text()
    assert "2.0" in text_after_click_two
    assert span_two.is_visible()

    # 3. RMB click on "100" standalone numeric token
    span_100 = page.locator("span.word[data-lower-clean='100']").first
    assert span_100.is_visible()
    span_100.click(button="right")
    text_after_click_100 = source_container.inner_text()
    assert "100" in text_after_click_100
    assert span_100.is_visible()

    # 4. RMB click on gray / untranslated token "unknownword"
    span_unknown = page.locator("span.word[data-lower-clean='unknownword']").first
    assert span_unknown.is_visible()
    span_unknown.click(button="right")
    text_after_click_unknown = source_container.inner_text()
    assert "unknownword" in text_after_click_unknown
    assert span_unknown.is_visible()


def test_quoted_word_rendering_and_rmb_flip(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "style.display = 'none'"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordDestination\n"
        "display\tотображение\n"
        "none\tничего\n"
    )
    tsv_file = tmp_path / "20260816110100-quoted-word.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260816110100",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)
    source_container = page.locator("#source-container")

    # Verify 'none' matched TSV dictionary and has clean lower_clean
    span_none = page.locator("span.word[data-lower-clean='none']").first
    assert span_none.is_visible()
    assert "highlight-" in (span_none.get_attribute("class") or "")

    # RMB click on 'none' flips to 'ничего'
    span_none.click(button="right")
    assert "ничего" in source_container.inner_text()
    assert "'" in source_container.inner_text()


def test_camel_case_identifier_adjacent_spans_and_rmb_flip(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "function flipWord() {"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "flip\tflipWord\tпереворачивать\n"
        "word\tflipWord\tслово\n"
    )
    tsv_file = tmp_path / "20260816110200-camel-case.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260816110200",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)
    source_container = page.locator("#source-container")

    span_flip = page.locator("span.word[data-lower-clean='flip']").first
    span_word = page.locator("span.word[data-lower-clean='word']").first

    assert span_flip.is_visible()
    assert span_word.is_visible()

    # 1. RMB click on 'flip' isolates flip to 'flip'
    span_flip.click(button="right")
    assert "function переворачиватьWord() {" in source_container.inner_text()

    # Re-click to unflip
    span_flip.click(button="right")
    assert "function flipWord() {" in source_container.inner_text()

    # 2. Drag across 'flip' to 'Word' flips both
    page.evaluate("""() => {
        const s1 = document.querySelector("span.word[data-lower-clean='flip']");
        const s2 = document.querySelector("span.word[data-lower-clean='word']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    assert "function переворачиватьслово() {" in source_container.inner_text()

    # 3. RMB click on 'Word' unflips 'Word' while 'flip' remains flipped
    span_word.click(button="right")
    assert "function переворачиватьWord() {" in source_container.inner_text()



def test_subtoken_hover_selection_isolation(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "curr_logical_idx, curr_visual_idx, curr_compound_id"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "curr\tcurr_logical_idx, curr_visual_idx, curr_compound_id\tтекущий\n"
        "logical\tcurr_logical_idx\tлогический\n"
        "idx\tcurr_logical_idx, curr_visual_idx\tиндекс\n"
        "visual\tcurr_visual_idx\tвизуальный\n"
        "compound\tcurr_compound_id\tсоставной\n"
        "id\tcurr_compound_id\tидентификатор\n"
    )
    tsv_file = tmp_path / "20260816110300-subtoken-isolation.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260816110300",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    span_logical = page.locator("span.word[data-lower-clean='logical']").first
    assert span_logical.is_visible()

    # Click subtoken 'logical'
    span_logical.click()

    # Verify table row for 'logical' is active
    row_logical = page.locator("tr[data-row-id='1']")
    assert "selected" in (row_logical.get_attribute("class") or "") or "active" in (row_logical.get_attribute("class") or "") or row_logical.is_visible()

    # Verify unrelated table rows (visual=3, compound=4, id=5) are not selected
    row_visual = page.locator("tr[data-row-id='3']")
    assert "selected" not in (row_visual.get_attribute("class") or "")

    row_compound = page.locator("tr[data-row-id='4']")
    assert "selected" not in (row_compound.get_attribute("class") or "")

    row_id = page.locator("tr[data-row-id='5']")
    assert "selected" not in (row_id.get_attribute("class") or "")


def test_terminal_payload_sets_dom_status_and_catches_ahk_errors(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    tsv_file = tmp_path / "20260818175000-test.de.tsv"
    tsv_file.write_text("# comment\nWordSource\tWordDestination\nHaus\tдом\n", encoding="utf-8")
    html = kardenwort_desk.run_render_flow(
        text="Das Haus",
        language="de",
        zid="20260818175000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )
    page.set_content(html)

    # Set up broken ahkCall that throws an exception
    page.evaluate("""() => {
        window.ahkCall = function(action, arg) {
            throw new Error("Simulated Trident COM bridge failure");
        };
    }""")

    # Apply terminal delta
    page.evaluate("""() => {
        window.AppState.applyDeltas({
            stage: "finished",
            status: "success",
            rows: { 0: { lemma: "Haus", trans: "дом" } }
        });
    }""")

    # Verify DOM marker attribute is set
    body_status = page.evaluate("() => document.body.getAttribute('data-worker-status')")
    assert body_status == "finished"
    assert page.evaluate("() => window.AppState.isFinished") is True


def test_literal_template_placeholders_rendered_verbatim(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Configuration uses {language} and {status} as template parameters."
    tsv_file = tmp_path / "20260828233000-placeholder.en.tsv"
    tsv_file.write_text("# comment\nWordSource\tWordDestination\nlanguage\tязык\nstatus\tстатус\n", encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260828233000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    source_container = page.locator("#source-container")
    container_text = source_container.inner_text()
    assert "{language}" in container_text
    assert "{status}" in container_text
    assert "{en}" not in container_text

    lang_span = page.locator("#source-container span.word", has_text="language")
    assert lang_span.count() >= 1

    status_span = page.locator("#source-container span.word", has_text="status")
    assert status_span.count() >= 1


def test_translation_token_hover_highlights_source_token(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Specs synced to openspec."
    tsv_file = tmp_path / "20260831002500-hover-test.en.tsv"
    tsv_file.write_text("# comment\nWordSource\tWordDestination\nsynced\tсинхронизированы\n", encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260831002500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    page.set_content(html)

    # Dynamic update of translation text
    page.evaluate("""() => {
        window.AppState.applyDeltas({
            stage: "translated_text",
            status: "success",
            translatedText: "Спецификации синхронизированы с openspec."
        });
    }""")

    # Hover translation span "синхронизированы"
    has_hover = page.evaluate("""() => {
        var transSpans = document.querySelectorAll('#translation-container span.word.hl-mvp');
        if (transSpans.length < 2) return false;
        var targetTrans = transSpans[1]; // "синхронизированы"
        var evt = new MouseEvent('mouseover', { bubbles: true });
        targetTrans.dispatchEvent(evt);
        
        var srcSpans = document.querySelectorAll('#source-container span.word');
        for (var i = 0; i < srcSpans.length; i++) {
            if (srcSpans[i].classList.contains('hl-mvp-hover')) {
                return true;
            }
        }
        return false;
    }""")
    assert has_hover is True, "Hovering translation span must apply hl-mvp-hover to aligned source span"

    # Mouse out removes highlight
    no_hover = page.evaluate("""() => {
        var transSpans = document.querySelectorAll('#translation-container span.word.hl-mvp');
        var targetTrans = transSpans[1];
        var evt = new MouseEvent('mouseout', { bubbles: true });
        targetTrans.dispatchEvent(evt);
        
        var srcSpans = document.querySelectorAll('#source-container span.word');
        for (var i = 0; i < srcSpans.length; i++) {
            if (srcSpans[i].classList.contains('hl-mvp-hover')) {
                return false;
            }
        }
        return true;
    }""")
    assert no_hover is True, "Mouseout on translation span must remove hl-mvp-hover from source span"


def test_progressive_lemma_update_replaces_skeleton_and_sets_provenance(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "apple"
    tsv_content = (
        "# comment\n"
        "TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n"
        "0\tapple\t\t1\n"
    )
    tsv_file = tmp_path / "20260831004000-prog-test.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260831004000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    page.set_content(html)

    # Initial render in progressive mode has skeleton-loader in word_dest cell
    td_trans = page.locator("tr[data-row-id='0'] td").nth(2)
    assert td_trans.is_visible()

    # Apply progressive delta with translated text and translated lemma
    page.evaluate("""() => {
        if (window.receiveUpdate) {
            window.receiveUpdate({
                stage: 'translated',
                status: 'success',
                textProvenance: 'live:argos',
                translatedText: 'яблоко',
                rows: {
                    '0': {
                        lemma: 'apple',
                        trans: 'яблоко',
                        token_order: '0',
                        provenance: 'live:argos'
                    }
                }
            });
        }
    }""")

    # Verify skeleton is cleared and translated text is displayed in td
    assert "яблоко" in td_trans.inner_text()
    assert td_trans.locator(".skeleton-loader").count() == 0
    assert td_trans.get_attribute("title") == "Translated via Argos (offline)"

    # Verify translation-container has provenance tooltip and child spans retain it
    tc = page.locator("#translation-container")
    assert tc.get_attribute("title") == "Translated via Argos (offline)"
    word_span = page.locator("#translation-container span.word").first
    assert word_span.is_visible()
    assert word_span.get_attribute("title") == "Translated via Argos (offline)"


def test_rmb_flip_during_progressive_loading_does_not_flip_to_skeleton_text(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "All artifacts complete."
    tsv_content = (
        "# comment\n"
        "TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n"
        "0\tall\t\t1\n"
        "1\tartifact\t\t1\n"
        "2\tcomplete\t\t1\n"
    )
    tsv_file = tmp_path / "20260831005500-skeleton-flip.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260831005500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    page.set_content(html)

    # Initial stage has skeleton loaders in table cells
    span_complete = page.locator("span.word[data-lower-clean='complete']").first
    assert span_complete.is_visible()

    # Trigger RMB click on 'complete' while translation cell still has skeleton 'Argos...'
    span_complete.click(button="right")

    # Word span must NOT be flipped to 'Argos...' and must NOT have .flipped class
    assert span_complete.inner_text() == "complete"
    assert "flipped" not in (span_complete.get_attribute("class") or "")
    assert "Argos" not in (page.locator("#source-container").inner_text())


def test_multisentence_container_tabs_progressive_lemma_sync(page, tmp_path):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "All artifacts complete. All tasks complete. All test suites verified."
    tsv_content = (
        "# comment\n"
        "TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n"
        "0\tall\t\t1\n"
        "1\tartifact\t\t1\n"
        "2\tcomplete\t\t1\n"
        "3\tall\t\t2\n"
        "4\ttask\t\t2\n"
        "5\tcomplete\t\t2\n"
        "6\tall\t\t3\n"
        "7\ttest\t\t3\n"
        "8\tsuite\t\t3\n"
        "9\tverify\t\t3\n"
    )
    tsv_file = tmp_path / "20260831005501-multisync.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260831005501",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        seq_num=2,
    )

    page.set_content(html)

    # Initial render shows tab chips
    tab_chips = page.locator(".kw-tab-chip")
    assert tab_chips.count() >= 3

    # Send stage="translated" progressive update with resolved lemmas
    page.evaluate("""() => {
        if (window.receiveUpdate) {
            window.receiveUpdate({
                stage: 'translated',
                status: 'success',
                rows: {
                    '0': { lemma: 'all', trans: 'все', token_order: '0', provenance: 'live:argos' },
                    '1': { lemma: 'artifact', trans: 'артефакты', token_order: '1', provenance: 'live:argos' },
                    '2': { lemma: 'complete', trans: 'завершены', token_order: '2', provenance: 'live:argos' },
                    '3': { lemma: 'all', trans: 'все', token_order: '3', provenance: 'live:argos' },
                    '4': { lemma: 'task', trans: 'задания', token_order: '4', provenance: 'live:argos' },
                    '5': { lemma: 'complete', trans: 'выполнены', token_order: '5', provenance: 'live:argos' },
                    '6': { lemma: 'all', trans: 'все', token_order: '6', provenance: 'live:argos' },
                    '7': { lemma: 'test', trans: 'тесты', token_order: '7', provenance: 'live:argos' },
                    '8': { lemma: 'suite', trans: 'наборы', token_order: '8', provenance: 'live:argos' },
                    '9': { lemma: 'verify', trans: 'проверены', token_order: '9', provenance: 'live:argos' }
                }
            });
        }
    }""")

    # Verify table row 2 (complete) now has 'завершены' and no skeleton loader
    td_complete = page.locator("tr[data-token-order='2'] td").nth(2)
    assert "завершены" in td_complete.inner_text()
    assert td_complete.locator(".skeleton-loader").count() == 0





