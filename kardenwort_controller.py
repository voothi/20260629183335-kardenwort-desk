import sys
import os
import re
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
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from b64util import encode

# Import core business logic primitives from kardenwort_desk
from kardenwort_desk import (
    load_config,
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
    query_spacy_server,
    query_translation_server,
    query_intellifiller_server,
    tokenize_text_with_fallback,
    translate_lemmas_fast_path,
    translate_source_text,
    run_headless_intellifiller,
    StructuredError,
    ErrorCode,
    generate_unique_zid,
    find_working_tsv,
    extract_zid,
    generate_slug,
    get_storage_adapter,
    parse_tsv_to_bundle,
    render_lookup_html,
    render_verify_language_html,
    run_render_flow,
    verify_language,
    synthesize_project_materials,
    aggregate_project_materials,
    resolve_project_deck_path,
    safe_write_update_js,
    format_translated_html,
    format_update_rows_dict,
    SessionLogger,
    find_wordfill_match,
    apply_wordfill_to_rows,
    resolve_wordfill_config,
    sort_rows_by_frequency,
    resolve_translations,
    SEC_SETTINGS,
    SEC_LANGUAGES,
    SEC_PIPELINE,
    SEC_TRIGGERS,
    SEC_SERVICES,
    SEC_TRANSLATION,
    SEC_TIMEOUTS,
    persist_default_language,
    spawn_ahk,
)

logger = logging.getLogger("kardenwort.desk.controller")

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

_DRAFT_SESSIONS: Dict[str, dict] = {}
_DRAFT_SESSIONS_LOCK = threading.Lock()


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
        if sys.platform == "win32" and self.job_handle:
            try:
                import ctypes
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel32.CloseHandle(self.job_handle)
                self.job_handle = None
            except Exception:
                pass


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
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._setup_service_definitions()

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
                    warmup_argos = self.config.getboolean(SEC_TRANSLATION, 'warmup_argos', fallback=False)
                else:
                    warmup_argos = False
                if warmup_argos:
                    trans_cmd.append("--warmup-argos")
            except Exception:
                pass

            try:
                argos_conc = self.config.get(SEC_TRANSLATION, 'argos_concurrency', fallback=None)
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
                    service.is_healthy = True
                    service.last_check = time.time()
                    return True
        except Exception:
            pass
        service.is_healthy = False
        service.last_check = time.time()
        return False

    def spawn_service(self, service: SidecarService) -> bool:
        with self._lock:
            if self.probe_health(service, timeout=0.5):
                logger.info(f"Sidecar '{service.name}' on port {service.port} is already running externally.")
                service.managed_by_supervisor = False
                return True

            if service.process and service.process.poll() is None:
                try:
                    service.process.terminate()
                    service.process.wait(timeout=1.0)
                except Exception:
                    pass

            cmd = service.launch_cmd
            script_path = Path(cmd[1]) if len(cmd) > 1 else None
            if script_path and not script_path.exists() and not (len(cmd) > 2 and cmd[1] == '-m'):
                logger.warning(f"Cannot launch sidecar '{service.name}': script {script_path} does not exist.")
                return False

            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(service.cwd) if service.cwd else None,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    close_fds=True,
                    env=env
                )
                service.process = proc
                service.managed_by_supervisor = True
                service.spawn_time = time.time()
                service.consecutive_failures = 0
                self.job.assign_process(proc)
                logger.info(f"Spawned sidecar '{service.name}' (PID: {proc.pid}) on port {service.port}")

                # Wait up to 15 seconds for initial health probe (accommodates heavy model loading)
                t_end = time.time() + 15.0
                while time.time() < t_end:
                    if proc.poll() is not None:
                        logger.warning(f"Sidecar '{service.name}' process exited unexpectedly with code {proc.poll()}.")
                        return False
                    if self.probe_health(service, timeout=0.5):
                        logger.info(f"Sidecar '{service.name}' is healthy on port {service.port}")
                        return True
                    time.sleep(0.3)
                logger.warning(f"Sidecar '{service.name}' spawned but health probe did not respond within 15s (process is still running with PID {proc.pid}).")
                return False
            except Exception as e:
                logger.error(f"Failed to spawn sidecar '{service.name}': {e}")
                return False

    def start(self):
        if not self.enabled:
            logger.info("Process supervisor is disabled.")
            return

        self._running = True
        logger.info("Starting ProcessSupervisor sidecar services...")
        for svc in self.services.values():
            self.spawn_service(svc)

        self._monitor_thread = threading.Thread(target=self._supervision_loop, daemon=True, name="SupervisorWatchdog")
        self._monitor_thread.start()

    def _supervision_loop(self):
        while self._running:
            time.sleep(5.0)
            if not self._running:
                break
            for svc in list(self.services.values()):
                healthy = self.probe_health(svc, timeout=1.5)
                if healthy:
                    svc.consecutive_failures = 0
                    continue
                if not svc.managed_by_supervisor:
                    continue

                # If process exited/crashed, restart immediately
                if svc.process is None or svc.process.poll() is not None:
                    exit_code = svc.process.poll() if svc.process else 'None'
                    logger.warning(f"Managed sidecar '{svc.name}' process exited (exit code: {exit_code}). Restarting...")
                    svc.restart_count += 1
                    svc.consecutive_failures = 0
                    self.spawn_service(svc)
                    continue

                # If process was spawned recently, give it a grace period to finish heavy initialization
                if time.time() - getattr(svc, 'spawn_time', 0.0) < 20.0:
                    continue

                # Require 3 consecutive probe failures before killing a running process
                svc.consecutive_failures = getattr(svc, 'consecutive_failures', 0) + 1
                if svc.consecutive_failures >= 3:
                    logger.warning(f"Health probe failed {svc.consecutive_failures} consecutive times for managed sidecar '{svc.name}' on port {svc.port}. Restarting...")
                    svc.restart_count += 1
                    svc.consecutive_failures = 0
                    self.spawn_service(svc)

    def stop(self):
        self._running = False
        logger.info("Stopping ProcessSupervisor and terminating managed sidecars...")
        with self._lock:
            for svc in self.services.values():
                if svc.managed_by_supervisor and svc.process:
                    try:
                        if svc.process.poll() is None:
                            svc.process.terminate()
                            svc.process.wait(timeout=1.5)
                    except Exception:
                        try:
                            svc.process.kill()
                        except Exception:
                            pass
            self.job.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def get_status_report(self) -> Dict[str, Any]:
        report = {}
        for name, svc in self.services.items():
            pid = svc.process.pid if svc.process and svc.process.poll() is None else None
            report[name] = {
                "port": svc.port,
                "healthy": svc.is_healthy,
                "pid": pid,
                "managed": svc.managed_by_supervisor,
                "restart_count": svc.restart_count,
                "last_check": svc.last_check
            }
        return report

    def get_service_status(self) -> Dict[str, Any]:
        """
        Returns a structured dictionary of sidecar health and ports.
        """
        status = {}
        for name, svc in self.services.items():
            status[name] = {
                "port": svc.port,
                "healthy": svc.is_healthy,
                "managed": svc.managed_by_supervisor,
            }
        return status


