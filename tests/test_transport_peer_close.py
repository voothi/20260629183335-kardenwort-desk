"""Actual peer resets terminate responses and release stream subscriptions."""
import socket
import struct
import threading
from http.server import ThreadingHTTPServer

import pytest

from kardenwort_controller import ControllerRequestHandler
from kardenwort_events import EventDispatcher


@pytest.mark.parametrize('response', ['json', 'error', 'stream'])
def test_peer_reset_does_not_retry_response_or_leak_subscription(response):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []

    class Handler(ControllerRequestHandler):
        def _dispatch_route(self, method):
            entered.set()
            assert release.wait(3), 'Client did not release owned response'
            try:
                if response == 'stream':
                    self._handle_sse_events('owned-peer-reset')
                elif response == 'error':
                    self._send_error_json(400, 'INVALID_PAYLOAD', 'owned rejection')
                else:
                    self._send_json(200, {'owned': 'response'})
            finally:
                finished.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.handle_error = lambda request, address: errors.append(address)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
    worker.start()
    client = socket.create_connection(server.server_address, timeout=3)
    try:
        client.sendall(b'GET /owned-reset HTTP/1.1\r\nHost: localhost\r\n\r\n')
        assert entered.wait(3), 'Owned request was not dispatched'
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('hh', 1, 0))
        client.close()
        release.set()
        assert finished.wait(3), 'Disconnected response did not terminate'
    finally:
        client.close()
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(3)
    assert not worker.is_alive()
    assert errors == []
    assert not EventDispatcher.get_instance().has_subscribers('owned-peer-reset')
