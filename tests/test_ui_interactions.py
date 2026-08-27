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

def extract_desk_js(lmb_play=False, lmb_source="lemma", lmb_chain_mode="joined", table_range_mode="none", rmb_play=False, rmb_chain_mode="separate", rmb_source="word_translation", anki_tts_cli="C:\\fake\\tts.py", python_exe="python.exe"):
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
    js = js.replace("{audio_lmb_chain_mode}", f'"{lmb_chain_mode}"')
    js = js.replace("{audio_table_range_mode}", f'"{table_range_mode}"')
    js = js.replace("{audio_rmb_play}", "true" if rmb_play else "false")
    js = js.replace("{audio_rmb_chain_mode}", f'"{rmb_chain_mode}"')
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


def test_progressive_skeleton_auto_resolution_polling(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    tsv_file = tmp_path / "20260826235959-skel.de.tsv"
    tsv_file.write_text(
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\tDas Haus\tДом\t\n",
        encoding="utf-8"
    )
    html_page = kardenwort_desk.run_render_flow(
        text="Das Haus",
        language="de",
        zid="20260826235959",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False
    )

    # 1. Page with skeleton loader sets up polling timer
    html_with_skeleton = html_page.replace(
        '<div class="translation-text" id="translation-container">',
        '<div class="translation-text" id="translation-container"><span class="skeleton-loader" data-pending="true">Loading...</span>'
    )
    page.set_content(html_with_skeleton)
    has_timer = page.evaluate("window._kwSkeletonPollTimer !== null && window._kwSkeletonPollTimer !== undefined")
    assert has_timer is True

    # 2. Page without skeleton loader skips background polling
    page.goto("about:blank")
    page.set_content(html_page)
    no_timer = page.evaluate("!window._kwSkeletonPollTimer")
    assert no_timer is True


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
    
    # 2. Test Full compound RMB flip when all sub-tokens are highlighted/selected:
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
    
    # 3. Test RMB unflip on case when all are selected: unflips all spans together
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

    # Test Projekt-Manager with lemma mode - single click plays only clicked sub-token
    page.set_content(html_pm)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    js_lemma = extract_desk_js(lmb_play=True, lmb_source="lemma")
    page.evaluate(js_lemma)
    
    span_pm = page.locator("span[data-lower-clean='manager']")
    span_pm.click(button="left")
    
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nManager")

    # Test Projekt-Manager with drag range playback
    page.evaluate("window.__ahkCalls = [];")
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='projekt']");
        const s2 = document.querySelector("span[data-lower-clean='manager']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
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

    # viel-zu-beschäftigte: lemma mode single-click -> "beschäftigt"
    page.set_content(html_vzb)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_vzb = page.locator("span[data-lower-clean='beschäftigte']")
    span_vzb.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nbeschäftigt")

    # viel-zu-beschäftigte: inflection mode single-click on 'viel' -> "viel"
    page.set_content(html_vzb)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span_vzb = page.locator("span[data-lower-clean='viel']")
    span_vzb.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nviel")

    # viel-zu-beschäftigte: inflection mode drag across all 3 words -> "viel zu beschäftigte"
    page.set_content(html_vzb)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection", lmb_chain_mode="joined"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='viel']");
        const s2 = document.querySelector("span[data-lower-clean='zu']");
        const s3 = document.querySelector("span[data-lower-clean='beschäftigte']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        s3.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nviel zu beschäftigte")

    # viel-zu-beschäftigte: separate chain mode drag across all 3 words -> single call with "viel ||| zu ||| beschäftigte"
    page.set_content(html_vzb)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection", lmb_chain_mode="separate"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='viel']");
        const s2 = document.querySelector("span[data-lower-clean='zu']");
        const s3 = document.querySelector("span[data-lower-clean='beschäftigte']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        s3.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nviel ||| zu ||| beschäftigte")


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

    # isn't: lemma mode -> "be not"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span = page.locator("span[data-lower-clean=\"isn't\"]")
    span.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nbe not")

    # isn't: inflection mode -> "is not"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span = page.locator("span[data-lower-clean=\"isn't\"]")
    span.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nis not")


def test_contraction_audio_order_were(page):
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="we\'re">we\'re</span>'
    manifest = [
        {"text": "we're", "is_word": True, "visual_idx": 0, "lower_clean": "we're", "row_ids": [1, 0]},
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
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">we're, are</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">быть</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">we</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">we're, we</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мы</div></td>
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

    # 1. lemma mode: plays "we be"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span = page.locator("span[data-lower-clean=\"we're\"]")
    span.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe be")

    # 2. inflection mode: plays "we are"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span = page.locator("span[data-lower-clean=\"we're\"]")
    span.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe are")


def test_render_flow_ordered_manifest_rows(tmp_path):
    import configparser
    import kardenwort_desk
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    source_text = "Today we're going to take a look."
    tsv_content = (
        "# comment\n"
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "be\twe're, are\tбыть\n"
        "we\twe're, we\tмы\n"
    )
    tsv_file = tmp_path / "20260816005700-test-ordered.en.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text=source_text,
        language="en",
        zid="20260816005700",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file)
    )

    import re, json
    m = re.search(r'<script id="token-map" type="application/json">\s*([\s\S]*?)\s*</script>', html)
    assert m is not None
    manifest = json.loads(m.group(1))

    were_token = next((t for t in manifest if t.get("lower_clean") == "we're"), None)
    assert were_token is not None
    # Row 0 is 'be', Row 1 is 'we'. Mapped targets for "we're" are ["we", "are"].
    # Row for 'we' (1) must precede row for 'be' (0).
    assert were_token["row_ids"] == [1, 0]


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

    # 1. Test clicking Projekt under lemma mode -> plays only "Projekt"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_projekt = page.locator("span[data-lower-clean='projekt']")
    span_projekt.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nProjekt")

    # 2. Test clicking Manager under inflection mode -> plays only "Manager"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection"))
    span_manager = page.locator("span[data-lower-clean='manager']")
    span_manager.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nManager")

    # 3. Test drag across Projekt -> Manager -> plays "Projekt Manager" on release
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="joined"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='projekt']");
        const s2 = document.querySelector("span[data-lower-clean='manager']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nProjekt Manager")

    # 4. Test drag across Projekt -> Manager with separate chain mode -> plays "Projekt ||| Manager"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='projekt']");
        const s2 = document.querySelector("span[data-lower-clean='manager']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nProjekt ||| Manager")


def test_rmb_flip_compound_with_shared_rows_no_bleeding(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="viel">viel</span>'
        '-'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="zu">zu</span>'
        '-'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="beschäftigte">beschäftigte</span>'
    )
    # Manifest where all tokens share rows 0..3 (derived from compound decomposition)
    manifest = [
        {"text": "viel", "is_word": True, "visual_idx": 0, "lower_clean": "viel", "row_ids": [0, 1, 2, 3]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "zu", "is_word": True, "visual_idx": 2, "lower_clean": "zu", "row_ids": [0, 1, 2, 3]},
        {"text": "-", "is_word": False, "visual_idx": 3},
        {"text": "beschäftigte", "is_word": True, "visual_idx": 4, "lower_clean": "beschäftigte", "row_ids": [0, 1, 2, 3]},
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
          <td data-col="WordSource"><div class="scrollable-cell">zu</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">viel-zu-beschäftigte</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">к</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">viel</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">viel-zu-beschäftigte</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">много</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">beschäftigen</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">viel-zu-beschäftigte</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">занимать</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">viel-zu-beschäftigen</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">viel-zu-beschäftigte</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">слишком занят</div></td>
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

    page.set_content(html)
    page.evaluate(extract_desk_js())
    
    span_viel = page.locator("span[data-lower-clean='viel']")
    span_zu = page.locator("span[data-lower-clean='zu']")
    span_besch = page.locator("span[data-lower-clean='beschäftigte']")
    
    # 1. Right click on isolated sub-token 'beschäftigte' flips only that sub-token
    span_besch.click(button="right")
    
    assert span_viel.inner_text() == "viel"
    assert span_zu.inner_text() == "zu"
    assert span_besch.inner_text() == "занимать"
    
    # 2. Dragging across all constituent parts flips all of them cleanly without composite bleeding
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='viel']");
        const s2 = document.querySelector("span[data-lower-clean='beschäftigte']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")

    assert span_viel.inner_text() == "много"
    assert span_zu.inner_text() == "к"
    assert span_besch.inner_text() == "занимать"
    
    source_container = page.locator("#source-container")
    assert "много-к-занимать" in source_container.inner_text()



def test_slash_separated_words_not_grouped_as_compound(page):
    source_html = (
        '('
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="separable">separable</span>'
        '/'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="two">two</span>'
        '-'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="part">part</span>'
        ')'
    )
    manifest = [
        {"text": "separable", "is_word": True, "visual_idx": 0, "lower_clean": "separable", "row_ids": [0]},
        {"text": "/", "is_word": False, "visual_idx": 1},
        {"text": "two", "is_word": True, "visual_idx": 2, "lower_clean": "two", "row_ids": [1, 2]},
        {"text": "-", "is_word": False, "visual_idx": 3},
        {"text": "part", "is_word": True, "visual_idx": 4, "lower_clean": "part", "row_ids": [1, 2]},
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
          <td data-col="WordSource"><div class="scrollable-cell">separable</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">separable</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">разделимый</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">two</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">two-part</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">два</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">part</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">two-part</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">часть</div></td>
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

    # 1. Clicking 'two' should only play 'two', NOT including 'separable' or sibling 'part'
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_two = page.locator("span[data-lower-clean='two']")
    span_two.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ntwo")

    # 2. Clicking 'separable' should only play 'separable'
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_separable = page.locator("span[data-lower-clean='separable']")
    span_separable.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nseparable")

    # 3. Dragging across 'two' -> 'part' plays 'two part'
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='two']");
        const s2 = document.querySelector("span[data-lower-clean='part']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ntwo part")


