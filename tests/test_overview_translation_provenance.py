"""Acceptance and control tests for Overview translation provenance preservation.

Exercises real desk.run_render_flow initial server-rendered HTML (before client
JavaScript runs), real owned SQLite and TSV storage, and Playwright client DOM
projection across hydration, tab switching, and live SSE/status updates.
"""
import json
import re
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import expect

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    owned_configuration,
    seed_cached_lookup_inputs,
    token_service,
    translation_service,
)


def _seed_sqlite_multiline_session(
    db_path: Path,
    *,
    zid: str,
    source_text: str,
    sentences: list[dict],
    words: list[dict],
    language: str = "de",
    target_language: str = "ru",
    text_mode: str = "multi",
) -> KardenwortDB:
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()
    db.insert_session(
        {
            "zid": zid,
            "slug": desk.generate_slug(source_text),
            "source_language": language,
            "target_language": target_language,
            "text_mode": text_mode,
            "source_raw_text": source_text,
        }
    )
    db.insert_sentences(
        [
            {
                "session_zid": zid,
                "sentence_index": item["sentence_index"],
                "sentence_source": item["sentence_source"],
                "sentence_destination": item["sentence_destination"],
                "text_provenance": item.get("text_provenance"),
            }
            for item in sentences
        ]
    )
    db.insert_words(
        [
            {
                "session_zid": zid,
                "sentence_index": item["sentence_index"],
                "token_order": item["token_order"],
                "quotation": item["quotation"],
                "inflected_form": item.get("inflected_form", item["quotation"]),
                "lemma": item["lemma"],
                "word_destination": item.get("word_destination", ""),
                "word_provenance": item.get("word_provenance"),
            }
            for item in words
        ]
    )
    return db


def _configure_render_profile(
    config,
    *,
    sentences_enabled: bool = True,
    display_mode: str = "monolithic",
    run_text: str = "manual",
    server_enabled: bool = False,
):
    config.read_dict(
        {
            "rendering": {"display_mode": display_mode},
            "sentences_mode": {
                "enabled": "true" if sentences_enabled else "false",
                "delivery_mode": "container",
                "spawn_order": "normal",
            },
            "wordfill": {"enabled": "false"},
            "server": {"enabled": "true" if server_enabled else "false"},
            "triggers": {
                "run_text_translation": run_text,
                "run_lemma_base_translation": "manual",
                "run_lemma_enrichment": "manual",
            },
        }
    )


def _render_static_blocks(browser, html: str):
    """Inspect initial server-rendered DOM before any client JavaScript executes."""
    context = browser.new_context(java_script_enabled=False)
    try:
        static_page = context.new_page()
        static_page.set_content(html)
        blocks = static_page.locator("#translation-container > div").evaluate_all(
            """(els) => els.map((el) => ({
                text: (el.textContent || '').trim(),
                provenance: el.getAttribute('data-provenance'),
                title: el.getAttribute('title'),
                pending: el.getAttribute('data-pending'),
                sentence: el.getAttribute('data-translation-sentence'),
                classes: el.className,
            }))"""
        )
        return blocks
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Failing acceptance scenarios (delta: Preserve provider labels on every Overview translation block)
# ---------------------------------------------------------------------------


def test_overview_two_translated_sentences_preserve_provenance_on_every_block(tmp_path, browser):
    """Scenario: Two translated sentences.

    Initial server-rendered Overview HTML must attach stored provider provenance
    and tooltip to every translated block, preserving sentence order and text.
    """
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="sqlite")
    _configure_render_profile(config, sentences_enabled=True, display_mode="monolithic", run_text="manual")

    zid = "20261121173001"
    source_text = "Alpha eins.\nBeta zwei."
    _seed_sqlite_multiline_session(
        paths["sqlite_db_path"],
        zid=zid,
        source_text=source_text,
        text_mode="multi",
        sentences=[
            {
                "sentence_index": 1,
                "sentence_source": "Alpha eins.",
                "sentence_destination": "Альфа первая.",
                "text_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "sentence_source": "Beta zwei.",
                "sentence_destination": "Бета вторая.",
                "text_provenance": "live:argos",
            },
        ],
        words=[
            {
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Alpha",
                "lemma": "Alpha",
                "word_destination": "альфа",
                "word_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "token_order": 0,
                "quotation": "Beta",
                "lemma": "Beta",
                "word_destination": "бета",
                "word_provenance": "live:argos",
            },
        ],
    )

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        spawn_children=False,
        wordfill_cfg={"enabled": False},
        seq_num=1,
    )

    blocks = _render_static_blocks(browser, html)
    assert [b["text"] for b in blocks] == ["Альфа первая.", "Бета вторая."]
    assert blocks[0]["provenance"] == "live:argos"
    assert blocks[0]["title"] == "Translated via Argos (offline)"
    assert blocks[1]["provenance"] == "live:argos"
    assert blocks[1]["title"] == "Translated via Argos (offline)"


