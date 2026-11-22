"""Phase 3 independent escaping gate tests (Tasks 3.1 & 3.2).

Independently reproduces and verifies each of the five escaping sink groups
from `20261119102725-harden-render-auth-and-html-escaping`:
  1. Multiline ASS/non-word tokens in source rendering
  2. Lookup source text in render_section('source') and render_lookup_html
  3. Theme attributes in both shells (render_declarative_web_shell & render_lookup_html)
  4. Verification fields, identifiers, and mismatch parameters in render_verify_language_html
  5. Controller credential bootstrap across /session/render, /api/v1/render, /api/v1/lookup, /verify-language

Each sink is tested independently with quotes, backslashes, HTML tags, and
closing-script payloads. Assertions check inert DOM, exact decoded owned data,
and adjacent normal rendering without printing credentials.
"""
import base64
import json
import re
import urllib.error
import urllib.request

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401


HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "WordDestination",
    "DeskSelected",
]


def _extract_json_island(html_text, script_id):
    pattern = rf'<script\s+id="{re.escape(script_id)}"\s+type="application/json">(.*?)</script>'
    match = re.search(pattern, html_text, flags=re.DOTALL)
    assert match is not None, f"Missing JSON island #{script_id}"
    return json.loads(match.group(1).strip() or "{}")


def _http_request(url, *, method="GET", headers=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", errors="replace")


def _offline_config_and_paths(tmp_path):
    config, paths, goldendict, _ = desk.load_config()
    config.set("storage", "backend", "tsv")
    config.set("rendering", "display_mode", "monolithic")
    config.set("sentences_mode", "enabled", "false")
    config.set("wordfill", "enabled", "false")
    paths = dict(paths)
    paths.update(
        storage_backend="tsv",
        sqlite_db_path=tmp_path / "escape_sinks.db",
        results_dir=tmp_path,
        generated_results_dir=tmp_path,
    )
    goldendict = dict(goldendict)
    goldendict["server_enabled"] = False
    goldendict["server_api_key"] = ""
    goldendict["sections"] = ["source", "translation", "lemmas"]
    return config, paths, goldendict


# ---------------------------------------------------------------------------
# Sink 1: Multiline ASS / non-word tokens
# ---------------------------------------------------------------------------


def test_3_1_sink1_multiline_ass_nonword_token_is_inert_and_exact(browser, tmp_path):
    """Sink 1: Multiline ASS/non-word token containing quotes, backslash, HTML, and </script> stays inert."""
    config, paths, _ = _offline_config_and_paths(tmp_path)
    ass_payload = '{\\i1\n"<img src=x onerror=window.__xss=1>\'\\</script>}'
    source_text = f"Das Haus {ass_payload} ist alt."
    zid = "20261119103101"
    tsv = tmp_path / f"{zid}-ass.de.tsv"
    tsv.write_text(
        "\t".join(HEADERS) + "\n" + "\t".join(["0", "Haus", "Haus", "1", source_text, "house", "house", "0"]) + "\n",
        encoding="utf-8",
    )

    html_out = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        tsv_path=str(tsv),
        spawn_children=False,
    )

    assert "<img src=x onerror=window.__xss=1>" not in html_out, (
        "Multiline ASS/non-word token must not emit unescaped HTML tags"
    )

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_content(html_out, wait_until="domcontentloaded")
        assert page.evaluate("() => Boolean(window.__xss)") is False
        assert page.locator("img[src='x']").count() == 0
        src_text_rendered = page.locator("#source-container").inner_text()
        assert "Das" in src_text_rendered and "Haus" in src_text_rendered and "alt" in src_text_rendered
        assert '<img src=x onerror=window.__xss=1>' in src_text_rendered
        assert "</script>" in src_text_rendered
        assert "\\" in src_text_rendered
        assert page.locator("#source-container .word", has_text="Haus").count() >= 1
        session_island = _extract_json_island(html_out, "session-data")
        assert session_island.get("source_text") == source_text
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Sink 2: Lookup source (render_section + render_lookup_html)
# ---------------------------------------------------------------------------