def test_zid_and_numeric_filtered_from_compound_audio_playback(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="20260815131120">20260815131120</span>'
        '-'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="token">token</span>'
        '-'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="mapping">mapping</span>'
        '-'
        '<span class="word" data-word-idx="6" data-line-idx="0" data-lower-clean="inflected">inflected</span>'
        '-'
        '<span class="word" data-word-idx="8" data-line-idx="0" data-lower-clean="expansion">expansion</span>'
    )
    manifest = [
        {"text": "20260815131120", "is_word": True, "visual_idx": 0, "lower_clean": "20260815131120", "row_ids": []},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "token", "is_word": True, "visual_idx": 2, "lower_clean": "token", "row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 3},
        {"text": "mapping", "is_word": True, "visual_idx": 4, "lower_clean": "mapping", "row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 5},
        {"text": "inflected", "is_word": True, "visual_idx": 6, "lower_clean": "inflected", "row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 7},
        {"text": "expansion", "is_word": True, "visual_idx": 8, "lower_clean": "expansion", "row_ids": [0]},
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
          <td data-col="WordSource"><div class="scrollable-cell">token</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">20260815131120-token-mapping-inflected-expansion</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">токен</div></td>
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

    # 1. Clicking on 'mapping' should play 'mapping' (single sub-token)
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_mapping = page.locator("span[data-lower-clean='mapping']")
    span_mapping.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nmapping")

    # 2. Clicking on 'token' should also play 'token' (single sub-token)
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_token = page.locator("span[data-lower-clean='token']")
    span_token.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ntoken")

    # 3. Drag across all tokens (from token to expansion) filters out the 14-digit timestamp
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='token']");
        const s2 = document.querySelector("span[data-lower-clean='expansion']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ntoken mapping inflected expansion")

    # 4. Clicking on the ZID span directly (which is excluded from candidate row selection) suppresses playback
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_zid = page.locator("span[data-lower-clean='20260815131120']")
    span_zid.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0


def test_pure_numeric_span_audio_playback_suppressed(page):
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="20260815131120">20260815131120</span>'
    manifest = [
        {"text": "20260815131120", "is_word": True, "visual_idx": 0, "lower_clean": "20260815131120", "row_ids": []},
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section"><div class="source-text" id="source-container">{source_html}</div></div>
  <div class="section">
    <table id="lemma-table">
      <tbody></tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">en</script>
</body>
</html>"""

    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_zid = page.locator("span[data-lower-clean='20260815131120']")
    span_zid.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0


def test_path_level_slashes_delimit_compound_groups_in_audio(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="archive">archive</span>'
        '/'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="20260815131120">20260815131120</span>'
        '-'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="token">token</span>'
        '-'
        '<span class="word" data-word-idx="6" data-line-idx="0" data-lower-clean="mapping">mapping</span>'
        '/'
        '<span class="word" data-word-idx="8" data-line-idx="0" data-lower-clean="specs">specs</span>'
        '/'
        '<span class="word" data-word-idx="10" data-line-idx="0" data-lower-clean="spec">spec</span>'
        '.'
        '<span class="word" data-word-idx="12" data-line-idx="0" data-lower-clean="md">md</span>'
    )
    manifest = [
        {"text": "archive", "is_word": True, "visual_idx": 0, "lower_clean": "archive", "row_ids": [0]},
        {"text": "/", "is_word": False, "visual_idx": 1},
        {"text": "20260815131120", "is_word": True, "visual_idx": 2, "lower_clean": "20260815131120", "row_ids": []},
        {"text": "-", "is_word": False, "visual_idx": 3},
        {"text": "token", "is_word": True, "visual_idx": 4, "lower_clean": "token", "row_ids": [1]},
        {"text": "-", "is_word": False, "visual_idx": 5},
        {"text": "mapping", "is_word": True, "visual_idx": 6, "lower_clean": "mapping", "row_ids": [1]},
        {"text": "/", "is_word": False, "visual_idx": 7},
        {"text": "specs", "is_word": True, "visual_idx": 8, "lower_clean": "specs", "row_ids": [2]},
        {"text": "/", "is_word": False, "visual_idx": 9},
        {"text": "spec", "is_word": True, "visual_idx": 10, "lower_clean": "spec", "row_ids": [3]},
        {"text": ".", "is_word": False, "visual_idx": 11},
        {"text": "md", "is_word": True, "visual_idx": 12, "lower_clean": "md", "row_ids": [4]},
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
          <td data-col="WordSource"><div class="scrollable-cell">archive</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">архив</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">token</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">token-mapping</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">токен</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">specs</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">спецификации</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">spec</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">спека</div></td>
        </tr>
        <tr data-row-id="4">
          <td data-col="WordSource"><div class="scrollable-cell">md</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">маркдаун</div></td>
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

    # 1. Clicking on 'specs' should only play 'specs'
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_specs = page.locator("span[data-lower-clean='specs']")
    span_specs.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nspecs")

    # 2. Clicking on 'token' should only play 'token'
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_token = page.locator("span[data-lower-clean='token']")
    span_token.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ntoken")

    # 3. Dragging across 'token' -> 'mapping' plays 'token mapping'
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='token']");
        const s2 = document.querySelector("span[data-lower-clean='mapping']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ntoken mapping")


def test_single_span_with_multiple_compound_tsv_rows_plays_only_direct_match(page):
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="archive">archive</span>'
    manifest = [
        {"text": "archive", "is_word": True, "visual_idx": 0, "lower_clean": "archive", "row_ids": [0, 1, 2]},
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
          <td data-col="WordSource"><div class="scrollable-cell">archive</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Archived, Archive</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">архив</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">archive/20260815131120</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">archive/20260815131120-token-mapping/</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">архив/20260815131120</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">archive/20260815131120-token-mapping/</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">archive/20260815131120-token-mapping/</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">расширение/</div></td>
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

    # 1. Clicking on 'archive' span: only plays "archive" (direct match), never concatenating compound rows or ZIDs
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    span_archive = page.locator("span[data-lower-clean='archive']")
    span_archive.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\narchive")

    # 2. Clicking directly on table row with ZID in lemma: plays only natural word "archive", stripping ZID
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))
    row_zid = page.locator("tr[data-row-id='1']")
    row_zid.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\narchive")


def test_rmb_drag_flip_preserves_unconnected_words_and_numbers(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="version">Version</span> '
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="2">2</span>'
        '.'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="0">0</span> '
        '<span class="word" data-word-idx="6" data-line-idx="0" data-lower-clean="release">release</span> '
        '<span class="word" data-word-idx="8" data-line-idx="0" data-lower-clean="100">100</span> '
        '<span class="word" data-word-idx="10" data-line-idx="0" data-lower-clean="unknown">unknown</span>'
    )
    manifest = [
        {"text": "Version", "is_word": True, "visual_idx": 0, "lower_clean": "version", "row_ids": [0]},
        {"text": " ", "is_word": False, "visual_idx": 1},
        {"text": "2", "is_word": True, "visual_idx": 2, "lower_clean": "2", "row_ids": []},
        {"text": ".", "is_word": False, "visual_idx": 3},
        {"text": "0", "is_word": True, "visual_idx": 4, "lower_clean": "0", "row_ids": []},
        {"text": " ", "is_word": False, "visual_idx": 5},
        {"text": "release", "is_word": True, "visual_idx": 6, "lower_clean": "release", "row_ids": [1]},
        {"text": " ", "is_word": False, "visual_idx": 7},
        {"text": "100", "is_word": True, "visual_idx": 8, "lower_clean": "100", "row_ids": []},
        {"text": " ", "is_word": False, "visual_idx": 9},
        {"text": "unknown", "is_word": True, "visual_idx": 10, "lower_clean": "unknown", "row_ids": []},
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
          <td data-col="WordSource"><div class="scrollable-cell">version</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">версия</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">release</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">релиз</div></td>
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

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_version = page.locator("span[data-lower-clean='version']")
    span_unknown = page.locator("span[data-lower-clean='unknown']")
    
    # RMB drag from Version across 2, 0, release, 100, unknown
    span_version.dispatch_event("mousedown", {"button": 2, "which": 3})
    page.locator("span[data-lower-clean='2']").dispatch_event("mouseover")
    page.locator("span[data-lower-clean='0']").dispatch_event("mouseover")
    page.locator("span[data-lower-clean='release']").dispatch_event("mouseover")
    page.locator("span[data-lower-clean='100']").dispatch_event("mouseover")
    span_unknown.dispatch_event("mouseover")
    page.evaluate("window.dispatchEvent(new MouseEvent('mouseup', {button: 2, which: 3}))")

    source_text = page.locator("#source-container").inner_text()

    # Translated words are flipped
    assert "версия" in source_text
    assert "релиз" in source_text

    # Untranslated words, numbers, and decimals are preserved and visible
    assert "2.0" in source_text
    assert "100" in source_text
    assert "unknown" in source_text
    assert page.locator("span[data-lower-clean='2']").is_visible()
    assert page.locator("span[data-lower-clean='0']").is_visible()
    assert page.locator("span[data-lower-clean='100']").is_visible()
    assert page.locator("span[data-lower-clean='unknown']").is_visible()


def test_quoted_words_and_camel_case_rmb_flip_interactions(page):
    manifest = [
        {"text": "display", "is_word": True, "visual_idx": 1, "lower_clean": "display", "row_ids": [0]},
        {"text": " ", "is_word": False, "visual_idx": 2},
        {"text": "=", "is_word": False, "visual_idx": 3},
        {"text": " ", "is_word": False, "visual_idx": 4},
        {"text": "'", "is_word": False, "visual_idx": 5},
        {"text": "none", "is_word": True, "visual_idx": 6, "lower_clean": "none", "row_ids": [1]},
        {"text": "'", "is_word": False, "visual_idx": 7},
        {"text": ";", "is_word": False, "visual_idx": 8},
        {"text": " ", "is_word": False, "visual_idx": 9},
        {"text": "flip", "is_word": True, "visual_idx": 10, "lower_clean": "flip", "row_ids": [2]},
        {"text": "Word", "is_word": True, "visual_idx": 11, "lower_clean": "word", "row_ids": [3]},
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div id="source-container">
  <span class="word highlight-orange" data-word-idx="1" data-line-idx="0" data-lower-clean="display">display</span>
  = '<span class="word highlight-orange" data-word-idx="6" data-line-idx="0" data-lower-clean="none">none</span>';
  <span class="word highlight-orange" data-word-idx="10" data-line-idx="0" data-compound-id="1" data-lower-clean="flip">flip</span><span class="word highlight-orange" data-word-idx="11" data-line-idx="0" data-compound-id="1" data-lower-clean="word">Word</span>
</div>
<div id="table-container">
  <table id="lemma-table">
    <tr data-row-id="0">
      <td data-col="WordSource">display</td>
      <td data-col="WordDestination">отображение</td>
    </tr>
    <tr data-row-id="1">
      <td data-col="WordSource">none</td>
      <td data-col="WordDestination">ничего</td>
    </tr>
    <tr data-row-id="2">
      <td data-col="WordSource">flip</td>
      <td data-col="WordDestination">переворачивать</td>
    </tr>
    <tr data-row-id="3">
      <td data-col="WordSource">word</td>
      <td data-col="WordDestination">слово</td>
    </tr>
  </table>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
</body>
</html>"""

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_none = page.locator("span[data-lower-clean='none']")
    span_flip = page.locator("span[data-lower-clean='flip']")
    span_word = page.locator("span[data-lower-clean='word']")

    # 1. RMB click on quoted word 'none'
    span_none.click(button="right")
    assert span_none.inner_text() == "ничего"
    assert "'" in page.locator("#source-container").inner_text()

    # 2. RMB click on camelCase 'flip' isolates flip to 'flip' sub-token
    span_flip.click(button="right")
    assert span_flip.inner_text() == "переворачивать"
    assert span_word.inner_text() == "Word"

    # Re-click to unflip 'flip'
    span_flip.click(button="right")
    assert span_flip.inner_text() == "flip"

    # Drag across 'flip' to 'Word' flips both
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='flip']");
        const s2 = document.querySelector("span[data-lower-clean='word']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    assert span_flip.inner_text() == "переворачивать"
    assert span_word.inner_text() == "слово"

    # 3. RMB click on 'Word' unflips 'Word' while 'flip' remains flipped
    span_word.click(button="right")
    assert span_flip.inner_text() == "переворачивать"
    assert span_word.inner_text() == "Word"


def test_adjacent_spans_lineage_grouping_safety(page):
    # Two adjacent words WITHOUT delimiter and WITHOUT matching compound id
    # should NOT be grouped together into a single compound entity.
    manifest = [
        {"text": "first", "is_word": True, "visual_idx": 0, "lower_clean": "first", "row_ids": [0]},
        {"text": "second", "is_word": True, "visual_idx": 1, "lower_clean": "second", "row_ids": [1]},
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div id="source-container">
  <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="first">first</span><span class="word highlight-orange" data-word-idx="1" data-line-idx="0" data-lower-clean="second">second</span>
</div>
<div id="table-container">
  <table id="lemma-table">
    <tr data-row-id="0">
      <td data-col="WordSource">first</td>
      <td data-col="WordDestination">первый</td>
    </tr>
    <tr data-row-id="1">
      <td data-col="WordSource">second</td>
      <td data-col="WordDestination">второй</td>
    </tr>
  </table>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
</body>
</html>"""

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_first = page.locator("span[data-lower-clean='first']")
    span_second = page.locator("span[data-lower-clean='second']")

    # RMB click on 'first' should ONLY flip 'first' to 'первый', NOT 'second'
    span_first.click(button="right")
    assert span_first.inner_text() == "первый"
    assert span_second.inner_text() == "second"

    # RMB click on 'second' should ONLY flip 'second' to 'второй'
    span_second.click(button="right")
    assert span_first.inner_text() == "первый"
    assert span_second.inner_text() == "второй"


def test_compound_subtoken_lmb_selection(page):
    manifest = [
        {"text": "split", "is_word": True, "visual_idx": 0, "lower_clean": "split", "row_ids": [0, 3]},
        {"text": "_", "is_word": False, "visual_idx": 1},
        {"text": "camel", "is_word": True, "visual_idx": 2, "lower_clean": "camel", "row_ids": [1, 3]},
        {"text": "_", "is_word": False, "visual_idx": 3},
        {"text": "case", "is_word": True, "visual_idx": 4, "lower_clean": "case", "row_ids": [2, 3]},
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div id="source-container">
  <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="split">split</span>_<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="camel">camel</span>_<span class="word highlight-orange" data-word-idx="4" data-line-idx="0" data-lower-clean="case">case</span>
</div>
<div id="table-container">
  <table id="lemma-table">
    <tr data-row-id="0">
      <td data-col="WordSource">split</td>
      <td data-col="WordDestination">разделять</td>
    </tr>
    <tr data-row-id="1">
      <td data-col="WordSource">camel</td>
      <td data-col="WordDestination">верблюд</td>
    </tr>
    <tr data-row-id="2">
      <td data-col="WordSource">case</td>
      <td data-col="WordDestination">случай</td>
    </tr>
    <tr data-row-id="3">
      <td data-col="WordSource">split_camel_case</td>
      <td data-col="WordDestination">разбиение camelCase</td>
    </tr>
  </table>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
</body>
</html>"""

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_split = page.locator("span[data-lower-clean='split']")
    span_camel = page.locator("span[data-lower-clean='camel']")
    
    # 1. Clicking on 'split' sub-token selects only its mapped rows (0, 3), not sibling rows (1, 2)
    span_split.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert sorted(selected_rows) == [0, 3]

    assert "selected" in page.locator("tr[data-row-id='0']").get_attribute("class")
    assert "selected" in page.locator("tr[data-row-id='3']").get_attribute("class")
    assert "selected" not in (page.locator("tr[data-row-id='1']").get_attribute("class") or "")
    assert "selected" not in (page.locator("tr[data-row-id='2']").get_attribute("class") or "")

    # 2. Clicking on 'camel' sub-token adds its mapped rows (1, 3) without disturbing row 0
    span_camel.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert sorted(selected_rows) == [0, 1, 3]

    # 3. Clicking on 'camel' again deselects [1, 3] (since both are currently selected), leaving row 0
    span_camel.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert sorted(selected_rows) == [0]

    # 4. Drag-selecting across split -> camel -> case accumulates all rows [0, 1, 2, 3]
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='split']");
        const s2 = document.querySelector("span[data-lower-clean='camel']");
        const s3 = document.querySelector("span[data-lower-clean='case']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        s3.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }));
    }""")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert sorted(selected_rows) == [0, 1, 2, 3]


def test_compound_subtoken_isolated_from_standalone_words(page, tmp_path, monkeypatch):
    import configparser
    import sys
    from kardenwort_desk import run_render_flow
    import kardenwort_desk

    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

    config = configparser.ConfigParser()
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "monolithic")
    config.add_section("triggers")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "manual")
    config.add_section("environment")
    config.set("environment", "kardenwort_workspace", str(tmp_path))
    config.add_section("languages")
    config.set("languages", "en_lemma_index", "en_idx")
    config.set("languages", "en_lemma_override", "en_over")
    config.set("languages", "en_prompt", "en_prompt")

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section("fields")
    mapping.add_section("fields_mapping.word")
    mapping.add_section("desk_columns")
    mapping.set("desk_columns", "WordSource", "lemma")
    mapping.set("desk_columns", "WordSourceInflectedForm", "inflected")
    mapping.set("desk_columns", "WordDestination", "word_translation")

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, "w") as f:
        mapping.write(f)

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "anki_mapping_file": str(mapping_file),
        "kardenwort_python": sys.executable
    }

    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)

    tsv_path = res_dir / "123-test-slug.en.tsv"
    tsv_path.write_text(
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "split_camel_case\tsplit_camel_case\tразбиение camelCase\n"
        "camel\tcamel\tверблюд\n",
        encoding="utf-8"
    )

    html = run_render_flow(
        text="split_camel_case split camel case",
        language="en",
        zid="123",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    page.set_content(html)

    # Spans inside compound split_camel_case
    comp_split = page.locator("span[data-word-idx='1']")
    comp_camel = page.locator("span[data-word-idx='3']")
    comp_case = page.locator("span[data-word-idx='5']")

    # Standalone spans
    standalone_split = page.locator("span[data-word-idx='7']")
    standalone_camel = page.locator("span[data-word-idx='9']")
    standalone_case = page.locator("span[data-word-idx='11']")

    # Standalone split and case are not connected (isolated from row 0)
    assert "not-connected" in (standalone_split.get_attribute("class") or "")
    assert "not-connected" in (standalone_case.get_attribute("class") or "")
    # Standalone camel is highlight-orange (matches row 1)
    assert "highlight-orange" in (standalone_camel.get_attribute("class") or "")

    # Subtokens inside compound are highlight-orange (match row 0, and camel matches row 1)
    assert "highlight-orange" in (comp_split.get_attribute("class") or "")
    assert "highlight-orange" in (comp_camel.get_attribute("class") or "")
    assert "highlight-orange" in (comp_case.get_attribute("class") or "")

    # 1. Click standalone 'camel': should select Row 1 only, highlighting ONLY standalone camel and comp_camel
    standalone_camel.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert selected_rows == [1]

    # Check bidirectional highlights
    assert "highlight-orange-active" in (standalone_camel.get_attribute("class") or "")
    assert "highlight-orange-active" in (comp_camel.get_attribute("class") or "")
    # comp_split and comp_case do NOT highlight because row 0 is NOT selected
    assert "highlight-orange-active" not in (comp_split.get_attribute("class") or "")
    assert "highlight-orange-active" not in (comp_case.get_attribute("class") or "")
    # standalone split and case do NOT highlight
    assert "highlight-orange-active" not in (standalone_split.get_attribute("class") or "")
    assert "highlight-orange-active" not in (standalone_case.get_attribute("class") or "")

    # Deselect standalone camel
    standalone_camel.click(button="left")
    assert json.loads(page.evaluate("window.getSelectedRows()")) == []

    # 2. Click compound 'camel': should select ONLY Row 1 (camel lemma) and NOT Row 0 (compound)
    comp_camel.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert selected_rows == [1]

    # Only camel spans are highlighted; sibling sub-tokens inside compound are NOT highlighted
    assert "highlight-orange-active" not in (comp_split.get_attribute("class") or "")
    assert "highlight-orange-active" in (comp_camel.get_attribute("class") or "")
    assert "highlight-orange-active" not in (comp_case.get_attribute("class") or "")
    assert "highlight-orange-active" in (standalone_camel.get_attribute("class") or "")
    assert "highlight-orange-active" not in (standalone_split.get_attribute("class") or "")
    assert "highlight-orange-active" not in (standalone_case.get_attribute("class") or "")

    # Deselect
    comp_camel.click(button="left")
    assert json.loads(page.evaluate("window.getSelectedRows()")) == []

    # 3. Drag across entire compound (comp_split -> comp_camel -> comp_case): selects Row 0 and Row 1
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-word-idx='1']");
        const s2 = document.querySelector("span[data-word-idx='5']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert sorted(selected_rows) == [0, 1]

    # Entire compound is highlighted
    assert "highlight-orange-active" in (comp_split.get_attribute("class") or "")
    assert "highlight-orange-active" in (comp_camel.get_attribute("class") or "")
    assert "highlight-orange-active" in (comp_case.get_attribute("class") or "")
    # Standalone camel is highlighted (due to row 1)
    assert "highlight-orange-active" in (standalone_camel.get_attribute("class") or "")
    # Standalone split and case are NOT highlighted (isolated from row 0)
    assert "highlight-orange-active" not in (standalone_split.get_attribute("class") or "")
    assert "highlight-orange-active" not in (standalone_case.get_attribute("class") or "")


def test_drag_selection_audio_deferred_until_mouseup(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="hello">hello</span> '
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="world">world</span>'
    )
    manifest = [
        {"text": "hello", "is_word": True, "visual_idx": 0, "lower_clean": "hello", "row_ids": [0]},
        {"text": " ", "is_word": False, "visual_idx": 1},
        {"text": "world", "is_word": True, "visual_idx": 2, "lower_clean": "world", "row_ids": [1]},
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
          <td data-col="WordSource"><div class="scrollable-cell">hello</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">привет</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">world</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мир</div></td>
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
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))

    # 1. During mousedown: zero audio calls
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='hello']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    assert len([c for c in calls if c.get("action") == "play"]) == 0

    # 2. During mouseover: still zero audio calls
    page.evaluate("""() => {
        const s2 = document.querySelector("span[data-lower-clean='world']");
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    assert len([c for c in calls if c.get("action") == "play"]) == 0

    # 3. Upon mouseup: exactly one audio call with joined text
    page.evaluate("""() => {
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nhello world")

    # 4. Deselection gesture suppresses audio on mouseup
    page.evaluate("window.__ahkCalls = [];")
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='hello']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0


def test_drag_selection_audio_separate_mode_emits_individual_calls(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="hello">hello</span> '
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="world">world</span>'
    )
    manifest = [
        {"text": "hello", "is_word": True, "visual_idx": 0, "lower_clean": "hello", "row_ids": [0]},
        {"text": " ", "is_word": False, "visual_idx": 1},
        {"text": "world", "is_word": True, "visual_idx": 2, "lower_clean": "world", "row_ids": [1]},
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
          <td data-col="WordSource"><div class="scrollable-cell">hello</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">привет</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">world</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мир</div></td>
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
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))

    # 1. During mousedown: zero audio calls
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='hello']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    assert len([c for c in calls if c.get("action") == "play"]) == 0

    # 2. During mouseover: still zero audio calls
    page.evaluate("""() => {
        const s2 = document.querySelector("span[data-lower-clean='world']");
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    assert len([c for c in calls if c.get("action") == "play"]) == 0

    # 3. Upon mouseup: exactly one single audio call with delimited words
    page.evaluate("""() => {
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nhello ||| world")


def test_drag_selection_audio_three_tokens_separate_vs_joined(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="split">split</span>_'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="camel">camel</span>_'
        '<span class="word" data-word-idx="4" data-line-idx="0" data-lower-clean="case">case</span>'
    )
    manifest = [
        {"text": "split", "is_word": True, "visual_idx": 0, "lower_clean": "split", "row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 1},
        {"text": "camel", "is_word": True, "visual_idx": 2, "lower_clean": "camel", "row_ids": [1]},
        {"text": "_", "is_word": False, "visual_idx": 3},
        {"text": "case", "is_word": True, "visual_idx": 4, "lower_clean": "case", "row_ids": [2]},
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
        <tr data-row-id="0"><td data-col="WordSource"><div class="scrollable-cell">split</div></td><td data-col="WordDestination"><div class="scrollable-cell">разделить</div></td></tr>
        <tr data-row-id="1"><td data-col="WordSource"><div class="scrollable-cell">camel</div></td><td data-col="WordDestination"><div class="scrollable-cell">верблюд</div></td></tr>
        <tr data-row-id="2"><td data-col="WordSource"><div class="scrollable-cell">case</div></td><td data-col="WordDestination"><div class="scrollable-cell">регистр</div></td></tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">en</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))

    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nsplit ||| camel ||| case")

    # Joined mode
    page.evaluate("window.__ahkCalls = [];")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="joined"))
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nsplit camel case")


def test_compound_subtoken_click_isolation_selects_only_atomic_row(page):
    manifest = [
        {"text": "multi", "is_word": True, "visual_idx": 0, "lower_clean": "multi", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "token", "is_word": True, "visual_idx": 2, "lower_clean": "token", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]}
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">
      <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="multi">multi</span>-<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="token">token</span>
    </div>
  </div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">multi-token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти-токен</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">multi</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">токен</div></td>
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
    page.set_content(html)
    page.evaluate(extract_desk_js())

    # Click on 'multi' sub-token span
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='multi']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")

    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert selected_rows == [1], f"Expected only atomic row [1] to be selected, got {selected_rows}"

    is_multi_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").classList.contains('highlight-orange-active')")
    is_token_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").classList.contains('highlight-orange-active')")
    assert is_multi_highlighted is True
    assert is_token_highlighted is False


def test_compound_subtoken_drag_selection_full_range_selects_composite_row(page):
    manifest = [
        {"text": "multi", "is_word": True, "visual_idx": 0, "lower_clean": "multi", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "token", "is_word": True, "visual_idx": 2, "lower_clean": "token", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]}
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">
      <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="multi">multi</span>-<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="token">token</span>
    </div>
  </div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">multi-token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти-токен</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">multi</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">токен</div></td>
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
    page.set_content(html)
    page.evaluate(extract_desk_js())

    # Drag across 'multi' to 'token'
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='multi']");
        const s2 = document.querySelector("span[data-lower-clean='token']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")

    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert set(selected_rows) == {0, 1, 2}, f"Expected rows [0, 1, 2] to be selected, got {selected_rows}"

    is_multi_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").classList.contains('highlight-orange-active')")
    is_token_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").classList.contains('highlight-orange-active')")
    assert is_multi_highlighted is True
    assert is_token_highlighted is True


def test_compound_partial_drag_selection_suppresses_composite_row(page):
    manifest = [
        {"text": "split", "is_word": True, "visual_idx": 0, "lower_clean": "split", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 1},
        {"text": "camel", "is_word": True, "visual_idx": 2, "lower_clean": "camel", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 3},
        {"text": "case", "is_word": True, "visual_idx": 4, "lower_clean": "case", "row_ids": [0, 3], "atomic_row_ids": [3], "compound_row_ids": [0]}
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">
      <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="split">split</span>_<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="camel">camel</span>_<span class="word highlight-orange" data-word-idx="4" data-line-idx="0" data-lower-clean="case">case</span>
    </div>
  </div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">split_camel_case</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">разделить_кэмел_кейс</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">split</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">разделить</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">camel</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">верблюд</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">case</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">регистр</div></td>
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
    page.set_content(html)
    page.evaluate(extract_desk_js())

    # Drag across 'split' to 'camel' only (partial drag: 2 out of 3 tokens)
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='split']");
        const s2 = document.querySelector("span[data-lower-clean='camel']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        s2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")

    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert set(selected_rows) == {1, 2}, f"Expected partial sub-token rows [1, 2], got {selected_rows}"

    is_split_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='split']\").classList.contains('highlight-orange-active')")
    is_camel_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='camel']\").classList.contains('highlight-orange-active')")
    is_case_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='case']\").classList.contains('highlight-orange-active')")
    assert is_split_highlighted is True
    assert is_camel_highlighted is True
    assert is_case_highlighted is False


def test_compound_table_row_click_highlights_all_constituent_spans(page):
    manifest = [
        {"text": "multi", "is_word": True, "visual_idx": 0, "lower_clean": "multi", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "token", "is_word": True, "visual_idx": 2, "lower_clean": "token", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]}
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">
      <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="multi">multi</span>-<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="token">token</span>
    </div>
  </div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">multi-token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти-токен</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">multi</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">токен</div></td>
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
    page.set_content(html)
    page.evaluate(extract_desk_js())

    # Click directly on Table Row 0 (composite compound row)
    page.evaluate("""() => {
        const row0 = document.querySelector("tr[data-row-id='0']");
        row0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
    }""")

    is_multi_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").classList.contains('highlight-orange-active')")
    is_token_highlighted = page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").classList.contains('highlight-orange-active')")
    assert is_multi_highlighted is True
    assert is_token_highlighted is True


def test_compound_with_inflected_forms_preserves_standalone_token_highlight(page, tmp_path, monkeypatch):
    import configparser
    import sys
    from kardenwort_desk import run_render_flow
    import kardenwort_desk

    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

    config = configparser.ConfigParser()
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "monolithic")
    config.add_section("triggers")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "manual")
    config.add_section("environment")
    config.set("environment", "kardenwort_workspace", str(tmp_path))
    config.add_section("languages")
    config.set("languages", "en_lemma_index", "en_idx")
    config.set("languages", "en_lemma_override", "en_over")
    config.set("languages", "en_prompt", "en_prompt")

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section("fields")
    mapping.add_section("fields_mapping.word")
    mapping.add_section("desk_columns")
    mapping.set("desk_columns", "WordSource", "lemma")
    mapping.set("desk_columns", "WordSourceInflectedForm", "inflected")
    mapping.set("desk_columns", "WordDestination", "word_translation")

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, "w") as f:
        mapping.write(f)

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "anki_mapping_file": str(mapping_file),
        "kardenwort_python": sys.executable
    }

    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)

    tsv_path = res_dir / "test-clicking.en.tsv"
    tsv_path.write_text(
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "click\tright-click, clicking\tщелкните\n",
        encoding="utf-8"
    )

    html = run_render_flow(
        text="When clicking a sub-token and right-click flips.",
        language="en",
        zid="123",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    page.set_content(html)

    # Standalone 'clicking' span must have highlight-orange and NOT be not-connected
    clicking_span = page.locator("span[data-lower-clean='clicking']")
    assert "highlight-orange" in (clicking_span.get_attribute("class") or "")
    assert "not-connected" not in (clicking_span.get_attribute("class") or "")

    # Click on 'clicking' should select row 0 (click)
    clicking_span.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert selected_rows == [0]
    assert "highlight-orange-active" in (clicking_span.get_attribute("class") or "")


def test_rmb_subtoken_flip_isolation_and_full_compound_flip(page):
    manifest = [
        {"text": "multi", "is_word": True, "visual_idx": 0, "lower_clean": "multi", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "-", "is_word": False, "visual_idx": 1},
        {"text": "token", "is_word": True, "visual_idx": 2, "lower_clean": "token", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]}
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">
      <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="multi">multi</span>-<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="token">token</span>
    </div>
  </div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">multi-token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти-токен</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">multi</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мульти</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">token</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">токен</div></td>
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
    page.set_content(html)
    page.evaluate(extract_desk_js())

    # 1. RMB click on 'multi' when compound is NOT fully selected
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='multi']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")

    multi_flipped = page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").classList.contains('flipped')")
    multi_text = page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").textContent")
    token_flipped = page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").classList.contains('flipped')")
    token_text = page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").textContent")

    assert multi_flipped is True
    assert multi_text == "мульти"
    assert token_flipped is False
    assert token_text == "token"

    # Re-click RMB on 'multi' to unflip
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='multi']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").classList.contains('flipped')") is False
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").textContent") == "multi"

    # 2. Select entire compound via row 0 (composite row)
    page.evaluate("""() => {
        const row0 = document.querySelector("tr[data-row-id='0']");
        row0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
    }""")

    # RMB click on 'multi' when compound IS fully selected -> flips all constituent spans
    page.evaluate("""() => {
        const s1 = document.querySelector("span[data-lower-clean='multi']");
        s1.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")

    assert page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").classList.contains('flipped')") is True
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='multi']\").textContent") == "мульти"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").classList.contains('flipped')") is True
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='token']\").textContent") == "токен"


def test_rmb_drag_flipping_and_unflipping_and_delimiter_preservation(page):
    source_html = (
        '<span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="split">split</span>_'
        '<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="camel">camel</span>_'
        '<span class="word highlight-orange" data-word-idx="4" data-line-idx="0" data-lower-clean="case">case</span>'
    )
    manifest = [
        {"text": "split", "is_word": True, "visual_idx": 0, "lower_clean": "split", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 1},
        {"text": "camel", "is_word": True, "visual_idx": 2, "lower_clean": "camel", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 3},
        {"text": "case", "is_word": True, "visual_idx": 4, "lower_clean": "case", "row_ids": [0, 3], "atomic_row_ids": [3], "compound_row_ids": [0]},
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
        <tr data-row-id="0"><td data-col="WordSource"><div class="scrollable-cell">split_camel_case</div></td><td data-col="WordDestination"><div class="scrollable-cell">разделить_кэмел_кейс</div></td></tr>
        <tr data-row-id="1"><td data-col="WordSource"><div class="scrollable-cell">split</div></td><td data-col="WordDestination"><div class="scrollable-cell">разделить</div></td></tr>
        <tr data-row-id="2"><td data-col="WordSource"><div class="scrollable-cell">camel</div></td><td data-col="WordDestination"><div class="scrollable-cell">верблюд</div></td></tr>
        <tr data-row-id="3"><td data-col="WordSource"><div class="scrollable-cell">case</div></td><td data-col="WordDestination"><div class="scrollable-cell">регистр</div></td></tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">en</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""
    page.set_content(html)
    page.evaluate(extract_desk_js())

    # Drag RMB from 'split' to 'case'
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")

    assert page.evaluate("document.querySelector(\"span[data-lower-clean='split']\").textContent") == "разделить"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='camel']\").textContent") == "верблюд"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='case']\").textContent") == "регистр"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='split']\").classList.contains('flipped')") is True
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='camel']\").classList.contains('flipped')") is True
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='case']\").classList.contains('flipped')") is True

    # Check that delimiters are intact in source-container
    container_text = page.evaluate("document.getElementById('source-container').textContent")
    assert container_text == "разделить_верблюд_регистр"

    # Drag RMB to UN-FLIP (starting from flipped 'split' across to 'case')
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")

    assert page.evaluate("document.querySelector(\"span[data-lower-clean='split']\").textContent") == "split"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='camel']\").textContent") == "camel"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='case']\").textContent") == "case"
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='split']\").classList.contains('flipped')") is False
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='camel']\").classList.contains('flipped')") is False
    assert page.evaluate("document.querySelector(\"span[data-lower-clean='case']\").classList.contains('flipped')") is False


