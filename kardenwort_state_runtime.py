"""Committed preference projections; configuration files remain static inputs."""
import threading
import sqlite3
from pathlib import Path

from kardenwort_db import KardenwortDB
from kardenwort_sqlite_preferences import PreferencesAdapter
from kardenwort_preference_contract import PREFERENCES


STATE_LOCK = threading.RLock()


def preference_store(config, paths):
    with STATE_LOCK:
        store = getattr(config, '_preference_store', None)
        configured_path = paths.get('sqlite_db_path')
        if store is not None and configured_path and store.db.db_path != Path(configured_path).resolve():
            store = None
        if store is None:
            db = KardenwortDB(config=config, resolved_paths=paths)
            store = PreferencesAdapter(db)
            try:
                store.initialize(config)
            except ValueError as error:
                raise sqlite3.DatabaseError('Static preference import is invalid') from error
            config._preference_store = store
        return store


def project_preferences(config, snapshot):
    for key, value in snapshot['values'].items():
        section = PREFERENCES[key][0]
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, key, str(value).lower() if isinstance(value, bool) else str(value))
    config._sqlite_preferences_snapshot = snapshot
    return snapshot


def refresh_preferences(config, paths):
    with STATE_LOCK:
        return project_preferences(config, preference_store(config, paths).snapshot())
