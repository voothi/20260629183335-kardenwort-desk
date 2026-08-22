import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import configparser

import kardenwort_desk as desk
from kardenwort_desk import SentenceMatchStrategy, normalize_sentence_for_lookup, SEC_LOOKUP
from kardenwort_db import KardenwortDB


def test_sentence_match_strategy_enum():
    assert SentenceMatchStrategy.CHECKSUM == "checksum"
    assert SentenceMatchStrategy.NORMALIZED == "normalized"
    assert SentenceMatchStrategy.CONTEXTUAL == "contextual"
    assert SentenceMatchStrategy.NONE == "none"

    assert SentenceMatchStrategy.from_str("checksum") == SentenceMatchStrategy.CHECKSUM
    assert SentenceMatchStrategy.from_str("NORMALIZED") == SentenceMatchStrategy.NORMALIZED
    assert SentenceMatchStrategy.from_str("contextual") == SentenceMatchStrategy.CONTEXTUAL
    assert SentenceMatchStrategy.from_str("none") == SentenceMatchStrategy.NONE
    assert SentenceMatchStrategy.from_str("unknown") == SentenceMatchStrategy.NORMALIZED
    assert SentenceMatchStrategy.from_str(None) == SentenceMatchStrategy.NORMALIZED


def test_normalize_sentence_for_lookup():
    # Markdown headers
    assert normalize_sentence_for_lookup("# Hello World") == "Hello World"
    assert normalize_sentence_for_lookup("### Chapter 1: Introduction") == "Chapter 1: Introduction"
    
    # Whitespace variances
    assert normalize_sentence_for_lookup("  This   is \n\n a   sentence.  ") == "This is a sentence."
    
    # Quotes and apostrophes
    assert normalize_sentence_for_lookup("It’s a ‘test’ with “smart quotes” and «guillemets».") == "It's a 'test' with \"smart quotes\" and \"guillemets\"."
    
    # Zero-width spaces & BOM
    assert normalize_sentence_for_lookup("\ufeff\u200bClean\u200c text\u200d") == "Clean text"


def test_db_find_sentence_by_strategy_checksum_and_fallback(tmp_path):
    db_path = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    session_record = {
        "zid": "20260822100001",
        "slug": "sample-sentence",
        "source_language": "en",
        "target_language": "ru",
        "text_mode": "single",
        "source_raw_text": "The quick brown fox jumps over the lazy dog.",
    }
    sentences = [{
        "session_zid": "20260822100001",
        "sentence_index": 1,
        "sentence_source": "The quick brown fox jumps over the lazy dog.",
        "sentence_destination": "Быстрая коричневая лиса прыгает через ленивую собаку.",
    }]
    words = []
    db.save_session_bundle(session_record, sentences, words)

    # 1. Exact match with checksum strategy
    res_chk = db.find_sentence_by_strategy(
        "The quick brown fox jumps over the lazy dog.",
        language="en",
        target_language="ru",
        strategy="checksum",
        allow_fallback=False,
    )
    assert len(res_chk) == 1
    assert res_chk[0]["sentence_destination"] == "Быстрая коричневая лиса прыгает через ленивую собаку."

    # 2. Mismatched format with checksum (allow_fallback=False) -> miss
    res_miss = db.find_sentence_by_strategy(
        "# The quick  brown fox jumps over the lazy dog.",
        language="en",
        target_language="ru",
        strategy="checksum",
        allow_fallback=False,
    )
    assert len(res_miss) == 0

    # 3. Mismatched format with checksum (allow_fallback=True) -> fallback to normalized match
    res_fb = db.find_sentence_by_strategy(
        "# The quick  brown fox jumps over the lazy dog.",
        language="en",
        target_language="ru",
        strategy="checksum",
        allow_fallback=True,
    )
    assert len(res_fb) == 1
    assert res_fb[0]["sentence_destination"] == "Быстрая коричневая лиса прыгает через ленивую собаку."


def test_db_find_sentence_by_strategy_normalized_and_contextual(tmp_path):
    db_path = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    session_record = {
        "zid": "20260822100002",
        "slug": "quotes-test",
        "source_language": "en",
        "target_language": "ru",
        "text_mode": "single",
        "source_raw_text": "He said, “It's fine.”",
    }
    sentences = [{
        "session_zid": "20260822100002",
        "sentence_index": 1,
        "sentence_source": "He said, “It's fine.”",
        "sentence_destination": "Он сказал: «Все в порядке.»",
    }]
    db.save_session_bundle(session_record, sentences, [])

    # Normalized match across quote style differences
    res_norm = db.find_sentence_by_strategy(
        "He said, \"It’s fine.\"",
        language="en",
        strategy="normalized",
    )
    assert len(res_norm) == 1
    assert res_norm[0]["sentence_destination"] == "Он сказал: «Все в порядке.»"

    # Contextual containment match
    res_ctx = db.find_sentence_by_strategy(
        "It's fine",
        language="en",
        strategy="contextual",
    )
    assert len(res_ctx) == 1

    # None strategy returns empty
    res_none = db.find_sentence_by_strategy(
        "He said, “It's fine.”",
        language="en",
        strategy="none",
    )
    assert len(res_none) == 0


def test_config_loader_lookup_section(tmp_path):
    ini_content = """
[lookup]
sentence_match_strategy = checksum
allow_checksum_fallback = false
"""
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text(ini_content, encoding="utf-8")

    config = configparser.ConfigParser()
    config.read(cfg_file, encoding="utf-8")

    assert config.has_section(SEC_LOOKUP)
    assert config.get(SEC_LOOKUP, "sentence_match_strategy") == "checksum"
    assert config.getboolean(SEC_LOOKUP, "allow_checksum_fallback") is False


