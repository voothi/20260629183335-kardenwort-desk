"""
tests/test_decoupled_enrichment.py - Unit tests for decoupled IntelliFiller ephemeral execution
and direct SQLite token ingestion without persistent disk TSVs.
"""

import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import kardenwort_desk as desk
from kardenwort_desk import (
    SqliteStorageAdapter,
    run_headless_intellifiller,
    _run_headless_intellifiller_impl,
    load_tsv_rows,
    save_tsv_rows_safely,
    load_config,
    SEC_SETTINGS,
    SEC_STORAGE,
)
from kardenwort_db import KardenwortDB


@pytest.fixture
def enrichment_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "kardenwort.db"

    anki_mapping = tmp_path / "anki-mapping.ini"
    anki_mapping.write_text(
        """[fields]
Quotation
WordSource
WordSourceInflectedForm
WordDestination
WordSourceMorphologyAI
WordSourceIPA
WordSourceSynonymAI
SentenceSource
SentenceDestination
SentenceSourceIndex
DeskSelected
Deck
LeitnerBox

[fields_mapping.word]
lemma = WordSource
selected = DeskSelected
sentence_index = SentenceSourceIndex

[fields_mapping.sentence]
source_sentence = SentenceSource
destination_sentence = SentenceDestination
""",
        encoding="utf-8",
    )

    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(
        f"""[settings]
default_language = de
default_target_language = ru
anki_mapping_file = {anki_mapping.resolve()}
favorites_prefix = 
results_dir = {results_dir.resolve()}

[storage]
backend = sqlite
sqlite_db_path = {db_path.resolve()}
fallback_to_tsv = true
""",
        encoding="utf-8",
    )

    config, resolved_paths, gd, wf = load_config(cfg_path)
    mig_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(db_path=db_path, migrations_dir=mig_dir)
    db.run_migrations()

    adapter = SqliteStorageAdapter(
        config=config,
        resolved_paths=resolved_paths,
        db_path=db_path,
    )

    return {
        "tmp_path": tmp_path,
        "config": config,
        "resolved_paths": resolved_paths,
        "db": db,
        "db_path": db_path,
        "results_dir": results_dir,
        "adapter": adapter,
    }


def test_run_headless_intellifiller_ephemeral_cli(enrichment_env):
    """
    Verifies that CLI subprocess IntelliFiller runs against an ephemeral TSV
    inside tempfile.TemporaryDirectory and copies results back to destination TSV.
    """
    tmp_path = enrichment_env["tmp_path"]
    config = enrichment_env["config"]
    resolved_paths = dict(enrichment_env["resolved_paths"])
    resolved_paths["kardenwort_python"] = Path("python")
    resolved_paths["intellifiller_headless"] = Path("mock_headless.py")

    target_tsv = tmp_path / "test_session.tsv"
    headers = ["Quotation", "WordSource", "WordDestination", "WordSourceMorphologyAI", "WordSourceIPA"]
    rows = [["Katze", "Katze", "", "", ""]]
    save_tsv_rows_safely(target_tsv, ["# comment"], headers, rows)

    def fake_subprocess_run(cmd, *args, **kwargs):
        # Locate the --tsv arg passed to the subprocess
        tsv_idx = cmd.index("--tsv") + 1
        sub_tsv = Path(cmd[tsv_idx])
        assert sub_tsv != target_tsv, "Subprocess must receive ephemeral temp TSV, not original target"
        assert sub_tsv.exists(), "Ephemeral temp TSV must exist during subprocess execution"

        # Simulate IntelliFiller enriching the ephemeral TSV
        comments, sub_headers, sub_rows = load_tsv_rows(sub_tsv)
        sub_rows[0][2] = "кошка"
        sub_rows[0][3] = "Noun|Fem|Sing"
        sub_rows[0][4] = "/ˈkat͡sə/"
        save_tsv_rows_safely(sub_tsv, comments, sub_headers, sub_rows)

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = ""
        mock_res.stderr = ""
        return mock_res

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        res = _run_headless_intellifiller_impl(
            tsv_path=target_tsv,
            prompt_name="morphology_and_ipa",
            config=config,
            resolved_paths=resolved_paths,
            zid="20260822100000",
        )
        assert res is True

    # Verify target_tsv received enriched data
    _, updated_headers, updated_rows = load_tsv_rows(target_tsv)
    assert updated_rows[0][2] == "кошка"
    assert updated_rows[0][3] == "Noun|Fem|Sing"
    assert updated_rows[0][4] == "/ˈkat͡sə/"


