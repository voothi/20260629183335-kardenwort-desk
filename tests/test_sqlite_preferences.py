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


def _pre_migration_env(tmp_path, name, session_zid, text_value):
    from pathlib import Path
    mig_dir = tmp_path / f'{name}_migrations'
    mig_dir.mkdir(exist_ok=True)
    source = Path(__file__).resolve().parents[1] / 'schemas/migrations'
    from kardenwort_state_backup import MIGRATION
    for path in source.glob('*.sql'):
        if path.name != MIGRATION:
            (mig_dir / path.name).write_bytes(path.read_bytes())
    db = KardenwortDB(db_path=tmp_path / f'{name}.db', migrations_dir=mig_dir,
                      resolved_paths={'results_dir': tmp_path / f'{name}_results'})
    db.run_migrations()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions(zid, slug, source_language, source_raw_text) VALUES (?, ?, 'de', ?)",
            (session_zid, name, text_value),
        )
    return db, mig_dir, source / MIGRATION


def test_backup_rejects_wrong_owner_and_post_ddl_without_modifying_files(tmp_path):
    from kardenwort_db import QueryExecutionError
    from kardenwort_state_backup import MIGRATION
    owner_db, owner_mig_dir, mig_src = _pre_migration_env(tmp_path, 'owner', '20261116210010', 'Owner text')
    foreign_db, _, _ = _pre_migration_env(tmp_path, 'foreign', '20261116210011', 'Foreign text')

    backup = owner_db.db_path.with_name(owner_db.db_path.name + '.before-preferences.sqlite')
    backup.write_bytes(foreign_db.db_path.read_bytes())
    wrong_owner_bytes = backup.read_bytes()

    (owner_mig_dir / MIGRATION).write_bytes(mig_src.read_bytes())
    with pytest.raises(QueryExecutionError):
        owner_db.run_migrations()
    assert backup.read_bytes() == wrong_owner_bytes
    with owner_db.get_connection(read_only=True) as conn:
        assert conn.execute("SELECT 1 FROM _migrations WHERE filename=?", (MIGRATION,)).fetchone() is None
        assert conn.execute("SELECT source_raw_text FROM sessions").fetchone()[0] == 'Owner text'

    # Now place a valid post-DDL SQLite database at backup target and verify rejection
    foreign_mig_dir = tmp_path / 'foreign_migrations'
    (foreign_mig_dir / MIGRATION).write_bytes(mig_src.read_bytes())
    foreign_db.run_migrations()
    foreign_backup = foreign_db.db_path.with_name(foreign_db.db_path.name + '.before-preferences.sqlite')
    assert foreign_backup.exists()
    backup.write_bytes(foreign_db.db_path.read_bytes())
    post_ddl_bytes = backup.read_bytes()
    with pytest.raises(QueryExecutionError):
        owner_db.run_migrations()
    assert backup.read_bytes() == post_ddl_bytes


def test_concurrent_migrators_publish_single_verified_backup_and_clean_own_tmp(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from kardenwort_state_backup import MIGRATION

    owner_db, mig_dir, mig_src = _pre_migration_env(tmp_path, 'concurrent', '20261116210020', 'Before concurrent')
    (mig_dir / MIGRATION).write_bytes(mig_src.read_bytes())

    db_a = KardenwortDB(db_path=owner_db.db_path, migrations_dir=mig_dir,
                        resolved_paths={'results_dir': tmp_path / 'res_a'}, busy_timeout_ms=5000)
    db_b = KardenwortDB(db_path=owner_db.db_path, migrations_dir=mig_dir,
                        resolved_paths={'results_dir': tmp_path / 'res_b'}, busy_timeout_ms=5000)
    barrier = threading.Barrier(2)

    def run_worker(inst):
        barrier.wait(timeout=5)
        return inst.run_migrations()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(run_worker, db_a)
        fb = pool.submit(run_worker, db_b)
        res_a = fa.result(timeout=10)
        res_b = fb.result(timeout=10)

    assert res_a['ok'] is True and res_b['ok'] is True
    assert sorted([len(res_a['applied']), len(res_b['applied'])]) == [0, 1]
    backup = owner_db.db_path.with_name(owner_db.db_path.name + '.before-preferences.sqlite')
    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('SELECT source_raw_text FROM sessions').fetchone()[0] == 'Before concurrent'
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='preferences'").fetchone() is None
    assert list(tmp_path.glob('*.tmp')) == []


def test_backup_copy_and_ddl_failure_preserve_source_and_clean_only_own_staging(tmp_path, monkeypatch):
    import kardenwort_state_backup as backup_mod
    from kardenwort_db import QueryExecutionError

    owner_db, mig_dir, mig_src = _pre_migration_env(tmp_path, 'failures', '20261116210030', 'Before fail')
    (mig_dir / backup_mod.MIGRATION).write_bytes(mig_src.read_bytes())

    # Part A: simulated backup copy failure cleans only its own staging artifact
    orig_checked_backup = backup_mod.checked_backup

    def failing_copy(source, staging):
        staging.write_bytes(b'partial-staging')
        raise sqlite3.DatabaseError('Simulated backup copy failure')

    monkeypatch.setattr(backup_mod, 'checked_backup', failing_copy)
    with pytest.raises(QueryExecutionError):
        owner_db.run_migrations()
    backup = owner_db.db_path.with_name(owner_db.db_path.name + '.before-preferences.sqlite')
    assert not backup.exists()
    assert list(tmp_path.glob('*.tmp')) == []
    with owner_db.get_connection(read_only=True) as conn:
        assert conn.execute('SELECT source_raw_text FROM sessions').fetchone()[0] == 'Before fail'

    # Part B: restore checked_backup, inject bad DDL into preference migration, verify atomic rollback + recoverable backup
    monkeypatch.setattr(backup_mod, 'checked_backup', orig_checked_backup)
    (mig_dir / backup_mod.MIGRATION).write_text(
        mig_src.read_text(encoding='utf-8') + '\nCREATE TABLE broken_ddl (INVALID SYNTAX HERE;',
        encoding='utf-8',
    )
    with pytest.raises(QueryExecutionError):
        owner_db.run_migrations()
    assert backup.exists()
    assert list(tmp_path.glob('*.tmp')) == []
    with owner_db.get_connection(read_only=True) as conn:
        assert conn.execute("SELECT 1 FROM _migrations WHERE filename=?", (backup_mod.MIGRATION,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='preferences'").fetchone() is None
        assert conn.execute('SELECT source_raw_text FROM sessions').fetchone()[0] == 'Before fail'
    with sqlite3.connect(backup) as bconn:
        assert bconn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert bconn.execute('SELECT source_raw_text FROM sessions').fetchone()[0] == 'Before fail'

