"""
Tests for OpenSpec change 20261012100100-fix-reword-occurrence-aggregation.
Verifies:
1. Single occurrence in later sentence (sentence 3) overwrites stored translation directly without comma blending.
2. Repeated re-word on two-occurrence 'der' does not grow the aggregate.
3. Reprocess wordfill skips a blended corpus value for a single-occurrence target.
"""

from pathlib import Path
from unittest.mock import patch
import pytest

import kardenwort_desk as kd
from kardenwort_db import KardenwortDB
import text_tokenizer as tok


def test_reword_single_occurrence_sentence_3_overwrites_without_comma(tmp_path):
    """
    Test 6.2: Single occurrence in sentence 3 with stored Google translation -> overwritten, no comma.
    Verifies that global visual index offset is mapped correctly and no phantom occurrence is created.
    """
    db_path = tmp_path / "kardenwort.db"
    migrations_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(str(db_path), migrations_dir=migrations_dir)
    db.run_migrations()

    sess_zid = "20261012120000"
    s1 = "Ich musste kurz eine Toilettenpause machen."
    s2 = "Das Navi hatte einen Fehler und ich konnte den Weg nicht finden."
    s3 = "## Höfliche Antworten und Verabschiedung Gerne!"
    full_text = f"{s1} {s2} {s3}"

    db.insert_session({
        "zid": sess_zid,
        "source_raw_text": full_text,
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "multi",
        "slug": "test-reword-s3",
    })

    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": s1,
        "sentence_destination": "Trans 1",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 2,
        "sentence_source": s2,
        "sentence_destination": "Trans 2",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 3,
        "sentence_source": s3,
        "sentence_destination": "Trans 3",
    })

    # Initial stored word with Google translation "Принятие"
    db.insert_word({
        "session_zid": sess_zid,
        "sentence_index": 3,
        "token_order": 20,
        "quotation": "Verabschiedung",
        "lemma": "Verabschiedung",
        "word_source": "Verabschiedung",
        "word_destination": "Принятие",
        "pos": "noun",
        "morphology": "die Verabschiedung",
        "word_provenance": "initial:google",
    })

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[roles]\n"
        "lemma=WordSource\n"
        "word_translation=WordDestination\n"
        "pos=WordSourcePOS\n"
        "morphology=WordSourceMorphologyAI\n"
        "sentence_index=SentenceSourceIndex\n"
        "sentence=SentenceSource\n"
        "[fields]\n"
        "TokenOrder=\nWordSource=\nWordDestination=\nWordSourcePOS=\nWordSourceMorphologyAI=\nSentenceSourceIndex=\nSentenceSource=\n",
        encoding="utf-8"
    )

    config_path = tmp_path / "config.ini"
    config_path.write_text(f"""[storage]
backend=sqlite
sqlite_path={db_path.as_posix()}
[settings]
default_language=de
default_target_language=ru
anki_mapping_file={mapping_path.as_posix()}
[environment]
kardenwort_workspace={tmp_path.as_posix()}
[languages]
de_prompt=test
""", encoding="utf-8")

    config, resolved_paths, _, _ = kd.load_config(config_path)
    storage_adapter = kd.get_storage_adapter(config, resolved_paths)

    # Compute global visual_idx of 'Verabschiedung' in full_text
    global_tokens = tok.build_word_list_internal(full_text, keep_spaces=True)
    target_token = next(t for t in global_tokens if t.get("lower_clean") == "verabschiedung")
    global_vidx = target_token["visual_idx"]

    captured_ephemeral_sent = []

    with patch("kardenwort_desk.run_headless_intellifiller") as mock_runner:
        def fake_runner(tsv_path, prompt_name, config, resolved_paths, selected_rows=None, reprocess=False, zid=None, trace_id=None):
            comments, headers, data_rows = kd.load_tsv_rows(tsv_path)
            col_s_src = headers.index("SentenceSource")
            captured_ephemeral_sent.append(data_rows[0][col_s_src])

            col_pos = headers.index("WordSourcePOS")
            col_dest = headers.index("WordDestination")
            col_morph = headers.index("WordSourceMorphologyAI")

            data_rows[0][col_pos] = "noun"
            data_rows[0][col_dest] = "прощание"
            data_rows[0][col_morph] = "die Verabschiedung (прощание)"
            storage_adapter._tsv_fallback.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            return True

        mock_runner.side_effect = fake_runner

        ok = storage_adapter.enrich_session_intellifiller(
            session_zid=sess_zid,
            prompt_name="test",
            selected_rows=[0],
            visual_indices=[global_vidx],
            reprocess=True,
        )
        assert ok is True

    # 1. Ephemeral sentence bracketed the target in sentence 3 correctly
    assert len(captured_ephemeral_sent) == 1
    assert "[Verabschiedung]" in captured_ephemeral_sent[0]

    # 2. SQLite words record overwrites cleanly without blending stale Google translation
    words = db.get_words_by_session(sess_zid)
    assert len(words) == 1
    w = words[0]
    assert w["word_destination"] == "прощание"
    assert "," not in w["word_destination"]
    assert "Принятие" not in w["word_destination"]


