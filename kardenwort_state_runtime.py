"""Committed preference projections; configuration files remain static inputs."""
import threading
import copy
import configparser
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import sqlite3
from pathlib import Path

from kardenwort_db import KardenwortDB
from kardenwort_sqlite_preferences import PreferencesAdapter, read_snapshot
from kardenwort_preference_contract import PREFERENCES, configured_preferences


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


_OPERATION_CONFIGS = ContextVar('committed_operation_configurations', default=None)


def _configuration_source(config):
    return getattr(config, '_operation_source', config)


def operation_configuration(config):
    source = _configuration_source(config)
    active = _OPERATION_CONFIGS.get() or {}
    return active.get(id(source), config)


class OperationConfiguration:
    """Expose an operation's captured parser while retaining the shared static owner."""
    def __get__(self, owner, owner_type=None):
        if owner is None:
            return self
        return operation_configuration(owner._configuration)

    def __set__(self, owner, config):
        owner._configuration = config


def _copy_operation_configuration(config):
    result = copy.copy(config)
    result._defaults = dict(config._defaults)
    result._sections = {name: dict(values) for name, values in config._sections.items()}
    result._proxies = {name: configparser.SectionProxy(result, name)
                       for name in [config.default_section, *result._sections]}
    _copy_operation_converters(config, result)
    snapshot = getattr(config, '_sqlite_preferences_snapshot', None)
    result._sqlite_preferences_snapshot = _clone_snapshot(snapshot) if snapshot is not None else None
    result._operation_source = _configuration_source(config)
    return result


def _copy_operation_converters(source, target):
    target._converters = configparser.ConverterMapping(target)
    for name, converter in source.converters.items():
        if converter is not None:
            target.converters[name] = converter


def _has_preference_owner(config, paths):
    return (getattr(config, '_preference_store', None) is not None or
            paths.get('storage_backend') == 'sqlite' or
            config.get('storage', 'backend', fallback='tsv') == 'sqlite')


def capture_operation_configuration(config, paths, refresh=True):
    source = _configuration_source(config)
    with STATE_LOCK:
        captured = _copy_operation_configuration(source)
    if refresh and _has_preference_owner(captured, paths):
        snapshot = _read_operation_preferences(captured, paths)
        if snapshot is not None:
            captured._sqlite_preferences_snapshot = None
            project_preferences(captured, snapshot)
    return captured


def _read_operation_preferences(config, paths):
    store = getattr(config, '_preference_store', None)
    if _store_matches(store, paths):
        return store.snapshot()
    db = KardenwortDB(config=config, resolved_paths=paths, read_only=True)
    if not db.db_path.exists():
        return None
    with db.get_connection(read_only=True) as connection:
        return _read_unimported_operation_preferences(config, connection)


def _read_unimported_operation_preferences(config, connection):
    table = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='preferences'").fetchone()
    if table is None:
        return None
    snapshot = read_snapshot(connection)
    existing = {row[0] for row in connection.execute('SELECT key FROM preferences')}
    snapshot['values'].update(configured_preferences(config, PREFERENCES.keys()-existing))
    return snapshot


@contextmanager
def preference_operation(config, paths, refresh=True):
    source = _configuration_source(config)
    active = _OPERATION_CONFIGS.get() or {}
    if config is None or id(source) in active:
        yield operation_configuration(config)
        return
    captured = capture_operation_configuration(config, paths, refresh)
    # Integer owner identities keep nested entry points on the same captured parser.
    bound = dict(active)
    bound[id(source)] = captured
    token = _OPERATION_CONFIGS.set(bound)
    try:
        yield captured
    finally:
        _OPERATION_CONFIGS.reset(token)


def operation_preferences(command):
    @wraps(command)
    def captured(owner, *args, **kwargs):
        with preference_operation(owner.config, owner.resolved_paths):
            return command(owner, *args, **kwargs)
    return captured


def request_preferences(uses_preferences):
    def decorate_request(command):
        @wraps(command)
        def captured_request(handler, *args, **kwargs):
            path = handler.path.split('?', 1)[0]
            if not uses_preferences(path):
                return command(handler, *args, **kwargs)
            with preference_operation(handler.server.config, handler.server.resolved_paths):
                return command(handler, *args, **kwargs)
        return captured_request
    return decorate_request
