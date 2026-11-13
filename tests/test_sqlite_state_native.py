"""Retained AHK command functions execute real HTTP; only tray drawing is replaced."""
from pathlib import Path
import os
import subprocess

import kardenwort_desk as desk
from kardenwort_state_runtime import preference_store
from tests.test_preference_commands import preference_world  # noqa: F401


NATIVE = Path(os.environ.get('KARDENWORT_NATIVE_REPO', Path(__file__).resolve().parents[2]/'20240411110510-autohotkey'))/'kardenwort-window/kardenwort-window.ahk'


def ahk_function(source, name):
    start = source.index('\n'+name+'(')+1
    opening = source.index('{', start)
    depth = 1
    quote = None
    escaped = False
    for index in range(opening+1, len(source)):
        char = source[index]
        if escaped:
            escaped = False
            continue
        if char == '`':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            continue
        depth += (char == '{')-(char == '}')
        if depth == 0:
            return source[start:index+1]
    raise AssertionError(name)


def test_native_language_commits_before_projection_and_has_no_ini_fallback(preference_world, tmp_path):
    url, server, path = preference_world
    store = preference_store(server.config, server.resolved_paths)
    source = NATIVE.read_text(encoding='utf-8-sig')
    names = ['SendControllerRequest', 'CommittedPrimitive', 'LoadCommittedPreferences',
             'ProjectCommittedTheme', 'SetLanguage', 'JSON_Stringify']
    script = '\n'.join(ahk_function(source, name) for name in names)
    before = path.read_bytes(), path.stat().st_mtime_ns
    harness = tmp_path/'committed-preferences.ahk'
    harness.write_text('''#Requires AutoHotkey v2.0
#NoTrayIcon
G_ServerHost := "127.0.0.1"
G_ServerPort := PORT
G_ControllerPort := PORT
G_ServerApiKey := "test-controller-api-key"
G_CurrentLang := "en"
G_DefaultLanguage := "en"
G_ServerStatus := "Running"
G_Theme := "dark"
G_DefaultZoom := 100
G_AutoUpdate := 0
G_AutoSave := 0
G_AutoSaveOnClose := 0
G_GuiBgColor := ""
G_GuiTextColor := ""
G_DwmDark := 1
try {
    if !SetLanguage("fr") || G_CurrentLang != "fr"
        throw Error("Committed language was not projected")
    G_ServerApiKey := "invalid"
    if SetLanguage("de") || G_CurrentLang != "fr"
        throw Error("Failed command projected or used a file fallback")
    FileAppend("verified", A_Args[1], "UTF-8-RAW")
    ExitApp(0)
} catch as e {
    FileAppend(e.Message, "*")
    ExitApp(1)
}
UpdateTrayMenu() {
}
UpdateTrayIcon() {
}
'''.replace('PORT', str(server.server_port))+script, encoding='utf-8')
    proof = tmp_path/'native-result.txt'
    result = subprocess.run([desk.get_ahk_executable(), '/ErrorStdOut', str(harness), str(proof)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout+result.stderr
    assert proof.read_text() == 'verified'
    assert store.get('default_language') == 'fr'
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert 'IniRead' not in ahk_function(source, 'LoadCommittedPreferences')
    assert 'PersistIniLanguage' not in ahk_function(source, 'SetLanguage')
