
/**
 * @file TranslationView.js
 * Shared domain shapes are documented once in ../types.js.
 * Autonomous JavaScript client component for rendering sentence translation,
 * clean word spans, rainbow link connections with invariant padding: 0, and RMB context audio.
 */

(function () {
    function compatibilityTranslationHtml(text) {
        if (typeof window.escapeHtml !== 'function') throw new Error('Required canonical escapeHtml helper is unavailable');
        return text.indexOf('<span') === -1 ? window.escapeHtml(text) : text;
    }

    function translationContext(container) {
        return container.getAttribute('data-sentence-idx') || '0';
    }

    function translationIsPending(container) {
        return container.querySelector('[data-pending="true"]') || container.querySelector('.skeleton-loader') || container.classList.contains('skeleton-loader');
    }

    /**
     * TranslationView component projection
     */
    var TranslationView = {

        container: null,

        _mounted: false,

        _contextHandler: null,

        _projectionContext: null,

        _projectionHtml: null,

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
            }
        },

        dispose: function () {
            if (this.container && this._contextHandler) this.container.removeEventListener('contextmenu', this._contextHandler);
            this._contextHandler = null;
            this._mounted = false;
            this._projectionContext = null;
            this._projectionHtml = null;
        },

        /** Project canonical translation HTML; callers supply the active sentence context. */
        project: function (html, context) {
            var container = this.container;
            if (!container) return false;
            var probe = document.createElement('div');
            probe.innerHTML = html;
            var pending = translationIsPending(container);
            if (!pending && this._projectionContext === context && this._projectionHtml === html && container.textContent === probe.textContent) return false;
            container.classList.remove('skeleton-loader');
            container.removeAttribute('data-pending');
            container.innerHTML = html;
            this._projectionContext = context;
            this._projectionHtml = html;
            window.tokenizeTranslation();
            return true;
        },

        /**
         * Render translation text into token spans
         * @param {SessionState} state
         */
        render: function (state) {
            if (!this.container) return;
            if (translationIsPending(this.container)) return;
            var text = state.translated_text || state.sentence_translation || '';
            if (!text) return;
            var changed = this.project(compatibilityTranslationHtml(text), translationContext(this.container));
            window.applyTextProvenance(this.container);
            return changed;
        },

        /**
         * Bind mouse events for contextmenu audio
         */
        bindEvents: function () {
            if (!this.container) return;
            this._contextHandler = /** @param {Event} e */ function (e) {
                var target = (e.target);
                var span = target && target.closest ? target.closest('#translation-container span.word') : null;
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
                        var lang = (typeof w.getSessionTargetLang === 'function') ? w.getSessionTargetLang() : (w.AppState && (w.AppState.target_lang || w.AppState.target_language || (w.AppState.bootstrap && (w.AppState.bootstrap.target_language || w.AppState.bootstrap.target_lang)))) || '';
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
        w.TranslationView = TranslationView;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = TranslationView;
    }
})();
