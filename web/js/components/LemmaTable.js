
/**
 * @file LemmaTable.js
 * Shared domain shapes are documented once in ../types.js.
 * Autonomous JavaScript client component for rendering vocabulary table rows,
 * handling inline double-click cell editing, row selection, and audio triggers.
 */

(function () {
    // Adapt the supported matrix entry point to the canonical workspace row owner.
    function legacyMatrixWords(state) {
        if (!state || !Array.isArray(state.data_rows)) return null;
        var headers = state.headers || [];
        if (headers.indexOf('WordSource') < 0 || headers.indexOf('WordDestination') < 0) {
            throw new Error('Legacy table rows require WordSource and WordDestination headers');
        }
        return state.data_rows.map(function(row, index) { return legacyMatrixWord(headers, row, index); });
    }

    function legacyMatrixWord(headers, row, index) {
        var fields = {WordSourceInflectedForm: 'inflected', WordSource: 'lemma', WordDestination: 'translation',
            WordSourceIPA: 'ipa', WordSourceMorphologyAI: 'morphology', WordSourcePOS: 'pos', WordSourceGender: 'gender'};
        var word = {row_id: index, token_order: index, sentence_idx: 1};
        headers.forEach(function(header, column) {
            if (fields[header]) word[fields[header]] = row[column] || '';
            if (header === 'TokenOrder') word.token_order = Number(row[column]);
            if (header === 'SentenceSourceIndex') word.sentence_idx = Number(row[column]);
        });
        if (word.translation === '[FAILED]' || String(word.translation).indexOf('[Error') === 0) word.translation = '';
        return word;
    }

    function legacySelectionIds(state) {
        return (state && (state.selectedRowIds || state.selected_ids)) || undefined;
    }

    function legacySelectionMap(state) {
        return (state && state.selectedLemmas) || (window.AppState && window.AppState.selectedLemmas) || {};
    }

    /**
     * LemmaTable component projection
     */
    var LemmaTable = {

        table: null,

        tbody: null,

        _mounted: false,

        _contextHandler: null,

        /**
         * Mount component to DOM container
         * @param {HTMLTableElement|string} target
         */
        mount: function (target) {
            var next = typeof target === 'string' ? document.querySelector(target) : target;
            if (this._mounted && this.table === next) return;
            this.dispose();
            if (typeof target === 'string') {
                this.table = (document.querySelector(target));
            } else {
                this.table = target;
            }
            if (this.table) {
                this._mounted = true;
                this.tbody = this.table.querySelector('tbody');
                this.bindEvents();
            }
        },

        dispose: function () {
            if (this.table && this._contextHandler) this.table.removeEventListener('contextmenu', this._contextHandler);
            this._contextHandler = null;
            this._mounted = false;
        },

        /**
         * Render table rows from session state or card vocabulary items
         * @param {SessionState} state
         * @param {Array<VocabularyRow>} [words]
         */
        render: function (state, words) {
            if (!this.table) return;
            this.tbody = this.table.querySelector('tbody');
            if (!this.tbody) return;
            var rows = words || legacyMatrixWords(state);
            if (!rows) return;
            window.WorkspaceTabs.bindCardWordsToTbody(this.tbody, rows, legacySelectionMap(state), legacySelectionIds(state));
        },

        /**
         * Backward-compatibility alias for render
         * @param {SessionState} state
         * @param {Array<VocabularyRow>} [words]
         */
        renderRows: function (state, words) {
            return this.render(state, words);
        },

        /**
         * Bind table row click, hover, and contextmenu audio events
         */
        bindEvents: function () {
            if (!this.table) return;

            this._contextHandler = /** @param {Event} e */ function (e) {
                var target = (e.target);
                var row = target && target.closest ? target.closest('#lemma-table tbody tr') : null;
                if (row) {
                    var w = (window);
                    if (w.__deskHandleMouseUp) {
                        e.preventDefault();
                        return;
                    }
                    var uiCfg = (w.AppState && w.AppState.bootstrap && w.AppState.bootstrap.ui_config) || w.__CONFIG__ || {};
                    if (uiCfg.audio_rmb_play) {
                        e.preventDefault();
                        var lemCell = row.querySelector('.col-lemma');
                        var transCell = row.querySelector('.col-translation');
                        var isTrans = (uiCfg.audio_rmb_source === 'word_translation');
                        var text = isTrans ? (transCell ? (transCell.textContent || '').trim() : '') : (lemCell ? (lemCell.textContent || '').trim() : '');
                        var lang = isTrans ? (w.AppState && (w.AppState.target_lang || w.AppState.target_language)) || 'ru' : (typeof w.getSessionLang === 'function' ? w.getSessionLang() : (w.AppState && w.AppState.language) || 'de');
                        if (text && typeof w.playAudio === 'function') {
                            w.playAudio(text, lang);
                        }
                    }
                }
            };
            this.table.addEventListener('contextmenu', this._contextHandler);
        }
    };

    // Expose on global window object
    var w = (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.LemmaTable = LemmaTable;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = LemmaTable;
    }
})();
