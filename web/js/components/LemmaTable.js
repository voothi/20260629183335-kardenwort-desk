
/**
 * @file LemmaTable.js
 * Shared domain shapes are documented once in ../types.js.
 * Autonomous JavaScript client component for rendering vocabulary table rows,
 * handling inline double-click cell editing, row selection, and audio triggers.
 */

(function () {
    // Adapt the supported matrix entry point to the canonical workspace row owner.
    function bootUiConfig(w) {
        var b = w.AppState && w.AppState.bootstrap;
        return (b && b.ui_config) || {};
    }

    function resolveRmbConfig(w) {
        var bootCfg = bootUiConfig(w);
        var winCfg = w.__CONFIG__ || {};
        var enabled = (winCfg.audio_rmb_play !== undefined) ? winCfg.audio_rmb_play : bootCfg.audio_rmb_play;
        var rawSource = winCfg.audio_rmb_source || bootCfg.audio_rmb_source || 'word_translation';
        return { enabled: Boolean(enabled), source: String(rawSource).trim().toLowerCase() };
    }

    function isPendingCell(cell) {
        if (!cell) return true;
        if (cell.getAttribute('data-pending') === 'true') return true;
        if (cell.classList && cell.classList.contains('skeleton-loader')) return true;
        return Boolean(cell.querySelector && cell.querySelector('.skeleton-loader, [data-pending="true"]'));
    }

    function isUnfinishedTranslation(cell, raw) {
        if (!raw || raw === '[FAILED]' || raw.indexOf('[Error') === 0) return true;
        if (raw.slice(-3) !== '...') return false;
        return !(cell.hasAttribute('data-provenance') || (cell.querySelector && cell.querySelector('[data-provenance]')));
    }

    function sanitizeSpokenText(text) {
        if (!text) return '';
        var s = String(text);
        s = s.replace(/(^|[^0-9])[0-9]{14}(?![0-9])/g, '$1 ');
        s = s.replace(/[\/\\_:]/g, ' ');
        s = s.replace(/(^|\s)[0-9]+(?=\s|$)/g, ' ');
        return s.replace(/\s+/g, ' ').trim();
    }

    function readCellText(row, selector) {
        var cell = row.querySelector(selector);
        return cell ? (cell.textContent || cell.innerText || '').trim() : '';
    }

    function resolveRowTranslationText(row) {
        var cell = row.querySelector('td.col-translation, td[data-col="WordDestination"], td[data-col="word_translation"]');
        if (isPendingCell(cell)) return '';
        var raw = (cell.textContent || cell.innerText || '').trim();
        return isUnfinishedTranslation(cell, raw) ? '' : sanitizeSpokenText(raw);
    }

    function resolveRowSourceText(row, mode) {
        var lemma = readCellText(row, 'td.col-lemma, td[data-col="WordSource"], td[data-col="lemma"]');
        var inflected = readCellText(row, 'td.col-inflected, td[data-col="WordSourceInflectedForm"], td[data-col="inflected"]');
        var raw = (mode === 'inflection' && inflected) ? inflected : (lemma || inflected);
        return sanitizeSpokenText(raw);
    }

    function readDomText(id) {
        var el = document.getElementById(id);
        return el ? (el.textContent || el.innerText || '').trim() : '';
    }

    function resolveSourceLang(w) {
        if (typeof w.getSessionLang === 'function') return w.getSessionLang();
        var s = w.AppState || {};
        var b = s.bootstrap || {};
        return s.language || b.language || b.lang || readDomText('session-lang');
    }

    function resolveStateTargetLang(s) {
        var direct = s.target_lang || s.target_language;
        if (direct) return direct;
        var b = s.bootstrap || {};
        return b.target_language || b.target_lang || '';
    }

    function resolveTargetLang(w) {
        if (typeof w.getSessionTargetLang === 'function') return w.getSessionTargetLang();
        return resolveStateTargetLang(w.AppState || {}) || readDomText('session-target-lang');
    }

    function resolveRmbAudio(w, row, mode) {
        if (mode === 'word_translation') {
            return { text: resolveRowTranslationText(row), lang: resolveTargetLang(w) };
        }
        return { text: resolveRowSourceText(row, mode), lang: resolveSourceLang(w) };
    }

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
                    var rmbCfg = resolveRmbConfig(w);
                    if (rmbCfg.enabled) {
                        e.preventDefault();
                        var audio = resolveRmbAudio(w, row, rmbCfg.source);
                        if (audio.text && audio.lang && typeof w.playAudio === 'function') {
                            w.playAudio(audio.text, audio.lang);
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
