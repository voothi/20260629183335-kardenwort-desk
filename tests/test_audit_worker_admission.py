"""Real child admission validates transported context before worker effects."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

import kardenwort_desk as desk
from kardenwort_progressive_context import encode_runtime
from scripts import run_dict_transport_audit as audit
from tests.test_progressive import setup_test_env


SECRET = 'audit-secret-never-record-runtime-bytes'
HARNESS = '''
import json, subprocess, sys, time
from pathlib import Path
import pytest
owner, source, mode, target = sys.argv[1:]
sys.path.append(str(Path.cwd()))
sys.path.insert(0, str(Path(source).parent))
from scripts import run_dict_transport_audit as audit
audit.OUT = Path(owner)
command = [sys.executable, source, 'progressive-worker', '--tsv', target,
    '--language', 'en', '--target-lang', 'ru', '--prompt', '', '--provider', 'google',
    '--word-empty', 'False', '--skip-intellifiller']
if mode == 'config': command[2:2] = ['--config', str(Path(owner)/'config.ini')]
elif mode == 'foreign-config': command[2:2] = ['--config', target]
elif mode == 'arbitrary': command = [sys.executable, '-c', 'raise RuntimeError("unsafe")', 'progressive-worker', '--runtime-stdin']
else: command += ['--runtime-stdin']
if mode == 'duplicate-source': command += ['--tsv', str(Path(owner).parent/'foreign/sentinel')]
if mode == 'foreign-interpreter': command[0] = str(Path(owner)/'untrusted-interpreter.exe')
blocked, owned = [], []
with pytest.MonkeyPatch.context() as patches:
    audit.install_subprocess_guard(patches, blocked, owned)
    try:
        if mode == 'pending':
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            child.stdin.write(sys.stdin.buffer.read()); child.stdin.flush()
            record = dict(parent_rejected=False, waiting=child.poll() is None)
        else:
            result = subprocess.run(command, input=sys.stdin.buffer.read(), capture_output=True, timeout=15)
            record = dict(returncode=result.returncode, stdout=result.stdout.decode(errors='replace'),
                          stderr=result.stderr.decode(errors='replace'), parent_rejected=False)
    except AssertionError:
        record = dict(parent_rejected=True)
    finally:
        started = time.monotonic()
        audit.finish_workers(owned)
        elapsed = time.monotonic()-started
        if mode == 'pending': child.stdin.close()
    record.update(children=len(owned), finished=all(p.poll() is not None for p in owned), cleanup_seconds=elapsed)
print('ADMISSION_RESULT='+json.dumps(record))
'''


@pytest.fixture
def worker_world(tmp_path):
    owner = tmp_path/'owner'; owner.mkdir()
    config, paths = setup_test_env(owner)
    config.read_dict({'storage': {'backend': 'tsv', 'sqlite_db_path': str(owner/'owned.db')},
        'settings': {'anki_mapping_file': str(paths['anki_mapping_file'])},
        'project_structure': {'generated_results_dir': str(owner)},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual', 'run_lemma_enrichment': 'manual'},
        'server': {'update_delivery': 'desktop_file', 'api_key': SECRET}})
    paths.update(base_dir=owner, storage_backend='tsv', sqlite_db_path=owner/'owned.db',
                 kardenwort_python=Path(sys.executable))
    source = owner/'20261115170500-owned.en.tsv'
    source.write_text('WordSource\tSentenceSource\tSentenceDestination\tSentenceSourceIndex\nowned\tOwned source.\tOwned translation.\t1\n')
    return owner, config, paths, source


def launch(owner, source, raw, mode='stdin', runtime=None):
    result = subprocess.run([sys.executable, '-B', '-c', HARNESS, str(owner),
        str(runtime or Path(desk.__file__)), mode, str(source)], input=raw,
        capture_output=True, timeout=30, cwd=audit.ROOT)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert SECRET.encode() not in result.stdout+result.stderr
    record = json.loads(result.stdout.decode().split('ADMISSION_RESULT=')[-1])
    assert SECRET not in json.dumps(record)
    assert record['finished']
    return record


def tree_bytes(owner):
    return {str(path.relative_to(owner)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in owner.rglob('*') if path.is_file()}


def worker_records(owner):
    evidence = owner/'audit-workers.jsonl'
    assert SECRET not in evidence.read_text()
    return [json.loads(line) for line in evidence.read_text().splitlines()]


def test_owned_stdin_worker_finishes_without_secondary_configuration(worker_world):
    owner, config, paths, source = worker_world
    original = source.read_bytes()
    record = launch(owner, source, encode_runtime(config, paths))
    assert not record['parent_rejected'] and record['returncode'] == 0, record
    assert record['children'] == 1
    assert source.with_suffix('.enrichment_done').exists()
    assert source.read_bytes() == original
    assert not paths['sqlite_db_path'].exists()
    assert sorted(path.name for path in owner.glob('*.ini')) == ['mapping.ini']
    records = worker_records(owner)
    assert [row['phase'] for row in records] == ['admitted', 'finished']
    assert records[0]['source'] == str(Path(desk.__file__).resolve())
    assert records[0]['decoder'] == str(Path(desk.__file__).parent/'kardenwort_progressive_context.py')


@pytest.mark.parametrize('fault', ['malformed', 'foreign-db', 'protected-db', 'missing-db',
    'foreign-results', 'missing-results', 'foreign-source', 'escaped-results', 'escaped-updates', 'foreign-db-log', 'duplicate-source'])
def test_stdin_rejection_precedes_foreign_or_worker_effects(worker_world, fault):
    owner, config, paths, source = worker_world
    foreign = owner.parent/'foreign'; foreign.mkdir()
    sentinel = foreign/'sentinel'; sentinel.write_bytes(b'untouched')
    if fault.endswith('db'):
        paths['storage_backend'] = 'sqlite'
        config.set('storage', 'backend', 'sqlite')
        paths['sqlite_db_path'] = foreign/'foreign.db'
    if fault == 'protected-db': paths['sqlite_db_path'] = audit.ROOT/'data/kardenwort.db'
    if fault == 'missing-db':
        paths.pop('sqlite_db_path'); config.remove_option('storage', 'sqlite_db_path')
    if fault == 'foreign-results': paths['generated_results_dir'] = foreign
    if fault == 'missing-results':
        for key in ['results_dir', 'base_dir', 'kardenwort_workspace']: paths.pop(key, None)
    if fault == 'foreign-source': source = sentinel
    if fault == 'escaped-results': paths['generated_results_dir'] = owner/'..'/'foreign'
    if fault == 'escaped-updates':
        junction = source.with_suffix('.updates')
        assert junction.parent.resolve().is_relative_to(owner.resolve())
        if sys.platform == 'win32':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(foreign)], capture_output=True)
            assert result.returncode == 0
        else:
            junction.symlink_to(foreign, target_is_directory=True)
    if fault == 'foreign-db-log':
        paths.update(storage_backend='sqlite', results_dir=foreign, generated_results_dir=owner)
    before, foreign_before = tree_bytes(owner), tree_bytes(foreign)
    raw = b'{malformed' if fault == 'malformed' else encode_runtime(config, paths)
    record = launch(owner, source, raw, mode='duplicate-source' if fault == 'duplicate-source' else 'stdin')
    assert not record['parent_rejected'], record  # A real child consumes and rejects the actual bytes.
    assert record['returncode'] != 0 and record['children'] == 1, record
    assert tree_bytes(foreign) == foreign_before
    after = tree_bytes(owner); after.pop('audit-workers.jsonl', None)
    assert after == before
    assert [row['phase'] for row in worker_records(owner)] == ['rejected']


def test_resolved_paths_take_precedence_over_unused_config_destinations(worker_world):
    owner, config, paths, source = worker_world
    # TSV runtime paths override lower-priority configuration paths.
    config.set('storage', 'sqlite_db_path', str(owner.parent/'never-open.db'))
    config.set('project_structure', 'generated_results_dir', str(owner.parent/'never-created'))
    record = launch(owner, source, encode_runtime(config, paths))
    assert not record['parent_rejected'] and record['returncode'] == 0, record
    assert source.with_suffix('.enrichment_done').exists()
    assert not (owner.parent/'never-open.db').exists()
    assert not (owner.parent/'never-created').exists()


@pytest.mark.parametrize('mode', ['arbitrary', 'foreign-interpreter'])
def test_runtime_flag_does_not_authorize_an_arbitrary_executable(worker_world, mode):
    owner, config, paths, source = worker_world
    record = launch(owner, source, encode_runtime(config, paths), mode=mode)
    assert record['parent_rejected'] and record['children'] == 0


def test_owned_cleanup_terminates_a_worker_still_waiting_for_stdin(worker_world):
    owner, config, paths, source = worker_world
    before = tree_bytes(owner)
    record = launch(owner, source, encode_runtime(config, paths), mode='pending')
    assert not record['parent_rejected'] and record['waiting'] and record['children'] == 1
    assert record['cleanup_seconds'] < 11
    assert tree_bytes(owner) == before


@pytest.mark.parametrize('owned', [False, True])
def test_existing_explicit_configuration_admission_is_retained(worker_world, owned):
    owner, config, paths, source = worker_world
    if owned:
        with (owner/'config.ini').open('w') as stream: config.write(stream)
        record = launch(owner, source, b'', mode='config')
        assert not record['parent_rejected'] and record['returncode'] == 0, record
        assert record['children'] == 1 and source.with_suffix('.enrichment_done').exists()
    else:
        protected = audit.ROOT/'config.ini'
        record = launch(owner, protected, b'', mode='foreign-config')
        assert record['parent_rejected'] and record['children'] == 0
        assert not source.with_suffix('.enrichment_done').exists()


def test_selected_revision_worker_uses_its_own_decoder(worker_world):
    from scripts.release_audit_sources import materialize
    owner, config, paths, source = worker_world
    selected = materialize(audit.ROOT, owner.parent/'revision', 'worker', 'HEAD')
    script = Path(selected['source'])/'kardenwort_desk.py'
    record = launch(owner, source, encode_runtime(config, paths), runtime=script)
    assert not record['parent_rejected'] and record['returncode'] == 0, record
    admitted = worker_records(owner)[0]
    assert admitted['source'] == str(script.resolve())
    assert admitted['decoder'] == str(script.parent/'kardenwort_progressive_context.py')
    assert admitted['source_sha256'] == selected['source_hashes']['kardenwort_desk.py']


def test_sqlite_worker_completes_against_its_effective_owned_database(worker_world):
    owner, config, paths, source = worker_world
    config.set('storage', 'backend', 'sqlite')
    paths.update(storage_backend='sqlite', generated_results_dir=owner/'generated', results_dir=owner/'logs')
    adapter = desk.SqliteStorageAdapter(config=config, resolved_paths=paths)
    headers = ['WordSource', 'SentenceSource', 'SentenceDestination', 'SentenceSourceIndex']
    rows = [['owned', 'Owned source.', 'Owned translation.', '1']]
    zid = '20261115170500'
    adapter.save_session(session_zid=zid, source_language='en', target_language='ru',
        source_raw_text='Owned source.', text_mode='single', headers=headers, data_rows=rows)
    original = adapter.restore_session(zid)['data_rows']
    record = launch(owner, source, encode_runtime(config, paths))
    assert not record['parent_rejected'] and record['returncode'] == 0, record
    assert adapter.db.get_worker_status(zid)['worker_status'] == 'finished'
    assert not source.with_suffix('.enrichment_done').exists()
    assert adapter.restore_session(zid)['data_rows'] == original
    assert not (owner/'config.ini').exists()


def test_worker_admission_functions_keep_branch_ceiling():
    from tests.test_intent_architecture import complexity, functions
    for name in ['audit_progressive_worker.py', 'run_dict_transport_audit.py']:
        tree = ast.parse((audit.ROOT/'scripts'/name).read_text())
        assert {node.name: complexity(node) for node in functions(tree) if complexity(node) > 7} == {}
