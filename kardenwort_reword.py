"""Pure Re-word identity resolution; visual positions never become write keys."""
from dataclasses import dataclass


class InvalidRewordTarget(ValueError):
    """Malformed or contradictory request, translated at the application boundary."""


@dataclass(frozen=True)
class RewordSelection:
    keys: tuple
    rows: tuple
    visuals: tuple
    visual_keys: tuple = ()


def index_value(value, minimum=0):
    text = str(value).strip()
    if isinstance(value, bool) or not text.isascii() or not text.isdigit():
        raise InvalidRewordTarget(f'Invalid coordinate: {value!r}')
    number = int(text)
    if number < minimum:
        raise InvalidRewordTarget(f'Coordinate below {minimum}: {number}')
    return number


def descriptors(value):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InvalidRewordTarget('Re-word targets must be a list of descriptors')
    kinds = {descriptor_kind(item) for item in value}
    if len(kinds) > 1:
        raise InvalidRewordTarget('Mixed visual and persistence descriptor formats')
    return value


def descriptor_kind(item):
    if 'token_order' in item:
        index_value(item.get('sentence_idx'), 1)
        index_value(item['token_order'])
        return 'occurrence'
    if 'visual_idx' in item:
        index_value(item['visual_idx'])
        if 'sentence_idx' in item:
            index_value(item['sentence_idx'])
        return 'visual'
    raise InvalidRewordTarget('Incomplete Re-word occurrence descriptor')


def manifest_columns(rows, headers, sentence_field):
    token_field = 'TokenOrder' if 'TokenOrder' in headers else 'token_order'
    if sentence_field not in headers or token_field not in headers:
        raise InvalidRewordTarget('Missing occurrence manifest columns')
    sent_col, token_col = headers.index(sentence_field), headers.index(token_field)
    if any(len(row) <= max(sent_col, token_col) for row in rows):
        raise InvalidRewordTarget('Incomplete occurrence manifest row')
    return sent_col, token_col


def restored_manifest_rows(restored):
    headers, rows = list(restored.get('headers', [])), restored.get('data_rows', [])
    if 'TokenOrder' in headers or 'token_order' in headers or 'words' not in restored:
        return headers, rows
    words = restored['words']
    if len(words) != len(rows):
        raise InvalidRewordTarget('Restored word manifest does not match its rows')
    return [*headers, 'TokenOrder'], [list(row) + [str(index_value(word['token_order']))]
                                     for row, word in zip(rows, words)]


def row_keys(rows, headers, sentence_field='SentenceSourceIndex'):
    sent_col, token_col = manifest_columns(rows, headers, sentence_field)
    keys = [(index_value(row[sent_col], 1), index_value(row[token_col])) for row in rows]
    if len(set(keys)) != len(keys):
        raise InvalidRewordTarget('Duplicate occurrence identity in manifest')
    return keys


def list_indices(values, size):
    if values is None:
        return None
    if not isinstance(values, list):
        raise InvalidRewordTarget('Selected rows must be a list')
    indices = tuple(dict.fromkeys(index_value(value) for value in values))
    if any(index >= size for index in indices):
        raise InvalidRewordTarget('Selected row absent from the rendered manifest')
    return indices


def coordinate_list(values, label):
    if values is None:
        return None
    if not isinstance(values, list):
        raise InvalidRewordTarget(label + ' must be a list')
    return tuple(dict.fromkeys(index_value(value) for value in values))


def validate_token_matches(keys, orders, matches, selected):
    if {keys[index][1] for index in matches} != orders:
        raise InvalidRewordTarget('Token selector contradicts the rendered rows')
    if selected is None and len(matches) != len(orders):
        raise InvalidRewordTarget('Ambiguous token order across sentences')
    if selected is not None and set(matches) != set(selected):
        raise InvalidRewordTarget('Token selector omits a selected row')


def token_indices(keys, tokens, selected):
    orders = coordinate_list(tokens, 'Token orders')
    if not orders:
        return selected
    candidates = range(len(keys)) if selected is None else selected
    matches = tuple(index for index in candidates if keys[index][1] in orders)
    validate_token_matches(keys, set(orders), matches, selected)
    return matches


