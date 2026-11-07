"""Default selection excludes artifacts; explicit negative probes remain runnable."""
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.run_dict_transport_audit import parse_arguments


@pytest.mark.parametrize('selection', [[], ['--'], ['--', 'owned/test_negative.py']])
def test_audit_default_and_explicit_selection_are_preserved(selection):
    options = parse_arguments(['--output-dir', 'results/owned-discovery', *selection])
    assert options.pytest_args == (['owned/test_negative.py'] if 'owned/test_negative.py' in selection else ['tests'])


def test_discovery_keeps_default_inventory_and_explicit_external_failure(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'pytest.ini').write_bytes((root / 'pytest.ini').read_bytes())
    tests, results = tmp_path / 'tests', tmp_path / 'results'
    tests.mkdir()
    results.mkdir()
    (tests / 'test_maintained.py').write_text('def test_maintained():\n    assert True\n', encoding='utf-8')
    external = results / 'test_owned_negative.py'
    external.write_text('def test_owned_negative():\n    assert False, "explicit owned failure"\n', encoding='utf-8')
    command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--confcutdir', str(tmp_path), '--rootdir', str(tmp_path)]
    default = subprocess.run([*command, '--collect-only'], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    explicit = subprocess.run([*command, 'tests', '--collect-only'], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert default.returncode == explicit.returncode == 0, default.stdout + explicit.stdout
    default_ids = [line for line in default.stdout.splitlines() if '::test_' in line]
    explicit_ids = [line for line in explicit.stdout.splitlines() if '::test_' in line]
    assert default_ids == explicit_ids == ['tests/test_maintained.py::test_maintained']
    probe = subprocess.run([*command, str(external)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 1, probe.stdout + probe.stderr
    assert 'explicit owned failure' in probe.stdout
    assert '1 failed' in probe.stdout
