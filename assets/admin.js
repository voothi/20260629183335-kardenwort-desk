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
    telemetry: null,
    sessionsExplorer: {
        sessions: [],
        totalCount: 0,
        page: 1,
        pageSize: 50,
        query: '',
        language: '',
        assigned: '',
        loading: false
    }
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
    initSessionsExplorer();
    initMaintenance();
    
    // Initial Load
    checkServerHealth();
    loadProjectTree();
    loadSessionsExplorer();
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
        showToast('Token saved', 'success');
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
    document.getElementById('btn-refresh-sessions').addEventListener('click', () => loadSessionsExplorer(true));
    document.getElementById('btn-refresh-trash').addEventListener('click', loadTrash);
    document.getElementById('btn-refresh-telemetry').addEventListener('click', loadTelemetry);
}

function refreshCurrentTab() {
    if (state.activeTab === 'tab-tree') loadProjectTree();
    else if (state.activeTab === 'tab-sessions') loadSessionsExplorer();
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
        container.innerHTML = `<div class="empty-state">No projects found.</div>`;
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
            <button class="btn btn-secondary btn-sm btn-add-child" data-id="${node.id}" title="Add Sub-Chapter">+ Sub</button>
            <button class="btn btn-secondary btn-sm btn-link-session" data-id="${node.id}" title="Link Session">+ Link</button>
            <button class="btn btn-secondary btn-sm btn-edit-node" data-id="${node.id}" title="Edit Project">Edit</button>
            <button class="btn btn-danger btn-sm btn-delete-node" data-id="${node.id}" title="Delete Project">Delete</button>
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
            <span class="text-muted" style="font-size: 12px;">(${escapeHtml(session.source_language || '')})</span>
        </div>
        <div class="session-actions">
            <button class="btn btn-icon btn-move-up" ${index === 0 ? 'disabled' : ''} title="Move Up">▲</button>
            <button class="btn btn-icon btn-move-down" ${index === total - 1 ? 'disabled' : ''} title="Move Down">▼</button>
            <button class="btn btn-icon btn-unlink" title="Unlink">Unlink</button>
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
    } catch (e) {}
}

