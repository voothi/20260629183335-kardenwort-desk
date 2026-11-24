"""Phase 1 real-SQLite reproduction and contract tests for Task 1.2:
Exact-coordinate save identity, cross-sentence/lemma spillover prevention,
duplicate/invalid anchor atomic rejection, metadata omission/update/clear/replacement,
and direct words versus tabular callers with cold bundle and untouched neighbor checks.
"""
import pytest

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB


TARGET_ZID = "20261124061001"
NEIGHBOR_ZID = "20261124061002"

TABULAR_HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
    "WordSourcePOS",
    "WordSourceGender",
    "DeskSelected",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
]


def _strip_surrogate_id(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "id"}
    if isinstance(out.get("extra_fields"), dict):
        out["extra_fields"] = {k: v for k, v in out["extra_fields"].items() if k.lower() != "tokenorder"}
    return out


def _custom_extra(word: dict) -> dict:
    ef = word.get("extra_fields") or {}
    return {k: v for k, v in ef.items() if k.lower() != "tokenorder"}


def _cold_bundle(db_path, zid):
    return KardenwortDB(db_path=db_path).get_session_bundle(zid, parse_json=True)


def _seed_neighbor(adapter):
    adapter.save_session(
        session_zid=NEIGHBOR_ZID,
        slug="neighbor",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Der Fluss.",
        sentences=[
            {
                "session_zid": NEIGHBOR_ZID,
                "sentence_index": 1,
                "sentence_source": "Der Fluss.",
                "sentence_destination": "The river.",
                "text_provenance": "live:deepl",
            }
        ],
        words=[
            {
                "session_zid": NEIGHBOR_ZID,
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Fluss",
                "lemma": "Fluss",
                "word_destination": "river",
                "ipa": "/flʊs/",
                "morphology": "Subst.Masc",
                "pos": "NOUN",
                "gender": "m",
                "selected": 1,
                "word_provenance": "live:deepl",
                "extra_fields": {
                    "occurrence_data": {"1": {"trans": "river", "pos": "NOUN"}},
                    "NeighborTag": "untouched",
                },
            }
        ],
    )


def _seed_two_sentence_session(adapter):
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        sentences=[
            {
                "session_zid": TARGET_ZID,
                "sentence_index": 1,
                "sentence_source": "Das Haus.",
                "sentence_destination": "The house.",
                "text_provenance": "live:google",
            },
            {
                "session_zid": TARGET_ZID,
                "sentence_index": 2,
                "sentence_source": "Der Baum.",
                "sentence_destination": "The tree.",
                "text_provenance": "live:deepl",
            },
        ],
        words=[
            {
                "session_zid": TARGET_ZID,
                "sentence_index": 1,
                "token_order": 0,
                "quotation": "Haus",
                "lemma": "Haus",
                "word_destination": "house",
                "ipa": "/haʊ̯s/",
                "morphology": "Subst.Neut",
                "pos": "NOUN",
                "gender": "n",
                "selected": 1,
                "word_provenance": "live:google",
                "extra_fields": {
                    "occurrence_data": {"1": {"trans": "house", "pos": "NOUN"}},
                    "CustomNote": "s1-note",
                    "KeepKey": "keep-value",
                },
            },
            {
                "session_zid": TARGET_ZID,
                "sentence_index": 2,
                "token_order": 0,
                "quotation": "Baum",
                "lemma": "Baum",
                "word_destination": "tree",
                "ipa": "/baʊ̯m/",
                "morphology": "Subst.Masc",
                "pos": "NOUN",
                "gender": "m",
                "selected": 0,
                "word_provenance": "live:deepl",
                "extra_fields": {
                    "occurrence_data": {"4": {"trans": "tree", "pos": "NOUN"}},
                    "CustomNote": "s2-note",
                },
            },
        ],
    )


