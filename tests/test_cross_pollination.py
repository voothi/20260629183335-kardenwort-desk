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
        "word_ipa": "WordSourceIPA",
        "word_morphology_ai": "WordSourceMorphologyAI",
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
