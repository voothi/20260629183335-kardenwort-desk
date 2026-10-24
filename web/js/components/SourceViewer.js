// @ts-check
/// <reference path="../host.d.ts" />

/**
 * @file SourceViewer.js
 * Autonomous typed vanilla client component for rendering source text chunks,
 * token spans, hover peer highlights, and RMB context triggers.
 */

/**
 * @typedef {import('../types.js').SessionState} SessionState
 * @typedef {import('../types.js').Token} Token
 */

(function () {
    /**
     * @param {string} str
     * @returns {string}
     */
    function escapeHtml(str) {
        var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
        if (w && typeof w.escapeHtml === 'function') {
            return w.escapeHtml(str);
        }
        throw new Error('Required canonical escapeHtml helper is unavailable');
    }

    /**
     * Splits text into alternating non-word and word chunks.
     * @param {string} text
     * @returns {Array<string>}
     */
    function splitTextTokens(text) {
        var rx = /([a-zA-Z0-9'\u00C0-\u024F\u0400-\u04FF\u0500-\u052F\u1E00-\u1EFF]+)/g;
        return text.split(rx);
    }

    /**
     * SourceViewer component projection
     */
    var SourceViewer = {
        /** @type {HTMLElement|null} */
        container: null,
        /** @type {boolean} */
        _mounted: false,
        /** @type {(() => void)|null} */
        _unsubscribe: null,
        /** @type {EventListener|null} */
        _contextHandler: null,

        /**
         * Mount component to DOM container
         * @param {HTMLElement|string} target
         */
        mount: function (target) {
            var next = typeof target === 'string' ? document.querySelector(target) : target;
            if (this._mounted && this.container === next) return;
            this.dispose();
            if (typeof target === 'string') {
                this.container = /** @type {HTMLElement|null} */ (document.querySelector(target));
            } else {
                this.container = target;
            }
            if (this.container) {
                this._mounted = true;
                this.bindEvents();
                var self = this;
                this._unsubscribe = window.AppStore.subscribe(function (state, action) {
                    if (action && action.type === 'UPDATE_DELTAS') self.project(state, action);
                });
            }
        },

        /**
         * @param {SessionState} state
         * @param {{payload?: import('../types.js').UpdatePayload}|null} action
         */
        project: function (state, action) {
            var text = state.sourceText;
            if (text === null || text === undefined || !this.container) return;
            var pending = this.container.querySelector('[data-pending="true"]');
            var payload = action && action.payload;
            if (payload && payload.data) payload = payload.data;
            if (!pending && !(payload && (payload.source_text || payload.sourceText))) return;
            var probe = document.createElement('div');
            probe.innerHTML = text;
            var clean = /** @param {Node} node */ function (node) { return (node.textContent || '').trim().replace(/\s+/g, ' '); };
            if (!pending && clean(this.container) === clean(probe)) return;
            this.container.innerHTML = text;
            window.tokenizeSource();
            window.rebindTokenSpans();
            window.buildLcIndex();
            window.updateBidirectionalHighlights();
        },

        dispose: function () {
            if (this._unsubscribe) this._unsubscribe();
            this._unsubscribe = null;
            if (this.container && this._contextHandler) this.container.removeEventListener('contextmenu', this._contextHandler);
            this._contextHandler = null;
            this._mounted = false;
        },

        /**
         * Render source text into word spans
         * @param {SessionState} state
         */
        render: function (state) {
            if (!this.container) return;
            var text = state.source_text || '';
            if (!text && this.container.children.length > 0) {
                // If DOM already contains pre-rendered chunks or spans, preserve them
                return;
            }

            var parts = splitTextTokens(text);
            var html = '';
            var wordIdx = 0;
            for (var k = 0; k < parts.length; k++) {
                var part = parts[k];
                if (!part) continue;
                if (k % 2 === 1) {
                    var lc = part.toLowerCase();
                    html += '<span class="word ' + 'highlight-orange" data-word="' + escapeHtml(part) +
                        '" data-word-idx="' + wordIdx +
                        '" data-order="' + wordIdx +
                        '" data-token-order="' + wordIdx +
                        '" data-lower-clean="' + escapeHtml(lc) +
                        '" data-line-idx="0" data-sentence-idx="1">' + escapeHtml(part) + '</span>';
                    wordIdx++;
                } else {
                    html += escapeHtml(part);
                }
            }
            this.container.innerHTML = html;
            var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
            if (w) {
                if (typeof w.rebindTokenSpans === 'function') w.rebindTokenSpans();
                if (typeof w.buildLcIndex === 'function') w.buildLcIndex();
                if (typeof w.wireMvpEvents === 'function') w.wireMvpEvents();
            }
        },

        /**
         * Bind mouse events for hover highlights and RMB triggers
         */
        bindEvents: function () {
            if (!this.container) return;
            var self = this;

            this._contextHandler = /** @param {Event} e */ function (e) {
                var target = /** @type {HTMLElement|null} */ (e.target);
                var span = target && target.closest ? target.closest('#source-container span.word') : null;
                if (span) {
                    var w = /** @type {any} */ (window);
                    if (w.__deskHandleMouseUp) {
                        e.preventDefault();
                        return;
                    }
                    var uiCfg = (w.AppState && w.AppState.bootstrap && w.AppState.bootstrap.ui_config) || w.__CONFIG__ || {};
                    if (uiCfg.audio_rmb_play) {
                        e.preventDefault();
                        var text = span.getAttribute('data-word') || span.textContent || '';
                        var lang = (typeof w.getSessionLang === 'function') ? w.getSessionLang() : (w.AppState && w.AppState.language) || 'de';
                        if (typeof w.playAudio === 'function') {
                            w.playAudio(text, lang);
                        }
                    }
                }
            };
            this.container.addEventListener('contextmenu', this._contextHandler);
        }
    };

    // Expose on global window object
    var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.SourceViewer = SourceViewer;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SourceViewer;
    }
})();
