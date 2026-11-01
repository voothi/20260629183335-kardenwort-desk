"""Public Re-word actions must supersede old contextual facts at their actual scope."""
import threading
import subprocess
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_controller as controller
import kardenwort_desk as desk
from tests.reword_contract_support import ZID, backend, provider_name, drain
from tests.release_projection_support import checked_world as world
from tests.test_reword_desktop_persistence import open_generated_desktop


def occurrence_world(world):
    source = 'Haus und Haus. Baum.'
    world.seed(ZID, [['0', 'Haus', 'Haus', 'house', '1', 'Haus und Haus.', '/haus/', 'noun'],
                     ['0', 'Baum', 'Baum', 'tree', '2', 'Baum.', '/baum/', 'noun']], text=source)
    visuals = [token['visual_idx'] for token in desk.tok.build_word_list_internal(source, keep_spaces=True)
               if token.get('text') == 'Haus']
    words = world.adapter.db.get_words_by_session(ZID)
    extra = dict(words[0]['extra_fields'])
    extra['occurrence_data'] = {str(visual): dict(trans='old-' + str(i), pos='n.', morph='keep-' + str(i))
                                for i, visual in enumerate(visuals)}
    world.adapter.db.update_word(words[0]['id'], {'extra_fields': extra})
    return visuals, world.adapter.db.get_words_by_session(ZID)[1]


def row_request(world, shape='complete'):
    return world.payload(shape, sentence_idx=1)


