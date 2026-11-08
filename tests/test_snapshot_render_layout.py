"""A committed snapshot must not force layout once per empty metadata cell."""
import json
import re

import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture, words


def test_empty_snapshot_cells_do_not_force_per_row_layout(page):
    count = 64
    html = render_fixture(desk, count, 'container')
    boot = json.loads(re.search(
        r'<script[^>]*id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S).group(1))
    # This component contract uses the real renderer/store; transport is disabled.
    boot['server_enabled'] = False
    boot['session_data']['server_enabled'] = False
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.set_content(desk.render_declarative_web_shell(boot, theme='dark'))
    page.wait_for_function('typeof toggleRowSelection === "function" && window.AppView')
    page.evaluate('toggleRowSelection(0, true)')
    geometry = '''() => Array.from(document.querySelectorAll(
        '#source-container .word,#lemma-table tr,#kw-action-toolbar button'
    )).map(el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})'''
    before = page.evaluate(geometry)
    monitor = page.context.new_cdp_session(page)
    monitor.send('Performance.enable')
    layouts_before = next(item['value'] for item in monitor.send('Performance.getMetrics')['metrics']
                          if item['name'] == 'LayoutCount')
    rows = {str(index): dict(row_id=index, token_order=index, sentence_idx=index+1,
            lemma=lemma, inflected=lemma, trans='translation', pos='', gender='', ipa='', morph='')
            for index, lemma in enumerate(words(count))}
    page.evaluate('receiveUpdate', dict(stage='finished', rows=rows))
    after = page.evaluate(geometry)
    layouts_after = next(item['value'] for item in monitor.send('Performance.getMetrics')['metrics']
                         if item['name'] == 'LayoutCount')
    assert len(before) == len(after)
    assert all(abs(a-b) < .01 for left, right in zip(before, after) for a, b in zip(left, right))
    assert page.locator('#lemma-table tr[data-row-id="0"]').get_attribute('data-selected') == '1'
    assert page.locator('#lemma-table tbody tr').count() == count
    assert page.locator('#lemma-table td.col-pos').all_text_contents() == [''] * count
    assert page.locator('#lemma-table td.col-gender').all_text_contents() == [''] * count
    assert page.evaluate('JSON.parse(getDeltas()).some(delta => delta.column === "DeskSelected" && delta.row_id === 0 && delta.value === "1")')
    # Count browser layout work, not machine-dependent elapsed milliseconds.
    assert layouts_after - layouts_before <= 4, (layouts_before, layouts_after)
