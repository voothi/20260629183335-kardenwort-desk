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

