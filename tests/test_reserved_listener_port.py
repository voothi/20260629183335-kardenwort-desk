"""The owned listener must not allocate the harness's offline-provider port."""
from http.server import BaseHTTPRequestHandler
from urllib.request import urlopen

from tests import browser_test_server as listeners


def test_offline_provider_port_is_never_bound_by_owned_listener(monkeypatch):
    attempted = []
    original_draw = listeners.random.randrange
    forced = iter([59999])

    def draw(*args):
        return next(forced, None) or original_draw(*args)

    def observed_server(address, handler, **options):
        attempted.append(address[1])
        return listeners._BrowserTestHTTPServer(address, handler, **options)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'owned listener')

    monkeypatch.setattr(listeners.random, 'randrange', draw)
    server = listeners.create_browser_test_server(Handler, server_class=observed_server)
    try:
        assert 59999 not in attempted
        server.start()
        with urlopen('http://127.0.0.1:'+str(server.server_port), timeout=3) as response:
            assert response.status == 200
            assert response.read() == b'owned listener'
    finally:
        server.server_close()
