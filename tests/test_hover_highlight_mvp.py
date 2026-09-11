import pytest
import configparser
from pathlib import Path
import kardenwort_desk
from kardenwort_desk import run_render_flow, SEC_SETTINGS, SEC_RENDERING

def test_native_html_styles_and_scripts(tmp_path):
    tsv_path = tmp_path / "test.tsv"
    tsv_path.write_text("SentenceSourceIndex\tWordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\n1\tAuto\t\t\tCar\n", encoding='utf-8')
    
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if not config.has_section(SEC_RENDERING):
        config.add_section(SEC_RENDERING)
    config.set(SEC_RENDERING, 'hover_highlight', 'true')
    config.set(SEC_RENDERING, 'hover_highlight_bookmarks', '4')
    config.set(SEC_RENDERING, 'hover_highlight_rainbow', 'true')
    
    # 1. Test Dark Theme
    html_dark = run_render_flow(
        text="Das Auto fährt schnell.",
        language="de",
        zid="20260826000001",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=tsv_path
    )
    
    assert 'id="hl-mvp-style"' in html_dark
    assert '.hl-mvp-hover' in html_dark
    assert '.hl-mvp-pin' in html_dark
    for i in range(8):
        assert f'.hl-mvp-pin-{i}' in html_dark
    assert 'data-bookmarks="4"' in html_dark
    assert 'data-rainbow="1"' in html_dark
    assert 'data-enabled="1"' in html_dark
    assert '#39d353' in html_dark  # Dark slot 0
    assert '#b78cf7' in html_dark  # Dark slot 1
    
    # 2. Test Light Theme
    html_light = run_render_flow(
        text="Das Auto fährt schnell.",
        language="de",
        zid="20260826000002",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="light",
        tsv_path=tsv_path
    )
    
    assert 'id="hl-mvp-style"' in html_light
    assert '#1a7f37' in html_light  # Light slot 0
    assert '#8250df' in html_light  # Light slot 1

def test_playwright_hover_and_rainbow_interaction(page, tmp_path):
    tsv_path = tmp_path / "test.tsv"
    tsv_path.write_text("SentenceSourceIndex\tWordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\n1\tAuto\t\t\tCar\n", encoding='utf-8')
    
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if not config.has_section(SEC_RENDERING):
        config.add_section(SEC_RENDERING)
    config.set(SEC_RENDERING, 'hover_highlight', 'true')
    config.set(SEC_RENDERING, 'hover_highlight_bookmarks', '3')
    config.set(SEC_RENDERING, 'hover_highlight_rainbow', 'true')
    
    html = run_render_flow(
        text="Das rote Auto fährt schnell.",
        language="de",
        zid="20260826000003",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=tsv_path
    )
    
    page.set_content(html)
    
    # Apply translated text to ensure tokens are present
    page.evaluate("""
        window.AppState.applyDeltas({
            translatedText: "<div>Красная машина едет быстро.</div>",
            stage: "finished"
        });
    """)
    
    # Verify __mvpInitialized flag
    is_init = page.evaluate("window.__mvpInitialized")
    assert is_init is True
    
    # Verify translation tokenization into span.word.hl-mvp
    trans_spans = page.locator("#translation-container span.word.hl-mvp")
    assert trans_spans.count() > 0
    
    src_spans = page.locator("#source-container span.word")
    assert src_spans.count() > 0
    
    # Test Hover Synchronization
    first_src = src_spans.nth(0)
    first_src.hover()
    
    # Check that a target span has hl-mvp-hover class
    hovered_trans = page.locator("#translation-container span.word.hl-mvp-hover")
    assert hovered_trans.count() >= 1
    
    # Move mouse away
    page.mouse.move(0, 0)
    assert page.locator("#translation-container span.word.hl-mvp-hover").count() == 0
    
    # Test Rainbow Slot Pinning
    # Pin 1st word
    first_src.click()
    assert "hl-mvp-pin" in first_src.get_attribute("class")
    assert "hl-mvp-pin-0" in first_src.get_attribute("class")
    
    # Pin 2nd word
    second_src = src_spans.nth(1)
    second_src.click()
    assert "hl-mvp-pin" in second_src.get_attribute("class")
    assert "hl-mvp-pin-1" in second_src.get_attribute("class")
    
    # Pin 3rd word
    third_src = src_spans.nth(2)
    third_src.click()
    assert "hl-mvp-pin" in third_src.get_attribute("class")
    assert "hl-mvp-pin-2" in third_src.get_attribute("class")
    
    # Pin 4th word (exceeds N=3 -> evicts 1st word)
    fourth_src = src_spans.nth(3)
    fourth_src.click()
    assert "hl-mvp-pin" in fourth_src.get_attribute("class")
    # First word should no longer be pinned
    assert "hl-mvp-pin" not in (first_src.get_attribute("class") or "")
    
    # Test Escape Key clears all pins
    page.keyboard.press("Escape")
    assert page.locator(".hl-mvp-pin").count() == 0
    assert page.locator(".hl-mvp-pin-0").count() == 0

