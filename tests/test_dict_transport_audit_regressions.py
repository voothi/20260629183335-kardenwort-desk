"""Review contracts for the two 20261026093717 changes.

Run through scripts/run_dict_transport_audit.py so every database is owned.
These assertions intentionally expose unfinished production behavior.
"""
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_dict_row_identity_regressions import TRANSPORT
from tests.test_goldendict_article_transport_regressions import render_test_lookup, render_test_desktop

ZID = "20261027221743"
HEADERS = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
ROWS = [["Haus", "Haus", "house", "1", "0", "0"],
        ["Baum", "Baum", "tree", "2", "0", "0"]]


def seed_sqlite(tmp_path):
    cfg, paths, _, _ = desk.load_config()
    database = tmp_path / "probe.db"
    cfg.set("storage", "backend", "sqlite")
    cfg.set("storage", "sqlite_db_path", str(database))
    paths.update(sqlite_db_path=database, storage_backend="sqlite")
    adapter = desk.SqliteStorageAdapter(config=cfg, resolved_paths=paths, db_path=database)
    adapter.save_session(session_zid=ZID, slug="audit", source_language="de", target_language="en",
                         text_mode="single", source_raw_text="Haus. Baum.", headers=HEADERS,
                         data_rows=[list(r) for r in ROWS], sentences=[
                             {"session_zid": ZID, "sentence_index": 1, "sentence_source": "Haus."},
                             {"session_zid": ZID, "sentence_index": 2, "sentence_source": "Baum."}])
    return cfg, paths, adapter


def destinations(adapter):
    with adapter.db.get_connection(read_only=True) as connection:
        return [r[0] for r in connection.execute(
            "SELECT word_destination FROM words WHERE session_zid=? ORDER BY sentence_index", (ZID,))]


def test_invalid_second_sqlite_target_leaves_entire_save_unchanged(tmp_path):
    cfg, paths, adapter = seed_sqlite(tmp_path)
    edits = [dict(row_id=0, sentence_idx=1, token_order=0, column="WordDestination", value="changed"),
             dict(row_id=1, sentence_idx=99, token_order=0, column="WordDestination", value="invalid")]
    with pytest.raises(desk.StructuredError):
        desk.core_edit_save(ZID, edits, cfg, paths, zid=ZID, language="de")
    assert destinations(adapter) == ["house", "tree"], "Rejected save partially committed its first edit"


def test_missing_both_sqlite_coordinates_cannot_use_display_position(tmp_path):
    cfg, paths, adapter = seed_sqlite(tmp_path)
    rejected = False
    try:
        desk.core_edit_save(ZID, [dict(row_id=1, column="WordDestination", value="guessed")],
                            cfg, paths, zid=ZID, language="de")
    except desk.StructuredError:
        rejected = True
    assert (rejected, destinations(adapter)) == (True, ["house", "tree"])


def test_unknown_tsv_occurrence_cannot_fall_back_to_display_row(tmp_path):
    cfg, paths, _, _ = desk.load_config()
    cfg.set("storage", "backend", "tsv")
    paths["storage_backend"] = "tsv"
    target = tmp_path / (ZID + ".de.tsv")
    desk.save_tsv_rows_safely(target, [], HEADERS, [list(r) for r in ROWS])
    before = target.read_bytes()
    rejected = False
    try:
        desk.core_edit_save(target, [dict(row_id=0, sentence_idx=99, token_order=99,
                                          column="WordDestination", value="wrong-row")],
                            cfg, paths, zid=ZID, language="de")
    except desk.StructuredError:
        rejected = True
    assert (rejected, target.read_bytes() == before) == (True, True)


