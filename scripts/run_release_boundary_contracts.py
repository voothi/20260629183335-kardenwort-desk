"""Run unchanged legacy contracts in a disposable, isolated filesystem checkout."""
import argparse
import configparser
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'results/20261025104755-release-boundaries'
CONTRACTS = ['tests/test_release_boundary_contracts.py',
             'tests/test_same_lemma_toggle.py::test_frontend_is_legacy_overview_context']
CONTROLS = ['tests/test_release_audit_boundaries.py::test_native_production_bridge_to_real_sqlite',
            'tests/test_intent_contract_integrity.py::test_native_polling_retains_pending_events_and_disposes',
            'tests/test_intent_contract_integrity.py::test_browser_windows_sync_without_echoes',
            'tests/test_intent_contract_integrity.py::test_undo_during_save_retains_reversal_and_history']
MEMBERSHIP = ['tests/test_release_membership_reads.py',
              'tests/test_release_boundary_contracts.py::test_authenticated_membership_roundtrip_is_read_only',
              'tests/test_release_boundary_contracts.py::test_invalid_or_unavailable_membership_is_structured_failure',
              'tests/test_release_boundary_contracts.py::test_membership_authentication_precedes_backend_read',
              'tests/test_same_lemma_toggle.py::test_frontend_is_legacy_overview_context']
STORAGE = ['tests/test_storage_router.py', 'tests/test_kardenwort_db.py', 'tests/test_sqlite_restore.py',
           'tests/test_overview_tab_selection_persistence.py', 'tests/test_dynamic_export.py',
           'tests/test_clean_translation_view.py',
           'tests/test_workspace_tab_container.py::test_playwright_card_scoped_send_to_anki_export',
           'tests/test_same_lemma_toggle.py::test_targeted_token_export_creates_single_card',
           'tests/test_same_lemma_toggle.py::test_merged_row_export_unrolls_all_occurrences',
           'tests/test_release_audit_boundaries.py::test_http_required_asset_failure_is_json_without_html']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_checkout(target):
    names = subprocess.run(['git', 'ls-files'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    names.append('tests/test_release_boundary_contracts.py')
    names.append('tests/test_release_membership_reads.py')
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
    # Standalone HTML must not bootstrap against the user's live controller.
    # Controller fixtures explicitly supply their own ephemeral server ports.
    config.set('server', 'enabled', 'false')
    config.set('server', 'port', '0')
    config.set('server', 'controller_port', '0')
    with (target / 'config.ini').open('w', encoding='utf-8') as stream:
        config.write(stream)
    (target / 'results').mkdir(exist_ok=True)


def guard_external_databases(checkout):
    """Catch configuration escapes before SQLite can touch another checkout."""
    guard = '''import json, sys, traceback, urllib.parse
from pathlib import Path
ROOT = Path(__file__).resolve().parent
def guard(event, args):
    if event != 'sqlite3.connect':
        return
    path = str(args[0])
    if path == ':memory:' or 'mode=memory' in path:
        return
    if path.startswith('file:'):
        path = urllib.parse.unquote(urllib.parse.urlsplit(path).path).lstrip('/')
    resolved = Path(path).resolve()
    if resolved.is_relative_to(ROOT):
        return
    record = dict(path=str(resolved), stack=traceback.format_stack(limit=10))
    with (ROOT / 'database-guard.jsonl').open('a', encoding='utf8') as stream:
        stream.write(json.dumps(record) + '\\n')
    raise RuntimeError('Isolated test attempted to open an external database')
sys.addaudithook(guard)
'''
    (checkout / 'sitecustomize.py').write_text(guard, encoding='utf-8')


@contextmanager
def temporary_checkout(scratch):
    temporary = tempfile.TemporaryDirectory(prefix='20261025104755-contracts-', dir=scratch)
    checkout = Path(temporary.name)
    try:
        yield checkout
    finally:
        try:
            temporary.cleanup()
        except PermissionError:
            # Windows can release a browser/bridge working-directory handle
            # after pytest exits. Retry only removal of the now-empty root.
            deadline = time.monotonic() + 10
            while checkout.exists():
                try:
                    checkout.rmdir()
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=('contracts', 'controls', 'membership', 'storage'))
    parser.add_argument('--filter', help='Optional pytest expression for fixture diagnosis')
    parser.add_argument('--evidence-dir', type=Path, default=EVIDENCE)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    assert evidence.parent == (ROOT / 'results').resolve(), 'Evidence must stay in the workspace results directory'
    assert not evidence.is_junction() and not evidence.is_symlink()
    evidence.mkdir(parents=True, exist_ok=True)
    protected = ROOT / 'data/kardenwort.db'
    before = digest(protected)
    scratch = ROOT / 'scratch'
    assert scratch.resolve().parent == ROOT.resolve()
    assert not scratch.is_junction() and not scratch.is_symlink()
    with temporary_checkout(scratch) as checkout:
        assert checkout.resolve().parent == scratch.resolve()
        copy_checkout(checkout)
        guard_external_databases(checkout)
        selected = {'contracts': CONTRACTS, 'controls': CONTROLS, 'membership': MEMBERSHIP, 'storage': STORAGE}[args.suite]
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *selected,
                   '--tb=short', '--basetemp=' + str(checkout / 'pytest-temp'),
                   '--junitxml=' + str(evidence / (args.suite + '.xml'))]
        if args.filter:
            command.extend(['-k', args.filter])
        env = dict(os.environ, SSLKEYLOGFILE='', KARDENWORT_NATIVE_REPO='U:/voothi/20240411110510-autohotkey',
                   PYTHONPATH=str(checkout))
        with (evidence / (args.suite + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT)
        metadata = dict(command=command, exit_code=result.returncode, isolated_checkout=str(checkout),
                        original_legacy_test_sha256=digest(ROOT / 'tests/test_same_lemma_toggle.py'),
                        copied_legacy_test_sha256=digest(checkout / 'tests/test_same_lemma_toggle.py'),
                        protected_db_before=before, protected_db_after=digest(protected))
        guard_log = checkout / 'database-guard.jsonl'
        metadata['external_database_attempts'] = [json.loads(line) for line in guard_log.read_text(encoding='utf8').splitlines()] if guard_log.exists() else []
    metadata['temporary_checkout_removed'] = not checkout.exists()
    (evidence / (args.suite + '-execution.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print((evidence / (args.suite + '.log')).read_text(encoding='utf-8').splitlines()[-1])
    print(json.dumps(dict(metadata, external_database_attempts=len(metadata['external_database_attempts']))))
    assert metadata['protected_db_before'] == metadata['protected_db_after'], 'Original database changed during isolated run'
    assert metadata['original_legacy_test_sha256'] == metadata['copied_legacy_test_sha256']
    assert metadata['temporary_checkout_removed']
    assert not metadata['external_database_attempts'], 'Configuration escaped the isolated database boundary'
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
