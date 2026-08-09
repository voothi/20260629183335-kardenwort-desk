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

    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers_no_lemma, role_fields_no_lemma)
    assert result == child_rows, "Must return rows unchanged if col_lemma == -1"

# ---------------------------------------------------------------------------
# Task 2.3: test_cross_pollinate_api_error
# ---------------------------------------------------------------------------

def test_cross_pollinate_api_error(tmp_path):
    """
    Verify that if a child TSV has an API error message (e.g. HTTP 429) in its
    destination field, it correctly recognizes the field as empty and inherits
    from a sibling TSV.
    """
    child_name = "20260809190000-child.en.tsv"
    sibling_name = "20260809185900-sibling.en.tsv"

    headers = _make_headers()
    role_fields = _make_role_fields()

    # Child TSV: has the lemma but dest field contains API error
    child_rows = [
        ["Haus", "Error calling Gemini API: HTTP 429 Too Many Requests", "", ""],
    ]
    child_tsv = tmp_path / child_name
    child_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(child_rows[0]) + "\n",
        encoding="utf-8",
    )

    # Sibling TSV: same lemma, valid translation
    sibling_rows = [
        ["Haus", "house", "/haʊs/", "Noun, neuter"],
    ]
    sibling_tsv = tmp_path / sibling_name
    sibling_tsv.write_text(
        "\t".join(headers) + "\n" + "\t".join(sibling_rows[0]) + "\n",
        encoding="utf-8",
    )

    result = desk.cross_pollinate_from_siblings(child_tsv, child_rows, headers, role_fields)

    assert result[0][1] == "house", "WordDestination should overwrite API error from sibling"

# ---------------------------------------------------------------------------
# Task 2.4: test_cross_pollinate_intra_file
# ---------------------------------------------------------------------------

def test_cross_pollinate_intra_file(tmp_path):
    """
    Verify that if a TSV contains duplicate lemmas, and one row has the translation
    while the other row is missing it, the missing row is populated from the 
    populated row in the SAME file, without needing an external sibling file.
    """
    tsv_name = "20260809190000-intra.en.tsv"
    
    headers = _make_headers()
    role_fields = _make_role_fields()

    # Same file, two occurrences of "Haus".
    # Row 0: Translated
    # Row 1: Empty destination
    data_rows = [
        ["Haus", "house", "/haʊs/", "Noun, neuter"],
        ["Haus", "", "", ""],
    ]
    
    tsv_path = tmp_path / tsv_name
    
    # We must write the file because cross_pollinate_from_siblings uses load_tsv_rows / save_tsv_rows
    # to update the actual file on disk.
    tsv_path.write_text(
        "\t".join(headers) + "\n" + 
        "\t".join(data_rows[0]) + "\n" +
        "\t".join(data_rows[1]) + "\n",
        encoding="utf-8"
    )

    result = desk.cross_pollinate_from_siblings(tsv_path, data_rows, headers, role_fields)
    
    assert result[1][1] == "house", "Intra-file pollination failed: WordDestination not populated"
    assert result[1][2] == "/haʊs/", "Intra-file pollination failed: IPA not populated"
    assert result[1][3] == "Noun, neuter", "Intra-file pollination failed: Morphology not populated"
