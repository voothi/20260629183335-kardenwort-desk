"""
tests/test_wordfill_sqlite.py - Unit tests for SQLite Indexed Wordfill and Hybrid Fallback
"""

import time
import pytest
from pathlib import Path
from unittest.mock import patch

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB


@pytest.fixture
def sqlite_wordfill_db(tmp_path):
    """Initializes a temporary KardenwortDB with initial schema."""
    db_file = tmp_path / "test_wordfill.db"
    mig_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(db_path=db_file, migrations_dir=mig_dir)
    db.run_migrations()
    return db


def test_sqlite_wordfill_indexed_lookup_speed_and_accuracy(sqlite_wordfill_db, tmp_path):
    """
    Verifies that find_wordfill_match queries SQLite with < 1ms query duration
    and correctly returns enriched fields (IPA, morphology, translation).
    """
    db = sqlite_wordfill_db

    # Seed DB with session and words
    session = {
        "zid": "20260820120000",
        "slug": "test-session",
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "single",
        "source_raw_text": "Der Apfel fällt nicht weit vom Stamm.",
    }
    sentences = [
        {
            "session_zid": "20260820120000",
            "sentence_index": 1,
            "sentence_source": "Der Apfel fällt nicht weit vom Stamm.",
            "sentence_destination": "Яблоко от яблони недалеко падает.",
        }
    ]
    words = [
        {
            "session_zid": "20260820120000",
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "Apfel",
            "inflected_form": "Äpfel",
            "lemma": "Apfel",
            "pos": "NOUN",
            "morphology": "Subst.Masc.Nom.Sg",
            "ipa": "[ˈapfl̩]",
            "word_destination": "яблоко",
            "selected": 1,
            "extra_fields": {"WordRussian": "яблоко", "WordEnglish": "apple"},
        }
    ]
    db.save_session_bundle(session, sentences, words)

    wordfill_cfg = {
        "enabled": True,
        "db": db,
        "sqlite_db_path": db.db_path,
        "scan_roots": [tmp_path / "empty_roots"],
        "target_quality": "full",
        "target_fallback": True,
        "scan_match_language": True,
    }

    # Measure lookup duration
    start = time.perf_counter()
    match = desk.find_wordfill_match("Apfel", "de", wordfill_cfg)
    dur_ms = (time.perf_counter() - start) * 1000.0

    assert match is not None
    assert match.get("WordDestination") == "яблоко"
    assert match.get("WordSourceIPA") == "[ˈapfl̩]"
    assert match.get("WordSourceMorphologyAI") == "Subst.Masc.Nom.Sg"
    assert match.get("WordRussian") == "яблоко"
    assert match.get("WordEnglish") == "apple"

    # Benchmark indexed SQLite query execution duration (< 1ms per query)
    timings = []
    with db.get_connection(read_only=True) as conn:
        for _ in range(50):
            t0 = time.perf_counter()
            candidates = db.find_wordfill_candidates("apfel", "de", limit=10, conn=conn)
            timings.append((time.perf_counter() - t0) * 1000.0)
            assert len(candidates) > 0

    avg_query_ms = sum(timings) / len(timings)
    assert avg_query_ms < 1.0  # Core SQLite indexed lookup executes in < 1ms


def test_sqlite_wordfill_case_insensitivity_and_inflected_lookup(sqlite_wordfill_db):
    """
    Verifies that lookups match case-insensitively on lemma, quotation, and inflected_form.
    """
    db = sqlite_wordfill_db

    session = {
        "zid": "20260820120100",
        "slug": "verbs",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Er ging nach Hause.",
    }
    sentences = [
        {
            "session_zid": "20260820120100",
            "sentence_index": 1,
            "sentence_source": "Er ging nach Hause.",
            "sentence_destination": "Он пошел домой.",
        }
    ]
    words = [
        {
            "session_zid": "20260820120100",
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "ging",
            "inflected_form": "ging",
            "lemma": "gehen",
            "pos": "VERB",
            "morphology": "Verb.Past.3Sg",
            "ipa": "[ɡɪŋ]",
            "word_destination": "идти",
            "selected": 1,
        }
    ]
    db.save_session_bundle(session, sentences, words)

    wordfill_cfg = {
        "enabled": True,
        "db": db,
        "target_quality": "any",
        "target_fallback": True,
    }

    # Match by inflected form "ging"
    match_inflected = desk.find_wordfill_match("ging", "de", wordfill_cfg)
    assert match_inflected is not None
    assert match_inflected["WordDestination"] == "идти"
    assert match_inflected["WordSourceIPA"] == "[ɡɪŋ]"

    # Match by lemma "GEHEN" (uppercase)
    match_lemma = desk.find_wordfill_match("GEHEN", "de", wordfill_cfg)
    assert match_lemma is not None
    assert match_lemma["WordDestination"] == "идти"