def test_overview_mixed_providers_preserve_distinct_provenance_across_initial_render_tabs_and_updates(
    tmp_path, browser, page
):
    """Scenario: Mixed providers.

    When stored sentences have different providers, each Overview block keeps its
    own stored provider and tooltip in initial server HTML, after client hydration,
    across tab switching, and after SSE/update delivery.
    """
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="sqlite")
    _configure_render_profile(config, sentences_enabled=True, display_mode="monolithic", run_text="manual")

    zid = "20261121173002"
    source_text = "Alpha eins.\nBeta zwei."
    _seed_sqlite_multiline_session(
        paths["sqlite_db_path"],
        zid=zid,
        source_text=source_text,
        text_mode="multi",
        sentences=[
            {
                "sentence_index": 1,
                "sentence_source": "Alpha eins.",
                "sentence_destination": "Альфа первая.",
                "text_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "sentence_source": "Beta zwei.",
                "sentence_destination": "Бета вторая.",
                "text_provenance": "live:google",
            },
        ],
        words=[
            {
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Alpha",
                "lemma": "Alpha",
                "word_destination": "альфа",
                "word_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "token_order": 0,
                "quotation": "Beta",
                "lemma": "Beta",
                "word_destination": "бета",
                "word_provenance": "live:google",
            },
        ],
    )

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        spawn_children=False,
        wordfill_cfg={"enabled": False},
        seq_num=1,
    )

    # 1. Server-rendered Overview HTML before client JS executes
    static_blocks = _render_static_blocks(browser, html)
    assert [b["text"] for b in static_blocks] == ["Альфа первая.", "Бета вторая."]
    assert static_blocks[0]["provenance"] == "live:argos"
    assert static_blocks[0]["title"] == "Translated via Argos (offline)"
    assert static_blocks[1]["provenance"] == "live:google"
    assert static_blocks[1]["title"] == "Translated via Google"

    # 2. Client hydration must not wipe mixed-provider Overview blocks
    page.set_content(html)
    expect(page.locator("#translation-container > div")).to_have_count(2)
    div0 = page.locator("#translation-container > div").nth(0)
    div1 = page.locator("#translation-container > div").nth(1)
    assert div0.get_attribute("data-provenance") == "live:argos"
    assert div0.get_attribute("title") == "Translated via Argos (offline)"
    assert div1.get_attribute("data-provenance") == "live:google"
    assert div1.get_attribute("title") == "Translated via Google"

    # 3. Switching to child cards and back to Overview preserves per-block provenance
    page.evaluate("() => window.WorkspaceTabs.switchToTab(2, true)")
    expect(page.locator("#translation-container")).to_contain_text("Альфа первая.")
    assert page.locator("#translation-container").get_attribute("data-provenance") == "live:argos"

    page.evaluate("() => window.WorkspaceTabs.switchToTab(3, true)")
    expect(page.locator("#translation-container")).to_contain_text("Бета вторая.")
    assert page.locator("#translation-container").get_attribute("data-provenance") == "live:google"

    page.evaluate("() => window.WorkspaceTabs.switchToTab(1, true)")
    expect(page.locator("#translation-container > div")).to_have_count(2)
    assert div0.get_attribute("data-provenance") == "live:argos"
    assert div0.get_attribute("title") == "Translated via Argos (offline)"
    assert div1.get_attribute("data-provenance") == "live:google"
    assert div1.get_attribute("title") == "Translated via Google"

    # Tokenized word spans inside each Overview block inherit that block's own provenance
    for span in div0.locator("span.word").all():
        assert span.get_attribute("data-provenance") == "live:argos"
        assert span.get_attribute("title") == "Translated via Argos (offline)"
    for span in div1.locator("span.word").all():
        assert span.get_attribute("data-provenance") == "live:google"
        assert span.get_attribute("title") == "Translated via Google"

    # 4. Live SSE/update delivery must not collapse mixed-provider Overview blocks
    page.evaluate(
        """() => window.receiveUpdate({
            stage: 'finished',
            status: 'success',
            text_provenance: 'live:argos',
            sentences: [
                {
                    sentence_index: 1,
                    sentence_source: 'Alpha eins.',
                    sentence_destination: 'Альфа первая.',
                    text_provenance: 'live:argos',
                },
                {
                    sentence_index: 2,
                    sentence_source: 'Beta zwei.',
                    sentence_destination: 'Бета вторая.',
                    text_provenance: 'live:google',
                },
            ],
            rows: {},
        })"""
    )
    expect(page.locator("#translation-container > div")).to_have_count(2)
    assert div0.get_attribute("data-provenance") == "live:argos"
    assert div0.get_attribute("title") == "Translated via Argos (offline)"
    assert div1.get_attribute("data-provenance") == "live:google"
    assert div1.get_attribute("title") == "Translated via Google"