async function unlinkSession(projectId, sessionZid) {
    const ok = await showConfirmDialog(`Unlink session ${sessionZid} from this project?`, {
        title: 'Unlink Session',
        okLabel: 'Unlink'
    });
    if (!ok) return;
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
    const ok = await showConfirmDialog(`Move project "${title}" to Recycle Bin?`, {
        title: 'Delete Project',
        okLabel: 'Delete'
    });
    if (!ok) return;
    try {
        await apiFetch('/api/v1/admin/projects/delete', {
            method: 'POST',
            body: JSON.stringify({ project_id: projectId })
        });
        showToast(`Project moved to Recycle Bin`, 'success');
        loadProjectTree();
        loadTrash();
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// 4. Modals (Project Create/Edit, Session Link & Confirmation)
// ---------------------------------------------------------------------------
let confirmModalResolver = null;

function showConfirmDialog(message, options = {}) {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('confirm-modal-title');
    const msgEl = document.getElementById('confirm-modal-message');
    const okBtn = document.getElementById('btn-confirm-ok');
    const cancelBtn = document.getElementById('btn-confirm-cancel');

    if (confirmModalResolver) {
        confirmModalResolver(false);
        confirmModalResolver = null;
    }

    titleEl.textContent = options.title || 'Confirm Action';
    msgEl.textContent = message;
    okBtn.textContent = options.okLabel || 'Confirm';
    cancelBtn.textContent = options.cancelLabel || 'Cancel';

    if (options.isDanger === false) {
        okBtn.className = 'btn btn-primary';
    } else {
        okBtn.className = 'btn btn-danger';
    }

    modal.classList.remove('hidden');
    okBtn.focus();

    return new Promise((resolve) => {
        confirmModalResolver = (val) => {
            modal.classList.add('hidden');
            confirmModalResolver = null;
            resolve(val);
        };
    });
}

function initModals() {
    const projectModal = document.getElementById('project-modal');
    const linkModal = document.getElementById('link-session-modal');
    const assignModal = document.getElementById('assign-project-modal');
    const confirmModal = document.getElementById('confirm-modal');

    document.getElementById('btn-new-root-project').addEventListener('click', () => openProjectModal());
    document.getElementById('btn-modal-close').addEventListener('click', () => projectModal.classList.add('hidden'));
    document.getElementById('btn-modal-cancel').addEventListener('click', () => projectModal.classList.add('hidden'));
    document.getElementById('btn-modal-save').addEventListener('click', saveProjectModal);

    document.getElementById('btn-link-modal-close').addEventListener('click', () => linkModal.classList.add('hidden'));
    document.getElementById('btn-link-modal-cancel').addEventListener('click', () => linkModal.classList.add('hidden'));
    document.getElementById('btn-link-modal-save').addEventListener('click', saveLinkSessionModal);

    document.getElementById('btn-assign-modal-close').addEventListener('click', () => assignModal.classList.add('hidden'));
    document.getElementById('btn-assign-modal-cancel').addEventListener('click', () => assignModal.classList.add('hidden'));
    document.getElementById('btn-assign-modal-save').addEventListener('click', saveAssignProjectModal);

    // TSV Inspector Modal Events
    const tsvModal = document.getElementById('tsv-inspector-modal');
    if (tsvModal) {
        document.getElementById('btn-tsv-modal-close').addEventListener('click', () => tsvModal.classList.add('hidden'));
        document.getElementById('btn-tsv-modal-dismiss').addEventListener('click', () => tsvModal.classList.add('hidden'));
        document.getElementById('btn-tsv-copy').addEventListener('click', async () => {
            const viewer = document.getElementById('tsv-raw-viewer');
            if (!viewer || !viewer.value) return;
            try {
                await navigator.clipboard.writeText(viewer.value);
                showToast('TSV content copied to clipboard', 'success');
            } catch (err) {
                viewer.select();
                document.execCommand('copy');
                showToast('TSV content copied to clipboard', 'success');
            }
        });
    }

    // Confirmation Modal Events
    const closeConfirm = (val) => {
        if (confirmModalResolver) {
            confirmModalResolver(val);
        } else {
            confirmModal.classList.add('hidden');
        }
    };

    document.getElementById('btn-confirm-modal-close').addEventListener('click', () => closeConfirm(false));
    document.getElementById('btn-confirm-cancel').addEventListener('click', () => closeConfirm(false));
    document.getElementById('btn-confirm-ok').addEventListener('click', () => closeConfirm(true));

    document.addEventListener('keydown', (e) => {
        if (confirmModal && !confirmModal.classList.contains('hidden')) {
            if (e.key === 'Escape') {
                e.preventDefault();
                closeConfirm(false);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                closeConfirm(true);
            }
        }
    });
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
        showToast('Title is required', 'error');
        return;
    }

    try {
        if (id) {
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
        const res = await apiFetch('/api/v1/admin/sessions?limit=200');
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
        showToast('Select a session', 'error');
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
        showToast('Session linked', 'success');
        document.getElementById('link-session-modal').classList.add('hidden');
        loadProjectTree();
        loadSessionsExplorer();
    } catch (e) {}
}

function flattenProjectTree(nodes, prefix = '') {
    let result = [];
    if (!nodes) return result;
    for (const node of nodes) {
        const label = prefix ? `${prefix} > ${node.title}` : node.title;
        result.push({ id: node.id, label: label, title: node.title });
        if (node.children && node.children.length > 0) {
            result = result.concat(flattenProjectTree(node.children, label));
        }
    }
    return result;
}

async function openAssignProjectModal(session) {
    const modal = document.getElementById('assign-project-modal');
    document.getElementById('assign-modal-session-zid').value = session.zid;
    
    const infoBox = document.getElementById('assign-modal-session-info');
    infoBox.innerHTML = `<strong>${escapeHtml(session.zid)}</strong> &bull; <span>${escapeHtml(session.slug || 'untitled')}</span> <span class="text-muted">(${escapeHtml(session.source_language || '')})</span>`;

    const select = document.getElementById('assign-project-select');
    select.innerHTML = '<option value="">Loading projects...</option>';
    modal.classList.remove('hidden');

    try {
        if (!state.projectTree || state.projectTree.length === 0) {
            const res = await apiFetch('/api/v1/admin/projects');
            state.projectTree = res.projects || [];
        }
        const flatProjects = flattenProjectTree(state.projectTree);
        if (flatProjects.length === 0) {
            select.innerHTML = '<option value="">No projects available - create a project first</option>';
            return;
        }
        select.innerHTML = flatProjects.map(p => 
            `<option value="${p.id}">${escapeHtml(p.label)}</option>`
        ).join('');
    } catch (e) {
        select.innerHTML = '<option value="">Failed to load projects</option>';
    }
}

async function saveAssignProjectModal() {
    const sessionZid = document.getElementById('assign-modal-session-zid').value;
    const projectId = document.getElementById('assign-project-select').value;

    if (!projectId) {
        showToast('Please select a target project', 'error');
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
        showToast('Session assigned to project', 'success');
        document.getElementById('assign-project-modal').classList.add('hidden');
        loadSessionsExplorer();
        loadProjectTree();
    } catch (e) {}
}

// ---------------------------------------------------------------------------
// 5. Sessions Library Explorer
// ---------------------------------------------------------------------------
function initSessionsExplorer() {
    const searchInput = document.getElementById('sessions-search-input');
    const langFilter = document.getElementById('sessions-lang-filter');
    const assignFilter = document.getElementById('sessions-assign-filter');
    const prevBtn = document.getElementById('btn-sessions-prev');
    const nextBtn = document.getElementById('btn-sessions-next');

    let debounceTimer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            state.sessionsExplorer.query = searchInput.value.trim();
            state.sessionsExplorer.page = 1;
            loadSessionsExplorer();
        }, 300);
    });

    langFilter.addEventListener('change', () => {
        state.sessionsExplorer.language = langFilter.value;
        state.sessionsExplorer.page = 1;
        loadSessionsExplorer();
    });

    assignFilter.addEventListener('change', () => {
        state.sessionsExplorer.assigned = assignFilter.value;
        state.sessionsExplorer.page = 1;
        loadSessionsExplorer();
    });

    prevBtn.addEventListener('click', () => {
        if (state.sessionsExplorer.page > 1) {
            state.sessionsExplorer.page--;
            loadSessionsExplorer();
        }
    });

        nextBtn.addEventListener('click', () => {
            const totalPages = Math.ceil(state.sessionsExplorer.totalCount / state.sessionsExplorer.pageSize) || 1;
            if (state.sessionsExplorer.page < totalPages) {
                state.sessionsExplorer.page++;
                loadSessionsExplorer();
            }
        });

    // Drag and Drop TSV Ingestion
    const dropZone = document.getElementById('sessions-drop-zone');
    const fileInput = document.getElementById('tsv-file-input');
    const browseBtn = document.getElementById('btn-browse-tsv');

    if (dropZone && fileInput) {
        if (browseBtn) {
            browseBtn.addEventListener('click', () => fileInput.click());
        }

        fileInput.addEventListener('change', () => {
            if (fileInput.files && fileInput.files.length > 0) {
                handleTsvFileUpload(Array.from(fileInput.files));
                fileInput.value = '';
            }
        });

        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });

        ['dragleave', 'dragend'].forEach(evt => {
            dropZone.addEventListener(evt, () => dropZone.classList.remove('dragover'));
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                handleTsvFileUpload(Array.from(e.dataTransfer.files));
            }
        });
    }
}

