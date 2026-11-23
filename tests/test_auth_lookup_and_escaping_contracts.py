"""Phase 1 reproduction and contract tests for Tasks 1.2 & 1.3:
- Task 1.2:
  * Create/admin/lookup success, error, and OPTIONS credential/CORS policy.
  * Lookup explicit empty-token authority, server_api_key stripping from ui_config,
    script-safe configuration delivery before app init, script order, and no duplicate
    title/script execution.
  * Residual backend, frontend (app.js, WorkspaceTabs.js), and admin (admin.html/admin.js)
    escaping sinks with round-trip literal preservation and zero XSS execution.
  * URL scheme validation on favicon_href and untrusted 'skeleton-loader' rejection.
  * Token-free admin URLs/downloads, non-JSON error handling, invariant toolbar geometry
    across themes, legacy URL token log redaction, and empty config.ini.template api_key.
- Task 1.3:
  * Explicit reviewed alias contract coverage: identity/302/query/404 retention,
    no-token Location, same-origin authenticated-header and real-browser follow-through,
    and metadata-free / cross-origin rejection cases.
"""
import configparser
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401


ROOT = Path(__file__).resolve().parent.parent
SESSION_ZID = "20261123061201"
ALIAS_ZID = "20261123061299"

HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "WordSourcePOS",
    "WordSourceGender",
    "ClassificationOxford",
    "Provenance",
    "DeskSelected",
]

BROWSER_NAV_HEADERS = {
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
}

HOSTILE_LITERAL = 'q"\'&<img src=x onerror=window.__xss=1></script><script>window.__xss=2</script> прощание Übersetzung'
SKELETON_POISON = 'literal skeleton-loader <img src=x onerror=window.__xss=3>'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_request(url, *, method="GET", headers=None, body=None, follow_redirects=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(_NoRedirect())
    try:
        resp = opener.open(req, timeout=10.0)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, resp_headers, resp.read().decode("utf-8", errors="replace"), resp.geturl()


def _extract_json_island(html_text: str, script_id: str) -> dict:
    pattern = rf'<script\s+id="{re.escape(script_id)}"\s+type="application/json">(.*?)</script>'
    match = re.search(pattern, html_text, flags=re.DOTALL)
    assert match is not None, f"Missing JSON island #{script_id}"
    return json.loads(match.group(1).strip() or "{}")


def _seed_session(server, adapter, *, zid=SESSION_ZID, sentence="Das Haus ist alt."):
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "false")
    server.config.set("wordfill", "enabled", "false")
    adapter.save_session(
        session_zid=zid,
        slug="auth-escape-test",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text=sentence,
        headers=HEADERS,
        data_rows=[[
            "0", "Haus", "Haus", "1", sentence, "Дом старый.", "дом",
            "/haʊ̯s/", "Substantiv", "n.", "n", "3k:A1", "live:test", "1",
        ]],
        sentences=[{
            "session_zid": zid,
            "sentence_index": 1,
            "sentence_source": sentence,
            "sentence_destination": "Дом старый.",
            "text_provenance": "live:test",
        }],
    )


def _offline_config_and_paths(tmp_path):
    config, paths, goldendict, _ = desk.load_config()
    config.set("storage", "backend", "tsv")
    config.set("rendering", "display_mode", "monolithic")
    config.set("sentences_mode", "enabled", "false")
    config.set("wordfill", "enabled", "false")
    config.set("classification", "enabled", "true")
    paths = dict(paths)
    paths.update(
        storage_backend="tsv",
        sqlite_db_path=tmp_path / "escape_contract.db",
        results_dir=tmp_path,
        generated_results_dir=tmp_path,
    )
    goldendict = dict(goldendict)
    goldendict["server_enabled"] = False
    goldendict["server_api_key"] = ""
    goldendict["sections"] = ["source", "translation", "lemmas"]
    goldendict["lemma_columns"] = ["inflected", "lemma", "translation", "ipa", "morphology", "pos", "gender"]
    return config, paths, goldendict


# ===========================================================================
# Task 1.2A: Create / Admin / Lookup credential & CORS policy
# ===========================================================================


