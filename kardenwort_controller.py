from kardenwort_state_runtime import (OperationConfiguration, operation_preferences,
                                      operation_configuration, request_preferences)
import kardenwort_progressive_completion as progressive
import sys
from collections import deque
from contextlib import contextmanager
import sqlite3
from kardenwort_db import QueryExecutionError
import kardenwort_reword as reword
from kardenwort_state_http import STATE_ROUTES, dispatch_state, set_language
from kardenwort_sqlite_preferences import PreferenceConflict
from kardenwort_state_runtime import refresh_preferences, preference_store
from kardenwort_audio import AudioError
from kardenwort_audio_runtime import execute_audio
from kardenwort_controller_lifetime import ControllerHTTPServer, run_cleanup, stop_owned_process
import os
import re
import inspect
import json
import time
import queue
import configparser
import socket
import logging
import threading
import traceback
import subprocess
import webbrowser
import urllib.parse
import urllib.request
import urllib.error
import ipaddress
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple, Union
from http.server import BaseHTTPRequestHandler
from b64util import encode

# Import core business logic primitives from kardenwort_desk
from kardenwort_translation_validation import is_stored_translation_failure, is_unfinished_translation
from kardenwort_desk import (
    load_config,
    resolve_ui_config,
    SEC_UI,
    load_kardenwort_config,
    resolve_results_dir,
    load_anki_mapping,
    get_role_fields,
    load_tsv_rows,
    save_tsv_rows_safely,
    compute_content_fingerprint,
    file_lock,
    check_coordination_busy,
    core_lookup,
    core_export,
    core_edit_save,
    resolve_occurrence_rows,
    resolve_reword_request,
    reword_descriptors,
    enrich_reword_tsv,
    checked_provider_rows,
    reword_query_occurrences,
    reword_query_provenances,
    commit_reword_sqlite,
    commit_reword_tsv,
    query_spacy_server,
    query_translation_server,
    query_intellifiller_server,
    tokenize_text_with_fallback,
    translate_lemmas_fast_path,
    translate_source_text,
    resolve_provider_chain,
    run_headless_intellifiller,
    StructuredError,
    ErrorCode,
    generate_unique_zid,
    find_working_tsv,
    extract_zid,
    generate_slug,
    get_storage_adapter,
    storage_backend_setting,
    parse_tsv_to_bundle,
    render_lookup_html,
    render_verify_language_html,
    run_render_flow,
    verify_language,
    synthesize_project_materials,
    aggregate_project_materials,
    resolve_project_deck_path,
    safe_write_update_js,
    get_session_active_providers,
    register_failover_callback,
    unregister_failover_callback,
    record_session_active_provider,
    record_provider_failover,
    is_network_online_multi,
    is_provider_cooled_down,
    format_translated_html,
    format_update_rows_dict,
    SessionLogger,
    find_wordfill_match,
    apply_wordfill_to_rows,
    resolve_wordfill_config,
    sort_rows_by_frequency,
    sort_session_data_rows,
    resolve_translations,
    SEC_SETTINGS,
    SEC_LANGUAGES,
    SEC_PIPELINE,
    SEC_TRIGGERS,
    SEC_SERVICES,
    SEC_TRANSLATION,
    SEC_TIMEOUTS,
    SEC_CLASSIFICATION,
    persist_default_language,
    persist_config_value,
    spawn_ahk,
)

logger = logging.getLogger("kardenwort.desk.controller")


@contextmanager
def committed_delivery(session_zid, fingerprint):
    try:
        yield
    except Exception as exc:
        raise StructuredError(ErrorCode.DESK_FAILED, 'Committed state could not be synchronized', details={
            'committed': True, 'session_zid': session_zid, 'fingerprint': fingerprint,
            'recovery_url': f'/session/status?zid={session_zid}&committed=1',
        }) from exc


def format_storage_update_rows(adapter, zid, rows, headers, roles, provenances):
    """Use stored occurrence facts only when the read model carries their identity."""
    metadata = {}
    if (getattr(adapter, 'backend_name', '') == 'sqlite' and Path(adapter.db.db_path).is_file()
            and 'TokenOrder' in headers and roles.get('sentence_index', 'SentenceSourceIndex') in headers):
        metadata = dict(row_occurrences=reword_query_occurrences(adapter, zid),
                        occurrence_provenances=reword_query_provenances(adapter, zid))
    return format_update_rows_dict(rows, headers, roles, row_provenances=provenances, **metadata)


def recovery_storage_rows(arbiter, adapter, zid, memo):
    if adapter.backend_name == 'sqlite':
        restored = adapter.restore_session(zid)
        headers, rows = reword.restored_manifest_rows(restored)
        return restored, headers, rows
    directory = resolve_results_dir(arbiter.resolved_paths, arbiter.config)
    path = memo.get('tsv_path') or find_working_tsv(directory, zid)
    if not path:
        raise StructuredError(ErrorCode.NOT_FOUND, 'Committed recovery storage is unavailable')
    with adapter.file_lock(Path(path)):
        _, headers, rows = adapter.load_tsv_rows(Path(path))
    return {}, headers, rows


def recovery_row_order(rows, headers, roles, memo):
    sentence = roles.get('sentence_index', 'SentenceSourceIndex')
    keys = reword.row_keys(rows, headers, sentence)
    if not memo.get('data_rows'):
        return rows
    original = reword.row_keys(memo['data_rows'], memo['headers'], sentence)
    rank = {key: index for index, key in enumerate(original)}
    return [row for key, row in sorted(zip(keys, rows), key=lambda pair: rank.get(pair[0], len(rank)))]


def restored_occurrence_metadata(restored):
    """Project occurrence facts without reading outside the restored transaction."""
    from kardenwort_db import sanitize_extra_fields
    words = restored.get('words', [])
    occurrences = {(str(word['sentence_index']), str(word['token_order'])):
        sanitize_extra_fields(word.get('extra_fields')).get('occurrence_data', {}) for word in words}
    provenances = {(str(word['sentence_index']), str(word['token_order'])):
        word.get('word_provenance', '') for word in words}
    return dict(row_occurrences=occurrences, occurrence_provenances=provenances)


def recovery_update_rows(restored, rows, headers, roles):
    """Project rows and occurrence facts from the same restored storage bundle."""
    return format_update_rows_dict(rows, headers, roles, **restored_occurrence_metadata(restored))


def progressive_delivery_failure(error, session_zid, fingerprint, committed):
    failure = StructuredError(ErrorCode.DESK_FAILED, 'Progressive terminal update could not be delivered',
        details=dict(committed=committed, session_zid=session_zid, fingerprint=fingerprint,
                     recovery_url=f'/session/status?zid={session_zid}&committed=1'))
    failure.__cause__ = error
    return failure


def _flatten_finalization_errors(errors):
    flattened = []
    for error in errors:
        if isinstance(error, BaseExceptionGroup):
            flattened.extend(_flatten_finalization_errors(error.exceptions))
        elif error is not None:
            flattened.append(error)
    return flattened


def raise_progressive_finalization_errors(*errors):
    flat = _flatten_finalization_errors(errors)
    if len(flat) == 1:
        raise flat[0]
    if flat:
        raise BaseExceptionGroup('Progressive finalization failed', flat)


def _sentence_writer_target(adapter):
    inner = getattr(adapter, 'adapter', None)
    if inner is not None and hasattr(inner, 'update_sentence_translation'):
        return inner.update_sentence_translation
    return adapter.update_sentence_translation


def _supports_keyword_arg(callable_obj, name: str) -> bool:
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    return name in sig.parameters or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()
    )


def _sentence_write_kwargs(target, target_field, zid, provenance):
    kwargs = {}
    if target_field != 'sentence_destination' or _supports_keyword_arg(target, 'target_field'):
        kwargs['target_field'] = target_field
    if _supports_keyword_arg(target, 'zid'):
        kwargs['zid'] = zid
    if provenance is not None and _supports_keyword_arg(target, 'provenance'):
        kwargs['provenance'] = provenance
    return kwargs


def _persist_sentence_field(adapter, session_zid, sentence_index, text, *, target_field, zid, provenance):
    kwargs = _sentence_write_kwargs(_sentence_writer_target(adapter), target_field, zid, provenance)
    if adapter.update_sentence_translation(session_zid, sentence_index, text, **kwargs) is False:
        raise RuntimeError(f'Sentence {sentence_index} ({target_field}) was not updated in storage')


def _sqlite_session_active(adapter, session_zid) -> bool:
    if not hasattr(adapter, 'db'):
        return True
    with adapter.db.get_connection(read_only=True) as conn:
        row = conn.execute('SELECT 1 FROM sessions WHERE zid = ? AND deleted_at IS NULL', (session_zid,)).fetchone()
    return row is not None


def _try_persist_sentence(adapter, session_zid, s_idx, text, target_field, req_zid, provenance, failures):
    try:
        _persist_sentence_field(
            adapter, session_zid, s_idx, text, target_field=target_field, zid=req_zid, provenance=provenance
        )
        return True
    except Exception as exc:
        logger.warning(f'Sentence {s_idx} ({target_field}) persistence failed for {session_zid}: {exc}')
        failures.append((s_idx, target_field, exc))
        return False


def _sentence_persistence_error(failures):
    _, first_field, first_exc = failures[0]
    failed_indices = sorted({idx for idx, _, _ in failures})
    err = progressive.sentence_stage_error(
        first_exc,
        provider='storage',
        failed_sentences=failed_indices,
        code='ERR_SENTENCE_PERSISTENCE_FAILED',
    )
    err['details']['failed_field'] = first_field
    err['details']['failed_writes'] = [
        {'sentence_index': idx, 'field': field, 'message': str(exc)}
        for idx, field, exc in failures
    ]
    return err


def _persist_sentence_entry(adapter, session_zid, raw_key, s_idx, trans, padded, req_zid, provenance, committed, failures):
    if _try_persist_sentence(adapter, session_zid, s_idx, trans, 'sentence_destination', req_zid, provenance, failures):
        committed.add(s_idx)
    padded_trans = progressive.padded_value(padded, raw_key)
    if padded_trans:
        _try_persist_sentence(adapter, session_zid, s_idx, padded_trans, 'sentence_destination2', req_zid, provenance, failures)


def _persist_sqlite_sentences(adapter, session_zid, raw_translations, provenance, req_zid):
    entries = progressive.sentence_entries(raw_translations)
    if not entries:
        return set(), None
    if not _sqlite_session_active(adapter, session_zid):
        raise StructuredError(ErrorCode.NOT_FOUND, f'Session {session_zid} was deleted before sentence commit')
    padded = raw_translations.get('PADDED') if isinstance(raw_translations, dict) else None
    committed, failures = set(), []
    for raw_key, s_idx, trans in entries:
        _persist_sentence_entry(adapter, session_zid, raw_key, s_idx, trans, padded, req_zid, provenance, committed, failures)
    return committed, (_sentence_persistence_error(failures) if failures else None)


def _session_cache_revision(session):
    if session is None:
        return None
    rows = session.get('data_rows')
    headers = session.get('headers')
    return (
        id(session),
        tuple(tuple(r) for r in rows) if rows is not None else None,
        session.get('fingerprint'),
        session.get('translatedText'),
        session.get('sentence_translation'),
        tuple(headers) if headers is not None else None,
    )


def committed_recovery_snapshot(arbiter, zid):
    adapter = getattr(arbiter, 'storage_adapter', None) or get_storage_adapter(
        arbiter.config, arbiter.resolved_paths, read_only=True)
    with arbiter._lock:
        raw_session = arbiter.sessions.get(zid)
        memo = dict(raw_session) if raw_session is not None else {}
        pre_revision = _session_cache_revision(raw_session)
    restored, headers, rows = recovery_storage_rows(arbiter, adapter, zid, memo)
    mapping = load_anki_mapping(arbiter.resolved_paths['anki_mapping_file'])
    roles = get_role_fields(mapping, headers)
    rows = recovery_row_order(rows, headers, roles, memo)
    snapshot = dict(session_zid=zid, rows=recovery_update_rows(restored, rows, headers, roles),
                fingerprint=compute_content_fingerprint(rows),
                translatedText=restored.get('sentence_translation', memo.get('translatedText', '')),
                sentences=restored.get('sentences', []))
    with arbiter._lock:
        current = arbiter.sessions.get(zid)
        if pre_revision is not None and _session_cache_revision(current) == pre_revision:
            current.update(headers=headers, data_rows=rows,
                fingerprint=snapshot['fingerprint'], translatedText=snapshot['translatedText'])
    return snapshot

