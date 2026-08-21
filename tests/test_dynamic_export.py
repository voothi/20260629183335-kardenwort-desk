"""
tests/test_dynamic_export.py - Unit tests for dynamic Anki favorites export from SQLite and TSV migration
"""

import json
import csv
import pytest
from pathlib import Path

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from kardenwort_desk import (
    SqliteStorageAdapter,
    migrate_tsvs_to_db,
    core_export,
    load_config,
    SEC_SETTINGS,
    SEC_STORAGE,
)


@pytest.fixture
def export_env(tmp_path):
    """Sets up a test environment with config.ini, anki-mapping.ini, SQLite DB, and favorites dir."""
    mapping_path = tmp_path / "anki-mapping.ini"
    mapping_path.write_text("""[fields]
Quotation
WordSource
WordSourceInflectedForm
WordDestination
WordSourceMorphologyAI
WordSourceIPA
SentenceSource
SentenceDestination
SentenceSourceIndex
DeskSelected
Deck
LeitnerBox
CustomNote

[fields_mapping.word]
lemma = WordSource
selected = DeskSelected
sentence_index = SentenceSourceIndex

[fields_mapping.sentence]
source_sentence = SentenceSource
destination_sentence = SentenceDestination
""", encoding="utf-8")

    db_path = tmp_path / "data" / "kardenwort.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(f"""[settings]
default_language = de
default_target_language = ru
anki_mapping_file = {mapping_path.resolve()}
favorites_prefix = 

