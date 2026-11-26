"""Regression contracts for Phase 3 frontend state, SSE, admin, and zero-reflow CSS fixes."""
from pathlib import Path
import re
import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_table_css_editor_and_dirty_preserve_invariant_box_metrics():
    css = (ROOT / "web/css/table.css").read_text(encoding="utf-8")
    editor_block = re.search(r"td\.editing\s+input\.cell-editor[^{]*\{([^}]*)\}", css)
    assert editor_block, "cell-editor CSS block missing"
    body = editor_block.group(1)
    assert "border: 0;" in body
    assert "padding: 0;" in body
    assert "outline:" in body

    dirty_block = re.search(r"td\.dirty\s*\{([^}]*)\}", css)
    assert dirty_block, "td.dirty CSS block missing"
    assert "border-left:" not in dirty_block.group(1)
    assert "box-shadow:" in dirty_block.group(1)


def test_app_and_admin_static_hygiene_invariants():
    app_js = (ROOT / "web/js/app.js").read_text(encoding="utf-8")
    assert "tdsEl[2]" not in app_js
    assert "readJsonResponse(res, 'Re-word failed')" in app_js
    assert "readJsonResponse(res, 'Export failed')" in app_js
    assert "parsed.status === 400 || parsed.status === 422" in app_js

    tv_js = (ROOT / "web/js/components/TranslationView.js").read_text(encoding="utf-8")
    assert "SAFE_PREBUILT_WORD_SPAN" not in tv_js

    admin_js = (ROOT / "assets/admin.js").read_text(encoding="utf-8")
    assert "setTimeout(() => URL.revokeObjectURL(objectUrl), 10000)" in admin_js
    assert "Failed to open project in Desk:" in admin_js
    assert "Vacuum failed:" in admin_js


def test_sqlite_state_flush_retries_transient_view_failure_and_aborts_on_dispose(page):
    sqlite_state_js = (ROOT / "web/js/features/sqlite-state.js").read_text(encoding="utf-8")
    page.set_content("<!doctype html><html><body></body></html>")
    page.add_script_tag(content=sqlite_state_js)

    result = page.evaluate("""async () => {
        let failOnce = true;
        let signals = [];
        let revision = 1;
        const send = (path, body, method, signal) => {
            signals.push(Boolean(signal));
            if (path === '/api/v1/client-view' && body.expected_revision === undefined) {
                return Promise.resolve({ active_card: 1, active_tokens: [], revision: 1, browser_imported: true });
            }
            if (path === '/api/v1/client-view' && body.expected_revision !== undefined) {
                if (failOnce) {
                    failOnce = false;
                    return Promise.reject(new Error('Transient view timeout'));
                }
                revision += 1;
                return Promise.resolve({ active_card: body.active_card, active_tokens: body.active_tokens, revision });
            }
            return Promise.resolve({ ok: true });
        };
        let activeCard = 1;
        const client = window.SqliteState.create({
            ui: { state_authority: 'sqlite', client_view_enabled: true },
            zid: '20261126200001',
            send,
            connected: true,
            draft: false,
            current: () => true,
            unresolved: () => false,
            present: () => {},
            failure: () => {},
            project: () => {},
            capture: () => ({ active_card: activeCard, active_tokens: [] }),
            restore: () => {},
        });
        await client.start();
        activeCard = 2;
        await client.save(); // fails transiently -> phase becomes 'failed'
        // flush() must retry and succeed once backend recovers instead of permanently throwing
        await client.flush();
        return { revision, allHadSignal: signals.every(Boolean) };
    }""")
    assert result["revision"] == 2
    assert result["allHadSignal"] is True


