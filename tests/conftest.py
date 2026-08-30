import sys
import socket
import urllib.parse
import urllib.request
import urllib.error
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

