"""Acceptance tests for handling audio socket detachment during controller disposal.

Spec: specs/audio-playback/spec.md
Requirement: Handle socket detachment during audio cancellation
"""
import ast
import configparser
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import select
import socket
import sys
import tempfile
import urllib.error
import urllib.request

import pytest

from kardenwort_audio import AudioError
from kardenwort_audio_runtime import disconnected, execute_audio
from kardenwort_controller import ControllerRequestHandler, ErrorCode, StructuredError, WindowsJobObject
from kardenwort_controller_lifetime import ControllerHTTPServer
from tests.audio_test_support import CLI, ENGINE, pcm
from tests.test_audio_synthesis import assert_owned_process_exited, owned_process_witness
from tests.test_intent_architecture import complexity, functions
from tests.thread_support import Worker

ROOT = Path(__file__).resolve().parents[1]


def _audio_http_request(server, body, path="/api/v1/audio/synthesize", token="owned-audio-token"):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Token": token},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=5)


@contextmanager
def real_audio_controller(tmp_path, handler_cls=ControllerRequestHandler, synthesis_timeout="5"):
    """Real ControllerHTTPServer + relocated audio CLI; only unmanaged gTTS is controlled."""
    private_media = Path(tempfile.gettempdir())
    previous_media = set(private_media.glob("kardenwort-audio-*"))

    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "relocated speech cli.py"
    script.write_bytes(CLI.read_bytes())
    addon = tmp_path / "addon"
    (addon / "vendor").mkdir(parents=True)
    (addon / "vendor" / "gtts.py").write_text(ENGINE, encoding="utf-8")
    (addon / "vendor" / "silence.mp3").write_bytes((ROOT / "tests/fixtures/silence.mp3").read_bytes())
    (tmp_path / "config.json").write_text(json.dumps({"anki_addon_path": str(addon)}), encoding="utf-8")
    (addon / "config.json").write_text(
        json.dumps(
            {
                "tts_engine": "gTTS",
                "tts_cycle_enabled": True,
                "audio_dictionary_cycle_enabled": True,
                "persistent_cache_enabled": True,
                "persistent_cache_path": str(tmp_path / "forbidden-cache"),
            }
        ),
        encoding="utf-8",
    )

    signal_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    signal_socket.bind(("127.0.0.1", 0))
    signal_socket.settimeout(5)

    launcher = tmp_path / "owned_cli.py"
    launcher.write_text(
        "import sys, runpy, os\n"
        f"os.environ['AUDIO_TEST_SIGNAL_PORT'] = {str(signal_socket.getsockname()[1])!r}\n"
        f"sys.path.insert(0, {str(addon / 'vendor')!r})\n"
        f"sys.argv[0] = {str(script)!r}\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n",
        encoding="utf-8",
    )

    server = ControllerHTTPServer(("127.0.0.1", 0), handler_cls)
    server.api_key = "owned-audio-token"
    server.config = configparser.ConfigParser()
    server.config.read_dict(
        {
            "audio": {"synthesis_timeout": str(synthesis_timeout), "playback_engine": "stateless_gtts"},
            "storage": {"backend": "sqlite", "sqlite_db_path": str(tmp_path / "audio.db")},
        }
    )
    server.resolved_paths = {
        "anki_tts_cli": launcher,
        "kardenwort_python": sys.executable,
        "sqlite_db_path": tmp_path / "audio.db",
        "storage_backend": "sqlite",
        "base_dir": tmp_path,
    }
    server.audio_signal = signal_socket
    worker = Worker(lambda: server.serve_forever(poll_interval=0.02))
    try:
        yield server, worker
    finally:
        try:
            server.shutdown()
            worker.result(timeout=5)
        finally:
            try:
                server.server_close()
            finally:
                signal_socket.close()
        assert not (tmp_path / "forbidden-cache").exists()
        assert set(private_media.glob("kardenwort-audio-*")) == previous_media


def test_detached_connection_probe_reports_disconnected():
    """Scenario: Detached connection (direct probe after ControllerHTTPServer.cancel_connection).

    When controller disposal detaches an owned socket before a disconnect probe,
    disconnected() must return True instead of raising ValueError on fd == -1.
    """
    server = ControllerHTTPServer(("127.0.0.1", 0), ControllerRequestHandler)
    owned_socket, peer_socket = socket.socketpair()
    try:
        with server.lock:
            server.connections.add(owned_socket)
        server.streams.stopping.set()
        server.cancel_connection(owned_socket)

        assert owned_socket.fileno() == -1
        assert disconnected(owned_socket) is True
        # Repeated disposal on an already-detached socket must remain idempotent.
        server.cancel_connection(owned_socket)
        assert server.errors == []
    finally:
        with server.lock:
            server.connections.discard(owned_socket)
        owned_socket.close()
        peer_socket.close()
        server.server_close()


