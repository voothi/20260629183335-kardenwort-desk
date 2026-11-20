"""Acceptance and control tests for single-session sentence identity alignment.

Exercises real SqliteStorageAdapter persistence, real kardenwort_alignment
validation, real SessionArbiter retext/reword boundaries, and real SQLite
storage with only unmanaged external translation/enrichment HTTP fixtures.
"""
import pytest

import kardenwort_alignment as alignment
import kardenwort_desk as desk
from kardenwort_desk import ErrorCode, StructuredError
from tests.controller_test_support import controller_world  # noqa: F401
from tests.enrichment_test_support import enrichment_engine


def _single_unit_headers(include_sentence_index: bool) -> list:
    headers = ["TokenOrder", "Quotation", "WordSource"]
    if include_sentence_index:
        headers.append("SentenceSourceIndex")
    headers.extend(["SentenceSource", "SentenceDestination", "WordDestination"])
    return headers


def _single_unit_rows(sentence_index_value=None) -> list:
    rows = [
        ["0", "Hallo", "hallo", "Hallo Welt.", "Старый перевод.", "привет"],
        ["1", "Welt", "welt", "Hallo Welt.", "Старый перевод.", "мир"],
    ]
    if sentence_index_value is None:
        return rows
    return [row[:3] + [sentence_index_value] + row[3:] for row in rows]


def test_fresh_single_unit_without_sentence_index_aligns_for_retext_and_reword(controller_world):
    """Scenario: Fresh single unit (manifest omits SentenceSourceIndex column)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    adapter = desk.SqliteStorageAdapter(config, paths)
    arbiter = server.arbiter
    arbiter.storage_adapter = adapter

    zid = "20261120213001"
    unrelated_apple = adapter.db.get_words_by_session("20261108113001")
    unrelated_dog = adapter.db.get_words_by_session("20261108113002")

    adapter.save_session(
        session_zid=zid,
        slug="hallo-welt",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hallo Welt.",
        headers=_single_unit_headers(include_sentence_index=False),
        data_rows=_single_unit_rows(sentence_index_value=None),
        text_provenance="cached:initial",
    )

    sentences = adapter.db.get_sentences_by_session(zid)
    words = adapter.db.get_words_by_session(zid)
    assert [s["sentence_index"] for s in sentences] == [1]
    assert [w["sentence_index"] for w in words] == [1, 1]
    alignment.validate_authoritative_records(sentences)

    restored = adapter.restore_session(zid)
    col_sent_idx = restored["headers"].index("SentenceSourceIndex")
    assert [row[col_sent_idx] for row in restored["data_rows"]] == ["1", "1"]

    retext_result = arbiter.retext_session(session_zid=zid, language="de", text_mode="single")
    assert retext_result["status"] == "success"
    updated_sentences = adapter.db.get_sentences_by_session(zid)
    assert [s["sentence_index"] for s in updated_sentences] == [1]
    assert updated_sentences[0]["sentence_destination"] == "google: Hallo Welt."

    with enrichment_engine(config) as engine:
        engine.release.set()
        reword_result = arbiter.reword_session(
            session_zid=zid,
            target_coordinates=[{"sentence_idx": 1, "token_order": 0}],
            language="de",
        )
    assert reword_result["status"] == "success"
    updated_words = {
        (w["sentence_index"], w["token_order"]): w
        for w in adapter.db.get_words_by_session(zid)
    }
    assert updated_words[(1, 0)]["word_destination"] == "trans_hallo"
    assert updated_words[(1, 1)]["word_destination"] == "мир"

    assert adapter.db.get_words_by_session("20261108113001") == unrelated_apple
    assert adapter.db.get_words_by_session("20261108113002") == unrelated_dog


def test_fresh_single_unit_with_blank_sentence_index_avoids_row_order_guessing(controller_world):
    """Scenario: Fresh single unit (SentenceSourceIndex header present but blank)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    adapter = desk.SqliteStorageAdapter(config, paths)

    zid = "20261120213002"
    adapter.save_session(
        session_zid=zid,
        slug="hallo-welt-blank-idx",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hallo Welt.",
        headers=_single_unit_headers(include_sentence_index=True),
        data_rows=_single_unit_rows(sentence_index_value=""),
        text_provenance="cached:initial",
    )

    sentences = adapter.db.get_sentences_by_session(zid)
    words = adapter.db.get_words_by_session(zid)
    assert [s["sentence_index"] for s in sentences] == [1]
    assert [w["sentence_index"] for w in words] == [1, 1]
    alignment.validate_authoritative_records(sentences)


