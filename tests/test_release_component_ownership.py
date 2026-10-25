"""Public component contracts run through real owners in the browser."""
import pytest

from tests.test_release_audit_regressions import audit_page  # noqa: F401


def test_source_compatibility_escapes_text_and_preserves_unchanged_nodes(audit_page):
    result = audit_page.evaluate('''() => {
        SourceViewer.render({source_text:'Haus <img src=x onerror="window.injected=true"> Baum'});
        const source = document.getElementById('source-container');
        const node = source.querySelector('span.word');
        SourceViewer.render({source_text:'Haus <img src=x onerror="window.injected=true"> Baum'});
        return {same:node === source.querySelector('span.word'), images:source.querySelectorAll('img').length,
            literal:source.textContent.includes('<img'), injected:!!window.injected};
    }''')
    assert result == dict(same=True, images=0, literal=True, injected=False)


def test_translation_compatibility_keeps_nodes_focus_and_updates_metadata(audit_page):
    result = audit_page.evaluate('''() => {
        TranslationView.render({translated_text:'A house and a tree.'});
        const node = document.querySelector('#translation-container span.word');
        const input = document.createElement('input');
        document.body.appendChild(input); input.focus(); input.value='unfinished edit';
        TranslationView.render({translated_text:'A house and a tree.'});
        AppStore.applyDeltas({translated_text:'A house and a tree.',text_provenance:'live:google'});
        return {same:node === document.querySelector('#translation-container span.word'),
            focus:document.activeElement===input, value:input.value,
            provenance:node.getAttribute('data-provenance'), title:!!node.getAttribute('title')};
    }''')
    assert result == dict(same=True, focus=True, value='unfinished edit', provenance='live:google', title=True)


def test_translation_compatibility_preserves_pending_placeholder(audit_page):
    result = audit_page.evaluate('''() => {
        const container=document.getElementById('translation-container');
        container.innerHTML='<span class="skeleton-loader" data-pending="true">Working</span>';
        const pending=container.firstChild;
        TranslationView.render({translated_text:'Premature replacement'});
        return pending===container.firstChild && pending.dataset.pending==='true';
    }''')
    assert result is True


def test_translation_compatibility_preserves_literal_and_prebuilt_inputs(audit_page):
    result = audit_page.evaluate('''() => {
        const baseline=AppState.translatedText;
        const container=document.getElementById('translation-container');
        TranslationView.render({translated_text:'A literal <div>word</div>.'});
        const literal=container.textContent;
        const divs=container.querySelectorAll('div').length;
        TranslationView.render({translated_text:'<span class="word" data-order="17" data-sentence-idx="4">word</span>'});
        const span=container.querySelector('span.word');
        return {literal,divs,order:span.dataset.order,sentence:span.dataset.sentenceIdx,
            authoritativeUnchanged:baseline===AppState.translatedText};
    }''')
    assert result == dict(literal='A literal <div>word</div>.', divs=0, order='17', sentence='4', authoritativeUnchanged=True)


def test_terminal_without_replacement_retains_ready_translation(audit_page):
    result = audit_page.evaluate('''() => {
        TranslationView.render({translated_text:'Ready translation.'});
        const node=document.querySelector('#translation-container span.word');
        AppState.translatedText='';
        AppStore.applyDeltas({stage:'finished',is_finished:true});
        return {same:node===document.querySelector('#translation-container span.word'),
            text:document.getElementById('translation-container').textContent};
    }''')
    assert result == dict(same=True, text='Ready translation.')


def test_matrix_compatibility_preserves_explicit_coordinates_and_selection(audit_page):
    result = audit_page.evaluate('''() => {
        LemmaTable.renderRows({headers:['WordSource','WordDestination','TokenOrder','SentenceSourceIndex'],
            data_rows:[['Haus','house','17','4'],['Baum','tree','28','5']],selected_ids:[1]});
        return Array.from(document.querySelectorAll('#lemma-table tbody tr')).map(row =>
            [row.dataset.rowId,row.dataset.tokenOrder,row.dataset.sentenceIdx,row.dataset.selected,
             row.querySelector('[data-col="WordDestination"]').textContent]);
    }''')
    assert result == [['0', '17', '4', '0', 'house'], ['1', '28', '5', '1', 'tree']]


