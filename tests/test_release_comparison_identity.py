"""Black-box CLI contracts for accurate, owned, non-overwriting release evidence."""
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASELINE = '56f8696'
TARGET = 'bf498c3'


@pytest.fixture
def comparison_cli(tmp_path):
    repository = tmp_path / 'repository'
    (repository / 'scripts').mkdir(parents=True)
    for path in ROOT.glob('*.py'):
        shutil.copy2(path, repository / path.name)
    for name in ('config.ini', 'anki-mapping.ini'):
        shutil.copy2(ROOT / name, repository / name)
    for directory in ('web', 'schemas'):
        shutil.copytree(ROOT / directory, repository / directory)
    config = configparser.ConfigParser(interpolation=None)
    config.read(repository / 'config.ini', encoding='utf-8-sig')
    for key, value in config['environment'].items():
        path = Path(value)
        config.set('environment', key, str(path if path.is_absolute() else (ROOT / path).resolve()))
    config.set('storage', 'sqlite_db_path', str(repository / 'data/fixture.db'))
    config.set('project_structure', 'generated_results_dir', str(repository / 'results'))
    config.set('wordfill', 'enabled', 'false')
    for key in ('run_text_translation', 'run_lemma_base_translation', 'run_lemma_enrichment'):
        config.set('triggers', key, 'manual')
    with (repository / 'config.ini').open('w', encoding='utf-8') as destination:
        config.write(destination)
    script = repository / 'scripts/release_audit_baseline.py'
    shutil.copy2(ROOT / 'scripts/release_audit_baseline.py', script)
    output = repository / 'results' / 'comparison'
    owner = tmp_path / 'isolation'
    owner.mkdir()
    (owner / 'scratch').mkdir()
    bootstrap = '''import runpy,sys
from pathlib import Path
from scripts import run_dict_transport_audit as audit
audit.OUT=Path(sys.argv[1])
isolation=audit.Isolation(audit.protected_snapshot(),'comparison-cli')
script=sys.argv[2]
sys.argv=[script,*sys.argv[3:]]
with audit.owned_runtime(audit.OUT/'scratch',isolation):
    runpy.run_path(script,run_name='__main__')
'''

    def run(*args):
        env = dict(os.environ, PYTHONPATH=str(ROOT), SSLKEYLOGFILE='',
                   GIT_DIR=str(ROOT / '.git'), GIT_WORK_TREE=str(repository),
                   GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory',
                   GIT_CONFIG_VALUE_0=repository.as_posix())
        return subprocess.run([sys.executable, '-c', bootstrap, str(owner), str(script),
            '--baseline-ref', BASELINE, '--output-dir', str(output), *args], cwd=repository,
            env=env, capture_output=True, text=True, timeout=40)

    return repository, output, run


def execution(output):
    record = output / 'execution.json'
    assert record.is_file(), 'Comparison CLI did not retain its execution identity and result'
    return json.loads(record.read_text(encoding='utf-8'))


def test_explicit_target_identifies_the_actual_executed_source(comparison_cli):
    _, output, run = comparison_cli
    result = run('--target-ref', TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    record = execution(output)
    assert record['baseline']['commit'].startswith(BASELINE)
    assert record['target']['commit'].startswith(TARGET)
    assert record['target']['mode'] == 'revision'
    assert record['target']['source_hashes']
    assert record['exit_code'] == 0


def test_working_tree_records_dirty_content_instead_of_claiming_exact_commit(comparison_cli):
    repository, output, run = comparison_cli
    with (repository / 'kardenwort_reword.py').open('a', encoding='utf-8') as source:
        source.write('\n# Owned working-tree identity probe.\n')
    result = run('--working-tree')
    assert result.returncode == 0, result.stdout + result.stderr
    target = execution(output)['target']
    assert target['mode'] == 'working-tree'
    assert target['dirty'] is True
    assert target['source_hashes']['kardenwort_reword.py']


def test_existing_output_is_rejected_without_changing_prior_evidence(comparison_cli):
    _, output, run = comparison_cli
    output.mkdir(parents=True)
    marker = output / 'styles.json'
    original = b'{"prior":"immutable evidence"}\n'
    marker.write_bytes(original)
    result = run('--target-ref', TARGET)
    assert result.returncode != 0, 'Existing comparison output was accepted'
    diagnostic = result.stdout + result.stderr
    assert 'exist' in diagnostic.lower() and str(output) in diagnostic
    assert marker.read_bytes() == original
    assert list(output.iterdir()) == [marker]


def test_unknown_target_fails_before_rendering_and_names_the_reference(comparison_cli):
    _, output, run = comparison_cli
    result = run('--target-ref', 'missing-release-reference')
    assert result.returncode != 0
    assert 'missing-release-reference' in result.stdout + result.stderr
    assert not list(output.glob('*.png'))


def test_missing_working_tree_asset_is_not_replaced_with_current_asset(comparison_cli):
    repository, output, run = comparison_cli
    (repository / 'web/js/app.js').unlink()
    result = run('--working-tree')
    assert result.returncode != 0, 'Missing runtime asset was silently supplied from another source tree'
    assert 'app.js' in result.stdout + result.stderr
    assert not list(output.glob('*.png'))


def test_foreign_runtime_import_cannot_claim_exact_source_identity(comparison_cli):
    repository, output, run = comparison_cli
    (repository / 'kardenwort_reword.py').unlink()
    result = run('--working-tree')
    assert result.returncode != 0, 'Comparison accepted a runtime helper imported from the outer checkout'
    assert 'kardenwort_reword' in result.stdout + result.stderr
    assert not list(output.glob('*.png'))
