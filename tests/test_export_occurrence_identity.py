"""Occurrence-safe explicit SQLite export acceptance contracts.

Verifies browser -> HTTP -> SQLite -> TSV identity resolution across:
- Frequency-sorted tables where display order differs from SQLite storage order
- Both /session/export (onSendToAnkiClick) and /api/v1/export (kwExportSelected)
- selected, unselected, and all export modes plus project deck mapping
- Repeated token_order across multiple sentences
- Compound / merged overview rows and token-scoped vs row-scoped selection
- Filtered / reordered views and legacy visual-only manifest compatibility
- Structured rejection of missing/invalid/ambiguous explicit targets before side effects
"""

import csv
import json
from pathlib import Path
import urllib.error
import urllib.request

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN, post


ZID = "20261119120001"
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


def _configure_frequency_sqlite_world(server, tmp_path, *, selection_mode="selected", filter_selected=False):
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir(parents=True, exist_ok=True)
    freq_file = tmp_path / "de_freq.txt"
    freq_file.write_text("Alpha\nHaus\nZebra\nBaum\nBeta\n", encoding="utf-8")

    server.resolved_paths["favorites_output_dir"] = fav_dir
    server.resolved_paths["kardenwort_workspace"] = tmp_path
    server.config.read_dict(
        {
            "settings": {
                "default_language": "de",
                "default_target_language": "ru",
                "favorites_output_dir": str(fav_dir),
                "favorites_prefix": "",
                "save_to_favorites_on_export": "true",
                "send_to_anki_after_export": "false",
                "export_selection_mode": selection_mode,
            },
            "languages": {
                "de_lemma_index": str(freq_file),
            },
            "rendering": {
                "highlight_same_lemma": "false",
                "filter_selected_only": "true" if filter_selected else "false",
                "auto_save_on_edit": "false",
            },
            "sentences_mode": {
                "enabled": "true",
                "delivery_mode": "container",
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


def _seed_zebra_alpha(adapter, *, zid=ZID, zebra_selected=0, alpha_selected=0):
    rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra Alpha.", "Зебра Альфа.", str(zebra_selected), "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "1", "Zebra Alpha.", "Зебра Альфа.", str(alpha_selected), "1"],
    ]
    sentences = [
        {
            "session_zid": zid,
            "sentence_index": 1,
            "sentence_source": "Zebra Alpha.",
            "sentence_destination": "Зебра Альфа.",
        }
    ]
    adapter.save_session(
        session_zid=zid,
        slug="zebra-alpha",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=sentences,
    )


def _read_exported_tsv(fav_dir: Path):
    files = list(fav_dir.glob("*.tsv"))
    assert len(files) == 1, f"Expected 1 exported TSV in {fav_dir}, got {files}"
    lines = [line for line in files[0].read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def _get_json(url, path, token=TOKEN):
    req = urllib.request.Request(url + path, headers={"X-API-Token": token})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.load(resp)


def _open_session_page(page, url, html):
    page.set_default_timeout(6000)
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.set_extra_http_headers({"X-API-Token": TOKEN})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(url + "/", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.onSendToAnkiClick === 'function'")
    return errors


@pytest.mark.parametrize("selection_mode", ["selected", "unselected"])
def test_frequency_sorted_browser_session_export_matches_visual_alpha(
    page, tmp_path, running_controller, selection_mode
):
    """Selecting visual Alpha (row 0) when SQLite order is Zebra, Alpha must export/select Alpha only."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode=selection_mode)
    _seed_zebra_alpha(adapter)

    html = desk.run_render_flow(
        text="Zebra Alpha.",
        language="de",
        zid=ZID,
        text_mode="single",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )
    errors = _open_session_page(page, url, html)
    try:
        # Frequency sort places Alpha first (data-row-id="0") and Zebra second (data-row-id="1").
        row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
        expect(row0).to_contain_text("Alpha")
        if selection_mode == "unselected":
            with page.expect_response("**/session/save") as saved:
                row0.click()
            assert saved.value.status == 200
            for w in adapter.db.get_words_by_session(ZID):
                adapter.db.update_word(w["id"], {"selected": 0}, zid=ZID)
        else:
            row0.click()

        with page.expect_response("**/session/export") as exported:
            page.locator("#kw-btn-export").click()
        assert exported.value.status == 200, exported.value.text()

        tsv_rows = _read_exported_tsv(fav_dir)
        assert len(tsv_rows) == 1
        words_after = adapter.db.get_words_by_session(ZID)
        by_lemma = {w["lemma"]: w for w in words_after}

        if selection_mode == "selected":
            assert tsv_rows[0]["WordSource"] == "Alpha"
            assert tsv_rows[0]["WordDestination"] == "альфа"
            assert by_lemma["Alpha"]["selected"] == 1
            assert by_lemma["Zebra"]["selected"] == 0
        else:
            assert tsv_rows[0]["WordSource"] == "Zebra"
            assert tsv_rows[0]["WordDestination"] == "зебра"
            assert by_lemma["Zebra"]["selected"] == 0
            assert by_lemma["Alpha"]["selected"] == 0
    finally:
        page.context.close()
        assert errors == []


def test_frequency_sorted_api_v1_export_matches_visual_alpha(page, tmp_path, running_controller):
    """window.kwExportSelected -> /api/v1/export must export/select visual Alpha when SQLite is Zebra, Alpha."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    _seed_zebra_alpha(adapter)

    status, rendered = _get_json(url, f"/api/v1/lookup?session_zid={ZID}&language=de")
    assert status == 200, rendered
    html = rendered.get("data", rendered)["html"]
    errors = _open_session_page(page, url, html)
    try:
        row0 = page.locator('#lemma-table tbody tr[data-row-id="0"]')
        expect(row0).to_contain_text("Alpha")
        cb0 = row0.locator("input.kw-tag-checkbox")
        cb0.check()
        expect(cb0).to_be_checked()

        # Clear DB selected flags so /api/v1/export proves its own selection update.
        for w in adapter.db.get_words_by_session(ZID):
            adapter.db.update_word(w["id"], {"selected": 0}, zid=ZID)

        with page.expect_response("**/api/v1/export") as exported:
            page.evaluate("window.kwExportSelected()")
        assert exported.value.status == 200, exported.value.text()

        tsv_rows = _read_exported_tsv(fav_dir)
        assert [r["WordSource"] for r in tsv_rows] == ["Alpha"]
        words_after = {w["lemma"]: w for w in adapter.db.get_words_by_session(ZID)}
        assert words_after["Alpha"]["selected"] == 1
        assert words_after["Zebra"]["selected"] == 0
    finally:
        page.context.close()
        assert errors == []


def test_repeated_token_order_across_sentences_exports_exact_occurrence(page, tmp_path, running_controller):
    """Repeated local token_order=0 across sentences must export/select only the chosen sentence's occurrence."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra.", "Зебра.", "0", "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "2", "Alpha.", "Альфа.", "0", "0"],
    ]
    sentences = [
        {"session_zid": ZID, "sentence_index": 1, "sentence_source": "Zebra.", "sentence_destination": "Зебра."},
        {"session_zid": ZID, "sentence_index": 2, "sentence_source": "Alpha.", "sentence_destination": "Альфа."},
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="repeated-token-order",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra. Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=sentences,
    )

    html = desk.run_render_flow(
        text="Zebra. Alpha.",
        language="de",
        zid=ZID,
        text_mode="single",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )
    errors = _open_session_page(page, url, html)
    try:
        # Tab 3 corresponds to Sentence 2 ("Alpha.").
        page.evaluate("WorkspaceTabs.switchToTab(3)")
        page.locator("#lemma-table tbody tr").first.click()
        with page.expect_response("**/session/export") as exported:
            page.locator("#kw-btn-export").click()
        assert exported.value.status == 200, exported.value.text()

        tsv_rows = _read_exported_tsv(fav_dir)
        assert len(tsv_rows) == 1
        assert tsv_rows[0]["WordSource"] == "Alpha"
        assert tsv_rows[0]["SentenceSourceIndex"] == "2"

        words_after = {(w["sentence_index"], w["token_order"]): w for w in adapter.db.get_words_by_session(ZID)}
        assert words_after[(2, 0)]["selected"] == 1
        assert words_after[(1, 0)]["selected"] == 0
    finally:
        page.context.close()
        assert errors == []


@pytest.mark.parametrize("endpoint", ["/session/export", "/api/v1/export"])
@pytest.mark.parametrize(
    "payload_patch",
    [
        {"selected_row_ids": [0], "target_coordinates": [{"sentence_idx": 99, "token_order": 0}]},
        {"selected_row_ids": [0], "target_coordinates": [{"token_order": 0}]},
        {"selected_row_ids": [0], "target_coordinates": [{"sentence_idx": 0, "token_order": 0}]},
        {"selected_row_ids": [0], "target_coordinates": [{"visual_idx": 0}, {"sentence_idx": 1, "token_order": 0}]},
        {"selected_row_ids": [99]},
        {"selected_row_ids": [0]},  # Visual-only without manifest when frequency sorting diverges from DB order
    ],
)
def test_invalid_or_ambiguous_explicit_export_rejects_without_fallback_or_effects(
    tmp_path, running_controller, endpoint, payload_patch
):
    """Invalid/ambiguous explicit targets must return structured 4xx error, never 'skipped' or fallback to persisted selection."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    # Seed unrelated persisted selection on Beta (token 2) while Zebra(0), Alpha(1) are unselected.
    rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra Alpha Beta.", "Зебра Альфа Бета.", "0", "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "1", "Zebra Alpha Beta.", "Зебра Альфа Бета.", "0", "1"],
        ["Beta", "Beta", "Beta", "бета", "1", "Zebra Alpha Beta.", "Зебра Альфа Бета.", "1", "2"],
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="invalid-target",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra Alpha Beta.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[{"session_zid": ZID, "sentence_index": 1, "sentence_source": "Zebra Alpha Beta."}],
    )
    before = adapter.db.get_words_by_session(ZID)

    body = {"session_zid": ZID, "language": "de", "selected_row_ids": [0], **payload_patch}
    status, response = post(url, endpoint, body)

    assert 400 <= status < 500, response
    assert response.get("status") == "error", response
    assert response.get("error_code"), response
    assert list(fav_dir.glob("*.tsv")) == []
    assert adapter.db.get_words_by_session(ZID) == before


def test_ambiguous_token_order_across_sentences_rejects_without_effects(tmp_path, running_controller):
    """Ambiguous token-only target across two sentences must reject before output or DB writes."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    rows = [
        ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra.", "Зебра.", "0", "0"],
        ["Alpha", "Alpha", "Alpha", "альфа", "2", "Alpha.", "Альфа.", "1", "0"],
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="ambig-token",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Zebra. Alpha.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[
            {"session_zid": ZID, "sentence_index": 1, "sentence_source": "Zebra."},
            {"session_zid": ZID, "sentence_index": 2, "sentence_source": "Alpha."},
        ],
    )
    before = adapter.db.get_words_by_session(ZID)

    status, response = post(url, "/session/export", {"session_zid": ZID, "language": "de", "token_orders": [0]})
    assert 400 <= status < 500, response
    assert response.get("status") == "error", response
    assert list(fav_dir.glob("*.tsv")) == []
    assert adapter.db.get_words_by_session(ZID) == before


def test_merged_overview_and_token_scoped_filtered_export(page, tmp_path, running_controller):
    """Merged overview row unrolls all occurrences; clicking a single source token exports only that occurrence."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected", filter_selected=True)
    # Seed Haus initially unselected; select it on Overview tab, then verify filtered/selected export.
    rows = [
        ["Baum", "Baum", "Baum", "дерево", "1", "Baum Haus.", "Дерево дом.", "0", "0"],
        ["Haus", "Haus", "Haus", "дом-1", "1", "Baum Haus.", "Дерево дом.", "0", "1"],
        ["Haus", "Haus", "Haus", "дом-2", "2", "Haus.", "Дом.", "0", "0"],
    ]
    adapter.save_session(
        session_zid=ZID,
        slug="merged-overview",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Baum Haus. Haus.",
        headers=HEADERS,
        data_rows=rows,
        sentences=[
            {"session_zid": ZID, "sentence_index": 1, "sentence_source": "Baum Haus."},
            {"session_zid": ZID, "sentence_index": 2, "sentence_source": "Haus."},
        ],
    )

    html = desk.run_render_flow(
        text="Baum Haus. Haus.",
        language="de",
        zid=ZID,
        text_mode="single",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )
    errors = _open_session_page(page, url, html)
    try:
        # Switch to Overview tab (tab seq 1) where Haus occurrences are merged.
        page.evaluate("WorkspaceTabs.switchToTab(1)")
        haus_row = page.locator("#lemma-table tbody tr", has_text="Haus").first
        haus_row.click()
        with page.expect_response("**/session/export") as exported:
            page.locator("#kw-btn-export").click()
        assert exported.value.status == 200, exported.value.text()

        tsv_rows = _read_exported_tsv(fav_dir)
        assert sorted((r["SentenceSourceIndex"], r["TokenOrder"]) for r in tsv_rows) == [("1", "1"), ("2", "0")]
        words_after = {(w["sentence_index"], w["token_order"]): w for w in adapter.db.get_words_by_session(ZID)}
        assert words_after[(1, 1)]["selected"] == 1
        assert words_after[(2, 0)]["selected"] == 1
        assert words_after[(1, 0)]["selected"] == 0

        # Deselect Haus in UI and reset DB selected flags, then select only Sentence 2's Haus token in source text.
        haus_row.click()
        for f in fav_dir.glob("*.tsv"):
            f.unlink()
        for w in adapter.db.get_words_by_session(ZID):
            adapter.db.update_word(w["id"], {"selected": 0}, zid=ZID)

        page.evaluate("WorkspaceTabs.switchToTab(3)")
        page.locator('#source-container span.word:visible:has-text("Haus")').first.click()
        with page.expect_response("**/session/export") as exported_tok:
            page.locator("#kw-btn-export").click()
        assert exported_tok.value.status == 200, exported_tok.value.text()

        tsv_rows_tok = _read_exported_tsv(fav_dir)
        assert [(r["SentenceSourceIndex"], r["TokenOrder"]) for r in tsv_rows_tok] == [("2", "0")]
        words_tok = {(w["sentence_index"], w["token_order"]): w for w in adapter.db.get_words_by_session(ZID)}
        assert words_tok[(2, 0)]["selected"] == 1
        assert words_tok[(1, 1)]["selected"] == 0
        assert words_tok[(1, 0)]["selected"] == 0
    finally:
        page.context.close()
        assert errors == []