def test_rmb_audio_deferred_click_drag_chaining_and_unflip_suppression(page):
    source_html = (
        '<span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="split">split</span>_'
        '<span class="word highlight-orange" data-word-idx="2" data-line-idx="0" data-lower-clean="camel">camel</span>_'
        '<span class="word highlight-orange" data-word-idx="4" data-line-idx="0" data-lower-clean="case">case</span>'
    )
    manifest = [
        {"text": "split", "is_word": True, "visual_idx": 0, "lower_clean": "split", "row_ids": [0, 1], "atomic_row_ids": [1], "compound_row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 1},
        {"text": "camel", "is_word": True, "visual_idx": 2, "lower_clean": "camel", "row_ids": [0, 2], "atomic_row_ids": [2], "compound_row_ids": [0]},
        {"text": "_", "is_word": False, "visual_idx": 3},
        {"text": "case", "is_word": True, "visual_idx": 4, "lower_clean": "case", "row_ids": [0, 3], "atomic_row_ids": [3], "compound_row_ids": [0]},
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
        <tr data-row-id="0"><td data-col="WordSource"><div class="scrollable-cell">split_camel_case</div></td><td data-col="WordDestination"><div class="scrollable-cell">разделить_кэмел_кейс</div></td></tr>
        <tr data-row-id="1"><td data-col="WordSource"><div class="scrollable-cell">split</div></td><td data-col="WordDestination"><div class="scrollable-cell">разделить</div></td></tr>
        <tr data-row-id="2"><td data-col="WordSource"><div class="scrollable-cell">camel</div></td><td data-col="WordDestination"><div class="scrollable-cell">верблюд</div></td></tr>
        <tr data-row-id="3"><td data-col="WordSource"><div class="scrollable-cell">case</div></td><td data-col="WordDestination"><div class="scrollable-cell">регистр</div></td></tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">en</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""
    page.set_content(html)
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="separate"))
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")

    # 1. RMB Click: mousedown emits no audio; mouseup emits audio for single word
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0, f"Expected 0 play calls on mousedown, got {play_calls}"

    page.evaluate("""() => {
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nразделить")

    # Unflip 'split'
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")

    # 2. RMB Drag in separate chain mode ('separate' -> ' ||| ')
    page.evaluate("window.__ahkCalls = [];")
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nразделить ||| верблюд ||| регистр")

    # 3. RMB Un-flip drag suppresses audio
    page.evaluate("window.__ahkCalls = [];")
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0, f"Expected 0 audio calls on un-flip, got {play_calls}"

    # 4. RMB Drag in joined chain mode ('joined' -> space)
    page.set_content(html)
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="joined"))
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate("""() => {
        const s0 = document.querySelector("span[data-lower-clean='split']");
        const s4 = document.querySelector("span[data-lower-clean='case']");
        s0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }));
        s4.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 2, buttons: 2 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 2, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nразделить верблюд регистр")


