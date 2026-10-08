import json
import pytest
from tests.test_ui_interactions import extract_desk_js


def make_container_html(zid="20261008172016", cards=None, dom_rows="", dom_trans=""):
    if cards is None:
        cards = []
    cards_json = json.dumps(cards)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body data-web-mode="true" data-zid="{zid}">
<div class="container">
  <div class="kw-workspace-tab-bar" id="kw-workspace-tab-bar">
    <div class="kw-tab-track" id="kw-tab-track">
      <button type="button" class="kw-tab-chip active" data-tab-seq="1" data-sentence-idx="0">All</button>
      <button type="button" class="kw-tab-chip" data-tab-seq="2" data-sentence-idx="1">1</button>
      <button type="button" class="kw-tab-chip" data-tab-seq="3" data-sentence-idx="2">2</button>
    </div>
  </div>
  <div id="source-container"><span>Das Haus ist gross.</span></div>
  <div id="translation-container" class="translation-text">{dom_trans}</div>
  <table id="lemma-table">
    <tbody>{dom_rows}</tbody>
  </table>
</div>
<script id="sentence-cards" type="application/json">
{cards_json}
</script>
<div id="session-zid" style="display:none;">{zid}</div>
</body>
</html>"""


def test_incident_reproduction_container_starts_watchdog_from_sibling_skeletons(page):
    """
    Task 4.1: Container page with pre-filled active card (0 DOM skeletons) and
    skeletons only in a sibling card starts polling, opens EventSource, and queries
    /session/status within 2 seconds.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "The house is big.",
            "words": [
                {
                    "row_id": "0",
                    "token_order": 0,
                    "lemma": "Haus",
                    "translation": "house",
                    "row_html": '<tr data-row-id="0" data-token-order="0"><td data-col="WordSource">Haus</td><td data-col="WordDestination"><div class="scrollable-cell">house</div></td></tr>'
                }
            ]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "The house is big.",
            "words": [
                {
                    "row_id": "0",
                    "token_order": 0,
                    "lemma": "Haus",
                    "translation": "house",
                    "row_html": '<tr data-row-id="0" data-token-order="0"><td data-col="WordSource">Haus</td><td data-col="WordDestination"><div class="scrollable-cell">house</div></td></tr>'
                }
            ]
        },
        {
            "seq_num": 3,
            "sentence_idx": 2,
            "translated_text": "",
            "words": [
                {
                    "row_id": "1",
                    "token_order": 1,
                    "lemma": "gross",
                    "translation": '<span class="skeleton-loader" data-pending="true">Google...</span>',
                    "row_html": '<tr data-row-id="1" data-token-order="1"><td data-col="WordSource">gross</td><td data-col="WordDestination"><div class="scrollable-cell"><span class="skeleton-loader" data-pending="true">Google...</span></div></td></tr>'
                }
            ]
        }
    ]

    dom_rows = '<tr data-row-id="0" data-token-order="0"><td data-col="WordSource">Haus</td><td data-col="WordDestination"><div class="scrollable-cell">house</div></td></tr>'
    dom_trans = "The house is big."

    page.set_content(make_container_html("20261008172016", cards=cards, dom_rows=dom_rows, dom_trans=dom_trans))

    page.evaluate("""() => {
        window.__statusQueries = [];
        window.__sseConnectCount = 0;

        window.EventSource = function(url) {
            this.url = url;
            this.readyState = 1;
            window.__sseConnectCount++;
            var self = this;
            this.close = function() { self.readyState = 2; };
        };

        window.fetch = function(url, options) {
            var urlStr = String(url);
            window.__statusQueries.push({ url: urlStr, time: Date.now() });
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "success",
                        data: { stage: "translating", is_finished: false }
                    });
                },
                text: function() { return Promise.resolve(""); }
            });
        };
    }""")

    page.evaluate(extract_desk_js())

    # Active DOM has 0 skeletons initially
    assert page.locator("#lemma-table .skeleton-loader").count() == 0

    # But unified watchdog is actively running!
    assert page.evaluate("window.countPendingSkeletons() > 0") is True
    assert page.evaluate("window._kwSkeletonPollTimer !== null") is True
    assert page.evaluate("window.__sseConnectCount") == 1

    # Status request dispatched within 2 seconds
    page.wait_for_timeout(500)
    assert page.evaluate("window.__statusQueries.length >= 1") is True
    assert page.evaluate("window.__statusQueries[0].url.indexOf('/session/status') !== -1") is True