ERROR_STATUS_MATRIX = {
    "TIMEOUT": 504,
    "INTERRUPTED": 409,
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

_DRAFT_SESSIONS: Dict[str, dict] = {}
_DRAFT_SESSIONS_LOCK = threading.Lock()
_RENDERED_SESSION_ALIASES: Dict[str, str] = {}
_RENDERED_SESSION_ALIASES_LOCK = threading.Lock()
_MAX_RENDERED_SESSION_ALIASES = 256


def _resolve_rendered_session_zid(html_result: Any, fallback_zid: str) -> str:
    if not isinstance(html_result, str) or not html_result:
        return fallback_zid
    for island_id in ("kardenwort-bootstrap", "session-data"):
        match = re.search(
            rf'<script[^>]*\bid="{island_id}"[^>]*>(.*?)</script>',
            html_result,
            re.S,
        )
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except Exception:
            continue
        if isinstance(payload, dict):
            resolved = str(payload.get("session_zid") or payload.get("zid") or "").strip()
            if resolved:
                return resolved
    return fallback_zid


def _remember_session_alias(requested_zid: Optional[str], resolved_zid: Optional[str]) -> None:
    req = str(requested_zid or "").strip()
    res = str(resolved_zid or "").strip()
    if not req or not res or req == res:
        return
    with _RENDERED_SESSION_ALIASES_LOCK:
        _RENDERED_SESSION_ALIASES[req] = res
        while len(_RENDERED_SESSION_ALIASES) > _MAX_RENDERED_SESSION_ALIASES:
            oldest = next(iter(_RENDERED_SESSION_ALIASES))
            del _RENDERED_SESSION_ALIASES[oldest]


def _lookup_session_alias(requested_zid: Optional[str]) -> Optional[str]:
    req = str(requested_zid or "").strip()
    if not req:
        return None
    with _RENDERED_SESSION_ALIASES_LOCK:
        return _RENDERED_SESSION_ALIASES.get(req)


# ---------------------------------------------------------------------------
# Windows Job Object Helper for Process Supervision
# ---------------------------------------------------------------------------
class WindowsJobObject:
    """
    Manages a Windows Job Object configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
    When the parent process terminates or closes the job handle, Windows kernel
    automatically and cleanly terminates all child processes in the job.
    """

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    def __init__(self):
        self.job_handle = None
        self._init_job()

    def _init_job(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            import ctypes.wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ('ReadOperationCount', ctypes.c_uint64),
                    ('WriteOperationCount', ctypes.c_uint64),
                    ('OtherOperationCount', ctypes.c_uint64),
                    ('ReadTransferCount', ctypes.c_uint64),
                    ('WriteTransferCount', ctypes.c_uint64),
                    ('OtherTransferCount', ctypes.c_uint64),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('PerProcessUserTimeLimit', ctypes.c_int64),
                    ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', ctypes.wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', ctypes.wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', ctypes.wintypes.DWORD),
                    ('SchedulingClass', ctypes.wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryLimit', ctypes.c_size_t),
                    ('PeakJobMemoryLimit', ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            self.job_handle = kernel32.CreateJobObjectW(None, None)
            if not self.job_handle:
                err = ctypes.get_last_error()
                logger.warning(f"Failed to create Windows Job Object: error {err}")
                return

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            success = kernel32.SetInformationJobObject(
                self.job_handle,
                self.JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info)
            )
            if not success:
                err = ctypes.get_last_error()
                logger.warning(f"Failed to set Job Object information: error {err}")
                kernel32.CloseHandle(self.job_handle)
                self.job_handle = None
            else:
                logger.info("Windows Job Object initialized with KILL_ON_JOB_CLOSE")
        except Exception as e:
            logger.warning(f"Error setting up Windows Job Object: {e}")
            self.job_handle = None

    def assign_process(self, proc: subprocess.Popen) -> bool:
        if sys.platform != "win32" or not self.job_handle or not proc:
            return False
        try:
            import ctypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            handle = getattr(proc, '_handle', None)
            if not handle and hasattr(proc, 'pid'):
                PROCESS_SET_QUOTA = 0x0100
                PROCESS_TERMINATE = 0x0001
                handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
            if handle:
                res = kernel32.AssignProcessToJobObject(self.job_handle, handle)
                if not res:
                    err = ctypes.get_last_error()
                    logger.warning(f"Failed to assign PID {proc.pid} to Job Object: error {err}")
                    return False
                return True
        except Exception as e:
            logger.warning(f"Exception assigning process to Job Object: {e}")
        return False

    def close(self):
        if sys.platform != "win32" or not self.job_handle:
            return
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        if not kernel32.CloseHandle(self.job_handle):
            raise ctypes.WinError(ctypes.get_last_error())
        self.job_handle = None


# ---------------------------------------------------------------------------
# Sidecar Process Supervisor
# ---------------------------------------------------------------------------
class SidecarService:
    def __init__(self, name: str, port: int, launch_cmd: List[str], cwd: Optional[Path] = None, health_path: str = "/health", host: str = "127.0.0.1"):
        self.name = name
        self.host = host
        self.port = port
        self.launch_cmd = launch_cmd
        self.cwd = cwd
        self.health_path = health_path
        self.process: Optional[subprocess.Popen] = None
        self.is_healthy: bool = False
        self.last_check: float = 0.0
        self.restart_count: int = 0
        self.managed_by_supervisor: bool = False
        self.consecutive_failures: int = 0
        self.spawn_time: float = 0.0
        self.health_data: Optional[Dict[str, Any]] = None

    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class ProcessSupervisor:
    """
    Supervises background HTTP microservices (SpaCy 8081, Translation 8082, IntelliFiller 8083).
    Performs periodic health probes, handles restarts, and attaches child processes to Windows Job Object.
    """

    def __init__(self, config: Any, resolved_paths: Dict[str, Any], enabled: bool = True):
        self.config = config
        self.resolved_paths = resolved_paths
        self.enabled = enabled
        self.job = WindowsJobObject()
        self.services: Dict[str, SidecarService] = {}
        self._monitor_stop = threading.Event()
        self._monitor_error = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        try:
            self._setup_service_definitions()
        except BaseException as error:
            errors = run_cleanup([self.job.close])
            if errors:
                raise BaseExceptionGroup('Supervisor initialization failed', [error, *errors])
            raise

    def _setup_service_definitions(self):
        desk_dir = Path(__file__).resolve().parent
        workspace_parent = desk_dir.parent

        # 1. SpaCy Linguistic Server (Port 8081)
        spacy_url_str = ""
        if self.config:
            try:
                spacy_url_str = self.config.get(SEC_SERVICES, 'spacy_server_url', fallback='') or ''
            except Exception:
                spacy_url_str = ''
        parsed_spacy = urllib.parse.urlparse(spacy_url_str) if spacy_url_str else None
        spacy_host = (parsed_spacy.hostname if parsed_spacy and parsed_spacy.hostname else "127.0.0.1")
        spacy_port = (parsed_spacy.port if parsed_spacy and parsed_spacy.port else 8081)

        spacy_python = self.resolved_paths.get('kardenwort_python', sys.executable)
        kardenwort_ws = self.resolved_paths.get('kardenwort_workspace', workspace_parent / "20241223170748-kardenwort")
        spacy_script = Path(kardenwort_ws) / "src" / "kardenwort" / "server" / "spacy_server.py"
        if not spacy_script.exists():
            spacy_cmd = [str(spacy_python), "-m", "kardenwort.server.spacy_server", "--port", str(spacy_port)]
        else:
            spacy_cmd = [str(spacy_python), str(spacy_script), "--port", str(spacy_port)]

        spacy_ttl = ""
        if self.config:
            try:
                spacy_ttl = self.config.get(SEC_SERVICES, 'spacy_model_idle_ttl', fallback='') or ''
            except Exception:
                spacy_ttl = ''
        if spacy_ttl and str(spacy_ttl).strip() and str(spacy_ttl).strip() != '0':
            spacy_cmd.extend(["--model-ttl", str(spacy_ttl).strip()])

        self.services["spacy"] = SidecarService(
            name="spacy",
            port=spacy_port,
            launch_cmd=spacy_cmd,
            cwd=Path(kardenwort_ws) if Path(kardenwort_ws).exists() else desk_dir,
            health_path="/health",
            host=spacy_host
        )

        # 2. Translation Server (Port 8082)
        trans_url_str = ""
        if self.config:
            try:
                trans_url_str = self.config.get(SEC_SERVICES, 'translation_server_url', fallback='') or ''
            except Exception:
                trans_url_str = ''
        parsed_trans = urllib.parse.urlparse(trans_url_str) if trans_url_str else None
        trans_host = (parsed_trans.hostname if parsed_trans and parsed_trans.hostname else "127.0.0.1")
        trans_port = (parsed_trans.port if parsed_trans and parsed_trans.port else 8082)

        trans_python = self.resolved_paths.get('deep_translator_python', sys.executable)
        trans_ws = workspace_parent / "20241122093311-deep-translator"
        trans_script = trans_ws / "translate_server.py"
        if not trans_script.exists():
            trans_fork = workspace_parent / "20260209094544-deep-translator" / "translate_server.py"
            if trans_fork.exists():
                trans_script = trans_fork

        trans_cmd = [str(trans_python), str(trans_script), "--port", str(trans_port)]
        if self.config:
            try:
                g_conc = self.config.get(SEC_TRANSLATION, 'google_max_concurrency', fallback=None)
                if g_conc is not None and str(g_conc).strip():
                    trans_cmd.extend(["--google-concurrency", str(g_conc).strip()])
            except Exception:
                pass

            try:
                g_delay = self.config.get(SEC_TRANSLATION, 'google_request_delay', fallback=None)
                if g_delay is not None and str(g_delay).strip():
                    trans_cmd.extend(["--google-delay", str(g_delay).strip()])
            except Exception:
                pass

            try:
                if hasattr(self.config, 'getboolean'):
                    enable_cache = self.config.getboolean(SEC_TRANSLATION, 'enable_translation_cache', fallback=True)
                else:
                    enable_cache = True
                if not enable_cache:
                    trans_cmd.append("--no-cache")
            except Exception:
                pass

            try:
                cache_sz = self.config.get(SEC_TRANSLATION, 'cache_size', fallback=None)
                if cache_sz is not None and str(cache_sz).strip():
                    trans_cmd.extend(["--cache-size", str(cache_sz).strip()])
            except Exception:
                pass

            try:
                if hasattr(self.config, 'getboolean'):
                    auto_fail = self.config.getboolean(SEC_TRANSLATION, 'auto_provider_failover', fallback=True)
                else:
                    auto_fail = True
                if auto_fail:
                    trans_cmd.append("--auto-failover")
            except Exception:
                pass

            try:
                if hasattr(self.config, 'getboolean'):
                    warmup_argos = self.config.getboolean(SEC_TRANSLATION, 'warmup_argos', fallback=True)
                else:
                    warmup_argos = True
                if warmup_argos:
                    trans_cmd.append("--warmup-argos")
                else:
                    trans_cmd.append("--no-warmup-argos")
            except Exception:
                pass

            try:
                argos_conc = self.config.get(SEC_TRANSLATION, 'argos_concurrency', fallback='2')
                if argos_conc is not None and str(argos_conc).strip():
                    trans_cmd.extend(["--argos-concurrency", str(argos_conc).strip()])
            except Exception:
                pass

        self.services["translation"] = SidecarService(
            name="translation",
            port=trans_port,
            launch_cmd=trans_cmd,
            cwd=trans_script.parent if trans_script.exists() else desk_dir,
            health_path="/health",
            host=trans_host
        )

        # 3. IntelliFiller Server (Port 8083)
        intelli_url_str = ""
        if self.config:
            try:
                intelli_url_str = self.config.get(SEC_SERVICES, 'intellifiller_server_url', fallback='') or ''
            except Exception:
                intelli_url_str = ''
        parsed_intelli = urllib.parse.urlparse(intelli_url_str) if intelli_url_str else None
        intelli_host = (parsed_intelli.hostname if parsed_intelli and parsed_intelli.hostname else "127.0.0.1")
        intelli_port = (parsed_intelli.port if parsed_intelli and parsed_intelli.port else 8083)
        intelli_python = (
            self.resolved_paths.get('intellifiller_python')
            or self.resolved_paths.get('kardenwort_python')
            or sys.executable
        )
        intelli_script = self.resolved_paths.get('intellifiller_headless')
        if not intelli_script or not Path(intelli_script).exists():
            intelli_ws = workspace_parent / "20251206123938-intellifiller-ai-addon-for-anki"
            intelli_script = intelli_ws / "IntelliFiller" / "headless_entrypoint.py"
            if not intelli_script.exists():
                intelli_script = intelli_ws / "headless_entrypoint.py"
        else:
            intelli_script = Path(intelli_script)

        intelli_cmd = [str(intelli_python), str(intelli_script), "--serve", "--port", str(intelli_port)]
        self.services["intellifiller"] = SidecarService(
            name="intellifiller",
            port=intelli_port,
            launch_cmd=intelli_cmd,
            cwd=intelli_script.parent.parent if intelli_script.exists() else desk_dir,
            health_path="/health",
            host=intelli_host
        )

    def probe_health(self, service: SidecarService, timeout: float = 1.0) -> bool:
        url = f"{service.url()}{service.health_path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Kardenwort-Supervisor/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    try:
                        service.health_data = json.loads(resp.read().decode('utf-8'))
                    except Exception:
                        service.health_data = {}
                    service.is_healthy = True
                    service.last_check = time.time()
                    return True
        except Exception:
            pass
        service.is_healthy = False
        service.health_data = None
        service.last_check = time.time()
        return False

    def spawn_service(self, service: SidecarService) -> bool:
        with self._lock:
            if self._monitor_stop.is_set():
                return False
            if self.probe_health(service, timeout=0.5):
                return self._reuse_healthy_service(service)
            if service.managed_by_supervisor and service.process is not None:
                stop_owned_process(service.process)
            return self._launch_service(service)

    def _reuse_healthy_service(self, service):
        service.managed_by_supervisor = bool(service.managed_by_supervisor and service.process is not None
                                             and service.process.poll() is None)
        owner = 'managed' if service.managed_by_supervisor else 'external'
        logger.info(f"Sidecar '{service.name}' on port {service.port} is already running ({owner}).")
        return True

    @staticmethod
    def _service_script_available(cmd):
        script = Path(cmd[1]) if len(cmd) > 1 else None
        return not script or script.exists() or (len(cmd) > 2 and cmd[1] == '-m')

    def _launch_service(self, service):
        if not self._service_script_available(service.launch_cmd):
            logger.warning(f"Cannot launch sidecar '{service.name}': script does not exist.")
            return False
        try:
            self._create_service_process(service)
            return self._await_service_health(service)
        except Exception as error:
            logger.error(f"Failed to spawn sidecar '{service.name}': {error}")
            return False

    def _create_service_process(self, service):
        creationflags = 0x08000000 | 0x00000200 if sys.platform == 'win32' else 0
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        proc = subprocess.Popen(service.launch_cmd,
            cwd=str(service.cwd) if service.cwd else None,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags, close_fds=True, env=env)
        service.process = proc
        service.managed_by_supervisor = True
        service.spawn_time = time.time()
        service.consecutive_failures = 0
        self.job.assign_process(proc)
        logger.info(f"Spawned sidecar '{service.name}' (PID: {proc.pid}) on port {service.port}")

    def _await_service_health(self, service):
        deadline = time.monotonic() + 15.0
        while not self._monitor_stop.is_set() and time.monotonic() < deadline:
            if service.process.poll() is not None:
                logger.warning(f"Sidecar '{service.name}' exited with code {service.process.returncode}.")
                return False
            if self.probe_health(service, timeout=0.5):
                logger.info(f"Sidecar '{service.name}' is healthy on port {service.port}")
                return True
            self._monitor_stop.wait(0.3)
        if not self._monitor_stop.is_set():
            logger.warning(f"Sidecar '{service.name}' health probe did not respond within 15s.")
        return False

    def start(self):
        if not self.enabled:
            logger.info("Process supervisor is disabled.")
            return

        self._monitor_stop.clear()
        logger.info("Starting ProcessSupervisor sidecar services...")
        for svc in self.services.values():
            self.spawn_service(svc)

        self._start_monitor()

    def _start_monitor(self):
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            raise RuntimeError('Supervisor watchdog is already running')
        self._monitor_stop.clear()
        self._monitor_error = None
        self._monitor_thread = threading.Thread(target=self._supervision_loop, name="SupervisorWatchdog", daemon=False)
        self._monitor_thread.start()

    def _supervision_loop(self):
        try:
            while not self._monitor_stop.wait(5.0):
                for svc in list(self.services.values()):
                    if self._monitor_stop.is_set():
                        return
                    self._supervise_service(svc)
        except BaseException as error:
            self._monitor_error = error

    def _supervise_service(self, svc):
        if self.probe_health(svc, timeout=1.5):
            svc.consecutive_failures = 0
            return
        if not svc.managed_by_supervisor:
            return
        if svc.process is None or svc.process.poll() is not None:
            exit_code = getattr(svc.process, 'returncode', None)
            logger.warning(f"Managed sidecar '{svc.name}' process exited (exit code: {exit_code}). Restarting...")
            svc.restart_count += 1
            svc.consecutive_failures = 0
            self.spawn_service(svc)
            return
        if time.time() - getattr(svc, 'spawn_time', 0.0) < 20.0:
            return
        svc.consecutive_failures = getattr(svc, 'consecutive_failures', 0) + 1
        if svc.consecutive_failures >= 3:
            logger.warning(f"Health probe failed {svc.consecutive_failures} consecutive times for managed sidecar '{svc.name}' on port {svc.port}. Restarting...")
            svc.restart_count += 1
            svc.consecutive_failures = 0
            self.spawn_service(svc)

    def _join_monitor(self):
        if self._monitor_thread is not None:
            self._monitor_thread.join(5)
            if self._monitor_thread.is_alive():
                raise TimeoutError('Supervisor watchdog did not terminate')
        if self._monitor_error is not None:
            raise self._monitor_error

    def _stop_services(self):
        with self._lock:
            processes = [svc.process for svc in self.services.values()
                         if svc.managed_by_supervisor and svc.process is not None]
        errors = run_cleanup([lambda process=process: stop_owned_process(process) for process in processes])
        if errors:
            raise BaseExceptionGroup('Managed sidecar disposal failed', errors)

    def stop(self):
        self._monitor_stop.set()
        logger.info("Stopping ProcessSupervisor and terminating managed sidecars...")
        errors = run_cleanup([self._join_monitor, self._stop_services, self.job.close])
        if errors:
            raise BaseExceptionGroup('Supervisor disposal failed', errors)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def get_status_report(self) -> Dict[str, Any]:
        report = {}
        for name, svc in self.services.items():
            pid = svc.process.pid if svc.process and svc.process.poll() is None else None
            entry = {
                "port": svc.port,
                "healthy": svc.is_healthy,
                "pid": pid,
                "managed": svc.managed_by_supervisor,
                "restart_count": svc.restart_count,
                "last_check": svc.last_check
            }
            if name == "translation":
                argos_status = None
                if svc.health_data and isinstance(svc.health_data, dict):
                    providers = svc.health_data.get("providers", {})
                    if isinstance(providers, dict):
                        argos_status = providers.get("argos")
                if argos_status:
                    entry["argos"] = argos_status
                elif svc.is_healthy:
                    entry["argos"] = "available"
            report[name] = entry
        return report

    def get_service_status(self) -> Dict[str, Any]:
        """
        Returns a structured dictionary of sidecar health and ports.
        """
        status = {}
        for name, svc in self.services.items():
            entry = {
                "port": svc.port,
                "healthy": svc.is_healthy,
                "managed": svc.managed_by_supervisor,
            }
            if name == "translation":
                argos_status = None
                if svc.health_data and isinstance(svc.health_data, dict):
                    providers = svc.health_data.get("providers", {})
                    if isinstance(providers, dict):
                        argos_status = providers.get("argos")
                if argos_status:
                    entry["argos"] = argos_status
                elif svc.is_healthy:
                    entry["argos"] = "available"
            status[name] = entry
        return status


# ---------------------------------------------------------------------------
# Centralized Controller Enrichment Task Queue
# ---------------------------------------------------------------------------
class EnrichmentQueue:
    """
    Centralized controller task queue with bounded worker concurrency,
    in-memory lemma deduplication cache, rate-limiting pacing, and in-flight request coalescing.
    """

    config = OperationConfiguration()

    def __init__(
        self,
        config: Any,
        resolved_paths: Dict[str, Any],
        max_workers: Optional[int] = None,
        translation_max_workers: Optional[int] = None,
    ):
        self.config = config
        self.resolved_paths = resolved_paths
        if max_workers is not None:
            self.max_workers = max(1, int(max_workers))
        elif config and hasattr(config, "getint"):
            self.max_workers = config.getint("intellifiller", "max_workers", fallback=1)
        else:
            self.max_workers = 1

        if translation_max_workers is not None:
            self.translation_max_workers = max(1, int(translation_max_workers))
        elif config and hasattr(config, "getint"):
            self.translation_max_workers = config.getint("translation", "google_max_concurrency", fallback=2)
        else:
            self.translation_max_workers = 2

        # Rate limiting pacing delay (seconds)
        if config and hasattr(config, "getfloat"):
            self.translation_delay = config.getfloat("translation", "google_request_delay", fallback=0.0)
        else:
            self.translation_delay = 0.0

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="EnrichmentWorker",
        )
        self._translation_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.translation_max_workers,
            thread_name_prefix="TranslationWorker",
        )
        self._progressive_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="ProgressiveWorker",
        )
        self._cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._inflight_lemmas: Dict[Tuple[str, str], concurrent.futures.Future] = {}
        self._lemma_trans_cache: Dict[Tuple[str, str, str], str] = {}
        self._inflight_trans: Dict[Tuple[str, str, str], concurrent.futures.Future] = {}
        self._active_progressive_sessions: Dict[str, concurrent.futures.Future] = {}
        self._progressive_generations: Dict[str, int] = {}
        self._unobserved_progressive_failures: List[BaseException] = []
        self._progressive_failures = self._unobserved_progressive_failures
        self._acknowledged_failures = deque(maxlen=64)
        self._shutting_down = False
        self._lock = threading.Lock()
        self._pacing_lock = threading.Lock()
        self._last_request_time = 0.0

    def get_cached(self, lemma: str, language: str = "de") -> Optional[Dict[str, Any]]:
        norm_lemma = lemma.strip()
        norm_lang = (language or "de").strip()
        with self._lock:
            cached = self._cache.get((norm_lemma, norm_lang))
            return dict(cached) if cached is not None else None

    def set_cached(self, lemma: str, language: str, result: Dict[str, Any]):
        norm_lemma = lemma.strip()
        norm_lang = (language or "de").strip()
        with self._lock:
            self._cache[(norm_lemma, norm_lang)] = dict(result)

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self._lemma_trans_cache.clear()

    def get_cached_translation(self, lemma: str, source_lang: str = "de", target_lang: str = "ru") -> Optional[str]:
        norm_lemma = (lemma or "").strip()
        norm_src = (source_lang or "de").strip()
        norm_tgt = (target_lang or "ru").strip()
        with self._lock:
            return self._lemma_trans_cache.get((norm_lemma, norm_src, norm_tgt))

    def set_cached_translation(self, lemma: str, source_lang: str, target_lang: str, translation: str):
        norm_lemma = (lemma or "").strip()
        norm_src = (source_lang or "de").strip()
        norm_tgt = (target_lang or "ru").strip()
        with self._lock:
            self._lemma_trans_cache[(norm_lemma, norm_src, norm_tgt)] = translation

    def translate_lemma(
        self,
        lemma: str,
        source_lang: str = "de",
        target_lang: str = "ru",
        provider: Optional[str] = None,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> str:
        norm_lemma = (lemma or "").strip()
        norm_src = (source_lang or "de").strip()
        norm_tgt = (target_lang or "ru").strip()
        if not norm_lemma:
            return ""

        key = (norm_lemma, norm_src, norm_tgt)
        fut = None
        with self._lock:
            if key in self._lemma_trans_cache:
                return self._lemma_trans_cache[key]
            if key in self._inflight_trans:
                fut = self._inflight_trans[key]
            else:
                fut = self._translation_executor.submit(
                    self._execute_translate_lemma,
                    norm_lemma,
                    norm_src,
                    norm_tgt,
                    provider,
                    zid,
                    trace_id,
                )
                self._inflight_trans[key] = fut

        try:
            res = fut.result()
            if isinstance(res, str) and res:
                with self._lock:
                    self._lemma_trans_cache[key] = res
            return res if isinstance(res, str) else ""
        finally:
            with self._lock:
                if self._inflight_trans.get(key) is fut:
                    del self._inflight_trans[key]

    @operation_preferences
    def _execute_translate_lemma(
        self,
        lemma: str,
        source_lang: str = "de",
        target_lang: str = "ru",
        provider: Optional[str] = None,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> str:
        norm_lemma = (lemma or "").strip()
        norm_src = (source_lang or "de").strip()
        norm_tgt = (target_lang or "ru").strip()
        if self.translation_delay > 0:
            with self._pacing_lock:
                now = time.time()
                elapsed = now - self._last_request_time
                if elapsed < self.translation_delay:
                    time.sleep(self.translation_delay - elapsed)
                self._last_request_time = time.time()

        lemma_chain, _ = resolve_provider_chain(self.config, task_type='lemma') if self.config else (['google'], 'chain')
        prov = provider or (lemma_chain[0] if lemma_chain else 'google')
        res_dict = translate_lemmas_fast_path(
            [norm_lemma],
            norm_src,
            norm_tgt,
            self.config,
            self.resolved_paths,
            prov,
        )
        return res_dict.get(norm_lemma, "")

    def translate_lemmas_coalesced(
        self,
        lemmas: List[str],
        source_lang: str = "de",
        target_lang: str = "ru",
        provider: Optional[str] = None,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, str]:
        results: Dict[str, str] = {}
        missing_lemmas = []
        for l in lemmas:
            norm_l = (l or "").strip()
            if not norm_l:
                continue
            cached = self.get_cached_translation(norm_l, source_lang, target_lang)
            if cached is not None:
                results[norm_l] = cached
            else:
                missing_lemmas.append(norm_l)

        if not missing_lemmas:
            return results

        futures = {}
        for l in missing_lemmas:
            key = (l, source_lang, target_lang)
            with self._lock:
                if key in self._lemma_trans_cache:
                    results[l] = self._lemma_trans_cache[key]
                    continue
                if key in self._inflight_trans:
                    futures[l] = self._inflight_trans[key]
                else:
                    fut = self._translation_executor.submit(
                        self._execute_translate_lemma,
                        l,
                        source_lang,
                        target_lang,
                        provider,
                        zid,
                        trace_id,
                    )
                    self._inflight_trans[key] = fut
                    futures[l] = fut

        for l, fut in futures.items():
            try:
                res = fut.result()
                if isinstance(res, str) and res:
                    results[l] = res
                    self.set_cached_translation(l, source_lang, target_lang, res)
            except Exception as e:
                logger.warning(f"Coalesced translation for lemma '{l}' failed: {e}")
            finally:
                key = (l, source_lang, target_lang)
                with self._lock:
                    if self._inflight_trans.get(key) is fut:
                        del self._inflight_trans[key]

        return results

    def enrich_lemma(
        self,
        lemma: str,
        language: str = "de",
        prompt_name: str = "",
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        norm_lemma = lemma.strip()
        norm_lang = (language or "de").strip()
        if not norm_lemma:
            return {}

        key = (norm_lemma, norm_lang)
        fut = None
        with self._lock:
            if key in self._cache:
                return dict(self._cache[key])
            if key in self._inflight_lemmas:
                fut = self._inflight_lemmas[key]
            else:
                fut = self._executor.submit(
                    self._execute_enrich_lemma,
                    norm_lemma,
                    norm_lang,
                    prompt_name,
                    zid,
                    trace_id,
                )
                self._inflight_lemmas[key] = fut

        try:
            res = fut.result()
            if isinstance(res, dict) and res:
                with self._lock:
                    self._cache[key] = dict(res)
            return dict(res) if isinstance(res, dict) else {}
        finally:
            with self._lock:
                if self._inflight_lemmas.get(key) is fut:
                    del self._inflight_lemmas[key]

    @operation_preferences
    def _execute_enrich_lemma(self, lemma, language='de', prompt_name='', zid=None, trace_id=None):
        if storage_backend_setting(self.config, self.resolved_paths) != 'sqlite':
            return self._execute_legacy_enrich_lemma(lemma, language, prompt_name, zid, trace_id)
        from kardenwort_enrichment import request_batch
        prompt = prompt_name or self.config.get(SEC_LANGUAGES, f'{language}_prompt', fallback='')
        batch = [dict(row_id=0, WordSource=lemma.strip())]
        try:
            result = request_batch(batch, prompt, language, self.config,
                                   query_intellifiller_server, zid, trace_id)[0]
        except ValueError as exc:
            raise StructuredError(ErrorCode.DESK_FAILED, str(exc)) from exc
        with self._lock:
            self._cache[(lemma.strip(), language)] = dict(result)
        return result

    def _execute_legacy_enrich_lemma(
        self,
        lemma: str,
        language: str = "de",
        prompt_name: str = "",
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        norm_lemma = lemma.strip()
        lang = language or "de"
        prompt = prompt_name or (self.config.get(SEC_LANGUAGES, f"{lang}_prompt", fallback="") if self.config else "")

        # 1. Try HTTP microservice if configured
        intellifiller_url = None
        if self.config:
            if self.config.has_section(SEC_SERVICES):
                intellifiller_url = self.config.get(SEC_SERVICES, 'intellifiller_server_url', fallback=None)
            elif self.config.has_section('services'):
                intellifiller_url = self.config.get('services', 'intellifiller_server_url', fallback=None)

        model = None
        base_url = None
        api_key = None
        temperature = None
        prompt_template = None
        timeout = 120
        if self.config and self.config.has_section("intellifiller"):
            model = self.config.get("intellifiller", "model", fallback=None)
            base_url = self.config.get("intellifiller", "base_url", fallback=None)
            api_key = self.config.get("intellifiller", "api_key", fallback=None)
            temperature = self.config.get("intellifiller", "temperature", fallback=None)
            prompt_template = self.config.get("intellifiller", "prompt_template", fallback=None)
        if self.config and hasattr(self.config, 'getint'):
            timeout = self.config.getint(SEC_TIMEOUTS, 'intellifiller_timeout', fallback=120)

        temp_val = float(temperature) if (temperature is not None and str(temperature).strip()) else None

        if intellifiller_url:
            try:
                batch_rows = [{"row_id": 0, "WordSource": norm_lemma, "WordDestination": "", "WordSourceIPA": "", "WordSourceMorphologyAI": "", "WordSourcePOS": "", "WordSourceGender": ""}]
                resp = query_intellifiller_server(
                    rows=batch_rows,
                    prompt=prompt,
                    language=lang,
                    server_url=intellifiller_url,
                    zid=zid,
                    trace_id=trace_id,
                    timeout=float(timeout),
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    temperature=temp_val,
                    prompt_template=prompt_template,
                )
                if resp and resp.get("status") == "success":
                    enriched = resp.get("enriched_rows", [])
                    if enriched:
                        item = enriched[0]
                        res = {}
                        for k in ("WordDestination", "WordSourceIPA", "WordSourceMorphologyAI", "WordSourcePOS", "WordSourceGender"):
                            if k in item and item[k]:
                                res[k] = str(item[k]).strip()
                        with self._lock:
                            self._cache[(norm_lemma, lang)] = res
                        return res
            except Exception as e:
                logger.warning(f"IntelliFiller HTTP query in EnrichmentQueue failed: {e}")

        # 2. Fallback to headless runner via ephemeral TSV
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_tsv = Path(temp_dir) / f"{zid or 'enrich'}-ephemeral.tsv"
            headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI", "WordSourcePOS", "WordSourceGender"]
            rows = [[norm_lemma, "", "", "", "", ""]]
            save_tsv_rows_safely(temp_tsv, [f"# language={lang}"], headers, rows)

            success = run_headless_intellifiller(
                tsv_path=temp_tsv,
                prompt_name=prompt,
                config=self.config,
                resolved_paths=self.resolved_paths,
                selected_rows=[0],
                reprocess=True,
                zid=zid,
                trace_id=trace_id,
            )

            if success and temp_tsv.exists():
                _, updated_headers, updated_rows = load_tsv_rows(temp_tsv)
                if updated_rows:
                    urow = updated_rows[0]
                    res = {}
                    for field in ("WordDestination", "WordSourceIPA", "WordSourceMorphologyAI", "WordSourcePOS", "WordSourceGender"):
                        if field in updated_headers:
                            c_idx = updated_headers.index(field)
                            if c_idx < len(urow) and urow[c_idx].strip():
                                res[field] = urow[c_idx].strip()
                    with self._lock:
                        self._cache[(norm_lemma, lang)] = res
                    return res

        return {}

    def enqueue_progressive_task(
        self,
        session_zid: str,
        arbiter: Any,
        language: str = "de",
        target_lang: str = "ru",
        text_mode: str = "single",
        prompt_name: str = "",
        skip_intellifiller: bool = False,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("Controller enrichment queue has been shut down")
            if session_zid in self._active_progressive_sessions:
                existing_fut = self._active_progressive_sessions[session_zid]
                if not existing_fut.done():
                    return {"status": "in_progress", "session_zid": session_zid}

            generation = self._progressive_generations.get(session_zid, 0) + 1
            self._progressive_generations[session_zid] = generation
            fut = self._progressive_executor.submit(
                self._execute_progressive_task,
                session_zid,
                arbiter,
                language,
                target_lang,
                text_mode,
                prompt_name,
                skip_intellifiller,
                zid,
                trace_id,
                generation,
            )
            self._active_progressive_sessions[session_zid] = fut
        fut.add_done_callback(lambda completed: self._finish_progressive_task(session_zid, completed))
        return {"status": "queued", "session_zid": session_zid}

    def _is_failure_tracked_locked(self, error: BaseException) -> bool:
        return any(existing is error for existing in self._unobserved_progressive_failures) or any(
            existing is error for existing in self._acknowledged_failures
        )

    def _finish_progressive_task(self, session_zid, future):
        error = None if future.cancelled() else future.exception()
        with self._lock:
            if error is not None and not self._is_failure_tracked_locked(error):
                self._unobserved_progressive_failures.append(error)
            if self._active_progressive_sessions.get(session_zid) is future:
                del self._active_progressive_sessions[session_zid]

    def _collect_wordfill_row_updates(self, row, headers, role_fields, match):
        patch = progressive.wordfill_patch(
            row, headers, role_fields, match, is_failure=is_stored_translation_failure
        )
        for field, index, value in patch:
            self._apply_wordfill_cell(row, index, value)
        return {field: value for field, _, value in patch}

    @staticmethod
    def _apply_wordfill_cell(row, index, value):
        if index < 0:
            return
        row.extend([''] * max(0, index + 1 - len(row)))
        row[index] = value

    def _wordfill_row(self, row, headers, roles, lemma_column, translation_column, language, config, path):
        if not progressive.eligible_wordfill(
            row, lemma_column, translation_column, is_unfinished=is_unfinished_translation
        ):
            return {}
        match = find_wordfill_match(row[lemma_column].strip(), language, config, exclude_path=path)
        if not match:
            return {}
        updates = self._collect_wordfill_row_updates(row, headers, roles, match)
        apply_wordfill_to_rows([row], headers, match)
        return updates

    def _wordfill_updates(self, rows, headers, roles, columns, language, config, path, keys):
        updates = []
        for index, row in enumerate(rows):
            values = self._wordfill_row(row, headers, roles, *columns, language, config, path)
            if values:
                sentence, token = keys[index]
                updates.append(dict(sentence_index=sentence, token_order=token, updates=values))
        return updates

    def _run_wordfill_stage(self, arbiter, storage_adapter, session_zid, req_zid, is_sqlite,
                            data_rows, headers, role_fields, occurrence_keys, col_lemma,
                            col_word_dest, sess_lang, tsv_path, comments):
        config = getattr(arbiter, 'wordfill_cfg', None) or resolve_wordfill_config(self.config, self.resolved_paths)
        if not (config and config.get('enabled', False)):
            return
        keys = occurrence_keys if is_sqlite else [(0, index) for index in range(len(data_rows))]
        updates = self._wordfill_updates(data_rows, headers, role_fields, (col_lemma, col_word_dest),
                                         sess_lang, config, tsv_path, keys)
        if not updates:
            return
        self._persist_wordfill_updates(storage_adapter, session_zid, req_zid, is_sqlite,
                                       tsv_path, comments, headers, data_rows, updates)

    @staticmethod
    def _persist_wordfill_updates(adapter, session_zid, zid, sqlite, path, comments, headers, rows, updates):
        if sqlite:
            adapter.batch_update_words(session_zid=session_zid, updates_list=updates, zid=zid)
            return
        with adapter.file_lock(path):
            adapter.save_tsv_rows_safely(path, comments, headers, rows)

    def _is_progressive_superseded(self, session_zid: str, generation: Optional[int]) -> bool:
        if generation is None:
            return False
        with self._lock:
            return self._progressive_generations.get(session_zid) != generation

    def _publish_progressive_final_state(self, arbiter, storage_adapter, session_zid, is_sqlite,
            tsv_path, sess_lang, headers, data_rows, role_fields, mapping, active_text_prov,
            worker_error, eff_trace_id, sess_row_provs, generation):
        request = progressive.CompletionRequest(session_zid, sess_lang, is_sqlite, tsv_path,
            mapping, headers, data_rows, role_fields, active_text_prov, worker_error,
            eff_trace_id, sess_row_provs, generation)
        self._complete_progressive_task(arbiter, storage_adapter, request)

    def _complete_progressive_task(self, arbiter, adapter, request):
        status_error = self._record_completion_status(adapter, request)
        publication_error = None
        try:
            self._publish_current_completion(arbiter, adapter, request)
        except Exception as error:
            publication_error = error
        raise_progressive_finalization_errors(status_error, publication_error)

    def _record_completion_status(self, adapter, request):
        if not request.sqlite:
            return None
        try:
            with adapter.db.get_connection() as connection:
                with self._lock:
                    if request.generation is None or self._progressive_generations.get(request.session_zid) == request.generation:
                        self._persist_completion_status(adapter, request, connection)
        except Exception as error:
            return error
        return None

    def _publish_current_completion(self, arbiter, adapter, request):
        for _ in range(3):
            if self._is_progressive_superseded(request.session_zid, request.generation):
                return
            snapshot = self._completion_snapshot(arbiter, adapter, request)
            outcome = self._accept_completion(arbiter, adapter, request, snapshot)
            if outcome != 'changed':
                return
        self._reject_unstable_completion(adapter, request)

    def _reject_unstable_completion(self, adapter, request):
        error = StructuredError(ErrorCode.DESK_FAILED, 'Completion changed during publication', details=dict(
            committed=True, session_zid=request.session_zid,
            recovery_url=f'/session/status?zid={request.session_zid}&committed=1'))
        status_error = None
        try:
            with adapter.db.get_connection() as connection:
                with self._lock:
                    self._mark_unstable_completion_locked(adapter, request, connection)
        except Exception as failure:
            status_error = failure
        raise_progressive_finalization_errors(status_error, error)

    def _mark_unstable_completion_locked(self, adapter, request, connection):
        if request.generation is None or self._progressive_generations.get(request.session_zid) == request.generation:
            adapter.db.set_worker_status(request.session_zid, 'failed',
                finished_at=datetime.now(timezone.utc).isoformat(), connection=connection)

    def _completion_snapshot(self, arbiter, adapter, request):
        if not request.sqlite:
            return self._legacy_completion_snapshot(arbiter, request)
        restored = self._restore_completion(adapter, request.session_zid)
        if restored is None:
            return self._deleted_completion_snapshot(request)
        return self._sqlite_completion_snapshot(restored, request)

    @staticmethod
    def _restore_completion(adapter, zid):
        try:
            return adapter.restore_session(zid)
        except StructuredError as error:
            if error.error_code != ErrorCode.NOT_FOUND:
                raise
            return None

    @staticmethod
    def _deleted_completion_snapshot(request):
        error = request.error or dict(code='ERR_SESSION_DELETED',
            message=f'Session {request.session_zid} was deleted before completion',
            provider='controller', details={})
        event = progressive.finished_event({}, '', {}, error)
        return progressive.CompletionSnapshot(None, [], [], {}, {}, event)

    def _sqlite_completion_snapshot(self, restored, request):
        headers, rows = reword.restored_manifest_rows(restored)
        roles = get_role_fields(request.mapping, headers) if request.mapping else request.roles
        rows = sort_session_data_rows(rows, headers, request.language, self.config,
            self.resolved_paths, role_fields=roles)
        metadata = restored_occurrence_metadata(restored)
        keys = reword.row_keys(rows, headers, roles.get('sentence_index', 'SentenceSourceIndex'), min_sentence=0)
        provenances = progressive.indexed_provenances(keys, metadata['occurrence_provenances'])
        structured = format_update_rows_dict(rows, headers, roles, row_provenances=provenances, **metadata)
        sentences = progressive.sentence_projection(restored.get('sentences') or [])
        provenance = progressive.text_provenance(sentences, request.text_provenance)
        event = progressive.finished_event(structured, compute_content_fingerprint(rows),
            provenances, request.error, sentences, provenance)
        return progressive.CompletionSnapshot(restored, headers, rows, roles, metadata, event)

    def _legacy_completion_snapshot(self, arbiter, request):
        with arbiter._lock:
            memo = dict(arbiter.sessions.get(request.session_zid, {}))
        provenances = dict(memo.get('row_provenances') or request.provenances)
        rows = sort_session_data_rows([list(row) for row in request.rows], request.headers,
            request.language, self.config, self.resolved_paths, role_fields=request.roles)
        structured = format_update_rows_dict(rows, request.headers, request.roles, row_provenances=provenances)
        event = progressive.finished_event(structured, compute_content_fingerprint(rows), provenances,
            request.error, memo.get('sentences'), request.text_provenance)
        return progressive.CompletionSnapshot({}, request.headers, rows, request.roles, {}, event)

    def _accept_completion(self, arbiter, adapter, request, snapshot):
        if not request.sqlite:
            return self._publish_completion_locked(arbiter, adapter, request, snapshot)
        with adapter.db.get_connection() as connection:
            current = adapter.db.get_session_bundle(request.session_zid, connection=connection)
            if progressive.source_facts(current) != progressive.source_facts(snapshot.restored):
                return 'changed'
            return self._publish_completion_locked(arbiter, adapter, request, snapshot)

    def _publish_completion_locked(self, arbiter, adapter, request, snapshot):
        # Lock order: SQLite writer (when applicable), queue generation, arbiter cache, dispatcher.
        with self._lock:
            if request.generation is not None and self._progressive_generations.get(request.session_zid) != request.generation:
                return 'superseded'
            with arbiter._lock:
                self._finish_completion_locked(arbiter, adapter, request, snapshot)
        return 'published'

    @staticmethod
    def _persist_completion_status(adapter, request, connection):
        active = connection.execute('SELECT 1 FROM sessions WHERE zid = ? AND deleted_at IS NULL',
            (request.session_zid,)).fetchone()
        adapter.db.set_worker_status(request.session_zid,
            'failed' if request.error or active is None else 'finished',
            finished_at=datetime.now(timezone.utc).isoformat(), connection=connection)

    def _finish_completion_locked(self, arbiter, adapter, request, snapshot):
        self._cache_completion_locked(arbiter, request.session_zid, snapshot)
        delivery_error = None
        try:
            self._deliver_completion(arbiter, adapter, request, snapshot)
        except Exception as error:
            delivery_error = progressive_delivery_failure(error, request.session_zid,
                snapshot.event['fingerprint'], snapshot.event['error'] is None)
        else:
            if snapshot.event['error']:
                self._acknowledged_failures.append(dict(snapshot.event['error']))
        raise_progressive_finalization_errors(None, delivery_error)

    @staticmethod
    def _cache_completion_locked(arbiter, zid, snapshot):
        if snapshot.restored is None:
            arbiter.sessions.pop(zid, None)
        elif zid in arbiter.sessions:
            arbiter.sessions[zid].update(progressive.cache_projection(snapshot))

    def _deliver_completion(self, arbiter, adapter, request, snapshot):
        if request.path and snapshot.rows and snapshot.headers:
            self._write_completion_update(adapter, request, snapshot)
        arbiter.emit_event(request.session_zid, snapshot.event)

    def _write_completion_update(self, adapter, request, snapshot):
        event = snapshot.event
        safe_write_update_js(request.path, snapshot.rows, snapshot.headers, snapshot.roles,
            stage='finished', status=event['status'], error=event['error'], zid=request.session_zid,
            trace_id=request.trace_id, text_provenance=event.get('text_provenance'),
            row_provenances=event['row_provenances'], config=self.config,
            resolved_paths=self.resolved_paths, storage_adapter=adapter, **snapshot.metadata)

    def _translate_and_persist_sentences(self, adapter, session_zid, is_sqlite, text, sess_lang,
                                         sess_target, text_mode, provider, req_zid, eff_trace_id, generation):
        try:
            raw = translate_source_text(
                text, sess_lang, sess_target, text_mode, self.config, self.resolved_paths,
                provider, zid=req_zid, trace_id=eff_trace_id,
            )
        except Exception as text_err:
            logger.warning(f"Sentence translation error in progressive queue for {session_zid}: {text_err}")
            if self._is_progressive_superseded(session_zid, generation):
                return None, set(), None, None, True
            stage_err = progressive.sentence_stage_error(text_err, provider=provider)
            partial = getattr(text_err, 'partial_dict', None)
            return self._persist_partial_sentences(adapter, session_zid, is_sqlite, partial, provider, req_zid, stage_err)
        if self._is_progressive_superseded(session_zid, generation):
            return None, set(), None, None, True
        prov = getattr(raw, "provenance", f"live:{provider}")
        if is_sqlite and isinstance(raw, dict):
            committed, persist_err = _persist_sqlite_sentences(adapter, session_zid, raw, prov, req_zid)
            return raw, committed, prov, persist_err, False
        return raw, set(), prov, None, False

    @staticmethod
    def _persist_partial_sentences(adapter, session_zid, is_sqlite, partial, provider, req_zid, stage_err):
        if not isinstance(partial, dict):
            return None, set(), None, stage_err, False
        prov = getattr(partial, 'provenance', f"live:{provider}")
        if not is_sqlite:
            return partial, set(), prov, stage_err, False
        try:
            committed, persist_err = _persist_sqlite_sentences(adapter, session_zid, partial, prov, req_zid)
        except Exception as exc:
            persist_err = progressive.sentence_stage_error(exc, provider='storage', code='ERR_SENTENCE_PERSISTENCE_FAILED')
            committed = set()
        return partial, committed, prov, progressive.combine_worker_errors(stage_err, persist_err), False

    def _read_committed_sqlite_sentences(self, adapter, session_zid, raw, committed_indices, fallback_prov):
        if not hasattr(adapter, 'db'):
            return progressive.committed_entries_summary(raw, committed_indices, fallback_prov)
        db_sents = adapter.db.get_sentences_by_session(session_zid) or []
        return (
            progressive.sentence_summary_rows(db_sents),
            progressive.committed_translation_map(db_sents),
            progressive.text_provenance(db_sents, None),
        )

    def _materialize_sentence_stage(self, adapter, session_zid, is_sqlite, raw, committed_indices,
                                    fallback_prov, text, text_mode, data_rows, col_idx, col_dest,
                                    tsv_path, comments, headers):
        if is_sqlite:
            sentences, committed_map, committed_prov = self._read_committed_sqlite_sentences(
                adapter, session_zid, raw, committed_indices, fallback_prov
            )
            resolve_translations(
                text, text_mode, data_rows, col_idx, col_dest, committed_map,
                tsv_path, comments, headers, persist=False, return_single=False,
            )
            html_out = format_translated_html(committed_map, text_mode=text_mode, text=text, config=self.config) if committed_map else ""
            return sentences, committed_map, committed_prov, html_out, None
        if raw is None:
            return [], {}, None, "", None
        try:
            resolve_translations(
                text, text_mode, data_rows, col_idx, col_dest, raw,
                tsv_path, comments, headers, persist=True, return_single=False,
            )
        except Exception as exc:
            err = progressive.sentence_stage_error(exc, provider='storage', code='ERR_SENTENCE_PERSISTENCE_FAILED')
            return [], {}, None, "", err
        entries = progressive.sentence_entries(raw)
        sentences = [{"sentence_index": i, "sentence_source": "", "sentence_destination": v.strip()} for _, i, v in entries]
        html_out = format_translated_html(raw, text_mode=text_mode, text=text, config=self.config)
        return sentences, raw, fallback_prov, html_out, None

    def _cache_sentence_stage(self, arbiter, session_zid, data_rows, new_fp, committed_map,
                              sentences_list, committed_prov, eff_tp_name, generation):
        with self._lock:
            if generation is not None and self._progressive_generations.get(session_zid) != generation:
                return False
            with arbiter._lock:
                sess = arbiter.sessions.get(session_zid)
                if sess is not None:
                    sess["data_rows"] = data_rows
                    sess["fingerprint"] = new_fp
                    sess["sentences"] = sentences_list
                    sess["sentence_translation"] = committed_map or None
                    sess["text_provenance"] = committed_prov
                    sess["textProvenance"] = committed_prov
                    if committed_map:
                        sess["active_text_provider"] = eff_tp_name
                        sess["active_provider"] = eff_tp_name
        return True

    def _emit_translated_text_stage(self, arbiter, adapter, session_zid, tsv_path, data_rows, headers,
                                    role_fields, sess_lang, eff_trace_id, new_fp, translated_html,
                                    sentences_list, stage_err, eff_tp_name, committed_prov):
        sorted_rows = sort_session_data_rows(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
        structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields)
        pub_provider = eff_tp_name if stage_err is None else None
        event = progressive.translated_text_event(
            structured_rows, new_fp, translated_html, sentences_list, stage_err,
            provider=pub_provider, provenance=committed_prov,
        )
        safe_write_update_js(
            tsv_path, sorted_rows, headers, role_fields, stage="translated_text", status=event["status"],
            error=stage_err, zid=session_zid, trace_id=eff_trace_id, translated_text=translated_html,
            text_translation_status=event["text_translation_status"],
            text_translation_failed=event["text_translation_failed"],
            text_provenance=committed_prov, sentences=sentences_list, provider=pub_provider,
            config=self.config, resolved_paths=self.resolved_paths, storage_adapter=adapter,
        )
        arbiter.emit_event(session_zid, event)

    def _run_sentence_stage(self, arbiter, adapter, session_zid, is_sqlite, text, sess_lang, sess_target,
                            text_mode, data_rows, headers, role_fields, col_idx, col_dest, tsv_path,
                            comments, req_zid, eff_trace_id, generation):
        text_chain, _ = resolve_provider_chain(self.config, task_type='text') if self.config else (['google'], 'chain')
        main_text_provider = text_chain[0] if text_chain else 'google'
        arbiter.emit_event(session_zid, {
            "type": "stage", "stage": "text_translation", "status": "in_progress", "provider": main_text_provider,
        })
        raw, committed_indices, fallback_prov, stage_err, superseded = self._translate_and_persist_sentences(
            adapter, session_zid, is_sqlite, text, sess_lang, sess_target, text_mode,
            main_text_provider, req_zid, eff_trace_id, generation,
        )
        if superseded:
            return None, None, True
        sentences_list, committed_map, committed_prov, translated_html, mat_err = self._materialize_sentence_stage(
            adapter, session_zid, is_sqlite, raw, committed_indices, fallback_prov,
            text, text_mode, data_rows, col_idx, col_dest, tsv_path, comments, headers,
        )
        stage_err = progressive.combine_worker_errors(stage_err, mat_err)
        eff_tp_name = str(committed_prov)[5:].strip().lower() if committed_prov and str(committed_prov).startswith("live:") else main_text_provider
        new_fp = compute_content_fingerprint(data_rows)
        if not self._cache_sentence_stage(
            arbiter, session_zid, data_rows, new_fp, committed_map, sentences_list, committed_prov, eff_tp_name, generation
        ):
            return None, None, True
        self._emit_translated_text_stage(
            arbiter, adapter, session_zid, tsv_path, data_rows, headers, role_fields, sess_lang,
            eff_trace_id, new_fp, translated_html, sentences_list, stage_err, eff_tp_name, committed_prov,
        )
        return stage_err, committed_prov, False

    def _start_progressive_worker_state(self, storage_adapter, session_zid, is_sqlite):
        if not (is_sqlite and hasattr(storage_adapter, 'db')):
            return None, None
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            storage_adapter.db.set_worker_status(session_zid, "running", started_at=now_iso)
            storage_adapter.db.update_worker_heartbeat(session_zid, heartbeat_at=now_iso)
            return None, None
        except Exception as exc:
            logger.error(f"Error initializing progressive worker state for session {session_zid}: {exc}\n{traceback.format_exc()}")
            return exc, progressive.startup_persistence_error(exc)

    def _finalize_progressive_execution(self, *, startup_error, **final_state_kwargs):
        finalization_error = None
        try:
            self._publish_progressive_final_state(**final_state_kwargs)
        except Exception as error:
            finalization_error = error
        raise_progressive_finalization_errors(startup_error, finalization_error)

    @operation_preferences
    def _execute_progressive_task(
        self,
        session_zid: str,
        arbiter: Any,
        language: str = "de",
        target_lang: str = "ru",
        text_mode: str = "single",
        prompt_name: str = "",
        skip_intellifiller: bool = False,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
        generation: Optional[int] = None,
    ):
        req_zid = zid or session_zid
        eff_trace_id = trace_id or f"{req_zid}:progressive:worker"
        storage_adapter = getattr(arbiter, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        mapping_path = Path(self.resolved_paths['anki_mapping_file']) if (self.resolved_paths and 'anki_mapping_file' in self.resolved_paths) else None
        mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None

        worker_error = None
        startup_error = None
        data_rows = []
        headers = []
        role_fields = {}
        sess_lang = language
        sess_target = target_lang
        tsv_path = None
        active_text_prov = None
        sess_row_provs: Dict[Any, str] = {}

        if self._is_progressive_superseded(session_zid, generation):
            return

        try:
            startup_error, startup_payload = self._start_progressive_worker_state(storage_adapter, session_zid, is_sqlite)
            if startup_error is not None:
                worker_error = progressive.combine_worker_errors(worker_error, startup_payload)
                return
            # 1. Recover / Load session data
            session_data = None
            with arbiter._lock:
                sess = arbiter.sessions.get(session_zid)
                if sess:
                    session_data = {
                        "text": sess.get("text", ""),
                        "comments": list(sess.get("comments", [])),
                        "headers": list(sess.get("headers", [])),
                        "data_rows": [list(r) for r in sess.get("data_rows", [])],
                        "language": sess.get("language") or language,
                        "target_lang": sess.get("target_lang") or target_lang,
                        "text_mode": sess.get("text_mode") or text_mode,
                        "tsv_path": sess.get("tsv_path"),
                    }

            if not session_data:
                t_cand = None if is_sqlite else find_working_tsv(results_dir, session_zid, language, storage_adapter=storage_adapter)
                if t_cand and t_cand.exists():
                    c, h, dr = storage_adapter.load_tsv_rows(t_cand)
                    source_txt = t_cand.with_suffix('.txt')
                    st_text = source_txt.read_text(encoding='utf-8') if source_txt.exists() else ""
                    session_data = {
                        "text": st_text,
                        "comments": c,
                        "headers": h,
                        "data_rows": dr,
                        "language": language,
                        "target_lang": target_lang,
                        "text_mode": text_mode,
                        "tsv_path": str(t_cand),
                    }
                elif is_sqlite:
                    restored = storage_adapter.restore_session(session_zid, results_dir=results_dir)
                    if restored:
                        restored_headers, restored_rows = reword.restored_manifest_rows(restored)
                        session_data = {
                            "text": restored.get("source_text") or restored.get("source_raw_text", ""),
                            "comments": restored.get("comments", []),
                            "headers": restored_headers,
                            "data_rows": restored_rows,
                            "language": restored.get("source_language") or language,
                            "target_lang": restored.get("target_language") or target_lang,
                            "text_mode": text_mode,
                            "tsv_path": restored.get("tsv_path"),
                        }

            if not session_data:
                raise StructuredError(ErrorCode.NOT_FOUND, f"Progressive task could not find session {session_zid}")

            headers = session_data["headers"]
            data_rows = session_data["data_rows"]
            text = session_data["text"]
            sess_lang = session_data["language"] or language
            sess_target = session_data["target_lang"] or target_lang
            role_fields = get_role_fields(mapping, headers) if mapping else {}
            tsv_path = session_zid if is_sqlite else (Path(session_data["tsv_path"]) if session_data.get("tsv_path") else results_dir / f"{session_zid}.{sess_lang}.tsv")

            col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
            col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
            col_sentence_idx = headers.index(role_fields['sentence_index']) if 'sentence_index' in role_fields and role_fields['sentence_index'] in headers else (headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1)
            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1
            occurrence_keys = reword.row_keys(data_rows, headers, role_fields.get('sentence_index', 'SentenceSourceIndex'), min_sentence=0) if is_sqlite else None

            # Emit initial source stage if not already emitted
            sorted_rows = sort_session_data_rows(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
            safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="source", zid=session_zid, trace_id=eff_trace_id, config=self.config, resolved_paths=self.resolved_paths, storage_adapter=storage_adapter)
            arbiter.emit_event(session_zid, {
                "type": "stage",
                "stage": "source",
                "status": "success",
                "rows": format_update_rows_dict(sorted_rows, headers, role_fields),
                "fingerprint": compute_content_fingerprint(data_rows),
            })

            # Stage 1: Text / Sentence Translation
            run_text = self.config.get(SEC_TRIGGERS, 'run_text_translation', fallback='auto') if self.config else 'auto'
            db_sents = []
            if is_sqlite and hasattr(storage_adapter, 'db'):
                try:
                    db_sents = storage_adapter.db.get_sentences_by_session(session_zid) or []
                except Exception:
                    db_sents = []
            sentence_translated = (
                progressive.sentences_complete(db_sents)
                if db_sents
                else (col_sentence_dest != -1 and any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows))
            )

            active_text_prov = session_data.get("text_provenance") or session_data.get("textProvenance")
            if not active_text_prov and db_sents:
                active_text_prov = progressive.text_provenance(db_sents, None)

            if not sentence_translated and run_text == 'auto' and text:
                stage1_error, stage1_prov, superseded = self._run_sentence_stage(
                    arbiter, storage_adapter, session_zid, is_sqlite, text, sess_lang, sess_target,
                    text_mode, data_rows, headers, role_fields, col_sentence_idx, col_sentence_dest,
                    tsv_path, session_data["comments"], req_zid, eff_trace_id, generation,
                )
                if superseded:
                    return
                active_text_prov = stage1_prov
                if stage1_error is not None:
                    worker_error = progressive.combine_worker_errors(worker_error, stage1_error)

            # Stage 2: Lemma Base Translation
            run_base = self.config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback='auto') if self.config else 'auto'
            if run_base == 'auto' and col_lemma != -1:
                # 2a. Wordfill pre-fill
                self._run_wordfill_stage(
                    arbiter,
                    storage_adapter,
                    session_zid,
                    req_zid,
                    is_sqlite,
                    data_rows,
                    headers,
                    role_fields,
                    occurrence_keys,
                    col_lemma,
                    col_word_dest,
                    sess_lang,
                    tsv_path,
                    session_data["comments"],
                )

                # 2b. Batch lemma translations
                lemmas_to_translate = []
                seen_lemmas = set()
                for row in data_rows:
                    if len(row) > col_lemma and row[col_lemma].strip():
                        val = row[col_lemma].strip()
                        is_trans = (col_word_dest != -1 and len(row) > col_word_dest and bool(row[col_word_dest].strip()))
                        if not is_trans and val not in seen_lemmas:
                            seen_lemmas.add(val)
                            lemmas_to_translate.append(val)

                trans_order = (self.config.get(SEC_TRANSLATION, 'translation_order', fallback='top_to_bottom') if self.config else 'top_to_bottom').strip().lower()
                if trans_order == 'bottom_to_top':
                    lemmas_to_translate = list(reversed(lemmas_to_translate))

                lemma_chain, _ = resolve_provider_chain(self.config, task_type='lemma') if self.config else (['google'], 'chain')
                lemma_provider = lemma_chain[0] if lemma_chain else 'google'
                translated_map: Dict[str, str] = {}
                if lemmas_to_translate:
                    chunk_size = 15
                    if self.config and hasattr(self.config, 'getint'):
                        chunk_size = self.config.getint(SEC_TRANSLATION, 'lemma_batch_size', fallback=15)
                    chunks = [lemmas_to_translate[i:i + chunk_size] for i in range(0, len(lemmas_to_translate), chunk_size)]
                    lemma_prov_tag = f"live:{lemma_provider}"
                    arbiter.emit_event(session_zid, {
                        "type": "stage",
                        "stage": "lemma_translation",
                        "status": "in_progress",
                        "provider": lemma_provider,
                    })
                    for chunk in chunks:
                        if is_sqlite and hasattr(storage_adapter, 'db'):
                            try:
                                storage_adapter.db.update_worker_heartbeat(session_zid, heartbeat_at=datetime.now(timezone.utc).isoformat())
                            except Exception:
                                pass
                        chunk_trans = translate_lemmas_fast_path(
                            chunk,
                            source=sess_lang,
                            target=sess_target,
                            config=self.config,
                            resolved_paths=self.resolved_paths,
                            provider=lemma_provider,
                            zid=session_zid,
                        )
                        reword.fast_provider_results(chunk, chunk_trans)
                        if chunk_trans:
                            chunk_prov = getattr(chunk_trans, 'provenance', None)
                            if chunk_prov:
                                lemma_prov_tag = chunk_prov
                                prov_cand = chunk_prov.split(':', 1)[1] if ':' in chunk_prov else chunk_prov
                                if prov_cand and prov_cand != lemma_provider:
                                    lemma_provider = prov_cand
                                    with arbiter._lock:
                                        if session_zid in arbiter.sessions:
                                            arbiter.sessions[session_zid]["active_lemma_provider"] = lemma_provider
                                            arbiter.sessions[session_zid]["active_provider"] = lemma_provider
                                    arbiter.emit_event(session_zid, {
                                        "type": "stage",
                                        "stage": "lemma_translation",
                                        "status": "in_progress",
                                        "provider": lemma_provider,
                                        "active_lemma_provider": lemma_provider,
                                        "active_provider": lemma_provider,
                                    })
                            translated_map.update(chunk_trans)

                    if translated_map:
                        updates = []
                        for row_idx, row in enumerate(data_rows):
                            if col_lemma != -1 and len(row) > col_lemma:
                                l_val = row[col_lemma].strip()
                                if l_val in translated_map and col_word_dest != -1:
                                    t_val = translated_map[l_val]
                                    if t_val and str(t_val).strip():
                                        while len(row) <= col_word_dest:
                                            row.append("")
                                        row[col_word_dest] = t_val
                                        if is_sqlite:
                                            updates.append(dict(sentence_index=occurrence_keys[row_idx][0],
                                                token_order=occurrence_keys[row_idx][1],
                                                updates=dict(word_destination=t_val, word_provenance=lemma_prov_tag)))

                        if is_sqlite:
                            if updates:
                                storage_adapter.batch_update_words(session_zid=session_zid, updates_list=updates, zid=req_zid)
                        else:
                            with storage_adapter.file_lock(tsv_path):
                                storage_adapter.save_tsv_rows_safely(tsv_path, session_data["comments"], headers, data_rows)

                        # Propagate newly resolved lemma translations to sibling tabs!
                        arbiter.propagate_translations_to_siblings(translated_map, exclude_session_zid=session_zid, language=sess_lang)

                new_fp = compute_content_fingerprint(data_rows)
                with arbiter._lock:
                    if session_zid in arbiter.sessions:
                        arbiter.sessions[session_zid]["data_rows"] = data_rows
                        arbiter.sessions[session_zid]["fingerprint"] = new_fp
                        if active_text_prov:
                            arbiter.sessions[session_zid]["text_provenance"] = active_text_prov
                            arbiter.sessions[session_zid]["textProvenance"] = active_text_prov
                        sess_row_provs = dict(arbiter.sessions[session_zid].get("row_provenances", {}) or {})

                if translated_map:
                    for row_idx, row in enumerate(data_rows):
                        if col_lemma != -1 and len(row) > col_lemma:
                            l_val = row[col_lemma].strip()
                            if l_val in translated_map:
                                t_ord = str(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).strip() else str(row_idx)
                                sess_row_provs[t_ord] = lemma_prov_tag
                                if t_ord.isdigit():
                                    sess_row_provs[int(t_ord)] = lemma_prov_tag

                with arbiter._lock:
                    if session_zid in arbiter.sessions:
                        arbiter.sessions[session_zid]["row_provenances"] = sess_row_provs

                sorted_rows = sort_session_data_rows(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields, row_provenances=sess_row_provs)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", zid=session_zid, trace_id=eff_trace_id, text_provenance=active_text_prov, row_provenances=sess_row_provs, config=self.config, resolved_paths=self.resolved_paths, storage_adapter=storage_adapter)
                trans_event = {
                    "type": "update",
                    "stage": "translated",
                    "status": "success",
                    "provider": lemma_provider,
                    "fingerprint": new_fp,
                    "rows": structured_rows,
                    "row_provenances": sess_row_provs,
                    "rowProvenances": sess_row_provs,
                }
                curr_sents = arbiter.sessions.get(session_zid, {}).get("sentences")
                if curr_sents:
                    trans_event["sentences"] = curr_sents
                if active_text_prov:
                    trans_event["text_provenance"] = active_text_prov
                    trans_event["textProvenance"] = active_text_prov
                arbiter.emit_event(session_zid, trans_event)

            # Stage 3: Enrichment (IntelliFiller)
            run_enrich = self.config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='manual') if self.config else 'manual'
            enrich_provider = self.config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback='intellifiller') if self.config else 'intellifiller'
            if not skip_intellifiller and run_enrich == 'auto' and enrich_provider == 'intellifiller':
                selected_rows = []
                col_ipa = progressive.header_index(headers, (role_fields.get('ipa', 'WordSourceIPA'), 'WordSourceIPA'))
                col_morph = progressive.header_index(headers, (role_fields.get('morphology', 'WordSourceMorphologyAI'), 'WordSourceMorphologyAI'))
                for idx, r in enumerate(data_rows):
                    if col_lemma != -1 and len(r) > col_lemma and r[col_lemma].strip():
                        need_dest = col_word_dest == -1 or len(r) <= col_word_dest or not r[col_word_dest].strip()
                        need_ipa = col_ipa != -1 and (len(r) <= col_ipa or not r[col_ipa].strip())
                        need_morph = col_morph != -1 and (len(r) <= col_morph or not r[col_morph].strip())
                        if need_dest or need_ipa or need_morph:
                            selected_rows.append(idx)

                if selected_rows:
                    arbiter.reword_session(
                        session_zid=session_zid,
                        selected_rows=selected_rows,
                        prompt=prompt_name,
                        language=sess_lang,
                        zid=req_zid,
                    )

        except Exception as e:
            logger.error(f"Error in progressive queue task for session {session_zid}: {e}\n{traceback.format_exc()}")
            worker_error = progressive.combine_worker_errors(
                worker_error,
                {
                    "code": "ERR_PROGRESSIVE_TASK_FAILED",
                    "message": str(e),
                    "provider": "controller",
                    "details": {},
                },
            )
        finally:
            self._finalize_progressive_execution(
                startup_error=startup_error,
                arbiter=arbiter,
                storage_adapter=storage_adapter,
                session_zid=session_zid,
                is_sqlite=is_sqlite,
                tsv_path=tsv_path,
                sess_lang=sess_lang,
                headers=headers,
                data_rows=data_rows,
                role_fields=role_fields,
                mapping=mapping,
                active_text_prov=active_text_prov,
                worker_error=worker_error,
                eff_trace_id=eff_trace_id,
                sess_row_provs=sess_row_provs,
                generation=generation,
            )

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "max_workers": self.max_workers,
                "translation_max_workers": self.translation_max_workers,
                "translation_delay_seconds": self.translation_delay,
                "enrichment_cache_size": len(self._cache),
                "translation_cache_size": len(self._lemma_trans_cache),
                "inflight_enrichments": len(self._inflight_lemmas),
                "inflight_translations": len(self._inflight_trans),
                "active_progressive_sessions": list(self._active_progressive_sessions.keys()),
            }

    def _collect_task_errors(self, tasks, wait: bool) -> list:
        calls = []
        for task in tasks:
            if not task.cancelled() and (wait or task.done()):
                calls.append(lambda t=task: t.result(timeout=0))
        return run_cleanup(calls)

    def _record_shutdown_errors(self, errors: list):
        with self._lock:
            errors.extend(self._unobserved_progressive_failures)
            self._unobserved_progressive_failures.clear()
            for err in errors:
                if not any(existing is err for existing in self._acknowledged_failures):
                    self._acknowledged_failures.append(err)

    def shutdown(self, wait: bool = True):
        with self._lock:
            self._shutting_down = True
            tasks = set(self._inflight_lemmas.values()) | set(self._inflight_trans.values()) | set(self._active_progressive_sessions.values())
        if not wait:
            for task in tasks:
                task.cancel()
        executors = (self._executor, self._translation_executor, self._progressive_executor)
        errors = run_cleanup([lambda executor=executor: executor.shutdown(wait=wait) for executor in executors])
        errors.extend(self._collect_task_errors(tasks, wait))
        self._record_shutdown_errors(errors)
        if errors:
            raise BaseExceptionGroup('Controller queue disposal failed', list({id(error): error for error in errors}.values()))


def to_one_based_sentence_map(raw, single_unit=False) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Normalize provider identities once, without inventing alignment."""
    from kardenwort_alignment import normalize_sentence_result
    try:
        return normalize_sentence_result(raw, single_unit=single_unit)
    except ValueError as exc:
        raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc


def pair_sentence_records(records: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Build aligned (sources, destinations) lists from sentence records, omitting incomplete pairs."""
    ordered = sorted(records or [], key=lambda s: int(s.get("sentence_index") or 0))
    pairs = [(str(s.get("sentence_source") or "").strip(), str(s.get("sentence_destination") or "").strip()) for s in ordered]
    pairs = [(src, dst) for src, dst in pairs if src and dst]
    srcs = [p[0] for p in pairs]
    dests = [p[1] for p in pairs]
    return srcs, dests


# ---------------------------------------------------------------------------
# In-Memory Session Arbiter & SSE Event Dispatcher
# ---------------------------------------------------------------------------
from kardenwort_events import EventDispatcher


# ---------------------------------------------------------------------------
class SessionArbiter:
    """
    Maintains centralized in-memory session states, serializes concurrent mutations,
    and manages Server-Sent Events (SSE) subscriber channels.
    """

    config = OperationConfiguration()

    def __init__(self, config: Any, resolved_paths: Dict[str, Any], wordfill_cfg: Optional[Dict[str, Any]] = None):
        self.config = config
        self.resolved_paths = resolved_paths
        self.wordfill_cfg = wordfill_cfg or resolve_wordfill_config(config, resolved_paths)
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.enrichment_queue = EnrichmentQueue(config, resolved_paths)
        try:
            register_failover_callback(self._on_global_failover)
        except Exception:
            pass

    def _on_global_failover(self, task: str, from_p: str, to_p: str, zid: Optional[str] = None):
        if zid:
            self.notify_provider_failover(zid, task, from_p, to_p)

    def notify_provider_failover(self, session_zid: str, task: str, from_provider: str, to_provider: str):
        from_p = (from_provider or "").strip().lower()
        to_p = (to_provider or "").strip().lower()
        with self._lock:
            sess = self.sessions.get(session_zid)
            if sess is not None:
                if task == 'text':
                    sess['active_text_provider'] = to_p
                elif task == 'lemma':
                    sess['active_lemma_provider'] = to_p
                sess['active_provider'] = to_p

        event = {
            "type": "provider_failover",
            "task": task,
            "from": from_p,
            "to": to_p,
            "provider": to_p,
            "active_provider": to_p,
        }
        if task == 'text':
            event['active_text_provider'] = to_p
        elif task == 'lemma':
            event['active_lemma_provider'] = to_p
        self.emit_event(session_zid, event)

    @property
    def subscribers(self) -> Dict[str, List[queue.Queue]]:
        return EventDispatcher.get_instance()._subscribers

    def register_subscriber(self, session_zid: str) -> queue.Queue:
        return EventDispatcher.get_instance().register(session_zid)

    def unregister_subscriber(self, session_zid: str, q: queue.Queue):
        EventDispatcher.get_instance().unregister(session_zid, q)

    def emit_event(self, session_zid: str, event_data: Dict[str, Any]):
        EventDispatcher.get_instance().publish(session_zid, event_data)

    _publish_event = emit_event

    @operation_preferences
    def create_session(
        self,
        text: str,
        language: str,
        target_lang: Optional[str] = None,
        text_mode: str = "single",
        sections: Optional[str] = None,
        theme: Optional[str] = None,
        zid: Optional[str] = None,
        bypass_lang_check: bool = False,
    ) -> Dict[str, Any]:
        req_zid = zid or generate_unique_zid()
        target_lang = target_lang or self.config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')

        # Run core lookup flow
        res = core_lookup(
            text=text,
            language=language,
            target_lang=target_lang,
            fmt="html",
            text_mode=text_mode,
            sections=sections,
            theme=theme,
            zid=req_zid,
            wordfill_cfg=self.wordfill_cfg,
            config=self.config,
            resolved_paths=self.resolved_paths,
            goldendict=getattr(self, 'goldendict', {}),
            bypass_lang_check=bypass_lang_check
        )

        session_zid = res["session_zid"]

        init_row_provenances = {}
        headers = res.get("headers", [])
        data_rows = res.get("data_rows", [])
        mapping = load_anki_mapping(self.resolved_paths.get('anki_mapping_file')) if (self.resolved_paths and 'anki_mapping_file' in self.resolved_paths) else None
        role_fields = get_role_fields(mapping, headers) if mapping else {}
        col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
        col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1

        is_sqlite = (self.resolved_paths.get('storage_backend') == 'sqlite') if self.resolved_paths else False
        storage_adapter = get_storage_adapter(self.config, self.resolved_paths) if is_sqlite else None

        stored_provs = res.get("row_provenances") or res.get("rowProvenances") or {}
        if not stored_provs and is_sqlite:
            try:
                db_words = storage_adapter.db.get_words_by_session(session_zid)
                for w in db_words:
                    w_prov = w.get("word_provenance")
                    if w_prov:
                        t_ord = str(w.get("token_order", ""))
                        if t_ord:
                            stored_provs[t_ord] = w_prov
                            if t_ord.isdigit():
                                stored_provs[int(t_ord)] = w_prov
            except Exception:
                pass

        if stored_provs:
            init_row_provenances.update(stored_provs)

        init_text_prov = res.get("text_provenance") or res.get("textProvenance")
        if not init_text_prov and is_sqlite:
            try:
                db_sents = storage_adapter.db.get_sentences_by_session(session_zid)
                if db_sents:
                    init_text_prov = next((s.get("text_provenance") for s in db_sents if s.get("text_provenance")), None)
            except Exception:
                pass
        text_chain, text_strat = resolve_provider_chain(self.config, task_type='text') if self.config else (['google'], 'chain')
        main_text_provider = text_chain[0] if text_chain else 'google'
        lemma_chain, lemma_strat = resolve_provider_chain(self.config, task_type='lemma') if self.config else (['google'], 'chain')
        main_lemma_provider = lemma_chain[0] if lemma_chain else 'google'

        check_ips_str = self.config.get(SEC_PIPELINE, 'fast_connectivity_check_ips', fallback=self.config.get(SEC_PIPELINE, 'fast_connectivity_check_ip', fallback='8.8.8.8, 1.1.1.1')) if self.config and self.config.has_section(SEC_PIPELINE) else ''
        check_ips = [ip.strip() for ip in check_ips_str.split(',') if ip.strip()]

        active_text_p = main_text_provider
        active_lemma_p = main_lemma_provider
        if (text_strat == 'offline_fallback' or lemma_strat == 'offline_fallback') and check_ips:
            if not is_network_online_multi(hosts=check_ips):
                if text_strat == 'offline_fallback' and active_text_p != 'argos':
                    active_text_p = 'argos'
                    record_provider_failover(main_text_provider, config=self.config, resolved_paths=self.resolved_paths, task_type='text')
                if lemma_strat == 'offline_fallback' and active_lemma_p != 'argos':
                    active_lemma_p = 'argos'
                    record_provider_failover(main_lemma_provider, config=self.config, resolved_paths=self.resolved_paths, task_type='lemma')
                record_session_active_provider(session_zid, text=active_text_p, lemma=active_lemma_p, provider=active_lemma_p or active_text_p, config=self.config, resolved_paths=self.resolved_paths)

        if text_strat != 'strict' and is_provider_cooled_down(active_text_p, config=self.config, resolved_paths=self.resolved_paths, task_type='text'):
            for cand in text_chain:
                if not is_provider_cooled_down(cand, config=self.config, resolved_paths=self.resolved_paths, task_type='text'):
                    active_text_p = cand
                    break

        if lemma_strat != 'strict' and is_provider_cooled_down(active_lemma_p, config=self.config, resolved_paths=self.resolved_paths, task_type='lemma'):
            for cand in lemma_chain:
                if not is_provider_cooled_down(cand, config=self.config, resolved_paths=self.resolved_paths, task_type='lemma'):
                    active_lemma_p = cand
                    break

        if active_text_p != main_text_provider or active_lemma_p != main_lemma_provider:
            record_session_active_provider(session_zid, text=active_text_p, lemma=active_lemma_p, provider=active_lemma_p or active_text_p, config=self.config, resolved_paths=self.resolved_paths)

        if not init_text_prov and res.get("sentence_translation"):
            init_text_prov = f"live:{active_text_p}"

        with self._lock:
            self.sessions[session_zid] = {
                "session_zid": session_zid,
                "language": language,
                "target_lang": target_lang,
                "text": text,
                "tsv_path": res["tsv_path"],
                "comments": res["comments"],
                "headers": res["headers"],
                "data_rows": res["data_rows"],
                "sentence_translation": res["sentence_translation"],
                "active_text_provider": active_text_p,
                "active_lemma_provider": active_lemma_p,
                "active_provider": active_lemma_p or active_text_p,
                "text_provenance": init_text_prov,
                "textProvenance": init_text_prov,
                "row_provenances": init_row_provenances,
                "fingerprint": res["fingerprint"],
                "lock": threading.Lock(),
                "created_at": time.time(),
            }

        res["active_text_provider"] = active_text_p
        res["active_lemma_provider"] = active_lemma_p
        res["active_provider"] = active_lemma_p or active_text_p
        res["row_provenances"] = init_row_provenances
        res["rowProvenances"] = init_row_provenances
        if init_text_prov:
            res["text_provenance"] = init_text_prov
            res["textProvenance"] = init_text_prov

        # Emit initial source stage event
        self.emit_event(session_zid, {
            "type": "stage",
            "stage": "source",
            "status": "success",
            "active_text_provider": active_text_p,
            "active_lemma_provider": active_lemma_p,
            "active_provider": active_lemma_p or active_text_p,
            "rows": res["data_rows"],
            "row_provenances": init_row_provenances,
            "rowProvenances": init_row_provenances,
            "fingerprint": res["fingerprint"]
        })

        if "html" in res:
            res["html_b64"] = encode(res["html"])

        return res

    start_session = create_session

    def save_session(
        self,
        session_zid: str,
        deltas: List[Dict[str, Any]],
        fingerprint: Optional[str] = None,
        language: Optional[str] = None,
        zid: Optional[str] = None
    ) -> Dict[str, Any]:
        req_zid = zid or generate_unique_zid()

        with self._lock:
            session = self.sessions.get(session_zid)

        adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        sess_lock = session["lock"] if session else self._lock
        committed_snapshot: Dict[str, Any] = {}
        with sess_lock:
            res = core_edit_save(
                tsv_path_or_session=session_zid,
                deltas=deltas,
                config=self.config,
                resolved_paths=self.resolved_paths,
                fingerprint=fingerprint,
                zid=req_zid,
                language=language or (session.get("language") if session else None),
                snapshot_out=committed_snapshot if (session and adapter.backend_name == 'sqlite') else None,
            )

            if session:
                session["fingerprint"] = res["fingerprint"]
                if committed_snapshot.get("headers") is not None and committed_snapshot.get("data_rows") is not None:
                    session["headers"] = committed_snapshot["headers"]
                    session["data_rows"] = committed_snapshot["data_rows"]
                else:
                    stored_path = session.get('tsv_path') or res.get('tsv_path')
                    if adapter.backend_name == 'sqlite' or (stored_path and Path(stored_path).is_file()):
                        _, current_headers, current_rows = adapter.load_tsv_rows(session_zid if adapter.backend_name == 'sqlite' else stored_path)
                        session['headers'] = current_headers
                        session['data_rows'] = current_rows

        raw_selection = res.get("shared_selection")
        selection = dict(raw_selection) if isinstance(raw_selection, dict) else None
        # Broadcast state mutation event to all connected SSE clients only after commit
        with committed_delivery(session_zid, res['fingerprint']):
            self.emit_event(session_zid, {
                "type": "update",
                "stage": "saved",
                "status": "success",
                "fingerprint": res["fingerprint"],
                "deltas": deltas,
                "shared_selection": selection,
                "zid": res["zid"]
            })

        return res

    def _retext_memo(self, session_zid):
        with self._lock:
            return dict(self.sessions.get(session_zid, {}))

    def _retext_path(self, adapter, session_zid, memo, path=None):
        if adapter.backend_name == 'sqlite':
            return session_zid
        candidate = path or memo.get('tsv_path')
        if candidate:
            return Path(candidate)
        language = memo.get('language') or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        results = resolve_results_dir(self.resolved_paths, self.config)
        return find_working_tsv(results, session_zid, language, storage_adapter=adapter) or results / f'{session_zid}.{language}.tsv'

    @staticmethod
    def _prepare_legacy_retext(adapter, path, memo):
        if adapter.backend_name == 'sqlite' or path.exists():
            return
        if memo.get('headers') and memo.get('data_rows'):
            path.parent.mkdir(parents=True, exist_ok=True)
            save_tsv_rows_safely(path, memo.get('comments', []), memo['headers'], memo['data_rows'])

    @staticmethod
    def _retext_maps(sentence_trans, sentence_map, padded_map):
        from kardenwort_alignment import persistence_map
        try:
            main = persistence_map(sentence_trans if sentence_map is None else sentence_map)
            padded = persistence_map(padded_map if padded_map is not None else sentence_trans.get('PADDED', {}))
            if not main:
                raise ValueError('Retext persistence requires a non-empty one-based sentence map')
            return main, padded
        except (ValueError, AttributeError) as exc:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc

    def _persist_retext_rows(self, adapter, path, session_zid, memo, main, padded, provenance, req_zid):
        from kardenwort_alignment import patch_translation_rows
        with adapter.file_lock(path):
            comments, headers, rows = adapter.load_tsv_rows(path)
            roles = memo.get('role_fields') or get_role_fields(load_anki_mapping(self.resolved_paths['anki_mapping_file']), headers)
            try:
                rows = patch_translation_rows(rows, headers, roles, main, padded)
            except ValueError as exc:
                raise StructuredError(ErrorCode.INVALID_STATE, str(exc)) from exc
            if adapter.backend_name == 'sqlite':
                adapter.persist_retext(session_zid, main, padded, provenance=provenance, zid=req_zid)
            else:
                adapter.save_tsv_rows_safely(path, comments, headers, rows)
        return comments, headers, rows, roles

    def _persist_retext_updates(self, session_zid, sentence_trans, active_text_prov=None,
                                req_zid=None, tsv_path=None, active_text_provider=None,
                                translated_text=None, sentence_map=None, padded_map=None):
        main, padded = self._retext_maps(sentence_trans, sentence_map, padded_map)
        adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        memo = self._retext_memo(session_zid)
        path = self._retext_path(adapter, session_zid, memo, tsv_path)
        self._prepare_legacy_retext(adapter, path, memo)
        provenance = active_text_prov or getattr(sentence_trans, 'provenance', 'live:retext')
        provider = active_text_provider or str(provenance).removeprefix('live:')
        if translated_text is None:
            translated_text = format_translated_html(sentence_trans, text_mode=memo.get('text_mode', 'single'), text=memo.get('text', ''), config=self.config)
        comments, headers, rows, roles = self._persist_retext_rows(adapter, path, session_zid, memo, main, padded, provenance, req_zid)
        result = dict(status='success', session_zid=session_zid, comments=comments, headers=headers,
                      data_rows=rows, role_fields=roles, fingerprint=compute_content_fingerprint(rows),
                      tsv_path=path, translated_text=translated_text, translatedText=translated_text,
                      text_provenance=provenance, textProvenance=provenance, active_text_provider=provider)
        self._cache_retext_result(session_zid, result, sentence_trans)
        return result

    def _cache_retext_result(self, session_zid, result, raw):
        with self._lock:
            session = self.sessions.setdefault(session_zid, {})
            session.update({key: result[key] for key in ('data_rows', 'fingerprint', 'translated_text', 'translatedText', 'text_provenance', 'textProvenance', 'active_text_provider')})
            session['sentence_translation'] = raw
            session['active_provider'] = result['active_text_provider']

    @staticmethod
    def _read_retext_sentences(adapter, session_zid):
        if adapter.backend_name != 'sqlite':
            return []
        from kardenwort_alignment import validate_authoritative_records
        try:
            records = adapter.db.get_sentences_by_session(session_zid)
            validate_authoritative_records(records)
            return records
        except Exception as exc:
            raise StructuredError(ErrorCode.DESK_FAILED, f'Re-text sentence read failed for session {session_zid}') from exc

    def _read_retext_source(self, adapter, session_zid, memo, path, records):
        from kardenwort_alignment import sentence_sources
        if records:
            return sentence_sources(records)
        if adapter.backend_name != 'sqlite':
            source = Path(path).with_suffix('.txt')
            if source.exists():
                return source.read_text(encoding='utf-8')
        if memo.get('text'):
            return memo['text']
        try:
            recovered = adapter.restore_session(session_zid).get('source_text', '')
        except Exception as exc:
            raise StructuredError(ErrorCode.DESK_FAILED, 'Re-text source read failed') from exc
        if not recovered:
            raise StructuredError(ErrorCode.DESK_FAILED, f'Source text not recoverable for session {session_zid}')
        return recovered

    def _retext_providers(self):
        providers, _ = resolve_provider_chain(self.config, task_type='text')
        preferred = self.config.get(SEC_PIPELINE, 'text_reprocess_provider', fallback='').strip().lower()
        if preferred:
            return [preferred] + [provider for provider in providers if provider != preferred]
        return providers

    def _translate_retext(self, text, language, target, mode, req_zid):
        last_error = None
        for provider in self._retext_providers():
            try:
                translated = translate_source_text(text, language, target, mode, self.config, self.resolved_paths, provider, zid=req_zid)
                if translated:
                    return translated, provider
            except Exception as exc:
                logger.warning("Retext provider '%s' failed", provider)
                last_error = exc
        raise StructuredError(ErrorCode.DESK_FAILED, 'All retext providers failed or returned empty output') from last_error

    @staticmethod
    def _align_retext(raw, records):
        from kardenwort_alignment import validate_complete_alignment
        main, padded = to_one_based_sentence_map(raw, single_unit=len(records) == 1)
        if records:
            try:
                validate_complete_alignment(main, records)
            except ValueError as exc:
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
        return main, padded

    @operation_preferences
    def retext_session(self, session_zid, language=None, text_mode='single', zid=None):
        req_zid = zid or generate_unique_zid()
        language = language or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        target = self.config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')
        adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        records = self._read_retext_sentences(adapter, session_zid)
        memo = self._retext_memo(session_zid)
        path = self._retext_path(adapter, session_zid, memo)
        text = self._read_retext_source(adapter, session_zid, memo, path, records)
        mode = 'multi' if records else text_mode
        raw, provider = self._translate_retext(text, language, target, mode, req_zid)
        main, padded = self._align_retext(raw, records)
        provenance = getattr(raw, 'provenance', None) or f'live:{provider}'
        provider = provenance.removeprefix('live:')
        translated = format_translated_html(raw, text_mode=mode, text=text, config=self.config)
        result = self._persist_retext_updates(session_zid, raw, provenance, req_zid, path, provider, translated, main, padded)
        return self._publish_retext_result(result, main, language)

    def _publish_retext_result(self, result, sentences, language):
        rows = sort_session_data_rows(result['data_rows'], result['headers'], language, self.config, self.resolved_paths, role_fields=result['role_fields'])
        payload = {key: result[key] for key in ('status', 'session_zid', 'fingerprint', 'data_rows', 'translated_text', 'translatedText', 'text_provenance', 'textProvenance', 'active_text_provider')}
        payload.update(sentences=sentences, active_provider=result['active_text_provider'], rows=format_update_rows_dict(rows, result['headers'], result['role_fields']))
        try:
            safe_write_update_js(result['tsv_path'], rows, result['headers'], result['role_fields'], stage='finished', status='success', source_text='', translated_text=result['translated_text'], sentences=sentences, zid=result['session_zid'], config=self.config, resolved_paths=self.resolved_paths)
            self.emit_event(result['session_zid'], dict(payload, type='update', stage='translated_text'))
        except Exception as exc:
            raise StructuredError(ErrorCode.DESK_FAILED, 'Re-text committed; update delivery failed', details={'committed': True, 'session_zid': result['session_zid'], 'fingerprint': result['fingerprint'], 'recovery_url': f"/session/status?zid={result['session_zid']}&committed=1"}) from exc
        return payload

    def propagate_enrichment_to_siblings(
        self,
        enriched_lemmas: Dict[str, Dict[str, str]],
        exclude_session_zid: Optional[str] = None,
        language: str = "de",
    ):
        """
        Propagates newly enriched lemma fields (WordDestination, WordSourceIPA, WordSourceMorphologyAI)
        simultaneously to all active sibling sessions and their subscribers.
        """
        if not enriched_lemmas:
            return

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = getattr(storage_adapter, 'backend_name', '') == 'sqlite'
        results_dir = resolve_results_dir(self.resolved_paths, self.config)

        with self._lock:
            sibling_zids = [z for z in self.sessions.keys() if z != exclude_session_zid]

        for sib_zid in sibling_zids:
            with self._lock:
                sess = self.sessions.get(sib_zid)
                if not sess:
                    continue
                sess_lang = sess.get("language") or language
                if sess_lang != language:
                    continue
                data_rows = [list(r) for r in sess.get("data_rows", [])]
                headers = list(sess.get("headers", []))
                role_fields = dict(sess.get("role_fields", {}))
                sib_row_provs = dict(sess.get("row_provenances", {}))

            if not data_rows or not headers:
                continue

            col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
            if col_lemma == -1:
                continue

            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1

            stored_session = storage_adapter.db.get_session(sib_zid) if is_sqlite else None
            keys = reword.row_keys(data_rows, headers, role_fields.get('sentence_index', 'SentenceSourceIndex')) if stored_session else None
            updates = []
            modified = False
            for row_id, row in enumerate(data_rows):
                if len(row) <= col_lemma:
                    continue
                lemma_val = row[col_lemma].strip()
                if lemma_val in enriched_lemmas:
                    enrich_dict = enriched_lemmas[lemma_val]
                    row_modified = False
                    for field_name, val in enrich_dict.items():
                        if not val or field_name == "word_provenance":
                            continue
                        if field_name not in headers:
                            headers.append(field_name)
                            for dr in data_rows:
                                dr.append("")
                        col_idx = headers.index(field_name)
                        while len(row) <= col_idx:
                            row.append("")
                        if is_unfinished_translation(row[col_idx]):
                            row[col_idx] = str(val)
                            if keys is not None:
                                updates.append(dict(sentence_index=keys[row_id][0], token_order=keys[row_id][1], field=field_name, value=str(val)))
                            row_modified = True
                            modified = True

                    if row_modified:
                        prov_val = enrich_dict.get("word_provenance", "live:intellifiller")
                        if keys is not None:
                            updates.append(dict(sentence_index=keys[row_id][0], token_order=keys[row_id][1], field='word_provenance', value=prov_val))
                        sib_row_provs[row_id] = prov_val
                        sib_row_provs[str(row_id)] = prov_val
                        if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).strip():
                            t_ord_str = str(row[col_token_order]).strip()
                            sib_row_provs[t_ord_str] = prov_val
                            if t_ord_str.isdigit():
                                sib_row_provs[int(t_ord_str)] = prov_val

            if modified:
                if is_sqlite:
                    storage_adapter.batch_update_words(session_zid=sib_zid, updates_list=updates, zid=sib_zid)
                else:
                    sib_tsv = find_working_tsv(results_dir, sib_zid, sess_lang, storage_adapter=storage_adapter)
                    if sib_tsv:
                        with storage_adapter.file_lock(sib_tsv):
                            comments, _, _ = storage_adapter.load_tsv_rows(sib_tsv)
                            storage_adapter.save_tsv_rows_safely(sib_tsv, comments, headers, data_rows)

                new_fp = compute_content_fingerprint(data_rows)
                with self._lock:
                    if sib_zid in self.sessions:
                        self.sessions[sib_zid]["data_rows"] = data_rows
                        self.sessions[sib_zid]["headers"] = headers
                        self.sessions[sib_zid]["fingerprint"] = new_fp
                        self.sessions[sib_zid]["row_provenances"] = sib_row_provs

                structured_rows = format_update_rows_dict(data_rows, headers, role_fields, row_provenances=sib_row_provs)
                self.emit_event(sib_zid, {
                    "type": "update",
                    "stage": "enrichment",
                    "status": "success",
                    "fingerprint": new_fp,
                    "rows": structured_rows,
                    "row_provenances": sib_row_provs,
                    "rowProvenances": sib_row_provs,
                })

    @operation_preferences
    def reword_session(
        self,
        session_zid: str,
        selected_rows: Optional[List[int]] = None,
        prompt: Optional[str] = None,
        language: Optional[str] = None,
        zid: Optional[str] = None,
        token_orders: Optional[List[Any]] = None,
        target_coordinates: Optional[List[Any]] = None,
        visual_indices: Optional[List[Any]] = None,
        sentence_idx: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            reword.validate_session_targets(session_zid, target_coordinates)
        except ValueError as exc:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
        req_zid = zid or generate_unique_zid()
        if sentence_idx is not None:
            try:
                sentence_idx = reword.index_value(sentence_idx)
            except reword.InvalidRewordTarget as exc:
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
        lang = language or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        target_lang = self.config.get(SEC_SETTINGS, 'default_target_language', fallback='ru') if self.config else 'ru'
        prompt_name = prompt or self.config.get(SEC_LANGUAGES, f"{lang}_prompt", fallback="")
        provider = self.config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback='intellifiller') if self.config else 'intellifiller'
        provider = (provider or 'intellifiller').strip().lower()

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        tsv_path = session_zid if is_sqlite else find_working_tsv(results_dir, session_zid, lang, storage_adapter=storage_adapter)
        if not tsv_path:
            tsv_path = results_dir / f"{session_zid}.{lang}.tsv"

        if not is_sqlite and (not tsv_path or not tsv_path.exists()):
            raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found for session {session_zid}")

        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)

        mapping = load_anki_mapping(self.resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        # Preserve the active render's row order only for a complete row snapshot.
        # Status delivery may cache projected rows without its headers contract.
        with self._lock:
            if session_zid in self.sessions and self.sessions[session_zid].get("data_rows") and self.sessions[session_zid].get("headers"):
                data_rows = [list(r) for r in self.sessions[session_zid]["data_rows"]]
                headers = list(self.sessions[session_zid]['headers'])
                role_fields = get_role_fields(mapping, headers)
            else:
                data_rows = sort_session_data_rows(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)

        col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
        existing_row_provs = {}
        with self._lock:
            if session_zid in self.sessions:
                existing_row_provs = dict(self.sessions[session_zid].get("row_provenances", {}))

        source_text = self.sessions.get(session_zid, {}).get('text', '')
        if is_sqlite:
            source_text = storage_adapter.restore_session(session_zid).get('source_text', '')
        try:
            selection = resolve_reword_request(data_rows, headers, role_fields, source_text, self.config,
                targets=target_coordinates, selected_rows=selected_rows, token_orders=token_orders,
                visual_indices=visual_indices)
        except ValueError as exc:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
        selected_row_indices = set(selection.rows)
        selected_keys = dict(zip(selection.rows, selection.keys))
        is_master_overview = sentence_idx is None or sentence_idx == 0

        if selected_row_indices and provider != 'none':
            reword_prov_tag = f"live:{provider}"
            new_reword_provs = {}
            for r_idx in selected_row_indices:
                new_reword_provs[r_idx] = reword_prov_tag
                new_reword_provs[str(r_idx)] = reword_prov_tag

            existing_row_provs.update(new_reword_provs)

        enriched_lemma_map: Dict[str, Dict[str, str]] = {}
        if selected_row_indices and provider != 'none':
            if provider == 'intellifiller':
                try:
                    original_rows, original_headers = data_rows, headers
                    if is_sqlite:
                        success = storage_adapter.enrich_session_intellifiller(
                            session_zid=session_zid, prompt_name=prompt_name, reprocess=True,
                            zid=req_zid, target_coordinates=reword_descriptors(selection),
                            visual_indices=list(selection.visuals))
                        if not success:
                            raise StructuredError(ErrorCode.DESK_FAILED, 'Re-word provider failed')
                        _, output_headers, output_rows = storage_adapter.load_tsv_rows(tsv_path)
                    else:
                        output_headers, output_rows = enrich_reword_tsv(
                            tsv_path, comments, headers, data_rows, role_fields, selection, source_text,
                            prompt_name, self.config, self.resolved_paths, run_headless_intellifiller, req_zid)
                    output = checked_provider_rows(original_rows, original_headers,
                                                   output_rows, output_headers, role_fields)
                    keys = reword.row_keys(original_rows, original_headers,
                        role_fields.get('sentence_index', 'SentenceSourceIndex'))
                    headers, data_rows = output_headers, [output[key] for key in keys]
                    for index in selection.rows:
                        row = data_rows[index]
                        lemma = row[col_lemma].strip()
                        item = {}
                        for role, field in [('word_translation', 'WordDestination'), ('ipa', 'WordSourceIPA'),
                                            ('morphology', 'WordSourceMorphologyAI'), ('pos', 'WordSourcePOS'),
                                            ('gender', 'WordSourceGender')]:
                            column = role_fields.get(role, field)
                            if column in headers and row[headers.index(column)].strip():
                                item[field] = row[headers.index(column)].strip()
                        if item:
                            item['word_provenance'] = 'live:intellifiller'
                            self.enrichment_queue.set_cached(lemma, lang, item)
                            enriched_lemma_map[lemma] = item
                except StructuredError:
                    raise
                except Exception as exc:
                    raise StructuredError(ErrorCode.DESK_FAILED, f'Re-word failed: {exc}') from exc

                # Propagate newly enriched lemmas to open sibling sessions strictly if Master Overview session
                if enriched_lemma_map and is_master_overview:
                    self.propagate_enrichment_to_siblings(enriched_lemma_map, exclude_session_zid=session_zid, language=lang)
            else:
                # Fast-path machine translation (deepl, google, argos, combined, etc.)
                try:
                    lemmas_to_translate = []
                    for r_idx in selected_row_indices:
                        if r_idx < len(data_rows):
                            row = data_rows[r_idx]
                            if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
                                lemmas_to_translate.append(row[col_lemma].strip())

                    translated_map: Dict[str, str] = {}
                    translated_provenances: Dict[str, str] = {}
                    if lemmas_to_translate:
                        lemmas_unique = list(dict.fromkeys(lemmas_to_translate))
                        provider_to_use = 'combined' if provider == 'combined' else provider
                        chunk_size = 15
                        if self.config and hasattr(self.config, 'getint'):
                            chunk_size = self.config.getint(SEC_TRANSLATION, 'lemma_batch_size', fallback=15)
                        chunks = [lemmas_unique[i:i + chunk_size] for i in range(0, len(lemmas_unique), chunk_size)]
                        for chunk in chunks:
                            chunk_trans = translate_lemmas_fast_path(
                                chunk,
                                source=lang,
                                target=target_lang,
                                config=self.config,
                                resolved_paths=self.resolved_paths,
                                provider=provider_to_use,
                            )
                            translated_map.update(reword.fast_provider_results(chunk, chunk_trans))
                            chunk_provenance = getattr(chunk_trans, 'provenance', None) or f'live:{provider_to_use}'
                            translated_provenances.update({lemma: chunk_provenance for lemma in chunk})

                        if translated_map:
                            changes = {}
                            for r_idx, key in zip(selection.rows, selection.keys):
                                lemma = data_rows[r_idx][col_lemma].strip()
                                changes[key] = {role_fields['word_translation']: translated_map[lemma],
                                                'word_provenance': translated_provenances[lemma]}
                                existing_row_provs[r_idx] = translated_provenances[lemma]
                                existing_row_provs[str(r_idx)] = translated_provenances[lemma]
                            if is_sqlite:
                                commit_reword_sqlite(storage_adapter, session_zid, changes, selection,
                                    role_fields, data_rows, headers, source_text, self.config, req_zid)
                            else:
                                commit_reword_tsv(tsv_path, changes, role_fields)
                            _, output_headers, output_rows = storage_adapter.load_tsv_rows(tsv_path)
                            output = checked_provider_rows(data_rows, headers, output_rows, output_headers, role_fields)
                            keys = reword.row_keys(data_rows, headers,
                                role_fields.get('sentence_index', 'SentenceSourceIndex'))
                            headers, data_rows = output_headers, [output[key] for key in keys]

                            if is_master_overview:
                                self.propagate_translations_to_siblings(translated_map, exclude_session_zid=session_zid,
                                    language=lang, lemma_provenances=translated_provenances)
                except StructuredError:
                    raise
                except Exception as e:
                    raise StructuredError(ErrorCode.DESK_FAILED, f"Re-word failed: {e}") from e

        # Re-evaluate dictionary classifications if enabled
        class_cols: List[Tuple[str, int]] = []
        if selected_row_indices and provider != 'none':
            try:
                desk_classification_enabled = self.config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=True) if self.config and self.config.has_section(SEC_CLASSIFICATION) else True
                kardenwort_workspace = self.resolved_paths.get('kardenwort_workspace')
                if kardenwort_workspace:
                    kardenwort_workspace = Path(kardenwort_workspace)
                    kw_config = load_kardenwort_config(kardenwort_workspace)
                    if desk_classification_enabled and kw_config and kw_config.has_section(SEC_CLASSIFICATION) and kw_config.getboolean(SEC_CLASSIFICATION, 'enabled', fallback=False):
                        if str(kardenwort_workspace / "src") not in sys.path:
                            sys.path.append(str(kardenwort_workspace / "src"))
                        from kardenwort.core.kardenwort import load_classification_dictionaries

                        if kw_config.has_option(SEC_CLASSIFICATION, f'dictionaries_{lang}'):
                            dicts = kw_config.get(SEC_CLASSIFICATION, f'dictionaries_{lang}', fallback='')
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

                                    prefix = ""
                                    if ":" in path_str:
                                        parts = path_str.split(":", 1)
                                        possible_prefix = parts[0].strip()
                                        if len(possible_prefix) <= 5 and "/" not in possible_prefix and "\\" not in possible_prefix:
                                            prefix = possible_prefix + ":"
                                            path_str = parts[1].strip()

                                    resolved_path = (kardenwort_workspace / path_str).resolve()
                                    classify_args.append(f"{name}={prefix}{resolved_path}")

                        if classify_args:
                            classifications = load_classification_dictionaries(classify_args)
                            if classifications and col_lemma != -1:
                                class_updates = []
                                for name, c_dict in classifications.items():
                                    if name in role_fields and role_fields[name] in headers:
                                        col_idx = headers.index(role_fields[name])
                                        if (name, col_idx) not in class_cols:
                                            class_cols.append((name, col_idx))
                                        for r_idx in selected_row_indices:
                                            if r_idx < len(data_rows):
                                                row = data_rows[r_idx]
                                                sent_val, t_ord_val = selected_keys[r_idx]
                                                lemma_val = row[col_lemma].strip().lower()
                                                val = c_dict.get(lemma_val, "")
                                                while len(row) <= col_idx:
                                                    row.append("")
                                                row[col_idx] = val
                                                class_updates.append({"token_order": t_ord_val, "sentence_index": sent_val, "field": role_fields[name], "value": val})

                                if is_sqlite:
                                    if class_updates:
                                        storage_adapter.batch_update_words(session_zid=session_zid, updates_list=class_updates, zid=req_zid)
                                else:
                                    with storage_adapter.file_lock(tsv_path):
                                        storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            except Exception as e_class:
                logger.error(f"Failed to update classification fields during reword: {e_class}")

        occurrence_provenances = reword_query_provenances(storage_adapter, session_zid) if is_sqlite else {
            (str(sentence), str(token)): existing_row_provs.get(index, existing_row_provs.get(str(index), ''))
            for index, (sentence, token) in enumerate(reword.row_keys(data_rows, headers,
                role_fields.get('sentence_index', 'SentenceSourceIndex')))}
        if is_sqlite:
            existing_row_provs = {index: occurrence_provenances[tuple(map(str, key))]
                for index, key in enumerate(reword.row_keys(data_rows, headers,
                    role_fields.get('sentence_index', 'SentenceSourceIndex')))}
        new_fp = compute_content_fingerprint(data_rows)

        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["fingerprint"] = new_fp
                self.sessions[session_zid]["row_provenances"] = existing_row_provs
                self.sessions[session_zid]['headers'] = headers

        row_occurrences = reword_query_occurrences(storage_adapter, session_zid)
        with committed_delivery(session_zid, new_fp):
            safe_write_update_js(
                tsv_path,
                data_rows,
                headers,
                role_fields,
                stage="finished",
                status="success",
                zid=session_zid,
                config=self.config, resolved_paths=self.resolved_paths, storage_adapter=storage_adapter,
                class_cols=class_cols if class_cols else None,
                row_provenances=existing_row_provs,
                row_occurrences=row_occurrences,
                occurrence_provenances=occurrence_provenances,
            )

            structured_rows = format_update_rows_dict(
                data_rows,
                headers,
                role_fields,
                class_cols=class_cols if class_cols else None,
                row_provenances=existing_row_provs,
                row_occurrences=row_occurrences,
                occurrence_provenances=occurrence_provenances,
            )

            self.emit_event(session_zid, {
                "type": "update",
                "stage": "enrichment",
                "status": "success",
                "fingerprint": new_fp,
                "rows": structured_rows,
                "row_provenances": existing_row_provs,
                "rowProvenances": existing_row_provs,
            })

        return {
            "status": "success",
            "session_zid": session_zid,
            "fingerprint": new_fp,
            "data_rows": data_rows,
            "rows": structured_rows,
            "row_provenances": existing_row_provs,
            "rowProvenances": existing_row_provs,
        }

    def enqueue_progressive_translation(
        self,
        session_zid: str,
        language: str = "de",
        target_lang: str = "ru",
        text_mode: str = "single",
        prompt_name: str = "",
        skip_intellifiller: bool = False,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.enrichment_queue.enqueue_progressive_task(
            session_zid=session_zid,
            arbiter=self,
            language=language,
            target_lang=target_lang,
            text_mode=text_mode,
            prompt_name=prompt_name,
            skip_intellifiller=skip_intellifiller,
            zid=zid,
            trace_id=trace_id,
        )

    def propagate_translations_to_siblings(
        self,
        lemma_translations: Dict[str, str],
        exclude_session_zid: Optional[str] = None,
        language: str = "de",
        lemma_provenances: Optional[Dict[str, str]] = None,
    ):
        """
        Propagates newly translated lemmas simultaneously to all active sibling sessions and their subscribers.
        """
        if not lemma_translations:
            return

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
        results_dir = resolve_results_dir(self.resolved_paths, self.config)

        with self._lock:
            sibling_zids = [z for z in self.sessions.keys() if z != exclude_session_zid]

        for sib_zid in sibling_zids:
            with self._lock:
                sess = self.sessions.get(sib_zid)
                if not sess:
                    continue
                sess_lang = sess.get("language") or language
                if sess_lang != language:
                    continue
                data_rows = [list(r) for r in sess.get("data_rows", [])]
                headers = list(sess.get("headers", []))
                role_fields = dict(sess.get("role_fields", {}))
                row_provenances = dict(sess.get('row_provenances', {}))

            if not data_rows or not headers:
                continue

            col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
            col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1

            if col_lemma == -1 or col_word_dest == -1:
                continue

            stored_session = storage_adapter.db.get_session(sib_zid) if is_sqlite else None
            keys = reword.row_keys(data_rows, headers, role_fields.get('sentence_index', 'SentenceSourceIndex')) if stored_session else None
            modified = False
            updates = []
            for row_idx, row in enumerate(data_rows):
                if len(row) <= col_lemma:
                    continue
                lemma_val = row[col_lemma].strip()
                if lemma_val in lemma_translations:
                    trans_val = lemma_translations[lemma_val]
                    if trans_val and not is_unfinished_translation(trans_val):
                        while len(row) <= col_word_dest:
                            row.append("")
                        if is_unfinished_translation(row[col_word_dest]):
                            row[col_word_dest] = trans_val
                            modified = True
                            provenance = (lemma_provenances or {}).get(lemma_val)
                            if provenance:
                                row_provenances[row_idx] = provenance
                                row_provenances[str(row_idx)] = provenance
                            if keys is not None:
                                updates.append(dict(sentence_index=keys[row_idx][0], token_order=keys[row_idx][1], field='word_destination', value=trans_val))
                                if provenance:
                                    updates.append(dict(sentence_index=keys[row_idx][0], token_order=keys[row_idx][1], field='word_provenance', value=provenance))

            if modified:
                if is_sqlite:
                    storage_adapter.batch_update_words(session_zid=sib_zid, updates_list=updates, zid=sib_zid)
                else:
                    sib_tsv = find_working_tsv(results_dir, sib_zid, sess_lang, storage_adapter=storage_adapter)
                    if sib_tsv:
                        with storage_adapter.file_lock(sib_tsv):
                            comments, _, _ = storage_adapter.load_tsv_rows(sib_tsv)
                            storage_adapter.save_tsv_rows_safely(sib_tsv, comments, headers, data_rows)

                new_fp = compute_content_fingerprint(data_rows)
                with self._lock:
                    if sib_zid in self.sessions:
                        self.sessions[sib_zid]["data_rows"] = data_rows
                        self.sessions[sib_zid]["fingerprint"] = new_fp
                        self.sessions[sib_zid]['row_provenances'] = row_provenances

                sorted_rows = sort_session_data_rows(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                sentence_field = role_fields.get('sentence_index', 'SentenceSourceIndex')
                occurrence_provenances = reword.project_provenances(data_rows, headers, row_provenances,
                    sentence_field) if 'TokenOrder' in headers and sentence_field in headers else None
                structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields,
                    row_provenances=row_provenances, occurrence_provenances=occurrence_provenances)
                if not is_sqlite and sib_tsv:
                    safe_write_update_js(sib_tsv, sorted_rows, headers, role_fields, stage="translated", zid=sib_zid,
                        config=self.config, resolved_paths=self.resolved_paths, storage_adapter=storage_adapter, row_provenances=row_provenances, occurrence_provenances=occurrence_provenances)
                self.emit_event(sib_zid, {
                    "type": "update",
                    "stage": "translated",
                    "status": "success",
                    "fingerprint": new_fp,
                    "rows": structured_rows,
                })

    def _prepare_sqlite_retry_text(self, records, language, target, provider, req_zid, trace_id):
        from kardenwort_alignment import sentence_sources, align_retry_sentences
        source = sentence_sources(records)
        raw = translate_source_text(source, language, target, 'multi', self.config,
            self.resolved_paths, provider, zid=req_zid, trace_id=trace_id)
        main, padded = align_retry_sentences(raw, records)
        provenance = getattr(raw, 'provenance', None) or f'live:{provider}'
        html = format_translated_html(raw, text_mode='multi', text=source, config=self.config)
        return (main, padded, provenance), html

    @staticmethod
    def _commit_sqlite_retry(adapter, session_zid, updates, text_plan, req_zid):
        if not updates and not text_plan:
            return
        with adapter.db.transaction(zid=req_zid) as conn:
            adapter.db.batch_update_words(session_zid, updates, zid=req_zid, connection=conn)
            if text_plan:
                main, padded, provenance = text_plan
                adapter.persist_retext(session_zid, main, padded, provenance, req_zid, connection=conn)

    @operation_preferences
    def retry_session_rows(
        self,
        session_zid: str,
        row_ids: Optional[List[int]] = None,
        language: Optional[str] = None,
        target_lang: Optional[str] = None,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
        token_orders: Optional[List[Any]] = None,
        target_coordinates=None,
    ) -> Dict[str, Any]:
        try:
            reword.validate_session_targets(session_zid, target_coordinates)
        except ValueError as exc:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
        req_zid = zid or generate_unique_zid()
        eff_trace_id = trace_id or f"{session_zid}:retry"
        lang = language or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        target_l = target_lang or self.config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

        tsv_path = session_zid if is_sqlite else None
        with self._lock:
            if session_zid in self.sessions:
                sess = self.sessions[session_zid]
                if sess.get("language"):
                    lang = sess["language"]
                if sess.get("target_lang"):
                    target_l = sess["target_lang"]
                if not is_sqlite and sess.get("tsv_path"):
                    cand = Path(sess["tsv_path"])
                    if cand.exists() or is_sqlite:
                        tsv_path = cand

        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        if not tsv_path:
            tsv_path = find_working_tsv(results_dir, session_zid, lang, storage_adapter=storage_adapter)
            if not tsv_path:
                tsv_path = results_dir / f"{session_zid}.{lang}.tsv"

        if not is_sqlite and (not tsv_path or not tsv_path.exists()):
            raise StructuredError(ErrorCode.NOT_FOUND, f"Working session {session_zid} not found")

        comments, headers, data_rows = [], [], []
        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)

        mapping = load_anki_mapping(self.resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
        col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
        col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1

        # If session is active in-memory, preserve its exact data_rows order; otherwise sort initial load
        with self._lock:
            if session_zid in self.sessions and self.sessions[session_zid].get("data_rows"):
                data_rows = [list(r) for r in self.sessions[session_zid]["data_rows"]]
            else:
                data_rows = sort_session_data_rows(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)

        if col_lemma == -1:
            raise StructuredError(ErrorCode.DESK_FAILED, f"Lemma column not found for session {session_zid}")

        if is_sqlite:
            source = storage_adapter.restore_session(session_zid).get('source_text', '')
            selected = row_ids
            if row_ids is None and token_orders is None and target_coordinates is None:
                selected = [index for index, row in enumerate(data_rows)
                            if col_word_dest == -1 or is_unfinished_translation(
                                row[col_word_dest] if len(row) > col_word_dest else ''
                            )]
            try:
                selection = resolve_reword_request(data_rows, headers, role_fields, source, self.config,
                    targets=target_coordinates, selected_rows=selected, token_orders=token_orders)
            except ValueError as exc:
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
            target_indices = list(selection.rows)
            selected_keys = dict(zip(selection.rows, selection.keys))
        else:
            target_indices = []
            if token_orders is not None:
                token_orders_set = {str(to).strip() for to in token_orders if to is not None and str(to).strip() != ""}
                for idx, r in enumerate(data_rows):
                    t_ord = str(r[col_token_order]).strip() if col_token_order != -1 and len(r) > col_token_order else str(idx)
                    if t_ord in token_orders_set:
                        target_indices.append(idx)
            elif row_ids is not None:
                for r in row_ids:
                    if str(r).isdigit() and 0 <= int(r) < len(data_rows):
                        target_indices.append(int(r))
                    else:
                        r_str = str(r).strip()
                        for idx, row in enumerate(data_rows):
                            t_ord = str(row[col_token_order]).strip() if col_token_order != -1 and len(row) > col_token_order else str(idx)
                            if t_ord == r_str:
                                target_indices.append(idx)
            elif target_coordinates is not None:
                target_indices = []
            else:
                for idx, r in enumerate(data_rows):
                    if len(r) > col_lemma and r[col_lemma].strip():
                        dest_val = r[col_word_dest].strip() if col_word_dest != -1 and len(r) > col_word_dest else ""
                        if is_unfinished_translation(dest_val):
                            target_indices.append(idx)

            target_indices = list(dict.fromkeys(target_indices))

        lemmas_to_retry = []
        seen = set()
        for idx in target_indices:
            row = data_rows[idx]
            if len(row) > col_lemma and row[col_lemma].strip():
                l_val = row[col_lemma].strip()
                if l_val not in seen:
                    seen.add(l_val)
                    lemmas_to_retry.append(l_val)

        translated_map = {}
        fast_prov = None
        if lemmas_to_retry:
            lemma_chain, _ = resolve_provider_chain(self.config, task_type='lemma') if self.config else (['google'], 'chain')
            provider = lemma_chain[0] if lemma_chain else 'google'
            try:
                translated_map = translate_lemmas_fast_path(
                    lemmas_to_retry,
                    lang,
                    target_l,
                    self.config,
                    self.resolved_paths,
                    provider,
                )
                reword.fast_provider_results(lemmas_to_retry, translated_map)
                fast_prov = getattr(translated_map, 'provenance', None) or f"live:{provider}"
            except Exception as e:
                logger.error(f"Retry translation failed: {e}")
                raise StructuredError(ErrorCode.DESK_FAILED, f"Retry translation failed: {e}") from e

        updates = []
        if translated_map:
            if col_word_dest == -1:
                headers.append(role_fields.get('word_translation', 'WordDestination'))
                col_word_dest = len(headers) - 1
                for r in data_rows:
                    r.append("")

            for row_idx in target_indices:
                row = data_rows[row_idx]
                if len(row) > col_lemma:
                    l_val = row[col_lemma].strip()
                    if l_val in translated_map:
                        t_val = translated_map[l_val]
                        if t_val and str(t_val).strip():
                            while len(row) <= col_word_dest:
                                row.append("")
                            row[col_word_dest] = t_val
                            if is_sqlite:
                                updates.append(dict(sentence_index=selected_keys[row_idx][0],
                                    token_order=selected_keys[row_idx][1], updates=dict(word_destination=t_val, word_provenance=fast_prov)))

            if not is_sqlite:
                with storage_adapter.file_lock(tsv_path):
                    storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                self.propagate_translations_to_siblings(translated_map, exclude_session_zid=session_zid, language=lang)

        translated_text_res = None
        retry_text_plan = None
        need_sentence_trans = False
        text_provider = None
        if row_ids is None and token_orders is None and target_coordinates is None:
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
            col_sentence_dest2 = headers.index('SentenceDestination2') if 'SentenceDestination2' in headers else (headers.index(role_fields['sentence_destination2']) if 'sentence_destination2' in role_fields and role_fields['sentence_destination2'] in headers else -1)
            col_sentence_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1

            source_raw_text = ""
            text_mode = 'single'
            if is_sqlite:
                from kardenwort_alignment import unresolved_sentences
                retry_records = unresolved_sentences(self._read_retext_sentences(storage_adapter, session_zid))
                restored = storage_adapter.restore_session(session_zid)
                source_raw_text = restored.get("source_raw_text") or restored.get("source_text", "")
                text_mode = restored.get("text_mode") or 'single'
            else:
                txt_cand = tsv_path.with_suffix('.txt') if tsv_path else None
                if txt_cand and txt_cand.exists():
                    source_raw_text = txt_cand.read_text(encoding='utf-8')

            need_sentence_trans = False
            if is_sqlite:
                need_sentence_trans = bool(retry_records)
            elif source_raw_text and col_sentence_dest != -1:
                if not any(len(r) > col_sentence_dest and r[col_sentence_dest].strip() for r in data_rows):
                    need_sentence_trans = True

            if need_sentence_trans and source_raw_text:
                text_chain, _ = resolve_provider_chain(self.config, task_type='text') if self.config else (['google'], 'chain')
                text_provider = text_chain[0] if text_chain else 'google'
                try:
                    if is_sqlite:
                        from kardenwort_alignment import patch_translation_rows
                        retry_text_plan, translated_text_res = self._prepare_sqlite_retry_text(
                            retry_records, lang, target_l, text_provider, req_zid, eff_trace_id)
                        data_rows = patch_translation_rows(data_rows, headers, role_fields,
                            retry_text_plan[0], retry_text_plan[1])
                    else:
                        sentence_translations_raw = translate_source_text(
                            source_raw_text, lang, target_l, text_mode, self.config, self.resolved_paths, text_provider, zid=req_zid, trace_id=eff_trace_id)
                        resolve_translations(
                            source_raw_text, text_mode, data_rows, col_sentence_index, col_sentence_dest,
                            sentence_translations_raw, tsv_path, comments, headers,
                            col_sentence_dest2=col_sentence_dest2, persist=True, return_single=False)
                        translated_text_res = format_translated_html(sentence_translations_raw, text_mode=text_mode, text=source_raw_text, config=self.config)
                except Exception as text_err:
                    if is_sqlite:
                        raise StructuredError(ErrorCode.DESK_FAILED, f'Sentence translation retry failed: {text_err}') from text_err
                    logger.warning(f"Sentence translation retry failed: {text_err}")

        if is_sqlite:
            self._commit_sqlite_retry(storage_adapter, session_zid, updates, retry_text_plan, req_zid)

        retried_row_provenances = {}
        if fast_prov:
            for row_idx in target_indices:
                if 0 <= row_idx < len(data_rows):
                    row = data_rows[row_idx]
                    retried_row_provenances[row_idx] = fast_prov
                    retried_row_provenances[str(row_idx)] = fast_prov
                    if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).strip():
                        t_ord_str = str(row[col_token_order]).strip()
                        retried_row_provenances[t_ord_str] = fast_prov
                        if t_ord_str.isdigit():
                            retried_row_provenances[int(t_ord_str)] = fast_prov

        new_fp = compute_content_fingerprint(data_rows)
        if is_sqlite and translated_map:
            with committed_delivery(session_zid, new_fp):
                self.propagate_translations_to_siblings(translated_map, exclude_session_zid=session_zid, language=lang)
        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["headers"] = headers
                self.sessions[session_zid]["fingerprint"] = new_fp
                if retried_row_provenances:
                    if "row_provenances" not in self.sessions[session_zid]:
                        self.sessions[session_zid]["row_provenances"] = {}
                    self.sessions[session_zid]["row_provenances"].update(retried_row_provenances)

        with self._lock:
            is_active_session = (session_zid in self.sessions)

        if is_active_session or (row_ids is not None and len(row_ids) > 0) or (token_orders is not None and len(token_orders) > 0):
            display_rows = data_rows
        else:
            display_rows = sort_session_data_rows(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)

        structured_rows = format_storage_update_rows(storage_adapter, session_zid, display_rows, headers, role_fields, retried_row_provenances)
        effective_text_prov = retry_text_plan[2] if retry_text_plan else (f"live:{text_provider}" if (need_sentence_trans and translated_text_res) else None)

        if not is_sqlite and tsv_path:
            safe_write_update_js(tsv_path, display_rows, headers, role_fields, stage="translated", status="success", translated_text=translated_text_res, text_provenance=effective_text_prov, zid=session_zid, trace_id=eff_trace_id, row_provenances=retried_row_provenances, config=self.config, resolved_paths=self.resolved_paths, storage_adapter=storage_adapter)

        event_payload = {
            "type": "update",
            "stage": "translated",
            "status": "success",
            "fingerprint": new_fp,
            "rows": structured_rows,
            "retried_rows": target_indices,
            "row_provenances": retried_row_provenances,
            "rowProvenances": retried_row_provenances,
        }
        if translated_text_res is not None:
            event_payload["translated_text"] = translated_text_res
            event_payload["translatedText"] = translated_text_res
        if effective_text_prov:
            event_payload["text_provenance"] = effective_text_prov
            event_payload["textProvenance"] = effective_text_prov
        if retry_text_plan:
            event_payload['sentences'] = retry_text_plan[0]
        with committed_delivery(session_zid, new_fp):
            self.emit_event(session_zid, event_payload)

        res_payload = {
            "status": "success",
            "session_zid": session_zid,
            "fingerprint": new_fp,
            "retried_rows": target_indices,
            "data_rows": data_rows,
            "rows": structured_rows,
            "row_provenances": retried_row_provenances,
            "rowProvenances": retried_row_provenances,
        }
        if translated_text_res is not None:
            res_payload["translated_text"] = translated_text_res
            res_payload["translatedText"] = translated_text_res
        if effective_text_prov:
            res_payload["text_provenance"] = effective_text_prov
            res_payload["textProvenance"] = effective_text_prov
        if retry_text_plan:
            res_payload['sentences'] = retry_text_plan[0]
        return res_payload

    def get_queue_status(self) -> Dict[str, Any]:
        return self.enrichment_queue.get_status()


