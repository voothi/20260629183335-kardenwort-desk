"""Committed card membership and restored local selection have separate baselines."""
import copy
import json
import re

import pytest

from tests.test_preference_commands import preference_world  # noqa: F401
from tests.sqlite_document_support import serve_document, wait_state, island
import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture, TRANSPORT, ZID, words


def initial_html(committed, transport=True):
    html = render_fixture(desk, 2, 'container')
    bootstrap = json.loads(re.search(r'<script id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S).group(1))
    overview = bootstrap['sentence_cards'][0]
    bootstrap['token_manifest'] = [dict(visual_idx=i+1, sentence_idx=i+1, token_order=i,
        atomic_id=i, row_ids=[i], lemma=word) for i, word in enumerate(words(2))]
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
    html = desk.render_declarative_web_shell(bootstrap, theme='dark')
    return html.replace('<head>', '<head>' + TRANSPORT) if transport else html


@pytest.mark.parametrize('committed,local', [(False, 0), (True, None), (True, 1)])
def test_initial_membership_preserves_restored_local_intent(page, committed, local, preference_world):
    from kardenwort_state_runtime import preference_store
    preference_store(preference_world[1].config, preference_world[1].resolved_paths).set('auto_save_on_close', False)
    html = initial_html(committed, transport=False)
    if local is not None:
        item = dict(lemma=words(2)[local], atomic_id=local, row_ids=[local],
                    sentence_idx=local + 1, token_order=local, visual_idx=local + 1)
        adapter = serve_document(page, preference_world, html)
        from kardenwort_client_views import ClientViewAdapter
        client = page.url.split('kw-client=')[1]
        ClientViewAdapter(adapter.db).set(ZID, client, 1, [item])
        page.reload(); wait_state(page)
    else:
        serve_document(page, preference_world, html)
    for reload in (False, True):
        if reload:
            page.reload(); wait_state(page)
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


def test_committed_baseline_does_not_inherit_object_properties(page):
    html = initial_html(False).replace(words(2)[0], 'constructor')
    page.set_content(html)
    page.wait_for_function('typeof getDeltas === "function" && WorkspaceTabs.getCards().length === 3')
    committed = page.evaluate('WorkspaceTabs.getCards().flatMap(card => card.words.filter(word => word.lemma === "constructor").map(word => word.selected))')
    assert committed
    assert all(value == '0' for value in committed)
