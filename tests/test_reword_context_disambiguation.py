from kardenwort_desk import (
    build_bracketed_sentence_context,
    sanitize_bracketed_field,
    aggregate_sequential_distinct,
)


def test_build_bracketed_sentence_context_by_visual_idx():
    sentence = "Er fängt heute mit der Arbeit an, weil ihm das Projekt gefällt. Er soll den Erfolg bringen, der ihm versprochen wurde."
    
    # Target second 'der' (visual_idx = 38)
    bracketed = build_bracketed_sentence_context(sentence, target_visual_idx=38)
    assert "[der] ihm versprochen wurde" in bracketed
    assert "mit der Arbeit" in bracketed
    assert "mit [der] Arbeit" not in bracketed

    # Target first 'der' (visual_idx = 9)
    bracketed_first = build_bracketed_sentence_context(sentence, target_visual_idx=9)
    assert "mit [der] Arbeit" in bracketed_first
    assert "bringen, der ihm" in bracketed_first
    assert "bringen, [der] ihm" not in bracketed_first


def test_build_bracketed_sentence_context_by_word_and_occurrence():
    sentence = "Der Mann sah den Hund, der bellte."
    
    # Target second occurrence of 'der' (index 1)
    b1 = build_bracketed_sentence_context(sentence, target_word="der", target_occurrence=1)
    assert "Hund, [der] bellte" in b1
    assert "Der Mann" in b1
    assert "[Der] Mann" not in b1

    # Target first occurrence of 'der' (index 0)
    b0 = build_bracketed_sentence_context(sentence, target_word="der", target_occurrence=0)
    assert "[Der] Mann" in b0
    assert "Hund, der bellte" in b0


def test_sanitize_bracketed_field():
    assert sanitize_bracketed_field("[der]") == "der"
    assert sanitize_bracketed_field(" [который] ") == "который"
    assert sanitize_bracketed_field("der") == "der"
    assert sanitize_bracketed_field("") == ""
    assert sanitize_bracketed_field(None) == ""
    assert sanitize_bracketed_field("[complex [nested] text]") == "complex nested text"


def test_aggregate_sequential_distinct_pos():
    # Sequential order 9 then 38
    pos_items = [(9, "art."), (38, "pron.")]
    assert aggregate_sequential_distinct(pos_items) == "art., pron."

    # Reverse input order still sorts by sentence position key
    pos_items_rev = [(38, "pron."), (9, "art.")]
    assert aggregate_sequential_distinct(pos_items_rev) == "art., pron."

    # Identical POS deduplication
    pos_identical = [(9, "art."), (38, "art.")]
    assert aggregate_sequential_distinct(pos_identical) == "art."

    # Comma-separated elements inside values
    pos_sub = [(9, "art., det."), (38, "pron.")]
    assert aggregate_sequential_distinct(pos_sub) == "art., det., pron."


def test_aggregate_sequential_distinct_translations():
    # Distinct translations in order of appearance
    trans_items = [(9, "тот"), (38, "который")]
    assert aggregate_sequential_distinct(trans_items) == "тот, который"

    # Deduplicated translations
    trans_dup = [(9, "тот"), (38, "тот")]
    assert aggregate_sequential_distinct(trans_dup) == "тот"

    # Reverse input sorted by key
    trans_rev = [(38, "который"), (9, "тот")]
    assert aggregate_sequential_distinct(trans_rev) == "тот, который"


def test_aggregate_sequential_distinct_morphology():
    morph_items = [
        (9, "der (определенный артикль мужского рода)"),
        (38, "der (относительное местоимение мужского рода)")
    ]
    res = aggregate_sequential_distinct(morph_items, delimiter="; ")
    assert "определенный артикль" in res
    assert "относительное местоимение" in res
    assert "; " in res


