"""Phase 1 contracts: failures reproduce defects before production repairs.

Only the browser's outbound transport is controlled. Renderers, components,
store, selection commands and save acknowledgments execute their real code.
These cases do not claim SQLite or native-host end-to-end verification.
"""

import json

import pytest
from playwright.sync_api import expect

import kardenwort_desk
from tests.desktop_component_fixture import render_desktop_components
from tests.test_controller_integration import running_controller  # noqa: F401


def audit_html(tmp_path, module=kardenwort_desk, theme="dark", container=False):
    config, paths, _, _ = module.load_config()
    config.set("storage", "backend", "tsv")
    config.set("server", "api_key", "test-controller-api-key")
    paths["storage_backend"] = "tsv"
    config.set("sentences_mode", "enabled", "true" if container else "false")
    config.set("sentences_mode", "delivery_mode", "container")
    config.set("rendering", "filter_selected_only", "false")
    config.set("rendering", "highlight_same_lemma", "false")
    config.set("rendering", "auto_save_on_edit", "false")
    tsv = tmp_path / "20261023221555-audit.de.tsv"
    tsv.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\thouse\t1\tDas Haus.\tThe house.\t1\n"
        "Baum\tBaum\ttree\t2\tDer Baum.\tThe tree.\t0\n",
        encoding="utf-8",
    )
    return module.run_render_flow(
        text="Das Haus. Der Baum.", language="de", zid="20261023221555",
        text_mode="single", config=config, resolved_paths=paths, theme=theme,
        tsv_path=str(tsv), spawn_children=False,
    )


TRANSPORT = """<script>
window.__requests = [];
window.__pendingSaves = [];
window.__pendingStatus = [];
window.__reloads = 0;
window.onSessionReload = function() { window.__reloads++; };
window.EventSource = class {
    constructor() { window.__eventSource = this; }
    close() {}
};
window.fetch = function(url, options) {
    const body = options && options.body ? JSON.parse(options.body) : null;
    const headers = options && options.headers ? options.headers : {};
    window.__requests.push({url: String(url), body, headers});
    const response = data => ({ok: true, status: 200, json: async () => data});
    if (String(url).includes('/session/save') && window.__holdSaves) {
        return new Promise(resolve => window.__pendingSaves.push(
            () => resolve(response({ok: true}))));
    }
    if (String(url).includes('/session/status') && window.__holdStatus) {
        return new Promise(resolve => window.__pendingStatus.push(
            () => resolve(response({ok: true, stage: 'finished'}))));
    }
    return Promise.resolve(response({ok: true, stage: 'finished'}));
};
</script>"""


@pytest.fixture
def audit_page(page, tmp_path, request):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = audit_html(tmp_path, container=getattr(request, "param", False))
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    page.wait_for_function("typeof window.toggleRowSelection === 'function'")
    assert errors == []
    return page


@pytest.mark.parametrize("command,field", [
    ("setFilterSelectedOnly", "filterSelectedOnly"),
    ("setHighlightSameLemma", "highlightSameLemma"),
])
def test_user_toggle_survives_background_store_delta(audit_page, command, field):
    page = audit_page
    page.evaluate("command => window[command](true)", command)
    page.evaluate("AppStore.applyDeltas({stage: 'translating'})")
    assert page.evaluate("field => AppState[field]", field) is True


def empty_filtered_table(page):
    page.evaluate("""() => {
        toggleRowSelection(0, false);
        toggleRowSelection(1, false);
        setFilterSelectedOnly(true);
    }""")
    expect(page.get_by_role("button", name="show all words")).to_be_visible()


def test_empty_action_survives_store_then_selection_projection(audit_page):
    page = audit_page
    empty_filtered_table(page)
    page.evaluate("""() => {
        AppStore.applyDeltas({stage: 'translating'});
        toggleRowSelection(1, true);
        toggleRowSelection(1, false);
    }""")
    expect(page.get_by_role("button", name="show all words")).to_be_visible()


