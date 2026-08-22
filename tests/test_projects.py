import os
import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from kardenwort_db import (
    KardenwortDB,
    QueryExecutionError,
    QuerySecurityError,
)
from kardenwort_desk import (
    SqliteStorageAdapter,
    resolve_project_deck_path,
    synthesize_project_deck_descriptions,
    aggregate_project_materials,
    synthesize_project_materials,
)


@pytest.fixture
def temp_project_env(tmp_path):
    """Fixture providing isolated DB, storage adapter, and results/favorites paths."""
    db_file = tmp_path / "data" / "test_kardenwort.db"
    migrations_dir = root_dir / "schemas" / "migrations"
    results_dir = tmp_path / "results"
    favorites_dir = tmp_path / "favorites"
    results_dir.mkdir(parents=True, exist_ok=True)
    favorites_dir.mkdir(parents=True, exist_ok=True)

    resolved_paths = {
        "sqlite_db_path": str(db_file),
        "db_path": str(db_file),
        "results_dir": str(results_dir),
        "favorites_output_dir": str(favorites_dir),
        "migrations_dir": str(migrations_dir),
    }

    db = KardenwortDB(
        db_path=db_file,
        migrations_dir=migrations_dir,
        resolved_paths=resolved_paths,
    )
    db.run_migrations()

    adapter = SqliteStorageAdapter(resolved_paths=resolved_paths, db_path=db_file)

    return {
        "db": db,
        "adapter": adapter,
        "resolved_paths": resolved_paths,
        "tmp_path": tmp_path,
        "favorites_dir": favorites_dir,
    }


# ---------------------------------------------------------------------------
# Unit Tests: Project Hierarchy, CRUD & Path Resolution
# ---------------------------------------------------------------------------
def test_project_crud_and_nesting(temp_project_env):
    db: KardenwortDB = temp_project_env["db"]

    # 1. Create root project
    p1 = db.create_project(title="Faust", description="Goethe's Tragedy")
    assert p1 > 0
    proj1 = db.get_project(p1)
    assert proj1 is not None
    assert proj1["title"] == "Faust"
    assert proj1["slug"] == "faust"
    assert proj1["parent_id"] is None

    # 2. Create nested child projects
    p2 = db.create_project(title="Act 1", parent_id=p1, description="First Act")
    p3 = db.create_project(title="Act 2", parent_id=p1, description="Second Act")
    p4 = db.create_project(title="Scene 1", parent_id=p2, description="Night")

    assert p2 > 0 and p3 > 0 and p4 > 0

    # 3. Path resolution
    path_nodes = db.get_project_path(p4)
    titles = [n["title"] for n in path_nodes]
    assert titles == ["Faust", "Act 1", "Scene 1"]

    deck_path = resolve_project_deck_path(p4, db, language="de")
    assert deck_path == "German::Faust::Act 1::Scene 1"

    # 4. Project tree retrieval
    tree = db.get_project_tree(p1)
    assert len(tree) == 1
    root_node = tree[0]
    assert root_node["title"] == "Faust"
    assert len(root_node["children"]) == 2
    act1_node = next(c for c in root_node["children"] if c["title"] == "Act 1")
    assert len(act1_node["children"]) == 1
    assert act1_node["children"][0]["title"] == "Scene 1"


def test_session_linking_and_reordering(temp_project_env):
    db: KardenwortDB = temp_project_env["db"]

    # Setup project and 3 mock sessions
    pid = db.create_project(title="German Short Stories", slug="short-stories")
    s1 = "20260821100001"
    s2 = "20260821100002"
    s3 = "20260821100003"

    for zid, slug in [(s1, "story-1"), (s2, "story-2"), (s3, "story-3")]:
        db.insert_session({
            "zid": zid,
            "slug": slug,
            "source_language": "de",
            "source_raw_text": f"Text for {slug}",
        })

    # Link sessions to project
    assert db.link_session_to_project(pid, s1)
    assert db.link_session_to_project(pid, s2)
    assert db.link_session_to_project(pid, s3)

    sessions = db.get_project_sessions(pid)
    assert len(sessions) == 3
    assert [s["session_zid"] for s in sessions] == [s1, s2, s3]

    # Reorder sessions: s3, s1, s2
    assert db.reorder_project_sessions(pid, [s3, s1, s2])
    reordered = db.get_project_sessions(pid)
    assert [s["session_zid"] for s in reordered] == [s3, s1, s2]

    # Unlink s1
    assert db.unlink_session_from_project(pid, s1)
    remaining = db.get_project_sessions(pid)
    assert [s["session_zid"] for s in remaining] == [s3, s2]


