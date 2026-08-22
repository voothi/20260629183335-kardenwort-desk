"""
kardenwort_db.py - Foundational SQLite Connection Manager and Migrations for Kardenwort-Desk

Provides zero-dependency database access, WAL mode management, structured logging to results/db.log,
deterministic SQL migrations (_migrations), integrity diagnostics, safe sandboxed read-only querying,
normalized relational CRUD helpers (sessions, sentences, words), and test teardown facilities.
"""

import os
import sys
import re
import json
import time
import sqlite3
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Union, Generator

logger = logging.getLogger("kardenwort.desk.db")


# ---------------------------------------------------------------------------
# Structured Database Logger (results/db.log)
# ---------------------------------------------------------------------------
class DBLogger:
    """
    Dedicated database logger writing structured log lines to results/db.log
    with automated size-based log rotation.
    """

    def __init__(self, log_path: Optional[Path] = None, max_mb: float = 5.0):
        self.log_path = log_path
        self.max_mb = max_mb
        self._lock = threading.Lock()

    def set_log_path(self, log_path: Path, max_mb: float = 5.0):
        self.log_path = log_path
        self.max_mb = max_mb

    def _rotate_if_needed(self):
        if not self.log_path or not self.log_path.exists():
            return
        try:
            if self.log_path.stat().st_size > self.max_mb * 1024 * 1024:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                # Retain the last 50% of the log lines
                with open(self.log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[len(lines) // 2:])
        except Exception:
            pass

    def log(
        self,
        level: str,
        message: str,
        zid: Optional[str] = None,
        duration_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        if not self.log_path:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        zid_str = f" [ZID:{zid}]" if zid else ""
        dur_str = f" ({duration_ms:.2f}ms)" if duration_ms is not None else ""
        detail_str = f" | {json.dumps(details, ensure_ascii=False)}" if details else ""
        line = f"{now_iso} [{level.upper()}] [KardenwortDB]{zid_str} {message}{dur_str}{detail_str}\n"

        with self._lock:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass


# Global singleton instance of DBLogger
_GLOBAL_DB_LOGGER = DBLogger()


def get_db_logger() -> DBLogger:
    return _GLOBAL_DB_LOGGER


# ---------------------------------------------------------------------------
# Kardenwort SQLite Connection
# ---------------------------------------------------------------------------
class KardenwortConnection(sqlite3.Connection):
    """
    Custom SQLite connection supporting contextual transaction blocks with
    immediate locking, automatic commit/rollback, and clean resource cleanup.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read_only: bool = False
        self._in_context_block: bool = False

    def __enter__(self) -> "KardenwortConnection":
        self._in_context_block = True
        if not self.read_only:
            try:
                self.execute("BEGIN IMMEDIATE;")
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type is not None:
                try:
                    self.rollback()
                except Exception:
                    pass
                return False
            else:
                try:
                    if not self.read_only:
                        self.commit()
                except Exception:
                    try:
                        self.rollback()
                    except Exception:
                        pass
                    raise
                return False
        finally:
            self._in_context_block = False
            self.close()


# ---------------------------------------------------------------------------
# KardenwortDB Connection and Relational Engine
# ---------------------------------------------------------------------------
class KardenwortDB:
    """
    Foundational connection manager and relational engine for Kardenwort SQLite databases.
    Enforces WAL journal mode, busy timeouts, foreign key constraints,
    provides deterministic schema migrations (_migrations), normalized CRUD helpers,
    integrity checks, and sandboxed query evaluation.
    """

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        config: Optional[Any] = None,
        resolved_paths: Optional[Dict[str, Any]] = None,
        busy_timeout_ms: int = 5000,
        migrations_dir: Optional[Union[str, Path]] = None,
    ):
        self.config = config
        self.resolved_paths = resolved_paths
        self.busy_timeout_ms = busy_timeout_ms

        # Resolve workspace / data root
        workspace_dir = Path(__file__).resolve().parent

        if db_path:
            self.db_path = Path(db_path).resolve()
        elif resolved_paths and "sqlite_db_path" in resolved_paths and resolved_paths["sqlite_db_path"]:
            self.db_path = Path(resolved_paths["sqlite_db_path"]).resolve()
        elif resolved_paths and "db_path" in resolved_paths and resolved_paths["db_path"]:
            self.db_path = Path(resolved_paths["db_path"]).resolve()
        elif config and hasattr(config, "get") and config.has_option("storage", "sqlite_db_path"):
            raw_p = config.get("storage", "sqlite_db_path")
            self.db_path = (workspace_dir / raw_p).resolve() if not Path(raw_p).is_absolute() else Path(raw_p).resolve()
        elif config and hasattr(config, "get") and config.has_option("db", "path"):
            raw_p = config.get("db", "path")
            self.db_path = (workspace_dir / raw_p).resolve() if not Path(raw_p).is_absolute() else Path(raw_p).resolve()
        else:
            self.db_path = (workspace_dir / "data" / "kardenwort.db").resolve()

        # Ensure parent data directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Resolve migrations directory
        if migrations_dir:
            self.migrations_dir = Path(migrations_dir).resolve()
        elif resolved_paths and "migrations_dir" in resolved_paths:
            self.migrations_dir = Path(resolved_paths["migrations_dir"]).resolve()
        else:
            self.migrations_dir = (workspace_dir / "schemas" / "migrations").resolve()

        # Configure db logger destination
        results_dir = workspace_dir / "results"
        if resolved_paths and "results_dir" in resolved_paths:
            r_dir = resolved_paths.get("results_dir")
            if r_dir:
                results_dir = Path(r_dir)
        elif resolved_paths and "generated_results_dir" in resolved_paths:
            r_dir = resolved_paths.get("generated_results_dir")
            if r_dir:
                results_dir = Path(r_dir)
        elif config and hasattr(config, "get"):
            r_dir = config.get("settings", "results_dir", fallback=None)
            if r_dir:
                results_dir = Path(r_dir)


        max_mb = 5.0
        if config and hasattr(config, "getfloat"):
            max_mb = config.getfloat(
                "profiling",
                "trace_log_max_mb",
                fallback=config.getfloat("settings", "trace_log_max_mb", fallback=5.0),
            )

        _GLOBAL_DB_LOGGER.set_log_path(results_dir / "db.log", max_mb=max_mb)
        self.logger = _GLOBAL_DB_LOGGER

    @property
    def wal_path(self) -> Path:
        return Path(f"{self.db_path}-wal")

    @property
    def shm_path(self) -> Path:
        return Path(f"{self.db_path}-shm")

    def get_connection(
        self, read_only: bool = False, zid: Optional[str] = None
    ) -> KardenwortConnection:
        """
        Creates and configures an optimized SQLite connection with WAL mode,
        busy timeout, and foreign keys enabled. Can be used directly or as a context manager.
        """
        start_t = time.perf_counter()
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
                factory=KardenwortConnection,
            )
            conn.row_factory = sqlite3.Row
            conn.read_only = read_only

            cursor = conn.cursor()
            if read_only:
                cursor.execute("PRAGMA query_only = ON;")
            else:
                cursor.execute("PRAGMA journal_mode = WAL;")
                cursor.execute("PRAGMA synchronous = NORMAL;")

            cursor.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.close()

            duration_ms = (time.perf_counter() - start_t) * 1000.0
            self.logger.log(
                "INFO",
                f"Opened connection to {self.db_path.name} (read_only={read_only})",
                zid=zid,
                duration_ms=duration_ms,
                details={"db_path": str(self.db_path), "read_only": read_only},
            )
            return conn
        except Exception as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            self.logger.log(
                "ERROR",
                f"Failed opening connection to {self.db_path.name}: {e}",
                zid=zid,
                duration_ms=duration_ms,
                details={"db_path": str(self.db_path), "error": str(e)},
            )
            raise

    @contextmanager
    def transaction(
        self, read_only: bool = False, zid: Optional[str] = None
    ) -> Generator[KardenwortConnection, None, None]:
        """
        Context manager helper providing atomic transaction blocks (BEGIN IMMEDIATE / COMMIT / ROLLBACK).
        """
        conn = self.get_connection(read_only=read_only, zid=zid)
        with conn:
            yield conn

    # ---------------------------------------------------------------------------
    # Deterministic Schema Migration Runner (_migrations)
    # ---------------------------------------------------------------------------
    def run_migrations(self, zid: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans schemas/migrations/*.sql and executes unapplied migrations in alphabetical
        order within individual atomic transactions, recording applied files into _migrations.
        """
        start_t = time.perf_counter()
        if not self.migrations_dir.exists():
            self.migrations_dir.mkdir(parents=True, exist_ok=True)

        def _migration_sort_key(p: Path):
            name = p.name
            m = re.match(r"^(\d+)", name)
            if m:
                return (0, int(m.group(1)), name)
            return (1, 0, name)

        migration_files = sorted(self.migrations_dir.glob("*.sql"), key=_migration_sort_key)
        applied_now: List[str] = []
        already_applied: List[str] = []

        try:
            # 1. Ensure _migrations table exists
            with self.get_connection(zid=zid) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS _migrations (
                        filename TEXT PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )

            # 2. Fetch list of already applied migrations
            with self.get_connection(read_only=True, zid=zid) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT filename FROM _migrations ORDER BY filename ASC;")
                already_applied = [row[0] for row in cursor.fetchall()]

            applied_set = set(already_applied)

            # 3. Apply pending migrations sequentially
            for sql_file in migration_files:
                if sql_file.name in applied_set:
                    continue

                sql_content = sql_file.read_text(encoding="utf-8")
                mig_start = time.perf_counter()

                with self.get_connection(zid=zid) as conn:
                    cursor = conn.cursor()
                    # Execute migration SQL statements individually within transaction
                    # to ensure full transactional rollback of DDL statements on error
                    for statement in sql_content.split(";"):
                        stmt_clean = statement.strip()
                        if stmt_clean:
                            cursor.execute(stmt_clean)
                    cursor.execute(
                        "INSERT INTO _migrations (filename, applied_at) VALUES (?, ?);",
                        (sql_file.name, datetime.now(timezone.utc).isoformat()),
                    )

                mig_dur = (time.perf_counter() - mig_start) * 1000.0
                applied_now.append(sql_file.name)
                self.logger.log(
                    "INFO",
                    f"Applied migration {sql_file.name}",
                    zid=zid,
                    duration_ms=mig_dur,
                    details={"migration": sql_file.name},
                )

            total_dur = (time.perf_counter() - start_t) * 1000.0
            result = {
                "ok": True,
                "applied": applied_now,
                "already_applied": already_applied,
                "total_available": len(migration_files),
                "total_applied": len(already_applied) + len(applied_now),
            }
            self.logger.log(
                "INFO",
                f"Migrations complete: {len(applied_now)} applied, {len(already_applied)} existing",
                zid=zid,
                duration_ms=total_dur,
                details=result,
            )
            return result

        except Exception as e:
            total_dur = (time.perf_counter() - start_t) * 1000.0
            self.logger.log(
                "ERROR",
                f"Migration failed: {e}",
                zid=zid,
                duration_ms=total_dur,
                details={"error": str(e)},
            )
            raise QueryExecutionError("MIGRATION_FAILED", f"Migration failed: {e}")

    # ---------------------------------------------------------------------------
    # Diagnostics & Status
    # ---------------------------------------------------------------------------
    def get_status(self, zid: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns structured database metrics including file size, WAL size,
        table row counts, and migration status.
        """
        exists = self.db_path.exists()
        size_bytes = self.db_path.stat().st_size if exists else 0
        wal_size_bytes = self.wal_path.stat().st_size if self.wal_path.exists() else 0

        status: Dict[str, Any] = {
            "ok": True,
            "database_path": str(self.db_path),
            "exists": exists,
            "size_bytes": size_bytes,
            "wal_size_bytes": wal_size_bytes,
            "migrations_applied": [],
            "tables": {},
        }

        if not exists:
            return status

        try:
            with self.get_connection(read_only=True, zid=zid) as conn:
                cursor = conn.cursor()

                # Check applied migrations if migration table exists
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('_migrations', 'schema_migrations');"
                )
                mig_table = cursor.fetchone()
                if mig_table:
                    t_name = mig_table[0]
                    # Check column names
                    cursor.execute(f"PRAGMA table_info(\"{t_name}\");")
                    cols = [c[1] for c in cursor.fetchall()]
                    col_name = "filename" if "filename" in cols else "version"
                    cursor.execute(f"SELECT {col_name} FROM \"{t_name}\" ORDER BY {col_name} ASC;")
                    status["migrations_applied"] = [row[0] for row in cursor.fetchall()]

                # Count rows per user table
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
                )
                tables = [r[0] for r in cursor.fetchall()]
                for t in tables:
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM \"{t}\";")
                        status["tables"][t] = cursor.fetchone()[0]
                    except Exception:
                        status["tables"][t] = -1

            self.logger.log("INFO", "Retrieved database status", zid=zid, details=status)
        except Exception as e:
            status["ok"] = False
            status["error"] = str(e)
            self.logger.log("ERROR", f"Failed retrieving status: {e}", zid=zid)

        return status

    def check_integrity(self, zid: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes PRAGMA integrity_check and PRAGMA foreign_key_check.
        """
        if not self.db_path.exists():
            return {
                "ok": True,
                "database_path": str(self.db_path),
                "integrity": ["ok (file does not exist yet)"],
                "foreign_key_violations": [],
            }

        start_t = time.perf_counter()
        try:
            with self.get_connection(read_only=True, zid=zid) as conn:
                cursor = conn.cursor()

                cursor.execute("PRAGMA integrity_check;")
                integrity_rows = [r[0] for r in cursor.fetchall()]

                cursor.execute("PRAGMA foreign_key_check;")
                fk_rows = [
                    {
                        "table": r[0],
                        "rowid": r[1],
                        "parent_table": r[2],
                        "fkid": r[3],
                    }
                    for r in cursor.fetchall()
                ]

            is_ok = integrity_rows == ["ok"] and len(fk_rows) == 0
            duration_ms = (time.perf_counter() - start_t) * 1000.0

            result = {
                "ok": is_ok,
                "database_path": str(self.db_path),
                "integrity": integrity_rows,
                "foreign_key_violations": fk_rows,
            }

            self.logger.log(
                "INFO" if is_ok else "WARN",
                f"Integrity check completed (ok={is_ok})",
                zid=zid,
                duration_ms=duration_ms,
                details=result,
            )
            return result
        except Exception as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            err_result = {
                "ok": False,
                "database_path": str(self.db_path),
                "error": str(e),
                "integrity": [],
                "foreign_key_violations": [],
            }
            self.logger.log("ERROR", f"Integrity check failed: {e}", zid=zid, duration_ms=duration_ms)
            return err_result

    def query_readonly(
        self,
        sql: str,
        params: Union[Tuple, List] = (),
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes a sandboxed read-only SQL statement.
        Enforces PRAGMA query_only = ON, sets a strict authorizer callback denying
        any mutating or schema-modifying operations, and returns results as JSON-ready dicts.
        """
        clean_sql = sql.strip()
        if not clean_sql:
            return []

        # Prevent semicolon-chained multiple statements
        sql_without_trailing = clean_sql.rstrip(";\t\r\n ")
        if ";" in sql_without_trailing:
            self.logger.log("WARN", f"Multiple SQL statements rejected in query_readonly: {sql}", zid=zid)
            raise QuerySecurityError("MUTATION_NOT_ALLOWED", "Chained multiple SQL statements are not permitted.")

        # Upfront rejection of non-read-only SQL statements
        first_token = clean_sql.split(None, 1)[0].upper()
        disallowed_tokens = {
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
            "ATTACH", "DETACH", "REPLACE", "TRUNCATE", "VACUUM", "REINDEX",
            "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
        }
        if first_token in disallowed_tokens:
            self.logger.log("WARN", f"Mutating SQL statement rejected in query_readonly: {clean_sql[:100]}", zid=zid)
            raise QuerySecurityError("MUTATION_NOT_ALLOWED", f"Statement type '{first_token}' is not permitted in read-only runner.")

        def authorizer(action: int, arg1: Optional[str], arg2: Optional[str], db_name: Optional[str], trigger_name: Optional[str]) -> int:
            allowed_actions = {
                sqlite3.SQLITE_SELECT,
                sqlite3.SQLITE_READ,
                sqlite3.SQLITE_FUNCTION,
            }
            if action in allowed_actions:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_PRAGMA:
                if arg1 and arg1.lower() in ("query_only", "table_info", "table_list", "database_list"):
                    return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        start_t = time.perf_counter()
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()
            cursor.execute("PRAGMA query_only = ON;")
            conn.set_authorizer(authorizer)

            cursor.execute(clean_sql, params)
            rows = cursor.fetchall()
            result = [dict(r) for r in rows]
            cursor.close()
            conn.close()

            duration_ms = (time.perf_counter() - start_t) * 1000.0
            self.logger.log(
                "INFO",
                f"Executed read-only query: {clean_sql[:100]}",
                zid=zid,
                duration_ms=duration_ms,
                details={"rows_count": len(result)},
            )
            return result

        except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            err_msg = str(e)
            self.logger.log(
                "ERROR",
                f"Read-only query failed/denied: {clean_sql[:100]} ({err_msg})",
                zid=zid,
                duration_ms=duration_ms,
                details={"sql": clean_sql, "error": err_msg},
            )
            if "not authorized" in err_msg.lower() or "attempt to write a readonly database" in err_msg.lower() or "readonly" in err_msg.lower():
                raise QuerySecurityError("MUTATION_NOT_ALLOWED", f"SQL operation not permitted in read-only runner: {err_msg}")
            raise QueryExecutionError("QUERY_FAILED", f"Query execution failed: {err_msg}")

        except Exception as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            self.logger.log("ERROR", f"Unexpected error during query: {e}", zid=zid, duration_ms=duration_ms)
            raise

    def reset(self, force: bool = False, zid: Optional[str] = None) -> Dict[str, Any]:
        """
        Safely removes database and associated WAL/SHM sidecars for clean test teardown.
        Requires force=True.
        """
        if not force:
            raise QuerySecurityError("INVALID_STATE", "Database reset requires --force flag.")

        start_t = time.perf_counter()
        unlinked = []
        for target in (self.db_path, self.wal_path, self.shm_path):
            if target.exists():
                try:
                    size = target.stat().st_size
                    target.unlink()
                    unlinked.append({"file": target.name, "size_bytes": size})
                except Exception as e:
                    self.logger.log("ERROR", f"Failed unlinking {target.name}: {e}", zid=zid)
                    raise

        duration_ms = (time.perf_counter() - start_t) * 1000.0
        self.logger.log(
            "WARN",
            f"Reset database at {self.db_path}",
            zid=zid,
            duration_ms=duration_ms,
            details={"unlinked": unlinked},
        )
        return {
            "ok": True,
            "message": f"Database at {self.db_path} has been reset.",
            "unlinked": unlinked,
        }

    # ---------------------------------------------------------------------------
    # Normalized CRUD Helpers: Sessions
    # ---------------------------------------------------------------------------
    def insert_session(self, session: Dict[str, Any], zid: Optional[str] = None) -> str:
        """
        Inserts or replaces a session record in the sessions table.
        """
        session_zid = session["zid"]
        slug = session.get("slug", "")
        source_lang = session.get("source_language", "")
        target_lang = session.get("target_language", "")
        text_mode = session.get("text_mode", "single")
        source_raw_text = session.get("source_raw_text", "")
        created_at = session.get("created_at") or datetime.now(timezone.utc).isoformat()
        updated_at = session.get("updated_at") or datetime.now(timezone.utc).isoformat()

        with self.get_connection(zid=zid) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    zid, slug, source_language, target_language, text_mode,
                    source_raw_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(zid) DO UPDATE SET
                    slug = excluded.slug,
                    source_language = excluded.source_language,
                    target_language = excluded.target_language,
                    text_mode = excluded.text_mode,
                    source_raw_text = excluded.source_raw_text,
                    updated_at = excluded.updated_at;
                """,
                (session_zid, slug, source_lang, target_lang, text_mode, source_raw_text, created_at, updated_at),
            )
        return session_zid

    def get_session(
        self, session_zid: str, include_deleted: bool = False, zid: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches a single session record by ZID. Excludes soft-deleted sessions unless include_deleted=True.
        """
        sql = "SELECT * FROM sessions WHERE zid = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += ";"
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (session_zid,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_sessions(
        self, limit: Optional[int] = None, offset: int = 0, include_deleted: bool = False, zid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns a list of sessions ordered by created_at DESC.
        """
        sql = "SELECT * FROM sessions"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        sql += " ORDER BY created_at DESC"
        params: List[Any] = []
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def update_session(self, session_zid: str, updates: Dict[str, Any], zid: Optional[str] = None) -> bool:
        """
        Updates fields of an existing session record.
        """
        if not updates:
            return False

        allowed_cols = {"slug", "source_language", "target_language", "text_mode", "source_raw_text", "updated_at", "deleted_at"}
        valid_updates = {k: v for k, v in updates.items() if k in allowed_cols}
        if "updated_at" not in valid_updates:
            valid_updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clauses = [f"{col} = ?" for col in valid_updates.keys()]
        values = list(valid_updates.values())
        values.append(session_zid)

        sql = f"UPDATE sessions SET {', '.join(set_clauses)} WHERE zid = ?;"
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, values)
            return cursor.rowcount > 0

    def list_sessions_with_counts(
        self, limit: Optional[int] = None, offset: int = 0, include_deleted: bool = False, zid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns a list of sessions with token count and sentence count ordered by created_at DESC.
        """
        filter_clause = "WHERE s.deleted_at IS NULL" if not include_deleted else ""
        sql = f"""
            SELECT 
                s.zid, 
                s.slug, 
                s.source_language, 
                s.target_language, 
                s.text_mode, 
                s.source_raw_text,
                s.created_at, 
                s.updated_at,
                s.deleted_at,
                COUNT(w.id) as token_count,
                COUNT(DISTINCT sn.sentence_index) as sentence_count
            FROM sessions s
            LEFT JOIN sentences sn ON sn.session_zid = s.zid
            LEFT JOIN words w ON w.session_zid = s.zid
            {filter_clause}
            GROUP BY s.zid
            ORDER BY s.created_at DESC
        """
        params: List[Any] = []
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def search_sessions(
        self,
        query: Optional[str] = None,
        language: Optional[str] = None,
        assigned: Optional[Union[bool, str]] = None,
        project_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
        zid: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Retrieves paginated sessions matching search query, language, project_id, and assignment status,
        along with sentence_count, word_count, and linked projects.
        Returns (sessions_list, total_count).
        """
        conditions: List[str] = []
        params: List[Any] = []

        if not include_deleted:
            conditions.append("s.deleted_at IS NULL")

        if query and query.strip():
            clean_q = f"%{query.strip()}%"
            conditions.append("(s.zid LIKE ? OR s.slug LIKE ? OR s.source_raw_text LIKE ?)")
            params.extend([clean_q, clean_q, clean_q])

        if language and language.strip() and language.strip().lower() != "all":
            conditions.append("s.source_language = ?")
            params.append(language.strip())

        if project_id is not None:
            conditions.append("EXISTS (SELECT 1 FROM project_sessions ps WHERE ps.session_zid = s.zid AND ps.project_id = ?)")
            params.append(project_id)

        if assigned is not None:
            if isinstance(assigned, bool):
                is_assigned = assigned
            else:
                str_assigned = str(assigned).strip().lower()
                if str_assigned in ("true", "1", "assigned"):
                    is_assigned = True
                elif str_assigned in ("false", "0", "unassigned"):
                    is_assigned = False
                else:
                    is_assigned = None

            if is_assigned is True:
                conditions.append("EXISTS (SELECT 1 FROM project_sessions ps WHERE ps.session_zid = s.zid)")
            elif is_assigned is False:
                conditions.append("NOT EXISTS (SELECT 1 FROM project_sessions ps WHERE ps.session_zid = s.zid)")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            # 1. Count total matching sessions
            count_sql = f"SELECT COUNT(*) FROM sessions s {where_clause};"
            cursor.execute(count_sql, params)
            total_count = cursor.fetchone()[0]

            if total_count == 0:
                return [], 0

            # 2. Fetch paginated session records with sentence and word counts
            data_sql = f"""
                SELECT 
                    s.zid, 
                    s.slug, 
                    s.source_language, 
                    s.target_language, 
                    s.text_mode, 
                    s.source_raw_text,
                    s.created_at, 
                    s.updated_at,
                    s.deleted_at,
                    (SELECT COUNT(*) FROM sentences sn WHERE sn.session_zid = s.zid) AS sentence_count,
                    (SELECT COUNT(*) FROM words w WHERE w.session_zid = s.zid) AS word_count
                FROM sessions s
                {where_clause}
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?;
            """
            fetch_params = list(params) + [limit, offset]
            cursor.execute(data_sql, fetch_params)
            sessions = [dict(r) for r in cursor.fetchall()]

            # 3. Batch attach linked projects for the fetched sessions
            if sessions:
                session_zids = [s["zid"] for s in sessions]
                placeholders = ",".join(["?"] * len(session_zids))
                proj_sql = f"""
                    SELECT ps.session_zid, p.id, p.title, p.slug, p.parent_id
                    FROM project_sessions ps
                    JOIN projects p ON ps.project_id = p.id
                    WHERE ps.session_zid IN ({placeholders}) AND p.deleted_at IS NULL
                    ORDER BY p.order_index ASC, p.id ASC;
                """
                cursor.execute(proj_sql, session_zids)
                proj_rows = cursor.fetchall()
                proj_map: Dict[str, List[Dict[str, Any]]] = {}
                for pr in proj_rows:
                    sz = pr["session_zid"]
                    proj_map.setdefault(sz, []).append({
                        "id": pr["id"],
                        "title": pr["title"],
                        "slug": pr["slug"],
                        "parent_id": pr["parent_id"],
                    })

                for s in sessions:
                    s["projects"] = proj_map.get(s["zid"], [])
                    s["token_count"] = s["word_count"]

            return sessions, total_count

    def soft_delete_session(self, session_zid: str, zid: Optional[str] = None) -> bool:
        """
        Soft-deletes a session by setting deleted_at to current timestamp.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET deleted_at = ? WHERE zid = ? AND deleted_at IS NULL;",
                (now_iso, session_zid),
            )
            return cursor.rowcount > 0

    def restore_session(self, session_zid: str, zid: Optional[str] = None) -> bool:
        """
        Restores a soft-deleted session by resetting deleted_at to NULL.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET deleted_at = NULL WHERE zid = ? AND deleted_at IS NOT NULL;",
                (session_zid,),
            )
            return cursor.rowcount > 0

    def get_deleted_sessions(
        self, limit: Optional[int] = None, offset: int = 0, zid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all soft-deleted sessions.
        """
        sql = "SELECT * FROM sessions WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        params: List[Any] = []
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def purge_deleted_sessions(
        self, older_than_days: Optional[float] = None, zid: Optional[str] = None
    ) -> int:
        """
        Permanently hard-deletes soft-deleted sessions (and cascades to sentences/words).
        """
        sql = "DELETE FROM sessions WHERE deleted_at IS NOT NULL"
        params: List[Any] = []
        if older_than_days is not None:
            sql += " AND (julianday('now') - julianday(deleted_at)) > ?"
            params.append(older_than_days)
        sql += ";"

        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor.rowcount

    def delete_session(self, session_zid: str, zid: Optional[str] = None) -> bool:
        """
        Deletes a session by ZID. Associated sentences and words are cascade deleted.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE zid = ?;", (session_zid,))
            return cursor.rowcount > 0

    def cleanup_db(self, older_than_days: float, zid: Optional[str] = None) -> int:
        """
        Deletes sessions older than specified number of days (cascades to sentences and words).
        """
        sql = "DELETE FROM sessions WHERE (julianday('now') - julianday(created_at)) > ?;"
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (older_than_days,))
            return cursor.rowcount

    def vacuum(self, zid: Optional[str] = None) -> bool:
        """
        Executes VACUUM and PRAGMA optimize on the database to defragment,
        reclaim free pages, and optimize SQLite query planner statistics.
        """
        t0 = time.perf_counter()
        conn = self.get_connection(read_only=False, zid=zid)
        try:
            conn.isolation_level = None
            conn.execute("VACUUM;")
            conn.execute("PRAGMA optimize;")
            dur_ms = (time.perf_counter() - t0) * 1000.0
            get_db_logger().log("INFO", "Database VACUUM and PRAGMA optimize completed", zid=zid, duration_ms=dur_ms)
            return True
        finally:
            conn.close()

    def backup_snapshot(
        self, backup_dir: Optional[Path] = None, zid: Optional[str] = None
    ) -> Path:
        """
        Creates a consistent, non-blocking physical binary snapshot of the database
        into backup_dir/kardenwort-YYYYMMDDHHMMSS.db using SQLite's native backup API.
        """
        if not backup_dir:
            backup_dir = self.db_path.parent / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        snapshot_zid = zid or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        target_path = backup_dir / f"{snapshot_zid}-kardenwort.db"

        t0 = time.perf_counter()
        source_conn = self.get_connection(read_only=True, zid=zid)
        try:
            target_conn = sqlite3.connect(str(target_path))
            try:
                source_conn.backup(target_conn)
            finally:
                target_conn.close()
        finally:
            source_conn.close()

        dur_ms = (time.perf_counter() - t0) * 1000.0
        size_bytes = target_path.stat().st_size if target_path.exists() else 0
        get_db_logger().log(
            "INFO",
            f"Physical backup snapshot created at {target_path.name}",
            zid=zid,
            duration_ms=dur_ms,
            details={"path": str(target_path), "bytes": size_bytes}
        )
        return target_path

    def get_sql_dump(self, zid: Optional[str] = None) -> str:
        """
        Generates a logical SQL dump (DDL + INSERT statements) of the database.
        """
        source_conn = self.get_connection(read_only=True, zid=zid)
        try:
            return "\n".join(source_conn.iterdump()) + "\n"
        finally:
            source_conn.close()

    def get_telemetry(self, zid: Optional[str] = None) -> Dict[str, Any]:
        """
        Collects comprehensive health and storage telemetry metrics.
        """
        db_stat = self.get_status(zid=zid)
        integrity = self.check_integrity(zid=zid)

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE deleted_at IS NULL;")
            active_sessions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM sessions WHERE deleted_at IS NOT NULL;")
            deleted_sessions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM projects WHERE deleted_at IS NULL;")
            total_projects = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM projects WHERE parent_id IS NULL AND deleted_at IS NULL;")
            root_projects = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM projects WHERE deleted_at IS NOT NULL;")
            deleted_projects = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM sentences;")
            total_sentences = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM words;")
            total_words = cursor.fetchone()[0]

        return {
            "size_bytes": db_stat.get("size_bytes", 0),
            "wal_size_bytes": db_stat.get("wal_size_bytes", 0),
            "shm_size_bytes": db_stat.get("shm_size_bytes", 0),
            "schema_version": db_stat.get("schema_version", 0),
            "integrity_ok": integrity.get("ok", False),
            "active_sessions": active_sessions,
            "deleted_sessions": deleted_sessions,
            "total_projects": total_projects,
            "root_projects": root_projects,
            "deleted_projects": deleted_projects,
            "total_sentences": total_sentences,
            "total_words": total_words,
        }

    # ---------------------------------------------------------------------------
    # Normalized CRUD & Tree Primitives: Projects & Hierarchies
    # ---------------------------------------------------------------------------
    def create_project(
        self,
        title: str,
        slug: Optional[str] = None,
        parent_id: Optional[int] = None,
        description: str = "",
        order_index: Optional[int] = None,
        zid: Optional[str] = None,
    ) -> int:
        """
        Creates a new project node in the hierarchy.
        """
        clean_title = (title or "").strip()
        if not clean_title:
            raise QuerySecurityError("INVALID_STATE", "Project title cannot be empty.")

        if not slug:
            clean_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", clean_title.lower()).strip("-") or "project"
        else:
            clean_slug = slug.strip()

        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            if order_index is None:
                if parent_id is None:
                    cursor.execute("SELECT COALESCE(MAX(order_index) + 1, 0) FROM projects WHERE parent_id IS NULL AND deleted_at IS NULL;")
                else:
                    cursor.execute("SELECT COALESCE(MAX(order_index) + 1, 0) FROM projects WHERE parent_id = ? AND deleted_at IS NULL;", (parent_id,))
                calc_order = cursor.fetchone()[0]
            else:
                calc_order = int(order_index)

            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT INTO projects (parent_id, title, slug, description, order_index, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (parent_id, clean_title, clean_slug, description or "", calc_order, now_iso, now_iso),
            )
            return cursor.lastrowid

    def get_project(
        self, project_id: int, include_deleted: bool = False, zid: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches a project record by ID.
        """
        sql = "SELECT * FROM projects WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += ";"
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (project_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_projects(
        self,
        parent_id: Optional[Union[int, str]] = "all",
        include_deleted: bool = False,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Lists projects filtered by parent_id and soft-deletion status.
        parent_id="all" returns all projects.
        parent_id=None returns only root projects.
        parent_id=int returns children of specified project.
        """
        conditions = []
        params: List[Any] = []

        if not include_deleted:
            conditions.append("deleted_at IS NULL")

        if parent_id != "all":
            if parent_id is None:
                conditions.append("parent_id IS NULL")
            else:
                conditions.append("parent_id = ?")
                params.append(parent_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM projects {where_clause} ORDER BY order_index ASC, id ASC;"

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def update_project(
        self, project_id: int, updates: Dict[str, Any], zid: Optional[str] = None
    ) -> bool:
        """
        Updates fields of an existing project record.
        """
        if not updates:
            return False

        allowed_cols = {"parent_id", "title", "slug", "description", "order_index", "updated_at", "deleted_at"}
        valid_updates = {k: v for k, v in updates.items() if k in allowed_cols}
        if "updated_at" not in valid_updates:
            valid_updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clauses = [f"{col} = ?" for col in valid_updates.keys()]
        values = list(valid_updates.values())
        values.append(project_id)

        sql = f"UPDATE projects SET {', '.join(set_clauses)} WHERE id = ?;"
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, values)
            return cursor.rowcount > 0

    def soft_delete_project(self, project_id: int, zid: Optional[str] = None) -> bool:
        """
        Soft-deletes a project and its entire descendant subtree (depth < 100).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE projects SET deleted_at = ?
                WHERE id IN (
                    WITH RECURSIVE project_subtree(id, depth) AS (
                        SELECT id, 0 FROM projects WHERE id = ?
                        UNION ALL
                        SELECT p.id, ps.depth + 1 FROM projects p JOIN project_subtree ps ON p.parent_id = ps.id WHERE ps.depth < 100
                    )
                    SELECT id FROM project_subtree
                ) AND deleted_at IS NULL;
                """,
                (now_iso, project_id),
            )
            return cursor.rowcount > 0

    def restore_project(
        self, project_id: int, restore_parents: bool = True, zid: Optional[str] = None
    ) -> bool:
        """
        Restores a soft-deleted project and its descendants, and optionally restores parent chain.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE projects SET deleted_at = NULL
                WHERE id IN (
                    WITH RECURSIVE project_subtree(id, depth) AS (
                        SELECT id, 0 FROM projects WHERE id = ?
                        UNION ALL
                        SELECT p.id, ps.depth + 1 FROM projects p JOIN project_subtree ps ON p.parent_id = ps.id WHERE ps.depth < 100
                    )
                    SELECT id FROM project_subtree
                ) AND deleted_at IS NOT NULL;
                """,
                (project_id,),
            )
            count = cursor.rowcount

            if restore_parents:
                cursor.execute(
                    """
                    UPDATE projects SET deleted_at = NULL
                    WHERE id IN (
                        WITH RECURSIVE project_ancestors(parent_id, depth) AS (
                            SELECT parent_id, 0 FROM projects WHERE id = ?
                            UNION ALL
                            SELECT p.parent_id, pa.depth + 1 FROM projects p JOIN project_ancestors pa ON p.id = pa.parent_id WHERE pa.depth < 100 AND p.parent_id IS NOT NULL
                        )
                        SELECT parent_id FROM project_ancestors WHERE parent_id IS NOT NULL
                    ) AND deleted_at IS NOT NULL;
                    """,
                    (project_id,),
                )
            return count > 0

    def delete_project(self, project_id: int, zid: Optional[str] = None) -> bool:
        """
        Hard-deletes a project by ID (cascading to children and project_sessions).
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM projects WHERE id = ?;", (project_id,))
            return cursor.rowcount > 0

    def get_deleted_projects(
        self, limit: Optional[int] = None, offset: int = 0, zid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all soft-deleted projects.
        """
        sql = "SELECT * FROM projects WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        params: List[Any] = []
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def purge_deleted_projects(
        self, older_than_days: Optional[float] = None, zid: Optional[str] = None
    ) -> int:
        """
        Permanently hard-deletes soft-deleted projects.
        """
        sql = "DELETE FROM projects WHERE deleted_at IS NOT NULL"
        params: List[Any] = []
        if older_than_days is not None:
            sql += " AND (julianday('now') - julianday(deleted_at)) > ?"
            params.append(older_than_days)
        sql += ";"

        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor.rowcount

    def get_project_path(
        self, project_id: int, include_deleted: bool = False, zid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Resolves ancestral path from root to current project node using recursive CTE.
        Bounded by depth < 100.
        """
        del_filter_base = "" if include_deleted else "AND deleted_at IS NULL"
        del_filter_join = "" if include_deleted else "AND p.deleted_at IS NULL"
        sql = f"""
            WITH RECURSIVE project_path(id, parent_id, title, slug, description, order_index, created_at, updated_at, deleted_at, depth) AS (
                SELECT id, parent_id, title, slug, description, order_index, created_at, updated_at, deleted_at, 0
                FROM projects
                WHERE id = ? {del_filter_base}
                UNION ALL
                SELECT p.id, p.parent_id, p.title, p.slug, p.description, p.order_index, p.created_at, p.updated_at, p.deleted_at, pp.depth + 1
                FROM projects p
                JOIN project_path pp ON p.id = pp.parent_id
                WHERE pp.depth < 100 {del_filter_join}
            )
            SELECT id, parent_id, title, slug, description, order_index, created_at, updated_at, deleted_at
            FROM project_path
            ORDER BY depth DESC;
        """
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (project_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_project_tree(
        self,
        project_id: Optional[int] = None,
        include_deleted: bool = False,
        max_depth: int = 100,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns a hierarchical project tree with nested 'children' and linked 'sessions'.
        If project_id is None, returns all root projects with their nested trees.
        """
        del_clause = "" if include_deleted else "WHERE deleted_at IS NULL"
        sql = f"SELECT * FROM projects {del_clause} ORDER BY order_index ASC, id ASC;"
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            all_projects = [dict(r) for r in cursor.fetchall()]

            sess_del_join = "" if include_deleted else "AND s.deleted_at IS NULL"
            ps_sql = f"""
                SELECT ps.project_id, ps.session_zid, ps.order_index, ps.added_at,
                       s.slug, s.source_language, s.target_language, s.text_mode, s.created_at
                FROM project_sessions ps
                JOIN sessions s ON ps.session_zid = s.zid {sess_del_join}
                ORDER BY ps.order_index ASC, ps.added_at ASC;
            """
            cursor.execute(ps_sql)
            all_links = [dict(r) for r in cursor.fetchall()]

        project_sessions_map: Dict[int, List[Dict[str, Any]]] = {}
        for link in all_links:
            p_id = link["project_id"]
            project_sessions_map.setdefault(p_id, []).append(link)

        node_map: Dict[int, Dict[str, Any]] = {}
        for p in all_projects:
            node = dict(p)
            node["children"] = []
            node["sessions"] = project_sessions_map.get(p["id"], [])
            node_map[p["id"]] = node

        roots: List[Dict[str, Any]] = []
        for p in all_projects:
            node = node_map[p["id"]]
            p_id = p.get("parent_id")
            if p_id and p_id in node_map:
                node_map[p_id]["children"].append(node)
            else:
                roots.append(node)

        if project_id is not None:
            if project_id in node_map:
                return [node_map[project_id]]
            return []

        return roots

    # ---------------------------------------------------------------------------
    # Session Linking & Reordering Methods
    # ---------------------------------------------------------------------------
    def link_session_to_project(
        self,
        project_id: int,
        session_zid: str,
        order_index: Optional[int] = None,
        zid: Optional[str] = None,
    ) -> bool:
        """
        Links a session to a project node with ordered sequencing.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            if order_index is None:
                cursor.execute(
                    "SELECT COALESCE(MAX(order_index) + 1, 0) FROM project_sessions WHERE project_id = ?;",
                    (project_id,),
                )
                calc_order = cursor.fetchone()[0]
            else:
                calc_order = int(order_index)

            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT INTO project_sessions (project_id, session_zid, order_index, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, session_zid) DO UPDATE SET
                    order_index = excluded.order_index;
                """,
                (project_id, session_zid, calc_order, now_iso),
            )
            return cursor.rowcount > 0

    def unlink_session_from_project(
        self, project_id: int, session_zid: str, zid: Optional[str] = None
    ) -> bool:
        """
        Unlinks a session from a project node.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM project_sessions WHERE project_id = ? AND session_zid = ?;",
                (project_id, session_zid),
            )
            return cursor.rowcount > 0

    def reorder_project_sessions(
        self, project_id: int, session_zids: List[str], zid: Optional[str] = None
    ) -> bool:
        """
        Atomically updates the order_index of sessions linked to a project.
        """
        if not session_zids:
            return False
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            for idx, szid in enumerate(session_zids):
                cursor.execute(
                    "UPDATE project_sessions SET order_index = ? WHERE project_id = ? AND session_zid = ?;",
                    (idx, project_id, szid),
                )
            return True

    def get_project_sessions(
        self,
        project_id: int,
        include_deleted_sessions: bool = False,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns all sessions linked to a project ordered by order_index ASC, added_at ASC.
        """
        del_join = "" if include_deleted_sessions else "AND s.deleted_at IS NULL"
        sql = f"""
            SELECT ps.project_id, ps.session_zid, ps.order_index, ps.added_at,
                   s.slug, s.source_language, s.target_language, s.text_mode, s.source_raw_text,
                   s.created_at, s.updated_at, s.deleted_at
            FROM project_sessions ps
            JOIN sessions s ON ps.session_zid = s.zid {del_join}
            WHERE ps.project_id = ?
            ORDER BY ps.order_index ASC, ps.added_at ASC;
        """
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (project_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_session_projects(
        self,
        session_zid: str,
        include_deleted_projects: bool = False,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns all projects linked to a session ordered by p.order_index ASC, p.id ASC.
        """
        del_join = "" if include_deleted_projects else "AND p.deleted_at IS NULL"
        sql = f"""
            SELECT p.*, ps.order_index as session_order_index, ps.added_at as linked_at
            FROM project_sessions ps
            JOIN projects p ON ps.project_id = p.id {del_join}
            WHERE ps.session_zid = ?
            ORDER BY p.order_index ASC, p.id ASC;
        """
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (session_zid,))
            return [dict(r) for r in cursor.fetchall()]

    # ---------------------------------------------------------------------------
    # Normalized CRUD Helpers: Sentences
    # ---------------------------------------------------------------------------
    def insert_sentence(self, sentence: Dict[str, Any], zid: Optional[str] = None):
        """
        Inserts or updates a single sentence record.
        """
        self.insert_sentences([sentence], zid=zid)

    def insert_sentences(self, sentences: List[Dict[str, Any]], zid: Optional[str] = None):
        """
        Batch inserts or updates sentences for a session.
        """
        if not sentences:
            return

        sql = """
            INSERT INTO sentences (
                session_zid, sentence_index, sentence_source, sentence_destination,
                sentence_destination2, sentence_source_ipa, sentence_source_audio
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_zid, sentence_index) DO UPDATE SET
                sentence_source = excluded.sentence_source,
                sentence_destination = excluded.sentence_destination,
                sentence_destination2 = excluded.sentence_destination2,
                sentence_source_ipa = excluded.sentence_source_ipa,
                sentence_source_audio = excluded.sentence_source_audio;
        """
        records = [
            (
                s["session_zid"],
                s["sentence_index"],
                s["sentence_source"],
                s.get("sentence_destination"),
                s.get("sentence_destination2"),
                s.get("sentence_source_ipa"),
                s.get("sentence_source_audio"),
            )
            for s in sentences
        ]

        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.executemany(sql, records)

    def get_sentences_by_session(self, session_zid: str, zid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns all sentences for a session ordered by sentence_index ASC.
        """
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM sentences WHERE session_zid = ? ORDER BY sentence_index ASC;",
                (session_zid,),
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_sentence(self, session_zid: str, sentence_index: int, zid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Fetches a single sentence record by session_zid and sentence_index.
        """
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM sentences WHERE session_zid = ? AND sentence_index = ?;",
                (session_zid, sentence_index),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_sentence(self, session_zid: str, sentence_index: int, zid: Optional[str] = None) -> bool:
        """
        Deletes a specific sentence. Associated words are cascade deleted.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sentences WHERE session_zid = ? AND sentence_index = ?;",
                (session_zid, sentence_index),
            )
            return cursor.rowcount > 0

    # ---------------------------------------------------------------------------
    # Normalized CRUD Helpers: Words
    # ---------------------------------------------------------------------------
    def _serialize_extra_fields(self, extra_fields: Any) -> Optional[str]:
        if extra_fields is None:
            return None
        if isinstance(extra_fields, (dict, list)):
            return json.dumps(extra_fields, ensure_ascii=False)
        return str(extra_fields)

    def _deserialize_extra_fields(self, record: Dict[str, Any]) -> Dict[str, Any]:
        raw = record.get("extra_fields")
        if raw and isinstance(raw, str):
            try:
                record["extra_fields"] = json.loads(raw)
            except Exception:
                pass
        return record

    def insert_word(self, word: Dict[str, Any], zid: Optional[str] = None) -> int:
        """
        Inserts a single word token record and returns its autoincrement ID.
        """
        ids = self.insert_words([word], zid=zid)
        return ids[0] if ids else -1

    def insert_words(self, words: List[Dict[str, Any]], zid: Optional[str] = None) -> List[int]:
        """
        Batch inserts word token records and returns their autoincrement IDs.
        """
        if not words:
            return []

        sql = """
            INSERT INTO words (
                session_zid, sentence_index, token_order, quotation, inflected_form,
                lemma, pos, morphology, ipa, word_destination, word_destination_inflected,
                selected, leitner_box, leitner_due, deck, classification_oxford,
                classification_goethe, extra_fields
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        inserted_ids: List[int] = []

        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            for w in words:
                cursor.execute(
                    sql,
                    (
                        w["session_zid"],
                        w["sentence_index"],
                        w.get("token_order", 0),
                        w["quotation"],
                        w.get("inflected_form"),
                        w["lemma"],
                        w.get("pos"),
                        w.get("morphology"),
                        w.get("ipa"),
                        w.get("word_destination"),
                        w.get("word_destination_inflected"),
                        w.get("selected", 0),
                        w.get("leitner_box", 1),
                        w.get("leitner_due"),
                        w.get("deck"),
                        w.get("classification_oxford"),
                        w.get("classification_goethe"),
                        self._serialize_extra_fields(w.get("extra_fields")),
                    ),
                )
                inserted_ids.append(cursor.lastrowid)

        return inserted_ids

    def get_words_by_session(
        self,
        session_zid: str,
        sentence_index: Optional[int] = None,
        parse_json: bool = True,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns words for a session (optionally filtered by sentence_index)
        ordered by token_order ASC, id ASC.
        """
        sql = "SELECT * FROM words WHERE session_zid = ?"
        params: List[Any] = [session_zid]
        if sentence_index is not None:
            sql += " AND sentence_index = ?"
            params.append(sentence_index)
        sql += " ORDER BY sentence_index ASC, token_order ASC, id ASC;"

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()]
            if parse_json:
                rows = [self._deserialize_extra_fields(r) for r in rows]
            return rows

    def get_word(
        self, word_id: int, parse_json: bool = True, zid: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches a word token record by primary key ID.
        """
        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM words WHERE id = ?;", (word_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            return self._deserialize_extra_fields(res) if parse_json else res

    def find_words_by_lemma(
        self,
        lemma: str,
        session_zid: Optional[str] = None,
        parse_json: bool = True,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Index-backed case-insensitive search on words by lemma (COLLATE NOCASE).
        """
        sql = "SELECT * FROM words WHERE lemma = ?"
        params: List[Any] = [lemma]
        if session_zid:
            sql += " AND session_zid = ?"
            params.append(session_zid)
        sql += " ORDER BY id ASC;"

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()]
            if parse_json:
                rows = [self._deserialize_extra_fields(r) for r in rows]
            return rows

    def find_words_by_quotation(
        self,
        quotation: str,
        session_zid: Optional[str] = None,
        parse_json: bool = True,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Index-backed case-insensitive search on words by quotation token (COLLATE NOCASE).
        """
        sql = "SELECT * FROM words WHERE quotation = ?"
        params: List[Any] = [quotation]
        if session_zid:
            sql += " AND session_zid = ?"
            params.append(session_zid)
        sql += " ORDER BY id ASC;"

        with self.get_connection(read_only=True, zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()]
            if parse_json:
                rows = [self._deserialize_extra_fields(r) for r in rows]
            return rows

    def find_wordfill_candidates(
        self,
        word: str,
        language: Optional[str] = None,
        exclude_zid: Optional[str] = None,
        limit: int = 10,
        zid: Optional[str] = None,
        conn: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fast indexed search for wordfill candidates querying words and sessions.
        Matches lemma, quotation, or inflected_form (case-insensitively).
        """
        if not word or not word.strip():
            return []

        clean_word = word.strip()
        sql = """
            SELECT 
                w.lemma, w.quotation, w.inflected_form, w.word_destination,
                w.pos, w.morphology, w.ipa, w.extra_fields,
                s.created_at, s.zid as session_zid, s.source_language
            FROM words w
            JOIN sessions s ON w.session_zid = s.zid
            WHERE (w.lemma = ? OR w.quotation = ? OR w.inflected_form = ?)
              AND s.deleted_at IS NULL
        """
        params: List[Any] = [clean_word, clean_word, clean_word]

        if language:
            sql += " AND s.source_language = ?"
            params.append(language)

        if exclude_zid:
            sql += " AND s.zid != ?"
            params.append(exclude_zid)

        sql += " ORDER BY s.created_at DESC LIMIT ?;"
        params.append(limit)

        if conn is not None:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()]
            return [self._deserialize_extra_fields(r) for r in rows]

        with self.get_connection(read_only=True, zid=zid) as c:
            cursor = c.cursor()
            cursor.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()]
            return [self._deserialize_extra_fields(r) for r in rows]

    def update_word(
        self, word_id: int, updates: Dict[str, Any], zid: Optional[str] = None
    ) -> bool:
        """
        Updates fields of a word token record.
        """
        if not updates:
            return False

        allowed_cols = {
            "sentence_index", "token_order", "quotation", "inflected_form",
            "lemma", "pos", "morphology", "ipa", "word_destination",
            "word_destination_inflected", "selected", "leitner_box", "leitner_due",
            "deck", "classification_oxford", "classification_goethe", "extra_fields",
        }
        valid_updates: Dict[str, Any] = {}
        for k, v in updates.items():
            if k in allowed_cols:
                if k == "extra_fields":
                    valid_updates[k] = self._serialize_extra_fields(v)
                else:
                    valid_updates[k] = v

        if not valid_updates:
            return False

        set_clauses = [f"{col} = ?" for col in valid_updates.keys()]
        values = list(valid_updates.values())
        values.append(word_id)

        sql = f"UPDATE words SET {', '.join(set_clauses)} WHERE id = ?;"
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, values)
            return cursor.rowcount > 0

    def delete_word(self, word_id: int, zid: Optional[str] = None) -> bool:
        """
        Deletes a word token record by primary key ID.
        """
        with self.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM words WHERE id = ?;", (word_id,))
            return cursor.rowcount > 0

    # ---------------------------------------------------------------------------
    # Atomic Session Bundle Operations
    # ---------------------------------------------------------------------------
    def save_session_bundle(
        self,
        session: Dict[str, Any],
        sentences: List[Dict[str, Any]],
        words: List[Dict[str, Any]],
        zid: Optional[str] = None,
    ) -> str:
        """
        Saves a session, its deduplicated sentences, and words inside a single atomic transaction.
        """
        session_zid = session["zid"]
        slug = session.get("slug", "")
        source_lang = session.get("source_language", "")
        target_lang = session.get("target_language", "")
        text_mode = session.get("text_mode", "single")
        source_raw_text = session.get("source_raw_text", "")
        created_at = session.get("created_at") or datetime.now(timezone.utc).isoformat()
        updated_at = session.get("updated_at") or datetime.now(timezone.utc).isoformat()

        with self.get_connection(zid=zid) as conn:
            # 1. Insert/update session
            conn.execute(
                """
                INSERT INTO sessions (
                    zid, slug, source_language, target_language, text_mode,
                    source_raw_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(zid) DO UPDATE SET
                    slug = excluded.slug,
                    source_language = excluded.source_language,
                    target_language = excluded.target_language,
                    text_mode = excluded.text_mode,
                    source_raw_text = excluded.source_raw_text,
                    updated_at = excluded.updated_at;
                """,
                (session_zid, slug, source_lang, target_lang, text_mode, source_raw_text, created_at, updated_at),
            )

            # 2. Insert sentences
            if sentences:
                sent_sql = """
                    INSERT INTO sentences (
                        session_zid, sentence_index, sentence_source, sentence_destination,
                        sentence_destination2, sentence_source_ipa, sentence_source_audio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_zid, sentence_index) DO UPDATE SET
                        sentence_source = excluded.sentence_source,
                        sentence_destination = excluded.sentence_destination,
                        sentence_destination2 = excluded.sentence_destination2,
                        sentence_source_ipa = excluded.sentence_source_ipa,
                        sentence_source_audio = excluded.sentence_source_audio;
                """
                sent_records = [
                    (
                        s.get("session_zid", session_zid),
                        s["sentence_index"],
                        s["sentence_source"],
                        s.get("sentence_destination"),
                        s.get("sentence_destination2"),
                        s.get("sentence_source_ipa"),
                        s.get("sentence_source_audio"),
                    )
                    for s in sentences
                ]
                conn.executemany(sent_sql, sent_records)

            # 3. Insert words (clear previous session words first to guarantee no duplicate rows)
            if words:
                conn.execute("DELETE FROM words WHERE session_zid = ?;", (session_zid,))
                word_sql = """
                    INSERT INTO words (
                        session_zid, sentence_index, token_order, quotation, inflected_form,
                        lemma, pos, morphology, ipa, word_destination, word_destination_inflected,
                        selected, leitner_box, leitner_due, deck, classification_oxford,
                        classification_goethe, extra_fields
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                word_records = [
                    (
                        w.get("session_zid", session_zid),
                        w["sentence_index"],
                        w.get("token_order", 0),
                        w["quotation"],
                        w.get("inflected_form"),
                        w["lemma"],
                        w.get("pos"),
                        w.get("morphology"),
                        w.get("ipa"),
                        w.get("word_destination"),
                        w.get("word_destination_inflected"),
                        w.get("selected", 0),
                        w.get("leitner_box", 1),
                        w.get("leitner_due"),
                        w.get("deck"),
                        w.get("classification_oxford"),
                        w.get("classification_goethe"),
                        self._serialize_extra_fields(w.get("extra_fields")),
                    )
                    for w in words
                ]
                conn.executemany(word_sql, word_records)

        return session_zid

    def get_session_bundle(
        self, session_zid: str, parse_json: bool = True, zid: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves the complete bundle for a session: session record, sentences, and words.
        """
        session = self.get_session(session_zid, zid=zid)
        if not session:
            return None

        sentences = self.get_sentences_by_session(session_zid, zid=zid)
        words = self.get_words_by_session(session_zid, parse_json=parse_json, zid=zid)

        return {
            "session": session,
            "sentences": sentences,
            "words": words,
        }


# ---------------------------------------------------------------------------
# Custom Database Exceptions
# ---------------------------------------------------------------------------
class QuerySecurityError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class QueryExecutionError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
