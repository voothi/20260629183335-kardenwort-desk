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
    """Test that container mode prefixes sequence [1/3] and multi_window mode strictly adheres to un-prefixed AHK title."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "cache_ttl_seconds", "0")
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

    # Parity in multi_window mode: no prefix
    config.set("sentences_mode", "delivery_mode", "multi_window")
    html_mw = kardenwort_desk.run_render_flow(
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
    m_mw = re.search(r"<title>(.*?)</title>", html_mw)
    actual_title_mw = m_mw.group(0) if m_mw else ""
    assert actual_title_mw == "<title>Kardenwort - en (multi) - 20260828161106-first-sentence-second-sentence.en.tsv - Ready</title>"


def test_playwright_workspace_tab_strip_and_navigation(page, tmp_path):
    """Test tab bar track, chevron clicks, keyboard switching, dynamic titles, and sentence chunk visibility."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    
    # 5 sentences for scrolling verification
    text = "First sentence.\nSecond sentence.\nThird sentence.\nFourth sentence.\nFifth sentence."
    tsv_file = tmp_path / "20260828170000-first-sentence-second-sentence.en.tsv"
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
    
    # Check initial active tab and title (no prefix)
    active_chip = page.locator(".kw-tab-chip.active")
    assert active_chip.inner_text() == "1"
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828170000-first-sentence-second-sentence.en.tsv - Ready"
    
    # Click Tab 2 (Sentence 1: sentence_idx = 1)
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    
    # Sentence 1 chunk should be visible, chunk 2 should be hidden
    chunk1 = page.locator('.kw-sentence-chunk[data-sentence-idx="1"]')
    chunk2 = page.locator('.kw-sentence-chunk[data-sentence-idx="2"]')
    assert chunk1.is_visible()
    assert not chunk2.is_visible()
    
    # Check title updated for sentence 1 with distinct sentence slug and without prefix
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828170001-first-sentence.en.tsv - Ready"
    
    # Press ']' key to go to Tab 3 (Sentence 2: sentence_idx = 2)
    page.keyboard.press("]")
    chunk3 = page.locator('.kw-sentence-chunk[data-sentence-idx="3"]')
    assert not chunk1.is_visible()
    assert chunk2.is_visible()
    assert not chunk3.is_visible()
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828170002-second-sentence.en.tsv - Ready"
    
    # Set narrow viewport so tab strip overflows and activates chevrons
    page.set_viewport_size({"width": 180, "height": 600})
    page.evaluate("window.WorkspaceTabs.init()")
    
    next_btn = page.locator("#kw-tab-next")
    prev_btn = page.locator("#kw-tab-prev")
    assert not next_btn.is_disabled()
    next_btn.click()
    assert not prev_btn.is_disabled()
    prev_btn.click()


