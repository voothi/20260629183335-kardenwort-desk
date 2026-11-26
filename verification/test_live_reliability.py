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
    cfg_path = Path(os.environ['KARDENWORT_LIVE_CONFIG'])
    if not cfg_path.is_file():
        raise FileNotFoundError(f'Live verification config not found: {cfg_path}')
    config = configparser.ConfigParser(interpolation=None)
    config.read(cfg_path, encoding='utf-8-sig')
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


def donor_state_digest(original_cli_path: Path) -> dict:
    """Capture tamper-evident digest of live CLI config, donor addon config, and donor user_cache state."""
    cli_cfg_path = original_cli_path.parent / 'config.json'
    cli_cfg = json.loads(cli_cfg_path.read_text(encoding='utf-8-sig'))
    addon_dir = (original_cli_path.parent / cli_cfg['anki_addon_path']).resolve()
    donor_cfg_path = addon_dir / 'config.json'
    donor_cache = addon_dir / 'user_cache'
    cache_entries = []
    if donor_cache.is_dir():
        for entry in sorted(donor_cache.iterdir(), key=lambda p: p.name):
            st = entry.stat()
            cache_entries.append((entry.name, st.st_size, st.st_mtime_ns))
    state_hashes = {}
    for name in ('audio_cycle_state.json', 'tts_cycle_state.json'):
        p = donor_cache / name
        state_hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return {
        'cli_config_sha256': hashlib.sha256(cli_cfg_path.read_bytes()).hexdigest(),
        'donor_config_sha256': hashlib.sha256(donor_cfg_path.read_bytes()).hexdigest() if donor_cfg_path.is_file() else None,
        'cache_entry_count': len(cache_entries),
        'cache_inventory_sha256': hashlib.sha256(json.dumps(cache_entries).encode('utf-8')).hexdigest(),
        'cycle_state_hashes': state_hashes,
    }


def build_isolated_cli_profile(original_cli_path: Path, target_dir: Path, *, mode: str, pinned_engine: str | None = None, owned_cache_dir: Path | None = None, owned_forvo_dir: Path | None = None):
    """Create an owned relocated CLI + profile preserving readonly vendor/model paths while isolating all mutable state."""
    target_dir.mkdir(parents=True, exist_ok=True)
    cli_cfg = json.loads((original_cli_path.parent / 'config.json').read_text(encoding='utf-8-sig'))
    addon_dir = (original_cli_path.parent / cli_cfg['anki_addon_path']).resolve()
    donor_cfg = json.loads((addon_dir / 'config.json').read_text(encoding='utf-8-sig'))
    merged_overrides = dict(cli_cfg.get('overrides', {}))
    merged_overrides.pop('comments', None)
    effective_engine = pinned_engine or merged_overrides.get('tts_engine') or donor_cfg.get('tts_engine', 'gTTS')

    if mode == 'stateless':
        # Pin the exact single engine and explicitly prohibit fallback or donor cache/dictionary mutation.
        merged_overrides.update({
            'tts_engine': effective_engine,
            'gtts_enabled': effective_engine == 'gTTS',
            'piper_enabled': effective_engine == 'Piper',
            'tts_cycle_enabled': False,
            'audio_dictionary_enabled': False,
            'audio_dictionary_cycle_enabled': False,
            'persistent_cache_enabled': False,
            'persistent_cache_path': '',
        })
    elif mode == 'default_pipeline':
        assert owned_cache_dir is not None
        owned_cache_dir.mkdir(parents=True, exist_ok=True)
        merged_overrides.update({
            'persistent_cache_enabled': True,
            'persistent_cache_path': str(owned_cache_dir),
            'audio_dictionary_enabled': owned_forvo_dir is not None,
            'audio_dictionary_path': str(owned_forvo_dir) if owned_forvo_dir is not None else '',
        })
    else:
        raise ValueError(f'Unknown isolated profile mode: {mode}')

    relocated = target_dir / 'speech-cli.py'
    relocated.write_bytes(original_cli_path.read_bytes())
    (target_dir / 'config.json').write_text(
        json.dumps({'anki_addon_path': str(addon_dir), 'overrides': merged_overrides}, indent=2),
        encoding='utf-8',
    )
    return relocated, effective_engine


def _decode_and_verify_nonsilent(media: bytes, content_type: str, artifact_path: Path) -> bytes:
    from kardenwort_audio import media_type
    assert content_type == media_type(media)
    artifact_path.write_bytes(media)
    decoder = shutil.which('ffmpeg')
    assert decoder, 'Full decoder unavailable'
    decoded = subprocess.run([decoder, '-v', 'error', '-i', str(artifact_path), '-f', 's16le',
                              '-acodec', 'pcm_s16le', '-'], capture_output=True, timeout=15)
    assert decoded.returncode == 0, 'Real media decoding failed'
    assert len(decoded.stdout) > 1600
    assert any(decoded.stdout), 'Decoded media is silent'
    return decoded.stdout


