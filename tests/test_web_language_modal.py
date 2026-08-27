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

    set_lang_fetches = [f for f in fetches if f["url"] == "/api/v1/set-language"]
    assert len(set_lang_fetches) == 1
    assert set_lang_fetches[0]["method"] == "POST"
    assert set_lang_fetches[0]["body"]["language"] == "de"



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


def test_language_modal_suppresses_content_on_mismatch(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus. Und hier ist ein Garten.",
        "session_zid": "20260827005722",
    }
    html = get_modal_test_page_html(tmp_path, mismatch_info=mismatch)
    page.set_content(html)

    # 1. Modal must be open
    assert page.locator("#kw-lang-modal").is_visible()

    # 2. Source text, sentence translation, and table rows must be clean/empty
    assert page.locator("#source-container").inner_text().strip() == ""
    assert page.locator("#translation-container").inner_text().strip() == ""
    assert page.locator("#lemma-table tbody tr").count() == 0


def test_language_modal_title_formatting_and_dynamic_update(page, tmp_path):
    config, resolved, _, _ = load_config()
    tsv_file = tmp_path / "20260827005722-test.en.tsv"
    tsv_file.write_text("# Headers\tLemma\tInflected\n# Data\tHaus\tHäuser\n", encoding="utf-8")

    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus.",
        "session_zid": "20260827005722",
    }

    # 1. Single mode title formatting
    html_single = run_render_flow(
        text="Das ist ein Haus.",
        language="en",
        zid="20260827005722",
        text_mode="single",
        config=config,
        resolved_paths=resolved,
        tsv_path=tsv_file,
        spawn_children=False,
        mismatch_info=mismatch,
    )
    page.set_content(inject_mock_fetch(html_single))
    assert page.title() == "Kardenwort - en (single)"

    # Clicking Yes updates title immediately to German
    page.locator("#kw-btn-lang-yes").click()
    page.wait_for_timeout(50)
    assert page.title() == "Kardenwort - de (single)"

    # 2. Multi mode title formatting
    html_multi = run_render_flow(
        text="Das ist ein Haus.\nUnd ein Garten.",
        language="en",
        zid="20260827005723",
        text_mode="multi",
        config=config,
        resolved_paths=resolved,
        tsv_path=tsv_file,
        spawn_children=False,
        mismatch_info=mismatch,
    )
    page.set_content(inject_mock_fetch(html_multi))
    assert page.title() == "Kardenwort - en (multi)"

    # Clicking No updates title in expected language
    page.locator("#kw-btn-lang-no").click()
    page.wait_for_timeout(50)
    assert page.title() == "Kardenwort - en (multi)"


def test_language_modal_button_state_during_inflight_request(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Das ist ein Haus.",
        "session_zid": "20260827005722",
    }
    # Mock fetch with an unresolved promise to keep the request in-flight
    delayed_fetch_script = """<script>
    window.fetch = function() {
        return new Promise(function() {}); // never resolves
    };
    </script>"""
    html = get_modal_test_page_html(tmp_path, mismatch_info=mismatch).replace("<head>", f"<head>\n{delayed_fetch_script}")
    page.set_content(html)

    btn_yes = page.locator("#kw-btn-lang-yes")
    btn_no = page.locator("#kw-btn-lang-no")
    btn_cancel = page.locator("#kw-btn-lang-cancel")

    assert not btn_yes.is_disabled()
    assert not btn_no.is_disabled()
    assert not btn_cancel.is_disabled()

    btn_yes.click()
    page.wait_for_timeout(50)

    # In-flight state: all buttons are disabled
    assert btn_yes.is_disabled()
    assert btn_no.is_disabled()
    assert btn_cancel.is_disabled()


