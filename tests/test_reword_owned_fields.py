"""Produced fields, transactional rejection and post-Re-word editing contracts."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.reword_contract_support import ZID, backend, provider_name, drain
from tests.release_projection_support import checked_world as world
from tests.test_reword_projection_history import occurrence_world, row_request
from tests.test_reword_desktop_persistence import open_generated_desktop


RUN_PROVIDER = desk.run_headless_intellifiller


def http_provider(world, monkeypatch, respond):
    world.config.add_section('services')
    world.config.set('services', 'intellifiller_server_url', 'http://fixture-provider.invalid')
    monkeypatch.setattr(desk, 'run_headless_intellifiller', RUN_PROVIDER)
    monkeypatch.setattr(desk, 'query_intellifiller_server', respond)


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_explicit_http_fields_replace_context_and_preserve_custom_metadata(world, monkeypatch):
    visuals, unrelated = occurrence_world(world)
    word = world.adapter.db.get_words_by_session(ZID)[0]
    world.adapter.db.update_word(word['id'], {'ipa': '/haus/', 'extra_fields': dict(
        word['extra_fields'], ContextNote='old-note', unrelated='keep')})

    def respond(rows, **kwargs):
        return dict(status='success', enriched_rows=[dict(row_id=rows[0]['row_id'],
            WordDestination='fresh-Haus', WordSourceIPA='/haus/', ContextNote='')])

    http_provider(world, monkeypatch, respond)
    status, payload = world.post(row_request(world))
    assert status == 200, payload
    stored, other = world.adapter.db.get_words_by_session(ZID)
    assert stored['extra_fields']['ContextNote'] == ''
    assert stored['extra_fields']['unrelated'] == 'keep'
    assert [stored['extra_fields']['occurrence_data'][str(v)] for v in visuals] == [
        dict(trans='fresh-Haus', pos='n.', morph='keep-0'),
        dict(trans='fresh-Haus', pos='n.', morph='keep-1')]
    assert other == unrelated


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_explicit_unchanged_http_field_retains_same_field_completion_policy(world, monkeypatch):
    mapping = world.owner / 'editable-http.ini'
    mapping.write_text(world.paths['anki_mapping_file'].read_text().replace(
        'editable_columns=WordDestination', 'editable_columns=WordDestination,WordSourceIPA'), encoding='utf-8')
    world.paths['anki_mapping_file'] = mapping
    entered, release = threading.Event(), threading.Event()

    def respond(rows, **kwargs):
        entered.set()
        assert release.wait(10)
        return dict(status='success', enriched_rows=[dict(row_id=rows[0]['row_id'],
            WordDestination='fresh-Haus', WordSourceIPA='/haus/')])

    http_provider(world, monkeypatch, respond)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(world.post, row_request(world))
        try:
            assert entered.wait(5)
            world.arbiter.save_session(ZID, [dict(row_id=0, sentence_idx=1, token_order=0,
                column='WordSourceIPA', value='/manual-earlier/')], language='de')
        finally:
            release.set()
        status, payload = pending.result(timeout=10)
    assert status == 200, payload
    assert world.adapter.db.get_words_by_session(ZID)[0]['ipa'] == '/haus/'


@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'unknown'])
def test_invalid_http_batch_has_no_writes_or_success(world, monkeypatch, damage):
    def respond(rows, **kwargs):
        item = dict(row_id=rows[0]['row_id'], WordDestination='fresh-Haus')
        items = [] if damage == 'missing' else [item, item] if damage == 'duplicate' else [dict(item, row_id=99)]
        return dict(status='success', enriched_rows=items)

    http_provider(world, monkeypatch, respond)
    before, channel = world.snapshot(), world.subscribe(ZID)
    status, payload = world.post(row_request(world))
    assert status >= 400, payload
    assert world.snapshot() == before
    assert drain(channel) == []


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
@pytest.mark.parametrize('failure', ['later-manifest', 'later-write'])
def test_later_occurrence_or_storage_failure_rolls_back_whole_batch(world, failure):
    words = world.adapter.db.get_words_by_session(ZID)
    if failure == 'later-manifest':
        world.adapter.db.update_word(words[1]['id'], {'extra_fields': {
            'occurrence_data': {'0': dict(trans='wrong-owner', pos='n.')}}})
    else:
        with world.adapter.db.transaction() as conn:
            conn.execute("CREATE TRIGGER reject_second BEFORE UPDATE OF word_destination ON words "
                         "WHEN NEW.sentence_index = 2 BEGIN SELECT RAISE(ABORT, 'fixture-write-failure'); END")
    before, channel = world.snapshot(), world.subscribe(ZID)
    status, payload = world.post(world.payload(row_ids=[0, 1], target_coordinates=[
        dict(sentence_idx=1, token_order=0), dict(sentence_idx=2, token_order=0)], sentence_idx=1))
    assert status >= 400, payload
    assert world.snapshot() == before
    assert drain(channel) == []


@pytest.mark.parametrize('provider_name', ['intellifiller'])
def test_provider_merges_context_against_current_transaction_record(world, monkeypatch):
    visuals, _ = occurrence_world(world)
    entered, release = threading.Event(), threading.Event()
    original = world.provider.enrich

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(desk, 'run_headless_intellifiller', held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(world.post, world.payload(
            target_coordinates=[dict(visual_idx=visuals[0])], sentence_idx=1))
        try:
            assert entered.wait(5)
            word = world.adapter.db.get_words_by_session(ZID)[0]
            extra = dict(word['extra_fields'], ConcurrentNote='keep-new')
            extra['occurrence_data'][str(visuals[0])]['pos'] = 'concurrent-pos'
            world.adapter.db.update_word(word['id'], {'extra_fields': extra})
        finally:
            release.set()
        status, payload = pending.result(timeout=10)
    assert status == 200, payload
    extra = world.adapter.db.get_words_by_session(ZID)[0]['extra_fields']
    assert extra['ConcurrentNote'] == 'keep-new'
    assert extra['occurrence_data'][str(visuals[0])] == dict(
        trans='fresh-Haus', pos='concurrent-pos', morph='keep-0')
    assert extra['occurrence_data'][str(visuals[1])]['trans'] == 'old-1'


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_generated_edit_history_and_save_after_contextual_row_reword(page, world, provider_name):
    occurrence_world(world)
    status, payload = world.post(row_request(world))
    assert status == 200, payload
    page.add_init_script('window.EventSource = undefined;')
    errors = open_generated_desktop(page, world, 'Haus und Haus. Baum.')
    page.route('**/session/status?*', lambda route: route.fulfill(json={
        'status': 'success', 'data': {'ok': True, 'status': 'ok', 'stage': 'finished',
        'is_finished': True, 'translatedText': 'The house. The tree.'}}))
    try:
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        cell = page.locator('#lemma-table tr[data-sentence-idx="1"] td[data-col="WordDestination"]')
        expect(cell.locator('.scrollable-cell')).to_have_text('fresh-Haus')
        cell.dblclick()
        editor = cell.locator('input.cell-editor')
        editor.fill('manual-after-reword')
        editor.press('Enter')
        page.keyboard.press('Control+z')
        expect(cell.locator('.scrollable-cell')).to_have_text('fresh-Haus')
        page.keyboard.press('Control+y')
        expect(cell.locator('.scrollable-cell')).to_have_text('manual-after-reword')
        with page.expect_response(lambda response: '/session/save' in response.url) as saved:
            page.locator('#kw-btn-save').click()
        assert saved.value.ok, saved.value.text()
        page.wait_for_function('!isDirty()')
        page.reload()
        page.wait_for_function('WorkspaceTabs.getCards().length >= 3')
        page.evaluate('WorkspaceTabs.switchToTab(2)')
        expect(cell.locator('.scrollable-cell')).to_have_text('manual-after-reword')
        assert world.values() == {(1, 0): 'manual-after-reword', (2, 0): 'tree'}
        assert errors == []
    finally:
        page.wait_for_load_state('networkidle')
        page.close()


@pytest.mark.parametrize('provider_name', ['intellifiller'])
@pytest.mark.parametrize('field,fact,value', [
    ('MorphologyAI', 'morph', 'fresh-morph'),
    ('morphology', 'morph', 'fresh-morph'),
    ('word_destination', 'trans', 'fresh-Haus'),
    ('pos', 'pos', 'v.'),
])
def test_supported_provider_aliases_update_raw_and_contextual_facts(world, monkeypatch, field, fact, value):
    visuals, _ = occurrence_world(world)

    def respond(rows, **kwargs):
        return dict(status='success', enriched_rows=[dict(row_id=str(rows[0]['row_id']), **{field: value})])

    http_provider(world, monkeypatch, respond)
    status, payload = world.post(row_request(world))
    assert status == 200, payload
    stored = world.adapter.db.get_words_by_session(ZID)[0]
    raw_field = dict(trans='word_destination', morph='morphology', pos='pos')[fact]
    assert stored[raw_field] == value
    assert [stored['extra_fields']['occurrence_data'][str(v)][fact] for v in visuals] == [value, value]