def test_sqlite_batch_update_words_direct_and_extra_fields(enrichment_env):
    """
    Verifies that KardenwortDB.batch_update_words updates standard columns
    and serializes unmapped custom columns into extra_fields JSON.
    """
    adapter = enrichment_env["adapter"]
    db = enrichment_env["db"]
    session_zid = "20260822103000"

    # Seed session
    session = {
        "zid": session_zid,
        "slug": "test-enrich",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Die Katze schläft.",
    }
    sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Die Katze schläft.", "sentence_destination": "Кошка спит."}
    ]
    words = [
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 0, "quotation": "Katze", "lemma": "Katze", "selected": 1},
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 1, "quotation": "schläft", "lemma": "schlafen", "selected": 0},
    ]
    db.save_session_bundle(session, sentences, words)

    updates = [
        {
            "token_order": 0,
            "updates": {
                "WordDestination": "кошка",
                "WordSourceMorphologyAI": "Noun|Fem|Sing",
                "WordSourceIPA": "/ˈkat͡sə/",
                "WordSourceSynonymAI": "Mieze, Stubentiger",
                "CustomNote": "Common feline",
            },
        }
    ]

    count = db.batch_update_words(session_zid, updates)
    assert count == 1

    db_words = db.get_words_by_session(session_zid)
    w0 = db_words[0]
    assert w0["word_destination"] == "кошка"
    assert w0["morphology"] == "Noun|Fem|Sing"
    assert w0["ipa"] == "/ˈkat͡sə/"

    # Check extra_fields contains custom columns
    assert isinstance(w0["extra_fields"], dict)
    assert w0["extra_fields"]["WordSourceSynonymAI"] == "Mieze, Stubentiger"
    assert w0["extra_fields"]["CustomNote"] == "Common feline"


def test_sqlite_enrich_session_intellifiller_and_export_fidelity(enrichment_env):
    """
    Verifies that SqliteStorageAdapter.enrich_session_intellifiller enriches SQLite session
    via ephemeral scratch payload without leaving persistent TSV files in results/,
    and export_favorites produces accurate dynamic TSVs including extra_fields.
    """
    adapter = enrichment_env["adapter"]
    db = enrichment_env["db"]
    results_dir = enrichment_env["results_dir"]
    session_zid = "20260822104000"

    # Seed session in SQLite
    session = {
        "zid": session_zid,
        "slug": "fidelity-lesson",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Der Hund bellt laut.",
    }
    sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Der Hund bellt laut.", "sentence_destination": "Собака громко лает."}
    ]
    words = [
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 0, "quotation": "Hund", "lemma": "Hund", "selected": 1},
        {"session_zid": session_zid, "sentence_index": 1, "token_order": 1, "quotation": "bellt", "lemma": "bellen", "selected": 0},
    ]
    db.save_session_bundle(session, sentences, words)

    # Mock run_headless_intellifiller to populate enriched fields in the ephemeral TSV
    def fake_headless_ifiller(tsv_path, *args, **kwargs):
        comments, headers, data_rows = load_tsv_rows(tsv_path)
        if "WordSourceSynonymAI" not in headers:
            headers.append("WordSourceSynonymAI")
            for r in data_rows:
                r.append("")
        syn_idx = headers.index("WordSourceSynonymAI")
        dest_idx = headers.index("WordDestination")
        morph_idx = headers.index("WordSourceMorphologyAI")
        ipa_idx = headers.index("WordSourceIPA")

        data_rows[0][dest_idx] = "пёс"
        data_rows[0][morph_idx] = "Noun|Masc|Sing"
        data_rows[0][ipa_idx] = "/hʊnt/"
        data_rows[0][syn_idx] = "Köter, Vierbeiner"
        save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
        return True

    with patch("kardenwort_desk.run_headless_intellifiller", side_effect=fake_headless_ifiller):
        success = adapter.enrich_session_intellifiller(
            session_zid=session_zid,
            prompt_name="morphology_and_ipa",
            selected_rows=[0],
        )
        assert success is True

    # Verify NO persistent TSV files exist in results/ for this session
    session_tsvs = list(results_dir.glob(f"*{session_zid}*.tsv"))
    assert len(session_tsvs) == 0, f"Expected no persistent TSV in results/, found: {session_tsvs}"

    # Verify SQLite words were enriched
    db_words = db.get_words_by_session(session_zid)
    w0 = db_words[0]
    assert w0["word_destination"] == "пёс"
    assert w0["morphology"] == "Noun|Masc|Sing"
    assert w0["ipa"] == "/hʊnt/"
    assert w0["extra_fields"]["WordSourceSynonymAI"] == "Köter, Vierbeiner"

    # Export favorites from SQLite and verify fidelity
    export_result = adapter.export_favorites(
        session_zid=session_zid,
        save_to_favorites_override=True,
        send_to_anki_override=False,
    )
    assert export_result.get("import_complete") is True or export_result.get("status") == "success"

    fav_dir = Path(enrichment_env["resolved_paths"]["favorites_output_dir"])
    fav_path = fav_dir / f"{session_zid}-fidelity-lesson.de.tsv"
    assert fav_path.exists()

    _, fav_headers, fav_rows = load_tsv_rows(fav_path)
    assert len(fav_rows) == 1
    dest_col = fav_headers.index("WordDestination")
    morph_col = fav_headers.index("WordSourceMorphologyAI")
    ipa_col = fav_headers.index("WordSourceIPA")
    syn_col = fav_headers.index("WordSourceSynonymAI")

    assert fav_rows[0][dest_col] == "пёс"
    assert fav_rows[0][morph_col] == "Noun|Masc|Sing"
    assert fav_rows[0][ipa_col] == "/hʊnt/"
    assert fav_rows[0][syn_col] == "Köter, Vierbeiner"
