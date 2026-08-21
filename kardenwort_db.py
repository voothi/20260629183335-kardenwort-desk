"""
kardenwort_db.py - Foundational SQLite Connection Manager and Diagnostics for Kardenwort-Desk

Provides zero-dependency database access, WAL mode management, structured logging to results/db.log,
integrity diagnostics, safe sandboxed read-only querying, and test teardown facilities.
"""

import os
import sys
import json
import time
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Union

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
# KardenwortDB Connection and Diagnostics Manager
# ---------------------------------------------------------------------------
class KardenwortDB:
    """
    Foundational connection manager for Kardenwort SQLite databases.
    Enforces WAL journal mode, busy timeouts, foreign key constraints,
    and provides integrity checks and sandboxed query evaluation.
    """

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        config: Optional[Any] = None,
        resolved_paths: Optional[Dict[str, Any]] = None,
        busy_timeout_ms: int = 5000,
    ):
        self.config = config
        self.resolved_paths = resolved_paths
        self.busy_timeout_ms = busy_timeout_ms

        # Resolve workspace / data root
        workspace_dir = Path(__file__).resolve().parent
        if resolved_paths and "kardenwort_workspace" in resolved_paths:
            kw_ws = resolved_paths.get("kardenwort_workspace")
            if kw_ws:
                workspace_dir = Path(kw_ws)

        if db_path:
            self.db_path = Path(db_path).resolve()
        else:
            self.db_path = (workspace_dir / "data" / "kardenwort.db").resolve()

        # Ensure parent data directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Configure db logger destination
        results_dir = workspace_dir / "results"
        if resolved_paths and "results_dir" in resolved_paths:
            r_dir = resolved_paths.get("results_dir")
            if r_dir:
                results_dir = Path(r_dir)
        elif config and hasattr(config, "get"):
            r_dir = config.get("settings", "results_dir", fallback=None)
            if r_dir:
                results_dir = Path(r_dir)

        max_mb = 5.0
        if config and hasattr(config, "getfloat"):
            max_mb = config.getfloat("profiling", "trace_log_max_mb", fallback=config.getfloat("settings", "trace_log_max_mb", fallback=5.0))

        _GLOBAL_DB_LOGGER.set_log_path(results_dir / "db.log", max_mb=max_mb)
        self.logger = _GLOBAL_DB_LOGGER

    @property
    def wal_path(self) -> Path:
        return Path(f"{self.db_path}-wal")

    @property
    def shm_path(self) -> Path:
        return Path(f"{self.db_path}-shm")

    def get_connection(self, read_only: bool = False, zid: Optional[str] = None) -> sqlite3.Connection:
        """
        Creates and configures an optimized SQLite connection with WAL mode,
        busy timeout, and foreign keys enabled.
        """
        start_t = time.perf_counter()
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row

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
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('schema_migrations', '_migrations');"
                )
                mig_table = cursor.fetchone()
                if mig_table:
                    t_name = mig_table[0]
                    cursor.execute(f"SELECT version FROM {t_name} ORDER BY version ASC;")
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
            # Strictly allow only read operations
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
            # Map SQLite denial or query_only write violations to MUTATION_NOT_ALLOWED
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
