/** First-paint gate only; SQLite and the existing card model own all values. */
(function () {
    if (window.WorkspacePresentation) window.WorkspacePresentation.dispose();
    var root = document.documentElement;
    var initialTitle = document.title;
    var icon = document.querySelector('link[rel="icon"]');
    var initialIcon = icon && icon.getAttribute('href');
    var client = null;
    function readyMetadata() {
        if (window.WorkspaceTabs && window.WorkspaceTabs.getActiveCard()) {
            window.WorkspaceTabs.projectActiveMetadata();
            return;
        }
        document.title = initialTitle;
        if (icon) icon.setAttribute('href', initialIcon);
    }
    function project(phase, error) {
        root.setAttribute('data-workspace-phase', phase);
        var workspace = document.querySelector('.kw-lookup-container');
        if (workspace) workspace.inert = phase !== 'ready';
        var message = document.getElementById('kw-workspace-message');
        if (message) message.textContent = phase === 'failed' ? 'Workspace unavailable: '+error.message : 'Loading workspace...';
        if (phase === 'ready') readyMetadata();
    }
    function blockPendingKey(event) {
        if (root.getAttribute('data-workspace-phase') === 'ready') return;
        if (event.target.closest && event.target.closest('dialog, #kw-workspace-status')) return;
        event.preventDefault(); event.stopImmediatePropagation();
    }
    function retry() { window.__stateReady = client.start(); }
    function suspend() {
        if (client) client.dispose();
        project('disposed');
        document.removeEventListener('keydown', blockPendingKey, true);
    }
    function dispose() {
        suspend();
        window.removeEventListener('pagehide', suspend);
        window.removeEventListener('pageshow', resume);
    }
    function bind(value) {
        client = value;
        document.getElementById('kw-workspace-retry').onclick = retry;
    }
    document.addEventListener('keydown', blockPendingKey, true);
    function resume(event) {
        if (event.persisted) {
            document.addEventListener('keydown', blockPendingKey, true);
            retry();
        }
    }
    window.addEventListener('pagehide', suspend);
    window.addEventListener('pageshow', resume);
    document.title = 'Kardenwort - Loading workspace';
    if (icon) icon.setAttribute('href', '/favicon.ico');
    project('initializing');
    window.WorkspacePresentation = {project: project, bind: bind, dispose: dispose};
})();