# ---------------------------------------------------------------------------
# Controller HTTP Request Handler
# ---------------------------------------------------------------------------
def generate_server_zid(server) -> str:
    now = datetime.now()
    if hasattr(server, 'seq_lock') and hasattr(server, 'seq_counter'):
        with server.seq_lock:
            server.seq_counter = (server.seq_counter + 1) % 10000
            seq = server.seq_counter
    else:
        seq = 0
    return f"{now:%Y%m%d%H%M%S}-{seq:04d}"


def request_preference_scope(path):
    return path.startswith('/session') or path in ('/', '/api/v1/render', '/lookup', '/verify-language')


class ControllerRequestHandler(BaseHTTPRequestHandler):
    """
    Central Controller daemon request handler supporting REST API, process supervision,
    and Server-Sent Events (SSE) streaming.
    """

    def setup(self):
        super().setup()
        self.connection.settimeout(30.0)

    def address_string(self):
        # Override to bypass reverse DNS lookups (<1ms localhost dispatch)
        return self.client_address[0]

    def log_message(self, format_str, *args):
        # Suppress access logs for health probes to avoid log spam
        path = getattr(self, 'path', '')
        if path and ('/health' in path or '/events' in path):
            return
        logger.info("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format_str % args))

    def _send_cors_headers(self):
        origin = self.headers.get('Origin')
        if origin and self._origin_allowed(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Headers', 'X-API-Token, X-ZID, Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    @staticmethod
    def _origin_allowed(origin):
        if origin == 'null':
            return True  # Opaque article requests additionally require a token header.
        parsed = urllib.parse.urlparse(origin)
        if parsed.scheme not in ('http', 'https'):
            return False
        if parsed.hostname == 'localhost':
            return True
        try:
            return ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            return False

    def _validate_request_origin(self):
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            raise StructuredError(ErrorCode.UNAUTHORIZED, 'Controller requires a loopback client')
        origin = self.headers.get('Origin')
        if origin and not self._origin_allowed(origin):
            raise StructuredError(ErrorCode.UNAUTHORIZED, 'Article origin is not allowed')
        if origin == 'null' and self.command != 'OPTIONS':
            self._authenticate_token()

    def do_OPTIONS(self):
        try:
            self._validate_request_origin()
        except StructuredError as error:
            self._send_error_json(403, error.error_code, error.message)
            return
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_json(self, status_code: int, data_obj: dict, *, credential_bearing: bool = False):
        body = json.dumps({"status": "success", "data": data_obj}, ensure_ascii=False).encode('utf-8')
        self._send_bytes(status_code, body, 'application/json; charset=utf-8', credential_bearing=credential_bearing)

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
        self._send_bytes(status_code, body, 'application/json; charset=utf-8')

    def _send_credential_cors_headers(self):
        origin = self._single_header('Origin')
        host_val = self._single_header('Host')
        if isinstance(origin, str) and isinstance(host_val, str):
            host_l = host_val.lower()
            if origin == 'null' or origin.lower() in (f'http://{host_l}', f'https://{host_l}'):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Headers', 'X-API-Token, X-ZID, Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def _send_bytes(self, status_code, body, content_type, *, credential_bearing: bool = False):
        try:
            self.send_response(status_code)
            if credential_bearing:
                self._send_credential_cors_headers()
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
            else:
                self._send_cors_headers()
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            if content_type.startswith('audio/'):
                self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True
            logger.debug('Peer disconnected during response to %s', self.path)

    def _send_credential_html(self, html_text: str):
        body = html_text.encode('utf-8')
        try:
            self.send_response(200)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True
            logger.debug('Peer disconnected during HTML response to %s', self.path)

    def _audio_request(self, path):
        self._authenticate_token(require_configured=True)
        body = self._read_json_body()
        try:
            result = execute_audio(body, self.server.resolved_paths, self.server.config,
                self.connection, WindowsJobObject, synthesize=path == '/api/v1/audio/synthesize')
        except AudioError as error:
            raise StructuredError(ErrorCode(error.code), str(error)) from error
        if isinstance(result, dict):
            self._send_json(200, result)
        else:
            data, content_type = result
            self._send_bytes(200, data, content_type)

    def _state_request(self, path, method):
        body = self._read_json_body() if method == 'POST' else {}
        if method != 'GET':
            self._authenticate_token(body, require_configured=True)
        try:
            if not getattr(self.server, 'ui_cfg', None):
                self.server.ui_cfg = resolve_ui_config(self.server.config)
            result = dispatch_state(self.server, path, method, body)
        except PreferenceConflict as error:
            self._send_json(409, dict(ok=False, error_code='ROW_STALE', current=error.current))
            return
        except ValueError as error:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, str(error)) from error
        except (sqlite3.Error, QueryExecutionError) as error:
            raise StructuredError(ErrorCode.CONFIGURATION_ERROR, 'SQLite state is unavailable') from error
        self._project_state_effect(path, result)
        self._send_json(200, result)

    def _project_state_effect(self, path, result):
        if path == '/api/v1/set-language':
            spawn_ahk(['--set-language', result['language']], base_dir=self.server.resolved_paths.get('base_dir'))

    def _read_json_body(self) -> dict:
        content_length_str = self.headers.get('Content-Length', '0')
        try:
            content_length = int(content_length_str)
        except ValueError:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, "Invalid Content-Length header")

        if content_length > 1024 * 1024:  # 1MB limit
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, "Payload size exceeds 1MB limit")

        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode('utf-8'))
        except Exception as e:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, f"Malformed JSON payload: {e}")

    def _extract_provided_token(self, body_data=None, query_params=None):
        provided_token = self.headers.get('X-API-Token') or self.headers.get('X-API-Key')
        if not provided_token:
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                provided_token = auth_header[7:].strip()
        if not provided_token and body_data and isinstance(body_data, dict):
            provided_token = body_data.get('token')
        if not provided_token and query_params and isinstance(query_params, dict):
            token_list = query_params.get('token', []) or query_params.get('api_token', [])
            if token_list:
                provided_token = token_list[0]
        return provided_token

    def _single_header(self, name: str):
        values = self.headers.get_all(name)
        if not values:
            return None
        if len(values) != 1:
            return False
        return values[0].strip()

    def _allowed_controller_hosts(self) -> set:
        port = getattr(self.server, 'server_port', None) or (
            self.server.server_address[1] if hasattr(self.server, 'server_address') else 18335
        )
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}
        bound = self.server.server_address[0] if hasattr(self.server, 'server_address') else ''
        if bound and bound not in ('0.0.0.0', '::', ''):
            hosts.add(f'{bound.lower()}:{port}')
        return hosts

    def _is_valid_browser_document_navigation(self, method: str, host_val: str) -> bool:
        if method != 'GET':
            return False
        mode = self._single_header('Sec-Fetch-Mode')
        dest = self._single_header('Sec-Fetch-Dest')
        site = self._single_header('Sec-Fetch-Site')
        if mode != 'navigate' or dest != 'document' or site not in ('none', 'same-origin'):
            return False
        origin = self._single_header('Origin')
        if origin is False:
            return False
        if origin is not None:
            return origin.lower() in (f'http://{host_val}', f'https://{host_val}')
        return True

    def _require_credential_admission(self, method: str, body_data=None, query_params=None, *, allow_browser_nav: bool) -> str:
        host_raw = self._single_header('Host')
        if not isinstance(host_raw, str) or host_raw.lower() not in self._allowed_controller_hosts():
            raise StructuredError(ErrorCode.UNAUTHORIZED, "Invalid or ambiguous controller Host header")
        host_val = host_raw.lower()

        api_key = str(getattr(self.server, 'api_key', '') or '').strip()
        if not api_key:
            if not allow_browser_nav:
                raise StructuredError(ErrorCode.TOKEN_NOT_CONFIGURED, "Controller authentication token is not configured")
            if not self._is_valid_browser_document_navigation(method, host_val):
                raise StructuredError(ErrorCode.UNAUTHORIZED, "Document request requires own-origin browser navigation")
            return ""

        provided_token = self._extract_provided_token(body_data=body_data, query_params=query_params)
        if provided_token:
            import hmac
            if not hmac.compare_digest(str(provided_token).strip(), api_key):
                raise StructuredError(ErrorCode.UNAUTHORIZED, "Invalid or missing API authentication token")
            return api_key

        if allow_browser_nav and self._is_valid_browser_document_navigation(method, host_val):
            return api_key

        raise StructuredError(ErrorCode.UNAUTHORIZED, "Invalid or missing API authentication token")

    def _authenticate_token(self, body_data=None, query_params=None, *, require_configured: bool = False):
        api_key = str(getattr(self.server, 'api_key', '') or '').strip()
        if not api_key:
            if require_configured:
                raise StructuredError(ErrorCode.TOKEN_NOT_CONFIGURED, "Controller authentication token is not configured")
            return

        provided_token = self._extract_provided_token(body_data=body_data, query_params=query_params)

        import hmac
        if not provided_token or not hmac.compare_digest(str(provided_token).strip(), api_key):
            raise StructuredError(ErrorCode.UNAUTHORIZED, "Invalid or missing API authentication token")

    def _serve_static_file(self, file_path: Path):
        if not file_path.is_file():
            raise StructuredError(ErrorCode.NOT_FOUND, f"File not found: {file_path.name}")

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

        self._send_bytes(200, content, content_type)

    def do_GET(self):
        try:
            self._dispatch_route('GET')
        except StructuredError as se:
            status_code = ERROR_STATUS_MATRIX.get(se.error_code, 500)
            self._send_error_json(status_code, se.error_code, se.message, se.details)
        except Exception as e:
            logger.error(f"Unhandled Controller error on GET {self.path}: {e}\n{traceback.format_exc()}")
            self._send_error_json(500, ErrorCode.SERVER_ERROR, f"Internal server error: {e}")

    def do_POST(self):
        try:
            self._dispatch_route('POST')
        except StructuredError as se:
            status_code = ERROR_STATUS_MATRIX.get(se.error_code, 500)
            self._send_error_json(status_code, se.error_code, se.message, se.details)
        except Exception as e:
            logger.error(f"Unhandled Controller error on POST {self.path}: {e}\n{traceback.format_exc()}")
            self._send_error_json(500, ErrorCode.SERVER_ERROR, f"Internal server error: {e}")

    def _event_dispatcher(self):
        return getattr(self.server, 'streams', None) or EventDispatcher.get_instance()

    def _handle_sse_events(self, zid: str):
        """Streams Server-Sent Events (SSE) for the specified session ZID."""
        dispatcher = self._event_dispatcher()
        q = dispatcher.register(zid)

        try:
            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            init_data = json.dumps({"type": "connected", "session_zid": zid, "time": time.time()})
            self.wfile.write(f"data: {init_data}\n\n".encode('utf-8'))
            self.wfile.flush()
            stopping = getattr(dispatcher, 'stopping', None)
            while stopping is None or not stopping.is_set():
                try:
                    event = q.get(timeout=10.0)
                    msg = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    self.wfile.write(msg.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    # Heartbeat comment to detect client disconnection
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError):
            self.close_connection = True
            logger.debug('Peer disconnected during event stream for %s', zid)
        finally:
            dispatcher.unregister(zid, q)

    def _snapshot_session_manifest(self, session_zid: str, language: Optional[str] = None):
        arbiter = getattr(self.server, 'arbiter', None)
        if not arbiter:
            return None
        with arbiter._lock:
            sess = arbiter.sessions.get(session_zid)
            if not sess or not sess.get("headers") or sess.get("data_rows") is None:
                return None
            return (
                list(sess["headers"]),
                list(map(list, sess["data_rows"])),
                sess.get("language") or language,
                sess.get("role_fields"),
            )

    def _session_rendered_manifest(self, session_zid: str, language: Optional[str] = None):
        snap = self._snapshot_session_manifest(session_zid, language)
        if not snap:
            return None
        headers, rows, sess_lang, role_fields = snap
        cfg = operation_configuration(self.server.config)
        display_rows = sort_session_data_rows(
            rows, headers, sess_lang, cfg, self.server.resolved_paths, role_fields=role_fields
        )
        return headers, display_rows

    def _parse_export_request(self, body: Dict[str, Any], require_selected_field: bool) -> Dict[str, Any]:
        session_zid = body.get('session_zid')
        if not session_zid:
            raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
        if require_selected_field:
            if 'selected_row_ids' not in body:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required payload field: 'selected_row_ids'")
            req_zid = generate_server_zid(self.server)
            trace_id = None
        else:
            req_zid = body.get('zid') or session_zid
            trace_id = f"{session_zid}:export:selection"
        selected_rows = body.get('selected_row_ids')
        if selected_rows is None:
            selected_rows = body.get('row_ids')
        return {
            "session_zid": session_zid,
            "selected_row_ids": selected_rows,
            "req_zid": req_zid,
            "trace_id": trace_id,
            "language": body.get('language'),
        }

    def _handle_export_request(self, method: str, path: str, *, require_selected_field: bool):
        if method != 'POST':
            raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
        body = self._read_json_body()
        self._authenticate_token(body, require_configured=True)
        params = self._parse_export_request(body, require_selected_field)
        res = core_export(
            tsv_path_or_session=params["session_zid"],
            selected_row_ids=params["selected_row_ids"],
            config=operation_configuration(self.server.config),
            resolved_paths=self.server.resolved_paths,
            fingerprint=body.get('fingerprint'),
            zid=params["req_zid"],
            language=params["language"],
            trace_id=params["trace_id"],
            target_coordinates=body.get('target_coordinates'),
            token_orders=body.get('token_orders'),
            visual_indices=body.get('visual_indices'),
            rendered_manifest=self._session_rendered_manifest(params["session_zid"], params["language"]),
        )
        self._send_json(200, res)

    @request_preferences(request_preference_scope)
    def _dispatch_route(self, method: str):
        self._validate_request_origin()
        EventDispatcher.get_instance().controller_owned = True
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        qs = urllib.parse.parse_qs(parsed_url.query)
        if path in STATE_ROUTES:
            return self._state_request(path, method)

        ctrl_mod = sys.modules.get('kardenwort_controller')
        http_mod = sys.modules.get('http_server')
        render_flow_fn = ctrl_mod.run_render_flow if (ctrl_mod and hasattr(ctrl_mod, 'run_render_flow')) else run_render_flow
        if http_mod and hasattr(http_mod, 'run_render_flow') and getattr(http_mod.run_render_flow, '__module__', None) != 'http_server':
            render_flow_fn = http_mod.run_render_flow

        # 1. Health Probe (Controller + Supervisor Sidecars + Database)
        if path in ('/health', '/api/v1/health'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            uptime = round(time.time() - getattr(self.server, 'start_time', time.time()), 2)
            services_status = {}
            supervisor_report = {}
            if hasattr(self.server, 'supervisor') and self.server.supervisor:
                if hasattr(self.server.supervisor, 'get_service_status'):
                    services_status = self.server.supervisor.get_service_status()
                if hasattr(self.server.supervisor, 'get_status_report'):
                    supervisor_report = self.server.supervisor.get_status_report()

            db_status = {}
            try:
                from kardenwort_db import KardenwortDB
                db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
                db_status = db.get_status()
            except Exception as e:
                db_status = {"ok": False, "error": str(e)}

            ctrl_port = getattr(self.server, 'server_port', None)
            if ctrl_port is None:
                ctrl_port = self.server.server_address[1] if hasattr(self.server, 'server_address') else 18335

            # Extract argos warmup status from translation service if available
            argos_warmup = "unavailable"
            trans_svc = services_status.get("translation", {})
            if isinstance(trans_svc, dict) and trans_svc.get("argos"):
                argos_warmup = trans_svc["argos"]
            elif isinstance(trans_svc, dict) and trans_svc.get("healthy"):
                argos_warmup = "available"

            self._send_json(200, {
                "ok": True,
                "status": "running",
                "controller": {
                    "port": ctrl_port,
                    "uptime_seconds": uptime
                },
                "services": services_status,
                "models": {
                    "argos": argos_warmup
                },
                "sidecars": supervisor_report,
                "database": db_status
            })
            return

        # 2. Server-Sent Events (SSE) Stream
        if path in ('/events', '/api/v1/events'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token()
            zid = qs.get('session', [''])[0] or qs.get('zid', [''])[0]
            if not zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'session' or 'zid' query parameter for SSE stream")
            self._handle_sse_events(zid)
            return

        # 3. Session Endpoints
        if path == '/session/create':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            text = body.get('text', '')
            language = body.get('language', '')
            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'text' in payload")
            if not language:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'language' in payload")

            res = self.server.arbiter.create_session(
                text=text,
                language=language,
                target_lang=body.get('target_lang'),
                text_mode=body.get('text_mode', 'single'),
                sections=body.get('sections'),
                theme=body.get('theme'),
                zid=body.get('zid'),
                bypass_lang_check=body.get('bypass_lang_check', False)
            )
            self._send_json(200, res)
            return

        if path == '/internal/selection':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, 'Selection query requires POST')
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            if not isinstance(body, dict):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, 'Selection query requires an object')
            session_zid = body.get('session_zid')
            if not isinstance(session_zid, str) or len(session_zid) != 14 or not session_zid.isascii() or not session_zid.isdigit() or session_zid == '0' * 14:
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, 'Selection query requires a session')
            membership = {}
            if not body.get('close'):
                adapter = getattr(self.server.arbiter, 'storage_adapter', None) or get_storage_adapter(
                    operation_configuration(self.server.config), self.server.resolved_paths, read_only=True)
                membership = adapter.get_shared_selection(session_zid)
            events = []
            client_id = body.get('client_id')
            if client_id:
                events = EventDispatcher.get_instance().poll_events(
                    session_zid, str(client_id), accept=body.get('accept_updates', True), close=body.get('close', False))
            result = {'ok': True, 'shared_selection': membership, 'events': events}
            if body.get('recover') and not body.get('close'):
                result['snapshot'] = committed_recovery_snapshot(self.server.arbiter, session_zid)
            self._send_json(200, result)
            return

        if path == '/internal/stage':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, 'Stage ingress requires POST')
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            session_zid = body.get('session_zid')
            event = body.get('event')
            if not session_zid or not isinstance(event, dict):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, 'Stage ingress requires a session and event')
            if event.get('session_zid', session_zid) != session_zid:
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, 'Stage session identity mismatch')
            self.server.arbiter.emit_event(session_zid, event)
            self._send_json(200, {'ok': True})
            return

        if path == '/session/save':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            session_zid = body.get('session_zid')
            deltas = body.get('deltas', [])
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            res = self.server.arbiter.save_session(
                session_zid=session_zid,
                deltas=deltas,
                fingerprint=body.get('fingerprint'),
                language=body.get('language'),
                zid=body.get('zid')
            )
            self._send_json(200, res)
            return

        if path == '/session/retext':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            session_zid = body.get('session_zid')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            res = self.server.arbiter.retext_session(
                session_zid=session_zid,
                language=body.get('language'),
                text_mode=body.get('text_mode', 'single'),
                zid=body.get('zid')
            )
            self._send_json(200, res)
            return

        if path == '/session/reword':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            session_zid = body.get('session_zid')
            selected_rows = body.get('row_ids') or body.get('selected_rows') or []
            token_orders = body.get('token_orders')
            target_coordinates = body.get('target_coordinates')
            visual_indices = body.get('visual_indices')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            res = self.server.arbiter.reword_session(
                session_zid=session_zid,
                selected_rows=selected_rows,
                prompt=body.get('prompt'),
                language=body.get('language'),
                zid=body.get('zid'),
                token_orders=token_orders,
                target_coordinates=target_coordinates,
                visual_indices=visual_indices,
                sentence_idx=body.get('sentence_idx'),
            )
            self._send_json(200, res)
            return

        if path == '/session/retry':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            session_zid = body.get('session_zid') or body.get('zid')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            row_ids = body.get('row_ids', body.get('selected_rows'))
            if isinstance(row_ids, (int, str)) and not isinstance(row_ids, bool) and str(row_ids).isdigit():
                row_ids = [int(row_ids)]
            token_orders = body.get('token_orders')

            res = self.server.arbiter.retry_session_rows(
                session_zid=session_zid,
                row_ids=row_ids,
                language=body.get('language'),
                target_lang=body.get('target_lang'),
                zid=body.get('zid'),
                trace_id=body.get('trace_id'),
                token_orders=token_orders,
                target_coordinates=body.get('target_coordinates'),
            )
            self._send_json(200, res)
            return

        if path == '/session/export':
            self._handle_export_request(method, path, require_selected_field=False)
            return

        if path == '/session/progressive/enqueue':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            from kardenwort_desk import progressive_storage_owner
            if 'storage_owner' in body and body['storage_owner'] != progressive_storage_owner(operation_configuration(self.server.config), self.server.resolved_paths):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, 'Progressive storage owner differs from controller')

            session_zid = body.get('session_zid') or body.get('zid')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            res = self.server.arbiter.enqueue_progressive_translation(
                session_zid=session_zid,
                language=body.get('language') or 'de',
                target_lang=body.get('target_lang') or 'ru',
                text_mode=body.get('text_mode', 'single'),
                prompt_name=body.get('prompt', ''),
                skip_intellifiller=body.get('skip_intellifiller', False),
                zid=body.get('zid'),
                trace_id=body.get('trace_id'),
            )
            self._send_json(200, res)
            return

        if path in ('/api/v1/queue/status', '/session/queue/status', '/queue/status'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            res = self.server.arbiter.get_queue_status()
            self._send_json(200, res)
            return

        worker_status_match = re.match(r"^/(?:api/v1/)?session(?:s)?/([0-9a-zA-Z_\-]+)/worker_status$", path)
        if worker_status_match:
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            target_zid = worker_status_match.group(1)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            status_dict = db.get_worker_status(target_zid)
            self._send_json(200, status_dict)
            return

        if path == '/session/status':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token()
            zid = qs.get('zid', [''])[0]
            if not zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'zid' query parameter")
            if qs.get('committed') == ['1']:
                self._send_json(200, committed_recovery_snapshot(self.server.arbiter, zid))
                return

            mapping_path = None
            if self.server.resolved_paths and "anki_mapping_file" in self.server.resolved_paths:
                mapping_path = Path(self.server.resolved_paths["anki_mapping_file"])
            elif operation_configuration(self.server.config) and hasattr(operation_configuration(self.server.config), "get"):
                raw_mp = operation_configuration(self.server.config).get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
                mapping_path = Path(raw_mp)
            mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None

            sess = None
            if hasattr(self.server, 'arbiter') and self.server.arbiter:
                with self.server.arbiter._lock:
                    sess = self.server.arbiter.sessions.get(zid)
            if sess:
                safe_sess = {k: v for k, v in sess.items() if k != "lock"}
                headers = safe_sess.get("headers", [])
                data_rows = safe_sess.get("data_rows", [])
                sess_lang = safe_sess.get("language") or safe_sess.get("lang") or "de"
                role_fields = get_role_fields(mapping, headers) if mapping else {}
                sess_row_provs = safe_sess.get("row_provenances")
                if sess_row_provs is None:
                    sess_row_provs = {}
                storage_adapter = getattr(self.server.arbiter, 'storage_adapter', None) or get_storage_adapter(
                    operation_configuration(self.server.config), self.server.resolved_paths, read_only=True)
                safe_sess["rows"] = format_storage_update_rows(storage_adapter, zid, data_rows, headers,
                    role_fields, sess_row_provs)
                safe_sess["row_provenances"] = sess_row_provs
                safe_sess["rowProvenances"] = sess_row_provs
                # Cold-cache status fallback: query SQLite sentences table if text_provenance is missing
                if not safe_sess.get("text_provenance") and not safe_sess.get("textProvenance"):
                    if hasattr(self.server, 'resolved_paths') and hasattr(self.server, 'config'):
                        storage_adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                        if getattr(storage_adapter, 'backend_name', '') == 'sqlite' and hasattr(storage_adapter, 'db'):
                            try:
                                db_sents = storage_adapter.db.get_sentences_by_session(zid)
                                if db_sents:
                                    eff_prov = next((s.get("text_provenance") for s in db_sents if s.get("text_provenance")), None)
                                    if eff_prov:
                                        safe_sess["text_provenance"] = eff_prov
                                        safe_sess["textProvenance"] = eff_prov
                            except Exception:
                                pass

                if safe_sess.get("text_provenance"):
                    safe_sess["textProvenance"] = safe_sess["text_provenance"]
                elif safe_sess.get("textProvenance"):
                    safe_sess["text_provenance"] = safe_sess["textProvenance"]
                else:
                    safe_sess["text_provenance"] = None
                    safe_sess["textProvenance"] = None
                if "translatedText" not in safe_sess or not safe_sess["translatedText"]:
                    st = safe_sess.get("sentence_translation")
                    if st:
                        safe_sess["translatedText"] = format_translated_html(
                            st,
                            text_mode=safe_sess.get("text_mode", "single"),
                            text=safe_sess.get("text", ""),
                            config=operation_configuration(self.server.config),
                        )
                    else:
                        # Extract sentence_destination from SQLite sentences when in-memory cache lacks translatedText
                        if hasattr(self.server, 'resolved_paths') and hasattr(self.server, 'config'):
                            storage_adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                            if getattr(storage_adapter, 'backend_name', '') == 'sqlite' and hasattr(storage_adapter, 'db'):
                                try:
                                    db_sents = storage_adapter.db.get_sentences_by_session(zid)
                                    if db_sents:
                                        s_srcs, s_dests = pair_sentence_records(db_sents)
                                        if s_dests:
                                            st_mode = "multi" if len(s_dests) >= 2 else safe_sess.get("text_mode", "single")
                                            st_text = "\n".join(s_srcs) if len(s_srcs) >= 2 else (safe_sess.get("text", "") or " ".join(s_srcs))
                                            formatted_tt = format_translated_html(
                                                s_dests,
                                                text_mode=st_mode,
                                                text=st_text,
                                                config=operation_configuration(self.server.config),
                                            )
                                            safe_sess["translatedText"] = formatted_tt
                                except Exception:
                                    pass
                        if not safe_sess.get("translatedText"):
                            safe_sess["translatedText"] = ""

                is_busy = False
                if hasattr(self.server, 'arbiter') and self.server.arbiter:
                    eq = getattr(self.server.arbiter, 'enrichment_queue', None)
                    if eq and hasattr(eq, '_active_progressive_sessions'):
                        with eq._lock:
                            if zid in eq._active_progressive_sessions:
                                is_busy = True
                safe_sess["ok"] = True
                safe_sess["zid"] = zid
                safe_sess["session_zid"] = zid
                safe_sess["is_finished"] = not is_busy
                safe_sess["stage"] = "translating" if is_busy else "finished"

                stored_provs = get_session_active_providers(zid, config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths) if 'get_session_active_providers' in globals() else {}
                active_text_p = safe_sess.get("active_text_provider") or stored_provs.get("active_text_provider")
                active_lemma_p = safe_sess.get("active_lemma_provider") or stored_provs.get("active_lemma_provider")
                active_p = safe_sess.get("active_provider") or stored_provs.get("active_provider")

                if not active_text_p:
                    tp = safe_sess.get("text_provenance") or safe_sess.get("textProvenance")
                    if tp and str(tp).startswith("live:"):
                        active_text_p = str(tp)[5:].strip().lower()
                if not active_lemma_p:
                    for rp in sess_row_provs.values():
                        if rp and str(rp).startswith("live:"):
                            active_lemma_p = str(rp)[5:].strip().lower()
                            break
                if not active_p:
                    active_p = active_lemma_p or active_text_p

                safe_sess["active_text_provider"] = active_text_p
                safe_sess["active_lemma_provider"] = active_lemma_p
                safe_sess["active_provider"] = active_p
                safe_sess["status"] = {
                    "is_finished": not is_busy,
                    "stage": "translating" if is_busy else "finished",
                    "active_provider": active_p,
                    "active_text_provider": active_text_p,
                    "active_lemma_provider": active_lemma_p,
                }

                if "sentences" not in safe_sess or not safe_sess["sentences"]:
                    s_list = []
                    if hasattr(self.server, 'resolved_paths') and hasattr(self.server, 'config'):
                        storage_adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                        if getattr(storage_adapter, 'backend_name', '') == 'sqlite' and hasattr(storage_adapter, 'db'):
                            try:
                                db_sents = storage_adapter.db.get_sentences_by_session(zid)
                                if db_sents:
                                    for s in sorted(db_sents, key=lambda x: x.get("sentence_index", 1)):
                                        s_list.append({
                                            "sentence_index": s.get("sentence_index", 1),
                                            "sentence_source": str(s.get("sentence_source") or "").strip(),
                                            "sentence_destination": str(s.get("sentence_destination") or "").strip(),
                                        })
                            except Exception:
                                pass
                    if not s_list:
                        st = safe_sess.get("sentence_translation")
                        if isinstance(st, dict):
                            for s_idx_raw, trans_val in sorted(st.items(), key=lambda x: int(x[0]) if (isinstance(x[0], int) or str(x[0]).isdigit()) else 1):
                                s_idx = (int(s_idx_raw) + 1) if (isinstance(s_idx_raw, int) or str(s_idx_raw).isdigit()) else 1
                                s_list.append({
                                    "sentence_index": s_idx,
                                    "sentence_source": "",
                                    "sentence_destination": str(trans_val).strip() if trans_val else "",
                                })
                    safe_sess["sentences"] = s_list

                self._send_json(200, safe_sess)
                return

            # Fallback: check persistent storage (SQLite or TSV)
            results_dir = resolve_results_dir(self.server.resolved_paths, operation_configuration(self.server.config))
            storage_adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
            is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

            tsv_path = find_working_tsv(results_dir, zid) if results_dir and not is_sqlite else None
            session_found = False
            is_busy = False
            restored = None

            if tsv_path and tsv_path.exists():
                session_found = True
                is_busy = check_coordination_busy(tsv_path)
                try:
                    restored = storage_adapter.restore_session(zid, results_dir=results_dir)
                except Exception:
                    try:
                        comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                        restored = {
                            "session_zid": zid,
                            "headers": headers,
                            "data_rows": data_rows,
                            "tsv_path": tsv_path,
                        }
                    except Exception:
                        pass
            else:
                try:
                    restored = storage_adapter.restore_session(zid, results_dir=results_dir)
                    if restored:
                        session_found = True
                        if not is_sqlite and results_dir and results_dir.exists():
                            possible_locks = list(results_dir.glob(f"{zid}*.lock"))
                            for lk in possible_locks:
                                if check_coordination_busy(lk):
                                    is_busy = True
                                    break
                except Exception:
                    session_found = False

            if not session_found:
                raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{zid}' not active in arbiter memory or persistent storage")

            # Extract headers, data_rows, and translatedText from restored session
            headers = []
            data_rows = []
            sentence_translation = ""
            source_text = ""
            text_mode = "single"
            sess_lang = "de"

            if restored:
                headers, data_rows = reword.restored_manifest_rows(restored)
                source_text = restored.get("source_text", "")
                sentence_translation = restored.get("sentence_translation", "")
                sess_lang = restored.get("source_language") or restored.get("language") or restored.get("lang") or "de"
                if restored.get("session") and isinstance(restored["session"], dict):
                    text_mode = restored["session"].get("text_mode", "single")
                    if not sess_lang:
                        sess_lang = restored["session"].get("source_language") or restored["session"].get("language") or "de"

            # If sentence_translation not found directly, try extracting from TSV columns or SQLite sentences
            role_fields = get_role_fields(mapping, headers) if mapping else {}
            is_single_para = (text_mode == 'single' and '\n' not in (source_text or '').strip() and '\r' not in (source_text or '').strip())
            joiner = " " if is_single_para else "\n"
            if is_single_para and sentence_translation:
                sentence_translation = " ".join([p.strip() for p in re.split(r'[\r\n]+', sentence_translation) if p.strip()])
            if not sentence_translation and data_rows and headers:
                col_sent_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
                col_sent_idx = headers.index(role_fields['sentence_index']) if 'sentence_index' in role_fields and role_fields['sentence_index'] in headers else (headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1)
                if col_sent_dest != -1:
                    seen_idx = set()
                    sent_trans_list = []
                    for r in data_rows:
                        if len(r) > col_sent_dest and r[col_sent_dest].strip():
                            idx = r[col_sent_idx] if col_sent_idx != -1 and len(r) > col_sent_idx else len(sent_trans_list)
                            if idx not in seen_idx:
                                seen_idx.add(idx)
                                sent_trans_list.append(r[col_sent_dest].strip())
                    if sent_trans_list:
                        sentence_translation = joiner.join(sent_trans_list)

            sentences_list = []
            if hasattr(storage_adapter, 'backend_name') and storage_adapter.backend_name == 'sqlite' and hasattr(storage_adapter, 'db'):
                try:
                    db_sents = storage_adapter.db.get_sentences_by_session(zid)
                    if db_sents:
                        for s in sorted(db_sents, key=lambda x: x.get("sentence_index", 1)):
                            s_dest = s.get("sentence_destination")
                            s_src = s.get("sentence_source")
                            entry = {
                                "sentence_index": s.get("sentence_index", 1),
                                "sentence_source": str(s_src).strip() if s_src else "",
                                "sentence_destination": str(s_dest).strip() if s_dest else "",
                            }
                            if s.get("text_provenance"):
                                entry["text_provenance"] = s.get("text_provenance")
                            sentences_list.append(entry)
                        if not sentence_translation:
                            clean_translations = [s["sentence_destination"] for s in sentences_list if s["sentence_destination"]]
                            if clean_translations:
                                sentence_translation = joiner.join(clean_translations)
                except Exception:
                    pass

            if is_sqlite:
                lifecycle = storage_adapter.db.get_worker_status(zid)
                is_busy = lifecycle['worker_status'] in ('queued', 'running')

            # Semantic completion check: if untranslated lemmas remain and session is recent or locked.
            # Use 1800s (30 min) recency window - Argos can take many minutes for large texts.
            is_recent = False
            try:
                if len(zid) >= 14 and zid[:14].isdigit():
                    yr, mo, dy = int(zid[0:4]), int(zid[4:6]), int(zid[6:8])
                    hr, mn, sc = int(zid[8:10]), int(zid[10:12]), int(zid[12:14])
                    dt = datetime(yr, mo, dy, hr, mn, min(sc, 59)) + timedelta(seconds=max(0, sc - 59))
                    now = datetime.now()
                    age_sec = (now - dt).total_seconds()
                    if abs(age_sec) <= 1800:
                        is_recent = True
            except Exception:
                pass
            # If the progressive worker is still actively registered, the session is busy
            # regardless of age. This prevents premature Retry badges during slow Argos runs.
            if not is_busy and hasattr(self.server, 'arbiter') and self.server.arbiter:
                eq = getattr(self.server.arbiter, 'enrichment_queue', None)
                if eq and hasattr(eq, '_active_progressive_sessions'):
                    with eq._lock:
                        if zid in eq._active_progressive_sessions:
                            is_busy = True

            has_untranslated_lemmas = False
            if data_rows and headers:
                col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
                col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
                if col_lemma != -1 and col_word_dest != -1:
                    for r in data_rows:
                        if len(r) > col_lemma and r[col_lemma].strip():
                            dest_val = r[col_word_dest].strip() if len(r) > col_word_dest else ""
                            if is_unfinished_translation(dest_val):
                                has_untranslated_lemmas = True
                                break

            session_is_busy = is_busy or (has_untranslated_lemmas and is_recent)


            fallback_row_provs = {}
            if is_sqlite:
                try:
                    db_words = storage_adapter.db.get_words_by_session(zid)
                    for w in db_words:
                        w_prov = w.get("word_provenance")
                        if w_prov:
                            t_ord = str(w.get("token_order", ""))
                            if t_ord:
                                fallback_row_provs[t_ord] = w_prov
                                if t_ord.isdigit():
                                    fallback_row_provs[int(t_ord)] = w_prov
                except Exception:
                    pass

            rows_dict = format_storage_update_rows(storage_adapter, zid, data_rows, headers, role_fields,
                fallback_row_provs)
            if len(sentences_list) >= 2:
                s_srcs, s_dests = pair_sentence_records(sentences_list)
                eff_status_mode = "multi" if len(s_dests) >= 2 else text_mode
                eff_status_text = "\n".join(s_srcs) if len(s_srcs) >= 2 else (source_text or " ".join(s_srcs))
                translated_html = format_translated_html(
                    s_dests,
                    text_mode=eff_status_mode,
                    text=eff_status_text,
                    config=operation_configuration(self.server.config)
                ) if s_dests else ""
            else:
                translated_html = format_translated_html(
                    sentence_translation,
                    text_mode=text_mode,
                    text=source_text,
                    config=operation_configuration(self.server.config)
                ) if sentence_translation else ""

            eff_text_prov = None
            if is_sqlite:
                try:
                    db_sents = storage_adapter.db.get_sentences_by_session(zid)
                    if db_sents:
                        eff_text_prov = next((s.get("text_provenance") for s in db_sents if s.get("text_provenance")), None)
                except Exception:
                    pass

            stored_provs = get_session_active_providers(zid, config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths) if 'get_session_active_providers' in globals() else {}
            active_text_p = stored_provs.get("active_text_provider")
            active_lemma_p = stored_provs.get("active_lemma_provider")
            active_p = stored_provs.get("active_provider")

            if not active_text_p and eff_text_prov and str(eff_text_prov).startswith("live:"):
                active_text_p = str(eff_text_prov)[5:].strip().lower()
            if not active_lemma_p:
                for rp in fallback_row_provs.values():
                    if rp and str(rp).startswith("live:"):
                        active_lemma_p = str(rp)[5:].strip().lower()
                        break
            if not active_p:
                active_p = active_lemma_p or active_text_p

            self._send_json(200, {
                "ok": True,
                "zid": zid,
                "session_zid": zid,
                "is_finished": not session_is_busy,
                "stage": "translating" if session_is_busy else "finished",
                "active_provider": active_p,
                "active_text_provider": active_text_p,
                "active_lemma_provider": active_lemma_p,
                "status": {
                    "is_finished": not session_is_busy,
                    "stage": "translating" if session_is_busy else "finished",
                    "active_provider": active_p,
                    "active_text_provider": active_text_p,
                    "active_lemma_provider": active_lemma_p,
                },
                "rows": rows_dict,
                "row_provenances": fallback_row_provs,
                "rowProvenances": fallback_row_provs,
                "text_provenance": eff_text_prov,
                "textProvenance": eff_text_prov,
                "translatedText": translated_html,
                "sentences": sentences_list,
            })
            return

        # Render endpoint (HTTP Fast-Path for AutoHotkey & Client UI)
        if path in ('/api/v1/render', '/render'):
            if method not in ('GET', 'POST'):
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")

            body = self._read_json_body() if method == 'POST' else {}
            server_api_key = self._require_credential_admission(
                method, body_data=body, query_params=qs, allow_browser_nav=False
            )

            session_zid = body.get('session_zid') or body.get('zid') or qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0]
            language = body.get('language') or body.get('lang') or qs.get('language', [None])[0] or qs.get('lang', [None])[0]
            text = body.get('text') or qs.get('text', [None])[0]
            text_mode = body.get('text_mode') or body.get('text-mode') or qs.get('text_mode', ['single'])[0]
            theme = body.get('theme') or qs.get('theme', [operation_configuration(self.server.config).get('ui', 'theme', fallback='dark')])[0]
            zoom = body.get('zoom') or qs.get('zoom', [operation_configuration(self.server.config).get('rendering', 'default_zoom', fallback='100')])[0]
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

            if not language and not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'language' or 'session_zid'")

            html_result = ""
            child_args = []
            active_zid = session_zid or generate_unique_zid()

            if session_zid:
                adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                    source_text = restored.get("source_text", "")
                    sess_lang = restored.get("source_language") or language or "de"
                    t_mode = restored.get("text_mode") or text_mode or "single"
                    slug = restored.get("slug") or restored.get("session", {}).get("slug", "") or (generate_slug(source_text) if source_text else "")
                    results_dir = Path(self.server.resolved_paths.get('kardenwort_workspace', '.')) / "results"
                    slug_suffix = f"-{slug}" if slug else ""
                    resolved_tsv = Path(tsv_path) if tsv_path else (results_dir / f"{session_zid}{slug_suffix}.{sess_lang}.tsv")

                    render_out = render_flow_fn(
                        text=source_text,
                        language=sess_lang,
                        zid=session_zid,
                        text_mode=t_mode,
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        zoom_level=str(zoom),
                        theme=theme,
                        tsv_path=resolved_tsv,
                        split_gap_limit=int(split_gap) if split_gap else None,
                        seq_num=int(seq_num) if seq_num else None,
                        trace_id=f"{session_zid}:render:init",
                        spawn_children=False,
                        return_children=True,
                        api_token=server_api_key,
                        server_enabled=True,
                    )
                    if isinstance(render_out, tuple):
                        html_result, child_args = render_out
                    else:
                        html_result = render_out
                        child_args = getattr(render_out, 'children', [])
                except Exception:
                    if text and language:
                        if not bypass_lang_check:
                            lang_res = verify_language(text, language, operation_configuration(self.server.config), bypass=False)
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

                        render_out = render_flow_fn(
                            text=text,
                            language=language,
                            zid=active_zid,
                            text_mode=text_mode,
                            config=operation_configuration(self.server.config),
                            resolved_paths=self.server.resolved_paths,
                            zoom_level=str(zoom),
                            theme=theme,
                            tsv_path=Path(tsv_path) if tsv_path else None,
                            split_gap_limit=int(split_gap) if split_gap else None,
                            seq_num=int(seq_num) if seq_num else None,
                            trace_id=f"{active_zid}:render:init",
                            spawn_children=False,
                            return_children=True,
                            api_token=server_api_key,
                            server_enabled=True,
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
                    lang_res = verify_language(text, language, operation_configuration(self.server.config), bypass=False)
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

                render_out = render_flow_fn(
                    text=text,
                    language=language,
                    zid=active_zid,
                    text_mode=text_mode,
                    config=operation_configuration(self.server.config),
                    resolved_paths=self.server.resolved_paths,
                    zoom_level=str(zoom),
                    theme=theme,
                    tsv_path=Path(tsv_path) if tsv_path else None,
                    split_gap_limit=int(split_gap) if split_gap else None,
                    seq_num=int(seq_num) if seq_num else None,
                    trace_id=f"{active_zid}:render:init",
                    spawn_children=False,
                    return_children=True,
                    api_token=server_api_key,
                    server_enabled=True,
                )
                if isinstance(render_out, tuple):
                    html_result, child_args = render_out
                else:
                    html_result = render_out
                    child_args = getattr(render_out, 'children', [])
            else:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' or 'session_zid'")

            resolved_zid = _resolve_rendered_session_zid(html_result, active_zid)
            _remember_session_alias(active_zid, resolved_zid)
            b64_html = encode(html_result)
            self._send_json(200, {
                "ok": True,
                "zid": resolved_zid,
                "session_zid": resolved_zid,
                "html_b64": b64_html,
                "html": html_result,
                "children": child_args or [],
            }, credential_bearing=True)
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

        # 4. Consolidated Legacy & Reader Endpoints (from http_server.py & web reader)
        # @deprecated GoldenDict HTML lookup rendering (/lookup and view_mode='goldendict') is deprecated and frozen in maintenance-only mode.
        if path in ('/lookup', '/session/render') or (path == '/' and (qs.get('text', [None])[0] or qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0] or qs.get('project_id', [None])[0] or qs.get('project', [None])[0])):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")

            admitted_token = self._require_credential_admission(
                method, query_params=qs, allow_browser_nav=True
            )
            session_zid = qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0]
            project_id = qs.get('project_id', [None])[0] or qs.get('project', [None])[0]
            text = qs.get('text', [None])[0]
            language = qs.get('language', [None])[0]

            if project_id:
                req_zid = generate_unique_zid()
                try:
                    synthesized = synthesize_project_materials(
                        project_id=int(project_id),
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        language=language,
                        zid=req_zid,
                    )
                except Exception as e:
                    raise StructuredError(ErrorCode.NOT_FOUND, f"Project '{project_id}' materials synthesis failed: {e}")

                source_text = synthesized.get("source_text", "")
                sess_lang = synthesized.get("source_language") or language or "de"
                target_lang = synthesized.get("target_language") or qs.get('target-lang', ['ru'])[0]
                data_rows = synthesized.get("data_rows", [])
                headers = synthesized.get("headers", [])
                comments = synthesized.get("comments", [])
                sentence_translation = ""
                if synthesized.get("sentences"):
                    is_single_para = ((synthesized.get("text_mode") or "single") == 'single' and '\n' not in (source_text or '').strip() and '\r' not in (source_text or '').strip())
                    joiner = " " if is_single_para else "\n"
                    sentence_translation = joiner.join([
                        (str(s.get("sentence_destination") or "").strip() or str(s.get("sentence_destination2") or "").strip() or str(s.get("sentence_source") or "").strip())
                        for s in synthesized["sentences"]
                    ])
                fingerprint = compute_content_fingerprint(data_rows)
                slug = synthesized.get("slug") or ""
                req_theme = qs.get('theme', [None])[0]
                view_mode = qs.get('view', [None])[0]
                synth_zid = synthesized.get("session_zid") or req_zid
                # @deprecated GoldenDict view mode is deprecated and frozen in maintenance-only mode.
                if view_mode == 'goldendict':
                    goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                    goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                    goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'translation', 'ipa', 'morphology', 'pos', 'gender'])
                    goldendict['theme'] = req_theme or 'dark'
                    goldendict.setdefault('heading_source', '__default__')
                    goldendict.setdefault('heading_translation', '__default__')
                    goldendict.setdefault('heading_lemmas', '__default__')
                    goldendict.setdefault('run_intellifiller', False)
                    goldendict['server_enabled'] = True
                    goldendict['server_api_key'] = admitted_token

                    html = render_lookup_html(
                        text=source_text,
                        language=sess_lang,
                        target_lang=target_lang,
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        zid=synth_zid,
                        goldendict=goldendict,
                        comments=comments,
                        headers=headers,
                        data_rows=data_rows,
                        sentence_translation=sentence_translation,
                        session_zid=synth_zid,
                        api_token=admitted_token,
                        server_enabled=True,
                        fingerprint=fingerprint,
                    )
                else:
                    adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                    adapter.save_session(
                        session_zid=synth_zid,
                        slug=slug,
                        source_language=sess_lang,
                        target_language=target_lang,
                        text_mode="multi",
                        source_raw_text=source_text,
                        headers=headers,
                        data_rows=data_rows,
                        sentences=synthesized.get("sentences"),
                    )
                    if adapter.backend_name == 'sqlite':
                        tsv_path = str(synth_zid)
                    else:
                        results_dir = Path(self.server.resolved_paths.get('kardenwort_workspace', '.')) / "results"
                        results_dir.mkdir(parents=True, exist_ok=True)
                        slug_suffix = f"-{slug}" if slug else ""
                        tsv_path = results_dir / f"{synth_zid}{slug_suffix}.{sess_lang}.tsv"
                        save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                    html = render_flow_fn(
                        text=source_text,
                        language=sess_lang,
                        zid=synth_zid,
                        text_mode="multi",
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        theme=req_theme,
                        tsv_path=tsv_path,
                        api_token=admitted_token,
                        server_enabled=True,
                    )
                self._send_credential_html(html)
                return

            if session_zid:
                adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                except Exception as e:
                    aliased_zid = _lookup_session_alias(session_zid)
                    if aliased_zid and aliased_zid != session_zid:
                        split_url = urllib.parse.urlsplit(self.path)
                        params = urllib.parse.parse_qsl(split_url.query, keep_blank_values=True)
                        rewritten = [
                            (k, aliased_zid if k in ('session_zid', 'zid') else v)
                            for k, v in params
                        ]
                        location = urllib.parse.urlunsplit(('', '', split_url.path, urllib.parse.urlencode(rewritten), ''))
                        self.send_response(302)
                        self._send_cors_headers()
                        self.send_header('Location', location)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    with _DRAFT_SESSIONS_LOCK:
                        draft = _DRAFT_SESSIONS.get(session_zid)
                    if draft:
                        req_theme = qs.get('theme', [None])[0] or draft.get("theme", "dark")
                        seq_num = qs.get('seq_num', [None])[0] or qs.get('seq-num', [None])[0] or draft.get("seq_num")
                        html = render_flow_fn(
                            text=draft.get("text", ""),
                            language=draft.get("language", language or "en"),
                            zid=session_zid,
                            text_mode=draft.get("text_mode", "single"),
                            config=operation_configuration(self.server.config),
                            resolved_paths=self.server.resolved_paths,
                            theme=req_theme,
                            seq_num=int(seq_num) if seq_num else None,
                            mismatch_info=draft.get("mismatch_info"),
                            api_token=admitted_token,
                            server_enabled=True,
                        )
                        self._send_credential_html(html)
                        return
                    raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{session_zid}' not found: {e}")

                source_text = restored.get("source_text", "")
                sess_lang = restored.get("source_language") or language or "de"
                target_lang = restored.get("target_language") or qs.get('target-lang', ['ru'])[0]
                headers, data_rows = reword.restored_manifest_rows(restored)
                comments = restored.get("comments", [])
                sentence_translation = restored.get("sentence_translation", "")
                text_mode_val = restored.get("text_mode") or (restored.get("session") or {}).get("text_mode") or "single"
                is_single_para = (text_mode_val == 'single' and '\n' not in (source_text or '').strip() and '\r' not in (source_text or '').strip())
                joiner = " " if is_single_para else "\n"
                if not sentence_translation and restored.get("sentences"):
                    sentence_translation = joiner.join([
                        (str(s.get("sentence_destination") or "").strip() or str(s.get("sentence_destination2") or "").strip())
                        for s in restored["sentences"]
                        if (str(s.get("sentence_destination") or "").strip() or str(s.get("sentence_destination2") or "").strip())
                    ])
                elif is_single_para and sentence_translation:
                    sentence_translation = " ".join([p.strip() for p in re.split(r'[\r\n]+', sentence_translation) if p.strip()])

                mapping_path = None
                if self.server.resolved_paths and "anki_mapping_file" in self.server.resolved_paths:
                    mapping_path = Path(self.server.resolved_paths["anki_mapping_file"])
                elif operation_configuration(self.server.config) and hasattr(operation_configuration(self.server.config), "get"):
                    raw_mp = operation_configuration(self.server.config).get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
                    mapping_path = Path(raw_mp)
                mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None
                role_fields = get_role_fields(mapping, headers) if mapping else {}

                # Incremental wordfill hydration for missing lemma fields
                wordfill_cfg = getattr(self.server, 'wordfill', None)
                if wordfill_cfg is None and operation_configuration(self.server.config) is not None:
                    wordfill_cfg = resolve_wordfill_config(operation_configuration(self.server.config), self.server.resolved_paths)

                hydrated_any = False
                if wordfill_cfg and wordfill_cfg.get('enabled', False) and data_rows and headers:
                    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
                    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
                    col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else (headers.index('WordSourceIPA') if 'WordSourceIPA' in headers else -1)
                    col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else (headers.index('WordSourceMorphologyAI') if 'WordSourceMorphologyAI' in headers else -1)
                    col_lemma_wf = col_lemma
                    if col_lemma_wf != -1:
                        seen_lemmas = {}
                        for i, row in enumerate(data_rows):
                            if len(row) > col_lemma_wf:
                                lemma_val = row[col_lemma_wf].strip()
                                if lemma_val:
                                    seen_lemmas.setdefault(lemma_val, []).append(i)
                        for lemma_val, row_indices in seen_lemmas.items():
                            needs_hydration = False
                            for idx in row_indices:
                                row = data_rows[idx]
                                need_dest = col_word_dest == -1 or len(row) <= col_word_dest or not row[col_word_dest].strip()
                                need_ipa = col_ipa != -1 and (len(row) <= col_ipa or not row[col_ipa].strip())
                                need_morph = col_morph != -1 and (len(row) <= col_morph or not row[col_morph].strip())
                                if need_dest or need_ipa or need_morph:
                                    needs_hydration = True
                                    break
                            if needs_hydration:
                                match = find_wordfill_match(lemma_val, sess_lang, wordfill_cfg)
                                if match:
                                    lemma_rows = [data_rows[i] for i in row_indices]
                                    apply_wordfill_to_rows(lemma_rows, headers, match)
                                    hydrated_any = True

                if hydrated_any:
                    try:
                        adapter.save_session(
                            session_zid=session_zid,
                            slug=restored.get("slug") or "",
                            source_language=sess_lang,
                            target_language=target_lang,
                            text_mode=restored.get("text_mode") or "single",
                            source_raw_text=source_text,
                            comments=comments,
                            headers=headers,
                            data_rows=data_rows,
                            sentences=restored.get("sentences"),
                        )
                        restored = adapter.restore_session(session_zid)
                    except Exception as save_err:
                        logger.warning(f"Could not persist hydrated session to storage: {save_err}")

                fingerprint = compute_content_fingerprint(restored.get("data_rows", []))

                if data_rows and headers:
                    data_rows = sort_rows_by_frequency(
                        data_rows, headers, sess_lang, operation_configuration(self.server.config), self.server.resolved_paths, role_fields=role_fields
                    )

                req_theme = qs.get('theme', [None])[0]
                view_mode = qs.get('view', [None])[0]
                slug = restored.get("slug") or restored.get("session", {}).get("slug", "") or (generate_slug(source_text) if source_text else "")
                text_mode = restored.get("text_mode") or "single"

                # @deprecated GoldenDict view mode is deprecated and frozen in maintenance-only mode.
                if view_mode == 'goldendict':
                    goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                    goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                    goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'translation', 'ipa', 'morphology', 'pos', 'gender'])
                    goldendict['theme'] = req_theme or 'dark'
                    goldendict.setdefault('theme', 'compact')
                    goldendict.setdefault('heading_source', '__default__')
                    goldendict.setdefault('heading_translation', '__default__')
                    goldendict.setdefault('heading_lemmas', '__default__')
                    goldendict.setdefault('run_intellifiller', False)
                    goldendict['server_enabled'] = True
                    goldendict['server_api_key'] = admitted_token

                    html = render_lookup_html(
                        text=source_text,
                        language=sess_lang,
                        target_lang=target_lang,
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        zid=session_zid,
                        goldendict=goldendict,
                        comments=comments,
                        headers=headers,
                        data_rows=data_rows,
                        sentence_translation=sentence_translation,
                        session_zid=session_zid,
                        api_token=admitted_token,
                        server_enabled=True,
                        fingerprint=fingerprint,
                    )
                else:
                    if getattr(adapter, 'backend_name', '') == 'sqlite':
                        tsv_path = str(session_zid)
                    else:
                        results_dir = Path(self.server.resolved_paths.get('kardenwort_workspace', '.')) / "results"
                        slug_suffix = f"-{slug}" if slug else ""
                        tsv_path = results_dir / f"{session_zid}{slug_suffix}.{sess_lang}.tsv"
                    seq_num = qs.get('seq_num', [None])[0] or qs.get('seq-num', [None])[0]

                    delivery_mode = "container"
                    if hasattr(self.server, 'config') and operation_configuration(self.server.config) and operation_configuration(self.server.config).has_section("sentences_mode"):
                        raw_delivery = operation_configuration(self.server.config).get("sentences_mode", "delivery_mode", fallback=None)
                        if raw_delivery is not None:
                            del_val = raw_delivery.strip().lower()
                            delivery_mode = del_val if del_val in ("container", "multi_window") else "container"
                        else:
                            raw_wtm = operation_configuration(self.server.config).get("sentences_mode", "web_tab_mode", fallback="container").strip().lower()
                            delivery_mode = "multi_window" if raw_wtm == "tabs" else "container"

                    has_multi_sentences = len(restored.get("sentences") or []) > 1
                    if not has_multi_sentences and source_text:
                        sents_split = [s for s in re.split(r'[\r\n]+', source_text.strip()) if s.strip()]
                        if len(sents_split) > 1:
                            has_multi_sentences = True

                    if delivery_mode == "container" and has_multi_sentences and (seq_num is None or str(seq_num).strip() in ("", "1")):
                        resolved_seq_num = 2
                    else:
                        try:
                            resolved_seq_num = int(seq_num) if (seq_num is not None and str(seq_num).strip()) else None
                        except (ValueError, TypeError):
                            resolved_seq_num = None

                    html = render_flow_fn(
                        text=source_text,
                        language=sess_lang,
                        zid=session_zid,
                        text_mode=text_mode,
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        theme=req_theme,
                        tsv_path=tsv_path,
                        seq_num=resolved_seq_num,
                        api_token=admitted_token,
                        server_enabled=True,
                    )
                self._send_credential_html(html)
                return

            if text and language:
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
                    config=operation_configuration(self.server.config),
                    resolved_paths=self.server.resolved_paths,
                    goldendict=self.server.goldendict,
                    bypass_lang_check=bypass_lang_check,
                )
                self._send_credential_html(res["html"])
                return


            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' or 'session_zid' or 'project_id' query parameter")
            if not language:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'language' query parameter")

        # @deprecated GoldenDict JSON lookup endpoint (/api/v1/lookup) is deprecated and frozen in maintenance-only mode.
        if path == '/api/v1/lookup':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            session_zid = qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0]
            project_id = qs.get('project_id', [None])[0] or qs.get('project', [None])[0]
            text = qs.get('text', [None])[0]
            language = qs.get('language', [None])[0]

            if project_id:
                req_zid = generate_unique_zid()
                try:
                    synthesized = synthesize_project_materials(
                        project_id=int(project_id),
                        config=operation_configuration(self.server.config),
                        resolved_paths=self.server.resolved_paths,
                        language=language,
                        zid=req_zid,
                    )
                except Exception as e:
                    raise StructuredError(ErrorCode.NOT_FOUND, f"Project '{project_id}' materials synthesis failed: {e}")

                source_text = synthesized.get("source_text", "")
                sess_lang = synthesized.get("source_language") or language or "de"
                target_lang = synthesized.get("target_language") or qs.get('target-lang', ['ru'])[0]
                data_rows = synthesized.get("data_rows", [])
                headers = synthesized.get("headers", [])
                comments = synthesized.get("comments", [])
                sentence_translation = ""
                if synthesized.get("sentences"):
                    is_single_para = ((synthesized.get("text_mode") or "single") == 'single' and '\n' not in (source_text or '').strip() and '\r' not in (source_text or '').strip())
                    joiner = " " if is_single_para else "\n"
                    sentence_translation = joiner.join([
                        (str(s.get("sentence_destination") or "").strip() or str(s.get("sentence_destination2") or "").strip() or str(s.get("sentence_source") or "").strip())
                        for s in synthesized["sentences"]
                    ])
                fingerprint = compute_content_fingerprint(data_rows)
                synth_zid = synthesized.get("session_zid") or f"project_{project_id}"

                goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'translation', 'ipa', 'morphology', 'pos', 'gender'])
                goldendict.setdefault('theme', 'compact')
                goldendict.setdefault('heading_source', '__default__')
                goldendict.setdefault('heading_translation', '__default__')
                goldendict.setdefault('heading_lemmas', '__default__')
                goldendict.setdefault('run_intellifiller', False)
                goldendict['server_enabled'] = True
                goldendict['server_api_key'] = getattr(self.server, 'api_key', '')

                html = render_lookup_html(
                    text=source_text,
                    language=sess_lang,
                    target_lang=target_lang,
                    config=operation_configuration(self.server.config),
                    resolved_paths=self.server.resolved_paths,
                    zid=synth_zid,
                    goldendict=goldendict,
                    comments=comments,
                    headers=headers,
                    data_rows=data_rows,
                    sentence_translation=sentence_translation,
                    session_zid=synth_zid,
                    api_token=getattr(self.server, 'api_key', ''),
                    server_enabled=True,
                    fingerprint=fingerprint,
                )
                self._send_json(200, {
                    "ok": True,
                    "html": html,
                    "project_id": int(project_id),
                    "session_zid": synth_zid,
                    "language": sess_lang,
                    "fingerprint": fingerprint,
                    "total_sessions": synthesized.get("total_sessions", 0),
                    "total_words": synthesized.get("total_words", 0),
                })
                return

            if session_zid:
                adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                except Exception as e:
                    raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{session_zid}' not found: {e}")

                source_text = restored.get("source_text", "")
                sess_lang = restored.get("source_language") or language or "de"
                target_lang = restored.get("target_language") or qs.get('target-lang', ['ru'])[0]
                headers, data_rows = reword.restored_manifest_rows(restored)
                comments = restored.get("comments", [])
                sentence_translation = restored.get("sentence_translation", "")
                text_mode_val = restored.get("text_mode") or (restored.get("session") or {}).get("text_mode") or "single"
                is_single_para = (text_mode_val == 'single' and '\n' not in (source_text or '').strip() and '\r' not in (source_text or '').strip())
                if is_single_para and sentence_translation:
                    sentence_translation = " ".join([p.strip() for p in re.split(r'[\r\n]+', sentence_translation) if p.strip()])

                mapping_path = None
                if self.server.resolved_paths and "anki_mapping_file" in self.server.resolved_paths:
                    mapping_path = Path(self.server.resolved_paths["anki_mapping_file"])
                elif operation_configuration(self.server.config) and hasattr(operation_configuration(self.server.config), "get"):
                    raw_mp = operation_configuration(self.server.config).get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
                    mapping_path = Path(raw_mp)
                mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None
                role_fields = get_role_fields(mapping, headers) if mapping else {}

                fingerprint = compute_content_fingerprint(restored.get("data_rows", []))

                if data_rows and headers:
                    data_rows = sort_rows_by_frequency(
                        data_rows, headers, sess_lang, operation_configuration(self.server.config), self.server.resolved_paths, role_fields=role_fields
                    )

                goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'translation', 'ipa', 'morphology', 'pos', 'gender'])
                goldendict.setdefault('theme', 'compact')
                goldendict.setdefault('heading_source', '__default__')
                goldendict.setdefault('heading_translation', '__default__')
                goldendict.setdefault('heading_lemmas', '__default__')
                goldendict.setdefault('run_intellifiller', False)
                goldendict['server_enabled'] = True
                goldendict['server_api_key'] = getattr(self.server, 'api_key', '')

                html = render_lookup_html(
                    text=source_text,
                    language=sess_lang,
                    target_lang=target_lang,
                    config=operation_configuration(self.server.config),
                    resolved_paths=self.server.resolved_paths,
                    zid=session_zid,
                    goldendict=goldendict,
                    comments=comments,
                    headers=headers,
                    data_rows=data_rows,
                    sentence_translation=sentence_translation,
                    session_zid=session_zid,
                    api_token=getattr(self.server, 'api_key', ''),
                    server_enabled=True,
                    fingerprint=fingerprint,
                )
                self._send_json(200, {
                    "ok": True,
                    "html": html,
                    "session_zid": session_zid,
                    "language": sess_lang,
                    "fingerprint": fingerprint
                })
                return

            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' or 'project_id' query parameter")
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
                config=operation_configuration(self.server.config),
                resolved_paths=self.server.resolved_paths,
                goldendict=self.server.goldendict,
                bypass_lang_check=bypass_lang_check,
            )
            self._send_json(200, res)
            return

        if path == '/api/v1/tag':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            for req_field in ('session_zid', 'language', 'row_id', 'status', 'fingerprint'):
                if req_field not in body:
                    raise StructuredError(ErrorCode.MISSING_FIELD, f"Missing required payload field: '{req_field}'")

            req_zid = generate_server_zid(self.server)
            deltas = [{"row_id": int(body["row_id"]), "token_order": int(body.get("token_order", body["row_id"])), "sentence_idx": int(body.get("sentence_idx", 1)), "column": "DeskSelected", "value": "1" if body["status"] else ""}]
            res = core_edit_save(
                tsv_path_or_session=body["session_zid"],
                deltas=deltas,
                config=operation_configuration(self.server.config),
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

        if path in ('/api/v1/export', '/session/export'):
            self._handle_export_request(method, path, require_selected_field=True)
            return

        # Atomic Language Confirmation & Session Tab Spawning
        if path == '/api/v1/confirm-language':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            session_zid = body.get('session_zid') or body.get('zid')
            action = body.get('action')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required payload field: 'session_zid'")
            if not action or action not in ('process', 'switch', 'keep', 'cancel'):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, f"Invalid or missing required payload field 'action': '{action}'. Must be 'process', 'switch', 'keep', or 'cancel'")

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

            if action == 'process':
                raw_lang = body.get('chosen_source_lang') or body.get('selected_language') or body.get('target_language') or draft.get("language") or "und"
                chosen_source_lang = raw_lang.strip().lower() if raw_lang else "und"
            elif action == 'switch':
                raw_detected = draft.get("mismatch_info", {}).get("detected_language") or draft.get("language")
                chosen_source_lang = raw_detected.strip().lower() if raw_detected else "en"
            elif action == 'keep':
                raw_exp = draft.get("language") or "en"
                chosen_source_lang = raw_exp.strip().lower() if raw_exp else "en"
            else:
                raw_exp = draft.get("language") or "en"
                chosen_source_lang = raw_exp.strip().lower() if raw_exp else "en"

            # Commit concrete language before launching a tray or session effect.
            if chosen_source_lang and chosen_source_lang != 'und':
                try:
                    current = preference_store(operation_configuration(self.server.config), self.server.resolved_paths).get('default_language')
                    if chosen_source_lang != current:
                        set_language(self.server, chosen_source_lang)
                        spawn_ahk(['--set-language', chosen_source_lang], base_dir=self.server.resolved_paths.get('base_dir'))
                except Exception:
                    with _DRAFT_SESSIONS_LOCK:
                        _DRAFT_SESSIONS[session_zid] = draft
                    raise

            raw_tsv = draft.get("tsv_path")
            if raw_tsv and chosen_source_lang != draft.get("language"):
                tsv_path_arg = None
            else:
                tsv_path_arg = Path(raw_tsv) if raw_tsv else None

            # Execute run_render_flow
            api_token = getattr(self.server, 'api_key', '')
            render_out = render_flow_fn(
                text=draft.get("text", ""),
                language=chosen_source_lang,
                zid=session_zid,
                text_mode=draft.get("text_mode", "single"),
                config=operation_configuration(self.server.config),
                resolved_paths=self.server.resolved_paths,
                zoom_level=str(draft.get("zoom", "100")),
                theme=draft.get("theme", "dark"),
                tsv_path=tsv_path_arg,
                seq_num=int(draft["seq_num"]) if draft.get("seq_num") else None,
                spawn_children=False,
                return_children=True,
                api_token=api_token,
                server_enabled=True,
            )
            if isinstance(render_out, tuple):
                html_result, child_args = render_out
            else:
                html_result = render_out
                child_args = getattr(render_out, 'children', [])

            resolved_session_zid = _resolve_rendered_session_zid(html_result, session_zid)
            _remember_session_alias(session_zid, resolved_session_zid)
            session_zid = resolved_session_zid

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
            delivery_mode = "container"
            if hasattr(self.server, 'config') and operation_configuration(self.server.config) and operation_configuration(self.server.config).has_section("sentences_mode"):
                parent_mode = operation_configuration(self.server.config).get("sentences_mode", "parent_mode", fallback="full").lower()
                raw_delivery = operation_configuration(self.server.config).get("sentences_mode", "delivery_mode", fallback=None)
                if raw_delivery is not None:
                    del_val = raw_delivery.strip().lower()
                    delivery_mode = del_val if del_val in ("container", "multi_window") else "container"
                else:
                    raw_wtm = operation_configuration(self.server.config).get("sentences_mode", "web_tab_mode", fallback="container").strip().lower()
                    delivery_mode = "multi_window" if raw_wtm == "tabs" else "container"

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
                    m_zid = re.search(r'(\d{14}(?:-\d+)?)', Path(str(t_path)).name)
                    c_zid = m_zid.group(1) if m_zid else session_zid
                    child_items.append((curr_seq, c_zid))
                    i += 2
                else:
                    i += 1

            spawn_urls = []
            if parent_mode != 'stub' or not child_items:
                spawn_urls.append(build_browser_url(session_zid, 1))

            if delivery_mode != 'container':
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
                "language": chosen_source_lang,
                "session_zid": session_zid,
                "urls": spawned_urls,
            })
            return

        # Browser tab spawning endpoint (bypasses browser popup blocking)
        if path == '/api/v1/spawn-tabs':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
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

        # Canonical synthesis and explicitly deprecated host-play aliases.
        if path in ('/api/v1/audio/synthesize', '/api/v1/audio/play', '/session/play'):
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._audio_request(path)
            return

        # 5. Admin Panel UI & Static Assets
        if path in ('/admin', '/admin/'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            desk_dir = Path(__file__).resolve().parent
            admin_html = desk_dir / "assets" / "admin.html"
            self._serve_static_file(admin_html)
            return

        if path.startswith('/assets/') or path in ('/admin.css', '/admin.js'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            desk_dir = Path(__file__).resolve().parent
            assets_dir = (desk_dir / "assets").resolve()
            if path in ('/admin.css', '/admin.js'):
                req_rel = path.lstrip('/')
            else:
                req_rel = path[len('/assets/'):]

            from kardenwort_assets import resolve_static_asset
            native_root = getattr(self.server, 'resolved_paths', {}).get('autohotkey_dir')
            try:
                target_path = resolve_static_asset(assets_dir, req_rel, native_root)
            except ValueError as exc:
                raise StructuredError(ErrorCode.UNAUTHORIZED, "Path traversal forbidden") from exc
            except FileNotFoundError as exc:
                raise StructuredError(ErrorCode.NOT_FOUND, f"Static asset '{req_rel}' not found") from exc

            self._serve_static_file(target_path)
            return

        # 5.1 Static Web Shell & Assets (web/ directory: /, /index.html, /css/*, /js/*, /favicon.ico)
        if (
            path in ('/', '/index.html', '/favicon.ico')
            or path.startswith('/css/')
            or path.startswith('/js/')
            or path.startswith('/web/')
        ):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            desk_dir = Path(__file__).resolve().parent
            web_dir = (desk_dir / "web").resolve()

            if path in ('/', '/index.html'):
                target_path = (web_dir / "index.html").resolve()
            elif path == '/favicon.ico':
                target_path = (web_dir / "favicon.ico").resolve()
                if not target_path.exists():
                    target_path = (desk_dir / "assets" / "favicon.ico").resolve()
                if not target_path.exists():
                    target_path = (desk_dir / "assets" / "numbers" / "1.ico").resolve()
            elif path.startswith('/web/'):
                req_rel = path[len('/web/'):]
                target_path = (web_dir / req_rel).resolve()
            else:
                req_rel = path.lstrip('/')
                target_path = (web_dir / req_rel).resolve()

            allowed = False
            try:
                target_path.relative_to(web_dir)
                allowed = True
            except ValueError:
                try:
                    target_path.relative_to((desk_dir / "assets").resolve())
                    allowed = True
                except ValueError:
                    pass

            if not allowed:
                raise StructuredError(ErrorCode.UNAUTHORIZED, "Path traversal forbidden")

            if not target_path.exists() or not target_path.is_file():
                raise StructuredError(ErrorCode.NOT_FOUND, f"Static asset '{path}' not found")

            self._serve_static_file(target_path)
            return

        # 6. Admin Project Tree REST API Endpoints
        if path == '/api/v1/admin/projects':
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            if method == 'GET':
                self._authenticate_token(query_params=qs)
                tree = db.get_project_tree()
                self._send_json(200, {"ok": True, "projects": tree})
                return
            elif method == 'POST':
                body = self._read_json_body()
                self._authenticate_token(body, require_configured=True)
                title = body.get('title')
                if not title:
                    raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'title' in project payload")
                slug = body.get('slug')
                parent_id = body.get('parent_id')
                description = body.get('description', '')
                project_id = db.create_project(
                    title=title,
                    slug=slug,
                    parent_id=int(parent_id) if parent_id is not None else None,
                    description=description
                )
                self._send_json(200, {"ok": True, "project_id": project_id})
                return
            else:
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")

        if path == '/api/v1/admin/projects/update':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            updates = {k: v for k, v in body.items() if k in ('title', 'slug', 'description', 'parent_id', 'order_index')}
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            ok = db.update_project(int(project_id), updates)
            self._send_json(200, {"ok": ok, "project_id": project_id})
            return

        if path == '/api/v1/admin/projects/delete':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            ok = db.soft_delete_project(int(project_id))
            self._send_json(200, {"ok": ok, "project_id": project_id})
            return

        if path == '/api/v1/admin/projects/link':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            session_zid = body.get('session_zid')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            ok = db.link_session_to_project(int(project_id), str(session_zid))
            self._send_json(200, {"ok": ok, "project_id": project_id, "session_zid": session_zid})
            return

        if path == '/api/v1/admin/projects/batch-link':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")

            mode = body.get('mode', 'explicit')
            session_zids = body.get('session_zids') or body.get('zids') or []
            filter_params = body.get('filter') or {}
            excluded_zids = body.get('excluded_zids') or []

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)

            if mode == 'all_matching' or (not session_zids and body.get('all_matching')):
                linked_count = db.link_sessions_batch(
                    project_id=int(project_id),
                    session_zids=None,
                    query=filter_params.get('query'),
                    language=filter_params.get('language'),
                    assigned=filter_params.get('assigned'),
                    filter_project_id=filter_params.get('project_id'),
                    excluded_zids=excluded_zids,
                )
            else:
                if not isinstance(session_zids, list) or len(session_zids) == 0:
                    raise StructuredError(ErrorCode.MISSING_FIELD, "Missing or empty 'session_zids' in payload")
                clean_list = [str(z) for z in session_zids if str(z) not in excluded_zids]
                linked_count = db.link_sessions_batch(
                    project_id=int(project_id),
                    session_zids=clean_list,
                )
            self._send_json(200, {"ok": True, "project_id": project_id, "linked_count": linked_count})
            return

        if path == '/api/v1/admin/projects/unlink':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            session_zid = body.get('session_zid')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            ok = db.unlink_session_from_project(int(project_id), str(session_zid))
            self._send_json(200, {"ok": ok, "project_id": project_id, "session_zid": session_zid})
            return

        if path == '/api/v1/admin/projects/reorder':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            session_zids = body.get('session_zids', [])
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            if not isinstance(session_zids, list):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, "'session_zids' must be a list")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            ok = db.reorder_project_sessions(int(project_id), session_zids)
            self._send_json(200, {"ok": ok, "project_id": project_id, "session_zids": session_zids})
            return

        if path == '/api/v1/admin/projects/export-deck':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            lang = body.get('language')
            req_zid = generate_server_zid(self.server)
            res = aggregate_project_materials(
                project_id=int(project_id),
                config=operation_configuration(self.server.config),
                resolved_paths=self.server.resolved_paths,
                language=lang,
                zid=req_zid,
            )
            self._send_json(200, res)
            return

        if path == '/api/v1/admin/projects/synthesize':
            if method not in ('GET', 'POST'):
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            if method == 'POST':
                body = self._read_json_body()
                self._authenticate_token(body, require_configured=True)
                project_id = body.get('project_id')
                lang = body.get('language')
            else:
                self._authenticate_token(query_params=qs)
                project_id = qs.get('project_id', [None])[0] or qs.get('project', [None])[0]
                lang = qs.get('language', [None])[0]

            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload/parameters")

            req_zid = generate_server_zid(self.server)
            res = synthesize_project_materials(
                project_id=int(project_id),
                config=operation_configuration(self.server.config),
                resolved_paths=self.server.resolved_paths,
                language=lang,
                zid=req_zid,
            )
            self._send_json(200, res)
            return

        if path == '/api/v1/admin/sessions':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)

            query = qs.get('query', [None])[0] or qs.get('q', [None])[0]
            language = qs.get('language', [None])[0] or qs.get('lang', [None])[0]
            assigned_raw = qs.get('assigned', [None])[0]
            project_id_raw = qs.get('project_id', [None])[0]
            limit_raw = qs.get('limit', ['50'])[0]
            offset_raw = qs.get('offset', ['0'])[0]

            limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else 50
            offset = int(offset_raw) if offset_raw and offset_raw.isdigit() else 0
            project_id = int(project_id_raw) if project_id_raw and project_id_raw.isdigit() else None

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            sessions, total_count = db.search_sessions(
                query=query,
                language=language,
                assigned=assigned_raw,
                project_id=project_id,
                limit=limit,
                offset=offset,
            )
            self._send_json(200, {
                "ok": True,
                "sessions": sessions,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
            })
            return

        if path == '/api/v1/admin/sessions/delete':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            session_zid = body.get('session_zid') or body.get('zid')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            ok = db.soft_delete_session(str(session_zid))
            self._send_json(200, {"ok": ok, "session_zid": session_zid})
            return

        if path == '/api/v1/admin/sessions/batch-delete':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            mode = body.get('mode', 'explicit')
            session_zids = body.get('session_zids') or body.get('zids') or []
            filter_params = body.get('filter') or {}
            excluded_zids = body.get('excluded_zids') or []

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)

            if mode == 'all_matching' or (not session_zids and body.get('all_matching')):
                deleted_count = db.soft_delete_sessions_batch(
                    session_zids=None,
                    query=filter_params.get('query'),
                    language=filter_params.get('language'),
                    assigned=filter_params.get('assigned'),
                    project_id=filter_params.get('project_id'),
                    excluded_zids=excluded_zids,
                )
            else:
                if not isinstance(session_zids, list) or len(session_zids) == 0:
                    raise StructuredError(ErrorCode.MISSING_FIELD, "Missing or empty 'session_zids' in payload")
                clean_list = [str(z) for z in session_zids if str(z) not in excluded_zids]
                deleted_count = db.soft_delete_sessions_batch(
                    session_zids=clean_list
                )
            self._send_json(200, {"ok": True, "deleted_count": deleted_count})
            return

        # 6.1 TSV Virtualization: Dynamic Export
        session_tsv_match = re.match(r"^/(?:api/v1/admin/sessions|api/v1/sessions|sessions)/([0-9a-zA-Z_\-]+)/tsv$", path)
        if session_tsv_match:
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            target_zid = session_tsv_match.group(1)
            adapter = get_storage_adapter(operation_configuration(self.server.config), self.server.resolved_paths)
            try:
                restored = adapter.restore_session(target_zid)
            except Exception as e:
                raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{target_zid}' not found: {e}")

            import io
            import csv
            output = io.StringIO()
            writer = csv.writer(output, delimiter='\t', lineterminator='\n')
            for comment in restored.get("comments", []):
                output.write(f"{comment}\n")
            headers = restored.get("headers", [])
            if headers:
                writer.writerow(headers)
            for row in restored.get("data_rows", []):
                sanitized_row = [str(cell).replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ') for cell in row]
                writer.writerow(sanitized_row)

            tsv_data = output.getvalue().encode('utf-8')
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            sess = db.get_session(str(target_zid)) or {}
            slug = sess.get("slug") or restored.get("slug") or restored.get("session", {}).get("slug", "") or ""
            lang = sess.get("source_language") or sess.get("source_lang") or restored.get("language") or ""

            slug_part = f"-{slug}" if slug else ""
            lang_part = f".{lang}" if lang else ""
            filename = f"{target_zid}{slug_part}{lang_part}.tsv"
            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'text/tab-separated-values; charset=utf-8')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(len(tsv_data)))
            self.end_headers()
            self.wfile.write(tsv_data)
            return

        # 6.2 TSV Virtualization: Drag-and-Drop / Ingestion
        if path in ('/api/v1/sessions/import-tsv', '/api/v1/admin/sessions/import-tsv'):
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")

            content_type = self.headers.get('Content-Type', '')
            filename = qs.get('filename', [None])[0]
            session_zid = qs.get('session_zid', [None])[0] or qs.get('zid', [None])[0]
            lang = qs.get('language', [None])[0]
            slug = qs.get('slug', [None])[0]

            if 'application/json' in content_type:
                body = self._read_json_body()
                self._authenticate_token(body, require_configured=True)
                tsv_content = body.get('tsv_content') or body.get('content') or body.get('tsv')
                filename = body.get('filename') or filename
                session_zid = body.get('session_zid') or body.get('zid') or session_zid
                lang = body.get('language') or lang
                slug = body.get('slug') or slug
            else:
                self._authenticate_token(query_params=qs, require_configured=True)
                content_len = int(self.headers.get('Content-Length', 0))
                tsv_bytes = self.rfile.read(content_len)
                tsv_content = tsv_bytes.decode('utf-8', errors='replace')

            if not tsv_content:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'tsv_content' in payload")

            from kardenwort_db import KardenwortDB
            req_zid = generate_server_zid(self.server)
            bundle = parse_tsv_to_bundle(
                tsv_content_or_path=tsv_content,
                filename=filename,
                session_zid=session_zid,
                language=lang,
                slug=slug,
                config=operation_configuration(self.server.config),
                resolved_paths=self.server.resolved_paths,
                zid=req_zid,
            )

            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            db.run_migrations(zid=req_zid)
            created_zid = db.save_session_bundle(
                session=bundle["session"],
                sentences=bundle["sentences"],
                words=bundle["words"],
                zid=req_zid,
            )

            self._send_json(200, {
                "ok": True,
                "session_zid": created_zid,
                "slug": bundle["session"].get("slug", ""),
                "source_language": bundle["session"].get("source_language", ""),
                "sentences_count": len(bundle["sentences"]),
                "words_count": len(bundle["words"]),
                "zid": req_zid
            })
            return


        # 7. Admin Trash & Soft Deletion Endpoints
        if path == '/api/v1/admin/trash':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            del_sessions = db.get_deleted_sessions()
            del_projects = db.get_deleted_projects()
            self._send_json(200, {
                "ok": True,
                "sessions": del_sessions,
                "projects": del_projects,
            })
            return

        if path == '/api/v1/admin/trash/restore':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)

            if 'zid' in body and body['zid']:
                session_zid = str(body['zid']).strip()
                ok = db.restore_session(session_zid)
                self._send_json(200, {"ok": ok, "restored_type": "session", "zid": session_zid})
                return
            elif 'project_id' in body and body['project_id'] is not None:
                project_id = int(body['project_id'])
                ok = db.restore_project(project_id)
                self._send_json(200, {"ok": ok, "restored_type": "project", "project_id": project_id})
                return
            else:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'zid' or 'project_id' in restore payload")

        if path == '/api/v1/admin/trash/purge':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            older_than_days = body.get('older_than_days')
            if older_than_days is not None:
                try:
                    older_than_days = float(older_than_days)
                except ValueError:
                    raise StructuredError(ErrorCode.INVALID_PAYLOAD, "'older_than_days' must be a numeric value")

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            purged_sessions = db.purge_deleted_sessions(older_than_days=older_than_days)
            purged_projects = db.purge_deleted_projects(older_than_days=older_than_days)
            self._send_json(200, {
                "ok": True,
                "purged_sessions": purged_sessions,
                "purged_projects": purged_projects
            })
            return

        # 8. Admin Database Maintenance & Telemetry Endpoints
        if path == '/api/v1/admin/backup/snapshot':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            req_zid = generate_server_zid(self.server)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            target_path = db.backup_snapshot(zid=req_zid)
            self._send_json(200, {
                "ok": True,
                "filename": target_path.name,
                "path": str(target_path),
                "bytes": target_path.stat().st_size if target_path.exists() else 0,
                "zid": req_zid
            })
            return

        if path == '/api/v1/admin/backup/dump.sql':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            req_zid = generate_server_zid(self.server)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            sql_dump = db.get_sql_dump(zid=req_zid).encode('utf-8')

            self.send_response(200)
            self._send_cors_headers()
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="kardenwort-dump.sql"')
            self.send_header('Content-Length', str(len(sql_dump)))
            self.end_headers()
            self.wfile.write(sql_dump)
            return

        if path == '/api/v1/admin/db/vacuum':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)
            req_zid = generate_server_zid(self.server)

            # The threaded HTTP server already owns this worker and its disposal.
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            db.vacuum(zid=req_zid)

            self._send_json(200, {
                "ok": True,
                "status": "dispatched",
                "completed": True,
                "message": "Database VACUUM and PRAGMA optimize completed",
                "zid": req_zid
            })
            return

        if path == '/api/v1/admin/telemetry':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            req_zid = generate_server_zid(self.server)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=operation_configuration(self.server.config), resolved_paths=self.server.resolved_paths)
            db_telemetry = db.get_telemetry(zid=req_zid)

            uptime = round(time.time() - getattr(self.server, 'start_time', time.time()), 2)
            sidecar_report = self.server.supervisor.get_status_report() if hasattr(self.server, 'supervisor') else {}

            self._send_json(200, {
                "ok": True,
                "database": db_telemetry,
                "controller": {
                    "port": self.server.server_port,
                    "uptime_seconds": uptime
                },
                "sidecars": sidecar_report,
                "zid": req_zid
            })
            return

        # 9. Shutdown Endpoint
        if path in ('/api/v1/shutdown', '/admin/shutdown'):
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body, require_configured=True)

            req_zid = generate_server_zid(self.server)
            self._send_json(200, {"zid": req_zid, "message": "Shutting down controller and sidecars."})

            self.server.request_shutdown()
            return

        raise StructuredError(ErrorCode.NOT_FOUND, f"Unknown API endpoint: {path}")


