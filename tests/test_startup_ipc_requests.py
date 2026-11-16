"""Native IPC uses the real startup, receiver and parser; only UI launch is recorded."""
import re
import subprocess
import threading

import pytest

import kardenwort_desk as desk
from kardenwort_state_runtime import preference_store
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_sqlite_state_native import NATIVE, ahk_function
from tests.test_tray_startup import native_script, startup_gate


PORTS = r'''
ForwardRequest(payload) {
    data := Buffer(3 * A_PtrSize, 0)
    NumPut("Ptr", 1, data, 0)
    NumPut("UInt", (StrLen(payload) + 1) * 2, data, A_PtrSize)
    NumPut("Ptr", StrPtr(payload), data, 2 * A_PtrSize)
    DetectHiddenWindows True
    if SendMessage(0x004A, 0, data.Ptr, , "ahk_id " A_ScriptHwnd) != 1
        throw Error("Owned IPC request was not acknowledged")
}
LaunchDesk(text, mode) {
    global G_OverrideSeqNum, G_OverrideZID, G_OverrideLanguage
    global G_Launches, G_InjectDuringLaunch, G_ExpectedFailure, G_Startup
    if G_Startup.phase != "ready"
        throw Error("Unready startup dispatched a request")
    G_Launches.Push("desk|" text "|" mode "|" G_OverrideSeqNum "|" G_OverrideZID "|" G_OverrideLanguage)
    G_OverrideSeqNum := ""
    G_OverrideZID := ""
    G_OverrideLanguage := ""
    if G_InjectDuringLaunch && G_Launches.Length == 1
        ForwardRequest("--desk`narrived during drain")
    if G_ExpectedFailure && G_Launches.Length == 1
        throw Error("Owned launch rejected")
    return 0
}
LaunchRestore(target) {
    global G_OverrideSeqNum, G_OverrideZID, G_OverrideLanguage, G_Launches
    G_Launches.Push("restore|" target "|" G_OverrideSeqNum "|" G_OverrideZID "|" G_OverrideLanguage)
    G_OverrideSeqNum := ""
    G_OverrideZID := ""
    G_OverrideLanguage := ""
    return 0
}
LaunchBrowserTab(*) {
    throw Error("Unexpected browser launch")
}
ObserveError(error, mode) {
    global G_ExpectedFailure, G_FailureObserved
    if G_ExpectedFailure && error.Message == "Owned launch rejected" {
        G_FailureObserved := true
        return true
    }
    Fail(error.Message)
}
FinishRequests(expected) {
    global G_Launches, G_BufferedArgs, G_Initialized
    if !G_Initialized || G_Launches.Length != expected
        return
    if G_BufferedArgs.Length
        Fail("Ready startup retained unprocessed requests")
    AssertCommitted()
    for launch in G_Launches
        FileAppend(launch "`n", A_ScriptDir "\requests.txt", "UTF-8-RAW")
    ExitApp(0)
}
'''


def ipc_script(tmp_path, port, body, *, deadline=8000):
    source = NATIVE.read_text(encoding='utf-8-sig')
    initial = re.search(r'    if \(A_Args.Length > 0\)\n        G_BufferedArgs.Push\(A_Args\)', source).group()
    script, _ = native_script(tmp_path, port, body.replace('INITIAL_REQUEST', initial), deadline=deadline)
    content = script.read_text(encoding='utf-8')
    content = content.replace('G_BufferedArgs := [["queued-request"]]', '''G_BufferedArgs := []
global G_LaunchInBrowser := 0
G_OverrideSeqNum := ""
G_OverrideZID := ""
G_OverrideLanguage := ""
G_Launches := []
G_InjectDuringLaunch := false
G_ExpectedFailure := false
G_FailureObserved := false
OnError(ObserveError)
OnMessage(0x004A, Receive_WM_COPYDATA)''')
    content = content.replace(ahk_function(content, 'ProcessArgs'), ahk_function(source, 'ProcessArgs'))
    for name in ('Receive_WM_COPYDATA', 'ExtractZidFromPath', 'BuildDeskBrowserUrl'):
        content += '\n'+ahk_function(source, name)
    script.write_text(content+'\n'+PORTS, encoding='utf-8')
    return script


