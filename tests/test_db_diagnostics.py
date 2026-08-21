import os
import sys
import json
import pytest
import sqlite3
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from kardenwort_db import (
    KardenwortDB,
    DBLogger,
    QuerySecurityError,
    QueryExecutionError,
)
import kardenwort_desk


@pytest.fixture
def temp_db(tmp_path):
    """Fixture providing a temporary KardenwortDB instance."""
    db_file = tmp_path / "data" / "test_kardenwort.db"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db = KardenwortDB(db_path=db_file, resolved_paths={"results_dir": results_dir})
    return db


def test_db_connection_pragmas(temp_db):
    """Test WAL mode, foreign keys, and busy timeout on connection."""
    conn = temp_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0].lower()
        assert journal_mode == "wal"

        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys = cursor.fetchone()[0]
        assert foreign_keys == 1

        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        assert busy_timeout == temp_db.busy_timeout_ms
        cursor.close()
    finally:
        conn.close()


def test_db_logging_and_rotation(tmp_path):
    """Test structured logging to db.log and rotation when exceeding max size."""
    log_file = tmp_path / "results" / "db.log"
    logger = DBLogger(log_path=log_file, max_mb=0.001)  # ~1KB threshold for fast testing

    # Write multiple log entries
    for i in range(100):
        logger.log("INFO", f"Test log line {i}", zid="20260821180000", duration_ms=1.23, details={"i": i})

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "[KardenwortDB]" in content
    assert "[INFO]" in content
    assert "[ZID:20260821180000]" in content
    assert "(1.23ms)" in content


def test_db_status_reporting(temp_db):
    """Test get_status() with empty and populated database."""
    # Before connection/tables
    status = temp_db.get_status()
    assert status["ok"] is True
    assert status["exists"] is False
    assert status["tables"] == {}

    # Initialize a table and populate
    conn = temp_db.get_connection()
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob');")
    conn.commit()
    conn.close()

    status = temp_db.get_status()
    assert status["ok"] is True
    assert status["exists"] is True
    assert status["size_bytes"] > 0
    assert "users" in status["tables"]
    assert status["tables"]["users"] == 2


def test_db_check_integrity(temp_db):
    """Test database integrity check and foreign key check."""
    conn = temp_db.get_connection()
    conn.executescript("""
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
    """)
    conn.execute("INSERT INTO parent (id) VALUES (1);")
    conn.execute("INSERT INTO child (id, parent_id) VALUES (10, 1);")
    conn.commit()
    conn.close()

    check_res = temp_db.check_integrity()
    assert check_res["ok"] is True
    assert check_res["integrity"] == ["ok"]
    assert check_res["foreign_key_violations"] == []


def test_db_check_foreign_key_violation(temp_db):
    """Test detecting foreign key violations."""
    conn = temp_db.get_connection()
    # Temporarily disable FK to insert orphaned record
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.executescript("""
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
    """)
    conn.execute("INSERT INTO child (id, parent_id) VALUES (10, 999);")
    conn.commit()
    conn.close()

    check_res = temp_db.check_integrity()
    assert check_res["ok"] is False
    assert len(check_res["foreign_key_violations"]) > 0
    assert check_res["foreign_key_violations"][0]["table"] == "child"


def test_db_query_readonly_success(temp_db):
    """Test executing valid read-only queries."""
    conn = temp_db.get_connection()
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT);")
    conn.execute("INSERT INTO items (title) VALUES ('Item A'), ('Item B');")
    conn.commit()
    conn.close()

    rows = temp_db.query_readonly("SELECT id, title FROM items ORDER BY id ASC;")
    assert len(rows) == 2
    assert rows[0] == {"id": 1, "title": "Item A"}
    assert rows[1] == {"id": 2, "title": "Item B"}


@pytest.mark.parametrize("mutating_sql", [
    "INSERT INTO items (title) VALUES ('Hacked');",
    "UPDATE items SET title = 'Changed';",
    "DELETE FROM items WHERE id = 1;",
    "DROP TABLE items;",
    "ALTER TABLE items ADD COLUMN extra TEXT;",
    "ATTACH DATABASE ':memory:' AS evil;",
    "SELECT 1; DROP TABLE items;",
])
def test_db_query_mutation_rejection(temp_db, mutating_sql):
    """Assert mutating SQL statements in query_readonly are blocked with MUTATION_NOT_ALLOWED."""
    conn = temp_db.get_connection()
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT);")
    conn.execute("INSERT INTO items (title) VALUES ('Original');")
    conn.commit()
    conn.close()

    with pytest.raises(QuerySecurityError) as exc_info:
        temp_db.query_readonly(mutating_sql)

    assert exc_info.value.error_code == "MUTATION_NOT_ALLOWED"

    # Verify items table was not mutated
    rows = temp_db.query_readonly("SELECT count(*) as cnt FROM items;")
    assert rows[0]["cnt"] == 1


def test_db_reset(temp_db):
    """Test database reset requires force=True and removes files cleanly."""
    conn = temp_db.get_connection()
    conn.execute("CREATE TABLE test_tab (id INT);")
    conn.commit()
    conn.close()

    assert temp_db.db_path.exists()

    # Reset without force should fail
    with pytest.raises(QuerySecurityError):
        temp_db.reset(force=False)

    assert temp_db.db_path.exists()

    # Reset with force
    res = temp_db.reset(force=True)
    assert res["ok"] is True
    assert not temp_db.db_path.exists()


def test_cli_diagnostics_dispatch(monkeypatch, capsys, tmp_path):
    """Test CLI dispatch for --db-status, --db-check, --db-query, and --db-reset."""
    # 1. Test --db-status
    test_args = ["kardenwort_desk.py", "--db-status", "--json"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc:
        kardenwort_desk.main()
    assert exc.value.code == 0

    # 2. Test --db-check
    test_args = ["kardenwort_desk.py", "--db-check"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc:
        kardenwort_desk.main()
    assert exc.value.code == 0

    # 3. Test --db-query
    test_args = ["kardenwort_desk.py", "--db-query", "SELECT 42 as answer;"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc:
        kardenwort_desk.main()
    assert exc.value.code == 0