async function handleTsvFileUpload(files) {
    const statusDiv = document.getElementById('drop-zone-status');
    if (!files || files.length === 0) return;

    statusDiv.classList.remove('hidden', 'error');
    statusDiv.textContent = `Uploading ${files.length} file(s)...`;

    let successCount = 0;
    let errorCount = 0;

    for (const file of files) {
        try {
            const text = await file.text();
            const res = await apiFetch('/api/v1/sessions/import-tsv', {
                method: 'POST',
                body: JSON.stringify({
                    tsv_content: text,
                    filename: file.name
                })
            });
            successCount++;
            showToast(`Ingested session ${res.session_zid || file.name} (${res.sentences_count} sent, ${res.words_count} words)`, 'success');
        } catch (err) {
            errorCount++;
            showToast(`Failed to import ${file.name}: ${err.message}`, 'error');
        }
    }

    if (errorCount === 0) {
        statusDiv.textContent = `Successfully imported ${successCount} session(s).`;
        setTimeout(() => statusDiv.classList.add('hidden'), 5000);
    } else {
        statusDiv.classList.add('error');
        statusDiv.textContent = `Import finished: ${successCount} succeeded, ${errorCount} failed.`;
    }

    loadSessionsExplorer(true);
    loadTelemetry();
}

async function openTsvInspectorModal(session) {
    const modal = document.getElementById('tsv-inspector-modal');
    const titleEl = document.getElementById('tsv-modal-title');
    const statsEl = document.getElementById('tsv-modal-stats');
    const viewer = document.getElementById('tsv-raw-viewer');
    const downloadBtn = document.getElementById('btn-tsv-download');

    titleEl.textContent = `Session TSV: ${session.zid} - ${session.slug || 'untitled'}`;
    statsEl.textContent = 'Streaming dynamic TSV from SQLite...';
    viewer.value = 'Loading TSV...';
    modal.classList.remove('hidden');

    let downloadUrl = `/api/v1/sessions/${encodeURIComponent(session.zid)}/tsv`;
    if (state.token) {
        downloadUrl += `?token=${encodeURIComponent(state.token)}`;
    }
    downloadBtn.setAttribute('href', downloadUrl);
    downloadBtn.setAttribute('download', `${session.zid}-session.tsv`);

    try {
        const headers = {};
        if (state.token) {
            headers['X-API-Token'] = state.token;
        }
        const resp = await fetch(downloadUrl, { headers });
        if (!resp.ok) {
            throw new Error(`Failed to fetch TSV: HTTP ${resp.status}`);
        }
        const text = await resp.text();
        viewer.value = text;

        const lines = text.split(/\r?\n/).filter(l => l.trim().length > 0);
        const dataRowsCount = lines.filter(l => !l.startsWith('#')).length - 1;
        const sizeKb = (new Blob([text]).size / 1024).toFixed(1);
        statsEl.textContent = `${Math.max(0, dataRowsCount)} rows | ${sizeKb} KB | UTF-8`;
    } catch (err) {
        viewer.value = `Error loading TSV: ${err.message}`;
        statsEl.textContent = 'Error loading TSV';
        showToast(err.message, 'error');
    }
}