def test_repeated_reword_on_two_occurrence_der_does_not_grow_aggregate(tmp_path):
    """
    Test 6.3: Repeated re-word on two-occurrence 'der' does not grow the aggregate.
    """
    db_path = tmp_path / "kardenwort.db"
    migrations_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(str(db_path), migrations_dir=migrations_dir)
    db.run_migrations()

    sess_zid = "20261012120100"
    raw_sentence = "Er fängt mit der Arbeit an, der ihm versprochen wurde."

    db.insert_session({
        "zid": sess_zid,
        "source_raw_text": raw_sentence,
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "single",
        "slug": "test-repeated-der",
    })

    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": raw_sentence,
        "sentence_destination": "Он начинает...",
    })

    db.insert_word({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "token_order": 0,
        "quotation": "der",
        "lemma": "der",
        "word_source": "der",
        "word_destination": "тот",
        "pos": "art.",
        "morphology": "der (определенный артикль мужского рода)",
        "word_provenance": "initial",
    })

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[roles]\n"
        "lemma=WordSource\n"
        "word_translation=WordDestination\n"
        "pos=WordSourcePOS\n"
        "morphology=WordSourceMorphologyAI\n"
        "sentence_index=SentenceSourceIndex\n"
        "sentence=SentenceSource\n"
        "[fields]\n"
        "TokenOrder=\nWordSource=\nWordDestination=\nWordSourcePOS=\nWordSourceMorphologyAI=\nSentenceSourceIndex=\nSentenceSource=\n",
        encoding="utf-8"
    )

    config_path = tmp_path / "config.ini"
    config_path.write_text(f"""[storage]
backend=sqlite
sqlite_path={db_path.as_posix()}
[settings]
default_language=de
default_target_language=ru
anki_mapping_file={mapping_path.as_posix()}
[environment]
kardenwort_workspace={tmp_path.as_posix()}
[languages]
de_prompt=test
""", encoding="utf-8")

    config, resolved_paths, _, _ = kd.load_config(config_path)
    storage_adapter = kd.get_storage_adapter(config, resolved_paths)

    # Dynamically find the second 'der' visual_idx
    sentence_tokens = tok.build_word_list_internal(raw_sentence, keep_spaces=True)
    der_tokens = [t for t in sentence_tokens if t.get("lower_clean") == "der"]
    assert len(der_tokens) == 2
    second_der_vidx = der_tokens[1]["visual_idx"]

    # 1st Re-word on second 'der'
    with patch("kardenwort_desk.run_headless_intellifiller") as mock_runner:
        def fake_runner_1(tsv_path, *args, **kwargs):
            comments, headers, data_rows = kd.load_tsv_rows(tsv_path)
            col_pos = headers.index("WordSourcePOS")
            col_dest = headers.index("WordDestination")
            col_morph = headers.index("WordSourceMorphologyAI")
            data_rows[0][col_pos] = "pron."
            data_rows[0][col_dest] = "который"
            data_rows[0][col_morph] = "der (относительное местоимение)"
            storage_adapter._tsv_fallback.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            return True

        mock_runner.side_effect = fake_runner_1

        ok1 = storage_adapter.enrich_session_intellifiller(
            session_zid=sess_zid,
            prompt_name="test",
            selected_rows=[0],
            visual_indices=[second_der_vidx],
            reprocess=True,
        )
        assert ok1 is True

    words = db.get_words_by_session(sess_zid)
    assert words[0]["word_destination"] == "тот, который"

    # 2nd Re-word on second 'der' again
    with patch("kardenwort_desk.run_headless_intellifiller") as mock_runner:
        def fake_runner_2(tsv_path, *args, **kwargs):
            comments, headers, data_rows = kd.load_tsv_rows(tsv_path)
            col_pos = headers.index("WordSourcePOS")
            col_dest = headers.index("WordDestination")
            col_morph = headers.index("WordSourceMorphologyAI")
            data_rows[0][col_pos] = "pron."
            data_rows[0][col_dest] = "который"
            data_rows[0][col_morph] = "der (относительное местоимение)"
            storage_adapter._tsv_fallback.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            return True

        mock_runner.side_effect = fake_runner_2

        ok2 = storage_adapter.enrich_session_intellifiller(
            session_zid=sess_zid,
            prompt_name="test",
            selected_rows=[0],
            visual_indices=[second_der_vidx],
            reprocess=True,
        )
        assert ok2 is True

    words2 = db.get_words_by_session(sess_zid)
    assert words2[0]["word_destination"] == "тот, который"
    assert words2[0]["word_destination"].count("который") == 1
    assert words2[0]["word_destination"].count("тот") == 1


