"""Behavioral reproduction & acceptance tests for runtime column names and POS display order.

Change: 20261119102732-fix-hydrated-table-coordinates-and-column-placeholders (Phase 3)
Verifies:
- HTTP-served /js/app.js (with zero template string substitution) resolves custom
  lemma_col_name, inflected_col_name, trans_col_name, and pos_display_order from
  window.__CONFIG__ at runtime, while retaining default WordSource /
  WordSourceInflectedForm / WordDestination compatibility.
- Both supported lookup HTML (_render_lookup_html_impl) and Desk workspace HTML
  (run_render_flow) emit lemma_col_name, inflected_col_name, trans_col_name, and
  pos_display_order in window.__CONFIG__ / ui_config and resolve custom columns live.
"""

import json
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN


ZID = "20261122185001"

CUSTOM_COLUMNS_MAPPING_INI = """[fields]
Quotation
CustomLemma
CustomInflected
CustomTranslation
SentenceSourceIndex
SentenceSource
SentenceDestination
DeskSelected
TokenOrder
WordSourcePOS
WordSourceGender

[fields_mapping.word]
Quotation=source_word
CustomLemma=lemma
CustomInflected=source_word
SentenceSource=source_sentence
SentenceSourceIndex=sentence_index
WordSourcePOS=pos
WordSourceGender=gender

[fields_mapping.sentence]
SentenceSource=source_sentence
SentenceDestination=destination_sentence
SentenceSourceIndex=sentence_index

[desk_columns]
Quotation=quotation
CustomLemma=lemma
CustomInflected=inflected
SentenceSource=sentence_source
SentenceDestination=sentence_destination
CustomTranslation=word_translation
DeskSelected=selected
WordSourcePOS=pos
WordSourceGender=gender

[desk_editable]
editable_columns=CustomLemma,CustomTranslation,CustomInflected,DeskSelected
"""


