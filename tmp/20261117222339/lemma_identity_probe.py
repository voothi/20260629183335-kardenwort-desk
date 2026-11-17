import json

import kardenwort_desk as desk
from tests.render_test_support import seed_render_tsv
from tests.test_same_lemma_toggle import inject_mock_fetch


def test_repeated_separable_identity_projection(page, tmp_path):
    config, paths, _, _ = desk.load_config()
    config.set('sentences_mode', 'enabled', 'true')
    config.set('sentences_mode', 'delivery_mode', 'container')
    text = 'Er fängt heute an und er fängt morgen an.'
    zid = '20261117222339'
    tsv = tmp_path / f'{zid}-identity.de.tsv'
    tsv.write_text(
        '# comment\n'
        'Quotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n'
        f'fängt + an\tanfangen\tverb\tbegin\t1\t{text}\tHe begins.\t0\n',
        encoding='utf-8',
    )
    seed_render_tsv(config, paths, str(tsv), zid, text, 'de', text_mode='multi')
    html = desk.run_render_flow(
        text=text, language='de', zid=zid, text_mode='multi', config=config,
        resolved_paths=paths, theme='dark', tsv_path=str(tsv),
        spawn_children=False, seq_num=1, wordfill_cfg={'enabled': False},
    )
    page.set_content(inject_mock_fetch(html))
    page.wait_for_selector('#lemma-table tbody tr')
    row = page.locator('#lemma-table tbody tr').filter(has_text='anfangen').first
    row.click()
    tokens = page.locator('#source-container span.word').evaluate_all(
        "els => els.filter(e => ['fängt', 'an'].includes(e.textContent)).map(e => ({text:e.textContent, attributes:Object.fromEntries(Array.from(e.attributes).map(a=>[a.name,a.value]))}))"
    )
    print('RENDERED_OCCURRENCES=' + json.dumps(tokens, ensure_ascii=True))
    assert len(tokens) == 4
    assert all('highlight-purple-active' in t['attributes']['class'] for t in tokens)
    assert all('data-compound-id' not in t['attributes'] for t in tokens)
    manifest = page.locator('#token-map').text_content()
    print('TOKEN_MAP=' + manifest if manifest else 'TOKEN_MAP=empty')
