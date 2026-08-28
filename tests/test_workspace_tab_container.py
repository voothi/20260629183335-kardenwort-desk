import pytest
import json
import configparser
from pathlib import Path
import kardenwort_desk

def create_mock_config(web_tab_mode="container", sentences_enabled=True):
    config = configparser.ConfigParser()
    config.read_dict({
        "settings": {
            "defaultzoom": "100",
            "theme": "dark",
            "text_mode": "single",
        },
        "sentences_mode": {
            "enabled": "true" if sentences_enabled else "false",
            "web_tab_mode": web_tab_mode,
            "parent_mode": "full",
            "auto_inject_updates": "false",
        },
        "sentence_boundary": {
            "abbreviations": "e.g.,i.e.,dr.,mr.,mrs.",
            "terminators": ".!?",
            "punctuation_marks": "\",')]}",
        },
        "translation": {
            "translation_wrap_max_chars": "90",
        },
        "server": {
            "enabled": "true",
            "host": "127.0.0.1",
            "port": "18335",
        }
    })
    return config

def test_render_flow_single_sentence_no_tabs(tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    text = "Das Haus ist gross."
    tsv_file = tmp_path / "20260828111800-test.de.tsv"
    tsv_file.write_text("Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\nHaus\tHaus\tдом\t1\t\n", encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260828111800",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    assert 'id="kw-workspace-tab-bar" style="display:none;"' in html
    assert '<script id="delivery-mode" type="text/plain">container</script>' in html
    assert '<script id="web-tab-mode" type="text/plain">container</script>' in html
    assert '<script id="sentence-cards" type="application/json">\n[]\n</script>' in html

def test_render_flow_multi_sentence_container_tabs(tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    text = "Das Haus ist gross. Die Katze schlaeft."
    tsv_file = tmp_path / "20260828111800-test.de.tsv"
    tsv_file.write_text("Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\nHaus\tHaus\tдом\t1\t\nKatze\tKatze\tкошка\t2\t\n", encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260828111800",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    assert '<div class="kw-workspace-tab-bar kw-tab-dock-top" id="kw-workspace-tab-bar">' in html
    assert 'has-dock-top' in html
    assert '<script id="tab-bar-position" type="text/plain">top</script>' in html
    assert '<button type="button" class="kw-tab-nav kw-tab-nav-prev" id="kw-tab-prev"' in html
    assert '<div class="kw-tab-track" id="kw-tab-track">' in html
    assert '<button type="button" class="kw-tab-nav kw-tab-nav-next" id="kw-tab-next"' in html
    assert '<button type="button" class="kw-tab-chip active" data-tab-seq="1" data-sentence-idx="0"' in html
    assert '<button type="button" class="kw-tab-chip" data-tab-seq="2" data-sentence-idx="1"' in html
    assert '<button type="button" class="kw-tab-chip" data-tab-seq="3" data-sentence-idx="2"' in html
    assert '<script id="delivery-mode" type="text/plain">container</script>' in html
    assert '<script id="web-tab-mode" type="text/plain">container</script>' in html
    assert '<span class="kw-sentence-chunk" data-sentence-idx="1">' in html
    assert '<span class="kw-sentence-chunk" data-sentence-idx="2">' in html
    assert 'data-sentence-idx="1"' in html
    assert 'data-sentence-idx="2"' in html

def test_render_flow_multi_sentence_tabs_mode(tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "multi_window")
    config.set("sentences_mode", "enabled", "true")
    text = "Das Haus ist gross. Die Katze schlaeft."
    tsv_file = tmp_path / "20260828111800-test.de.tsv"
    tsv_file.write_text("Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\nHaus\tHaus\tдом\t1\t\nKatze\tKatze\tкошка\t2\t\n", encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260828111800",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    assert 'id="kw-workspace-tab-bar" style="display:none;"' in html
    assert '<script id="delivery-mode" type="text/plain">multi_window</script>' in html
    assert '<script id="web-tab-mode" type="text/plain">tabs</script>' in html

def test_sentence_chunks_encapsulation_structure(tmp_path):
    """Test that all tokens and delimiters of each sentence are wrapped in .kw-sentence-chunk."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    text = "Erste Zeile.\nZweite Zeile.\nDritte Zeile."
    tsv_file = tmp_path / "20260828111800-test.de.tsv"
    tsv_file.write_text("Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\nZeile\tZeile\tстрока\t1\t\nZeile\tZeile\tстрока\t2\t\nZeile\tZeile\tстрока\t3\t\n", encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260828111800",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    assert '<span class="kw-sentence-chunk" data-sentence-idx="1">' in html
    assert '<span class="kw-sentence-chunk" data-sentence-idx="2">' in html
    assert '<span class="kw-sentence-chunk" data-sentence-idx="3">' in html

def test_window_title_parity_with_ahk(tmp_path):
    """Test that generated <title> strictly adheres to Kardenwort - {lang} ({text_mode}) - {tsv_filename} - Ready."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    text = "First sentence.\nSecond sentence."
    tsv_file = tmp_path / "20260828161106-first-sentence-second-sentence.en.tsv"
    tsv_file.write_text("Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\nFirst\tFirst\tпервый\t1\t\nSecond\tSecond\tвторой\t2\t\n", encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260828161106",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    import re
    m = re.search(r"<title>(.*?)</title>", html)
    actual_title = m.group(0) if m else ""
    assert actual_title == "<title>Kardenwort - en (multi) - 20260828161106-first-sentence-second-sentence.en.tsv - Ready</title>"

def test_playwright_workspace_tab_strip_and_navigation(page, tmp_path):
    """Test tab bar track, chevron clicks, keyboard switching, dynamic titles, and sentence chunk visibility."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    
    # 5 sentences for scrolling verification
    text = "First sentence.\nSecond sentence.\nThird sentence.\nFourth sentence.\nFifth sentence."
    tsv_file = tmp_path / "20260828170000-master.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n"
        "Third\tThird\tтретий\t3\t\n"
        "Fourth\tFourth\tчетвертый\t4\t\n"
        "Fifth\tFifth\tпятый\t5\t\n",
        encoding="utf-8"
    )
    
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260828170000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")
    
    # Check initial active tab and title
    active_chip = page.locator(".kw-tab-chip.active")
    assert active_chip.inner_text() == "1"
    assert "20260828170000-first-sentence-second-sentence.en.tsv - Ready" in page.title()
    
    # Click Tab 2 (Sentence 1: sentence_idx = 1)
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    
    # Sentence 1 chunk should be visible, chunk 2 should be hidden
    chunk1 = page.locator('.kw-sentence-chunk[data-sentence-idx="1"]')
    chunk2 = page.locator('.kw-sentence-chunk[data-sentence-idx="2"]')
    assert chunk1.is_visible()
    assert not chunk2.is_visible()
    
    # Check title updated for sentence 1
    assert "20260828170001-first-sentence-second-sentence.en.tsv - Ready" in page.title()
    
    # Press ']' key to go to Tab 3 (Sentence 2: sentence_idx = 2)
    page.keyboard.press("]")
    chunk3 = page.locator('.kw-sentence-chunk[data-sentence-idx="3"]')
    assert not chunk1.is_visible()
    assert chunk2.is_visible()
    assert not chunk3.is_visible()
    assert "20260828170002-first-sentence-second-sentence.en.tsv - Ready" in page.title()
    
    # Set narrow viewport so tab strip overflows and activates chevrons
    page.set_viewport_size({"width": 180, "height": 600})
    page.evaluate("window.WorkspaceTabs.init()")
    
    next_btn = page.locator("#kw-tab-next")
    prev_btn = page.locator("#kw-tab-prev")
    assert not next_btn.is_disabled()
    next_btn.click()
    assert not prev_btn.is_disabled()
    prev_btn.click()



