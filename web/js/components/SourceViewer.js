

/**
 * @file SourceViewer.js
 * Shared domain shapes are documented once in ../types.js.
 * Autonomous JavaScript client component for rendering source text chunks,
 * token spans, hover peer highlights, and RMB context triggers.
 */

(function () {
    function sourcePayload(action) {
        var payload = action && action.payload;
        return payload && payload.data ? payload.data : payload;
    }

    function sourceProjectionRequested(container, payload) {
        return !!(sourceIsPending(container) || (payload && (payload.source_text || payload.sourceText)));
    }

    function sourceIsPending(container) {
        return !!(container.querySelector('[data-pending="true"], .skeleton-loader') || container.classList.contains('skeleton-loader'));
    }

    function cleanSourceText(node) {
        return (node.textContent || '').trim().replace(/\s+/g, ' ');
    }

    /**
     * SourceViewer component projection
     */
    var SourceViewer = {

        container: null,

        _mounted: false,

        _unsubscribe: null,

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
                this.container = (document.querySelector(target));
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
         * @param {{payload?: UpdatePayload}|null} action
         */
        project: function (state, action) {
            var text = state.sourceText;
            if (text === null || text === undefined || !this.container) return;
            if (!sourceProjectionRequested(this.container, sourcePayload(action))) return;
            var probe = document.createElement('div');
            probe.innerHTML = text;
            var pending = sourceIsPending(this.container);
            if (!pending && cleanSourceText(this.container) === cleanSourceText(probe)) return;
            this.container.classList.remove('skeleton-loader');
            this.container.removeAttribute('data-pending');
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
            if (!text && this.container.children.length) return;
            if (typeof window.escapeHtml !== 'function') throw new Error('Required canonical escapeHtml helper is unavailable');
            return this.project({sourceText: window.escapeHtml(text)}, {payload: {sourceText: text}});
        },

        /**
         * Bind mouse events for hover highlights and RMB triggers
         */
        bindEvents: function () {
            if (!this.container) return;
            var self = this;

            this._contextHandler = /** @param {Event} e */ function (e) {
                var target = (e.target);
                var span = target && target.closest ? target.closest('#source-container span.word') : null;
                if (span) {
                    var w = (window);
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
    var w = (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.SourceViewer = SourceViewer;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = SourceViewer;
    }
})();
