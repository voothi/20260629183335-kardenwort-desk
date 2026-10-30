"""Owned audit paths, verified backups, capacity rejection and collection guards."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from scripts import run_dict_transport_audit as audit


@pytest.fixture
def backup_world(tmp_path):
    root = tmp_path / 'repository'
    (root / 'data').mkdir(parents=True)
    database = root / 'data' / 'fixture.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE words (translation TEXT)')
        connection.execute('INSERT INTO words VALUES (?)', ('original',))
    output = root / 'results' / 'first-run'
    output.mkdir(parents=True)
    snapshot = {'data/fixture.db': audit.digest_file(database)}
    return root, output, snapshot


def test_matching_owned_backup_is_reused_without_another_copy(backup_world):
    root, output, snapshot = backup_world
    first = audit.prepare_backup(output, snapshot, 1024, root=root)
    second = root / 'results' / 'second-run'
    second.mkdir()
    result = audit.prepare_backup(second, snapshot, 1024, first['database_backup'],
                                 output / 'backup-manifest.json', root)
    assert result['backup_reused'] is True
    assert result['database_backup'] == first['database_backup']
    assert not list(second.iterdir())
    with sqlite3.connect(Path(result['database_backup']) / 'fixture.db') as connection:
        assert connection.execute('SELECT translation FROM words').fetchall() == [('original',)]


@pytest.mark.parametrize('corruption', ['bytes', 'hashes', 'ownership'])
def test_backup_mismatch_stops_without_copying_or_changing_source(backup_world, corruption):
    root, output, snapshot = backup_world
    first = audit.prepare_backup(output, snapshot, 1024, root=root)
    manifest = output / 'backup-manifest.json'
    metadata = json.loads(manifest.read_text())
    if corruption == 'bytes':
        (Path(first['database_backup']) / 'fixture.db').write_bytes(b'corrupt retained backup')
    elif corruption == 'hashes':
        metadata['protected_before']['data/fixture.db'] = 'incorrect'
    else:
        metadata['database_backup'] = str(root / 'results' / 'someone-else')
    manifest.write_text(json.dumps(metadata))
    second = root / 'results' / 'second-run'
    second.mkdir()
    with pytest.raises(ValueError, match='[Bb]ackup|[Rr]etained'):
        audit.prepare_backup(second, snapshot, 1024, first['database_backup'], manifest, root)
    assert not list(second.iterdir())
    assert audit.digest_file(root / 'data' / 'fixture.db') == snapshot['data/fixture.db']


def test_insufficient_capacity_stops_before_backup_creation(backup_world):
    root, output, snapshot = backup_world
    with pytest.raises(OSError, match='Insufficient audit capacity'):
        audit.prepare_backup(output, snapshot, 10**30, root=root)
    assert not list(output.iterdir())
    assert audit.digest_file(root / 'data' / 'fixture.db') == snapshot['data/fixture.db']


def test_reused_backup_still_requires_test_capacity(backup_world):
    root, output, snapshot = backup_world
    first = audit.prepare_backup(output, snapshot, 1024, root=root)
    second = root / 'results' / 'second-run'
    second.mkdir()
    with pytest.raises(OSError, match='Insufficient audit capacity'):
        audit.prepare_backup(second, snapshot, 10**30, first['database_backup'],
                             output / 'backup-manifest.json', root)
    assert not list(second.iterdir())


def test_reuse_requires_ownership_manifest(backup_world):
    root, output, snapshot = backup_world
    with pytest.raises(ValueError, match='both a directory and its ownership manifest'):
        audit.prepare_backup(output, snapshot, 1024, reuse=output / 'unknown', root=root)
    assert not list(output.iterdir())


def test_audit_rejects_paths_outside_results(tmp_path):
    with pytest.raises(ValueError, match='named directory under repository results'):
        audit.require_owned(tmp_path / 'unowned', tmp_path)
    with pytest.raises(ValueError):
        audit.require_owned(tmp_path / 'results', tmp_path)


def test_project_temporary_output_requires_a_named_owned_directory():
    owner = Path(os.environ['TEMP']) / 'kardenwort-desk-audits' / audit.ROOT.name
    assert audit.require_owned(owner / 'explicit-run') == (owner / 'explicit-run').resolve()
    with pytest.raises(ValueError):
        audit.require_owned(owner)
    with pytest.raises(ValueError):
        audit.require_owned(owner.parent / 'another-repository' / 'run')


def test_guards_are_active_during_collection(tmp_path):
    probe = tmp_path / 'test_collection_guards.py'
    probe.write_text('''import socket
import subprocess
import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from pathlib import Path
cfg, paths, _, _ = desk.load_config()
assert Path(paths['sqlite_db_path']).is_relative_to(Path(__file__).parent / 'nested-run')
try:
    with KardenwortDB(Path(desk.__file__).parent / 'data' / 'kardenwort.db').get_connection():
        raise AssertionError('protected connection unexpectedly opened')
except AssertionError as error:
    assert 'outside its owned fixture directory' in str(error)
else:
    raise AssertionError('collection database guard missing')
try:
    with socket.socket() as connection:
        connection.connect(('127.0.0.1', 18335))
except AssertionError as error:
    assert 'protected local service' in str(error)
else:
    raise AssertionError('collection socket guard missing')
try:
    subprocess.Popen(['python', 'progressive-worker'])
except AssertionError as error:
    assert 'ambient workspace configuration' in str(error)
else:
    raise AssertionError('collection worker guard missing')
def test_collection_guards():
    assert True
''', encoding='utf-8')
    output = tmp_path / 'nested-run'
    output.mkdir()
    (output / 'scratch').mkdir()
    bootstrap = """from pathlib import Path
