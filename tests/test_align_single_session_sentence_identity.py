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


@pytest.mark.parametrize(
    "bad_indices",
    [
        ["1", ""],
        ["abc", "1"],
        ["0", "0"],
        ["-1", "1"],
    ],
)
def test_invalid_or_mixed_sentence_source_index_rejected_without_writes(controller_world, bad_indices):
    """Scenario: Unknown/malformed identity is rejected without row-order guessing."""
    _, server = controller_world
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    zid = "20261120213006"
    rows = [
        ["0", "Hallo", "hallo", bad_indices[0], "Hallo Welt.", "Старый.", "привет"],
        ["1", "Welt", "welt", bad_indices[1], "Hallo Welt.", "Старый.", "мир"],
    ]
    with pytest.raises(StructuredError) as exc_info:
        adapter.save_session(
            session_zid=zid,
            slug="bad-sent-idx",
            source_language="de",
            target_language="ru",
            text_mode="single",
            source_raw_text="Hallo Welt.",
            headers=_single_unit_headers(include_sentence_index=True),
            data_rows=rows,
        )
    assert exc_info.value.error_code == ErrorCode.INVALID_STATE
    assert adapter.db.get_session(zid) is None
    assert adapter.db.get_sentences_by_session(zid) == []
    assert adapter.db.get_words_by_session(zid) == []


def test_ambiguous_multi_line_without_sentence_columns_rejected(controller_world):
    """Scenario: Unknown multi-unit source (multi mode + multi-line raw text, no index/source cols)."""
    _, server = controller_world
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    zid = "20261120213007"
    with pytest.raises(StructuredError) as exc_info:
        adapter.save_session(
            session_zid=zid,
            slug="multi-no-cols",
            source_language="de",
            target_language="ru",
            text_mode="multi",
            source_raw_text="Erster Satz.\nZweiter Satz.",
            headers=["TokenOrder", "Quotation", "WordSource", "WordDestination"],
            data_rows=[
                ["0", "Erster", "erste", "первый"],
                ["1", "Zweiter", "zweite", "второй"],
            ],
        )
    assert exc_info.value.error_code == ErrorCode.INVALID_STATE
    assert adapter.db.get_session(zid) is None


def test_ambiguous_multi_sentence_with_blank_sentence_index_rejected(controller_world):
    """Scenario: Unknown multi-unit source (blank SentenceSourceIndex column on distinct sentences)."""
    _, server = controller_world
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    zid = "20261120213008"
    with pytest.raises(StructuredError) as exc_info:
        adapter.save_session(
            session_zid=zid,
            slug="multi-blank-idx",
            source_language="de",
            target_language="ru",
            text_mode="multi",
            source_raw_text="Erster Satz.\nZweiter Satz.",
            headers=_single_unit_headers(include_sentence_index=True),
            data_rows=[
                ["0", "Erster", "erste", "", "Erster Satz.", "Первое.", "первый"],
                ["1", "Zweiter", "zweite", "", "Zweiter Satz.", "Второе.", "второй"],
            ],
        )
    assert exc_info.value.error_code == ErrorCode.INVALID_STATE
    assert adapter.db.get_session(zid) is None


def _seed_legacy_zero_index_session(adapter, zid, sentences, words, *, client_views=(), overview_tokens=()):
    """Directly seed historical zero-index SQLite rows to exercise the compatibility boundary."""
    adapter.db.save_session_bundle(
        session={
            "zid": zid,
            "slug": f"legacy-{zid}",
            "source_language": "de",
            "target_language": "ru",
            "text_mode": "single",
            "source_raw_text": sentences[0]["sentence_source"],
        },
        sentences=sentences,
        words=words,
        zid=zid,
    )
    with adapter.db.get_connection(zid=zid) as conn:
        for client_id, active_tokens_json in client_views:
            conn.execute(
                "INSERT INTO client_view_state(session_zid, client_id, active_card, active_tokens, revision) "
                "VALUES (?, ?, 1, ?, 2);",
                (zid, client_id, active_tokens_json),
            )
        for token_order in overview_tokens:
            conn.execute(
                "INSERT INTO overview_selections(session_zid, token_order, selected) VALUES (?, ?, 1);",
                (zid, token_order),
            )


