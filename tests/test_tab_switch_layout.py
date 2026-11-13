"""Rapid real tab projections retain geometry without repeated forced layout."""
import copy
import json
import re
import pytest
import kardenwort_desk as desk
from scripts.benchmark_release_interactions import render_fixture


@pytest.mark.parametrize('count',[64,256])
def test_tab_roundtrip_batches_browser_layouts(page,count):
    rendered=render_fixture(desk,count,'container')
    boot=json.loads(re.search(r'<script[^>]*id="kardenwort-bootstrap"[^>]*>(.*?)</script>',rendered,re.S).group(1))
    boot['server_enabled']=False
    boot['session_data']['server_enabled']=False
    boot['ui_config'].update(highlight_same_lemma=False,auto_save_on_edit=False)
    overview=boot['sentence_cards'][0]
    for index in range(2):
        child=copy.deepcopy(overview)
        child.update(seq_num=index+2,sentence_idx=index+1,words=[copy.deepcopy(overview['words'][index])],selected_ids=[])
        child['source_text']=overview['words'][index]['word']+'.'
        boot['sentence_cards'].append(child)
    page.set_viewport_size({'width':1280,'height':900})
    page.set_content(desk.render_declarative_web_shell(boot,theme='dark'))
    page.wait_for_function('window.WorkspaceTabs && window.AppState && typeof toggleRowSelection=== "function"')
    page.evaluate('toggleRowSelection(0,true)')
    geometry="""() => Array.from(document.querySelectorAll(
        '#source-container .word,#lemma-table tr,#kw-action-toolbar button'
    )).map(el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})"""
    before=page.evaluate(geometry)
    monitor=page.context.new_cdp_session(page)
    monitor.send('Performance.enable')
    layouts_before=next(item['value'] for item in monitor.send('Performance.getMetrics')['metrics'] if item['name']=='LayoutCount')
    observations=page.evaluate("""() => {
        const observed=[];
        for(let cycle=0;cycle<3;cycle++){
            for(const seq of [2,3,1]){
                WorkspaceTabs.switchToTab(seq);
                observed.push({seq:WorkspaceTabs.getActiveTabSeq(),rows:document.querySelectorAll('#lemma-table tbody tr[data-row-id]').length});
            }
        }
        return observed;
    }""")
    after=page.evaluate(geometry)
    layouts_after=next(item['value'] for item in monitor.send('Performance.getMetrics')['metrics'] if item['name']=='LayoutCount')
    monitor.detach()
    assert observations==[dict(seq=seq,rows=rows) for _ in range(3) for seq,rows in ((2,1),(3,1),(1,count))]
    assert len(before)==len(after)
    assert all(abs(a-b)<.01 for left,right in zip(before,after) for a,b in zip(left,right))
    assert page.locator('#lemma-table tr[data-row-id="0"]').get_attribute('data-selected')=='1'
    assert page.evaluate('WorkspaceTabs.getActiveTabSeq()')==1
    # At most one layout per transition plus final projection measurement.
    assert layouts_after-layouts_before<=10,(layouts_before,layouts_after)