# ---------------------------------------------------------------------------
# Centralized Controller Enrichment Task Queue
# ---------------------------------------------------------------------------
class EnrichmentQueue:
    """
    Centralized controller task queue with bounded worker concurrency,
    in-memory lemma deduplication cache, rate-limiting pacing, and in-flight request coalescing.
    """

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
        self._cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._inflight_lemmas: Dict[Tuple[str, str], concurrent.futures.Future] = {}
        self._lemma_trans_cache: Dict[Tuple[str, str, str], str] = {}
        self._inflight_trans: Dict[Tuple[str, str, str], concurrent.futures.Future] = {}
        self._active_progressive_sessions: Dict[str, concurrent.futures.Future] = {}
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

        prov = provider or (self.config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google') if self.config else 'google')
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

    def _execute_enrich_lemma(
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
                batch_rows = [{"row_id": 0, "WordSource": norm_lemma, "WordDestination": "", "WordSourceIPA": "", "WordSourceMorphologyAI": ""}]
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
                        for k in ("WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"):
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
            headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
            rows = [[norm_lemma, "", "", ""]]
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
                    for field in ("WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"):
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
            if session_zid in self._active_progressive_sessions:
                existing_fut = self._active_progressive_sessions[session_zid]
                if not existing_fut.done():
                    return {"status": "in_progress", "session_zid": session_zid}

            fut = self._translation_executor.submit(
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
            )
            self._active_progressive_sessions[session_zid] = fut
            return {"status": "queued", "session_zid": session_zid}

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
    ):
        req_zid = zid or session_zid
        eff_trace_id = trace_id or f"{req_zid}:progressive:worker"
        storage_adapter = getattr(arbiter, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')
        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        mapping_path = Path(self.resolved_paths['anki_mapping_file']) if (self.resolved_paths and 'anki_mapping_file' in self.resolved_paths) else None
        mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None

        worker_error = None
        data_rows = []
        headers = []
        role_fields = {}
        sess_lang = language
        sess_target = target_lang
        tsv_path = None

        try:
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
                t_cand = find_working_tsv(results_dir, session_zid, language, storage_adapter=storage_adapter)
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
                        session_data = {
                            "text": restored.get("source_text") or restored.get("source_raw_text", ""),
                            "comments": restored.get("comments", []),
                            "headers": restored.get("headers", []),
                            "data_rows": restored.get("data_rows", []),
                            "language": restored.get("source_language") or language,
                            "target_lang": restored.get("target_language") or target_lang,
                            "text_mode": text_mode,
                            "tsv_path": restored.get("tsv_path"),
                        }

            if not session_data:
                logger.warning(f"Progressive task could not find session {session_zid}")
                return

            headers = session_data["headers"]
            data_rows = session_data["data_rows"]
            text = session_data["text"]
            sess_lang = session_data["language"] or language
            sess_target = session_data["target_lang"] or target_lang
            role_fields = get_role_fields(mapping, headers) if mapping else {}
            tsv_path = Path(session_data["tsv_path"]) if session_data.get("tsv_path") else (results_dir / f"{session_zid}.{sess_lang}.tsv" if results_dir else Path(f"{session_zid}.tsv"))

            col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
            col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
            col_sentence_idx = headers.index(role_fields['sentence_index']) if 'sentence_index' in role_fields and role_fields['sentence_index'] in headers else (headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1)
            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1

            # Emit initial source stage if not already emitted
            sorted_rows = sort_rows_by_frequency(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
            safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="source", zid=session_zid, trace_id=eff_trace_id)
            arbiter.emit_event(session_zid, {
                "type": "stage",
                "stage": "source",
                "status": "success",
                "rows": format_update_rows_dict(sorted_rows, headers, role_fields),
                "fingerprint": compute_content_fingerprint(data_rows),
            })

            # Stage 1: Text / Sentence Translation
            run_text = self.config.get(SEC_TRIGGERS, 'run_text_translation', fallback='auto') if self.config else 'auto'
            sentence_translated = False
            if col_sentence_dest != -1:
                if any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows):
                    sentence_translated = True

            active_text_prov = session_data.get("text_provenance") or session_data.get("textProvenance")
            if not active_text_prov and is_sqlite:
                try:
                    db_sents = storage_adapter.db.get_sentences_by_session(session_zid)
                    if db_sents:
                        active_text_prov = next((s.get("text_provenance") for s in db_sents if s.get("text_provenance")), None)
                except Exception:
                    pass

            if not sentence_translated and run_text == 'auto' and text:
                main_text_provider = self.config.get(SEC_PIPELINE, 'text_base_provider', fallback='google') if self.config else 'google'
                try:
                    sentence_translations_raw = translate_source_text(
                        text, sess_lang, sess_target, text_mode, self.config, self.resolved_paths, main_text_provider, zid=req_zid, trace_id=eff_trace_id
                    )
                    active_text_prov = getattr(sentence_translations_raw, "provenance", f"live:{main_text_provider}")
                    if is_sqlite and isinstance(sentence_translations_raw, dict):
                        for s_idx_raw, trans in sentence_translations_raw.items():
                            if trans and isinstance(trans, str):
                                s_idx = (int(s_idx_raw) + 1) if (isinstance(s_idx_raw, int) or str(s_idx_raw).isdigit()) else 1
                                try:
                                    storage_adapter.update_sentence_translation(session_zid, s_idx, trans, zid=req_zid, provenance=active_text_prov)
                                except Exception:
                                    try:
                                        storage_adapter.update_sentence_translation(session_zid, s_idx, trans, zid=req_zid)
                                    except Exception:
                                        pass

                    resolve_translations(
                        text, text_mode, data_rows, col_sentence_idx, col_sentence_dest,
                        sentence_translations_raw, tsv_path, session_data["comments"], headers,
                        persist=(not is_sqlite), return_single=False
                    )

                    new_fp = compute_content_fingerprint(data_rows)
                    with arbiter._lock:
                        if session_zid in arbiter.sessions:
                            arbiter.sessions[session_zid]["data_rows"] = data_rows
                            arbiter.sessions[session_zid]["fingerprint"] = new_fp
                            arbiter.sessions[session_zid]["sentence_translation"] = sentence_translations_raw
                            arbiter.sessions[session_zid]["text_provenance"] = active_text_prov
                            arbiter.sessions[session_zid]["textProvenance"] = active_text_prov

                    translated_html = format_translated_html(sentence_translations_raw, text_mode=text_mode, text=text, config=self.config)
                    sorted_rows = sort_rows_by_frequency(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                    structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields)
                    safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated_text", zid=session_zid, trace_id=eff_trace_id, translated_text=translated_html, text_translation_status="success", text_translation_failed=False, text_provenance=active_text_prov)
                    arbiter.emit_event(session_zid, {
                        "type": "update",
                        "stage": "translated_text",
                        "status": "success",
                        "text_translation_status": "success",
                        "textTranslationStatus": "success",
                        "text_translation_failed": False,
                        "textTranslationFailed": False,
                        "fingerprint": new_fp,
                        "rows": structured_rows,
                        "translated_text": translated_html,
                        "translatedText": translated_html,
                        "text_provenance": active_text_prov,
                        "textProvenance": active_text_prov,
                    })
                except Exception as text_err:
                    logger.warning(f"Sentence translation error in progressive queue for {session_zid}: {text_err}")
                    active_text_prov = None
                    sorted_rows = sort_rows_by_frequency(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                    structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields)
                    safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated_text", zid=session_zid, trace_id=eff_trace_id, translated_text="", text_translation_status="failed", text_translation_failed=True)
                    arbiter.emit_event(session_zid, {
                        "type": "update",
                        "stage": "translated_text",
                        "status": "success",
                        "text_translation_status": "failed",
                        "textTranslationStatus": "failed",
                        "text_translation_failed": True,
                        "textTranslationFailed": True,
                        "fingerprint": compute_content_fingerprint(data_rows),
                        "rows": structured_rows,
                        "translated_text": "",
                        "translatedText": "",
                    })

            # Stage 2: Lemma Base Translation
            run_base = self.config.get(SEC_TRIGGERS, 'run_lemma_base_translation', fallback='auto') if self.config else 'auto'
            if run_base == 'auto' and col_lemma != -1:
                # 2a. Wordfill pre-fill
                wordfill_cfg = getattr(arbiter, 'wordfill_cfg', None) or resolve_wordfill_config(self.config, self.resolved_paths)
                if wordfill_cfg and wordfill_cfg.get('enabled', False):
                    wf_applied = False
                    for row in data_rows:
                        if len(row) > col_lemma and row[col_lemma].strip():
                            l_val = row[col_lemma].strip()
                            is_trans = (col_word_dest != -1 and len(row) > col_word_dest and bool(row[col_word_dest].strip()))
                            if not is_trans:
                                match = find_wordfill_match(l_val, sess_lang, wordfill_cfg, exclude_path=tsv_path)
                                if match:
                                    apply_wordfill_to_rows([row], headers, match)
                                    wf_applied = True
                    if wf_applied:
                        if is_sqlite:
                            updates = []
                            for row_idx, row in enumerate(data_rows):
                                if col_lemma != -1 and len(row) > col_lemma and col_word_dest != -1 and len(row) > col_word_dest and row[col_word_dest].strip():
                                    t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                                    updates.append({"token_order": t_ord, "field": "word_destination", "value": row[col_word_dest]})
                            if updates:
                                storage_adapter.batch_update_words(session_zid=session_zid, updates_list=updates, zid=req_zid)
                        else:
                            with storage_adapter.file_lock(tsv_path):
                                storage_adapter.save_tsv_rows_safely(tsv_path, session_data["comments"], headers, data_rows)

                # 2b. Coalesced lemma translations
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

                if lemmas_to_translate:
                    lemma_provider = self.config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google') if self.config else 'google'
                    translated_map = self.translate_lemmas_coalesced(
                        lemmas_to_translate,
                        source_lang=sess_lang,
                        target_lang=sess_target,
                        provider=lemma_provider,
                        zid=req_zid,
                        trace_id=eff_trace_id,
                    )

                    if translated_map:
                        updates = []
                        lemma_prov_tag = f"live:{lemma_provider}"
                        for row_idx, row in enumerate(data_rows):
                            if col_lemma != -1 and len(row) > col_lemma:
                                l_val = row[col_lemma].strip()
                                if l_val in translated_map and col_word_dest != -1:
                                    t_val = translated_map[l_val]
                                    while len(row) <= col_word_dest:
                                        row.append("")
                                    row[col_word_dest] = t_val
                                    t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                                    updates.append({"token_order": t_ord, "field": "word_destination", "value": t_val})
                                    updates.append({"token_order": t_ord, "field": "word_provenance", "value": lemma_prov_tag})

                        if is_sqlite:
                            if updates:
                                storage_adapter.batch_update_words(session_zid=session_zid, updates_list=updates, zid=req_zid)
                        else:
                            with storage_adapter.file_lock(tsv_path):
                                storage_adapter.save_tsv_rows_safely(tsv_path, session_data["comments"], headers, data_rows)

                        # Propagate newly resolved lemma translations to sibling tabs!
                        arbiter.propagate_translations_to_siblings(translated_map, exclude_session_zid=session_zid, language=sess_lang)

                new_fp = compute_content_fingerprint(data_rows)
                sess_row_provs = {}
                with arbiter._lock:
                    if session_zid in arbiter.sessions:
                        arbiter.sessions[session_zid]["data_rows"] = data_rows
                        arbiter.sessions[session_zid]["fingerprint"] = new_fp
                        if active_text_prov:
                            arbiter.sessions[session_zid]["text_provenance"] = active_text_prov
                            arbiter.sessions[session_zid]["textProvenance"] = active_text_prov
                        sess_row_provs = arbiter.sessions[session_zid].get("row_provenances", {})

                if translated_map:
                    lemma_prov_tag = f"live:{lemma_provider}"
                    for row_idx, row in enumerate(data_rows):
                        if col_lemma != -1 and len(row) > col_lemma:
                            l_val = row[col_lemma].strip()
                            if l_val in translated_map:
                                t_ord = str(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).strip() else str(row_idx)
                                sess_row_provs[t_ord] = lemma_prov_tag
                                if t_ord.isdigit():
                                    sess_row_provs[int(t_ord)] = lemma_prov_tag

                sorted_rows = sort_rows_by_frequency(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields, row_provenances=sess_row_provs)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", zid=session_zid, trace_id=eff_trace_id, text_provenance=active_text_prov, row_provenances=sess_row_provs)
                trans_event = {
                    "type": "update",
                    "stage": "translated",
                    "status": "success",
                    "fingerprint": new_fp,
                    "rows": structured_rows,
                    "row_provenances": sess_row_provs,
                    "rowProvenances": sess_row_provs,
                }
                if active_text_prov:
                    trans_event["text_provenance"] = active_text_prov
                    trans_event["textProvenance"] = active_text_prov
                arbiter.emit_event(session_zid, trans_event)

            # Stage 3: Enrichment (IntelliFiller)
            run_enrich = self.config.get(SEC_TRIGGERS, 'run_lemma_enrichment', fallback='manual') if self.config else 'manual'
            enrich_provider = self.config.get(SEC_PIPELINE, 'lemma_reprocess_provider', fallback='intellifiller') if self.config else 'intellifiller'
            if not skip_intellifiller and run_enrich == 'auto' and enrich_provider == 'intellifiller':
                selected_rows = []
                col_ipa = headers.index(role_fields.get('ipa', 'WordSourceIPA')) if role_fields.get('ipa', 'WordSourceIPA') in headers else -1
                col_morph = headers.index(role_fields.get('morphology', 'WordSourceMorphology')) if role_fields.get('morphology', 'WordSourceMorphology') in headers else -1
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
            worker_error = {
                "code": "ERR_PROGRESSIVE_TASK_FAILED",
                "message": str(e),
                "provider": "controller",
                "details": {},
            }
        finally:
            with self._lock:
                if session_zid in self._active_progressive_sessions:
                    del self._active_progressive_sessions[session_zid]

            # Emit final finished event
            status_val = "failed" if worker_error else "success"
            new_fp = compute_content_fingerprint(data_rows) if data_rows else ""
            structured_rows = format_update_rows_dict(data_rows, headers, role_fields) if (data_rows and headers and role_fields) else {}
            if tsv_path and data_rows and headers:
                sorted_rows = sort_rows_by_frequency(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="finished", status=status_val, error=worker_error, zid=session_zid, trace_id=eff_trace_id, text_provenance=active_text_prov)

            finished_event = {
                "type": "update",
                "stage": "finished",
                "status": status_val,
                "error": worker_error,
                "fingerprint": new_fp,
                "rows": structured_rows,
            }
            if active_text_prov:
                finished_event["text_provenance"] = active_text_prov
                finished_event["textProvenance"] = active_text_prov
            arbiter.emit_event(session_zid, finished_event)

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

    def shutdown(self, wait: bool = True):
        self._executor.shutdown(wait=wait)
        self._translation_executor.shutdown(wait=wait)


# ---------------------------------------------------------------------------
# In-Memory Session Arbiter & SSE Event Dispatcher
# ---------------------------------------------------------------------------
class SessionArbiter:
    """
    Maintains centralized in-memory session states, serializes concurrent mutations,
    and manages Server-Sent Events (SSE) subscriber channels.
    """

    def __init__(self, config: Any, resolved_paths: Dict[str, Any], wordfill_cfg: Optional[Dict[str, Any]] = None):
        self.config = config
        self.resolved_paths = resolved_paths
        self.wordfill_cfg = wordfill_cfg or resolve_wordfill_config(config, resolved_paths)
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.subscribers: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()
        self.enrichment_queue = EnrichmentQueue(config, resolved_paths)

    def register_subscriber(self, session_zid: str) -> queue.Queue:
        q = queue.Queue(maxsize=1000)
        with self._lock:
            if session_zid not in self.subscribers:
                self.subscribers[session_zid] = []
            self.subscribers[session_zid].append(q)
        return q

    def unregister_subscriber(self, session_zid: str, q: queue.Queue):
        with self._lock:
            if session_zid in self.subscribers:
                try:
                    self.subscribers[session_zid].remove(q)
                    if not self.subscribers[session_zid]:
                        del self.subscribers[session_zid]
                except ValueError:
                    pass

    def emit_event(self, session_zid: str, event_data: Dict[str, Any]):
        event_payload = dict(event_data)
        event_payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        event_payload["session_zid"] = session_zid

        with self._lock:
            queues = list(self.subscribers.get(session_zid, []))

        for q in queues:
            try:
                q.put_nowait(event_payload)
            except queue.Full:
                pass

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
        if not init_text_prov and res.get("sentence_translation"):
            main_text_provider = self.config.get(SEC_PIPELINE, 'text_base_provider', fallback='google') if self.config else 'google'
            init_text_prov = f"live:{main_text_provider}"

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
                "text_provenance": init_text_prov,
                "textProvenance": init_text_prov,
                "row_provenances": init_row_provenances,
                "fingerprint": res["fingerprint"],
                "lock": threading.Lock(),
                "created_at": time.time(),
            }

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
            "rows": res["data_rows"],
            "row_provenances": init_row_provenances,
            "rowProvenances": init_row_provenances,
            "fingerprint": res["fingerprint"]
        })

        if "html" in res:
            res["html_b64"] = encode(res["html"])

        return res

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

        sess_lock = session["lock"] if session else self._lock
        with sess_lock:
            res = core_edit_save(
                tsv_path_or_session=session_zid,
                deltas=deltas,
                config=self.config,
                resolved_paths=self.resolved_paths,
                fingerprint=fingerprint,
                zid=req_zid,
                language=language or (session.get("language") if session else None),
            )

            if session:
                session["fingerprint"] = res["fingerprint"]
                if Path(res.get("tsv_path", "")).exists():
                    try:
                        _, _, disk_rows = load_tsv_rows(Path(res["tsv_path"]))
                        session["data_rows"] = disk_rows
                    except Exception:
                        pass

        # Broadcast state mutation event to all connected SSE clients
        self.emit_event(session_zid, {
            "type": "update",
            "stage": "saved",
            "status": "success",
            "fingerprint": res["fingerprint"],
            "deltas": deltas,
            "zid": res["zid"]
        })

        return res

    def retext_session(
        self,
        session_zid: str,
        language: Optional[str] = None,
        text_mode: str = "single",
        zid: Optional[str] = None
    ) -> Dict[str, Any]:
        req_zid = zid or generate_unique_zid()
        lang = language or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        target_lang = self.config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

        tsv_path = None
        session_text = ""
        with self._lock:
            if session_zid in self.sessions:
                sess = self.sessions[session_zid]
                session_text = sess.get("text", "")
                if sess.get("tsv_path"):
                    cand = Path(sess["tsv_path"])
                    if cand.exists():
                        tsv_path = cand
                    else:
                        tsv_path = cand
                        comments = sess.get("comments", [])
                        headers = sess.get("headers", [])
                        data_rows = sess.get("data_rows", [])
                        if headers and data_rows:
                            try:
                                tsv_path.parent.mkdir(parents=True, exist_ok=True)
                                save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                            except Exception:
                                pass

        if not tsv_path:
            results_dir = resolve_results_dir(self.resolved_paths, self.config)
            tsv_path = find_working_tsv(results_dir, session_zid, lang, storage_adapter=storage_adapter)
            if not tsv_path:
                tsv_path = results_dir / f"{session_zid}.{lang}.tsv"

        if not is_sqlite and (not tsv_path or not tsv_path.exists()):
            raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found for session {session_zid}")

        source_txt = tsv_path.with_suffix('.txt') if tsv_path else None
        if not is_sqlite and source_txt and source_txt.exists():
            text = source_txt.read_text(encoding='utf-8')
        elif session_text:
            text = session_text
            if not is_sqlite and source_txt:
                try:
                    source_txt.write_text(text, encoding='utf-8')
                except Exception:
                    pass
        else:
            # Fallback: recover source text from storage_adapter / SQLite session record
            recovered_text = ""
            try:
                bundle = storage_adapter.restore_session(session_zid)
                recovered_text = bundle.get("source_text", "") or ""
                if recovered_text:
                    # Lazy session warm-up: populate in-memory cache so subsequent calls skip SQLite
                    with self._lock:
                        if session_zid not in self.sessions:
                            self.sessions[session_zid] = {}
                        self.sessions[session_zid]["text"] = recovered_text
                        if bundle.get("tsv_path"):
                            self.sessions[session_zid]["tsv_path"] = str(bundle["tsv_path"])
                        if bundle.get("data_rows") is not None:
                            self.sessions[session_zid]["data_rows"] = bundle["data_rows"]
                        if bundle.get("headers") is not None:
                            self.sessions[session_zid]["headers"] = bundle["headers"]
                        if bundle.get("comments") is not None:
                            self.sessions[session_zid]["comments"] = bundle["comments"]
            except Exception:
                pass
            if not recovered_text and source_txt and source_txt.exists():
                text = source_txt.read_text(encoding='utf-8')
            elif not recovered_text:
                raise StructuredError(ErrorCode.DESK_FAILED, f"Source text not recoverable for session {session_zid}")
            else:
                text = recovered_text

        provider = self.config.get(SEC_PIPELINE, 'text_reprocess_provider', fallback='deepl')

        # Translate in-memory
        sentence_trans = translate_source_text(text, lang, target_lang, text_mode, self.config, self.resolved_paths, provider, zid=req_zid)

        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
            mapping = load_anki_mapping(self.resolved_paths['anki_mapping_file'])
            role_fields = get_role_fields(mapping, headers)
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1

            if col_sentence_dest != -1:
                for row in data_rows:
                    if len(row) > col_sentence_dest and sentence_trans:
                        row[col_sentence_dest] = list(sentence_trans.values())[0] if isinstance(sentence_trans, dict) else str(sentence_trans)
                storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)

        new_fp = compute_content_fingerprint(data_rows)
        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["fingerprint"] = new_fp

        translated_html = format_translated_html(sentence_trans, text_mode=text_mode, text=text, config=self.config)
        structured_rows = format_update_rows_dict(data_rows, headers, role_fields)
        safe_write_update_js(
            tsv_path,
            data_rows,
            headers,
            role_fields,
            stage="finished",
            status="success",
            source_text="",
            translated_text=translated_html,
            zid=session_zid,
            config=self.config
        )

        self.emit_event(session_zid, {
            "type": "update",
            "stage": "translated_text",
            "status": "success",
            "fingerprint": new_fp,
            "rows": structured_rows,
            "translated_text": translated_html,
            "translatedText": translated_html
        })

        return {
            "status": "success",
            "session_zid": session_zid,
            "fingerprint": new_fp,
            "data_rows": data_rows,
            "rows": structured_rows,
            "translated_text": translated_html,
            "translatedText": translated_html
        }

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

            if not data_rows or not headers:
                continue

            col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
            if col_lemma == -1:
                continue

            modified = False
            for row in data_rows:
                if len(row) <= col_lemma:
                    continue
                lemma_val = row[col_lemma].strip()
                if lemma_val in enriched_lemmas:
                    enrich_dict = enriched_lemmas[lemma_val]
                    for field_name, val in enrich_dict.items():
                        if not val:
                            continue
                        if field_name not in headers:
                            headers.append(field_name)
                            for dr in data_rows:
                                dr.append("")
                        col_idx = headers.index(field_name)
                        while len(row) <= col_idx:
                            row.append("")
                        if not row[col_idx].strip() or 'skeleton-loader' in row[col_idx] or row[col_idx] == '[FAILED]':
                            row[col_idx] = str(val)
                            modified = True

            if modified:
                new_fp = compute_content_fingerprint(data_rows)
                with self._lock:
                    if sib_zid in self.sessions:
                        self.sessions[sib_zid]["data_rows"] = data_rows
                        self.sessions[sib_zid]["headers"] = headers
                        self.sessions[sib_zid]["fingerprint"] = new_fp

                # Persist to TSV or SQLite
                sib_tsv = find_working_tsv(results_dir, sib_zid, sess_lang, storage_adapter=storage_adapter)
                if sib_tsv:
                    try:
                        with storage_adapter.file_lock(sib_tsv):
                            comments, _, _ = storage_adapter.load_tsv_rows(sib_tsv)
                            storage_adapter.save_tsv_rows_safely(sib_tsv, comments, headers, data_rows)
                    except Exception as e:
                        logger.warning(f"Failed to save propagated sibling TSV {sib_zid}: {e}")

                structured_rows = format_update_rows_dict(data_rows, headers, role_fields)
                self.emit_event(sib_zid, {
                    "type": "update",
                    "stage": "enrichment",
                    "status": "success",
                    "fingerprint": new_fp,
                    "rows": structured_rows,
                })

    def reword_session(
        self,
        session_zid: str,
        selected_rows: List[int],
        prompt: Optional[str] = None,
        language: Optional[str] = None,
        zid: Optional[str] = None
    ) -> Dict[str, Any]:
        req_zid = zid or generate_unique_zid()
        lang = language or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        prompt_name = prompt or self.config.get(SEC_LANGUAGES, f"{lang}_prompt", fallback="")

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        tsv_path = find_working_tsv(results_dir, session_zid, lang, storage_adapter=storage_adapter)
        if not tsv_path:
            tsv_path = results_dir / f"{session_zid}.{lang}.tsv"

        if not is_sqlite and (not tsv_path or not tsv_path.exists()):
            raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found for session {session_zid}")

        with storage_adapter.file_lock(tsv_path):
            comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)

        mapping = load_anki_mapping(self.resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1

        # Enforce frequency sort parity so selected_rows match displayed UI table rows
        data_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)

        wordfill_cfg = getattr(self, 'wordfill_cfg', None) or resolve_wordfill_config(self.config, self.resolved_paths)
        if wordfill_cfg and wordfill_cfg.get('enabled', False) and col_lemma != -1:
            target_quality = wordfill_cfg.get('target_quality', 'any')
            target_quality_tier = {'any': 0, 'partial': 1, 'full': 2}.get(target_quality, 0)
            remaining_selected = []
            for row_id in selected_rows:
                if 0 <= row_id < len(data_rows):
                    row = data_rows[row_id]
                    if len(row) > col_lemma and row[col_lemma].strip():
                        lemma_val = row[col_lemma].strip()
                        match = find_wordfill_match(lemma_val, lang, wordfill_cfg, exclude_path=tsv_path)
                        if match:
                            has_ipa = bool(match.get('WordSourceIPA', '').strip())
                            has_morph = bool(match.get('WordSourceMorphologyAI', '').strip())
                            tier = 2 if (has_ipa and has_morph) else (1 if (has_ipa or has_morph) else 0)
                            if tier >= target_quality_tier:
                                apply_wordfill_to_rows([row], headers, match)
                                logger.info(
                                    f"wordfill (reword_session): pre-filled quality tier {tier} for row {row_id} lemma '{lemma_val}' "
                                    f"from corpus; skipping IntelliFiller."
                                )
                                continue
                remaining_selected.append(row_id)

            if len(remaining_selected) < len(selected_rows):
                with storage_adapter.file_lock(tsv_path):
                    storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                selected_rows = remaining_selected

        enriched_lemma_map: Dict[str, Dict[str, str]] = {}
        if selected_rows:
            if is_sqlite:
                try:
                    storage_adapter.enrich_session_intellifiller(
                        session_zid=session_zid,
                        prompt_name=prompt_name,
                        selected_rows=selected_rows,
                        reprocess=True,
                        zid=req_zid,
                    )
                except StructuredError:
                    raise
                except Exception as e:
                    raise StructuredError(ErrorCode.DESK_FAILED, f"Re-word failed: {e}") from e
                comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                data_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)
                col_w_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
                col_w_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else -1
                col_w_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else -1
                for r_idx in selected_rows:
                    if 0 <= r_idx < len(data_rows):
                        r = data_rows[r_idx]
                        if col_lemma != -1 and len(r) > col_lemma:
                            l_val = r[col_lemma].strip()
                            if l_val:
                                item_enrich = {}
                                if col_w_dest != -1 and len(r) > col_w_dest and r[col_w_dest].strip():
                                    item_enrich["WordDestination"] = r[col_w_dest].strip()
                                if col_w_ipa != -1 and len(r) > col_w_ipa and r[col_w_ipa].strip():
                                    item_enrich["WordSourceIPA"] = r[col_w_ipa].strip()
                                if col_w_morph != -1 and len(r) > col_w_morph and r[col_w_morph].strip():
                                    item_enrich["WordSourceMorphologyAI"] = r[col_w_morph].strip()
                                if item_enrich:
                                    self.enrichment_queue.set_cached(l_val, lang, item_enrich)
                                    enriched_lemma_map[l_val] = item_enrich
            else:
                try:
                    rows_to_enrich = []
                    for r_idx in selected_rows:
                        if 0 <= r_idx < len(data_rows):
                            row = data_rows[r_idx]
                            lemma_val = row[col_lemma].strip() if col_lemma != -1 and len(row) > col_lemma else ""
                            if not lemma_val:
                                continue
                            cached = self.enrichment_queue.get_cached(lemma_val, lang)
                            if cached:
                                for k, v in cached.items():
                                    if k not in headers:
                                        headers.append(k)
                                        for dr in data_rows:
                                            dr.append("")
                                    c_idx = headers.index(k)
                                    while len(row) <= c_idx:
                                        row.append("")
                                    row[c_idx] = str(v)
                                enriched_lemma_map[lemma_val] = cached
                            else:
                                rows_to_enrich.append(r_idx)

                    if rows_to_enrich:
                        run_headless_intellifiller(
                            tsv_path,
                            prompt_name,
                            self.config,
                            self.resolved_paths,
                            selected_rows=rows_to_enrich,
                            reprocess=True,
                            zid=req_zid,
                        )
                        comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                        data_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)
                        col_w_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
                        col_w_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else -1
                        col_w_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else -1
                        for r_idx in rows_to_enrich:
                            if 0 <= r_idx < len(data_rows):
                                r = data_rows[r_idx]
                                if col_lemma != -1 and len(r) > col_lemma:
                                    l_val = r[col_lemma].strip()
                                    if l_val:
                                        item_enrich = {}
                                        if col_w_dest != -1 and len(r) > col_w_dest and r[col_w_dest].strip():
                                            item_enrich["WordDestination"] = r[col_w_dest].strip()
                                        if col_w_ipa != -1 and len(r) > col_w_ipa and r[col_w_ipa].strip():
                                            item_enrich["WordSourceIPA"] = r[col_w_ipa].strip()
                                        if col_w_morph != -1 and len(r) > col_w_morph and r[col_w_morph].strip():
                                            item_enrich["WordSourceMorphologyAI"] = r[col_w_morph].strip()
                                        if item_enrich:
                                            self.enrichment_queue.set_cached(l_val, lang, item_enrich)
                                            enriched_lemma_map[l_val] = item_enrich
                    else:
                        with storage_adapter.file_lock(tsv_path):
                            storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                except StructuredError:
                    raise
                except Exception as e:
                    raise StructuredError(ErrorCode.DESK_FAILED, f"Re-word failed: {e}") from e

            # Propagate newly enriched lemmas to open sibling sessions!
            if enriched_lemma_map:
                self.propagate_enrichment_to_siblings(enriched_lemma_map, exclude_session_zid=session_zid, language=lang)

        new_fp = compute_content_fingerprint(data_rows)

        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["fingerprint"] = new_fp

        safe_write_update_js(
            tsv_path,
            data_rows,
            headers,
            role_fields,
            stage="finished",
            status="success",
            zid=session_zid,
            config=self.config
        )

        structured_rows = format_update_rows_dict(data_rows, headers, role_fields)

        self.emit_event(session_zid, {
            "type": "update",
            "stage": "enrichment",
            "status": "success",
            "fingerprint": new_fp,
            "rows": structured_rows
        })

        return {
            "status": "success",
            "session_zid": session_zid,
            "fingerprint": new_fp,
            "data_rows": data_rows,
            "rows": structured_rows
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

            if not data_rows or not headers:
                continue

            col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
            col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
            col_token_order = headers.index("TokenOrder") if "TokenOrder" in headers else -1

            if col_lemma == -1 or col_word_dest == -1:
                continue

            modified = False
            updates = []
            for row_idx, row in enumerate(data_rows):
                if len(row) <= col_lemma:
                    continue
                lemma_val = row[col_lemma].strip()
                if lemma_val in lemma_translations:
                    trans_val = lemma_translations[lemma_val]
                    while len(row) <= col_word_dest:
                        row.append("")
                    if not row[col_word_dest].strip() or 'skeleton-loader' in row[col_word_dest] or row[col_word_dest] == '[FAILED]':
                        row[col_word_dest] = trans_val
                        modified = True
                        t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                        updates.append({"token_order": t_ord, "field": "word_destination", "value": trans_val})

            if modified:
                new_fp = compute_content_fingerprint(data_rows)
                with self._lock:
                    if sib_zid in self.sessions:
                        self.sessions[sib_zid]["data_rows"] = data_rows
                        self.sessions[sib_zid]["fingerprint"] = new_fp

                if is_sqlite:
                    if updates:
                        try:
                            storage_adapter.batch_update_words(session_zid=sib_zid, updates_list=updates, zid=sib_zid)
                        except Exception as e:
                            logger.warning(f"Failed to batch update SQLite words for sibling {sib_zid}: {e}")
                else:
                    sib_tsv = find_working_tsv(results_dir, sib_zid, sess_lang, storage_adapter=storage_adapter)
                    if sib_tsv:
                        try:
                            with storage_adapter.file_lock(sib_tsv):
                                comments, _, _ = storage_adapter.load_tsv_rows(sib_tsv)
                                storage_adapter.save_tsv_rows_safely(sib_tsv, comments, headers, data_rows)
                        except Exception as e:
                            logger.warning(f"Failed to save propagated sibling TSV {sib_zid}: {e}")

                sorted_rows = sort_rows_by_frequency(data_rows, headers, sess_lang, self.config, self.resolved_paths, role_fields=role_fields)
                structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields)
                if not is_sqlite and sib_tsv:
                    safe_write_update_js(sib_tsv, sorted_rows, headers, role_fields, stage="translated", zid=sib_zid)
                self.emit_event(sib_zid, {
                    "type": "update",
                    "stage": "translated",
                    "status": "success",
                    "fingerprint": new_fp,
                    "rows": structured_rows,
                })

    def retry_session_rows(
        self,
        session_zid: str,
        row_ids: Optional[List[int]] = None,
        language: Optional[str] = None,
        target_lang: Optional[str] = None,
        zid: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        req_zid = zid or generate_unique_zid()
        eff_trace_id = trace_id or f"{session_zid}:retry"
        lang = language or self.config.get(SEC_SETTINGS, 'default_language', fallback='en')
        target_l = target_lang or self.config.get(SEC_SETTINGS, 'default_target_language', fallback='ru')

        storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
        is_sqlite = (getattr(storage_adapter, 'backend_name', '') == 'sqlite')

        tsv_path = None
        with self._lock:
            if session_zid in self.sessions:
                sess = self.sessions[session_zid]
                if sess.get("language"):
                    lang = sess["language"]
                if sess.get("target_lang"):
                    target_l = sess["target_lang"]
                if sess.get("tsv_path"):
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

        data_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)

        if col_lemma == -1:
            raise StructuredError(ErrorCode.DESK_FAILED, f"Lemma column not found for session {session_zid}")

        target_indices = []
        if row_ids is not None and len(row_ids) > 0:
            target_indices = [idx for idx in row_ids if 0 <= idx < len(data_rows)]
        else:
            for idx, r in enumerate(data_rows):
                if len(r) > col_lemma and r[col_lemma].strip():
                    dest_val = r[col_word_dest].strip() if col_word_dest != -1 and len(r) > col_word_dest else ""
                    if not dest_val or 'skeleton-loader' in dest_val or dest_val == '[FAILED]':
                        target_indices.append(idx)

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
            provider = self.config.get(SEC_PIPELINE, 'lemma_base_provider', fallback='google') if self.config else 'google'
            try:
                translated_map = translate_lemmas_fast_path(
                    lemmas_to_retry,
                    lang,
                    target_l,
                    self.config,
                    self.resolved_paths,
                    provider,
                )
                fast_prov = getattr(translated_map, 'provenance', None) or f"live:{provider}"
            except Exception as e:
                logger.error(f"Retry translation failed: {e}")
                raise StructuredError(ErrorCode.DESK_FAILED, f"Retry translation failed: {e}") from e

        if translated_map:
            if col_word_dest == -1:
                headers.append(role_fields.get('word_translation', 'WordDestination'))
                col_word_dest = len(headers) - 1
                for r in data_rows:
                    r.append("")

            updates = []
            for row_idx in target_indices:
                row = data_rows[row_idx]
                if len(row) > col_lemma:
                    l_val = row[col_lemma].strip()
                    if l_val in translated_map:
                        t_val = translated_map[l_val]
                        while len(row) <= col_word_dest:
                            row.append("")
                        row[col_word_dest] = t_val
                        t_ord = int(row[col_token_order]) if col_token_order != -1 and len(row) > col_token_order and str(row[col_token_order]).isdigit() else row_idx
                        updates.append({"token_order": t_ord, "field": "word_destination", "value": t_val})

            if is_sqlite:
                if updates:
                    try:
                        storage_adapter.batch_update_words(session_zid=session_zid, updates_list=updates, zid=req_zid)
                    except Exception as e:
                        logger.warning(f"Failed to batch update words during retry for session {session_zid}: {e}")
            else:
                with storage_adapter.file_lock(tsv_path):
                    storage_adapter.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)

            self.propagate_translations_to_siblings(translated_map, exclude_session_zid=session_zid, language=lang)

        translated_text_res = None
        need_sentence_trans = False
        text_provider = None
        if row_ids is None or len(row_ids) == 0:
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
            col_sentence_dest2 = headers.index('SentenceDestination2') if 'SentenceDestination2' in headers else (headers.index(role_fields['sentence_destination2']) if 'sentence_destination2' in role_fields and role_fields['sentence_destination2'] in headers else -1)
            col_sentence_index = headers.index(role_fields.get('sentence_index', 'SentenceSourceIndex')) if role_fields.get('sentence_index', 'SentenceSourceIndex') in headers else -1

            source_raw_text = ""
            text_mode = 'single'
            if is_sqlite:
                restored = storage_adapter.restore_session(session_zid)
                source_raw_text = restored.get("source_raw_text") or restored.get("source_text", "")
                text_mode = restored.get("text_mode") or 'single'
            else:
                txt_cand = tsv_path.with_suffix('.txt') if tsv_path else None
                if txt_cand and txt_cand.exists():
                    source_raw_text = txt_cand.read_text(encoding='utf-8')

            need_sentence_trans = False
            if source_raw_text and col_sentence_dest != -1:
                if not any(len(r) > col_sentence_dest and r[col_sentence_dest].strip() for r in data_rows):
                    need_sentence_trans = True

            if need_sentence_trans and source_raw_text:
                text_provider = self.config.get(SEC_PIPELINE, 'text_base_provider', fallback='google') if self.config else 'google'
                try:
                    sentence_translations_raw = translate_source_text(
                        source_raw_text, lang, target_l, text_mode, self.config, self.resolved_paths, text_provider, zid=req_zid, trace_id=eff_trace_id
                    )
                    if is_sqlite and isinstance(sentence_translations_raw, dict):
                        padded_dict = sentence_translations_raw.get('PADDED') or {}
                        for s_idx_raw, trans in sentence_translations_raw.items():
                            if s_idx_raw in ('FULL_TEXT', 'PADDED'):
                                continue
                            if trans and isinstance(trans, str):
                                s_idx = (int(s_idx_raw) + 1) if (isinstance(s_idx_raw, int) or str(s_idx_raw).isdigit()) else 1
                                try:
                                    storage_adapter.update_sentence_translation(session_zid, s_idx, trans, target_field="sentence_destination", zid=req_zid)
                                except Exception:
                                    pass
                                if padded_dict:
                                    padded_trans = padded_dict.get(s_idx_raw) or padded_dict.get(int(s_idx_raw) if str(s_idx_raw).isdigit() else s_idx_raw)
                                    if padded_trans and isinstance(padded_trans, str):
                                        try:
                                            storage_adapter.update_sentence_translation(session_zid, s_idx, padded_trans, target_field="sentence_destination2", zid=req_zid)
                                        except Exception:
                                            pass

                    resolve_translations(
                        source_raw_text, text_mode, data_rows, col_sentence_index, col_sentence_dest,
                        sentence_translations_raw, tsv_path, comments, headers,
                        col_sentence_dest2=col_sentence_dest2,
                        persist=(not is_sqlite), return_single=False
                    )
                    translated_text_res = format_translated_html(sentence_translations_raw, text_mode=text_mode, text=source_raw_text, config=self.config)
                except Exception as text_err:
                    logger.warning(f"Sentence translation retry failed: {text_err}")

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
        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["headers"] = headers
                self.sessions[session_zid]["fingerprint"] = new_fp
                if retried_row_provenances:
                    if "row_provenances" not in self.sessions[session_zid]:
                        self.sessions[session_zid]["row_provenances"] = {}
                    self.sessions[session_zid]["row_provenances"].update(retried_row_provenances)

        sorted_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)
        structured_rows = format_update_rows_dict(sorted_rows, headers, role_fields, row_provenances=retried_row_provenances)
        effective_text_prov = f"live:{text_provider}" if (need_sentence_trans and translated_text_res) else None

        if not is_sqlite and tsv_path:
            safe_write_update_js(tsv_path, sorted_rows, headers, role_fields, stage="translated", status="success", translated_text=translated_text_res, text_provenance=effective_text_prov, zid=session_zid, trace_id=eff_trace_id, row_provenances=retried_row_provenances)

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
        return res_payload

    def get_queue_status(self) -> Dict[str, Any]:
        return self.enrichment_queue.get_status()


