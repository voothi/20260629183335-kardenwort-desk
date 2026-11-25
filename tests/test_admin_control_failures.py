"""Negative admin actions preserve real records and leave a usable interface."""
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.controller_test_support import controller_world
from tests.test_admin_persisted_controls import admin_contract, create_project, submit

pytestmark = pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)


def test_empty_project_title_stays_editable_without_request_or_commit(admin_contract):
    page, db = admin_contract
    requests = []
    page.on('request', lambda request: requests.append(request.url) if request.method == 'POST' else None)
    page.locator('#btn-new-root-project').click()
    page.locator('#project-title-input').fill('   ')
    page.locator('#project-desc-input').fill('Preserve this draft')
    page.locator('#btn-modal-save').click()
    expect(page.locator('#toast-container')).to_contain_text('Title is required')
    expect(page.locator('#project-modal')).to_be_visible()
    expect(page.locator('#project-desc-input')).to_have_value('Preserve this draft')
    assert requests == []
    assert db.list_projects() == []
    page.locator('#btn-modal-cancel').click()
    page.reload()
    assert db.list_projects() == []


def test_snapshot_path_failure_is_visible_and_can_be_retried(admin_contract):
    page, db = admin_contract
    before = db.get_session_bundle('20261112080001')
    obstruction = db.db_path.parent/'backup'
    obstruction.write_text('Owned backup obstruction', encoding='utf-8')
    page.locator('[data-tab="tab-maintenance"]').click()
    with page.expect_response(lambda response: '/api/v1/admin/backup/snapshot' in response.url) as failed:
        page.locator('#btn-snapshot-backup').click()
    assert failed.value.status == 500, failed.value.text()
    expect(page.locator('#snapshot-status')).to_have_text('Failed')
    expect(page.locator('#toast-container .error')).to_be_visible()
    assert obstruction.read_text(encoding='utf-8') == 'Owned backup obstruction'
    assert db.get_session_bundle('20261112080001') == before
    obstruction.unlink()
    with page.expect_response(lambda response: '/api/v1/admin/backup/snapshot' in response.url) as retried:
        page.locator('#btn-snapshot-backup').click()
    assert retried.value.status == 200, retried.value.text()
    payload = retried.value.json()
    result = payload.get('data', payload)
    destination = Path(result['path']).resolve()
    assert destination.is_relative_to(db.db_path.parent.resolve())
    with sqlite3.connect(destination) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert db.get_session_bundle('20261112080001') == before


def test_rejected_sql_download_opens_error_without_download_or_mutation(admin_contract):
    page, db = admin_contract
    before = db.get_session_bundle('20261112080001')
    page.locator('#api-token-input').fill('owned-invalid-token')
    page.locator('#save-token-btn').click()
    page.locator('[data-tab="tab-maintenance"]').click()
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    # Spec 20261122213459 Decision 4: SQL export uses authenticated fetch/Blob (no URL-token popup) and surfaces errors in-page.
    with page.expect_response(lambda response: '/api/v1/admin/backup/dump.sql' in response.url) as rejected:
        page.locator('#btn-sql-dump').click()
    assert rejected.value.status == 403
    expect(page.locator('#toast-container .toast.error').last).to_contain_text('SQL dump failed')
    assert downloads == []
    assert db.get_session_bundle('20261112080001') == before


@pytest.mark.parametrize('content', ['', 'UnknownColumn\nnot-a-session\n',
    'WordSource\tWordDestination\n   \tignored\n',
    'WordSource\tWordDestination\nHaus\thouse\n\tbroken\n'])
def test_malformed_tsv_import_rejects_without_changing_existing_records(admin_contract, content):
    page, db = admin_contract
    before = {zid: db.get_session_bundle(zid) for zid in ['20261112080001', '20261112080002']}
    page.locator('[data-tab="tab-sessions"]').click()
    with page.expect_file_chooser() as chooser:
        page.locator('#btn-browse-tsv').click()
    with page.expect_response(lambda response: '/api/v1/sessions/import-tsv' in response.url) as rejected:
        chooser.value.set_files(dict(name='20261112212501-invalid.de.tsv', mimeType='text/tab-separated-values', buffer=content.encode()))
    assert rejected.value.status == 400, rejected.value.text()
    expect(page.locator('#drop-zone-status')).to_contain_text('1 failed')
    assert db.get_session_bundle('20261112212501') is None
    assert {zid: db.get_session_bundle(zid) for zid in before} == before
    page.reload()
    assert {zid: db.get_session_bundle(zid) for zid in before} == before


