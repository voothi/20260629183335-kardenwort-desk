"""Browser listeners stay allocated and tolerate a concurrent port collision."""
import socket
from http.server import BaseHTTPRequestHandler

import pytest

from tests import browser_test_server


def test_browser_server_reserves_high_port_and_retries_collision(monkeypatch):
    first = browser_test_server.create_browser_test_server(BaseHTTPRequestHandler)
    second = None
    choose = browser_test_server.random.randrange
    choices = []

    def collide_once(start, stop):
        port = first.server_port if not choices else choose(start, stop)
        choices.append(port)
        return port

    monkeypatch.setattr(browser_test_server.random, 'randrange', collide_once)
    try:
        second = browser_test_server.create_browser_test_server(BaseHTTPRequestHandler)
        assert choices[0] == first.server_port
        assert len(choices) >= 2
        assert 49152 <= first.server_port <= 65535
        assert 49152 <= second.server_port <= 65535
        assert first.server_port != second.server_port
        with socket.socket() as contender:
            with pytest.raises(OSError):
                contender.bind(second.server_address)
    finally:
        first.server_close()
        if second is not None:
            second.server_close()
