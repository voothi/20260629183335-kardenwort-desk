"""Required article assets fail explicitly; desktop assets are never required."""
import configparser
import json
from pathlib import Path
import re
import shutil

import pytest

import kardenwort_desk as desk


@pytest.mark.parametrize('warm', [False, True])
@pytest.mark.parametrize('asset,enabled,disable_css,required', [
    ('article.html', False, False, True),
    ('article.css', False, False, True),
    ('article.css', True, True, False),
    ('article.js', False, False, False),
    ('article.js', True, False, True),
])
def test_article_asset_requirements_match_delivery_mode(tmp_path, monkeypatch, warm, asset, enabled, disable_css, required):
    source = Path(desk.__file__).parent / 'web' / 'dict'
    target = tmp_path / 'web' / 'dict'
    shutil.copytree(source, target)
    # A real isolated asset tree, deliberately containing no desktop assets.
    monkeypatch.setattr(desk, '__file__', str(tmp_path / 'kardenwort_desk.py'))
    config = configparser.ConfigParser()
    config.read_string('[settings]\nenable_performance_tracing=false\n')

    def render():
        return desk.render_lookup_html('Haus', 'de', 'en', config, {'results_dir': tmp_path},
            '20261028212349', dict(server_enabled=enabled, disable_css=disable_css,
            sections=['lemmas'], lemma_columns=['lemma']), [],
            ['WordSource', 'SentenceSourceIndex', 'TokenOrder'], [['Haus', '2', '0']], '',
            server_enabled=enabled, api_token='disposable-asset-token')

    if warm:
        assert render().startswith('<!DOCTYPE html>')
    (target / asset).unlink()
    if required:
        with pytest.raises(FileNotFoundError, match=asset.replace('.', r'\.')):
            render()
    else:
        assert 'Haus' in render()


@pytest.mark.parametrize('sentence_header', ['SentenceSourceIndex', 'SentenceIndex', 'sentence_index', 'sentence_idx'])
def test_combined_article_preserves_authoritative_identity_and_coordinate_aliases(tmp_path, sentence_header):
    config = configparser.ConfigParser()
    config.read_string('[settings]\nenable_performance_tracing=false\n')
    payload = json.loads(desk.render_lookup_combined(
        'Haus', 'de', 'en', config, {'results_dir': tmp_path}, '20261028214500',
        dict(server_enabled=True, sections=['lemmas'], lemma_columns=['lemma']), [],
        ['WordSource', sentence_header, 'token_order', 'DeskSelected'], [['Haus', '2', '0', '1']], '',
        session_zid='20261028212349', api_token='disposable-combined-token', fingerprint='verified-fingerprint'))
    metadata = json.loads(re.search(r'<script type="application/json" data-kw-article>(.*?)</script>', payload['html'], re.S)[1])
    assert metadata['session_zid'] == '20261028212349'
    assert metadata['fingerprint'] == 'verified-fingerprint'
    assert metadata['rows'][0]['targets'] == [dict(row_id=0, sentence_idx=2, token_order=0)]
    assert 'Haus' in payload['text']