async function loadSessionsExplorer(force = false) {
    const tbody = document.getElementById('sessions-table-body');
    const prevBtn = document.getElementById('btn-sessions-prev');
    const nextBtn = document.getElementById('btn-sessions-next');
    const pageInfo = document.getElementById('sessions-page-info');
    const totalInfo = document.getElementById('sessions-total-info');

    const { page, pageSize, query, language, assigned } = state.sessionsExplorer;
    const offset = (page - 1) * pageSize;

    const params = new URLSearchParams();
    params.set('limit', pageSize);
    params.set('offset', offset);
    if (query) params.set('query', query);
    if (language) params.set('language', language);
    if (assigned) params.set('assigned', assigned);

    try {
        tbody.innerHTML = '<tr><td colspan="7" class="loading-spinner">Loading sessions library...</td></tr>';
        const res = await apiFetch(`/api/v1/admin/sessions?${params.toString()}`);
        state.sessionsExplorer.sessions = res.sessions || [];
        state.sessionsExplorer.totalCount = res.total_count || 0;

        renderSessionsExplorerTable(state.sessionsExplorer.sessions);

        const totalPages = Math.ceil(state.sessionsExplorer.totalCount / pageSize) || 1;
        pageInfo.textContent = `Page ${page} of ${totalPages}`;
        totalInfo.textContent = `Total: ${state.sessionsExplorer.totalCount} sessions`;
        prevBtn.disabled = page <= 1;
        nextBtn.disabled = page >= totalPages;
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">Failed to load sessions.</td></tr>';
    }
}

