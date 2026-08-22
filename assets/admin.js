/**
 * Kardenwort Desk Admin Panel - Frontend Controller
 * Utilitarian Minimalist Edition
 */

// State Management
const state = {
    token: localStorage.getItem('kardenwort_api_token') || new URLSearchParams(window.location.search).get('token') || '',
    activeTab: 'tab-tree',
    projectTree: [],
    allSessions: [],
    deletedSessions: [],
    deletedProjects: [],
    telemetry: null
};

// Helper: HTTP Request with API Token
async function apiFetch(endpoint, options = {}) {
    const headers = options.headers || {};
    if (state.token) {
        headers['X-API-Token'] = state.token;
    }
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';

    let url = endpoint;
    if (state.token && !url.includes('token=')) {
        const separator = url.includes('?') ? '&' : '?';
        url += `${separator}token=${encodeURIComponent(state.token)}`;
    }

    try {
        const response = await fetch(url, { ...options, headers });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.message || data.error || `HTTP ${response.status}`);
        }
        return data.data !== undefined ? data.data : data;
    } catch (err) {
        showToast(err.message, 'error');
        throw err;
    }
}

// Toast Notifications (Minimalist & Plaintext)
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const prefix = type === 'success' ? '[OK]' : type === 'error' ? '[ERR]' : '[INFO]';
    toast.innerHTML = `<span>${prefix}</span> <span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.remove();
    }, 4000);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
}

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    initAuthToken();
    initTabs();
    initModals();
    initMaintenance();
    
    // Initial Load
    checkServerHealth();
    loadProjectTree();
    loadTrash();
    loadTelemetry();
});

// ---------------------------------------------------------------------------
// 1. Auth & Server Status
// ---------------------------------------------------------------------------
function initAuthToken() {
    const tokenInput = document.getElementById('api-token-input');
    const saveTokenBtn = document.getElementById('save-token-btn');

    if (state.token) {
        tokenInput.value = state.token;
    }

    saveTokenBtn.addEventListener('click', () => {
        state.token = tokenInput.value.trim();
        localStorage.setItem('kardenwort_api_token', state.token);
        showToast('API Token saved', 'success');
        checkServerHealth();
        refreshCurrentTab();
    });
}

async function checkServerHealth() {
    const statusPill = document.getElementById('server-status-pill');
    const statusText = document.getElementById('server-status-text');
    try {
        const data = await apiFetch('/api/v1/health');
        if (data.ok) {
            statusPill.className = 'status-pill online';
            statusText.textContent = `Online (Port ${data.controller?.port || 18335})`;
        }
    } catch (e) {
        statusPill.className = 'status-pill error';
        statusText.textContent = 'Disconnected / Auth Error';
    }
}

// ---------------------------------------------------------------------------
// 2. Navigation Tabs
// ---------------------------------------------------------------------------
function initTabs() {
    document.querySelectorAll('.nav-tab').forEach(tabBtn => {
        tabBtn.addEventListener('click', () => {
            document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

            tabBtn.classList.add('active');
            const targetId = tabBtn.getAttribute('data-tab');
            const targetPane = document.getElementById(targetId);
            if (targetPane) {
                targetPane.classList.add('active');
                state.activeTab = targetId;
                refreshCurrentTab();
            }
        });
    });

    document.getElementById('btn-refresh-tree').addEventListener('click', loadProjectTree);
    document.getElementById('btn-refresh-trash').addEventListener('click', loadTrash);
    document.getElementById('btn-refresh-telemetry').addEventListener('click', loadTelemetry);
}

function refreshCurrentTab() {
    if (state.activeTab === 'tab-tree') loadProjectTree();
    else if (state.activeTab === 'tab-trash') loadTrash();
    else if (state.activeTab === 'tab-telemetry') loadTelemetry();
}

// ---------------------------------------------------------------------------
// 3. Project Tree Explorer
// ---------------------------------------------------------------------------
async function loadProjectTree() {
    const container = document.getElementById('project-tree-container');
    try {
        const res = await apiFetch('/api/v1/admin/projects');
        state.projectTree = res.projects || [];
        renderProjectTree(state.projectTree, container);
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed to load project tree. Verify token or server connection.</div>`;
    }
}

