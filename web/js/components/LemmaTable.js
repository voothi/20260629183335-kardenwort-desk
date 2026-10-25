
/**
 * @file LemmaTable.js
 * Autonomous typed vanilla client component for rendering vocabulary table rows,
 * handling inline double-click cell editing, row selection, and audio triggers.
 */

/**
 * @typedef {import('../types.js').SessionState} SessionState
 * @typedef {import('../types.js').VocabularyRow} VocabularyRow
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
     * LemmaTable component projection
     */
    var LemmaTable = {
        /** @type {HTMLTableElement|null} */
        table: null,
        /** @type {HTMLTableSectionElement|null} */
        tbody: null,
        /** @type {boolean} */
        _mounted: false,
        /** @type {EventListener|null} */
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
                this.table = /** @type {HTMLTableElement|null} */ (document.querySelector(target));
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
            if (!this.tbody) {
                if (this.table) this.tbody = this.table.querySelector('tbody');
                if (!this.tbody) return;
            }

            var w = /** @type {any} */ (window);
            // If WorkspaceTabs is active and manages tbody binding, delegate to avoid desync
            if (w.WorkspaceTabs && typeof w.WorkspaceTabs.bindCardWordsToTbody === 'function' && words) {
                var selMap = (state && state.selectedLemmas) || (w.AppState && w.AppState.selectedLemmas) || {};
                w.WorkspaceTabs.bindCardWordsToTbody(this.tbody, words, selMap);
                return;
            }

            // Otherwise, render rows from state.data_rows if present
            if (state && Array.isArray(state.data_rows) && state.data_rows.length > 0) {
                var headers = state.headers || [];
                var colInf = headers.indexOf('WordSourceInflectedForm');
                var colLemma = headers.indexOf('WordSource');
                var colDest = headers.indexOf('WordDestination');
                var colIpa = headers.indexOf('WordSourceIPA');
                var colMorph = headers.indexOf('WordSourceMorphologyAI');
                var colPos = headers.indexOf('WordSourcePOS');
                var colGen = headers.indexOf('WordSourceGender');

                var frag = document.createDocumentFragment();
                state.data_rows.forEach(function (row, idx) {
                    var tr = document.createElement('tr');
                    tr.setAttribute('data-row-id', String(idx));
                    tr.setAttribute('data-token-order', String(idx));
                    var selIds = (state && (state.selectedRowIds || state.selected_ids)) ||
                                 (w.AppState && (w.AppState.selectedRowIds || w.AppState.selected_ids));
                    var isSel = Boolean(selIds && (selIds.indexOf(idx) !== -1 || selIds.indexOf(String(idx)) !== -1));
                    tr.setAttribute('data-selected', isSel ? '1' : '0');
                    tr.setAttribute('data-filter-hidden', isSel ? '0' : '1');
                    if (isSel) {
                        tr.classList.add('selected', 'kw-row-selected');
                    }

                    /**
                     * @param {string} cls
                     * @param {string} colName
                     * @param {string|Node|any} text
                     * @param {boolean} isEditable
                     */
                    function makeCell(cls, colName, text, isEditable) {
                        var td = document.createElement('td');
                        td.className = (isEditable ? 'editable ' : '') + cls;
                        if (colName) td.setAttribute('data-col', colName);
                        var scrollDiv = document.createElement('div');
                        scrollDiv.className = 'scrollable-cell';
                        var nodeVal = /** @type {any} */ (text);
                        if (nodeVal && typeof nodeVal === 'object' && nodeVal.nodeType) {
                            scrollDiv.appendChild(nodeVal);
                        } else if (typeof text === 'string' && text.indexOf('skeleton-loader') !== -1) {
                            if (text.indexOf('kw-skeleton-cell') === -1) {
                                scrollDiv.innerHTML = text.replace('class="skeleton-loader', 'class="skeleton-loader kw-skeleton-cell');
                            } else {
                                scrollDiv.innerHTML = text;
                            }
                        } else {
                            scrollDiv.textContent = text || '';
                        }
                        td.appendChild(scrollDiv);
                        return td;
                    }

                    var valInf = (colInf !== -1 && row[colInf]) ? row[colInf] : '';
                    var valLem = (colLemma !== -1 && row[colLemma]) ? row[colLemma] : (row[0] || '');
                    var valDest = (colDest !== -1 && row[colDest]) ? row[colDest] : (row[1] || '');
                    if (valDest === '[FAILED]' || (typeof valDest === 'string' && valDest.indexOf('[Error') === 0)) {
                        valDest = '';
                    }
                    var valIpa = (colIpa !== -1 && row[colIpa]) ? row[colIpa] : '';
                    var valMorph = (colMorph !== -1 && row[colMorph]) ? row[colMorph] : '';
                    var valPos = (colPos !== -1 && row[colPos]) ? row[colPos] : '';
                    var valGen = (colGen !== -1 && row[colGen]) ? row[colGen] : '';

                    tr.appendChild(makeCell('col-inflected', 'WordSourceInflectedForm', valInf, true));
                    tr.appendChild(makeCell('col-lemma', 'WordSource', valLem, true));
                    tr.appendChild(makeCell('col-translation', 'WordDestination', valDest, true));
                    tr.appendChild(makeCell('col-ipa', 'WordSourceIPA', valIpa, false));
                    tr.appendChild(makeCell('col-morphology', 'WordSourceMorphologyAI', valMorph, false));
                    tr.appendChild(makeCell('col-pos', 'WordSourcePOS', valPos, false));
                    tr.appendChild(makeCell('col-gender', 'WordSourceGender', valGen, false));

                    frag.appendChild(tr);
                });

                this.tbody.innerHTML = '';
                this.tbody.appendChild(frag);

                if (typeof w.rebindTableRows === 'function') {
                    w.rebindTableRows();
                }
                if (typeof w.updateTableRowsSelectionVisuals === 'function') {
                    w.updateTableRowsSelectionVisuals();
                }
            }
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
                var target = /** @type {HTMLElement|null} */ (e.target);
                var row = target && target.closest ? target.closest('#lemma-table tbody tr') : null;
                if (row) {
                    var w = /** @type {any} */ (window);
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
    var w = /** @type {any} */ (typeof window !== 'undefined' ? window : null);
    if (w) {
        w.LemmaTable = LemmaTable;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = LemmaTable;
    }
})();
