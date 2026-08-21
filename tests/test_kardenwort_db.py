import os
import sys
import json
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timezone

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from kardenwort_db import (
    KardenwortDB,
    KardenwortConnection,
    QueryExecutionError,
    QuerySecurityError,
)


@pytest.fixture
def temp_db(tmp_path):
    """Fixture providing a fresh KardenwortDB instance with migration directory."""
    db_file = tmp_path / "data" / "test_kardenwort.db"
    migrations_dir = root_dir / "schemas" / "migrations"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    db = KardenwortDB(
        db_path=db_file,
        migrations_dir=migrations_dir,
        resolved_paths={"results_dir": results_dir},
    )
    return db


# ---------------------------------------------------------------------------
# Section 1: Database Initialization & Pragmas
# ---------------------------------------------------------------------------
def test_db_init_and_pragmas(temp_db):
    """Verify WAL mode, normal synchronous, busy timeout, and foreign keys on connection."""
    conn = temp_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        assert cursor.fetchone()[0].lower() == "wal"

        cursor.execute("PRAGMA synchronous;")
        # NORMAL synchronous is 1
        assert cursor.fetchone()[0] in (1, "NORMAL", "normal")

        cursor.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1

        cursor.execute("PRAGMA busy_timeout;")
        assert cursor.fetchone()[0] == temp_db.busy_timeout_ms
        cursor.close()
    finally:
        conn.close()


def test_transaction_context_manager(temp_db):
    """Verify atomic transaction block with context manager."""
    temp_db.run_migrations()

    # Successful insert inside context manager
    with temp_db.get_connection() as conn:
        conn.execute("INSERT INTO sessions (zid, slug, source_language, source_raw_text) VALUES ('20260821000001', 's1', 'en', 'Hello');")

    sess = temp_db.get_session("20260821000001")
    assert sess is not None
    assert sess["slug"] == "s1"

    # Transaction helper
    with temp_db.transaction() as conn:
        conn.execute("INSERT INTO sessions (zid, slug, source_language, source_raw_text) VALUES ('20260821000002', 's2', 'en', 'World');")

    sess2 = temp_db.get_session("20260821000002")
    assert sess2 is not None
    assert sess2["slug"] == "s2"


def test_transaction_rollback_on_error(temp_db):
    """Verify automatic transaction rollback when an exception occurs inside context block."""
    temp_db.run_migrations()

    # Pre-populate session
    temp_db.insert_session({"zid": "20260821100001", "slug": "base", "source_language": "de", "source_raw_text": "Hallo"})

    # Attempt transaction that fails halfway
    with pytest.raises(ValueError):
        with temp_db.get_connection() as conn:
            conn.execute("INSERT INTO sessions (zid, slug, source_language, source_raw_text) VALUES ('20260821100002', 'should_rollback', 'de', 'Test');")
            raise ValueError("Simulated unexpected failure during processing")

    # The uncommitted session must not exist
    assert temp_db.get_session("20260821100002") is None
    # The pre-populated session must remain intact
    assert temp_db.get_session("20260821100001") is not None


# ---------------------------------------------------------------------------
# Section 2: Migration Runner & _migrations Tracking
# ---------------------------------------------------------------------------
def test_migration_runner_initial_schema(temp_db):
    """Verify deterministic migration execution and _migrations table recording."""
    res = temp_db.run_migrations()
    assert res["ok"] is True
    assert "001_initial_schema.sql" in res["applied"]
    assert res["total_applied"] >= 1

    # Check status
    status = temp_db.get_status()
    assert status["ok"] is True
    assert "001_initial_schema.sql" in status["migrations_applied"]
    assert "sessions" in status["tables"]
    assert "sentences" in status["tables"]
    assert "words" in status["tables"]
    assert "_migrations" in status["tables"]

    # Running migrations again should skip already applied migrations without error
    res_second = temp_db.run_migrations()
    assert res_second["ok"] is True
    assert len(res_second["applied"]) == 0
    assert "001_initial_schema.sql" in res_second["already_applied"]