def _configure_custom_column_world(server, adapter, tmp_path):
    mapping_file = tmp_path / "anki-mapping.ini"
    mapping_file.write_text(CUSTOM_COLUMNS_MAPPING_INI, encoding="utf-8")
    server.resolved_paths["kardenwort_workspace"] = tmp_path
    server.resolved_paths["anki_mapping_file"] = mapping_file
    adapter.resolved_paths["kardenwort_workspace"] = tmp_path
    adapter.resolved_paths["anki_mapping_file"] = mapping_file

    server.config.read_dict(
        {
            "settings": {
                "default_language": "de",
                "default_target_language": "ru",
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
            "audio": {
                "lmb_play": "false",
                "rmb_play": "true",
                "rmb_source": "lemma",
                "playback_engine": "controller",
            },
            "server": {"api_key": TOKEN, "enabled": "true"},
        }
    )
    server.arbiter.config = server.config
    server.arbiter.resolved_paths = server.resolved_paths
    if isinstance(server.goldendict, dict):
        server.goldendict["server_port"] = server.server_port
    return mapping_file


def _hydrate_http_index_with_bootstrap(page, base_url, bootstrap_payload):
    page.set_default_timeout(6000)
    page.set_extra_http_headers({"X-API-Token": TOKEN})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(f"{base_url}/index.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.hydrateSession === 'function'")
    page.evaluate(
        """(payload) => {
            const bootEl = document.getElementById('kardenwort-bootstrap');
            bootEl.textContent = JSON.stringify(payload);
            document.dispatchEvent(new Event('DOMContentLoaded'));
        }""",
        bootstrap_payload,
    )
    return errors


def test_http_served_app_js_resolves_custom_columns_over_http(
    page, running_controller
):
    """HTTP-served /js/app.js resolves configured lemma/inflected/translation column names via window.__CONFIG__."""
    base_url, _, _ = running_controller

    audio_requests = []

    def _capture_audio(route):
        audio_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "ok"}),
        )

    page.route("**/api/v1/audio/synthesize", _capture_audio)

    bootstrap = {
        "ui_config": {
            "lemma_col_name": "CustomLemma",
            "inflected_col_name": "CustomInflected",
            "trans_col_name": "CustomTranslation",
            "audio_rmb_play": True,
            "audio_rmb_source": "lemma",
            "audio_lmb_play": False,
        },
        "session_data": {
            "zid": ZID,
            "session_zid": ZID,
            "language": "de",
            "target_lang": "ru",
            "source_text": "Er ging.",
            "sentence_translation": "Он шёл.",
            "headers": [
                "CustomInflected",
                "CustomLemma",
                "CustomTranslation",
                "SentenceSourceIndex",
                "TokenOrder",
                "DeskSelected",
            ],
            "data_rows": [["ging", "gehen", "идти", "1", "0", "0"]],
            "role_fields": {
                "inflected": "CustomInflected",
                "lemma": "CustomLemma",
                "word_translation": "CustomTranslation",
                "sentence_index": "SentenceSourceIndex",
                "selected": "DeskSelected",
            },
            "server_enabled": True,
            "api_token": TOKEN,
        },
    }

    errors = _hydrate_http_index_with_bootstrap(page, base_url, bootstrap)
    try:
        row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
        expect(row0).to_be_visible()
        assert row0.locator("td.col-lemma").get_attribute("data-col") == "CustomLemma"
        assert row0.locator("td.col-inflected").get_attribute("data-col") == "CustomInflected"
        assert row0.locator("td.col-translation").get_attribute("data-col") == "CustomTranslation"

        # 1. RMB with audio_rmb_source='lemma' must resolve CustomLemma -> "gehen".
        row0.locator("td.col-lemma").click(button="right")
        page.wait_for_function("() => true")
        assert len(audio_requests) == 1, f"Expected lemma audio synthesis request, got {audio_requests}"
        assert audio_requests[-1].get("text") == "gehen"

        # 2. RMB with audio_rmb_source='inflection' must resolve CustomInflected -> "ging".
        page.evaluate("() => { window.__CONFIG__.audio_rmb_source = 'inflection'; }")
        row0.locator("td.col-inflected").click(button="right")
        assert len(audio_requests) == 2, f"Expected inflection audio synthesis request, got {audio_requests}"
        assert audio_requests[-1].get("text") == "ging"

        # 3. RMB with audio_rmb_source='word_translation' must resolve CustomTranslation -> "идти".
        page.evaluate("() => { window.__CONFIG__.audio_rmb_source = 'word_translation'; }")
        row0.locator("td.col-translation").click(button="right")
        assert len(audio_requests) == 3, f"Expected translation audio synthesis request, got {audio_requests}"
        assert audio_requests[-1].get("text") == "идти"

        # Verify raw HTTP-served /js/app.js also contains zero unreplaced column placeholders.
        with urllib.request.urlopen(f"{base_url}/js/app.js", timeout=5) as resp:
            assert resp.status == 200
            js_source = resp.read().decode("utf-8")
        assert "{lemma_col_name}" not in js_source
        assert "{inflected_col_name}" not in js_source
        assert "{trans_col_name}" not in js_source
    finally:
        page.context.close()
        assert errors == []


