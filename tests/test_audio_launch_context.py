"""Configured audio scripts retain the normal Python script import context."""
import json
import sys
from types import SimpleNamespace
from urllib.request import Request, urlopen

from tests.controller_test_support import controller_lifetime


def test_relocated_audio_cli_imports_its_sibling_module(tmp_path):
    capability = tmp_path/'relocated speech capability'
    capability.mkdir()
    (capability/'owned_speech_module.py').write_text('MESSAGE = "sibling import works"\n')
    result_file = capability/'actual-arguments.json'
    script = capability/'speak.py'
    script.write_text(
        'import json,sys\nfrom pathlib import Path\nfrom owned_speech_module import MESSAGE\n'
        'Path(__file__).with_name("actual-arguments.json").write_text(json.dumps([MESSAGE,sys.argv[1:]]))\n'
    )
    with controller_lifetime(tmp_path, SimpleNamespace(server_port=59999)) as (base, server):
        server.resolved_paths.update(anki_tts_cli=script, kardenwort_python=sys.executable)
        request = Request(base+'/api/v1/audio/play',
            json.dumps({'text':'Haus', 'language':'de'}).encode(),
            {'Content-Type':'application/json', 'X-API-Token':server.api_key})
        with urlopen(request, timeout=10) as response:
            reply = json.load(response)['data']
        assert reply['status'] == 'completed'
        assert reply['playback_confirmed'] is False
    assert json.loads(result_file.read_text()) == ['sibling import works', ['--', 'Haus', 'de']]