def test_language_modal_multi_sentence_child_tab_spawning(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Erste Satz. Zweite Satz.",
        "session_zid": "20260827005722",
    }
    children_args = [
        "--seq-num", "2", "--restore", "U:\\voothi\\results\\20260827005723-erste.de.tsv",
        "--seq-num", "3", "--restore", "U:\\voothi\\results\\20260827005724-zweite.de.tsv"
    ]
    children_json = json.dumps(children_args)
    mock_spawn_script = f"""<script>
    window.__openedTabs = [];
    window.__spawnCalls = [];
    window.open = function(url, target) {{
        window.__openedTabs.push({{ url: url, target: target }});
        return {{}};
    }};
    window.fetch = function(url, options) {{
        if (url === '/api/v1/spawn-tabs') {{
            var body = (options && options.body) ? JSON.parse(options.body) : {{}};
            window.__spawnCalls.push(body);
            return Promise.resolve({{
                ok: true,
                status: 200,
                json: function() {{ return Promise.resolve({{ ok: true, spawned: 2 }}); }}
            }});
        }}
        return Promise.resolve({{
            ok: true,
            status: 200,
            json: function() {{
                return Promise.resolve({{
                    ok: true,
                    data: {{
                        html: null,
                        children: {children_json}
                    }}
                }});
            }}
        }});
    }};
    </script>"""
    html = get_modal_test_page_html(tmp_path, mismatch_info=mismatch).replace("<head>", f"<head>\n{mock_spawn_script}")
    page.set_content(html)

    page.locator("#kw-btn-lang-yes").click()
    page.wait_for_timeout(50)

    spawn_calls = page.evaluate("window.__spawnCalls")
    assert len(spawn_calls) == 1
    assert len(spawn_calls[0]["urls"]) == 2
    assert spawn_calls[0]["urls"][0] == "/session/render?session_zid=20260827005723&seq_num=2&bypass_lang_check=true"
    assert spawn_calls[0]["urls"][1] == "/session/render?session_zid=20260827005724&seq_num=3&bypass_lang_check=true"

    # Also verify fallback to window.open if /api/v1/spawn-tabs fails
    mock_fallback_script = f"""<script>
    window.__openedTabs = [];
    window.open = function(url, target) {{
        window.__openedTabs.push({{ url: url, target: target }});
        return {{}};
    }};
    window.fetch = function(url, options) {{
        if (url === '/api/v1/spawn-tabs') {{
            return Promise.resolve({{
                ok: false,
                status: 500,
                json: function() {{ return Promise.resolve({{ ok: false }}); }}
            }});
        }}
        return Promise.resolve({{
            ok: true,
            status: 200,
            json: function() {{
                return Promise.resolve({{
                    ok: true,
                    data: {{
                        html: null,
                        children: {children_json}
                    }}
                }});
            }}
        }});
    }};
    </script>"""
    html_fallback = get_modal_test_page_html(tmp_path, mismatch_info=mismatch).replace("<head>", f"<head>\n{mock_fallback_script}")
    page.set_content(html_fallback)
    page.locator("#kw-btn-lang-yes").click()
    page.wait_for_timeout(50)

    opened = page.evaluate("window.__openedTabs")
    assert len(opened) == 2
    assert opened[0]["url"] == "/session/render?session_zid=20260827005723&seq_num=2&bypass_lang_check=true"
    assert opened[1]["url"] == "/session/render?session_zid=20260827005724&seq_num=3&bypass_lang_check=true"


def test_language_modal_reverse_ordered_child_tab_spawning(page, tmp_path):
    mismatch = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "text": "Erste Satz. Zweite Satz. Dritte Satz.",
        "session_zid": "20260827005722",
    }
    # In reverse spawn order, child sessions arrive with highest sequence number first
    children_args = [
        "--seq-num", "4", "--restore", "U:\\voothi\\results\\20260827005725-dritte.de.tsv",
        "--seq-num", "3", "--restore", "U:\\voothi\\results\\20260827005724-zweite.de.tsv",
        "--seq-num", "2", "--restore", "U:\\voothi\\results\\20260827005723-erste.de.tsv",
    ]
    children_json = json.dumps(children_args)
    mock_spawn_script = f"""<script>
    window.__spawnCalls = [];
    window.fetch = function(url, options) {{
        if (url === '/api/v1/spawn-tabs') {{
            var body = (options && options.body) ? JSON.parse(options.body) : {{}};
            window.__spawnCalls.push(body);
            return Promise.resolve({{
                ok: true,
                status: 200,
                json: function() {{ return Promise.resolve({{ ok: true, spawned: 3 }}); }}
            }});
        }}
        return Promise.resolve({{
            ok: true,
            status: 200,
            json: function() {{
                return Promise.resolve({{
                    ok: true,
                    data: {{
                        html: null,
                        children: {children_json}
                    }}
                }});
            }}
        }});
    }};
    </script>"""
    html = get_modal_test_page_html(tmp_path, mismatch_info=mismatch).replace("<head>", f"<head>\n{mock_spawn_script}")
    page.set_content(html)

    page.locator("#kw-btn-lang-yes").click()
    page.wait_for_timeout(50)

    spawn_calls = page.evaluate("window.__spawnCalls")
    assert len(spawn_calls) == 1
    assert len(spawn_calls[0]["urls"]) == 3
    # Exactly in reverse order as delivered by backend
    assert spawn_calls[0]["urls"][0] == "/session/render?session_zid=20260827005725&seq_num=4&bypass_lang_check=true"
    assert spawn_calls[0]["urls"][1] == "/session/render?session_zid=20260827005724&seq_num=3&bypass_lang_check=true"
    assert spawn_calls[0]["urls"][2] == "/session/render?session_zid=20260827005723&seq_num=2&bypass_lang_check=true"


