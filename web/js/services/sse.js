
/**
 * @file sse.js
 * Client-side Server-Sent Events (SSE) streaming bridge.
 * Connects EventSource to /api/v1/events?session=<zid>, routes deltas to store,
 * and maintains connection status indicators in #session-header.
 */

/**
 * @typedef {{type: 'ping'|'heartbeat'|'connected'|'disconnected'}} ConnectionEvent
 * @typedef {{type: 'provider_failover', task_type?: string, task?: string, new_provider?: string, to?: string, provider?: string}} ProviderFailoverEvent
 * @typedef {{type: 'tag_updated', row_id: string|number, status: boolean}} TagEvent
 * @typedef {(UpdatePayload & {type?: undefined})|ConnectionEvent|ProviderFailoverEvent|TagEvent} StreamEvent
 */

(function () {

    var activeEventSource = null;

    var activeSessionZid = null;

    var activeServerUrl = null;

    var connectionCounter = 0;

    var activeConnectionId = 0;

    function authenticatedSource(url, token) {
        var source = {readyState: 0, onopen: null, onerror: null, onmessage: null};
        var controller = null, timer = null, closed = false, delay = 1000;
        var listeners = {};
        source.addEventListener = function(type, listener) {
            (listeners[type] = listeners[type] || []).push(listener);
        };
        source.close = function() {
            closed = true;
            source.readyState = 2;
            clearTimeout(timer);
            if (controller) controller.abort();
        };
        function failed(error) {
            if (closed) return;
            source.readyState = error.terminal ? 2 : 0;
            if (error.terminal && controller) controller.abort();
            if (source.onerror) source.onerror(error);
            if (!error.terminal) {
                timer = setTimeout(start, delay);
                delay = Math.min(delay * 2, 16000);
            }
        }
        function consume(reader) {
            var decoder = new TextDecoder('utf-8'), buffer = '', data = [], eventType = 'message';
            function dispatch() {
                if (!data.length || closed) return;
                var event = {data: data.join('\n')};
                if (eventType === 'message' && source.onmessage) source.onmessage(event);
                (listeners[eventType] || []).forEach(function(listener) { listener(event); });
            }
            function line(value) {
                value = value.replace(/\r$/, '');
                if (!value) {
                    dispatch();
                    data = [];
                    eventType = 'message';
                } else if (value.indexOf('data:') === 0) {
                    data.push(value.slice(5).replace(/^ /, ''));
                } else if (value.indexOf('event:') === 0) {
                    eventType = value.slice(6).replace(/^ /, '');
                }
            }
            function read() {
                return reader.read().then(function(chunk) {
                    if (closed) return;
                    if (chunk.done) throw new Error('Event stream closed');
                    buffer += decoder.decode(chunk.value, {stream: true});
                    var lines = buffer.split('\n');
                    buffer = lines.pop();
                    lines.forEach(line);
                    return read();
                });
            }
            return read();
        }
        function start() {
            if (closed) return;
            if (typeof AbortController === 'undefined' || typeof TextDecoder === 'undefined' || typeof fetch !== 'function') {
                var unavailable = new Error('Authenticated event streaming is unavailable in this host');
                unavailable.terminal = true;
                failed(unavailable);
                return;
            }
            controller = new AbortController();
            var options = {headers: {'Accept': 'text/event-stream', 'X-API-Token': token}, cache: 'no-store'};
            options.signal = controller.signal;
            Promise.resolve().then(function() { return fetch(url, options); }).then(function(response) {
                if (closed) return;
                if (!response.ok || !response.body || !response.body.getReader || typeof TextDecoder === 'undefined') {
                    var error = new Error('Authenticated event stream unavailable' + (response.status ? ' (' + response.status + ')' : ''));
                    error.terminal = true;
                    throw error;
                }
                source.readyState = 1;
                delay = 1000;
                if (source.onopen) source.onopen({});
                return consume(response.body.getReader());
            }).catch(failed);
        }
        timer = setTimeout(start, 0);
        return source;
    }

    function createSource(url, token) {
        token = token || (window.getApiToken ? window.getApiToken() : window.API_TOKEN) || '';
        return token ? authenticatedSource(url, token) : new EventSource(url);
    }

    function invalidateMembership() {
        if (window.invalidateStreamMembership) window.invalidateStreamMembership();
    }

    /**
     * Updates the SSE connection status badge in #session-header
     * @param {'connected'|'reconnecting'|'disconnected'|'error'} status
     */
    function updateConnectionIndicator(status) {
        var w = (window);
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
                indicator.removeAttribute('data-sync');
                indicator.removeAttribute('data-sync-message');
            } else {
                indicator.textContent = 'SSE: Offline';
                indicator.title = 'Disconnected from streaming server';
                indicator.removeAttribute('data-sync');
                indicator.removeAttribute('data-sync-message');
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
    function connect(sessionZid, serverUrl, token) {
        if (!sessionZid || typeof window.EventSource === 'undefined') return;

        var w = (window);
        var bootstrapSession = (w.__BOOTSTRAP__ && (w.__BOOTSTRAP__.session_data || w.__BOOTSTRAP__)) || null;
        if (bootstrapSession && bootstrapSession.server_enabled === false) return;
        if (!serverUrl && bootstrapSession && bootstrapSession.server_url) {
            serverUrl = bootstrapSession.server_url;
        }

        // Guard against file:/// filesystem URI errors
        if (window.location.protocol === 'file:' && !serverUrl) return;

        // Repeated mounts preserve the existing session subscription.
        if (activeEventSource && activeSessionZid === sessionZid) return;
        if (activeEventSource) {
            invalidateMembership();
            try { activeEventSource.close(); } catch (e) { console.warn('[SSE] Error closing active EventSource:', e); }
            activeEventSource = null;
            activeSessionZid = null;
            activeServerUrl = null;
            activeConnectionId = 0;
            w.__sseActiveZid = null;
        }

        try {
            var base = (serverUrl || '').replace(/\/+$/, '');
            if (!base && (!bootstrapSession || bootstrapSession.server_enabled !== false)) {
                if (window.location && (window.location.protocol === 'http:' || window.location.protocol === 'https:')) {
                    base = (window.location.origin || '').replace(/\/+$/, '');
                }
            }
            var sseUrl = (base ? base : '') + '/api/v1/events?session=' + encodeURIComponent(sessionZid);
            var es = createSource(sseUrl, token);

            activeConnectionId = ++connectionCounter;
            activeEventSource = es;
            activeSessionZid = sessionZid;
            activeServerUrl = serverUrl || null;
            w.__sseActiveZid = sessionZid;

            var isConnectionOpen = false;

            updateConnectionIndicator('reconnecting');

            function notifyConnectionReady() {
                if (activeEventSource !== es) return;
                updateConnectionIndicator('connected');
                if (!isConnectionOpen) {
                    isConnectionOpen = true;
                    if (typeof w.reconcileStreamMembership === 'function') {
                        w.reconcileStreamMembership({
                            session: sessionZid,
                            connectionId: activeConnectionId,
                            serverUrl: serverUrl
                        }).catch(function(error) {
                            console.debug('[SSE] Membership recovery stopped:', error.message);
                        });
                    }
                }
            }

            es.onopen = function () {
                notifyConnectionReady();
            };

            es.onerror = function () {
                if (activeEventSource !== es) return;
                isConnectionOpen = false;
                invalidateMembership();
                activeConnectionId = ++connectionCounter;
                if (es.readyState === EventSource.CONNECTING) {
                    updateConnectionIndicator('reconnecting');
                    var indicator = document.getElementById('sse-status-indicator');
                    if (indicator) {
                        indicator.removeAttribute('data-sync');
                        indicator.removeAttribute('data-sync-message');
                    }
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
                        notifyConnectionReady();
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
                        var cb = (document.querySelector('.kw-tag-checkbox[data-token-order="' + data.row_id + '"]'));
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
            invalidateMembership();
            try { activeEventSource.close(); } catch (e) { console.warn('[SSEBridge] Error closing active EventSource:', e); }
            activeEventSource = null;
            activeSessionZid = null;
            activeServerUrl = null;
            activeConnectionId = 0;
            var w = (window);
            w.__sseActiveZid = null;
            updateConnectionIndicator('disconnected');
            var indicator = document.getElementById('sse-status-indicator');
            if (indicator) {
                indicator.removeAttribute('data-sync');
                indicator.removeAttribute('data-sync-message');
            }
        }
    }

    var SSEBridge = {
        createSource: createSource,
        connect: connect,
        disconnect: disconnect,
        updateConnectionIndicator: updateConnectionIndicator,
        getActiveZid: function () { return activeSessionZid; },
        getActiveConnectionId: function () { return activeConnectionId; },
        getServerUrl: function () { return activeServerUrl; }
    };

    var w = (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.SSEBridge = SSEBridge;
        w.initSSE = connect;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SSEBridge;
    }
})();
