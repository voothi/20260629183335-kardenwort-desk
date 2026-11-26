"""An owned, verified pre-migration SQLite snapshot, never a fallback owner."""
from contextlib import closing
import errno
import os
import sqlite3
import threading
from uuid import uuid4


MIGRATION = '20261108200139_preferences_and_client_views.sql'
_ADDITIVE_TABLES = frozenset({
    'preferences',
    'preference_metadata',
    'client_view_state',
    'browser_state_imports',
})
_UNSAFE_BACKUP_SIDECARS = ('-wal', '-shm', '-journal')


def checked_backup(source, target):
    with closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        result = destination.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise sqlite3.DatabaseError('Pre-migration backup failed integrity check')


def _schema_signature(connection):
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type ASC, name ASC"
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def _database_identity(connection):
    return tuple(
        connection.execute(f'PRAGMA {name}').fetchone()[0]
        for name in ('user_version', 'application_id', 'page_size')
    )


def _table_query(connection, table_name):
    escaped = table_name.replace('"', '""')
    try:
        connection.execute(f'SELECT _rowid_ FROM "{escaped}" LIMIT 0')
        return f'SELECT * FROM "{escaped}" ORDER BY _rowid_ ASC'
    except sqlite3.OperationalError:
        columns = [row[1].replace('"', '""') for row in connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()]
        order = ', '.join(f'"{col}" ASC' for col in columns)
        return f'SELECT * FROM "{escaped}"' + (f' ORDER BY {order}' if order else '')


def _fetch_chunk(cursor, size=512):
    return [tuple(row) for row in cursor.fetchmany(size)]


def _compare_table_streams(source_cursor, backup_cursor, table_name):
    while True:
        source_chunk = _fetch_chunk(source_cursor)
        backup_chunk = _fetch_chunk(backup_cursor)
        if source_chunk != backup_chunk:
            raise sqlite3.DatabaseError(
                f"Existing pre-migration backup content mismatch in table '{table_name}'"
            )
        if not source_chunk:
            return


def _has_pre_ddl_state(connection):
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if '_migrations' not in tables:
        return False
    if _ADDITIVE_TABLES & tables:
        return False
    applied = connection.execute(
        'SELECT 1 FROM _migrations WHERE filename = ?',
        (MIGRATION,),
    ).fetchone()
    return applied is None


def _has_sequence_table(connection):
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone())


def _verify_backup_metadata(source, backup_conn):
    if backup_conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise sqlite3.DatabaseError('Existing pre-migration backup is invalid')
    if not _has_pre_ddl_state(backup_conn):
        raise sqlite3.DatabaseError(
            'Existing pre-migration backup is not a pre-DDL preference-migration snapshot'
        )
    if not _has_pre_ddl_state(source):
        raise sqlite3.DatabaseError(
            'Source database already passed preference migration; cannot verify backup ownership'
        )
    if _database_identity(backup_conn) != _database_identity(source):
        raise sqlite3.DatabaseError(
            'Existing pre-migration backup database identity does not match source database'
        )


def _verify_backup_rows(source, backup_conn, table_names):
    for table_name in table_names:
        query = _table_query(source, table_name)
        with closing(source.execute(query)) as src_cur, closing(backup_conn.execute(query)) as bak_cur:
            _compare_table_streams(src_cur, bak_cur, table_name)


def _verify_backup_contents(source, backup_conn):
    backup_schema = _schema_signature(backup_conn)
    source_schema = _schema_signature(source)
    if backup_schema != source_schema:
        raise sqlite3.DatabaseError(
            'Existing pre-migration backup schema does not match source database'
        )
    table_names = [name for kind, name, _ in source_schema if kind == 'table']
    seq_in_source = _has_sequence_table(source)
    if seq_in_source != _has_sequence_table(backup_conn):
        raise sqlite3.DatabaseError(
            'Existing pre-migration backup sequence state does not match source database'
        )
    if seq_in_source:
        table_names.append('sqlite_sequence')
    _verify_backup_rows(source, backup_conn, table_names)


def _verify_existing_backup(source, target):
    with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as backup_conn:
        _verify_backup_metadata(source, backup_conn)
        _verify_backup_contents(source, backup_conn)


def _remove_staging_artifacts(staging):
    staging.unlink(missing_ok=True)
    for suffix in _UNSAFE_BACKUP_SIDECARS:
        staging.with_name(staging.name + suffix).unlink(missing_ok=True)


def _hardlink_unsupported(error):
    if getattr(error, 'winerror', None) in (1, 17, 50):
        return True
    unsupported = {errno.EXDEV, errno.EPERM, errno.EACCES, getattr(errno, 'ENOTSUP', -1), getattr(errno, 'EOPNOTSUPP', -1)}
    return error.errno in unsupported


def _commit_staged_backup(staging, target, source):
    try:
        os.link(staging, target)
    except FileExistsError:
        _verify_existing_backup(source, target)
    except OSError as error:
        if not _hardlink_unsupported(error):
            raise
        if target.exists():
            _verify_existing_backup(source, target)
            return
        os.replace(staging, target)


def backup_preference_migration(db, migration):
    if migration != MIGRATION:
        return None
    target = db.db_path.with_name(db.db_path.name + '.before-preferences.sqlite')
    source = db.get_connection(read_only=True)
    staging = None
    try:
        if target.exists():
            _verify_existing_backup(source, target)
            return target
        staging = target.with_name(
            f'{target.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp'
        )
        checked_backup(source, staging)
        _commit_staged_backup(staging, target, source)
        return target
    finally:
        if staging is not None:
            _remove_staging_artifacts(staging)
        source.close()
