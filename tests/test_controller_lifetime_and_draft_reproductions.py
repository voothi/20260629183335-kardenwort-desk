"""Phase 1 reproductions for draft claim/retry/cancel races, Win32 handle ownership,
IPv6 URL formatting, expected response timeouts, and primary DB error preservation.
"""
import ctypes
import ctypes.wintypes
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler
import json
import sqlite3
import sys
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import kardenwort_controller as controller
from kardenwort_controller_lifetime import ControllerHTTPServer
from kardenwort_db import KardenwortDB
from kardenwort_state_runtime import preference_store
from tests.controller_test_support import controller_lifetime, owned_configuration
from tests.thread_support import Worker


TOKEN = "test-controller-api-key"


def _post_json(url, path, body, token=TOKEN):
    req = Request(
        url + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Token": token},
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=10)
    except HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(raw) if raw else {}


def test_draft_confirm_transient_render_failure_is_retryable_without_replaying_language_effects(tmp_path, monkeypatch):
    """Transient render failure during /api/v1/confirm-language must release claim so retry succeeds,
    without duplicating already-committed language preference / AHK side effects.
    """
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999), backend="sqlite") as (url, server):
        server.config.set("settings", "default_language", "de")
        store = preference_store(server.config, server.resolved_paths)
        assert store.get("default_language") == "de"

        host_calls = []
        render_attempts = {"count": 0}
        monkeypatch.setattr(controller, "spawn_ahk", lambda *args, **kwargs: host_calls.append(args))
        monkeypatch.setattr(controller.webbrowser, "open_new_tab", lambda *args, **kwargs: True)

        def flaky_render(*args, **kwargs):
            render_attempts["count"] += 1
            if render_attempts["count"] == 1:
                raise RuntimeError("transient render outage")
            return "<html><body>ok</body></html>", []

        monkeypatch.setattr(controller, "run_render_flow", flaky_render)

        zid = "20261123052001"
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS[zid] = {"text": "Bonjour.", "language": "de", "text_mode": "single"}
        try:
            st1, b1 = _post_json(
                url, "/api/v1/confirm-language",
                {"session_zid": zid, "action": "process", "chosen_source_lang": "fr"},
            )
            assert st1 == 500, b1
            assert store.get("default_language") == "fr"
            assert len(host_calls) == 1

            # Retry must succeed (not 404 NOT_FOUND!) and must not replay language/AHK effects.
            st2, b2 = _post_json(
                url, "/api/v1/confirm-language",
                {"session_zid": zid, "action": "process", "chosen_source_lang": "fr"},
            )
            assert st2 == 200, f"Transient render failure lost retryable draft: {st2} {b2}"
            assert b2["data"]["language"] == "fr"
            assert len(host_calls) == 1
        finally:
            with controller._DRAFT_SESSIONS_LOCK:
                controller._DRAFT_SESSIONS.pop(zid, None)


