/** SQLite preference/view ownership. Browser storage is read only for import. */
(function () {
    var preferenceKeys = ['highlight_same_lemma', 'filter_selected_only'];
    function restoreOccurrences(items, manifest) {
        return items.map(function(item) {
            var matches = manifest.filter(function(token) {
                return Number(token.sentence_idx) === Number(item.sentence_idx) && Number(token.token_order) === Number(item.token_order) &&
                    Number(token.visual_idx) === Number(item.visual_idx);
            });
            if (matches.length !== 1) return null;
            var token = matches[0];
            return {visual_idx: token.visual_idx, sentence_idx: token.sentence_idx, token_order: token.token_order,
                atomic_id: token.atomic_id, lemma: token.lemma, row_ids: token.row_ids.slice(),
                atomic_row_ids: (token.atomic_row_ids !== undefined ? token.atomic_row_ids : token.row_ids).slice()};
        }).filter(function(item) { return item !== null; });
    }
    function clientSlot() {
        var match = location.hash.match(/(?:^#|&)kw-client=([0-9a-f]{32})(?:&|$)/);
        var navigation = performance.getEntriesByType('navigation')[0];
        var reusable = match && navigation && (navigation.type === 'reload' || navigation.type === 'back_forward');
        var id = reusable ? match[1] : randomClientId();
        history.replaceState(null, '', '#kw-client='+id);
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
    function clearLegacy(zid) {
        if (!hasLegacyStorageOrigin()) return;
        preferenceKeys.forEach(function(key) { localStorage.removeItem('kw_'+key); });
        legacyKeys(zid).forEach(function(key) { sessionStorage.removeItem(key); });
    }
    function readLegacy(zid) {
        if (!hasLegacyStorageOrigin()) return {preferences: {}, view: null};
        var values = {}, keys = legacyKeys(zid);
        preferenceKeys.forEach(function(key) {
            var raw = localStorage.getItem('kw_'+key);
            if (raw !== null) {
                values[key] = legacyBoolean(raw);
            }
        });
        var tokens = sessionStorage.getItem(keys[0]), card = sessionStorage.getItem(keys[1]);
        var view = tokens !== null || card !== null;
        return {preferences: values, view: view ? {active_card: Number(card || 1), active_tokens: JSON.parse(tokens || '[]')} : null};
    }
    function legacyBoolean(raw) {
        if (raw !== 'true' && raw !== 'false') throw new Error('Invalid saved browser preference');
        return raw === 'true';
    }
    function importDialog(send, body, current) {
        return new Promise(function(resolve) {
            var dialog = document.createElement('dialog');
            var message = document.createElement('p'), preview = document.createElement('pre');
            var accept = document.createElement('button'), keep = document.createElement('button');
            dialog.setAttribute('data-testid', 'legacy-state-import');
            message.textContent = 'Saved browser settings were found. Choose which values to keep.';
            preview.textContent = importPreview(body, current);
            accept.textContent = 'Import browser settings'; keep.textContent = 'Keep database settings';
            function finish(payload) {
                accept.disabled = true; keep.disabled = true;
                send('/api/v1/browser-state/import', payload).then(function(result) {
                    dialog.close(); dialog.remove(); resolve(result);
                }, function(error) {
                    message.textContent = 'Import not saved: '+error.message+'. Review and choose again.';
                    if (error.current) {
                        body.revisions = error.current.revisions;
                        preview.textContent = importPreview(body, error.current);
                    }
                    accept.disabled = false; keep.disabled = false;
                });
            }
            accept.onclick = function() { finish(body); };
            keep.onclick = function() { finish({session_zid: body.session_zid, client_id: body.client_id, preferences: {}, revisions: {}}); };
            dialog.addEventListener('cancel', function(event) { event.preventDefault(); });
            [message, preview, accept, keep].forEach(function(element) { dialog.appendChild(element); });
            document.body.appendChild(dialog); dialog.showModal();
        });
    }
    function importPreview(body, current) {
        var values = {};
        Object.keys(body.preferences).forEach(function(key) { values[key] = current.values[key]; });
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
    function readView(state) { return state.options.send('/api/v1/client-view', viewBody(state)).then(requireView); }
    function migrate(state, view) {
        if (view.browser_imported) { clearLegacy(state.options.zid); return; }
        var legacy = readLegacy(state.options.zid);
        if (!Object.keys(legacy.preferences).length && !legacy.view) return;
        if (legacy.view) legacy.view.expected_revision = view.revision;
        var body = Object.assign(viewBody(state), legacy, {revisions: Object.assign({}, state.revisions)});
        return importDialog(state.options.send, body, {values: state.options.ui}).then(function(result) {
            receive(state, result); clearLegacy(state.options.zid);
        });
    }
    function start(state) {
        if (state.phase === 'draft') { state.ready = Promise.resolve(); return state.ready; }
        if (!state.enabled) { state.phase = 'static'; return Promise.resolve(); }
        state.ready = readView(state).then(migrate.bind(null, state)).then(readView.bind(null, state)).then(function(view) {
            state.viewRevision = view.revision;
            state.options.restore(view);
            state.phase = 'ready';
        });
        state.ready.catch(reportFailure.bind(null, state));
        return state.ready;
    }
    function reportFailure(state, error) {
        state.phase = 'failed'; state.failure = error; state.options.failure(error);
    }
    function save(state) {
        if (!state.enabled || state.phase === 'draft') return Promise.resolve();
        var intent = state.options.capture();
        state.tail = state.tail.then(function() { return state.ready; }).then(function() {
            state.phase = 'pending';
            return state.options.send('/api/v1/client-view', Object.assign(viewBody(state), intent, {expected_revision: state.viewRevision}));
        }).then(requireView).then(function(view) {
            state.viewRevision = view.revision; state.phase = 'ready'; state.failure = null;
        }, reportFailure.bind(null, state));
        return state.tail;
    }
    function flush(state) {
        return Promise.resolve(state.ready).then(function() { return state.tail; }).then(function() {
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
            return state.options.send('/api/v1/config', {section: 'rendering', key: key,
                value: String(value), expected_revision: state.revisions[key]});
        }).then(function(result) {
            if (state.enabled) requirePreferenceAck(result, key, value);
            state.revisions[key] = result.revision;
            return result;
        }).catch(reconcileFailure.bind(null, state));
    }
    function requirePreferenceAck(result, key, value) {
        if (result.key !== key || result.committed_value !== value) throw new Error('Preference value was not acknowledged');
        if (!Number.isInteger(result.revision) || result.revision < 1) throw new Error('Invalid committed preference revision');
    }
    function receive(state, snapshot) {
        Object.keys(snapshot.values).forEach(function(key) {
            if (snapshot.revisions[key] < (state.revisions[key] || 0)) return;
            state.revisions[key] = snapshot.revisions[key];
            if (state.options.unresolved(key)) return;
            state.options.project(key, snapshot.values[key]);
        });
    }
    function create(options) {
        var enabled = options.ui.state_authority === 'sqlite';
        var state = {options: options, enabled: enabled, client: enabled && !options.draft ? clientSlot() : null,
            revisions: Object.assign({}, options.ui.preference_revisions || {}), viewRevision: 0,
            phase: options.draft ? 'draft' : 'initializing', failure: null, tail: Promise.resolve(), ready: null};
        return {start: start.bind(null, state), save: save.bind(null, state), flush: flush.bind(null, state),
            sendPreference: sendPreference.bind(null, state), receive: receive.bind(null, state)};
    }
    window.SqliteState = {create: create, restoreOccurrences: restoreOccurrences};
})();
