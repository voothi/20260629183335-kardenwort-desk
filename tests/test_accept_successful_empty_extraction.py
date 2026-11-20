"""Acceptance and control tests for successful empty JSONL extraction.

Exercises real JSONL extraction boundary, real configured extractor subprocess
(with deterministic unmanaged spaCy/translation HTTP fixtures), real isolated
SQLite persistence, and Playwright DOM projection with zero internal mocks.
"""
import json
import subprocess

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_extraction import extraction_rows
from tests.controller_test_support import controller_world  # noqa: F401
from tests.render_pipeline_support import configure_render_pipeline
from tests.test_structured_enrichment import control_writes, prohibited


def _mapping_fields(paths):
    mapping = desk.load_anki_mapping(paths["anki_mapping_file"])
    return list(mapping["fields"].keys())


@pytest.mark.parametrize("stdout", ["", "\n", "   \r\n\t\n"])
def test_extraction_rows_accepts_empty_and_whitespace_stdout(stdout):
    """Direct boundary contract: zero records return an empty list."""
    headers = ["TokenOrder", "WordSource", "SentenceSourceIndex", "SentenceSource"]
    assert extraction_rows(stdout, headers) == []


def test_real_extractor_zero_records_single_sentence_persists_and_renders(controller_world, page):
    """Scenario: Successful zero records (single-sentence real extractor + SQLite + DOM)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.read_dict({
        "rendering": {"display_mode": "monolithic"},
        "sentences_mode": {"enabled": "false"},
        "wordfill": {"enabled": "false"},
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
    })

    zid = "20261120210001"
    source_text = "!!!"
    cold = desk.SqliteStorageAdapter(config, paths)
    unrelated_apple = cold.db.get_words_by_session("20261108113001")
    unrelated_dog = cold.db.get_words_by_session("20261108113002")

    with control_writes() as writes:
        html = desk.run_render_flow(
            source_text, "en", zid, "single", config, paths,
            spawn_children=False, wordfill_cfg={"enabled": False},
        )

    assert prohibited(writes) == []
    assert cold.db.get_words_by_session("20261108113001") == unrelated_apple
    assert cold.db.get_words_by_session("20261108113002") == unrelated_dog

    session = cold.db.get_session(zid)
    assert session is not None
    assert session["source_raw_text"] == source_text
    assert cold.db.get_words_by_session(zid) == []

    restored = cold.restore_session(zid)
    assert restored["source_text"] == source_text
    assert restored["words"] == []
    assert restored["data_rows"] == []

    page.set_content(html)
    expect(page.locator("#source-container")).to_contain_text(source_text)
    expect(page.locator("#lemma-table tbody tr[data-row-id]")).to_have_count(0)


def test_real_extractor_zero_records_container_retains_empty_vocabulary_presentation(controller_world, page):
    """Scenario: Successful zero records (multi-sentence container empty-vocabulary view)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.read_dict({
        "rendering": {"display_mode": "monolithic"},
        "sentences_mode": {"enabled": "true", "delivery_mode": "container", "min_sentences": "2"},
        "wordfill": {"enabled": "false"},
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
    })

    zid = "20261120210002"
    source_text = "!!! ..."
    cold = desk.SqliteStorageAdapter(config, paths)
    unrelated_apple = cold.db.get_words_by_session("20261108113001")

    with control_writes() as writes:
        html = desk.run_render_flow(
            source_text, "en", zid, "single", config, paths,
            spawn_children=False, wordfill_cfg={"enabled": False},
        )

    assert prohibited(writes) == []
    assert cold.db.get_words_by_session("20261108113001") == unrelated_apple

    session = cold.db.get_session(zid)
    assert session is not None
    assert session["source_raw_text"] == source_text
    assert cold.db.get_words_by_session(zid) == []
    assert [s["sentence_source"] for s in cold.db.get_sentences_by_session(zid)] == ["!!!", "..."]

    page.set_content(html)
    expect(page.locator("#source-container")).to_contain_text("!!!")
    expect(page.locator("#lemma-table tbody tr[data-row-id]")).to_have_count(0)
    expect(page.locator("#lemma-table tbody tr.kw-empty-table-row td.kw-empty-cell")).to_contain_text(
        "No vocabulary words in this section"
    )


