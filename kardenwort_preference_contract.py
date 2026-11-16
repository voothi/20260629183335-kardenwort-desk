"""Pure input contract for the existing rendering-preference commands."""
from dataclasses import dataclass
import re


BOOLEAN_VALUES = {'true': True, '1': True, 'yes': True, 'on': True,
                  'false': False, '0': False, 'no': False, 'off': False}


@dataclass(frozen=True)
class PreferenceCommand:
    section: str
    key: str
    selected: object

    @property
    def value(self):
        return str(self.selected).lower() if isinstance(self.selected, bool) else str(self.selected)


def boolean_value(value):
    if isinstance(value, bool):
        return value
    if type(value) not in (str, int):
        raise ValueError('Preference value must be a boolean')
    normalized = str(value).strip().lower()
    if normalized not in BOOLEAN_VALUES:
        raise ValueError('Invalid boolean preference value')
    return BOOLEAN_VALUES[normalized]


PREFERENCES = {
    'highlight_same_lemma': ('rendering', False, 'bool'),
    'filter_selected_only': ('rendering', False, 'bool'),
    'auto_save_on_edit': ('ui', False, 'bool'),
    'auto_save_on_close': ('ui', True, 'bool'),
    'auto_inject_updates': ('rendering', True, 'bool'),
    'theme': ('ui', 'dark', 'theme'),
    'default_zoom': ('rendering', 100, 'zoom'),
    'default_language': ('settings', 'de', 'language'),
}


def language_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*', value):
        raise ValueError('Invalid concrete study language')
    if value.lower() == 'und':
        raise ValueError('Automatic language detection is not an active language')
    return value.lower()


def zoom_value(value):
    if isinstance(value, bool) or not str(value).isdigit():
        raise ValueError('Zoom must be a positive integer')
    result = int(value)
    if not 10 <= result <= 500:
        raise ValueError('Zoom must be between 10 and 500 percent')
    return result


def theme_value(value):
    if value not in ('dark', 'light', 'white', 'compact'):
        raise ValueError('Invalid theme')
    return value


def preference_value(key, value):
    if key not in PREFERENCES:
        raise ValueError('Preference key is not permitted')
    normalizers = {'bool': boolean_value, 'language': language_value, 'zoom': zoom_value, 'theme': theme_value}
    return normalizers[PREFERENCES[key][2]](value)


def configured_preferences(config, keys=None):
    result = {}
    for key in PREFERENCES if keys is None else keys:
        section, default, kind = PREFERENCES[key]
        raw = config.get(section, key) if config.has_option(section, key) else default
        result[key] = preference_value(key, raw)
    return result



def _explicit_key(raw):
    if not isinstance(raw, str):
        raise ValueError('Preference section and key must be strings')
    normalized = raw.strip().lower()
    if normalized not in PREFERENCES:
        raise ValueError('Preference key is not permitted')
    return normalized


def _alias_key(body):
    matches = []
    for raw in body:
        if isinstance(raw, str) and raw.strip().lower() in PREFERENCES:
            matches.append(raw)
    if len(matches) != 1:
        raise ValueError('Exactly one preference key is required')
    return matches[0]


def command_key(body):
    if 'key' in body:
        return _explicit_key(body['key'])
    return _alias_key(body)


def command_section(body, key):
    canonical = PREFERENCES[key][0]
    if 'section' not in body:
        return canonical
    section = body['section']
    if not isinstance(section, str):
        raise ValueError('Preference section and key must be strings')
    if section.strip().lower() != canonical:
        raise ValueError('Preference key is not permitted')
    return canonical


def parse_preference_command(body):
    if not isinstance(body, dict):
        raise ValueError('Preference command must be an object')
    raw_key = command_key(body)
    key = raw_key.strip().lower()
    section = command_section(body, key)
    if 'key' in body:
        if 'value' not in body:
            raise ValueError('Preference value is required')
        raw_value = body['value']
    else:
        raw_value = body[raw_key]
    return PreferenceCommand(section, key, preference_value(key, raw_value))
