"""Owned, bounded CLI audio execution. HTTP and UI depend inward on this slice."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import select
import signal
import socket
import subprocess
import tempfile
import threading
import time

from kardenwort_audio import AudioError, MAX_BYTES, audio_request, media_type

_SLOTS = threading.BoundedSemaphore(2)
_GATE = "import json,runpy,sys; sys.argv=json.load(sys.stdin); runpy.run_path(sys.argv[0],run_name='__main__')"


def capability(paths):
    script, python = paths.get('anki_tts_cli'), paths.get('kardenwort_python')
    if not script or not python:
        raise AudioError('Configured TTS script and Python are required', 'CONFIGURATION_ERROR')
    if not Path(script).is_file() or not Path(python).is_file():
        raise AudioError('Configured TTS capability is unavailable', 'CONFIGURATION_ERROR')
    return str(Path(python).resolve()), str(Path(script).resolve())


def disconnected(connection):
    try:
        readable, _, _ = select.select([connection], [], [], 0)
        return bool(readable) and not connection.recv(1, socket.MSG_PEEK)
    except OSError:
        return True


@contextmanager
def process_owner(python, arguments, directory, job_factory):
    options = {'creationflags': 0x08000004} if os.name == 'nt' else {'start_new_session': True}
    job = job_factory() if os.name == 'nt' else None
    process = None
    try:
        process = subprocess.Popen([python, '-c', _GATE], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=directory, **options)
        if job is not None and not job.assign_process(process):
            raise AudioError('Cannot own synthesis process tree')
        resume_launcher(process)
        # The child cannot execute the configured capability before ownership succeeds.
        process.stdin.write(json.dumps(arguments).encode('utf-8'))
        process.stdin.close()
        yield process
    finally:
        close_process(process, job)


def resume_launcher(process):
    if os.name == 'nt':
        from kardenwort_audio_windows import resume_owned_process
        resume_owned_process(process.pid)


def close_process(process, job):
    if job is not None:
        drain_windows_job(job)
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
    if process.stdin is not None:
        process.stdin.close()


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
        handles = owned_job_handles(kernel, query, job.job_handle)
        if not terminate(job.job_handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        wait_process_handles(kernel, handles)
        wait_job_empty(query, job.job_handle, Accounting)
    finally:
        close_process_handles(kernel, handles)
        job.close()


def owned_job_handles(kernel, query, job):
    import ctypes
    from ctypes import wintypes
    data = ctypes.create_string_buffer(65536)
    if not query(job,3,data,ctypes.sizeof(data),None):
        raise ctypes.WinError(ctypes.get_last_error())
    count = ctypes.c_uint32.from_buffer(data,4).value
    pids = (ctypes.c_size_t * count).from_buffer(data,8)
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handles = []
    for pid in pids:
        handle = kernel.OpenProcess(0x100000,False,pid)
        if handle:
            handles.append(handle)
        elif ctypes.get_last_error() != 87:
            close_process_handles(kernel,handles)
            raise ctypes.WinError(ctypes.get_last_error())
    return handles


def wait_process_handles(kernel, handles):
    from ctypes import wintypes
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
    deadline = time.monotonic() + 5
    for handle in handles:
        remaining = max(0,int((deadline-time.monotonic())*1000))
        if kernel.WaitForSingleObject(handle,remaining) != 0:
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
    value = config.getfloat('audio', 'synthesis_timeout', fallback=30)
    if not 0.05 <= value <= 60:
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


def execute_audio(body, paths, config, connection, job_factory, synthesize=True):
    text, language = audio_request(body)
    if synthesize and '|||' in text:
        raise AudioError('Separate audio must be requested in sequence by the client', 'INVALID_PAYLOAD')
    python, script = capability(paths)
    timeout = execution_timeout(config)
    if not _SLOTS.acquire(blocking=False):
        raise AudioError('Audio capacity is busy', 'ROW_BUSY')
    try:
        with media_directory() as directory:
            return execute_owned(python, script, text, language, directory, timeout,
                connection, job_factory, synthesize)
    finally:
        _SLOTS.release()


def execute_owned(python, script, text, language, directory, timeout, connection, job_factory, synthesize):
    output = Path(directory) / 'speech.media' if synthesize else None
    arguments = [script]
    if synthesize:
        arguments.extend(['--no-play', '--stateless', '--output', str(output)])
    arguments.extend(['--', text, language])
    with process_owner(python, arguments, directory, job_factory) as process:
        wait_process(process, connection, timeout, output)
    if output is None:
        return {'ok': True, 'status': 'completed', 'playback_confirmed': False,
                'deprecated': True, 'text': text, 'language': language}
    if not output.is_file():
        raise AudioError('Audio process returned no media')
    with output.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    return data, media_type(data)
