"""Table-row RMB audio playback acceptance contracts."""
import configparser
import hashlib
import json
from pathlib import Path
import urllib.request

import pytest

import kardenwort_desk
from tests.audio_test_support import audio_server  # noqa: F401
from tests.test_audio_browser import DEVICE, resolve_audio

ROOT = Path(__file__).resolve().parents[1]


def _isolated_tsv_config(tmp_path, *, lmb_source="lemma", rmb_source="word_translation", rmb_play=True, mapping_path=None):
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
    if mapping_path is not None:
        config.set("settings", "anki_mapping_file", str(mapping_path))
        paths["anki_mapping_file"] = mapping_path
    return config, paths


def _render_haus_document(
    tmp_path,
    *,
    lmb_source="lemma",
    rmb_source="word_translation",
    rmb_play=True,
    translation="house",
    provenance=None,
    headers=None,
    row_values=None,
    mapping_path=None,
):
    config, paths = _isolated_tsv_config(
        tmp_path,
        lmb_source=lmb_source,
        rmb_source=rmb_source,
        rmb_play=rmb_play,
        mapping_path=mapping_path,
    )
    cols = headers or [
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "SentenceDestination",
        "DeskSelected",
    ]
    row = row_values or ["Haus", "Haus", "Häuser", translation, "1", "Das Haus.", "The house.", "0"]
    tsv = tmp_path / "20261118183557-haus.de.tsv"
    tsv.write_text("\t".join(cols) + "\n" + "\t".join(row) + "\n", encoding="utf-8")
    html = kardenwort_desk.run_render_flow(
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
    if provenance:
        html = html.replace(
            f'<div class="scrollable-cell">{translation}</div>',
            f'<div class="scrollable-cell" data-provenance="{provenance}">{translation}</div>',
        )
    return html


def _mount_with_device(page, html):
    device_head = f"<script>({DEVICE})();\nwindow.EventSource = class {{ close() {{}} }};</script>"
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.set_content(html.replace("<head>", "<head>" + device_head, 1))
    page.wait_for_selector('#lemma-table tbody tr[data-row-id="0"]')
    return errors


def _selection_state_hash(page):
    snapshot = page.evaluate("""() => ({
        selectedAttrs: Array.from(document.querySelectorAll('#lemma-table tbody tr')).map(r => r.getAttribute('data-selected')),
        selectedLemmas: window.AppState ? window.AppState.selectedLemmas : null,
        activeTokens: window.AppState ? window.AppState.activeTokenSelections : null,
        deltas: typeof window.getDeltas === 'function' ? JSON.parse(window.getDeltas()) : null,
    })""")
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()


def _captured_requests(page):
    return page.evaluate(
        "window.requests.map(r => ({url: String(r.url), method: r.options && r.options.method, body: JSON.parse(r.options.body)}))"
    )


@pytest.mark.parametrize("lmb_source", ["lemma", "inflection"])
def test_generated_document_table_rmb_translation_independent_of_lmb_source(page, tmp_path, lmb_source):
    html = _render_haus_document(tmp_path, lmb_source=lmb_source, rmb_source="word_translation", rmb_play=True)
    errors = _mount_with_device(page, html)
    before_hash = _selection_state_hash(page)

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    page.wait_for_function("window.requests && window.requests.length >= 1")
    requests = _captured_requests(page)
    assert errors == []
    assert len(requests) == 1
    assert requests[0]["url"].endswith("/api/v1/audio/synthesize")
    assert requests[0]["method"] == "POST"
    assert requests[0]["body"] == {"text": "house", "language": "en"}
    assert _selection_state_hash(page) == before_hash

    resolve_audio(page, 0)
    page.wait_for_function("window.players.length === 1")
    page.evaluate("window.players[0].dispatchEvent(new Event('play')); window.players[0].dispatchEvent(new Event('ended'));")
    assert page.evaluate("window.audioPlayback.getState()") == "finished"


@pytest.mark.parametrize(
    "rmb_play,rmb_source,lmb_source,expected",
    [
        (False, "word_translation", "lemma", None),
        (True, "lemma", "inflection", {"text": "Haus", "language": "de"}),
        (True, "inflection", "lemma", {"text": "Häuser", "language": "de"}),
    ],
)
def test_generated_document_table_rmb_modes_and_disabled(page, tmp_path, rmb_play, rmb_source, lmb_source, expected):
    html = _render_haus_document(tmp_path, lmb_source=lmb_source, rmb_source=rmb_source, rmb_play=rmb_play)
    errors = _mount_with_device(page, html)
    before_hash = _selection_state_hash(page)

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    requests = _captured_requests(page)
    assert errors == []
    assert _selection_state_hash(page) == before_hash
    if expected is None:
        assert requests == []
    else:
        assert len(requests) == 1
        assert requests[0]["body"] == expected


def test_generated_document_table_rmb_custom_trans_column(page, tmp_path):
    mapping = configparser.ConfigParser()
    mapping.add_section("desk_columns")
    mapping.set("desk_columns", "CustomLemma", "lemma")
    mapping.set("desk_columns", "CustomInflected", "inflected")
    mapping.set("desk_columns", "CustomMeaning", "word_translation")
    mapping_path = tmp_path / "mapping.ini"
    with open(mapping_path, "w", encoding="utf-8") as fh:
        mapping.write(fh)

    html = _render_haus_document(
        tmp_path,
        lmb_source="lemma",
        rmb_source="word_translation",
        rmb_play=True,
        headers=["CustomLemma", "CustomInflected", "CustomMeaning", "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "DeskSelected"],
        row_values=["Haus", "Häuser", "house", "1", "Das Haus.", "The house.", "0"],
        mapping_path=mapping_path,
    )
    errors = _mount_with_device(page, html)

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    page.wait_for_function("window.requests && window.requests.length >= 1")
    requests = _captured_requests(page)
    assert errors == []
    assert len(requests) == 1
    assert requests[0]["body"] == {"text": "house", "language": "en"}


@pytest.mark.parametrize(
    "cell_html",
    [
        "",
        "[FAILED]",
        "[FAILED] Retry",
        "[Error: timeout]",
        '<span class="skeleton-loader kw-skeleton-cell" title="Google...">Google...</span>',
        '<span class="skeleton-loader" data-pending="true">Translating...</span>',
        "Translating...",
        "20261118183557 42",
    ],
)
def test_generated_document_table_rmb_unfinished_translation_remains_silent(page, tmp_path, cell_html):
    html = _render_haus_document(
        tmp_path,
        lmb_source="lemma",
        rmb_source="word_translation",
        rmb_play=True,
        translation="placeholder",
    )
    errors = _mount_with_device(page, html)
    page.evaluate(
        "html => { document.querySelector('#lemma-table tbody tr[data-row-id=\"0\"] td.col-translation .scrollable-cell').innerHTML = html; }",
        cell_html,
    )

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    assert errors == []
    assert _captured_requests(page) == []


def test_generated_document_table_rmb_provenanced_ellipsis_translation_speaks(page, tmp_path):
    html = _render_haus_document(
        tmp_path,
        lmb_source="lemma",
        rmb_source="word_translation",
        rmb_play=True,
        translation="Wait...",
    )
    errors = _mount_with_device(page, html)
    page.evaluate("""() => {
        const cell = document.querySelector('#lemma-table tbody tr[data-row-id="0"] td.col-translation .scrollable-cell');
        cell.setAttribute('data-provenance', 'live:google');
        cell.textContent = 'Wait...';
    }""")

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    page.wait_for_function("window.requests && window.requests.length >= 1")
    requests = _captured_requests(page)
    assert errors == []
    assert len(requests) == 1
    assert requests[0]["body"] == {"text": "Wait...", "language": "en"}


def test_generated_document_single_dispatch_ownership_with_mounted_lemma_table(page, tmp_path):
    html = _render_haus_document(tmp_path, lmb_source="inflection", rmb_source="word_translation", rmb_play=True)
    errors = _mount_with_device(page, html)
    page.evaluate("() => { window.LemmaTable && window.LemmaTable.mount('#lemma-table'); }")

    row = page.locator('#lemma-table tbody tr[data-row-id="0"]')
    row.click(button="right")

    page.wait_for_function("window.requests && window.requests.length >= 1")
    requests = _captured_requests(page)
    assert errors == []
    assert len(requests) == 1
    assert requests[0]["body"] == {"text": "house", "language": "en"}


@pytest.mark.parametrize(
    "ui_cfg,cell_html,expected",
    [
        ({"audio_rmb_play": True}, "house", ("house", "en")),
        ({"audio_rmb_play": False, "audio_rmb_source": "word_translation"}, "house", None),
        ({"audio_rmb_play": "false", "audio_rmb_source": "word_translation"}, "house", None),
        ({"audio_rmb_play": True, "audio_rmb_source": "inflection"}, "house", ("Häuser", "de")),
        ({"audio_rmb_play": True, "audio_rmb_source": "lemma"}, "house", ("Haus", "de")),
        ({"audio_rmb_play": True}, '<span class="skeleton-loader" data-pending="true">Translating...</span>', None),
        ({"audio_rmb_play": True}, "[FAILED]", None),
        ({"audio_rmb_play": True}, "[FAILED] Retry", None),
        ({"audio_rmb_play": True}, "Translating...", None),
        ({"audio_rmb_play": True}, "20261118183557 house 42", ("house", "en")),
        ({"audio_rmb_play": True}, "20261118183557 42", None),
    ],
)
def test_standalone_lemma_table_fallback_parity(page, ui_cfg, cell_html, expected):
    page.set_content(f"""<!DOCTYPE html><html><body>
        <table id="lemma-table"><tbody>
          <tr data-row-id="0">
            <td class="col-inflected" data-col="WordSourceInflectedForm">Häuser</td>
            <td class="col-lemma" data-col="WordSource">Haus</td>
            <td class="col-translation" data-col="WordDestination">{cell_html}</td>
          </tr>
        </tbody></table>
        <script id="session-lang" type="text/plain">de</script>
        <script id="session-target-lang" type="text/plain">en</script>
    </body></html>""")
    page.add_script_tag(path=str(ROOT / "web/js/components/LemmaTable.js"))
    page.evaluate(
        """cfg => {
            window.__CONFIG__ = cfg;
            window.__spoken = [];
            window.playAudio = (text, lang) => window.__spoken.push([text, lang]);
            window.LemmaTable.mount('#lemma-table');
        }""",
        ui_cfg,
    )

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    spoken = page.evaluate("window.__spoken")
    if expected is None:
        assert spoken == []
    else:
        assert spoken == [list(expected)]


def test_generated_document_owned_browser_synthesis_roundtrip(page, tmp_path, audio_server):  # noqa: F811
    server, _ = audio_server
    html = _render_haus_document(tmp_path, lmb_source="inflection", rmb_source="word_translation", rmb_play=True)
    html = html.replace(
        "<head>",
        "<head><script>window.EventSource = class { close() {} };</script>",
        1,
    )
    synthesized = []

    def relay_synthesis(route):
        req = route.request
        headers = {k: v for k, v in req.headers.items() if k.lower() in ("content-type", "x-api-token")}
        headers["X-API-Token"] = server.api_key
        upstream = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/v1/audio/synthesize",
            data=(req.post_data or "").encode("utf-8"),
            headers=headers,
            method="POST",
        )
        synthesized.append(json.loads(req.post_data or "{}"))
        with urllib.request.urlopen(upstream, timeout=5) as resp:
            route.fulfill(status=resp.status, content_type=resp.headers["Content-Type"], body=resp.read())

    page.route("http://desk.invalid/", lambda route: route.fulfill(content_type="text/html", body=html))
    page.route("**/api/v1/audio/synthesize", relay_synthesis)

    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto("http://desk.invalid/")
    page.wait_for_selector('#lemma-table tbody tr[data-row-id="0"]')
    page.evaluate("() => { window.__audioStates = []; window.addEventListener('desk-audio-state', e => window.__audioStates.push(e.detail.state)); }")

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")
    page.wait_for_function("window.audioPlayback && window.audioPlayback.getState() === 'finished'")

    assert errors == []
    assert synthesized == [{"text": "house", "language": "en"}]
    assert page.evaluate("window.__audioStates") == ["loading", "playing", "finished"]


def test_browser_tts_engine_bypasses_backend_and_sequences_segments(page, tmp_path):
    config, paths = _isolated_tsv_config(tmp_path, lmb_source="lemma", rmb_source="word_translation", rmb_play=True)
    config.set("audio", "playback_engine", "browser_tts")
    tsv = tmp_path / "20261118183557-haus.de.tsv"
    tsv.write_text(
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n"
        "Haus\tHaus\tHäuser\thouse\t1\tDas Haus.\tThe house.\t0\n",
        encoding="utf-8",
    )
    html = kardenwort_desk.run_render_flow(
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
    speech_stub = """<script>
    window.__utterances = [];
    window.__speechCancels = 0;
    window.__audioStates = [];
    window.addEventListener('desk-audio-state', e => window.__audioStates.push(e.detail.state));
    window.SpeechSynthesisUtterance = class {
        constructor(text) { this.text = text; this.lang = ''; }
    };
    Object.defineProperty(window, 'speechSynthesis', {
        configurable: true,
        value: {
            cancel() { window.__speechCancels++; },
            speak(u) { window.__utterances.push(u); },
        },
    });
    </script>"""
    device_head = f"<script>({DEVICE})();\nwindow.EventSource = class {{ close() {{}} }};</script>" + speech_stub
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.set_content(html.replace("<head>", "<head>" + device_head, 1))
    page.wait_for_selector('#lemma-table tbody tr[data-row-id="0"]')

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")
    assert _captured_requests(page) == []
    assert page.evaluate("window.__utterances.map(u => [u.text, u.lang])") == [["house", "en"]]
    page.evaluate("() => { window.__utterances[0].onstart(); window.__utterances[0].onend(); }")
    assert page.evaluate("window.audioPlayback.getState()") == "finished"

    page.evaluate("() => { window.playAudio('bei ||| der', 'de'); }")
    assert page.evaluate("window.__utterances.map(u => [u.text, u.lang])") == [["house", "en"], ["bei", "de"]]
    page.evaluate("() => { window.__utterances[1].onstart(); window.__utterances[1].onend(); }")
    assert page.evaluate("window.__utterances.map(u => [u.text, u.lang])") == [["house", "en"], ["bei", "de"], ["der", "de"]]
    page.evaluate("() => { window.__utterances[2].onstart(); window.__utterances[2].onend(); }")
    assert page.evaluate("window.audioPlayback.getState()") == "finished"
    assert _captured_requests(page) == []
    assert errors == []


def test_generated_document_deprecated_native_ahk_bridge_boundary(page, tmp_path):
    html = _render_haus_document(tmp_path, lmb_source="inflection", rmb_source="word_translation", rmb_play=True)
    device_head = f"<script>({DEVICE})();\nwindow.EventSource = class {{ close() {{}} }};</script>"
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.set_content(
        html.replace(
            "<head>",
            "<head>" + device_head + "<script>window.__ahkCalls = []; window.ahkCall = (action, arg) => window.__ahkCalls.push({action, arg});</script>",
            1,
        )
    )
    page.wait_for_selector('#lemma-table tbody tr[data-row-id="0"]')

    page.locator('#lemma-table tbody tr[data-row-id="0"]').click(button="right")

    ahk_play = [c for c in page.evaluate("window.__ahkCalls") if c.get("action") == "play"]
    assert errors == []
    assert len(ahk_play) == 1
    assert ahk_play[0]["arg"].endswith("en\nhouse")
    assert _captured_requests(page) == []