@pytest.mark.parametrize("edit_autosave", [False, True])
def test_selection_saves_without_manual_save(audit_page, edit_autosave):
    page = audit_page
    page.clock.install()
    page.evaluate("enabled => { __CONFIG__.auto_save_on_edit = enabled; }", edit_autosave)
    page.locator('#lemma-table tr[data-row-id="1"] td[data-col="WordSource"]').click()
    assert any(d["column"] == "DeskSelected" for d in json.loads(page.evaluate("getDeltas()")))
    # Advance the actual debounce clock; no wall-clock sleep or fabricated save.
    page.clock.run_for(1000)
    saves = page.evaluate("__requests.filter(r => r.url.includes('/session/save'))")
    assert len(saves) == 1
    assert all(d["column"] == "DeskSelected" for d in saves[0]["body"]["deltas"])


def test_late_save_acknowledgment_preserves_new_selection(audit_page):
    page = audit_page
    page.evaluate("window.__holdSaves = true")
    page.locator('#lemma-table tr[data-row-id="1"] td[data-col="WordSource"]').click()
    page.evaluate("void onSaveClick()")
    page.wait_for_function("__pendingSaves.length === 1")
    page.locator('#lemma-table tr[data-row-id="0"] td[data-col="WordSource"]').click()
    page.evaluate("__pendingSaves.shift()()")
    expect(page.get_by_role("button", name="Save (Ctrl+S)", exact=True)).to_be_enabled()
    pending = json.loads(page.evaluate("getDeltas()"))
    assert any(d["row_id"] == 0 and d["column"] == "DeskSelected" for d in pending)
    assert page.evaluate("isDirty()") is True


@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("audit_page", [False, True], indirect=True, ids=["single", "container"])
def test_f5_status_with_optional_sse_overlap(audit_page, overlap):
    page = audit_page
    empty_filtered_table(page)
    page.evaluate("window.__holdStatus = true")
    page.keyboard.press("F5")
    page.wait_for_function("__pendingStatus.length >= 1 || __reloads > 0")
    assert page.evaluate("__reloads") == 0, page.evaluate("__requests")
    assert page.evaluate("__pendingStatus.length") == 1
    if overlap:
        page.evaluate("""() => {
            SSEBridge.connect('20260912232500');
            __eventSource.onmessage({data: JSON.stringify({stage: 'translating'})});
            toggleRowSelection(1, true);
            toggleRowSelection(1, false);
        }""")
    page.evaluate("__pendingStatus.shift()()")
    expect(page.get_by_role("button", name="Update", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="show all words")).to_be_visible()
    assert page.evaluate("__reloads") == 0