def unique_visual(visual, manifest, allowed=None):
    matches = set(manifest.get(visual, ()))
    if allowed is not None:
        matches.intersection_update(allowed)
    if len(matches) != 1:
        raise ValueError(f'Coordinate alignment violation: target visual indices [{visual}] '
                         f'resolve to {len(matches)} occurrences')
    return next(iter(matches))


def request_visuals(targets, visual_indices):
    embedded = tuple(dict.fromkeys(index_value(item['visual_idx']) for item in targets if 'visual_idx' in item))
    explicit = coordinate_list(visual_indices, 'Visual indices')
    if embedded and explicit:
        agree(embedded, explicit)
    return explicit or embedded


def resolve_selection(rows, headers, targets=None, selected_rows=None, token_orders=None,
                      visual_indices=None, visual_manifest=None, all_rows=False,
                      sentence_field='SentenceSourceIndex'):
    targets = descriptors(targets)
    keys = row_keys(rows, headers, sentence_field)
    selected = token_indices(keys, token_orders, list_indices(selected_rows, len(rows)))
    visuals = request_visuals(targets, visual_indices)
    requested = occurrence_indices(targets, keys)
    requested, visual_keys = resolve_visual_selection(keys, requested, selected, visuals, visual_manifest)
    requested = agree(selected, requested)
    if requested is None:
        requested = tuple(range(len(rows))) if all_rows else ()
    return RewordSelection(tuple(keys[index] for index in requested), tuple(requested), visuals,
                           visual_keys)


def resolve_visual_selection(keys, requested, selected, visuals, manifest):
    if not visuals:
        return requested, ()
    allowed = requested if requested is not None else selected
    indices = tuple(unique_visual(value, manifest or {}, allowed) for value in visuals)
    requested = agree(requested, tuple(dict.fromkeys(indices)))
    return requested, tuple((keys[index], visual) for index, visual in zip(indices, visuals))


def occurrence_indices(targets, keys):
    if not targets or descriptor_kind(targets[0]) == 'visual':
        return None
    wanted = [(index_value(item['sentence_idx'], 1), index_value(item['token_order'])) for item in targets]
    return lookup_occurrences(wanted, keys)


def lookup_occurrences(wanted, keys):
    manifest = {key: index for index, key in enumerate(keys)}
    if any(key not in manifest for key in wanted):
        raise InvalidRewordTarget('Requested occurrence absent from the session manifest')
    return tuple(dict.fromkeys(manifest[key] for key in wanted))


def agree(first, second):
    if first is not None and second is not None and set(first) != set(second):
        raise InvalidRewordTarget('Contradictory Re-word selectors')
    return second if second is not None else first


def sentence_offsets(sentences, source_text, tokenize):
    offsets, cursor = {}, 0
    for sentence, text in sorted(sentences.items()):
        start = source_text.find(text, cursor)
        if not text or start < 0:
            raise ValueError('Coordinate alignment violation: source sentence absent from session text')
        offsets[sentence] = len(tokenize(source_text[:start]))
        cursor = start + len(text)
    return offsets


def row_forms(row, headers, lemma_field, quotation_field, article_forms):
    lemma = str(row[headers.index(lemma_field)]).strip().lower()
    quotation = str(row[headers.index(quotation_field)]) if quotation_field in headers else ''
    forms = {lemma, *(part.strip().lower() for part in quotation.split(','))}
    if lemma == 'der':
        forms.update(article_forms)
    return forms


def visual_manifest(rows, headers, source_text, tokenize, sentence_field='SentenceSourceIndex',
                    source_field='SentenceSource', lemma_field='WordSource', quotation_field='Quotation',
                    article_forms=()):
    keys = row_keys(rows, headers, sentence_field)
    sentences = source_sentences(rows, headers, keys, source_field, lemma_field, source_text)
    offsets = sentence_offsets(sentences, source_text, tokenize)
    positions = {sentence: visual_word_positions(tokenize(text)) for sentence, text in sentences.items()}
    result = {}
    for index, (key, row) in enumerate(zip(keys, rows)):
        forms = row_forms(row, headers, lemma_field, quotation_field, article_forms)
        add_visual_row(result, positions[key[0]], forms, offsets[key[0]], index)
    return result


