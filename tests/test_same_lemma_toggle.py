import json
import pytest
from pathlib import Path
import configparser
import kardenwort_desk

def get_desk_page_html(tmp_path, theme="dark", zid="20261002101427", text_mode="multi", highlight_same_lemma=False):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("rendering"):
        config.add_section("rendering")
    config.set("rendering", "highlight_same_lemma", "true" if highlight_same_lemma else "false")

    tsv_file = tmp_path / f"{zid}-same-lemma-test.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tдом\t1\tDas Haus ist groß.\tДом большой.\t1\n"
        "groß\tgroß\tбольшой\t1\tDas Haus ist groß.\tДом большой.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Das Haus ist groß.",
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
window.fetch = async function(url, options) {
    var bodyObj = (options && options.body) ? JSON.parse(options.body) : {};
    window.__fetches.push({ url: url, options: options, body: bodyObj });
    return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, status: 'success', rows: {} })
    };
};
</script>"""
    return html.replace('<head>', '<head>' + mock_script)


def test_same_lemma_button_markup_and_order(tmp_path):
    """Verifies that #kw-btn-same-lemma is present immediately before #kw-btn-filter-selected."""
    html = get_desk_page_html(tmp_path, highlight_same_lemma=False)
    
    assert 'id="kw-btn-same-lemma"' in html
    assert 'title="Toggle Lemma-wide highlighting"' in html
    assert '>Lemma</button>' in html
    
    # Verify order in toolbar: Same Lemma appears before Selected
    same_lemma_idx = html.find('id="kw-btn-same-lemma"')
    filter_idx = html.find('id="kw-btn-filter-selected"')
    assert same_lemma_idx != -1 and filter_idx != -1
    assert same_lemma_idx < filter_idx


def test_same_lemma_initial_active_class(tmp_path):
    """Verifies that active class is applied based on highlight_same_lemma config."""
    # When false
    html_off = get_desk_page_html(tmp_path, highlight_same_lemma=False)
    assert 'id="kw-btn-same-lemma" class="btn-toggle"' in html_off
    
    # When true
    html_on = get_desk_page_html(tmp_path, highlight_same_lemma=True)
    assert 'id="kw-btn-same-lemma" class="btn-toggle active"' in html_on


def test_same_lemma_css_rules(tmp_path):
    """Verifies that CSS rules for #kw-btn-same-lemma.active and .lemma-peer-highlight exist."""
    html = get_desk_page_html(tmp_path)
    
    assert '.kw-action-toolbar button#kw-btn-same-lemma.active' in html
    assert '.source-text span.word.lemma-peer-highlight' in html


def test_same_lemma_toggle_interaction(page, tmp_path):
    """Verifies clicking #kw-btn-same-lemma toggles state, active class, and sends config update."""
    raw_html = get_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    
    page.set_content(html)
    
    same_lemma_btn = page.locator("#kw-btn-same-lemma")
    assert same_lemma_btn.is_visible()
    
    # Initially inactive
    assert not same_lemma_btn.evaluate("el => el.classList.contains('active')")
    assert page.evaluate("() => window.AppState.highlightSameLemma") is False
    
    # Click to toggle ON
    same_lemma_btn.click()
    assert same_lemma_btn.evaluate("el => el.classList.contains('active')")
    assert page.evaluate("() => window.AppState.highlightSameLemma") is True
    assert "Lemma highlighting enabled" in page.locator("#kw-toast-container").inner_text()
    
    # Verify fetch was called to persist config
    fetches = page.evaluate("() => window.__fetches")
    assert len(fetches) == 1
    assert fetches[0]["url"] == "/api/v1/config"
    assert fetches[0]["body"]["section"] == "rendering"
    assert fetches[0]["body"]["key"] == "highlight_same_lemma"
    assert fetches[0]["body"]["value"] == "true"
    
    # Click to toggle OFF
    same_lemma_btn.click()
    assert not same_lemma_btn.evaluate("el => el.classList.contains('active')")
    assert page.evaluate("() => window.AppState.highlightSameLemma") is False
    assert "Lemma highlighting disabled" in page.locator("#kw-toast-container").inner_text()
    
    fetches = page.evaluate("() => window.__fetches")
    assert len(fetches) == 2
    assert fetches[1]["body"]["value"] == "false"


def get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=False, zid="20261002101427"):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("rendering"):
        config.add_section("rendering")
    config.set("rendering", "highlight_same_lemma", "true" if highlight_same_lemma else "false")

    tsv_file = tmp_path / f"{zid}-multi-test.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Am\tder\tв\t1\tAm Morgen trinke ich Kaffee.\tУтром я пью кофе.\t0\n"
        "Beim\tder\tво время\t2\tBeim Spiel hatte er Spaß.\tВо время игры ему было весело.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Am Morgen trinke ich Kaffee.\nBeim Spiel hatte er Spaß.",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    return html