# ---------------------------------------------------------------------------
# Controller Entrypoint & Subcommand
# ---------------------------------------------------------------------------
def run_controller(args=None):
    config_path = getattr(args, 'config', None)
    config, resolved_paths, goldendict, _wordfill = load_config(config_path)

    enabled = goldendict.get('server_enabled', True)
    if not enabled:
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, "Controller is disabled in config.ini ([server] enabled = false)")

    host, port = _controller_binding(args, config, goldendict)

    server = ControllerHTTPServer((host, port), ControllerRequestHandler)
    with _controller_runtime(server):
        _initialize_controller(server, config, resolved_paths, goldendict, _wordfill, args)
        startup_zid = generate_unique_zid()
        logger.info(f"Kardenwort Controller Daemon started on {host}:{server.server_port} (ZID: {startup_zid})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Controller stopped by KeyboardInterrupt.")


def _controller_binding(args, config, goldendict):
    host = getattr(args, 'host', None) or goldendict.get('server_host', '127.0.0.1')
    port = getattr(args, 'port', None)
    if port is None:
        port = config.getint('server', 'controller_port', fallback=config.getint('server', 'port', fallback=goldendict.get('server_port', 18335)))
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, f"Controller host must be loopback (127.0.0.1). Specified: {host}")
    return host, port


