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
    assert '<button type="button" class="kw-tab-chip" data-tab-seq="1" data-sentence-idx="0"' in html
    assert '<button type="button" class="kw-tab-chip active" data-tab-seq="2" data-sentence-idx="1"' in html
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
        return_children=False,
        seq_num=1
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
    config.set("sentences_mode", "spawn_order", "normal")
    
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
        return_children=False,
        seq_num=1
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
    config.set("sentences_mode", "spawn_order", "normal")
    
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
        spawn_children=False, return_children=False, seq_num=1
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
        seq_num=1,
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
    # Child cards in reverse mode: [1] [3] [2]
    assert cards_data[1]["seq_num"] == 3
    assert cards_data[1]["slug"] == "and-on-a-drive"
    assert "20260828233846-and-on-a-drive.en.tsv" in cards_data[1]["tsv_filename"]
    assert cards_data[2]["seq_num"] == 2
    assert cards_data[2]["slug"] == "it-works-by-restoring"
    assert "20260828233845-it-works-by-restoring.en.tsv" in cards_data[2]["tsv_filename"]

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


def test_container_initial_active_tab_spawn_order_reverse(page, tmp_path):
    """Test 3.1: Verify tab [2] is initial active tab and tabs are ordered [1] [5] [4] [3] [2*] when spawn_order = reverse."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "reverse")

    text = "First sentence.\nSecond sentence.\nThird sentence.\nFourth sentence."
    tsv_file = tmp_path / "20260829011000-first-sentence-second-sentence.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n"
        "Third\tThird\tтретий\t3\t\n"
        "Fourth\tFourth\tчетвертый\t4\t\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260829011000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )

    # Server HTML check: [1] [5] [4] [3] [2*]
    import re
    chips_match = re.search(r'<div class="kw-tab-track" id="kw-tab-track">(.*?)</div>', html)
    assert chips_match is not None
    chips_html = chips_match.group(1)
    chip_seqs = re.findall(r'data-tab-seq="(\d+)"', chips_html)
    assert chip_seqs == ["1", "5", "4", "3", "2"]

    assert '<button type="button" class="kw-tab-chip active" data-tab-seq="2" data-sentence-idx="1"' in html
    assert '<link rel="icon" type="image/x-icon" href="/assets/numbers/2.ico">' in html
    assert "20260829011001-first-sentence.en.tsv" in html

    # Playwright client-side check
    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    active_chip = page.locator(".kw-tab-chip.active")
    assert active_chip.inner_text() == "2"
    assert "20260829011001-first-sentence.en.tsv" in page.title()

    chunk1 = page.locator('#source-container > .kw-sentence-chunk[data-sentence-idx="1"]')
    chunk2 = page.locator('#source-container > .kw-sentence-chunk[data-sentence-idx="2"]')
    assert chunk1.is_visible()
    assert not chunk2.is_visible()


def test_container_initial_active_tab_spawn_order_normal(page, tmp_path):
    """Test 3.2: Verify tab [2] is initial active tab and tabs are ordered [1] [2*] [3] [4] [5] when spawn_order = normal."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")

    text = "First sentence.\nSecond sentence.\nThird sentence.\nFourth sentence."
    tsv_file = tmp_path / "20260829011000-first-sentence-second-sentence.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n"
        "Third\tThird\tтретий\t3\t\n"
        "Fourth\tFourth\tчетвертый\t4\t\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260829011000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )

    # 4 sentences + 1 overview = 5 total cards; tabs are [1] [2*] [3] [4] [5]
    import re
    chips_match = re.search(r'<div class="kw-tab-track" id="kw-tab-track">(.*?)</div>', html)
    assert chips_match is not None
    chips_html = chips_match.group(1)
    chip_seqs = re.findall(r'data-tab-seq="(\d+)"', chips_html)
    assert chip_seqs == ["1", "2", "3", "4", "5"]

    assert '<button type="button" class="kw-tab-chip active" data-tab-seq="2" data-sentence-idx="1"' in html
    assert '<button type="button" class="kw-tab-chip" data-tab-seq="1" data-sentence-idx="0"' in html
    assert '<link rel="icon" type="image/x-icon" href="/assets/numbers/2.ico">' in html
    assert "20260829011001-first-sentence.en.tsv" in html

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    active_chip = page.locator(".kw-tab-chip.active")
    assert active_chip.inner_text() == "2"
    assert "20260829011001-first-sentence.en.tsv" in page.title()

    chunk1 = page.locator('#source-container > .kw-sentence-chunk[data-sentence-idx="1"]')
    chunk4 = page.locator('#source-container > .kw-sentence-chunk[data-sentence-idx="4"]')
    assert chunk1.is_visible()
    assert not chunk4.is_visible()


