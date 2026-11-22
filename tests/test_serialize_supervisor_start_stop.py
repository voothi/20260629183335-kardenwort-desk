"""Acceptance tests for serializing ProcessSupervisor startup and shutdown.

Spec: specs/product-reliability-acceptance/spec.md
Requirement: Serialize supervisor startup and shutdown
"""
import socket
import sys
import threading

import kardenwort_controller as controller
from kardenwort_controller_lifetime import stop_owned_process
from tests.controller_test_support import owned_configuration
from tests.test_controller_lifetime import CHILD_HEALTH_SCRIPT
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
