"""Owned controller and real child-process storage boundary contracts."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import ast
import configparser
import inspect

import pytest

import kardenwort_desk as desk
from tests.browser_test_server import create_browser_test_server
from tests.controller_test_support import controller_world
from tests.test_progressive import setup_test_env
from tests.test_intent_architecture import complexity
from kardenwort_progressive_context import encode_runtime, decode_runtime


@pytest.mark.parametrize('owner', [
    {'backend': 'tsv', 'path': 'foreign-results'},
    {'backend': 'sqlite', 'path': 'foreign.db'},
])
def test_controller_rejects_foreign_progressive_storage(controller_world, owner):
    url, server = controller_world
    before = server.arbiter.get_queue_status()
    body = {'session_zid': '20261115095000', 'storage_owner': owner}
    request = Request(url+'/session/progressive/enqueue', method='POST',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'X-API-Token': server.api_key})
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=5)
    assert caught.value.code == 400
    assert server.arbiter.get_queue_status() == before
    adapter = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    with adapter.db.get_connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM sessions WHERE zid=?', (body['session_zid'],)).fetchone()[0] == 0


@contextmanager
def rejecting_controller():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(409)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = create_browser_test_server(Handler)
    try:
        server.start()
        yield server
    finally:
        server.server_close()


@pytest.mark.parametrize('controller_mode', ['explicit', 'absent'])
def test_detached_worker_uses_caller_runtime_and_finishes(tmp_path, monkeypatch, controller_mode):
    config, paths = setup_test_env(tmp_path)
    config.read_dict({'storage': {'backend': 'tsv'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual', 'run_lemma_enrichment': 'manual'},
        'server': {'update_delivery': 'desktop_file'}})
    paths.update(base_dir=tmp_path, storage_backend='tsv', sqlite_db_path=tmp_path/'owned.db', kardenwort_python=Path(sys.executable))
    source = tmp_path/'20261115095100-owned.en.tsv'
    source.write_text('WordSource\tSentenceSource\tSentenceDestination\tSentenceSourceIndex\nowned\tOwned source.\tOwned translation.\t1\n', encoding='utf-8')
    requests = []
    if controller_mode == 'absent':
        def unexpected_controller(request, **kwargs):
            requests.append(request.full_url)
            raise ConnectionRefusedError('No controller belongs to this caller')
        monkeypatch.setattr('urllib.request.urlopen', unexpected_controller)
    processes = []
    original = subprocess.Popen

    def owned_launch(command, *args, **kwargs):
        # Refuse the known unsafe old launch before it can read live config.
        assert '--runtime-stdin' in command
        process = original(command, *args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', owned_launch)
    try:
        with rejecting_controller() as controller:
            if controller_mode == 'explicit':
                config.read_dict({'server': {'controller_port': str(controller.server_port)}})
            assert desk.run_progressive_worker_async(source, 'en', 'ru', '', 'google', 'False',
                skip_intellifiller=True, resolved_paths=paths, config=config)
            assert len(processes) == 1
            assert processes[0].wait(timeout=15) == 0
        assert requests == []
        assert source.with_suffix('.enrichment_done').exists()
        assert source.read_text(encoding='utf-8').splitlines()[-1].startswith('owned\t')
        assert not paths['sqlite_db_path'].exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize('case_sensitive', [False, True])
@pytest.mark.parametrize('interpolation', [None, configparser.BasicInterpolation(), configparser.ExtendedInterpolation()])
def test_runtime_transport_preserves_configuration_semantics(tmp_path, case_sensitive, interpolation):
    config = configparser.ConfigParser(allow_no_value=True, interpolation=interpolation)
    if case_sensitive:
        config.optionxform = str
    label = '100%% ready' if isinstance(interpolation, configparser.BasicInterpolation) else '100% ready'
    config.read_dict({'DEFAULT': {'fallback': 'unchanged'}, 'settings': {'Label': label, 'empty': None}})
    restored, paths = decode_runtime(encode_runtime(config,
        {'results_dir': tmp_path, 'storage_backend': 'tsv', 'config': config}))
    assert restored.get('settings', 'fallback') == 'unchanged'
    assert restored.get('settings', 'Label') == config.get('settings', 'Label')
    assert restored.get('settings', 'empty') is None
    assert restored.optionxform('Label') == config.optionxform('Label')
    assert paths['results_dir'] == tmp_path and isinstance(paths['results_dir'], Path)
    assert paths['storage_backend'] == 'tsv' and 'config' not in paths


def test_malformed_worker_runtime_fails_before_file_or_storage_effects(tmp_path):
    source = tmp_path/'20261115095200-owned.en.tsv'
    source.write_text('WordSource\nowned\n')
    result = subprocess.run([sys.executable, str(Path(desk.__file__)), 'progressive-worker',
        '--runtime-stdin', '--tsv', str(source), '--language', 'en', '--target-lang', 'ru',
        '--prompt', '', '--provider', 'google', '--word-empty', 'False'],
        input=b'{malformed', capture_output=True, timeout=10)
    assert result.returncode != 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [source.name]


def test_worker_boundary_functions_retain_branch_ceiling():
    for function in [encode_runtime, decode_runtime, desk.progressive_storage_owner,
                     desk.require_progressive_endpoint, desk.launch_progressive_child,
                     desk.progressive_runtime_inputs, desk.has_projection_database,
                     desk.update_projection_adapter, desk.update_projection_sentences]:
        assert complexity(ast.parse(inspect.getsource(function)).body[0]) <= 7, function.__name__
