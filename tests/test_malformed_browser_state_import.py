"""Acceptance and control tests for malformed legacy browser state recovery.

Exercises real HTTP controller routes, real isolated SQLite persistence, and
Playwright Chromium against the rendered web shell with zero internal mocks.
"""
import json

import pytest
from playwright.sync_api import expect

from kardenwort_state_http import config_snapshot
from scripts.benchmark_release_interactions import ZID, payload
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_release_boundary_contracts import post
from tests.test_sqlite_state_browser import ready, sqlite_page  # noqa: F401


OTHER_ZID = "20990101000000"


def _seed_storage_once(page, *, local=None, session=None):
    """Populate browser storage once per tab so reloads do not re-seed keys."""
    script = """({local, session}) => {
        if (sessionStorage.getItem('__kw_test_seeded')) return;
        sessionStorage.setItem('__kw_test_seeded', '1');
        for (const [k, v] of Object.entries(local || {})) localStorage.setItem(k, v);
        for (const [k, v] of Object.entries(session || {})) sessionStorage.setItem(k, v);
    }"""
    page.add_init_script(f"({script})({json.dumps({'local': local or {}, 'session': session or {}})})")


def _client_id(page):
    return page.evaluate("""() => {
        const match = location.hash.match(/(?:^#|&)kw-client=([0-9a-f]{32})(?:&|$)/);
        return match ? match[1] : null;
    }""")


def _first_lemma():
    return payload(3)[0][0]


@pytest.mark.parametrize(
    "malformed_raw,secret_marker",
    [
        ('{"secret_raw_leak":', "secret_raw_leak"),
        ("[unterminated_secret", "unterminated_secret"),
    ],
)
def test_malformed_token_json_offers_recovery_and_keeps_database_settings(
    sqlite_page, malformed_raw, secret_marker
):
    """Scenarios: Malformed token JSON + Keep database settings."""
    page, url, server, adapter, path = sqlite_page
    before_ini = path.read_bytes(), path.stat().st_mtime_ns
    assert post(url, "/api/v1/config", {"highlight_same_lemma": True})[0] == 200
    lemma = _first_lemma()
    assert adapter.update_word_selection(ZID, 1, 0, True) is True
    before_membership = adapter.get_shared_selection(ZID)
    assert before_membership[lemma] is True

    _seed_storage_once(
        page,
        local={
            "kw_highlight_same_lemma": "false",
            "unrelated_local_key": "keep-local",
        },
        session={
            f"kw_active_tokens_{ZID}": malformed_raw,
            f"kw_active_card_{ZID}": "2",
            f"kw_active_tokens_{OTHER_ZID}": "[]",
            "unrelated_session_key": "keep-session",
        },
    )

    page.goto(url + "/")
    dialog = page.get_by_test_id("legacy-state-import")
    expect(dialog).to_be_visible()

    assert page.locator("html").get_attribute("data-workspace-phase") == "initializing"
    dialog_text = dialog.inner_text()
    assert "malformed_json" in dialog_text
    assert secret_marker not in dialog_text
    assert malformed_raw not in dialog_text
    expect(dialog.get_by_role("button", name="Import browser settings")).to_have_count(0)
    keep_btn = dialog.get_by_role("button", name="Keep database settings")
    expect(keep_btn).to_be_visible()

    # Raw legacy values must stay intact until the user explicitly chooses recovery.
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") == malformed_raw
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_card_{ZID}") == "2"
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") == "false"

    import_requests = []
    page.on(
        "request",
        lambda req: import_requests.append(req.post_data_json)
        if req.url.endswith("/api/v1/browser-state/import")
        else None,
    )

    with page.expect_response(lambda r: r.url.endswith("/api/v1/browser-state/import") and r.ok):
        keep_btn.click()
    ready(page)

    assert page.locator("html").get_attribute("data-workspace-phase") == "ready"
    expect(dialog).to_have_count(0)

    client = _client_id(page)
    assert client is not None
    assert import_requests == [
        {
            "session_zid": ZID,
            "client_id": client,
            "preferences": {},
            "revisions": {},
        }
    ]

    # Only the relevant legacy import keys are retired.
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") is None
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_card_{ZID}") is None
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") is None
    assert page.evaluate("localStorage.getItem('kw_filter_selected_only')") is None
    assert page.evaluate("localStorage.getItem('unrelated_local_key')") == "keep-local"
    assert page.evaluate("sessionStorage.getItem('unrelated_session_key')") == "keep-session"
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{OTHER_ZID}") == "[]"

    # Committed SQLite state, shared membership, and static INI stay authoritative and unchanged.
    assert config_snapshot(server, {})["preferences"]["values"]["highlight_same_lemma"] is True
    assert page.evaluate("AppState.highlightSameLemma") is True
    assert adapter.get_shared_selection(ZID) == before_membership
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before_ini
    with adapter.db.get_connection(read_only=True) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM browser_state_imports WHERE client_id=?",
            (client,),
        ).fetchone()[0]
    assert count == 1

    # Reload restores directly with no dialog.
    page.reload()
    ready(page)
    expect(page.get_by_test_id("legacy-state-import")).to_have_count(0)
    assert page.evaluate("AppState.highlightSameLemma") is True


