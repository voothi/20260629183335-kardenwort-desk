import configparser
from pathlib import Path
import pytest

import kardenwort_desk as desk
from kardenwort_desk import (
    get_storage_adapter,
    SqliteStorageAdapter,
    TsvStorageAdapter,
)
from tests.controller_test_support import controller_world, token_service, DeterministicTranslationHandler
from tests.lookup_engine_support import NounModel, translate_lookup
from tests.test_structured_enrichment import control_writes, prohibited


@pytest.fixture
def lookup_world(controller_world, monkeypatch):
    _, server = controller_world
    config, paths, goldendict = server.config, server.resolved_paths, server.goldendict
    goldendict.update(lookup_ttl_seconds=0, run_intellifiller=False,
                     sentence_match_strategy='none', allow_checksum_fallback=False)
    config.set('wordfill', 'enabled', 'false')
    config.read_dict({'settings': {'anki_context_words_before': '0', 'anki_context_words_after': '0',
        'anki_translated_context_words_before': '0', 'anki_translated_context_words_after': '0'}})
    # Declare lexical selection through real extractor rules, preserving full model context.
    selection = Path(paths['base_dir'])/'noun-selection.tsv'
    selection.write_text('\tregex:(?i:I|like|eat|an|are|yellow|sweet|red|the|quick)\t-\n', encoding='utf-8')
    config.set('languages', 'en_lemma_override', str(selection))
    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', translate_lookup)
    with token_service(NounModel) as tokenizer:
        config.set('services', 'spacy_server_url', f'http://127.0.0.1:{tokenizer.server_port}')
        yield server


def test_db_first_lookup_flow_sqlite_persistence(monkeypatch, lookup_world):
    """
    Verifies that running run_lookup_flow with SQLite storage backend persists
    the session directly into SQLite database tables (sessions, sentences, words)
    without intermediate TSV disk writes.
    """
    server = lookup_world
    config, resolved_paths, goldendict = server.config, server.resolved_paths, server.goldendict
    saved_tsv_calls = []
    original = desk.save_tsv_rows_safely

    def observe(path, comments, headers, rows):
        saved_tsv_calls.append(str(path))
        return original(path, comments, headers, rows)

    monkeypatch.setattr(desk, 'save_tsv_rows_safely', observe)

    test_zid = "20260822100000"
    with control_writes() as writes:
        res = desk.run_lookup_flow(
            text="I like apples. Bananas are yellow.",
            language="en",
            target_lang="ru",
            fmt="html",
            config=config,
            resolved_paths=resolved_paths,
            goldendict=goldendict,
            zid=test_zid,
            text_mode="sentences"
        )

    assert prohibited(writes) == []
    assert server.translation_service.provider_calls

    # In-memory execution: No intermediate save_tsv_rows_safely calls during translation/wordfill stages
    assert len(saved_tsv_calls) == 0

    # Verify directly stored in SQLite DB
    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, SqliteStorageAdapter)
    
    with adapter.db.get_connection(zid=test_zid) as conn:
        session_row = conn.execute("SELECT * FROM sessions WHERE zid = ?", (test_zid,)).fetchone()
        assert session_row is not None
        assert session_row["source_language"] == "en"
        assert session_row["target_language"] == "ru"
        assert session_row["text_mode"] == "sentences"

        sentence_rows = conn.execute("SELECT * FROM sentences WHERE session_zid = ? ORDER BY sentence_index ASC", (test_zid,)).fetchall()
        assert len(sentence_rows) == 2
        assert sentence_rows[0]["sentence_source"] == "I like apples."
        assert sentence_rows[0]["sentence_destination"] == "Я люблю яблоки."
        assert sentence_rows[1]["sentence_source"] == "Bananas are yellow."
        assert sentence_rows[1]["sentence_destination"] == "Бананы желтые."

        word_rows = conn.execute("SELECT * FROM words WHERE session_zid = ? ORDER BY token_order ASC", (test_zid,)).fetchall()
        assert len(word_rows) == 2
        assert word_rows[0]["lemma"] == "apple"
        assert word_rows[0]["word_destination"] == "apple_ru"
        assert word_rows[1]["lemma"] == "banana"
        assert word_rows[1]["word_destination"] == "banana_ru"

    # Verify session restore reconstructs full headers and data
    restored = adapter.restore_session(test_zid)
    assert restored is not None
    assert len(restored["data_rows"]) == 2
    headers_lower = [h.lower() for h in restored["headers"]]
    lemma_col = headers_lower.index("wordsource")
    trans_col = headers_lower.index("worddestination")
    assert restored["data_rows"][0][lemma_col] == "apple"
    assert restored["data_rows"][0][trans_col] == "apple_ru"
    assert restored["data_rows"][1][lemma_col] == "banana"
    assert restored["data_rows"][1][trans_col] == "banana_ru"