@pytest.mark.parametrize("src_lang,tgt_lang,src_word,tgt_word,src_sent,tgt_sent", [
    ("en", "fr", "house", "maison", "A house.", "Une maison."),
    ("es", "it", "casa", "casa", "Una casa.", "Una casa."),
])
def test_f1_non_german_lookup_language_metadata(page, src_lang, tgt_lang, src_word, tgt_word, src_sent, tgt_sent):
    config, paths, goldendict, _ = kardenwort_desk.load_config()
    goldendict["server_enabled"] = True
    goldendict["server_api_key"] = "test-controller-api-key"
    zid = "20261025211003"
    html = render_desktop_components(
        src_sent, src_lang, tgt_lang, config, paths, zid, goldendict,
        [], ["WordSource", "WordDestination", "DeskSelected", "SentenceSourceIndex", "TokenOrder"], [[src_word, tgt_word, "1", "1", "0"]], tgt_sent,
        session_zid=zid, api_token="test-controller-api-key", server_enabled=True,
    )
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    assert page.evaluate("document.getElementById('session-lang').textContent.trim()") == src_lang
    assert page.evaluate("document.getElementById('session-target-lang').textContent.trim()") == tgt_lang
    assert page.evaluate("getSessionLang()") == src_lang
    assert page.evaluate("getSessionTargetLang()") == tgt_lang

    # 1. Re-text request
    page.evaluate("void onRetextClick()")
    retext_req = page.evaluate("__requests.find(r => r.url.includes('/session/retext'))")
    assert retext_req is not None
    assert retext_req["body"]["language"] == src_lang
    assert retext_req["body"]["session_zid"] == zid
    assert retext_req["headers"].get("X-API-Token") == "test-controller-api-key"

    # 2. Re-word request
    page.evaluate("void onRewordClick()")
    reword_req = page.evaluate("__requests.find(r => r.url.includes('/session/reword'))")
    assert reword_req is not None
    assert reword_req["body"]["language"] == src_lang
    assert reword_req["body"]["session_zid"] == zid
    assert reword_req["headers"].get("X-API-Token") == "test-controller-api-key"

    # 3. Save request
    page.locator('#lemma-table tr[data-row-id="0"]').click()
    page.evaluate("void onSaveClick()")
    save_req = page.evaluate("__requests.find(r => r.url.includes('/session/save'))")
    assert save_req is not None
    assert save_req["body"]["language"] == src_lang
    assert save_req["body"]["session_zid"] == zid
    assert save_req["headers"].get("X-API-Token") == "test-controller-api-key"

    # 4. Export request
    page.locator('#lemma-table tr[data-row-id="0"]').click()
    page.evaluate("void onSendToAnkiClick()")
    export_req = page.evaluate("__requests.find(r => r.url.includes('/session/export'))")
    assert export_req is not None
    assert export_req["body"]["language"] == src_lang
    assert export_req["body"]["session_zid"] == zid
    assert export_req["headers"].get("X-API-Token") == "test-controller-api-key"

    # 5. Source audio playback
    page.evaluate(f"() => window.playAudio('{src_word}', '{src_lang}')")
    audio_src_req = page.evaluate(f"__requests.find(r => r.url.includes('/api/v1/audio/play') && r.body && r.body.language === '{src_lang}')")
    assert audio_src_req is not None
    assert audio_src_req["body"]["text"] == src_word
    assert audio_src_req["headers"].get("X-API-Token") == "test-controller-api-key"

    # 6. Target audio playback
    page.evaluate(f"() => window.playAudio('{tgt_word}', '{tgt_lang}')")
    audio_tgt_req = page.evaluate(f"__requests.find(r => r.url.includes('/api/v1/audio/play') && r.body && r.body.language === '{tgt_lang}')")
    assert audio_tgt_req is not None
    assert audio_tgt_req["body"]["text"] == tgt_word
    assert audio_tgt_req["headers"].get("X-API-Token") == "test-controller-api-key"


def test_phase2_missing_language_rejection_without_template_fallback(page):
    config, paths, goldendict, _ = kardenwort_desk.load_config()
    goldendict["server_enabled"] = True
    goldendict["server_api_key"] = "test-controller-api-key"
    zid = "20261025211003"
    html = render_desktop_components(
        "Some text.", "", "", config, paths, zid, goldendict,
        [], ["WordSource", "WordDestination", "DeskSelected"], [["some", "quelque", "0"]], "Quelque texte.",
        session_zid=zid, api_token="test-controller-api-key", server_enabled=True,
    )
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    assert page.evaluate("getSessionLang()") == ""
    assert page.evaluate("getSessionTargetLang()") == ""
    page.evaluate("void onRetextClick()")
    retext_req = page.evaluate("__requests.find(r => r.url.includes('/session/retext'))")
    assert retext_req is None

    page.locator('#lemma-table tr[data-row-id="0"]').click()
    error_msg = page.evaluate("""() => {
        return window.flushPendingSaves().then(() => null).catch(e => e.message);
    }""")
    assert error_msg == "Session language missing"


