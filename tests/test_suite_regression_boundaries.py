"""Desktop compatibility must not weaken occurrence or authentication boundaries."""
import json
from pathlib import Path

import pytest

import kardenwort_desk as desk
from kardenwort_controller import SessionArbiter
from tests.test_dict_transport_audit_regressions import ZID, destinations, seed_sqlite
from tests.test_ui_interactions import get_base_html, init_appstate
from tests.test_release_save_convergence import hold_stream_recovery
from tests.test_release_boundary_contracts import (
    open_browser, restored_membership, save_selection, seed_session, use_backend, wait_for_held,
)
from tests.test_controller_integration import running_controller  # noqa: F401


def test_sqlite_adapter_honors_explicit_database_file(tmp_path):
    database = tmp_path / 'declared.db'
    adapter = desk.SqliteStorageAdapter(resolved_paths={'database_file': database})
    assert Path(adapter.db.db_path).resolve() == database.resolve()
    assert database.is_file()


def test_draft_rows_keep_distinct_history_and_reject_unresolved_sqlite_save(page, tmp_path):
    html, _, _, headers, rows = get_base_html()
    page.set_content(html)
    page.evaluate("""() => {
        const row = document.querySelector('tr[data-row-id="0"]').cloneNode(true);
        row.dataset.rowId = '1';
        document.querySelector('#lemma-table tbody').appendChild(row);
    }""")
    init_appstate(page, rows * 2, headers)
    for index, value in enumerate(('cottage', 'tree')):
        cell = page.locator(f'tr[data-row-id="{index}"] td[data-col="WordDestination"]')
        cell.dblclick()
        editor = cell.locator('input')
        editor.fill(value)
        editor.press('Enter')
    deltas = json.loads(page.evaluate('getDeltas()'))
    assert [(d['row_id'], d['value']) for d in deltas] == [(0, 'cottage'), (1, 'tree')]
    assert all(d['sentence_idx'] == '' and d['token_order'] == '' for d in deltas)
    page.evaluate('undo()')
    assert [(d['row_id'], d['value']) for d in json.loads(page.evaluate('getDeltas()'))] == [(0, 'cottage')]
    cfg, paths, adapter = seed_sqlite(tmp_path)
    with pytest.raises(desk.StructuredError):
        desk.core_edit_save(ZID, deltas, cfg, paths)
    assert destinations(adapter) == ['house', 'tree']


@pytest.mark.parametrize('boundary', ['controller', 'adapter'])
@pytest.mark.parametrize('targets', [
    [{'visual_idx': 0}, {'sentence_idx': 2, 'token_order': 0}],
    [{'sentence_idx': 2}],
    [{'visual_idx': -1}],
])
def test_invalid_reword_target_formats_reject_without_writes(tmp_path, boundary, targets):
    cfg, paths, adapter = seed_sqlite(tmp_path)
    before = adapter.db.get_words_by_session(ZID)
    with pytest.raises(desk.StructuredError):
        if boundary == 'controller':
            arbiter = SessionArbiter(config=cfg, resolved_paths=paths)
            arbiter.reword_session(ZID, selected_rows=[0], target_coordinates=targets, language='de')
        else:
            adapter.enrich_session_intellifiller(ZID, 'fixture', selected_rows=[0], target_coordinates=targets)
    assert adapter.db.get_words_by_session(ZID) == before


def test_visual_context_retains_selected_sqlite_occurrence(tmp_path, monkeypatch):
    _, _, adapter = seed_sqlite(tmp_path)

    def provider(tsv_path, selected_rows=None, **kwargs):
        comments, headers, rows = desk.load_tsv_rows(tsv_path)
        for index in selected_rows:
            rows[index][headers.index('WordDestination')] = 'cottage'
        desk.save_tsv_rows_safely(tsv_path, comments, headers, rows)
        return True

    monkeypatch.setattr(desk, 'run_headless_intellifiller', provider)
    assert adapter.enrich_session_intellifiller(
        ZID, 'fixture', selected_rows=[0], reprocess=True,
        target_coordinates=[{'visual_idx': 1, 'sentence_idx': 99}])
    assert destinations(adapter) == ['cottage', 'tree']

def test_watchdog_waits_for_membership_recovery(page, tmp_path, running_controller):
    reads = hold_stream_recovery(page, tmp_path, running_controller)
    statuses = []
    page.route('**/session/status*', lambda route: statuses.append(route))
    page.evaluate('_kwPollSessionStatus()')
    page.evaluate('Boolean(window.getDeltas)')
    assert statuses == []
    reads[0].fulfill(response=reads[0].fetch())
    page.wait_for_function("__recoveryResult === 'resolved'")

def test_status_started_before_recovery_is_discarded(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter, selected=True)
    open_browser(page, tmp_path, url, selected=True)
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.sync === 'synced'")
    page.clock.install()
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)
    page.evaluate("AppState.translatedText='House. Tree.'; document.querySelector('#translation-container').textContent='House. Tree.'")
    page.evaluate('''() => {
        window.__staleStatusApplications = 0;
        const receive = window.receiveUpdate;
        window.receiveUpdate = payload => {
            if (payload.probe_id === 'stale-status') __staleStatusApplications++;
            return receive(payload);
        };
    }''')
    statuses, reads = [], []
    page.route('**/session/status?*', lambda route: statuses.append(route))
    page.evaluate('void onUpdateClick()')
    wait_for_held(page, statuses, 1)
    stale = statuses[0].fetch()
    payload = stale.json()
    payload.get('data', payload)['probe_id'] = 'stale-status'
    page.route('**/internal/selection', lambda route: reads.append(route))
    page.evaluate('''() => {
        window.__recoveryResult = 'pending';
        reconcileStreamMembership({session:SSEBridge.getActiveZid(),connectionId:SSEBridge.getActiveConnectionId()})
            .then(()=>{__recoveryResult='resolved';},()=>{__recoveryResult='rejected';});
    }''')
    wait_for_held(page, reads, 1)
    save_selection(url, 0, False)
    save_selection(url, 1, True)
    reads[0].fulfill(response=reads[0].fetch())
    page.wait_for_function("__recoveryResult === 'resolved'")
    page.evaluate("document.getElementById('kw-btn-update').disabled = true")
    statuses[0].fulfill(response=stale, json=payload)
    page.wait_for_function("document.getElementById('kw-btn-update').disabled === false")
    assert page.evaluate('__staleStatusApplications') == 0
    assert page.evaluate('!AppState.selectedLemmas.haus && !!AppState.selectedLemmas.baum && !isDirty()')
    assert restored_membership(adapter) == {'haus': False, 'baum': True}