def test_migration_runner_custom_scripts(tmp_path):
    """Verify sequential execution in alphabetical order for custom migrations."""
    db_file = tmp_path / "custom.db"
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)

    # Write 001 and 002 migration files
    (mig_dir / "001_alpha.sql").write_text("CREATE TABLE alpha (id INTEGER PRIMARY KEY, name TEXT);", encoding="utf-8")
    (mig_dir / "002_beta.sql").write_text("CREATE TABLE beta (id INTEGER PRIMARY KEY, alpha_id INTEGER REFERENCES alpha(id));", encoding="utf-8")

    db = KardenwortDB(db_path=db_file, migrations_dir=mig_dir)
    res = db.run_migrations()
    assert res["ok"] is True
    assert res["applied"] == ["001_alpha.sql", "002_beta.sql"]

    status = db.get_status()
    assert "alpha" in status["tables"]
    assert "beta" in status["tables"]


def test_migration_runner_failure_rollback(tmp_path):
    """Verify failed migration rolls back cleanly without recording into _migrations."""
    db_file = tmp_path / "fail.db"
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)

    (mig_dir / "001_good.sql").write_text("CREATE TABLE good (id INT PRIMARY KEY);", encoding="utf-8")
    (mig_dir / "002_bad.sql").write_text("CREATE TABLE bad (id INT PRIMARY KEY); SYNTAX ERROR INVALID SQL;", encoding="utf-8")

    db = KardenwortDB(db_path=db_file, migrations_dir=mig_dir)
    with pytest.raises(QueryExecutionError):
        db.run_migrations()

    status = db.get_status()
    assert "001_good.sql" in status["migrations_applied"]
    assert "002_bad.sql" not in status["migrations_applied"]
    assert "good" in status["tables"]
    assert "bad" not in status["tables"]


# ---------------------------------------------------------------------------
# Section 3: Normalized Relational CRUD & Foreign Key Cascading
# ---------------------------------------------------------------------------
def test_crud_sessions(temp_db):
    """Test insert, read, list, update, and delete on sessions."""
    temp_db.run_migrations()

    sess_data = {
        "zid": "20260821120000",
        "slug": "kafka-verwandlung",
        "source_language": "de",
        "target_language": "en",
        "text_mode": "multi",
        "source_raw_text": "Als Gregor Samsa eines Morgens erwachte...",
    }

    zid = temp_db.insert_session(sess_data)
    assert zid == "20260821120000"

    # Get session
    retrieved = temp_db.get_session("20260821120000")
    assert retrieved is not None
    assert retrieved["slug"] == "kafka-verwandlung"
    assert retrieved["source_language"] == "de"
    assert retrieved["text_mode"] == "multi"

    # List sessions
    sessions_list = temp_db.list_sessions()
    assert len(sessions_list) == 1
    assert sessions_list[0]["zid"] == "20260821120000"

    # Update session
    updated = temp_db.update_session("20260821120000", {"slug": "kafka-metamorphosis", "target_language": "fr"})
    assert updated is True

    retrieved2 = temp_db.get_session("20260821120000")
    assert retrieved2["slug"] == "kafka-metamorphosis"
    assert retrieved2["target_language"] == "fr"

    # Delete session
    deleted = temp_db.delete_session("20260821120000")
    assert deleted is True
    assert temp_db.get_session("20260821120000") is None