def _initialize_controller(server, config, resolved_paths, goldendict, wordfill, args):
    server.disable_nagle_algorithm = True
    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.wordfill_cfg = wordfill
    refresh_preferences(config, resolved_paths)
    server.ui_cfg = resolve_ui_config(config)
    server.api_key = goldendict.get('server_api_key', '')
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()
    server.arbiter = SessionArbiter(config, resolved_paths, wordfill_cfg=wordfill)
    server.arbiter.goldendict = goldendict
    enable_supervisor = not getattr(args, 'no_sidecars', False)
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=enable_supervisor)
    if enable_supervisor:
        server.supervisor.start()


def _controller_cleanup(server):
    actions = [server.server_close, server.finish_shutdown]
    arbiter = getattr(server, 'arbiter', None)
    if arbiter is not None:
        actions.extend([arbiter.enrichment_queue.shutdown,
                        lambda: unregister_failover_callback(arbiter._on_global_failover)])
    supervisor = getattr(server, 'supervisor', None)
    if supervisor is not None:
        actions.append(supervisor.stop)
    return run_cleanup(actions)


@contextmanager
def _controller_runtime(server):
    failure = None
    try:
        yield server
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = _controller_cleanup(server)
        if errors:
            if failure is not None:
                errors.insert(0, failure)
            raise BaseExceptionGroup('Controller lifecycle failed', errors)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kardenwort Desk Central Controller Daemon")
    parser.add_argument("--host", default=None, help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to (default: 8080)")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    parser.add_argument("--no-sidecars", action="store_true", help="Do not spawn or supervise sidecar microservices")
    cli_args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_controller(cli_args)
