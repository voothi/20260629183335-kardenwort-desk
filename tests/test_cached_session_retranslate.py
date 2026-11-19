"""Re-translating already-cached text must resolve to the real SQLite session ZID."""
import json
import urllib.error
import urllib.request

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def test_retranslate_cached_text_returns_resolved_zid_and_redirects_alias(running_controller, monkeypatch):
    server_url, server, adapter = running_controller
    server.arbiter.storage_adapter = adapter
    monkeypatch.setattr(desk, "run_progressive_worker_async", lambda *args, **kwargs: None)

    cached_zid = "20261119230000"
    requested_zid = "20261119235530"
    text = "Das Haus ist groß."
    target_lang = server.config.get("settings", "default_target_language", fallback="ru")

    adapter.save_session(
        session_zid=cached_zid,
        slug="das-haus-ist-gross",
        source_language="de",
        target_language=target_lang,
        text_mode="single",
        source_raw_text=text,
        headers=["Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination", "SentenceSourceIndex", "DeskSelected"],
        data_rows=[["Haus", "Haus", "дом", text, "Дом большой.", "1", "0"]],
        sentences=[{
            "session_zid": cached_zid,
            "sentence_index": 1,
            "sentence_source": text,
            "sentence_destination": "Дом большой.",
        }],
        zid=cached_zid,
    )

    try:
        # 1. AutoHotkey-style POST /api/v1/render with a fresh ZID for already-cached text.
        payload = json.dumps({
            "session_zid": requested_zid,
            "zid": requested_zid,
            "language": "de",
            "text_mode": "single",
            "text": text,
            "bypass_lang_check": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{server_url}/api/v1/render",
            data=payload,
            headers={"Content-Type": "application/json", "X-API-Token": server.api_key},
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
        data = body.get("data", body)
        assert data["zid"] == cached_zid
        assert data["session_zid"] == cached_zid

        # 2. Opening the requested ZID via browser launch URL redirects 302 to the real SQLite ZID.
        launch_url = f"{server_url}/?session_zid={requested_zid}&seq_num=1&theme=dark&token={server.api_key}"
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(launch_url, timeout=10.0)
            raise AssertionError("Expected 302 redirect from ephemeral alias ZID to cached SQLite ZID")
        except urllib.error.HTTPError as redirect_err:
            assert redirect_err.code == 302
            location = redirect_err.headers.get("Location", "")
            assert f"session_zid={cached_zid}" in location
            assert "seq_num=1" in location
            assert "theme=dark" in location

        # 3. Following the redirect renders the cached session cleanly with HTTP 200.
        with urllib.request.urlopen(launch_url, timeout=10.0) as page_resp:
            assert page_resp.status == 200
            html = page_resp.read().decode("utf-8")
            assert f"session_zid={cached_zid}" in page_resp.geturl()
            assert cached_zid in html

        # 4. Control: genuinely unknown session ZID still fails fast with 404 NOT_FOUND.
        unknown_url = f"{server_url}/?session_zid=20990101000000&token={server.api_key}"
        try:
            urllib.request.urlopen(unknown_url, timeout=10.0)
            raise AssertionError("Expected 404 NOT_FOUND for unknown session ZID")
        except urllib.error.HTTPError as not_found:
            assert not_found.code == 404
    finally:
        try:
            adapter.delete_session(cached_zid)
        except Exception:
            pass
