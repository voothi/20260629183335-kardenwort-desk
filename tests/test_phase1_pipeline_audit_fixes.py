"""Regression contracts for Phase 1 Desk pipeline, SQLite, and progressive fixes."""
import configparser
from unittest.mock import MagicMock, patch

import pytest

import kardenwort_alignment as alignment
import kardenwort_desk as desk
import kardenwort_progressive_completion as completion
import kardenwort_reword as reword
import kardenwort_translation_validation as validation
from kardenwort_db import KardenwortDB
from kardenwort_events import SubscriberQueue


def _make_sqlite_adapter(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(exist_ok=True)
    db_path = tmp_path / "phase1.db"
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "storage": {"backend": "sqlite", "sqlite_db_path": str(db_path)},
        "project_structure": {"generated_results_dir": str(results_dir)},
    })
    paths = {
        "base_dir": tmp_path,
        "results_dir": results_dir,
        "generated_results_dir": results_dir,
        "sqlite_db_path": db_path,
        "storage_backend": "sqlite",
        "migrations_dir": desk.Path(desk.__file__).resolve().parent / "schemas" / "migrations",
    }
    return desk.SqliteStorageAdapter(cfg, paths, db_path=db_path)


def test_multiline_http_429_classifiers_agree():
    sample = "Error calling Google: HTTP 429\nSecond line real text"
    assert validation.is_stored_rate_limit_failure(sample) is False
    assert validation.is_stored_translation_failure(sample) is False
    assert reword.is_stored_rate_limit_failure(sample) is False
    assert alignment._is_rate_limit_failure(sample) is False
    assert completion._is_rate_limit_failure(sample) is False

    exact = "Error calling Google: HTTP 429 Too Many Requests"
    assert validation.is_stored_rate_limit_failure(exact) is True
    assert reword.is_stored_rate_limit_failure(exact) is True
    assert alignment._is_rate_limit_failure(exact) is True
    assert completion._is_rate_limit_failure(exact) is True


