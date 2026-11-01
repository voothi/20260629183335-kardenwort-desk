"""Own transport shutdown and surface unexpected HTTP worker exceptions."""
import sys
import threading
import time
import traceback
from http.server import ThreadingHTTPServer

import pytest

from kardenwort_events import EventDispatcher
import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.reword_contract_support import RewordWorld, RewordRequestHandler, TOKEN
from tests.test_controller_integration import running_controller


def observe_workers(server):
    expected, unexpected, workers = [], [], []
    process = server.process_request_thread

    def observe_worker(*args):
        workers.append(threading.current_thread())
        return process(*args)

    def observe_error(request, address):
        kind, error, trace = sys.exc_info()
        frames = traceback.extract_tb(trace)
        awaiting_request = any(frame.name == 'handle_one_request' and
            'self.raw_requestline = self.rfile.readline' in (frame.line or '') for frame in frames)
        if isinstance(error, ConnectionAbortedError) and awaiting_request:
            expected.append('Peer closed idle HTTP connection before a request line')
        else:
            unexpected.append(''.join(traceback.format_exception(kind, error, trace)))

    # Observe the real transport boundary; no request dispatch or domain result
    # is replaced. Idle peer-close is distinct from a failure while responding.
    server.handle_error = observe_error
    server.process_request_thread = observe_worker
    return expected, unexpected, workers


@pytest.fixture
def checked_controller(tmp_path, page, record_testsuite_property, request):
    lifetime = running_controller.__wrapped__(tmp_path)
    value = next(lifetime)
    _, server, _ = value
    server.daemon_threads = False
    expected, unexpected, workers = observe_workers(server)
    try:
        yield value
    finally:
        page.close()
        dispatcher = EventDispatcher.get_instance()
        for zid in list(dispatcher._subscribers):
            dispatcher.publish(zid, {'type': 'heartbeat'})
        try:
            next(lifetime)
        except StopIteration:
            pass
        record_testsuite_property('expected_idle_peer_disconnects_' + request.node.name, len(expected))
        assert not [worker for worker in workers if worker.is_alive()], 'Owned HTTP worker did not terminate'
        assert unexpected == [], '\n'.join(unexpected)


@pytest.fixture
def checked_world(tmp_path, monkeypatch, backend, provider_name, request, record_testsuite_property):
    owner = tmp_path / 'world'
    owner.mkdir()
    world = RewordWorld(owner, backend, provider_name)
    module = controller if provider_name == 'google' or backend == 'tsv' else desk
    method = 'translate_lemmas_fast_path' if provider_name == 'google' else 'run_headless_intellifiller'
    provider = world.provider.translate if provider_name == 'google' else world.provider.enrich
    monkeypatch.setattr(module, method, provider)
    server = ThreadingHTTPServer(('127.0.0.1', 0), RewordRequestHandler)
    server.daemon_threads = False
    server.config, server.resolved_paths, server.goldendict = world.config, world.paths, {}
    server.api_key, server.seq_counter, server.seq_lock = TOKEN, 0, threading.Lock()
    server.start_time, server.arbiter = time.time(), world.arbiter
    server.supervisor = controller.ProcessSupervisor(world.config, world.paths, enabled=False)
    expected, unexpected, workers = observe_workers(server)
    world.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
    thread.start()
    try:
        yield world
    finally:
        if 'page' in request.fixturenames:
            request.getfixturevalue('page').close()
        for zid, channel in world.subscriptions:
            world.arbiter.unregister_subscriber(zid, channel)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        world.arbiter.enrichment_queue.shutdown(wait=True)
        record_testsuite_property('expected_idle_peer_disconnects_' + request.node.name, len(expected))
        assert not thread.is_alive()
        assert not [worker for worker in workers if worker.is_alive()]
        assert unexpected == [], '\n'.join(unexpected)
