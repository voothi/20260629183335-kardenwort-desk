"""Daily-use preference migration and revisions use real isolated SQLite."""
import configparser
import sqlite3

import pytest

from kardenwort_db import KardenwortDB


def database(tmp_path):
    db = KardenwortDB(db_path=tmp_path/'owned.db', resolved_paths={'results_dir': tmp_path/'results'})
    db.run_migrations()
    return db


def test_preference_and_view_schema_is_forward_only_and_relational(tmp_path):
    db = database(tmp_path)
    with db.get_connection(read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'preferences', 'preference_metadata', 'client_view_state', 'browser_state_imports'} <= tables
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    assert db.run_migrations()['applied'] == []
    with db.get_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO client_view_state(session_zid,client_id) VALUES ('missing',?)", ('a'*32,))


def test_static_import_is_atomic_idempotent_and_preserves_edits(tmp_path):
    from kardenwort_sqlite_preferences import PreferencesAdapter
    db = database(tmp_path)
    config = configparser.ConfigParser()
    config.read_dict({'rendering': {'filter_selected_only': 'true'}, 'settings': {'default_language': 'de'}})
    store = PreferencesAdapter(db)
    store.initialize(config)
    before = store.snapshot()
    assert before['values']['filter_selected_only'] is True
    assert before['values']['auto_save_on_edit'] is False
    changed = store.set('filter_selected_only', False, expected_revision=before['revisions']['filter_selected_only'])
    assert changed['revision'] == before['revisions']['filter_selected_only']+1
    config.set('rendering', 'filter_selected_only', 'true')
    restarted = PreferencesAdapter(db)
    restarted.initialize(config)
    assert restarted.snapshot()['values']['filter_selected_only'] is False


def test_invalid_initial_import_rolls_back_without_completion_marker(tmp_path):
    from kardenwort_sqlite_preferences import PreferencesAdapter
    db = database(tmp_path)
    config = configparser.ConfigParser()
    config.read_dict({'rendering': {'filter_selected_only': 'invalid'}})
    with pytest.raises(ValueError):
        PreferencesAdapter(db).initialize(config)
    with db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM preferences').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM preference_metadata').fetchone()[0] == 0


def test_owned_backup_precedes_additive_migration_and_never_replaces_new_edits(tmp_path):
    from kardenwort_state_backup import MIGRATION
    from kardenwort_sqlite_preferences import PreferencesAdapter
    from pathlib import Path
    migrations = tmp_path/'migrations'
    migrations.mkdir()
    source = Path(__file__).resolve().parents[1]/'schemas/migrations'
    for path in source.glob('*.sql'):
        if path.name != MIGRATION:
            (migrations/path.name).write_bytes(path.read_bytes())
    db = KardenwortDB(db_path=tmp_path/'upgrade.db', migrations_dir=migrations,
                     resolved_paths={'results_dir': tmp_path/'results'})
    db.run_migrations()
    with db.get_connection() as connection:
        connection.execute("INSERT INTO sessions(zid,slug,source_language,source_raw_text) VALUES('20261108205000','owned','de','Before')")
    (migrations/MIGRATION).write_bytes((source/MIGRATION).read_bytes())
    db.run_migrations()
    backup = db.db_path.with_name(db.db_path.name+'.before-preferences.sqlite')
    before = backup.read_bytes()
    with sqlite3.connect(backup) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('SELECT source_raw_text FROM sessions').fetchone()[0] == 'Before'
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='preferences'").fetchone() is None
    store = PreferencesAdapter(db)
    config = configparser.ConfigParser()
    store.initialize(config)
    store.set('theme', 'light')
    with db.get_connection() as connection:
        connection.execute("UPDATE sessions SET source_raw_text='After'")
    # Code rollback retains additive tables and data; restart never restores a stale snapshot.
    reopened = KardenwortDB(db_path=db.db_path, migrations_dir=migrations,
                           resolved_paths={'results_dir': tmp_path/'results'})
    assert reopened.run_migrations()['applied'] == []
    assert PreferencesAdapter(reopened).get('theme') == 'light'
    with reopened.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT source_raw_text FROM sessions').fetchone()[0] == 'After'
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='idx_client_view_expiration'").fetchone()
    assert backup.read_bytes() == before


def test_interrupted_static_import_rolls_back_and_retry_preserves_database_values(tmp_path):
    from kardenwort_sqlite_preferences import PreferencesAdapter
    db = database(tmp_path)
    store = PreferencesAdapter(db)
    store.set('theme', 'white')
    with db.get_connection() as connection:
        connection.execute("CREATE TRIGGER interrupt_import BEFORE INSERT ON preference_metadata BEGIN SELECT RAISE(ABORT,'interrupted'); END")
    with pytest.raises(Exception, match='interrupted'):
        store.initialize(configparser.ConfigParser())
    with db.get_connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM preferences').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM preference_metadata').fetchone()[0] == 0
        connection.execute('DROP TRIGGER interrupt_import')
    stale = configparser.ConfigParser()
    stale.read_dict({'ui': {'theme': 'invalid stale value'}})
    store.initialize(stale)
    assert store.get('theme') == 'white'
    assert store.get('auto_save_on_close') is True


def test_corrupt_existing_backup_is_not_overwritten_or_treated_as_absent(tmp_path):
    from kardenwort_state_backup import MIGRATION, backup_preference_migration
    db = database(tmp_path)
    backup = db.db_path.with_name(db.db_path.name+'.before-preferences.sqlite')
    backup.write_bytes(b'owned corrupt backup')
    with pytest.raises(sqlite3.DatabaseError):
        backup_preference_migration(db, MIGRATION)
    assert backup.read_bytes() == b'owned corrupt backup'
    with db.get_connection(read_only=True) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
