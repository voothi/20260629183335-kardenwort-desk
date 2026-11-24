/** Local restoration presentation; SQLite and the existing card model own values. */
(function () {
    if (window.WorkspacePresentation) window.WorkspacePresentation.dispose();
    var root = document.documentElement;
    var initialTitle = document.title;
    var icon = document.querySelector('link[rel="icon"]');
    var initialIcon = icon && icon.getAttribute('href');
    var client = null;
    var regionSelector = '#source-container, #translation-container, #lemma-table, .kw-tab-chip';
    var buttonSelector = '#kw-action-toolbar button:not(#kw-btn-hand-tool), #kw-tab-prev, #kw-tab-next';
    var actionEvents = ['click', 'mousedown', 'mouseup', 'dblclick', 'contextmenu'];
    function isReady() { return root.getAttribute('data-workspace-phase') === 'ready'; }
    function unknownIdentity() {
        return root.getAttribute('data-workspace-scope') === 'cards' && root.getAttribute('data-workspace-identity') === 'unknown';
    }
    function readyMetadata() {
        if (window.WorkspaceTabs && window.WorkspaceTabs.getActiveCard()) {
            window.WorkspaceTabs.projectActiveMetadata();
            return;
        }
        document.title = initialTitle;
        if (icon) icon.setAttribute('href', initialIcon);
    }
    function projectRegions() {
        document.querySelectorAll(regionSelector).forEach(function(element) {
            element.inert = unknownIdentity();
            element.setAttribute('aria-busy', String(!isReady()));
        });
        document.querySelectorAll(buttonSelector).forEach(function(element) {
            if (isReady()) element.removeAttribute('aria-disabled');
            else element.setAttribute('aria-disabled', 'true');
        });
    }
    function project(phase, error) {
        root.setAttribute('data-workspace-phase', phase);
        if (phase === 'ready') root.setAttribute('data-workspace-identity', 'known');
        projectRegions();
        var message = document.getElementById('kw-workspace-message');
        if (message) message.textContent = phase === 'failed' ? 'Workspace unavailable: '+error.message : '';
        projectPlaceholders(phase);
        if (phase === 'ready') readyMetadata();
    }
    function projectPlaceholders(phase) {
        document.querySelectorAll('.kw-view-placeholder').forEach(function(element) {
            element.textContent = phase === 'failed' ? 'View unavailable' : 'Restoring...';
        });
    }
    function isSelectionKey(event) {
        return ['Alt', 'Control', 'Meta', 'Shift', 'Tab'].indexOf(event.key) !== -1 ||
            ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c');
    }
    function targetElement(event) {
        var t = event && event.target;
        if (!t) return null;
        if (typeof t.closest === 'function') return t;
        if (t.parentElement && typeof t.parentElement.closest === 'function') return t.parentElement;
        return null;
    }
    function independentTarget(event) {
        var el = targetElement(event);
        return Boolean(el && el.closest('dialog, #kw-workspace-status, #kw-btn-hand-tool'));
    }
    function blockPendingKey(event) {
        if (isReady() || independentTarget(event) || isSelectionKey(event)) return;
        if (!isMutationKey(event)) return;
        event.stopImmediatePropagation();
        if (/^(Arrow|Page|Home$|End$|Escape$)/.test(event.key)) return;
        event.preventDefault();
    }
    function isMutationKey(event) {
        if (['Delete', 'F2', 'F5', ' ', 'Escape', '[', ']', 'ArrowUp', 'ArrowDown'].indexOf(event.key) !== -1) return true;
        if (event.ctrlKey || event.metaKey) return /^(?:[1-9aszy]|PageUp|PageDown)$/i.test(event.key);
        if (event.altKey) return /^[1-9]$/.test(event.key);
        return false;
    }
    function blockPendingAction(event) {
        if (isReady() || independentTarget(event)) return;
        var el = targetElement(event);
        if (!el || !el.closest('[aria-disabled="true"], '+regionSelector)) return;
        if (allowTextSelection(event)) { event.stopImmediatePropagation(); return; }
        event.preventDefault(); event.stopImmediatePropagation();
    }
    function allowTextSelection(event) {
        if (unknownIdentity()) return false;
        var el = targetElement(event);
        return Boolean(el && el.closest('#source-container, #translation-container')) &&
            (window.__persistentSelectableMode || event.altKey);
    }
    function retry() {
        if (client && typeof client.start === 'function') window.__stateReady = client.start();
    }
    function suspend() {
        if (client) client.dispose();
        if (root.getAttribute('data-workspace-scope') === 'cards') {
            root.setAttribute('data-workspace-identity', 'unknown');
            document.title = 'Kardenwort - Restoring view';
            if (icon) icon.setAttribute('href', '/favicon.ico');
        }
        project('disposed');
    }
    function dispose() {
        suspend();
        document.removeEventListener('keydown', blockPendingKey, true);
        actionEvents.forEach(function(name) { document.removeEventListener(name, blockPendingAction, true); });
        window.removeEventListener('pagehide', suspend);
        window.removeEventListener('pageshow', resume);
    }
    function bind(value) {
        client = value;
        var retryBtn = document.getElementById('kw-workspace-retry');
        if (retryBtn) retryBtn.onclick = retry;
        projectRegions();
    }
    function resume(event) {
        if (event && event.persisted) retry();
    }
    document.addEventListener('keydown', blockPendingKey, true);
    actionEvents.forEach(function(name) { document.addEventListener(name, blockPendingAction, true); });
    window.addEventListener('pagehide', suspend);
    window.addEventListener('pageshow', resume);
    root.setAttribute('data-workspace-identity', 'known');
    if (root.getAttribute('data-workspace-scope') === 'cards' &&
        window.SqliteState && typeof window.SqliteState.restoredClientId === 'function' &&
        window.SqliteState.restoredClientId()) {
        root.setAttribute('data-workspace-identity', 'unknown');
        document.title = 'Kardenwort - Restoring view';
        if (icon) icon.setAttribute('href', '/favicon.ico');
    }
    project('initializing');
    window.WorkspacePresentation = {project: project, bind: bind, dispose: dispose, isReady: isReady, unknownIdentity: unknownIdentity};
})();
