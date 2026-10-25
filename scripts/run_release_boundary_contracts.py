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
CONVERGENCE = ['tests/test_release_save_convergence.py', 'tests/test_release_audit_regressions.py',
               'tests/test_intent_contract_integrity.py',
               'tests/test_release_audit_boundaries.py::test_browser_selection_autosave_roundtrip_to_real_sqlite',
               'tests/test_release_audit_boundaries.py::test_failed_save_prevents_f5_and_keeps_dirty_input',
               'tests/test_release_audit_boundaries.py::test_native_snapshot_ack_keeps_later_selection',
               'tests/test_release_audit_boundaries.py::test_edit_during_selection_save_survives_ack_and_offline_retry',
               'tests/test_release_audit_boundaries.py::test_select_switch_update_delayed_ack_reload_with_sqlite']
WATCHDOG = ['tests/test_container_watchdog_lifecycle.py', 'tests/test_watchdog_resilience.py',
            'tests/test_web_action_toolbar.py',
            'tests/test_release_save_convergence.py::test_watchdog_cleanup_keeps_persistent_membership_stream',
            'tests/test_release_save_convergence.py::test_disconnected_stream_callbacks_cannot_change_current_session']
OWNERSHIP = ['tests/test_intent_architecture.py', 'tests/test_static_web_shell.py',
             'tests/test_clean_translation_view.py', 'tests/test_table_cell_tooltips.py',
             'tests/test_workspace_tab_container.py', 'tests/test_release_component_ownership.py',
             'tests/test_release_audit_boundaries.py::test_canonical_grammar_browser_server_parity',
             'tests/test_release_audit_boundaries.py::test_pairwise_shell_preferences',
             'tests/test_release_audit_boundaries.py::test_long_translation_update_preserves_active_layout']
