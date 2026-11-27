"""Phase 1 isolated reproductions for PIPE-H1 through PIPE-H14."""
from argparse import Namespace
from pathlib import Path
import json
import re

import pytest

import kardenwort_alignment as alignment
import kardenwort_desk as desk
import kardenwort_enrichment as enrichment
import kardenwort_progressive_completion as progressive
import kardenwort_reword as reword
import kardenwort_translation_validation as tv
from kardenwort_db import KardenwortDB
from tests.controller_test_support import owned_configuration
from tests.sqlite_document_support import island


HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "WordSourcePOS",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "SentenceDestination2",
    "DeskSelected",
]


def _build_sqlite_env(tmp_path: Path, frequency_words=()):
    config, paths, _ = owned_configuration(tmp_path, "", "sqlite")
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        + "\n".join(HEADERS)
        + "\n[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "WordSourceIPA=ipa\n"
        "WordSourceMorphologyAI=morphology\n"
        "WordSourcePOS=pos\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n"
        "SentenceDestination=sentence_destination\n"
        "SentenceDestination2=sentence_destination2\n"
        "DeskSelected=selected\n"
        "[desk_editable]\n"
        "editable_columns=WordDestination\n",
        encoding="utf-8",
    )
    lang_cfg = {"de_prompt": "fixture", "en_prompt": "fixture"}
    if frequency_words:
        freq_file = tmp_path / "en_frequency.txt"
        freq_file.write_text("\n".join(frequency_words) + "\n", encoding="utf-8")
        lang_cfg["en_lemma_index"] = freq_file.name
    config.read_dict({
        "settings": {
            "default_language": "en",
            "default_target_language": "ru",
            "anki_mapping_file": str(mapping_file),
            "context_window_left": "1",
            "context_window_right": "1",
        },
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
        "pipeline": {
            "progressive_text_translation": "false",
        },
        "rendering": {
            "display_mode": "monolithic",
        },
        "multi_sentence": {
            "enabled": "true",
            "delivery_mode": "container",
            "deduplication_scope": "sentence",
        },
        "languages": lang_cfg,
        "classification": {"enabled": "false"},
        "wordfill": {"enabled": "false"},
        "environment": {"kardenwort_workspace": str(tmp_path)},
    })
    paths["anki_mapping_file"] = mapping_file
    paths["kardenwort_workspace"] = tmp_path
    adapter = desk.SqliteStorageAdapter(config, paths)
    return config, paths, adapter


def test_pipe_h1_all_four_classifiers_recognize_translation_error():
    """PIPE-H1: [Translation Error: ...] must be classified as failure across all 4 modules."""
    err = "[Translation Error: Connection refused]"
    assert tv.is_stored_translation_failure(err) is True
    assert tv.is_unfinished_translation(err) is True
    assert alignment.failed_sentence_text(err) is True
    assert progressive._default_failure(err) is True
    assert reword.is_stored_translation_failure(err) is True
    assert reword.is_unfinished_translation(err) is True


