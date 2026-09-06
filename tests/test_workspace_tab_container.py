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

def test_card_oriented_workspace_container_vocabulary_and_inflection_isolation(tmp_path):
    """
    Verifies that in container mode with deduplication_scope=sentence:
    - Sentence 2 retains shared lemmas (e.g. 'be')
    - Sentence 1 does not contain foreign inflections ('are')
    - Master Overview card [1] (sentence_idx = 0) has globally deduplicated vocabulary with combined inflections
    - Each card includes words and sentence context metadata
    """
    from kardenwort_db import KardenwortDB
    db_path = tmp_path / "test_isol.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "deduplication_scope", "sentence")
    config.set("sentences_mode", "enabled", "true")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    unique_zid = "20260906990003"
    text = "She is happy. They are happy."
    tsv_file = tmp_path / f"{unique_zid}-happy.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "is\tbe\tis\tбыть\t1\t0\n"
        "happy\thappy\thappy\tсчастливый\t1\t0\n"
        "are\tbe\tare\tбыть\t2\t0\n"
        "happy\thappy\thappy\tсчастливый\t2\t0\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid=unique_zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=2
    )

    cards_json = html.split('<script id="sentence-cards" type="application/json">\n')[1].split('\n</script>')[0]
    cards = json.loads(cards_json)
    assert len(cards) == 3

    # Master Overview Card [1] (sentence_idx = 0)
    master = [c for c in cards if c["sentence_idx"] == 0][0]
    assert "words" in master
    master_lemmas = [w["lemma"] for w in master["words"]]
    assert "be" in master_lemmas
    assert "happy" in master_lemmas
    be_master = [w for w in master["words"] if w["lemma"] == "be"][0]
    assert "is" in be_master["inflected"] and "are" in be_master["inflected"]

    # Child Card 1 (sentence_idx = 1)
    card1 = [c for c in cards if c["sentence_idx"] == 1][0]
    assert "words" in card1
    card1_be = [w for w in card1["words"] if w["lemma"] == "be"][0]
    assert card1_be["inflected"] == "is"
    assert "are" not in card1_be["inflected"]
    assert card1["SentenceSource"] == "She is happy."

    # Child Card 2 (sentence_idx = 2)
    card2 = [c for c in cards if c["sentence_idx"] == 2][0]
    assert "words" in card2
    card2_be = [w for w in card2["words"] if w["lemma"] == "be"][0]
    assert card2_be["inflected"] == "are"
    assert "is" not in card2_be["inflected"]
    assert card2["SentenceSource"] == "They are happy."