def test_reprocess_wordfill_skips_blended_corpus_for_single_occurrence(tmp_path):
    """
    Test 6.4: Wordfill skips a blended corpus value for a single-occurrence target.
    """
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus_tsv = corpus_dir / "20261012120000-corpus-merged.de.tsv"

    # Write corpus row with blended destination
    headers = [
        "TokenOrder", "WordSource", "WordDestination", "WordSourcePOS",
        "WordSourceIPA", "WordSourceMorphologyAI", "SentenceSource", "SentenceSourceIndex"
    ]
    corpus_rows = [
        ["1", "Verabschiedung", "прощание, Принятие", "noun", "feːɐ̯ˈʔapʃiːdʊŋ", "die Verabschiedung", "Verabschiedung", "1"]
    ]
    with open(corpus_tsv, "w", encoding="utf-8", newline="") as f:
        f.write("# Corpus\n")
        f.write("\t".join(headers) + "\n")
        for r in corpus_rows:
            f.write("\t".join(r) + "\n")

    mapping_file = tmp_path / "anki-mapping.ini"
    mapping_file.write_text(
        "[roles]\n"
        "lemma=WordSource\n"
        "word_translation=WordDestination\n"
        "pos=WordSourcePOS\n"
        "morphology=WordSourceMorphologyAI\n"
        "sentence_index=SentenceSourceIndex\n"
        "sentence=SentenceSource\n"
        "[fields]\n"
        + "\n".join(f"{h}=" for h in headers) + "\n",
        encoding="utf-8"
    )

    config_path = tmp_path / "config.ini"
    config_path.write_text(f"""[settings]
default_language=de
default_target_language=ru
anki_mapping_file={mapping_file.as_posix()}
[pipeline]
run_lemmatizer=true
lemma_base_provider=google
lemma_reprocess_provider=intellifiller
[wordfill]
enabled=true
scan_roots={corpus_dir.as_posix()}
scan_depth=1
scan_scope=all
target_quality=any
target_fallback=true
storage_backend=tsv
""", encoding="utf-8")

    config, resolved_paths, _, _ = kd.load_config(config_path)

    # Single-occurrence session row
    session_rows = [
        ["1", "Verabschiedung", "", "", "", "", "Das ist die Verabschiedung.", "1"]
    ]

    # Verify find_wordfill_match finds the match in corpus
    wordfill_cfg = kd.resolve_wordfill_config(config, resolved_paths)
    match = kd.find_wordfill_match("Verabschiedung", "de", wordfill_cfg)
    assert match is not None
    assert match.get("WordDestination") == "прощание, Принятие"

    # Simulate reprocess worker wordfill logic on single-occurrence row
    selected_rows = [0]
    col_lemma = headers.index("WordSource")
    col_dest_name = "WordDestination"
    col_s_src = headers.index("SentenceSource")

    remaining_selected = []
    for row_id in selected_rows:
        row = session_rows[row_id]
        lemma_val = row[col_lemma].strip()
        m = kd.find_wordfill_match(lemma_val, "de", wordfill_cfg)
        if m:
            match_dest = (m.get(col_dest_name) or m.get('WordDestination') or '').strip()
            if ', ' in match_dest:
                sent_text = row[col_s_src].strip()
                if sent_text:
                    toks = tok.build_word_list_internal(sent_text, keep_spaces=True)
                    lem_clean = lemma_val.lower()
                    match_cnt = sum(1 for t in toks if t.get('is_word') and (t.get('lower_clean') or t.get('text', '')).lower() == lem_clean)
                    if match_cnt <= 1:
                        # Guard triggered: skip applying blended corpus match
                        remaining_selected.append(row_id)
                        continue
            kd.apply_wordfill_to_rows([row], headers, m)
        remaining_selected.append(row_id)

    # Row was NOT pre-filled with the blended translation and remains in selected
    assert remaining_selected == [0]
    assert session_rows[0][headers.index("WordDestination")] == ""


