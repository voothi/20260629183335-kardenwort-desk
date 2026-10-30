"""Bounded test workers and owned SSE transports; errors return to the caller."""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import threading
import time
from urllib.parse import urlparse

from kardenwort_events import EventDispatcher


class Worker:
    def __init__(self, target):
        self.value = None
        self.error = None
        self.thread = threading.Thread(target=self._run, args=(target,))
        self.thread.start()

    def _run(self, target):
        try:
            self.value = target()
        except BaseException as error:
            self.error = error

    def result(self, timeout=5):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "Test worker did not terminate"
        if self.error is not None:
            raise self.error
        return self.value


class SSEEvents:
    def __init__(self):
        self.events = []
        self.changed = threading.Condition()

    def read(self, response):
        while True:
            line = response.readline()
            assert line, "SSE stream closed unexpectedly"
            if line.startswith(b'data: '):
                event = json.loads(line[6:])
                if event.get('type') == 'test_listener_stop':
                    return
                with self.changed:
                    self.events.append(event)
                    self.changed.notify_all()

    def wait(self, predicate, timeout=3):
        with self.changed:
            assert self.changed.wait_for(lambda: predicate(self.events), timeout), "SSE event deadline expired"


@contextmanager
def sse_events(server_url, zid):
    parsed = urlparse(server_url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    stream = SSEEvents()
    worker = None
    response = None
    try:
        connection.request('GET', f'/events?zid={zid}',
                           headers={'X-API-Token': 'test-controller-api-key'})
        response = connection.getresponse()
        assert response.status == 200
        worker = Worker(lambda: stream.read(response))
        stream.wait(lambda events: bool(events) and events[0]['type'] == 'connected')
        yield stream
    finally:
        try:
            if worker is not None:
                EventDispatcher.get_instance().publish(zid, {'type': 'test_listener_stop'})
                worker.result()
        finally:
            connection.close()
            if response is not None:
                response.close()
            drain_sse_subscription(zid)


def drain_sse_subscription(zid):
    dispatcher = EventDispatcher.get_instance()
    deadline = time.monotonic() + 3
    tick = threading.Event()
    while dispatcher.has_subscribers(zid) and time.monotonic() < deadline:
        dispatcher.publish(zid, {'type': 'test_listener_closed'})
        tick.wait(0.01)
    assert not dispatcher.has_subscribers(zid), 'Server retained the closed SSE subscription'