@pytest.mark.parametrize(
    "case_name,build_stdout,expected_exc",
    [
        ("malformed_json", lambda _fields: "{not valid json\n", ValueError),
        (
            "valid_then_malformed_json",
            lambda fields: json.dumps({name: "x" for name in fields}) + "\n{broken\n",
            ValueError,
        ),
        ("non_object_json", lambda _fields: '["not", "a", "dict"]\n', ValueError),
        ("missing_manifest_fields", lambda _fields: json.dumps({"WordSource": "house"}) + "\n", ValueError),
        (
            "extra_manifest_field",
            lambda fields: json.dumps({**{name: "x" for name in fields}, "UnexpectedField": "y"}) + "\n",
            ValueError,
        ),
        (
            "non_scalar_field_value",
            lambda fields: json.dumps({**{name: "x" for name in fields}, fields[0]: ["nested"]}) + "\n",
            ValueError,
        ),
    ],
)
def test_malformed_nonempty_output_fails_explicitly_without_partial_ingestion(
    controller_world, monkeypatch, case_name, build_stdout, expected_exc
):
    """Scenario: Malformed nonempty output (explicit failure, zero partial ingestion)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    fields = _mapping_fields(paths)
    bad_stdout = build_stdout(fields)
    zid = "20261120210003"
    cold = desk.SqliteStorageAdapter(config, paths)
    unrelated_before = cold.db.get_words_by_session("20261108113001")

    def fake_extractor(command, **kwargs):
        assert "--structured-output" in command
        assert "--output-file" not in command and "--text1-file" not in command
        return subprocess.CompletedProcess(command, 0, bad_stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_extractor)

    with control_writes() as writes:
        with pytest.raises(expected_exc):
            desk.prepare_lookup_tsv(
                "One house.", "en", "ru", config, paths, zid,
                ttl_seconds=0, cache_key=f"{zid}-one-house.en.tsv",
            )

    assert prohibited(writes) == []
    assert cold.db.get_session(zid) is None
    assert cold.db.get_words_by_session(zid) == []
    assert cold.db.get_sentences_by_session(zid) == []
    assert cold.db.get_words_by_session("20261108113001") == unrelated_before


@pytest.mark.parametrize("stdout", ["", "partial"])
def test_nonzero_extractor_exit_fails_explicitly_without_partial_ingestion(
    controller_world, monkeypatch, stdout
):
    """Control: Nonzero process exit remains an explicit KARDENWORT_FAILED error."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    zid = "20261120210004"
    cold = desk.SqliteStorageAdapter(config, paths)
    unrelated_before = cold.db.get_words_by_session("20261108113001")

    def failing_extractor(command, **kwargs):
        assert "--structured-output" in command
        raise subprocess.CalledProcessError(returncode=1, cmd=command, output=stdout, stderr="extractor boom")

    monkeypatch.setattr(subprocess, "run", failing_extractor)

    with control_writes() as writes:
        with pytest.raises(desk.StructuredError) as exc_info:
            desk.prepare_lookup_tsv(
                "!!!", "en", "ru", config, paths, zid,
                ttl_seconds=0, cache_key=f"{zid}-untitled.en.tsv",
            )

    assert exc_info.value.error_code == desk.ErrorCode.KARDENWORT_FAILED
    assert prohibited(writes) == []
    assert cold.db.get_session(zid) is None
    assert cold.db.get_words_by_session(zid) == []
    assert cold.db.get_words_by_session("20261108113001") == unrelated_before


def test_ordinary_extraction_retains_identity_validation_and_ingestion(controller_world, page):
    """Scenario: Ordinary extraction (valid adjacent control with real extractor + SQLite + DOM)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.read_dict({
        "rendering": {"display_mode": "monolithic"},
        "sentences_mode": {"enabled": "false"},
        "wordfill": {"enabled": "false"},
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
    })

    zid = "20261120210005"
    source_text = "One house."
    cold = desk.SqliteStorageAdapter(config, paths)
    unrelated_before = cold.db.get_words_by_session("20261108113001")

    with control_writes() as writes:
        html = desk.run_render_flow(
            source_text, "en", zid, "single", config, paths,
            spawn_children=False, wordfill_cfg={"enabled": False},
        )

    assert prohibited(writes) == []
    assert cold.db.get_words_by_session("20261108113001") == unrelated_before

    session = cold.db.get_session(zid)
    assert session is not None
    assert session["source_raw_text"] == source_text
    words = cold.db.get_words_by_session(zid)
    assert {w["lemma"].lower() for w in words} == {"one", "house"}
    assert all(w["sentence_index"] == 1 for w in words)

    page.set_content(html)
    expect(page.locator("#source-container")).to_contain_text("One")
    expect(page.locator("#source-container")).to_contain_text("house")
    expect(page.locator("#lemma-table tbody tr[data-row-id]")).to_have_count(2)
