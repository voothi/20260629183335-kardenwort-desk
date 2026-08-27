import sys
import os
import json
import hmac
import time
import socket
import logging
import threading
import traceback
import webbrowser
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
    verify_language,
    run_render_flow,
    render_verify_language_html,
    get_storage_adapter,
    SEC_SETTINGS,
    persist_default_language,
    spawn_ahk,
)
from kardenwort_controller import ProcessSupervisor

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
    "LANGUAGE_MISMATCH": 422,
    "SERVER_ERROR": 500,
    "DESK_FAILED": 500,
    "CONFIGURATION_ERROR": 500,
    "INVALID_STATE": 500,
}

_DRAFT_SESSIONS: dict = {}
_DRAFT_SESSIONS_LOCK = threading.Lock()



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
        # Generous 30-second socket timeout to prevent connection drops during heavy concurrent render bursts
        self.connection.settimeout(30.0)

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

    def _serve_static_file(self, file_path: Path):
        ext = file_path.suffix.lower()
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".sql": "text/plain; charset=utf-8",
        }
        content_type = content_types.get(ext, "application/octet-stream")

        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except Exception as e:
            raise StructuredError(ErrorCode.SERVER_ERROR, f"Failed to read file: {e}")

        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

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

        # Static assets endpoint
        if path.startswith('/assets/'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            desk_dir = Path(__file__).resolve().parent
            assets_dir = (desk_dir / "assets").resolve()
            req_rel = path[len('/assets/'):]

            target_path = (assets_dir / req_rel).resolve()
            allowed = False
            try:
                target_path.relative_to(assets_dir)
                allowed = True
            except ValueError:
                pass

            if not target_path.exists() and req_rel.startswith('numbers/'):
                ahk_dir = getattr(self.server, 'resolved_paths', {}).get('autohotkey_dir')
                candidates = []
                if ahk_dir:
                    candidates.append((Path(ahk_dir) / "assets" / req_rel).resolve())
                if desk_dir.parent:
                    ahk_repo = next(desk_dir.parent.glob("*-autohotkey"), None)
                    if ahk_repo:
                        candidates.append((ahk_repo / "assets" / req_rel).resolve())
                    candidates.append((desk_dir.parent / "20240411110510-autohotkey" / "assets" / req_rel).resolve())
                for cand in candidates:
                    if cand.exists() and cand.is_file():
                        target_path = cand
                        allowed = True
                        break
                if not target_path.exists():
                    fallback_cands = [
                        assets_dir / "numbers" / "1.ico",
                        (desk_dir.parent / "20240411110510-autohotkey" / "assets" / "numbers" / "1.ico").resolve() if desk_dir.parent else None,
                    ]
                    for fb in fallback_cands:
                        if fb and fb.exists() and fb.is_file():
                            target_path = fb
                            allowed = True
                            break

            if not allowed:
                raise StructuredError(ErrorCode.UNAUTHORIZED, "Path traversal forbidden")

            if not target_path.exists() or not target_path.is_file():
                raise StructuredError(ErrorCode.NOT_FOUND, f"Static asset '{req_rel}' not found")

            self._serve_static_file(target_path)
            return

        # Verification page endpoint
        if path == '/verify-language':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            session_zid = qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0]
            req_theme = qs.get('theme', [None])[0] or 'dark'
            token = qs.get('token', [''])[0] or getattr(self.server, 'api_key', '')
            with _DRAFT_SESSIONS_LOCK:
                draft = _DRAFT_SESSIONS.get(session_zid)
            mismatch_info = draft.get("mismatch_info") if draft else {
                "session_zid": session_zid,
                "is_mismatch": False,
            }
            html = render_verify_language_html(
                mismatch_info=mismatch_info,
                theme=req_theme,
                api_token=token,
                session_zid=session_zid or "",
            )
            body = html.encode('utf-8')
            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Health endpoint
        if path == '/api/v1/health':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            services_status = {}
            if hasattr(self.server, 'supervisor') and self.server.supervisor:
                services_status = self.server.supervisor.get_service_status()
            self._send_json(200, {
                "ok": True,
                "status": "running",
                "services": services_status
            })
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

            bypass_lang_check = (
                qs.get('bypass-lang-check', ['false'])[0].lower() in ('true', '1') or
                qs.get('force-language', ['false'])[0].lower() in ('true', '1')
            )

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
                bypass_lang_check=bypass_lang_check,
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

            bypass_lang_check = (
                qs.get('bypass-lang-check', ['false'])[0].lower() in ('true', '1') or
                qs.get('force-language', ['false'])[0].lower() in ('true', '1')
            )

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
                bypass_lang_check=bypass_lang_check,
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

        # Render endpoint (HTTP Fast-Path for AutoHotkey & Client UI)
        if path in ('/api/v1/render', '/render'):
            if method not in ('GET', 'POST'):
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body() if method == 'POST' else {}

            session_zid = body.get('session_zid') or body.get('zid') or qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0]
            language = body.get('language') or body.get('lang') or qs.get('language', [None])[0] or qs.get('lang', [None])[0]
            text = body.get('text') or qs.get('text', [None])[0]
            text_mode = body.get('text_mode') or body.get('text-mode') or qs.get('text_mode', ['single'])[0]
            theme = body.get('theme') or qs.get('theme', ['dark'])[0]
            zoom = body.get('zoom') or qs.get('zoom', ['100'])[0]
            seq_num = body.get('seq_num') or body.get('seq-num') or qs.get('seq_num', [None])[0]
            split_gap = body.get('split_gap_limit') or body.get('split-gap-limit') or qs.get('split_gap_limit', [None])[0]
            tsv_path = body.get('tsv') or qs.get('tsv', [None])[0]
            raw_bypass = body.get('bypass_lang_check')
            if raw_bypass is None:
                raw_bypass = body.get('bypass-lang-check')
            if raw_bypass is None:
                raw_bypass = body.get('force_language')
            if raw_bypass is None:
                raw_bypass = body.get('force-language')

            if raw_bypass is not None:
                if isinstance(raw_bypass, bool):
                    bypass_lang_check = raw_bypass
                elif isinstance(raw_bypass, (int, float)):
                    bypass_lang_check = bool(raw_bypass)
                elif isinstance(raw_bypass, str):
                    bypass_lang_check = raw_bypass.strip().lower() in ('true', '1', 'yes')
                else:
                    bypass_lang_check = bool(raw_bypass)
            else:
                bypass_lang_check = (
                    qs.get('bypass-lang-check', ['false'])[0].lower() in ('true', '1', 'yes') or
                    qs.get('bypass_lang_check', ['false'])[0].lower() in ('true', '1', 'yes') or
                    qs.get('force-language', ['false'])[0].lower() in ('true', '1', 'yes') or
                    qs.get('force_language', ['false'])[0].lower() in ('true', '1', 'yes')
                )

            active_zid = session_zid or generate_unique_zid()

            html_result = ""
            child_args = []
            if session_zid:
                adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                    source_text = restored.get("source_text", "")
                    sess_lang = restored.get("source_language") or language or "de"
                    t_mode = restored.get("text_mode") or text_mode or "single"
                    render_out = run_render_flow(
                        text=source_text,
                        language=sess_lang,
                        zid=session_zid,
                        text_mode=t_mode,
                        config=self.server.config,
                        resolved_paths=self.server.resolved_paths,
                        zoom_level=str(zoom),
                        theme=theme,
                        tsv_path=Path(tsv_path) if tsv_path else None,
                        split_gap_limit=int(split_gap) if split_gap else None,
                        seq_num=int(seq_num) if seq_num else None,
                        trace_id=f"{session_zid}:render:init",
                        spawn_children=False,
                        return_children=True,
                    )
                    if isinstance(render_out, tuple):
                        html_result, child_args = render_out
                    else:
                        html_result = render_out
                        child_args = getattr(render_out, 'children', [])
                except Exception:
                    if text and language:
                        if not bypass_lang_check:
                            lang_res = verify_language(text, language, self.server.config, bypass=False)
                            if not lang_res.is_match:
                                if lang_res.action in ("block", "prompt"):
                                    with _DRAFT_SESSIONS_LOCK:
                                        _DRAFT_SESSIONS[active_zid] = {
                                            "text": text,
                                            "language": language,
                                            "text_mode": text_mode,
                                            "theme": theme,
                                            "zoom": zoom,
                                            "tsv_path": tsv_path,
                                            "seq_num": seq_num,
                                            "mismatch_info": {
                                                "is_mismatch": True,
                                                "detected_language": lang_res.detected_lang,
                                                "expected_language": lang_res.expected_lang,
                                                "confidence": lang_res.confidence,
                                                "action": lang_res.action,
                                                "text": text,
                                                "session_zid": active_zid,
                                            }
                                        }
                                    raise StructuredError(
                                        ErrorCode.LANGUAGE_MISMATCH,
                                        lang_res.message,
                                        details={
                                            "detected_language": lang_res.detected_lang,
                                            "expected_language": lang_res.expected_lang,
                                            "confidence": lang_res.confidence,
                                            "action": lang_res.action,
                                            "session_zid": active_zid,
                                        }
                                    )
                                elif lang_res.action == "warn":
                                    logger.warning(lang_res.message)

                        render_out = run_render_flow(
                            text=text,
                            language=language,
                            zid=active_zid,
                            text_mode=text_mode,
                            config=self.server.config,
                            resolved_paths=self.server.resolved_paths,
                            zoom_level=str(zoom),
                            theme=theme,
                            tsv_path=Path(tsv_path) if tsv_path else None,
                            split_gap_limit=int(split_gap) if split_gap else None,
                            seq_num=int(seq_num) if seq_num else None,
                            trace_id=f"{active_zid}:render:init",
                            spawn_children=False,
                            return_children=True,
                        )
                        if isinstance(render_out, tuple):
                            html_result, child_args = render_out
                        else:
                            html_result = render_out
                            child_args = getattr(render_out, 'children', [])
                    else:
                        raise
            elif text and language:
                if not bypass_lang_check:
                    lang_res = verify_language(text, language, self.server.config, bypass=False)
                    if not lang_res.is_match:
                        if lang_res.action in ("block", "prompt"):
                            with _DRAFT_SESSIONS_LOCK:
                                _DRAFT_SESSIONS[active_zid] = {
                                    "text": text,
                                    "language": language,
                                    "text_mode": text_mode,
                                    "theme": theme,
                                    "zoom": zoom,
                                    "tsv_path": tsv_path,
                                    "seq_num": seq_num,
                                    "mismatch_info": {
                                        "is_mismatch": True,
                                        "detected_language": lang_res.detected_lang,
                                        "expected_language": lang_res.expected_lang,
                                        "confidence": lang_res.confidence,
                                        "action": lang_res.action,
                                        "text": text,
                                        "session_zid": active_zid,
                                    }
                                }
                            raise StructuredError(
                                ErrorCode.LANGUAGE_MISMATCH,
                                lang_res.message,
                                details={
                                    "detected_language": lang_res.detected_lang,
                                    "expected_language": lang_res.expected_lang,
                                    "confidence": lang_res.confidence,
                                    "action": lang_res.action,
                                    "session_zid": active_zid,
                                }
                            )
                        elif lang_res.action == "warn":
                            logger.warning(lang_res.message)

                render_out = run_render_flow(
                    text=text,
                    language=language,
                    zid=active_zid,
                    text_mode=text_mode,
                    config=self.server.config,
                    resolved_paths=self.server.resolved_paths,
                    zoom_level=str(zoom),
                    theme=theme,
                    tsv_path=Path(tsv_path) if tsv_path else None,
                    split_gap_limit=int(split_gap) if split_gap else None,
                    seq_num=int(seq_num) if seq_num else None,
                    trace_id=f"{active_zid}:render:init",
                    spawn_children=False,
                    return_children=True,
                )
                if isinstance(render_out, tuple):
                    html_result, child_args = render_out
                else:
                    html_result = render_out
                    child_args = getattr(render_out, 'children', [])
            else:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' or 'session_zid'")

            import base64
            b64_html = base64.b64encode(html_result.encode('utf-8')).decode('ascii')
            self._send_json(200, {
                "ok": True,
                "zid": active_zid,
                "html_b64": b64_html,
                "html": html_result,
                "children": child_args or [],
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
            deltas = [{"row_id": int(body["row_id"]), "token_order": int(body.get("token_order", body["row_id"])), "sentence_idx": int(body.get("sentence_idx", 1)), "column": "DeskSelected", "value": "1" if body["status"] else ""}]
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
            if getattr(self.server, 'api_key', ''):
                self._authenticate_token(body)

            req_zid = generate_server_zid(self.server)
            self._send_json(200, {"zid": req_zid})

            def shutdown_server():
                time.sleep(0.1)
                try:
                    if hasattr(self.server, 'supervisor') and self.server.supervisor:
                        self.server.supervisor.stop()
                    self.server.shutdown()
                    self.server.server_close()
                except Exception as e:
                    logger.error(f"Error during server shutdown: {e}")

            threading.Thread(target=shutdown_server, daemon=True).start()
            return

        # Language synchronization endpoint
        if path == '/api/v1/set-language':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            language = body.get('language') or body.get('lang')
            if not language or not isinstance(language, str) or not language.strip():
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing or invalid required payload field: 'language'")
            language = language.strip().lower()

            # 1. Update in-memory config
            if hasattr(self.server, 'config') and self.server.config:
                if not self.server.config.has_section(SEC_SETTINGS):
                    self.server.config.add_section(SEC_SETTINGS)
                self.server.config.set(SEC_SETTINGS, 'default_language', language)

            # 2. Persist to config.ini files (desk and AHK)
            base_dir = getattr(self.server, 'resolved_paths', {}).get('base_dir') if hasattr(self.server, 'resolved_paths') else None
            persisted = persist_default_language(language, base_dir=base_dir)

            # 3. Notify AutoHotkey process via IPC
            spawn_ahk(["--set-language", language], base_dir=base_dir)

            self._send_json(200, {
                "ok": True,
                "language": language,
                "persisted": persisted,
            })
            return

        # Atomic Language Confirmation & Session Tab Spawning
        if path == '/api/v1/confirm-language':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            session_zid = body.get('session_zid') or body.get('zid')
            action = body.get('action')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required payload field: 'session_zid'")
            if not action or action not in ('switch', 'keep', 'cancel'):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, f"Invalid or missing required payload field 'action': '{action}'. Must be 'switch', 'keep', or 'cancel'")

            with _DRAFT_SESSIONS_LOCK:
                draft = _DRAFT_SESSIONS.pop(session_zid, None)

            if action == 'cancel':
                self._send_json(200, {
                    "ok": True,
                    "action": "cancel",
                    "session_zid": session_zid,
                })
                return

            if not draft:
                raise StructuredError(ErrorCode.NOT_FOUND, f"Draft session '{session_zid}' not found or already confirmed")

            if action == 'switch':
                target_lang = draft.get("mismatch_info", {}).get("detected_language") or draft.get("language")
                if target_lang:
                    target_lang = target_lang.strip().lower()
                else:
                    target_lang = "en"

                # 1. Update in-memory config
                if hasattr(self.server, 'config') and self.server.config:
                    if not self.server.config.has_section(SEC_SETTINGS):
                        self.server.config.add_section(SEC_SETTINGS)
                    self.server.config.set(SEC_SETTINGS, 'default_language', target_lang)

                # 2. Persist to config.ini files (desk and AHK)
                base_dir = getattr(self.server, 'resolved_paths', {}).get('base_dir') if hasattr(self.server, 'resolved_paths') else None
                persisted = persist_default_language(target_lang, base_dir=base_dir)

                # 3. Notify AutoHotkey process via IPC
                spawn_ahk(["--set-language", target_lang], base_dir=base_dir)
            else:
                target_lang = draft.get("language") or "en"

            # Execute run_render_flow
            render_out = run_render_flow(
                text=draft.get("text", ""),
                language=target_lang,
                zid=session_zid,
                text_mode=draft.get("text_mode", "single"),
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                zoom_level=str(draft.get("zoom", "100")),
                theme=draft.get("theme", "dark"),
                tsv_path=Path(draft["tsv_path"]) if draft.get("tsv_path") else None,
                seq_num=int(draft["seq_num"]) if draft.get("seq_num") else None,
                spawn_children=False,
                return_children=True,
            )
            if isinstance(render_out, tuple):
                html_result, child_args = render_out
            else:
                html_result = render_out
                child_args = getattr(render_out, 'children', [])

            # Construct URLs for browser tabs
            port = getattr(self.server, 'server_port', None) or (self.server.server_address[1] if hasattr(self.server, 'server_address') else 18335)
            host = self.server.server_address[0] if hasattr(self.server, 'server_address') and self.server.server_address[0] not in ('0.0.0.0', '') else '127.0.0.1'
            api_token = getattr(self.server, 'api_key', '')
            theme = draft.get("theme", "dark")

            def build_browser_url(s_zid, s_num):
                u = f"http://{host}:{port}/?session_zid={urllib.parse.quote(str(s_zid))}&seq_num={urllib.parse.quote(str(s_num))}&theme={urllib.parse.quote(str(theme))}"
                if api_token:
                    u += f"&token={urllib.parse.quote(str(api_token))}"
                return u

            parent_mode = "full"
            if hasattr(self.server, 'config') and self.server.config and self.server.config.has_section("sentences_mode"):
                parent_mode = self.server.config.get("sentences_mode", "parent_mode", fallback="full").lower()

            child_items = []
            i = 0
            curr_seq = 2
            while i < len(child_args):
                arg = child_args[i]
                if arg == "--seq-num" and i + 1 < len(child_args):
                    try:
                        curr_seq = int(child_args[i + 1])
                    except Exception:
                        pass
                    i += 2
                elif arg == "--restore" and i + 1 < len(child_args):
                    t_path = child_args[i + 1]
                    m_zid = re.search(r'(\d{14}(?:-\d+)?)', str(t_path))
                    c_zid = m_zid.group(1) if m_zid else session_zid
                    child_items.append((curr_seq, c_zid))
                    i += 2
                else:
                    i += 1

            spawn_urls = []
            if parent_mode != 'stub' or not child_items:
                spawn_urls.append(build_browser_url(session_zid, 1))

            for c_seq, c_zid in child_items:
                spawn_urls.append(build_browser_url(c_zid, c_seq))

            spawned_urls = []
            for u in spawn_urls:
                try:
                    webbrowser.open_new_tab(u)
                    spawned_urls.append(u)
                    time.sleep(0.05)
                except Exception as e:
                    logger.warning(f"Failed to spawn browser tab for {u}: {e}")

            self._send_json(200, {
                "ok": True,
                "action": action,
                "language": target_lang,
                "session_zid": session_zid,
                "urls": spawned_urls,
            })
            return

        # Browser tab spawning endpoint
        if path == '/api/v1/spawn-tabs':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            raw_urls = body.get('urls') or []
            if isinstance(raw_urls, str):
                raw_urls = [raw_urls]
            elif not isinstance(raw_urls, list):
                raw_urls = []

            if not raw_urls and 'children' in body:
                children = body.get('children')
                if isinstance(children, list):
                    for c in children:
                        if isinstance(c, dict):
                            z = c.get('zid') or c.get('session_zid')
                            s = c.get('seq_num', '1')
                            if z:
                                raw_urls.append(f"/session/render?session_zid={urllib.parse.quote(str(z))}&seq_num={urllib.parse.quote(str(s))}&bypass_lang_check=true")
                        elif isinstance(c, str):
                            raw_urls.append(c)

            port = getattr(self.server, 'server_port', None) or (self.server.server_address[1] if hasattr(self.server, 'server_address') else 18335)
            host = self.server.server_address[0] if hasattr(self.server, 'server_address') and self.server.server_address[0] not in ('0.0.0.0', '') else '127.0.0.1'
            base_url = f"http://{host}:{port}"

            spawned_urls = []
            for u in raw_urls:
                if not u:
                    continue
                full_url = str(u) if (str(u).startswith('http://') or str(u).startswith('https://')) else f"{base_url}{str(u) if str(u).startswith('/') else '/' + str(u)}"
                try:
                    webbrowser.open_new_tab(full_url)
                    spawned_urls.append(full_url)
                except Exception as e:
                    logger.warning(f"Failed to spawn tab for url {full_url}: {e}")

            self._send_json(200, {
                "ok": True,
                "spawned": len(spawned_urls),
                "urls": spawned_urls
            })
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

    ThreadingHTTPServer.request_queue_size = 64
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

    # Initialize & start sidecar supervisor
    enable_supervisor = not (getattr(args, 'no_sidecars', False) if args else False)
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=enable_supervisor)
    if enable_supervisor:
        server.supervisor.start()

    startup_zid = generate_unique_zid()
    logger.info(f"Kardenwort Desk HTTP Server started on {host}:{port}", extra={"zid": startup_zid})

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("HTTP Server stopped by KeyboardInterrupt.")
    finally:
        try:
            if hasattr(server, 'supervisor') and server.supervisor:
                server.supervisor.stop()
            server.server_close()
        except Exception:
            pass
