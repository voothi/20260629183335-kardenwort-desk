"""Keep browser test listeners in the high dynamic-port range and reserve them."""
import errno
from http.server import ThreadingHTTPServer
import random


class _BrowserTestHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def create_browser_test_server(handler):
    for attempt in range(128):
        port = random.randrange(49152, 65536)
        try:
            return _BrowserTestHTTPServer(('127.0.0.1', port), handler)
        except OSError as error:
            if error.errno != errno.EADDRINUSE or attempt == 127:
                raise