def test_pipe_h4_all_bypassed_failure_sites_filter_rate_limit_and_translation_error(tmp_path):
    """PIPE-H4: all 11 bypassed call sites must reject HTTP 429 and [Translation Error: ...]."""
    rate_err = "HTTP 429 Too Many Requests"
    trans_err = "[Translation Error: Connection refused]"
    config, paths, adapter = _build_sqlite_env(tmp_path)
    role_fields = desk.get_role_fields(desk.load_anki_mapping(paths["anki_mapping_file"]), HEADERS)

    # 1. _sanitize_rows
    sanitized = desk._sanitize_rows([[rate_err, trans_err, "valid"]])
    assert sanitized == [["", "", "valid"]]

    # 2. is_base_translation_finished
    assert desk.is_base_translation_finished(
        ["WordSource", "WordDestination"],
        [["house", rate_err]],
        {"lemma": "WordSource", "word_translation": "WordDestination"},
    ) is False
    assert desk.is_base_translation_finished(
        ["WordSource", "WordDestination"],
        [["house", trans_err]],
        {"lemma": "WordSource", "word_translation": "WordDestination"},
    ) is False

    # 3. deduplicate_rows
    deduped = desk.deduplicate_rows(
        [
            ["house", "NOUN", "house", rate_err],
            ["house", "NOUN", "houses", trans_err],
            ["house", "NOUN", "house", "дом"],
        ],
        col_word_source=0,
        col_pos=1,
        col_inflected=2,
        config=config,
        col_word_dest=3,
        deduplicate_pos_aware=False,
    )
    assert deduped[0][3] == "дом"

    # 4. aggregate_sequential_distinct
    agg = desk.aggregate_sequential_distinct([(0, rate_err), (1, trans_err), (2, "дом")])
    assert agg == "дом"

    # 5. _matched_chunk_rows & fast_provider_results
    patches, updates = progressive.plan_chunk_word_updates(
        [["house", ""]],
        col_lemma=0,
        col_word_dest=1,
        occurrence_keys=[(1, 0)],
        chunk_trans={"house": rate_err},
        chunk_prov="live:google",
        is_sqlite=True,
    )
    assert patches == []
    assert updates == []
    with pytest.raises(reword.InvalidRewordTarget):
        reword.fast_provider_results(["house"], {"house": trans_err})

    # 6. restore_session context left/right
    zid = "20261127200201"
    adapter.save_session(
        session_zid=zid,
        slug="ctx-fail",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="One.\nTwo.\nThree.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "One.", "sentence_destination": "Один."},
            {"sentence_index": 2, "sentence_source": "Two.", "sentence_destination": "Два."},
            {"sentence_index": 3, "sentence_source": "Three.", "sentence_destination": "Три."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "one", "quotation": "One", "word_destination": "один"},
            {"sentence_index": 2, "token_order": 0, "lemma": "two", "quotation": "Two", "word_destination": "два"},
            {"sentence_index": 3, "token_order": 0, "lemma": "three", "quotation": "Three", "word_destination": "три"},
        ],
        zid=zid,
    )
    with adapter.db.get_connection() as conn:
        conn.execute(
            "UPDATE sentences SET sentence_destination = ?, sentence_destination2 = ? WHERE session_zid = ? AND sentence_index = 1",
            (rate_err, trans_err, zid),
        )
        conn.execute(
            "UPDATE sentences SET sentence_destination = ?, sentence_destination2 = ? WHERE session_zid = ? AND sentence_index = 3",
            (trans_err, rate_err, zid),
        )
        conn.execute(
            "UPDATE words SET word_destination = ?, word_provenance = NULL, extra_fields = ? WHERE session_zid = ? AND sentence_index = 2",
            (rate_err, json.dumps({"occurrence_data": {"0": {"trans": trans_err, "pos": "NUM"}}}), zid),
        )
    restored = adapter.restore_session(zid)
    r_headers = restored["headers"]
    row2 = restored["data_rows"][1]
    for ctx_col in (
        "SentenceDestinationContextLeft",
        "SentenceDestinationContextRight",
        "SentenceDestination2ContextLeft",
        "SentenceDestination2ContextRight",
    ):
        if ctx_col in r_headers:
            assert rate_err not in row2[r_headers.index(ctx_col)]
            assert trans_err not in row2[r_headers.index(ctx_col)]

    # 7. write_update_js SQLite sentence projection
    import kardenwort_delivery
    upd_payload = {}
    orig_deliver = kardenwort_delivery.deliver_stage
    try:
        kardenwort_delivery.deliver_stage = lambda cfg, zid_arg, payload, dispatcher: upd_payload.update(payload) or True
        desk.write_update_js(
            zid,
            restored["data_rows"],
            restored["headers"],
            desk.get_role_fields(desk.load_anki_mapping(paths["anki_mapping_file"]), restored["headers"]),
            stage="translated_text",
            config=config,
            resolved_paths=paths,
            storage_adapter=adapter,
        )
    finally:
        kardenwort_delivery.deliver_stage = orig_deliver
    if "translatedText" in upd_payload and upd_payload["translatedText"]:
        assert rate_err not in upd_payload["translatedText"]
        assert trans_err not in upd_payload["translatedText"]

    # 8. WordFill candidates (SQLite & find_wordfill_match)
    cands = adapter.db.find_wordfill_candidates("two", language="en")
    assert all(not tv.is_stored_translation_failure(c.get("word_destination")) for c in cands)
    wf_match = desk.find_wordfill_match(
        "two",
        "en",
        {"enabled": True, "storage_backend": "sqlite", "db": adapter.db, "target_language": "ru"},
    )
    assert wf_match is None or not tv.is_stored_translation_failure(wf_match.get("WordDestination"))

    # 9-11. run_render_flow Overview, row_occurrences sanitization, and provenance guard
    html_out = desk.run_render_flow(
        text="One.\nTwo.\nThree.",
        language="en",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
    )
    assert rate_err not in html_out
    assert trans_err not in html_out
    cards = island(html_out, "sentence-cards")
    overview_words = cards[0]["words"]
    two_ov = [w for w in overview_words if w["lemma"].lower() == "two"][0]
    assert two_ov["translation"] == ""
    assert two_ov["provenance"] == ""


