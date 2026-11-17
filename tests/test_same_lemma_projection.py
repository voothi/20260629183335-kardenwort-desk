"""Contracts for transient exact source bindings; storage coordinates remain unchanged."""
import ast
from pathlib import Path

import pytest

import kardenwort_desk as desk
from tests.test_inflected_lemma_alignment import _create_render_test_env
from tests.sqlite_document_support import island
from tests.test_intent_architecture import complexity


@pytest.mark.parametrize('words, forms, sentences, expected', [
    (['er', 'fängt', 'an', 'und', 'er', 'fängt', 'an'], ['fängt', 'an'], [1]*7, [(1, 2), (5, 6)]),
    (['an', 'fängt', 'er', 'an', 'fängt', 'er'], ['an', 'fängt'], [1]*6, [(0, 1), (3, 4)]),
    (['fängt', 'an', 'fängt', 'an'], ['fängt', 'an'], [1, 1, 2, 2], [(0, 1), (2, 3)]),
    (['fängt', 'heute', 'an'], ['fängt', 'an'], [1, 1, 2], []),
    (['fängt'], ['fängt'], [1], []),
    (['fängt'], ['fängt', 'an'], [1], []),
    (['an', 'fängt'], ['fängt', 'an'], [1, 1], []),
])
def test_grouped_anchor_contract_preserves_ordinary_position_result(words, forms, sentences, expected):
    groups, ok = desk.resolve_anchored_positions(forms, words, 60, sentence_boundaries=sentences, grouped=True)
    assert groups == expected
    assert ok is bool(expected)
    positions, ordinary_ok = desk.resolve_anchored_positions(forms, words, 60, sentence_boundaries=sentences)
    assert positions == {position for group in expected for position in group}
    assert ordinary_ok is ok


def test_bindings_keep_distinct_row_owners_and_occurrence_visual_coordinates():
    assert desk.project_bound_visual_indices({4: [(0, 2), (3, 5)], 7: [(0, 2)]}, [1, 3, 5, 8, 10, 12]) == {
        0: {'4': [1, 5], '7': [1, 5]}, 2: {'4': [1, 5], '7': [1, 5]},
        3: {'4': [8, 12]}, 5: {'4': [8, 12]},
    }


@pytest.mark.parametrize('groups, message', [
    ({0: [(-1, 1)]}, 'outside document'),
    ({0: [(0, 99)]}, 'outside document'),
    ({0: [(0, 1), (0, 2)]}, 'Ambiguous source binding'),
])
def test_invalid_or_conflicting_bindings_fail_before_projection(groups, message):
    with pytest.raises(ValueError, match=message):
        desk.project_bound_visual_indices(groups, [1, 3, 5])


def test_explicit_particle_first_file_projection_keeps_exact_bindings(tmp_path):
    config, paths = _create_render_test_env(tmp_path)
    config.read_dict({'storage': {'backend': 'tsv'}})
    text = 'An fängt er heute, an fängt er morgen.'
    tsv = tmp_path/'20261117225056-explicit.de.tsv'
    tsv.write_text('Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tTokenOrder\tSentenceSourceIndex\n'
        'fängt + an\tanfangen\tfängt + an\tbegin\t0+1\t1\n'
        'fängt + an\tanfangen\tfängt + an\tbegin\t4+5\t1\n', encoding='utf-8')
    html = desk.run_render_flow(text=text, language='de', zid='20261117225056', text_mode='multi',
        config=config, resolved_paths=paths, tsv_path=str(tsv), spawn_children=False,
        wordfill_cfg={'enabled': False})
    manifest = island(html, 'token-map')
    mapped = [token for token in manifest if token.get('bound_visual_idxs_by_row')]
    assert [(token['text'], token['bound_visual_idxs_by_row']) for token in mapped] == [
        ('An', {'0': [1, 3]}), ('fängt', {'0': [1, 3]}),
        ('an', {'1': [10, 12]}), ('fängt', {'1': [10, 12]}),
    ]
    assert [token['token_order'] for token in mapped] == ['0+1', '0+1', '4+5', '4+5']
    assert [token['source_sentence_idx'] for token in mapped] == [1]*4


def test_new_binding_helpers_are_bounded_and_have_no_io():
    tree = ast.parse((Path(desk.__file__)).read_text(encoding='utf-8-sig'))
    names = {'_bind_source_visuals', 'project_bound_visual_indices'}
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    assert all(complexity(node) <= 7 for node in nodes)
    assert not any(isinstance(child, (ast.Import, ast.ImportFrom)) for node in nodes for child in ast.walk(node))
    calls = {child.func.id for node in nodes for child in ast.walk(node)
             if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)}
    assert calls.isdisjoint({'open', 'exec', 'eval', '__import__', 'print'})


def test_source_sentence_identity_does_not_inherit_the_shared_vocabulary_row(tmp_path):
    from tests.test_same_lemma_toggle import _row_highlight_document
    html, _ = _row_highlight_document(tmp_path, 'Er fängt heute an.\nEr fängt morgen an.',
        [('fängt + an', 'anfangen', 'verb', 1)])
    mapped = [token for token in island(html, 'token-map') if token.get('bound_visual_idxs_by_row')]
    assert [(token['source_sentence_idx'], token['sentence_idx']) for token in mapped] == [
        (1, 1), (1, 1), (2, 1), (2, 1),
    ]
    assert [token['row_ids'] for token in mapped] == [[0]]*4
    first_pair = [mapped[0]['visual_idx'], mapped[1]['visual_idx']]
    second_pair = [mapped[2]['visual_idx'], mapped[3]['visual_idx']]
    assert [token['bound_visual_idxs_by_row'] for token in mapped] == [
        {'0': first_pair}, {'0': first_pair}, {'0': second_pair}, {'0': second_pair},
    ]