import sys
import pytest
from scripts import run_dict_transport_audit as audit
audit.OUT = Path(sys.argv[1])
before = audit.protected_snapshot()
isolation = audit.Isolation(before, 'collection-probe')
with audit.owned_runtime(audit.OUT / 'scratch', isolation):
    result = pytest.main([sys.argv[2], '-q', '-p', 'no:cacheprovider',
                          '--basetemp', str(audit.OUT / 'fixtures'),
                          '--confcutdir', str(Path(sys.argv[2]).parent),
                          '--rootdir', str(Path(sys.argv[2]).parent),
                          '--junitxml', str(audit.OUT / 'junit.xml'),
                          '-W', 'error::pytest.PytestUnhandledThreadExceptionWarning'], plugins=[isolation])
assert before == audit.protected_snapshot()
sys.exit(int(result))
"""
    result = subprocess.run([sys.executable, '-c', bootstrap, str(output), str(probe)],
                            capture_output=True, text=True, timeout=45, cwd=audit.ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / 'junit.xml').is_file()
    assert '1 passed' in result.stdout


def test_cli_capacity_failure_precedes_collection_and_preserves_prior_output():
    with tempfile.TemporaryDirectory(prefix='audit-capacity-contract-', dir=audit.ROOT / 'results') as directory:
        owner = Path(directory)
        marker = owner / 'collected'
        probe = owner / 'test_never_collected.py'
        probe.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n', encoding='utf-8')
        output = owner / 'run'
        command = [sys.executable, str(audit.ROOT / 'scripts/run_dict_transport_audit.py'),
                   '--output-dir', str(output), '--reserve-mb', '1e30', '--', str(probe)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert result.returncode == 2, result.stdout + result.stderr
        assert not marker.exists()
        record = output / 'execution.json'
        original = record.read_bytes()
        execution = json.loads(original)
        assert execution['stage'] == 'preflight-failed'
        assert execution['exit_code'] == 2
        assert 'Insufficient audit capacity' in execution['error']
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert repeated.returncode != 0
        assert record.read_bytes() == original
        assert not marker.exists()


def test_audit_boundary_complexity_ceiling():
    import ast
    from tests.test_intent_architecture import complexity, functions
    tree = ast.parse(Path(audit.__file__).read_text(encoding='utf-8'))
    violations = {node.name: complexity(node) for node in functions(tree) if complexity(node) > 7}
    assert violations == {}