def test_crud_sentences_and_words_with_json_extra(temp_db):
    """Test insert, query, update of sentences and words with JSON extra_fields serialization."""
    temp_db.run_migrations()

    sess_zid = "20260821130000"
    temp_db.insert_session({
        "zid": sess_zid,
        "slug": "ein-beispiel",
        "source_language": "de",
        "target_language": "en",
        "source_raw_text": "Das ist ein schöner Tag.",
    })

    # Insert sentence
    temp_db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 0,
        "sentence_source": "Das ist ein schöner Tag.",
        "sentence_destination": "That is a beautiful day.",
        "sentence_source_ipa": "das ɪst aɪn ˈʃøːnɐ taːk",
    })

    sentences = temp_db.get_sentences_by_session(sess_zid)
    assert len(sentences) == 1
    assert sentences[0]["sentence_source"] == "Das ist ein schöner Tag."

    # Insert words with extra_fields dict
    extra_dict = {"anki_note_id": 12345678, "custom_tag": "A1", "morphemes": ["schön", "er"]}
    word_id = temp_db.insert_word({
        "session_zid": sess_zid,
        "sentence_index": 0,
        "token_order": 3,
        "quotation": "schöner",
        "inflected_form": "schöner",
        "lemma": "schön",
        "pos": "ADJ",
        "morphology": "Pos|Masc|Nom|Sg",
        "ipa": "ˈʃøːnɐ",
        "word_destination": "beautiful",
        "classification_goethe": "A1",
        "extra_fields": extra_dict,
    })
    assert word_id > 0

    # Retrieve word and verify JSON deserialization
    retrieved_word = temp_db.get_word(word_id, parse_json=True)
    assert retrieved_word is not None
    assert retrieved_word["quotation"] == "schöner"
    assert retrieved_word["lemma"] == "schön"
    assert isinstance(retrieved_word["extra_fields"], dict)
    assert retrieved_word["extra_fields"]["anki_note_id"] == 12345678
    assert retrieved_word["extra_fields"]["morphemes"] == ["schön", "er"]

    # Update word
    upd_ok = temp_db.update_word(word_id, {"selected": 1, "leitner_box": 2})
    assert upd_ok is True
    updated_word = temp_db.get_word(word_id)
    assert updated_word["selected"] == 1
    assert updated_word["leitner_box"] == 2


def test_cascading_deletion(temp_db):
    """Verify that deleting a session cleanly cascades and removes associated sentences and words."""
    temp_db.run_migrations()

    sess_zid = "20260821140000"
    temp_db.insert_session({
        "zid": sess_zid,
        "slug": "cascade-test",
        "source_language": "en",
        "source_raw_text": "Apple is good. Banana is sweet.",
    })

    # Insert 2 sentences
    temp_db.insert_sentences([
        {"session_zid": sess_zid, "sentence_index": 0, "sentence_source": "Apple is good."},
        {"session_zid": sess_zid, "sentence_index": 1, "sentence_source": "Banana is sweet."},
    ])

    # Insert words for both sentences
    temp_db.insert_words([
        {"session_zid": sess_zid, "sentence_index": 0, "token_order": 0, "quotation": "Apple", "lemma": "apple"},
        {"session_zid": sess_zid, "sentence_index": 0, "token_order": 1, "quotation": "is", "lemma": "be"},
        {"session_zid": sess_zid, "sentence_index": 1, "token_order": 0, "quotation": "Banana", "lemma": "banana"},
    ])

    # Verify counts before deletion
    status_before = temp_db.get_status()
    assert status_before["tables"]["sessions"] == 1
    assert status_before["tables"]["sentences"] == 2
    assert status_before["tables"]["words"] == 3

    # Delete session
    del_ok = temp_db.delete_session(sess_zid)
    assert del_ok is True

    # Verify cascading deletion
    status_after = temp_db.get_status()
    assert status_after["tables"]["sessions"] == 0
    assert status_after["tables"]["sentences"] == 0
    assert status_after["tables"]["words"] == 0

    # Foreign key check should be completely clean
    integrity = temp_db.check_integrity()
    assert integrity["ok"] is True
    assert integrity["foreign_key_violations"] == []


