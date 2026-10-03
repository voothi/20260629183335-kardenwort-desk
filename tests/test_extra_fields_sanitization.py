import json
from pathlib import Path
from unittest.mock import patch
import pytest

from kardenwort_db import (
    KardenwortDB,
    sanitize_extra_fields,
    repair_corrupted_extra_fields,
)
from kardenwort_desk import SqliteStorageAdapter, load_config, load_tsv_rows


@pytest.fixture
def temp_db(tmp_path):
    root_dir = Path(__file__).resolve().parent.parent
    db_file = tmp_path / "data" / "test_kardenwort.db"
    migrations_dir = root_dir / "schemas" / "migrations"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    db = KardenwortDB(
        db_path=db_file,
        migrations_dir=migrations_dir,
        resolved_paths={"results_dir": results_dir},
    )
    db.run_migrations()
    return db


def test_sanitize_extra_fields_deep_nesting():
    """Verify that deeply nested extra_fields JSON strings and dicts are unwrapped cleanly."""
    base_data = {
        "lemma": "der",
        "occurrence_data": {"0": {"pos": "art.", "trans": "the"}},
    }

    # 5 levels of nesting: {"extra_fields": json.dumps({"extra_fields": ...})}
    nested = base_data
    for _ in range(5):
        nested = {"extra_fields": json.dumps(nested)}

    sanitized = sanitize_extra_fields(nested)
    assert isinstance(sanitized, dict)
    assert "extra_fields" not in sanitized
    assert sanitized.get("lemma") == "der"
    assert "occurrence_data" in sanitized
    assert sanitized["occurrence_data"]["0"]["pos"] == "art."


def test_sanitize_extra_fields_dict_nesting():
    """Verify that nested dicts under extra_fields or extrafields keys are flattened."""
    data = {
        "custom_col": "val1",
        "extra_fields": {
            "extrafields": {
                "custom_col": "val1",
                "nested_col": "val2",
                "occurrence_data": {"1": {"morph": "nom"}},
            }
        },
    }
    sanitized = sanitize_extra_fields(data)
    assert "extra_fields" not in sanitized
    assert "extrafields" not in sanitized
    assert sanitized.get("custom_col") == "val1"
    assert sanitized.get("nested_col") == "val2"
    assert sanitized.get("occurrence_data") == {"1": {"morph": "nom"}}


def test_sanitize_extra_fields_invalid_json():
    """Verify fallback handling when extra_fields contains malformed strings or non-dicts."""
    assert sanitize_extra_fields("invalid { json") == {}
    assert sanitize_extra_fields(None) == {}
    assert sanitize_extra_fields([1, 2, 3]) == {}


def test_batch_update_words_does_not_nest_extra_fields(temp_db):
    """Verify batch_update_words maps extra_fields directly without creating nested subkeys."""
    session_zid = "20261003210000"
    session = {
        "zid": session_zid,
        "slug": "test-session",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "der Test",
    }
    sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "der Test", "sentence_destination": ""}
    ]
    words = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "der",
            "lemma": "der",
            "extra_fields": {"occurrence_data": {"0": {"pos": "art."}}},
        }
    ]
    temp_db.save_session_bundle(session, sentences, words)

    # Repeatedly update the word via batch_update_words 3 times with extra_fields
    for i in range(3):
        updates = [
            {
                "token_order": 0,
                "sentence_index": 1,
                "updates": {
                    "pos": "art.",
                    "extra_fields": json.dumps({
                        "occurrence_data": {str(i): {"pos": "art.", "iter": i}},
                        "custom_flag": f"run_{i}",
                    }),
                },
            }
        ]
        count = temp_db.batch_update_words(session_zid, updates)
        assert count == 1

    # Verify database contents
    db_words = temp_db.get_words_by_session(session_zid, parse_json=True)
    assert len(db_words) == 1
    w0 = db_words[0]
    ef = w0["extra_fields"]

    assert isinstance(ef, dict)
    assert "extra_fields" not in ef
    assert "extrafields" not in ef
    assert ef.get("custom_flag") == "run_2"
    assert "occurrence_data" in ef
    assert "2" in ef["occurrence_data"]


