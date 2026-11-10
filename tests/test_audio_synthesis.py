"""Public HTTP/actual child-process contracts; only the unmanaged engine is controlled."""
import ast
import json
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request

import pytest

from kardenwort_audio import AudioError, media_type, MAX_BYTES
from tests.test_intent_architecture import complexity, functions

ROOT = Path(__file__).resolve().parents[1]
from tests.audio_test_support import CLI, audio_server, pcm



def request(server, body, path='/api/v1/audio/synthesize', token='owned-audio-token', method='POST'):
    headers = {'Content-Type': 'application/json', 'X-API-Token': token}
    url = 'http://127.0.0.1:' + str(server.server_address[1])
    req = urllib.request.Request(url+path, data=json.dumps(body).encode() if method == 'POST' else None,
        headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=5)


def test_synthesis_returns_checked_client_media_from_relocated_capability(audio_server):
    server, owner = audio_server
    with request(server, {'text': '--clipboard', 'language': 'de'}) as response:
        assert response.status == 200
        assert response.headers['Content-Type'] == 'audio/wav'
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.read() == pcm()
    assert not list(owner.rglob('tts_cycle_state.json'))
    assert not list(owner.rglob('audio_cycle_state.json'))
    assert not list(owner.rglob('speech.media'))


def test_synthesis_preserves_valid_mpeg_bytes_and_mime(audio_server):
    server, _ = audio_server
    with request(server, {'text':'mpeg','language':'en'}) as response:
        assert response.headers['Content-Type'] == 'audio/mpeg'
        assert response.read() == (ROOT/'tests/fixtures/silence.mp3').read_bytes()


def test_mpeg_structural_validation_rejects_truncation():
    data = (ROOT/'tests/fixtures/silence.mp3').read_bytes()
    assert media_type(data) == 'audio/mpeg'
    assert media_type(b'ID3\x04\x00\x00\x00\x00\x00\x00' + data) == 'audio/mpeg'
    with pytest.raises(AudioError):
        media_type(data[:-1])


@pytest.mark.parametrize('body,status', [([],400), ({},400), ({'text':'a','language':[]},400),
    ({'text':'x'*2001,'language':'en'},400), ({'text':'a ||| b','language':'en'},400),
    ({'text':'invalid','language':'en'},500), ({'text':'failure','language':'en'},500),
    ({'text':'oversize','language':'en'},500)])
def test_synthesis_rejects_invalid_commands_engine_and_bytes(audio_server, body, status):
    server, _ = audio_server
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, body)
    assert failure.value.code == status
    assert json.load(failure.value)['status'] == 'error'


def test_synthesis_auth_method_and_missing_capability_are_explicit(audio_server):
    server, _ = audio_server
    for token, method, status in [('', 'POST',403), ('owned-audio-token','GET',405)]:
        with pytest.raises(urllib.error.HTTPError) as failure:
            request(server, {'text':'word','language':'en'}, token=token, method=method)
        assert failure.value.code == status
    server.resolved_paths['anki_tts_cli'] = 'missing-configured-cli.py'
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, {'text':'word','language':'en'})
    assert failure.value.code == 500
    assert json.load(failure.value)['error_code'] == 'CONFIGURATION_ERROR'
    server.api_key = ''
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, {'text':'word','language':'en'})
    assert failure.value.code == 403
    assert json.load(failure.value)['error_code'] == 'TOKEN_NOT_CONFIGURED'


def test_synthesis_timeout_releases_process_and_capacity(audio_server):
    server, owner = audio_server
    server.config.set('audio','synthesis_timeout','0.15')
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, {'text':'hang','language':'en'})
    assert failure.value.code == 504
    server.config.set('audio','synthesis_timeout','2')
    with request(server, {'text':'word','language':'en'}) as response:
        assert response.read() == pcm()
    assert not list(owner.rglob('speech.media'))


def open_hanging_request(server):
    connection = socket.create_connection(server.server_address, timeout=3)
    body = json.dumps({'text':'hang','language':'en'}).encode()
    headers = f'POST /api/v1/audio/synthesize HTTP/1.0\r\nX-API-Token: {server.api_key}\r\nContent-Length: {len(body)}\r\n\r\n'
    connection.sendall(headers.encode() + body)
    pid = int(server.audio_signal.recv(100))
    return connection, owned_process_witness(pid)


def owned_process_witness(pid):
    if sys.platform != 'win32':
        return pid
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x100000, False, pid)
    assert handle, f'Cannot observe entered owned audio process {pid}'
    return kernel,handle,pid


