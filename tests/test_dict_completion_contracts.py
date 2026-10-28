"""Remaining configured-field and merged-occurrence article contracts."""
import json
import urllib.error
import urllib.request

import pytest

from tests.desktop_component_fixture import render_desktop_components
import kardenwort_desk as desk
from tests.test_dict_row_identity_regressions import TRANSPORT
from tests.test_dict_transport_audit_regressions import edit_translation, mount_article
from tests.test_goldendict_article_transport_regressions import render_test_desktop
from tests.test_controller_integration import running_controller  # noqa: F401
from tests.test_release_boundary_contracts import (
    open_browser, seed_session, use_backend, wait_for_held,
)


def lookup(page, cfg, paths, gd, headers, rows):
    gd.update(server_enabled=True, server_api_key="test-controller-api-key")
    html = render_desktop_components("Haus. Haus.", "de", "en", cfg, paths, "20261027225852", gd,
                                   [], headers, rows, "House. House.", session_zid="20261027225852",
                                   server_enabled=True, api_token="test-controller-api-key")
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))


@pytest.mark.parametrize('backend', ['tsv', 'sqlite'])
def test_configured_translation_field_is_emitted_and_saved(page, tmp_path, backend):
    cfg, paths, gd, _ = desk.load_config()
    mapping = tmp_path / "custom-mapping.ini"
    mapping.write_text("[desk_columns]\nWordSource=lemma\nGloss=word_translation\nDeskSelected=selected\n"
                       "[desk_editable]\neditable_columns=Gloss,DeskSelected\n", encoding="utf-8")
    paths["anki_mapping_file"] = mapping
    paths["storage_backend"] = backend
    cfg.set("storage", "backend", backend)
    headers = ["WordSource", "Gloss", "SentenceSourceIndex", "TokenOrder", "DeskSelected"]
    rows = [["Haus", "house", "2", "0", "0"]]
    target = tmp_path / "20261027225852.de.tsv"
    if backend == 'sqlite':
        adapter = desk.SqliteStorageAdapter(cfg, paths)
        adapter.save_session(session_zid='20261027225852', headers=headers, data_rows=rows)
        target = '20261027225852'
    else:
        desk.save_tsv_rows_safely(target, [], headers, rows)
    lookup(page, cfg, paths, gd, headers, rows)
    edit_translation(page, 0, "cottage")
    deltas = json.loads(page.evaluate("getDeltas()"))
    assert deltas[0]["column"] == "Gloss"
    desk.core_edit_save(target, deltas, cfg, paths)
    if backend == 'sqlite':
        restored = adapter.restore_session(target)
        assert restored['data_rows'][0][restored['headers'].index('Gloss')] == 'cottage'
    else:
        assert desk.load_tsv_rows(target)[2][0][1] == "cottage"


def test_merged_lookup_reword_carries_every_occurrence(page):
    cfg, paths, gd, _ = desk.load_config()
    headers = ["WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected", "all_row_ids"]
    rows = [["Haus", "house", "1", "0", "0", "0,1"],
            ["Haus", "house", "2", "0", "0", ""]]
    lookup(page, cfg, paths, gd, headers, rows)
    page.evaluate("toggleRowSelection(0, true)")
    page.evaluate("onRewordClick()")
    page.wait_for_function("__requests.some(r=>r.url.includes('/session/reword'))")
    payload = page.evaluate("__requests.find(r=>r.url.includes('/session/reword')).body")
    targets = {(t["sentence_idx"], t["token_order"]) for t in payload["target_coordinates"]}
    assert targets == {(1, 0), (2, 0)}
    row_targets = json.loads(page.locator('tr[data-row-id="0"]').get_attribute("data-occurrence-targets"))
    assert {(t["sentence_idx"], t["token_order"]) for t in row_targets} == targets


@pytest.mark.parametrize('backend', ['tsv', 'sqlite'])
def test_configured_selection_field_saves_and_reloads(page, tmp_path, backend):
    cfg, paths, gd, _ = desk.load_config()
    mapping = tmp_path / 'selection-mapping.ini'
    mapping.write_text('[desk_columns]\nWordSource=lemma\nWordDestination=word_translation\n'
                       'Marked=selected\n[desk_editable]\neditable_columns=WordDestination,Marked\n',
                       encoding='utf-8')
    paths['anki_mapping_file'] = mapping
    paths['storage_backend'] = backend
    cfg.set('storage', 'backend', backend)
    headers = ['WordSource', 'WordDestination', 'SentenceSourceIndex', 'TokenOrder', 'Marked']
    rows = [['Haus', 'house', '2', '0', '0']]
    target = tmp_path / '20261027225852.de.tsv'
    if backend == 'sqlite':
        adapter = desk.SqliteStorageAdapter(cfg, paths)
        adapter.save_session(session_zid='20261027225852', headers=headers, data_rows=rows)
        target = '20261027225852'
    else:
        desk.save_tsv_rows_safely(target, [], headers, rows)
    lookup(page, cfg, paths, gd, headers, rows)
    page.locator('input.kw-tag-checkbox').click()
    deltas = json.loads(page.evaluate('getDeltas()'))
    assert len(deltas) == 1 and deltas[0]['value'] == '1'
    desk.core_edit_save(target, deltas, cfg, paths)
    restored = adapter.restore_session(target) if backend == 'sqlite' else dict(zip(
        ('comments', 'headers', 'data_rows'), desk.load_tsv_rows(target)))
    assert restored['data_rows'][0][restored['headers'].index('Marked')] == '1'
    if backend == 'sqlite':
        assert adapter.db.get_words_by_session(target)[0]['selected'] == 1
    lookup(page, cfg, paths, gd, restored['headers'], restored['data_rows'])
    assert page.locator('input.kw-tag-checkbox').is_checked()
    page.locator('input.kw-tag-checkbox').click()
    desk.core_edit_save(target, json.loads(page.evaluate('getDeltas()')), cfg, paths)
    restored = adapter.restore_session(target) if backend == 'sqlite' else dict(zip(
        ('comments', 'headers', 'data_rows'), desk.load_tsv_rows(target)))
    assert restored['data_rows'][0][restored['headers'].index('Marked')] in ('', '0')
    if backend == 'sqlite':
        assert adapter.db.get_words_by_session(target)[0]['selected'] == 0


