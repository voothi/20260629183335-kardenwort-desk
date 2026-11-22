"""Acceptance tests for serializing ProcessSupervisor startup and shutdown.

Spec: specs/product-reliability-acceptance/spec.md
Requirement: Serialize supervisor startup and shutdown
"""
import ast
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import socket
import sys
import threading

import pytest

import kardenwort_controller as controller
from kardenwort_controller_lifetime import stop_owned_process
from tests.browser_test_server import create_browser_test_server
from tests.controller_test_support import owned_configuration
from tests.test_controller_lifetime import CHILD_HEALTH_SCRIPT
from tests.test_intent_architecture import complexity, functions
from tests.thread_support import Worker


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_owned_child_service(tmp_path, name: str, status: int = 200) -> controller.SidecarService:
    script = tmp_path / f"{name}-health-child.py"
    script.write_text(CHILD_HEALTH_SCRIPT, encoding="utf-8")
    port = _free_loopback_port()
    return controller.SidecarService(
        name=name,
        port=port,
        launch_cmd=[sys.executable, str(script), str(port), str(status)],
        cwd=tmp_path,
    )


def _new_enabled_supervisor(tmp_path, services: dict[str, controller.SidecarService]) -> controller.ProcessSupervisor:
    config, paths, _ = owned_configuration(tmp_path, "", "sqlite")
    supervisor = controller.ProcessSupervisor(config, paths, enabled=True)
    supervisor.services = services
    return supervisor


def _cleanup_services(services: dict[str, controller.SidecarService]) -> None:
    for svc in services.values():
        if svc.process is not None and svc.process.poll() is None:
            stop_owned_process(svc.process)


def test_supervisor_stop_during_start(tmp_path, monkeypatch):
    """Scenario: Stop during startup.

    When stop completes while startup is waiting before watchdog creation,
    resuming that startup must not clear cancellation or start a watchdog or sidecar.
    """
    svc = _make_owned_child_service(tmp_path, "owned-svc")
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})

    before_monitor = threading.Event()
    allow_resume = threading.Event()
    original_start_monitor = supervisor._start_monitor

    def rendezvous_before_monitor(*args, **kwargs):
        before_monitor.set()
        assert allow_resume.wait(5), "Timed out waiting to resume startup after stop"
        return original_start_monitor(*args, **kwargs)

    # Deterministic rendezvous around the real _start_monitor method (not faked).
    monkeypatch.setattr(supervisor, "_start_monitor", rendezvous_before_monitor)

    starter = Worker(supervisor.start)
    try:
        assert before_monitor.wait(10), "Startup did not reach watchdog creation boundary"
        assert svc.process is not None and svc.process.poll() is None

        supervisor.stop()
        assert svc.process.poll() is not None
        if sys.platform == "win32":
            assert supervisor.job.job_handle is None

        allow_resume.set()
        starter.result(timeout=5)

        assert supervisor._monitor_stop.is_set(), (
            "Resumed earlier startup cleared cancellation after stop had already completed"
        )
        assert supervisor._monitor_thread is None or not supervisor._monitor_thread.is_alive(), (
            "Resumed earlier startup launched a live SupervisorWatchdog after stop had already completed"
        )
        assert svc.process.poll() is not None
    finally:
        allow_resume.set()
        try:
            starter.result(timeout=5)
        finally:
            supervisor.stop()
            _cleanup_services(supervisor.services)


def test_supervisor_ordinary_lifecycle(tmp_path):
    """Scenario: Ordinary lifecycle (valid adjacent control).

    When the supervisor starts normally and then stops, owned resources terminate
    and the watchdog is joined.
    """
    svc = _make_owned_child_service(tmp_path, "ordinary-svc")
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})
    try:
        supervisor.start()
        assert svc.process is not None and svc.process.poll() is None
        assert svc.managed_by_supervisor is True
        assert supervisor.probe_health(svc) is True
        assert supervisor._monitor_thread is not None and supervisor._monitor_thread.is_alive()

        supervisor.stop()
        assert svc.process.poll() is not None
        assert not supervisor._monitor_thread.is_alive()
        assert supervisor._monitor_stop.is_set()
        if sys.platform == "win32":
            assert supervisor.job.job_handle is None
    finally:
        supervisor.stop()
        _cleanup_services(supervisor.services)


