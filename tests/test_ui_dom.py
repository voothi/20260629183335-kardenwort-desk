import pytest
from pathlib import Path
import json
import kardenwort_desk

def test_playwright_dom_validation(page, tmp_path):
    tsv_path = tmp_path / "test.tsv"
    
    # 2.2 Generate HTML
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    data_rows = [["Haus", "дом", "", ""]]
    
    html = kardenwort_desk.render_lookup_html(
        text="Das Haus", 
        language="de", 
        target_lang="ru", 
        config=config, 
        resolved_paths=resolved_paths, 
        zid="000", 
        goldendict=goldendict, 
        comments=[], 
        headers=headers, 
        data_rows=data_rows, 
        sentence_translation="The house"
    )
    
    html = html.replace("Das Haus", '<span class="word highlight-purple" data-word-idx="0">Das</span> <span class="word highlight-orange" data-word-idx="1">Haus</span>')
    
    # Ensure it's valid HTML
    assert "<html>" in html
    
    # 2.3 Load HTML into page
    page.set_content(html)
    
    # Let's mock a payload and apply it
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}
    kardenwort_desk.write_update_js(
        tsv_path,
        data_rows,
        headers,
        role_fields,
        stage="finished",
        source_text='<span class="word highlight-purple" data-word-idx="0">Das</span> <span class="word highlight-orange" data-word-idx="1">Haus</span>',
        translated_text="The house"
    )
    
    # 2.6 Verify pathlib usage (already doing it here via tsv_path.parent)
    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1, "Expected exactly 1 JS payload file to be created"
    
    # 2.4 Evaluate JS
    js_content = js_files[0].read_text(encoding="utf-8")
    page.evaluate(js_content)
    
    # Wait for any potential async renders (though it should be sync)
    page.wait_for_selector(".highlight-purple")
    
    # 2.5 Assert DOM structure preserved
    purple_spans = page.locator(".highlight-purple").count()
    assert purple_spans >= 1, "The <span class=\"highlight-purple\"> elements should be preserved after update"
    
    orange_spans = page.locator(".highlight-orange").count()
    assert orange_spans >= 1, "The <span class=\"highlight-orange\"> elements should be preserved after update"
    
    # Verify the table was updated with the payload
    assert page.locator("td:has-text('дом')").is_visible(), "Table should be updated with new destination word"
