/**
 * web/js/app.js - Kardenwort Desk client-side application logic
 * Handles state hydration from #session-data, SSE stream connection,
 * interactive tagging, and Anki export.
 */

(function () {
    'use strict';

    var SERVER_URL = window.location.origin || '';
    var SESSION_ZID = '';
    var API_TOKEN = '';
    var LANGUAGE = 'de';
    var TARGET_LANG = 'ru';
    var FINGERPRINT = '';
    var SERVER_ENABLED = false;
    var IS_OFFLINE = false;
    var sseSource = null;

    function getSelectedRowIds() {
        var selected = [];
        var seen = {};

        var checkedBoxes = document.querySelectorAll('.kw-tag-checkbox:checked');
        checkedBoxes.forEach(function (cb) {
            var rId = cb.getAttribute('data-row-id');
            if (rId === null || rId === '') {
                rId = cb.getAttribute('data-token-order');
            }
            if (rId !== null && rId !== '') {
                var num = parseInt(rId, 10);
                if (!isNaN(num) && !seen[num]) {
                    seen[num] = true;
                    selected.push(num);
                }
            }
        });

        var selectedRows = document.querySelectorAll('#lemma-table tbody tr.kw-row-selected, #lemma-table tbody tr.selected, #lemma-table tbody tr[data-selected="1"]');
        selectedRows.forEach(function (row) {
            var rId = row.getAttribute('data-row-id');
            if (rId === null || rId === '') {
                rId = row.getAttribute('data-token-order');
            }
            if (rId !== null && rId !== '') {
                var num = parseInt(rId, 10);
                if (!isNaN(num) && !seen[num]) {
                    seen[num] = true;
                    selected.push(num);
                }
            }
        });

        if (selected.length === 0 && window.AppState && Array.isArray(window.AppState.selected_ids)) {
            window.AppState.selected_ids.forEach(function (id) {
                var num = parseInt(id, 10);
                if (!isNaN(num) && !seen[num]) {
                    seen[num] = true;
                    selected.push(num);
                }
            });
        }

        return selected;
    }
    window.getSelectedRowIds = getSelectedRowIds;

    function setStatusMessage(msg, isError) {
        var statusEl = document.getElementById('kw-interactive-status');
        if (statusEl) {
            statusEl.textContent = msg;
            statusEl.style.color = isError ? 'var(--status-error, #f85149)' : 'var(--status-success, #3fb950)';
            statusEl.style.display = msg ? 'inline-block' : 'none';
        }
    }

    function setSseStatus(status) {
        var el = document.getElementById('sse-status');
        if (!el) return;
        el.className = 'sse-status';
        if (status === 'connected') {
            el.classList.add('connected');
            el.title = 'SSE Connected';
        } else if (status === 'error') {
            el.classList.add('error');
            el.title = 'SSE Error / Disconnected';
        } else {
            el.title = 'SSE Offline';
        }
    }

    window.kwToggleTag = function (checkboxEl, rowId) {
        var newStatus = checkboxEl.checked;
        var rowEl = checkboxEl.closest ? checkboxEl.closest('tr') : checkboxEl.parentElement.parentElement;
        if (rowEl) {
            if (newStatus) {
                rowEl.classList.add('kw-row-selected');
            } else {
                rowEl.classList.remove('kw-row-selected');
            }
        }
        setStatusMessage('Saving...', false);

        var payload = {
            session_zid: SESSION_ZID,
            language: LANGUAGE,
            row_id: rowId,
            token_order: rowId,
            status: newStatus,
            fingerprint: FINGERPRINT
        };

        fetch(SERVER_URL + '/api/v1/tag', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Token': API_TOKEN
            },
            body: JSON.stringify(payload)
        })
        .then(function (res) {
            if (res.status === 409) {
                return res.json().then(function (data) {
                    if (data.error_code === 'ROW_STALE') {
                        setStatusMessage('Rows changed, reloading...', true);
                        setTimeout(function () { window.location.reload(); }, 1200);
                    } else if (data.error_code === 'ROW_BUSY') {
                        setStatusMessage('Updating, retrying...', false);
                        setTimeout(function () { window.kwToggleTag(checkboxEl, rowId); }, 1000);
                    } else {
                        setStatusMessage('Conflict: ' + (data.message || 'Stale data'), true);
                        checkboxEl.checked = !newStatus;
                        if (rowEl) {
                            if (!newStatus) rowEl.classList.add('kw-row-selected');
                            else rowEl.classList.remove('kw-row-selected');
                        }
                    }
                });
            }
            if (!res.ok) {
                return res.json().then(function (data) {
                    setStatusMessage(data.message || 'Error updating tag', true);
                    checkboxEl.checked = !newStatus;
                    if (rowEl) {
                        if (!newStatus) rowEl.classList.add('kw-row-selected');
                        else rowEl.classList.remove('kw-row-selected');
                    }
                });
            }
            return res.json().then(function (data) {
                if (data.data && data.data.fingerprint) {
                    FINGERPRINT = data.data.fingerprint;
                }
                setStatusMessage('Saved', false);
                setTimeout(function () { setStatusMessage('', false); }, 1500);
            });
        })
        .catch(function () {
            IS_OFFLINE = true;
            setStatusMessage('Offline (tag failed)', true);
            checkboxEl.checked = !newStatus;
            if (rowEl) {
                if (!newStatus) rowEl.classList.add('kw-row-selected');
                else rowEl.classList.remove('kw-row-selected');
            }
        });
    };

    window.kwExportSelected = function () {
        var selectedIds = getSelectedRowIds();
        var btn = document.getElementById('kw-export-btn');
        if (btn) btn.disabled = true;
        setStatusMessage('Exporting...', false);

        var payload = {
            session_zid: SESSION_ZID,
            language: LANGUAGE,
            selected_row_ids: selectedIds,
            fingerprint: FINGERPRINT
        };

        fetch(SERVER_URL + '/api/v1/export', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Token': API_TOKEN
            },
            body: JSON.stringify(payload)
        })
        .then(function (res) {
            if (btn) btn.disabled = false;
            if (!res.ok) {
                return res.json().then(function (data) {
                    setStatusMessage('Export failed: ' + (data.message || res.statusText), true);
                });
            }
            return res.json().then(function () {
                setStatusMessage('Export started!', false);
                setTimeout(function () { setStatusMessage('', false); }, 2500);
            });
        })
        .catch(function () {
            if (btn) btn.disabled = false;
            setStatusMessage('Offline (export failed)', true);
        });
    };

    function initSSE(sessionZid) {
        if (!sessionZid || typeof window.EventSource === 'undefined') return;
        try {
            if (sseSource) {
                sseSource.close();
            }
            sseSource = new EventSource('/events?zid=' + encodeURIComponent(sessionZid));
            sseSource.onopen = function () {
                setSseStatus('connected');
            };
            sseSource.onerror = function () {
                setSseStatus('error');
            };
            sseSource.onmessage = function (e) {
                try {
                    var data = JSON.parse(e.data);
                    if (data.type === 'tag_updated') {
                        var cb = document.querySelector('.kw-tag-checkbox[data-token-order="' + data.row_id + '"]');
                        if (cb && cb.checked !== data.status) {
                            cb.checked = data.status;
                            var r = cb.closest ? cb.closest('tr') : cb.parentElement.parentElement;
                            if (r) {
                                if (data.status) r.classList.add('kw-row-selected');
                                else r.classList.remove('kw-row-selected');
                            }
                        }
                    }
                } catch (err) {
                    // Ignore ping or non-json SSE comments
                }
            };
        } catch (e) {
            setSseStatus('error');
        }
    }

    function hydrateSession() {
        var bootstrapEl = document.getElementById('kardenwort-bootstrap');
        var dataEl = document.getElementById('session-data');

        var rawBootstrap = bootstrapEl ? (bootstrapEl.textContent || '').trim() : '';
        var rawSession = dataEl ? (dataEl.textContent || '').trim() : '';

        var bootstrapPayload = null;
        if (rawBootstrap && rawBootstrap !== '{}') {
            try {
                bootstrapPayload = JSON.parse(rawBootstrap);
            } catch (err) {
                console.error('Error parsing #kardenwort-bootstrap JSON:', err);
            }
        }

        var sessionPayload = null;
        if (rawSession && rawSession !== '{}') {
            try {
                sessionPayload = JSON.parse(rawSession);
            } catch (err) {
                console.error('Error parsing #session-data JSON:', err);
            }
        }

        if (!bootstrapPayload && !sessionPayload) {
            return;
        }

        var session = (bootstrapPayload && (bootstrapPayload.session_data || bootstrapPayload)) || sessionPayload || {};

        try {
            // Unpack Unified Bootstrap State into window globals & AppState
            if (bootstrapPayload) {
                window.__CONFIG__ = bootstrapPayload.ui_config || window.__CONFIG__ || {};
                window.AppState = window.AppState || {};
                window.AppState.config = window.__CONFIG__;
                window.AppState.bootstrap = bootstrapPayload;
                if (bootstrapPayload.sentence_cards) {
                    window.AppState.sentenceCards = bootstrapPayload.sentence_cards;
                    window.AppState.sentence_cards = bootstrapPayload.sentence_cards;
                    window.sentence_cards = bootstrapPayload.sentence_cards;
                }
                if (bootstrapPayload.delivery_mode) {
                    window.AppState.deliveryMode = bootstrapPayload.delivery_mode;
                }
            } else if (session) {
                window.__CONFIG__ = window.__CONFIG__ || {};
                window.AppState = window.AppState || {};
                window.AppState.config = window.__CONFIG__;
            }

            SESSION_ZID = session.session_zid || session.zid || '';
            API_TOKEN = session.api_token || '';
            LANGUAGE = session.language || 'de';
            TARGET_LANG = session.target_lang || session.target_language || 'ru';
            FINGERPRINT = session.fingerprint || '';
            SERVER_ENABLED = Boolean(session.server_enabled);
            if (session.server_url) {
                SERVER_URL = session.server_url;
            }

            if (session.sentence_translation || session.translated_text) {
                window.AppState.translatedText = session.sentence_translation || session.translated_text;
                window.AppState.translated_text = window.AppState.translatedText;
            }
            if (session.source_text) {
                window.AppState.sourceText = session.source_text;
                window.AppState.source_text = session.source_text;
            }

            // Hydrate Header
            var titleEl = document.getElementById('session-title');
            if (titleEl && SESSION_ZID) {
                titleEl.textContent = 'Kardenwort Desk - ' + SESSION_ZID;
            }
            var badgeEl = document.getElementById('lang-badge');
            if (badgeEl) {
                badgeEl.textContent = LANGUAGE + ' \u2192 ' + TARGET_LANG;
            }

            // Hydrate Source
            var srcEl = document.getElementById('source-container');
            if (srcEl && !srcEl.children.length && !srcEl.textContent.trim() && session.source_text) {
                srcEl.textContent = session.source_text;
            }

            // Hydrate Translation
            var transEl = document.getElementById('translation-container');
            if (transEl && !transEl.children.length && !transEl.textContent.trim() && session.sentence_translation) {
                transEl.textContent = session.sentence_translation;
            }

            // Hydrate Table
            var tbody = document.querySelector('#lemma-table tbody');
            if (tbody && !tbody.children.length && session.data_rows && session.data_rows.length) {
                var headers = session.headers || [];
                var colInf = headers.indexOf('WordSourceInflectedForm');
                var colLemma = headers.indexOf('WordSource');
                if (colLemma === -1) colLemma = headers.indexOf('lemma');
                var colDest = headers.indexOf('WordDestination');
                if (colDest === -1) colDest = headers.indexOf('word_translation');
                var colIpa = headers.indexOf('WordSourceIPA');
                if (colIpa === -1) colIpa = headers.indexOf('ipa');
                var colMorph = headers.indexOf('WordSourceMorphologyAI');
                if (colMorph === -1) colMorph = headers.indexOf('morphology');
                var colPos = headers.indexOf('WordSourcePOS');
                if (colPos === -1) colPos = headers.indexOf('pos');
                var colGen = headers.indexOf('WordSourceGender');
                if (colGen === -1) colGen = headers.indexOf('gender');
                if (colGen === -1) colGen = headers.indexOf('g');

                session.data_rows.forEach(function (row, idx) {
                    var tr = document.createElement('tr');
                    tr.setAttribute('data-row-id', idx);
                    tr.setAttribute('data-token-order', idx);

                    if (SERVER_ENABLED) {
                        var tdTag = document.createElement('td');
                        tdTag.className = 'kw-tag-control';
                        var cb = document.createElement('input');
                        cb.type = 'checkbox';
                        cb.className = 'kw-tag-checkbox';
                        cb.setAttribute('data-row-id', idx);
                        cb.setAttribute('data-token-order', idx);
                        cb.onchange = function () { window.kwToggleTag(this, idx); };
                        tdTag.appendChild(cb);
                        tr.appendChild(tdTag);
                    }

                    function makeCell(cls, colName, text, isEditable) {
                        var td = document.createElement('td');
                        td.className = (isEditable ? 'editable ' : '') + cls;
                        if (colName) td.setAttribute('data-col', colName);
                        var scrollDiv = document.createElement('div');
                        scrollDiv.className = 'scrollable-cell';
                        scrollDiv.textContent = text || '';
                        td.appendChild(scrollDiv);
                        return td;
                    }

                    var valInf = (colInf !== -1 && row[colInf]) ? row[colInf] : '';
                    var valLem = (colLemma !== -1 && row[colLemma]) ? row[colLemma] : (row[0] || '');
                    var valDest = (colDest !== -1 && row[colDest]) ? row[colDest] : (row[1] || '');
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

                    tbody.appendChild(tr);
                });
            }

            // Synchronize thead tag header with actual checkboxes
            var theadTr = document.querySelector('#lemma-table thead tr');
            if (theadTr) {
                var existingTagTh = theadTr.querySelector('.kw-tag-header');
                var hasCheckboxes = !!document.querySelector('#lemma-table tbody .kw-tag-control, #lemma-table tbody .kw-tag-checkbox');
                var shouldHaveTagHeader = SERVER_ENABLED && hasCheckboxes;
                if (!shouldHaveTagHeader && existingTagTh) {
                    existingTagTh.parentNode.removeChild(existingTagTh);
                } else if (shouldHaveTagHeader && !existingTagTh) {
                    var newTh = document.createElement('th');
                    newTh.className = 'kw-tag-header';
                    newTh.innerHTML = '&#9733;';
                    theadTr.insertBefore(newTh, theadTr.firstChild);
                }
            }

            // Invoke WorkspaceTabs.init() if multi-sentence session data is present (R9)
            var cardList = (bootstrapPayload && bootstrapPayload.sentence_cards) || (sessionPayload && sessionPayload.sentence_cards) || (window.AppState && window.AppState.sentenceCards);
            var cardScript = document.getElementById('sentence-cards');
            var cardCount = (cardList && cardList.length) || (cardScript ? 2 : 0);
            var isMultiMode = (bootstrapPayload && (bootstrapPayload.text_mode === 'multi')) || (sessionPayload && (sessionPayload.text_mode === 'multi'));
            var hasMultiSentence = (cardCount > 1) || isMultiMode;

            if (hasMultiSentence && window.WorkspaceTabs && typeof window.WorkspaceTabs.init === 'function') {
                window.WorkspaceTabs.init();
            }

            if (SERVER_ENABLED && SESSION_ZID) {
                initSSE(SESSION_ZID);
            }
        } catch (e) {
            console.error('Error hydrating session data:', e);
        }
    }

    // WorkspaceTabs lifecycle callback pipeline exposed on window (R38)
    window.clearMVPBookmarks = function () {
        var cleared = false;
        if (window.mvpBookmarks && window.mvpBookmarks.length > 0) {
            cleared = true;
            window.mvpBookmarks = [];
        }
        var pinned = document.querySelectorAll('.hl-mvp-pin, [class*="hl-mvp-pin-"], .hl-mvp-hover, .hl-mvp-active');
        pinned.forEach(function (el) {
            el.classList.remove('hl-mvp-pin', 'hl-mvp-hover', 'hl-mvp-active');
            for (var k = 0; k < 8; k++) {
                el.classList.remove('hl-mvp-pin-' + k);
            }
        });
        if (typeof window.refreshBookmarkClasses === 'function') {
            window.refreshBookmarkClasses();
        }
        return cleared;
    };

    window.refreshBookmarkClasses = function () {
        var allSpans = document.querySelectorAll('#source-container span.word, #translation-container span.word, #translation-container span.hl-mvp');
        allSpans.forEach(function (span) {
            span.classList.remove('hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                span.classList.remove('hl-mvp-pin-' + k);
            }
        });
        var allRows = document.querySelectorAll('#lemma-table tbody tr');
        allRows.forEach(function (row) {
            row.classList.remove('hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                row.classList.remove('hl-mvp-pin-' + k);
            }
        });

        var bookmarks = window.mvpBookmarks || [];
        bookmarks.forEach(function (b) {
            if (b.srcSpan) {
                b.srcSpan.classList.add('hl-mvp-pin');
                if (b.slot !== undefined) b.srcSpan.classList.add('hl-mvp-pin-' + b.slot);
            }
            if (b.transSpan) {
                b.transSpan.classList.add('hl-mvp-pin');
                if (b.slot !== undefined) b.transSpan.classList.add('hl-mvp-pin-' + b.slot);
            }
            if (b.rowId !== undefined) {
                var targetRow = document.querySelector('#lemma-table tbody tr[data-row-id="' + b.rowId + '"]');
                if (targetRow) {
                    targetRow.classList.add('hl-mvp-pin');
                    if (b.slot !== undefined) targetRow.classList.add('hl-mvp-pin-' + b.slot);
                }
            }
        });
    };

    window.updateRowStyles = function () {
        window.AppState = window.AppState || {};
        var selectedLemmas = window.AppState.selectedLemmas || {};
        var selectedIds = window.AppState.selected_ids || [];

        var rows = document.querySelectorAll('#lemma-table tbody tr');
        rows.forEach(function (row) {
            var rId = row.getAttribute('data-row-id');
            var lemCell = row.querySelector('.col-lemma');
            var lem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';
            
            var isSel = (lem && selectedLemmas[lem]) ||
                        (rId !== null && selectedIds.indexOf(rId) !== -1) ||
                        (rId !== null && selectedIds.indexOf(parseInt(rId, 10)) !== -1) ||
                        row.getAttribute('data-selected') === '1' ||
                        row.classList.contains('kw-row-selected');

            if (isSel) {
                row.classList.add('selected', 'kw-row-selected');
                row.setAttribute('data-selected', '1');
            } else {
                row.classList.remove('selected', 'kw-row-selected');
                row.setAttribute('data-selected', '0');
            }
        });

        if (typeof window.updateToolbarState === 'function') {
            window.updateToolbarState();
        }
        if (typeof window.updateEmptySelectionState === 'function') {
            window.updateEmptySelectionState();
        }
    };

    window.rebindTableRows = function () {
        var rows = document.querySelectorAll('#lemma-table tbody tr');
        rows.forEach(function (row) {
            if (row.__kw_bound) return;
            row.__kw_bound = true;

            row.addEventListener('click', function (e) {
                if (e.target && (e.target.tagName === 'INPUT' || e.target.isContentEditable)) return;
                var rId = row.getAttribute('data-row-id');
                var currentlySel = row.classList.contains('kw-row-selected') || row.getAttribute('data-selected') === '1';
                var newSel = !currentlySel;

                if (newSel) {
                    row.classList.add('selected', 'kw-row-selected');
                    row.setAttribute('data-selected', '1');
                } else {
                    row.classList.remove('selected', 'kw-row-selected');
                    row.setAttribute('data-selected', '0');
                }

                var lemCell = row.querySelector('.col-lemma');
                var lem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';
                window.AppState = window.AppState || {};
                window.AppState.selectedLemmas = window.AppState.selectedLemmas || {};
                window.AppState.selected_ids = window.AppState.selected_ids || [];

                if (lem) {
                    if (newSel) window.AppState.selectedLemmas[lem] = true;
                    else delete window.AppState.selectedLemmas[lem];
                }

                if (rId !== null) {
                    var numId = parseInt(rId, 10);
                    var strId = String(rId);
                    if (newSel) {
                        if (window.AppState.selected_ids.indexOf(strId) === -1 && window.AppState.selected_ids.indexOf(numId) === -1) {
                            window.AppState.selected_ids.push(strId);
                        }
                    } else {
                        window.AppState.selected_ids = window.AppState.selected_ids.filter(function (id) {
                            return id !== strId && id !== numId;
                        });
                    }
                }

                if (typeof window.syncSelectionsToCardsAndState === 'function') {
                    window.syncSelectionsToCardsAndState();
                }
                if (typeof window.updateRowStyles === 'function') {
                    window.updateRowStyles();
                }
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                }
            });
        });

        window.updateRowStyles();
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
        if (typeof window.refreshBookmarkClasses === 'function') {
            window.refreshBookmarkClasses();
        }
    };

    function init() {
        hydrateSession();
        if (typeof window.rebindTableRows === 'function') {
            window.rebindTableRows();
        }
        var hasMulti = !!document.getElementById('sentence-cards') || (window.AppState && window.AppState.sentenceCards && window.AppState.sentenceCards.length > 1);
        if (hasMulti && window.WorkspaceTabs && typeof window.WorkspaceTabs.init === 'function') {
            window.WorkspaceTabs.init();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
