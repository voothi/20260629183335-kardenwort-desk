"""Fixed delays cannot replace readiness; owned executor errors reach the parent."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.thread_support import executor_tasks


def test_test_sources_have_no_fixed_sleep_or_browser_wait_calls():
    violations = []
    for path in Path(__file__).parent.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
            if name in {'sleep', 'wait_for_timeout'}:
                violations.append((str(path.relative_to(Path(__file__).parent)), node.lineno, name))
    assert violations == []


def test_executor_owner_reports_all_errors_and_closes_every_pool(monkeypatch):
    pools = [ThreadPoolExecutor(max_workers=1), ThreadPoolExecutor(max_workers=1)]

    def fail(error):
        raise error

    with pytest.raises(ExceptionGroup, match='Owned executor task failures') as caught:
        with executor_tasks(monkeypatch):
            pools[0].submit(fail, ValueError('first executor'))
            pools[1].submit(fail, RuntimeError('second executor'))
    assert [str(error) for error in caught.value.exceptions] == ['first executor', 'second executor']
    assert [type(error) for error in caught.value.exceptions] == [ValueError, RuntimeError]
    for pool in pools:
        with pytest.raises(RuntimeError, match='cannot schedule new futures after shutdown'):
            pool.submit(lambda: None)