function renderProjectTree(nodes, container) {
    if (!nodes || nodes.length === 0) {
        container.innerHTML = `<div class="empty-state">No projects found. Click "+ New Root Project" to create a book or collection.</div>`;
        return;
    }

    container.innerHTML = '';
    const rootList = document.createElement('div');
    rootList.className = 'tree-roots';

    nodes.forEach(node => {
        rootList.appendChild(createNodeElement(node));
    });
    container.appendChild(rootList);
}

function createNodeElement(node) {
    const nodeWrapper = document.createElement('div');
    nodeWrapper.className = 'tree-node';
    nodeWrapper.setAttribute('data-id', node.id);

    const hasChildren = (node.children && node.children.length > 0) || (node.sessions && node.sessions.length > 0);

    const card = document.createElement('div');
    card.className = 'node-card';
    card.innerHTML = `
        <div class="node-info">
            <span class="node-toggle" data-id="${node.id}">${hasChildren ? '▼' : '·'}</span>
            <span class="node-title">${escapeHtml(node.title)}</span>
            <span class="node-slug">${escapeHtml(node.slug)}</span>
            ${node.description ? `<span class="node-desc">${escapeHtml(node.description)}</span>` : ''}
        </div>
        <div class="node-actions">
            <button class="btn btn-secondary btn-sm btn-add-child" data-id="${node.id}" title="Add Sub-Chapter">+ Sub-Chapter</button>
            <button class="btn btn-secondary btn-sm btn-link-session" data-id="${node.id}" title="Link Session">+ Link Session</button>
            <button class="btn btn-secondary btn-sm btn-edit-node" data-id="${node.id}" title="Edit Project">[Edit]</button>
            <button class="btn btn-danger btn-sm btn-delete-node" data-id="${node.id}" title="Delete Project">[Delete]</button>
        </div>
    `;

    // Event listeners
    card.querySelector('.node-toggle').addEventListener('click', (e) => {
        const childrenDiv = nodeWrapper.querySelector('.node-children-wrapper');
        if (childrenDiv) {
            const isHidden = childrenDiv.style.display === 'none';
            childrenDiv.style.display = isHidden ? 'block' : 'none';
            e.target.textContent = isHidden ? '▼' : '▶';
        }
    });

    card.querySelector('.btn-add-child').addEventListener('click', () => openProjectModal(null, node.id));
    card.querySelector('.btn-link-session').addEventListener('click', () => openLinkSessionModal(node.id));
    card.querySelector('.btn-edit-node').addEventListener('click', () => openProjectModal(node));
    card.querySelector('.btn-delete-node').addEventListener('click', () => deleteProject(node.id, node.title));

    nodeWrapper.appendChild(card);

    // Children & Sessions wrapper
    const childrenWrapper = document.createElement('div');
    childrenWrapper.className = 'node-children-wrapper';

    // Render Linked Sessions
    if (node.sessions && node.sessions.length > 0) {
        const sessionList = document.createElement('div');
        sessionList.className = 'session-list';
        node.sessions.forEach((s, idx) => {
            sessionList.appendChild(createSessionElement(s, node.id, idx, node.sessions.length));
        });
        childrenWrapper.appendChild(sessionList);
    }

    // Render Child Projects
    if (node.children && node.children.length > 0) {
        const childContainer = document.createElement('div');
        childContainer.className = 'node-children';
        node.children.forEach(childNode => {
            childContainer.appendChild(createNodeElement(childNode));
        });
        childrenWrapper.appendChild(childContainer);
    }

    nodeWrapper.appendChild(childrenWrapper);
    return nodeWrapper;
}

function createSessionElement(session, projectId, index, total) {
    const item = document.createElement('div');
    item.className = 'session-item';
    item.setAttribute('data-zid', session.session_zid);
    item.innerHTML = `
        <div class="session-item-info">
            <span class="session-badge">${escapeHtml(session.session_zid)}</span>
            <span class="session-slug">${escapeHtml(session.slug || 'untitled')}</span>
            <span class="text-muted" style="font-size: 11px;">(${escapeHtml(session.source_language || '')})</span>
        </div>
        <div class="session-actions">
            <button class="btn btn-icon btn-move-up" ${index === 0 ? 'disabled' : ''} title="Move Up">[Up]</button>
            <button class="btn btn-icon btn-move-down" ${index === total - 1 ? 'disabled' : ''} title="Move Down">[Down]</button>
            <button class="btn btn-icon btn-unlink" title="Unlink from Chapter">[Unlink]</button>
        </div>
    `;

    item.querySelector('.btn-move-up').addEventListener('click', () => moveSessionOrder(projectId, index, -1));
    item.querySelector('.btn-move-down').addEventListener('click', () => moveSessionOrder(projectId, index, 1));
    item.querySelector('.btn-unlink').addEventListener('click', () => unlinkSession(projectId, session.session_zid));

    return item;
}

