"""Owned unmanaged enrichment engine and transparent scheduling observations."""
import concurrent.futures
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
import threading

from tests.browser_test_server import create_browser_test_server


class EnrichmentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == '/enrich'
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.changed:
            self.server.requests.append(payload)
            self.server.active += 1
            self.server.maximum_active = max(self.server.maximum_active, self.server.active)
            self.server.changed.notify_all()
        try:
            assert self.server.release.wait(5), 'Enrichment engine was not released'
            rows = self.server.response(payload['rows'])
            body = json.dumps(dict(status='success', enriched_rows=rows)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with self.server.changed:
                self.server.active -= 1
                self.server.changed.notify_all()

    def log_message(self, *args):
        pass


@contextmanager
def enrichment_engine(config, response=None):
    server = create_browser_test_server(EnrichmentHandler)
    server.requests = []
    server.changed = threading.Condition()
    server.release = threading.Event()
    server.active = server.maximum_active = 0
    server.response = response or enriched_rows
    config.set('services', 'intellifiller_server_url', f'http://127.0.0.1:{server.server_port}')
    try:
        server.start()
        yield server
    finally:
        server.release.set()
        server.server_close()


def enriched_rows(rows):
    return [dict(row_id=row['row_id'], WordDestination='trans_' + row['WordSource'],
                 WordSourceIPA='/ipa_' + row['WordSource'] + '/',
                 WordSourceMorphologyAI='morph_' + row['WordSource']) for row in rows]


class FutureWaiters:
    """Observe real Future waits; delegate every outcome to the original primitive.

    Caller registration excludes HTTP/executor infrastructure. This controls only
    the scheduling rendezvous, without substituting queue/domain/provider results.
    """
    def __init__(self, monkeypatch):
        self.changed = threading.Condition()
        self.callers = set()
        self.waiters = set()
        original = concurrent.futures.Future.result

        def observed(future, *args, **kwargs):
            identity = threading.get_ident()
            with self.changed:
                if identity in self.callers:
                    self.waiters.add(identity)
                    self.changed.notify_all()
            return original(future, *args, **kwargs)

        monkeypatch.setattr(concurrent.futures.Future, 'result', observed)

    def call(self, target):
        with self.changed:
            self.callers.add(threading.get_ident())
        return target()

    def wait(self, count):
        with self.changed:
            assert self.changed.wait_for(lambda: len(self.waiters) == count, 5), 'Caller wait deadline expired'