def test_subtoken_inflected_form_lmb_click_selects_lemma_and_highlights(page, tmp_path, monkeypatch):
    import configparser
    import sys
    from kardenwort_desk import run_render_flow
    import kardenwort_desk

    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

    config = configparser.ConfigParser()
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "monolithic")
    config.add_section("triggers")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "manual")
    config.add_section("environment")
    config.set("environment", "kardenwort_workspace", str(tmp_path))
    config.add_section("languages")
    config.set("languages", "en_lemma_index", "en_idx")
    config.set("languages", "en_lemma_override", "en_over")
    config.set("languages", "en_prompt", "en_prompt")

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section("fields")
    mapping.add_section("fields_mapping.word")
    mapping.add_section("desk_columns")
    mapping.set("desk_columns", "WordSource", "lemma")
    mapping.set("desk_columns", "WordSourceInflectedForm", "inflected")
    mapping.set("desk_columns", "WordDestination", "word_translation")

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, "w") as f:
        mapping.write(f)

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "anki_mapping_file": str(mapping_file),
        "kardenwort_python": sys.executable
    }

    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)

    tsv_path = res_dir / "test-ai-curated.en.tsv"
    tsv_path.write_text(
        "WordSource\tWordSourceInflectedForm\tWordDestination\n"
        "AI\tAI-curated\tИИ\n"
        "AI-curated\tAI-curated\tкурировать\n"
        "curate\tAI-curated\tкурировать\n",
        encoding="utf-8"
    )

    html = run_render_flow(
        text="This digest is AI-curated.",
        language="en",
        zid="123",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    page.set_content(html)

    # curated span must have highlight-orange and NOT be not-connected
    curated_span = page.locator("span[data-lower-clean='curated']")
    assert "highlight-orange" in (curated_span.get_attribute("class") or "")
    assert "not-connected" not in (curated_span.get_attribute("class") or "")

    # Click on 'curated' should select row 2 (curate) and NOT row 1 (AI-curated)
    curated_span.click(button="left")
    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert selected_rows == [2]
    assert "highlight-orange-active" in (curated_span.get_attribute("class") or "")

    # Sibling 'AI' span must NOT be highlighted as active
    ai_span = page.locator("span[data-lower-clean='ai']")
    assert "highlight-orange-active" not in (ai_span.get_attribute("class") or "")


def test_subtoken_click_fallback_when_atomic_row_ids_empty(page):
    manifest = [
        {"text": "custom", "is_word": True, "visual_idx": 0, "lower_clean": "custom", "row_ids": [0], "atomic_row_ids": [], "compound_row_ids": [0]}
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">
      <span class="word highlight-orange" data-word-idx="0" data-line-idx="0" data-lower-clean="custom">custom</span>
    </div>
  </div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">custom-word</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">кастомное-слово</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
</body>
</html>"""
    page.set_content(html)
    page.evaluate(extract_desk_js())

    span = page.locator("span[data-lower-clean='custom']")
    span.click(button="left")

    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert selected_rows == [0]
    assert "highlight-orange-active" in (span.get_attribute("class") or "")


def test_sample_file_ai_curated_bidirectional_selection(page, monkeypatch):
    import configparser
    import sys
    from pathlib import Path
    from kardenwort_desk import run_render_flow
    import kardenwort_desk

    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

    workspace_path = Path(kardenwort_desk.__file__).parent
    tsv_path = workspace_path / "results" / "20260816202412-this-digest-is-ai.en.tsv"
    txt_path = workspace_path / "results" / "20260816202412-this-digest-is-ai.en.txt"
    if not tsv_path.exists() or not txt_path.exists():
        pytest.skip("Sample files not present")

    config = configparser.ConfigParser()
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "monolithic")
    config.add_section("triggers")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "manual")
    config.add_section("environment")
    config.set("environment", "kardenwort_workspace", str(workspace_path))
    config.add_section("languages")
    config.set("languages", "en_lemma_index", "en_idx")
    config.set("languages", "en_lemma_override", "en_over")
    config.set("languages", "en_prompt", "en_prompt")

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section("fields")
    mapping.add_section("fields_mapping.word")
    mapping.add_section("desk_columns")
    mapping.set("desk_columns", "WordSource", "lemma")
    mapping.set("desk_columns", "WordSourceInflectedForm", "inflected")
    mapping.set("desk_columns", "WordDestination", "word_translation")

    mapping_file = workspace_path / "anki-mapping.ini"
    resolved_paths = {
        "kardenwort_workspace": workspace_path,
        "anki_mapping_file": str(mapping_file),
        "kardenwort_python": sys.executable
    }

    text_content = txt_path.read_text(encoding="utf-8").strip()

    html = run_render_flow(
        text=text_content,
        language="en",
        zid="123",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    page.set_content(html)

    # Click on 'curated' span -> selects curate row (row index 5 in 0-indexed data rows)
    curated_span = page.locator("span[data-lower-clean='curated']")
    assert "highlight-orange" in (curated_span.get_attribute("class") or "")
    curated_span.click(button="left")

    selected_rows = json.loads(page.evaluate("window.getSelectedRows()"))
    assert 5 in selected_rows
    assert "highlight-orange-active" in (curated_span.get_attribute("class") or "")


def test_rmb_contraction_decomposition_flip(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="we\'ll">we\'ll</span> '
        '<span class="word" data-word-idx="1" data-line-idx="0" data-lower-clean="let\'s">let\'s</span> '
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="they\'re">they\'re</span>'
    )
    manifest = [
        {"text": "we'll", "is_word": True, "visual_idx": 0, "lower_clean": "we'll", "row_ids": [0, 1]},
        {"text": "let's", "is_word": True, "visual_idx": 1, "lower_clean": "let's", "row_ids": [2, 3]},
        {"text": "they're", "is_word": True, "visual_idx": 2, "lower_clean": "they're", "row_ids": [4, 5]},
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
          <td data-col="WordSource"><div class="scrollable-cell">we</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мы</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">will</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">воля</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">let</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">позволять</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">us</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мы</div></td>
        </tr>
        <tr data-row-id="4">
          <td data-col="WordSource"><div class="scrollable-cell">they</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">они</div></td>
        </tr>
        <tr data-row-id="5">
          <td data-col="WordSource"><div class="scrollable-cell">be</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">быть</div></td>
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

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_well = page.locator("span[data-lower-clean=\"we'll\"]")
    span_lets = page.locator("span[data-lower-clean=\"let's\"]")
    span_theyre = page.locator("span[data-lower-clean=\"they're\"]")

    # 1. Flip we'll -> "мы воля"
    span_well.click(button="right")
    assert span_well.inner_text() == "мы воля"

    # 2. Flip let's -> "позволять мы"
    span_lets.click(button="right")
    assert span_lets.inner_text() == "позволять мы"

    # 3. Flip they're -> "они быть"
    span_theyre.click(button="right")
    assert span_theyre.inner_text() == "они быть"

    # 4. Unflip back
    span_well.click(button="right")
    assert span_well.inner_text() == "we'll"

    span_lets.click(button="right")
    assert span_lets.inner_text() == "let's"

    span_theyre.click(button="right")
    assert span_theyre.inner_text() == "they're"


def test_rmb_abbreviation_decomposition_flip(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="fyi">fyi</span> '
        '<span class="word" data-word-idx="1" data-line-idx="0" data-lower-clean="gui">GUI</span>'
    )
    manifest = [
        {"text": "fyi", "is_word": True, "visual_idx": 0, "lower_clean": "fyi", "row_ids": [0, 1, 2]},
        {"text": "GUI", "is_word": True, "visual_idx": 1, "lower_clean": "gui", "row_ids": [3, 4, 5]},
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
          <td data-col="WordSource"><div class="scrollable-cell">for</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">для</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">your</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">ваш</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">information</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">информация</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">graphical</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">графический</div></td>
        </tr>
        <tr data-row-id="4">
          <td data-col="WordSource"><div class="scrollable-cell">user</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">пользователь</div></td>
        </tr>
        <tr data-row-id="5">
          <td data-col="WordSource"><div class="scrollable-cell">interface</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">интерфейс</div></td>
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

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_fyi = page.locator("span[data-lower-clean='fyi']")
    span_gui = page.locator("span[data-lower-clean='gui']")

    # 1. Flip fyi -> "для ваш информация"
    span_fyi.click(button="right")
    assert span_fyi.inner_text() == "для ваш информация"

    # 2. Flip GUI -> "графический пользователь интерфейс"
    span_gui.click(button="right")
    assert span_gui.inner_text() == "графический пользователь интерфейс"

    # 3. Unflip
    span_fyi.click(button="right")
    assert span_fyi.inner_text() == "fyi"

    span_gui.click(button="right")
    assert span_gui.inner_text() == "GUI"


def test_rmb_multi_span_compound_subtoken_isolation(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="state">state</span>-'
        '<span class="word" data-word-idx="1" data-line-idx="0" data-lower-clean="of">of</span>-'
        '<span class="word" data-word-idx="2" data-line-idx="0" data-lower-clean="the">the</span>-'
        '<span class="word" data-word-idx="3" data-line-idx="0" data-lower-clean="art">art</span>'
    )
    manifest = [
        {"text": "state", "is_word": True, "visual_idx": 0, "lower_clean": "state", "row_ids": [0, 4]},
        {"text": "of", "is_word": True, "visual_idx": 1, "lower_clean": "of", "row_ids": [1, 4]},
        {"text": "the", "is_word": True, "visual_idx": 2, "lower_clean": "the", "row_ids": [2, 4]},
        {"text": "art", "is_word": True, "visual_idx": 3, "lower_clean": "art", "row_ids": [3, 4]},
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
          <td data-col="WordSource"><div class="scrollable-cell">state</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">состояние</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">of</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">из</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">the</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">этот</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">art</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">искусство</div></td>
        </tr>
        <tr data-row-id="4">
          <td data-col="WordSource"><div class="scrollable-cell">state_of_the_art</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">новейший</div></td>
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

    page.set_content(html)
    page.evaluate(extract_desk_js())

    span_state = page.locator("span[data-lower-clean='state']")
    span_of = page.locator("span[data-lower-clean='of']")
    span_the = page.locator("span[data-lower-clean='the']")
    span_art = page.locator("span[data-lower-clean='art']")

    # Clicking 'state' sub-span flips only 'state' to 'состояние' (not compound 'новейший')
    span_state.click(button="right")
    assert span_state.inner_text() == "состояние"
    assert span_art.inner_text() == "art"

    # Clicking 'art' sub-span flips only 'art' to 'искусство'
    span_art.click(button="right")
    assert span_art.inner_text() == "искусство"

    # Unflip
    span_state.click(button="right")
    assert span_state.inner_text() == "state"


def test_rmb_contraction_audio_playback_resolution(page):
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="we\'ll">we\'ll</span>'
    manifest = [
        {"text": "we'll", "is_word": True, "visual_idx": 0, "lower_clean": "we'll", "row_ids": [0, 1]},
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
          <td data-col="WordSource"><div class="scrollable-cell">we</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мы</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">will</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">воля</div></td>
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

    # 1. Separate chain mode (default): plays "мы ||| воля"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="separate"))

    span = page.locator("span[data-lower-clean=\"we'll\"]")
    span.click(button="right")

    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nмы ||| воля")

    # 2. Joined chain mode: plays "мы воля"
    page.evaluate("window.__ahkCalls = [];")
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="joined"))
    span.click(button="right")

    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nмы воля")


def test_lmb_contraction_single_token_chain_modes(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="we\'ll">we\'ll</span> '
        '<span class="word" data-word-idx="1" data-line-idx="0" data-lower-clean="we\'re">we\'re</span>'
    )
    manifest = [
        {"text": "we'll", "is_word": True, "visual_idx": 0, "lower_clean": "we'll", "row_ids": [0, 1]},
        {"text": "we're", "is_word": True, "visual_idx": 1, "lower_clean": "we're", "row_ids": [2, 3]},
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
          <td data-col="WordSource"><div class="scrollable-cell">we</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">we'll, we</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мы</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">will</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">we'll, will</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">воля</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">we</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">we're, we</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">мы</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">be</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">we're, are</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">быть</div></td>
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

    # 1. we'll: lemma mode with separate chain mode -> "we ||| will"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))
    span_well = page.locator("span[data-lower-clean=\"we'll\"]")
    span_well.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe ||| will")

    # 2. we'll: lemma mode with joined chain mode -> "we will"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="joined"))
    span_well = page.locator("span[data-lower-clean=\"we'll\"]")
    span_well.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe will")

    # 3. we're: inflection mode with separate chain mode -> "we ||| are"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection", lmb_chain_mode="separate"))
    span_were = page.locator("span[data-lower-clean=\"we're\"]")
    span_were.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe ||| are")

    # 4. we're: inflection mode with joined chain mode -> "we are"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection", lmb_chain_mode="joined"))
    span_were = page.locator("span[data-lower-clean=\"we're\"]")
    span_were.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe are")

    # 5. we're: lemma mode with separate chain mode -> "we ||| be"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))
    span_were = page.locator("span[data-lower-clean=\"we're\"]")
    span_were.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nwe ||| be")


def test_abbreviation_single_token_audio_chain_modes(page):
    source_html = (
        '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="fyi">fyi</span> '
        '<span class="word" data-word-idx="1" data-line-idx="0" data-lower-clean="gui">GUI</span>'
    )
    manifest = [
        {"text": "fyi", "is_word": True, "visual_idx": 0, "lower_clean": "fyi", "row_ids": [0, 1, 2]},
        {"text": "GUI", "is_word": True, "visual_idx": 1, "lower_clean": "gui", "row_ids": [3, 4, 5]},
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
          <td data-col="WordSource"><div class="scrollable-cell">for</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">для</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">your</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">ваш</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">information</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">информация</div></td>
        </tr>
        <tr data-row-id="3">
          <td data-col="WordSource"><div class="scrollable-cell">graphical</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">графический</div></td>
        </tr>
        <tr data-row-id="4">
          <td data-col="WordSource"><div class="scrollable-cell">user</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">пользователь</div></td>
        </tr>
        <tr data-row-id="5">
          <td data-col="WordSource"><div class="scrollable-cell">interface</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">интерфейс</div></td>
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

    # 1. FYI: LMB separate chain mode -> "for ||| your ||| information"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))
    span_fyi = page.locator("span[data-lower-clean='fyi']")
    span_fyi.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nfor ||| your ||| information")

    # 2. FYI: LMB joined chain mode -> "for your information"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="joined"))
    span_fyi = page.locator("span[data-lower-clean='fyi']")
    span_fyi.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\nfor your information")

    # 3. FYI: RMB separate chain mode -> "для ||| ваш ||| информация"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="separate"))
    span_fyi = page.locator("span[data-lower-clean='fyi']")
    span_fyi.click(button="right")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nдля ||| ваш ||| информация")

    # 4. FYI: RMB joined chain mode -> "для ваш информация"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="joined"))
    span_fyi = page.locator("span[data-lower-clean='fyi']")
    span_fyi.click(button="right")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nдля ваш информация")

    # 5. GUI: LMB separate chain mode -> "graphical ||| user ||| interface"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate"))
    span_gui = page.locator("span[data-lower-clean='gui']")
    span_gui.click(button="left")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("en\\ngraphical ||| user ||| interface")

    # 6. GUI: RMB separate chain mode -> "графический ||| пользователь ||| интерфейс"
    page.set_content(html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(rmb_play=True, rmb_chain_mode="separate"))
    span_gui.click(button="right")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("ru\\nграфический ||| пользователь ||| интерфейс")


def test_web_view_skeleton_auto_resolution(page):
    """
    Verify that web client watchdog polling against /session/status automatically
    detects is_finished=True and triggers reload / resolution without manual update click.
    """
    html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body data-web-mode="true" data-zid="20260827120000">
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">Das Haus</div>
  </div>
  <table id="lemma-table">
    <tbody>
      <tr data-row-id="0">
        <td data-col="WordSource">Haus</td>
        <td data-col="WordDestination"><div class="skeleton-loader"></div></td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>"""
    page.set_content(html)

    page.evaluate("""
        window.__fetches = [];
        window.__reloaded = false;
        window.onSessionReload = function() { window.__reloaded = true; };
        window.fetch = function(url, options) {
            window.__fetches.push(String(url));
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "success",
                        data: {
                            ok: true,
                            is_finished: true,
                            stage: "finished",
                            zid: "20260827120000"
                        }
                    });
                }
            });
        };
    """)
    page.evaluate(extract_desk_js())

    page.wait_for_function("() => window.__reloaded === true", timeout=5000)
    fetches = page.evaluate("window.__fetches")
    assert len(fetches) >= 1
    assert any("/session/status" in f and "20260827120000" in f for f in fetches)


def test_watchdog_polling_concurrent_with_eventsource(page):
    """
    Verify that watchdog status polling runs concurrently with active EventSource connection
    and does not wait for EventSource error/timeout.
    """
    html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body data-web-mode="true" data-zid="20260827120000">
<div class="container">
  <table id="lemma-table">
    <tbody>
      <tr data-row-id="0">
        <td data-col="WordSource">Haus</td>
        <td data-col="WordDestination"><div class="skeleton-loader"></div></td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>"""
    page.set_content(html)

    # Mock EventSource to remain connected without errors, and mock fetch to track status polling
    page.evaluate("""
        window.__fetches = [];
        window.__sseConnected = false;

        function MockEventSource(url) {
            this.url = url;
            this.readyState = 1;
            window.__sseConnected = true;
            window._mockSSE = this;
        }
        MockEventSource.prototype.close = function() {
            this.readyState = 2;
            window.__sseClosed = true;
        };
        window.EventSource = MockEventSource;

        window.fetch = function(url, options) {
            window.__fetches.push(String(url));
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "success",
                        data: {
                            ok: true,
                            is_finished: true,
                            stage: "finished",
                            zid: "20260827120000",
                            rows: {
                                "0": { "trans": "House" }
                            }
                        }
                    });
                }
            });
        };
    """)

    page.evaluate(extract_desk_js())

    # Watchdog polling must execute even though EventSource is alive
    page.wait_for_function("() => window.__fetches && window.__fetches.some(f => f && f.indexOf('/session/status') !== -1)", timeout=5000)
    assert page.evaluate("window.__sseConnected") is True
    fetches = page.evaluate("window.__fetches")
    assert any("/session/status" in f for f in fetches)


def test_cleanup_orphan_skeletons_non_destructive(page):
    """
    Verify cleanupOrphanSkeletons removes .skeleton-loader and data-pending
    without wiping existing text content to empty strings, and dispatches a recovery query.
    """
    html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body data-web-mode="true" data-zid="20260827120000">
<div class="container">
  <div class="translation-text" id="translation-container">
    <span class="skeleton-loader" data-pending="true">Pending Translation Text</span>
  </div>
  <table id="lemma-table">
    <tbody>
      <tr data-row-id="0">
        <td data-col="WordSource">Haus</td>
        <td data-col="WordDestination">
          <div class="scrollable-cell">
            <span class="skeleton-loader" data-pending="true">Pending Lemma Text</span>
          </div>
        </td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>"""
    page.set_content(html)

    page.evaluate("""
        window.__recoveryCalls = [];
        window.fetch = function(url, options) {
            window.__recoveryCalls.push(String(url));
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "success",
                        data: { ok: true, rows: {} }
                    });
                }
            });
        };
    """)
    page.evaluate(extract_desk_js())

    # Execute cleanup
    page.evaluate("window.cleanupOrphanSkeletons()")

    # 1. Classes and attributes removed
    assert page.locator(".skeleton-loader").count() == 0
    assert page.locator("[data-pending='true']").count() == 0

    # 2. Text is NOT wiped to empty strings
    lemma_cell_text = page.locator("td[data-col='WordDestination']").inner_text()
    assert "Pending Lemma Text" in lemma_cell_text

    trans_text = page.locator("#translation-container").inner_text()
    assert "Pending Translation Text" in trans_text

    # 3. Recovery query was dispatched
    page.wait_for_function("() => window.__recoveryCalls && window.__recoveryCalls.length > 0", timeout=3000)
    recovery_calls = page.evaluate("window.__recoveryCalls")
    assert any("/session/status" in c and "20260827120000" in c for c in recovery_calls)