async function moveSessionOrder(projectId, currentIndex, delta) {
    const findNode = (nodes) => {
        for (const n of nodes) {
            if (n.id === projectId) return n;
            if (n.children) {
                const found = findNode(n.children);
                if (found) return found;
            }
        }
        return null;
    };

    const node = findNode(state.projectTree);
    if (!node || !node.sessions) return;

    const newIndex = currentIndex + delta;
    if (newIndex < 0 || newIndex >= node.sessions.length) return;

    const reorderedZids = node.sessions.map(s => s.session_zid);
    const temp = reorderedZids[currentIndex];
    reorderedZids[currentIndex] = reorderedZids[newIndex];
    reorderedZids[newIndex] = temp;

    try {
        await apiFetch('/api/v1/admin/projects/reorder', {
            method: 'POST',
            body: JSON.stringify({
                project_id: projectId,
                session_zids: reorderedZids
            })
        });
        showToast('Session order updated', 'success');
        loadProjectTree();
    } catch (e) {
        // error toast shown by apiFetch
    }
}

async function unlinkSession(projectId, sessionZid) {
    if (!confirm(`Unlink session ${sessionZid} from this chapter?`)) return;
    try {
        await apiFetch('/api/v1/admin/projects/unlink', {
            method: 'POST',
            body: JSON.stringify({
                project_id: projectId,
                session_zid: sessionZid
            })
        });
        showToast('Session unlinked', 'success');
        loadProjectTree();
    } catch (e) {}
}