def test_3_1_sink2_lookup_source_is_inert_and_consistent_with_translation(browser, tmp_path):
    """Sink 2: Lookup source must escape HTML/quotes/backslashes/</script> consistently with translation."""
    config, paths, goldendict = _offline_config_and_paths(tmp_path)
    source_payload = 'Das Haus "<img src=x onerror=window.__xss=1>\'\\</script> ist alt.'
    trans_payload = 'The house "<img src=y onerror=window.__xss=2>\'\\</script> is old.'
    zid = "20261119103102"
    rows = [["0", "Haus", "Haus", "1", source_payload, trans_payload, "house", "0"]]

    # 1. Direct render_section('source', ctx)
    sec_ctx = {
        "text": source_payload,
        "sentence_translation": trans_payload,
        "headers": HEADERS,
        "data_rows": rows,
        "language": "de",
        "target_lang": "ru",
        "headings": {"source": "Source Text", "translation": "Translation"},
    }
    source_section_html = desk.render_section("source", sec_ctx)
    assert "<img src=x onerror=window.__xss=1>" not in source_section_html
    assert "&lt;img src=x onerror=window.__xss=1&gt;" in source_section_html

    # 2. Full render_lookup_html shell
    html_out = desk.render_lookup_html(
        text=source_payload,
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=paths,
        zid=zid,
        goldendict=goldendict,
        comments=[],
        headers=HEADERS,
        data_rows=rows,
        sentence_translation=trans_payload,
        session_zid=zid,
        api_token="",
        server_enabled=False,
    )
    assert "<img src=x onerror=window.__xss=1>" not in html_out

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_content(html_out, wait_until="domcontentloaded")
        assert page.evaluate("() => Boolean(window.__xss)") is False
        assert page.locator("img[src='x']").count() == 0
        assert page.locator("img[src='y']").count() == 0
        assert page.locator("#source-container").inner_text().strip() == source_payload
        assert page.locator("#translation-container").inner_text().strip() == trans_payload
        assert _extract_json_island(html_out, "session-data").get("source_text") == source_payload
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Sink 3: Theme attributes in both shells
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_theme",
    [
        'dark"><img src=x onerror=window.__xss=1><span data-t="\'\\</script>',
        'dark" data-injected="1',
    ],
)
def test_3_1_sink3_theme_attributes_validated_and_escaped_in_both_shells(browser, tmp_path, bad_theme):
    """Sink 3: Both declarative web shell and lookup HTML shell must validate and escape theme attributes."""
    config, paths, goldendict = _offline_config_and_paths(tmp_path)
    zid = "20261119103103"
    rows = [["0", "Haus", "Haus", "1", "Das Haus.", "house", "house", "0"]]

    # Shell 1: render_declarative_web_shell
    bootstrap_payload = {
        "ui_config": {},
        "session_data": {
            "zid": zid,
            "session_zid": zid,
            "language": "de",
            "target_lang": "ru",
            "source_text": "Das Haus.",
            "sentence_translation": "house",
            "headers": HEADERS,
            "data_rows": rows,
            "server_enabled": False,
            "api_token": "",
        },
        "source_text": "Das Haus.",
        "sentence_translation": "house",
        "theme": bad_theme,
        "zid": zid,
        "session_zid": zid,
        "language": "de",
        "target_language": "ru",
    }
    shell1_html = desk.render_declarative_web_shell(
        bootstrap_payload=bootstrap_payload,
        theme=bad_theme,
        source_html='<span class="word">Das</span> <span class="word">Haus</span>.',
        sentence_html="house",
    )

    # Shell 2: render_lookup_html
    gd_bad = dict(goldendict)
    gd_bad["theme"] = bad_theme
    shell2_html = desk.render_lookup_html(
        text="Das Haus.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=paths,
        zid=zid,
        goldendict=gd_bad,
        comments=[],
        headers=HEADERS,
        data_rows=rows,
        sentence_translation="house",
        session_zid=zid,
        api_token="",
        server_enabled=False,
    )

    for shell_name, html_doc in (("declarative_web_shell", shell1_html), ("lookup_html_shell", shell2_html)):
        assert "<img src=x onerror=window.__xss=1>" not in html_doc, f"[{shell_name}] unescaped tag from theme"
        assert 'data-injected="1"' not in html_doc, f"[{shell_name}] attribute breakout from theme"

        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_content(html_doc, wait_until="domcontentloaded")
            assert page.evaluate("() => Boolean(window.__xss)") is False, f"[{shell_name}] XSS executed"
            assert page.locator("img[src='x']").count() == 0, f"[{shell_name}] injected img present"
            assert page.evaluate("() => document.body.getAttribute('data-injected')") is None, (
                f"[{shell_name}] injected attribute present on <body>"
            )
            body_class = page.evaluate("() => document.body.className")
            assert any(
                cls in ("theme-dark", "theme-light", "theme-white", "theme-compact")
                for cls in body_class.split()
            ), f"[{shell_name}] expected supported theme class, got {body_class!r}"
        finally:
            context.close()

    # Adjacent normal rendering control for all supported themes
    for valid_theme in ("dark", "light", "white", "compact"):
        s1_ok = desk.render_declarative_web_shell(bootstrap_payload=bootstrap_payload, theme=valid_theme)
        assert f'<body class="theme-{valid_theme}">' in s1_ok
        gd_ok = dict(goldendict)
        gd_ok["theme"] = valid_theme
        s2_ok = desk.render_lookup_html(
            text="Das Haus.", language="de", target_lang="ru", config=config, resolved_paths=paths,
            zid=zid, goldendict=gd_ok, comments=[], headers=HEADERS, data_rows=rows,
            sentence_translation="house", session_zid=zid, api_token="", server_enabled=False,
        )
        assert f'<body class="theme-{valid_theme}">' in s2_ok