def test_pipe_h2_blank_line_multi_sentence_alignment_updates_sqlite_cleanly(tmp_path, monkeypatch):
    """PIPE-H2: multi-mode text with blank lines must update SQLite sentence_index 1 and 2 without targeting index 3."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    config.set("triggers", "run_text_translation", "auto")
    zid = "20261127200202"
    text = "First.\n\nSecond."
    auth_sents = desk._build_authoritative_sentences(zid, text, "multi", config)
    assert [s["sentence_index"] for s in auth_sents] == [1, 2]

    adapter.save_session(
        session_zid=zid,
        slug="first-second",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text=text,
        sentences=auth_sents,
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "first", "quotation": "First", "word_destination": "первый"},
            {"sentence_index": 2, "token_order": 0, "lemma": "second", "quotation": "Second", "word_destination": "второй"},
        ],
        zid=zid,
    )

    monkeypatch.setattr(
        desk,
        "translate_source_text",
        lambda *a, **kw: desk.ProvenanceDict({0: "Первое.", 1: "", 2: "Второе."}, provenance="live:google"),
    )

    _, headers, data_rows = adapter.load_tsv_rows(zid)
    role_fields = desk.get_role_fields(desk.load_anki_mapping(paths["anki_mapping_file"]), headers)
    args = Namespace(zid=zid, language="en", target_lang="ru", text_mode="multi", provider="google", trace_id=f"{zid}:t")
    res = desk._progressive_worker_stage_translation_impl(
        tmp_path / f"{zid}-first-second.en.tsv", args, config, paths, data_rows, headers, role_fields, zid
    )

    assert res.error is None
    sents = {s["sentence_index"]: s for s in adapter.db.get_sentences_by_session(zid)}
    assert sents[1]["sentence_destination"] == "Первое."
    assert sents[2]["sentence_destination"] == "Второе."


def test_pipe_h3_partially_unattributed_multiline_child_card_does_not_inherit_other_provider(tmp_path):
    """PIPE-H3: an unattributed child card in a multiline session must not inherit another sentence's provider."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    zid = "20261127200203"
    text = "First line.\nSecond line."
    adapter.save_session(
        session_zid=zid,
        slug="partial-prov",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text=text,
        sentences=[
            {"sentence_index": 1, "sentence_source": "First line.", "sentence_destination": "Первая строка.", "text_provenance": "live:google"},
            {"sentence_index": 2, "sentence_source": "Second line.", "sentence_destination": "Вторая строка.", "text_provenance": None},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "first", "quotation": "First", "word_destination": "первый"},
            {"sentence_index": 2, "token_order": 0, "lemma": "second", "quotation": "Second", "word_destination": "второй"},
        ],
        zid=zid,
    )

    html_out = desk.run_render_flow(
        text=text,
        language="en",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=2,
        wordfill_cfg={"enabled": False},
    )
    cards = {c["sentence_idx"]: c for c in island(html_out, "sentence-cards")}
    assert cards[1]["text_provenance"] == "live:google"
    assert cards[2]["text_provenance"] == ""
    match = re.search(r'<div[^>]*data-translation-sentence="2"[^>]*>', html_out)
    assert match is not None
    assert 'data-provenance="live:google"' not in match.group(0)