def test_count_pending_skeletons_parsing_and_no_double_count(page):
    """
    Task 4.2:
    - countPendingSkeletons() before WorkspaceTabs.init() returns > 0 from #sentence-cards
    - active card with 3 skeleton cells in DOM and 3 in card model returns exactly 3 (no double counting).
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "Ready text",
            "words": [
                {
                    "row_id": "0",
                    "token_order": 0,
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="0"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                },
                {
                    "row_id": "1",
                    "token_order": 1,
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="1"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                },
                {
                    "row_id": "2",
                    "token_order": 2,
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="2"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                }
            ]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "Sentence 1",
            "words": []
        }
    ]

    dom_rows = """
      <tr data-row-id="0"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>
      <tr data-row-id="1"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>
      <tr data-row-id="2"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>
    """

    page.set_content(make_container_html("20261008172017", cards=cards, dom_rows=dom_rows, dom_trans="Ready text"))
    page.evaluate(extract_desk_js())

    # Double-count guard: 3 DOM elements + 3 words in active card = exactly 3
    count = page.evaluate("window.countPendingSkeletons()")
    assert count == 3


def test_non_resolution_when_is_finished_false_keeps_polling(page):
    """
    Task 4.3: Non-resolution: status is_finished = false clearing only the active card
    keeps polling and does not call onSessionReload.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "",
            "words": [
                {
                    "row_id": "0",
                    "token_order": 0,
                    "lemma": "Haus",
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="0"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                }
            ]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "",
            "words": [
                {
                    "row_id": "1",
                    "token_order": 1,
                    "lemma": "gross",
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="1"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                }
            ]
        }
    ]

    dom_rows = '<tr data-row-id="0"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
    page.set_content(make_container_html("20261008172018", cards=cards, dom_rows=dom_rows))

    page.evaluate("""() => {
        window.__reloaded = false;
        window.onSessionReload = function() { window.__reloaded = true; };
    }""")
    page.evaluate(extract_desk_js())

    # Status returns clearing active card row 0, but is_finished is false
    page.evaluate("""() => {
        window.receiveUpdate({
            stage: "translating",
            is_finished: false,
            rows: {
                "0": { "trans": "house", "token_order": 0 }
            }
        });
    }""")

    # Active DOM has 0 skeletons now, but sibling card 2 is still pending
    assert page.evaluate("window.countPendingSkeletons() > 0") is True
    assert page.evaluate("window._kwSkeletonPollTimer !== null") is True
    assert page.evaluate("window.__reloaded") is False