def test_overview_escapes_provenance_metadata_in_initial_html(tmp_path, browser):
    """Requirement invariant: emitted provenance metadata and tooltips are safely HTML-escaped."""
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="sqlite")
    _configure_render_profile(config, sentences_enabled=True, display_mode="monolithic", run_text="manual")
    zid = "20261121173003"
    source_text = "Alpha eins.\nBeta zwei."
    unsafe_prov = 'live:custom" data-injected="1'
    _seed_sqlite_multiline_session(
        paths["sqlite_db_path"],
        zid=zid,
        source_text=source_text,
        text_mode="multi",
        sentences=[
            {
                "sentence_index": 1,
                "sentence_source": "Alpha eins.",
                "sentence_destination": "Альфа первая.",
                "text_provenance": unsafe_prov,
            },
            {
                "sentence_index": 2,
                "sentence_source": "Beta zwei.",
                "sentence_destination": "Бета вторая.",
                "text_provenance": "live:google",
            },
        ],
        words=[
            {
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Alpha",
                "lemma": "Alpha",
                "word_destination": "альфа",
            },
            {
                "sentence_index": 2,
                "token_order": 0,
                "quotation": "Beta",
                "lemma": "Beta",
                "word_destination": "бета",
            },
        ],
    )
    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        spawn_children=False,
        wordfill_cfg={"enabled": False},
        seq_num=1,
    )
    assert 'data-injected="1"' not in html
    blocks = _render_static_blocks(browser, html)
    assert len(blocks) == 2
    assert blocks[0]["provenance"] == unsafe_prov
    assert blocks[1]["provenance"] == "live:google"


def test_overview_withholds_provenance_from_unfinished_failed_skeleton(tmp_path, browser):
    """Requirement invariant: unfinished [FAILED] skeletons never receive provenance or duplicate title."""
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="tsv")
    _configure_render_profile(
        config, sentences_enabled=False, display_mode="progressive", run_text="auto"
    )
    zid = "20261121173004"
    source_text = "Alpha eins."
    slug = desk.generate_slug(source_text)
    tsv_path = paths["results_dir"] / f"{zid}-{slug}.de.tsv"
    tsv_path.write_text(
        "TokenOrder\tQuotation\tWordSource\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tWordDestination\n"
        f"0\tAlpha\tAlpha\t1\t{source_text}\t[FAILED]\tальфа\n",
        encoding="utf-8",
    )
    txt_path = paths["results_dir"] / f"{zid}-{slug}.ru.txt"
    txt_path.write_text("[FAILED]\n", encoding="utf-8")

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        tsv_path=str(tsv_path),
        spawn_children=False,
        wordfill_cfg={"enabled": False},
    )

    container_match = re.search(
        r'<div[^>]+id="translation-container"[^>]*>(.*?)</div>\s*<span',
        html,
        re.DOTALL,
    )
    assert container_match is not None
    inner_html = container_match.group(1)
    assert 'data-pending="true"' in inner_html
    assert "data-provenance=" not in inner_html
    assert len(re.findall(r'\btitle="', inner_html)) == 1
    fail_blocks = _render_static_blocks(browser, html)
    assert len(fail_blocks) == 1
    assert fail_blocks[0]["pending"] == "true"
    assert fail_blocks[0]["provenance"] is None


# ---------------------------------------------------------------------------
# Valid adjacent controls (must pass both before and after the repair)
# ---------------------------------------------------------------------------


