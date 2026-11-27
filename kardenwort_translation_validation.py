"""Pure stored translation failure classification without broad substring matching."""
import re


_EXACT_HTTP_429_RE = re.compile(r'^HTTP\s+429\s+Too\s+Many\s+Requests\Z', re.IGNORECASE)
_PROVIDER_HTTP_429_RE = re.compile(r'^Error\s+calling\s+[^\r\n]*\bHTTP\s+429\b[^\r\n]*\Z', re.IGNORECASE)


def is_stored_rate_limit_failure(value) -> bool:
    """Return True only for confirmed whole-field HTTP 429 rate-limit failure strings."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return bool(_EXACT_HTTP_429_RE.match(text) or _PROVIDER_HTTP_429_RE.match(text))


def is_stored_translation_failure(value) -> bool:
    """Return True for recognized stored translation failure markers and HTTP 429 failures."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text == '[FAILED]' or text.startswith(('[Error', '[Translation Error')):
        return True
    return is_stored_rate_limit_failure(text)


def is_unfinished_translation(value) -> bool:
    """Return True when a translation field is empty, pending skeleton, or a stored failure."""
    if value is None:
        return True
    text = str(value).strip()
    if not text or 'skeleton-loader' in text:
        return True
    return is_stored_translation_failure(text)


def is_field_empty(row, col_idx: int) -> bool:
    """Evaluate whether a tabular translation cell is unfinished/empty."""
    if col_idx < 0 or row is None or col_idx >= len(row):
        return True
    return is_unfinished_translation(row[col_idx])


def sanitize_translation_value(value) -> str:
    """Project recognized stored translation failure values as empty string without mutating storage."""
    if value is None:
        return ''
    text = str(value)
    return '' if is_stored_translation_failure(text) else text


def sanitize_occurrence_translations(occurrence):
    """Return a shallow projection copy of occurrence_data with stored failure translations cleared."""
    if not isinstance(occurrence, dict):
        return occurrence
    cleaned = {}
    for key, entry in occurrence.items():
        if isinstance(entry, dict) and 'trans' in entry:
            copy_entry = dict(entry)
            copy_entry['trans'] = sanitize_translation_value(entry.get('trans'))
            cleaned[key] = copy_entry
        else:
            cleaned[key] = entry
    return cleaned