@pytest.mark.parametrize(
    "local_seed,session_seed,expected_category,secret_marker",
    [
        (
            {"kw_highlight_same_lemma": "bad_bool_secret"},
            {},
            "invalid_boolean",
            "bad_bool_secret",
        ),
        (
            {},
            {f"kw_active_card_{ZID}": "bad_card_secret"},
            "invalid_active_card",
            "bad_card_secret",
        ),
        (
            {},
            {f"kw_active_tokens_{ZID}": '{"not_array":"secret_shape"}'},
            "invalid_active_tokens",
            "secret_shape",
        ),
    ],
)
def test_malformed_legacy_boolean_card_and_token_shape_offer_recovery(
    sqlite_page, local_seed, session_seed, expected_category, secret_marker
):
    """Distinguish invalid booleans, cards, and token shapes without leaking raw values."""
    page, url, server, adapter, path = sqlite_page
    _seed_storage_once(page, local=local_seed, session=session_seed)

    page.goto(url + "/")
    dialog = page.get_by_test_id("legacy-state-import")
    expect(dialog).to_be_visible()

    dialog_text = dialog.inner_text()
    assert expected_category in dialog_text
    assert secret_marker not in dialog_text
    expect(dialog.get_by_role("button", name="Import browser settings")).to_have_count(0)

    for key, expected_val in local_seed.items():
        assert page.evaluate("k => localStorage.getItem(k)", key) == expected_val
    for key, expected_val in session_seed.items():
        assert page.evaluate("k => sessionStorage.getItem(k)", key) == expected_val

    dialog.get_by_role("button", name="Keep database settings").click()
    ready(page)

    assert page.locator("html").get_attribute("data-workspace-phase") == "ready"
    for key in local_seed:
        assert page.evaluate("k => localStorage.getItem(k)", key) is None
    for key in session_seed:
        assert page.evaluate("k => sessionStorage.getItem(k)", key) is None


def test_database_read_failure_stays_visible_even_with_malformed_legacy_json(sqlite_page):
    """Scenario: Database failure."""
    page, url, server, adapter, path = sqlite_page
    malformed_raw = "{corrupt_during_db_error"
    _seed_storage_once(page, session={f"kw_active_tokens_{ZID}": malformed_raw})

    # Trigger a genuine SQLite read failure on /api/v1/client-view.
    with adapter.db.get_connection() as conn:
        conn.execute("ALTER TABLE client_view_state RENAME TO client_view_state_offline")
    try:
        page.goto(url + "/")
        page.wait_for_function("document.documentElement.getAttribute('data-workspace-phase') === 'failed'")
        expect(page.locator("#kw-workspace-message")).to_contain_text("Workspace unavailable")
        expect(page.get_by_test_id("legacy-state-import")).to_have_count(0)
        assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") == malformed_raw
    finally:
        with adapter.db.get_connection() as conn:
            conn.execute("ALTER TABLE client_view_state_offline RENAME TO client_view_state")

    # Once SQLite is readable again, Retry reaches the malformed-legacy recovery choice.
    page.locator("#kw-workspace-retry").click()
    dialog = page.get_by_test_id("legacy-state-import")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Keep database settings").click()
    ready(page)
    assert page.locator("html").get_attribute("data-workspace-phase") == "ready"
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") is None


