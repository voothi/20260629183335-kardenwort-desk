"""
Unit tests for Clean Translation View Context Isolation.
Verifies that sentence context padding (anki_context_words_before/after)
remains strictly contained in tabular Anki card fields (data_rows / SentenceDestination),
while the UI Translation view and sentences table (sentence_destination) present clean,
unpadded 1:1 sentence translations across initial render and progressive worker updates.
"""

import configparser
import json
import re
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
    
    config.add_section("languages")
    config.set("languages", "de_prompt", "german")
    config.set("languages", "en_prompt", "english")
    
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


def test_single_mode_multi_sentence_unified_div_and_matches_write_update_js(mock_clean_context_env):
    """Verifies that multi-sentence single mode initial HTML produces a single unified <div> paragraph and matches write_update_js output without layout shifting."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]

    adapter = desk.get_storage_adapter(config, resolved_paths)
    session_zid = "20260825184501"

    headers = [
        "Quotation", "WordSource", "WordDestination", "SentenceSource",
        "SentenceDestination", "SentenceDestination2", "SentenceSourceIndex", "DeskSelected"
    ]
    data_rows = [
        ["", "Erste", "first", "Erster Satz.", "First sentence.", "", "1", "1"],
        ["", "Zweite", "second", "Zweiter Satz.", "Second sentence.", "", "2", "1"]
    ]

    adapter.save_session(
        session_zid=session_zid,
        slug="multi-sent",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Erster Satz. Zweiter Satz.",
        headers=headers,
        data_rows=data_rows,
        sentences=[
            {
                "session_zid": session_zid,
                "sentence_index": 1,
                "sentence_source": "Erster Satz.",
                "sentence_destination": "First sentence.",
                "sentence_destination2": "Padded First sentence.",
            },
            {
                "session_zid": session_zid,
                "sentence_index": 2,
                "sentence_source": "Zweiter Satz.",
                "sentence_destination": "Second sentence.",
                "sentence_destination2": "Padded Second sentence.",
            }
        ],
        zid=session_zid,
    )

    tsv_path = env["results_dir"] / f"{session_zid}-multi-sent.de.tsv"
    role_fields = {
        "sentence_destination": "SentenceDestination",
        "sentence_index": "SentenceSourceIndex",
        "lemma": "WordSource",
        "word_translation": "WordDestination",
    }

    # 1. Initial render HTML
    with patch.object(desk, "run_progressive_worker_async"):
        html = desk._run_render_flow_impl(
            text="Erster Satz. Zweiter Satz.",
            language="de",
            zid=session_zid,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    # Initial HTML #translation-container must contain a single unified <div> paragraph with active provenance
    expected_unified_html = '<div data-provenance="cached:sqlite" title="Loaded from session cache (SQLite)">First sentence. Second sentence.</div>'
    assert f'<div class="translation-text" id="translation-container">{expected_unified_html}</div>' in html
    assert "<div>First sentence.</div><div>Second sentence.</div>" not in html

    # 2. write_update_js output
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
    payload_str = latest_js[len("if (typeof window.receiveUpdate === 'function') { window.receiveUpdate("):-4]
    payload = json.loads(payload_str)

    # write_update_js output sends clean plain text matching the content
    assert payload["translatedText"] == "First sentence. Second sentence."


def test_multi_mode_preserves_stacked_divs(mock_clean_context_env):
    """Verifies that multi text mode preserves line-by-line stacked <div> tags."""
    sentence_trans = {
        0: "First line.",
        1: "Second line."
    }
    html_single = desk.format_translated_html(sentence_trans, text_mode="single", text="First line. Second line.")
    assert html_single == "<div>First line. Second line.</div>"

    html_multi = desk.format_translated_html(sentence_trans, text_mode="multi", text="First line.\nSecond line.")
    assert html_multi == "<div>First line.</div><div>Second line.</div>"


def test_write_update_js_emits_text_translation_failure_signals(tmp_path):
    """Verifies that write_update_js serializes textTranslationStatus and textTranslationFailed."""
    tsv_path = tmp_path / "20260830143412-test.en.tsv"
    tsv_path.write_text("Quotation\tWordSource\tWordDestination\n", encoding="utf-8")
    
    js_path = desk.write_update_js(
        tsv_path=tsv_path,
        data_rows=[],
        headers=["Quotation", "WordSource", "WordDestination"],
        role_fields={"lemma": "WordSource", "word_translation": "WordDestination"},
        stage="translated_text",
        translated_text="",
        text_translation_status="failed",
        text_translation_failed=True,
        zid="20260830143412"
    )
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")
    payload_str = content[len("if (typeof window.receiveUpdate === 'function') { window.receiveUpdate("):-4]
    payload = json.loads(payload_str)
    
    assert payload["stage"] == "translated_text"
    assert payload["textTranslationStatus"] == "failed"
    assert payload["textTranslationFailed"] is True


def test_write_update_js_omits_text_provenance_when_not_provided(tmp_path):
    """Task 1.2: write_update_js must omit textProvenance unless explicitly passed, avoiding cached:sqlite clobbering."""
    tsv_path = tmp_path / "20260830205231-test.en.tsv"
    tsv_path.write_text("Quotation\tWordSource\tWordDestination\tSentenceDestination\n", encoding="utf-8")

    js_path = desk.write_update_js(
        tsv_path=tsv_path,
        data_rows=[["", "apple", "яблоко", "The apple."]],
        headers=["Quotation", "WordSource", "WordDestination", "SentenceDestination"],
        role_fields={"lemma": "WordSource", "word_translation": "WordDestination", "sentence_destination": "SentenceDestination"},
        stage="finished",
        translated_text="The apple.",
        zid="20260830205231"
    )
    content = js_path.read_text(encoding="utf-8")
    payload_str = content[len("if (typeof window.receiveUpdate === 'function') { window.receiveUpdate("):-4]
    payload = json.loads(payload_str)

    assert "textProvenance" not in payload
    assert "text_provenance" not in payload


def test_progressive_worker_finished_preserves_live_text_provenance(mock_clean_context_env, monkeypatch):
    """Task 1.1: cmd_progressive_worker passes active_text_prov to safe_write_update_js at stage='finished'."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]
    session_zid = "20260830205231"
    tsv_path = env["results_dir"] / f"{session_zid}-test.de.tsv"
    headers = ["WordSource", "WordDestination", "SentenceSourceIndex", "SentenceDestination"]
    data_rows = [["Katze", "", "1", ""]]

    with open(tsv_path, 'w', encoding='utf-8', newline='') as f:
        f.write("\t".join(headers) + "\n")
        f.write("\t".join(data_rows[0]) + "\n")

    txt_path = tsv_path.with_suffix('.txt')
    txt_path.write_text("Die Katze schläft.", encoding='utf-8')

    captured_updates = []
    def mock_safe_write_update_js(t_path, rows, h, rf, **kw):
        captured_updates.append(kw)

    monkeypatch.setattr(desk, "load_config", lambda *a, **kw: (config, resolved_paths, {}, {}))
    monkeypatch.setattr(desk, "safe_write_update_js", mock_safe_write_update_js)
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "The cat sleeps", "FULL_TEXT": "The cat sleeps"})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda *a, **kw: {"Katze": "cat"})

    class MockArgs:
        zid = session_zid
        tsv = str(tsv_path)
        config = None
        language = "de"
        target_lang = "en"
        text_mode = "single"
        skip_intellifiller = True

    desk.cmd_progressive_worker(MockArgs())

    # Verify stage="finished" captured live text provenance
    expected_prov = f"live:{config.get('pipeline', 'text_base_provider', fallback='google')}"
    finished_updates = [u for u in captured_updates if u.get("stage") == "finished"]
    assert len(finished_updates) > 0
    assert finished_updates[-1].get("text_provenance") == expected_prov