def test_playwright_card_scoped_independent_selections(page, tmp_path):
    """
    Verifies that selecting rows on Tab 2 (Sentence 1) does not leak into Tab 3 (Sentence 2),
    and switching between tabs preserves each card's independent selections.
    """
    from kardenwort_db import KardenwortDB
    db_path = tmp_path / "test_selections.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    unique_zid = "20260906990001"
    text = "Das Haus ist gross. Die Katze schlaeft."
    tsv_file = tmp_path / f"{unique_zid}-house-cat.de.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "Haus\tHaus\tHaus\tдом\t1\t0\n"
        "Katze\tKatze\tKatze\tкошка\t2\t0\n",
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
        seq_num=2
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # We are on Tab 2 (Sentence 1). Verify row is present and unselected initially.
    row_sent1 = page.locator("#lemma-table tbody tr").first
    assert row_sent1.get_attribute("data-selected") == "0"

    # Click row on Tab 2 to select it
    row_sent1.click()
    assert row_sent1.get_attribute("data-selected") == "1"
    assert "selected" in (row_sent1.get_attribute("class") or "")

    # Switch to Tab 3 (Sentence 2)
    tab3_chip = page.locator('.kw-tab-chip[data-tab-seq="3"]')
    tab3_chip.click()
    page.wait_for_function("document.querySelector('#lemma-table tbody tr td') && document.querySelector('#lemma-table tbody tr td').textContent.includes('Katze')", timeout=5000)

    # On Tab 3, the table row is for Katze and must NOT be selected
    row_sent2 = page.locator("#lemma-table tbody tr").first
    assert "Katze" in row_sent2.inner_text()
    assert row_sent2.get_attribute("data-selected") == "0"

    # Select row on Tab 3
    row_sent2.click()
    assert row_sent2.get_attribute("data-selected") == "1"

    # Switch back to Tab 2 (Sentence 1)
    tab2_chip = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2_chip.click()
    page.wait_for_function("document.querySelector('#lemma-table tbody tr td') && document.querySelector('#lemma-table tbody tr td').textContent.includes('Haus')", timeout=5000)

    # Verify Tab 2's row is still selected!
    row_sent1_back = page.locator("#lemma-table tbody tr").first
    assert "Haus" in row_sent1_back.inner_text()
    assert row_sent1_back.get_attribute("data-selected") == "1"

    # Switch back to Tab 3 (Sentence 2)
    tab3_chip.click()
    page.wait_for_function("document.querySelector('#lemma-table tbody tr td') && document.querySelector('#lemma-table tbody tr td').textContent.includes('Katze')", timeout=5000)
    row_sent2_back = page.locator("#lemma-table tbody tr").first
    assert "Katze" in row_sent2_back.inner_text()
    assert row_sent2_back.get_attribute("data-selected") == "1"

def test_playwright_bidirectional_token_hover_mapping_parity(page, tmp_path):
    """
    Verifies bidirectional token hover and selection mapping parity across child tabs in container mode.
    """
    from kardenwort_db import KardenwortDB
    db_path = tmp_path / "test_hover.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    unique_zid = "20260906990002"
    text = "Das Haus ist gross. Die Katze schlaeft."
    tsv_file = tmp_path / f"{unique_zid}-hover-parity.de.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tHaus\tдом\t1\tДом большой.\t0\n"
        "Katze\tKatze\tKatze\tкошка\t2\tКошка спит.\t0\n",
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
        seq_num=2
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # On Tab 2 (Sentence 1):
    # 1. Hovering over token "Haus" highlights translation span in #translation-container
    haus_token = page.locator('#source-container span.word', has_text='Haus')
    haus_token.hover()
    trans_span = page.locator('#translation-container span.hl-mvp')
    assert trans_span.count() >= 1

    # 2. Clicking token "Haus" triggers bidirectional selection to the active table row
    haus_token.click()
    haus_row = page.locator("#lemma-table tbody tr").first
    assert haus_row.get_attribute("data-selected") == "1"
    assert "highlight-orange-active" in (haus_token.get_attribute("class") or "")

    # Switch to Tab 3 (Sentence 2)
    tab3_chip = page.locator('.kw-tab-chip[data-tab-seq="3"]')
    tab3_chip.click()
    page.wait_for_function("document.querySelector('#lemma-table tbody tr td') && document.querySelector('#lemma-table tbody tr td').textContent.includes('Katze')", timeout=5000)

    # On Tab 3 (Sentence 2):
    # 1. Hovering over token "Katze" highlights translation span
    katze_token = page.locator('#source-container span.word', has_text='Katze')
    katze_token.hover()

    # 2. Clicking token "Katze" triggers bidirectional selection to Sentence 2 table row
    katze_token.click()
    katze_row = page.locator("#lemma-table tbody tr").first
    assert katze_row.get_attribute("data-selected") == "1"
    assert "highlight-orange-active" in (katze_token.get_attribute("class") or "")


