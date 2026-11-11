"""The display consumes aligned text; provider metadata never becomes content."""
import pytest
from kardenwort_alignment import display_sentence_text


def test_padded_metadata_and_full_text_are_not_displayed_or_duplicated():
    assert display_sentence_text({1:' second ', 0:'first', 'FULL_TEXT':'first second',
                                  'PADDED':{0:'context first context', 1:'context second context'}}) == 'first second'


def test_pending_and_failed_entries_are_not_displayed():
    assert display_sentence_text({0:None, 1:'', 2:'[FAILED]', 3:'[Error: offline]', 4:'ready'}) == 'ready'


@pytest.mark.parametrize('entries', [{0: {'text':'fabricated'}}, {0:'first', '0':'duplicate'}, {'unknown':'text'}])
def test_malformed_or_ambiguous_sentence_results_fail(entries):
    with pytest.raises(ValueError):
        display_sentence_text(entries)