async function deleteProject(projectId, title) {
    if (!confirm(`Are you sure you want to delete project "${title}"? All sub-chapters will also be moved to the recycle bin.`)) return;
    try {
        await apiFetch('/api/v1/admin/projects/delete', {
            method: 'POST',
            body: JSON.stringify({ project_id: projectId })
        });
        showToast(`Project "${title}" moved to Recycle Bin`, 'success');
        loadProjectTree();
        loadTrash();
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// 4. Modals (Project Create/Edit & Session Link)
// ---------------------------------------------------------------------------
function initModals() {
    const projectModal = document.getElementById('project-modal');
    const linkModal = document.getElementById('link-session-modal');

    document.getElementById('btn-new-root-project').addEventListener('click', () => openProjectModal());
    document.getElementById('btn-modal-close').addEventListener('click', () => projectModal.classList.add('hidden'));
    document.getElementById('btn-modal-cancel').addEventListener('click', () => projectModal.classList.add('hidden'));

    document.getElementById('btn-modal-save').addEventListener('click', saveProjectModal);

    document.getElementById('btn-link-modal-close').addEventListener('click', () => linkModal.classList.add('hidden'));
    document.getElementById('btn-link-modal-cancel').addEventListener('click', () => linkModal.classList.add('hidden'));
    document.getElementById('btn-link-modal-save').addEventListener('click', saveLinkSessionModal);
}

function openProjectModal(node = null, parentId = null) {
    const modal = document.getElementById('project-modal');
    const titleEl = document.getElementById('modal-project-title');
    const idInput = document.getElementById('modal-project-id');
    const parentInput = document.getElementById('modal-parent-id');
    const titleInput = document.getElementById('project-title-input');
    const slugInput = document.getElementById('project-slug-input');
    const descInput = document.getElementById('project-desc-input');

    if (node) {
        titleEl.textContent = 'Edit Project';
        idInput.value = node.id;
        parentInput.value = node.parent_id || '';
        titleInput.value = node.title || '';
        slugInput.value = node.slug || '';
        descInput.value = node.description || '';
    } else {
        titleEl.textContent = parentId ? 'Create Sub-Chapter' : 'Create Root Project';
        idInput.value = '';
        parentInput.value = parentId || '';
        titleInput.value = '';
        slugInput.value = '';
        descInput.value = '';
    }

    modal.classList.remove('hidden');
    titleInput.focus();
}

async function saveProjectModal() {
    const id = document.getElementById('modal-project-id').value;
    const parentId = document.getElementById('modal-parent-id').value;
    const title = document.getElementById('project-title-input').value.trim();
    const slug = document.getElementById('project-slug-input').value.trim();
    const description = document.getElementById('project-desc-input').value.trim();

    if (!title) {
        showToast('Project title is required', 'error');
        return;
    }

    try {
        if (id) {
            // Update
            await apiFetch('/api/v1/admin/projects/update', {
                method: 'POST',
                body: JSON.stringify({
                    project_id: parseInt(id),
                    title,
                    slug: slug || undefined,
                    description
                })
            });
            showToast('Project updated', 'success');
        } else {
            // Create
            await apiFetch('/api/v1/admin/projects', {
                method: 'POST',
                body: JSON.stringify({
                    title,
                    slug: slug || undefined,
                    parent_id: parentId ? parseInt(parentId) : null,
                    description
                })
            });
            showToast('Project created', 'success');
        }
        document.getElementById('project-modal').classList.add('hidden');
        loadProjectTree();
    } catch (e) {}
}

async function openLinkSessionModal(projectId) {
    const modal = document.getElementById('link-session-modal');
    document.getElementById('link-modal-project-id').value = projectId;
    const select = document.getElementById('link-session-select');
    select.innerHTML = '<option value="">Loading sessions...</option>';
    modal.classList.remove('hidden');

    try {
        const res = await apiFetch('/api/v1/admin/sessions');
        const sessions = res.sessions || [];
        if (sessions.length === 0) {
            select.innerHTML = '<option value="">No sessions available</option>';
            return;
        }
        select.innerHTML = sessions.map(s => 
            `<option value="${s.zid}">${escapeHtml(s.zid)} - ${escapeHtml(s.slug || 'Untitled')} (${s.source_language || ''})</option>`
        ).join('');
    } catch (e) {
        select.innerHTML = '<option value="">Failed to load sessions</option>';
    }
}

async function saveLinkSessionModal() {
    const projectId = document.getElementById('link-modal-project-id').value;
    const sessionZid = document.getElementById('link-session-select').value;
    if (!sessionZid) {
        showToast('Please select a session', 'error');
        return;
    }

    try {
        await apiFetch('/api/v1/admin/projects/link', {
            method: 'POST',
            body: JSON.stringify({
                project_id: parseInt(projectId),
                session_zid: sessionZid
            })
        });
        showToast('Session linked successfully', 'success');
        document.getElementById('link-session-modal').classList.add('hidden');
        loadProjectTree();
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// 5. Recycle Bin (Trash) Management
// ---------------------------------------------------------------------------
async function loadTrash() {
    const sessionsList = document.getElementById('deleted-sessions-list');
    const projectsList = document.getElementById('deleted-projects-list');
    const badge = document.getElementById('trash-count-badge');

    try {
        const data = await apiFetch('/api/v1/admin/trash');
        state.deletedSessions = data.sessions || [];
        state.deletedProjects = data.projects || [];

        const totalCount = state.deletedSessions.length + state.deletedProjects.length;
        badge.textContent = totalCount;

        // Render deleted sessions
        if (state.deletedSessions.length === 0) {
            sessionsList.innerHTML = '<div class="empty-state">No deleted sessions</div>';
        } else {
            sessionsList.innerHTML = state.deletedSessions.map(s => `
                <div class="trash-item">
                    <div class="trash-item-info">
                        <strong>${escapeHtml(s.zid)}</strong>
                        <span class="text-muted" style="font-size: 11px;">${escapeHtml(s.slug || 'Untitled')} &bull; Deleted: ${escapeHtml(s.deleted_at || '')}</span>
                    </div>
                    <div class="trash-item-actions">
                        <button class="btn btn-secondary btn-sm" onclick="restoreTrash('session', '${s.zid}')">[Restore]</button>
                    </div>
                </div>
            `).join('');
        }

        // Render deleted projects
        if (state.deletedProjects.length === 0) {
            projectsList.innerHTML = '<div class="empty-state">No deleted projects</div>';
        } else {
            projectsList.innerHTML = state.deletedProjects.map(p => `
                <div class="trash-item">
                    <div class="trash-item-info">
                        <strong>${escapeHtml(p.title)}</strong>
                        <span class="text-muted" style="font-size: 11px;">ID: ${p.id} &bull; Deleted: ${escapeHtml(p.deleted_at || '')}</span>
                    </div>
                    <div class="trash-item-actions">
                        <button class="btn btn-secondary btn-sm" onclick="restoreTrash('project', ${p.id})">[Restore]</button>
                    </div>
                </div>
            `).join('');
        }
    } catch (e) {
        sessionsList.innerHTML = '<div class="empty-state">Error loading trash</div>';
        projectsList.innerHTML = '<div class="empty-state">Error loading trash</div>';
    }
}

window.restoreTrash = async function(type, id) {
    try {
        const payload = type === 'session' ? { zid: id } : { project_id: id };
        await apiFetch('/api/v1/admin/trash/restore', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        showToast(`Restored ${type} successfully`, 'success');
        loadTrash();
        loadProjectTree();
    } catch (e) {}
};

document.getElementById('btn-purge-trash').addEventListener('click', async () => {
    if (!confirm('Permanently purge all deleted records? This action cannot be undone!')) return;
    try {
        const res = await apiFetch('/api/v1/admin/trash/purge', { method: 'POST', body: JSON.stringify({}) });
        showToast(`Purged ${res.purged_sessions || 0} sessions and ${res.purged_projects || 0} projects`, 'success');
        loadTrash();
    } catch (e) {}
});

// ---------------------------------------------------------------------------
// 6. Database Maintenance Suite
// ---------------------------------------------------------------------------
function initMaintenance() {
    const btnSnapshot = document.getElementById('btn-snapshot-backup');
    const btnSqlDump = document.getElementById('btn-sql-dump');
    const btnVacuum = document.getElementById('btn-vacuum-db');
    const snapStatus = document.getElementById('snapshot-status');
    const vacStatus = document.getElementById('vacuum-status');

    btnSnapshot.addEventListener('click', async () => {
        snapStatus.textContent = 'Creating snapshot...';
        try {
            const res = await apiFetch('/api/v1/admin/backup/snapshot', { method: 'POST', body: JSON.stringify({}) });
            snapStatus.textContent = `Saved: ${res.filename} (${(res.bytes / 1024 / 1024).toFixed(2)} MB)`;
            showToast(`Snapshot created: ${res.filename}`, 'success');
        } catch (e) {
            snapStatus.textContent = 'Snapshot failed';
        }
    });

    btnSqlDump.addEventListener('click', () => {
        let dumpUrl = '/api/v1/admin/backup/dump.sql';
        if (state.token) {
            dumpUrl += `?token=${encodeURIComponent(state.token)}`;
        }
        window.open(dumpUrl, '_blank');
    });

    btnVacuum.addEventListener('click', async () => {
        vacStatus.textContent = 'Vacuum dispatched...';
        try {
            const res = await apiFetch('/api/v1/admin/db/vacuum', { method: 'POST', body: JSON.stringify({}) });
            vacStatus.textContent = `Status: ${res.status || 'Running in background'}`;
            showToast('Database VACUUM triggered in worker thread', 'success');
        } catch (e) {
            vacStatus.textContent = 'Vacuum failed';
        }
    });
}

// ---------------------------------------------------------------------------
// 7. Telemetry & Stats
// ---------------------------------------------------------------------------
async function loadTelemetry() {
    try {
        const data = await apiFetch('/api/v1/admin/telemetry');
        state.telemetry = data;

        const db = data.database || {};
        document.getElementById('metric-db-size').textContent = `${(db.size_bytes / 1024 / 1024 || 0).toFixed(2)} MB`;
        document.getElementById('metric-wal-size').textContent = `WAL: ${(db.wal_size_bytes / 1024 || 0).toFixed(1)} KB`;

        document.getElementById('metric-active-sessions').textContent = db.active_sessions ?? '--';
        document.getElementById('metric-deleted-sessions').textContent = `Deleted: ${db.deleted_sessions ?? '--'}`;

        document.getElementById('metric-total-projects').textContent = db.total_projects ?? '--';
        document.getElementById('metric-root-projects').textContent = `Root projects: ${db.root_projects ?? '--'}`;

        document.getElementById('metric-total-sentences').textContent = db.total_sentences ?? '--';
        document.getElementById('metric-total-words').textContent = `Words: ${db.total_words ?? '--'}`;

        document.getElementById('raw-telemetry-json').textContent = JSON.stringify(data, null, 2);
    } catch (e) {
        document.getElementById('raw-telemetry-json').textContent = 'Error fetching telemetry';
    }
}
