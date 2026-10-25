"""Run unchanged legacy contracts in a disposable, isolated filesystem checkout."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'results/20261025104755-release-boundaries'
CONTRACTS = ['tests/test_release_boundary_contracts.py',
             'tests/test_same_lemma_toggle.py::test_frontend_is_legacy_overview_context']
CONTROLS = ['tests/test_release_audit_boundaries.py::test_native_production_bridge_to_real_sqlite',
            'tests/test_intent_contract_integrity.py::test_native_polling_retains_pending_events_and_disposes',
            'tests/test_intent_contract_integrity.py::test_browser_windows_sync_without_echoes',
            'tests/test_intent_contract_integrity.py::test_undo_during_save_retains_reversal_and_history']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_checkout(target):
    names = subprocess.run(['git', 'ls-files'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    names.append('tests/test_release_boundary_contracts.py')
    for name in names:
        source = ROOT / name
        if not source.is_file():
            continue
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    config.read(ROOT / 'config.ini', encoding='utf-8-sig')
    for key, value in config['environment'].items():
        path = Path(value)
        config.set('environment', key, str(path if path.is_absolute() else (ROOT / path).resolve()))
    config.set('storage', 'sqlite_db_path', str(target / 'data/kardenwort.db'))
    config.set('project_structure', 'generated_results_dir', str(target / 'results'))
    with (target / 'config.ini').open('w', encoding='utf-8') as stream:
        config.write(stream)
    (target / 'results').mkdir(exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=('contracts', 'controls'))
    parser.add_argument('--filter', help='Optional pytest expression for fixture diagnosis')
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    protected = ROOT / 'data/kardenwort.db'
    before = digest(protected)
    scratch = ROOT / 'scratch'
    assert scratch.resolve().parent == ROOT.resolve()
    assert not scratch.is_junction() and not scratch.is_symlink()
    with tempfile.TemporaryDirectory(prefix='20261025104755-contracts-', dir=ROOT / 'scratch') as directory:
        checkout = Path(directory)
        assert checkout.resolve().parent == scratch.resolve()
        copy_checkout(checkout)
        selected = CONTRACTS if args.suite == 'contracts' else CONTROLS
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *selected,
                   '--tb=short', '--basetemp=' + str(checkout / 'pytest-temp'),
                   '--junitxml=' + str(EVIDENCE / (args.suite + '.xml'))]
        if args.filter:
            command.extend(['-k', args.filter])
        env = dict(os.environ, SSLKEYLOGFILE='', KARDENWORT_NATIVE_REPO='U:/voothi/20240411110510-autohotkey',
                   PYTHONPATH=str(checkout))
        with (EVIDENCE / (args.suite + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT)
        metadata = dict(command=command, exit_code=result.returncode, isolated_checkout=str(checkout),
                        original_legacy_test_sha256=digest(ROOT / 'tests/test_same_lemma_toggle.py'),
                        copied_legacy_test_sha256=digest(checkout / 'tests/test_same_lemma_toggle.py'),
                        protected_db_before=before, protected_db_after=digest(protected))
    metadata['temporary_checkout_removed'] = not checkout.exists()
    (EVIDENCE / (args.suite + '-execution.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print((EVIDENCE / (args.suite + '.log')).read_text(encoding='utf-8').splitlines()[-1])
    print(json.dumps(metadata))
    assert metadata['protected_db_before'] == metadata['protected_db_after'], 'Original database changed during isolated run'
    assert metadata['original_legacy_test_sha256'] == metadata['copied_legacy_test_sha256']
    assert metadata['temporary_checkout_removed']
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