def test_legacy_zero_index_only_0_adapts_atomically_preserving_facts_views_and_overview(controller_world):
    """Scenario: Existing legacy identity (only_0 -> 1 atomic adaptation)."""
    import json
    from kardenwort_client_views import ClientViewAdapter

    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    adapter = desk.SqliteStorageAdapter(config, paths)
    arbiter = server.arbiter
    arbiter.storage_adapter = adapter

    zid = "20261120213010"
    client_id = "0123456789abcdef0123456789abcdef"
    unrelated_apple = adapter.db.get_words_by_session("20261108113001")

    _seed_legacy_zero_index_session(
        adapter,
        zid,
        sentences=[
            {
                "session_zid": zid,
                "sentence_index": 0,
                "sentence_source": "Hallo Welt.",
                "sentence_destination": "Привет мир.",
                "sentence_destination2": "Привет, мир.",
                "sentence_source_ipa": "/halo velt/",
                "sentence_source_audio": "hallo.mp3",
                "text_provenance": "cached:legacy",
            }
        ],
        words=[
            {
                "session_zid": zid,
                "sentence_index": 0,
                "token_order": 0,
                "quotation": "Hallo",
                "inflected_form": "Hallo",
                "lemma": "hallo",
                "word_destination": "привет",
                "selected": 1,
                "leitner_box": 3,
                "word_provenance": "cached:legacy_word",
                "extra_fields": {"Note": "keep-me"},
            },
            {
                "session_zid": zid,
                "sentence_index": 0,
                "token_order": 1,
                "quotation": "Welt",
                "inflected_form": "Welt",
                "lemma": "welt",
                "word_destination": "мир",
                "selected": 0,
                "leitner_box": 1,
                "word_provenance": "cached:legacy_word",
            },
        ],
        client_views=[
            (
                client_id,
                json.dumps([{"visual_idx": 0, "sentence_idx": 0, "token_order": "0", "lemma": "hallo"}]),
            )
        ],
        overview_tokens=(0,),
    )

    # Before adaptation: canonical boundary strictly rejects sentence_index=0.
    with pytest.raises(ValueError):
        alignment.validate_authoritative_records(adapter.db.get_sentences_by_session(zid))
    with pytest.raises(StructuredError):
        arbiter.retext_session(session_zid=zid, language="de", text_mode="single")

    words_before_ids = [w["id"] for w in adapter.db.get_words_by_session(zid)]

    result = adapter.adapt_legacy_zero_index_session(zid)
    assert result == {
        "session_zid": zid,
        "adapted": True,
        "mode": "promote",
        "moved_words": 2,
        "updated_views": 1,
    }

    # Idempotent no-op once canonical.
    second = adapter.adapt_legacy_zero_index_session(zid)
    assert second["adapted"] is False
    assert second["reason"] == "already_canonical"

    # Verify sentences, words, provenance, extra_fields, client_view_state, and overview_selections.
    sentences = adapter.db.get_sentences_by_session(zid)
    assert len(sentences) == 1
    assert sentences[0]["sentence_index"] == 1
    assert sentences[0]["sentence_source"] == "Hallo Welt."
    assert sentences[0]["sentence_destination"] == "Привет мир."
    assert sentences[0]["sentence_destination2"] == "Привет, мир."
    assert sentences[0]["sentence_source_ipa"] == "/halo velt/"
    assert sentences[0]["sentence_source_audio"] == "hallo.mp3"
    assert sentences[0]["text_provenance"] == "cached:legacy"
    alignment.validate_authoritative_records(sentences)

    words_after = adapter.db.get_words_by_session(zid, parse_json=True)
    assert [w["id"] for w in words_after] == words_before_ids
    assert [w["sentence_index"] for w in words_after] == [1, 1]
    assert words_after[0]["word_destination"] == "привет"
    assert words_after[0]["word_provenance"] == "cached:legacy_word"
    assert words_after[0]["leitner_box"] == 3
    assert words_after[0]["selected"] == 1
    assert words_after[0]["extra_fields"] == {"Note": "keep-me"}
    assert words_after[1]["word_destination"] == "мир"

    # overview_selections untouched
    assert adapter.get_overview_selections(zid) == {0}

    # client_view_state updated atomically and passes strict ClientViewAdapter validation
    view = ClientViewAdapter(adapter.db).get(zid, client_id)
    assert view["revision"] == 3
    assert view["cleared"] is False
    assert view["active_tokens"] == [{"visual_idx": 0, "sentence_idx": 1, "token_order": "0", "lemma": "hallo"}]

    # Canonical retext and reword now succeed on the adapted session.
    retext_res = arbiter.retext_session(session_zid=zid, language="de", text_mode="single")
    assert retext_res["status"] == "success"
    assert adapter.db.get_sentences_by_session(zid)[0]["sentence_destination"] == "google: Hallo Welt."

    with enrichment_engine(config) as engine:
        engine.release.set()
        reword_res = arbiter.reword_session(
            session_zid=zid,
            target_coordinates=[{"sentence_idx": 1, "token_order": 0}],
            language="de",
        )
    assert reword_res["status"] == "success"
    reworded = {w["token_order"]: w for w in adapter.db.get_words_by_session(zid)}
    assert reworded[0]["word_destination"] == "trans_hallo"
    assert reworded[1]["word_destination"] == "мир"
    assert adapter.db.get_words_by_session("20261108113001") == unrelated_apple


