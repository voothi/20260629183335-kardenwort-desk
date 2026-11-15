"""Admin controls through the shipped page, controller and an owned SQLite file."""
import sqlite3
import csv
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from kardenwort_db import KardenwortDB
from kardenwort_desk import SqliteStorageAdapter
from tests.controller_test_support import controller_world

pytestmark = pytest.mark.parametrize('controller_world', [{'backend': 'sqlite', 'seed_cache': False}], indirect=True)


@pytest.fixture
def admin_contract(page, controller_world):
    url, server = controller_world
    server.config.read_dict({
        'wordfill': {'enabled': 'false'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'},
    })
    db = KardenwortDB(config=server.config, resolved_paths=server.resolved_paths)
    db.run_migrations()
    server.resolved_paths['favorites_output_dir'] = db.db_path.parent/'favorites'
    for zid in ('20261112080001', '20261112080002'):
        db.insert_session(dict(zid=zid, raw_text='Haus.', clean_text='Haus.',
            source_language='de', target_language='en', text_mode='single', slug=zid))
    page.goto(url+'/admin?token='+server.api_key)
    expect(page.locator('#project-tree-container .empty-state')).to_have_text('No projects found.')
    try:
        yield page, db
    finally:
        page.context.close()


def submit(page, selector, endpoint):
    with page.expect_response(lambda response: response.request.method == 'POST'
                              and response.url.split('?')[0].endswith(endpoint)) as response:
        page.locator(selector).click()
    assert response.value.status == 200, response.value.text()


def node(page, project_id):
    return page.locator(f'.tree-node[data-id="{project_id}"] > .node-card')


def create_project(page, db, title, parent=None):
    if parent is None:
        page.locator('#btn-new-root-project').click()
    else:
        node(page, parent).get_by_role('button', name='+ Child', exact=True).click()
    page.locator('#project-title-input').fill(title)
    page.locator('#project-slug-input').fill(title.lower())
    page.locator('#project-desc-input').fill('Owned description')
    submit(page, '#btn-modal-save', '/api/v1/admin/projects')
    project = next(item for item in db.list_projects() if item['title'] == title)
    expect(node(page, project['id'])).to_contain_text(title)
    assert project['description'] == 'Owned description'
    assert project['parent_id'] == parent
    return project['id']


def test_admin_project_create_edit_cancel_delete_restore_and_purge(admin_contract):
    page, db = admin_contract
    root = create_project(page, db, 'OwnedRoot')
    child = create_project(page, db, 'OwnedChild', root)
    node(page, child).get_by_role('button', name='Edit', exact=True).click()
    page.locator('#project-title-input').fill('Cancelled edit')
    page.locator('#btn-modal-cancel').click()
    assert db.get_project(child)['title'] == 'OwnedChild'
    node(page, child).get_by_role('button', name='Edit', exact=True).click()
    page.locator('#project-title-input').fill('EditedChild')
    submit(page, '#btn-modal-save', '/api/v1/admin/projects/update')
    assert db.get_project(child)['title'] == 'EditedChild'
    page.reload()
    expect(node(page, child)).to_contain_text('EditedChild')
    node(page, child).get_by_role('button', name='Delete', exact=True).click()
    page.locator('#btn-confirm-cancel').click()
    assert db.get_project(child) is not None
    node(page, child).get_by_role('button', name='Delete', exact=True).click()
    submit(page, '#btn-confirm-ok', '/api/v1/admin/projects/delete')
    assert db.get_project(child) is None
    assert db.get_project(child, include_deleted=True)['deleted_at']
    page.locator('[data-tab="tab-trash"]').click()
    trash = page.locator('#deleted-projects-list .trash-item').filter(has_text='EditedChild')
    expect(trash).to_be_visible()
    with page.expect_response(lambda response: '/api/v1/admin/trash/restore' in response.url) as restored:
        trash.get_by_role('button', name='Restore', exact=True).click()
    assert restored.value.status == 200
    assert db.get_project(child)['title'] == 'EditedChild'
    page.reload()
    expect(node(page, child)).to_contain_text('EditedChild')
    node(page, child).get_by_role('button', name='Delete', exact=True).click()
    submit(page, '#btn-confirm-ok', '/api/v1/admin/projects/delete')
    page.locator('[data-tab="tab-trash"]').click()
    page.locator('#btn-purge-trash').click()
    submit(page, '#btn-confirm-ok', '/api/v1/admin/trash/purge')
    assert db.get_project(child, include_deleted=True) is None
    assert db.get_project(root)['title'] == 'OwnedRoot'
    page.reload()
    expect(node(page, root)).to_contain_text('OwnedRoot')
    expect(node(page, child)).to_have_count(0)


def test_admin_link_reorder_unlink_persist_after_reload(admin_contract):
    page, db = admin_contract
    project = create_project(page, db, 'LinkedRoot')
    zids = ['20261112080001', '20261112080002']
    for zid in zids:
        node(page, project).get_by_role('button', name='+ Link', exact=True).click()
        expect(page.locator(f'#link-session-select option[value="{zid}"]')).to_have_count(1)
        page.locator('#link-session-select').select_option(zid)
        submit(page, '#btn-link-modal-save', '/api/v1/admin/projects/link')
        expect(page.locator(f'.tree-node[data-id="{project}"] .btn-unlink')).to_have_count(zids.index(zid)+1)
    assert [item['session_zid'] for item in db.get_project_sessions(project)] == zids
    tree = page.locator(f'.tree-node[data-id="{project}"]')
    with page.expect_response(lambda response: '/api/v1/admin/projects/reorder' in response.url) as reordered:
        tree.get_by_title('Move Down', exact=True).first.click()
    assert reordered.value.status == 200
    assert [item['session_zid'] for item in db.get_project_sessions(project)] == zids[::-1]
    page.reload()
    expect(tree.get_by_role('button', name='Unlink', exact=True)).to_have_count(2)
    with page.expect_response(lambda response: '/api/v1/admin/projects/reorder' in response.url) as moved_up:
        tree.get_by_title('Move Up', exact=True).last.click()
    assert moved_up.value.status == 200
    assert [item['session_zid'] for item in db.get_project_sessions(project)] == zids
    with page.expect_response(lambda response: '/api/v1/admin/projects/reorder' in response.url) as moved_down:
        tree.get_by_title('Move Down', exact=True).first.click()
    assert moved_down.value.status == 200
    assert [item['session_zid'] for item in db.get_project_sessions(project)] == zids[::-1]
    page.reload()
    expect(tree.get_by_role('button', name='Unlink', exact=True)).to_have_count(2)
    # The rendered session order is the persisted order, independent of session-library sorting.
    assert tree.locator('.session-item').all_text_contents()[0].find(zids[1]) >= 0
    tree.get_by_role('button', name='Unlink', exact=True).first.click()
    page.locator('#btn-confirm-modal-close').click()
    assert len(db.get_project_sessions(project)) == 2
    tree.get_by_role('button', name='Unlink', exact=True).first.click()
    submit(page, '#btn-confirm-ok', '/api/v1/admin/projects/unlink')
    assert [item['session_zid'] for item in db.get_project_sessions(project)] == zids[:1]
    page.reload()
    expect(tree.get_by_role('button', name='Unlink', exact=True)).to_have_count(1)
    assert tree.locator('.session-item').inner_text().find(zids[0]) >= 0


@pytest.mark.parametrize('batch', [False, True], ids=['single', 'batch'])
def test_admin_session_assignment_delete_restore_and_purge(admin_contract, batch):
    page, db = admin_contract
    project = create_project(page, db, 'AssignmentRoot')
    zids = ['20261112080001', '20261112080002']
    page.locator('[data-tab="tab-sessions"]').click()
    expect(page.locator('#sessions-table-body tr[data-zid]')).to_have_count(2)
    if batch:
        page.locator('#master-sessions-checkbox').check()
        page.locator('#btn-batch-assign-sessions').click()
    else:
        page.locator(f'tr[data-zid="{zids[0]}"]').get_by_role('button', name='+ Assign', exact=True).click()
    page.locator('#assign-project-select').select_option(str(project))
    page.locator('#btn-assign-modal-cancel').click()
    assert db.get_project_sessions(project) == []
    if batch:
        page.locator('#btn-batch-assign-sessions').click()
    else:
        page.locator(f'tr[data-zid="{zids[0]}"]').get_by_role('button', name='+ Assign', exact=True).click()
    page.locator('#assign-project-select').select_option(str(project))
    endpoint = '/api/v1/admin/projects/batch-link' if batch else '/api/v1/admin/projects/link'
    submit(page, '#btn-assign-modal-save', endpoint)
    expected = zids if batch else zids[:1]
    assert sorted(item['session_zid'] for item in db.get_project_sessions(project)) == expected
    page.reload()
    expect(node(page, project).locator('..').locator('.session-item')).to_have_count(len(expected))
    page.locator('[data-tab="tab-sessions"]').click()
    expect(page.locator('#sessions-table-body tr[data-zid]')).to_have_count(2)
    if batch:
        page.locator('#master-sessions-checkbox').check()
        page.locator('#btn-batch-delete-sessions').click()
    else:
        page.locator(f'tr[data-zid="{zids[0]}"]').get_by_role('button', name='Del', exact=True).click()
    page.locator('#btn-confirm-cancel').click()
    assert all(db.get_session(zid) is not None for zid in zids)
    if batch:
        page.locator('#btn-batch-delete-sessions').click()
    else:
        page.locator(f'tr[data-zid="{zids[0]}"]').get_by_role('button', name='Del', exact=True).click()
    endpoint = '/api/v1/admin/sessions/batch-delete' if batch else '/api/v1/admin/sessions/delete'
    submit(page, '#btn-confirm-ok', endpoint)
    assert all(db.get_session(zid) is None for zid in expected)
    page.reload()
    page.locator('[data-tab="tab-trash"]').click()
    trash = page.locator('#deleted-sessions-list .trash-item').filter(has_text=zids[0])
    expect(trash).to_be_visible()
    with page.expect_response(lambda response: '/api/v1/admin/trash/restore' in response.url) as restored:
        trash.get_by_role('button', name='Restore', exact=True).click()
    assert restored.value.status == 200
    assert db.get_session(zids[0]) is not None
    assert zids[0] in [item['session_zid'] for item in db.get_project_sessions(project)]
    page.reload()
    expect(node(page, project).locator('..')).to_contain_text(zids[0])
    if batch:
        page.locator('[data-tab="tab-trash"]').click()
        page.locator('#btn-purge-trash').click()
        submit(page, '#btn-confirm-ok', '/api/v1/admin/trash/purge')
        assert db.get_session(zids[1], include_deleted=True) is None
        assert db.get_session(zids[0]) is not None
        assert db.get_project(project) is not None


def test_admin_rejected_project_save_preserves_input_and_retry_commits(admin_contract):
    page, db = admin_contract
    token = page.locator('#api-token-input').input_value()
    page.locator('#api-token-input').fill('invalid-owned-token')
    page.locator('#save-token-btn').click()
    page.locator('#btn-new-root-project').click()
    page.locator('#project-title-input').fill('RetriedRoot')
    with page.expect_response(lambda response: response.request.method == 'POST'
                              and '/api/v1/admin/projects' in response.url) as rejected:
        page.locator('#btn-modal-save').click()
    assert rejected.value.status == 403
    assert db.list_projects() == []
    expect(page.locator('#project-modal')).to_be_visible()
    expect(page.locator('#project-title-input')).to_have_value('RetriedRoot')
    page.locator('#btn-modal-close').click()
    page.locator('#api-token-input').fill(token)
    page.locator('#save-token-btn').click()
    page.locator('#btn-new-root-project').click()
    page.locator('#project-title-input').fill('RetriedRoot')
    submit(page, '#btn-modal-save', '/api/v1/admin/projects')
    projects = db.list_projects()
    assert len(projects) == 1
    assert projects[0]['title'] == 'RetriedRoot'
    page.reload()
    expect(node(page, projects[0]['id'])).to_contain_text('RetriedRoot')


def test_admin_snapshot_and_sql_download_restore_real_contents(admin_contract, tmp_path):
    page, db = admin_contract
    project = create_project(page, db, 'BackupRoot')
    page.locator('[data-tab="tab-maintenance"]').click()
    with page.expect_response(lambda response: '/api/v1/admin/backup/snapshot' in response.url) as snapshot:
        page.locator('#btn-snapshot-backup').click()
    assert snapshot.value.status == 200
    payload = snapshot.value.json()
    data = payload.get('data', payload)
    backup = Path(data['path']).resolve()
    assert backup.is_relative_to(db.db_path.parent.resolve())
    assert data['bytes'] == backup.stat().st_size
    with sqlite3.connect(backup) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert connection.execute('SELECT title FROM projects WHERE id=?', (project,)).fetchone() == ('BackupRoot',)
        assert connection.execute('SELECT COUNT(*) FROM sessions').fetchone() == (2,)
    with page.expect_download() as download:
        page.locator('#btn-sql-dump').click()
    destination = tmp_path/'download.sql'
    download.value.save_as(destination)
    assert download.value.suggested_filename == 'kardenwort-dump.sql'
    with sqlite3.connect(':memory:') as restored:
        restored.executescript(destination.read_text(encoding='utf-8'))
        assert restored.execute('SELECT title FROM projects WHERE id=?', (project,)).fetchone() == ('BackupRoot',)
        assert restored.execute('SELECT COUNT(*) FROM sessions').fetchone() == (2,)
    page.reload()
    expect(node(page, project)).to_contain_text('BackupRoot')


def test_admin_legacy_import_inspector_and_export_roundtrip(admin_contract):
    page, db = admin_contract
    zid = '20261112080003'
    text = ('Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n'
            'Haus\tHaus\thouse\t1\tDas Haus.\tThe house.\t1\n')
    page.locator('[data-tab="tab-sessions"]').click()
    with page.expect_file_chooser() as chooser:
        page.locator('#btn-browse-tsv').click()
    with page.expect_response(lambda response: '/api/v1/sessions/import-tsv' in response.url) as imported:
        chooser.value.set_files(dict(name=zid+'-owned.de.tsv', mimeType='text/tab-separated-values', buffer=text.encode()))
    assert imported.value.status == 200
    bundle = db.get_session_bundle(zid)
    assert bundle['session']['source_language'] == 'de'
    assert bundle['words'][0]['word_destination'] == 'house'
    assert bundle['words'][0]['selected'] == 1
    row = page.locator(f'#sessions-table-body tr[data-zid="{zid}"]')
    expect(row).to_be_visible()
    row.get_by_role('button', name='TSV', exact=True).click()
    viewer = page.locator('#tsv-raw-viewer')
    page.wait_for_function("document.getElementById('tsv-raw-viewer').value.includes('house')")
    exported = viewer.input_value()
    assert 'Das Haus.' in exported
    assert 'house' in exported
    with page.expect_download() as download:
        page.locator('#btn-tsv-download').click()
    assert download.value.failure() is None
    assert download.value.suggested_filename.startswith(zid)
    page.locator('#btn-tsv-modal-dismiss').click()
    expect(page.locator('#tsv-inspector-modal')).to_be_hidden()
    page.reload()
    page.locator('[data-tab="tab-sessions"]').click()
    expect(row).to_be_visible()
    assert db.get_session_bundle(zid)['words'] == bundle['words']


def test_admin_filtered_cross_page_assignment_preserves_exclusion_and_geometry(admin_contract):
    page, db = admin_contract
    project = create_project(page, db, 'PagedRoot')
    zids = [str(20261112081000+index) for index in range(51)]
    for zid in zids:
        db.insert_session(dict(zid=zid, raw_text='Tree.', clean_text='Tree.',
            source_language='en', target_language='de', text_mode='single', slug='page-owned-'+zid))
    page.reload()
    page.locator('[data-tab="tab-sessions"]').click()
    with page.expect_response(lambda response: '/api/v1/admin/sessions?' in response.url
                              and 'query=page-owned' in response.url):
        page.locator('#sessions-search-input').fill('page-owned')
    page.locator('#sessions-lang-filter').select_option('en')
    with page.expect_response(lambda response: '/api/v1/admin/sessions?' in response.url
                              and 'assigned=unassigned' in response.url):
        page.locator('#sessions-assign-filter').select_option('unassigned')
    expect(page.locator('#sessions-total-info')).to_have_text('Total: 51 sessions')
    expect(page.locator('#sessions-table-body tr[data-zid]')).to_have_count(50)
    row = page.locator('#sessions-table-body tr[data-zid]').first
    expect(row).to_be_visible()
    excluded = row.get_attribute('data-zid')
    before = row.bounding_box()
    row.locator('.session-row-checkbox').check()
    assert row.bounding_box() == before
    page.locator('#btn-batch-clear-selection').click()
    page.locator('#btn-master-dropdown-toggle').click()
    page.locator('[data-select="unassigned"]').click()
    expect(page.locator('#sessions-selected-count')).to_have_text('50 selected')
    page.locator('#btn-select-all-matching').click()
    expect(page.locator('#sessions-selected-count')).to_have_text('51 selected')
    row.locator('.session-row-checkbox').uncheck()
    expect(page.locator('#sessions-selected-count')).to_have_text('50 selected')
    page.locator('#btn-sessions-next').click()
    expect(page.locator('#sessions-page-info')).to_have_text('Page 2 of 2')
    expect(page.locator('#sessions-table-body tr[data-zid]')).to_have_count(1)
    page.locator('#btn-sessions-prev').click()
    expect(page.locator('#sessions-page-info')).to_have_text('Page 1 of 2')
    page.locator('#btn-batch-assign-sessions').click()
    page.locator('#assign-project-select').select_option(str(project))
    submit(page, '#btn-assign-modal-save', '/api/v1/admin/projects/batch-link')
    assert {item['session_zid'] for item in db.get_project_sessions(project)} == set(zids)-{excluded}
    page.reload()
    page.locator('[data-tab="tab-sessions"]').click()
    page.locator('#sessions-assign-filter').select_option('assigned')
    expect(page.locator('#sessions-total-info')).to_have_text('Total: 50 sessions')
    page.locator('#btn-master-dropdown-toggle').click()
    page.locator('[data-select="assigned"]').click()
    expect(page.locator('#sessions-selected-count')).to_have_text('50 selected')
    page.locator('#btn-master-dropdown-toggle').click()
    page.locator('[data-select="none"]').click()
    expect(page.locator('#sessions-batch-toolbar')).to_be_hidden()
    page.locator('#btn-master-dropdown-toggle').click()
    page.locator('[data-select="all"]').click()
    expect(page.locator('#sessions-selected-count')).to_have_text('50 selected')
    assert db.get_session(excluded) is not None


def test_admin_refresh_tabs_and_modal_close_preserve_database(admin_contract):
    page, db = admin_contract
    project = create_project(page, db, 'RefreshRoot')
    before = db.get_project(project)
    for tab, button, endpoint in [
        ('tree', 'btn-refresh-tree', '/api/v1/admin/projects'),
        ('sessions', 'btn-refresh-sessions', '/api/v1/admin/sessions'),
        ('trash', 'btn-refresh-trash', '/api/v1/admin/trash'),
        ('telemetry', 'btn-refresh-telemetry', '/api/v1/admin/telemetry'),
    ]:
        page.locator(f'[data-tab="tab-{tab}"]').click()
        with page.expect_response(lambda response: response.url.split('?')[0].endswith(endpoint)) as refreshed:
            page.locator('#'+button).click()
        assert refreshed.value.status == 200
        expect(page.locator('#tab-'+tab)).to_be_visible()
    expect(page.locator('#metric-active-sessions')).to_have_text('2')
    page.locator('[data-tab="tab-tree"]').click()
    for close in ['btn-link-modal-close', 'btn-link-modal-cancel']:
        node(page, project).get_by_role('button', name='+ Link', exact=True).click()
        page.locator('#'+close).click()
        expect(page.locator('#link-session-modal')).to_be_hidden()
        assert db.get_project_sessions(project) == []
    page.locator('[data-tab="tab-sessions"]').click()
    page.locator('tr[data-zid="20261112080001"]').get_by_role('button', name='+ Assign', exact=True).click()
    page.locator('#btn-assign-modal-close').click()
    expect(page.locator('#assign-project-modal')).to_be_hidden()
    assert db.get_project_sessions(project) == []
    assert db.get_project(project) == before


def test_admin_project_export_writes_only_selected_owned_material(admin_contract, controller_world):
    page, db = admin_contract
    _, server = controller_world
    project = create_project(page, db, 'ExportRoot')
    adapter = SqliteStorageAdapter(server.config, server.resolved_paths)
    zid = '20261112080001'
    adapter.save_session(session_zid=zid, source_language='de', target_language='en',
        text_mode='single', source_raw_text='Haus Baum.',
        headers=['Quotation', 'WordSource', 'WordDestination', 'DeskSelected', 'SentenceSource'],
        data_rows=[['Haus', 'Haus', 'house', '1', 'Haus Baum.'],
                   ['Baum', 'Baum', 'tree', '0', 'Haus Baum.']])
    db.link_session_to_project(project, zid)
    page.locator('#btn-refresh-tree').click()
    expect(page.locator(f'.tree-node[data-id="{project}"] .session-item')).to_have_count(1)
    before = db.get_session_bundle(zid)
    with page.expect_response(lambda response: '/api/v1/admin/projects/export-deck' in response.url) as exported:
        node(page, project).get_by_role('button', name='Export Deck', exact=True).click()
    assert exported.value.status == 200
    payload = exported.value.json()
    result = payload.get('data', payload)
    assert result['total_words'] == 1
    output = Path(result['tsv_path']).resolve()
    assert output.is_relative_to(db.db_path.parent.resolve())
    lines = [line for line in output.read_text(encoding='utf-8').splitlines() if not line.startswith('#')]
    rows = list(csv.DictReader(lines, delimiter='\t'))
    assert len(rows) == 1
    assert rows[0]['WordSource'] == 'Haus'
    assert rows[0]['WordDestination'] == 'house'
    assert rows[0]['Deck'].endswith('::ExportRoot')
    metadata = json.loads(Path(result['json_path']).read_text(encoding='utf-8'))
    assert any(key.endswith('::ExportRoot') for key in metadata['deck_descriptions'])
    assert db.get_session_bundle(zid) == before
    page.reload()
    expect(node(page, project)).to_contain_text('ExportRoot')


@pytest.mark.parametrize('scope', ['project', 'session'])
@pytest.mark.parametrize('view', ['desk', 'dict'])
def test_admin_reader_buttons_open_owned_rendered_view(admin_contract, controller_world, scope, view):
    page, db = admin_contract
    _, server = controller_world
    project = create_project(page, db, 'ReaderRoot')
    zid = '20261112080001'
    adapter = SqliteStorageAdapter(server.config, server.resolved_paths)
    adapter.save_session(session_zid=zid, source_language='de', target_language='en',
        text_mode='single', source_raw_text='Haus.',
        headers=['Quotation', 'WordSource', 'WordDestination', 'DeskSelected', 'SentenceSource', 'SentenceSourceIndex'],
        data_rows=[['Haus', 'Haus', 'house', '1', 'Haus.', '1']])
    db.link_session_to_project(project, zid)
    if scope == 'project':
        page.locator('#btn-refresh-tree').click()
        expect(page.locator(f'.tree-node[data-id="{project}"] .session-item')).to_have_count(1)
        target = node(page, project)
    else:
        page.locator('[data-tab="tab-sessions"]').click()
        target = page.locator(f'#sessions-table-body tr[data-zid="{zid}"]')
    before = db.get_session_bundle(zid)
    with page.expect_popup() as opened:
        target.get_by_role('button', name='Desk' if view == 'desk' else 'Dict', exact=True).click()
    popup = opened.value
    try:
        popup.wait_for_load_state('domcontentloaded')
        assert f'project_id={project}' in popup.url if scope == 'project' else f'session_zid={zid}' in popup.url
        assert ('view=goldendict' in popup.url) is (view == 'dict')
        expect(popup.locator('body')).to_contain_text('Haus')
        assert db.get_session_bundle(zid) == before
    finally:
        popup.close()


def test_admin_inspector_copies_to_owned_clipboard_port_and_closes(admin_contract):
    page, db = admin_contract
    # Clipboard is an unmanaged OS port; this case never touches the user's clipboard.
    page.evaluate('''() => {
        window.__copiedText = null;
        Object.defineProperty(navigator, 'clipboard', {value: {
            writeText: async text => { window.__copiedText = text; }
        }, configurable: true});
    }''')
    page.locator('[data-tab="tab-sessions"]').click()
    row = page.locator('#sessions-table-body tr[data-zid="20261112080001"]')
    row.get_by_role('button', name='TSV', exact=True).click()
    page.wait_for_function("document.getElementById('tsv-raw-viewer').value.includes('WordSource')")
    exported = page.locator('#tsv-raw-viewer').input_value()
    page.locator('#btn-tsv-copy').click()
    page.wait_for_function('window.__copiedText !== null')
    assert page.evaluate('window.__copiedText') == exported
    page.locator('#btn-tsv-modal-close').click()
    expect(page.locator('#tsv-inspector-modal')).to_be_hidden()
    assert len(db.list_sessions()) == 2


@pytest.mark.parametrize('blocked', [False, True], ids=['completed', 'write-lock-failure'])
def test_admin_vacuum_reports_actual_sqlite_result(admin_contract, blocked):
    page, db = admin_contract
    before = db.list_sessions()
    page.locator('[data-tab="tab-maintenance"]').click()
    blocker = None
    if blocked:
        blocker = sqlite3.connect(db.db_path, isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
    try:
        with page.expect_response(lambda response: '/api/v1/admin/db/vacuum' in response.url) as vacuum:
            page.locator('#btn-vacuum-db').click()
            if blocked:
                expect(page.locator('#btn-vacuum-db')).to_be_disabled()
        assert vacuum.value.status == (500 if blocked else 200), vacuum.value.text()
        expect(page.locator('#vacuum-status')).to_have_text('Failed' if blocked else 'Finished')
        expect(page.locator('#btn-vacuum-db')).to_be_enabled()
    finally:
        if blocker is not None:
            blocker.rollback()
            blocker.close()
    assert db.list_sessions() == before
    page.reload()
    page.locator('[data-tab="tab-telemetry"]').click()
    page.wait_for_function("document.getElementById('raw-telemetry-json').textContent.includes('integrity_ok')")
    assert json.loads(page.locator('#raw-telemetry-json').inner_text())['database']['integrity_ok'] is True
