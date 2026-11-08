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
    result = {name: checked_value(name, item[name]) for name in fields}
    for name in ('TokenOrder', 'token_order', 'SentenceSourceIndex', 'sentence_index', 'WordSource'):
        if name in result and result[name] != str(original.get(name, '')):
            raise reword.InvalidRewordTarget('IntelliFiller changed occurrence identity')
    return result


def checked_value(name, value):
    if value is not None and not isinstance(value, str):
        raise reword.InvalidRewordTarget('IntelliFiller field must be text or null')
    value = '' if value is None else value
    if name.lower().replace('_', '') == 'worddestination' and not value.strip():
        raise reword.InvalidRewordTarget('IntelliFiller returned an empty translation')
    if name in ('gender', 'WordSourceGender'):
        return normalized_gender(value)
    return value


def normalized_gender(value):
    value = value.strip().lower()
    return value if value in ('m', 'f', 'n') else ''


def enrich_rows(rows, headers, selection, language, prompt, config, query, zid=None, trace_id=None):
    batch = [dict(zip(headers, rows[index]), row_id=index) for index in selection.rows]
    patches = request_batch(batch, prompt, language, config, query, zid, trace_id)
    changes = {key: patches[index] for index, key in zip(selection.rows, selection.keys)}
    columns, output = reword.merge_changes(rows, headers, changes)
    return columns, output, {index: set(patch) for index, patch in patches.items()}
