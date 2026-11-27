"""Phase 1 Playwright reproductions for FE-H9, FE-H10, FE-M2, FE-M3, FE-M4, and FE-L1/L2/L4.

Change: 20261127194156-fix-frontend-state-security-rendering-and-admin (Task 1.2)
Specs:
- specs/unified-web-workspace-tabs/spec.md
- specs/clean-translation-view/spec.md
- specs/static-web-shell/spec.md
- specs/client-sse-bridge/spec.md
"""
from pathlib import Path

from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.release_projection_support import checked_controller as running_controller  # noqa: F401
from tests.test_frontend_sse_lifecycle_contracts import _serve_minimal_sse_harness
from tests.test_progressive_sse_deduplication import (
    TOKEN,
    _cleanup_page_streams,
    _configure_sqlite_world,
    _render_session_html,
    _seed_session,
    subscriber_barrier,  # noqa: F401
)
from tests.test_table_rmb_audio import _captured_requests, _mount_with_device, _render_haus_document


ROOT = Path(__file__).resolve().parents[1]


def _render_multi_word_doc(tmp_path, *, omit_audio_mode_keys=False, container_tabs=False):
    config, paths, _, _ = desk.load_config()
    config.set("storage", "backend", "tsv")
    config.set("server", "api_key", "test-audio-api-key")
    config.set("settings", "default_target_language", "en")
    config.set("sentences_mode", "enabled", "true" if container_tabs else "false")
    if container_tabs:
        config.set("sentences_mode", "delivery_mode", "container")
        config.set("sentences_mode", "web_tab_mode", "container")
    config.set("rendering", "display_mode", "monolithic")
    config.set("audio", "lmb_play", "true")
    config.set("audio", "lmb_source", "lemma")
    config.set("audio", "rmb_play", "true")
    config.set("audio", "rmb_source", "word_translation")
    paths["storage_backend"] = "tsv"
    paths["base_dir"] = tmp_path
    paths["results_dir"] = tmp_path / "results"
    paths["generated_results_dir"] = tmp_path / "results"
    paths["sqlite_db_path"] = tmp_path / "desk.db"

    cols = [
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "WordSourceIPA",
        "WordSourceMorphologyAI",
        "SentenceSourceIndex",
        "SentenceSource",
        "SentenceDestination",
        "DeskSelected",
    ]
    if container_tabs:
        text = "Haus gut. Buch neu."
        rows = [
            ["Haus", "Haus", "Haus", "house", "", "", "1", "Haus gut.", "House good.", "0"],
            ["Buch", "Buch", "Buch", "book", "", "", "2", "Buch neu.", "Book new.", "0"],
        ]
    else:
        text = "Haus Buch."
        rows = [
            ["Haus", "Haus", "Haus", "house", "", "", "1", "Haus Buch.", "House book.", "0"],
            ["Buch", "Buch", "Buch", "book", "", "", "1", "Haus Buch.", "House book.", "0"],
        ]
    tsv = tmp_path / "20261127210001-haus-buch.de.tsv"
    tsv.write_text(
        "\t".join(cols) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    html = desk.run_render_flow(
        text=text,
        language="de",
        zid="20261127210001",
        text_mode="single",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=str(tsv),
        wordfill_cfg={"enabled": False},
        spawn_children=False,
    )
    if omit_audio_mode_keys:
        strip_script = """<script>
        (function() {
            var el = document.getElementById('kardenwort-bootstrap');
            if (el) {
                var data = JSON.parse(el.textContent);
                if (data && data.ui_config) {
                    delete data.ui_config.audio_lmb_chain_mode;
                    delete data.ui_config.audio_source_range_mode;
                    delete data.ui_config.audio_table_range_mode;
                    delete data.ui_config.audio_rmb_chain_mode;
                }
                el.textContent = JSON.stringify(data);
            }
            if (window.__CONFIG__) {
                delete window.__CONFIG__.audio_lmb_chain_mode;
                delete window.__CONFIG__.audio_source_range_mode;
                delete window.__CONFIG__.audio_table_range_mode;
                delete window.__CONFIG__.audio_rmb_chain_mode;
            }
        })();
        </script>"""
        marker = "</script>"
        idx = html.find('id="kardenwort-bootstrap"')
        assert idx != -1
        end_tag = html.find(marker, idx)
        html = html[: end_tag + len(marker)] + strip_script + html[end_tag + len(marker) :]
    return html


def test_fe_h9_independent_per_cell_skeleton_diffing(page, tmp_path):
    """FE-H9: Resolving WordDestination, IPA, or Morphology must not drop or freeze other pending cells."""
    html = _render_haus_document(tmp_path)
    _mount_with_device(page, html)

    # Scenario A: Both WordDestination and IPA pending; translation resolves while IPA remains pending.
    res_a = page.evaluate(
        """() => {
            const tbody = document.querySelector('#lemma-table tbody');
            const initial = [{
                row_id: 0, token_order: 0, sentence_idx: 1,
                inflected: 'Haus', lemma: 'Haus', translation: '', ipa: '', morphology: '',
                is_pending: true, pending_provider: 'google',
                is_ipa_pending: true, is_morph_pending: false, enrich_pending_provider: 'intellifiller'
            }];
            window.WorkspaceTabs.bindCardWordsToTbody(tbody, initial, {});
            const afterInit = {
                transSkel: Boolean(tbody.querySelector('td.col-translation .skeleton-loader')),
                ipaSkel: Boolean(tbody.querySelector('td.col-ipa .skeleton-loader')),
            };
            const resolvedTrans = [{
                row_id: 0, token_order: 0, sentence_idx: 1,
                inflected: 'Haus', lemma: 'Haus', translation: 'house', ipa: '', morphology: '',
                is_pending: false, pending_provider: null,
                is_ipa_pending: true, is_morph_pending: false, enrich_pending_provider: 'intellifiller'
            }];
            window.WorkspaceTabs.bindCardWordsToTbody(tbody, resolvedTrans, {});
            return {
                afterInit,
                transText: tbody.querySelector('td.col-translation').textContent.trim(),
                transSkel: Boolean(tbody.querySelector('td.col-translation .skeleton-loader')),
                ipaSkel: Boolean(tbody.querySelector('td.col-ipa .skeleton-loader')),
            };
        }"""
    )
    assert res_a["afterInit"] == {"transSkel": True, "ipaSkel": True}
    assert res_a["transText"] == "house"
    assert res_a["transSkel"] is False
    assert res_a["ipaSkel"] is True

    # Scenario B: Both WordDestination and IPA/Morphology pending; IPA & Morphology resolve while translation stays pending.
    res_b = page.evaluate(
        """() => {
            const tbody = document.querySelector('#lemma-table tbody');
            tbody.innerHTML = '';
            const initial = [{
                row_id: 0, token_order: 0, sentence_idx: 1,
                inflected: 'Haus', lemma: 'Haus', translation: '', ipa: '', morphology: '',
                is_pending: true, pending_provider: 'google',
                is_ipa_pending: true, is_morph_pending: true, enrich_pending_provider: 'intellifiller'
            }];
            window.WorkspaceTabs.bindCardWordsToTbody(tbody, initial, {});
            const resolvedEnrich = [{
                row_id: 0, token_order: 0, sentence_idx: 1,
                inflected: 'Haus', lemma: 'Haus', translation: '', ipa: '/haʊs/', morphology: 'N.Neut.Sg',
                is_pending: true, pending_provider: 'google',
                is_ipa_pending: false, is_morph_pending: false, enrich_pending_provider: null
            }];
            window.WorkspaceTabs.bindCardWordsToTbody(tbody, resolvedEnrich, {});
            return {
                transSkel: Boolean(tbody.querySelector('td.col-translation .skeleton-loader')),
                ipaText: tbody.querySelector('td.col-ipa').textContent.trim(),
                ipaSkel: Boolean(tbody.querySelector('td.col-ipa .skeleton-loader')),
                morphText: tbody.querySelector('td.col-morphology').textContent.trim(),
                morphSkel: Boolean(tbody.querySelector('td.col-morphology .skeleton-loader')),
            };
        }"""
    )
    assert res_b["transSkel"] is True
    assert res_b["ipaText"] == "/haʊs/"
    assert res_b["ipaSkel"] is False
    assert res_b["morphText"] == "N.Neut.Sg"
    assert res_b["morphSkel"] is False


def test_fe_h10_translation_container_rmb_audio_with_app_mounted(page, tmp_path):
    """FE-H10: Right-clicking span.word in #translation-container plays target-language audio even with app.js mounted."""
    html = _render_haus_document(tmp_path, rmb_play=True)
    errors = _mount_with_device(page, html)
    assert page.evaluate("typeof window.__deskHandleMouseUp === 'function'") is True

    word_span = page.locator("#translation-container span.word", has_text="house").first
    expect(word_span).to_be_visible()
    word_span.click(button="right")

    page.wait_for_function("window.requests && window.requests.length >= 1", timeout=2000)
    reqs = _captured_requests(page)
    assert errors == []
    assert len(reqs) == 1
    assert reqs[0]["url"].endswith("/api/v1/audio/synthesize")
    assert reqs[0]["body"] == {"text": "house", "language": "en"}

    # Hand Tool (__selectableTextMode = true) must still suppress RMB audio.
    page.evaluate("window.__selectableTextMode = true; window.requests.length = 0;")
    word_span.click(button="right")
    assert _captured_requests(page) == []


def test_fe_m2_lemma_table_render_preserves_selected_lemmas_purity(page, tmp_path):
    """FE-M2: LemmaTable.render() must never pollute AppState.selectedLemmas with numeric row IDs."""
    html = _render_haus_document(tmp_path)
    _mount_with_device(page, html)

    result = page.evaluate(
        """() => {
            window.AppState.selectedLemmas = { haus: true };
            const rows = [{
                row_id: 3, token_order: 3, sentence_idx: 1,
                inflected: 'Haus', lemma: 'Haus', translation: 'house'
            }];
            window.LemmaTable.render({ selectedRowIds: ['3'], selectedLemmas: window.AppState.selectedLemmas }, rows);
            const tr = document.querySelector('#lemma-table tbody tr[data-row-id="3"]');
            return {
                selectedLemmaKeys: Object.keys(window.AppState.selectedLemmas),
                rowSelectedAttr: tr ? tr.getAttribute('data-selected') : null,
            };
        }"""
    )
    assert result["rowSelectedAttr"] == "1"
    assert result["selectedLemmaKeys"] == ["haus"]
    assert "3" not in result["selectedLemmaKeys"]


def test_fe_m3_cached_row_html_updates_selected_and_filter_hidden_structurally(page, tmp_path):
    """FE-M3: Cached wordObj.row_html must update standalone selected class and data-filter-hidden cleanly."""
    html = _render_haus_document(tmp_path)
    _mount_with_device(page, html)

    result = page.evaluate(
        """() => {
            const tbody = document.querySelector('#lemma-table tbody');
            const cachedSelected = '<tr data-row-id="0" data-token-order="0" data-sentence-idx="1" data-selected="1" data-filter-hidden="0" class="highlight-orange selected"><td class="editable col-inflected"><div class="scrollable-cell">Haus</div></td><td class="editable col-lemma"><div class="scrollable-cell">Haus</div></td><td class="editable col-translation"><div class="scrollable-cell">house</div></td></tr>';
            const word = { row_id: 0, token_order: 0, sentence_idx: 1, lemma: 'Haus', inflected: 'Haus', translation: 'house', row_html: cachedSelected };

            window.WorkspaceTabs.bindCardWordsToTbody(tbody, [word], {}, []);
            const trOff = tbody.querySelector('tr[data-row-id="0"]');
            const offState = {
                hasSelected: trOff.classList.contains('selected'),
                hasKwSelected: trOff.classList.contains('kw-row-selected'),
                dataSelected: trOff.getAttribute('data-selected'),
                dataFilterHidden: trOff.getAttribute('data-filter-hidden'),
            };

            const cachedUnselected = '<tr data-row-id="0" data-token-order="0" data-sentence-idx="1" data-selected="0" data-filter-hidden="1" class="highlight-orange"><td class="editable col-inflected"><div class="scrollable-cell">Haus</div></td><td class="editable col-lemma"><div class="scrollable-cell">Haus</div></td><td class="editable col-translation"><div class="scrollable-cell">house</div></td></tr>';
            const word2 = { row_id: 0, token_order: 0, sentence_idx: 1, lemma: 'Haus', inflected: 'Haus', translation: 'house', row_html: cachedUnselected };
            window.WorkspaceTabs.bindCardWordsToTbody(tbody, [word2], {'0': true}, ['0']);
            const trOn = tbody.querySelector('tr[data-row-id="0"]');
            const onState = {
                hasSelected: trOn.classList.contains('selected'),
                hasKwSelected: trOn.classList.contains('kw-row-selected'),
                dataSelected: trOn.getAttribute('data-selected'),
                dataFilterHidden: trOn.getAttribute('data-filter-hidden'),
            };
            return { offState, onState };
        }"""
    )
    assert result["offState"] == {
        "hasSelected": False,
        "hasKwSelected": False,
        "dataSelected": "0",
        "dataFilterHidden": "1",
    }
    assert result["onState"] == {
        "hasSelected": True,
        "hasKwSelected": True,
        "dataSelected": "1",
        "dataFilterHidden": "0",
    }


def test_fe_m4_sse_reconnect_resolves_rotated_token(
    page, tmp_path, running_controller, subscriber_barrier
):
    """FE-M4a: SSE reconnect(true) must resolve the latest window.API_TOKEN dynamically."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    zid = "20261127210002"
    _seed_session(server, adapter, zid, pending=False)

    _serve_minimal_sse_harness(page, url)
    try:
        page.evaluate(
            """([zid, baseUrl, t1]) => {
                window.__sseTokens = [];
                window.API_TOKEN = t1;
                const origFetch = window.fetch;
                window.fetch = function(input, init) {
                    const hdrs = (init && init.headers) || {};
                    window.__sseTokens.push(hdrs['X-API-Token'] || '');
                    return origFetch.apply(this, arguments);
                };
                window.SSEBridge.connect(zid, baseUrl);
            }""",
            [zid, url, server.api_key],
        )
        page.wait_for_function("window.__sseTokens.length === 1", timeout=5000)
        page.wait_for_function(
            "document.getElementById('sse-status-indicator')?.dataset.status === 'connected'",
            timeout=5000,
        )

        rotated = "rotated-sse-token-20261127"
        server.api_key = rotated
        page.evaluate(
            """(t2) => {
                window.API_TOKEN = t2;
                const src = window.SSEBridge.getActiveSource();
                src.readyState = 0;
                if (src.onerror) src.onerror(new Error('Simulated drop'));
                window.SSEBridge.reconnect(true);
            }""",
            rotated,
        )
        page.wait_for_function("window.__sseTokens.length >= 2", timeout=5000)
        assert page.evaluate("window.__sseTokens[window.__sseTokens.length - 1]") == rotated
    finally:
        server.api_key = TOKEN
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(zid, 0)


def test_fe_m4_sse_recovery_mode_resets_and_tag_updated_projects_full_state(
    page, tmp_path, running_controller, subscriber_barrier
):
    """FE-M4b/c: Committed recovery resets recoveryMode to 'membership'; tag_updated projects full selection state."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    zid = "20261127210003"
    _seed_session(server, adapter, zid, pending=False)

    html = _render_session_html(server, zid, pending=False)
    route_url = f"{url}/sse-recovery/{zid}"
    status_urls = []
    page.on(
        "request",
        lambda req: status_urls.append(req.url) if "/api/v1/status" in req.url else None,
    )
    page.route(route_url, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html))
    try:
        page.goto(route_url, wait_until="domcontentloaded")
        page.wait_for_function(
            "document.getElementById('sse-status-indicator')?.dataset.status === 'connected' && window.__stateReady !== undefined",
            timeout=5000,
        )
        page.evaluate("window.__stateReady")

        # Enter committed recovery mode after stream error.
        page.evaluate("window.SSEBridge.getActiveSource().onerror(new Error('Transient drop'))")
        assert page.evaluate("window.SSEBridge.needsCommittedRecovery()") is True

        # Reconcile stream membership -> committed recovery succeeds -> recoveryMode must reset.
        page.evaluate(
            """() => {
                const b = window.SSEBridge;
                return window.reconcileStreamMembership({
                    session: b.getActiveZid(),
                    connectionId: b.getActiveConnectionId(),
                    serverUrl: b.getServerUrl(),
                    refreshStatus: true
                });
            }"""
        )
        assert page.evaluate("window.SSEBridge.needsCommittedRecovery()") is False
        committed_before = sum(1 for u in status_urls if "committed=1" in u)
        assert committed_before >= 1

        # Next reconcileStreamMembership() must NOT force committed=1 again.
        page.evaluate(
            """() => {
                const b = window.SSEBridge;
                return window.reconcileStreamMembership({
                    session: b.getActiveZid(),
                    connectionId: b.getActiveConnectionId(),
                    serverUrl: b.getServerUrl()
                });
            }"""
        )
        assert sum(1 for u in status_urls if "committed=1" in u) == committed_before
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(zid, 0)


