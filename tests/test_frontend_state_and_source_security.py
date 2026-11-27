"""Phase 1 Playwright reproductions for REG-1 (sqlite-state.js) and FE-C3 (SourceViewer.js XSS).

Change: 20261127194156-fix-frontend-state-security-rendering-and-admin (Task 1.1)
Specs: specs/static-web-shell/spec.md
"""
import pytest
from playwright.sync_api import expect

from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_sqlite_state_browser import ready, sqlite_page  # noqa: F401


@pytest.mark.parametrize("trigger_second_save", [True, False])
def test_reg1_fresh_revision_zero_transient_500_recovers_and_allows_update(
    sqlite_page, trigger_second_save
):
    """REG-1: A transient 500 on first save of a fresh revision===0 session must recover."""
    page, url, server, adapter, _ = sqlite_page
    page.goto(url + "/")
    ready(page)

    # Confirm fresh session starts at revision 0 before any client-view commit.
    view_posts = []
    fail_once = {"remaining": 1}

    def handle_client_view(route):
        body = route.request.post_data_json or {}
        if "active_card" in body:
            view_posts.append(body)
            if fail_once["remaining"] > 0:
                fail_once["remaining"] -= 1
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body='{"ok":false,"message":"Transient view save failure"}',
                )
                return
        route.continue_()

    page.route("**/api/v1/client-view", handle_client_view)

    # First user view change triggers POST /api/v1/client-view with expected_revision=0 -> 500.
    with page.expect_response(
        lambda resp: resp.url.endswith("/api/v1/client-view") and resp.status == 500
    ):
        page.evaluate("WorkspaceTabs.switchToTab(2)")

    assert len(view_posts) == 1
    assert view_posts[0]["expected_revision"] == 0

    # Endpoint has now recovered (fail_once['remaining'] == 0 -> real controller HTTP 200).
    if trigger_second_save:
        # Next user view change must NOT be permanently ignored by viewInactive().
        with page.expect_response(
            lambda resp: resp.url.endswith("/api/v1/client-view") and resp.status == 200,
            timeout=3000,
        ):
            page.evaluate("WorkspaceTabs.switchToTab(3)")
        assert len(view_posts) == 2

    # Clicking #kw-btn-update runs flushPendingSaves() -> stateClient.flush() -> /api/v1/actions/update.
    update_requests = []

    def handle_update(route):
        update_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok":true,"status":"accepted"}',
        )

    page.route("**/api/v1/actions/update", handle_update)

    if not trigger_second_save:
        # Without a second tab switch, flush() itself must retryFailedView() and succeed.
        with page.expect_response(
            lambda resp: resp.url.endswith("/api/v1/client-view") and resp.status == 200,
            timeout=3000,
        ):
            with page.expect_request(
                lambda req: req.url.endswith("/api/v1/actions/update"),
                timeout=3000,
            ):
                page.locator("#kw-btn-update").click()
    else:
        with page.expect_request(
            lambda req: req.url.endswith("/api/v1/actions/update"),
            timeout=3000,
        ):
            page.locator("#kw-btn-update").click()

    assert len(update_requests) == 1
    assert len(view_posts) >= 2
    toast_text = page.locator("#kw-toast-container").inner_text()
    assert "Save failed; action stopped" not in toast_text


def test_fe_c3_source_viewer_project_blocks_xss_and_preserves_angle_brackets(sqlite_page):
    """FE-C3: SourceViewer.project() must never inject raw HTML into probe/container innerHTML."""
    page, url, server, adapter, _ = sqlite_page
    page.goto(url + "/")
    ready(page)

    xss_pending = 'Hello <img src=x onerror="window.__xssPending=(window.__xssPending||0)+1"> world'
    xss_delta = 'Delta <img src=x onerror="window.__xssDelta=(window.__xssDelta||0)+1"> text'

    # Case A: Pending #source-container projection.
    result_pending = page.evaluate(
        """(payload) => {
            window.__xssPending = 0;
            const c = document.getElementById('source-container');
            c.classList.add('skeleton-loader');
            c.setAttribute('data-pending', 'true');
            c.innerHTML = '<span class="skeleton-loader" data-pending="true">Loading...</span>';
            window.SourceViewer.project({ sourceText: payload }, { payload: {} });
            return {
                imgCount: c.querySelectorAll('img').length,
                text: c.textContent,
            };
        }""",
        xss_pending,
    )
    # Yield a frame so any injected <img src=x onerror=...> has fired.
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 50)))")
    assert page.evaluate("window.__xssPending") == 0
    assert result_pending["imgCount"] == 0
    assert '<img src=x onerror=' in result_pending["text"]

    # Case B: UPDATE_DELTAS projection through AppStore.
    result_delta = page.evaluate(
        """(payload) => {
            window.__xssDelta = 0;
            window.AppStore.applyDeltas({ sourceText: payload });
            const c = document.getElementById('source-container');
            return {
                imgCount: c.querySelectorAll('img').length,
                text: c.textContent,
            };
        }""",
        xss_delta,
    )
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 50)))")
    assert page.evaluate("window.__xssDelta") == 0
    assert result_delta["imgCount"] == 0
    assert '<img src=x onerror=' in result_delta["text"]

    # Case C: Benign angle brackets / ampersands / quotes preserved without double-escaping.
    benign_render = 'a < b && c > d "ok"'
    rendered_text = page.evaluate(
        """(text) => {
            window.SourceViewer.render({ source_text: text });
            return document.getElementById('source-container').textContent;
        }""",
        benign_render,
    )
    assert rendered_text == benign_render

    benign_project = 'x < y && z > w "ok"'
    projected_text = page.evaluate(
        """(text) => {
            window.SourceViewer.project({ sourceText: text }, { payload: { sourceText: text } });
            return document.getElementById('source-container').textContent;
        }""",
        benign_project,
    )
    assert projected_text == benign_project
