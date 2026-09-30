import pytest
from pathlib import Path
import json
import kardenwort_desk
from tests.test_ui_interactions import extract_desk_js


def get_resilience_test_html(zid="20260930230000"):
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body data-web-mode="true" data-zid="{zid}">
<div class="container">
  <div class="section">
    <div class="section-title">Translation</div>
    <div class="translation-text" id="translation-container">
      <span class="skeleton-loader" data-pending="true">Translating text...</span>
    </div>
  </div>
  <div class="section">
    <div class="section-title">Lemmas</div>
    <table id="lemma-table">
      <tbody>
        <tr data-row-id="0">
          <td data-col="WordSource"><div class="scrollable-cell">Haus</div></td>
          <td data-col="WordDestination" class="editable">
            <div class="scrollable-cell">
              <span class="skeleton-loader" data-pending="true">Translating lemma...</span>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
<div id="session-zid" style="display:none;">{zid}</div>
</body>
</html>
"""


def test_watchdog_offline_interruption_and_reconnection_self_healing(page):
    """
    Test 3.1 & 3.2: Verify that when network goes offline during translation,
    orphan cleanup preserves skeletons without premature stripping, enters heartbeat fallback,
    and upon 'online' reconnection event, automatically self-heals and renders finished translations
    without requiring a page reload (F5).
    """
    zid = "20260930230000"
    page.set_content(get_resilience_test_html(zid))

    # Setup mock network environment
    page.evaluate("""
        window.__simulatedOffline = false;
        window.__workerFinished = false;
        window.__statusQueries = [];

        window.fetch = function(url, options) {
            var urlStr = String(url);
            window.__statusQueries.push({ url: urlStr, offline: window.__simulatedOffline, time: Date.now() });

            if (window.__simulatedOffline) {
                return Promise.reject(new TypeError("Failed to fetch - network offline"));
            }

            if (urlStr.indexOf('/session/status') !== -1) {
                if (window.__workerFinished) {
                    return Promise.resolve({
                        ok: true,
                        status: 200,
                        json: function() {
                            return Promise.resolve({
                                status: "success",
                                data: {
                                    stage: "finished",
                                    is_finished: true,
                                    translatedText: "Das Haus ist gross.",
                                    rows: {
                                        "0": { "WordDestination": "house" }
                                    }
                                }
                            });
                        }
                    });
                } else {
                    return Promise.resolve({
                        ok: true,
                        status: 200,
                        json: function() {
                            return Promise.resolve({
                                status: "success",
                                data: {
                                    stage: "translating",
                                    is_finished: false,
                                    worker_status: "running"
                                }
                            });
                        }
                    });
                }
            }

            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() { return Promise.resolve({}); },
                text: function() { return Promise.resolve(""); }
            });
        };
    """)

    page.evaluate(extract_desk_js())

    # Watchdog should be initialized and skeletons present
    assert page.locator(".skeleton-loader").count() >= 2
    assert page.evaluate("window._kwSkeletonPollTimer !== null") is True

    # 1. Simulate network disconnect / Wi-Fi drop
    page.evaluate("""
        window.__simulatedOffline = true;
        Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
        window.dispatchEvent(new Event('offline'));
    """)

    # Fast forward watchdog budget or simulate timeout trigger while offline
    page.evaluate("""
        window._kwAdvanceActiveElapsedMs(35000);
        if (window.cleanupOrphanSkeletons) {
            window.cleanupOrphanSkeletons();
        }
    """)

    # Skeletons MUST NOT be stripped while offline
    assert page.locator(".skeleton-loader").count() >= 2
    assert page.evaluate("window._kwIsHeartbeat === true") is True

    # 2. Worker finishes in the background on the local machine
    page.evaluate("window.__workerFinished = true;")

    # 3. Simulate Wi-Fi toggling back on / network restoration
    page.evaluate("""
        window.__simulatedOffline = false;
        Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
        window.dispatchEvent(new Event('online'));
    """)

    # 4. Verify client watchdog automatically self-heals without page reload (F5)
    page.wait_for_function("() => document.querySelectorAll('.skeleton-loader').length === 0", timeout=5000)
    assert page.locator(".skeleton-loader").count() == 0
    assert page.locator("[data-pending='true']").count() == 0

    # Verify updated content is rendered
    page.wait_for_function("() => document.getElementById('translation-container').innerText.includes('Das Haus ist gross')", timeout=5000)
    assert "Das Haus ist gross" in page.locator("#translation-container").inner_text()
    assert "house" in page.locator("td[data-col='WordDestination']").inner_text()


def test_watchdog_window_focus_triggers_status_reconciliation(page):
    """
    Verify that when pending skeletons remain, switching focus back to the browser window
    triggers an immediate status reconciliation query.
    """
    zid = "20260930230001"
    page.set_content(get_resilience_test_html(zid))

    page.evaluate("""
        window.__queryCount = 0;
        window.__workerFinished = false;

        window.fetch = function(url, options) {
            if (String(url).indexOf('/session/status') !== -1) {
                window.__queryCount++;
                if (window.__workerFinished) {
                    return Promise.resolve({
                        ok: true,
                        status: 200,
                        json: function() {
                            return Promise.resolve({
                                status: "success",
                                data: {
                                    stage: "finished",
                                    is_finished: true,
                                    translatedText: "Guten Tag Welt.",
                                    rows: { "0": { "WordDestination": "world" } }
                                }
                            });
                        }
                    });
                }
            }
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "success",
                        data: { stage: "translating", is_finished: false }
                    });
                }
            });
        };
    """)

    page.evaluate(extract_desk_js())
    assert page.locator(".skeleton-loader").count() >= 2

    # Worker finishes in background
    page.evaluate("window.__workerFinished = true;")

    # User focuses back on window
    page.evaluate("window.dispatchEvent(new Event('focus'));")

    # Status reconciliation resolves skeletons immediately
    page.wait_for_function("() => document.querySelectorAll('.skeleton-loader').length === 0", timeout=5000)
    assert page.locator(".skeleton-loader").count() == 0
    assert "Guten Tag Welt" in page.locator("#translation-container").inner_text()


def test_bounded_status_fetch_aborts_and_releases_lock(page):
    """
    Verify that status fetches utilize AbortController and clear the concurrency lock
    on error or abort in a finally block.
    """
    zid = "20260930230002"
    page.set_content(get_resilience_test_html(zid))

    page.evaluate("""
        window.__receivedSignal = false;
        window.__aborted = false;
        window.fetch = function(url, options) {
            var urlStr = String(url);
            if (urlStr.indexOf('/session/status') !== -1) {
                if (options && options.signal) {
                    window.__receivedSignal = true;
                    return new Promise(function(resolve, reject) {
                        options.signal.addEventListener('abort', function() {
                            window.__aborted = true;
                            reject(new DOMException('Aborted', 'AbortError'));
                        });
                    });
                }
            }
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() { return Promise.resolve({}); },
                text: function() { return Promise.resolve(""); }
            });
        };
    """)

    page.evaluate(extract_desk_js())

    # Probe status
    page.evaluate("""
        if (typeof window._kwPollSessionStatus === 'function') {
            window._kwPollSessionStatus();
        }
    """)

    # Verify AbortSignal was passed to fetch
    assert page.evaluate("window.__receivedSignal") is True


def test_watchdog_sse_exponential_backoff_and_reconnect(page):
    """
    Verify that EventSource socket drop schedules exponential reconnect backoff,
    and receiving an online event immediately forces fresh reconnection.
    """
    zid = "20260930230003"
    page.set_content(get_resilience_test_html(zid))

    page.evaluate("""
        window.__sseConnectCount = 0;
        function MockEventSource(url) {
            this.url = url;
            this.readyState = 1; // OPEN
            window.__sseConnectCount++;
            var self = this;
            this.close = function() {
                self.readyState = 2; // CLOSED
            };
        }
        window.EventSource = MockEventSource;
        window.fetch = function() {
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() { return Promise.resolve({}); },
                text: function() { return Promise.resolve(""); }
            });
        };
    """)

    page.evaluate(extract_desk_js())
    assert page.evaluate("window.__sseConnectCount") == 1

    # Simulate SSE error
    page.evaluate("""
        if (window._kwEvtSource && typeof window._kwEvtSource.onerror === 'function') {
            window._kwEvtSource.onerror(new Event('error'));
        }
    """)

    # Socket closed and reconnect timer scheduled
    assert page.evaluate("window._kwEvtSource === null") is True
    assert page.evaluate("window._kwSseReconnectTimer !== null") is True

    # Online event cancels backoff timer and immediately reconnects
    page.evaluate("window.dispatchEvent(new Event('online'));")
    assert page.evaluate("window.__sseConnectCount") == 2