def source_sentences(rows, headers, keys, source_field, lemma_field, source_text):
    if not source_text or source_field not in headers or lemma_field not in headers:
        raise ValueError('Coordinate alignment violation: missing authoritative source manifest')
    source_col = headers.index(source_field)
    sentences = {}
    for key, row in zip(keys, rows):
        text = str(row[source_col])
        if key[0] in sentences and sentences[key[0]] != text:
            raise ValueError('Coordinate alignment violation: conflicting source sentences')
        sentences[key[0]] = text
    return sentences


def visual_word_positions(tokens):
    result = {}
    for token in tokens:
        if token.get('is_word'):
            form = token.get('lower_clean', token['text'].lower())
            result.setdefault(form, []).append(token['visual_idx'])
    return result


def add_visual_row(result, positions, forms, offset, index):
    for form in forms:
        for visual in positions.get(form, ()):
            result.setdefault(offset + visual, []).append(index)


def provider_rows(original_rows, original_headers, updated_rows, updated_headers,
                  sentence_field='SentenceSourceIndex'):
    validate_provider_columns(updated_rows, updated_headers)
    original = row_keys(original_rows, original_headers, sentence_field)
    updated = row_keys(updated_rows, updated_headers, sentence_field)
    if set(original) != set(updated):
        raise InvalidRewordTarget('Provider changed the occurrence manifest')
    if not set(original_headers).issubset(updated_headers):
        raise InvalidRewordTarget('Provider removed required columns')
    return dict(zip(updated, updated_rows))


def validate_provider_columns(rows, headers):
    if len(set(headers)) != len(headers):
        raise InvalidRewordTarget('Duplicate provider columns')
    if any(len(row) != len(headers) for row in rows):
        raise InvalidRewordTarget('Incomplete provider result row')


def fast_provider_results(lemmas, values):
    if not isinstance(values, dict) or set(values) != set(lemmas):
        raise InvalidRewordTarget('Fast provider changed the requested lemma batch')
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise InvalidRewordTarget('Fast provider returned an incomplete translation batch')
    return values


def merge_rows(rows, headers, output, output_headers, selection, sentence_field='SentenceSourceIndex'):
    keys = row_keys(rows, headers, sentence_field)
    columns = [*headers, *(name for name in output_headers if name not in headers)]
    result = []
    for key, row in zip(keys, rows):
        values = dict(zip(headers, row))
        if key in selection.keys:
            values.update(zip(output_headers, output[key]))
        result.append([values.get(name, '') for name in columns])
    return columns, result


def project_occurrences(occurrences):
    """Read-only, ordered distinct display values; never used to build a write."""
    result = {}
    entries = [value for _, value in sorted(occurrences.items(), key=lambda item: index_value(item[0]))]
    for field, delimiter in [('trans', ', '), ('pos', ', '), ('morph', '; ')]:
        values = [entry[field] for entry in entries if entry.get(field)]
        result[field] = delimiter.join(dict.fromkeys(values))
    return result


def project_provenances(rows, headers, provenances, sentence_field='SentenceSourceIndex'):
    keys = row_keys(rows, headers, sentence_field)
    return {tuple(map(str, key)): provenances.get(index, provenances.get(str(index)))
            for index, key in enumerate(keys)}


def project_rows(rows, headers, fields, occurrences, sentence_field='SentenceSourceIndex'):
    if not occurrences:
        return [list(row) for row in rows]
    keys = row_keys(rows, headers, sentence_field)
    result = []
    for key, row in zip(keys, rows):
        values = dict(zip(headers, row))
        projection = project_occurrences(occurrences.get(tuple(map(str, key)), {}))
        values.update({fields[field]: value for field, value in projection.items()
                       if value})
        result.append([values[name] for name in headers])
    return result
