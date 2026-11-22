"""Browser/HTTP/SQLite acceptance tests for hydrated table coordinates and manifests.

Change: 20261119102732-fix-hydrated-table-coordinates-and-column-placeholders (Phase 1)
Verifies client-side hydrateSession() with positional headers + data_rows arrays:
- Custom and reordered headers via configured anki-mapping roles and aliases
- Repeated sentence-local token_order=0 across frequency-reordered rows
- Both workspace checkbox save (/session/save) and direct kwToggleTag (/api/v1/tag)
- Separate canonical render-row/group identity (data-row-id / data-all-row-ids) vs
  persistence occurrence targets (data-sentence-idx / data-token-order / data-occurrence-targets)
- Missing, ambiguous, malformed, or incompatible grouped manifests produce an explicit
  error with zero partial interactive DOM rows and zero SQLite mutations.
"""

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN


ZID = "20261122181501"

CUSTOM_MAPPING_INI = """[fields]
TokenOrder
WordDestination
CustomSentIdx
WordSource
WordSourceInflectedForm
CustomSelected
Quotation
SentenceSource
SentenceDestination

[fields_mapping.word]
Quotation=source_word
WordSource=lemma
WordSourceInflectedForm=source_word
SentenceSource=source_sentence
CustomSentIdx=sentence_index

[fields_mapping.sentence]
SentenceSource=source_sentence
SentenceDestination=destination_sentence
CustomSentIdx=sentence_index

[desk_columns]
Quotation=quotation
WordSource=lemma
WordSourceInflectedForm=inflected
SentenceSource=sentence_source
SentenceDestination=sentence_destination
WordDestination=word_translation
CustomSelected=selected
CustomSentIdx=sentence_index

[desk_editable]
editable_columns=WordSource,WordDestination,WordSourceInflectedForm,CustomSelected
"""

REORDERED_CUSTOM_HEADERS = [
    "token_order",
    "WordDestination",
    "CustomSentIdx",
    "WordSource",
    "WordSourceInflectedForm",
    "CustomSelected",
    "Quotation",
    "SentenceSource",
    "SentenceDestination",
]