def test_initial_multi_sentence_save_does_not_bleed_token_order_provenance(tmp_path):
    adapter = _make_sqlite_adapter(tmp_path)
    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination",
    ]
    rows = [
        ["0", "Haus", "Haus", "1", "Haus.", "Дом.", "дом"],
        ["0", "Baum", "Baum", "2", "Baum.", "Дерево.", "дерево"],
    ]
    sentences = [
        {"session_zid": "20261126180001", "sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "Дом."},
        {"session_zid": "20261126180001", "sentence_index": 2, "sentence_source": "Baum.", "sentence_destination": "Дерево."},
    ]
    adapter.save_session(
        session_zid="20261126180001",
        slug="multi",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Haus. Baum.",
        headers=headers,
        data_rows=rows,
        sentences=sentences,
        row_provenances={0: "corpus:wordfill", 1: "live:google"},
    )
    words = adapter.db.get_words_by_session("20261126180001")
    assert [(w["sentence_index"], w["token_order"], w["word_provenance"]) for w in words] == [
        (1, 0, "corpus:wordfill"),
        (2, 0, "live:google"),
    ]


def test_indexed_provenances_avoids_sorted_token_order_collision():
    keys = [(1, 2), (1, 0)]
    provs = {("1", "2"): "corpus:wordfill", ("1", "0"): "live:google"}
    indexed = completion.indexed_provenances(keys, provs)
    assert indexed[0] == "corpus:wordfill"
    assert indexed[1] == "live:google"
    assert 2 not in indexed


def test_plan_chunk_word_updates_preserves_already_translated_repeated_lemma():
    rows = [
        ["0", "gehen", "идти (AI)", "1"],
        ["1", "gehen", "", "1"],
        ["2", "gehen", "[FAILED]", "1"],
    ]
    keys = [(1, 0), (1, 1), (1, 2)]
    lemmas = completion.extract_untranslated_lemmas(rows, col_lemma=1, col_word_dest=2)
    assert lemmas == ["gehen"]

    patches, sqlite_updates = completion.plan_chunk_word_updates(
        rows, col_lemma=1, col_word_dest=2, occurrence_keys=keys,
        chunk_trans={"gehen": "ходить"}, chunk_prov="live:google", is_sqlite=True,
    )
    assert [p[0] for p in patches] == [1, 2]
    assert [u["token_order"] for u in sqlite_updates] == [1, 2]


def test_extract_untranslated_lemmas_recovers_failures_and_skeletons():
    rows = [
        ["0", "Haus", "дом"],
        ["1", "Baum", "[FAILED]"],
        ["2", "Kind", "[Error 500]"],
        ["3", "Buch", "HTTP 429 Too Many Requests"],
        ["4", "Tag", '<span class="skeleton-loader">...</span>'],
    ]
    assert completion.extract_untranslated_lemmas(rows, col_lemma=1, col_word_dest=2) == [
        "Baum", "Kind", "Buch", "Tag"
    ]


def test_committed_translation_map_filters_failures_and_bools():
    sentences = [
        {"sentence_index": True, "sentence_destination": "bad bool"},
        {"sentence_index": 1, "sentence_destination": "HTTP 429 Too Many Requests"},
        {"sentence_index": 2, "sentence_destination": "Clean translation"},
        {"sentence_index": 3, "sentence_destination": "[FAILED]"},
    ]
    assert completion.committed_translation_map(sentences) == {1: "Clean translation"}


def test_translate_text_impl_offline_fallback_does_not_jump_to_argos_when_online_and_cooled_down(tmp_path):
    desk.clear_provider_cooldowns()
    cfg = configparser.ConfigParser()
    cfg.add_section(desk.SEC_PIPELINE)
    cfg.set(desk.SEC_PIPELINE, "text_provider_chain", "google, argos")
    cfg.set(desk.SEC_PIPELINE, "failover_strategy", "offline_fallback")
    cfg.set(desk.SEC_PIPELINE, "fast_connectivity_check_ips", "8.8.8.8")
    paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    desk.record_provider_cooldown("google", duration=60.0, config=cfg, resolved_paths=paths, task_type="text")
    mock_argos = MagicMock(return_value="Argos text")
    try:
        with patch("kardenwort_desk.is_network_online_multi", return_value=True), \
             patch("kardenwort_desk.run_argos_translation", mock_argos):
            with pytest.raises(desk.TranslationException) as exc_info:
                desk._translate_text_impl("Hello", "en", "de", cfg, paths)
            assert exc_info.value.envelope["code"] == "ERR_PROVIDER_COOLDOWN"
            mock_argos.assert_not_called()
    finally:
        desk.clear_provider_cooldowns()


def test_resolve_export_selection_explicit_empty_token_orders_and_contradictions():
    headers = ["SentenceSourceIndex", "TokenOrder", "WordSource"]
    rows = [["1", "0", "Haus"], ["1", "1", "Baum"]]
    assert reword.resolve_export_selection(rows, headers, token_orders=[]) == ()
    with pytest.raises(reword.InvalidRewordTarget):
        reword.resolve_export_selection(rows, headers, targets=[], selected_rows=[0], allow_unmapped_indices=True)
    with pytest.raises(reword.InvalidRewordTarget):
        reword.resolve_export_selection(
            rows, headers,
            targets=[{"sentence_idx": 1, "token_order": 0}],
            token_orders=[1],
        )


def test_update_sentence_translation_fail_fast_and_direct_word_failure_filtering(tmp_path):
    adapter = _make_sqlite_adapter(tmp_path)
    adapter.save_session(
        session_zid="20261126180002",
        slug="failfast",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"session_zid": "20261126180002", "sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "Дом."}],
        words=[{
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "Haus",
            "lemma": "Haus",
            "word_destination": "HTTP 429 Too Many Requests",
            "word_destination_inflected": "[FAILED]",
        }],
    )
    words = adapter.db.get_words_by_session("20261126180002")
    assert words[0]["word_destination"] is None
    assert words[0]["word_destination_inflected"] is None

    assert adapter.update_sentence_translation("20261126180002", True, "bad") is False
    with pytest.raises(ValueError, match="Unsupported sentence target_field"):
        adapter.update_sentence_translation("20261126180002", 1, "text", target_field="invalid_col")


def test_format_update_rows_dict_sanitizes_occurrence_data():
    headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex"]
    rows = [["0", "Haus", "дом", "1"]]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "sentence_index": "SentenceSourceIndex"}
    row_occurrences = {("1", "0"): {"0": {"trans": "HTTP 429 Too Many Requests", "pos": "n."}}}
    formatted = desk.format_update_rows_dict(rows, headers, role_fields, row_occurrences=row_occurrences)
    assert formatted["0"]["occurrence_data"]["0"]["trans"] == ""


def test_db_atomic_purge_returning_and_unbounded_subscriber_queue(tmp_path):
    adapter = _make_sqlite_adapter(tmp_path)
    adapter.save_session(
        session_zid="20261126180003",
        slug="purge",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"session_zid": "20261126180003", "sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "Дом."}],
        words=[],
    )
    adapter.db.soft_delete_session("20261126180003")
    purged = adapter.db.purge_deleted_sessions_returning()
    assert purged == ["20261126180003"]
    assert adapter.db.get_session("20261126180003") is None

    q = SubscriberQueue(maxsize=0)
    q.publish({"type": "update", "session_zid": "s1"})
    assert q.get_nowait()["type"] == "update"
