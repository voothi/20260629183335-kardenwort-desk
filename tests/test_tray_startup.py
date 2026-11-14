"""Real native startup, HTTP/SQLite preferences, menu and GDI icon projection."""
from contextlib import contextmanager
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import textwrap
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler

import pytest

import kardenwort_desk as desk
from kardenwort_state_runtime import preference_store
from tests.browser_test_server import create_browser_test_server
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_sqlite_state_native import NATIVE, ahk_function


@contextmanager
def startup_gate(target, ready, config_reply=None, shutdown=None, release=None):
    """External transport readiness only; accepted preferences come from real SQLite."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/release' and release is not None:
                release.set()
                self.send_response(200)
                self.end_headers()
                return
            override = config_reply() if config_reply and self.path == '/api/v1/config' else None
            if override is not None:
                status, body = override
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

                return
            if not ready():
                self.send_response(503)
                self.end_headers()
                return
            request = Request(target+self.path, headers={'X-API-Token': self.headers.get('X-API-Token', '')})
            try:
                response = urlopen(request, timeout=5)
            except Exception as error:
                if not hasattr(error, 'code'):
                    raise
                response = error
            with response:
                body = response.read()
                self.send_response(response.status)
                self.end_headers()
                self.wfile.write(body)

        def do_POST(self):
            if self.path == '/api/v1/shutdown' and shutdown is not None:
                shutdown.set()
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(405)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = create_browser_test_server(Handler)
    server.start()
    try:
        yield server
    finally:
        server.server_close()


FUNCTIONS = [
    'InitializeTrayMenu', 'StartServerProcess', 'EnsureControllerProcess',
    'StopStartupReadiness', 'RetryStartupPreferences', 'CompleteStartupInitialization', 'UpdateTrayMenu', 'UpdateTrayIcon',
    'CreateIconFromText', 'LoadCommittedPreferences', 'CommittedPrimitive',
    'ProjectCommittedTheme', 'SendControllerRequest', 'CheckServerHealth',
    'RestartServerProcess', 'LoadServerConfig', 'ShutdownServerHttp',
    'SetLanguage', 'JSON_Stringify',
]


def native_script(tmp_path, port, body, *, deadline=8000, token='test-controller-api-key'):
    tmp_path = tmp_path/'native'
    tmp_path.mkdir(exist_ok=True)
    source = NATIVE.read_text(encoding='utf-8-sig')
    functions = '\n'.join(ahk_function(source, name) for name in FUNCTIONS if '\n'+name+'(' in source)
    startup = NATIVE.parents[1]/'Lib/DeskStartup.ahk'
    definitions = startup.read_text(encoding='utf-8-sig') if startup.exists() else ''
    marker = tmp_path/'launched.txt'
    launcher = tmp_path/'owned-controller.py'
    launcher.write_text('import os,threading\nfrom pathlib import Path\n'
                        'os.close(1)\nos.close(2)\n'
                        f'with Path({str(marker)!r}).open("a") as stream: stream.write(str(os.getpid())+"\\n")\nthreading.Event().wait()\n')
    (tmp_path/'config.ini').write_text('[Languages]\nde=German\nfr=French,Fr,0x008000,0xFFFFFF\n'
        f'[server]\nenabled=true\nhost=127.0.0.1\nport={port}\ncontroller_port={port}\napi_key={token}\n')
    header = f'''#Requires AutoHotkey v2.0
#NoTrayIcon
G_CurrentLang := ""
G_DefaultLanguage := ""
G_CurrentHIcon := 0
langCodes := []
langNames := Map()
langInfo := Map()
G_ServerEnabled := true
G_ServerPID := 0
G_ServerHost := "127.0.0.1"
G_ServerPort := {port}
G_ControllerPort := {port}
G_ServerApiKey := "{token}"
G_ServerStatus := "Offline"
G_DeskPythonPath := "{sys.executable}"
G_DeskScriptPath := "{launcher}"
G_Startup := 0
G_StartupTimeoutMs := {deadline}
G_StartupPollMs := 30
G_Initialized := false
G_BufferedArgs := [["queued-request"]]
G_HandledArgs := []
G_Theme := "dark"
G_DefaultZoom := 100
G_AutoUpdate := 0
G_AutoSave := 0
G_AutoSaveOnClose := 0
G_GuiBgColor := ""
G_GuiTextColor := ""
G_DwmDark := 1
OnExit(Cleanup)
SetTimer(Watchdog, -15000)
try {{
{body}
}} catch as err {{
    Fail(err.Message)
}}
'''
    helpers = r'''
Cleanup(*) {
    global G_ServerPID, G_Startup
    if IsObject(G_Startup)
        G_Startup.Stop()
    if G_ServerPID {
        try ProcessClose(G_ServerPID)
    }
}
Fail(message) {
    FileAppend(message, "*")
    ExitApp(1)
}
Watchdog() {
    global G_CurrentLang, G_ServerStatus, G_Startup
    phase := IsObject(G_Startup) ? G_Startup.phase : "legacy"
    Fail("Owned native startup watchdog expired; language=" G_CurrentLang "; status=" G_ServerStatus "; phase=" phase)
}
MenuLanguage() {
    textBuffer := Buffer(512)
    DllCall("GetMenuStringW", "Ptr", A_TrayMenu.Handle, "UInt", 0, "Ptr", textBuffer, "Int", 256, "UInt", 0x400)
    return StrGet(textBuffer)
}
IconPixels(icon) {
    dc := DllCall("GetDC", "Ptr", 0, "Ptr")
    memory := DllCall("CreateCompatibleDC", "Ptr", dc, "Ptr")
    bitmap := DllCall("CreateCompatibleBitmap", "Ptr", dc, "Int", 16, "Int", 16, "Ptr")
    old := DllCall("SelectObject", "Ptr", memory, "Ptr", bitmap, "Ptr")
    DllCall("DrawIconEx", "Ptr", memory, "Int", 0, "Int", 0, "Ptr", icon, "Int", 16, "Int", 16, "UInt", 0, "Ptr", 0, "UInt", 3)
    pixels := ""
    loop 16 {
        y := A_Index-1
        loop 16
            pixels .= DllCall("GetPixel", "Ptr", memory, "Int", A_Index-1, "Int", y, "UInt") ","
    }
    DllCall("SelectObject", "Ptr", memory, "Ptr", old)
    DllCall("DeleteObject", "Ptr", bitmap)
    DllCall("DeleteDC", "Ptr", memory)
    DllCall("ReleaseDC", "Ptr", 0, "Ptr", dc)
    return pixels
}
AssertCommitted(language := "de") {
    global G_CurrentLang, G_CurrentHIcon
    if G_CurrentLang != language || MenuLanguage() != "Current Language: " language
        Fail("Committed menu language is missing: " MenuLanguage())
    icons := Map("de", {text: "De", bg: 0x8B0000, fg: 0xFFFFFF}, "fr", {text: "Fr", bg: 0x008000, fg: 0xFFFFFF})
    info := icons[language]
    expected := CreateIconFromText(info.text, info.bg, info.fg)
    matches := IconPixels(expected) == IconPixels(G_CurrentHIcon)
    DllCall("DestroyIcon", "Ptr", expected)
    if !matches
        Fail("Tray icon pixels do not match the committed language label/color")
}
OpenAdminPanel(*) {
}
OpenServerLogsDir(*) {
}
ProcessArgs(args) {
    global G_CurrentLang, G_HandledArgs, G_Startup
    if G_CurrentLang == ""
        Fail("Startup request used an empty language")
    G_HandledArgs.Push(args)
}
KardenMsgBox(message, *) {
    Fail(message)
}
'''
    script = tmp_path/'startup.ahk'
    script.write_text(header+helpers+functions+'\n'+definitions, encoding='utf-8')
    return script, marker


def execute(script):
    result = subprocess.run([desk.get_ahk_executable(), '/ErrorStdOut', str(script)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.parametrize('readiness', ['warm', 'cold', 'late', 'controller-port'])
def test_startup_projects_committed_menu_and_real_icon(preference_world, tmp_path, readiness):
    url, controller, config = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    before = store.snapshot(), config.read_bytes(), config.stat().st_mtime_ns
    started = time.monotonic()
    marker = tmp_path/'native/launched.txt'
    def ready():
        if readiness in ('warm', 'controller-port'):
            return True
        return marker.exists() and (readiness != 'late' or time.monotonic()-started > 3.2)
    with startup_gate(url, ready) as gate:
        script, marker = native_script(tmp_path, gate.server_port,
            ('G_ServerPort := 1\n' if readiness == 'controller-port' else '')+'''
InitializeTrayMenu()
StartServerProcess()
SetTimer(CheckReady, 20)
CheckReady() {
    global G_CurrentLang, G_HandledArgs
    if G_CurrentLang != "de"
        return
    if IsObject(G_Startup) && G_HandledArgs.Length != 1
        return
    AssertCommitted()
    FileAppend("verified", "*")
    ExitApp(0)
}
''')
        execute(script)
    assert marker.exists() is (readiness not in ('warm', 'controller-port')), 'Warm startup must not launch a duplicate controller'
    if marker.exists():
        assert len(marker.read_text().splitlines()) == 1, 'Startup launched duplicate controllers'
    assert (store.snapshot(), config.read_bytes(), config.stat().st_mtime_ns) == before


@pytest.mark.parametrize('failure', ['offline', 'auth', 'malformed', 'storage'])
@pytest.mark.parametrize('retained', [False, True])
def test_startup_failure_and_explicit_retry(preference_world, tmp_path, failure, retained):
    url, controller, config = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    failure_flag = tmp_path/'native/failure.txt'
    if not retained:
        failure_flag.parent.mkdir()
        failure_flag.touch()
    corrupt = False

    def config_reply():
        nonlocal corrupt
        failing = failure_flag.exists()
        if failure == 'storage' and failing != corrupt:
            if failing:
                with store.db.get_connection() as connection:
                    connection.execute("UPDATE preferences SET value='\"und\"' WHERE key='default_language'")
            else:
                store.set('default_language', 'de')
            corrupt = failing
        if not failing:
            return None
        return {'auth': (403, b'{}'), 'malformed': (200, b'{"invalid":true}')}.get(failure)

    stage = 'initial' if retained else 'failure'
    expected = 'de' if retained else ''
    with startup_gate(url, lambda: failure != 'offline' or not failure_flag.exists(), config_reply) as gate:
        script, _ = native_script(tmp_path, gate.server_port, rf'''
stage := "{stage}"
InitializeTrayMenu()
StartServerProcess()
SetTimer(CheckFailureAndRetry, 20)
CheckFailureAndRetry() {{
    global G_Startup, G_CurrentLang, G_ServerStatus, G_Initialized, stage
    if stage == "initial" && G_Startup.phase == "ready" {{
        AssertCommitted()
        FileAppend("fail", A_ScriptDir "\failure.txt")
        stage := "failure"
        RetryStartupPreferences()
        return
    }}
    if stage == "failure" && G_Startup.phase == "failed" {{
        if G_CurrentLang != "{expected}" || G_ServerStatus == "Running"
            Fail("Failed readiness projected a fabricated or lost language")
        if G_CurrentLang == "de"
            AssertCommitted()
        else if MenuLanguage() != "Current Language: Unavailable"
            Fail("Unavailable startup was not explicit")
        FileDelete(A_ScriptDir "\failure.txt")
        stage := "retry"
        RetryStartupPreferences()
        return
    }}
    if stage == "retry" && G_Startup.phase == "ready" {{
        AssertCommitted()
        ExitApp(0)
    }}
}}
''', deadline=400)
        execute(script)
    assert store.get('default_language') == 'de'
    marker = tmp_path/'native/launched.txt'
    assert not marker.exists() or len(marker.read_text().splitlines()) == 1


def test_restart_rehydrates_without_replaying_startup_arguments(preference_world, tmp_path):
    url, controller, config = preference_world
    preference_store(controller.config, controller.resolved_paths).set('default_language', 'de')
    shutdown = threading.Event()
    marker = tmp_path/'native/launched.txt'
    with startup_gate(url, lambda: not shutdown.is_set() or marker.exists(), shutdown=shutdown) as gate:
        script, _ = native_script(tmp_path, gate.server_port, '''
stage := "initial"
InitializeTrayMenu()
StartServerProcess()
SetTimer(CheckRestart, 20)
CheckRestart() {
    global G_Startup, G_HandledArgs, stage
    if G_Startup.phase != "ready" || G_HandledArgs.Length != 1
        return
    AssertCommitted()
    if stage == "initial" {
        stage := "restart"
        RestartServerProcess()
        return
    }
    ExitApp(0)
}
''')
        execute(script)
    assert shutdown.is_set() and marker.exists()
    assert len(marker.read_text().splitlines()) == 1


@pytest.mark.parametrize('action', ['stop', 'replacement', 'language'])
def test_cancelled_response_cannot_project_after_stop_or_replacement(preference_world, tmp_path, action):
    url, controller, config = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    seen, finished = tmp_path/'seen.txt', tmp_path/'finished.txt'
    release = threading.Event()
    held_once = False

    def held_snapshot():
        nonlocal held_once
        if held_once:
            return None
        held_once = True
        with urlopen(Request(url+'/api/v1/config', headers={'X-API-Token': 'test-controller-api-key'}), timeout=5) as response:
            body = response.read()
        store.set('default_language', 'fr')
        seen.touch()
        assert release.wait(10), 'Native cancellation never released the owned response'
        finished.touch()
        return 200, body

    with startup_gate(url, lambda: True, held_snapshot, release=release) as gate:
        replacement = action != 'stop'
        language = action == 'language'
        body = rf'''
stage := "waiting"
old := 0
InitializeTrayMenu()
StartServerProcess()
SetTimer(CheckCancellation, 20)
CheckCancellation() {{
    global G_Startup, G_ControllerPort, G_CurrentLang, G_HandledArgs, stage, old
    if stage == "waiting" && FileExist("{seen}") {{
        old := G_Startup
        if !{int(language)}
            StopStartupReadiness()
        stage := "cancelled"
        if {int(replacement)} {{
            G_ControllerPort := {controller.server_port}
            if {int(language)} {{
                if !SetLanguage("fr")
                    Fail("Language commit during initialization failed")
            }} else
                RetryStartupPreferences()
        }}
    }}
    if stage == "cancelled" {{
        if {int(replacement)} && (G_Startup.phase != "ready" || G_HandledArgs.Length != 1)
            return
        currentPort := G_ControllerPort
        G_ControllerPort := {gate.server_port}
        reply := ""
        SendControllerRequest("/release", "", &reply, 2, "GET")
        G_ControllerPort := currentPort
        stage := "released"
    }}
    if stage != "released" || !FileExist("{finished}")
        return
    if (!{int(language)} && old.phase != "stopped") || IsObject(old.request)
        Fail("Cancelled request retained ownership")
    if !{int(language)} && (IsObject(old.timer) || IsObject(old.readyTimer))
        Fail("Cancelled request retained timer ownership")
    if {int(replacement)}
        AssertCommitted("fr")
    else if G_CurrentLang != "" || G_HandledArgs.Length
        Fail("Stopped startup projected a stale snapshot or handled a queued request")
    ExitApp(0)
}}
'''
        script, _ = native_script(tmp_path, gate.server_port, body)
        try:
            execute(script)
        finally:
            release.set()


def test_startup_slice_has_bounded_branch_complexity():
    source = (NATIVE.parents[1]/'Lib/DeskStartup.ahk').read_text(encoding='utf-8-sig')
    methods = textwrap.dedent('\n'.join(source.splitlines()[2:-1]))
    counts = {}
    for name in re.findall(r'^([A-Za-z_]+)\([^\n]*\) \{', methods, re.M):
        body = ahk_function('\n'+methods, name)
        code = re.sub(r'"(?:`.|[^"`])*"|;[^\n]*', '', body)
        counts[name] = 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', code))
    assert counts, 'No startup methods were inspected'
    assert max(counts.values()) <= 7, counts

