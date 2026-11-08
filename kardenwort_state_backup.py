"""An owned, verified pre-migration SQLite snapshot, never a fallback owner."""
import sqlite3
from contextlib import closing


MIGRATION = '20261108200139_preferences_and_client_views.sql'


def checked_backup(source, target):
    with closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        result = destination.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise sqlite3.DatabaseError('Pre-migration backup failed integrity check')


def backup_preference_migration(db, migration):
    if migration != MIGRATION:
        return None
    target = db.db_path.with_name(db.db_path.name+'.before-preferences.sqlite')
    if target.exists():
        with closing(sqlite3.connect(target.as_uri()+'?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('Existing pre-migration backup is invalid')
        return target
    source = db.get_connection(read_only=True)
    try:
        checked_backup(source, target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    return target
