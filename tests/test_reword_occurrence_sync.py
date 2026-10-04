import pytest
import kardenwort_desk
from kardenwort_desk import SEC_RENDERING


def test_reword_delta_preserves_occurrence_filtered_view(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    raw_sentence = (
        "Er fängt heute mit der Arbeit an, die ihm gefällt, "
        "weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."
    )
    tsv_content = (
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tWordSourceMorphologyAI\tSentenceSourceIndex\n"
        "das, den, der, die\tder\tart., pron.\tтот, который\tder (определенный артикль); der (относительное местоимение)\t1\n"
    )
    tsv_file = tmp_path / "20261004160000-test.de.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html_out = kardenwort_desk.run_render_flow(
        text=raw_sentence,
        language="de",
        zid="20261004160000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
    )

    page.set_content(html_out)
    page.wait_for_timeout(150)

    # 1. Initially, row 0 has data-occ and displays the full set if nothing selected
    inf_cell = page.locator("tr[data-row-id='0'] td.col-inflected")
    assert "das, den, der, die" in inf_cell.inner_text()

    # 2. Select tokens 'das' (visual 25) and 'der' (visual 38)
    page.evaluate("""() => {
        window.AppState.activeTokenSelections = [
            { visual_idx: 25, word: 'das' },
            { visual_idx: 38, word: 'der' }
        ];
        window.applyOccurrenceView();
    }""")
    page.wait_for_timeout(50)

    # Table should now show occurrence-filtered text "das, der"
    assert inf_cell.inner_text().strip() == "das, der"

    # 3. Simulate server returning a reword delta containing the unrolled lemma inflected string
    page.evaluate("""() => {
        window.receiveUpdate({
            ok: true,
            status: 'success',
            rows: {
                "0": {
                    lemma: "der",
                    inflected: "das, den, der, die",
                    trans: "тот самый, который именно",
                    morphology: "der (обновленный артикль)",
                    token_order: "0"
                }
            }
        });
    }""")
    page.wait_for_timeout(50)

    # The visible cell text must STILL be the occurrence-filtered subset "das, der"
    assert inf_cell.inner_text().strip() == "das, der"

    # But the baseline attribute data-orig-html must have been updated with the server delta
    data_orig_html = inf_cell.get_attribute("data-orig-html")
    assert "das, den, der, die" in data_orig_html

    # 4. Deselect all tokens -> restores the freshly updated baseline
    page.evaluate("""() => {
        window.AppState.activeTokenSelections = [];
        window.applyOccurrenceView();
    }""")
    page.wait_for_timeout(50)

    assert inf_cell.inner_text().strip() == "das, den, der, die"


def test_reword_delta_preserves_active_bookmarks(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if not config.has_section(SEC_RENDERING):
        config.add_section(SEC_RENDERING)
    config.set(SEC_RENDERING, "hover_highlight", "true")
    config.set(SEC_RENDERING, "hover_highlight_bookmarks", "4")
    config.set(SEC_RENDERING, "hover_highlight_rainbow", "true")

    raw_sentence = "Das Auto fährt schnell."
    tsv_content = (
        "Quotation\tWordSource\tWordSourcePOS\tWordDestination\tWordSourceMorphologyAI\tSentenceSourceIndex\n"
        "Auto\tAuto\tn.\tмашина\tAuto (сущ.)\t1\n"
    )
    tsv_file = tmp_path / "20261004160001-test.de.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html_out = kardenwort_desk.run_render_flow(
        text=raw_sentence,
        language="de",
        zid="20261004160001",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
    )

    page.set_content(html_out)
    page.wait_for_timeout(150)

    first_src = page.locator("#source-container span.word").first
    first_src.click()
    page.wait_for_timeout(50)

    # Pin a rainbow slot on first word
    assert "hl-mvp-pin" in (first_src.get_attribute("class") or "")
    assert "hl-mvp-pin-0" in (first_src.get_attribute("class") or "")

    # Apply row delta
    page.evaluate("""() => {
        window.receiveUpdate({
            ok: true,
            status: 'success',
            rows: {
                "0": {
                    lemma: "Auto",
                    trans: "автомобиль",
                    token_order: "0"
                }
            }
        });
    }""")
    page.wait_for_timeout(50)

    # Bookmarks should remain active and visible on first_src
    assert "hl-mvp-pin" in (first_src.get_attribute("class") or "")
    assert "hl-mvp-pin-0" in (first_src.get_attribute("class") or "")