def test_run_lookup_flow_sentence_strategy_reuse(tmp_path, monkeypatch):
    db_path = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    session_record = {
        "zid": "20260822100003",
        "slug": "cached-sentence",
        "source_language": "en",
        "target_language": "ru",
        "text_mode": "single",
        "source_raw_text": "Hello world.",
    }
    sentences = [{
        "session_zid": "20260822100003",
        "sentence_index": 1,
        "sentence_source": "Hello world.",
        "sentence_destination": "Привет мир.",
    }]
    db.save_session_bundle(session_record, sentences, [])

    config = configparser.ConfigParser()
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_path))
    config.add_section("lookup")
    config.set("lookup", "sentence_match_strategy", "normalized")
    config.set("lookup", "allow_checksum_fallback", "true")
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "google")
    config.set("pipeline", "lemma_base_provider", "google")
    config.add_section("languages")
    config.set("languages", "en_prompt", "English Vocab")

    resolved_paths = {
        "storage_backend": "sqlite",
        "sqlite_db_path": db_path,
        "kardenwort_workspace": tmp_path,
        "anki_mapping_file": tmp_path / "anki-mapping.ini",
    }

    # Create dummy mapping file
    (tmp_path / "anki-mapping.ini").write_text("[fields]\nWordSource=1\nSentenceSource=2\nSentenceDestination=3\n[fields_mapping.word]\nsentence_index=SentenceSourceIndex\n[fields_mapping.sentence]\nsource_sentence=SentenceSource\ndestination_sentence=SentenceDestination\n[desk_columns]\n", encoding="utf-8")

    goldendict = {
        "lookup_ttl_seconds": 300,
        "run_intellifiller": False,
        "format": "html",
        "sentence_match_strategy": "normalized",
        "allow_checksum_fallback": True,
    }

    # Mock external translation to verify it is NOT called when matching sentence is found
    translate_called = False
    def failing_translate(*args, **kwargs):
        nonlocal translate_called
        translate_called = True
        return {0: "External Translation Call"}
    monkeypatch.setattr(desk, "translate_source_text", failing_translate)

    # Mock prepare_lookup_tsv to create rows
    def mock_prepare_lookup_tsv(*args, **kwargs):
        tsv_path = tmp_path / "20260822100004-hello-world.en.tsv"
        tsv_path.write_text("# Comment\nSentenceSourceIndex\tSentenceSource\tSentenceDestination\n1\tHello world.\t\n", encoding="utf-8")
        return tsv_path
    monkeypatch.setattr(desk, "prepare_lookup_tsv", mock_prepare_lookup_tsv)

    res = desk.run_lookup_flow(
        text="# Hello   world.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid="20260822100004",
        sentence_match_strategy="normalized",
    )

    # Reused database translation without calling external translation API
    assert res[3] == "Привет мир."
    assert not translate_called


def test_run_lookup_flow_no_checksum_bypass(tmp_path, monkeypatch):
    db_path = tmp_path / "kardenwort.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    session_record = {
        "zid": "20260822100005",
        "slug": "bypass-test",
        "source_language": "en",
        "target_language": "ru",
        "text_mode": "single",
        "source_raw_text": "Bypass sentence.",
    }
    sentences = [{
        "session_zid": "20260822100005",
        "sentence_index": 1,
        "sentence_source": "Bypass sentence.",
        "sentence_destination": "Старый перевод.",
    }]
    db.save_session_bundle(session_record, sentences, [])

    config = configparser.ConfigParser()
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_db_path", str(db_path))
    config.add_section("lookup")
    config.set("lookup", "sentence_match_strategy", "normalized")
    config.set("lookup", "allow_checksum_fallback", "true")
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "google")
    config.set("pipeline", "lemma_base_provider", "google")
    config.add_section("languages")
    config.set("languages", "en_prompt", "English Vocab")

    resolved_paths = {
        "storage_backend": "sqlite",
        "sqlite_db_path": db_path,
        "kardenwort_workspace": tmp_path,
        "anki_mapping_file": tmp_path / "anki-mapping.ini",
    }

    (tmp_path / "anki-mapping.ini").write_text("[fields]\nWordSource=1\nSentenceSource=2\nSentenceDestination=3\n[fields_mapping.word]\nsentence_index=SentenceSourceIndex\n[fields_mapping.sentence]\nsource_sentence=SentenceSource\ndestination_sentence=SentenceDestination\n[desk_columns]\n", encoding="utf-8")

    goldendict = {
        "lookup_ttl_seconds": 300,
        "run_intellifiller": False,
        "format": "html",
    }

    def mock_prepare_lookup_tsv(*args, **kwargs):
        tsv_path = tmp_path / "20260822100006-bypass.en.tsv"
        tsv_path.write_text("# Comment\nSentenceSourceIndex\tSentenceSource\tSentenceDestination\n1\tBypass sentence.\t\n", encoding="utf-8")
        return tsv_path
    monkeypatch.setattr(desk, "prepare_lookup_tsv", mock_prepare_lookup_tsv)

    # When no_checksum_lookup=True, translate_source_text MUST be called
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: {0: "Свежий перевод."})

    res = desk.run_lookup_flow(
        text="Bypass sentence.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid="20260822100006",
        no_checksum_lookup=True,
    )

    assert res[3] == "Свежий перевод."