def test_repair_corrupted_extra_fields(temp_db):
    """Verify repair_corrupted_extra_fields repairs bloated/nested records in the words table."""
    session_zid = "20261003210001"
    session = {
        "zid": session_zid,
        "slug": "test-repair",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "wurde mit",
    }
    sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "wurde mit", "sentence_destination": ""}
    ]
    words = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "wurde",
            "lemma": "werden",
        },
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "mit",
            "lemma": "mit",
            "extra_fields": {"occurrence_data": {"1": {"pos": "prep."}}},
        },
    ]
    temp_db.save_session_bundle(session, sentences, words)

    # Intentionally corrupt word 0 with deeply nested extra_fields
    bloated = {"lemma": "wurde", "occurrence_data": {"0": {"pos": "verb"}}}
    for _ in range(4):
        bloated = {"extra_fields": json.dumps(bloated)}

    with temp_db.get_connection() as conn:
        conn.execute(
            "UPDATE words SET extra_fields = ? WHERE session_zid = ? AND token_order = 0;",
            (json.dumps(bloated), session_zid),
        )

    repaired_count = repair_corrupted_extra_fields(temp_db)
    assert repaired_count >= 1

    db_words = temp_db.get_words_by_session(session_zid, parse_json=True)
    w0 = [w for w in db_words if w["token_order"] == 0][0]
    ef0 = w0["extra_fields"]

    assert isinstance(ef0, dict)
    assert "extra_fields" not in ef0
    assert ef0.get("lemma") == "wurde"
    assert "occurrence_data" in ef0


def test_reword_wordfill_word_integration(tmp_path):
    """
    Integration test:
    - Seed a session with wordfill populated data (provenance corpus:wordfill, occurrence_data).
    - Enrich with intellifiller twice (re-word).
    - Ensure zero ERR_TSV_PARSE, proper provenance update, and clean extra_fields without recursion.
    """
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
SentenceSource
SentenceDestination
SentenceSourceIndex
DeskSelected

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

    session_zid = "20261003210002"
    session = {
        "zid": session_zid,
        "slug": "test-wordfill-reword",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Es wurde dunkel.",
    }
    sentences = [
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Es wurde dunkel.", "sentence_destination": "Стемнело."}
    ]
    words = [
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "Es",
            "lemma": "es",
            "word_provenance": "corpus:wordfill",
            "extra_fields": {"occurrence_data": {"0": {"pos": "pron.", "trans": "оно"}}},
        },
        {
            "session_zid": session_zid,
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "wurde",
            "lemma": "werden",
            "word_provenance": "corpus:wordfill",
            "extra_fields": {"occurrence_data": {"1": {"pos": "verb", "trans": "становиться", "morph": "past"}}},
        },
    ]
    db.save_session_bundle(session, sentences, words)

    # Mock headless intellifiller
    with patch("kardenwort_desk.run_headless_intellifiller") as mock_runner:
        def fake_runner(tsv_path, *args, **kwargs):
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            # Verify no nested extra_fields in headers
            assert "extra_fields" not in [h.lower() for h in headers]
            dest_col = headers.index("WordDestination")
            data_rows[1][dest_col] = "стал (reworded)"
            adapter._tsv_fallback.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            return True

        mock_runner.side_effect = fake_runner

        # First re-word
        ok1 = adapter.enrich_session_intellifiller(
            session_zid=session_zid,
            prompt_name="test",
            selected_rows=[1],
            reprocess=True,
        )
        assert ok1 is True

        # Second re-word (re-clicking re-word on already enriched/wordfill row)
        ok2 = adapter.enrich_session_intellifiller(
            session_zid=session_zid,
            prompt_name="test",
            selected_rows=[1],
            reprocess=True,
        )
        assert ok2 is True

    # Validate final state in SQLite
    db_words = db.get_words_by_session(session_zid, parse_json=True)
    w1 = [w for w in db_words if w["token_order"] == 1][0]
    assert w1["word_destination"] == "стал (reworded)"
    assert w1["word_provenance"] == "live:intellifiller"
    ef1 = w1["extra_fields"]
    assert isinstance(ef1, dict)
    assert "extra_fields" not in ef1
    assert "extrafields" not in ef1
    assert "occurrence_data" in ef1
