"""Run review probes with owned storage and a protected-file comparison."""
import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = None
REVISION = None


def digest_file(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def require_owned(path, root=ROOT):
    path = Path(path).resolve()
    owners = [(root / 'results').resolve(),
              (Path(os.environ['TEMP']) / 'kardenwort-desk-audits' / root.name).resolve()]
    if not any(path != owner and path.is_relative_to(owner) for owner in owners):
        raise ValueError('Audit paths must belong to a named directory under repository results '
                         'or the project-specific system temporary audit directory')
    return path


def capacity_preflight(output, reserve_bytes, backup_bytes=0):
    if reserve_bytes <= 0:
        raise ValueError('A positive test/evidence capacity reservation is required')
    free = shutil.disk_usage(output).free
    required = reserve_bytes + backup_bytes
    if free < required:
        raise OSError(f'Insufficient audit capacity: need {required} bytes, available {free}')
    return dict(available_bytes=free, reserved_bytes=reserve_bytes, backup_bytes=backup_bytes)


def database_hashes(snapshot):
    return {name: digest for name, digest in snapshot.items() if Path(name).parts[0] == 'data'}


def verify_backup(reuse, manifest, expected, root):
    backup = require_owned(reuse, root)
    metadata = json.loads(require_owned(manifest, root).read_text(encoding='utf-8'))
    if Path(metadata['database_backup']).resolve() != backup:
        raise ValueError('Backup ownership manifest names a different directory')
    if database_hashes(metadata['protected_before']) != expected:
        raise ValueError('Backup ownership manifest does not match current protected hashes')
    for relative, digest in expected.items():
        if digest_file(backup / Path(relative).name) != digest:
            raise ValueError('Retained backup does not match current protected hashes')
    return backup


def copy_backup(output, snapshot, root):
    backup = output / 'protected-before'
    backup.mkdir()
    for relative, digest in database_hashes(snapshot).items():
        target = backup / Path(relative).name
        shutil.copyfile(root / relative, target)
        if digest_file(target) != digest:
            raise ValueError('Protected database changed during backup creation')
    metadata = dict(database_backup=str(backup), protected_before=snapshot)
    (output / 'backup-manifest.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return backup


def prepare_backup(output, snapshot, reserve_bytes, reuse=None, manifest=None, root=ROOT):
    expected = database_hashes(snapshot)
    if (reuse is None) != (manifest is None):
        raise ValueError('Backup reuse requires both a directory and its ownership manifest')
    if reuse is not None:
        backup = verify_backup(reuse, manifest, expected, root)
        capacity = capacity_preflight(output, reserve_bytes)
        return dict(database_backup=str(backup), backup_reused=True, capacity=capacity)
    size = sum((root / name).stat().st_size for name in expected)
    capacity = capacity_preflight(output, reserve_bytes, size)
    backup = copy_backup(output, snapshot, root)
    return dict(database_backup=str(backup), backup_reused=False, capacity=capacity)


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='New directory under repository results or TEMP/kardenwort-desk-audits/REPOSITORY; must not exist')
    parser.add_argument('--reserve-mb', type=float, default=128,
                        help='Required free test/evidence capacity, in addition to a new backup')
    parser.add_argument('--reuse-backup', type=Path)
    parser.add_argument('--backup-manifest', type=Path)
    parser.add_argument('--source-ref')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    if options.pytest_args[:1] == ['--']:
        options.pytest_args.pop(0)
    return options


def prepare_source(revision, output):
    if revision is None:
        return
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', revision + '^{commit}'],
                                     cwd=ROOT, text=True).strip()
    source = output / 'source'
    shutil.copytree(ROOT / 'web', source / 'web')
    for relative in ('kardenwort_desk.py', 'kardenwort_controller.py', 'kardenwort_reword.py',
                     'web/js/app.js', 'web/js/types.js', 'web/js/services/sse.js',
                     'web/js/components/WorkspaceTabs.js'):
        # Historical trees can predate the separate Re-word helper module.
        names = subprocess.check_output(['git', 'ls-tree', '--name-only', commit, '--', relative], cwd=ROOT, text=True)
        if names.strip():
            content = subprocess.check_output(['git', 'show', commit + ':' + relative], cwd=ROOT)
            (source / relative).write_bytes(content)
    sys.path.insert(0, str(source))


