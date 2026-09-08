import sys
import configparser
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import kardenwort_desk
from kardenwort_desk import (
    deduplicate_rows_by_lemma,
    _sanitize_rows,
    run_render_flow,
    SqliteStorageAdapter,
    SEC_TOKEN_MAPPINGS,
    SEC_CLASSIFICATION,
    SEC_RENDERING,
    SEC_LANGUAGES,
    SEC_STORAGE,
)


def _create_render_test_env(tmp_path):
    config = configparser.ConfigParser()
    config.add_section(SEC_TOKEN_MAPPINGS)
    config.set(SEC_TOKEN_MAPPINGS, 'enabled', 'true')
    config.set(SEC_TOKEN_MAPPINGS, 'apostrophe_chars', "', ’, ‘, `, ´, ʼ")
    config.set(SEC_TOKEN_MAPPINGS, 'split_gap_limit', '3')
    config.add_section(SEC_CLASSIFICATION)
    config.set(SEC_CLASSIFICATION, 'enabled', 'false')
    config.add_section(SEC_RENDERING)
    config.set(SEC_RENDERING, 'theme', 'dark')
    config.add_section(SEC_LANGUAGES)
    config.set(SEC_LANGUAGES, 'en_prompt', 'en_prompt')
    config.set(SEC_LANGUAGES, 'en_lemma_index', 'en_idx')
    config.set(SEC_LANGUAGES, 'en_lemma_override', 'en_over')

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section('fields')
    mapping.add_section('fields_mapping.word')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordSourceInflectedForm', 'inflected')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    mapping.set('desk_columns', 'WordSourceMorphologyAI', 'morphology')

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, 'w', encoding='utf-8') as f:
        mapping.write(f)

    resolved_paths = {
        'kardenwort_workspace': tmp_path,
        'anki_mapping_file': str(mapping_file),
        'kardenwort_python': sys.executable
    }
    return config, resolved_paths


def test_deduplicate_rows_by_lemma_preserves_empty_lemma_rows():
    """Verify that rows without a lemma are preserved and not silently dropped."""
    headers = ["WordSource", "WordSourceInflectedForm", "WordDestination"]
    data_rows = [
        ["run", "running", "бежать"],
        ["", "", ""],  # empty row / separator
        ["walk", "walking", "идти"],
        ["", "trailing_comment", ""],
    ]
    role_fields = {"lemma": "WordSource", "inflected": "WordSourceInflectedForm"}
    deduped = deduplicate_rows_by_lemma(data_rows, headers, role_fields=role_fields)
    assert len(deduped) == 4
    assert deduped[0][0] == "run"
    assert deduped[1][0] == ""
    assert deduped[2][0] == "walk"
    assert deduped[3][0] == ""
    assert deduped[3][1] == "trailing_comment"


def test_sanitize_rows_strips_failed_sentinels():
    """Verify that _sanitize_rows replaces [FAILED] values with empty string."""
    data_rows = [
        ["pass", "passing", "[FAILED]", "noun"],
        ["run", "[FAILED]", "бежать", "[FAILED]"],
        ["walk", "walking", "идти", "verb"],
    ]
    sanitized = _sanitize_rows(data_rows)
    assert sanitized[0] == ["pass", "passing", "", "noun"]
    assert sanitized[1] == ["run", "", "бежать", ""]
    assert sanitized[2] == ["walk", "walking", "идти", "verb"]


def test_failed_sentinel_stripped_before_save_and_on_restore(tmp_path):
    """Verify that [FAILED] sentinels are stripped on SQLite save and restore."""
    db_path = tmp_path / "test_kardenwort.db"
    cfg = configparser.ConfigParser()
    cfg.add_section("storage")
    cfg.set("storage", "backend", "sqlite")
    cfg.add_section("paths")
    cfg.set("paths", "database_file", str(db_path))
    resolved_paths = {"database_file": db_path}

    adapter = SqliteStorageAdapter(config=cfg, resolved_paths=resolved_paths)
    zid = "20260822230117"
    headers = ["Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination", "SentenceSourceIndex", "SentenceSource"]
    data_rows = [
        ["passing", "pass", "passing", "[FAILED]", "1", "He is passing and running."],
        ["running", "run", "running", "бежать", "1", "He is passing and running."],
    ]

    adapter.save_session(
        session_zid=zid,
        slug="test-save-strip",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="He is passing and running.",
        headers=headers,
        data_rows=data_rows,
        zid=zid,
    )

    restored = adapter.restore_session(zid)
    restored_rows = restored["data_rows"]
    col_dest = restored["headers"].index("WordDestination")
    assert restored_rows[0][col_dest] == ""
    assert restored_rows[1][col_dest] == "бежать"


def test_morphology_ai_column_resolves_non_negative_index(tmp_path):
    """Verify that col_morph resolves to a valid index when WordSourceMorphologyAI is present in headers."""
    config, resolved_paths = _create_render_test_env(tmp_path)
    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_file = res_dir / "20260822230117-morph.en.tsv"
    tsv_content = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tWordSourceMorphologyAI\n"
        "passing\tpass\tpassing\tпроходить\tVerb: Pres Part\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = run_render_flow(
        text="passing",
        language="en",
        zid="20260822230117",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    assert "Verb: Pres Part" in html
    assert 'data-col="WordSourceMorphologyAI"' in html