def test_merged_lookup_edit_rejects_ambiguous_word_target(page):
    cfg, paths, gd, _ = desk.load_config()
    headers = ["WordSource", "WordDestination", "SentenceSourceIndex", "TokenOrder", "DeskSelected", "all_row_ids"]
    rows = [["Haus", "house", "1", "0", "0", "0,1"],
            ["Haus", "house", "2", "0", "0", ""]]
    lookup(page, cfg, paths, gd, headers, rows)
    page.locator('tr[data-row-id="0"] td.col-translation').dblclick()
    assert page.locator('tr[data-row-id="0"] input.edit-input').count() == 0
    assert json.loads(page.evaluate("getDeltas()")) == []
    page.evaluate('AppState.activeTokenSelections = [{sentence_idx:2, token_order:0}]')
    edit_translation(page, 0, 'cottage')
    edits = json.loads(page.evaluate('getDeltas()'))
    assert len(edits) == 1
    assert (edits[0]['sentence_idx'], edits[0]['token_order'], edits[0]['value']) == (2, 0, 'cottage')


@pytest.mark.parametrize('path', ['/session/status?zid=20261027225852', '/api/v1/events?session=20261027225852'])
def test_article_reads_require_authentication(running_controller, path):
    url, _, _ = running_controller
    try:
        response = urllib.request.urlopen(url + path, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.status in (401, 403)


def test_arbitrary_web_origin_cannot_read_article_credentials(running_controller):
    url, _, _ = running_controller
    request = urllib.request.Request(url + '/session/status?zid=20261027225852',
                                     headers={'Origin': 'https://untrusted.example', 'X-API-Token': 'test-controller-api-key'})
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.status == 403
        assert response.headers.get('Access-Control-Allow-Origin') != '*'


def test_different_origin_article_receives_authenticated_membership_stream(running_controller, page):
    url, server, adapter = running_controller
    zid = '20261027225852'
    headers = ['WordSource', 'WordDestination', 'SentenceSourceIndex', 'TokenOrder', 'DeskSelected']
    rows = [['Haus', 'house', '1', '0', '0']]
    adapter.save_session(session_zid=zid, headers=headers, data_rows=rows, source_language='de')
    html = render_test_desktop(server_enabled=True, server_url=url, api_token='test-controller-api-key',
                              session_zid=zid, headers=headers, data_rows=rows)
    streams = []
    page.on('request', lambda request: streams.append({
        'authenticated': request.headers.get('x-api-token') == 'test-controller-api-key',
        'credential_in_url': 'test-controller-api-key' in request.url,
    }) if '/api/v1/events?' in request.url else None)
    with mount_article(page, html):
        page.wait_for_function("document.querySelector('#sse-status-indicator[data-sync=synced]') !== null")
        server.arbiter.save_session(zid, [dict(row_id=0, sentence_idx=1, token_order=0,
                                              column='DeskSelected', value='1')], language='de')
        page.wait_for_function('AppState.selectedLemmas.haus === true')
        assert page.locator('input.kw-tag-checkbox').is_checked()
        assert streams and all(item == {'authenticated': True, 'credential_in_url': False} for item in streams)


def test_status_started_before_save_cannot_overwrite_acknowledged_selection(running_controller, page, tmp_path):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.evaluate("AppState.translatedText='House. Tree.'; document.querySelector('#translation-container').textContent='House. Tree.'")
    page.evaluate("""() => {
        window.__staleStatusApplications = 0;
        const receive = window.receiveUpdate;
        window.receiveUpdate = payload => {
            if (payload.probe_id === 'stale-status') __staleStatusApplications++;
            return receive(payload);
        };
    }""")
    reads = []
    page.route('**/session/status?*', lambda route: reads.append(route))
    page.evaluate('void onUpdateClick()')
    wait_for_held(page, reads, 1)
    stale = reads[0].fetch()
    payload = stale.json()
    payload.get('data', payload)['probe_id'] = 'stale-status'
    page.evaluate('toggleRowSelection(1, true); void onSaveClick(true)')
    page.wait_for_function('!isDirty() && !!AppState.selectedLemmas.baum')
    page.evaluate("document.getElementById('kw-btn-update').disabled = true")
    reads[0].fulfill(response=stale, json=payload)
    page.wait_for_function("document.getElementById('kw-btn-update').disabled === false")
    assert page.evaluate('!isDirty() && !!AppState.selectedLemmas.baum')
    assert page.evaluate('__staleStatusApplications') == 0
    assert adapter._read_shared_selection('20261023221555')['baum']