def test_save_session_prevents_cross_sentence_token_order_spillover(tmp_path):
    """Shared local token_order 0 across sentences must never inherit fields from another sentence."""
    db_path = tmp_path / "cross_sentence.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    _seed_two_sentence_session(adapter)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)

    # Save a third sentence with token_order 0 and same lemma 'Haus' (empty fields) plus a fourth with 'Wald';
    # neither must inherit fields/provenance from (1, 0) or (2, 0).
    sentences = [
        {"session_zid": TARGET_ZID, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "The house.", "text_provenance": "live:google"},
        {"session_zid": TARGET_ZID, "sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "The tree.", "text_provenance": "live:deepl"},
        {"session_zid": TARGET_ZID, "sentence_index": 3, "sentence_source": "Noch ein Haus.", "sentence_destination": "Another house.", "text_provenance": "live:argos"},
    ]
    rows = [
        ["0", "Haus", "Haus", "house", "/haʊ̯s/", "Subst.Neut", "NOUN", "n", "1", "1", "Das Haus.", "The house."],
        ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "0", "2", "Der Baum.", "The tree."],
        ["0", "Haus", "Haus", "", "", "", "", "", "0", "3", "Noch ein Haus.", "Another house."],
    ]
    # Add sentence 3 first so multi-sentence validation recognizes sentence_index 3
    adapter.db.insert_sentence(sentences[2])
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.\nNoch ein Haus.",
        headers=TABULAR_HEADERS,
        data_rows=rows,
    )

    bundle = _cold_bundle(db_path, TARGET_ZID)
    by_coord = {(w["sentence_index"], w["token_order"]): w for w in bundle["words"]}
    s3_haus = by_coord[(3, 0)]
    assert s3_haus["lemma"] == "Haus"
    assert not s3_haus["word_destination"], f"Cross-sentence token_order/lemma spillover: {s3_haus}"
    assert s3_haus["ipa"] is None
    assert s3_haus["morphology"] is None
    assert s3_haus["pos"] is None
    assert s3_haus["gender"] is None
    assert s3_haus["word_provenance"] is None
    assert not (s3_haus["extra_fields"] or {}).get("occurrence_data")
    assert not (s3_haus["extra_fields"] or {}).get("CustomNote")
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before


def test_save_session_prevents_cross_lemma_spillover_and_stale_inheritance_on_lemma_replacement(tmp_path):
    """Changing lemma at (1, 0) is a replacement; it must not inherit old (1, 0) fields or another coordinate's lemma."""
    db_path = tmp_path / "lemma_replacement.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    _seed_two_sentence_session(adapter)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)
    s2_before = _cold_bundle(db_path, TARGET_ZID)["words"][1]

    # Case A: Replace (1, 0) lemma Haus -> Wald with empty fields; must NOT inherit Haus metadata/values.
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        headers=TABULAR_HEADERS,
        data_rows=[
            ["0", "Wald", "Wald", "", "", "", "", "", "0", "1", "Das Haus.", "The house."],
            ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "0", "2", "Der Baum.", "The tree."],
        ],
    )
    bundle_a = _cold_bundle(db_path, TARGET_ZID)
    by_coord_a = {(w["sentence_index"], w["token_order"]): w for w in bundle_a["words"]}
    replaced = by_coord_a[(1, 0)]
    assert replaced["lemma"] == "Wald"
    assert replaced["word_destination"] is None
    assert replaced["ipa"] is None
    assert replaced["morphology"] is None
    assert replaced["pos"] is None
    assert replaced["gender"] is None
    assert replaced["selected"] == 0
    assert replaced["word_provenance"] is None
    assert _custom_extra(replaced) == {}
    assert _strip_surrogate_id(by_coord_a[(2, 0)]) == _strip_surrogate_id(s2_before)

    # Case B: Replace (1, 0) lemma Wald -> Baum (which exists at (2, 0)); must NOT inherit (2, 0) via lemma fallback.
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        headers=TABULAR_HEADERS,
        data_rows=[
            ["0", "Baum", "Baum", "", "", "", "", "", "0", "1", "Das Haus.", "The house."],
            ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "0", "2", "Der Baum.", "The tree."],
        ],
    )
    bundle_b = _cold_bundle(db_path, TARGET_ZID)
    by_coord_b = {(w["sentence_index"], w["token_order"]): w for w in bundle_b["words"]}
    assert by_coord_b[(1, 0)]["word_destination"] is None
    assert by_coord_b[(1, 0)]["ipa"] is None
    assert by_coord_b[(1, 0)]["word_provenance"] is None
    assert _custom_extra(by_coord_b[(1, 0)]) == {}
    assert _strip_surrogate_id(by_coord_b[(2, 0)]) == _strip_surrogate_id(s2_before)
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before


