"""Pure translation identity validation; metadata never supplies sentence values."""


def sentence_index(key):
    if isinstance(key, bool) or not str(key).isascii() or not str(key).isdigit():
        raise ValueError('Invalid sentence identity')
    return int(key)


def sentence_value(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Sentence translation must be non-empty text')
    return value


def numeric_sentence_entries(entries, metadata=()):
    if not isinstance(entries, dict):
        raise ValueError('Sentence entries must be a mapping')
    result = {}
    for key, value in entries.items():
        if key in metadata:
            continue
        index = sentence_index(key) + 1
        if index in result:
            raise ValueError('Duplicate sentence identity')
        result[index] = sentence_value(value)
    return result


def normalize_sentence_result(raw, single_unit=False):
    if isinstance(raw, str):
        if not single_unit or not raw.strip():
            raise ValueError('Scalar translation requires one confirmed unit')
        return {1: raw}, {}
    main = numeric_sentence_entries(raw, ('FULL_TEXT', 'PADDED'))
    if not main:
        raise ValueError('Translation output has no aligned sentence entries')
    padded = numeric_sentence_entries(raw.get('PADDED', {}))
    if not set(padded).issubset(main):
        raise ValueError('Padded sentence identities do not match main entries')
    return main, padded


def _is_word_char(ch):
    return ch.isalnum() or ch == '_'


def _ends_http(token):
    return token.endswith('http') and (len(token) == 4 or not _is_word_char(token[-5]))


def _starts_429(token):
    return token.startswith('429') and (len(token) == 3 or not _is_word_char(token[3]))


def _is_provider_http_429(parts):
    if parts[:2] != ['error', 'calling']:
        return False
    return any(_ends_http(left) and _starts_429(right) for left, right in zip(parts[2:], parts[3:]))


def _is_rate_limit_failure(text):
    if '\n' in text or '\r' in text:
        return False
    parts = text.lower().split()
    return parts == ['http', '429', 'too', 'many', 'requests'] or _is_provider_http_429(parts)


def failed_sentence_text(value):
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text == '[FAILED]' or text.startswith(('[Error', '[Translation Error')):
        return True
    return bool(text) and _is_rate_limit_failure(text)


def unresolved_sentences(records):
    result = []
    for record in records:
        value = record.get('sentence_destination') or ''
        if not value.strip() or failed_sentence_text(value):
            result.append(record)
    return result


def align_retry_sentences(raw, records):
    main, padded = normalize_sentence_result(raw, single_unit=len(records) == 1)
    if set(main) != set(range(1, len(records) + 1)):
        raise ValueError('Retry translation does not match requested sentence units')
    mapped = {record['sentence_index']: main[index+1] for index, record in enumerate(records)}
    context = {record['sentence_index']: padded[index+1] for index, record in enumerate(records) if index+1 in padded}
    return mapped, context


def _display_entry_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Sentence translation must be non-empty text')
    cleaned = value.strip()
    return None if not cleaned or failed_sentence_text(cleaned) else cleaned


def display_sentence_text(entries):
    if not isinstance(entries, dict):
        raise ValueError('Sentence entries must be a mapping')
    main = {}
    seen = set()
    for key, value in entries.items():
        if key in ('FULL_TEXT', 'PADDED'):
            continue
        index = sentence_index(key) + 1
        if index in seen:
            raise ValueError('Duplicate sentence identity')
        seen.add(index)
        text = _display_entry_text(value)
        if text is not None:
            main[index] = text
    return ' '.join(main[index] for index in sorted(main))


def validate_complete_alignment(main, records):
    identities = [record['sentence_index'] for record in records]
    if len(set(identities)) != len(identities) or set(main) != set(identities):
        raise ValueError('Translation identities do not match authoritative sentences')


def validate_authoritative_records(records):
    identities = set()
    for record in records:
        index = record['sentence_index']
        if type(index) is not int or index < 1 or index in identities:
            raise ValueError('Invalid authoritative sentence identity')
        sentence_value(record['sentence_source'])
        identities.add(index)


def sentence_sources(records):
    return '\n'.join(record['sentence_source'] for record in records)


def persistence_index(key, existing):
    index = sentence_index(key)
    if index < 1 or index in existing:
        raise ValueError('Persistence requires unique positive sentence identities')
    return index


def persistence_map(entries):
    if not isinstance(entries, dict):
        raise ValueError('Persistence requires a one-based sentence map')
    result = {}
    for key, value in entries.items():
        if key in ('FULL_TEXT', 'PADDED'):
            continue
        index = persistence_index(key, result)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError('Persistence requires text values')
        result[index] = value
    return result


def column_index(headers, name):
    return headers.index(name) if name in headers else None


def row_sentence_index(row, column, main):
    if column is None:
        return next(iter(main))
    if column >= len(row):
        return None
    value = str(row[column]).strip()
    return int(value) if value.isascii() and value.isdigit() else None


def patch_destination(row, column, value):
    if column is None or column >= len(row):
        return
    if value is None or not value.strip():
        return
    row[column] = value


def patch_translation_rows(rows, headers, roles, main, padded):
    sentence_column = column_index(headers, roles.get('sentence_index', 'SentenceSourceIndex'))
    if sentence_column is None and len(main) > 1:
        raise ValueError('Cannot align multi-sentence retext without SentenceSourceIndex column')
    destination = column_index(headers, roles.get('sentence_destination', 'SentenceDestination'))
    destination2 = column_index(headers, 'SentenceDestination2')
    result = [list(row) for row in rows]
    for row in result:
        index = row_sentence_index(row, sentence_column, main)
        patch_destination(row, destination, main.get(index))
        patch_destination(row, destination2, padded.get(index))
    return result
