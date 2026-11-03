"""
tests/test_atomic_session_recovery.py - Deterministic Verification for Atomic Session
Recovery Snapshots and Guarded Cache Publication.

Verifies:
1. Deterministic concurrent writer commits interleaved between sentences and words
   reads in KardenwortDB.get_session_bundle and committed_recovery_snapshot yield
   a single coherent database revision (old-sentence/old-word) with
   conn.in_transaction == True.
2. Failed transaction initiation in KardenwortConnection.__enter__ closes the
   connection and raises without executing the with block in autocommit mode.
3. Concurrent in-memory mutations to arbiter.sessions[zid] during
   committed_recovery_snapshot (data_rows, fingerprint, translatedText) are preserved
   and not overwritten by in-flight storage reads.
"""

import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kardenwort_db import KardenwortConnection, KardenwortDB
from kardenwort_desk import SqliteStorageAdapter
from kardenwort_controller import committed_recovery_snapshot


@pytest.fixture
def test_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "kardenwort.db"

    anki_mapping = tmp_path / "anki-mapping.ini"
    anki_mapping.write_text(
        """[fields]
Quotation = quotation
WordSource = lemma
WordDestination = word_destination
SentenceSource = sentence_source
SentenceDestination = sentence_destination
SentenceSourceIndex = sentence_index
TokenOrder = token_order

[desk_columns]
quotation = Quotation
lemma = WordSource
word_translation = WordDestination
sentence_source = SentenceSource
sentence_destination = SentenceDestination
sentence_index = SentenceSourceIndex
token_order = TokenOrder
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

    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)

    return {
        "db": db,
        "adapter": adapter,
        "config": config,
        "resolved_paths": resolved_paths,
        "db_path": db_path,
    }


def seed_test_session(db, session_zid="20261103210000"):
    with db.get_connection(read_only=False) as conn:
        conn.execute(
            """
            INSERT INTO sessions (zid, slug, source_language, target_language, text_mode, source_raw_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_zid, "test-session", "en", "ru", "single", "The cat sat on the mat."),
        )
        conn.execute(
            """
            INSERT INTO sentences (session_zid, sentence_index, sentence_source, sentence_destination)
            VALUES (?, ?, ?, ?)
            """,
            (session_zid, 1, "The cat sat on the mat.", "old-sentence"),
        )
        conn.execute(
            """
            INSERT INTO words (session_zid, sentence_index, token_order, quotation, lemma, word_destination, extra_fields)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_zid,
                1,
                0,
                "cat",
                "cat",
                "old-word",
                json.dumps({"word_destination": "old-word", "occurrence_data": {}}),
            ),
        )


# ===========================================================================
# 1. Deterministic Interleaved Concurrent Writer Tests
# ===========================================================================

def test_get_session_bundle_atomic_snapshot_under_concurrent_writer(test_env, monkeypatch):
    """
    Verifies that when a concurrent writer commits an update to sentences and words
    between the reader's sentences and words queries in KardenwortDB.get_session_bundle:
    - The read connection executes inside an active transaction (conn.in_transaction == True).
    - The returned bundle is a single coherent snapshot containing wholly old values.
    - Subsequent reads outside the transaction see the new committed revision.
    """
    db = test_env["db"]
    session_zid = "20261103210000"
    seed_test_session(db, session_zid)

    reader_in_transaction_states = []
    writer_committed = []

    orig_cursor = KardenwortConnection.cursor

    def wrapping_cursor(conn_self, *args, **kwargs):
        real_c = orig_cursor(conn_self, *args, **kwargs)

        class InterceptingCursor:
            def execute(self, sql, *a, **kw):
                res = real_c.execute(sql, *a, **kw)
                if "FROM sentences" in sql and conn_self.read_only:
                    reader_in_transaction_states.append(conn_self.in_transaction)
                    # Concurrent writer commits new-sentence and new-word
                    with db.get_connection(read_only=False) as writer_conn:
                        writer_conn.execute(
                            "UPDATE sentences SET sentence_destination = ? WHERE session_zid = ?",
                            ("new-sentence", session_zid),
                        )
                        writer_conn.execute(
                            "UPDATE words SET word_destination = ?, extra_fields = ? WHERE session_zid = ?",
                            (
                                "new-word",
                                json.dumps({"word_destination": "new-word", "occurrence_data": {}}),
                                session_zid,
                            ),
                        )
                    writer_committed.append(True)
                return res

            def fetchone(self):
                return real_c.fetchone()

            def fetchall(self):
                return real_c.fetchall()

            def __getattr__(self, name):
                return getattr(real_c, name)

        return InterceptingCursor()

    monkeypatch.setattr(KardenwortConnection, "cursor", wrapping_cursor)

    bundle = db.get_session_bundle(session_zid)

    # 1. Assert reader was in an explicit transaction during the read
    assert reader_in_transaction_states == [True]
    assert writer_committed == [True]

    # 2. Assert bundle has coherent old revision (never mixed old-sentence + new-word)
    assert bundle["sentences"][0]["sentence_destination"] == "old-sentence"
    assert bundle["words"][0]["word_destination"] == "old-word"

    # 3. Assert subsequent read outside the transaction sees the new committed state
    monkeypatch.setattr(KardenwortConnection, "cursor", orig_cursor)
    fresh_bundle = db.get_session_bundle(session_zid)
    assert fresh_bundle["sentences"][0]["sentence_destination"] == "new-sentence"
    assert fresh_bundle["words"][0]["word_destination"] == "new-word"


def test_committed_recovery_snapshot_atomic_revision_under_concurrent_writer(test_env, monkeypatch):
    """
    Verifies that committed_recovery_snapshot returns a coherent single-revision snapshot
    under concurrent writes and does not produce torn states.
    """
    db = test_env["db"]
    adapter = test_env["adapter"]
    session_zid = "20261103210000"
    seed_test_session(db, session_zid)

    arbiter = MagicMock()
    arbiter._lock = threading.Lock()
    arbiter.storage_adapter = adapter
    arbiter.config = test_env["config"]
    arbiter.resolved_paths = test_env["resolved_paths"]
    arbiter.sessions = {session_zid: {}}

    orig_cursor = KardenwortConnection.cursor

    def wrapping_cursor(conn_self, *args, **kwargs):
        real_c = orig_cursor(conn_self, *args, **kwargs)

        class InterceptingCursor:
            def execute(self, sql, *a, **kw):
                res = real_c.execute(sql, *a, **kw)
                if "FROM sentences" in sql and conn_self.read_only:
                    with db.get_connection(read_only=False) as writer_conn:
                        writer_conn.execute(
                            "UPDATE sentences SET sentence_destination = ? WHERE session_zid = ?",
                            ("new-sentence", session_zid),
                        )
                        writer_conn.execute(
                            "UPDATE words SET word_destination = ?, extra_fields = ? WHERE session_zid = ?",
                            (
                                "new-word",
                                json.dumps({"word_destination": "new-word", "occurrence_data": {}}),
                                session_zid,
                            ),
                        )
                return res

            def fetchone(self):
                return real_c.fetchone()

            def fetchall(self):
                return real_c.fetchall()

            def __getattr__(self, name):
                return getattr(real_c, name)

        return InterceptingCursor()

    monkeypatch.setattr(KardenwortConnection, "cursor", wrapping_cursor)

    snapshot = committed_recovery_snapshot(arbiter, session_zid)
    monkeypatch.setattr(KardenwortConnection, "cursor", orig_cursor)

    # Assert coherent old snapshot
    assert snapshot["translatedText"] == "old-sentence"
    assert snapshot["sentences"][0]["sentence_destination"] == "old-sentence"
    # rows is dict mapping index to row dict
    assert snapshot["rows"][0]["trans"] == "old-word"


# ===========================================================================
# 2. Fail-Fast Transaction Ownership Tests
# ===========================================================================

def test_kardenwort_connection_enter_fails_fast_and_closes_on_read_only(test_env):
    """
    Verifies that when BEGIN; fails in KardenwortConnection.__enter__ for read_only=True,
    the connection is closed immediately and the exception is raised without entering
    the with block in autocommit mode.
    """
    db = test_env["db"]
    conn = db.get_connection(read_only=True)

    with patch.object(conn, "execute", side_effect=sqlite3.OperationalError("database is locked")):
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            with conn:
                pytest.fail("Block body must not be executed when transaction initiation fails")

    # Connection must be closed
    assert conn._in_context_block is False
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        conn.execute("SELECT 1;")


def test_kardenwort_connection_enter_fails_fast_and_closes_on_writable(test_env):
    """
    Verifies that when BEGIN IMMEDIATE; fails in KardenwortConnection.__enter__ for writable,
    the connection is closed immediately and the exception is raised.
    """
    db = test_env["db"]
    conn = db.get_connection(read_only=False)

    with patch.object(conn, "execute", side_effect=sqlite3.DatabaseError("disk I/O error")):
        with pytest.raises(sqlite3.DatabaseError, match="disk I/O error"):
            with conn:
                pytest.fail("Block body must not be executed when transaction initiation fails")

    # Connection must be closed
    assert conn._in_context_block is False
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        conn.execute("SELECT 1;")


# ===========================================================================
# 3. Guarded Controller Recovery Cache Publication Tests
# ===========================================================================

def test_committed_recovery_preserves_concurrent_data_rows_mutation(test_env, monkeypatch):
    """
    Verifies that if arbiter.sessions[zid]['data_rows'] is concurrently mutated
    while storage read is in flight, committed_recovery_snapshot does not overwrite it.
    """
    db = test_env["db"]
    adapter = test_env["adapter"]
    session_zid = "20261103210000"
    seed_test_session(db, session_zid)

    arbiter = MagicMock()
    arbiter._lock = threading.Lock()
    arbiter.storage_adapter = adapter
    arbiter.config = test_env["config"]
    arbiter.resolved_paths = test_env["resolved_paths"]

    initial_headers = ["Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination", "SentenceSourceIndex", "TokenOrder"]
    initial_rows = [["cat", "cat", "old-word", "The cat sat on the mat.", "old-sentence", "1", "0"]]

    arbiter.sessions = {
        session_zid: {
            "headers": list(initial_headers),
            "data_rows": [list(r) for r in initial_rows],
            "fingerprint": "initial-fp",
            "translatedText": "old-sentence",
        }
    }

    from kardenwort_controller import recovery_storage_rows as orig_recovery_storage_rows

    def mutating_recovery_storage_rows(arb, adap, zid, memo):
        res = orig_recovery_storage_rows(arb, adap, zid, memo)
        # Simulate concurrent in-memory mutation by another thread
        with arbiter._lock:
            arbiter.sessions[session_zid]["data_rows"] = [["cat", "cat", "concurrent-user-edit", "...", "...", "1", "0"]]
        return res

    monkeypatch.setattr("kardenwort_controller.recovery_storage_rows", mutating_recovery_storage_rows)

    snapshot = committed_recovery_snapshot(arbiter, session_zid)

    # 1. Snapshot returns restored data
    assert snapshot is not None
    # 2. arbiter.sessions[session_zid]['data_rows'] was NOT overwritten!
    with arbiter._lock:
        current_rows = arbiter.sessions[session_zid]["data_rows"]
    assert current_rows == [["cat", "cat", "concurrent-user-edit", "...", "...", "1", "0"]]


def test_committed_recovery_preserves_concurrent_fingerprint_mutation(test_env, monkeypatch):
    """
    Verifies that if arbiter.sessions[zid]['fingerprint'] is concurrently mutated,
    committed_recovery_snapshot does not overwrite it.
    """
    db = test_env["db"]
    adapter = test_env["adapter"]
    session_zid = "20261103210000"
    seed_test_session(db, session_zid)

    arbiter = MagicMock()
    arbiter._lock = threading.Lock()
    arbiter.storage_adapter = adapter
    arbiter.config = test_env["config"]
    arbiter.resolved_paths = test_env["resolved_paths"]

    initial_headers = ["Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination", "SentenceSourceIndex", "TokenOrder"]
    initial_rows = [["cat", "cat", "old-word", "The cat sat on the mat.", "old-sentence", "1", "0"]]

    arbiter.sessions = {
        session_zid: {
            "headers": list(initial_headers),
            "data_rows": [list(r) for r in initial_rows],
            "fingerprint": "original-fp",
            "translatedText": "old-sentence",
        }
    }

    from kardenwort_controller import recovery_storage_rows as orig_recovery_storage_rows

    def mutating_recovery_storage_rows(arb, adap, zid, memo):
        res = orig_recovery_storage_rows(arb, adap, zid, memo)
        with arbiter._lock:
            arbiter.sessions[session_zid]["fingerprint"] = "new-concurrent-fingerprint"
        return res

    monkeypatch.setattr("kardenwort_controller.recovery_storage_rows", mutating_recovery_storage_rows)

    committed_recovery_snapshot(arbiter, session_zid)

    with arbiter._lock:
        current_fp = arbiter.sessions[session_zid]["fingerprint"]
    assert current_fp == "new-concurrent-fingerprint"


def test_committed_recovery_preserves_concurrent_translation_mutation(test_env, monkeypatch):
    """
    Verifies that if arbiter.sessions[zid]['translatedText'] is concurrently mutated,
    committed_recovery_snapshot does not overwrite it.
    """
    db = test_env["db"]
    adapter = test_env["adapter"]
    session_zid = "20261103210000"
    seed_test_session(db, session_zid)

    arbiter = MagicMock()
    arbiter._lock = threading.Lock()
    arbiter.storage_adapter = adapter
    arbiter.config = test_env["config"]
    arbiter.resolved_paths = test_env["resolved_paths"]

    initial_headers = ["Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination", "SentenceSourceIndex", "TokenOrder"]
    initial_rows = [["cat", "cat", "old-word", "The cat sat on the mat.", "old-sentence", "1", "0"]]

    arbiter.sessions = {
        session_zid: {
            "headers": list(initial_headers),
            "data_rows": [list(r) for r in initial_rows],
            "fingerprint": "original-fp",
            "translatedText": "initial-translation",
        }
    }

    from kardenwort_controller import recovery_storage_rows as orig_recovery_storage_rows

    def mutating_recovery_storage_rows(arb, adap, zid, memo):
        res = orig_recovery_storage_rows(arb, adap, zid, memo)
        with arbiter._lock:
            arbiter.sessions[session_zid]["translatedText"] = "concurrently-streamed-translation"
        return res

    monkeypatch.setattr("kardenwort_controller.recovery_storage_rows", mutating_recovery_storage_rows)

    committed_recovery_snapshot(arbiter, session_zid)

    with arbiter._lock:
        current_trans = arbiter.sessions[session_zid]["translatedText"]
    assert current_trans == "concurrently-streamed-translation"


def test_committed_recovery_publishes_cache_when_unmodified(test_env):
    """
    Verifies that when arbiter.sessions[zid] is NOT modified concurrently,
    committed_recovery_snapshot successfully publishes the restored snapshot.
    """
    db = test_env["db"]
    adapter = test_env["adapter"]
    session_zid = "20261103210000"
    seed_test_session(db, session_zid)

    arbiter = MagicMock()
    arbiter._lock = threading.Lock()
    arbiter.storage_adapter = adapter
    arbiter.config = test_env["config"]
    arbiter.resolved_paths = test_env["resolved_paths"]

    # Initial cached state matches disk
    arbiter.sessions = {
        session_zid: {
            "headers": ["Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination", "SentenceSourceIndex", "TokenOrder"],
            "data_rows": [["cat", "cat", "old-word", "The cat sat on the mat.", "old-sentence", "1", "0"]],
            "fingerprint": "initial-fp",
            "translatedText": "old-sentence",
        }
    }

    snapshot = committed_recovery_snapshot(arbiter, session_zid)

    with arbiter._lock:
        cached = arbiter.sessions[session_zid]
    assert cached["translatedText"] == "old-sentence"
    assert cached["fingerprint"] == snapshot["fingerprint"]
