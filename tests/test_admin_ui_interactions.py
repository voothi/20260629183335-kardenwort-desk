import pytest
import json
from pathlib import Path

SAMPLE_SESSIONS = [
    {"zid": "20260822000001", "slug": "sess-1", "source_language": "de", "sentence_count": 5, "word_count": 50, "projects": [{"id": 1, "title": "Book 1"}], "source_raw_text": "Sample 1"},
    {"zid": "20260822000002", "slug": "sess-2", "source_language": "en", "sentence_count": 3, "word_count": 30, "projects": [], "source_raw_text": "Sample 2"},
    {"zid": "20260822000003", "slug": "sess-3", "source_language": "fr", "sentence_count": 8, "word_count": 90, "projects": [{"id": 2, "title": "Book 2"}], "source_raw_text": "Sample 3"},
]

@pytest.fixture
def admin_page(page):
    desk_dir = Path(__file__).resolve().parent.parent
    html_content = (desk_dir / "assets" / "admin.html").read_text(encoding="utf-8")
    js_content = (desk_dir / "assets" / "admin.js").read_text(encoding="utf-8")
    css_content = (desk_dir / "assets" / "admin.css").read_text(encoding="utf-8")

    # Disable DOMContentLoaded automatic network loads
    js_content = js_content.replace("document.addEventListener('DOMContentLoaded',", "window.__initApp = (")

    full_html = f"""<!DOCTYPE html>
<html>
<head><style>{css_content}</style></head>
<body class="theme-dark">
{html_content.split('<body class="theme-dark">')[1].split('<script src="/assets/admin.js"></script>')[0]}
<script>
{js_content}
</script>
</body>
</html>"""

    page.set_content(full_html)

    # Initialize tab and sessions explorer without network fetch
    page.evaluate("""(sessions) => {
        initSessionsExplorer();
        document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
        document.getElementById('tab-sessions').classList.add('active');

        window.state.sessionsExplorer.sessions = sessions;
        window.state.sessionsExplorer.totalCount = 100;
        window.state.sessionsExplorer.pageSize = 3;
        window.state.sessionsExplorer.page = 1;
        window.renderSessionsExplorerTable(sessions);
    }""", SAMPLE_SESSIONS)

    return page


def test_admin_sessions_batch_selection_and_highlight(admin_page):
    page = admin_page

    # 1. Verify rows rendered with checkboxes
    rows = page.locator("#sessions-table-body tr")
    assert rows.count() == 3

    # Initially no batch toolbar
    assert page.locator("#sessions-batch-toolbar").evaluate("el => el.classList.contains('hidden')") is True

    # 2. Click first row checkbox
    first_chk = page.locator("tr[data-zid='20260822000001'] .session-row-checkbox")
    first_chk.click()

    # Verify first row is selected and highlighted (yellow desk highlight)
    first_row = page.locator("tr[data-zid='20260822000001']")
    assert "row-selected" in (first_row.get_attribute("class") or "")
    assert page.locator("#sessions-batch-toolbar").evaluate("el => el.classList.contains('hidden')") is False
    assert "1 selected" in page.locator("#sessions-selected-count").inner_text()

    # Master checkbox should be indeterminate
    assert page.locator("#master-sessions-checkbox").evaluate("el => el.indeterminate") is True

    # 3. Master Checkbox - Select All on page
    master_chk = page.locator("#master-sessions-checkbox")
    master_chk.click()

    assert page.locator("tr[data-zid='20260822000001']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000002']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000003']").evaluate("el => el.classList.contains('row-selected')") is True
    assert "3 selected" in page.locator("#sessions-selected-count").inner_text()

    # Cross-page banner should appear since totalCount (100) > page count (3)
    assert page.locator("#sessions-selection-banner").evaluate("el => el.classList.contains('hidden')") is False
    assert "Select all 100 sessions in library" in page.locator("#btn-select-all-matching").inner_text()

    # 4. Click cross-page selection link
    page.locator("#btn-select-all-matching").click()
    assert "100 selected" in page.locator("#sessions-selected-count").inner_text()
    assert "Clear selection" in page.locator("#btn-select-all-matching").inner_text()

    # 5. Clear selection
    page.locator("#btn-batch-clear-selection").click()
    assert page.locator("#sessions-batch-toolbar").evaluate("el => el.classList.contains('hidden')") is True
    assert page.locator("#sessions-selection-banner").evaluate("el => el.classList.contains('hidden')") is True
    assert master_chk.evaluate("el => el.checked") is False


