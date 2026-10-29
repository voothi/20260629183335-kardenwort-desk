"""20261029102523 phase 1: exact targets and scope through real HTTP/storage.

Governing requirements: Coordinate-Aware Reword Dispatch, Explicit Master
Overview Determination, Re-word Target Shape Validation Before Effects, and
Exact Occurrence Identity Across Re-word Provider Handoffs. These tests are
expected to expose baseline defects; no xfail or internal-domain mocks.
"""
import pytest

import kardenwort_desk as desk
from tests.reword_contract_support import (
    ZID, SIBLING, backend, provider_name, world, drain,
)


@pytest.mark.parametrize('invalid', [False, 0, '', {}], ids=['false', 'zero', 'empty-string', 'mapping'])
def test_false_non_list_target_payload_is_rejected_without_effects(world, invalid):
    before = world.snapshot()
    error = None
    try:
        world.arbiter.reword_session(ZID, selected_rows=[0], language='de', sentence_idx=1,
                                    target_coordinates=invalid)
    except desk.StructuredError as caught:
        error = caught
    assert (error.error_code if error else None, world.provider.calls, world.snapshot()) == (
        'INVALID_PAYLOAD', [], before)


@pytest.mark.parametrize('invalid', [False, {}], ids=['false', 'mapping'])
def test_invalid_http_target_does_not_mutate_real_storage(world, invalid):
    before = world.snapshot()
    status, payload = world.post(world.payload(target_coordinates=invalid, sentence_idx=1))
    assert (status, payload.get('error_code'), world.provider.calls, world.snapshot()) == (
        400, 'INVALID_PAYLOAD', [], before)


@pytest.mark.parametrize('position', ['other-word', 'absent'])
def test_visual_target_mismatch_does_not_mutate_selected_row(world, position):
    visual = world.visual('Baum') if position == 'other-word' else 99999
    before = world.snapshot()
    status, payload = world.post(world.payload(
        target_coordinates=[dict(visual_idx=visual, sentence_idx=1)], sentence_idx=1))
    assert (status >= 400, world.provider.calls, world.snapshot()) == (True, [], before), payload


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_valid_visual_reword_keeps_other_sqlite_occurrence_unchanged(world):
    status, payload = world.post(world.payload('visual', sentence_idx=1))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('shape', ['complete', 'visual', 'legacy'])
def test_http_reword_preserves_exact_source_target(world, shape):
    before = world.snapshot()
    status, payload = world.post(world.payload(shape, sentence_idx=1))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}
    assert len(world.provider.calls) == 1
    if world.backend == 'sqlite':
        other_before = next(word for word in before[ZID]['words'] if word['sentence_index'] == 2)
        other_after = next(word for word in world.snapshot()[ZID]['words'] if word['sentence_index'] == 2)
        assert other_after == other_before


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('shape', ['complete', 'visual', 'legacy'])
@pytest.mark.parametrize('scope', [None, 0, 2], ids=['omitted-master', 'explicit-master', 'child'])
def test_master_propagation_is_independent_of_target_shape(world, shape, scope):
    sibling_rows = [['7', 'Haus', 'Haus', '', '1', 'Haus.', '', ''],
                    ['8', 'Fremd', 'Fremd', 'keep-unrelated', '1', 'Fremd.', '', '']]
    world.seed(SIBLING, sibling_rows)
    populated, foreign = '20261029222451', '20261029222452'
    world.seed(populated, [['9', 'Haus', 'Haus', 'keep-existing', '1', 'Haus.', '', '']])
    world.seed(foreign, [['9', 'Haus', 'Haus', '', '1', 'Haus.', '', '']], language='en')
    channel = world.subscribe(SIBLING)
    body = world.payload(shape)
    if scope is not None:
        body['sentence_idx'] = scope
    status, payload = world.post(body)
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}
    expected = 'fresh-Haus' if scope in (None, 0) else ''
    assert world.values(SIBLING) == {(1, 7): expected, (1, 8): 'keep-unrelated'}
    assert world.values(populated) == {(1, 9): 'keep-existing'}
    assert world.values(foreign) == {(1, 9): ''}
    assert len(drain(channel)) == (1 if scope in (None, 0) else 0)


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('invalid_kind', ['false', 'mapping', 'visual-mismatch', 'visual-absent',
                                          'partial', 'mixed', 'later-unknown', 'contradictory-row'])
def test_http_invalid_batch_rejects_before_provider_or_storage(world, invalid_kind):
    valid = dict(sentence_idx=1, token_order=0)
    targets = {
        'false': False, 'mapping': {},
        'visual-mismatch': [dict(visual_idx=world.visual('Baum'), sentence_idx=1)],
        'visual-absent': [dict(visual_idx=99999)],
        'partial': [dict(sentence_idx=1)],
        'mixed': [dict(visual_idx=world.visual('Haus')), valid],
        'later-unknown': [valid, dict(sentence_idx=99, token_order=0)],
        'contradictory-row': [dict(sentence_idx=2, token_order=0)],
    }[invalid_kind]
    before = world.snapshot()
    status, payload = world.post(world.payload(target_coordinates=targets, sentence_idx=1))
    assert (status >= 400, world.provider.calls, world.snapshot()) == (True, [], before), payload


@pytest.mark.parametrize('invalid', [False, 0, '', {}], ids=['false', 'zero', 'empty-string', 'mapping'])
@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_direct_adapter_rejects_false_non_list_targets(world, invalid):
    before = world.snapshot()
    error = None
    try:
        world.adapter.enrich_session_intellifiller(ZID, 'fixture', selected_rows=[0],
                                                    target_coordinates=invalid, reprocess=True)
    except desk.StructuredError as caught:
        error = caught
    assert (error.error_code if error else None, world.provider.calls, world.snapshot()) == (
        'INVALID_PAYLOAD', [], before)


@pytest.mark.parametrize('coordinates', [None, []], ids=['none', 'empty-list'])
def test_valid_target_absence_keeps_row_selected_request(world, coordinates):
    status, payload = world.post(world.payload(target_coordinates=coordinates, sentence_idx=1))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_complete_identity_survives_reordered_render_rows(world):
    world.arbiter.sessions[ZID]['data_rows'].reverse()
    status, payload = world.post(world.payload(row_ids=[1], sentence_idx=1))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_provider_row_reordering_preserves_exact_writeback(world):
    world.provider.reverse = True
    status, payload = world.post(world.payload(sentence_idx=1))
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}