def test_soft_deletion_and_restore_sessions(temp_project_env):
    db: KardenwortDB = temp_project_env["db"]

    zid = "20260821120000"
    db.insert_session({
        "zid": zid,
        "slug": "soft-delete-test",
        "source_language": "de",
        "source_raw_text": "Ein Test.",
    })

    assert db.get_session(zid) is not None
    assert len(db.list_sessions()) >= 1

    # Soft delete session
    assert db.soft_delete_session(zid)

    # Regular lookup must NOT return soft-deleted session
    assert db.get_session(zid) is None
    assert all(s["zid"] != zid for s in db.list_sessions())

    # Deleted sessions list should find it
    deleted = db.get_deleted_sessions()
    assert any(s["zid"] == zid for s in deleted)

    # Restore session
    assert db.restore_session(zid)
    assert db.get_session(zid) is not None
    assert any(s["zid"] == zid for s in db.list_sessions())


def test_soft_delete_and_restore_project_hierarchy(temp_project_env):
    db: KardenwortDB = temp_project_env["db"]

    p_root = db.create_project(title="Root Book")
    p_chap = db.create_project(title="Chapter 1", parent_id=p_root)
    p_sec = db.create_project(title="Section A", parent_id=p_chap)

    # Soft delete root project (cascades down subtree)
    assert db.soft_delete_project(p_root)

    assert db.get_project(p_root) is None
    assert db.get_project(p_chap) is None
    assert db.get_project(p_sec) is None

    # Restore bottom section (should restore ancestors when restore_parents=True)
    assert db.restore_project(p_sec, restore_parents=True)

    assert db.get_project(p_root) is not None
    assert db.get_project(p_chap) is not None
    assert db.get_project(p_sec) is not None


# ---------------------------------------------------------------------------
# Integration Tests: Bottom-Up Material Synthesis & Anki Import Compatibility
# ---------------------------------------------------------------------------
def test_bottom_up_material_aggregation_and_anki_metadata(temp_project_env):
    db: KardenwortDB = temp_project_env["db"]
    adapter: SqliteStorageAdapter = temp_project_env["adapter"]
    resolved_paths = temp_project_env["resolved_paths"]

    # 1. Build project structure: Book -> Chapter 1 & Chapter 2
    book_id = db.create_project(title="Faust", description="Goethe's Magnum Opus", slug="faust")
    chap1_id = db.create_project(title="Prolog im Himmel", parent_id=book_id, description="Heavenly Prologue")
    chap2_id = db.create_project(title="Nacht", parent_id=book_id, description="Faust at night")

    # 2. Populate 2 sessions with words
    s1_zid = "20260821140001"
    s2_zid = "20260821140002"

    adapter.save_session(
        session_zid=s1_zid,
        slug="prolog",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Die Sonne tönt nach alter Weise.",
        headers=["Quotation", "WordSource", "WordDestination", "DeskSelected", "SentenceSource", "Deck"],
        data_rows=[
            ["Die", "der", "The", "1", "Die Sonne tönt nach alter Weise.", ""],
            ["Sonne", "Sonne", "Sun", "1", "Die Sonne tönt nach alter Weise.", ""],
            ["tönt", "tönen", "sounds", "0", "Die Sonne tönt nach alter Weise.", ""],
        ],
    )

    adapter.save_session(
        session_zid=s2_zid,
        slug="nacht",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Habe nun, ach! Philosophie studiert.",
        headers=["Quotation", "WordSource", "WordDestination", "DeskSelected", "SentenceSource", "Deck"],
        data_rows=[
            ["Philosophie", "Philosophie", "Philosophy", "1", "Habe nun, ach! Philosophie studiert.", ""],
            ["studiert", "studieren", "studied", "1", "Habe nun, ach! Philosophie studiert.", ""],
        ],
    )

    # Link sessions
    db.link_session_to_project(chap1_id, s1_zid, order_index=0)
    db.link_session_to_project(chap2_id, s2_zid, order_index=0)

    # 3. Test export_favorites on a single session linked to project
    res_single = adapter.export_favorites(s1_zid, language="German")
    fav_files = list(temp_project_env["favorites_dir"].glob("*.tsv"))
    assert len(fav_files) >= 1

    # Read exported single TSV to verify Deck header contains project path
    exported_tsv_lines = fav_files[0].read_text(encoding="utf-8").strip().splitlines()
    data_lines = [l for l in exported_tsv_lines if not l.startswith("#")]
    headers = data_lines[0].split("\t")
    deck_idx = headers.index("Deck")
    first_data_row = data_lines[1].split("\t")
    assert first_data_row[deck_idx] == "German::Faust::Prolog im Himmel"

    # 4. Synthesize entire project deck
    agg_result = aggregate_project_materials(
        project_id=book_id,
        resolved_paths=resolved_paths,
        language="German",
    )

    assert agg_result["ok"] is True
    assert agg_result["total_sessions"] == 2
    # 2 selected from s1 + 2 selected from s2 = 4 total words
    assert agg_result["total_words"] == 4

    tsv_path = Path(agg_result["tsv_path"])
    json_path = Path(agg_result["json_path"])
    assert tsv_path.exists()
    assert json_path.exists()

    # 5. Validate companion JSON description structure for anki-csv-importer.py
    metadata = json.loads(json_path.read_text(encoding="utf-8"))
    assert "deck_descriptions" in metadata
    descriptions = metadata["deck_descriptions"]
    assert "German::Faust" in descriptions
    assert descriptions["German::Faust"] == "Goethe's Magnum Opus"
    assert "German::Faust::Prolog im Himmel" in descriptions
    assert descriptions["German::Faust::Prolog im Himmel"] == "Heavenly Prologue"
    assert "German::Faust::Nacht" in descriptions
    assert descriptions["German::Faust::Nacht"] == "Faust at night"

    # 6. Validate TSV content and hierarchical deck assignments
    tsv_content = tsv_path.read_text(encoding="utf-8").strip().splitlines()
    tsv_data = [l for l in tsv_content if not l.startswith("#")]
    agg_headers = tsv_data[0].split("\t")
    agg_deck_idx = agg_headers.index("Deck")
    quot_idx = agg_headers.index("Quotation")

    deck_values = {row.split("\t")[quot_idx]: row.split("\t")[agg_deck_idx] for row in tsv_data[1:]}
    assert deck_values["Die"] == "German::Faust::Prolog im Himmel"
    assert deck_values["Sonne"] == "German::Faust::Prolog im Himmel"
    assert deck_values["Philosophie"] == "German::Faust::Nacht"
    assert deck_values["studiert"] == "German::Faust::Nacht"


