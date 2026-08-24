"""
tests/test_sqlite_mutations.py - Test suite for SQLite atomic mutations, cell updates,
selection toggles, cascading deletion, and session lifecycle CLI commands.
"""

import os
import sys
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import kardenwort_desk as desk
from kardenwort_desk import (
    SqliteStorageAdapter,
    StorageRouter,
    get_storage_adapter,
    core_edit_save,
    StructuredError,
    ErrorCode,
)
from kardenwort_db import KardenwortDB


@pytest.fixture
def temp_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "kardenwort.db"

    # Create dummy anki-mapping.ini
    anki_mapping = tmp_path / "anki-mapping.ini"
    anki_mapping.write_text(
        """[fields]
Quotation = quotation
WordSource = lemma
WordSourceInflectedForm = inflected_form
WordDestination = word_destination
WordSourceMorphologyAI = morphology
WordSourceIPA = ipa
DeskSelected = selected
SentenceSource = sentence_source
SentenceDestination = sentence_destination

[desk_columns]
quotation = quotation
lemma = WordSource
inflected_form = WordSourceInflectedForm
word_translation = WordDestination
morphology = WordSourceMorphologyAI
ipa = WordSourceIPA
selected = DeskSelected
sentence_source = SentenceSource
sentence_destination = SentenceDestination

[desk_editable]
editable_columns = WordDestination,WordSource,WordSourceInflectedForm,WordSourceMorphologyAI,WordSourceIPA,DeskSelected
""",
        encoding="utf-8",
    )

    resolved_paths = {
        "sqlite_db_path": str(db_path),
        "results_dir": str(results_dir),
        "base_dir": str(tmp_path),
        "anki_mapping_file": str(anki_mapping),
        "storage_backend": "sqlite",
        "storage_fallback_to_tsv": False,
    }

    config = MagicMock()
    config.get.side_effect = lambda sec, opt, fallback=None: {
        ("storage", "backend"): "sqlite",
        ("storage", "sqlite_db_path"): str(db_path),
        ("settings", "results_dir"): str(results_dir),
        ("settings", "default_language"): "en",
        ("settings", "default_target_language"): "ru",
        ("settings", "anki_mapping_file"): str(anki_mapping),
    }.get((sec, opt), fallback)
    config.getboolean.side_effect = lambda sec, opt, fallback=False: {
        ("storage", "fallback_to_tsv"): False,
    }.get((sec, opt), fallback)
    config.getint.side_effect = lambda sec, opt, fallback=0: fallback
    config.has_option.side_effect = lambda sec, opt: (sec, opt) in [
        ("storage", "backend"),
        ("storage", "sqlite_db_path"),
        ("settings", "results_dir"),
    ]

    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "results_dir": results_dir,
        "resolved_paths": resolved_paths,
        "config": config,
    }


def _seed_sample_session(adapter: SqliteStorageAdapter, session_zid: str = "20260821190000"):
    headers = [
        "Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination",
        "WordSourceMorphologyAI", "WordSourceIPA", "DeskSelected",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination"
    ]
    data_rows = [
        ["cats", "cat", "cats", "кошки", "N;PL", "kæts", "0", "1", "Cats sleep.", "Кошки спят."],
        ["sleep", "sleep", "sleep", "спят", "V;PRES", "sliːp", "1", "1", "Cats sleep.", "Кошки спят."],
        ["dogs", "dog", "dogs", "собаки", "N;PL", "dɒɡz", "0", "2", "Dogs bark.", "Собаки лают."],
    ]
    comments = ["# Sample test session"]
    adapter.save_session(
        session_zid=session_zid,
        slug="test-sample",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Cats sleep. Dogs bark.",
        headers=headers,
        data_rows=data_rows,
        comments=comments,
    )