def test_admin_sessions_dropdown_filters(admin_page):
    page = admin_page

    # 1. Open master dropdown
    page.locator("#btn-master-dropdown-toggle").click()
    assert page.locator("#master-dropdown-menu").evaluate("el => el.classList.contains('hidden')") is False

    # 2. Select "Unassigned"
    page.locator(".dropdown-item[data-select='unassigned']").click()

    # Should select only sess-2 (sess-1 has book 1, sess-3 has book 2)
    assert page.locator("tr[data-zid='20260822000001']").evaluate("el => el.classList.contains('row-selected')") is False
    assert page.locator("tr[data-zid='20260822000002']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000003']").evaluate("el => el.classList.contains('row-selected')") is False
    assert "1 selected" in page.locator("#sessions-selected-count").inner_text()

    # 3. Select "Assigned"
    page.locator("#btn-master-dropdown-toggle").click()
    page.locator(".dropdown-item[data-select='assigned']").click()

    assert page.locator("tr[data-zid='20260822000001']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000002']").evaluate("el => el.classList.contains('row-selected')") is False
    assert page.locator("tr[data-zid='20260822000003']").evaluate("el => el.classList.contains('row-selected')") is True
    assert "2 selected" in page.locator("#sessions-selected-count").inner_text()


def test_admin_sessions_shift_click_and_row_click(admin_page):
    page = admin_page

    # 1. Click on the text area of row 1 (not directly on checkbox)
    page.locator("tr[data-zid='20260822000001'] td:nth-child(3)").click()
    assert page.locator("tr[data-zid='20260822000001']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000001'] .session-row-checkbox").evaluate("el => el.checked") is True

    # 2. Shift+Click on row 3 checkbox to select range (1 to 3)
    page.locator("tr[data-zid='20260822000003'] .session-row-checkbox").click(modifiers=["Shift"])

    assert page.locator("tr[data-zid='20260822000001']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000002']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000003']").evaluate("el => el.classList.contains('row-selected')") is True
    assert "3 selected" in page.locator("#sessions-selected-count").inner_text()


def test_admin_sessions_batch_assign_interaction(admin_page):
    page = admin_page

    # Mock apiFetch to intercept batch-link and projects list
    page.evaluate("""() => {
        window.__batchLinkCalls = [];
        window.apiFetch = async (url, opts) => {
            if (url.includes('/api/v1/admin/projects/batch-link')) {
                window.__batchLinkCalls.push(JSON.parse(opts.body));
                return { ok: true, linked_count: 2 };
            }
            if (url.includes('/api/v1/admin/projects')) {
                return { projects: [{ id: 42, title: "Target Book", children: [] }] };
            }
            if (url.includes('/api/v1/admin/sessions')) {
                return { sessions: window.state.sessionsExplorer.sessions, total_count: 3 };
            }
            return { ok: true };
        };
        initModals();
    }""")

    # 1. Select first two rows
    page.locator("tr[data-zid='20260822000001'] .session-row-checkbox").click()
    page.locator("tr[data-zid='20260822000002'] .session-row-checkbox").click()

    # 2. Verify "+ Assign to Node" button is visible in batch toolbar
    batch_assign_btn = page.locator("#btn-batch-assign-sessions")
    assert batch_assign_btn.is_visible() is True
    assert "+ Assign to Node" in batch_assign_btn.inner_text()

    # 3. Click "+ Assign to Node"
    batch_assign_btn.click()

    # 4. Modal should open in batch mode
    assign_modal = page.locator("#assign-project-modal")
    assert assign_modal.evaluate("el => !el.classList.contains('hidden')") is True
    assert "Assign 2 Sessions to Project Node" in page.locator("#assign-modal-title").inner_text()
    assert page.locator("#assign-modal-mode").input_value() == "batch"

    # 5. Target node select options populated
    select_el = page.locator("#assign-project-select")
    assert select_el.locator("option").count() >= 1

    # 6. Click Save/Assign button
    page.locator("#btn-assign-modal-save").click()

    # 7. Modal is hidden
    assert assign_modal.evaluate("el => el.classList.contains('hidden')") is True

    # 8. Verify backend was called with expected batch-link payload
    calls = page.evaluate("() => window.__batchLinkCalls")
    assert len(calls) == 1
    assert calls[0]["project_id"] == 42
    assert calls[0]["mode"] == "explicit"
    assert "20260822000001" in calls[0]["session_zids"]
    assert "20260822000002" in calls[0]["session_zids"]

    # 9. Verify rows remain selected and highlighted (user requirement!)
    assert page.locator("tr[data-zid='20260822000001']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000002']").evaluate("el => el.classList.contains('row-selected')") is True
    assert page.locator("tr[data-zid='20260822000001'] .session-row-checkbox").evaluate("el => el.checked") is True
    assert page.locator("tr[data-zid='20260822000002'] .session-row-checkbox").evaluate("el => el.checked") is True
    assert "2 selected" in page.locator("#sessions-selected-count").inner_text()


