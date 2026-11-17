"""Temporary Phase 1 review evidence; permanent regression work is Phase 2."""
import json

import pytest
import kardenwort_desk as desk
from tests.render_test_support import seed_render_tsv
from tests.test_same_lemma_toggle import inject_mock_fetch, get_multi_occurrence_desk_page_html


def render(page, tmp_path, text, rows, theme='dark', file_host=False):
    config, paths, _, _ = desk.load_config()
    config.set('sentences_mode', 'enabled', 'true')
    config.set('sentences_mode', 'delivery_mode', 'container')
    config.set('rendering', 'highlight_same_lemma', 'false')
    from kardenwort_state_runtime import preference_store
    preference_store(config, paths).set('highlight_same_lemma', False)
    zid = '20261117223156'
    tsv = tmp_path / f'{zid}-phase1.de.tsv'
    tsv.write_text(
        '# comment\nQuotation\tWordSource\tWordSourcePOS\tWordDestination\tTokenOrder\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n'
        + ''.join(f'{quotation}\t{lemma}\t{pos}\tmeaning\t{order}\t1\t{text}\tTranslation.\t0\n' for quotation, lemma, pos, order in rows),
        encoding='utf-8',
    )
    if file_host:
        config.set('storage', 'backend', 'tsv')
        paths['storage_backend'] = 'tsv'
    else:
        seed_render_tsv(config, paths, str(tsv), zid, text, 'de', text_mode='multi')
    html = desk.run_render_flow(
        text=text, language='de', zid=zid, text_mode='multi', config=config,
        resolved_paths=paths, theme=theme, tsv_path=str(tsv),
        spawn_children=False, seq_num=1, wordfill_cfg={'enabled': False},
    )
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector('#lemma-table tbody tr')


def roles(page, words):
    return page.locator('#source-container span.word').evaluate_all(
        "(els, words) => els.filter(e => words.includes(e.textContent.toLowerCase())).map(e => e.classList.contains('highlight-purple-active') || e.classList.contains('highlight-orange-active') ? 'primary' : e.classList.contains('lemma-peer-highlight') ? 'peer' : 'baseline')", words)


@pytest.mark.parametrize('theme', ['dark', 'light', 'white'])
@pytest.mark.parametrize('particle_first', [False, True])
def test_bound_occurrences_and_geometry(page, tmp_path, theme, particle_first):
    text = 'An fängt er heute, an fängt er morgen.' if particle_first else 'Er fängt heute an und er fängt morgen an.'
    quotation = 'An + fängt' if particle_first else 'fängt + an'
    render(page, tmp_path, text, [(quotation, 'anfangen', 'verb', '0')], theme)
    page.locator('#lemma-table tbody tr').first.click()
    geometry = "() => Array.from(document.querySelectorAll('#source-container span.word, .kw-action-toolbar, #kw-btn-same-lemma, #kw-btn-filter-selected')).map(e => {const r=e.getBoundingClientRect(); return [e.textContent,r.x,r.y,r.width,r.height];})"
    before = page.evaluate(geometry)
    assert roles(page, ['fängt', 'an']) == ['primary', 'primary', 'baseline', 'baseline']
    manifest = json.loads(page.locator('#token-map').text_content())
    bindings = [t['bound_visual_idxs_by_row']['0'] for t in manifest if t.get('bound_visual_idxs_by_row')]
    assert bindings[0] == bindings[1]
    assert bindings[2] == bindings[3]
    assert bindings[0] != bindings[2]
    membership = page.evaluate('getSelectedRowsArray()')
    page.locator('#kw-btn-same-lemma').click()
    assert roles(page, ['fängt', 'an']) == ['primary', 'primary', 'peer', 'peer']
    on = page.evaluate(geometry)
    assert on == before
    assert page.evaluate('getSelectedRowsArray()') == membership
    page.locator('#kw-btn-same-lemma').click()
    after = page.evaluate(geometry)
    assert after == before
    assert roles(page, ['fängt', 'an']) == ['primary', 'primary', 'baseline', 'baseline']
    (tmp_path / 'geometry.json').write_text(json.dumps({'off': before, 'on': on, 'off_again': after}), encoding='utf-8')
    print(f'GEOMETRY {theme} particle_first={particle_first}: {len(before)} boxes identical OFF/ON/OFF')


