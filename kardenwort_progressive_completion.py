"""Pure completion projections and wordfill field selection."""
from dataclasses import dataclass


WORD_FIELDS = (
    ('word_translation', 'word_destination', ('WordDestination',)),
    ('ipa', 'ipa', ('WordSourceIPA',)),
    ('morphology', 'morphology', ('WordSourceMorphologyAI', 'WordSourceMorphology')),
    ('pos', 'pos', ('WordSourcePOS', 'POS')),
    ('gender', 'gender', ('WordSourceGender', 'Gender')),
)


@dataclass(frozen=True)
class CompletionRequest:
    session_zid: str
    language: str
    sqlite: bool
    path: object
    mapping: object
    headers: list
    rows: list
    roles: dict
    text_provenance: object
    error: object
    trace_id: str
    provenances: dict
    generation: object


@dataclass(frozen=True)
class CompletionSnapshot:
    restored: object
    headers: list
    rows: list
    roles: dict
    metadata: dict
    event: dict


def source_facts(restored):
    if restored is None:
        return None
    return tuple(restored.get(key) for key in ('session', 'sentences', 'words'))


def header_index(headers, names):
    return next((headers.index(name) for name in names if name in headers), -1)


def current_value(row, index):
    if index < 0 or index >= len(row):
        return ''
    return row[index].strip()


def _default_failure(value):
    return value == '[FAILED]' or value.startswith('[Error')


def incoming_value(match, names, is_failure=_default_failure):
    for name in names:
        value = str(match.get(name) or '').strip()
        if value and not is_failure(value):
            return value
    return ''


def wordfill_field(row, headers, roles, match, definition, is_failure=_default_failure):
    role, field, defaults = definition
    names = (roles[role], *defaults) if roles.get(role) else defaults
    index = header_index(headers, names)
    current = current_value(row, index)
    if current and not is_failure(current):
        return None
    value = incoming_value(match, names, is_failure=is_failure)
    return (field, index, value) if value else None


def wordfill_patch(row, headers, roles, match, is_failure=_default_failure):
    fields = (
        wordfill_field(row, headers, roles, match, definition, is_failure=is_failure)
        for definition in WORD_FIELDS
    )
    return [field for field in fields if field is not None]


def eligible_wordfill(row, lemma_column, translation_column, is_unfinished=None):
    if not current_value(row, lemma_column):
        return False
    translation = current_value(row, translation_column)
    return is_unfinished(translation) if is_unfinished is not None else not translation


def indexed_provenances(keys, provenances):
    result = {}
    unique_tokens = len({str(key[1]) for key in keys}) == len(keys)
    for index, key in enumerate(keys):
        value = provenances.get(tuple(map(str, key)))
        if not value:
            continue
        result[index] = value
        result[str(index)] = value
        if unique_tokens:
            result.setdefault(key[1], value)
            result.setdefault(str(key[1]), value)
    return result


def sentence_projection(sentences):
    return [dict(sentence_index=row['sentence_index'],
                 sentence_source=str(row.get('sentence_source') or '').strip(),
                 sentence_destination=str(row.get('sentence_destination') or '').strip(),
                 sentence_destination2=str(row.get('sentence_destination2') or '').strip(),
                 text_provenance=row.get('text_provenance'))
            for row in sorted(sentences, key=lambda row: row['sentence_index'])]


def text_provenance(sentences, previous):
    return next((row['text_provenance'] for row in sentences if row.get('text_provenance')), previous)


def finished_event(rows, fingerprint, provenances, error, sentences=None, provenance=None):
    event = dict(type='update', stage='finished', status='failed' if error else 'success',
                 error=error, fingerprint=fingerprint, rows=rows,
                 row_provenances=provenances, rowProvenances=provenances)
    if sentences:
        event['sentences'] = sentences
    if provenance:
        event.update(text_provenance=provenance, textProvenance=provenance)
    return event