def test_phase2_css_enabled_and_disabled_renderers(page):
    config, paths, goldendict, _ = kardenwort_desk.load_config()
    zid = "20261025211003"

    # 1. Lookup with CSS disabled
    goldendict["disable_css"] = True
    html_lookup_off = kardenwort_desk.render_lookup_html(
        "A house.", "en", "fr", config, paths, zid, goldendict,
        [], ["WordSource", "WordDestination"], [["house", "maison"]], "Une maison.",
        session_zid=zid, api_token="test-controller-api-key", server_enabled=True,
    )
    assert not any("<link" in l and "stylesheet" in l for l in html_lookup_off.splitlines())
    assert '<style id="modular-css">' not in html_lookup_off
    assert '<style id="configured-theme">' not in html_lookup_off

    # 2. Lookup with CSS enabled
    goldendict["disable_css"] = False
    html_lookup_on = kardenwort_desk.render_lookup_html(
        "A house.", "en", "fr", config, paths, zid, goldendict,
        [], ["WordSource", "WordDestination"], [["house", "maison"]], "Une maison.",
        session_zid=zid, api_token="test-controller-api-key", server_enabled=True,
    )
    assert not any("<link" in l and "/css/" in l for l in html_lookup_on.splitlines())
    assert '<style>' in html_lookup_on
    assert 'id="modular-css"' not in html_lookup_on

    # 3. Declarative web shell with CSS disabled
    shell_off = kardenwort_desk.render_declarative_web_shell(
        {"session_zid": zid, "language": "en", "target_lang": "fr"},
        theme="dark", disable_css=True,
    )
    assert not any("<link" in l and "stylesheet" in l for l in shell_off.splitlines())
    assert '<style id="modular-css">' not in shell_off
    assert '<style id="configured-theme">' not in shell_off

    # 4. Declarative web shell with CSS enabled
    shell_on = kardenwort_desk.render_declarative_web_shell(
        {"session_zid": zid, "language": "en", "target_lang": "fr"},
        theme="dark", disable_css=False,
    )
    assert not any("<link" in l and "/css/" in l for l in shell_on.splitlines())
    assert '<style id="modular-css">' in shell_on




@pytest.mark.parametrize("backend", ["sqlite", "tsv"])
def test_f2_browser_reconnect_recovers_durable_membership(page, tmp_path, running_controller, backend):
    from pathlib import Path
    url, server, _ = running_controller
    server.config.set("storage", "backend", backend)
    server.resolved_paths["storage_backend"] = backend
    server.resolved_paths["generated_results_dir"] = server.resolved_paths["results_dir"]
    if backend == "tsv":
        adapter = kardenwort_desk.TsvStorageAdapter(server.config, server.resolved_paths)
    else:
        adapter = kardenwort_desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter

    zid = "20261025211003"
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "DeskSelected", "TokenOrder"]
    rows = [
        ["Haus", "Haus", "house", "1", "Das Haus.", "0", "0"],
        ["Baum", "Baum", "tree", "2", "Der Baum.", "0", "1"],
    ]
    path = Path(server.resolved_paths["results_dir"]) / f"{zid}-reconnect.de.tsv"
    adapter.save_session(
        session_zid=zid, slug="reconnect-gap", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.",
        headers=headers, data_rows=rows, working_tsv_path=path,
    )
    server.arbiter.sessions[zid] = {
        "text": "Das Haus. Der Baum.", "headers": headers, "data_rows": rows, "tsv_path": str(path),
    }

    config, paths, _, _ = kardenwort_desk.load_config()
    paths.update(storage_backend=backend, sqlite_db_path=server.resolved_paths["sqlite_db_path"],
                 results_dir=server.resolved_paths["results_dir"])
    config.set("storage", "backend", backend)
    config.set("server", "api_key", "test-controller-api-key")

    tsv = tmp_path / f"{zid}-audit.de.tsv"
    tsv.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\thouse\t1\tDas Haus.\tThe house.\t0\n"
        "Baum\tBaum\ttree\t2\tDer Baum.\tThe tree.\t0\n",
        encoding="utf-8",
    )
    html = kardenwort_desk.run_render_flow(
        text="Das Haus. Der Baum.", language="de", zid=zid,
        text_mode="single", config=config, resolved_paths=paths, theme="dark",
        tsv_path=str(tsv), spawn_children=False,
    )

    page.set_extra_http_headers({"X-API-Token": "test-controller-api-key"})
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto(url + "/")
    page.wait_for_function("typeof window.toggleRowSelection === 'function'")

    # Disconnect stream
    page.evaluate("SSEBridge.disconnect()")

    # Remote mutation while stream is disconnected: Haus is selected in durable storage
    rows[0][headers.index("DeskSelected")] = "1"
    adapter.save_session(
        session_zid=zid, slug="reconnect-gap", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.",
        headers=headers, data_rows=rows, working_tsv_path=path,
    )

    # Reconnect stream after connection gap
    page.evaluate(f"() => SSEBridge.connect('{zid}', '{url}')")
    page.wait_for_function("document.getElementById('sse-status-indicator') && document.getElementById('sse-status-indicator').dataset.status === 'connected'")

    page.wait_for_function("window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas.haus === true")
    visible = page.evaluate("({haus: !!(window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas.haus), baum: !!(window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas.baum)})")
    assert visible == {"haus": True, "baum": False}, f"Visible selection {visible} must reflect durable state after reconnect"
    assert page.evaluate("document.getElementById('sse-status-indicator').getAttribute('data-sync')") == "synced"


