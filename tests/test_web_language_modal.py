import json
import pytest
from pathlib import Path
from kardenwort_desk import (
    run_render_flow,
    load_config,
    get_language_display_name,
)

def get_modal_test_page_html(tmp_path: Path, mismatch_info=None, theme="dark") -> str:
    config, resolved, _, _ = load_config()
    tsv_file = tmp_path / "20260827005722-test.en.tsv"
    tsv_file.write_text("# Headers\tLemma\tInflected\n# Data\tHaus\tHäuser\n", encoding="utf-8")
    
    html = run_render_flow(
        text="Das ist ein deutsches Haus.",
        language="en",
        zid="20260827005722",
        text_mode="single",
        config=config,
        resolved_paths=resolved,
        zoom_level="100",
        theme=theme,
        tsv_path=tsv_file,
        spawn_children=False,
        mismatch_info=mismatch_info,
    )
    return html

def inject_mock_fetch(html: str) -> str:
    mock_script = """<script>
    window.__fetches = [];
    window.fetch = function(url, options) {
        options = options || {};
        var bodyData = null;
        if (options.body) {
            try { bodyData = JSON.parse(options.body); } catch(e) { bodyData = options.body; }
        }
        window.__fetches.push({ url: url, method: options.method || 'GET', body: bodyData });
        return Promise.resolve({
            ok: true,
            status: 200,
            json: function() { return Promise.resolve({ ok: true, html: null }); }
        });
    };
    </script>"""
    return html.replace("<head>", f"<head>\n{mock_script}")


def test_language_names_helper():
    assert get_language_display_name("de") == "German"
    assert get_language_display_name("en") == "English"
    assert get_language_display_name("ru") == "Russian"
    assert get_language_display_name("fr") == "French"
    assert get_language_display_name("es") == "Spanish"
    assert get_language_display_name("it") == "Italian"
    assert get_language_display_name("zh") == "Chinese"
    assert get_language_display_name("ja") == "Japanese"
    assert get_language_display_name("pt") == "Portuguese"
    assert get_language_display_name("nl") == "Dutch"
    assert get_language_display_name("pl") == "Polish"
    assert get_language_display_name("uk") == "Ukrainian"
    assert get_language_display_name("unknown") == "Unknown"
    assert get_language_display_name(None) == ""


def test_language_modal_markup_and_styles(tmp_path):
    for theme in ["dark", "light", "white"]:
        html = get_modal_test_page_html(tmp_path, theme=theme)
        assert 'id="kw-lang-modal"' in html
        assert 'class="kw-modal-box"' in html
        assert 'id="kw-lang-modal-title"' in html
        assert 'id="kw-lang-modal-body"' in html
        assert 'id="kw-btn-lang-yes"' in html
        assert 'id="kw-btn-lang-no"' in html
        assert 'id="kw-btn-lang-cancel"' in html
        assert 'class="kw-modal-backdrop"' in html


def test_language_modal_exact_composition_and_text(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein schönes deutsches Haus.",
        "session_zid": "20260827005722",
    }
    html = get_modal_test_page_html(tmp_path, mismatch_info=mismatch)
    page.set_content(html)

    modal = page.locator("#kw-lang-modal")
    assert modal.is_visible()

    title = page.locator("#kw-lang-modal-title")
    assert title.text_content().strip() == "Language Verification"

    body = page.locator("#kw-lang-modal-body")
    expected_body = "The text appears to be German (de), but the active profile is English (en).\n\nSwitch language to German?"
    assert body.text_content().strip() == expected_body

    btn_yes = page.locator("#kw-btn-lang-yes")
    btn_no = page.locator("#kw-btn-lang-no")
    btn_cancel = page.locator("#kw-btn-lang-cancel")

    assert btn_yes.text_content().strip() == "Yes"
    assert btn_no.text_content().strip() == "No"
    assert btn_cancel.text_content().strip() == "Cancel"


def test_language_modal_yes_click_action(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus.",
        "session_zid": "20260827005722",
    }
    html = inject_mock_fetch(get_modal_test_page_html(tmp_path, mismatch_info=mismatch))
    page.set_content(html)

    page.locator("#kw-btn-lang-yes").click()
    page.wait_for_timeout(50)

    fetches = page.evaluate("window.__fetches")
    render_fetches = [f for f in fetches if f["url"] == "/api/v1/render"]
    assert len(render_fetches) == 1
    assert render_fetches[0]["body"]["language"] == "de"
    assert render_fetches[0]["body"]["bypass_lang_check"] is True


def test_language_modal_no_click_action(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus.",
        "session_zid": "20260827005722",
    }
    html = inject_mock_fetch(get_modal_test_page_html(tmp_path, mismatch_info=mismatch))
    page.set_content(html)

    page.locator("#kw-btn-lang-no").click()
    page.wait_for_timeout(50)

    fetches = page.evaluate("window.__fetches")
    render_fetches = [f for f in fetches if f["url"] == "/api/v1/render"]
    assert len(render_fetches) == 1
    assert render_fetches[0]["body"]["language"] == "en"
    assert render_fetches[0]["body"]["bypass_lang_check"] is True


def test_language_modal_cancel_click_action(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus.",
        "session_zid": "20260827005722",
    }
    html = inject_mock_fetch(get_modal_test_page_html(tmp_path, mismatch_info=mismatch))
    page.set_content(html)

    modal = page.locator("#kw-lang-modal")
    assert modal.is_visible()

    page.locator("#kw-btn-lang-cancel").click()
    page.wait_for_timeout(50)

    assert not modal.is_visible()
    fetches = page.evaluate("window.__fetches")
    render_fetches = [f for f in fetches if f["url"] == "/api/v1/render"]
    assert len(render_fetches) == 0


def test_language_modal_keyboard_enter_and_escape(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus.",
        "session_zid": "20260827005722",
    }
    # 1. Test Enter key triggers Yes
    html = inject_mock_fetch(get_modal_test_page_html(tmp_path, mismatch_info=mismatch))
    page.set_content(html)

    page.keyboard.press("Enter")
    page.wait_for_timeout(50)

    fetches = page.evaluate("window.__fetches")
    render_fetches = [f for f in fetches if f["url"] == "/api/v1/render"]
    assert len(render_fetches) == 1
    assert render_fetches[0]["body"]["language"] == "de"

    # 2. Test Escape key triggers Cancel
    page.set_content(html)
    modal = page.locator("#kw-lang-modal")
    assert modal.is_visible()

    page.keyboard.press("Escape")
    page.wait_for_timeout(50)
    assert not modal.is_visible()
