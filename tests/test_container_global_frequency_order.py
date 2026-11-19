"""Global container vocabulary frequency ordering and lifecycle consumer contracts.

Phase 1 acceptance & lifecycle control tests for:
- Global frequency ranking across multi-sentence containers (`sort_session_data_rows` & `run_render_flow`)
- Immutable SQLite `(sentence_index, token_order)` coordinates & preserved source/card order
- Repeated local `token_order=0` across sentences and merged Overview groups (`all_row_ids`)
- Cold & memoized `/session/status`, progressive completion, and `hydrateSession` consumers
- Edit, selection, and refresh read-back stability
"""

import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_controller import SessionArbiter
from kardenwort_events import EventDispatcher
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import TOKEN, post


ZID = "20261119140001"
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


def _configure_owned_world(
    server,
    tmp_path,
    *,
    lang="de",
    freq_lines=("Alpha", "Baum", "Zebra"),
    delivery_mode="container",
    dedup_scope="sentence",
    sentences_enabled=True,
):
    freq_file = tmp_path / f"{lang}_freq.txt"
    freq_file.write_text("\n".join(freq_lines) + "\n", encoding="utf-8")

    server.resolved_paths["kardenwort_workspace"] = tmp_path
    server.config.read_dict(
        {
            "settings": {
                "default_language": lang,
                "default_target_language": "ru",
            },
            "languages": {
                f"{lang}_lemma_index": str(freq_file),
            },
            "rendering": {
                "highlight_same_lemma": "false",
                "filter_selected_only": "false",
                "auto_save_on_edit": "false",
            },
            "sentences_mode": {
                "enabled": "true" if sentences_enabled else "false",
                "delivery_mode": delivery_mode,
                "deduplication_scope": dedup_scope,
                "spawn_order": "forward",
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


def _seed_multi_sentence_session(adapter, *, zid=ZID, lang="de", include_merged_baum=True):
    if include_merged_baum:
        source_text = "Zebra Baum.\nAlpha Baum."
        sentences = [
            {
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": "Zebra Baum.",
                "sentence_destination": "Зебра дерево.",
            },
            {
                "session_zid": zid,
                "sentence_index": 2,
                "sentence_source": "Alpha Baum.",
                "sentence_destination": "Альфа дерево.",
            },
        ]
        rows = [
            ["Zebra", "Zebra", "Zebra", "зебра", "1", "Zebra Baum.", "Зебра дерево.", "0", "0"],
            ["Baum", "Baum", "Baum", "дерево1", "1", "Zebra Baum.", "Зебра дерево.", "0", "1"],
            ["Alpha", "Alpha", "Alpha", "альфа", "2", "Alpha Baum.", "Альфа дерево.", "0", "0"],
            ["Baum", "Baum", "Baum", "дерево2", "2", "Alpha Baum.", "Альфа дерево.", "0", "1"],
        ]
        row_provs = {0: "live:google", 1: "live:google", 2: "live:deepl", 3: "live:deepl"}
    else:
        source_text = "Zebra.\nAlpha."
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
        row_provs = {0: "live:google", 1: "live:deepl"}

    adapter.save_session(
        session_zid=zid,
        slug="zebra-alpha-multi",
        source_language=lang,
        target_language="ru",
        text_mode="multi",
        source_raw_text=source_text,
        headers=HEADERS,
        data_rows=rows,
        sentences=sentences,
        row_provenances=row_provs,
    )
    # Give each occurrence distinct provenance in SQLite so cross-sentence collisions are detectable.
    with adapter.db.transaction() as conn:
        for idx, row in enumerate(rows):
            prov = row_provs[idx]
            conn.execute(
                "UPDATE words SET word_provenance = ? WHERE session_zid = ? AND sentence_index = ? AND token_order = ?",
                (prov, zid, int(row[4]), int(row[8])),
            )
    return source_text


def _open_page(page, url, html):
    page.set_default_timeout(6000)
    page.route(url + "/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.set_extra_http_headers({"X-API-Token": TOKEN})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(url + "/", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.receiveUpdate === 'function'")
    return errors


def _get_json(url, path):
    req = urllib.request.Request(url + path, headers={"X-API-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.load(resp)


def _extract_island(html: str, island_id: str):
    match = re.search(
        rf'<script id="{re.escape(island_id)}"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match, f"Missing JSON island #{island_id}"
    return json.loads(match.group(1).strip())


@pytest.mark.parametrize(
    "lang,freq_lines,rows_in,expected_order",
    [
        (
            "de",
            ("Alpha", "Baum", "Zebra"),
            [
                ["Zebra", "1", "0"],
                ["Baum", "1", "1"],
                ["Alpha", "2", "0"],
                ["Baum", "2", "1"],
            ],
            [("Alpha", "2", "0"), ("Baum", "1", "1"), ("Baum", "2", "1"), ("Zebra", "1", "0")],
        ),
        (
            "en",
            ("alpha", "color", "zebra"),
            [
                ["unlisted-term", "1", "0"],
                ["zebra", "1", "1"],
                ["color/colour", "2", "0"],
                ["alpha", "2", "1"],
            ],
            [("alpha", "2", "1"), ("color/colour", "2", "0"), ("zebra", "1", "1"), ("unlisted-term", "1", "0")],
        ),
    ],
)
def test_container_sort_reproduces_global_order_and_preserves_coordinates(
    tmp_path, lang, freq_lines, rows_in, expected_order
):
    """1.1: Container sorting must rank across sentences globally while preserving immutable coordinates."""
    idx_file = tmp_path / f"{lang}_freq.txt"
    idx_file.write_text("\n".join(freq_lines) + "\n", encoding="utf-8")

    cfg = desk.load_config(Path(desk.__file__).resolve().parent / "config.ini")[0]
    cfg.set(desk.SEC_LANGUAGES, f"{lang}_lemma_index", str(idx_file))
    cfg.set(desk.SEC_SENTENCES_MODE, "delivery_mode", "container")
    paths = {"kardenwort_workspace": tmp_path}
    headers = ["WordSource", "SentenceSourceIndex", "TokenOrder"]
    roles = {"lemma": "WordSource", "sentence_index": "SentenceSourceIndex"}

    snapshot_before = [tuple(r) for r in rows_in]
    global_sorted = desk.sort_rows_by_frequency(rows_in, headers, lang, cfg, paths, role_fields=roles)
    container_sorted = desk.sort_session_data_rows(
        rows_in,
        headers,
        lang,
        cfg,
        paths,
        role_fields=roles,
        source_sentences=["Sentence 1.", "Sentence 2."],
        delivery_mode="container",
    )

    assert [tuple(r) for r in global_sorted] == expected_order
    assert [tuple(r) for r in container_sorted] == expected_order
    assert sorted(tuple(r) for r in container_sorted) == sorted(snapshot_before)


def test_normal_render_container_orders_vocabulary_globally_while_keeping_cards_and_overview(
    tmp_path, running_controller
):
    """1.2 / 1.3: Normal render sorts container rows globally while keeping sentence/card order & Overview groups."""
    url, server, adapter = running_controller
    _configure_owned_world(server, tmp_path, lang="de", freq_lines=("Alpha", "Baum", "Zebra"))
    source_text = _seed_multi_sentence_session(adapter, include_merged_baum=True)

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=ZID,
        text_mode="multi",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )

    cards = _extract_island(html, "sentence-cards")

    # Source sentences and cards keep their original 1..N order.
    assert [c["seq_num"] for c in cards] == [1, 2, 3]
    assert cards[1]["source_text"] == "Zebra Baum."
    assert cards[2]["source_text"] == "Alpha Baum."
    assert [(w["lemma"], w["row_id"], w["token_order"]) for w in cards[1]["words"]] == [
        ("Baum", "1", "1"),
        ("Zebra", "3", "0"),
    ]
    assert [(w["lemma"], w["row_id"], w["token_order"]) for w in cards[2]["words"]] == [
        ("Alpha", "0", "0"),
        ("Baum", "2", "1"),
    ]

    # Overview tab (card 0) keeps global frequency order and valid merged row references.
    overview_words = cards[0]["words"]
    assert [w["lemma"] for w in overview_words] == ["Alpha", "Baum", "Zebra"]
    assert overview_words[1]["all_row_ids"] == ["1", "2"]
    assert overview_words[1]["occurrence_targets"] == [
        {"sentence_idx": 1, "token_order": 1},
        {"sentence_idx": 2, "token_order": 1},
    ]

    # SQLite stored coordinates and values remain untouched.
    db_words = [(w["lemma"], w["sentence_index"], w["token_order"]) for w in adapter.db.get_words_by_session(ZID)]
    assert db_words == [("Zebra", 1, 0), ("Baum", 1, 1), ("Alpha", 2, 0), ("Baum", 2, 1)]


def test_cold_and_memoized_status_updates_preserve_global_order_and_occurrence_routing(
    page, tmp_path, running_controller
):
    """1.2 / 1.3: Cold and memoized /session/status must not corrupt repeated token_order=0 or cardless views."""
    url, server, adapter = running_controller
    _configure_owned_world(server, tmp_path, lang="de", freq_lines=("Alpha", "Baum", "Zebra"))
    source_text = _seed_multi_sentence_session(adapter, include_merged_baum=True)

    # Mark Alpha and Zebra as pending/blank initially in DB? First render with translations, then update DB
    # to simulate background completion arriving via cold /session/status.
    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=ZID,
        text_mode="multi",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )

    # Update SQLite translations & distinct provenances so cold /session/status delivers new values.
    adapter.db.batch_update_words(
        ZID,
        [
            {"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "зебра_cold", "word_provenance": "live:google"}},
            {"sentence_index": 2, "token_order": 0, "updates": {"word_destination": "альфа_cold", "word_provenance": "live:deepl"}},
        ],
    )
    server.arbiter.sessions.clear()

    # Check raw cold /session/status payload carries explicit occurrence coordinates.
    status_code, cold_raw = _get_json(url, f"/session/status?zid={ZID}")
    assert status_code == 200
    cold_payload = cold_raw.get("data", cold_raw)
    assert cold_payload["rows"]["0"]["sentence_idx"] == "1"
    assert cold_payload["rows"]["0"]["token_order"] == "0"

    errors = _open_page(page, url, html)
    try:
        # 1) With WorkspaceTabs active, trigger /session/status update and normalizeCardSkeletons.
        page.locator("#kw-btn-update").click()
        page.wait_for_function(
            "() => window.AppState && window.AppState.rows && "
            "Object.values(window.AppState.rows).some(r => r && r.trans === 'зебра_cold')"
        )
        # Switch to Overview tab (seq 1) and verify order + translations + normalizeCardSkeletons safety.
        page.evaluate(
            """() => {
                const cards = window.WorkspaceTabs.getCards();
                for (const c of cards) {
                    for (const w of (c.words || [])) {
                        if (w.lemma === 'Zebra' || w.lemma === 'Alpha') {
                            w.translation = '<span class="skeleton-loader">Loading...</span>';
                            w.row_html = null;
                        }
                    }
                }
                window.normalizeCardSkeletons('finished');
                window.WorkspaceTabs.switchToTab(1);
            }"""
        )
        overview_rows = page.locator("#lemma-table tbody tr")
        expect(overview_rows).to_have_count(3)
        assert [overview_rows.nth(i).locator("td.col-lemma").inner_text().strip() for i in range(3)] == [
            "Alpha",
            "Baum",
            "Zebra",
        ]
        assert overview_rows.nth(0).locator("td.col-translation").inner_text().strip() == "альфа_cold"
        assert overview_rows.nth(2).locator("td.col-translation").inner_text().strip() == "зебра_cold"

        # Verify sentence tabs keep each occurrence's own translation and provenance.
        page.evaluate("window.WorkspaceTabs.switchToTab(2)")
        expect(page.locator("#lemma-table tbody tr").nth(1).locator("td.col-lemma")).to_have_text("Zebra")
        expect(page.locator("#lemma-table tbody tr").nth(1).locator("td.col-translation")).to_have_text("зебра_cold")

        page.evaluate("window.WorkspaceTabs.switchToTab(3)")
        expect(page.locator("#lemma-table tbody tr").nth(0).locator("td.col-lemma")).to_have_text("Alpha")
        expect(page.locator("#lemma-table tbody tr").nth(0).locator("td.col-translation")).to_have_text("альфа_cold")

        # 2) Memoized /session/status with arbiter memory populated in storage order.
        restored = adapter.restore_session(ZID)
        with server.arbiter._lock:
            server.arbiter.sessions[ZID] = {
                "session_zid": ZID,
                "language": "de",
                "target_lang": "ru",
                "text": source_text,
                "headers": restored["headers"],
                "data_rows": restored["data_rows"],
                "sentence_translation": "Зебра дерево.\nАльфа дерево.",
                "fingerprint": desk.compute_content_fingerprint(restored["data_rows"]),
                "created_at": time.time(),
            }
        adapter.db.batch_update_words(
            ZID,
            [
                {"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "зебра_memo"}},
                {"sentence_index": 2, "token_order": 0, "updates": {"word_destination": "альфа_memo"}},
            ],
        )
        restored2 = adapter.restore_session(ZID)
        with server.arbiter._lock:
            server.arbiter.sessions[ZID]["data_rows"] = restored2["data_rows"]
        page.locator("#kw-btn-update").click()
        page.wait_for_function(
            "() => window.AppState && window.AppState.rows && "
            "Object.values(window.AppState.rows).some(r => r && r.trans === 'альфа_memo')"
        )
        page.evaluate("window.WorkspaceTabs.switchToTab(1)")
        overview_rows = page.locator("#lemma-table tbody tr")
        assert [overview_rows.nth(i).locator("td.col-lemma").inner_text().strip() for i in range(3)] == [
            "Alpha",
            "Baum",
            "Zebra",
        ]
        assert overview_rows.nth(0).locator("td.col-translation").inner_text().strip() == "альфа_memo"
        assert overview_rows.nth(2).locator("td.col-translation").inner_text().strip() == "зебра_memo"
    finally:
        page.context.close()
        assert errors == []


def test_cardless_container_cold_status_update_routes_by_occurrence_not_storage_index(
    page, tmp_path, running_controller
):
    """1.2 / 1.3: Even without sentence cards (dedup=none), cold /session/status must not clobber Alpha with Zebra."""
    url, server, adapter = running_controller
    _configure_owned_world(
        server,
        tmp_path,
        lang="de",
        freq_lines=("Alpha", "Zebra"),
        delivery_mode="container",
        dedup_scope="none",
        sentences_enabled=False,
    )
    source_text = _seed_multi_sentence_session(adapter, include_merged_baum=False)

    # Render while Alpha has blank translation (showing skeleton/blank), then populate SQLite.
    adapter.db.batch_update_words(
        ZID,
        [{"sentence_index": 2, "token_order": 0, "updates": {"word_destination": ""}}],
    )
    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=ZID,
        text_mode="multi",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )

    adapter.db.batch_update_words(
        ZID,
        [
            {"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "зебра_only", "word_provenance": "live:google"}},
            {"sentence_index": 2, "token_order": 0, "updates": {"word_destination": "альфа_only", "word_provenance": "live:deepl"}},
        ],
    )
    server.arbiter.sessions.clear()

    errors = _open_page(page, url, html)
    try:
        rows_loc = page.locator("#lemma-table tbody tr[data-row-id]")
        expect(rows_loc).to_have_count(2)
        assert rows_loc.nth(0).locator("td.col-lemma").inner_text().strip() == "Alpha"
        assert rows_loc.nth(1).locator("td.col-lemma").inner_text().strip() == "Zebra"

        page.locator("#kw-btn-update").click()
        page.wait_for_function(
            "() => window.AppState && window.AppState.isFinished === true"
        )
        assert rows_loc.nth(0).locator("td.col-lemma").inner_text().strip() == "Alpha"
        assert rows_loc.nth(0).locator("td.col-translation").inner_text().strip() == "альфа_only"
        assert rows_loc.nth(1).locator("td.col-lemma").inner_text().strip() == "Zebra"
        assert rows_loc.nth(1).locator("td.col-translation").inner_text().strip() == "зебра_only"
    finally:
        page.context.close()
        assert errors == []


def test_progressive_delivery_keeps_global_order_and_exact_occurrence_provenance(
    page, tmp_path, running_controller
):
    """1.2 / 1.3: Progressive completion snapshot emits globally ordered rows and routes repeated token_order=0."""
    url, server, adapter = running_controller
    _configure_owned_world(server, tmp_path, lang="de", freq_lines=("Alpha", "Zebra"))
    source_text = _seed_multi_sentence_session(adapter, include_merged_baum=False)

    # Clear translations so progressive worker fills them via owned DeterministicTranslationHandler.
    adapter.db.batch_update_words(
        ZID,
        [
            {"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "", "word_provenance": None}},
            {"sentence_index": 2, "token_order": 0, "updates": {"word_destination": "", "word_provenance": None}},
        ],
    )
    with adapter.db.transaction() as conn:
        conn.execute("UPDATE sentences SET sentence_destination = '', text_provenance = NULL WHERE session_zid = ?", (ZID,))

    restored = adapter.restore_session(ZID)
    headers, data_rows = restored["headers"], restored["data_rows"]
    with server.arbiter._lock:
        server.arbiter.sessions[ZID] = {
            "session_zid": ZID,
            "language": "de",
            "target_lang": "ru",
            "text": source_text,
            "tsv_path": ZID,
            "comments": restored["comments"],
            "headers": headers,
            "data_rows": data_rows,
            "sentence_translation": "",
            "fingerprint": desk.compute_content_fingerprint(data_rows),
            "lock": server.arbiter._lock,
            "created_at": time.time(),
        }

    events_q = EventDispatcher.get_instance().register(ZID)
    try:
        server.arbiter.enrichment_queue._execute_progressive_task(
            ZID, server.arbiter, "de", "ru", skip_intellifiller=True
        )
        emitted = []
        while not events_q.empty():
            emitted.append(events_q.get_nowait())
    finally:
        EventDispatcher.get_instance().unregister(ZID, events_q)

    finished = next(e for e in reversed(emitted) if e.get("stage") == "finished")
    assert finished["rows"]["0"]["lemma"] == "Alpha"
    assert finished["rows"]["0"]["sentence_idx"] == "2"
    assert finished["rows"]["1"]["lemma"] == "Zebra"
    assert finished["rows"]["1"]["sentence_idx"] == "1"

    # Render page and verify memoized /session/status & finished event both project Alpha before Zebra.
    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=ZID,
        text_mode="multi",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )
    errors = _open_page(page, url, html)
    try:
        page.evaluate("(evt) => window.receiveUpdate(evt)", finished)
        page.evaluate("window.WorkspaceTabs.switchToTab(1)")
        rows_loc = page.locator("#lemma-table tbody tr")
        assert [rows_loc.nth(i).locator("td.col-lemma").inner_text().strip() for i in range(2)] == [
            "Alpha",
            "Zebra",
        ]
        assert rows_loc.nth(0).locator("td.col-translation").inner_text().strip() == "google: Alpha"
        assert rows_loc.nth(1).locator("td.col-translation").inner_text().strip() == "google: Zebra"
    finally:
        page.context.close()
        assert errors == []


def test_client_hydrate_session_preserves_global_order_and_occurrence_coordinates(
    page, tmp_path, running_controller
):
    """1.2 / 1.3: Client-side hydrateSession() with empty tbody preserves global order and true coordinates."""
    url, server, adapter = running_controller
    _configure_owned_world(
        server,
        tmp_path,
        lang="de",
        freq_lines=("Alpha", "Zebra"),
        dedup_scope="none",
        sentences_enabled=False,
    )
    source_text = _seed_multi_sentence_session(adapter, include_merged_baum=False)
    restored = adapter.restore_session(ZID)
    sorted_rows = desk.sort_session_data_rows(
        restored["data_rows"],
        restored["headers"],
        "de",
        server.config,
        server.resolved_paths,
    )
    html = desk.render_lookup_html(
        text=source_text,
        language="de",
        target_lang="ru",
        config=server.config,
        resolved_paths=server.resolved_paths,
        zid=ZID,
        goldendict={
            "sections": ["source", "translation", "lemmas"],
            "lemma_columns": ["inflected", "lemma", "translation", "ipa", "morphology", "pos", "gender"],
            "server_enabled": True,
            "server_api_key": TOKEN,
            "server_port": server.server_port,
        },
        comments=restored["comments"],
        headers=restored["headers"],
        data_rows=sorted_rows,
        sentence_translation="Зебра.\nАльфа.",
        session_zid=ZID,
        api_token=TOKEN,
        server_enabled=True,
        fingerprint=desk.compute_content_fingerprint(sorted_rows),
    )
    # Force client-side hydration fallback by emptying #lemma-table tbody before page load.
    empty_tbody_html = re.sub(
        r'(<table[^>]*id="lemma-table"[^>]*>.*?<tbody>)(.*?)(</tbody>)',
        r"\1\3",
        html,
        count=1,
        flags=re.DOTALL,
    )

    adapter.db.batch_update_words(
        ZID,
        [
            {"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "зебра_hyd"}},
            {"sentence_index": 2, "token_order": 0, "updates": {"word_destination": "альфа_hyd"}},
        ],
    )
    server.arbiter.sessions.clear()

    errors = _open_page(page, url, empty_tbody_html)
    try:
        rows_loc = page.locator("#lemma-table tbody tr[data-row-id]")
        expect(rows_loc).to_have_count(2)
        assert rows_loc.nth(0).locator("td.col-lemma").inner_text().strip() == "Alpha"
        assert rows_loc.nth(1).locator("td.col-lemma").inner_text().strip() == "Zebra"
        assert rows_loc.nth(0).get_attribute("data-sentence-idx") == "2"
        assert rows_loc.nth(0).get_attribute("data-token-order") == "0"
        assert rows_loc.nth(1).get_attribute("data-sentence-idx") == "1"
        assert rows_loc.nth(1).get_attribute("data-token-order") == "0"

        page.locator("#kw-btn-update").click()
        page.wait_for_function(
            "() => window.AppState && window.AppState.isFinished === true"
        )
        assert rows_loc.nth(0).locator("td.col-translation").inner_text().strip() == "альфа_hyd"
        assert rows_loc.nth(1).locator("td.col-translation").inner_text().strip() == "зебра_hyd"
    finally:
        page.context.close()
        assert errors == []


def test_edit_selection_and_refresh_stability_with_repeated_token_order(
    page, tmp_path, running_controller
):
    """1.3: Editing and selecting repeated token_order=0 occurrences persists to SQLite and survives refresh."""
    url, server, adapter = running_controller
    _configure_owned_world(server, tmp_path, lang="de", freq_lines=("Alpha", "Baum", "Zebra"))
    source_text = _seed_multi_sentence_session(adapter, include_merged_baum=True)

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=ZID,
        text_mode="multi",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )
    errors = _open_page(page, url, html)
    try:
        # Select and edit Zebra in Sentence 1 (token_order=0, sentence_idx=1) and verify Alpha (sentence_idx=2, token_order=0) is untouched.
        page.evaluate("window.WorkspaceTabs.switchToTab(2)")
        zebra_row = page.locator("#lemma-table tbody tr", has_text="Zebra")
        with page.expect_response("**/session/save") as save_sel:
            zebra_row.click()
        assert save_sel.value.status == 200

        trans_cell = zebra_row.locator("td.col-translation")
        trans_cell.dblclick()
        editor = trans_cell.locator("input, textarea").first
        editor.fill("зебра_edited")
        editor.press("Enter")
        with page.expect_response("**/session/save") as save_edit:
            page.keyboard.press("Control+s")
        assert save_edit.value.status == 200

        words_db = {(w["sentence_index"], w["token_order"]): w for w in adapter.db.get_words_by_session(ZID)}
        assert words_db[(1, 0)]["word_destination"] == "зебра_edited"
        assert words_db[(1, 0)]["selected"] == 1
        assert words_db[(2, 0)]["word_destination"] == "альфа"
        assert words_db[(2, 0)]["selected"] == 0
    finally:
        page.context.close()
        assert errors == []

    # Re-render after refresh and confirm order + edited values + selections stay intact.
    html_after = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=ZID,
        text_mode="multi",
        config=server.config,
        resolved_paths=server.resolved_paths,
        tsv_path=ZID,
        spawn_children=False,
    )
    cards_after = _extract_island(html_after, "sentence-cards")
    assert [w["lemma"] for w in cards_after[0]["words"]] == ["Alpha", "Baum", "Zebra"]
    assert cards_after[0]["words"][2]["translation"] == "зебра_edited"
    assert cards_after[0]["words"][2]["selected"] == "1"
    assert cards_after[0]["words"][0]["selected"] == "0"