# ---------------------------------------------------------------------------
# Controller HTTP Request Handler
# ---------------------------------------------------------------------------
def generate_server_zid(server) -> str:
    now = datetime.now()
    with server.seq_lock:
        server.seq_counter = (server.seq_counter + 1) % 10000
        seq = server.seq_counter
    return f"{now:%Y%m%d%H%M%S}-{seq:04d}"


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
        if self.path and ('/health' in self.path or '/events' in self.path):
            return
        logger.info("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format_str % args))

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'X-API-Token, X-ZID, Content-Type, Authorization')
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

        if content_length > 1024 * 1024:  # 1MB limit
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, "Payload size exceeds 1MB limit")

        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode('utf-8'))
        except Exception as e:
            raise StructuredError(ErrorCode.INVALID_PAYLOAD, f"Malformed JSON payload: {e}")

    def _authenticate_token(self, body_data=None, query_params=None):
        api_key = getattr(self.server, 'api_key', '')
        if not api_key:
            return

        provided_token = self.headers.get('X-API-Token') or self.headers.get('X-API-Key')
        if not provided_token:
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                provided_token = auth_header[7:].strip()
        if not provided_token and body_data and isinstance(body_data, dict):
            provided_token = body_data.get('token')
        if not provided_token and query_params and isinstance(query_params, dict):
            token_list = query_params.get('token', [])
            if not token_list:
                token_list = query_params.get('api_token', [])
            if token_list:
                provided_token = token_list[0]

        import hmac
        if not provided_token or not hmac.compare_digest(str(provided_token).strip(), str(api_key).strip()):
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

    def _handle_sse_events(self, zid: str):
        """Streams Server-Sent Events (SSE) for the specified session ZID."""
        arbiter: SessionArbiter = self.server.arbiter
        q = arbiter.register_subscriber(zid)

        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

        # Send initial connected greeting
        init_data = json.dumps({"type": "connected", "session_zid": zid, "time": time.time()})
        self.wfile.write(f"data: {init_data}\n\n".encode('utf-8'))
        self.wfile.flush()

        try:
            while True:
                try:
                    event = q.get(timeout=10.0)
                    msg = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    self.wfile.write(msg.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    # Heartbeat comment to detect client disconnection
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (socket.error, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            arbiter.unregister_subscriber(zid, q)

    def _dispatch_route(self, method: str):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        qs = urllib.parse.parse_qs(parsed_url.query)
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
                db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
                db_status = db.get_status()
            except Exception as e:
                db_status = {"ok": False, "error": str(e)}

            ctrl_port = getattr(self.server, 'server_port', None)
            if ctrl_port is None:
                ctrl_port = self.server.server_address[1] if hasattr(self.server, 'server_address') else 18335

            self._send_json(200, {
                "ok": True,
                "status": "running",
                "controller": {
                    "port": ctrl_port,
                    "uptime_seconds": uptime
                },
                "services": services_status,
                "sidecars": supervisor_report,
                "database": db_status
            })
            return

        # 2. Server-Sent Events (SSE) Stream
        if path == '/events':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            zid = qs.get('zid', [''])[0]
            if not zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'zid' query parameter for SSE stream")
            self._handle_sse_events(zid)
            return

        # 3. Session Endpoints
        if path == '/session/create':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

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

        if path == '/session/save':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

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
            self._authenticate_token(body)

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
            self._authenticate_token(body)

            session_zid = body.get('session_zid')
            selected_rows = body.get('row_ids') or body.get('selected_rows') or []
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            res = self.server.arbiter.reword_session(
                session_zid=session_zid,
                selected_rows=selected_rows,
                prompt=body.get('prompt'),
                language=body.get('language'),
                zid=body.get('zid')
            )
            self._send_json(200, res)
            return

        if path == '/session/retry':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            session_zid = body.get('session_zid') or body.get('zid')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            row_ids = body.get('row_ids') or body.get('selected_rows') or []
            if isinstance(row_ids, (int, str)) and str(row_ids).isdigit():
                row_ids = [int(row_ids)]
            elif not isinstance(row_ids, list):
                row_ids = []
            row_ids = [int(r) for r in row_ids if str(r).isdigit() or isinstance(r, int)]

            res = self.server.arbiter.retry_session_rows(
                session_zid=session_zid,
                row_ids=row_ids if row_ids else None,
                language=body.get('language'),
                target_lang=body.get('target_lang'),
                zid=body.get('zid'),
                trace_id=body.get('trace_id'),
            )
            self._send_json(200, res)
            return

        if path == '/session/export':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            session_zid = body.get('session_zid')
            selected_rows = body.get('selected_row_ids') or body.get('row_ids') or []
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")

            res = core_export(
                tsv_path_or_session=session_zid,
                selected_row_ids=selected_rows,
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                language=body.get('language'),
                zid=body.get('zid') or session_zid,
                trace_id=f"{session_zid}:export:selection",
            )
            self._send_json(200, res)
            return

        if path == '/session/progressive/enqueue':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

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
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            status_dict = db.get_worker_status(target_zid)
            self._send_json(200, status_dict)
            return

        if path == '/session/status':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            zid = qs.get('zid', [''])[0]
            if not zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'zid' query parameter")

            mapping_path = None
            if self.server.resolved_paths and "anki_mapping_file" in self.server.resolved_paths:
                mapping_path = Path(self.server.resolved_paths["anki_mapping_file"])
            elif self.server.config and hasattr(self.server.config, "get"):
                raw_mp = self.server.config.get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
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
                if data_rows and headers:
                    data_rows = sort_rows_by_frequency(
                        data_rows, headers, sess_lang, self.server.config, self.server.resolved_paths, role_fields=role_fields
                    )
                sess_row_provs = safe_sess.get("row_provenances")
                if sess_row_provs is None:
                    sess_row_provs = {}
                safe_sess["rows"] = format_update_rows_dict(data_rows, headers, role_fields, row_provenances=sess_row_provs)
                safe_sess["row_provenances"] = sess_row_provs
                safe_sess["rowProvenances"] = sess_row_provs
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
                            config=self.server.config,
                        )
                    else:
                        safe_sess["translatedText"] = ""
                self._send_json(200, safe_sess)
                return

            # Fallback: check persistent storage (SQLite or TSV)
            results_dir = resolve_results_dir(self.server.resolved_paths, self.server.config)
            storage_adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)

            tsv_path = find_working_tsv(results_dir, zid) if results_dir else None
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
                        if results_dir and results_dir.exists():
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
                headers = restored.get("headers", [])
                data_rows = restored.get("data_rows", [])
                source_text = restored.get("source_text", "")
                sentence_translation = restored.get("sentence_translation", "")
                sess_lang = restored.get("source_language") or restored.get("language") or restored.get("lang") or "de"
                if restored.get("session") and isinstance(restored["session"], dict):
                    text_mode = restored["session"].get("text_mode", "single")
                    if not sess_lang:
                        sess_lang = restored["session"].get("source_language") or restored["session"].get("language") or "de"

            # If sentence_translation not found directly, try extracting from TSV columns or SQLite sentences
            role_fields = get_role_fields(mapping, headers) if mapping else {}
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
                        sentence_translation = "\n".join(sent_trans_list)

            sentences_list = []
            if hasattr(storage_adapter, 'backend_name') and storage_adapter.backend_name == 'sqlite' and hasattr(storage_adapter, 'db'):
                try:
                    db_sents = storage_adapter.db.get_sentences_by_session(zid)
                    if db_sents:
                        for s in sorted(db_sents, key=lambda x: x.get("sentence_index", 1)):
                            s_dest = s.get("sentence_destination")
                            s_src = s.get("sentence_source")
                            sentences_list.append({
                                "sentence_index": s.get("sentence_index", 1),
                                "sentence_source": str(s_src).strip() if s_src else "",
                                "sentence_destination": str(s_dest).strip() if s_dest else "",
                            })
                        if not sentence_translation:
                            clean_translations = [s["sentence_destination"] for s in sentences_list if s["sentence_destination"]]
                            if clean_translations:
                                sentence_translation = "\n".join(clean_translations)
                except Exception:
                    pass

            # Semantic completion check: if untranslated lemmas remain and session is recent (<= 300s) or locked
            is_recent = False
            try:
                if len(zid) >= 14 and zid[:14].isdigit():
                    yr, mo, dy = int(zid[0:4]), int(zid[4:6]), int(zid[6:8])
                    hr, mn, sc = int(zid[8:10]), int(zid[10:12]), int(zid[12:14])
                    dt = datetime(yr, mo, dy, hr, mn, min(sc, 59)) + timedelta(seconds=max(0, sc - 59))
                    now = datetime.now()
                    age_sec = (now - dt).total_seconds()
                    if abs(age_sec) <= 300:
                        is_recent = True
            except Exception:
                pass

            has_untranslated_lemmas = False
            if data_rows and headers:
                col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else (headers.index('WordSource') if 'WordSource' in headers else -1)
                col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else (headers.index('WordDestination') if 'WordDestination' in headers else -1)
                if col_lemma != -1 and col_word_dest != -1:
                    for r in data_rows:
                        if len(r) > col_lemma and r[col_lemma].strip():
                            dest_val = r[col_word_dest].strip() if len(r) > col_word_dest else ""
                            if not dest_val or "skeleton-loader" in dest_val:
                                has_untranslated_lemmas = True
                                break

            session_is_busy = is_busy or (has_untranslated_lemmas and is_recent)

            if data_rows and headers:
                data_rows = sort_rows_by_frequency(
                    data_rows, headers, sess_lang, self.server.config, self.server.resolved_paths, role_fields=role_fields
                )

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

            rows_dict = format_update_rows_dict(data_rows, headers, role_fields, row_provenances=fallback_row_provs)
            translated_html = format_translated_html(
                sentence_translation,
                text_mode=text_mode,
                text=source_text,
                config=self.server.config
            ) if sentence_translation else ""
            
            eff_text_prov = None
            if is_sqlite:
                try:
                    db_sents = storage_adapter.db.get_sentences_by_session(zid)
                    if db_sents:
                        eff_text_prov = next((s.get("text_provenance") for s in db_sents if s.get("text_provenance")), None)
                except Exception:
                    pass

            self._send_json(200, {
                "ok": True,
                "zid": zid,
                "session_zid": zid,
                "is_finished": not session_is_busy,
                "stage": "translating" if session_is_busy else "finished",
                "status": {
                    "is_finished": not session_is_busy,
                    "stage": "translating" if session_is_busy else "finished"
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

            if not language and not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'language' or 'session_zid'")

            html_result = ""
            child_args = []
            active_zid = session_zid or generate_unique_zid()

            if session_zid:
                adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
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
                        config=self.server.config,
                        resolved_paths=self.server.resolved_paths,
                        zoom_level=str(zoom),
                        theme=theme,
                        tsv_path=resolved_tsv,
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

                        render_out = render_flow_fn(
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

                render_out = render_flow_fn(
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

            b64_html = encode(html_result)
            self._send_json(200, {
                "ok": True,
                "zid": active_zid,
                "html_b64": b64_html,
                "html": html_result,
                "children": child_args or [],
            })
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
        if path in ('/', '/lookup', '/session/render'):
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
                        config=self.server.config,
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
                    sentence_translation = "\n".join([
                        str(s.get("sentence_destination") or s.get("sentence_destination2") or s.get("sentence_source") or "").strip()
                        for s in synthesized["sentences"]
                    ])
                fingerprint = compute_content_fingerprint(data_rows)
                slug = synthesized.get("slug") or ""
                req_theme = qs.get('theme', [None])[0]
                view_mode = qs.get('view', [None])[0]
                synth_zid = synthesized.get("session_zid") or req_zid
                if view_mode == 'goldendict':
                    goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                    goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                    goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
                    goldendict['theme'] = req_theme or 'dark'
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
                        config=self.server.config,
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
                else:
                    adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
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
                        config=self.server.config,
                        resolved_paths=self.server.resolved_paths,
                        theme=req_theme or 'dark',
                        tsv_path=tsv_path,
                    )
                body = html.encode('utf-8')
                self.send_response(200)
                self._send_cors_headers()
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if session_zid:
                adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                except Exception as e:
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
                            config=self.server.config,
                            resolved_paths=self.server.resolved_paths,
                            theme=req_theme,
                            seq_num=int(seq_num) if seq_num else None,
                            mismatch_info=draft.get("mismatch_info"),
                        )
                        body = html.encode('utf-8')
                        self.send_response(200)
                        self._send_cors_headers()
                        self.send_header('Content-Type', 'text/html; charset=utf-8')
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{session_zid}' not found: {e}")

                source_text = restored.get("source_text", "")
                sess_lang = restored.get("source_language") or language or "de"
                target_lang = restored.get("target_language") or qs.get('target-lang', ['ru'])[0]
                data_rows = restored.get("data_rows", [])
                headers = restored.get("headers", [])
                comments = restored.get("comments", [])
                sentence_translation = restored.get("sentence_translation", "")
                if not sentence_translation and restored.get("sentences"):
                    sentence_translation = "\n".join([
                        str(s.get("sentence_destination") or s.get("sentence_destination2") or "").strip()
                        for s in restored["sentences"]
                        if (s.get("sentence_destination") or s.get("sentence_destination2"))
                    ])

                mapping_path = None
                if self.server.resolved_paths and "anki_mapping_file" in self.server.resolved_paths:
                    mapping_path = Path(self.server.resolved_paths["anki_mapping_file"])
                elif self.server.config and hasattr(self.server.config, "get"):
                    raw_mp = self.server.config.get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
                    mapping_path = Path(raw_mp)
                mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None
                role_fields = get_role_fields(mapping, headers) if mapping else {}

                # Incremental wordfill hydration for missing lemma fields
                wordfill_cfg = getattr(self.server, 'wordfill', None)
                if wordfill_cfg is None and self.server.config is not None:
                    wordfill_cfg = resolve_wordfill_config(self.server.config, self.server.resolved_paths)

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
                    except Exception as save_err:
                        logger.warning(f"Could not persist hydrated session to storage: {save_err}")

                if data_rows and headers:
                    data_rows = sort_rows_by_frequency(
                        data_rows, headers, sess_lang, self.server.config, self.server.resolved_paths, role_fields=role_fields
                    )

                fingerprint = compute_content_fingerprint(data_rows)

                req_theme = qs.get('theme', [None])[0]
                view_mode = qs.get('view', [None])[0]
                slug = restored.get("slug") or restored.get("session", {}).get("slug", "") or (generate_slug(source_text) if source_text else "")
                text_mode = restored.get("text_mode") or "single"

                if view_mode == 'goldendict':
                    goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                    goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                    goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
                    goldendict['theme'] = req_theme or 'dark'
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
                        config=self.server.config,
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
                else:
                    results_dir = Path(self.server.resolved_paths.get('kardenwort_workspace', '.')) / "results"
                    slug_suffix = f"-{slug}" if slug else ""
                    tsv_path = results_dir / f"{session_zid}{slug_suffix}.{sess_lang}.tsv"
                    seq_num = qs.get('seq_num', [None])[0] or qs.get('seq-num', [None])[0]

                    delivery_mode = "container"
                    if hasattr(self.server, 'config') and self.server.config and self.server.config.has_section("sentences_mode"):
                        raw_delivery = self.server.config.get("sentences_mode", "delivery_mode", fallback=None)
                        if raw_delivery is not None:
                            del_val = raw_delivery.strip().lower()
                            delivery_mode = del_val if del_val in ("container", "multi_window") else "container"
                        else:
                            raw_wtm = self.server.config.get("sentences_mode", "web_tab_mode", fallback="container").strip().lower()
                            delivery_mode = "multi_window" if raw_wtm == "tabs" else "container"

                    if delivery_mode == "container" and (seq_num is None or str(seq_num).strip() in ("", "1")):
                        resolved_seq_num = 2
                    else:
                        try:
                            resolved_seq_num = int(seq_num) if seq_num else None
                        except (ValueError, TypeError):
                            resolved_seq_num = None

                    html = render_flow_fn(
                        text=source_text,
                        language=sess_lang,
                        zid=session_zid,
                        text_mode=text_mode,
                        config=self.server.config,
                        resolved_paths=self.server.resolved_paths,
                        theme=req_theme or 'dark',
                        tsv_path=tsv_path,
                        seq_num=resolved_seq_num,
                    )
                body = html.encode('utf-8')
                self.send_response(200)
                self._send_cors_headers()
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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

            if path == '/':
                tok = qs.get('token', [None])[0]
                target = f"/admin?token={urllib.parse.quote(tok)}" if tok else "/admin"
                self.send_response(302)
                self._send_cors_headers()
                self.send_header('Location', target)
                self.end_headers()
                return

            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' or 'session_zid' or 'project_id' query parameter")
            if not language:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'language' query parameter")

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
                        config=self.server.config,
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
                    sentence_translation = "\n".join([
                        str(s.get("sentence_destination") or s.get("sentence_destination2") or s.get("sentence_source") or "").strip()
                        for s in synthesized["sentences"]
                    ])
                fingerprint = compute_content_fingerprint(data_rows)
                synth_zid = synthesized.get("session_zid") or f"project_{project_id}"

                goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
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
                    config=self.server.config,
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
                adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                except Exception as e:
                    raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{session_zid}' not found: {e}")

                source_text = restored.get("source_text", "")
                sess_lang = restored.get("source_language") or language or "de"
                target_lang = restored.get("target_language") or qs.get('target-lang', ['ru'])[0]
                data_rows = restored.get("data_rows", [])
                headers = restored.get("headers", [])
                comments = restored.get("comments", [])
                sentence_translation = restored.get("sentence_translation", "")

                mapping_path = None
                if self.server.resolved_paths and "anki_mapping_file" in self.server.resolved_paths:
                    mapping_path = Path(self.server.resolved_paths["anki_mapping_file"])
                elif self.server.config and hasattr(self.server.config, "get"):
                    raw_mp = self.server.config.get(SEC_SETTINGS, "anki_mapping_file", fallback="./anki-mapping.ini")
                    mapping_path = Path(raw_mp)
                mapping = load_anki_mapping(mapping_path) if mapping_path and mapping_path.exists() else None
                role_fields = get_role_fields(mapping, headers) if mapping else {}

                if data_rows and headers:
                    data_rows = sort_rows_by_frequency(
                        data_rows, headers, sess_lang, self.server.config, self.server.resolved_paths, role_fields=role_fields
                    )

                fingerprint = compute_content_fingerprint(data_rows)

                goldendict = dict(self.server.goldendict) if self.server.goldendict else {}
                goldendict.setdefault('sections', ['source', 'translation', 'lemmas'])
                goldendict.setdefault('lemma_columns', ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
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
                    config=self.server.config,
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
                config=self.server.config,
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

        if path in ('/api/v1/export', '/session/export'):
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            for req_field in ('session_zid', 'selected_row_ids'):
                if req_field not in body:
                    raise StructuredError(ErrorCode.MISSING_FIELD, f"Missing required payload field: '{req_field}'")

            req_zid = generate_server_zid(self.server)
            res = core_export(
                tsv_path_or_session=body["session_zid"],
                selected_row_ids=body["selected_row_ids"],
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                fingerprint=body.get("fingerprint"),
                zid=req_zid,
                language=body.get("language"),
            )
            self._send_json(200, res)
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
                persist_default_language(target_lang, base_dir=base_dir)

                # 3. Notify AutoHotkey process via IPC
                spawn_ahk(["--set-language", target_lang], base_dir=base_dir)
            else:
                target_lang = draft.get("language") or "en"

            # Execute run_render_flow
            render_out = render_flow_fn(
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
            delivery_mode = "container"
            if hasattr(self.server, 'config') and self.server.config and self.server.config.has_section("sentences_mode"):
                parent_mode = self.server.config.get("sentences_mode", "parent_mode", fallback="full").lower()
                raw_delivery = self.server.config.get("sentences_mode", "delivery_mode", fallback=None)
                if raw_delivery is not None:
                    del_val = raw_delivery.strip().lower()
                    delivery_mode = del_val if del_val in ("container", "multi_window") else "container"
                else:
                    raw_wtm = self.server.config.get("sentences_mode", "web_tab_mode", fallback="container").strip().lower()
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
                "language": target_lang,
                "session_zid": session_zid,
                "urls": spawned_urls,
            })
            return

        # Browser tab spawning endpoint (bypasses browser popup blocking)
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

        # Audio playback endpoint (Non-blocking background TTS synthesis for web sessions)
        if path in ('/api/v1/audio/play', '/session/play'):
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)

            text = body.get('text') or qs.get('text', [None])[0]
            language = body.get('language') or body.get('lang') or qs.get('language', [None])[0] or qs.get('lang', [None])[0]
            if not text:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'text' in payload")
            if not language:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'language' in payload")

            anki_tts_cli = None
            if hasattr(self.server, 'resolved_paths') and self.server.resolved_paths:
                anki_tts_cli = self.server.resolved_paths.get('anki_tts_cli')

            if not anki_tts_cli or not Path(anki_tts_cli).exists():
                desk_dir = Path(__file__).resolve().parent
                workspace_parent = desk_dir.parent
                candidates = list(workspace_parent.glob("*-anki-tts-cli/anki-tts-cli.py"))
                if candidates:
                    anki_tts_cli = candidates[0]

            if not anki_tts_cli or not Path(anki_tts_cli).exists():
                raise StructuredError(ErrorCode.CONFIGURATION_ERROR, "anki_tts_cli script path is not configured or does not exist")

            python_exe = None
            if hasattr(self.server, 'resolved_paths') and self.server.resolved_paths:
                python_exe = self.server.resolved_paths.get('kardenwort_python')
            if not python_exe and hasattr(self.server, 'config') and self.server.config:
                try:
                    python_exe = self.server.config.get(SEC_ENVIRONMENT, 'kardenwort_python', fallback=sys.executable)
                except Exception:
                    python_exe = sys.executable
            if not python_exe:
                python_exe = sys.executable

            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000  # CREATE_NO_WINDOW

            cmd = [str(python_exe), str(anki_tts_cli), str(text), str(language)]
            try:
                subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception as e:
                logger.warning(f"Failed to spawn audio player: {e}")
                raise StructuredError(ErrorCode.SERVER_ERROR, f"Failed to spawn audio player: {e}")

            self._send_json(200, {
                "ok": True,
                "status": "playing",
                "text": text,
                "language": language
            })
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
                # Fallback to 1.ico if not found
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

        # 6. Admin Project Tree REST API Endpoints
        if path == '/api/v1/admin/projects':
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            if method == 'GET':
                self._authenticate_token(query_params=qs)
                tree = db.get_project_tree()
                self._send_json(200, {"ok": True, "projects": tree})
                return
            elif method == 'POST':
                body = self._read_json_body()
                self._authenticate_token(body)
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
            self._authenticate_token(body)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            updates = {k: v for k, v in body.items() if k in ('title', 'slug', 'description', 'parent_id', 'order_index')}
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            ok = db.update_project(int(project_id), updates)
            self._send_json(200, {"ok": ok, "project_id": project_id})
            return

        if path == '/api/v1/admin/projects/delete':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            ok = db.soft_delete_project(int(project_id))
            self._send_json(200, {"ok": ok, "project_id": project_id})
            return

        if path == '/api/v1/admin/projects/link':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)
            project_id = body.get('project_id')
            session_zid = body.get('session_zid')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            ok = db.link_session_to_project(int(project_id), str(session_zid))
            self._send_json(200, {"ok": ok, "project_id": project_id, "session_zid": session_zid})
            return

        if path == '/api/v1/admin/projects/unlink':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)
            project_id = body.get('project_id')
            session_zid = body.get('session_zid')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            ok = db.unlink_session_from_project(int(project_id), str(session_zid))
            self._send_json(200, {"ok": ok, "project_id": project_id, "session_zid": session_zid})
            return

        if path == '/api/v1/admin/projects/reorder':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)
            project_id = body.get('project_id')
            session_zids = body.get('session_zids', [])
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            if not isinstance(session_zids, list):
                raise StructuredError(ErrorCode.INVALID_PAYLOAD, "'session_zids' must be a list")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            ok = db.reorder_project_sessions(int(project_id), session_zids)
            self._send_json(200, {"ok": ok, "project_id": project_id, "session_zids": session_zids})
            return

        if path == '/api/v1/admin/projects/export-deck':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)
            project_id = body.get('project_id')
            if project_id is None:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'project_id' in payload")
            lang = body.get('language')
            req_zid = generate_server_zid(self.server)
            res = aggregate_project_materials(
                project_id=int(project_id),
                config=self.server.config,
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
                self._authenticate_token(body)
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
                config=self.server.config,
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
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            self._authenticate_token(body)
            session_zid = body.get('session_zid') or body.get('zid')
            if not session_zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'session_zid' in payload")
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
            ok = db.soft_delete_session(str(session_zid))
            self._send_json(200, {"ok": ok, "session_zid": session_zid})
            return

        if path == '/api/v1/admin/sessions/batch-delete':
            if method != 'POST':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            body = self._read_json_body()
            self._authenticate_token(body)
            mode = body.get('mode', 'explicit')
            session_zids = body.get('session_zids') or body.get('zids') or []
            filter_params = body.get('filter') or {}
            excluded_zids = body.get('excluded_zids') or []

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)

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
            adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
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
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
                self._authenticate_token(body)
                tsv_content = body.get('tsv_content') or body.get('content') or body.get('tsv')
                filename = body.get('filename') or filename
                session_zid = body.get('session_zid') or body.get('zid') or session_zid
                lang = body.get('language') or lang
                slug = body.get('slug') or slug
            else:
                self._authenticate_token(query_params=qs)
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
                config=self.server.config,
                resolved_paths=self.server.resolved_paths,
                zid=req_zid,
            )

            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            self._authenticate_token(body)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)

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
            self._authenticate_token(body)
            older_than_days = body.get('older_than_days')
            if older_than_days is not None:
                try:
                    older_than_days = float(older_than_days)
                except ValueError:
                    raise StructuredError(ErrorCode.INVALID_PAYLOAD, "'older_than_days' must be a numeric value")

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            self._authenticate_token(body)
            req_zid = generate_server_zid(self.server)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            self._authenticate_token(body)
            req_zid = generate_server_zid(self.server)

            def run_vacuum_worker(server_config, resolved_paths, req_zid):
                try:
                    from kardenwort_db import KardenwortDB
                    db = KardenwortDB(config=server_config, resolved_paths=resolved_paths)
                    db.vacuum(zid=req_zid)
                except Exception as e:
                    logger.error(f"Background vacuum worker failed (ZID: {req_zid}): {e}")

            threading.Thread(
                target=run_vacuum_worker,
                args=(self.server.config, self.server.resolved_paths, req_zid),
                daemon=True
            ).start()

            self._send_json(200, {
                "ok": True,
                "status": "dispatched",
                "message": "Database VACUUM and PRAGMA optimize running asynchronously in background worker",
                "zid": req_zid
            })
            return

        if path == '/api/v1/admin/telemetry':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            self._authenticate_token(query_params=qs)
            req_zid = generate_server_zid(self.server)
            from kardenwort_db import KardenwortDB
            db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
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
            if getattr(self.server, 'api_key', ''):
                self._authenticate_token(body)

            req_zid = generate_server_zid(self.server)
            self._send_json(200, {"zid": req_zid, "message": "Shutting down controller and sidecars."})

            def shutdown_server():
                time.sleep(0.1)
                try:
                    if hasattr(self.server, 'supervisor'):
                        self.server.supervisor.stop()
                    self.server.shutdown()
                    self.server.server_close()
                except Exception as e:
                    logger.error(f"Error during controller shutdown: {e}")

            threading.Thread(target=shutdown_server, daemon=True).start()
            return

        raise StructuredError(ErrorCode.NOT_FOUND, f"Unknown API endpoint: {path}")


