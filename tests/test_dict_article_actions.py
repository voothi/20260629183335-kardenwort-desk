"""Dedicated article actions against owned real SQLite/TSV and HTTP commands."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

import kardenwort_desk as desk
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import use_backend, wait_for_held


ZID = '20261028212349'
HEADERS = ['Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex', 'TokenOrder', 'DeskSelected']
ROWS = [['Haus', 'Haus', 'house', '1', '7', '0'],
        ['Baum', 'Baum', 'tree', '2', '0', '0'],
        ['Haus', ' Haus ', 'house', '3', '0', '0']]


@pytest.fixture(params=['sqlite', 'tsv'])
def article_session(request, running_controller, tmp_path):
    url, server, sqlite = running_controller
    backend = request.param
    use_backend(server, backend)
    server.config.set('languages', 'de_lemma_index', '')
    server.config.set('settings', 'send_to_anki_after_export', 'false')
    server.config.set('settings', 'save_to_favorites_on_export', 'true')
    favorites = tmp_path / 'favorites'
    server.resolved_paths['favorites_output_dir'] = favorites
    path = Path(server.resolved_paths['results_dir']) / (ZID + '-article.de.tsv')
    if backend == 'sqlite':
        sqlite.save_session(session_zid=ZID, slug='article', source_language='de', target_language='en',
                            headers=HEADERS, data_rows=[list(row) for row in ROWS])
    else:
        desk.save_tsv_rows_safely(path, [], HEADERS, [list(row) for row in ROWS])

    def restored():
        if backend == 'sqlite':
            return sqlite.restore_session(ZID)
        comments, headers, rows = desk.load_tsv_rows(path)
        return dict(comments=comments, headers=headers, data_rows=rows)

    def render(**kwargs):
        data = restored()
        options = dict(server_enabled=True, server_host='127.0.0.1', server_port=server.server_port,
                       sections=['source', 'translation', 'lemmas'], theme='compact',
                       lemma_columns=['lemma', 'translation'])
        options.update(kwargs.pop('options', {}))
        return desk.render_lookup_html('Haus. Baum. Haus.', 'de', 'en', server.config,
            server.resolved_paths, ZID, options, [], data['headers'], data['data_rows'], 'House. Tree. House.',
            session_zid=kwargs.pop('session_zid', ZID), api_token=kwargs.pop('api_token', 'test-controller-api-key'),
            server_enabled=True, **kwargs)

    return render, restored, favorites


@pytest.fixture
def article_page(page):
    content = {'html': ''}

    class ArticleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = content['html'].encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), ArticleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def mount(html):
        content['html'] = html
        page.goto(f'http://127.0.0.1:{server.server_port}/article.html')
        expect(page.locator('.kw-dict-article').first).to_have_attribute('data-state', 'idle')

    try:
        yield mount
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def selected(restored):
    data = restored()
    headers = data['headers']
    return {(int(row[headers.index('SentenceSourceIndex')]), int(row[headers.index('TokenOrder')])):
            desk.selection_is_selected(row[headers.index('DeskSelected')]) for row in data['data_rows']}


def change(page, row, value):
    page.locator(f'tr[data-row-id="{row}"] input.kw-tag-checkbox').set_checked(value)
    expect(page.locator('.kw-dict-article')).to_have_attribute('data-state', 'idle')


def test_tag_deselect_export_and_reopen_preserve_canonical_targets(page, article_session, article_page):
    render, restored, favorites = article_session
    requests = []
    page.on('request', lambda request: requests.append(request))
    article_page(render())
    change(page, 0, True)
    assert selected(restored) == {(1, 7): True, (2, 0): False, (3, 0): True}
    assert page.locator('tr[data-row-id="2"] input').is_checked()
    saves = [request for request in requests if '/session/save' in request.url]
    payload = saves[0].post_data_json
    assert {(item['sentence_idx'], item['token_order']) for item in payload['deltas']} == {(1, 7), (3, 0)}
    assert saves[0].headers['x-api-token'] == 'test-controller-api-key'
    with page.expect_response('**/session/export'):
        page.get_by_role('button', name='Export to Anki').click()
    expect(page.get_by_role('status')).to_have_text('Export acknowledged.')
    exported = list(favorites.glob('*.tsv'))
    assert len(exported) == 1
    _, headers, rows = desk.load_tsv_rows(exported[0])
    assert {(int(row[headers.index('SentenceSourceIndex')]), int(row[headers.index('TokenOrder')]))
            for row in rows} == {(1, 7), (3, 0)}
    article_page(render())
    assert page.locator('tr[data-row-id="0"] input').is_checked()
    change(page, 2, False)
    assert selected(restored) == {(1, 7): False, (2, 0): False, (3, 0): False}
    assert not page.locator('tr[data-row-id="0"] input').is_checked()
    assert not any('/events' in request.url or '/session/status' in request.url for request in requests)


def test_newer_reversal_survives_held_acknowledgment_and_export_waits(page, article_session, article_page):
    render, restored, favorites = article_session
    article_page(render())
    change(page, 1, True)
    held, exports = [], []
    page.route('**/session/save', lambda route: held.append(route))
    page.on('request', lambda request: exports.append(request) if '/session/export' in request.url else None)
    checkbox = page.locator('tr[data-row-id="0"] input')
    checkbox.check()
    wait_for_held(page, held, 1)
    checkbox.uncheck()
    page.get_by_role('button', name='Export to Anki').click()
    assert exports == []
    held[0].fulfill(response=held[0].fetch())
    wait_for_held(page, held, 2)
    assert not checkbox.is_checked()
    assert exports == []
    held[1].fulfill(response=held[1].fetch())
    expect(page.get_by_role('status')).to_have_text('Export acknowledged.')
    assert selected(restored) == {(1, 7): False, (2, 0): True, (3, 0): False}
    assert exports[0].post_data_json['selected_row_ids'] == [1]
    _, headers, rows = desk.load_tsv_rows(next(favorites.glob('*.tsv')))
    assert [(row[headers.index('SentenceSourceIndex')], row[headers.index('TokenOrder')]) for row in rows] == [('2', '0')]


@pytest.mark.parametrize('failure', ['authentication', 'stale', 'transport'])
def test_failed_tag_is_explicit_preserves_intent_and_blocks_export(page, article_session, article_page, failure):
    render, restored, favorites = article_session
    kwargs = {'api_token': 'bad-token'} if failure == 'authentication' else {}
    if failure == 'stale':
        kwargs['fingerprint'] = 'stale'
    article_page(render(**kwargs))
    if failure == 'transport':
        page.route('**/session/save', lambda route: route.abort())
    page.locator('tr[data-row-id="0"] input').check()
    expect(page.locator('.kw-dict-article')).to_have_attribute('data-state', 'failed')
    assert page.locator('tr[data-row-id="0"] input').is_checked()
    expect(page.get_by_role('status')).to_be_visible()
    assert 'reopen' in page.get_by_role('status').inner_text()
    expected = {'authentication': 'UNAUTHORIZED', 'stale': 'ROW_STALE', 'transport': 'Transport failed'}
    assert expected[failure] in page.get_by_role('status').inner_text()
    assert selected(restored) == {(1, 7): False, (2, 0): False, (3, 0): False}
    assert page.get_by_role('button', name='Export to Anki').is_disabled()
    assert not list(favorites.glob('*.tsv'))


def test_css_disabled_actions_and_hostile_content_are_safe(page, article_session, article_page):
    render, restored, _ = article_session
    html = render(options={'disable_css': True, 'heading_lemmas': '</h3><script>window.pwned=1</script>',
                           'lemma_columns': ['translation', 'lemma', 'ipa']})
    article_page(html)
    assert page.locator('style, link[rel=stylesheet]').count() == 0
    assert page.evaluate('typeof window.pwned') == 'undefined'
    assert page.get_by_role('columnheader').all_text_contents() == ['★', 'Translation', 'Lemma', 'Ipa']
    change(page, 1, True)
    assert selected(restored)[(2, 0)]


def replace_fixture_rows(running_controller, headers, rows):
    _, server, adapter = running_controller
    if server.resolved_paths['storage_backend'] == 'sqlite':
        adapter.save_session(session_zid=ZID, slug='article', source_language='de', target_language='en',
                             headers=headers, data_rows=rows)
    else:
        path = Path(server.resolved_paths['results_dir']) / (ZID + '-article.de.tsv')
        desk.save_tsv_rows_safely(path, [], headers, rows)


def test_configured_selection_mapping_is_preserved(page, article_session, article_page, running_controller, tmp_path):
    render, restored, _ = article_session
    _, server, _ = running_controller
    mapping = tmp_path / 'mapping.ini'
    mapping.write_text('[desk_columns]\nWordSource=lemma\nWordDestination=word_translation\nMarked=selected\n'
                       '[desk_editable]\neditable_columns=WordDestination,Marked\n', encoding='utf-8')
    server.resolved_paths['anki_mapping_file'] = mapping
    headers = ['Marked' if field == 'DeskSelected' else field for field in HEADERS]
    replace_fixture_rows(running_controller, headers, [list(row) for row in ROWS])
    article_page(render())
    change(page, 1, True)
    data = restored()
    assert [(row[data['headers'].index('Marked')]) for row in data['data_rows']] == ['0', '1', '0']
    article_page(render())
    assert page.locator('tr[data-row-id="1"] input').is_checked()


def test_merged_row_emits_all_occurrences_without_display_index_substitution(page, article_session, article_page, running_controller):
    render, restored, _ = article_session
    rows = [list(row) + ['0,2' if index == 0 else ''] for index, row in enumerate(ROWS)]
    replace_fixture_rows(running_controller, HEADERS + ['all_row_ids'], rows)
    requests = []
    page.on('request', lambda request: requests.append(request) if '/session/save' in request.url else None)
    article_page(render())
    change(page, 0, True)
    assert {(item['sentence_idx'], item['token_order']) for item in requests[0].post_data_json['deltas']} == {(1, 7), (3, 0)}
    assert selected(restored) == {(1, 7): True, (2, 0): False, (3, 0): True}


def test_two_embedded_articles_keep_session_and_pending_state_independent(page, article_session, article_page, running_controller):
    render, restored, _ = article_session
    _, server, adapter = running_controller
    other = '20261028213630'
    data = restored()
    if server.resolved_paths['storage_backend'] == 'sqlite':
        adapter.save_session(session_zid=other, source_language='de', target_language='en',
                             headers=data['headers'], data_rows=data['data_rows'])
    else:
        desk.save_tsv_rows_safely(Path(server.resolved_paths['results_dir']) / (other + '-article.de.tsv'),
                                 [], data['headers'], data['data_rows'])
    article_page(render() + render(session_zid=other))
    articles = page.locator('.kw-dict-article')
    expect(articles.nth(1)).to_have_attribute('data-state', 'idle')
    articles.nth(0).locator('tr[data-row-id="0"] input').check()
    expect(articles.nth(0)).to_have_attribute('data-state', 'idle')
    assert selected(restored)[(1, 7)]
    assert not articles.nth(1).locator('tr[data-row-id="0"] input').is_checked()
    assert articles.nth(1).get_by_role('status').inner_text().strip() == ''
    membership = server.arbiter.storage_adapter.get_shared_selection(other)
    assert not any(membership.values())


def test_incomplete_identity_keeps_static_content_and_disables_mutations(page, article_session, article_page, running_controller):
    _, _, _ = article_session
    _, server, _ = running_controller
    html = desk.render_lookup_html('Haus.', 'de', 'en', server.config, server.resolved_paths, ZID,
        dict(server_host='127.0.0.1', server_port=server.server_port, server_enabled=True,
             sections=['lemmas'], lemma_columns=['lemma', 'translation']),
        [], ['WordSource', 'WordDestination', 'DeskSelected'], [['Haus', 'house', '0']], '',
        session_zid=ZID, api_token='test-controller-api-key', server_enabled=True)
    requests = []
    page.on('request', lambda request: requests.append(request.url) if '/session/' in request.url else None)
    # This case intentionally cannot initialize an interactive article.
    page.set_content(html)
    expect(page.locator('.kw-dict-article')).to_have_attribute('data-state', 'unavailable')
    assert 'house' in page.get_by_role('table').inner_text()
    assert page.get_by_role('checkbox').is_disabled()
    assert page.get_by_role('button', name='Export to Anki').is_disabled()
    assert requests == []


@pytest.mark.parametrize('mode', ['all', 'unselected'])
def test_export_policy_acknowledges_current_membership_and_allows_next_tag(page, article_session, article_page, running_controller, mode):
    render, restored, _ = article_session
    _, server, _ = running_controller
    server.config.set('settings', 'export_selection_mode', mode)
    article_page(render())
    change(page, 0, True)
    with page.expect_response('**/session/export'):
        page.get_by_role('button', name='Export to Anki').click()
    expect(page.get_by_role('status')).to_have_text('Export acknowledged.')
    actual = selected(restored)
    for row, target in enumerate([(1, 7), (2, 0), (3, 0)]):
        assert page.locator(f'tr[data-row-id="{row}"] input').is_checked() == actual[target]
    change(page, 0, False)
    assert not selected(restored)[(1, 7)]


def test_export_rejects_stale_fingerprint_before_any_export_effect(page, article_session, article_page, running_controller):
    render, _, favorites = article_session
    _, server, _ = running_controller
    article_page(render())
    change(page, 0, True)
    server.arbiter.save_session(ZID, [dict(row_id=1, sentence_idx=2, token_order=0,
                                         column='DeskSelected', value='1')], language='de')
    page.get_by_role('button', name='Export to Anki').click()
    expect(page.locator('.kw-dict-article')).to_have_attribute('data-state', 'failed')
    assert 'ROW_STALE' in page.get_by_role('status').inner_text()
    assert not list(favorites.glob('*.tsv'))