def test_import_decision_failure_preserves_malformed_legacy_keys(sqlite_page):
    """A failed authenticated recovery decision must not delete legacy keys."""
    page, url, server, adapter, path = sqlite_page
    malformed_raw = "{persist_until_commit"
    _seed_storage_once(page, session={f"kw_active_tokens_{ZID}": malformed_raw})

    page.goto(url + "/")
    dialog = page.get_by_test_id("legacy-state-import")
    expect(dialog).to_be_visible()

    with adapter.db.get_connection() as conn:
        conn.execute(
            "CREATE TRIGGER reject_malformed_recovery BEFORE INSERT ON browser_state_imports "
            "BEGIN SELECT RAISE(ABORT, 'owned recovery abort'); END"
        )
    try:
        with page.expect_request("**/api/v1/browser-state/import"):
            dialog.get_by_role("button", name="Keep database settings").click()
        expect(dialog).to_contain_text("Import not saved")
        expect(dialog.get_by_role("button", name="Import browser settings")).to_have_count(0)
        assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") == malformed_raw
        with adapter.db.get_connection(read_only=True) as conn:
            assert conn.execute("SELECT COUNT(*) FROM browser_state_imports").fetchone()[0] == 0
    finally:
        with adapter.db.get_connection() as conn:
            conn.execute("DROP TRIGGER IF EXISTS reject_malformed_recovery")

    dialog.get_by_role("button", name="Keep database settings").click()
    ready(page)
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") is None


@pytest.mark.parametrize("mode", ["getter", "getItem"])
def test_inaccessible_browser_storage_stays_explicit_failure(sqlite_page, mode):
    """Inaccessible storage must stay explicit and never masquerade as malformed content."""
    page, url, server, adapter, path = sqlite_page
    if mode == "getter":
        page.add_init_script(
            """(() => {
                Object.defineProperty(window, 'sessionStorage', {
                    configurable: true,
                    get() { throw new DOMException('Blocked storage access', 'SecurityError'); }
                });
            })();"""
        )
    else:
        page.add_init_script(
            """(() => {
                Storage.prototype.getItem = function() {
                    throw new DOMException('Blocked storage access', 'SecurityError');
                };
            })();"""
        )

    page.goto(url + "/")
    page.wait_for_function("document.documentElement.getAttribute('data-workspace-phase') === 'failed'")
    expect(page.locator("#kw-workspace-message")).to_contain_text("inaccessible_storage")
    expect(page.get_by_test_id("legacy-state-import")).to_have_count(0)


def test_valid_legacy_browser_state_adjacent_control(sqlite_page):
    """Valid adjacent control: well-formed legacy preferences and view still import cleanly."""
    page, url, server, adapter, path = sqlite_page
    before_ini = path.read_bytes(), path.stat().st_mtime_ns
    valid_tokens = json.dumps([
        {"sentence_idx": 1, "token_order": 0, "visual_idx": 0, "atomic_id": 0, "row_ids": [0], "lemma": _first_lemma()}
    ])
    _seed_storage_once(
        page,
        local={
            "kw_highlight_same_lemma": "true",
            "unrelated_local_key": "keep-local",
        },
        session={
            f"kw_active_card_{ZID}": "2",
            f"kw_active_tokens_{ZID}": valid_tokens,
            "unrelated_session_key": "keep-session",
        },
    )

    page.goto(url + "/")
    dialog = page.get_by_test_id("legacy-state-import")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Import browser settings")).to_be_visible()
    expect(dialog.get_by_role("button", name="Keep database settings")).to_be_visible()

    dialog.get_by_role("button", name="Import browser settings").click()
    ready(page)

    assert page.locator("html").get_attribute("data-workspace-phase") == "ready"
    assert page.evaluate("AppState.highlightSameLemma") is True
    assert page.evaluate("WorkspaceTabs.getActiveTabSeq()") == 2
    assert len(page.evaluate("AppState.activeTokenSelections")) == 1
    assert config_snapshot(server, {})["preferences"]["values"]["highlight_same_lemma"] is True
    assert page.evaluate("localStorage.getItem('kw_highlight_same_lemma')") is None
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_card_{ZID}") is None
    assert page.evaluate("k => sessionStorage.getItem(k)", f"kw_active_tokens_{ZID}") is None
    assert page.evaluate("localStorage.getItem('unrelated_local_key')") == "keep-local"
    assert page.evaluate("sessionStorage.getItem('unrelated_session_key')") == "keep-session"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before_ini