UPDATE_RECOVERY = ['tests/test_release_update_recovery.py',
                   'tests/test_release_save_convergence.py::test_command_waits_for_save_and_reconciliation']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_checkout(target):
    names = subprocess.run(['git', 'ls-files'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    names.append('tests/test_release_boundary_contracts.py')
    names.append('tests/test_release_membership_reads.py')
    names.append('tests/test_release_save_convergence.py')
    names.append('tests/test_release_component_ownership.py')
    names.append('tests/test_release_update_recovery.py')
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
def forbid_compiler_tooling(event, args):
    if event not in ('open', 'os.listdir', 'os.scandir', 'subprocess.Popen'):
        return
    value = str(args[:2]).lower().replace(chr(92), '/')
    retired = ('20261024225159-typescript-cache', '20261024225159-type-baseline',
               '20261024225159-type-analysis.py', '20261025010043-close-types.cjs',
               'node_modules/typescript', '/tsc.js', '/tsserver.js')
    executable = Path(str(args[0] or args[1]).split()[0].strip(chr(34))).stem.lower() if event == 'subprocess.Popen' else ''
    if not any(name in value for name in retired) and executable not in ('tsc', 'tsserver', 'npx', 'npm'):
        return
    with (ROOT / 'tooling-guard.jsonl').open('a', encoding='utf8') as stream:
        stream.write(json.dumps(dict(event=event, target=value)) + '\\n')
    raise RuntimeError('Isolated verification attempted to use retired compiler tooling')

def guard(event, args):
    forbid_compiler_tooling(event, args)
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


def configure_fixture_browser(checkout):
    """Windows can allocate ephemeral fixture ports blocked by Chromium."""
    target = checkout / 'conftest.py'
    assert not target.exists(), 'Do not overwrite a repository root conftest'
    target.write_text('''import pytest

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return dict(browser_type_launch_args, args=[*browser_type_launch_args.get("args", []),
        "--explicitly-allowed-ports=5060,5061,6000,6566,6665,6666,6667,6668,6669,6697,10080"])
''', encoding='utf-8')


@contextmanager
def temporary_checkout(scratch):
    temporary = tempfile.TemporaryDirectory(prefix='20261025104755-contracts-', dir=scratch)
    checkout = Path(temporary.name)
    try:
        yield checkout
    finally:
        # Windows may release a browser handle just after pytest exits.
        # This target was created here and must still be inside scratch.
        assert checkout.resolve().parent == scratch.resolve()
        assert not checkout.is_junction() and not checkout.is_symlink()
        deadline = time.monotonic() + 10
        while checkout.exists():
            try:
                temporary.cleanup()
            except OSError as error:
                if error.winerror not in (5, 32, 145) or time.monotonic() >= deadline:
                    raise
                time.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=('contracts', 'controls', 'membership', 'storage', 'convergence', 'watchdog', 'ownership', 'update', 'all'))
    parser.add_argument('--filter', help='Optional pytest expression for fixture diagnosis')
    parser.add_argument('--evidence-dir', type=Path, default=EVIDENCE)
    parser.add_argument('--run-label', default='', help='Unique suffix for retained run evidence')
    args = parser.parse_args()
    if not args.run_label.replace('-', '').isalnum() and args.run_label:
        parser.error('Run label must contain only letters, numbers, or hyphens')
    evidence_name = args.suite + ('-' + args.run_label if args.run_label else '')
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
        configure_fixture_browser(checkout)
        selected = {'contracts': CONTRACTS, 'controls': CONTROLS, 'membership': MEMBERSHIP, 'storage': STORAGE, 'convergence': CONVERGENCE, 'watchdog': WATCHDOG, 'ownership': OWNERSHIP, 'update': UPDATE_RECOVERY, 'all': ['tests']}[args.suite]
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *selected,
                   '--tb=short', '--basetemp=' + str(checkout / 'pytest-temp'),
                   '--junitxml=' + str(evidence / (evidence_name + '.xml'))]
        if args.filter:
            command.extend(['-k', args.filter])
        env = dict(os.environ, SSLKEYLOGFILE='', KARDENWORT_NATIVE_REPO='U:/voothi/20240411110510-autohotkey',
                   PYTHONPATH=str(checkout))
        with (evidence / (evidence_name + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT)
        metadata = dict(command=command, exit_code=result.returncode, isolated_checkout=str(checkout),
                        original_legacy_test_sha256=digest(ROOT / 'tests/test_same_lemma_toggle.py'),
                        copied_legacy_test_sha256=digest(checkout / 'tests/test_same_lemma_toggle.py'),
                        protected_db_before=before, protected_db_after=digest(protected))
        metadata['source_sha256'] = {name: digest(checkout / name) for name in
            ('web/js/app.js', 'web/js/types.js', 'web/js/state/store.js', 'web/js/services/sse.js',
             'web/js/components/SourceViewer.js', 'web/js/components/TranslationView.js',
             'web/js/components/LemmaTable.js', 'web/js/components/WorkspaceTabs.js',
             'kardenwort_desk.py', 'kardenwort_controller.py', 'kardenwort_events.py', 'scripts/run_release_boundary_contracts.py', 'tests/test_release_save_convergence.py',
             'tests/test_release_component_ownership.py', 'tests/test_release_update_recovery.py')}
        metadata['native_host_sha256'] = digest(Path(env['KARDENWORT_NATIVE_REPO']) / 'kardenwort-window/kardenwort-window.ahk')
        guard_log = checkout / 'database-guard.jsonl'
        metadata['external_database_attempts'] = [json.loads(line) for line in guard_log.read_text(encoding='utf8').splitlines()] if guard_log.exists() else []
        tooling_log = checkout / 'tooling-guard.jsonl'
        metadata['compiler_tooling_attempts'] = [json.loads(line) for line in tooling_log.read_text(encoding='utf8').splitlines()] if tooling_log.exists() else []
        # Preserve test proof even if Windows delays releasing a checkout handle.
        metadata['temporary_checkout_removed'] = False
        (evidence / (evidence_name + '-execution.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    metadata['temporary_checkout_removed'] = not checkout.exists()
    (evidence / (evidence_name + '-execution.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print((evidence / (evidence_name + '.log')).read_text(encoding='utf-8').splitlines()[-1])
    print(json.dumps(dict(metadata, external_database_attempts=len(metadata['external_database_attempts']))))
    assert metadata['protected_db_before'] == metadata['protected_db_after'], 'Original database changed during isolated run'
    assert metadata['original_legacy_test_sha256'] == metadata['copied_legacy_test_sha256']
    assert metadata['temporary_checkout_removed']
    assert not metadata['external_database_attempts'], 'Configuration escaped the isolated database boundary'
    assert not metadata['compiler_tooling_attempts'], 'Verification depends on retired compiler tooling'
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
