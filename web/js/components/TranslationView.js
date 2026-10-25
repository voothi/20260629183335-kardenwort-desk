
/**
 * @file TranslationView.js
 * Autonomous typed vanilla client component for rendering sentence translation,
 * clean word spans, rainbow link connections with invariant padding: 0, and RMB context audio.
 */

/**
 * @typedef {import('../types.js').SessionState} SessionState
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
     * TranslationView component projection
     */
    var TranslationView = {
        /** @type {HTMLElement|null} */
        container: null,
        /** @type {boolean} */
        _mounted: false,
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
            }
        },

        dispose: function () {
            if (this.container && this._contextHandler) this.container.removeEventListener('contextmenu', this._contextHandler);
            this._contextHandler = null;
            this._mounted = false;
        },

        /**
         * Render translation text into token spans
         * @param {SessionState} state
         */
        render: function (state) {
            if (!this.container) return;

            // Preserve active skeleton loaders during progressive streaming
            if (this.container.querySelector('[data-pending="true"]') ||
                this.container.querySelector('.skeleton-loader') ||
                this.container.classList.contains('skeleton-loader')) {
                return;
            }

            var text = state.translated_text || state.sentence_translation || '';
            if (text) {
                // If text does not already contain HTML word spans, wrap words
                if (text.indexOf('<span') === -1) {
                    var parts = splitTextTokens(text);
                    var html = '';
                    var wordIdx = 0;
                    for (var k = 0; k < parts.length; k++) {
                        var part = parts[k];
                        if (!part) continue;
                        if (k % 2 === 1) {
                            var lc = part.toLowerCase();
                            html += '<span class="word hl-mvp" data-word="' + escapeHtml(part) +
                                '" data-word-idx="' + wordIdx +
                                '" data-order="' + wordIdx +
                                '" data-lower-clean="' + escapeHtml(lc) +
                                '" data-line-idx="0" data-sentence-idx="1" style="padding: 0;">' + escapeHtml(part) + '</span>';
                            wordIdx++;
                        } else {
                            html += escapeHtml(part);
                        }
                    }
                    this.container.innerHTML = html;
                } else {
                    this.container.innerHTML = text;
                }
                var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
                if (w && typeof w.wireMvpEvents === 'function') {
                    w.wireMvpEvents();
                }
            }
        },

        /**
         * Bind mouse events for contextmenu audio
         */
        bindEvents: function () {
            if (!this.container) return;
            this._contextHandler = /** @param {Event} e */ function (e) {
                var target = /** @type {HTMLElement|null} */ (e.target);
                var span = target && target.closest ? target.closest('#translation-container span.word') : null;
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
                        var lang = (w.AppState && (w.AppState.target_lang || w.AppState.target_language)) || 'ru';
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
        w.TranslationView = TranslationView;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = TranslationView;
    }
})();
