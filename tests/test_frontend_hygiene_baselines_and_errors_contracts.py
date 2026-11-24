"""Phase 1 real-boundary reproduction & baseline tests for geometry, scans, errors, rollback, init, and audio.

Change: 20261122213501-stabilize-frontend-sse-focus-columns-and-js-hygiene (Task 1.3)
Specs:
  - specs/same-lemma-highlight-toggle/spec.md
  - specs/table-selection-model/spec.md
  - specs/static-web-shell/spec.md
"""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_client_views import ClientViewAdapter
from tests.test_preference_commands import preference_world  # noqa: F401
from tests.test_same_lemma_toggle import (
    _open_row_highlight_document,
    _row_highlight_document,
)
from tests.test_sqlite_state_browser import ready, sqlite_page  # noqa: F401
from tests.test_static_web_shell import test_server  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("theme", ["dark", "light", "white"])
def test_1_3_geometry_theme_editor_tokens_and_large_view_scan_baseline(page, tmp_path, theme):
    """Capture exact wrapped geometry, theme editor tokens, zero hot geometry reads, and scan/query baselines."""
    page.set_viewport_size({"width": 360, "height": 900})

    # Representative large wrapped fixture: 120 tokens across repeated lemmas + compound tokens.
    words = []
    rows = []
    for idx in range(40):
        words.extend(["Haus", "E-Mail", "Baum"])
    source_text = " ".join(words) + "."
    rows = [
        ("Haus", "Haus", "noun", 1),
        ("E-Mail", "E-Mail", "noun", 1),
        ("Baum", "Baum", "noun", 1),
    ]
    html, _ = _row_highlight_document(tmp_path, source_text, rows, theme=theme)
    _open_row_highlight_document(page, html)

    geometry_js = """() => {
        const rect = b => [b.x, b.y, b.width, b.height];
        const sc = document.getElementById('source-container');
        const r = document.createRange();
        r.selectNodeContents(sc);
        return {
            container: rect(sc.getBoundingClientRect()),
            lines: Array.from(r.getClientRects()).map(rect),
            words: Array.from(sc.querySelectorAll('span.word')).map(w => rect(w.getBoundingClientRect()))
        };
    }"""
    before_geom = page.evaluate(geometry_js)
    assert len({round(w[1], 1) for w in before_geom["words"]}) > 3, "Fixture must wrap across multiple lines"

    # Measure DOM query counts, nested scan counts, and hot geometry reads during updateBidirectionalHighlights().
    metrics = page.evaluate(
        """() => {
            const origQSA = document.querySelectorAll.bind(document);
            const origQS = document.querySelector.bind(document);
            const origGetAttr = Element.prototype.getAttribute;
            const origRect = Element.prototype.getBoundingClientRect;
            const origRects = Element.prototype.getClientRects;
            let qsaCount = 0, qsCount = 0, attrReads = 0, geomReads = 0;

            document.querySelectorAll = function() { qsaCount++; return origQSA.apply(this, arguments); };
            document.querySelector = function() { qsCount++; return origQS.apply(this, arguments); };
            Element.prototype.getAttribute = function() { attrReads++; return origGetAttr.apply(this, arguments); };
            Element.prototype.getBoundingClientRect = function() { geomReads++; return origRect.apply(this, arguments); };
            Element.prototype.getClientRects = function() { geomReads++; return origRects.apply(this, arguments); };

            try {
                const span = origQS('#source-container span.word[data-lower-clean="haus"]');
                span.click();
                origQS('#kw-btn-same-lemma').click();
                qsaCount = 0; qsCount = 0; attrReads = 0; geomReads = 0;
                window.updateBidirectionalHighlights();
                return {
                    tokenCount: origQSA('#source-container span.word').length,
                    qsaCount,
                    qsCount,
                    attrReads,
                    geomReads,
                };
            } finally {
                document.querySelectorAll = origQSA;
                document.querySelector = origQS;
                Element.prototype.getAttribute = origGetAttr;
                Element.prototype.getBoundingClientRect = origRect;
                Element.prototype.getClientRects = origRects;
            }
        }"""
    )
    (tmp_path / f"baseline-metrics-{theme}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # Zero geometry reads inside highlight loop and exact line-box invariance.
    assert metrics["geomReads"] == 0, f"Geometry reads occurred in hot highlight loop: {metrics}"
    assert page.evaluate(geometry_js) == before_geom, f"Text/line geometry shifted in {theme} theme"
    # Bounded query budget: highlighting 160 spans must NOT issue O(N) DOM queries per pass.
    assert metrics["qsCount"] + metrics["qsaCount"] <= 12, (
        f"Excessive DOM queries inside updateBidirectionalHighlights ({metrics})"
    )

    # Cell editor input must use theme tokens rather than hardcoded dark hex inline styles.
    page.evaluate(
        """() => {
            const cell = document.querySelector('#lemma-table tbody tr[data-row-id="0"] td[data-col="WordDestination"]');
            window.makeEditable(cell);
        }"""
    )
    dest_cell = page.locator('#lemma-table tbody tr[data-row-id="0"] td[data-col="WordDestination"]')
    editor = dest_cell.locator("input.cell-editor")
    expect(editor).to_be_visible()
    inline_bg = editor.evaluate("el => el.style.background || el.style.backgroundColor")
    inline_color = editor.evaluate("el => el.style.color")
    assert inline_bg == "" and inline_color == "", (
        f"Cell editor uses hardcoded inline theme override instead of CSS theme tokens ({theme}): "
        f"bg={inline_bg!r}, color={inline_color!r}; baseline_metrics={metrics}"
    )
    page.keyboard.press("Escape")


def test_1_3_non_json_action_errors_and_delete_validation_surface_sanitized_feedback(page, test_server):
    """Non-JSON 502 HTML responses on actions/state/audio and expected Delete errors must show clean feedback."""
    port = test_server
    base_url = f"http://127.0.0.1:{port}"
    html_502 = "<html><body><h1>502 Bad Gateway</h1></body></html>"

    sample_bootstrap = {
        "ui_config": {"highlight_same_lemma": False, "filter_selected_only": False, "theme": "dark"},
        "sentence_cards": [
            {
                "seq_num": 1,
                "selected_ids": [],
                "words": [{"word": "Fuchs", "lemma": "Fuchs", "row_id": 0, "token_order": 0}],
            }
        ],
        "delivery_mode": "container",
        "session_data": {
            "zid": "20261124213201",
            "session_zid": "20261124213201",
            "language": "de",
            "target_lang": "ru",
            "source_text": "Ein Fuchs.",
            "sentence_translation": "Лиса.",
            "headers": [
                "WordSourceInflectedForm",
                "WordSource",
                "WordSourcePOS",
                "WordDestination",
                "SentenceSourceIndex",
                "TokenOrder",
            ],
            "data_rows": [["Fuchs", "Fuchs", "n.", "лиса", "1", "0"]],
            "server_enabled": True,
            "api_token": "bootstrap-token",
        },
    }

    for pattern in (
        "**/session/save",
        "**/session/retext",
        "**/session/reword",
        "**/session/export",
        "**/api/v1/tag",
        "**/api/v1/client-view",
        "**/api/v1/audio/synthesize",
    ):
        page.route(pattern, lambda route: route.fulfill(status=502, content_type="text/html", body=html_502))

    page_errors = []
    unhandled = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(f"{base_url}/index.html")
    page.evaluate(
        """(boot) => {
            window.addEventListener('unhandledrejection', ev => {
                window.__unhandledRejections = window.__unhandledRejections || [];
                window.__unhandledRejections.push(String(ev.reason));
            });
            document.getElementById('kardenwort-bootstrap').textContent = JSON.stringify(boot);
            document.dispatchEvent(new Event('DOMContentLoaded'));
        }""",
        sample_bootstrap,
    )
    page.wait_for_selector("#lemma-table tbody tr[data-row-id='0']")

    # 1. Non-JSON error on Retext must surface sanitized toast without raw JSON SyntaxError.
    with page.expect_response("**/session/retext"):
        page.locator("#kw-btn-retext").click()
    page.wait_for_function("document.getElementById('kw-toast-container').innerText.trim().length > 0")
    retext_toast = page.locator("#kw-toast-container").inner_text()
    assert "Unexpected token" not in retext_toast and "<html>" not in retext_toast, (
        f"Non-JSON Retext error leaked raw parser/HTML error: {retext_toast!r}"
    )

    # 2. Non-JSON error on Audio synthesis must surface sanitized error message.
    page.evaluate("document.getElementById('kw-toast-container').innerHTML = ''")
    with page.expect_response("**/api/v1/audio/synthesize"):
        page.evaluate("window.playAudio('Fuchs', 'de')")
    page.wait_for_function("document.getElementById('kw-toast-container').innerText.trim().length > 0")
    audio_toast = page.locator("#kw-toast-container").inner_text()
    assert "Unexpected token" not in audio_toast and "<html>" not in audio_toast, (
        f"Non-JSON audio error leaked raw parser/HTML error: {audio_toast!r}"
    )

    # 3. Non-JSON error on stateRequest (/api/v1/client-view) must not leak JSON parse error.
    state_err = page.evaluate(
        """() => {
            return fetch('/api/v1/client-view', {method: 'POST', body: '{}'}).then(() => {
                return window.saveClientView ? window.saveClientView().then(() => 'ok', e => String(e)) : 'no-client';
            });
        }"""
    )
    assert "Unexpected token" not in str(state_err)

    # 4. Expected Delete validation error (missing occurrence coordinates) must be caught cleanly with toast.
    page.evaluate(
        """() => {
            document.getElementById('kw-toast-container').innerHTML = '';
            const tr = document.querySelector('#lemma-table tbody tr[data-row-id="0"]');
            tr.removeAttribute('data-occurrence-targets');
            tr.removeAttribute('data-sentence-idx');
            tr.removeAttribute('data-token-order');
            tr.classList.add('selected', 'kw-row-selected');
            tr.setAttribute('data-selected', '1');
            window.onDeleteClick();
        }"""
    )
    delete_toast = page.locator("#kw-toast-container").inner_text()
    assert "occurrence" in delete_toast.lower() or "delete" in delete_toast.lower(), (
        f"Expected sanitized toast on invalid Delete coordinates, got: {delete_toast!r}"
    )
    unhandled = page.evaluate("window.__unhandledRejections || []")
    assert page_errors == [], f"Uncaught page errors on non-JSON/Delete failures: {page_errors}"
    assert unhandled == [], f"Unhandled promise rejections on non-JSON failures: {unhandled}"


def test_1_3_empty_live_update_clears_metadata_atomically_and_failed_edit_save_rolls_back(page, test_server):
    """Empty terminal live update must clear stale metadata, and rejected manual edit must roll back consistently."""
    port = test_server
    base_url = f"http://127.0.0.1:{port}"
    sample_bootstrap = {
        "ui_config": {"highlight_same_lemma": False, "filter_selected_only": False, "theme": "dark"},
        "sentence_cards": [
            {
                "seq_num": 1,
                "selected_ids": [],
                "words": [{"word": "Fuchs", "lemma": "Fuchs", "row_id": 0, "token_order": 0}],
            }
        ],
        "delivery_mode": "container",
        "session_data": {
            "zid": "20261124213202",
            "session_zid": "20261124213202",
            "language": "de",
            "target_lang": "ru",
            "source_text": "Ein Fuchs.",
            "sentence_translation": "Лиса.",
            "headers": [
                "WordSourceInflectedForm",
                "WordSource",
                "WordSourcePOS",
                "WordDestination",
                "SentenceSourceIndex",
                "TokenOrder",
            ],
            "data_rows": [["Fuchs", "Fuchs", "n.", "лиса", "1", "0"]],
            "server_enabled": True,
            "api_token": "bootstrap-token",
        },
    }

    page.route(
        "**/session/save",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"ok": False, "message": "Rejected manual edit"}),
        ),
    )

    page.goto(f"{base_url}/index.html")
    page.evaluate(
        """(boot) => {
            document.getElementById('kardenwort-bootstrap').textContent = JSON.stringify(boot);
            document.dispatchEvent(new Event('DOMContentLoaded'));
        }""",
        sample_bootstrap,
    )
    page.wait_for_selector("#lemma-table tbody tr[data-row-id='0']")

    # Case A: Populate translation + provenance metadata, then send terminal empty live update.
    empty_meta = page.evaluate(
        """() => {
            window.AppState.rows['0'] = {
                row_id: 0, token_order: 0, sentence_idx: 1,
                lemma: 'Fuchs', trans: 'лиса', provenance: 'live:google'
            };
            window.AppView.renderRow('0', 'translated_lemmas');
            window.AppState.rows['0'] = {
                row_id: 0, token_order: 0, sentence_idx: 1,
                lemma: 'Fuchs', trans: '', provenance: ''
            };
            window.AppState.isFinished = true;
            window.AppView.renderRow('0', 'finished');
            const td = document.querySelector('#lemma-table tbody tr[data-row-id="0"] td[data-col="WordDestination"]');
            const div = td.querySelector('.scrollable-cell');
            return {
                text: div.textContent,
                tdProv: td.getAttribute('data-provenance'),
                tdTitle: td.getAttribute('title'),
                tdOrigHtml: td.getAttribute('data-orig-html'),
                tdOrigTitle: td.getAttribute('data-orig-title'),
                divProv: div.getAttribute('data-provenance'),
                divTitle: div.getAttribute('title'),
            };
        }"""
    )
    assert empty_meta["text"] == ""
    assert empty_meta["tdProv"] is None, f"Stale data-provenance remained after empty update: {empty_meta}"
    assert empty_meta["tdTitle"] is None, f"Stale title remained after empty update: {empty_meta}"
    assert empty_meta["tdOrigHtml"] in (None, ""), f"Stale data-orig-html remained after empty update: {empty_meta}"
    assert empty_meta["tdOrigTitle"] is None, f"Stale data-orig-title remained after empty update: {empty_meta}"
    assert empty_meta["divProv"] is None and empty_meta["divTitle"] is None

    # Case B: Restore baseline cell value, edit manually, and reject /session/save -> visible failure + rollback.
    page.evaluate(
        """() => {
            window.AppState.rows['0'] = {
                row_id: 0, token_order: 0, sentence_idx: 1,
                lemma: 'Fuchs', trans: 'лиса', provenance: 'live:google'
            };
            window.AppView.renderRow('0', 'finished');
        }"""
    )
    dest_cell = page.locator("tr[data-row-id='0'] td[data-col='WordDestination']")
    dest_cell.dblclick()
    editor = dest_cell.locator("input.cell-editor")
    editor.fill("ошибочная лиса")
    page.keyboard.press("Enter")
    assert dest_cell.locator(".scrollable-cell").inner_text() == "ошибочная лиса"

    saved_ok = page.evaluate("window.onSaveClick(false)")
    assert saved_ok is False
    toast_text = page.locator("#kw-toast-container").inner_text()
    assert "Rejected manual edit" in toast_text
    assert dest_cell.locator(".scrollable-cell").inner_text() == "лиса", (
        "Rejected manual edit was not rolled back to last acknowledged value"
    )
    assert dest_cell.get_attribute("data-provenance") == "live:google"


