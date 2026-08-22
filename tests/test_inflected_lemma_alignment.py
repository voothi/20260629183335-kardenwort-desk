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