def test_draft_confirm_failure_cannot_resurrect_cancelled_or_replaced_draft(tmp_path, monkeypatch):
    """Concurrent cancel or replacement draft arriving during in-flight confirm-language must not
    be resurrected or clobbered when the first confirm fails.
    """
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999), backend="sqlite") as (url, server):
        server.config.set("settings", "default_language", "de")
        monkeypatch.setattr(controller, "spawn_ahk", lambda *args, **kwargs: None)
        monkeypatch.setattr(controller.webbrowser, "open_new_tab", lambda *args, **kwargs: True)

        entered = threading.Event()
        release = threading.Event()

        def failing_set_language(*args, **kwargs):
            entered.set()
            assert release.wait(5), "Confirm race was not released"
            raise sqlite3.OperationalError("simulated transient failure")

        monkeypatch.setattr(controller, "set_language", failing_set_language)

        # Case A: Concurrent cancel while confirm is in flight.
        zid_cancel = "20261123052002"
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS[zid_cancel] = {"text": "First.", "language": "de", "text_mode": "single"}
        try:
            w_confirm = Worker(lambda: _post_json(
                url, "/api/v1/confirm-language",
                {"session_zid": zid_cancel, "action": "process", "chosen_source_lang": "fr"},
            ))
            assert entered.wait(5)
            st_cancel, _ = _post_json(url, "/api/v1/confirm-language", {"session_zid": zid_cancel, "action": "cancel"})
            assert st_cancel == 200
            release.set()
            st_fail, _ = w_confirm.result(timeout=5)
            assert st_fail == 500
            with controller._DRAFT_SESSIONS_LOCK:
                assert zid_cancel not in controller._DRAFT_SESSIONS, "Failed confirm resurrected a cancelled draft"
        finally:
            release.set()
            with controller._DRAFT_SESSIONS_LOCK:
                controller._DRAFT_SESSIONS.pop(zid_cancel, None)

        # Case B: Newer replacement draft arrives while confirm is in flight.
        entered.clear()
        release.clear()
        zid_replace = "20261123052003"
        old_draft = {"text": "Old draft.", "language": "de", "text_mode": "single"}
        new_draft = {"text": "Newer draft.", "language": "en", "text_mode": "single"}
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS[zid_replace] = old_draft
        try:
            w_confirm2 = Worker(lambda: _post_json(
                url, "/api/v1/confirm-language",
                {"session_zid": zid_replace, "action": "process", "chosen_source_lang": "fr"},
            ))
            assert entered.wait(5)
            with controller._DRAFT_SESSIONS_LOCK:
                controller._DRAFT_SESSIONS[zid_replace] = new_draft
            release.set()
            st_fail2, _ = w_confirm2.result(timeout=5)
            assert st_fail2 == 500
            with controller._DRAFT_SESSIONS_LOCK:
                assert controller._DRAFT_SESSIONS.get(zid_replace) == new_draft, (
                    "Failed confirm clobbered a newer replacement draft"
                )
        finally:
            release.set()
            with controller._DRAFT_SESSIONS_LOCK:
                controller._DRAFT_SESSIONS.pop(zid_replace, None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Win32 handle ownership contract")
def test_win32_job_object_types_signatures_and_closes_owned_handles_only(monkeypatch):
    """WindowsJobObject must configure typed HANDLE/BOOL/DWORD signatures, close owned OpenProcess
    handles on success and failure, never close borrowed Popen._handle, and close job_handle on init failure.
    """
    closed_handles = []
    opened_handles = [0x100000010, 0x100000020]
    assign_results = [0, 1]  # First fallback assign fails, second succeeds
    set_info_should_raise = {"flag": False}

    class FakeWinFunc:
        def __init__(self, impl):
            self.impl = impl
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.impl(*args)

    class ObservedKernel32:
        def __init__(self):
            self.CreateJobObjectW = FakeWinFunc(lambda _a, _b: 0x200000001)
            self.SetInformationJobObject = FakeWinFunc(self._set_info)
            self.OpenProcess = FakeWinFunc(lambda _acc, _inh, _pid: opened_handles.pop(0))
            self.AssignProcessToJobObject = FakeWinFunc(lambda _jh, _ph: assign_results.pop(0))
            self.CloseHandle = FakeWinFunc(self._close_handle)

        def _set_info(self, _jh, _cls, _info, _sz):
            if set_info_should_raise["flag"]:
                raise OSError("SetInformationJobObject failed")
            return 1

        def _close_handle(self, h):
            closed_handles.append(h)
            return 1

    fake_k32 = ObservedKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: fake_k32)

    job = controller.WindowsJobObject()
    try:
        # 1. All Win32 API calls must have typed argtypes and restype (HANDLE/BOOL/DWORD).
        assert fake_k32.CreateJobObjectW.restype in (ctypes.wintypes.HANDLE, ctypes.c_void_p)
        assert fake_k32.SetInformationJobObject.argtypes is not None
        assert fake_k32.OpenProcess.restype in (ctypes.wintypes.HANDLE, ctypes.c_void_p)
        assert fake_k32.AssignProcessToJobObject.argtypes is not None

        # 2. Fallback OpenProcess handle must be closed once on AssignProcessToJobObject failure.
        proc_fail = SimpleNamespace(pid=4242)
        assert job.assign_process(proc_fail) is False
        assert closed_handles == [0x100000010], f"Leaked owned OpenProcess handle on assignment failure: {closed_handles}"

        # 3. Fallback OpenProcess handle must also be closed once on success.
        proc_ok = SimpleNamespace(pid=4243)
        assert job.assign_process(proc_ok) is True
        assert closed_handles == [0x100000010, 0x100000020]

        # 4. Borrowed Popen._handle must NEVER be closed by assign_process.
        assign_results.append(1)
        proc_borrowed = SimpleNamespace(pid=4244, _handle=0x300000099)
        assert job.assign_process(proc_borrowed) is True
        assert 0x300000099 not in closed_handles
    finally:
        job.close()

    # 5. Init exception after CreateJobObjectW must close the created job handle.
    closed_handles.clear()
    set_info_should_raise["flag"] = True
    failed_job = controller.WindowsJobObject()
    assert failed_job.job_handle is None
    assert 0x200000001 in closed_handles, "Leaked job_handle when SetInformationJobObject raised"


