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


def _default_failure(value):
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text == '[FAILED]' or text.startswith('[Error'):
        return True
    return bool(text) and _is_rate_limit_failure(text)


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


def _clean_provenance(value):
    return value.strip() if isinstance(value, str) else ''


def _validated_wordfill_provenance(patch, provenance, existing_provenance=None):
    clean = _clean_provenance(provenance)
    if not patch or not clean:
        return ''
    if any(field == 'word_destination' for field, _, _ in patch):
        return clean
    return '' if _clean_provenance(existing_provenance) else clean


def wordfill_patch(row, headers, roles, match, is_failure=_default_failure, provenance=None, existing_provenance=None):
    patch = [
        field
        for definition in WORD_FIELDS
        if (field := wordfill_field(row, headers, roles, match, definition, is_failure=is_failure)) is not None
    ]
    resolved_provenance = _validated_wordfill_provenance(patch, provenance, existing_provenance=existing_provenance)
    if resolved_provenance:
        patch.append(('word_provenance', -1, resolved_provenance))
    return patch


def _apply_sqlite_wordfill_provenances(updates, existing_provenances, occurrence_keys, sess_row_provs, occurrence_provs):
    for key, value in (existing_provenances or {}).items():
        if value:
            occurrence_provs[key] = value
    for item in updates:
        wf_prov = item['updates'].get('word_provenance')
        if wf_prov:
            occurrence_provs[(str(item['sentence_index']), str(item['token_order']))] = wf_prov
    sess_row_provs.update(indexed_provenances(occurrence_keys, occurrence_provs))


def _apply_tsv_wordfill_provenances(updates, data_rows, col_token_order, sess_row_provs):
    for item in updates:
        wf_prov = item['updates'].get('word_provenance')
        row_idx = item['token_order']
        if wf_prov and 0 <= row_idx < len(data_rows):
            _record_row_provenance(data_rows[row_idx], row_idx, col_token_order, wf_prov, sess_row_provs)


def apply_wordfill_provenances(updates, existing_provenances, data_rows, occurrence_keys,
                               col_token_order, is_sqlite, sess_row_provs, occurrence_provs):
    if not updates:
        return
    if is_sqlite and occurrence_keys:
        _apply_sqlite_wordfill_provenances(
            updates, existing_provenances, occurrence_keys, sess_row_provs, occurrence_provs
        )
        return
    _apply_tsv_wordfill_provenances(updates, data_rows, col_token_order, sess_row_provs)


def eligible_wordfill(row, lemma_column, translation_column, is_unfinished=None):
    if not current_value(row, lemma_column):
        return False
    translation = current_value(row, translation_column)
    return is_unfinished(translation) if is_unfinished is not None else not translation


def _is_unfinished_cell(value):
    text = str(value or '').strip()
    return not text or 'skeleton-loader' in text or _default_failure(text)


def _token_aliases_safe(keys):
    tokens = [str(key[1]) for key in keys]
    if len(set(tokens)) != len(tokens):
        return False
    index_keys = {str(index) for index in range(len(keys))}
    return all(tok == str(index) or tok not in index_keys for index, tok in enumerate(tokens))


def indexed_provenances(keys, provenances):
    result = {}
    alias_tokens = _token_aliases_safe(keys)
    for index, key in enumerate(keys):
        value = provenances.get(tuple(map(str, key)))
        if not value:
            continue
        result[index] = value
        result[str(index)] = value
        if alias_tokens:
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
        return key + 1 if key >= 0 else None
    return int(key) + 1 if isinstance(key, str) and key.isascii() and key.isdigit() else None


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
    key_str = str(key)
    candidates = (key, key_str, int(key_str)) if (key_str.isascii() and key_str.isdigit()) else (key, key_str)
    for candidate in candidates:
        value = padded_dict.get(candidate)
        if isinstance(value, str) and value:
            return value
    return None


def _has_valid_sentence_dest(row):
    dest = str(row.get('sentence_destination') or '').strip()
    return bool(dest) and not _default_failure(dest)


def sentences_complete(db_sentences):
    sources = [row for row in db_sentences if str(row.get('sentence_source') or '').strip()]
    return bool(sources) and all(_has_valid_sentence_dest(row) for row in sources)


def _valid_sentence_entry(row):
    index = row.get('sentence_index')
    if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
        return None
    dest = str(row.get('sentence_destination') or '').strip()
    if not dest or _default_failure(dest):
        return None
    return index - 1, dest


def committed_translation_map(sentences):
    result = {}
    for row in sorted(sentences, key=lambda item: item.get('sentence_index', 1)):
        entry = _valid_sentence_entry(row)
        if entry is not None:
            result[entry[0]] = entry[1]
    return result


def sentence_summary_rows(db_sentences):
    rows = []
    for row in sorted(db_sentences, key=lambda item: item.get('sentence_index', 1)):
        entry = {
            'sentence_index': row.get('sentence_index', 1),
            'sentence_source': str(row.get('sentence_source') or '').strip(),
            'sentence_destination': str(row.get('sentence_destination') or '').strip(),
        }
        if row.get('text_provenance'):
            entry['text_provenance'] = row.get('text_provenance')
        rows.append(entry)
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


