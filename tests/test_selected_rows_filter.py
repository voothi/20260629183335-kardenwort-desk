import json
import pytest
from pathlib import Path
import kardenwort_desk

def get_desk_page_html(tmp_path, theme="dark", zid="20260912232500", text_mode="multi"):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    tsv_file = tmp_path / f"{zid}-filter-test.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\tDas Haus ist groß.\tДом большой.\t1\n"
        "groß\tgroß\tбольшой\t1\tDas Haus ist groß.\tДом большой.\t0\n"
        "Baum\tBaum\tдерево\t2\tDer Baum ist grün.\tДерево зелёное.\t0\n"
        "grün\tgrün\tзелёный\t2\tDer Baum ist grün.\tДерево зелёное.\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Das Haus ist groß.\nDer Baum ist grün.",
        language="de",
        zid=zid,
        text_mode=text_mode,
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
window.__reloads = 0;
window.onSessionReload = function() {
    window.__reloads = (window.__reloads || 0) + 1;
};
if (window.location) {
    try {
        window.location.reload = function() {
            window.__reloads = (window.__reloads || 0) + 1;
        };
    } catch(e) {}
}
window.fetch = async function(url, options) {
    var bodyObj = (options && options.body) ? JSON.parse(options.body) : {};
    window.__fetches.push({ url: url, options: options, body: bodyObj });
    return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, status: 'success' })
    };
};
</script>"""
    return html.replace('<head>', '<head>' + mock_script)


def test_selected_filter_button_markup_and_order(tmp_path):
    """Verifies that #kw-btn-filter-selected is present immediately before #kw-btn-save."""
    html = get_desk_page_html(tmp_path)
    
    assert 'id="kw-btn-filter-selected"' in html
    assert 'title="Toggle view: show only selected words"' in html
    assert '>Selected</button>' in html
    
    # Verify order in toolbar: Selected appears before Save
    filter_idx = html.find('id="kw-btn-filter-selected"')
    save_idx = html.find('id="kw-btn-save"')
    assert filter_idx != -1 and save_idx != -1
    assert filter_idx < save_idx


def test_selected_filter_css_rules(tmp_path):
    """Verifies that CSS rules for .btn-filter-active and .kw-filter-selected-only exist."""
    html = get_desk_page_html(tmp_path)
    
    assert '.kw-action-toolbar button.btn-filter-active' in html
    assert 'background: #d29922;' in html
    assert 'color: #0d1117;' in html
    assert '#lemma-table.kw-filter-selected-only:not(.kw-table-dragging) tbody tr[data-selected="0"]' in html
    assert 'display: none !important;' in html
    assert '.kw-empty-selection-row' in html
    assert '#lemma-table.kw-filter-selected-only tbody tr.kw-empty-selection-row.kw-empty-visible' in html
    assert '.kw-empty-selection-cell' in html


def test_selected_filter_toggle_interaction(page, tmp_path):
    """Verifies clicking the Selected button toggles filter state and row visibility."""
    html = inject_mock_fetch(get_desk_page_html(tmp_path, text_mode="single"))
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    lemma_table = page.locator("#lemma-table")
    
    assert filter_btn.is_visible()
    assert not filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert not lemma_table.evaluate("el => el.classList.contains('kw-filter-selected-only')")
    assert page.evaluate("window.AppState.filterSelectedOnly") is False

    # Row 0 (Haus) has data-selected="1", Row 1 (groß) has data-selected="0"
    row0 = page.locator("#lemma-table tbody tr[data-row-id='0']")
    row1 = page.locator("#lemma-table tbody tr[data-row-id='1']")
    assert row0.is_visible()
    assert row1.is_visible()

    # Click Selected filter toggle
    filter_btn.click()
    page.wait_for_timeout(50)

    # State should now be active
    assert filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert lemma_table.evaluate("el => el.classList.contains('kw-filter-selected-only')")
    assert page.evaluate("window.AppState.filterSelectedOnly") is True

    # Selected row remains visible, unselected row is hidden
    assert row0.is_visible()
    assert not row1.is_visible()

    # Click Selected button again to deactivate
    filter_btn.click()
    page.wait_for_timeout(50)

    # State restored to inactive and both rows visible
    assert not filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert not lemma_table.evaluate("el => el.classList.contains('kw-filter-selected-only')")
    assert page.evaluate("window.AppState.filterSelectedOnly") is False
    assert row0.is_visible()
    assert row1.is_visible()


