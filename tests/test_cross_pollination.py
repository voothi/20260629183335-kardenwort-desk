"""
Tests for cross_pollinate_from_siblings — verifying that a child progressive window
correctly inherits field values (WordDestination, WordSourceIPA, WordSourceMorphologyAI)
from a sibling TSV, and fails safely when column mappings are missing.
"""
import pytest
from pathlib import Path
from unittest.mock import patch
import kardenwort_desk as desk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_role_fields():
    return {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "ipa": "WordSourceIPA",
        "morphology": "WordSourceMorphologyAI",
    }


def _make_headers():
    return ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]


# ---------------------------------------------------------------------------
# Task 2.1: test_cross_pollinate_basic
# ---------------------------------------------------------------------------

def test_cross_pollinate_basic(tmp_path):
    """
    Verify that a child TSV with a missing WordDestination, WordSourceIPA, and
    WordSourceMorphologyAI for a given lemma ('Haus') correctly inherits those
    field values from a sibling TSV that has already computed them.
    """
    child_name = "20260809190000-child.en.tsv"
    sibling_name = "20260809185900-sibling.en.tsv"

    headers = _make_headers()
    role_fields = _make_role_fields()

    # Child TSV: has the lemma but all enrichment fields are empty
    child_rows = [
        ["Haus", "", "", ""],
    ]
    child_tsv = tmp_path / child_name
    child_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(child_rows[0]) + "\n",
        encoding="utf-8",
    )

    # Sibling TSV: same lemma, all fields filled
    sibling_rows = [
        ["Haus", "house", "/haʊs/", "Noun, neuter"],
    ]
    sibling_tsv = tmp_path / sibling_name
    sibling_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(sibling_rows[0]) + "\n",
        encoding="utf-8",
    )

    # Call the function — it reads sibling_tsv from disk and merges into child_rows
    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers, role_fields)

    assert result[0][1] == "house",      "WordDestination should be inherited from sibling"
    assert result[0][2] == "/haʊs/",     "WordSourceIPA should be inherited from sibling"
    assert result[0][3] == "Noun, neuter", "WordSourceMorphologyAI should be inherited from sibling"


# ---------------------------------------------------------------------------
# Task 2.2: test_cross_pollinate_bounds
# ---------------------------------------------------------------------------

def test_cross_pollinate_bounds(tmp_path):
    """
    Verify that cross_pollinate_from_siblings fails gracefully (returns data_rows
    unchanged) when the child TSV does not have a WordSource (lemma) column at all,
    making col_lemma == -1 and preventing any merge attempt.
    """
    child_name = "20260809190000-child.en.tsv"
    sibling_name = "20260809185900-sibling.en.tsv"

    # Headers that lack WordSource entirely
    headers_no_lemma = ["WordDestination", "WordSourceIPA"]
    role_fields_no_lemma = {
        "lemma": "WordSource",  # points to a col that doesn't exist → col_lemma == -1
        "word_translation": "WordDestination",
        "word_ipa": "WordSourceIPA",
    }

    child_rows = [["", ""],]
    child_tsv = tmp_path / child_name
    child_tsv.write_text(
        "\t".join(headers_no_lemma) + "\n" + "\t".join(child_rows[0]) + "\n",
        encoding="utf-8",
    )

    # Create a valid sibling with data (it should still be ignored when col_lemma == -1)
    sibling_tsv = tmp_path / sibling_name
    sibling_tsv.write_text(
        "WordSource\tWordDestination\tWordSourceIPA\n"
        "Haus\thouse\t/haʊs/\n",
        encoding="utf-8",
    )

    # Should return data_rows unchanged — no merge, no exception
    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers_no_lemma, role_fields_no_lemma)

    assert result == child_rows, (
        "cross_pollinate_from_siblings must return data_rows unchanged when "
        "col_lemma == -1 (no WordSource column in child TSV)"
    )


# ---------------------------------------------------------------------------
# Task 2.3: test_cross_pollinate_ignores_failed_markers
# ---------------------------------------------------------------------------

def test_cross_pollinate_ignores_failed_markers(tmp_path):
    """
    Verify that cross_pollinate_from_siblings explicitly ignores [FAILED] and
    error indicators from sibling TSVs so they are not copied to child rows.
    """
    child_name = "20260809190005-child.en.tsv"
    sibling_name = "20260809190000-sibling.en.tsv"

    headers = _make_headers()
    role_fields = _make_role_fields()

    child_rows = [
        ["Haus", "", "", ""],
    ]
    child_tsv = tmp_path / child_name
    child_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(child_rows[0]) + "\n",
        encoding="utf-8",
    )

    # Sibling has [FAILED] or error string
    sibling_rows = [
        ["Haus", "[FAILED]", "[Error: 429]", "[FAILED]"],
    ]
    sibling_tsv = tmp_path / sibling_name
    sibling_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(sibling_rows[0]) + "\n",
        encoding="utf-8",
    )

    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers, role_fields)

    assert result[0][1] == "", "Failed WordDestination must not be copied to child"
    assert result[0][2] == "", "Failed WordSourceIPA must not be copied to child"
    assert result[0][3] == "", "Failed WordSourceMorphologyAI must not be copied to child"


# ---------------------------------------------------------------------------
# Task 2.4: test_cross_pollinate_heals_failed_cells
# ---------------------------------------------------------------------------