def test_sse_tag_updated_handles_coordinates_and_special_chars_without_poison_crash(page):
    sse_js = (ROOT / "web/js/services/sse.js").read_text(encoding="utf-8")
    page.set_content("""<!doctype html><html><body>
        <table><tbody>
            <tr data-row-id="0" data-sentence-idx="1" data-token-order="2">
                <td><input class="kw-tag-checkbox" type="checkbox" data-row-id="0" data-sentence-idx="1" data-token-order="2"></td>
            </tr>
            <tr data-row-id="1" data-sentence-idx="2" data-token-order="0">
                <td><input class="kw-tag-checkbox" type="checkbox" data-row-id="1" data-sentence-idx="2" data-token-order="0"></td>
            </tr>
        </tbody></table>
    </body></html>""")
    page.add_script_tag(content=sse_js)

    outcome = page.evaluate("""() => {
        let captured = null;
        window.EventSource = function(url) {
            this.url = url;
            this.readyState = 1;
            this.close = () => { this.readyState = 2; };
            this.addEventListener = () => {};
            captured = this;
        };
        window.SSEBridge.connect('20261126200002', 'http://127.0.0.1:18335');
        // Malformed row_id must not throw querySelector SyntaxError
        captured.onmessage({ data: JSON.stringify({ type: 'tag_updated', row_id: '0"]', status: true }) });
        // Exact row_id=0 must check first row (token_order=2), not second row (token_order=0)
        captured.onmessage({ data: JSON.stringify({ type: 'tag_updated', row_id: 0, sentence_idx: 1, status: true }) });
        const cbs = Array.from(document.querySelectorAll('.kw-tag-checkbox')).map(el => el.checked);
        window.SSEBridge.disconnect();
        return cbs;
    }""")
    assert outcome == [True, False]


def test_workspace_presentation_hand_tool_shortcut_and_null_error_and_speech_utterance_ref(page):
    wp_js = (ROOT / "web/js/features/workspace-presentation.js").read_text(encoding="utf-8")
    audio_js = (ROOT / "web/js/features/audio.js").read_text(encoding="utf-8")
    page.set_content("""<!doctype html><html><body>
        <div id="kw-workspace-status" hidden><span id="kw-workspace-message"></span></div>
        <div class="kw-view-placeholder" hidden></div>
    </body></html>""")
    page.add_script_tag(content=wp_js)
    page.add_script_tag(content=audio_js)

    res = page.evaluate("""() => {
        // Null error must not throw TypeError
        window.WorkspacePresentation.project('failed', null);
        const msg = document.getElementById('kw-workspace-message').textContent;
        const statusVisibleOnFail = !document.getElementById('kw-workspace-status').hidden;

        // Ctrl+Shift+A (Hand Tool) must not be blocked while pending
        window.WorkspacePresentation.project('initializing');
        const statusHiddenOnInit = document.getElementById('kw-workspace-status').hidden;
        let defaultPrevented = false;
        const ev = new KeyboardEvent('keydown', { key: 'A', ctrlKey: true, shiftKey: true, cancelable: true });
        document.dispatchEvent(ev);
        defaultPrevented = ev.defaultPrevented;

        // BrowserSpeechPlayback keeps utterance ref and finishes multi-segment
        let spoken = [];
        window.SpeechSynthesisUtterance = function(t) { this.text = t; };
        Object.defineProperty(window, 'speechSynthesis', {
            configurable: true,
            value: {
                cancel: () => {},
                speak: (u) => { spoken.push(u.text); if (u.onend) u.onend(); }
            }
        });
        let states = [];
        const player = new window.DeskBrowserSpeechPlayback({
            onState: (s) => states.push(s),
            onError: () => {},
        });
        player.play('eins ||| zwei', 'de');
        return { msg, statusVisibleOnFail, statusHiddenOnInit, defaultPrevented, spoken, states };
    }""")
    assert "Unknown error" in res["msg"]
    assert res["statusVisibleOnFail"] is True
    assert res["statusHiddenOnInit"] is True
    assert res["defaultPrevented"] is False
    assert res["spoken"] == ["eins", "zwei"]
    assert res["states"][-1]["state"] == "finished"
