import sys
import os
import json
import hmac
import time
import socket
import logging
import threading
import traceback
import urllib.parse
from pathlib import Path
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# Import core business logic primitives from kardenwort_desk
from kardenwort_desk import (
    load_config,
    core_lookup,
    core_export,
    core_edit_save,
    StructuredError,
    ErrorCode,
    generate_unique_zid,
    find_working_tsv,
)

logger = logging.getLogger("kardenwort.desk.http_server")


ERROR_STATUS_MATRIX = {
    "INVALID_PAYLOAD": 400,
    "MISSING_FIELD": 400,
    "UNAUTHORIZED": 403,
    "TOKEN_NOT_CONFIGURED": 403,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "ROW_STALE": 409,
    "ROW_BUSY": 409,
    "SERVER_ERROR": 500,
    "DESK_FAILED": 500,
    "CONFIGURATION_ERROR": 500,
    "INVALID_STATE": 500,
}


def generate_server_zid(server) -> str:
    """
    Generates a unique server-side ZID per state-mutating request.
    Uses thread-safe monotonic incrementing to prevent collision within the same second.
    """
    now = datetime.now()
    with server.seq_lock:
        server.seq_counter = (server.seq_counter + 1) % 10000
        seq = server.seq_counter
    return f"{now:%Y%m%d%H%M%S}-{seq:04d}"