def test_cross_pollinate_heals_failed_cells(tmp_path):
    """
    Verify that if a target child TSV has [FAILED] in a cell, and a healthy sibling
    has a valid translation, cross_pollinate_from_siblings overwrites [FAILED] with
    the healthy sibling value.
    """
    child_name = "20260809190005-child.en.tsv"
    sibling_name = "20260809190000-sibling.en.tsv"

    headers = _make_headers()
    role_fields = _make_role_fields()

    child_rows = [
        ["Haus", "[FAILED]", "", ""],
    ]
    child_tsv = tmp_path / child_name
    child_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(child_rows[0]) + "\n",
        encoding="utf-8",
    )

    sibling_rows = [
        ["Haus", "house", "/haʊs/", "Noun, neuter"],
    ]
    sibling_tsv = tmp_path / sibling_name
    sibling_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(sibling_rows[0]) + "\n",
        encoding="utf-8",
    )

    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers, role_fields)

    assert result[0][1] == "house", "Child [FAILED] cell should be healed by healthy sibling"
    assert result[0][2] == "/haʊs/", "Child IPA should be populated from healthy sibling"


# ---------------------------------------------------------------------------
# Task 2.5: Fast-path delimiter & fallback tests
# ---------------------------------------------------------------------------

def test_translate_lemmas_fast_path_newline_delimiter(monkeypatch):
    """
    Verify that translate_lemmas_fast_path successfully splits newline-delimited
    translations if semicolons were converted to newlines by the translation engine.
    """
    lemmas = ["Haus", "Buch", "Tisch"]
    
    def mock_translate_text(text, source, target, config, resolved_paths, provider):
        if ";" in text:
            # Engine returned newlines instead of semicolons
            return "house\nbook\ntable"
        return text + "-trans"

    monkeypatch.setattr(desk, 'translate_text', mock_translate_text)

    res = desk.translate_lemmas_fast_path(lemmas, "de", "en", None, None, "google")
    assert res == {"Haus": "house", "Buch": "book", "Tisch": "table"}


def test_translate_lemmas_fast_path_individual_fallback(monkeypatch):
    """
    Verify that if fast-path batch translation completely fails alignment,
    individual calls are made and populate the returned dictionary for each lemma.
    """
    lemmas = ["Haus", "Buch"]

    calls = []
    def mock_translate_text(text, source, target, config, resolved_paths, provider):
        calls.append(text)
        if ";" in text:
            return "malformed single string response"
        if text == "Haus":
            return "house"
        if text == "Buch":
            return "book"
        return text

    monkeypatch.setattr(desk, 'translate_text', mock_translate_text)

    res = desk.translate_lemmas_fast_path(lemmas, "de", "en", None, None, "google")
    assert res == {"Haus": "house", "Buch": "book"}
    assert "Haus" in calls
    assert "Buch" in calls


# ---------------------------------------------------------------------------
# Task 2.6: Sibling cross-pollination strictly excludes sentence-level fields
# ---------------------------------------------------------------------------

def test_cross_pollinate_excludes_sentence_level_fields(tmp_path):
    """
    Verify that cross_pollinate_from_siblings copies word-level fields
    (WordDestination, WordSourceIPA, WordSourceMorphologyAI) but NEVER copies
    or overwrites sentence-level fields (SentenceSource, SentenceDestination,
    SentenceSourceContextPrevious, Quotation, TextSource) even if the target has empty
    or different sentence fields.
    """
    child_name = "20260809190005-child.en.tsv"
    sibling_name = "20260809190000-sibling.en.tsv"

    headers = [
        "WordSource",
        "WordDestination",
        "WordSourceIPA",
        "WordSourceMorphologyAI",
        "SentenceSource",
        "SentenceDestination",
        "SentenceSourceContextPrevious",
        "Quotation",
        "TextSource",
    ]
    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "ipa": "WordSourceIPA",
        "morphology": "WordSourceMorphologyAI",
        "sentence_source": "SentenceSource",
        "sentence_destination": "SentenceDestination",
    }

    # Child TSV: has "Haus" in Sentence B ("Das ist mein Haus."), translation missing
    child_rows = [
        [
            "Haus",
            "",
            "",
            "",
            "Das ist mein Haus.",
            "That is my house.",
            "Vorheriger Satz.",
            "Zitat B",
            "Text Quelle B",
        ],
    ]
    child_tsv = tmp_path / child_name
    child_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(child_rows[0]) + "\n",
        encoding="utf-8",
    )

    # Sibling TSV: has "Haus" in Sentence A ("Ein altes Haus steht dort.")
    sibling_rows = [
        [
            "Haus",
            "house",
            "/haʊs/",
            "Noun, neuter",
            "Ein altes Haus steht dort.",
            "An old house stands there.",
            "Anderer Kontext.",
            "Zitat A",
            "Text Quelle A",
        ],
    ]
    sibling_tsv = tmp_path / sibling_name
    sibling_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(sibling_rows[0]) + "\n",
        encoding="utf-8",
    )

    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers, role_fields)

    # Word-level fields SHOULD be populated
    assert result[0][1] == "house", "WordDestination should be cross-pollinated"
    assert result[0][2] == "/haʊs/", "WordSourceIPA should be cross-pollinated"
    assert result[0][3] == "Noun, neuter", "WordSourceMorphologyAI should be cross-pollinated"

    # Sentence-level fields MUST NOT be overwritten by sibling
    assert result[0][4] == "Das ist mein Haus.", "SentenceSource must NOT be overwritten by sibling"
    assert result[0][5] == "That is my house.", "SentenceDestination must NOT be overwritten by sibling"
    assert result[0][6] == "Vorheriger Satz.", "SentenceSourceContextPrevious must NOT be overwritten by sibling"
    assert result[0][7] == "Zitat B", "Quotation must NOT be overwritten by sibling"
    assert result[0][8] == "Text Quelle B", "TextSource must NOT be overwritten by sibling"

