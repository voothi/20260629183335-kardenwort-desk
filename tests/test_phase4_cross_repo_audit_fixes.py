"""Regression contracts for Phase 4 cross-repo anki-tts-cli, audit script, and live verification fixes."""
import importlib.util
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import pytest

from scripts import audit_progressive_worker as audit_worker
from verification import test_live_reliability as live_rel


ROOT = Path(__file__).resolve().parent.parent
CLI_PATH = ROOT.parent / "20260119103526-anki-tts-cli" / "anki-tts-cli.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("anki_tts_cli_phase4", CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_anki_tts_cli_tls_verify_enabled_and_dead_helpers_removed():
    src = CLI_PATH.read_text(encoding="utf-8")
    assert "verify=False" not in src
    assert "verify=True" in src
    assert 'warnings.simplefilter("ignore")' not in src
    assert "def load_state(" not in src
    assert "def save_state(" not in src


def test_anki_tts_cli_decode_gtts_line_non_greedy():
    mod = _load_cli_module()
    out = bytearray()
    # "AQID" decodes to b"\x01\x02\x03"; trailing quote-bracket must not greedily capture extra chars
    line = b'[["wrb.fr","jQ1olc","[\\"AQID\\"]",null,null,null,"extra\\"]"]]'
    mod._decode_gtts_line(line, out)
    assert bytes(out) == b"\x01\x02\x03"


def test_anki_tts_cli_stateless_gtts_enforces_hard_timeout(tmp_path):
    mod = _load_cli_module()
    release = threading.Event()

    class HangingGtts:
        def __init__(self, *args, **kwargs):
            pass

        def write_to_fp(self, fp):
            release.wait(2.0)

    out_file = tmp_path / "out.mp3"
    t0 = time.monotonic()
    try:
        with patch.object(mod, "gTTS", HangingGtts):
            with pytest.raises(TimeoutError, match="gTTS timed out"):
                mod.stateless_gtts("Haus", "de", {"gtts_enabled": True, "gtts_timeout_sec": 0.2}, str(out_file))
    finally:
        release.set()
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5
    assert not out_file.exists()


def test_anki_tts_cli_run_piper_tts_enforces_timeout(tmp_path):
    mod = _load_cli_module()
    fake_piper = tmp_path / "piper.py"
    fake_piper.write_text("import time; time.sleep(5)", encoding="utf-8")
    conf = {
        "piper_executable": sys.executable,
        "piper_script": str(fake_piper),
        "piper_timeout_sec": 0.25,
    }
    out_file = tmp_path / "piper.wav"
    t0 = time.monotonic()
    ok = mod.run_piper_tts("Haus", "de", str(out_file), conf)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed < 2.0
    assert not out_file.exists()


def test_anki_tts_cli_stateless_rejects_multi_segment(tmp_path):
    mod = _load_cli_module()
    parser = mod._build_parser()
    args = parser.parse_args(["--no-play", "--stateless", "--output", str(tmp_path / "x.mp3"), "--", "a ||| b", "de"])
    with patch.object(mod, "load_config", return_value={}):
        with pytest.raises(ValueError, match="one segment"):
            mod._run_main(args, parser)


def test_audit_worker_handles_equals_form_arguments_and_live_config_fails_fast(tmp_path, monkeypatch):
    args = ["progressive-worker", "--tsv=foo.tsv", "--zid=20261126200001"]
    assert audit_worker.has_argument(args, "--zid") is True
    assert audit_worker.argument(args, "--zid") == "20261126200001"
    assert audit_worker.argument(args, "--tsv") == "foo.tsv"

    missing = tmp_path / "nonexistent.ini"
    monkeypatch.setenv("KARDENWORT_LIVE_CONFIG", str(missing))
    with pytest.raises(FileNotFoundError):
        live_rel.live_config()