def test_pipe_h5_early_wordfill_preserves_translation_provenance_and_filters_target_language(tmp_path):
    """PIPE-H5: supplementary IPA WordFill preserves existing translation provenance and filters by target_language."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    config.set("settings", "default_language", "de")
    config.set("settings", "default_target_language", "en")

    # Seed de -> ru corpus session (wrong target_language!) and de -> en corpus session (right target_language)
    adapter.save_session(
        session_zid="20261127200204",
        slug="corpus-ru",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "Дом."}],
        words=[{
            "sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus",
            "word_destination": "дом", "ipa": "/haʊs-ru/", "morphology": "RU-morph",
        }],
    )
    adapter.save_session(
        session_zid="20261127200205",
        slug="corpus-en",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "House."}],
        words=[{
            "sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus",
            "word_destination": "home", "ipa": "/haʊs/", "morphology": "Noun.Neut",
        }],
    )

    # Query DB candidates strictly for target_language='en'
    cands = adapter.db.find_wordfill_candidates("Haus", language="de", target_language="en")
    assert cands and all(c["word_destination"] != "дом" for c in cands)

    # Seed current de -> en session where Haus already has WordDestination='house' (live:deepl) but missing IPA
    zid = "20261127200206"
    adapter.save_session(
        session_zid=zid,
        slug="current-en",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "House."}],
        words=[{
            "sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus",
            "word_destination": "house", "ipa": "", "morphology": "", "word_provenance": "live:deepl",
        }],
    )

    desk.run_render_flow(
        text="Haus.",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": True, "storage_backend": "sqlite", "db": adapter.db},
    )

    stored = adapter.db.get_words_by_session(zid)[0]
    assert stored["word_destination"] == "house"
    assert stored["ipa"] == "/haʊs/"
    assert stored["word_provenance"] == "live:deepl"


def test_pipe_h6_list_sessions_with_counts_avoids_cartesian_product(tmp_path):
    """PIPE-H6: list_sessions_with_counts must return sentence_count=3, token_count=4 (not 12)."""
    _, _, adapter = _build_sqlite_env(tmp_path)
    zid = "20261127200207"
    adapter.save_session(
        session_zid=zid,
        slug="counts",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="A.\nB.\nC.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "A."},
            {"sentence_index": 2, "sentence_source": "B."},
            {"sentence_index": 3, "sentence_source": "C."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "a1", "quotation": "A1"},
            {"sentence_index": 1, "token_order": 1, "lemma": "a2", "quotation": "A2"},
            {"sentence_index": 2, "token_order": 0, "lemma": "b1", "quotation": "B1"},
            {"sentence_index": 3, "token_order": 0, "lemma": "c1", "quotation": "C1"},
        ],
    )

    listed = adapter.db.list_sessions_with_counts()
    row = [s for s in listed if s["zid"] == zid][0]
    assert row["sentence_count"] == 3
    assert row["token_count"] == 4


def test_pipe_h7_get_cached_session_excludes_soft_deleted_and_filters_mode_and_target(tmp_path):
    """PIPE-H7: get_cached_session must ignore soft-deleted sessions and respect text_mode and target_language."""
    _, _, adapter = _build_sqlite_env(tmp_path)
    active_zid = "20261127200208"
    deleted_zid = "20261127200209"
    wrong_mode_zid = "20261127200210"

    adapter.save_session(
        session_zid=active_zid,
        slug="hello-world",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hello world.",
        sentences=[{"sentence_index": 1, "sentence_source": "Hello world."}],
        words=[{"sentence_index": 1, "token_order": 0, "lemma": "hello", "quotation": "Hello"}],
    )
    adapter.save_session(
        session_zid=deleted_zid,
        slug="hello-world",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hello world.",
        sentences=[{"sentence_index": 1, "sentence_source": "Hello world."}],
        words=[{"sentence_index": 1, "token_order": 0, "lemma": "hello", "quotation": "Hello"}],
    )
    adapter.save_session(
        session_zid=wrong_mode_zid,
        slug="hello-world",
        source_language="en",
        target_language="de",
        text_mode="multi",
        source_raw_text="Hello   world!",
        sentences=[{"sentence_index": 1, "sentence_source": "Hello   world!"}],
        words=[{"sentence_index": 1, "token_order": 0, "lemma": "hello", "quotation": "Hello"}],
    )
    with adapter.db.get_connection() as conn:
        conn.execute("UPDATE sessions SET created_at = '2026-11-27T20:00:00+00:00' WHERE zid = ?", (active_zid,))
        conn.execute(
            "UPDATE sessions SET created_at = '2026-11-27T20:01:00+00:00', deleted_at = '2026-11-27T20:02:00+00:00' WHERE zid = ?",
            (deleted_zid,),
        )
        conn.execute("UPDATE sessions SET created_at = '2026-11-27T20:03:00+00:00' WHERE zid = ?", (wrong_mode_zid,))

    # Exact raw_text query must skip soft-deleted newer session and return active_zid
    hit = adapter.get_cached_session(
        "hello-world", "en", 86400000, source_raw_text="Hello world.", target_language="ru", text_mode="single"
    )
    assert hit is not None and hit["session"]["zid"] == active_zid

    # Normalized query with text_mode='multi' and target_language='ru' must not match single-mode active_zid
    miss = adapter.get_cached_session(
        "hello-world", "en", 86400000, source_raw_text="hello world", target_language="ru", text_mode="multi"
    )
    assert miss is None

    # Slug-only fallback must also respect deleted_at, text_mode, and target_language
    slug_hit = adapter.get_cached_session(
        "hello-world", "en", 86400000, source_raw_text="", target_language="ru", text_mode="single"
    )
    assert slug_hit is not None and slug_hit["session"]["zid"] == active_zid


def test_pipe_h8_overview_selections_scoped_by_sentence_index_across_multi_sentence_session(tmp_path):
    """PIPE-H8: overview_selections must be keyed by (session_zid, sentence_index, token_order)."""
    _, _, adapter = _build_sqlite_env(tmp_path)
    zid = "20261127200211"
    adapter.save_session(
        session_zid=zid,
        slug="ov-sel",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Apple.\nBanana.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "Apple."},
            {"sentence_index": 2, "sentence_source": "Banana."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "apple", "quotation": "Apple", "selected": 0},
            {"sentence_index": 2, "token_order": 0, "lemma": "banana", "quotation": "Banana", "selected": 0},
        ],
    )

    assert adapter.update_word_selection(zid, 1, 0, 1) is True
    restored = adapter.restore_session(zid, include_overview_selections=True)
    words_by_sent = {w["sentence_index"]: w for w in restored["words"]}
    assert words_by_sent[1]["selected"] == 1
    assert words_by_sent[2]["selected"] == 0

    # Deselecting (2, 0) must not clear (1, 0)
    assert adapter.update_word_selection(zid, 2, 0, 0) is True
    restored_after = adapter.restore_session(zid, include_overview_selections=True)
    words_after = {w["sentence_index"]: w for w in restored_after["words"]}
    assert words_after[1]["selected"] == 1
    assert words_after[2]["selected"] == 0

    # Selecting both must yield selected_count == 2 in search_sessions and get_project_tree
    assert adapter.update_word_selection(zid, 2, 0, 1) is True
    proj_id = adapter.db.create_project("Proj")
    adapter.db.link_session_to_project(proj_id, zid)
    sessions, _ = adapter.db.search_sessions(query=zid)
    assert sessions[0]["selected_count"] == 2
    tree = adapter.db.get_project_tree(project_id=proj_id)
    assert tree[0]["sessions"][0]["selected_count"] == 2


def test_pipe_h9_format_translated_html_does_not_leak_padded_or_accept_bool_nonascii_keys():
    """PIPE-H9: format_translated_html must not leak PADDED/FULL_TEXT or accept bool/non-ASCII numeric keys."""
    leaked = desk.format_translated_html({0: "", "PADDED": {0: "secret"}, "FULL_TEXT": ""})
    assert "secret" not in leaked
    assert leaked == ""

    weird = desk.format_translated_html({True: "bool-val", "²": "sup-val"})
    assert "bool-val" not in weird
    assert "sup-val" not in weird


def test_pipe_h10_overview_rollup_preserves_sentence_token_order_and_scopes_ov_row_occ(tmp_path):
    """PIPE-H10: Overview rollup preserves (sentence_index, token_order) order and scopes ov_row_occ by sentence."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    zid = "20261127200212"
    text = "Run fast.\nRun slow."
    # Seed Sentence 2 first in reverse order or frequency order so mid order differs from (sentence_index, token_order)
    adapter.save_session(
        session_zid=zid,
        slug="rollup-order",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text=text,
        sentences=[
            {"sentence_index": 1, "sentence_source": "Run fast.", "sentence_destination": "Беги быстро."},
            {"sentence_index": 2, "sentence_source": "Run slow.", "sentence_destination": "Беги медленно."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "run", "quotation": "Run", "inflected_form": "Run", "word_destination": "бежать-1", "morphology": "morph-1", "pos": "VERB"},
            {"sentence_index": 2, "token_order": 0, "lemma": "run", "quotation": "Run", "inflected_form": "Run", "word_destination": "бежать-2", "morphology": "morph-2", "pos": "VERB"},
        ],
    )
    # Reverse data_rows via preloaded_bundle so mid=0 is sentence 2 and mid=1 is sentence 1
    restored = adapter.restore_session(zid)
    reversed_bundle = dict(restored)
    reversed_bundle["data_rows"] = list(reversed(restored["data_rows"]))
    reversed_bundle["words"] = list(reversed(restored["words"]))

    html_out = desk.run_render_flow(
        text=text,
        language="en",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
        preloaded_bundle=reversed_bundle,
    )
    cards = island(html_out, "sentence-cards")
    run_ov = [w for w in cards[0]["words"] if w["lemma"].lower() == "run"][0]
    assert run_ov["translation"] == "бежать-1, бежать-2"
    assert run_ov["morphology"] == "morph-1; morph-2"
    occ = run_ov["occurrence_data"]
    # Visual tokens in sentence 1 and sentence 2 must carry their own sentence's translation
    sent_trans_vals = {entry["trans"] for entry in occ.values()}
    assert sent_trans_vals == {"бежать-1", "бежать-2"}


