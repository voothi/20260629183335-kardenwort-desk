"""Complete provider manifests, legacy ambiguity and exact sibling persistence."""
import pytest

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.reword_contract_support import ZID, SIBLING, backend, provider_name, world, drain


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'changed', 'short', 'failure'])
def test_invalid_provider_batch_has_no_persisted_effect(world, monkeypatch, damage):
    original = world.provider.enrich

    def damaged_response(tsv_path, *args, **kwargs):
        original(tsv_path, *args, **kwargs)
        comments, headers, rows = desk.load_tsv_rows(tsv_path)
        if damage == 'missing':
            rows.pop()
        elif damage == 'duplicate':
            rows.append(list(rows[0]))
        elif damage == 'changed':
            rows[-1][headers.index('SentenceSourceIndex')] = '99'
        elif damage == 'short':
            rows[-1].pop()
        desk.save_tsv_rows_safely(tsv_path, comments, headers, rows)
        return damage != 'failure'

    module = desk if world.backend == 'sqlite' else controller
    monkeypatch.setattr(module, 'run_headless_intellifiller', damaged_response)
    channel = world.subscribe(ZID)
    before = world.snapshot()
    status, payload = world.post(world.payload())
    assert status >= 400, payload
    assert len(world.provider.calls) == 1
    assert world.snapshot() == before
    assert drain(channel) == []


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_bare_token_selector_rejects_cross_sentence_ambiguity(world):
    before = world.snapshot()
    status, payload = world.post(world.payload(row_ids=None, target_coordinates=None, token_orders=[0]))
    assert status >= 400, payload
    assert (world.provider.calls, world.snapshot()) == ([], before)


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_numeric_strings_resolve_exact_identity(world):
    status, payload = world.post(world.payload(row_ids=['0'], sentence_idx='1',
        target_coordinates=[dict(sentence_idx='1', token_order='0')]))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_sibling_fill_preserves_populated_duplicate_token_in_other_sentence(world):
    world.seed(SIBLING, [['0', 'Haus', 'Haus', '', '1', 'Haus.', '', ''],
                         ['0', 'Haus', 'Haus', 'keep', '2', 'Haus.', '/keep/', 'keep-morph']], text='Haus. Haus.')
    before = world.adapter.db.get_words_by_session(SIBLING)[1]
    status, payload = world.post(world.payload(sentence_idx=0))
    assert status == 200, payload
    assert world.values(SIBLING) == {(1, 0): 'fresh-Haus', (2, 0): 'keep'}
    after = world.adapter.db.get_words_by_session(SIBLING)[1]
    assert after['word_destination'] == before['word_destination']
    assert after['word_provenance'] == before['word_provenance']


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_visual_update_refreshes_raw_occurrence_and_projected_response(world):
    words = world.adapter.db.get_words_by_session(ZID)
    visual = str(world.visual('Haus'))
    extra = {'occurrence_data': {visual: {'trans': 'old-house', 'pos': 'n.'}}}
    world.adapter.db.update_word(words[0]['id'], {'extra_fields': extra})
    status, payload = world.post(world.payload('visual', sentence_idx=1))
    assert status == 200, payload
    words = world.adapter.db.get_words_by_session(ZID)
    assert words[0]['word_destination'] == 'fresh-Haus'
    assert words[0]['extra_fields']['occurrence_data'][visual]['trans'] == 'fresh-Haus'
    rows = payload.get('data', payload)['rows']
    assert rows['0']['trans'] == 'fresh-Haus'
    assert rows['0']['provenance'] == 'live:' + world.config.get('pipeline', 'lemma_reprocess_provider')
    assert rows['1']['provenance'] == 'fixture:initial'


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_returned_provenance_remains_scoped_with_duplicate_token_orders(world):
    status, payload = world.post(world.payload(sentence_idx=1))
    assert status == 200, payload
    rows = payload.get('data', payload)['rows']
    assert rows['0']['provenance'] == 'live:' + world.config.get('pipeline', 'lemma_reprocess_provider')
    assert rows['1']['provenance'] == 'fixture:initial'


@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_ephemeral_prompt_preserves_literal_source_brackets(world):
    source = 'Haus [alt].'
    world.seed(ZID, [['0', 'Haus', 'Haus', 'house', '1', source, '', '']], text=source)
    status, payload = world.post(world.payload('visual', sentence_idx=1))
    assert status == 200, payload
    _, headers, rows = world.adapter.load_tsv_rows(world.path(ZID))
    assert rows[0][headers.index('SentenceSource')] == source
    assert '[Haus]' in world.provider.calls[0][0]['SentenceSource']


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_legacy_row_and_token_select_only_the_resolved_sentence(world):
    status, payload = world.post(world.payload(row_ids=[1], token_orders=[0],
                                              target_coordinates=None, sentence_idx=2))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'house', (2, 0): 'fresh-Baum'}


@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('targets', [None, []], ids=['none', 'empty-list'])
def test_intentional_whole_session_adapter_call_keeps_its_scope(world, targets):
    assert world.adapter.enrich_session_intellifiller(ZID, 'fixture', target_coordinates=targets)
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'fresh-Baum'}