@pytest.mark.parametrize(
    "bad_case,rows",
    [
        (
            "duplicate_coord",
            [
                ["0", "Haus", "Haus", "house", "", "", "", "", "0", "1", "Das Haus.", "The house."],
                ["0", "Dach", "Dach", "roof", "", "", "", "", "0", "1", "Das Haus.", "The house."],
            ],
        ),
        (
            "negative_token_order",
            [["-1", "Haus", "Haus", "house", "", "", "", "", "0", "1", "Das Haus.", "The house."]],
        ),
        (
            "non_ascii_digit_token_order",
            [["\u0661", "Haus", "Haus", "house", "", "", "", "", "0", "1", "Das Haus.", "The house."]],
        ),
        (
            "composite_token_order",
            [["1+5", "Haus", "Haus", "house", "", "", "", "", "0", "1", "Das Haus.", "The house."]],
        ),
        (
            "non_numeric_token_order",
            [["abc", "Haus", "Haus", "house", "", "", "", "", "0", "1", "Das Haus.", "The house."]],
        ),
        (
            "non_ascii_sentence_index",
            [["0", "Haus", "Haus", "house", "", "", "", "", "0", "\u0661", "Das Haus.", "The house."]],
        ),
    ],
)
def test_save_session_tabular_rejects_duplicate_and_invalid_anchors_atomically(tmp_path, bad_case, rows):
    """Duplicate or invalid tabular coordinates reject before any SQLite mutation."""
    db_path = tmp_path / f"bad_tab_{bad_case}.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    _seed_two_sentence_session(adapter)
    target_before = _cold_bundle(db_path, TARGET_ZID)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)

    with pytest.raises((desk.StructuredError, ValueError)):
        adapter.save_session(
            session_zid=TARGET_ZID,
            slug="identity-target",
            source_language="de",
            target_language="en",
            text_mode="multi",
            source_raw_text="Das Haus.\nDer Baum.",
            headers=TABULAR_HEADERS,
            data_rows=rows,
        )

    assert _cold_bundle(db_path, TARGET_ZID) == target_before
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before


