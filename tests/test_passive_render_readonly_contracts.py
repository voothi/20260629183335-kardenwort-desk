"""Phase 1 reproduction and contract tests for Task 1.1:
Passive render aliases must be strictly read-only projections.

Covers:
- Real owned SQLite storage (no storage mocks) and real HTTP controller.
- Passive session GETs (/session/render, /, /?zid=, /lookup, view=goldendict, /api/v1/lookup)
  with wordfill enabled, blank/configured keys, and rich occurrence_data metadata:
  asserts zero writer connections, zero migrations, zero save_session calls, zero
  provider/worker/spawn/file effects, exact occurrence_data & bundle preservation,
  and coherent bundle/fingerprint projection.
- Passive project GETs (/session/render?project_id=, /?project_id=, /?project=,
  /lookup?project_id=, /api/v1/lookup?project_id=, GET /api/v1/admin/projects/synthesize)
  with blank/configured keys: asserts zero session writes, zero writer connections,
  zero migrations, zero heading translation/tokenization provider calls, and unchanged
  source sessions.
- Passive draft GETs (/session/render?session_zid=draft, /?session_zid=draft,
  /verify-language?session_zid=draft): asserts zero writes/providers/workers/spawns.
- Missing/uninitialized SQLite database on passive GET: fails explicitly without
  implicitly creating or migrating the database file.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from tests.test_controller_integration import running_controller  # noqa: F401


SESSION_ZID = "20261123061101"
SESSION_ZID_2 = "20261123061102"
DONOR_ZID = "20261123061103"
DRAFT_ZID = "20261123061104"

HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "DeskSelected",
]

BROWSER_NAV_HEADERS = {
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_get(url, *, headers=None):
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        resp = opener.open(req, timeout=10.0)
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, resp_headers, resp.read().decode("utf-8", errors="replace")


def _extract_json_island(html_text: str, script_id: str) -> dict:
    pattern = rf'<script\s+id="{re.escape(script_id)}"\s+type="application/json">(.*?)</script>'
    match = re.search(pattern, html_text, flags=re.DOTALL)
    assert match is not None, f"Missing JSON island #{script_id}"
    return json.loads(match.group(1).strip() or "{}")


def _snapshot_db(adapter) -> dict:
    with adapter.db.get_connection(read_only=True) as conn:
        sessions = [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY zid").fetchall()]
        sentences = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM sentences ORDER BY session_zid, sentence_index"
            ).fetchall()
        ]
        words = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM words ORDER BY session_zid, sentence_index, token_order"
            ).fetchall()
        ]
        projects = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id").fetchall()]
        links = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM project_sessions ORDER BY project_id, order_index, session_zid"
            ).fetchall()
        ]
        client_views = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM client_view_state ORDER BY session_zid, client_id"
            ).fetchall()
        ]
    return {
        "sessions": sessions,
        "sentences": sentences,
        "words": words,
        "projects": projects,
        "links": links,
        "client_views": client_views,
    }


def _snapshot_dir_files(directory: Path) -> dict:
    if not directory.exists():
        return {}
    snap = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.name != "db.log":
            snap[str(p.relative_to(directory))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def _install_effect_spies(monkeypatch):
    """Spy on every forbidden persistence/provider/worker/spawn effect without mocking storage reads."""
    events = {
        "write_connections": [],
        "migrations": [],
        "save_session": [],
        "translate_text": [],
        "tokenize_text": [],
        "worker_spawn": [],
        "ahk_spawn": [],
        "browser_open": [],
    }

    orig_get_conn = KardenwortDB.get_connection

    def spy_get_conn(self, read_only=False, zid=None):
        if not read_only:
            events["write_connections"].append((str(self.db_path), zid))
        return orig_get_conn(self, read_only=read_only, zid=zid)

    orig_run_migrations = KardenwortDB.run_migrations

    def spy_run_migrations(self, *args, **kwargs):
        events["migrations"].append(str(self.db_path))
        return orig_run_migrations(self, *args, **kwargs)

    orig_save_session = desk.SqliteStorageAdapter.save_session

    def spy_save_session(self, *args, **kwargs):
        events["save_session"].append(kwargs.get("session_zid") or (args[0] if args else ""))
        return orig_save_session(self, *args, **kwargs)

    def spy_translate(*args, **kwargs):
        events["translate_text"].append(args[0] if args else kwargs.get("text"))
        raise AssertionError("External translate_text must not run during passive GET")

    def spy_tokenize(*args, **kwargs):
        events["tokenize_text"].append(args[0] if args else kwargs.get("text"))
        raise AssertionError("External tokenize_text_with_fallback must not run during passive GET")

    def spy_worker(*args, **kwargs):
        events["worker_spawn"].append((args, kwargs))

    def spy_ahk(*args, **kwargs):
        events["ahk_spawn"].append((args, kwargs))

    def spy_browser(*args, **kwargs):
        events["browser_open"].append((args, kwargs))

    monkeypatch.setattr(KardenwortDB, "get_connection", spy_get_conn)
    monkeypatch.setattr(KardenwortDB, "run_migrations", spy_run_migrations)
    monkeypatch.setattr(desk.SqliteStorageAdapter, "save_session", spy_save_session)
    monkeypatch.setattr(desk, "translate_text", spy_translate)
    monkeypatch.setattr(desk, "tokenize_text_with_fallback", spy_tokenize)
    monkeypatch.setattr(desk, "run_progressive_worker_async", spy_worker)
    monkeypatch.setattr(controller, "spawn_ahk", spy_ahk)
    monkeypatch.setattr(webbrowser, "open_new_tab", spy_browser)
    return events


def _seed_wordfill_session_with_occurrence_metadata(server, adapter, tmp_path):
    """Seed a donor wordfill session and a target session with missing IPA/morph and occurrence_data."""
    results_dir = Path(server.resolved_paths["results_dir"])
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "false")
    server.config.set("wordfill", "enabled", "true")
    server.config.set("wordfill", "scan_roots", str(results_dir))
    server.wordfill = desk.resolve_wordfill_config(server.config, server.resolved_paths)

    # Donor session in SQLite AND TSV so wordfill definitely finds a match if invoked
    adapter.save_session(
        session_zid=DONOR_ZID,
        slug="donor-haus",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus steht dort.",
        headers=HEADERS,
        data_rows=[["0", "Haus", "Haus", "1", "Das Haus steht dort.", "Дом стоит там.", "дом", "/haʊ̯s/", "Substantiv, n.", "1"]],
        sentences=[{
            "session_zid": DONOR_ZID,
            "sentence_index": 1,
            "sentence_source": "Das Haus steht dort.",
            "sentence_destination": "Дом стоит там.",
            "text_provenance": "live:donor",
        }],
    )
    donor_tsv = results_dir / f"{DONOR_ZID}-donor-haus.de.tsv"
    donor_tsv.write_text(
        "\t".join(HEADERS) + "\n"
        + "\t".join(["0", "Haus", "Haus", "1", "Das Haus steht dort.", "Дом стоит там.", "дом", "/haʊ̯s/", "Substantiv, n.", "1"]) + "\n",
        encoding="utf-8",
    )

    # Target session: missing IPA & morphology (triggers old wordfill hydration write)
    # and carrying explicit occurrence_data + provenance in words.extra_fields
    adapter.save_session(
        session_zid=SESSION_ZID,
        slug="target-haus",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Das Haus ist alt.",
        headers=HEADERS,
        data_rows=[["0", "Haus", "Haus", "1", "Das Haus ist alt.", "Дом старый.", "дом", "", "", "1"]],
        sentences=[{
            "session_zid": SESSION_ZID,
            "sentence_index": 1,
            "sentence_source": "Das Haus ist alt.",
            "sentence_destination": "Дом старый.",
            "text_provenance": "live:authoritative",
        }],
    )
    rich_extra = {
        "provenance": "live:authoritative",
        "occurrence_data": {
            "2": {
                "form": "Haus",
                "pos": "n.",
                "trans": "дом (контекст)",
                "morph": "Nominativ Singular",
                "provenance": "live:authoritative",
            }
        },
    }
    with adapter.db.get_connection() as conn:
        conn.execute(
            "UPDATE words SET extra_fields = ? WHERE session_zid = ? AND lemma = ?",
            (json.dumps(rich_extra, ensure_ascii=False), SESSION_ZID, "Haus"),
        )
    return rich_extra


@pytest.mark.parametrize("blank_key", [False, True])
@pytest.mark.parametrize(
    "alias_name,path_template,auth_mode",
    [
        ("session_render", "/session/render?session_zid={zid}", "browser"),
        ("root_session_zid", "/?session_zid={zid}", "browser"),
        ("root_zid", "/?zid={zid}", "browser"),
        ("root_goldendict", "/?session_zid={zid}&view=goldendict", "browser"),
        ("lookup_alias", "/lookup?session_zid={zid}", "browser"),
        ("api_lookup", "/api/v1/lookup?session_zid={zid}&language=de", "header"),
    ],
)
def test_1_1_passive_session_get_aliases_wordfill_no_write_and_preserve_occurrence_metadata(
    tmp_path, running_controller, monkeypatch, blank_key, alias_name, path_template, auth_mode
):
    """Task 1.1: Passive session GETs must not write/migrate/hydrate-save or discard occurrence_data."""
    server_url, server, adapter = running_controller
    rich_extra = _seed_wordfill_session_with_occurrence_metadata(server, adapter, tmp_path)
    if blank_key:
        server.api_key = ""
        server.config.set("server", "api_key", "")
        if isinstance(server.goldendict, dict):
            server.goldendict["server_api_key"] = ""

    results_dir = Path(server.resolved_paths["results_dir"])
    db_before = _snapshot_db(adapter)
    files_before = _snapshot_dir_files(results_dir)
    expected_fp = desk.compute_content_fingerprint(adapter.restore_session(SESSION_ZID)["data_rows"])
    events = _install_effect_spies(monkeypatch)

    headers = (
        {"X-API-Token": server.api_key}
        if auth_mode == "header"
        else dict(BROWSER_NAV_HEADERS)
    )
    status, _, body = _http_get(
        f"{server_url}{path_template.format(zid=SESSION_ZID)}",
        headers=headers,
    )
    expect_blank_key_reject = blank_key and alias_name == "api_lookup"
    if expect_blank_key_reject:
        assert status == 403, f"[{alias_name}/blank={blank_key}] expected 403 TOKEN_NOT_CONFIGURED, got {status}: {body[:200]}"
        assert json.loads(body).get("error_code") == "TOKEN_NOT_CONFIGURED"
    else:
        assert status == 200, f"[{alias_name}/blank={blank_key}] expected 200, got {status}: {body[:200]}"

    # Zero persistence / DDL / adapter init / provider / worker / spawn / file effects
    assert events["write_connections"] == [], f"[{alias_name}] opened write DB connections: {events['write_connections']}"
    assert events["migrations"] == [], f"[{alias_name}] ran migrations during passive GET"
    assert events["save_session"] == [], f"[{alias_name}] called save_session during passive GET"
    assert events["translate_text"] == []
    assert events["tokenize_text"] == []
    assert events["worker_spawn"] == []
    assert events["ahk_spawn"] == []
    assert events["browser_open"] == []
    assert _snapshot_dir_files(results_dir) == files_before, f"[{alias_name}] wrote/modified files during passive GET"

    # Exact SQLite state & occurrence_data preservation
    db_after = _snapshot_db(adapter)
    assert db_after == db_before, f"[{alias_name}] mutated SQLite state during passive GET"
    word_row = next(w for w in db_after["words"] if w["session_zid"] == SESSION_ZID and w["lemma"] == "Haus")
    assert json.loads(word_row["extra_fields"]) == rich_extra

    if not expect_blank_key_reject:
        # Rendered bundle/fingerprint coherence: never pair unsaved hydrated cells with stored fingerprint
        html_doc = json.loads(body)["data"]["html"] if auth_mode == "header" else body
        session_island = _extract_json_island(html_doc, "session-data")
        if "fingerprint" in session_island and session_island["fingerprint"]:
            assert session_island["fingerprint"] == expected_fp
        assert "/haʊ̯s/" not in html_doc, (
            f"[{alias_name}] passive GET projected unsaved wordfill IPA that does not match stored session"
        )


@pytest.mark.parametrize("blank_key", [False, True])
@pytest.mark.parametrize(
    "alias_name,path_template,auth_mode",
    [
        ("session_render_project", "/session/render?project_id={pid}", "browser"),
        ("root_project_id", "/?project_id={pid}", "browser"),
        ("root_project", "/?project={pid}", "browser"),
        ("root_project_goldendict", "/?project_id={pid}&view=goldendict", "browser"),
        ("lookup_project", "/lookup?project_id={pid}", "browser"),
        ("api_lookup_project", "/api/v1/lookup?project_id={pid}", "header"),
        ("admin_synthesize_get", "/api/v1/admin/projects/synthesize?project_id={pid}", "header"),
    ],
)
def test_1_1_passive_project_get_aliases_no_write_no_provider_no_tokenizer(
    tmp_path, running_controller, monkeypatch, blank_key, alias_name, path_template, auth_mode
):
    """Task 1.1: Passive project GETs must not persist sessions, migrate, translate, or tokenize headings."""
    server_url, server, adapter = running_controller
    server.config.set("rendering", "display_mode", "monolithic")
    server.config.set("sentences_mode", "enabled", "true")
    server.config.set("sentences_mode", "delivery_mode", "container")
    server.config.set("wordfill", "enabled", "false")
    if not server.config.has_section("pipeline"):
        server.config.add_section("pipeline")
    server.config.set("pipeline", "translation_provider", "google")

    for zid, slug, sent, word, trans in (
        (SESSION_ZID, "proj-s1", "Das Haus ist alt.", "Haus", "дом"),
        (SESSION_ZID_2, "proj-s2", "Der Baum ist hoch.", "Baum", "дерево"),
    ):
        adapter.save_session(
            session_zid=zid,
            slug=slug,
            source_language="de",
            target_language="ru",
            text_mode="single",
            source_raw_text=sent,
            headers=HEADERS,
            data_rows=[["0", word, word, "1", sent, trans, trans, "", "", "1"]],
            sentences=[{
                "session_zid": zid,
                "sentence_index": 1,
                "sentence_source": sent,
                "sentence_destination": trans,
                "text_provenance": "live:test",
            }],
        )
    pid = adapter.db.create_project(title="Goethe Kapitel Eins", slug="goethe-kapitel-eins")
    adapter.db.link_session_to_project(pid, SESSION_ZID, order_index=0)
    adapter.db.link_session_to_project(pid, SESSION_ZID_2, order_index=1)

    if blank_key:
        server.api_key = ""
        server.config.set("server", "api_key", "")
        if isinstance(server.goldendict, dict):
            server.goldendict["server_api_key"] = ""

    results_dir = Path(server.resolved_paths["results_dir"])
    db_before = _snapshot_db(adapter)
    files_before = _snapshot_dir_files(results_dir)
    events = _install_effect_spies(monkeypatch)

    headers = {"X-API-Token": server.api_key} if auth_mode == "header" else dict(BROWSER_NAV_HEADERS)
    status, _, body = _http_get(
        f"{server_url}{path_template.format(pid=pid)}",
        headers=headers,
    )
    if blank_key and alias_name == "api_lookup_project":
        assert status == 403, f"[{alias_name}/blank={blank_key}] expected 403 TOKEN_NOT_CONFIGURED, got {status}: {body[:200]}"
        assert json.loads(body).get("error_code") == "TOKEN_NOT_CONFIGURED"
    else:
        assert status == 200, f"[{alias_name}/blank={blank_key}] expected 200, got {status}: {body[:200]}"
    assert events["write_connections"] == [], f"[{alias_name}] opened write DB connection: {events['write_connections']}"
    assert events["migrations"] == [], f"[{alias_name}] ran migrations during project GET"
    assert events["save_session"] == [], f"[{alias_name}] persisted session during project GET"
    assert events["translate_text"] == [], f"[{alias_name}] invoked heading translator during project GET"
    assert events["tokenize_text"] == [], f"[{alias_name}] invoked heading tokenizer during project GET"
    assert events["worker_spawn"] == []
    assert events["ahk_spawn"] == []
    assert events["browser_open"] == []
    assert _snapshot_db(adapter) == db_before, f"[{alias_name}] mutated SQLite during project GET"
    assert _snapshot_dir_files(results_dir) == files_before, f"[{alias_name}] wrote files during project GET"


@pytest.mark.parametrize("blank_key", [False, True])
@pytest.mark.parametrize(
    "alias_name,path_template",
    [
        ("session_render_draft", "/session/render?session_zid={zid}"),
        ("root_draft", "/?session_zid={zid}"),
        ("verify_language_draft", "/verify-language?session_zid={zid}&theme=dark"),
    ],
)
def test_1_1_passive_draft_get_aliases_no_write_no_processing(
    tmp_path, running_controller, monkeypatch, blank_key, alias_name, path_template
):
    """Task 1.1: Passive draft GETs must render stored mismatch metadata with zero writes/processing."""
    server_url, server, adapter = running_controller
    if blank_key:
        server.api_key = ""
        server.config.set("server", "api_key", "")
        if isinstance(server.goldendict, dict):
            server.goldendict["server_api_key"] = ""

    with controller._DRAFT_SESSIONS_LOCK:
        controller._DRAFT_SESSIONS[DRAFT_ZID] = {
            "text": "Das Haus ist alt.",
            "language": "en",
            "text_mode": "single",
            "theme": "dark",
            "zoom": "100",
            "tsv_path": None,
            "seq_num": None,
            "mismatch_info": {
                "is_mismatch": True,
                "detected_language": "de",
                "expected_language": "en",
                "detected_name": "German",
                "expected_name": "English",
                "confidence": 0.99,
                "action": "prompt",
                "text": "Das Haus ist alt.",
                "session_zid": DRAFT_ZID,
            },
        }
    try:
        db_before = _snapshot_db(adapter)
        results_dir = Path(server.resolved_paths["results_dir"])
        files_before = _snapshot_dir_files(results_dir)
        events = _install_effect_spies(monkeypatch)

        status, _, body = _http_get(
            f"{server_url}{path_template.format(zid=DRAFT_ZID)}",
            headers=dict(BROWSER_NAV_HEADERS),
        )
        assert status == 200, f"[{alias_name}/blank={blank_key}] expected 200, got {status}"
        assert events["write_connections"] == [], f"[{alias_name}] opened write DB connection on draft GET"
        assert events["migrations"] == [], f"[{alias_name}] ran migrations on draft GET"
        assert events["save_session"] == []
        assert events["translate_text"] == []
        assert events["tokenize_text"] == []
        assert events["worker_spawn"] == []
        assert events["ahk_spawn"] == []
        assert events["browser_open"] == []
        assert _snapshot_db(adapter) == db_before
        assert _snapshot_dir_files(results_dir) == files_before
        with controller._DRAFT_SESSIONS_LOCK:
            assert DRAFT_ZID in controller._DRAFT_SESSIONS
    finally:
        with controller._DRAFT_SESSIONS_LOCK:
            controller._DRAFT_SESSIONS.pop(DRAFT_ZID, None)


@pytest.mark.parametrize(
    "path",
    [
        f"/session/render?session_zid={SESSION_ZID}",
        f"/?session_zid={SESSION_ZID}",
        "/?project_id=1",
        f"/lookup?session_zid={SESSION_ZID}",
    ],
)
def test_1_1_passive_get_missing_uninitialized_db_fails_explicitly_without_initializing(
    tmp_path, running_controller, monkeypatch, path
):
    """Task 1.1: Missing/uninitialized DB must return explicit unavailable error, never create/migrate on GET."""
    server_url, server, _ = running_controller
    missing_db = tmp_path / "never_initialized" / "kardenwort.db"
    assert not missing_db.exists()

    server.resolved_paths["sqlite_db_path"] = str(missing_db)
    server.resolved_paths["db_path"] = str(missing_db)
    server.config.set("storage", "sqlite_db_path", str(missing_db))

    events = _install_effect_spies(monkeypatch)
    status, _, body = _http_get(f"{server_url}{path}", headers=dict(BROWSER_NAV_HEADERS))

    assert status in (500, 503), f"[{path}] expected explicit unavailable status (500/503), got {status}: {body[:200]}"
    assert missing_db.exists() is False, f"[{path}] passive GET implicitly created SQLite file on disk"
    assert events["migrations"] == [], f"[{path}] passive GET ran migrations on missing DB"
    assert events["write_connections"] == [], f"[{path}] passive GET opened write connection on missing DB"
