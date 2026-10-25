// @ts-check

/**
 * @file sse.js
 * Client-side Server-Sent Events (SSE) streaming bridge.
 * Connects EventSource to /api/v1/events?session=<zid>, routes deltas to store,
 * and maintains connection status indicators in #session-header.
 */

/**
 * @typedef {import('../types.js').UpdatePayload} UpdatePayload
 * @typedef {{type: 'ping'|'heartbeat'|'connected'|'disconnected'}} ConnectionEvent
 * @typedef {{type: 'provider_failover', task_type?: string, task?: string, new_provider?: string, to?: string, provider?: string}} ProviderFailoverEvent
 * @typedef {{type: 'tag_updated', row_id: string|number, status: boolean}} TagEvent
 * @typedef {(UpdatePayload & {type?: undefined})|ConnectionEvent|ProviderFailoverEvent|TagEvent} StreamEvent
 */

(function () {
    /** @type {EventSource|null} */
    var activeEventSource = null;
    /** @type {string|null} */
    var activeSessionZid = null;

    /**
     * Updates the SSE connection status badge in #session-header
     * @param {'connected'|'reconnecting'|'disconnected'|'error'} status
     */
    function updateConnectionIndicator(status) {
        var w = /** @type {any} */ (window);
        var header = document.getElementById('session-header');
        var indicator = document.getElementById('sse-status-indicator');

        if (!indicator) {
            indicator = document.createElement('span');
            indicator.id = 'sse-status-indicator';
            indicator.className = 'kw-sse-status';
            if (!header) indicator.style.display = 'none';
            (header || document.body).appendChild(indicator);
        }

        if (indicator) {
            indicator.className = 'kw-sse-status kw-status-' + status;
            indicator.setAttribute('data-status', status);
            if (status === 'connected') {
                indicator.textContent = 'SSE: Connected';
                indicator.title = 'Real-time in-memory streaming active';
            } else if (status === 'reconnecting') {
                indicator.textContent = 'SSE: Reconnecting...';
                indicator.title = 'Connection lost, attempting reconnection';
            } else {
                indicator.textContent = 'SSE: Offline';
                indicator.title = 'Disconnected from streaming server';
            }
        }

        // Backward-compatible helper in app.js
        if (typeof w.setSseStatus === 'function') {
            w.setSseStatus(status);
        }
    }

    /**
     * Connect to SSE streaming endpoint for session
     * @param {string} sessionZid
     * @param {string} [serverUrl]
     */
    function connect(sessionZid, serverUrl) {
        if (!sessionZid || typeof window.EventSource === 'undefined') return;

        var w = /** @type {any} */ (window);
        // Guard against file:/// filesystem URI errors
        if (window.location.protocol === 'file:' && !serverUrl) return;

        // Repeated mounts preserve the existing session subscription.
        if (activeEventSource && activeSessionZid === sessionZid) return;
        if (activeEventSource) {
            try { activeEventSource.close(); } catch (e) { console.warn('[SSE] Error closing active EventSource:', e); }
            activeEventSource = null;
            activeSessionZid = null;
            w.__sseActiveZid = null;
        }

        try {
            var base = (serverUrl || window.location.origin || '').replace(/\/+$/, '');
            var sseUrl = (base ? base : '') + '/api/v1/events?session=' + encodeURIComponent(sessionZid);
            var es = new EventSource(sseUrl);

            activeEventSource = es;
            activeSessionZid = sessionZid;
            w.__sseActiveZid = sessionZid;

            updateConnectionIndicator('reconnecting');

            es.onopen = function () {
                if (activeEventSource !== es) return;
                updateConnectionIndicator('connected');
            };

            es.onerror = function () {
                if (activeEventSource !== es) return;
                if (es.readyState === EventSource.CONNECTING) {
                    updateConnectionIndicator('reconnecting');
                } else {
                    updateConnectionIndicator('disconnected');
                }
            };

            es.onmessage = function (e) {
                if (activeEventSource !== es) return;
                var data;
                try {
                    data = JSON.parse(e.data);
                } catch (err) {
                    console.error('[SSE] Invalid JSON event', err);
                    updateConnectionIndicator('error');
                    return;
                }
                try {
                    applyEvent(data);
                } catch (err) {
                    console.error('[SSE] Event application failed', err);
                    updateConnectionIndicator('error');
                    throw err;
                }
            };
            /** @param {StreamEvent|null} data */
            function applyEvent(data) {
                if (data && (data.type === 'ping' || data.type === 'heartbeat')) return;
                if (data && data.type === 'disconnected') {
                    disconnect();
                    return;
                }
                    if (!data) return;

                    if (data.type === 'connected') {
                        updateConnectionIndicator('connected');
                        return;
                    }

                    if (data.type === 'provider_failover') {
                        if (typeof w.updateActiveProvider === 'function') {
                            var tType = data.task_type || data.task;
                            var nProv = data.new_provider || data.to || data.provider;
                            if (nProv) w.updateActiveProvider(tType, nProv);
                        }
                        return;
                    }

                    if (data.type === 'tag_updated') {
                        var cb = /** @type {HTMLInputElement|null} */ (document.querySelector('.kw-tag-checkbox[data-token-order="' + data.row_id + '"]'));
                        if (cb && cb.checked !== data.status) {
                            cb.checked = data.status;
                            var r = cb.closest ? cb.closest('tr') : cb.parentElement ? cb.parentElement.parentElement : null;
                            if (r) {
                                if (data.status) r.classList.add('kw-row-selected');
                                else r.classList.remove('kw-row-selected');
                            }
                        }
                        return;
                    }

                    // The canonical update command applies each event once.
                    if (typeof w.receiveUpdate === 'function') {
                        w.receiveUpdate(data);
                    } else if (w.AppStore) {
                        w.AppStore.applyDeltas(data);
                    }
            }

        } catch (e) {
            updateConnectionIndicator('error');
            console.error('[SSE] Connection initialization failed', e);
            throw e;
        }
    }

    /**
     * Disconnect active SSE connection
     */
    function disconnect() {
        if (activeEventSource) {
            try { activeEventSource.close(); } catch (e) { console.warn('[SSEBridge] Error closing active EventSource:', e); }
            activeEventSource = null;
            activeSessionZid = null;
            var w = /** @type {any} */ (window);
            w.__sseActiveZid = null;
            updateConnectionIndicator('disconnected');
        }
    }

    var SSEBridge = {
        connect: connect,
        disconnect: disconnect,
        updateConnectionIndicator: updateConnectionIndicator,
        getActiveZid: function () { return activeSessionZid; }
    };

    var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.SSEBridge = SSEBridge;
        w.initSSE = connect;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SSEBridge;
    }
})();