function renderSessionsExplorerTable(sessions) {
    const tbody = document.getElementById('sessions-table-body');
    if (!sessions || sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No matching sessions found.</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    sessions.forEach(s => {
        const tr = document.createElement('tr');

        // Projects badge
        let projectsHtml = '<span class="text-dim">—</span>';
        if (s.projects && s.projects.length > 0) {
            projectsHtml = s.projects.map(p => 
                `<span class="project-tag" title="Project ID: ${p.id}">${escapeHtml(p.title)}</span>`
            ).join(' ');
        }

        // Preview snippet
        const rawPreview = s.source_raw_text ? (s.source_raw_text.length > 70 ? s.source_raw_text.substring(0, 70) + '...' : s.source_raw_text) : '';
        const slugDisplay = s.slug || 'untitled';

        tr.innerHTML = `
            <td><code class="session-badge">${escapeHtml(s.zid)}</code></td>
            <td>
                <div class="session-cell-slug"><strong>${escapeHtml(slugDisplay)}</strong></div>
                ${rawPreview ? `<div class="session-cell-preview text-muted">${escapeHtml(rawPreview)}</div>` : ''}
            </td>
            <td><span class="lang-tag">${escapeHtml(s.source_language || '—')}</span></td>
            <td>${s.sentence_count ?? 0}</td>
            <td>${s.word_count ?? s.token_count ?? 0}</td>
            <td>${projectsHtml}</td>
            <td>
                <div class="table-actions">
                    <button class="btn btn-secondary btn-sm btn-view-tsv" title="View & Download TSV">TSV</button>
                    <button class="btn btn-secondary btn-sm btn-assign-proj" title="Assign to Project">+ Book</button>
                    <button class="btn btn-secondary btn-sm btn-open-reader" title="Open in Reader">Open</button>
                    <button class="btn btn-danger btn-sm btn-delete-sess" title="Delete Session">Del</button>
                </div>
            </td>
        `;

        tr.querySelector('.btn-view-tsv').addEventListener('click', () => openTsvInspectorModal(s));
        tr.querySelector('.btn-assign-proj').addEventListener('click', () => openAssignProjectModal(s));
        tr.querySelector('.btn-open-reader').addEventListener('click', () => openSessionInReader(s.zid));
        tr.querySelector('.btn-delete-sess').addEventListener('click', () => deleteSessionFromLibrary(s.zid));

        tbody.appendChild(tr);
    });
}

function openSessionInReader(zid) {
    let url = `/?session_zid=${encodeURIComponent(zid)}&theme=dark`;
    if (state.token) {
        url += `&token=${encodeURIComponent(state.token)}`;
    }
    window.open(url, '_blank');
}

async function deleteSessionFromLibrary(zid) {
    const ok = await showConfirmDialog(`Move session ${zid} to Recycle Bin?`, {
        title: 'Delete Session',
        okLabel: 'Delete'
    });
    if (!ok) return;
    try {
        await apiFetch('/api/v1/admin/sessions/delete', {
            method: 'POST',
            body: JSON.stringify({ session_zid: zid })
        });
        showToast('Session moved to Recycle Bin', 'success');
        loadSessionsExplorer();
        loadTrash();
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

        if (badge) {
            const totalCount = state.deletedSessions.length + state.deletedProjects.length;
            badge.textContent = totalCount;
        }

        // Render deleted sessions
        if (state.deletedSessions.length === 0) {
            sessionsList.innerHTML = '<div class="empty-state">No deleted sessions</div>';
        } else {
            sessionsList.innerHTML = state.deletedSessions.map(s => `
                <div class="trash-item">
                    <div class="trash-item-info">
                        <strong>${escapeHtml(s.zid)}</strong>
                        <span class="text-muted" style="font-size: 12px;">${escapeHtml(s.slug || 'Untitled')} &bull; Deleted: ${escapeHtml(s.deleted_at || '')}</span>
                    </div>
                    <div class="trash-item-actions">
                        <button class="btn btn-secondary btn-sm" onclick="restoreTrash('session', '${s.zid}')">Restore</button>
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
                        <span class="text-muted" style="font-size: 12px;">ID: ${p.id} &bull; Deleted: ${escapeHtml(p.deleted_at || '')}</span>
                    </div>
                    <div class="trash-item-actions">
                        <button class="btn btn-secondary btn-sm" onclick="restoreTrash('project', ${p.id})">Restore</button>
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
        showToast(`Restored ${type}`, 'success');
        loadTrash();
        loadProjectTree();
    } catch (e) {}
};

document.getElementById('btn-purge-trash').addEventListener('click', async () => {
    const ok = await showConfirmDialog('Permanently purge all deleted records from Recycle Bin?', {
        title: 'Purge Recycle Bin',
        okLabel: 'Purge All'
    });
    if (!ok) return;
    try {
        const res = await apiFetch('/api/v1/admin/trash/purge', { method: 'POST', body: JSON.stringify({}) });
        showToast(`Purged ${res.purged_sessions || 0} sessions, ${res.purged_projects || 0} projects`, 'success');
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
            snapStatus.textContent = `${res.filename} (${(res.bytes / 1024 / 1024).toFixed(2)} MB)`;
            showToast(`Snapshot created`, 'success');
        } catch (e) {
            snapStatus.textContent = 'Failed';
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
        vacStatus.textContent = 'Dispatched...';
        try {
            const res = await apiFetch('/api/v1/admin/db/vacuum', { method: 'POST', body: JSON.stringify({}) });
            vacStatus.textContent = `${res.status || 'Running'}`;
            showToast('Vacuum triggered', 'success');
        } catch (e) {
            vacStatus.textContent = 'Failed';
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
