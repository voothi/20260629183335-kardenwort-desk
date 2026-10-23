"""Phase 1 contracts: failures reproduce defects before production repairs.

Only the browser's outbound transport is controlled. Renderers, components,
store, selection commands and save acknowledgments execute their real code.
These cases do not claim SQLite or native-host end-to-end verification.
"""

import json

import pytest
from playwright.sync_api import expect

import kardenwort_desk


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
    window.__requests.push({url: String(url), body});
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
