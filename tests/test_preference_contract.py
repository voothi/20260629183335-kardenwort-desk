"""Pure command vocabulary and typed normalization, without storage doubles."""
import pytest

from kardenwort_preference_contract import parse_preference_command


@pytest.mark.parametrize('value,expected', [('false', False), ('no', False), ('off', False),
    ('0', False), (0, False), (False, False), (' true ', True), ('YES', True)])
def test_boolean_vocabulary_is_explicit(value, expected):
    result = parse_preference_command(dict(section='rendering', key='filter_selected_only', value=value))
    assert result.section == 'rendering' and result.key == 'filter_selected_only'
    assert result.selected is expected
    assert result.value == ('true' if expected else 'false')


@pytest.mark.parametrize('body', [None, [], True, 'true', {},
    {'filter_selected_only': True, 'highlight_same_lemma': True},
    {'section': [], 'key': 'filter_selected_only', 'value': True},
    {'section': 'rendering', 'key': ['filter_selected_only'], 'value': True}])
def test_malformed_or_ambiguous_command_is_rejected(body):
    with pytest.raises(ValueError):
        parse_preference_command(body)
