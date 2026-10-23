import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import kardenwort_desk as kd

def _island(html: str, island_id: str) -> str:
    pattern = r'<script id="' + re.escape(island_id) + r'" type="application/json">(.*?)</script>'
    matches = re.findall(pattern, html, flags=re.DOTALL)
    assert len(matches) == 1, f"expected exactly one #{island_id}, got {len(matches)}"
    return matches[0]

def test_session_data_breakout_blocked():
    payload = {"session_data": {"text": "a</script><b id=pwn>x</b>"}, "token_manifest": []}
    html = kd.render_declarative_web_shell(payload)
    assert "</script><b id=pwn>" not in html
    assert json.loads(_island(html, "session-data"))["text"] == "a</script><b id=pwn>x</b>"

def test_token_map_breakout_blocked():
    payload = {"session_data": {"text": "x"}, "token_manifest": [{"text": "</script><i id=pwn2>"}]}
    html = kd.render_declarative_web_shell(payload)
    assert "</script><i id=pwn2>" not in html
    assert json.loads(_island(html, "token-map")) == payload["token_manifest"]

def test_serialize_json_island_roundtrip():
    obj = {"a": "<>&\u2028\u2029", "ru": "прощание", "de": "Übersetzung"}
    out = kd.serialize_json_island(obj)
    assert "<" not in out
    assert ">" not in out
    assert "&" not in out
    assert "\u2028" not in out
    assert "\u2029" not in out
    assert json.loads(out) == obj
    assert "прощание" in out
    assert "Übersetzung" in out

def test_no_raw_json_dumps_for_islands():
    src = (ROOT / "kardenwort_desk.py").read_text(encoding="utf-8")
    assert re.findall(
        r'^\s*(session_json|token_manifest_json|bootstrap_json|ui_cfg_json|sentence_cards_json|mism_json|lookup_bootstrap_json)\s*=\s*json\.dumps\(',
        src,
        flags=re.MULTILINE
    ) == []