def test_http_served_app_js_retains_default_column_and_pos_compatibility(
    page, running_controller
):
    """When column names and pos_display_order are omitted from window.__CONFIG__, HTTP-served /js/app.js retains standard defaults."""
    base_url, _, _ = running_controller
    audio_requests = []
    page.route(
        "**/api/v1/audio/synthesize",
        lambda route: (
            audio_requests.append(json.loads(route.request.post_data or "{}")),
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok"})),
        ),
    )
    occ_payload = json.dumps(
        {
            "0": {"form": "in", "pos": "prep.", "trans": "в", "morph": "prep"},
            "1": {"form": "der", "pos": "art.", "trans": "тот", "morph": "art"},
        }
    )
    bootstrap = {
        "ui_config": {
            "audio_rmb_play": True,
            "audio_rmb_source": "lemma",
            "audio_lmb_play": False,
        },
        "session_data": {
            "zid": ZID,
            "session_zid": ZID,
            "language": "de",
            "target_lang": "ru",
            "source_text": "in der",
            "sentence_translation": "в тот",
            "headers": [
                "WordSourceInflectedForm",
                "WordSource",
                "WordDestination",
                "WordSourcePOS",
                "SentenceSourceIndex",
                "TokenOrder",
                "DeskSelected",
                "occ",
            ],
            "data_rows": [["ging", "gehen", "идти", "prep., art.", "1", "0", "0", occ_payload]],
            "server_enabled": True,
            "api_token": TOKEN,
        },
    }
    errors = _hydrate_http_index_with_bootstrap(page, base_url, bootstrap)
    try:
        row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
        expect(row0).to_be_visible()
        row0.locator("td.col-lemma").click(button="right")
        assert len(audio_requests) == 1
        assert audio_requests[-1].get("text") == "gehen"

        page.evaluate("() => { window.__CONFIG__.audio_rmb_source = 'inflection'; }")
        row0.locator("td.col-inflected").click(button="right")
        assert len(audio_requests) == 2
        assert audio_requests[-1].get("text") == "ging"

        page.evaluate(
            """() => {
                window.AppState.activeTokenSelections = [
                    { visual_idx: 0, sentence_idx: 1, token_order: 0 },
                    { visual_idx: 1, sentence_idx: 1, token_order: 0 },
                ];
                window.updateBidirectionalHighlights();
            }"""
        )
        assert row0.locator("td.col-pos").inner_text().strip() == "art., prep."
    finally:
        page.context.close()
        assert errors == []


def test_http_served_app_js_resolves_configured_pos_display_order(page, running_controller):
    """HTTP-served /js/app.js sorts multi-occurrence POS values using window.__CONFIG__.pos_display_order."""
    base_url, _, _ = running_controller

    # Reversed custom POS order: prep. (0) before art. (1) instead of default art. (0) before prep. (3).
    custom_pos_order = {"prep.": 0, "art.": 1}
    occ_payload = json.dumps(
        {
            "0": {"form": "der", "pos": "art.", "trans": "тот", "morph": "art"},
            "1": {"form": "in", "pos": "prep.", "trans": "в", "morph": "prep"},
        }
    )

    bootstrap = {
        "ui_config": {
            "pos_display_order": custom_pos_order,
        },
        "session_data": {
            "zid": ZID,
            "session_zid": ZID,
            "language": "de",
            "target_lang": "ru",
            "source_text": "der in",
            "sentence_translation": "тот в",
            "headers": [
                "WordSourceInflectedForm",
                "WordSource",
                "WordDestination",
                "WordSourcePOS",
                "SentenceSourceIndex",
                "TokenOrder",
                "DeskSelected",
                "occ",
            ],
            "data_rows": [["der, in", "der", "тот", "art., prep.", "1", "0", "0", occ_payload]],
            "server_enabled": True,
            "api_token": TOKEN,
        },
    }

    errors = _hydrate_http_index_with_bootstrap(page, base_url, bootstrap)
    try:
        row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
        expect(row0).to_be_visible()

        # Select both occurrences and trigger bidirectional occurrence overlay rendering.
        page.evaluate(
            """() => {
                window.AppState.activeTokenSelections = [
                    { visual_idx: 0, sentence_idx: 1, token_order: 0 },
                    { visual_idx: 1, sentence_idx: 1, token_order: 0 },
                ];
                window.updateBidirectionalHighlights();
            }"""
        )

        pos_text = row0.locator("td.col-pos").inner_text().strip()
        assert pos_text == "prep., art.", (
            f"Expected custom pos_display_order ('prep., art.'), got {pos_text!r}"
        )
    finally:
        page.context.close()
        assert errors == []


