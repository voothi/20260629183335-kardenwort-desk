"""Acceptance and control tests for blank/whitespace pending sentence display.

Exercises the pure kardenwort_alignment.display_sentence_text projection,
real desk.run_render_flow HTML/bootstrap assembly with owned TSV and SQLite
storage, real controller progressive worker execution coordinated via
deterministic threading.Event synchronization on the unmanaged translation
engine fixture, and Playwright DOM projection.
"""
import json
import re
import threading
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import expect

import kardenwort_alignment as alignment
import kardenwort_desk as desk
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    owned_configuration,
    seed_cached_lookup_inputs,
    token_service,
    translation_service,
)


def _extract_bootstrap_payload(html: str) -> dict:
    match = re.search(
        r'<script[^>]+id="kardenwort-bootstrap"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None, "kardenwort-bootstrap script tag missing from rendered HTML"
    return json.loads(match.group(1))


def _seed_tsv_session_files(results_dir, zid: str, slug: str, source_text: str, txt_content: str):
    tsv_path = results_dir / f"{zid}-{slug}.de.tsv"
    tsv_path.write_text(
        "TokenOrder\tQuotation\tWordSource\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tWordDestination\n"
        f"0\tHallo\thallo\t1\t{source_text}\t\tпривет\n"
        f"1\tWelt\twelt\t1\t{source_text}\t\tмир\n",
        encoding="utf-8",
    )
    txt_path = results_dir / f"{zid}-{slug}.ru.txt"
    txt_path.write_text(txt_content, encoding="utf-8")
    return tsv_path, txt_path


# ---------------------------------------------------------------------------
# Pure projection acceptance & controls (kardenwort_alignment)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entries",
    [
        {0: "   "},
        {0: "\t \n", 1: "", 2: None},
        {0: "   ", "FULL_TEXT": "must not leak", "PADDED": {0: "padded context"}},
    ],
)
def test_whitespace_pending_value_contributes_no_sentence_text(entries):
    """Scenario: Whitespace pending value."""
    assert alignment.display_sentence_text(entries) == ""


def test_mixed_ready_and_whitespace_pending_preserves_order_and_filters_metadata():
    """Scenario: Mixed ready and pending (ordering, failure markers, FULL_TEXT/PADDED excluded)."""
    entries = {
        2: " second ",
        0: "   ",
        1: "first",
        3: None,
        4: "",
        5: "[FAILED]",
        6: "[Error: offline]",
        "FULL_TEXT": "first second",
        "PADDED": {1: "ctx first ctx", 2: "ctx second ctx"},
    }
    assert alignment.display_sentence_text(entries) == "first second"
    assert alignment.display_sentence_text({10: "ten", 2: "two", "0": "   "}) == "two ten"


@pytest.mark.parametrize(
    "invalid_entries",
    [
        None,
        ["ready"],
        "ready",
        42,
        {0: {"text": "fabricated"}},
        {0: ["nested"]},
        {0: 123},
        {0: False},
        {0: "   ", "0": "ready"},
        {0: "", "0": "ready"},
        {0: None, "0": "ready"},
        {0: "first", "0": "duplicate"},
        {"unknown": "   "},
        {"unknown": ""},
        {"unknown": None},
        {True: "   "},
        {-1: "   "},
    ],
)
def test_invalid_structure_keys_and_duplicates_still_fail_explicitly(invalid_entries):
    """Scenario: Invalid structure (validated BEFORE skipping None/empty/whitespace)."""
    with pytest.raises(ValueError):
        alignment.display_sentence_text(invalid_entries)


def test_control_empty_string_pending_and_strict_provider_validation_remain_intact():
    """Valid adjacent control: empty-string pending works and provider/authoritative validators stay strict."""
    assert (
        alignment.display_sentence_text(
            {0: "", 1: "ready", "FULL_TEXT": "ready", "PADDED": {1: "ctx"}}
        )
        == "ready"
    )
    with pytest.raises(ValueError):
        alignment.numeric_sentence_entries({0: "   "})
    with pytest.raises(ValueError):
        alignment.normalize_sentence_result({0: "   "})
    with pytest.raises(ValueError):
        alignment.validate_authoritative_records(
            [{"sentence_index": 1, "sentence_source": "   "}]
        )


# ---------------------------------------------------------------------------
# Owned render-flow acceptance & controls (TSV + SQLite + Playwright)
# ---------------------------------------------------------------------------

def test_render_flow_whitespace_pending_text_does_not_crash_page(tmp_path, page):
    """Scenario: Whitespace pending value in real run_render_flow assembly."""
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="tsv")
    config.read_dict(
        {
            "rendering": {"display_mode": "monolithic"},
            "sentences_mode": {"enabled": "false"},
            "wordfill": {"enabled": "false"},
            "triggers": {
                "run_text_translation": "manual",
                "run_lemma_base_translation": "manual",
                "run_lemma_enrichment": "manual",
            },
        }
    )

    zid = "20261121061001"
    source_text = "Hallo Welt."
    slug = desk.generate_slug(source_text)
    tsv_path, _ = _seed_tsv_session_files(
        paths["results_dir"], zid, slug, source_text, txt_content="   \n   "
    )

    html = desk.run_render_flow(
        source_text,
        "de",
        zid,
        "single",
        config,
        paths,
        tsv_path=str(tsv_path),
        spawn_children=False,
        wordfill_cfg={"enabled": False},
    )

    payload = _extract_bootstrap_payload(html)
    assert payload["session_data"]["sentence_translation"] == ""
    assert payload["sentence_translation"] == ""

    page.set_content(html)
    expect(page.locator("#source-container")).to_contain_text("Hallo")
    expect(page.locator("#translation-container")).to_have_text("")