def test_control_single_line_flattened_translation_preserves_text_and_provenance(tmp_path, browser):
    """Scenario: Single-line control.

    When the formatter intentionally flattens a single-mode translation,
    the text and existing provider metadata remain unchanged.
    """
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="sqlite")
    _configure_render_profile(config, sentences_enabled=False, display_mode="monolithic", run_text="manual")

    zid = "20261121173005"
    source_text = "Alpha eins. Beta zwei."
    _seed_sqlite_multiline_session(
        paths["sqlite_db_path"],
        zid=zid,
        source_text=source_text,
        text_mode="single",
        sentences=[
            {
                "sentence_index": 1,
                "sentence_source": "Alpha eins.",
                "sentence_destination": "Альфа первая.",
                "text_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "sentence_source": "Beta zwei.",
                "sentence_destination": "Бета вторая.",
                "text_provenance": "live:argos",
            },
        ],
        words=[
            {
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Alpha",
                "lemma": "Alpha",
                "word_destination": "альфа",
                "word_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "token_order": 0,
                "quotation": "Beta",
                "lemma": "Beta",
                "word_destination": "бета",
                "word_provenance": "live:argos",
            },
        ],
    )

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        spawn_children=False,
        wordfill_cfg={"enabled": False},
    )

    blocks = _render_static_blocks(browser, html)
    assert len(blocks) == 1
    assert blocks[0]["text"] == "Альфа первая. Бета вторая."
    assert blocks[0]["provenance"] == "live:argos"
    assert blocks[0]["title"] == "Translated via Argos (offline)"


def test_control_child_card_initial_formatting_remains_valid(tmp_path, browser):
    """Valid adjacent control: child-card initial formatting keeps sentence scope and provenance."""
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="sqlite")
    _configure_render_profile(config, sentences_enabled=True, display_mode="monolithic", run_text="manual")

    zid = "20261121173006"
    source_text = "Alpha eins.\nBeta zwei."
    _seed_sqlite_multiline_session(
        paths["sqlite_db_path"],
        zid=zid,
        source_text=source_text,
        text_mode="multi",
        sentences=[
            {
                "sentence_index": 1,
                "sentence_source": "Alpha eins.",
                "sentence_destination": "Альфа первая.",
                "text_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "sentence_source": "Beta zwei.",
                "sentence_destination": "Бета вторая.",
                "text_provenance": "live:argos",
            },
        ],
        words=[
            {
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Alpha",
                "lemma": "Alpha",
                "word_destination": "альфа",
                "word_provenance": "live:argos",
            },
            {
                "sentence_index": 2,
                "token_order": 0,
                "quotation": "Beta",
                "lemma": "Beta",
                "word_destination": "бета",
                "word_provenance": "live:argos",
            },
        ],
    )

    html = desk.run_render_flow(
        text=source_text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        spawn_children=False,
        wordfill_cfg={"enabled": False},
        seq_num=2,
    )

    blocks = _render_static_blocks(browser, html)
    assert [(b["sentence"], b["text"]) for b in blocks] == [
        ("1", "Альфа первая."),
        ("2", "Бета вторая."),
    ]
    assert blocks[0]["provenance"] == "live:argos"
    assert blocks[0]["title"] == "Translated via Argos (offline)"
    assert blocks[1]["provenance"] == "live:argos"
    assert blocks[1]["title"] == "Translated via Argos (offline)"


