"""Own real HTTP workers, sockets and exact event-stream subscriptions."""
import errno
import os
from http.server import ThreadingHTTPServer
import queue
import random
import socket
import subprocess
import sys
import threading
from time import monotonic
import traceback

from kardenwort_events import EventDispatcher

CLEANUP_SECONDS = 3.0


class OwnedEventStreams:
    def __init__(self):
        self.handles = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def register(self, zid):
        with self.lock:
            dispatcher = EventDispatcher.get_instance()
            channel = dispatcher.register(zid)
            self.handles[channel] = (dispatcher, zid)
            if self.stopping.is_set():
                self.wake(channel)
            return channel

    def unregister(self, zid, channel):
        with self.lock:
            dispatcher, registered_zid = self.handles.pop(channel)
            assert zid == registered_zid
            dispatcher.unregister(zid, channel)

    @staticmethod
    def wake(channel):
        try:
            channel.put_nowait({'type': 'heartbeat'})
        except queue.Full:
            # A full queue is already readable and needs no extra wake-up.
            return

    def cancel(self):
        with self.lock:
            self.stopping.set()
            for channel in self.handles:
                self.wake(channel)


def owned_handler(handler):
    if hasattr(handler, '_handle_sse_events') and not hasattr(handler, '_event_dispatcher'):
        raise ValueError('Selected SSE runtime lacks the ownership hook; use its frozen historical fixtures')
    class OwnedHandler(handler):
        def _event_dispatcher(self):
            return self.server.streams

    return OwnedHandler


def transport_frame(trace):
    frame = traceback.extract_tb(trace)[-1]
    return (frame.filename.endswith('socket.py') and frame.name == 'readinto'
            or frame.filename.endswith('socketserver.py') and frame.name == 'write')


def idle_peer_close(error, trace):
    if not isinstance(error, ConnectionAbortedError):
        return False
    return any(frame.name == 'handle_one_request' and 'self.rfile.readline' in (frame.line or '')
               for frame in traceback.extract_tb(trace))


def shutdown_transport_error(error, trace):
    codes = {errno.EBADF, errno.ENOTCONN, errno.ECONNRESET, errno.ECONNABORTED,
             errno.EPIPE, 10038, 10053, 10054, 10057, 10058}
    return isinstance(error, OSError) and error.errno in codes and transport_frame(trace)


class _BrowserTestHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = False

    def __init__(self, address, handler, **server_options):
        self.streams = OwnedEventStreams()
        self.connections = set()
        self.cancelled_connections = set()
        self.workers = []
        self.errors = []
        self.closed_event = threading.Event()
        self.expected_disconnects = []
        self.lock = threading.Lock()
        self.serving = threading.Event()
        self.server_thread = None
        self.closed = False
        super().__init__(address, owned_handler(handler), **server_options)

    def get_request(self):
        connection, address = super().get_request()
        with self.lock:
            self.connections.add(connection)
        return connection, address

    def process_request(self, request, address):
        worker = threading.Thread(target=self.process_request_thread, args=(request, address), daemon=False)
        worker.start()
        with self.lock:
            self.workers.append(worker)

    def process_request_thread(self, request, address):
        try:
            self.finish_request(request, address)
        except BaseException:
            self.handle_error(request, address)
        finally:
            self.shutdown_request(request)

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.lock:
                self.connections.discard(request)

    def handle_error(self, request, address):
        _, error, trace = sys.exc_info()
        with self.lock:
            cancelled = request in self.cancelled_connections
            transport_close = cancelled and shutdown_transport_error(error, trace)
            destination = self.expected_disconnects if idle_peer_close(error, trace) or transport_close else self.errors
            destination.append(error)

    def serve_forever(self, poll_interval=.02):
        self.server_thread = threading.current_thread()
        self.serving.set()
        try:
            super().serve_forever(poll_interval=poll_interval)
        except BaseException as error:
            self.errors.append(error)

    def start(self):
        thread = threading.Thread(target=self.serve_forever)
        thread.start()
        assert self.serving.wait(CLEANUP_SECONDS), 'Owned HTTP listener did not start'
        return thread

    def cancel_connection(self, connection):
        with self.lock:
            self.cancelled_connections.add(connection)
        if connection.fileno() < 0:
            return
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError as error:
            if error.errno not in {errno.ENOTCONN, errno.EBADF, errno.ECONNRESET, errno.ECONNABORTED,
                                  10038, 10057, 10058}:
                self.errors.append(error)

    def server_close(self):
        if self.closed:
            return
        self.closed = True
        deadline = monotonic() + CLEANUP_SECONDS
        if self.server_thread is not None:
            self.shutdown()
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            self.cancel_connection(connection)
        self.streams.cancel()
        for worker in self.workers:
            worker.join(max(0, deadline - monotonic()))
        if self.server_thread is not None:
            self.server_thread.join(max(0, deadline - monotonic()))
        super().server_close()
        self.check_closed(deadline)
        self.closed_event.set()

    def check_closed(self, deadline):
        live = [worker.name for worker in self.workers if worker.is_alive()]
        invalid = [not self.resources_stopped(), monotonic() >= deadline]
        if any(invalid):
            failure = AssertionError(f'Owned HTTP cleanup exceeded its contract: workers={live}, '
                f'streams={len(self.streams.handles)}, sockets={len(self.connections)}')
            for worker in self.workers:
                if worker.is_alive():
                    failure.add_note(worker_stack(worker))
            self.errors.append(failure)
        if self.errors:
            raise BaseExceptionGroup('Owned HTTP worker/disposal failures', self.errors)

    def resources_stopped(self):
        active = [self.connections, self.streams.handles, any(worker.is_alive() for worker in self.workers),
                  self.server_thread is not None and self.server_thread.is_alive()]
        return not any(active)


def worker_stack(worker):
    frame = sys._current_frames().get(worker.ident)
    stack = ''.join(traceback.format_stack(frame)) if frame is not None else 'frame unavailable'
    return worker.name + '\n' + stack


def occupied_windows_port(error, port):
    if os.name != 'nt' or error.errno not in {errno.EACCES, 10013}:
        return False
    try:
        result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                                text=True, timeout=1, check=True)
    except subprocess.SubprocessError as probe:
        error.add_note(f'Occupied-port inspection failed: {type(probe).__name__}')
        return False
    endpoints = {f'0.0.0.0:{port}', f'127.0.0.1:{port}', f'[::]:{port}', f'[::1]:{port}'}
    return any(occupied_endpoint(fields, endpoints)
               for fields in (line.split() for line in result.stdout.splitlines()))


def occupied_endpoint(fields, endpoints):
    return len(fields) >= 4 and fields[0] == 'TCP' and fields[1] in endpoints


def allocation_collision(error, port):
    return error.errno == errno.EADDRINUSE or occupied_windows_port(error, port)


def create_browser_test_server(handler, server_class=_BrowserTestHTTPServer, **server_options):
    for attempt in range(128):
        port = random.randrange(49152, 65536)
        try:
            return server_class(('127.0.0.1', port), handler, **server_options)
        except OSError as error:
            if attempt == 127 or not allocation_collision(error, port):
                error.add_note(f'Owned test listener bind: 127.0.0.1:{port}; attempt {attempt + 1}')
                raise