def test_control_render_flow_empty_pending_text_succeeds(tmp_path, page):
    """Valid adjacent control: empty pending translation file renders cleanly."""
    config, paths, _ = owned_configuration(tmp_path, translation_url="", backend="tsv")
    config.read_dict(
        {
            "rendering": {"display_mode": "monolithic"},
            "sentences_mode": {"enabled": "false"},
            "wordfill": {"enabled": "false"},
            "triggers": {
                "run_text_translation": "manual",
                "run_lemma_base_translation": "manual",
                "run_lemma_enrichment": "manual",
            },
        }
    )

    zid = "20261121061002"
    source_text = "Hallo Welt."
    slug = desk.generate_slug(source_text)
    tsv_path, _ = _seed_tsv_session_files(
        paths["results_dir"], zid, slug, source_text, txt_content=""
    )

    html = desk.run_render_flow(
        source_text,
        "de",
        zid,
        "single",
        config,
        paths,
        tsv_path=str(tsv_path),
        spawn_children=False,
        wordfill_cfg={"enabled": False},
    )

    payload = _extract_bootstrap_payload(html)
    assert payload["session_data"]["sentence_translation"] == ""
    assert payload["sentence_translation"] == ""

    page.set_content(html)
    expect(page.locator("#source-container")).to_contain_text("Hallo")
    expect(page.locator("#translation-container")).to_have_text("")


def test_progressive_pending_skeleton_and_later_committed_translation_delivery(tmp_path, page):
    """Decision 3: Stable pending skeleton during in-flight translation and clean replacement on commit."""
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
            config.read_dict(
                {
                    "rendering": {"display_mode": "progressive"},
                    "sentences_mode": {"enabled": "false"},
                    "wordfill": {"enabled": "false"},
                    "triggers": {
                        "run_text_translation": "auto",
                        "run_lemma_base_translation": "manual",
                        "run_lemma_enrichment": "manual",
                    },
                }
            )
            seed_cached_lookup_inputs(server)

            adapter = desk.SqliteStorageAdapter(config, paths)
            unrelated_apple = adapter.db.get_words_by_session("20261108113001")
            unrelated_dog = adapter.db.get_words_by_session("20261108113002")

            zid = "20261121061003"
            source_text = "Hallo Welt."
            adapter.save_session(
                session_zid=zid,
                slug=desk.generate_slug(source_text),
                source_language="de",
                target_language="ru",
                text_mode="single",
                source_raw_text=source_text,
                headers=[
                    "TokenOrder",
                    "Quotation",
                    "WordSource",
                    "SentenceSourceIndex",
                    "SentenceSource",
                    "SentenceDestination",
                    "WordDestination",
                ],
                data_rows=[
                    ["0", "Hallo", "hallo", "1", source_text, "   ", "привет"],
                    ["1", "Welt", "welt", "1", source_text, "   ", "мир"],
                ],
                text_provenance=None,
            )

            sub_queue = server.arbiter.register_subscriber(zid)

            try:
                html = desk.run_render_flow(
                    source_text,
                    "de",
                    zid,
                    "single",
                    config,
                    paths,
                    spawn_children=False,
                    wordfill_cfg={"enabled": False},
                )

                payload = _extract_bootstrap_payload(html)
                assert payload["session_data"]["sentence_translation"] == ""
                assert payload["worker_launched"] is True

                page.set_content(html)
                expect(
                    page.locator('#translation-container .skeleton-loader[data-pending="true"]')
                ).to_have_count(1)
                assert arrived.wait(timeout=10), "External translation engine was never invoked"

                # Release external translation and wait deterministically on the real controller subscriber queue.
                release.set()
                finished_event = None
                while True:
                    evt = sub_queue.get(timeout=15)
                    evt_data = evt.get("data") if isinstance(evt, dict) and isinstance(evt.get("data"), dict) else evt
                    if isinstance(evt_data, dict) and (
                        evt_data.get("stage") == "finished" or evt_data.get("is_finished") is True
                    ):
                        finished_event = evt_data
                        break
                assert finished_event is not None

                sentences = adapter.db.get_sentences_by_session(zid)
                assert [s["sentence_index"] for s in sentences] == [1]
                assert sentences[0]["sentence_destination"] == "google: Hallo Welt."
                assert sentences[0]["text_provenance"] == "live:google"

                req = Request(
                    f"{controller_url}/session/status?zid={zid}",
                    headers={"X-API-Token": server.api_key},
                )
                with urlopen(req, timeout=5) as resp:
                    status_body = json.loads(resp.read().decode("utf-8"))
                assert status_body["status"] == "success"
                status_data = status_body["data"]
                assert status_data["is_finished"] is True
                assert "google: Hallo Welt." in status_data["translatedText"]

                page.evaluate("(update) => window.receiveUpdate(update)", status_data)
                expect(
                    page.locator('#translation-container .skeleton-loader')
                ).to_have_count(0)
                expect(
                    page.locator('#translation-container [data-pending="true"]')
                ).to_have_count(0)
                expect(page.locator("#translation-container")).to_have_text("google: Hallo Welt.")

                # Re-render reflects committed translation in bootstrap payload without duplication.
                html_after = desk.run_render_flow(
                    source_text,
                    "de",
                    zid,
                    "single",
                    config,
                    paths,
                    spawn_children=False,
                    wordfill_cfg={"enabled": False},
                )
                payload_after = _extract_bootstrap_payload(html_after)
                assert payload_after["session_data"]["sentence_translation"] == "google: Hallo Welt."

                # Unrelated sessions remain untouched.
                assert adapter.db.get_words_by_session("20261108113001") == unrelated_apple
                assert adapter.db.get_words_by_session("20261108113002") == unrelated_dog
            finally:
                release.set()
                server.arbiter.unregister_subscriber(zid, sub_queue)