def test_dynamic_selection_reactivity_without_reload(page, tmp_path):
    """Verifies that toggling word selection while filter is active dynamically shows/hides rows without page reload."""
    html = inject_mock_fetch(get_desk_page_html(tmp_path, text_mode="single"))
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    filter_btn.click()
    page.wait_for_timeout(50)

    row0 = page.locator("#lemma-table tbody tr[data-row-id='0']")
    row1 = page.locator("#lemma-table tbody tr[data-row-id='1']")
    assert row0.is_visible()
    assert not row1.is_visible()

    # Click on the word "groß" in source text or toggle row 1 selection
    page.evaluate("window.toggleRowSelection(1, true)")
    page.wait_for_timeout(50)

    # Row 1 is now selected (data-selected="1") and instantly visible in filtered table
    assert row1.get_attribute("data-selected") == "1"
    assert row1.is_visible()
    assert page.evaluate("window.__reloads") == 0

    # Deselect Row 0 (Haus)
    page.evaluate("window.toggleRowSelection(0, false)")
    page.wait_for_timeout(50)

    # Row 0 is now unselected (data-selected="0") and instantly hidden from filtered table
    assert row0.get_attribute("data-selected") == "0"
    assert not row0.is_visible()
    assert page.evaluate("window.__reloads") == 0


def test_tab_switching_filter_persistence(page, tmp_path):
    """Verifies that filter state persists when navigating between Sentence Card tabs and Overview tab."""
    html = inject_mock_fetch(get_desk_page_html(tmp_path, text_mode="multi"))
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    lemma_table = page.locator("#lemma-table")

    # Turn filter ON
    filter_btn.click()
    page.wait_for_timeout(50)
    assert page.evaluate("window.AppState.filterSelectedOnly") is True
    assert filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert lemma_table.evaluate("el => el.classList.contains('kw-filter-selected-only')")

    # Switch to Sentence Card 2 (Tab seq 3 or sentence_idx 2)
    tabs = page.locator(".kw-tab-chip")
    tab_count = tabs.count()
    assert tab_count >= 2

    # Click next tab
    page.evaluate("window.WorkspaceTabs.nextTab()")
    page.wait_for_timeout(50)

    # Verify filter remains active after tab switch
    assert page.evaluate("window.AppState.filterSelectedOnly") is True
    assert filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert lemma_table.evaluate("el => el.classList.contains('kw-filter-selected-only')")

    # Switch back to Overview tab (Tab seq 1)
    page.evaluate("window.WorkspaceTabs.switchToTab(1)")
    page.wait_for_timeout(50)

    assert page.evaluate("window.AppState.filterSelectedOnly") is True
    assert filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert lemma_table.evaluate("el => el.classList.contains('kw-filter-selected-only')")


def test_keyboard_arrow_navigation_skips_hidden_rows(page, tmp_path):
    """Verifies that ArrowDown and ArrowUp skip rows hidden by the filter."""
    html = inject_mock_fetch(get_desk_page_html(tmp_path, text_mode="single"))
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    # Row 0 is selected, Row 1 is unselected
    # Let's add row 2 selected so we have Row 0 (visible), Row 1 (hidden), Row 2 (visible)
    page.evaluate("""() => {
        var tbody = document.querySelector('#lemma-table tbody');
        var tr2 = document.createElement('tr');
        tr2.setAttribute('data-row-id', '2');
        tr2.setAttribute('data-selected', '1');
        tr2.innerHTML = '<td class="editable" data-col="WordSource">Baum</td>';
        tbody.appendChild(tr2);
        window.rebindTableRows();
    }""")

    # Enable filter
    filter_btn.click()
    page.wait_for_timeout(50)

    # Focus initial row using ArrowDown
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(50)
    focused_id_1 = page.evaluate("window.getFocusedRowId()")
    assert focused_id_1 == 0

    # Press ArrowDown again -> should skip hidden Row 1 and focus Row 2
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(50)
    focused_id_2 = page.evaluate("window.getFocusedRowId()")
    assert focused_id_2 == 2

    # Press ArrowUp -> should skip hidden Row 1 and focus Row 0
    page.keyboard.press("ArrowUp")
    page.wait_for_timeout(50)
    focused_id_3 = page.evaluate("window.getFocusedRowId()")
    assert focused_id_3 == 0