def test_resolution_stops_polling_and_closes_sse(page):
    """
    Task 4.4: Resolution: status is_finished = true stops polling, closes SSE,
    and unified count becomes 0.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "",
            "words": [
                {
                    "row_id": "0",
                    "token_order": 0,
                    "lemma": "Haus",
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="0"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                }
            ]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "",
            "words": [
                {
                    "row_id": "1",
                    "token_order": 1,
                    "lemma": "gross",
                    "translation": '<span class="skeleton-loader" data-pending="true">...</span>',
                    "row_html": '<tr data-row-id="1"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
                }
            ]
        }
    ]

    dom_rows = '<tr data-row-id="0"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">...</span></td></tr>'
    page.set_content(make_container_html("20261008172019", cards=cards, dom_rows=dom_rows))

    page.evaluate("""() => {
        window.EventSource = function(url) {
            this.url = url;
            this.readyState = 1;
            var self = this;
            this.close = function() { self.readyState = 2; };
        };
    }""")
    page.evaluate(extract_desk_js())

    assert page.evaluate("window._kwSkeletonPollTimer !== null") is True
    assert page.evaluate("window._kwEvtSource !== null") is True

    # Deliver terminal finished status
    page.evaluate("""() => {
        window.receiveUpdate({
            stage: "finished",
            is_finished: true,
            translatedText: "The house is big.",
            rows: {
                "0": { "trans": "house", "token_order": 0 },
                "1": { "trans": "big", "token_order": 1 }
            }
        });
        if (window._kwEvtSource && window._kwEvtSource.onmessage) {
            window._kwEvtSource.onmessage({
                data: JSON.stringify({
                    stage: "finished",
                    is_finished: true,
                    rows: {
                        "0": { "trans": "house", "token_order": 0 },
                        "1": { "trans": "big", "token_order": 1 }
                    }
                })
            });
        }
    }""")

    assert page.evaluate("window._kwSkeletonPollTimer === null") is True
    assert page.evaluate("window._kwEvtSource === null") is True
    assert page.evaluate("window.countPendingSkeletons()") == 0


def test_probe_zero_pending_container_page(page):
    """
    Task 4.5: Probe: zero pending container page issues exactly one status request
    and no poll timer when finished; starts polling when busy.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "The house is big.",
            "words": [
                { "row_id": "0", "token_order": 0, "lemma": "Haus", "translation": "house", "row_html": '<tr><td>Haus</td><td>house</td></tr>' }
            ]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "The house is big.",
            "words": [
                { "row_id": "0", "token_order": 0, "lemma": "Haus", "translation": "house", "row_html": '<tr><td>Haus</td><td>house</td></tr>' }
            ]
        }
    ]

    dom_rows = '<tr><td>Haus</td><td>house</td></tr>'
    page.set_content(make_container_html("20261008172020", cards=cards, dom_rows=dom_rows, dom_trans="The house is big."))

    # Case A: Backend is finished
    page.evaluate("""() => {
        window.__probeCalls = 0;
        var origFetch = window.fetch;
        window.fetch = function(url, options) {
            var urlStr = String(url);
            if (urlStr.indexOf('/session/') !== -1 && urlStr.indexOf('/status') !== -1) {
                window.__probeCalls++;
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: function() { return Promise.resolve({ status: "success", data: { is_finished: true, stage: "finished" } }); }
                });
            }
            return origFetch ? origFetch.apply(this, arguments) : Promise.resolve({ ok: true, json: function() { return Promise.resolve({}); } });
        };
    }""")
    page.evaluate(extract_desk_js())

    page.wait_for_timeout(300)
    assert page.evaluate("window.__probeCalls") == 1
    assert page.evaluate("!window._kwSkeletonPollTimer") is True

    # Case B: Backend is busy (translating) -> awakened watchdog starts polling
    page.set_content(make_container_html("20261008172021", cards=cards, dom_rows=dom_rows, dom_trans="The house is big."))
    page.evaluate("""() => {
        window.__probeCalls = 0;
        var origFetch = window.fetch;
        window.fetch = function(url, options) {
            var urlStr = String(url);
            if (urlStr.indexOf('/session/') !== -1 && urlStr.indexOf('/status') !== -1) {
                window.__probeCalls++;
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: function() { return Promise.resolve({ status: "success", data: { is_finished: false, stage: "translating" } }); }
                });
            }
            return origFetch ? origFetch.apply(this, arguments) : Promise.resolve({ ok: true, json: function() { return Promise.resolve({}); } });
        };
    }""")
    page.evaluate(extract_desk_js())

    page.wait_for_timeout(300)
    assert page.evaluate("window.__probeCalls >= 1") is True
    assert page.evaluate("window._kwSkeletonPollTimer !== null") is True