@pytest.mark.parametrize('controller_world', ['tsv'], indirect=True)
def test_db_first_lookup_flow_tsv_fallback(lookup_world):
    """
    Verifies that when storage backend is explicitly set to tsv,
    run_lookup_flow works with TsvStorageAdapter without error.
    """
    server = lookup_world
    config, resolved_paths, goldendict = server.config, server.resolved_paths, server.goldendict
    mapping = configparser.ConfigParser(interpolation=None)
    fields = ['WordSource', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource',
              'SentenceDestination', 'TokenOrder']
    mapping.optionxform = str
    mapping.read_dict({'fields': {field: '' for field in fields},
        'desk_columns': {'WordSource': 'lemma', 'WordDestination': 'word_translation',
                        'SentenceSourceIndex': 'sentence_index', 'SentenceSource': 'sentence',
                        'SentenceDestination': 'sentence_destination'},
        'fields_mapping.word': {'WordSource': 'lemma', 'SentenceSource': 'source_sentence',
                                'SentenceSourceIndex': 'sentence_index', 'TokenOrder': 'token_order'}})
    mapping_path = Path(resolved_paths['base_dir'])/'legacy-mapping.ini'
    with mapping_path.open('w', encoding='utf-8') as stream:
        mapping.write(stream)
    resolved_paths['anki_mapping_file'] = mapping_path
    config.set('settings', 'anki_mapping_file', str(mapping_path))

    test_zid = "20260822100001"
    res = desk.run_lookup_flow(
        text="Oranges are sweet.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid=test_zid,
        text_mode="single"
    )

    adapter = get_storage_adapter(config, resolved_paths)
    assert isinstance(adapter, TsvStorageAdapter)
    
    comments, headers, data_rows, sent_trans = res
    assert data_rows[0][0] == "orange"
    assert data_rows[0][1] == "апельсин"


def test_db_first_lookup_flow_export_format(lookup_world):
    """
    Verifies that dynamic TSV export from a session persisted directly to SQLite
    matches expected full column format (82+ columns when standard mapping is used).
    """
    server = lookup_world
    config, resolved_paths, goldendict = server.config, server.resolved_paths, server.goldendict

    test_zid = "20260822100002"
    desk.run_lookup_flow(
        text="Cherries are red.",
        language="en",
        target_lang="ru",
        fmt="html",
        config=config,
        resolved_paths=resolved_paths,
        goldendict=goldendict,
        zid=test_zid,
        text_mode="single"
    )

    adapter = get_storage_adapter(config, resolved_paths)
    restored = adapter.restore_session(test_zid)
    assert restored is not None
    # Verify 82+ standard columns reconstructed
    assert len(restored["headers"]) >= 82
    assert "Quotation" in restored["headers"]
    assert "WordSource" in restored["headers"]
    assert "SentenceSource" in restored["headers"]
    assert "SentenceDestination" in restored["headers"]
    assert len(restored["data_rows"]) == 1
    
    ws_idx = restored["headers"].index("WordSource")
    wd_idx = restored["headers"].index("WordDestination")
    assert restored["data_rows"][0][ws_idx] == "cherry"
    assert restored["data_rows"][0][wd_idx] == "вишня"