def test_phase3_local_uncommitted_selections_preserved_during_recovery(page, tmp_path, running_controller):
    from pathlib import Path
    url, server, adapter = running_controller
    zid = "20261025211003"
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "DeskSelected", "TokenOrder"]
    rows = [
        ["Haus", "Haus", "house", "1", "Das Haus.", "0", "0"],
        ["Baum", "Baum", "tree", "2", "Der Baum.", "0", "1"],
    ]
    path = Path(server.resolved_paths["results_dir"]) / f"{zid}-reconnect.de.tsv"
    adapter.save_session(
        session_zid=zid, slug="reconnect-gap", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.",
        headers=headers, data_rows=rows, working_tsv_path=path,
    )
    server.arbiter.sessions[zid] = {
        "text": "Das Haus. Der Baum.", "headers": headers, "data_rows": rows, "tsv_path": str(path),
    }

    config, paths, _, _ = kardenwort_desk.load_config()
    paths.update(storage_backend="sqlite", sqlite_db_path=server.resolved_paths["sqlite_db_path"],
                 results_dir=server.resolved_paths["results_dir"])
    config.set("storage", "backend", "sqlite")
    config.set("server", "api_key", "test-controller-api-key")

    tsv = tmp_path / f"{zid}-audit.de.tsv"
    tsv.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\thouse\t1\tDas Haus.\tThe house.\t0\n"
        "Baum\tBaum\ttree\t2\tDer Baum.\tThe tree.\t0\n",
        encoding="utf-8",
    )
    html = kardenwort_desk.run_render_flow(
        text="Das Haus. Der Baum.", language="de", zid=zid,
        text_mode="single", config=config, resolved_paths=paths, theme="dark",
        tsv_path=str(tsv), spawn_children=False,
    )

    page.set_extra_http_headers({"X-API-Token": "test-controller-api-key"})
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto(url + "/")
    page.wait_for_function("typeof window.toggleRowSelection === 'function'")

    page.evaluate("SSEBridge.disconnect()")

    # 1. Local user selects Baum (row 1) while disconnected (uncommitted delta)
    page.evaluate("toggleRowSelection(1, true)")
    assert page.evaluate("isDirty()") is True

    # 2. Remote storage changed Haus to selected
    rows[0][headers.index("DeskSelected")] = "1"
    adapter.save_session(
        session_zid=zid, slug="reconnect-gap", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.",
        headers=headers, data_rows=rows, working_tsv_path=path,
    )

    # 3. Stream reconnects
    page.evaluate(f"() => SSEBridge.connect('{zid}', '{url}')")
    page.wait_for_function("document.getElementById('sse-status-indicator') && document.getElementById('sse-status-indicator').dataset.status === 'connected'")
    page.wait_for_function("window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas.haus === true")

    # Both Haus (from remote backend) AND Baum (from uncommitted local delta) must be selected!
    visible = page.evaluate("({haus: !!(window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas.haus), baum: !!(window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas.baum)})")
    assert visible == {"haus": True, "baum": True}, f"Local intent for Baum must be preserved alongside recovered Haus: {visible}"
    assert page.evaluate("isDirty()") is True, "Local uncommitted deltas must retain dirty state"