def test_legacy_zero_index_duplicate_0_and_1_merges_atomically(controller_world):
    """Scenario: Existing legacy identity (historical split 0_and_1 -> 1 merge)."""
    _, server = controller_world
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    arbiter = server.arbiter
    arbiter.storage_adapter = adapter

    zid = "20261120213011"
    _seed_legacy_zero_index_session(
        adapter,
        zid,
        sentences=[
            {
                "session_zid": zid,
                "sentence_index": 0,
                "sentence_source": "Hallo Welt",
                "sentence_destination": None,
                "text_provenance": None,
            },
            {
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": "Hallo Welt",
                "sentence_destination": "Привет мир",
                "text_provenance": "live:google",
            },
        ],
        words=[
            {"session_zid": zid, "sentence_index": 0, "token_order": 0, "quotation": "Hallo", "lemma": "hallo", "word_destination": "привет"},
            {"session_zid": zid, "sentence_index": 0, "token_order": 1, "quotation": "Welt", "lemma": "welt", "word_destination": "мир"},
        ],
        overview_tokens=(1,),
    )

    result = adapter.adapt_legacy_zero_index_session(zid)
    assert result == {
        "session_zid": zid,
        "adapted": True,
        "mode": "merge",
        "moved_words": 2,
        "updated_views": 0,
    }

    sentences = adapter.db.get_sentences_by_session(zid)
    assert len(sentences) == 1
    assert sentences[0]["sentence_index"] == 1
    assert sentences[0]["sentence_destination"] == "Привет мир"
    assert sentences[0]["text_provenance"] == "live:google"
    alignment.validate_authoritative_records(sentences)

    words = adapter.db.get_words_by_session(zid)
    assert [(w["sentence_index"], w["token_order"]) for w in words] == [(1, 0), (1, 1)]
    assert adapter.get_overview_selections(zid) == {1}

    retext_res = arbiter.retext_session(session_zid=zid, language="de", text_mode="single")
    assert retext_res["status"] == "success"


@pytest.mark.parametrize(
    "case_name,sentences,words,client_views",
    [
        (
            "conflicting_source",
            [
                {"sentence_index": 0, "sentence_source": "Erster Satz.", "sentence_destination": None},
                {"sentence_index": 1, "sentence_source": "Zweiter Satz.", "sentence_destination": "Второе."},
            ],
            [{"sentence_index": 0, "token_order": 0, "quotation": "Erster", "lemma": "erste"}],
            (),
        ),
        (
            "conflicting_destination",
            [
                {"sentence_index": 0, "sentence_source": "Hallo Welt.", "sentence_destination": "Старый."},
                {"sentence_index": 1, "sentence_source": "Hallo Welt.", "sentence_destination": "Новый."},
            ],
            [{"sentence_index": 0, "token_order": 0, "quotation": "Hallo", "lemma": "hallo"}],
            (),
        ),
        (
            "multi_sentence_with_zero",
            [
                {"sentence_index": 0, "sentence_source": "Satz 0.", "sentence_destination": None},
                {"sentence_index": 1, "sentence_source": "Satz 1.", "sentence_destination": None},
                {"sentence_index": 2, "sentence_source": "Satz 2.", "sentence_destination": None},
            ],
            [{"sentence_index": 0, "token_order": 0, "quotation": "Satz", "lemma": "satz"}],
            (),
        ),
        (
            "corrupt_client_view_rolls_back",
            [
                {"sentence_index": 0, "sentence_source": "Hallo Welt.", "sentence_destination": "Привет мир."},
            ],
            [{"sentence_index": 0, "token_order": 0, "quotation": "Hallo", "lemma": "hallo"}],
            (("abcdef0123456789abcdef0123456789", "{corrupt-json"),),
        ),
    ],
)
def test_legacy_zero_index_invalid_cases_reject_without_changes(
    controller_world, case_name, sentences, words, client_views
):
    """Scenario: Existing legacy identity rejects conflicts/corruption with zero DB changes."""
    _, server = controller_world
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    zid = f"202611202130{12 + abs(hash(case_name)) % 80:02d}"

    full_sents = [{"session_zid": zid, **s} for s in sentences]
    full_words = [{"session_zid": zid, **w} for w in words]
    _seed_legacy_zero_index_session(
        adapter, zid, full_sents, full_words, client_views=client_views, overview_tokens=(0,)
    )

    sents_before = adapter.db.get_sentences_by_session(zid)
    words_before = adapter.db.get_words_by_session(zid)
    overview_before = adapter.get_overview_selections(zid)

    with pytest.raises(StructuredError) as exc_info:
        adapter.adapt_legacy_zero_index_session(zid)
    assert exc_info.value.error_code == ErrorCode.INVALID_STATE

    assert adapter.db.get_sentences_by_session(zid) == sents_before
    assert adapter.db.get_words_by_session(zid) == words_before
    assert adapter.get_overview_selections(zid) == overview_before

