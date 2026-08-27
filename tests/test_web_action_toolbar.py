import json
import pytest
from pathlib import Path
import kardenwort_desk

def get_desk_page_html(tmp_path, theme="dark", zid="20260826214953", seq_num=None):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    tsv_file = tmp_path / f"{zid}-test.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\tDas Haus\tДом\t\n"
        "Baum\tBaum\tдерево\t1\tDas Haus\tДом\t\n"
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
        seq_num=seq_num,
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


def test_window_sequence_branding_in_web_view(page, tmp_path):
    # 1. Test with seq_num = 1 (Master window) - clean title without bracketed sequence prefix
    html_seq1 = get_desk_page_html(tmp_path, zid="20260826235951", seq_num=1)
    page.set_content(html_seq1)
    assert page.title() == "Kardenwort - de (single)"
    favicon = page.locator("link[rel='icon']")
    assert favicon.get_attribute("href") == "/assets/numbers/1.ico"
    badge = page.locator("#kw-seq-badge")
    assert badge.is_visible()
    assert badge.inner_text() == "#1"

    # 2. Test with seq_num = 3 (Child window) - clean title, sequence icon + badge preserved
    html_seq3 = get_desk_page_html(tmp_path, zid="20260826235953", seq_num=3)
    page.set_content(html_seq3)
    assert page.title() == "Kardenwort - de (single)"
    assert page.locator("link[rel='icon']").get_attribute("href") == "/assets/numbers/3.ico"
    assert page.locator("#kw-seq-badge").inner_text() == "#3"

    # 3. Test without seq_num (Default fallback)
    html_default = get_desk_page_html(tmp_path, zid="20260826235950", seq_num=None)
    page.set_content(html_default)
    assert page.title() == "Kardenwort - de (single)"
    assert page.locator("link[rel='icon']").get_attribute("href") == "/assets/numbers/1.ico"
    assert not page.locator("#kw-seq-badge").is_visible()

def test_ahk_host_toolbar_suppression(page, tmp_path):
    # 1. Standard web browser mode: toolbar is visible, kw-ahk-native-host is absent
    html = get_desk_page_html(tmp_path, zid="20260826235955", seq_num=1)
    page.set_content(html)
    assert not page.evaluate("document.body.classList.contains('kw-ahk-native-host')")
    assert page.locator("#kw-action-toolbar").is_visible()

    # 2. AutoHotkey ActiveX host mode (window.ahkCall present): toolbar hidden, class added
    mock_ahk_script = "<script>window.ahkCall = function(cmd, arg) {};</script>"
    html_ahk = html.replace("<head>", f"<head>\n{mock_ahk_script}")
    page.set_content(html_ahk)
    assert page.evaluate("document.body.classList.contains('kw-ahk-native-host')")
    assert not page.locator("#kw-action-toolbar").is_visible()

    # 3. AutoHotkey external host mode (window.external.ahkCall present): toolbar hidden
    mock_ext_script = "<script>window.external = { ahkCall: function(cmd, arg) {} };</script>"
    html_ext = html.replace("<head>", f"<head>\n{mock_ext_script}")
    page.set_content(html_ext)
    assert page.evaluate("document.body.classList.contains('kw-ahk-native-host')")
    assert not page.locator("#kw-action-toolbar").is_visible()

def test_mshtml_activex_environment_without_fetch(page, tmp_path):
    page_errors = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # Simulate MSHTML/Trident environment: document.documentMode = 11, window.ActiveXObject defined, window.fetch deleted
    mock_mshtml_env = """<script>
document.documentMode = 11;
window.ActiveXObject = function() {};
delete window.fetch;
delete window.EventSource;
</script>"""
    html = get_desk_page_html(tmp_path, zid="20260827003912", seq_num=2)
    html_mshtml = html.replace("<head>", f"<head>\n{mock_mshtml_env}")

    page.set_content(html_mshtml)

    # Verify no unhandled JavaScript errors during load/watchdog setup
    assert len(page_errors) == 0

    # Verify toolbar is suppressed and kw-ahk-native-host class is present
    assert page.evaluate("document.body.classList.contains('kw-ahk-native-host')")
    assert not page.locator("#kw-action-toolbar").is_visible()

    # Verify action handlers execute safely with fallback toasts without throwing script errors
    page.evaluate("window.onSaveClick && window.onSaveClick()")
    page.evaluate("window.onRetextClick && window.onRetextClick()")
    page.evaluate("window.onRewordClick && window.onRewordClick()")
    page.evaluate("window.onSendToAnkiClick && window.onSendToAnkiClick()")

    assert len(page_errors) == 0


def test_toolbar_api_token_propagation(page, tmp_path):
    html = inject_mock_fetch(get_desk_page_html(tmp_path))
    # Inject window.API_TOKEN
    token_script = "<script>window.API_TOKEN = 'secret-test-token-42';</script>"
    html_with_token = html.replace("<head>", f"<head>\n{token_script}")
    page.set_content(html_with_token)

    # Verify getApiToken() resolves the token
    resolved_tok = page.evaluate("window.getApiToken()")
    assert resolved_tok == "secret-test-token-42"

    # 1. Test Save
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    cell.dblclick()
    edit_input = cell.locator("input")
    edit_input.fill("новое_здание")
    page.keyboard.press("Enter")

    save_btn = page.locator("#kw-btn-save")
    save_btn.click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 1
    assert fetches[0]["url"] == "/session/save"
    assert fetches[0]["options"]["headers"]["X-API-Token"] == "secret-test-token-42"
    assert fetches[0]["body"]["token"] == "secret-test-token-42"

    # 2. Test Retext
    retext_btn = page.locator("#kw-btn-retext")
    retext_btn.click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 2
    assert fetches[1]["url"] == "/session/retext"
    assert fetches[1]["options"]["headers"]["X-API-Token"] == "secret-test-token-42"
    assert fetches[1]["body"]["token"] == "secret-test-token-42"

    # 3. Test Reword
    page.locator("tr[data-row-id='0']").click()
    reword_btn = page.locator("#kw-btn-reword")
    reword_btn.click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 3
    assert fetches[2]["url"] == "/session/reword"
    assert fetches[2]["options"]["headers"]["X-API-Token"] == "secret-test-token-42"
    assert fetches[2]["body"]["token"] == "secret-test-token-42"

    # 4. Test Export
    export_btn = page.locator("#kw-btn-export")
    export_btn.click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("window.__fetches")
    assert len(fetches) == 4
    assert fetches[3]["url"] == "/session/export"
    assert fetches[3]["options"]["headers"]["X-API-Token"] == "secret-test-token-42"
    assert fetches[3]["body"]["token"] == "secret-test-token-42"




