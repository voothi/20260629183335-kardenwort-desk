import json
import pytest
from pathlib import Path
import kardenwort_desk

def get_desk_page_html(tmp_path, theme="dark", zid="20260826214953"):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    tsv_file = tmp_path / f"{zid}-test.de.tsv"
    tsv_content = (
        "# comment\n"
        "WordSource\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tOxford\n"
        "Haus\tдом\t[haʊs]\tNoun\tA1\n"
        "Baum\tдерево\t[baʊm]\tNoun\tA1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Das Haus und der Baum",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme=theme,
        tsv_path=str(tsv_file),
        spawn_children=False
    )
    return html

def inject_mock_fetch(html):
    mock_script = """<script>
window.__fetches = [];
window.fetch = async function(url, options) {
    var bodyObj = (options && options.body) ? JSON.parse(options.body) : {};
    window.__fetches.push({ url: url, options: options, body: bodyObj });
    if (url === '/session/save') {
        return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, status: 'success' })
        };
    }
    if (url === '/session/reword') {
        return { ok: true, status: 200, json: async () => ({ ok: true, reprocess_started: true, rows: 1 }) };
    }
    if (url === '/session/retext') {
        return { ok: true, status: 200, json: async () => ({ ok: true, retext_started: true }) };
    }
    if (url === '/session/export') {
        return { ok: true, status: 200, json: async () => ({ ok: true, status: 'success', import_complete: true }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
</script>"""
    return html.replace("<head>", f"<head>\n{mock_script}")

def test_toolbar_markup_and_styles_rendered(tmp_path):
    html = get_desk_page_html(tmp_path)
    assert 'class="kw-action-toolbar"' in html
    assert '>Save (Ctrl+S)</button>' in html
    assert '>Update</button>' in html
    assert '>Re-text</button>' in html
    assert '>Re-word</button>' in html
    assert '>Send to Anki</button>' in html
    assert '>Hand Tool</button>' in html
    assert '>Delete</button>' in html
    assert 'id="kw-toast-container"' in html
    assert 'min-width: 110px;' in html
    assert 'padding-bottom: 70px;' in html
    assert '.kw-toast' in html
    assert '.kw-toast-success' in html
    assert '.kw-toast-warning' in html
    assert '.kw-toast-error' in html
    assert '.kw-toast-info' in html
    # Ensure no emojis in toolbar buttons
    for emoji in ['💾', '🔄', '📝', '🔤', '📦', '✋', '🗑️']:
        assert emoji not in html

def test_toolbar_across_themes(tmp_path):
    for idx, theme in enumerate(["dark", "light", "white"]):
        zid = f"2026082621495{idx}"
        html = get_desk_page_html(tmp_path, theme=theme, zid=zid)
        assert f'theme-{theme}' in html
        assert 'id="kw-action-toolbar"' in html
        assert 'id="kw-toast-container"' in html

def test_save_button_dirty_state_and_rest_dispatch(page, tmp_path):
    html = inject_mock_fetch(get_desk_page_html(tmp_path))
    page.set_content(html)

    # Check save button is initially disabled
    save_btn = page.locator("#kw-btn-save")
    assert save_btn.is_disabled()

    # Edit cell to trigger dirty state
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    cell.dblclick()
    edit_input = cell.locator("input")
    edit_input.fill("новое_здание")
    page.keyboard.press("Enter")

    # Now Save button should be enabled
    assert not save_btn.is_disabled()
    assert page.evaluate("window.isDirty()") is True

    # Click save button
    save_btn.click()
    page.wait_for_timeout(100)

    # Verify fetch payload
    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 1
    assert fetches[0]["url"] == "/session/save"
    assert fetches[0]["body"]["session_zid"] == "20260826214953"
    assert len(fetches[0]["body"]["deltas"]) >= 1

    # Verify clean state restored and toast shown
    assert save_btn.is_disabled()
    assert page.evaluate("window.isDirty()") is False
    toast = page.locator(".kw-toast-success")
    assert toast.is_visible()
    assert "Edits saved successfully" in toast.inner_text()

def test_reword_and_retext_rest_dispatch(page, tmp_path):
    html = inject_mock_fetch(get_desk_page_html(tmp_path))
    page.set_content(html)

    # 1. Click Re-word without selection -> warning toast, no fetch
    reword_btn = page.locator("#kw-btn-reword")
    reword_btn.click()
    page.wait_for_timeout(50)
    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 0
    toast_warn = page.locator(".kw-toast-warning")
    assert toast_warn.is_visible()
    assert "Please select rows to re-word." in toast_warn.inner_text()

    # 2. Select row 0 and click Re-word -> dispatches /session/reword
    row0 = page.locator("tr[data-row-id='0']")
    row0.click()
    reword_btn.click()
    page.wait_for_timeout(100)
    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 1
    assert fetches[0]["url"] == "/session/reword"
    assert fetches[0]["body"]["row_ids"] == [0]
    assert fetches[0]["body"]["session_zid"] == "20260826214953"

    # 3. Click Re-text -> dispatches /session/retext
    retext_btn = page.locator("#kw-btn-retext")
    retext_btn.click()
    page.wait_for_timeout(100)
    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 2
    assert fetches[1]["url"] == "/session/retext"
    assert fetches[1]["body"]["session_zid"] == "20260826214953"

def test_send_to_anki_rest_dispatch(page, tmp_path):
    html = inject_mock_fetch(get_desk_page_html(tmp_path))
    page.set_content(html)

    export_btn = page.locator("#kw-btn-export")

    # 1. No selection -> warning toast
    export_btn.click()
    page.wait_for_timeout(50)
    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 0
    toast_warn = page.locator(".kw-toast-warning")
    assert toast_warn.is_visible()
    assert "Please select rows to export." in toast_warn.inner_text()

    # 2. Select rows 0 and 1 -> dispatches /session/export
    page.locator("tr[data-row-id='0']").click()
    page.locator("tr[data-row-id='1']").click()
    export_btn.click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 1
    assert fetches[0]["url"] == "/session/export"
    assert sorted(fetches[0]["body"]["row_ids"]) == [0, 1]
    assert fetches[0]["body"]["session_zid"] == "20260826214953"

    toast_success = page.locator(".kw-toast-success")
    assert toast_success.is_visible()
    assert "2 cards exported to Anki" in toast_success.inner_text()

def test_hand_tool_and_delete_and_shortcuts(page, tmp_path):
    html = get_desk_page_html(tmp_path)
    page.set_content(html)

    # 1. Hand tool toggle
    hand_btn = page.locator("#kw-btn-hand-tool")
    assert not page.evaluate("document.body.classList.contains('text-selection-mode-active')")
    hand_btn.click()
    assert page.evaluate("document.body.classList.contains('text-selection-mode-active')")
    assert "active" in hand_btn.get_attribute("class")

    hand_btn.click()
    assert not page.evaluate("document.body.classList.contains('text-selection-mode-active')")
    assert "active" not in hand_btn.get_attribute("class")

    # 2. Delete selected rows via Delete button & keyboard
    row0 = page.locator("tr[data-row-id='0']")
    row0.click()
    delete_btn = page.locator("#kw-btn-delete")
    delete_btn.click()
    assert not row0.is_visible()
    assert page.evaluate("window.isDirty()") is True

    # 3. Ctrl+Z undo restores row
    page.keyboard.press("Control+z")
    assert row0.is_visible()
    assert page.evaluate("window.isDirty()") is False

    # 4. Delete key deletes row again
    row0.click()
    page.keyboard.press("Delete")
    assert not row0.is_visible()
    assert page.evaluate("window.isDirty()") is True