def test_rebind_mvp_on_appstate_deltas(page, tmp_path):
    tsv_path = tmp_path / "test.tsv"
    tsv_path.write_text("SentenceSourceIndex\tWordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\n1\tAuto\t\t\tCar\n", encoding='utf-8')
    
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if not config.has_section(SEC_RENDERING):
        config.add_section(SEC_RENDERING)
    
    html = run_render_flow(
        text="Das Haus",
        language="de",
        zid="20260826000004",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=tsv_path
    )
    
    page.set_content(html)
    
    # Update translation container dynamically via AppState
    page.evaluate("""
        window.AppState.applyDeltas({
            translatedText: "<div>Большой красивый дом</div>",
            stage: "translated"
        });
    """)
    
    # Verify new translation words are tokenized and wired
    new_trans_spans = page.locator("#translation-container span.word.hl-mvp")
    assert new_trans_spans.count() == 3

def test_selectable_text_mode_toggle(page, tmp_path):
    tsv_path = tmp_path / "test.tsv"
    tsv_path.write_text("SentenceSourceIndex\tWordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\n1\tAuto\t\t\tCar\n", encoding='utf-8')
    
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    html = run_render_flow(
        text="Das Haus",
        language="de",
        zid="20260826000005",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=tsv_path
    )
    page.set_content(html)
    
    # Activate text selection mode programmatically
    page.evaluate("window.setSelectableTextMode(true)")
    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')") is True
    assert page.evaluate("window.__selectableTextMode") is True
    
    # Deactivate text selection mode programmatically
    page.evaluate("window.setSelectableTextMode(false)")
    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')") is False
    assert page.evaluate("window.__selectableTextMode") is False

    # Press Alt key down
    page.keyboard.down("Alt")
    assert page.evaluate("window.__selectableTextMode") is True
    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')") is True

    # Release Alt key
    page.keyboard.up("Alt")
    assert page.evaluate("window.__selectableTextMode") is False
    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')") is False

    # Transient Alt hold followed by window blur
    page.keyboard.down("Alt")
    assert page.evaluate("window.__selectableTextMode") is True
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    assert page.evaluate("window.__selectableTextMode") is False
    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')") is False
    page.keyboard.up("Alt")

    # Persistent mode test
    page.evaluate("window.setSelectableTextMode(true, true)")
    assert page.evaluate("window.__selectableTextMode") is True
    assert page.evaluate("window.__persistentSelectableMode") is True
    page.keyboard.up("Alt")
    assert page.evaluate("window.__selectableTextMode") is True
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    assert page.evaluate("window.__selectableTextMode") is True
    page.evaluate("window.setSelectableTextMode(false, false)")
    assert page.evaluate("window.__selectableTextMode") is False


