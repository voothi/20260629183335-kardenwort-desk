"""Actual controller startup failures and owned daemon shutdown contracts."""
from types import SimpleNamespace
from contextlib import contextmanager
from http.client import HTTPConnection, RemoteDisconnected
from http.server import BaseHTTPRequestHandler
import json
import socket
import struct
import subprocess
import sys
import threading

import pytest

import kardenwort_controller as controller
from tests.controller_test_support import owned_configuration
from tests.thread_support import Worker, wait_for_http
from kardenwort_events import EventDispatcher
from kardenwort_controller_lifetime import ControllerHTTPServer, run_cleanup, stop_owned_process


def test_failed_preference_initialization_releases_bound_controller_socket(tmp_path, monkeypatch):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    config.set('server', 'controller_port', '0')
    config.set('settings', 'anki_mapping_file', str(paths['anki_mapping_file']))
    paths['sqlite_db_path'].write_bytes(b'not a SQLite database')
    configuration = tmp_path / 'config.ini'
    with configuration.open('w', encoding='utf-8') as stream:
        config.write(stream)
    instances = []
    original = controller.ControllerHTTPServer

    class ObservedServer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)

    # Resource construction is observed, not replaced by a fake listener/result.
    monkeypatch.setattr(controller, 'ControllerHTTPServer', ObservedServer)
    try:
        with pytest.raises(Exception, match='database|SQLite'):
            controller.run_controller(SimpleNamespace(config=configuration, port=0, host='127.0.0.1', no_sidecars=True))
        assert len(instances) == 1
        assert instances[0].socket.fileno() == -1
    finally:
        for server in instances:
            server.server_close()


@contextmanager
def real_controller(tmp_path, monkeypatch, entrypoint=controller.run_controller):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    config.set('settings', 'anki_mapping_file', str(paths['anki_mapping_file']))
    config.set('server', 'controller_port', '0')
    config.set('server', 'api_key', 'owned-lifecycle-token')
    configuration = tmp_path / 'config.ini'
    with configuration.open('w', encoding='utf-8') as stream:
        config.write(stream)
    instances, bound, serving = [], threading.Event(), threading.Event()
    original = controller.ControllerHTTPServer

    class ObservedServer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)
            bound.set()

        def serve_forever(self, *args, **kwargs):
            serving.set()
            return super().serve_forever(*args, **kwargs)

    monkeypatch.setattr(controller, 'ControllerHTTPServer', ObservedServer)
    worker = Worker(lambda: entrypoint(SimpleNamespace(
        config=configuration, host='127.0.0.1', port=0, no_sidecars=True)))
    try:
        assert bound.wait(5)
        server = instances[0]
        port = server.socket.getsockname()[1]
        wait_for_http(f'http://127.0.0.1:{port}/health', worker.thread.is_alive)
        yield server, worker, port
    finally:
        if instances and serving.is_set() and worker.thread.is_alive():
            instances[0].shutdown()
        try:
            worker.result(timeout=10)
        finally:
            for server in instances:
                server.server_close()


def test_shutdown_drains_idle_socket_and_stream_without_removing_other_subscriber(tmp_path, monkeypatch):
    dispatcher = EventDispatcher.get_instance()
    zid = '20261111150001'
    independent = dispatcher.register(zid)
    stream = idle = None
    try:
        with real_controller(tmp_path, monkeypatch) as (server, worker, port):
            stream = HTTPConnection('127.0.0.1', port, timeout=5)
            stream.request('GET', f'/events?zid={zid}', headers={'X-API-Token': server.api_key})
            response = stream.getresponse()
            assert response.status == 200
            assert json.loads(response.fp.readline().decode()[6:])['type'] == 'connected'
            idle = socket.create_connection(('127.0.0.1', port), timeout=5)
            shutdown = HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                shutdown.request('POST', '/api/v1/shutdown', '{}',
                    {'Content-Type': 'application/json', 'X-API-Token': server.api_key})
                closed = shutdown.getresponse()
                assert closed.status == 200
                closed.read()
            finally:
                shutdown.close()
            worker.result(timeout=10)
            assert server.closed_event.is_set()
            assert server.socket.fileno() == -1
            assert not server.workers and not server.connections and not server.streams.handles
            assert server.shutdown_worker is not None and not server.shutdown_worker.is_alive()
            assert server.errors == []
            assert idle.recv(1) == b''
            with dispatcher._lock:
                assert dispatcher._subscribers[zid] == [independent]
            dispatcher.publish(zid, {'type': 'unrelated-still-live'})
            assert independent.get(timeout=1)['type'] == 'unrelated-still-live'
    finally:
        if stream is not None:
            stream.close()
        if idle is not None:
            idle.close()
        dispatcher.unregister(zid, independent)