def test_ambiguous_multi_sentence_without_sentence_index_is_rejected_without_writes(controller_world):
    """Scenario: Unknown multi-unit source (distinct sentences lack exact identity)."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    adapter = desk.SqliteStorageAdapter(config, paths)

    zid = "20261120213003"
    unrelated_apple = adapter.db.get_words_by_session("20261108113001")

    with pytest.raises(StructuredError) as exc_info:
        adapter.save_session(
            session_zid=zid,
            slug="ambiguous-multi",
            source_language="de",
            target_language="ru",
            text_mode="multi",
            source_raw_text="Erster Satz.\nZweiter Satz.",
            headers=_single_unit_headers(include_sentence_index=False),
            data_rows=[
                ["0", "Erster", "erste", "Erster Satz.", "Первое.", "первый"],
                ["1", "Zweiter", "zweite", "Zweiter Satz.", "Второе.", "второй"],
            ],
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_STATE
    assert adapter.db.get_session(zid) is None
    assert adapter.db.get_sentences_by_session(zid) == []
    assert adapter.db.get_words_by_session(zid) == []
    assert adapter.db.get_words_by_session("20261108113001") == unrelated_apple


def test_control_explicit_positive_sentence_index_retext_and_reword_succeeds(controller_world):
    """Valid adjacent control: explicit SentenceSourceIndex=1 works across retext/reword."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    adapter = desk.SqliteStorageAdapter(config, paths)
    arbiter = server.arbiter
    arbiter.storage_adapter = adapter

    zid = "20261120213004"
    unrelated_apple = adapter.db.get_words_by_session("20261108113001")

    adapter.save_session(
        session_zid=zid,
        slug="control-explicit-one",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hallo Welt.",
        headers=_single_unit_headers(include_sentence_index=True),
        data_rows=_single_unit_rows(sentence_index_value="1"),
        text_provenance="cached:initial",
    )

    sentences = adapter.db.get_sentences_by_session(zid)
    words = adapter.db.get_words_by_session(zid)
    assert [s["sentence_index"] for s in sentences] == [1]
    assert [w["sentence_index"] for w in words] == [1, 1]
    alignment.validate_authoritative_records(sentences)

    retext_result = arbiter.retext_session(session_zid=zid, language="de", text_mode="single")
    assert retext_result["status"] == "success"
    updated_sentences = adapter.db.get_sentences_by_session(zid)
    assert [s["sentence_index"] for s in updated_sentences] == [1]
    assert updated_sentences[0]["sentence_destination"] == "google: Hallo Welt."

    with enrichment_engine(config) as engine:
        engine.release.set()
        reword_result = arbiter.reword_session(
            session_zid=zid,
            target_coordinates=[{"sentence_idx": 1, "token_order": 0}],
            language="de",
        )
    assert reword_result["status"] == "success"
    updated_words = {
        (w["sentence_index"], w["token_order"]): w
        for w in adapter.db.get_words_by_session(zid)
    }
    assert updated_words[(1, 0)]["word_destination"] == "trans_hallo"
    assert updated_words[(1, 1)]["word_destination"] == "мир"
    assert adapter.db.get_words_by_session("20261108113001") == unrelated_apple


def test_control_multi_sentence_and_overview_selections_remain_intact(controller_world):
    """Valid adjacent control: explicit multi-sentence identity and overview_selections."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    adapter = desk.SqliteStorageAdapter(config, paths)

    zid = "20261120213005"
    adapter.save_session(
        session_zid=zid,
        slug="control-multi-overview",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Erster Satz.\nZweiter Satz.",
        headers=_single_unit_headers(include_sentence_index=True),
        data_rows=[
            ["0", "Erster", "erste", "1", "Erster Satz.", "Первое.", "первый"],
            ["1", "Zweiter", "zweite", "2", "Zweiter Satz.", "Второе.", "второй"],
        ],
        text_provenance="cached:initial",
    )

    assert adapter.update_word_selection(zid, sentence_idx=0, token_order=0, selected=1) is True
    assert adapter.get_overview_selections(zid) == {0}
    sentences = adapter.db.get_sentences_by_session(zid)
    assert [s["sentence_index"] for s in sentences] == [1, 2]
    alignment.validate_authoritative_records(sentences)