def test_explicit_particle_first_binding(page, tmp_path):
    render(page, tmp_path, 'An fängt er heute, an fängt er morgen.', [
        ('fängt + an', 'anfangen', 'verb', '0+1'),
        ('fängt + an', 'anfangen', 'verb', '4+5'),
    ], file_host=True)
    page.locator('#lemma-table tbody tr').first.click()
    manifest = json.loads(page.locator('#token-map').text_content())
    bindings = [t['bound_visual_idxs_by_row'] for t in manifest if t.get('bound_visual_idxs_by_row')]
    assert bindings == [{'0': [1, 3]}, {'0': [1, 3]}, {'1': [10, 12]}, {'1': [10, 12]}]
    assert page.evaluate('getSelectedRowsArray()') == [0, 1]
    # This single-sentence file host exposes two distinct selected table rows.
    assert roles(page, ['fängt', 'an']) == ['primary', 'primary', 'primary', 'primary']
    page.locator('#kw-btn-same-lemma').click()
    assert roles(page, ['fängt', 'an']) == ['primary', 'primary', 'primary', 'primary']


def test_two_selected_groups_and_compound_siblings(page, tmp_path):
    render(page, tmp_path, 'E-Mail bleibt E-Mail und Haus bleibt Haus.', [
        ('E-Mail', 'E-Mail', 'noun', '0'), ('Haus', 'Haus', 'noun', '1')])
    page.locator('#lemma-table tbody tr').filter(has_text='E-Mail').first.click()
    page.locator('#lemma-table tbody tr').filter(has_text='Haus').first.click()
    assert roles(page, ['e', 'mail']) == ['primary', 'primary', 'baseline', 'baseline']
    assert roles(page, ['haus']) == ['primary', 'baseline']
    page.locator('#kw-btn-same-lemma').click()
    assert roles(page, ['e', 'mail']) == ['primary', 'primary', 'peer', 'peer']
    assert roles(page, ['haus']) == ['primary', 'peer']


def test_later_sentence_owns_primary(page, tmp_path):
    page.set_content(inject_mock_fetch(get_multi_occurrence_desk_page_html(tmp_path)))
    page.wait_for_selector('#lemma-table tbody tr')
    page.locator('#lemma-table tbody tr').first.click()
    assert roles(page, ['am', 'beim']) == ['primary', 'baseline']
    page.evaluate('WorkspaceTabs.switchToTab(WorkspaceTabs.getCards().find(c => c.sentence_idx === 2).seq_num)')
    if page.evaluate('getSelectedRowsArray().length'):
        page.locator('#lemma-table tbody tr').first.click()
    page.locator('#lemma-table tbody tr').first.click()
    assert roles(page, ['am', 'beim']) == ['baseline', 'primary']
    # Retain an overview canonical ID that has no span in this sentence projection.
    page.locator('#lemma-table tbody tr').first.evaluate("row => {row.setAttribute('data-row-id', '0'); row.setAttribute('data-all-row-ids', '0,1');}")
    page.evaluate('updateBidirectionalHighlights()')
    assert roles(page, ['am', 'beim']) == ['baseline', 'primary']
    page.evaluate('WorkspaceTabs.switchToTab(WorkspaceTabs.getCards().find(c => c.sentence_idx === 0).seq_num)')
    assert roles(page, ['am', 'beim']) == ['primary', 'baseline']


def test_grouped_anchor_api():
    words = ['er', 'fängt', 'heute', 'an', 'und', 'er', 'fängt', 'morgen', 'an']
    groups, ok = desk.resolve_anchored_positions(['fängt', 'an'], words, 60, grouped=True)
    assert ok and groups == [(1, 3), (6, 8)]
    positions, ok = desk.resolve_anchored_positions(['fängt', 'an'], words, 60)
    assert ok and positions == {1, 3, 6, 8}
    assert desk.project_bound_visual_indices({0: groups}, list(range(len(words)))) == {
        1: {'0': [1, 3]}, 3: {'0': [1, 3]}, 6: {'0': [6, 8]}, 8: {'0': [6, 8]}}
    with pytest.raises(ValueError, match='outside document'):
        desk.project_bound_visual_indices({0: [(1, 99)]}, [0, 1])
    with pytest.raises(ValueError, match='Ambiguous source binding'):
        desk.project_bound_visual_indices({0: [(0, 1), (0, 2)]}, [0, 1, 2])
