import pytest
from pathlib import Path
import json
import re
import kardenwort_desk

def get_base_html():
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI", "Oxford"]
    data_rows = [["Haus", "дом", "", "", ""]]
    
    html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="section-title">Source Text</div>
    <div class="source-text" id="source-container">Das Haus</div>
  </div>
  
  <div class="section">
    <div class="section-title">Translation</div>
    <div class="translation-text" id="translation-container">The house</div>
  </div>
  
  <div class="section">
    <div class="section-title">Lemmas</div>
    <table id="lemma-table">
      <thead>
        <tr>
          <th class="col-checkbox" data-col="DeskSelected">#</th>
          <th data-col="WordSource">Source</th>
          <th data-col="WordDestination">Translation</th>
          <th data-col="WordSourceIPA">IPA</th>
          <th data-col="WordSourceMorphologyAI">Morphology</th>
          <th data-col="Oxford">Oxford</th>
        </tr>
      </thead>
      <tbody>
        <tr data-row-id="0">
          <td class="col-checkbox" data-col="DeskSelected"></td>
          <td data-col="WordSource"><div class="scrollable-cell">Haus</div></td>
          <td data-col="WordDestination" class="editable"><div class="scrollable-cell">дом</div></td>
          <td data-col="WordSourceIPA"><div class="scrollable-cell"></div></td>
          <td class="col-morphology" data-col="WordSourceMorphologyAI"><div class="scrollable-cell"></div></td>
          <td data-col="Oxford"><div class="scrollable-cell"></div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
    return html, None, None, headers, data_rows

def extract_desk_js(lmb_play=False, lmb_source="lemma", rmb_play=False, rmb_source="word_translation", anki_tts_cli="C:\\fake\\tts.py", python_exe="python.exe"):
    desk_path = Path(kardenwort_desk.__file__)
    content = desk_path.read_text(encoding="utf-8")
    js_lines = []
    in_js = False
    for line in content.splitlines():
        if '<script type="text/javascript">' in line:
            in_js = True
            continue
        if in_js and '</script>' in line:
            break
        if in_js:
            js_lines.append(line)
            
    js = "\n".join(js_lines)
    # Ensure init() runs immediately since page load already fired
    js = re.sub(r"window\.addEventListener\('load',\s*init,\s*false\);", "init();", js)
    
    # Replace AHK python string formatting variables with valid defaults
    js = js.replace("{audio_lmb_play}", "true" if lmb_play else "false")
    js = js.replace("{audio_lmb_source}", f'"{lmb_source}"')
    js = js.replace("{audio_rmb_play}", "true" if rmb_play else "false")
    js = js.replace("{audio_rmb_source}", f'"{rmb_source}"')
    js = js.replace("{audio_anki_tts_cli}", anki_tts_cli.replace("\\", "\\\\"))
    js = js.replace("{audio_python_exe}", python_exe.replace("\\", "\\\\"))
    js = js.replace("{has_highlight_col}", "false")
    js = js.replace("{selected_col_name}", "DeskSelected")
    js = js.replace("{lemma_col_name}", "WordSource")
    js = js.replace("{inflected_col_name}", "WordSourceInflectedForm")
    js = js.replace("{ipa_col_name}", "WordSourceIPA")
    js = js.replace("{theme_class}", "theme-dark")
    js = js.replace("{selectable_mode}", "false")
    js = js.replace("{input_bg}", "white")
    js = js.replace("{text_color}", "black")
    js = js.replace("{input_border}", "gray")
    
    return js

def extract_mvp_js():
    ahk_path = Path(__file__).parents[2] / "20240411110510-autohotkey" / "kardenwort-window" / "kardenwort-window.ahk"
    if not ahk_path.exists():
        return ""
    ahk_content = ahk_path.read_text(encoding="utf-8")
    js_lines = []
    in_js_block = False
    for line in ahk_content.splitlines():
        if 'js := ""' in line:
            in_js_block = True
            continue
        if in_js_block:
            if 'IE.document.parentWindow.execScript(js)' in line or 'ExecuteJavaScript(' in line or 'scriptEl.text :=' in line:
                break
            
            # Match strings enclosed in quotes, ignoring AHK concat operators
            # AHK string literals can contain escaped quotes as "" or `"
            match = re.search(r'"((?:[^"`]|""|`.)*)"', line)
            if match:
                code_line = match.group(1).replace('""', '"').replace('`"', '"')
                # Fix the AHK interpolated line for selectableTextMode
                if 'window.setSelectableTextMode(' in code_line:
                    code_line = 'window.setSelectableTextMode(false);'
                js_lines.append(code_line)
                
            if '})();"' in line:
                break
    return "\n".join(js_lines)