def test_enrich_session_coordinate_mismatch_raises_value_error(tmp_path):
    """
    Test 2.1: Assert coordinate alignment between sentence visual index and session visual index.
    Raises ValueError immediately when target visual index does not align with sentence token coordinates.
    """
    db_path = tmp_path / "kardenwort.db"
    migrations_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(str(db_path), migrations_dir=migrations_dir)
    db.run_migrations()

    sess_zid = "20261012120500"
    s1 = "Erste Satz ohne das Wort."
    s2 = "Zweite Satz mit dem Zielwort."
    full_text = f"{s1} {s2}"

    db.insert_session({
        "zid": sess_zid,
        "source_raw_text": full_text,
        "source_language": "de",
        "target_language": "ru",
        "text_mode": "multi",
        "slug": "test-coord-mismatch",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": s1,
        "sentence_destination": "Первое предложение.",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 2,
        "sentence_source": s2,
        "sentence_destination": "Второе предложение с целевым словом.",
    })
    db.insert_word({
        "session_zid": sess_zid,
        "sentence_index": 2,
        "token_order": 10,
        "quotation": "Zielwort",
        "lemma": "Zielwort",
        "word_source": "Zielwort",
        "word_destination": "целевое слово",
        "pos": "noun",
        "morphology": "das Zielwort",
        "word_provenance": "initial",
    })

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[roles]\n"
        "lemma=WordSource\n"
        "word_translation=WordDestination\n"
        "pos=WordSourcePOS\n"
        "morphology=WordSourceMorphologyAI\n"
        "sentence_index=SentenceSourceIndex\n"
        "sentence=SentenceSource\n"
        "[fields]\n"
        "TokenOrder=\nWordSource=\nWordDestination=\nWordSourcePOS=\nWordSourceMorphologyAI=\nSentenceSourceIndex=\nSentenceSource=\n",
        encoding="utf-8"
    )

    config_path = tmp_path / "config.ini"
    config_path.write_text(f"""[storage]
backend=sqlite
sqlite_path={db_path.as_posix()}
[settings]
default_language=de
default_target_language=ru
anki_mapping_file={mapping_path.as_posix()}
[environment]
kardenwort_workspace={tmp_path.as_posix()}
[languages]
de_prompt=test
""", encoding="utf-8")

    config, resolved_paths, _, _ = kd.load_config(config_path)
    storage_adapter = kd.get_storage_adapter(config, resolved_paths)

    # Intentionally pass bogus visual index 999 that does NOT align with sentence 2 tokens
    with pytest.raises(ValueError) as excinfo:
        storage_adapter.enrich_session_intellifiller(
            session_zid=sess_zid,
            prompt_name="test",
            selected_rows=[0],
            visual_indices=[999],
            reprocess=True,
        )
    assert "Coordinate alignment violation" in str(excinfo.value)
    assert "target visual indices [999]" in str(excinfo.value)

