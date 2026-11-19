"""Table-row RMB audio playback acceptance contracts."""
import json
import pytest

import kardenwort_desk
from tests.test_audio_browser import DEVICE, resolve_audio


def _render_haus_document(tmp_path, *, lmb_source, rmb_source="word_translation", rmb_play=True):
    config, paths, _, _ = kardenwort_desk.load_config()
    config.set("storage", "backend", "tsv")
    config.set("server", "api_key", "test-audio-api-key")
    config.set("settings", "default_target_language", "en")
    config.set("sentences_mode", "enabled", "false")
    config.set("rendering", "display_mode", "monolithic")
    config.set("audio", "lmb_play", "true")
    config.set("audio", "lmb_source", lmb_source)
    config.set("audio", "rmb_play", "true" if rmb_play else "false")
    config.set("audio", "rmb_source", rmb_source)

    paths["storage_backend"] = "tsv"
    paths["base_dir"] = tmp_path
    paths["results_dir"] = tmp_path / "results"
    paths["generated_results_dir"] = tmp_path / "results"
    paths["sqlite_db_path"] = tmp_path / "desk.db"

    tsv = tmp_path / "20261118183557-haus.de.tsv"
    tsv.write_text(
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tHäuser\thouse\t1\tDas Haus.\tThe house.\t0\n",
        encoding="utf-8",
    )
    return kardenwort_desk.run_render_flow(
        text="Das Haus.",
        language="de",
        zid="20261118183557",
        text_mode="single",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=str(tsv),
        wordfill_cfg={"enabled": False},
        spawn_children=False,
    )


@pytest.mark.parametrize("lmb_source", ["lemma", "inflection"])
def test_generated_document_table_rmb_translation_independent_of_lmb_source(page, tmp_path, lmb_source):
    html = _render_haus_document(tmp_path, lmb_source=lmb_source, rmb_source="word_translation", rmb_play=True)
    device_head = f"<script>({DEVICE})();\nwindow.EventSource = class {{ close() {{}} }};</script>"
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.set_content(html.replace("<head>", "<head>" + device_head, 1))
    page.wait_for_selector('#lemma-table tbody tr[data-row-id="0"]')

    row = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    row.click(button="right")

    page.wait_for_function("window.requests && window.requests.length >= 1")
    requests = page.evaluate(
        "window.requests.map(r => ({url: String(r.url), method: r.options && r.options.method, body: JSON.parse(r.options.body)}))"
    )
    assert errors == []
    assert len(requests) == 1
    assert requests[0]["url"].endswith("/api/v1/audio/synthesize")
    assert requests[0]["method"] == "POST"
    assert requests[0]["body"] == {"text": "house", "language": "en"}

    resolve_audio(page, 0)
    page.wait_for_function("window.players.length === 1")
    page.evaluate("window.players[0].dispatchEvent(new Event('play')); window.players[0].dispatchEvent(new Event('ended'));")
    assert page.evaluate("window.audioPlayback.getState()") == "finished"
