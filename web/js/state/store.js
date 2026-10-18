// @ts-check

/**
 * @file store.js
 * Unidirectional atomic state manager for Kardenwort Desk.
 * Manages SessionState, provides event subscription, action dispatch, and atomic delta merging.
 */

/**
 * @typedef {import('../types.js').SessionState} SessionState
 * @typedef {import('../types.js').UpdatePayload} UpdatePayload
 * @typedef {import('../types.js').UIConfig} UIConfig
 *
 * @typedef {Object} Action
 * @property {string} type - Action type discriminator
 * @property {any} [payload] - Action payload
 *
 * @typedef {function(SessionState, Action?): void} StateListener
 */

(function () {
    /** @type {SessionState} */
    var state = {
        zid: '',
        session_zid: '',
        language: 'de',
        target_lang: 'ru',
        source_text: '',
        translated_text: '',
        text_mode: 'single',
        delivery_mode: 'container',
        server_enabled: false,
        server_url: '',
        api_token: '',
        headers: [],
        data_rows: [],
        sentence_cards: [],
        config: {},
        selectedLemmas: {},
        highlightSameLemma: false,
        filterSelectedOnly: false,
        isFinished: false,
        selectedRowIds: [],
        activeTokenSelections: [],
        activeSentenceIdx: null,
        activeTabSeq: null
    };

    /** @type {Set<StateListener>} */
    var listeners = new Set();

    /**
     * Get current state snapshot
     * @returns {SessionState}
     */
    function getState() {
        return state;
    }

    /**
     * Subscribe to state mutations
     * @param {StateListener} listener
     * @returns {function(): void} Unsubscribe function
     */
    function subscribe(listener) {
        if (typeof listener !== 'function') {
            throw new TypeError('Listener must be a function');
        }
        listeners.add(listener);
        return function unsubscribe() {
            listeners.delete(listener);
        };
    }

    /**
     * Notify all subscribed listeners
     * @param {Action} [action]
     */
    function notify(action) {
        // Synchronize with legacy window.AppState if present
        var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
        if (w && w.AppState) {
            Object.assign(w.AppState, state);
        }
        listeners.forEach(function (listener) {
            try {
                listener(state, action);
            } catch (err) {
                console.error('[Store] Error in state listener:', err);
            }
        });
    }

    /**
     * Dispatch an action to mutate state
     * @param {Action} action
     */
    function dispatch(action) {
        if (!action || typeof action.type !== 'string') {
            throw new TypeError('Action must have a string type');
        }

        switch (action.type) {
            case 'INIT_SESSION': {
                var p = action.payload || {};
                state.zid = p.zid || p.session_zid || state.zid;
                state.session_zid = p.session_zid || p.zid || state.session_zid;
                state.language = p.language || p.lang || state.language;
                state.target_lang = p.target_lang || p.target_language || state.target_lang;
                state.source_text = p.source_text || p.sourceText || state.source_text;
                state.translated_text = p.translated_text || p.sentence_translation || p.translatedText || state.translated_text;
                state.sentence_translation = state.translated_text;
                state.text_mode = p.text_mode || state.text_mode;
                state.delivery_mode = p.delivery_mode || state.delivery_mode;
                state.server_enabled = Boolean(p.server_enabled);
                state.server_url = p.server_url || state.server_url;
                state.api_token = p.api_token || state.api_token;
                state.headers = Array.isArray(p.headers) ? p.headers : state.headers;
                state.data_rows = Array.isArray(p.data_rows) ? p.data_rows : state.data_rows;
                state.sentence_cards = Array.isArray(p.sentence_cards) ? p.sentence_cards : state.sentence_cards;
                state.config = Object.assign({}, state.config, p.ui_config || p.config || {});
                if (state.config.highlight_same_lemma !== undefined) {
                    state.highlightSameLemma = Boolean(state.config.highlight_same_lemma);
                }
                if (state.config.filter_selected_only !== undefined) {
                    state.filterSelectedOnly = Boolean(state.config.filter_selected_only);
                }
                break;
            }

            case 'SET_CONFIG': {
                var cfg = action.payload || {};
                state.config = Object.assign({}, state.config, cfg);
                if (cfg.highlight_same_lemma !== undefined) {
                    state.highlightSameLemma = Boolean(cfg.highlight_same_lemma);
                }
                if (cfg.filter_selected_only !== undefined) {
                    state.filterSelectedOnly = Boolean(cfg.filter_selected_only);
                }
                break;
            }

            case 'TOGGLE_HIGHLIGHT_SAME_LEMMA': {
                state.highlightSameLemma = (action.payload !== undefined) ? Boolean(action.payload) : !state.highlightSameLemma;
                if (state.config) state.config.highlight_same_lemma = state.highlightSameLemma;
                break;
            }

            case 'TOGGLE_FILTER_SELECTED': {
                state.filterSelectedOnly = (action.payload !== undefined) ? Boolean(action.payload) : !state.filterSelectedOnly;
                if (state.config) state.config.filter_selected_only = state.filterSelectedOnly;
                break;
            }

            case 'SET_FINISHED': {
                state.isFinished = Boolean(action.payload);
                break;
            }

            case 'UPDATE_DELTAS': {
                applyDeltas(action.payload);
                return; // applyDeltas calls notify internally
            }

            default:
                // Custom or component-specific action
                if (action.payload && typeof action.payload === 'object') {
                    Object.assign(state, action.payload);
                }
                break;
        }

        notify(action);
    }

    /**
     * Atomically merges incoming WebSocket, SSE, or polling delta payloads into state.
     * @param {UpdatePayload|any} payload
     */
    function applyDeltas(payload) {
        if (!payload) return;

        // Unpack raw or wrapped envelope payloads
        var data = payload;
        if (payload.data && typeof payload.data === 'object') {
            data = payload.data;
        }

        if (data.stage === 'finished' || payload.stage === 'finished') {
            state.isFinished = true;
        }

        var tText = data.translated_text || data.sentence_translation || data.translatedText || payload.translated_text || payload.sentence_translation;
        if (tText) {
            state.translated_text = tText;
            state.sentence_translation = tText;
        }

        var sText = data.source_text || data.sourceText || payload.source_text;
        if (sText) {
            state.source_text = sText;
        }

        var prov = data.text_provenance || data.textProvenance || payload.text_provenance;
        if (prov) {
            // @ts-ignore
            state.textProvenance = prov;
        }

        if (Array.isArray(data.sentence_cards)) {
            state.sentence_cards = data.sentence_cards;
        }

        notify({ type: 'UPDATE_DELTAS', payload: payload });
    }

    var Store = {
        getState: getState,
        dispatch: dispatch,
        subscribe: subscribe,
        applyDeltas: applyDeltas
    };

    // Expose on global window object
    var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.AppStore = Store;
        w.AppState = w.AppState || {};
        // Link store.applyDeltas to window.AppState.applyDeltas
        w.AppState.applyDeltas = applyDeltas;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Store;
    }
})();
