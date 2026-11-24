
/**
 * @file sse.js
 * Client-side Server-Sent Events (SSE) streaming bridge.
 * Connects EventSource to /api/v1/events?session=<zid>, routes deltas to store,
 * and maintains connection status indicators in #session-header.
 */

/**
 * @typedef {{type: 'ping'|'heartbeat'|'connected'|'disconnected'|'resync_required'}} ConnectionEvent
 * @typedef {{type: 'provider_failover', task_type?: string, task?: string, new_provider?: string, to?: string, provider?: string}} ProviderFailoverEvent
 * @typedef {{type: 'tag_updated', row_id: string|number, status: boolean}} TagEvent
 * @typedef {(UpdatePayload & {type?: undefined})|ConnectionEvent|ProviderFailoverEvent|TagEvent} StreamEvent
 */

(function () {

    var activeEventSource = null;

    var activeConnectionOpen = false;

    var activeSessionZid = null;

    var activeServerUrl = null;

    var activeNormalizedServer = null;

    var activeToken = '';

    var connectionCounter = 0;

    var activeConnectionId = 0;

    var reconnectTimer = null;

    var reconnectBackoffMs = 1000;

    var persistedResumeState = null;

    var eventSubscribers = [];

    var MAX_RETRY_DELAY_MS = 16000;

    var MAX_POISON_RETRIES = 1;

    function isTransientHttpStatus(status) {
        return status === 429 || status === 500 || status === 502 || status === 503 || status === 504;
    }

    function parseRetryAfterMs(headers, defaultDelayMs) {
        if (!headers || typeof headers.get !== 'function') return defaultDelayMs;
        var raw = headers.get('Retry-After');
        if (!raw) return defaultDelayMs;
        var trimmed = String(raw).trim();
        var seconds = Number(trimmed);
        if (isFinite(seconds) && seconds >= 0) {
            return Math.min(Math.max(Math.round(seconds * 1000), 0), MAX_RETRY_DELAY_MS);
        }
        var parsedDate = Date.parse(trimmed);
        if (!isNaN(parsedDate)) {
            return Math.min(Math.max(parsedDate - Date.now(), 0), MAX_RETRY_DELAY_MS);
        }
        return defaultDelayMs;
    }

    function disposeAttempt(attempt) {
        if (!attempt || attempt.disposed) return;
        attempt.disposed = true;
        if (attempt.reader) {
            var reader = attempt.reader;
            attempt.reader = null;
            try {
                var cancelResult = reader.cancel();
                if (cancelResult && typeof cancelResult.catch === 'function') {
                    cancelResult.catch(noop);
                }
            } catch (e) {}
        }
        if (attempt.controller) {
            var ctrl = attempt.controller;
            attempt.controller = null;
            try { ctrl.abort(); } catch (e) {}
        }
    }

    function authenticatedSource(url, token) {
        var source = {readyState: 0, onopen: null, onerror: null, onmessage: null, __isAuthenticatedSource: true};
        var activeAttempt = null, timer = null, closed = false, terminalStopped = false;
        var delay = 1000, attemptId = 0, consecutiveHandlerFailures = 0;
        var listeners = {};
        function clearAttemptTimer() {
            if (timer !== null) {
                clearTimeout(timer);
                timer = null;
            }
        }
        source.addEventListener = function(type, listener) {
            (listeners[type] = listeners[type] || []).push(listener);
        };
        source.hasPendingReconnect = function() {
            return !closed && !terminalStopped && timer !== null;
        };
        source.canAccelerateReconnect = function() {
            return !closed && !terminalStopped && timer !== null;
        };
        source.isTerminal = function() {
            return terminalStopped;
        };
        source.close = function() {
            closed = true;
            terminalStopped = true;
            attemptId++;
            source.readyState = 2;
            clearAttemptTimer();
            disposeAttempt(activeAttempt);
            activeAttempt = null;
        };
        source.reconnectNow = function(force) {
            if (closed || source.readyState === 1) return;
            if (!force && (terminalStopped || timer === null)) return;
            terminalStopped = false;
            consecutiveHandlerFailures = 0;
            clearAttemptTimer();
            delay = 1000;
            attemptId++;
            disposeAttempt(activeAttempt);
            activeAttempt = null;
            start();
        };
        function failed(attempt, error) {
            disposeAttempt(attempt);
            if (closed || !attempt || attempt.id !== attemptId) return;
            if (activeAttempt === attempt) activeAttempt = null;
            var err = error || new Error('Event stream failed');
            if (err.isHandlerError) {
                consecutiveHandlerFailures += 1;
                if (consecutiveHandlerFailures > MAX_POISON_RETRIES) {
                    err.terminal = true;
                    err.isPoison = true;
                }
            }
            if (err.terminal) {
                terminalStopped = true;
                source.readyState = 2;
                clearAttemptTimer();
            } else {
                source.readyState = 0;
            }
            if (source.onerror) source.onerror(err);
            if (!err.terminal && !closed && !terminalStopped && attempt.id === attemptId) {
                var nextDelay = typeof err.retryDelayMs === 'number' ? err.retryDelayMs : delay;
                timer = setTimeout(start, nextDelay);
                delay = Math.min(Math.max(delay * 2, nextDelay), MAX_RETRY_DELAY_MS);
            }
        }
        function consume(attempt, reader) {
            attempt.reader = reader;
            var decoder = new TextDecoder('utf-8'), buffer = '', data = [], eventType = 'message';
            function isStale() {
                return closed || attempt.disposed || attempt.id !== attemptId;
            }
            function dispatch() {
                if (!data.length || isStale()) return;
                var event = {data: data.join('\n')};
                if (eventType === 'message' && source.onmessage) source.onmessage(event);
                (listeners[eventType] || []).forEach(function(listener) { listener(event); });
                consecutiveHandlerFailures = 0;
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
                if (isStale()) {
                    disposeAttempt(attempt);
                    return Promise.resolve();
                }
                return reader.read().then(function(chunk) {
                    if (isStale()) {
                        disposeAttempt(attempt);
                        return;
                    }
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
            if (closed || terminalStopped) return;
            timer = null;
            disposeAttempt(activeAttempt);
            var myAttempt = {id: ++attemptId, controller: null, reader: null, disposed: false};
            activeAttempt = myAttempt;
            if (typeof AbortController === 'undefined' || typeof TextDecoder === 'undefined' || typeof fetch !== 'function') {
                var unavailable = new Error('Authenticated event streaming is unavailable in this host');
                unavailable.terminal = true;
                failed(myAttempt, unavailable);
                return;
            }
            myAttempt.controller = new AbortController();
            var options = {headers: {'Accept': 'text/event-stream', 'X-API-Token': token}, cache: 'no-store'};
            options.signal = myAttempt.controller.signal;
            Promise.resolve().then(function() { return fetch(url, options); }).then(function(response) {
                if (closed || myAttempt.disposed || myAttempt.id !== attemptId) {
                    if (response && response.body && typeof response.body.getReader === 'function') {
                        try {
                            var staleReader = response.body.getReader();
                            var staleCancel = staleReader.cancel();
                            if (staleCancel && typeof staleCancel.catch === 'function') staleCancel.catch(noop);
                        } catch (e) {}
                    }
                    disposeAttempt(myAttempt);
                    return;
                }
                if (!response || !response.ok) {
                    var status = response && typeof response.status === 'number' ? response.status : 0;
                    var httpErr = new Error('Authenticated event stream unavailable' + (status ? ' (' + status + ')' : ''));
                    if (isTransientHttpStatus(status)) {
                        httpErr.terminal = false;
                        httpErr.retryDelayMs = parseRetryAfterMs(response && response.headers, delay);
                    } else {
                        httpErr.terminal = true;
                    }
                    throw httpErr;
                }
                if (!response.body || !response.body.getReader || typeof TextDecoder === 'undefined') {
                    var capErr = new Error('Authenticated event stream body reader unavailable');
                    capErr.terminal = true;
                    throw capErr;
                }
                source.readyState = 1;
                delay = 1000;
                if (source.onopen) source.onopen({});
                return consume(myAttempt, response.body.getReader());
            }).catch(function(error) {
                failed(myAttempt, error);
            });
        }
        timer = setTimeout(start, 0);
        return source;
    }

    function resolveEffectiveToken(token) {
        if (token !== undefined && token !== null) return String(token);
        var resolved = (window.getApiToken ? window.getApiToken() : window.API_TOKEN) || '';
        return String(resolved);
    }

    function createSource(url, token) {
        var effectiveToken = resolveEffectiveToken(token);
        return effectiveToken ? authenticatedSource(url, effectiveToken) : new EventSource(url);
    }

    function clearReconnectTimer() {
        if (reconnectTimer !== null) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    }

    function noop() {}

    function releaseSource(es) {
        if (!es) return;
        es.onopen = noop;
        es.onerror = noop;
        es.onmessage = noop;
        try { es.close(); } catch (e) { console.warn('[SSE] Error closing active EventSource:', e); }
    }

    function normalizeServerBase(serverUrl, bootstrapSession) {
        var base = (serverUrl || '').replace(/\/+$/, '');
        if (base === 'null' || base.indexOf('about:') === 0) base = '';
        if (!base && (!bootstrapSession || bootstrapSession.server_enabled !== false)) {
            if (window.location && (window.location.protocol === 'http:' || window.location.protocol === 'https:')) {
                base = (window.location.origin || '').replace(/\/+$/, '');
            }
        }
        return base;
    }

    function currentDocumentZid(fallback) {
        var el = typeof document !== 'undefined' && document.getElementById ? document.getElementById('session-zid') : null;
        var zid = el ? (el.textContent || el.innerText || '').trim() : '';
        return zid || fallback || '';
    }

    function subscribe(listener) {
        if (typeof listener !== 'function') {
            throw new TypeError('SSE event subscriber must be a function');
        }
        if (eventSubscribers.indexOf(listener) === -1) {
            eventSubscribers.push(listener);
        }
        return function unsubscribe() {
            var idx = eventSubscribers.indexOf(listener);
            if (idx !== -1) eventSubscribers.splice(idx, 1);
        };
    }

    function notifyEventSubscribers(data) {
        var snapshot = eventSubscribers.slice();
        for (var i = 0; i < snapshot.length; i++) {
            snapshot[i](data);
        }
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
            var tabs = document.querySelector('.kw-workspace-tab-bar .kw-tab-track');
            (header || (tabs && tabs.parentNode) || document.body).appendChild(indicator);
        }

        if (indicator) {
            indicator.hidden = !header;
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

    function scheduleNativeReconnect(sessionZid, serverUrl, token) {
        if (reconnectTimer !== null) return;
        var delay = reconnectBackoffMs;
        reconnectBackoffMs = Math.min(reconnectBackoffMs * 2, 16000);
        reconnectTimer = setTimeout(function () {
            reconnectTimer = null;
            if (activeSessionZid === sessionZid && !activeEventSource) {
                connect(sessionZid, serverUrl, token, true);
            }
        }, delay);
    }

    /**
     * Connect to SSE streaming endpoint for session
     * @param {string} sessionZid
     * @param {string} [serverUrl]
     */
    function connect(sessionZid, serverUrl, token, recoverCommitted) {
        if (!sessionZid || typeof window.EventSource === 'undefined') return null;

        var w = (window);
        var explicitServerUrl = Boolean(serverUrl);
        var bootstrapRoot = w.__BOOTSTRAP__ || (w.AppState && w.AppState.bootstrap) || null;
        var bootstrapSession = (bootstrapRoot && (bootstrapRoot.session_data || bootstrapRoot)) || null;
        var inLookupMode = typeof w.isLookupMode === 'function'
            ? w.isLookupMode()
            : Boolean(typeof document !== 'undefined' && document.body && document.body.getAttribute('data-view-mode') === 'lookup');
        if (bootstrapSession && bootstrapSession.server_enabled === false && (inLookupMode || !explicitServerUrl)) {
            return null;
        }
        if (!serverUrl && bootstrapSession && bootstrapSession.server_url) {
            serverUrl = bootstrapSession.server_url;
        }
        if (!serverUrl && bootstrapRoot && bootstrapRoot.server_url) {
            serverUrl = bootstrapRoot.server_url;
        }

        // Guard against file:/// filesystem URI errors
        if (window.location.protocol === 'file:' && !serverUrl) return null;

        persistedResumeState = null;

        var normalizedBase = normalizeServerBase(serverUrl, bootstrapSession);
        var effectiveToken = resolveEffectiveToken(token);

        // Reuse an existing live subscription only when session, normalized server and credential all match.
        if (activeEventSource && activeEventSource.readyState !== 2 &&
            activeSessionZid === sessionZid &&
            activeNormalizedServer === normalizedBase &&
            activeToken === effectiveToken) {
            return activeEventSource;
        }
        clearReconnectTimer();
        if (activeEventSource) {
            invalidateMembership();
            releaseSource(activeEventSource);
            activeEventSource = null;
            activeConnectionOpen = false;
            activeSessionZid = null;
            activeServerUrl = null;
            activeNormalizedServer = null;
            activeToken = '';
            activeConnectionId = 0;
            w.__sseActiveZid = null;
        }

        try {
            var sseUrl = (normalizedBase ? normalizedBase : '') + '/api/v1/events?session=' + encodeURIComponent(sessionZid);
            var es = createSource(sseUrl, effectiveToken);

            activeConnectionId = ++connectionCounter;
            activeEventSource = es;
            activeConnectionOpen = false;
            activeSessionZid = sessionZid;
            activeServerUrl = serverUrl || null;
            activeNormalizedServer = normalizedBase;
            activeToken = effectiveToken;
            w.__sseActiveZid = sessionZid;

            es.recoveryMode = recoverCommitted ? 'committed' : 'membership';

            updateConnectionIndicator('reconnecting');

            function notifyConnectionReady() {
                if (activeEventSource !== es) return;
                reconnectBackoffMs = 1000;
                updateConnectionIndicator('connected');
                if (!activeConnectionOpen) {
                    activeConnectionOpen = true;
                    if (typeof w.reconcileStreamMembership === 'function') {
                        w.reconcileStreamMembership({
                            session: sessionZid,
                            connectionId: activeConnectionId,
                            serverUrl: serverUrl,
                            refreshStatus: es.recoveryMode === 'committed'
                        }).catch(function(error) {
                            console.debug('[SSE] Membership recovery stopped:', error.message);
                        });
                    }
                }
            }

            es.onopen = function () {
                notifyConnectionReady();
            };

            es.onerror = function (err) {
                if (activeEventSource !== es) return;
                activeConnectionOpen = false;
                es.recoveryMode = 'committed';
                invalidateMembership();
                activeConnectionId = ++connectionCounter;
                if (es.__isAuthenticatedSource) {
                    if (err && err.isPoison) {
                        updateConnectionIndicator('error');
                    } else {
                        updateConnectionIndicator(es.readyState === 0 ? 'reconnecting' : 'disconnected');
                    }
                    return;
                }
                if (typeof es.readyState === 'number' && es.readyState !== 0) {
                    releaseSource(es);
                    activeEventSource = null;
                    updateConnectionIndicator('reconnecting');
                    scheduleNativeReconnect(sessionZid, serverUrl, effectiveToken);
                    return;
                }
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
                    if (err && typeof err === 'object') err.isHandlerError = true;
                    throw err;
                }
            };
            /** @param {StreamEvent|null} data */
            function applyEvent(data) {
                if (data && (data.type === 'ping' || data.type === 'heartbeat')) return;
                if (data && data.type === 'resync_required') {
                    disconnect();
                    connect(sessionZid, serverUrl, token, true);
                    return;
                }
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
                if (data.type === 'preferences_updated') {
                    if (w.receivePreferenceSnapshot) w.receivePreferenceSnapshot(data.preferences);
                    return;
                }
                if (typeof w.noteStreamMutation === 'function') w.noteStreamMutation();
                if (typeof w.receiveUpdate === 'function') {
                    w.receiveUpdate(data);
                } else if (w.AppStore) {
                    w.AppStore.applyDeltas(data);
                }
                notifyEventSubscribers(data);
            }
            return es;

        } catch (e) {
            updateConnectionIndicator('error');
            console.error('[SSE] Connection initialization failed', e);
            throw e;
        }
    }

    function reconnect(force) {
        if (!activeSessionZid) return null;
        if (activeEventSource && activeEventSource.__isAuthenticatedSource) {
            if (activeEventSource.readyState !== 1) {
                if (force || (activeEventSource.canAccelerateReconnect && activeEventSource.canAccelerateReconnect())) {
                    activeEventSource.reconnectNow(Boolean(force));
                }
            }
            return activeEventSource;
        }
        var needsReconnect = !activeEventSource || activeEventSource.readyState === 2 || reconnectTimer !== null;
        if (!needsReconnect && (!force || activeConnectionOpen)) {
            return activeEventSource;
        }
        var zid = activeSessionZid;
        var url = activeServerUrl;
        var tok = activeToken;
        clearReconnectTimer();
        reconnectBackoffMs = 1000;
        if (activeEventSource) {
            releaseSource(activeEventSource);
            activeEventSource = null;
            activeConnectionOpen = false;
        }
        return connect(zid, url, tok, true);
    }

    /**
     * Disconnect active SSE connection
     * @param {boolean} [preserveResume]
     */
    function disconnect(preserveResume) {
        clearReconnectTimer();
        reconnectBackoffMs = 1000;
        if (!preserveResume) {
            persistedResumeState = null;
        }
        if (activeEventSource || activeSessionZid) {
            invalidateMembership();
            if (activeEventSource) {
                releaseSource(activeEventSource);
            }
            activeEventSource = null;
            activeConnectionOpen = false;
            activeSessionZid = null;
            activeServerUrl = null;
            activeNormalizedServer = null;
            activeToken = '';
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

    function handlePageHide(event) {
        if (event && event.persisted) {
            if (activeSessionZid) {
                persistedResumeState = {
                    sessionZid: activeSessionZid,
                    serverUrl: activeServerUrl,
                    token: activeToken
                };
            }
            disconnect(true);
        } else {
            persistedResumeState = null;
            disconnect(false);
        }
    }

    function handlePageShow(event) {
        if (!event || !event.persisted || !persistedResumeState) return;
        var resume = persistedResumeState;
        persistedResumeState = null;
        var targetZid = currentDocumentZid(resume.sessionZid);
        if (!targetZid) return;
        connect(targetZid, resume.serverUrl, resume.token, true);
    }

    var SSEBridge = {
        createSource: createSource,
        connect: connect,
        reconnect: reconnect,
        disconnect: disconnect,
        subscribe: subscribe,
        updateConnectionIndicator: updateConnectionIndicator,
        getActiveZid: function () { return activeSessionZid; },
        getActiveConnectionId: function () { return activeConnectionId; },
        getActiveSource: function () { return activeEventSource; },
        hasReconnectTimer: function () {
            return reconnectTimer !== null || Boolean(activeEventSource && activeEventSource.hasPendingReconnect && activeEventSource.hasPendingReconnect());
        },
        needsCommittedRecovery: function () { return activeEventSource && activeEventSource.recoveryMode === 'committed'; },
        getServerUrl: function () { return activeServerUrl; },
        _onPageHide: handlePageHide,
        _onPageShow: handlePageShow
    };

    var w = (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.SSEBridge = SSEBridge;
        w.initSSE = connect;
        if (w.addEventListener && !w.__sseLifecycleAttached) {
            w.__sseLifecycleAttached = true;
            w.addEventListener('pagehide', function (event) {
                if (w.SSEBridge && w.SSEBridge._onPageHide) w.SSEBridge._onPageHide(event);
            });
            w.addEventListener('pageshow', function (event) {
                if (w.SSEBridge && w.SSEBridge._onPageShow) w.SSEBridge._onPageShow(event);
            });
            w.addEventListener('online', function () {
                if (w.SSEBridge && w.SSEBridge.reconnect) w.SSEBridge.reconnect(false);
            });
        }
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SSEBridge;
    }
})();
