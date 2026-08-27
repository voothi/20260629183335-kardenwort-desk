import sys
import argparse
import json
import logging
import configparser
import csv
import os
import re
import subprocess
import tempfile
import atexit
import contextlib
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import shutil
import html
import socket
import threading
import time
import functools
import traceback
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, Any, Union, List, FrozenSet, TypedDict, Tuple, Dict
from enum import Enum, auto

# Add local vendor directory for third-party dependencies (e.g. watchdog)
vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
if vendor_dir not in sys.path:
    sys.path.append(vendor_dir)


class ErrorCode(str, Enum):
    """
    Authoritative enumeration of all permitted structured diagnostic error code
    identifiers for Kardenwort-Desk. Corresponds 1-to-1 with schemas/error_catalog.json.

    Inherits from str so that json.dumps() serializes members as plain strings
    without requiring a custom JSON encoder, preserving backward compatibility
    with historical IPC consumers and AutoHotkey substring matchers.
    """
    UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"
    INTERRUPTED = "INTERRUPTED"
    TIMEOUT = "TIMEOUT"
    DESK_FAILED = "DESK_FAILED"
    KARDENWORT_FAILED = "KARDENWORT_FAILED"
    UNRAISABLE_EXCEPTION = "UNRAISABLE_EXCEPTION"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    INVALID_STATE = "INVALID_STATE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    # HTTP Server & API diagnostic error codes
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    MISSING_FIELD = "MISSING_FIELD"
    UNAUTHORIZED = "UNAUTHORIZED"
    TOKEN_NOT_CONFIGURED = "TOKEN_NOT_CONFIGURED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    ROW_STALE = "ROW_STALE"
    ROW_BUSY = "ROW_BUSY"
    SERVER_ERROR = "SERVER_ERROR"
    LANGUAGE_MISMATCH = "LANGUAGE_MISMATCH"
    # Anki export & import pipeline error codes
    ERR_ANKI_NOT_RUNNING = "ERR_ANKI_NOT_RUNNING"
    ERR_ANKICONNECT_DISABLED = "ERR_ANKICONNECT_DISABLED"
    ERR_DECK_NOT_FOUND = "ERR_DECK_NOT_FOUND"
    ERR_NOTE_TYPE_MISMATCH = "ERR_NOTE_TYPE_MISMATCH"
    # Database diagnostics error codes
    MUTATION_NOT_ALLOWED = "MUTATION_NOT_ALLOWED"
    QUERY_FAILED = "QUERY_FAILED"


# Frozen set of all valid catalog codes for O(1) membership checks.
_VALID_ERROR_CODES: FrozenSet[str] = frozenset(member.value for member in ErrorCode)


class SentenceMatchStrategy(str, Enum):
    """
    Authoritative enumeration of sentence matching and lookup strategies.
    - CHECKSUM: Exact content / checksum match.
    - NORMALIZED: Normalized sentence matching (whitespace collapsed, markdown headers stripped, quotes normalized).
    - CONTEXTUAL: Contextual substring / window search.
    - NONE: Bypass sentence search and cached lookup completely.
    """
    CHECKSUM = "checksum"
    NORMALIZED = "normalized"
    CONTEXTUAL = "contextual"
    NONE = "none"

    @classmethod
    def from_str(cls, val: Optional[str]) -> "SentenceMatchStrategy":
        if not val:
            return cls.NORMALIZED
        clean = str(val).strip().lower()
        for member in cls:
            if member.value == clean:
                return member
        return cls.NORMALIZED


def normalize_sentence_for_lookup(text: str) -> str:
    r"""
    Normalizes a sentence or text fragment for flexible, decoupled search:
    - Strips zero-width chars and BOM
    - Strips leading Markdown heading tokens (^#+\s*)
    - Normalizes curly/smart apostrophes and quotes
    - Collapses consecutive whitespace into a single space
    - Strips surrounding whitespace
    """
    if not text:
        return ""
    cleaned = text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
    lines = []
    for line in cleaned.splitlines():
        line = re.sub(r'^\s*#{1,6}\s+', '', line)
        lines.append(line)
    cleaned = " ".join(lines)
    cleaned = re.sub(r"[’‘´ʼ]", "'", cleaned)
    cleaned = re.sub(r'[“”«»]', '"', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


class StructuredError(Exception):
    """
    Authoritative exception class for structured errors across CLI and HTTP layers.
    Contains error_code, message, and optional details dictionary.
    """
    def __init__(self, error_code: Union[ErrorCode, str], message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.error_code = error_code.value if isinstance(error_code, ErrorCode) else error_code
        self.message = message
        self.details = details or {}


def compute_content_fingerprint(data_rows: List[Any], sentence_translation: str = "") -> str:
    """
    Computes a SHA1 content hash of the rendered data rows and sentence translation.
    Used for optimistic locking (detecting row content mutations/deletions).
    """
    import hashlib
    h = hashlib.sha1()
    for row in data_rows:
        if row is None:
            continue
        row_str = "\x1f".join(str(cell) for cell in row)
        h.update(row_str.encode('utf-8'))
        h.update(b"\x1e")
    h.update(sentence_translation.encode('utf-8'))
    return h.hexdigest()


def check_coordination_busy(tsv_path: Path) -> bool:
    """
    Checks if background IntelliFiller or progressive worker sentinel lock files are held.
    Returns True if busy (held by background process), False if free.
    """
    sentinels = [
        Path(str(tsv_path) + ".intellifiller.lock"),
        Path(str(tsv_path) + ".worker.lock")
    ]
    for lock_path in sentinels:
        if not lock_path.exists():
            continue
        try:
            with open(lock_path, 'a') as f:
                if sys.platform == 'win32':
                    import msvcrt
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        return True
                else:
                    import fcntl
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Static Typed Payload Models (TypedDict)
# ---------------------------------------------------------------------------
# These models specify the exact field identifiers, primitive types, and
# optional attributes for all structured dictionaries emitted by emit_payload
# across operational command handlers.
#
# Non-Goals:
#   - Runtime validation (TypedDict is a static-analysis contract only).
#   - Altering existing JSON serialization format or field ordering.
#
# Cross-Language Note:
#   These models serve as authoritative struct blueprints for AI agent
#   compilation into Go struct serialization tags and Rust serde structures
#   during upcoming platform migrations.
# ---------------------------------------------------------------------------


class ExportSkippedPayload(TypedDict):
    """
    Emitted by execute_export when no rows qualify for export based on the
    configured selection mode, or when none of the selected indices are valid.

    cmd_export / execute_export → emit_payload({"status": "skipped", ...})
    """
    status: str          # Always "skipped"
    message: str         # Human-readable warning message for the UI


class ExportImportStartedPayload(TypedDict):
    """
    Emitted by execute_export when a detached Anki import process is
    successfully launched in the background (detach_import_on_send=True).

    cmd_export / execute_export → emit_payload({"import_started": True, ...})
    """
    import_started: bool  # Always True
    show_window: bool     # Whether the Anki import window should be shown
    pid: int              # PID of the detached import subprocess
    log: str              # Absolute path to the detached import log file
    tsv: str              # Absolute path to the exported TSV/favorites file
    note: str             # Advisory message for the UI (e.g. "safe to close")


class ExportImportCompletePayload(TypedDict):
    """
    Emitted by execute_export when a synchronous Anki import completes
    successfully, or when a favorites file is saved without sending to Anki.

    cmd_export / execute_export → emit_payload({"import_complete": True, ...})
    """
    import_complete: bool  # Always True
    show_window: bool      # Whether the Anki import window should be shown
    output: str            # Import result message or success path description


class ExportSuccessPayload(TypedDict):
    """
    Emitted by execute_export when send_to_anki=False and save_to_favorites=False
    — indicating the export was prepared for Anki but no favorites file was created.

    cmd_export / execute_export → emit_payload({"status": "success", ...})
    """
    status: str   # Always "success"
    message: str  # Human-readable success description


class EditSaveSuccessPayload(TypedDict):
    """
    Emitted by cmd_edit_save upon successfully applying all deltas and
    persisting the updated working TSV file.

    cmd_edit_save → emit_payload({"status": "success"})
    """
    status: str  # Always "success"


class ReprocessStartedPayload(TypedDict):
    """
    Emitted by cmd_reprocess when background worker successfully launches.
    """
    reprocess_started: bool  # Always True
    rows: int                # Number of rows cleared/reprocessed


class RetextStartedPayload(TypedDict):
    """
    Emitted by cmd_retext when background worker successfully launches.
    """
    retext_started: bool     # Always True


def emit_payload(data, raw=False):
    """Emit a structured payload to sys.__stdout__ for AHK or GoldenDict consumers.

    IPC Payload Defense contract (ipc-payload-defense / ipc-hardening):
      When raw=True and the consumer is the AutoHotkey front-end over a shell
      process boundary, callers MUST Base64-encode complex payloads (multi-line
      HTML, structured JSON dicts) via b64util.encode() before calling this
      function. This eliminates Windows command-line buffer overflow risks and
      code-page corruption for foreign-language content.

      Compliant AHK-bound callers (must use encode() before raw=True):
        - cmd_render  → emit_payload(encode(html), raw=True)
        - cmd_desk    → emit_payload(encode(html), raw=True)
        - cmd_restore → emit_payload(encode(response_str), raw=True)

      Exempt paths (intentionally NOT Base64-encoded, per design non-goals):
        - cmd_lookup  → plain text/HTML for GoldenDict (human-facing, not AHK)
        - cmd_merge   → ANSI console text for human terminal output

    Args:
        data: dict (JSON-serialised, raw=False) or str (raw=True).
        raw:  If True, emit data as-is; caller is responsible for encoding.
    """
    out = sys.__stdout__
    if out is None:
        out = sys.stderr
        if out is None:
            return
    if not raw:
        payload_str = json.dumps(data) + "\n"
    else:
        payload_str = data + "\n"
    try:
        out.write(payload_str)
        out.flush()
    except OSError:
        if out is not sys.stderr and sys.stderr is not None:
            try:
                sys.stderr.write(payload_str)
                sys.stderr.flush()
            except OSError:
                pass

def print_structured_error(error_code, message, details=None):
    code_str = error_code.value if hasattr(error_code, 'value') else str(error_code)
    if code_str not in _VALID_ERROR_CODES:
        import warnings
        warnings.warn(
            f"print_structured_error: unrecognized error code {code_str!r} is not "
            f"in the shared error catalog. Permitted codes: {sorted(_VALID_ERROR_CODES)}",
            stacklevel=2,
        )
    error_payload = {
        "error_code": code_str,
        "message": message,
    }
    if details:
        error_payload["details"] = details
    if sys.stderr is not None:
        sys.stderr.write(json.dumps(error_payload) + "\n")
        sys.stderr.flush()

def _custom_excepthook(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        try:
            print_structured_error("INTERRUPTED", "Process was interrupted.")
        except Exception:
            if sys.stderr is not None:
                sys.stderr.write('{"error_code": "INTERRUPTED", "message": "Process was interrupted."}\n')
                sys.stderr.flush()
        return
    if issubclass(exc_type, SystemExit):
        if isinstance(exc_value.code, int):
            return
    try:
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        print_structured_error("UNHANDLED_EXCEPTION", str(exc_value), details=tb_str)
    except Exception:
        if sys.stderr is not None:
            sys.stderr.write('{"error_code": "UNHANDLED_EXCEPTION", "message": "Critical exception in error handler."}\n')
            sys.stderr.flush()

sys.excepthook = _custom_excepthook

if hasattr(sys, 'unraisablehook'):
    def _custom_unraisablehook(unraisable):
        try:
            tb_str = "".join(traceback.format_exception(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback)) if unraisable.exc_traceback else None
            print_structured_error("UNRAISABLE_EXCEPTION", unraisable.err_msg or "Unraisable exception", details=tb_str)
        except Exception:
            if sys.stderr is not None:
                sys.stderr.write('{"error_code": "UNRAISABLE_EXCEPTION", "message": "Unraisable exception."}\n')
                sys.stderr.flush()
    sys.unraisablehook = _custom_unraisablehook

if hasattr(threading, 'excepthook'):
    def _custom_thread_excepthook(args):
        try:
            tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)) if args.exc_traceback else None
            print_structured_error("UNHANDLED_EXCEPTION", str(args.exc_value), details=tb_str)
        except Exception:
            if sys.stderr is not None:
                sys.stderr.write('{"error_code": "UNHANDLED_THREAD_EXCEPTION", "message": "Thread exception."}\n')
                sys.stderr.flush()
    threading.excepthook = _custom_thread_excepthook

import text_tokenizer as tok

DEFAULT_COMBINE_ORDER = "contractions_first"
DEFAULT_APOSTROPHE_CHARS = "', ’, ‘, `, ´, ʼ"

SEC_SETTINGS = "settings"
SEC_TOKEN_MAPPINGS = "token_mappings"
SEC_MERGE = "merge"
SEC_SENTENCES_MODE = "sentences_mode"
SEC_CLASSIFICATION = "classification"
SEC_TIMEOUTS = "timeouts"
SEC_PIPELINE = "pipeline"
SEC_TRIGGERS = "triggers"
SEC_TRANSLATION = "translation"
SEC_TRANSLATION_PROVIDERS = "translation_providers"
SEC_RENDERING = "rendering"
SEC_ENVIRONMENT = "environment"
SEC_LANGUAGES = "languages"
SEC_LANGUAGE_RESOURCES = "language_resources"
SEC_PROJECT_STRUCTURE = "project_structure"
SEC_AUDIO = "audio"
SEC_GOLDENDICT = "goldendict"
SEC_WORDFILL = "wordfill"
SEC_SERVER = "server"
SEC_SERVICES = "services"
SEC_LANGUAGE_CHECK = "language_check"
SEC_STORAGE = "storage"
SEC_LOOKUP = "lookup"
SINGLE_WORD_DELIMITERS = ('-', '.', '_')



# ---------------------------------------------------------------------------
# HTTP Microservices Circuit Breaker & Connection Fast-Fail
# ---------------------------------------------------------------------------
_MICROSERVICE_CIRCUIT_BREAKER: Dict[str, float] = {}
_MICROSERVICE_CB_LOCK = threading.Lock()
MICROSERVICE_COOLDOWN_DEFAULT: float = 5.0
MICROSERVICE_CONNECT_TIMEOUT_DEFAULT: float = 0.2  # 200ms connection probe


def reset_microservice_circuit_breaker() -> None:
    """Resets all cached offline microservice states. Primarily used in unit tests."""
    with _MICROSERVICE_CB_LOCK:
        _MICROSERVICE_CIRCUIT_BREAKER.clear()


def _normalize_endpoint(server_url: str) -> str:
    """Normalizes the server URL to scheme://hostname:port."""
    if not server_url:
        return ""
    parsed = urllib.parse.urlparse(server_url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if scheme == "https" else 80)
    return f"{scheme}://{host}:{port}"


def is_endpoint_available(server_url: str, cooldown: float = MICROSERVICE_COOLDOWN_DEFAULT) -> bool:
    """
    Checks whether the endpoint is considered available or in offline cooldown.
    """
    if not server_url:
        return False
    endpoint = _normalize_endpoint(server_url)
    with _MICROSERVICE_CB_LOCK:
        last_failure = _MICROSERVICE_CIRCUIT_BREAKER.get(endpoint)
        if last_failure is not None:
            if time.time() - last_failure < cooldown:
                return False
            # Cooldown expired, clear and allow a probe
            del _MICROSERVICE_CIRCUIT_BREAKER[endpoint]
            return True
        return True


def record_endpoint_failure(server_url: str) -> None:
    """Records that connection or communication with an endpoint failed."""
    if not server_url:
        return
    endpoint = _normalize_endpoint(server_url)
    with _MICROSERVICE_CB_LOCK:
        _MICROSERVICE_CIRCUIT_BREAKER[endpoint] = time.time()


def record_endpoint_success(server_url: str) -> None:
    """Records that endpoint responded successfully, clearing any failure state."""
    if not server_url:
        return
    endpoint = _normalize_endpoint(server_url)
    with _MICROSERVICE_CB_LOCK:
        _MICROSERVICE_CIRCUIT_BREAKER.pop(endpoint, None)


def check_endpoint_reachable(server_url: str, connect_timeout: float = MICROSERVICE_CONNECT_TIMEOUT_DEFAULT) -> bool:
    """
    Fast-probes TCP connectivity to the endpoint host:port within connect_timeout (<= 200ms).
    """
    if not server_url:
        return False
    parsed = urllib.parse.urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=connect_timeout):
            return True
    except Exception:
        return False


def query_spacy_server(
    text: str,
    language: str = "de",
    server_url: str = "http://127.0.0.1:8081",
    zid: Optional[str] = None,
    trace_id: Optional[str] = None,
    options: Optional[dict] = None,
    timeout: float = 3.0,
    connect_timeout: float = MICROSERVICE_CONNECT_TIMEOUT_DEFAULT
) -> Optional[dict]:
    """
    Queries the persistent SpaCy HTTP microservice for tokenization.
    Returns parsed JSON dictionary on success, or None on failure/offline.
    """
    if not server_url or not is_endpoint_available(server_url):
        return None
    if not check_endpoint_reachable(server_url, connect_timeout=connect_timeout):
        record_endpoint_failure(server_url)
        logger.debug(f"SpaCy HTTP microservice connection probe failed at {server_url}")
        return None
    url = f"{server_url.rstrip('/')}/tokenize"
    payload = {
        "text": text,
        "language": language,
        "zid": zid,
        "trace_id": trace_id,
        "options": options or {}
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-ZID": str(zid or ""),
                "X-Trace-ID": str(trace_id or "")
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode('utf-8')
                result = json.loads(body)
                if result.get("status") == "success":
                    record_endpoint_success(server_url)
                    return result
    except Exception as e:
        record_endpoint_failure(server_url)
        logger.debug(f"SpaCy HTTP microservice unavailable at {server_url}: {e}")
    return None


def query_translation_server(
    text: str,
    source: str,
    target: str,
    provider: str = "google",
    server_url: str = "http://127.0.0.1:8082",
    zid: Optional[str] = None,
    trace_id: Optional[str] = None,
    deepl_api_key: Optional[str] = None,
    timeout: float = 10.0,
    connect_timeout: float = MICROSERVICE_CONNECT_TIMEOUT_DEFAULT
) -> Optional[dict]:
    """
    Queries the persistent translation HTTP microservice.
    Returns parsed JSON dictionary on success or structured error response, or None on connection refusal/offline.
    """
    if not server_url or not is_endpoint_available(server_url):
        return None
    if not check_endpoint_reachable(server_url, connect_timeout=connect_timeout):
        record_endpoint_failure(server_url)
        logger.debug(f"Translation HTTP microservice connection probe failed at {server_url}")
        return None
    url = f"{server_url.rstrip('/')}/translate"
    payload = {
        "text": text,
        "source": source,
        "target": target,
        "provider": provider,
        "zid": zid,
        "trace_id": trace_id,
    }
    if deepl_api_key:
        payload["deepl_api_key"] = deepl_api_key
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-ZID": str(zid or ""),
                "X-Trace-ID": str(trace_id or "")
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode('utf-8')
                result = json.loads(body)
                if result.get("status") == "success":
                    record_endpoint_success(server_url)
                    return result
    except urllib.error.HTTPError as he:
        try:
            body = he.read().decode('utf-8')
            err_result = json.loads(body)
            if isinstance(err_result, dict) and (err_result.get("status") == "error" or "code" in err_result):
                record_endpoint_success(server_url)
                return err_result
        except Exception:
            pass
        logger.debug(f"Translation HTTP microservice HTTPError {he.code} at {server_url}")
    except Exception as e:
        record_endpoint_failure(server_url)
        logger.debug(f"Translation HTTP microservice unavailable at {server_url}: {e}")
    return None


def query_intellifiller_server(
    rows: List[Dict[str, Any]],
    prompt: str,
    language: str = "de",
    server_url: str = "http://127.0.0.1:8083",
    zid: Optional[str] = None,
    trace_id: Optional[str] = None,
    field_mapping: Optional[dict] = None,
    timeout: float = 30.0,
    connect_timeout: float = MICROSERVICE_CONNECT_TIMEOUT_DEFAULT,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    prompt_template: Optional[str] = None
) -> Optional[dict]:
    """
    Queries the persistent IntelliFiller HTTP microservice.
    Returns parsed JSON dictionary on success or structured error response, or None on connection refusal/offline.
    """
    if not server_url or not is_endpoint_available(server_url):
        return None
    if not check_endpoint_reachable(server_url, connect_timeout=connect_timeout):
        record_endpoint_failure(server_url)
        logger.debug(f"IntelliFiller HTTP microservice connection probe failed at {server_url}")
        return None
    url = f"{server_url.rstrip('/')}/enrich"
    payload = {
        "rows": rows,
        "prompt": prompt,
        "language": language,
        "zid": zid,
        "trace_id": trace_id,
    }
    if field_mapping:
        payload["field_mapping"] = field_mapping
    if model:
        payload["model"] = model
    if base_url:
        payload["base_url"] = base_url
    if api_key:
        payload["api_key"] = api_key
    if temperature is not None:
        payload["temperature"] = temperature
    if prompt_template:
        payload["prompt_template"] = prompt_template
    try:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-ZID": str(zid or ""),
                "X-Trace-ID": str(trace_id or "")
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode('utf-8')
                result = json.loads(body)
                if result.get("status") == "success":
                    record_endpoint_success(server_url)
                    return result
    except urllib.error.HTTPError as he:
        try:
            body = he.read().decode('utf-8')
            err_result = json.loads(body)
            if isinstance(err_result, dict) and (err_result.get("status") == "error" or "code" in err_result):
                record_endpoint_success(server_url)
                return err_result
        except Exception:
            pass
        logger.debug(f"IntelliFiller HTTP microservice HTTPError {he.code} at {server_url}")
    except Exception as e:
        record_endpoint_failure(server_url)
        logger.debug(f"IntelliFiller HTTP microservice unavailable at {server_url}: {e}")
    return None


def tokenize_text_with_fallback(
    text: str,
    language: str,
    config: Any,
    resolved_paths: Dict[str, Any],
    zid: Optional[str] = None,
    trace_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Attempts high-speed in-memory tokenization via SpaCy HTTP server.
    Falls back to invoking kardenwort.py via CLI subprocess if offline or unavailable.
    """
    spacy_url = None
    if config:
        if config.has_section(SEC_SERVICES):
            spacy_url = config.get(SEC_SERVICES, 'spacy_server_url', fallback=None)
        elif config.has_section('services'):
            spacy_url = config.get('services', 'spacy_server_url', fallback=None)

    if spacy_url:
        resp = query_spacy_server(text, language, server_url=spacy_url, zid=zid, trace_id=trace_id)
        if resp and "tokens" in resp:
            return resp["tokens"]

    # Fallback to CLI subprocess
    python_exe = resolved_paths.get('kardenwort_python', sys.executable)
    kardenwort_ws = resolved_paths.get('kardenwort_workspace', Path('.'))
    kardenwort_script = Path(kardenwort_ws) / "src" / "kardenwort" / "core" / "kardenwort.py"

    cmd = [
        str(python_exe),
        str(kardenwort_script),
        "--language", language,
        "--text", text,
        "--structured-output"
    ]
    if spacy_url:
        cmd.extend(["--spacy-server-url", spacy_url])
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8', timeout=15, env=env)
        tokens = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict) and "word" in item:
                    tokens.append(item)
            except Exception:
                pass
        return tokens
    except Exception as e:
        logger.warning(f"CLI tokenization fallback failed: {e}")
        return []



@dataclass(frozen=True)
class LanguageCheckConfig:
    enabled: bool = True
    languages: Tuple[str, ...] = ("en", "de")
    min_char_length: int = 4
    confidence_threshold: float = 0.60
    action_on_mismatch: str = "prompt"  # "prompt", "block", "warn"

    @classmethod
    def from_config(cls, config: Any) -> "LanguageCheckConfig":
        if isinstance(config, cls):
            return config
        enabled = True
        languages = ("en", "de")
        min_char_length = 4
        confidence_threshold = 0.60
        action_on_mismatch = "prompt"

        if config and hasattr(config, "has_section") and config.has_section(SEC_LANGUAGE_CHECK):
            if hasattr(config, "getboolean"):
                enabled = config.getboolean(SEC_LANGUAGE_CHECK, "enabled", fallback=True)
            if hasattr(config, "get"):
                raw_langs = config.get(SEC_LANGUAGE_CHECK, "languages", fallback="en, de")
                if raw_langs:
                    parsed_langs = [l.strip().lower() for l in raw_langs.split(",") if l.strip()]
                    if parsed_langs:
                        languages = tuple(parsed_langs)
                action_on_mismatch = config.get(SEC_LANGUAGE_CHECK, "action_on_mismatch", fallback="prompt").strip().lower()
                if action_on_mismatch not in ("prompt", "block", "warn"):
                    action_on_mismatch = "prompt"
            if hasattr(config, "getint"):
                min_char_length = config.getint(SEC_LANGUAGE_CHECK, "min_char_length", fallback=4)
            if hasattr(config, "getfloat"):
                confidence_threshold = config.getfloat(SEC_LANGUAGE_CHECK, "confidence_threshold", fallback=0.60)
        elif config and hasattr(config, "has_section") and config.has_section(SEC_LANGUAGES):
            found_langs = []
            for k in config[SEC_LANGUAGES].keys():
                if "_" in k:
                    lang_prefix = k.split("_")[0].lower()
                    if lang_prefix not in found_langs:
                        found_langs.append(lang_prefix)
            if found_langs:
                languages = tuple(found_langs)

        return cls(
            enabled=enabled,
            languages=languages,
            min_char_length=min_char_length,
            confidence_threshold=confidence_threshold,
            action_on_mismatch=action_on_mismatch,
        )


@dataclass(frozen=True)
class LanguageVerificationResult:
    is_match: bool
    expected_lang: str
    detected_lang: Optional[str]
    confidence: float
    action: str  # "proceed", "prompt", "block", "warn"
    message: str = ""


_LINGUA_DETECTOR_CACHE: Dict[Tuple[str, ...], Any] = {}
_LINGUA_DETECTOR_LOCK = threading.Lock()


def verify_language(text: str, expected_lang: str, config: Any, bypass: bool = False) -> LanguageVerificationResult:
    """
    Pre-flight language verification using lingua-py.
    When enabled=False or bypass=True, returns immediately with 0 overhead and no lingua imports.
    """
    lang_cfg = LanguageCheckConfig.from_config(config)
    expected_code = expected_lang.strip().lower() if expected_lang else "en"

    if not lang_cfg.enabled or bypass:
        return LanguageVerificationResult(
            is_match=True,
            expected_lang=expected_code,
            detected_lang=None,
            confidence=1.0,
            action="proceed",
            message="Language verification disabled or bypassed."
        )

    clean_text = text.strip() if text else ""
    if len(clean_text) < lang_cfg.min_char_length:
        return LanguageVerificationResult(
            is_match=True,
            expected_lang=expected_code,
            detected_lang=None,
            confidence=1.0,
            action="proceed",
            message=f"Text length ({len(clean_text)}) below minimum threshold ({lang_cfg.min_char_length})."
        )

    try:
        from lingua import LanguageDetectorBuilder, IsoCode639_1
    except ImportError as e:
        logger.warning(f"lingua-py library not available for language verification: {e}")
        return LanguageVerificationResult(
            is_match=True,
            expected_lang=expected_code,
            detected_lang=None,
            confidence=1.0,
            action="proceed",
            message="lingua library unavailable, verification skipped."
        )

    cache_key = tuple(sorted(lang_cfg.languages))
    with _LINGUA_DETECTOR_LOCK:
        if cache_key not in _LINGUA_DETECTOR_CACHE:
            iso_codes = []
            for l in lang_cfg.languages:
                l_upper = l.strip().upper()
                if hasattr(IsoCode639_1, l_upper):
                    iso_codes.append(getattr(IsoCode639_1, l_upper))
            if iso_codes:
                _LINGUA_DETECTOR_CACHE[cache_key] = LanguageDetectorBuilder.from_iso_codes_639_1(*iso_codes).build()
            else:
                _LINGUA_DETECTOR_CACHE[cache_key] = LanguageDetectorBuilder.from_all_languages().build()
        detector = _LINGUA_DETECTOR_CACHE[cache_key]

    confidence_values = detector.compute_language_confidence_values(clean_text)
    if not confidence_values:
        return LanguageVerificationResult(
            is_match=True,
            expected_lang=expected_code,
            detected_lang=None,
            confidence=0.0,
            action="proceed",
            message="No language detected with confidence."
        )

    top_result = confidence_values[0]
    detected_code = top_result.language.iso_code_639_1.name.lower()
    confidence = top_result.value

    if confidence < lang_cfg.confidence_threshold:
        return LanguageVerificationResult(
            is_match=True,
            expected_lang=expected_code,
            detected_lang=detected_code,
            confidence=confidence,
            action="proceed",
            message=f"Confidence ({confidence:.2f}) below threshold ({lang_cfg.confidence_threshold:.2f})."
        )

    if detected_code == expected_code:
        return LanguageVerificationResult(
            is_match=True,
            expected_lang=expected_code,
            detected_lang=detected_code,
            confidence=confidence,
            action="proceed",
            message=f"Language matched: {detected_code} (confidence: {confidence:.2f})."
        )

    action = lang_cfg.action_on_mismatch
    msg = f"Language mismatch detected: input text appears to be '{detected_code}' (confidence {confidence:.2f}), but expected language is '{expected_code}'."
    return LanguageVerificationResult(
        is_match=False,
        expected_lang=expected_code,
        detected_lang=detected_code,
        confidence=confidence,
        action=action,
        message=msg
    )


@dataclass(frozen=True)
class RuntimeTokenConfig:
    combine_source_words: bool = False
    combine_order: str = DEFAULT_COMBINE_ORDER
    prefer_lowercase: bool = True
    filter_by_window: bool = True
    apostrophe_chars: str = DEFAULT_APOSTROPHE_CHARS
    token_mappings_enabled: bool = True
    lemmatize_mapped_tokens: bool = True

    @property
    def combine_source_words_order(self) -> str:
        return self.combine_order

    @property
    def combine_source_words_prefer_lowercase(self) -> bool:
        return self.prefer_lowercase

    @classmethod
    def from_config(cls, config: Any) -> "RuntimeTokenConfig":
        if isinstance(config, cls):
            return config

        if config and hasattr(config, "getboolean"):
            filter_by_window = config.getboolean(SEC_SETTINGS, "filter_inflected_by_window", fallback=True)
            combine_source_words = config.getboolean(SEC_SETTINGS, "combine_source_words", fallback=False)
            token_mappings_enabled = config.getboolean(SEC_TOKEN_MAPPINGS, "enabled", fallback=True)
            lemmatize_mapped_tokens = config.getboolean(SEC_TOKEN_MAPPINGS, "lemmatize_mapped_tokens", fallback=True)
        else:
            filter_by_window = True
            combine_source_words = False
            token_mappings_enabled = True
            lemmatize_mapped_tokens = True

        if config and hasattr(config, "get"):
            combine_order = config.get(
                SEC_TOKEN_MAPPINGS,
                "combine_source_words_order",
                fallback=config.get(SEC_SETTINGS, "combine_source_words_order", fallback=DEFAULT_COMBINE_ORDER),
            ).strip().lower()
            apostrophe_chars = config.get(
                SEC_TOKEN_MAPPINGS,
                "apostrophe_chars",
                fallback=config.get(SEC_SETTINGS, "apostrophe_chars", fallback=DEFAULT_APOSTROPHE_CHARS),
            ).strip('"')
            prefer_lowercase = config.getboolean(
                SEC_TOKEN_MAPPINGS,
                "combine_source_words_prefer_lowercase",
                fallback=config.getboolean(SEC_SETTINGS, "combine_source_words_prefer_lowercase", fallback=True),
            )
        else:
            combine_order = DEFAULT_COMBINE_ORDER
            apostrophe_chars = DEFAULT_APOSTROPHE_CHARS
            prefer_lowercase = True

        # Keep tokenizer synchronized with configured apostrophe characters
        tok.APOSTROPHE_CHARS.update(c.strip() for c in apostrophe_chars.split(',') if c.strip())

        return cls(
            combine_source_words=combine_source_words,
            combine_order=combine_order,
            prefer_lowercase=prefer_lowercase,
            filter_by_window=filter_by_window,
            apostrophe_chars=apostrophe_chars,
            token_mappings_enabled=token_mappings_enabled,
            lemmatize_mapped_tokens=lemmatize_mapped_tokens,
        )


@dataclass(frozen=True)
class BatchMergeConfig:
    deduplicate: bool = True
    deduplicate_by_lemma: bool = True
    sort_frequency: bool = False
    combine_order: str = DEFAULT_COMBINE_ORDER
    prefer_lowercase: bool = True
    apostrophe_chars: str = DEFAULT_APOSTROPHE_CHARS

    @property
    def combine_source_words_order(self) -> str:
        return self.combine_order

    @property
    def combine_source_words_prefer_lowercase(self) -> bool:
        return self.prefer_lowercase

    @classmethod
    def from_config(cls, config: Any, args: Any = None) -> "BatchMergeConfig":
        if isinstance(config, cls):
            return config
        dedup_arg = getattr(args, "deduplicate", False) if args else False
        sort_arg = getattr(args, "sort_frequency", False) if args else False

        if config and hasattr(config, "getboolean"):
            dedup_val = config.getboolean(
                SEC_MERGE, "deduplicate",
                fallback=config.getboolean(SEC_SETTINGS, "merge_deduplicate", fallback=True)
            )
            sort_val = config.getboolean(
                SEC_MERGE, "sort_frequency",
                fallback=config.getboolean(SEC_SETTINGS, "merge_sort_frequency", fallback=False)
            )
            dedup_lemma = config.getboolean(
                SEC_MERGE, "deduplicate_by_lemma",
                fallback=config.getboolean(SEC_SETTINGS, "merge_deduplicate_by_lemma", fallback=True)
            )
        else:
            dedup_val = True
            sort_val = False
            dedup_lemma = True

        dedup_final = dedup_val if not dedup_arg else True
        sort_final = sort_val if not sort_arg else True

        if config and hasattr(config, "get"):
            combine_order = config.get(
                SEC_MERGE, "combine_source_words_order",
                fallback=config.get(SEC_TOKEN_MAPPINGS, "combine_source_words_order",
                                    fallback=config.get(SEC_SETTINGS, "combine_source_words_order", fallback=DEFAULT_COMBINE_ORDER))
            ).strip().lower()
            apostrophe_chars = config.get(
                SEC_MERGE, "apostrophe_chars",
                fallback=config.get(SEC_TOKEN_MAPPINGS, "apostrophe_chars",
                                    fallback=config.get(SEC_SETTINGS, "apostrophe_chars", fallback=DEFAULT_APOSTROPHE_CHARS))
            ).strip('"')
            prefer_lowercase = config.getboolean(
                SEC_MERGE, "combine_source_words_prefer_lowercase",
                fallback=config.getboolean(SEC_TOKEN_MAPPINGS, "combine_source_words_prefer_lowercase",
                                           fallback=config.getboolean(SEC_SETTINGS, "combine_source_words_prefer_lowercase", fallback=True))
            )
        else:
            combine_order = DEFAULT_COMBINE_ORDER
            apostrophe_chars = DEFAULT_APOSTROPHE_CHARS
            prefer_lowercase = True

        return cls(
            deduplicate=dedup_final,
            deduplicate_by_lemma=dedup_lemma,
            sort_frequency=sort_final,
            combine_order=combine_order,
            prefer_lowercase=prefer_lowercase,
            apostrophe_chars=apostrophe_chars,
        )


@dataclass(frozen=True)
class DeGCSConfig:
    enabled: bool = False
    pos_tags: Optional[str] = None
    split_mode: Optional[str] = None
    part_singularization: Optional[str] = None
    preserve_compound_word: bool = False
    add_parts_to_wordlist: bool = False
    skip_merge_fractions: bool = False
    mask_unknown_parts: bool = False

    @classmethod
    def from_config(cls, config: Any) -> "DeGCSConfig":
        if isinstance(config, cls):
            return config

        enabled = False
        preserve_compound_word = False
        add_parts_to_wordlist = False
        skip_merge_fractions = False
        mask_unknown_parts = False
        pos_tags = None
        split_mode = None
        part_singularization = None

        if config and hasattr(config, "getboolean"):
            try:
                enabled = config.getboolean(SEC_SETTINGS, "de_gcs", fallback=False)
            except Exception:
                enabled = False
            try:
                preserve_compound_word = config.getboolean(SEC_SETTINGS, "de_gcs_preserve_compound_word", fallback=False)
            except Exception:
                preserve_compound_word = False
            try:
                add_parts_to_wordlist = config.getboolean(SEC_SETTINGS, "de_gcs_add_parts_to_wordlist", fallback=False)
            except Exception:
                add_parts_to_wordlist = False
            try:
                skip_merge_fractions = config.getboolean(SEC_SETTINGS, "de_gcs_skip_merge_fractions", fallback=False)
            except Exception:
                skip_merge_fractions = False
            try:
                mask_unknown_parts = config.getboolean(SEC_SETTINGS, "de_gcs_mask_unknown_parts", fallback=False)
            except Exception:
                mask_unknown_parts = False

        if config and hasattr(config, "get"):
            try:
                pos_tags = config.get(SEC_SETTINGS, "de_gcs_pos_tags", fallback=None)
                if pos_tags is not None and not isinstance(pos_tags, str):
                    pos_tags = str(pos_tags)
            except Exception:
                pos_tags = None

            try:
                split_mode = config.get(SEC_SETTINGS, "de_gcs_split_mode", fallback=None)
                if split_mode is not None and not isinstance(split_mode, str):
                    split_mode = str(split_mode)
            except Exception:
                split_mode = None

            try:
                part_singularization = config.get(SEC_SETTINGS, "de_gcs_part_singularization", fallback=None)
                if part_singularization is not None and not isinstance(part_singularization, str):
                    part_singularization = str(part_singularization)
            except Exception:
                part_singularization = None

        return cls(
            enabled=enabled,
            pos_tags=pos_tags,
            split_mode=split_mode,
            part_singularization=part_singularization,
            preserve_compound_word=preserve_compound_word,
            add_parts_to_wordlist=add_parts_to_wordlist,
            skip_merge_fractions=skip_merge_fractions,
            mask_unknown_parts=mask_unknown_parts,
        )

    def to_cli_args(self) -> List[str]:
        if not self.enabled:
            return []
        cmd = ["--de-gcs"]
        if self.pos_tags:
            cmd.append("--de-gcs-pos-tags")
            cmd.extend(self.pos_tags.strip().split())
        if self.split_mode:
            cmd.extend(["--de-gcs-split-mode", self.split_mode.strip()])
        if self.part_singularization:
            cmd.extend(["--de-gcs-part-singularization", self.part_singularization.strip()])
        if self.preserve_compound_word:
            cmd.append("--de-gcs-preserve-compound-word")
        if self.add_parts_to_wordlist:
            cmd.append("--de-gcs-add-parts-to-wordlist")
        if self.skip_merge_fractions:
            cmd.append("--de-gcs-skip-merge-fractions")
        if self.mask_unknown_parts:
            cmd.append("--de-gcs-mask-unknown-parts")
        return cmd


@dataclass(frozen=True)
class SentenceBoundaryConfig:
    terminators: str = ".!?:"
    punctuation_marks: str = ".,;:!?()\"[]{}—–"
    abbrev_str: str = ""
    abbrev_set: Optional[FrozenSet[str]] = None
    words_before: int = 0
    words_after: int = 0
    max_words: int = 0
    translated_words_before: int = 0
    translated_words_after: int = 0
    translated_max_words: int = 0
    context_mode: str = "single"

    @classmethod
    def from_config(cls, config: Any) -> "SentenceBoundaryConfig":
        if isinstance(config, cls):
            return config

        terminators = ".!?:"
        punctuation_marks = ".,;:!?()\"[]{}—–"
        abbrev_str = ""
        abbrev_set = None
        words_before = 0
        words_after = 0
        max_words = 0
        translated_words_before = 0
        translated_words_after = 0
        translated_max_words = 0
        context_mode = "single"

        if config and hasattr(config, "get"):
            try:
                abbrev_str = config.get(SEC_SETTINGS, 'anki_abbrev_list', fallback="")
                if abbrev_str.strip():
                    abbrev_set = frozenset(a.lower().rstrip('.') for a in abbrev_str.split())
            except Exception:
                abbrev_str = ""
                abbrev_set = None

            try:
                t_val = None
                if hasattr(config, "has_section") and config.has_section(SEC_SENTENCES_MODE):
                    t_val = config.get(SEC_SENTENCES_MODE, 'terminators', fallback=None)
                if t_val is None:
                    t_val = config.get(SEC_SETTINGS, 'anki_sentence_terminators', fallback=".!?:")
                if t_val:
                    t_val = str(t_val).strip('"')
                if t_val and t_val.strip():
                    terminators = t_val
                else:
                    terminators = ".!?:"
            except Exception:
                terminators = ".!?:"

            try:
                p_val = None
                if hasattr(config, "has_section") and config.has_section(SEC_SENTENCES_MODE):
                    p_val = config.get(SEC_SENTENCES_MODE, 'punctuation_marks', fallback=None)
                if p_val is None:
                    p_val = ".,;:!?()\"[]{}—–"
                else:
                    p_val = str(p_val).strip('"')
                punctuation_marks = p_val
            except Exception:
                punctuation_marks = ".,;:!?()\"[]{}—–"

            try:
                context_mode = str(config.get(SEC_SETTINGS, 'anki_context_mode', fallback='single')).lower()
            except Exception:
                context_mode = 'single'

        if config and hasattr(config, "getint"):
            try:
                words_before = config.getint(SEC_SETTINGS, 'anki_context_words_before', fallback=0)
            except Exception:
                words_before = 0
            try:
                words_after = config.getint(SEC_SETTINGS, 'anki_context_words_after', fallback=0)
            except Exception:
                words_after = 0
            try:
                max_words = config.getint(SEC_SETTINGS, 'anki_context_max_words', fallback=0)
            except Exception:
                max_words = 0
            try:
                translated_words_before = config.getint(SEC_SETTINGS, 'anki_translated_context_words_before', fallback=0)
            except Exception:
                translated_words_before = 0
            try:
                translated_words_after = config.getint(SEC_SETTINGS, 'anki_translated_context_words_after', fallback=0)
            except Exception:
                translated_words_after = 0
            try:
                translated_max_words = config.getint(SEC_SETTINGS, 'anki_translated_context_max_words', fallback=0)
            except Exception:
                translated_max_words = 0

        return cls(
            terminators=terminators,
            punctuation_marks=punctuation_marks,
            abbrev_str=abbrev_str,
            abbrev_set=abbrev_set,
            words_before=words_before,
            words_after=words_after,
            max_words=max_words,
            translated_words_before=translated_words_before,
            translated_words_after=translated_words_after,
            translated_max_words=translated_max_words,
            context_mode=context_mode,
        )


@dataclass(frozen=True)
class SentencesModeConfig:
    enabled: bool = False
    min_sentences: int = 2
    alignment_method: str = "auto"
    spawn_order: str = "normal"
    parent_mode: str = "full"
    multi_mode_decompose: bool = False
    legacy_spawn_children: bool = False
    deduplication_scope: str = "sentence"
    
    def should_split_sentences(self, num_sentences: int) -> bool:
        """Returns True if the configuration dictates that this text should be decomposed into multiple sentence windows."""
        return self.enabled and num_sentences >= self.min_sentences
        
    def get_expected_window_count(self, num_sentences: int) -> int:
        """Returns the number of Kardenwort Desk windows that will spawn under this configuration."""
        if not self.should_split_sentences(num_sentences):
            return 1
            
        parent_spawns = 0 if self.parent_mode == 'none' else 1
        return num_sentences + parent_spawns

    @classmethod
    def from_config(cls, config: Any) -> "SentencesModeConfig":
        if isinstance(config, cls):
            return config

        enabled = False
        min_sentences = 2
        alignment_method = "auto"
        spawn_order = "normal"
        parent_mode = "full"
        multi_mode_decompose = False
        legacy_spawn_children = False
        deduplication_scope = "sentence"

        if config and hasattr(config, "has_section") and hasattr(config, "get"):
            try:
                if config.has_section(SEC_SENTENCES_MODE):
                    if hasattr(config, "getboolean"):
                        enabled = config.getboolean(SEC_SENTENCES_MODE, "enabled", fallback=False)
                        multi_mode_decompose = config.getboolean(SEC_SENTENCES_MODE, "multi_mode_sentence_decomposition", fallback=False)
                        legacy_spawn_children = config.getboolean(SEC_SENTENCES_MODE, "legacy_spawn_children", fallback=False)
                    if hasattr(config, "getint"):
                        min_sentences = config.getint(SEC_SENTENCES_MODE, "min_sentences", fallback=2)
                    alignment_method = config.get(SEC_SENTENCES_MODE, "alignment_method", fallback="auto")
                    spawn_order = config.get(SEC_SENTENCES_MODE, "spawn_order", fallback="normal")
                    parent_mode = config.get(SEC_SENTENCES_MODE, "parent_mode", fallback="full")
                    deduplication_scope = config.get(SEC_SENTENCES_MODE, "deduplication_scope", fallback="sentence").strip().lower()
            except Exception:
                pass

        return cls(
            enabled=enabled,
            min_sentences=min_sentences,
            alignment_method=alignment_method,
            spawn_order=spawn_order,
            parent_mode=parent_mode,
            multi_mode_decompose=multi_mode_decompose,
            legacy_spawn_children=legacy_spawn_children,
            deduplication_scope=deduplication_scope,
        )


class OperationalMode(Enum):
    MONOLITHIC_LIVE = auto()
    MULTI_SENTENCE_LOCAL_DEDUP = auto()
    MULTI_GLOBAL_COMBINED = auto()


@dataclass(frozen=True)
class ExecutionContext:
    mode: OperationalMode
    combine_source_words: bool
    config: Any = None
    token_cfg: Optional[RuntimeTokenConfig] = None

    @classmethod
    def from_config(cls, text_mode: str, config: Any, token_cfg: Optional[RuntimeTokenConfig] = None, will_split: bool = False) -> "ExecutionContext":
        if isinstance(config, cls):
            return config

        if token_cfg is None:
            token_cfg = RuntimeTokenConfig.from_config(config)

        combine_source_words = token_cfg.combine_source_words
        smc = SentencesModeConfig.from_config(config)
        sentences_enabled = smc.enabled
        dedup_scope = smc.deduplication_scope

        is_multi_sentence = (text_mode == 'multi' and sentences_enabled) or (will_split and sentences_enabled)

        if is_multi_sentence:
            if dedup_scope == 'sentence':
                mode = OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP
            else:
                mode = OperationalMode.MULTI_GLOBAL_COMBINED
        else:
            mode = OperationalMode.MONOLITHIC_LIVE

        return cls(
            mode=mode,
            combine_source_words=combine_source_words,
            config=config,
            token_cfg=token_cfg,
        )


@dataclass(frozen=True)
class OperationalWorkflowResult:
    mode: OperationalMode
    dedup_scope: str
    combine_source_words: bool


class PipelineStrategy:
    mode: OperationalMode

    def execute(self, exec_ctx: ExecutionContext, *args: Any, **kwargs: Any) -> OperationalWorkflowResult:
        raise NotImplementedError


class MonolithicLiveStrategy(PipelineStrategy):
    mode = OperationalMode.MONOLITHIC_LIVE

    def execute(self, exec_ctx: ExecutionContext, *args: Any, **kwargs: Any) -> OperationalWorkflowResult:
        return OperationalWorkflowResult(
            mode=self.mode,
            dedup_scope="global",
            combine_source_words=exec_ctx.combine_source_words,
        )


class SentenceLocalDedupStrategy(PipelineStrategy):
    mode = OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP

    def execute(self, exec_ctx: ExecutionContext, *args: Any, **kwargs: Any) -> OperationalWorkflowResult:
        return OperationalWorkflowResult(
            mode=self.mode,
            dedup_scope="sentence",
            combine_source_words=exec_ctx.combine_source_words,
        )


class MultiGlobalCombinedStrategy(PipelineStrategy):
    mode = OperationalMode.MULTI_GLOBAL_COMBINED

    def execute(self, exec_ctx: ExecutionContext, *args: Any, **kwargs: Any) -> OperationalWorkflowResult:
        return OperationalWorkflowResult(
            mode=self.mode,
            dedup_scope="global",
            combine_source_words=exec_ctx.combine_source_words,
        )


class ModeDispatcher:
    _strategies = {
        OperationalMode.MONOLITHIC_LIVE: MonolithicLiveStrategy(),
        OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP: SentenceLocalDedupStrategy(),
        OperationalMode.MULTI_GLOBAL_COMBINED: MultiGlobalCombinedStrategy(),
    }

    @classmethod
    def get_strategy(cls, mode_or_ctx: Union[OperationalMode, ExecutionContext]) -> PipelineStrategy:
        if isinstance(mode_or_ctx, ExecutionContext):
            mode = mode_or_ctx.mode
        elif isinstance(mode_or_ctx, OperationalMode):
            mode = mode_or_ctx
        else:
            raise TypeError("ModeDispatcher requires OperationalMode or ExecutionContext")
        strategy = cls._strategies.get(mode)
        if strategy is None:
            raise ValueError(f"No pipeline strategy registered for operational mode: {mode}")
        return strategy

    @classmethod
    def dispatch(cls, exec_ctx: ExecutionContext, *args: Any, **kwargs: Any) -> OperationalWorkflowResult:
        strategy = cls.get_strategy(exec_ctx)
        return strategy.execute(exec_ctx, *args, **kwargs)


class ConfigError(Exception):
    pass

_ACTIVE_ZIDS_LOCK = threading.Lock()
_ACTIVE_ZIDS = set()
_BOOT_LOCK = threading.Lock()
_HAS_BOOTED = False

class TraceTimer(contextlib.ContextDecorator):
    def __init__(self, phase, zid, config, resolved_paths, extra=None):
        self.phase = phase
        self.zid = zid
        self.config = config
        self.resolved_paths = resolved_paths
        self.extra = extra
        
        self.enabled = False
        self.max_mb = 5
        if self.config and hasattr(self.config, 'getboolean'):
            try:
                self.enabled = self.config.getboolean('profiling', 'enable_performance_tracing', fallback=self.config.getboolean(SEC_SETTINGS, 'enable_performance_tracing', fallback=False))
            except Exception:
                self.enabled = False
            try:
                self.max_mb = self.config.getfloat('profiling', 'trace_log_max_mb', fallback=self.config.getfloat(SEC_SETTINGS, 'trace_log_max_mb', fallback=5.0))
            except Exception:
                self.max_mb = 5.0

    def __enter__(self):
        if not self.enabled:
            return self
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        global _HAS_BOOTED
        if not self.enabled:
            return False
            
        duration = time.perf_counter() - getattr(self, 'start', time.perf_counter())
        
        with _BOOT_LOCK:
            cold_start = not _HAS_BOOTED
            if not _HAS_BOOTED:
                _HAS_BOOTED = True
            
        try:
            try:
                _kw_workspace = self.resolved_paths.get('kardenwort_workspace') if isinstance(self.resolved_paths, dict) else None
                _kw_cfg = load_kardenwort_config(_kw_workspace) if _kw_workspace else self.config
            except Exception:
                _kw_cfg = self.config
            results_dir = resolve_results_dir(self.resolved_paths, _kw_cfg)
            if not results_dir:
                return False
                
            log_file = Path(results_dir) / 'speed_trace.jsonl'
            
            with file_lock(log_file):
                if log_file.exists() and log_file.stat().st_size > self.max_mb * 1024 * 1024:
                    try:
                        with open(log_file, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        with open(log_file, 'w', encoding='utf-8') as f:
                            f.writelines(lines[len(lines)//2:])
                    except Exception:
                        pass
                        
                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "zid": self.zid,
                    "phase": self.phase,
                    "duration": duration,
                    "cold_start": cold_start,
                    "status": "error" if exc and exc[0] is not None else "success"
                }
                if self.extra:
                    entry["extra"] = self.extra
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry) + '\n')
        except Exception:
            pass
        return False

_last_zid = None
def generate_unique_zid():
    global _last_zid
    from datetime import datetime
    current = datetime.now().strftime("%Y%m%d%H%M%S")
    if _last_zid and current <= _last_zid:
        current = str(int(_last_zid) + 1)
    _last_zid = current
    return current

def parse_sections_list(raw, valid_tokens):
    if not raw or not raw.strip():
        return []
    result = []
    for token in raw.split(','):
        t = token.strip()
        if not t:
            continue
        if t in valid_tokens:
            result.append(t)
        else:
            sys.stderr.write(f"Warning: Unknown section token '{t}' ignored.\n")
    return result

def parse_columns_list(raw, valid_tokens):
    if not raw or not raw.strip():
        return []
    result = []
    for token in raw.split(','):
        t = token.strip()
        if not t:
            continue
        if t in valid_tokens:
            result.append(t)
        else:
            sys.stderr.write(f"Warning: Unknown column token '{t}' ignored.\n")
    return result

_warned_keys = set()
def _warn_deprecated(key, msg):
    if key not in _warned_keys:
        _warned_keys.add(key)
        logger.warning(msg)

def _migrate_config(config):
    # Ensure sections exist
    if not config.has_section(SEC_PIPELINE):
        config.add_section(SEC_PIPELINE)
    if not config.has_section(SEC_TRIGGERS):
        config.add_section(SEC_TRIGGERS)
    if not config.has_section(SEC_RENDERING):
        config.add_section(SEC_RENDERING)

    # Read legacy providers
    legacy_main = config.get(SEC_TRANSLATION_PROVIDERS, 'main_text_translation', fallback=None) if config.has_section(SEC_TRANSLATION_PROVIDERS) else None
    legacy_lemmas = config.get(SEC_TRANSLATION_PROVIDERS, 'lemmas_translation', fallback=None) if config.has_section(SEC_TRANSLATION_PROVIDERS) else None

    # Resolve text_base_provider (migrated from main_text_provider or legacy_main)
    text_base = config.get(SEC_PIPELINE, 'text_base_provider', fallback=None)
    old_main_text = config.get(SEC_PIPELINE, 'main_text_provider', fallback=None)
    if text_base is None:
        if old_main_text is not None:
            text_base = old_main_text
        elif legacy_main is not None:
            text_base = legacy_main
        else:
            text_base = 'google'
    config.set(SEC_PIPELINE, 'text_base_provider', text_base)

    # Resolve text_reprocess_provider
    text_reprocess = config.get(SEC_PIPELINE, 'text_reprocess_provider', fallback=None)
    if text_reprocess is None:
        text_reprocess = 'deepl'
    config.set(SEC_PIPELINE, 'text_reprocess_provider', text_reprocess)

    # Resolve lemma_base_provider (migrated from base_provider)
    lemma_base = config.get(SEC_PIPELINE, 'lemma_base_provider', fallback=None)
    old_base = config.get(SEC_PIPELINE, 'base_provider', fallback=None)
    if lemma_base is None:
        if old_base is not None:
            lemma_base = old_base
        else:
            if legacy_main is not None or legacy_lemmas is not None:
                _warn_deprecated('translation_providers', "Section [translation_providers] is deprecated; map its settings to [pipeline].")
            if legacy_main == 'deepl':
                lemma_base = 'deepl'
            else:
                lemma_base = 'google'
    config.set(SEC_PIPELINE, 'lemma_base_provider', lemma_base)

    # Resolve lemma_reprocess_provider (migrated from enrichment_provider)
    lemma_reprocess = config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback=None)
    old_enrichment = config.get(SEC_PIPELINE, 'enrichment_provider', fallback=None)
    if lemma_reprocess is None:
        if old_enrichment is not None:
            lemma_reprocess = old_enrichment
        else:
            if legacy_main is not None or legacy_lemmas is not None:
                _warn_deprecated('translation_providers', "Section [translation_providers] is deprecated; map its settings to [pipeline].")
            if legacy_lemmas in ('google', 'deepl'):
                lemma_reprocess = 'none'
            elif legacy_lemmas in ('intellifiller', 'combined'):
                lemma_reprocess = 'intellifiller'
            else:
                lemma_reprocess = 'intellifiller'
    config.set(SEC_PIPELINE, 'lemma_reprocess_provider', lemma_reprocess)

    # Read legacy triggers
    legacy_lazy = config.get(SEC_SETTINGS, 'lazy_processing', fallback=None) if config.has_section(SEC_SETTINGS) else None

    # Resolve triggers
    run_lemma_base = config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback=None)
    old_run_base = config.get(SEC_TRIGGERS, 'run_base_translation', fallback=None)
    run_lemma_enrich = config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback=None)
    old_run_enrich = config.get(SEC_TRIGGERS, 'run_enrichment', fallback=None)
    run_text = config.get(SEC_TRIGGERS, 'run_text_translation', fallback=None)

    if run_lemma_base is None or run_lemma_enrich is None or run_text is None:
        mapped_base = 'auto'
        mapped_enrich = 'auto'
        if legacy_lazy is not None:
            _warn_deprecated('lazy_processing', "lazy_processing is deprecated; map it to triggers.run_lemma_base_translation and triggers.run_lemma_enrichment.")
            lazy_val = legacy_lazy.lower()
            if lazy_val in ('true', 'all'):
                mapped_base = 'manual'
                mapped_enrich = 'manual'
            elif lazy_val == 'llm_only':
                mapped_base = 'auto'
                mapped_enrich = 'manual'
            else:
                mapped_base = 'auto'
                mapped_enrich = 'auto'
        
        if run_lemma_base is None:
            run_lemma_base = old_run_base if old_run_base is not None else mapped_base
        if run_lemma_enrich is None:
            run_lemma_enrich = old_run_enrich if old_run_enrich is not None else mapped_enrich
        if run_text is None:
            run_text = run_lemma_base
    
    config.set(SEC_TRIGGERS, 'run_lemma_base_translation', run_lemma_base)
    config.set(SEC_TRIGGERS, 'run_lemma_enrichment', run_lemma_enrich)
    config.set(SEC_TRIGGERS, 'run_text_translation', run_text)

    # Read legacy rendering
    legacy_prog = config.get(SEC_SETTINGS, 'progressive_loading', fallback=None) if config.has_section(SEC_SETTINGS) else None

    # Resolve rendering
    display_mode = config.get(SEC_RENDERING, 'display_mode', fallback=None)
    if display_mode is None:
        if legacy_prog is not None:
            _warn_deprecated('progressive_loading', "progressive_loading is deprecated; map it to rendering.display_mode.")
            if legacy_prog.lower() == 'true':
                display_mode = 'progressive'
            else:
                display_mode = 'monolithic'
        else:
            display_mode = 'progressive'
    config.set(SEC_RENDERING, 'display_mode', display_mode)

    if not config.has_section(SEC_SETTINGS):
        config.add_section(SEC_SETTINGS)
    raw_gap = config.get(SEC_SETTINGS, 'split_gap_limit', fallback=None)
    val_gap = 60
    if raw_gap is not None:
        try:
            val_gap = int(raw_gap)
        except ValueError:
            val_gap = 60
    config.set(SEC_SETTINGS, 'split_gap_limit', str(val_gap))

def resolve_wordfill_config(config, resolved_paths=None):
    """
    Parse and resolve wordfill configuration dictionary from config (ConfigParser or dict)
    and optional resolved_paths.
    Returns a dict with: enabled, scan_roots, scan_depth, scan_scope, scan_sort_order,
    scan_match_language, scan_max_files, target_quality, target_fallback, sqlite_db_path,
    backend, storage_backend.
    """
    base_dir = Path('.')
    if isinstance(resolved_paths, dict) and 'base_dir' in resolved_paths and resolved_paths['base_dir']:
        base_dir = Path(resolved_paths['base_dir'])

    storage_backend = 'tsv'
    if isinstance(resolved_paths, dict) and 'storage_backend' in resolved_paths and resolved_paths['storage_backend']:
        storage_backend = str(resolved_paths['storage_backend']).strip().lower()
    elif config is not None and SEC_STORAGE in config:
        st = config[SEC_STORAGE]
        storage_backend = str(st.get('backend', 'tsv') if hasattr(st, 'get') else st.get('backend', 'tsv')).strip().lower()

    wordfill = {
        'enabled': False,
        'scan_roots': [],
        'scan_depth': 1,
        'scan_scope': 'merged',
        'scan_sort_order': 'chronological',
        'scan_match_language': True,
        'scan_max_files': 500,
        'target_quality': 'any',
        'target_fallback': True,
        'sqlite_db_path': None,
        'backend': storage_backend,
        'storage_backend': storage_backend,
    }

    if config is None:
        return wordfill

    if SEC_WORDFILL in config:
        wf = config[SEC_WORDFILL]
        if hasattr(wf, 'getboolean'):
            wordfill['enabled'] = wf.getboolean('enabled', fallback=False)
            raw_roots = wf.get('scan_roots', '')
            wordfill['scan_depth'] = wf.getint('scan_depth', fallback=1)
            wordfill['scan_scope'] = wf.get('scan_scope', 'merged').strip().lower()
            wordfill['scan_sort_order'] = wf.get('scan_sort_order', 'chronological').strip().lower()
            wordfill['scan_match_language'] = wf.getboolean('scan_match_language', fallback=True)
            wordfill['scan_max_files'] = wf.getint('scan_max_files', fallback=500)
            wordfill['target_quality'] = wf.get('target_quality', 'any').strip().lower()
            wordfill['target_fallback'] = wf.getboolean('target_fallback', fallback=True)
        elif isinstance(wf, dict):
            wordfill['enabled'] = bool(wf.get('enabled', False))
            raw_roots = wf.get('scan_roots', '')
            wordfill['scan_depth'] = int(wf.get('scan_depth', 1))
            wordfill['scan_scope'] = str(wf.get('scan_scope', 'merged')).strip().lower()
            wordfill['scan_sort_order'] = str(wf.get('scan_sort_order', 'chronological')).strip().lower()
            wordfill['scan_match_language'] = bool(wf.get('scan_match_language', True))
            wordfill['scan_max_files'] = int(wf.get('scan_max_files', 500))
            wordfill['target_quality'] = str(wf.get('target_quality', 'any')).strip().lower()
            wordfill['target_fallback'] = bool(wf.get('target_fallback', True))
        else:
            raw_roots = ''

        parsed_roots = []
        if isinstance(raw_roots, list):
            for r in raw_roots:
                if not Path(r).is_absolute():
                    parsed_roots.append((base_dir / r).resolve())
                else:
                    parsed_roots.append(Path(r).resolve())
        elif isinstance(raw_roots, str):
            import re
            for raw_root in re.split(r'[\n,]+', raw_roots):
                raw_root = raw_root.strip()
                if not raw_root:
                    continue
                if not Path(raw_root).is_absolute():
                    parsed_roots.append((base_dir / raw_root).resolve())
                else:
                    parsed_roots.append(Path(raw_root).resolve())
        wordfill['scan_roots'] = parsed_roots

    # Resolve sqlite_db_path
    if isinstance(resolved_paths, dict) and 'sqlite_db_path' in resolved_paths and resolved_paths['sqlite_db_path']:
        wordfill['sqlite_db_path'] = Path(resolved_paths['sqlite_db_path']).resolve()
    elif SEC_STORAGE in config:
        st = config[SEC_STORAGE]
        db_p = st.get('sqlite_db_path', 'data/kardenwort.db') if hasattr(st, 'get') else st.get('sqlite_db_path', 'data/kardenwort.db')
        if db_p:
            db_p = str(db_p).strip()
            if not Path(db_p).is_absolute():
                wordfill['sqlite_db_path'] = (base_dir / db_p).resolve()
            else:
                wordfill['sqlite_db_path'] = Path(db_p).resolve()
    else:
        wordfill['sqlite_db_path'] = (base_dir / 'data' / 'kardenwort.db').resolve()

    return wordfill


def load_config(config_path=None):
    """
    Loads config.ini.
    Resolves relative paths starting with '../' or './' relative to the config file's location.
    Validates that all environment paths exist.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent / "config.ini"
    else:
        config_path = Path(config_path).resolve()
        
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
        
    config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    config.read(config_path, encoding='utf-8')
    
    base_dir = config_path.parent
    resolved_paths = {'base_dir': base_dir}
    
    # 1. Resolve environment paths
    if SEC_ENVIRONMENT in config:
        for key, value in config[SEC_ENVIRONMENT].items():
            if not Path(value).is_absolute():
                resolved_path = (base_dir / value).resolve()
            else:
                resolved_path = Path(value).resolve()
            resolved_paths[key] = resolved_path
            
    # Check each resolved path exists
    for key, path in resolved_paths.items():
        if key == 'base_dir':
            continue
        if not path.exists():
            raise ConfigError(f"Path configured for '{key}' does not exist: {path}")
            
    # 2. Settings paths
    if SEC_SETTINGS in config:
        # favorites_output_dir is relative to config.ini location
        fav_dir = config[SEC_SETTINGS].get('favorites_output_dir', './favorites')
        if fav_dir.startswith('../') or fav_dir.startswith('./'):
            resolved_paths['favorites_output_dir'] = (base_dir / fav_dir).resolve()
        else:
            resolved_paths['favorites_output_dir'] = Path(fav_dir).resolve()
            
        # anki_mapping_file is relative to config.ini location
        mapping_file = config[SEC_SETTINGS].get('anki_mapping_file', './anki-mapping.ini')
        if mapping_file.startswith('../') or mapping_file.startswith('./'):
            resolved_paths['anki_mapping_file'] = (base_dir / mapping_file).resolve()
        else:
            resolved_paths['anki_mapping_file'] = Path(mapping_file).resolve()
            
        if not resolved_paths['anki_mapping_file'].exists():
            raise ConfigError(f"anki_mapping_file path configured for 'anki_mapping_file' does not exist: {resolved_paths['anki_mapping_file']}")

    # 3. Project structure paths
    if SEC_PROJECT_STRUCTURE in config:
        res_dir = config[SEC_PROJECT_STRUCTURE].get('generated_results_dir')
        if res_dir:
            if Path(res_dir).is_absolute():
                resolved_paths['generated_results_dir'] = Path(res_dir).resolve()
            else:
                resolved_paths['generated_results_dir'] = (base_dir / res_dir).resolve()
            
    goldendict = {}
    if SEC_GOLDENDICT in config:
        gd = config[SEC_GOLDENDICT]
        goldendict['format'] = gd.get('format', 'html')
        goldendict['target_language'] = gd.get('target_language', config.get(SEC_SETTINGS, 'default_target_language', fallback='ru'))
        if not goldendict['target_language']:
            goldendict['target_language'] = config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')
        goldendict['run_intellifiller'] = gd.getboolean('run_intellifiller', fallback=False)
        goldendict['lookup_ttl_seconds'] = gd.getint('lookup_ttl_seconds', fallback=300)
        goldendict['theme'] = gd.get('theme', 'dark')
        goldendict['emit_meta_comment'] = gd.getboolean('emit_meta_comment', fallback=True)
        goldendict['disable_css'] = gd.getboolean('disable_css', fallback=False)
        
        raw_sections = gd.get('sections', 'translation,lemmas')
        goldendict['sections'] = parse_sections_list(raw_sections, ['source', 'translation', 'lemmas'])
        
        goldendict['heading_source'] = gd.get('heading_source', '')
        goldendict['heading_translation'] = gd.get('heading_translation', '')
        goldendict['heading_lemmas'] = gd.get('heading_lemmas', '')
        
        raw_columns = gd.get('lemma_columns', 'inflected,lemma,translation')
        goldendict['lemma_columns'] = parse_columns_list(raw_columns, ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
    else:
        goldendict['format'] = 'html'
        goldendict['target_language'] = config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')
        goldendict['run_intellifiller'] = False
        goldendict['lookup_ttl_seconds'] = 300
        goldendict['theme'] = 'dark'
        goldendict['emit_meta_comment'] = True
        goldendict['disable_css'] = False
        goldendict['sections'] = ['translation', 'lemmas']
        goldendict['heading_source'] = ''
        goldendict['heading_translation'] = ''
        goldendict['heading_lemmas'] = ''
        goldendict['lemma_columns'] = ['inflected', 'lemma', 'translation']

    if SEC_SERVER in config:
        srv = config[SEC_SERVER]
        goldendict['server_enabled'] = srv.getboolean('enabled', fallback=False)
        goldendict['server_host'] = srv.get('host', '127.0.0.1').strip()
        goldendict['server_port'] = srv.getint('port', fallback=18335)
        goldendict['server_api_key'] = srv.get('api_key', '').strip()
    else:
        goldendict['server_enabled'] = False
        goldendict['server_host'] = '127.0.0.1'
        goldendict['server_port'] = 18335
        goldendict['server_api_key'] = ''

    if SEC_LOOKUP in config:
        lk = config[SEC_LOOKUP]
        goldendict['sentence_match_strategy'] = lk.get('sentence_match_strategy', 'normalized').strip().lower()
        goldendict['allow_checksum_fallback'] = lk.getboolean('allow_checksum_fallback', fallback=True)
    else:
        goldendict['sentence_match_strategy'] = 'normalized'
        goldendict['allow_checksum_fallback'] = True

    # 4. Storage configuration
    if SEC_STORAGE in config:
        st = config[SEC_STORAGE]
        backend = st.get('backend', 'tsv').strip().lower()
        db_p = st.get('sqlite_db_path', 'data/kardenwort.db').strip()
        if not Path(db_p).is_absolute():
            resolved_paths['sqlite_db_path'] = (base_dir / db_p).resolve()
        else:
            resolved_paths['sqlite_db_path'] = Path(db_p).resolve()
        resolved_paths['storage_backend'] = backend
        resolved_paths['storage_fallback_to_tsv'] = st.getboolean('fallback_to_tsv', fallback=True)
    else:
        resolved_paths['storage_backend'] = 'tsv'
        resolved_paths['sqlite_db_path'] = (base_dir / 'data' / 'kardenwort.db').resolve()
        resolved_paths['storage_fallback_to_tsv'] = True

    wordfill = resolve_wordfill_config(config, resolved_paths)

    _migrate_config(config)
    _validate_translation_config(config)
    return config, resolved_paths, goldendict, wordfill


def load_kardenwort_config(kardenwort_workspace):
    kw_config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    if kardenwort_workspace:
        kw_config.read(Path(kardenwort_workspace) / "config.ini", encoding='utf-8')
    return kw_config

def resolve_results_dir(resolved_paths, kw_config):
    if not isinstance(resolved_paths, dict):
        return Path('results').resolve()
    if 'generated_results_dir' in resolved_paths and resolved_paths['generated_results_dir']:
        return Path(resolved_paths['generated_results_dir']).resolve()
    if 'results_dir' in resolved_paths and resolved_paths['results_dir']:
        return Path(resolved_paths['results_dir']).resolve()
    if 'kardenwort_workspace' in resolved_paths and resolved_paths['kardenwort_workspace']:
        kardenwort_workspace = Path(resolved_paths['kardenwort_workspace'])
        results_dir_name = kw_config.get(SEC_PROJECT_STRUCTURE, 'generated_results_dir', fallback='results') if kw_config and hasattr(kw_config, 'get') else 'results'
        return (kardenwort_workspace / results_dir_name).resolve()
    if 'base_dir' in resolved_paths and resolved_paths['base_dir']:
        return (Path(resolved_paths['base_dir']) / 'results').resolve()
    return Path('results').resolve()

@functools.lru_cache(maxsize=32)
def _load_anki_mapping_cached(mapping_str: str):
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str # Preserve case for Anki field names!
    mapping.read(mapping_str, encoding='utf-8')
    return mapping

def load_anki_mapping(mapping_path):
    if isinstance(mapping_path, (str, Path)):
        return _load_anki_mapping_cached(str(mapping_path))
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str # Preserve case for Anki field names!
    mapping.read(mapping_path, encoding='utf-8')
    return mapping

def build_field_mapping(mapping, mode):
    field_mapping = dict(mapping[f'fields_mapping.{mode}'])
    if 'tts' in mapping:
        field_mapping.update(dict(mapping['tts']))
    return field_mapping

def get_role_fields(mapping, headers):
    role_fields = {}
    headers_lower = {h.lower(): h for h in headers}
    if mapping and 'desk_columns' in mapping:
        for field, role in mapping['desk_columns'].items():
            field_lower = field.lower()
            if field_lower in headers_lower:
                role_fields[role] = headers_lower[field_lower]
                
    if 'lemma' not in role_fields:
        if 'wordsource' in headers_lower:
            role_fields['lemma'] = headers_lower['wordsource']
        elif 'lemma' in headers_lower:
            role_fields['lemma'] = headers_lower['lemma']
    if 'word_translation' not in role_fields:
        if 'worddestination' in headers_lower:
            role_fields['word_translation'] = headers_lower['worddestination']
        elif 'word_translation' in headers_lower:
            role_fields['word_translation'] = headers_lower['word_translation']
    if 'morphology' not in role_fields and 'wordsourcemorphologyai' in headers_lower:
        role_fields['morphology'] = headers_lower['wordsourcemorphologyai']
    if 'ipa' not in role_fields and 'wordsourceipa' in headers_lower:
        role_fields['ipa'] = headers_lower['wordsourceipa']
    if 'selected' not in role_fields and 'deskselected' in headers_lower:
        role_fields['selected'] = headers_lower['deskselected']
        
    sentence_index_col = headers_lower.get("sentencesourceindex", "SentenceSourceIndex")
    if mapping and 'fields_mapping.word' in mapping:
        for col, role in mapping['fields_mapping.word'].items():
            if role == 'sentence_index':
                sentence_index_col = col
                break
    role_fields['sentence_index'] = sentence_index_col
    
    if mapping and 'fields_mapping.sentence' in mapping:
        for col, role in mapping['fields_mapping.sentence'].items():
            if role == 'destination_sentence':
                role_fields['sentence_destination'] = col
            elif role == 'source_sentence':
                role_fields['sentence_source'] = col
                
    if 'sentence_source' not in role_fields and 'sentencesource' in headers_lower:
        role_fields['sentence_source'] = headers_lower['sentencesource']
    if 'sentence_destination' not in role_fields and 'sentencedestination' in headers_lower:
        role_fields['sentence_destination'] = headers_lower['sentencedestination']
        
    return role_fields

# Setup structured logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        }
        if hasattr(record, "zid"):
            log_data["zid"] = record.zid
        return json.dumps(log_data)

logger = logging.getLogger("kardenwort_desk")

class SessionLogger:
    def __init__(self, zid, results_dir, trace_id=None):
        if hasattr(zid, "_mock_name") or "Mock" in str(type(zid)):
            zid = "session_mock"
        self.zid = str(zid)
        self.results_dir = Path(results_dir)
        self.trace_id = str(trace_id) if trace_id and not ("Mock" in str(type(trace_id))) else f"{zid}:session"
        self.log_path = self.results_dir / f"{zid}.log"

    def _write_entry(self, level, message, trace_id=None):
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        tid = trace_id or self.trace_id
        entry = f"[{timestamp_str}] [{tid}] [{level.upper()}] {message}\n"
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"Failed to write to session log {self.log_path}: {e}")

    def info(self, message, trace_id=None):
        self._write_entry("INFO", message, trace_id)

    def warning(self, message, trace_id=None):
        self._write_entry("WARNING", message, trace_id)

    def error(self, message, trace_id=None):
        self._write_entry("ERROR", message, trace_id)

    def debug(self, message, trace_id=None):
        self._write_entry("DEBUG", message, trace_id)

def safe_write_update_js(tsv_path, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None, class_cols=None, empty_payload=False, config=None, error=None, zid=None, trace_id=None):
    import inspect
    kwargs = {
        "stage": stage,
        "status": status,
        "source_text": source_text,
        "translated_text": translated_text,
        "class_cols": class_cols,
        "empty_payload": empty_payload,
        "config": config,
        "error": error,
        "zid": zid,
        "trace_id": trace_id,
    }
    try:
        sig = inspect.signature(write_update_js)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if has_varkw:
            filtered_kwargs = kwargs
        else:
            accepted_params = set(sig.parameters.keys())
            filtered_kwargs = {k: v for k, v in kwargs.items() if k in accepted_params}
        return write_update_js(tsv_path, data_rows, headers, role_fields, **filtered_kwargs)
    except Exception:
        try:
            return write_update_js(tsv_path, data_rows, headers, role_fields, stage=stage, status=status)
        except Exception:
            return write_update_js(tsv_path, data_rows, headers, role_fields)

class TranslationException(Exception):
    def __init__(self, message, envelope=None):
        super().__init__(message)
        self.envelope = envelope or {}
        self.code = self.envelope.get("code", "ERR_TRANSLATION_FAILED")
        self.message = message
        self.details = self.envelope.get("details", {})

def setup_logging(verbose=False, debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    if debug:
        logger.setLevel(logging.DEBUG)
    elif verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)

def get_deepl_key(config, base_dir):
    deepl_settings_file_val = config.get(SEC_ENVIRONMENT, 'deepl_settings_file', fallback=None)
    if not deepl_settings_file_val:
        return None
        
    settings_path = (base_dir / deepl_settings_file_val).resolve()
    if not settings_path.exists():
        logger.warning(f"DeepL settings file not found: {settings_path}")
        return None
        
    settings = configparser.ConfigParser()
    read_success = False
    for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16le', 'cp1252'):
        try:
            settings.read(settings_path, encoding=enc)
            read_success = True
            break
        except Exception:
            continue
    if not read_success:
        logger.error(f"Failed to decode DeepL settings file {settings_path}")
        return None
    
    salt = settings.get('Security', 'Salt', fallback='')
    secrets_path_val = settings.get('Security', 'SecretsPath', fallback='')
    if not secrets_path_val:
        return None
        
    secrets_path = (settings_path.parent / secrets_path_val).resolve()
    if not secrets_path.exists():
        logger.warning(f"DeepL secrets file not found: {secrets_path}")
        return None
        
    secrets = configparser.ConfigParser()
    read_success = False
    for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16le', 'cp1252'):
        try:
            secrets.read(secrets_path, encoding=enc)
            read_success = True
            break
        except Exception:
            continue
    if not read_success:
        logger.error(f"Failed to decode DeepL secrets file {secrets_path}")
        return None
    
    obfuscated_key = secrets.get('DeepL', 'Key', fallback='')
    if not obfuscated_key:
        return None
        
    import base64
    try:
        decoded_bytes = base64.b64decode(obfuscated_key)
        if not salt:
            return obfuscated_key
            
        salt_bytes = salt.encode('utf-8')
        deobfuscated_bytes = bytearray()
        for i, b in enumerate(decoded_bytes):
            deobfuscated_bytes.append(b ^ salt_bytes[i % len(salt_bytes)])
            
        key_str = deobfuscated_bytes.decode('utf-8', errors='replace')
        if key_str.startswith('%%SEC%%'):
            return key_str[7:]
        else:
            return obfuscated_key
    except Exception as e:
        logger.warning(f"Error deobfuscating DeepL key: {e}. Using raw key.")
        return obfuscated_key

# ---------------------------------------------------------------------------
# Storage Backend Router & Adapters (TSV / SQLite)
# ---------------------------------------------------------------------------
class StorageAdapter:
    """
    Abstract base class for Kardenwort storage backends (TSV flat-files, SQLite database).
    Encapsulates session persistence, caching, row loading/saving, and concurrency synchronization.
    """
    backend_name: str = "base"

    def save_session(
        self,
        session_zid: str,
        slug: str,
        source_language: str,
        target_language: str,
        text_mode: str,
        source_raw_text: str,
        sentences: Optional[List[Dict[str, Any]]] = None,
        words: Optional[List[Dict[str, Any]]] = None,
        comments: Optional[List[str]] = None,
        headers: Optional[List[str]] = None,
        data_rows: Optional[List[List[str]]] = None,
        working_tsv_path: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Any:
        raise NotImplementedError

    def load_session(
        self,
        session_zid: str,
        working_tsv_path: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_cached_session(
        self,
        slug: str,
        source_language: str,
        lookup_ttl_seconds: int,
        results_dir: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Optional[Any]:
        raise NotImplementedError

    def restore_session(
        self,
        zid: str,
        results_dir: Optional[Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def save_tsv_rows_safely(
        self,
        tsv_path: Path,
        comments: List[str],
        headers: List[str],
        data_rows: List[List[str]],
        **kwargs,
    ) -> None:
        raise NotImplementedError

    def load_tsv_rows(
        self,
        tsv_path: Path,
        **kwargs,
    ) -> Tuple[List[str], List[str], List[List[str]]]:
        raise NotImplementedError

    @contextlib.contextmanager
    def file_lock(self, file_path: Path):
        raise NotImplementedError

    def update_word(
        self,
        session_zid: str,
        sentence_idx: Optional[int],
        token_order: int,
        field: str,
        value: Any,
        zid: Optional[str] = None,
    ) -> bool:
        return False

    def update_word_selection(
        self,
        session_zid: str,
        sentence_idx: Optional[int],
        token_order: int,
        selected: Union[int, bool, str],
        zid: Optional[str] = None,
    ) -> bool:
        return False

    def update_sentence_translation(
        self,
        session_zid: str,
        sentence_index: int,
        text: str,
        target_field: str = "sentence_destination",
        zid: Optional[str] = None,
    ) -> bool:
        return False

    def batch_update_words(
        self,
        session_zid: str,
        updates_list: List[Dict[str, Any]],
        zid: Optional[str] = None,
    ) -> int:
        return 0

    def delete_session(self, session_zid: str, zid: Optional[str] = None) -> bool:
        return False

    def list_sessions(
        self, limit: Optional[int] = None, offset: int = 0, zid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return []

    def cleanup_db(self, older_than_days: float, zid: Optional[str] = None) -> int:
        return 0

    def vacuum(self, zid: Optional[str] = None) -> bool:
        return False

    def find_sentence_by_strategy(
        self,
        sentence_text: str,
        language: Optional[str] = None,
        target_language: Optional[str] = None,
        strategy: str = "normalized",
        allow_fallback: bool = True,
        exclude_zid: Optional[str] = None,
        limit: int = 10,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return []


class TsvStorageAdapter(StorageAdapter):
    """
    Legacy flat-file TSV storage adapter preserving existing file I/O, file locks,
    and results/*.tsv directory caching behavior.
    """
    backend_name: str = "tsv"

    def __init__(self, config=None, resolved_paths=None):
        self.config = config
        self.resolved_paths = resolved_paths or {}

    def save_session(
        self,
        session_zid: str,
        slug: str,
        source_language: str,
        target_language: str,
        text_mode: str,
        source_raw_text: str,
        sentences: Optional[List[Dict[str, Any]]] = None,
        words: Optional[List[Dict[str, Any]]] = None,
        comments: Optional[List[str]] = None,
        headers: Optional[List[str]] = None,
        data_rows: Optional[List[List[str]]] = None,
        working_tsv_path: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Any:
        if working_tsv_path and headers is not None and data_rows is not None:
            self.save_tsv_rows_safely(working_tsv_path, comments or [], headers, data_rows)
            return working_tsv_path
        return None

    def load_session(
        self,
        session_zid: str,
        working_tsv_path: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        if working_tsv_path and working_tsv_path.exists():
            comments, headers, data_rows = self.load_tsv_rows(working_tsv_path)
            return {
                "session_zid": session_zid,
                "comments": comments,
                "headers": headers,
                "data_rows": data_rows,
                "tsv_path": working_tsv_path,
            }
        return None

    def restore_session(
        self,
        zid: str,
        results_dir: Optional[Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        if not zid or str(zid).strip() in ("", "00000000000000"):
            raise StructuredError(
                ErrorCode.INVALID_STATE,
                f"Invalid session ZID '{zid}' for session restoration.",
            )

        target_dir = results_dir
        if not target_dir:
            if self.resolved_paths and "results_dir" in self.resolved_paths:
                target_dir = Path(self.resolved_paths["results_dir"])
            elif self.resolved_paths and "base_dir" in self.resolved_paths:
                target_dir = Path(self.resolved_paths["base_dir"]) / "results"
            else:
                target_dir = Path("results")

        tsv_files = list(target_dir.glob(f"{zid}-*.tsv")) if target_dir.exists() else []
        if not tsv_files and target_dir.exists():
            tsv_files = list(target_dir.glob(f"{zid}*.tsv"))

        if not tsv_files:
            raise StructuredError(
                ErrorCode.NOT_FOUND,
                f"Legacy session TSV for ZID '{zid}' not found in {target_dir}.",
            )

        tsv_path = tsv_files[0]
        comments, headers, data_rows = self.load_tsv_rows(tsv_path)

        txt_files = list(target_dir.glob(f"{zid}-*.txt")) if target_dir.exists() else []
        if not txt_files and target_dir.exists():
            txt_files = list(target_dir.glob(f"{zid}*.txt"))
        txt_path = txt_files[0] if txt_files else None

        source_text = ""
        if txt_path and txt_path.exists():
            try:
                source_text = txt_path.read_text(encoding="utf-8")
            except Exception:
                pass

        return {
            "session_zid": zid,
            "source_text": source_text,
            "comments": comments,
            "headers": headers,
            "data_rows": data_rows,
            "tsv_path": tsv_path,
            "txt_path": txt_path,
        }

    def get_cached_session(
        self,
        slug: str,
        source_language: str,
        lookup_ttl_seconds: int,
        results_dir: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Optional[Path]:
        if lookup_ttl_seconds <= 0 or not results_dir or not results_dir.exists():
            return None
        now = time.time()
        for cached_file in results_dir.glob(f"*-{slug}.{source_language}.tsv"):
            if cached_file.is_file():
                if (now - cached_file.stat().st_mtime) <= lookup_ttl_seconds:
                    return cached_file
        return None

    def save_tsv_rows_safely(
        self,
        tsv_path: Path,
        comments: List[str],
        headers: List[str],
        data_rows: List[List[str]],
        **kwargs,
    ) -> None:
        temp_path = tsv_path.with_suffix('.tsv.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8', newline='') as f:
                import csv
                writer = csv.writer(f, delimiter='\t', lineterminator='\n')
                for comment in comments:
                    f.write(comment + '\n')
                writer.writerow(headers)
                for row in data_rows:
                    sanitized_row = [str(cell).replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ') for cell in row]
                    writer.writerow(sanitized_row)
            os.replace(temp_path, tsv_path)
        except Exception as e:
            if temp_path.exists():
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise e

    def load_tsv_rows(
        self,
        tsv_path: Path,
        **kwargs,
    ) -> Tuple[List[str], List[str], List[List[str]]]:
        import csv
        comments = []
        headers = []
        data_rows = []
        lines_to_parse = []
        with open(tsv_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not headers and not lines_to_parse and line.startswith('#'):
                    comments.append(line.rstrip('\r\n'))
                else:
                    lines_to_parse.append(line)
        reader = csv.reader(lines_to_parse, delimiter='\t')
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            else:
                data_rows.append(row)
        return comments, headers, data_rows

    @contextlib.contextmanager
    def file_lock(self, file_path: Path):
        lock_file_path = file_path.with_suffix('.lock')
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_file_path, 'w')
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()
            try:
                os.remove(lock_file_path)
            except Exception:
                pass


class SqliteStorageAdapter(StorageAdapter):
    """
    Relational SQLite storage adapter backed by KardenwortDB (WAL mode, transactions).
    Persists normalized session metadata, single-copy sentences, and word tokens.
    """
    backend_name: str = "sqlite"

    def __init__(self, config=None, resolved_paths=None, db_path=None):
        self.config = config
        self.resolved_paths = resolved_paths or {}
        from kardenwort_db import KardenwortDB
        self.db = KardenwortDB(
            db_path=db_path or self.resolved_paths.get("sqlite_db_path") or self.resolved_paths.get("db_path"),
            config=config,
            resolved_paths=self.resolved_paths,
        )
        self.db.run_migrations()
        self._tsv_fallback = TsvStorageAdapter(config=config, resolved_paths=resolved_paths)

    def save_session(
        self,
        session_zid: str,
        slug: str = "",
        source_language: str = "",
        target_language: str = "",
        text_mode: str = "single",
        source_raw_text: str = "",
        sentences: Optional[List[Dict[str, Any]]] = None,
        words: Optional[List[Dict[str, Any]]] = None,
        comments: Optional[List[str]] = None,
        headers: Optional[List[str]] = None,
        data_rows: Optional[List[List[str]]] = None,
        working_tsv_path: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Any:
        if not session_zid or str(session_zid).strip() in ("", "00000000000000"):
            raise StructuredError(
                ErrorCode.INVALID_STATE,
                f"Invalid session ZID '{session_zid}' for SQLite storage persistence.",
            )

        existing_sess = None
        try:
            bundle = self.db.get_session_bundle(session_zid, zid=zid)
            if bundle and bundle.get("session"):
                existing_sess = bundle["session"]
        except Exception:
            existing_sess = None

        if existing_sess:
            slug = slug or existing_sess.get("slug", "")
            source_language = source_language or existing_sess.get("source_language", "")
            target_language = target_language or existing_sess.get("target_language", "")
            text_mode = text_mode if (text_mode != "single" or "text_mode" not in existing_sess) else existing_sess.get("text_mode", "single")
            source_raw_text = source_raw_text or existing_sess.get("source_raw_text", "")

        now_iso = datetime.now(timezone.utc).isoformat()
        session_record = {
            "zid": session_zid,
            "slug": slug or "",
            "source_language": source_language or "",
            "target_language": target_language or "",
            "text_mode": text_mode or "single",
            "source_raw_text": source_raw_text or "",
            "created_at": existing_sess.get("created_at", now_iso) if existing_sess else now_iso,
            "updated_at": now_iso,
        }

        # If sentences and words are provided directly
        norm_sentences = [{**dict(s), "session_zid": session_zid} for s in sentences] if sentences is not None else []
        norm_words = list(words) if words is not None else []

        # If headers and data_rows are provided, normalize into sentences and words
        if headers is not None and data_rows is not None and not (sentences and words):
            headers_lower = {h.lower(): idx for idx, h in enumerate(headers)}

            def get_col_val(r: List[str], col_name: str, default: str = "") -> str:
                idx = headers_lower.get(col_name.lower())
                return r[idx] if idx is not None and idx < len(r) else default

            col_sent_idx = headers_lower.get("sentencesourceindex")
            col_sent_src = headers_lower.get("sentencesource")
            col_sent_dst = headers_lower.get("sentencedestination")
            col_sent_dst2 = headers_lower.get("sentencedestination2")
            col_sent_ipa = headers_lower.get("sentencesourceipa")
            col_sent_aud = headers_lower.get("sentencesourceaudio")

            known_word_cols = {
                "quotation", "wordsource", "wordsourceinflectedform",
                "worddestination", "worddestinationinflectedform",
                "wordsourcemorphologyai", "wordsourceipa", "deskselected", "leitnerbox",
                "leitnerdue", "deck", "classificationoxford", "classificationgoethe",
                "sentencesourceindex", "sentencesource", "sentencedestination",
                "sentencedestination2", "sentencesourceipa", "sentencesourceaudio",
                "sentencesourcecontextleft", "sentencesourcecontextright",
                "sentencedestinationcontextleft", "sentencedestinationcontextright",
                "sentencedestination2contextleft", "sentencedestination2contextright",
            }

            existing_sents_by_idx = {}
            try:
                ex_sents = self.db.get_sentences_by_session(session_zid, zid=zid)
                if ex_sents:
                    existing_sents_by_idx = {s["sentence_index"]: dict(s) for s in ex_sents}
            except Exception:
                existing_sents_by_idx = {}

            sent_map: Dict[int, Dict[str, Any]] = {}
            word_list: List[Dict[str, Any]] = []

            for row_idx, row in enumerate(data_rows):
                # Extract sentence index
                s_idx = 0
                if col_sent_idx is not None and col_sent_idx < len(row):
                    raw_s_idx = str(row[col_sent_idx]).strip()
                    if raw_s_idx.isdigit():
                        s_idx = int(raw_s_idx)
                    else:
                        s_idx = row_idx
                elif existing_sents_by_idx:
                    # Fallback to existing sentence index if available
                    sorted_existing_keys = sorted(existing_sents_by_idx.keys())
                    if row_idx < len(sorted_existing_keys):
                        s_idx = sorted_existing_keys[row_idx]
                    else:
                        s_idx = sorted_existing_keys[-1]

                existing_sent = existing_sents_by_idx.get(s_idx, {})
                src_val = row[col_sent_src] if col_sent_src is not None and col_sent_src < len(row) and str(row[col_sent_src]).strip() else existing_sent.get("sentence_source", "")
                dst_val = row[col_sent_dst] if col_sent_dst is not None and col_sent_dst < len(row) and str(row[col_sent_dst]).strip() else existing_sent.get("sentence_destination")
                dst2_val = row[col_sent_dst2] if col_sent_dst2 is not None and col_sent_dst2 < len(row) and str(row[col_sent_dst2]).strip() else existing_sent.get("sentence_destination2")
                ipa_val = row[col_sent_ipa] if col_sent_ipa is not None and col_sent_ipa < len(row) and str(row[col_sent_ipa]).strip() else existing_sent.get("sentence_source_ipa")
                aud_val = row[col_sent_aud] if col_sent_aud is not None and col_sent_aud < len(row) and str(row[col_sent_aud]).strip() else existing_sent.get("sentence_source_audio")

                if src_val or s_idx in existing_sents_by_idx:
                    if s_idx not in sent_map:
                        sent_map[s_idx] = {
                            "session_zid": session_zid,
                            "sentence_index": s_idx,
                            "sentence_source": src_val,
                            "sentence_destination": dst_val,
                            "sentence_destination2": dst2_val,
                            "sentence_source_ipa": ipa_val,
                            "sentence_source_audio": aud_val,
                        }

                # Extract word fields
                quotation = get_col_val(row, "quotation") or get_col_val(row, "wordsourceinflectedform") or get_col_val(row, "wordsource") or ""
                lemma = get_col_val(row, "wordsource")
                inflected = get_col_val(row, "wordsourceinflectedform")
                morph = get_col_val(row, "wordsourcemorphologyai")
                ipa = get_col_val(row, "wordsourceipa")
                w_dest = get_col_val(row, "worddestination")
                w_dest_inf = get_col_val(row, "worddestinationinflectedform")
                sel_raw = get_col_val(row, "deskselected")
                selected = 1 if str(sel_raw).strip() in ("1", "true", "True") else 0
                box_raw = get_col_val(row, "leitnerbox")
                box = int(box_raw) if box_raw.isdigit() else 1
                due = get_col_val(row, "leitnerdue") or None
                deck = get_col_val(row, "deck") or None
                oxford = get_col_val(row, "classificationoxford") or None
                goethe = get_col_val(row, "classificationgoethe") or None

                # Collect extra fields
                extra: Dict[str, Any] = {}
                for h_idx, h_name in enumerate(headers):
                    if h_name.lower() not in known_word_cols and h_idx < len(row):
                        val = row[h_idx]
                        if val:
                            extra[h_name] = val

                t_ord_raw = get_col_val(row, "tokenorder")
                token_order = int(t_ord_raw) if (t_ord_raw is not None and str(t_ord_raw).strip().isdigit()) else row_idx

                word_entry = {
                    "session_zid": session_zid,
                    "sentence_index": s_idx,
                    "token_order": token_order,
                    "quotation": quotation,
                    "inflected_form": inflected or None,
                    "lemma": lemma,
                    "pos": None,
                    "morphology": morph or None,
                    "ipa": ipa or None,
                    "word_destination": w_dest or None,
                    "word_destination_inflected": w_dest_inf or None,
                    "selected": selected,
                    "leitner_box": box,
                    "leitner_due": due,
                    "deck": deck,
                    "classification_oxford": oxford,
                    "classification_goethe": goethe,
                    "extra_fields": extra if extra else None,
                }
                word_list.append(word_entry)

            if sentences is not None:
                norm_sentences = [{**dict(s), "session_zid": session_zid} for s in sentences]
            elif not sent_map:
                try:
                    existing_sents = self.db.get_sentences_by_session(session_zid, zid=zid)
                    if existing_sents:
                        norm_sentences = [dict(s) for s in existing_sents]
                    else:
                        norm_sentences = []
                except Exception:
                    norm_sentences = []
            else:
                norm_sentences = list(sent_map.values())
            norm_words = word_list

        with TraceTimer("sqlite_save", session_zid, self.config, self.resolved_paths):
            self.db.save_session_bundle(
                session=session_record,
                sentences=norm_sentences,
                words=norm_words,
                zid=zid or session_zid,
            )
            # SQLite persistence is complete and authoritative (zero mirror TSV written to results/)

        return session_zid

    def load_session(
        self,
        session_zid: str,
        working_tsv_path: Optional[Path] = None,
        zid: Optional[str] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        bundle = self.db.get_session_bundle(session_zid, zid=zid)
        if bundle:
            return bundle
        if working_tsv_path and working_tsv_path.exists():
            return self._tsv_fallback.load_session(session_zid, working_tsv_path=working_tsv_path, zid=zid)
        return None

    def restore_session(
        self,
        zid: str,
        results_dir: Optional[Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        if not zid or str(zid).strip() in ("", "00000000000000"):
            raise StructuredError(
                ErrorCode.INVALID_STATE,
                f"Invalid session ZID '{zid}' for SQLite session restoration.",
            )

        with TraceTimer("sqlite_restore", zid, self.config, self.resolved_paths):
            bundle = self.db.get_session_bundle(zid, parse_json=True, zid=zid)

            if not bundle or not bundle.get("session"):
                fallback = True
                if self.resolved_paths and "storage_fallback_to_tsv" in self.resolved_paths:
                    fallback = self.resolved_paths["storage_fallback_to_tsv"]
                elif self.config and hasattr(self.config, "getboolean"):
                    fallback = self.config.getboolean(SEC_STORAGE, "fallback_to_tsv", fallback=True)

                if fallback:
                    logger.info(f"Session ZID '{zid}' not found in SQLite. Falling back to legacy TSV.")
                    return self._tsv_fallback.restore_session(zid, results_dir=results_dir, **kwargs)
                else:
                    raise StructuredError(
                        ErrorCode.NOT_FOUND,
                        f"Session with ZID '{zid}' not found in SQLite database.",
                    )

            session = bundle["session"]
            db_sentences = bundle.get("sentences", [])
            db_words = bundle.get("words", [])

            source_text = session.get("source_raw_text", "") or ""
            if not source_text:
                # Fallback: reconstruct from sentences.sentence_source in sentence_index order
                sentence_parts = [
                    s["sentence_source"]
                    for s in sorted(db_sentences, key=lambda s: s.get("sentence_index", 0))
                    if s.get("sentence_source")
                ]
                source_text = " ".join(sentence_parts)
            comments = [f"# Restored from SQLite session {zid}"]

            # Load headers from anki-mapping.ini [fields]
            headers = []
            mapping_path = None
            if self.resolved_paths and "anki_mapping_file" in self.resolved_paths:
                mapping_path = Path(self.resolved_paths["anki_mapping_file"])
            elif self.config and hasattr(self.config, "get"):
                raw_mp = self.config.get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
                mapping_path = Path(raw_mp)

            if mapping_path and mapping_path.exists():
                try:
                    mapping = load_anki_mapping(mapping_path)
                    if "fields" in mapping:
                        headers = list(mapping["fields"].keys())
                    if "desk_columns" in mapping:
                        existing_h = {h.lower() for h in headers}
                        for dc in mapping["desk_columns"].keys():
                            if dc.lower() not in existing_h:
                                headers.append(dc)
                                existing_h.add(dc.lower())
                except Exception:
                    pass

            if not headers:
                headers = [
                    "Quotation", "WordSource", "WordSource2", "WordSourceInflectedForm", "WordSourceInflectedForm2",
                    "WordDestination", "WordDestinationInflectedForm", "WordSourceContext", "SentenceSourceContextLeft",
                    "SentenceSource", "SentenceSourceContextRight", "SentenceDestinationContextLeft", "SentenceDestination",
                    "SentenceDestinationContextRight", "SentenceDestination2ContextLeft", "SentenceDestination2",
                    "SentenceDestination2ContextRight", "SentenceSourceWordlist", "SentenceSourceCloze",
                    "SentenceSourceRewriteAISentenceSource", "SentenceSourceRewriteAISentenceDestination",
                    "WordSourceMorphologyAI", "Note", "WordRussian", "WordUkrainian", "WordEnglish", "WordGerman",
                    "WordSourceMorphemeFirst", "WordSourceMorphemeFirstDefinition", "WordSourceMorphemeSecond",
                    "WordSourceMorphemeSecondDefinition", "WordSourceMorphemeThird", "WordSourceMorphemeThirdDefinition",
                    "WordSourceMorphemeFourth", "WordSourceMorphemeFourthDefinition", "WordSourceMorphemeFifth",
                    "WordSourceMorphemeFifthDefinition", "WordSourceIPA", "WordSourceSynonymAI",
                    "WordSourceDefinitionAISentenceSource", "WordSourceDefinitionAISentenceDestination",
                    "WordSourceDefinitionFirst", "WordSourceDefinitionFirstClipping", "WordSourceDefinitionSecond",
                    "WordDestinationDefinitionFirst", "WordDestinationDefinitionSecond", "WordSourceAudio",
                    "SentenceSourceIPA", "SentenceSourceAudio", "Image", "WordSourceCloze", "WordSourceContextAI",
                    "TextSource", "TextDestination", "TextSourceURL", "SentenceEnglish", "SentenceGerman",
                    "SentenceUkrainian", "SentenceRussian", "Source", "SourceURL", "SeparatorAudio",
                    "Source-en-GB", "Source-en-US", "Source-de-DE", "Source-uk-UA", "Source-ru-RU",
                    "Destination-en-GB", "Destination-en-US", "Destination-de-DE", "Destination-uk-UA",
                    "Destination-ru-RU", "Overlapping", "ToggleAlwaysEmptyField", "Note ID",
                    "am-all-morphs", "am-all-morphs-count", "am-unknown-morphs", "am-unknown-morphs-count",
                    "am-highlighted", "am-score", "am-score-terms", "am-study-morphs",
                    "SentenceSourceIndex", "Deck", "LeitnerBox", "LeitnerDue", "DeskSelected",
                    "ClassificationOxford", "ClassificationGoethe",
                ]
            else:
                existing_h = {h.lower() for h in headers}
                if db_sentences or any(w.get("sentence_index") for w in db_words):
                    if "sentencesourceindex" not in existing_h:
                        headers.append("SentenceSourceIndex")
                        existing_h.add("sentencesourceindex")
                    if "sentencesource" not in existing_h:
                        headers.append("SentenceSource")
                        existing_h.add("sentencesource")

            # Preserve and append any unmapped custom columns found in extra_fields
            extra_headers = []
            for word in db_words:
                ef = word.get("extra_fields")
                if isinstance(ef, str):
                    try:
                        ef = json.loads(ef)
                    except Exception:
                        ef = {}
                if isinstance(ef, dict):
                    for k in ef.keys():
                        if not any(h.lower() == k.lower() for h in headers) and not any(eh.lower() == k.lower() for eh in extra_headers):
                            extra_headers.append(k)
            headers.extend(extra_headers)

            # Dynamic Context Reconstruction
            context_window_left = 1
            context_window_right = 1
            if self.config and hasattr(self.config, "getint"):
                try:
                    context_window_left = max(1, self.config.getint(SEC_SETTINGS, "context_window_left", fallback=1))
                except Exception:
                    context_window_left = 1
                try:
                    context_window_right = max(1, self.config.getint(SEC_SETTINGS, "context_window_right", fallback=1))
                except Exception:
                    context_window_right = 1

            sentences_by_idx = {row["sentence_index"]: row for row in db_sentences}
            sorted_sent_indices = sorted(sentences_by_idx.keys()) if sentences_by_idx else [1]
            max_s_idx = sorted_sent_indices[-1] if sorted_sent_indices else 1
            min_s_idx = sorted_sent_indices[0] if sorted_sent_indices else 1

            # Deduplicate words by sentence_index, token_order, quotation, lemma
            seen_tokens = set()
            unique_db_words = []
            for word in db_words:
                s_idx = word.get("sentence_index", 1)
                t_ord = word.get("token_order", 0)
                q_txt = str(word.get("quotation") or "").strip().lower()
                l_txt = str(word.get("lemma") or "").strip().lower()
                token_key = (s_idx, t_ord, q_txt, l_txt)
                if token_key in seen_tokens:
                    continue
                seen_tokens.add(token_key)
                unique_db_words.append(word)

            data_rows = []
            for word in unique_db_words:
                s_idx = word.get("sentence_index", 1)
                sent_record = sentences_by_idx.get(s_idx, {})

                left_indices = [j for j in range(max(min_s_idx, s_idx - context_window_left), s_idx) if j in sentences_by_idx]
                right_indices = [j for j in range(s_idx + 1, min(max_s_idx + 1, s_idx + context_window_right + 1)) if j in sentences_by_idx]

                src_left_parts = [sentences_by_idx[j]["sentence_source"] for j in left_indices if sentences_by_idx[j].get("sentence_source")]
                src_right_parts = [sentences_by_idx[j]["sentence_source"] for j in right_indices if sentences_by_idx[j].get("sentence_source")]

                dst_left_parts = [sentences_by_idx[j]["sentence_destination"] for j in left_indices if sentences_by_idx[j].get("sentence_destination")]
                dst_right_parts = [sentences_by_idx[j]["sentence_destination"] for j in right_indices if sentences_by_idx[j].get("sentence_destination")]

                dst2_left_parts = [sentences_by_idx[j]["sentence_destination2"] for j in left_indices if sentences_by_idx[j].get("sentence_destination2")]
                dst2_right_parts = [sentences_by_idx[j]["sentence_destination2"] for j in right_indices if sentences_by_idx[j].get("sentence_destination2")]

                src_left = " ".join(src_left_parts)
                src_right = " ".join(src_right_parts)
                src_curr = sent_record.get("sentence_source") or ""

                dst_left = " ".join(dst_left_parts)
                dst_right = " ".join(dst_right_parts)
                dst_curr = sent_record.get("sentence_destination") or ""

                dst2_left = " ".join(dst2_left_parts)
                dst2_right = " ".join(dst2_right_parts)
                dst2_curr = sent_record.get("sentence_destination2") or ""

                sent_ipa = sent_record.get("sentence_source_ipa") or ""
                sent_audio = sent_record.get("sentence_source_audio") or ""

                extra_fields = word.get("extra_fields") or {}
                if isinstance(extra_fields, str):
                    try:
                        extra_fields = json.loads(extra_fields)
                    except Exception:
                        extra_fields = {}

                row_cells = []
                for h in headers:
                    h_lower = h.lower()
                    if h_lower == "quotation":
                        row_cells.append(str(word.get("quotation") or ""))
                    elif h_lower in ("wordsource", "lemma"):
                        row_cells.append(str(word.get("lemma") or ""))
                    elif h_lower == "wordsource2":
                        val = extra_fields.get(h) if h in extra_fields else (word.get("lemma") or "")
                        row_cells.append(str(val or ""))
                    elif h_lower in ("wordsourceinflectedform", "inflected_form", "inflectedform"):
                        row_cells.append(str(word.get("inflected_form") or ""))
                    elif h_lower == "wordsourceinflectedform2":
                        val = extra_fields.get(h) if h in extra_fields else (word.get("inflected_form") or "")
                        row_cells.append(str(val or ""))
                    elif h_lower in ("worddestination", "word_destination", "word_translation"):
                        w_dest = str(word.get("word_destination") or "")
                        if w_dest.strip() == "[FAILED]":
                            w_dest = ""
                        row_cells.append(w_dest)
                    elif h_lower in ("worddestinationinflectedform", "word_destination_inflected"):
                        w_dest_inf = str(word.get("word_destination_inflected") or "")
                        if w_dest_inf.strip() == "[FAILED]":
                            w_dest_inf = ""
                        row_cells.append(w_dest_inf)
                    elif h_lower == "tokenorder":
                        row_cells.append(str(word.get("token_order", 0)))
                    elif h_lower in ("wordsourcemorphologyai", "morphology", "morphologyai"):
                        row_cells.append(str(word.get("morphology") or ""))
                    elif h_lower in ("wordsourceipa", "ipa"):
                        row_cells.append(str(word.get("ipa") or ""))
                    elif h_lower in ("deskselected", "selected"):
                        row_cells.append(str(word.get("selected", 0)))
                    elif h_lower in ("leitnerbox", "leitner_box"):
                        row_cells.append(str(word.get("leitner_box", 1)))
                    elif h_lower in ("leitnerdue", "leitner_due"):
                        row_cells.append(str(word.get("leitner_due") or ""))
                    elif h_lower == "deck":
                        row_cells.append(str(word.get("deck") or ""))
                    elif h_lower in ("classificationoxford", "classification_oxford"):
                        row_cells.append(str(word.get("classification_oxford") or ""))
                    elif h_lower in ("classificationgoethe", "classification_goethe"):
                        row_cells.append(str(word.get("classification_goethe") or ""))
                    elif h_lower == "sentencesourceindex":
                        row_cells.append(str(s_idx))
                    elif h_lower == "sentencesourcecontextleft":
                        row_cells.append(src_left)
                    elif h_lower == "sentencesource":
                        row_cells.append(src_curr)
                    elif h_lower == "sentencesourcecontextright":
                        row_cells.append(src_right)
                    elif h_lower == "sentencedestinationcontextleft":
                        row_cells.append(dst_left)
                    elif h_lower == "sentencedestination":
                        row_cells.append(dst_curr)
                    elif h_lower == "sentencedestinationcontextright":
                        row_cells.append(dst_right)
                    elif h_lower == "sentencedestination2contextleft":
                        row_cells.append(dst2_left)
                    elif h_lower == "sentencedestination2":
                        row_cells.append(dst2_curr)
                    elif h_lower == "sentencedestination2contextright":
                        row_cells.append(dst2_right)
                    elif h_lower == "sentencesourceipa":
                        row_cells.append(sent_ipa)
                    elif h_lower == "sentencesourceaudio":
                        row_cells.append(sent_audio)
                    else:
                        val = extra_fields.get(h)
                        if val is None:
                            for k, v in extra_fields.items():
                                if k.lower() == h_lower:
                                    val = v
                                    break
                        row_cells.append(str(val) if val is not None else "")

                data_rows.append(row_cells)

            sentence_trans_parts = []
            for s in db_sentences:
                st = (s.get("sentence_destination") or s.get("sentence_destination2") or "").strip()
                if st:
                    sentence_trans_parts.append(st)
            sentence_translation = "\n".join(sentence_trans_parts)

            return {
                "session_zid": zid,
                "source_text": source_text,
                "sentence_translation": sentence_translation,
                "source_language": session.get("source_language", ""),
                "target_language": session.get("target_language", ""),
                "comments": comments,
                "headers": headers,
                "data_rows": data_rows,
                "session": session,
                "sentences": db_sentences,
                "words": unique_db_words,
            }

    def get_cached_session(
        self,
        slug: str,
        source_language: str,
        lookup_ttl_seconds: int,
        results_dir: Optional[Path] = None,
        zid: Optional[str] = None,
        source_raw_text: Optional[str] = None,
        target_language: Optional[str] = None,
        text_mode: Optional[str] = None,
        **kwargs,
    ) -> Optional[Any]:
        if lookup_ttl_seconds <= 0:
            return None

        strategy = str(kwargs.get("sentence_match_strategy") or kwargs.get("strategy") or "normalized").strip().lower()
        if strategy == "none":
            return None

        raw_text = source_raw_text or kwargs.get("text") or ""
        if raw_text:
            normalized_raw = raw_text.strip()
            conditions = ["TRIM(source_raw_text) = ?", "source_language = ?"]
            params: List[Any] = [normalized_raw, source_language]
            if target_language:
                conditions.append("target_language = ?")
                params.append(target_language)
            if text_mode:
                conditions.append("text_mode = ?")
                params.append(text_mode)
            params.append(lookup_ttl_seconds)

            sql = f"""
                SELECT zid, slug, source_language, target_language, text_mode, source_raw_text, created_at, updated_at
                FROM sessions
                WHERE {' AND '.join(conditions)}
                  AND (julianday('now') - julianday(created_at)) * 86400 <= ?
                ORDER BY created_at DESC LIMIT 1;
            """
            rows = self.db.query_readonly(sql, tuple(params), zid=zid)
            if rows:
                cached_zid = rows[0]["zid"]
                return self.db.get_session_bundle(cached_zid, zid=zid)

            # Strategy fallback for normalized or contextual
            if strategy in ("normalized", "contextual"):
                cand_conditions = ["source_language = ?"]
                cand_params: List[Any] = [source_language]
                if target_language:
                    cand_conditions.append("target_language = ?")
                    cand_params.append(target_language)
                cand_params.append(lookup_ttl_seconds)
                cand_sql = f"""
                    SELECT zid, slug, source_language, target_language, text_mode, source_raw_text, created_at, updated_at
                    FROM sessions
                    WHERE {' AND '.join(cand_conditions)}
                      AND (julianday('now') - julianday(created_at)) * 86400 <= ?
                    ORDER BY created_at DESC;
                """
                cand_rows = self.db.query_readonly(cand_sql, tuple(cand_params), zid=zid)
                norm_query = normalize_sentence_for_lookup(raw_text)
                for cr in cand_rows:
                    cand_text = cr.get("source_raw_text") or ""
                    cand_norm = normalize_sentence_for_lookup(cand_text)
                    if strategy == "normalized" and cand_norm == norm_query:
                        return self.db.get_session_bundle(cr["zid"], zid=zid)
                    elif strategy == "contextual":
                        if norm_query == cand_norm or norm_query in cand_norm or cand_norm in norm_query:
                            return self.db.get_session_bundle(cr["zid"], zid=zid)
            return None

        sql = """
            SELECT zid, slug, source_language, target_language, text_mode, source_raw_text, created_at, updated_at
            FROM sessions
            WHERE slug = ? AND source_language = ?
              AND (julianday('now') - julianday(created_at)) * 86400 <= ?
            ORDER BY created_at DESC LIMIT 1;
        """
        rows = self.db.query_readonly(sql, (slug, source_language, lookup_ttl_seconds), zid=zid)
        if rows:
            cached_zid = rows[0]["zid"]
            return self.db.get_session_bundle(cached_zid, zid=zid)
        return None

    def find_sentence_by_strategy(
        self,
        sentence_text: str,
        language: Optional[str] = None,
        target_language: Optional[str] = None,
        strategy: str = "normalized",
        allow_fallback: bool = True,
        exclude_zid: Optional[str] = None,
        limit: int = 10,
        zid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.db.find_sentence_by_strategy(
            sentence_text=sentence_text,
            language=language,
            target_language=target_language,
            strategy=strategy,
            allow_fallback=allow_fallback,
            exclude_zid=exclude_zid,
            limit=limit,
            zid=zid,
        )

    def save_tsv_rows_safely(
        self,
        tsv_path: Path,
        comments: List[str],
        headers: List[str],
        data_rows: List[List[str]],
        **kwargs,
    ) -> None:
        zid = extract_zid(tsv_path) or generate_unique_zid()
        slug_match = re.match(r'^\d{14}-(.*?)(?:\.[a-z]{2})?\.tsv$', tsv_path.name, re.IGNORECASE)
        slug_val = slug_match.group(1) if slug_match else ""
        lang_val = kwargs.get("language") or (tsv_path.suffixes[-2][1:] if len(tsv_path.suffixes) >= 2 else "en")
        self.save_session(
            session_zid=zid,
            slug=slug_val,
            source_language=lang_val,
            target_language=kwargs.get("target_language", "ru"),
            text_mode=kwargs.get("text_mode", "single"),
            comments=comments,
            headers=headers,
            data_rows=data_rows,
            working_tsv_path=None,
            zid=zid,
        )

    def load_tsv_rows(
        self,
        tsv_path: Path,
        **kwargs,
    ) -> Tuple[List[str], List[str], List[List[str]]]:
        if tsv_path:
            sess_zid = extract_zid(tsv_path)
            if sess_zid:
                try:
                    restored = self.restore_session(sess_zid)
                    if restored is not None and "data_rows" in restored:
                        return (
                            restored.get("comments", []),
                            restored.get("headers", []),
                            restored.get("data_rows", []),
                        )
                except Exception:
                    pass
        return self._tsv_fallback.load_tsv_rows(tsv_path)

    @contextlib.contextmanager
    def file_lock(self, file_path: Path):
        if file_path and not file_path.exists():
            yield
        else:
            with self._tsv_fallback.file_lock(file_path):
                yield

    def update_word(
        self,
        session_zid: str,
        sentence_idx: Optional[int],
        token_order: int,
        field: str,
        value: Any,
        zid: Optional[str] = None,
    ) -> bool:
        """
        Pinpoint atomic SQL update for a single word token cell.
        """
        field_mapping = {
            "quotation": "quotation",
            "wordsource": "lemma",
            "lemma": "lemma",
            "wordsource2": "wordsource2",
            "wordsourceinflectedform": "inflected_form",
            "wordsourceinflectedform2": "wordsourceinflectedform2",
            "inflectedform": "inflected_form",
            "inflected_form": "inflected_form",
            "worddestination": "word_destination",
            "word_destination": "word_destination",
            "worddestinationinflectedform": "word_destination_inflected",
            "word_destination_inflected": "word_destination_inflected",
            "wordsourcemorphologyai": "morphology",
            "morphology": "morphology",
            "wordsourceipa": "ipa",
            "ipa": "ipa",
            "deskselected": "selected",
            "selected": "selected",
            "leitnerbox": "leitner_box",
            "leitner_box": "leitner_box",
            "leitnerdue": "leitner_due",
            "leitner_due": "leitner_due",
            "deck": "deck",
            "classificationoxford": "classification_oxford",
            "classification_oxford": "classification_oxford",
            "classificationgoethe": "classification_goethe",
            "classification_goethe": "classification_goethe",
            "pos": "pos",
        }
        f_norm = field.strip().lower().replace("_", "")
        db_col = field_mapping.get(f_norm)

        with self.db.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            if db_col and db_col not in ("wordsource2", "wordsourceinflectedform2"):
                cast_val = value
                if db_col == "selected":
                    cast_val = 1 if str(value).strip() in ("1", "true", "True") else 0
                elif db_col == "leitner_box":
                    try:
                        cast_val = int(value)
                    except (ValueError, TypeError):
                        cast_val = 1

                if sentence_idx is not None:
                    cursor.execute(
                        f"UPDATE words SET {db_col} = ? WHERE session_zid = ? AND sentence_index = ? AND token_order = ?;",
                        (cast_val, session_zid, sentence_idx, token_order),
                    )
                else:
                    cursor.execute(
                        f"UPDATE words SET {db_col} = ? WHERE session_zid = ? AND token_order = ?;",
                        (cast_val, session_zid, token_order),
                    )
                return cursor.rowcount > 0
            else:
                # Custom / unmapped field stored inside extra_fields JSON
                if sentence_idx is not None:
                    cursor.execute(
                        "SELECT id, extra_fields FROM words WHERE session_zid = ? AND sentence_index = ? AND token_order = ?;",
                        (session_zid, sentence_idx, token_order),
                    )
                else:
                    cursor.execute(
                        "SELECT id, extra_fields FROM words WHERE session_zid = ? AND token_order = ?;",
                        (session_zid, token_order),
                    )
                row = cursor.fetchone()
                if not row:
                    return False
                word_id = row["id"]
                raw_ef = row["extra_fields"]
                ef_dict = {}
                if raw_ef:
                    try:
                        ef_dict = json.loads(raw_ef)
                    except Exception:
                        ef_dict = {}
                ef_dict[field] = value
                cursor.execute(
                    "UPDATE words SET extra_fields = ? WHERE id = ?;",
                    (json.dumps(ef_dict, ensure_ascii=False), word_id),
                )
                return cursor.rowcount > 0

    def update_word_selection(
        self,
        session_zid: str,
        sentence_idx: Optional[int],
        token_order: int,
        selected: Union[int, bool, str],
        zid: Optional[str] = None,
    ) -> bool:
        """
        Updates the 'selected' state (0 or 1) for a word row atomically in SQLite.
        """
        sel_val = 1 if str(selected).strip() in ("1", "true", "True") else 0
        with self.db.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            if sentence_idx is not None:
                cursor.execute(
                    "UPDATE words SET selected = ? WHERE session_zid = ? AND sentence_index = ? AND token_order = ?;",
                    (sel_val, session_zid, sentence_idx, token_order),
                )
            else:
                cursor.execute(
                    "UPDATE words SET selected = ? WHERE session_zid = ? AND token_order = ?;",
                    (sel_val, session_zid, token_order),
                )
            return cursor.rowcount > 0

    def update_sentence_translation(
        self,
        session_zid: str,
        sentence_index: int,
        text: str,
        target_field: str = "sentence_destination",
        zid: Optional[str] = None,
    ) -> bool:
        """
        Pinpoint atomic SQL update for a sentence translation in the sentences table.
        """
        allowed_cols = {
            "sentence_destination", "sentence_destination2", "sentence_source_ipa", "sentence_source_audio", "sentence_source"
        }
        col = target_field if target_field in allowed_cols else "sentence_destination"
        with self.db.get_connection(zid=zid) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE sentences SET {col} = ? WHERE session_zid = ? AND sentence_index = ?;",
                (text, session_zid, sentence_index),
            )
            return cursor.rowcount > 0

    def batch_update_words(
        self,
        session_zid: str,
        updates_list: List[Dict[str, Any]],
        zid: Optional[str] = None,
    ) -> int:
        """
        Batch update word fields across multiple rows in a single atomic SQL transaction.
        """
        return self.db.batch_update_words(session_zid, updates_list, zid=zid)

    def enrich_session_intellifiller(
        self,
        session_zid: str,
        prompt_name: str,
        selected_rows: Optional[List[int]] = None,
        reprocess: bool = False,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> bool:
        """
        Enriches a SQLite session using IntelliFiller via ephemeral scratch payload,
        ingesting enriched tokens directly into SQLite words & extra_fields without
        leaving persistent TSV files in results/.
        """
        zid = zid or session_zid
        restored = self.restore_session(session_zid)
        comments = restored.get("comments", [])
        headers = restored.get("headers", [])
        data_rows = restored.get("data_rows", [])
        db_words = restored.get("words", [])

        if not data_rows:
            return True

        session = restored.get("session", {})
        lang = restored.get("source_language") or (session.get("source_language") if isinstance(session, dict) else None) or (self.config.get(SEC_SETTINGS, "default_language", fallback="en") if self.config and hasattr(self.config, "get") else "en")

        mapping = None
        if self.resolved_paths and "anki_mapping_file" in self.resolved_paths:
            mapping = load_anki_mapping(self.resolved_paths["anki_mapping_file"])
        elif self.config and hasattr(self.config, "get"):
            raw_mp = self.config.get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
            if raw_mp and Path(raw_mp).exists():
                mapping = load_anki_mapping(Path(raw_mp))
        role_fields = get_role_fields(mapping, headers) if mapping else {}

        # Frequency sort data_rows and align db_words in lockstep to ensure selected_rows parity
        headers_with_idx = list(headers) + ["__temp_sort_idx__"]
        data_rows_with_idx = [list(r) + [str(i)] for i, r in enumerate(data_rows)]
        sorted_rows_with_idx = sort_rows_by_frequency(
            data_rows_with_idx, headers_with_idx, lang, self.config, self.resolved_paths, role_fields=role_fields
        )
        sorted_indices = [int(r[-1]) for r in sorted_rows_with_idx]
        data_rows = [r[:-1] for r in sorted_rows_with_idx]
        db_words = [db_words[idx] if idx < len(db_words) else {} for idx in sorted_indices]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_tsv_path = Path(temp_dir) / f"{session_zid}-ephemeral.tsv"
            self._tsv_fallback.save_tsv_rows_safely(temp_tsv_path, comments, headers, data_rows)

            success = run_headless_intellifiller(
                tsv_path=temp_tsv_path,
                prompt_name=prompt_name,
                config=self.config,
                resolved_paths=self.resolved_paths,
                selected_rows=selected_rows,
                reprocess=reprocess,
                zid=zid,
                trace_id=trace_id,
            )

            if success and temp_tsv_path.exists():
                _, updated_headers, updated_rows = load_tsv_rows(temp_tsv_path)
                updates_list = []
                for row_idx, r in enumerate(updated_rows):
                    if selected_rows is not None and row_idx not in selected_rows:
                        continue
                    row_updates = {}
                    for col_idx, h in enumerate(updated_headers):
                        if col_idx < len(r):
                            row_updates[h] = r[col_idx]

                    if row_idx < len(db_words):
                        w_id = db_words[row_idx].get("id")
                        updates_list.append({
                            "id": w_id,
                            "token_order": db_words[row_idx].get("token_order", row_idx),
                            "sentence_index": db_words[row_idx].get("sentence_index", 1),
                            "updates": row_updates,
                        })
                    else:
                        updates_list.append({
                            "token_order": row_idx,
                            "updates": row_updates,
                        })

                if updates_list:
                    self.db.batch_update_words(session_zid, updates_list, zid=zid)

            return success

    def delete_session(self, session_zid: str, zid: Optional[str] = None) -> bool:
        """
        Deletes session metadata, cascading deletion to sentences and words in SQLite.
        """
        return self.db.delete_session(session_zid, zid=zid)

    def list_sessions(self, limit: Optional[int] = None, offset: int = 0, zid: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.list_sessions_with_counts(limit=limit, offset=offset, zid=zid)

    def cleanup_db(self, older_than_days: float, zid: Optional[str] = None) -> int:
        return self.db.cleanup_db(older_than_days=older_than_days, zid=zid)

    def vacuum(self, zid: Optional[str] = None) -> bool:
        return self.db.vacuum(zid=zid)

    def export_favorites(
        self,
        session_zid: str,
        selected_row_ids: Optional[List[int]] = None,
        save_to_favorites_override: Optional[bool] = None,
        send_to_anki_override: Optional[bool] = None,
        language: Optional[str] = None,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dynamically exports selected rows from SQLite storage directly to standard favorites TSV.
        Guarantees 100% column format and ordering equivalence with anki-mapping.ini.
        """
        zid = zid or session_zid or generate_unique_zid()
        restored = self.restore_session(session_zid)
        headers = restored["headers"]
        data_rows = restored["data_rows"]
        db_words = restored.get("words", [])
        session = restored.get("session", {})

        selected_col_idx = headers.index("DeskSelected") if "DeskSelected" in headers else -1

        export_selection_mode = "selected"
        if self.config and hasattr(self.config, "get"):
            export_selection_mode = self.config.get(SEC_SETTINGS, "export_selection_mode", fallback="selected").lower()

        if export_selection_mode == "all":
            actual_export_rows = list(range(len(data_rows)))
        elif export_selection_mode == "unselected":
            if selected_row_ids is not None:
                actual_export_rows = [i for i in range(len(data_rows)) if i not in selected_row_ids]
            else:
                actual_export_rows = [
                    i for i, row in enumerate(data_rows)
                    if not (len(row) > selected_col_idx and selected_col_idx != -1 and row[selected_col_idx] in ("1", "true", "True"))
                ]
        else:
            # "selected"
            if selected_row_ids is not None and len(selected_row_ids) > 0:
                actual_export_rows = selected_row_ids
            else:
                actual_export_rows = [
                    i for i, row in enumerate(data_rows)
                    if len(row) > selected_col_idx and selected_col_idx != -1 and row[selected_col_idx] in ("1", "true", "True")
                ]

        if not actual_export_rows:
            logger.warning("No rows to export based on selection mode.")
            return {
                "status": "skipped",
                "message": "Warning: No rows to export based on selection mode. Export skipped.",
            }

        exported_rows = []
        for row_id in actual_export_rows:
            if 0 <= row_id < len(data_rows):
                row_copy = list(data_rows[row_id])
                if selected_col_idx != -1:
                    if len(row_copy) > selected_col_idx:
                        row_copy[selected_col_idx] = "1"
                    else:
                        row_copy.extend([""] * (selected_col_idx - len(row_copy) + 1))
                        row_copy[selected_col_idx] = "1"
                exported_rows.append(row_copy)

        if not exported_rows:
            return {
                "status": "skipped",
                "message": "Warning: None of the selected row indices were valid.",
            }

        # Auto-save selected=1 back into SQLite for the exported words
        try:
            selected_set = set(actual_export_rows)
            for row_idx, word in enumerate(db_words):
                if row_idx in selected_set and word.get("id"):
                    self.db.update_word(word["id"], {"selected": 1}, zid=zid)
        except Exception as e:
            logger.warning(f"Failed to auto-save selected state to SQLite: {e}")

        # Check if session belongs to any project hierarchy and resolve deck path
        try:
            linked_projects = self.db.get_session_projects(session_zid)
            if linked_projects:
                primary_project = linked_projects[0]
                lang_for_deck = session.get("source_language") or language or "en"
                hierarchical_deck = resolve_project_deck_path(primary_project["id"], self.db, language=lang_for_deck)
                deck_col_idx = headers.index("Deck") if "Deck" in headers else -1
                if deck_col_idx != -1:
                    for row_copy in exported_rows:
                        if len(row_copy) > deck_col_idx:
                            row_copy[deck_col_idx] = hierarchical_deck
                        else:
                            row_copy.extend([""] * (deck_col_idx - len(row_copy) + 1))
                            row_copy[deck_col_idx] = hierarchical_deck
        except Exception as e:
            logger.warning(f"Failed to resolve project hierarchical deck: {e}")

        # Resolve destination path
        fav_dir = Path(self.resolved_paths.get("favorites_output_dir") or "favorites")
        fav_dir.mkdir(parents=True, exist_ok=True)

        fav_prefix = ""
        if self.config and hasattr(self.config, "get"):
            fav_prefix = self.config.get(SEC_SETTINGS, "favorites_prefix", fallback="")

        slug = session.get("slug", "")
        lang = session.get("source_language") or language or "en"
        filename_base = f"{session_zid}-{slug}.{lang}.tsv" if slug else f"{session_zid}.{lang}.tsv"
        dest_filename = f"{fav_prefix}{filename_base}"
        dest_path = fav_dir / dest_filename

        save_to_favorites = True
        if self.config and hasattr(self.config, "getboolean"):
            save_to_favorites = self.config.getboolean(SEC_SETTINGS, "save_to_favorites_on_export", fallback=True)
        if save_to_favorites_override is not None:
            save_to_favorites = save_to_favorites_override

        results_dir = Path(self.resolved_paths.get("results_dir") or "results")
        import_path = dest_path if save_to_favorites else (results_dir / f"temp_import_{dest_filename}")

        comments = []
        with self._tsv_fallback.file_lock(import_path):
            self._tsv_fallback.save_tsv_rows_safely(import_path, comments, headers, exported_rows)

        if save_to_favorites:
            logger.info(f"Exported favorites to {import_path}")
        else:
            logger.info(f"Exported temporary file for Anki import to {import_path}")

        send_to_anki = False
        if self.config and hasattr(self.config, "getboolean"):
            send_to_anki = self.config.getboolean(SEC_SETTINGS, "send_to_anki_after_export", fallback=False)
        if send_to_anki_override is not None:
            send_to_anki = send_to_anki_override

        if send_to_anki:
            detach = True
            show_window = False
            if self.config and hasattr(self.config, "getboolean"):
                detach = self.config.getboolean(SEC_SETTINGS, "detach_import_on_send", fallback=True)
                show_window = self.config.getboolean(SEC_SETTINGS, "show_import_window", fallback=False)

            if detach:
                show_window = False
                pid, log_path = run_detached_import(import_path, self.config, self.resolved_paths, zid, trace_id=trace_id)
                return {
                    "import_started": True,
                    "show_window": show_window,
                    "pid": pid,
                    "log": log_path,
                    "tsv": str(import_path),
                    "note": "safe to close the window",
                }
            else:
                success, output = run_synchronous_import(import_path, self.config, self.resolved_paths, zid=zid, trace_id=trace_id)
                if success:
                    return {
                        "import_complete": True,
                        "show_window": show_window,
                        "output": output,
                    }
                else:
                    raise StructuredError(ErrorCode.DESK_FAILED, "Anki import failed synchronously", {"details": output})
        else:
            if save_to_favorites:
                show_window = False
                if self.config and hasattr(self.config, "getboolean"):
                    show_window = self.config.getboolean(SEC_SETTINGS, "show_import_window", fallback=False)
                return {
                    "import_complete": True,
                    "show_window": show_window,
                    "output": f"SUCCESS: Exported to {import_path}",
                }
            else:
                return {
                    "status": "success",
                    "message": "SUCCESS: Ready for Anki (no favorites file created)",
                }


# ---------------------------------------------------------------------------
# Project Hierarchy & Material Synthesis Helpers
# ---------------------------------------------------------------------------
LANGUAGE_NAMES: Dict[str, str] = {
    "de": "German",
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "zh": "Chinese",
    "ja": "Japanese",
}


def resolve_project_deck_path(
    project_id: int,
    db: Any,
    language: Optional[str] = None,
) -> str:
    """
    Resolves recursive project ancestral path from root to node and formats
    hierarchical deck name (e.g. Language::Book::Volume::Chapter).
    """
    path_nodes = db.get_project_path(project_id)
    titles = [n["title"] for n in path_nodes if n.get("title")]

    parts = []
    if language:
        lang_name = LANGUAGE_NAMES.get(language.lower(), language.capitalize())
        parts.append(lang_name)
    parts.extend(titles)

    return "::".join(parts) if parts else (language or "Default")


def synthesize_project_deck_descriptions(
    project_id: int,
    db: Any,
    language: Optional[str] = None,
) -> Dict[str, str]:
    """
    Traverses project subtree and maps hierarchical deck paths to node descriptions
    for companion anki-csv-importer metadata JSON.
    """
    descriptions: Dict[str, str] = {}
    trees = db.get_project_tree(project_id)

    def _traverse(node: Dict[str, Any]):
        p_id = node["id"]
        deck_path = resolve_project_deck_path(p_id, db, language=language)
        desc = node.get("description")
        if desc:
            descriptions[deck_path] = desc
        for child in node.get("children", []):
            _traverse(child)

    for root_node in trees:
        _traverse(root_node)

    return descriptions


def aggregate_project_materials(
    project_id: int,
    config: Optional[Any] = None,
    resolved_paths: Optional[Dict[str, Any]] = None,
    language: Optional[str] = None,
    export_all: bool = False,
    zid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Bottom-up material aggregator collecting selected words across all sessions
    in a project and its subprojects ordered sequentially by order_index.
    Synthesizes both a consolidated hierarchical TSV and deck description JSON.
    """
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)

    project = db.get_project(project_id)
    if not project:
        raise StructuredError(ErrorCode.NOT_FOUND, f"Project with ID '{project_id}' not found.")

    trees = db.get_project_tree(project_id)
    if not trees:
        raise StructuredError(ErrorCode.NOT_FOUND, f"No project tree found for ID '{project_id}'.")

    lang = language or (config.get(SEC_SETTINGS, "default_language", fallback="en") if config and hasattr(config, "get") else "en")

    all_data_rows: List[List[str]] = []
    headers: List[str] = []
    total_sessions = 0

    def _collect(node: Dict[str, Any]):
        nonlocal headers, total_sessions
        p_id = node["id"]
        deck_path = resolve_project_deck_path(p_id, db, language=lang)
        sessions = db.get_project_sessions(p_id)

        for sess in sessions:
            s_zid = sess["session_zid"]
            restored = adapter.restore_session(s_zid)
            if not headers and restored.get("headers"):
                headers = list(restored["headers"])

            sess_rows = restored.get("data_rows", [])
            sel_idx = headers.index("DeskSelected") if "DeskSelected" in headers else -1
            deck_idx = headers.index("Deck") if "Deck" in headers else -1

            for row in sess_rows:
                is_selected = (
                    sel_idx != -1
                    and len(row) > sel_idx
                    and str(row[sel_idx]).strip() in ("1", "true", "True")
                )
                if export_all or is_selected:
                    row_copy = list(row)
                    if sel_idx != -1:
                        if len(row_copy) > sel_idx:
                            row_copy[sel_idx] = "1"
                        else:
                            row_copy.extend([""] * (sel_idx - len(row_copy) + 1))
                            row_copy[sel_idx] = "1"
                    if deck_idx != -1:
                        if len(row_copy) > deck_idx:
                            row_copy[deck_idx] = deck_path
                        else:
                            row_copy.extend([""] * (deck_idx - len(row_copy) + 1))
                            row_copy[deck_idx] = deck_path
                    all_data_rows.append(row_copy)

            total_sessions += 1

        for child in node.get("children", []):
            _collect(child)

    for root_node in trees:
        _collect(root_node)

    if not headers:
        headers = [
            "Quotation", "WordSource", "WordSource2", "WordSourceInflectedForm", "WordSourceInflectedForm2",
            "WordDestination", "WordDestinationInflectedForm", "WordSourceContext", "SentenceSourceContextLeft",
            "SentenceSource", "SentenceSourceContextRight", "SentenceDestinationContextLeft", "SentenceDestination",
            "SentenceDestinationContextRight", "SentenceDestination2ContextLeft", "SentenceDestination2",
            "SentenceDestination2ContextRight", "SentenceSourceWordlist", "SentenceSourceCloze",
            "SentenceSourceRewriteAISentenceSource", "SentenceSourceRewriteAISentenceDestination",
            "WordSourceMorphologyAI", "Note", "WordRussian", "WordUkrainian", "WordEnglish", "WordGerman",
            "WordSourceMorphemeFirst", "WordSourceMorphemeFirstDefinition", "WordSourceMorphemeSecond",
            "WordSourceMorphemeSecondDefinition", "WordSourceMorphemeThird", "WordSourceMorphemeThirdDefinition",
            "WordSourceMorphemeFourth", "WordSourceMorphemeFourthDefinition", "WordSourceMorphemeFifth",
            "WordSourceMorphemeFifthDefinition", "WordSourceIPA", "WordSourceSynonymAI",
            "WordSourceDefinitionAISentenceSource", "WordSourceDefinitionAISentenceDestination",
            "WordSourceDefinitionFirst", "WordSourceDefinitionFirstClipping", "WordSourceDefinitionSecond",
            "WordDestinationDefinitionFirst", "WordDestinationDefinitionSecond", "WordSourceAudio",
            "SentenceSourceIPA", "SentenceSourceAudio", "Image", "WordSourceCloze", "WordSourceContextAI",
            "TextSource", "TextDestination", "TextSourceURL", "SentenceEnglish", "SentenceGerman",
            "SentenceUkrainian", "SentenceRussian", "Source", "SourceURL", "SeparatorAudio",
            "Source-en-GB", "Source-en-US", "Source-de-DE", "Source-uk-UA", "Source-ru-RU",
            "Destination-en-GB", "Destination-en-US", "Destination-de-DE", "Destination-uk-UA",
            "Destination-ru-RU", "Overlapping", "ToggleAlwaysEmptyField", "Note ID",
            "am-all-morphs", "am-all-morphs-count", "am-unknown-morphs", "am-unknown-morphs-count",
            "am-highlighted", "am-score", "am-score-terms", "am-study-morphs",
            "SentenceSourceIndex", "Deck", "LeitnerBox", "LeitnerDue", "DeskSelected",
            "ClassificationOxford", "ClassificationGoethe",
        ]

    # Destination directories
    fav_dir = Path(resolved_paths.get("favorites_output_dir") if resolved_paths else "favorites")
    fav_dir.mkdir(parents=True, exist_ok=True)

    slug = project.get("slug") or f"project_{project_id}"
    base_filename = f"{project_id}-{slug}.{lang}"
    tsv_dest = fav_dir / f"{base_filename}.tsv"
    json_dest = fav_dir / f"{base_filename}.json"

    # 1. Save TSV
    comments = [f"# Aggregated Project Deck: {project.get('title')} (ID: {project_id})"]
    save_tsv_rows_safely(tsv_dest, comments, headers, all_data_rows)

    # 2. Synthesize Deck Descriptions JSON
    deck_descriptions = synthesize_project_deck_descriptions(project_id, db, language=lang)
    metadata = {
        "deck_descriptions": deck_descriptions,
        "project_id": project_id,
        "project_title": project.get("title"),
        "project_slug": slug,
        "language": lang,
        "total_sessions": total_sessions,
        "total_words": len(all_data_rows),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    json_dest.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "ok": True,
        "project_id": project_id,
        "project_title": project.get("title"),
        "language": lang,
        "tsv_path": str(tsv_dest),
        "json_path": str(json_dest),
        "total_sessions": total_sessions,
        "total_words": len(all_data_rows),
        "deck_descriptions": deck_descriptions,
    }


def deduplicate_rows_by_lemma(
    data_rows: List[List[str]],
    headers: List[str],
    role_fields: Optional[Dict[str, Any]] = None,
) -> List[List[str]]:
    """
    Deduplicates data rows by lemma, preserving first occurrence order and merging non-empty fields & inflected forms.
    Rows with empty lemmas are preserved unchanged.
    """
    if not data_rows or not headers:
        return data_rows

    lemma_col = role_fields.get('lemma', 'WordSource') if isinstance(role_fields, dict) else 'WordSource'
    inflected_col = role_fields.get('inflected', 'WordSourceInflectedForm') if isinstance(role_fields, dict) else 'WordSourceInflectedForm'
    col_lemma = headers.index(lemma_col) if lemma_col in headers else -1
    col_inflected = headers.index(inflected_col) if inflected_col in headers else -1
    col_quotation = headers.index('Quotation') if 'Quotation' in headers else -1

    if col_lemma == -1:
        return data_rows

    ordered_entries = []
    grouped_rows: Dict[str, List[List[str]]] = {}
    for row in data_rows:
        raw_lemma = row[col_lemma].strip() if len(row) > col_lemma else ""
        if raw_lemma and re.match(r'^\d{14}-', raw_lemma):
            raw_lemma = re.sub(r'^\d{14}-', '', raw_lemma)
        clean_lemma = raw_lemma.strip('-')
        lemma_val = clean_lemma.lower()
        if not lemma_val:
            ordered_entries.append(('row', row))
            continue
        if len(row) > col_lemma and row[col_lemma].strip() != clean_lemma:
            row = list(row)
            row[col_lemma] = clean_lemma
        if lemma_val not in grouped_rows:
            grouped_rows[lemma_val] = []
            ordered_entries.append(('lemma', lemma_val))
        grouped_rows[lemma_val].append(row)

    unique_rows: List[List[str]] = []
    for entry_type, val in ordered_entries:
        if entry_type == 'row':
            unique_rows.append(val)
            continue
        lem = val
        rows_list = grouped_rows[lem]
        merged_row = list(rows_list[0])
        merged_inflected: List[str] = []
        for r in rows_list:
            for i, cell in enumerate(r):
                if i < len(merged_row) and not merged_row[i].strip() and cell.strip():
                    merged_row[i] = cell
            if col_inflected != -1:
                inf_val = r[col_inflected].strip() if len(r) > col_inflected else ""
                if not inf_val and col_quotation != -1 and len(r) > col_quotation:
                    inf_val = r[col_quotation].strip()
                if inf_val:
                    for part in [p.strip() for p in inf_val.split(',') if p.strip()]:
                        if part not in merged_inflected:
                            merged_inflected.append(part)
        if col_inflected != -1 and merged_inflected:
            merged_row[col_inflected] = ", ".join(sort_inflected_forms(
                merged_inflected,
                apostrophe_chars=("'", "’", "‘", "`", "´", "ʼ"),
                order='contractions_first',
                prefer_lowercase=True,
            ))
        unique_rows.append(merged_row)

    return unique_rows


_LEMMA_FREQUENCY_INDEX_CACHE: Dict[str, Tuple[float, Dict[str, int]]] = {}


def load_lemma_frequency_index_cached(file_path: Union[str, Path]) -> Dict[str, int]:
    """
    Loads and caches a lemma frequency index dictionary mapping word/lemma string -> frequency rank index.
    Automatically invalidates and reloads if the underlying file modification time (mtime) changes.
    """
    path_obj = Path(file_path)
    if not path_obj.exists():
        return {}
    try:
        mtime = path_obj.stat().st_mtime
    except Exception:
        return {}

    resolved_key = str(path_obj.resolve())
    cached = _LEMMA_FREQUENCY_INDEX_CACHE.get(resolved_key)
    if cached and cached[0] == mtime:
        return cached[1]

    index: Dict[str, int] = {}
    try:
        with open(path_obj, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f):
                word = line.strip()
                if word and word not in index:
                    index[word] = line_number
        _LEMMA_FREQUENCY_INDEX_CACHE[resolved_key] = (mtime, index)
    except Exception as err:
        logger.warning(f"Error reading lemma frequency index {file_path}: {err}")
        return {}
    return index


def get_lemma_sort_key(word: str, lemma_index: Dict[str, int], language: str = "en", case_sensitive: Optional[bool] = None) -> Tuple[bool, int, str]:
    """
    Generates a deterministic sort key tuple (is_unranked: bool, rank: int, lemma_lower: str)
    matching kardenwort core lemma frequency sorting.
    """
    language = language or "en"
    if case_sensitive is None:
        case_sensitive = (language == "de")

    def get_variations(w: str) -> List[str]:
        vars_set: List[str] = []
        w_clean = w.strip()
        vars_set.append(w_clean)
        if not case_sensitive:
            vars_set.append(w_clean.lower())

        w_straight = w_clean.replace("’", "'")
        vars_set.append(w_straight)
        if not case_sensitive:
            vars_set.append(w_straight.lower())

        w_no_apo = w_clean.replace("’", "").replace("'", "").replace("`", "")
        vars_set.append(w_no_apo)
        if not case_sensitive:
            vars_set.append(w_no_apo.lower())

        seen = set()
        res = []
        for v in vars_set:
            if v not in seen:
                seen.add(v)
                res.append(v)
        return res

    for var in get_variations(word):
        if var in lemma_index:
            return (False, lemma_index[var], word.lower())

    parts = []
    if ',' in word:
        parts = [p.strip() for p in word.split(',') if p.strip()]
    elif '/' in word:
        parts = [p.strip() for p in word.split('/') if p.strip()]

    if parts:
        found_indices = []
        for p in parts:
            for var in get_variations(p):
                if var in lemma_index:
                    found_indices.append(lemma_index[var])
                    break
        if found_indices:
            val = min(found_indices)
            return (False, val, word.lower())

    return (True, 0, word.lower())


def sort_rows_by_frequency(
    data_rows: List[List[str]],
    headers: List[str],
    lang: str,
    config: Optional[Any],
    resolved_paths: Optional[Dict[str, Any]],
    role_fields: Optional[Dict[str, Any]] = None,
) -> List[List[str]]:
    """
    Sorts data rows globally by word frequency ranking in the configured language frequency dictionary index.
    Known words are ordered by increasing frequency rank index (top 1 to N), followed by unranked words.
    Uses in-memory caching to avoid subprocess spawning.
    """
    if not data_rows or not headers:
        return data_rows

    lemma_col = role_fields.get('lemma', 'WordSource') if isinstance(role_fields, dict) else 'WordSource'
    col_lemma = headers.index(lemma_col) if lemma_col in headers else -1
    if col_lemma == -1:
        return data_rows

    try:
        kardenwort_workspace = resolved_paths.get('kardenwort_workspace') if resolved_paths else None
        lemma_index_rel = config.get(SEC_LANGUAGES, f'{lang}_lemma_index', fallback="") if config and hasattr(config, "get") else ""
        if not lemma_index_rel:
            return data_rows

        if kardenwort_workspace:
            lemma_index_file = Path(kardenwort_workspace) / lemma_index_rel
        else:
            lemma_index_file = Path(lemma_index_rel)

        if not lemma_index_file.exists():
            return data_rows

        lemma_index = load_lemma_frequency_index_cached(lemma_index_file)
        if not lemma_index:
            return data_rows

        sorted_rows = list(data_rows)
        sorted_rows.sort(
            key=lambda r: get_lemma_sort_key(
                r[col_lemma].strip() if len(r) > col_lemma else "",
                lemma_index,
                language=lang
            )
        )
        return sorted_rows
    except Exception as sort_err:
        logger.warning(f"Failed to sort rows by frequency: {sort_err}")
        return data_rows


def synthesize_project_materials(
    project_id: int,
    db: Optional[Any] = None,
    config: Optional[Any] = None,
    resolved_paths: Optional[Dict[str, Any]] = None,
    language: Optional[str] = None,
    zid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Synthesizes continuous reading and study materials across all sessions in a project
    and its descendant subprojects ordered sequentially by order_index.
    Restores session bundles from SQLite in memory, populates hierarchical deck tags,
    and returns a unified multi-chapter reader structure.
    """
    from kardenwort_db import KardenwortDB
    if db is None:
        db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)

    project = db.get_project(project_id)
    if not project:
        raise StructuredError(ErrorCode.NOT_FOUND, f"Project with ID '{project_id}' not found.")

    trees = db.get_project_tree(project_id)
    if not trees:
        raise StructuredError(ErrorCode.NOT_FOUND, f"No project tree found for ID '{project_id}'.")

    lang = language or (
        config.get(SEC_SETTINGS, "default_language", fallback="de")
        if config and hasattr(config, "get")
        else "de"
    )

    all_data_rows: List[List[str]] = []
    all_sentences: List[Dict[str, Any]] = []
    source_texts: List[str] = []
    headers: List[str] = []
    chapters: List[Dict[str, Any]] = []
    total_sessions = 0
    global_sentence_offset = 0
    pending_heading_lemmas: List[Dict[str, Any]] = []

    target_lang = (
        config.get(SEC_SETTINGS, "default_target_language", fallback="ru")
        if config and hasattr(config, "get")
        else "ru"
    )
    provider = (
        config.get(SEC_PIPELINE, "translation_provider", fallback="none")
        if config and hasattr(config, "get")
        else "none"
    )

    def _collect(node: Dict[str, Any], level: int = 0):
        nonlocal headers, total_sessions, global_sentence_offset
        p_id = node["id"]
        title = str(node.get("title", "") or "").strip()
        deck_path = resolve_project_deck_path(p_id, db, language=lang)
        sessions = db.get_project_sessions(p_id)

        target_synth_zid = zid or f"project_{project_id}"

        # 1. Inject node title as Markdown heading if present
        if title:
            heading_hashes = '#' * min(level + 1, 6)
            heading_text = f"{heading_hashes} {title}"
            source_texts.append(heading_text)

            heading_sent_idx = 1 + global_sentence_offset
            global_sentence_offset += 1

            heading_translation = ""
            if provider and provider != "none":
                try:
                    heading_translation = translate_text(
                        heading_text,
                        source=lang,
                        target=target_lang,
                        config=config,
                        resolved_paths=resolved_paths,
                        provider=provider,
                        zid=zid,
                    ) or ""
                except Exception as ex:
                    logger.debug(f"Heading translation failed: {ex}")
                    heading_translation = ""

            if not heading_translation.strip():
                heading_translation = heading_text

            all_sentences.append({
                "session_zid": target_synth_zid,
                "sentence_index": heading_sent_idx,
                "sentence_source": heading_text,
                "sentence_destination": heading_translation,
                "project_id": p_id,
                "deck": deck_path,
            })

            # Lemmatize heading title
            tokens = []
            try:
                tokens = tokenize_text_with_fallback(
                    title,
                    language=lang,
                    config=config,
                    resolved_paths=resolved_paths or {},
                    zid=zid,
                )
            except Exception as tok_err:
                logger.debug(f"Heading tokenization failed: {tok_err}")
                tokens = []

            heading_words = []
            if tokens:
                for tok in tokens:
                    w = tok.get("word") or tok.get("text") or ""
                    lem = tok.get("lemma") or w
                    if w and (tok.get("is_word", True) or any(c.isalpha() for c in w)):
                        if any(c.isalpha() for c in w):
                            heading_words.append((w, lem))
            else:
                raw_words = re.findall(r'\b[^\W\d_]+\b', title, re.UNICODE)
                for w in raw_words:
                    if w.strip():
                        heading_words.append((w.strip(), w.strip()))

            if heading_words:
                pending_heading_lemmas.append({
                    "words": heading_words,
                    "heading_text": heading_text,
                    "heading_translation": heading_translation,
                    "sentence_index": heading_sent_idx,
                    "deck": deck_path,
                })

        for sess in sessions:
            s_zid = sess["session_zid"]
            restored = adapter.restore_session(s_zid)
            if not headers and restored.get("headers"):
                headers = list(restored["headers"])

            sess_rows = restored.get("data_rows", [])
            sess_sents = restored.get("sentences", [])
            sess_text = restored.get("source_text") or restored.get("source_raw_text") or ""
            if sess_text:
                source_texts.append(sess_text)

            deck_idx = headers.index("Deck") if "Deck" in headers else -1
            sent_idx_col = headers.index("SentenceSourceIndex") if "SentenceSourceIndex" in headers else -1

            for row in sess_rows:
                row_copy = list(row)
                if deck_idx != -1:
                    if len(row_copy) > deck_idx:
                        row_copy[deck_idx] = deck_path
                    else:
                        row_copy.extend([""] * (deck_idx - len(row_copy) + 1))
                        row_copy[deck_idx] = deck_path

                if sent_idx_col != -1 and len(row_copy) > sent_idx_col:
                    raw_s = str(row_copy[sent_idx_col]).strip()
                    if raw_s.isdigit():
                        row_copy[sent_idx_col] = str(int(raw_s) + global_sentence_offset)

                all_data_rows.append(row_copy)

            if sess_sents:
                for sent in sess_sents:
                    sent_copy = dict(sent)
                    orig_s_idx = sent_copy.get("sentence_index", 1)
                    sent_copy["session_zid"] = target_synth_zid
                    sent_copy["sentence_index"] = orig_s_idx + global_sentence_offset
                    sent_copy["project_id"] = p_id
                    sent_copy["deck"] = deck_path
                    all_sentences.append(sent_copy)
                max_s = max(s.get("sentence_index", 1) for s in sess_sents)
                global_sentence_offset += max_s
            elif sess_rows:
                all_sentences.append({
                    "session_zid": target_synth_zid,
                    "sentence_index": 1 + global_sentence_offset,
                    "sentence_source": sess_text or "",
                    "sentence_destination": "",
                    "project_id": p_id,
                    "deck": deck_path,
                })
                global_sentence_offset += 1

            chapters.append({
                "project_id": p_id,
                "project_title": node.get("title", ""),
                "deck": deck_path,
                "session_zid": s_zid,
                "word_count": len(sess_rows),
            })
            total_sessions += 1

        for child in node.get("children", []):
            _collect(child, level=level + 1)

    for root_node in trees:
        _collect(root_node, level=0)

    if not headers:
        headers = [
            "Quotation", "WordSource", "WordSource2", "WordSourceInflectedForm", "WordSourceInflectedForm2",
            "WordDestination", "WordDestinationInflectedForm", "WordSourceContext", "SentenceSourceContextLeft",
            "SentenceSource", "SentenceSourceContextRight", "SentenceDestinationContextLeft", "SentenceDestination",
            "SentenceDestinationContextRight", "SentenceDestination2ContextLeft", "SentenceDestination2",
            "SentenceDestination2ContextRight", "SentenceSourceWordlist", "SentenceSourceCloze",
            "SentenceSourceRewriteAISentenceSource", "SentenceSourceRewriteAISentenceDestination",
            "WordSourceMorphologyAI", "Note", "WordRussian", "WordUkrainian", "WordEnglish", "WordGerman",
            "WordSourceMorphemeFirst", "WordSourceMorphemeFirstDefinition", "WordSourceMorphemeSecond",
            "WordSourceMorphemeSecondDefinition", "WordSourceMorphemeThird", "WordSourceMorphemeThirdDefinition",
            "WordSourceMorphemeFourth", "WordSourceMorphemeFourthDefinition", "WordSourceMorphemeFifth",
            "WordSourceMorphemeFifthDefinition", "WordSourceIPA", "WordSourceSynonymAI",
            "WordSourceDefinitionAISentenceSource", "WordSourceDefinitionAISentenceDestination",
            "WordSourceDefinitionFirst", "WordSourceDefinitionFirstClipping", "WordSourceDefinitionSecond",
            "WordDestinationDefinitionFirst", "WordDestinationDefinitionSecond", "WordSourceAudio",
            "SentenceSourceIPA", "SentenceSourceAudio", "Image", "WordSourceCloze", "WordSourceContextAI",
            "TextSource", "TextDestination", "TextSourceURL", "SentenceEnglish", "SentenceGerman",
            "SentenceUkrainian", "SentenceRussian", "Source", "SourceURL", "SeparatorAudio",
            "Source-en-GB", "Source-en-US", "Source-de-DE", "Source-uk-UA", "Source-ru-RU",
            "Destination-en-GB", "Destination-en-US", "Destination-de-DE", "Destination-uk-UA",
            "Destination-ru-RU", "Overlapping", "ToggleAlwaysEmptyField", "Note ID",
            "am-all-morphs", "am-all-morphs-count", "am-unknown-morphs", "am-unknown-morphs-count",
            "am-highlighted", "am-score", "am-score-terms", "am-study-morphs",
            "SentenceSourceIndex", "Deck", "LeitnerBox", "LeitnerDue", "DeskSelected",
            "ClassificationOxford", "ClassificationGoethe",
        ]

    if pending_heading_lemmas and headers:
        col_quot = headers.index("Quotation") if "Quotation" in headers else -1
        col_lemma = headers.index("WordSource") if "WordSource" in headers else -1
        col_inflected = headers.index("WordSourceInflectedForm") if "WordSourceInflectedForm" in headers else -1
        col_sent_src = headers.index("SentenceSource") if "SentenceSource" in headers else -1
        col_sent_dest = headers.index("SentenceDestination") if "SentenceDestination" in headers else -1
        col_sent_idx = headers.index("SentenceSourceIndex") if "SentenceSourceIndex" in headers else -1
        col_deck = headers.index("Deck") if "Deck" in headers else -1
        col_sel = headers.index("DeskSelected") if "DeskSelected" in headers else -1

        for item in pending_heading_lemmas:
            h_text = item["heading_text"]
            h_trans = item.get("heading_translation") or h_text
            s_idx = item["sentence_index"]
            d_path = item["deck"]
            for w, lem in item["words"]:
                h_row = [""] * len(headers)
                if col_quot != -1:
                    h_row[col_quot] = w
                if col_lemma != -1:
                    h_row[col_lemma] = lem
                if col_inflected != -1:
                    h_row[col_inflected] = w
                if col_sent_src != -1:
                    h_row[col_sent_src] = h_text
                if col_sent_dest != -1:
                    h_row[col_sent_dest] = h_trans
                if col_sent_idx != -1:
                    h_row[col_sent_idx] = str(s_idx)
                if col_deck != -1:
                    h_row[col_deck] = d_path
                if col_sel != -1:
                    h_row[col_sel] = "1"
                all_data_rows.append(h_row)

    combined_text = "\n".join(t.strip() for t in source_texts if t.strip())
    all_data_rows = deduplicate_rows_by_lemma(all_data_rows, headers)
    all_data_rows = sort_rows_by_frequency(all_data_rows, headers, lang, config, resolved_paths)

    return {
        "ok": True,
        "session_zid": zid or f"project_{project_id}",
        "project_id": project_id,
        "project_title": project.get("title", ""),
        "slug": project.get("slug") or f"project_{project_id}",
        "source_language": lang,
        "target_language": target_lang,
        "text_mode": "multi",
        "source_text": combined_text,
        "source_raw_text": combined_text,
        "headers": headers,
        "data_rows": all_data_rows,
        "sentences": all_sentences,
        "total_sessions": total_sessions,
        "total_words": len(all_data_rows),
        "comments": [f"# Synthesized Project Session: {project.get('title')} (ID: {project_id})"],
        "chapters": chapters,
        "fingerprint": compute_content_fingerprint(all_data_rows),
    }


class StorageRouter:
    """
    Unified storage router coordinating SQLite and legacy TSV backends,
    supporting automatic fallback and seamless restore operations.
    """
    def __init__(self, config=None, resolved_paths=None, storage_override=None):
        self.config = config
        self.resolved_paths = resolved_paths or {}
        self.storage_override = storage_override
        self.adapter = get_storage_adapter(config, resolved_paths, storage_override)
        self.fallback_to_tsv = True
        if resolved_paths and "storage_fallback_to_tsv" in resolved_paths:
            self.fallback_to_tsv = resolved_paths["storage_fallback_to_tsv"]
        elif config and hasattr(config, "getboolean"):
            self.fallback_to_tsv = config.getboolean(SEC_STORAGE, "fallback_to_tsv", fallback=True)

    def save_session(self, *args, **kwargs):
        return self.adapter.save_session(*args, **kwargs)

    def load_session(self, *args, **kwargs):
        return self.adapter.load_session(*args, **kwargs)

    def restore_session(self, zid: str, **kwargs) -> Dict[str, Any]:
        return self.adapter.restore_session(zid, **kwargs)

    def get_cached_session(self, *args, **kwargs):
        return self.adapter.get_cached_session(*args, **kwargs)

    def save_tsv_rows_safely(self, *args, **kwargs):
        return self.adapter.save_tsv_rows_safely(*args, **kwargs)

    def load_tsv_rows(self, *args, **kwargs):
        return self.adapter.load_tsv_rows(*args, **kwargs)

    def update_word(self, *args, **kwargs):
        return self.adapter.update_word(*args, **kwargs)

    def update_word_selection(self, *args, **kwargs):
        return self.adapter.update_word_selection(*args, **kwargs)

    def update_sentence_translation(self, *args, **kwargs):
        return self.adapter.update_sentence_translation(*args, **kwargs)

    def batch_update_words(self, *args, **kwargs):
        return self.adapter.batch_update_words(*args, **kwargs)

    def delete_session(self, *args, **kwargs):
        return self.adapter.delete_session(*args, **kwargs)

    def list_sessions(self, *args, **kwargs):
        return self.adapter.list_sessions(*args, **kwargs)

    def cleanup_db(self, *args, **kwargs):
        return self.adapter.cleanup_db(*args, **kwargs)

    def vacuum(self, *args, **kwargs):
        return self.adapter.vacuum(*args, **kwargs)

    @contextlib.contextmanager
    def file_lock(self, file_path: Path):
        with self.adapter.file_lock(file_path):
            yield


_DEFAULT_TSV_ADAPTER = TsvStorageAdapter()


def get_storage_adapter(config=None, resolved_paths=None, storage_override=None) -> StorageAdapter:
    """
    Factory function returning the active StorageAdapter (TsvStorageAdapter or SqliteStorageAdapter)
    based on CLI override, config.ini [storage] section, or defaults.
    """
    backend = storage_override
    if not backend:
        if resolved_paths and "storage_backend" in resolved_paths:
            backend = resolved_paths["storage_backend"]
        elif config and hasattr(config, "get"):
            backend = config.get(SEC_STORAGE, "backend", fallback="tsv").strip().lower()
        else:
            backend = "tsv"

    backend = (backend or "tsv").strip().lower()
    if backend == "sqlite":
        return SqliteStorageAdapter(config=config, resolved_paths=resolved_paths)
    return TsvStorageAdapter(config=config, resolved_paths=resolved_paths)


@contextlib.contextmanager
def file_lock(file_path: Path, adapter: Optional[StorageAdapter] = None):
    act_adapter = adapter or _DEFAULT_TSV_ADAPTER
    with act_adapter.file_lock(file_path):
        yield


def _sanitize_rows(data_rows: Optional[List[List[str]]]) -> Optional[List[List[str]]]:
    """
    Sanitizes data rows by replacing internal '[FAILED]' sentinels with empty string.
    """
    if not data_rows:
        return data_rows
    sanitized = []
    for row in data_rows:
        sanitized.append([("" if (isinstance(cell, str) and cell.strip() == "[FAILED]") else cell) for cell in row])
    return sanitized


def load_tsv_rows(tsv_path: Path, adapter: Optional[StorageAdapter] = None) -> Tuple[List[str], List[str], List[List[str]]]:
    act_adapter = adapter or _DEFAULT_TSV_ADAPTER
    return act_adapter.load_tsv_rows(tsv_path)


def save_tsv_rows_safely(tsv_path: Path, comments: List[str], headers: List[str], data_rows: List[List[str]], adapter: Optional[StorageAdapter] = None) -> None:
    act_adapter = adapter or _DEFAULT_TSV_ADAPTER
    act_adapter.save_tsv_rows_safely(tsv_path, comments, headers, _sanitize_rows(data_rows) or [])


def extract_zid(path):
    if not path:
        return "00000000000000"
    name = path.name if hasattr(path, 'name') else Path(str(path)).name
    match = re.match(r'^(\d{14})', name)
    return match.group(1) if match else "00000000000000"


GERMAN_UMLAUT_MAP = {
    'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', 'ẞ': 'ss',
    'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue',
}


def generate_slug(text, max_words=4):
    if not text:
        return "untitled"
    # Remove ASS and HTML tags
    cleaned = re.sub(r'\{[^}]*\}', ' ', text)
    cleaned = re.sub(r'<[^>]*>', ' ', cleaned)

    # Normalize German umlauts matching zid_name.py
    for src, dst in GERMAN_UMLAUT_MAP.items():
        cleaned = cleaned.replace(src, dst)

    tokens = tok.build_word_list_internal(cleaned, keep_spaces=False)
    words = []
    for t in tokens:
        if not t.get("is_word"):
            continue
        raw_text = t.get("text", "")
        sub_parts = tok.split_camel_case(raw_text) or [raw_text]
        for p in sub_parts:
            clean_p = tok.utf8_to_lower("".join(ch for ch in p if ch.isalnum()))
            if clean_p and not clean_p.isdigit():
                words.append(clean_p)
                if len(words) >= max_words:
                    break
        if len(words) >= max_words:
            break

    slug = '-'.join(words)
    return slug if slug else "untitled"


def normalize_bracket_spacing(text: str) -> str:
    """Normalize spacing around brackets: remove inner whitespace in (...), [...], {...}."""
    if not text:
        return text
    # Remove whitespace immediately after opening brackets
    text = re.sub(r'([(\[{])\s+', r'\1', text)
    # Remove whitespace immediately before closing brackets
    text = re.sub(r'\s+([)\]}])', r'\1', text)
    return text


def is_tsv_llm_filled(headers, data_rows, mapping):

    role_fields = get_role_fields(mapping, headers)
    col_lemma = headers.index(role_fields.get('lemma', 'WordSource')) if role_fields and role_fields.get('lemma', 'WordSource') in headers else -1
    col_word_dest = headers.index(role_fields.get('word_translation', 'WordDestination')) if role_fields and role_fields.get('word_translation', 'WordDestination') in headers else -1
    
    if col_lemma != -1 and col_word_dest != -1:
        for row in data_rows:
            if len(row) > col_lemma and row[col_lemma].strip():
                if len(row) <= col_word_dest or not row[col_word_dest].strip():
                    return False

    ai_cols = ['WordSourceMorphologyAI', 'WordSourceIPA']
    present_ai_cols = [col for col in ai_cols if col in headers]
    if present_ai_cols:
        for col in present_ai_cols:
            col_idx = headers.index(col)
            for row in data_rows:
                lemma = row[col_lemma].strip() if col_lemma != -1 and len(row) > col_lemma else ''
                if len(lemma) > 3:
                    if len(row) <= col_idx or not row[col_idx].strip():
                        return False
                        
    return True


def is_base_translation_finished(headers, data_rows, role_fields, lemma_base_provider=None):
    if not data_rows:
        return True
    col_lemma = headers.index(role_fields.get('lemma', 'WordSource')) if role_fields and role_fields.get('lemma', 'WordSource') in headers else -1
    col_word_dest = headers.index(role_fields.get('word_translation', 'WordDestination')) if role_fields and role_fields.get('word_translation', 'WordDestination') in headers else -1
    col_ipa = headers.index(role_fields.get('ipa', 'WordSourceIPA')) if role_fields and role_fields.get('ipa', 'WordSourceIPA') in headers else -1
    col_morph = headers.index(role_fields.get('morphology', 'WordSourceMorphologyAI')) if role_fields and role_fields.get('morphology', 'WordSourceMorphologyAI') in headers else -1
    
    if col_lemma != -1 and col_word_dest != -1:
        for row in data_rows:
            if len(row) > col_lemma and row[col_lemma].strip():
                if len(row) <= col_word_dest or not row[col_word_dest].strip() or 'skeleton-loader' in row[col_word_dest]:
                    return False
                if lemma_base_provider == 'intellifiller':
                    if col_ipa != -1 and (len(row) <= col_ipa or not row[col_ipa].strip() or 'skeleton-loader' in row[col_ipa]):
                        return False
                    if col_morph != -1 and (len(row) <= col_morph or not row[col_morph].strip() or 'skeleton-loader' in row[col_morph]):
                        return False
    return True



def find_working_tsv(results_dir, zid, language="en", storage_adapter=None):
    if not zid:
        return None
    p = Path(zid)
    if p.exists() and p.is_file():
        return p
    if results_dir is None:
        return None
    results_dir = Path(results_dir)
    if (results_dir / zid).exists() and (results_dir / zid).is_file():
        return results_dir / zid
    if not str(zid).endswith('.tsv') and (results_dir / f"{zid}.tsv").exists():
        return results_dir / f"{zid}.tsv"

    files = list(results_dir.glob(f"{zid}-*.{language}.tsv"))
    if not files:
        files = list(results_dir.glob(f"{zid}-*.tsv"))
    if not files:
        files = list(results_dir.glob(f"*{zid}*.tsv"))
    if files:
        return files[0]

    # Check for matching .updates directories
    update_dirs = [d for d in results_dir.glob(f"{zid}-*.{language}.updates") if d.is_dir()]
    if not update_dirs:
        update_dirs = [d for d in results_dir.glob(f"{zid}-*.updates") if d.is_dir()]
    if not update_dirs:
        update_dirs = [d for d in results_dir.glob(f"*{zid}*.updates") if d.is_dir()]
    if update_dirs:
        first_dir = update_dirs[0]
        base_stem = first_dir.name[:-8] if first_dir.name.endswith('.updates') else first_dir.stem
        return results_dir / f"{base_stem}.tsv"

    # Fallback to query SQLite session slug if storage adapter is available
    adapter = storage_adapter
    if adapter is None:
        try:
            adapter = get_storage_adapter()
        except Exception:
            adapter = None

    if adapter and getattr(adapter, 'backend_name', '') == 'sqlite':
        try:
            bundle = adapter.db.get_session_bundle(str(zid)) if hasattr(adapter, 'db') and hasattr(adapter.db, 'get_session_bundle') else None
            if bundle and bundle.get("session"):
                sess = bundle["session"]
                slug = sess.get("slug") or ""
                source_lang = sess.get("source_language") or language or "en"
                if slug:
                    return results_dir / f"{zid}-{slug}.{source_lang}.tsv"
                else:
                    return results_dir / f"{zid}.{source_lang}.tsv"
        except Exception:
            pass

    return None

def run_google_translation(text, source, target, config, resolved_paths, zid=None, trace_id=None):
    server_url = None
    if config:
        if config.has_section(SEC_SERVICES):
            server_url = config.get(SEC_SERVICES, 'translation_server_url', fallback=None)
        elif config.has_section('services'):
            server_url = config.get('services', 'translation_server_url', fallback=None)

    if server_url:
        timeout = config.getint(SEC_TIMEOUTS, 'translation_timeout', fallback=60) if config else 60
        resp = query_translation_server(text, source, target, provider="google", server_url=server_url, zid=zid, trace_id=trace_id, timeout=timeout)
        if resp:
            if resp.get("status") == "success":
                return resp.get("translated_text", "")
            elif resp.get("status") == "error":
                results_dir = resolve_results_dir(resolved_paths, config)
                if zid and results_dir:
                    sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
                    sess_logger.error(f"[{resp.get('code')}] {resp.get('message')}")
                raise TranslationException(resp.get("message", "Google translation failed"), envelope=resp)

    python_exe = resolved_paths['deep_translator_python']
    script_path = resolved_paths['translate_google_script']
    
    cmd = [
        str(python_exe),
        str(script_path),
        "--text", text,
        "--source", source,
        "--target", target,
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
    if config.getboolean(SEC_PIPELINE, 'use_local_fork', fallback=True):
        cmd.append("--use-local-fork")
        
    timeout = config.getint(SEC_TIMEOUTS, 'translation_timeout', fallback=60)
    logger.info(f"Running Google translation command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if res.returncode == 0:
        return res.stdout.strip()
    else:
        err_envelope = None
        for line in reversed(res.stderr.strip().splitlines()):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    data = json.loads(line)
                    if data.get("status") == "error" or "code" in data:
                        err_envelope = data
                        break
                except Exception:
                    pass
        if not err_envelope:
            err_envelope = {
                "status": "error",
                "zid": zid,
                "trace_id": trace_id,
                "code": "ERR_TRANSLATION_FAILED",
                "message": f"Google translation failed: {res.stderr.strip()}",
                "provider": "google",
                "details": {"stderr": res.stderr.strip(), "returncode": res.returncode}
            }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"[{err_envelope.get('code')}] {err_envelope.get('message')}")
        raise TranslationException(err_envelope.get("message"), envelope=err_envelope)

def run_deepl_translation(text, source, target, config, resolved_paths, zid=None, trace_id=None):
    deepl_key = get_deepl_key(config, resolved_paths['base_dir'])
    if not deepl_key:
        err_envelope = {
            "status": "error",
            "zid": zid,
            "trace_id": trace_id,
            "code": "ERR_DEEPL_AUTH",
            "message": "DeepL API key not configured or failed to resolve",
            "provider": "deepl",
            "details": {}
        }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"[{err_envelope['code']}] {err_envelope['message']}")
        raise TranslationException(err_envelope["message"], envelope=err_envelope)

    server_url = None
    if config:
        if config.has_section(SEC_SERVICES):
            server_url = config.get(SEC_SERVICES, 'translation_server_url', fallback=None)
        elif config.has_section('services'):
            server_url = config.get('services', 'translation_server_url', fallback=None)

    if server_url:
        timeout = config.getint(SEC_TIMEOUTS, 'translation_timeout', fallback=60) if config else 60
        resp = query_translation_server(text, source, target, provider="deepl", server_url=server_url, zid=zid, trace_id=trace_id, deepl_api_key=deepl_key, timeout=timeout)
        if resp:
            if resp.get("status") == "success":
                return resp.get("translated_text", "")
            elif resp.get("status") == "error":
                results_dir = resolve_results_dir(resolved_paths, config)
                if zid and results_dir:
                    sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
                    sess_logger.error(f"[{resp.get('code')}] {resp.get('message')}")
                raise TranslationException(resp.get("message", "DeepL translation failed"), envelope=resp)

    python_exe = resolved_paths['deep_translator_python']
    script_path = resolved_paths['translate_deepl_script']
        
    cmd = [
        str(python_exe),
        str(script_path),
        "--text", text,
        "--source", source,
        "--target", target,
        "--deepl-api-key", deepl_key,
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
    if config.getboolean(SEC_PIPELINE, 'use_local_fork', fallback=True):
        cmd.append("--use-local-fork")
        
    timeout = config.getint(SEC_TIMEOUTS, 'translation_timeout', fallback=60)
    
    logged_cmd = cmd[:]
    if "--deepl-api-key" in logged_cmd:
        idx = logged_cmd.index("--deepl-api-key")
        if idx + 1 < len(logged_cmd):
            logged_cmd[idx + 1] = "********"
    logger.info(f"Running DeepL translation command: {' '.join(logged_cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if res.returncode == 0:
        return res.stdout.strip()
    else:
        err_envelope = None
        for line in reversed(res.stderr.strip().splitlines()):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    data = json.loads(line)
                    if data.get("status") == "error" or "code" in data:
                        err_envelope = data
                        break
                except Exception:
                    pass
        if not err_envelope:
            err_envelope = {
                "status": "error",
                "zid": zid,
                "trace_id": trace_id,
                "code": "ERR_TRANSLATION_FAILED",
                "message": f"DeepL translation failed: {res.stderr.strip()}",
                "provider": "deepl",
                "details": {"stderr": res.stderr.strip(), "returncode": res.returncode}
            }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"[{err_envelope.get('code')}] {err_envelope.get('message')}")
        raise TranslationException(err_envelope.get("message"), envelope=err_envelope)

def run_argos_translation(text, source, target, config, resolved_paths, zid=None, trace_id=None):
    server_url = None
    if config:
        if config.has_section(SEC_SERVICES):
            server_url = config.get(SEC_SERVICES, 'translation_server_url', fallback=None)
        elif config.has_section('services'):
            server_url = config.get('services', 'translation_server_url', fallback=None)

    if server_url:
        timeout = config.getint(SEC_TIMEOUTS, 'translation_timeout', fallback=60) * 2 if config else 120
        resp = query_translation_server(text, source, target, provider="argos", server_url=server_url, zid=zid, trace_id=trace_id, timeout=timeout)
        if resp:
            if resp.get("status") == "success":
                return resp.get("translated_text", "")
            elif resp.get("status") == "error":
                results_dir = resolve_results_dir(resolved_paths, config)
                if zid and results_dir:
                    sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
                    sess_logger.error(f"[{resp.get('code')}] {resp.get('message')}")
                raise TranslationException(resp.get("message", "Argos translation failed"), envelope=resp)

    python_exe = resolved_paths.get('argotranslate_python')
    script_path = resolved_paths.get('argotranslate_script')
    
    if not python_exe or not script_path:
        err_envelope = {
            "status": "error",
            "zid": zid,
            "trace_id": trace_id,
            "code": "ERR_DEPENDENCY_MISSING",
            "message": "argotranslate_python or argotranslate_script not configured in config.ini",
            "provider": "argos",
            "details": {}
        }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"[{err_envelope['code']}] {err_envelope['message']}")
        raise TranslationException(err_envelope["message"], envelope=err_envelope)
        
    cmd = [
        str(python_exe),
        str(script_path),
        "-f", source,
        "-t", target
    ]
        
    # Double the timeout for local offline translation to handle model loading overhead and concurrent requests
    timeout = config.getint(SEC_TIMEOUTS, 'translation_timeout', fallback=60) * 2
    logger.info(f"Running Argos translation command: {' '.join(cmd)}")
    
    try:
        # Pass text via stdin to avoid command-line length limits and escaping issues on Windows
        res = subprocess.run(cmd, input=text, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
        if res.returncode == 0:
            return res.stdout.strip()
        else:
            err_envelope = {
                "status": "error",
                "zid": zid,
                "trace_id": trace_id,
                "code": "ERR_TRANSLATION_FAILED",
                "message": f"Argos translation failed (code {res.returncode}): {res.stderr.strip()}",
                "provider": "argos",
                "details": {"stderr": res.stderr.strip(), "returncode": res.returncode}
            }
            results_dir = resolve_results_dir(resolved_paths, config)
            if zid and results_dir:
                sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
                sess_logger.error(f"[{err_envelope['code']}] {err_envelope['message']}")
            raise TranslationException(err_envelope["message"], envelope=err_envelope)
    except subprocess.TimeoutExpired as e:
        err_envelope = {
            "status": "error",
            "zid": zid,
            "trace_id": trace_id,
            "code": "ERR_TIMEOUT",
            "message": f"Argos translation timed out after {timeout} seconds. Model loading under concurrent load may exceed limits: {e}",
            "provider": "argos",
            "details": {"timeout": timeout}
        }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"[{err_envelope['code']}] {err_envelope['message']}")
        raise TranslationException(err_envelope["message"], envelope=err_envelope)

def is_network_online_multi(hosts, port=53, timeout=1.0):
    if not hosts:
        return True
        
    def check_host(host):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host.strip(), port))
            s.close()
            return True
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        futures = [executor.submit(check_host, h) for h in hosts]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                return True
        return False

def translate_text(text, source, target, config, resolved_paths, provider, zid=None, trace_id=None):
    with TraceTimer("translate_text", zid or "unknown", config, resolved_paths):
        return _translate_text_impl(text, source, target, config, resolved_paths, provider, zid=zid, trace_id=trace_id)

def _translate_text_impl(text, source, target, config, resolved_paths, provider, zid=None, trace_id=None):
    auto_fallback = config.getboolean(SEC_PIPELINE, 'auto_offline_fallback', fallback=True)
    
    check_ips_str = config.get(SEC_PIPELINE, 'fast_connectivity_check_ips', fallback=config.get(SEC_PIPELINE, 'fast_connectivity_check_ip', fallback='8.8.8.8, 1.1.1.1'))
    check_ips = [ip.strip() for ip in check_ips_str.split(',') if ip.strip()]
    
    if auto_fallback and check_ips and provider != 'argos':
        if not is_network_online_multi(hosts=check_ips):
            logger.warning(f"Fast connectivity check to {check_ips} failed. Bypassing online providers and going straight to Argos.")
            try:
                return run_argos_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
            except Exception as ex2:
                logger.error(f"Argos offline fallback failed: {ex2}")
                raise ex2

    try:
        if provider == 'google':
            return run_google_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
        elif provider == 'deepl':
            return run_deepl_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
        elif provider == 'argos':
            return run_argos_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
        elif provider == 'mock':
            time.sleep(0.01) # Simulate deterministic micro-delay
            return f"[MOCK] {text}"
        elif provider in ('combined', 'intellifiller'):
            try:
                return run_google_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
            except Exception as e:
                logger.warning(f"Google translation failed: {e}. Trying DeepL failover...")
                return run_deepl_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
        elif provider == 'none':
            return ""
        else:
            raise Exception(f"Unsupported translation provider: {provider}")
    except Exception as e:
        if auto_fallback and provider != 'argos':
            # Verify if it's an actual offline event vs an API rate limit (429)
            if check_ips and not is_network_online_multi(hosts=check_ips):
                logger.warning(f"Primary provider '{provider}' failed: {e}. Network appears offline. Auto-fallback to Argos...")
                try:
                    return run_argos_translation(text, source, target, config, resolved_paths, zid=zid, trace_id=trace_id)
                except Exception as ex2:
                    logger.error(f"Argos offline fallback failed: {ex2}")
                    raise ex2
            else:
                # Network is online. Likely a rate limit or transient API error. Raise to trigger retry loop.
                logger.warning(f"Primary provider '{provider}' failed: {e}. Network is online. Raising exception for retries...")
                raise e
        else:
            if provider != 'argos':
                logger.warning(f"Provider '{provider}' failed: {e}. Auto-offline fallback is disabled.")
            raise e

def translate_lemmas_fast_path(lemmas, source, target, config, resolved_paths, provider):
    if not lemmas:
        return {}
        
    compact_line = "; ".join(lemmas)
    translations = {}
    try:
        translated_line = translate_text(compact_line, source, target, config, resolved_paths, provider)
        parts = [p.strip() for p in translated_line.split(';')]
        if len(parts) != len(lemmas):
            # Check secondary delimiters like newline or period
            lines = [p.strip() for p in translated_line.splitlines() if p.strip()]
            if len(lines) == len(lemmas):
                parts = lines
            else:
                periods = [p.strip() for p in translated_line.split('.') if p.strip()]
                if len(periods) == len(lemmas):
                    parts = periods
                    
        if len(parts) == len(lemmas):
            logger.info("Fast-path lemma translation aligned successfully.")
            for i, lemma in enumerate(lemmas):
                val = parts[i]
                if not val:
                    try:
                        val = translate_text(lemma, source, target, config, resolved_paths, provider)
                    except Exception:
                        val = ""
                translations[lemma] = val.strip() if val else ""
            return translations
        else:
            logger.warning(f"Fast-path alignment failure: expected {len(lemmas)} parts, got {len(parts)}. Falling back to individual calls.")
    except Exception as e:
        logger.warning(f"Fast-path translation failed: {e}. Falling back to individual calls.")
        
    for lemma in lemmas:
        try:
            val = translate_text(lemma, source, target, config, resolved_paths, provider)
            translations[lemma] = val.strip() if val else ""
        except Exception as e:
            logger.warning(f"Failed to translate lemma '{lemma}': {e}")
            translations[lemma] = ""
    return translations

EXIT_PARTIAL_TRANSLATION_PERSISTED = 2

class TranslationAlignmentError(Exception):
    def __init__(self, message, partial_dict=None):
        super().__init__(message)
        self.partial_dict = partial_dict or {}

_LEADING_PUNCT_RE = re.compile(r'^([.,!?;:\s]+)\s*')
_PUNCT_ONLY_RE = re.compile(r'^[.,!?;:\s]+$')

def clean_sentence_splits(lines):
    cleaned = list(lines)
    for i in range(1, len(cleaned)):
        line = cleaned[i]
        combined = line.strip()
        if not combined:
            continue
        if _PUNCT_ONLY_RE.match(combined):
            cleaned[i - 1] = cleaned[i - 1] + combined
            cleaned[i] = ""
        else:
            m = _LEADING_PUNCT_RE.match(combined)
            if m:
                punct = m.group(1).rstrip()
                remainder = combined[m.end():].strip()
                cleaned[i - 1] = cleaned[i - 1] + punct
                cleaned[i] = remainder
    return cleaned

def _split_long_line(line, max_chars=90):
    words = line.split()
    if not words:
        return []
    out = []
    cur = words[0]
    for word in words[1:]:
        candidate = f"{cur} {word}"
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            out.append(cur)
            cur = word
    out.append(cur)
    return out

def split_single_mode_text(text, max_chars=90, abbrevs=None, terminators=".!?:", punctuation_marks=".,;:!?()\"[]{}—–"):
    import re
    if abbrevs is None:
        abbrevs = {
            "ca", "z.b", "usw", "uzw", "bzw", "etc", "t.con", "d.h", "u.a", "vgl", "ggf",
            "bspw", "u.u", "i.d.r", "bzgl", "evtl", "sog", "bsp", "z.zt", "m.e",
            "e.g", "i.e", "approx", "vs", "cf", "ltd", "co", "inc", "prof", "dr",
            "mr", "mrs", "ms"
        }
    
    escaped_terms = "".join(re.escape(c) for c in terminators)
    candidates = list(re.finditer(rf'(?<=[{escaped_terms}])\s+|[\r\n]+', text))
    
    splits = []
    last_idx = 0
    for m in candidates:
        split_pos = m.start()
        punc = text[split_pos - 1] if split_pos > 0 else ''
        
        if punc == '.':
            preceding_part = text[last_idx:split_pos]
            match = re.search(r'([a-zA-Z0-9.-]+)$', preceding_part.strip())
            if match:
                word = match.group(1).lower().rstrip('.')
                full_word = word
                if len(word) == 1 and preceding_part.strip().endswith(f" {word}"):
                    prev_part = preceding_part.strip()[:-len(word)].strip()
                    prev_match = re.search(r'([a-zA-Z0-9.-]+)$', prev_part)
                    if prev_match:
                        prev_word = prev_match.group(1).lower()
                        if prev_word.endswith('.'):
                            full_word = f"{prev_word} {word}"
                
                clean_word = full_word.replace(' ', '')
                if clean_word in abbrevs or clean_word.replace('.', '') in abbrevs:
                    continue
                clean_word_no_dot = clean_word.replace('.', '')
                if re.match(r'^[a-zA-Z]$', clean_word_no_dot):
                    continue
                if clean_word_no_dot.isdigit():
                    continue
        
        splits.append(split_pos)
        
    sentences = []
    start = 0
    for pos in splits:
        sentences.append(text[start:pos].strip())
        spaces_match = re.match(r'^\s+', text[pos:])
        start = pos + (spaces_match.end() if spaces_match else 0)
    sentences.append(text[start:].strip())
    sentences = [s for s in sentences if s]
    
    if not sentences:
        return []
        
    has_punctuation = any(char in punctuation_marks for char in text)
    if len(sentences) <= 1 and max_chars > 0 and len(text) > max_chars and not has_punctuation:
        return _split_long_line(text, max_chars)
    return sentences

def pad_sentences(sentences, original_text, words_before=0, words_after=0, max_words=0):
    if not (words_before or words_after):
        return sentences
        
    import re
    spans = []
    current_pos = 0
    for s in sentences:
        start = original_text.find(s, current_pos)
        if start == -1:
            start = current_pos
        end = start + len(s)
        spans.append((start, end))
        current_pos = end
        
    # Get all top-level orthographic tokens
    tokens = tok.build_orthographic_token_spans(original_text)
    
    # Pre-calculate the character span of each token in original_text
    token_spans = []
    curr_idx = 0
    for t in tokens:
        t_len = len(t["text"])
        token_spans.append({
            "start": curr_idx,
            "end": curr_idx + t_len,
            "is_word": t["is_word"],
            "text": t["text"]
        })
        curr_idx += t_len
        
    padded = []
    for i, (s_i, e_i) in enumerate(spans):
        pad_s = s_i
        pad_e = e_i
        
        # Word-based padding using the orthographic token spans
        if words_before > 0:
            words_before_tokens = [ts for ts in token_spans if ts["end"] <= s_i and ts["is_word"]]
            if len(words_before_tokens) >= words_before:
                pad_s = words_before_tokens[-words_before]["start"]
            else:
                pad_s = 0
                
        if words_after > 0:
            words_after_tokens = [ts for ts in token_spans if ts["start"] >= e_i and ts["is_word"]]
            if len(words_after_tokens) >= words_after:
                pad_e = words_after_tokens[words_after - 1]["end"]
            else:
                pad_e = len(original_text)
                
        padded_sentence = original_text[pad_s:pad_e].replace('\n', ' ').replace('\r', ' ').strip()
        padded_sentence = re.sub(r'\s+', ' ', padded_sentence)
        
        # Truncate context if it exceeds max_words
        if max_words > 0:
            padded_words = tok.build_orthographic_word_list(padded_sentence)
            if len(padded_words) > max_words:
                target_sentence = sentences[i]
                target_words = tok.build_orthographic_word_list(target_sentence)
                if target_words:
                    n_p = len(padded_words)
                    n_t = len(target_words)
                    
                    target_start_idx = -1
                    for j in range(n_p - n_t + 1):
                        if padded_words[j:j+n_t] == target_words:
                            target_start_idx = j
                            break
                            
                    if target_start_idx == -1:
                        target_start_idx = (n_p - n_t) // 2
                        
                    target_end_idx = target_start_idx + n_t - 1
                    
                    span = n_t
                    if span >= max_words:
                        crop_start = target_start_idx
                        crop_end = target_end_idx
                    else:
                        left_cap = (max_words - span) // 2
                        right_cap = max_words - span - left_cap
                        
                        actual_left_cap = min(left_cap, target_start_idx)
                        actual_right_cap = min(right_cap, n_p - 1 - target_end_idx)
                        
                        leftover_right = left_cap - actual_left_cap
                        leftover_left = right_cap - actual_right_cap
                        
                        if leftover_right > 0:
                            actual_right_cap = min(actual_right_cap + leftover_right, n_p - 1 - target_end_idx)
                        if leftover_left > 0:
                            actual_left_cap = min(actual_left_cap + leftover_left, target_start_idx)
                            
                        crop_start = target_start_idx - actual_left_cap
                        crop_end = target_end_idx + actual_right_cap
                        
                    p_tokens = tok.build_orthographic_token_spans(padded_sentence)
                    p_token_spans = []
                    p_curr_idx = 0
                    for t in p_tokens:
                        t_len = len(t["text"])
                        p_token_spans.append({
                            "start": p_curr_idx,
                            "end": p_curr_idx + t_len,
                            "is_word": t["is_word"]
                        })
                        p_curr_idx += t_len
                        
                    word_token_spans = [ts for ts in p_token_spans if ts["is_word"]]
                    if word_token_spans:
                        f_char = 0 if crop_start == 0 else word_token_spans[crop_start]["start"]
                        l_char = len(padded_sentence) if crop_end == len(word_token_spans) - 1 else word_token_spans[crop_end]["end"]
                        padded_sentence = padded_sentence[f_char:l_char].strip()
                        
        padded.append(padded_sentence)
        
    return padded


def pad_translated_sentences(translated_sentences, words_before=0, words_after=0, max_words=0):
    """Pad each translated sentence with context words from neighbouring translated sentences.

    Unlike pad_sentences() which works on source-language character spans, this function
    operates entirely on the translated word arrays — no second API call is required.
    """
    if not (words_before or words_after) or not translated_sentences:
        return translated_sentences

    padded = []
    num_sentences = len(translated_sentences)

    for i, s in enumerate(translated_sentences):
        words_b_str = ""
        if words_before > 0 and i > 0:
            prev_s = translated_sentences[i - 1]
            prev_words = tok.build_orthographic_token_spans(prev_s)
            valid_word_indices = [idx for idx, t in enumerate(prev_words) if t["is_word"]]
            if len(valid_word_indices) >= words_before:
                start_token_idx = valid_word_indices[-words_before]
                words_b_str = "".join(t["text"] for t in prev_words[start_token_idx:])
            else:
                words_b_str = prev_s
            words_b_str = words_b_str.strip()

        words_a_str = ""
        if words_after > 0 and i < num_sentences - 1:
            next_s = translated_sentences[i + 1]
            next_words = tok.build_orthographic_token_spans(next_s)
            valid_word_indices = [idx for idx, t in enumerate(next_words) if t["is_word"]]
            if len(valid_word_indices) >= words_after:
                end_token_idx = valid_word_indices[words_after - 1]
                words_a_str = "".join(t["text"] for t in next_words[:end_token_idx + 1])
            else:
                words_a_str = next_s
            words_a_str = words_a_str.strip()

        parts = []
        if words_b_str:
            parts.append(words_b_str)
        parts.append(s.strip())
        if words_a_str:
            parts.append(words_a_str)

        padded_sentence = " ".join(parts).replace('\n', ' ').replace('\r', ' ').strip()
        padded_sentence = re.sub(r'\s+', ' ', padded_sentence)

        # Truncate context if it exceeds max_words by removing words from the edges.
        if max_words > 0:
            padded_words = tok.build_orthographic_word_list(padded_sentence)
            if len(padded_words) > max_words:
                before_word_count = len(tok.build_orthographic_word_list(words_b_str)) if words_b_str else 0
                after_word_count = len(tok.build_orthographic_word_list(words_a_str)) if words_a_str else 0
                n_p = len(padded_words)
                excess = n_p - max_words
                # Remove excess from context only — never touch target sentence words.
                to_remove_start = min(before_word_count, excess)
                excess -= to_remove_start
                to_remove_end = min(after_word_count, excess)

                start_idx = to_remove_start
                end_idx = n_p - to_remove_end if to_remove_end > 0 else n_p
                padded_words = padded_words[start_idx:end_idx]
                padded_sentence = " ".join(padded_words)

        padded.append(padded_sentence)

    return padded


def _effective_text_mode(text, configured_text_mode=None):
    if configured_text_mode == 'multi':
        return 'multi'
    stripped = text.strip()
    return 'multi' if ('\n' in stripped or '\r' in stripped) else 'single'

def _validate_translated_line(orig_line, trans_line, idx, config):
    if not trans_line.strip():
        raise ValueError(f"Empty line returned for non-empty source at line index {idx}")
        
    word_count_check = config.getboolean(SEC_TRANSLATION, 'translation_word_count_check', fallback=False)
    if word_count_check:
        orig_words = len(orig_line.split())
        trans_words = len(trans_line.split())
        if orig_words > 0:
            abs_tolerance = config.getint(SEC_TRANSLATION, 'translation_word_count_abs_tolerance', fallback=5)
            if abs(orig_words - trans_words) > abs_tolerance:
                min_ratio = config.getfloat(SEC_TRANSLATION, 'translation_word_count_min_ratio', fallback=0.25)
                max_ratio = config.getfloat(SEC_TRANSLATION, 'translation_word_count_max_ratio', fallback=3.5)
                ratio = trans_words / orig_words
                if ratio < min_ratio or ratio > max_ratio:
                    raise ValueError(
                        f"Word count mismatch at line {idx}: original has {orig_words} words, "
                        f"translated has {trans_words} words (ratio {ratio:.2f} outside [{min_ratio}, {max_ratio}])"
                    )

def _build_chunks(lines, chunk_size, config):
    chunks = []
    adaptive_max_lines = config.getint(SEC_TRANSLATION, 'translation_adaptive_max_lines', fallback=30)
    adaptive_max_chars = config.getint(SEC_TRANSLATION, 'translation_adaptive_max_chars', fallback=1000)
    
    if chunk_size > 0:
        chunk = []
        chunk_indices = []
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            chunk.append(line)
            chunk_indices.append(idx)
            if len(chunk) == chunk_size:
                chunks.append((chunk, chunk_indices))
                chunk = []
                chunk_indices = []
        if chunk:
            chunks.append((chunk, chunk_indices))
    else:
        chunk = []
        chunk_indices = []
        chunk_char_count = 0
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            
            line_len = len(line)
            if len(chunk) >= adaptive_max_lines or (chunk_char_count + line_len) > adaptive_max_chars:
                chunks.append((chunk, chunk_indices))
                chunk = []
                chunk_indices = []
                chunk_char_count = 0
            
            chunk.append(line)
            chunk_indices.append(idx)
            chunk_char_count += line_len
        if chunk:
            chunks.append((chunk, chunk_indices))
    return chunks

def split_by_proportion(text, lengths):
    if not text or not lengths:
        return [text.strip()] if text else []
    if len(lengths) == 1:
        return [text.strip()]
    total = sum(lengths)
    if total == 0:
        n = len(lengths)
        equal = len(text) // n
        return [text[i * equal:(i + 1) * equal].strip() for i in range(n - 1)] + [text[(n - 1) * equal:].strip()]
    parts = []
    remaining = text.strip()
    remaining_total = total
    for i, length in enumerate(lengths):
        if i == len(lengths) - 1:
            parts.append(remaining.strip())
            break
        if not remaining:
            parts.extend([''] * (len(lengths) - i))
            break
        target_idx = int(round(len(remaining) * length / remaining_total))
        target_idx = max(1, min(target_idx, len(remaining) - 1))
        search_window = max(target_idx, len(remaining) - target_idx)
        split_idx = None
        for offset in range(search_window + 1):
            for candidate in (target_idx - offset, target_idx + offset):
                if 1 <= candidate < len(remaining) - 1 and remaining[candidate] == ' ':
                    split_idx = candidate
                    break
            if split_idx is not None:
                break
        if split_idx is None:
            split_idx = target_idx
        parts.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()
        remaining_total -= length
    return parts

def make_merge_split_marker(index):
    return f"[[KWSPLIT{index:04d}]]"

def split_merged_text_by_markers(text, markers):
    if not markers:
        return [text.strip()]
    parts = []
    remaining = text
    for marker in markers:
        marker_idx = remaining.find(marker)
        if marker_idx < 0:
            raise ValueError(f"Missing merge split marker in translated text: {marker}")
        parts.append(remaining[:marker_idx].strip())
        remaining = remaining[marker_idx + len(marker):]
    parts.append(remaining.strip())
    return parts

def _validate_translation_config(config):
    if not config.has_section(SEC_TRANSLATION):
        return
    split_mode = config.get(SEC_TRANSLATION, 'translation_split_mode', fallback='newline_join')
    word_count_check = config.getboolean(SEC_TRANSLATION, 'translation_word_count_check', fallback=False)
    if split_mode == 'proportional' and word_count_check:
        logger.warning(
            "Config validation warning: translation_word_count_check = true is incompatible with "
            "translation_split_mode = proportional. Forcing translation_word_count_check to false."
        )
        config.set(SEC_TRANSLATION, 'translation_word_count_check', 'false')

def _write_translation_txt(text, effective_text_mode, sentence_translations_raw, out_path, *, save_flag, overwrite=False):
    if not save_flag:
        return
    if not sentence_translations_raw:
        return
    if not overwrite and out_path.exists() and out_path.stat().st_size > 0:
        return
        
    out_path.parent.mkdir(parents=True, exist_ok=True)
        
    if effective_text_mode == 'single':
        if 'FULL_TEXT' in sentence_translations_raw:
            translation_text_out = sentence_translations_raw['FULL_TEXT']
        else:
            sorted_keys = sorted([k for k in sentence_translations_raw.keys() if isinstance(k, int) or (isinstance(k, str) and k.isdigit())], key=int)
            translation_text_out = " ".join(sentence_translations_raw.get(i, "").strip() for i in sorted_keys if sentence_translations_raw.get(i, ""))
    else:
        num_lines = len(text.splitlines())
        translation_lines = [sentence_translations_raw.get(i, "").strip() for i in range(num_lines)]
        translation_text_out = "\n".join(translation_lines)
        
    out_path.write_text(translation_text_out, encoding='utf-8')

def resolve_translations(text, text_mode, data_rows, col_index, col_sentence_dest,
                         sentence_translations_raw, tsv_path, comments, headers,
                         *, col_text_dest=-1, persist=True, return_single=False,
                         adapter: Optional[StorageAdapter] = None, config=None, resolved_paths=None):
    eff_mode = _effective_text_mode(text, text_mode)
    act_adapter = adapter or (get_storage_adapter(config, resolved_paths) if (config or resolved_paths) else None) or _DEFAULT_TSV_ADAPTER
    
    content_to_absolute = {}
    if eff_mode != 'single':
        c_idx = 0
        for a_idx, ln in enumerate(text.splitlines()):
            if ln.strip():
                content_to_absolute[c_idx] = a_idx
                c_idx += 1
    
    for row in data_rows:
        content_line_idx = 0
        if col_index != -1 and len(row) > col_index:
            try:
                content_line_idx = int(row[col_index]) - 1
            except ValueError:
                pass
        
        abs_idx = content_line_idx if eff_mode == 'single' else content_to_absolute.get(content_line_idx, 0)
        
        if col_sentence_dest != -1:
            while len(row) <= col_sentence_dest:
                row.append("")
            row[col_sentence_dest] = sentence_translations_raw.get(abs_idx, "")
            
        if eff_mode == 'single' and col_text_dest != -1:
            while len(row) <= col_text_dest:
                row.append("")
            row[col_text_dest] = sentence_translations_raw.get('FULL_TEXT', sentence_translations_raw.get(0, ""))
            
    if persist and tsv_path:
        with act_adapter.file_lock(tsv_path):
            act_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            
    if return_single:
        if text_mode == 'single':
            if 'FULL_TEXT' in sentence_translations_raw:
                return sentence_translations_raw['FULL_TEXT']
            sorted_keys = sorted([k for k in sentence_translations_raw.keys() if isinstance(k, int) or (isinstance(k, str) and k.isdigit())], key=int)
            return " ".join([sentence_translations_raw.get(i, "").strip() for i in sorted_keys if sentence_translations_raw.get(i, "")])
        return sentence_translations_raw.get(0, "")
    return None

def format_translated_html(sentence_translations, text_mode="single", text="", config=None):
    if not sentence_translations:
        return ""
    if isinstance(sentence_translations, str):
        raw_lines = [sentence_translations]
    elif isinstance(sentence_translations, dict):
        sorted_keys = sorted([k for k in sentence_translations.keys() if isinstance(k, int) or (isinstance(k, str) and k.isdigit())], key=int)
        raw_lines = [sentence_translations[k] for k in sorted_keys if sentence_translations[k]]
        if not raw_lines and sentence_translations:
            raw_lines = [str(v) for v in sentence_translations.values() if v]
    elif isinstance(sentence_translations, (list, tuple)):
        raw_lines = [str(v) for v in sentence_translations if v]
    else:
        raw_lines = [str(sentence_translations)]

    norm_brackets = config.getboolean(SEC_SETTINGS, 'normalize_bracket_spacing', fallback=True) if config else True
    lines = [html.escape(normalize_bracket_spacing(line.strip()) if norm_brackets else line.strip()) for line in raw_lines]

    eff_mode = _effective_text_mode(text, text_mode) if text else text_mode
    is_single = (eff_mode == 'single')
    if text and ('\n' in text.strip() or '\r' in text.strip()):
        is_single = False

    if is_single:
        valid_lines = [s for s in lines if s]
        if valid_lines and all(s == valid_lines[0] for s in valid_lines):
            valid_lines = [valid_lines[0]]
        return f"<div>{' '.join(valid_lines)}</div>" if valid_lines else ""
    else:
        return "".join(f"<div>{line if line else '&nbsp;'}</div>" for line in lines)


def translate_source_text(text, source_lang, target_lang, text_mode, config, resolved_paths, provider, chunk_callback=None, zid=None, trace_id=None):
    import time
    
    eff_mode = _effective_text_mode(text, text_mode)
    
    split_mode = config.get(SEC_TRANSLATION, 'translation_split_mode', fallback='newline_join')
    chunk_size = config.getint(SEC_TRANSLATION, 'translation_chunk_size', fallback=0)
    max_retries = config.getint(SEC_TRANSLATION, 'translation_max_retries', fallback=3)
    retry_backoff = config.getfloat(SEC_TRANSLATION, 'translation_retry_backoff', fallback=1.0)
    fix_sentence_splits = config.getboolean(SEC_TRANSLATION, 'translation_fix_sentence_splits', fallback=False)
    wrap_max_chars = config.getint(SEC_TRANSLATION, 'translation_wrap_max_chars', fallback=90)
    
    if eff_mode == 'single':
        if len(text) <= wrap_max_chars and '\n' not in text.strip():
            try:
                return {0: translate_text(text, source_lang, target_lang, config, resolved_paths, provider, zid=zid, trace_id=trace_id).strip()}
            except Exception as e:
                logger.error(f"Failed to translate main text: {e}")
                if isinstance(e, TranslationException):
                    raise e
                return {0: f"[Translation Error: {e}]"}
        else:
            sbc = SentenceBoundaryConfig.from_config(config)
            pseudo_lines = split_single_mode_text(text, wrap_max_chars, abbrevs=sbc.abbrev_set, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
            
            apply_source_padding = False
            apply_translated_padding = False
            if sbc.words_before > 0 or sbc.words_after > 0:
                if sbc.context_mode == 'both' or sbc.context_mode == eff_mode:
                    apply_source_padding = True
            
            if sbc.translated_words_before > 0 or sbc.translated_words_after > 0:
                if sbc.context_mode == 'both' or sbc.context_mode == eff_mode:
                    apply_translated_padding = True

            if apply_source_padding and apply_translated_padding:
                logger.warning(
                    "Both anki_context_words_* and anki_translated_context_words_* are active. "
                    "Source padding is bypassed for translation input, but will still apply to the SentenceSource column. "
                    "Translated padding takes precedence for the destination text."
                )

            try:
                # Always translate the unpadded sentences first (for Translate View and TextDestination).
                # chunk_callback is forwarded so progressive workers receive partial streaming
                # updates even when native padding is applied afterwards.
                unpadded_translations = translate_source_text(
                    "\n".join(pseudo_lines), source_lang, target_lang, 'multi',
                    config, resolved_paths, provider, chunk_callback=chunk_callback, zid=zid, trace_id=trace_id
                )

                sorted_int_keys = sorted([i for i in unpadded_translations.keys() if isinstance(i, int) or (isinstance(i, str) and i.isdigit())], key=int)
                full_text_trans = " ".join(
                    unpadded_translations.get(i, "").strip()
                    for i in sorted_int_keys
                    if unpadded_translations.get(i, "")
                )

                if apply_translated_padding:
                    # Native Python Subtitle Padding Algorithm.
                    # We pad the already-translated array natively, avoiding a second network call.
                    translated_array = [unpadded_translations.get(i, "").strip() for i in sorted_int_keys]
                    padded_translated_array = pad_translated_sentences(
                        translated_array,
                        words_before=sbc.translated_words_before,
                        words_after=sbc.translated_words_after,
                        max_words=sbc.translated_max_words
                    )

                    pseudo_translations = {i: padded_translated_array[i] for i in range(len(padded_translated_array))}
                    if full_text_trans:
                        pseudo_translations['FULL_TEXT'] = full_text_trans
                    return pseudo_translations
                    
                elif apply_source_padding:
                    # Legacy fallback: source-based padding requires a second API call
                    padded_lines = pad_sentences(pseudo_lines, text, sbc.words_before, sbc.words_after, max_words=sbc.max_words)
                    
                    # 1. Translate the padded sentences for the TSV (SentenceDestination)
                    # Pass chunk_callback=None so padded sentences do not leak into intermediate UI streaming callbacks
                    pseudo_translations = translate_source_text(
                        "\n".join(padded_lines), source_lang, target_lang, 'multi',
                        config, resolved_paths, provider, chunk_callback=None, zid=zid, trace_id=trace_id
                    )
                    
                    if full_text_trans:
                        pseudo_translations['FULL_TEXT'] = full_text_trans
                        
                    return pseudo_translations
                else:
                    if full_text_trans:
                        unpadded_translations['FULL_TEXT'] = full_text_trans
                    return unpadded_translations
            except TranslationAlignmentError as tae:
                raise TranslationAlignmentError(
                    tae.args[0],
                    partial_dict=tae.partial_dict
                )
                
    raw_lines = text.splitlines()
    if fix_sentence_splits:
        lines = clean_sentence_splits(raw_lines)
    else:
        lines = raw_lines
        
    translations = {idx: "" for idx in range(len(lines))}
    
    if split_mode == 'line_by_line':
        first_failure = None
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            success = False
            last_err = None
            for attempt in range(1, max_retries + 1):
                try:
                    trans_line = translate_text(line, source_lang, target_lang, config, resolved_paths, provider, zid=zid, trace_id=trace_id)
                    _validate_translated_line(line, trans_line, idx, config)
                    translations[idx] = trans_line.strip()
                    success = True
                    break
                except Exception as e:
                    last_err = e
                    if attempt < max_retries:
                        time.sleep(retry_backoff)
            if not success:
                translations[idx] = ""
                if first_failure is None:
                    first_failure = (idx, last_err)
                    
            if chunk_callback:
                chunk_callback(translations)
                
        if first_failure is not None:
            failed_idx, failed_err = first_failure
            raise TranslationAlignmentError(
                f"Line-by-line translation failed at line {failed_idx}: {failed_err}",
                partial_dict=translations
            )
        return translations
        
    chunks = _build_chunks(lines, chunk_size, config)
    
    for chunk_text_list, indices in chunks:
        success = False
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                if split_mode == 'newline_join':
                    joined_text = "\n".join(chunk_text_list)
                    translated_joined = translate_text(joined_text, source_lang, target_lang, config, resolved_paths, provider, zid=zid, trace_id=trace_id)
                    normalized = translated_joined.replace('\r\n', '\n').replace('\r', '\n')
                    translated_chunk_lines = normalized.split('\n')
                    
                    if len(translated_chunk_lines) > 1 and translated_chunk_lines[-1] == "":
                        translated_chunk_lines.pop()
                    if len(translated_chunk_lines) > 1 and translated_chunk_lines[0] == "":
                        translated_chunk_lines.pop(0)
                        
                elif split_mode == 'marker':
                    escaped_chunk_text_list = [line.replace("[[KWSPLIT", "__KWSPLITESC__") for line in chunk_text_list]
                    parts = []
                    markers = []
                    for i, line in enumerate(escaped_chunk_text_list):
                        if i > 0:
                            marker = make_merge_split_marker(i)
                            markers.append(marker)
                            parts.append(marker)
                        parts.append(line)
                    joined_text = " ".join(parts)
                    
                    translated_joined = translate_text(joined_text, source_lang, target_lang, config, resolved_paths, provider, zid=zid, trace_id=trace_id)
                    parts_split = split_merged_text_by_markers(translated_joined, markers)
                    translated_chunk_lines = [part.replace("__KWSPLITESC__", "[[KWSPLIT") for part in parts_split]
                    
                elif split_mode == 'proportional':
                    joined_text = " ".join(chunk_text_list)
                    translated_joined = translate_text(joined_text, source_lang, target_lang, config, resolved_paths, provider, zid=zid, trace_id=trace_id)
                    lengths = [len(line) for line in chunk_text_list]
                    translated_chunk_lines = split_by_proportion(translated_joined, lengths)
                else:
                    raise ValueError(f"Unknown translation_split_mode: {split_mode}")
                    
                if len(translated_chunk_lines) != len(chunk_text_list):
                    raise ValueError(f"Line count mismatch (expected {len(chunk_text_list)}, got {len(translated_chunk_lines)})")
                    
                for i, orig_line in enumerate(chunk_text_list):
                    _validate_translated_line(orig_line, translated_chunk_lines[i], indices[i], config)
                    
                for list_idx, target_idx in enumerate(indices):
                    translations[target_idx] = translated_chunk_lines[list_idx].strip()
                success = True
                break
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    time.sleep(retry_backoff)
                    
        if not success:
            first_rescue_failure = None
            for list_idx, target_idx in enumerate(indices):
                original_line = chunk_text_list[list_idx]
                try:
                    rescued_line = translate_text(original_line, source_lang, target_lang, config, resolved_paths, provider, zid=zid, trace_id=trace_id)
                    _validate_translated_line(original_line, rescued_line, target_idx, config)
                    translations[target_idx] = rescued_line.strip()
                except Exception as rescue_err:
                    translations[target_idx] = ""
                    if first_rescue_failure is None:
                        first_rescue_failure = (target_idx, rescue_err)
            if first_rescue_failure is not None:
                failed_idx, failed_err = first_rescue_failure
                raise TranslationAlignmentError(
                    f"Rescue translation failed for line {failed_idx}: {failed_err}",
                    partial_dict=translations
                )
                
        if chunk_callback:
            chunk_callback(translations)
            
    return translations

class IntelliFillerError(Exception):
    def __init__(self, message: str, envelope: Optional[dict] = None):
        super().__init__(message)
        self.envelope = envelope or {}

def run_headless_intellifiller(tsv_path, prompt_name, config, resolved_paths, selected_rows=None, reprocess=False, zid=None, trace_id=None):
    if zid is None:
        m = re.match(r"^(\d{14})", tsv_path.name)
        zid = m.group(1) if m else (tsv_path.name.split('-')[0] if '-' in tsv_path.name else "unknown")
    
    # Determine provider and model for performance tracing
    model_name = "qwen2.5:3b"
    base_url = None
    if config and config.has_section("intellifiller"):
        model_name = config.get("intellifiller", "model", fallback="qwen2.5:3b")
        base_url = config.get("intellifiller", "base_url", fallback=None)
    
    provider_id = "local_ollama" if (base_url and ("127.0.0.1" in base_url or "localhost" in base_url)) else ("cloud_openai" if not base_url or "api.openai.com" in base_url else "custom_endpoint")
    extra_trace = {
        "provider": provider_id,
        "model": model_name
    }
    with TraceTimer("intellifiller_enrichment", zid, config, resolved_paths, extra=extra_trace):
        return _run_headless_intellifiller_impl(tsv_path, prompt_name, config, resolved_paths, selected_rows, reprocess, zid=zid, trace_id=trace_id)

def _run_headless_intellifiller_impl(tsv_path, prompt_name, config, resolved_paths, selected_rows=None, reprocess=False, zid=None, trace_id=None):
    lock_target = Path(str(tsv_path) + ".intellifiller")
    with file_lock(lock_target):
        # 1. Read [intellifiller] and timeout sections from config
        model = None
        base_url = None
        api_key = None
        temperature = None
        prompt_template = None
        timeout = config.getint(SEC_TIMEOUTS, 'intellifiller_timeout', fallback=120) if config and hasattr(config, 'getint') else 120
        if config and config.has_section("intellifiller"):
            model = config.get("intellifiller", "model", fallback=None)
            base_url = config.get("intellifiller", "base_url", fallback=None)
            api_key = config.get("intellifiller", "api_key", fallback=None)
            temperature = config.get("intellifiller", "temperature", fallback=None)
            prompt_template = config.get("intellifiller", "prompt_template", fallback=None)

        # 2. Try HTTP microservice if configured
        intellifiller_url = None
        if config:
            if config.has_section(SEC_SERVICES):
                intellifiller_url = config.get(SEC_SERVICES, 'intellifiller_server_url', fallback=None)
            elif config.has_section('services'):
                intellifiller_url = config.get('services', 'intellifiller_server_url', fallback=None)

        if intellifiller_url:
            try:
                comments, headers, data_rows = load_tsv_rows(tsv_path)
                lang = "de"
                for c in comments:
                    if "language=" in c:
                        lang = c.split("language=")[-1].strip().split()[0]
                        break

                mapping = None
                try:
                    mapping = load_anki_mapping(resolved_paths.get('anki_mapping_file'))
                except Exception:
                    pass

                role_fields = get_role_fields(mapping, headers) if mapping else {}
                target_field = role_fields.get('word_translation', 'WordDestination')

                target_indices = []
                for i, row in enumerate(data_rows):
                    if selected_rows is not None and i not in selected_rows:
                        continue
                    if not reprocess:
                        if target_field in headers:
                            idx = headers.index(target_field)
                            if idx < len(row) and row[idx].strip():
                                continue
                    target_indices.append(i)

                if not target_indices:
                    logger.info("No rows to enrich in TSV (all filled or excluded).")
                    return True

                batch_rows = []
                for idx in target_indices:
                    row_dict = {"row_id": idx}
                    for c_idx, h in enumerate(headers):
                        if c_idx < len(data_rows[idx]):
                            row_dict[h] = data_rows[idx][c_idx]
                    batch_rows.append(row_dict)

                timeout = config.getint(SEC_TIMEOUTS, 'intellifiller_timeout', fallback=120) if config else 120
                temp_val = float(temperature) if (temperature is not None and str(temperature).strip()) else None
                resp = query_intellifiller_server(
                    rows=batch_rows,
                    prompt=prompt_name,
                    language=lang,
                    server_url=intellifiller_url,
                    zid=zid,
                    trace_id=trace_id,
                    timeout=float(timeout),
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    temperature=temp_val,
                    prompt_template=prompt_template
                )

                if resp is not None:
                    if resp.get("status") == "error" or ("code" in resp and resp.get("status") != "success"):
                        raise IntelliFillerError(resp.get("message", "IntelliFiller server returned error"), envelope=resp)

                    if resp.get("status") == "success":
                        for item in resp.get("enriched_rows", []):
                            r_idx = item.get("row_id")
                            if r_idx is not None and 0 <= r_idx < len(data_rows):
                                for k, v in item.items():
                                    if k in ("row_id", "status", "zid", "trace_id"):
                                        continue
                                    if k not in headers:
                                        headers.append(k)
                                        for r in data_rows:
                                            r.append("")
                                    col_idx = headers.index(k)
                                    while len(data_rows[r_idx]) <= col_idx:
                                        data_rows[r_idx].append("")
                                    data_rows[r_idx][col_idx] = str(v)

                        save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                        logger.info("Headless IntelliFiller (HTTP microservice) finished successfully.")
                        return True
            except IntelliFillerError:
                raise
            except Exception as e:
                logger.warning(f"IntelliFiller HTTP microservice failed, falling back to CLI subprocess: {e}")

        python_exe = resolved_paths['kardenwort_python']
        headless_script = resolved_paths['intellifiller_headless']

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_tsv_path = Path(temp_dir) / tsv_path.name
            if tsv_path.exists():
                shutil.copy2(tsv_path, temp_tsv_path)

            cmd = [
                str(python_exe),
                str(headless_script),
                "--tsv", str(temp_tsv_path),
                "--prompt", prompt_name,
            ]
            if model:
                cmd.extend(["--model", str(model)])
            if base_url:
                cmd.extend(["--base-url", str(base_url)])
            if api_key:
                cmd.extend(["--api-key", str(api_key)])
            if temperature is not None and str(temperature).strip():
                cmd.extend(["--temperature", str(temperature).strip()])
            if prompt_template:
                cmd.extend(["--prompt-template", str(prompt_template)])
            if timeout:
                cmd.extend(["--timeout", str(timeout)])

            if reprocess:
                cmd.append("--reprocess")
            if zid:
                cmd.extend(["--zid", str(zid)])
            if trace_id:
                cmd.extend(["--trace-id", str(trace_id)])

            if selected_rows:
                rows_str = ",".join(str(r) for r in selected_rows)
                cmd.extend(["--selected-rows", rows_str])

            try:
                mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
                headers = []
                with open(temp_tsv_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.startswith('#'):
                            headers = [h.strip() for h in line.split('\t')]
                            break
                role_fields = get_role_fields(mapping, headers)
                target_field = role_fields.get('word_translation', 'WordDestination')
                if target_field:
                    cmd.extend(["--target-field", target_field])
            except Exception:
                pass

            timeout = config.getint(SEC_TIMEOUTS, 'intellifiller_timeout', fallback=120)
            logger.info(f"Running headless IntelliFiller command: {' '.join(cmd)}")

            res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
            if res.returncode == 0:
                if temp_tsv_path.exists():
                    comments, headers, data_rows = load_tsv_rows(temp_tsv_path)
                    save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                logger.info("Headless IntelliFiller finished successfully.")
                return True
            else:
                parsed_err = None
                combined_output = (res.stderr or "") + "\n" + (res.stdout or "")
                for line in reversed(combined_output.strip().splitlines()):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict) and data.get("status") == "error":
                                parsed_err = data
                                break
                        except Exception:
                            pass

                if not parsed_err:
                    err_msg = res.stderr.strip() if res.stderr else f"Process exited with code {res.returncode}"
                    parsed_err = {
                        "status": "error",
                        "zid": str(zid) if zid else "",
                        "trace_id": str(trace_id) if trace_id else "",
                        "code": "ERR_INTELLIFILLER_CRASH",
                        "message": err_msg,
                        "row_id": None,
                        "retryable": False,
                        "details": {"exit_code": res.returncode}
                    }

                logger.error(f"Headless IntelliFiller failed with exit code {res.returncode}: [{parsed_err.get('code')}] {parsed_err.get('message')}")
                raise IntelliFillerError(parsed_err.get("message", "Headless IntelliFiller failed"), envelope=parsed_err)

def run_headless_intellifiller_async(tsv_path, prompt_name, config, resolved_paths, selected_rows=None, zid=None, trace_id=None):
    python_exe = (resolved_paths.get('kardenwort_python') if resolved_paths else None) or sys.executable
    desk_script = Path(__file__).resolve()
    
    if selected_rows is None:
        try:
            _, _, data_rows = load_tsv_rows(tsv_path)
            selected_rows = list(range(len(data_rows)))
        except Exception:
            selected_rows = []
            
    if not selected_rows:
        return
        
    rows_str = ",".join(str(r) for r in selected_rows)
    
    cmd = [
        str(python_exe),
        str(desk_script),
        "batch-worker",
        "--tsv", str(tsv_path),
        "--prompt", prompt_name,
        "--rows", rows_str
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
        
    logger.info(f"Kicking off background batch-worker: {' '.join(cmd)}")
    if sys.platform == 'win32':
        creationflags = 0x08000000 | 0x00000200
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True
        )
    else:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )

def run_progressive_worker_async(tsv_path, language, target_lang, prompt_name, lemmas_provider, word_translations_empty, skip_intellifiller=False, text_mode='single', zid=None, trace_id=None, resolved_paths=None):
    python_exe = (resolved_paths.get('kardenwort_python') if resolved_paths else None) or sys.executable
    desk_script = Path(__file__).resolve()
    if not zid:
        import re
        m = re.match(r"^(\d{14})", Path(tsv_path).name)
        if m:
            zid = m.group(1)
    if not trace_id and zid:
        trace_id = f"{zid}:progressive:worker"

    cmd = [
        str(python_exe),
        str(desk_script),
        "progressive-worker",
        "--tsv", str(tsv_path),
        "--language", language,
        "--target-lang", target_lang,
        "--prompt", prompt_name,
        "--provider", lemmas_provider,
        "--word-empty", str(word_translations_empty),
        "--text-mode", text_mode
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
    if skip_intellifiller:
        cmd.append("--skip-intellifiller")
    logger.info(f"Kicking off background progressive-worker: {' '.join(cmd)}")
    if sys.platform == 'win32':
        creationflags = 0x08000000 | 0x00000200
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=creationflags, close_fds=True
        )
    else:
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True
        )

def run_detached_import(favorites_tsv_path, config, resolved_paths, zid, trace_id=None):
    python_exe = resolved_paths['kardenwort_python']
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    runner_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort_runner.py"
    
    if not trace_id:
        trace_id = f"{zid}:export:anki"
    
    cmd = [
        str(python_exe),
        str(runner_script),
        "--import-only",
        "--tsv", str(favorites_tsv_path),
        "--zid", str(zid),
        "--trace-id", str(trace_id),
        "--play-sound-on-completion"
    ]
    
    log_file_path = favorites_tsv_path.parent / f"{zid}-import.log"
    logger.info(f"Launching detached import: {' '.join(cmd)}")
    
    # Detached background import always runs without showing a console window
    show_window = False
    creationflags = 0
    if sys.platform == 'win32':
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        # DETACHED_PROCESS = 0x00000008
        # CREATE_NO_WINDOW = 0x08000000
        if show_window:
            creationflags = 0x00000200 | 0x00000008
        else:
            creationflags = 0x00000200 | 0x08000000

    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        if sys.platform == 'win32':
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True
            )
        else:
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True
            )
        
    return p.pid, str(log_file_path)

def run_synchronous_import(favorites_tsv_path, config, resolved_paths, zid=None, trace_id=None):
    python_exe = resolved_paths['kardenwort_python']
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    runner_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort_runner.py"
    
    cmd = [
        str(python_exe),
        str(runner_script),
        "--import-only",
        "--tsv", str(favorites_tsv_path),
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
    elif zid:
        cmd.extend(["--trace-id", f"{zid}:export:anki"])
    
    logger.info(f"Running synchronous import: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', check=True)
        return True, res.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def prepare_lookup_tsv(text, language, target_lang, config, resolved_paths, zid, *, ttl_seconds, cache_key, text_mode='single', will_split=False):
    with TraceTimer("lemmatization", zid, config, resolved_paths):
        return _prepare_lookup_tsv_impl(text, language, target_lang, config, resolved_paths, zid, ttl_seconds=ttl_seconds, cache_key=cache_key, text_mode=text_mode, will_split=will_split)

def _prepare_lookup_tsv_impl(text, language, target_lang, config, resolved_paths, zid, *, ttl_seconds, cache_key, text_mode='single', will_split=False):
    eff_mode = _effective_text_mode(text, text_mode)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    working_tsv_path = results_dir / cache_key
    
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
    
    import time
    import re
    
    if not is_sqlite:
        results_dir.mkdir(parents=True, exist_ok=True)
        # Clean up stale .updates directories (> 5 minutes old) to prevent clutter
        try:
            import shutil
            now = time.time()
            for d in results_dir.rglob("*.updates"):
                if d.is_dir() and (now - d.stat().st_mtime) > 300:
                    try:
                        shutil.rmtree(d)
                    except OSError:
                        pass
        except Exception:
            pass
            
        if working_tsv_path.exists():
            if ttl_seconds <= 0 or (time.time() - working_tsv_path.stat().st_mtime) <= ttl_seconds:
                return working_tsv_path
                
        if ttl_seconds > 0:
            m = re.match(r'^\d{14}-(.+)', cache_key)
            if m:
                slug_part = m.group(1)
                for cached_file in results_dir.glob(f"*-{slug_part}"):
                    if cached_file.is_file():
                        if (time.time() - cached_file.stat().st_mtime) <= ttl_seconds:
                            return cached_file
                
        # Clean up any leftover .updates from previous sessions to avoid polling stale data
        updates_dir = working_tsv_path.parent / f"{working_tsv_path.stem}.updates"
        if updates_dir.exists():
            try:
                import shutil
                shutil.rmtree(updates_dir)
            except OSError:
                pass
    else:
        # SQLite caching check
        if ttl_seconds > 0:
            m = re.match(r'^\d{14}-(.*?)(?:\.[a-z]{2})?\.tsv$', cache_key, re.IGNORECASE)
            slug_part = m.group(1) if m else generate_slug(text)
            cached_bundle = storage_adapter.get_cached_session(
                slug_part, language, ttl_seconds,
                source_raw_text=text,
                target_language=target_lang,
                text_mode=eff_mode,
                zid=zid,
            )
            if cached_bundle and cached_bundle.get("session"):
                cached_zid = cached_bundle["session"].get("zid")
                return results_dir / f"{cached_zid}-{slug_part}.{language}.tsv"
            
    stem = cache_key
    if stem.endswith('.tsv'):
        stem = stem[:-4]
    source_text_path = results_dir / f"{stem}.txt"
    
    wrap_max_chars = config.getint(SEC_TRANSLATION, 'translation_wrap_max_chars', fallback=90)
    
    save_source_text = config.getboolean(SEC_SETTINGS, 'save_source_text', fallback=True)
    if not is_sqlite and save_source_text:
        if eff_mode == 'single':
            source_text_path.write_text(text, encoding='utf-8')
        elif not source_text_path.exists():
            source_text_path.write_text(text, encoding='utf-8')
        
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    fields = list(mapping['fields'].keys())
    field_mapping = build_field_mapping(mapping, 'word')
    
    lemma_index_rel = config.get(SEC_LANGUAGES, f'{language}_lemma_index')
    lemma_override_rel = config.get(SEC_LANGUAGES, f'{language}_lemma_override')
    
    lemma_index_file = kardenwort_workspace / lemma_index_rel
    lemma_override_file = kardenwort_workspace / lemma_override_rel
    
    python_exe = resolved_paths['kardenwort_python']
    kardenwort_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort.py"
    
    temp_file_path = None
    temp_dir_obj = None
    
    try:
        sbc = SentenceBoundaryConfig.from_config(config)
        token_cfg = RuntimeTokenConfig.from_config(config)
        exec_ctx = ExecutionContext.from_config(eff_mode, config, token_cfg, will_split=will_split)
        workflow_res = ModeDispatcher.dispatch(exec_ctx)
        dedup_scope = workflow_res.dedup_scope
        combine_source_words = workflow_res.combine_source_words

        if is_sqlite:
            temp_dir_obj = tempfile.TemporaryDirectory()
            temp_dir_path = Path(temp_dir_obj.name)
            if eff_mode == 'single':
                split_lines = split_single_mode_text(text, wrap_max_chars, abbrevs=sbc.abbrev_set, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
                temp_content = "\n".join(split_lines)
            else:
                temp_content = text
            temp_src_file = temp_dir_path / f"{stem}.txt"
            temp_src_file.write_text(temp_content, encoding='utf-8')
            text_file_to_pass = temp_src_file
            out_file_to_pass = temp_dir_path / cache_key
        else:
            text_file_to_pass = source_text_path
            out_file_to_pass = working_tsv_path
            use_temp = (eff_mode == 'single') or (not save_source_text)
            if use_temp:
                if eff_mode == 'single':
                    split_lines = split_single_mode_text(text, wrap_max_chars, abbrevs=sbc.abbrev_set, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
                    temp_content = "\n".join(split_lines)
                else:
                    temp_content = text
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', encoding='utf-8', delete=False)
                temp_file_path = Path(temp_file.name)
                try:
                    temp_file.write(temp_content)
                finally:
                    temp_file.close()
                text_file_to_pass = temp_file_path
            
        use_simplemma_correction = config.getboolean(SEC_SETTINGS, 'use_simplemma_correction', fallback=False)
        simplemma_after_spacy = config.getboolean(SEC_SETTINGS, 'simplemma_after_spacy', fallback=False)
        simplemma_pos_aware = config.getboolean(SEC_SETTINGS, 'simplemma_pos_aware', fallback=False)
        simplemma_smart_fallback = config.getboolean(SEC_SETTINGS, 'simplemma_smart_fallback', fallback=False)

        cmd = [
            str(python_exe),
            str(kardenwort_script),
            "--type", "word",
            "--language", language,
            "--deduplication-scope", dedup_scope,
            "--lemma-index-file", str(lemma_index_file),
            "--lemma-override-file", str(lemma_override_file),
            "--sentence-context-size", "0",
            "--anki-csv-header", json.dumps(fields),
            "--anki-field-mapping", json.dumps(field_mapping),
            "--output-file", str(out_file_to_pass),
            "--text1-file", str(text_file_to_pass),
            "--tts-destination-lang", target_lang
        ]
        
        if use_simplemma_correction:
            cmd.append("--use-simplemma-correction")
        if simplemma_after_spacy:
            cmd.append("--simplemma-after-spacy")
        if simplemma_pos_aware:
            cmd.append("--simplemma-pos-aware")
        if simplemma_smart_fallback:
            cmd.append("--simplemma-smart-fallback")
            
        force_proper_noun_capitalization = config.getboolean(SEC_SETTINGS, 'force_proper_noun_capitalization', fallback=False)
        if force_proper_noun_capitalization:
            cmd.append("--force-proper-noun-capitalization")
            
        prefer_shortest_form = config.getboolean(SEC_SETTINGS, 'prefer_shortest_form', fallback=False)
        if prefer_shortest_form:
            cmd.append("--prefer-shortest-form")
            
        de_force_noun_capitalization = config.getboolean(SEC_SETTINGS, 'de_force_noun_capitalization', fallback=False)
        if de_force_noun_capitalization:
            cmd.append("--de-force-noun-capitalization")
            
        preserve_composite_tokens = config.getboolean(SEC_SETTINGS, 'preserve_composite_tokens', fallback=False)
        if preserve_composite_tokens:
            cmd.append("--preserve-composite-tokens")
            
        strip_garbage_characters = config.get(SEC_SETTINGS, 'strip_garbage_characters', fallback=None)
        if strip_garbage_characters is not None:
            cmd.extend(["--strip-garbage-characters", strip_garbage_characters])
        
        if language == "de":
            de_dictionary_file = kw_config.get(SEC_LANGUAGE_RESOURCES, 'dictionary_file_de', fallback='german.dic')
            de_dict_path = kardenwort_workspace / "data" / de_dictionary_file
            cmd.extend([
                "--de-dictionary-file", str(de_dict_path),
            ])
            
            de_fix_genitive = config.getboolean(SEC_SETTINGS, 'de_fix_genitive', fallback=True)
            if de_fix_genitive:
                cmd.append("--de-fix-genitive")
                
            cmd.extend(DeGCSConfig.from_config(config).to_cli_args())
                
        # token_cfg, exec_ctx, and combine_source_words are resolved via ModeDispatcher above

        if combine_source_words:
            cmd.append("--combine-source-words")
            cmd.extend(["--combine-source-words-order", token_cfg.combine_order])
            cmd.extend(["--apostrophe-chars", token_cfg.apostrophe_chars])

            
        if token_cfg.token_mappings_enabled:
            cmd.append("--token-mappings-enabled")
        else:
            cmd.append("--disable-token-mappings")

            
        if token_cfg.lemmatize_mapped_tokens:
            cmd.append("--lemmatize-mapped-tokens")
        desk_classification_enabled = config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=True) if config.has_section(SEC_CLASSIFICATION) else True
        if not desk_classification_enabled:
            cmd.append("--disable-classification")

        # Forward SpaCy HTTP Microservice URL if configured in [services]
        spacy_server_url = ""
        if config and hasattr(config, "has_section") and config.has_section(SEC_SERVICES) and config.has_option(SEC_SERVICES, 'spacy_server_url'):
            spacy_server_url = config.get(SEC_SERVICES, 'spacy_server_url', fallback='').strip()
        elif kw_config and hasattr(kw_config, "has_section") and kw_config.has_section(SEC_SERVICES) and kw_config.has_option(SEC_SERVICES, 'spacy_server_url'):
            spacy_server_url = kw_config.get(SEC_SERVICES, 'spacy_server_url', fallback='').strip()

        if spacy_server_url:
            cmd.extend(["--spacy-server-url", spacy_server_url])
            
        kardenwort_timeout = config.getint(SEC_TIMEOUTS, 'kardenwort_timeout', fallback=120)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        logger.info(f"Running kardenwort.py: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, timeout=kardenwort_timeout, env=env, capture_output=True, text=True, encoding='utf-8')
        except subprocess.TimeoutExpired as e:
            print_structured_error("TIMEOUT", f"kardenwort.py timed out after {kardenwort_timeout} seconds")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print_structured_error("KARDENWORT_FAILED", f"kardenwort.py failed with exit code {e.returncode}", {"stderr": e.stderr})
            sys.exit(1)

        apply_padding = False
        if sbc.words_before > 0 or sbc.words_after > 0:
            if sbc.context_mode == 'both':
                apply_padding = True
            elif sbc.context_mode == eff_mode:
                apply_padding = True

        if out_file_to_pass.exists():
            try:
                comments, headers, data_rows = load_tsv_rows(out_file_to_pass)
                role_fields = get_role_fields(mapping, headers)
                col_src_idx = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
                col_src_sent = headers.index(role_fields.get('sentence_source', 'SentenceSource')) if role_fields.get('sentence_source', 'SentenceSource') in headers else -1
                if apply_padding and col_src_idx != -1 and col_src_sent != -1:
                    if eff_mode == 'single':
                        sentences = split_single_mode_text(text, wrap_max_chars, abbrevs=sbc.abbrev_set, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
                    else:
                        sentences = [ln.strip() for ln in text.splitlines()]
                    padded_sentences = pad_sentences(sentences, text, sbc.words_before, sbc.words_after, max_words=sbc.max_words)
                    for row in data_rows:
                        if len(row) > col_src_idx and len(row) > col_src_sent:
                            try:
                                idx = int(row[col_src_idx]) - 1
                                if 0 <= idx < len(padded_sentences):
                                    row[col_src_sent] = padded_sentences[idx]
                            except ValueError:
                                pass
                if is_sqlite:
                    slug_match = re.match(r'^\d{14}-(.*?)(?:\.[a-z]{2})?\.tsv$', cache_key, re.IGNORECASE)
                    slug_val = slug_match.group(1) if slug_match else generate_slug(text)
                    storage_adapter.save_session(
                        session_zid=zid,
                        slug=slug_val,
                        source_language=language,
                        target_language=target_lang,
                        text_mode=text_mode,
                        source_raw_text=text,
                        comments=comments,
                        headers=headers,
                        data_rows=data_rows,
                        working_tsv_path=None,
                        zid=zid,
                    )
                else:
                    if apply_padding:
                        save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
            except Exception as e:
                logger.error(f"Failed to process prepared TSV tokens: {e}")
    finally:
        if temp_file_path is not None:
            try:
                os.remove(temp_file_path)
            except OSError:
                pass
        if temp_dir_obj is not None:
            try:
                temp_dir_obj.cleanup()
            except Exception:
                pass

    return working_tsv_path

def is_complex_inflected_form(form, apostrophe_chars):
    if any(c in form for c in apostrophe_chars) or '-' in form or ' ' in form:
        return True
    if len(form) >= 2 and form.isupper():
        return True
    return any(not c.isalnum() for c in form)

def sort_inflected_forms(forms, apostrophe_chars, order='contractions_first', prefer_lowercase=True):
    unique_forms_dict = {}
    for f in forms:
        f_clean = f.strip()
        if not f_clean:
            continue
            
        if prefer_lowercase:
            f_lower = f_clean.lower()
            if f_lower not in unique_forms_dict:
                unique_forms_dict[f_lower] = f_clean
            elif f_clean == f_lower:
                # If we have an uppercase version, override it with the lowercase version!
                unique_forms_dict[f_lower] = f_clean
        else:
            if f_clean not in unique_forms_dict:
                unique_forms_dict[f_clean] = f_clean

    unique_forms = list(unique_forms_dict.values())
    
    if order == 'contractions_first':
        unique_forms.sort(key=lambda f: (not is_complex_inflected_form(f, apostrophe_chars), -len(f), f.lower()))
    elif order == 'alphabetical':
        unique_forms.sort(key=lambda f: f.lower())
    return unique_forms


_TOKEN_MAPPINGS_CACHE: Dict[Tuple[str, str], Dict[str, List[str]]] = {}


def get_desk_token_mappings(resolved_paths=None, language=None, config=None) -> Dict[str, List[str]]:
    """
    Loads and caches token mappings dictionary for the specified language.
    Mapping keys (lowercased, whitespace stripped, normalized apostrophes)
    map to list of target tokens (e.g. "we're" -> ["we", "are"]).
    """
    if not language:
        return {}

    lang_key = str(language).strip().lower()
    ws_path = None
    if resolved_paths and isinstance(resolved_paths, dict) and 'kardenwort_workspace' in resolved_paths:
        p = resolved_paths['kardenwort_workspace']
        ws_path = Path(p) if p else None
    elif config and hasattr(config, 'get') and config.has_section(SEC_ENVIRONMENT):
        env_ws = config.get(SEC_ENVIRONMENT, 'kardenwort_workspace', fallback=None)
        if env_ws:
            ws_path = Path(env_ws)

    cache_key = (str(ws_path.resolve()) if ws_path and ws_path.exists() else "none", lang_key)
    if cache_key in _TOKEN_MAPPINGS_CACHE:
        return _TOKEN_MAPPINGS_CACHE[cache_key]

    mappings: Dict[str, List[str]] = {}
    mapping_files: List[Path] = []

    if ws_path and ws_path.exists():
        kw_config_path = ws_path / "config.ini"
        if kw_config_path.exists():
            try:
                kw_cfg = configparser.ConfigParser(allow_no_value=True, interpolation=None)
                kw_cfg.read(kw_config_path, encoding='utf-8')
                if kw_cfg.has_section('token_mappings') and kw_cfg.has_option('token_mappings', lang_key):
                    raw_entries = kw_cfg.get('token_mappings', lang_key)
                    for entry in raw_entries.split(','):
                        entry = entry.strip()
                        if entry:
                            fpath = ws_path / entry if not os.path.isabs(entry) else Path(entry)
                            if fpath.exists():
                                mapping_files.append(fpath)
            except Exception as e:
                logger.warning(f"Failed to read token_mappings from {kw_config_path}: {e}")

        # Fallback to standard data/{lang} path if no config entries resolved
        if not mapping_files:
            data_dir = ws_path / "data" / lang_key
            if data_dir.exists():
                for candidate in data_dir.glob("lemma_*_*.tsv"):
                    mapping_files.append(candidate)

    for mf in mapping_files:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter="\t")
                for row in reader:
                    if not row or row[0].startswith('#') or len(row) < 2:
                        continue
                    src = row[0].strip()
                    norm_src = src.replace('’', "'").replace('‘', "'").replace('`', "'").replace('´', "'").replace('ʼ', "'")
                    norm_src = re.sub(r'\s+', '', norm_src).lower()
                    targets = [t.strip() for t in row[1:] if t.strip()]
                    if norm_src and targets:
                        mappings[norm_src] = targets
        except Exception as e:
            logger.warning(f"Error loading token mapping file {mf}: {e}")

    _TOKEN_MAPPINGS_CACHE[cache_key] = mappings
    return mappings


def deduplicate_rows(data_rows, col_word_source, col_pos, col_inflected, config, window_text=None, language=None, resolved_paths=None):
    deduped_rows = []
    seen_words = {}

    token_config = RuntimeTokenConfig.from_config(config)
    filter_by_window = token_config.filter_by_window
    combine_source_words = token_config.combine_source_words
    token_mappings_enabled = token_config.token_mappings_enabled
    order_cfg = token_config.combine_order
    apo_cfg_str = token_config.apostrophe_chars
    apo_cfg = tuple(c.strip() for c in apo_cfg_str.split(',') if c.strip())
    
    prefer_lowercase_cfg = token_config.prefer_lowercase

    is_filtering_window = False
    window_words_exact = set()
    if window_text and filter_by_window:
        is_filtering_window = True
        apo_pattern = "".join(re.escape(c) for c in apo_cfg)
        word_pattern = r"[\w" + apo_pattern + r"]+"
        raw_words = re.findall(word_pattern, window_text)
        window_words_exact = set(w.strip() for w in raw_words if w.strip())
        contraction_pattern = r"(?:n[" + apo_pattern + r"]t|[" + apo_pattern + r"](?:s|ve|ll|d|re|m)?)$"
        for w in list(window_words_exact):
            stem = re.sub(contraction_pattern, "", w, flags=re.IGNORECASE)
            if stem and stem != w:
                window_words_exact.add(stem)
            for part in re.split(r"[" + apo_pattern + r"]+", w):
                if part:
                    window_words_exact.add(part)

        if token_mappings_enabled and language:
            token_mappings = get_desk_token_mappings(resolved_paths, language, config)
            if token_mappings:
                for w in list(window_words_exact):
                    norm_w = w.replace('’', "'").replace('‘', "'").replace('`', "'").replace('´', "'").replace('ʼ', "'")
                    norm_w = re.sub(r'\s+', '', norm_w).lower()
                    if norm_w in token_mappings:
                        for tgt in token_mappings[norm_w]:
                            window_words_exact.add(tgt)
                norm_window = window_text.replace('’', "'").replace('‘', "'").replace('`', "'").replace('´', "'").replace('ʼ', "'").lower()
                norm_window_stripped = re.sub(r'\s+', '', norm_window)
                for norm_key, targets in token_mappings.items():
                    if len(norm_key) > 1 and norm_key in norm_window_stripped:
                        for tgt in targets:
                            window_words_exact.add(tgt)

        window_words_lower = set(w.lower() for w in window_words_exact)

        def _is_in_window(p_clean):
            if p_clean in window_words_exact or p_clean.lower() in window_words_lower:
                return True
            p_stem = re.sub(contraction_pattern, "", p_clean, flags=re.IGNORECASE)
            if p_stem in window_words_exact or p_stem.lower() in window_words_lower:
                return True
            p_parts = [w for w in re.findall(word_pattern, p_clean) if w.strip()]
            if not p_parts:
                return False
            for part in p_parts:
                part_stem = re.sub(contraction_pattern, "", part, flags=re.IGNORECASE)
                if (part not in window_words_exact and part.lower() not in window_words_lower and
                    part_stem not in window_words_exact and part_stem.lower() not in window_words_lower):
                    return False
            return True

    POSSESSIVE_DISCARD_TOKENS = {"'s", "’s", "‘s", "´s", "`s", "ʼs", "'", "’"}
    for row in data_rows:
        if len(row) > col_word_source and col_word_source != -1:
            if re.match(r'^\d{14}-', row[col_word_source]):
                row = list(row)
                row[col_word_source] = re.sub(r'^\d{14}-', '', row[col_word_source])
            if row[col_word_source].strip().startswith('-') or row[col_word_source].strip().endswith('-'):
                row = list(row)
                row[col_word_source] = row[col_word_source].strip().strip('-')
            w = row[col_word_source].strip().lower()
            if w in POSSESSIVE_DISCARD_TOKENS:
                continue
            if w == "s":
                inf_val = row[col_inflected].strip().lower() if col_inflected != -1 and len(row) > col_inflected else ""
                quot_val = row[0].strip().lower() if len(row) > 0 else ""
                if not inf_val or inf_val in POSSESSIVE_DISCARD_TOKENS or inf_val == "s" or quot_val in POSSESSIVE_DISCARD_TOKENS:
                    continue
            pos = row[col_pos].strip().lower() if col_pos != -1 and len(row) > col_pos else ""
            if not combine_source_words:
                inf_form_lower = row[col_inflected].strip().lower() if col_inflected != -1 and len(row) > col_inflected else ""
                key = (w, pos, inf_form_lower)
            else:
                key = (w, pos)
            if w and key in seen_words:
                existing_row_idx = seen_words[key]
                if col_inflected != -1 and len(row) > col_inflected:
                    new_inflected = row[col_inflected].strip()
                    if new_inflected:
                        while len(deduped_rows[existing_row_idx]) <= col_inflected:
                            deduped_rows[existing_row_idx].append("")
                        existing_inflected = deduped_rows[existing_row_idx][col_inflected].strip()
                        existing_parts = [p.strip() for p in existing_inflected.split(',') if p.strip()]
                        new_parts = [p.strip() for p in new_inflected.split(',') if p.strip()]
                        for p in new_parts:
                            if p and p not in existing_parts:
                                existing_parts.append(p)
                        if is_filtering_window:
                            final_parts = []
                            for p in existing_parts:
                                p_clean = p.strip()
                                if _is_in_window(p_clean):
                                    final_parts.append(p)
                            existing_parts = final_parts
                        deduped_rows[existing_row_idx][col_inflected] = ", ".join(sort_inflected_forms(existing_parts, apo_cfg, order_cfg, prefer_lowercase_cfg))
                continue
            if w:
                seen_words[key] = len(deduped_rows)
                if col_inflected != -1 and len(row) > col_inflected:
                    cur_inf = row[col_inflected].strip()
                    if cur_inf:
                        parts = [p.strip() for p in cur_inf.split(',') if p.strip()]
                        final_parts = []
                        if is_filtering_window:
                            for p in parts:
                                p_clean = p.strip()
                                if _is_in_window(p_clean):
                                    final_parts.append(p)
                        else:
                            final_parts = parts
                        row = list(row)
                        row[col_inflected] = ", ".join(sort_inflected_forms(final_parts, apo_cfg, order_cfg, prefer_lowercase_cfg))
                        row = tuple(row)
        deduped_rows.append(list(row))
    return deduped_rows


def resolve_row_inflected_form(row, col_inflected, col_inflected2=-1, col_quotation=-1, col_lemma=-1):
    """
    Evaluate inflected form in order:
      1. WordSourceInflectedForm (col_inflected)
      2. WordSourceInflectedForm2 (col_inflected2)
      3. Quotation (col_quotation)
      4. WordSource / Lemma (col_lemma)
    """
    if col_inflected != -1 and len(row) > col_inflected and row[col_inflected].strip():
        return row[col_inflected].strip()
    if col_inflected2 != -1 and len(row) > col_inflected2 and row[col_inflected2].strip():
        return row[col_inflected2].strip()
    if col_quotation != -1 and len(row) > col_quotation and row[col_quotation].strip():
        return row[col_quotation].strip()
    if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
        return row[col_lemma].strip()
    return ""


SPLIT_GAP_LIMIT = 60


def resolve_anchored_positions(inflected_words, source_word_cleans, gap_limit):
    """
    Finds the set of non-overlapping minimum-span ordered tuples of source-word positions.
    inflected_words: list of lowered, cleaned inflected form words.
    source_word_cleans: list of lowered, cleaned source words.
    gap_limit: int, maximum allowed distance between consecutive positions.
    """
    if len(inflected_words) < 2:
        return set(), False

    # Collect occurrence lists
    occs = []
    for word in inflected_words:
        occs.append([idx for idx, w in enumerate(source_word_cleans) if w == word])

    # If any of the words are not in the source text, no tuple can be formed
    if any(not lst for lst in occs):
        return set(), False

    valid_tuples = []
    k = len(inflected_words)

    def backtrack(step, current_tuple):
        if step == k:
            valid_tuples.append(tuple(current_tuple))
            return
        
        prev_pos = current_tuple[-1] if current_tuple else None
        for pos in occs[step]:
            if prev_pos is not None:
                if pos <= prev_pos:
                    continue
                if pos - prev_pos > gap_limit:
                    continue
            current_tuple.append(pos)
            backtrack(step + 1, current_tuple)
            current_tuple.pop()

    backtrack(0, [])

    if not valid_tuples:
        return set(), False

    # Sort candidates by (span, start_pos)
    valid_tuples.sort(key=lambda t: (t[-1] - t[0], t[0]))

    used_positions = set()
    selected_positions = set()
    
    for t in valid_tuples:
        if any(p in used_positions for p in t):
            continue
        for p in t:
            used_positions.add(p)
            selected_positions.add(p)

    return selected_positions, len(selected_positions) > 0

def parse_source_sentences(text, text_mode, config):
    smc = SentencesModeConfig.from_config(config)
    sbc = SentenceBoundaryConfig.from_config(config)
    wrap_max_chars = config.getint(SEC_TRANSLATION, 'translation_wrap_max_chars', fallback=90)
    eff_mode = _effective_text_mode(text, text_mode)
    
    if eff_mode == 'single':
        source_sentences = split_single_mode_text(text, wrap_max_chars, abbrevs=sbc.abbrev_set, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
    else:
        # Match kardenwort.py core behavior: if multi_mode_remove_empty_lines is true, drop empty lines.
        remove_empty = config.getboolean(SEC_SETTINGS, 'multi_mode_remove_empty_lines', fallback=True)
        if smc.multi_mode_decompose:
            source_sentences = []
            for line in text.splitlines():
                if line.strip():
                    # Pass max_chars=0 to disable arbitrary length wrapping for multi mode paragraphs
                    source_sentences.extend(split_single_mode_text(line, 0, abbrevs=sbc.abbrev_set, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks))
                elif not remove_empty:
                    source_sentences.append(line)
            # CRITICAL: Overwrite the original text with joined flattened sentences.
            # In multi mode, kardenwort.py core determines data array length strictly by newlines via splitlines().
            # If we don't sync this string, the core will generate fewer items than the frontend expects,
            # causing fatal misalignments between generated sentence windows and TSV lemma mappings.
            text = "\n".join(source_sentences)
        else:
            # In multi mode, preserve the exact line structure to match kardenwort.py's indexing
            if remove_empty:
                source_sentences = [line for line in text.splitlines() if line.strip()]
                text = "\n".join(source_sentences)
            else:
                source_sentences = text.splitlines()
    return source_sentences, text, smc


LANGUAGE_NAMES_MAP: Dict[str, str] = {
    "en": "English",
    "de": "German",
    "ru": "Russian",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "zh": "Chinese",
    "ja": "Japanese",
    "pt": "Portuguese",
    "nl": "Dutch",
    "pl": "Polish",
    "uk": "Ukrainian",
}


def get_language_display_name(code: Optional[str]) -> str:
    if not code:
        return ""
    clean = code.strip().lower()
    return LANGUAGE_NAMES_MAP.get(clean, code.strip().capitalize())


def render_verify_language_html(
    mismatch_info: Optional[dict],
    theme: str = "dark",
    api_token: str = "",
    session_zid: str = "",
) -> str:
    """
    Renders a lightweight, standalone HTML page for /verify-language.
    Displays language mismatch confirmation card with Yes, No, and Cancel buttons.
    """
    mismatch = mismatch_info or {}
    zid = session_zid or mismatch.get("session_zid", "")
    det_code = mismatch.get("detected_language") or ""
    exp_code = mismatch.get("expected_language") or ""
    det_name = mismatch.get("detected_name") or get_language_display_name(det_code) or det_code
    exp_name = mismatch.get("expected_name") or get_language_display_name(exp_code) or exp_code
    det_label = f"{det_name} ({det_code})" if det_code else det_name
    exp_label = f"{exp_name} ({exp_code})" if exp_code else exp_name

    if det_label and exp_label:
        prompt_text = f"The text appears to be {det_label}, but the active profile is {exp_label}.\n\nSwitch language to {det_name}?"
    else:
        prompt_text = "Language mismatch detected.\n\nProceed with verification?"

    is_light = theme in ("light", "white")
    theme_class = "theme-light" if is_light else "theme-dark"

    token_json = json.dumps(api_token)
    zid_json = json.dumps(zid)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Language Verification - Kardenwort</title>
<style>
:root {{
    --bg-primary: #0d0f12;
    --bg-card: #161a22;
    --bg-hover: #1f242d;
    --border-color: rgba(255, 255, 255, 0.1);
    --border-hover: rgba(255, 255, 255, 0.2);
    --text-main: #e3e6eb;
    --text-muted: #8b949e;
    --card-shadow: 0 8px 32px rgba(0, 0, 0, 0.45);
}}
body.theme-light, body.theme-white {{
    --bg-primary: #f6f8fa;
    --bg-card: #ffffff;
    --bg-hover: #eaeef2;
    --border-color: #d0d7de;
    --border-hover: #afb8c1;
    --text-main: #24292f;
    --text-muted: #57606a;
    --card-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
}}
* {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}
body {{
    background-color: var(--bg-primary);
    color: var(--text-main);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 20px;
    overflow: hidden;
}}
.kw-verify-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 24px;
    max-width: 480px;
    width: 100%;
    box-shadow: var(--card-shadow);
    text-align: left;
    transition: opacity 0.2s ease;
}}
.kw-verify-title {{
    color: var(--text-main);
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.kw-verify-body {{
    color: var(--text-main);
    font-size: 13.5px;
    line-height: 1.6;
    margin-bottom: 22px;
    white-space: pre-wrap;
    word-break: break-word;
}}
.kw-verify-actions {{
    display: flex;
    justify-content: center;
    gap: 8px;
}}
.kw-btn {{
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    min-width: 84px;
    height: 28px;
    padding: 4px 14px;
    border-radius: 4px;
    cursor: pointer;
    border: 1px solid var(--border-color);
    background-color: var(--bg-card);
    color: var(--text-main);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    transition: background-color 0.15s ease, border-color 0.15s ease, opacity 0.15s ease;
    user-select: none;
}}
.kw-btn:hover:not(:disabled) {{
    background-color: var(--bg-hover);
    border-color: var(--border-hover);
}}
.kw-btn:disabled {{
    opacity: 0.4;
    cursor: not-allowed;
}}
.kw-btn-yes, .kw-btn-no, .kw-btn-cancel {{
    /* Inherit base .kw-btn design tokens */
}}
.kw-status-msg {{
    color: var(--text-muted);
    font-size: 13px;
    margin-top: 12px;
    display: none;
    text-align: center;
}}
</style>
</head>
<body class="{theme_class}">
<div class="kw-verify-card" id="kw-lang-modal">
    <div class="kw-verify-title" id="kw-lang-modal-title">Language Verification</div>
    <div class="kw-verify-body" id="kw-lang-modal-body">{prompt_text}</div>
    <div class="kw-verify-actions">
        <button id="kw-btn-lang-yes" class="kw-btn kw-btn-yes" onclick="submitChoice('switch')">Yes</button>
        <button id="kw-btn-lang-no" class="kw-btn kw-btn-no" onclick="submitChoice('keep')">No</button>
        <button id="kw-btn-lang-cancel" class="kw-btn kw-btn-cancel" onclick="submitChoice('cancel')">Cancel</button>
    </div>
    <div id="kw-status-msg" class="kw-status-msg"></div>
</div>
<script>
const sessionZid = {zid_json};
const apiToken = {token_json};
let submitted = false;

function setButtonsDisabled(disabled) {{
    document.getElementById('kw-btn-lang-yes').disabled = disabled;
    document.getElementById('kw-btn-lang-no').disabled = disabled;
    document.getElementById('kw-btn-lang-cancel').disabled = disabled;
}}

function showFallbackMessage(msg) {{
    const statusDiv = document.getElementById('kw-status-msg');
    if (statusDiv) {{
        statusDiv.textContent = msg;
        statusDiv.style.display = 'block';
    }}
    const bodyDiv = document.getElementById('kw-lang-modal-body');
    if (bodyDiv) {{
        bodyDiv.textContent = msg;
    }}
}}

function closeTabSafely(fallbackMsg) {{
    window.close();
    setTimeout(function() {{
        if (!window.closed) {{
            showFallbackMessage(fallbackMsg);
        }}
    }}, 200);
}}

function submitChoice(action) {{
    if (submitted) return;
    submitted = true;
    setButtonsDisabled(true);

    const headers = {{ 'Content-Type': 'application/json' }};
    if (apiToken) {{
        headers['X-API-Key'] = apiToken;
    }}

    fetch('/api/v1/confirm-language', {{
        method: 'POST',
        headers: headers,
        body: JSON.stringify({{ session_zid: sessionZid, action: action }})
    }})
    .then(function(res) {{
        return res.json();
    }})
    .then(function(data) {{
        const msg = action === 'cancel' 
            ? 'Verification cancelled. You may close this tab.' 
            : 'Configuration updated. You may close this tab.';
        closeTabSafely(msg);
    }})
    .catch(function(err) {{
        console.error('Language confirmation failed:', err);
        const msg = action === 'cancel' 
            ? 'Verification cancelled. You may close this tab.' 
            : 'Configuration updated. You may close this tab.';
        closeTabSafely(msg);
    }});
}}

document.addEventListener('keydown', function(e) {{
    if (e.key === 'Enter') {{
        e.preventDefault();
        submitChoice('switch');
    }} else if (e.key === 'Escape') {{
        e.preventDefault();
        submitChoice('cancel');
    }}
}});
</script>
</body>
</html>
"""


def run_render_flow(text, language, zid, text_mode, config, resolved_paths, zoom_level="100", theme="dark", tsv_path=None, split_gap_limit=60, wordfill_cfg=None, seq_num=None, trace_id=None, spawn_children=True, return_children=False, mismatch_info=None):
    with _ACTIVE_ZIDS_LOCK:
        if zid in _ACTIVE_ZIDS:
            logger.warning(f"[{zid}] Concurrent render skipped — already active. Rapid duplicate hotkey fire detected.")
            if return_children:
                return "", []
            return ""
        _ACTIVE_ZIDS.add(zid)
    try:
        return _run_render_flow_impl(text, language, zid, text_mode, config, resolved_paths, zoom_level, theme, tsv_path, split_gap_limit, wordfill_cfg, seq_num, trace_id=trace_id, spawn_children=spawn_children, return_children=return_children, mismatch_info=mismatch_info)
    finally:
        with _ACTIVE_ZIDS_LOCK:
            _ACTIVE_ZIDS.discard(zid)

def _run_render_flow_impl(text, language, zid, text_mode, config, resolved_paths, zoom_level="100", theme="dark", tsv_path=None, split_gap_limit=60, wordfill_cfg=None, seq_num=None, trace_id=None, spawn_children=True, return_children=False, mismatch_info=None):
    is_mismatch = bool(mismatch_info and mismatch_info.get("is_mismatch"))
    if wordfill_cfg is None and config is not None:
        wordfill_cfg = resolve_wordfill_config(config, resolved_paths)
    normalize_brackets = config.getboolean(SEC_SETTINGS, 'normalize_bracket_spacing', fallback=True) if config else True
    if text:
        text = text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
        if normalize_brackets:
            text = normalize_bracket_spacing(text)
    target_lang = config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')
    
    display_mode_val = config.get(SEC_RENDERING, 'display_mode', fallback='progressive')
    is_progressive_translation_enabled = config.getboolean(SEC_PIPELINE, 'progressive_text_translation', fallback=False)
    if display_mode_val != 'progressive':
        if is_progressive_translation_enabled:
            logger.info(f"[{zid}] progressive_text_translation=true has no effect: display_mode is '{display_mode_val}' (must be 'progressive').")
        is_progressive_translation_enabled = False
        
    progressive_timeout_seconds = config.getint(SEC_PIPELINE, 'progressive_timeout_seconds', fallback=15)
    children_tsv_paths = []
    ahk_args = []
    
    if tsv_path:
        try:
            p_tsv = Path(tsv_path)
            if p_tsv.exists():
                # STRICT CONTRACT WARNING: DO NOT change this logic to read `# Children` tags or any other arbitrary metadata from the TSV file!
                # The TSV file format is a strict cross-repository contract (desk, core, window, Anki, etc.).
                # Child relationships MUST be resolved purely by parsing the ZID sequences in filenames on disk.
                my_zid_match = re.match(r'^(\d{14})', p_tsv.name)
                if my_zid_match:
                    my_zid = my_zid_match.group(1)
                    try:
                        my_dt = datetime.strptime(my_zid, '%Y%m%d%H%M%S')
                        # Check if any older sibling exists in the batch (0 < delta <= 120s).
                        # If an older sibling exists, this window is a CHILD window, NOT a master.
                        has_older_sibling = False
                        older_prefixes = {
                            (my_dt - timedelta(seconds=s)).strftime('%Y%m%d%H%M')
                            for s in range(0, 121, 60)
                        }
                        for prefix in older_prefixes:
                            for sibling in p_tsv.parent.glob(f"{prefix}*.tsv"):
                                if sibling == p_tsv:
                                    continue
                                sib_match = re.match(r'^(\d{14})', sibling.name)
                                if not sib_match:
                                    continue
                                try:
                                    sib_dt = datetime.strptime(sib_match.group(1), '%Y%m%d%H%M%S')
                                    delta = (my_dt - sib_dt).total_seconds()
                                    if 0 < delta <= 120:
                                        has_older_sibling = True
                                        break
                                except (ValueError, TypeError):
                                    continue
                            if has_older_sibling:
                                break
                                
                        if not has_older_sibling:
                            # Children are spawned 1..N seconds after the master ZID.
                            # Glob two or three minute-prefixes to survive minute-boundary rollovers
                            # (e.g. master at :43 → child #17 lands at the next minute's :00).
                            # Supports up to 120 child sentences (well above typical use).
                            minute_prefixes = {
                                (my_dt + timedelta(seconds=s)).strftime('%Y%m%d%H%M')
                                for s in range(0, 121, 60)
                            }
                            for prefix in sorted(minute_prefixes):
                                for sibling in p_tsv.parent.glob(f"{prefix}*.tsv"):
                                    if sibling == p_tsv:
                                        continue
                                    sib_match = re.match(r'^(\d{14})', sibling.name)
                                    if not sib_match:
                                        continue
                                    try:
                                        sib_dt = datetime.strptime(sib_match.group(1), '%Y%m%d%H%M%S')
                                        delta = (sib_dt - my_dt).total_seconds()
                                        if 0 < delta <= 120:
                                            children_tsv_paths.append(sibling)
                                    except (ValueError, TypeError):
                                        continue
                            children_tsv_paths.sort(key=lambda p: p.name)
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
            
    sbc = SentenceBoundaryConfig.from_config(config)
        
    wrap_max_chars = config.getint(SEC_TRANSLATION, 'translation_wrap_max_chars', fallback=90)
    
    # parse_source_sentences builds SentencesModeConfig internally and returns it as the third value.
    # Reuse it here to avoid constructing SentencesModeConfig twice from the same config.
    source_sentences, text, smc = parse_source_sentences(text, text_mode, config)
    sentences_enabled = smc.enabled
    min_sentences = smc.min_sentences
    alignment_method = smc.alignment_method
    spawn_order = smc.spawn_order
    parent_mode = smc.parent_mode
    multi_mode_decompose = smc.multi_mode_decompose
    will_split = not is_mismatch and not tsv_path and (
        smc.should_split_sentences(len(source_sentences)) or 
        # legacy_spawn_children only fires when sentences_mode is enabled to avoid
        # unexpected splits from old config files migrated with enabled=false.
        (text_mode == 'multi' and smc.enabled and smc.legacy_spawn_children and len(source_sentences) >= 2)
    )
    
    # Progressive mode is incompatible with Sentences Mode multi-window architecture ONLY when:
    #   a) We are about to split a fresh text into child windows (will_split), OR
    #   b) A TSV is provided AND it is a parent window with actual child TSVs already on disk.
    # A standalone merged TSV being re-rendered (no children) MUST stay progressive so that
    # the progressive worker is launched and lemma_base_provider translation runs correctly.
    tsv_has_active_children = bool(tsv_path and sentences_enabled and children_tsv_paths)
    if (will_split or tsv_has_active_children) and (display_mode_val == 'progressive' or is_progressive_translation_enabled):
        logger.info(f"[{zid}] Progressive mode disabled (incompatible with Sentences Mode multi-window architecture)")
        display_mode_val = 'monolithic'
        is_progressive_translation_enabled = False
        # NOTE: Do NOT mutate the shared config object here. The caller may reuse the same
        # ConfigParser across subsequent renders (e.g. cmd_restore calling run_render_flow
        # in a loop). Use the local variables display_mode_val and
        # is_progressive_translation_enabled throughout this function to track effective mode.
    
    if will_split:
        main_text_provider = config.get(SEC_PIPELINE, 'text_base_provider', fallback='google')
        master_slug = generate_slug(text)
        master_cache_key = f"{zid}-{master_slug}.{language}.tsv"
        
        translated_sentences = []
        translated_paragraph = ""
        master_tsv_path = None
        
        parallelize = config.getboolean(SEC_PIPELINE, 'parallelize_core_and_translation', fallback=True)
        
        def do_translation():
            if is_progressive_translation_enabled:
                logger.info(f"[{zid}] [Translation Worker] Bypassed background translation due to progressive_text_translation=true")
                return ""
            logger.info(f"[{zid}] [Translation Worker] Starting background translation via {main_text_provider}")
            return translate_text(text, language, target_lang, config, resolved_paths, main_text_provider, zid=zid)
            
        def do_core():
            logger.info(f"[{zid}] [Core Worker] Starting background TSV generation")
            return prepare_lookup_tsv(
                text, language, target_lang, config, resolved_paths, zid,
                ttl_seconds=0, cache_key=master_cache_key, text_mode=text_mode,
                will_split=will_split
            )
            
        if parallelize:
            executor = ThreadPoolExecutor(max_workers=2)
            try:
                future_trans = executor.submit(do_translation)
                future_core = executor.submit(do_core)
                
                concurrent.futures.wait(
                    [future_trans, future_core], 
                    return_when=concurrent.futures.FIRST_EXCEPTION
                )
                
                core_exc = future_core.exception()
                if core_exc:
                    logger.error(f"[{zid}] Core TSV generation failed in parallel executor: {core_exc}")
                    raise core_exc
                    
                trans_exc = future_trans.exception()
                if trans_exc:
                    logger.warning(f"[{zid}] Holistic translation failed in parallel executor: {trans_exc}")
                else:
                    try:
                        translated_paragraph = future_trans.result()
                        translated_sentences = split_single_mode_text(translated_paragraph, wrap_max_chars, abbrevs=None, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
                    except Exception as e:
                        logger.warning(f"[{zid}] Holistic translation failed in parallel executor during split: {e}")
                        
                master_tsv_path = future_core.result()
            finally:
                if sys.version_info >= (3, 9):
                    executor.shutdown(wait=False, cancel_futures=True)
                else:
                    executor.shutdown(wait=False)
        else:
            try:
                translated_paragraph = do_translation()
                translated_sentences = split_single_mode_text(translated_paragraph, wrap_max_chars, abbrevs=None, terminators=sbc.terminators, punctuation_marks=sbc.punctuation_marks)
            except Exception as e:
                logger.warning(f"[{zid}] Holistic translation failed: {e}")
                
            master_tsv_path = do_core()
                
        # Fallback to newline_join block translation when sentence-count alignment fails
        attempt_newline_join = not is_progressive_translation_enabled and ((len(translated_sentences) != len(source_sentences)) or (alignment_method == 'newline_join'))
        if attempt_newline_join and alignment_method != 'proportion':
            try:
                translations_dict = translate_source_text(
                    "\n".join(source_sentences), language, target_lang, 'multi',
                    config, resolved_paths, main_text_provider, zid=zid
                )
                translated_sentences = [translations_dict.get(i, "").strip() for i in range(len(source_sentences))]
            except Exception as e:
                logger.error(f"[{zid}] Newline-join alignment fallback failed: {e}")
                
        # Final proportional safety net fallback
        if not is_progressive_translation_enabled and len(translated_sentences) != len(source_sentences):
            if not translated_paragraph:
                try:
                    translated_paragraph = translate_text(text, language, target_lang, config, resolved_paths, main_text_provider, zid=zid)
                except Exception:
                    translated_paragraph = "[Translation Error]"
            lengths = [len(s) for s in source_sentences]
            translated_sentences = split_by_proportion(translated_paragraph, lengths)
            
        while len(translated_sentences) < len(source_sentences):
            translated_sentences.append("")
        translated_sentences = translated_sentences[:len(source_sentences)]
        # Compute padding for the master TSV and all child windows
        apply_source_padding = False
        apply_translated_padding = False
        if sbc.words_before > 0 or sbc.words_after > 0:
            if sbc.context_mode in ('both', 'single'):
                apply_source_padding = True
        
        if sbc.translated_words_before > 0 or sbc.translated_words_after > 0:
            if sbc.context_mode in ('both', 'single'):
                apply_translated_padding = True

        padded_source_sentences = source_sentences
        if apply_source_padding:
            padded_source_sentences = pad_sentences(source_sentences, text, sbc.words_before, sbc.words_after, max_words=sbc.max_words)
            
        padded_translated_sentences = translated_sentences
        if apply_translated_padding:
            padded_translated_sentences = pad_translated_sentences(translated_sentences, sbc.translated_words_before, sbc.translated_words_after, max_words=sbc.translated_max_words)
        
        storage_adapter = get_storage_adapter(config, resolved_paths)
        comments, headers, data_rows = storage_adapter.load_tsv_rows(master_tsv_path)
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        
        col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
        col_sentence_source = headers.index(role_fields['sentence_source']) if 'sentence_source' in role_fields and role_fields['sentence_source'] in headers else -1
        col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
        
        # Populate translations back into master TSV using the padded sentences
        for row in data_rows:
            row_sent_idx = -1
            if col_index != -1 and len(row) > col_index:
                try:
                    row_sent_idx = int(row[col_index]) - 1
                except ValueError:
                    pass
            if 0 <= row_sent_idx < len(source_sentences):
                if col_sentence_source != -1:
                    while len(row) <= col_sentence_source:
                        row.append("")
                    row[col_sentence_source] = padded_source_sentences[row_sent_idx]
                if col_sentence_dest != -1:
                    while len(row) <= col_sentence_dest:
                        row.append("")
                    row[col_sentence_dest] = padded_translated_sentences[row_sent_idx]
                    
        col_word_source = headers.index(role_fields.get('lemma', 'WordSource')) if role_fields.get('lemma', 'WordSource') in headers else -1
        col_pos = headers.index(role_fields.get('pos', 'WordSourcePOS')) if role_fields.get('pos', 'WordSourcePOS') in headers else -1
        col_inflected = headers.index(role_fields.get('inflected', 'WordSourceInflectedForm')) if role_fields.get('inflected', 'WordSourceInflectedForm') in headers else -1
        col_inflected2 = headers.index('WordSourceInflectedForm2') if 'WordSourceInflectedForm2' in headers else -1
        col_quotation = headers.index('Quotation') if 'Quotation' in headers else -1
        
        dedup_scope_cfg = smc.deduplication_scope
        if col_word_source != -1 and dedup_scope_cfg != 'none':
            master_data_rows = deduplicate_rows(data_rows, col_word_source, col_pos, col_inflected, config, window_text=text, language=language, resolved_paths=resolved_paths)
        else:
            master_data_rows = [list(r) for r in data_rows]

        # STRICT CONTRACT WARNING: DO NOT append `# Children` tags or any other arbitrary metadata to `comments` here!
        # The TSV format must remain clean. Child relationships are strictly resolved via filenames.
        storage_adapter = get_storage_adapter(config, resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

        if is_sqlite:
            master_sentences = []
            for idx_s, s_src in enumerate(source_sentences):
                s_dst = translated_sentences[idx_s] if idx_s < len(translated_sentences) else ""
                s_dst2 = padded_translated_sentences[idx_s] if idx_s < len(padded_translated_sentences) else s_dst
                master_sentences.append({
                    "session_zid": zid,
                    "sentence_index": idx_s + 1,
                    "sentence_source": s_src,
                    "sentence_destination": s_dst,
                    "sentence_destination2": s_dst2,
                })
            storage_adapter.save_session(
                session_zid=zid,
                slug=master_slug,
                source_language=language,
                target_language=target_lang,
                text_mode="multi" if text_mode == "multi" else "single",
                source_raw_text=text,
                comments=comments,
                headers=headers,
                data_rows=master_data_rows,
                sentences=master_sentences,
                working_tsv_path=None,
                zid=zid,
            )
        else:
            save_tsv_rows_safely(master_tsv_path, comments, headers, master_data_rows)

        kardenwort_workspace = resolved_paths['kardenwort_workspace']
        kw_config = load_kardenwort_config(kardenwort_workspace)
        results_dir = resolve_results_dir(resolved_paths, kw_config)
        if not is_sqlite:
            results_dir.mkdir(parents=True, exist_ok=True)
            
            # Cleanup old markers from previous runs with the same ZID to prevent race conditions
            for marker in results_dir.glob(f"{zid[:14]}*"):
                if marker.suffix in ['.base_translation_done', '.enrichment_done', '.the_cut_done']:
                    try:
                        marker.unlink()
                    except Exception:
                        pass
            
            # Write master translation file
            master_trans_path = results_dir / f"{zid}-{master_slug}.{target_lang}.txt"
            master_trans_path.write_text(translated_paragraph, encoding='utf-8')
        
        with TraceTimer("the_cut", zid, config, resolved_paths):
            sub_tsv_paths = []
            token_cfg = RuntimeTokenConfig.from_config(config)
            apo_set = set(c.strip() for c in token_cfg.apostrophe_chars.split(',') if c.strip())
            apo_regex = r"[\w" + "".join(re.escape(c) for c in sorted(apo_set)) + r"]+"
            try:
                master_time = datetime.strptime(zid[:14], '%Y%m%d%H%M%S')
            except Exception:
                master_time = datetime.now()
                
            for i in range(len(source_sentences)):
                sub_text = source_sentences[i]
                sub_trans = translated_sentences[i] if i < len(translated_sentences) else ""
                sub_trans_padded = padded_translated_sentences[i] if i < len(padded_translated_sentences) else sub_trans
                
                sub_dt = master_time + timedelta(seconds=i+1)
                sub_zid = sub_dt.strftime('%Y%m%d%H%M%S')
                
                sub_slug = generate_slug(sub_text)
                
                if not is_sqlite:
                    sub_txt_path = results_dir / f"{sub_zid}-{sub_slug}.{language}.txt"
                    sub_txt_path.write_text(sub_text, encoding='utf-8')
                    
                    sub_trans_path = results_dir / f"{sub_zid}-{sub_slug}.{target_lang}.txt"
                    sub_trans_path.write_text(sub_trans, encoding='utf-8')
                
                sub_rows = []
                sub_tokens = tok.build_word_list_internal(sub_text, keep_spaces=True)
                sub_words = {t["lower_clean"] for t in sub_tokens if t.get("is_word") and "lower_clean" in t}
                
                for row in data_rows:
                    row_sent_idx = -1
                    if col_index != -1 and len(row) > col_index:
                        try:
                            row_sent_idx = int(row[col_index]) - 1
                        except ValueError:
                            pass
                    
                    if row_sent_idx != -1:
                        matches_sentence = (row_sent_idx == i)
                    else:
                        matches_sentence = False
                        row_inf = resolve_row_inflected_form(row, col_inflected, col_inflected2, col_quotation, col_word_source)
                        row_lem = row[col_word_source].strip() if col_word_source != -1 and len(row) > col_word_source else ""
                        forms = [f.strip() for f in row_inf.split(',')] if row_inf else []
                        clean_lemma = row_lem.strip() if row_lem else ""
                        has_compound = any(any(ch in f for ch in ('_', '-', '.', '/', '\\', ':', '#', '@')) or len(tok.split_camel_case(f)) > 1 for f in forms)
                        if clean_lemma and (any(ch in clean_lemma for ch in ('_', '-', '.', '/', '\\', ':', '#', '@')) or len(tok.split_camel_case(clean_lemma)) > 1):
                            has_compound = True
                        if has_compound or not forms:
                            forms_to_check = list(dict.fromkeys(forms + ([clean_lemma] if clean_lemma else [])))
                        else:
                            forms_to_check = forms

                        for f in forms_to_check:
                            if not f: continue
                            clean_f = tok.utf8_to_lower("".join(ch for ch in f if ch.isalnum() or ch in apo_set))
                            if clean_f in sub_words:
                                matches_sentence = True
                                break
                            subtokens = tok.decompose_identifier(f)
                            if any(tok.utf8_to_lower("".join(ch for ch in s if ch.isalnum() or ch in apo_set)) in sub_words for s in subtokens):
                                matches_sentence = True
                                break
                            parts = re.findall(apo_regex, f.lower())
                            if any("".join(ch for ch in p if ch.isalnum() or ch in apo_set) in sub_words for p in parts if len(p) > 1 and p in sub_words):
                                matches_sentence = True
                                break
                                
                    if matches_sentence:
                        sub_row = list(row)
                        if col_index != -1:
                            while len(sub_row) <= col_index:
                                sub_row.append("")
                            sub_row[col_index] = "1"
                        if col_sentence_source != -1:
                            while len(sub_row) <= col_sentence_source:
                                sub_row.append("")
                            sub_row[col_sentence_source] = padded_source_sentences[i]
                        if col_sentence_dest != -1:
                            while len(sub_row) <= col_sentence_dest:
                                sub_row.append("")
                            sub_trans_padded = padded_translated_sentences[i] if i < len(padded_translated_sentences) else sub_trans
                            sub_row[col_sentence_dest] = sub_trans_padded
                        sub_rows.append(sub_row)
                        
                if col_word_source != -1 and dedup_scope_cfg == 'sentence':
                    sub_rows = deduplicate_rows(sub_rows, col_word_source, col_pos, col_inflected, config, window_text=sub_text, language=language, resolved_paths=resolved_paths)

                # Pre-sort child sentence rows by lemma frequency so restore_session() is an immediate O(1) load
                sub_rows = sort_rows_by_frequency(
                    data_rows=sub_rows,
                    headers=headers,
                    lang=language,
                    config=config,
                    resolved_paths=resolved_paths,
                    role_fields=role_fields,
                )

                sub_tsv_path = results_dir / f"{sub_zid}-{sub_slug}.{language}.tsv"
                if is_sqlite:
                    child_sentences = [{
                        "session_zid": sub_zid,
                        "sentence_index": 1,
                        "sentence_source": sub_text,
                        "sentence_destination": sub_trans,
                        "sentence_destination2": sub_trans_padded,
                    }]
                    storage_adapter.save_session(
                        session_zid=sub_zid,
                        slug=sub_slug,
                        source_language=language,
                        target_language=target_lang,
                        text_mode="single",
                        source_raw_text=sub_text,
                        comments=comments,
                        headers=headers,
                        data_rows=sub_rows,
                        sentences=child_sentences,
                        working_tsv_path=None,
                        zid=sub_zid,
                    )
                else:
                    save_tsv_rows_safely(sub_tsv_path, comments, headers, sub_rows)
                sub_tsv_paths.append(sub_tsv_path)
            

        master_seq = seq_num if seq_num is not None else 1
        ahk_args = []
        
        paths_to_spawn = [(master_seq + i + 1, path) for i, path in enumerate(sub_tsv_paths)]
        if spawn_order == 'reverse':
            paths_to_spawn.reverse()
            
        for seq, path in paths_to_spawn:
            ahk_args.extend(["--seq-num", str(seq), "--restore", str(path)])

        base_dir = resolved_paths.get('base_dir') if resolved_paths else Path('.')
        if ahk_args and spawn_children:
            spawn_ahk(ahk_args, base_dir)
        
        if not is_sqlite:
            try:
                master_tsv_path.with_suffix('.the_cut_done').touch(exist_ok=True)
            except Exception:
                pass
        
        if parent_mode == 'stub':
            if not is_sqlite:
                try:
                    if master_tsv_path.exists():
                        master_tsv_path.unlink()
                except Exception:
                    pass
                try:
                    master_txt_path = results_dir / f"{zid}-{master_slug}.{language}.txt"
                    if master_txt_path.exists():
                        master_txt_path.unlink()
                except Exception:
                    pass
                try:
                    master_trans_path = results_dir / f"{zid}-{master_slug}.{target_lang}.txt"
                    if master_trans_path.exists():
                        master_trans_path.unlink()
                except Exception:
                    pass
            try:
                # Clean up orphaned .updates/ directory created by write_update_js for progressive mode.
                # Without this, stub-mode TSV deletions leave empty directories accumulating in results/.
                master_updates_dir = master_tsv_path.parent / f"{master_tsv_path.stem}.updates"
                if master_updates_dir.exists() and master_updates_dir.is_dir():
                    import shutil
                    shutil.rmtree(master_updates_dir, ignore_errors=True)
            except Exception:
                pass
            try:
                storage_adapter = get_storage_adapter(config, resolved_paths)
                if storage_adapter.backend_name == 'sqlite':
                    storage_adapter.delete_session(zid)
            except Exception as e:
                logger.warning(f"Failed deleting stub parent session from SQLite: {e}")

            bg_color = "#f6f8fa" if theme in ("light", "white") else "#0d0f12"
            text_color = "#24292f" if theme in ("light", "white") else "#c9d1d9"
            
            paths_str = ",".join(str(path) for path in sub_tsv_paths)
            children_div = f'<div id="kardenwort-children" style="display:none;">{paths_str}</div>'
            stub_div = '<div id="kardenwort-is-stub" style="display:none;">1</div>'
            
            stub_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<style>
html, body {{
    overflow: hidden;
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    background-color: {bg_color};
}}
</style>
</head>
<body style="color: {text_color}; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; text-align: center; display: flex; align-items: center; justify-content: center; box-sizing: border-box;">
    <div style="font-size: 16px; font-weight: 500;">Splitting paragraph into separate sentence windows...</div>
    {children_div}
    {stub_div}
</body>
</html>
"""
            if return_children:
                return stub_html, ahk_args
            return stub_html

        # Override tsv_path and children_tsv_paths to let the render flow continue
        tsv_path = master_tsv_path
        children_tsv_paths = sub_tsv_paths

    
    # Resolve audio playback configuration
    lmb_play_val = "false"
    lmb_source_val = "lemma"
    lmb_chain_mode_val = "joined"
    rmb_play_val = "false"
    rmb_chain_mode_val = "separate"
    if config.has_section(SEC_AUDIO):
        lmb_play_val = "true" if config.getboolean(SEC_AUDIO, 'lmb_play', fallback=False) else "false"
        lmb_source_val = config.get(SEC_AUDIO, 'lmb_source', fallback='lemma').strip().lower()
        lmb_chain_mode_val = config.get(SEC_AUDIO, 'lmb_chain_mode', fallback='joined').strip().lower()
        rmb_play_val = "true" if config.getboolean(SEC_AUDIO, 'rmb_play', fallback=False) else "false"
        rmb_chain_mode_val = config.get(SEC_AUDIO, 'rmb_chain_mode', fallback='separate').strip().lower()

    anki_tts_cli_path = ""
    if 'anki_tts_cli' in resolved_paths:
        anki_tts_cli_path = str(resolved_paths['anki_tts_cli']).replace('\\', '/')

    python_exe_path = ""
    if 'kardenwort_python' in resolved_paths:
        python_exe_path = str(resolved_paths['kardenwort_python']).replace('\\', '/')

    
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    
    slug = generate_slug(text)
    cache_key = f"{zid}-{slug}.{language}.tsv"
    
    eff_mode = _effective_text_mode(text, text_mode)
    
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    cached_session_bundle = None
    if is_sqlite:
        ttl_val = config.getint(SEC_STORAGE, 'cache_ttl_seconds', fallback=config.getint(SEC_SETTINGS, 'lookup_ttl_seconds', fallback=86400))
        if ttl_val > 0:
            cached_session_bundle = storage_adapter.get_cached_session(
                slug, language, ttl_val,
                source_raw_text=text,
                target_language=target_lang,
                text_mode=eff_mode,
                zid=zid,
            )

    if is_mismatch:
        working_tsv_path = Path(tsv_path) if tsv_path else (results_dir / f"{zid}-{slug}.{language}.tsv")
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file']) if (resolved_paths and 'anki_mapping_file' in resolved_paths) else {}
        comments = []
        headers = ["WordSource", "WordDestination", "WordSourceInflectedForm", "WordSourceIPA", "WordSourceMorphologyAI", "DeskSelected"]
        data_rows = []
    elif cached_session_bundle and cached_session_bundle.get("session"):
        cached_zid = cached_session_bundle["session"].get("zid")
        working_tsv_path = results_dir / f"{cached_zid}-{slug}.{language}.tsv"
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)
    elif tsv_path and (Path(tsv_path).exists() or is_sqlite):
        working_tsv_path = Path(tsv_path)
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)
    else:
        working_tsv_path = prepare_lookup_tsv(
            text, language, target_lang, config, resolved_paths, zid,
            ttl_seconds=0, cache_key=cache_key, text_mode=eff_mode
        )
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)

    tsv_slug = slug
    tsv_match = re.match(r'^\d{14}-(.*?)(?:\.[a-z]{2})?\.tsv$', working_tsv_path.name, re.IGNORECASE)
    if tsv_match:
        tsv_slug = tsv_match.group(1)

    if is_sqlite and (not text or not text.strip()):
        try:
            target_zid_to_read = extract_zid(working_tsv_path)
            if target_zid_to_read != "00000000000000":
                restored_meta = storage_adapter.restore_session(target_zid_to_read)
                if restored_meta and restored_meta.get("source_text"):
                    text = restored_meta["source_text"]
                    eff_mode = _effective_text_mode(text, text_mode)
        except Exception:
            pass

    llm_filled = is_tsv_llm_filled(headers, data_rows, mapping)
    
    text_base_provider = config.get(SEC_PIPELINE, 'text_base_provider', fallback='google')
    main_text_provider = text_base_provider
    lemma_base_provider = config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google')
    role_fields = get_role_fields(mapping, headers)
    col_lemma_check = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    if col_lemma_check != -1:
        seen_lemmas_check = set()
        dup_lemmas = []
        for r in data_rows:
            if len(r) > col_lemma_check:
                l_val = r[col_lemma_check].strip().lower()
                if l_val:
                    if l_val in seen_lemmas_check and l_val not in dup_lemmas:
                        dup_lemmas.append(l_val)
                    seen_lemmas_check.add(l_val)
        if dup_lemmas:
            logger.warning(f"Duplicate lemmas detected in restored data_rows: {dup_lemmas}")

    col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
    if col_token_order == -1:
        headers.append("TokenOrder")
        col_token_order = len(headers) - 1
        for r_i, r in enumerate(data_rows):
            r.append(str(r_i))

    data_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
        
    col_highlighted = headers.index(role_fields['selected']) if 'selected' in role_fields and role_fields['selected'] in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_inflected = headers.index(role_fields['inflected']) if 'inflected' in role_fields and role_fields['inflected'] in headers else -1
    col_inflected2 = headers.index('WordSourceInflectedForm2') if 'WordSourceInflectedForm2' in headers else -1
    col_quotation = headers.index('Quotation') if 'Quotation' in headers else -1
    
    # --- Word-fill early pre-fill step ---
    if not is_mismatch and wordfill_cfg and wordfill_cfg.get('enabled', False):
        try:
            col_lemma_wf = col_lemma if col_lemma != -1 else (headers.index('WordSource') if 'WordSource' in headers else -1)
            if col_lemma_wf != -1:
                seen_lemmas = {}
                for i, row in enumerate(data_rows):
                    if len(row) > col_lemma_wf:
                        lemma_val = row[col_lemma_wf].strip()
                        if lemma_val:
                            seen_lemmas.setdefault(lemma_val, []).append(i)
                for lemma_val, row_indices in seen_lemmas.items():
                    match = find_wordfill_match(lemma_val, language, wordfill_cfg, exclude_path=working_tsv_path)
                    if match:
                        lemma_rows = [data_rows[i] for i in row_indices]
                        apply_wordfill_to_rows(lemma_rows, headers, match)
                        logger.info(
                            f"wordfill (desk): pre-filled {len(match)} field(s) for lemma '{lemma_val}' "
                            f"from corpus."
                        )
                if is_sqlite:
                    storage_adapter.save_session(
                        session_zid=zid,
                        slug=tsv_slug,
                        source_language=language,
                        target_language=target_lang,
                        text_mode=text_mode,
                        source_raw_text=text,
                        comments=comments,
                        headers=headers,
                        data_rows=_sanitize_rows(data_rows),
                        working_tsv_path=None,
                        zid=zid,
                    )
                else:
                    with file_lock(working_tsv_path):
                        save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
        except Exception as wf_err:
            logger.warning(f"wordfill (desk): early pre-fill step failed, continuing: {wf_err}")

    col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
    
    is_progressive = display_mode_val == 'progressive'
    updates_dir = working_tsv_path.parent / f"{working_tsv_path.stem}.updates"
    if is_progressive and updates_dir.exists():
        try:
            for js_file in updates_dir.glob("*.js"):
                with open(js_file, 'r', encoding='utf-8') as f:
                    if '"stage": "finished"' in f.read():
                        is_progressive = False
                        break
        except Exception:
            pass
    auto_inject_updates = config.getboolean(SEC_RENDERING, 'auto_inject_updates', fallback=True)
    hover_highlight_enabled = config.getboolean(SEC_RENDERING, 'hover_highlight', fallback=True)
    hover_highlight_bookmarks = config.getint(SEC_RENDERING, 'hover_highlight_bookmarks', fallback=3)
    hover_highlight_rainbow = config.getboolean(SEC_RENDERING, 'hover_highlight_rainbow', fallback=True)
    run_base = config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback='auto')
    run_text = config.get(SEC_TRIGGERS, 'run_text_translation', fallback='auto')
    run_enrich = config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='auto')
    base_provider = config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google')
    enrich_provider = config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback='intellifiller')
    
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    source_text_path = working_tsv_path.with_suffix('.txt')
    if not is_sqlite and not is_mismatch:
        if eff_mode == 'single':
            source_text_path.write_text(text, encoding='utf-8')
        elif not source_text_path.exists():
            source_text_path.write_text(text, encoding='utf-8')
            
    sentence_translated = False
    if col_sentence_dest != -1:
        if any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows):
            sentence_translated = True
            
    has_untranslated_lemmas = False
    
    # Get column indices for IPA and Morphology to check if they are missing
    col_ipa = headers.index(role_fields.get('ipa', 'WordSourceIPA')) if role_fields.get('ipa', 'WordSourceIPA') in headers else -1
    col_morph = headers.index(role_fields.get('morphology', 'WordSourceMorphologyAI')) if role_fields.get('morphology', 'WordSourceMorphologyAI') in headers else -1

    if col_lemma != -1 and col_word_dest != -1:
        for row in data_rows:
            if len(row) > col_lemma and row[col_lemma].strip():
                need_dest = len(row) <= col_word_dest or not row[col_word_dest].strip()
                need_ipa = base_provider == 'intellifiller' and col_ipa != -1 and (len(row) <= col_ipa or not row[col_ipa].strip())
                need_morph = base_provider == 'intellifiller' and col_morph != -1 and (len(row) <= col_morph or not row[col_morph].strip())
                if need_dest or need_ipa or need_morph:
                    has_untranslated_lemmas = True
                    break
    elif col_word_dest != -1:
        # Fallback if col_lemma is not defined but we have word_dest
        if not all(len(row) > col_word_dest and row[col_word_dest].strip() for row in data_rows if any(row)):
            has_untranslated_lemmas = True
            
    # If monolithic mode and run_base is auto, run base translation synchronously
    if not is_mismatch and not is_progressive and run_base == 'auto':
        try:
            if not sentence_translated:
                with TraceTimer("monolithic_text_translation", zid, config, resolved_paths):
                    sentence_translations_raw = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, main_text_provider, zid=zid)
                resolve_translations(
                    text, text_mode, data_rows, col_index, col_sentence_dest,
                    sentence_translations_raw, working_tsv_path, comments, headers,
                    persist=(not is_sqlite), return_single=False
                )
                if is_sqlite:
                    storage_adapter.save_session(
                        session_zid=zid,
                        slug=tsv_slug,
                        source_language=language,
                        target_language=target_lang,
                        text_mode=text_mode,
                        source_raw_text=text,
                        comments=comments,
                        headers=headers,
                        data_rows=data_rows,
                        working_tsv_path=None,
                        zid=zid,
                    )
                if not is_sqlite:
                    eff_mode = _effective_text_mode(text, text_mode)
                    translation_text_path = results_dir / f"{zid}-{tsv_slug}.{target_lang}.txt"
                    save_translation_text = config.getboolean(SEC_SETTINGS, 'save_translation_text', fallback=False)
                    _write_translation_txt(text, eff_mode, sentence_translations_raw, translation_text_path, save_flag=save_translation_text, overwrite=True)
                
 
        except TranslationAlignmentError as tae:
            logger.error(f"Monolithic translation alignment error: {tae}")
            sentence_translations_raw = tae.partial_dict
            resolve_translations(
                text, text_mode, data_rows, col_index, col_sentence_dest,
                sentence_translations_raw, working_tsv_path, comments, headers,
                persist=(not is_sqlite), return_single=False
            )
            if is_sqlite:
                storage_adapter.save_session(
                    session_zid=zid,
                    slug=tsv_slug,
                    source_language=language,
                    target_language=target_lang,
                    text_mode=text_mode,
                    source_raw_text=text,
                    comments=comments,
                    headers=headers,
                    data_rows=data_rows,
                    working_tsv_path=None,
                    zid=zid,
                )
            if not is_sqlite:
                eff_mode = _effective_text_mode(text, text_mode)
                translation_text_path = results_dir / f"{zid}-{tsv_slug}.{target_lang}.txt"
                save_translation_text = config.getboolean(SEC_SETTINGS, 'save_translation_text', fallback=False)
                _write_translation_txt(text, eff_mode, sentence_translations_raw, translation_text_path, save_flag=save_translation_text, overwrite=True)
            run_enrich = 'manual'
                
        if has_untranslated_lemmas and not children_tsv_paths:
            if base_provider == 'intellifiller':
                selected_rows_to_enrich = []
                for i, row in enumerate(data_rows):
                    if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
                        need_dest = col_word_dest == -1 or len(row) <= col_word_dest or not row[col_word_dest].strip()
                        need_ipa = col_ipa != -1 and (len(row) <= col_ipa or not row[col_ipa].strip())
                        need_morph = col_morph != -1 and (len(row) <= col_morph or not row[col_morph].strip())
                        if need_dest or need_ipa or need_morph:
                            selected_rows_to_enrich.append(i)
                            
                if selected_rows_to_enrich:
                    if is_sqlite:
                        prompt_name = config.get(SEC_LANGUAGES, f'{language}_prompt', fallback='')
                        storage_adapter.enrich_session_intellifiller(
                            session_zid=zid, prompt_name=prompt_name, selected_rows=selected_rows_to_enrich, reprocess=True, zid=zid
                        )
                        comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)
                    else:
                        with file_lock(working_tsv_path):
                            save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
                        prompt_name = config.get(SEC_LANGUAGES, f'{language}_prompt', fallback='')
                        run_headless_intellifiller(working_tsv_path, prompt_name, config, resolved_paths, selected_rows=selected_rows_to_enrich, reprocess=True)
                        comments, headers, data_rows = load_tsv_rows(working_tsv_path)
            else:
                lemmas_to_translate = []
                for row in data_rows:
                    if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
                        if col_word_dest == -1 or len(row) <= col_word_dest or not row[col_word_dest].strip():
                            lemmas_to_translate.append(row[col_lemma].strip())
                lemmas_to_translate = list(set(lemmas_to_translate))
                
                lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, language, target_lang, config, resolved_paths, base_provider)
                
                for row in data_rows:
                    if col_lemma != -1 and len(row) > col_lemma:
                        lemma_val = row[col_lemma]
                        if col_word_dest != -1:
                            while len(row) <= col_word_dest:
                                row.append("")
                            if not row[col_word_dest].strip():
                                row[col_word_dest] = lemma_translations.get(lemma_val, "")
                with file_lock(working_tsv_path):
                    save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
                
    translation_text_path = results_dir / f"{zid}-{tsv_slug}.{target_lang}.txt"
    
    extracted_translations = {}
    for row in data_rows:
        content_line_idx = 0
        if col_index != -1 and len(row) > col_index:
            try:
                content_line_idx = int(row[col_index]) - 1
            except ValueError:
                pass
        if col_sentence_dest != -1 and len(row) > col_sentence_dest:
            extracted_translations[content_line_idx] = row[col_sentence_dest]

    db_sents = None
    if is_sqlite:
        try:
            target_zid_to_read = extract_zid(working_tsv_path)
            if target_zid_to_read != "00000000000000":
                db_sents = storage_adapter.db.get_sentences_by_session(target_zid_to_read)
                for s in db_sents:
                    s_idx = s.get("sentence_index", 1) - 1
                    s_dest = s.get("sentence_destination")
                    if s_dest and str(s_dest).strip():
                        extracted_translations[s_idx] = str(s_dest).strip()
                    elif str(s.get("sentence_source", "")).strip().startswith("#"):
                        extracted_translations[s_idx] = str(s.get("sentence_source", "")).strip()
        except Exception:
            pass
            
    sentence_translations = {}
    if not sentence_translated and 'sentence_translations_raw' in locals():
        sentence_translations = sentence_translations_raw
    elif translation_text_path.exists():
        translation_lines = translation_text_path.read_text(encoding='utf-8').splitlines()
        if eff_mode == 'single':
            sentence_translations[0] = " ".join(translation_lines)
        else:
            clean_translations = [ln.strip() for ln in translation_lines if ln.strip()]
            c_idx = 0
            for a_idx, ln in enumerate(text.splitlines()):
                if ln.strip():
                    if c_idx < len(clean_translations):
                        sentence_translations[a_idx] = clean_translations[c_idx]
                    elif ln.strip().startswith("#"):
                        sentence_translations[a_idx] = ln.strip()
                    else:
                        sentence_translations[a_idx] = ""
                    c_idx += 1
                else:
                    sentence_translations[a_idx] = ""
    else:
        if eff_mode == 'single':
            if is_sqlite and db_sents:
                clean_lines = [str(s.get("sentence_destination")).strip() for s in sorted(db_sents, key=lambda x: x.get("sentence_index", 1)) if s.get("sentence_destination") and str(s.get("sentence_destination")).strip()]
                if clean_lines:
                    sentence_translations[0] = " ".join(clean_lines)
                else:
                    sentence_translations[0] = extracted_translations.get(0, "")
            else:
                sentence_translations[0] = extracted_translations.get(0, "")
        else:
            c_idx = 0
            for a_idx, ln in enumerate(text.splitlines()):
                if ln.strip():
                    val = extracted_translations.get(c_idx, "")
                    if not val and ln.strip().startswith("#"):
                        val = ln.strip()
                    sentence_translations[a_idx] = val
                    c_idx += 1
                else:
                    sentence_translations[a_idx] = ""
            
    if not is_sqlite and not is_mismatch:
        save_translation_text = config.getboolean(SEC_SETTINGS, 'save_translation_text', fallback=False)
        translation_text_path = results_dir / f"{zid}-{tsv_slug}.{target_lang}.txt"
        eff_mode = _effective_text_mode(text, text_mode)
        _write_translation_txt(text, eff_mode, sentence_translations, translation_text_path, save_flag=save_translation_text, overwrite=False)
            
    worker_launched = False
    if not is_mismatch and not llm_filled:
        prompt_name = config.get(SEC_LANGUAGES, f'{language}_prompt')
        
        is_master_window = bool(children_tsv_paths)
        
        if is_progressive or is_master_window:
            needs_worker = False
            if run_text == 'auto' and not sentence_translated:
                needs_worker = True
            if run_base == 'auto' and has_untranslated_lemmas and not is_master_window:
                needs_worker = True
            if run_enrich == 'auto' and enrich_provider == 'intellifiller' and not is_master_window:
                needs_worker = True
            # Master window: launch worker to receive cross-pollinated data from children.
            if is_master_window:
                needs_worker = True
                
            if needs_worker:
                skip_intellifiller = (run_enrich == 'manual') or (enrich_provider == 'none') or is_master_window
                try:
                    try:
                        run_progressive_worker_async(
                            working_tsv_path, language, target_lang, prompt_name,
                            base_provider, str(has_untranslated_lemmas),
                            skip_intellifiller, eff_mode,
                            zid=zid, trace_id=(trace_id or f"{zid}:progressive:worker")
                        )
                    except TypeError:
                        run_progressive_worker_async(
                            working_tsv_path, language, target_lang, prompt_name,
                            base_provider, str(has_untranslated_lemmas),
                            skip_intellifiller, eff_mode
                        )
                    worker_launched = True
                except Exception as e:
                    logger.error(f"Failed to launch progressive worker async: {e}")
                    worker_launched = False
        else:
            # Monolithic mode enrichment
            if run_enrich == 'auto' and enrich_provider == 'intellifiller':
                run_headless_intellifiller(working_tsv_path, prompt_name, config, resolved_paths)
                comments, headers, data_rows = load_tsv_rows(working_tsv_path)
                
    if is_progressive and not worker_launched:
        write_update_js(working_tsv_path, data_rows, headers, role_fields, stage="finished", empty_payload=True)
        # Only flag WordDestination as [FAILED] if the base provider was expected to fill it but didn't.
        # IPA, Morphology, etc. are exclusively intellifiller fields — they must NOT be flagged here
        # because intellifiller was never scheduled to run in this code path.
        if run_base == 'auto':
            try:
                with storage_adapter.file_lock(working_tsv_path):
                    comments, headers_latest, current_rows = storage_adapter.load_tsv_rows(working_tsv_path)
                    col_lemma = headers_latest.index(role_fields.get('lemma', 'WordSource')) if role_fields and role_fields.get('lemma', 'WordSource') in headers_latest else -1
                    col_word_dest = headers_latest.index(role_fields.get('word_translation', 'WordDestination')) if role_fields and role_fields.get('word_translation', 'WordDestination') in headers_latest else -1
                    
                    modified_sweep = False
                    updates = []
                    for row_idx, row in enumerate(current_rows):
                        if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
                            if col_word_dest != -1:
                                if len(row) <= col_word_dest:
                                    row.extend([''] * (col_word_dest - len(row) + 1))
                                if not row[col_word_dest].strip() or 'skeleton-loader' in row[col_word_dest]:
                                    row[col_word_dest] = ""
                                    modified_sweep = True
                                    if is_sqlite:
                                        updates.append({
                                            "token_order": row_idx,
                                            "field": "word_destination",
                                            "value": "",
                                        })
                    if modified_sweep:
                        if is_sqlite:
                            if updates:
                                storage_adapter.batch_update_words(session_zid=zid, updates_list=updates, zid=zid)
                        else:
                            save_tsv_rows_safely(working_tsv_path, comments, headers_latest, current_rows)
                        data_rows = current_rows
            except Exception as e:
                logger.error(f"Error sweeping FAILED (WordDestination) in UI thread: {e}")

        if not is_sqlite:
            try:
                working_tsv_path.with_suffix('.base_translation_done').touch(exist_ok=True)
            except Exception:
                pass
            try:
                working_tsv_path.with_suffix('.enrichment_done').touch(exist_ok=True)
            except Exception:
                pass

    token_cfg = RuntimeTokenConfig.from_config(config)
    apo_set = set(c.strip() for c in token_cfg.apostrophe_chars.split(',') if c.strip())
    apo_regex = r"[\w" + "".join(re.escape(c) for c in sorted(apo_set)) + r"]+"
                    
    def _has_comp_marker(s):
        if not s:
            return False
        if any(ch in s for ch in ('_', '-', '.', '/', '\\', ':', '#', '@')):
            return True
        return any(len(tok.split_camel_case(w)) > 1 for w in s.split())

    token_to_rows = {}
    row_candidates = {}
    row_direct_candidates = {}
    compound_rows = set()
    row_primary_lemmas = {}
    for row_id, row in enumerate(data_rows):
        if col_lemma != -1 and len(row) > col_lemma and re.match(r'^\d{14}-', row[col_lemma]):
            row = list(row)
            row[col_lemma] = re.sub(r'^\d{14}-', '', row[col_lemma])
            data_rows[row_id] = row
        lemma_val = row[col_lemma] if col_lemma != -1 and len(row) > col_lemma else ""
        inflected_val = resolve_row_inflected_form(row, col_inflected, col_inflected2, col_quotation, col_lemma)
        
        candidates = []
        candidates_seen = set()

        def _add_cand(c):
            if c and not c.isdigit() and c not in candidates_seen:
                candidates_seen.add(c)
                candidates.append(c)

        forms = [f.strip() for f in inflected_val.split(',')] if inflected_val else []
        clean_lemma = lemma_val.strip() if lemma_val else ""
        clean_lemma_lower = tok.utf8_to_lower("".join(ch for ch in clean_lemma if ch.isalnum() or ch in apo_set)) if clean_lemma else ""
        row_primary_lemmas[row_id] = clean_lemma
        
        direct_cands = set()
        if clean_lemma_lower:
            direct_cands.add(clean_lemma_lower)
        for form in forms:
            c_form = tok.utf8_to_lower("".join(ch for ch in form if ch.isalnum() or ch in apo_set))
            if c_form:
                direct_cands.add(c_form)
                c_stem = re.sub(r"(?:n[" + "".join(re.escape(c) for c in apo_set) + r"]t|[" + "".join(re.escape(c) for c in apo_set) + r"](?:s|ve|ll|d|re|m)?)$", "", c_form, flags=re.IGNORECASE)
                if c_stem:
                    direct_cands.add(c_stem)
        row_direct_candidates[row_id] = direct_cands

        has_compound = any(_has_comp_marker(f) for f in forms)
        is_composite_row = bool(clean_lemma and _has_comp_marker(clean_lemma))
        if has_compound or is_composite_row:
            compound_rows.add(row_id)

        if has_compound or is_composite_row or not forms:
            vals_to_check = list(dict.fromkeys(forms + ([clean_lemma] if clean_lemma else [])))
        else:
            vals_to_check = forms
        
        for val in vals_to_check:
            if val:
                path_nodes = [n.strip() for n in re.split(r'[/\\]+', val) if n.strip()]
                if len(path_nodes) > 1:
                    for node in path_nodes:
                        node_subs = tok.decompose_identifier(node)
                        clean_node_subs = [tok.utf8_to_lower("".join(ch for ch in s if ch.isalnum() or ch in apo_set)) for s in node_subs]
                        clean_node = tok.utf8_to_lower("".join(ch for ch in node if ch.isalnum() or ch in apo_set))
                        node_matches = (not clean_lemma_lower or is_composite_row or clean_lemma_lower in clean_node or
                                        any(s == clean_lemma_lower or
                                            (len(clean_lemma_lower) >= 3 and s.startswith(clean_lemma_lower)) or
                                            (len(s) >= 3 and clean_lemma_lower.startswith(s)) or
                                            (len(clean_lemma_lower) >= 4 and len(s) >= 4 and s[:4] == clean_lemma_lower[:4])
                                            for s in clean_node_subs if s))
                        if node_matches:
                            _add_cand(clean_node)
                            for sub in node_subs:
                                clean_sub = tok.utf8_to_lower("".join(ch for ch in sub if ch.isalnum() or ch in apo_set))
                                sub_matches = (not clean_lemma_lower or
                                               is_composite_row or
                                               clean_sub == clean_lemma_lower or
                                               clean_lemma_lower == clean_node or
                                               (len(clean_lemma_lower) >= 3 and clean_sub.startswith(clean_lemma_lower)) or
                                               (len(clean_sub) >= 3 and clean_lemma_lower.startswith(clean_sub)) or
                                               (len(clean_lemma_lower) >= 4 and len(clean_sub) >= 4 and clean_sub[:4] == clean_lemma_lower[:4]))
                                if sub_matches:
                                    _add_cand(clean_sub)
                                    if not is_composite_row:
                                        direct_cands.add(clean_sub)
                                        clean_sub_stem = re.sub(r"(?:n[" + "".join(re.escape(c) for c in apo_set) + r"]t|[" + "".join(re.escape(c) for c in apo_set) + r"](?:s|ve|ll|d|re|m)?)$", "", clean_sub, flags=re.IGNORECASE)
                                        if clean_sub_stem and clean_sub_stem != clean_sub:
                                            direct_cands.add(clean_sub_stem)
                else:
                    clean_val = tok.utf8_to_lower("".join(ch for ch in val if ch.isalnum() or ch in apo_set))
                    _add_cand(clean_val)
                    clean_val_stem = re.sub(r"(?:n[" + "".join(re.escape(c) for c in apo_set) + r"]t|[" + "".join(re.escape(c) for c in apo_set) + r"](?:s|ve|ll|d|re|m)?)$", "", clean_val, flags=re.IGNORECASE)
                    if clean_val_stem and clean_val_stem != clean_val:
                        _add_cand(clean_val_stem)
                    subtokens = tok.decompose_identifier(val)
                    for sub in subtokens:
                        clean_sub = tok.utf8_to_lower("".join(ch for ch in sub if ch.isalnum() or ch in apo_set))
                        sub_matches = (not clean_lemma_lower or
                                       is_composite_row or
                                       clean_sub == clean_lemma_lower or
                                       clean_lemma_lower == clean_val or
                                       (len(clean_lemma_lower) >= 3 and clean_sub.startswith(clean_lemma_lower)) or
                                       (len(clean_sub) >= 3 and clean_lemma_lower.startswith(clean_sub)) or
                                       (len(clean_lemma_lower) >= 4 and len(clean_sub) >= 4 and clean_sub[:4] == clean_lemma_lower[:4]))
                        if sub_matches:
                            _add_cand(clean_sub)
                            if not is_composite_row:
                                direct_cands.add(clean_sub)
                                clean_sub_stem = re.sub(r"(?:n[" + "".join(re.escape(c) for c in apo_set) + r"]t|[" + "".join(re.escape(c) for c in apo_set) + r"](?:s|ve|ll|d|re|m)?)$", "", clean_sub, flags=re.IGNORECASE)
                                if clean_sub_stem and clean_sub_stem != clean_sub:
                                    direct_cands.add(clean_sub_stem)
                    if any(c.isspace() for c in val):
                        parts = re.findall(apo_regex, val.lower())
                        if len(parts) > 1:
                            for part in parts:
                                clean_part = tok.utf8_to_lower("".join(ch for ch in part if ch.isalnum() or ch in apo_set))
                                _add_cand(clean_part)
        row_candidates[row_id] = candidates
        for cand in candidates:
            if cand not in token_to_rows:
                token_to_rows[cand] = []
            if row_id not in token_to_rows[cand]:
                token_to_rows[cand].append(row_id)

            
    source_tokens = tok.build_word_list_internal(text, keep_spaces=True)
    source_word_cleans = [t["lower_clean"] for t in source_tokens if t.get("is_word") and "lower_clean" in t]

    COMPOUND_DELIMITERS = {'_', '-', '.', '/', '\\', ':', '#', '@'}
    n_tokens = len(source_tokens)
    i = 0
    while i < n_tokens:
        if source_tokens[i].get("is_word"):
            j = i
            chain = [j]
            while j + 2 < n_tokens:
                delim_tok = source_tokens[j + 1]
                next_word_tok = source_tokens[j + 2]
                if (not delim_tok.get("is_word") and 
                    delim_tok.get("text") and 
                    all(c in COMPOUND_DELIMITERS for c in delim_tok.get("text")) and
                    next_word_tok.get("is_word")):
                    chain.append(j + 2)
                    j += 2
                else:
                    break
            if len(chain) > 1:
                for idx in chain:
                    source_tokens[idx]["is_in_compound"] = True
                i = chain[-1] + 1
                continue
            elif source_tokens[i].get("compound_id") is not None:
                source_tokens[i]["is_in_compound"] = True
            else:
                source_tokens[i]["is_in_compound"] = False
        i += 1

    single_word_rows = set()
    anchored_positions = {}
    for row_id, row in enumerate(data_rows):
        inflected_val = resolve_row_inflected_form(row, col_inflected, col_inflected2, col_quotation, col_lemma)
        forms = [f.strip() for f in inflected_val.split(',')] if inflected_val else []
        
        has_single_word_form = False
        row_anchored_pos = set()
        
        for form in forms:
            if not form: continue
            inf_words = [tok.utf8_to_lower("".join(ch for ch in p if ch.isalnum() or ch in apo_set))
                         for p in re.findall(apo_regex, form)]
            inf_words = [w for w in inf_words if w]
            
            if len(inf_words) >= 2 and not any(ch in form for ch in SINGLE_WORD_DELIMITERS):
                pos_set, ok = resolve_anchored_positions(inf_words, source_word_cleans, split_gap_limit)
                if ok:
                    row_anchored_pos.update(pos_set)
            elif len(inf_words) == 1 or any(ch in form for ch in SINGLE_WORD_DELIMITERS):
                has_single_word_form = True
                
        if has_single_word_form or not forms:
            single_word_rows.add(row_id)
            
        anchored_positions[row_id] = row_anchored_pos

    paired_rows = {row_id for row_id, pos_set in anchored_positions.items() if pos_set}
    
    col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
    row_to_c_idx = {}
    if col_index != -1:
        for row_id, row in enumerate(data_rows):
            if len(row) > col_index:
                try:
                    row_to_c_idx[row_id] = int(row[col_index]) - 1
                except ValueError:
                    row_to_c_idx[row_id] = -1
                    
    absolute_to_c_idx = {}
    if eff_mode not in ('single', 'multi'):
        c_idx = 0
        for a_idx, ln in enumerate(text.splitlines()):
            if ln.strip():
                absolute_to_c_idx[a_idx] = c_idx
                c_idx += 1

    span_htmls = []
    word_counter = 0
    current_a_idx = 0
    for token in source_tokens:
        tok_text = token["text"]
        text_escaped = tok_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        if token["is_word"]:
            lower_clean = token.get("lower_clean", "")
            mapped_rows = token_to_rows.get(lower_clean, [])
            if not mapped_rows:
                stem = re.sub(r"(?:n[" + "".join(re.escape(c) for c in apo_set) + r"]t|[" + "".join(re.escape(c) for c in apo_set) + r"](?:s|ve|ll|d|re|m)?)$", "", lower_clean, flags=re.IGNORECASE)
                if stem and stem in token_to_rows:
                    mapped_rows = token_to_rows[stem]
            
            if eff_mode not in ('single', 'multi') and col_index != -1:
                curr_c_idx = absolute_to_c_idx.get(current_a_idx, -1)
                mapped_rows = [r_idx for r_idx in mapped_rows if row_to_c_idx.get(r_idx, -1) == curr_c_idx]

            is_in_comp = token.get("is_in_compound", token.get("compound_id") is not None)
            filtered_cand_rows = []
            for r_idx in mapped_rows:
                if r_idx in compound_rows and not is_in_comp:
                    if lower_clean in row_direct_candidates.get(r_idx, set()):
                        filtered_cand_rows.append(r_idx)
                else:
                    filtered_cand_rows.append(r_idx)
            mapped_rows = filtered_cand_rows
                
            token["filtered_mapped_rows"] = mapped_rows
            
            classes = ["word"]
            if mapped_rows:
                is_paired = any(word_counter in anchored_positions.get(r_idx, set()) for r_idx in mapped_rows)
                if is_paired:
                    classes.append("highlight-purple")
                elif any(r_idx in single_word_rows for r_idx in mapped_rows):
                    classes.append("highlight-orange")
                else:
                    classes.append("not-connected")
            else:
                classes.append("not-connected")
            classes_str = " ".join(classes)
            compound_id = token.get("compound_id")
            compound_attr = f' data-compound-id="{compound_id}"' if compound_id is not None else ""
            span_htmls.append(
                f'<span class="{classes_str}" data-word-idx="{token["visual_idx"]}" '
                f'data-line-idx="{current_a_idx}"{compound_attr} '
                f'data-lower-clean="{lower_clean}">{text_escaped}</span>'
            )
            word_counter += 1
        else:
            if tok_text in ("\\N", "\\n"):
                span_htmls.append("<br>")
                current_a_idx += 1
            elif "\n" in tok_text or "\r" in tok_text:
                normalized = tok_text.replace("\r\n", "\n").replace("\r", "\n")
                parts = normalized.split("\n")
                current_a_idx += len(parts) - 1
                span_htmls.append("<br>".join(parts))
            else:
                span_htmls.append(text_escaped)
                
    source_html = "" if is_mismatch else "".join(span_htmls)
    
    if is_mismatch:
        sentence_html = ""
    else:
        has_real_text = any(t and str(t).strip() for t in sentence_translations.values())
        if is_progressive and run_text == 'auto' and not has_real_text:
            sentence_html = '<div class="skeleton-loader" data-pending="true" style="width: 100%; max-width: 500px;"></div>'
        else:
            sentence_html = format_translated_html(sentence_translations, text_mode=text_mode, text=text, config=config)
    
    col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else -1
    col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else -1

    header_cols = ["Inflected", "Lemma", "Translation", "IPA", "Morphology"]
    
    dynamic_roles = []
    desk_classification_enabled = config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=True) if config.has_section(SEC_CLASSIFICATION) else True
    
    if desk_classification_enabled and kw_config is not None and hasattr(kw_config, 'has_section') and kw_config.has_section(SEC_CLASSIFICATION) and kw_config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=False):
        if kw_config.has_option(SEC_CLASSIFICATION, f'dictionaries_{language}'):
            dicts = kw_config.get(SEC_CLASSIFICATION, f'dictionaries_{language}', fallback='')
        else:
            dicts = kw_config.get(SEC_CLASSIFICATION, 'dictionaries', fallback='')
        if dicts:
            for d in dicts.split(','):
                d = d.strip()
                if not d: continue
                if '=' in d:
                    name, _ = d.split('=', 1)
                    name = name.strip()
                    if name not in dynamic_roles:
                        dynamic_roles.append(name)
                        header_cols.append(name.capitalize())

    dynamic_cols_indices = []
    for role in dynamic_roles:
        c_idx = headers.index(role_fields[role]) if role in role_fields and role_fields[role] in headers else -1
        dynamic_cols_indices.append(c_idx)

    th_elements = []
    for h in header_cols:
        h_lower = h.lower()
        if h_lower in [r.lower() for r in dynamic_roles]:
            th_elements.append(f'<th class="col-classification">{h}</th>')
        elif h_lower == "translation":
            th_elements.append(f'<th class="col-translation">{h}</th>')
        elif h_lower == "morphology":
            th_elements.append(f'<th class="col-morphology">{h}</th>')
        else:
            th_elements.append(f'<th>{h}</th>')
    table_header_html = "<tr>" + "".join(th_elements) + "</tr>"

    # Extract resolved column names and editable styling once outside the loop for orthogonal behavior and safety when data_rows is empty
    lemma_col_name = role_fields.get('lemma', 'WordSource')
    trans_col_name = role_fields.get('word_translation', 'WordDestination')
    inflected_col_name = role_fields.get('inflected', 'WordSourceInflectedForm')
    ipa_col_name = role_fields.get('ipa', 'WordSourceIPA')
    morph_col_name = role_fields.get('morphology', 'WordSourceMorphologyAI')
    selected_col_name = role_fields.get('selected', 'DeskSelected')

    editable_cols = mapping.get('desk_editable', 'editable_columns', fallback='')
    lemma_class = "editable" if lemma_col_name in editable_cols else ""
    trans_class = "editable" if trans_col_name in editable_cols else ""
    inflected_class = "editable" if inflected_col_name in editable_cols else ""

    table_rows = []
    for row_id, row in enumerate(data_rows):
        if col_lemma != -1 and len(row) > col_lemma and re.match(r'^\d{14}-', row[col_lemma]):
            row = list(row)
            row[col_lemma] = re.sub(r'^\d{14}-', '', row[col_lemma])
            data_rows[row_id] = row
        lemma_val = row[col_lemma] if col_lemma != -1 and len(row) > col_lemma else ""
        inflected_val = resolve_row_inflected_form(row, col_inflected, col_inflected2, col_quotation, col_lemma)
        trans_val = row[col_word_dest] if col_word_dest != -1 and len(row) > col_word_dest else ""
        if trans_val == "[FAILED]":
            trans_val = ""
        morph_val = row[col_morph] if col_morph != -1 and len(row) > col_morph else ""
        ipa_val = row[col_ipa] if col_ipa != -1 and len(row) > col_ipa else ""
        
        # Skeleton loaders for cells pending when a configured provider is expected to fill them:
        if run_base == 'auto' and not trans_val.strip() and has_untranslated_lemmas:
            trans_val = '<span class="skeleton-loader" style="width: 60px;"></span>'
        if run_enrich == 'auto' and enrich_provider == 'intellifiller' and not ipa_val.strip() and not llm_filled:
            ipa_val = '<span class="skeleton-loader" style="width: 50px;"></span>'
        if run_enrich == 'auto' and enrich_provider == 'intellifiller' and not morph_val.strip() and not llm_filled:
            morph_val = '<span class="skeleton-loader" style="width: 80px;"></span>'
        
        row_highlight_class = "highlight-purple" if (row_id in paired_rows) else "highlight-orange"
        
        is_selected = "0"
        if col_highlighted != -1:
            highlighted_val = row[col_highlighted] if len(row) > col_highlighted else ""
            if highlighted_val.strip().lower() in ["1", "true"]:
                is_selected = "1"

        token_order_val = row[col_token_order] if col_token_order != -1 and len(row) > col_token_order and row[col_token_order].strip() else str(row_id)
        sent_idx_val = row[col_index] if col_index != -1 and len(row) > col_index and row[col_index].strip().isdigit() else "1"

        dynamic_tds = ""
        for role, d_idx in zip(dynamic_roles, dynamic_cols_indices):
            val = row[d_idx] if d_idx != -1 and len(row) > d_idx else ""
            display_val = val
            span_class = ""
            if ":" in val:
                parts = val.split(":", 1)
                possible_prefix = parts[0].strip()
                if len(possible_prefix) <= 5 and "/" not in possible_prefix and "\\" not in possible_prefix:
                    display_val = parts[1].strip()
                    span_class = f"level-{possible_prefix.lower()}"
            
            if span_class:
                inner_html = f'<span class="{span_class}">{display_val}</span>'
            else:
                inner_html = display_val
            dynamic_tds += f'<td class="col-classification" data-col="{role}"><div class="scrollable-cell">{inner_html}</div></td>'

        table_rows.append(
            f'<tr data-row-id="{row_id}" data-token-order="{token_order_val}" data-sentence-idx="{sent_idx_val}" data-selected="{is_selected}" class="{row_highlight_class}">'
            f'<td class="{inflected_class}" data-col="{inflected_col_name}"><div class="scrollable-cell">{inflected_val}</div></td>'
            f'<td class="{lemma_class}" data-col="{lemma_col_name}"><div class="scrollable-cell">{lemma_val}</div></td>'
            f'<td class="{trans_class} col-translation" data-col="{trans_col_name}"><div class="scrollable-cell">{trans_val}</div></td>'
            f'<td data-col="{ipa_col_name}"><div class="scrollable-cell">{ipa_val}</div></td>'
            f'<td class="col-morphology" data-col="{morph_col_name}"><div class="scrollable-cell">{morph_val}</div></td>'
            f'{dynamic_tds}'
            f'</tr>'
        )
    table_rows_html = "" if is_mismatch else "\n".join(table_rows)
    
    token_manifest = []
    word_counter = 0
    token_mappings = get_desk_token_mappings(resolved_paths, language, config) if token_cfg.token_mappings_enabled else {}
    for token in source_tokens:
        tok_data = {
            "text": token["text"],
            "is_word": token["is_word"],
            "visual_idx": token["visual_idx"]
        }
        if token["is_word"] and "lower_clean" in token:
            lower_clean = token["lower_clean"]
            tok_data["lower_clean"] = lower_clean
            mapped_rows = token.get("filtered_mapped_rows", token_to_rows.get(lower_clean, []))
            if not mapped_rows:
                stem = re.sub(r"(?:n[" + "".join(re.escape(c) for c in apo_set) + r"]t|[" + "".join(re.escape(c) for c in apo_set) + r"](?:s|ve|ll|d|re|m)?)$", "", lower_clean, flags=re.IGNORECASE)
                if stem and stem in token_to_rows:
                    mapped_rows = token_to_rows[stem]
            is_in_comp = token.get("is_in_compound", token.get("compound_id") is not None)
            filtered_cand_rows = []
            for r_idx in mapped_rows:
                if r_idx in compound_rows and not is_in_comp:
                    if lower_clean in row_direct_candidates.get(r_idx, set()):
                        filtered_cand_rows.append(r_idx)
                else:
                    filtered_cand_rows.append(r_idx)
            mapped_rows = filtered_cand_rows
            
            filtered_rows = []
            for r_idx in mapped_rows:
                if r_idx in single_word_rows:
                    filtered_rows.append(r_idx)
                elif word_counter in anchored_positions.get(r_idx, set()):
                    filtered_rows.append(r_idx)
            if filtered_rows:
                if token_mappings and lower_clean:
                    norm_tok = lower_clean.replace('’', "'").replace('‘', "'").replace('`', "'").replace('´', "'").replace('ʼ', "'")
                    norm_tok = re.sub(r'\s+', '', norm_tok).lower()
                    if norm_tok in token_mappings:
                        targets = [t.lower().strip() for t in token_mappings[norm_tok] if t.strip()]
                        if targets:
                            def _row_rank(r_idx):
                                if 0 <= r_idx < len(data_rows):
                                    r = data_rows[r_idx]
                                    lem = r[col_lemma].strip().lower() if col_lemma != -1 and len(r) > col_lemma else ""
                                    inf = resolve_row_inflected_form(r, col_inflected, col_inflected2, col_quotation, col_lemma).lower()
                                    forms = [f.strip() for f in inf.split(',') if f.strip()]
                                    r_words = set(forms)
                                    if lem:
                                        r_words.add(lem)
                                    for f in forms:
                                        for part in f.split():
                                            r_words.add(part)
                                    for idx, target in enumerate(targets):
                                        if target in r_words:
                                            return idx
                                return len(targets)
                            filtered_rows = sorted(filtered_rows, key=_row_rank)
                tok_data["row_ids"] = filtered_rows
                
                atomic_rows = []
                compound_cand_rows = []
                for r_idx in filtered_rows:
                    if r_idx in compound_rows:
                        if lower_clean in row_direct_candidates.get(r_idx, set()) and not _has_comp_marker(row_primary_lemmas.get(r_idx)):
                            atomic_rows.append(r_idx)
                        else:
                            compound_cand_rows.append(r_idx)
                    else:
                        atomic_rows.append(r_idx)
                tok_data["atomic_row_ids"] = atomic_rows
                tok_data["compound_row_ids"] = compound_cand_rows
            word_counter += 1
        token_manifest.append(tok_data)
        
    if is_mismatch:
        token_manifest = []
        
    _html_gen_timer = TraceTimer("html_generation", zid, config, resolved_paths)
    _html_gen_timer.__enter__()
    
    html_page = """<!DOCTYPE html>
<!-- saved from url=(0014)about:internet -->
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{page_title}</title>
<link rel="icon" type="image/x-icon" href="{favicon_href}">
<style id="hl-mvp-style">
  
 

  *, *:before, *:after {
    -webkit-box-sizing: border-box;
    -moz-box-sizing: border-box;
    box-sizing: border-box;
  }
  /* For standard Webkit/Blink browsers */
  ::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  ::-webkit-scrollbar-track {
    background: {scrollbar_track};
  }
  ::-webkit-scrollbar-thumb {
    background: {scrollbar_thumb};
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: {scrollbar_thumb_hover};
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background-color: {bg_color};
    color: {text_color};
    margin: 0;
    padding: 0;
    padding-bottom: 60px;
    font-size: 14px;
    line-height: 1.5;
    zoom: {zoom_level};
    width: {inverse_zoom_width};
    /* For IE11 / Shell.Explorer emulation scrollbar styling */
    scrollbar-face-color: {scrollbar_thumb};
    scrollbar-track-color: {scrollbar_track};
    scrollbar-arrow-color: {text_muted};
    scrollbar-shadow-color: {scrollbar_track};
    scrollbar-highlight-color: {scrollbar_track};
    scrollbar-3dlight-color: {scrollbar_track};
    scrollbar-darkshadow-color: {scrollbar_track};
    scrollbar-base-color: {scrollbar_track};
  }
  .container {
    padding: 16px;
    padding-bottom: 70px;
    display: inline-block;
    min-width: 100%;
  }
  .section {
    background: {section_bg};
    border: 1px solid {section_border};
    border-radius: 2px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }
  .section-title {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: {text_muted};
    margin-bottom: 8px;
    font-weight: 600;
  }
  .source-text {
    font-size: 16px;
    color: {text_color};
    line-height: 1.6;
    word-break: break-word;
    white-space: {source_white_space};
    -moz-user-select: none;
    -webkit-user-select: none;
    -ms-user-select: none;
    user-select: none;
  }
  .source-text span.word,
  #source-container span.word,
  #translation-container span.word {
    cursor: pointer;
    transition: background-color 0.2s, color 0.2s;
    border-radius: 3px;
    padding: 0 2px;
  }
  .source-text span.word.flipped {
    background-color: {flipped_bg};
    color: {flipped_text};
    font-weight: 300;
    border: 1px dashed {flipped_border};
    padding: 0 3px;
    margin: 0 -1px;
    border-radius: 4px;
  }
  .source-text span.word:hover {
    background-color: {word_hover};
  }
  body.theme-dark #translation-container span.word:hover {
    background-color: rgba(255, 255, 255, 0.1);
    border-radius: 4px;
  }
  body.theme-light #translation-container span.word:hover,
  body.theme-white #translation-container span.word:hover {
    background-color: rgba(0, 0, 0, 0.06);
    border-radius: 4px;
  }
  body.theme-dark #source-container span.word.hl-mvp-pin,
  body.theme-dark #translation-container span.word.hl-mvp-pin,
  body.theme-dark #source-container span.word.hl-mvp-hover,
  body.theme-dark #translation-container span.word.hl-mvp-hover,
  body.theme-white #source-container span.word.hl-mvp-pin,
  body.theme-white #translation-container span.word.hl-mvp-pin,
  body.theme-white #source-container span.word.hl-mvp-hover,
  body.theme-white #translation-container span.word.hl-mvp-hover {
    border: 2px solid #39c5ff !important;
    border-radius: 4px !important;
    margin: -2px !important;
  }
  body.theme-light #source-container span.word.hl-mvp-pin,
  body.theme-light #translation-container span.word.hl-mvp-pin,
  body.theme-light #source-container span.word.hl-mvp-hover,
  body.theme-light #translation-container span.word.hl-mvp-hover {
    border: 2px solid #0969da !important;
    border-radius: 4px !important;
    margin: -2px !important;
  }
  body.theme-dark #source-container span.word.hl-mvp-pin-0,
  body.theme-dark #translation-container span.word.hl-mvp-pin-0 { border: 2px solid #39d353 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-1,
  body.theme-dark #translation-container span.word.hl-mvp-pin-1 { border: 2px solid #b78cf7 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-2,
  body.theme-dark #translation-container span.word.hl-mvp-pin-2 { border: 2px solid #ff9c3a !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-3,
  body.theme-dark #translation-container span.word.hl-mvp-pin-3 { border: 2px solid #ff79c6 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-4,
  body.theme-dark #translation-container span.word.hl-mvp-pin-4 { border: 2px solid #f2ca30 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-5,
  body.theme-dark #translation-container span.word.hl-mvp-pin-5 { border: 2px solid #39c5ff !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-6,
  body.theme-dark #translation-container span.word.hl-mvp-pin-6 { border: 2px solid #ff7b72 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-dark #source-container span.word.hl-mvp-pin-7,
  body.theme-dark #translation-container span.word.hl-mvp-pin-7 { border: 2px solid #a5b4fc !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-0,
  body.theme-light #translation-container span.word.hl-mvp-pin-0,
  body.theme-white #source-container span.word.hl-mvp-pin-0,
  body.theme-white #translation-container span.word.hl-mvp-pin-0 { border: 2px solid #1a7f37 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-1,
  body.theme-light #translation-container span.word.hl-mvp-pin-1,
  body.theme-white #source-container span.word.hl-mvp-pin-1,
  body.theme-white #translation-container span.word.hl-mvp-pin-1 { border: 2px solid #8250df !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-2,
  body.theme-light #translation-container span.word.hl-mvp-pin-2,
  body.theme-white #source-container span.word.hl-mvp-pin-2,
  body.theme-white #translation-container span.word.hl-mvp-pin-2 { border: 2px solid #bc4c00 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-3,
  body.theme-light #translation-container span.word.hl-mvp-pin-3,
  body.theme-white #source-container span.word.hl-mvp-pin-3,
  body.theme-white #translation-container span.word.hl-mvp-pin-3 { border: 2px solid #cf222e !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-4,
  body.theme-light #translation-container span.word.hl-mvp-pin-4,
  body.theme-white #source-container span.word.hl-mvp-pin-4,
  body.theme-white #translation-container span.word.hl-mvp-pin-4 { border: 2px solid #b08800 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-5,
  body.theme-light #translation-container span.word.hl-mvp-pin-5,
  body.theme-white #source-container span.word.hl-mvp-pin-5,
  body.theme-white #translation-container span.word.hl-mvp-pin-5 { border: 2px solid #0891b2 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-6,
  body.theme-light #translation-container span.word.hl-mvp-pin-6,
  body.theme-white #source-container span.word.hl-mvp-pin-6,
  body.theme-white #translation-container span.word.hl-mvp-pin-6 { border: 2px solid #e11d48 !important; border-radius: 4px !important; margin: -2px !important; }
  body.theme-light #source-container span.word.hl-mvp-pin-7,
  body.theme-light #translation-container span.word.hl-mvp-pin-7,
  body.theme-white #source-container span.word.hl-mvp-pin-7,
  body.theme-white #translation-container span.word.hl-mvp-pin-7 { border: 2px solid #7c3aed !important; border-radius: 4px !important; margin: -2px !important; }
  body.text-selection-mode-active,
  body.text-selection-mode-active * {
    cursor: text !important;
  }
  body.text-selection-mode-active .source-text,
  body.text-selection-mode-active .source-text *,
  body.text-selection-mode-active #source-container,
  body.text-selection-mode-active #source-container *,
  body.text-selection-mode-active #translation-container,
  body.text-selection-mode-active #translation-container *,
  body.text-selection-mode-active #lemma-table,
  body.text-selection-mode-active #lemma-table *,
  body.text-selection-mode-active #lemma-table td,
  body.text-selection-mode-active #lemma-table th {
    -webkit-user-select: text !important;
    -moz-user-select: text !important;
    -ms-user-select: text !important;
    user-select: text !important;
    cursor: text !important;
  }
  body.text-selection-mode-active #lemma-table tr:hover td {
    background: transparent !important;
  }
  body.text-selection-mode-active #lemma-table tr.selected td,
  body.text-selection-mode-active #lemma-table tr.kw-row-selected td {
    background: transparent !important;
    color: inherit !important;
  }
  body.text-selection-mode-active span.word,
  body.text-selection-mode-active span.token,
  body.text-selection-mode-active #source-container span.word,
  body.text-selection-mode-active #translation-container span.word,
  body.text-selection-mode-active #source-container span.token,
  body.text-selection-mode-active #translation-container span.token,
  body.text-selection-mode-active span.flipped,
  body.text-selection-mode-active span.highlight-orange-active,
  body.text-selection-mode-active span.highlight-purple-active,
  body.text-selection-mode-active span.hl-mvp-pin,
  body.text-selection-mode-active span.hl-mvp-hover,
  body.text-selection-mode-active span[class*="hl-mvp-"] {
    cursor: text !important;
    background-color: transparent !important;
    background: transparent !important;
    color: inherit !important;
    border: none !important;
    outline: none !important;
    padding: 0 !important;
    margin: 0 !important;
    text-decoration: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
  }
  body.text-selection-mode-active span.word:hover,
  body.text-selection-mode-active #source-container span.word:hover,
  body.text-selection-mode-active #translation-container span.word:hover {
    background-color: transparent !important;
    border: none !important;
    outline: none !important;
  }
  .source-text span.highlight-orange {
  }
  .source-text span.highlight-purple {
  }
  .source-text span.not-connected {
    background-color: {not_connected_bg};
    color: {not_connected_text};
    cursor: default;
  }
  .source-text span.not-connected:hover {
    background-color: {not_connected_bg};
  }
  .source-text span.word.highlight-orange-active {
    background-color: {highlight_orange_active_bg} !important;
    color: {highlight_orange_active_text} !important;
    text-decoration: none;
    border-color: {highlight_orange_active_text} !important;
  }
  .source-text span.word.highlight-orange-active:hover {
    background-color: {highlight_orange_active_hover_bg} !important;
    color: {highlight_orange_active_text} !important;
  }
  .source-text span.word.highlight-purple-active {
    background-color: {highlight_purple_active_bg} !important;
    color: {highlight_purple_active_text} !important;
    text-decoration: none;
    border-color: {highlight_purple_active_text} !important;
  }
  .source-text span.word.highlight-purple-active:hover {
    background-color: {highlight_purple_active_hover_bg} !important;
    color: {highlight_purple_active_text} !important;
  }
  .source-text span.word.active-subtoken {
    outline: 1.5px solid #2ea043 !important;
  }
  .translation-text {
    font-size: 16px;
    color: {text_color};
    line-height: 1.6;
    word-break: break-word;
    white-space: {source_white_space};
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 8px;
    table-layout: auto;
  }
  #lemma-table th, #lemma-table td {
    width: 1%;
    white-space: nowrap;
    padding-right: 24px;
  }
  #lemma-table th.col-morphology, #lemma-table td.col-morphology {
    width: auto;
  }
  #lemma-table th:last-child, #lemma-table td:last-child {
    width: auto;
    padding-right: 12px;
  }
  #lemma-table th.col-classification:last-child, #lemma-table td.col-classification:last-child {
    width: 1%;
    padding-right: 12px;
  }
  .scrollable-cell {
    width: 100%;
    box-sizing: border-box;
    -ms-overflow-style: none;  /* IE and Edge */
    scrollbar-width: none;  /* Firefox */
  }
  .scrollable-cell::-webkit-scrollbar {
    display: none; /* Chrome, Safari and Opera */
  }
  /* When the window is NOT maximized (normally sized) */
  body:not(.maximized) {
    max-width: 100vw;
    overflow-x: hidden;
  }
  body:not(.maximized) .container {
    display: block;
    width: 100%;
    max-width: 100%;
  }
  body:not(.maximized) .section {
    max-width: 100%;
  }
  body:not(.maximized) #lemma-table {
    table-layout: fixed;
    width: 100%;
  }
  body:not(.maximized) #lemma-table th,
  body:not(.maximized) #lemma-table td {
    width: 15%;
    padding-right: 12px;
  }
  body:not(.maximized) #lemma-table th.col-translation,
  body:not(.maximized) #lemma-table td.col-translation {
    width: 20%;
  }
  body:not(.maximized) #lemma-table th.col-morphology,
  body:not(.maximized) #lemma-table td.col-morphology {
    width: 26%;
  }
  body:not(.maximized) #lemma-table th.col-classification,
  body:not(.maximized) #lemma-table td.col-classification {
    width: 10%;
  }
  th.col-classification,
  td.col-classification {
    text-align: center !important;
    padding-left: 0px !important;
    padding-right: 0px !important;
  }
  body:not(.maximized) .scrollable-cell {
    overflow-x: auto;
    white-space: nowrap;
    max-width: 100%;
  }
  /* When maximized */
  body.maximized .scrollable-cell {
    overflow-x: visible;
    white-space: normal;
    max-width: none;
  }
  th {
    text-align: left;
    padding: 10px 12px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: {text_muted};
    border-bottom: 1px solid {table_th_border};
    font-weight: 600;
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid {table_border};
    color: {table_text};
    vertical-align: top;
  }
  tr:hover td {
    background: {row_hover};
  }
  tr.selected.highlight-orange td {
    background: {selected_orange_row_bg};
    color: {selected_orange_row_text};
  }
  tr.selected.highlight-purple td {
    background: {selected_purple_row_bg};
    color: {selected_purple_row_text};
  }
  .editable {
    cursor: pointer;
  }
  td.dirty {
    border-left: 3px solid #ff7b72;
  }
  .skeleton-loader {
    display: inline-block;
    height: 1.2em;
    width: 100%;
    background: linear-gradient(-90deg, {table_border} 0%, {table_th_border} 50%, {table_border} 100%);
    background-size: 400% 400%;
    animation: pulse-skeleton 1.5s ease infinite;
    border-radius: 4px;
    vertical-align: middle;
  }
  @keyframes pulse-skeleton {
    0% { background-position: 0% 50% }
    50% { background-position: 100% 50% }
    100% { background-position: 0% 50% }
  }
  .level-3k {
    color: {level_3k_color};
  }
  .level-5k {
    color: {level_5k_color};
  }
  td[data-col="goethe"] {
    color: {level_goethe_color};
  }
  /* Sticky Action Toolbar */
  .kw-action-toolbar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: {section_bg};
    border-top: 1px solid {section_border};
    padding: 8px 16px;
    display: flex;
    gap: 8px;
    justify-content: center;
    align-items: center;
    z-index: 1000;
    box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.1);
  }
  body.kw-ahk-native-host {
    padding-bottom: 0px !important;
  }
  body.kw-ahk-native-host #kw-action-toolbar {
    display: none !important;
  }
  body.kw-ahk-native-host .container {
    padding-bottom: 15px !important;
  }
  .kw-action-toolbar button {
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    min-width: 110px;
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid {section_border};
    background: {input_bg};
    color: {text_color};
    cursor: pointer;
    transition: background-color 0.15s, border-color 0.15s, opacity 0.15s;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    gap: 6px;
    user-select: none;
    -webkit-user-select: none;
  }
  .kw-action-toolbar button:hover:not(:disabled) {
    background: {row_hover};
    border-color: {text_muted};
  }
  .kw-action-toolbar button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .kw-action-toolbar button.btn-primary {
    background: #1f6feb;
    border-color: #388bfd;
    color: #ffffff;
  }
  .kw-action-toolbar button.btn-primary:hover:not(:disabled) {
    background: #388bfd;
  }
  .kw-action-toolbar button.btn-danger {
    color: #ff7b72;
  }
  .kw-action-toolbar button.btn-danger:hover:not(:disabled) {
    background: rgba(248, 81, 73, 0.15);
    border-color: #f85149;
  }
  .kw-action-toolbar button.active {
    background: {flipped_bg};
    border-color: {flipped_border};
    color: {flipped_text};
  }
  .kw-seq-badge {
    font-family: inherit;
    font-size: 13px;
    font-weight: 700;
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid {section_border};
    background: {input_bg};
    color: {text_muted};
    display: inline-flex;
    align-items: center;
    justify-content: center;
    user-select: none;
    -webkit-user-select: none;
  }
  body.theme-light .kw-seq-badge,
  body.theme-white .kw-seq-badge {
    background: #e1e4e8;
    color: #24292f;
    border: 1px solid #d0d7de;
  }
  /* In-Page Toast Notifications */
  .kw-toast-container {
    position: fixed;
    bottom: 60px;
    right: 20px;
    z-index: 2000;
    display: flex;
    flex-direction: column;
    gap: 8px;
    pointer-events: none;
  }
  .kw-toast {
    pointer-events: auto;
    min-width: 200px;
    max-width: 380px;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.4;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    display: flex;
    align-items: center;
    gap: 10px;
    animation: kwToastIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    transition: opacity 0.25s ease, transform 0.25s ease;
    cursor: pointer;
  }
  @keyframes kwToastIn {
    from { opacity: 0; transform: translateY(10px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }
  .kw-toast.kw-toast-hiding {
    opacity: 0;
    transform: translateY(10px) scale(0.95);
  }
  .kw-toast-info {
    background: #1f242c;
    color: #e3e6eb;
    border: 1px solid #388bfd;
  }
  .kw-toast-success {
    background: #1a2f23;
    color: #56d364;
    border: 1px solid #2ea043;
  }
  .kw-toast-warning {
    background: #332512;
    color: #e3b341;
    border: 1px solid #d29922;
  }
  .kw-toast-error {
    background: #341a1c;
    color: #f85149;
    border: 1px solid #da3633;
  }
  body.theme-light .kw-toast-info,
  body.theme-white .kw-toast-info {
    background: #ddf4ff;
    color: #0969da;
    border: 1px solid #54aeff;
  }
  body.theme-light .kw-toast-success,
  body.theme-white .kw-toast-success {
    background: #dafbe1;
    color: #1a7f37;
    border: 1px solid #4ac26b;
  }
  body.theme-light .kw-toast-warning,
  body.theme-white .kw-toast-warning {
    background: #fff8c5;
    color: #9a6700;
    border: 1px solid #d4a72c;
  }
  body.theme-light .kw-toast-error,
  body.theme-white .kw-toast-error {
    background: #ffebe9;
    color: #cf222e;
    border: 1px solid #ff8182;
  }
  /* Language Verification Modal */
  .kw-modal-backdrop {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 3000;
    backdrop-filter: blur(2px);
  }
  .kw-modal-backdrop.hidden,
  .kw-modal-backdrop[style*="display: none"] {
    display: none !important;
  }
  .kw-modal-box {
    background: {modal_bg};
    border: 1px solid {modal_border};
    border-radius: 8px;
    width: 90%;
    max-width: 480px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45);
    padding: 22px 24px;
    color: {text_color};
    font-family: inherit;
    box-sizing: border-box;
    animation: kwModalIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  }
  @keyframes kwModalIn {
    from { opacity: 0; transform: scale(0.95) translateY(-8px); }
    to { opacity: 1; transform: scale(1) translateY(0); }
  }
  .kw-modal-header {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 14px;
    color: {text_color};
  }
  .kw-modal-body {
    font-size: 13.5px;
    line-height: 1.6;
    margin-bottom: 22px;
    color: {text_color};
    white-space: pre-wrap;
    word-break: break-word;
  }
  .kw-modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .kw-modal-actions button {
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    min-width: 75px;
    height: 32px;
    padding: 4px 16px;
    border-radius: 4px;
    border: 1px solid {section_border};
    background: {input_bg};
    color: {text_color};
    cursor: pointer;
    transition: background-color 0.15s, border-color 0.15s;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    user-select: none;
    -webkit-user-select: none;
  }
  .kw-modal-actions button:hover:not(:disabled) {
    background: {row_hover};
    border-color: {text_muted};
  }
  .kw-modal-actions button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .kw-modal-actions button.btn-primary {
    background: #1f6feb;
    border-color: #388bfd;
    color: #ffffff;
  }
  .kw-modal-actions button.btn-primary:hover:not(:disabled) {
    background: #388bfd;
  }
  body.theme-light .kw-modal-box,
  body.theme-white .kw-modal-box {
    background: #ffffff;
    border-color: #d0d7de;
    color: #24292f;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
  }
  body.theme-light .kw-modal-header,
  body.theme-white .kw-modal-header,
  body.theme-light .kw-modal-body,
  body.theme-white .kw-modal-body {
    color: #24292f;
  }
  body.theme-light .kw-modal-actions button,
  body.theme-white .kw-modal-actions button {
    background: #f6f8fa;
    border-color: #d0d7de;
    color: #24292f;
  }
  body.theme-light .kw-modal-actions button:hover:not(:disabled),
  body.theme-white .kw-modal-actions button:hover:not(:disabled) {
    background: #eaeef2;
    border-color: #afb8c1;
  }
  body.theme-light .kw-modal-actions button.btn-primary,
  body.theme-white .kw-modal-actions button.btn-primary {
    background: #1f6feb;
    border-color: #388bfd;
    color: #ffffff;
  }
  body.theme-light .kw-modal-actions button.btn-primary:hover:not(:disabled),
  body.theme-white .kw-modal-actions button.btn-primary:hover:not(:disabled) {
    background: #388bfd;
  }
</style>
</head>
<body class="{theme_class}">
<div class="container">
  <div class="section">
    <div class="source-text" id="source-container">{source_html}</div>
  </div>
  
  <div class="section">
    <div class="translation-text" id="translation-container">{sentence_html}</div>
  </div>
  
  <div class="section">
    <table id="lemma-table">
      <thead>
        {table_header_html}
      </thead>
      <tbody>
        {table_rows_html}
      </tbody>
    </table>
  </div>
</div>
<div class="kw-action-toolbar" id="kw-action-toolbar">
  {seq_badge_html}
  <button type="button" id="kw-btn-save" class="btn-primary" disabled title="Save changes (Ctrl+S)">Save (Ctrl+S)</button>
  <button type="button" id="kw-btn-update" title="Update / Re-render view (F5)">Update</button>
  <button type="button" id="kw-btn-retext" title="Re-translate text">Re-text</button>
  <button type="button" id="kw-btn-reword" title="Re-process selected words">Re-word</button>
  <button type="button" id="kw-btn-export" title="Send selected rows to Anki">Send to Anki</button>
  <button type="button" id="kw-btn-hand-tool" title="Toggle Text Selection / Hand Tool (Ctrl+Shift+A)">Hand Tool</button>
  <button type="button" id="kw-btn-delete" class="btn-danger" title="Delete selected rows (Delete)">Delete</button>
</div>
<div class="kw-modal-backdrop" id="kw-lang-modal" style="{lang_modal_display}">
  <div class="kw-modal-box">
    <div class="kw-modal-header" id="kw-lang-modal-title">Language Verification</div>
    <div class="kw-modal-body" id="kw-lang-modal-body">{lang_mismatch_body}</div>
    <div class="kw-modal-actions">
      <button type="button" id="kw-btn-lang-yes" class="btn-primary">Yes</button>
      <button type="button" id="kw-btn-lang-no">No</button>
      <button type="button" id="kw-btn-lang-cancel">Cancel</button>
    </div>
  </div>
</div>
<div class="kw-toast-container" id="kw-toast-container"></div>
<script id="mismatch-info" type="application/json">
{mismatch_info_json}
</script>
<script id="token-map" type="application/json">
{token_manifest}
</script>
<script id="tsv-path" type="text/plain">{working_tsv_path}</script>
<script id="llm-filled" type="text/plain">{llm_filled_js}</script>
<script id="session-zid" type="text/plain">{zid}</script>
<script id="session-lang" type="text/plain">{language}</script>
<script id="session-target-lang" type="text/plain">{target_language}</script>
<script id="display-mode" type="text/plain">{display_mode_js}</script>
<script id="text-mode" type="text/plain">{text_mode_js}</script>
<script id="auto-inject-updates" type="text/plain">{auto_inject_updates_js}</script>
<script id="run-enrichment" type="text/plain">{run_enrichment_js}</script>
<script id="worker-launched" type="text/plain">{worker_launched_js}</script>
<script id="hl-mvp-script" type="text/plain" data-bookmarks="{hover_highlight_bookmarks}" data-rainbow="{hover_highlight_rainbow}" data-enabled="{hover_highlight_enabled}"></script>


<script type="text/javascript">
(function() {
    function isNativeAhkHost() {
        return window.location.protocol === 'file:' ||
               typeof window.ahkCall !== 'undefined' ||
               (window.external && typeof window.external.ahkCall !== 'undefined') ||
               !!(document.documentMode || window.ActiveXObject);
    }
    function checkAhkHost() {
        if (isNativeAhkHost()) {
            if (document.body && document.body.classList) {
                document.body.classList.add('kw-ahk-native-host');
            }
        }
    }
    checkAhkHost();

    function addEvent(el, type, fn) {
        if (el.addEventListener) {
            el.addEventListener(type, fn, false);
        } else if (el.attachEvent) {
            el.attachEvent('on' + type, fn);
        } else {
            el['on' + type] = fn;
        }
    }
    
    window.forceRepaint = function() {
        if (document.body) {
            var _reflow = document.body.offsetHeight;
        }
    };

    window.__mvpInitialized = true;

    function removeClass(el, name) {
        try { if (el && el.classList) el.classList.remove(name); } catch(e) {}
    }
    function addClass(el, name) {
        try { if (el && el.classList) el.classList.add(name); } catch(e) {}
    }
    function isAttached(el) {
        while (el) {
            if (el === document.documentElement) return true;
            el = el.parentNode;
        }
        return false;
    }
    function escapeHtml(str) {
        if (!str) return '';
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    var mvpBookmarks = [];
    var sourceSpansArray = [];
    var transSpansArray = [];
    var mvpN = 3;
    var isMvpIndexBuilt = false;
    var mvpRainbowMode = true;
    var mvpHighlightEnabled = true;

    var scriptEl = document.getElementById('hl-mvp-script');
    if (scriptEl) {
        var dr = scriptEl.getAttribute('data-rainbow');
        if (dr !== null) mvpRainbowMode = (dr === '1' || dr === 'true');
        var db = scriptEl.getAttribute('data-bookmarks');
        if (db) mvpN = parseInt(db, 10);
        var de = scriptEl.getAttribute('data-enabled');
        if (de !== null) mvpHighlightEnabled = (de === '1' || de === 'true');
    }
    if (window.__mvpBookmarks) mvpN = parseInt(window.__mvpBookmarks, 10);
    if (isNaN(mvpN) || mvpN < 1) mvpN = 3;

    function refreshBookmarkClasses() {
        for (var i = 0; i < sourceSpansArray.length; i++) {
            var span = sourceSpansArray[i];
            removeClass(span, 'hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                removeClass(span, 'hl-mvp-pin-' + k);
            }
        }
        for (var j = 0; j < transSpansArray.length; j++) {
            var span = transSpansArray[j];
            removeClass(span, 'hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                removeClass(span, 'hl-mvp-pin-' + k);
            }
        }
        for (var m = 0; m < mvpBookmarks.length; m++) {
            var b = mvpBookmarks[m];
            if (b.srcSpan) {
                addClass(b.srcSpan, 'hl-mvp-pin');
                if (mvpRainbowMode && b.slot !== undefined) {
                    addClass(b.srcSpan, 'hl-mvp-pin-' + b.slot);
                }
            }
            if (b.transSpan) {
                addClass(b.transSpan, 'hl-mvp-pin');
                if (mvpRainbowMode && b.slot !== undefined) {
                    addClass(b.transSpan, 'hl-mvp-pin-' + b.slot);
                }
            }
        }
    }

    function tokenizeText(text) {
        var rx = /([a-zA-Z0-9'\u00C0-\u024F\u0400-\u04FF\u0500-\u052F\u1E00-\u1EFF]+)/g;
        return text.split(rx);
    }

    function tokenizeTranslation() {
        var tc = document.getElementById('translation-container');
        if (!tc) return;
        if (tc.querySelector('[data-pending="true"]') || tc.querySelector('.skeleton-loader') || tc.classList.contains('skeleton-loader')) {
            return;
        }
        var divs = tc.getElementsByTagName('div');
        if (divs.length === 0) {
            var firstChild = tc.firstChild;
            if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                if (!firstChild.classList.contains('hl-mvp')) {
                    var childSpans = tc.getElementsByTagName('span');
                    for (var j = 0; j < childSpans.length; j++) {
                        var span = childSpans[j];
                        if (span.classList && span.classList.contains('word')) {
                            addClass(span, 'hl-mvp');
                            span.setAttribute('data-line-idx', '0');
                        }
                    }
                }
            } else {
                var text = tc.textContent || tc.innerText || '';
                var parts = tokenizeText(text);
                var html = '';
                for (var k = 0; k < parts.length; k++) {
                    var part = parts[k];
                    if (!part) continue;
                    if (k % 2 === 1) {
                        var lc = part.toLowerCase();
                        html += '<span class="word hl-mvp" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="0">' + escapeHtml(part) + '</span>';
                    } else {
                        html += escapeHtml(part);
                    }
                }
                tc.innerHTML = html;
            }
            return;
        }
        for (var i = 0; i < divs.length; i++) {
            var div = divs[i];
            var firstChild = div.firstChild;
            if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                if (firstChild.classList.contains('hl-mvp')) continue;
                var childSpans = div.getElementsByTagName('span');
                for (var j = 0; j < childSpans.length; j++) {
                    var span = childSpans[j];
                    if (span.classList && span.classList.contains('word')) {
                        addClass(span, 'hl-mvp');
                        span.setAttribute('data-line-idx', String(i));
                    }
                }
            } else {
                var text = div.textContent || div.innerText || '';
                var parts = tokenizeText(text);
                var html = '';
                for (var k = 0; k < parts.length; k++) {
                    var part = parts[k];
                    if (!part) continue;
                    if (k % 2 === 1) {
                        var lc = part.toLowerCase();
                        html += '<span class="word hl-mvp" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="' + i + '">' + escapeHtml(part) + '</span>';
                    } else {
                        html += escapeHtml(part);
                    }
                }
                div.innerHTML = html;
            }
        }
    }

    function buildLcIndex() {
        sourceSpansArray = [];
        transSpansArray = [];
        var sc = document.getElementById('source-container');
        if (sc) {
            var srcSpans = sc.getElementsByTagName('span');
            if (srcSpans.length > 0) isMvpIndexBuilt = true;
            for (var i = 0; i < srcSpans.length; i++) {
                var span = srcSpans[i];
                if (span.classList && span.classList.contains('word')) {
                    span.setAttribute('data-mvp-idx', String(sourceSpansArray.length));
                    span.setAttribute('data-mvp-type', 'source');
                    sourceSpansArray.push(span);
                }
            }
        }
        var tc = document.getElementById('translation-container');
        if (tc) {
            var transSpans = tc.getElementsByTagName('span');
            for (var j = 0; j < transSpans.length; j++) {
                var span = transSpans[j];
                if (span.classList && span.classList.contains('hl-mvp')) {
                    span.setAttribute('data-mvp-idx', String(transSpansArray.length));
                    span.setAttribute('data-mvp-type', 'trans');
                    transSpansArray.push(span);
                }
            }
        }
    }

    function getTargetIdx(idx, isSource) {
        var sourceArray = isSource ? sourceSpansArray : transSpansArray;
        var targetArray = isSource ? transSpansArray : sourceSpansArray;
        var span = sourceArray[idx];
        if (!span) return -1;
        var lineIdx = span.getAttribute('data-line-idx');
        if (!lineIdx) {
            lineIdx = "0";
        }
        var sourceLineSpans = [];
        var sourcePos = 0;
        for (var i = 0; i < sourceArray.length; i++) {
            var sLine = sourceArray[i].getAttribute('data-line-idx') || "0";
            if (sLine === lineIdx) {
                sourceLineSpans.push(i);
                if (i === idx) sourcePos = sourceLineSpans.length - 1;
            }
        }
        var targetLineSpans = [];
        for (var j = 0; j < targetArray.length; j++) {
            var tLine = targetArray[j].getAttribute('data-line-idx') || "0";
            if (tLine === lineIdx) {
                targetLineSpans.push(j);
            }
        }
        if (targetLineSpans.length === 0) return -1;
        if (sourceLineSpans.length <= 1 || targetLineSpans.length <= 1) return targetLineSpans[0];
        var ratio = sourcePos / (sourceLineSpans.length - 1);
        var targetPos = Math.round(ratio * (targetLineSpans.length - 1));
        return targetLineSpans[targetPos];
    }

    function wireMvpEvents() {
        if (!mvpHighlightEnabled) return;
        var ensureIndex = function() {
            if (!isMvpIndexBuilt) buildLcIndex();
        };
        var handleMouseOver = function() {
            if (window.__selectableTextMode) return;
            ensureIndex();
            var idxStr = this.getAttribute('data-mvp-idx');
            if (idxStr !== null && idxStr !== '') {
                var idx = parseInt(idxStr, 10);
                var isSource = (this.getAttribute('data-mvp-type') === 'source');
                var targetIdx = getTargetIdx(idx, isSource);
                var targetSpan = isSource ? transSpansArray[targetIdx] : sourceSpansArray[targetIdx];
                if (targetSpan) {
                    addClass(targetSpan, 'hl-mvp-hover');
                }
            }
        };
        var handleMouseOut = function() {
            if (window.__selectableTextMode) return;
            ensureIndex();
            var idxStr = this.getAttribute('data-mvp-idx');
            if (idxStr !== null && idxStr !== '') {
                var idx = parseInt(idxStr, 10);
                var isSource = (this.getAttribute('data-mvp-type') === 'source');
                var targetIdx = getTargetIdx(idx, isSource);
                var targetSpan = isSource ? transSpansArray[targetIdx] : sourceSpansArray[targetIdx];
                if (targetSpan && !targetSpan.classList.contains('hl-mvp-pin')) {
                    removeClass(targetSpan, 'hl-mvp-hover');
                }
            }
        };
        var handleClick = function(e) {
            if (window.__selectableTextMode) return;
            ensureIndex();
            e = e || window.event;
            var btn = (e.button !== undefined) ? e.button : e.which;
            if (btn !== 0 && btn !== 1) return;
            var idxStr = this.getAttribute('data-mvp-idx');
            if (idxStr === null || idxStr === '') return;
            var idx = parseInt(idxStr, 10);
            var isSource = (this.getAttribute('data-mvp-type') === 'source');
            var bKey = (isSource ? 's' : 't') + idxStr;
            var bIdx = -1;
            for (var i = 0; i < mvpBookmarks.length; i++) {
                if (mvpBookmarks[i].srcSpan === this || mvpBookmarks[i].transSpan === this) { bIdx = i; break; }
            }
            var targetIdx = getTargetIdx(idx, isSource);
            var srcSpan = isSource ? sourceSpansArray[idx] : sourceSpansArray[targetIdx];
            var transSpan = isSource ? transSpansArray[targetIdx] : transSpansArray[idx];
            if (bIdx !== -1) {
                var entry = mvpBookmarks[bIdx];
                if (entry.srcSpan) removeClass(entry.srcSpan, 'hl-mvp-hover');
                if (entry.transSpan) removeClass(entry.transSpan, 'hl-mvp-hover');
                mvpBookmarks.splice(bIdx, 1);
            } else {
                while (mvpBookmarks.length >= mvpN) {
                    var oldest = mvpBookmarks.shift();
                    if (oldest.srcSpan) removeClass(oldest.srcSpan, 'hl-mvp-hover');
                    if (oldest.transSpan) removeClass(oldest.transSpan, 'hl-mvp-hover');
                }
                var slot = 0;
                if (mvpRainbowMode) {
                    var usedSlots = {};
                    for (var i = 0; i < mvpBookmarks.length; i++) {
                        if (mvpBookmarks[i].slot !== undefined) {
                            usedSlots[mvpBookmarks[i].slot] = true;
                        }
                    }
                    for (var s = 0; s < mvpN; s++) {
                        if (!usedSlots[s]) {
                            slot = s;
                            break;
                        }
                    }
                }
                mvpBookmarks.push({ idx: bKey, srcSpan: srcSpan, transSpan: transSpan, slot: slot });
            }
            refreshBookmarkClasses();
        };

        var sc = document.getElementById('source-container');
        if (sc) {
            var srcSpans = sc.getElementsByTagName('span');
            for (var i = 0; i < srcSpans.length; i++) {
                var span = srcSpans[i];
                if (span.classList && span.classList.contains('word')) {
                    if (span.getAttribute('data-mvp-wired')) continue;
                    span.setAttribute('data-mvp-wired', '1');
                    addEvent(span, 'mouseover', handleMouseOver);
                    addEvent(span, 'mouseout', handleMouseOut);
                    addEvent(span, 'click', handleClick);
                }
            }
        }
        var tc = document.getElementById('translation-container');
        if (tc) {
            var transSpans = tc.getElementsByTagName('span');
            for (var j = 0; j < transSpans.length; j++) {
                var span = transSpans[j];
                if (span.classList && span.classList.contains('hl-mvp')) {
                    if (span.getAttribute('data-mvp-wired')) continue;
                    span.setAttribute('data-mvp-wired', '1');
                    addEvent(span, 'mouseover', handleMouseOver);
                    addEvent(span, 'mouseout', handleMouseOut);
                    addEvent(span, 'click', handleClick);
                }
            }
        }
    }

    window.clearMVPBookmarks = function() {
        var cleared = false;
        if (mvpBookmarks && mvpBookmarks.length > 0) {
            cleared = true;
            for (var i = 0; i < mvpBookmarks.length; i++) {
                var entry = mvpBookmarks[i];
                try {
                    if (entry.srcSpan && isAttached(entry.srcSpan)) {
                        removeClass(entry.srcSpan, 'hl-mvp-hover');
                    }
                } catch(e) {}
                try {
                    if (entry.transSpan && isAttached(entry.transSpan)) {
                        removeClass(entry.transSpan, 'hl-mvp-hover');
                    }
                } catch(e) {}
            }
            mvpBookmarks = [];
            refreshBookmarkClasses();
        }
        return cleared;
    };

    window.getBookmarkIndices = function() {
        var indices = [];
        if (mvpBookmarks) {
            for (var i = 0; i < mvpBookmarks.length; i++) {
                indices.push(mvpBookmarks[i].idx);
            }
        }
        return indices.join(',');
    };

    window.restoreBookmarksByIndices = function(indicesStr) {
        mvpBookmarks = [];
        if (!indicesStr) {
            refreshBookmarkClasses();
            return;
        }
        var indices = indicesStr.split(',');
        if (!isMvpIndexBuilt) buildLcIndex();
        for (var i = 0; i < indices.length; i++) {
            var bKey = indices[i];
            if (bKey === '') continue;
            var prefix = bKey.charAt(0);
            var idx, isSource;
            if (prefix === 's' || prefix === 't') {
                idx = parseInt(bKey.substring(1), 10);
                isSource = (prefix === 's');
            } else {
                idx = parseInt(bKey, 10);
                isSource = true;
            }
            var targetIdx = getTargetIdx(idx, isSource);
            var srcSpan = isSource ? sourceSpansArray[idx] : sourceSpansArray[targetIdx];
            var transSpan = isSource ? transSpansArray[targetIdx] : transSpansArray[idx];
            var slot = 0;
            if (mvpRainbowMode) {
                var usedSlots = {};
                for (var m = 0; m < mvpBookmarks.length; m++) {
                    if (mvpBookmarks[m].slot !== undefined) {
                        usedSlots[mvpBookmarks[m].slot] = true;
                    }
                }
                for (var s = 0; s < mvpN; s++) {
                    if (!usedSlots[s]) {
                        slot = s;
                        break;
                    }
                }
            }
            mvpBookmarks.push({ idx: bKey, srcSpan: srcSpan, transSpan: transSpan, slot: slot });
        }
        refreshBookmarkClasses();
    };

    window.__selectableTextMode = false;
    window.__persistentSelectableMode = false;

    window.setSelectableTextMode = function(active, isPersistent) {
        if (isPersistent !== undefined) {
            window.__persistentSelectableMode = !!isPersistent;
        }
        window.__selectableTextMode = !!active;
        if (active) {
            if (document.body && document.body.classList) document.body.classList.add('text-selection-mode-active');
        } else {
            if (document.body && document.body.classList) document.body.classList.remove('text-selection-mode-active');
        }
    };

    addEvent(window, 'keydown', function(e) {
        e = e || window.event;
        if (e.key === 'Alt' || e.keyCode === 18) {
            if (!window.__selectableTextMode) {
                window.setSelectableTextMode(true, false);
            }
        }
    });

    addEvent(window, 'keyup', function(e) {
        e = e || window.event;
        if (e.key === 'Alt' || e.keyCode === 18) {
            if (!window.__persistentSelectableMode) {
                window.setSelectableTextMode(false, false);
            }
        }
    });

    addEvent(window, 'blur', function() {
        if (!window.__persistentSelectableMode) {
            window.setSelectableTextMode(false, false);
        }
    });

    window.rebindMVPBookmarks = function() {
        tokenizeTranslation();
        buildLcIndex();
        wireMvpEvents();
    };

    var isInitialized = false;
    function init() {
        if (isInitialized) return;
        isInitialized = true;
        checkAhkHost();
        var tableRows = [];
        var selectedRowIdsMap = {};
        var initialHighlights = {};
        var hasHighlightCol = false;
        var lastClickedRowId = null;
        var focusedRowId = null;
        var deltas = [];
        var historyStack = [];
        var historyIndex = -1;
        var touchedCells = {};
        var lastClickedCell = null;
        var lastHoveredCell = null;
        var isDragSelecting = false;
        var dragStartRowId = null;
        var dragSelectMode = true;
        var isTokenDragSelecting = false;
        var tokenDragMode = true;
        var dragOccurred = false;
        var justFinishedDrag = false;
        var tokenDragStartIdx = -1;
        var tokenDragLastIdx = -1;
        var initialSelectedMap = null;
        var mousedownTargetSpan = null;
        var isRmbDragFlipping = false;
        var rmbFlipMode = true;
        var initialFlippedMap = null;
        
        var tokenMap = [];
        try {
            var tokenMapEl = document.getElementById('token-map');
            var jsonStr = tokenMapEl.text || tokenMapEl.textContent || tokenMapEl.innerHTML || "[]";
            tokenMap = JSON.parse(jsonStr);
        } catch(e) {}
        
        var isProgressive = false;
        try {
            var progEl = document.getElementById('display-mode');
            if (progEl && (progEl.textContent || progEl.innerText).trim() === 'progressive') {
                isProgressive = true;
            }
        } catch(e) {}
        
        window.AppState = {
            rows: {},
            sourceText: null,
            translatedText: null,
            stage: null,
            isFinished: false,
            applyDeltas: function(data) {
                if (!data) return;
                
                var isTerminal = (data.stage === 'finished' || data.status === 'failed' || data.status === 'partial_persisted');
                if (isTerminal) {
                    window.AppState.isFinished = true;
                    try {
                        if (document.body) {
                            document.body.setAttribute('data-worker-status', 'finished');
                        }
                    } catch (e) {
                    }
                }
                
                var updated = false;
                
                if (data.sourceText !== undefined && data.sourceText !== "") {
                    window.AppState.sourceText = data.sourceText;
                    if (window.AppView.renderSourceText(data.stage)) updated = true;
                } else if (document.getElementById('source-container') && document.getElementById('source-container').querySelector('[data-pending="true"]')) {
                    if (window.AppView.renderSourceText(data.stage)) updated = true;
                }
                
                if (data.translatedText !== undefined && data.translatedText !== "") {
                    window.AppState.translatedText = data.translatedText;
                    if (window.AppView.renderTranslatedText(data.stage)) updated = true;
                } else if (document.getElementById('translation-container') && document.getElementById('translation-container').querySelector('[data-pending="true"]')) {
                    if (window.AppView.renderTranslatedText(data.stage)) updated = true;
                }
                
                var rowsData = null;
                if (data.stage) {
                    if (data.rows) rowsData = data.rows;
                } else {
                    rowsData = data;
                }
                
                if (rowsData) {
                    for (var rowId in rowsData) {
                        if (rowsData.hasOwnProperty(rowId)) {
                            if (!window.AppState.rows[rowId]) window.AppState.rows[rowId] = {};
                            var delta = rowsData[rowId];
                            for (var key in delta) {
                                window.AppState.rows[rowId][key] = delta[key];
                            }
                            if (window.AppView.renderRow(rowId, data.stage)) {
                                updated = true;
                            }
                        }
                    }
                }
                if (window.AppState.isFinished) {
                    var skels = document.querySelectorAll('td .skeleton-loader');
                    if (skels.length > 0) {
                        for (var i = 0; i < skels.length; i++) {
                            var p = skels[i].parentNode;
                            if (p && p.classList.contains('scrollable-cell')) p.innerHTML = "";
                            else if (p && p.tagName === 'TD') p.innerHTML = "";
                        }
                        updated = true;
                    }
                }
                
                if (updated) {
                    if (window.clearMVPBookmarks) window.clearMVPBookmarks();
                    if (window.rebindMVPBookmarks) window.rebindMVPBookmarks();
                    
                    if (window.forceRepaint) window.forceRepaint();
                }

                // Signal AHK only after all rendering is complete so that "Ready"
                // in the window title reflects the window being fully populated.
                if (isTerminal && window.ahkCall) {
                    try {
                        window.ahkCall('finished', '');
                    } catch (e) {
                    }
                }
            }
        };

        function setCellText(el, text) {
            if (!el) return;
            el.innerHTML = text ? String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : "";
        }

        window.AppView = {
            renderSourceText: function(globalStage) {
                var container = document.getElementById('source-container');
                if (!container) return false;
                var pendingNode = container.querySelector('[data-pending="true"]');
                if (window.AppState.sourceText === null && !pendingNode) return false;
                var hasSpans = container.querySelector('span.word') !== null;
                var currentText = (container.textContent || container.innerText || "").trim().replace(/\\s+/g, ' ');
                var tempDiv = document.createElement('div');
                tempDiv.innerHTML = window.AppState.sourceText || "";
                var newText = (tempDiv.textContent || tempDiv.innerText || "").trim().replace(/\\s+/g, ' ');
                
                // Do not destroy perfectly good span.word tags if the text hasn't changed
                if (hasSpans && !pendingNode && currentText === newText) {
                    return false;
                }
                
                var forceUpdate = (globalStage === 'finished' || globalStage === 'translated' || globalStage === 'translated_lemmas' || window.AppState.isFinished);
                if (pendingNode || currentText !== newText || (forceUpdate && !hasSpans)) {
                    container.innerHTML = window.AppState.sourceText || "";
                    if (typeof tokenSpans !== 'undefined') {
                        tokenSpans = [];
                    }
                    return true;
                }
                return false;
            },
            renderTranslatedText: function(globalStage) {
                var container = document.getElementById('translation-container');
                if (!container) return false;
                var pendingNode = container.querySelector('[data-pending="true"]');
                if (window.AppState.translatedText === null && !pendingNode) return false;
                var currentText = (container.textContent || container.innerText || "").trim().replace(/\\s+/g, ' ');
                var tempDiv = document.createElement('div');
                tempDiv.innerHTML = window.AppState.translatedText || "";
                var newText = (tempDiv.textContent || tempDiv.innerText || "").trim().replace(/\\s+/g, ' ');
                var forceUpdate = (globalStage === 'finished' || globalStage === 'translated' || globalStage === 'translated_lemmas' || window.AppState.isFinished);
                if (newText || !pendingNode || forceUpdate) {
                    if (pendingNode || currentText !== newText || forceUpdate) {
                        container.innerHTML = window.AppState.translatedText || "";
                        return true;
                    }
                }
                return false;
            },
            renderRow: function(rowId, globalStage) {
                var updated = false;
                var rowData = window.AppState.rows[rowId];
                if (!rowData) return false;
                var tr = null;
                if (rowData.token_order !== undefined && rowData.token_order !== null && String(rowData.token_order) !== "") {
                    tr = document.querySelector('tr[data-token-order="' + rowData.token_order + '"]');
                }
                if (!tr) {
                    tr = document.querySelector('tr[data-row-id="' + rowId + '"]');
                }
                if (!tr) return false;
                var tds = tr.getElementsByTagName('td');
                if (tds.length >= 5) {
                    if (!tds[0].classList.contains('dirty') && rowData.hasOwnProperty('inflected') && rowData.inflected !== undefined && rowData.inflected !== "") {
                        var div = tds[0].querySelector('.scrollable-cell');
                        var val = rowData.inflected || "";
                        var oldVal = div ? (div.textContent || div.innerText) : (tds[0].classList.contains('editing') ? null : (tds[0].textContent || tds[0].innerText));
                        if (oldVal !== val) {
                            if (div) setCellText(div, val);
                            else if (!tds[0].classList.contains('editing')) setCellText(tds[0], val);
                            updated = true;
                        }
                    }
                    if (!tds[1].classList.contains('dirty') && rowData.hasOwnProperty('lemma') && rowData.lemma !== undefined) {
                        var div = tds[1].querySelector('.scrollable-cell');
                        var val = rowData.lemma || "";
                        if (globalStage === 'translated_lemmas' || globalStage === 'translated' || globalStage === 'finished') {
                            if (div) setCellText(div, val);
                            else if (!tds[1].classList.contains('editing')) setCellText(tds[1], val);
                            updated = true;
                        } else {
                            var oldVal = div ? (div.textContent || div.innerText) : (tds[1].classList.contains('editing') ? null : (tds[1].textContent || tds[1].innerText));
                            if (oldVal !== val) {
                                if (div) setCellText(div, val);
                                else if (!tds[1].classList.contains('editing')) setCellText(tds[1], val);
                                updated = true;
                            }
                        }
                    }
                    if (!tds[2].classList.contains('dirty') && rowData.hasOwnProperty('trans') && rowData.trans !== undefined) {
                        var div = tds[2].querySelector('.scrollable-cell');
                        var val = rowData.trans || "";
                        if (globalStage === 'translated_words' || globalStage === 'finished' || window.AppState.isFinished) {
                            if (div) setCellText(div, val);
                            else if (!tds[2].classList.contains('editing')) setCellText(tds[2], val);
                            updated = true;
                        } else {
                            var oldVal = div ? (div.textContent || div.innerText) : (tds[2].classList.contains('editing') ? null : (tds[2].textContent || tds[2].innerText));
                            var hasSkeleton = (div || tds[2]).querySelector('.skeleton-loader') !== null;
                            var shouldUpdate = (oldVal !== val);
                            if (hasSkeleton) shouldUpdate = (val !== "") || (globalStage === 'finished') || window.AppState.isFinished;
                            if (shouldUpdate) {
                                if (div) setCellText(div, val);
                                else if (!tds[2].classList.contains('editing')) setCellText(tds[2], val);
                                updated = true;
                            }
                        }
                    }
                    if (!tds[3].classList.contains('dirty') && rowData.hasOwnProperty('ipa') && rowData.ipa !== undefined) {
                        var div = tds[3].querySelector('.scrollable-cell');
                        var val = rowData.ipa || "";
                        if (globalStage === 'translated_words' || globalStage === 'finished' || window.AppState.isFinished) {
                            if (div) setCellText(div, val);
                            else if (!tds[3].classList.contains('editing')) setCellText(tds[3], val);
                            updated = true;
                        } else {
                            var oldVal = div ? (div.textContent || div.innerText) : (tds[3].classList.contains('editing') ? null : (tds[3].textContent || tds[3].innerText));
                            var hasSkeleton = (div || tds[3]).querySelector('.skeleton-loader') !== null;
                            var shouldUpdate = (oldVal !== val);
                            if (hasSkeleton) shouldUpdate = (val !== "") || (globalStage === 'finished') || window.AppState.isFinished;
                            if (shouldUpdate) {
                                if (div) setCellText(div, val);
                                else if (!tds[3].classList.contains('editing')) setCellText(tds[3], val);
                                updated = true;
                            }
                        }
                    }
                    if (!tds[4].classList.contains('dirty') && rowData.hasOwnProperty('morph') && rowData.morph !== undefined) {
                        var div = tds[4].querySelector('.scrollable-cell');
                        var val = rowData.morph || "";
                        if (globalStage === 'translated_words' || globalStage === 'finished' || window.AppState.isFinished) {
                            if (div) div.innerHTML = val;
                            else if (!tds[4].classList.contains('editing')) tds[4].innerHTML = val;
                            updated = true;
                        } else {
                            var oldVal = div ? div.innerHTML : (tds[4].classList.contains('editing') ? null : tds[4].innerHTML);
                            var hasSkeleton = (div || tds[4]).querySelector('.skeleton-loader') !== null;
                            var shouldUpdate = (oldVal !== val);
                            if (hasSkeleton) shouldUpdate = (val !== "") || (globalStage === 'finished') || window.AppState.isFinished;
                            if (shouldUpdate) {
                                if (div) div.innerHTML = val;
                                else if (!tds[4].classList.contains('editing')) tds[4].innerHTML = val;
                                updated = true;
                            }
                        }
                    }
                    if (rowData.hasOwnProperty('classifications') && rowData.classifications !== undefined) {
                        for (var class_name in rowData.classifications) {
                            if (rowData.classifications.hasOwnProperty(class_name)) {
                                var val = rowData.classifications[class_name] || "";
                                var cell = tr.querySelector('td[data-col="' + class_name + '"]');
                                if (cell) {
                                    var div = cell.querySelector('.scrollable-cell');
                                    var displayVal = val;
                                    var spanClass = "";
                                    if (val.indexOf(':') !== -1) {
                                        var parts = val.split(':', 2);
                                        var possiblePrefix = parts[0].trim();
                                        if (possiblePrefix.length <= 5 && possiblePrefix.indexOf('/') === -1 && possiblePrefix.indexOf('\\\\') === -1) {
                                            displayVal = parts[1].trim();
                                            spanClass = "level-" + possiblePrefix.toLowerCase();
                                        }
                                    }
                                    var innerHtml = spanClass ? '<span class="' + spanClass + '">' + displayVal + '</span>' : displayVal;
                                    var oldHtml = div ? div.innerHTML : cell.innerHTML;
                                    if (oldHtml !== innerHtml) {
                                        if (div) div.innerHTML = innerHtml;
                                        else cell.innerHTML = innerHtml;
                                        updated = true;
                                    }
                                }
                            }
                        }
                    }
                }
                return updated;
            }
        };

        window.receiveUpdate = function(data) {
            window.AppState.applyDeltas(data);
        };

        // Progressive Skeleton Auto-Resolution Hook (SSE + Short-interval Watchdog Polling)
        try {
            var isWebMode = (window.location.protocol !== 'file:') || (document.body && document.body.getAttribute('data-web-mode') === 'true');
            var sessZidEl = document.getElementById('session-zid');
            var curZid = sessZidEl ? (sessZidEl.textContent || sessZidEl.innerText || "").trim() : "";
            if (!curZid) {
                var params = new URLSearchParams(window.location.search);
                curZid = params.get('session_zid') || params.get('zid') || "";
            }
            if (!curZid && document.body && document.body.getAttribute('data-zid')) {
                curZid = document.body.getAttribute('data-zid');
            }

            var hasSkeletons = document.querySelectorAll('.skeleton-loader, [data-pending="true"]').length > 0;

            // 1. SSE Real-time Listener (only in web mode with EventSource support)
            if (isWebMode && curZid && typeof EventSource !== 'undefined') {
                try {
                    var sseUrl = "/events?zid=" + encodeURIComponent(curZid);
                    var evtSource = new EventSource(sseUrl);
                    evtSource.onmessage = function(e) {
                        try {
                            var parsed = JSON.parse(e.data);
                            if (parsed && (parsed.type === 'stage' || parsed.type === 'update' || parsed.rows || parsed.stage || parsed.is_finished)) {
                                window.receiveUpdate(parsed);
                                var remaining = document.querySelectorAll('.skeleton-loader, [data-pending="true"]').length;
                                if (remaining === 0 || parsed.is_finished || parsed.stage === 'finished') {
                                    try { evtSource.close(); } catch(err) {}
                                }
                            }
                        } catch(err) {}
                    };
                } catch(sseErr) {}
            }

            // 2. Automated watchdog polling when skeleton loaders are present (only in web mode with fetch support)
            if (isWebMode && typeof fetch !== 'undefined' && hasSkeletons && curZid) {
                var startTime = Date.now();
                var maxBudgetMs = 15000; // 15-second watchdog budget
                var pollIntervalMs = 500;
                var isPolling = false;
                var resolved = false;

                var pollSessionStatus = function() {
                    if (resolved || (Date.now() - startTime > maxBudgetMs)) {
                        if (window._kwSkeletonPollTimer) {
                            clearInterval(window._kwSkeletonPollTimer);
                            window._kwSkeletonPollTimer = null;
                        }
                        return;
                    }
                    if (isPolling) return;
                    isPolling = true;

                    var statusUrl = "/session/status?zid=" + encodeURIComponent(curZid);
                    fetch(statusUrl, { method: 'GET', headers: { 'Accept': 'application/json' } })
                        .then(function(res) {
                            if (res.ok) return res.json();
                            throw new Error("status endpoint error");
                        })
                        .then(function(resObj) {
                            isPolling = false;
                            var data = (resObj && resObj.data) ? resObj.data : resObj;
                            if (data && (data.is_finished || data.stage === 'finished' || (data.status && (data.status.is_finished || data.status === 'finished')))) {
                                resolved = true;
                                if (window._kwSkeletonPollTimer) {
                                    clearInterval(window._kwSkeletonPollTimer);
                                    window._kwSkeletonPollTimer = null;
                                }
                                if (window.onSessionReload) {
                                    window.onSessionReload();
                                } else {
                                    window.location.reload();
                                }
                            } else if (data && data.rows) {
                                if (window.receiveUpdate) {
                                    window.receiveUpdate(data);
                                }
                                var remaining = document.querySelectorAll('.skeleton-loader, [data-pending="true"]').length;
                                if (remaining === 0) {
                                    resolved = true;
                                    if (window._kwSkeletonPollTimer) {
                                        clearInterval(window._kwSkeletonPollTimer);
                                        window._kwSkeletonPollTimer = null;
                                    }
                                }
                            }
                        })
                        .catch(function() {
                            var renderCheckUrl = "/?session_zid=" + encodeURIComponent(curZid);
                            fetch(renderCheckUrl, { method: 'GET' })
                                .then(function(res) { return res.text(); })
                                .then(function(htmlText) {
                                    isPolling = false;
                                    if (htmlText && htmlText.indexOf('skeleton-loader') === -1) {
                                        resolved = true;
                                        if (window._kwSkeletonPollTimer) {
                                            clearInterval(window._kwSkeletonPollTimer);
                                            window._kwSkeletonPollTimer = null;
                                        }
                                        if (window.onSessionReload) {
                                            window.onSessionReload();
                                        } else {
                                            window.location.reload();
                                        }
                                    }
                                })
                                .catch(function() {
                                    isPolling = false;
                                });
                        });
                };

                window._kwSkeletonPollTimer = setInterval(pollSessionStatus, pollIntervalMs);
                setTimeout(pollSessionStatus, 200);
            }
        } catch(e) {}

        window.startPolling = function() {
            // Manual trigger fallback if needed
        };

        var workerLaunched = false;
        try {
            var wlEl = document.getElementById('worker-launched');
            if (wlEl && (wlEl.textContent || wlEl.innerText).trim() === 'true') {
                workerLaunched = true;
            }
        } catch(e) {}
        
        var sourceContainer = document.getElementById('source-container');
        var spans = sourceContainer ? sourceContainer.getElementsByTagName('span') : [];
        var tokenSpans = [];
        for (var i = 0; i < spans.length; i++) {
            if (spans[i].classList.contains('word')) {
                tokenSpans.push(spans[i]);
            }
        }
        var lemmaTable = document.getElementById('lemma-table');
        if (lemmaTable) {
            var tbodies = lemmaTable.getElementsByTagName('tbody');
            var rowsContainer = tbodies.length > 0 ? tbodies[0] : lemmaTable;
            var allRows = rowsContainer.getElementsByTagName('tr');
            for (var i = 0; i < allRows.length; i++) {
                if (allRows[i].getAttribute('data-row-id') !== null) {
                    tableRows.push(allRows[i]);
                }
            }
        }

        function isCompoundDelimiterNode(node) {
            if (!node) return false;
            if (node.nodeType === 3) { // Text node
                var txt = node._origVal !== undefined ? node._origVal : (node.nodeValue || node.textContent || "");
                return txt.length > 0 && /^[-_–—.:#@]+$/.test(txt);
            }
            return false;
        }

        function findCompoundSiblingSpans(span) {
            if (!span || !span.classList || !span.classList.contains('word')) return span ? [span] : [];
            
            var compoundId = span.getAttribute('data-compound-id');
            var startSpan = span;
            var curr = span;
            while (curr) {
                var prevNode = curr.previousSibling;
                if (isCompoundDelimiterNode(prevNode)) {
                    var prevSpan = prevNode.previousSibling;
                    if (prevSpan && prevSpan.nodeType === 1 && prevSpan.classList && prevSpan.classList.contains('word')) {
                        startSpan = prevSpan;
                        curr = prevSpan;
                        continue;
                    }
                } else if (prevNode && prevNode.nodeType === 1 && prevNode.classList && prevNode.classList.contains('word')) {
                    var prevCompoundId = prevNode.getAttribute('data-compound-id');
                    if (compoundId && prevCompoundId && compoundId === prevCompoundId) {
                        startSpan = prevNode;
                        curr = prevNode;
                        continue;
                    }
                }
                break;
            }
            
            var group = [startSpan];
            curr = startSpan;
            while (curr) {
                var nextNode = curr.nextSibling;
                if (isCompoundDelimiterNode(nextNode)) {
                    var nextSpan = nextNode.nextSibling;
                    if (nextSpan && nextSpan.nodeType === 1 && nextSpan.classList && nextSpan.classList.contains('word')) {
                        group.push(nextSpan);
                        curr = nextSpan;
                        continue;
                    }
                } else if (nextNode && nextNode.nodeType === 1 && nextNode.classList && nextNode.classList.contains('word')) {
                    var nextCompoundId = nextNode.getAttribute('data-compound-id');
                    var currCompoundId = curr.getAttribute('data-compound-id');
                    if (currCompoundId && nextCompoundId && currCompoundId === nextCompoundId) {
                        group.push(nextNode);
                        curr = nextNode;
                        continue;
                    }
                }
                break;
            }
            
            return group;
        }

        function findTokenData(span) {
            var wordIdx = parseInt(span.getAttribute('data-word-idx'));
            for (var i = 0; i < tokenMap.length; i++) {
                var t = tokenMap[i];
                if (t.visual_idx === wordIdx) {
                    return t;
                }
            }
            return null;
        }

        function getRawRowTranslations(span) {
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return [];
            }
            var translations = [];
            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = null;
                for (var k = 0; k < tableRows.length; k++) {
                    if (parseInt(tableRows[k].getAttribute('data-row-id')) === rowId) {
                        tr = tableRows[k];
                        break;
                    }
                }
                if (tr) {
                    var tds = tr.getElementsByTagName('td');
                    for (var m = 0; m < tds.length; m++) {
                        if (tds[m].getAttribute('data-col') === 'WordDestination') {
                            var trans = tds[m].textContent || tds[m].innerText || "";
                            trans = trans.trim();
                            if (trans && translations.indexOf(trans) === -1) {
                                translations.push(trans);
                            }
                        }
                    }
                }
            }
            return translations;
        }

        function isMatchingSubTokenLemma(lemma, spanClean) {
            if (!lemma || !spanClean) return false;
            var l = lemma.toLowerCase().trim();
            var s = spanClean.toLowerCase().trim();
            if (l === s) return true;
            if (l.indexOf(s) !== -1 || s.indexOf(l) !== -1) return true;
            if (l.length >= 4 && s.length >= 4) {
                var stemLen = Math.min(l.length, s.length, 5);
                if (l.substring(0, stemLen) === s.substring(0, stemLen)) {
                    return true;
                }
            }
            return false;
        }

        function getSpanSpecificTranslation(span, group) {
            if (!group) group = findCompoundSiblingSpans(span);
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return "";
            }

            if (!group || group.length <= 1) {
                var raw = getRawRowTranslations(span);
                return raw.join(' ');
            }
            
            var spanClean = (span.getAttribute('data-lower-clean') || span.getAttribute('data-original-text') || span.textContent || "").trim().toLowerCase();
            
            var otherSpansClean = [];
            if (group && group.length > 1) {
                for (var i = 0; i < group.length; i++) {
                    if (group[i] !== span) {
                        var otherClean = (group[i].getAttribute('data-lower-clean') || group[i].getAttribute('data-original-text') || group[i].textContent || "").trim().toLowerCase();
                        if (otherClean) otherSpansClean.push(otherClean);
                    }
                }
            }

            var specificTranslations = [];
            var compoundTranslations = [];

            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = null;
                for (var k = 0; k < tableRows.length; k++) {
                    if (parseInt(tableRows[k].getAttribute('data-row-id')) === rowId) {
                        tr = tableRows[k];
                        break;
                    }
                }
                if (!tr) continue;

                var tds = tr.getElementsByTagName('td');
                var lemma = "";
                var trans = "";
                for (var m = 0; m < tds.length; m++) {
                    var col = tds[m].getAttribute('data-col');
                    if (col === '{lemma_col_name}') {
                        lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                    } else if (col === 'WordDestination') {
                        trans = (tds[m].textContent || tds[m].innerText || "").trim();
                    }
                }

                if (!trans) continue;

                var lemmaClean = lemma.toLowerCase();
                var isFullCompoundLemma = (lemmaClean.indexOf('-') !== -1 || lemmaClean.indexOf('_') !== -1 || lemmaClean.indexOf(' ') !== -1 || lemmaClean.indexOf('/') !== -1 || lemmaClean.indexOf('.') !== -1 || lemmaClean.indexOf(':') !== -1);

                if (isFullCompoundLemma) {
                    if (compoundTranslations.indexOf(trans) === -1) {
                        compoundTranslations.push(trans);
                    }
                } else {
                    var matchesThis = isMatchingSubTokenLemma(lemmaClean, spanClean);
                    var matchesOther = false;
                    if (group && group.length > 1) {
                        for (var o = 0; o < otherSpansClean.length; o++) {
                            if (isMatchingSubTokenLemma(lemmaClean, otherSpansClean[o])) {
                                matchesOther = true;
                                break;
                            }
                        }
                    }

                    if (matchesThis && !matchesOther) {
                        if (specificTranslations.indexOf(trans) === -1) {
                            specificTranslations.push(trans);
                        }
                    }
                }
            }

            if (specificTranslations.length > 0) {
                return specificTranslations.join(' ');
            }

            // 2. If compound is a group and there is a shared multi-part translation
            if (group.length > 1) {
                var spanIdx = -1;
                for (var i = 0; i < group.length; i++) {
                    if (group[i] === span) {
                        spanIdx = i;
                        break;
                    }
                }
                
                var rowTranslations = getRawRowTranslations(span);
                
                if (rowTranslations.length === group.length && spanIdx >= 0 && spanIdx < rowTranslations.length) {
                    return rowTranslations[spanIdx];
                }
                
                if (rowTranslations.length === 1) {
                    var singleTrans = rowTranslations[0];
                    var parts = singleTrans.indexOf(',') !== -1 ? singleTrans.split(',') : (singleTrans.indexOf('_') !== -1 ? singleTrans.split('_') : []);
                    parts = parts.map(function(p) { return p.trim(); }).filter(function(p) { return p.length > 0; });
                    if (parts.length === group.length && spanIdx >= 0 && spanIdx < parts.length) {
                        return parts[spanIdx];
                    }
                    if (spanIdx === 0) {
                        return singleTrans;
                    } else {
                        return "";
                    }
                }
            }
            
            var raw = getRawRowTranslations(span);
            return raw.join(' ');
        }

        function getWordTranslation(span) {
            var group = findCompoundSiblingSpans(span);
            if (group.length > 1) {
                var t = getSpanSpecificTranslation(span, group);
                if (t) return t;
            }
            
            var raw = getRawRowTranslations(span);
            return raw.join(' ');
        }

        function flipWord(span, toTranslation, forceCompound) {
            if (!span) return;
            var group = findCompoundSiblingSpans(span);
            var targets = [span];

            if (forceCompound === true) {
                targets = group;
            } else if (forceCompound === false) {
                targets = [span];
            } else {
                // Default logic: flip full compound ONLY if the compound is already fully selected
                if (group.length > 1) {
                    var allSelected = true;
                    for (var g = 0; g < group.length; g++) {
                        var gSpan = group[g];
                        var hasHighlight = gSpan.classList.contains('highlight-orange-active') || gSpan.classList.contains('highlight-purple-active');
                        var gTd = findTokenData(gSpan);
                        var compoundRowSelected = false;
                        if (gTd && gTd.compound_row_ids) {
                            for (var c = 0; c < gTd.compound_row_ids.length; c++) {
                                if (selectedRowIdsMap.hasOwnProperty(String(gTd.compound_row_ids[c]))) {
                                    compoundRowSelected = true;
                                    break;
                                }
                            }
                        }
                        if (!hasHighlight && !compoundRowSelected) {
                            allSelected = false;
                            break;
                        }
                    }
                    if (allSelected) {
                        targets = group;
                    }
                }
            }

            for (var i = 0; i < targets.length; i++) {
                var s = targets[i];
                if (!s.getAttribute('data-original-text')) {
                    s.setAttribute('data-original-text', s.textContent || s.innerText || "");
                }
                var isFlipped = s.classList.contains('flipped');

                if (toTranslation) {
                    if (!isFlipped) {
                        var trans = getSpanSpecificTranslation(s, group);
                        if (trans !== undefined && trans !== null && trans.trim().length > 0) {
                            s.classList.add('flipped');
                            s.textContent = trans;
                        } else {
                            s.textContent = s.getAttribute('data-original-text');
                        }
                    }
                } else {
                    if (isFlipped) {
                        s.classList.remove('flipped');
                        s.textContent = s.getAttribute('data-original-text');
                    }
                }
                s.style.display = '';
            }

            // Manage delimiters between sibling spans in the compound group
            for (var j = 0; j < group.length - 1; j++) {
                var currSpan = group[j];
                var nextSpan = group[j + 1];
                var delimNode = currSpan.nextSibling;
                if (delimNode && isCompoundDelimiterNode(delimNode)) {
                    if (delimNode._origVal === undefined) {
                        delimNode._origVal = delimNode.nodeValue || "";
                    }
                    delimNode.nodeValue = delimNode._origVal;
                }
            }
        }

        var audioLmbPlay = {audio_lmb_play};
        var audioLmbSource = {audio_lmb_source};
        var audioLmbChainMode = {audio_lmb_chain_mode};
        var audioRmbPlay = {audio_rmb_play};
        var audioRmbChainMode = {audio_rmb_chain_mode};
        var audioAnkiTtsCli = "{audio_anki_tts_cli}";
        var audioPythonExe = "{audio_python_exe}";

        function sanitizeSpokenText(text) {
            if (!text) return "";
            var s = String(text);
            if (s.indexOf('|||') !== -1) {
                var parts = s.split('|||');
                var cleanParts = [];
                for (var i = 0; i < parts.length; i++) {
                    var p = sanitizeSpokenText(parts[i]);
                    if (p) cleanParts.push(p);
                }
                return cleanParts.join(' ||| ');
            }
            // Strip 14-digit ZIDs
            s = s.replace(/(^|[^0-9])[0-9]{14}(?![0-9])/g, '$1 ');
            // Replace path separators, colons, and underscore delimiters with spaces
            s = s.replace(/[\\/\\\\_:]/g, ' ');
            // Strip standalone numeric tokens
            s = s.replace(/(^|\\s)[0-9]+(?=\\s|$)/g, ' ');
            return s.replace(/\\s+/g, ' ').trim();
        }

        function playAudio(text, lang) {
            if (!text || !lang) return;
            var clean = sanitizeSpokenText(text);
            if (!clean) return;
            if (window.ahkCall && audioAnkiTtsCli && audioPythonExe) {
                var sanitizedText = clean.replace(/\\r?\\n|\\r/g, ' ');
                var escapedText = sanitizedText.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
                try {
                    window.ahkCall('play', audioPythonExe + "\\n" + audioAnkiTtsCli + "\\n" + lang + "\\n" + escapedText);
                } catch (e) {
                }
            }
        }

        function getCleanConstituentInflection(rawInflection, tokenText) {
            if (!rawInflection) return "";
            var parts = rawInflection.split(',');
            parts = parts.map(function(p) { return p.trim(); }).filter(function(p) { return p.length > 0; });
            if (parts.length <= 1) {
                return parts.length === 1 ? parts[0] : rawInflection.trim();
            }
            var cleanToken = (tokenText || "").trim().toLowerCase();
            var filtered = parts.filter(function(p) {
                return p.trim().toLowerCase() !== cleanToken;
            });
            if (filtered.length > 0) {
                return filtered[0];
            }
            return parts[0];
        }

        function getSpanSpecificSpokenWord(span, sourceMode, group) {
            if (!span) return "";
            var spanClean = (span.getAttribute('data-lower-clean') || span.getAttribute('data-original-text') || span.textContent || "").trim().toLowerCase();
            var spanText = (span.getAttribute('data-original-text') || span.textContent || span.innerText || "").trim();
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return spanText;
            }

            var bestRowLemma = "";
            var bestRowInflected = "";
            var exactMatchFound = false;

            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = null;
                for (var k = 0; k < tableRows.length; k++) {
                    if (parseInt(tableRows[k].getAttribute('data-row-id')) === rowId) {
                        tr = tableRows[k];
                        break;
                    }
                }
                if (!tr) continue;

                var tds = tr.getElementsByTagName('td');
                var lemma = "";
                var inflected = "";
                for (var m = 0; m < tds.length; m++) {
                    var col = tds[m].getAttribute('data-col');
                    if (col === '{lemma_col_name}') {
                        lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                    } else if (col === '{inflected_col_name}') {
                        inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                    }
                }

                var lemmaClean = lemma.toLowerCase();
                var inflectedClean = inflected.toLowerCase();
                var inflectedParts = inflectedClean.split(',').map(function(f) { return f.trim(); });

                if (lemmaClean === spanClean || inflectedClean === spanClean || inflectedParts.indexOf(spanClean) !== -1) {
                    bestRowLemma = lemma;
                    bestRowInflected = inflected;
                    exactMatchFound = true;
                    break;
                }

                if (!exactMatchFound && spanClean.length >= 4 && lemmaClean.length >= 4) {
                    var stemLen = Math.min(spanClean.length, lemmaClean.length, 5);
                    if (spanClean.substring(0, stemLen) === lemmaClean.substring(0, stemLen)) {
                        bestRowLemma = lemma;
                        bestRowInflected = inflected;
                        exactMatchFound = true;
                    }
                }

                if (!bestRowLemma && lemma) {
                    bestRowLemma = lemma;
                    bestRowInflected = inflected;
                }
            }

            if (sourceMode === 'inflection') {
                if (bestRowInflected) {
                    if (bestRowInflected.toLowerCase() !== spanClean && (bestRowInflected.indexOf('-') !== -1 || bestRowInflected.indexOf('_') !== -1 || bestRowInflected.indexOf(' ') !== -1 || bestRowInflected.indexOf('/') !== -1 || bestRowInflected.indexOf('\\\\') !== -1)) {
                        return spanText;
                    }
                    var cleanInf = getCleanConstituentInflection(bestRowInflected, spanClean);
                    if (cleanInf.toLowerCase() !== spanClean && !exactMatchFound) {
                        return spanText;
                    }
                    return cleanInf || bestRowLemma || spanText;
                }
                return spanText;
            } else {
                if (bestRowLemma.toLowerCase() !== spanClean && !exactMatchFound) {
                    return spanText;
                }
                if (bestRowLemma.toLowerCase() !== spanClean && (bestRowLemma.indexOf('-') !== -1 || bestRowLemma.indexOf('_') !== -1 || bestRowLemma.indexOf('/') !== -1 || bestRowLemma.indexOf('\\\\') !== -1 || bestRowLemma.indexOf(' ') !== -1)) {
                    return spanText;
                }
                return bestRowLemma || spanText;
            }
        }

        function getSingleTokenWordsToPlay(s, sourceMode, chainMode) {
            if (!s) return "";
            var words = [];
            var tokenData = findTokenData(s);
            var spanClean = (s.getAttribute('data-lower-clean') || s.getAttribute('data-original-text') || s.textContent || "").trim().toLowerCase();
            var spanText = (s.getAttribute('data-original-text') || s.textContent || s.innerText || "").trim();
            
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                var sanitized = sanitizeSpokenText(spanText);
                if (sanitized) words.push(sanitized);
            } else {
                // Check for direct-match rows (e.g. contractions like "isn't" -> [be, not], or sub-token "camel" -> [camel])
                var directMatchRows = [];
                for (var j = 0; j < tokenData.row_ids.length; j++) {
                    var rowId = tokenData.row_ids[j];
                    var tr = null;
                    for (var k = 0; k < tableRows.length; k++) {
                        if (parseInt(tableRows[k].getAttribute('data-row-id')) === rowId) {
                            tr = tableRows[k];
                            break;
                        }
                    }
                    if (!tr) continue;
                    
                    var tds = tr.getElementsByTagName('td');
                    var lemma = "";
                    var inflected = "";
                    for (var m = 0; m < tds.length; m++) {
                        var col = tds[m].getAttribute('data-col');
                        if (col === '{lemma_col_name}') {
                            lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                        } else if (col === '{inflected_col_name}') {
                            inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                        }
                    }
                    
                    var lemmaClean = lemma.toLowerCase();
                    var inflectedClean = inflected.toLowerCase();
                    var inflectedParts = inflectedClean.split(',').map(function(f) { return f.trim(); });
                    
                    if (lemmaClean === spanClean || inflectedClean === spanClean || inflectedParts.indexOf(spanClean) !== -1) {
                        directMatchRows.push({ rowId: rowId, lemma: lemma, inflected: inflected });
                    }
                }
                
                if (directMatchRows.length > 0) {
                    for (var d = 0; d < directMatchRows.length; d++) {
                        var rowObj = directMatchRows[d];
                        if (sourceMode === 'inflection') {
                            var cleanInflection = getCleanConstituentInflection(rowObj.inflected, spanClean);
                            if (cleanInflection && cleanInflection.toLowerCase() !== spanClean && (cleanInflection.indexOf('-') !== -1 || cleanInflection.indexOf('_') !== -1 || cleanInflection.indexOf(' ') !== -1 || cleanInflection.indexOf('/') !== -1 || cleanInflection.indexOf('\\\\') !== -1)) {
                                cleanInflection = (rowObj.lemma && rowObj.lemma.toLowerCase() === spanClean) ? rowObj.lemma : spanText;
                            }
                            var term = sanitizeSpokenText(cleanInflection || rowObj.lemma || spanText);
                            if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                                words.push(term);
                            }
                        } else {
                            var term = sanitizeSpokenText(rowObj.lemma || spanText);
                            if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                                words.push(term);
                            }
                        }
                    }
                } else {
                    var group = findCompoundSiblingSpans(s);
                    if ((!group || group.length <= 1) && tokenData.row_ids.length > 0) {
                        for (var j = 0; j < tokenData.row_ids.length; j++) {
                            var rowId = tokenData.row_ids[j];
                            var tr = null;
                            for (var k = 0; k < tableRows.length; k++) {
                                if (parseInt(tableRows[k].getAttribute('data-row-id')) === rowId) {
                                    tr = tableRows[k];
                                    break;
                                }
                            }
                            if (!tr) continue;
                            var tds = tr.getElementsByTagName('td');
                            var lemma = "";
                            var inflected = "";
                            for (var m = 0; m < tds.length; m++) {
                                var col = tds[m].getAttribute('data-col');
                                if (col === '{lemma_col_name}') {
                                    lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                                } else if (col === '{inflected_col_name}') {
                                    inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                                }
                            }
                            var term = (sourceMode === 'inflection' && inflected) ? inflected : lemma;
                            if (!term) term = lemma || inflected || spanText;
                            term = sanitizeSpokenText(term);
                            if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                                words.push(term);
                            }
                        }
                    }
                    if (words.length === 0) {
                        var term = getSpanSpecificSpokenWord(s, sourceMode, [s]);
                        term = sanitizeSpokenText(term);
                        if (term) words.push(term);
                    }
                }
            }
            
            var delim = (chainMode === 'separate' || chainMode === 'per_word') ? ' ||| ' : ' ';
            return words.join(delim);
        }

        function getCompoundWordsToPlay(targetSpan, sourceMode) {
            if (!targetSpan) return "";
            var group = findCompoundSiblingSpans(targetSpan);
            if (!group || group.length === 0) group = [targetSpan];
            
            var words = [];
            for (var i = 0; i < group.length; i++) {
                var s = group[i];
                var term = getSingleTokenWordsToPlay(s, sourceMode);
                if (term) {
                    words.push(term);
                }
            }
            return words.join(' ');
        }

        function getWordLemma(span) {
            return getSingleTokenWordsToPlay(span, 'lemma');
        }

        function getWordInflectedForm(span) {
            return getSingleTokenWordsToPlay(span, 'inflection');
        }

        for (var i = 0; i < tokenSpans.length; i++) {
            (function(span) {
                addEvent(span, 'mousedown', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    
                    if (e.button === 0) { // LMB
                        var clickedTokenData = findTokenData(span);
                        if (!clickedTokenData) return;

                        var targetRowIds = (clickedTokenData.atomic_row_ids && clickedTokenData.atomic_row_ids.length > 0)
                            ? clickedTokenData.atomic_row_ids.slice()
                            : (clickedTokenData.compound_row_ids && clickedTokenData.compound_row_ids.length > 0)
                                ? clickedTokenData.compound_row_ids.slice()
                                : (clickedTokenData.row_ids || []).slice();
                        
                        isTokenDragSelecting = true;
                        dragOccurred = false;
                        mousedownTargetSpan = span;
                        
                        tokenDragStartIdx = -1;
                        tokenDragLastIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                tokenDragStartIdx = k;
                                tokenDragLastIdx = k;
                                break;
                            }
                        }
                        
                        initialSelectedMap = {};
                        for (var key in selectedRowIdsMap) {
                            if (selectedRowIdsMap.hasOwnProperty(key)) {
                                initialSelectedMap[key] = selectedRowIdsMap[key];
                            }
                        }
                        
                        var allSelected = true;
                        if (targetRowIds.length === 0) {
                            allSelected = false;
                        } else {
                            for (var j = 0; j < targetRowIds.length; j++) {
                                if (!selectedRowIdsMap.hasOwnProperty(String(targetRowIds[j]))) {
                                    allSelected = false;
                                    break;
                                }
                            }
                        }
                        
                        tokenDragMode = !allSelected;
                        
                        for (var j = 0; j < targetRowIds.length; j++) {
                            if (tokenDragMode) {
                                selectedRowIdsMap[String(targetRowIds[j])] = true;
                            } else {
                                delete selectedRowIdsMap[String(targetRowIds[j])];
                            }
                        }
                        updateRowStyles();
                        updateBidirectionalHighlights();
                        
                        if (e.preventDefault) {
                            e.preventDefault();
                        } else {
                            e.returnValue = false;
                        }
                    } else if (e.button === 2) { // RMB
                        isRmbDragFlipping = true;
                        dragOccurred = false;
                        mousedownTargetSpan = span;
                        
                        tokenDragStartIdx = -1;
                        tokenDragLastIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                tokenDragStartIdx = k;
                                tokenDragLastIdx = k;
                                break;
                            }
                        }
                        
                        rmbFlipMode = !span.classList.contains('flipped');
                        flipWord(span, rmbFlipMode);
                        
                        initialFlippedMap = [];
                        for (var k = 0; k < tokenSpans.length; k++) {
                            initialFlippedMap.push(tokenSpans[k].classList.contains('flipped'));
                        }
                        
                        if (e.preventDefault) {
                            e.preventDefault();
                        } else {
                            e.returnValue = false;
                        }
                    }
                });
                
                addEvent(span, 'mouseover', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    if (isTokenDragSelecting) {
                        if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                            isTokenDragSelecting = false;
                            notifyAHKSelection();
                            return;
                        }
                        dragOccurred = true;
                        
                        var currIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                currIdx = k;
                                break;
                            }
                        }
                        if (currIdx === -1 || tokenDragStartIdx === -1) return;
                        
                        selectedRowIdsMap = {};
                        for (var key in initialSelectedMap) {
                            if (initialSelectedMap.hasOwnProperty(key)) {
                                selectedRowIdsMap[key] = initialSelectedMap[key];
                            }
                        }
                        
                        var minIdx = Math.min(tokenDragStartIdx, currIdx);
                        var maxIdx = Math.max(tokenDragStartIdx, currIdx);
                        
                        tokenDragLastIdx = currIdx;
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            var td = findTokenData(s);
                            if (td) {
                                var atomics = (td.atomic_row_ids && td.atomic_row_ids.length > 0)
                                    ? td.atomic_row_ids
                                    : (td.compound_row_ids && td.compound_row_ids.length > 0)
                                        ? td.compound_row_ids
                                        : (td.row_ids || []);
                                for (var j = 0; j < atomics.length; j++) {
                                    if (tokenDragMode) {
                                        selectedRowIdsMap[String(atomics[j])] = true;
                                    } else {
                                        delete selectedRowIdsMap[String(atomics[j])];
                                    }
                                }
                            }
                        }
                        
                        var evaluatedGroups = [];
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            if (evaluatedGroups.indexOf(s) !== -1) continue;
                            var grp = findCompoundSiblingSpans(s);
                            for (var g = 0; g < grp.length; g++) {
                                evaluatedGroups.push(grp[g]);
                            }
                            
                            var allInDragRange = true;
                            for (var g = 0; g < grp.length; g++) {
                                var grpSpan = grp[g];
                                var grpIdx = -1;
                                for (var idx = 0; idx < tokenSpans.length; idx++) {
                                    if (tokenSpans[idx] === grpSpan) {
                                        grpIdx = idx;
                                        break;
                                    }
                                }
                                if (grpIdx < minIdx || grpIdx > maxIdx) {
                                    allInDragRange = false;
                                    break;
                                }
                            }
                            
                            if (allInDragRange) {
                                for (var g = 0; g < grp.length; g++) {
                                    var gTd = findTokenData(grp[g]);
                                    if (gTd && gTd.compound_row_ids) {
                                        for (var j = 0; j < gTd.compound_row_ids.length; j++) {
                                            if (tokenDragMode) {
                                                selectedRowIdsMap[String(gTd.compound_row_ids[j])] = true;
                                            } else {
                                                delete selectedRowIdsMap[String(gTd.compound_row_ids[j])];
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        
                        updateRowStyles();
                        updateBidirectionalHighlights();
                    } else if (isRmbDragFlipping) {
                        if (e.isTrusted && e.buttons !== undefined && (e.buttons & 2) === 0) {
                            isRmbDragFlipping = false;
                            return;
                        }
                        var currIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                currIdx = k;
                                break;
                            }
                        }
                        if (currIdx === -1 || tokenDragStartIdx === -1) return;
                        if (currIdx === tokenDragStartIdx && !dragOccurred) return;
                        dragOccurred = true;
                        tokenDragLastIdx = currIdx;
                        
                        var minIdx = Math.min(tokenDragStartIdx, currIdx);
                        var maxIdx = Math.max(tokenDragStartIdx, currIdx);
                        
                        for (var k = 0; k < tokenSpans.length; k++) {
                            var s = tokenSpans[k];
                            var inRange = (k >= minIdx && k <= maxIdx);
                            var shouldFlip = inRange ? rmbFlipMode : initialFlippedMap[k];
                            flipWord(s, shouldFlip, false);
                        }
                    }
                });
            })(tokenSpans[i]);
        }
        
        var sourceContainer = document.getElementById('source-container');
        if (sourceContainer) {
            addEvent(sourceContainer, 'contextmenu', function(e) {
                if (window.__selectableTextMode) return;
                e = e || window.event;
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                return false;
            });
        }
        
        var lemmaTable = document.getElementById('lemma-table');
        var tableRows = [];
        if (lemmaTable) {
            var tbodies = lemmaTable.getElementsByTagName('tbody');
            var rowsContainer = tbodies.length > 0 ? tbodies[0] : lemmaTable;
            var allRows = rowsContainer.getElementsByTagName('tr');
            for (var i = 0; i < allRows.length; i++) {
                if (allRows[i].getAttribute('data-row-id') !== null) {
                    tableRows.push(allRows[i]);
                }
            }
        }
        
        var hasHighlightCol = {has_highlight_col};

        for (var i = 0; i < tableRows.length; i++) {
            var row = tableRows[i];
            var rowIdStr = String(row.getAttribute('data-row-id'));
            var isHighlighted = false;
            if (hasHighlightCol && row.getAttribute('data-selected') === '1') {
                isHighlighted = true;
            }
            initialHighlights[rowIdStr] = isHighlighted;
            if (isHighlighted) {
                selectedRowIdsMap[rowIdStr] = true;
            }
        }
        updateRowStyles();
        updateBidirectionalHighlights();
        
        for (var i = 0; i < tableRows.length; i++) {
            (function(row) {
                addEvent(row, 'mousedown', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    var target = e.target || e.srcElement;
                    if (target && target.tagName === 'INPUT') {
                        return;
                    }
                    if (e.button !== 0 && e.button !== 2) {
                        return;
                    }
                    var rowId = parseInt(row.getAttribute('data-row-id'));
                    var rowIdStr = String(rowId);
                    
                    if (e.button === 0) { // LMB
                        isDragSelecting = true;
                        dragOccurred = false;
                        
                        if (e.shiftKey && lastClickedRowId !== null) {
                            dragStartRowId = lastClickedRowId;
                            dragSelectMode = true;
                            
                            initialSelectedMap = {};
                            for (var key in selectedRowIdsMap) {
                                if (selectedRowIdsMap.hasOwnProperty(key)) {
                                    initialSelectedMap[key] = selectedRowIdsMap[key];
                                }
                            }
                            
                            var start = Math.min(parseInt(lastClickedRowId), parseInt(rowId));
                            var end = Math.max(parseInt(lastClickedRowId), parseInt(rowId));
                            for (var j = start; j <= end; j++) {
                                selectedRowIdsMap[String(j)] = true;
                            }
                            lastClickedRowId = rowId;
                        } else {
                            dragStartRowId = rowId;
                            
                            initialSelectedMap = {};
                            for (var key in selectedRowIdsMap) {
                                if (selectedRowIdsMap.hasOwnProperty(key)) {
                                    initialSelectedMap[key] = selectedRowIdsMap[key];
                                }
                            }
                            
                            if (selectedRowIdsMap.hasOwnProperty(rowIdStr)) {
                                delete selectedRowIdsMap[rowIdStr];
                                dragSelectMode = false;
                            } else {
                                selectedRowIdsMap[rowIdStr] = true;
                                dragSelectMode = true;
                            }
                            lastClickedRowId = rowId;
                        }
                        
                        focusedRowId = rowId;
                        updateRowStyles();
                        updateBidirectionalHighlights();
                        
                        if (audioLmbPlay && dragSelectMode) {
                            var tds = row.getElementsByTagName('td');
                            var lemma = "";
                            var inflection = "";
                            for (var m = 0; m < tds.length; m++) {
                                if (tds[m].getAttribute('data-col') === '{lemma_col_name}') {
                                    lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                                }
                                if (tds[m].getAttribute('data-col') === '{inflected_col_name}') {
                                    inflection = (tds[m].textContent || tds[m].innerText || "").trim();
                                }
                            }
                            var textToPlay = (audioLmbSource === 'inflection' && inflection) ? inflection : lemma;
                            if (!textToPlay) textToPlay = lemma || inflection;
                            var sourceLang = (document.getElementById('session-lang').textContent || document.getElementById('session-lang').innerText || 'en').trim();
                            playAudio(textToPlay, sourceLang);
                        }
                    } else if (e.button === 2) { // RMB
                        if (audioRmbPlay) {
                            var tds = row.getElementsByTagName('td');
                            var translation = "";
                            for (var m = 0; m < tds.length; m++) {
                                if (tds[m].getAttribute('data-col') === 'WordDestination') {
                                    translation = (tds[m].textContent || tds[m].innerText || "").trim();
                                }
                            }
                            var targetLang = (document.getElementById('session-target-lang').textContent || document.getElementById('session-target-lang').innerText || 'ru').trim();
                            playAudio(translation, targetLang);
                        }
                    }
                    
                    if (e.preventDefault) {
                        e.preventDefault();
                    } else {
                        e.returnValue = false;
                    }
                });
                
                addEvent(row, 'contextmenu', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    return false;
                });
                
                addEvent(row, 'mouseover', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    if (isDragSelecting) {
                        if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                            isDragSelecting = false;
                            notifyAHKSelection();
                            return;
                        }
                        dragOccurred = true;
                        var rowId = parseInt(row.getAttribute('data-row-id'));
                        
                        // Reset to the state before the current drag gesture started
                        selectedRowIdsMap = {};
                        for (var key in initialSelectedMap) {
                            if (initialSelectedMap.hasOwnProperty(key)) {
                                selectedRowIdsMap[key] = initialSelectedMap[key];
                            }
                        }
                        
                        // Apply the drag selection range from dragStartRowId to current rowId
                        var start = Math.min(dragStartRowId, rowId);
                        var end = Math.max(dragStartRowId, rowId);
                        for (var j = start; j <= end; j++) {
                            var rIdStr = String(j);
                            if (dragSelectMode) {
                                selectedRowIdsMap[rIdStr] = true;
                            } else {
                                delete selectedRowIdsMap[rIdStr];
                            }
                        }
                        
                        focusedRowId = rowId;
                        updateRowStyles();
                        updateBidirectionalHighlights();
                    }
                });
                
                var tds = row.getElementsByTagName('td');
                for (var j = 0; j < tds.length; j++) {
                    if (tds[j].classList.contains('editable')) {
                        (function(cell) {
                            addEvent(cell, 'click', function(e) {
                                if (window.__selectableTextMode) return;
                                lastClickedCell = cell;
                            });
                            addEvent(cell, 'mouseover', function(e) {
                                if (window.__selectableTextMode) return;
                                lastHoveredCell = cell;
                            });
                            addEvent(cell, 'mouseout', function(e) {
                                if (window.__selectableTextMode) return;
                                if (lastHoveredCell === cell) {
                                    lastHoveredCell = null;
                                }
                            });
                            addEvent(cell, 'dblclick', function() {
                                if (window.__selectableTextMode) return;
                                makeEditable(cell);
                            });
                        })(tds[j]);
                    }
                }
            })(tableRows[i]);
        }
        
        function handleMouseUp(e) {
            e = e || window.event;
            var needNotify = false;
            if (isDragSelecting || isTokenDragSelecting || isRmbDragFlipping) {
                if (dragOccurred) {
                    justFinishedDrag = true;
                    setTimeout(function() {
                        justFinishedDrag = false;
                    }, 50);
                }
                
                if (isTokenDragSelecting && tokenDragMode && audioLmbPlay && tokenDragStartIdx !== -1) {
                    var minIdx = (tokenDragLastIdx !== -1) ? Math.min(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    var maxIdx = (tokenDragLastIdx !== -1) ? Math.max(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    if (dragOccurred && minIdx !== maxIdx) {
                        var dragWords = [];
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var term = getSingleTokenWordsToPlay(tokenSpans[k], audioLmbSource, audioLmbChainMode);
                            if (term) {
                                dragWords.push(term);
                            }
                        }
                        if (dragWords.length > 0) {
                            var sourceLang = (document.getElementById('session-lang').textContent || document.getElementById('session-lang').innerText || 'en').trim();
                            if (audioLmbChainMode === 'separate' || audioLmbChainMode === 'per_word') {
                                playAudio(dragWords.join(' ||| '), sourceLang);
                            } else {
                                var dragText = dragWords.join(' ');
                                if (dragText) {
                                    playAudio(dragText, sourceLang);
                                }
                            }
                        }
                    } else {
                        var targetSpan = mousedownTargetSpan || (tokenDragStartIdx !== -1 ? tokenSpans[tokenDragStartIdx] : null);
                        if (targetSpan) {
                            var singleText = getSingleTokenWordsToPlay(targetSpan, audioLmbSource, audioLmbChainMode);
                            if (singleText) {
                                var sourceLang = (document.getElementById('session-lang').textContent || document.getElementById('session-lang').innerText || 'en').trim();
                                playAudio(singleText, sourceLang);
                            }
                        }
                    }
                } else if (isRmbDragFlipping && rmbFlipMode && audioRmbPlay && tokenDragStartIdx !== -1) {
                    var minIdx = (tokenDragLastIdx !== -1) ? Math.min(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    var maxIdx = (tokenDragLastIdx !== -1) ? Math.max(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    var targetLang = (document.getElementById('session-target-lang').textContent || document.getElementById('session-target-lang').innerText || 'ru').trim();

                    if (dragOccurred && minIdx !== maxIdx) {
                        var dragTranslations = [];
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            var grp = findCompoundSiblingSpans(s);
                            var rawTrans = (grp && grp.length > 1)
                                ? [getSpanSpecificTranslation(s, grp)]
                                : getRawRowTranslations(s);
                            if (!rawTrans || rawTrans.length === 0) {
                                var wt = getWordTranslation(s);
                                if (wt) rawTrans = [wt];
                            }
                            var delim = (audioRmbChainMode === 'separate' || audioRmbChainMode === 'per_word') ? ' ||| ' : ' ';
                            var cleanParts = [];
                            for (var t = 0; t < rawTrans.length; t++) {
                                var c = sanitizeSpokenText(rawTrans[t]);
                                if (c) cleanParts.push(c);
                            }
                            if (cleanParts.length > 0) {
                                dragTranslations.push(cleanParts.join(delim));
                            }
                        }
                        if (dragTranslations.length > 0) {
                            if (audioRmbChainMode === 'separate' || audioRmbChainMode === 'per_word') {
                                playAudio(dragTranslations.join(' ||| '), targetLang);
                            } else {
                                var dragText = dragTranslations.join(' ');
                                if (dragText) {
                                    playAudio(dragText, targetLang);
                                }
                            }
                        }
                    } else {
                        var targetSpan = mousedownTargetSpan || (tokenDragStartIdx !== -1 ? tokenSpans[tokenDragStartIdx] : null);
                        if (targetSpan) {
                            var grp = findCompoundSiblingSpans(targetSpan);
                            var rawTrans = (grp && grp.length > 1)
                                ? [getSpanSpecificTranslation(targetSpan, grp)]
                                : getRawRowTranslations(targetSpan);
                            if (!rawTrans || rawTrans.length === 0) {
                                var wt = getWordTranslation(targetSpan);
                                if (wt) rawTrans = [wt];
                            }
                            var delim = (audioRmbChainMode === 'separate' || audioRmbChainMode === 'per_word') ? ' ||| ' : ' ';
                            var cleanParts = [];
                            for (var t = 0; t < rawTrans.length; t++) {
                                var c = sanitizeSpokenText(rawTrans[t]);
                                if (c) cleanParts.push(c);
                            }
                            if (cleanParts.length > 0) {
                                playAudio(cleanParts.join(delim), targetLang);
                            }
                        }
                    }
                }
                
                isDragSelecting = false;
                isTokenDragSelecting = false;
                isRmbDragFlipping = false;
                needNotify = true;
            }
            mousedownTargetSpan = null;
            tokenDragStartIdx = -1;
            tokenDragLastIdx = -1;
            if (needNotify) {
                notifyAHKSelection();
            }
        }
        addEvent(document, 'mouseup', handleMouseUp);
        addEvent(window, 'mouseup', handleMouseUp);
        
        addEvent(document, 'contextmenu', function(e) {
            if (window.__selectableTextMode) return;
            if (justFinishedDrag) {
                e = e || window.event;
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                return false;
            }
        });
        
        addEvent(document, 'keydown', function(e) {
            e = e || window.event;
            var activeEl = document.activeElement;
            if (activeEl && activeEl.tagName === 'INPUT') return;
            
            var keyCode = e.keyCode;
            if (e.ctrlKey && keyCode === 90) { // Ctrl+Z
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.undo) window.undo();
                return;
            } else if (e.ctrlKey && keyCode === 89) { // Ctrl+Y
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.redo) window.redo();
                return;
            } else if (e.ctrlKey && keyCode === 65) { // Ctrl+A
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (typeof tableRows !== 'undefined' && tableRows.length > 0) {
                    for (var i = 0; i < tableRows.length; i++) {
                        var rowId = String(tableRows[i].getAttribute('data-row-id'));
                        selectedRowIdsMap[rowId] = true;
                    }
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    if (typeof notifyAHKSelection !== 'undefined') {
                        notifyAHKSelection();
                    }
                }
                return;
            }
            if (keyCode === 27) { // Escape key
                if (window.clearMVPBookmarks) window.clearMVPBookmarks();
                clearAllSelections();
                updateBidirectionalHighlights();
                notifyAHKSelection();
                return;
            }
            if (keyCode === 40 || keyCode === 38) { // ArrowDown or ArrowUp
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (tableRows.length === 0) return;
                
                if (focusedRowId === null) {
                    focusedRowId = 0;
                } else {
                    if (keyCode === 40) {
                        focusedRowId = Math.min(focusedRowId + 1, tableRows.length - 1);
                    } else {
                        focusedRowId = Math.max(focusedRowId - 1, 0);
                    }
                }
                updateRowFocus();
            } else if (keyCode === 46) { // Delete
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.deleteSelectedRows) {
                    window.deleteSelectedRows();
                }
            } else if (keyCode === 32) { // Space
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (focusedRowId !== null) {
                    if (selectedRowIdsMap.hasOwnProperty(focusedRowId)) {
                        delete selectedRowIdsMap[focusedRowId];
                    } else {
                        selectedRowIdsMap[focusedRowId] = true;
                    }
                    lastClickedRowId = focusedRowId;
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    notifyAHKSelection();
                }
            } else if (keyCode === 113) { // F2
                var cellToEdit = null;
                if (lastHoveredCell) {
                    var rId = parseInt(lastHoveredCell.parentElement.getAttribute('data-row-id'));
                    if (rId === focusedRowId) {
                        cellToEdit = lastHoveredCell;
                    }
                }
                if (!cellToEdit && lastClickedCell) {
                    var rId = parseInt(lastClickedCell.parentElement.getAttribute('data-row-id'));
                    if (rId === focusedRowId) {
                        cellToEdit = lastClickedCell;
                    }
                }
                if (!cellToEdit && focusedRowId !== null) {
                    var activeRow = null;
                    for (var k = 0; k < tableRows.length; k++) {
                        if (tableRows[k].getAttribute('data-row-id') == focusedRowId) {
                            activeRow = tableRows[k];
                            break;
                        }
                    }
                    if (activeRow) {
                        var tds = activeRow.getElementsByTagName('td');
                        for (var k = 0; k < tds.length; k++) {
                            if (tds[k].classList.contains('editable')) {
                                cellToEdit = tds[k];
                                break;
                            }
                        }
                    }
                }
                if (cellToEdit) {
                    makeEditable(cellToEdit);
                }
            }
        });
        
        addEvent(document, 'click', function(e) {
            if (justFinishedDrag) {
                justFinishedDrag = false;
                return;
            }
        });
        
        function clearAllSelections() {
            selectedRowIdsMap = {};
            lastClickedRowId = null;
            updateRowStyles();
            updateBidirectionalHighlights();
        }
        
        window.clearAllSelectionsAndNotify = function() {
            clearAllSelections();
            notifyAHKSelection();
            if (window.forceRepaint) window.forceRepaint();
        };
        
        function toggleRowSelection(rowId, forceState) {
            var rIdStr = String(rowId);
            if (forceState) {
                selectedRowIdsMap[rIdStr] = true;
            } else {
                if (selectedRowIdsMap.hasOwnProperty(rIdStr)) {
                    delete selectedRowIdsMap[rIdStr];
                } else {
                    selectedRowIdsMap[rIdStr] = true;
                }
            }
            updateRowStyles();
        }
        
        function updateRowStyles() {
            for (var i = 0; i < tableRows.length; i++) {
                var row = tableRows[i];
                var rowIdStr = String(row.getAttribute('data-row-id'));
                if (selectedRowIdsMap.hasOwnProperty(rowIdStr)) {
                    row.classList.add('selected');
                } else {
                    row.classList.remove('selected');
                }
            }
        }
        
        function updateRowFocus() {
            for (var i = 0; i < tableRows.length; i++) {
                var row = tableRows[i];
                var rowId = parseInt(row.getAttribute('data-row-id'));
                if (rowId === focusedRowId) {
                    row.style.outline = '1px solid #58a6ff';
                    row.scrollIntoView({ block: 'nearest' });
                } else {
                    row.style.outline = 'none';
                }
            }
        }
        
        function updateBidirectionalHighlights() {
            for (var i = 0; i < tokenSpans.length; i++) {
                var span = tokenSpans[i];
                try {
                    span.classList.remove('highlight-orange-active');
                    span.classList.remove('highlight-purple-active');
                    span.classList.remove('active-subtoken');
                } catch(e) {}
            }
            
            for (var rId in selectedRowIdsMap) {
                if (!selectedRowIdsMap.hasOwnProperty(rId)) continue;
                var rowId = parseInt(rId);
                for (var i = 0; i < tokenMap.length; i++) {
                    var token = tokenMap[i];
                    if (token.row_ids && token.row_ids.indexOf(rowId) !== -1) {
                        var span = null;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k].getAttribute('data-word-idx') == token.visual_idx) {
                                span = tokenSpans[k];
                                break;
                            }
                        }
                        if (span) {
                            try {
                                if (span.classList.contains('highlight-purple')) {
                                    span.classList.add('highlight-purple-active');
                                } else if (span.classList.contains('highlight-orange')) {
                                    span.classList.add('highlight-orange-active');
                                }
                            } catch(e) {}
                        }
                    }
                }
            }

            if (mousedownTargetSpan && mousedownTargetSpan.classList && 
                (mousedownTargetSpan.classList.contains('highlight-orange-active') || mousedownTargetSpan.classList.contains('highlight-purple-active'))) {
                try {
                    mousedownTargetSpan.classList.add('active-subtoken');
                } catch(e) {}
            }
        }
        
        function getSelectedRowsArray() {
            var arr = [];
            for (var k in selectedRowIdsMap) {
                if (selectedRowIdsMap.hasOwnProperty(k)) {
                    arr.push(parseInt(k));
                }
            }
            return arr;
        }
        
        function notifyAHKSelection() {
            if (window.ahkCall) {
                try {
                    window.ahkCall('selection', getSelectedRowsArray().join(','));
                    window.ahkCall('dirty', window.isDirty() ? 'true' : 'false');
                } catch (e) {
                }
            }
        }
        
        function makeEditable(cell) {
            if (cell.getElementsByTagName('input').length > 0) return;
            
            var scrollDiv = cell.querySelector('.scrollable-cell');
            var originalValue = scrollDiv ? (scrollDiv.textContent || scrollDiv.innerText) : (cell.textContent || cell.innerText || "");
            var colName = cell.getAttribute('data-col');
            var trParent = cell.parentElement;
            var rowId = trParent.getAttribute('data-row-id');
            var tokenOrder = trParent.getAttribute('data-token-order');
            var tokenOrderVal = (tokenOrder !== null && tokenOrder !== '') ? parseInt(tokenOrder, 10) : parseInt(rowId, 10);
            var sentenceIdx = trParent.getAttribute('data-sentence-idx');
            var sentIdxVal = (sentenceIdx !== null && sentenceIdx !== '') ? parseInt(sentenceIdx, 10) : 1;
            
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'edit-input';
            input.value = originalValue;
            input.style.width = '100%';
            input.style.boxSizing = 'border-box';
            input.style.background = '{input_bg}';
            input.style.color = '{text_color}';
            input.style.border = '1px solid {input_border}';
            input.style.borderRadius = '4px';
            input.style.padding = '4px';
            
            cell.innerHTML = '';
            if (!cell.classList.contains('editing')) {
                cell.classList.add('editing');
            }
            cell.appendChild(input);
            input.focus();
            try {
                input.select();
            } catch(e) {}
            
            window.cancelActiveEdit = function() {
                window.cancelActiveEdit = null;
                cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(originalValue));
                cell.appendChild(div);
                cell.classList.remove('editing');
                if (window.forceRepaint) window.forceRepaint();
            };
            
            function commit() {
                if (!window.cancelActiveEdit) return;
                window.cancelActiveEdit = null;
                var newValue = input.value;
                cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(newValue));
                cell.appendChild(div);
                cell.classList.remove('editing');
                if (newValue !== originalValue) {
                    var action = {
                        type: 'edit',
                        rowId: parseInt(rowId, 10),
                        tokenOrder: tokenOrderVal,
                        sentenceIdx: sentIdxVal,
                        column: colName,
                        oldValue: originalValue,
                        newValue: newValue,
                        cell: cell
                    };
                    pushHistory(action);
                    rebuildDeltas();
                    touchedCells[rowId + '_' + colName] = true;
                }
                if (window.forceRepaint) window.forceRepaint();
            }
            window.commitActiveEdit = commit;
            
            addEvent(input, 'keydown', function(e) {
                e = e || window.event;
                var keyCode = e.keyCode;
                if (e.ctrlKey && keyCode === 65) { // Ctrl+A
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    input.select();
                } else if (keyCode === 13) { // Enter
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    commit();
                } else if (keyCode === 27) { // Escape
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    if (window.cancelActiveEdit) window.cancelActiveEdit();
                } else if (keyCode === 9) { // Tab
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    commit();
                    
                    var tds = document.getElementsByTagName('td');
                    var editables = [];
                    for (var k = 0; k < tds.length; k++) {
                        if (tds[k].classList.contains('editable')) {
                            editables.push(tds[k]);
                        }
                    }
                    
                    var idx = -1;
                    for (var k = 0; k < editables.length; k++) {
                        if (editables[k] === cell) {
                            idx = k;
                            break;
                        }
                    }
                    var nextIdx = e.shiftKey ? idx - 1 : idx + 1;
                    if (nextIdx >= 0 && nextIdx < editables.length) {
                        makeEditable(editables[nextIdx]);
                    }
                }
            });
            
            addEvent(input, 'blur', function() {
                commit();
            });
        }
        
        window.getSelectedRows = function() {
            return JSON.stringify(getSelectedRowsArray());
        };
        
        window.setSelectedRows = function(rowsJsonStr) {
            try {
                var arr = JSON.parse(rowsJsonStr);
                selectedRowIdsMap = {};
                for (var i = 0; i < arr.length; i++) {
                    selectedRowIdsMap[String(arr[i])] = true;
                }
                updateRowStyles();
                updateBidirectionalHighlights();
                if (window.forceRepaint) window.forceRepaint();
            } catch(e) {}
        };
        
        window.deleteSelectedRows = function() {
            var selected = getSelectedRowsArray();
            if (selected.length === 0) return;
            var tOrders = [];
            var sIndices = [];
            for (var i = 0; i < selected.length; i++) {
                var rId = selected[i];
                var tOrd = rId;
                var sIdx = 1;
                for (var k = 0; k < tableRows.length; k++) {
                    if (parseInt(tableRows[k].getAttribute('data-row-id'), 10) === rId) {
                        var toAttr = tableRows[k].getAttribute('data-token-order');
                        if (toAttr !== null && toAttr !== '') tOrd = parseInt(toAttr, 10);
                        var siAttr = tableRows[k].getAttribute('data-sentence-idx');
                        if (siAttr !== null && siAttr !== '') sIdx = parseInt(siAttr, 10);
                        break;
                    }
                }
                tOrders.push(tOrd);
                sIndices.push(sIdx);
            }
            var action = {
                type: 'delete',
                rowIds: selected,
                tokenOrders: tOrders,
                sentenceIndices: sIndices
            };
            pushHistory(action);
            applyAction(action);
            rebuildDeltas();
        };
        
        function pushHistory(action) {
            historyStack.splice(historyIndex + 1);
            historyStack.push(action);
            historyIndex++;
        }
        
        function applyAction(action) {
            if (action.type === 'edit') {
                action.cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(action.newValue));
                action.cell.appendChild(div);
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    for (var i = 0; i < tableRows.length; i++) {
                        if (parseInt(tableRows[i].getAttribute('data-row-id')) === rId) {
                            tableRows[i].style.display = 'none';
                            break;
                        }
                    }
                }
                clearAllSelections();
            }
        }
        
        function revertAction(action) {
            if (action.type === 'edit') {
                action.cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(action.oldValue));
                action.cell.appendChild(div);
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    for (var i = 0; i < tableRows.length; i++) {
                        if (parseInt(tableRows[i].getAttribute('data-row-id')) === rId) {
                            tableRows[i].style.display = '';
                            break;
                        }
                    }
                }
            }
        }
        
        window.undo = function() {
            if (historyIndex < 0) return;
            var action = historyStack[historyIndex];
            historyIndex--;
            revertAction(action);
            rebuildDeltas();
        };
        
        window.redo = function() {
            if (historyIndex >= historyStack.length - 1) return;
            historyIndex++;
            var action = historyStack[historyIndex];
            applyAction(action);
            rebuildDeltas();
        };
        
        function rebuildDeltas() {
            deltas = [];
            var tds = document.getElementsByTagName('td');
            for (var k = 0; k < tds.length; k++) {
                tds[k].classList.remove('dirty');
            }
            
            for (var i = 0; i <= historyIndex; i++) {
                var action = historyStack[i];
                if (action.type === 'edit') {
                    var found = false;
                    for (var k = 0; k < deltas.length; k++) {
                        if (deltas[k].row_id === action.rowId && deltas[k].column === action.column) {
                            deltas[k].value = action.newValue;
                            deltas[k].token_order = action.tokenOrder;
                            deltas[k].sentence_idx = action.sentenceIdx;
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        deltas.push({
                            row_id: action.rowId,
                            token_order: action.tokenOrder,
                            sentence_idx: action.sentenceIdx,
                            column: action.column,
                            value: action.newValue
                        });
                    }
                } else if (action.type === 'delete') {
                    for (var j = 0; j < action.rowIds.length; j++) {
                        var rId = action.rowIds[j];
                        var tOrd = (action.tokenOrders && action.tokenOrders[j] !== undefined) ? action.tokenOrders[j] : rId;
                        var sIdx = (action.sentenceIndices && action.sentenceIndices[j] !== undefined) ? action.sentenceIndices[j] : 1;
                        deltas.push({
                            row_id: rId,
                            token_order: tOrd,
                            sentence_idx: sIdx,
                            column: '_delete',
                            value: true
                        });
                    }
                }
            }
            
            for (var k = 0; k < deltas.length; k++) {
                var d = deltas[k];
                if (d.column !== '_delete') {
                    for (var j = 0; j < tableRows.length; j++) {
                        if (parseInt(tableRows[j].getAttribute('data-row-id')) === d.row_id) {
                            var tdst = tableRows[j].getElementsByTagName('td');
                            for (var m = 0; m < tdst.length; m++) {
                                if (tdst[m].getAttribute('data-col') === d.column) {
                                    if (!tdst[m].classList.contains('dirty')) {
                                        tdst[m].classList.add('dirty');
                                    }
                                    break;
                                }
                            }
                            break;
                        }
                    }
                }
            }
            
            if (window.ahkCall) {
                try {
                    window.ahkCall('dirty', deltas.length > 0 ? 'true' : 'false');
                } catch (e) {
                }
            }
            
            if (typeof updateToolbarState === 'function') updateToolbarState();

            // Force MSHTML repaint/reflow after undo/redo
            if (window.forceRepaint) window.forceRepaint();
        }
        
        window.getDeltas = function() {
            var mergedDeltas = [];
            for (var i = 0; i < deltas.length; i++) {
                mergedDeltas.push(deltas[i]);
            }
            if (hasHighlightCol) {
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    var tOrdStr = row.getAttribute('data-token-order');
                    var tOrdVal = (tOrdStr !== null && tOrdStr !== '') ? parseInt(tOrdStr, 10) : parseInt(rowIdStr, 10);
                    var sIdxStr = row.getAttribute('data-sentence-idx');
                    var sIdxVal = (sIdxStr !== null && sIdxStr !== '') ? parseInt(sIdxStr, 10) : 1;
                    var currentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                    var initiallySelected = initialHighlights[rowIdStr] || false;
                    if (currentlySelected !== initiallySelected) {
                        mergedDeltas.push({
                            row_id: parseInt(rowIdStr, 10),
                            token_order: tOrdVal,
                            sentence_idx: sIdxVal,
                            column: '{selected_col_name}',
                            value: currentlySelected ? '1' : ''
                        });
                    }
                }
            }
            return JSON.stringify(mergedDeltas);
        };
        
        window.clearDirty = function() {
            historyStack = [];
            historyIndex = -1;
            deltas = [];
            if (hasHighlightCol) {
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    initialHighlights[rowIdStr] = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                }
            }
            var tds = document.getElementsByTagName('td');
            for (var k = 0; k < tds.length; k++) {
                tds[k].classList.remove('dirty');
            }
            if (window.ahkCall) {
                try {
                    window.ahkCall('dirty', 'false');
                } catch (e) {
                }
            }
            if (typeof updateToolbarState === 'function') updateToolbarState();
        };
        
        window.isDirty = function() {
            if (deltas.length > 0) return true;
            if (hasHighlightCol) {
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    var currentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                    var initiallySelected = initialHighlights[rowIdStr] || false;
                    if (currentlySelected !== initiallySelected) {
                        return true;
                    }
                }
            }
            return false;
        };
        
        window.editFocusedCell = function() {
            var cellToEdit = null;
            if (lastHoveredCell) {
                var rId = parseInt(lastHoveredCell.parentElement.getAttribute('data-row-id'));
                if (rId === focusedRowId) {
                    cellToEdit = lastHoveredCell;
                }
            }
            if (!cellToEdit && lastClickedCell) {
                var rId = parseInt(lastClickedCell.parentElement.getAttribute('data-row-id'));
                if (rId === focusedRowId) {
                    cellToEdit = lastClickedCell;
                }
            }
            if (!cellToEdit && focusedRowId !== null) {
                for (var k = 0; k < tableRows.length; k++) {
                    if (tableRows[k].getAttribute('data-row-id') == focusedRowId) {
                        var tds = tableRows[k].getElementsByTagName('td');
                        for (var j = 0; j < tds.length; j++) {
                            if (tds[j].classList.contains('editable')) {
                                cellToEdit = tds[j];
                                break;
                            }
                        }
                        break;
                    }
                }
            }
            if (cellToEdit) {
                makeEditable(cellToEdit);
            }
            if (window.forceRepaint) window.forceRepaint();
        };
        
        window.selectAllInActiveEdit = function() {
            var el = document.activeElement;
            if (el && el.tagName === 'INPUT') {
                el.select();
            }
        };
        
        window.selectAllRows = function() {
            if (typeof tableRows !== 'undefined' && tableRows.length > 0) {
                for (var i = 0; i < tableRows.length; i++) {
                    var rowId = String(tableRows[i].getAttribute('data-row-id'));
                    selectedRowIdsMap[rowId] = true;
                }
                updateRowStyles();
                updateBidirectionalHighlights();
                if (typeof notifyAHKSelection !== 'undefined') {
                    notifyAHKSelection();
                }
            }
        };
        
        window.copySelection = function() {
            try {
                document.execCommand('copy');
            } catch(e) {}
        };
        
        function updateToolbarState() {
            var saveBtn = document.getElementById('kw-btn-save');
            if (saveBtn) {
                saveBtn.disabled = !window.isDirty();
            }
        }
        window.updateToolbarState = updateToolbarState;

        window.showToast = function(msg, type, durationMs) {
            type = type || 'info';
            durationMs = durationMs || 3000;
            var container = document.getElementById('kw-toast-container');
            if (!container) return;
            var toast = document.createElement('div');
            toast.className = 'kw-toast kw-toast-' + type;
            
            var icon = 'i';
            if (type === 'success') icon = '✓';
            else if (type === 'warning') icon = '!';
            else if (type === 'error') icon = '✕';
            
            toast.innerHTML = '<span class="kw-toast-icon">' + icon + '</span><span class="kw-toast-msg">' + escapeHtml(msg) + '</span>';
            
            function dismiss() {
                if (toast.classList.contains('kw-toast-hiding')) return;
                toast.classList.add('kw-toast-hiding');
                setTimeout(function() {
                    if (toast.parentNode) toast.parentNode.removeChild(toast);
                }, 250);
            }
            
            toast.onclick = dismiss;
            container.appendChild(toast);
            setTimeout(dismiss, durationMs);
        };

        function getSessionZid() {
            var el = document.getElementById('session-zid');
            var zidVal = el ? (el.textContent || el.innerText || "").trim() : "";
            if (!zidVal) {
                try {
                    var urlParams = new URLSearchParams(window.location.search);
                    zidVal = urlParams.get('zid') || urlParams.get('session_zid') || "";
                } catch(e) {}
            }
            return zidVal;
        }

        function getSessionLang() {
            var el = document.getElementById('session-lang');
            return el ? (el.textContent || el.innerText || "en").trim() : "en";
        }

        function getApiToken() {
            if (typeof API_TOKEN !== 'undefined' && API_TOKEN && API_TOKEN !== '__API_TOKEN__') {
                return API_TOKEN;
            }
            if (typeof window.API_TOKEN !== 'undefined' && window.API_TOKEN) {
                return window.API_TOKEN;
            }
            try {
                var params = new URLSearchParams(window.location.search);
                return params.get('token') || params.get('api_token') || "";
            } catch(e) {
                return "";
            }
        }
        window.getApiToken = getApiToken;

        window.onSaveClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            if (!window.isDirty()) {
                window.showToast("No changes to save.", "info");
                return;
            }
            if (typeof fetch === 'undefined') {
                window.showToast("Network save unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing, cannot save.", "error");
                return;
            }
            var deltasJson = [];
            try {
                deltasJson = JSON.parse(window.getDeltas());
            } catch(e) {}
            
            var saveBtn = document.getElementById('kw-btn-save');
            if (saveBtn) saveBtn.disabled = true;

            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                deltas: deltasJson,
                language: getSessionLang()
            };
            if (tok) bodyPayload.token = tok;

            fetch('/session/save', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success')) {
                    window.clearDirty();
                    updateToolbarState();
                    window.showToast("Edits saved successfully", "success");
                } else {
                    updateToolbarState();
                    window.showToast("Save failed: " + (resObj.data.message || resObj.data.error || "Server error"), "error");
                }
            })
            .catch(function(err) {
                updateToolbarState();
                window.showToast("Save error: " + (err.message || String(err)), "error");
            });
        };

        window.onUpdateClick = window.onUpdateClick || function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            window.location.reload();
        };

        window.onRetextClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            if (typeof fetch === 'undefined') {
                window.showToast("Retext unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing.", "error");
                return;
            }
            window.showToast("Retexting session...", "info");
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                language: getSessionLang()
            };
            if (tok) bodyPayload.token = tok;

            fetch('/session/retext', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success' || (resObj.data.data && (resObj.data.data.ok || resObj.data.data.status === 'success')) || resObj.data.retext_started)) {
                    window.showToast("Retext completed", "success");
                    if (window.onUpdateClick) {
                        window.onUpdateClick();
                    } else if (window.location && window.location.reload) {
                        window.location.reload();
                    }
                } else {
                    window.showToast("Retext failed: " + (resObj.data.message || (resObj.data.data && resObj.data.data.message) || resObj.data.error || "Server error"), "error");
                }
            })
            .catch(function(err) {
                window.showToast("Retext error: " + (err.message || String(err)), "error");
            });
        };

        window.onRewordClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            var rows = getSelectedRowsArray();
            if (!rows.length) {
                window.showToast("Please select rows to re-word.", "warning");
                return;
            }
            if (typeof fetch === 'undefined') {
                window.showToast("Reword unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing.", "error");
                return;
            }
            window.showToast("Re-wording " + rows.length + " rows...", "info");
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                row_ids: rows,
                language: getSessionLang()
            };
            if (tok) bodyPayload.token = tok;

            fetch('/session/reword', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success' || (resObj.data.data && (resObj.data.data.ok || resObj.data.data.status === 'success')) || resObj.data.reprocess_started)) {
                    window.showToast("Re-word completed for " + rows.length + " rows", "success");
                    if (window.onUpdateClick) {
                        window.onUpdateClick();
                    } else if (window.location && window.location.reload) {
                        window.location.reload();
                    }
                } else {
                    window.showToast("Re-word failed: " + (resObj.data.message || (resObj.data.data && resObj.data.data.message) || resObj.data.error || "Server error"), "error");
                }
            })
            .catch(function(err) {
                window.showToast("Re-word error: " + (err.message || String(err)), "error");
            });
        };

        window.onSendToAnkiClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            var rows = getSelectedRowsArray();
            if (!rows.length) {
                window.showToast("Please select rows to export.", "warning");
                return;
            }
            if (typeof fetch === 'undefined') {
                window.showToast("Export unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing.", "error");
                return;
            }
            window.showToast("Exporting to Anki...", "info");
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                row_ids: rows,
                language: getSessionLang()
            };
            if (tok) bodyPayload.token = tok;

            fetch('/session/export', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success' || resObj.data.import_complete || resObj.data.import_started)) {
                    window.showToast("✓ " + rows.length + " cards exported to Anki", "success");
                } else {
                    window.showToast("Export failed: " + (resObj.data.message || resObj.data.error || "Server error"), "error");
                }
            })
            .catch(function(err) {
                window.showToast("Export error: " + (err.message || String(err)), "error");
            });
        };

        window.onHandToolClick = function() {
            var newMode = !window.__persistentSelectableMode;
            window.setSelectableTextMode(newMode, newMode);
            var handBtn = document.getElementById('kw-btn-hand-tool');
            if (handBtn) {
                if (newMode) handBtn.classList.add('active');
                else handBtn.classList.remove('active');
            }
            window.showToast(newMode ? "Text selection mode active" : "Hand tool / Table interaction active", "info", 1500);
        };

        window.onDeleteClick = function() {
            var rows = getSelectedRowsArray();
            if (!rows.length) {
                window.showToast("Please select rows to delete.", "warning");
                return;
            }
            window.deleteSelectedRows();
            updateToolbarState();
            window.showToast("Deleted " + rows.length + " rows (Ctrl+Z to undo)", "info", 2000);
        };

        var LANGUAGE_NAME_MAP = {
            "en": "English",
            "de": "German",
            "ru": "Russian",
            "fr": "French",
            "es": "Spanish",
            "it": "Italian",
            "zh": "Chinese",
            "ja": "Japanese",
            "pt": "Portuguese",
            "nl": "Dutch",
            "pl": "Polish",
            "uk": "Ukrainian"
        };

        function getLanguageName(code) {
            if (!code) return "";
            return LANGUAGE_NAME_MAP[code.toLowerCase()] || code.toUpperCase();
        }

        function getTheme() {
            if (document.body && document.body.classList) {
                if (document.body.classList.contains('theme-light')) return 'light';
                if (document.body.classList.contains('theme-white')) return 'white';
            }
            return 'dark';
        }

        function getTextMode() {
            var el = document.getElementById('text-mode');
            return el ? (el.textContent || el.innerText || 'single').trim() : 'single';
        }

        window.showLanguageVerificationModal = function(info) {
            info = info || {};
            var detCode = info.detected_language || "";
            var expCode = info.expected_language || getSessionLang() || "";
            var detName = info.detected_name || getLanguageName(detCode) || detCode;
            var expName = info.expected_name || getLanguageName(expCode) || expCode;
            var detLabel = detName + (detCode ? " (" + detCode + ")" : "");
            var expLabel = expName + (expCode ? " (" + expCode + ")" : "");
            var promptMsg = "The text appears to be " + detLabel + ", but the active profile is " + expLabel + ".\\n\\nSwitch language to " + detName + "?";

            var modal = document.getElementById('kw-lang-modal');
            var bodyEl = document.getElementById('kw-lang-modal-body');
            if (bodyEl) bodyEl.textContent = promptMsg;
            if (modal) {
                modal.style.display = 'flex';
                window.__langMismatchInfo = info;
                var yesBtn = document.getElementById('kw-btn-lang-yes');
                if (yesBtn) yesBtn.focus();
            }
        };

        window.hideLanguageVerificationModal = function() {
            var modal = document.getElementById('kw-lang-modal');
            if (modal) modal.style.display = 'none';
        };

        function setLangModalLoading(loading) {
            var yesBtn = document.getElementById('kw-btn-lang-yes');
            var noBtn = document.getElementById('kw-btn-lang-no');
            var cancelBtn = document.getElementById('kw-btn-lang-cancel');
            if (yesBtn) yesBtn.disabled = !!loading;
            if (noBtn) noBtn.disabled = !!loading;
            if (cancelBtn) cancelBtn.disabled = !!loading;
        }

        function extractChildSessions(children) {
            var results = [];
            if (!children || !Array.isArray(children)) return results;
            var curSeq = null;
            for (var i = 0; i < children.length; i++) {
                var item = children[i];
                if (typeof item === 'string') {
                    if (item === '--seq-num' && i + 1 < children.length) {
                        curSeq = children[i + 1];
                        i++;
                    } else if (item === '--restore' && i + 1 < children.length) {
                        var curTsv = children[i + 1];
                        i++;
                        if (curTsv) {
                            var zidMatch = curTsv.match(/(\\d{14})/);
                            var childZid = zidMatch ? zidMatch[1] : null;
                            if (childZid) {
                                results.push({ zid: childZid, seq_num: curSeq || '1' });
                            }
                            curSeq = null;
                        }
                    } else if (item.match && item.match(/(\\d{14})/)) {
                        var zidMatch = item.match(/(\\d{14})/);
                        results.push({ zid: zidMatch[1], seq_num: '1' });
                    }
                } else if (typeof item === 'object' && item !== null) {
                    var z = item.zid || item.session_zid;
                    if (z) {
                        results.push({ zid: z, seq_num: item.seq_num || '1' });
                    }
                }
            }
            return results;
        }

        function spawnChildTabs(children) {
            var childSessions = extractChildSessions(children);
            if (!childSessions || childSessions.length === 0) return;
            var tok = getApiToken();
            var tokenQuery = tok ? '&token=' + encodeURIComponent(tok) : '';
            var urls = [];
            for (var i = 0; i < childSessions.length; i++) {
                var c = childSessions[i];
                var childUrl = '/session/render?session_zid=' + encodeURIComponent(c.zid) + '&seq_num=' + encodeURIComponent(c.seq_num) + '&bypass_lang_check=true' + tokenQuery;
                urls.push(childUrl);
            }
            if (urls.length === 0) return;

            var fallbackOpen = function() {
                for (var j = 0; j < urls.length; j++) {
                    try {
                        window.open(urls[j], '_blank');
                    } catch(e) {
                        console.error("Failed spawning child tab", e);
                    }
                }
            };

            if (typeof fetch !== 'undefined') {
                var headers = { 'Content-Type': 'application/json' };
                if (tok) headers['X-API-Token'] = tok;
                var bodyPayload = { urls: urls, children: childSessions };
                if (tok) bodyPayload.token = tok;
                fetch('/api/v1/spawn-tabs', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(bodyPayload)
                })
                .then(function(res) {
                    if (!res.ok) {
                        fallbackOpen();
                    }
                })
                .catch(function(e) {
                    console.warn("Server spawn-tabs failed, falling back to window.open", e);
                    fallbackOpen();
                });
            } else {
                fallbackOpen();
            }
        }

        window.onLangYes = function() {
            var yesBtn = document.getElementById('kw-btn-lang-yes');
            if (yesBtn && yesBtn.disabled) return;

            var info = window.__langMismatchInfo || {};
            var detLang = info.detected_language;
            if (!detLang) {
                window.hideLanguageVerificationModal();
                return;
            }
            var sZid = getSessionZid() || info.session_zid || "";
            var srcContainer = document.getElementById('source-container');
            var srcText = info.text || info.source_text || (srcContainer ? (srcContainer.textContent || srcContainer.innerText || "") : "");
            var detName = info.detected_name || getLanguageName(detLang) || detLang;
            var effMode = getTextMode();
            var tok = getApiToken();
            var tokenQuery = tok ? '&token=' + encodeURIComponent(tok) : '';

            document.title = "Kardenwort - " + detLang + " (" + effMode + ")";
            window.showToast("Switching language to " + detName + "...", "info");
            setLangModalLoading(true);

            if (typeof fetch !== 'undefined') {
                var headers = { 'Content-Type': 'application/json' };
                if (tok) headers['X-API-Token'] = tok;

                var langPayload = { language: detLang };
                if (tok) langPayload.token = tok;

                fetch('/api/v1/set-language', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(langPayload)
                }).catch(function(e) {
                    console.warn("Failed to synchronize language with controller:", e);
                });

                var renderPayload = {
                    session_zid: sZid,
                    zid: sZid,
                    language: detLang,
                    text: srcText,
                    bypass_lang_check: true,
                    theme: getTheme(),
                    text_mode: effMode
                };
                if (tok) renderPayload.token = tok;

                fetch('/api/v1/render', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(renderPayload)
                })
                .then(function(res) {
                    return res.json().then(function(data) { return { ok: res.ok, status: res.status, data: data }; });
                })
                .then(function(resObj) {
                    setLangModalLoading(false);
                    window.hideLanguageVerificationModal();
                    if (resObj.ok && resObj.data) {
                        var childrenList = resObj.data.children || (resObj.data.data && resObj.data.data.children) || [];
                        if (childrenList && childrenList.length > 0) {
                            spawnChildTabs(childrenList);
                        }
                        var html = resObj.data.html || (resObj.data.html_b64 ? decodeURIComponent(escape(atob(resObj.data.html_b64))) : null);
                        if (html) {
                            document.open();
                            document.write(html);
                            document.close();
                            return;
                        }
                    }
                    window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(detLang) + '&bypass_lang_check=true' + tokenQuery;
                })
                .catch(function(err) {
                    setLangModalLoading(false);
                    window.showToast("Language switch failed: " + (err.message || String(err)), "error");
                });
            } else {
                setLangModalLoading(false);
                window.hideLanguageVerificationModal();
                window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(detLang) + '&bypass_lang_check=true' + tokenQuery;
            }
        };

        window.onLangNo = function() {
            var noBtn = document.getElementById('kw-btn-lang-no');
            if (noBtn && noBtn.disabled) return;

            var info = window.__langMismatchInfo || {};
            var expLang = info.expected_language || getSessionLang() || "en";
            var sZid = getSessionZid() || info.session_zid || "";
            var srcContainer = document.getElementById('source-container');
            var srcText = info.text || info.source_text || (srcContainer ? (srcContainer.textContent || srcContainer.innerText || "") : "");
            var expName = info.expected_name || getLanguageName(expLang) || expLang;
            var effMode = getTextMode();
            var tok = getApiToken();
            var tokenQuery = tok ? '&token=' + encodeURIComponent(tok) : '';

            document.title = "Kardenwort - " + expLang + " (" + effMode + ")";
            window.showToast("Processing in " + expName + "...", "info");
            setLangModalLoading(true);

            if (typeof fetch !== 'undefined') {
                var headers = { 'Content-Type': 'application/json' };
                if (tok) headers['X-API-Token'] = tok;

                var renderPayload = {
                    session_zid: sZid,
                    zid: sZid,
                    language: expLang,
                    text: srcText,
                    bypass_lang_check: true,
                    theme: getTheme(),
                    text_mode: effMode
                };
                if (tok) renderPayload.token = tok;

                fetch('/api/v1/render', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(renderPayload)
                })
                .then(function(res) {
                    return res.json().then(function(data) { return { ok: res.ok, status: res.status, data: data }; });
                })
                .then(function(resObj) {
                    setLangModalLoading(false);
                    window.hideLanguageVerificationModal();
                    if (resObj.ok && resObj.data) {
                        var childrenList = resObj.data.children || (resObj.data.data && resObj.data.data.children) || [];
                        if (childrenList && childrenList.length > 0) {
                            spawnChildTabs(childrenList);
                        }
                        var html = resObj.data.html || (resObj.data.html_b64 ? decodeURIComponent(escape(atob(resObj.data.html_b64))) : null);
                        if (html) {
                            document.open();
                            document.write(html);
                            document.close();
                            return;
                        }
                    }
                    window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(expLang) + '&bypass_lang_check=true' + tokenQuery;
                })
                .catch(function(err) {
                    setLangModalLoading(false);
                    window.showToast("Render failed: " + (err.message || String(err)), "error");
                });
            } else {
                setLangModalLoading(false);
                window.hideLanguageVerificationModal();
                window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(expLang) + '&bypass_lang_check=true' + tokenQuery;
            }
        };

        window.onLangCancel = function() {
            window.hideLanguageVerificationModal();
            window.showToast("Language verification cancelled.", "info");
        };

        var btnSave = document.getElementById('kw-btn-save');
        if (btnSave) addEvent(btnSave, 'click', window.onSaveClick);
        var btnUpdate = document.getElementById('kw-btn-update');
        if (btnUpdate) addEvent(btnUpdate, 'click', window.onUpdateClick);
        var btnRetext = document.getElementById('kw-btn-retext');
        if (btnRetext) addEvent(btnRetext, 'click', window.onRetextClick);
        var btnReword = document.getElementById('kw-btn-reword');
        if (btnReword) addEvent(btnReword, 'click', window.onRewordClick);
        var btnExport = document.getElementById('kw-btn-export');
        if (btnExport) addEvent(btnExport, 'click', window.onSendToAnkiClick);
        var btnHand = document.getElementById('kw-btn-hand-tool');
        if (btnHand) addEvent(btnHand, 'click', window.onHandToolClick);
        var btnDelete = document.getElementById('kw-btn-delete');
        if (btnDelete) addEvent(btnDelete, 'click', window.onDeleteClick);

        var btnLangYes = document.getElementById('kw-btn-lang-yes');
        if (btnLangYes) addEvent(btnLangYes, 'click', window.onLangYes);
        var btnLangNo = document.getElementById('kw-btn-lang-no');
        if (btnLangNo) addEvent(btnLangNo, 'click', window.onLangNo);
        var btnLangCancel = document.getElementById('kw-btn-lang-cancel');
        if (btnLangCancel) addEvent(btnLangCancel, 'click', window.onLangCancel);

        addEvent(document, 'keydown', function(e) {
            e = e || window.event;
            var target = e.target || e.srcElement;
            var isInput = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA');

            var langModal = document.getElementById('kw-lang-modal');
            var isModalOpen = langModal && langModal.style.display !== 'none' && !langModal.classList.contains('hidden');
            if (isModalOpen) {
                if (e.key === 'Escape' || e.keyCode === 27) {
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    window.onLangCancel();
                    return false;
                }
                if (e.key === 'Enter' || e.keyCode === 13) {
                    var focused = document.activeElement;
                    if (focused === btnLangNo) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.onLangNo();
                        return false;
                    } else if (focused === btnLangCancel) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.onLangCancel();
                        return false;
                    } else {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.onLangYes();
                        return false;
                    }
                }
            }

            // Ctrl+S -> Save
            if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.keyCode === 83)) {
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                window.onSaveClick();
                return false;
            }

            // Delete key -> Delete selected rows when not typing in input
            if ((e.key === 'Delete' || e.keyCode === 46) && !isInput) {
                if (getSelectedRowsArray().length > 0) {
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    window.onDeleteClick();
                    return false;
                }
            }

            // Ctrl+Z -> Undo (when not in input)
            if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.keyCode === 90) && !isInput) {
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                window.undo();
                updateToolbarState();
                return false;
            }

            // Ctrl+Y or Ctrl+Shift+Z -> Redo (when not in input)
            if (((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.keyCode === 89) || ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'z' || e.keyCode === 90))) && !isInput) {
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                window.redo();
                updateToolbarState();
                return false;
            }
        });

        addEvent(window, 'beforeunload', function(e) {
            if (window.isDirty && window.isDirty()) {
                e = e || window.event;
                var msg = 'You have unsaved changes in this session.';
                if (e) e.returnValue = msg;
                return msg;
            }
        });

        var mismatchScript = document.getElementById('mismatch-info');
        if (mismatchScript) {
            try {
                var mData = JSON.parse(mismatchScript.textContent || mismatchScript.innerText || 'null');
                if (mData && (mData.is_mismatch || mData.detected_language)) {
                    window.showLanguageVerificationModal(mData);
                }
            } catch(e) {}
        }

        updateToolbarState();

        if (window.rebindMVPBookmarks) {
            window.rebindMVPBookmarks();
        }
    }

    function handleHorizontalScroll(e) {
        e = e || window.event;
        if (e.shiftKey || e.altKey) {
            var delta = e.wheelDelta ? -e.wheelDelta : (e.detail ? e.detail * 40 : 0);
            
            var target = e.target || e.srcElement;
            var scrollCell = null;
            var curr = target;
            while (curr) {
                if (curr.classList && curr.classList.contains('scrollable-cell')) {
                    scrollCell = curr;
                    break;
                }
                curr = curr.parentNode;
            }
            
            if (scrollCell) {
                scrollCell.scrollLeft += delta;
            } else {
                var scrollEl = document.documentElement || document.body;
                scrollEl.scrollLeft += delta;
            }
            
            if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
            return false;
        }
    }
    addEvent(document, 'mousewheel', handleHorizontalScroll);
    addEvent(document, 'DOMMouseScroll', handleHorizontalScroll);

    if (window.addEventListener) {
        window.addEventListener('DOMContentLoaded', init, false);
        window.addEventListener('load', init, false);
    } else if (window.attachEvent) {
        window.attachEvent('onload', init);
    } else {
        window.onload = init;
    }
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        init();
    }
})();
</script>
</body>
</html>
"""

    watchdog_js = ""
    if display_mode_val == 'progressive':
        timeout_ms = progressive_timeout_seconds * 1000
        watchdog_js = f"""<script>
setTimeout(function() {{
    var pendings = document.querySelectorAll('[data-pending="true"]');
    if (pendings.length > 0) {{
        for (var i = 0; i < pendings.length; i++) {{
            pendings[i].classList.remove("skeleton-loader");
            pendings[i].innerHTML = "<span style='color: #ff5555; font-style: italic;'>[Timeout: Background Process Failed]</span>";
            pendings[i].removeAttribute("data-pending");
        }}
    }}
}}, {timeout_ms});
</script>"""

    html_page = html_page.replace("</body>", f"{watchdog_js}\n</body>")

    # zoom_level is now passed as an argument
    
    try:
        numeric_zoom = float(zoom_level.replace('%', ''))
        inverse_width = f"{10000 / numeric_zoom:.3f}%"
    except Exception:
        inverse_width = "100%"

    if zoom_level.isdigit():
        zoom_level = f"{zoom_level}%"
        
    html_page = html_page.replace("{zoom_level}", zoom_level)
    html_page = html_page.replace("{inverse_zoom_width}", inverse_width)
    html_page = html_page.replace("{source_html}", source_html)
    html_page = html_page.replace("{sentence_html}", sentence_html)
    html_page = html_page.replace("{table_header_html}", table_header_html)
    html_page = html_page.replace("{table_rows_html}", table_rows_html)
    html_page = html_page.replace("{token_manifest}", json.dumps(token_manifest))
    html_page = html_page.replace("{working_tsv_path}", str(working_tsv_path))
    html_page = html_page.replace("{llm_filled_js}", "true" if llm_filled else "false")
    html_page = html_page.replace("{zid}", zid)
    html_page = html_page.replace("{display_mode_js}", "progressive" if is_progressive else "monolithic")
    html_page = html_page.replace("{text_mode_js}", eff_mode)
    html_page = html_page.replace("{auto_inject_updates_js}", "true" if auto_inject_updates else "false")
    html_page = html_page.replace("{run_enrichment_js}", run_enrich)
    html_page = html_page.replace("{worker_launched_js}", "true" if worker_launched else "false")
    html_page = html_page.replace("{hover_highlight_bookmarks}", str(hover_highlight_bookmarks))
    html_page = html_page.replace("{hover_highlight_rainbow}", "1" if hover_highlight_rainbow else "0")
    html_page = html_page.replace("{hover_highlight_enabled}", "1" if hover_highlight_enabled else "0")

    if mismatch_info and mismatch_info.get("is_mismatch"):
        det_code = mismatch_info.get("detected_language") or ""
        exp_code = mismatch_info.get("expected_language") or language or ""
        det_name = mismatch_info.get("detected_name") or get_language_display_name(det_code) or det_code
        exp_name = mismatch_info.get("expected_name") or get_language_display_name(exp_code) or exp_code
        det_label = f"{det_name} ({det_code})" if det_code else det_name
        exp_label = f"{exp_name} ({exp_code})" if exp_code else exp_name
        lang_mismatch_body = f"The text appears to be {det_label}, but the active profile is {exp_label}.\n\nSwitch language to {det_name}?"
        lang_modal_display = "display: flex;"
    else:
        lang_mismatch_body = ""
        lang_modal_display = "display: none;"

    html_page = html_page.replace("{lang_mismatch_body}", lang_mismatch_body)
    html_page = html_page.replace("{lang_modal_display}", lang_modal_display)
    html_page = html_page.replace("{mismatch_info_json}", json.dumps(mismatch_info) if mismatch_info else "null")

    html_page = html_page.replace("{language}", language)
    html_page = html_page.replace("{target_language}", target_lang)
    html_page = html_page.replace("{lemma_col_name}", lemma_col_name)
    html_page = html_page.replace("{inflected_col_name}", inflected_col_name)
    html_page = html_page.replace("{audio_lmb_play}", lmb_play_val)
    html_page = html_page.replace("{audio_lmb_source}", f'"{lmb_source_val}"')
    html_page = html_page.replace("{audio_lmb_chain_mode}", f'"{lmb_chain_mode_val}"')
    html_page = html_page.replace("{audio_rmb_play}", rmb_play_val)
    html_page = html_page.replace("{audio_rmb_chain_mode}", f'"{rmb_chain_mode_val}"')
    html_page = html_page.replace("{audio_anki_tts_cli}", anki_tts_cli_path.replace("\\", "\\\\"))
    html_page = html_page.replace("{audio_python_exe}", python_exe_path.replace("\\", "\\\\"))

    # Format Title, Favicon, and Sequence Badge
    mode_label = "multi" if (eff_mode == "multi" or text_mode == "multi") else "single"
    page_title = f"Kardenwort - {language} ({mode_label})"
    if seq_num is not None and str(seq_num).strip():
        try:
            seq_int = int(seq_num)
        except (ValueError, TypeError):
            seq_int = 1
        favicon_num = seq_int if 1 <= seq_int <= 99 else 1
        favicon_href = f"/assets/numbers/{favicon_num}.ico"
        seq_badge_html = f'<span class="kw-seq-badge" id="kw-seq-badge">#{seq_int}</span>'
    else:
        favicon_href = "/assets/numbers/1.ico"
        seq_badge_html = ""

    html_page = html_page.replace("{page_title}", page_title)
    html_page = html_page.replace("{favicon_href}", favicon_href)
    html_page = html_page.replace("{seq_badge_html}", seq_badge_html)
    html_page = html_page.replace("{theme_class}", f"theme-{theme}")
    html_page = html_page.replace("{source_white_space}", "pre-wrap" if eff_mode == "multi" else "normal")
    html_page = html_page.replace("{selected_col_name}", selected_col_name)
    html_page = html_page.replace("{has_highlight_col}", "true" if col_highlighted != -1 else "false")

    theme = theme.lower()
    
    light_defaults = {'bg_color': '#f6f8fa', 'text_color': '#24292f', 'section_bg': '#ffffff', 'section_border': '#d0d7de', 'text_muted': '#57606a', 'table_border': '#d8dee4', 'table_th_border': '#d0d7de', 'table_text': '#24292f', 'row_hover': '#f3f4f6', 'word_hover': 'rgba(0, 0, 0, 0.05)', 'highlight_orange_active_bg': 'rgba(255, 225, 105, 0.4)', 'highlight_orange_active_text': '#b07e00', 'highlight_orange_active_hover_bg': 'rgba(255, 225, 105, 0.6)', 'highlight_purple_active_bg': '#dcd0ff', 'highlight_purple_active_text': '#24292f', 'highlight_purple_active_hover_bg': '#b89bf8', 'selected_orange_row_bg': 'rgba(255, 225, 105, 0.3)', 'selected_orange_row_text': '#b07e00', 'selected_purple_row_bg': 'rgba(220, 208, 255, 0.3)', 'selected_purple_row_text': '#6f42c1', 'flipped_bg': 'rgba(56, 166, 255, 0.15)', 'flipped_text': '#0969da', 'flipped_border': 'rgba(9, 105, 218, 0.6)', 'input_bg': '#ffffff', 'input_border': '#0969da', 'scrollbar_track': '#f6f8fa', 'scrollbar_thumb': '#d0d7de', 'scrollbar_thumb_hover': '#afb8c1', 'not_connected_bg': 'rgba(175, 184, 193, 0.15)', 'not_connected_text': '#57606a', 'level_3k_color': '#0969da', 'level_5k_color': '#bc4c00', 'level_goethe_color': '#1a7f37', 'modal_bg': '#ffffff', 'modal_border': '#d0d7de'}
    
    dark_defaults = {'bg_color': '#0d0f12', 'text_color': '#e3e6eb', 'section_bg': 'rgba(255, 255, 255, 0.03)', 'section_border': 'rgba(255, 255, 255, 0.1)', 'text_muted': '#8b949e', 'table_border': 'rgba(255, 255, 255, 0.05)', 'table_th_border': 'rgba(255, 255, 255, 0.1)', 'table_text': '#e3e6eb', 'row_hover': 'rgba(255, 255, 255, 0.02)', 'word_hover': 'rgba(255, 255, 255, 0.1)', 'highlight_orange_active_bg': 'rgba(255, 204, 0, 0.25)', 'highlight_orange_active_text': '#ffcc00', 'highlight_orange_active_hover_bg': 'rgba(255, 204, 0, 0.4)', 'highlight_purple_active_bg': '#9370db', 'highlight_purple_active_text': '#ffffff', 'highlight_purple_active_hover_bg': '#7b59c4', 'selected_orange_row_bg': 'rgba(255, 204, 0, 0.15)', 'selected_orange_row_text': '#ffcc00', 'selected_purple_row_bg': 'rgba(147, 112, 219, 0.15)', 'selected_purple_row_text': '#b39ddb', 'flipped_bg': 'rgba(56, 166, 255, 0.22)', 'flipped_text': '#a5d6ff', 'flipped_border': 'rgba(165, 214, 255, 0.6)', 'input_bg': '#1c1f24', 'input_border': '#58a6ff', 'scrollbar_track': '#0d0f12', 'scrollbar_thumb': '#30363d', 'scrollbar_thumb_hover': '#8b949e', 'not_connected_bg': 'rgba(139, 148, 158, 0.15)', 'not_connected_text': '#8b949e', 'level_3k_color': '#58a6ff', 'level_5k_color': '#ff9d5c', 'level_goethe_color': '#3fb950', 'modal_bg': '#161b22', 'modal_border': 'rgba(255, 255, 255, 0.15)'}
    
    if theme in ("light", "white"):
        theme_colors = dict(light_defaults)
        section = "theme_light"
    else:
        theme_colors = dict(dark_defaults)
        section = "theme_dark"
        
    if config.has_section(section):
        for key in theme_colors.keys():
            if config.has_option(section, key):
                theme_colors[key] = config.get(section, key)

    for key, val in theme_colors.items():
        html_page = html_page.replace('{' + key + '}', val)

    if children_tsv_paths:
        paths_str = ",".join(str(path) for path in children_tsv_paths)
        children_div = f'<div id="kardenwort-children" style="display:none;">{paths_str}</div>'
        html_page = html_page.replace("</body>", f"{children_div}</body>")
    
    _html_gen_timer.__exit__(None, None, None)
    if return_children:
        return html_page, ahk_args
    return html_page

class LookupResultTuple(tuple):
    """
    Backwards-compatible 4-tuple with working_tsv_path attribute.
    Allows both 4-item unpacking (comments, headers, data_rows, sentence_translation)
    and accessing res.working_tsv_path directly.
    """
    working_tsv_path = None

    def __new__(cls, comments, headers, data_rows, sentence_translation, working_tsv_path=None):
        inst = super().__new__(cls, (comments, headers, data_rows, sentence_translation))
        inst.working_tsv_path = working_tsv_path
        return inst


def run_lookup_flow(
    text, language, target_lang, fmt, config, resolved_paths, goldendict, zid,
    text_mode='single', wordfill_cfg=None, sentence_match_strategy=None,
    allow_checksum_fallback=None, no_checksum_lookup=False
):
    if wordfill_cfg is None and config is not None:
        wordfill_cfg = resolve_wordfill_config(config, resolved_paths)
    if text: text = text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
    import hashlib
    import time
    
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    
    slug = generate_slug(text)
    cache_key = f"{zid}-{slug}.{language}.tsv"
    
    working_tsv_path = results_dir / cache_key
    
    ttl_seconds = goldendict.get('lookup_ttl_seconds', 300) if isinstance(goldendict, dict) else 300
    run_intellifiller = goldendict.get('run_intellifiller', False) if isinstance(goldendict, dict) else False

    if no_checksum_lookup:
        effective_strategy = "none"
    elif sentence_match_strategy is not None:
        effective_strategy = sentence_match_strategy.value if isinstance(sentence_match_strategy, SentenceMatchStrategy) else str(sentence_match_strategy).lower()
    else:
        effective_strategy = goldendict.get('sentence_match_strategy') or (config.get(SEC_LOOKUP, 'sentence_match_strategy', fallback='normalized').lower() if hasattr(config, 'get') and config.has_section(SEC_LOOKUP) else 'normalized')

    if allow_checksum_fallback is None:
        effective_allow_fallback = goldendict.get('allow_checksum_fallback', config.getboolean(SEC_LOOKUP, 'allow_checksum_fallback', fallback=True) if hasattr(config, 'getboolean') and config.has_section(SEC_LOOKUP) else True)
    else:
        effective_allow_fallback = bool(allow_checksum_fallback)
    
    storage_adapter = get_storage_adapter(config, resolved_paths)

    sentence_translations = None
    if effective_strategy != "none" and hasattr(storage_adapter, 'find_sentence_by_strategy'):
        try:
            matches = storage_adapter.find_sentence_by_strategy(
                sentence_text=text,
                language=language,
                target_language=target_lang,
                strategy=effective_strategy,
                allow_fallback=effective_allow_fallback,
                exclude_zid=zid,
                limit=1,
                zid=zid,
            )
            if matches and matches[0].get("sentence_destination"):
                matched_dest = str(matches[0]["sentence_destination"]).strip()
                if matched_dest:
                    sentence_translations = {0: matched_dest, "FULL_TEXT": matched_dest}
        except Exception as e:
            logger.debug(f"Strategy sentence search failed: {e}")

    if sentence_translations is None:
        main_text_provider = config.get(SEC_PIPELINE, 'text_base_provider', fallback='google')
        try:
            sentence_translations = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, main_text_provider, zid=zid)
        except TranslationAlignmentError as tae:
            logger.error(f"Lookup translation alignment error: {tae}")
            sentence_translations = tae.partial_dict

    cached = False
    with storage_adapter.file_lock(working_tsv_path):
        if ttl_seconds > 0 and effective_strategy != "none":
            cached_res = storage_adapter.get_cached_session(
                slug, language, ttl_seconds,
                results_dir=results_dir,
                source_raw_text=text,
                target_language=target_lang,
                text_mode=text_mode,
                zid=zid,
                sentence_match_strategy=effective_strategy,
                allow_checksum_fallback=effective_allow_fallback,
            )
            if cached_res is not None:
                if isinstance(cached_res, dict):
                    cached_zid = cached_res.get("session", {}).get("zid") or cached_res.get("zid")
                    if cached_zid:
                        try:
                            restored = storage_adapter.restore_session(cached_zid)
                            if restored and restored.get("data_rows"):
                                cached_tsv_path = results_dir / f"{cached_zid}-{slug}.{language}.tsv"
                                return LookupResultTuple(
                                    restored.get("comments", []),
                                    restored.get("headers", []),
                                    restored.get("data_rows", []),
                                    restored.get("sentence_translation", ""),
                                    cached_tsv_path
                                )
                        except Exception as e:
                            logger.warning(f"Could not restore cached SQLite session '{cached_zid}': {e}")
                elif isinstance(cached_res, Path):
                    working_tsv_path = cached_res
                    cached = True

        if not cached:
            working_tsv_path = prepare_lookup_tsv(text, language, target_lang, config, resolved_paths, zid, ttl_seconds=0, cache_key=cache_key, text_mode=text_mode)

        comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)

    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    role_fields = get_role_fields(mapping, headers)
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    
    # --- Word-fill early pre-fill step ---
    # Runs at the start so that existing translations are filled before slow translations run.
    filled_lemmas = set()
    if wordfill_cfg and wordfill_cfg.get('enabled', False):
        try:
            col_lemma_wf = col_lemma if col_lemma != -1 else (headers.index('WordSource') if 'WordSource' in headers else -1)
            if col_lemma_wf != -1:
                # Collect unique lemmas
                seen_lemmas = {}
                for i, row in enumerate(data_rows):
                    if len(row) > col_lemma_wf:
                        lemma_val = row[col_lemma_wf].strip()
                        if lemma_val:
                            seen_lemmas.setdefault(lemma_val, []).append(i)
                # Query and apply per unique lemma
                for lemma_val, row_indices in seen_lemmas.items():
                    match = find_wordfill_match(lemma_val, language, wordfill_cfg, exclude_path=working_tsv_path)
                    if match:
                        lemma_rows = [data_rows[i] for i in row_indices]
                        apply_wordfill_to_rows(lemma_rows, headers, match)
                        filled_lemmas.add(lemma_val)
                        logger.info(
                            f"wordfill: pre-filled {len(match)} field(s) for lemma '{lemma_val}' "
                            f"from corpus."
                        )
                # In-memory wordfill pre-fill complete; deferred to final save_session
        except Exception as wf_err:
            logger.warning(f"wordfill: early pre-fill step failed, continuing: {wf_err}")
    
    col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
    
    sentence_translation = resolve_translations(
        text, text_mode, data_rows, col_index, col_sentence_dest,
        sentence_translations, working_tsv_path, comments, headers,
        persist=False, return_single=True
    )
    
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
    if is_sqlite and isinstance(sentence_translations, dict):
        for s_idx_raw, trans in sentence_translations.items():
            if trans and isinstance(trans, str):
                s_idx = (int(s_idx_raw) + 1) if (isinstance(s_idx_raw, int) or str(s_idx_raw).isdigit()) else 1
                try:
                    storage_adapter.update_sentence_translation(zid, s_idx, trans, zid=zid)
                except Exception:
                    pass

    if not is_sqlite:
        save_translation_text = config.getboolean(SEC_SETTINGS, 'save_translation_text', fallback=False)
        translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
        eff_mode = _effective_text_mode(text, text_mode)
        _write_translation_txt(text, eff_mode, sentence_translations, translation_text_path, save_flag=save_translation_text, overwrite=True)
    
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    if col_lemma != -1 and col_word_dest != -1:
        lemmas_provider = config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google')
        lemmas_to_translate = []
        for row in data_rows:
            if len(row) > col_lemma and row[col_lemma].strip():
                if len(row) <= col_word_dest or not row[col_word_dest].strip():
                    lemmas_to_translate.append(row[col_lemma].strip())
        
        if lemmas_to_translate:
            unique_lemmas = list(dict.fromkeys(lemmas_to_translate))
            translations = translate_lemmas_fast_path(unique_lemmas, language, target_lang, config, resolved_paths, lemmas_provider)
            
            for row in data_rows:
                if len(row) > col_lemma and row[col_lemma].strip():
                    lemma = row[col_lemma].strip()
                    if len(row) <= col_word_dest or not row[col_word_dest].strip():
                        trans = translations.get(lemma, "")
                        while len(row) <= col_word_dest:
                            row.append("")
                        row[col_word_dest] = trans

    if run_intellifiller:
        if is_sqlite:
            storage_adapter.save_session(
                session_zid=zid,
                slug=slug,
                source_language=language,
                target_language=target_lang,
                text_mode=text_mode,
                source_raw_text=text,
                comments=comments,
                headers=headers,
                data_rows=data_rows,
                working_tsv_path=working_tsv_path,
                zid=zid,
            )
            prompt_name = config.get(SEC_LANGUAGES, f'{language}_prompt', fallback='')
            storage_adapter.enrich_session_intellifiller(
                session_zid=zid,
                prompt_name=prompt_name,
                reprocess=True,
                zid=zid,
            )
            comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)
        else:
            with storage_adapter.file_lock(working_tsv_path):
                storage_adapter.save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
            prompt_name = config.get(SEC_LANGUAGES, f'{language}_prompt', fallback='')
            run_headless_intellifiller(working_tsv_path, prompt_name, config, resolved_paths)
            comments, headers, data_rows = storage_adapter.load_tsv_rows(working_tsv_path)
    else:
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers}
        
        cols_to_remove = []
        if 'morphology' in role_fields:
            cols_to_remove.append(role_fields['morphology'])
        elif 'WordSourceMorphologyAI' in headers:
            cols_to_remove.append('WordSourceMorphologyAI')
            
        if 'ipa' in role_fields:
            cols_to_remove.append(role_fields['ipa'])
        elif 'WordSourceIPA' in headers:
            cols_to_remove.append('WordSourceIPA')
            
        for col_name in cols_to_remove:
            if col_name in headers:
                col_idx = headers.index(col_name)
                for row in data_rows:
                    if len(row) > col_idx:
                        # Clear only if the lemma was not early pre-filled by wordfill
                        col_lemma_wf = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
                        lemma_val = row[col_lemma_wf].strip() if col_lemma_wf != -1 and len(row) > col_lemma_wf else ""
                        if lemma_val not in filled_lemmas:
                            row[col_idx] = ""

    # Persist session to active storage backend
    try:
        storage_adapter.save_session(
            session_zid=zid,
            slug=slug,
            source_language=language,
            target_language=target_lang,
            text_mode=text_mode,
            source_raw_text=text,
            comments=comments,
            headers=headers,
            data_rows=data_rows,
            working_tsv_path=working_tsv_path,
            zid=zid,
        )
    except Exception as save_err:
        logger.warning(f"Failed persisting session to storage adapter: {save_err}")

    return LookupResultTuple(comments, headers, data_rows, sentence_translation, working_tsv_path)


def normalize_blank_lines(text):
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    normalized = []
    last_empty = True
    for line in lines:
        if not line:
            if not last_empty:
                normalized.append("")
                last_empty = True
        else:
            normalized.append(line)
            last_empty = False
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized)

INJECTED_JS_TEMPLATE = """
<script>
(function() {
    var SERVER_URL = "__SERVER_URL__";
    var SESSION_ZID = "__SESSION_ZID__";
    var API_TOKEN = "__API_TOKEN__";
    var LANGUAGE = "__LANGUAGE__";
    var FINGERPRINT = "__FINGERPRINT__";
    var IS_OFFLINE = false;

    function getSelectedRowIds() {
        var checkboxes = document.querySelectorAll('.kw-tag-checkbox');
        var selected = [];
        checkboxes.forEach(function(cb) {
            if (cb.checked) {
                var tOrd = cb.getAttribute('data-token-order');
                var rId = cb.getAttribute('data-row-id');
                selected.push(parseInt((tOrd !== null && tOrd !== '') ? tOrd : rId, 10));
            }
        });
        return selected;
    }

    function setStatusMessage(msg, isError) {
        var statusEl = document.getElementById('kw-interactive-status');
        if (statusEl) {
            statusEl.textContent = msg;
            statusEl.style.color = isError ? '#f85149' : '#3fb950';
            statusEl.style.display = msg ? 'inline-block' : 'none';
        }
    }

    window.kwToggleTag = function(checkboxEl, rowId) {
        var newStatus = checkboxEl.checked;
        var rowEl = checkboxEl.closest ? checkboxEl.closest('tr') : checkboxEl.parentElement.parentElement;
        if (rowEl) {
            if (newStatus) {
                rowEl.classList.add('kw-row-selected');
            } else {
                rowEl.classList.remove('kw-row-selected');
            }
        }
        setStatusMessage('Saving...', false);

        var payload = {
            session_zid: SESSION_ZID,
            language: LANGUAGE,
            row_id: rowId,
            token_order: rowId,
            status: newStatus,
            fingerprint: FINGERPRINT
        };

        fetch(SERVER_URL + '/api/v1/tag', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Token': API_TOKEN
            },
            body: JSON.stringify(payload)
        })
        .then(function(res) {
            if (res.status === 409) {
                return res.json().then(function(data) {
                    if (data.error_code === 'ROW_STALE') {
                        setStatusMessage('Rows changed, reloading...', true);
                        setTimeout(function() { window.location.reload(); }, 1200);
                    } else if (data.error_code === 'ROW_BUSY') {
                        setStatusMessage('Updating, retrying...', false);
                        setTimeout(function() { kwToggleTag(checkboxEl, rowId); }, 1000);
                    } else {
                        setStatusMessage('Conflict: ' + (data.message || 'Stale data'), true);
                        checkboxEl.checked = !newStatus;
                        if (rowEl) {
                            if (!newStatus) rowEl.classList.add('kw-row-selected');
                            else rowEl.classList.remove('kw-row-selected');
                        }
                    }
                });
            }
            if (!res.ok) {
                return res.json().then(function(data) {
                    setStatusMessage(data.message || 'Error updating tag', true);
                    checkboxEl.checked = !newStatus;
                    if (rowEl) {
                        if (!newStatus) rowEl.classList.add('kw-row-selected');
                        else rowEl.classList.remove('kw-row-selected');
                    }
                });
            }
            return res.json().then(function(data) {
                if (data.data && data.data.fingerprint) {
                    FINGERPRINT = data.data.fingerprint;
                }
                setStatusMessage('Saved', false);
                setTimeout(function() { setStatusMessage('', false); }, 1500);
            });
        })
        .catch(function(err) {
            IS_OFFLINE = true;
            setStatusMessage('Offline (tag failed)', true);
            checkboxEl.checked = !newStatus;
            if (rowEl) {
                if (!newStatus) rowEl.classList.add('kw-row-selected');
                else rowEl.classList.remove('kw-row-selected');
            }
        });
    };

    window.kwExportSelected = function() {
        var selectedIds = getSelectedRowIds();
        var btn = document.getElementById('kw-export-btn');
        if (btn) btn.disabled = true;
        setStatusMessage('Exporting...', false);

        var payload = {
            session_zid: SESSION_ZID,
            language: LANGUAGE,
            selected_row_ids: selectedIds,
            fingerprint: FINGERPRINT
        };

        fetch(SERVER_URL + '/api/v1/export', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Token': API_TOKEN
            },
            body: JSON.stringify(payload)
        })
        .then(function(res) {
            if (btn) btn.disabled = false;
            if (!res.ok) {
                return res.json().then(function(data) {
                    setStatusMessage('Export failed: ' + (data.message || res.statusText), true);
                });
            }
            return res.json().then(function(data) {
                setStatusMessage('Export started!', false);
                setTimeout(function() { setStatusMessage('', false); }, 2500);
            });
        })
        .catch(function(err) {
            if (btn) btn.disabled = false;
            setStatusMessage('Offline (export failed)', true);
        });
    };
})();
</script>
"""


def render_section(token, ctx):
    import re
    html_output = ""
    
    def make_heading(heading_key, default_text):
        h_text = ctx.get('headings', {}).get(heading_key, "")
        if h_text == '__default__':
            h_text = default_text
        if h_text:
            return f"<h3>{h_text}</h3>\n"
        return ""
        
    if token == "source":
        heading = make_heading("source", "Source Text")
        normalized_text = normalize_blank_lines(ctx["text"])
        safe_text = normalized_text.replace('\r', '')
        html_output += f'<div class="kw-section">{heading}<div class="kw-source-text">{safe_text}</div></div>\n'
        
    elif token == "translation":
        heading = make_heading("translation", "Translation")
        normalized_trans = normalize_blank_lines(ctx.get("sentence_translation", ""))
        safe_trans = html.escape(normalized_trans.replace('\r', ''))
        html_output += f'<div class="kw-section">{heading}<div class="kw-translation">{safe_trans}</div></div>\n'
        
    elif token == "lemmas":
        heading = make_heading("lemmas", "Lemmas")
        html_output += f'<div class="kw-section">{heading}'
        html_output += '<div class="kw-table-container"><table class="kw-lemmas-table">\n'
        
        role_fields = ctx.get('role_fields', {})
        COLUMN_TOKEN_MAP = {
            'inflected': role_fields.get('inflected') or ('WordSourceInflectedForm' if 'WordSourceInflectedForm' in ctx.get('headers', []) else None),
            'lemma': role_fields.get('lemma') or ('WordSource' if 'WordSource' in ctx.get('headers', []) else None),
            'ipa': role_fields.get('ipa') or ('WordSourceIPA' if 'WordSourceIPA' in ctx.get('headers', []) else None),
            'morphology': role_fields.get('morphology') or ('WordSourceMorphologyAI' if 'WordSourceMorphologyAI' in ctx.get('headers', []) else None),
            'translation': role_fields.get('word_translation') or ('WordDestination' if 'WordDestination' in ctx.get('headers', []) else None)
        }
        COLUMN_TOKEN_MAP = {k: v for k, v in COLUMN_TOKEN_MAP.items() if v}

        server_enabled = ctx.get('server_enabled', False)
        headers = ctx['headers']
        data_rows = ctx['data_rows']

        selected_col_name = role_fields.get('selected', 'DeskSelected')
        selected_col_idx = headers.index(selected_col_name) if selected_col_name in headers else -1

        valid_tokens = []
        html_output += '<thead><tr>'
        if server_enabled:
            html_output += '<th class="kw-tag-header">★</th>'
        for col_token in ctx.get('column_tokens', []):
            if col_token not in COLUMN_TOKEN_MAP:
                logger.warning(f"Unknown lemma_columns token: {col_token}")
                continue
            valid_tokens.append(col_token)
            html_output += f'<th>{col_token.capitalize()}</th>'
        html_output += '</tr></thead>\n<tbody>\n'

        col_indices = {}
        for t in valid_tokens:
            field = COLUMN_TOKEN_MAP[t]
            col_indices[t] = headers.index(field) if field in headers else -1

        col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
        for row_id, row in enumerate(data_rows):
            sel_val = row[selected_col_idx] if selected_col_idx != -1 and len(row) > selected_col_idx else ""
            is_checked_bool = str(sel_val).strip() in ("1", "true", "True")
            row_cls = ' class="kw-row-selected"' if is_checked_bool else ''
            token_order_val = row[col_token_order] if col_token_order != -1 and len(row) > col_token_order and row[col_token_order].strip() else str(row_id)
            html_output += f'<tr data-row-id="{row_id}" data-token-order="{token_order_val}"{row_cls}>'
            if server_enabled:
                is_checked = "checked" if is_checked_bool else ""
                html_output += f'<td class="kw-tag-control"><input type="checkbox" class="kw-tag-checkbox" data-row-id="{row_id}" data-token-order="{token_order_val}" {is_checked} onchange="kwToggleTag(this, {token_order_val})"></td>'
            for t in valid_tokens:
                idx = col_indices[t]
                val = row[idx] if idx != -1 and len(row) > idx else ""
                if isinstance(val, str):
                    val = val.replace('\r', '')
                html_output += f'<td>{val}</td>'
            html_output += '</tr>\n'

        html_output += '</tbody></table></div>\n'

        if server_enabled:
            html_output += '<div class="kw-actions-bar"><button id="kw-export-btn" class="kw-export-btn" onclick="kwExportSelected()">Export to Anki</button><span id="kw-interactive-status" class="kw-interactive-status"></span></div>\n'
        html_output += '</div>\n'
        
    return html_output

def render_lookup_html(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation, session_zid=None, api_token="", server_enabled=False, fingerprint=""):
    with TraceTimer("html_generation", zid, config, resolved_paths):
        return _render_lookup_html_impl(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation, session_zid=session_zid, api_token=api_token, server_enabled=server_enabled, fingerprint=fingerprint)

def _render_lookup_html_impl(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation, session_zid=None, api_token="", server_enabled=False, fingerprint=""):
    sections = goldendict.get('sections', ['translation', 'lemmas']) if isinstance(goldendict, dict) else ['translation', 'lemmas']
    column_tokens = goldendict.get('lemma_columns', ['inflected', 'lemma', 'translation']) if isinstance(goldendict, dict) else ['inflected', 'lemma', 'translation']
    
    headings = {
        'source': goldendict.get('heading_source', ''),
        'translation': goldendict.get('heading_translation', ''),
        'lemmas': goldendict.get('heading_lemmas', '')
    }
    
    role_fields = {}
    if resolved_paths and 'anki_mapping_file' in resolved_paths:
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)

    col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
    if col_token_order == -1:
        headers.append("TokenOrder")
        col_token_order = len(headers) - 1
        for r_i, r in enumerate(data_rows):
            r.append(str(r_i))

    data_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)

    effective_server_enabled = server_enabled or goldendict.get('server_enabled', False)
    effective_api_token = api_token or goldendict.get('server_api_key', '')
    
    ctx = {
        'text': text,
        'sentence_translation': sentence_translation,
        'headers': headers,
        'data_rows': data_rows,
        'language': language,
        'target_lang': target_lang,
        'run_intellifiller': goldendict.get('run_intellifiller', False) if isinstance(goldendict, dict) else False,
        'column_tokens': column_tokens,
        'headings': headings,
        'role_fields': role_fields,
        'server_enabled': effective_server_enabled,
        'session_zid': session_zid,
        'api_token': effective_api_token,
        'fingerprint': fingerprint,
    }
    
    html_output = '<div class="kw-lookup-container">\n'
    for sec in sections:
        html_output += render_section(sec, ctx)
    html_output += '</div>\n'
    
    theme = goldendict.get('theme', 'compact')
    
    css = ""
    if theme == 'compact':
        css = """
        .kw-lookup-container {
            --table-th-border: #ccc;
            --table-border: #eee;
            margin: 0;
            padding: 2px;
            font-family: inherit;
            font-size: inherit;
            line-height: inherit;
            font-style: normal;
            font-weight: normal;
        }
        .kw-source-text {
            white-space: pre-wrap;
            padding: 2px 0;
            margin-bottom: 1em;
            font-family: inherit;
            font-style: normal;
            font-weight: normal;
        }
        .kw-translation {
            padding: 2px 0;
            margin-bottom: 1em;
            font-family: inherit;
            font-style: normal;
            font-weight: normal;
        }
        .kw-lemmas-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 4px;
        }
        .kw-lemmas-table th {
            text-align: left;
            padding: 2px 4px;
            border-bottom: 1px solid var(--table-th-border);
        }
        .kw-lemmas-table td {
            padding: 2px 4px;
        }
        .kw-lemmas-table tr.kw-row-selected {
            background-color: rgba(255, 225, 105, 0.4) !important;
        }
        .kw-tag-header {
            width: 32px;
            text-align: center;
        }
        .kw-tag-control {
            text-align: center;
            width: 32px;
        }
        .kw-tag-checkbox {
            cursor: pointer;
            width: 16px;
            height: 16px;
            accent-color: #0969da;
        }
        .kw-actions-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 8px;
            margin-bottom: 8px;
        }
        .kw-export-btn {
            background-color: #2da44e;
            color: #ffffff;
            border: 1px solid rgba(27, 31, 36, 0.15);
            border-radius: 6px;
            padding: 3px 10px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
        }
        .kw-export-btn:hover {
            background-color: #2c974b;
        }
        .kw-export-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        .kw-interactive-status {
            font-size: 12px;
            font-weight: 500;
            margin-left: 8px;
            display: none;
        }"""
    else:
        css = """
        html, body {
            background-color: #0d0f12;
            color: #e3e6eb;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            font-size: 14px;
            line-height: 1.5;
            margin: 0;
            padding: 0;
        }
        .kw-lookup-container {
            --bg-primary: #0d0f12;
            --bg-secondary: #13161c;
            --bg-card: #161a22;
            --bg-hover: #1f242d;
            --border-color: rgba(255, 255, 255, 0.1);
            --border-subtle: rgba(255, 255, 255, 0.05);
            --text-main: #e3e6eb;
            --text-muted: #8b949e;
            --font-size-xs: 12px;
            --font-size-sm: 13px;
            --font-size-base: 14px;
            --font-size-md: 16px;
            --radius-sm: 2px;

            background-color: var(--bg-primary);
            color: var(--text-main);
            max-width: 1400px;
            margin: 0 auto;
            padding: 16px;
            box-sizing: border-box;
        }
        .kw-section {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            padding: 14px 16px;
            margin-bottom: 14px;
        }
        h3, .kw-heading {
            font-size: var(--font-size-xs);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin: 0 0 8px 0;
        }
        .kw-source-text {
            font-size: var(--font-size-md);
            line-height: 1.6;
            color: var(--text-main);
            white-space: pre-wrap;
            margin: 0;
        }
        .kw-translation {
            font-size: var(--font-size-md);
            line-height: 1.6;
            color: var(--text-main);
            white-space: pre-wrap;
            margin: 0;
        }
        .kw-table-container {
            border: 1px solid var(--border-color);
            background: var(--bg-secondary);
            border-radius: var(--radius-sm);
            overflow-x: auto;
            margin-top: 8px;
        }
        .kw-lemmas-table {
            width: 100%;
            border-collapse: collapse;
            font-size: var(--font-size-base);
        }
        .kw-lemmas-table th {
            background: var(--bg-card);
            color: var(--text-muted);
            font-size: var(--font-size-xs);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid var(--border-color);
        }
        .kw-lemmas-table td {
            padding: 8px 12px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-main);
        }
        .kw-lemmas-table tr:last-child td {
            border-bottom: none;
        }
        .kw-lemmas-table tr:hover td {
            background-color: var(--bg-hover);
        }
        .kw-lemmas-table tr.kw-row-selected td {
            background-color: rgba(255, 204, 0, 0.2) !important;
        }
        .kw-tag-header {
            width: 36px;
            text-align: center;
        }
        .kw-tag-control {
            text-align: center;
            width: 36px;
        }
        .kw-tag-checkbox {
            cursor: pointer;
            width: 16px;
            height: 16px;
            accent-color: #388bfd;
        }
        .kw-actions-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 12px;
        }
        .kw-export-btn {
            background-color: var(--bg-card);
            color: var(--text-main);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            padding: 4px 12px;
            height: 26px;
            font-size: var(--font-size-sm);
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            text-decoration: none;
        }
        .kw-export-btn:hover {
            background-color: var(--bg-hover);
            border-color: rgba(255, 255, 255, 0.2);
        }
        .kw-export-btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }
        .kw-interactive-status {
            font-size: var(--font-size-xs);
            font-weight: 500;
            color: #3fb950;
            margin-left: 8px;
            display: none;
            font-family: var(--font-mono, monospace);
        }"""
        if theme == 'light':
            css = css.replace('#0d0f12', '#f6f8fa').replace('#13161c', '#ffffff').replace('#161a22', '#ffffff').replace('#e3e6eb', '#24292f').replace('rgba(255, 255, 255, 0.1)', 'rgba(0, 0, 0, 0.1)').replace('rgba(255, 255, 255, 0.05)', 'rgba(0, 0, 0, 0.05)')

    if goldendict.get('disable_css', False):
        base_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Kardenwort Lookup</title>
</head>
<body>
{html_output}
</body>
</html>"""
    else:
        base_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Kardenwort Lookup</title>
    <style>{css}
    </style>
</head>
<body>
{html_output}
</body>
</html>"""

    if effective_server_enabled and session_zid:
        server_host = goldendict.get('server_host', '127.0.0.1')
        server_port = goldendict.get('server_port', 18335)
        server_url = f"http://{server_host}:{server_port}"
        injected_script = (
            INJECTED_JS_TEMPLATE
            .replace("__SERVER_URL__", server_url)
            .replace("__SESSION_ZID__", session_zid or "")
            .replace("__API_TOKEN__", effective_api_token or "")
            .replace("__LANGUAGE__", language or "")
            .replace("__FINGERPRINT__", fingerprint or "")
        )
        base_html = base_html.replace("</body>", f"{injected_script}\n</body>")

    return base_html

def render_lookup_text(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation):
    import re
    sections = goldendict['sections']
    column_tokens = goldendict['lemma_columns']
    
    headings = {
        'source': goldendict.get('heading_source', ''),
        'translation': goldendict.get('heading_translation', ''),
        'lemmas': goldendict.get('heading_lemmas', '')
    }
    
    role_fields = {}
    if resolved_paths and 'anki_mapping_file' in resolved_paths:
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
    
    out = []
    
    def add_heading(key, default):
        h = headings.get(key, "")
        if h == '__default__':
            h = default
        if h:
            out.append(f"=== {h} ===")
            
    for sec in sections:
        if sec == 'source':
            add_heading("source", "Source Text")
            out.append(text)
            out.append("")
        elif sec == 'translation':
            add_heading("translation", "Translation")
            out.append(sentence_translation)
            out.append("")
        elif sec == 'lemmas':
            add_heading("lemmas", "Lemmas")
            
            COLUMN_TOKEN_MAP = {
                'inflected': role_fields.get('inflected') or ('WordSourceInflectedForm' if 'WordSourceInflectedForm' in headers else None),
                'lemma': role_fields.get('lemma') or ('WordSource' if 'WordSource' in headers else None),
                'ipa': role_fields.get('ipa') or ('WordSourceIPA' if 'WordSourceIPA' in headers else None),
                'morphology': role_fields.get('morphology') or ('WordSourceMorphologyAI' if 'WordSourceMorphologyAI' in headers else None),
                'translation': role_fields.get('word_translation') or ('WordDestination' if 'WordDestination' in headers else None)
            }
            # Exclude tokens with no resolvable column
            COLUMN_TOKEN_MAP = {k: v for k, v in COLUMN_TOKEN_MAP.items() if v}
            
            valid_tokens = []
            for t in column_tokens:
                if t in COLUMN_TOKEN_MAP:
                    valid_tokens.append(t)
                else:
                    logger.warning(f"Unknown lemma_columns token: {t}")
            
            col_indices = {}
            for t in valid_tokens:
                field = COLUMN_TOKEN_MAP[t]
                col_indices[t] = headers.index(field) if field in headers else -1
                
            for row in data_rows:
                row_vals = []
                for t in valid_tokens:
                    idx = col_indices[t]
                    val = row[idx] if idx != -1 and len(row) > idx else ""
                    val = re.sub(r'<br\s*/?>', ' ', val, flags=re.IGNORECASE)
                    val = re.sub(r'<[^>]+>', '', val)
                    row_vals.append(val.strip())
                out.append("\t".join(row_vals))
            out.append("")
            
    return "\n".join(out).strip()

def render_lookup_combined(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation):
    import json
    html_out = render_lookup_html(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
    text_out = render_lookup_text(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
    return json.dumps({
        "html": html_out,
        "text": text_out
    }, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Word-fill engine
# ---------------------------------------------------------------------------

def is_wordfill_eligible(col_name):
    """
    Determine if a column should be filled from the word-fill engine.
    We avoid hardcoding explicit fields. Instead we fill Word-level attributes,
    while avoiding Sentence-level attributes and the primary WordSource itself.
    """
    if col_name in ('WordSource', 'WordSourceInflectedForm', 'WordSourceInflectedForm2', 'WordDestinationInflectedForm'):
        return False
    if col_name.startswith('Sentence'):
        return False
    if col_name.startswith('Word'):
        return True
    return False

def collect_candidate_files(scan_roots, scan_depth, scan_scope, language, scan_sort_order='chronological', scan_max_files=500, scan_match_language=True):
    """
    Return a list of candidate TSV Paths, sorted and capped at max_scan_files.

    scan_match_language=True (default): only files whose name ends with '.{language}.tsv'
        are included. Prevents cross-language homograph matches (e.g. German "Bank"
        returned when searching English "bank").
    scan_match_language=False: any '.tsv' file is eligible regardless of its language suffix.
        Useful for mixed-language corpora.

    Sort order:
      If scan_sort_order == 'chronological' (default):
          Primary:   newest ZID first (later timestamp = higher priority).
          Secondary: merged before session — within the SAME ZID, merged files are ranked before session files.
      If scan_sort_order == 'merged_first':
          Primary:   merged before session — ALL merged files are ranked before ANY session files.
          Secondary: newest ZID first (later timestamp = higher priority).
    In scan_scope='merged' only merged files are present, so the primary/secondary distinction doesn't change the outcome.
    """
    candidates = []

    for root in scan_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        # Level 0: files directly in root
        for f in root.iterdir():
            if not f.is_file():
                continue
            if scan_match_language and not f.name.endswith(f'.{language}.tsv'):
                continue
            if scan_scope == 'merged' and '-merged.' not in f.name:
                continue
            candidates.append(f)
        # Level 1: one subdirectory deep
        if scan_depth >= 1:
            for sub in root.iterdir():
                if not sub.is_dir():
                    continue
                for f in sub.iterdir():
                    if not f.is_file():
                        continue
                    if scan_match_language and not f.name.endswith(f'.{language}.tsv'):
                        continue
                    if scan_scope == 'merged' and '-merged.' not in f.name:
                        continue
                    candidates.append(f)

    # Sort candidates
    if scan_sort_order == 'merged_first':
        # Primary: merged before session
        # Secondary: newest ZID first
        candidates.sort(
            key=lambda p: (1 if '-merged.' in p.name else 0, extract_zid(p)),
            reverse=True
        )
    else:
        # Default: chronological
        # Primary: newest ZID first
        # Secondary: merged before session (within SAME ZID)
        candidates.sort(
            key=lambda p: (extract_zid(p), 1 if '-merged.' in p.name else 0),
            reverse=True
        )

    if len(candidates) > scan_max_files:
        logger.info(
            f"wordfill: candidate file count {len(candidates)} exceeds scan_max_files={scan_max_files}; "
            f"scanning only the {scan_max_files} most recent."
        )
        candidates = candidates[:scan_max_files]

    return candidates


def score_wordfill_row(row, headers):
    """Return quality tier: 2=full (IPA+morphology), 1=partial (one of them), 0=bare."""
    ipa_idx = headers.index('WordSourceIPA') if 'WordSourceIPA' in headers else -1
    morph_idx = headers.index('WordSourceMorphologyAI') if 'WordSourceMorphologyAI' in headers else -1

    has_ipa = ipa_idx != -1 and len(row) > ipa_idx and bool(row[ipa_idx].strip())
    has_morph = morph_idx != -1 and len(row) > morph_idx and bool(row[morph_idx].strip())

    if has_ipa and has_morph:
        return 2
    if has_ipa or has_morph:
        return 1
    return 0


def find_wordfill_match(word, language, wordfill_cfg, exclude_path=None):
    """
    Scan configured SQLite DB and TSV corpus for the best matching row for *word* in *language*.
    Returns a dict {column: value} for non-empty eligible fields, or None.
    Priority: SQLite indexed match first (<1ms), then external candidate TSVs.
    Within source: newest ZID file first, then quality tier (full > partial > bare).
    """
    if not wordfill_cfg or not wordfill_cfg.get('enabled', False):
        return None

    scan_roots = wordfill_cfg.get('scan_roots', [])
    scan_depth = wordfill_cfg.get('scan_depth', 1)
    scan_scope = wordfill_cfg.get('scan_scope', 'merged')
    scan_sort_order = wordfill_cfg.get('scan_sort_order', 'chronological')
    scan_match_language = wordfill_cfg.get('scan_match_language', True)
    scan_max_files = wordfill_cfg.get('scan_max_files', 500)
    target_quality = wordfill_cfg.get('target_quality', 'any')
    target_fallback = wordfill_cfg.get('target_fallback', True)

    target_quality_tier = {'any': 0, 'partial': 1, 'full': 2}.get(target_quality, 0)
    backend = str(wordfill_cfg.get('storage_backend') or wordfill_cfg.get('backend') or 'tsv').strip().lower()

    word_lower = word.strip().lower()
    if not word_lower:
        return None

    best_fallback_score = -1
    best_fallback_match = None

    exclude_zid = extract_zid(Path(exclude_path)) if exclude_path else None

    # --- Phase 1: SQLite Indexed Search (< 1ms) ---
    is_sqlite_active = (backend == 'sqlite') or ('db' in wordfill_cfg and wordfill_cfg['db'] is not None)
    try:
        db = wordfill_cfg.get('db')
        db_path = wordfill_cfg.get('sqlite_db_path')
        if not db and db_path:
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(db_path=db_path)

        if db and db.db_path and db.db_path.exists():
            is_sqlite_active = True
            search_lang = language if scan_match_language else None
            candidates = db.find_wordfill_candidates(
                word=word,
                language=search_lang,
                exclude_zid=exclude_zid,
                limit=10,
            )
            for cand in candidates:
                match_dict = {}
                if cand.get('word_destination'):
                    match_dict['WordDestination'] = str(cand['word_destination']).strip()
                if cand.get('morphology'):
                    match_dict['WordSourceMorphologyAI'] = str(cand['morphology']).strip()
                if cand.get('ipa'):
                    match_dict['WordSourceIPA'] = str(cand['ipa']).strip()

                extra = cand.get('extra_fields') or {}
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        if is_wordfill_eligible(k) and v and 'skeleton-loader' not in str(v):
                            match_dict[k] = str(v).strip()

                if match_dict:
                    has_ipa = bool(match_dict.get('WordSourceIPA', '').strip())
                    has_morph = bool(match_dict.get('WordSourceMorphologyAI', '').strip())
                    tier = 2 if (has_ipa and has_morph) else (1 if (has_ipa or has_morph) else 0)

                    if tier >= target_quality_tier:
                        # Direct early exit on valid target quality match
                        return match_dict
                    elif tier > best_fallback_score:
                        best_fallback_score = tier
                        best_fallback_match = match_dict
    except Exception as e:
        logger.warning(f"Failed SQLite wordfill search for '{word}': {e}")

    # If SQLite storage is active, do not fall back to scanning disk TSVs in scan_roots
    if is_sqlite_active and backend != 'tsv':
        if target_fallback:
            return best_fallback_match
        return None

    # --- Phase 2: Hybrid Fallback to External TSV Scanning (for TSV storage mode) ---
    if scan_roots:
        candidates = collect_candidate_files(
            scan_roots, scan_depth, scan_scope, language,
            scan_sort_order=scan_sort_order,
            scan_max_files=scan_max_files,
            scan_match_language=scan_match_language
        )

        for file_rank, tsv_path in enumerate(candidates):
            if exclude_path and tsv_path.resolve() == Path(exclude_path).resolve():
                continue
            if exclude_zid and extract_zid(tsv_path) == exclude_zid and '-merged.' not in tsv_path.name:
                continue

            try:
                with file_lock(tsv_path):
                    _comments, headers, data_rows = load_tsv_rows(tsv_path)
            except Exception as e:
                logger.warning(f"Failed to load candidate {tsv_path}: {e}")
                continue

            lemma_idx = headers.index('WordSource') if 'WordSource' in headers else -1
            inflected_idx = headers.index('WordSourceInflectedForm') if 'WordSourceInflectedForm' in headers else -1
            quotation_idx = headers.index('Quotation') if 'Quotation' in headers else -1

            if lemma_idx == -1 and inflected_idx == -1 and quotation_idx == -1:
                continue

            file_best_score = -1
            file_best_match = None

            for row in data_rows:
                # Check lemma match
                lemma_val = row[lemma_idx].strip().lower() if lemma_idx != -1 and len(row) > lemma_idx else ''
                inflected_val = row[inflected_idx].strip().lower() if inflected_idx != -1 and len(row) > inflected_idx else ''
                quotation_val = row[quotation_idx].strip().lower() if quotation_idx != -1 and len(row) > quotation_idx else ''

                if word_lower not in (lemma_val, inflected_val, quotation_val):
                    continue

                tier = score_wordfill_row(row, headers)

                match_dict = {}
                for col_idx, col in enumerate(headers):
                    if is_wordfill_eligible(col):
                        if len(row) > col_idx:
                            val = row[col_idx].strip()
                            if val and 'skeleton-loader' not in val:
                                match_dict[col] = val

                # Maximize quality within this file
                if file_best_match is None or tier > file_best_score:
                    file_best_score = tier
                    file_best_match = match_dict

            if file_best_match is not None:
                if file_best_score >= target_quality_tier:
                    return file_best_match
                else:
                    if file_best_score > best_fallback_score:
                        best_fallback_score = file_best_score
                        best_fallback_match = file_best_match

    # Exhausted all candidates.
    if target_fallback:
        return best_fallback_match
    else:
        return None


def apply_wordfill_to_rows(data_rows, headers, match_row_dict):
    """
    For each row in data_rows, if it has a non-empty WordSource value that
    matches the match (checked externally), copy eligible empty fields from match_row_dict.
    The caller is responsible for calling this once per unique lemma with the appropriate match.
    This function copies into ALL rows provided (assumes caller pre-filtered by lemma).
    Only empty target cells are filled; existing values are preserved.
    """
    for col_name, fill_value in match_row_dict.items():
        if not is_wordfill_eligible(col_name):
            continue
        if col_name not in headers:
            continue
        col_idx = headers.index(col_name)
        for row in data_rows:
            # Extend row if needed
            while len(row) <= col_idx:
                row.append('')
            if not row[col_idx].strip():
                row[col_idx] = fill_value


def cmd_wordfill(args):
    config, resolved_paths, goldendict, wordfill_cfg = load_config(args.config)
    match = find_wordfill_match(args.word, args.language, wordfill_cfg)
    emit_payload({'match': match})
    sys.exit(0)


def core_lookup(
    text, language, target_lang=None, config_path=None, fmt=None, text_mode='single',
    sections=None, lemma_columns=None, theme=None, no_headings=False, disable_css=False,
    zid=None, wordfill_cfg=None, config=None, resolved_paths=None, goldendict=None,
    bypass_lang_check=False, storage=None, sentence_match_strategy=None,
    allow_checksum_fallback=None, no_checksum_lookup=False
):
    if zid is None:
        zid = generate_unique_zid()

    if config is None or resolved_paths is None or goldendict is None:
        c_tuple = load_config(config_path)
        config, resolved_paths, goldendict = c_tuple[0], c_tuple[1], c_tuple[2]
        if wordfill_cfg is None:
            wordfill_cfg = c_tuple[3]
    elif wordfill_cfg is None:
        wordfill_cfg = resolve_wordfill_config(config, resolved_paths)

    if storage:
        resolved_paths = dict(resolved_paths)
        resolved_paths['storage_backend'] = storage
        if wordfill_cfg:
            wordfill_cfg = dict(wordfill_cfg)
            wordfill_cfg['storage_backend'] = storage
            wordfill_cfg['backend'] = storage

    if not bypass_lang_check:

        lang_res = verify_language(text, language, config, bypass=False)
        if not lang_res.is_match:
            if lang_res.action in ("block", "prompt"):
                raise StructuredError(
                    ErrorCode.LANGUAGE_MISMATCH,
                    lang_res.message,
                    details={
                        "detected_language": lang_res.detected_lang,
                        "expected_language": lang_res.expected_lang,
                        "confidence": lang_res.confidence,
                        "action": lang_res.action,
                    }
                )
            elif lang_res.action == "warn":
                logger.warning(lang_res.message)

    goldendict = dict(goldendict)

    if fmt:
        goldendict['format'] = fmt
    if theme:
        goldendict['theme'] = theme
    if sections:
        goldendict['sections'] = parse_sections_list(sections, ['source', 'translation', 'lemmas']) if isinstance(sections, str) else sections
    if lemma_columns:
        goldendict['lemma_columns'] = parse_columns_list(lemma_columns, ['inflected', 'lemma', 'ipa', 'morphology', 'translation']) if isinstance(lemma_columns, str) else lemma_columns
    if no_headings:
        goldendict['heading_source'] = ""
        goldendict['heading_translation'] = ""
        goldendict['heading_lemmas'] = ""
    if disable_css:
        goldendict['disable_css'] = True

    if not target_lang:
        target_lang = config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')

    if f"{language}_prompt" not in config[SEC_LANGUAGES]:
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, f"Missing {language}_prompt in [languages]")

    if text_mode == 'single' and '\n' in text.strip():
        text_mode = 'multi'

    if text_mode == 'multi':
        remove_empty = config.getboolean(SEC_SETTINGS, 'multi_mode_remove_empty_lines', fallback=True)
        clean_spaces = config.getboolean(SEC_SETTINGS, 'multi_mode_clean_spaces', fallback=True)
        if remove_empty or clean_spaces:
            import re
            new_lines = []
            for line in text.splitlines():
                if clean_spaces:
                    line = re.sub(r'[ \t]+', ' ', line).strip()
                if remove_empty and not line.strip():
                    continue
                new_lines.append(line)
            text = "\n".join(new_lines)

    lookup_res = run_lookup_flow(
        text, language, target_lang, goldendict['format'], config, resolved_paths, goldendict, zid, text_mode,
        wordfill_cfg=wordfill_cfg,
        sentence_match_strategy=sentence_match_strategy,
        allow_checksum_fallback=allow_checksum_fallback,
        no_checksum_lookup=no_checksum_lookup,
    )
    comments, headers, data_rows, sentence_translation = lookup_res[:4]
    working_tsv_path = getattr(lookup_res, 'working_tsv_path', None) or (lookup_res[4] if len(lookup_res) > 4 else None)
    if working_tsv_path is None:
        kw_config = load_kardenwort_config(resolved_paths['kardenwort_workspace'])
        results_dir = resolve_results_dir(resolved_paths, kw_config)
        slug = generate_slug(text)
        working_tsv_path = results_dir / f"{zid}-{slug}.{language}.tsv"

    session_zid = extract_zid(working_tsv_path) or working_tsv_path.stem
    storage_adapter = get_storage_adapter(config, resolved_paths)
    if storage_adapter.backend_name == 'sqlite' and session_zid:
        try:
            restored = storage_adapter.restore_session(session_zid)
            if restored and restored.get("data_rows"):
                comments = restored.get("comments", comments)
                headers = restored.get("headers", headers)
                data_rows = restored.get("data_rows", data_rows)
        except Exception as e:
            logger.warning(f"Could not restore session '{session_zid}' for lookup view: {e}")

    fingerprint = compute_content_fingerprint(data_rows)
    server_enabled = goldendict.get('server_enabled', False)
    api_token = goldendict.get('server_api_key', '')

    current_fmt = goldendict['format']
    if current_fmt == 'html':
        out = render_lookup_html(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation, session_zid=session_zid, api_token=api_token, server_enabled=server_enabled, fingerprint=fingerprint)
    elif current_fmt == 'text':
        out = render_lookup_text(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
    else:
        out = render_lookup_combined(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)

    return {
        "html": out,
        "session_zid": session_zid,
        "language": language,
        "tsv_path": str(working_tsv_path),
        "comments": comments,
        "headers": headers,
        "data_rows": data_rows,
        "sentence_translation": sentence_translation,
        "fingerprint": fingerprint
    }


def cmd_lookup(args):
    zid = getattr(args, 'zid', None) or generate_unique_zid()
    logger.info("Lookup subcommand invoked", extra={"zid": zid})
    try:
        res = core_lookup(
            text=args.text,
            language=args.language,
            target_lang=getattr(args, 'target_lang', None),
            config_path=getattr(args, 'config', None),
            fmt=getattr(args, 'format', None),
            text_mode=getattr(args, 'text_mode', 'single'),
            sections=getattr(args, 'sections', None),
            lemma_columns=getattr(args, 'lemma_columns', None),
            theme=getattr(args, 'theme', None),
            no_headings=getattr(args, 'no_headings', False),
            disable_css=getattr(args, 'disable_css', False),
            zid=zid,
            bypass_lang_check=getattr(args, 'bypass_lang_check', False),
            storage=getattr(args, 'storage', None),
            sentence_match_strategy=getattr(args, 'sentence_match_strategy', None),
            no_checksum_lookup=getattr(args, 'no_checksum_lookup', False),
        )
        emit_payload(res["html"], raw=True)

        sys.exit(0)
    except StructuredError as se:
        print_structured_error(se.error_code, se.message, se.details)
        fmt = getattr(args, 'format', 'html')
        if fmt == 'text':
            emit_payload(f"Error: {se.message}", raw=True)
        else:
            emit_payload(f'<div style="color: red; padding: 10px; font-family: sans-serif;">Error: {se.message}</div>', raw=True)
        sys.exit(1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Lookup failed: {str(e)}")
        fmt = getattr(args, 'format', 'html')
        if fmt == 'text':
            emit_payload(f"Error: {str(e)}", raw=True)
        else:
            emit_payload(f'<div style="color: red; padding: 10px; font-family: sans-serif;">Error: {str(e)}</div>', raw=True)
        sys.exit(1)

def cmd_render(args):
    logger.info("Render subcommand invoked", extra={"zid": args.zid})
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    
    if not args.text:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print_structured_error("INVALID_STATE", "No text provided to render")
            sys.exit(1)
    else:
        text = args.text
        
    text_mode = getattr(args, 'text_mode', 'single')
    if text_mode == 'single' and '\n' in text.strip():
        text_mode = 'multi'
        
    if text_mode == 'multi':
        remove_empty = config.getboolean(SEC_SETTINGS, 'multi_mode_remove_empty_lines', fallback=True)
        clean_spaces = config.getboolean(SEC_SETTINGS, 'multi_mode_clean_spaces', fallback=True)
        if remove_empty or clean_spaces:
            import re
            new_lines = []
            for line in text.splitlines():
                if clean_spaces:
                    line = re.sub(r'[ \t]+', ' ', line).strip()
                if remove_empty and not line.strip():
                    continue
                new_lines.append(line)
            text = "\n".join(new_lines)
        
    bypass_lang = getattr(args, 'bypass_lang_check', False)
    if not bypass_lang:
        lang_res = verify_language(text, args.language, config, bypass=False)
        if not lang_res.is_match:
            if lang_res.action in ("block", "prompt"):
                print_structured_error(
                    ErrorCode.LANGUAGE_MISMATCH,
                    lang_res.message,
                    details={
                        "detected_language": lang_res.detected_lang,
                        "expected_language": lang_res.expected_lang,
                        "confidence": lang_res.confidence,
                        "action": lang_res.action,
                    }
                )
                sys.exit(1)
            elif lang_res.action == "warn":
                logger.warning(lang_res.message)

    try:
        zoom_val = args.zoom if args.zoom else config.get(SEC_RENDERING, 'default_zoom', fallback=config.get(SEC_SETTINGS, 'default_zoom', fallback='100'))
        split_gap = args.split_gap_limit if args.split_gap_limit is not None else config.getint(SEC_SETTINGS, 'split_gap_limit', fallback=60)
        trace_id = getattr(args, 'trace_id', None) or (f"{args.zid}:render:init" if getattr(args, 'zid', None) else None)
        html = run_render_flow(text, args.language, args.zid, args.text_mode, config, resolved_paths, zoom_val, args.theme, args.tsv, split_gap_limit=split_gap, wordfill_cfg=_wordfill, seq_num=getattr(args, 'seq_num', None), trace_id=trace_id)
        from b64util import encode
        emit_payload(encode(html), raw=True)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Render failed: {str(e)}")
        sys.exit(1)

def core_export(tsv_path_or_session, selected_row_ids, config, resolved_paths, fingerprint=None, zid=None, language=None, trace_id=None):
    if zid is None:
        zid = generate_unique_zid()

    storage_backend = "tsv"
    if resolved_paths and "storage_backend" in resolved_paths:
        storage_backend = resolved_paths["storage_backend"]
    elif config and hasattr(config, "get"):
        storage_backend = config.get(SEC_STORAGE, "backend", fallback="tsv")

    if storage_backend == "sqlite":
        if isinstance(tsv_path_or_session, Path):
            sess_zid = extract_zid(tsv_path_or_session) or str(tsv_path_or_session.name)
        elif isinstance(tsv_path_or_session, str):
            if '/' in tsv_path_or_session or '\\' in tsv_path_or_session or tsv_path_or_session.endswith('.tsv'):
                sess_zid = extract_zid(Path(tsv_path_or_session)) or tsv_path_or_session
            else:
                sess_zid = tsv_path_or_session
        else:
            sess_zid = zid

        adapter = get_storage_adapter(config, resolved_paths)
        if isinstance(adapter, SqliteStorageAdapter):
            if fingerprint:
                try:
                    restored = adapter.restore_session(sess_zid)
                    current_fp = compute_content_fingerprint(restored["data_rows"])
                    if fingerprint != current_fp:
                        raise StructuredError(ErrorCode.ROW_STALE, f"Row content hash mismatch. Rendered: {fingerprint}, Current: {current_fp}")
                except StructuredError:
                    raise
                except Exception:
                    pass

            res = adapter.export_favorites(
                session_zid=sess_zid,
                selected_row_ids=selected_row_ids,
                language=language,
                zid=zid,
                trace_id=trace_id,
            )
            if isinstance(res, dict):
                res["zid"] = zid
            return res

    if isinstance(tsv_path_or_session, Path):
        tsv_path = tsv_path_or_session
    elif isinstance(tsv_path_or_session, str) and (Path(tsv_path_or_session).exists() or '\\' in tsv_path_or_session or '/' in tsv_path_or_session):
        tsv_path = Path(tsv_path_or_session)
    else:
        kardenwort_workspace = resolved_paths['kardenwort_workspace']
        kw_config = load_kardenwort_config(kardenwort_workspace)
        results_dir = resolve_results_dir(resolved_paths, kw_config)
        lang = language or config.get(SEC_SETTINGS, 'default_language', fallback='en')
        tsv_path = find_working_tsv(results_dir, str(tsv_path_or_session), lang)

    if not tsv_path or not tsv_path.exists():
        raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found: {tsv_path}")

    if check_coordination_busy(tsv_path):
        raise StructuredError(ErrorCode.ROW_BUSY, f"Working TSV file is locked by a background worker: {tsv_path.name}")

    try:
        comments, headers, data_rows = load_tsv_rows(tsv_path)
    except Exception as e:
        raise StructuredError(ErrorCode.DESK_FAILED, f"Failed to read working TSV: {e}")

    if fingerprint:
        current_fp = compute_content_fingerprint(data_rows)
        if fingerprint != current_fp:
            raise StructuredError(ErrorCode.ROW_STALE, f"Row content hash mismatch. Rendered: {fingerprint}, Current: {current_fp}")

    export_selection_mode = config.get(SEC_SETTINGS, 'export_selection_mode', fallback='selected').lower()
    if export_selection_mode == 'all':
        actual_export_rows = list(range(len(data_rows)))
    elif export_selection_mode == 'unselected':
        actual_export_rows = [i for i in range(len(data_rows)) if i not in selected_row_ids]
    else:
        actual_export_rows = selected_row_ids

    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    lang = language or config.get(SEC_SETTINGS, 'default_language', fallback='en')

    res = execute_export(tsv_path, actual_export_rows, config, resolved_paths, results_dir, zid, lang, is_from_ui=False, data_rows=data_rows, headers=headers, comments=comments, trace_id=trace_id)
    if not isinstance(res, dict):
        res = {"status": "success", "import_started": False, "tsv": str(tsv_path), "zid": zid}
    else:
        res["status"] = res.get("status", "success")
        res["zid"] = zid
    return res


def cmd_export(args):
    logger.info("Export subcommand invoked")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    manifest_path = Path(args.selection_manifest).resolve()
    if not manifest_path.exists():
        print_structured_error("INVALID_STATE", f"Selection manifest not found: {manifest_path}")
        sys.exit(1)

    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            manifest = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_STATE", f"Failed to parse selection manifest: {e}")
        sys.exit(1)

    selected_rows = manifest.get("selected_row_ids", [])
    zid = manifest.get("zid") or getattr(args, "zid", None)
    if not zid:
        print_structured_error("INVALID_STATE", "Selection manifest must contain 'zid'")
        sys.exit(1)

    trace_id = getattr(args, "trace_id", None) or manifest.get("trace_id") or (f"{zid}:export:selection" if zid else None)

    tsv_path_str = manifest.get("tsv_path")
    tsv_param = Path(tsv_path_str) if tsv_path_str else zid

    try:
        payload = core_export(tsv_param, selected_rows, config, resolved_paths, zid=zid, language=args.language, trace_id=trace_id)
        emit_payload(payload)
    except StructuredError as se:
        print_structured_error(se.error_code, se.message, se.details)
        sys.exit(1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Export failed: {e}")
        sys.exit(1)


def execute_export(tsv_path, actual_export_rows, config, resolved_paths, results_dir, zid, lang, is_from_ui, data_rows, headers, comments, save_to_favorites_override=None, send_to_anki_override=None, trace_id=None):
    if not actual_export_rows:
        logger.warning("No rows to export based on selection mode.")
        skipped_payload: ExportSkippedPayload = {
            "status": "skipped",
            "message": "Warning: No rows to export based on selection mode. Export skipped.",
        }
        if is_from_ui:
            emit_payload(skipped_payload)
        return skipped_payload
        
    exported_rows = []
    for row_id in actual_export_rows:
        if 0 <= row_id < len(data_rows):
            exported_rows.append(list(data_rows[row_id]))  # copy so we can mutate independently
        else:
            logger.warning(f"Export row index {row_id} is out of bounds (total rows: {len(data_rows)})")

    if not exported_rows:
        skipped_payload: ExportSkippedPayload = {
            "status": "skipped",
            "message": "Warning: None of the selected row indices were valid.",
        }
        if is_from_ui:
            emit_payload(skipped_payload)
        return skipped_payload

    # Resolve the selected column name from mapping config.
    selected_col_name = 'DeskSelected'
    try:
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        _rf = get_role_fields(mapping, headers)
        selected_col_name = _rf.get('selected', selected_col_name)
    except Exception:
        pass

    selected_col_idx = headers.index(selected_col_name) if selected_col_name in headers else -1

    if selected_col_idx != -1:
        # 1. Stamp DeskSelected=1 on every exported (favorites) row copy
        for row in exported_rows:
            if len(row) > selected_col_idx:
                row[selected_col_idx] = '1'
            else:
                row.extend([''] * (selected_col_idx - len(row) + 1))
                row[selected_col_idx] = '1'

        # 2. Auto-save DeskSelected=1 back into the source TSV so the
        #    window does not need to be manually saved after Send to Anki.
        try:
            selected_row_set = set(actual_export_rows)
            for row_id, row in enumerate(data_rows):
                if row is None:
                    continue
                if row_id in selected_row_set:
                    if len(row) > selected_col_idx:
                        row[selected_col_idx] = '1'
                    else:
                        row.extend([''] * (selected_col_idx - len(row) + 1))
                        row[selected_col_idx] = '1'
            with file_lock(tsv_path):
                save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            logger.info(f"Auto-saved DeskSelected state to source TSV: {tsv_path}")
        except Exception as e:
            logger.warning(f"Failed to auto-save DeskSelected to source TSV: {e}")

    fav_dir = resolved_paths['favorites_output_dir']
    fav_dir.mkdir(parents=True, exist_ok=True)

    fav_prefix = config.get(SEC_SETTINGS, 'favorites_prefix', fallback='')
    dest_filename = f"{fav_prefix}{tsv_path.name}"
    dest_path = fav_dir / dest_filename

    save_to_favorites = config.getboolean(SEC_SETTINGS, 'save_to_favorites_on_export', fallback=True) if save_to_favorites_override is None else save_to_favorites_override
    import_path = dest_path if save_to_favorites else (results_dir / f"temp_import_{dest_filename}")

    try:
        with file_lock(import_path):
            save_tsv_rows_safely(import_path, comments, headers, exported_rows)
        if save_to_favorites:
            logger.info(f"Exported favorites to {import_path}")
            
            copy_txt = config.getboolean(SEC_SETTINGS, 'copy_source_txt_to_favorites_on_export', fallback=False)
            if copy_txt:
                txt_files = list(tsv_path.parent.glob(f"{zid}-*.txt"))
                for txt_file in txt_files:
                    try:
                        dest_txt_path = fav_dir / f"{fav_prefix}{txt_file.name}"
                        shutil.copy2(txt_file, dest_txt_path)
                        logger.info(f"Copied source text {txt_file.name} to favorites")
                    except Exception as e:
                        logger.error(f"Failed to copy source text {txt_file.name} to favorites: {e}")
        else:
            logger.info(f"Exported temporary file for Anki import to {import_path}")
        
        send_to_anki = config.getboolean(SEC_SETTINGS, 'send_to_anki_after_export', fallback=False) if send_to_anki_override is None else send_to_anki_override
        if send_to_anki:
            detach = config.getboolean(SEC_SETTINGS, 'detach_import_on_send', fallback=True)
            show_window = config.getboolean(SEC_SETTINGS, 'show_import_window', fallback=False)
            if detach:
                show_window = False
                pid, log_path = run_detached_import(import_path, config, resolved_paths, zid, trace_id=trace_id)
                response: ExportImportStartedPayload = {
                    "import_started": True,
                    "show_window": show_window,
                    "pid": pid,
                    "log": log_path,
                    "tsv": str(import_path),
                    "note": "safe to close the window",
                }
                if is_from_ui:
                    emit_payload(response)
                return response
            else:
                success, output = run_synchronous_import(import_path, config, resolved_paths, zid=zid, trace_id=trace_id)
                if success:
                    import_complete_payload: ExportImportCompletePayload = {
                        "import_complete": True,
                        "show_window": show_window,
                        "output": output,
                    }
                    if is_from_ui:
                        emit_payload(import_complete_payload)
                    return import_complete_payload
                else:
                    if is_from_ui:
                        print_structured_error("DESK_FAILED", "Anki import failed synchronously", {"details": output})
                        sys.exit(1)
                    else:
                        raise StructuredError(ErrorCode.DESK_FAILED, "Anki import failed synchronously", {"details": output})

        else:
            if save_to_favorites:
                show_window = config.getboolean(SEC_SETTINGS, 'show_import_window', fallback=False)
                import_complete_payload: ExportImportCompletePayload = {
                    "import_complete": True,
                    "show_window": show_window,
                    "output": f"SUCCESS: Exported to {import_path}",
                }
                if is_from_ui:
                    emit_payload(import_complete_payload)
                return import_complete_payload
            else:
                success_payload: ExportSuccessPayload = {
                    "status": "success",
                    "message": "SUCCESS: Ready for Anki (no favorites file created)",
                }
                if is_from_ui:
                    emit_payload(success_payload)
                return success_payload
    except SystemExit:
        raise
    except Exception as e:
        if is_from_ui:
            print_structured_error("DESK_FAILED", f"Failed to execute export: {e}")
            sys.exit(1)
        else:
            raise StructuredError(ErrorCode.DESK_FAILED, f"Failed to execute export: {e}")
    except Exception as e:
        if is_from_ui:
            print_structured_error("DESK_FAILED", f"Failed to save exported favorites: {e}")
            sys.exit(1)
        else:
            logger.error(f"Failed to save exported favorites: {e}")

def execute_selected_pipeline(args, force_send_to_anki: bool):
    logger.info(f"Selected pipeline invoked (force_send_to_anki={force_send_to_anki})")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    
    lang = args.language or config.get(SEC_SETTINGS, 'default_language', fallback='en')
    
    if not hasattr(args, 'files') or not args.files:
        logger.warning("No files provided for selected pipeline.")
        return

    storage_backend = "tsv"
    if resolved_paths and "storage_backend" in resolved_paths:
        storage_backend = resolved_paths["storage_backend"]
    elif config and hasattr(config, "get"):
        storage_backend = config.get(SEC_STORAGE, "backend", fallback="tsv")

    if storage_backend == "sqlite":
        adapter = get_storage_adapter(config, resolved_paths)
        if isinstance(adapter, SqliteStorageAdapter):
            for file_or_zid in args.files:
                p = Path(file_or_zid)
                sess_zid = extract_zid(p) if (p.exists() or '/' in file_or_zid or '\\' in file_or_zid or file_or_zid.endswith('.tsv')) else file_or_zid
                if not sess_zid:
                    continue
                trace_id = getattr(args, 'trace_id', None) or (f"{sess_zid}:export:selected" if sess_zid else None)
                adapter.export_favorites(
                    session_zid=sess_zid,
                    selected_row_ids=None,
                    save_to_favorites_override=True,
                    send_to_anki_override=force_send_to_anki,
                    language=lang,
                    zid=sess_zid,
                    trace_id=trace_id,
                )
            print("Selected pipeline execution complete.")
            if getattr(args, 'pause', False):
                input("\nPress Enter to exit...")
            return

    mapping = None
    try:
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    except Exception:
        pass

    for tsv_path_str in args.files:
        tsv_path = Path(tsv_path_str).resolve()
        if not tsv_path.exists():
            logger.error(f"File not found: {tsv_path}")
            continue
            
        zid = extract_zid(tsv_path)
        if not zid:
            logger.warning(f"Could not extract ZID from {tsv_path.name}. Using default.")
            zid = "00000000000000"
            
        try:
            comments, headers, data_rows = load_tsv_rows(tsv_path)
        except Exception as e:
            logger.error(f"Failed to read TSV {tsv_path}: {e}")
            continue
            
        selected_col_name = 'DeskSelected'
        if mapping:
            _rf = get_role_fields(mapping, headers)
            selected_col_name = _rf.get('selected', selected_col_name)
            
        selected_col_idx = headers.index(selected_col_name) if selected_col_name in headers else -1
        if selected_col_idx == -1:
            logger.warning(f"No {selected_col_name} column found in {tsv_path}. Skipping.")
            continue
            
        actual_export_rows = []
        for i, row in enumerate(data_rows):
            if row and len(row) > selected_col_idx and row[selected_col_idx] == '1':
                actual_export_rows.append(i)
                
        if not actual_export_rows:
            logger.info(f"No selected rows found in {tsv_path.name}. Skipping.")
            continue
            
        trace_id = getattr(args, 'trace_id', None) or (f"{zid}:export:selected" if zid else None)
        execute_export(
            tsv_path=tsv_path,
            actual_export_rows=actual_export_rows,
            config=config,
            resolved_paths=resolved_paths,
            results_dir=results_dir,
            zid=zid,
            lang=lang,
            is_from_ui=False,
            data_rows=data_rows,
            headers=headers,
            comments=comments,
            save_to_favorites_override=True,
            send_to_anki_override=force_send_to_anki,
            trace_id=trace_id
        )
        
    print("Selected pipeline execution complete.")
    if getattr(args, 'pause', False):
        input("\nPress Enter to exit...")

def cmd_export_selected(args):
    execute_selected_pipeline(args, force_send_to_anki=False)

def cmd_import_selected(args):
    execute_selected_pipeline(args, force_send_to_anki=True)

def cmd_reprocess(args):
    logger.info("Reprocess subcommand invoked")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    
    manifest_path = Path(args.selection_manifest).resolve()
    if not manifest_path.exists():
        print_structured_error("INVALID_STATE", f"Selection manifest not found: {manifest_path}")
        sys.exit(1)
        
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            manifest = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_STATE", f"Failed to parse selection manifest: {e}")
        sys.exit(1)
        
    selected_rows = manifest.get("selected_row_ids", [])
    zid = manifest.get("zid")
    if not zid:
        print_structured_error("INVALID_STATE", "Selection manifest must contain 'zid'")
        sys.exit(1)
        
    if not selected_rows:
        logger.warning("No rows selected for reprocess.")
        emit_payload({"status": "skipped", "message": "Warning: No rows selected. Reprocess skipped."})
        sys.exit(0)
        
    lang = args.language or config.get(SEC_SETTINGS, 'default_language', fallback='en')
    
    tsv_path_str = manifest.get("tsv_path")
    if tsv_path_str:
        tsv_path = Path(tsv_path_str)
    else:
        tsv_path = find_working_tsv(results_dir, zid, lang)
        
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    if is_sqlite:
        restored = storage_adapter.restore_session(zid)
        comments = restored.get("comments", [])
        headers = restored.get("headers", [])
        data_rows = restored.get("data_rows", [])
        tsv_path = Path(tsv_path_str) if tsv_path_str else (results_dir / f"{zid}.{lang}.tsv")
    else:
        if not tsv_path or not tsv_path.exists():
            print_structured_error("DESK_FAILED", f"Working TSV file not found for session ZID {zid}")
            sys.exit(1)
        try:
            comments, headers, data_rows = load_tsv_rows(tsv_path)
        except Exception as e:
            print_structured_error("DESK_FAILED", f"Failed to read working TSV: {e}")
            sys.exit(1)
        
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    role_fields = get_role_fields(mapping, headers)
    
    editable_cols = [c.strip() for c in mapping.get('desk_editable', 'editable_columns', fallback='').split(',') if c.strip()]
    
    exclude_roles = {'lemma', 'inflected', 'word_translation', 'selected', 'source_word', 'source_sentence', 'sentence_index', 'quotation'}
    exclude_from_clear = set()
    for role, col in role_fields.items():
        if role in exclude_roles and col in headers:
            exclude_from_clear.add(col)
    for sec in ('fields', 'desk_columns', 'fields_mapping.word', 'fields_mapping.sentence'):
        if sec in mapping:
            for col, role in mapping[sec].items():
                if role in exclude_roles and col in headers:
                    exclude_from_clear.add(col)
    # Ensure source word columns are always protected from clearing
    for col in headers:
        if col.lower() in ('wordsource', 'wordsourceinflectedform', 'quotation', 'sentencesource', 'sentencesourceindex'):
            exclude_from_clear.add(col)
                    
    fields_to_clear = [c for c in editable_cols if c not in exclude_from_clear]
    for col in headers:
        if col.startswith('Word') and col not in exclude_from_clear:
            if col not in fields_to_clear:
                fields_to_clear.append(col)
            
    cleared_count = 0
    valid_selected = []
    source_col = role_fields.get('lemma', 'WordSource')
    source_idx = headers.index(source_col) if source_col in headers else -1

    for row_id in selected_rows:
        if 0 <= row_id < len(data_rows):
            row = data_rows[row_id]
            if not any(str(cell).strip() for cell in row):
                continue
            if source_idx != -1 and len(row) > source_idx and not str(row[source_idx]).strip():
                continue
                
            valid_selected.append(row_id)
            for col in fields_to_clear:
                if col in headers:
                    col_idx = headers.index(col)
                    if len(data_rows[row_id]) > col_idx:
                        data_rows[row_id][col_idx] = ""
            cleared_count += 1
            
    selected_rows = valid_selected
            
    if cleared_count == 0:
        emit_payload({"status": "skipped", "message": "Warning: None of the selected row indices were valid."})
        sys.exit(0)
        
    trace_id = getattr(args, 'trace_id', None) or (f"{zid}:reprocess:worker" if zid else None)
    try:
        if is_sqlite:
            storage_adapter.save_session(
                session_zid=zid,
                comments=comments,
                headers=headers,
                data_rows=data_rows,
                working_tsv_path=None,
                zid=zid,
            )
        else:
            with file_lock(tsv_path):
                save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            
        role_fields = get_role_fields(mapping, headers)
        run_enrich = config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='auto')
        if run_enrich == 'auto':
            safe_write_update_js(tsv_path, data_rows, headers, role_fields, zid=zid, trace_id=trace_id)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to save working TSV after clearing fields: {e}")
        sys.exit(1)
        
    prompt_name = config.get(SEC_LANGUAGES, f'{lang}_prompt')
    logger.info(f"Triggering IntelliFiller async to reprocess {cleared_count} rows in batches.")
    
    python_exe = (resolved_paths.get('kardenwort_python') if resolved_paths else None) or sys.executable
    desk_script = Path(__file__).resolve()
    
    cmd = [
        str(python_exe),
        str(desk_script),
        "batch-worker",
        "--tsv", str(tsv_path),
        "--prompt", prompt_name,
        "--rows", ",".join(str(r) for r in selected_rows)
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
    if args.config:
        cmd.extend(["--config", args.config])
        
    log_path = tsv_path.with_suffix('.log')
    try:
        log_file = open(log_path, 'a', encoding='utf-8')
    except Exception:
        log_file = subprocess.DEVNULL
        
    try:
        if sys.platform == 'win32':
            creationflags = 0x08000000 | 0x00000200
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                creationflags=creationflags,
                close_fds=True
            )
        else:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                close_fds=True
            )
        reprocess_payload: ReprocessStartedPayload = {"reprocess_started": True}
        emit_payload(reprocess_payload)
    except Exception as e:
        logger.error(f"Failed to launch batch worker: {e}")
        print_structured_error("DESK_FAILED", f"Failed to launch worker: {e}")
        sys.exit(1)

def _reprocess_worker_stage_fast_path(tsv_path, config, resolved_paths, data_rows, headers, role_fields, selected_rows, lemmas_provider, language, target_lang, zid=None, trace_id=None):
    col_lemma_name = role_fields.get('lemma', 'WordSource')
    col_word_dest_name = role_fields.get('word_translation', 'WordDestination')
    storage_adapter = get_storage_adapter(config, resolved_paths)
    
    col_lemma = headers.index(col_lemma_name) if col_lemma_name in headers else -1
    col_word_dest = headers.index(col_word_dest_name) if col_word_dest_name in headers else -1
    
    if col_lemma != -1 and col_word_dest != -1:
        lemmas_to_translate = []
        for row_id in selected_rows:
            if 0 <= row_id < len(data_rows):
                row = data_rows[row_id]
                if len(row) > col_lemma and row[col_lemma].strip():
                    lemmas_to_translate.append(row[col_lemma].strip())
        
        if lemmas_to_translate:
            lemmas_to_translate = list(set(lemmas_to_translate))
            provider_to_use = 'combined' if lemmas_provider == 'combined' else lemmas_provider
            lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, language, target_lang, config, resolved_paths, provider_to_use)
            
            with storage_adapter.file_lock(tsv_path):
                comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                data_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
                col_lemma = headers.index(col_lemma_name) if col_lemma_name in headers else -1
                col_word_dest = headers.index(col_word_dest_name) if col_word_dest_name in headers else -1
                if col_lemma != -1 and col_word_dest != -1:
                    for row_id in selected_rows:
                        if 0 <= row_id < len(data_rows):
                            row = data_rows[row_id]
                            if len(row) > col_lemma:
                                lemma_val = row[col_lemma].strip()
                                while len(row) <= col_word_dest:
                                    row.append("")
                                if lemma_val in lemma_translations:
                                    row[col_word_dest] = lemma_translations[lemma_val]
                    storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                
            run_enrich = config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='auto')
            if run_enrich == 'auto':
                sorted_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, zid=zid, trace_id=trace_id)
    return data_rows


def _reprocess_worker_stage_intellifiller(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, selected_rows, zid=None, trace_id=None):
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
    lang = getattr(args, 'language', None) or config.get(SEC_SETTINGS, 'default_language', fallback='en')

    if is_sqlite:
        prompt_val = getattr(args, 'prompt', None) or config.get(SEC_LANGUAGES, f"{getattr(args, 'language', 'en')}_prompt", fallback="")
        storage_adapter.enrich_session_intellifiller(
            session_zid=zid,
            prompt_name=prompt_val,
            selected_rows=selected_rows,
            reprocess=True,
            zid=zid,
            trace_id=trace_id,
        )
        comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
        sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
        safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="enrichment", zid=zid, trace_id=trace_id)
        return sorted_rows

    batch_size = config.getint(SEC_SETTINGS, 'intellifiller_batch_size', fallback=5)
    for i in range(0, len(selected_rows), batch_size):
        batch = selected_rows[i:i + batch_size]
        logger.info(f"Running IntelliFiller for batch {i // batch_size + 1}: {len(batch)} rows.")
        batch_trace_id = f"{trace_id}:batch_{i // batch_size + 1}" if trace_id else None
        run_headless_intellifiller(tsv_path, args.prompt, config, resolved_paths, selected_rows=batch, reprocess=True, zid=zid, trace_id=batch_trace_id)
        
        try:
            with file_lock(tsv_path):
                comments, headers, data_rows = load_tsv_rows(tsv_path)
            sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
            run_enrich = config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='auto')
            if run_enrich == 'auto':
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, zid=zid, trace_id=batch_trace_id)
            data_rows = sorted_rows
        except Exception as e:
            logger.error(f"Failed to write update JS after IntelliFiller batch: {e}")
    return data_rows

def cmd_reprocess_worker(args):
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    tsv_path = Path(args.tsv)
    
    rows_str = args.rows
    if not rows_str:
        return
        
    selected_rows = [int(r.strip()) for r in rows_str.split(',') if r.strip()]
    lemmas_provider = config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback='intellifiller')
    language = config.get(SEC_SETTINGS, 'default_language', fallback='en')
    target_lang = config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')
    
    m = re.match(r"^(\d{14})", tsv_path.name)
    zid = getattr(args, 'zid', None) or (m.group(1) if m else "session")
    trace_id = getattr(args, 'trace_id', None) or f"{zid}:reprocess:worker"
    results_dir = resolve_results_dir(resolved_paths, config)
    sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id) if results_dir else None
    worker_error = None
    if sess_logger:
        sess_logger.info("Reprocess worker started")
        
    data_rows, headers, role_fields = [], [], {}
    class_cols = []
    
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    try:
        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
            
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)

        # Enforce frequency sort parity so selected_rows match displayed UI table rows
        data_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
        
        run_lemmatizer = config.getboolean(SEC_PIPELINE, 'run_lemmatizer', fallback=True)
        col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
        original_lemmas = {}
        if not run_lemmatizer and col_lemma != -1:
            for row_id in selected_rows:
                if 0 <= row_id < len(data_rows):
                    original_lemmas[row_id] = data_rows[row_id][col_lemma]

        wordfill_cfg = resolve_wordfill_config(config, resolved_paths)
        if wordfill_cfg and wordfill_cfg.get('enabled', False) and col_lemma != -1:
            target_quality = wordfill_cfg.get('target_quality', 'any')
            target_quality_tier = {'any': 0, 'partial': 1, 'full': 2}.get(target_quality, 0)
            remaining_selected = []
            for row_id in selected_rows:
                if 0 <= row_id < len(data_rows):
                    row = data_rows[row_id]
                    if len(row) > col_lemma and row[col_lemma].strip():
                        lemma_val = row[col_lemma].strip()
                        match = find_wordfill_match(lemma_val, language, wordfill_cfg, exclude_path=tsv_path)
                        if match:
                            has_ipa = bool(match.get('WordSourceIPA', '').strip())
                            has_morph = bool(match.get('WordSourceMorphologyAI', '').strip())
                            tier = 2 if (has_ipa and has_morph) else (1 if (has_ipa or has_morph) else 0)
                            if tier >= target_quality_tier:
                                apply_wordfill_to_rows([row], headers, match)
                                logger.info(
                                    f"wordfill (reprocess): pre-filled quality tier {tier} for row {row_id} lemma '{lemma_val}' "
                                    f"from corpus; skipping IntelliFiller."
                                )
                                continue
                remaining_selected.append(row_id)

            if len(remaining_selected) < len(selected_rows):
                if is_sqlite:
                    storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                else:
                    with storage_adapter.file_lock(tsv_path):
                        save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                sorted_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, zid=zid, trace_id=trace_id)
                selected_rows = remaining_selected
        
        if selected_rows and lemmas_provider in ('combined', 'google', 'deepl'):
            try:
                data_rows = _reprocess_worker_stage_fast_path(tsv_path, config, resolved_paths, data_rows, headers, role_fields, selected_rows, lemmas_provider, language, target_lang, zid=zid, trace_id=trace_id)
                safe_write_update_js(tsv_path, data_rows, headers, role_fields, zid=zid, trace_id=trace_id)
            except Exception as e:
                logger.error(f"Failed fast-path translation during reprocess: {e}")

        if selected_rows and lemmas_provider in ('intellifiller', 'combined'):
            try:
                data_rows = _reprocess_worker_stage_intellifiller(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, selected_rows, zid=zid, trace_id=trace_id)
            except Exception as e:
                logger.error(f"Failed IntelliFiller stage during reprocess: {e}")
                worker_error = getattr(e, 'envelope', None) or {
                    "code": "ERR_INTELLIFILLER_FAILED",
                    "message": str(e),
                    "provider": "intellifiller",
                    "details": {}
                }
                if sess_logger:
                    sess_logger.error(f"IntelliFiller stage failed: [{worker_error.get('code')}] {worker_error.get('message')}")
                
        if not worker_error:
            if not run_lemmatizer and col_lemma != -1 and original_lemmas:
                with storage_adapter.file_lock(tsv_path):
                    comments_latest, headers_latest, data_rows_latest = storage_adapter.load_tsv_rows(tsv_path)
                    data_rows_latest = sort_rows_by_frequency(data_rows_latest, headers_latest, language, config, resolved_paths, role_fields=role_fields)
                    for row_id, orig_val in original_lemmas.items():
                        if 0 <= row_id < len(data_rows_latest):
                            data_rows_latest[row_id][col_lemma] = orig_val
                    storage_adapter.save_tsv_rows_safely(tsv_path, comments_latest, headers_latest, data_rows_latest)
                    data_rows = data_rows_latest
                    
            try:
                desk_classification_enabled = config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=True) if config.has_section(SEC_CLASSIFICATION) else True
                kardenwort_workspace = resolved_paths['kardenwort_workspace']
                kw_config = load_kardenwort_config(kardenwort_workspace)
                if desk_classification_enabled and kw_config.has_section(SEC_CLASSIFICATION) and kw_config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=False):
                    # Import core kardenwort loaders
                    import sys
                    if str(kardenwort_workspace / "src") not in sys.path:
                        sys.path.append(str(kardenwort_workspace / "src"))
                    from kardenwort.core.kardenwort import load_classification_dictionaries
                    
                    if kw_config.has_option(SEC_CLASSIFICATION, f'dictionaries_{language}'):
                        dicts = kw_config.get(SEC_CLASSIFICATION, f'dictionaries_{language}', fallback='')
                    else:
                        dicts = kw_config.get(SEC_CLASSIFICATION, 'dictionaries', fallback='')
                    classify_args = []
                    if dicts:
                        for d in dicts.split(','):
                            d = d.strip()
                            if d and '=' in d:
                                name, path_str = d.split('=', 1)
                                name = name.strip()
                                path_str = path_str.strip()
                                
                                # Support prefix path e.g. 3k:data/en/oxford.tsv
                                prefix = ""
                                if ":" in path_str:
                                    parts = path_str.split(":", 1)
                                    possible_prefix = parts[0].strip()
                                    if len(possible_prefix) <= 5 and "/" not in possible_prefix and "\\" not in possible_prefix:
                                        prefix = possible_prefix + ":"
                                        path_str = parts[1].strip()
                                        
                                resolved_path = (kardenwort_workspace / path_str).resolve()
                                classify_args.append(f"{name}={prefix}{resolved_path}")
                                
                    classifications = load_classification_dictionaries(classify_args)
                    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
                    if col_lemma != -1:
                        with storage_adapter.file_lock(tsv_path):
                            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                            data_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
                            for name, c_dict in classifications.items():
                                if name in role_fields and role_fields[name] in headers:
                                    col_idx = headers.index(role_fields[name])
                                    class_cols.append((name, col_idx))
                                    for row_id in selected_rows:
                                        if 0 <= row_id < len(data_rows):
                                            lemma = data_rows[row_id][col_lemma].strip().lower()
                                            val = c_dict.get(lemma, "")
                                            while len(data_rows[row_id]) <= col_idx:
                                                data_rows[row_id].append("")
                                            data_rows[row_id][col_idx] = val
                                            
                            storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            except Exception as e_class:
                logger.error(f"Failed to update classification fields during reprocess: {e_class}")
                if sess_logger:
                    sess_logger.error(f"Failed to update classification fields: {e_class}")
                
    except Exception as e:
        logger.error(f"Unhandled exception in cmd_reprocess_worker: {e}")
        worker_error = {
            "code": "ERR_REPROCESS_FAILED",
            "message": str(e),
            "provider": "desk",
            "details": {}
        }
        if sess_logger:
            sess_logger.error(f"Reprocess failed: {e}")
    finally:
        try:
            status_val = "failed" if worker_error else "success"
            sorted_rows = sort_rows_by_frequency(data_rows, headers, language, config, resolved_paths, role_fields=role_fields)
            safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="finished", status=status_val, class_cols=class_cols, error=worker_error, zid=zid, trace_id=trace_id)
            if sess_logger:
                sess_logger.info("Reprocess finished event emitted")
        except Exception as e:
            logger.error(f"Failed to write finished event in reprocess: {e}")

_update_seq_counter = 0

def write_update_js(tsv_path, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None, class_cols=None, empty_payload=False, config=None, error=None, zid=None, trace_id=None):
    import time
    global _update_seq_counter
    _update_seq_counter += 1
    
    if config is None:
        try:
            config = load_config()[0]
        except Exception:
            config = None

    if zid is None and tsv_path:
        m = re.match(r"^(\d{14})", tsv_path.name)
        if m:
            zid = m.group(1)
    if trace_id is None and zid:
        trace_id = f"{zid}:update"
    
    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    update_js_path = updates_dir / f"{_update_seq_counter:06d}.js"
    
    if empty_payload:
        update_data = {
            "stage": stage,
            "status": status,
            "rows": {}
        }
        if error is not None:
            update_data["error"] = error
        if zid is not None:
            update_data["zid"] = zid
        if trace_id is not None:
            update_data["trace_id"] = trace_id
    else:
        col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
        col_inflected = headers.index(role_fields['inflected']) if 'inflected' in role_fields and role_fields['inflected'] in headers else -1
        col_inflected2 = headers.index("WordSourceInflectedForm2") if "WordSourceInflectedForm2" in headers else -1
        col_quotation = headers.index("Quotation") if "Quotation" in headers else -1
        col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
        col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else -1
        col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else -1
        col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
        col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
        
        rows_data = {}
        for row_id, row in enumerate(data_rows):
            lemma_val = row[col_lemma] if col_lemma != -1 and len(row) > col_lemma else ""
            inflected_val = resolve_row_inflected_form(row, col_inflected, col_inflected2, col_quotation, col_lemma)
            trans_val = row[col_word_dest] if col_word_dest != -1 and len(row) > col_word_dest else ""
            morph_val = row[col_morph] if col_morph != -1 and len(row) > col_morph else ""
            ipa_val = row[col_ipa] if col_ipa != -1 and len(row) > col_ipa else ""
            token_order_val = row[col_token_order] if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).strip() else str(row_id)
            sent_idx_val = row[col_index] if col_index != -1 and len(row) > col_index and str(row[col_index]).strip().isdigit() else "1"
            rows_data[row_id] = {
                "lemma": lemma_val,
                "inflected": inflected_val,
                "trans": trans_val,
                "ipa": ipa_val,
                "morph": morph_val,
                "token_order": token_order_val,
                "sentence_idx": sent_idx_val
            }
            if class_cols:
                class_vals = {}
                for name, col_idx in class_cols:
                    val = row[col_idx] if len(row) > col_idx else ""
                    class_vals[name] = val
                rows_data[row_id]["classifications"] = class_vals
            
        if stage is None:
            # Inline snapshot — only emit rows that have at least one non-empty field
            update_data = {
                row_id: d for row_id, d in rows_data.items()
                if d["trans"] or d["ipa"] or d["morph"] or d["lemma"]
            }
        else:
            # Note: We intentionally do not fallback to reading source_text from .txt when it is None
            # because sending plain text destroys the span DOM established in the frontend.
            if translated_text is None and zid:
                try:
                    storage_adapter = get_storage_adapter(config)
                    if getattr(storage_adapter, 'backend_name', '') == 'sqlite' and hasattr(storage_adapter, 'db'):
                        db_sents = storage_adapter.db.get_sentences_by_session(zid)
                        clean_translations = []
                        if db_sents:
                            for s in sorted(db_sents, key=lambda x: x.get("sentence_index", 1)):
                                s_dest = s.get("sentence_destination")
                                if s_dest and str(s_dest).strip():
                                    clean_translations.append(str(s_dest).strip())
                        if clean_translations:
                            norm_brackets = config.getboolean(SEC_SETTINGS, 'normalize_bracket_spacing', fallback=True) if config else True
                            lines = [html.escape(normalize_bracket_spacing(line) if norm_brackets else line) for line in clean_translations]
                            is_single = True
                            if source_text:
                                stripped_src = source_text.strip()
                                if '\n' in stripped_src or '\r' in stripped_src:
                                    is_single = False
                            else:
                                bundle = storage_adapter.load_session(zid) if hasattr(storage_adapter, 'load_session') else None
                                src_raw = bundle.get("session", {}).get("source_raw_text") if bundle else None
                                if src_raw and ('\n' in src_raw or '\r' in src_raw):
                                    is_single = False
                                elif tsv_path and tsv_path.with_suffix('.txt').exists():
                                    try:
                                        txt = tsv_path.with_suffix('.txt').read_text(encoding='utf-8')
                                        if '\n' in txt or '\r' in txt:
                                            is_single = False
                                    except Exception:
                                        pass
                            if is_single:
                                translated_text = f"<div>{' '.join(lines)}</div>"
                            else:
                                translated_text = "".join(f"<div>{line if line else '&nbsp;'}</div>" for line in lines)
                except Exception as e:
                    logger.debug(f"Failed to read clean translation from SQLite in write_update_js: {e}")

            if translated_text is None:
                if tsv_path:
                    try:
                        parent = tsv_path.parent
                        source_stem_full = tsv_path.stem
                        source_stem = source_stem_full.rsplit('.', 1)[0] if '.' in source_stem_full else source_stem_full
                        for f in parent.glob(f"{source_stem}.*.txt"):
                            if f.stem != source_stem_full:
                                txt_content = f.read_text(encoding='utf-8').strip()
                                if txt_content:
                                    norm_brackets = config.getboolean(SEC_SETTINGS, 'normalize_bracket_spacing', fallback=True) if config else True
                                    lines = [html.escape(normalize_bracket_spacing(line.strip()) if norm_brackets else line.strip()) for line in txt_content.splitlines()]
                                    is_single = True
                                    source_txt_path = tsv_path.with_suffix('.txt')
                                    if source_txt_path.exists():
                                        try:
                                            src_txt = source_txt_path.read_text(encoding='utf-8').strip()
                                            if '\n' in src_txt or '\r' in src_txt:
                                                is_single = False
                                        except Exception:
                                            pass
                                    if is_single:
                                        translated_text = f"<div>{' '.join(lines)}</div>"
                                    else:
                                        translated_text = "".join(f"<div>{line if line else '&nbsp;'}</div>" for line in lines)
                                    break
                    except Exception as e:
                        logger.error(f"Failed to read clean translation text file in write_update_js: {e}")

            if translated_text is None:
                col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
                col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
                if col_sentence_dest != -1:
                    idx_to_sentence = {}
                    for row in data_rows:
                        if len(row) > col_sentence_dest:
                            s = row[col_sentence_dest].strip()
                            if s:
                                idx_val = 0
                                if col_index != -1 and len(row) > col_index and row[col_index].strip():
                                    try:
                                        idx_val = int(row[col_index])
                                    except ValueError:
                                        pass
                                if idx_val not in idx_to_sentence:
                                    idx_to_sentence[idx_val] = s
                    
                    sorted_keys = sorted(idx_to_sentence.keys())
                    norm_brackets = config.getboolean(SEC_SETTINGS, 'normalize_bracket_spacing', fallback=True) if config else True
                    sentences = [html.escape(normalize_bracket_spacing(idx_to_sentence[k]) if norm_brackets else idx_to_sentence[k]) for k in sorted_keys]
                    
                    is_single = True
                    if source_text:
                        stripped_src = source_text.strip()
                        if '\n' in stripped_src or '\r' in stripped_src:
                            is_single = False
                    else:
                        source_txt_path = tsv_path.with_suffix('.txt')
                        if source_txt_path.exists():
                            try:
                                txt = source_txt_path.read_text(encoding='utf-8')
                                stripped_txt = txt.strip()
                                if '\n' in stripped_txt or '\r' in stripped_txt:
                                    is_single = False
                            except Exception:
                                pass

                    if is_single:
                        non_empty = [s for s in sentences if s]
                        if non_empty and all(s == non_empty[0] for s in non_empty):
                            sentences = [non_empty[0]]
                        translated_text = f"<div>{' '.join(sentences)}</div>"
                    else:
                        translated_text = "".join(f"<div>{s}</div>" for s in sentences)
                    
            update_data = {
                "stage": stage,
                "status": status,
                "rows": rows_data
            }
            if source_text:
                update_data["sourceText"] = source_text
            if translated_text:
                update_data["translatedText"] = translated_text
            if error is not None:
                update_data["error"] = error
            if zid is not None:
                update_data["zid"] = zid
            if trace_id is not None:
                update_data["trace_id"] = trace_id
        
    js_content = f"if (typeof window.receiveUpdate === 'function') {{ window.receiveUpdate({json.dumps(update_data)}); }}"
    
    temp_path = update_js_path.with_name(update_js_path.name + '.tmp')
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    for attempt in range(10):
        try:
            os.replace(temp_path, update_js_path)
            break
        except PermissionError:
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to move update js file (attempt {attempt + 1}): {e}")
            time.sleep(0.1)
    else:
        logger.error(f"Failed to atomically move update js file after 10 retries: {update_js_path}")
    return update_js_path

def _progressive_worker_stage_translation(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields):
    m = re.match(r'^(\d{14})', tsv_path.name)
    zid = getattr(args, 'zid', None) or (m.group(1) if m else "unknown")
    trace_id = getattr(args, 'trace_id', None) or f"{zid}:progressive:translation"
    with TraceTimer("background_text_translation", zid, config, resolved_paths):
        return _progressive_worker_stage_translation_impl(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, zid, trace_id=trace_id)

def _progressive_worker_stage_translation_impl(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, zid, trace_id=None):
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
    
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    lang = getattr(args, 'language', None) or config.get(SEC_SETTINGS, 'default_language', fallback='en')
    run_text = config.get(SEC_TRIGGERS, 'run_text_translation', fallback='auto')
    run_base = config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback='auto')
    
    try:
        # check if sentence needs translation
        sentence_translated = False
        if col_sentence_dest != -1:
            if any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows):
                sentence_translated = True
                
        translated_text_emitted = False
        if not sentence_translated and run_text == 'auto':
            source_txt_path = tsv_path.with_suffix('.txt')
            text = ""
            if source_txt_path.exists():
                text = source_txt_path.read_text(encoding='utf-8')
            elif is_sqlite:
                restored = storage_adapter.restore_session(zid)
                text = restored.get("source_raw_text") or restored.get("source_text", "")

            if text:
                main_text_provider = config.get(SEC_PIPELINE, 'text_base_provider', fallback='google')
                col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
                try:
                    def on_chunk_done(partial_translations, _text=text, _col_index=col_index, _col_sentence_dest=col_sentence_dest):
                        c, h, curr_rows = storage_adapter.load_tsv_rows(tsv_path)
                        resolve_translations(
                            _text, getattr(args, 'text_mode', 'single'), curr_rows, _col_index, _col_sentence_dest,
                            partial_translations, tsv_path, c, h,
                            persist=(not is_sqlite), return_single=False
                        )
                        safe_write_update_js(tsv_path, curr_rows, h, role_fields, stage=None, zid=zid, trace_id=trace_id)

                    sentence_translations_raw = translate_source_text(
                        text, getattr(args, 'language', 'en'), args.target_lang, getattr(args, 'text_mode', 'single'), config, resolved_paths, main_text_provider, zid=zid, trace_id=trace_id, chunk_callback=on_chunk_done)
                    
                    # Update sentences table directly in SQLite mode
                    if is_sqlite and isinstance(sentence_translations_raw, dict):
                        for s_idx_raw, trans in sentence_translations_raw.items():
                            if trans and isinstance(trans, str):
                                s_idx = (int(s_idx_raw) + 1) if (isinstance(s_idx_raw, int) or str(s_idx_raw).isdigit()) else 1
                                try:
                                    storage_adapter.update_sentence_translation(zid, s_idx, trans, zid=zid)
                                except Exception:
                                    pass

                    c, h, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                    resolve_translations(
                        text, getattr(args, 'text_mode', 'single'), data_rows, col_index, col_sentence_dest,
                        sentence_translations_raw, tsv_path, c, h,
                        persist=(not is_sqlite), return_single=False
                    )
                    if is_sqlite:
                        slug_match = re.match(r'^\d{14}-(.*?)(?:\.[a-z]{2})?\.tsv$', tsv_path.name, re.IGNORECASE)
                        slug_val = slug_match.group(1) if slug_match else ""
                        storage_adapter.save_session(
                            session_zid=zid,
                            slug=slug_val,
                            source_language=getattr(args, 'language', 'en'),
                            target_language=args.target_lang,
                            text_mode=getattr(args, 'text_mode', 'single'),
                            source_raw_text=text,
                            comments=c,
                            headers=h,
                            data_rows=data_rows,
                            working_tsv_path=tsv_path,
                            zid=zid,
                        )
                    
                    sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                    safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated_text", zid=zid, trace_id=trace_id)
                except Exception as e:
                    logger.error(f"Failed in text translation: {e}")
                    raise
                    
        if run_base == 'auto' and col_lemma != -1:
            lang = getattr(args, 'language', 'en')
            wordfill_cfg = resolve_wordfill_config(config, resolved_paths)
            if wordfill_cfg and wordfill_cfg.get('enabled', False):
                try:
                    wf_applied = False
                    for i, row in enumerate(data_rows):
                        if len(row) > col_lemma and row[col_lemma].strip():
                            lemma_val = row[col_lemma].strip()
                            is_translated = (col_word_dest != -1 and len(row) > col_word_dest and bool(row[col_word_dest].strip()))
                            if not is_translated:
                                match = find_wordfill_match(lemma_val, lang, wordfill_cfg, exclude_path=tsv_path)
                                if match:
                                    apply_wordfill_to_rows([row], headers, match)
                                    wf_applied = True
                                    logger.info(
                                        f"wordfill (progressive): pre-filled {len(match)} field(s) for lemma '{lemma_val}' from corpus."
                                    )
                    if wf_applied:
                        if is_sqlite:
                            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
                            updates = []
                            for row_idx, row in enumerate(data_rows):
                                if col_lemma != -1 and len(row) > col_lemma:
                                    if col_word_dest != -1 and len(row) > col_word_dest and row[col_word_dest].strip():
                                        t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                                        updates.append({
                                            "token_order": t_ord,
                                            "field": "word_destination",
                                            "value": row[col_word_dest],
                                        })
                            if updates:
                                storage_adapter.batch_update_words(session_zid=zid, updates_list=updates, zid=zid)
                        else:
                            with file_lock(tsv_path):
                                comments, h_curr, _ = load_tsv_rows(tsv_path)
                                save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                except Exception as wf_err:
                    logger.warning(f"wordfill (progressive): translation pre-fill step failed: {wf_err}")

            lemmas_to_translate = []
            seen = set()
            for row in data_rows:
                if len(row) > col_lemma and row[col_lemma].strip():
                    val = row[col_lemma].strip()
                    is_translated = (col_word_dest != -1 and len(row) > col_word_dest and bool(row[col_word_dest].strip()))
                    if not is_translated and val not in seen:
                        seen.add(val)
                        lemmas_to_translate.append(val)
            
            translation_order = config.get(SEC_TRANSLATION, 'translation_order', fallback='top_to_bottom').strip().lower()
            if translation_order == 'bottom_to_top':
                lemmas_to_translate = list(reversed(lemmas_to_translate))
            if lemmas_to_translate:
                provider = config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google')
                if provider == 'intellifiller':
                    selected_rows_to_enrich = []
                    col_ipa = headers.index(role_fields.get('ipa', 'WordSourceIPA')) if role_fields.get('ipa', 'WordSourceIPA') in headers else -1
                    col_morph = headers.index(role_fields.get('morphology', 'WordSourceMorphology')) if role_fields.get('morphology', 'WordSourceMorphology') in headers else -1

                    for i, row in enumerate(data_rows):
                        if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip() in lemmas_to_translate:
                            need_dest = col_word_dest == -1 or len(row) <= col_word_dest or not row[col_word_dest].strip()
                            need_ipa = col_ipa != -1 and (len(row) <= col_ipa or not row[col_ipa].strip())
                            need_morph = col_morph != -1 and (len(row) <= col_morph or not row[col_morph].strip())
                            if need_dest or need_ipa or need_morph:
                                selected_rows_to_enrich.append(i)
                            
                    if selected_rows_to_enrich:
                        data_rows = _progressive_worker_stage_enrichment(
                            tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, stage_name="translated", selected_rows=selected_rows_to_enrich
                        )
                    else:
                        sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                        safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", zid=zid, trace_id=trace_id)
                else:
                    chunk_size = config.getint(SEC_TRANSLATION, 'translation_chunk_size', fallback=0)
                    if chunk_size == 0:
                        chunk_size = 15
                    if chunk_size > 0:
                        chunks = [lemmas_to_translate[i:i + chunk_size] for i in range(0, len(lemmas_to_translate), chunk_size)]
                    else:
                        chunks = [lemmas_to_translate]
                        
                    for chunk in chunks:
                        lemma_translations = translate_lemmas_fast_path(chunk, getattr(args, 'language', 'en'), args.target_lang, config, resolved_paths, provider)
                        
                        if is_sqlite:
                            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
                            updates = []
                            for row_idx, row in enumerate(data_rows):
                                if col_lemma != -1 and len(row) > col_lemma:
                                    lemma_val = row[col_lemma]
                                    if lemma_val in lemma_translations and col_word_dest != -1:
                                        trans_val = lemma_translations[lemma_val]
                                        while len(row) <= col_word_dest:
                                            row.append("")
                                        row[col_word_dest] = trans_val
                                        t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                                        updates.append({
                                            "token_order": t_ord,
                                            "field": "word_destination",
                                            "value": trans_val,
                                        })
                            if updates:
                                storage_adapter.batch_update_words(session_zid=zid, updates_list=updates, zid=zid)
                        else:
                            with file_lock(tsv_path):
                                comments, headers, current_rows = load_tsv_rows(tsv_path)
                                for row in current_rows:
                                    if col_lemma != -1 and len(row) > col_lemma:
                                        lemma_val = row[col_lemma]
                                        if col_word_dest != -1:
                                            while len(row) <= col_word_dest:
                                                row.append("")
                                            if lemma_val in lemma_translations:
                                                row[col_word_dest] = lemma_translations[lemma_val]
                                save_tsv_rows_safely(tsv_path, comments, headers, current_rows)
                                data_rows = current_rows
                        
                        sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                        safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage=None, zid=zid, trace_id=trace_id)
                        
                    sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                    safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", zid=zid, trace_id=trace_id)
            else:
                sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", zid=zid, trace_id=trace_id)
        else:
            lang = getattr(args, 'language', 'en')
            sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
            safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", zid=zid, trace_id=trace_id)
    except Exception as e:
        logger.error(f"Failing in translated stage: {e}")
        err_obj = getattr(e, 'envelope', None) or {
            "code": "ERR_TRANSLATION_FAILED",
            "message": str(e),
            "provider": "desk",
            "details": {}
        }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"Translation stage failed: [{err_obj.get('code')}] {err_obj.get('message')}")
        safe_write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated", status="failed", error=err_obj, zid=zid, trace_id=trace_id)
    return data_rows

def _progressive_worker_stage_enrichment(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, stage_name="enrichment", selected_rows=None):
    m = re.match(r'^(\d{14})', tsv_path.name)
    zid = getattr(args, 'zid', None) or (m.group(1) if m else "unknown")
    trace_id = getattr(args, 'trace_id', None) or f"{zid}:progressive:{stage_name}"
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
    try:
        batch_size = config.getint(SEC_SETTINGS, 'intellifiller_batch_size', fallback=30)
        if selected_rows is None:
            selected_rows = list(range(len(data_rows)))
            
        col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
        col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else -1
        col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else -1

        # Protection against empty lines slipping through to IntelliFiller
        valid_selected = []
        source_col = role_fields.get('lemma', 'WordSource')
        source_idx = headers.index(source_col) if source_col in headers else -1

        for r in selected_rows:
            if r < len(data_rows):
                row = data_rows[r]
                if not any(str(cell).strip() for cell in row):
                    continue
                if source_idx != -1 and len(row) > source_idx and not str(row[source_idx]).strip():
                    continue
                
                has_dest = col_word_dest != -1 and len(row) > col_word_dest and str(row[col_word_dest]).strip()
                has_ipa = col_ipa != -1 and len(row) > col_ipa and str(row[col_ipa]).strip()
                has_morph = col_morph != -1 and len(row) > col_morph and str(row[col_morph]).strip()
                
                need_dest = col_word_dest != -1 and not has_dest
                need_ipa = col_ipa != -1 and not has_ipa
                need_morph = col_morph != -1 and not has_morph
                
                if not (need_dest or need_ipa or need_morph):
                    continue
                valid_selected.append(r)
                
        selected_rows = valid_selected
        
        prompt_val = getattr(args, 'prompt', None) or config.get(SEC_LANGUAGES, f"{getattr(args, 'language', 'en')}_prompt", fallback="")
        lang = getattr(args, 'language', None) or config.get(SEC_SETTINGS, 'default_language', fallback='en')

        if is_sqlite:
            storage_adapter.enrich_session_intellifiller(
                session_zid=zid,
                prompt_name=prompt_val,
                selected_rows=selected_rows,
                reprocess=True,
                zid=zid,
                trace_id=trace_id,
            )
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
            sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
            safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage=stage_name, zid=zid, trace_id=trace_id)
        else:
            for i in range(0, len(selected_rows), batch_size):
                batch = selected_rows[i:i + batch_size]
                logger.info(f"Running IntelliFiller for progressive batch {i // batch_size + 1}: {len(batch)} rows.")
                run_headless_intellifiller(tsv_path, prompt_val, config, resolved_paths, selected_rows=batch, reprocess=True)
                
                comments, headers, data_rows = load_tsv_rows(tsv_path)
                sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage=stage_name, zid=zid, trace_id=trace_id)
    except Exception as e:
        logger.error(f"Failing in {stage_name} stage: {e}")
        err_obj = getattr(e, 'envelope', None) or {
            "code": "ERR_ENRICHMENT_FAILED",
            "message": str(e),
            "provider": "intellifiller",
            "details": {}
        }
        results_dir = resolve_results_dir(resolved_paths, config)
        if zid and results_dir:
            sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id)
            sess_logger.error(f"Enrichment stage failed: [{err_obj.get('code')}] {err_obj.get('message')}")
        safe_write_update_js(tsv_path, data_rows, headers, role_fields, stage=stage_name, status="failed", error=err_obj, zid=zid, trace_id=trace_id)
    return data_rows

def cmd_retext(args):
    logger.info("Retext subcommand invoked")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir = resolve_results_dir(resolved_paths, kw_config)
    
    manifest_path = Path(args.selection_manifest).resolve()
    if not manifest_path.exists():
        print_structured_error("INVALID_STATE", f"Selection manifest not found: {manifest_path}")
        sys.exit(1)
        
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            manifest = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_STATE", f"Failed to parse selection manifest: {e}")
        sys.exit(1)
        
    zid = manifest.get("zid")
    if not zid:
        print_structured_error("INVALID_STATE", "Selection manifest must contain 'zid'")
        sys.exit(1)
        
    lang = args.language or config.get(SEC_SETTINGS, 'default_language', fallback='en')
    
    tsv_path_str = manifest.get("tsv_path")
    if tsv_path_str:
        tsv_path = Path(tsv_path_str)
    else:
        tsv_path = find_working_tsv(results_dir, zid, lang)
        
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    if is_sqlite:
        tsv_path = Path(tsv_path_str) if tsv_path_str else (results_dir / f"{zid}.{lang}.tsv")
    else:
        if not tsv_path or not tsv_path.exists():
            print_structured_error("DESK_FAILED", f"Working TSV file not found for session ZID {zid}")
            sys.exit(1)
        
    logger.info("Triggering async retext worker.")
    
    python_exe = (resolved_paths.get('kardenwort_python') if resolved_paths else None) or sys.executable
    desk_script = Path(__file__).resolve()
    
    trace_id = getattr(args, 'trace_id', None) or (f"{zid}:retext:worker" if zid else None)
    cmd = [
        str(python_exe),
        str(desk_script),
        "retext-worker",
        "--tsv", str(tsv_path),
        "--language", lang,
        "--text-mode", args.text_mode
    ]
    if zid:
        cmd.extend(["--zid", str(zid)])
    if trace_id:
        cmd.extend(["--trace-id", str(trace_id)])
    if args.config:
        cmd.extend(["--config", args.config])
        
    log_path = tsv_path.with_suffix('.log')
    try:
        log_file = open(log_path, 'a', encoding='utf-8')
    except Exception:
        log_file = subprocess.DEVNULL
        
    try:
        if sys.platform == 'win32':
            creationflags = 0x08000000 | 0x00000200
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                creationflags=creationflags,
                close_fds=True
            )
        else:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                close_fds=True
            )
        retext_payload: RetextStartedPayload = {"retext_started": True}
        emit_payload(retext_payload)
    except Exception as e:
        logger.error(f"Failed to launch retext worker: {e}")
        print_structured_error("DESK_FAILED", f"Failed to launch worker: {e}")
        sys.exit(1)

def cmd_retext_worker(args):
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    tsv_path = Path(args.tsv)
    language = args.language
    text_mode = args.text_mode
    target_lang = config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')
    m = re.match(r"^(\d{14})", tsv_path.name)
    zid = getattr(args, 'zid', None) or (m.group(1) if m else "session")
    trace_id = getattr(args, 'trace_id', None) or f"{zid}:retext:worker"
    results_dir = resolve_results_dir(resolved_paths, config)
    sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id) if results_dir else None
    
    worker_error = None
    sentence_translations = None
    translated_html = None
    if sess_logger:
        sess_logger.info("Retext worker started")
    
    storage_adapter = get_storage_adapter(config, resolved_paths)
    is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

    try:
        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
            
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        
        if is_sqlite:
            restored = storage_adapter.restore_session(zid)
            text = restored.get("source_text", "")
            if not text:
                text = restored.get("session", {}).get("source_raw_text", "")
        else:
            source_text_path = tsv_path.with_suffix('.txt')
            if not source_text_path.exists():
                worker_error = {
                    "code": "ERR_SOURCE_FILE_MISSING",
                    "message": "Source text file missing for retext",
                    "provider": "desk",
                    "details": {}
                }
                if sess_logger:
                    sess_logger.error(worker_error["message"])
                return
            text = source_text_path.read_text(encoding='utf-8')
        text_reprocess_provider = config.get(SEC_PIPELINE, 'text_reprocess_provider', fallback='deepl')
        logger.info(f"Retext worker translating using provider {text_reprocess_provider}")
        if sess_logger:
            sess_logger.info(f"Translating source text via provider '{text_reprocess_provider}'")
        
        slug = generate_slug(text)
        
        try:
            sentence_translations = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, text_reprocess_provider, zid=zid, trace_id=trace_id)
        except TranslationAlignmentError as tae:
            logger.error(f"Retext worker translation alignment error: {tae}")
            if sess_logger:
                sess_logger.warning(f"Translation alignment partial fallback: {tae}")
            sentence_translations = tae.partial_dict
        except TranslationException as te:
            worker_error = te.envelope
            if sess_logger:
                sess_logger.error(f"[{te.code}] {te.message}")
            raise te
        except Exception as te_other:
            worker_error = {
                "code": "ERR_TRANSLATION_FAILED",
                "message": str(te_other),
                "provider": text_reprocess_provider,
                "details": {}
            }
            if sess_logger:
                sess_logger.error(f"Retext translation failed: {te_other}")
            raise te_other
            
        translated_html = format_translated_html(sentence_translations, text_mode=text_mode, text=text, config=config)

        if not is_sqlite:
            target_text_path = tsv_path.parent / f"{zid}-{slug}.{target_lang}.txt"
            eff_mode = _effective_text_mode(text, text_mode)
            _write_translation_txt(text, eff_mode, sentence_translations, target_text_path, save_flag=True, overwrite=True)
        
        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
        col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
        col_text_dest = headers.index(role_fields['text_destination']) if 'text_destination' in role_fields and role_fields['text_destination'] in headers else -1
        col_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1
        
        resolve_translations(
            text, text_mode, data_rows, col_index, col_sentence_dest,
            sentence_translations, tsv_path, comments, headers,
            col_text_dest=col_text_dest, persist=True, return_single=False,
            adapter=storage_adapter, config=config, resolved_paths=resolved_paths
        )
        if sess_logger:
            sess_logger.info("Retext completed successfully")
    except Exception as e:
        logger.error(f"Unhandled exception in cmd_retext_worker: {e}")
        if not worker_error:
            worker_error = {
                "code": "ERR_RETEXT_FAILED",
                "message": str(e),
                "provider": "desk",
                "details": {}
            }
    finally:
        try:
            with storage_adapter.file_lock(tsv_path):
                comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
            mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
            role_fields = get_role_fields(mapping, headers)
            # source_text="" because retext never changes the source text;
            # sending it would cause receiveUpdate to wipe the span DOM.
            status_val = "failed" if worker_error else "success"
            safe_write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished", status=status_val, source_text="", translated_text=translated_html, error=worker_error, zid=zid, trace_id=trace_id, config=config)
        except Exception as fe:
            logger.error(f"Failed to write finished event in retext: {fe}")
def get_batch_sibling_tsvs(working_tsv_path, max_delta_seconds=120):
    from datetime import datetime
    zid_match = re.match(r'^(\d{14})', working_tsv_path.name)
    if not zid_match:
        return []
    my_zid = zid_match.group(1)
    try:
        dt_my = datetime.strptime(my_zid, '%Y%m%d%H%M%S')
    except Exception:
        return []

    siblings = []
    for sibling in working_tsv_path.parent.glob("*.tsv"):
        if sibling == working_tsv_path:
            continue
        sib_match = re.match(r'^(\d{14})', sibling.name)
        if not sib_match:
            continue
        sib_zid = sib_match.group(1)
        try:
            dt_sib = datetime.strptime(sib_zid, '%Y%m%d%H%M%S')
            if abs((dt_sib - dt_my).total_seconds()) <= max_delta_seconds:
                siblings.append((sib_zid, sibling))
        except Exception:
            continue
            
    siblings.sort(key=lambda x: x[0])
    return [p for _, p in siblings]

def wait_for_older_siblings_in_batch(working_tsv_path, mapping, lemma_base_provider=None, data_rows_count=0, is_sqlite=False):
    if is_sqlite:
        return
    import time
    from datetime import datetime
    import threading
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        has_watchdog = True
    except ImportError:
        has_watchdog = False

    zid_match = re.match(r'^(\d{14})', working_tsv_path.name)
    if not zid_match: return
    my_zid = zid_match.group(1)
    
    max_wait = 30.0
    
    def check_siblings():
        sibling_tsvs = get_batch_sibling_tsvs(working_tsv_path)
        if not sibling_tsvs:
            return True
            
        zids = [re.match(r'^(\d{14})', s.name).group(1) for s in sibling_tsvs if re.match(r'^(\d{14})', s.name)]
        zids.append(my_zid)
        master_zid = min(zids)
        
        if my_zid == master_zid:
            has_younger = any(z > my_zid for z in zids)
            if has_younger:
                cut_done_marker = working_tsv_path.with_suffix('.the_cut_done')
                if not cut_done_marker.exists():
                    return False
            return True

        for sibling in sibling_tsvs:
            sib_match = re.match(r'^(\d{14})', sibling.name)
            if not sib_match: continue
            sib_zid = sib_match.group(1)
            
            try:
                dt_my = datetime.strptime(my_zid, '%Y%m%d%H%M%S')
                dt_sib = datetime.strptime(sib_zid, '%Y%m%d%H%M%S')
                diff_sec = (dt_my - dt_sib).total_seconds()
                
                # Check if it's an OLDER sibling from the SAME batch
                if 0 < diff_sec <= 120:
                    marker_file = sibling.with_suffix('.base_translation_done')
                    if marker_file.exists():
                        continue
                        
                    with file_lock(sibling):
                        _, headers, data_rows = load_tsv_rows(sibling)
                    role_fields = get_role_fields(mapping, headers)
                    if not is_base_translation_finished(headers, data_rows, role_fields, lemma_base_provider=lemma_base_provider):
                        return False
            except Exception as e:
                logger.warning(f"Error checking sibling TSV {sibling}: {e}")
        return True

    if check_siblings():
        return

    if not has_watchdog:
        start_wait = time.time()
        while time.time() - start_wait < max_wait:
            if check_siblings():
                break
            time.sleep(1)
        return

    event_cond = threading.Condition()
    class SiblingChangeHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.src_path.endswith('.tsv') or event.src_path.endswith('.base_translation_done') or event.src_path.endswith('.the_cut_done'):
                with event_cond:
                    event_cond.notify_all()
        def on_created(self, event):
            if event.src_path.endswith('.tsv') or event.src_path.endswith('.base_translation_done') or event.src_path.endswith('.the_cut_done'):
                with event_cond:
                    event_cond.notify_all()
                    
    observer = Observer()
    handler = SiblingChangeHandler()
    observer.schedule(handler, path=str(working_tsv_path.parent), recursive=False)
    observer.start()
    
    try:
        start_wait = time.time()
        with event_cond:
            while time.time() - start_wait < max_wait:
                if check_siblings():
                    break
                event_cond.wait(timeout=1.0)
    finally:
        observer.stop()
        observer.join()

def wait_for_older_siblings_enrichment_in_batch(working_tsv_path, data_rows_count=0, is_sqlite=False):
    if is_sqlite:
        return
    import time
    from datetime import datetime
    import threading
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        has_watchdog = True
    except ImportError:
        has_watchdog = False

    zid_match = re.match(r'^(\d{14})', working_tsv_path.name)
    if not zid_match: return
    my_zid = zid_match.group(1)
    
    max_wait = 30.0
    
    def check_siblings():
        sibling_tsvs = get_batch_sibling_tsvs(working_tsv_path)
        if not sibling_tsvs:
            return True
            
        zids = [re.match(r'^(\d{14})', s.name).group(1) for s in sibling_tsvs if re.match(r'^(\d{14})', s.name)]
        zids.append(my_zid)
        master_zid = min(zids)

        found_older = False
        for sibling in sibling_tsvs:
            sib_match = re.match(r'^(\d{14})', sibling.name)
            if not sib_match: continue
            sib_zid = sib_match.group(1)
            
            if sib_zid == master_zid and my_zid != master_zid:
                # Children do not wait for the master window in enrichment.
                continue
                
            try:
                dt_my = datetime.strptime(my_zid, '%Y%m%d%H%M%S')
                dt_sib = datetime.strptime(sib_zid, '%Y%m%d%H%M%S')
                diff_sec = (dt_my - dt_sib).total_seconds()
                
                # Check if it's an OLDER sibling from the SAME batch
                if 0 < diff_sec <= 120:
                    found_older = True
                    marker_file = sibling.with_suffix('.enrichment_done')
                    if not marker_file.exists():
                        return False
            except Exception as e:
                logger.warning(f"Error checking sibling TSV {sibling} for enrichment: {e}")
        
        # Master role auto-detection: if no older siblings exist, wait for younger siblings (children).
        # This ensures cross-pollination happens after all children finish enrichment.
        if my_zid == master_zid:
            has_younger = any(z > my_zid for z in zids)
            if has_younger:
                cut_done_marker = working_tsv_path.with_suffix('.the_cut_done')
                if not cut_done_marker.exists():
                    return False
                    
                for sibling in sibling_tsvs:
                    sib_match = re.match(r'^(\d{14})', sibling.name)
                    if not sib_match: continue
                    sib_zid = sib_match.group(1)
                    try:
                        dt_my = datetime.strptime(my_zid, '%Y%m%d%H%M%S')
                        dt_sib = datetime.strptime(sib_zid, '%Y%m%d%H%M%S')
                        diff_sec = (dt_sib - dt_my).total_seconds()  # reversed: younger siblings
                        if 0 < diff_sec <= 120:
                            marker_file = sibling.with_suffix('.enrichment_done')
                            if not marker_file.exists():
                                return False
                    except Exception as e:
                        logger.warning(f"Error checking younger sibling TSV {sibling} for enrichment: {e}")
        
        return True

    if check_siblings():
        return

    if not has_watchdog:
        start_wait = time.time()
        while time.time() - start_wait < max_wait:
            if check_siblings():
                break
            time.sleep(1)
        return

    event_cond = threading.Condition()
    class SiblingChangeHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.src_path.endswith('.tsv') or event.src_path.endswith('.enrichment_done') or event.src_path.endswith('.the_cut_done'):
                with event_cond:
                    event_cond.notify_all()
        def on_created(self, event):
            if event.src_path.endswith('.tsv') or event.src_path.endswith('.enrichment_done') or event.src_path.endswith('.the_cut_done'):
                with event_cond:
                    event_cond.notify_all()
                    
    observer = Observer()
    handler = SiblingChangeHandler()
    observer.schedule(handler, path=str(working_tsv_path.parent), recursive=False)
    observer.start()
    
    try:
        start_wait = time.time()
        with event_cond:
            while time.time() - start_wait < max_wait:
                if check_siblings():
                    break
                event_cond.wait(timeout=1.0)
    finally:
        observer.stop()
        observer.join()

def cross_pollinate_from_siblings(working_tsv_path, data_rows, headers, role_fields, storage_adapter=None, is_sqlite=False):
    if not data_rows:
        return data_rows
        
    col_lemma = headers.index(role_fields.get('lemma', 'WordSource')) if role_fields and role_fields.get('lemma', 'WordSource') in headers else -1
    
    if col_lemma == -1:
        return data_rows

    zid_match = re.match(r'^(\d{14})', working_tsv_path.name)
    if not zid_match:
        return data_rows
    my_zid = zid_match.group(1)

    missing_lemmas = {}
    for i, row in enumerate(data_rows):
        if len(row) > col_lemma:
            lemma = row[col_lemma].strip()
            if not lemma: continue
            if lemma not in missing_lemmas:
                missing_lemmas[lemma] = []
            missing_lemmas[lemma].append(i)

    if not missing_lemmas:
        return data_rows

    modified = False
    
    if is_sqlite:
        db = getattr(storage_adapter, 'db', None)
        if not db:
            from kardenwort_db import KardenwortDB
            db = KardenwortDB()

        sibling_zids = []
        try:
            from datetime import datetime
            dt_my = datetime.strptime(my_zid, '%Y%m%d%H%M%S')
            with db.get_connection(read_only=True) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT zid FROM sessions WHERE deleted_at IS NULL AND zid != ?;", (my_zid,))
                for row in cursor.fetchall():
                    szid = row["zid"] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
                    if not re.match(r'^\d{14}', str(szid)):
                        continue
                    try:
                        dt_sib = datetime.strptime(str(szid)[:14], '%Y%m%d%H%M%S')
                        if abs((dt_sib - dt_my).total_seconds()) <= 120:
                            sibling_zids.append((str(szid), dt_sib))
                    except Exception:
                        continue
            sibling_zids.sort(key=lambda x: x[0])
        except Exception as e:
            logger.warning(f"Failed to query SQLite sibling sessions for cross-pollination: {e}")

        for sib_zid, _ in sibling_zids:
            try:
                sib_words = db.get_words_by_session(sib_zid, parse_json=True)
                for w in sib_words:
                    sib_lemma = str(w.get("lemma") or "").strip()
                    if not sib_lemma or sib_lemma not in missing_lemmas:
                        continue
                    
                    # Prepare list of eligible word-level attributes to transfer
                    field_val_pairs = []
                    if w.get("word_destination"):
                        field_val_pairs.append(("WordDestination", w["word_destination"]))
                    if w.get("morphology"):
                        field_val_pairs.append(("WordSourceMorphologyAI", w["morphology"]))
                    if w.get("ipa"):
                        field_val_pairs.append(("WordSourceIPA", w["ipa"]))
                    
                    extra = w.get("extra_fields") or {}
                    if isinstance(extra, dict):
                        for k, v in extra.items():
                            if is_wordfill_eligible(k) and v:
                                field_val_pairs.append((k, v))
                    
                    for col_name, sib_val in field_val_pairs:
                        if not is_wordfill_eligible(col_name):
                            continue
                        if col_name not in headers:
                            continue
                        target_c = headers.index(col_name)
                        
                        sib_val_str = str(sib_val).strip()
                        if sib_val_str and 'skeleton-loader' not in sib_val_str and sib_val_str != '[FAILED]' and not sib_val_str.startswith('[Error'):
                            for target_row_idx in missing_lemmas[sib_lemma]:
                                target_row = data_rows[target_row_idx]
                                if len(target_row) <= target_c:
                                    target_row.extend([''] * (target_c - len(target_row) + 1))
                                target_val = target_row[target_c].strip()
                                if not target_val or 'skeleton-loader' in target_val or target_val == '[FAILED]' or target_val.startswith('[Error'):
                                    target_row[target_c] = sib_val_str
                                    modified = True
            except Exception as e:
                logger.warning(f"Failed to cross-pollinate from SQLite sibling {sib_zid}: {e}")

        if modified and storage_adapter:
            try:
                storage_adapter.save_session(session_zid=my_zid, headers=headers, data_rows=data_rows)
            except Exception as e:
                logger.warning(f"Failed to save cross-pollinated SQLite session {my_zid}: {e}")

    else:
        for sibling in get_batch_sibling_tsvs(working_tsv_path):
            try:
                with file_lock(sibling):
                    sib_comments, sib_headers, sib_rows = load_tsv_rows(sibling)
            except Exception:
                continue
                
            sib_col_lemma = sib_headers.index(role_fields.get('lemma', 'WordSource')) if role_fields and role_fields.get('lemma', 'WordSource') in sib_headers else -1
            if sib_col_lemma == -1: continue

            col_map = {}
            for sib_c, h in enumerate(sib_headers):
                if h in headers and sib_c != sib_col_lemma and is_wordfill_eligible(h):
                    col_map[sib_c] = headers.index(h)

            for sib_row in sib_rows:
                if len(sib_row) > sib_col_lemma:
                    sib_lemma = sib_row[sib_col_lemma].strip()
                    if sib_lemma in missing_lemmas:
                        for target_row_idx in missing_lemmas[sib_lemma]:
                            target_row = data_rows[target_row_idx]
                            
                            max_target_col = max(col_map.values()) if col_map else -1
                            if len(target_row) <= max_target_col:
                                target_row.extend([''] * (max_target_col - len(target_row) + 1))
                                
                            for sib_c, target_c in col_map.items():
                                if len(sib_row) > sib_c:
                                    sib_val = sib_row[sib_c].strip()
                                    if sib_val and 'skeleton-loader' not in sib_val and sib_val != '[FAILED]' and not sib_val.startswith('[Error'):
                                        target_val = target_row[target_c].strip()
                                        if not target_val or 'skeleton-loader' in target_val or target_val == '[FAILED]' or target_val.startswith('[Error'):
                                            target_row[target_c] = sib_row[sib_c]
                                            modified = True

        if modified:
            with file_lock(working_tsv_path):
                comments, headers_latest, _ = load_tsv_rows(working_tsv_path)
                save_tsv_rows_safely(working_tsv_path, comments, headers_latest, data_rows)

    return data_rows

def cmd_progressive_worker(args):
    tsv_path = Path(args.tsv)
    lock_target = Path(str(tsv_path) + ".worker")
    with file_lock(lock_target):
        log_path = tsv_path.with_suffix('.log')
    file_handler = None
    m = re.match(r'^(\d{14})', tsv_path.name)
    zid = getattr(args, 'zid', None) or (m.group(1) if m else "unknown")
    trace_id = getattr(args, 'trace_id', None) or f"{zid}:progressive:worker"
    worker_error = None
    try:
        try:
            file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
            file_handler.setFormatter(JSONFormatter())
            logger.addHandler(file_handler)
        except Exception as log_err:
            sys.stderr.write(f"Warning: Failed to setup progressive-worker log file: {log_err}\n")
            
        logger.info("Progressive-worker subcommand invoked")
        config, resolved_paths, goldendict, _wordfill = load_config(args.config)
        storage_adapter = get_storage_adapter(config, resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
        results_dir = resolve_results_dir(resolved_paths, config)
        sess_logger = SessionLogger(zid, results_dir, trace_id=trace_id) if results_dir else None
        if sess_logger:
            sess_logger.info("Progressive worker started")
        import os
        os.environ["KARDEN_ACTIVE_TEXT_MODE"] = getattr(args, 'text_mode', 'single')
        
        if not is_sqlite and not tsv_path.exists():
            return
            
        comments, headers, data_rows = [], [], []
        role_fields = {}
        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
            if not data_rows:
                return
            mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
            role_fields = get_role_fields(mapping, headers)
            
        lang = getattr(args, 'language', None) or config.get(SEC_SETTINGS, 'default_language', fallback='en')
        sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
        # Write initial source stage immediately so UI renders without delay
        safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="source", zid=zid, trace_id=trace_id)
            
        base_provider = config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google')
        has_siblings = bool(get_batch_sibling_tsvs(tsv_path)) or getattr(args, 'text_mode', 'single') == 'multi' or is_sqlite
        if has_siblings:
            wait_for_older_siblings_in_batch(tsv_path, mapping, lemma_base_provider=base_provider, data_rows_count=len(data_rows), is_sqlite=is_sqlite)
            data_rows = cross_pollinate_from_siblings(tsv_path, data_rows, headers, role_fields, storage_adapter=storage_adapter, is_sqlite=is_sqlite)
            
        try:
            run_base = config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback='auto')
            run_text = config.get(SEC_TRIGGERS, 'run_text_translation', fallback='auto')
            run_enrich = config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='auto')
            enrich_provider = config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback='intellifiller')

            # 1. Base Translation Stage
            if run_base == 'auto' or run_text == 'auto':
                data_rows = _progressive_worker_stage_translation(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields)
                
            if not is_sqlite:
                try:
                    tsv_path.with_suffix('.base_translation_done').touch()
                except Exception:
                    pass
                    
            # 2. Enrichment Stage
            skip_intellifiller = getattr(args, 'skip_intellifiller', False) or run_enrich == 'manual' or enrich_provider == 'none'
            if has_siblings:
                wait_for_older_siblings_enrichment_in_batch(tsv_path, data_rows_count=len(data_rows), is_sqlite=is_sqlite)
                zid_part = tsv_path.name.split('-')[0] if '-' in tsv_path.name else "unknown"
                with TraceTimer("cross_pollinate_from_siblings", zid_part, config, resolved_paths):
                    data_rows = cross_pollinate_from_siblings(tsv_path, data_rows, headers, role_fields, storage_adapter=storage_adapter, is_sqlite=is_sqlite)
            if not skip_intellifiller:
                data_rows = _progressive_worker_stage_enrichment(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields)

        except SystemExit as se:
            raise se
        except Exception as e:
            logger.error(f"Unhandled exception in cmd_progressive_worker: {e}")
            worker_error = {
                "code": "ERR_PROGRESSIVE_WORKER_FAILED",
                "message": str(e),
                "provider": "desk",
                "details": {}
            }
            if sess_logger:
                sess_logger.error(f"Progressive worker unhandled exception: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            # Final sweep for FAILED to prevent stuck skeleton loaders
            try:
                run_base = config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback='auto')
                if run_base == 'auto':
                    with storage_adapter.file_lock(tsv_path):
                        comments, headers_latest, current_rows = storage_adapter.load_tsv_rows(tsv_path)
                        col_lemma = headers_latest.index(role_fields.get('lemma', 'WordSource')) if role_fields and role_fields.get('lemma', 'WordSource') in headers_latest else -1
                        col_word_dest = headers_latest.index(role_fields.get('word_translation', 'WordDestination')) if role_fields and role_fields.get('word_translation', 'WordDestination') in headers_latest else -1
                        col_token_order = headers_latest.index("TokenOrder") if "TokenOrder" in headers_latest else -1
                        
                        modified_sweep = False
                        updates = []
                        for row_idx, row in enumerate(current_rows):
                            if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
                                if col_word_dest != -1:
                                    if len(row) <= col_word_dest:
                                        row.extend([''] * (col_word_dest - len(row) + 1))
                                    if not row[col_word_dest].strip() or 'skeleton-loader' in row[col_word_dest]:
                                        row[col_word_dest] = ""
                                        modified_sweep = True
                                        if is_sqlite:
                                            t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                                            updates.append({
                                                "token_order": t_ord,
                                                "field": "word_destination",
                                                "value": "",
                                            })
                        if modified_sweep:
                            if is_sqlite:
                                if updates:
                                    storage_adapter.batch_update_words(session_zid=zid, updates_list=updates, zid=zid)
                            else:
                                save_tsv_rows_safely(tsv_path, comments, headers_latest, current_rows)
                        data_rows = current_rows
            except Exception as e:
                logger.error(f"Error in progressive worker FAILED sweep: {e}")

            # 3. Finished Event
            if not is_sqlite:
                try:
                    tsv_path.with_suffix('.base_translation_done').touch(exist_ok=True)
                except Exception:
                    pass
                try:
                    tsv_path.with_suffix('.enrichment_done').touch(exist_ok=True)
                except Exception:
                    pass
            try:
                status_val = "failed" if worker_error else "success"
                sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, config, resolved_paths, role_fields=role_fields)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="finished", status=status_val, error=worker_error, zid=zid, trace_id=trace_id)
                if tsv_path.exists():
                    import os
                    os.utime(tsv_path, None)
                if sess_logger:
                    sess_logger.info(f"Progressive worker finished (status={status_val})")
            except Exception as e:
                logger.error(f"Failed to write finished event: {e}")
    finally:
        if file_handler:
            logger.removeHandler(file_handler)
            file_handler.close()




def core_edit_save(tsv_path_or_session, deltas, config, resolved_paths, fingerprint=None, zid=None, language=None, trace_id=None):
    if zid is None:
        zid = generate_unique_zid()

    storage_adapter = get_storage_adapter(config, resolved_paths)
    session_zid = None
    if isinstance(tsv_path_or_session, str) and (tsv_path_or_session.strip().isdigit() or len(tsv_path_or_session.strip()) >= 14):
        session_zid = extract_zid(tsv_path_or_session.strip()) or tsv_path_or_session.strip()
    elif tsv_path_or_session:
        session_zid = extract_zid(str(tsv_path_or_session))

    if storage_adapter.backend_name == 'sqlite' and session_zid:
        bundle = storage_adapter.load_session(session_zid)
        if bundle and bundle.get("session"):
            mapping_file = resolved_paths.get('anki_mapping_file') if resolved_paths else None
            mapping = load_anki_mapping(mapping_file) if mapping_file else None
            editable_cols = []
            role_fields = {}
            if mapping:
                if hasattr(mapping, 'has_section') and mapping.has_section('desk_editable'):
                    editable_cols = [c.strip() for c in mapping.get('desk_editable', 'editable_columns', fallback='').split(',') if c.strip()]
                if hasattr(mapping, 'has_section') and mapping.has_section('desk_columns'):
                    role_fields = {role: field for field, role in mapping['desk_columns'].items()}
            selected_col_name = role_fields.get('selected', 'DeskSelected')
            if selected_col_name not in editable_cols:
                editable_cols.append(selected_col_name)

            restored = storage_adapter.restore_session(session_zid)
            current_data_rows = restored.get("data_rows", [])
            if fingerprint:
                current_fp = compute_content_fingerprint(current_data_rows)
                if fingerprint != current_fp:
                    raise StructuredError(ErrorCode.ROW_STALE, f"Row content hash mismatch. Rendered: {fingerprint}, Current: {current_fp}")

            for delta in deltas:
                row_id = delta.get("row_id")
                token_order = delta.get("token_order") if delta.get("token_order") is not None else row_id
                sentence_idx = delta.get("sentence_idx")
                col_name = delta.get("column")
                val = delta.get("value")

                if row_id is None or col_name is None or val is None:
                    raise StructuredError(ErrorCode.INVALID_STATE, "Each delta must have 'row_id', 'column', and 'value'")

                if col_name == "_delete":
                    with storage_adapter.db.get_connection(zid=zid) as conn:
                        conn.execute("DELETE FROM words WHERE session_zid = ? AND token_order = ?;", (session_zid, token_order))
                    continue

                if col_name not in editable_cols and col_name.lower() not in [c.lower() for c in editable_cols]:
                    raise StructuredError(ErrorCode.DESK_FAILED, f"Column '{col_name}' is not inline-editable.")

                if col_name in (selected_col_name, "DeskSelected", "selected"):
                    storage_adapter.update_word_selection(session_zid, sentence_idx=sentence_idx, token_order=token_order, selected=val, zid=zid)
                else:
                    storage_adapter.update_word(session_zid, sentence_idx=sentence_idx, token_order=token_order, field=col_name, value=val, zid=zid)

            updated_restored = storage_adapter.restore_session(session_zid)
            new_fingerprint = compute_content_fingerprint(updated_restored.get("data_rows", []))

            # Mirror updates to TSV file if it exists on disk
            tsv_path = None
            if isinstance(tsv_path_or_session, Path) and tsv_path_or_session.exists():
                tsv_path = tsv_path_or_session
            elif isinstance(tsv_path_or_session, str) and Path(tsv_path_or_session).exists():
                tsv_path = Path(tsv_path_or_session)
            elif resolved_paths and 'kardenwort_workspace' in resolved_paths:
                try:
                    kw_config = load_kardenwort_config(resolved_paths['kardenwort_workspace'])
                    results_dir = resolve_results_dir(resolved_paths, kw_config)
                    lang = language or config.get(SEC_SETTINGS, 'default_language', fallback='en')
                    tsv_path = find_working_tsv(results_dir, str(session_zid), lang)
                except Exception:
                    pass

            if tsv_path and tsv_path.exists():
                try:
                    with file_lock(tsv_path):
                        comments, headers, data_rows = load_tsv_rows(tsv_path)
                        col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
                        for delta in deltas:
                            r_id = delta.get("row_id")
                            t_ord = delta.get("token_order")
                            c_name = delta.get("column")
                            v_val = delta.get("value")
                            target_row_idx = None
                            if col_token_order != -1 and t_ord is not None:
                                for idx, r in enumerate(data_rows):
                                    if r is not None and len(r) > col_token_order and str(r[col_token_order]).strip() == str(t_ord):
                                        target_row_idx = idx
                                        break
                            if target_row_idx is None and r_id is not None and 0 <= r_id < len(data_rows):
                                target_row_idx = r_id

                            if target_row_idx is not None:
                                if c_name == "_delete":
                                    data_rows[target_row_idx] = None
                                elif c_name in headers and data_rows[target_row_idx] is not None:
                                    data_rows[target_row_idx][headers.index(c_name)] = v_val
                        data_rows = [r for r in data_rows if r is not None]
                        save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                except Exception as e:
                    logger.warning(f"Failed updating mirror TSV during SQLite edit-save: {e}")

            results_dir = resolve_results_dir(resolved_paths, config) if resolved_paths and config else None
            if (zid or session_zid) and results_dir:
                sess_logger = SessionLogger(zid or session_zid, results_dir, trace_id=(trace_id or f"{zid or session_zid}:edit-save"))
                sess_logger.info(f"Saved {len(deltas)} edit delta(s) via SQLite to session {session_zid}")

            return {
                "status": "success",
                "fingerprint": new_fingerprint,
                "session_zid": session_zid,
                "zid": zid
            }

    if isinstance(tsv_path_or_session, Path):
        tsv_path = tsv_path_or_session
    elif isinstance(tsv_path_or_session, str) and (Path(tsv_path_or_session).exists() or '\\' in tsv_path_or_session or '/' in tsv_path_or_session):
        tsv_path = Path(tsv_path_or_session)
    else:
        kardenwort_workspace = resolved_paths['kardenwort_workspace']
        kw_config = load_kardenwort_config(kardenwort_workspace)
        results_dir = resolve_results_dir(resolved_paths, kw_config)
        lang = language or config.get(SEC_SETTINGS, 'default_language', fallback='en')
        tsv_path = find_working_tsv(results_dir, str(tsv_path_or_session), lang)

    if not tsv_path or not tsv_path.exists():
        raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found: {tsv_path}")

    if check_coordination_busy(tsv_path):
        raise StructuredError(ErrorCode.ROW_BUSY, f"Working TSV file is locked by a background worker: {tsv_path.name}")

    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    editable_cols = [c.strip() for c in mapping.get('desk_editable', 'editable_columns', fallback='').split(',') if c.strip()]

    with file_lock(tsv_path):
        try:
            comments, headers, data_rows = load_tsv_rows(tsv_path)
        except Exception as e:
            raise StructuredError(ErrorCode.DESK_FAILED, f"Failed to load working TSV: {e}")

        if fingerprint:
            current_fp = compute_content_fingerprint(data_rows)
            if fingerprint != current_fp:
                raise StructuredError(ErrorCode.ROW_STALE, f"Row content hash mismatch. Rendered: {fingerprint}, Current: {current_fp}")

        role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers}
        selected_col_name = role_fields.get('selected', 'DeskSelected')
        if selected_col_name not in editable_cols:
            editable_cols.append(selected_col_name)

        for delta in deltas:
            row_id = delta.get("row_id")
            col_name = delta.get("column")
            val = delta.get("value")

            if row_id is None or col_name is None or val is None:
                raise StructuredError(ErrorCode.INVALID_STATE, "Each delta must have 'row_id', 'column', and 'value'")

            if col_name == "_delete":
                if 0 <= row_id < len(data_rows):
                    data_rows[row_id] = None
                continue

            if col_name not in editable_cols:
                raise StructuredError(ErrorCode.DESK_FAILED, f"Column '{col_name}' is not inline-editable.")

            if col_name not in headers:
                raise StructuredError(ErrorCode.DESK_FAILED, f"Column '{col_name}' not found in TSV headers.")

            col_idx = headers.index(col_name)
            if 0 <= row_id < len(data_rows):
                if data_rows[row_id] is not None:
                    data_rows[row_id][col_idx] = val
            else:
                raise StructuredError(ErrorCode.DESK_FAILED, f"Row index {row_id} is out of bounds (total rows: {len(data_rows)})")

        data_rows = [r for r in data_rows if r is not None]
        save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
        new_fingerprint = compute_content_fingerprint(data_rows)
        session_zid = extract_zid(tsv_path)

    results_dir = resolve_results_dir(resolved_paths, config)
    if (zid or session_zid) and results_dir:
        sess_logger = SessionLogger(zid or session_zid, results_dir, trace_id=(trace_id or f"{zid or session_zid}:edit-save"))
        sess_logger.info(f"Saved {len(deltas)} edit delta(s) to TSV {tsv_path.name}")

    return {
        "status": "success",
        "fingerprint": new_fingerprint,
        "session_zid": session_zid,
        "zid": zid
    }


def cmd_edit_save(args):
    logger.info("Edit-save subcommand invoked", extra={"zid": getattr(args, 'zid', None)})
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    deltas_path = Path(args.deltas).resolve()
    if not deltas_path.exists():
        print_structured_error("INVALID_STATE", f"Deltas file not found: {deltas_path}")
        sys.exit(1)

    try:
        with open(deltas_path, 'r', encoding='utf-8-sig') as f:
            deltas = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_STATE", f"Failed to parse deltas: {e}")
        sys.exit(1)

    tsv_param = getattr(args, 'tsv', None) or getattr(args, 'zid', None)
    trace_id = getattr(args, 'trace_id', None) or (f"{args.zid}:edit-save" if getattr(args, 'zid', None) else None)
    try:
        res = core_edit_save(tsv_param, deltas, config, resolved_paths, zid=getattr(args, 'zid', None), language=getattr(args, 'language', None), trace_id=trace_id)
        edit_save_payload: EditSaveSuccessPayload = {"status": "success"}
        emit_payload(edit_save_payload)
    except StructuredError as se:
        print_structured_error(se.error_code, se.message, se.details)
        sys.exit(1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to process and save working TSV: {e}")
        sys.exit(1)

def _c(code, text):
    return f"\033[{code}m{text}\033[0m"

def make_progress_bar(current: int, total: int, label: str = "files", status: str = "") -> str:
    term_width = shutil.get_terminal_size((100, 20)).columns
    bar_width = 30
    percent = (current / total) * 100 if total > 0 else 100
    filled = int(round(bar_width * percent / 100.0))
    bar = _c("32", "━" * filled) + _c("90", "━" * (bar_width - filled))
    text = f"{current}/{total} {label} ({percent:.1f}%)"
    
    max_status_width = max(20, term_width - 6)
    if len(status) > max_status_width:
        status = status[:max_status_width - 3] + "..."
        
    out = f"\r\033[K    {bar} {_c('36', text)}\n\033[K"
    if status:
        out += f"    {_c('90', status)}"
    out += "\033[A" # Move cursor back up to the progress bar line
    return out

def clear_progress_bar():
    sys.stdout.write("\r\033[K\n\r\033[K\033[A")
    sys.stdout.flush()

def cmd_merge(args):
    os.system("") # Enable ANSI on Windows
    print("Kardenwort Desk: Merging files...\n")
    logger.info("Merge subcommand invoked")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)
    merge_config = BatchMergeConfig.from_config(config, args=args)
    deduplicate = merge_config.deduplicate
    sort_frequency = merge_config.sort_frequency
    deduplicate_by_lemma = merge_config.deduplicate_by_lemma
    
    try:
        input_paths = [Path(f).resolve() for f in args.files]
        if not input_paths:
            print_structured_error("INVALID_STATE", "No inputs provided.")
            sys.exit(1)
            
        base_dest_dir = input_paths[0].parent
        
        if len(input_paths) == 2 and input_paths[0].parent == input_paths[1].parent:
            parent_dir = input_paths[0].parent
            start_zid = extract_zid(input_paths[0])
            end_zid = extract_zid(input_paths[1])
            
            if start_zid and end_zid:
                if start_zid > end_zid:
                    start_zid, end_zid = end_zid, start_zid
                    
                all_items = []
                for child in parent_dir.iterdir():
                    child_zid = extract_zid(child)
                    if child_zid and start_zid <= child_zid <= end_zid:
                        all_items.append(child)
                if all_items:
                    input_paths = all_items
                    
        expanded_files = []
        for path in input_paths:
            if path.is_dir():
                expanded_files.extend(list(path.rglob("*.tsv")))
            else:
                expanded_files.append(path)
                
        def map_to_tsv(path):
            if path.suffix.lower() == '.tsv':
                return path
            zid = extract_zid(path)
            if zid:
                parent = path.parent
                matches = []
                for pattern in (f"{zid}-*.tsv", f"{zid}.*.tsv", f"{zid}.tsv"):
                    for m in parent.glob(pattern):
                        m_res = m.resolve()
                        if m_res not in matches:
                            matches.append(m_res)
                if matches:
                    return matches[0]
            return None

        files = []
        original_files_by_tsv = {}
        for f in expanded_files:
            tsv_path = map_to_tsv(f)
            if tsv_path:
                if tsv_path not in files:
                    files.append(tsv_path)
                    original_files_by_tsv[tsv_path] = set()
                original_files_by_tsv[tsv_path].add(f)

        if not files:
            print_structured_error("INVALID_STATE", "No TSV files found in the selection to merge.")
            sys.exit(1)
            
        files.sort(key=extract_zid)

        default_lang = config.get(SEC_SETTINGS, 'default_language', fallback='en')
        def extract_lang_from_tsv(path):
            match = re.search(r'\.([a-z]{2})\.tsv$', path.name.lower())
            return match.group(1) if match else default_lang

        def extract_lang_from_txt(path):
            match = re.search(r'\.([a-z]{2})\.txt$', path.name.lower())
            return match.group(1) if match else ""

        def get_dest_tsv_path(target, lang, lang_files, dest_dir, timestamp_id):
            if target == "new":
                return dest_dir / f"{timestamp_id}-merged.{lang}.tsv"
            elif target == "first":
                return lang_files[0]
            else:
                path = Path(target).resolve()
                parent = path.parent
                name = path.name
                match = re.search(r'\.([a-z]{2})\.tsv$', name.lower())
                if match:
                    new_name = name[:match.start()] + f".{lang}.tsv"
                else:
                    new_name = path.stem + f".{lang}.tsv"
                return parent / new_name

        def get_dest_txt_path(dest_tsv_path, lang_key):
            name = dest_tsv_path.name
            lang_match = re.search(r'\.([a-z]{2})\.tsv$', name)
            if lang_match:
                prefix = name[:lang_match.start()]
            else:
                prefix = dest_tsv_path.stem
                
            if lang_key:
                return dest_tsv_path.parent / f"{prefix}.{lang_key}.txt"
            else:
                return dest_tsv_path.parent / f"{prefix}.txt"

        # Group files by language
        files_by_lang = {}
        for f in files:
            lang = extract_lang_from_tsv(f)
            if lang not in files_by_lang:
                files_by_lang[lang] = []
            files_by_lang[lang].append(f)

        import time
        all_written_tsvs = []
        all_written_txts = []
        
        total_files = len(files)
        processed_files = 0
        sys.stdout.write(make_progress_bar(processed_files, total_files, status="Preparing files..."))
        sys.stdout.flush()

        for idx, (lang, lang_files) in enumerate(sorted(files_by_lang.items())):
            timestamp_id = generate_unique_zid()
            # Load headers and files, and compute union headers
            loaded_files = []
            union_headers = []
            union_headers_set = set()
            for f in lang_files:
                if not f.exists():
                    clear_progress_bar()
                    print_structured_error("INVALID_STATE", f"File not found: {f}")
                    sys.exit(1)
                try:
                    comments, headers, rows = load_tsv_rows(f)
                    loaded_files.append((f, comments, headers, rows))
                    for h in headers:
                        if h not in union_headers_set:
                            union_headers.append(h)
                            union_headers_set.add(h)
                            
                    processed_files += 1
                    sys.stdout.write(make_progress_bar(processed_files, total_files, status=f"Merged {f.name}"))
                    sys.stdout.flush()
                except Exception as e:
                    clear_progress_bar()
                    print_structured_error("DESK_FAILED", f"Failed to read file {f.name}: {e}")
                    sys.exit(1)

            first_headers = union_headers

            all_comments = []
            all_data_rows = []
            texts_by_lang = {}
            
            try:
                mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
                role_fields = get_role_fields(mapping, first_headers)
                sentence_index_col = role_fields.get('sentence_index', 'SentenceSourceIndex')
                col_index = first_headers.index(sentence_index_col) if sentence_index_col in first_headers else -1
            except Exception as e:
                logger.warning(f"Failed to load sentence_index mapping: {e}")
                col_index = first_headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in first_headers else -1
                role_fields = {}
            
            current_line_offset = 0
            for f, comments, headers, rows in loaded_files:
                if not all_comments:
                    all_comments = comments
                    
                zid = extract_zid(f)
                parent_dir = f.parent
                txt_files = list(parent_dir.glob(f"{zid}-*.txt"))
                if not txt_files:
                    base_txt = f.with_suffix('.txt')
                    if base_txt.exists():
                        txt_files = [base_txt]
                        
                non_empty_lines = 0
                if txt_files:
                    try:
                        txt_content = txt_files[0].read_text(encoding='utf-8')
                        non_empty_lines = sum(1 for line in txt_content.splitlines() if line.strip())
                    except Exception as e:
                        logger.warning(f"Failed to read/count lines in sibling text {txt_files[0]}: {e}")
                        
                for t in txt_files:
                    lang_key = extract_lang_from_txt(t) or lang
                    try:
                        content = t.read_text(encoding='utf-8')
                        if lang_key not in texts_by_lang:
                            texts_by_lang[lang_key] = []
                        texts_by_lang[lang_key].append(content)
                    except Exception as e:
                        logger.warning(f"Failed to read sibling text {t}: {e}")
                
                # Align rows to the union header schema
                aligned_rows = []
                for row in rows:
                    aligned_row = []
                    for h in union_headers:
                        if h in headers:
                            h_idx = headers.index(h)
                            aligned_row.append(row[h_idx] if h_idx < len(row) else "")
                        else:
                            aligned_row.append("")
                    aligned_rows.append(aligned_row)

                # Offset the SentenceSourceIndex values for this file's rows
                if col_index != -1:
                    for row in aligned_rows:
                        if len(row) > col_index:
                            val = row[col_index].strip()
                            try:
                                orig_idx = int(val) if val else 1
                                if non_empty_lines == 1:
                                    row[col_index] = str(current_line_offset + 1)
                                else:
                                    row[col_index] = str(orig_idx + current_line_offset)
                            except ValueError:
                                pass
                                
                all_data_rows.extend(aligned_rows)
                current_line_offset += non_empty_lines
                
            dest_dir = base_dest_dir
            dest_tsv_path = get_dest_tsv_path(args.target, lang, lang_files, dest_dir, timestamp_id)

            # Deduplicate rows by unique (inflected, lemma) pairs if requested
            if deduplicate:
                lemma_col = role_fields.get('lemma', 'WordSource') if isinstance(role_fields, dict) else 'WordSource'
                inflected_col = role_fields.get('inflected', 'WordSourceInflectedForm') if isinstance(role_fields, dict) else 'WordSourceInflectedForm'
                col_lemma = first_headers.index(lemma_col) if lemma_col in first_headers else -1
                col_inflected = first_headers.index(inflected_col) if inflected_col in first_headers else -1
                    
                if col_lemma != -1 and col_inflected != -1:
                    order_cfg = merge_config.combine_order
                    apo_cfg = tuple(c.strip() for c in merge_config.apostrophe_chars.split(',') if c.strip())
                    
                    prefer_lowercase_cfg = merge_config.prefer_lowercase

                    col_inflected2 = first_headers.index('WordSourceInflectedForm2') if 'WordSourceInflectedForm2' in first_headers else -1
                    col_quotation = first_headers.index('Quotation') if 'Quotation' in first_headers else -1
                    seen_pairs = []
                    grouped_rows = {}
                    for row in all_data_rows:
                        lemma_val = row[col_lemma].strip().lower() if len(row) > col_lemma else ""
                        inf_val = resolve_row_inflected_form(row, col_inflected, col_inflected2, col_quotation, col_lemma)
                        if inf_val:
                            inf_parts = [p.strip() for p in inf_val.lower().split(',') if p.strip()]
                            inflected_key = tuple(sorted(inf_parts))
                        else:
                            inflected_key = ()
                        
                        pair = lemma_val if deduplicate_by_lemma else (inflected_key, lemma_val)
                        if pair not in grouped_rows:
                            grouped_rows[pair] = []
                            seen_pairs.append(pair)
                        grouped_rows[pair].append(row)
                    
                    unique_data_rows = []
                    for pair in seen_pairs:
                        rows_list = grouped_rows[pair]
                        # Merge rows by overlaying non-empty fields from oldest to newest
                        merged_row = [""] * len(first_headers)
                        merged_inflected = []
                        for r in rows_list:
                            for i, cell in enumerate(r):
                                if i < len(merged_row) and cell.strip():
                                    merged_row[i] = cell
                            if col_inflected != -1 and len(r) > col_inflected:
                                current_inf = r[col_inflected].strip()
                                if current_inf:
                                    for part in [p.strip() for p in current_inf.split(',') if p.strip()]:
                                        if part not in merged_inflected:
                                            merged_inflected.append(part)
                        if col_inflected != -1 and merged_inflected:
                            merged_row[col_inflected] = ", ".join(sort_inflected_forms(merged_inflected, apo_cfg, order_cfg, prefer_lowercase_cfg))
                        unique_data_rows.append(merged_row)
                    all_data_rows = unique_data_rows

            # Sort by frequency if requested
            if sort_frequency:
                all_data_rows = sort_rows_by_frequency(
                    all_data_rows,
                    first_headers,
                    lang,
                    config,
                    resolved_paths,
                    role_fields=role_fields,
                )

            written_txt_paths = set()
            try:
                with file_lock(dest_tsv_path):
                    save_tsv_rows_safely(dest_tsv_path, all_comments, first_headers, all_data_rows)
                all_written_tsvs.append(dest_tsv_path)
                
                for lang_key, sibling_texts in texts_by_lang.items():
                    dest_txt_path = get_dest_txt_path(dest_tsv_path, lang_key)
                    merged_text = "\n\n".join(sibling_texts)
                    
                    with file_lock(dest_txt_path):
                        temp_txt = dest_txt_path.with_suffix('.txt.tmp')
                        bak_txt = dest_txt_path.with_suffix('.txt.bak')
                        try:
                            temp_txt.write_text(merged_text, encoding='utf-8')
                            if dest_txt_path.exists():
                                if bak_txt.exists():
                                    os.remove(bak_txt)
                                os.rename(dest_txt_path, bak_txt)
                            try:
                                os.rename(temp_txt, dest_txt_path)
                            except Exception as e:
                                if bak_txt.exists():
                                    os.rename(bak_txt, dest_txt_path)
                                raise e
                            if bak_txt.exists():
                                try:
                                    os.remove(bak_txt)
                                except OSError:
                                    pass
                            written_txt_paths.add(dest_txt_path)
                            all_written_txts.append(dest_txt_path)
                        except Exception as e:
                            if temp_txt.exists():
                                try:
                                    os.remove(temp_txt)
                                except OSError:
                                    pass
                            raise e
                            
                delete_sources = getattr(args, 'delete_sources', False)
                if not delete_sources and config:
                    delete_sources = config.getboolean(SEC_MERGE, 'delete_sources', fallback=config.getboolean(SEC_SETTINGS, 'merge_delete_sources', fallback=False))
                        
                if delete_sources:
                    for f in lang_files:
                        if f == dest_tsv_path:
                            continue
                        try:
                            # 1. Delete original explicitly selected files mapped to this TSV
                            if f in original_files_by_tsv:
                                for orig_f in original_files_by_tsv[f]:
                                    if orig_f != dest_tsv_path and orig_f not in written_txt_paths:
                                        try:
                                            os.remove(orig_f)
                                        except OSError:
                                            pass
                            # 2. Delete the TSV itself
                            try:
                                os.remove(f)
                            except OSError:
                                pass
                            # 3. Delete any remaining .txt files for this ZID
                            zid = extract_zid(f)
                            if zid:
                                for t_file in f.parent.glob(f"{zid}*.txt"):
                                    if t_file not in written_txt_paths:
                                        try:
                                            os.remove(t_file)
                                        except OSError:
                                            pass
                        except Exception as e:
                            logger.warning(f"Failed to delete merged source {f.name}: {e}")
            except Exception as e:
                clear_progress_bar()
                print_structured_error("DESK_FAILED", f"Merge execution failed for '{lang}': {e}")
                sys.exit(1)

        clear_progress_bar()
        
        success_msg = _c("1;32", "SUCCESS: Merged Files") + "\n"
        
        if all_written_tsvs:
            success_msg += _c("36", "\nTSVs:\n")
            for p in sorted(all_written_tsvs):
                success_msg += f"  - {_c('90', str(p.parent) + os.sep)}{_c('1', p.name)}\n"
                
        if all_written_txts:
            success_msg += _c("36", "\nTXTs:\n")
            for p in sorted(all_written_txts):
                success_msg += f"  - {_c('90', str(p.parent) + os.sep)}{_c('1', p.name)}\n"

        emit_payload(success_msg, raw=True)
        if getattr(args, 'pause', False):
            input("\nPress Enter to exit...")
    except Exception as e:
        clear_progress_bar()
        print_structured_error("DESK_FAILED", f"Merge execution failed: {e}")
        if getattr(args, 'pause', False):
            input("\nPress Enter to exit...")
        sys.exit(1)

def get_ahk_executable():
    import shutil
    ahk_exes = ["AutoHotkey.exe", "AutoHotkey64.exe", "AutoHotkey32.exe"]
    
    # 1. Try to find any in PATH
    for name in ahk_exes:
        path_match = shutil.which(name)
        if path_match:
            return path_match
            
    # 2. Check common installation directories
    possible_dirs = [
        Path(r"C:\Program Files\AutoHotkey\v2"),
        Path(r"C:\Program Files\AutoHotkey"),
        Path(r"C:\Program Files (x86)\AutoHotkey"),
    ]
    # Scan C:\AHK and its subfolders (like C:\AHK\AutoHotkey_2.0.18)
    try:
        c_ahk = Path(r"C:\AHK")
        if c_ahk.exists():
            possible_dirs.append(c_ahk)
            for sub in c_ahk.iterdir():
                if sub.is_dir() and "autohotkey" in sub.name.lower():
                    possible_dirs.append(sub)
    except Exception:
        pass
        
    for p_dir in possible_dirs:
        for name in ahk_exes:
            if (p_dir / name).exists():
                return str(p_dir / name)
                
def persist_default_language(language: str, base_dir=None) -> bool:
    """
    Persists the default language to config.ini files in both kardenwort-desk and kardenwort-window.
    Preserves comments and formatting via regex substitution.
    """
    if not language:
        return False
    
    if not base_dir:
        base_dir = Path(__file__).resolve().parent
    base_dir = Path(base_dir)

    success = False
    
    # 1. Update desk config.ini
    desk_config = base_dir / "config.ini"
    if desk_config.exists():
        try:
            content = desk_config.read_text(encoding="utf-8")
            if re.search(r'(?i)^\s*default_language\s*=', content, flags=re.MULTILINE):
                new_content = re.sub(
                    r'(?i)^(\s*default_language\s*=\s*).*$',
                    r'\g<1>' + language,
                    content,
                    flags=re.MULTILINE
                )
                desk_config.write_text(new_content, encoding="utf-8")
                success = True
        except Exception as e:
            logger.warning(f"Failed to update desk config.ini with default_language={language}: {e}")

    # 2. Update autohotkey config.ini
    ahk_repo = next(base_dir.parent.glob("*-autohotkey"), None) if base_dir.parent else None
    if ahk_repo:
        ahk_config = ahk_repo / "kardenwort-window" / "config.ini"
        if ahk_config.exists():
            try:
                content = ahk_config.read_text(encoding="utf-8")
                if re.search(r'(?i)^\s*DefaultLanguage\s*=', content, flags=re.MULTILINE):
                    new_content = re.sub(
                        r'(?i)^(\s*DefaultLanguage\s*=\s*).*$',
                        r'\g<1>' + language,
                        content,
                        flags=re.MULTILINE
                    )
                    ahk_config.write_text(new_content, encoding="utf-8")
                    success = True
            except Exception as e:
                logger.warning(f"Failed to update AHK config.ini with DefaultLanguage={language}: {e}")

    return success


def spawn_ahk(args_list, base_dir=None):
    if not base_dir:
        base_dir = Path(__file__).resolve().parent
    base_dir = Path(base_dir)
    ahk_repo = next(base_dir.parent.glob("*-autohotkey"), None) if base_dir.parent else None
    if not ahk_repo:
        logger.error("Could not find autohotkey repository in parent directory.")
        return False
    ahk_script = ahk_repo / "kardenwort-window" / "kardenwort-window.ahk"
    if not ahk_script.exists():
        logger.error(f"AHK script not found at {ahk_script}")
        return False
    
    found_exe = get_ahk_executable()
    
    def chunk_args(args_list, base_cmd, max_len=8000, chunk_size=4):
        base_len = sum(len(c) + 1 for c in base_cmd)
        current_chunk = []
        current_len = base_len
        for i in range(0, len(args_list), chunk_size):
            block = args_list[i:i+chunk_size]
            block_len = sum(len(str(a)) + 1 for a in block)
            if current_len + block_len > max_len and current_chunk:
                yield current_chunk
                current_chunk = []
                current_len = base_len
            current_chunk.extend(block)
            current_len += block_len
        if current_chunk:
            yield current_chunk

    success = True
    if found_exe:
        base_cmd = [found_exe, str(ahk_script)]
        for chunk in chunk_args(args_list, base_cmd):
            cmd = base_cmd + chunk
            logger.info(f"Spawning AHK via executable: {' '.join(cmd)}")
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, close_fds=True)
            except Exception as e:
                logger.error(f"Failed to spawn AHK window process: {e}")
                success = False
        return success
    else:
        logger.warning(f"No AutoHotkey executable found, falling back to shell execution for {ahk_script.name}")
        base_cmd = ["cmd.exe", "/c", "start", '""', str(ahk_script)] if sys.version_info < (3, 10) else [str(ahk_script)]
        for chunk in chunk_args(args_list, base_cmd):
            try:
                if sys.version_info >= (3, 10):
                    args_str = ' '.join(f'"{a}"' for a in chunk)
                    os.startfile(str(ahk_script), operation='open', arguments=args_str)
                else:
                    cmd = base_cmd + chunk
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, close_fds=True)
            except Exception as e2:
                logger.error(f"Failed to spawn AHK via fallback: {e2}")
                success = False
        return success

def cmd_restore(args):
    logger.info("Restore subcommand invoked")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)

    target_project = getattr(args, "project", None) or getattr(args, "project_id", None)
    if target_project is not None:
        if not getattr(args, "no_gui", False):
            spawn_ahk(["--project", str(target_project)], resolved_paths['base_dir'])
            return
        synthesized = synthesize_project_materials(
            project_id=int(target_project),
            config=config,
            resolved_paths=resolved_paths,
            language=getattr(args, "language", None),
            zid=getattr(args, "zid", None),
        )
        if getattr(args, 'json_output', False) or getattr(args, 'json', False):
            out_str = json.dumps(synthesized, indent=2, ensure_ascii=False)
            if sys.__stdout__ is not None:
                sys.__stdout__.write(out_str + "\n")
                sys.__stdout__.flush()
            else:
                print(out_str)
        else:
            payload = {
                "source_text": synthesized.get("source_text", ""),
                "headers": synthesized.get("headers", []),
                "data_rows": synthesized.get("data_rows", []),
                "warnings": [],
                "project_id": int(target_project),
                "project_title": synthesized.get("project_title", ""),
                "total_sessions": synthesized.get("total_sessions", 0),
                "total_words": synthesized.get("total_words", 0),
            }
            from b64util import encode
            response_str = json.dumps(payload)
            emit_payload(encode(response_str), raw=True)
        return

    file_list = args.file if isinstance(args.file, list) else ([args.file] if args.file else [])
    target_zid = getattr(args, "zid", None)

    if not file_list and not target_zid:
        print_structured_error("INVALID_STATE", "Either --file or --zid or --project must be provided for restore.")
        sys.exit(1)

    router = StorageRouter(config=config, resolved_paths=resolved_paths)

    if not args.no_gui:
        ahk_args = []
        if target_zid:
            ahk_args.extend(["--restore", str(target_zid)])
        if file_list:
            zid_groups = {}
            non_zid_files = []
            for file_val in file_list:
                input_path = Path(file_val).resolve()
                if input_path.exists():
                    match = re.match(r"^(\d{14})", input_path.name)
                    if match:
                        zid = match.group(1)
                        if zid not in zid_groups:
                            zid_groups[zid] = []
                        zid_groups[zid].append(input_path)
                    else:
                        non_zid_files.append(input_path)
                elif re.match(r"^\d{14}$", str(file_val).strip()):
                    ahk_args.extend(["--restore", str(file_val).strip()])
                else:
                    print_structured_error("INVALID_STATE", f"File to restore not found: {input_path}")

            def priority(p):
                ext = p.suffix.lower()
                if ext == '.tsv': return 0
                if ext == '.txt': return 1
                return 2

            for zid, files in zid_groups.items():
                best_file = sorted(files, key=priority)[0]
                ahk_args.extend(["--restore", str(best_file)])

            for file_path in non_zid_files:
                ahk_args.extend(["--restore", str(file_path)])

        if ahk_args:
            spawn_ahk(ahk_args, resolved_paths['base_dir'])
        return

    # In --no-gui mode
    zid_to_restore = target_zid
    input_path = None
    if file_list:
        first_file = file_list[0]
        input_path = Path(first_file).resolve()
        if input_path.exists():
            zid_to_restore = extract_zid(input_path)
        elif re.match(r"^\d{14}$", str(first_file).strip()):
            zid_to_restore = str(first_file).strip()

    if not zid_to_restore and input_path and not input_path.exists():
        print_structured_error("INVALID_STATE", f"File to restore not found: {input_path}")
        sys.exit(1)

    results_dir = None
    if input_path and input_path.exists():
        results_dir = input_path.parent
    else:
        try:
            results_dir = resolve_results_dir(resolved_paths, config)
        except Exception:
            results_dir = None

    try:
        restored = router.restore_session(zid_to_restore, results_dir=results_dir)
        payload = {
            "source_text": restored.get("source_text", ""),
            "headers": restored.get("headers", []),
            "data_rows": restored.get("data_rows", []),
            "warnings": [],
            "tsv_path": str(restored.get("tsv_path", "")),
            "txt_path": str(restored.get("txt_path", "")),
        }
    except StructuredError as se:
        print_structured_error(se.error_code.name if hasattr(se.error_code, "name") else str(se.error_code), se.message)
        sys.exit(1)
    except Exception as e:
        print_structured_error("INVALID_STATE", f"Session restore failed: {e}")
        sys.exit(1)

    from b64util import encode
    response_str = json.dumps(payload)
    emit_payload(encode(response_str), raw=True)

def cmd_desk(args):
    logger.info("Desk subcommand invoked")
    config, resolved_paths, goldendict, _wordfill = load_config(args.config)

    target_project = getattr(args, "project", None) or getattr(args, "project_id", None)
    if target_project is not None:
        if not getattr(args, "no_gui", False):
            spawn_ahk(["--project", str(target_project)], resolved_paths['base_dir'])
            return
        synthesized = synthesize_project_materials(
            project_id=int(target_project),
            config=config,
            resolved_paths=resolved_paths,
            language=getattr(args, "language", None),
            zid=getattr(args, "zid", None),
        )
        if getattr(args, 'json_output', False) or getattr(args, 'json', False):
            out_str = json.dumps(synthesized, indent=2, ensure_ascii=False)
            if sys.__stdout__ is not None:
                sys.__stdout__.write(out_str + "\n")
                sys.__stdout__.flush()
            else:
                print(out_str)
        else:
            from b64util import encode
            response_str = json.dumps(synthesized)
            emit_payload(encode(response_str), raw=True)
        return

    file_list = args.file if isinstance(args.file, list) else ([args.file] if args.file else [])
    if not file_list:
        print_structured_error("INVALID_STATE", "Either --file or --project must be provided for desk.")
        sys.exit(1)

    if not args.no_gui:
        ahk_args = []
        zid_groups = {}
        non_zid_files = []
        for file_val in file_list:
            file_path = Path(file_val).resolve()
            if not file_path.exists():
                print_structured_error("INVALID_STATE", f"File to analyze not found: {file_path}")
                continue
                
            match = re.match(r"^(\d{14})", file_path.name)
            if match:
                zid = match.group(1)
                if zid not in zid_groups:
                    zid_groups[zid] = []
                zid_groups[zid].append(file_path)
            else:
                non_zid_files.append(file_path)
                
        def priority(p):
            ext = p.suffix.lower()
            if ext == '.tsv': return 0
            if ext == '.txt': return 1
            return 2

        for zid, files in zid_groups.items():
            best_file = sorted(files, key=priority)[0]
            logger.info(f"File '{best_file.name}' is recognized as an existing session. Delegating to restore...")
            ahk_args.extend(["--restore", str(best_file)])
            
        for file_path in non_zid_files:
            is_tsv = file_path.suffix == '.tsv'
            if is_tsv:
                logger.info(f"File '{file_path.name}' is recognized as an existing session. Delegating to restore...")
                ahk_args.extend(["--restore", str(file_path)])
            else:
                ahk_args.extend(["--desk", str(file_path), "--text-mode", args.text_mode])
                
        if ahk_args:
            spawn_ahk(ahk_args, resolved_paths['base_dir'])
        return
        
    file_path = Path(file_list[0]).resolve()
    if not file_path.exists():
        print_structured_error("INVALID_STATE", f"File to analyze not found: {file_path}")
        sys.exit(1)
        
    # Auto-detection: if it's a .tsv or starts with a 14-digit ZID, it's a restore session
    is_tsv = file_path.suffix == '.tsv'
    has_zid = bool(re.match(r"^\d{14}-", file_path.name))
    if is_tsv or has_zid:
        logger.info(f"File '{file_path.name}' is recognized as an existing session. Delegating to restore...")
        args.file = [str(file_path)]
        cmd_restore(args)
        return
        
    try:
        text = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to read file: {e}")
        sys.exit(1)
        
    text_mode = getattr(args, 'text_mode', 'single')
    if text_mode == 'single' and '\n' in text.strip():
        text_mode = 'multi'
        
    if text_mode == 'multi':
        remove_empty = config.getboolean(SEC_SETTINGS, 'multi_mode_remove_empty_lines', fallback=True)
        clean_spaces = config.getboolean(SEC_SETTINGS, 'multi_mode_clean_spaces', fallback=True)
        if remove_empty or clean_spaces:
            new_lines = []
            for line in text.splitlines():
                if clean_spaces:
                    line = re.sub(r'[ \t]+', ' ', line).strip()
                if remove_empty and not line.strip():
                    continue
                new_lines.append(line)
            text = "\n".join(new_lines)
        
    lang = args.language
    if not lang:
        lang_match = re.search(r'\.([a-z]{2})\.(txt|srt)$', file_path.name)
        if lang_match:
            lang = lang_match.group(1)
        else:
            lang = config.get(SEC_SETTINGS, 'default_language', fallback='en')
            
    timestamp_id = generate_unique_zid()
    
    bypass_lang = getattr(args, 'bypass_lang_check', False)
    if not bypass_lang:
        lang_res = verify_language(text, lang, config, bypass=False)
        if not lang_res.is_match:
            if lang_res.action in ("block", "prompt"):
                print_structured_error(
                    ErrorCode.LANGUAGE_MISMATCH,
                    lang_res.message,
                    details={
                        "detected_language": lang_res.detected_lang,
                        "expected_language": lang_res.expected_lang,
                        "confidence": lang_res.confidence,
                        "action": lang_res.action,
                    }
                )
                sys.exit(1)
            elif lang_res.action == "warn":
                logger.warning(lang_res.message)

    try:
        theme_val = args.theme if hasattr(args, 'theme') else "dark"
        split_gap = config.getint(SEC_SETTINGS, 'split_gap_limit', fallback=60)
        html = run_render_flow(text, lang, timestamp_id, args.text_mode, config, resolved_paths, theme=theme_val, split_gap_limit=split_gap)
        from b64util import encode
        emit_payload(encode(html), raw=True)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Desk flow failed: {str(e)}")
        sys.exit(1)

def cmd_db_status(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    status = db.get_status(zid=getattr(args, 'zid', None))
    if sys.__stdout__ is not None:
        sys.__stdout__.write(json.dumps(status, indent=2) + "\n")
        sys.__stdout__.flush()
    else:
        print(json.dumps(status))
    sys.exit(0 if status.get("ok", False) else 1)

def cmd_db_check(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    check_result = db.check_integrity(zid=getattr(args, 'zid', None))
    if sys.__stdout__ is not None:
        sys.__stdout__.write(json.dumps(check_result, indent=2) + "\n")
        sys.__stdout__.flush()
    else:
        print(json.dumps(check_result))
    sys.exit(0 if check_result.get("ok", False) else 1)

def cmd_db_query(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB, QuerySecurityError, QueryExecutionError
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    query_str = getattr(args, 'db_query', None) or getattr(args, 'query', '')
    if not query_str:
        print_structured_error("INVALID_STATE", "No SQL query provided for --db-query")
        sys.exit(1)
    try:
        rows = db.query_readonly(query_str, zid=getattr(args, 'zid', None))
        if sys.__stdout__ is not None:
            sys.__stdout__.write(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
            sys.__stdout__.flush()
        else:
            print(json.dumps(rows, ensure_ascii=False))
        sys.exit(0)
    except QuerySecurityError as e:
        print_structured_error("MUTATION_NOT_ALLOWED", e.message)
        sys.exit(1)
    except QueryExecutionError as e:
        print_structured_error("QUERY_FAILED", e.message)
        sys.exit(1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Database query failed: {str(e)}")
        sys.exit(1)

def cmd_db_reset(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB, QuerySecurityError
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    force = getattr(args, 'force', False)
    try:
        result = db.reset(force=force, zid=getattr(args, 'zid', None))
        if sys.__stdout__ is not None:
            sys.__stdout__.write(json.dumps(result, indent=2) + "\n")
            sys.__stdout__.flush()
        else:
            print(json.dumps(result))
        sys.exit(0)
    except QuerySecurityError as e:
        print_structured_error(e.error_code, e.message)
        sys.exit(1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Database reset failed: {str(e)}")
        sys.exit(1)

def cmd_list_sessions(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    limit = getattr(args, 'limit', None)
    sessions = db.list_sessions_with_counts(limit=limit, zid=getattr(args, 'zid', None))

    if getattr(args, 'json_output', False) or getattr(args, 'json', False):
        out_str = json.dumps(sessions, indent=2, ensure_ascii=False)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(out_str + "\n")
            sys.__stdout__.flush()
        else:
            print(out_str)
    else:
        if not sessions:
            print("No active sessions found in database.")
        else:
            header = f"{'ZID':<16} {'LANG':<8} {'TOKENS':<8} {'CREATED AT':<25} {'SLUG'}"
            print(header)
            print("-" * len(header))
            for s in sessions:
                lang = f"{s.get('source_language', '')}->{s.get('target_language', '')}"
                print(f"{s.get('zid', ''):<16} {lang:<8} {s.get('token_count', 0):<8} {str(s.get('created_at', '')):<25} {s.get('slug', '')}")
    sys.exit(0)


def cmd_delete_session(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    session_zid = getattr(args, 'zid', None)
    if not session_zid or not str(session_zid).strip():
        print_structured_error("INVALID_STATE", "Missing required --zid for session deletion")
        sys.exit(1)

    session_zid = str(session_zid).strip()
    storage_adapter = get_storage_adapter(config, resolved_paths)
    deleted = storage_adapter.delete_session(session_zid, zid=session_zid)

    # Clean up disk TSV / txt if present in results/
    results_dir = resolve_results_dir(resolved_paths, config) if resolved_paths and config else None
    if results_dir and results_dir.exists():
        for p in results_dir.glob(f"{session_zid}*"):
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    import shutil
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    res = {
        "ok": deleted,
        "deleted_zid": session_zid,
        "message": f"Session '{session_zid}' deleted successfully." if deleted else f"Session '{session_zid}' not found."
    }
    if getattr(args, 'json_output', False) or getattr(args, 'json', False):
        out_str = json.dumps(res, indent=2)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(out_str + "\n")
            sys.__stdout__.flush()
        else:
            print(out_str)
    else:
        print(res["message"])
    sys.exit(0 if deleted else 1)


def cmd_cleanup_db(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    older_than = getattr(args, 'older_than', None)
    if older_than is None:
        print_structured_error("INVALID_STATE", "Missing required --older-than (in days) for cleanup-db")
        sys.exit(1)

    try:
        days = float(older_than)
    except (ValueError, TypeError):
        print_structured_error("INVALID_STATE", f"Invalid --older-than value: '{older_than}'")
        sys.exit(1)

    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    count = db.cleanup_db(older_than_days=days, zid=getattr(args, 'zid', None))

    res = {
        "ok": True,
        "deleted_count": count,
        "older_than_days": days,
        "message": f"Purged {count} session(s) older than {days} day(s)."
    }
    if getattr(args, 'json_output', False) or getattr(args, 'json', False):
        out_str = json.dumps(res, indent=2)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(out_str + "\n")
            sys.__stdout__.flush()
        else:
            print(out_str)
    else:
        print(res["message"])
    sys.exit(0)


def cmd_vacuum_db(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    ok = db.vacuum(zid=getattr(args, 'zid', None))

    res = {
        "ok": ok,
        "message": "Database vacuumed and defragmented successfully." if ok else "Database vacuum failed."
    }
    if getattr(args, 'json_output', False) or getattr(args, 'json', False):
        out_str = json.dumps(res, indent=2)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(out_str + "\n")
            sys.__stdout__.flush()
        else:
            print(out_str)
    else:
        print(res["message"])
    sys.exit(0 if ok else 1)


def parse_tsv_to_bundle(
    tsv_content_or_path: Union[str, Path, List[str]],
    filename: Optional[str] = None,
    session_zid: Optional[str] = None,
    language: Optional[str] = None,
    slug: Optional[str] = None,
    source_raw_text: Optional[str] = None,
    config: Optional[Any] = None,
    resolved_paths: Optional[Dict[str, Any]] = None,
    zid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Parses raw TSV content (or lines, or a Path) into an atomic session bundle dictionary
    containing 'session', 'sentences', and 'words' structures suitable for SQLite insertion
    via save_session_bundle.
    """
    comments: List[str] = []
    headers: List[str] = []
    data_rows: List[List[str]] = []

    if isinstance(tsv_content_or_path, Path) or (isinstance(tsv_content_or_path, str) and "\n" not in tsv_content_or_path and Path(tsv_content_or_path).is_file()):
        p = Path(tsv_content_or_path)
        if not filename:
            filename = p.name
        comments, headers, data_rows = load_tsv_rows(p)
    elif isinstance(tsv_content_or_path, list):
        import csv
        lines_to_parse = []
        for line in tsv_content_or_path:
            line_str = line.rstrip("\r\n")
            if not headers and not lines_to_parse and line_str.startswith("#"):
                comments.append(line_str)
            elif line_str:
                lines_to_parse.append(line_str)
        reader = csv.reader(lines_to_parse, delimiter="\t")
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            else:
                data_rows.append(row)
    else:
        import csv
        raw_text = str(tsv_content_or_path)
        lines_to_parse = []
        for line in raw_text.splitlines():
            line_str = line.rstrip("\r\n")
            if not headers and not lines_to_parse and line_str.startswith("#"):
                comments.append(line_str)
            elif line_str:
                lines_to_parse.append(line_str)
        reader = csv.reader(lines_to_parse, delimiter="\t")
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            else:
                data_rows.append(row)

    if not headers or not data_rows:
        raise StructuredError(
            ErrorCode.INVALID_PAYLOAD,
            "Invalid or empty TSV content provided for ingestion.",
        )

    # Determine session ZID
    file_zid = session_zid
    if not file_zid and filename:
        file_zid = extract_zid(filename)
    if not file_zid:
        file_zid = generate_unique_zid()

    # Determine language and slug
    detected_lang = language
    detected_slug = slug or ""
    if filename:
        name_no_ext = Path(filename).stem
        parts = name_no_ext.split(".")
        if not detected_lang and len(parts) > 1 and len(parts[-1]) in (2, 3, 5):
            detected_lang = parts[-1]
        slug_part = parts[0]
        if not detected_slug:
            extracted_zid_in_fn = extract_zid(slug_part)
            if extracted_zid_in_fn and slug_part.startswith(f"{extracted_zid_in_fn}-"):
                detected_slug = slug_part[len(extracted_zid_in_fn) + 1:]
            elif not extracted_zid_in_fn:
                detected_slug = slug_part

    if not detected_lang:
        detected_lang = (
            config.get(SEC_SETTINGS, "default_language", fallback="de")
            if config and hasattr(config, "get")
            else "de"
        )

    headers_lower = {h.lower(): idx for idx, h in enumerate(headers)}

    def get_col_val(r: List[str], col_name: str, default: str = "") -> str:
        idx = headers_lower.get(col_name.lower())
        return r[idx] if idx is not None and idx < len(r) else default

    known_cols = {
        "quotation", "wordsource", "wordsourceinflectedform",
        "worddestination", "worddestinationinflectedform",
        "wordsourcemorphologyai", "wordsourceipa", "deskselected", "leitnerbox",
        "leitnerdue", "deck", "classificationoxford", "classificationgoethe",
        "sentencesourceindex", "sentencesource", "sentencedestination",
        "sentencedestination2", "sentencesourceipa", "sentencesourceaudio",
        "sentencesourcecontextleft", "sentencesourcecontextright",
        "sentencedestinationcontextleft", "sentencedestinationcontextright",
        "sentencedestination2contextleft", "sentencedestination2contextright",
        "sentencesourcewordlist", "sentencesourcecloze",
        "wordsource2", "wordsourceinflectedform2",
        "textsource", "textdestination", "textsourceurl", "source", "sourceurl",
        "separatoraudio", "note", "note id", "togglealwaysemptyfield",
    }

    raw_sentence_index_map: Dict[Any, int] = {}
    seq_counter = 1
    sent_map: Dict[int, Dict[str, Any]] = {}
    word_list: List[Dict[str, Any]] = []

    for row_idx, row in enumerate(data_rows):
        raw_s_val = get_col_val(row, "sentencesourceindex")
        if raw_s_val:
            if raw_s_val not in raw_sentence_index_map:
                raw_sentence_index_map[raw_s_val] = seq_counter
                seq_counter += 1
            norm_s_idx = raw_sentence_index_map[raw_s_val]
        else:
            sent_src_text = get_col_val(row, "sentencesource")
            if sent_src_text:
                if sent_src_text not in raw_sentence_index_map:
                    raw_sentence_index_map[sent_src_text] = seq_counter
                    seq_counter += 1
                norm_s_idx = raw_sentence_index_map[sent_src_text]
            else:
                norm_s_idx = 1

        if norm_s_idx not in sent_map:
            sent_map[norm_s_idx] = {
                "session_zid": file_zid,
                "sentence_index": norm_s_idx,
                "sentence_source": get_col_val(row, "sentencesource"),
                "sentence_destination": get_col_val(row, "sentencedestination") or None,
                "sentence_destination2": get_col_val(row, "sentencedestination2") or None,
                "sentence_source_ipa": get_col_val(row, "sentencesourceipa") or None,
                "sentence_source_audio": get_col_val(row, "sentencesourceaudio") or None,
            }

        quotation = (
            get_col_val(row, "quotation")
            or get_col_val(row, "wordsourceinflectedform")
            or get_col_val(row, "wordsource")
            or ""
        )
        lemma = get_col_val(row, "wordsource") or quotation
        inflected = get_col_val(row, "wordsourceinflectedform")
        morph = get_col_val(row, "wordsourcemorphologyai")
        ipa = get_col_val(row, "wordsourceipa")
        w_dest = get_col_val(row, "worddestination")
        w_dest_inf = get_col_val(row, "worddestinationinflectedform")
        sel_raw = get_col_val(row, "deskselected")
        selected = 1 if str(sel_raw).strip() in ("1", "true", "True") else 0
        box_raw = get_col_val(row, "leitnerbox")
        box = int(box_raw) if box_raw.isdigit() else 1
        due = get_col_val(row, "leitnerdue") or None
        deck = get_col_val(row, "deck") or None
        oxford = get_col_val(row, "classificationoxford") or None
        goethe = get_col_val(row, "classificationgoethe") or None

        extra: Dict[str, Any] = {}
        for h_idx, h_name in enumerate(headers):
            if h_name.lower() not in known_cols and h_idx < len(row):
                val = row[h_idx]
                if val:
                    extra[h_name] = val

        word_entry = {
            "session_zid": file_zid,
            "sentence_index": norm_s_idx,
            "token_order": row_idx,
            "quotation": quotation,
            "inflected_form": inflected or None,
            "lemma": lemma,
            "pos": None,
            "morphology": morph or None,
            "ipa": ipa or None,
            "word_destination": w_dest or None,
            "word_destination_inflected": w_dest_inf or None,
            "selected": selected,
            "leitner_box": box,
            "leitner_due": due,
            "deck": deck,
            "classification_oxford": oxford,
            "classification_goethe": goethe,
            "extra_fields": extra if extra else None,
        }
        word_list.append(word_entry)

    text_mode = "multi" if len(sent_map) > 1 else "single"
    if not source_raw_text:
        source_raw_text = "\n".join(
            s["sentence_source"] for s in sent_map.values() if s.get("sentence_source")
        )

    target_lang = (
        config.get(SEC_SETTINGS, "default_target_language", fallback="ru")
        if config and hasattr(config, "get")
        else "ru"
    )

    session_record = {
        "zid": file_zid,
        "slug": detected_slug,
        "source_language": detected_lang,
        "target_language": target_lang,
        "text_mode": text_mode,
        "source_raw_text": source_raw_text,
    }

    return {
        "session": session_record,
        "sentences": list(sent_map.values()),
        "words": word_list,
        "comments": comments,
        "headers": headers,
        "data_rows": data_rows,
    }


def migrate_tsvs_to_db(results_dir: Path, config=None, resolved_paths=None, zid: Optional[str] = None) -> Dict[str, Any]:
    """
    Parses historical TSV files in results_dir, normalizes sentence indices to 1-based integers,
    deduplicates single-copy sentences, serializes extra fields to JSON, and executes chunked
    idempotent batch inserts into kardenwort.db.
    """
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)
    db.run_migrations(zid=zid)

    results_dir = Path(results_dir).resolve()
    if not results_dir.exists():
        return {
            "ok": True,
            "scanned_files": 0,
            "migrated_sessions": 0,
            "skipped_sessions": 0,
            "total_sentences": 0,
            "total_words": 0,
            "errors": [],
        }

    tsv_files = sorted(results_dir.glob("*.tsv"))
    session_files = [
        f for f in tsv_files
        if not f.name.endswith('.lock') and not f.name.startswith('temp_import_')
    ]

    with db.get_connection(read_only=True, zid=zid) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT zid FROM sessions;")
        existing_zids = set(r[0] for r in cursor.fetchall())

    migrated_sessions = 0
    skipped_sessions = 0
    total_sentences = 0
    total_words = 0
    errors = []

    for tsv_path in session_files:
        try:
            file_zid = extract_zid(tsv_path)
            if not file_zid:
                continue

            if file_zid in existing_zids:
                skipped_sessions += 1
                continue

            with file_lock(tsv_path):
                comments, headers, data_rows = load_tsv_rows(tsv_path)

            if not data_rows:
                skipped_sessions += 1
                continue

            name_no_ext = tsv_path.stem
            parts = name_no_ext.split('.')
            lang = parts[-1] if len(parts) > 1 and len(parts[-1]) in (2, 3, 5) else (
                config.get(SEC_SETTINGS, 'default_language', fallback='en') if config and hasattr(config, 'get') else 'en'
            )

            slug_part = parts[0]
            if slug_part.startswith(f"{file_zid}-"):
                slug = slug_part[len(file_zid) + 1:]
            else:
                slug = ""

            source_raw_text = ""
            txt_candidates = list(tsv_path.parent.glob(f"{file_zid}*.txt"))
            if txt_candidates:
                try:
                    source_raw_text = txt_candidates[0].read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            headers_lower = {h.lower(): idx for idx, h in enumerate(headers)}

            def get_col_val(r: List[str], col_name: str, default: str = "") -> str:
                idx = headers_lower.get(col_name.lower())
                return r[idx] if idx is not None and idx < len(r) else default

            known_cols = {
                "quotation", "wordsource", "wordsourceinflectedform",
                "worddestination", "worddestinationinflectedform",
                "wordsourcemorphologyai", "wordsourceipa", "deskselected", "leitnerbox",
                "leitnerdue", "deck", "classificationoxford", "classificationgoethe",
                "sentencesourceindex", "sentencesource", "sentencedestination",
                "sentencedestination2", "sentencesourceipa", "sentencesourceaudio",
                "sentencesourcecontextleft", "sentencesourcecontextright",
                "sentencedestinationcontextleft", "sentencedestinationcontextright",
                "sentencedestination2contextleft", "sentencedestination2contextright",
                "sentencesourcewordlist", "sentencesourcecloze",
                "wordsource2", "wordsourceinflectedform2",
            }

            raw_sentence_index_map: Dict[Any, int] = {}
            seq_counter = 1
            sent_map: Dict[int, Dict[str, Any]] = {}
            word_list: List[Dict[str, Any]] = []

            for row_idx, row in enumerate(data_rows):
                raw_s_val = get_col_val(row, "sentencesourceindex")
                if raw_s_val:
                    if raw_s_val not in raw_sentence_index_map:
                        raw_sentence_index_map[raw_s_val] = seq_counter
                        seq_counter += 1
                    norm_s_idx = raw_sentence_index_map[raw_s_val]
                else:
                    sent_src_text = get_col_val(row, "sentencesource")
                    if sent_src_text:
                        if sent_src_text not in raw_sentence_index_map:
                            raw_sentence_index_map[sent_src_text] = seq_counter
                            seq_counter += 1
                        norm_s_idx = raw_sentence_index_map[sent_src_text]
                    else:
                        norm_s_idx = 1

                if norm_s_idx not in sent_map:
                    sent_map[norm_s_idx] = {
                        "session_zid": file_zid,
                        "sentence_index": norm_s_idx,
                        "sentence_source": get_col_val(row, "sentencesource"),
                        "sentence_destination": get_col_val(row, "sentencedestination") or None,
                        "sentence_destination2": get_col_val(row, "sentencedestination2") or None,
                        "sentence_source_ipa": get_col_val(row, "sentencesourceipa") or None,
                        "sentence_source_audio": get_col_val(row, "sentencesourceaudio") or None,
                    }

                quotation = get_col_val(row, "quotation") or get_col_val(row, "wordsourceinflectedform") or get_col_val(row, "wordsource") or ""
                lemma = get_col_val(row, "wordsource") or quotation
                inflected = get_col_val(row, "wordsourceinflectedform")
                morph = get_col_val(row, "wordsourcemorphologyai")
                ipa = get_col_val(row, "wordsourceipa")
                w_dest = get_col_val(row, "worddestination")
                w_dest_inf = get_col_val(row, "worddestinationinflectedform")
                sel_raw = get_col_val(row, "deskselected")
                selected = 1 if str(sel_raw).strip() in ("1", "true", "True") else 0
                box_raw = get_col_val(row, "leitnerbox")
                box = int(box_raw) if box_raw.isdigit() else 1
                due = get_col_val(row, "leitnerdue") or None
                deck = get_col_val(row, "deck") or None
                oxford = get_col_val(row, "classificationoxford") or None
                goethe = get_col_val(row, "classificationgoethe") or None

                extra: Dict[str, Any] = {}
                for h_idx, h_name in enumerate(headers):
                    if h_name.lower() not in known_cols and h_idx < len(row):
                        val = row[h_idx]
                        if val:
                            extra[h_name] = val

                word_entry = {
                    "session_zid": file_zid,
                    "sentence_index": norm_s_idx,
                    "token_order": row_idx,
                    "quotation": quotation,
                    "inflected_form": inflected or None,
                    "lemma": lemma,
                    "pos": None,
                    "morphology": morph or None,
                    "ipa": ipa or None,
                    "word_destination": w_dest or None,
                    "word_destination_inflected": w_dest_inf or None,
                    "selected": selected,
                    "leitner_box": box,
                    "leitner_due": due,
                    "deck": deck,
                    "classification_oxford": oxford,
                    "classification_goethe": goethe,
                    "extra_fields": extra if extra else None,
                }
                word_list.append(word_entry)

            text_mode = "multi" if len(sent_map) > 1 else "single"
            if not source_raw_text:
                source_raw_text = "\n".join(s["sentence_source"] for s in sent_map.values() if s.get("sentence_source"))

            session_record = {
                "zid": file_zid,
                "slug": slug,
                "source_language": lang,
                "target_language": config.get(SEC_SETTINGS, 'default_target_language', fallback='ru') if config and hasattr(config, 'get') else 'ru',
                "text_mode": text_mode,
                "source_raw_text": source_raw_text,
            }

            db.save_session_bundle(
                session=session_record,
                sentences=list(sent_map.values()),
                words=word_list,
                zid=zid or file_zid,
            )

            existing_zids.add(file_zid)
            migrated_sessions += 1
            total_sentences += len(sent_map)
            total_words += len(word_list)
            logger.info(f"Migrated session {file_zid} ({len(sent_map)} sentences, {len(word_list)} words)")

        except Exception as e:
            logger.error(f"Failed to migrate TSV {tsv_path}: {e}")
            errors.append({"file": str(tsv_path), "error": str(e)})

    return {
        "ok": len(errors) == 0,
        "scanned_files": len(session_files),
        "migrated_sessions": migrated_sessions,
        "skipped_sessions": skipped_sessions,
        "total_sentences": total_sentences,
        "total_words": total_words,
        "errors": errors,
    }


def cmd_migrate_tsvs_to_db(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    kardenwort_workspace = resolved_paths.get('kardenwort_workspace')
    kw_config = load_kardenwort_config(kardenwort_workspace) if kardenwort_workspace else None
    results_dir = resolve_results_dir(resolved_paths, kw_config)

    zid = getattr(args, 'zid', None)
    res = migrate_tsvs_to_db(results_dir, config=config, resolved_paths=resolved_paths, zid=zid)

    if getattr(args, 'json_output', False) or getattr(args, 'json', False):
        out_str = json.dumps(res, indent=2)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(out_str + "\n")
            sys.__stdout__.flush()
        else:
            print(out_str)
    else:
        print(f"Scanned {res['scanned_files']} files: {res['migrated_sessions']} migrated, {res['skipped_sessions']} skipped, {res['total_sentences']} sentences, {res['total_words']} words.")
        if res.get('errors'):
            for err in res['errors']:
                print(f"Error migrating {err['file']}: {err['error']}")
    sys.exit(0 if res["ok"] else 1)


def cmd_create_project(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB, QuerySecurityError
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)

    title = getattr(args, 'title', None)
    if not title:
        print_structured_error("INVALID_STATE", "Missing required --title for project creation")
        sys.exit(1)

    slug = getattr(args, 'slug', None)
    parent_id = getattr(args, 'parent_id', None)
    description = getattr(args, 'description', '') or ''
    order_index = getattr(args, 'order_index', None)
    zid = getattr(args, 'zid', None)

    try:
        project_id = db.create_project(
            title=title,
            slug=slug,
            parent_id=parent_id,
            description=description,
            order_index=order_index,
            zid=zid,
        )
        created_project = db.get_project(project_id)
        res = {
            "ok": True,
            "project_id": project_id,
            "project": created_project,
            "message": f"Project '{title}' (ID: {project_id}) created successfully.",
        }
        if getattr(args, 'json_output', False) or getattr(args, 'json', False):
            out_str = json.dumps(res, indent=2, ensure_ascii=False)
            if sys.__stdout__ is not None:
                sys.__stdout__.write(out_str + "\n")
                sys.__stdout__.flush()
            else:
                print(out_str)
        else:
            print(res["message"])
        sys.exit(0)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to create project: {e}")
        sys.exit(1)


def cmd_list_projects(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)

    parent_id = getattr(args, 'parent_id', "all")
    include_deleted = getattr(args, 'include_deleted', False)
    as_tree = getattr(args, 'tree', False)
    zid = getattr(args, 'zid', None)

    if as_tree:
        p_id = int(parent_id) if parent_id not in ("all", None) else None
        data = db.get_project_tree(project_id=p_id, include_deleted=include_deleted, zid=zid)
    else:
        p_id = int(parent_id) if parent_id not in ("all", None) else parent_id
        data = db.list_projects(parent_id=p_id, include_deleted=include_deleted, zid=zid)

    if getattr(args, 'json_output', False) or getattr(args, 'json', False):
        out_str = json.dumps(data, indent=2, ensure_ascii=False)
        if sys.__stdout__ is not None:
            sys.__stdout__.write(out_str + "\n")
            sys.__stdout__.flush()
        else:
            print(out_str)
    else:
        if not data:
            print("No projects found.")
        else:
            if as_tree:
                def _print_node(node, level=0):
                    indent = "  " * level
                    print(f"{indent}- [{node['id']}] {node['title']} (slug: {node['slug']}, sessions: {len(node.get('sessions', []))})")
                    for child in node.get("children", []):
                        _print_node(child, level + 1)
                for root in data:
                    _print_node(root)
            else:
                header = f"{'ID':<6} {'PARENT':<8} {'ORDER':<6} {'SLUG':<20} {'TITLE'}"
                print(header)
                print("-" * len(header))
                for p in data:
                    parent_str = str(p.get('parent_id') or '-')
                    print(f"{p.get('id', ''):<6} {parent_str:<8} {p.get('order_index', 0):<6} {p.get('slug', ''):<20} {p.get('title', '')}")
    sys.exit(0)


def cmd_link_session(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)

    project_id = getattr(args, 'project_id', None)
    session_zid = getattr(args, 'session_zid', None) or getattr(args, 'zid', None)

    if project_id is None:
        print_structured_error("INVALID_STATE", "Missing required --project-id for session linking")
        sys.exit(1)
    if not session_zid:
        print_structured_error("INVALID_STATE", "Missing required --session-zid / --zid for session linking")
        sys.exit(1)

    order_index = getattr(args, 'order_index', None)
    zid = getattr(args, 'zid', None)

    try:
        ok = db.link_session_to_project(
            project_id=int(project_id),
            session_zid=str(session_zid).strip(),
            order_index=int(order_index) if order_index is not None else None,
            zid=zid,
        )
        res = {
            "ok": ok,
            "project_id": int(project_id),
            "session_zid": str(session_zid).strip(),
            "message": f"Session '{session_zid}' linked to project {project_id} successfully." if ok else "Failed to link session.",
        }
        if getattr(args, 'json_output', False) or getattr(args, 'json', False):
            out_str = json.dumps(res, indent=2)
            if sys.__stdout__ is not None:
                sys.__stdout__.write(out_str + "\n")
                sys.__stdout__.flush()
            else:
                print(out_str)
        else:
            print(res["message"])
        sys.exit(0 if ok else 1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to link session to project: {e}")
        sys.exit(1)


def cmd_reorder_session(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)
    from kardenwort_db import KardenwortDB
    db = KardenwortDB(config=config, resolved_paths=resolved_paths)

    project_id = getattr(args, 'project_id', None)
    if project_id is None:
        print_structured_error("INVALID_STATE", "Missing required --project-id for session reordering")
        sys.exit(1)

    raw_zids = getattr(args, 'session_zids', None)
    if not raw_zids:
        print_structured_error("INVALID_STATE", "Missing required --session-zids for session reordering")
        sys.exit(1)

    if isinstance(raw_zids, str):
        session_zids = [z.strip() for z in raw_zids.split(",") if z.strip()]
    else:
        session_zids = list(raw_zids)

    zid = getattr(args, 'zid', None)

    try:
        ok = db.reorder_project_sessions(
            project_id=int(project_id),
            session_zids=session_zids,
            zid=zid,
        )
        res = {
            "ok": ok,
            "project_id": int(project_id),
            "session_zids": session_zids,
            "message": f"Reordered {len(session_zids)} session(s) in project {project_id} successfully." if ok else "Failed to reorder sessions.",
        }
        if getattr(args, 'json_output', False) or getattr(args, 'json', False):
            out_str = json.dumps(res, indent=2)
            if sys.__stdout__ is not None:
                sys.__stdout__.write(out_str + "\n")
                sys.__stdout__.flush()
            else:
                print(out_str)
        else:
            print(res["message"])
        sys.exit(0 if ok else 1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to reorder project sessions: {e}")
        sys.exit(1)


def cmd_export_project_deck(args):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, _, _ = load_config(config_path)

    project_id = getattr(args, 'project_id', None)
    if project_id is None:
        print_structured_error("INVALID_STATE", "Missing required --project-id for export-project-deck")
        sys.exit(1)

    lang = getattr(args, 'language', None)
    send_to_anki = getattr(args, 'send_to_anki', False)
    zid = getattr(args, 'zid', None)

    try:
        result = aggregate_project_materials(
            project_id=int(project_id),
            config=config,
            resolved_paths=resolved_paths,
            language=lang,
            zid=zid,
        )

        if send_to_anki:
            tsv_path = Path(result["tsv_path"])
            json_path = Path(result["json_path"])
            pid, log_path = run_detached_import(
                tsv_path,
                config,
                resolved_paths,
                zid=zid or f"project_{project_id}",
                trace_id=f"project:{project_id}:export",
            )
            result["anki_import_started"] = True
            result["pid"] = pid
            result["log"] = log_path

        if getattr(args, 'json_output', False) or getattr(args, 'json', False):
            out_str = json.dumps(result, indent=2, ensure_ascii=False)
            if sys.__stdout__ is not None:
                sys.__stdout__.write(out_str + "\n")
                sys.__stdout__.flush()
            else:
                print(out_str)
        else:
            print(f"Exported project '{result['project_title']}' (ID: {project_id}) to:")
            print(f"  TSV:  {result['tsv_path']}")
            print(f"  JSON: {result['json_path']}")
            print(f"  Total sessions: {result['total_sessions']}, Total words: {result['total_words']}")
        sys.exit(0)
    except StructuredError as se:
        print_structured_error(se.error_code, se.message, se.details)
        sys.exit(1)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to export project deck: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Kardenwort Desk Orchestration Core")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    parser.add_argument("--storage", choices=["tsv", "sqlite"], default=None, help="Storage backend (tsv or sqlite, overrides config)")
    parser.add_argument("--bypass-lang-check", "--force-language", dest="bypass_lang_check", action="store_true", help="Bypass pre-flight language verification")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    parser.add_argument("--zid", default=None, help="Session ZID")
    parser.add_argument("--trace-id", default=None, help="Trace correlation ID")
    parser.add_argument("--db-status", action="store_true", help="Output database status")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")
    parser.add_argument("--db-check", action="store_true", help="Run database integrity and foreign key checks")
    parser.add_argument("--db-query", default=None, help="Execute safe read-only SQL query")
    parser.add_argument("--db-reset", action="store_true", help="Reset database (requires --force)")
    parser.add_argument("--force", action="store_true", help="Force flag for destructive operations")
    parser.add_argument("--list-sessions", action="store_true", help="List active sessions with token counts")
    parser.add_argument("--delete-session", action="store_true", help="Delete session by ZID (requires --zid)")
    parser.add_argument("--cleanup-db", action="store_true", help="Purge old sessions (requires --older-than)")
    parser.add_argument("--older-than", type=float, default=None, help="Retention period in days for cleanup-db")
    parser.add_argument("--vacuum-db", action="store_true", help="Defragment and vacuum SQLite database")
    parser.add_argument("--migrate-tsvs-to-db", action="store_true", help="Migrate historical TSVs in results/ to SQLite DB")
    parser.add_argument("--create-project", action="store_true", help="Create a project node")
    parser.add_argument("--list-projects", action="store_true", help="List projects or project tree")
    parser.add_argument("--link-session", action="store_true", help="Link session to project")
    parser.add_argument("--reorder-session", action="store_true", help="Reorder sessions in project")
    parser.add_argument("--export-project-deck", action="store_true", help="Export aggregated project deck")
    parser.add_argument("--title", default=None, help="Project title")
    parser.add_argument("--slug", default=None, help="Project slug")
    parser.add_argument("--parent-id", type=int, default=None, help="Parent project ID")
    parser.add_argument("--project", "--project-id", dest="project_id", type=int, default=None, help="Target project ID or synthesize project session")
    parser.add_argument("--session-zid", default=None, help="Session ZID for project linking")
    parser.add_argument("--session-zids", nargs="+", default=None, help="Session ZIDs for project reordering")
    parser.add_argument("--description", default=None, help="Project description")
    parser.add_argument("--order-index", type=int, default=None, help="Explicit order index")
    parser.add_argument("--tree", action="store_true", help="Display projects as tree")
    parser.add_argument("--include-deleted", action="store_true", help="Include soft-deleted records")
    parser.add_argument("--send-to-anki", action="store_true", help="Send exported deck directly to Anki")
    parser.add_argument("--sentence-match-strategy", choices=["normalized", "checksum", "contextual", "none"], default=None, help="Sentence match/lookup strategy")
    parser.add_argument("--no-checksum-lookup", action="store_true", help="Bypass sentence matching and cached checksum lookup")

    subparsers = parser.add_subparsers(dest="command", required=False)

    # create-project
    p_create_proj = subparsers.add_parser("create-project")
    p_create_proj.add_argument("--title", required=True, help="Project title")
    p_create_proj.add_argument("--slug", default=None, help="Project slug")
    p_create_proj.add_argument("--parent-id", type=int, default=None, help="Parent project ID")
    p_create_proj.add_argument("--description", default="", help="Project description")
    p_create_proj.add_argument("--order-index", type=int, default=None, help="Order index")
    p_create_proj.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # list-projects
    p_list_proj = subparsers.add_parser("list-projects")
    p_list_proj.add_argument("--parent-id", type=int, default=None, help="Parent project ID filter")
    p_list_proj.add_argument("--tree", action="store_true", help="Display as hierarchical tree")
    p_list_proj.add_argument("--include-deleted", action="store_true", help="Include soft-deleted projects")
    p_list_proj.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # link-session
    p_link_sess = subparsers.add_parser("link-session")
    p_link_sess.add_argument("--project-id", type=int, required=True, help="Project ID")
    p_link_sess.add_argument("--session-zid", "--zid", dest="session_zid", required=True, help="Session ZID")
    p_link_sess.add_argument("--order-index", type=int, default=None, help="Order index")
    p_link_sess.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # reorder-session
    p_reorder_sess = subparsers.add_parser("reorder-session")
    p_reorder_sess.add_argument("--project-id", type=int, required=True, help="Project ID")
    p_reorder_sess.add_argument("--session-zids", nargs="+", required=True, help="Ordered session ZIDs")
    p_reorder_sess.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # export-project-deck
    p_export_proj = subparsers.add_parser("export-project-deck")
    p_export_proj.add_argument("--project-id", type=int, required=True, help="Project ID")
    p_export_proj.add_argument("--language", default=None, help="Language code")
    p_export_proj.add_argument("--send-to-anki", action="store_true", help="Send to Anki after export")
    p_export_proj.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # db-status
    p_db_status = subparsers.add_parser("db-status")
    p_db_status.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # db-check
    p_db_check = subparsers.add_parser("db-check")

    # db-query
    p_db_query = subparsers.add_parser("db-query")
    p_db_query.add_argument("--query", required=True, help="SQL query to execute")

    # db-reset
    p_db_reset = subparsers.add_parser("db-reset")
    p_db_reset.add_argument("--force", action="store_true", help="Confirm database reset")

    # list-sessions
    p_list_sess = subparsers.add_parser("list-sessions")
    p_list_sess.add_argument("--limit", type=int, default=None, help="Limit number of sessions returned")
    p_list_sess.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # delete-session
    p_del_sess = subparsers.add_parser("delete-session")
    p_del_sess.add_argument("--zid", required=True, help="Session ZID to delete")
    p_del_sess.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # cleanup-db
    p_cleanup = subparsers.add_parser("cleanup-db")
    p_cleanup.add_argument("--older-than", type=float, required=True, help="Retention window in days")
    p_cleanup.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # vacuum-db
    p_vacuum = subparsers.add_parser("vacuum-db")
    p_vacuum.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # migrate-tsvs-to-db
    p_migrate = subparsers.add_parser("migrate-tsvs-to-db")
    p_migrate.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # lookup
    p_lookup = subparsers.add_parser("lookup")
    p_lookup.add_argument("--text", required=True, help="Text to lookup")
    p_lookup.add_argument("--language", help="Source language code")
    p_lookup.add_argument("--target-lang", help="Target language code")
    p_lookup.add_argument("--format", choices=["html", "text", "combined"], help="Output format")
    p_lookup.add_argument("--text-mode", choices=["single", "multi", "auto", "sentence"], default="single", help="Text translation mode")
    p_lookup.add_argument("--storage", choices=["tsv", "sqlite"], default=None, help="Storage backend override")
    p_lookup.add_argument("--sections", help="Comma-separated sections to render")
    p_lookup.add_argument("--lemma-columns", help="Comma-separated columns for the lemmas table")
    p_lookup.add_argument("--no-headings", action="store_true", help="Disable headings")
    p_lookup.add_argument("--disable-css", action="store_true", help="Disable outputting CSS styles in HTML")
    p_lookup.add_argument("--theme", choices=["dark", "light", "compact"], help="Theme (html format)")
    p_lookup.add_argument("--bypass-lang-check", "--force-language", dest="bypass_lang_check", action="store_true", help="Bypass pre-flight language verification")
    p_lookup.add_argument("--sentence-match-strategy", choices=["normalized", "checksum", "contextual", "none"], default=None, help="Sentence match/lookup strategy")
    p_lookup.add_argument("--no-checksum-lookup", action="store_true", help="Bypass sentence matching and cached checksum lookup")
    p_lookup.add_argument("--zid", default=None, help="Session ZID")
    p_lookup.add_argument("--trace-id", default=None, help="Trace correlation ID")


    # render
    p_render = subparsers.add_parser("render")
    p_render.add_argument("--text", help="Selected text")
    p_render.add_argument("--language", required=True, help="Language code")
    p_render.add_argument("--zid", required=True, help="Session ZID")
    p_render.add_argument("--trace-id", default=None, help="Trace correlation ID")
    p_render.add_argument("--text-mode", choices=["single", "multi"], default="single")
    p_render.add_argument("--zoom", default=None, help="Zoom level for CSS scaling (falls back to config default_zoom)")
    p_render.add_argument("--tsv", default=None, help="Path to TSV file to render")
    p_render.add_argument("--theme", default="dark", choices=["dark", "light", "white"], help="Theme (dark or light or white)")
    p_render.add_argument("--split-gap-limit", type=int, default=None, help="Maximum source-word index distance allowed between parts of a split/separable verb construct")
    p_render.add_argument("--seq-num", type=int, default=None, help="Parent window sequence number")
    p_render.add_argument("--bypass-lang-check", "--force-language", dest="bypass_lang_check", action="store_true", help="Bypass pre-flight language verification")

    # export
    p_export = subparsers.add_parser("export")
    p_export.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_export.add_argument("--language", required=True, help="Language code")
    p_export.add_argument("--zid", default=None, help="Session ZID")
    p_export.add_argument("--trace-id", default=None, help="Trace correlation ID")

    p_export_selected = subparsers.add_parser("export-selected")
    p_export_selected.add_argument("--files", nargs="+", required=True, help="Paths to TSV files")
    p_export_selected.add_argument("--language", default=None, help="Language code")
    p_export_selected.add_argument("--pause", action="store_true", help="Pause on exit")
    p_export_selected.add_argument("--zid", default=None, help="Session ZID")
    p_export_selected.add_argument("--trace-id", default=None, help="Trace correlation ID")

    p_import_selected = subparsers.add_parser("import-selected")
    p_import_selected.add_argument("--files", nargs="+", required=True, help="Paths to TSV files")
    p_import_selected.add_argument("--language", default=None, help="Language code")
    p_import_selected.add_argument("--pause", action="store_true", help="Pause on exit")
    p_import_selected.add_argument("--zid", default=None, help="Session ZID")
    p_import_selected.add_argument("--trace-id", default=None, help="Trace correlation ID")

    # reprocess
    p_reprocess = subparsers.add_parser("reprocess")
    p_reprocess.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_reprocess.add_argument("--language", required=True, help="Language code")
    p_reprocess.add_argument("--zid", default=None, help="Session ZID")
    p_reprocess.add_argument("--trace-id", default=None, help="Trace correlation ID")

    # retext
    p_retext = subparsers.add_parser("retext")
    p_retext.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_retext.add_argument("--language", required=True, help="Language code")
    p_retext.add_argument("--text-mode", default="single", choices=["single", "multi"], help="Text mode (single or multi)")
    p_retext.add_argument("--zid", default=None, help="Session ZID")
    p_retext.add_argument("--trace-id", default=None, help="Trace correlation ID")

    # batch-worker
    p_batch_worker = subparsers.add_parser("batch-worker")
    p_batch_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_batch_worker.add_argument("--prompt", required=True, help="Prompt name")
    p_batch_worker.add_argument("--rows", required=True, help="Comma-separated list of row indices")
    p_batch_worker.add_argument("--zid", default=None, help="Session ZID")
    p_batch_worker.add_argument("--trace-id", default=None, help="Trace correlation ID")

    # retext-worker
    p_retext_worker = subparsers.add_parser("retext-worker")
    p_retext_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_retext_worker.add_argument("--language", required=True, help="Language code")
    p_retext_worker.add_argument("--text-mode", default="single", choices=["single", "multi"], help="Text mode (single or multi)")
    p_retext_worker.add_argument("--zid", default=None, help="Session ZID")
    p_retext_worker.add_argument("--trace-id", default=None, help="Trace correlation ID")

    # progressive-worker
    p_prog_worker = subparsers.add_parser("progressive-worker")
    p_prog_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_prog_worker.add_argument("--language", required=True, help="Language code")
    p_prog_worker.add_argument("--target-lang", required=True, help="Target language code")
    p_prog_worker.add_argument("--prompt", required=True, help="Prompt name")
    p_prog_worker.add_argument("--provider", required=True, help="Lemmas provider")
    p_prog_worker.add_argument("--word-empty", required=True, help="Word translations empty flag")
    p_prog_worker.add_argument("--text-mode", default="single", help="Text chunking mode")
    p_prog_worker.add_argument("--skip-intellifiller", action="store_true", help="Skip intellifiller phase")
    p_prog_worker.add_argument("--zid", default=None, help="Session ZID")
    p_prog_worker.add_argument("--trace-id", default=None, help="Trace correlation ID")

    # edit-save
    p_edit = subparsers.add_parser("edit-save")
    p_edit.add_argument("--deltas", required=True, help="Deltas JSON file path")
    p_edit.add_argument("--zid", required=True, help="Session ZID")
    p_edit.add_argument("--trace-id", default=None, help="Trace correlation ID")
    p_edit.add_argument("--language", help="Language code")
    p_edit.add_argument("--tsv", help="Explicit TSV path")

    # merge
    p_merge = subparsers.add_parser("merge")
    p_merge.add_argument("--files", nargs="+", required=True, help="List of TSV files to merge")
    p_merge.add_argument("--target", default="new", help="Merge target path, new, or first")
    p_merge.add_argument("--delete-sources", action="store_true", help="Delete source files after merge")
    p_merge.add_argument("--deduplicate", action="store_true", help="Deduplicate by lemma or (inflected, lemma) pair, prioritizing the row with most fields filled")
    p_merge.add_argument("--sort-frequency", action="store_true", help="Sort the merged TSV rows by lemma frequency from Kardenwort Core")
    p_merge.add_argument("--pause", action="store_true", help="Pause on exit to keep console window open")

    # restore
    p_restore = subparsers.add_parser("restore")
    p_restore.add_argument("--file", nargs="*", default=None, help="Session file to restore")
    p_restore.add_argument("--zid", default=None, help="Session ZID to restore")
    p_restore.add_argument("--project", "--project-id", dest="project", type=int, default=None, help="Project ID to synthesize")
    p_restore.add_argument("--language", default=None, help="Language code")
    p_restore.add_argument("--no-gui", action="store_true", help="Do not spawn AHK window")
    p_restore.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # desk
    p_desk = subparsers.add_parser("desk")
    p_desk.add_argument("--file", nargs="*", default=None, help="Text file to analyze")
    p_desk.add_argument("--project", "--project-id", dest="project", type=int, default=None, help="Project ID for multi-chapter synthesis")
    p_desk.add_argument("--text-mode", choices=["single", "multi"], default="multi")
    p_desk.add_argument("--language", help="Language code")
    p_desk.add_argument("--no-gui", action="store_true", help="Do not spawn AHK window")
    p_desk.add_argument("--theme", default="dark", choices=["dark", "light", "white"], help="Theme (dark or light or white)")
    p_desk.add_argument("--bypass-lang-check", "--force-language", dest="bypass_lang_check", action="store_true", help="Bypass pre-flight language verification")
    p_desk.add_argument("--sentence-match-strategy", choices=["normalized", "checksum", "contextual", "none"], default=None, help="Sentence match/lookup strategy")
    p_desk.add_argument("--no-checksum-lookup", action="store_true", help="Bypass sentence matching and cached checksum lookup")
    p_desk.add_argument("--zid", default=None, help="Session ZID")
    p_desk.add_argument("--trace-id", default=None, help="Trace correlation ID")
    p_desk.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")

    # wordfill
    p_wordfill = subparsers.add_parser("wordfill")
    p_wordfill.add_argument("--word", required=True, help="Word to look up (lemma or inflected form)")
    p_wordfill.add_argument("--language", required=True, help="Source language code (e.g. en, de)")

    # server
    p_server = subparsers.add_parser("server")
    p_server.add_argument("--host", default=None, help="Host address to bind to (overrides config)")
    p_server.add_argument("--port", type=int, default=None, help="Port number to bind to (overrides config)")
    p_server.add_argument("--no-sidecars", action="store_true", help="Do not spawn or supervise sidecar microservices")

    # controller
    p_controller = subparsers.add_parser("controller")
    p_controller.add_argument("--host", default=None, help="Host address to bind to (overrides config)")
    p_controller.add_argument("--port", type=int, default=None, help="Port number to bind to (overrides config)")
    p_controller.add_argument("--no-sidecars", action="store_true", help="Do not spawn or supervise sidecar microservices")

    try:
        args = parser.parse_args()
        
        if hasattr(args, 'text') and args.text:
            args.text = args.text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
            
    except SystemExit as e:
        if e.code != 0:
            print_structured_error("INVALID_STATE", "Failed to parse command line arguments")
            sys.exit(1)
        sys.exit(0)

    setup_logging(verbose=args.verbose, debug=args.debug)

    # Top-level DB diagnostics shortcuts
    if getattr(args, 'db_status', False):
        cmd_db_status(args)
        return
    if getattr(args, 'db_check', False):
        cmd_db_check(args)
        return
    if getattr(args, 'db_query', None) is not None:
        cmd_db_query(args)
        return
    if getattr(args, 'db_reset', False):
        cmd_db_reset(args)
        return
    if getattr(args, 'list_sessions', False):
        cmd_list_sessions(args)
        return
    if getattr(args, 'delete_session', False):
        cmd_delete_session(args)
        return
    if getattr(args, 'cleanup_db', False):
        cmd_cleanup_db(args)
        return
    if getattr(args, 'vacuum_db', False):
        cmd_vacuum_db(args)
        return
    if getattr(args, 'migrate_tsvs_to_db', False):
        cmd_migrate_tsvs_to_db(args)
        return
    if getattr(args, 'create_project', False):
        cmd_create_project(args)
        return
    if getattr(args, 'list_projects', False):
        cmd_list_projects(args)
        return
    if getattr(args, 'link_session', False):
        cmd_link_session(args)
        return
    if getattr(args, 'reorder_session', False):
        cmd_reorder_session(args)
        return
    if getattr(args, 'export_project_deck', False):
        cmd_export_project_deck(args)
        return
    if getattr(args, 'project', None) is not None and not getattr(args, 'command', None):
        cmd_desk(args)
        return

    commands = {
        "lookup": cmd_lookup,
        "render": cmd_render,
        "export": cmd_export,
        "export-selected": cmd_export_selected,
        "import-selected": cmd_import_selected,
        "reprocess": cmd_reprocess,
        "retext": cmd_retext,
        "batch-worker": cmd_reprocess_worker,
        "retext-worker": cmd_retext_worker,
        "progressive-worker": cmd_progressive_worker,
        "edit-save": cmd_edit_save,
        "merge": cmd_merge,
        "restore": cmd_restore,
        "desk": cmd_desk,
        "wordfill": cmd_wordfill,
        "server": lambda args: __import__('kardenwort_controller').run_controller(args),
        "controller": lambda args: __import__('kardenwort_controller').run_controller(args),
        "db-status": cmd_db_status,
        "db-check": cmd_db_check,
        "db-query": cmd_db_query,
        "db-reset": cmd_db_reset,
        "list-sessions": cmd_list_sessions,
        "delete-session": cmd_delete_session,
        "cleanup-db": cmd_cleanup_db,
        "vacuum-db": cmd_vacuum_db,
        "migrate-tsvs-to-db": cmd_migrate_tsvs_to_db,
        "create-project": cmd_create_project,
        "list-projects": cmd_list_projects,
        "link-session": cmd_link_session,
        "reorder-session": cmd_reorder_session,
        "export-project-deck": cmd_export_project_deck,
    }

    if not getattr(args, 'command', None):
        parser.print_help(sys.stderr)
        sys.exit(1)

    try:
        commands[args.command](args)
    except Exception as e:
        if hasattr(e, 'error_code') and hasattr(e, 'message'):
            print_structured_error(e.error_code, e.message, details=getattr(e, 'details', None))
        else:
            print_structured_error("DESK_FAILED", str(e))
        sys.exit(1)

if __name__ == "__main__":
    sys.stdout = sys.stderr
    
    if sys.__stdout__ is not None and hasattr(sys.__stdout__, 'reconfigure'):
        sys.__stdout__.reconfigure(encoding='utf-8')
    
    if sys.stderr is not None and hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
        
    main()