def test_fe_m4_sse_tag_updated_projects_full_selection_state(
    page, tmp_path, running_controller, subscriber_barrier
):
    """FE-M4c: Incoming tag_updated must synchronize selectedLemmas, data-selected, cards, and highlights."""
    url, server, adapter = running_controller
    _configure_sqlite_world(server, adapter, tmp_path)
    zid = "20261127210004"
    _seed_session(server, adapter, zid, pending=False)

    html = _render_session_html(server, zid, pending=False)
    route_url = f"{url}/sse-tag-updated/{zid}"
    page.route(route_url, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html))
    try:
        page.goto(route_url, wait_until="domcontentloaded")
        page.wait_for_function(
            "document.getElementById('sse-status-indicator')?.dataset.status === 'connected' && window.__stateReady !== undefined",
            timeout=5000,
        )
        page.evaluate("window.__stateReady")

        tag_result = page.evaluate(
            """() => {
                const tr = document.querySelector('#lemma-table tbody tr[data-row-id]');
                const rowId = tr.getAttribute('data-row-id');
                const sentIdx = Number(tr.getAttribute('data-sentence-idx') || 1);
                const tokOrd = Number(tr.getAttribute('data-token-order') || rowId);
                const lemma = (tr.querySelector('td.col-lemma').textContent || '').trim().toLowerCase();
                const es = window.SSEBridge.getActiveSource();
                es.onmessage({
                    data: JSON.stringify({
                        type: 'tag_updated',
                        row_id: rowId,
                        sentence_idx: sentIdx,
                        token_order: tokOrd,
                        status: true
                    })
                });
                const activeCard = window.WorkspaceTabs ? window.WorkspaceTabs.getActiveCard() : null;
                const highlightedWords = document.querySelectorAll('#source-container span.word.highlight-orange-active, #source-container span.word.highlight-active, #source-container span.word.active-token');
                return {
                    lemma,
                    rowDataSelected: tr.getAttribute('data-selected'),
                    rowHasSelectedClass: tr.classList.contains('selected') || tr.classList.contains('kw-row-selected'),
                    selectedLemmaFlag: Boolean(window.AppState.selectedLemmas && window.AppState.selectedLemmas[lemma]),
                    cardHasRowId: Boolean(activeCard && (activeCard.selected_ids || []).map(String).includes(String(rowId))),
                    highlightedCount: highlightedWords.length,
                };
            }"""
        )
        assert tag_result["rowDataSelected"] == "1"
        assert tag_result["rowHasSelectedClass"] is True
        assert tag_result["selectedLemmaFlag"] is True
        assert tag_result["cardHasRowId"] is True
        assert tag_result["highlightedCount"] >= 1
    finally:
        _cleanup_page_streams(page)
        subscriber_barrier.flush_closed_peers(zid, 0)