def test_ipv6_url_formatting_brackets_once_and_preserves_hostnames(tmp_path, monkeypatch):
    """Advertised URLs built from raw/bracketed IPv6 literals must bracket once and preserve hostnames."""
    assert controller.SidecarService("spacy", 8081, [], host="::1").url() == "http://[::1]:8081"
    assert controller.SidecarService("spacy", 8081, [], host="[::1]").url() == "http://[::1]:8081"
    assert controller.SidecarService("spacy", 8081, [], host="127.0.0.1").url() == "http://127.0.0.1:8081"
    assert controller.SidecarService("spacy", 8081, [], host="localhost").url() == "http://localhost:8081"

    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999), backend="sqlite") as (url, server):
        opened_urls = []
        monkeypatch.setattr(controller.webbrowser, "open_new_tab", lambda u: opened_urls.append(u) or True)
        orig_addr = server.server_address
        try:
            server.server_address = ("::1", server.server_port)
            st, body = _post_json(url, "/api/v1/spawn-tabs", {"urls": ["/session/render?session_zid=20261123052010"]})
            assert st == 200, body
            assert body["data"]["urls"][0].startswith(f"http://[::1]:{server.server_port}/")

            server.server_address = ("[::1]", server.server_port)
            st2, body2 = _post_json(url, "/api/v1/spawn-tabs", {"urls": ["/session/render?session_zid=20261123052010"]})
            assert st2 == 200, body2
            assert body2["data"]["urls"][0].startswith(f"http://[::1]:{server.server_port}/")
        finally:
            server.server_address = orig_addr


def test_expected_response_timeout_does_not_fail_server_disposal():
    """Expected socket TimeoutError during response write closes cleanly without failing server_close()."""
    handled = threading.Event()
    close_flag = {"closed": False}

    class TimeoutWriteHandler(controller.ControllerRequestHandler):
        def _dispatch_route(self, method):
            def timeout_write(_data):
                raise TimeoutError("simulated client write timeout")
            self.wfile.write = timeout_write
            try:
                self._send_json(200, {"ok": True})
                close_flag["closed"] = bool(self.close_connection)
            finally:
                handled.set()

    server = ControllerHTTPServer(("127.0.0.1", 0), TimeoutWriteHandler)
    worker = Worker(server.serve_forever)
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request("GET", "/health")
        try:
            conn.getresponse().read()
        except Exception:
            pass
        assert handled.wait(5)
    finally:
        conn.close()
        server.shutdown()
        worker.result(timeout=5)
    # Must dispose cleanly with zero unexpected worker errors and close_connection set.
    server.server_close()
    assert server.errors == []
    assert close_flag["closed"] is True


def test_kardenwort_connection_preserves_primary_error_when_close_fails(tmp_path):
    """If a primary DB error occurs inside a connection context and close() also fails,
    the primary persistence error must be preserved rather than masked by the close failure.
    """
    config, paths, _ = owned_configuration(tmp_path, "", "sqlite")
    db = KardenwortDB(config=config, resolved_paths=paths)
    db.run_migrations()

    conn = db.get_connection()
    real_close = conn.close

    def failing_close():
        real_close()
        raise sqlite3.ProgrammingError("secondary close failure")

    conn.close = failing_close
    with pytest.raises(sqlite3.OperationalError, match="primary write failure"):
        with conn:
            raise sqlite3.OperationalError("primary write failure")