class TestSqliteAtomicMutations:
    def test_sqlite_update_word(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190100"
        _seed_sample_session(adapter, session_zid)

        # 1. Update word_destination for token_order 0
        ok = adapter.update_word(session_zid, sentence_idx=1, token_order=0, field="WordDestination", value="коты")
        assert ok is True

        # 2. Update morphology and ipa
        ok_morph = adapter.update_word(session_zid, sentence_idx=None, token_order=0, field="morphology", value="Noun;Plural")
        ok_ipa = adapter.update_word(session_zid, sentence_idx=None, token_order=0, field="ipa", value="/kæts/")
        assert ok_morph is True
        assert ok_ipa is True

        # 3. Verify in database
        words = adapter.db.get_words_by_session(session_zid)
        cat_word = words[0]
        assert cat_word["word_destination"] == "коты"
        assert cat_word["morphology"] == "Noun;Plural"
        assert cat_word["ipa"] == "/kæts/"

        # 4. Update custom field in extra_fields
        ok_custom = adapter.update_word(session_zid, sentence_idx=None, token_order=0, field="CustomNote", value="Important word")
        assert ok_custom is True
        words_after = adapter.db.get_words_by_session(session_zid)
        assert words_after[0]["extra_fields"]["CustomNote"] == "Important word"

    def test_sqlite_update_word_selection(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190200"
        _seed_sample_session(adapter, session_zid)

        # Token 0 is initially selected = 0
        words = adapter.db.get_words_by_session(session_zid)
        assert words[0]["selected"] == 0

        # Toggle to 1
        ok = adapter.update_word_selection(session_zid, sentence_idx=1, token_order=0, selected=1)
        assert ok is True
        words = adapter.db.get_words_by_session(session_zid)
        assert words[0]["selected"] == 1

        # Toggle back to 0 using boolean False
        ok = adapter.update_word_selection(session_zid, sentence_idx=None, token_order=0, selected=False)
        assert ok is True
        words = adapter.db.get_words_by_session(session_zid)
        assert words[0]["selected"] == 0

    def test_sqlite_update_sentence_translation(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190300"
        _seed_sample_session(adapter, session_zid)

        # Update sentence 1 translation
        ok = adapter.update_sentence_translation(session_zid, sentence_index=1, text="Котики спят сладко.")
        assert ok is True

        sentences = adapter.db.get_sentences_by_session(session_zid)
        assert len(sentences) == 2
        sent1 = next(s for s in sentences if s["sentence_index"] == 1)
        assert sent1["sentence_destination"] == "Котики спят сладко."

        # Restored session reflects updated sentence translation across linked rows
        restored = adapter.restore_session(session_zid)
        row0 = restored["data_rows"][0]
        # SentenceDestination is at index 9
        assert "Котики спят сладко." in row0

    def test_sqlite_batch_update_words(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190400"
        _seed_sample_session(adapter, session_zid)

        updates = [
            {"token_order": 0, "updates": {"word_destination": "котята", "selected": 1}},
            {"token_order": 1, "updates": {"word_destination": "дремлют"}},
            {"token_order": 2, "updates": {"word_destination": "пёсики", "selected": 1}},
        ]
        count = adapter.batch_update_words(session_zid, updates)
        assert count == 3

        words = adapter.db.get_words_by_session(session_zid)
        assert words[0]["word_destination"] == "котята"
        assert words[0]["selected"] == 1
        assert words[1]["word_destination"] == "дремлют"
        assert words[2]["word_destination"] == "пёсики"
        assert words[2]["selected"] == 1


class TestCoreEditSaveSqlite:
    def test_core_edit_save_atomic_sqlite(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190500"
        _seed_sample_session(adapter, session_zid)

        # Get initial fingerprint
        restored = adapter.restore_session(session_zid)
        initial_fp = desk.compute_content_fingerprint(restored["data_rows"])

        # Execute edit deltas through core_edit_save
        deltas = [
            {"row_id": 0, "column": "WordDestination", "value": "котейки"},
            {"row_id": 0, "column": "DeskSelected", "value": "1"},
        ]
        res = core_edit_save(
            tsv_path_or_session=session_zid,
            deltas=deltas,
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            fingerprint=initial_fp,
        )

        assert res["status"] == "success"
        assert res["session_zid"] == session_zid
        assert res["fingerprint"] != initial_fp

        # Verify persisted changes
        words = adapter.db.get_words_by_session(session_zid)
        assert words[0]["word_destination"] == "котейки"
        assert words[0]["selected"] == 1

    def test_core_edit_save_stale_fingerprint_rejected(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190600"
        _seed_sample_session(adapter, session_zid)

        deltas = [{"row_id": 0, "column": "WordDestination", "value": "котейки"}]
        with pytest.raises(StructuredError) as exc_info:
            core_edit_save(
                tsv_path_or_session=session_zid,
                deltas=deltas,
                config=temp_env["config"],
                resolved_paths=temp_env["resolved_paths"],
                fingerprint="stale_invalid_hash_12345",
            )
        assert exc_info.value.error_code == ErrorCode.ROW_STALE


class TestCascadingDeletionAndLifecycle:
    def test_delete_session_cascades(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821190700"
        _seed_sample_session(adapter, session_zid)

        # Verify existence
        assert adapter.db.get_session(session_zid) is not None
        assert len(adapter.db.get_sentences_by_session(session_zid)) == 2
        assert len(adapter.db.get_words_by_session(session_zid)) == 3

        # Delete session
        deleted = adapter.delete_session(session_zid)
        assert deleted is True

        # Verify cascading deletion across all tables
        assert adapter.db.get_session(session_zid) is None
        assert len(adapter.db.get_sentences_by_session(session_zid)) == 0
        assert len(adapter.db.get_words_by_session(session_zid)) == 0

    def test_stub_parent_cleanup_sqlite(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        parent_zid = "20260821190800"
        _seed_sample_session(adapter, parent_zid)

        # Emulate stub parent mode deletion
        adapter.delete_session(parent_zid)

        # Verify parent session is wiped from SQLite
        assert adapter.db.get_session(parent_zid) is None
        assert len(adapter.db.get_words_by_session(parent_zid)) == 0


class TestCliLifecycleCommands:
    def test_cli_list_sessions(self, temp_env, capsys):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        _seed_sample_session(adapter, "20260821190901")
        _seed_sample_session(adapter, "20260821190902")

        args = MagicMock()
        args.config = None
        args.limit = None
        args.zid = None
        args.json_output = True
        args.json = True

        with patch("kardenwort_desk.load_config", return_value=(temp_env["config"], temp_env["resolved_paths"], None, None)):
            with pytest.raises(SystemExit) as exc:
                desk.cmd_list_sessions(args)
            assert exc.value.code == 0

    def test_cli_delete_session(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821191000"
        _seed_sample_session(adapter, session_zid)

        args = MagicMock()
        args.config = None
        args.zid = session_zid
        args.json_output = True
        args.json = True

        with patch("kardenwort_desk.load_config", return_value=(temp_env["config"], temp_env["resolved_paths"], None, None)):
            with pytest.raises(SystemExit) as exc:
                desk.cmd_delete_session(args)
            assert exc.value.code == 0

        assert adapter.db.get_session(session_zid) is None

    def test_cli_cleanup_db(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260821191100"
        _seed_sample_session(adapter, session_zid)

        args = MagicMock()
        args.config = None
        args.older_than = 30.0
        args.zid = None
        args.json_output = True
        args.json = True

        with patch("kardenwort_desk.load_config", return_value=(temp_env["config"], temp_env["resolved_paths"], None, None)):
            with pytest.raises(SystemExit) as exc:
                desk.cmd_cleanup_db(args)
            assert exc.value.code == 0

    def test_cli_vacuum_db(self, temp_env):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        _seed_sample_session(adapter, "20260821191200")

        args = MagicMock()
        args.config = None
        args.zid = None
        args.json_output = True
        args.json = True

        with patch("kardenwort_desk.load_config", return_value=(temp_env["config"], temp_env["resolved_paths"], None, None)):
            with pytest.raises(SystemExit) as exc:
                desk.cmd_vacuum_db(args)
            assert exc.value.code == 0

    def test_sqlite_enrich_session_intellifiller_frequency_sort_parity(self, temp_env, tmp_path):
        adapter = SqliteStorageAdapter(
            config=temp_env["config"],
            resolved_paths=temp_env["resolved_paths"],
            db_path=temp_env["db_path"],
        )
        session_zid = "20260824223000"

        # 1. Frequency index where 'apple' is rank 1, 'zebra' is rank 2
        idx_file = tmp_path / "en_index.txt"
        idx_file.write_text("apple\nzebra\n", encoding="utf-8")

        # Configure config to return en_lemma_index
        orig_side_effect = temp_env["config"].get.side_effect
        def custom_config_get(sec, opt, fallback=None):
            if (sec, opt) == ("languages", "en_lemma_index"):
                return str(idx_file)
            return orig_side_effect(sec, opt, fallback=fallback)
        temp_env["config"].get.side_effect = custom_config_get

        # 2. Seed session with zebra as token 0, apple as token 1
        headers = [
            "Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination",
            "WordSourceMorphologyAI", "WordSourceIPA", "DeskSelected",
            "SentenceSourceIndex", "SentenceSource", "SentenceDestination"
        ]
        data_rows = [
            ["zebra", "zebra", "zebra", "", "", "", "0", "1", "A zebra is striped.", ""],
            ["apple", "apple", "apple", "", "", "", "0", "1", "An apple is sweet.", ""],
        ]
        adapter.save_session(
            session_zid=session_zid,
            slug="parity-test",
            source_language="en",
            target_language="ru",
            text_mode="single",
            source_raw_text="A zebra is striped. An apple is sweet.",
            headers=headers,
            data_rows=data_rows,
            comments=["# test"],
        )

        # 3. Mock run_headless_intellifiller
        # In frequency sorted order: row 0 is 'apple', row 1 is 'zebra'
        # When caller selects row 0 (apple), intellifiller enriches row 0
        def fake_headless_ifiller(tsv_path, *args, **kwargs):
            comments, headers, data_rows = desk.load_tsv_rows(tsv_path)
            assert data_rows[0][headers.index("WordSource")] == "apple"
            assert data_rows[1][headers.index("WordSource")] == "zebra"

            dest_idx = headers.index("WordDestination")
            data_rows[0][dest_idx] = "яблоко"
            desk.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            return True

        with patch("kardenwort_desk.run_headless_intellifiller", side_effect=fake_headless_ifiller):
            success = adapter.enrich_session_intellifiller(
                session_zid=session_zid,
                prompt_name="morphology_and_ipa",
                selected_rows=[0],
            )
            assert success is True

        # 4. Verify in DB that apple (token_order 1) was updated, zebra (token_order 0) remains unchanged
        db_words = adapter.db.get_words_by_session(session_zid)
        w_zebra = next(w for w in db_words if w["lemma"] == "zebra")
        w_apple = next(w for w in db_words if w["lemma"] == "apple")

        assert w_apple["word_destination"] == "яблоко"
        assert w_zebra["word_destination"] in ("", None)

