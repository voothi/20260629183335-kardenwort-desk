"""Own transport shutdown and surface unexpected HTTP worker exceptions."""
import threading
import time

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.reword_contract_support import RewordWorld, RewordRequestHandler, TOKEN, dispose_world, owned_clients
from tests.test_controller_integration import running_controller
from tests.browser_test_server import create_browser_test_server


@pytest.fixture
def checked_controller(tmp_path, owned_browser_lifetime, page, record_testsuite_property, request):
    lifetime = running_controller.__wrapped__(tmp_path, request)
    value = next(lifetime)
    _, server, _ = value
    server.daemon_threads = False
    expected, unexpected, workers = server.expected_disconnects, server.errors, server.workers
    try:
        yield value
    finally:
        try:
            owned_browser_lifetime.close()
        finally:
            try:
                next(lifetime)
            except StopIteration:
                pass
        record_testsuite_property('expected_idle_peer_disconnects_' + request.node.name, len(expected))
        assert not [worker for worker in workers if worker.is_alive()], 'Owned HTTP worker did not terminate'
        assert unexpected == [], '\n'.join(map(str, unexpected))


@pytest.fixture
def checked_world(tmp_path, monkeypatch, backend, provider_name, request, record_testsuite_property):
    owner = tmp_path / 'world'
    owner.mkdir()
    world = RewordWorld(owner, backend, provider_name)
    module = controller if provider_name == 'google' or backend == 'tsv' else desk
    method = 'translate_lemmas_fast_path' if provider_name == 'google' else 'run_headless_intellifiller'
    provider = world.provider.translate if provider_name == 'google' else world.provider.enrich
    monkeypatch.setattr(module, method, provider)
    clients = owned_clients(request)
    server = create_browser_test_server(RewordRequestHandler)
    server.daemon_threads = False
    server.config, server.resolved_paths, server.goldendict = world.config, world.paths, {}
    server.api_key, server.seq_counter, server.seq_lock = TOKEN, 0, threading.Lock()
    server.start_time, server.arbiter = time.time(), world.arbiter
    server.supervisor = controller.ProcessSupervisor(world.config, world.paths, enabled=False)
    expected, unexpected, workers = server.expected_disconnects, server.errors, server.workers
    world.port = server.server_port
    thread = server.start()
    try:
        yield world
    finally:
        dispose_world(world, server, clients)
        thread.join(timeout=5)
        record_testsuite_property('expected_idle_peer_disconnects_' + request.node.name, len(expected))
        assert not thread.is_alive()
        assert not [worker for worker in workers if worker.is_alive()]
        assert unexpected == [], '\n'.join(map(str, unexpected))