def test_sqlite_wordfill_hybrid_fallback_to_tsv(sqlite_wordfill_db, tmp_path):
    """
    Verifies that when a word is not present in SQLite, the engine falls back to
    scanning candidate TSV files in scan_roots.
    """
    db = sqlite_wordfill_db

    # Create an external candidate TSV in scan_roots
    scan_root = tmp_path / "tsv_corpus"
    scan_root.mkdir(parents=True, exist_ok=True)
    tsv_file = scan_root / "20260819100000-fruit.de.tsv"

    headers = [
        "Quotation", "WordSource", "WordSourceInflectedForm", "WordDestination",
        "WordSourceMorphologyAI", "WordSourceIPA", "DeskSelected"
    ]
    rows = [
        ["Zwetschge", "Zwetschge", "", "слива", "Subst.Fem.Nom.Sg", "[ˈtsveːtʃɡə]", "1"]
    ]

    desk.save_tsv_rows_safely(tsv_file, ["# External TSV"], headers, rows)

    wordfill_cfg = {
        "enabled": True,
        "db": db,
        "sqlite_db_path": db.db_path,
        "scan_roots": [scan_root],
        "scan_depth": 1,
        "scan_scope": "all",
        "target_quality": "full",
        "target_fallback": True,
        "scan_match_language": True,
    }

    # "Zwetschge" is NOT in SQLite, but IS in the external TSV
    match = desk.find_wordfill_match("Zwetschge", "de", wordfill_cfg)
    assert match is not None
    assert match["WordDestination"] == "слива"
    assert match["WordSourceIPA"] == "[ˈtsveːtʃɡə]"
    assert match["WordSourceMorphologyAI"] == "Subst.Fem.Nom.Sg"


def test_sqlite_hit_avoids_tsv_scanning(sqlite_wordfill_db, tmp_path):
    """
    Verifies that when a high-quality match is found in SQLite,
    external TSVs in scan_roots are not loaded from disk.
    """
    db = sqlite_wordfill_db

    # Seed DB with "katze"
    session = {
        "zid": "20260820130000",
        "slug": "pets",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Die Katze schläft.",
    }
    sentences = [
        {"session_zid": "20260820130000", "sentence_index": 1, "sentence_source": "Die Katze schläft."}
    ]
    words = [
        {
            "session_zid": "20260820130000",
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "Katze",
            "lemma": "Katze",
            "morphology": "Subst.Fem",
            "ipa": "[ˈkat͡sə]",
            "word_destination": "кошка",
        }
    ]
    db.save_session_bundle(session, sentences, words)

    scan_root = tmp_path / "tsv_corpus"
    scan_root.mkdir(parents=True, exist_ok=True)
    tsv_file = scan_root / "20260819100000-dummy.de.tsv"
    desk.save_tsv_rows_safely(tsv_file, [], ["WordSource"], [["dummy"]])

    wordfill_cfg = {
        "enabled": True,
        "db": db,
        "scan_roots": [scan_root],
        "scan_scope": "all",
        "target_quality": "full",
        "target_fallback": True,
    }

    with patch("kardenwort_desk.load_tsv_rows") as mock_load_tsv:
        match = desk.find_wordfill_match("Katze", "de", wordfill_cfg)
        assert match is not None
        assert match["WordDestination"] == "кошка"
        # Confirm TSVs on disk were not touched
        assert mock_load_tsv.call_count == 0