def test_hierarchical_target_resolution_unit(page, tmp_path):
    """
    Unit test verifying getTargetIdx hierarchical resolution logic in JS environment:
    - Tier 1: Sentence Scope resolution
    - Tier 2: Line / Cue Scope resolution for auto-subtitles
    - Tier 3: Proportional ratio with child-tab line-offset isolation and defensive fallback
    """
    tsv_path = tmp_path / "test_unit.tsv"
    tsv_path.write_text("SentenceSourceIndex\tWordSource\tWordDestination\n1\tAuto\tCar\n", encoding='utf-8')
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    html = run_render_flow(
        text="Das Auto.",
        language="de",
        zid="20260911000001",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=tsv_path
    )
    page.set_content(html)

    # 1. Test Child Tab context isolation: source span has global line 5, translation span has line 0, both sent 3
    res_child = page.evaluate("""() => {
        document.getElementById('source-container').innerHTML =
            '<span class="word hl-mvp" data-sentence-idx="3" data-line-idx="5">Wort1</span> ' +
            '<span class="word hl-mvp" data-sentence-idx="3" data-line-idx="5">Wort2</span>';
        document.getElementById('translation-container').innerHTML =
            '<span class="word hl-mvp" data-sentence-idx="3" data-line-idx="0">Word1</span> ' +
            '<span class="word hl-mvp" data-sentence-idx="3" data-line-idx="0">Word2</span>';
        window.buildLcIndex();
        return window.getTargetIdx ? window.getTargetIdx(0, true) : -1;
    }""")
    assert res_child == 0, f"Child tab word 0 must map to target span 0, got {res_child}"

    # 2. Test Tab 1 multi-sentence isolation: Sentence 1 and Sentence 2
    res_sent2 = page.evaluate("""() => {
        document.getElementById('source-container').innerHTML =
            '<span class="kw-sentence-chunk" data-sentence-idx="1"><span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">S1A</span> <span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">S1B</span></span> ' +
            '<span class="kw-sentence-chunk" data-sentence-idx="2"><span class="word hl-mvp" data-sentence-idx="2" data-line-idx="1">S2A</span> <span class="word hl-mvp" data-sentence-idx="2" data-line-idx="1">S2B</span></span>';
        document.getElementById('translation-container').innerHTML =
            '<div data-sentence-idx="1" data-line-idx="0"><span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">T1A</span> <span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">T1B</span></div>' +
            '<div data-sentence-idx="2" data-line-idx="1"><span class="word hl-mvp" data-sentence-idx="2" data-line-idx="1">T2A</span> <span class="word hl-mvp" data-sentence-idx="2" data-line-idx="1">T2B</span></div>';
        window.buildLcIndex();
        // Source idx 2 is S2A (1st word of Sentence 2). Should map to T2A (idx 2 in target array)
        var s2a_target = window.getTargetIdx(2, true);
        // Source idx 3 is S2B (2nd word of Sentence 2). Should map to T2B (idx 3 in target array)
        var s2b_target = window.getTargetIdx(3, true);
        return { s2a: s2a_target, s2b: s2b_target };
    }""")
    assert res_sent2["s2a"] == 2, f"S2A must map to T2A (idx 2), got {res_sent2['s2a']}"
    assert res_sent2["s2b"] == 3, f"S2B must map to T2B (idx 3), got {res_sent2['s2b']}"

    # 3. Test Unpunctuated auto-subtitles: uniform sentence index 1 across multiple line cues
    res_cues = page.evaluate("""() => {
        document.getElementById('source-container').innerHTML =
            '<span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">Line0A</span> <span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">Line0B</span><br>' +
            '<span class="word hl-mvp" data-sentence-idx="1" data-line-idx="1">Line1A</span> <span class="word hl-mvp" data-sentence-idx="1" data-line-idx="1">Line1B</span>';
        document.getElementById('translation-container').innerHTML =
            '<div data-sentence-idx="1" data-line-idx="0"><span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">Trans0A</span> <span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">Trans0B</span></div>' +
            '<div data-sentence-idx="1" data-line-idx="1"><span class="word hl-mvp" data-sentence-idx="1" data-line-idx="1">Trans1A</span> <span class="word hl-mvp" data-sentence-idx="1" data-line-idx="1">Trans1B</span></div>';
        window.buildLcIndex();
        // Source idx 2 is Line1A (1st word of cue line 1). Should map to Trans1A (idx 2 in target array)
        var line1a_target = window.getTargetIdx(2, true);
        return line1a_target;
    }""")
    assert res_cues == 2, f"Line1A must map to Trans1A (idx 2), got {res_cues}"

    # 4. Test Defensive Fallback: asymmetric lines (source line 1, target single line 0)
    res_fallback = page.evaluate("""() => {
        document.getElementById('source-container').innerHTML =
            '<span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">A</span><br>' +
            '<span class="word hl-mvp" data-sentence-idx="1" data-line-idx="1">B</span>';
        document.getElementById('translation-container').innerHTML =
            '<span class="word hl-mvp" data-sentence-idx="1" data-line-idx="0">OnlyOneLine</span>';
        window.buildLcIndex();
        var target_for_b = window.getTargetIdx(1, true);
        return target_for_b;
    }""")
    assert res_fallback == 0, f"Fallback must map to target span 0 instead of -1, got {res_fallback}"


