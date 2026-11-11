"""Real transport disposal preserves failures and independent subscriptions."""
from http.client import HTTPConnection, RemoteDisconnected
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler
import json
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

from kardenwort_events import EventDispatcher
from tests.browser_test_server import create_browser_test_server
from tests import browser_test_server
from tests.test_controller_integration import running_controller
from tests.test_release_boundary_contracts import wait_for_held

ZID = '20261107230052'


def test_idle_controller_connection_timeout_logs_before_request_path_exists(caplog):
    from kardenwort_controller import ControllerRequestHandler
    finished = threading.Event()

    class IdleHandler(ControllerRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(0.01)

        def finish(self):
            try:
                super().finish()
            finally:
                finished.set()

    server = create_browser_test_server(IdleHandler)
    try:
        server.start()
        with caplog.at_level('INFO', logger='kardenwort.desk.controller'):
            with socket.create_connection(server.server_address, timeout=3) as client:
                assert finished.wait(3), 'Idle connection did not reach its deadline'
                assert client.recv(1) == b''
    finally:
        server.server_close()
    assert 'Request timed out:' in caplog.text
    assert not server.errors
    assert_drained(server)


@pytest.fixture
def controllers(tmp_path):
    with ExitStack() as lifetimes:
        owners = []
        for name in ('first', 'second'):
            owner = tmp_path / name
            owner.mkdir()
            lifetime = running_controller.__wrapped__(owner, None)
            owners.append(next(lifetime))
            lifetimes.callback(next, lifetime, None)
        yield owners


def open_stream(server):
    client = HTTPConnection(*server.server_address, timeout=3)
    client.request('GET', '/events?zid=' + ZID, headers={'X-API-Token': 'test-controller-api-key'})
    response = client.getresponse()
    assert response.status == 200
    assert json.loads(response.readline()[6:])['type'] == 'connected'
    assert response.readline() == b'\n'
    return client, response


def assert_drained(server):
    assert not server.streams.handles
    assert not server.connections
    assert not any(worker.is_alive() for worker in server.workers)
    assert not server.server_thread.is_alive()
    assert not any(worker.daemon for worker in server.workers)


def test_disposing_one_server_preserves_same_session_http_and_sse(controllers):
    (_, first, _), (url, second, _) = controllers
    left, left_stream = open_stream(first)
    right, right_stream = open_stream(second)
    try:
        dispatcher = EventDispatcher.get_instance()
        assert len(dispatcher._subscribers[ZID]) == 2
        first.server_close()
        assert_drained(first)
        assert len(dispatcher._subscribers[ZID]) == 1
        dispatcher.publish(ZID, {'stage': 'finished', 'value': 'second-still-live'})
        event = json.loads(right_stream.readline()[6:])
        assert event['value'] == 'second-still-live'
        assert right_stream.readline() == b'\n'
        client = HTTPConnection(*second.server_address, timeout=3)
        try:
            client.request('GET', '/health')
            assert client.getresponse().status == 200
        finally:
            client.close()
        second.server_close()
        assert_drained(second)
        assert not dispatcher.has_subscribers(ZID)
    finally:
        left.close()
        right.close()
        left_stream.close()
        right_stream.close()


@pytest.mark.parametrize('failure', [None, AssertionError, KeyboardInterrupt], ids=['success', 'assertion', 'interrupt'])
def test_open_stream_cleanup_is_bounded_on_body_completion(controllers, failure):
    _, server, _ = controllers[0]
    client, response = open_stream(server)
    start = time.monotonic()
    try:
        try:
            if failure is not None:
                raise failure('owned body completion')
        finally:
            server.server_close()
    except BaseException as error:
        assert failure is not None and isinstance(error, failure)
    finally:
        client.close()
        response.close()
    assert time.monotonic() - start < 1
    assert_drained(server)


class FailingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.entered.set()
        if self.path == '/after-cancellation':
            assert self.server.streams.stopping.wait(3), 'Owned cancellation did not arrive'
        raise self.server.failure('original owned HTTP failure')


@pytest.mark.parametrize('path', ['/before-cancellation', '/after-cancellation'])
@pytest.mark.parametrize('failure', [RuntimeError, OSError])
def test_worker_error_is_preserved_before_or_during_cancellation(path, failure):
    server = create_browser_test_server(FailingHandler)
    server.entered, server.failure = threading.Event(), failure
    server.start()
    client = HTTPConnection(*server.server_address, timeout=3)
    try:
        client.request('GET', path)
        assert server.entered.wait(3)
        if path == '/before-cancellation':
            with pytest.raises(RemoteDisconnected):
                client.getresponse()
        with pytest.raises(ExceptionGroup, match='Owned HTTP worker') as caught:
            server.server_close()
        assert any(isinstance(error, failure) and str(error) == 'original owned HTTP failure'
                   for error in caught.value.exceptions), repr(caught.value.exceptions)
        assert_drained(server)
    finally:
        client.close()
        server.server_close()


def test_unexpected_owned_server_worker_fails_pytest(tmp_path):
    probe = tmp_path / 'test_owned_worker_failure.py'
    probe.write_text('''from http.client import HTTPConnection, RemoteDisconnected
from http.server import BaseHTTPRequestHandler
import pytest
from tests.browser_test_server import create_browser_test_server
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raise RuntimeError('owned worker failure probe')
def test_failure():
    server = create_browser_test_server(Handler)
    server.start()
    client = HTTPConnection(*server.server_address, timeout=3)
    try:
        client.request('GET', '/')
        with pytest.raises(RemoteDisconnected):
            client.getresponse()
    finally:
        client.close()
        server.server_close()
''', encoding='utf-8')
    result = subprocess.run([sys.executable, '-m', 'pytest', str(probe), '-q', '-p', 'no:cacheprovider',
        '--confcutdir', str(tmp_path), '--rootdir', str(tmp_path),
        '-W', 'error::pytest.PytestUnhandledThreadExceptionWarning'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'RuntimeError: owned worker failure probe' in result.stdout
    assert '1 failed' in result.stdout
    assert 'PytestUnhandledThreadExceptionWarning' not in result.stdout


@pytest.mark.parametrize('failure', [None, AssertionError, KeyboardInterrupt], ids=['success', 'assertion', 'interrupt'])
def test_held_browser_route_finishes_before_transport_shutdown(owned_browser_lifetime, page, controllers, failure):
    url, server, _ = controllers[0]
    held = []
    page.goto(url + '/health')
    page.route('**/owned-held', lambda route: held.append(route))
    page.evaluate("void fetch('/owned-held').catch(()=>{})")
    wait_for_held(page, held, 1)
    try:
        try:
            if failure is not None:
                raise failure('owned browser body completion')
        finally:
            owned_browser_lifetime.close()
    except BaseException as error:
        assert failure is not None and isinstance(error, failure)
    assert page.is_closed()
    assert not any(owned_browser_lifetime.pending.values())
    server.server_close()
    assert_drained(server)


def test_owned_routes_preserve_explicit_handler_unregistration(owned_browser_lifetime, page, controllers):
    url, _, _ = controllers[0]
    page.goto(url + '/health')

    def response(route):
        route.fulfill(status=201, body='owned route')

    page.route('**/owned-response', response)
    assert page.evaluate("fetch('/owned-response').then(r=>r.status)") == 201
    page.unroute('**/owned-response', response)
    assert page.evaluate("fetch('/owned-response').then(r=>r.status)") == 404


def test_reaching_emergency_deadline_fails_and_releases_listener(monkeypatch):
    server = create_browser_test_server(BaseHTTPRequestHandler)
    clock = iter([0, 3])
    monkeypatch.setattr(browser_test_server, 'monotonic', lambda: next(clock))
    with pytest.raises(ExceptionGroup, match='Owned HTTP worker/disposal') as caught:
        server.server_close()
    assert any('cleanup exceeded' in str(error) for error in caught.value.exceptions)
    with pytest.raises(OSError):
        socket.create_connection(server.server_address, timeout=.2)
    assert server.resources_stopped()


def test_historical_sse_handler_without_ownership_hook_fails_before_serving():
    class HistoricalHandler(BaseHTTPRequestHandler):
        def _handle_sse_events(self, zid):
            raise AssertionError('Unsupported historical handler must not execute')

    with pytest.raises(ValueError, match='frozen historical fixtures'):
        create_browser_test_server(HistoricalHandler)


def test_owned_shutdown_tolerates_real_peer_reset():
    entered, release = threading.Event(), threading.Event()

    class ResetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            assert release.wait(3)
            self.send_response(200)
            self.end_headers()

    server = create_browser_test_server(ResetHandler)
    server.start()
    client = socket.create_connection(server.server_address, timeout=3)
    try:
        client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        assert entered.wait(3)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('hh', 1, 0))
        client.close()
        with server.lock:
            connection = next(iter(server.connections))
        server.cancel_connection(connection)
        release.set()
        server.server_close()
        assert_drained(server)
        assert server.errors == []
    finally:
        client.close()
        release.set()
        server.server_close()