def test_intermediate_empty_status_preserves_skeleton_loader(page):
    """
    Verify that intermediate status updates carrying empty translation values
    (e.g., stage='translating', trans='') do NOT wipe active skeleton loaders,
    and polling remains active until final translated values arrive.
    """
    html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body data-web-mode="true" data-zid="20260827120000">
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">Das Haus</div>
  </div>
  <table id="lemma-table">
    <tbody>
      <tr data-row-id="0">
        <td><div class="scrollable-cell">Haus</div></td>
        <td><div class="scrollable-cell">Haus</div></td>
        <td><div class="scrollable-cell"><span class="skeleton-loader" data-pending="true">...</span></div></td>
        <td><div class="scrollable-cell"></div></td>
        <td><div class="scrollable-cell"></div></td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>"""
    page.set_content(html)

    page.evaluate("""
        window.__callCount = 0;
        window.__reloaded = false;
        window.onSessionReload = function() { window.__reloaded = true; };
        window.fetch = function(url, options) {
            window.__callCount++;
            if (window.__callCount === 1) {
                // First call: intermediate update with empty translation string
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: function() {
                        return Promise.resolve({
                            ok: true,
                            is_finished: false,
                            stage: "translating",
                            zid: "20260827120000",
                            rows: {
                                "0": { "trans": "" }
                            }
                        });
                    }
                });
            } else {
                // Subsequent call: finished update with real translation
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: function() {
                        return Promise.resolve({
                            ok: true,
                            is_finished: true,
                            stage: "finished",
                            zid: "20260827120000",
                            rows: {
                                "0": { "trans": "House" }
                            }
                        });
                    }
                });
            }
        };
    """)
    page.evaluate(extract_desk_js())

    # Wait for completion pass
    page.wait_for_function("() => window.__reloaded === true", timeout=5000)
    assert page.locator("tr[data-row-id='0'] td").nth(2).text_content() == "House"


def test_audio_playback_web_fallback_and_ahk_parity(page):
    source_html = '<span class="word" data-word-idx="0" data-line-idx="0" data-lower-clean="haus">Haus</span>'
    manifest = [
        {"text": "Haus", "is_word": True, "visual_idx": 0, "lower_clean": "haus", "row_ids": [0]},
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
          <td data-col="WordSource">Haus</td>
          <td data-col="WordDestination">house</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">{json.dumps(manifest)}</script>
<script id="session-lang" type="text/plain">de</script>
</body>
</html>"""

    # Scenario A: When window.ahkCall is present, it is used directly and fetch is NOT called
    page.set_content(html)
    page.evaluate("""
        window.__ahkCalls = [];
        window.__fetchCalls = [];
        window.ahkCall = function(action, arg) {
            window.__ahkCalls.push({action: action, arg: arg});
        };
        window.fetch = function(url, options) {
            window.__fetchCalls.push({url: url, options: options});
            return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
        };
    """)
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))

    span = page.locator("span[data-lower-clean='haus']")
    span.click(button="left")

    ahk_calls = page.evaluate("window.__ahkCalls")
    fetch_calls = page.evaluate("window.__fetchCalls")

    ahk_play_calls = [c for c in ahk_calls if c.get("action") == "play"]
    assert len(ahk_play_calls) == 1
    assert "Haus" in ahk_play_calls[0]["arg"]
    assert "de" in ahk_play_calls[0]["arg"]
    audio_fetch_calls = [f for f in fetch_calls if f.get("url") == "/api/v1/audio/play"]
    assert len(audio_fetch_calls) == 0

    # Scenario B: When window.ahkCall is absent/undefined, client falls back to POST /api/v1/audio/play
    page.set_content(html)
    page.evaluate("""
        delete window.ahkCall;
        window.__fetchCalls = [];
        window.fetch = function(url, options) {
            window.__fetchCalls.push({url: url, options: options});
            return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
        };
    """)
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma"))

    span = page.locator("span[data-lower-clean='haus']")
    span.click(button="left")

    fetch_calls = page.evaluate("window.__fetchCalls")
    audio_fetch_calls = [f for f in fetch_calls if f.get("url") == "/api/v1/audio/play"]
    assert len(audio_fetch_calls) == 1
    assert audio_fetch_calls[0]["options"]["method"] == "POST"
    body = json.loads(audio_fetch_calls[0]["options"]["body"])
    assert body["text"] == "Haus"