# ---------------------------------------------------------------------------
# Controller Entrypoint & Subcommand
# ---------------------------------------------------------------------------
def run_controller(args=None):
    config_path = getattr(args, 'config', None) if args else None
    config, resolved_paths, goldendict, _wordfill = load_config(config_path)

    enabled = goldendict.get('server_enabled', True)
    if not enabled:
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, "Controller is disabled in config.ini ([server] enabled = false)")

    host = getattr(args, 'host', None) if args else None
    if not host:
        host = goldendict.get('server_host', '127.0.0.1')

    port = getattr(args, 'port', None) if args else None
    if not port:
        port = config.getint('server', 'controller_port', fallback=config.getint('server', 'port', fallback=goldendict.get('server_port', 18335)))

    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, f"Controller host must be loopback (127.0.0.1). Specified: {host}")

    ThreadingHTTPServer.request_queue_size = 64
    server = ThreadingHTTPServer((host, port), ControllerRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.disable_nagle_algorithm = True

    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = goldendict
    server.wordfill_cfg = _wordfill
    server.api_key = goldendict.get('server_api_key', '')
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()
    server.server_port = port

    # Initialize in-memory SessionArbiter
    server.arbiter = SessionArbiter(config, resolved_paths, wordfill_cfg=_wordfill)
    server.arbiter.goldendict = goldendict

    # Initialize & start sidecar supervisor
    enable_supervisor = not (getattr(args, 'no_sidecars', False) if args else False)
    server.supervisor = ProcessSupervisor(config, resolved_paths, enabled=enable_supervisor)
    if enable_supervisor:
        server.supervisor.start()

    startup_zid = generate_unique_zid()
    logger.info(f"Kardenwort Controller Daemon started on {host}:{port} (ZID: {startup_zid})")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Controller stopped by KeyboardInterrupt.")
    finally:
        try:
            if hasattr(server, 'supervisor'):
                server.supervisor.stop()
            server.server_close()
        except Exception:
            pass


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