def test_cli_project_commands(temp_project_env, monkeypatch):
    from types import SimpleNamespace
    from kardenwort_desk import (
        cmd_create_project,
        cmd_list_projects,
        cmd_link_session,
        cmd_reorder_session,
        cmd_export_project_deck,
    )

    db: KardenwortDB = temp_project_env["db"]
    resolved_paths = temp_project_env["resolved_paths"]

    # 1. Test cmd_create_project
    args_create = SimpleNamespace(
        config=None,
        title="Test Book",
        slug="test-book",
        parent_id=None,
        description="Book Description",
        order_index=0,
        zid=None,
        json_output=True,
    )
    # Mock sys.exit to avoid exiting test runner
    monkeypatch.setattr(sys, "exit", lambda code=0: None)
    monkeypatch.setattr(
        "kardenwort_desk.load_config",
        lambda p: (None, resolved_paths, None, None)
    )

    cmd_create_project(args_create)
    projects = db.list_projects()
    assert any(p["title"] == "Test Book" for p in projects)
    p_obj = next(p for p in projects if p["title"] == "Test Book")
    pid = p_obj["id"]

    # 2. Test cmd_list_projects
    args_list = SimpleNamespace(
        config=None,
        parent_id="all",
        include_deleted=False,
        tree=True,
        zid=None,
        json_output=True,
    )
    cmd_list_projects(args_list)

    # 3. Create session & link via cmd_link_session
    s_zid = "20260821150001"
    db.insert_session({
        "zid": s_zid,
        "slug": "chapter-one",
        "source_language": "de",
        "source_raw_text": "Sample text",
    })

    args_link = SimpleNamespace(
        config=None,
        project_id=pid,
        session_zid=s_zid,
        zid=s_zid,
        order_index=1,
        json_output=True,
    )
    cmd_link_session(args_link)

    links = db.get_project_sessions(pid)
    assert len(links) == 1
    assert links[0]["session_zid"] == s_zid

    # 4. Test cmd_reorder_session
    s_zid2 = "20260821150002"
    db.insert_session({
        "zid": s_zid2,
        "slug": "chapter-two",
        "source_language": "de",
        "source_raw_text": "Sample text 2",
    })
    db.link_session_to_project(pid, s_zid2)

    args_reorder = SimpleNamespace(
        config=None,
        project_id=pid,
        session_zids=[s_zid2, s_zid],
        zid=None,
        json_output=True,
    )
    cmd_reorder_session(args_reorder)
    reordered_links = db.get_project_sessions(pid)
    assert [l["session_zid"] for l in reordered_links] == [s_zid2, s_zid]

    # 5. Test cmd_export_project_deck
    args_export = SimpleNamespace(
        config=None,
        project_id=pid,
        language="German",
        send_to_anki=False,
        zid=None,
        json_output=True,
    )
    cmd_export_project_deck(args_export)
    fav_files = list(temp_project_env["favorites_dir"].glob("*.tsv"))
    assert len(fav_files) >= 1


