import sys
import os
import re
import json
import time
import queue
import socket
import logging
import threading
import traceback
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
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
    run_render_flow,
    verify_language,
    synthesize_project_materials,
    aggregate_project_materials,
    resolve_project_deck_path,
    safe_write_update_js,
    format_translated_html,
    SessionLogger,
    find_wordfill_match,
    apply_wordfill_to_rows,
    resolve_wordfill_config,
    sort_rows_by_frequency,
    SEC_SETTINGS,
    SEC_LANGUAGES,
    SEC_PIPELINE,
    SEC_TRIGGERS,
    SEC_SERVICES,
    SEC_TRANSLATION,
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
                "fingerprint": res["fingerprint"],
                "lock": threading.Lock(),
                "created_at": time.time(),
            }

        # Emit initial source stage event
        self.emit_event(session_zid, {
            "type": "stage",
            "stage": "source",
            "status": "success",
            "rows": res["data_rows"],
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

        if not tsv_path or not tsv_path.exists():
            results_dir = resolve_results_dir(self.resolved_paths, self.config)
            storage_adapter = getattr(self, 'storage_adapter', None) or get_storage_adapter(self.config, self.resolved_paths)
            tsv_path = find_working_tsv(results_dir, session_zid, lang, storage_adapter=storage_adapter)

        if not tsv_path or not tsv_path.exists():
            raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found for session {session_zid}")

        source_txt = tsv_path.with_suffix('.txt')
        if source_txt.exists():
            text = source_txt.read_text(encoding='utf-8')
        elif session_text:
            text = session_text
            try:
                source_txt.write_text(text, encoding='utf-8')
            except Exception:
                pass
        else:
            raise StructuredError(ErrorCode.DESK_FAILED, f"Source text file missing for session {session_zid}")
        provider = self.config.get(SEC_PIPELINE, 'text_reprocess_provider', fallback='deepl')

        # Translate in-memory
        sentence_trans = translate_source_text(text, lang, target_lang, text_mode, self.config, self.resolved_paths, provider, zid=req_zid)

        with file_lock(tsv_path):
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            mapping = load_anki_mapping(self.resolved_paths['anki_mapping_file'])
            role_fields = get_role_fields(mapping, headers)
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1

            if col_sentence_dest != -1:
                for row in data_rows:
                    if len(row) > col_sentence_dest and sentence_trans:
                        row[col_sentence_dest] = list(sentence_trans.values())[0] if isinstance(sentence_trans, dict) else str(sentence_trans)
                save_tsv_rows_safely(tsv_path, comments, headers, data_rows)

        new_fp = compute_content_fingerprint(data_rows)
        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["fingerprint"] = new_fp

        translated_html = format_translated_html(sentence_trans, text_mode=text_mode, text=text, config=self.config)
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
            "rows": data_rows,
            "translated_text": translated_html
        })

        return {
            "status": "success",
            "session_zid": session_zid,
            "fingerprint": new_fp,
            "data_rows": data_rows,
            "translated_text": translated_html
        }

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

        if selected_rows:
            if is_sqlite:
                storage_adapter.enrich_session_intellifiller(
                    session_zid=session_zid,
                    prompt_name=prompt_name,
                    selected_rows=selected_rows,
                    reprocess=True,
                    zid=req_zid,
                )
                comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                data_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)
            else:
                run_headless_intellifiller(tsv_path, prompt_name, self.config, self.resolved_paths, selected_rows=selected_rows, reprocess=True, zid=req_zid)
                comments, headers, data_rows = storage_adapter.load_tsv_rows(tsv_path)
                data_rows = sort_rows_by_frequency(data_rows, headers, lang, self.config, self.resolved_paths, role_fields=role_fields)

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

        self.emit_event(session_zid, {
            "type": "update",
            "stage": "enrichment",
            "status": "success",
            "fingerprint": new_fp,
            "rows": data_rows
        })

        return {
            "status": "success",
            "session_zid": session_zid,
            "fingerprint": new_fp,
            "data_rows": data_rows
        }


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
        self.connection.settimeout(5.0)

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

        provided_token = self.headers.get('X-API-Token')
        if not provided_token and body_data and isinstance(body_data, dict):
            provided_token = body_data.get('token')
        if not provided_token and query_params and isinstance(query_params, dict):
            token_list = query_params.get('token', [])
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

        # 1. Health Probe (Controller + Supervisor Sidecars + Database)
        if path in ('/health', '/api/v1/health'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            uptime = round(time.time() - getattr(self.server, 'start_time', time.time()), 2)
            supervisor_report = self.server.supervisor.get_status_report() if hasattr(self.server, 'supervisor') else {}
            
            db_status = {}
            try:
                from kardenwort_db import KardenwortDB
                db = KardenwortDB(config=self.server.config, resolved_paths=self.server.resolved_paths)
                db_status = db.get_status()
            except Exception as e:
                db_status = {"ok": False, "error": str(e)}

            self._send_json(200, {
                "ok": True,
                "controller": {
                    "port": self.server.server_port,
                    "uptime_seconds": uptime
                },
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

        if path == '/session/status':
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            zid = qs.get('zid', [''])[0]
            if not zid:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing 'zid' query parameter")
            with self.server.arbiter._lock:
                sess = self.server.arbiter.sessions.get(zid)
            if not sess:
                raise StructuredError(ErrorCode.NOT_FOUND, f"Session '{zid}' not active in arbiter memory")
            safe_sess = {k: v for k, v in sess.items() if k != "lock"}
            self._send_json(200, safe_sess)
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
            active_zid = session_zid or generate_unique_zid()

            if session_zid:
                adapter = get_storage_adapter(self.server.config, self.server.resolved_paths)
                try:
                    restored = adapter.restore_session(session_zid)
                    source_text = restored.get("source_text", "")
                    sess_lang = restored.get("source_language") or language or "de"
                    t_mode = restored.get("text_mode") or text_mode or "single"
                    slug = restored.get("slug") or ""
                    results_dir = Path(self.server.resolved_paths.get('kardenwort_workspace', '.')) / "results"
                    slug_suffix = f"-{slug}" if slug else ""
                    resolved_tsv = Path(tsv_path) if tsv_path else (results_dir / f"{session_zid}{slug_suffix}.{sess_lang}.tsv")

                    html_result = run_render_flow(
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
                    )
                except Exception:
                    if text and language:
                        if not bypass_lang_check:
                            lang_res = verify_language(text, language, self.server.config, bypass=False)
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

                        html_result = run_render_flow(
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
                        )
                    else:
                        raise
            elif text and language:
                if not bypass_lang_check:
                    lang_res = verify_language(text, language, self.server.config, bypass=False)
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

                html_result = run_render_flow(
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
                )
            else:
                raise StructuredError(ErrorCode.MISSING_FIELD, "Missing required 'text' or 'session_zid'")

            b64_html = encode(html_result)
            self._send_json(200, {
                "ok": True,
                "zid": active_zid,
                "html_b64": b64_html,
                "html": html_result,
            })
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
                    html = run_render_flow(
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
                fingerprint = compute_content_fingerprint(data_rows)

                req_theme = qs.get('theme', [None])[0]
                view_mode = qs.get('view', [None])[0]
                slug = restored.get("slug") or ""
                text_mode = restored.get("text_mode") or "single"

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
                    html = run_render_flow(
                        text=source_text,
                        language=sess_lang,
                        zid=session_zid,
                        text_mode=text_mode,
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
            try:
                target_path.relative_to(assets_dir)
            except ValueError:
                raise StructuredError(ErrorCode.UNAUTHORIZED, "Path traversal forbidden")

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
            slug = sess.get("slug") or restored.get("slug") or ""
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
        port = config.getint('server', 'controller_port', fallback=config.getint('server', 'port', fallback=8080))

    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise StructuredError(ErrorCode.CONFIGURATION_ERROR, f"Controller host must be loopback (127.0.0.1). Specified: {host}")

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
