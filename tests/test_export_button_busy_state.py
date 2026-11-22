"""Acceptance controls for kwExportSelected host-button busy state and settlement.

Change: 20261119102732-fix-hydrated-table-coordinates-and-column-placeholders (Phase 2)
Verifies:
- Canonical workspace shell (#kw-btn-export) enters disabled busy state while
  POST /api/v1/export is in-flight and settles back to enabled on success,
  HTTP error, and network failure while remaining usable.
- Supported legacy lookup markup (#kw-export-btn) retains identical busy-state
  and success/failure settlement behavior.
- Backend occurrence targeting (target_coordinates -> SQLite + exported TSV)
  is preserved unchanged.
"""

import csv
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN


ZID = "20261122183301"
HEADERS = [
    "Quotation",
    "WordSource",
    "WordSourceInflectedForm",
    "WordDestination",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "DeskSelected",
    "TokenOrder",
]


def _configure_world(server, adapter, tmp_path):
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    freq_file = tmp_path / "de_freq.txt"
    freq_file.write_text("Alpha\nZebra\n", encoding="utf-8")

    server.resolved_paths["favorites_output_dir"] = fav_dir
    server.resolved_paths["kardenwort_workspace"] = tmp_path
    adapter.resolved_paths["favorites_output_dir"] = fav_dir
    adapter.resolved_paths["kardenwort_workspace"] = tmp_path

    server.config.read_dict(
        {
            "settings": {
                "default_language": "de",
                "default_target_language": "ru",
                "favorites_output_dir": str(fav_dir),
                "favorites_prefix": "",
                "save_to_favorites_on_export": "true",
                "send_to_anki_after_export": "false",
                "export_selection_mode": "selected",
            },
            "languages": {
                "de_lemma_index": str(freq_file),
            },
            "rendering": {
                "highlight_same_lemma": "false",
                "filter_selected_only": "false",
                "auto_save_on_edit": "false",
            },
            "sentences_mode": {
                "enabled": "false",
                "delivery_mode": "container",
                "deduplication_scope": "none",
            },
            "triggers": {
                "run_text_translation": "manual",
                "run_lemma_base_translation": "manual",
                "run_lemma_enrichment": "manual",
            },
            "wordfill": {"enabled": "false"},
            "audio": {"lmb_play": "false", "rmb_play": "false"},
            "server": {"api_key": TOKEN, "enabled": "true"},
        }
    )
    server.arbiter.config = server.config
    server.arbiter.resolved_paths = server.resolved_paths
    if isinstance(server.goldendict, dict):
        server.goldendict["server_port"] = server.server_port
    return fav_dir


def _seed_session(adapter):
    rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra.", "Зебра.", "0", "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "2", "Alpha.", "Альфа.", "0", "0"],
    ]
    sentences = [
        {
            "session_zid": ZID,
            "sentence_index": 1,
            "sentence_source": "Zebra.",
            "sentence_destination": "Зебра.",
        },
        {
            "session_zid": ZID,
            "sentence_index": 2,
            "sentence_source": "Alpha.",
            "sentence_destination": "Альфа.",
        },
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="export-busy-state",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Zebra.\nAlpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=sentences,
    )
    return rows


def _render_host_html(server, adapter, host_mode):
    restored = adapter.restore_session(ZID)
    html = desk.render_lookup_html(
        text="Zebra.\nAlpha.",
        language="de",
        target_lang="ru",
        config=server.config,
        resolved_paths=server.resolved_paths,
        zid=ZID,
        goldendict={
            "sections": ["source", "translation", "lemmas"],
            "lemma_columns": ["inflected", "lemma", "translation"],
            "server_enabled": True,
            "server_api_key": TOKEN,
            "server_port": server.server_port,
        },
        comments=[],
        headers=restored["headers"],
        data_rows=restored["data_rows"],
        sentence_translation="Зебра.\nАльфа.",
        session_zid=ZID,
        api_token=TOKEN,
        server_enabled=True,
        fingerprint=desk.compute_content_fingerprint(restored["data_rows"]),
    )
    if host_mode == "legacy":
        # Legacy lookup markup renders #kw-export-btn without canonical #kw-btn-export.
        html = html.replace(
            'id="kw-btn-export"',
            'id="kw-export-btn" onclick="kwExportSelected()"',
            1,
        )
    return html