# ---------------------------------------------------------------------------
# Sink 4: Verification fields, identifiers, and mismatch parameters
# ---------------------------------------------------------------------------


def test_3_1_sink4_verify_language_parameters_and_fields_are_inert_and_exact(browser):
    """Sink 4: render_verify_language_html must safely serialize JSON fields and escape HTML text slots."""
    payload_zid = '20261119"</script><img src=x onerror=window.__xss=1>\\\''
    det_code = 'xx"</script><img src=y onerror=window.__xss=2>\\\''
    exp_code = 'en"</script><img src=z onerror=window.__xss=3>\\\''
    det_name = 'CustomLang <img src=w onerror=window.__xss=4> "\'\\'
    exp_name = 'English <img src=v onerror=window.__xss=5> "\'\\'

    html_out = desk.render_verify_language_html(
        mismatch_info={
            "is_mismatch": True,
            "session_zid": payload_zid,
            "detected_language": det_code,
            "expected_language": exp_code,
            "detected_name": det_name,
            "expected_name": exp_name,
        },
        theme="dark",
        api_token="",
        session_zid=payload_zid,
    )

    assert "<img src=x onerror=window.__xss=1>" not in html_out
    assert "<img src=y onerror=window.__xss=2>" not in html_out
    assert "<img src=z onerror=window.__xss=3>" not in html_out
    assert "<img src=w onerror=window.__xss=4>" not in html_out
    assert "<img src=v onerror=window.__xss=5>" not in html_out

    context = browser.new_context()
    try:
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.set_content(html_out, wait_until="domcontentloaded")

        assert page.evaluate("() => Boolean(window.__xss)") is False
        assert page.locator("img").count() == 0
        assert page_errors == [], f"Unexpected script/parse errors: {page_errors}"

        # Exact decoded runtime identifiers from safely serialized JSON island
        decoded = page.evaluate("() => ({ zid: sessionZid, det: detCode, exp: expCode })")
        assert decoded["zid"] == payload_zid
        assert decoded["det"] == det_code
        assert decoded["exp"] == exp_code

        # Exact visible literal text in HTML body and <option>
        body_text = page.locator("#kw-lang-modal-body").inner_text()
        assert det_name in body_text and exp_name in body_text
        und_opt_text = page.locator('#kw-verify-lang-select option[value="und"]').inner_text()
        assert det_name in und_opt_text and det_code in und_opt_text
    finally:
        context.close()

    # Adjacent normal rendering control
    normal_html = desk.render_verify_language_html(
        mismatch_info={
            "is_mismatch": True,
            "session_zid": "20261119103199",
            "detected_language": "de",
            "expected_language": "en",
            "detected_name": "German",
            "expected_name": "English",
        },
        theme="light",
        api_token="",
        session_zid="20261119103199",
    )
    assert '<body class="theme-light">' in normal_html
    assert "German (de)" in normal_html and "English (en)" in normal_html