def test_isolated_token_selection_when_same_lemma_off(page, tmp_path):
    """Verifies that selecting a token only highlights that token when Same Lemma is OFF."""
    raw_html = get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")

    # Locate token spans: "Am" in sent 1, "Beim" in sent 2
    beim_span = page.locator('#source-container span.word:has-text("Beim")').first
    am_span = page.locator('#source-container span.word:has-text("Am")').first
    
    assert beim_span.is_visible()
    assert am_span.is_visible()

    # Click "Beim"
    beim_span.click()

    # "Beim" should be actively highlighted
    assert "highlight-orange-active" in (beim_span.get_attribute("class") or "")
    
    # "Am" should NOT receive active highlight or peer highlight
    assert "highlight-orange-active" not in (am_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (am_span.get_attribute("class") or "")

    # AppState should record activeTokenSelections with sentence_idx=2
    active_tokens = page.evaluate("() => window.AppState.activeTokenSelections")
    assert len(active_tokens) == 1
    assert active_tokens[0]["sentence_idx"] == 2


def test_peer_highlighting_when_same_lemma_on(page, tmp_path):
    """Verifies that selecting a token shows muted peer highlight when Same Lemma is ON."""
    raw_html = get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=True)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")

    beim_span = page.locator('#source-container span.word:has-text("Beim")').first
    am_span = page.locator('#source-container span.word:has-text("Am")').first
    
    # Click "Beim"
    beim_span.click()

    # "Beim" gets primary active orange highlight
    assert "highlight-orange-active" in (beim_span.get_attribute("class") or "")
    
    # "Am" gets muted peer highlight
    assert "lemma-peer-highlight" in (am_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (am_span.get_attribute("class") or "")

    # Toggle Same Lemma OFF via button click
    same_lemma_btn = page.locator("#kw-btn-same-lemma")
    same_lemma_btn.click()

    # Peer highlight should be removed from "Am"
    assert "lemma-peer-highlight" not in (am_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (beim_span.get_attribute("class") or "")

    # Toggle back ON
    same_lemma_btn.click()
    assert "lemma-peer-highlight" in (am_span.get_attribute("class") or "")


def test_table_row_click_clears_active_token_selections(page, tmp_path):
    """Verifies that clicking a table row clears token-scoped selection and selects all peer tokens."""
    raw_html = get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    beim_span = page.locator('#source-container span.word:has-text("Beim")').first
    am_span = page.locator('#source-container span.word:has-text("Am")').first
    
    # 1. Click "Beim" in text -> token-scoped selection
    beim_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 1
    assert "highlight-orange-active" in (beim_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (am_span.get_attribute("class") or "")

    # 2. Click table row directly -> clears activeTokenSelections
    table_row = page.locator("#lemma-table tbody tr").first
    table_row.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0

    # 3. Click table row to select it row-scoped -> with Lemma OFF, only primary occurrence (Am) gets highlight-orange-active
    table_row.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0
    assert "highlight-orange-active" in (am_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (beim_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (beim_span.get_attribute("class") or "")

    # 4. Toggle Lemma ON -> peer occurrence (Beim) dynamically receives lemma-peer-highlight in real time
    page.locator("#kw-btn-same-lemma").click()
    assert "highlight-orange-active" in (am_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (beim_span.get_attribute("class") or "")

    # 5. Toggle Lemma OFF -> peer occurrence loses lemma-peer-highlight
    page.locator("#kw-btn-same-lemma").click()
    assert "lemma-peer-highlight" not in (beim_span.get_attribute("class") or "")


def test_targeted_token_export_creates_single_card(page, tmp_path, monkeypatch):
    """Verifies that targeted text selection exports strictly the selected occurrence (Task 6.3)."""
    monkeypatch.setattr(kardenwort_desk, "run_detached_import", lambda *a, **k: (1234, Path("test.log")))
    raw_html = get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")

    # Click "Beim" in Sentence 2
    beim_span = page.locator('#source-container span.word:has-text("Beim")').first
    beim_span.click()

    # Trigger Send to Anki export
    page.locator("#kw-btn-export").click()
    page.wait_for_timeout(100)

    # 1. Verify client fetch sent strictly row_ids=[1] (Sentence 2)
    fetches = page.evaluate("() => window.__fetches")
    export_fetches = [f for f in fetches if f["url"] == "/session/export"]
    assert len(export_fetches) == 1
    assert export_fetches[0]["body"]["row_ids"] == [1]
    assert export_fetches[0]["body"]["selected_row_ids"] == [1]

    # 2. Verify backend core_export creates strictly 1 card with Sentence 2 context
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("settings", "send_to_anki", "false")
    resolved_paths["favorites_output_dir"] = str(tmp_path / "favorites")
    (tmp_path / "favorites").mkdir(parents=True, exist_ok=True)

    tsv_file = tmp_path / "20261002101427-multi-test.de.tsv"
    res = kardenwort_desk.core_export(
        tsv_path_or_session=tsv_file,
        selected_row_ids=[1],
        config=config,
        resolved_paths=resolved_paths,
        zid="20261002101427",
        language="de"
    )
    fav_file = Path(res["tsv"])
    assert fav_file.exists()
    _, headers, data_rows = kardenwort_desk.load_tsv_rows(fav_file)
    assert len(data_rows) == 1
    quotation_idx = headers.index("Quotation")
    sentence_idx = headers.index("SentenceSource")
    assert data_rows[0][quotation_idx] == "Beim"
    assert data_rows[0][sentence_idx] == "Beim Spiel hatte er Spaß."


def test_merged_row_export_unrolls_all_occurrences(page, tmp_path, monkeypatch):
    """Verifies that selecting the merged table row unrolls all occurrences as distinct Anki cards (Task 6.4)."""
    monkeypatch.setattr(kardenwort_desk, "run_detached_import", lambda *a, **k: (1234, Path("test.log")))
    raw_html = get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#lemma-table tbody tr")

    # Verify row is selected (unrolling both occurrences data-all-row-ids="0,1")
    # If not selected, click to select it
    is_selected = page.evaluate("() => window.getSelectedRowsArray().length > 0")
    if not is_selected:
        table_row = page.locator("#lemma-table tbody tr").first
        table_row.click()

    # AppState active token selections must be empty (row-scoped selection)
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1]

    # Trigger Send to Anki export
    page.locator("#kw-btn-export").click()
    page.wait_for_timeout(100)

    # 1. Verify client fetch sent unrolled row_ids=[0, 1]
    fetches = page.evaluate("() => window.__fetches")
    export_fetches = [f for f in fetches if f["url"] == "/session/export"]
    assert len(export_fetches) == 1
    assert export_fetches[0]["body"]["row_ids"] == [0, 1]
    assert export_fetches[0]["body"]["selected_row_ids"] == [0, 1]

    # 2. Verify backend core_export creates 2 cards for both sentences
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("settings", "send_to_anki", "false")
    resolved_paths["favorites_output_dir"] = str(tmp_path / "favorites_unroll")
    (tmp_path / "favorites_unroll").mkdir(parents=True, exist_ok=True)

    tsv_file = tmp_path / "20261002101427-multi-test.de.tsv"
    res = kardenwort_desk.core_export(
        tsv_path_or_session=tsv_file,
        selected_row_ids=[0, 1],
        config=config,
        resolved_paths=resolved_paths,
        zid="20261002101427",
        language="de"
    )
    fav_file = Path(res["tsv"])
    assert fav_file.exists()
    _, headers, data_rows = kardenwort_desk.load_tsv_rows(fav_file)
    assert len(data_rows) == 2
    quotation_idx = headers.index("Quotation")
    sentence_idx = headers.index("SentenceSource")
    assert data_rows[0][quotation_idx] == "Am"
    assert data_rows[0][sentence_idx] == "Am Morgen trinke ich Kaffee."
    assert data_rows[1][quotation_idx] == "Beim"
    assert data_rows[1][sentence_idx] == "Beim Spiel hatte er Spaß."


def test_translation_rollup_and_deduplication(tmp_path):
    """Verifies that overview table deduplicates and rolls up translations across atomic occurrences (Task 4.2)."""
    # 1. Distinct translations: "в" and "во время" -> "в, во время"
    html_distinct = get_multi_occurrence_desk_page_html(tmp_path, zid="20261002235911")
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_distinct, "html.parser")
    row = soup.find("tr", {"data-all-row-ids": "0,1"})
    assert row is not None
    trans_td = row.find("td", class_="col-translation")
    assert trans_td.text.strip() == "в, во время"

    # 2. Identical translations: "в" and "в" -> deduplicated to single "в"
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    tsv_file = tmp_path / "20261002235912-identical.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Am\tder\tв\t1\tAm Morgen trinke ich Kaffee.\tУтром я пью кофе.\t0\n"
        "Beim\tder\tв\t2\tBeim Spiel hatte er Spaß.\tВо время игры ему было весело.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")
    html_identical = kardenwort_desk.run_render_flow(
        text="Am Morgen trinke ich Kaffee.\nBeim Spiel hatte er Spaß.",
        language="de",
        zid="20261002235912",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    soup_identical = BeautifulSoup(html_identical, "html.parser")
    row_identical = soup_identical.find("tr", {"data-all-row-ids": "0,1"})
    assert row_identical is not None
    trans_td_identical = row_identical.find("td", class_="col-translation")
    assert trans_td_identical.text.strip() == "в"


def test_multiline_numbered_sentence_tooltips_and_breakdown(tmp_path):
    """Verifies multiline numbered sentence tooltips and form-to-translation breakdowns (Tasks 5.1 & 5.2)."""
    html = get_multi_occurrence_desk_page_html(tmp_path, zid="20261002235913")
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find("tr", {"data-all-row-ids": "0,1"})
    assert row is not None

    # Task 5.1: Multiline numbered tooltip with Unicode bold words on inflected cell
    inf_td = row.find("td", class_="col-inflected")
    inf_title = inf_td.get("title", "")
    assert "[1] 𝗔𝗺 Morgen trinke ich Kaffee." in inf_title
    assert "[2] 𝗕𝗲𝗶𝗺 Spiel hatte er Spaß." in inf_title
    assert "\n" in inf_title

    # Task 5.2: Form-to-translation mapping breakdown on translation cell
    trans_td = row.find("td", class_="col-translation")
    trans_title = trans_td.get("title", "")
    assert "Am: в" in trans_title
    assert "Beim: во время" in trans_title


def test_reword_dispatch_and_dynamic_rollup(page, tmp_path):
    """Verifies that Re-word adheres to dual-mode dispatch and rolls up dynamic updates in DOM."""
    raw_html = get_multi_occurrence_desk_page_html(tmp_path, highlight_same_lemma=False, zid="20261002235914")
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.evaluate("() => { window.onUpdateClick = function() {}; }")
    page.wait_for_selector("#source-container span.word")

    # 1. Targeted token re-word: click "Beim" in Sentence 2 -> Re-word sends row_ids=[1]
    beim_span = page.locator('#source-container span.word:has-text("Beim")').first
    beim_span.click()
    page.locator("#kw-btn-reword").click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("() => window.__fetches")
    reword_fetches = [f for f in fetches if f["url"] == "/session/reword"]
    assert len(reword_fetches) == 1
    assert reword_fetches[0]["body"]["row_ids"] == [1]

    # 2. Table row re-word: click table row to clear token selection and select merged row
    page.evaluate("() => window.clearAllSelectionsAndNotify()")
    table_row = page.locator("#lemma-table tbody tr").first
    table_row.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1]

    page.wait_for_function("() => !document.getElementById('kw-btn-reword').disabled")
    page.locator("#kw-btn-reword").click()
    page.wait_for_timeout(100)

    fetches = page.evaluate("() => window.__fetches")
    reword_fetches = [f for f in fetches if f["url"] == "/session/reword"]
    assert len(reword_fetches) == 2
    assert reword_fetches[1]["body"]["row_ids"] == [0, 1]

    # 3. Dynamic delta update via receiveUpdate rolls up translations in DOM
    page.evaluate("""() => {
        window.receiveUpdate({
            rows: {
                "0": { trans: "утром" },
                "1": { trans: "во время матча" }
            }
        });
    }""")
    page.wait_for_timeout(100)
    trans_cell_text = page.locator("#lemma-table tbody tr[data-all-row-ids='0,1'] td.col-translation").inner_text()
    assert "утром" in trans_cell_text
    assert "во время матча" in trans_cell_text


def get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=False, zid="20261003101348"):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("rendering"):
        config.add_section("rendering")
    config.set("rendering", "highlight_same_lemma", "true" if highlight_same_lemma else "false")

    tsv_file = tmp_path / f"{zid}-intra-test.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "den\tder\tart.\tв\t1\tEr sieht den Hund, das Kind und die Katze.\tОн видит собаку, ребенка и кошку.\t0\n"
        "das\tder\tart.\tв\t1\tEr sieht den Hund, das Kind und die Katze.\tОн видит собаку, ребенка и кошку.\t0\n"
        "die\tder\tart.\tв\t1\tEr sieht den Hund, das Kind und die Katze.\tОн видит собаку, ребенка и кошку.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = kardenwort_desk.run_render_flow(
        text="Er sieht den Hund, das Kind und die Katze.",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    return html


def test_intra_sentence_duplicate_lemmas_selection_and_highlighting(page, tmp_path):
    """Verifies that non-verb tokens (articles) within the same sentence are not coupled as partners (Tasks 3.1 & 3.2)."""
    raw_html = get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")

    den_span = page.locator('#source-container span.word:has-text("den")').first
    das_span = page.locator('#source-container span.word:has-text("das")').first
    die_span = page.locator('#source-container span.word:has-text("die")').first

    # 1. Click "den" in text
    den_span.click()

    # Strictly "den" is in activeTokenSelections
    active_tokens = page.evaluate("() => window.AppState.activeTokenSelections")
    assert len(active_tokens) == 1

    # "den" gets highlight-orange-active, "das" and "die" do not
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (das_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (die_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (das_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (die_span.get_attribute("class") or "")

    # 2. Toggle Lemma ON -> "das" and "die" dynamically receive lemma-peer-highlight
    page.locator("#kw-btn-same-lemma").click()
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (das_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (die_span.get_attribute("class") or "")

    # 3. Toggle Lemma OFF -> peer highlights removed
    page.locator("#kw-btn-same-lemma").click()
    assert "lemma-peer-highlight" not in (das_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (die_span.get_attribute("class") or "")


def test_first_click_token_selection_when_table_row_preselected(page, tmp_path):
    """Verifies that clicking a table-selected word span immediately deselects it on the first click without adding a frame."""
    raw_html = get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    # Select table row
    table_row = page.locator("#lemma-table tbody tr").first
    table_row.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0

    # Click "das" in text on first click -> deselects immediately on first click
    das_span = page.locator('#source-container span.word:has-text("das")').first
    das_span.click()

    # Must be cleanly deselected without pinning a frame
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0
    assert "highlight-orange-active" not in (das_span.get_attribute("class") or "")
    assert "hl-mvp-pin" not in (das_span.get_attribute("class") or "")


def test_sequential_clicks_shared_lemma_additive_selection(page, tmp_path):
    """Verifies that sequential clicks on words sharing a single lemma row additively select without toggle oscillation (Task 3.1)."""
    raw_html = get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    den_span = page.locator('#source-container span.word:has-text("den")').first
    das_span = page.locator('#source-container span.word:has-text("das")').first
    die_span = page.locator('#source-container span.word:has-text("die")').first

    # 1. Click 'den'
    den_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 1
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1, 2]
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (das_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (die_span.get_attribute("class") or "")

    # 2. Click 'das' -> must additively select 'das' without deselecting row 0
    das_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 2
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1, 2]
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")
    assert "highlight-orange-active" not in (die_span.get_attribute("class") or "")

    # 3. Click 'die' -> all three active, row 0 remains selected
    die_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 3
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1, 2]
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (die_span.get_attribute("class") or "")


def test_dependency_checked_shared_lemma_deselection(page, tmp_path):
    """Verifies that deselecting one token keeps the shared row selected until the final token is deselected (Task 3.2)."""
    raw_html = get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    den_span = page.locator('#source-container span.word:has-text("den")').first
    das_span = page.locator('#source-container span.word:has-text("das")').first

    # Select both
    den_span.click()
    das_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 2
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1, 2]

    # Deselect 'den' -> 'das' remains, so row 0 stays selected
    den_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 1
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1, 2]
    assert "highlight-orange-active" not in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")

    # Deselect 'das' -> last token deselected, so row 0 is deselected
    das_span.click()
    assert page.evaluate("() => window.AppState.activeTokenSelections.length") == 0
    assert page.evaluate("() => window.getSelectedRowsArray()") == []
    assert "highlight-orange-active" not in (das_span.get_attribute("class") or "")


def test_shared_lemma_multi_token_with_lemma_toggle(page, tmp_path):
    """Verifies that unselected peers display lemma-peer-highlight when Lemma toggle is ON with multiple active tokens (Task 2.2)."""
    raw_html = get_intra_sentence_desk_page_html(tmp_path, highlight_same_lemma=False)
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    den_span = page.locator('#source-container span.word:has-text("den")').first
    das_span = page.locator('#source-container span.word:has-text("das")').first
    die_span = page.locator('#source-container span.word:has-text("die")').first

    # Select 'den' and 'das'
    den_span.click()
    das_span.click()

    # Toggle Lemma ON
    page.locator("#kw-btn-same-lemma").click()
    assert page.evaluate("() => window.AppState.highlightSameLemma") is True

    # 'den' and 'das' active, 'die' is peer highlight
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (die_span.get_attribute("class") or "")

    # Toggle Lemma OFF -> 'die' returns to baseline
    page.locator("#kw-btn-same-lemma").click()
    assert page.evaluate("() => window.AppState.highlightSameLemma") is False
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (die_span.get_attribute("class") or "")


def test_unified_article_pronoun_pos_and_translation_aggregation(page, tmp_path):
    """Verifies that unified der rows aggregate distinct POS tags ('art., pron.') and translations ('тот, который') (Tasks 3.1 & 3.2)."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("settings"):
        config.add_section("settings")
    config.set("settings", "unify_article_pronoun_lemmas", "true")
    config.set("settings", "deduplicate_pos_aware", "true")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    zid = "20261003132144"
    tsv_file = tmp_path / f"{zid}-unified-art-pron.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "der\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt.\tОн начинает сегодня работу, которая ему нравится.\t0\n"
        "die\tder\tpron.\tкоторый\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt.\tОн начинает сегодня работу, которая ему нравится.\t0\n"
        "das\tder\tart.\tтот\t2\tWeil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tПотому что проект должен принести успех, который ему обещали.\t0\n"
        "den\tder\tart.\tтот\t2\tWeil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tПотому что проект должен принести успех, который ему обещали.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    text = "Er fängt heute mit der Arbeit an, die ihm gefällt. Weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."
    raw_html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    # 1. Exactly one unified row for lemma 'der' on Master Overview (Tab 1)
    rows = page.locator("#lemma-table tbody tr[data-sentence-idx='0']")
    assert rows.count() == 1

    # 2. Consolidated POS is 'art., pron.' and tooltip is 'Article, Pronoun'
    pos_cell = rows.first.locator("td.col-pos")
    assert pos_cell.inner_text().strip() == "art., pron."
    assert "Article, Pronoun" in (pos_cell.get_attribute("title") or "")

    # 3. Consolidated translation is 'тот, который'
    trans_cell = rows.first.locator("td.col-translation")
    assert trans_cell.inner_text().strip() == "тот, который"

    # 4. Clicking relative pronoun 'die' in text selects the unified row
    die_span = page.locator('#source-container span.word:has-text("die")').first
    die_span.click()
    assert page.evaluate("() => window.getSelectedRowsArray()") == [0, 1, 2, 3]
    assert "highlight-orange-active" in (die_span.get_attribute("class") or "")


def test_unified_article_only_single_role_preserves_pure_art_pos(page, tmp_path):
    """Verifies that sentences containing exclusively articles retain exact POS 'art.' and primary translation (Task 3.2)."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("settings"):
        config.add_section("settings")
    config.set("settings", "unify_article_pronoun_lemmas", "true")
    config.set("settings", "deduplicate_pos_aware", "true")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    zid = "20261003150502"
    tsv_file = tmp_path / f"{zid}-unified-art-only.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "den\tder\tart.\tтот\t1\tEr sieht den Hund und das Kind.\tОн видит собаку и ребенка.\t0\n"
        "das\tder\tart.\tтот\t2\tEr sieht das Kind und die Katze.\tОн видит ребенка и кошку.\t0\n"
        "die\tder\tart.\tтот\t2\tEr sieht das Kind und die Katze.\tОн видит ребенка и кошку.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    text = "Er sieht den Hund und das Kind. Er sieht das Kind und die Katze."
    raw_html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    rows = page.locator("#lemma-table tbody tr[data-sentence-idx='0']")
    assert rows.count() == 1

    pos_cell = rows.first.locator("td.col-pos")
    assert pos_cell.inner_text().strip() == "art."
    assert pos_cell.get_attribute("title") == "Article"

    trans_cell = rows.first.locator("td.col-translation")
    assert trans_cell.inner_text().strip() == "тот"


def test_active_subtoken_click_does_not_highlight_earlier_duplicate_token(page, tmp_path):
    """Verifies that clicking a duplicate lemma token (second 'der') does not apply solid active highlight to the earlier token (Task 3.1)."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("settings"):
        config.add_section("settings")
    config.set("settings", "unify_article_pronoun_lemmas", "true")
    config.set("settings", "deduplicate_pos_aware", "true")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    zid = "20261003163541"
    tsv_file = tmp_path / f"{zid}-duplicate-token.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "der\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "die\tder\tpron.\tкоторый\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "das\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "den\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "der\tder\tpron.\tкоторый\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    text = "Er fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."
    raw_html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    html = inject_mock_fetch(raw_html)
    page.set_content(html)
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    der_spans = page.locator('#source-container span.word:has-text("der")')
    assert der_spans.count() >= 2
    first_der = der_spans.nth(0)
    second_der = der_spans.nth(1)

    # 1. Click second 'der' in text
    second_der.click()

    # 2. Second 'der' receives active highlight, first 'der' remains non-active
    assert "highlight-orange-active" in (second_der.get_attribute("class") or "")
    assert "highlight-orange-active" not in (first_der.get_attribute("class") or "")

    # 3. Toggle Same Lemma ON -> first 'der' receives lemma-peer-highlight, second remains active
    page.locator("#kw-btn-same-lemma").click()
    assert page.evaluate("() => window.AppState.highlightSameLemma") is True
    assert "highlight-orange-active" in (second_der.get_attribute("class") or "")
    assert "lemma-peer-highlight" in (first_der.get_attribute("class") or "")

    # 4. Toggle Same Lemma OFF -> first 'der' loses peer highlight, second remains active
    page.locator("#kw-btn-same-lemma").click()
    assert page.evaluate("() => window.AppState.highlightSameLemma") is False
    assert "highlight-orange-active" in (second_der.get_attribute("class") or "")
    assert "lemma-peer-highlight" not in (first_der.get_attribute("class") or "")


def test_active_subtoken_persists_across_page_reload(page, tmp_path):
    """Verifies that active subtoken selection is preserved across page reload via sessionStorage (Task 3.2)."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("settings"):
        config.add_section("settings")
    config.set("settings", "unify_article_pronoun_lemmas", "true")
    config.set("settings", "deduplicate_pos_aware", "true")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    zid = "20261003163542"
    tsv_file = tmp_path / f"{zid}-reload-persistence.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "der\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "die\tder\tpron.\tкоторый\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "das\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "den\tder\tart.\tтот\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
        "der\tder\tpron.\tкоторый\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    text = "Er fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."
    raw_html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    html = inject_mock_fetch(raw_html)

    html_file = tmp_path / "page.html"
    html_file.write_text(html, encoding="utf-8")

    page.goto(html_file.as_uri())
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    der_spans = page.locator('#source-container span.word:has-text("der")')
    first_der = der_spans.nth(0)
    second_der = der_spans.nth(1)

    # 1. Click second 'der' and verify active highlight
    second_der.click()
    assert "highlight-orange-active" in (second_der.get_attribute("class") or "")
    assert "highlight-orange-active" not in (first_der.get_attribute("class") or "")

    # 2. Reload page (F5)
    page.reload()
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    der_spans_after = page.locator('#source-container span.word:has-text("der")')
    first_der_after = der_spans_after.nth(0)
    second_der_after = der_spans_after.nth(1)

    # 3. Verify second 'der' retained active highlight across reload
    assert "highlight-orange-active" in (second_der_after.get_attribute("class") or "")
    assert "highlight-orange-active" not in (first_der_after.get_attribute("class") or "")

    # 4. Now click 'das' and reload again
    das_span = page.locator('#source-container span.word:has-text("das")').first
    das_span.click()
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")

    page.reload()
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    das_span_after = page.locator('#source-container span.word:has-text("das")').first
    first_der_after2 = page.locator('#source-container span.word:has-text("der")').nth(0)
    assert "highlight-orange-active" in (das_span_after.get_attribute("class") or "")
    assert "highlight-orange-active" not in (first_der_after2.get_attribute("class") or "")


def test_table_row_click_highlights_all_der_occurrences(page, tmp_path):
    """Verifies that selecting 'der' in the lemma table highlights all 'der' occurrences in active orange (20261004001104)."""
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    if not config.has_section("rendering"):
        config.add_section("rendering")
    config.set("rendering", "highlight_same_lemma", "false")
    if not config.has_section("settings"):
        config.add_section("settings")
    config.set("settings", "unify_article_pronoun_lemmas", "true")
    config.set("settings", "deduplicate_pos_aware", "true")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    zid = "20261004001105"
    tsv_file = tmp_path / f"{zid}-table-der.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "das, den, der, die\tder\tart., pron.\tтот, который\t1\tEr fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde.\tОн начинает сегодня с работы, которая ему нравится, потому что проект должен принести успех, который ему обещали.\t0\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    text = "Er fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."
    raw_html = kardenwort_desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False}
    )
    html = inject_mock_fetch(raw_html)

    html_file = tmp_path / "page_table_der.html"
    html_file.write_text(html, encoding="utf-8")

    page.goto(html_file.as_uri())
    page.wait_for_selector("#source-container span.word")
    page.wait_for_selector("#lemma-table tbody tr")

    der_spans = page.locator('#source-container span.word:has-text("der")')
    first_der = der_spans.nth(0)
    second_der = der_spans.nth(1)

    table_row = page.locator("#lemma-table tbody tr").first

    # 1. Click table row for 'der'
    table_row.click()

    # 2. Both 'der' tokens AND all inflected row constituents (die, das, den) must be highlighted in active yellow (highlight-orange-active)
    die_span = page.locator('#source-container span.word:has-text("die")').first
    das_span = page.locator('#source-container span.word:has-text("das")').first
    den_span = page.locator('#source-container span.word:has-text("den")').first

    assert "highlight-orange-active" in (first_der.get_attribute("class") or "")
    assert "highlight-orange-active" in (second_der.get_attribute("class") or "")
    assert "highlight-orange-active" in (die_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (das_span.get_attribute("class") or "")
    assert "highlight-orange-active" in (den_span.get_attribute("class") or "")


def test_frontend_pos_display_order_injected(page, tmp_path):
    """Verifies that POS_DISPLAY_ORDER_MAP is injected from Python constants into the desk JS scope."""
    raw_html = get_desk_page_html(tmp_path)
    html = inject_mock_fetch(raw_html)
    html_file = tmp_path / "page_pos_order.html"
    html_file.write_text(html, encoding="utf-8")

    page.goto(html_file.as_uri())
    page.wait_for_selector("#source-container span.word")

    pos_order = page.evaluate("() => window.POS_DISPLAY_ORDER_MAP")
    assert pos_order == {"art.": 0, "pron.": 1, "det.": 2, "prep.": 3}


def test_frontend_row_group_helpers(page, tmp_path):
    """Verifies window.getRowGroupIds and window.getExtraRowIds parsing and deduplication."""
    raw_html = get_desk_page_html(tmp_path)
    html = inject_mock_fetch(raw_html)
    html_file = tmp_path / "page_row_helpers.html"
    html_file.write_text(html, encoding="utf-8")

    page.goto(html_file.as_uri())
    page.wait_for_selector("#source-container span.word")

    result = page.evaluate("""() => {
        var tr1 = document.createElement('tr');
        tr1.setAttribute('data-row-id', '10');

        var tr2 = document.createElement('tr');
        tr2.setAttribute('data-row-id', '20');
        tr2.setAttribute('data-all-row-ids', '20, 21, 22');

        var tr3 = document.createElement('tr');
        tr3.setAttribute('data-row-id', '30');
        tr3.setAttribute('data-all-row-ids', ' 30,  31 , 31, 32  ');

        return {
            nullTr: window.getRowGroupIds(null),
            tr1All: window.getRowGroupIds(tr1),
            tr1Extra: window.getExtraRowIds(tr1),
            tr2All: window.getRowGroupIds(tr2),
            tr2Extra: window.getExtraRowIds(tr2),
            tr3All: window.getRowGroupIds(tr3),
            tr3Extra: window.getExtraRowIds(tr3),
        };
    }""")

    assert result["nullTr"] == []
    assert result["tr1All"] == ["10"]
    assert result["tr1Extra"] == []
    assert result["tr2All"] == ["20", "21", "22"]
    assert result["tr2Extra"] == ["21", "22"]
    assert result["tr3All"] == ["30", "31", "32"]
    assert result["tr3Extra"] == ["31", "32"]


def test_frontend_is_legacy_overview_context(page, tmp_path):
    """Verifies window.isLegacyOverviewContext correctly identifies legacy overview cards (< 20261002000000)."""
    raw_html = get_desk_page_html(tmp_path)
    html = inject_mock_fetch(raw_html)
    html_file = tmp_path / "page_legacy_overview.html"
    html_file.write_text(html, encoding="utf-8")

    page.goto(html_file.as_uri())
    page.wait_for_selector("#source-container span.word")

    result = page.evaluate("""() => {
        var origTabs = window.WorkspaceTabs;
        var r = {};

        // Case 1: not overview tab (index 1)
        window.WorkspaceTabs = { getActiveCard: () => ({ index: 1, zid: '20261001120000' }) };
        r.nonOverview = window.isLegacyOverviewContext();

        // Case 2: overview tab, old zid (< 20261002000000)
        window.WorkspaceTabs = { getActiveCard: () => ({ index: 0, zid: '20261001120000' }) };
        r.legacyOverview = window.isLegacyOverviewContext();

        // Case 3: overview tab, new zid (>= 20261002000000)
        window.WorkspaceTabs = { getActiveCard: () => ({ index: 0, zid: '20261002000000' }) };
        r.modernOverviewExact = window.isLegacyOverviewContext();

        window.WorkspaceTabs = { getActiveCard: () => ({ index: 0, zid: '20261003153000' }) };
        r.modernOverviewLater = window.isLegacyOverviewContext();

        // Case 4: No WorkspaceTabs, fallback to SESSION_ZID / __CONFIG__
        window.WorkspaceTabs = null;
        r.noTabs = window.isLegacyOverviewContext();

        window.WorkspaceTabs = origTabs;
        return r;
    }""")

    assert result["nonOverview"] is False
    assert result["legacyOverview"] is True
    assert result["modernOverviewExact"] is False
    assert result["modernOverviewLater"] is False
    assert result["noTabs"] is False


def test_frontend_decide_token_click_action(page, tmp_path):
    """Verifies window.decideTokenClickAction decision matrix for both active selection and idle states."""
    raw_html = get_desk_page_html(tmp_path)
    html = inject_mock_fetch(raw_html)
    html_file = tmp_path / "page_click_action.html"
    html_file.write_text(html, encoding="utf-8")

    page.goto(html_file.as_uri())
    page.wait_for_selector("#source-container span.word")

    result = page.evaluate("""() => {
        var r = {};

        // When hasActiveTokens = true:
        r.active_inSelections = window.decideTokenClickAction({
            hasActiveTokens: true,
            isInActiveSelections: true,
            isTokenPinned: false,
            isRowSelected: false,
            isRowBackedByActiveToken: false,
            isOrphanSelected: false
        });
        r.active_pinned = window.decideTokenClickAction({
            hasActiveTokens: true,
            isInActiveSelections: false,
            isTokenPinned: true,
            isRowSelected: false,
            isRowBackedByActiveToken: false,
            isOrphanSelected: false
        });
        r.active_tableSelectedToken = window.decideTokenClickAction({
            hasActiveTokens: true,
            isInActiveSelections: false,
            isTokenPinned: false,
            isRowSelected: true,
            isRowBackedByActiveToken: false,
            isOrphanSelected: false
        });
        r.active_orphanSelected = window.decideTokenClickAction({
            hasActiveTokens: true,
            isInActiveSelections: false,
            isTokenPinned: false,
            isRowSelected: false,
            isRowBackedByActiveToken: false,
            isOrphanSelected: true
        });
        r.active_rowBackedByActiveToken_notInSelections = window.decideTokenClickAction({
            hasActiveTokens: true,
            isInActiveSelections: false,
            isTokenPinned: false,
            isRowSelected: true,
            isRowBackedByActiveToken: true,
            isOrphanSelected: false
        });
        r.active_unselectedToken = window.decideTokenClickAction({
            hasActiveTokens: true,
            isInActiveSelections: false,
            isTokenPinned: false,
            isRowSelected: false,
            isRowBackedByActiveToken: false,
            isOrphanSelected: false
        });

        // When hasActiveTokens = false:
        r.idle_visuallyActive = window.decideTokenClickAction({
            hasActiveTokens: false,
            isVisuallyActive: true,
            isRowSelected: false,
            isLemmaSelected: false,
            isInActiveSelections: false,
            isTokenPinned: false,
            isOrphanSelected: false
        });
        r.idle_rowSelected = window.decideTokenClickAction({
            hasActiveTokens: false,
            isVisuallyActive: false,
            isRowSelected: true,
            isLemmaSelected: false,
            isInActiveSelections: false,
            isTokenPinned: false,
            isOrphanSelected: false
        });
        r.idle_lemmaSelected = window.decideTokenClickAction({
            hasActiveTokens: false,
            isVisuallyActive: false,
            isRowSelected: false,
            isLemmaSelected: true,
            isInActiveSelections: false,
            isTokenPinned: false,
            isOrphanSelected: false
        });
        r.idle_inactive = window.decideTokenClickAction({
            hasActiveTokens: false,
            isVisuallyActive: false,
            isRowSelected: false,
            isLemmaSelected: false,
            isInActiveSelections: false,
            isTokenPinned: false,
            isOrphanSelected: false
        });

        return r;
    }""")

    # Active tokens state
    assert result["active_inSelections"]["shouldDeselect"] is True
    assert result["active_pinned"]["shouldDeselect"] is True
    assert result["active_tableSelectedToken"]["shouldDeselect"] is True
    assert result["active_tableSelectedToken"]["isTableSelectedToken"] is True
    assert result["active_orphanSelected"]["shouldDeselect"] is True
    assert result["active_rowBackedByActiveToken_notInSelections"]["shouldDeselect"] is False
    assert result["active_unselectedToken"]["shouldDeselect"] is False

    # Idle state
    assert result["idle_visuallyActive"]["shouldDeselect"] is True
    assert result["idle_rowSelected"]["shouldDeselect"] is True
    assert result["idle_lemmaSelected"]["shouldDeselect"] is True
    assert result["idle_inactive"]["shouldDeselect"] is False

