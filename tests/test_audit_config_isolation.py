"""Real configuration actions stay inside the audit's owned configuration."""
import configparser
import hashlib
from pathlib import Path

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from scripts import run_dict_transport_audit as audit
from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import post


@pytest.fixture(autouse=True)
def owned_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'OUT', tmp_path)
    monkeypatch.setattr(desk, 'load_config', lambda config_path=None: audit.load_owned_config(config_path, tmp_path / 'config-owner'))
    monkeypatch.setattr(controller, 'load_config', desk.load_config)
    monkeypatch.setattr(controller, 'persist_config_value', audit.guarded_config_persistence)


def test_http_preference_persists_only_to_owned_config(running_controller):
    url, server, _ = running_controller
    protected = Path(__file__).resolve().parents[1] / 'config.ini'
    before = hashlib.sha256(protected.read_bytes()).hexdigest()
    owned = Path(server.resolved_paths['base_dir']) / 'config.ini'
    assert owned.resolve() != protected.resolve()
    config = configparser.ConfigParser(interpolation=None)
    config.read(owned, encoding='utf-8')
    other = config.get('rendering', 'highlight_same_lemma')
    status, payload = post(url, '/api/v1/config', dict(filter_selected_only=True))
    assert status == 200, payload
    config.read(owned, encoding='utf-8')
    assert config.getboolean('rendering', 'filter_selected_only') is True
    assert config.get('rendering', 'highlight_same_lemma') == other
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == before


def test_audit_rejects_protected_config_write_before_effect():
    protected = Path(__file__).resolve().parents[1] / 'config.ini'
    before = protected.read_bytes()
    with pytest.raises(AssertionError, match='configuration persistence outside'):
        controller.persist_config_value('rendering', 'filter_selected_only', 'true', base_dir=protected.parent)
    assert protected.read_bytes() == before