@pytest.mark.parametrize('component,target', [('SourceViewer', '#source-container'),
                                              ('TranslationView', '#translation-container'),
                                              ('LemmaTable', '#lemma-table')])
def test_component_remount_keeps_one_context_binding(audit_page, component, target):
    result = audit_page.evaluate('''([component,target]) => {
        const owner=window[component], element=document.querySelector(target);
        owner.dispose();
        let additions=0, removals=0;
        const add=element.addEventListener.bind(element), remove=element.removeEventListener.bind(element);
        element.addEventListener=function(type,...args) {if(type==='contextmenu') additions++;return add(type,...args)};
        element.removeEventListener=function(type,...args) {if(type==='contextmenu') removals++;return remove(type,...args)};
        owner.mount(target);owner.mount(target);owner.dispose();owner.dispose();owner.mount(target);
        return {additions,removals};
    }''', [component, target])
    assert result == dict(additions=2, removals=1)


def test_terminal_text_regions_clear_pending_through_component_owners(audit_page):
    audit_page.evaluate('''() => {
        document.getElementById('source-container').innerHTML='<span data-pending="true" class="skeleton-loader">Working</span>';
        document.getElementById('translation-container').innerHTML='<span data-pending="true" class="skeleton-loader">Working</span>';
        AppStore.applyDeltas({sourceText:'Das Haus.',translatedText:'The house.',stage:'finished',is_finished:true});
    }''')
    assert audit_page.locator('#source-container').inner_text() == 'Das Haus.'
    assert audit_page.locator('#translation-container').inner_text() == 'The house.'
    assert audit_page.locator('#source-container [data-pending], #translation-container .skeleton-loader').count() == 0


def test_required_source_helper_failure_is_explicit(audit_page):
    message = audit_page.evaluate('''() => {
        const helper=window.escapeHtml; delete window.escapeHtml;
        try { SourceViewer.render({source_text:'Haus'}); }
        catch(error) { return error.message; }
        finally { window.escapeHtml=helper; }
    }''')
    assert message == 'Required canonical escapeHtml helper is unavailable'


@pytest.mark.parametrize('audit_page', [True], indirect=True)
def test_workspace_uses_bootstrap_language_and_mode(audit_page):
    title = audit_page.evaluate('''() => {
        AppState.bootstrap.language='fr'; AppState.bootstrap.text_mode='multi';
        document.getElementById('session-lang').textContent='de';
        document.getElementById('text-mode').textContent='single';
        WorkspaceTabs.switchToTab(1,true);
        return document.title;
    }''')
    assert 'fr (multi)' in title


@pytest.mark.parametrize('audit_page', [True], indirect=True)
def test_missing_workspace_metadata_is_diagnosed_without_invented_values(audit_page):
    messages = []
    audit_page.on('console', lambda message: messages.append(message.text) if message.type == 'error' else None)
    title = audit_page.evaluate('''() => {
        delete AppState.bootstrap.language; delete AppState.bootstrap.lang;
        delete AppState.bootstrap.text_mode; delete AppState.bootstrap.session_data;
        delete AppState.language; delete AppState.lang; delete AppState.text_mode;
        delete window.__CONFIG__.language; delete window.__CONFIG__.lang; delete window.__CONFIG__.text_mode;
        document.getElementById('session-lang').remove(); document.getElementById('text-mode').remove();
        const script=document.getElementById('kardenwort-bootstrap'); if(script)script.textContent='{}';
        WorkspaceTabs.switchToTab(1,true);
        return document.title;
    }''')
    assert any('Missing workspace session language' in message for message in messages)
    assert any('Missing workspace text mode' in message for message in messages)
    assert 'en (single)' not in title
    assert '(single)' not in title
