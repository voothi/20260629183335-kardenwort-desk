/** SQLite preference/view ownership. Browser storage is read only for import. */
(function () {
    var preferenceKeys = ['highlight_same_lemma', 'filter_selected_only'];
    var viewReadDeadlineMs = 30000;
    function hasOccurrenceCoordinates(token) {
        return token.sentence_idx !== undefined && token.sentence_idx !== null &&
            token.token_order !== undefined && token.token_order !== null;
    }
    function matchesOccurrence(item, token) {
        if (!hasOccurrenceCoordinates(token)) return false;
        return Number(token.sentence_idx) === Number(item.sentence_idx) &&
            Number(token.token_order) === Number(item.token_order) &&
            Number(token.visual_idx) === Number(item.visual_idx);
    }
    function projectRestoredOccurrence(token) {
        var atomicRows = token.atomic_row_ids !== undefined ? token.atomic_row_ids : token.row_ids;
        var rowIds = Array.isArray(token.row_ids) ? token.row_ids.slice() : [];
        var atomicIds = Array.isArray(atomicRows) ? atomicRows.slice() : [];
        return {visual_idx: token.visual_idx, sentence_idx: token.sentence_idx, token_order: token.token_order,
            atomic_id: token.atomic_id !== undefined ? token.atomic_id : null, lemma: token.lemma || '',
            row_ids: rowIds, atomic_row_ids: atomicIds};
    }
    function restoreOccurrences(items, manifest) {
        return items.map(function(item) {
            var matches = manifest.filter(matchesOccurrence.bind(null, item));
            if (matches.length !== 1) return null;
            return projectRestoredOccurrence(matches[0]);
        }).filter(function(item) { return item !== null; });
    }
    function restoredClientId() {
        var match = location.hash.match(/(?:^#|&)kw-client=([0-9a-f]{32})(?:&|$)/);
        var navigation = performance.getEntriesByType('navigation')[0];
        if (!match || !navigation) return null;
        return navigation.type === 'reload' || navigation.type === 'back_forward' ? match[1] : null;
    }
    function replaceClientHash(id) {
        var raw = location.hash ? location.hash.slice(1) : '';
        var kept = raw ? raw.split('&').filter(function(part) { return part && !/^kw-client=/.test(part); }) : [];
        kept.push('kw-client=' + id);
        return '#' + kept.join('&');
    }
    function clientSlot() {
        var id = restoredClientId() || randomClientId();
        history.replaceState(null, '', replaceClientHash(id));
        return id;
    }
    function randomClientId() {
        var bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        return Array.prototype.map.call(bytes, function(value) { return ('0'+value.toString(16)).slice(-2); }).join('');
    }
    function legacyKeys(zid) {
        return ['kw_active_tokens_'+zid, 'kw_active_card_'+zid];
    }
    function hasLegacyStorageOrigin() {
        return !(location.protocol === 'about:' && window.origin === 'null');
    }
    function readStorageItem(name, key) {
        try {
            return window[name].getItem(key);
        } catch (error) {
            throw new Error('Browser storage is inaccessible (inaccessible_storage)');
        }
    }
    function removeStorageItem(name, key) {
        try {
            window[name].removeItem(key);
        } catch (error) {
            throw new Error('Browser storage is inaccessible (inaccessible_storage)');
        }
    }
    function clearLegacy(zid) {
        if (!hasLegacyStorageOrigin()) return;
        preferenceKeys.forEach(function(key) { removeStorageItem('localStorage', 'kw_'+key); });
        legacyKeys(zid).forEach(function(key) { removeStorageItem('sessionStorage', key); });
    }
    function isLegacyInt(value, min) {
        if (typeof value !== 'number' && typeof value !== 'string') return false;
        var text = String(value);
        var number = Number(text);
        return Number.isInteger(number) && number >= min && String(number) === text;
    }
    function isValidLegacyOccurrence(item) {
        if (!item || typeof item !== 'object' || Array.isArray(item)) return false;
        return isLegacyInt(item.sentence_idx, 1) && isLegacyInt(item.token_order, 0);
    }
    function parseLegacyCard(raw, key, problems) {
        if (raw === null) return 1;
        if (!isLegacyInt(raw, 1)) {
            problems.push({key: key, category: 'invalid_active_card'});
            return null;
        }
        return Number(raw);
    }
    function parseLegacyTokens(raw, key, problems) {
        if (raw === null) return [];
        var parsed;
        try {
            parsed = JSON.parse(raw);
        } catch (error) {
            problems.push({key: key, category: 'malformed_json'});
            return null;
        }
        if (!Array.isArray(parsed) || parsed.length > 10000 || !parsed.every(isValidLegacyOccurrence)) {
            problems.push({key: key, category: 'invalid_active_tokens'});
            return null;
        }
        return parsed;
    }
    function readLegacyPreferences(problems) {
        var values = {};
        preferenceKeys.forEach(function(key) {
            var storageKey = 'kw_'+key;
            var raw = readStorageItem('localStorage', storageKey);
            if (raw === null) return;
            try {
                values[key] = legacyBoolean(raw);
            } catch (error) {
                problems.push({key: storageKey, category: 'invalid_boolean'});
            }
        });
        return values;
    }
    function readLegacyView(zid, problems) {
        var keys = legacyKeys(zid);
        var tokensRaw = readStorageItem('sessionStorage', keys[0]);
        var cardRaw = readStorageItem('sessionStorage', keys[1]);
        if (tokensRaw === null && cardRaw === null) return null;
        var activeCard = parseLegacyCard(cardRaw, keys[1], problems);
        var activeTokens = parseLegacyTokens(tokensRaw, keys[0], problems);
        if (activeCard === null || activeTokens === null) return null;
        return {active_card: activeCard, active_tokens: activeTokens};
    }
    function readLegacy(zid) {
        if (!hasLegacyStorageOrigin()) return {preferences: {}, view: null, problems: []};
        var problems = [];
        var preferences = readLegacyPreferences(problems);
        var view = readLegacyView(zid, problems);
        return {preferences: preferences, view: view, problems: problems};
    }
    function legacyBoolean(raw) {
        if (raw !== 'true' && raw !== 'false') throw new Error('Invalid saved browser preference');
        return raw === 'true';
    }
    function hasImportProblems(body) {
        return !!(body.problems && body.problems.length);
    }
    function buildImportElements(body, current) {
        var recovery = hasImportProblems(body);
        var message = document.createElement('p'), preview = document.createElement('pre');
        var keep = document.createElement('button');
        message.textContent = recovery
            ? 'Saved browser settings are malformed and cannot be imported. Choose Keep database settings to discard only those obsolete browser keys.'
            : 'Saved browser settings were found. Choose which values to keep.';
        preview.textContent = importPreview(body, current);
        keep.textContent = 'Keep database settings';
        var accept = recovery ? null : document.createElement('button');
        if (accept) accept.textContent = 'Import browser settings';
        return {message: message, preview: preview, accept: accept, keep: keep, recovery: recovery};
    }
    function setImportDisabled(ui, disabled) {
        if (ui.accept) ui.accept.disabled = disabled;
        ui.keep.disabled = disabled;
    }
    function syncImportConflict(body, current) {
        body.revisions = current.revisions;
        if (body.view && current.view && Number.isInteger(current.view.revision)) {
            body.view.expected_revision = current.view.revision;
        }
    }
    function importDialog(send, body, current, signal) {
        return new Promise(function(resolve, reject) {
            var dialog = document.createElement('dialog');
            var ui = buildImportElements(body, current);
            dialog.setAttribute('data-testid', 'legacy-state-import');
            if (ui.recovery) dialog.setAttribute('data-import-mode', 'recovery');
            function finish(payload) {
                setImportDisabled(ui, true);
                send('/api/v1/browser-state/import', payload).then(function(result) {
                    if (signal.aborted) return;
                    signal.removeEventListener('abort', cancel);
                    dialog.close(); dialog.remove(); resolve(result);
                }, function(error) {
                    if (signal.aborted) return;
                    ui.message.textContent = 'Import not saved: '+error.message+'. Review and choose again.';
                    if (error.current) {
                        syncImportConflict(body, error.current);
                        ui.preview.textContent = importPreview(body, error.current);
                    }
                    setImportDisabled(ui, false);
                });
            }
            if (ui.accept) ui.accept.onclick = function() { finish(body); };
            ui.keep.onclick = function() { finish({session_zid: body.session_zid, client_id: body.client_id, preferences: {}, revisions: {}}); };
            dialog.addEventListener('cancel', function(event) { event.preventDefault(); });
            [ui.message, ui.preview, ui.accept, ui.keep].filter(Boolean).forEach(function(element) { dialog.appendChild(element); });
            function cancel() { if (dialog.open) dialog.close(); dialog.remove(); reject(new Error('Workspace restoration cancelled')); }
            signal.addEventListener('abort', cancel, {once:true});
            document.body.appendChild(dialog); dialog.showModal();
        });
    }
    function importPreview(body, current) {
        var values = {};
        var keys = hasImportProblems(body) ? preferenceKeys : Object.keys(body.preferences);
        keys.forEach(function(key) { values[key] = current.values[key]; });
        if (hasImportProblems(body)) {
            return JSON.stringify({problems: body.problems, database: values}, null, 2);
        }
        return JSON.stringify({browser: {preferences: body.preferences, view: body.view}, database: values}, null, 2);
    }
    function viewBody(state) { return {session_zid: state.options.zid, client_id: state.client}; }
    function requireView(view) {
        if (!Number.isInteger(view.revision) || view.revision < 0) throw new Error('Invalid committed view revision');
        if (!Number.isInteger(view.active_card) || view.active_card < 1 || !Array.isArray(view.active_tokens)) {
            throw new Error('Invalid committed view state');
        }
        return view;
    }
    function checkRun(state, run) {
        if (state.run !== run || run.signal.aborted) throw new Error('Workspace restoration cancelled');
    }
    function readView(state, run) {
        checkRun(state, run);
        run.deadline = setTimeout(function() { run.abort(new Error('Workspace read timed out. Retry to reconnect.')); }, viewReadDeadlineMs);
        return state.options.send('/api/v1/client-view', viewBody(state), undefined, run.signal).then(function(view) {
            checkRun(state, run); return requireView(view);
        }).finally(function() {
            clearTimeout(run.deadline);
        });
    }
    function buildImportBody(state, view, legacy) {
        if (legacy.problems.length) {
            return Object.assign(viewBody(state), {preferences: {}, view: null, revisions: {}, problems: legacy.problems});
        }
        if (legacy.view) legacy.view.expected_revision = view.revision;
        return Object.assign(viewBody(state), {preferences: legacy.preferences, view: legacy.view, revisions: Object.assign({}, state.revisions)});
    }
    function migrate(state, run, view) {
        checkRun(state, run);
        if (view.browser_imported) { clearLegacy(state.options.zid); return view; }
        var legacy = readLegacy(state.options.zid);
        if (!legacy.problems.length && !Object.keys(legacy.preferences).length && !legacy.view) return view;
        var body = buildImportBody(state, view, legacy);
        function send(path, payload) { return state.options.send(path, payload, undefined, run.signal); }
        return importDialog(send, body, {values: state.options.ui}, run.signal).then(function(result) {
            checkRun(state, run); receive(state, result); clearLegacy(state.options.zid);
            return readView(state, run);
        });
    }
    function present(state, phase, error) {
        if (state.options.present) state.options.present(phase, error);
    }
    function normalizeIds(ids) {
        return Array.isArray(ids) ? ids.map(String) : [];
    }
    function tokenSignature(item) {
        return {
            visual_idx: Number(item.visual_idx),
            sentence_idx: item.sentence_idx == null ? null : Number(item.sentence_idx),
            token_order: item.token_order == null ? '' : String(item.token_order),
            atomic_id: item.atomic_id == null ? null : item.atomic_id,
            lemma: String(item.lemma || ''),
            atomic_row_ids: normalizeIds(item.atomic_row_ids),
            row_ids: normalizeIds(item.row_ids)
        };
    }
    function viewSignature(view) {
        return JSON.stringify({
            active_card: Number(view.active_card),
            active_tokens: (view.active_tokens || []).map(tokenSignature)
        });
    }
    function dispose(state) {
        if (state.run) { clearTimeout(state.run.deadline); state.run.abort(); }
        state.run = null; state.phase = 'disposed'; state.viewInitialized = false;
        state.pendingIntent = null; state.inFlight = false; state.inFlightSignature = null;
    }
    function applyRestoredView(state, run, view) {
        checkRun(state, run);
        state.viewRevision = view.revision;
        var reconcile = state.options.restore(view);
        if (reconcile) {
            return commitView(state, state.options.capture(), run).then(function() {
                state.viewInitialized = true;
            });
        }
        state.viewInitialized = true;
        state.committedSignature = view.revision > 0 ? viewSignature(state.options.capture()) : null;
    }
    function start(state) {
        dispose(state);
        if (!state.viewEnabled || state.options.draft) {
            state.phase = state.options.draft ? 'draft' : 'static';
            present(state, 'ready'); state.ready = Promise.resolve(); return state.ready;
        }
        var run = new AbortController(); state.run = run;
        state.phase = 'initializing'; state.failure = null; state.tail = Promise.resolve();
        state.viewInitialized = false;
        state.committedSignature = null; state.pendingIntent = null; state.inFlight = false; state.inFlightSignature = null;
        present(state, 'initializing');
        state.ready = readView(state, run).then(migrate.bind(null, state, run))
            .then(applyRestoredView.bind(null, state, run))
            .then(function() {
                checkRun(state, run); state.phase = 'ready'; present(state, 'ready');
            }).catch(function(error) {
                if (state.run === run) {
                    reportFailure(state, error); present(state, 'failed', error);
                }
                throw error;
            });
        state.ready.then(undefined, function(error) { console.debug('[Workspace] Restoration stopped:', error.message); });
        return state.ready;
    }
    function commitView(state, intent, run) {
        var signature = viewSignature(intent);
        return state.options.send('/api/v1/client-view', Object.assign(viewBody(state), intent,
            {expected_revision: state.viewRevision}), undefined, run.signal).then(requireView).then(function(view) {
                checkRun(state, run); state.viewRevision = view.revision; state.committedSignature = signature;
            });
    }
    function reportFailure(state, error) {
        state.phase = 'failed'; state.failure = error; state.options.failure(error);
    }
    function sameCommittedView(state, signature) {
        return state.phase !== 'failed' && state.committedSignature !== null && signature === state.committedSignature;
    }
    function isRedundantView(state, signature) {
        if (!state.inFlight) return sameCommittedView(state, signature);
        return !state.pendingIntent && signature === state.inFlightSignature;
    }
    function finishViewSend(state, run, error) {
        if (state.run !== run) return;
        state.inFlightSignature = null;
        if (error) reportFailure(state, error);
        if (state.pendingIntent && state.pendingIntent.run === run) {
            return sendPendingView(state, run);
        }
        state.inFlight = false;
        if (!error) state.phase = 'ready';
    }
    function sendPendingView(state, run) {
        if (state.run !== run) throw new Error('Workspace save belongs to an inactive document');
        var next = state.pendingIntent;
        state.pendingIntent = null;
        if (!next || next.run !== run) {
            state.inFlight = false;
            return;
        }
        if (sameCommittedView(state, next.signature)) {
            state.inFlight = false;
            state.phase = 'ready';
            state.failure = null;
            return;
        }
        state.phase = 'pending';
        state.inFlightSignature = next.signature;
        return state.options.send('/api/v1/client-view', Object.assign(viewBody(state), next.intent,
            {expected_revision: state.viewRevision}), undefined, run.signal).then(requireView).then(function(view) {
            if (state.run !== run) return;
            state.viewRevision = view.revision;
            state.committedSignature = next.signature;
            state.failure = null;
            return finishViewSend(state, run, null);
        }, function(error) {
            return finishViewSend(state, run, error);
        });
    }
    function enqueueView(state, intent, signature) {
        var run = state.run;
        state.pendingIntent = {intent: intent, signature: signature, run: run};
        if (state.inFlight) return state.tail;
        state.inFlight = true;
        state.tail = state.tail.then(function() { return state.ready; }).then(function() {
            return sendPendingView(state, run);
        }, function(error) {
            if (state.run === run) {
                state.inFlight = false;
                state.inFlightSignature = null;
                reportFailure(state, error);
            }
        });
        return state.tail;
    }
    function viewInactive(state) {
        if (!state.viewEnabled || state.phase === 'draft' || state.phase === 'disposed') return true;
        return state.phase === 'failed' && !state.viewInitialized;
    }
    function save(state) {
        if (viewInactive(state)) return Promise.resolve();
        if (state.phase === 'initializing') return state.ready;
        var intent = state.options.capture();
        var signature = viewSignature(intent);
        if (isRedundantView(state, signature)) return state.tail;
        return enqueueView(state, intent, signature);
    }
    function retryFailedView(state) {
        if (state.phase !== 'failed' || !state.viewEnabled || !state.viewInitialized) return;
        return save(state);
    }
    function flush(state) {
        var retryPreviousFailure = state.phase === 'failed' && !state.inFlight;
        return Promise.resolve(state.ready).then(function() {
            return state.tail;
        }).then(function() {
            if (retryPreviousFailure) return retryFailedView(state);
        }).then(function() {
            if (state.phase === 'failed') throw state.failure;
        });
    }
    function reconcileFailure(state, error) {
        if (error.current) { receive(state, error.current); return Promise.reject(error); }
        return state.options.send('/api/v1/config', undefined, 'GET').then(function(result) {
            receive(state, result.preferences); throw error;
        }, function() { throw error; });
    }
    function sendPreference(state, key, value) {
        return Promise.resolve(state.ready).then(function() {
            return state.options.send('/api/v1/config', {key: key,
                value: String(value), expected_revision: state.revisions[key]});
        }).then(function(result) {
            if (state.enabled) {
                requirePreferenceAck(result, key, value);
                acceptPreference(state, key, result.committed_value, result.revision);
            }
            return result;
        }).catch(reconcileFailure.bind(null, state));
    }
    function requirePreferenceAck(result, key, value) {
        if (result.key !== key || result.committed_value !== value) throw new Error('Preference value was not acknowledged');
        if (!Number.isInteger(result.revision) || result.revision < 1) throw new Error('Invalid committed preference revision');
    }
    function acceptPreference(state, key, value, revision) {
        if (!Number.isInteger(revision) || revision < 0) throw new Error('Invalid committed preference revision');
        if (revision < state.revisions[key]) return;
        if (revision === state.revisions[key] && value !== state.values[key]) throw new Error('Conflicting committed preference revision');
        state.revisions[key] = revision; state.values[key] = value;
    }
    function projectPreference(state, key) {
        if (state.enabled) state.options.project(key, state.values[key]);
    }
    function receive(state, snapshot) {
        Object.keys(snapshot.values).forEach(function(key) {
            acceptPreference(state, key, snapshot.values[key], snapshot.revisions[key]);
            if (!state.options.unresolved(key)) projectPreference(state, key);
        });
    }
    function create(options) {
        var enabled = options.ui.state_authority === 'sqlite' && options.connected !== false;
        var viewEnabled = enabled && options.ui.client_view_enabled !== false;
        var state = {options: options, enabled: enabled, viewEnabled: viewEnabled, viewInitialized: false, client: viewEnabled && !options.draft ? clientSlot() : null,
            revisions: Object.assign({}, options.ui.preference_revisions || {}), values: Object.assign({}, options.ui), viewRevision: 0,
            committedSignature: null, inFlight: false, inFlightSignature: null, pendingIntent: null,
            phase: options.draft ? 'draft' : 'initializing', failure: null, tail: Promise.resolve(), ready: null};
        return {start: start.bind(null, state), dispose: dispose.bind(null, state), save: save.bind(null, state), flush: flush.bind(null, state),
            sendPreference: sendPreference.bind(null, state), receive: receive.bind(null, state), projectPreference: projectPreference.bind(null, state)};
    }
    window.SqliteState = {create: create, restoreOccurrences: restoreOccurrences, restoredClientId: restoredClientId};
})();