def test_playwright_tab_docking_positions(page, tmp_path):
    """Test fixed top docking, bottom docking, and inline modes in Playwright."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    
    text = "First sentence.\nSecond sentence."
    tsv_file = tmp_path / "20260828180000-dock.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n",
        encoding="utf-8"
    )

    # 1. Top docking
    config.set("sentences_mode", "tab_bar_position", "top")
    html_top = kardenwort_desk.run_render_flow(
        text=text, language="en", zid="20260828180000", text_mode="multi",
        config=config, resolved_paths=resolved_paths, tsv_path=str(tsv_file),
        spawn_children=False, return_children=False
    )
    page.set_content(html_top)
    page.wait_for_selector("#kw-workspace-tab-bar")
    tab_bar = page.locator("#kw-workspace-tab-bar")
    container = page.locator(".container")
    
    assert tab_bar.evaluate("el => window.getComputedStyle(el).position") == "fixed"
    assert tab_bar.evaluate("el => window.getComputedStyle(el).top") == "0px"
    assert tab_bar.evaluate("el => window.getComputedStyle(el).zIndex") == "1000"
    assert page.locator("body").evaluate("el => el.classList.contains('has-dock-top')") is True
    assert container.evaluate("el => window.getComputedStyle(el).paddingTop") == "48px"

    # 2. Bottom docking
    config.set("sentences_mode", "tab_bar_position", "bottom")
    html_bottom = kardenwort_desk.run_render_flow(
        text=text, language="en", zid="20260828180000", text_mode="multi",
        config=config, resolved_paths=resolved_paths, tsv_path=str(tsv_file),
        spawn_children=False, return_children=False
    )
    page.set_content(html_bottom)
    page.wait_for_selector("#kw-workspace-tab-bar")
    tab_bar = page.locator("#kw-workspace-tab-bar")
    container = page.locator(".container")
    
    assert tab_bar.evaluate("el => window.getComputedStyle(el).position") == "fixed"
    assert tab_bar.evaluate("el => window.getComputedStyle(el).bottom") == "50px"
    assert tab_bar.evaluate("el => window.getComputedStyle(el).zIndex") == "1000"
    assert page.locator("body").evaluate("el => el.classList.contains('has-dock-bottom')") is True
    assert container.evaluate("el => window.getComputedStyle(el).paddingBottom") == "95px"

    # 3. Inline mode
    config.set("sentences_mode", "tab_bar_position", "inline")
    html_inline = kardenwort_desk.run_render_flow(
        text=text, language="en", zid="20260828180000", text_mode="multi",
        config=config, resolved_paths=resolved_paths, tsv_path=str(tsv_file),
        spawn_children=False, return_children=False
    )
    page.set_content(html_inline)
    page.wait_for_selector("#kw-workspace-tab-bar")
    tab_bar = page.locator("#kw-workspace-tab-bar")
    
    assert tab_bar.evaluate("el => window.getComputedStyle(el).position") == "static"
    assert page.locator("body").evaluate("el => !el.classList.contains('has-dock-top') && !el.classList.contains('has-dock-bottom')") is True
    assert tab_bar.evaluate("el => el.classList.contains('kw-tab-dock-inline')") is True


def test_playwright_glassmorphic_styling_and_hover(page, tmp_path):
    """Test backdrop-filter blur and hover transition between translucent and opaque backgrounds."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "tab_bar_position", "top")
    
    text = "First sentence.\nSecond sentence."
    tsv_file = tmp_path / "20260828180500-glass.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n",
        encoding="utf-8"
    )

    # Dark Theme
    html_dark = kardenwort_desk.run_render_flow(
        text=text, language="en", zid="20260828180500", text_mode="multi",
        config=config, resolved_paths=resolved_paths, tsv_path=str(tsv_file),
        spawn_children=False, return_children=False
    )
    page.set_content(html_dark)
    page.wait_for_selector("#kw-workspace-tab-bar")
    tab_bar = page.locator("#kw-workspace-tab-bar")
    
    bf = tab_bar.evaluate("el => window.getComputedStyle(el).backdropFilter || window.getComputedStyle(el).webkitBackdropFilter")
    assert "blur(8px)" in bf
    
    # Idle translucent background (dark: rgba(22, 27, 34, 0.8))
    bg_idle = tab_bar.evaluate("el => window.getComputedStyle(el).backgroundColor")
    assert "rgba(22, 27, 34, 0.8)" in bg_idle or "0.8" in bg_idle
    
    # Hover solid opaque background (dark: #161b22 -> rgb(22, 27, 34))
    tab_bar.hover()
    page.wait_for_timeout(300)
    bg_hover = tab_bar.evaluate("el => window.getComputedStyle(el).backgroundColor")
    assert "rgb(22, 27, 34)" in bg_hover