def test_render_flow_preserves_data_token_order_attribute(tmp_path):
    """Verify that rendered <tr> carries data-token-order attribute."""
    config, resolved_paths = _create_render_test_env(tmp_path)
    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_file = res_dir / "20260822230117-tokenorder.en.tsv"
    tsv_content = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tTokenOrder\n"
        "passing\tpass\tpassing\tпроходить\t42\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = run_render_flow(
        text="passing",
        language="en",
        zid="20260822230117",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    assert 'data-token-order="42"' in html


def test_write_update_js_serializes_token_order_and_inflected(tmp_path):
    """Verify that write_update_js serializes token_order, inflected form, and sentence_idx."""
    from kardenwort_desk import write_update_js
    import json

    tsv_file = tmp_path / "20260823005501-test.en.tsv"
    tsv_file.write_text("Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tTokenOrder\tSentenceSourceIndex\n", encoding="utf-8")
    headers = ["Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination", "TokenOrder", "SentenceSourceIndex"]
    data_rows = [
        ["dem", "der", "dem", "the", "5", "1"],
        ["geht", "gehen", "geht", "goes", "2", "1"],
    ]
    role_fields = {
        "lemma": "WordSource",
        "inflected": "WordSourceInflectedForm",
        "word_translation": "WordDestination",
    }
    import configparser
    tsv_config = configparser.ConfigParser()
    tsv_config.read_string("[storage]\nbackend=tsv\n")
    js_path = write_update_js(tsv_file, data_rows, headers, role_fields, stage="translated", zid="20260823005501", config=tsv_config)
    assert js_path is not None and js_path.exists()
    content = js_path.read_text(encoding="utf-8")
    assert "window.receiveUpdate" in content
    # Parse update payload
    prefix = "window.receiveUpdate("
    start = content.index(prefix) + len(prefix)
    end = content.rindex(");")
    payload = json.loads(content[start:end])
    assert "rows" in payload
    rows = payload["rows"]
    assert "0" in rows or 0 in rows
    r0 = rows.get("0") or rows.get(0)
    assert r0["token_order"] == "5"
    assert r0["inflected"] == "dem"
    assert r0["lemma"] == "der"
    assert r0["trans"] == "the"
    assert r0["sentence_idx"] == "1"


def test_js_render_row_includes_token_order_lookup_and_inflected_sync(tmp_path):
    """Verify that generated HTML JavaScript contains token_order matching and inflected synchronization."""
    config, resolved_paths = _create_render_test_env(tmp_path)
    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_file = res_dir / "20260823005501-jscheck.en.tsv"
    tsv_content = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tTokenOrder\n"
        "passing\tpass\tpassing\tпроходить\t12\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = run_render_flow(
        text="passing",
        language="en",
        zid="20260823005501",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    assert "data-token-order" in html
    assert "rowData.token_order" in html
    assert "rowData.hasOwnProperty('inflected')" in html