def test_tab_switching_timer_identity_and_rate_limit(page):
    """
    Task 4.6: Tab switching while running keeps the same timer identity and SSE instance count;
    rapid switching after stop invokes initWatchdog() at most once per 5 s.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "",
            "words": [{ "row_id": "0", "token_order": 0, "translation": '<span class="skeleton-loader">...</span>' }]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "",
            "words": [{ "row_id": "1", "token_order": 1, "translation": '<span class="skeleton-loader">...</span>' }]
        }
    ]
    dom_rows = '<tr><td><span class="skeleton-loader">...</span></td></tr>'
    page.set_content(make_container_html("20261008172022", cards=cards, dom_rows=dom_rows))

    page.evaluate("""() => {
        window.__sseInstances = 0;
        window.EventSource = function(url) {
            this.url = url;
            this.readyState = 1;
            window.__sseInstances++;
            var self = this;
            this.close = function() { self.readyState = 2; };
        };
        window.fetch = function() { return Promise.resolve({ ok: true, status: 200, json: function() { return Promise.resolve({}); } }); };
    }""")
    page.evaluate(extract_desk_js())

    initial_timer = page.evaluate("window._kwSkeletonPollTimer")
    assert initial_timer is not None
    assert page.evaluate("window.__sseInstances") == 1

    # Switch tabs multiple times while running
    page.evaluate("""() => {
        window.WorkspaceTabs.switchToTab(2);
        window.WorkspaceTabs.switchToTab(1);
        window.WorkspaceTabs.switchToTab(2);
    }""")

    # Timer identity and SSE instance count remain unchanged
    assert page.evaluate("window._kwSkeletonPollTimer") == initial_timer
    assert page.evaluate("window.__sseInstances") == 1

    # Stop watchdog manually
    page.evaluate("""() => {
        clearInterval(window._kwSkeletonPollTimer);
        window._kwSkeletonPollTimer = null;
        if (window._kwEvtSource) { window._kwEvtSource.close(); window._kwEvtSource = null; }
    }""")

    # Rapid tab switching after stop
    page.evaluate("""() => {
        window.__initCalls = 0;
        var origInit = window.initWatchdog;
        window.initWatchdog = function() {
            window.__initCalls++;
            return origInit.apply(this, arguments);
        };

        window.WorkspaceTabs.switchToTab(1);
        window.WorkspaceTabs.switchToTab(2);
        window.WorkspaceTabs.switchToTab(1);
        window.WorkspaceTabs.switchToTab(2);
    }""")

    # Rate limited to at most 1 initWatchdog call within the 5s window
    assert page.evaluate("window.__initCalls <= 1") is True


def test_terminal_normalization_mounts_no_skeletons(page):
    """
    Task 4.7: Terminal normalization: after timeout strip and after completion,
    switching to a previously pending card mounts no .skeleton-loader and issues no status request.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "Overview",
            "words": [{ "row_id": "0", "token_order": 0, "translation": "ready", "row_html": '<tr><td>ready</td></tr>' }]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "",
            "words": [
                {
                    "row_id": "1",
                    "token_order": 1,
                    "translation": '<span class="skeleton-loader" data-pending="true">Google...</span>',
                    "row_html": '<tr data-row-id="1"><td data-col="WordDestination"><span class="skeleton-loader" data-pending="true">Google...</span></td></tr>'
                }
            ]
        }
    ]
    dom_rows = '<tr><td>ready</td></tr>'
    page.set_content(make_container_html("20261008172023", cards=cards, dom_rows=dom_rows, dom_trans="Overview"))
    page.evaluate(extract_desk_js())

    page.evaluate("""() => {
        window.__netRequests = 0;
        window.fetch = function() {
            window.__netRequests++;
            return Promise.resolve({ ok: true, status: 200, json: function() { return Promise.resolve({}); } });
        };

        // Complete the session
        window.AppState.isFinished = true;
        window.normalizeCardSkeletons('finished');
    }""")

    # Switch to card 2 which previously had skeleton markers
    page.evaluate("window.WorkspaceTabs.switchToTab(2);")

    # No skeleton-loader mounted!
    assert page.locator(".skeleton-loader").count() == 0
    assert page.locator("[data-pending='true']").count() == 0
    assert page.evaluate("window.__netRequests") == 0


