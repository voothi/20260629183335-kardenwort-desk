"""
Unit tests for Clean Translation View Context Isolation.
Verifies that sentence context padding (anki_context_words_before/after)
remains strictly contained in tabular Anki card fields (data_rows / SentenceDestination),
while the UI Translation view and sentences table (sentence_destination) present clean,
unpadded 1:1 sentence translations across initial render and progressive worker updates.
"""

import configparser
import json
import pytest
from pathlib import Path
from unittest.mock import patch

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB


@pytest.fixture
def mock_clean_context_env(tmp_path):
    """Sets up a clean test environment with SQLite storage and context padding enabled."""
    db_path = tmp_path / "kardenwort.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = tmp_path / "anki-mapping.ini"
    
    mapping_content = """
[fields]
Quotation = 1
WordSource = 2
WordDestination = 6
SentenceSource = 10
SentenceDestination = 13
SentenceDestination2 = 16
SentenceSourceIndex = 81
DeskSelected = 84

[desk_columns]
SentenceDestination = sentence_destination
SentenceDestination2 = sentence_destination2
SentenceSourceIndex = sentence_index
"""
    mapping_path.write_text(mapping_content, encoding="utf-8")
    
    config = configparser.ConfigParser()
    config.add_section("settings")
    config.set("settings", "storage_backend", "sqlite")
    config.set("settings", "anki_context_mode", "single")
    config.set("settings", "anki_context_words_before", "5")
    config.set("settings", "anki_context_words_after", "5")
    config.set("settings", "anki_translated_context_words_before", "5")
    config.set("settings", "anki_translated_context_words_after", "5")
    config.set("settings", "normalize_bracket_spacing", "true")
    config.set("settings", "default_language", "de")
    config.set("settings", "default_target_language", "en")
    
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_path))
    
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "google")
    config.set("pipeline", "lemma_base_provider", "google")
    
    config.add_section("triggers")
    config.set("triggers", "run_text_translation", "auto")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.set("triggers", "run_lemma_enrichment", "auto")
    
    resolved_paths = {
        "anki_mapping_file": str(mapping_path),
        "results_dir": str(results_dir),
        "sqlite_db_path": str(db_path),
        "kardenwort_workspace": tmp_path,
    }
    
    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "results_dir": results_dir,
        "config": config,
        "resolved_paths": resolved_paths,
    }