def test_playwright_child_tab_hover_alignment_and_pinning(page, tmp_path):
    """
    Verifies that on Child Tabs (Tabs 2..N in container mode), hovering and pinning words
    correctly synchronizes to the active child translation without global line offset dropouts.
    """
    from kardenwort_db import KardenwortDB
    db_path = tmp_path / "test_child_hover.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    unique_zid = "20260911000002"
    text = "Erstes Wort hier. Zweiter Satz mit Spezialrad."
    tsv_file = tmp_path / f"{unique_zid}-child-hover.de.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\tSentenceDestination\tDeskSelected\n"
        "Wort\tWort\tWort\tслово\t1\tПервое слово здесь.\t0\n"
        "Spezialrad\tSpezialrad\tSpezialrad\tспециальное колесо\t2\tВторое предложение со спецколесом.\t0\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid=unique_zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=3  # Start on Tab 3 (Sentence 2)
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Locate token "Spezialrad" in visible Sentence 2 chunk
    spezial_token = page.locator('#source-container span.word', has_text='Spezialrad')
    assert spezial_token.count() == 1

    # 1. Hovering token "Spezialrad" highlights translation span in #translation-container
    spezial_token.hover()
    hovered_trans = page.locator('#translation-container span.word.hl-mvp-hover')
    assert hovered_trans.count() >= 1, "Hovering source word on child tab must highlight translation word"

    # 2. Clicking token "Spezialrad" creates rainbow pin (hl-mvp-pin and hl-mvp-pin-0) on both source and translation
    spezial_token.click()
    assert "hl-mvp-pin" in (spezial_token.get_attribute("class") or "")
    pinned_trans = page.locator('#translation-container span.word.hl-mvp-pin')
    assert pinned_trans.count() >= 1, "Clicking source word on child tab must pin translation word"


def test_playwright_unpunctuated_auto_subtitles_cue_alignment(page, tmp_path):
    """
    Verifies that unpunctuated auto-generated subtitles align cue-by-cue across multi-line breaks.
    """
    tsv_path = tmp_path / "test_subs.tsv"
    tsv_path.write_text(
        "SentenceSourceIndex\tWordSource\tWordDestination\n"
        "1\twelcome\tдобро пожаловать\n"
        "1\teveryone\tвсе\n"
        "1\ttoday\tсегодня\n"
        "1\tspeaking\tговоря\n",
        encoding='utf-8'
    )

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "enabled", "false")
    config.set(SEC_RENDERING, 'hover_highlight', 'true')
    config.set(SEC_RENDERING, 'hover_highlight_rainbow', 'true')

    subs_text = "welcome everyone\ntoday we are speaking"
    html = run_render_flow(
        text=subs_text,
        language="en",
        zid="20260911000003",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=tsv_path
    )
    page.set_content(html)

    # Set multi-line translation
    page.evaluate("""
        window.AppState.applyDeltas({
            translatedText: "<div>добро пожаловать всем</div><div>сегодня мы говорим</div>",
            stage: "finished"
        });
    """)

    # Hover word "speaking" on Line 1 in source
    speaking_token = page.locator('#source-container span.word', has_text='speaking')
    assert speaking_token.count() == 1
    speaking_token.hover()

    # Highlighted translation span must be in Line 1 div (e.g. "говорим"), NOT in Line 0 div
    hovered_spans = page.locator('#translation-container span.word.hl-mvp-hover')
    assert hovered_spans.count() >= 1
    # Check parent div line index of hovered span
    parent_line_idx = hovered_spans.first.evaluate("el => el.getAttribute('data-line-idx')")
    assert parent_line_idx == "1", f"Hovered translation span must belong to line-idx 1, got {parent_line_idx}"