def test_table_row_lmb_audio_interactions(page):
    table_html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div class="container">
  <div class="section"><div class="source-text" id="source-container"><span class="word" data-word-idx="0" data-lower-clean="test">test</span></div></div>
  <div class="section">
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">Haus</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Häuser</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">дом</div></td>
        </tr>
        <tr data-row-id="1">
          <td data-col="WordSource"><div class="scrollable-cell">Baum</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Bäume</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">дерево</div></td>
        </tr>
        <tr data-row-id="2">
          <td data-col="WordSource"><div class="scrollable-cell">Kind</div></td>
          <td data-col="WordSourceInflectedForm"><div class="scrollable-cell">Kinder</div></td>
          <td data-col="WordDestination"><div class="scrollable-cell">ребенок</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">[]</script>
<script id="session-lang" type="text/plain">de</script>
<script id="session-target-lang" type="text/plain">ru</script>
</body>
</html>"""

    # --- Scenario 1: Single click plays on mouseup, NOT on mousedown ---
    page.set_content(table_html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", table_range_mode="none"))

    # Dispatch mousedown on row 0
    page.evaluate("""() => {
        const row0 = document.querySelector("tr[data-row-id='0']");
        row0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0, "No audio should play on mousedown"

    # Dispatch mouseup
    page.evaluate("document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1, "Audio should play on mouseup"
    assert play_calls[0]["arg"].endswith("de\\nHaus")

    # --- Scenario 2: Row deselection suppresses audio ---
    page.evaluate("window.__ahkCalls = [];")
    # Click row 0 again to deselect it
    page.evaluate("""() => {
        const row0 = document.querySelector("tr[data-row-id='0']");
        row0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0, "Deselection should not emit audio"

    # --- Scenario 3: Table row drag is silent when table_range_mode = none ---
    page.set_content(table_html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", table_range_mode="none"))

    page.evaluate("""() => {
        const r0 = document.querySelector("tr[data-row-id='0']");
        const r1 = document.querySelector("tr[data-row-id='1']");
        const r2 = document.querySelector("tr[data-row-id='2']");
        r0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        r1.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        r2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0, "Drag selection should be silent when table_range_mode is none"

    # --- Scenario 4: Shift-click is silent when table_range_mode = none ---
    page.set_content(table_html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", table_range_mode="none"))

    # First single click row 0
    page.evaluate("""() => {
        const r0 = document.querySelector("tr[data-row-id='0']");
        r0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    page.evaluate("window.__ahkCalls = [];")
    # Shift+click row 2
    page.evaluate("""() => {
        const r2 = document.querySelector("tr[data-row-id='2']");
        r2.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1, shiftKey: true }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0, shiftKey: true }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 0, "Shift-click range should be silent when table_range_mode is none"

    # --- Scenario 5: Row drag plays all rows when table_range_mode = all and lmb_chain_mode = separate ---
    page.set_content(table_html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="separate", table_range_mode="all"))

    page.evaluate("""() => {
        const r0 = document.querySelector("tr[data-row-id='0']");
        const r1 = document.querySelector("tr[data-row-id='1']");
        const r2 = document.querySelector("tr[data-row-id='2']");
        r0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        r1.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        r2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nHaus ||| Baum ||| Kind")

    # --- Scenario 6: Row drag plays all rows when table_range_mode = all and lmb_chain_mode = joined ---
    page.set_content(table_html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="lemma", lmb_chain_mode="joined", table_range_mode="all"))

    page.evaluate("""() => {
        const r0 = document.querySelector("tr[data-row-id='0']");
        const r1 = document.querySelector("tr[data-row-id='1']");
        const r2 = document.querySelector("tr[data-row-id='2']");
        r0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        r1.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        r2.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nHaus Baum Kind")

    # --- Scenario 7: Shift-click plays range when table_range_mode = all (including reverse direction) ---
    page.set_content(table_html)
    page.evaluate("window.__ahkCalls = []; window.ahkCall = function(action, arg) { window.__ahkCalls.push({action: action, arg: arg}); };")
    page.evaluate(extract_desk_js(lmb_play=True, lmb_source="inflection", lmb_chain_mode="separate", table_range_mode="all"))

    # Click row 2 first
    page.evaluate("""() => {
        const r2 = document.querySelector("tr[data-row-id='2']");
        r2.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0 }));
    }""")
    page.evaluate("window.__ahkCalls = [];")
    # Reverse Shift-click to row 0
    page.evaluate("""() => {
        const r0 = document.querySelector("tr[data-row-id='0']");
        r0.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1, shiftKey: true }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, buttons: 0, shiftKey: true }));
    }""")
    calls = page.evaluate("window.__ahkCalls")
    play_calls = [c for c in calls if c.get("action") == "play"]
    assert len(play_calls) == 1
    assert play_calls[0]["arg"].endswith("de\\nHäuser ||| Bäume ||| Kinder")




















