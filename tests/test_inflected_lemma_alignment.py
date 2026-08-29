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