@pytest.mark.parametrize('provider', [p.strip() for p in os.environ.get('KARDENWORT_LIVE_PROVIDERS', 'google').split(',') if p.strip()])
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
    """Stateless isolated engine check: pins playback_engine=stateless_gtts and exact CLI engine without fallback or donor writes."""
    configured = live_config()
    origin = Path(os.environ['KARDENWORT_LIVE_CONFIG']).parent
    original_cli = (origin / configured.get('environment', 'anki_tts_cli')).resolve()
    donor_before = donor_state_digest(original_cli)

    relocated, effective_engine = build_isolated_cli_profile(
        original_cli,
        tmp_path / 'stateless-profile',
        mode='stateless',
        pinned_engine='Piper' if engine == 'Piper' else None,
    )
    paths = {
        'anki_tts_cli': relocated,
        'kardenwort_python': (origin / configured.get('environment', 'kardenwort_python')).resolve(),
    }
    server = create_browser_test_server(ControllerRequestHandler)
    server.api_key = 'owned-live-audio-token'
    server.config = configparser.ConfigParser()
    server.config.read_dict({'audio': {'synthesis_timeout': '30', 'playback_engine': 'stateless_gtts'}})
    server.resolved_paths = paths
    server.start()
    try:
        with post(f'http://127.0.0.1:{server.server_port}', '/api/v1/audio/synthesize',
                  {'text': {'en': 'A small red fox sleeps.', 'ru': 'Маленькая рыжая лиса спит.'}[language],
                   'language': language}, server.api_key) as response:
            assert response.status == 200, f'Stateless {effective_engine} synthesis failed: HTTP {response.status}'
            content_type = response.headers.get_content_type()
            media = response.read()
    finally:
        server.server_close()

    expected_mime = 'audio/wav' if effective_engine == 'Piper' else 'audio/mpeg'
    assert content_type == expected_mime, f'Engine {effective_engine} expected {expected_mime}, got {content_type}'
    artifact = tmp_path / ('live-stateless.wav' if content_type == 'audio/wav' else 'live-stateless.mp3')
    decoded_pcm = _decode_and_verify_nonsilent(media, content_type, artifact)

    donor_after = donor_state_digest(original_cli)
    assert donor_after == donor_before, 'Live donor config or user_cache was modified during stateless check'

    (tmp_path / 'live-audio-proof.json').write_text(json.dumps({
        'pipeline_scope': 'stateless_pinned_engine',
        'requested_profile': engine,
        'pinned_engine': effective_engine,
        'language': language,
        'mime': content_type,
        'bytes': len(media),
        'sha256': hashlib.sha256(media).hexdigest(),
        'decoded_pcm_bytes': len(decoded_pcm),
        'decoded_nonzero': True,
        'donor_state_unchanged': True,
        'physical_listening': 'unexecuted'}, indent=2))


def test_live_default_anki_tts_cli_pipeline_isolated(tmp_path):
    """Separate default anki_tts_cli pipeline check using owned profile/cache/Forvo; live donor state remains untouched."""
    configured = live_config()
    origin = Path(os.environ['KARDENWORT_LIVE_CONFIG']).parent
    original_cli = (origin / configured.get('environment', 'anki_tts_cli')).resolve()
    donor_before = donor_state_digest(original_cli)

    owned_cache = tmp_path / 'owned-cache'
    owned_forvo = tmp_path / 'owned-forvo'
    relocated, effective_engine = build_isolated_cli_profile(
        original_cli,
        tmp_path / 'default-profile',
        mode='default_pipeline',
        owned_cache_dir=owned_cache,
        owned_forvo_dir=owned_forvo,
    )
    paths = {
        'anki_tts_cli': relocated,
        'kardenwort_python': (origin / configured.get('environment', 'kardenwort_python')).resolve(),
    }
    server = create_browser_test_server(ControllerRequestHandler)
    server.api_key = 'owned-live-default-token'
    server.config = configparser.ConfigParser()
    server.config.read_dict({'audio': {'synthesis_timeout': '30', 'playback_engine': 'anki_tts_cli'}})
    server.resolved_paths = paths
    server.start()
    try:
        with post(f'http://127.0.0.1:{server.server_port}', '/api/v1/audio/synthesize',
                  {'text': 'A small red fox sleeps.', 'language': 'en'}, server.api_key) as response:
            assert response.status == 200, f'Default pipeline synthesis failed: HTTP {response.status}'
            content_type = response.headers.get_content_type()
            media = response.read()
    finally:
        server.server_close()

    artifact = tmp_path / ('live-default.wav' if content_type == 'audio/wav' else 'live-default.mp3')
    decoded_pcm = _decode_and_verify_nonsilent(media, content_type, artifact)

    donor_after = donor_state_digest(original_cli)
    assert donor_after == donor_before, 'Live donor config or user_cache was modified during isolated default pipeline check'
    assert any(owned_cache.iterdir()), 'Expected owned isolated cache to record default pipeline output/state'

    (tmp_path / 'live-default-pipeline-proof.json').write_text(json.dumps({
        'pipeline_scope': 'default_anki_tts_cli_isolated',
        'configured_default_engine': effective_engine,
        'mime': content_type,
        'bytes': len(media),
        'sha256': hashlib.sha256(media).hexdigest(),
        'decoded_pcm_bytes': len(decoded_pcm),
        'decoded_nonzero': True,
        'owned_cache_populated': True,
        'donor_state_unchanged': True,
        'physical_listening': 'unexecuted'}, indent=2))
