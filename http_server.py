"""
Compatibility shim for Kardenwort Desk HTTP Server.
Delegates all request dispatching and lifecycle execution to kardenwort_controller.
"""
import sys
import os
import logging
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# Import canonical implementations from kardenwort_controller and kardenwort_desk
from kardenwort_controller import (
    ControllerRequestHandler,
    run_controller,
    generate_server_zid,
    ProcessSupervisor,
    SessionArbiter,
    _DRAFT_SESSIONS,
    _DRAFT_SESSIONS_LOCK,
    ERROR_STATUS_MATRIX,
)
from kardenwort_desk import (
    load_config,
    core_lookup,
    core_export,
    core_edit_save,
    StructuredError,
    ErrorCode,
    generate_unique_zid,
    find_working_tsv,
    verify_language,
    render_verify_language_html,
    get_storage_adapter,
    SEC_SETTINGS,
    persist_default_language,
    spawn_ahk,
    get_bundled_css,
)

from pathlib import Path

logger = logging.getLogger("kardenwort.desk.http_server")

# Canonical static asset directory and sub-routes (/js/*, /css/*)
STATIC_WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_JS_DIR = STATIC_WEB_DIR / "js"
STATIC_CSS_DIR = STATIC_WEB_DIR / "css"

import queue
import threading
from typing import Dict, List, Any, Optional

class EventDispatcher:
    """
    Thread-safe Server-Sent Events (SSE) dispatcher for session-scoped in-memory streams.
    Manages subscriber queues without requiring disk-based update files.
    """
    _instance: Optional["EventDispatcher"] = None
    _singleton_lock = threading.Lock()

    def __init__(self):
        self._subscribers: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EventDispatcher":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = EventDispatcher()
            return cls._instance

    def register(self, session_zid: str) -> queue.Queue:
        q = queue.Queue(maxsize=1000)
        with self._lock:
            if session_zid not in self._subscribers:
                self._subscribers[session_zid] = []
            self._subscribers[session_zid].append(q)
        return q

    def unregister(self, session_zid: str, q: queue.Queue):
        with self._lock:
            if session_zid in self._subscribers:
                try:
                    self._subscribers[session_zid].remove(q)
                    if not self._subscribers[session_zid]:
                        del self._subscribers[session_zid]
                except ValueError:
                    pass

    def has_subscribers(self, session_zid: str) -> bool:
        with self._lock:
            return bool(self._subscribers.get(session_zid))

    def publish(self, session_zid: str, event_data: Dict[str, Any]):
        payload = dict(event_data)
        from datetime import datetime, timezone
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload["session_zid"] = session_zid
        with self._lock:
            queues = list(self._subscribers.get(session_zid, []))
        for q in queues:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

# Backward-compatibility alias
APIRequestHandler = ControllerRequestHandler


def run_render_flow(*args, **kwargs):
    """
    Dynamic forwarder for run_render_flow to allow monkeypatching on http_server or kardenwort_controller.
    """
    import kardenwort_controller
    return kardenwort_controller.run_render_flow(*args, **kwargs)


def cmd_server(args=None):
    """
    Subcommand entrypoint to start the Kardenwort Desk HTTP background server.
    Delegates directly to canonical run_controller.
    """
    return run_controller(args)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kardenwort Desk HTTP Server (Unified Controller)")
    parser.add_argument("--host", default=None, help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to (default: 18335)")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    parser.add_argument("--no-sidecars", action="store_true", help="Do not spawn or supervise sidecar microservices")
    cli_args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_controller(cli_args)
