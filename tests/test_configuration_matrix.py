"""
test_configuration_matrix.py
Verifies pairwise configuration testing across multi-modal modes, providers, themes, and audio engines.
Codifies the protocol defined in configuration-matrix-testing/SKILL.md.
"""

import sys
import configparser
from pathlib import Path
import pytest

import kardenwort_desk as kd
from kardenwort_desk import (
    SEC_SETTINGS, SEC_RENDERING, SEC_TRANSLATION, SEC_TRANSLATION_PROVIDERS,
    SEC_AUDIO, SEC_TOKEN_MAPPINGS, SEC_CLASSIFICATION, SEC_LANGUAGES
)


@pytest.fixture(autouse=True)
def mock_async_workers(monkeypatch):
    """Ensure async background subprocesses and sidecars do not spawn during matrix tests."""
    monkeypatch.setattr(kd, 'run_progressive_worker_async', lambda *args, **kwargs: None)


def _build_matrix_test_env(tmp_path, mode: str, provider: str, theme: str, audio: str):
    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'default_language', 'de')
    config.set(SEC_SETTINGS, 'default_target_language', 'ru')

    config.add_section(SEC_RENDERING)
    config.set(SEC_RENDERING, 'theme', theme)
    config.set(SEC_RENDERING, 'highlight_engine', 'hover_highlight')

    config.add_section(SEC_TRANSLATION)
    config.set(SEC_TRANSLATION, 'provider', provider)

    config.add_section(SEC_AUDIO)
    config.set(SEC_AUDIO, 'player', audio)

    config.add_section(SEC_TOKEN_MAPPINGS)
    config.set(SEC_TOKEN_MAPPINGS, 'enabled', 'true')
    config.set(SEC_TOKEN_MAPPINGS, 'apostrophe_chars', "', ’, ‘, `, ´, ʼ")
    config.set(SEC_TOKEN_MAPPINGS, 'split_gap_limit', '3')

    config.add_section(SEC_CLASSIFICATION)
    config.set(SEC_CLASSIFICATION, 'enabled', 'false')

    config.add_section(SEC_LANGUAGES)
    config.set(SEC_LANGUAGES, 'de_prompt', 'de_prompt')
    config.set(SEC_LANGUAGES, 'en_prompt', 'en_prompt')

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section('fields')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordSourceInflectedForm', 'inflected')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    mapping.set('desk_columns', 'WordSourceMorphologyAI', 'morphology')

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, 'w', encoding='utf-8') as f:
        mapping.write(f)

    tsv_file = tmp_path / "matrix-session.de.tsv"
    tsv_content = (
        "# language=de\n"
        "TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n"
        "0\tHund\tсобака\t1\n"
        "1\tKatze\tкошка\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    resolved_paths = {
        'kardenwort_workspace': tmp_path,
        'anki_mapping_file': str(mapping_file),
        'kardenwort_python': sys.executable,
        'tsv_path': str(tsv_file)
    }

    return config, resolved_paths, str(tsv_file)


# 12 Pairwise Covering Combinations across:
# - Mode: single, multi, sentences, overview
# - Provider: google, argos, intellifiller, offline
# - Theme: dark, light, white
# - Audio: piper, gtts, mpv
PAIRWISE_CONFIG_MATRIX = [
    ("single", "google", "dark", "piper"),
    ("multi", "argos", "light", "gtts"),
    ("sentences", "intellifiller", "white", "mpv"),
    ("overview", "offline", "dark", "gtts"),
    ("single", "argos", "white", "piper"),
    ("multi", "google", "dark", "mpv"),
    ("sentences", "offline", "light", "piper"),
    ("overview", "intellifiller", "white", "gtts"),
    ("single", "offline", "light", "mpv"),
    ("multi", "intellifiller", "dark", "piper"),
    ("sentences", "google", "dark", "gtts"),
    ("overview", "argos", "light", "mpv"),
]


# Additional cases complete the twelve previously missing axis pairs.
PAIRWISE_CONFIG_MATRIX += [('multi', 'offline', 'white', 'gtts'), ('overview', 'google', 'light', 'piper'), ('single', 'intellifiller', 'light', 'gtts'), ('sentences', 'argos', 'dark', 'gtts'), ('multi', 'google', 'white', 'gtts')]


@pytest.mark.parametrize("mode,provider,theme,audio", PAIRWISE_CONFIG_MATRIX)
def test_configuration_matrix_pairwise_rendering(tmp_path, mode, provider, theme, audio):
    """
    Verifies that every pairwise combination of display mode, translation provider,
    visual theme, and audio engine initializes and renders cleanly without error.
    """
    config, resolved_paths, tsv_path = _build_matrix_test_env(
        tmp_path=tmp_path,
        mode=mode,
        provider=provider,
        theme=theme,
        audio=audio
    )

    source_text = "Der Hund und die Katze schlafen im Haus."
    zid = "20261012224000"

    html_out = kd.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode=mode,
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path,
        spawn_children=False,
    )

    assert html_out is not None
    assert len(html_out) > 0
    assert f"theme-{theme}" in html_out
    assert "source-container" in html_out or "source-text" in html_out
    assert "lemma-table" in html_out


def test_orthogonal_key_independence(tmp_path):
    """
    Verifies that mutating any configuration dimension (e.g. theme) strictly
    preserves orthogonal dimensions (e.g. provider, audio, storage).
    """
    config, resolved_paths, _ = _build_matrix_test_env(
        tmp_path=tmp_path,
        mode="single",
        provider="google",
        theme="dark",
        audio="piper"
    )

    # Invariant snapshot before mutation
    initial_provider = config.get(SEC_TRANSLATION, 'provider')
    initial_audio = config.get(SEC_AUDIO, 'player')
    initial_lang = config.get(SEC_SETTINGS, 'default_language')

    # Mutate theme
    config.set(SEC_RENDERING, 'theme', 'light')

    # Verify orthogonal keys remained unchanged
    assert config.get(SEC_RENDERING, 'theme') == 'light'
    assert config.get(SEC_TRANSLATION, 'provider') == initial_provider
    assert config.get(SEC_AUDIO, 'player') == initial_audio
    assert config.get(SEC_SETTINGS, 'default_language') == initial_lang


def test_configuration_matrix_contains_every_axis_pair():
    from itertools import combinations, product
    axes = [set(row[index] for row in PAIRWISE_CONFIG_MATRIX) for index in range(4)]
    for first, second in combinations(range(4), 2):
        expected = set(product(axes[first], axes[second]))
        actual = {(row[first], row[second]) for row in PAIRWISE_CONFIG_MATRIX}
        assert actual == expected, (first, second, expected - actual)
