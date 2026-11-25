"""Phase 1 owned process/profile/cache tests for gTTS timeout, bounds, media publication, and cycle state.

Change: 20261122213502-harden-audio-runtime-tts-cli-and-repo-hygiene (Task 1.1)
Spec: specs/audio-playback/spec.md
"""
from __future__ import annotations

import base64
import configparser
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave

import pytest

import kardenwort_desk as desk
from kardenwort_audio import media_type
from kardenwort_controller import ControllerRequestHandler
from kardenwort_controller_lifetime import ControllerHTTPServer
from tests.thread_support import Worker

ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = Path(desk.load_config()[1]["anki_tts_cli"]).resolve()
DONOR_ADDON = (CLI_SOURCE.parent / json.loads((CLI_SOURCE.parent / "config.json").read_text(encoding="utf-8"))["anki_addon_path"]).resolve()
DONOR_VENDOR = DONOR_ADDON / "vendor"
SILENCE_MP3 = (ROOT / "tests/fixtures/silence.mp3").read_bytes()


def _make_wav(frames: int = 800, sample: bytes = b"\x01\x00") -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as media:
        media.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        media.writeframes(sample * frames)
    return buf.getvalue()


def _make_tagged_mp3(tag_text: str) -> bytes:
    """Create a valid ID3v2-prefixed MP3 with a distinct ASCII tag payload."""
    payload = tag_text.encode("ascii")
    # Minimal valid ID3v2.3 header with synchsafe size + TXXX-like padding bytes
    size = len(payload)
    synchsafe = bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )
    return b"ID3\x03\x00\x00" + synchsafe + payload + SILENCE_MP3


def _gtts_rpc_body(mp3_bytes: bytes) -> bytes:
    b64 = base64.b64encode(mp3_bytes).decode("ascii")
    return f')]}}\'\n\n[["wrb.fr","jQ1olc","[\\"{b64}\\"]",null,null,null,"generic"]]\n'.encode("utf-8")


def _install_owned_gtts_copy(target_vendor_dir: Path) -> None:
    """Copy real vendored gtts into owned temp vendor dir and route _translate_url to TEST_GTTS_URL when set."""
    dst = target_vendor_dir / "gtts"
    shutil.copytree(DONOR_VENDOR / "gtts", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    utils_file = dst / "utils.py"
    src = utils_file.read_text(encoding="utf-8")
    hook = (
        "\nimport os as _test_os\n"
        "_orig_translate_url = _translate_url\n"
        "def _translate_url(tld='com', path=''):\n"
        "    override = _test_os.environ.get('TEST_GTTS_URL')\n"
        "    if override:\n"
        "        return override\n"
        "    return _orig_translate_url(tld=tld, path=path)\n"
    )
    utils_file.write_text(src + hook, encoding="utf-8")

FAKE_PIPER = """import argparse
import io
import json
import os
import sys
import wave
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Owned fake Piper TTS")
    parser.add_argument("--lang", type=str, required=True)
    parser.add_argument("--text", type=str)
    parser.add_argument("--clipboard", action="store_true")
    parser.add_argument("--output-file", type=str, required=True)
    args = parser.parse_args()

    record_path = os.environ.get("TEST_PIPER_RECORD")
    if record_path:
        with open(record_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"lang": args.lang, "text": args.text, "clipboard": args.clipboard}) + "\\n")

    if os.environ.get("TEST_PIPER_FAIL") == "1":
        sys.stderr.write("Fake Piper forced failure for secret_token=SHOULD_NOT_LEAK\\n")
        sys.exit(2)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as media:
        media.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        media.writeframes(b"\\x01\\x00" * 800)
    Path(args.output_file).write_bytes(buf.getvalue())


if __name__ == "__main__":
    main()
"""


class _ControlledGTTSServer:
    """Real loopback HTTP server simulating ok, hung, trickling, oversized, late, and barrier gTTS responses."""

    def __init__(self, mp3_bytes: bytes = SILENCE_MP3):
        self.mp3_bytes = mp3_bytes
        self.rpc_body = _gtts_rpc_body(mp3_bytes)
        self.mode = "ok"
        self.release = threading.Event()
        self.request_entered = threading.Event()
        self.request_count = 0
        self.bytes_sent_total = 0
        self.barrier: threading.Barrier | None = None
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    self.rfile.read(length)
                with owner.lock:
                    owner.request_count += 1
                owner.request_entered.set()

                if owner.barrier is not None:
                    owner.barrier.wait(timeout=5)

                mode = owner.mode
                if mode == "ok":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(owner.rpc_body)))
                    self.end_headers()
                    self.wfile.write(owner.rpc_body)
                    with owner.lock:
                        owner.bytes_sent_total += len(owner.rpc_body)
                elif mode == "hang":
                    owner.release.wait(15)
                elif mode == "late":
                    # Trickle slowly past timeout so read-inactivity socket timeout does not kill it early,
                    # then deliver valid MP3 bytes once release is set.
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    for _ in range(80):
                        if owner.release.is_set():
                            break
                        try:
                            self.wfile.write(b"\n")
                            self.wfile.flush()
                        except OSError:
                            return
                        owner.release.wait(0.05)
                    try:
                        self.wfile.write(owner.rpc_body)
                        self.wfile.flush()
                    except OSError:
                        pass
                elif mode == "trickle":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    for _ in range(300):
                        if owner.release.is_set():
                            break
                        try:
                            self.wfile.write(b"x\n")
                            self.wfile.flush()
                            with owner.lock:
                                owner.bytes_sent_total += 2
                        except OSError:
                            break
                        owner.release.wait(0.03)
                elif mode == "oversize":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    chunk = b"A" * 65536
                    for _ in range(384):  # 24 MiB total if never aborted
                        if owner.release.is_set():
                            break
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            with owner.lock:
                                owner.bytes_sent_total += len(chunk)
                        except OSError:
                            break

        class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = ThreadedHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/_/TranslateWebserverUi/data/batchexecute"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.release.set()
        self._server.shutdown()
        self._server.server_close()