def test_supervisor_repeated_stop(tmp_path):
    """Scenario: Repeated stop (valid adjacent control).

    When stop is called again after a completed stop, no owned process or thread
    is resurrected or disposed twice unsafely.
    """
    svc = _make_owned_child_service(tmp_path, "repeated-stop-svc")
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})
    try:
        supervisor.start()
        assert svc.process is not None and svc.process.poll() is None
        assert supervisor._monitor_thread is not None and supervisor._monitor_thread.is_alive()

        supervisor.stop()
        first_thread = supervisor._monitor_thread
        first_process = svc.process
        assert first_process.poll() is not None
        assert not first_thread.is_alive()
        if sys.platform == "win32":
            assert supervisor.job.job_handle is None

        # Repeated stop after completed stop must remain safe and idempotent.
        supervisor.stop()
        supervisor.stop()

        assert supervisor._monitor_thread is first_thread
        assert not first_thread.is_alive()
        assert svc.process is first_process
        assert first_process.poll() is not None
        assert supervisor._monitor_stop.is_set()
        if sys.platform == "win32":
            assert supervisor.job.job_handle is None
    finally:
        supervisor.stop()
        _cleanup_services(supervisor.services)


def test_supervisor_stop_between_sidecar_spawns_during_start(tmp_path, monkeypatch):
    """Stop during startup between service 1 and service 2 must prevent spawning service 2 or watchdog."""
    svc1 = _make_owned_child_service(tmp_path, "svc-one")
    svc2 = _make_owned_child_service(tmp_path, "svc-two")
    supervisor = _new_enabled_supervisor(tmp_path, {svc1.name: svc1, svc2.name: svc2})

    after_first = threading.Event()
    allow_resume = threading.Event()
    original_spawn = supervisor.spawn_service
    spawn_calls = []

    def rendezvous_spawn(service, *args, **kwargs):
        spawn_calls.append(service.name)
        result = original_spawn(service, *args, **kwargs)
        if service.name == svc1.name:
            after_first.set()
            assert allow_resume.wait(5), "Timed out waiting to resume startup between services"
        return result

    monkeypatch.setattr(supervisor, "spawn_service", rendezvous_spawn)

    starter = Worker(supervisor.start)
    try:
        assert after_first.wait(10), "Startup did not finish spawning first service"
        assert svc1.process is not None and svc1.process.poll() is None
        assert svc2.process is None

        supervisor.stop()
        assert svc1.process.poll() is not None

        allow_resume.set()
        starter.result(timeout=5)

        assert spawn_calls == [svc1.name]
        assert svc2.process is None
        assert supervisor._monitor_thread is None
        assert supervisor._monitor_stop.is_set()
    finally:
        allow_resume.set()
        try:
            starter.result(timeout=5)
        finally:
            supervisor.stop()
            _cleanup_services(supervisor.services)


def test_supervisor_stop_during_start_health_wait(tmp_path):
    """Stop during start() health-wait cancels probing, terminates spawned child, and launches no watchdog."""
    entered = threading.Event()

    class UnhealthyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.probes += 1
            self.send_response(503)
            self.end_headers()
            if self.server.probes >= 2:
                entered.set()

        def log_message(self, *args):
            pass

    listener = create_browser_test_server(UnhealthyHandler)
    listener.probes = 0
    listener.start()
    child = tmp_path / "unhealthy-start-child.py"
    child.write_text("import threading\nthreading.Event().wait()\n", encoding="utf-8")
    svc = controller.SidecarService("startup-wait-svc", listener.server_port, [sys.executable, str(child)], tmp_path)
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})

    starter = Worker(supervisor.start)
    stopper = None
    try:
        assert entered.wait(5), "Child did not reach post-launch health probe during start()"
        assert svc.process is not None and svc.process.poll() is None

        stopper = Worker(supervisor.stop)
        stopper.result(timeout=3)
        starter.result(timeout=3)

        assert svc.process.poll() is not None
        assert supervisor._monitor_thread is None
        assert supervisor._monitor_stop.is_set()
    finally:
        supervisor._monitor_stop.set()
        try:
            starter.result(timeout=10)
        finally:
            supervisor.stop()
            if stopper is not None:
                stopper.result(timeout=5)
            _cleanup_services(supervisor.services)
            listener.server_close()