def test_case_insensitive_queries_collate_nocase(temp_db):
    """Verify case-insensitive search on lemma and quotation backed by COLLATE NOCASE indexes."""
    temp_db.run_migrations()

    sess_zid = "20260821150000"
    temp_db.insert_session({
        "zid": sess_zid,
        "slug": "case-test",
        "source_language": "de",
        "source_raw_text": "Haus haus HAUS",
    })
    temp_db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 0,
        "sentence_source": "Haus haus HAUS",
    })
    temp_db.insert_words([
        {"session_zid": sess_zid, "sentence_index": 0, "token_order": 0, "quotation": "Haus", "lemma": "Haus"},
        {"session_zid": sess_zid, "sentence_index": 0, "token_order": 1, "quotation": "haus", "lemma": "haus"},
        {"session_zid": sess_zid, "sentence_index": 0, "token_order": 2, "quotation": "HAUS", "lemma": "HAUS"},
    ])

    # Query with lowercase 'haus'
    res_lower = temp_db.find_words_by_lemma("haus")
    assert len(res_lower) == 3

    # Query with uppercase 'HAUS'
    res_upper = temp_db.find_words_by_lemma("HAUS")
    assert len(res_upper) == 3

    # Query quotation with mixed case 'HaUs'
    res_quot = temp_db.find_words_by_quotation("HaUs")
    assert len(res_quot) == 3


def test_session_bundle_atomic_save_and_retrieve(temp_db):
    """Verify atomic bundle saving and retrieval."""
    temp_db.run_migrations()

    sess_zid = "20260821160000"
    session_data = {
        "zid": sess_zid,
        "slug": "bundle-test",
        "source_language": "en",
        "target_language": "de",
        "source_raw_text": "Quick test.",
    }
    sentences_data = [
        {"sentence_index": 0, "sentence_source": "Quick test.", "sentence_destination": "Schneller Test."}
    ]
    words_data = [
        {"sentence_index": 0, "token_order": 0, "quotation": "Quick", "lemma": "quick", "pos": "ADJ", "extra_fields": {"tag": "fast"}},
        {"sentence_index": 0, "token_order": 1, "quotation": "test", "lemma": "test", "pos": "NOUN", "extra_fields": {"tag": "exam"}},
    ]

    bundle_zid = temp_db.save_session_bundle(session_data, sentences_data, words_data)
    assert bundle_zid == sess_zid

    bundle = temp_db.get_session_bundle(sess_zid)
    assert bundle is not None
    assert bundle["session"]["slug"] == "bundle-test"
    assert len(bundle["sentences"]) == 1
    assert bundle["sentences"][0]["sentence_destination"] == "Schneller Test."
    assert len(bundle["words"]) == 2
    assert bundle["words"][0]["extra_fields"] == {"tag": "fast"}
    assert bundle["words"][1]["extra_fields"] == {"tag": "exam"}


def test_concurrent_readers_and_writer_under_wal(temp_db):
    """Verify concurrency resilience under WAL mode: readers do not block writers and vice versa."""
    import threading

    temp_db.run_migrations()
    sess_zid = "20260821170000"
    temp_db.insert_session({
        "zid": sess_zid,
        "slug": "concurrency",
        "source_language": "en",
        "source_raw_text": "Concurrent test.",
    })
    temp_db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 0,
        "sentence_source": "Concurrent test.",
    })

    errors = []

    def reader_worker():
        try:
            for _ in range(25):
                res = temp_db.query_readonly("SELECT count(*) as cnt FROM sessions WHERE zid = ?;", (sess_zid,))
                assert res[0]["cnt"] == 1
        except Exception as e:
            errors.append(e)

    def writer_worker():
        try:
            for i in range(25):
                temp_db.insert_word({
                    "session_zid": sess_zid,
                    "sentence_index": 0,
                    "token_order": i,
                    "quotation": f"word{i}",
                    "lemma": f"word{i}",
                })
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=reader_worker),
        threading.Thread(target=reader_worker),
        threading.Thread(target=writer_worker),
        threading.Thread(target=reader_worker),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    words = temp_db.get_words_by_session(sess_zid)
    assert len(words) == 25