def test_fe_l1_workspace_tabs_fallback_chip_empty_cell_and_fail_fast_lang(page, tmp_path):
    """FE-L1: default [2] chip fallback, token-styled colspan=8 empty cell (zero inline style), fail-fast getSessionLang."""
    import re
    html = _render_multi_word_doc(tmp_path, container_tabs=True)
    # Strip SSR tab chips so WorkspaceTabs.init() executes the client-side fallback chip builder fresh.
    html = re.sub(
        r'(<div class="kw-tab-track" id="kw-tab-track">).*?(</div>)',
        r"\1\2",
        html,
        flags=re.DOTALL,
    )
    _mount_with_device(page, html)

    chip_res = page.evaluate(
        """() => {
            const track = document.getElementById('kw-tab-track');
            const chips = Array.from(track.querySelectorAll('.kw-tab-chip')).map(el => ({
                seq: el.getAttribute('data-tab-seq'),
                active: el.classList.contains('active'),
            }));
            const mockCards = [
                { seq_num: 1, sentence_idx: 0, label: '[1]', words: [], source_text: 'Haus gut. Buch neu.' },
                { seq_num: 2, sentence_idx: 1, label: '[2]', words: [], source_text: 'Haus gut.' },
            ];
            window.WorkspaceTabs.init(mockCards);
            const emptyCell = document.querySelector('#lemma-table tbody td.kw-empty-cell');
            return {
                chips,
                emptyColspan: emptyCell ? emptyCell.getAttribute('colspan') : null,
                emptyInlineStyle: emptyCell ? emptyCell.getAttribute('style') : 'missing',
            };
        }"""
    )
    assert chip_res["chips"] == [
        {"seq": "1", "active": False},
        {"seq": "2", "active": True},
        {"seq": "3", "active": False},
    ]
    assert chip_res["emptyColspan"] == "8"
    assert chip_res["emptyInlineStyle"] is None

    lang_throws = page.evaluate(
        """() => {
            const orig = window.getSessionLang;
            window.getSessionLang = function() { throw new Error('Missing session language'); };
            try {
                const tbody = document.querySelector('#lemma-table tbody');
                let bindThrew = false;
                try {
                    window.WorkspaceTabs.bindCardWordsToTbody(tbody, [{
                        row_id: 0, token_order: 0, sentence_idx: 1,
                        inflected: 'Haus', lemma: 'Haus', translation: 'house'
                    }], {});
                } catch (e) {
                    bindThrew = /Missing session language/.test(String(e));
                }
                let titleThrew = false;
                try {
                    window.WorkspaceTabs.projectActiveMetadata();
                } catch (e) {
                    titleThrew = /Missing session language/.test(String(e));
                }
                return { bindThrew, titleThrew };
            } finally {
                window.getSessionLang = orig;
            }
        }"""
    )
    assert lang_throws["bindThrew"] is True
    assert lang_throws["titleThrew"] is True


