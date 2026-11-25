"""Phase 3 contracts for config.ini.template parity, .gitignore hygiene, and personal config preservation.

Change: 20261122213502-harden-audio-runtime-tts-cli-and-repo-hygiene (Tasks 3.1, 3.5)
Spec: specs/workspace-artifact-maintenance/spec.md
"""
from __future__ import annotations

import configparser
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_3_1_template_documents_supported_audio_and_storage_keys_with_deliberate_defaults():
    """config.ini.template must parse cleanly and document rmb_source, synthesis_timeout, playback_engine, and storage keys."""
    tpl = configparser.ConfigParser(interpolation=None)
    tpl.read(ROOT / "config.ini.template", encoding="utf-8-sig")

    assert tpl.has_section("audio")
    assert tpl.get("audio", "playback_engine") == "anki_tts_cli"
    assert tpl.getfloat("audio", "synthesis_timeout") == 30.0
    assert tpl.get("audio", "rmb_source") == "word_translation"
    assert tpl.get("audio", "lmb_source") == "lemma"
    assert tpl.get("audio", "lmb_chain_mode") == "joined"
    assert tpl.get("audio", "rmb_chain_mode") == "separate"
    assert tpl.get("audio", "source_range_mode") == "all"
    assert tpl.get("audio", "table_range_mode") == "none"

    assert tpl.has_section("storage")
    assert tpl.get("storage", "backend") == "sqlite"
    assert tpl.get("storage", "sqlite_db_path") == "data/kardenwort.db"
    assert tpl.getint("storage", "cache_ttl_seconds") == 86400
    assert tpl.getboolean("storage", "fallback_to_tsv") is True

    # Secret placeholder ownership stays scrubbed (coordinated with 20261122213459).
    assert tpl.has_section("server")
    assert tpl.get("server", "api_key").strip() == ""


def test_3_1_gitignore_has_valid_line_termination_and_blocks_targeted_artifacts():
    """Ignore rules must end with a newline, stop reintroduction of targeted backup/scratch files, and not ban test fixtures."""
    raw = (ROOT / ".gitignore").read_bytes()
    assert raw.endswith(b"\n"), ".gitignore must end with a trailing newline"

    for ignored_path in (
        "data/kardenwort.db.before-preferences.sqlite",
        "tmp/20261117222339/lemma_identity_probe.py",
        "tmp/20261117231649/canonical-validation.json",
    ):
        res = subprocess.run(
            ["git", "check-ignore", "--no-index", ignored_path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        assert res.returncode == 0, f"Expected .gitignore to ignore {ignored_path}"

    # Do not blanket-ban legitimate fixture assets under tests/fixtures/.
    fixture_res = subprocess.run(
        ["git", "check-ignore", "--no-index", "tests/fixtures/silence.mp3"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    assert fixture_res.returncode != 0, ".gitignore must not ignore tests/fixtures/silence.mp3"