def test_phase3_concurrent_live_event_prevents_stale_read_overwrite(page, tmp_path, running_controller):
    from pathlib import Path
    url, server, adapter = running_controller
    zid = "20261025211003"
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "DeskSelected", "TokenOrder"]
    rows = [
        ["Haus", "Haus", "house", "1", "Das Haus.", "0", "0"],
        ["Baum", "Baum", "tree", "2", "Der Baum.", "0", "1"],
    ]
    path = Path(server.resolved_paths["results_dir"]) / f"{zid}-reconnect.de.tsv"
    adapter.save_session(
        session_zid=zid, slug="reconnect-gap", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.",
        headers=headers, data_rows=rows, working_tsv_path=path,
    )
    server.arbiter.sessions[zid] = {
        "text": "Das Haus. Der Baum.", "headers": headers, "data_rows": rows, "tsv_path": str(path),
    }

    config, paths, _, _ = kardenwort_desk.load_config()
    paths.update(storage_backend="sqlite", sqlite_db_path=server.resolved_paths["sqlite_db_path"],
                 results_dir=server.resolved_paths["results_dir"])
    config.set("storage", "backend", "sqlite")
    config.set("server", "api_key", "test-controller-api-key")

    tsv = tmp_path / f"{zid}-audit.de.tsv"
    tsv.write_text(
        "Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\thouse\t1\tDas Haus.\tThe house.\t0\n"
        "Baum\tBaum\ttree\t2\tDer Baum.\tThe tree.\t0\n",
        encoding="utf-8",
    )
    html = kardenwort_desk.run_render_flow(
        text="Das Haus. Der Baum.", language="de", zid=zid,
        text_mode="single", config=config, resolved_paths=paths, theme="dark",
        tsv_path=str(tsv), spawn_children=False,
    )

    page.set_extra_http_headers({"X-API-Token": "test-controller-api-key"})
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto(url + "/")
    page.wait_for_function("typeof window.toggleRowSelection === 'function'")

    # Intercept /internal/selection to hold the read in flight
    held_reads = []
    page.route("**/internal/selection", lambda route: held_reads.append(route))

    # Trigger recovery via SSEBridge connect
    page.evaluate(f"() => SSEBridge.connect('{zid}', '{url}')")
    import time
    for _ in range(50):
        if len(held_reads) >= 1:
            break
        page.wait_for_timeout(50)
    assert len(held_reads) >= 1

    # While read is held, a newer live event arrives updating membership
    page.evaluate("() => window.applySharedSelection({haus: false, baum: true})")
    assert page.evaluate("({haus: !!(AppState.selectedLemmas && AppState.selectedLemmas.haus), baum: !!(AppState.selectedLemmas && AppState.selectedLemmas.baum)})") == {"haus": False, "baum": True}

    # Now fulfill the held read with older state {haus: true, baum: false}
    # Because membershipRevision changed, the older read must NOT overwrite {baum: true}!
    first_read = held_reads.pop(0)
    # Allow the subsequent retry read to unroute and fetch real storage
    page.unroute("**/internal/selection")
    rows[1][headers.index("DeskSelected")] = "1"
    adapter.save_session(
        session_zid=zid, slug="reconnect-gap", source_language="de", target_language="en",
        text_mode="single", source_raw_text="Das Haus. Der Baum.",
        headers=headers, data_rows=rows, working_tsv_path=path,
    )
    first_read.fulfill(status=200, json={"ok": True, "shared_selection": {"haus": True, "baum": False}})

    page.wait_for_function("document.getElementById('sse-status-indicator') && document.getElementById('sse-status-indicator').getAttribute('data-sync') === 'synced'")
    visible = page.evaluate("({haus: !!(AppState.selectedLemmas && AppState.selectedLemmas.haus), baum: !!(AppState.selectedLemmas && AppState.selectedLemmas.baum)})")
    # Older read was rejected by revision guard; latest state converged
    assert visible["baum"] is True, f"Older read must not have clobbered baum: {visible}"