def test_async_controller_status_preserves_mixed_provenance_and_unrelated_sessions(tmp_path, page):
    """Deterministic async coordination: controller worker + /session/status preserve per-sentence provenance and leave unrelated sessions untouched."""
    arrived = threading.Event()
    release = threading.Event()

    class GatedTranslationHandler(DeterministicTranslationHandler):
        def translate(self, provider, text, source, target):
            arrived.set()
            assert release.wait(timeout=10), "Timed out waiting to release gated external translation"
            return super().translate(provider, text, source, target)

    with translation_service(GatedTranslationHandler) as translator, token_service() as tokenizer:
        with controller_lifetime(tmp_path, translator, backend="sqlite") as (controller_url, server):
            config, paths = server.config, server.resolved_paths
            config.set("services", "spacy_server_url", f"http://127.0.0.1:{tokenizer.server_port}")
            _configure_render_profile(
                config,
                sentences_enabled=True,
                display_mode="progressive",
                run_text="auto",
                server_enabled=True,
            )
            seed_cached_lookup_inputs(server)

            adapter = desk.SqliteStorageAdapter(config, paths)
            unrelated_apple_sents = adapter.db.get_sentences_by_session("20261108113001")
            unrelated_dog_sents = adapter.db.get_sentences_by_session("20261108113002")

            # Seed an owned two-sentence session where sentence 1 has Argos provenance and sentence 2 is translated asynchronously via Google
            zid = "20261121173007"
            source_text = "Alpha eins.\nBeta zwei."
            _seed_sqlite_multiline_session(
                paths["sqlite_db_path"],
                zid=zid,
                source_text=source_text,
                text_mode="multi",
                sentences=[
                    {
                        "sentence_index": 1,
                        "sentence_source": "Alpha eins.",
                        "sentence_destination": "",
                        "text_provenance": None,
                    },
                    {
                        "sentence_index": 2,
                        "sentence_source": "Beta zwei.",
                        "sentence_destination": "",
                        "text_provenance": None,
                    },
                ],
                words=[
                    {
                        "sentence_index": 1,
                        "token_order": 0,
                        "quotation": "Alpha",
                        "lemma": "Alpha",
                        "word_destination": "альфа",
                        "word_provenance": "live:argos",
                    },
                    {
                        "sentence_index": 2,
                        "token_order": 0,
                        "quotation": "Beta",
                        "lemma": "Beta",
                        "word_destination": "бета",
                        "word_provenance": "live:google",
                    },
                ],
            )

            sub_queue = server.arbiter.register_subscriber(zid)
            try:
                html = desk.run_render_flow(
                    text=source_text,
                    language="de",
                    zid=zid,
                    text_mode="multi",
                    config=config,
                    resolved_paths=paths,
                    spawn_children=False,
                    wordfill_cfg={"enabled": False},
                    seq_num=1,
                )
                page.set_content(html)
                expect(
                    page.locator('#translation-container .skeleton-loader[data-pending="true"]')
                ).to_have_count(1)
                assert (
                    page.locator('#translation-container .skeleton-loader').get_attribute("data-provenance")
                    is None
                )
                assert arrived.wait(timeout=10), "External translation engine was never invoked"

                release.set()
                while True:
                    evt = sub_queue.get(timeout=15)
                    evt_data = evt.get("data") if isinstance(evt, dict) and isinstance(evt.get("data"), dict) else evt
                    if isinstance(evt_data, dict) and (
                        evt_data.get("stage") == "finished" or evt_data.get("is_finished") is True
                    ):
                        break

                # Simulate mixed stored providers (sentence 1 Argos, sentence 2 Google) and fetch real /session/status
                adapter.db.insert_sentence(
                    {
                        "session_zid": zid,
                        "sentence_index": 1,
                        "sentence_source": "Alpha eins.",
                        "sentence_destination": "argos: Alpha eins.",
                        "text_provenance": "live:argos",
                    }
                )
                persisted = adapter.db.get_sentences_by_session(zid)
                assert [s["text_provenance"] for s in persisted] == ["live:argos", "live:google"]

                req = Request(
                    f"{controller_url}/session/status?zid={zid}",
                    headers={"X-API-Token": server.api_key},
                )
                with urlopen(req, timeout=5) as resp:
                    status_body = json.loads(resp.read().decode("utf-8"))
                assert status_body["status"] == "success"
                status_data = status_body["data"]
                assert [s.get("text_provenance") for s in status_data["sentences"]] == [
                    "live:argos",
                    "live:google",
                ]

                page.evaluate("(update) => window.receiveUpdate(update)", status_data)
                expect(page.locator("#translation-container > div")).to_have_count(2)
                div0 = page.locator("#translation-container > div").nth(0)
                div1 = page.locator("#translation-container > div").nth(1)
                assert div0.get_attribute("data-provenance") == "live:argos"
                assert div0.get_attribute("title") == "Translated via Argos (offline)"
                assert div1.get_attribute("data-provenance") == "live:google"
                assert div1.get_attribute("title") == "Translated via Google"

                # Unrelated sessions and their stored provenance remain completely untouched
                assert adapter.db.get_sentences_by_session("20261108113001") == unrelated_apple_sents
                assert adapter.db.get_sentences_by_session("20261108113002") == unrelated_dog_sents
            finally:
                release.set()
                server.arbiter.unregister_subscriber(zid, sub_queue)
