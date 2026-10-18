// @ts-check

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
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
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

        /**
         * Mount component to DOM container
         * @param {HTMLElement|string} target
         */
        mount: function (target) {
            if (typeof target === 'string') {
                this.container = /** @type {HTMLElement|null} */ (document.querySelector(target));
            } else {
                this.container = target;
            }
            if (this.container) {
                this.bindEvents();
            }
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
            var w = typeof window !== 'undefined' ? window : null;
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

            this.container.addEventListener('mouseover', function (e) {
                var target = /** @type {HTMLElement|null} */ (e.target);
                var span = target && target.closest ? target.closest('#source-container span.word') : null;
                if (span) {
                    var w = /** @type {any} */ (window);
                    if (w.AppState && w.AppState.highlightSameLemma) {
                        var lem = span.getAttribute('data-lower-clean') || span.textContent || '';
                        if (typeof w.highlightSameLemmaPeers === 'function') {
                            w.highlightSameLemmaPeers(lem.trim().toLowerCase());
                        }
                    }
                }
            });

            this.container.addEventListener('mouseout', function (e) {
                var w = /** @type {any} */ (window);
                if (w.AppState && w.AppState.highlightSameLemma) {
                    if (typeof w.clearSameLemmaHighlights === 'function') {
                        w.clearSameLemmaHighlights();
                    }
                }
            });

            this.container.addEventListener('contextmenu', function (e) {
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
            });
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