def assert_owned_process_exited(witness):
    if sys.platform != 'win32':
        import os
        with pytest.raises(ProcessLookupError):
            os.kill(witness,0)
        return
    import ctypes
    from ctypes import wintypes
    kernel,handle,pid = witness
    try:
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        assert kernel.WaitForSingleObject(handle,0) == 0, f'Owned audio PID {pid} remains alive'
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)


@pytest.mark.parametrize('_iteration',range(10))
def test_disconnect_cancels_owned_process_and_overload_is_explicit(audio_server,_iteration):
    server, _ = audio_server
    server.config.set('audio','synthesis_timeout','10')
    connections = []
    pids = []
    try:
        for _ in range(2):
            connection, pid = open_hanging_request(server)
            connections.append(connection)
            pids.append(pid)
        with pytest.raises(urllib.error.HTTPError) as failure:
            request(server, {'text':'word','language':'en'})
        assert failure.value.code == 409
        assert json.load(failure.value)['error_code'] == 'ROW_BUSY'
    finally:
        for connection in connections:
            connection.close()
    for worker in list(server.workers):
        worker.join(3)
        assert not worker.is_alive()
    for pid in pids:
        assert_owned_process_exited(pid)
    with request(server, {'text':'word','language':'en'}) as response:
        assert response.read() == pcm()


@pytest.mark.parametrize('path',['/api/v1/audio/play','/session/play'])
def test_host_alias_reports_job_exit_failure(audio_server,path):
    server, owner = audio_server
    script = owner/'failed-host.py'
    script.write_text('raise SystemExit(7)\n')
    server.resolved_paths['anki_tts_cli'] = script
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, {'text':'word','language':'en'},path=path)
    assert failure.value.code == 500
    assert json.load(failure.value)['error_code'] == 'DESK_FAILED'


@pytest.mark.parametrize('engine,enabled',[('unknown',True),('gTTS',False),('Piper',False),('Piper',True)])
def test_configured_engine_failure_does_not_substitute_another_engine(audio_server,engine,enabled):
    server, owner = audio_server
    profile = {'tts_engine':engine,'gtts_enabled':enabled,'piper_enabled':enabled,
        'tts_cycle_enabled':True, 'audio_dictionary_cycle_enabled':True}
    (owner/'addon'/'config.json').write_text(json.dumps(profile))
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server,{'text':'word','language':'en'})
    assert failure.value.code == 500
    assert json.load(failure.value)['error_code'] == 'DESK_FAILED'


def test_real_host_cli_does_not_report_success_after_failed_chain_segment(audio_server):
    server, owner = audio_server
    (owner/'addon'/'config.json').write_text(json.dumps({'tts_engine':'gTTS',
        'gtts_enabled':False,'piper_enabled':False,'tts_cycle_enabled':False,
        'audio_dictionary_cycle_enabled':False,'persistent_cache_enabled':False}))
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server,{'text':'one ||| two','language':'en'},path='/api/v1/audio/play')
    assert failure.value.code == 500
    assert json.load(failure.value)['error_code'] == 'DESK_FAILED'


@pytest.mark.parametrize('data', [b'', b'ID3', b'not audio', b'ID3'+b'\0'*7,
    b'RIFF'+b'\0'*100, pcm()[:-1], b'x'*(MAX_BYTES+1)], ids=['empty','id3','garbage','id3-only','bad-wav','truncated-wav','oversized'])
def test_invalid_media_is_not_a_success(data):
    with pytest.raises(AudioError):
        media_type(data)


def test_audio_slice_dependencies_and_complexity():
    for name in ('kardenwort_audio', 'kardenwort_audio_runtime', 'kardenwort_audio_windows'):
        tree = ast.parse((ROOT/(name+'.py')).read_text())
        violations = {node.name:complexity(node) for node in functions(tree) if complexity(node)>7}
        assert not violations
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not imports.intersection({'kardenwort_controller','kardenwort_desk'})
    tree = ast.parse(CLI.read_text())
    names = {'synthesize_stateless','stateless_gtts','stateless_piper'}
    measured = {node.name:complexity(node) for node in functions(tree) if node.name in names}
    assert set(measured) == names
    assert max(measured.values()) <= 7


def test_audio_javascript_branch_ceiling():
    import re
    from tests.test_preference_js_structure import function_body
    source = (ROOT/'web/js/features/audio.js').read_text()
    names = ['publish','disposeMedia','cancel','fail','validateBlob','attachMedia','mediaFailed',
        'startMedia','checkedBlob','nextSegment','play']
    measured = {name: 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', function_body(source,name)))
        for name in names}
    assert max(measured.values()) <= 7, measured