def test_lowercase_token_order_survives_lookup_rendering(page):
    headers = ["Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "token_order", "DeskSelected"]
    html = render_test_lookup(server_enabled=True, headers=headers,
                              data_rows=[["Haus", "Haus", "house", "2", "9", "0"]])
    page.route("**/*", lambda route: route.abort())
    page.set_content(html)
    assert page.locator('#lemma-table tbody tr').first.get_attribute("data-token-order") == "9"


@contextmanager
def mount_article(page, html):
    content = html.encode("utf-8")
    class ArticleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ArticleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/audit-article.html")
        page.wait_for_function("typeof window.onSaveClick === 'function'")
        yield
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def edit_translation(page, row, value):
    cell = page.locator(f'tr[data-row-id="{row}"] td.col-translation')
    cell.dblclick()
    field = cell.locator("input.edit-input")
    expect(field).to_be_visible()
    field.fill(value)
    field.press("Enter")


def test_missing_lookup_sentence_cannot_emit_guessed_edit(page):
    html = render_test_lookup(server_enabled=True,
        headers=["Quotation", "WordSource", "WordDestination", "TokenOrder", "DeskSelected"],
        data_rows=[["Baum", "Baum", "tree", "0", "0"]])
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.route('**/*', lambda route: route.abort())
    page.set_content(html)
    expect(page.locator('.kw-dict-article')).to_have_attribute('data-state', 'unavailable')
    page.locator('tr[data-row-id="0"] td.col-translation').dblclick()
    assert page.locator('input.edit-input').count() == 0
    assert page.get_by_role('checkbox').is_disabled()
    assert page.evaluate("typeof window.getDeltas") == 'undefined'
    assert requests == []


@pytest.mark.parametrize("action", ["playAudio('Haus', 'de')", "retrySession()", "retryRow(0)"])
def test_offline_audio_dispatches_no_backend_request(page, action):
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.route("**/*", lambda route: route.abort())
    page.set_content(render_test_lookup(server_enabled=False))
    requests.clear()
    name = action.split("(", 1)[0]
    page.evaluate("() => { if (typeof " + name + " === 'function') " + action + "; }")
    # One browser round trip flushes the synchronous fetch dispatch without a fixed delay.
    page.evaluate("() => Promise.resolve()")
    assert requests == []


@pytest.mark.parametrize("invalid", ['not-json', '[]', '{"extra_fields": {"nested": true}}'])
def test_invalid_extra_fields_cannot_create_recursive_metadata(tmp_path, invalid):
    cfg, paths, adapter = seed_sqlite(tmp_path)
    before = adapter.db.get_words_by_session(ZID)
    with pytest.raises(desk.StructuredError):
        adapter.update_word(ZID, 1, 0, "extra_fields", invalid)
    assert adapter.db.get_words_by_session(ZID) == before


def test_real_different_origin_save_persists_exact_occurrence(running_controller, page):
    url, server, adapter = running_controller
    adapter.save_session(session_zid=ZID, slug="audit", source_language="de", target_language="en",
                         text_mode="single", source_raw_text="Haus. Baum.", headers=HEADERS,
                         data_rows=[list(r) for r in ROWS], sentences=[
                             {"session_zid": ZID, "sentence_index": 1, "sentence_source": "Haus."},
                             {"session_zid": ZID, "sentence_index": 2, "sentence_source": "Baum."}])
    html = render_test_desktop(server_enabled=True, server_url=url, api_token="test-controller-api-key",
                              session_zid=ZID, headers=HEADERS, data_rows=[list(r) for r in ROWS])
    failures = []
    responses = []
    console = []
    page.on("console", lambda message: console.append(message.text))
    page.on("requestfailed", lambda request: failures.append((request.url, request.failure)))
    page.on("response", lambda response: responses.append((response.url, response.status)))
    with mount_article(page, html):
        edit_translation(page, 1, "correct-tree")
        page.evaluate("onSaveClick()")
        assert destinations(adapter) == ["house", "correct-tree"], (failures, responses, console)
        assert page.evaluate("isDirty()") is False


def test_reword_selected_sentence_keeps_complete_target_descriptor(running_controller, page):
    url, server, adapter = running_controller
    html = render_test_desktop(server_enabled=True, server_url=url, api_token="test-controller-api-key",
                              session_zid=ZID, headers=HEADERS, data_rows=[list(r) for r in ROWS])
    # Inspect the request contract independently of browser transport capability.
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    page.evaluate("toggleRowSelection(1, true)")
    page.evaluate("onRewordClick()")
    page.wait_for_function("__requests.some(r => r.url.includes('/session/reword'))")
    payload = page.evaluate("__requests.find(r => r.url.includes('/session/reword')).body")
    targets = payload.get("target_coordinates") or []
    assert any(t.get("sentence_idx") == 2 and t.get("token_order") == 0 for t in targets), payload


def test_desktop_stream_recovery_reads_real_membership(running_controller, page, tmp_path):
    from tests.test_release_boundary_contracts import open_browser, seed_session, use_backend
    url, server, adapter = running_controller
    use_backend(server, "sqlite")
    seed_session(server, adapter, selected=True)
    open_browser(page, tmp_path, url, selected=True)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    assert page.evaluate("AppState.selectedLemmas") == {"haus": True}, "Recovery fabricated membership instead of reading SQLite"


@pytest.mark.parametrize("backend", ["sqlite", "tsv"])
def test_reword_explicit_target_changes_only_selected_sentence(running_controller, monkeypatch, backend):
    from tests.test_release_boundary_contracts import use_backend
    from pathlib import Path
    import kardenwort_controller as controller
    url, server, adapter = running_controller
    use_backend(server, backend)
    server.config.set("pipeline", "lemma_reprocess_provider", "google")
    path = Path(server.resolved_paths["results_dir"]) / (ZID + ".de.tsv")
    if backend == "sqlite":
        adapter.save_session(session_zid=ZID, slug="audit", source_language="de", target_language="en",
                             text_mode="single", source_raw_text="Haus. Baum.", headers=HEADERS,
                             data_rows=[list(r) for r in ROWS])
    else:
        desk.save_tsv_rows_safely(path, [], HEADERS, [list(r) for r in ROWS])
    # Only the external translation provider is controlled; persistence is real.
    monkeypatch.setattr(controller, "translate_lemmas_fast_path",
                        lambda lemmas, **kwargs: {lemma: "translated-" + lemma for lemma in lemmas})
    payload = dict(session_zid=ZID, language="de", row_ids=[1], token_orders=[0], sentence_idx=0,
                   target_coordinates=[dict(sentence_idx=2, token_order=0)])
    request = urllib.request.Request(url + "/session/reword", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "X-API-Token": "test-controller-api-key"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
    if backend == "sqlite":
        assert destinations(adapter) == ["house", "translated-Baum"]
    else:
        _, headers, rows = desk.load_tsv_rows(path)
        actual = {r[headers.index("WordSource")]: r[headers.index("WordDestination")] for r in rows}
        assert actual == {"Haus": "house", "Baum": "translated-Baum"}
