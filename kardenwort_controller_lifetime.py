"""Controller-owned HTTP connections, streams, workers and shutdown signaling."""
import errno
from http.server import HTTPServer, ThreadingHTTPServer
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback

from kardenwort_events import EventDispatcher


def run_cleanup(actions):
    errors = []
    for action in actions:
        try:
            action()
        except BaseException as error:
            errors.append(error)
    return errors


def stop_owned_process(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.5)


class ControllerStreams:
    def __init__(self):
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.handles = {}

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
            dispatcher, registered = self.handles.pop(channel)
            if zid != registered:
                raise ValueError('Stream subscription identity changed')
            dispatcher.unregister(zid, channel)

    @staticmethod
    def wake(channel):
        try:
            channel.put_nowait({'type': 'heartbeat'})
        except queue.Full:
            return  # A full channel is already readable.

    def cancel(self):
        with self.lock:
            self.stopping.set()
            for channel in self.handles:
                self.wake(channel)


def expected_close(error):
    return isinstance(error, OSError) and error.errno in {
        errno.EBADF, errno.ENOTCONN, errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE,
        10038, 10053, 10054, 10057, 10058}


def transport_failure(error):
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return False
    frame = frames[-1]
    return (frame.filename.endswith('socket.py') and frame.name == 'readinto'
            or frame.filename.endswith('socketserver.py') and frame.name == 'write')


def expected_peer_disconnect(error):
    return isinstance(error, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)) and transport_failure(error)


class ControllerHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 64
    shutdown_timeout = 5.0

    def __init__(self, *args, **kwargs):
        self.streams = ControllerStreams()
        self.lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.connections, self.workers = set(), set()
        self.errors = []
        self.closed_event = threading.Event()
        self.shutdown_worker = None
        self.shutdown_error = None
        self._last_cache_prune = time.monotonic()
        super().__init__(*args, **kwargs)

    def service_actions(self):
        EventDispatcher.get_instance().prune_polling_clients()
        arbiter = getattr(self, 'arbiter', None)
        now = time.monotonic()
        if arbiter is not None and (now - self._last_cache_prune) >= 30.0:
            self._last_cache_prune = now
            arbiter.prune_idle_caches(now=now)

    def get_request(self):
        connection, address = super().get_request()
        with self.lock:
            self.connections.add(connection)
        return connection, address

    def process_request(self, request, address):
        worker = threading.Thread(target=self.process_request_thread, args=(request, address), daemon=False)
        with self.lock:
            self.workers.add(worker)
            try:
                worker.start()
            except BaseException:
                self.workers.discard(worker)
                raise

    def process_request_thread(self, request, address):
        try:
            self.finish_request(request, address)
        except BaseException as error:
            self.record_error(error)
        finally:
            try:
                self.shutdown_request(request)
            except BaseException as error:
                self.record_error(error)
            finally:
                with self.lock:
                    self.workers.discard(threading.current_thread())

    def record_error(self, error, transport=False):
        if expected_peer_disconnect(error):
            return
        if self.streams.stopping.is_set() and expected_close(error) and (transport or transport_failure(error)):
            return
        with self.lock:
            self.errors.append(error)

    def handle_error(self, request, address):
        self.record_error(sys.exc_info()[1])

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.lock:
                self.connections.discard(request)

    def cancel_connections(self):
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            self.cancel_connection(connection)

    def cancel_connection(self, connection):
        if connection.fileno() < 0:
            return
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError as error:
            self.record_error(error, transport=True)
        # makefile() retains socket references. On Windows, shutdown alone does
        # not wake a timed recv/select; detach prevents a later double close.
        descriptor = connection.detach()
        if descriptor >= 0:
            try:
                socket.close(descriptor)
            except OSError as error:
                self.record_error(error, transport=True)

    def join_workers(self):
        deadline = time.monotonic() + self.shutdown_timeout
        with self.lock:
            workers = list(self.workers)
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in workers):
            error = TimeoutError('Controller HTTP workers did not terminate')
            for worker in workers:
                if worker.is_alive():
                    frame = sys._current_frames().get(worker.ident)
                    error.add_note(worker.name + '\n' + ''.join(traceback.format_stack(frame)))
            raise error

    def server_close(self):
        with self.close_lock:
            if not self.closed_event.is_set():
                self.streams.stopping.set()
                cleanup_errors = run_cleanup([
                    lambda: HTTPServer.server_close(self), self.streams.cancel,
                    self.cancel_connections, self.join_workers])
                with self.lock:
                    self.errors.extend(cleanup_errors)
                self.closed_event.set()
            with self.lock:
                errors = list(self.errors)
                self.errors.clear()
            if errors:
                raise BaseExceptionGroup('Controller HTTP disposal failed', errors)

    def request_shutdown(self):
        with self.lock:
            if self.shutdown_worker is not None:
                return
            self.shutdown_worker = threading.Thread(target=self.shutdown_and_close)
            self.shutdown_worker.start()

    def shutdown_and_close(self):
        try:
            self.shutdown()
            self.server_close()
        except BaseException as error:
            self.shutdown_error = error

    def finish_shutdown(self):
        if self.shutdown_worker is not None:
            self.shutdown_worker.join(self.shutdown_timeout)
            if self.shutdown_worker.is_alive():
                raise TimeoutError('Controller shutdown worker did not terminate')
        if self.shutdown_error is not None:
            raise self.shutdown_error