class APIRequestHandler(BaseHTTPRequestHandler):
    """
    Standalone HTTP request handler providing REST API endpoints for Kardenwort Desk.
    """

    def setup(self):
        super().setup()
        # Enforce strict 5-second socket timeout to prevent Slowloris-style thread hangs
        self.connection.settimeout(5.0)

    def address_string(self):
        # Override to bypass reverse DNS lookups (prevents multi-second request delays)
        return self.client_address[0]

    def log_message(self, format_str, *args):
        # Suppress access logs for health checks to prevent AHK polling log spam
        if self.path and '/api/v1/health' in self.path:
            return
        logger.info("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format_str % args))

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'X-API-Token, Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_json(self, status_code: int, data_obj: dict):
        body = json.dumps({"status": "success", "data": data_obj}, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status_code: int, error_code, message: str, details=None, zid=None):
        payload = {
            "status": "error",
            "error_code": error_code.value if hasattr(error_code, 'value') else str(error_code),
            "message": message,
            "zid": zid or generate_server_zid(self.server),
        }
        if details:
            payload["details"] = details
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        content_length_str = self.headers.get('Content-Length', '0')
        try:
            content_length = int(content_length_str)
        except ValueError:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, "Invalid Content-Length header")

        if content_length > 1024 * 1024:  # 1MB payload limit
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, "Payload size exceeds 1MB limit")

        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode('utf-8'))
        except Exception as e:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, f"Malformed JSON payload: {e}")

    def _authenticate_token(self, body_data=None):
        api_key = getattr(self.server, 'api_key', '')
        if not api_key:
            raise StructuredError(ErrorCode.TOKEN_NOT_CONFIGURED, "Server api_key is empty in config.ini")

        provided_token = self.headers.get('X-API-Token')
        if not provided_token and body_data and isinstance(body_data, dict):
            provided_token = body_data.get('token')

        if not provided_token or not hmac.compare_digest(provided_token.strip(), api_key):
            raise StructuredError(ErrorCode.UNAUTHORIZED, "Invalid or missing API authentication token")

    def do_GET(self):
        try:
            self._dispatch_route('GET')
        except StructuredError as se:
            status_code = ERROR_STATUS_MATRIX.get(se.error_code, 500)
            self._send_error_json(status_code, se.error_code, se.message, se.details)
        except Exception as e:
            logger.error(f"Unhandled HTTP server error on GET {self.path}: {e}\n{traceback.format_exc()}")
            self._send_error_json(500, ErrorCode.SERVER_ERROR, f"Internal server error: {e}")

    def do_POST(self):
        try:
            self._dispatch_route('POST')
        except StructuredError as se:
            status_code = ERROR_STATUS_MATRIX.get(se.error_code, 500)
            self._send_error_json(status_code, se.error_code, se.message, se.details)
        except Exception as e:
            logger.error(f"Unhandled HTTP server error on POST {self.path}: {e}\n{traceback.format_exc()}")
            self._send_error_json(500, ErrorCode.SERVER_ERROR, f"Internal server error: {e}")

    def _dispatch_route(self, method: str):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        qs = urllib.parse.parse_qs(parsed_url.query)

        # Health endpoint
        if path == '/api/v1/health':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._send_json(200, {"ok": True})
            return

        # HTML Lookup endpoint
        if path == '/lookup':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            text = qs.get('text', [''])[0]
            language = qs.get('language', [''])[0]
            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' query parameter")
            if not language:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'language' query parameter")

            res = core_lookup(
                text=text,
                language=language,
                target_lang=qs.get('target-lang', [None])[0],
                fmt=qs.get('format', ['html'])[0],
                text_mode=qs.get('text-mode', ['single'])[0],
                sections=qs.get('sections', [None])[0],
                lemma_columns=qs.get('lemma-columns', [None])[0],
                theme=qs.get('theme', [None])[0],
                no_headings='no-headings' in qs,
                disable_css='disable-css' in qs,
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                goldendict=self.server.goldendict,
            )
            body = res["html"].encode('utf-8')
            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # JSON Lookup endpoint
        if path == '/api/v1/lookup':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            text = qs.get('text', [''])[0]
            language = qs.get('language', [''])[0]
            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' query parameter")
            if not language:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'language' query parameter")

            res = core_lookup(
                text=text,
                language=language,
                target_lang=qs.get('target-lang', [None])[0],
                fmt='html',
                text_mode=qs.get('text-mode', ['single'])[0],
                sections=qs.get('sections', [None])[0],
                lemma_columns=qs.get('lemma-columns', [None])[0],
                theme=qs.get('theme', [None])[0],
                no_headings='no-headings' in qs,
                disable_css='disable-css' in qs,
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                goldendict=self.server.goldendict,
            )
            self._send_json(200, {
                "session_zid": res["session_zid"],
                "language": res["language"],
                "fingerprint": res["fingerprint"],
                "tsv_path": res["tsv_path"],
                "comments": res["comments"],
                "headers": res["headers"],
                "data_rows": res["data_rows"],
                "sentence_translation": res["sentence_translation"],
            })
            return

        # Tag mutation endpoint
        if path == '/api/v1/tag':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            for req_field in ('session_zid', 'language', 'row_id', 'status', 'fingerprint'):
                if req_field not in body:
                    raise StructuredError(ErrorCode.MISSING_FIELD, f"Missing required payload field: '{req_field}'")

            req_zid = generate_server_zid(self.server)
            deltas = [{"row_id": int(body["row_id"]), "column": "DeskSelected", "value": "1" if body["status"] else "0"}]
            res = core_edit_save(
                tsv_path_or_session=body["session_zid"],
                deltas=deltas,
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                fingerprint=body["fingerprint"],
                zid=req_zid,
                language=body["language"],
            )
            self._send_json(200, {
                "zid": res["zid"],
                "session_zid": res["session_zid"],
                "fingerprint": res["fingerprint"],
            })
            return

        # Export endpoint
        if path == '/api/v1/export':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            for req_field in ('session_zid', 'language', 'selected_row_ids', 'fingerprint'):
                if req_field not in body:
                    raise StructuredError(ErrorCode.MISSING_FIELD, f"Missing required payload field: '{req_field}'")

            req_zid = generate_server_zid(self.server)
            res = core_export(
                tsv_path_or_session=body["session_zid"],
                selected_row_ids=body["selected_row_ids"],
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                fingerprint=body["fingerprint"],
                zid=req_zid,
                language=body["language"],
            )
            self._send_json(200, res)
            return

        # Shutdown endpoint
        if path == '/api/v1/shutdown':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            req_zid = generate_server_zid(self.server)
            self._send_json(200, {"zid": req_zid})

            def shutdown_server():
                time.sleep(0.1)
                try:
                    self.server.shutdown()
                    self.server.server_close()
                except Exception as e:
                    logger.error(f"Error during server shutdown: {e}")

            threading.Thread(target=shutdown_server, daemon=True).start()
            return

        raise StructuredError(ErrorCode.NOT_FOUND, f"Unknown API endpoint: {path}")


def cmd_server(args):
    """
    Subcommand entrypoint to start the Kardenwort Desk HTTP background server.
    """
    config, resolved_paths, goldendict, _wordfill = load_config(getattr(args, 'config', None))

    enabled = goldendict.get('server_enabled', False)
    if not enabled:
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, "HTTP Server is disabled in config.ini ([server] enabled = false)")

    host = getattr(args, 'host', None) or goldendict.get('server_host', '127.0.0.1')
    port = getattr(args, 'port', None) or goldendict.get('server_port', 18335)

    # Loopback restriction check (Task 3.1)
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, f"HTTP Server host must be loopback (127.0.0.1). Specified: {host}")

    server = ThreadingHTTPServer((host, port), APIRequestHandler)
    server.allow_reuse_address = False
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.api_key = goldendict.get('server_api_key', '')
    server.seq_counter = 0
    server.seq_lock = threading.Lock()

    startup_zid = generate_unique_zid()
    logger.info(f"Kardenwort Desk HTTP Server started on {host}:{port}", extra={"zid": startup_zid})

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("HTTP Server stopped by KeyboardInterrupt.")
    finally:
        try:
            server.server_close()
        except Exception:
            pass