def test_supervisor_stop_wakes_and_joins_watchdog_without_starting_services(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    supervisor = controller.ProcessSupervisor(config, paths, enabled=False)
    try:
        supervisor._start_monitor()
        assert supervisor._monitor_thread.is_alive()
        supervisor.stop()
        assert not supervisor._monitor_thread.is_alive()
        assert supervisor.job.job_handle is None
    finally:
        supervisor.stop()


def test_owned_child_is_terminated_and_reaped():
    process = subprocess.Popen([sys.executable, '-u', '-c',
        "import sys; print('ready', flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    ready = Worker(process.stdout.readline)
    try:
        assert ready.result(timeout=5).strip() == b'ready'
        stop_owned_process(process)
        assert process.poll() is not None
        stop_owned_process(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        ready.result(timeout=5)


def test_cleanup_attempts_all_resources_and_retains_each_failure():
    visited = []

    def fail(name):
        visited.append(name)
        raise OSError(name)

    errors = run_cleanup([lambda: fail('socket'), lambda: fail('child'), lambda: visited.append('finished')])
    assert visited == ['socket', 'child', 'finished']
    assert [str(error) for error in errors] == ['socket', 'child']


def test_startup_and_cleanup_failures_both_reach_owner():
    visited = []

    def failed_close():
        visited.append('socket')
        raise OSError('close failed')

    resource = SimpleNamespace(server_close=failed_close, finish_shutdown=lambda: visited.append('shutdown'))
    with pytest.raises(ExceptionGroup, match='Controller lifecycle failed') as caught:
        with controller._controller_runtime(resource):
            raise ValueError('startup failed')
    assert visited == ['socket', 'shutdown']
    assert [str(error) for error in caught.value.exceptions] == ['startup failed', 'close failed']


def test_unexpected_http_worker_error_reaches_disposal_owner():
    class FailedHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            raise ValueError('unexpected worker failure')

    server = ControllerHTTPServer(('127.0.0.1', 0), FailedHandler)
    worker = Worker(server.serve_forever)
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        connection.request('GET', '/')
        with pytest.raises(RemoteDisconnected):
            connection.getresponse()
    finally:
        connection.close()
        server.shutdown()
        worker.result(timeout=5)
    with pytest.raises(ExceptionGroup, match='Controller HTTP disposal failed') as caught:
        server.server_close()
    assert len(caught.value.exceptions) == 1
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert str(caught.value.exceptions[0]) == 'unexpected worker failure'
    assert not server.connections and not server.workers and server.socket.fileno() == -1


def test_request_cleanup_failure_reaches_owner_after_socket_close():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

    class FailedCloseServer(ControllerHTTPServer):
        def shutdown_request(self, request):
            super().shutdown_request(request)
            raise OSError('owned request cleanup failed')

    server = FailedCloseServer(('127.0.0.1', 0), Handler)
    worker = Worker(server.serve_forever)
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        connection.request('GET', '/')
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
        server.shutdown()
        worker.result(timeout=5)
    with pytest.raises(ExceptionGroup, match='Controller HTTP disposal failed') as caught:
        server.server_close()
    assert [str(error) for error in caught.value.exceptions] == ['owned request cleanup failed']
    assert not server.connections and not server.workers and server.socket.fileno() == -1


def test_supervisor_invalid_service_input_releases_acquired_job(tmp_path, monkeypatch):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    config.set('services', 'spacy_server_url', 'http://127.0.0.1:invalid')
    jobs = []
    original = controller.WindowsJobObject

    class ObservedJob(original):
        def __init__(self):
            super().__init__()
            jobs.append(self)

    monkeypatch.setattr(controller, 'WindowsJobObject', ObservedJob)
    with pytest.raises(ValueError):
        controller.ProcessSupervisor(config, paths, enabled=False)
    assert len(jobs) == 1 and jobs[0].job_handle is None


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows owned-handle failure contract')
def test_job_close_reports_actual_invalid_owned_handle():
    import ctypes
    job = controller.WindowsJobObject()
    assert job.job_handle is not None
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    assert kernel32.CloseHandle(job.job_handle)
    try:
        with pytest.raises(OSError) as caught:
            job.close()
        assert caught.value.winerror == 6
    finally:
        job.job_handle = None  # The owned kernel handle was already closed above.


def test_unobserved_progressive_setup_failure_reaches_queue_owner(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    mapping = tmp_path / 'invalid-mapping.ini'
    mapping.write_text('not a valid INI mapping', encoding='utf-8')
    paths['anki_mapping_file'] = mapping
    arbiter = controller.SessionArbiter(config, paths, wordfill_cfg={'enabled': False})
    work = arbiter.enrichment_queue
    try:
        result = work.enqueue_progressive_task('20261111152000', arbiter, 'en', 'ru', skip_intellifiller=True)
        assert result['status'] == 'queued'
        work._progressive_executor.shutdown(wait=True)
        assert not work._active_progressive_sessions
        with pytest.raises(ExceptionGroup, match='Controller queue disposal failed') as caught:
            work.shutdown()
        assert any('section headers' in str(error) for error in caught.value.exceptions)
        for executor in (work._executor, work._translation_executor, work._progressive_executor):
            with pytest.raises(RuntimeError, match='cannot schedule new futures after shutdown'):
                executor.submit(lambda: None)
    finally:
        controller.unregister_failover_callback(arbiter._on_global_failover)


def test_missing_progressive_session_finishes_as_explicit_failure(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    arbiter = controller.SessionArbiter(config, paths, wordfill_cfg={'enabled': False})
    zid = '20261111153300'
    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        assert arbiter.enrichment_queue.enqueue_progressive_task(zid, arbiter)['status'] == 'queued'
        arbiter.enrichment_queue.shutdown()
        finished = events.get(timeout=3)
        assert finished['stage'] == 'finished' and finished['status'] == 'failed'
        assert 'not found' in finished['error']['message']
        assert not arbiter.enrichment_queue._active_progressive_sessions
        with pytest.raises(controller.StructuredError, match='not found'):
            controller.get_storage_adapter(config, paths).restore_session(zid)
    finally:
        dispatcher.unregister(zid, events)
        controller.unregister_failover_callback(arbiter._on_global_failover)


def test_peer_reset_is_a_transport_completion_not_a_worker_failure():
    entered = threading.Event()

    class ReadHandler(BaseHTTPRequestHandler):
        def handle(self):
            entered.set()
            super().handle()

    server = ControllerHTTPServer(('127.0.0.1', 0), ReadHandler)
    worker = Worker(server.serve_forever)
    client = socket.create_connection(server.server_address, timeout=3)
    try:
        client.sendall(b'GET / HTTP/1.1\r\n')
        assert entered.wait(3)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('hh', 1, 0))
        client.close()
    finally:
        client.close()
        server.shutdown()
        worker.result(timeout=5)
        server.server_close()
    assert server.errors == []
    assert not server.connections and not server.workers


def test_domain_oserror_is_not_hidden_as_shutdown_cancellation():
    import errno

    class FailedHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.streams.stopping.set()
            raise OSError(errno.EBADF, 'domain failure during shutdown')

    server = ControllerHTTPServer(('127.0.0.1', 0), FailedHandler)
    worker = Worker(server.serve_forever)
    client = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        client.request('GET', '/')
        with pytest.raises(RemoteDisconnected):
            client.getresponse()
    finally:
        client.close()
        server.shutdown()
        worker.result(timeout=5)
    with pytest.raises(ExceptionGroup, match='Controller HTTP disposal failed') as caught:
        server.server_close()
    assert any('domain failure during shutdown' in str(error) for error in caught.value.exceptions)


CHILD_HEALTH_SCRIPT = '''from http.server import HTTPServer, BaseHTTPRequestHandler
import sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(int(sys.argv[2]))
        self.end_headers()
        self.wfile.write(b'{"status":"healthy"}')
    def log_message(self, *args): pass
server = HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler)
print(server.server_port, flush=True)
server.serve_forever()
'''


@contextmanager
def serving_child(status=200):
    process = subprocess.Popen([sys.executable, '-u', '-c', CHILD_HEALTH_SCRIPT, '0', str(status)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    ready = Worker(process.stdout.readline)
    try:
        port = int(ready.result(timeout=5))
        yield process, port
    finally:
        stop_owned_process(process)
        process.stdout.close()
        process.stderr.close()
        ready.result(timeout=5)


def test_healthy_managed_child_keeps_its_owner_and_is_reaped(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    supervisor = controller.ProcessSupervisor(config, paths, enabled=False)
    with serving_child() as (process, port):
        service = controller.SidecarService('owned-health', port, [], tmp_path)
        service.process, service.managed_by_supervisor = process, True
        supervisor.services = {service.name: service}
        try:
            assert supervisor.spawn_service(service)
            assert service.managed_by_supervisor
            supervisor.stop()
            assert process.poll() is not None
        finally:
            supervisor.stop()


def test_healthy_external_child_survives_supervisor_stop(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    supervisor = controller.ProcessSupervisor(config, paths, enabled=False)
    with serving_child() as (process, port):
        service = controller.SidecarService('external-health', port, [], tmp_path)
        supervisor.services = {service.name: service}
        try:
            assert supervisor.spawn_service(service)
            assert not service.managed_by_supervisor
            assert service.process is None
            supervisor.stop()
            assert process.poll() is None
            assert supervisor.probe_health(service)
        finally:
            supervisor.stop()


def test_restarting_managed_child_reaps_both_generations(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    supervisor = controller.ProcessSupervisor(config, paths, enabled=False)
    replacement = tmp_path / 'owned-healthy-child.py'
    replacement.write_text(CHILD_HEALTH_SCRIPT, encoding='utf-8')
    with serving_child(status=503) as (previous, port):
        service = controller.SidecarService('owned-restart', port,
            [sys.executable, str(replacement), str(port), '200'], tmp_path)
        service.process, service.managed_by_supervisor = previous, True
        supervisor.services = {service.name: service}
        try:
            assert supervisor.spawn_service(service)
            assert previous.poll() is not None
            assert service.process is not previous
            assert service.process.poll() is None
            assert service.managed_by_supervisor
            assert supervisor.probe_health(service)
            supervisor.stop()
            assert service.process.poll() is not None
        finally:
            supervisor.stop()


def test_stop_interrupts_owned_child_health_startup(tmp_path):
    from tests.browser_test_server import create_browser_test_server
    entered = threading.Event()

    class UnhealthyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.probes += 1
            self.send_response(503)
            self.end_headers()
            if self.server.probes >= 2:
                entered.set()

    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    supervisor = controller.ProcessSupervisor(config, paths, enabled=False)
    listener = create_browser_test_server(UnhealthyHandler)
    listener.probes = 0
    listener.start()
    child = tmp_path / 'owned-unhealthy-child.py'
    child.write_text('import threading\nthreading.Event().wait()\n', encoding='utf-8')
    service = controller.SidecarService('owned-startup', listener.server_port,
        [sys.executable, str(child)], tmp_path)
    supervisor.services = {service.name: service}
    launcher = Worker(lambda: supervisor.spawn_service(service))
    stopper = None
    try:
        assert entered.wait(5), 'Child did not reach its actual post-launch health probe'
        assert service.process is not None and service.process.poll() is None
        stopper = Worker(supervisor.stop)
        stopper.result(timeout=3)
        assert launcher.result(timeout=3) is False
        assert service.process.poll() is not None
    finally:
        supervisor._monitor_stop.set()
        try:
            launcher.result(timeout=20)
        finally:
            supervisor.stop()
            if stopper is not None:
                stopper.result(timeout=5)
            listener.server_close()