# ---------------------------------------------------------------------------
# Sink 5: Controller credential bootstrap across all branches
# ---------------------------------------------------------------------------


def test_3_1_sink5_controller_credential_bootstrap_is_inert_and_exact(
    browser, tmp_path, running_controller
):
    """Sink 5: Special-char controller key stays inert and exact across /session/render, /api/v1/render, /api/v1/lookup, /verify-language."""
    server_url, server, adapter = running_controller
    special_key = 'owned-sink5-key-"\\\'</script><img src=x onerror=window.__xss=1>'
    server.api_key = special_key
    server.config.set("server", "api_key", special_key)
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "false")
    gd = dict(server.goldendict) if getattr(server, "goldendict", None) else {}
    gd["server_enabled"] = True
    gd["server_api_key"] = special_key
    server.goldendict = gd

    zid = "20261119103105"
    draft_zid = "20261119103106"
    adapter.save_session(
        session_zid=zid,
        slug="sink5-bootstrap",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus ist alt.",
        headers=HEADERS,
        data_rows=[["0", "Haus", "Haus", "1", "Das Haus ist alt.", "house", "house", "0"]],
        sentences=[{
            "session_zid": zid,
            "sentence_index": 1,
            "sentence_source": "Das Haus ist alt.",
            "sentence_destination": "house",
            "sentence_destination2": "house",
            "text_provenance": "live:test",
        }],
    )
    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[draft_zid] = {
            "text": "Das Haus ist alt.",
            "language": "en",
            "text_mode": "single",
            "theme": "dark",
            "zoom": "100",
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": "de",
                "expected_language": "en",
                "detected_name": "German",
                "expected_name": "English",
                "session_zid": draft_zid,
            },
        }

    try:
        # 1. Fresh browser GET /session/render
        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            resp = page.goto(f"{server_url}/session/render?session_zid={zid}", wait_until="domcontentloaded")
            assert resp is not None and resp.status == 200
            raw_html = resp.text()
            assert ("<script>window.API_TOKEN" in raw_html) is False
            assert ("</script><img src=x" in raw_html) is False
            expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
            assert page.evaluate("() => Boolean(window.__xss)") is False
            assert page.locator("img[src='x']").count() == 0
            assert (page.evaluate("() => window.getApiToken()") == special_key) is True
            assert errors == []
        finally:
            context.close()

        # 2. Fresh browser GET /verify-language
        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            resp = page.goto(f"{server_url}/verify-language?session_zid={draft_zid}&theme=dark", wait_until="domcontentloaded")
            assert resp is not None and resp.status == 200
            raw_html = resp.text()
            assert ('const apiToken = "' in raw_html) is False
            assert ("</script><img src=x" in raw_html) is False
            assert page.evaluate("() => Boolean(window.__xss)") is False
            assert page.locator("img[src='x']").count() == 0
            assert (page.evaluate("() => apiToken") == special_key) is True
            assert errors == []
        finally:
            context.close()

        # 3. Authenticated POST /api/v1/render (HTML & HTML-base64)
        st_r, _, body_r = _http_request(
            f"{server_url}/api/v1/render",
            method="POST",
            headers={"X-API-Token": special_key},
            body={"session_zid": zid, "language": "de"},
        )
        assert st_r == 200
        data_r = json.loads(body_r)["data"]
        html_r = data_r["html"]
        assert html_r == base64.b64decode(data_r["html_b64"]).decode("utf-8")
        assert ("<script>window.API_TOKEN" in html_r) is False
        assert ("</script><img src=x" in html_r) is False
        assert (_extract_json_island(html_r, "session-data").get("api_token") == special_key) is True

        # 4. Authenticated GET /api/v1/lookup
        st_l, _, body_l = _http_request(
            f"{server_url}/api/v1/lookup?session_zid={zid}&language=de",
            method="GET",
            headers={"X-API-Token": special_key},
        )
        assert st_l == 200
        html_l = json.loads(body_l)["data"]["html"]
        assert ("<script>window.API_TOKEN" in html_l) is False
        assert ("</script><img src=x" in html_l) is False
        assert (_extract_json_island(html_l, "session-data").get("api_token") == special_key) is True
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(draft_zid, None)