class OwnedCliEnv:
    """Isolated owned copy of anki-tts-cli.py + owned profile/cache/forvo/piper."""

    def __init__(self, root: Path):
        self.root = root
        self.cli_dir = root / "c"
        self.addon_dir = root / "a"
        self.vendor_dir = self.addon_dir / "vendor"
        self.cache_dir = root / "uc"
        self.forvo_dir = root / "fv"
        self.cli_dir.mkdir(parents=True, exist_ok=True)
        self.vendor_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.forvo_dir.mkdir(parents=True, exist_ok=True)

        self.cli_script = self.cli_dir / "anki-tts-cli.py"
        self.cli_script.write_bytes(CLI_SOURCE.read_bytes())

        self.piper_script = self.cli_dir / "fake_piper.py"
        self.piper_script.write_text(FAKE_PIPER, encoding="utf-8")

        _install_owned_gtts_copy(self.vendor_dir)

        (self.cli_dir / "config.json").write_text(
            json.dumps({"anki_addon_path": str(self.addon_dir), "overrides": {}}),
            encoding="utf-8",
        )
        self.write_donor_config({})

    def write_donor_config(self, overrides: dict):
        base = {
            "audio_dictionary_enabled": False,
            "audio_dictionary_path": str(self.forvo_dir),
            "audio_dictionary_lang_map": {},
            "audio_dictionary_exclusions": [],
            "audio_dictionary_cycle_enabled": False,
            "audio_dictionary_cycle_limit": 2,
            "tts_engine": "gTTS",
            "persistent_cache_enabled": True,
            "persistent_cache_path": str(self.cache_dir),
            "tts_cycle_enabled": False,
            "gtts_enabled": True,
            "gtts_timeout_sec": 0.25,
            "gtts_cache_enabled": True,
            "piper_enabled": True,
            "piper_python_path": sys.executable,
            "piper_script_path": str(self.piper_script),
            "piper_cache_enabled": False,
        }
        base.update(overrides)
        (self.addon_dir / "config.json").write_text(json.dumps(base), encoding="utf-8")

    def make_env(self, gtts_url: str | None = None, extra: dict | None = None) -> dict:
        env = os.environ.copy()
        env.pop("SSLKEYLOGFILE", None)
        env["PYTHONPATH"] = str(self.vendor_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if gtts_url:
            env["TEST_GTTS_URL"] = gtts_url
        if extra:
            env.update(extra)
        return env

    def run_cli(self, args: list[str], *, gtts_url: str | None = None, timeout: float = 5.0, extra_env: dict | None = None):
        cmd = [sys.executable, str(self.cli_script), *args]
        return subprocess.run(
            cmd,
            env=self.make_env(gtts_url=gtts_url, extra=extra_env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )


def _donor_config_sha256() -> str:
    cfg = DONOR_ADDON / "config.json"
    return hashlib.sha256(cfg.read_bytes()).hexdigest() if cfg.is_file() else ""


@pytest.fixture
def owned_cli(tmp_path):
    before_hash = _donor_config_sha256()
    env = OwnedCliEnv(tmp_path)
    yield env
    assert _donor_config_sha256() == before_hash, "Live donor config.json was unexpectedly modified"


@pytest.fixture
def gtts_server():
    server = _ControlledGTTSServer()
    try:
        yield server
    finally:
        server.close()


def test_1_1_hung_gtts_times_out_within_budget_and_piper_fallback_succeeds(owned_cli, gtts_server, tmp_path):
    """When gTTS hangs past gtts_timeout_sec and Piper succeeds, CLI must exit within budget (no non-daemon hang)."""
    gtts_server.mode = "hang"
    owned_cli.write_donor_config({"gtts_timeout_sec": 0.25, "piper_enabled": True, "gtts_enabled": True})
    out_file = tmp_path / "out.media"

    t0 = time.monotonic()
    try:
        result = owned_cli.run_cli(
            ["--no-play", "--output", str(out_file), "--", "hello", "en"],
            gtts_url=gtts_server.url,
            timeout=4.0,
        )
    finally:
        gtts_server.release.set()
    elapsed = time.monotonic() - t0

    assert gtts_server.request_count >= 1, "gTTS HTTP endpoint was never reached"
    assert result.returncode == 0, f"CLI failed: rc={result.returncode} stderr={result.stderr}"
    assert elapsed < 3.5, f"CLI exceeded timeout+fallback budget: {elapsed:.2f}s"
    assert out_file.is_file()
    data = out_file.read_bytes()
    assert media_type(data) == "audio/wav"
    assert data == _make_wav()
    assert not list(owned_cli.cache_dir.glob("*.temp*"))
    assert not list(owned_cli.cache_dir.glob("*.mp3"))


def test_1_1_trickling_gtts_enforces_total_monotonic_deadline_and_falls_back(owned_cli, gtts_server, tmp_path):
    """Trickling bytes below read-inactivity timeout must still trip the total monotonic synthesis deadline."""
    gtts_server.mode = "trickle"
    owned_cli.write_donor_config({"gtts_timeout_sec": 0.25, "piper_enabled": True, "gtts_enabled": True})
    out_file = tmp_path / "trickle.media"

    t0 = time.monotonic()
    try:
        result = owned_cli.run_cli(
            ["--no-play", "--output", str(out_file), "--", "trickle", "en"],
            gtts_url=gtts_server.url,
            timeout=4.0,
        )
    finally:
        gtts_server.release.set()
    elapsed = time.monotonic() - t0

    assert gtts_server.request_count >= 1, "gTTS HTTP endpoint was never reached"
    assert result.returncode == 0, f"CLI failed on trickle fallback: rc={result.returncode} stderr={result.stderr}"
    assert elapsed < 3.5, f"Trickling response bypassed total deadline: {elapsed:.2f}s"
    assert media_type(out_file.read_bytes()) == "audio/wav"
    assert not list(owned_cli.cache_dir.glob("*.temp*"))
    assert not list(owned_cli.cache_dir.glob("*.mp3"))


def test_1_1_oversized_gtts_response_is_rejected_without_unbounded_publication(owned_cli, gtts_server, tmp_path):
    """Oversized gTTS response (>16 MiB) must abort early within memory/byte bounds and never publish files."""
    gtts_server.mode = "oversize"
    owned_cli.write_donor_config({"gtts_timeout_sec": 5.0, "piper_enabled": False, "gtts_enabled": True})
    out_file = tmp_path / "oversize.media"

    try:
        result = owned_cli.run_cli(
            ["--no-play", "--output", str(out_file), "--", "oversize", "en"],
            gtts_url=gtts_server.url,
            timeout=8.0,
        )
    finally:
        gtts_server.release.set()

    assert gtts_server.request_count >= 1, "gTTS HTTP endpoint was never reached"
    assert result.returncode != 0
    assert not out_file.exists()
    assert not list(owned_cli.cache_dir.glob("*.temp*"))
    assert not list(owned_cli.cache_dir.glob("*.mp3"))
    # Must abort before buffering the full 24 MiB stream.
    assert gtts_server.bytes_sent_total < 18 * 1024 * 1024, (
        f"Server streamed {gtts_server.bytes_sent_total} bytes without early client abort"
    )


def test_1_1_repeated_segment_timeouts_bound_workers_and_complete_with_fallback(owned_cli, gtts_server, tmp_path):
    """Repeated gTTS timeouts across multi-segment CLI use must not accumulate workers or hang shutdown."""
    gtts_server.mode = "hang"
    owned_cli.write_donor_config({"gtts_timeout_sec": 0.2, "piper_enabled": True, "gtts_enabled": True})
    out_file = tmp_path / "multi.media"
    piper_log = tmp_path / "piper_calls.jsonl"

    t0 = time.monotonic()
    try:
        result = owned_cli.run_cli(
            ["--no-play", "--output", str(out_file), "--", "one ||| two ||| three ||| four", "en"],
            gtts_url=gtts_server.url,
            timeout=6.0,
            extra_env={"TEST_PIPER_RECORD": str(piper_log)},
        )
    finally:
        gtts_server.release.set()
    elapsed = time.monotonic() - t0

    assert gtts_server.request_count == 4, f"Expected 4 gTTS HTTP attempts, got {gtts_server.request_count}"
    assert result.returncode == 0, f"Multi-segment fallback failed: rc={result.returncode} stderr={result.stderr}"
    assert elapsed < 5.5, f"Multi-segment timeouts took too long: {elapsed:.2f}s"
    calls = [json.loads(line) for line in piper_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [c["text"] for c in calls] == ["one", "two", "three", "four"]
    assert media_type(out_file.read_bytes()) == "audio/wav"
    assert not list(owned_cli.cache_dir.glob("*.temp*"))


def test_1_1_gtts_timeout_and_piper_failure_exits_nonzero_with_redacted_stderr(owned_cli, gtts_server, tmp_path):
    """When both gTTS timeout and Piper fallback fail, CLI exits nonzero and redacts user payload/secrets."""
    gtts_server.mode = "hang"
    owned_cli.write_donor_config({"gtts_timeout_sec": 0.2, "piper_enabled": True, "gtts_enabled": True})
    out_file = tmp_path / "fail.media"
    secret_text = "PRIVATE_USER_SENTENCE_XYZ123"

    try:
        result = owned_cli.run_cli(
            ["--no-play", "--output", str(out_file), "--", secret_text, "en"],
            gtts_url=gtts_server.url,
            timeout=4.0,
            extra_env={"TEST_PIPER_FAIL": "1"},
        )
    finally:
        gtts_server.release.set()

    assert result.returncode != 0
    assert not out_file.exists()
    assert secret_text not in result.stderr
    assert "SHOULD_NOT_LEAK" not in result.stderr
    assert not list(owned_cli.cache_dir.glob("*.temp*"))


def test_1_1_late_gtts_completion_is_fenced_from_cache_and_output(owned_cli, gtts_server, tmp_path):
    """A late gTTS response completing after timeout must never publish late staging/cache/output files."""
    gtts_server.mode = "late"
    owned_cli.write_donor_config({"gtts_timeout_sec": 0.2, "piper_enabled": True, "gtts_enabled": True})
    out_file = tmp_path / "late.media"

    result = owned_cli.run_cli(
        ["--no-play", "--output", str(out_file), "--", "lateword", "en"],
        gtts_url=gtts_server.url,
        timeout=4.0,
    )
    assert gtts_server.request_count >= 1, "gTTS HTTP endpoint was never reached"
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    expected_wav = _make_wav()
    assert out_file.read_bytes() == expected_wav

    # Release the server to deliver late MP3 bytes if any worker were still alive.
    gtts_server.release.set()
    # Allow server handler socket write to settle without arbitrary sleep by closing server cleanly.
    gtts_server.close()

    assert out_file.read_bytes() == expected_wav
    assert not list(owned_cli.cache_dir.glob("*.mp3"))
    assert not list(owned_cli.cache_dir.glob("*.temp*"))


def test_1_1_desk_http_default_engine_recovers_from_hung_gtts_via_piper(owned_cli, gtts_server, tmp_path):
    """Desk /api/v1/audio/synthesize under default anki_tts_cli returns HTTP 200 Piper WAV instead of 504 on gTTS hang."""
    gtts_server.mode = "hang"
    owned_cli.write_donor_config({"gtts_timeout_sec": 0.25, "piper_enabled": True, "gtts_enabled": True})

    launcher = tmp_path / "desk_cli_launcher.py"
    launcher.write_text(
        "import os, runpy, sys\n"
        "os.environ.pop('SSLKEYLOGFILE', None)\n"
        f"os.environ['TEST_GTTS_URL'] = {gtts_server.url!r}\n"
        f"sys.path.insert(0, {str(owned_cli.vendor_dir)!r})\n"
        f"sys.argv[0] = {str(owned_cli.cli_script)!r}\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n",
        encoding="utf-8",
    )

    server = ControllerHTTPServer(("127.0.0.1", 0), ControllerRequestHandler)
    server.api_key = "owned-phase1-token"
    server.config = configparser.ConfigParser()
    server.config.read_dict(
        {
            "audio": {"synthesis_timeout": "5", "playback_engine": "anki_tts_cli"},
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
    worker = Worker(lambda: server.serve_forever(poll_interval=0.02))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/v1/audio/synthesize",
            data=json.dumps({"text": "Haus", "language": "de"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-API-Token": server.api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "audio/wav"
            assert resp.read() == _make_wav()
    finally:
        gtts_server.release.set()
        server.shutdown()
        worker.result(timeout=5)
        server.server_close()


def test_1_1_concurrent_identical_media_publication_is_safe_across_two_processes(owned_cli, gtts_server, tmp_path):
    """Two CLI processes synthesizing the exact same media key concurrently must both publish valid media cleanly."""
    gtts_server.mode = "ok"
    gtts_server.barrier = threading.Barrier(2, timeout=5)
    owned_cli.write_donor_config(
        {
            "gtts_timeout_sec": 4.0,
            "gtts_cache_enabled": True,
            "persistent_cache_enabled": True,
            "piper_enabled": False,
        }
    )
    out1 = tmp_path / "p1.media"
    out2 = tmp_path / "p2.media"
    env = owned_cli.make_env(gtts_url=gtts_server.url)

    p1 = subprocess.Popen(
        [sys.executable, str(owned_cli.cli_script), "--no-play", "--output", str(out1), "--", "sharedword", "de"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        [sys.executable, str(owned_cli.cli_script), "--no-play", "--output", str(out2), "--", "sharedword", "de"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    o1, e1 = p1.communicate(timeout=8)
    o2, e2 = p2.communicate(timeout=8)

    assert p1.returncode == 0, f"p1 failed: {e1} {o1}"
    assert p2.returncode == 0, f"p2 failed: {e2} {o2}"
    assert out1.read_bytes() == SILENCE_MP3
    assert out2.read_bytes() == SILENCE_MP3
    assert media_type(out1.read_bytes()) == "audio/mpeg"
    assert media_type(out2.read_bytes()) == "audio/mpeg"
    assert not list(owned_cli.cache_dir.glob("*.temp*"))


def test_1_1_concurrent_cycle_state_preserves_distinct_keys_without_lost_updates(owned_cli, tmp_path):
    """Two CLI processes updating distinct audio_cycle keys concurrently must lose zero updates."""
    for word in ("alpha", "beta"):
        for idx in range(7):
            sp_dir = owned_cli.forvo_dir / "de" / f"sp{idx}"
            sp_dir.mkdir(parents=True, exist_ok=True)
            (sp_dir / f"{word}.mp3").write_bytes(_make_tagged_mp3(f"{word}-sp{idx}"))

    owned_cli.write_donor_config(
        {
            "audio_dictionary_enabled": True,
            "audio_dictionary_cycle_enabled": True,
            "audio_dictionary_cycle_limit": 7,
            "persistent_cache_enabled": True,
            "gtts_enabled": False,
            "piper_enabled": False,
        }
    )

    iterations = 5
    start_barrier = threading.Barrier(2, timeout=5)
    errors = []

    def worker_loop(word: str):
        try:
            start_barrier.wait()
            for step in range(iterations):
                out = tmp_path / f"{word}_{step}.mp3"
                res = owned_cli.run_cli(["--no-play", "--output", str(out), "--", word, "de"], timeout=8.0)
                if res.returncode != 0:
                    errors.append((word, step, res.returncode, res.stderr))
                    return
        except BaseException as exc:
            errors.append((word, -1, -1, repr(exc)))

    t1 = threading.Thread(target=worker_loop, args=("alpha",))
    t2 = threading.Thread(target=worker_loop, args=("beta",))
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert not t1.is_alive() and not t2.is_alive()
    assert not errors, f"Worker errors: {errors}"

    state_file = owned_cli.cache_dir / "audio_cycle_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state.get("alpha|de") == iterations, f"Lost update on alpha|de: {state}"
    assert state.get("beta|de") == iterations, f"Lost update on beta|de: {state}"
    assert not list(owned_cli.cache_dir.glob("*.lock"))


def test_1_1_concurrent_cycle_state_serializes_same_key_rotation(owned_cli, tmp_path):
    """Four concurrent CLI processes on the same Forvo key must observe 4 distinct serialized speakers."""
    expected_payloads = set()
    for idx in range(4):
        sp_dir = owned_cli.forvo_dir / "de" / f"sp{idx}"
        sp_dir.mkdir(parents=True, exist_ok=True)
        payload = _make_tagged_mp3(f"gamma-sp{idx}")
        (sp_dir / "gamma.mp3").write_bytes(payload)
        expected_payloads.add(payload)

    owned_cli.write_donor_config(
        {
            "audio_dictionary_enabled": True,
            "audio_dictionary_cycle_enabled": True,
            "audio_dictionary_cycle_limit": 4,
            "persistent_cache_enabled": True,
            "gtts_enabled": False,
            "piper_enabled": False,
        }
    )

    env = owned_cli.make_env()
    outputs = [tmp_path / f"same_{idx}.mp3" for idx in range(4)]
    procs = [
        subprocess.Popen(
            [sys.executable, str(owned_cli.cli_script), "--no-play", "--output", str(out), "--", "gamma", "de"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for out in outputs
    ]
    observed = set()
    for idx, proc in enumerate(procs):
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, f"proc {idx} failed: {stderr} {stdout}"
        observed.add(outputs[idx].read_bytes())

    assert observed == expected_payloads, (
        f"Expected 4 distinct serialized speaker rotations, got {len(observed)} unique payloads"
    )
    state = json.loads((owned_cli.cache_dir / "audio_cycle_state.json").read_text(encoding="utf-8"))
    assert state.get("gamma|de") == 0
    assert not list(owned_cli.cache_dir.glob("*.lock"))


def test_1_1_corrupt_cycle_state_is_diagnosed_and_not_silently_reset(owned_cli, tmp_path):
    """Existing corrupt cycle-state JSON must be diagnosed explicitly rather than silently overwritten with {}."""
    for idx in range(2):
        sp_dir = owned_cli.forvo_dir / "de" / f"sp{idx}"
        sp_dir.mkdir(parents=True, exist_ok=True)
        (sp_dir / "corrupt.mp3").write_bytes(_make_tagged_mp3(f"c-sp{idx}"))

    owned_cli.write_donor_config(
        {
            "audio_dictionary_enabled": True,
            "audio_dictionary_cycle_enabled": True,
            "audio_dictionary_cycle_limit": 2,
            "persistent_cache_enabled": True,
            "gtts_enabled": False,
            "piper_enabled": False,
        }
    )

    corrupt_raw = "{corrupt-json-marker"
    state_file = owned_cli.cache_dir / "audio_cycle_state.json"
    state_file.write_text(corrupt_raw, encoding="utf-8")

    out = tmp_path / "corrupt.mp3"
    result = owned_cli.run_cli(["--no-play", "--output", str(out), "--", "corrupt", "de"], timeout=5.0)

    assert result.returncode != 0, "CLI silently succeeded despite corrupt cycle-state JSON"
    assert state_file.read_text(encoding="utf-8") == corrupt_raw, "Corrupt cycle-state JSON was silently overwritten"
    assert result.stderr.strip(), "Expected explicit diagnostic on stderr for corrupt cycle state"