def test_supervisor_stop_joins_watchdog_without_lock_deadlock(tmp_path, monkeypatch):
    """stop() must not hold lifecycle locks while joining a watchdog that calls spawn_service()."""
    entered_health_wait = threading.Event()

    class UnhealthyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.probes += 1
            self.send_response(503)
            self.end_headers()
            # Probe 1: _supervise_service, Probe 2: spawn_service pre-check, Probe 3+: _await_service_health.
            if self.server.probes >= 3:
                entered_health_wait.set()

        def log_message(self, *args):
            pass

    listener = create_browser_test_server(UnhealthyHandler)
    listener.probes = 0
    listener.start()
    child = tmp_path / "watchdog-restart-child.py"
    child.write_text("import threading\nthreading.Event().wait()\n", encoding="utf-8")
    svc = controller.SidecarService("watchdog-svc", listener.server_port, [sys.executable, str(child)], tmp_path)
    svc.managed_by_supervisor = True
    svc.process = None

    config, paths, _ = owned_configuration(tmp_path, "", "sqlite")
    supervisor = controller.ProcessSupervisor(config, paths, enabled=False)
    supervisor.services = {svc.name: svc}

    # Drive watchdog immediately into _supervise_service -> spawn_service -> _await_service_health.
    watch_ready = threading.Event()

    def immediate_loop(generation=None, stop_event=None):
        watch_ready.set()
        supervisor._supervise_service(svc, generation=generation, stop_event=stop_event)

    monkeypatch.setattr(supervisor, "_supervision_loop", immediate_loop)
    try:
        assert supervisor._start_monitor() is True
        assert watch_ready.wait(3)
        assert entered_health_wait.wait(5), "Watchdog did not enter spawn_service health wait"
        assert svc.process is not None and svc.process.poll() is None

        stopper = Worker(supervisor.stop)
        stopper.result(timeout=3)

        assert not supervisor._monitor_thread.is_alive()
        assert svc.process.poll() is not None
    finally:
        supervisor.stop()
        _cleanup_services(supervisor.services)
        listener.server_close()


def test_supervisor_repeated_and_concurrent_stop_does_not_double_dispose(tmp_path, monkeypatch):
    """Repeated and concurrent stop() calls dispose owned resources exactly once."""
    svc = _make_owned_child_service(tmp_path, "single-dispose-svc")
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})

    stop_entered = threading.Event()
    allow_stop_finish = threading.Event()
    dispose_calls = []
    original_stop_services = supervisor._stop_services
    original_job_close = supervisor.job.close
    job_close_calls = []

    def observed_stop_services():
        dispose_calls.append("stop_services")
        stop_entered.set()
        assert allow_stop_finish.wait(5), "Timed out waiting for concurrent stop caller"
        return original_stop_services()

    def observed_job_close():
        job_close_calls.append("job_close")
        return original_job_close()

    monkeypatch.setattr(supervisor, "_stop_services", observed_stop_services)
    monkeypatch.setattr(supervisor.job, "close", observed_job_close)

    try:
        supervisor.start()
        assert svc.process is not None and svc.process.poll() is None

        first_stopper = Worker(supervisor.stop)
        assert stop_entered.wait(5), "First stop did not reach service disposal"

        second_stopper = Worker(supervisor.stop)
        allow_stop_finish.set()
        first_stopper.result(timeout=5)
        second_stopper.result(timeout=5)

        # Additional sequential repeated stop must be a safe no-op.
        supervisor.stop()

        assert dispose_calls == ["stop_services"]
        assert job_close_calls == ["job_close"]
        assert svc.process.poll() is not None
        assert not supervisor._monitor_thread.is_alive()
    finally:
        allow_stop_finish.set()
        supervisor.stop()
        _cleanup_services(supervisor.services)


