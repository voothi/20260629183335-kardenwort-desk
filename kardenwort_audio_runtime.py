"""Owned, bounded CLI audio execution. HTTP and UI depend inward on this slice."""
from contextlib import contextmanager
import logging
import math
from pathlib import Path
import os
import re
import select
import signal
import socket
import subprocess
import tempfile
import threading
import time

from kardenwort_audio import AudioError, MAX_BYTES, audio_request, media_type, playback_engine

_SLOTS = threading.BoundedSemaphore(2)
STDERR_TAIL_BYTES = 4096
logger = logging.getLogger('kardenwort.desk.audio')

_SECRET_PATTERN = re.compile(
    r'(?i)(bearer\s+)\S+|((?:api[_-]?key|secret[_-]?token|token|secret|password)\s*[=:]\s*)\S+'
)
_PATH_PATTERN = re.compile(r"[A-Za-z]:[/\\][^\s'\"]+|/(?:home|Users|tmp|var)/[^\s'\"]+")


def capability(paths):
    script, python = paths.get('anki_tts_cli'), paths.get('kardenwort_python')
    if not script or not python:
        raise AudioError('Configured TTS script and Python are required', 'CONFIGURATION_ERROR')
    if not Path(script).is_file() or not Path(python).is_file():
        raise AudioError('Configured TTS capability is unavailable', 'CONFIGURATION_ERROR')
    return str(Path(python).resolve()), str(Path(script).resolve())


def redact_diagnostic(text, user_text=''):
    cleaned = str(text or '')
    if user_text:
        cleaned = cleaned.replace(user_text, '[REDACTED_TEXT]')
    cleaned = _SECRET_PATTERN.sub(lambda m: (m.group(1) or m.group(2)) + '[REDACTED]', cleaned)
    cleaned = _PATH_PATTERN.sub('[REDACTED_PATH]', cleaned)
    return cleaned[-STDERR_TAIL_BYTES:]


class _BoundedStderrDrain:
    """Continuously drains child stderr into a bounded rolling tail buffer to prevent pipe deadlock."""

    def __init__(self, pipe, limit=STDERR_TAIL_BYTES):
        self._pipe = pipe
        self._limit = limit
        self._buf = bytearray()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            while True:
                chunk = self._pipe.read(4096)
                if not chunk:
                    break
                self._append(chunk)
        except OSError:
            pass
        finally:
            self._close_pipe()

    def _append(self, chunk):
        self._buf.extend(chunk)
        excess = len(self._buf) - self._limit
        if excess > 0:
            del self._buf[:excess]

    def _close_pipe(self):
        try:
            self._pipe.close()
        except OSError:
            pass

    def finish(self, timeout=2.0):
        self._thread.join(timeout=timeout)
        return bytes(self._buf).decode('utf-8', errors='replace')


def _descriptor_detached(connection):
    fileno = getattr(connection, 'fileno', None)
    if not callable(fileno):
        return False
    try:
        return fileno() < 0
    except OSError:
        return True


def disconnected(connection):
    if _descriptor_detached(connection):
        return True
    try:
        readable, _, _ = select.select([connection], [], [], 0)
        return bool(readable) and not connection.recv(1, socket.MSG_PEEK)
    except ValueError:
        if _descriptor_detached(connection):
            return True
        raise
    except OSError:
        return True


def _cleanup_owned_process(process, job, drain, user_text, primary_failed):
    try:
        close_process(process, job)
    except Exception as cleanup_error:
        logger.warning('Audio cleanup failure: %s', redact_diagnostic(cleanup_error, user_text))
        if not primary_failed:
            raise
    finally:
        _log_stderr_tail(drain, user_text, primary_failed)


def _log_stderr_tail(drain, user_text, primary_failed):
    if drain is None:
        return
    tail = drain.finish().strip()
    if primary_failed and tail:
        logger.warning('Audio subprocess stderr tail: %s', redact_diagnostic(tail, user_text))