def test_clicking_middle_selected_row_does_not_cascade_deselection(page, tmp_path):
    """Verifies that clicking a selected row in the middle of a filtered table only deselects that single row, without cascading to remaining rows."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    zid = "20260912233600"
    tsv_file = tmp_path / f"{zid}-cascade.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Baum\tBaum\tдерево\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Katze\tKatze\tкошка\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Hund\tHund\tсобака\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Vogel\tVogel\tптица\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Haus Baum Katze Hund Vogel",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False
    )
    html = inject_mock_fetch(html)
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    filter_btn.click()
    page.wait_for_timeout(50)

    # All rows initially selected and visible in filtered mode
    row_info = page.evaluate("Array.from(document.querySelectorAll('#lemma-table tbody tr')).map(r => ({id: r.getAttribute('data-row-id'), sel: r.getAttribute('data-selected'), text: r.innerText}))")
    assert len(row_info) >= 5, f"Expected 5 rows, got {row_info}"

    for r in row_info:
        r_id = r['id']
        assert page.locator(f"#lemma-table tbody tr[data-row-id='{r_id}']").is_visible()

    # Click on a middle row (e.g. 2nd element) with Control modifier to toggle deselection
    mid_id = row_info[2]['id']
    mid_row = page.locator(f"#lemma-table tbody tr[data-row-id='{mid_id}']")
    mid_row.locator("td").first.click(modifiers=["Control"])
    page.wait_for_timeout(50)

    # Only mid_id should be hidden; other rows MUST remain visible
    assert not page.locator(f"#lemma-table tbody tr[data-row-id='{mid_id}']").is_visible()
    for r in row_info:
        r_id = r['id']
        if r_id != mid_id:
            assert page.locator(f"#lemma-table tbody tr[data-row-id='{r_id}']").is_visible()


def test_drag_deselection_across_rows_in_filtered_mode(page, tmp_path):
    """Verifies that dragging across rows in Selected mode with Ctrl smoothly deselects the dragged range and hides them on mouseup."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    zid = "20260913000500"
    tsv_file = tmp_path / f"{zid}-drag-filtered.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Baum\tBaum\tдерево\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Katze\tKatze\tкошка\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Hund\tHund\tсобака\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
        "Vogel\tVogel\tптица\t1\tHaus Baum Katze Hund Vogel\tДом дерево кошка собака птица\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Haus Baum Katze Hund Vogel",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False
    )
    html = inject_mock_fetch(html)
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    filter_btn.click()
    page.wait_for_timeout(50)

    # Perform drag gesture across row 1 (Baum) to row 2 (Katze) with Ctrl held
    row1 = page.locator("#lemma-table tbody tr").nth(1)
    row2 = page.locator("#lemma-table tbody tr").nth(2)

    box1 = row1.bounding_box()
    box2 = row2.bounding_box()
    assert box1 is not None and box2 is not None

    page.keyboard.down("Control")
    page.mouse.move(box1["x"] + 20, box1["y"] + box1["height"] / 2)
    page.mouse.down()
    page.mouse.move(box2["x"] + 20, box2["y"] + box2["height"] / 2, steps=5)
    page.mouse.up()
    page.keyboard.up("Control")
    page.wait_for_timeout(50)

    # Rows 1 and 2 should now be deselected and hidden in filtered mode
    # Rows 0, 3, 4 should remain selected and visible
    row0_el = page.locator("#lemma-table tbody tr").nth(0)
    row3_el = page.locator("#lemma-table tbody tr").nth(3)
    row4_el = page.locator("#lemma-table tbody tr").nth(4)

    assert row0_el.is_visible()
    assert not row1.is_visible()
    assert not row2.is_visible()
    assert row3_el.is_visible()
    assert row4_el.is_visible()


def test_empty_selection_placeholder_displayed_when_no_rows_selected(page, tmp_path):
    """Verifies that when the Selected filter is active and 0 rows are selected, an empty state row is displayed."""
    html = inject_mock_fetch(get_desk_page_html(tmp_path, text_mode="single"))
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")
    
    # Deselect row 0 so 0 rows are selected
    page.evaluate("window.toggleRowSelection(0, false)")
    page.wait_for_timeout(50)

    # Empty placeholder should NOT be present when filter is OFF
    assert page.locator("#kw-empty-selection-row").count() == 0

    # Turn filter ON
    filter_btn.click()
    page.wait_for_timeout(50)

    # Now empty state row must be present and visible
    empty_row = page.locator("#kw-empty-selection-row")
    assert empty_row.is_visible()
    assert "No items selected." in empty_row.inner_text()
    assert "show all words" in empty_row.inner_text()

    # Click the "show all words" action link
    clear_btn = page.locator("#kw-btn-empty-clear-filter")
    clear_btn.click()
    page.wait_for_timeout(50)

    # Filter is turned OFF
    assert page.evaluate("window.AppState.filterSelectedOnly") is False
    assert not filter_btn.evaluate("el => el.classList.contains('btn-filter-active')")
    assert page.locator("#kw-empty-selection-row").count() == 0
    assert page.locator("#lemma-table tbody tr[data-row-id='0']").is_visible()
    assert page.locator("#lemma-table tbody tr[data-row-id='1']").is_visible()


def test_empty_selection_placeholder_reactivity_on_selecting_word(page, tmp_path):
    """Verifies that selecting a word while the empty placeholder is visible immediately hides it and shows the row."""
    html = inject_mock_fetch(get_desk_page_html(tmp_path, text_mode="single"))
    page.set_content(html)

    filter_btn = page.locator("#kw-btn-filter-selected")

    # Deselect row 0 -> 0 rows selected
    page.evaluate("window.toggleRowSelection(0, false)")
    filter_btn.click()
    page.wait_for_timeout(50)

    empty_row = page.locator("#kw-empty-selection-row")
    assert empty_row.is_visible()

    # Select row 1 (groß)
    page.evaluate("window.toggleRowSelection(1, true)")
    page.wait_for_timeout(50)

    # Placeholder must be removed, row 1 must be visible
    assert page.locator("#kw-empty-selection-row").count() == 0
    assert page.locator("#lemma-table tbody tr[data-row-id='1']").is_visible()

    # Deselect row 1 again -> placeholder reappears
    page.evaluate("window.toggleRowSelection(1, false)")
    page.wait_for_timeout(50)
    assert empty_row.is_visible()


