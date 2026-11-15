"""Explicit diskless partial events do not borrow configuration-only storage."""
import configparser
import sqlite3

import pytest

import kardenwort_desk as desk
from tests.test_update_projection_storage_owner import observe_sqlite_opens


@pytest.mark.parametrize('existing', [False, True])
def test_diskless_config_only_projection_does_not_borrow_database(tmp_path, request, existing):
    database = tmp_path / 'config-only.db'
    if existing:
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE untouched(value TEXT)')
        connection.close()
    before = database.read_bytes() if existing else None
    config = configparser.ConfigParser()
    config.read_dict({'storage': {'backend': 'sqlite', 'sqlite_db_path': str(database)},
                      'server': {'update_delivery': 'desktop_file'}})
    attempts = request.getfixturevalue('observe_sqlite_opens')
    result = desk.write_update_js(tmp_path / '20261115113200-partial.tsv', [], [], {},
        stage='finished', zid='20261115113200', config=config, diskless=True)
    assert attempts == []
    assert (database.read_bytes() if database.exists() else None) == before
    assert result is None
    assert sorted(path.name for path in tmp_path.iterdir()) == (['config-only.db'] if existing else [])