@contextmanager
def process_owner(python, arguments, directory, job_factory, user_text=''):
    options = {'creationflags': 0x08000004} if os.name == 'nt' else {'start_new_session': True}
    job = job_factory() if os.name == 'nt' else None
    process = None
    drain = None
    primary_failed = False
    try:
        process = subprocess.Popen([python, *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=directory, **options)
        drain = _BoundedStderrDrain(process.stderr)
        if job is not None and not job.assign_process(process):
            raise AudioError('Cannot own synthesis process tree')
        # Windows resumes only after job assignment; POSIX creates its session before exec.
        resume_launcher(process)
        yield process
    except BaseException:
        primary_failed = True
        raise
    finally:
        _cleanup_owned_process(process, job, drain, user_text, primary_failed)


def resume_launcher(process):
    if os.name == 'nt':
        from kardenwort_audio_windows import resume_owned_process
        resume_owned_process(process.pid)


def _reap_process(process):
    if process is None:
        return
    if os.name != 'nt':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def close_process(process, job):
    try:
        if job is not None:
            drain_windows_job(job)
    finally:
        _reap_process(process)


def _collect_job_handles(kernel, query, job_handle):
    handles = []
    try:
        return owned_job_handles(kernel, query, job_handle, handles), None
    except OSError as error:
        return handles, error


def drain_windows_job(job):
    """A Windows venv launcher can exit before its interpreter descendants."""
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = [('times', ctypes.c_int64 * 4), ('faults', wintypes.DWORD),
                   ('total', wintypes.DWORD), ('active', wintypes.DWORD), ('terminated', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    terminate = kernel.TerminateJobObject
    terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    query = kernel.QueryInformationJobObject
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    handles = []
    try:
        if not job.job_handle:
            return
        handles, enum_error = _collect_job_handles(kernel, query, job.job_handle)
        if not terminate(job.job_handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        wait_process_handles(kernel, handles)
        if enum_error is not None:
            raise enum_error
        wait_job_empty(query, job.job_handle, Accounting)
    finally:
        close_process_handles(kernel, handles)
        job.close()


def owned_job_handles(kernel, query, job, handles=None):
    import ctypes
    from ctypes import wintypes
    collected = [] if handles is None else handles
    data = ctypes.create_string_buffer(65536)
    if not query(job, 3, data, ctypes.sizeof(data), None):
        raise ctypes.WinError(ctypes.get_last_error())
    count = ctypes.c_uint32.from_buffer(data, 4).value
    pids = (ctypes.c_size_t * count).from_buffer(data, 8)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    for pid in pids:
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            collected.append(handle)
        elif ctypes.get_last_error() != 87:
            raise ctypes.WinError(ctypes.get_last_error())
    return collected


def wait_process_handles(kernel, handles):
    from ctypes import wintypes
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    deadline = time.monotonic() + 5
    for handle in handles:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        if kernel.WaitForSingleObject(handle, remaining) != 0:
            raise AudioError('Audio process did not signal completion')


def close_process_handles(kernel, handles):
    from ctypes import wintypes
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for handle in handles:
        kernel.CloseHandle(handle)


def wait_job_empty(query, handle, accounting):
    import ctypes
    deadline = time.monotonic() + 5
    while True:
        status = accounting()
        if not query(handle, 1, ctypes.byref(status), ctypes.sizeof(status), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not status.active:
            return
        if time.monotonic() >= deadline:
            raise AudioError('Audio process tree did not drain')
        threading.Event().wait(0.005)


def check_limits(connection, deadline, output):
    if disconnected(connection):
        raise AudioError('Audio request cancelled', 'INTERRUPTED')
    if time.monotonic() >= deadline:
        raise AudioError('Audio execution timed out', 'TIMEOUT')
    if output is not None and private_media_size(output.parent) > MAX_BYTES:
        raise AudioError('Audio exceeds byte limit')


def private_media_size(directory):
    try:
        return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())
    except FileNotFoundError:
        # Engines atomically rename their private binary output; resample at
        # the next bounded process poll, and validate the final read as well.
        return 0


def wait_process(process, connection, timeout, output):
    deadline = time.monotonic() + timeout
    while True:
        check_limits(connection, deadline, output)
        try:
            code = process.wait(timeout=0.025)
            break
        except subprocess.TimeoutExpired:
            continue
    if code:
        raise AudioError('Audio process failed', 'DESK_FAILED')


def execution_timeout(config):
    try:
        value = config.getfloat('audio', 'synthesis_timeout', fallback=30) if config else 30.0
    except ValueError as error:
        raise AudioError('Audio timeout must be between 0.05 and 60 seconds', 'CONFIGURATION_ERROR') from error
    if not math.isfinite(value) or not 0.05 <= value <= 60:
        raise AudioError('Audio timeout must be between 0.05 and 60 seconds', 'CONFIGURATION_ERROR')
    return value


@contextmanager
def media_directory():
    owner = tempfile.TemporaryDirectory(prefix='kardenwort-audio-')
    try:
        yield owner.name
    finally:
        release_media(owner)


def release_media(owner):
    # Windows can signal an exited process/job before releasing its CWD handle.
    # Poll that owned OS resource, bounded independently of engine execution.
    deadline = time.monotonic() + 5
    while True:
        try:
            owner.cleanup()
            return
        except PermissionError as error:
            if error.winerror != 32 or time.monotonic() >= deadline:
                raise
            threading.Event().wait(0.005)


def configured_playback_engine(config):
    raw = config.get('audio', 'playback_engine', fallback='anki_tts_cli') if config else 'anki_tts_cli'
    return playback_engine(raw)


def synthesis_arguments(script, text, language, output, stateless):
    arguments = [script]
    if output is not None:
        arguments.extend(['--no-play', '--output', str(output)])
        if stateless:
            arguments.insert(2, '--stateless')
    arguments.extend(['--', text, language])
    return arguments


def _resolve_synthesis_mode(config, synthesize):
    engine = configured_playback_engine(config)
    if synthesize and engine == 'browser_tts':
        raise AudioError('Backend audio synthesis is disabled when playback_engine is browser_tts', 'CONFIGURATION_ERROR')
    return engine == 'stateless_gtts' if synthesize else False


def execute_audio(body, paths, config, connection, job_factory, synthesize=True):
    text, language = audio_request(body)
    if synthesize and '|||' in text:
        raise AudioError('Separate audio must be requested in sequence by the client', 'INVALID_PAYLOAD')
    stateless = _resolve_synthesis_mode(config, synthesize)
    python, script = capability(paths)
    timeout = execution_timeout(config)
    if not _SLOTS.acquire(blocking=False):
        raise AudioError('Audio capacity is busy', 'ROW_BUSY')
    try:
        with media_directory() as directory:
            return execute_owned(python, script, text, language, directory, timeout,
                connection, job_factory, synthesize, stateless)
    finally:
        _SLOTS.release()


def execute_owned(python, script, text, language, directory, timeout, connection, job_factory, synthesize, stateless=False):
    if disconnected(connection):
        raise AudioError('Audio request cancelled', 'INTERRUPTED')
    output = Path(directory) / 'speech.media' if synthesize else None
    arguments = synthesis_arguments(script, text, language, output, stateless)
    with process_owner(python, arguments, directory, job_factory, user_text=text) as process:
        wait_process(process, connection, timeout, output)
    if output is None:
        return {'ok': True, 'status': 'completed', 'playback_confirmed': False,
                'deprecated': True, 'text': text, 'language': language}
    if not output.is_file():
        raise AudioError('Audio process returned no media')
    with output.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    return data, media_type(data)
