"""Committed card membership and restored local selection have separate baselines."""
import copy
import json
import re

import pytest

import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture, TRANSPORT, ZID, words


def initial_html(committed):
    html = render_fixture(desk, 2, 'container')
    bootstrap = json.loads(re.search(r'<script id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S).group(1))
    overview = bootstrap['sentence_cards'][0]
    children = []
    for index, word in enumerate(overview['words']):
        child = copy.deepcopy(overview)
        child.update(seq_num=index + 2, sentence_idx=index + 1, words=[copy.deepcopy(word)], selected_ids=[])
        if committed and index == 0:
            child['words'][0]['selected'] = '1'
            child['selected_ids'] = ['0']
            bootstrap['session_data']['data_rows'][0][5] = '1'
        children.append(child)
    bootstrap['sentence_cards'].extend(children)
    return desk.render_declarative_web_shell(bootstrap, theme='dark').replace('<head>', '<head>' + TRANSPORT)


@pytest.mark.parametrize('committed,local', [(False, 0), (True, None), (True, 1)])
def test_initial_membership_preserves_restored_local_intent(page, committed, local):
    html = initial_html(committed)
    if local is not None:
        item = dict(lemma=words(2)[local], atomic_id=local, row_ids=[local],
                    sentence_idx=local + 1, token_order=local, visual_idx=local + 1)
        page.add_init_script('sessionStorage.setItem(' + json.dumps('kw_active_tokens_' + ZID) + ',' +
                             json.dumps(json.dumps([item])) + ')')
    page.route('http://fixture.local/', lambda route: route.fulfill(content_type='text/html', body=html))
    page.goto('http://fixture.local/')
    for reload in (False, True):
        if reload:
            page.reload()
        page.wait_for_function('typeof getDeltas === "function" && WorkspaceTabs.getCards().length === 3')
        pending = page.evaluate('JSON.parse(getDeltas())')
        assert page.evaluate('isDirty()') == (local is not None)
        if local is None:
            assert pending == []
        else:
            assert pending
            assert all(delta['column'] == 'DeskSelected' and delta['row_id'] == local and delta['value'] == '1'
                       for delta in pending)
            assert page.evaluate('AppState.activeTokenSelections')[0]['lemma'] == words(2)[local]