def test_phase3_sync_feedback_and_failure_retry(page, tmp_path):
    html = audit_html(tmp_path)
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))

    # Mock /internal/selection to fail
    page.evaluate("""() => {
        window.__syncAttempts = 0;
        const origFetch = window.fetch;
        window.fetch = function(url, options) {
            if (String(url).includes('/internal/selection')) {
                window.__syncAttempts++;
                return Promise.resolve({ok: false, status: 503, json: async () => ({ok: false, status: 'error'})});
            }
            return origFetch(url, options);
        };
    }""")

    error = page.evaluate("""() => {
        SSEBridge.connect('20261023221555');
        return window.reconcileStreamMembership({
            session: SSEBridge.getActiveZid(), connectionId: SSEBridge.getActiveConnectionId()
        }).then(() => null, error => error.message);
    }""")
    assert error == 'Selection reconciliation failed with status 503'
    page.wait_for_function("document.getElementById('sse-status-indicator') && document.getElementById('sse-status-indicator').getAttribute('data-sync') === 'failed'", timeout=5000)
    attempts = page.evaluate("window.__syncAttempts")
    assert attempts >= 3, f"Expected at least 3 bounded retries, got {attempts}"




def test_f3_css_disabled_output_suppresses_stylesheets_and_fetches(page):
    config, paths, goldendict, _ = kardenwort_desk.load_config()
    goldendict["disable_css"] = True
    zid = "20261025211003"
    html = kardenwort_desk.render_lookup_html(
        "A house.", "en", "fr", config, paths, zid, goldendict,
        [], ["WordSource", "WordDestination"], [["house", "maison"]], "Une maison.",
        session_zid=zid, api_token="test-controller-api-key", server_enabled=True,
    )
    css_links = [line for line in html.splitlines() if "<link" in line and "stylesheet" in line]
    assert css_links == [], f"Expected 0 stylesheet links when disable_css=True, found: {css_links}"

    css_requests = []
    page.route("**/css/**", lambda route: (css_requests.append(route.request.url), route.fulfill(status=200, body="")))
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    assert css_requests == [], f"Expected 0 CSS fetches when disable_css=True, requested: {css_requests}"


def test_f4_translation_natural_growth_no_nested_scrolling(page, tmp_path):
    page.set_viewport_size({"width": 1280, "height": 900})
    html = audit_html(tmp_path)
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))

    long_sentence = "A complete multiline translated sentence that provides substantial content."
    long_translation = "\n".join([long_sentence] * 40)

    page.evaluate("receiveUpdate", {"translatedText": long_translation})

    has_internal_scroll = page.locator("#translation-container").evaluate("el => el.scrollHeight > el.clientHeight")
    assert not has_internal_scroll, "Translation container must not have internal scrollbars"

    dynamic_height = page.locator("#translation-container").evaluate("el => el.getBoundingClientRect().height")
    page.evaluate("text => { const el = document.getElementById('translation-container'); el.style.removeProperty('--kw-text-region-size'); el.textContent = text; }", long_translation)
    fresh_height = page.locator("#translation-container").evaluate("el => el.getBoundingClientRect().height")
    assert abs(dynamic_height - fresh_height) < 2.0, f"Dynamic height {dynamic_height} must match fresh render height {fresh_height}"


def test_f5_alt_hand_tool_covers_all_tokens_and_native_selection(page, tmp_path):
    html = audit_html(tmp_path)
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))

    page.evaluate("window.setSelectableTextMode(true, false)")

    words = page.locator("#source-container .word, #translation-container .word")
    count = words.count()
    assert count > 0
    cursors = [words.nth(i).evaluate("el => getComputedStyle(el).cursor") for i in range(count)]
    non_text_cursors = [c for c in cursors if c != "text"]
    assert non_text_cursors == [], f"All word tokens must have cursor: text under Alt, found: {non_text_cursors}"

    metrics_alt = page.locator("#source-container .word").first.evaluate("el => { const s = getComputedStyle(el); return {pad: s.padding, mar: s.margin, border: s.border}; }")
    page.evaluate("window.setSelectableTextMode(false, false)")
    metrics_idle = page.locator("#source-container .word").first.evaluate("el => { const s = getComputedStyle(el); return {pad: s.padding, mar: s.margin, border: s.border}; }")
    assert metrics_alt == metrics_idle, f"Token box geometry must remain invariant across modes: alt={metrics_alt} vs idle={metrics_idle}"

