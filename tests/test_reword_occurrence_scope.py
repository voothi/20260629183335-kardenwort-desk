from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest
from kardenwort_desk import SqliteStorageAdapter


def test_reword_scopes_row_to_targeted_occurrence(tmp_path):
    """
    When re-wording a specific occurrence (e.g. visual_idx 38 as pron.),
    earlier recorded occurrences in occurrence_data (e.g. visual_idx 9 as art.)
    must NOT cause the row-level POS to be aggregated into 'art., pron.'.
    The row should reflect the currently re-worded token (pron.).
    """
    from kardenwort_db import KardenwortDB, sanitize_extra_fields

    import json

    db_path = tmp_path / "test.db"
    db = KardenwortDB(db_path)
    db.run_migrations()
    session_zid = "20261003233000"

    db.insert_session({
        "zid": session_zid,
        "slug": "test-slug",
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "word"
    })
    db.insert_sentences([
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Er fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."}
    ])
    db.insert_words([{
        "session_zid": session_zid,
        "sentence_index": 1,
        "token_order": 0,
        "quotation": "das, den, der, die",
        "inflected_form": "das, den, der, die",
        "lemma": "der",
        "pos": "art.",
        "word_destination": "тот",
        "morphology": "der (определенный артикль)",
        "extra_fields": json.dumps({
            "occurrence_data": {
                "9": {
                    "pos": "art.",
                    "trans": "этот",
                    "morph": "der (определенный артикль, женский род, дательный падеж)"
                }
            }
        })
    }])

    from kardenwort_desk import SqliteStorageAdapter

    adapter = SqliteStorageAdapter(config=None, resolved_paths=None, db_path=db_path)
    # Mock restore_session and headless intellifiller
    def fake_restore(zid):
        words = db.get_words_by_session(zid)
        return {
            "session": {"source_language": "de", "target_language": "ru"},
            "comments": [],
            "headers": ["TokenOrder", "SentenceSourceIndex", "WordSource", "WordSourceInflectedForm", "WordSourcePOS", "WordDestination", "WordSourceMorphologyAI"],
            "data_rows": [["0", "1", "der", "das, den, der, die", "art.", "тот", "der (определенный артикль)"]],
            "words": words,
            "source_language": "de"
        }

    adapter.restore_session = fake_restore

    with patch("kardenwort_desk.run_headless_intellifiller") as mock_filler, \
         patch("kardenwort_desk.load_tsv_rows") as mock_load:
        mock_filler.return_value = True
        # Intellifiller returns 'pron.' and 'который' for target index 38
        mock_load.return_value = (
            [],
            ["TokenOrder", "SentenceSourceIndex", "WordSource", "WordSourceInflectedForm", "WordSourcePOS", "WordDestination", "WordSourceMorphologyAI"],
            [["0", "1", "der", "das, den, der, die", "pron.", "который", "der (корень: относительное местоимение)"]]
        )

        res = adapter.enrich_session_intellifiller(
            session_zid=session_zid,
            prompt_name="default",
            token_orders=["0"],
            visual_indices=[38]
        )

        assert res is True

        words = db.get_words_by_session(session_zid)
        der_word = words[0]

        # The row-level POS must be 'pron.', NOT aggregated 'art., pron.'!
        assert der_word["pos"] == "pron."
        assert der_word["word_destination"] == "который"

        # But occurrence_data must contain both occurrences 9 and 38!
        ef = sanitize_extra_fields(der_word["extra_fields"])
        occ = ef.get("occurrence_data", {})
        assert "9" in occ
        assert occ["9"]["pos"] == "art."
        assert "38" in occ
        assert occ["38"]["pos"] == "pron."


def test_reword_multiple_occurrences_aggregates_pos(tmp_path):
    """
    When multiple distinct occurrences are targeted in the same re-word
    (e.g. visual_indices [9, 38] with 'art.' and 'pron.'), the row-level POS
    is aggregated into 'art., pron.'.
    """
    from kardenwort_db import KardenwortDB, sanitize_extra_fields
    import json

    db_path = tmp_path / "test2.db"
    db = KardenwortDB(db_path)
    db.run_migrations()
    session_zid = "20261003234000"

    db.insert_session({
        "zid": session_zid,
        "slug": "test-slug",
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "word"
    })
    db.insert_sentences([
        {"session_zid": session_zid, "sentence_index": 1, "sentence_source": "Er fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."}
    ])
    db.insert_words([{
        "session_zid": session_zid,
        "sentence_index": 1,
        "token_order": 0,
        "quotation": "das, den, der, die",
        "inflected_form": "das, den, der, die",
        "lemma": "der",
        "pos": "art.",
        "word_destination": "тот",
        "morphology": "der (определенный артикль)",
        "extra_fields": "{}"
    }])

    from kardenwort_desk import SqliteStorageAdapter

    adapter = SqliteStorageAdapter(config=None, resolved_paths=None, db_path=db_path)

    def fake_restore(zid):
        words = db.get_words_by_session(zid)
        return {
            "session": {"source_language": "de", "target_language": "ru"},
            "comments": [],
            "headers": ["TokenOrder", "SentenceSourceIndex", "WordSource", "WordSourceInflectedForm", "WordSourcePOS", "WordDestination", "WordSourceMorphologyAI"],
            "data_rows": [["0", "1", "der", "das, den, der, die", "art.", "тот", "der (определенный артикль)"]],
            "words": words,
            "source_language": "de"
        }

    adapter.restore_session = fake_restore

    with patch("kardenwort_desk.run_headless_intellifiller") as mock_filler, \
         patch("kardenwort_desk.load_tsv_rows") as mock_load:
        mock_filler.return_value = True
        mock_load.return_value = (
            [],
            ["TokenOrder", "SentenceSourceIndex", "WordSource", "WordSourceInflectedForm", "WordSourcePOS", "WordDestination", "WordSourceMorphologyAI"],
            [["0", "1", "der", "das, den, der, die", "art., pron.", "тот, который", "der (артикль); der (местоимение)"]]
        )

        res = adapter.enrich_session_intellifiller(
            session_zid=session_zid,
            prompt_name="default",
            token_orders=["0"],
            visual_indices=[9, 38]
        )

        assert res is True

        words = db.get_words_by_session(session_zid)
        der_word = words[0]
        assert "art." in der_word["pos"]
        assert "pron." in der_word["pos"]