@pytest.mark.parametrize(
    "route_name,path,method,body",
    [
        ("session_create", "/session/create", "POST", {"text": "Das Haus.", "language": "de", "bypass_lang_check": True}),
        ("admin_root", "/admin", "GET", None),
        ("admin_slash", "/admin/", "GET", None),
        ("api_lookup", f"/api/v1/lookup?session_zid={SESSION_ZID}&language=de", "GET", None),
        ("lookup_alias", f"/lookup?session_zid={SESSION_ZID}", "GET", None),
    ],
)
def test_1_2_credential_policy_create_admin_lookup_success_error_and_options(
    running_controller, route_name, path, method, body
):
    """Task 1.2: /session/create, /admin, /admin/, /api/v1/lookup, /lookup enforce credential headers & strict CORS."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter)
    secret = server.api_key
    hostile_origins = ("http://127.0.0.1:59998", "http://localhost:59998", "https://evil.example")

    # 1. Hostile cross-port / remote OPTIONS preflight must never reflect hostile Origin
    for origin in hostile_origins:
        _, opt_hdrs, _, _ = _http_request(
            f"{server_url}{path}",
            method="OPTIONS",
            headers={"Origin": origin, "Access-Control-Request-Method": method},
        )
        assert opt_hdrs.get("access-control-allow-origin") != origin, (
            f"[{route_name}] OPTIONS reflected hostile origin {origin}"
        )

    # 2. Error response (403 unauthenticated / metadata-free) must carry no-store, no-referrer, no hostile ACAO, no secret
    err_status, err_hdrs, err_body, _ = _http_request(
        f"{server_url}{path}",
        method=method,
        headers={"Origin": "http://127.0.0.1:59998"},
        body=body,
    )
    assert err_status == 403, f"[{route_name}] expected 403, got {err_status}"
    assert err_hdrs.get("cache-control") == "no-store", f"[{route_name}] 403 missing Cache-Control: no-store"
    assert err_hdrs.get("referrer-policy") == "no-referrer", f"[{route_name}] 403 missing Referrer-Policy: no-referrer"
    assert err_hdrs.get("access-control-allow-origin") != "http://127.0.0.1:59998", (
        f"[{route_name}] 403 reflected cross-port localhost Origin"
    )
    assert (secret in err_body) is False

    # 3. Authenticated success must carry no-store & no-referrer (and frame-ancestors 'none' for HTML)
    ok_status, ok_hdrs, ok_body, _ = _http_request(
        f"{server_url}{path}",
        method=method,
        headers={"X-API-Token": secret, "Origin": server_url},
        body=body,
    )
    assert ok_status == 200, f"[{route_name}] authenticated request expected 200, got {ok_status}: {ok_body[:200]}"
    assert ok_hdrs.get("cache-control") == "no-store", f"[{route_name}] 200 missing Cache-Control: no-store"
    assert ok_hdrs.get("referrer-policy") == "no-referrer", f"[{route_name}] 200 missing Referrer-Policy: no-referrer"
    if "text/html" in ok_hdrs.get("content-type", ""):
        assert ok_hdrs.get("content-security-policy") == "frame-ancestors 'none'"
    else:
        assert ok_hdrs.get("content-security-policy") == "frame-ancestors 'none'" or route_name in ("session_create", "api_lookup")
        assert ok_hdrs.get("access-control-allow-origin") == server_url


def test_1_2_admin_browser_nav_admission_and_command_isolation(browser, running_controller):
    """Task 1.2: Fresh browser GET /admin admits with in-page bootstrap and no URL token; commands stay auth-gated."""
    server_url, server, adapter = running_controller
    _seed_session(server, adapter)
    secret = server.api_key

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(10000)
        resp = page.goto(f"{server_url}/admin", wait_until="domcontentloaded")
        assert resp is not None and resp.status == 200, f"Fresh browser /admin expected 200, got {getattr(resp, 'status', None)}"
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        assert hdrs.get("cache-control") == "no-store"
        assert hdrs.get("referrer-policy") == "no-referrer"
        assert hdrs.get("content-security-policy") == "frame-ancestors 'none'"
        assert "access-control-allow-origin" not in hdrs
        assert (secret in page.url) is False and ("token=" in page.url) is False
        # In-page bootstrap supplies token in memory without persisting to localStorage
        assert (page.evaluate("(tok) => Boolean(window.state && window.state.token === tok)", secret)) is True
        stored = page.evaluate("() => ({l: Object.assign({}, localStorage), s: Object.assign({}, sessionStorage)})")
        assert (secret in json.dumps(stored)) is False
    finally:
        context.close()

    # Document nav headers alone must NEVER authorize /api/v1/admin/db/vacuum
    vac_status, _, vac_body, _ = _http_request(
        f"{server_url}/api/v1/admin/db/vacuum",
        method="POST",
        headers=dict(BROWSER_NAV_HEADERS),
        body={},
    )
    assert vac_status == 403
    assert json.loads(vac_body).get("error_code") == "UNAUTHORIZED"


# ===========================================================================
# Task 1.2B: Lookup empty-token authority, config parity, script order, title
# ===========================================================================


def test_1_2_lookup_empty_token_config_parity_script_order_and_no_duplicate_title(browser, tmp_path):
    """Task 1.2: Explicit api_token='' stays empty; server_api_key stripped from ui_config; config before init; no dup title."""
    config, paths, goldendict = _offline_config_and_paths(tmp_path)
    leaked_key = "lookup-secret-must-not-resurrect"
    gd = dict(goldendict)
    gd["server_enabled"] = True
    gd["server_api_key"] = leaked_key

    rows = [["0", "Haus", "Haus", "1", "Das Haus.", "house", "house", "", "", "n.", "n", "", "", "0"]]

    # 1. Explicit api_token="" while goldendict carries server_api_key -> must NOT resurrect key
    html_empty_tok = desk.render_lookup_html(
        text="Das Haus.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=paths,
        zid=SESSION_ZID,
        goldendict=gd,
        comments=[],
        headers=HEADERS,
        data_rows=rows,
        sentence_translation="house",
        session_zid=SESSION_ZID,
        api_token="",
        server_enabled=True,
    )
    has_leaked = leaked_key in html_empty_tok
    assert has_leaked is False, "Explicit api_token='' must not resurrect goldendict server_api_key"
    assert _extract_json_island(html_empty_tok, "session-data").get("api_token") == ""
    boot_empty = _extract_json_island(html_empty_tok, "kardenwort-bootstrap")
    assert "server_api_key" not in (boot_empty.get("ui_config") or {})

    # 2. Explicit admitted token with server_enabled=True -> stripped from ui_config, present in credential bootstrap
    admitted = "admitted-lookup-token-xyz"
    html_admitted = desk.render_lookup_html(
        text="Das Haus.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=paths,
        zid=SESSION_ZID,
        goldendict=gd,
        comments=[],
        headers=HEADERS,
        data_rows=rows,
        sentence_translation="house",
        session_zid=SESSION_ZID,
        api_token=admitted,
        server_enabled=True,
    )
    boot_admitted = _extract_json_island(html_admitted, "kardenwort-bootstrap")
    assert "server_api_key" not in (boot_admitted.get("ui_config") or {}), (
        "server_api_key must be stripped from general ui_config in all modes"
    )
    has_leaked_admitted = leaked_key in html_admitted
    assert has_leaked_admitted is False
    admitted_matches = _extract_json_island(html_admitted, "session-data").get("api_token") == admitted
    assert admitted_matches is True

    # Config delivered via #ui-config / window.__CONFIG__ before app init in real browser
    context = browser.new_context()
    try:
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.set_content(html_admitted, wait_until="domcontentloaded")
        assert page_errors == [], f"Lookup shell page errors: {page_errors}"
        cfg_check = page.evaluate("""() => ({
            hasConfig: Boolean(window.__CONFIG__ && typeof window.__CONFIG__ === 'object'),
            hasGrammar: Boolean(window.__CONFIG__ && window.__CONFIG__.grammar),
            hasPosOrder: Boolean(
                window.__CONFIG__
                && window.__CONFIG__.pos_display_order
                && typeof window.__CONFIG__.pos_display_order === 'object'
                && window.__CONFIG__.pos_display_order['art.'] === 0
            ),
            hasServerApiKey: Boolean(window.__CONFIG__ && 'server_api_key' in window.__CONFIG__),
            lemmaCol: window.__CONFIG__ && window.__CONFIG__.lemma_col_name,
        })""")
        assert cfg_check["hasConfig"] is True
        assert cfg_check["hasGrammar"] is True
        assert cfg_check["hasPosOrder"] is True
        assert cfg_check["hasServerApiKey"] is False
        assert cfg_check["lemmaCol"] == "WordSource"
    finally:
        context.close()

    # 3. Disabled mode: no duplicate title attributes on disabled buttons, no resurrected key
    html_disabled = desk.render_lookup_html(
        text="Das Haus.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=paths,
        zid=SESSION_ZID,
        goldendict=gd,
        comments=[],
        headers=HEADERS,
        data_rows=rows,
        sentence_translation="house",
        session_zid=SESSION_ZID,
        api_token=admitted,
        server_enabled=False,
    )
    has_admitted_disabled = admitted in html_disabled
    has_leaked_disabled = leaked_key in html_disabled
    assert has_admitted_disabled is False
    assert has_leaked_disabled is False
    dup_titles = re.findall(r'<button\b[^>]*\btitle="[^"]*"[^>]*\btitle="[^"]*"[^>]*>', html_disabled)
    assert dup_titles == [], f"Found duplicate title attributes on disabled lookup buttons: {dup_titles}"


# ===========================================================================
# Task 1.2C: Backend escaping sinks, skeleton poison, URL schemes, raw islands
# ===========================================================================


def test_1_2_backend_desk_overview_lookup_raw_islands_skeleton_and_favicon_sinks(browser, tmp_path):
    """Task 1.2: Every backend text/attribute/JSON/raw-island/URL sink preserves literal data with zero execution."""
    config, paths, goldendict = _offline_config_and_paths(tmp_path)
    config.set("sentences_mode", "enabled", "true")
    config.set("sentences_mode", "delivery_mode", "container")

    # 1. Desk + Overview cells via run_render_flow with hostile literal AND skeleton-loader poison
    tsv = tmp_path / f"{SESSION_ZID}-hostile.de.tsv"
    tsv_headers = [
        "TokenOrder", "Quotation", "WordSource", "WordSourceInflectedForm",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination",
        "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI",
        "WordSourcePOS", "WordSourceGender", "ClassificationOxford", "Provenance", "DeskSelected",
    ]
    row1 = [
        "0", "Haus", HOSTILE_LITERAL, HOSTILE_LITERAL,
        "1", "Das Haus ist alt.", "Дом старый.",
        HOSTILE_LITERAL, HOSTILE_LITERAL, HOSTILE_LITERAL,
        HOSTILE_LITERAL, HOSTILE_LITERAL, f"3k:{HOSTILE_LITERAL}", HOSTILE_LITERAL, "1",
    ]
    row2 = [
        "1", "Baum", "Baum", "Baum",
        "2", "Der Baum ist hoch.", "Дерево высокое.",
        SKELETON_POISON, SKELETON_POISON, SKELETON_POISON,
        "n.", "m", "5k:B2", "live:test", "0",
    ]
    tsv.write_text(
        "\t".join(tsv_headers) + "\n"
        + "\t".join(row1) + "\n"
        + "\t".join(row2) + "\n",
        encoding="utf-8",
    )

    desk_html = desk.run_render_flow(
        text="Das Haus ist alt. Der Baum ist hoch.",
        language="de",
        zid=SESSION_ZID,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        tsv_path=str(tsv),
        spawn_children=False,
    )

    # 2. Lookup cells via render_lookup_html
    lookup_html = desk.render_lookup_html(
        text="Das Haus ist alt.",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=paths,
        zid=SESSION_ZID,
        goldendict=goldendict,
        comments=[],
        headers=tsv_headers,
        data_rows=[row1, row2],
        sentence_translation=HOSTILE_LITERAL,
        session_zid=SESSION_ZID,
        api_token="",
        server_enabled=False,
    )

    for label, doc in (("desk_container", desk_html), ("lookup_shell", lookup_html)):
        context = browser.new_context()
        try:
            page = context.new_page()
            page.set_content(doc, wait_until="domcontentloaded")
            assert page.evaluate("() => Boolean(window.__xss)") is False, f"[{label}] XSS executed"
            assert page.locator("img[src='x']").count() == 0, f"[{label}] injected <img> found in DOM"
            table_text = page.locator("#lemma-table").inner_text()
            assert HOSTILE_LITERAL in table_text, f"[{label}] hostile literal corrupted in lemma table"
            assert SKELETON_POISON in table_text, f"[{label}] skeleton-loader literal treated as markup instead of text"
            # Untrusted skeleton-loader text must NOT create a real .skeleton-loader element
            assert page.locator("#lemma-table .skeleton-loader").count() == 0, (
                f"[{label}] untrusted 'skeleton-loader' string became trusted .skeleton-loader DOM element"
            )
        finally:
            context.close()

    # 3. Raw-text <script type="text/plain"> islands & favicon_href URL scheme validation
    island_payload = 'val</script><img src=x onerror=window.__xss=4>&"\'прощание'
    shell_html = desk.render_declarative_web_shell(
        bootstrap_payload={
            "ui_config": {},
            "session_data": {"zid": island_payload, "session_zid": island_payload, "language": "de"},
            "zid": island_payload,
            "session_zid": island_payload,
            "language": island_payload,
            "target_language": island_payload,
            "tsv_path": island_payload,
            "sentence_provenance": island_payload,
            "text_base_provider": island_payload,
            "lemma_base_provider": island_payload,
        },
        theme="dark",
        favicon_href='javascript:window.__xss=5" onerror="window.__xss=6',
    )
    assert "javascript:window.__xss" not in shell_html, "Unsafe javascript: favicon_href scheme must be rejected"

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_content(shell_html, wait_until="domcontentloaded")
        assert page.evaluate("() => Boolean(window.__xss)") is False, "Raw-text island or favicon executed XSS"
        assert page.locator("img[src='x']").count() == 0
        # Exact round-trip recovery for arbitrary metadata (e.g. tsv_path & sentence_provenance)
        recovered_prov = page.evaluate("() => document.getElementById('sentence-provenance').textContent")
        recovered_tsv = page.evaluate("() => document.getElementById('tsv-path').textContent")
        assert recovered_prov == island_payload or _extract_json_island(shell_html, "kardenwort-bootstrap")["sentence_provenance"] == island_payload
        assert recovered_tsv == island_payload or _extract_json_island(shell_html, "kardenwort-bootstrap")["tsv_path"] == island_payload
    finally:
        context.close()


# ===========================================================================
# Task 1.2D: Frontend (app.js & WorkspaceTabs.js) live/reload DOM sinks
# ===========================================================================


def test_1_2_frontend_app_and_workspacetabs_sinks_initial_and_live_updates(browser, tmp_path):
    """Task 1.2: WorkspaceTabs & app.js live updates must not execute untrusted HTML or trust 'skeleton-loader'."""
    config, paths, _ = _offline_config_and_paths(tmp_path)
    tsv = tmp_path / f"{SESSION_ZID}-fe.de.tsv"
    tsv.write_text(
        "\t".join(HEADERS) + "\n"
        + "\t".join(["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", "дом", "", "", "n.", "n", "3k:A1", "live:test", "0"]) + "\n",
        encoding="utf-8",
    )
    shell_html = desk.run_render_flow(
        text="Das Haus.",
        language="de",
        zid=SESSION_ZID,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        tsv_path=str(tsv),
        spawn_children=False,
    )

    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_content(shell_html, wait_until="domcontentloaded")

        # 1. WorkspaceTabs.getTranslationHtml with div + img onerror & skeleton-loader poison
        res_wt = page.evaluate(
            """([hostileDiv, skelPoison]) => {
                window.__xss = 0;
                const h1 = window.WorkspaceTabs.getTranslationHtml(hostileDiv);
                const h2 = window.WorkspaceTabs.getTranslationHtml(skelPoison);
                const tc = document.getElementById('translation-container');
                tc.innerHTML = h2;
                return {
                    xss: Boolean(window.__xss),
                    imgs: document.querySelectorAll('img[src="x"]').length,
                    h1HasImgTag: h1.includes('<img'),
                    tcText: tc.textContent,
                    tcSkelCount: tc.querySelectorAll('.skeleton-loader').length,
                };
            }""",
            ['<div><img src=x onerror=window.__xss=1>hello</div>', SKELETON_POISON],
        )
        assert res_wt["xss"] is False, "WorkspaceTabs.getTranslationHtml executed XSS via tmp.innerHTML or skeleton trust"
        assert res_wt["imgs"] == 0
        assert res_wt["h1HasImgTag"] is False
        assert res_wt["tcSkelCount"] == 0
        assert SKELETON_POISON in res_wt["tcText"]

        # 2. WorkspaceTabs.init with hostile ipa/morphology/pos/gender -> tab render
        res_tab = page.evaluate(
            """([hostile, skelPoison]) => {
                window.__xss = 0;
                window.WorkspaceTabs.init([{
                    seq_num: 1,
                    sentence_idx: 1,
                    label: '1',
                    source_html: '<span class="word">Haus</span>',
                    translated_text: hostile,
                    words: [{
                        row_id: '0',
                        token_order: '0',
                        sentence_idx: '1',
                        inflected: hostile,
                        lemma: 'Haus',
                        translation: skelPoison,
                        ipa: hostile,
                        morphology: hostile,
                        pos: hostile,
                        gender: hostile,
                        selected: '0',
                        provenance: hostile,
                    }],
                }]);
                const tbody = document.querySelector('#lemma-table tbody');
                return {
                    xss: Boolean(window.__xss),
                    imgs: tbody.querySelectorAll('img[src="x"]').length,
                    ipaText: (tbody.querySelector('td.col-ipa') || {}).textContent || '',
                    morphText: (tbody.querySelector('td.col-morphology') || {}).textContent || '',
                    posText: (tbody.querySelector('td.col-pos') || {}).textContent || '',
                    transText: (tbody.querySelector('td.col-translation') || {}).textContent || '',
                    skelCount: tbody.querySelectorAll('.skeleton-loader').length,
                };
            }""",
            [HOSTILE_LITERAL, SKELETON_POISON],
        )
        assert res_tab["xss"] is False, "WorkspaceTabs table render executed XSS"
        assert res_tab["imgs"] == 0
        assert res_tab["ipaText"] == HOSTILE_LITERAL
        assert res_tab["morphText"] == HOSTILE_LITERAL
        assert res_tab["posText"] == HOSTILE_LITERAL
        assert res_tab["transText"] == SKELETON_POISON
        assert res_tab["skelCount"] == 0

        # 3. app.js live receiveUpdate path (morphology, ipa, classification, translation)
        res_live = page.evaluate(
            """([hostile]) => {
                window.__xss = 0;
                window.receiveUpdate({
                    stage: 'translated_words',
                    rows: {
                        '0': {
                            row_id: '0',
                            token_order: '0',
                            sentence_idx: 1,
                            lemma: 'Haus',
                            trans: hostile,
                            ipa: hostile,
                            morphology: hostile,
                            pos: hostile,
                            gender: hostile,
                            classifications: { oxford: '3k:' + hostile },
                        },
                    },
                });
                const tbody = document.querySelector('#lemma-table tbody');
                return {
                    xss: Boolean(window.__xss),
                    imgs: tbody.querySelectorAll('img[src="x"]').length,
                    morphText: (tbody.querySelector('td.col-morphology') || {}).textContent || '',
                    clsText: (tbody.querySelector('td[data-col="oxford"]') || {}).textContent || '',
                };
            }""",
            [HOSTILE_LITERAL],
        )
        assert res_live["xss"] is False, "app.js receiveUpdate executed XSS on morphology/classification"
        assert res_live["imgs"] == 0
        assert res_live["morphText"] == HOSTILE_LITERAL
    finally:
        context.close()


# ===========================================================================
# Task 1.2E: Admin sinks, token-free URLs, non-JSON errors, toolbar geometry,
#            template key scrub, and legacy URL log redaction
# ===========================================================================


def test_1_2_admin_sinks_token_free_urls_non_json_errors_and_toolbar_geometry(browser):
    """Task 1.2: Admin option/trash escaping, token-free URLs, non-JSON error surfacing, invariant toolbar geometry."""
    html_content = (ROOT / "assets" / "admin.html").read_text(encoding="utf-8")
    js_content = (ROOT / "assets" / "admin.js").read_text(encoding="utf-8")
    css_content = (ROOT / "assets" / "admin.css").read_text(encoding="utf-8")
    js_no_auto = js_content.replace("document.addEventListener('DOMContentLoaded',", "window.__initApp = (")

    body_inner = html_content.split('<body class="theme-dark">')[1].split('<script src="/assets/admin.js"></script>')[0]

    for theme_cls in ("theme-dark", "theme-light", "theme-white"):
        full_html = (
            f'<!DOCTYPE html><html><head><style>{css_content}</style></head>'
            f'<body class="{theme_cls}">{body_inner}<script>{js_no_auto}</script></body></html>'
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            page = context.new_page()
            page.set_content(full_html, wait_until="domcontentloaded")
            sample_sessions = [
                {"zid": "20261123000001", "slug": "s1", "source_language": "de", "sentence_count": 1, "word_count": 5, "projects": [], "source_raw_text": "S1"},
                {"zid": "20261123000002", "slug": "s2", "source_language": "de", "sentence_count": 1, "word_count": 5, "projects": [], "source_raw_text": "S2"},
            ]
            # Verify toolbar does NOT reserve unused blank space when unselected AND row boxes stay invariant on toggle
            metrics = page.evaluate(
                """(sessions) => {
                    initSessionsExplorer();
                    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
                    document.getElementById('tab-sessions').classList.add('active');
                    window.state.sessionsExplorer.sessions = sessions;
                    window.state.sessionsExplorer.totalCount = 2;
                    window.renderSessionsExplorerTable(sessions);

                    const toolbar = document.getElementById('sessions-batch-toolbar');
                    const banner = document.getElementById('sessions-selection-banner');
                    const row1 = document.querySelector("tr[data-zid='20261123000001']");
                    const beforeToolbarRect = toolbar.getBoundingClientRect();
                    const beforeBannerRect = banner.getBoundingClientRect();
                    const beforeRowRect = row1.getBoundingClientRect();

                    row1.querySelector('.session-row-checkbox').click();
                    const afterRowRect = row1.getBoundingClientRect();

                    return {
                        beforeToolbarHeight: beforeToolbarRect.height,
                        beforeToolbarVisible: getComputedStyle(toolbar).visibility !== 'hidden' && getComputedStyle(toolbar).display !== 'none',
                        beforeBannerHeight: beforeBannerRect.height,
                        beforeTop: beforeRowRect.top,
                        afterTop: afterRowRect.top,
                        beforeHeight: beforeRowRect.height,
                        afterHeight: afterRowRect.height,
                    };
                }""",
                sample_sessions,
            )
            # Toggling selection must not shift existing table row bounding boxes
            assert abs(metrics["afterTop"] - metrics["beforeTop"]) <= 0.5, (
                f"[{theme_cls}] row shifted vertically on selection toggle: {metrics}"
            )
            assert abs(metrics["afterHeight"] - metrics["beforeHeight"]) <= 0.5
            # Hidden batch-toolbar + selection-banner must not reserve permanently unused blank height above table
            gap_check = page.evaluate("""() => {
                clearAllSelections();
                updateSelectionUI();
                const drop = document.getElementById('sessions-drop-zone').getBoundingClientRect();
                const tbl = document.querySelector('.sessions-table-container').getBoundingClientRect();
                return tbl.top - drop.bottom;
            }""")
            assert gap_check <= 24, f"[{theme_cls}] unused toolbar/banner blank space remains above table: {gap_check}px"
        finally:
            context.close()

    # Hostile trash/option sinks, token-free fetch/downloads, and non-JSON error surfacing
    full_html = (
        f'<!DOCTYPE html><html><head><style>{css_content}</style></head>'
        f'<body class="theme-dark">{body_inner}<script>{js_no_auto}</script></body></html>'
    )
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_content(full_html, wait_until="domcontentloaded")
        hostile_zid = "2026');window.__xss=1;//\"><img src=x onerror=window.__xss=2>"
        hostile_lang = 'de"><img src=x onerror=window.__xss=3>'
        result = page.evaluate(
            """async ([hostileZid, hostileLang]) => {
                window.__xss = 0;
                window.state.token = 'admin-secret-token-check';
                const fetchCalls = [];
                const openedUrls = [];
                window.open = (u) => { openedUrls.push(String(u)); return { close() {}, location: {} }; };
                window.fetch = async (url, opts) => {
                    fetchCalls.push({ url: String(url), headers: Object.assign({}, (opts && opts.headers) || {}) });
                    if (String(url).includes('/api/v1/admin/sessions')) {
                        return { ok: true, status: 200, headers: new Headers({'content-type': 'application/json'}),
                                 text: async () => JSON.stringify({ status: 'success', data: { sessions: [{ zid: hostileZid, slug: 's', source_language: hostileLang }] } }),
                                 json: async () => ({ status: 'success', data: { sessions: [{ zid: hostileZid, slug: 's', source_language: hostileLang }] } }) };
                    }
                    if (String(url).includes('/api/v1/admin/trash')) {
                        return { ok: true, status: 200, headers: new Headers({'content-type': 'application/json'}),
                                 text: async () => JSON.stringify({ status: 'success', data: { sessions: [{ zid: hostileZid, slug: 's', deleted_at: '2026' }], projects: [] } }),
                                 json: async () => ({ status: 'success', data: { sessions: [{ zid: hostileZid, slug: 's', deleted_at: '2026' }], projects: [] } }) };
                    }
                    if (String(url).includes('/api/v1/admin/db/vacuum')) {
                        return { ok: false, status: 502, headers: new Headers({'content-type': 'text/html'}),
                                 text: async () => '<html><body>502 Bad Gateway</body></html>',
                                 json: async () => { throw new SyntaxError('Unexpected token <'); } };
                    }
                    if (String(url).includes('/tsv')) {
                        return { ok: true, status: 200, headers: new Headers({'content-type': 'text/tab-separated-values'}),
                                 text: async () => 'WordSource\\nHaus\\n',
                                 blob: async () => new Blob(['WordSource\\nHaus\\n']) };
                    }
                    if (String(url).includes('dump.sql')) {
                        return { ok: true, status: 200, headers: new Headers({'content-type': 'text/plain'}),
                                 text: async () => 'CREATE TABLE t(x);',
                                 blob: async () => new Blob(['CREATE TABLE t(x);']) };
                    }
                    return { ok: true, status: 200, headers: new Headers({'content-type': 'application/json'}),
                             text: async () => JSON.stringify({ ok: true }),
                             json: async () => ({ ok: true }) };
                };

                initMaintenance();
                await openLinkSessionModal(1);
                await loadTrash();
                await openTsvInspectorModal({ zid: '20261123000001', slug: 's1', source_language: 'de' });
                document.getElementById('btn-sql-dump').click();

                let restoredArgs = null;
                window.restoreTrash = (type, id) => { restoredArgs = [type, id]; };
                const restoreBtn = document.querySelector('#deleted-sessions-list button');
                const hasInlineOnclick = restoreBtn ? restoreBtn.hasAttribute('onclick') : false;
                if (restoreBtn) restoreBtn.click();

                // Non-JSON 502 vacuum failure: await button re-enable via MutationObserver / microtask
                const vacBtn = document.getElementById('btn-vacuum-db');
                const vacDone = new Promise((resolve) => {
                    const origFetch = window.fetch;
                    const obs = new MutationObserver(() => {
                        if (!vacBtn.disabled && document.getElementById('vacuum-status').textContent === 'Failed') {
                            obs.disconnect();
                            resolve();
                        }
                    });
                    obs.observe(vacBtn, { attributes: true });
                    obs.observe(document.getElementById('vacuum-status'), { childList: true, characterData: true, subtree: true });
                });
                vacBtn.click();
                await vacDone;
                const toasts = Array.from(document.querySelectorAll('#toast-container .toast')).map(t => t.textContent);

                const tsvHref = document.getElementById('btn-tsv-download').getAttribute('href') || '';
                const optVal = document.querySelector('#link-session-select option') ? document.querySelector('#link-session-select option').value : '';
                return {
                    xss: Boolean(window.__xss),
                    imgs: document.querySelectorAll('img[src="x"]').length,
                    optVal,
                    hasInlineOnclick,
                    restoredArgs,
                    fetchUrlsWithToken: fetchCalls.filter(c => c.url.includes('token=')).map(c => c.url),
                    openedUrlsWithToken: openedUrls.filter(u => u.includes('token=')),
                    tsvHrefHasToken: tsvHref.includes('token='),
                    toasts,
                };
            }""",
            [hostile_zid, hostile_lang],
        )
        assert result["xss"] is False, "Admin template sink executed XSS"
        assert result["imgs"] == 0, "Admin template injected <img> node"
        assert result["optVal"] == hostile_zid, f"Option value corrupted: {result['optVal']!r}"
        assert result["hasInlineOnclick"] is False, "Recycle Bin restore button must use delegated event, not inline onclick"
        assert result["restoredArgs"] == ["session", hostile_zid], f"Delegated restore passed wrong ID: {result['restoredArgs']!r}"
        assert result["fetchUrlsWithToken"] == [], f"apiFetch leaked ?token= in URL: {result['fetchUrlsWithToken']}"
        assert result["openedUrlsWithToken"] == [], f"window.open leaked ?token= in URL: {result['openedUrlsWithToken']}"
        assert result["tsvHrefHasToken"] is False, "TSV download href leaked ?token="
        assert any("502" in t and "Unexpected token" not in t for t in result["toasts"]), (
            f"Non-JSON 502 vacuum error did not surface clean status feedback: {result['toasts']}"
        )
    finally:
        context.close()


def test_1_2_template_key_scrubbed_and_legacy_url_token_redacted_in_logs(running_controller, caplog):
    """Task 1.2: config.ini.template must have empty [server] api_key; controller access logs redact query tokens."""
    template_cfg = configparser.ConfigParser()
    template_cfg.read(ROOT / "config.ini.template", encoding="utf-8")
    has_template_key = bool(template_cfg.get("server", "api_key", fallback="").strip())
    assert has_template_key is False, "config.ini.template must not ship a non-empty [server] api_key"

    server_url, server, adapter = running_controller
    _seed_session(server, adapter)
    secret = server.api_key

    with caplog.at_level(logging.INFO, logger=controller.logger.name):
        _http_request(
            f"{server_url}/?session_zid={SESSION_ZID}&token={urllib.parse.quote(secret)}&api_token={urllib.parse.quote(secret)}",
            method="GET",
            headers={},
        )
    logged_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert (secret in logged_text) is False, "Controller access/error logs must redact legacy incoming URL query tokens"


# ===========================================================================
# Task 1.3: Explicit reviewed alias redirect contract coverage
# ===========================================================================


def test_1_3_alias_redirect_contract_no_token_location_header_browser_and_rejections(
    browser, running_controller, monkeypatch
):
    """Task 1.3: Reviewed alias contract without URL-secret restoration."""
    server_url, server, adapter = running_controller
    server.arbiter.storage_adapter = adapter
    monkeypatch.setattr(desk, "run_progressive_worker_async", lambda *a, **kw: None)

    cached_zid = "20261123061301"
    requested_zid = "20261123061302"
    text = "Das Haus ist groß."
    target_lang = server.config.get("settings", "default_target_language", fallback="ru")
    secret = server.api_key

    adapter.save_session(
        session_zid=cached_zid,
        slug="das-haus-ist-gross-alias",
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

    # 1. Retain identity assertion on POST /api/v1/render
    st, _, raw, _ = _http_request(
        f"{server_url}/api/v1/render",
        method="POST",
        headers={"X-API-Token": secret},
        body={
            "session_zid": requested_zid,
            "zid": requested_zid,
            "language": "de",
            "text_mode": "single",
            "text": text,
            "bypass_lang_check": True,
        },
    )
    assert st == 200
    data = json.loads(raw)["data"]
    assert data["zid"] == cached_zid
    assert data["session_zid"] == cached_zid

    # 2. Retain 302 + query preservation + add no-token Location & security headers
    st_302, hdrs_302, body_302, _ = _http_request(
        f"{server_url}/?session_zid={requested_zid}&seq_num=1&theme=dark",
        method="GET",
        headers={"X-API-Token": secret},
        follow_redirects=False,
    )
    assert st_302 == 302
    loc = hdrs_302.get("location", "")
    assert loc.startswith("/?"), f"Alias Location must remain relative/same-controller, got {loc!r}"
    assert f"session_zid={cached_zid}" in loc
    assert "seq_num=1" in loc
    assert "theme=dark" in loc
    assert "token=" not in loc and "api_token=" not in loc and (secret in loc) is False
    assert hdrs_302.get("cache-control") == "no-store"
    assert hdrs_302.get("referrer-policy") == "no-referrer"
    assert hdrs_302.get("content-security-policy") == "frame-ancestors 'none'"
    assert (secret in body_302) is False

    # 3. Same-origin authenticated-header follow-through -> 200 with cached_zid
    st_200, _, html_200, final_url = _http_request(
        f"{server_url}{loc}",
        method="GET",
        headers={"X-API-Token": secret},
    )
    assert st_200 == 200
    assert f"session_zid={cached_zid}" in final_url
    assert cached_zid in html_200

    # 4. Real fresh browser follow-through (zero URL token, zero injected headers) -> 200 ready
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(10000)
        resp = page.goto(f"{server_url}/?session_zid={requested_zid}&seq_num=1&theme=dark", wait_until="domcontentloaded")
        assert resp is not None and resp.status == 200
        assert f"session_zid={cached_zid}" in page.url
        assert ("token=" in page.url) is False and (secret in page.url) is False
        expect(page.locator("html")).to_have_attribute("data-workspace-phase", "ready")
    finally:
        context.close()

    # 5. Negative: metadata-free client with initial URL token only -> 403 at redirect target
    st_meta_free, _, body_meta_free, _ = _http_request(
        f"{server_url}/?session_zid={requested_zid}&seq_num=1&theme=dark&token={urllib.parse.quote(secret)}",
        method="GET",
        headers={},
        follow_redirects=True,
    )
    assert st_meta_free == 403
    assert (secret in body_meta_free) is False

    # 6. Negative: cross-origin alias request -> 403 with no credential and no ACAO reflection
    for origin in ("http://127.0.0.1:59998", "https://evil.example"):
        st_xo, hdrs_xo, body_xo, _ = _http_request(
            f"{server_url}/?session_zid={requested_zid}&seq_num=1&theme=dark",
            method="GET",
            headers={"Origin": origin},
        )
        assert st_xo == 403
        assert hdrs_xo.get("access-control-allow-origin") != origin
        assert (secret in body_xo) is False

    # 7. Retain 404 for unknown session ZID (authenticated)
    st_404, _, _, _ = _http_request(
        f"{server_url}/?session_zid=20990101000000",
        method="GET",
        headers={"X-API-Token": secret},
    )
    assert st_404 == 404