def test_translation_container_word_span_zero_padding_and_window_tokenization(mock_clean_context_env):
    """Task 2.1 & 2.2: CSS #translation-container span.word must use padding: 0 and window.tokenizeTranslation is exposed."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]
    session_zid = "20260830205231"
    tsv_path = env["results_dir"] / f"{session_zid}-test.de.tsv"

    adapter = desk.get_storage_adapter(config, resolved_paths)
    adapter.save_session(
        session_zid=session_zid,
        slug="test",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Schuhcenter.de.",
        sentences=[{
            "session_zid": session_zid,
            "sentence_index": 1,
            "sentence_source": "Schuhcenter.de.",
            "sentence_destination": "Schuhcenter.de.",
        }],
        headers=["Quotation", "WordSource", "WordDestination", "SentenceDestination", "SentenceSourceIndex"],
        data_rows=[["", "Schuhcenter", "Schuhcenter", "Schuhcenter.de.", "1"]],
        comments=[],
        working_tsv_path=tsv_path,
        zid=session_zid,
    )

    with patch.object(desk, "run_progressive_worker_async"):
        html = desk._run_render_flow_impl(
            text="Schuhcenter.de.",
            language="de",
            zid=session_zid,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    # 1. Verify CSS rule #translation-container span.word uses padding: 0
    assert "#translation-container span.word" in html
    css_match = re.search(r'#translation-container\s+span\.word\s*\{([^}]+)\}', html)
    assert css_match is not None
    css_block = css_match.group(1)
    assert "padding: 0;" in css_block or "padding:0;" in css_block

    # 2. Verify window.tokenizeTranslation is exposed in client scripts
    assert "window.tokenizeTranslation = tokenizeTranslation" in html


def test_skeleton_loader_proportions_unified(mock_clean_context_env):
    """Task 2.1 & 2.2: Base .skeleton-loader (20px) and #translation-container .skeleton-loader (25.6px) match typography heights."""
    env = mock_clean_context_env
    config = env["config"]
    resolved_paths = env["resolved_paths"]
    session_zid = "20260830205231"
    tsv_path = env["results_dir"] / f"{session_zid}-test.de.tsv"

    adapter = desk.get_storage_adapter(config, resolved_paths)
    adapter.save_session(
        session_zid=session_zid,
        slug="test-skel",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Katze",
        sentences=[],
        headers=["Quotation", "WordSource", "WordDestination"],
        data_rows=[["", "Katze", ""]],
        comments=[],
        working_tsv_path=tsv_path,
        zid=session_zid,
    )

    with patch.object(desk, "run_progressive_worker_async"):
        html = desk._run_render_flow_impl(
            text="Katze",
            language="de",
            zid=session_zid,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    # 1. Base .skeleton-loader rule has min-height: 20px; height: 20px; line-height: 20px;
    base_match = re.search(r'\.skeleton-loader\s*\{([^}]+)\}', html)
    assert base_match is not None
    base_css = base_match.group(1)
    assert "min-height: 20px;" in base_css
    assert "height: 20px;" in base_css
    assert "line-height: 20px;" in base_css

    # 2. Translation container .skeleton-loader rule has min-height: 25.6px; height: 25.6px; line-height: 25.6px;
    tc_match = re.search(r'#translation-container\s+\.skeleton-loader\s*\{([^}]+)\}', html)
    assert tc_match is not None
    tc_css = tc_match.group(1)
    assert "min-height: 25.6px;" in tc_css
    assert "height: 25.6px;" in tc_css
    assert "line-height: 25.6px;" in tc_css





