"""Pure input contract for the existing rendering-preference commands."""
from dataclasses import dataclass


WRITABLE_KEYS = frozenset({'highlight_same_lemma', 'filter_selected_only'})
BOOLEAN_VALUES = {'true': True, '1': True, 'yes': True, 'on': True,
                  'false': False, '0': False, 'no': False, 'off': False}


@dataclass(frozen=True)
class PreferenceCommand:
    section: str
    key: str
    selected: bool

    @property
    def value(self):
        return 'true' if self.selected else 'false'


def boolean_value(value):
    if isinstance(value, bool):
        return value
    if type(value) not in (str, int):
        raise ValueError('Preference value must be a boolean')
    normalized = str(value).strip().lower()
    if normalized not in BOOLEAN_VALUES:
        raise ValueError('Invalid boolean preference value')
    return BOOLEAN_VALUES[normalized]


def command_key(body):
    key = body.get('key')
    if key:
        return key
    aliases = WRITABLE_KEYS.intersection(body)
    if len(aliases) != 1:
        raise ValueError('Exactly one preference key is required')
    return next(iter(aliases))


def parse_preference_command(body):
    if not isinstance(body, dict):
        raise ValueError('Preference command must be an object')
    section, key = body.get('section', 'rendering'), command_key(body)
    if not isinstance(section, str) or not isinstance(key, str):
        raise ValueError('Preference section and key must be strings')
    section, key = section.strip().lower(), key.strip().lower()
    if section != 'rendering' or key not in WRITABLE_KEYS:
        raise ValueError('Preference key is not permitted')
    value = body.get('value') if body.get('key') else body[key]
    return PreferenceCommand(section, key, boolean_value(value))