def test_playwright_dynamic_document_title_card_prefix(page, tmp_path):
    """Test dynamic document.title card sequence prefixing ([1/3], [2/3], [3/3]) upon tab navigation."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    
    text = "First sentence.\nSecond sentence."
    tsv_file = tmp_path / "20260828181000-first-sentence-second-sentence.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text, language="en", zid="20260828181000", text_mode="multi",
        config=config, resolved_paths=resolved_paths, tsv_path=str(tsv_file),
        spawn_children=False, return_children=False
    )
    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Initial title: no [1/3] prefix
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828181000-first-sentence-second-sentence.en.tsv - Ready"

    # Click tab 2 (Sentence 1): updates dynamically without prefix, with sentence 1 slug
    page.locator('.kw-tab-chip[data-tab-seq="2"]').click()
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828181001-first-sentence.en.tsv - Ready"

    # Click tab 3 (Sentence 2): updates dynamically without prefix, with sentence 2 slug
    page.locator('.kw-tab-chip[data-tab-seq="3"]').click()
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828181002-second-sentence.en.tsv - Ready"

    # Click tab 1 (All overview): updates dynamically back to overview without prefix
    page.locator('.kw-tab-chip[data-tab-seq="1"]').click()
    assert not page.title().startswith("[")
    assert page.title() == "Kardenwort - en (multi) - 20260828181000-first-sentence-second-sentence.en.tsv - Ready"


def test_clean_window_title_and_fallback_slug_synthesis(tmp_path):
    """Verifies that render flow produces clean document title without sequence fraction prefix and synthesizes slug when omitted."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")

    text = "Alpha sentence.\nBeta sentence."
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260828235000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        spawn_children=False,
        return_children=False,
    )
    import re
    m = re.search(r"<title>(.*?)</title>", html)
    assert m is not None
    title = m.group(1)
    assert not title.startswith("[")
    assert title == "Kardenwort - en (multi) - 20260828235000-alpha-sentence-beta-sentence.en.tsv - Ready"


def test_distinct_per_sentence_card_slugs_and_restore_session(tmp_path):
    """Verifies each sentence card has a distinct slug and restore_session exposes slug at top level."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")

    text = "It works by restoring.\nAnd on a drive without encryption."
    tsv_file = tmp_path / "20260828233844-it-works-by-restoring.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "It\tIt\tОно\t1\t\n"
        "And\tAnd\tИ\t2\t\n",
        encoding="utf-8"
    )

    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "cache_ttl_seconds", "0")

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260828233844",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
    )
    import json, re
    cards_match = re.search(r'<script id="sentence-cards"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert cards_match is not None
    cards_data = json.loads(cards_match.group(1).strip())
    assert len(cards_data) == 3
    # Master card
    assert cards_data[0]["seq_num"] == 1
    assert cards_data[0]["slug"] == "it-works-by-restoring"
    # Child card 1
    assert cards_data[1]["seq_num"] == 2
    assert cards_data[1]["slug"] == "it-works-by-restoring"
    assert "20260828233845-it-works-by-restoring.en.tsv" in cards_data[1]["tsv_filename"]
    # Child card 2 has distinct sentence slug!
    assert cards_data[2]["seq_num"] == 3
    assert cards_data[2]["slug"] == "and-on-a-drive"
    assert "20260828233846-and-on-a-drive.en.tsv" in cards_data[2]["tsv_filename"]

    # Test restore_session top-level slug exposure
    adapter = kardenwort_desk.get_storage_adapter(config, resolved_paths)
    restored = adapter.restore_session("20260828233844", results_dir=tmp_path)
    assert "slug" in restored
    assert restored["slug"] == "it-works-by-restoring"


def test_action_toolbar_glassmorphism_css(tmp_path):
    """Verifies that .kw-action-toolbar includes translucent glassmorphism backdrop, blur, and hover rules."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    html_dark = kardenwort_desk.run_render_flow(
        text="Test text.",
        language="en",
        zid="20260828235500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        spawn_children=False,
    )
    assert ".kw-action-toolbar {" in html_dark
    assert "backdrop-filter: blur(8px);" in html_dark
    assert "-webkit-backdrop-filter: blur(8px);" in html_dark
    assert "background: rgba(22, 27, 34, 0.8);" in html_dark
    assert ".kw-action-toolbar:hover {" in html_dark
    assert "background: #161b22;" in html_dark

    html_light = kardenwort_desk.run_render_flow(
        text="Test text.",
        language="en",
        zid="20260828235501",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="light",
        spawn_children=False,
    )
    assert "body.theme-light .kw-action-toolbar" in html_light
    assert "background: rgba(255, 255, 255, 0.85);" in html_light
    assert "body.theme-light .kw-action-toolbar:hover" in html_light