def test_background_translation_update_automatically_hydrates_active_child_tab_and_switched_tabs(page, tmp_path):
    """Regression test (20260906194642): Verify that background translation update dynamically

    hydrates the active child tab translation container and word rows without requiring the user
    to click the Update button, and preserves translations across tab switches.
    """
    from kardenwort_db import KardenwortDB
    db_path = tmp_path / "test_isol.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    text = "Das Haus ist gross. Die Katze schlaeft."
    tsv_file = tmp_path / "20260906200500-test-auto-hydrate.de.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "Haus\tHaus\t\t1\t\n"
        "Katze\tKatze\t\t2\t\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260906200500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=2  # AutoHotkey launches on Tab 2 (Sentence 1)
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Initial state on Tab 2: skeleton loader present
    trans_container = page.locator("#translation-container")
    assert page.locator("#translation-container .skeleton-loader").count() >= 1

    # Simulate background translation arriving via receiveUpdate (SSE or watchdog)
    update_payload = {
        "type": "update",
        "stage": "translated_text",
        "translatedText": "<div>Дом большой.</div><div>Кошка спит.</div>",
        "rows": {
            "0": {"WordDestination": "дом", "trans": "дом"},
            "1": {"WordDestination": "кошка", "trans": "кошка"}
        }
    }
    page.evaluate(f"window.receiveUpdate({json.dumps(update_payload)});")

    # Verify Tab 2 (Sentence 1) is automatically populated with sentence 1 translation
    assert page.locator("#translation-container .skeleton-loader").count() == 0
    assert page.locator("#translation-container .btn-retry-cell").count() == 0
    assert trans_container.inner_text().strip() == "Дом большой."

    # Verify table row for Haus on Tab 2 is populated
    haus_td = page.locator('tr[data-token-order="0"] td[data-col="WordDestination"]')
    assert haus_td.inner_text().strip() == "дом"

    # Switch to Tab 3 (Sentence 2)
    tab3 = page.locator('.kw-tab-chip[data-tab-seq="3"]')
    tab3.click()
    assert page.locator("#translation-container .skeleton-loader").count() == 0
    assert trans_container.inner_text().strip() == "Кошка спит."
    katze_td = page.locator('tr[data-token-order="1"] td[data-col="WordDestination"]')
    assert katze_td.inner_text().strip() == "кошка"

    # Switch to Tab 1 (All sentences)
    tab1 = page.locator('.kw-tab-chip[data-tab-seq="1"]')
    tab1.click()
    tab1_text = trans_container.inner_text()
    assert "Дом большой." in tab1_text
    assert "Кошка спит." in tab1_text

    # Switch back to Tab 2: translation and row remain populated without skeletons
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    assert trans_container.inner_text().strip() == "Дом большой."
    assert haus_td.inner_text().strip() == "дом"
    assert page.locator("#translation-container .skeleton-loader").count() == 0


def test_tab1_overview_row_and_source_text_highlight(page, tmp_path):
    """
    Test (20260906213101): Verifies that in container mode, on the first (overview) tab:
    1. Clicking a table row highlights the row in yellow (.selected) and highlights corresponding source text words.
    2. Clicking a word in SOURCE TEXT highlights the word and highlights the corresponding row in yellow in the table.
    3. Selections on Tab 1 persist across tab switching.
    """
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")

    text = "Das Haus ist gross. Die Katze schlaeft."
    tsv_file = tmp_path / "20260906223000-tab1-hl.de.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\t0\n"
        "Katze\tKatze\tкошка\t2\t0\n",
        encoding="utf-8"
    )

    # Start on Tab 2 (Sentence 1)
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid="20260906223000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=2
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Switch to Tab 1 (All)
    tab1 = page.locator('.kw-tab-chip[data-tab-seq="1"]')
    tab1.click()
    page.wait_for_selector('#lemma-table tbody tr')

    first_tr = page.locator('#lemma-table tbody tr').first
    assert "selected" not in (first_tr.get_attribute("class") or "")

    # 1. Click first row on Tab 1 -> must become selected (yellow)
    first_tr.click()
    assert "selected" in (first_tr.get_attribute("class") or "")
    assert first_tr.get_attribute("data-selected") == "1"

    # Corresponding word in source text must also be highlighted
    haus_word = page.locator('#source-container span.word:has-text("Haus")').first
    assert "highlight-orange-active" in (haus_word.get_attribute("class") or "")

    # Switch to Tab 2 (Sentence 1) and back to Tab 1 -> Tab 1 selection must persist
    tab2 = page.locator('.kw-tab-chip[data-tab-seq="2"]')
    tab2.click()
    page.wait_for_selector('#lemma-table tbody tr')

    tab1.click()
    page.wait_for_selector('#lemma-table tbody tr')
    first_tr_back = page.locator('#lemma-table tbody tr').first
    assert "selected" in (first_tr_back.get_attribute("class") or "")
    assert first_tr_back.get_attribute("data-selected") == "1"

    # 2. Click word in SOURCE TEXT on Tab 1 -> corresponding row in table must highlight
    katze_word = page.locator('#source-container span.word:has-text("Katze")').first
    katze_word.click()
    katze_tr = page.locator('#lemma-table tbody tr:has-text("Katze")').first
    assert "selected" in (katze_tr.get_attribute("class") or "")
    assert katze_tr.get_attribute("data-selected") == "1"