def test_fe_l2_translation_view_render_replaces_pending_skeleton(page, tmp_path):
    """FE-L2: TranslationView.render() must replace a pending skeleton when non-empty translated text is provided."""
    html = _render_haus_document(tmp_path)
    _mount_with_device(page, html)

    trans_res = page.evaluate(
        """() => {
            const c = document.getElementById('translation-container');
            c.innerHTML = '<span class="skeleton-loader" data-pending="true">Google...</span>';
            window.TranslationView.render({ translated_text: 'Hello world' });
            return {
                hasSkeleton: Boolean(c.querySelector('.skeleton-loader, [data-pending="true"]')),
                wordCount: c.querySelectorAll('span.word').length,
                text: c.textContent.trim(),
            };
        }"""
    )
    assert trans_res["hasSkeleton"] is False
    assert trans_res["wordCount"] == 2
    assert trans_res["text"] == "Hello world"


def _audio_requests(page):
    return [r for r in _captured_requests(page) if r["url"].endswith("/api/v1/audio/synthesize")]


def test_fe_l4_omitted_audio_config_defaults_match_canonical_spec(page, tmp_path):
    """FE-L4: Omitted audio defaults in app.js must match canonical ('joined', 'all', 'none', 'separate')."""
    html_audio = _render_multi_word_doc(tmp_path, omit_audio_mode_keys=True)
    _mount_with_device(page, html_audio)

    # 1. LMB drag across 2 source words without Ctrl -> joined ("Haus Buch") (source_range_mode='all', lmb_chain_mode='joined').
    w0 = page.locator("#source-container span.word").nth(0)
    w1 = page.locator("#source-container span.word").nth(1)
    b0 = w0.bounding_box()
    b1 = w1.bounding_box()
    page.mouse.move(b0["x"] + b0["width"] / 2, b0["y"] + b0["height"] / 2)
    page.mouse.down(button="left")
    page.mouse.move(b1["x"] + b1["width"] / 2, b1["y"] + b1["height"] / 2)
    page.mouse.up(button="left")

    page.wait_for_function(
        "window.requests && window.requests.some(r => String(r.url).endsWith('/api/v1/audio/synthesize'))",
        timeout=2000,
    )
    assert _audio_requests(page)[0]["body"] == {"text": "Haus Buch", "language": "de"}

    # 2. LMB drag across 2 table rows without Ctrl -> NO audio (table_range_mode='none').
    page.evaluate("window.requests.length = 0;")
    r0 = page.locator('#lemma-table tbody tr[data-row-id="0"]').bounding_box()
    r1 = page.locator('#lemma-table tbody tr[data-row-id="1"]').bounding_box()
    page.mouse.move(r0["x"] + 20, r0["y"] + r0["height"] / 2)
    page.mouse.down(button="left")
    page.mouse.move(r1["x"] + 20, r1["y"] + r1["height"] / 2)
    page.mouse.up(button="left")
    assert _audio_requests(page) == []

    # 3. RMB drag-flip across 2 source words -> separate ("house ||| book") (rmb_chain_mode='separate').
    page.evaluate("window.requests.length = 0;")
    page.mouse.move(b0["x"] + b0["width"] / 2, b0["y"] + b0["height"] / 2)
    page.mouse.down(button="right")
    page.mouse.move(b1["x"] + b1["width"] / 2, b1["y"] + b1["height"] / 2)
    page.mouse.up(button="right")

    page.wait_for_function(
        "window.requests && window.requests.some(r => String(r.url).endsWith('/api/v1/audio/synthesize'))",
        timeout=2000,
    )
    assert _audio_requests(page)[0]["body"] == {"text": "house ||| book", "language": "en"}