def test_legacy_visual_only_client_resolves_through_active_controller_manifest(tmp_path, running_controller):
    """Legacy visual-only client sending selected_row_ids=[0] resolves through controller's rendered manifest."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    _seed_zebra_alpha(adapter)

    restored = adapter.restore_session(ZID)
    sorted_rows = desk.sort_session_data_rows(
        restored["data_rows"],
        restored["headers"],
        "de",
        server.config,
        server.resolved_paths,
    )
    assert sorted_rows[0][restored["headers"].index("WordSource")] == "Alpha"
    server.arbiter.sessions[ZID] = {
        "session_zid": ZID,
        "language": "de",
        "headers": restored["headers"],
        "data_rows": sorted_rows,
    }

    status, response = post(url, "/session/export", {"session_zid": ZID, "language": "de", "selected_row_ids": [0]})
    assert status == 200, response
    tsv_rows = _read_exported_tsv(fav_dir)
    assert [r["WordSource"] for r in tsv_rows] == ["Alpha"]
    words_after = {w["lemma"]: w for w in adapter.db.get_words_by_session(ZID)}
    assert words_after["Alpha"]["selected"] == 1
    assert words_after["Zebra"]["selected"] == 0


def test_legacy_visual_only_client_after_unsorted_progressive_session_update(tmp_path, running_controller):
    """Legacy visual-only export resolves display row 0 (Alpha) even when arbiter.sessions holds unsorted storage rows."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    _seed_zebra_alpha(adapter)

    restored = adapter.restore_session(ZID)
    # Mirror progressive translated_text / translated / retranslate updates which write storage-order rows.
    assert restored["data_rows"][0][restored["headers"].index("WordSource")] == "Zebra"
    server.arbiter.sessions[ZID] = {
        "session_zid": ZID,
        "language": "de",
        "headers": restored["headers"],
        "data_rows": [list(r) for r in restored["data_rows"]],
    }

    status, response = post(url, "/session/export", {"session_zid": ZID, "language": "de", "selected_row_ids": [0]})
    assert status == 200, response
    tsv_rows = _read_exported_tsv(fav_dir)
    assert [r["WordSource"] for r in tsv_rows] == ["Alpha"]
    words_after = {w["lemma"]: w for w in adapter.db.get_words_by_session(ZID)}
    assert words_after["Alpha"]["selected"] == 1
    assert words_after["Zebra"]["selected"] == 0


def test_implicit_selection_and_project_deck_preserved(tmp_path, running_controller):
    """Implicit persisted selection (selected_row_ids=None) and project hierarchical Deck remain intact."""
    url, server, adapter = running_controller
    fav_dir = _configure_frequency_sqlite_world(server, tmp_path, selection_mode="selected")
    _seed_zebra_alpha(adapter, zebra_selected=0, alpha_selected=1)

    proj_id = adapter.db.create_project(title="DeckTest", description="", slug="decktest")
    adapter.db.link_session_to_project(proj_id, ZID, order_index=0)

    res = desk.core_export(
        tsv_path_or_session=ZID,
        selected_row_ids=None,
        config=server.config,
        resolved_paths=server.resolved_paths,
        zid=ZID,
        language="de",
    )
    assert res.get("import_complete") is True or res.get("status") == "success"
    tsv_rows = _read_exported_tsv(fav_dir)
    assert [r["WordSource"] for r in tsv_rows] == ["Alpha"]
    assert tsv_rows[0]["DeskSelected"] == "1"
    assert "DeckTest" in tsv_rows[0].get("Deck", "")