def test_pipe_h11_preloaded_bundle_hydrates_occurrences_provenances_and_overview_selections(tmp_path):
    """PIPE-H11: preloaded_bundle must hydrate row_occurrences, occurrence_provenances, row_provenances, and overview selections."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    zid = "20261127200213"
    text = "Apple.\nBanana."
    adapter.save_session(
        session_zid=zid,
        slug="preloaded",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text=text,
        sentences=[
            {"sentence_index": 1, "sentence_source": "Apple.", "sentence_destination": "Яблоко.", "text_provenance": "live:deepl"},
            {"sentence_index": 2, "sentence_source": "Banana.", "sentence_destination": "Банан.", "text_provenance": "live:deepl"},
        ],
        words=[
            {
                "sentence_index": 1, "token_order": 0, "lemma": "apple", "quotation": "Apple",
                "word_destination": "яблоко", "word_provenance": "live:deepl", "selected": 1,
                "extra_fields": {"occurrence_data": {"0": {"trans": "яблоко", "pos": "NOUN"}}},
            },
            {
                "sentence_index": 2, "token_order": 0, "lemma": "banana", "quotation": "Banana",
                "word_destination": "банан", "word_provenance": "live:google", "selected": 0,
            },
        ],
    )
    adapter.update_word_selection(zid, 1, 0, 1)
    bundle = adapter.restore_session(zid, include_overview_selections=True)
    bundle["overview_selections"] = adapter.get_overview_selections(zid)

    html_out = desk.run_render_flow(
        text=text,
        language="en",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
        preloaded_bundle=bundle,
    )
    cards = island(html_out, "sentence-cards")
    ov_by_lemma = {w["lemma"].lower(): w for w in cards[0]["words"]}
    assert ov_by_lemma["apple"]["provenance"] == "live:deepl"
    assert ov_by_lemma["banana"]["provenance"] == "live:google"
    assert ov_by_lemma["apple"]["selected"] == "1"
    assert ov_by_lemma["apple"]["occurrence_data"].get("0", {}).get("trans") == "яблоко"


def test_pipe_h12_enrich_rows_forwards_custom_sentence_index_role(tmp_path):
    """PIPE-H12: kardenwort_enrichment.enrich_rows must forward custom sentence_index header to merge_changes."""
    config, _, _ = _build_sqlite_env(tmp_path)
    config.set("services", "intellifiller_server_url", "http://127.0.0.1:1")
    headers = ["TokenOrder", "CustomSentIdx", "WordSource", "WordDestination"]
    rows = [["0", "1", "Haus", ""]]
    selection = reword.RewordSelection(keys=((1, 0),), rows=(0,), visuals=(), visual_keys=())

    def fake_query(**_kwargs):
        return {"status": "success", "enriched_rows": [{"row_id": 0, "WordDestination": "house"}]}

    cols, out_rows, _ = enrichment.enrich_rows(
        rows,
        headers,
        selection,
        "de",
        "fixture",
        config,
        fake_query,
        roles={"sentence_index": "CustomSentIdx"},
    )
    assert out_rows[0][cols.index("WordDestination")] == "house"


def test_pipe_h13_reword_resolve_selection_rejects_contradictory_or_zero_visual_sentence_idx():
    """PIPE-H13: visual descriptor sentence_idx must be >= 1 and match the resolved occurrence's sentence."""
    headers = ["TokenOrder", "SentenceSourceIndex", "WordSource", "WordDestination"]
    rows = [["0", "1", "Haus", "house"]]
    visual_manifest = {1: (0,)}

    with pytest.raises(ValueError):
        reword.resolve_selection(
            rows, headers, targets=[{"visual_idx": 1, "sentence_idx": 0}], visual_manifest=visual_manifest
        )

    with pytest.raises(ValueError):
        reword.resolve_selection(
            rows, headers, targets=[{"visual_idx": 1, "sentence_idx": 999}], visual_manifest=visual_manifest
        )