def test_synthesize_project_materials_and_reader_view(temp_project_env):
    db: KardenwortDB = temp_project_env["db"]
    adapter: SqliteStorageAdapter = temp_project_env["adapter"]
    resolved_paths = temp_project_env["resolved_paths"]

    # 1. Create Book with 2 chapters
    book_id = db.create_project(title="Faust", slug="faust", description="Goethe's Tragedy")
    c1_id = db.create_project(title="Chapter 1", parent_id=book_id, slug="ch1")
    c2_id = db.create_project(title="Chapter 2", parent_id=book_id, slug="ch2")

    # 2. Add sessions to chapters
    s1_zid = "20260822100001"
    s2_zid = "20260822100002"

    adapter.save_session(
        session_zid=s1_zid,
        slug="ch1-sess",
        source_language="de",
        source_raw_text="Erster Satz im ersten Kapitel.",
        headers=["Quotation", "WordSource", "DeskSelected", "SentenceSource", "SentenceSourceIndex", "Deck"],
        data_rows=[
            ["Erster", "erst", "1", "Erster Satz im ersten Kapitel.", "1", ""],
            ["Satz", "Satz", "0", "Erster Satz im ersten Kapitel.", "1", ""],
        ],
    )

    adapter.save_session(
        session_zid=s2_zid,
        slug="ch2-sess",
        source_language="de",
        source_raw_text="Zweiter Satz im zweiten Kapitel.",
        headers=["Quotation", "WordSource", "DeskSelected", "SentenceSource", "SentenceSourceIndex", "Deck"],
        data_rows=[
            ["Zweiter", "zweit", "1", "Zweiter Satz im zweiten Kapitel.", "1", ""],
            ["Satz", "Satz", "1", "Zweiter Satz im zweiten Kapitel.", "1", ""],
        ],
    )

    db.link_session_to_project(c1_id, s1_zid, order_index=0)
    db.link_session_to_project(c2_id, s2_zid, order_index=1)

    # 3. Test synthesize_project_materials
    synthesized = synthesize_project_materials(
        project_id=book_id,
        db=db,
        resolved_paths=resolved_paths,
        language="German",
    )

    assert synthesized["ok"] is True
    assert synthesized["project_id"] == book_id
    assert synthesized["project_title"] == "Faust"
    assert synthesized["total_sessions"] == 2
    assert synthesized["total_words"] == 3  # 3 unique lemmas: erst, Satz, zweit (Satz merged)

    # Check continuous text contains both chapter texts
    assert "Erster Satz" in synthesized["source_text"]
    assert "Zweiter Satz" in synthesized["source_text"]

    # Check hierarchical deck paths in data rows
    headers = synthesized["headers"]
    deck_idx = headers.index("Deck")
    quot_idx = headers.index("Quotation")

    deck_map = {r[quot_idx]: r[deck_idx] for r in synthesized["data_rows"]}
    assert deck_map["Erster"] == "German::Faust::Chapter 1"
    assert deck_map["Zweiter"] == "German::Faust::Chapter 2"

    # Check chapter metadata
    assert len(synthesized["chapters"]) == 2
    assert synthesized["chapters"][0]["deck"] == "German::Faust::Chapter 1"
    assert synthesized["chapters"][1]["deck"] == "German::Faust::Chapter 2"