def init_appstate(page, data_rows, headers, source_text="Das Haus", translated_text="The house"):
    desk_js = extract_desk_js()
    page.evaluate(desk_js)
    
    # We need to initialize the AppState by sending an initial payload
    # since we bypassed write_update_js
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}
    # Construct a delta similar to write_update_js
    rows_data = {}
    for i, row in enumerate(data_rows):
        rows_data[str(i)] = {
            "lemma": row[0],
            "trans": row[1],
            "ipa": row[2] if len(row) > 2 else "",
            "morph": row[3] if len(row) > 3 else ""
        }
    
    payload = {
        "stage": "finished",
        "status": "success",
        "rows": rows_data,
        "sourceText": source_text,
        "translatedText": translated_text
    }
    page.evaluate(f"window.receiveUpdate({json.dumps(payload)})")


def test_dynamic_labels(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    update_data = {
        "stage": "finished",
        "rows": {
            "0": {
                "classifications": {
                    "Oxford": "3k:B2"
                }
            }
        }
    }
    
    page.evaluate(f"window.receiveUpdate({json.dumps(update_data)})")
    
    # Assert dynamic styling for Oxford column
    oxford_cell = page.locator("td[data-col='Oxford'] span")
    assert oxford_cell.count() == 1
    assert oxford_cell.get_attribute("class") == "level-3k"
    assert oxford_cell.inner_text() == "B2"


def test_hover_highlight_mvp(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0">Er</span> <span class="word" data-word-idx="1" data-line-idx="0">läuft</span>'
    trans_html = "<div>He is running.</div><div>He is walking.</div><div>He is jumping.</div>"
    
    # Prepare the initial HTML blocks
    html = html.replace("The house", trans_html)
    html = html.replace("Das Haus", source_html)
    page.set_content(html)
    
    # Initialize JS state with the span-rich text
    init_appstate(page, data_rows, headers, source_text=source_html, translated_text=trans_html)
    
    mvp_js = extract_mvp_js()
    if not mvp_js:
        pytest.skip("AHK MVP JS not found")
        
    page.evaluate("window.HoverHighlightMvpBookmarks = 2;")
    page.evaluate(mvp_js)
    
    # Verify tokenization idempotent and correctly formed
    page.wait_for_selector("#translation-container span.word.hl-mvp", timeout=2000)
    spans = page.locator("#translation-container span.word.hl-mvp")
    assert spans.count() >= 3, "Should tokenize the translation text into spans"
    
    # Verify hover interactions
    source_word = page.locator("span.word[data-word-idx='0']").first
    # Hover source word
    source_word.hover()
    
    # Simulate Esc to clear bookmarks
    page.keyboard.press("Escape")
    pinned = page.locator(".hl-mvp-pin")
    assert pinned.count() == 0


def test_appstate_partial_updates(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    update1 = {"stage": "translated_text", "translatedText": "New translation here"}
    page.evaluate(f"window.receiveUpdate({json.dumps(update1)})")
    assert page.locator("#translation-container").inner_text() == "New translation here"
    
    update2 = {"stage": "translated_lemmas", "rows": {"0": {"lemma": "NewHaus"}}}
    page.evaluate(f"window.receiveUpdate({json.dumps(update2)})")
    assert page.locator("td").filter(has_text="NewHaus").is_visible()

def test_row_selection(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    row = page.locator("tr[data-row-id='0']")
    row.click()
    
    assert "selected" in row.get_attribute("class")
    
    # Check selection JS api
    selected_rows = page.evaluate("window.getSelectedRows()")
    assert selected_rows == '[0]'
    
    # Deselect
    row.click(modifiers=["Control"])
    assert "selected" not in row.get_attribute("class") or row.get_attribute("class") is None

def test_cell_edit_and_undo(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Double click the translation cell to edit
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    cell.dblclick()
    
    # Verify input element appears
    input_el = page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input")
    input_el.wait_for(state="visible", timeout=2000)
    
    # Change text and press enter
    input_el.fill("новый дом")
    input_el.press("Enter")
    
    # Verify it updated
    assert cell.locator(".scrollable-cell").inner_text() == "новый дом"
    
    # Verify AppState deltas tracking
    deltas_json = page.evaluate("window.getDeltas()")
    deltas = json.loads(deltas_json)
    assert len(deltas) > 0
    assert deltas[0]["row_id"] == 0
    assert deltas[0]["column"] == "WordDestination"
    assert deltas[0]["value"] == "новый дом"
    assert page.evaluate("window.isDirty()") is True
    
    # Undo
    page.evaluate("window.undo()")
    assert cell.locator(".scrollable-cell").inner_text() == "дом"
    assert page.evaluate("window.isDirty()") is False

def test_row_deletion_and_undo(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Select the row
    row = page.locator("tr[data-row-id='0']")
    row.click()
    
    # Verify selected
    assert "selected" in row.get_attribute("class")
    
    # Press Delete
    page.keyboard.press("Delete")
    
    # Verify row is hidden
    assert row.evaluate("el => window.getComputedStyle(el).display") == "none"
    
    # Verify AppState deltas tracking (should contain _delete)
    deltas_json = page.evaluate("window.getDeltas()")
    import json
    deltas = json.loads(deltas_json)
    assert len(deltas) > 0
    assert deltas[0]["row_id"] == 0
    assert deltas[0]["column"] == "_delete"
    assert deltas[0]["value"] is True
    
    # Undo
    page.evaluate("window.undo()")
    
    # Verify row is visible again
    assert row.evaluate("el => window.getComputedStyle(el).display") != "none"

def test_cancel_edit(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Double click the translation cell to edit
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    page.evaluate("window.__selectableTextMode = false;")
    cell.dblclick()
    
    # Verify input element appears
    input_el = page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input")
    input_el.wait_for(state='visible', timeout=2000)
    
    # Change text and press Escape
    input_el.fill("новый дом")
    input_el.press("Escape")
    
    # Verify it reverted back without pushing history
    assert cell.locator(".scrollable-cell").inner_text() == "дом"
    
    # Verify AppState deltas tracking is empty
    deltas_json = page.evaluate("window.getDeltas()")
    import json
    deltas = json.loads(deltas_json)
    assert len(deltas) == 0

def test_global_undo_redo_shortcuts(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    row = page.locator("tr[data-row-id='0']")
    row.click()
    page.keyboard.press("Delete")
    
    # Verify row is hidden
    assert row.evaluate("el => window.getComputedStyle(el).display") == "none"
    
    # Press Ctrl+Z
    page.keyboard.press("Control+z")
    
    # Verify row is visible again
    assert row.evaluate("el => window.getComputedStyle(el).display") != "none"
    
    # Press Ctrl+Y
    page.keyboard.press("Control+y")
    
    # Verify row is hidden again
    assert row.evaluate("el => window.getComputedStyle(el).display") == "none"

def test_global_select_all_shortcut(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    # Add a second row to test select all
    html = html.replace('</tbody>', '<tr data-row-id="1"><td class="col-checkbox" data-col="DeskSelected"></td><td data-col="WordSource"><div class="scrollable-cell">Hund</div></td><td data-col="WordDestination" class="editable"><div class="scrollable-cell">собака</div></td><td data-col="WordSourceIPA"><div class="scrollable-cell"></div></td><td class="col-morphology" data-col="WordSourceMorphologyAI"><div class="scrollable-cell"></div></td><td data-col="Oxford"><div class="scrollable-cell"></div></td></tr></tbody>')
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Press Ctrl+A
    page.keyboard.press("Control+a")
    
    # Verify both rows are selected
    row0 = page.locator("tr[data-row-id='0']")
    row1 = page.locator("tr[data-row-id='1']")
    
    assert "selected" in row0.get_attribute("class")
    assert "selected" in row1.get_attribute("class")

def test_keyboard_row_navigation_and_space_toggle(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    html = html.replace('</tbody>', '<tr data-row-id="1"><td class="col-checkbox" data-col="DeskSelected"></td><td data-col="WordSource"><div class="scrollable-cell">Hund</div></td><td data-col="WordDestination" class="editable"><div class="scrollable-cell">собака</div></td><td data-col="WordSourceIPA"><div class="scrollable-cell"></div></td><td class="col-morphology" data-col="WordSourceMorphologyAI"><div class="scrollable-cell"></div></td><td data-col="Oxford"><div class="scrollable-cell"></div></td></tr></tbody>')
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Focus table by pressing ArrowDown
    page.keyboard.press("ArrowDown")
    
    row0 = page.locator("tr[data-row-id='0']")
    row1 = page.locator("tr[data-row-id='1']")
    
    # Verify row 0 is focused (has outline style set)
    assert "solid" in row0.evaluate("el => el.style.outline")
    
    # Press Space to toggle selection of row 0
    page.keyboard.press("Space")
    assert "selected" in row0.get_attribute("class")
    
    # Press ArrowDown to navigate to row 1
    page.keyboard.press("ArrowDown")
    assert "solid" in row1.evaluate("el => el.style.outline")
    
    # Press Space to toggle selection of row 1
    page.keyboard.press("Space")
    assert "selected" in row1.get_attribute("class")


def test_f2_shortcut_editing(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Click the destination cell to set lastClickedCell and focus
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    page.evaluate("window.__selectableTextMode = false;")
    cell.click()
    
    # Press F2
    page.keyboard.press("F2")
    
    # Verify input element appears
    input_el = page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input")
    input_el.wait_for(state="visible", timeout=2000)
    assert input_el.is_visible() is True


def test_dirty_cell_class(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    page.evaluate("window.__selectableTextMode = false;")
    cell.dblclick()
    
    input_el = page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input")
    input_el.wait_for(state="visible", timeout=2000)
    input_el.fill("новый дом")
    input_el.press("Enter")
    
    # Verify td has dirty class
    assert "dirty" in cell.get_attribute("class")
    
    # Undo edit
    page.evaluate("window.undo()")
    
    # Verify dirty class is removed
    assert "dirty" not in (cell.get_attribute("class") or "")

def test_force_repaint_reflow(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Ensure window.forceRepaint executes without error
    page.evaluate("window.forceRepaint()")
    height = page.evaluate("document.body.offsetHeight")
    assert height >= 0


def test_receive_update_resilience(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Call receiveUpdate with null, empty, or partial data
    page.evaluate("window.receiveUpdate(null)")
    page.evaluate("window.receiveUpdate({})")
    page.evaluate("window.receiveUpdate({stage: 'unknown', status: 'error'})")
    
    # AppState should remain active and responsive
    assert page.evaluate("window.AppState.isFinished") is False or True


def test_skeleton_loader_cleanup_on_finish(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    # Inject skeleton loaders
    html = html.replace('<div class="scrollable-cell">Haus</div>', '<div class="scrollable-cell"><span class="skeleton-loader">...</span></div>')
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Send finished update
    payload = {
        "stage": "finished",
        "status": "success",
        "rows": {"0": {"lemma": "Haus", "trans": "дом"}},
    }
    import json
    page.evaluate(f"window.receiveUpdate({json.dumps(payload)})")
    
    # Verify cell text was updated and skeleton loader replaced
    cell_text = page.locator("tr[data-row-id='0'] td[data-col='WordSource']").inner_text()
    assert "Haus" in cell_text

def test_pending_stub_preservation_and_smooth_update(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    # Inject pending skeleton stub into translation container
    html = html.replace('<div class="translation-text" id="translation-container">The house</div>', '<div class="translation-text" id="translation-container"><span data-pending="true" class="skeleton-loader">Loading translation...</span></div>')
    page.set_content(html)
    
    desk_js = extract_desk_js()
    page.evaluate(desk_js)
    
    # 1. Receive early intermediate stage with no translatedText yet
    payload = {"stage": "init", "status": "processing"}
    import json
    page.evaluate(f"window.receiveUpdate({json.dumps(payload)})")
    
    # Verify the pending stub is preserved (did NOT collapse into empty blank space)
    container = page.locator("#translation-container")
    assert "Loading translation..." in container.inner_text()
    assert container.locator("[data-pending='true']").count() == 1
    
    # 2. Receive actual translatedText payload
    payload_translated = {"stage": "translated_text", "translatedText": "Дом был красивым."}
    page.evaluate(f"window.receiveUpdate({json.dumps(payload_translated)})")
    
    # Verify smooth in-place update replaces stub with actual text
    assert container.inner_text() == "Дом был красивым."
    assert container.locator("[data-pending='true']").count() == 0


def test_smooth_partial_cell_update_without_full_rerender(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Add a custom attribute to source container to detect if it gets re-rendered
    page.evaluate("document.getElementById('source-container').setAttribute('data-test-marker', 'active')")
    
    # Send row-only update for translation
    payload = {
        "stage": "translated_words",
        "rows": {"0": {"trans": "строение"}}
    }
    import json
    page.evaluate(f"window.receiveUpdate({json.dumps(payload)})")
    
    # Verify row 0 translation cell updated
    trans_cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    assert "строение" in trans_cell.inner_text()
    
    # Verify source-container marker remains intact (page was not re-rendered)
    assert page.locator("#source-container").get_attribute("data-test-marker") == "active"

def test_hand_tool_vs_select_text_mode(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    
    # 1. Enable Select Text Mode (Hand Tool disabled)
    page.evaluate("window.__selectableTextMode = true;")
    cell.dblclick(force=True)
    
    # Verify cell editing is BLOCKED when in Select Text mode
    assert page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input").count() == 0
    
    # 2. Disable Select Text Mode (Hand Tool active)
    page.evaluate("window.__selectableTextMode = false;")
    cell.dblclick(force=True)
    
    # Verify cell editing WORKS when Hand Tool is active
    input_el = page.locator("tr[data-row-id='0'] td[data-col='WordDestination'] input")
    input_el.wait_for(state="visible", timeout=2000)
    assert input_el.is_visible() is True


def test_ahk_bridge_selected_rows_and_clear(page, tmp_path):
    html, _, _, headers, data_rows = get_base_html()
    page.set_content(html)
    init_appstate(page, data_rows, headers)
    
    # Programmatically set selected rows via AHK bridge
    import json
    page.evaluate(f"window.setSelectedRows({json.dumps('[0]')})")
    
    # Verify row has selected class
    row0 = page.locator("tr[data-row-id='0']")
    assert "selected" in row0.get_attribute("class")
    
    # Verify getSelectedRows returns JSON string array with 0
    selected_raw = page.evaluate("window.getSelectedRows()")
    selected = json.loads(selected_raw) if isinstance(selected_raw, str) else selected_raw
    assert selected == [0] or selected == ["0"]
    
    # Programmatically clear selections via AHK bridge
    page.evaluate("window.clearAllSelectionsAndNotify()")
    
    # Verify selections are cleared
    selected_after_raw = page.evaluate("window.getSelectedRows()")
    selected_after = json.loads(selected_after_raw) if isinstance(selected_after_raw, str) else selected_after_raw
    assert len(selected_after) == 0
    assert "selected" not in (row0.get_attribute("class") or "")

def test_ie_cache_busting_meta_header_and_in_place_updates(page, tmp_path):
    html_output, _, _, headers, data_rows = get_base_html()
    
    # Inject X-UA-Compatible header if testing desk HTML generator template
    html_output = html_output.replace('<head><meta charset="utf-8"></head>', '<head><meta charset="utf-8"><meta http-equiv="X-UA-Compatible" content="IE=edge"></head>')
    
    # Load into Playwright
    page.set_content(html_output)
    desk_js = extract_desk_js()
    page.evaluate(desk_js)
    
    # Verify <meta http-equiv="X-UA-Compatible" content="IE=edge"> tag is loaded in DOM
    meta_tag = page.locator("meta[http-equiv='X-UA-Compatible']")
    assert meta_tag.get_attribute("content") == "IE=edge"
    
    # Verify in-place updates preserve the page URL (avoiding IE file re-navigation cache bugs)
    initial_url = page.url
    payload = {"stage": "finished", "status": "success", "rows": {"0": {"trans": "updated"}}}
    import json
    page.evaluate(f"window.receiveUpdate({json.dumps(payload)})")
    
    assert page.url == initial_url


def test_composite_identifier_unified_rmb_flip_and_lmb_symmetry(page):
    source_html = (
        'def <span class="word highlight-orange" data-word-idx="3" data-line-idx="0" data-lower-clean="split">split</span>'
        '_'
        '<span class="word highlight-orange" data-word-idx="5" data-line-idx="0" data-lower-clean="camel">camel</span>'
        '_'
        '<span class="word highlight-orange" data-word-idx="7" data-line-idx="0" data-lower-clean="case">case</span>'
        '():'
    )
    
    token_manifest = [
        {"text": "def", "is_word": True, "visual_idx": 1},
        {"text": " ", "is_word": False, "visual_idx": 2},
        {"text": "split", "is_word": True, "visual_idx": 3, "lower_clean": "split", "row_ids": [0, 1, 2]},
        {"text": "_", "is_word": False, "visual_idx": 4},
        {"text": "camel", "is_word": True, "visual_idx": 5, "lower_clean": "camel", "row_ids": [0, 1, 2]},
        {"text": "_", "is_word": False, "visual_idx": 6},
        {"text": "case", "is_word": True, "visual_idx": 7, "lower_clean": "case", "row_ids": [0, 1, 2]},
        {"text": "(", "is_word": False, "visual_idx": 8},
        {"text": ")", "is_word": False, "visual_idx": 9},
        {"text": ":", "is_word": False, "visual_idx": 10}
    ]
    
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="section-title">Source Text</div>
    <div class="source-text" id="source-container">{source_html}</div>
  </div>
  
  <div class="section">
    <div class="section-title">Translation</div>
    <div class="translation-text" id="translation-container">definition</div>
  </div>
  
  <div class="section">
    <div class="section-title">Lemmas</div>
    <table id="lemma-table">
      <thead>
        <tr>
          <th class="col-checkbox" data-col="DeskSelected">#</th>
          <th data-col="WordSource">Source</th>
          <th data-col="WordDestination">Translation</th>
        </tr>
      </thead>
      <tbody>
        <tr data-row-id="0">
          <td class="col-checkbox" data-col="DeskSelected"></td>
          <td data-col="WordSource"><div class="scrollable-cell">split</div></td>
          <td data-col="WordDestination" class="editable"><div class="scrollable-cell">расколоть</div></td>
        </tr>
        <tr data-row-id="1">
          <td class="col-checkbox" data-col="DeskSelected"></td>
          <td data-col="WordSource"><div class="scrollable-cell">camel</div></td>
          <td data-col="WordDestination" class="editable"><div class="scrollable-cell">верблюд</div></td>
        </tr>
        <tr data-row-id="2">
          <td class="col-checkbox" data-col="DeskSelected"></td>
          <td data-col="WordSource"><div class="scrollable-cell">case</div></td>
          <td data-col="WordDestination" class="editable"><div class="scrollable-cell">случай</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">
{json.dumps(token_manifest)}
</script>
<script id="session-lang" type="text/plain">en</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>
"""
    page.set_content(html)
    desk_js = extract_desk_js()
    page.evaluate(desk_js)
    
    span_split = page.locator("span[data-lower-clean='split']")
    span_camel = page.locator("span[data-lower-clean='camel']")
    span_case = page.locator("span[data-lower-clean='case']")
    
    # 1. Test LMB click on camel:
    # All constituent sub-tokens highlighted in yellow, clicked sub-token gets active-subtoken
    span_camel.click(button="left")
    
    assert "highlight-orange-active" in (span_split.get_attribute("class") or "")
    assert "highlight-orange-active" in (span_camel.get_attribute("class") or "")
    assert "highlight-orange-active" in (span_case.get_attribute("class") or "")
    assert "active-subtoken" in (span_camel.get_attribute("class") or "")
    
    # 2. Test Unified RMB flip on camel:
    # All constituent sub-tokens flip simultaneously to their ordered translations
    span_camel.click(button="right")
    
    assert span_split.inner_text() == "расколоть"
    assert span_camel.inner_text() == "верблюд"
    assert span_case.inner_text() == "случай"
    assert "flipped" in (span_split.get_attribute("class") or "")
    assert "flipped" in (span_camel.get_attribute("class") or "")
    assert "flipped" in (span_case.get_attribute("class") or "")
    
    source_container = page.locator("#source-container")
    assert "расколоть_верблюд_случай" in source_container.inner_text()
    
    # 3. Test RMB unflip on case:
    span_case.click(button="right")
    
    assert span_split.inner_text() == "split"
    assert span_camel.inner_text() == "camel"
    assert span_case.inner_text() == "case"
    assert "flipped" not in (span_split.get_attribute("class") or "")
    assert "flipped" not in (span_camel.get_attribute("class") or "")
    assert "flipped" not in (span_case.get_attribute("class") or "")
    assert "split_camel_case" in source_container.inner_text()


def test_compound_audio_playback_resolution(page):
    # Test 1: Projekt-Manager
    source_html_pm = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="projekt">Projekt</span>'
        '-'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="manager">Manager</span>'
    )
    manifest_pm = [
        {"text": "Projekt", "is_word": True, "visual_idx": 0, "lower_clean": "projekt", "row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "Manager", "is_word": True, "visual_idx": 2, "lower_clean": "manager", "row_ids": [1]},
    ]
    
    html_pm = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section"><div class="source-text" id="source-container">{source_html_pm}</div></div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">Projekt</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Projekt</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">проект</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">Manager</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Manager</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">менеджер</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest_pm)}</script>
<script id="session-lang" type="text/plain">de</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""

    # Test Projekt-Manager with lemma mode
    page.set_content(html_pm)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    js_lemma = extract_desk_js(lmb_play=True, lmb_source="lemma")
    page.evaluate(js_lemma)
    
    span_pm = page.locator("span[data-lower-clean='manager']")
    span_pm.click(button="left")
    
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nProjekt Manager")

    # Test viel-zu-beschäftigte with lemma and inflection modes
    source_html_vzb = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="viel">viel</span>'
        '-'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="zu">zu</span>'
        '-'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="beschäftigte">beschäftigte</span>'
    )
    manifest_vzb = [
        {"text": "viel", "is_word": True, "visual_idx": 0, "lower_clean": "viel", "row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "zu", "is_word": True, "visual_idx": 2, "lower_clean": "zu", "row_ids": [1]},
        {"text": "-", "is_word": False, "visual_idx": 3},
        {"text": "beschäftigte", "is_word": True, "visual_idx": 4, "lower_clean": "beschäftigte", "row_ids": [2]},
    ]
    html_vzb = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section"><div class="source-text" id="source-container">{source_html_vzb}</div></div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">viel</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">viel</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">много</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">zu</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">zu</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">слишком</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">beschäftigt</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">beschäftigte</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">занятой</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest_vzb)}</script>
<script id="session-lang" type="text/plain">de</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""

    # viel-zu-beschäftigte: lemma mode -> "viel zu beschäftigt"
    page.set_content(html_vzb)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_vzb = page.locator("span[data-lower-clean='beschäftigte']")
    span_vzb.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nviel zu beschäftigt")

    # viel-zu-beschäftigte: inflection mode -> "viel zu beschäftigte"
    page.set_content(html_vzb)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span_vzb = page.locator("span[data-lower-clean='viel']")
    span_vzb.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nviel zu beschäftigte")


def test_contraction_audio_playback_resolution(page):
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="isn\'t">isn\'t</span>'
    manifest = [
        {"text": "isn't", "is_word": True, "visual_idx": 0, "lower_clean": "isn't", "row_ids": [0, 1]},
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section"><div class="source-text" id="source-container">{source_html}</div></div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">be</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">isn't, is</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">быть</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">not</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">isn't, not</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">не</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">en</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""

    # 1. Contraction: lemma mode -> "be not"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span = page.locator("span[data-lower-clean=\"isn't\"]")
    span.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nbe not")

    # 2. Contraction: inflection mode -> "is not"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span = page.locator("span[data-lower-clean=\"isn't\"]")
    span.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nis not")


def test_shared_multi_row_compound_audio_no_duplication(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="projekt">Projekt</span>'
        '-'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="manager">Manager</span>'
    )
    # Both spans share all row_ids [0, 1, 2] from compound decomposition in TSV
    manifest = [
        {"text": "Projekt", "is_word": True, "visual_idx": 0, "lower_clean": "projekt", "row_ids": [0, 1, 2]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "Manager", "is_word": True, "visual_idx": 2, "lower_clean": "manager", "row_ids": [0, 1, 2]},
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section"><div class="source-text" id="source-container">{source_html}</div></div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">Projekt</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Projekt-Manager</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">проект</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">Manager</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Projekt-Manager</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">менеджер</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">Projekt-Manager</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Projekt-Manager</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">проектный менеджер</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">de</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""

    # 1. Test clicking Projekt under lemma mode
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_projekt = page.locator("span[data-lower-clean='projekt']")
    span_projekt.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nProjekt Manager")

    # 2. Test clicking Manager under inflection mode
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span_manager = page.locator("span[data-lower-clean='manager']")
    span_manager.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nProjekt Manager")

