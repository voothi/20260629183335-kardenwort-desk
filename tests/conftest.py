import sys
import socket
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
import pytest


LIVE_DAEMON_PORTS = {8081, 8082, 8083, 59999}

@pytest.fixture(autouse=True)
def capture_raw_stdout_in_tests(monkeypatch):
    """
    Redirects sys.__stdout__ to sys.stdout during pytest execution.
    This allows pytest's standard stream capture to catch emit_payload()
    calls, keeping the test runner console output clean and concise.
    """
    monkeypatch.setattr(sys, "__stdout__", sys.stdout)


@pytest.fixture(autouse=True)
def isolate_microservice_sidecars(monkeypatch):
    """
    Hermetically isolates automated test runs from live background developer daemons
    running on default localhost ports (8081 for SpaCy, 8082 for Translation, 8083 for IntelliFiller).
    Also simulates immediate connection refusal for the offline test mock port (59999).
    Integration tests requiring live microservices must spin up dynamic ephemeral servers
    on dynamic ports (e.g. port 0).
    """
    try:
        import kardenwort_desk
        orig_check = kardenwort_desk.check_endpoint_reachable

        def sandboxed_check(server_url: str, connect_timeout: float = kardenwort_desk.MICROSERVICE_CONNECT_TIMEOUT_DEFAULT) -> bool:
            if not server_url:
                return False
            parsed = urllib.parse.urlparse(server_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if port in LIVE_DAEMON_PORTS:
                return False
            return orig_check(server_url, connect_timeout=connect_timeout)

        monkeypatch.setattr(kardenwort_desk, "check_endpoint_reachable", sandboxed_check)
    except ImportError:
        pass

    try:
        import kardenwort_controller
        orig_probe = kardenwort_controller.ProcessSupervisor.probe_health

        def sandboxed_probe(self, service, timeout=1.0):
            if service.port in LIVE_DAEMON_PORTS:
                service.is_healthy = False
                return False
            return orig_probe(self, service, timeout=timeout)

        monkeypatch.setattr(kardenwort_controller.ProcessSupervisor, "probe_health", sandboxed_probe)
    except (ImportError, AttributeError):
        pass

    orig_urlopen = urllib.request.urlopen

    def sandboxed_urlopen(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kwargs):
        req_url = url.full_url if isinstance(url, urllib.request.Request) else str(url)
        parsed = urllib.parse.urlparse(req_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname or ""
        if host in ("127.0.0.1", "localhost") and port in LIVE_DAEMON_PORTS:
            raise urllib.error.URLError(ConnectionRefusedError(10061, f"Sandboxed port {port} blocked in test harness"))
        return orig_urlopen(url, data, timeout, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", sandboxed_urlopen)


@pytest.fixture(autouse=True)
def reset_provider_cooldowns():
    """
    Ensures that provider circuit breaker cooldown states do not leak across tests.
    """
    try:
        import kardenwort_desk
        kardenwort_desk.clear_provider_cooldowns()
    except (ImportError, AttributeError):
        pass
    yield
    try:
        import kardenwort_desk
        kardenwort_desk.clear_provider_cooldowns()
        for h in list(getattr(kardenwort_desk.logger, "handlers", [])):
            stream = getattr(h, "stream", None)
            if stream is not None and getattr(stream, "closed", False):
                kardenwort_desk.logger.removeHandler(h)
    except (ImportError, AttributeError):
        pass


@pytest.fixture(scope="session", autouse=True)
def isolate_test_results_dir(tmp_path_factory):
    """
    Hermetically isolates automated test runs from polluting the repository root `results/`
    directory with ephemeral session logs, updates, marker files, or generated TSVs.
    Redirects kardenwort_desk.load_config and default resolve_results_dir logic to a sandboxed
    temporary directory, unless an explicit non-repo directory is specified.
    """
    sandboxed_results = tmp_path_factory.mktemp("sandboxed_results")
    repo_results = (Path(__file__).resolve().parent.parent / "results").resolve()
    mp = pytest.MonkeyPatch()

    def _is_repo_results(p):
        if p is None:
            return True
        try:
            resolved = Path(p).resolve()
            return resolved == repo_results or repo_results in resolved.parents
        except Exception:
            return False

    try:
        import kardenwort_desk
        orig_load_config = kardenwort_desk.load_config
        orig_resolve_results = kardenwort_desk.resolve_results_dir

        def sandboxed_load_config(*args, **kwargs):
            cfg, resolved_paths, gd, wf = orig_load_config(*args, **kwargs)
            if isinstance(resolved_paths, dict):
                resolved_paths["results_dir"] = sandboxed_results
                resolved_paths["generated_results_dir"] = sandboxed_results
            return cfg, resolved_paths, gd, wf

        def sandboxed_resolve_results(resolved_paths=None, kw_config=None):
            if isinstance(resolved_paths, dict):
                for key in ("generated_results_dir", "results_dir"):
                    val = resolved_paths.get(key)
                    if val and not _is_repo_results(val):
                        return Path(val).resolve()
            res = orig_resolve_results(resolved_paths, kw_config)
            if _is_repo_results(res):
                return sandboxed_results
            return Path(res).resolve()

        mp.setattr(kardenwort_desk, "load_config", sandboxed_load_config)
        mp.setattr(kardenwort_desk, "resolve_results_dir", sandboxed_resolve_results)

        if hasattr(kardenwort_desk, "SessionLogger"):
            orig_logger_init = kardenwort_desk.SessionLogger.__init__

            def sandboxed_logger_init(self, zid, results_dir, trace_id=None):
                if _is_repo_results(results_dir):
                    results_dir = sandboxed_results
                orig_logger_init(self, zid, results_dir, trace_id=trace_id)

            mp.setattr(kardenwort_desk.SessionLogger, "__init__", sandboxed_logger_init)
    except (ImportError, AttributeError):
        pass

    try:
        import kardenwort_controller
        if hasattr(kardenwort_controller, "load_config"):
            mp.setattr(kardenwort_controller, "load_config", sandboxed_load_config)
        if hasattr(kardenwort_controller, "resolve_results_dir"):
            mp.setattr(kardenwort_controller, "resolve_results_dir", sandboxed_resolve_results)
    except (ImportError, AttributeError):
        pass

    try:
        import kardenwort_db
        if hasattr(kardenwort_db, "_GLOBAL_DB_LOGGER") and hasattr(kardenwort_db._GLOBAL_DB_LOGGER, "set_log_path"):
            orig_set_log_path = kardenwort_db._GLOBAL_DB_LOGGER.set_log_path

            def sandboxed_set_log_path(log_path, max_mb=5.0):
                if log_path and _is_repo_results(Path(log_path).parent):
                    log_path = sandboxed_results / Path(log_path).name
                orig_set_log_path(log_path, max_mb=max_mb)

            mp.setattr(kardenwort_db._GLOBAL_DB_LOGGER, "set_log_path", sandboxed_set_log_path)
    except (ImportError, AttributeError):
        pass

    yield sandboxed_results
    mp.undo()



