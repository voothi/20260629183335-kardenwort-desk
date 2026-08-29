import configparser
from pathlib import Path
import pytest
from kardenwort_db import KardenwortDB
from kardenwort_desk import SqliteStorageAdapter, SEC_LANGUAGES, SEC_STORAGE, render_lookup_html

def test_sqlite_storage_preserves_multi_sentences_when_saved_without_sentences_arg(tmp_path):
    db_path = tmp_path / "kardenwort.db"
    cfg = configparser.ConfigParser()
    cfg.add_section(SEC_LANGUAGES)
    cfg.add_section("storage")
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(db_path))

    resolved_paths = {
        "sqlite_db_path": str(db_path),
        "kardenwort_workspace": str(tmp_path),
        "anki_mapping_file": str(tmp_path / "anki-mapping.ini"),
    }

    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    adapter = SqliteStorageAdapter(config=cfg, resolved_paths=resolved_paths)
    session_zid = "20260829030522"

    initial_sentences = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "sentence_source": "Omarchy is installed using an ISO.",
            "sentence_destination": "Omarchy устанавливается с использованием ISO.",
        },
        {
            "session_zid": session_zid,
            "sentence_index": 2,
            "sentence_source": "You can choose between full-disk or free-space.",
            "sentence_destination": "Вы можете выбрать между полнодисковой установкой или свободным пространством.",
        },
    ]

    headers = ["WordSource", "WordSourceInflectedForm", "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "WordDestination"]
    data_rows = [
        ["install", "installed", "1", "Full holistic text", "Full holistic translation for all sentences together.", ""],
        ["choose", "choose", "2", "Full holistic text", "Full holistic translation for all sentences together.", ""],
    ]

    # Initial save with structured sentences
    adapter.save_session(
        session_zid=session_zid,
        slug="test-omarchy",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Omarchy is installed using an ISO. You can choose between full-disk or free-space.",
        headers=headers,
        data_rows=data_rows,
        sentences=initial_sentences,
        zid=session_zid,
    )

    # Verify initial sentences saved
    sents = db.get_sentences_by_session(session_zid)
    assert len(sents) == 2
    assert sents[0]["sentence_destination"] == "Omarchy устанавливается с использованием ISO."
    assert sents[1]["sentence_destination"] == "Вы можете выбрать между полнодисковой установкой или свободным пространством."

    # Now simulate a subsequent save_session call (e.g. from render or wordfill) with updated data_rows and sentences=None
    updated_rows = [
        ["install", "installed", "1", "Full holistic text", "Full holistic translation for all sentences together.", "установить"],
        ["choose", "choose", "2", "Full holistic text", "Full holistic translation for all sentences together.", "выбирать"],
    ]

    adapter.save_session(
        session_zid=session_zid,
        headers=headers,
        data_rows=updated_rows,
        zid=session_zid,
    )

    # Verify existing structured sentence destinations were NOT overwritten by the holistic data_rows[col_sent_dst]
    sents_after = db.get_sentences_by_session(session_zid)
    assert len(sents_after) == 2
    assert sents_after[0]["sentence_destination"] == "Omarchy устанавливается с использованием ISO."
    assert sents_after[1]["sentence_destination"] == "Вы можете выбрать между полнодисковой установкой или свободным пространством."

def test_render_flow_multi_sentence_sqlite_tab_isolation(tmp_path):
    import json
    import kardenwort_desk

    cfg, resolved_paths, _, _ = kardenwort_desk.load_config()
    db_path = tmp_path / "kardenwort.db"
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(db_path))
    cfg.set("sentences_mode", "enabled", "true")
    cfg.set("sentences_mode", "delivery_mode", "container")
    cfg.set("sentences_mode", "min_sentences", "2")
    cfg.set("settings", "text_mode", "single")
    cfg.set("settings", "default_target_language", "ru")

    resolved_paths["sqlite_db_path"] = str(db_path)

    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    adapter = SqliteStorageAdapter(config=cfg, resolved_paths=resolved_paths)
    session_zid = "20260829030522"

    initial_sentences = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "sentence_source": "Omarchy is installed using an ISO.",
            "sentence_destination": "Omarchy устанавливается с использованием ISO.",
        },
        {
            "session_zid": session_zid,
            "sentence_index": 2,
            "sentence_source": "You can choose between full-disk or free-space.",
            "sentence_destination": "Вы можете выбрать между полнодисковой установкой или свободным пространством.",
        },
    ]

    headers = ["WordSource", "WordSourceInflectedForm", "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "WordDestination", "DeskSelected"]
    data_rows = [
        ["install", "installed", "1", "Omarchy is installed using an ISO.", "Omarchy устанавливается с использованием ISO.", "установить", "1"],
        ["choose", "choose", "2", "You can choose between full-disk or free-space.", "Вы можете выбрать между полнодисковой установкой или свободным пространством.", "выбирать", "1"],
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="omarchy-install",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Omarchy is installed using an ISO. You can choose between full-disk or free-space.",
        headers=headers,
        data_rows=data_rows,
        sentences=initial_sentences,
        zid=session_zid,
    )

    html = kardenwort_desk.run_render_flow(
        text="Omarchy is installed using an ISO. You can choose between full-disk or free-space.",
        language="en",
        zid=session_zid,
        text_mode="single",
        config=cfg,
        resolved_paths=resolved_paths,
        spawn_children=False,
        return_children=False,
        seq_num=2,
    )

    # Verify sentence_cards are generated cleanly without paragraph duplication
    cards_match = None
    for line in html.splitlines():
        if 'id="sentence-cards"' in line:
            cards_json = html.split('<script id="sentence-cards" type="application/json">\n')[1].split('\n</script>')[0]
            cards = json.loads(cards_json)
            cards_match = cards
            break

    assert cards_match is not None
    assert len(cards_match) == 3  # Master + 2 child cards
    card_sent1 = [c for c in cards_match if c["sentence_idx"] == 1][0]
    card_sent2 = [c for c in cards_match if c["sentence_idx"] == 2][0]
    assert "Omarchy" in card_sent1["translated_text"]
    assert "Вы можете выбрать" in card_sent2["translated_text"]