def _open_page(page, url, html):
    page.set_default_timeout(6000)
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.set_extra_http_headers({"X-API-Token": TOKEN})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(url + "/", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.kwExportSelected === 'function'")
    return errors


def _install_export_gate(page):
    page.evaluate(
        """() => {
            if (!document.getElementById('kw-interactive-status')) {
                const statusEl = document.createElement('span');
                statusEl.id = 'kw-interactive-status';
                document.body.appendChild(statusEl);
            }
            const origFetch = window.fetch.bind(window);
            window.__exportGate = { mode: 'hold', started: false, calls: [], release: null };
            window.fetch = function(url, opts) {
                if (String(url).includes('/api/v1/export')) {
                    window.__exportGate.started = true;
                    window.__exportGate.calls.push({
                        url: String(url),
                        body: opts && opts.body ? JSON.parse(opts.body) : null,
                    });
                    if (window.__exportGate.mode === 'hold') {
                        return new Promise((resolve, reject) => {
                            window.__exportGate.release = (action) => {
                                window.__exportGate.mode = 'pass';
                                if (action === 'success') {
                                    origFetch(url, opts).then(resolve, reject);
                                } else if (action === 'http_error') {
                                    resolve(new Response(
                                        JSON.stringify({ status: 'error', message: 'Simulated export failure' }),
                                        { status: 500, headers: { 'Content-Type': 'application/json' } }
                                    ));
                                } else if (action === 'network_error') {
                                    reject(new TypeError('Failed to fetch'));
                                }
                            };
                        });
                    }
                }
                return origFetch(url, opts);
            };
        }"""
    )


def _read_exported_tsv(fav_dir: Path):
    files = list(fav_dir.glob("*.tsv"))
    assert len(files) == 1, f"Expected 1 exported TSV in {fav_dir}, got {files}"
    lines = [line for line in files[0].read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


@pytest.mark.parametrize("host_mode,btn_selector", [
    ("canonical", "#kw-btn-export"),
    ("legacy", "#kw-export-btn"),
])
@pytest.mark.parametrize("settlement_mode", ["success", "http_error", "network_error"])
def test_kw_export_selected_busy_state_and_settlement(
    page, tmp_path, running_controller, host_mode, btn_selector, settlement_mode
):
    """kwExportSelected disables the active host export button in-flight, settles on success/failure, and preserves backend targeting."""
    url, server, adapter = running_controller
    fav_dir = _configure_world(server, adapter, tmp_path)
    _seed_session(adapter)
    html = _render_host_html(server, adapter, host_mode)

    errors = _open_page(page, url, html)
    try:
        btn = page.locator(btn_selector)
        expect(btn).to_be_visible()
        expect(btn).to_be_enabled()

        # Select visual row 0 (Alpha -> sentence 2, token 0) and clear DB selected flags
        # so the real /api/v1/export call proves backend targeting independently.
        row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
        expect(row0).to_contain_text("Alpha")
        cb0 = row0.locator("input.kw-tag-checkbox")
        cb0.check()
        expect(cb0).to_be_checked()
        for w in adapter.db.get_words_by_session(ZID):
            adapter.db.update_word(w["id"], {"selected": 0}, zid=ZID)

        _install_export_gate(page)

        # Invoke kwExportSelected and confirm the request is held in-flight.
        page.evaluate("() => window.kwExportSelected()")
        page.wait_for_function("() => window.__exportGate && window.__exportGate.started === true")

        # Authoritative busy-state contract: host button MUST be disabled while in-flight.
        expect(btn).to_be_disabled()

        # Verify backend occurrence targeting payload is preserved.
        calls = page.evaluate("() => window.__exportGate.calls")
        assert len(calls) == 1
        assert calls[0]["body"]["target_coordinates"] == [{"sentence_idx": 2, "token_order": 0}]

        if settlement_mode == "success":
            with page.expect_response(lambda r: "/api/v1/export" in r.url) as exported:
                page.evaluate("() => window.__exportGate.release('success')")
            assert exported.value.status == 200, exported.value.text()
            expect(btn).to_be_enabled()

            tsv_rows = _read_exported_tsv(fav_dir)
            assert [r["WordSource"] for r in tsv_rows] == ["Alpha"]
            words_after = {w["lemma"]: w for w in adapter.db.get_words_by_session(ZID)}
            assert words_after["Alpha"]["selected"] == 1
            assert words_after["Zebra"]["selected"] == 0
        else:
            page.evaluate(f"() => window.__exportGate.release({json.dumps(settlement_mode)})")
            expect(btn).to_be_enabled()
            status_text = page.locator("#kw-interactive-status").inner_text().lower()
            assert "failed" in status_text

            # Button must remain usable for a subsequent real export after failure settlement.
            with page.expect_response(lambda r: "/api/v1/export" in r.url) as retry_exported:
                page.evaluate("() => window.kwExportSelected()")
            assert retry_exported.value.status == 200, retry_exported.value.text()
            expect(btn).to_be_enabled()
            tsv_rows = _read_exported_tsv(fav_dir)
            assert [r["WordSource"] for r in tsv_rows] == ["Alpha"]
    finally:
        page.context.close()
        assert errors == []
