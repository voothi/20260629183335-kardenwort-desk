"""Snapshots project all rows without repeated document-wide identity scans."""
import json
import re

import pytest
import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture, words


@pytest.mark.parametrize('count', [128, 512])
def test_snapshot_row_lookup_work_is_bounded(page, count):
    html = render_fixture(desk, count, 'container')
    boot = json.loads(re.search(
        r'<script[^>]*id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S).group(1))
    boot['server_enabled'] = False
    boot['session_data']['server_enabled'] = False
    page.set_content(desk.render_declarative_web_shell(boot, theme='dark'))
    page.wait_for_function('window.AppView && typeof receiveUpdate === "function"')
    rows = {str(index): dict(row_id=index, token_order=index, sentence_idx=index+1,
            lemma=lemma, inflected=lemma, trans='updated-'+str(index), pos='', gender='')
            for index, lemma in enumerate(words(count))}
    queries = page.evaluate(r"""rows => {
        const original = document.querySelector;
        let queries = 0;
        document.querySelector = function(selector) {
            if (/^tr\[data-(token-order|row-id)=/.test(selector)) queries++;
            return original.call(this, selector);
        };
        try { receiveUpdate({stage:'finished', rows}); }
        finally { document.querySelector = original; }
        return queries;
    }""", rows)
    assert page.locator('#lemma-table tbody tr[data-row-id]').count() == count
    assert page.locator('#lemma-table td.col-translation .scrollable-cell').all_text_contents() == [
        'updated-'+str(index) for index in range(count)]
    assert page.evaluate('!isDirty()')
    # Observe DOM reads, preserving actual query results; no machine-time threshold.
    assert queries <= 4, queries