def startup_persistence_error(error):
    return {
        'code': 'ERR_PROGRESSIVE_STARTUP_FAILED',
        'message': str(error),
        'provider': 'storage',
        'details': {'phase': 'startup'},
    }


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


def lemma_stage_error(error, provider='controller', failed_lemmas=(), code='ERR_TRANSLATION_FAILED'):
    envelope = getattr(error, 'envelope', None)
    if isinstance(envelope, dict) and envelope:
        payload = dict(envelope)
        payload.setdefault('code', code)
        payload.setdefault('message', str(error))
        payload.setdefault('provider', provider)
    else:
        payload = dict(code=code, message=str(error), provider=provider)
    details = dict(payload.get('details') or {})
    if failed_lemmas:
        details['failed_lemmas'] = list(failed_lemmas)
    payload['details'] = details
    return payload


def is_untranslated_lemma(row, col_lemma, col_word_dest):
    if col_lemma < 0 or len(row) <= col_lemma:
        return None
    val = row[col_lemma].strip()
    if not val:
        return None
    if col_word_dest != -1 and len(row) > col_word_dest and not _is_unfinished_cell(row[col_word_dest]):
        return None
    return val


def extract_untranslated_lemmas(data_rows, col_lemma, col_word_dest):
    seen = set()
    lemmas = []
    for row in data_rows:
        val = is_untranslated_lemma(row, col_lemma, col_word_dest)
        if val and val not in seen:
            seen.add(val)
            lemmas.append(val)
    return lemmas


def _record_row_provenance(row, row_idx, col_token_order, chunk_prov, sess_row_provs, key=None, occurrence_provs=None):
    t_ord = current_value(row, col_token_order) or str(row_idx)
    sess_row_provs[t_ord] = chunk_prov
    if t_ord.isascii() and t_ord.isdigit():
        sess_row_provs[int(t_ord)] = chunk_prov
    if occurrence_provs is not None and key is not None:
        occurrence_provs[(str(key[0]), str(key[1]))] = chunk_prov


def _set_row_cell(row, col_word_dest, trans_val):
    while len(row) <= col_word_dest:
        row.append("")
    row[col_word_dest] = trans_val


def _chunk_occurrence_key(row_idx, occurrence_keys):
    if occurrence_keys and row_idx < len(occurrence_keys):
        return occurrence_keys[row_idx]
    return None


def _sqlite_word_update(key, trans_val, chunk_prov):
    return dict(
        sentence_index=key[0],
        token_order=key[1],
        updates=dict(word_destination=trans_val, word_provenance=chunk_prov),
    )


def _matched_chunk_rows(data_rows, col_lemma, col_word_dest, chunk_trans):
    matched = []
    for row_idx, row in enumerate(data_rows):
        if not _is_unfinished_cell(current_value(row, col_word_dest)):
            continue
        trans = chunk_trans.get(current_value(row, col_lemma))
        if trans and str(trans).strip():
            matched.append((row_idx, trans))
    return matched


def plan_chunk_word_updates(data_rows, col_lemma, col_word_dest, occurrence_keys, chunk_trans, chunk_prov, is_sqlite):
    if col_lemma < 0 or col_word_dest < 0:
        return [], []
    patches = []
    sqlite_updates = []
    for row_idx, trans in _matched_chunk_rows(data_rows, col_lemma, col_word_dest, chunk_trans):
        key = _chunk_occurrence_key(row_idx, occurrence_keys)
        patches.append((row_idx, trans, key))
        if is_sqlite and key is not None:
            sqlite_updates.append(_sqlite_word_update(key, trans, chunk_prov))
    return patches, sqlite_updates


def build_candidate_rows(data_rows, patches, col_word_dest):
    candidate = [list(row) for row in data_rows]
    for row_idx, trans_val, _ in patches:
        _set_row_cell(candidate[row_idx], col_word_dest, trans_val)
    return candidate


def apply_committed_chunk_patches(data_rows, patches, col_word_dest, col_token_order, chunk_prov, sess_row_provs, occurrence_provs=None):
    for row_idx, trans_val, key in patches:
        row = data_rows[row_idx]
        _set_row_cell(row, col_word_dest, trans_val)
        _record_row_provenance(row, row_idx, col_token_order, chunk_prov, sess_row_provs, key=key, occurrence_provs=occurrence_provs)


def translated_stage_event(rows, fingerprint, provenances, error, provider=None, sentences=None, text_provenance=None):
    event = dict(
        type='update',
        stage='translated',
        status='failed' if error else 'success',
        fingerprint=fingerprint,
        rows=rows,
        row_provenances=provenances,
        rowProvenances=provenances,
    )
    if error:
        event['error'] = error
    if provider:
        event.update(provider=provider, active_lemma_provider=provider, active_provider=provider)
    if sentences:
        event['sentences'] = sentences
    if text_provenance:
        event.update(text_provenance=text_provenance, textProvenance=text_provenance)
    return event