def test_cli_project_reader_integration(temp_project_env, monkeypatch):
    from types import SimpleNamespace
    from kardenwort_desk import cmd_desk, cmd_restore

    db: KardenwortDB = temp_project_env["db"]
    adapter: SqliteStorageAdapter = temp_project_env["adapter"]
    resolved_paths = temp_project_env["resolved_paths"]

    pid = db.create_project(title="CLI Book", slug="cli-book")
    sid = "20260822120001"
    adapter.save_session(
        session_zid=sid,
        slug="cli-sess",
        source_language="de",
        source_raw_text="CLI Text",
        headers=["Quotation", "WordSource", "DeskSelected", "SentenceSource", "SentenceSourceIndex", "Deck"],
        data_rows=[["CLI", "cli", "1", "CLI Text", "1", ""]],
    )
    db.link_session_to_project(pid, sid)

    monkeypatch.setattr(
        "kardenwort_desk.load_config",
        lambda p: (None, resolved_paths, None, None)
    )

    # 1. Test cmd_desk with --project and --no-gui
    args_desk = SimpleNamespace(
        config=None,
        project=pid,
        file=[],
        text_mode="multi",
        language="German",
        no_gui=True,
        theme="dark",
        bypass_lang_check=True,
        zid=None,
        trace_id=None,
        json_output=True,
    )
    cmd_desk(args_desk)

    # 2. Test cmd_restore with --project and --no-gui
    args_restore = SimpleNamespace(
        config=None,
        project=pid,
        file=[],
        zid=None,
        no_gui=True,
        language="German",
        json_output=True,
    )
    cmd_restore(args_restore)


def test_synthesize_project_materials_frequency_sorting(temp_project_env, monkeypatch):
    from kardenwort_desk import synthesize_project_materials, sort_rows_by_frequency

    db: KardenwortDB = temp_project_env["db"]
    adapter: SqliteStorageAdapter = temp_project_env["adapter"]
    tmp_path = temp_project_env["tmp_path"]
    resolved_paths = dict(temp_project_env["resolved_paths"])
    resolved_paths["kardenwort_workspace"] = tmp_path
    resolved_paths["kardenwort_python"] = "python"

    # Mock subprocess for sort-frequency returning a deterministic frequency order: 'the', 'button', 'attribute', 'modal'
    def mock_subprocess_run(cmd, input, capture_output, text, encoding, check):
        from types import SimpleNamespace
        order = ["the", "button", "attribute", "modal"]
        words = [w.strip() for w in input.splitlines() if w.strip()]
        sorted_w = sorted(words, key=lambda x: order.index(x.lower()) if x.lower() in order else 999)
        return SimpleNamespace(stdout="\n".join(sorted_w) + "\n")

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    pid = db.create_project(title="Freq Project", slug="freq-project")
    sid1 = "20260822130001"
    sid2 = "20260822130002"

    adapter.save_session(
        session_zid=sid1,
        slug="sess1",
        source_language="en",
        source_raw_text="Sentence 1",
        headers=["Quotation", "WordSource", "DeskSelected", "SentenceSource", "SentenceSourceIndex", "Deck"],
        data_rows=[
            ["modal", "modal", "1", "Sentence 1", "1", ""],
            ["attribute", "attribute", "1", "Sentence 1", "1", ""],
        ],
    )
    adapter.save_session(
        session_zid=sid2,
        slug="sess2",
        source_language="en",
        source_raw_text="Sentence 2",
        headers=["Quotation", "WordSource", "DeskSelected", "SentenceSource", "SentenceSourceIndex", "Deck"],
        data_rows=[
            ["the", "the", "1", "Sentence 2", "1", ""],
            ["button", "button", "1", "Sentence 2", "1", ""],
        ],
    )

    db.link_session_to_project(pid, sid1, order_index=0)
    db.link_session_to_project(pid, sid2, order_index=1)

    import configparser
    cfg = configparser.ConfigParser()
    cfg.add_section("languages")
    cfg.set("languages", "en_lemma_index", "data/en/freq.csv")

    # Ensure fake lemma index exists
    freq_dir = tmp_path / "data" / "en"
    freq_dir.mkdir(parents=True, exist_ok=True)
    (freq_dir / "freq.csv").write_text("the\nbutton\nattribute\nmodal\n", encoding="utf-8")

    synthesized = synthesize_project_materials(
        project_id=pid,
        db=db,
        config=cfg,
        resolved_paths=resolved_paths,
        language="en",
    )

    assert synthesized["ok"] is True
    lemmas = [r[1] for r in synthesized["data_rows"]]
    # Expected order: the (#0), button (#1), attribute (#2), modal (#3)
    assert lemmas == ["the", "button", "attribute", "modal"]