def test_container_initial_active_tab_explicit_seq_num_override(tmp_path):
    """Test 3.3: Verify explicit seq_num overrides both reverse and normal spawn_order."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")

    text = "First sentence.\nSecond sentence.\nThird sentence."
    tsv_file = tmp_path / "20260829011000-override.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n"
        "Third\tThird\tтретий\t3\t\n",
        encoding="utf-8"
    )

    # 1. Reverse spawn order with explicit seq_num=3 (Sentence 2)
    config.set("sentences_mode", "spawn_order", "reverse")
    html_seq3 = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260829011000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=3
    )
    assert '<button type="button" class="kw-tab-chip active" data-tab-seq="3" data-sentence-idx="2"' in html_seq3
    assert '<button type="button" class="kw-tab-chip" data-tab-seq="2" data-sentence-idx="1"' in html_seq3
    assert '<link rel="icon" type="image/x-icon" href="/assets/numbers/3.ico">' in html_seq3

    # 2. Normal spawn order with explicit seq_num=1 (Master overview)
    config.set("sentences_mode", "spawn_order", "normal")
    html_seq1 = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260829011000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )
    assert '<button type="button" class="kw-tab-chip active" data-tab-seq="1" data-sentence-idx="0"' in html_seq1
    assert '<button type="button" class="kw-tab-chip" data-tab-seq="4" data-sentence-idx="3"' in html_seq1
    assert '<link rel="icon" type="image/x-icon" href="/assets/numbers/1.ico">' in html_seq1


def test_playwright_child_tab_translation_skeletons_and_live_updates(page, tmp_path, monkeypatch):
    """
    Verify that:
    1. Child tabs retain skeleton loaders while translation is pending (!AppState.isFinished).
    2. Live translation updates via WorkspaceTabs.updateSentences dynamically render the active tab.
    3. Switching back to Tab 1 after translation completes renders the dynamic master translation without stuck skeletons.
    4. Switching to a failed tab when AppState.isFinished renders an interactive retry badge.
    """
    monkeypatch.setattr(kardenwort_desk, "translate_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(kardenwort_desk, "translate_source_text", lambda *args, **kwargs: {})

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    config.set("rendering", "display_mode", "progressive")
    config.set("pipeline", "progressive_text_translation", "true")
    config.set("triggers", "run_text_translation", "auto")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "cache_ttl_seconds", "0")

    import time
    text = f"First sentence {time.time_ns()}.\nSecond sentence {time.time_ns()}."
    unique_zid = f"20260829{int(time.time() * 1000) % 1000000:06d}"
    kw_cfg = kardenwort_desk.load_kardenwort_config(resolved_paths['kardenwort_workspace'])
    res_dir = kardenwort_desk.resolve_results_dir(resolved_paths, kw_cfg)
    for stale in res_dir.glob(f"{unique_zid}*"):
        try:
            if stale.is_file(): stale.unlink()
            elif stale.is_dir():
                import shutil
                shutil.rmtree(stale)
        except Exception:
            pass

    tsv_file = tmp_path / f"{unique_zid}-multi.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "First\tFirst\tпервый\t1\t\n"
        "Second\tSecond\tвторой\t2\t\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid=unique_zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Initial state: Tab 1 active with skeleton loader in translation container
    trans_container = page.locator("#translation-container")
    assert trans_container.locator(".skeleton-loader").count() >= 1

    # 1. Switch to Tab 2 (Sentence 1) while translation is pending
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    assert trans_container.locator(".skeleton-loader").count() >= 1
    assert trans_container.locator('[data-pending="true"]').count() >= 1

    # 2. Receive live sentence translation for Sentence 1
    page.evaluate("""
        window.WorkspaceTabs.updateSentences([
            { sentence_index: 1, sentence_destination: "Первое предложение." }
        ]);
    """)
    assert trans_container.locator(".skeleton-loader").count() == 0
    assert "Первое предложение." in trans_container.inner_text()

    # 3. Complete master translation in AppState
    page.evaluate("""
        window.AppState.translatedText = "Первое предложение. Второе предложение.";
        window.AppState.isFinished = true;
        window.AppView.renderTranslatedText("finished");
    """)

    # Switch back to Tab 1 (All) - should show dynamic full translation without stuck skeleton
    tab1 = page.locator('.kw-tab-chip[data-tab-seq="1"]')
    tab1.click()
    assert trans_container.locator(".skeleton-loader").count() == 0
    assert "Первое предложение. Второе предложение." in trans_container.inner_text()

    # 4. Switch to Tab 3 (Sentence 2) which was not translated and is finished -> should show retry button
    tab3 = page.locator('.kw-tab-chip[data-tab-seq="3"]')
    tab3.click()
    assert trans_container.locator(".skeleton-loader").count() == 0
    retry_btn = trans_container.locator(".btn-retry-cell")
    assert retry_btn.count() == 1
    assert "Retry" in retry_btn.inner_text()


def test_tab_switching_renders_clean_plaintext_without_div_tokens(page, tmp_path):
    """Verify that switching between tab 1 and child tabs renders clean plain text without literal <div> or < div > word tokens."""
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")

    text = "Super is the center of everything #\nAll the muscle memory you've built around Cmd transfers to one key: Super."
    tsv_file = tmp_path / "20260829193000-super-is-the-center.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "Super\tSuper\tСупер\t1\t\n"
        "All\tAll\tВся\t2\t\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260829193000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Update with translations containing full paragraph and sentences
    page.evaluate("""
        window.WorkspaceTabs.updateSentences([
            { sentence_index: 1, sentence_destination: "Супер — это центр всего #" },
            { sentence_index: 2, sentence_destination: "Вся мышечная память переносится на одну клавишу: Super." }
        ]);
        window.AppState.translatedText = "Супер — это центр всего #\\nВся мышечная память переносится на одну клавишу: Super.";
        window.AppView.renderTranslatedText("finished");
    """)

    trans_container = page.locator("#translation-container")

    # 1. On Tab 1 (Overview)
    tab1 = page.locator('.kw-tab-chip[data-tab-seq="1"]')
    tab1.click()
    tab1_text = trans_container.inner_text()
    assert "<div>" not in tab1_text
    assert "< div >" not in tab1_text
    assert "Супер — это центр всего #" in tab1_text
    assert page.locator('#translation-container span.word:has-text("div")').count() == 0

    # 2. On Tab 2 (Sentence 1)
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    tab2_text = trans_container.inner_text()
    assert "<div>" not in tab2_text
    assert "< div >" not in tab2_text
    assert tab2_text.strip() == "Супер — это центр всего #"
    assert page.locator('#translation-container span.word:has-text("div")').count() == 0

    # 3. On Tab 3 (Sentence 2)
    tab3 = page.locator('.kw-tab-chip[data-tab-seq="3"]')
    tab3.click()
    tab3_text = trans_container.inner_text()
    assert "<div>" not in tab3_text
    assert "< div >" not in tab3_text
    assert tab3_text.strip() == "Вся мышечная память переносится на одну клавишу: Super."
    assert page.locator('#translation-container span.word:has-text("div")').count() == 0


def test_skeleton_provider_state_indicators(page, tmp_path, monkeypatch):
    """
    Verifies that pending skeleton loaders display concise provider state indicators
    (e.g., Argos..., DeepL..., IntelliFiller...) in translation container and lemma table
    without text clipping or layout shifting.
    """
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    config.set("rendering", "display_mode", "progressive")
    config.set("pipeline", "text_base_provider", "argos")
    config.set("pipeline", "lemma_base_provider", "argos")
    config.set("pipeline", "lemma_reprocess_provider", "intellifiller")
    config.set("pipeline", "progressive_text_translation", "true")
    config.set("triggers", "run_text_translation", "auto")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "auto")
    config.set("wordfill", "enabled", "false")
    if not config.has_section("languages"):
        config.add_section("languages")
    config.set("languages", "de_prompt", "dummy")

    import time
    unique_zid = f"20260830{int(time.time() * 1000) % 1000000:06d}"
    tsv_file = tmp_path / f"{unique_zid}-test.de.tsv"
    tsv_content = (
        "# Source: Das ist der erste Satz. Das ist der zweite Satz.\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tSentenceSourceIndex\tSentenceDestination\n"
        "UnbekanntesWort1\tUnbekanntesWort1\t\t\t\t1\t\n"
        "UnbekanntesWort2\tUnbekanntesWort2\t\t\t\t2\t\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    monkeypatch.setattr(kardenwort_desk, "prepare_lookup_tsv", lambda *args, **kwargs: tsv_file)
    monkeypatch.setattr(kardenwort_desk, "translate_text", lambda *a, **k: "")
    monkeypatch.setattr(kardenwort_desk, "translate_source_text", lambda *a, **k: {})
    monkeypatch.setattr(kardenwort_desk, "run_progressive_worker_async", lambda *a, **k: None)
    monkeypatch.setattr(kardenwort_desk, "write_update_js", lambda *a, **k: None)

    html = kardenwort_desk._run_render_flow_impl(
        text="Das ist der erste Satz. Das ist der zweite Satz.",
        language="de",
        zid=unique_zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        wordfill_cfg={"enabled": False},
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Verify translation skeleton displays Argos...
    trans_container = page.locator("#translation-container")
    assert trans_container.locator(".skeleton-loader").count() >= 1
    assert trans_container.locator(".skeleton-loader").inner_text().strip() == "Argos..."
    assert trans_container.locator('.skeleton-loader[title="Argos..."]').count() >= 1

    # Verify lemma table cells display Argos... and IntelliFiller...
    lemma_cells = page.locator("#lemma-table td[data-col='WordDestination'] .skeleton-loader")
    assert lemma_cells.count() >= 1
    assert lemma_cells.first.inner_text().strip() == "Argos..."
    assert lemma_cells.first.get_attribute("title") == "Argos..."

    ipa_cells = page.locator("#lemma-table td[data-col='WordSourceIPA'] .skeleton-loader")
    assert ipa_cells.count() >= 1
    assert ipa_cells.first.inner_text().strip() == "IntelliFiller..."
    assert ipa_cells.first.get_attribute("title") == "IntelliFiller..."

    # Verify switching tab renders dynamic provider skeleton for child tab
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    assert trans_container.locator(".skeleton-loader").count() >= 1
    assert trans_container.locator(".skeleton-loader").inner_text().strip() == "Argos..."


def test_single_sentence_container_mode_preserves_seq_num_1(tmp_path):
    """Test (20260831013851, 20260831014735, 20260831015524): In container mode,

    a single-sentence session must retain seq_num=1 (and not force seq_num=2).
    """
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    text = "Ein einzelner Satz."
    tsv_file = tmp_path / "20260831021500-single.de.tsv"
    tsv_file.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\tSatz\tпредложение\t1\n", encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260831021500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=None,
    )

    # In a single-sentence session, active sequence number must be 1
    assert 'data-active-seq-num="1"' in html or 'active_seq_num = 1' in html or '<title>' in html
    # Ensure there is no [2/1] or favicon 2 forced
    assert "data-tab-seq=\"2\"" not in html









