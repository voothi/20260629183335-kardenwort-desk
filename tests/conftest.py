import sys
import pytest

@pytest.fixture(autouse=True)
def capture_raw_stdout_in_tests(monkeypatch):
    """
    Redirects sys.__stdout__ to sys.stdout during pytest execution.
    This allows pytest's standard stream capture to catch emit_payload()
    calls, keeping the test runner console output clean and concise.
    """
    monkeypatch.setattr(sys, "__stdout__", sys.stdout)
