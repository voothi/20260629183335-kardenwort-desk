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


def incoming_value(match, names):
    for name in names:
        value = str(match.get(name) or '').strip()
        if value and value != '[FAILED]' and not value.startswith('[Error'):
            return value
    return ''


def wordfill_field(row, headers, roles, match, definition):
    role, field, defaults = definition
    names = (roles[role], *defaults) if roles.get(role) else defaults
    index = header_index(headers, names)
    if current_value(row, index):
        return None
    value = incoming_value(match, names)
    return (field, index, value) if value else None


def wordfill_patch(row, headers, roles, match):
    fields = (wordfill_field(row, headers, roles, match, definition) for definition in WORD_FIELDS)
    return [field for field in fields if field is not None]


def eligible_wordfill(row, lemma_column, translation_column):
    return bool(current_value(row, lemma_column)) and not current_value(row, translation_column)


def indexed_provenances(keys, provenances):
    result = {}
    for index, key in enumerate(keys):
        value = provenances.get(tuple(map(str, key)))
        if value:
            for alias in (index, str(index), key[1], str(key[1])):
                result[alias] = value
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
