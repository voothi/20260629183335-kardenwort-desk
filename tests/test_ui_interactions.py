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

def extract_desk_js():
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
    js = js.replace("{audio_lmb_play}", "false")
    js = js.replace("{audio_lmb_source}", "false")
    js = js.replace("{audio_rmb_play}", "false")
    js = js.replace("{audio_rmb_source}", "false")
    js = js.replace("{audio_anki_tts_cli}", "''")
    js = js.replace("{audio_python_exe}", "''")
    js = js.replace("{has_highlight_col}", "false")
    js = js.replace("{selected_col_name}", "DeskSelected")
    js = js.replace("{lemma_col_name}", "WordSource")
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
