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
)

logger = logging.getLogger("kardenwort.desk.http_server")

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