def test_render_html_no_brackets_in_presentation(tmp_path):
    import kardenwort_desk
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    raw_sentence = "Er fängt heute mit der Arbeit an, weil ihm das Projekt gefällt. Er soll den Erfolg bringen, der ihm versprochen wurde."
    tsv_file = tmp_path / "20261003180000-test.de.tsv"
    tsv_content = (
        "Quotation\tWordSource\tWordDestination\tWordSourcePOS\tWordSourceMorphologyAI\tSentenceSourceIndex\n"
        "der\tder\tтот, который\tart., pron.\tder (артикль); der (местоимение)\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")
    
    html = kardenwort_desk.run_render_flow(
        text=raw_sentence,
        language="de",
        zid="20261003180000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=False,
        return_children=False
    )
    
    # Presentation must NOT contain leaked square brackets around target words
    assert "[der]" not in html
    assert "[который]" not in html
    assert "тот, который" in html
    assert "art., pron." in html


def test_sqlite_reword_disambiguation_and_aggregation(tmp_path):
    from pathlib import Path
    from unittest.mock import patch
    from kardenwort_db import KardenwortDB
    import kardenwort_desk

    db_path = tmp_path / "kardenwort.db"
    migrations_dir = Path(__file__).resolve().parent.parent / "schemas" / "migrations"
    db = KardenwortDB(str(db_path), migrations_dir=migrations_dir)
    db.run_migrations()
    
    sess_zid = "20261003183000"
    raw_sentence = "Er fängt heute mit der Arbeit an, weil ihm das Projekt gefällt. Er soll den Erfolg bringen, der ihm versprochen wurde."
    db.insert_session({
        "zid": sess_zid,
        "source_language": "de",
        "target_language": "ru",
        "raw_text": raw_sentence,
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
        "[roles]\nlemma=WordSource\nword_translation=WordDestination\npos=WordSourcePOS\nmorphology=WordSourceMorphologyAI\nsentence_index=SentenceSourceIndex\nsentence=SentenceSource\n"
        "[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nWordSourcePOS=\nWordSourceMorphologyAI=\nSentenceSourceIndex=\nSentenceSource=\n",
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

    config, resolved_paths, _, _ = kardenwort_desk.load_config(config_path)
    storage_adapter = kardenwort_desk.get_storage_adapter(config, resolved_paths)

    captured_ephemeral_sent = []

    with patch("kardenwort_desk.run_headless_intellifiller") as mock_runner:
        def fake_runner(tsv_path, prompt_name, config, resolved_paths, selected_rows=None, reprocess=False, zid=None, trace_id=None):
            comments, headers, data_rows = kardenwort_desk.load_tsv_rows(tsv_path)
            col_s_src = headers.index("SentenceSource")
            captured_ephemeral_sent.append(data_rows[0][col_s_src])
            
            col_pos = headers.index("WordSourcePOS")
            col_dest = headers.index("WordDestination")
            col_morph = headers.index("WordSourceMorphologyAI")
            
            data_rows[0][col_pos] = "pron."
            data_rows[0][col_dest] = "который"
            data_rows[0][col_morph] = "der (относительное местоимение)"
            storage_adapter._tsv_fallback.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            return True
            
        mock_runner.side_effect = fake_runner

        # Target the second occurrence of 'der' (visual_idx = 38)
        ok = storage_adapter.enrich_session_intellifiller(
            session_zid=sess_zid,
            prompt_name="test",
            selected_rows=[0],
            visual_indices=[38],
            reprocess=True,
        )
        assert ok is True

    # 1. Ephemeral sentence passed to IntelliFiller had the target in brackets
    assert len(captured_ephemeral_sent) == 1
    assert "[der] ihm versprochen wurde" in captured_ephemeral_sent[0]
    assert "mit der Arbeit" in captured_ephemeral_sent[0]

    # 2. SQLite words record aggregated the constituent roles in sentence appearance order
    words = db.get_words_by_session(sess_zid)
    assert len(words) == 1
    w = words[0]
    assert w["pos"] == "art., pron."
    assert w["word_destination"] == "тот, который"
    assert "определенный артикль" in w["morphology"]
    assert "относительное местоимение" in w["morphology"]

    # 3. Persistent sentence in SQLite remains completely clean of square brackets
    sentences = db.get_sentences_by_session(sess_zid)
    assert "[" not in sentences[0]["sentence_source"]
    assert "]" not in sentences[0]["sentence_source"]

