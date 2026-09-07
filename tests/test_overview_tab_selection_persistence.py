import pytest
import json
import re
from pathlib import Path
import kardenwort_desk
from kardenwort_db import KardenwortDB


def test_overview_tab_selection_and_f5_restoration(page, tmp_path):
    """
    Task 4.1: End-to-end test verifying Tab 1 row selection, saving via delta API,
    and restoration upon reload / F5.
    """
    db_path = tmp_path / "test_tab1_persist.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    unique_zid = "20260907043001"
    text = "The quick brown fox. Jumps over the lazy dog."
    tsv_file = tmp_path / f"{unique_zid}.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\tTokenOrder\n"
        "The\tthe\t\t1\t0\t0\n"
        "quick\tquick\t\t1\t0\t1\n"
        "fox\tfox\t\t1\t0\t2\n"
        "Jumps\tjump\t\t2\t0\t3\n"
        "dog\tdog\t\t2\t0\t4\n",
        encoding="utf-8"
    )

    adapter = kardenwort_desk.get_storage_adapter(config, resolved_paths)
    adapter.save_session(
        session_zid=unique_zid,
        slug="test-overview-persist",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text=text,
        headers=["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "DeskSelected", "TokenOrder"],
        data_rows=[
            ["The", "the", "The", "1", "0", "0"],
            ["quick", "quick", "quick", "1", "0", "1"],
            ["fox", "fox", "fox", "1", "0", "2"],
            ["Jumps", "jump", "jump", "2", "0", "3"],
            ["dog", "dog", "dog", "2", "0", "4"],
        ],
        zid=unique_zid,
    )

    # 1. Initial render - Tab 1 has no selections
    html1 = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid=unique_zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    cards_match = re.search(r'<script id="sentence-cards" type="application/json">\s*([\s\S]*?)\s*</script>', html1)
    assert cards_match is not None
    cards1 = json.loads(cards_match.group(1))
    assert cards1[0]["selected_ids"] == []

    # 2. Simulate selecting row 0 on Tab 1 (sentence_idx = 0) and saving via delta update
    ok = adapter.update_word_selection(unique_zid, sentence_idx=0, token_order=0, selected=1)
    assert ok is True
    assert adapter.get_overview_selections(unique_zid) == {0}

    # 3. Simulate reload (F5) - render flow queries adapter for saved overview selections
    html2 = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid=unique_zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    cards_match2 = re.search(r'<script id="sentence-cards" type="application/json">\s*([\s\S]*?)\s*</script>', html2)
    assert cards_match2 is not None
    cards2 = json.loads(cards_match2.group(1))

    # Tab 1 master_card MUST restore selected_ids with row 0
    assert "0" in cards2[0]["selected_ids"]
    assert cards2[0]["words"][0]["selected"] == "1"
    assert "selected" in cards2[0]["words"][0]["highlight_class"]

    # 4. Verify in Playwright DOM that row 0 renders as selected
    page.set_content(html2)
    page.wait_for_selector("#lemma-table")
    row0 = page.locator('#lemma-table tbody tr[data-token-order="0"]')
    assert row0.get_attribute("data-selected") == "1"
    assert "kw-row-selected" in (row0.get_attribute("class") or "")


def test_bidirectional_selection_isolation(page, tmp_path):
    """
    Task 4.2: End-to-end test verifying strict bidirectional selection isolation
    between Tab 1 (sentence_idx=0) and constituent sentence tabs (Tabs 2..N).
    """
    db_path = tmp_path / "test_tab_iso.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "spawn_order", "normal")
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    unique_zid = "20260907043002"
    text = "First sentence here. Second sentence follows."
    tsv_file = tmp_path / f"{unique_zid}.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\tTokenOrder\n"
        "First\tfirst\t\t1\t0\t0\n"
        "here\there\t\t1\t0\t1\n"
        "Second\tsecond\t\t2\t0\t2\n"
        "follows\tfollow\t\t2\t0\t3\n",
        encoding="utf-8"
    )

    adapter = kardenwort_desk.get_storage_adapter(config, resolved_paths)
    adapter.save_session(
        session_zid=unique_zid,
        slug="test-isolation",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text=text,
        headers=["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "DeskSelected", "TokenOrder"],
        data_rows=[
            ["First", "first", "First", "1", "0", "0"],
            ["here", "here", "here", "1", "0", "1"],
            ["Second", "second", "Second", "2", "0", "2"],
            ["follows", "follow", "follows", "2", "0", "3"],
        ],
        zid=unique_zid,
    )

    # Select token 0 on Tab 1 (sentence_idx = 0)
    adapter.update_word_selection(unique_zid, sentence_idx=0, token_order=0, selected=1)

    # Select token 2 on Tab 3 (Sentence 2, sentence_idx = 2)
    adapter.update_word_selection(unique_zid, sentence_idx=2, token_order=2, selected=1)

    # Render container view
    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid=unique_zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    cards_match = re.search(r'<script id="sentence-cards" type="application/json">\s*([\s\S]*?)\s*</script>', html)
    assert cards_match is not None
    cards = json.loads(cards_match.group(1))

    # Tab 1 (Overview, sentence_idx=0) has token 0 selected, but NOT token 2
    assert "0" in cards[0]["selected_ids"]
    assert "2" not in cards[0]["selected_ids"]

    # Tab 2 (Sentence 1, sentence_idx=1) has NO selections (token 0 was selected on Tab 1, not Tab 2)
    assert cards[1]["selected_ids"] == []

    # Tab 3 (Sentence 2, sentence_idx=2) has token 2 selected, but NOT token 0
    assert "2" in cards[2]["selected_ids"]
    assert "0" not in cards[2]["selected_ids"]

    # In database: words table has selected=1 only for token_order 2
    words = adapter.db.get_words_by_session(unique_zid)
    sent_words_selected = {w["token_order"]: w["selected"] for w in words}
    assert sent_words_selected[0] == 0  # Tab 1 selection did NOT bleed into words table
    assert sent_words_selected[2] == 1  # Tab 3 selection preserved

    # overview_selections table has only token_order 0
    assert adapter.get_overview_selections(unique_zid) == {0}


def test_ctrl_s_single_execution_and_clean_hotkey(page, tmp_path):
    """
    Task 4.3: Test verifying single-execution Ctrl+S hotkey behavior without
    duplicate keydown events or spurious secondary notifications.
    """
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("sentences_mode", "enabled", "true")

    unique_zid = "20260907043003"
    text = "Alpha beta gamma."
    tsv_file = tmp_path / f"{unique_zid}.en.tsv"
    tsv_file.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tDeskSelected\n"
        "Alpha\talpha\t\t1\t0\n",
        encoding="utf-8"
    )

    html = kardenwort_desk.run_render_flow(
        text=text,
        language="en",
        zid=unique_zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False,
        seq_num=1
    )

    page.set_content(html)
    page.wait_for_selector("#lemma-table")

    # Count how many times onSaveClick is invoked on Ctrl+S
    invocations = page.evaluate("""
        () => {
            let count = 0;
            const origSave = window.onSaveClick;
            window.onSaveClick = function() {
                count++;
                if (origSave) origSave.apply(this, arguments);
            };
            window.__getSaveCount = () => count;
        }
    """)

    # Press Ctrl+S
    page.keyboard.press("Control+s")

    save_count = page.evaluate("() => window.__getSaveCount()")
    assert save_count == 1, f"Ctrl+S must trigger onSaveClick exactly once, got {save_count}"