def cache_projection(snapshot):
    event = snapshot.event
    result = dict(headers=snapshot.headers, data_rows=snapshot.rows,
                  fingerprint=event['fingerprint'], row_provenances=event['row_provenances'])
    if 'sentences' in event:
        result['sentences'] = event['sentences']
    if 'text_provenance' in event:
        result.update(text_provenance=event['text_provenance'], textProvenance=event['text_provenance'])
    return result


def _sentence_index(key):
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key + 1
    return int(key) + 1 if isinstance(key, str) and key.isdigit() else None


def sentence_entries(raw):
    if not isinstance(raw, dict):
        return []
    entries = []
    for key, value in raw.items():
        index = _sentence_index(key)
        if index is not None and isinstance(value, str) and value:
            entries.append((key, index, value))
    return sorted(entries, key=lambda item: item[1])


def padded_value(padded_dict, key):
    if not isinstance(padded_dict, dict):
        return None
    candidates = (key, str(key), int(key)) if str(key).isdigit() else (key, str(key))
    for candidate in candidates:
        value = padded_dict.get(candidate)
        if isinstance(value, str) and value:
            return value
    return None


def sentences_complete(db_sentences):
    sources = [row for row in db_sentences if str(row.get('sentence_source') or '').strip()]
    return bool(sources) and all(str(row.get('sentence_destination') or '').strip() for row in sources)


def committed_translation_map(sentences):
    result = {}
    for row in sorted(sentences, key=lambda item: item.get('sentence_index', 1)):
        index = row.get('sentence_index')
        dest = str(row.get('sentence_destination') or '').strip()
        if isinstance(index, int) and index > 0 and dest:
            result[index - 1] = dest
    return result


def sentence_summary_rows(db_sentences):
    rows = []
    for row in sorted(db_sentences, key=lambda item: item.get('sentence_index', 1)):
        rows.append({
            'sentence_index': row.get('sentence_index', 1),
            'sentence_source': str(row.get('sentence_source') or '').strip(),
            'sentence_destination': str(row.get('sentence_destination') or '').strip(),
        })
    return rows


def committed_entries_summary(raw, committed_indices, fallback_prov):
    entries = [item for item in sentence_entries(raw) if item[1] in committed_indices]
    sentences = [{'sentence_index': idx, 'sentence_source': '', 'sentence_destination': val.strip()} for _, idx, val in entries]
    committed_map = {idx - 1: val.strip() for _, idx, val in entries}
    return sentences, committed_map, (fallback_prov if committed_map else None)


def sentence_stage_error(error, provider='controller', failed_sentences=(), code='ERR_SENTENCE_TRANSLATION_FAILED'):
    envelope = getattr(error, 'envelope', None)
    if isinstance(envelope, dict) and envelope:
        payload = dict(envelope)
        payload.setdefault('code', code)
        payload.setdefault('message', str(error))
        payload.setdefault('provider', provider)
    else:
        payload = dict(code=code, message=str(error), provider=provider)
    details = dict(payload.get('details') or {})
    if failed_sentences:
        details['failed_sentences'] = list(failed_sentences)
    payload['details'] = details
    return payload


def combine_worker_errors(primary, secondary):
    if not primary:
        return secondary
    if not secondary:
        return primary
    combined = dict(primary)
    details = dict(combined.get('details') or {})
    details['subsequent_error'] = secondary
    combined['details'] = details
    return combined


def translated_text_event(rows, fingerprint, translated_html, sentences, error, provider=None, provenance=None):
    failed = error is not None
    status = 'failed' if failed else 'success'
    event = dict(
        type='update',
        stage='translated_text',
        status=status,
        text_translation_status=status,
        textTranslationStatus=status,
        text_translation_failed=failed,
        textTranslationFailed=failed,
        fingerprint=fingerprint,
        rows=rows,
        translated_text=translated_html,
        translatedText=translated_html,
        sentences=sentences,
    )
    if failed:
        event['error'] = error
    if provider:
        event.update(provider=provider, active_text_provider=provider, active_provider=provider)
    if provenance:
        event.update(text_provenance=provenance, textProvenance=provenance)
    return event
