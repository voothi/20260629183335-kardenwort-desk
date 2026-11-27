"""Phase 1 Playwright reproductions for /admin FE-H13, FE-M8, and FE-L3.

Change: 20261127194156-fix-frontend-state-security-rendering-and-admin (Task 1.3)
Spec: specs/admin-sessions-explorer/spec.md
"""
import pytest
from playwright.sync_api import expect

from tests.controller_test_support import controller_world  # noqa: F401
from tests.test_admin_persisted_controls import admin_contract, create_project, submit  # noqa: F401

pytestmark = pytest.mark.parametrize(
    "controller_world", [{"backend": "sqlite", "seed_cache": False}], indirect=True
)


_CONTRAST_HELPER_JS = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    function parseRgb(str) {
        const m = String(str).match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
        if (!m) return null;
        return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: m[4] === undefined ? 1 : Number(m[4]) };
    }
    function effectiveBg(node) {
        let cur = node;
        while (cur && cur.nodeType === 1) {
            const bg = parseRgb(getComputedStyle(cur).backgroundColor);
            if (bg && bg.a > 0) return bg;
            cur = cur.parentElement;
        }
        return { r: 255, g: 255, b: 255, a: 1 };
    }
    function relLum(c) {
        const ch = [c.r, c.g, c.b].map(v => {
            const s = v / 255;
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
    }
    const fg = parseRgb(getComputedStyle(el).color);
    const bg = effectiveBg(el);
    const l1 = Math.max(relLum(fg), relLum(bg));
    const l2 = Math.min(relLum(fg), relLum(bg));
    return {
        fg,
        bg,
        ratio: (l1 + 0.05) / (l2 + 0.05),
    };
}"""


@pytest.mark.parametrize("theme_class", ["theme-dark", "theme-light", "theme-white"])
def test_fe_h13_admin_contrast_across_dark_light_white_themes(admin_contract, theme_class):
    """FE-H13: TSV viewer, code block, modal close hover, and dropdown hover must maintain >= 4.5:1 contrast."""
    page, db = admin_contract
    page.evaluate("(cls) => { document.body.className = cls; }", theme_class)

    # 1. Telemetry .code-block (#raw-telemetry-json)
    page.locator('[data-tab="tab-telemetry"]').click()
    expect(page.locator("#raw-telemetry-json")).to_be_visible()
    code_contrast = page.evaluate(_CONTRAST_HELPER_JS, "#raw-telemetry-json")
    assert code_contrast["ratio"] >= 4.5, f"{theme_class} .code-block contrast too low: {code_contrast}"

    # 2. Sessions dropdown .dropdown-item:hover
    page.locator('[data-tab="tab-sessions"]').click()
    expect(page.locator('#sessions-table-body tr[data-zid="20261112080001"]')).to_be_visible()
    page.locator("#btn-master-dropdown-toggle").click()
    dropdown_item = page.locator('#master-dropdown-menu .dropdown-item[data-select="all"]')
    expect(dropdown_item).to_be_visible()
    dropdown_item.hover()
    dropdown_contrast = page.evaluate(_CONTRAST_HELPER_JS, '#master-dropdown-menu .dropdown-item[data-select="all"]')
    assert dropdown_contrast["ratio"] >= 4.5, f"{theme_class} .dropdown-item:hover contrast too low: {dropdown_contrast}"

    # 3. TSV inspector .tsv-viewer-textarea & .modal-close:hover
    page.locator('tr[data-zid="20261112080001"]').get_by_role("button", name="TSV", exact=True).click()
    expect(page.locator("#tsv-raw-viewer")).to_be_visible()
    tsv_contrast = page.evaluate(_CONTRAST_HELPER_JS, "#tsv-raw-viewer")
    assert tsv_contrast["ratio"] >= 4.5, f"{theme_class} .tsv-viewer-textarea contrast too low: {tsv_contrast}"

    close_btn = page.locator("#btn-tsv-modal-close")
    close_btn.hover()
    close_contrast = page.evaluate(_CONTRAST_HELPER_JS, "#btn-tsv-modal-close")
    assert close_contrast["ratio"] >= 4.5, f"{theme_class} .modal-close:hover contrast too low: {close_contrast}"


def test_fe_m8_confirm_modal_enter_respects_cancel_and_close_focus(admin_contract):
    """FE-M8: Pressing Enter while Cancel or Close is focused must resolve false and never confirm destruction."""
    page, db = admin_contract

    # Focus Cancel and press Enter -> must resolve false.
    page.evaluate("() => { window.__confirmResult = null; showConfirmDialog('Delete item?').then(v => { window.__confirmResult = v; }); }")
    expect(page.locator("#confirm-modal")).to_be_visible()
    page.locator("#btn-confirm-cancel").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#confirm-modal")).to_be_hidden()
    assert page.evaluate("window.__confirmResult") is False

    # Focus modal close [x] and press Enter -> must resolve false.
    page.evaluate("() => { window.__confirmResult = null; showConfirmDialog('Delete item?').then(v => { window.__confirmResult = v; }); }")
    expect(page.locator("#confirm-modal")).to_be_visible()
    page.locator("#btn-confirm-modal-close").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#confirm-modal")).to_be_hidden()
    assert page.evaluate("window.__confirmResult") is False

    # Focus Confirm OK and press Enter -> must resolve true.
    page.evaluate("() => { window.__confirmResult = null; showConfirmDialog('Delete item?').then(v => { window.__confirmResult = v; }); }")
    expect(page.locator("#confirm-modal")).to_be_visible()
    page.locator("#btn-confirm-ok").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#confirm-modal")).to_be_hidden()
    assert page.evaluate("window.__confirmResult") is True


def test_fe_m8_async_races_in_tsv_inspector_and_sessions_explorer(admin_contract):
    """FE-M8: Stale TSV inspector and sessions explorer responses must be cancelled/discarded."""
    page, db = admin_contract
    page.locator('[data-tab="tab-sessions"]').click()
    expect(page.locator('#sessions-table-body tr[data-zid="20261112080001"]')).to_be_visible()

    # Part A: TSV inspector async race (Session A slow, Session B fast, then Session A resolves).
    held_tsv = []

    def intercept_tsv_a(route):
        held_tsv.append(route)

    page.route("**/api/v1/sessions/20261112080001/tsv", intercept_tsv_a)
    page.route(
        "**/api/v1/sessions/20261112080002/tsv",
        lambda route: route.fulfill(
            status=200,
            content_type="text/tab-separated-values",
            body="WordSource\tWordDestination\nSessionB\tWinner\n",
        ),
    )

    try:
        with page.expect_request("**/api/v1/sessions/20261112080001/tsv", timeout=3000):
            page.locator('tr[data-zid="20261112080001"]').get_by_role("button", name="TSV", exact=True).click()
        assert len(held_tsv) == 1
        # Close modal and open Session B before Session A finishes.
        page.locator("#btn-tsv-modal-close").click()
        page.locator('tr[data-zid="20261112080002"]').get_by_role("button", name="TSV", exact=True).click()
        expect(page.locator("#tsv-raw-viewer")).to_have_value("WordSource\tWordDestination\nSessionB\tWinner\n")

        # Now release stale Session A response (if not already aborted).
        try:
            held_tsv[0].fulfill(
                status=200,
                content_type="text/tab-separated-values",
                body="WordSource\tWordDestination\nSessionA\tStaleLoser\n",
            )
        except Exception:
            # Already aborted by AbortController — expected and clean.
            pass
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 50)))")
        assert "SessionB" in page.locator("#tsv-raw-viewer").input_value()
        assert "StaleLoser" not in page.locator("#tsv-raw-viewer").input_value()
        page.locator("#btn-tsv-modal-close").click()

        # Part B: loadSessionsExplorer async race (Query A slow, Query B fast, then Query A resolves).
        held_sessions = []

        def intercept_sessions(route):
            if "query=slow-race-a" in route.request.url:
                held_sessions.append(route)
                return
            if "query=fast-race-b" in route.request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"sessions":[{"zid":"20261112080002","slug":"winner-b","source_language":"de","sentence_count":1,"word_count":1,"projects":[]}],"total_count":1}',
                )
                return
            route.continue_()

        page.route(lambda u: "/api/v1/admin/sessions?" in u, intercept_sessions)
        with page.expect_request(lambda req: "query=slow-race-a" in req.url, timeout=3000):
            page.evaluate("() => { window.state.sessionsExplorer.query = 'slow-race-a'; void window.loadSessionsExplorer(); }")
        expect(page.locator("#sessions-table-body td.loading-spinner")).to_be_visible()

        page.evaluate("() => { window.state.sessionsExplorer.query = 'fast-race-b'; void window.loadSessionsExplorer(); }")
        expect(page.locator('#sessions-table-body tr[data-zid="20261112080002"]')).to_be_visible()

        try:
            held_sessions[0].fulfill(
                status=200,
                content_type="application/json",
                body='{"sessions":[{"zid":"20261112080001","slug":"stale-loser-a","source_language":"de","sentence_count":1,"word_count":1,"projects":[]}],"total_count":1}',
            )
        except Exception:
            pass
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 50)))")
        zids = page.locator("#sessions-table-body tr[data-zid]").evaluate_all("els => els.map(e => e.dataset.zid)")
        assert zids == ["20261112080002"]
    finally:
        page.unroute_all(behavior="ignoreErrors")


def test_fe_m8_single_error_toast_on_api_failure(admin_contract):
    """FE-M8: Failed apiFetch callers must show exactly one error toast, not duplicates."""
    page, db = admin_contract
    page.route(
        "**/api/v1/admin/db/vacuum",
        lambda route: route.fulfill(status=500, content_type="application/json", body='{"message":"Vacuum boom"}'),
    )
    page.locator('[data-tab="tab-maintenance"]').click()
    page.evaluate("document.getElementById('toast-container').innerHTML = ''")
    with page.expect_response(lambda r: "/api/v1/admin/db/vacuum" in r.url):
        page.locator("#btn-vacuum-db").click()
    expect(page.locator("#vacuum-status")).to_have_text("Failed")
    assert page.locator("#toast-container .toast.error").count() == 1

    # Also check TSV import failure produces a single error toast.
    page.locator('[data-tab="tab-sessions"]').click()
    page.evaluate("document.getElementById('toast-container').innerHTML = ''")
    with page.expect_file_chooser() as chooser:
        page.locator("#btn-browse-tsv").click()
    with page.expect_response(lambda r: "/api/v1/sessions/import-tsv" in r.url):
        chooser.value.set_files(
            dict(name="20261127210003-bad.de.tsv", mimeType="text/tab-separated-values", buffer=b"")
        )
    expect(page.locator("#drop-zone-status")).to_contain_text("1 failed")
    assert page.locator("#toast-container .toast.error").count() == 1


def test_fe_m8_filtered_batch_assign_clears_vanished_selections(admin_contract):
    """FE-M8: Batch-assigning under assigned='unassigned' filter must clear vanished selections."""
    page, db = admin_contract
    project_id = create_project(page, db, "FilterTarget")

    page.locator('[data-tab="tab-sessions"]').click()
    with page.expect_response(lambda r: "/api/v1/admin/sessions?" in r.url and "assigned=unassigned" in r.url):
        page.locator("#sessions-assign-filter").select_option("unassigned")

    expect(page.locator('#sessions-table-body tr[data-zid="20261112080001"]')).to_be_visible()
    page.locator('tr[data-zid="20261112080001"] .session-row-checkbox').click()
    expect(page.locator("#sessions-selected-count")).to_have_text("1 selected")

    page.locator("#btn-batch-assign-sessions").click()
    page.locator("#assign-project-select").select_option(str(project_id))
    submit(page, "#btn-assign-modal-save", "/api/v1/admin/projects/batch-link")

    # 20261112080001 is now assigned and vanished from the unassigned table.
    expect(page.locator('#sessions-table-body tr[data-zid="20261112080001"]')).to_have_count(0)
    assert page.evaluate("window.state.sessionsExplorer.selectedZids.size") == 0
    assert page.evaluate("window.state.sessionsExplorer.allMatching") is False
    expect(page.locator("#sessions-batch-toolbar")).to_be_hidden()


def test_fe_l3_loading_row_colspan_and_active_theme_in_launch_urls(admin_contract):
    """FE-L3: Sessions loading row spans all 9 columns and launch URLs propagate active theme."""
    page, db = admin_contract
    page.locator('[data-tab="tab-sessions"]').click()
    expect(page.locator('#sessions-table-body tr[data-zid="20261112080001"]')).to_be_visible()

    # Part A: Loading row colspan must match the 9 header columns.
    held = []
    page.route(lambda u: "/api/v1/admin/sessions?" in u, lambda route: held.append(route))
    try:
        with page.expect_request(lambda req: "/api/v1/admin/sessions?" in req.url, timeout=3000):
            page.evaluate("() => { void window.loadSessionsExplorer(); }")
        expect(page.locator("#sessions-table-body td.loading-spinner")).to_be_visible()
        header_cols = page.locator("#sessions-data-table thead th").count()
        loading_colspan = page.locator("#sessions-table-body td.loading-spinner").get_attribute("colspan")
        assert header_cols == 9
        assert loading_colspan == "9"
    finally:
        for r in held:
            try:
                r.continue_()
            except Exception:
                pass
        page.unroute_all(behavior="ignoreErrors")

    # Part B: Active theme propagation in launch URLs.
    page.route(
        "**/api/v1/admin/projects/synthesize",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok":true,"session_zid":"20261127210099"}'
        ),
    )
    for body_cls, expected_theme in [("theme-light", "light"), ("theme-white", "white"), ("theme-dark", "dark")]:
        urls = page.evaluate(
            """async (cls) => {
                document.body.className = cls;
                const opened = [];
                window.open = (u) => {
                    const popup = { location: { href: u }, close() {} };
                    opened.push(popup);
                    return popup;
                };
                openSessionInDesk('20261112080001');
                openSessionInDict('20261112080001');
                openProjectInDict(7);
                await openProjectInDesk(7);
                return opened.map(p => p.location.href);
            }""",
            body_cls,
        )
        assert len(urls) == 4
        for launched in urls:
            assert f"theme={expected_theme}" in launched, f"Expected theme={expected_theme} in {launched}"
