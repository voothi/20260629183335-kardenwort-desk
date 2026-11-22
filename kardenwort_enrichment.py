"""Structured IntelliFiller dispatch and complete-batch validation; no file protocol."""
import kardenwort_reword as reword


def provider_options(config):
    options = {key: config.get('intellifiller', key, fallback=None)
               for key in ('model', 'base_url', 'api_key', 'prompt_template')}
    temperature = config.get('intellifiller', 'temperature', fallback=None)
    options['temperature'] = float(temperature) if temperature else None
    options['timeout'] = config.getfloat('timeouts', 'intellifiller_timeout', fallback=120)
    return options


def request_batch(rows, prompt, language, config, query, zid=None, trace_id=None):
    url = config.get('services', 'intellifiller_server_url', fallback='').strip()
    if not url:
        raise reword.InvalidRewordTarget('IntelliFiller structured endpoint is not configured')
    response = query(rows=rows, prompt=prompt, language=language, server_url=url,
                     zid=zid, trace_id=trace_id, **provider_options(config))
    if not isinstance(response, dict) or response.get('status') != 'success':
        raise reword.InvalidRewordTarget('IntelliFiller structured request failed')
    items = response.get('enriched_rows')
    fields = reword.http_provider_fields(items, [row['row_id'] for row in rows])
    originals = {row['row_id']: row for row in rows}
    return {reword.index_value(item['row_id']): checked_fields(
        item, originals[reword.index_value(item['row_id'])], fields[reword.index_value(item['row_id'])])
        for item in items}


def checked_fields(item, original, fields):
    if not fields:
        raise reword.InvalidRewordTarget('IntelliFiller returned no produced fields')
    if item.get('status') in ('error', 'failed'):
        raise reword.InvalidRewordTarget('IntelliFiller returned a failed row')
    result = {name: checked_value(name, item[name]) for name in fields}
    for name in ('TokenOrder', 'token_order', 'SentenceSourceIndex', 'sentence_index'):
        if name in result and result[name] != str(original.get(name, '')):
            raise reword.InvalidRewordTarget('IntelliFiller changed occurrence identity')
    return result


def checked_value(name, value):
    if value is not None and not isinstance(value, str):
        raise reword.InvalidRewordTarget('IntelliFiller field must be text or null')
    value = '' if value is None else value
    if name.lower().replace('_', '') == 'worddestination' and invalid_translation(value):
        raise reword.InvalidRewordTarget('IntelliFiller returned an empty or failed translation')
    if name in ('gender', 'WordSourceGender'):
        return normalized_gender(value)
    return value


def invalid_translation(value):
    return reword.is_unfinished_translation(value)


def normalized_gender(value):
    value = value.strip().lower()
    return value if value in ('m', 'f', 'n') else ''


PROMPT_TARGET_ROLES = {
    'ipa_extraction': ('ipa', 'WordSourceIPA'),
    'lemma_extraction': ('pos', 'WordSourcePOS'),
}


def target_column(roles, prompt=''):
    """Resolve the configured target column for ordinary (reprocess=False) eligibility."""
    key = str(prompt or '').strip().lower().replace(' ', '_').replace('-', '_')
    role, default = PROMPT_TARGET_ROLES.get(key, ('word_translation', 'WordDestination'))
    return (roles or {}).get(role, default)


def row_needs_enrichment(row, headers, column):
    if column not in headers:
        return True
    index = headers.index(column)
    value = row[index] if index < len(row) else ''
    return reword.is_unfinished_translation(value)


def _filtered_visuals(selection, retained_keys):
    if not selection.visual_keys:
        return (), ()
    visual_keys = tuple((key, visual) for key, visual in selection.visual_keys if key in retained_keys)
    visuals = tuple(dict.fromkeys(visual for _, visual in visual_keys))
    return visuals, visual_keys


def eligible_selection(rows, headers, selection, roles, prompt='', reprocess=False):
    """Filter selection rows, keys, and visuals in lockstep before provider dispatch."""
    if reprocess:
        return selection
    column = target_column(roles, prompt)
    kept = [(index, key) for index, key in zip(selection.rows, selection.keys)
            if row_needs_enrichment(rows[index], headers, column)]
    keys = tuple(key for _, key in kept)
    indices = tuple(index for index, _ in kept)
    visuals, visual_keys = _filtered_visuals(selection, set(keys))
    return reword.RewordSelection(keys, indices, visuals, visual_keys)


def enrich_rows(rows, headers, selection, language, prompt, config, query, zid=None, trace_id=None):
    batch = [dict(zip(headers, rows[index]), row_id=index) for index in selection.rows]
    patches = request_batch(batch, prompt, language, config, query, zid, trace_id)
    changes = {key: patches[index] for index, key in zip(selection.rows, selection.keys)}
    columns, output = reword.merge_changes(rows, headers, changes)
    return columns, output, {index: set(patch) for index, patch in patches.items()}


IDENTITY_COLUMNS = frozenset({
    'row_id',
    'TokenOrder',
    'token_order',
    'SentenceSourceIndex',
    'sentence_index',
    'DeskSelected',
})


def _identity_columns(roles):
    sentence_col = (roles or {}).get('sentence_index')
    return IDENTITY_COLUMNS | ({sentence_col} if sentence_col else set())


def _row_dict(headers, row, row_id):
    values = {name: (row[col] if col < len(row) else '') for col, name in enumerate(headers)}
    values['row_id'] = row_id
    return values


def canonical_batch(scratch, headers, selection):
    """Build normalized 0..N-1 batch rows for shared structured provider dispatch."""
    return [_row_dict(headers, scratch[index], pos) for pos, index in enumerate(selection.rows)]


def _canonical_row_key(row, identity_cols):
    return tuple((name, str(row[name])) for name in sorted(row) if name not in identity_cols)


def progressive_request_key(batch, prompt, source_language, target_language, config, roles=None):
    """Build an immutable equivalence key covering all meaning-affecting request inputs."""
    url = config.get('services', 'intellifiller_server_url', fallback='').strip()
    opts = provider_options(config)
    provider_key = (
        url,
        opts['model'],
        opts['base_url'],
        opts['api_key'],
        opts['prompt_template'],
        opts['temperature'],
    )
    role_key = tuple(sorted((str(k), str(v)) for k, v in (roles or {}).items()))
    identity_cols = _identity_columns(roles)
    rows_key = tuple(_canonical_row_key(row, identity_cols) for row in batch)
    return (
        provider_key,
        str(prompt or '').strip(),
        str(source_language or '').strip().lower(),
        str(target_language or '').strip().lower(),
        role_key,
        rows_key,
    )


def subscriber_changes(selection, roles, patches_by_pos, sanitize=None):
    """Map validated canonical batch patches onto one subscriber's exact occurrence keys."""
    if len(patches_by_pos) != len(selection.keys):
        raise reword.InvalidRewordTarget('Shared enrichment batch size does not match subscriber selection')
    changes = {}
    for pos, key in enumerate(selection.keys):
        if pos not in patches_by_pos:
            raise reword.InvalidRewordTarget('Shared enrichment batch omitted a subscriber position')
        raw_patch = patches_by_pos[pos]
        cleaned = {name: (sanitize(value) if sanitize else value) for name, value in raw_patch.items()}
        changes[key] = reword.provider_values({}, cleaned, roles, set(cleaned))
    return changes