def test_supervisor_deliberate_restart_requires_new_generation(tmp_path, monkeypatch):
    """Deliberate start() after completed stop() creates a new lifecycle generation isolated from stale startup."""
    svc = _make_owned_child_service(tmp_path, "restart-gen-svc")
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})

    before_first_monitor = threading.Event()
    allow_stale_resume = threading.Event()
    original_start_monitor = supervisor._start_monitor
    calls = []

    def gated_monitor(*args, **kwargs):
        calls.append(kwargs.get("generation"))
        if len(calls) == 1:
            before_first_monitor.set()
            assert allow_stale_resume.wait(5), "Timed out waiting to resume stale startup"
        return original_start_monitor(*args, **kwargs)

    monkeypatch.setattr(supervisor, "_start_monitor", gated_monitor)

    stale_starter = Worker(supervisor.start)
    try:
        assert before_first_monitor.wait(10)
        first_event = supervisor._monitor_stop
        first_generation = supervisor._generation
        first_process = svc.process
        assert first_process is not None and first_process.poll() is None

        # Stop completes while gen 1 startup is still paused before _start_monitor.
        supervisor.stop()
        assert first_event.is_set()
        assert first_process.poll() is not None

        # Deliberate restart allocates a new lifecycle generation and fresh stop event.
        supervisor.start()
        second_event = supervisor._monitor_stop
        second_generation = supervisor._generation
        second_thread = supervisor._monitor_thread
        second_process = svc.process

        assert second_generation > first_generation
        assert second_event is not first_event
        assert first_event.is_set()
        assert not second_event.is_set()
        assert second_thread is not None and second_thread.is_alive()
        assert second_process is not None and second_process is not first_process and second_process.poll() is None

        # Late resumption of stale gen 1 startup must not disturb gen 2/3 or raise.
        allow_stale_resume.set()
        stale_starter.result(timeout=5)

        assert supervisor._generation == second_generation
        assert supervisor._monitor_thread is second_thread and second_thread.is_alive()
        assert not second_event.is_set()
        assert first_event.is_set()

        # Final stop terminates the restarted generation cleanly.
        supervisor.stop()
        assert second_event.is_set()
        assert not second_thread.is_alive()
        assert second_process.poll() is not None
    finally:
        allow_stale_resume.set()
        try:
            stale_starter.result(timeout=5)
        finally:
            supervisor.stop()
            _cleanup_services(supervisor.services)


def test_supervisor_stop_preserves_disposal_errors(tmp_path, monkeypatch):
    """Real disposal errors must reach the caller and must not be hidden as a clean stop."""
    svc = _make_owned_child_service(tmp_path, "error-preserve-svc")
    supervisor = _new_enabled_supervisor(tmp_path, {svc.name: svc})

    fail_once = [True]
    original_close = supervisor.job.close

    def flaky_close():
        if fail_once[0]:
            raise OSError("simulated job close failure")
        return original_close()

    monkeypatch.setattr(supervisor.job, "close", flaky_close)

    try:
        supervisor.start()
        with pytest.raises(ExceptionGroup, match="Supervisor disposal failed") as caught:
            supervisor.stop()
        assert any("simulated job close failure" in str(err) for err in caught.value.exceptions)
        assert svc.process.poll() is not None
        assert not supervisor._monitor_thread.is_alive()

        # Failed shutdown blocks silent restart and retries disposal on subsequent stop().
        with pytest.raises(RuntimeError, match="failed shutdown"):
            supervisor.start()

        fail_once[0] = False
        supervisor.stop()
        if sys.platform == "win32":
            assert supervisor.job.job_handle is None
    finally:
        fail_once[0] = False
        supervisor.stop()
        _cleanup_services(supervisor.services)


def test_supervisor_lifecycle_helpers_meet_complexity_ceiling():
    """Every modified and newly added ProcessSupervisor lifecycle helper must stay within CC <= 7."""
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "kardenwort_controller.py").read_text(encoding="utf-8-sig"))
    expected = {
        "_active_stop_event",
        "_is_generation_cancelled",
        "_begin_start_generation",
        "_abort_failed_start",
        "_replace_and_launch_service",
        "_confirm_healthy_service",
        "_claim_started_monitor",
        "_claim_monitor_generation",
        "_begin_stop",
        "_finish_stop",
        "start",
        "_start_monitor",
        "_supervision_loop",
        "_supervise_service",
        "spawn_service",
        "_reuse_healthy_service",
        "_service_script_available",
        "_launch_service",
        "_create_service_process",
        "_await_service_health",
        "_join_monitor",
        "_stop_services",
        "stop",
    }
    measured = {node.name: complexity(node) for node in functions(tree) if node.name in expected}
    assert set(measured) == expected
    assert max(measured.values()) <= 7, measured
