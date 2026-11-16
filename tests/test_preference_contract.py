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
    {'section': 'rendering', 'key': ['filter_selected_only'], 'value': True},
    {'key': None, 'theme': 'light'},
    {'key': '', 'theme': 'light'},
    {'key': 'theme'},
    {'theme': 'light', 'THEME': 'dark'},
    {'section': None, 'key': 'theme', 'value': 'light'},
    {'section': 'rendering', 'key': 'theme', 'value': 'light'},
    {'section': 'ui', 'default_zoom': 125}])
def test_malformed_or_ambiguous_command_is_rejected(body):
    with pytest.raises(ValueError):
        parse_preference_command(body)


@pytest.mark.parametrize('key,value,expected_section,expected_selected', [
    ('auto_save_on_edit', True, 'ui', True),
    ('highlight_same_lemma', 'yes', 'rendering', True),
    ('theme', 'light', 'ui', 'light'),
    ('default_zoom', '125', 'rendering', 125),
    ('default_language', 'en-US', 'settings', 'en-us'),
])
def test_omitted_section_infers_canonical_section_for_all_kinds(key, value, expected_section, expected_selected):
    keyed = parse_preference_command(dict(key=key, value=value))
    assert keyed.section == expected_section and keyed.key == key and keyed.selected == expected_selected
    aliased = parse_preference_command({f' {key.upper()} ': value})
    assert aliased.section == expected_section and aliased.key == key and aliased.selected == expected_selected