def test_snapshot_row_reword_returns_its_fresh_result_with_existing_context(world, monkeypatch):
    root = Path(desk.__file__).parent

    def historical(name, filename):
        module = types.ModuleType(name)
        module.__file__ = str(root / filename)
        monkeypatch.setitem(sys.modules, name, module)
        source = subprocess.check_output(['git', 'show', '56f8696:' + filename], cwd=root).decode('utf-8')
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
        return module

    occurrence_world(world)
    old_desk = historical('projection_snapshot_desk', 'kardenwort_desk.py')
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, 'kardenwort_desk', old_desk)
        old_controller = historical('projection_snapshot_controller', 'kardenwort_controller.py')
    monkeypatch.setattr(old_controller, 'translate_lemmas_fast_path', world.provider.translate)
    result = old_controller.SessionArbiter.reword_session(world.arbiter, ZID,
        selected_rows=[0], sentence_idx=1, language='de')
    assert result['rows']['0']['trans'] == 'fresh-Haus'
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('shape', ['complete', 'legacy'])
def test_row_reword_supersedes_context_in_storage_response_and_event(world, provider_name, shape):
    visuals, unrelated = occurrence_world(world)
    channel = world.subscribe(ZID)
    status, payload = world.post(row_request(world, shape))
    assert status == 200, payload
    body = payload.get('data', payload)
    stored, other = world.adapter.db.get_words_by_session(ZID)
    events = [event for event in drain(channel) if event.get('rows')]
    assert other == unrelated
    observed = dict(raw=stored['word_destination'], response=body['rows']['0']['trans'],
                    event=events[-1]['rows']['0']['trans'],
                    occurrences=[stored['extra_fields']['occurrence_data'][str(v)]['trans'] for v in visuals])
    assert observed == dict(raw='fresh-Haus', response='fresh-Haus', event='fresh-Haus',
                            occurrences=['fresh-Haus', 'fresh-Haus'])


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_visual_then_row_then_visual_keeps_requested_scope(world, provider_name):
    visuals, unrelated = occurrence_world(world)
    first = world.payload(target_coordinates=[dict(visual_idx=visuals[0])], sentence_idx=1)
    status, payload = world.post(first)
    assert status == 200, payload
    after_visual = world.adapter.db.get_words_by_session(ZID)[0]['extra_fields']['occurrence_data']
    assert after_visual[str(visuals[0])]['trans'] == 'fresh-Haus'
    assert after_visual[str(visuals[1])]['trans'] == 'old-1'
    status, payload = world.post(row_request(world))
    assert status == 200, payload
    after_row = world.adapter.db.get_words_by_session(ZID)[0]['extra_fields']['occurrence_data']
    status, payload = world.post(world.payload(target_coordinates=[dict(visual_idx=visuals[1])], sentence_idx=1))
    assert status == 200, payload
    after_second_visual = world.adapter.db.get_words_by_session(ZID)[0]['extra_fields']['occurrence_data']
    assert dict(after_row=[after_row[str(v)]['trans'] for v in visuals],
                after_second_visual=[after_second_visual[str(v)]['trans'] for v in visuals]) == dict(
        after_row=['fresh-Haus', 'fresh-Haus'], after_second_visual=['fresh-Haus', 'fresh-Haus'])
    assert world.adapter.db.get_words_by_session(ZID)[1] == unrelated


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_existing_same_field_policy_keeps_last_completed_write(world, monkeypatch, provider_name):
    entered, release = threading.Event(), threading.Event()
    original = world.provider.translate if provider_name == 'google' else world.provider.enrich

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    module = controller if provider_name == 'google' or world.backend == 'tsv' else desk
    method = 'translate_lemmas_fast_path' if provider_name == 'google' else 'run_headless_intellifiller'
    monkeypatch.setattr(module, method, held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(world.post, row_request(world))
        try:
            assert entered.wait(5)
            world.arbiter.save_session(ZID, [dict(row_id=0, sentence_idx=1, token_order=0,
                column='WordDestination', value='manual-earlier')], language='de')
            assert world.values()[(1, 0)] == 'manual-earlier'
        finally:
            release.set()
        status, payload = pending.result(timeout=10)
    assert status == 200, payload
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_repeat_row_action_preserves_natural_punctuation_and_other_sentence(world, monkeypatch, provider_name):
    translated = 'house, home; dwelling.'
    if provider_name == 'google':
        def respond(lemmas, **kwargs):
            return {lemma: translated for lemma in lemmas}
        monkeypatch.setattr(controller, 'translate_lemmas_fast_path', respond)
    else:
        def respond(tsv_path, *args, selected_rows=None, **kwargs):
            comments, headers, rows = desk.load_tsv_rows(tsv_path)
            for index in selected_rows:
                rows[index][headers.index('WordDestination')] = translated
            rows.reverse()
            desk.save_tsv_rows_safely(tsv_path, comments, headers, rows)
            return True
        monkeypatch.setattr(desk if world.backend == 'sqlite' else controller, 'run_headless_intellifiller', respond)
    for _ in range(2):
        status, payload = world.post(row_request(world))
        assert status == 200, payload
        assert world.values() == {(1, 0): translated, (2, 0): 'tree'}
        assert payload.get('data', payload)['rows']['0']['trans'] == translated


@pytest.mark.parametrize('provider_name', ['google'])
def test_visual_translation_preserves_unproduced_contextual_fields(world):
    visuals, _ = occurrence_world(world)
    status, payload = world.post(world.payload(target_coordinates=[dict(visual_idx=visuals[0])], sentence_idx=1))
    assert status == 200, payload
    occurrence = world.adapter.db.get_words_by_session(ZID)[0]['extra_fields']['occurrence_data'][str(visuals[0])]
    assert occurrence == dict(trans='fresh-Haus', pos='n.', morph='keep-0')


@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_provider_completion_preserves_unrelated_committed_edit(world, monkeypatch):
    mapping = world.owner / 'editable-mapping.ini'
    mapping.write_text(world.paths['anki_mapping_file'].read_text().replace(
        'editable_columns=WordDestination', 'editable_columns=WordDestination,WordSourceIPA'), encoding='utf-8')
    world.paths['anki_mapping_file'] = mapping
    entered, release = threading.Event(), threading.Event()
    original = world.provider.enrich

    def held_provider(tsv_path, *args, **kwargs):
        entered.set()
        assert release.wait(10), 'Provider completion was never released'
        return original(tsv_path, *args, **kwargs)

    monkeypatch.setattr(desk if world.backend == 'sqlite' else controller, 'run_headless_intellifiller', held_provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(world.post, row_request(world))
        try:
            assert entered.wait(5), 'Provider did not start'
            world.arbiter.save_session(ZID, [dict(row_id=0, sentence_idx=1, token_order=0,
                column='WordSourceIPA', value='/manual-new/')], language='de')
        finally:
            release.set()
        status, payload = pending.result(timeout=10)
    assert status == 200, payload
    _, headers, rows = world.adapter.load_tsv_rows(world.path(ZID))
    assert rows[0][headers.index('WordSourceIPA')] == '/manual-new/'
    assert world.values() == {(1, 0): 'fresh-Haus', (2, 0): 'tree'}


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_generated_row_reword_updates_display_and_reload_after_context(page, world, provider_name, capsys):
    word = world.adapter.db.get_words_by_session(ZID)[0]
    extra = dict(word['extra_fields'])
    extra['occurrence_data'] = {str(world.visual('Haus')): dict(trans='previous-context', pos='n.')}
    world.adapter.db.update_word(word['id'], {'extra_fields': extra})
    # HTTP response projection is the boundary under test; dispatcher events are
    # asserted separately, without leaving an unowned browser SSE socket open.
    page.add_init_script('window.EventSource = undefined;')
    errors = []
    try:
        errors = open_generated_desktop(page, world, 'Haus. Baum.')
        page.route('**/session/status?*', lambda route: route.fulfill(json={
            'status': 'success', 'data': {'ok': True, 'status': 'ok', 'stage': 'finished',
            'is_finished': True, 'translatedText': 'The house. The tree.'}}))
        page.evaluate('WorkspaceTabs.switchToTab(2); toggleRowSelection(0,true); AppState.activeTokenSelections=[];')
        cell = page.locator('#lemma-table tr[data-sentence-idx="1"] td[data-col="WordDestination"] .scrollable-cell')
        expect(cell).to_have_text('previous-context')
        with page.expect_response(lambda response: '/session/reword' in response.url) as response:
            page.get_by_role('button', name='Re-word', exact=True).click()
        assert response.value.ok, response.value.text()
        page.wait_for_function('!document.getElementById("kw-btn-reword").disabled')
        displayed = cell.inner_text()
        page.reload()
        page.wait_for_function('WorkspaceTabs.getCards().length >= 3')
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        reloaded = cell.inner_text()
        assert dict(raw=world.values()[(1, 0)], display=displayed, reload=reloaded) == dict(
            raw='fresh-Haus', display='fresh-Haus', reload='fresh-Haus')
        assert errors == []
    finally:
        if not page.is_closed():
            page.wait_for_load_state('networkidle')
        page.close()
    assert 'Exception occurred during processing' not in capsys.readouterr().err
