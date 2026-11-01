"""Pending-to-resolved rendering must retain short-content table box metrics."""
import pytest

import kardenwort_desk as desk
from tests.test_release_audit_regressions import TRANSPORT


@pytest.mark.parametrize('theme', ['dark', 'light', 'white', 'compact'])
@pytest.mark.parametrize('active', [1, 2], ids=['overview', 'child'])
def test_loading_completion_preserves_table_and_token_geometry(page, theme, active):
    words = [dict(row_id=0, token_order=0, sentence_idx=1, word='Haus', inflected='Haus',
                  lemma='Haus', sentence_text='Das Haus.', translation='', is_pending=True, selected='0')]
    boot = dict(session_zid='20261101191227', zid='20261101191227', language='de', target_language='en',
        display_mode='progressive', text_mode='single', auto_inject_updates=True, worker_launched=True,
        source_text='Das Haus.', sentence_translation='', delivery_mode='container',
        text_base_provider='google', lemma_base_provider='google',
        ui_config=dict(filter_selected_only=False, highlight_same_lemma=False),
        session_data=dict(session_zid='20261101191227', zid='20261101191227', language='de',
                          target_lang='en', source_text='Das Haus.', server_enabled=True),
        sentence_cards=[dict(seq_num=seq, sentence_idx=seq-1, zid='20261101191227',
            source_text='Das Haus.', translated_text='', words=words, selected_ids=[]) for seq in [1, 2]])
    page.set_content(desk.render_declarative_web_shell(boot, theme=theme).replace('<head>', '<head>' + TRANSPORT))
    page.wait_for_function('window.WorkspaceTabs && WorkspaceTabs.getCards().length === 2')
    page.evaluate('seq => WorkspaceTabs.switchToTab(seq)', active)
    geometry = '''() => Array.from(document.querySelectorAll(
        '#source-container .word,#translation-container,#lemma-table,#lemma-table tr,#kw-action-toolbar button'
    )).map(el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})'''
    assert page.locator('.skeleton-loader').count() > 0
    before = page.evaluate(geometry)
    page.evaluate('receiveUpdate', dict(stage='finished', text_translation_status='finished',
        translatedText='The house.', sentences={'0':'The house.'}, rows={'0':dict(row_id=0,
        sentence_idx=1, token_order=0, trans='house', provenance='live:google')}))
    page.wait_for_function('document.querySelectorAll(".skeleton-loader").length === 0')
    after = page.evaluate(geometry)
    assert len(before) == len(after)
    assert all(abs(a-b) < .01 for left, right in zip(before, after) for a,b in zip(left,right)), (
        theme, active, before, after)