if __name__ == '__main__':
    options = parse_arguments(sys.argv[1:])
    OUT = require_owned(options.output_dir)
    OUT.mkdir(parents=True, exist_ok=False)
    try:
        capacity_preflight(OUT, int(options.reserve_mb * 1024 * 1024))
        REVISION = options.source_ref
        prepare_source(REVISION, OUT)
    except Exception as error:
        evidence = dict(stage='preflight-failed', command=sys.argv, exit_code=2,
                        error=f'{type(error).__name__}: {error}', output_dir=str(OUT))
        (OUT / 'execution.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(evidence['error'], file=sys.stderr)
        sys.exit(2)

import pytest
import kardenwort_desk as desk
import kardenwort_db
import kardenwort_controller


def protected_snapshot():
    files = [ROOT / "config.ini", ROOT / "anki-mapping.ini"]
    files += list((ROOT / "data").glob("*.db*"))
    return {str(p.relative_to(ROOT)): digest_file(p)
            for p in files if p.is_file()}


def protected_file_stats():
    files = [ROOT / 'config.ini', ROOT / 'anki-mapping.ini', *(ROOT / 'data').glob('*.db*')]
    return {str(p.relative_to(ROOT)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in files if p.is_file()}



def source_snapshot():
    source_root = Path(desk.__file__).parent
    files = list(source_root.glob('*.py'))
    for directory in ('web', 'tests', 'scripts'):
        owner = source_root if directory == 'web' else ROOT
        files.extend(path for path in (owner / directory).rglob('*')
                     if path.suffix in ('.py', '.js', '.css', '.html') and '__pycache__' not in path.parts)
    return {str(path): digest_file(path) for path in sorted(set(files))}


_load_config = desk.load_config
_get_connection = kardenwort_db.KardenwortDB.get_connection
PROTECTED_PORTS = {18335, 8081, 8082, 8083}


def protected_endpoint(url):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.port not in PROTECTED_PORTS:
        return False
    if parsed.hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        return False


def load_owned_config(config_path, owner):
    requested = Path(config_path).resolve() if config_path is not None else ROOT / "config.ini"
    cfg, paths, gd, wordfill = _load_config(requested)
    if requested != ROOT / "config.ini":
        return cfg, paths, gd, wordfill
    database = owner / "config-default.db"
    owned_results = owner / "results"
    owned_results.mkdir(parents=True, exist_ok=True)
    cfg.set("storage", "sqlite_db_path", str(database))
    cfg.set("project_structure", "generated_results_dir", str(owned_results))
    paths.update(sqlite_db_path=database, results_dir=owned_results, generated_results_dir=owned_results)
    if isinstance(wordfill, dict):
        wordfill.update(sqlite_db_path=database, scan_roots=[owned_results])
    return cfg, paths, gd, wordfill


def guarded_connection(instance, *args, **kwargs):
    if not Path(instance.db_path).resolve().is_relative_to(OUT):
        raise AssertionError("Audit attempted database access outside its owned fixture directory")
    return _get_connection(instance, *args, **kwargs)


def worker_owns_storage(args):
    if '--config' not in args:
        return False
    position = args.index('--config') + 1
    if position >= len(args):
        return False
    config_path = Path(args[position]).resolve()
    if not config_path.is_relative_to(OUT):
        return False
    _, paths, _, _ = _load_config(config_path)
    database = paths.get('sqlite_db_path')
    return database is not None and Path(database).resolve().is_relative_to(OUT)


class Isolation:
    def __init__(self, before, label):
        self.before = before
        self.stats = protected_file_stats()
        self.label = label

    def check_protected(self, nodeid):
        stats = protected_file_stats()
        if stats != self.stats:
            after = protected_snapshot()
            if after != self.before:
                evidence = {'test': nodeid, 'protected_before': self.before, 'protected_after': after}
                (OUT / (self.label + '-protected-change.json')).write_text(json.dumps(evidence, indent=2))
                pytest.exit('Audit stopped because protected workspace files changed', returncode=99)
            self.stats = stats

    @pytest.fixture(autouse=True)
    def audit_protected_files(self, request):
        self.check_protected(request.node.nodeid)
        yield
        self.check_protected(request.node.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            with (OUT / (self.label + '-failures.txt')).open('a', encoding='utf-8') as output:
                output.write(report.nodeid + '\n' + str(report.longrepr) + '\n')

    @contextmanager
    def owned_network(self, monkeypatch):
        # asyncio's Windows transport subclasses Popen while importing Playwright.
        from playwright.sync_api import Browser
        blocked_workers, owned_workers, blocked = [], [], []
        install_socket_guard(monkeypatch)
        install_url_guard(monkeypatch)
        install_subprocess_guard(monkeypatch, blocked_workers, owned_workers)
        install_browser_guard(monkeypatch, Browser, blocked)
        try:
            yield
        finally:
            finish_workers(owned_workers)
            if blocked or blocked_workers:
                with (OUT / 'blocked-local-services.jsonl').open('a', encoding='utf-8') as output:
                    output.write(json.dumps({'scope': 'collection and test execution', 'ports': sorted(set(blocked)),
                                             'requests': len(blocked), 'workers': blocked_workers}) + '\n')

    @pytest.fixture(autouse=True)
    def audit_owned_config(self, tmp_path, monkeypatch):
        owner = tmp_path.with_name(tmp_path.name + '-audit-config')
        monkeypatch.setattr(desk, "load_config", lambda config_path=None: load_owned_config(config_path, owner))


def install_socket_guard(monkeypatch):
    original_connect = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = address[:2]
            url = f'http://[{host}]:{port}' if ':' in host else f'http://{host}:{port}'
            if protected_endpoint(url):
                raise AssertionError('Audit blocked a socket connection to a protected local service')
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', connect)


def install_url_guard(monkeypatch):
    original_urlopen = urllib.request.urlopen

    def urlopen(url, *args, **kwargs):
        target = url.full_url if isinstance(url, urllib.request.Request) else url
        if protected_endpoint(target):
            raise urllib.error.URLError('Audit blocked access to a protected local service')
        return original_urlopen(url, *args, **kwargs)

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)


def worker_command(args):
    return isinstance(args, (list, tuple)) and any(str(arg) in ('progressive-worker', 'batch-worker') for arg in args)


def native_command(args):
    return isinstance(args, (list, tuple)) and any(Path(str(arg)).name == 'kardenwort-window.ahk' for arg in args)


def install_subprocess_guard(monkeypatch, blocked, owned):
    original_popen = subprocess.Popen

    def popen(args, *positional, **kwargs):
        if worker_command(args):
            if not worker_owns_storage(args):
                blocked.append([str(arg) for arg in args])
                raise AssertionError('Audit blocked an unattended worker using ambient workspace configuration')
            process = original_popen(args, *positional, **kwargs)
            owned.append(process)
            return process
        if native_command(args):
            raise AssertionError('Audit blocked a physical native window launch')
        return original_popen(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', popen)


def install_browser_guard(monkeypatch, browser_type, blocked):
    original_context = browser_type.new_context
    original_page = browser_type.new_page

    def route_request(route):
        if protected_endpoint(route.request.url):
            blocked.append(urllib.parse.urlparse(route.request.url).port)
            route.abort('blockedbyclient')
        else:
            route.continue_()

    def new_context(browser, *args, **kwargs):
        context = original_context(browser, *args, **kwargs)
        context.route('**/*', route_request)
        return context

    def new_page(browser, *args, **kwargs):
        page = original_page(browser, *args, **kwargs)
        page.route('**/*', route_request)
        return page

    monkeypatch.setattr(browser_type, 'new_context', new_context)
    monkeypatch.setattr(browser_type, 'new_page', new_page)


def finish_workers(workers):
    for process in workers:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


@contextmanager
def owned_runtime(owner, isolation):
    # Install before pytest collection, including module-scoped fixture setup.
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(tempfile, 'tempdir', str(owner))
        patches.setattr(desk, 'load_config', lambda config_path=None: load_owned_config(config_path, owner))
        patches.setattr(kardenwort_controller, 'load_config', desk.load_config)
        patches.setattr(kardenwort_db.KardenwortDB, 'get_connection', guarded_connection)
        with isolation.owned_network(patches):
            yield


class Tee:
    def __init__(self, terminal, log):
        self.terminal, self.log = terminal, log

    def write(self, value):
        self.log.write(value)
        return self.terminal.write(value)

    def flush(self):
        self.log.flush()
        self.terminal.flush()

    def __getattr__(self, name):
        return getattr(self.terminal, name)


def main(options):
    os.environ.pop('SSLKEYLOGFILE', None)
    os.environ.setdefault('KARDENWORT_NATIVE_REPO', str(ROOT.parent / '20240411110510-autohotkey'))
    before = protected_snapshot()
    source_before = source_snapshot()
    args = options.pytest_args + ['-q', '-p', 'no:cacheprovider', '--tb=short',
                                 '--basetemp', str(OUT / 'fixtures'), '--junitxml', str(OUT / 'junit.xml')]
    evidence = dict(stage='preflight', command=sys.argv, pytest_args=args,
                    source_ref=REVISION or 'working-tree', source_before=source_before,
                    protected_before=before, output_dir=str(OUT),
                    loaded_modules=[desk.__file__, kardenwort_controller.__file__])
    record = OUT / 'execution.json'
    result = 2
    try:
        evidence.update(prepare_backup(OUT, before, int(options.reserve_mb * 1024 * 1024),
                                      options.reuse_backup, options.backup_manifest))
        original_cfg, _, _, _ = _load_config(ROOT / 'config.ini')
        PROTECTED_PORTS.add(original_cfg.getint('server', 'port', fallback=18335))
        owner = OUT / 'scratch'
        owner.mkdir()
        isolation = Isolation(before, 'run')
        with owned_runtime(owner, isolation), (OUT / 'pytest.log').open('w', encoding='utf-8') as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                evidence['stage'] = 'running'
                record.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
                result = int(pytest.main(args, plugins=[isolation]))
        evidence['stage'] = 'finished'
    except Exception as error:
        result = 2
        evidence.update(stage='failed', error=f'{type(error).__name__}: {error}')
        print(evidence['error'], file=sys.stderr)
    finally:
        after = protected_snapshot()
        source_after = source_snapshot()
        evidence.update(exit_code=result, source_after=source_after, source_unchanged=source_before == source_after,
                        protected_after=after, protected_unchanged=before == after)
        if before != after or source_before != source_after:
            result = evidence['exit_code'] = 99
        record.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print('Protected files unchanged:', before == after)
    return result


if __name__ == '__main__':
    sys.exit(main(options))