def test_controller_disposal_detaches_audio_socket_and_cancels_owned_synthesis(tmp_path, caplog):
    """Scenario: Detached connection (real audio request / controller disposal interleaving).

    When controller disposal detaches an active owned audio request socket while
    synthesis is running, the audio probe must report disconnected (INTERRUPTED),
    cancel/reap the owned synthesis process, and avoid unhandled 500 ValueError crashes.
    """
    outcomes = []

    class ObservingAudioHandler(ControllerRequestHandler):
        def _audio_request(self, path):
            try:
                return super()._audio_request(path)
            except BaseException as exc:
                outcomes.append(exc)
                raise

    caplog.set_level(logging.ERROR, logger="kardenwort.desk.controller")
    client = None
    witness = None

    with real_audio_controller(tmp_path, handler_cls=ObservingAudioHandler, synthesis_timeout="10") as (server, worker):
        try:
            client = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
            body = json.dumps({"text": "hang", "language": "en"}).encode("utf-8")
            headers = (
                "POST /api/v1/audio/synthesize HTTP/1.0\r\n"
                f"X-API-Token: {server.api_key}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "\r\n"
            )
            client.sendall(headers.encode("ascii") + body)

            # Deterministic rendezvous: wait until real child synthesis process reports its PID.
            pid = int(server.audio_signal.recv(100))
            witness = owned_process_witness(pid)

            # Trigger real controller disposal while synthesis is actively running.
            server.shutdown()
            worker.result(timeout=5)
            server.server_close()
            # Repeated server_close must remain idempotent.
            server.server_close()

            assert_owned_process_exited(witness)
            witness = None

            assert server.errors == []
            assert not server.connections
            assert not server.workers

            assert len(outcomes) == 1
            assert isinstance(outcomes[0], StructuredError), (
                f"Expected StructuredError(INTERRUPTED), got {type(outcomes[0]).__name__}: {outcomes[0]}"
            )
            assert outcomes[0].error_code == ErrorCode.INTERRUPTED
            assert not any("Unhandled Controller error" in rec.getMessage() for rec in caplog.records), (
                [rec.getMessage() for rec in caplog.records]
            )
        finally:
            if client is not None:
                client.close()
            if witness is not None and sys.platform == "win32":
                kernel, handle, _ = witness
                kernel.CloseHandle(handle)

    # Confirm synthesis capacity (_SLOTS) was released after detached-socket cancellation.
    with real_audio_controller(tmp_path / "post-cancel") as (next_server, _next_worker):
        with _audio_http_request(next_server, {"text": "Haus", "language": "de"}) as response:
            assert response.status == 200
            assert response.read() == pcm()


def test_ordinary_audio_request_on_controller_http_server_is_unchanged(tmp_path):
    """Scenario: Ordinary audio request (valid adjacent control).

    When the client connection remains valid on ControllerHTTPServer, valid
    synthesis and response behavior are unchanged.
    """
    with real_audio_controller(tmp_path) as (server, _worker):
        with _audio_http_request(server, {"text": "Haus", "language": "de"}) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "audio/wav"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.read() == pcm()
    assert not list(tmp_path.rglob("speech.media"))


def test_unrelated_audio_validation_and_engine_failures_remain_visible(tmp_path):
    """Scenario: Unrelated failure (valid adjacent control).

    When synthesis or validation fails for a non-transport reason on ControllerHTTPServer,
    the real failure remains visible and is not classified as cancellation (INTERRUPTED).
    """
    with real_audio_controller(tmp_path) as (server, _worker):
        cases = [
            ({"text": "a ||| b", "language": "en"}, 400, "INVALID_PAYLOAD"),
            ({"text": "failure", "language": "en"}, 500, "DESK_FAILED"),
            ({"text": "invalid", "language": "en"}, 500, "SERVER_ERROR"),
        ]
        for body, expected_status, expected_code in cases:
            with pytest.raises(urllib.error.HTTPError) as caught:
                _audio_http_request(server, body)
            assert caught.value.code == expected_status
            payload = json.load(caught.value)
            assert payload["status"] == "error"
            assert payload["error_code"] == expected_code
            assert payload["error_code"] != "INTERRUPTED"