def test_lookup_html_and_desk_shell_emit_runtime_column_and_pos_config(
    page, tmp_path, running_controller
):
    """Both _render_lookup_html_impl and run_render_flow populate window.__CONFIG__ with column names and pos_display_order."""
    base_url, server, adapter = running_controller
    _configure_custom_column_world(server, adapter, tmp_path)

    headers = [
        "Quotation",
        "CustomLemma",
        "CustomInflected",
        "CustomTranslation",
        "SentenceSourceIndex",
        "SentenceSource",
        "SentenceDestination",
        "DeskSelected",
        "TokenOrder",
        "WordSourcePOS",
        "WordSourceGender",
    ]
    rows = [
        ["ging", "gehen", "ging", "идти", "1", "Er ging.", "Он шёл.", "0", "0", "v.", ""],
    ]
    sentences = [
        {
            "session_zid": ZID,
            "sentence_index": 1,
            "sentence_source": "Er ging.",
            "sentence_destination": "Он шёл.",
        }
    ]
    seed_words = [
        {
            "session_zid": ZID,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "ging",
            "inflected_form": "ging",
            "lemma": "gehen",
            "word_destination": "идти",
            "pos": "v.",
            "gender": "",
            "selected": 0,
            "extra_fields": {
                "CustomLemma": "gehen",
                "CustomInflected": "ging",
                "CustomTranslation": "идти",
            },
        }
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="custom-cols-lookup",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Er ging.",
        sentences=sentences,
        words=seed_words,
    )

    # 1. Lookup HTML must emit pos_display_order + custom column names in window.__CONFIG__ / ui_config.
    lookup_html = desk.render_lookup_html(
        text="Er ging.",
        language="de",
        target_lang="ru",
        config=server.config,
        resolved_paths=server.resolved_paths,
        zid=ZID,
        goldendict={
            "sections": ["source", "translation", "lemmas"],
            "lemma_columns": ["inflected", "lemma", "translation", "pos"],
            "server_enabled": True,
            "server_api_key": TOKEN,
            "server_port": server.server_port,
            "audio_rmb_play": True,
            "audio_rmb_source": "lemma",
        },
        comments=[],
        headers=headers,
        data_rows=rows,
        sentence_translation="Он шёл.",
        session_zid=ZID,
        api_token=TOKEN,
        server_enabled=True,
        fingerprint=desk.compute_content_fingerprint(rows),
    )

    page.set_default_timeout(6000)
    page.route(base_url + "/lookup-test", lambda route: route.fulfill(content_type="text/html", body=lookup_html))
    audio_requests = []
    page.route(
        "**/api/v1/audio/synthesize",
        lambda route: (
            audio_requests.append(json.loads(route.request.post_data or "{}")),
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok"})),
        ),
    )
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(base_url + "/lookup-test", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.hydrateSession === 'function'")

    try:
        cfg = page.evaluate("() => window.__CONFIG__ || {}")
        assert cfg.get("pos_display_order") == desk.POS_DISPLAY_ORDER
        assert cfg.get("lemma_col_name") == "CustomLemma"
        assert cfg.get("inflected_col_name") == "CustomInflected"
        assert cfg.get("trans_col_name") == "CustomTranslation"

        # 2. Desk shell run_render_flow must also emit those keys in ui_config / window.__CONFIG__.
        desk_html = desk.run_render_flow(
            text="Er ging.",
            language="de",
            zid=ZID,
            text_mode="single",
            config=server.config,
            resolved_paths=server.resolved_paths,
            tsv_path=ZID,
            spawn_children=False,
        )
        page.route(base_url + "/desk-test", lambda route: route.fulfill(content_type="text/html", body=desk_html))
        page.goto(base_url + "/desk-test", wait_until="domcontentloaded")
        page.wait_for_function("typeof window.hydrateSession === 'function'")
        desk_cfg = page.evaluate("() => window.__CONFIG__ || {}")
        assert desk_cfg.get("pos_display_order") == desk.POS_DISPLAY_ORDER
        assert desk_cfg.get("lemma_col_name") == "CustomLemma"
        assert desk_cfg.get("inflected_col_name") == "CustomInflected"
        assert desk_cfg.get("trans_col_name") == "CustomTranslation"
    finally:
        page.context.close()
        assert errors == []