def test_shift_range_and_cross_page_assignment_persist_exact_membership(admin_contract):
    page, db = admin_contract
    project = create_project(page, db, 'RangeRoot')
    for index in range(51):
        zid = str(20261112212600+index)
        db.insert_session(dict(zid=zid, raw_text='Tree.', clean_text='Tree.', source_language='en',
            target_language='de', text_mode='single', slug='range-owned-'+zid))
    page.reload()
    page.locator('[data-tab="tab-sessions"]').click()
    with page.expect_response(lambda response: '/api/v1/admin/sessions?' in response.url and 'query=range-owned' in response.url):
        page.locator('#sessions-search-input').fill('range-owned')
    rows = page.locator('#sessions-table-body tr[data-zid]')
    expect(rows).to_have_count(50)
    zids = rows.evaluate_all('rows => rows.map(row => row.dataset.zid)')
    box = rows.nth(1).bounding_box()
    rows.nth(1).locator('.session-row-checkbox').click()
    rows.nth(3).locator('.session-row-checkbox').click(modifiers=['Shift'])
    expect(page.locator('#sessions-selected-count')).to_have_text('3 selected')
    assert rows.nth(1).bounding_box() == box
    rows.nth(2).locator('.session-row-checkbox').click(modifiers=['Shift'])
    expect(page.locator('#sessions-selected-count')).to_have_text('1 selected')
    page.locator('#btn-sessions-next').click()
    expect(rows).to_have_count(1)
    last = rows.first.get_attribute('data-zid')
    rows.first.locator('.session-row-checkbox').click(modifiers=['Shift'])
    expect(page.locator('#sessions-selected-count')).to_have_text('2 selected')
    page.locator('#btn-batch-assign-sessions').click()
    page.locator('#assign-project-select').select_option(str(project))
    submit(page, '#btn-assign-modal-save', '/api/v1/admin/projects/batch-link')
    assert {item['session_zid'] for item in db.get_project_sessions(project)} == {zids[1], last}
    page.reload()
    assert {item['session_zid'] for item in db.get_project_sessions(project)} == {zids[1], last}


@pytest.mark.parametrize('source', ['Quotation', 'WordSourceInflectedForm'])
def test_import_retains_declared_legacy_lemma_columns(admin_contract, source):
    page, db = admin_contract
    page.locator('[data-tab="tab-sessions"]').click()
    content = source+'\tWordDestination\nHaus\thouse\n'
    with page.expect_file_chooser() as chooser:
        page.locator('#btn-browse-tsv').click()
    with page.expect_response(lambda response: '/api/v1/sessions/import-tsv' in response.url) as imported:
        chooser.value.set_files(dict(name='20261112212502-legacy.de.tsv', mimeType='text/tab-separated-values', buffer=content.encode()))
    assert imported.value.status == 200, imported.value.text()
    words = db.get_session_bundle('20261112212502')['words']
    assert len(words) == 1
    assert words[0]['lemma'] == 'Haus'
    assert words[0]['word_destination'] == 'house'


@pytest.mark.parametrize('fallback', ['accepted', 'rejected', 'exception'])
def test_inspector_clipboard_reports_actual_external_port_result(admin_contract, fallback):
    page, db = admin_contract
    before = db.get_session_bundle('20261112080001')
    page.evaluate('''fallback => {
        Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
            writeText: () => Promise.reject(new Error('Owned clipboard permission denial'))}});
        window.__copyCommands = [];
        document.execCommand = command => {
            window.__copyCommands.push(command);
            if (fallback === 'exception') throw new Error('Owned legacy clipboard failure');
            return fallback === 'accepted';
        };
    }''', fallback)
    page.locator('[data-tab="tab-sessions"]').click()
    page.locator('tr[data-zid="20261112080001"]').get_by_role('button', name='TSV', exact=True).click()
    page.wait_for_function("document.getElementById('tsv-raw-viewer').value.includes('WordSource')")
    page.locator('#btn-tsv-copy').click()
    if fallback == 'accepted':
        expect(page.locator('#toast-container .success')).to_contain_text('copied')
    else:
        expect(page.locator('#toast-container .error')).to_contain_text('Copy failed')
        expect(page.locator('#toast-container .success')).to_have_count(0)
    assert page.evaluate('window.__copyCommands') == ['copy']
    assert db.get_session_bundle('20261112080001') == before