def run_requests(script, args=()):
    result = subprocess.run([desk.get_ahk_executable(), '/ErrorStdOut', str(script), *args],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr
    return (script.parent/'requests.txt').read_text(encoding='utf-8').splitlines()


@pytest.mark.parametrize('during_drain', [False, True])
def test_initial_and_forwarded_invocations_keep_parser_boundaries(preference_world, tmp_path, during_drain):
    url, controller, path = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    before = store.snapshot(), path.read_bytes()
    script = ipc_script(tmp_path, controller.server_port, '''
G_InjectDuringLaunch := INJECT
INITIAL_REQUEST
ForwardRequest("--set-language`nde`n--seq-num`n7`n--zid`n20261115162002`n--language`nfr`n--restore`nowned restore")
ForwardRequest("--desk`nindependent request")
ForwardRequest("")
InitializeTrayMenu()
StartServerProcess()
SetTimer(CheckRequests, 20)
CheckRequests() {
    FinishRequests(EXPECTED)
}
'''.replace('INJECT', str(during_drain).lower()).replace('EXPECTED', '4' if during_drain else '3'))
    observed = run_requests(script, ['--text-mode', 'single', '--seq-num', '4', '--zid',
        '20261115162001', '--language', 'de', '--desk', 'initial text'])
    expected = ['desk|initial text|single|4|20261115162001|de',
                'restore|owned restore|7|20261115162002|fr',
                'desk|independent request|multi|||']
    if during_drain:
        expected.append('desk|arrived during drain|multi|||')
    assert observed == expected
    assert (store.snapshot(), path.read_bytes()) == before


def test_failed_readiness_retains_ipc_until_retry_without_stale_replay(preference_world, tmp_path):
    url, controller, path = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    before = store.snapshot(), path.read_bytes()
    with startup_gate(url, lambda: True, config_reply=lambda: (403, b'{}')) as rejected:
        script = ipc_script(tmp_path, rejected.server_port, f'''
INITIAL_REQUEST
ForwardRequest("--desk`npending IPC")
InitializeTrayMenu()
StartServerProcess()
old := G_Startup
stage := "failure"
SetTimer(CheckRetry, 20)
CheckRetry() {{
    global G_Startup, G_ControllerPort, G_Launches, old, stage
    if stage == "failure" && G_Startup.phase == "failed" {{
        if G_Launches.Length
            Fail("Failed readiness dispatched requests")
        stage := "retry"
        G_ControllerPort := {controller.server_port}
        RetryStartupPreferences()
    }}
    if stage == "retry" && G_Initialized {{
        old.NotifyReady()
        FinishRequests(2)
    }}
}}
''', deadline=400)
        observed = run_requests(script, ['--desk', 'initial request'])
    assert observed == ['desk|initial request|multi|||', 'desk|pending IPC|multi|||']
    assert (store.snapshot(), path.read_bytes()) == before


def test_parser_failure_retains_later_invocations_without_replaying_partial_effect(preference_world, tmp_path):
    url, controller, path = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    before = store.snapshot(), path.read_bytes()
    script = ipc_script(tmp_path, controller.server_port, '''
G_ExpectedFailure := true
INITIAL_REQUEST
ForwardRequest("--desk`nlater one")
ForwardRequest("--desk`nlater two")
InitializeTrayMenu()
StartServerProcess()
stage := "failure"
SetTimer(CheckParserRecovery, 20)
CheckParserRecovery() {
    global G_FailureObserved, G_BufferedArgs, G_Launches, G_ExpectedFailure, stage
    if stage == "failure" && G_FailureObserved {
        if G_BufferedArgs.Length != 2 || G_Launches.Length != 1
            Fail("Parser failure discarded or replayed requests")
        stage := "retry"
        G_ExpectedFailure := false
        RetryStartupPreferences()
    }
    if stage == "retry"
        FinishRequests(3)
}
''')
    observed = run_requests(script, ['--desk', 'partial launch'])
    assert observed == ['desk|partial launch|multi|||', 'desk|later one|multi|||', 'desk|later two|multi|||']
    assert (store.snapshot(), path.read_bytes()) == before


def test_ready_ipc_waits_for_replacement_readiness_if_dispatch_is_cancelled(preference_world, tmp_path):
    url, controller, path = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    before = store.snapshot(), path.read_bytes()
    release = threading.Event()
    paused, seen = tmp_path/'native/paused', tmp_path/'native/seen'

    def held_preferences():
        if paused.exists() and not release.is_set():
            seen.touch()
            return 503, b'{}'
        return None

    with startup_gate(url, lambda: True, config_reply=held_preferences, release=release) as gate:
        script = ipc_script(tmp_path, gate.server_port, rf'''
INITIAL_REQUEST
InitializeTrayMenu()
StartServerProcess()
stage := "ready"
SetTimer(CheckReplacement, 20)
CheckReplacement() {{
    global G_Initialized, G_Launches, stage
    if stage == "ready" && G_Initialized {{
        ForwardRequest("--desk`nready one")
        ForwardRequest("--desk`nready two")
        StopStartupReadiness()
        CompleteStartupInitialization()
        if G_Launches.Length
            Fail("Stopped readiness dispatched a pending IPC timer")
        FileAppend("pause", "{paused}")
        stage := "waiting"
        RetryStartupPreferences()
    }}
    if stage == "waiting" && FileExist("{seen}") {{
        if G_Launches.Length
            Fail("Replacement dispatched requests before committed readiness")
        reply := ""
        if !SendControllerRequest("/release", "", &reply, 2, "GET")
            Fail("Owned readiness release failed")
        stage := "replacement"
    }}
    if stage == "replacement"
        FinishRequests(2)
}}
''')
        try:
            observed = run_requests(script)
        finally:
            release.set()
    assert observed == ['desk|ready one|multi|||', 'desk|ready two|multi|||']
    assert (store.snapshot(), path.read_bytes()) == before


def test_stopped_controller_browser_launch_waits_for_committed_preferences_and_cancels_stale(preference_world, tmp_path):
    url, controller, path = preference_world
    store = preference_store(controller.config, controller.resolved_paths)
    store.set('default_language', 'de')
    source = NATIVE.read_text(encoding='utf-8-sig')
    extra = '\n'.join(ahk_function(source, n) for n in (
        'LaunchBrowserTab', '_ExecuteBrowserLaunch', 'FetchHtmlViaHttp',
        'BuildDeskBrowserUrl', 'BuildVerifyLanguageUrl', 'ExtractZidFromPath',
    ))
    gate_flag = tmp_path / 'config-ready.txt'
    render_posts = []

    def config_override():
        if not gate_flag.exists():
            return 503, b'{}'
        return None

    def handle_post(req_path, headers, raw):
        if req_path == '/api/v1/render':
            render_posts.append(raw.decode('utf-8'))
            return 200, b'{"html_b64":"b2s="}'
        return None

    with startup_gate(url, lambda: True, config_reply=config_override, post_reply=handle_post) as gate:
        script, _ = native_script(tmp_path, gate.server_port, '''
G_BufferedArgs := []
G_LaunchInBrowser := 1
G_SplitGapLimit := 60
G_WindowCount := 0
G_OpenedTabs := []
InitializeTrayMenu()

; Phase A: healthy endpoint but /api/v1/config returns 503 -> must defer, not launch immediately
if !LaunchBrowserTab("hello world", "single", "20261116000001")
    Fail("LaunchBrowserTab failed to join asynchronous readiness")
if G_OpenedTabs.Length != 0 || G_PendingBrowserLaunches.Length != 1
    Fail("LaunchBrowserTab did not defer until committed preferences arrived")

; Cancel generation and verify stale NotifyReady cannot dispatch cancelled tab
old := G_Startup
StopStartupReadiness()
if G_PendingBrowserLaunches.Length != 0
    Fail("StopStartupReadiness did not clear pending browser launches")
old.NotifyReady()
if G_OpenedTabs.Length != 0
    Fail("Cancelled startup dispatched stale browser tab")

; Phase B: new deferred launch while /api/v1/config still 503 -> wait -> release -> hydrates de & opens tab
if !LaunchBrowserTab("Haus.", "single", "20261116000002")
    Fail("Replacement LaunchBrowserTab failed to queue")
SetTimer(ReleaseConfig, -250)
ReleaseConfig() {
    FileAppend("ready", "__GATE__")
}
SetTimer(CheckBrowserReady, 20)
CheckBrowserReady() {
    global G_OpenedTabs, G_PendingBrowserLaunches, G_Startup
    if !IsObject(G_Startup) || G_Startup.phase != "ready" || G_OpenedTabs.Length == 0
        return
    if G_PendingBrowserLaunches.Length != 0
        Fail("Completed startup left pending browser launches")
    AssertCommitted("de")
    for item in G_OpenedTabs
        FileAppend(item "`n", A_ScriptDir "\\tabs.txt", "UTF-8-RAW")
    ExitApp(0)
}
'''.replace('__GATE__', str(gate_flag)))
        text = script.read_text(encoding='utf-8')
        text = text.replace('_ExecuteBrowserLaunch(text, mode, zid) {\n    global G_BrowserLaunches, G_CurrentLang\n    G_BrowserLaunches.Push("browser|" text "|" mode "|" zid "|" G_CurrentLang)\n}\n', '')
        overrides = '''
CleanClipboardText(text) {
    return Trim(text)
}
Run(target, *) {
    global G_OpenedTabs
    G_OpenedTabs.Push(target)
}
TrayTip(*) {
}
''' + '\n' + extra
        script.write_text(text + overrides, encoding='utf-8')
        from tests.test_tray_startup import execute
        execute(script)
    opened = (tmp_path / 'native/tabs.txt').read_text(encoding='utf-8').splitlines()
    assert len(opened) == 1
    assert 'session_zid=20261116000002' in opened[0]
    assert len(render_posts) == 1
    assert '"language": "de"' in render_posts[0]