def test_subtoken_candidate_isolation_and_compound_highlighting(tmp_path):
    """Verify that short-prefix matching works for atomic lemmas without leaking to sibling subtokens."""
    config, resolved_paths = _create_render_test_env(tmp_path)
    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_file = res_dir / "20260824192029-compcheck.en.tsv"
    tsv_content = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tTokenOrder\n"
        "record-setting\tset\trecord-setting\tустанавливать\t7\n"
        "record-setting\trecord\trecord-setting\tзапись\t9\n"
        "record-setting\trecord-set\trecord-setting\tустановление рекорда\t21\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = run_render_flow(
        text="A record-setting achievement.",
        language="en",
        zid="20260824192029",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    import json
    import re
    m = re.search(r'<script id="token-map" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert m is not None
    token_map = json.loads(m.group(1))

    record_tok = next((t for t in token_map if t.get("text") == "record"), None)
    setting_tok = next((t for t in token_map if t.get("text") == "setting"), None)

    assert record_tok is not None
    assert setting_tok is not None

    # Atomic row isolation
    assert 1 in record_tok["atomic_row_ids"]  # row 1 is lemma 'record'
    assert 0 not in record_tok["atomic_row_ids"]  # row 0 is lemma 'set'
    assert 0 in setting_tok["atomic_row_ids"]  # row 0 is lemma 'set'
    assert 1 not in setting_tok["atomic_row_ids"]  # row 1 is lemma 'record'

    # Composite row mapping
    assert 2 in record_tok["compound_row_ids"]  # row 2 is composite 'record-set'
    assert 2 in setting_tok["compound_row_ids"]  # row 2 is composite 'record-set'


def test_german_contraction_surface_forms_collected_and_filtered():
    """Verify that deduplicate_rows extracts surface forms (am, im, beim) from Quotation and filters out un-occurring dem."""
    import kardenwort_desk as desk
    import configparser

    config = configparser.ConfigParser()
    config.add_section(desk.SEC_SETTINGS)
    config.set(desk.SEC_SETTINGS, 'filter_inflected_by_window', 'true')
    config.set(desk.SEC_SETTINGS, 'combine_source_words', 'true')

    # data_rows: [Quotation, WordSource (lemma), WordSourceInflectedForm (spacy decomp)]
    data_rows = [
        ["am", "der", "dem"],
        ["im", "der", "dem"],
        ["die", "der", "die"],
        ["beim", "der", "dem"],
    ]
    window_text = "am Morgen im Haus beim Spiel die Katze"
    deduped = desk.deduplicate_rows(
        data_rows, col_word_source=1, col_pos=-1, col_inflected=2, config=config,
        window_text=window_text, col_quotation=0
    )

    assert len(deduped) == 1
    inflected_result = deduped[0][2]
    # Surface words 'am', 'im', 'beim', 'die' must be present; synthetic 'dem' must be absent!
    assert "am" in inflected_result
    assert "im" in inflected_result
    assert "beim" in inflected_result
    assert "die" in inflected_result
    assert "dem" not in inflected_result.split(', ')


def test_overview_tab_all_row_ids_and_inflected_title_tooltip(tmp_path):
    """Verify that Tab 1 overview rows contain data-all-row-ids and title tooltip on WordSourceInflectedForm cell."""
    config, resolved_paths = _create_render_test_env(tmp_path)
    config.add_section("sentences_mode")
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_file = res_dir / "20260908163000-ovtest.de.tsv"
    tsv_content = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\n"
        "am\tder\tam\tthe\t1\n"
        "die\tder\tdie\tthe\t2\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html = run_render_flow(
        text="am Morgen. die Katze.",
        language="de",
        zid="20260908163000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    assert 'all_row_ids' in html or 'data-all-row-ids' in html
    assert 'data-col="WordSourceInflectedForm"' in html or 'WordSourceInflectedForm' in html
    assert 'am' in html and 'die' in html


def test_separable_verb_exact_token_indices_and_particle_disambiguation(tmp_path):
    """Verify that German separable verbs pair finite stem with clause-final particle (purple) without stealing internal preposition."""
    config, resolved_paths = _create_render_test_env(tmp_path)
    config.set(SEC_TOKEN_MAPPINGS, 'split_gap_limit', '60')
    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_file = res_dir / "20260908165354-sepverb.de.tsv"
    
    # 1. Test with explicit TokenOrder coordinate (4+14)
    tsv_content = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tTokenOrder\tSentenceSourceIndex\n"
        "passt\tanpassen\tpasst, an\tадаптировать\t4+14\t1\n"
        "an\tan\tan\tк\t11\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    sentence = "Mit dem passenden Zubehör passt du das Bike schnell und einfach an deinen Alltag an."
    html = run_render_flow(
        text=sentence,
        language="de",
        zid="20260908165354",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
    )

    import re
    # Extract source text word spans in sequential order
    word_spans = re.findall(r'<span class="(word[^"]*)" data-word-idx="\d+"[^>]*data-lower-clean="[^"]+"[^>]*>([^<]+)</span>', html)
    assert len(word_spans) == 15, f"Expected 15 source text word spans, got {len(word_spans)}"

    cls_passt, text_passt = word_spans[4]
    assert text_passt == "passt"
    assert "highlight-purple" in cls_passt, f"passt (idx 4) must be highlight-purple, got {cls_passt}"

    cls_an_prep, text_an_prep = word_spans[11]
    assert text_an_prep == "an"
    assert "highlight-purple" not in cls_an_prep, f"preposition an (idx 11) must NOT be highlight-purple, got {cls_an_prep}"
    assert "highlight-orange" in cls_an_prep, f"preposition an should be highlight-orange for its own row, got {cls_an_prep}"

    cls_an_part, text_an_part = word_spans[14]
    assert text_an_part == "an"
    assert "highlight-purple" in cls_an_part, f"terminal an (idx 14) must be highlight-purple, got {cls_an_part}"

    # 2. Test fallback without TokenOrder coordinate (resolves via resolve_anchored_positions)
    tsv_file_fallback = res_dir / "20260908165354-fallback.de.tsv"
    tsv_content_fallback = (
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\n"
        "passt\tanpassen\tpasst an\tадаптировать\t1\n"
        "an\tan\tan\tк\t1\n"
    )
    tsv_file_fallback.write_text(tsv_content_fallback, encoding="utf-8")

    html_fb = run_render_flow(
        text=sentence,
        language="de",
        zid="20260908165354",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file_fallback),
    )
    word_spans_fb = re.findall(r'<span class="(word[^"]*)" data-word-idx="\d+"[^>]*data-lower-clean="[^"]+"[^>]*>([^<]+)</span>', html_fb)
    assert len(word_spans_fb) == 15

    assert "highlight-purple" in word_spans_fb[4][0], f"fallback passt must be highlight-purple, got {word_spans_fb[4][0]}"
    assert "highlight-purple" in word_spans_fb[14][0], f"fallback terminal an must be highlight-purple, got {word_spans_fb[14][0]}"
    assert "highlight-purple" not in word_spans_fb[11][0], f"fallback preposition an must NOT be highlight-purple, got {word_spans_fb[11][0]}"