def test_listener_accumulation_visibility_change_single_request(page):
    """
    Task 4.8: Listener accumulation: three initWatchdog() calls followed by
    hidden -> visible dispatch exactly one status request.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "",
            "words": [{ "row_id": "0", "token_order": 0, "translation": '<span class="skeleton-loader">...</span>' }]
        }
    ]
    dom_rows = '<tr><td><span class="skeleton-loader">...</span></td></tr>'
    page.set_content(make_container_html("20261008172024", cards=cards, dom_rows=dom_rows))

    page.evaluate("""() => {
        window.fetch = function() { return Promise.resolve({ ok: true, status: 200, json: function() { return Promise.resolve({}); } }); };
    }""")
    page.evaluate(extract_desk_js())

    # Call initWatchdog three times
    page.evaluate("""() => {
        window.initWatchdog();
        window.initWatchdog();
        window.initWatchdog();

        window.__visibilityStatusQueries = 0;
        var origPoll = window._kwPollSessionStatus;
        window._kwPollSessionStatus = function() {
            window.__visibilityStatusQueries++;
        };
    }""")

    # Toggle hidden -> visible
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', { value: true, configurable: true });
        document.dispatchEvent(new Event('visibilitychange'));
        Object.defineProperty(document, 'hidden', { value: false, configurable: true });
        document.dispatchEvent(new Event('visibilitychange'));
    }""")

    # Exactly 1 poll status request triggered from the visibility handler
    assert page.evaluate("window.__visibilityStatusQueries") == 1


def test_inactive_card_hydration_by_token_order(page):
    """
    Task 4.9: Inactive card hydration by token_order from a globally keyed status payload.
    """
    cards = [
        {
            "seq_num": 1,
            "sentence_idx": 0,
            "translated_text": "Overview",
            "words": [{ "row_id": "0", "token_order": 0, "translation": "ready", "row_html": '<tr><td>ready</td></tr>' }]
        },
        {
            "seq_num": 2,
            "sentence_idx": 1,
            "translated_text": "The cat.",
            "words": [
                {
                    "row_id": "1",
                    "token_order": 17,
                    "lemma": "Katze",
                    "translation": '<span class="skeleton-loader">Google...</span>',
                    "row_html": '<tr data-row-id="1" data-token-order="17"><td data-col="WordDestination"><span class="skeleton-loader">Google...</span></td></tr>'
                }
            ]
        }
    ]
    dom_rows = '<tr><td>ready</td></tr>'
    page.set_content(make_container_html("20261008172025", cards=cards, dom_rows=dom_rows, dom_trans="Overview"))
    page.evaluate(extract_desk_js())

    # Receive update keyed by global index 17
    page.evaluate("""() => {
        window.receiveUpdate({
            rows: {
                "17": {
                    "token_order": 17,
                    "lemma": "Katze",
                    "trans": "cat"
                }
            }
        });
    }""")

    # Inactive card word should be updated and cached row_html cleared
    card2_word = page.evaluate("window.WorkspaceTabs.getCards()[1].words[0]")
    assert card2_word["translation"] == "cat"
    assert card2_word["row_html"] is None

    # Switching to tab 2 renders "cat" without skeletons
    page.evaluate("window.WorkspaceTabs.switchToTab(2);")
    assert page.locator(".skeleton-loader").count() == 0
    assert "cat" in page.locator("tr[data-token-order='17'] td[data-col='WordDestination']").inner_text()