@pytest.mark.parametrize(
    "bad_case,words",
    [
        (
            "duplicate_coord",
            [
                {"sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"},
                {"sentence_index": 1, "token_order": 0, "quotation": "Dach", "lemma": "Dach", "word_destination": "roof"},
            ],
        ),
        (
            "bool_token_order",
            [{"sentence_index": 1, "token_order": True, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"}],
        ),
        (
            "negative_token_order",
            [{"sentence_index": 1, "token_order": -1, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"}],
        ),
        (
            "composite_token_order",
            [{"sentence_index": 1, "token_order": "1+5", "quotation": "Haus", "lemma": "Haus", "word_destination": "house"}],
        ),
        (
            "invalid_sentence_zero",
            [{"sentence_index": 0, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"}],
        ),
        (
            "bool_sentence_index",
            [{"sentence_index": True, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"}],
        ),
    ],
)
def test_save_session_direct_words_rejects_duplicate_and_invalid_anchors_atomically(tmp_path, bad_case, words):
    """Duplicate or invalid direct-word coordinates reject atomically before any write."""
    db_path = tmp_path / f"bad_words_{bad_case}.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    _seed_two_sentence_session(adapter)
    target_before = _cold_bundle(db_path, TARGET_ZID)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)

    with pytest.raises((desk.StructuredError, ValueError)):
        adapter.save_session(
            session_zid=TARGET_ZID,
            slug="identity-target",
            source_language="de",
            target_language="en",
            text_mode="multi",
            source_raw_text="Das Haus.\nDer Baum.",
            sentences=[
                {"sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "The house."},
                {"sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "The tree."},
            ],
            words=words,
        )

    assert _cold_bundle(db_path, TARGET_ZID) == target_before
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before


def test_save_session_tabular_preserves_omitted_metadata_updates_custom_and_clears_explicitly(tmp_path):
    """Tabular save preserves omitted occurrence_data/custom fields, merges explicit custom updates, and clears explicit empties."""
    db_path = tmp_path / "tabular_metadata.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    _seed_two_sentence_session(adapter)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)
    s2_before = _cold_bundle(db_path, TARGET_ZID)["words"][1]

    # 1. Omission: headers omit CustomNote, KeepKey, and occurrence_data -> all must survive!
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        headers=TABULAR_HEADERS,
        data_rows=[
            ["0", "Haus", "Haus", "house", "/haʊ̯s/", "Subst.Neut", "NOUN", "n", "1", "1", "Das Haus.", "The house."],
            ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "0", "2", "Der Baum.", "The tree."],
        ],
    )
    after_omit = _cold_bundle(db_path, TARGET_ZID)
    by_coord = {(w["sentence_index"], w["token_order"]): w for w in after_omit["words"]}
    assert _custom_extra(by_coord[(1, 0)]) == {
        "occurrence_data": {"1": {"trans": "house", "pos": "NOUN"}},
        "CustomNote": "s1-note",
        "KeepKey": "keep-value",
    }
    assert _strip_surrogate_id(by_coord[(2, 0)]) == _strip_surrogate_id(s2_before)

    # 2. Explicit update of CustomNote column updates CustomNote while preserving omitted KeepKey & occurrence_data.
    headers_with_note = TABULAR_HEADERS + ["CustomNote"]
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        headers=headers_with_note,
        data_rows=[
            ["0", "Haus", "Haus", "house", "/haʊ̯s/", "Subst.Neut", "NOUN", "n", "1", "1", "Das Haus.", "The house.", "s1-updated"],
            ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "0", "2", "Der Baum.", "The tree.", "s2-note"],
        ],
    )
    after_update = _cold_bundle(db_path, TARGET_ZID)
    by_coord_u = {(w["sentence_index"], w["token_order"]): w for w in after_update["words"]}
    assert _custom_extra(by_coord_u[(1, 0)]) == {
        "occurrence_data": {"1": {"trans": "house", "pos": "NOUN"}},
        "CustomNote": "s1-updated",
        "KeepKey": "keep-value",
    }
    assert _strip_surrogate_id(by_coord_u[(2, 0)]) == _strip_surrogate_id(s2_before)

    # 3. Explicit clear: CustomNote column present with empty string clears CustomNote while preserving omitted KeepKey & occurrence_data.
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        headers=headers_with_note,
        data_rows=[
            ["0", "Haus", "Haus", "house", "/haʊ̯s/", "Subst.Neut", "NOUN", "n", "1", "1", "Das Haus.", "The house.", ""],
            ["0", "Baum", "Baum", "tree", "/baʊ̯m/", "Subst.Masc", "NOUN", "m", "0", "2", "Der Baum.", "The tree.", "s2-note"],
        ],
    )
    after_clear = _cold_bundle(db_path, TARGET_ZID)
    by_coord_c = {(w["sentence_index"], w["token_order"]): w for w in after_clear["words"]}
    assert _custom_extra(by_coord_c[(1, 0)]) == {
        "occurrence_data": {"1": {"trans": "house", "pos": "NOUN"}},
        "KeepKey": "keep-value",
    }
    assert _strip_surrogate_id(by_coord_c[(2, 0)]) == _strip_surrogate_id(s2_before)
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before


def test_save_session_direct_words_metadata_omission_update_clear_and_replacement(tmp_path):
    """Direct words caller distinguishes omitted extra_fields, explicit partial clear/update, and full replacement."""
    db_path = tmp_path / "direct_words_metadata.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    _seed_two_sentence_session(adapter)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)
    sentences = [
        {"sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "The house.", "text_provenance": "live:google"},
        {"sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "The tree.", "text_provenance": "live:deepl"},
    ]

    # 1. Omitted extra_fields key on unchanged word preserves existing extra_fields
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        sentences=sentences,
        words=[
            {"sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"},
            {"sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": "tree"},
        ],
    )
    by_coord = {(w["sentence_index"], w["token_order"]): w for w in _cold_bundle(db_path, TARGET_ZID)["words"]}
    assert by_coord[(1, 0)]["extra_fields"] == {
        "occurrence_data": {"1": {"trans": "house", "pos": "NOUN"}},
        "CustomNote": "s1-note",
        "KeepKey": "keep-value",
    }
    assert by_coord[(2, 0)]["extra_fields"] == {
        "occurrence_data": {"4": {"trans": "tree", "pos": "NOUN"}},
        "CustomNote": "s2-note",
    }

    # 2. Explicit empty extra_fields={} on unchanged word clears extra_fields (full replacement of extra_fields)
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        sentences=sentences,
        words=[
            {"sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house", "extra_fields": {}},
            {"sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": "tree"},
        ],
    )
    by_coord_2 = {(w["sentence_index"], w["token_order"]): w for w in _cold_bundle(db_path, TARGET_ZID)["words"]}
    assert by_coord_2[(1, 0)]["extra_fields"] in (None, {})
    assert by_coord_2[(2, 0)]["extra_fields"] == {
        "occurrence_data": {"4": {"trans": "tree", "pos": "NOUN"}},
        "CustomNote": "s2-note",
    }

    # 3. Direct words lemma replacement drops stale fields/metadata on the replaced coordinate
    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="identity-target",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        sentences=sentences,
        words=[
            {"sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"},
            {"sentence_index": 2, "token_order": 0, "quotation": "Stein", "lemma": "Stein", "word_destination": "stone"},
        ],
    )
    by_coord_3 = {(w["sentence_index"], w["token_order"]): w for w in _cold_bundle(db_path, TARGET_ZID)["words"]}
    assert by_coord_3[(2, 0)]["lemma"] == "Stein"
    assert by_coord_3[(2, 0)]["word_destination"] == "stone"
    assert by_coord_3[(2, 0)]["ipa"] is None
    assert by_coord_3[(2, 0)]["word_provenance"] != "live:deepl"
    assert by_coord_3[(2, 0)]["extra_fields"] in (None, {})
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before


def test_save_session_direct_words_resolves_occurrence_provenance_without_token_order_collision(tmp_path):
    """Direct words caller with repeated token_order=0 resolves provenance by (sentence_index, token_order)."""
    db_path = tmp_path / "direct_words_prov.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    _seed_neighbor(adapter)
    neighbor_before = _cold_bundle(db_path, NEIGHBOR_ZID)

    adapter.save_session(
        session_zid=TARGET_ZID,
        slug="direct-prov",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Das Haus.\nDer Baum.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "The house."},
            {"sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "The tree."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": "house"},
            {"sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": "tree"},
        ],
        row_provenances={
            (1, 0): "live:google",
            (2, 0): "live:deepl",
        },
    )

    by_coord = {(w["sentence_index"], w["token_order"]): w for w in _cold_bundle(db_path, TARGET_ZID)["words"]}
    assert by_coord[(1, 0)]["word_provenance"] == "live:google"
    assert by_coord[(2, 0)]["word_provenance"] == "live:deepl"
    assert _cold_bundle(db_path, NEIGHBOR_ZID) == neighbor_before
