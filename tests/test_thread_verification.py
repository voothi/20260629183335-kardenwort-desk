"""A correct concurrent result must not hide a failed first worker."""
import subprocess
import sys
from io import BytesIO

import pytest

from tests.thread_support import SSEEvents, Worker


def test_worker_failure_is_raised_by_test_thread():
    def fail():
        raise RuntimeError('unexpected first-worker failure')

    worker = Worker(fail)
    with pytest.raises(RuntimeError, match='unexpected first-worker failure'):
        worker.result()
    assert not worker.thread.is_alive()


def test_unexpected_worker_failure_fails_pytest(tmp_path):
    # Execute an intentionally failing test in a separate pytest process.
    probe = tmp_path / 'test_worker_failure.py'
    probe.write_text('import threading\n'
                     'from unittest.mock import patch\n'
                     'from kardenwort_desk import run_render_flow\n'
                     'from tests.thread_support import Worker\n'
                     'def test_unexpected_worker_failure():\n'
                     '    entered, release = threading.Event(), threading.Event()\n'
                     '    def fail():\n'
                     '        entered.set()\n'
                     '        assert release.wait(3)\n'
                     '        raise RuntimeError("verification failure probe")\n'
                     '    def render(*args, **kwargs):\n'
                     '        return fail()\n'
                     '    def run():\n'
                     '        return run_render_flow("text", "de", "failure-probe", "single", None, {})\n'
                     '    with patch("kardenwort_desk._run_render_flow_impl", side_effect=render):\n'
                     '        first = Worker(run)\n'
                     '        try:\n'
                     '            assert entered.wait(3)\n'
                     '            assert run() == ""\n'
                     '        finally:\n'
                     '            release.set()\n'
                     '            first.result()\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-m', 'pytest', str(probe), '-q',
                             '-p', 'no:cacheprovider',
                             '-W', 'error::pytest.PytestUnhandledThreadExceptionWarning'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'RuntimeError: verification failure probe' in result.stdout
    assert '1 failed' in result.stdout
    assert 'PytestUnhandledThreadExceptionWarning' not in result.stdout


@pytest.mark.parametrize('body', [b'', b'data: invalid-json\n'])
def test_unexpected_sse_read_failure_reaches_test_thread(body):
    stream = SSEEvents()
    reader = Worker(lambda: stream.read(BytesIO(body)))
    with pytest.raises((AssertionError, ValueError)):
        reader.result()
    assert not reader.thread.is_alive()
