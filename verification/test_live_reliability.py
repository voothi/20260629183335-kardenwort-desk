"""Explicit live checks; excluded from default discovery by pytest.ini.

Select one provider with KARDENWORT_LIVE_PROVIDERS=google (or argos).
No engine, controller, routing or SQLite outcomes are substituted.
"""
import configparser
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

import kardenwort_desk as desk
from kardenwort_controller import ControllerRequestHandler
from tests.browser_test_server import create_browser_test_server
from tests.controller_test_support import controller_lifetime, translation_service, TranslationRequestHandler


def live_config():
    config = configparser.ConfigParser(interpolation=None)
    config.read(os.environ['KARDENWORT_LIVE_CONFIG'], encoding='utf-8-sig')
    return config


def post(base, path, body, token):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'X-API-Token': token})
    try:
        return urllib.request.urlopen(request, timeout=65)
    except urllib.error.HTTPError as response:
        return response


def seed(adapter, zid):
    sources = ['A small red fox sleeps.', 'The green tree grows.']
    headers = ['TokenOrder', 'Quotation', 'WordSource', 'SentenceSourceIndex',
               'SentenceSource', 'SentenceDestination', 'WordDestination']
    rows = [['0', word, word, str(index), source, 'Previous translation', 'old word']
            for index, (source, word) in enumerate(zip(sources, ['fox', 'tree']), 1)]
    adapter.save_session(session_zid=zid, slug='owned-live-check', source_language='en',
        target_language='ru', text_mode='multi', source_raw_text='\n'.join(sources),
        headers=headers, data_rows=rows, text_provenance='cached:fixture')
    return sources


@pytest.mark.parametrize('provider', os.environ.get('KARDENWORT_LIVE_PROVIDERS', 'google').split(','))
def test_live_retext_persistence_and_cross_session_isolation(tmp_path, provider):
    assert provider in ('google', 'argos', 'deepl')
    configured = live_config()
    with translation_service(TranslationRequestHandler) as translator, \
            controller_lifetime(tmp_path, translator) as (base, server):
        server.config.set('pipeline', 'text_reprocess_provider', provider)
        server.config.set('pipeline', 'text_provider_chain', provider)
        if provider == 'deepl':
            # Explicit opt-in only; never copy or publish the credential.
            assert os.environ.get('KARDENWORT_ALLOW_QUOTA') == '1'
            origin = Path(os.environ['KARDENWORT_LIVE_CONFIG']).parent
            settings = configured.get('environment', 'deepl_settings_file')
            server.config.set('environment', 'deepl_settings_file', str((origin / settings).resolve()))
        adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
        target, neighbor = '20261112222201', '20261112222202'
        sources = seed(adapter, target)
        seed(adapter, neighbor)
        before = adapter.db.get_session_bundle(target)
        untouched = adapter.db.get_session_bundle(neighbor)
        with post(base, '/session/retext', {'session_zid': target, 'language': 'en',
                  'text_mode': 'multi'}, server.api_key) as response:
            status = response.status
            payload = json.load(response)
        after = adapter.db.get_session_bundle(target)
        assert adapter.db.get_session_bundle(neighbor) == untouched
        if status != 200:
            assert after == before, 'Failed live request partially committed'
            pytest.fail(f'Live {provider} unavailable: HTTP {status}; storage unchanged')
        data = payload.get('data', payload)
        assert payload['status'] == 'success'
        assert data['active_text_provider'] == provider
        assert data['text_provenance'] == f'live:{provider}'
        stored = adapter.db.get_sentences_by_session(target)
        assert [row['sentence_index'] for row in stored] == [1, 2]
        returned = {int(key): value for key, value in data['sentences'].items()}
        assert {row['sentence_index']: row['sentence_destination'] for row in stored} == returned
        assert all(row['text_provenance'] == f'live:{provider}' for row in stored)
        assert all(value.strip() and value not in sources and '\n' not in value for value in returned.values())
        expected_words = deepcopy(before['words'])
        for word in expected_words:
            word['extra_fields']['SentenceDestination'] = returned[word['sentence_index']]
        assert after['words'] == expected_words
        cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths,
                                        db_path=server.resolved_paths['sqlite_db_path'])
        assert cold.db.get_session_bundle(target) == after
        assert cold.db.get_session_bundle(neighbor) == untouched
        (tmp_path / f'live-{provider}-proof.json').write_text(json.dumps({
            'provider': provider, 'http_status': status, 'sentences': returned,
            'provenance': data['text_provenance'], 'cold_readback_equal': True,
            'word_identity_and_lemma_values_unchanged': True,
            'word_sentence_context_matches_authoritative_translation': True,
            'neighbor_bundle_unchanged': True}, indent=2))


@pytest.mark.parametrize('engine', ['configured', 'Piper'])
@pytest.mark.parametrize('language', ['en', 'ru'])
def test_live_configured_synthesis_and_full_decode(tmp_path, engine, language):
    configured = live_config()
    origin = Path(os.environ['KARDENWORT_LIVE_CONFIG']).parent
    paths = {key: (origin / configured.get('environment', key)).resolve()
             for key in ('anki_tts_cli', 'kardenwort_python')}
    if engine == 'Piper':
        # Explicit alternate owned profile; the live CLI/configuration is untouched.
        original = paths['anki_tts_cli']
        profile = json.loads((original.parent / 'config.json').read_text(encoding='utf-8-sig'))
        profile['anki_addon_path'] = str((original.parent / profile['anki_addon_path']).resolve())
        profile.setdefault('overrides', {})['tts_engine'] = 'Piper'
        relocated = tmp_path / 'speech-cli.py'
        relocated.write_bytes(original.read_bytes())
        (tmp_path / 'config.json').write_text(json.dumps(profile))
        paths['anki_tts_cli'] = relocated
    server = create_browser_test_server(ControllerRequestHandler)
    server.api_key = 'owned-live-audio-token'
    server.config = configparser.ConfigParser()
    server.config.read_dict({'audio': {'synthesis_timeout': '30'}})
    server.resolved_paths = paths
    server.start()
    try:
        with post(f'http://127.0.0.1:{server.server_port}', '/api/v1/audio/synthesize',
                  {'text': {'en': 'A small red fox sleeps.', 'ru': 'Маленькая рыжая лиса спит.'}[language],
                   'language': language}, server.api_key) as response:
            assert response.status == 200, f'Synthesis HTTP {response.status}'
            content_type = response.headers.get_content_type()
            media = response.read()
    finally:
        server.server_close()
    from kardenwort_audio import media_type
    assert content_type == media_type(media)
    artifact = tmp_path / ('live-client-audio.wav' if content_type == 'audio/wav' else 'live-client-audio.mp3')
    artifact.write_bytes(media)
    decoder = shutil.which('ffmpeg')
    assert decoder, 'Full decoder unavailable'
    decoded = subprocess.run([decoder, '-v', 'error', '-i', str(artifact), '-f', 's16le',
                              '-acodec', 'pcm_s16le', '-'], capture_output=True, timeout=15)
    assert decoded.returncode == 0, 'Real media decoding failed'
    assert len(decoded.stdout) > 1600
    assert any(decoded.stdout), 'Decoded media is silent'
    (tmp_path / 'live-audio-proof.json').write_text(json.dumps({
        'engine_profile': engine, 'language': language,
        'mime': content_type, 'bytes': len(media), 'sha256': hashlib.sha256(media).hexdigest(),
        'decoded_pcm_bytes': len(decoded.stdout), 'decoded_nonzero': True,
        'decoder_exit_code': decoded.returncode, 'physical_listening': 'unexecuted'}, indent=2))
