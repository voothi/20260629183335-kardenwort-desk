"""Phase 1 contracts for CLI literal arguments, glob/lang mapping, timeouts, noisy stderr, socket detachment, and Windows job termination.

Change: 20261122213502-harden-audio-runtime-tts-cli-and-repo-hygiene (Task 1.2)
Spec: specs/audio-playback/spec.md
"""
from __future__ import annotations

import configparser
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

from kardenwort_audio import AudioError, media_type
from kardenwort_audio_runtime import (
    _SLOTS,
    close_process,
    disconnected,
    drain_windows_job,
    execute_audio,
    execution_timeout,
)
from kardenwort_controller import ControllerRequestHandler, WindowsJobObject
from kardenwort_controller_lifetime import ControllerHTTPServer
from tests.test_audio_cli_concurrency_and_timeout_contracts import (
    OwnedCliEnv,
    SILENCE_MP3,
    _make_tagged_mp3,
    _make_wav,
    gtts_server,  # noqa: F401
    owned_cli,  # noqa: F401
)


@pytest.mark.parametrize("literal_text", ["--clipboard", "-h", "--output-file=evil"])
@pytest.mark.parametrize("stateless", [False, True])
def test_1_2_piper_binds_hyphen_leading_text_literally(owned_cli, tmp_path, literal_text, stateless):
    """Piper must receive hyphen-leading text via --text=<literal> without flag interpretation."""
    owned_cli.write_donor_config(
        {
            "tts_engine": "Piper",
            "piper_enabled": True,
            "gtts_enabled": False,
            "persistent_cache_enabled": False,
        }
    )
    piper_log = tmp_path / "piper_args.jsonl"
    out_file = tmp_path / "piper_literal.wav"
    args = ["--no-play", "--output", str(out_file)]
    if stateless:
        args.append("--stateless")
    args.extend(["--", literal_text, "de"])

    result = owned_cli.run_cli(args, timeout=5.0, extra_env={"TEST_PIPER_RECORD": str(piper_log)})

    assert result.returncode == 0, f"Piper literal binding failed: rc={result.returncode} stderr={result.stderr}"
    assert out_file.is_file()
    assert media_type(out_file.read_bytes()) == "audio/wav"
    records = [json.loads(line) for line in piper_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["text"] == literal_text
    assert records[0]["clipboard"] is False


def test_1_2_language_mapping_exact_precedes_short_code_and_normalizes_hyphens(owned_cli, tmp_path):
    """Exact language mapping must precede short-code normalization on '-'/'_', and Piper receives short code for 'de-DE'."""
    exact_mp3 = _make_tagged_mp3("EXACT-DE-DE")
    short_mp3 = _make_tagged_mp3("SHORT-DE")

    exact_dir = owned_cli.forvo_dir / "German_Exact" / "sp1"
    short_dir = owned_cli.forvo_dir / "German_Short" / "sp1"
    exact_dir.mkdir(parents=True, exist_ok=True)
    short_dir.mkdir(parents=True, exist_ok=True)
    (exact_dir / "haus.mp3").write_bytes(exact_mp3)
    (short_dir / "haus.mp3").write_bytes(short_mp3)

    owned_cli.write_donor_config(
        {
            "audio_dictionary_enabled": True,
            "audio_dictionary_lang_map": {"de-DE": "German_Exact", "de": "German_Short"},
            "audio_dictionary_cycle_enabled": False,
            "tts_engine": "Piper",
            "piper_enabled": True,
            "gtts_enabled": False,
            "persistent_cache_enabled": False,
        }
    )

    out_exact = tmp_path / "exact.mp3"
    r1 = owned_cli.run_cli(["--no-play", "--output", str(out_exact), "--", "Haus", "de-DE"], timeout=5.0)
    assert r1.returncode == 0, r1.stderr
    assert out_exact.read_bytes() == exact_mp3

    # Unmapped hyphenated locale de-AT must normalize '-' -> 'de' and match German_Short.
    out_short = tmp_path / "short.mp3"
    r2 = owned_cli.run_cli(["--no-play", "--output", str(out_short), "--", "Haus", "de-AT"], timeout=5.0)
    assert r2.returncode == 0, f"Hyphen short-code fallback failed: {r2.stderr}"
    assert out_short.read_bytes() == short_mp3

    # Piper with hyphenated language tag must receive short code 'de'.
    piper_log = tmp_path / "piper_lang.jsonl"
    out_piper = tmp_path / "piper_hyphen.wav"
    r3 = owned_cli.run_cli(
        ["--no-play", "--stateless", "--output", str(out_piper), "--", "Wort", "de-DE"],
        timeout=5.0,
        extra_env={"TEST_PIPER_RECORD": str(piper_log)},
    )
    assert r3.returncode == 0, r3.stderr
    records = [json.loads(line) for line in piper_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records[0]["lang"] == "de", f"Expected Piper short lang 'de' for 'de-DE', got {records[0]['lang']!r}"


def test_1_2_audio_dictionary_escapes_literal_glob_components(owned_cli, tmp_path):
    """Literal glob metacharacters in root path, mapped language folder, and filename must be escaped."""
    literal_root = tmp_path / "forvo_[de]"
    decoy_root = tmp_path / "forvo_d"
    target_mp3 = _make_tagged_mp3("LITERAL-BRACKETS")
    decoy_mp3 = _make_tagged_mp3("DECOY-WRONG")

    target_dir = literal_root / "[German]" / "sp1"
    decoy_dir = decoy_root / "G" / "sp1"
    target_dir.mkdir(parents=True, exist_ok=True)
    decoy_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "[haus].mp3").write_bytes(target_mp3)
    (decoy_dir / "h.mp3").write_bytes(decoy_mp3)
    (literal_root / "[German]" / "sp1" / "h.mp3").write_bytes(decoy_mp3)

    owned_cli.write_donor_config(
        {
            "audio_dictionary_enabled": True,
            "audio_dictionary_path": str(literal_root),
            "audio_dictionary_lang_map": {"de": "[German]"},
            "audio_dictionary_cycle_enabled": False,
            "gtts_enabled": False,
            "piper_enabled": False,
            "persistent_cache_enabled": False,
        }
    )

    out_file = tmp_path / "glob_literal.mp3"
    result = owned_cli.run_cli(["--no-play", "--output", str(out_file), "--", "[Haus]", "de"], timeout=5.0)

    assert result.returncode == 0, f"Literal glob component lookup failed: {result.stderr}"
    assert out_file.read_bytes() == target_mp3


@pytest.mark.parametrize("bad_timeout", [0, -1, -0.5, "0", "-5", "nan", "inf", "-inf", "abc", None])
def test_1_2_cli_invalid_gtts_timeout_fails_explicitly_under_single_policy(owned_cli, gtts_server, tmp_path, bad_timeout):
    """Invalid/zero/negative/non-finite gtts_timeout_sec must fail explicitly rather than diverge or coerce."""
    gtts_server.mode = "ok"
    owned_cli.write_donor_config(
        {
            "gtts_timeout_sec": bad_timeout,
            "gtts_enabled": True,
            "piper_enabled": False,
            "persistent_cache_enabled": False,
        }
    )
    out_file = tmp_path / "bad_timeout.media"
    result = owned_cli.run_cli(
        ["--no-play", "--output", str(out_file), "--", "hello", "en"],
        gtts_url=gtts_server.url,
        timeout=5.0,
    )
    assert result.returncode != 0, f"CLI accepted invalid gtts_timeout_sec={bad_timeout!r}"
    assert not out_file.exists()


@pytest.mark.parametrize("bad_timeout", ["0", "-1", "nan", "inf", "-inf", "abc", "61"])
def test_1_2_desk_invalid_synthesis_timeout_raises_configuration_error(bad_timeout):
    """Desk execution_timeout must reject non-finite, non-positive, out-of-range, and non-numeric values as CONFIGURATION_ERROR."""
    cfg = configparser.ConfigParser()
    cfg.read_dict({"audio": {"synthesis_timeout": bad_timeout}})
    with pytest.raises(AudioError) as exc:
        execution_timeout(cfg)
    assert exc.value.code == "CONFIGURATION_ERROR"


def test_1_2_cli_gtts_does_not_mutate_process_global_socket_or_urllib3(owned_cli, gtts_server, tmp_path):
    """Running gTTS synthesis in-process must not mutate socket.setdefaulttimeout or urllib3.util.connection.allowed_gai_family."""
    gtts_server.mode = "ok"
    owned_cli.write_donor_config(
        {
            "gtts_timeout_sec": 2.0,
            "gtts_enabled": True,
            "piper_enabled": False,
            "persistent_cache_enabled": False,
        }
    )
    probe = tmp_path / "probe_globals.py"
    probe.write_text(
        "import importlib.util, json, os, socket, sys\n"
        "import urllib3.util.connection as uconn\n"
        "orig_gai = uconn.allowed_gai_family\n"
        "orig_timeout = socket.getdefaulttimeout()\n"
        f"spec = importlib.util.spec_from_file_location('anki_tts_cli', {str(owned_cli.cli_script)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "conf = mod.load_config()\n"
        f"out = {str(tmp_path / 'g.mp3')!r}\n"
        "ok = mod.run_gtts_with_timeout('hello', 'en', out, conf)\n"
        "print(json.dumps({\n"
        "    'ok': ok,\n"
        "    'timeout_unchanged': socket.getdefaulttimeout() == orig_timeout,\n"
        "    'gai_unchanged': uconn.allowed_gai_family is orig_gai,\n"
        "}))\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(probe)],
        env=owned_cli.make_env(gtts_url=gtts_server.url),
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["timeout_unchanged"] is True, "CLI mutated global socket.setdefaulttimeout()"
    assert payload["gai_unchanged"] is True, "CLI mutated global urllib3.util.connection.allowed_gai_family"


def test_1_2_runtime_drains_multi_megabyte_stderr_without_deadlock_and_redacts_tail(tmp_path, caplog):
    """execute_audio must drain multi-MB child stderr continuously without pipe deadlock, cap/redact diagnostics, and preserve DESK_FAILED."""
    noisy_script = tmp_path / "noisy_fail_cli.py"
    noisy_script.write_text(
        "import sys\n"
        "chunk = ('NOISY_LINE Bearer sk-live-secret-9999 C:\\\\Users\\\\private\\\\file.txt PRIVATE_PAYLOAD_ABC ' * 40 + '\\n')\n"
        "for _ in range(1000):\n"
        "    sys.stderr.write(chunk)\n"
        "sys.stderr.write('FINAL_MARKER_TAIL token=TOP_SECRET_VALUE\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    cfg = configparser.ConfigParser()
    cfg.read_dict({"audio": {"synthesis_timeout": "5", "playback_engine": "stateless_gtts"}})
    paths = {"anki_tts_cli": noisy_script, "kardenwort_python": sys.executable}
    s1, s2 = socket.socketpair()
    caplog.set_level(logging.WARNING)
    try:
        with pytest.raises(AudioError) as exc:
            execute_audio({"text": "PRIVATE_PAYLOAD_ABC", "language": "en"}, paths, cfg, s1, WindowsJobObject)
        assert exc.value.code == "DESK_FAILED"
        audio_logs = [rec.getMessage() for rec in caplog.records if "FINAL_MARKER_TAIL" in rec.getMessage()]
        assert audio_logs, "Expected bounded stderr tail diagnostic to be captured and logged"
        for msg in audio_logs:
            assert len(msg) <= 4500, f"Logged stderr diagnostic was not bounded: {len(msg)} chars"
            assert "sk-live-secret-9999" not in msg, "Bearer secret leaked into stderr diagnostic"
            assert "TOP_SECRET_VALUE" not in msg, "Token secret leaked into stderr diagnostic"
            assert "PRIVATE_PAYLOAD_ABC" not in msg, "User text payload leaked into stderr diagnostic"
            assert "C:\\Users\\private" not in msg, "Local user path leaked into stderr diagnostic"
    finally:
        s1.close()
        s2.close()

    # Verify _SLOTS capacity was released cleanly.
    assert _SLOTS.acquire(blocking=False)
    _SLOTS.release()


def test_1_2_socket_detachment_cancels_before_spawn_and_releases_slot(tmp_path):
    """Pre-detached socket cancels cleanly with INTERRUPTED without spawning child or leaking _SLOTS."""
    server = ControllerHTTPServer(("127.0.0.1", 0), ControllerRequestHandler)
    owned_socket, peer_socket = socket.socketpair()
    sentinel = tmp_path / "should_not_run.txt"
    script = tmp_path / "noop.py"
    script.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n", encoding="utf-8")
    cfg = configparser.ConfigParser()
    cfg.read_dict({"audio": {"synthesis_timeout": "2", "playback_engine": "stateless_gtts"}})
    paths = {"anki_tts_cli": script, "kardenwort_python": sys.executable}
    try:
        server.streams.stopping.set()
        server.cancel_connection(owned_socket)
        assert disconnected(owned_socket) is True
        with pytest.raises(AudioError) as exc:
            execute_audio({"text": "Haus", "language": "de"}, paths, cfg, owned_socket, WindowsJobObject)
        assert exc.value.code == "INTERRUPTED"
        assert not sentinel.exists()
    finally:
        owned_socket.close()
        peer_socket.close()
        server.server_close()
    assert _SLOTS.acquire(blocking=False)
    _SLOTS.release()


class _WinFn:
    """Callable wrapper supporting ctypes-style .argtypes/.restype attributes."""

    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object cleanup contract")
def test_1_2_drain_windows_job_attempts_termination_when_query_fails(monkeypatch):
    """If QueryInformationJobObject fails during enumeration, drain_windows_job MUST still call TerminateJobObject and close job once."""
    calls = {"terminate": 0, "close_job": 0, "close_handles": []}

    class FakeJob:
        def __init__(self):
            self.job_handle = 0x1234

        def close(self):
            calls["close_job"] += 1
            self.job_handle = None

    class FakeKernel:
        def __init__(self):
            self.QueryInformationJobObject = _WinFn(self._query)
            self.TerminateJobObject = _WinFn(self._terminate)
            self.OpenProcess = _WinFn(lambda access, inherit, pid: 0)
            self.WaitForSingleObject = _WinFn(lambda handle, ms: 0)
            self.CloseHandle = _WinFn(self._close)

        def _query(self, handle, info_class, buf, size, ret_len):
            ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED
            return 0

        def _terminate(self, handle, exit_code):
            calls["terminate"] += 1
            return 1

        def _close(self, handle):
            calls["close_handles"].append(handle)
            return 1

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: FakeKernel())
    job = FakeJob()

    with pytest.raises(OSError):
        drain_windows_job(job)

    assert calls["terminate"] == 1, "TerminateJobObject was skipped after QueryInformationJobObject failure"
    assert calls["close_job"] == 1
    assert job.job_handle is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object cleanup contract")
def test_1_2_drain_windows_job_attempts_termination_on_openprocess_access_denied(monkeypatch):
    """ERROR_ACCESS_DENIED (5) in owned_job_handles must NOT be ignored as exited PID and must still attempt TerminateJobObject."""
    calls = {"terminate": 0, "close_job": 0, "closed_handles": [], "opened_pids": []}

    class FakeJob:
        def __init__(self):
            self.job_handle = 0x5678

        def close(self):
            calls["close_job"] += 1
            self.job_handle = None

    class FakeKernel:
        def __init__(self):
            self.QueryInformationJobObject = _WinFn(self._query)
            self.OpenProcess = _WinFn(self._open)
            self.TerminateJobObject = _WinFn(self._terminate)
            self.WaitForSingleObject = _WinFn(lambda handle, ms: 0)
            self.CloseHandle = _WinFn(self._close)

        def _query(self, handle, info_class, buf, size, ret_len):
            if info_class == 3:
                # JobObjectBasicProcessIdList: NumberOfAssignedProcesses=2, NumberOfProcessIdsInList=2, pids=[101, 202]
                ctypes.c_uint32.from_buffer(buf, 0).value = 2
                ctypes.c_uint32.from_buffer(buf, 4).value = 2
                arr = (ctypes.c_size_t * 2).from_buffer(buf, 8)
                arr[0] = 101
                arr[1] = 202
                return 1
            if info_class == 1:
                # ActiveProcesses = 0 after terminate
                return 1
            return 0

        def _open(self, access, inherit, pid):
            calls["opened_pids"].append(pid)
            if pid == 101:
                return 0xAA01
            ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED
            return 0

        def _terminate(self, handle, exit_code):
            calls["terminate"] += 1
            return 1

        def _close(self, handle):
            calls["closed_handles"].append(handle)
            return 1

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: FakeKernel())
    job = FakeJob()

    with pytest.raises(PermissionError) as exc:
        drain_windows_job(job)

    assert getattr(exc.value, "winerror", None) == 5
    assert calls["terminate"] == 1, "TerminateJobObject was skipped after OpenProcess ERROR_ACCESS_DENIED"
    assert calls["closed_handles"] == [0xAA01], f"Expected opened handle 0xAA01 closed exactly once, got {calls['closed_handles']}"
    assert calls["close_job"] == 1
    assert job.job_handle is None


def test_1_2_close_process_retains_primary_failure_when_cleanup_raises(monkeypatch, tmp_path):
    """If child synthesis fails AND job cleanup fails, primary AudioError must not be replaced by cleanup failure."""
    import kardenwort_audio_runtime as runtime

    def failing_drain(_job):
        raise PermissionError(13, "Access is denied", None, 5)

    monkeypatch.setattr(runtime, "drain_windows_job", failing_drain)

    fail_script = tmp_path / "fail_primary.py"
    fail_script.write_text("import sys; sys.exit(9)\n", encoding="utf-8")

    cfg = configparser.ConfigParser()
    cfg.read_dict({"audio": {"synthesis_timeout": "2", "playback_engine": "stateless_gtts"}})
    paths = {"anki_tts_cli": fail_script, "kardenwort_python": sys.executable}
    s1, s2 = socket.socketpair()

    class StubJob:
        job_handle = 1

        def assign_process(self, _proc):
            return True

        def close(self):
            self.job_handle = None

    try:
        with pytest.raises(AudioError) as exc:
            execute_audio({"text": "Haus", "language": "de"}, paths, cfg, s1, StubJob)
        assert exc.value.code == "DESK_FAILED"
    finally:
        s1.close()
        s2.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Genuine 64-bit Windows handle ownership test")
def test_1_2_real_windows_job_object_terminates_owned_process_and_closes_handle():
    """Genuine Windows Job Object must own, terminate, drain, and close a real 64-bit child process handle."""
    job = WindowsJobObject()
    assert job.job_handle
    proc = subprocess.Popen(
        [sys.executable, "-c", "import threading; threading.Event().wait()"],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert job.assign_process(proc) is True
        drain_windows_job(job)
        assert job.job_handle is None
        assert proc.wait(timeout=3) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        job.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named OS mutex abandonment recovery contract")
def test_2_2_abandoned_windows_mutex_recovers_cleanly_without_lock_files(owned_cli, tmp_path):
    """If a process crashes while owning the cycle-state Windows mutex, the next CLI process recovers via WAIT_ABANDONED."""
    for idx in range(2):
        sp_dir = owned_cli.forvo_dir / "de" / f"sp{idx}"
        sp_dir.mkdir(parents=True, exist_ok=True)
        (sp_dir / "crash.mp3").write_bytes(_make_tagged_mp3(f"crash-sp{idx}"))

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

    # Spawn a process that acquires the exact cycle-state named mutex and crashes via os._exit(99) without releasing it.
    crash_script = tmp_path / "abandon_mutex.py"
    crash_script.write_text(
        "import ctypes, hashlib, os\n"
        "from ctypes import wintypes\n"
        f"cache_dir = {str(owned_cli.cache_dir)!r}\n"
        "scope = 'state:' + os.path.normcase(os.path.abspath(cache_dir)) + ':audio_cycle'\n"
        "name = 'Local\\\\KardenwortTts_' + hashlib.sha256(scope.encode('utf-8')).hexdigest()[:24]\n"
        "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]\n"
        "k.CreateMutexW.restype = wintypes.HANDLE\n"
        "k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]\n"
        "k.WaitForSingleObject.restype = wintypes.DWORD\n"
        "h = k.CreateMutexW(None, False, name)\n"
        "assert h and k.WaitForSingleObject(h, 5000) in (0, 0x80)\n"
        "os._exit(99)\n",
        encoding="utf-8",
    )
    # Keep a second handle open in this process so Windows preserves the named mutex object in abandoned state!
    scope = "state:" + os.path.normcase(os.path.abspath(str(owned_cli.cache_dir))) + ":audio_cycle"
    import hashlib as _hl
    mname = "Local\\KardenwortTts_" + _hl.sha256(scope.encode("utf-8")).hexdigest()[:24]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    keeper = kernel.CreateMutexW(None, False, mname)
    assert keeper
    try:
        crashed = subprocess.run([sys.executable, str(crash_script)], timeout=5.0)
        assert crashed.returncode == 99

        out = tmp_path / "recovered.mp3"
        res = owned_cli.run_cli(["--no-play", "--output", str(out), "--", "crash", "de"], timeout=5.0)
        assert res.returncode == 0, f"Failed to recover abandoned mutex: {res.stderr}"
        assert "recovered abandoned" in res.stderr.lower()
        assert out.read_bytes() == _make_tagged_mp3("crash-sp0")
        assert not list(owned_cli.cache_dir.glob("*.lock"))
    finally:
        kernel.CloseHandle(keeper)


def test_2_5_isolated_live_verification_profiles_pin_engines_and_protect_donor_state(owned_cli, tmp_path):
    """Hermetic contract for verification/test_live_reliability.py profile pinning and donor-state protection."""
    from verification.test_live_reliability import build_isolated_cli_profile, donor_state_digest

    owned_cli.write_donor_config(
        {
            "tts_engine": "gTTS",
            "gtts_enabled": True,
            "piper_enabled": True,
            "tts_cycle_enabled": True,
            "audio_dictionary_enabled": True,
            "persistent_cache_enabled": True,
        }
    )
    before = donor_state_digest(owned_cli.cli_script)

    stateless_cli, pinned = build_isolated_cli_profile(
        owned_cli.cli_script, tmp_path / "iso_stateless", mode="stateless", pinned_engine="Piper"
    )
    assert pinned == "Piper"
    cfg_stateless = json.loads((stateless_cli.parent / "config.json").read_text(encoding="utf-8"))
    ov = cfg_stateless["overrides"]
    assert ov["tts_engine"] == "Piper"
    assert ov["piper_enabled"] is True
    assert ov["gtts_enabled"] is False
    assert ov["persistent_cache_enabled"] is False
    assert ov["audio_dictionary_enabled"] is False
    assert ov["tts_cycle_enabled"] is False

    owned_cache = tmp_path / "iso_cache"
    owned_forvo = tmp_path / "iso_forvo"
    default_cli, eff = build_isolated_cli_profile(
        owned_cli.cli_script,
        tmp_path / "iso_default",
        mode="default_pipeline",
        owned_cache_dir=owned_cache,
        owned_forvo_dir=owned_forvo,
    )
    assert eff == "gTTS"
    cfg_default = json.loads((default_cli.parent / "config.json").read_text(encoding="utf-8"))
    assert cfg_default["overrides"]["persistent_cache_path"] == str(owned_cache)
    assert cfg_default["overrides"]["audio_dictionary_path"] == str(owned_forvo)
    assert donor_state_digest(owned_cli.cli_script) == before