[storage]
backend = sqlite
sqlite_db_path = {db_path.resolve()}
fallback_to_tsv = true
""", encoding="utf-8")

    config, resolved_paths, gd, wf = load_config(cfg_path)
    mig_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(db_path=db_path, migrations_dir=mig_dir)
    db.run_migrations()

    return {
        "tmp_path": tmp_path,
        "config": config,
        "resolved_paths": resolved_paths,
        "db": db,
        "db_path": db_path,
        "mapping_path": mapping_path,
    }


def test_dynamic_export_generates_standard_favorites_tsv(export_env):
    """
    Verifies that dynamic favorites export queries SQLite where selected=1,
    formats columns according to anki-mapping.ini, and generates standard favorites TSV.
    """
    db = export_env["db"]
    config = export_env["config"]
    resolved_paths = export_env["resolved_paths"]
    tmp_path = export_env["tmp_path"]

    # Seed DB with a session having 3 selected words and 2 unselected words
    session_zid = "20260820120000"
    session = {
        "zid": session_zid,
        "slug": "sample-lesson",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Das ist ein schöner Apfel. Er schmeckt gut.",
    }
    sentences = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "sentence_source": "Das ist ein schöner Apfel.",
            "sentence_destination": "Это красивое яблоко.",
        },
        {
            "session_zid": session_zid,
            "sentence_index": 2,
            "sentence_source": "Er schmeckt gut.",
            "sentence_destination": "Оно вкусное.",
        },
    ]
    words = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "Das",
            "lemma": "der",
            "selected": 0,
        },
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 3,
            "quotation": "schöner",
            "inflected_form": "schöner",
            "lemma": "schön",
            "morphology": "Adj.Masc.Nom",
            "ipa": "[ˈʃøːnɐ]",
            "word_destination": "красивый",
            "selected": 1,
            "extra_fields": {"CustomNote": "Adjektiv"},
        },
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 4,
            "quotation": "Apfel",
            "lemma": "Apfel",
            "morphology": "Subst.Masc.Nom",
            "ipa": "[ˈapfl̩]",
            "word_destination": "яблоко",
            "selected": 1,
        },
        {
            "session_zid": session_zid,
            "sentence_index": 2,
            "token_order": 0,
            "quotation": "Er",
            "lemma": "er",
            "selected": 0,
        },
        {
            "session_zid": session_zid,
            "sentence_index": 2,
            "token_order": 1,
            "quotation": "schmeckt",
            "inflected_form": "schmeckt",
            "lemma": "schmecken",
            "morphology": "Verb.Pres.3Sg",
            "ipa": "[ʃmɛkt]",
            "word_destination": "иметь вкус",
            "selected": 1,
        },
    ]
    db.save_session_bundle(session, sentences, words)

    # Perform dynamic export
    res = core_export(
        tsv_path_or_session=session_zid,
        selected_row_ids=None,
        config=config,
        resolved_paths=resolved_paths,
        zid=session_zid,
        language="de",
    )

    assert res.get("import_complete") is True or res.get("status") == "success"

    fav_dir = Path(resolved_paths["favorites_output_dir"])
    expected_fav_file = fav_dir / f"{session_zid}-sample-lesson.de.tsv"
    assert expected_fav_file.exists()

    # Read exported favorites TSV
    comments, headers, data_rows = desk.load_tsv_rows(expected_fav_file)

    # Check header ordering matches anki-mapping.ini fields
    expected_fields = [
        "Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination",
        "WordSourceMorphologyAI", "WordSourceIPA", "SentenceSource", "SentenceDestination",
        "SentenceSourceIndex", "DeskSelected", "Deck", "LeitnerBox", "CustomNote"
    ]
    for field in expected_fields:
        assert field in headers

    # Verify only the 3 selected rows were exported
    assert len(data_rows) == 3

    # Check that DeskSelected is '1' on all rows
    sel_idx = headers.index("DeskSelected")
    lemma_idx = headers.index("WordSource")
    src_sent_idx = headers.index("SentenceSource")
    custom_note_idx = headers.index("CustomNote")

    exported_lemmas = [r[lemma_idx] for r in data_rows]
    assert exported_lemmas == ["schön", "Apfel", "schmecken"]

    for row in data_rows:
        assert row[sel_idx] == "1"

    # Check sentence linking
    assert data_rows[0][src_sent_idx] == "Das ist ein schöner Apfel."
    assert data_rows[1][src_sent_idx] == "Das ist ein schöner Apfel."
    assert data_rows[2][src_sent_idx] == "Er schmeckt gut."

    # Check custom note preservation
    assert data_rows[0][custom_note_idx] == "Adjektiv"


def test_dynamic_export_selection_modes(export_env):
    """
    Verifies that export_selection_mode ('selected', 'all', 'unselected') works as expected in SQLite mode.
    """
    db = export_env["db"]
    config = export_env["config"]
    resolved_paths = export_env["resolved_paths"]

    session_zid = "20260820120200"
    session = {
        "zid": session_zid,
        "slug": "mode-test",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Eins zwei drei.",
    }
    sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Eins zwei drei."}
    ]
    words = [
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 0, "quotation": "Eins", "lemma": "eins", "selected": 1},
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 1, "quotation": "zwei", "lemma": "zwei", "selected": 0},
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 2, "quotation": "drei", "lemma": "drei", "selected": 1},
    ]
    db.save_session_bundle(session, sentences, words)

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=db.db_path)

    # 1. Mode 'all'
    zid_all = "20260820120201"
    session_all = {"zid": zid_all, "slug": "mode-all", "source_language": "de", "source_raw_text": "Eins zwei drei."}
    sentences_all = [{"session_zid": zid_all, "sentence_index": 1, "sentence_source": "Eins zwei drei."}]
    words_all = [
        {"session_zid": zid_all, "sentence_index": 1, "token_order": 0, "quotation": "Eins", "lemma": "eins", "selected": 1},
        {"session_zid": zid_all, "sentence_index": 1, "token_order": 1, "quotation": "zwei", "lemma": "zwei", "selected": 0},
        {"session_zid": zid_all, "sentence_index": 1, "token_order": 2, "quotation": "drei", "lemma": "drei", "selected": 1},
    ]
    db.save_session_bundle(session_all, sentences_all, words_all)
    config.set(SEC_SETTINGS, "export_selection_mode", "all")
    res_all = adapter.export_favorites(zid_all)
    fav_file_all = Path(resolved_paths["favorites_output_dir"]) / f"{zid_all}-mode-all.de.tsv"
    _, _, rows_all = desk.load_tsv_rows(fav_file_all)
    assert len(rows_all) == 3

    # 2. Mode 'unselected'
    zid_unsel = "20260820120202"
    session_unsel = {"zid": zid_unsel, "slug": "mode-unsel", "source_language": "de", "source_raw_text": "Eins zwei drei."}
    sentences_unsel = [{"session_zid": zid_unsel, "sentence_index": 1, "sentence_source": "Eins zwei drei."}]
    words_unsel = [
        {"session_zid": zid_unsel, "sentence_index": 1, "token_order": 0, "quotation": "Eins", "lemma": "eins", "selected": 1},
        {"session_zid": zid_unsel, "sentence_index": 1, "token_order": 1, "quotation": "zwei", "lemma": "zwei", "selected": 0},
        {"session_zid": zid_unsel, "sentence_index": 1, "token_order": 2, "quotation": "drei", "lemma": "drei", "selected": 1},
    ]
    db.save_session_bundle(session_unsel, sentences_unsel, words_unsel)
    config.set(SEC_SETTINGS, "export_selection_mode", "unselected")
    res_unsel = adapter.export_favorites(zid_unsel)
    fav_file_unsel = Path(resolved_paths["favorites_output_dir"]) / f"{zid_unsel}-mode-unsel.de.tsv"
    _, _, rows_unsel = desk.load_tsv_rows(fav_file_unsel)
    assert len(rows_unsel) == 1
    assert rows_unsel[0][0] == "zwei"

    # 3. Mode 'selected'
    zid_sel = "20260820120203"
    session_sel = {"zid": zid_sel, "slug": "mode-sel", "source_language": "de", "source_raw_text": "Eins zwei drei."}
    sentences_sel = [{"session_zid": zid_sel, "sentence_index": 1, "sentence_source": "Eins zwei drei."}]
    words_sel = [
        {"session_zid": zid_sel, "sentence_index": 1, "token_order": 0, "quotation": "Eins", "lemma": "eins", "selected": 1},
        {"session_zid": zid_sel, "sentence_index": 1, "token_order": 1, "quotation": "zwei", "lemma": "zwei", "selected": 0},
        {"session_zid": zid_sel, "sentence_index": 1, "token_order": 2, "quotation": "drei", "lemma": "drei", "selected": 1},
    ]
    db.save_session_bundle(session_sel, sentences_sel, words_sel)
    config.set(SEC_SETTINGS, "export_selection_mode", "selected")
    res_sel = adapter.export_favorites(zid_sel)
    fav_file_sel = Path(resolved_paths["favorites_output_dir"]) / f"{zid_sel}-mode-sel.de.tsv"
    _, _, rows_sel = desk.load_tsv_rows(fav_file_sel)
    assert len(rows_sel) == 2


def test_migrate_tsvs_to_db_idempotency_and_integrity(export_env):
    """
    Verifies that migrate_tsvs_to_db parses historical results/*.tsv files,
    normalizes sentence indices, serializes extra fields to JSON, and inserts into kardenwort.db idempotently.
    """
    tmp_path = export_env["tmp_path"]
    config = export_env["config"]
    resolved_paths = export_env["resolved_paths"]
    db = export_env["db"]

    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = results_dir

    # Create 2 historical TSV files
    zid1 = "20260810100000"
    tsv1 = results_dir / f"{zid1}-historical1.de.tsv"
    txt1 = results_dir / f"{zid1}-historical1.txt"
    txt1.write_text("Sonne scheint. Vögel singen.", encoding="utf-8")

    headers1 = [
        "Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceDestination",
        "SentenceSourceIndex", "DeskSelected", "SpecialCategory"
    ]
    rows1 = [
        ["Sonne", "Sonne", "солнце", "Sonne scheint.", "Солнце светит.", "0", "1", "Nature"],
        ["scheint", "scheinen", "светить", "Sonne scheint.", "Солнце светит.", "0", "0", "Nature"],
        ["Vögel", "Vogel", "птицы", "Vögel singen.", "Птицы поют.", "1", "1", "Animals"],
    ]
    desk.save_tsv_rows_safely(tsv1, ["# Historical session 1"], headers1, rows1)

    zid2 = "20260811110000"
    tsv2 = results_dir / f"{zid2}-historical2.de.tsv"
    headers2 = ["Quotation", "WordSource", "WordDestination", "SentenceSource", "SentenceSourceIndex", "DeskSelected"]
    rows2 = [
        ["Buch", "Buch", "книга", "Das Buch ist gut.", "1", "1"]
    ]
    desk.save_tsv_rows_safely(tsv2, ["# Historical session 2"], headers2, rows2)

    # 1. Run initial migration
    res1 = migrate_tsvs_to_db(results_dir, config=config, resolved_paths=resolved_paths)
    assert res1["ok"] is True
    assert res1["migrated_sessions"] == 2
    assert res1["skipped_sessions"] == 0
    assert res1["total_sentences"] == 3
    assert res1["total_words"] == 4

    # Verify session 1 in DB
    bundle1 = db.get_session_bundle(zid1, parse_json=True)
    assert bundle1 is not None
    assert bundle1["session"]["slug"] == "historical1"
    assert bundle1["session"]["source_language"] == "de"
    assert len(bundle1["sentences"]) == 2
    assert len(bundle1["words"]) == 3

    # Check sentence indices normalized to 1-based
    s_indices = [s["sentence_index"] for s in bundle1["sentences"]]
    assert s_indices == [1, 2]

    # Check extra fields preserved as JSON dict
    word_sonne = [w for w in bundle1["words"] if w["lemma"] == "Sonne"][0]
    assert word_sonne["extra_fields"] == {"SpecialCategory": "Nature"}
    assert word_sonne["selected"] == 1

    # 2. Run migration a second time -> IDEMPOTENT (0 migrated, 2 skipped)
    res2 = migrate_tsvs_to_db(results_dir, config=config, resolved_paths=resolved_paths)
    assert res2["ok"] is True
    assert res2["migrated_sessions"] == 0
    assert res2["skipped_sessions"] == 2