def _configure_world(server, adapter, tmp_path, *, freq_lines=("Alpha", "Baum", "Zebra"), custom_mapping=False):
    freq_file = tmp_path / "de_freq.txt"
    freq_file.write_text("\n".join(freq_lines) + "\n", encoding="utf-8")

    server.resolved_paths["kardenwort_workspace"] = tmp_path
    adapter.resolved_paths["kardenwort_workspace"] = tmp_path
    if custom_mapping:
        mapping_file = tmp_path / "anki-mapping.ini"
        mapping_file.write_text(CUSTOM_MAPPING_INI, encoding="utf-8")
        server.resolved_paths["anki_mapping_file"] = mapping_file
        adapter.resolved_paths["anki_mapping_file"] = mapping_file

    server.config.read_dict(
        {
            "settings": {
                "default_language": "de",
                "default_target_language": "ru",
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
    return freq_file


def _seed_repeated_token_zero_session(adapter, *, zid=ZID):
    """Seed two sentences both with local token_order=0: Zebra (S1,T0) and Alpha (S2,T0)."""
    headers = [
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
    sentences = [
        {
            "session_zid": zid,
            "sentence_index": 1,
            "sentence_source": "Zebra.",
            "sentence_destination": "Зебра.",
        },
        {
            "session_zid": zid,
            "sentence_index": 2,
            "sentence_source": "Alpha.",
            "sentence_destination": "Альфа.",
        },
    ]
    rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra.", "Зебра.", "0", "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "2", "Alpha.", "Альфа.", "0", "0"],
    ]
    adapter.save_session(
        session_zid=zid,
        slug="hydrated-coords",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Zebra.\nAlpha.",
        headers=headers,
        data_rows=rows,
        sentences=sentences,
    )
    return "Zebra.\nAlpha."


def _reordered_custom_rows_from_sqlite(adapter, *, zid=ZID):
    """Build positional rows matching REORDERED_CUSTOM_HEADERS directly from SQLite state."""
    words = adapter.db.get_words_by_session(zid)
    sents = {s["sentence_index"]: s for s in adapter.db.get_sentences_by_session(zid)}
    rows = []
    for w in words:
        s_idx = int(w["sentence_index"])
        sent = sents.get(s_idx, {})
        rows.append(
            [
                str(w["token_order"]),
                str(w.get("word_destination") or ""),
                str(s_idx),
                str(w.get("lemma") or ""),
                str(w.get("inflected_form") or w.get("lemma") or ""),
                "1" if w.get("selected") else "0",
                str(w.get("quotation") or w.get("lemma") or ""),
                str(sent.get("sentence_source") or ""),
                str(sent.get("sentence_destination") or ""),
            ]
        )
    return rows


def _empty_tbody(html: str) -> str:
    return re.sub(
        r'(<table[^>]*id="lemma-table"[^>]*>.*?<tbody>)(.*?)(</tbody>)',
        r"\1\3",
        html,
        count=1,
        flags=re.DOTALL,
    )


def _replace_json_island(html: str, island_id: str, payload: dict) -> str:
    serialized = desk.serialize_json_island(payload)
    return re.sub(
        rf'<script id="{re.escape(island_id)}" type="application/json">.*?</script>',
        lambda _: f'<script id="{island_id}" type="application/json">\n{serialized}\n</script>',
        html,
        count=1,
        flags=re.DOTALL,
    )


def _extract_json_island(html: str, island_id: str) -> dict:
    match = re.search(
        rf'<script id="{re.escape(island_id)}" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, f"Missing JSON island #{island_id}"
    return json.loads(match.group(1).strip())


def _render_empty_hydrated_lookup_html(server, source_text, headers, data_rows, *, zid=ZID, fingerprint=None):
    html = desk.render_lookup_html(
        text=source_text,
        language="de",
        target_lang="ru",
        config=server.config,
        resolved_paths=server.resolved_paths,
        zid=zid,
        goldendict={
            "sections": ["source", "translation", "lemmas"],
            "lemma_columns": ["inflected", "lemma", "translation"],
            "server_enabled": True,
            "server_api_key": TOKEN,
            "server_port": server.server_port,
        },
        comments=[],
        headers=headers,
        data_rows=data_rows,
        sentence_translation="Зебра.\nАльфа.",
        session_zid=zid,
        api_token=TOKEN,
        server_enabled=True,
        fingerprint=fingerprint if fingerprint is not None else desk.compute_content_fingerprint(data_rows),
    )
    return _empty_tbody(html)


def _open_page(page, url, html):
    page.set_default_timeout(6000)
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.set_extra_http_headers({"X-API-Token": TOKEN})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(url + "/", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.kwToggleTag === 'function'")
    return errors


def _word_selection_by_coord(adapter, zid=ZID):
    return {
        (int(w["sentence_index"]), int(w["token_order"])): int(w["selected"])
        for w in adapter.db.get_words_by_session(zid)
    }


def test_hydrated_checkbox_custom_reordered_headers_repeated_token_order_zero(
    page, tmp_path, running_controller
):
    """Hydrated checkbox click with custom/reordered headers and repeated token_order=0 persists exact SQLite target and survives reload."""
    url, server, adapter = running_controller
    _configure_world(server, adapter, tmp_path, freq_lines=("Alpha", "Zebra"), custom_mapping=True)
    source_text = _seed_repeated_token_zero_session(adapter)

    rows_initial = _reordered_custom_rows_from_sqlite(adapter)
    restored_fp = desk.compute_content_fingerprint(adapter.restore_session(ZID)["data_rows"])
    html = _render_empty_hydrated_lookup_html(
        server, source_text, REORDERED_CUSTOM_HEADERS, rows_initial, fingerprint=restored_fp
    )

    errors = _open_page(page, url, html)
    try:
        rows_loc = page.locator("#lemma-table tbody tr[data-row-id]")
        expect(rows_loc).to_have_count(2)

        # Frequency sort places Alpha (S2, T0) at display row 0 and Zebra (S1, T0) at display row 1.
        row0 = rows_loc.nth(0)
        row1 = rows_loc.nth(1)
        assert row0.locator("td.col-lemma").inner_text().strip() == "Alpha"
        assert row1.locator("td.col-lemma").inner_text().strip() == "Zebra"

        # Both <tr> and .kw-tag-checkbox must carry authoritative occurrence coordinates.
        assert row0.get_attribute("data-row-id") == "0"
        assert row0.get_attribute("data-sentence-idx") == "2"
        assert row0.get_attribute("data-token-order") == "0"
        cb0 = row0.locator("input.kw-tag-checkbox")
        assert cb0.get_attribute("data-row-id") == "0"
        assert cb0.get_attribute("data-sentence-idx") == "2"
        assert cb0.get_attribute("data-token-order") == "0"
        assert not cb0.is_checked()

        assert row1.get_attribute("data-row-id") == "1"
        assert row1.get_attribute("data-sentence-idx") == "1"
        assert row1.get_attribute("data-token-order") == "0"
        cb1 = row1.locator("input.kw-tag-checkbox")
        assert cb1.get_attribute("data-row-id") == "1"
        assert cb1.get_attribute("data-sentence-idx") == "1"
        assert cb1.get_attribute("data-token-order") == "0"
        assert not cb1.is_checked()

        # Toggle display row 0 (Alpha -> sentence 2, token 0) via real checkbox click.
        with page.expect_response(lambda r: "/session/save" in r.url) as save_info:
            cb0.click()
        assert save_info.value.status == 200, save_info.value.text()
        expect(cb0).to_be_checked()

        # SQLite must update ONLY (sentence_index=2, token_order=0); sentence 1 token 0 stays unselected.
        assert _word_selection_by_coord(adapter) == {(1, 0): 0, (2, 0): 1}

        # Reload hydrated view from SQLite state and verify selection persists on Alpha only.
        rows_reloaded = _reordered_custom_rows_from_sqlite(adapter)
        html_reloaded = _render_empty_hydrated_lookup_html(
            server, source_text, REORDERED_CUSTOM_HEADERS, rows_reloaded
        )
        page.unroute(url + "/")
        page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html_reloaded))
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof window.kwToggleTag === 'function'")

        reloaded_rows = page.locator("#lemma-table tbody tr[data-row-id]")
        expect(reloaded_rows).to_have_count(2)
        assert reloaded_rows.nth(0).locator("td.col-lemma").inner_text().strip() == "Alpha"
        expect(reloaded_rows.nth(0).locator("input.kw-tag-checkbox")).to_be_checked()
        expect(reloaded_rows.nth(1).locator("input.kw-tag-checkbox")).not_to_be_checked()
    finally:
        page.context.close()
        assert errors == []


def test_hydrated_checkbox_direct_api_v1_tag_submits_exact_coordinates(
    page, tmp_path, running_controller
):
    """Direct kwToggleTag -> POST /api/v1/tag on a hydrated checkbox sends exact sentence_idx and token_order without coordinate error."""
    url, server, adapter = running_controller
    _configure_world(server, adapter, tmp_path, freq_lines=("Alpha", "Zebra"), custom_mapping=True)
    source_text = _seed_repeated_token_zero_session(adapter)

    rows_initial = _reordered_custom_rows_from_sqlite(adapter)
    restored_fp = desk.compute_content_fingerprint(adapter.restore_session(ZID)["data_rows"])
    html = _render_empty_hydrated_lookup_html(
        server, source_text, REORDERED_CUSTOM_HEADERS, rows_initial, fingerprint=restored_fp
    )

    errors = _open_page(page, url, html)
    try:
        cb0 = page.locator('#lemma-table tbody tr[data-row-id="0"] input.kw-tag-checkbox')
        expect(cb0).to_be_visible()
        assert cb0.get_attribute("data-sentence-idx") == "2"
        assert cb0.get_attribute("data-token-order") == "0"

        # Force the direct /api/v1/tag path in kwToggleTag.
        page.evaluate(
            """() => {
                window.toggleRowSelection = undefined;
                window.notifyAHKSelection = undefined;
                if (!document.getElementById('kw-interactive-status')) {
                    const s = document.createElement('span');
                    s.id = 'kw-interactive-status';
                    document.body.appendChild(s);
                }
            }"""
        )

        with page.expect_response(lambda r: "/api/v1/tag" in r.url) as tag_info:
            cb0.click()
        resp = tag_info.value
        assert resp.status == 200, resp.text()
        sent = json.loads(resp.request.post_data or "{}")
        assert str(sent.get("sentence_idx")) == "2"
        assert str(sent.get("token_order")) == "0"
        assert sent.get("status") is True

        status_text = page.locator("#kw-interactive-status").inner_text()
        assert "Missing row coordinates" not in status_text
        assert _word_selection_by_coord(adapter) == {(1, 0): 0, (2, 0): 1}
    finally:
        page.context.close()
        assert errors == []


def test_hydrated_grouped_bindings_preserve_render_group_and_occurrence_targets(
    page, tmp_path, running_controller
):
    """Hydrated grouped row preserves render-row/group identity separately from persistence occurrence targets and updates all grouped SQLite occurrences."""
    url, server, adapter = running_controller
    _configure_world(server, adapter, tmp_path, freq_lines=("Baum", "Zebra"), custom_mapping=False)

    seed_headers = [
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
    sentences = [
        {
            "session_zid": ZID,
            "sentence_index": 1,
            "sentence_source": "Zebra Baum.",
            "sentence_destination": "Зебра дерево.",
        },
        {
            "session_zid": ZID,
            "sentence_index": 2,
            "sentence_source": "Baum.",
            "sentence_destination": "Дерево.",
        },
    ]
    seed_rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra Baum.", "Зебра дерево.", "0", "0"],
        ["Baum", "Baum", "Baum", "дерево1", "1", "Zebra Baum.", "Зебра дерево.", "0", "1"],
        ["Baum", "Baum", "Baum", "дерево2", "2", "Baum.", "Дерево.", "0", "0"],
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="grouped-hydrated",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Zebra Baum.\nBaum.",
        headers=seed_headers,
        data_rows=seed_rows,
        sentences=sentences,
    )

    # Grouped payload: display row 0 ("Baum") binds render-group IDs "10,20" and persistence
    # occurrences (1,1) + (2,0); display row 1 ("Zebra") binds (1,0) only.
    grouped_headers = [
        "TokenOrder",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "DeskSelected",
        "all_row_ids",
    ]
    grouped_rows = [
        ["1", "Baum", "Baum", "дерево1", "1", "0", "10,20"],
        ["0", "Zebra", "Zebra", "зебра", "1", "0", "30"],
    ]
    html = _render_empty_hydrated_lookup_html(server, "Zebra Baum.\nBaum.", grouped_headers, grouped_rows)

    # Supply authoritative row/group occurrence manifest in both bootstrap and session islands.
    occurrence_manifest = [
        [{"sentence_idx": 1, "token_order": 1}, {"sentence_idx": 2, "token_order": 0}],
        [{"sentence_idx": 1, "token_order": 0}],
    ]
    for island_id in ("session-data", "kardenwort-bootstrap"):
        payload = _extract_json_island(html, island_id)
        if island_id == "kardenwort-bootstrap":
            payload["row_occurrence_targets"] = occurrence_manifest
            if isinstance(payload.get("session_data"), dict):
                payload["session_data"]["row_occurrence_targets"] = occurrence_manifest
        else:
            payload["row_occurrence_targets"] = occurrence_manifest
        html = _replace_json_island(html, island_id, payload)

    errors = _open_page(page, url, html)
    try:
        rows_loc = page.locator("#lemma-table tbody tr[data-row-id]")
        expect(rows_loc).to_have_count(2)

        baum_row = rows_loc.nth(0)
        zebra_row = rows_loc.nth(1)
        assert baum_row.locator("td.col-lemma").inner_text().strip() == "Baum"
        assert baum_row.get_attribute("data-row-id") == "0"
        assert baum_row.get_attribute("data-all-row-ids") == "10,20"
        assert json.loads(baum_row.get_attribute("data-occurrence-targets") or "[]") == [
            {"sentence_idx": 1, "token_order": 1},
            {"sentence_idx": 2, "token_order": 0},
        ]

        baum_cb = baum_row.locator("input.kw-tag-checkbox")
        assert baum_cb.get_attribute("data-row-id") == "0"
        assert baum_cb.get_attribute("data-all-row-ids") == "10,20"
        assert baum_cb.get_attribute("data-sentence-idx") == "1"
        assert baum_cb.get_attribute("data-token-order") == "1"

        assert zebra_row.get_attribute("data-row-id") == "1"
        assert zebra_row.get_attribute("data-all-row-ids") == "30"
        assert json.loads(zebra_row.get_attribute("data-occurrence-targets") or "[]") == [
            {"sentence_idx": 1, "token_order": 0}
        ]

        with page.expect_response(lambda r: "/session/save" in r.url) as save_info:
            baum_cb.click()
        assert save_info.value.status == 200, save_info.value.text()

        # Both grouped Baum occurrences (1,1) and (2,0) must be selected; Zebra (1,0) untouched.
        assert _word_selection_by_coord(adapter) == {(1, 0): 0, (1, 1): 1, (2, 0): 1}
    finally:
        page.context.close()
        assert errors == []


@pytest.mark.parametrize(
    "case_name,headers,data_rows,extra_manifest",
    [
        (
            "missing_sentence_header",
            ["TokenOrder", "WordSource", "WordSourceInflectedForm", "WordDestination", "DeskSelected"],
            [
                ["0", "Alpha", "Alpha", "альфа", "0"],
                ["0", "Zebra", "Zebra", "зебра", "0"],
            ],
            None,
        ),
        (
            "missing_token_header",
            ["SentenceSourceIndex", "WordSource", "WordSourceInflectedForm", "WordDestination", "DeskSelected"],
            [
                ["2", "Alpha", "Alpha", "альфа", "0"],
                ["1", "Zebra", "Zebra", "зебра", "0"],
            ],
            None,
        ),
        (
            "ambiguous_sentence_headers",
            ["SentenceSourceIndex", "sentence_idx", "TokenOrder", "WordSource", "WordDestination", "DeskSelected"],
            [
                ["2", "1", "0", "Alpha", "альфа", "0"],
                ["1", "2", "0", "Zebra", "зебра", "0"],
            ],
            None,
        ),
        (
            "malformed_later_row_cell",
            ["SentenceSourceIndex", "TokenOrder", "WordSource", "WordDestination", "DeskSelected"],
            [
                ["2", "0", "Alpha", "альфа", "0"],
                ["0", "0", "Zebra", "зебра", "0"],
            ],
            None,
        ),
        (
            "incompatible_grouped_binding",
            ["SentenceSourceIndex", "TokenOrder", "WordSource", "WordDestination", "DeskSelected", "all_row_ids"],
            [
                ["2", "0", "Alpha", "альфа", "0", "10,20"],
                ["1", "0", "Zebra", "зебра", "0", "30"],
            ],
            {"row_occurrence_targets": [[{"sentence_idx": 0, "token_order": -1}], [{"sentence_idx": 1, "token_order": 0}]]},
        ),
    ],
)
def test_hydrated_invalid_or_ambiguous_manifest_renders_no_partial_dom_or_mutations(
    page, tmp_path, running_controller, case_name, headers, data_rows, extra_manifest
):
    """Missing, ambiguous, malformed, or incompatible grouped manifests fail explicitly with zero partial rows and zero mutations."""
    url, server, adapter = running_controller
    _configure_world(server, adapter, tmp_path, freq_lines=("Alpha", "Zebra"), custom_mapping=False)
    source_text = _seed_repeated_token_zero_session(adapter)

    # Render a valid shell first, then replace its JSON islands with the invalid manifest
    # so client-side hydrateSession() receives the exact malformed payload over HTTP.
    base_rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra.", "Зебра.", "0", "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "2", "Alpha.", "Альфа.", "0", "0"],
    ]
    base_headers = [
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
    html = _render_empty_hydrated_lookup_html(server, source_text, base_headers, base_rows)
    for island_id in ("session-data", "kardenwort-bootstrap"):
        payload = _extract_json_island(html, island_id)
        if island_id == "kardenwort-bootstrap":
            payload["headers"] = headers
            payload["data_rows"] = data_rows
            if extra_manifest:
                payload.update(extra_manifest)
            if isinstance(payload.get("session_data"), dict):
                payload["session_data"]["headers"] = headers
                payload["session_data"]["data_rows"] = data_rows
                if extra_manifest:
                    payload["session_data"].update(extra_manifest)
        else:
            payload["headers"] = headers
            payload["data_rows"] = data_rows
            if extra_manifest:
                payload.update(extra_manifest)
        html = _replace_json_island(html, island_id, payload)

    mutation_requests = []

    def _track_request(req):
        if any(ep in req.url for ep in ("/session/save", "/api/v1/tag", "/api/v1/export")):
            mutation_requests.append(req.url)

    page.on("request", _track_request)
    errors = _open_page(page, url, html)
    try:
        # Must NOT insert any partial interactive rows or checkboxes.
        assert page.locator("#lemma-table tbody tr").count() == 0, (
            f"[{case_name}] Expected zero partial table rows on invalid identity manifest"
        )
        assert page.locator("#lemma-table tbody input.kw-tag-checkbox").count() == 0, (
            f"[{case_name}] Expected zero interactive checkboxes on invalid identity manifest"
        )

        # Must report an explicit error (page error, toast, or status indicator).
        has_error_toast = page.locator(".kw-toast-error").count() > 0
        status_el = page.locator("#kw-interactive-status")
        has_status_error = status_el.count() > 0 and bool(status_el.inner_text().strip())
        assert errors or has_error_toast or has_status_error, (
            f"[{case_name}] Expected explicit error signal on invalid identity manifest"
        )

        # Must issue zero mutation requests and leave SQLite completely untouched.
        assert mutation_requests == [], f"[{case_name}] Unexpected mutation requests: {mutation_requests}"
        assert _word_selection_by_coord(adapter) == {(1, 0): 0, (2, 0): 0}
    finally:
        page.context.close()