def test_pipe_h14_worker_combines_errors_rejects_empty_sentence_success_and_fixes_retext_worker(
    tmp_path, monkeypatch
):
    """PIPE-H14: combine sentence+lemma errors, reject 0-sentence success, respect eligible_rows, fix cmd_retext_worker."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    config.set("triggers", "run_text_translation", "auto")
    config.set("triggers", "run_lemma_base_translation", "auto")
    zid = "20261127200214"
    adapter.save_session(
        session_zid=zid,
        slug="worker-err",
        source_language="en",
        target_language="ru",
        text_mode="single",
        source_raw_text="Hello.",
        sentences=[{"sentence_index": 1, "sentence_source": "Hello.", "sentence_destination": ""}],
        words=[{"sentence_index": 1, "token_order": 0, "lemma": "hello", "quotation": "Hello", "word_destination": ""}],
        zid=zid,
    )

    # (a) Both sentence and lemma stages fail -> combine_worker_errors preserves both
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("sent boom")))
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("lemma boom")))
    _, headers, data_rows = adapter.load_tsv_rows(zid)
    role_fields = desk.get_role_fields(desk.load_anki_mapping(paths["anki_mapping_file"]), headers)
    args = Namespace(zid=zid, language="en", target_lang="ru", text_mode="single", provider="google", trace_id=f"{zid}:t")
    res = desk._progressive_worker_stage_translation_impl(
        tmp_path / f"{zid}-worker-err.en.tsv", args, config, paths, data_rows, headers, role_fields, zid
    )
    assert res.error is not None
    assert "sent boom" in str(res.error.get("message", ""))
    assert "subsequent_error" in (res.error.get("details") or {})

    # (b) 0 updated sentences must not emit text_translation_status='success'
    events = []
    orig_write = desk.safe_write_update_js
    monkeypatch.setattr(
        desk,
        "safe_write_update_js",
        lambda *a, **kw: events.append((kw.get("stage"), kw.get("text_translation_status"), kw.get("text_translation_failed"))) or orig_write(*a, **kw),
    )
    monkeypatch.setattr(desk, "translate_source_text", lambda *a, **kw: desk.ProvenanceDict({}, provenance="live:google"))
    monkeypatch.setattr(desk, "translate_lemmas_fast_path", lambda lemmas, *a, **kw: desk.ProvenanceDict({l: "привет" for l in lemmas}, provenance="live:google"))
    desk._progressive_worker_stage_translation_impl(
        tmp_path / f"{zid}-worker-err.en.tsv", args, config, paths, [list(r) for r in data_rows], headers, role_fields, zid
    )
    text_events = [e for e in events if e[0] == "translated_text"]
    assert text_events and text_events[-1][1] == "failed" and text_events[-1][2] is True

    # (c) cmd_retext_worker uses actual failover provenance & composite provenance keys
    retext_zid = "20261127200215"
    adapter.save_session(
        session_zid=retext_zid,
        slug="retext-prov",
        source_language="en",
        target_language="ru",
        text_mode="multi",
        source_raw_text="One.\nTwo.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "One.", "sentence_destination": "Old 1."},
            {"sentence_index": 2, "sentence_source": "Two.", "sentence_destination": "Old 2."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "one", "quotation": "One", "word_destination": "один", "word_provenance": "live:deepl"},
            {"sentence_index": 2, "token_order": 0, "lemma": "two", "quotation": "Two", "word_destination": "два", "word_provenance": "live:google"},
        ],
    )
    config.set("pipeline", "text_reprocess_provider", "deepl")
    retext_captured = []
    monkeypatch.setattr(desk, "load_config", lambda *_a, **_kw: (config, paths, {}, {"enabled": False}))
    monkeypatch.setattr(
        desk,
        "translate_source_text",
        lambda *a, **kw: desk.ProvenanceDict({0: "Новый 1.", 1: "Новый 2."}, provenance="live:argos"),
    )
    monkeypatch.setattr(
        desk,
        "safe_write_update_js",
        lambda *a, **kw: retext_captured.append(kw) or None,
    )
    desk.cmd_retext_worker(
        Namespace(config=None, tsv=str(tmp_path / f"{retext_zid}-retext-prov.en.tsv"), language="en", text_mode="multi", zid=retext_zid, trace_id=f"{retext_zid}:r")
    )
    assert retext_captured
    fin_kw = retext_captured[-1]
    assert fin_kw.get("text_provenance") == "live:argos"
    occ_provs = fin_kw.get("occurrence_provenances") or {}
    assert occ_provs.get(("1", "0")) == "live:deepl"
    assert occ_provs.get(("2", "0")) == "live:google"