def test_1_3_duplicate_startup_initialization_preserves_restored_active_card(sqlite_page):
    """Startup must not run duplicate hydrateSession/WorkspaceTabs.init passes or reset restored activeTabSeq."""
    page, url, server, adapter, _ = sqlite_page
    page.add_init_script(
        """(() => {
            window.__startupCalls = { hydrateSession: 0, workspaceTabsInit: 0 };
            let _hydrate;
            Object.defineProperty(window, 'hydrateSession', {
                configurable: true,
                get() { return _hydrate; },
                set(fn) {
                    _hydrate = typeof fn === 'function' ? function() {
                        window.__startupCalls.hydrateSession += 1;
                        return fn.apply(this, arguments);
                    } : fn;
                }
            });
            let _tabs;
            Object.defineProperty(window, 'WorkspaceTabs', {
                configurable: true,
                get() { return _tabs; },
                set(obj) {
                    if (obj && typeof obj.init === 'function' && !obj.__wrappedInit) {
                        const origInit = obj.init;
                        obj.init = function() {
                            window.__startupCalls.workspaceTabsInit += 1;
                            return origInit.apply(this, arguments);
                        };
                        obj.__wrappedInit = true;
                    }
                    _tabs = obj;
                }
            });
        })();"""
    )
    page.goto(url + "/")
    ready(page)
    startup_calls = page.evaluate("window.__startupCalls")

    # Persist active_card = 2 into real SQLite and reload so client restores card 2.
    page.evaluate("WorkspaceTabs.switchToTab(2)")
    page.evaluate("flushPendingSaves()")
    page.reload()
    ready(page)
    assert page.evaluate("WorkspaceTabs.getActiveTabSeq()") == 2

    # Re-trigger startup paths: duplicate DOMContentLoaded, hydrateSession(), and WorkspaceTabs.init().
    page.evaluate(
        """() => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            if (typeof window.hydrateSession === 'function') window.hydrateSession();
            if (window.WorkspaceTabs && window.WorkspaceTabs.init) window.WorkspaceTabs.init();
        }"""
    )
    assert page.evaluate("WorkspaceTabs.getActiveTabSeq()") == 2, (
        "Duplicate startup initialization reset restored activeTabSeq back to 1"
    )
    assert startup_calls["workspaceTabsInit"] == 1, (
        f"Duplicate WorkspaceTabs.init() invocations observed during normal startup: {startup_calls}"
    )


