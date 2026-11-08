"""Real allocation and context-route lifetimes retain visible failures."""
from http.server import BaseHTTPRequestHandler
import errno
import socket
import pytest

from tests import browser_test_server
from tests.test_browser_server_lifetime import controllers
from tests.test_release_boundary_contracts import wait_for_held


def test_context_held_route_is_finished_before_its_page_closes(browser, owned_browser_lifetime, controllers):
    url, _, _ = controllers[0]
    context = browser.new_context()
    page = context.new_page()
    page.goto(url + '/health')
    held = []
    context.route('**/owned-context-held', lambda route: held.append(route))
    page.evaluate("void fetch('/owned-context-held').catch(()=>{})")
    wait_for_held(page, held, 1)
    owned_browser_lifetime.close()
    assert page.is_closed()
    assert context not in browser.contexts
    assert not any(owned_browser_lifetime.pending.values())


def test_existing_wildcard_listener_is_an_allocation_collision(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('0.0.0.0', 0))
        listener.listen(1)
        occupied = listener.getsockname()[1]
        choose = browser_test_server.random.randrange
        choices = []

        def collide_once(start, stop):
            port = occupied if not choices else choose(start, stop)
            choices.append(port)
            return port

        monkeypatch.setattr(browser_test_server.random, 'randrange', collide_once)
        server = browser_test_server.create_browser_test_server(BaseHTTPRequestHandler)
        try:
            assert choices[0] == occupied
            assert len(choices) >= 2
            assert 49152 <= server.server_port < 65536
            assert server.server_port != occupied
        finally:
            server.server_close()


def test_unconfirmed_bind_permission_error_is_not_retried(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temporary:
        temporary.bind(('127.0.0.1', 0))
        unused_port = temporary.getsockname()[1]
    attempts = []
    denied = PermissionError(errno.EACCES, 'unconfirmed bind permission failure')

    def reject(*args, **kwargs):
        attempts.append(args)
        raise denied

    monkeypatch.setattr(browser_test_server.random, 'randrange', lambda *args: unused_port)
    with pytest.raises(PermissionError) as caught:
        browser_test_server.create_browser_test_server(BaseHTTPRequestHandler, server_class=reject)
    assert caught.value is denied
    assert len(attempts) == 1
