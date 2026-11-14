"""Owned real CLI/HTTP lifetime with controlled unmanaged speech output."""
import configparser
import io
import json
from pathlib import Path
import socket
import sys
import wave
import tempfile

import pytest
import kardenwort_desk as desk
from kardenwort_controller import ControllerRequestHandler
from tests.browser_test_server import create_browser_test_server

ROOT = Path(__file__).resolve().parents[1]
CLI = Path(desk.load_config()[1]['anki_tts_cli'])


def pcm():
    output = io.BytesIO()
    with wave.open(output, 'wb') as media:
        media.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        media.writeframes(b'\0\0' * 800)
    return output.getvalue()


ENGINE = '''import wave
class gTTS:
    def __init__(self, text, **options): self.text = text
    def save(self, destination):
        if self.text == 'mpeg':
            from pathlib import Path
            Path(destination).write_bytes(Path(__file__).with_name('silence.mp3').read_bytes())
            return
        if self.text == 'failure': raise RuntimeError('engine failure')
        if self.text == 'hang':
            import threading, socket, os
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as signal:
                signal.sendto(str(os.getpid()).encode(), ('127.0.0.1', int(os.environ['AUDIO_TEST_SIGNAL_PORT'])))
            threading.Event().wait()
        if self.text == 'invalid':
            open(destination, 'wb').write(b'not audio')
            return
        if self.text == 'oversize':
            open(destination, 'wb').write(b'x' * (8*1024*1024+1))
            return
        with wave.open(destination, 'wb') as media:
            media.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            media.writeframes(b'\\0\\0' * 800)
class gTTSError(Exception): pass
'''


@pytest.fixture
def audio_server(tmp_path):
    # A relocatable owned CLI with its real config loader and CLI parser. Only
    # the third-party gTTS package boundary is replaced, never Desk execution.
    private_media = Path(tempfile.gettempdir())
    previous_media = set(private_media.glob('kardenwort-audio-*'))
    script = tmp_path / 'relocated speech cli.py'
    script.write_bytes(CLI.read_bytes())
    addon = tmp_path / 'addon'
    (addon/'vendor').mkdir(parents=True)
    (addon/'vendor'/'gtts.py').write_text(ENGINE, encoding='utf-8')
    (addon/'vendor'/'silence.mp3').write_bytes((ROOT/'tests/fixtures/silence.mp3').read_bytes())
    (tmp_path/'config.json').write_text(json.dumps({'anki_addon_path': str(addon)}))
    (addon/'config.json').write_text(json.dumps({'tts_engine': 'gTTS',
        'tts_cycle_enabled': True, 'audio_dictionary_cycle_enabled': True,
        'persistent_cache_enabled': True, 'persistent_cache_path': str(tmp_path/'forbidden-cache')}))
    # -S excludes ambient site packages, so the owned external port is selected.
    # A launcher preserves actual Python execution while selecting that port.
    signal_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    signal_socket.bind(('127.0.0.1',0))
    signal_socket.settimeout(3)
    launcher = tmp_path/'owned_cli.py'
    launcher.write_text("import sys,runpy,os\nos.environ['AUDIO_TEST_SIGNAL_PORT']=" + repr(str(signal_socket.getsockname()[1])) + "\nsys.path.insert(0, " + repr(str(addon/'vendor')) + ")\n"
        "sys.argv[0]=" + repr(str(script)) + "\nrunpy.run_path(sys.argv[0],run_name='__main__')\n")
    server = create_browser_test_server(ControllerRequestHandler)
    server.api_key = 'owned-audio-token'
    server.config = configparser.ConfigParser()
    server.config.read_dict({'audio': {'synthesis_timeout': '2'},
        'storage': {'backend':'sqlite','sqlite_db_path':str(tmp_path/'audio.db')}})
    server.resolved_paths = {'anki_tts_cli': launcher, 'kardenwort_python': sys.executable,
        'sqlite_db_path':tmp_path/'audio.db','storage_backend':'sqlite','base_dir':tmp_path}
    server.start()
    server.audio_signal = signal_socket
    try:
        yield server, tmp_path
    finally:
        server.server_close()
        signal_socket.close()
        assert not (tmp_path/'forbidden-cache').exists()
        assert set(private_media.glob('kardenwort-audio-*')) == previous_media
