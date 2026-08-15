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
    kardenwort_desk.write_update_js(
        tsv_path,
        data_rows,
        headers,
        role_fields,
        stage="finished",
        source_text='<span class="word highlight-purple" data-word-idx="0">Das</span> <span class="word highlight-orange" data-word-idx="1">Haus</span>',
        translated_text="The house"
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
    page.add_init_script("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
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
    archive_row = page.locator("tr[data-row-id='0']")
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



def test_rmb_flip_compound_with_leading_zid_hides_zid_and_removes_leading_hyphen(page, tmp_path):
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

    # Find the token span and RMB click it to flip the compound
    token_span = page.locator("span.word[data-lower-clean='token']").first
    assert token_span.is_visible()

    # Dispatch RMB click (button 2)
    token_span.click(button="right")

    source_container = page.locator("#source-container")
    flipped_text = source_container.inner_text()

    # The flipped text should not have a leading hyphen before жетон, nor should the ZID be visible
    assert "Change: жетон" in flipped_text or "Change:жетон" in flipped_text
    assert "Change: - жетон" not in flipped_text
    assert "Change: -жетон" not in flipped_text
    assert "20260815131120" not in flipped_text

    # RMB click again to un-flip
    token_span.click(button="right")
    unflipped_text = source_container.inner_text()
    assert "20260815131120-token-mapping-inflected-expansion" in unflipped_text

    # 3rd RMB click: flip back to Russian
    token_span.click(button="right")
    flipped_text_2 = source_container.inner_text()
    assert "Change: жетон" in flipped_text_2 or "Change:жетон" in flipped_text_2
    assert "20260815131120" not in flipped_text_2

    # 4th RMB click: un-flip back to English
    token_span.click(button="right")
    unflipped_text_2 = source_container.inner_text()
    assert "20260815131120-token-mapping-inflected-expansion" in unflipped_text_2