def test_write_update_js_resolves_clean_translation_from_sqlite(mock_clean_context_env):
    """Verifies write_update_js prioritizes SQLite clean sentence_destination over padded data_rows."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]
    
    adapter = desk.get_storage_adapter(config, resolved_paths)
    session_zid = "20260822190001"
    
    headers = [
        "Quotation", "WordSource", "WordDestination", "SentenceSource",
        "SentenceDestination", "SentenceDestination2", "SentenceSourceIndex", "DeskSelected"
    ]
    # Padded data rows containing context padding
    data_rows = [
        ["", "Haus", "house", "Vorwort Das Haus Nachwort", "Padded Before This is the house Padded After", "", "1", "1"]
    ]
    
    # Store session with clean sentence in DB, but padded row in data_rows
    adapter.save_session(
        session_zid=session_zid,
        slug="test-house",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Das Haus.",
        headers=headers,
        data_rows=data_rows,
        sentences=[{
            "session_zid": session_zid,
            "sentence_index": 1,
            "sentence_source": "Das Haus.",
            "sentence_destination": "This is the house.",
            "sentence_destination2": "Padded Before This is the house Padded After",
        }],
        zid=session_zid,
    )
    
    tsv_path = env["results_dir"] / f"{session_zid}-test-house.de.tsv"
    role_fields = {
        "sentence_destination": "SentenceDestination",
        "sentence_index": "SentenceSourceIndex",
        "lemma": "WordSource",
        "word_translation": "WordDestination",
    }
    
    # Progressive stage 'finished' with translated_text=None
    desk.write_update_js(
        tsv_path=tsv_path,
        data_rows=data_rows,
        headers=headers,
        role_fields=role_fields,
        stage="finished",
        config=config,
        zid=session_zid,
    )
    
    updates_dir = env["results_dir"] / f"{tsv_path.stem}.updates"
    update_files = sorted(updates_dir.glob("*.js"))
    assert len(update_files) > 0
    
    latest_js = update_files[-1].read_text(encoding="utf-8")
    assert "window.receiveUpdate(" in latest_js
    
    payload_str = latest_js[len("if (typeof window.receiveUpdate === 'function') { window.receiveUpdate("):-4]
    payload = json.loads(payload_str)
    
    # The translatedText MUST be the clean sentence translation, NOT the padded string
    assert "This is the house." in payload["translatedText"]
    assert "Padded Before" not in payload["translatedText"]
    assert "Padded After" not in payload["translatedText"]


def test_render_flow_uses_clean_translation(mock_clean_context_env):
    """Verifies _run_render_flow_impl outputs clean translation in UI HTML and ignores context padding."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]
    
    adapter = desk.get_storage_adapter(config, resolved_paths)
    session_zid = "20260822190002"
    
    headers = [
        "Quotation", "WordSource", "WordDestination", "SentenceSource",
        "SentenceDestination", "SentenceDestination2", "SentenceSourceIndex", "DeskSelected"
    ]
    data_rows = [
        ["", "Buch", "book", "Vorkontext Das Buch Nachkontext", "ContextLeft The book ContextRight", "", "1", "1"]
    ]
    
    adapter.save_session(
        session_zid=session_zid,
        slug="test-book",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Das Buch.",
        headers=headers,
        data_rows=data_rows,
        sentences=[{
            "session_zid": session_zid,
            "sentence_index": 1,
            "sentence_source": "Das Buch.",
            "sentence_destination": "The book.",
            "sentence_destination2": "ContextLeft The book ContextRight",
        }],
        zid=session_zid,
    )
    
    tsv_path = env["results_dir"] / f"{session_zid}-test-book.de.tsv"
    
    with patch.object(desk, "run_progressive_worker_async") as mock_worker:
        html = desk._run_render_flow_impl(
            text="Das Buch.",
            language="de",
            zid=session_zid,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )
        
        # HTML translation box must contain clean sentence translation, NOT padded context
        assert "The book." in html
        assert "ContextLeft" not in html
        assert "ContextRight" not in html


def test_child_sentence_windows_isolate_clean_translation(mock_clean_context_env):
    """Verifies that child sessions store clean translation in sentence_destination and padded in sentence_destination2."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]
    
    sub_text = "Er ging nach Hause."
    sub_trans_clean = "He went home."
    sub_trans_padded = "ContextBefore He went home. ContextAfter"
    sub_zid = "20260822190003"
    sub_slug = "er-ging-nach-hause"
    
    headers = [
        "Quotation", "WordSource", "WordDestination", "SentenceSource",
        "SentenceDestination", "SentenceDestination2", "SentenceSourceIndex", "DeskSelected"
    ]
    sub_rows = [
        ["", "ging", "went", "ContextBefore Er ging nach Hause. ContextAfter", sub_trans_padded, "", "1", "1"]
    ]
    
    child_sentences = [{
        "session_zid": sub_zid,
        "sentence_index": 1,
        "sentence_source": sub_text,
        "sentence_destination": sub_trans_clean,
        "sentence_destination2": sub_trans_padded,
    }]
    
    adapter = desk.get_storage_adapter(config, resolved_paths)
    adapter.save_session(
        session_zid=sub_zid,
        slug=sub_slug,
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text=sub_text,
        headers=headers,
        data_rows=sub_rows,
        sentences=child_sentences,
        zid=sub_zid,
    )
    
    db_sents = adapter.db.get_sentences_by_session(sub_zid)
    assert len(db_sents) == 1
    assert db_sents[0]["sentence_destination"] == "He went home."
    assert db_sents[0]["sentence_destination2"] == "ContextBefore He went home. ContextAfter"
    
    # In SQLite session restoration, SentenceDestination holds clean 1:1 translation and SentenceDestination2 holds padded context
    restored = adapter.restore_session(sub_zid)
    dest1_idx = restored["headers"].index("SentenceDestination")
    dest2_idx = restored["headers"].index("SentenceDestination2")
    assert restored["data_rows"][0][dest1_idx] == "He went home."
    assert restored["data_rows"][0][dest2_idx] == "ContextBefore He went home. ContextAfter"
