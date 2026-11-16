"""Committed preference projections; configuration files remain static inputs."""
import threading
import sqlite3
from pathlib import Path

from kardenwort_db import KardenwortDB
from kardenwort_sqlite_preferences import PreferencesAdapter
from kardenwort_preference_contract import PREFERENCES


STATE_LOCK = threading.RLock()


def _store_matches(store, paths):
    configured_path = paths.get('sqlite_db_path')
    if store is None:
        return False
    if configured_path and store.db.db_path != Path(configured_path).resolve():
        return False
    return True


def preference_store(config, paths):
    with STATE_LOCK:
        store = getattr(config, '_preference_store', None)
        if _store_matches(store, paths):
            return store
    db = KardenwortDB(config=config, resolved_paths=paths)
    candidate = PreferencesAdapter(db)
    try:
        candidate.initialize(config)
    except ValueError as error:
        raise sqlite3.DatabaseError('Static preference import is invalid') from error
    with STATE_LOCK:
        existing = getattr(config, '_preference_store', None)
        if _store_matches(existing, paths):
            return existing
        config._preference_store = candidate
        config._sqlite_preferences_snapshot = None
        return candidate


def _clone_snapshot(snapshot):
    if not isinstance(snapshot, dict) or 'values' not in snapshot or 'revisions' not in snapshot:
        raise ValueError('Invalid preference snapshot')
    values, revisions = snapshot['values'], snapshot['revisions']
    if not isinstance(values, dict) or not isinstance(revisions, dict):
        raise ValueError('Invalid preference snapshot')
    return dict(values=dict(values), revisions=dict(revisions))


def _is_older_snapshot(current, candidate):
    if not current:
        return False
    cur_rev, new_rev = current.get('revisions', {}), candidate['revisions']
    return any(new_rev.get(key, 0) < cur_rev.get(key, 0) for key in PREFERENCES)


def _snapshot_entries(values):
    entries = []
    for key, value in values.items():
        if key not in PREFERENCES:
            raise ValueError('Preference key is not permitted')
        section = PREFERENCES[key][0]
        encoded = str(value).lower() if isinstance(value, bool) else str(value)
        entries.append((section, key, encoded))
    return entries


def _capture_entry(config, section, key):
    had_section = config.has_section(section)
    had_opt = had_section and config.has_option(section, key)
    prev = config.get(section, key, raw=True) if had_opt else None
    return (section, key, had_section, had_opt, prev)


def _restore_entries(config, prior):
    for section, key, had_section, had_opt, prev in reversed(prior):
        if had_opt:
            config.set(section, key, prev)
        elif not had_section and config.has_section(section):
            config.remove_section(section)
        elif config.has_option(section, key):
            config.remove_option(section, key)


def _apply_config_entries(config, entries):
    prior = []
    try:
        for section, key, encoded in entries:
            prior.append(_capture_entry(config, section, key))
            if not config.has_section(section):
                config.add_section(section)
            config.set(section, key, encoded)
    except Exception:
        _restore_entries(config, prior)
        raise


def project_preferences(config, snapshot):
    candidate = _clone_snapshot(snapshot)
    entries = _snapshot_entries(candidate['values'])
    with STATE_LOCK:
        current = getattr(config, '_sqlite_preferences_snapshot', None)
        if _is_older_snapshot(current, candidate) or current == candidate:
            return _clone_snapshot(current)
        _apply_config_entries(config, entries)
        config._sqlite_preferences_snapshot = candidate
        return _clone_snapshot(candidate)


def refresh_preferences(config, paths):
    snapshot = preference_store(config, paths).snapshot()
    return project_preferences(config, snapshot)
