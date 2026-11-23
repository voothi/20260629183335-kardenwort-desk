"""Re-translating already-cached text must resolve to the real SQLite session ZID.

Reviewed alias security contract:
- Alias 302 Location remains relative/same-controller, preserves session_zid/seq_num/theme,
  excludes token/api_token, and sets credential security headers.
- Same-origin X-API-Token header clients and fresh Playwright browsers follow the 302 to 200.
- Metadata-free clients relying solely on an initial URL token reject 403 at the redirected URL,
  and cross-origin alias requests reject 403 without credential disclosure or ACAO reflection.
- Unknown session ZIDs retain 404 NOT_FOUND.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def test_retranslate_cached_text_returns_resolved_zid_and_redirects_alias(
    browser, running_controller, monkeypatch
):
    server_url, server, adapter = running_controller
    server.arbiter.storage_adapter = adapter
    monkeypatch.setattr(desk, "run_progressive_worker_async", lambda *args, **kwargs: None)

    cached_zid = "20261119230000"
    requested_zid = "20261119235530"
    text = "Das Haus ist groß."
    target_lang = server.config.get("settings", "default_target_language", fallback="ru")
    secret = server.api_key

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
            headers={"Content-Type": "application/json", "X-API-Token": secret},
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
        data = body.get("data", body)
        assert data["zid"] == cached_zid
        assert data["session_zid"] == cached_zid

        # 2. Opening the requested ZID redirects 302 to the real SQLite ZID without leaking any token.
        alias_path = f"/?session_zid={requested_zid}&seq_num=1&theme=dark"
        launch_url = f"{server_url}{alias_path}&token={urllib.parse.quote(secret)}"
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(
                urllib.request.Request(f"{server_url}{alias_path}", headers={"X-API-Token": secret}),
                timeout=10.0,
            )
            raise AssertionError("Expected 302 redirect from ephemeral alias ZID to cached SQLite ZID")
        except urllib.error.HTTPError as redirect_err:
            assert redirect_err.code == 302
            location = redirect_err.headers.get("Location", "")
            assert location.startswith("/?")
            assert f"session_zid={cached_zid}" in location
            assert "seq_num=1" in location
            assert "theme=dark" in location
            assert "token=" not in location and "api_token=" not in location
            assert (secret in location) is False
            assert redirect_err.headers.get("Cache-Control") == "no-store"
            assert redirect_err.headers.get("Referrer-Policy") == "no-referrer"
            assert redirect_err.headers.get("Content-Security-Policy") == "frame-ancestors 'none'"

        # 3a. Authenticated same-origin header client follows the redirect and renders 200 cleanly.
        target_req = urllib.request.Request(f"{server_url}{location}", headers={"X-API-Token": secret})
        with urllib.request.urlopen(target_req, timeout=10.0) as page_resp:
            assert page_resp.status == 200
            html = page_resp.read().decode("utf-8")
            assert f"session_zid={cached_zid}" in page_resp.geturl()
            assert cached_zid in html

        # 3b. Real fresh browser navigation follows the 302 without URL credentials and reaches ready.
        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(10000)
            nav_resp = page.goto(f"{server_url}{alias_path}", wait_until="domcontentloaded")
            assert nav_resp is not None and nav_resp.status == 200
            assert f"session_zid={cached_zid}" in page.url
            assert ("token=" in page.url) is False and (secret in page.url) is False
            expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
        finally:
            context.close()

        # 3c. Negative: metadata-free client relying only on an initial URL token rejects 403 at redirect target.
        try:
            urllib.request.urlopen(launch_url, timeout=10.0)
            raise AssertionError("Expected 403 when metadata-free client follows token-stripped redirect")
        except urllib.error.HTTPError as denied_err:
            assert denied_err.code == 403
            denied_body = denied_err.read().decode("utf-8", errors="replace")
            assert (secret in denied_body) is False

        # 3d. Negative: cross-origin alias request rejects 403 with no credential or ACAO reflection.
        for hostile_origin in ("http://127.0.0.1:59998", "https://evil.example"):
            xo_req = urllib.request.Request(
                f"{server_url}{alias_path}",
                headers={"Origin": hostile_origin},
            )
            try:
                opener.open(xo_req, timeout=10.0)
                raise AssertionError(f"Expected 403 for cross-origin alias request from {hostile_origin}")
            except urllib.error.HTTPError as xo_err:
                assert xo_err.code == 403
                assert xo_err.headers.get("Access-Control-Allow-Origin") != hostile_origin
                xo_body = xo_err.read().decode("utf-8", errors="replace")
                assert (secret in xo_body) is False

        # 4. Control: genuinely unknown session ZID still fails fast with 404 NOT_FOUND.
        unknown_req = urllib.request.Request(
            f"{server_url}/?session_zid=20990101000000",
            headers={"X-API-Token": secret},
        )
        try:
            urllib.request.urlopen(unknown_req, timeout=10.0)
            raise AssertionError("Expected 404 NOT_FOUND for unknown session ZID")
        except urllib.error.HTTPError as not_found:
            assert not_found.code == 404
    finally:
        try:
            adapter.delete_session(cached_zid)
        except Exception:
            pass