def test_detached_connection_during_select_toctou_window_reports_disconnected(monkeypatch):
    """If controller disposal detaches the socket between pre-check and select(), probe still returns True."""
    server = ControllerHTTPServer(("127.0.0.1", 0), ControllerRequestHandler)
    owned_socket, peer_socket = socket.socketpair()
    original_select = select.select
    detached_inside_window = []

    def detach_before_select(rlist, wlist, xlist, timeout=None):
        if owned_socket in rlist and not detached_inside_window:
            detached_inside_window.append(True)
            server.streams.stopping.set()
            server.cancel_connection(owned_socket)
        return original_select(rlist, wlist, xlist, timeout)

    monkeypatch.setattr(select, "select", detach_before_select)
    try:
        assert owned_socket.fileno() >= 0
        assert disconnected(owned_socket) is True
        assert detached_inside_window == [True]
        assert owned_socket.fileno() == -1
        assert server.errors == []
    finally:
        owned_socket.close()
        peer_socket.close()
        server.server_close()


def test_unrelated_value_error_on_live_socket_is_not_masked_as_disconnected(monkeypatch):
    """A ValueError raised while the socket descriptor is still valid must propagate unmasked."""
    owned_socket, peer_socket = socket.socketpair()

    def raise_unrelated_value_error(*_args, **_kwargs):
        raise ValueError("unrelated select validation error")

    monkeypatch.setattr(select, "select", raise_unrelated_value_error)
    try:
        assert owned_socket.fileno() >= 0
        with pytest.raises(ValueError, match="unrelated select validation error"):
            disconnected(owned_socket)
    finally:
        owned_socket.close()
        peer_socket.close()


def test_pre_detached_connection_cancels_without_spawning_synthesis_process(tmp_path):
    """If controller disposal already detached the socket before spawn, execute_audio cancels cleanly without spawning."""
    server = ControllerHTTPServer(("127.0.0.1", 0), ControllerRequestHandler)
    owned_socket, peer_socket = socket.socketpair()
    sentinel = tmp_path / "spawned-sentinel.txt"
    script = tmp_path / "probe_cli.py"
    script.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n", encoding="utf-8")
    config = configparser.ConfigParser()
    config.read_dict({"audio": {"synthesis_timeout": "2", "playback_engine": "stateless_gtts"}})
    paths = {"anki_tts_cli": script, "kardenwort_python": sys.executable}
    try:
        server.streams.stopping.set()
        server.cancel_connection(owned_socket)
        assert owned_socket.fileno() == -1

        with pytest.raises(AudioError) as caught:
            execute_audio({"text": "Haus", "language": "de"}, paths, config, owned_socket, WindowsJobObject)
        assert caught.value.code == "INTERRUPTED"
        assert not sentinel.exists()
        assert server.errors == []
    finally:
        owned_socket.close()
        peer_socket.close()
        server.server_close()


def test_cancel_connection_is_idempotent_without_stopping_flag():
    """Calling cancel_connection repeatedly on an owned socket records no errors and remains idempotent."""
    server = ControllerHTTPServer(("127.0.0.1", 0), ControllerRequestHandler)
    owned_socket, peer_socket = socket.socketpair()
    try:
        with server.lock:
            server.connections.add(owned_socket)
        server.cancel_connection(owned_socket)
        assert owned_socket.fileno() == -1
        server.cancel_connection(owned_socket)
        assert server.errors == []
    finally:
        with server.lock:
            server.connections.discard(owned_socket)
        owned_socket.close()
        peer_socket.close()
        server.server_close()


def test_modified_audio_and_lifetime_functions_meet_complexity_ceiling():
    """All modified audio-runtime and controller-lifetime functions must stay within CC <= 7."""
    targets = {
        "kardenwort_audio_runtime.py": {"_descriptor_detached", "disconnected", "execute_owned"},
        "kardenwort_controller_lifetime.py": {"cancel_connection"},
    }
    for filename, expected_names in targets.items():
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        measured = {node.name: complexity(node) for node in functions(tree) if node.name in expected_names}
        assert set(measured) == expected_names
        assert max(measured.values()) <= 7, (filename, measured)

