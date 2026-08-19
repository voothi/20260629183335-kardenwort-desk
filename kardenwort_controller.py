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
    SessionLogger,
    SEC_SETTINGS,
    SEC_LANGUAGES,
    SEC_PIPELINE,
    SEC_TRIGGERS,
    SEC_SERVICES,
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

    def __init__(self, config: Any, resolved_paths: Dict[str, Any]):
        self.config = config
        self.resolved_paths = resolved_paths
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

        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        tsv_path = find_working_tsv(results_dir, session_zid, lang)
        if not tsv_path or not tsv_path.exists():
            raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found for session {session_zid}")

        source_txt = tsv_path.with_suffix('.txt')
        if not source_txt.exists():
            raise StructuredError(ErrorCode.DESK_FAILED, f"Source text file missing for session {session_zid}")

        text = source_txt.read_text(encoding='utf-8')
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

        self.emit_event(session_zid, {
            "type": "update",
            "stage": "translated_text",
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

        results_dir = resolve_results_dir(self.resolved_paths, self.config)
        tsv_path = find_working_tsv(results_dir, session_zid, lang)
        if not tsv_path or not tsv_path.exists():
            raise StructuredError(ErrorCode.DESK_FAILED, f"Working TSV file not found for session {session_zid}")

        run_headless_intellifiller(tsv_path, prompt_name, self.config, self.resolved_paths, selected_rows=selected_rows, reprocess=True, zid=req_zid)

        comments, headers, data_rows = load_tsv_rows(tsv_path)
        new_fp = compute_content_fingerprint(data_rows)

        with self._lock:
            if session_zid in self.sessions:
                self.sessions[session_zid]["data_rows"] = data_rows
                self.sessions[session_zid]["fingerprint"] = new_fp

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

    def _authenticate_token(self, body_data=None):
        api_key = getattr(self.server, 'api_key', '')
        if not api_key:
            return

        provided_token = self.headers.get('X-API-Token')
        if not provided_token and body_data and isinstance(body_data, dict):
            provided_token = body_data.get('token')

        import hmac
        if not provided_token or not hmac.compare_digest(str(provided_token).strip(), str(api_key).strip()):
            raise StructuredError(ErrorCode.UNAUTHORIZED, "Invalid or missing API authentication token")

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

        # 1. Health Probe (Controller + Supervisor Sidecars)
        if path in ('/health', '/api/v1/health'):
            if method != 'GET':
                raise StructuredError(ErrorCode.METHOD_NOT_ALLOWED, f"Method {method} not allowed for {path}")
            uptime = round(time.time() - getattr(self.server, 'start_time', time.time()), 2)
            supervisor_report = self.server.supervisor.get_status_report() if hasattr(self.server, 'supervisor') else {}
            self._send_json(200, {
                "ok": True,
                "controller": {
                    "port": self.server.server_port,
                    "uptime_seconds": uptime
                },
                "sidecars": supervisor_report
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

        # 4. Consolidated Legacy Endpoints (from http_server.py)
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
            deltas = [{"row_id": int(body["row_id"]), "column": "DeskSelected", "value": "1" if body["status"] else ""}]
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

        # 5. Shutdown Endpoint
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
    server.api_key = goldendict.get('server_api_key', '')
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    server.start_time = time.time()
    server.server_port = port

    # Initialize in-memory SessionArbiter
    server.arbiter = SessionArbiter(config, resolved_paths)
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
