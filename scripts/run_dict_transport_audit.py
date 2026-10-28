"""Run review probes with owned storage and a protected-file comparison."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "results" / "20261027221743-dict-transport-audit"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.pop("SSLKEYLOGFILE", None)
os.environ.setdefault("KARDENWORT_NATIVE_REPO", str(ROOT.parent / "20240411110510-autohotkey"))
REVISION = None
if "--source-ref" in sys.argv:
    position = sys.argv.index("--source-ref")
    REVISION = sys.argv[position + 1]
    del sys.argv[position:position + 2]
    source = OUT / ("source-" + REVISION)
    source.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "web", source / "web", dirs_exist_ok=True)
    for relative in ("kardenwort_desk.py", "kardenwort_controller.py", "web/js/app.js",
                     "web/js/types.js", "web/js/services/sse.js", "web/js/components/WorkspaceTabs.js"):
        content = subprocess.check_output(["git", "show", REVISION + ":" + relative], cwd=ROOT)
        (source / relative).write_bytes(content)
    sys.path.insert(0, str(source))

import pytest
import kardenwort_desk as desk
import kardenwort_db
import kardenwort_controller


def protected_snapshot():
    files = [ROOT / "config.ini", ROOT / "anki-mapping.ini"]
    files += list((ROOT / "data").glob("*.db*"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file()}


def protected_file_stats():
    files = [ROOT / 'config.ini', ROOT / 'anki-mapping.ini', *(ROOT / 'data').glob('*.db*')]
    return {str(p.relative_to(ROOT)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in files if p.is_file()}


def backup_databases(label, snapshot):
    destination = OUT / (label + '-protected-before')
    destination.mkdir()
    for relative, digest in snapshot.items():
        if relative.startswith('data' + os.sep):
            target = destination / Path(relative).name
            shutil.copyfile(ROOT / relative, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise AssertionError('Protected database changed while its pre-run copy was created')
    return str(destination)


def source_snapshot():
    source_root = Path(desk.__file__).parent
    files = [source_root / relative for relative in (
        "kardenwort_desk.py", "kardenwort_controller.py", "web/js/app.js", "web/js/services/sse.js", "web/js/types.js")]
    files += [ROOT / relative for relative in ("tests/test_dict_transport_audit_regressions.py",
        "tests/test_dict_completion_contracts.py", "tests/test_release_audit_regressions.py", "tests/test_sqlite_mutations.py",
        "tests/test_controller.py", "tests/test_controller_api.py", "tests/test_server_sidecars.py",
        "tests/test_static_web_shell.py", "tests/test_ui_interactions.py", "tests/test_web_language_modal.py",
        "tests/test_sentence_idx_contract.py", "tests/test_suite_regression_boundaries.py")]
    files.append(Path(__file__))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


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

    @pytest.fixture(autouse=True)
    def audit_owned_network(self, request, monkeypatch):
        original_urlopen = urllib.request.urlopen

        def urlopen(url, *args, **kwargs):
            target = url.full_url if isinstance(url, urllib.request.Request) else url
            if protected_endpoint(target):
                raise urllib.error.URLError('Audit blocked access to a protected local service')
            return original_urlopen(url, *args, **kwargs)

        monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
        original_popen = subprocess.Popen
        blocked_workers = []
        owned_workers = []

        def popen(args, *positional, **kwargs):
            if isinstance(args, (list, tuple)) and any(str(arg) in ('progressive-worker', 'batch-worker') for arg in args):
                if not worker_owns_storage(args):
                    blocked_workers.append(next(str(arg) for arg in args if str(arg) in ('progressive-worker', 'batch-worker')))
                    raise AssertionError('Audit blocked an unattended worker using ambient workspace configuration')
                process = original_popen(args, *positional, **kwargs)
                owned_workers.append(process)
                return process
            if isinstance(args, (list, tuple)) and any(Path(str(arg)).name == 'kardenwort-window.ahk' for arg in args):
                raise AssertionError('Audit blocked a physical native window launch')
            return original_popen(args, *positional, **kwargs)

        monkeypatch.setattr(subprocess, 'Popen', popen)
        from playwright.sync_api import Browser
        original_context = Browser.new_context
        original_page = Browser.new_page
        blocked = []

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

        monkeypatch.setattr(Browser, 'new_context', new_context)
        monkeypatch.setattr(Browser, 'new_page', new_page)
        yield
        for process in owned_workers:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        if blocked or blocked_workers:
            with (OUT / 'blocked-local-services.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps({'test': request.node.nodeid, 'ports': sorted(set(blocked)),
                                         'requests': len(blocked), 'workers': blocked_workers}) + '\n')

    @pytest.fixture(autouse=True)
    def audit_owned_config(self, tmp_path, monkeypatch):
        owner = tmp_path.with_name(tmp_path.name + '-audit-config')
        monkeypatch.setattr(desk, "load_config", lambda config_path=None: load_owned_config(config_path, owner))


if __name__ == "__main__":
    label = sys.argv[1]
    original_cfg, _, _, _ = _load_config(ROOT / 'config.ini')
    PROTECTED_PORTS.add(original_cfg.getint('server', 'port', fallback=18335))
    before = protected_snapshot()
    backup = backup_databases(label, before)
    source_before = source_snapshot()
    owner = OUT / (label + "-scratch")
    owner.mkdir(exist_ok=True)
    tempfile.tempdir = str(owner)
    # These guards precede collection and module-scoped fixtures as well as test bodies.
    desk.load_config = lambda config_path=None: load_owned_config(config_path, owner)
    kardenwort_controller.load_config = desk.load_config
    kardenwort_db.KardenwortDB.get_connection = guarded_connection
    args = sys.argv[2:] + ["-q", "-p", "no:cacheprovider", "--tb=short", "--basetemp", str(OUT / (label + "-tmp")),
                           "--junitxml", str(OUT / (label + ".xml"))]
    (OUT / (label + '-execution.json')).write_text(json.dumps({
        'stage': 'running', 'pytest_args': args, 'protected_before': before,
        'database_backup': backup, 'source_before': source_before}, indent=2))
    result = pytest.main(args, plugins=[Isolation(before, label)])
    after = protected_snapshot()
    source_after = source_snapshot()
    evidence = {"pytest_args": args, "source_ref": REVISION or "working-tree", "exit_code": int(result),
                "stage": "finished", "database_backup": backup,
                "loaded_modules": [desk.__file__, kardenwort_controller.__file__],
                "source_before": source_before, "source_after": source_after,
                "source_unchanged": source_before == source_after,
                "protected_before": before, "protected_after": after,
                "protected_unchanged": before == after}
    (OUT / (label + "-execution.json")).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print("Protected files unchanged:", before == after)
    sys.exit(int(result) if before == after else 99)