def test_anki_card_generation_integrity_preserved(tmp_path):
    """
    Task 4.4: Verify Anki card generation, restore_session, and wordfill integrity
    remain unaffected by overview_selections persistence.
    """
    db_path = tmp_path / "test_anki_integrity.db"
    KardenwortDB(db_path=db_path).run_migrations()

    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if not config.has_section("storage"):
        config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_path))
    resolved_paths["sqlite_db_path"] = str(db_path)

    adapter = kardenwort_desk.get_storage_adapter(config, resolved_paths)
    session_zid = "20260907043004"

    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "DeskSelected", "TokenOrder"]
    data_rows = [
        ["one", "one", "один", "1", "1", "0"],
        ["two", "two", "два", "1", "0", "1"],
        ["three", "three", "три", "2", "1", "2"],
    ]
    adapter.save_session(
        session_zid=session_zid,
        slug="test-anki",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="One two. Three.",
        headers=headers,
        data_rows=data_rows,
        zid=session_zid,
    )

    # Persist overview selections
    adapter.update_word_selection(session_zid, sentence_idx=0, token_order=0, selected=1)
    adapter.update_word_selection(session_zid, sentence_idx=0, token_order=1, selected=1)
    assert adapter.get_overview_selections(session_zid) == {0, 1}

    # restore_session must strictly return 3 constituent rows, not 5
    restored = adapter.restore_session(session_zid)
    assert len(restored["data_rows"]) == 3
    assert len(restored["words"]) == 3

    # Anki cards correspond to selected=1 in constituent sentences (rows 0 and 2)
    rest_headers = restored["headers"]
    sel_idx = rest_headers.index("DeskSelected")
    quot_idx = rest_headers.index("Quotation")
    selected_for_anki = [r for r in restored["data_rows"] if str(r[sel_idx]).strip() in ("1", "true")]
    assert len(selected_for_anki) == 2
    assert selected_for_anki[0][quot_idx] == "one"
    assert selected_for_anki[1][quot_idx] == "three"

