"""Fast providers must validate the full selected batch before any write/event."""
import pytest

import kardenwort_controller as controller
from kardenwort_desk import ProvenanceDict
import kardenwort_desk as desk
from tests.reword_contract_support import ZID, SIBLING, backend, provider_name, world, drain


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['google'])
@pytest.mark.parametrize('damage', ['missing', 'extra', 'empty', 'invalid'])
def test_invalid_fast_provider_batch_has_no_effect(world, monkeypatch, damage):
    def translate(lemmas, **kwargs):
        world.provider.calls.append(list(lemmas))
        result = {lemma: 'fresh-' + lemma for lemma in lemmas}
        if damage == 'missing':
            result.pop('Baum')
        elif damage == 'extra':
            result['Fremd'] = 'unexpected-result'
        elif damage == 'empty':
            result['Baum'] = ''
        else:
            result['Baum'] = None
        return result

    monkeypatch.setattr(controller, 'translate_lemmas_fast_path', translate)
    channel = world.subscribe(ZID)
    before = world.snapshot()
    status, payload = world.post(world.payload(row_ids=[0, 1], sentence_idx=1,
        target_coordinates=[dict(sentence_idx=1, token_order=0), dict(sentence_idx=2, token_order=0)]))
    assert status >= 400, payload
    assert len(world.provider.calls) == 1
    assert world.snapshot() == before
    assert drain(channel) == []


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name,reverse_output', [
    ('google', False), ('intellifiller', False), ('intellifiller', True)])
@pytest.mark.parametrize('shape', ['visual', 'complete', 'legacy'])
def test_reordered_render_and_provider_output_preserve_target(world, shape, reverse_output):
    world.provider.reverse = reverse_output
    world.arbiter.sessions[ZID]['data_rows'].reverse()
    before = world.snapshot()
    status, payload = world.post(world.payload(shape, row_ids=[1], sentence_idx=1))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}
    if world.backend == 'sqlite':
        assert world.snapshot()[ZID]['words'][1] == before[ZID]['words'][1]


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['google'])
def test_chunk_failover_provenance_survives_source_and_sibling_projection(world, monkeypatch):
    world.config.read_dict({'translation': {'lemma_batch_size': '1'}})
    world.seed(SIBLING, [['7', 'Haus', 'Haus', '', '1', 'Haus.', '', ''],
                         ['8', 'Baum', 'Baum', '', '2', 'Baum.', '', '']], text='Haus. Baum.')

    def translate(lemmas, **kwargs):
        lemma = lemmas[0]
        return ProvenanceDict({lemma: 'fresh-' + lemma},
                              provenance='live:argos' if lemma == 'Haus' else 'live:deepl')

    monkeypatch.setattr(controller, 'translate_lemmas_fast_path', translate)
    status, payload = world.post(world.payload(row_ids=[0, 1], sentence_idx=0,
        target_coordinates=[dict(sentence_idx=1, token_order=0), dict(sentence_idx=2, token_order=0)]))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'fresh-Baum'}
    assert world.values(SIBLING) == {(1, 7): 'fresh-Haus', (2, 8): 'fresh-Baum'}
    rows = payload.get('data', payload)['rows']
    assert [rows[str(i)]['provenance'] for i in range(2)] == ['live:argos', 'live:deepl']
    sibling = world.arbiter.sessions[SIBLING]['row_provenances']
    assert [sibling[i] for i in range(2)] == ['live:argos', 'live:deepl']
    if world.backend == 'sqlite':
        for zid in (ZID, SIBLING):
            words = world.adapter.db.get_words_by_session(zid)
            assert [word['word_provenance'] for word in words] == ['live:argos', 'live:deepl']


def test_restore_header_parity_and_view_identity_are_separate(world):
    # Model a stored session whose configured/exported fields omit TokenOrder.
    for word in world.adapter.db.get_words_by_session(ZID):
        extra = {name: value for name, value in word['extra_fields'].items()
                 if name.lower().replace('_', '') != 'tokenorder'}
        world.adapter.db.update_word(word['id'], {'extra_fields': extra})
    before = world.snapshot()
    restored = world.adapter.restore_session(ZID)
    assert 'TokenOrder' not in restored['headers']
    _, headers, rows = world.adapter.load_tsv_rows(world.path(ZID))
    assert 'TokenOrder' in headers
    assert [row[headers.index('TokenOrder')] for row in rows] == ['0', '0']
    assert [row[headers.index('SentenceSourceIndex')] for row in rows] == ['1', '2']
    assert world.snapshot() == before


def test_sqlite_save_mirror_keeps_database_occurrence_identity(world):
    for word in world.adapter.db.get_words_by_session(ZID):
        extra = {name: value for name, value in word['extra_fields'].items()
                 if name.lower().replace('_', '') != 'tokenorder'}
        world.adapter.db.update_word(word['id'], {'extra_fields': extra})
    comments, headers, rows = world.adapter.load_tsv_rows(world.path(ZID))
    desk.save_tsv_rows_safely(world.path(ZID), comments, headers, rows)
    world.arbiter.save_session(ZID, [dict(row_id=0, sentence_idx=1, token_order=0,
        column='WordDestination', value='edited-house')], language='de')
    _, mirrored_headers, mirrored_rows = desk.load_tsv_rows(world.path(ZID))
    assert [row[mirrored_headers.index('TokenOrder')] for row in mirrored_rows] == ['0', '0']
    assert world.values() == {(1, 0): 'edited-house', (2, 0): 'tree'}
