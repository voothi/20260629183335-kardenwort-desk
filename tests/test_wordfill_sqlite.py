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


def test_sqlite_wordfill_tsv_mode_falls_back_to_scan_roots(sqlite_wordfill_db, tmp_path):
    """
    Verifies that in pure TSV storage mode ([storage] backend = tsv),
    when a word is not present in SQLite, the engine falls back to
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
        "storage_backend": "tsv",
        "backend": "tsv",
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


def test_sqlite_wordfill_sqlite_mode_bypasses_tsv_scanning(sqlite_wordfill_db, tmp_path):
    """
    Verifies that when SQLite storage backend is active ([storage] backend = sqlite),
    a miss in SQLite returns None and NEVER falls back to scanning disk TSVs in scan_roots.
    """
    db = sqlite_wordfill_db

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
        "storage_backend": "sqlite",
        "backend": "sqlite",
        "scan_roots": [scan_root],
        "scan_depth": 1,
        "scan_scope": "all",
        "target_quality": "full",
        "target_fallback": True,
        "scan_match_language": True,
    }

    with patch("kardenwort_desk.collect_candidate_files") as mock_collect, \
         patch("kardenwort_desk.load_tsv_rows") as mock_load_tsv:
        # "Zwetschge" is not in SQLite
        match = desk.find_wordfill_match("Zwetschge", "de", wordfill_cfg)
        assert match is None
        # Assert Phase 2 disk scanning was completely bypassed
        assert mock_collect.call_count == 0
        assert mock_load_tsv.call_count == 0


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
        "storage_backend": "sqlite",
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


def test_cross_pollinate_from_sqlite_siblings(sqlite_wordfill_db, tmp_path):
    """
    Verifies that cross_pollinate_from_siblings retrieves word-level fields (morphology,
    IPA, word translation) from SQLite sibling sessions without reading TSVs from disk,
    while strictly avoiding sentence-level fields.
    """
    db = sqlite_wordfill_db

    # Sibling session (master or older sibling in same batch)
    sib_zid = "20260824120000"
    sib_session = {
        "zid": sib_zid,
        "slug": "sib-session",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Das Buch ist gut.",
    }
    sib_sentences = [
        {
            "session_zid": sib_zid,
            "sentence_index": 1,
            "sentence_source": "Das Buch ist gut.",
            "sentence_destination": "Книга хорошая.",
        }
    ]
    sib_words = [
        {
            "session_zid": sib_zid,
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "Buch",
            "lemma": "Buch",
            "morphology": "Subst.Neut.Nom.Sg",
            "ipa": "[buːx]",
            "word_destination": "книга",
            "extra_fields": {"WordCustom": "test_custom_attr", "SentenceSource": "FORBIDDEN_OVERWRITE"},
        }
    ]
    db.save_session_bundle(sib_session, sib_sentences, sib_words)

    # Current child session within 10s of sibling
    my_zid = "20260824120010"
    my_session = {
        "zid": my_zid,
        "slug": "my-child-session",
        "source_language": "de",
        "target_language": "ru",
        "source_raw_text": "Ich lese ein Buch.",
    }
    my_sentences = [
        {
            "session_zid": my_zid,
            "sentence_index": 1,
            "sentence_source": "Ich lese ein Buch.",
            "sentence_destination": "Я читаю книгу.",
        }
    ]
    my_words = [
        {
            "session_zid": my_zid,
            "sentence_index": 1,
            "token_order": 3,
            "quotation": "Buch",
            "lemma": "Buch",
            "morphology": "",
            "ipa": "",
            "word_destination": "",
        }
    ]
    db.save_session_bundle(my_session, my_sentences, my_words)

    # Target data rows in child worker
    headers = [
        "Quotation", "WordSource", "WordDestination", "WordSourceMorphologyAI",
        "WordSourceIPA", "WordCustom", "SentenceSource", "SentenceDestination"
    ]
    data_rows = [
        ["Buch", "Buch", "", "", "", "", "Ich lese ein Buch.", "Я читаю книгу."]
    ]
    role_fields = {"lemma": "WordSource"}

    from kardenwort_desk import SqliteStorageAdapter
    adapter = SqliteStorageAdapter(db_path=db.db_path)

    working_path = tmp_path / f"{my_zid}-my-child-session.tsv"

    # Cross pollinate with SQLite adapter
    result_rows = desk.cross_pollinate_from_siblings(
        working_path, data_rows, headers, role_fields,
        storage_adapter=adapter, is_sqlite=True
    )

    assert result_rows[0][headers.index("WordDestination")] == "книга"
    assert result_rows[0][headers.index("WordSourceMorphologyAI")] == "Subst.Neut.Nom.Sg"
    assert result_rows[0][headers.index("WordSourceIPA")] == "[buːx]"
    assert result_rows[0][headers.index("WordCustom")] == "test_custom_attr"

    # Ensure sentence-level fields are completely untouched
    assert result_rows[0][headers.index("SentenceSource")] == "Ich lese ein Buch."
    assert result_rows[0][headers.index("SentenceDestination")] == "Я читаю книгу."


def test_cmd_progressive_worker_sqlite_mode_fast_path(sqlite_wordfill_db, tmp_path, monkeypatch):
    """
    Verifies that cmd_progressive_worker in SQLite mode runs immediately without
    blocking on watchdog or waiting for physical disk marker files.
    """
    db = sqlite_wordfill_db
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    import configparser
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
anki_mapping_file=./anki-mapping.ini
[pipeline]
parallelize_core_and_translation=false
progressive_text_translation=true
progressive_timeout_seconds=15
lemma_base_provider=google
[triggers]
run_text_translation=auto
run_lemma_base_translation=auto
run_lemma_enrichment=auto
[storage]
backend=sqlite
sqlite_db_path=data/kardenwort.db
[sentences_mode]
enabled=true
""")
    config.set("storage", "sqlite_db_path", str(db.db_path))

    anki_mapping_path = tmp_path / "anki-mapping.ini"
    anki_mapping_path.write_text("""[fields]
WordSource = lemma
WordDestination = word_translation
WordSourceMorphologyAI = morphology
WordSourceIPA = ipa
SentenceSourceIndex = sentence_index
SentenceSource = sentence_source
SentenceDestination = sentence_destination
""", encoding="utf-8")
    config.set("settings", "anki_mapping_file", str(anki_mapping_path))

    cfg_file = tmp_path / "test_config.ini"
    with open(cfg_file, "w", encoding="utf-8") as cf:
        config.write(cf)

    resolved_paths = {
        "storage_backend": "sqlite",
        "sqlite_db_path": db.db_path,
        "results_dir": results_dir,
        "anki_mapping_file": anki_mapping_path,
    }

    # Populate session in SQLite
    test_zid = "20260824140000"
    db.save_session_bundle(
        session={
            "zid": test_zid,
            "slug": "fast-path-test",
            "source_language": "de",
            "target_language": "ru",
            "text_mode": "single",
            "source_raw_text": "Der Hund bellt.",
        },
        sentences=[{"sentence_index": 1, "sentence_source": "Der Hund bellt.", "sentence_destination": None}],
        words=[{"sentence_index": 1, "token_order": 1, "quotation": "Hund", "lemma": "Hund", "word_destination": None}]
    )

    # Monkeypatch translation stages to verify execution
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Собака лает."})
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: {l: "собака" for l in lemmas})

    class Args:
        tsv = str(results_dir / f"{test_zid}-fast-path-test.tsv")
        zid = test_zid
        stage = "all"
        target_lang = "ru"
        config = str(cfg_file)
        text_mode = "single"
        skip_intellifiller = True

    start_time = time.perf_counter()
    desk.cmd_progressive_worker(Args())
    elapsed = time.perf_counter() - start_time

    # Must complete near-instantaneously (< 2.0s), never blocking on 30s timeouts
    assert elapsed < 2.0

    # Ensure updated translation is saved to SQLite
    with db.get_connection(zid=test_zid) as conn:
        words = conn.execute("SELECT * FROM words WHERE session_zid = ?", (test_zid,)).fetchall()
        assert len(words) == 1
        assert words[0]["word_destination"] == "собака"