def test_1_3_audio_engine_switch_cancels_prior_engine_and_ignores_late_synthesis(page, test_server):
    """Switching audio engine must cancel prior engine transport/speech and ignore its late synthesis completion."""
    port = test_server
    base_url = f"http://127.0.0.1:{port}"
    page.goto(f"{base_url}/index.html")
    page.wait_for_function("typeof window.playAudio === 'function'")

    result = page.evaluate(
        """async () => {
            window.__fetchReqs = [];
            window.__audioPlayCalls = [];
            window.__speechSpeakCalls = [];
            window.__speechCancelCalls = 0;

            URL.createObjectURL = () => 'blob:test-audio-0';
            URL.revokeObjectURL = () => {};
            window.Audio = class extends EventTarget {
                constructor() { super(); this.paused = false; }
                play() { window.__audioPlayCalls.push(this.src); return Promise.resolve(); }
                pause() { this.paused = true; }
                removeAttribute(k) { delete this[k]; }
                load() {}
            };
            window.SpeechSynthesisUtterance = class {
                constructor(text) { this.text = text; this.lang = ''; }
            };
            Object.defineProperty(window, 'speechSynthesis', {
                configurable: true,
                value: {
                    speak(u) { window.__speechSpeakCalls.push({text: u.text, lang: u.lang}); },
                    cancel() { window.__speechCancelCalls += 1; }
                }
            });
            window.fetch = function(url, options) {
                return new Promise((resolve, reject) => {
                    window.__fetchReqs.push({url, options, resolve, reject});
                });
            };

            // 1. Start playback on anki_tts_cli (controller HTTP synthesis).
            window.__CONFIG__ = { audio_playback_engine: 'anki_tts_cli' };
            window.playAudio('altes Wort', 'de');
            const abortedBeforeSwitch = window.__fetchReqs[0].options.signal.aborted;

            // 2. Switch engine to browser_tts and trigger playback while old fetch is still in flight.
            window.__CONFIG__.audio_playback_engine = 'browser_tts';
            window.playAudio('neues Wort', 'de');
            const abortedAfterSwitch = window.__fetchReqs[0].options.signal.aborted;

            // 3. Resolve the stale anki_tts_cli synthesis response late.
            window.__fetchReqs[0].resolve({
                ok: true,
                status: 200,
                blob: () => Promise.resolve(new Blob(['RIFFdata'], {type: 'audio/wav'}))
            });
            for (let i = 0; i < 6; i++) await Promise.resolve();

            const audioPlaysAfterLateResolve = window.__audioPlayCalls.length;

            // 4. Switch back from browser_tts to anki_tts_cli -> must cancel browser speech synthesis.
            const cancelBeforeBackSwitch = window.__speechCancelCalls;
            window.__CONFIG__.audio_playback_engine = 'anki_tts_cli';
            window.playAudio('drittes Wort', 'de');
            const cancelAfterBackSwitch = window.__speechCancelCalls;

            return {
                fetchCount: window.__fetchReqs.length,
                abortedBeforeSwitch,
                abortedAfterSwitch,
                audioPlaysAfterLateResolve,
                speechSpeakCalls: window.__speechSpeakCalls,
                cancelBeforeBackSwitch,
                cancelAfterBackSwitch,
            };
        }"""
    )

    assert result["abortedBeforeSwitch"] is False
    assert result["abortedAfterSwitch"] is True, (
        f"Switching to browser_tts failed to abort prior anki_tts_cli fetch: {result}"
    )
    assert result["audioPlaysAfterLateResolve"] == 0, (
        f"Late prior-engine synthesis restarted old <audio> playback after engine switch: {result}"
    )
    assert len(result["speechSpeakCalls"]) == 1 and result["speechSpeakCalls"][0]["text"] == "neues Wort"
    assert result["cancelAfterBackSwitch"] > result["cancelBeforeBackSwitch"], (
        f"Switching back to anki_tts_cli failed to cancel active browser_tts speech: {result}"
    )