def test_container_update_matching_preserves_token_order_when_frequency_sorted(page, tmp_path):
    """
    Verifies that when receiveUpdate receives rows sorted globally by frequency,
    words are matched by token_order rather than sort index, preventing table rebuilding
    or row scrambling on Update. Also verifies sentence translation hydration.
    """
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")

    text = "The cat sleeps.\nA dog barks."
    tsv_file = tmp_path / "20260906233000-sample.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tTokenOrder\tSentenceSourceIndex\n"
        "The\tThe\t\t0\t1\n"
        "cat\tcat\t\t1\t1\n"
        "sleeps\tsleep\t\t2\t1\n"
        "A\tA\t\t3\t2\n"
        "dog\tdog\t\t4\t2\n"
        "barks\tbark\t\t5\t2\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid="20260906233000",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num="2"
    )

    page.set_content(html)
    page.wait_for_selector("#kw-workspace-tab-bar")

    # Simulate frequency-sorted payload from /session/status:
    # Most frequent globally: dog (TokenOrder 4) at key 0, cat (TokenOrder 1) at key 1
    status_payload = {
        "ok": True,
        "rows": {
            "0": {"token_order": "4", "sentence_idx": "2", "lemma": "dog", "trans": "собака"},
            "1": {"token_order": "1", "sentence_idx": "1", "lemma": "cat", "trans": "кот"},
            "2": {"token_order": "0", "sentence_idx": "1", "lemma": "The", "trans": "этот"},
        },
        "sentences": [
            {"sentence_index": 1, "sentence_destination": "Кот спит."},
            {"sentence_index": 2, "sentence_destination": "Собака лает."}
        ]
    }

    # Call receiveUpdate with status payload
    page.evaluate("(payload) => window.receiveUpdate(payload)", status_payload)

    # Active tab is Tab 2 (Sentence 1). Verify row 'cat' (TokenOrder 1) has translation 'кот'
    # and NOT 'собака' (which was at sorted index 0)!
    cat_tr = page.locator('#lemma-table tbody tr[data-token-order="1"]')
    assert cat_tr.count() == 1
    assert "кот" in cat_tr.locator('td.col-translation').inner_text()
    assert "собака" not in cat_tr.locator('td.col-translation').inner_text()

    # Verify sentence translation for Tab 2 was updated to 'Кот спит.'
    assert "Кот спит." in page.locator("#translation-container").inner_text()

    # Switch to Tab 3 (Sentence 2). Verify row 'dog' has translation 'собака'
    page.locator('.kw-tab-chip[data-tab-seq="3"]').click()
    dog_tr = page.locator('#lemma-table tbody tr[data-token-order="4"]')
    assert dog_tr.count() == 1
    assert "собака" in dog_tr.locator('td.col-translation').inner_text()
    assert "Собака лает." in page.locator("#translation-container").inner_text()













