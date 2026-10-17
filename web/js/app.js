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
                if (window.__CONFIG__.highlight_same_lemma !== undefined) {
                    window.AppState.highlightSameLemma = Boolean(window.__CONFIG__.highlight_same_lemma);
                }
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
                if (window.__CONFIG__.highlight_same_lemma !== undefined) {
                    window.AppState.highlightSameLemma = Boolean(window.__CONFIG__.highlight_same_lemma);
                }
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

            if (window.AppState && !window.AppState.applyDeltas) {
                window.AppState.applyDeltas = function (data) {
                    if (!data) return;
                    if (data.stage === 'finished') {
                        window.AppState.isFinished = true;
                    }
                    if (data.translatedText || data.translated_text) {
                        var tText = data.translatedText || data.translated_text;
                        window.AppState.translatedText = tText;
                        window.AppState.translated_text = tText;
                        var tc = document.getElementById('translation-container');
                        if (tc) {
                            tc.innerHTML = tText;
                            tokenizeTranslation();
                        }
                    }
                    if (data.sourceText || data.source_text) {
                        var sText = data.sourceText || data.source_text;
                        window.AppState.sourceText = sText;
                        window.AppState.source_text = sText;
                        var sc = document.getElementById('source-container');
                        if (sc) {
                            sc.innerHTML = sText;
                            tokenizeSource();
                        }
                    }
                    if (data.textProvenance || data.text_provenance) {
                        window.AppState.textProvenance = data.textProvenance || data.text_provenance;
                    }
                    buildLcIndex();
                    if (typeof window.updateBidirectionalHighlights === 'function') {
                        window.updateBidirectionalHighlights();
                    }
                };
            }
        } catch (e) {
            console.error('Error hydrating session data:', e);
        }
    }

    // ----------------------------------------------------
    // Translation Tokenization Pipeline & Bidirectional Cross-Highlighting (R15, R30)
    // ----------------------------------------------------

    var sourceSpansArray = [];
    var transSpansArray = [];
    var isMvpIndexBuilt = false;

    window.sourceSpansArray = sourceSpansArray;
    window.transSpansArray = transSpansArray;
    window.mvpBookmarks = window.mvpBookmarks || [];
    window.__selectableTextMode = false;
    window.__persistentSelectableMode = false;

    function tokenizeText(text) {
        var rx = /([a-zA-Z0-9'\u00C0-\u024F\u0400-\u04FF\u0500-\u052F\u1E00-\u1EFF]+)/g;
        return text.split(rx);
    }
    window.tokenizeText = tokenizeText;

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function tokenizeSource() {
        var sc = document.getElementById('source-container');
        if (!sc) return;
        if (sc.querySelector('span.word')) return;
        var raw = (sc.textContent || '').trim();
        if (!raw) return;
        var parts = tokenizeText(raw);
        var html = '';
        var wordIdx = 0;
        for (var k = 0; k < parts.length; k++) {
            var part = parts[k];
            if (!part) continue;
            if (k % 2 === 1) {
                var lc = part.toLowerCase();
                html += '<span class="word highlight-orange" data-word="' + escapeHtml(part) + '" data-word-idx="' + wordIdx + '" data-order="' + wordIdx + '" data-token-order="' + wordIdx + '" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="0" data-sentence-idx="1">' + escapeHtml(part) + '</span>';
                wordIdx++;
            } else {
                html += escapeHtml(part);
            }
        }
        sc.innerHTML = html;
    }
    window.tokenizeSource = tokenizeSource;

    function tokenizeTranslation(text, words) {
        var tc = document.getElementById('translation-container');
        if (!tc) return;
        if (tc.querySelector('[data-pending="true"]') || tc.querySelector('.skeleton-loader') || tc.classList.contains('skeleton-loader')) {
            return;
        }
        if (typeof text === 'string' && text) {
            tc.innerHTML = text;
        }

        var sc = document.getElementById('source-container');
        var maxSourceSent = 1;
        if (sc) {
            var scChunks = sc.querySelectorAll('.kw-sentence-chunk, span.word[data-sentence-idx]');
            for (var scIdx = 0; scIdx < scChunks.length; scIdx++) {
                var sVal = parseInt(scChunks[scIdx].getAttribute('data-sentence-idx'), 10);
                if (!isNaN(sVal) && sVal > maxSourceSent) maxSourceSent = sVal;
            }
        }
        if (maxSourceSent === 1 && window.AppState && window.AppState.sentenceCards && window.AppState.sentenceCards.length > 1) {
            maxSourceSent = window.AppState.sentenceCards.length - 1;
        }
        var isSingleChildTab = (tc.getAttribute('data-sentence-idx') && parseInt(tc.getAttribute('data-sentence-idx'), 10) > 0);
        var hasMultiSent = (!isSingleChildTab && maxSourceSent > 1);

        var divs = tc.getElementsByTagName('div');
        if (divs.length === 0) {
            var tcSentIdx = tc.getAttribute('data-sentence-idx') || '1';
            var tcLineIdx = tc.getAttribute('data-line-idx') || '0';
            var firstChild = tc.firstChild;
            if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                var childSpans = tc.getElementsByTagName('span');
                for (var j = 0; j < childSpans.length; j++) {
                    var span = childSpans[j];
                    if (span.classList && span.classList.contains('word')) {
                        if (!span.classList.contains('hl-mvp')) span.classList.add('hl-mvp');
                        if (!span.getAttribute('data-line-idx')) span.setAttribute('data-line-idx', tcLineIdx);
                        if (!span.getAttribute('data-sentence-idx')) span.setAttribute('data-sentence-idx', tcSentIdx);
                        if (!span.getAttribute('data-word')) span.setAttribute('data-word', span.textContent.trim());
                        if (!span.getAttribute('data-order')) span.setAttribute('data-order', String(j));
                    }
                }
            } else {
                var rawText = tc.textContent || tc.innerText || '';
                var parts = tokenizeText(rawText);
                var html = '';
                var curSent = hasMultiSent ? 1 : (parseInt(tcSentIdx, 10) || 1);
                var wordCounter = 0;
                for (var k = 0; k < parts.length; k++) {
                    var part = parts[k];
                    if (!part) continue;
                    if (k % 2 === 1) {
                        var lc = part.toLowerCase();
                        html += '<span class="word hl-mvp" data-word="' + escapeHtml(part) + '" data-order="' + wordCounter + '" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="' + escapeHtml(tcLineIdx) + '" data-sentence-idx="' + curSent + '">' + escapeHtml(part) + '</span>';
                        wordCounter++;
                    } else {
                        if (hasMultiSent && (/[.!?]/.test(part) || part.indexOf('\n') !== -1 || part.indexOf('\r') !== -1)) {
                            if (curSent < maxSourceSent) curSent++;
                        }
                        html += escapeHtml(part);
                    }
                }
                tc.innerHTML = html;
            }
        } else if (divs.length === 1 && hasMultiSent) {
            var div = divs[0];
            var firstChild = div.firstChild;
            if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                var childSpans = div.getElementsByTagName('span');
                for (var j = 0; j < childSpans.length; j++) {
                    var span = childSpans[j];
                    if (span.classList && span.classList.contains('word')) {
                        if (!span.classList.contains('hl-mvp')) span.classList.add('hl-mvp');
                        if (!span.getAttribute('data-line-idx')) span.setAttribute('data-line-idx', '0');
                        if (!span.getAttribute('data-sentence-idx')) span.setAttribute('data-sentence-idx', '1');
                        if (!span.getAttribute('data-word')) span.setAttribute('data-word', span.textContent.trim());
                        if (!span.getAttribute('data-order')) span.setAttribute('data-order', String(j));
                    }
                }
            } else {
                var rawText = div.textContent || div.innerText || '';
                var parts = tokenizeText(rawText);
                var html = '';
                var curSent = 1;
                var wordCounter = 0;
                for (var k = 0; k < parts.length; k++) {
                    var part = parts[k];
                    if (!part) continue;
                    if (k % 2 === 1) {
                        var lc = part.toLowerCase();
                        html += '<span class="word hl-mvp" data-word="' + escapeHtml(part) + '" data-order="' + wordCounter + '" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="0" data-sentence-idx="' + curSent + '">' + escapeHtml(part) + '</span>';
                        wordCounter++;
                    } else {
                        if (hasMultiSent && (/[.!?]/.test(part) || part.indexOf('\n') !== -1 || part.indexOf('\r') !== -1)) {
                            if (curSent < maxSourceSent) curSent++;
                        }
                        html += escapeHtml(part);
                    }
                }
                div.innerHTML = html;
            }
        } else {
            var globalWordCounter = 0;
            for (var i = 0; i < divs.length; i++) {
                var div = divs[i];
                var divSentIdx = div.getAttribute('data-sentence-idx') || String(i + 1);
                div.setAttribute('data-sentence-idx', divSentIdx);
                div.setAttribute('data-line-idx', String(i));
                var firstChild = div.firstChild;
                if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                    var childSpans = div.getElementsByTagName('span');
                    for (var j = 0; j < childSpans.length; j++) {
                        var span = childSpans[j];
                        if (span.classList && span.classList.contains('word')) {
                            if (!span.classList.contains('hl-mvp')) span.classList.add('hl-mvp');
                            if (!span.getAttribute('data-line-idx')) span.setAttribute('data-line-idx', String(i));
                            if (!span.getAttribute('data-sentence-idx')) span.setAttribute('data-sentence-idx', divSentIdx);
                            if (!span.getAttribute('data-word')) span.setAttribute('data-word', span.textContent.trim());
                            if (!span.getAttribute('data-order')) span.setAttribute('data-order', String(globalWordCounter++));
                        }
                    }
                } else {
                    var rawText = div.textContent || div.innerText || '';
                    var parts = tokenizeText(rawText);
                    var html = '';
                    for (var k = 0; k < parts.length; k++) {
                        var part = parts[k];
                        if (!part) continue;
                        if (k % 2 === 1) {
                            var lc = part.toLowerCase();
                            html += '<span class="word hl-mvp" data-word="' + escapeHtml(part) + '" data-order="' + globalWordCounter + '" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="' + i + '" data-sentence-idx="' + escapeHtml(divSentIdx) + '">' + escapeHtml(part) + '</span>';
                            globalWordCounter++;
                        } else {
                            html += escapeHtml(part);
                        }
                    }
                    div.innerHTML = html;
                }
            }
        }

        // Provenance handling
        var effProv = (window.AppState ? (window.AppState.textProvenance || window.AppState.text_provenance || window.AppState.provenance) : null) || tc.getAttribute('data-provenance');
        if (!effProv) {
            var provScript = document.getElementById('sentence-provenance');
            if (provScript) {
                var sProv = (provScript.textContent || provScript.innerText || '').trim();
                if (sProv) effProv = sProv;
            }
        }
        if (!effProv && window.AppState && window.AppState.translatedText && !window.AppState.textTranslationFailed) {
            var provEl = document.getElementById('text-base-provider');
            var baseProv = provEl ? (provEl.textContent || provEl.innerText || '').trim() : 'google';
            effProv = 'live:' + (baseProv || 'google');
        }
        if (effProv) {
            var pTitle = (typeof window.formatProvenanceTooltip === 'function') ? window.formatProvenanceTooltip(effProv) : ('Translated via ' + effProv);
            if (pTitle) {
                tc.setAttribute('data-provenance', effProv);
                tc.setAttribute('title', pTitle);
                var spans = tc.getElementsByTagName('span');
                for (var sIdx = 0; sIdx < spans.length; sIdx++) {
                    if (spans[sIdx].classList && (spans[sIdx].classList.contains('word') || spans[sIdx].classList.contains('hl-mvp'))) {
                        spans[sIdx].setAttribute('data-provenance', effProv);
                        spans[sIdx].setAttribute('title', pTitle);
                    }
                }
            }
        }

        buildLcIndex();
        if (typeof window.wireMvpEvents === 'function') {
            window.wireMvpEvents();
        }
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
    }
    window.tokenizeTranslation = tokenizeTranslation;

    function buildLcIndex() {
        sourceSpansArray = [];
        transSpansArray = [];
        var sc = document.getElementById('source-container');
        if (sc) {
            var srcSpans = sc.querySelectorAll('span.word');
            if (srcSpans.length > 0) isMvpIndexBuilt = true;
            for (var i = 0; i < srcSpans.length; i++) {
                var span = srcSpans[i];
                var chunk = span.closest ? span.closest('.kw-sentence-chunk') : span.parentElement;
                if (chunk && chunk.classList && chunk.classList.contains('kw-sentence-chunk') && chunk.style && chunk.style.display === 'none') {
                    continue;
                }
                span.setAttribute('data-mvp-idx', String(sourceSpansArray.length));
                span.setAttribute('data-mvp-type', 'source');
                var sentIdx = span.getAttribute('data-sentence-idx');
                if (!sentIdx && chunk && chunk.getAttribute) {
                    sentIdx = chunk.getAttribute('data-sentence-idx');
                    if (sentIdx) span.setAttribute('data-sentence-idx', sentIdx);
                }
                sourceSpansArray.push(span);
            }
        }
        var tc = document.getElementById('translation-container');
        if (tc) {
            var tcSentIdx = tc.getAttribute('data-sentence-idx');
            var transSpans = tc.querySelectorAll('span.word, span.hl-mvp');
            for (var j = 0; j < transSpans.length; j++) {
                var span = transSpans[j];
                var chunk = span.closest ? span.closest('div') : span.parentElement;
                if (chunk && chunk.style && chunk.style.display === 'none') {
                    continue;
                }
                span.setAttribute('data-mvp-idx', String(transSpansArray.length));
                span.setAttribute('data-mvp-type', 'trans');
                var spanSentIdx = span.getAttribute('data-sentence-idx');
                if (!spanSentIdx) {
                    if (chunk && chunk.getAttribute && chunk.getAttribute('data-sentence-idx')) {
                        spanSentIdx = chunk.getAttribute('data-sentence-idx');
                    } else if (tcSentIdx) {
                        spanSentIdx = tcSentIdx;
                    }
                    if (spanSentIdx) {
                        span.setAttribute('data-sentence-idx', spanSentIdx);
                    }
                }
                transSpansArray.push(span);
            }
        }
        window.sourceSpansArray = sourceSpansArray;
        window.transSpansArray = transSpansArray;
    }
    window.buildLcIndex = buildLcIndex;

    function getTargetIdx(idx, isSource) {
        var sourceArray = isSource ? sourceSpansArray : transSpansArray;
        var targetArray = isSource ? transSpansArray : sourceSpansArray;
        if (!targetArray || targetArray.length === 0) return -1;
        var span = sourceArray[idx];
        if (!span) return -1;

        var spanSentIdx = span.getAttribute('data-sentence-idx');
        var spanLineIdx = span.getAttribute('data-line-idx') || '0';

        // Tier 1: Sentence Scope resolution
        var sourceSentPool = [];
        var targetSentPool = [];

        if (spanSentIdx) {
            for (var s = 0; s < sourceArray.length; s++) {
                if (sourceArray[s].getAttribute('data-sentence-idx') === spanSentIdx) {
                    sourceSentPool.push(s);
                }
            }
            for (var t = 0; t < targetArray.length; t++) {
                if (targetArray[t].getAttribute('data-sentence-idx') === spanSentIdx) {
                    targetSentPool.push(t);
                }
            }
        }

        if (targetSentPool.length === 0) {
            sourceSentPool = [];
            for (var s2 = 0; s2 < sourceArray.length; s2++) {
                sourceSentPool.push(s2);
            }
            targetSentPool = [];
            for (var t2 = 0; t2 < targetArray.length; t2++) {
                targetSentPool.push(t2);
            }
        }

        // Tier 2: Line / Cue Scope resolution
        var distinctSourceLines = {};
        for (var i = 0; i < sourceSentPool.length; i++) {
            var l = sourceArray[sourceSentPool[i]].getAttribute('data-line-idx') || '0';
            distinctSourceLines[l] = true;
        }
        var distinctTargetLines = {};
        for (var j = 0; j < targetSentPool.length; j++) {
            var l2 = targetArray[targetSentPool[j]].getAttribute('data-line-idx') || '0';
            distinctTargetLines[l2] = true;
        }

        var sourcePool = sourceSentPool;
        var targetPool = targetSentPool;

        var sourceLineCount = Object.keys(distinctSourceLines).length;
        var targetLineCount = Object.keys(distinctTargetLines).length;

        if (sourceLineCount > 1 && targetLineCount > 1) {
            var subSourceLinePool = [];
            var subTargetLinePool = [];
            for (var si = 0; si < sourceSentPool.length; si++) {
                var sIdx = sourceSentPool[si];
                if ((sourceArray[sIdx].getAttribute('data-line-idx') || '0') === spanLineIdx) {
                    subSourceLinePool.push(sIdx);
                }
            }
            for (var ti = 0; ti < targetSentPool.length; ti++) {
                var tIdx = targetSentPool[ti];
                if ((targetArray[tIdx].getAttribute('data-line-idx') || '0') === spanLineIdx) {
                    subTargetLinePool.push(tIdx);
                }
            }
            if (subTargetLinePool.length > 0 && subSourceLinePool.length > 0) {
                sourcePool = subSourceLinePool;
                targetPool = subTargetLinePool;
            }
        }

        // Tier 3: Positional ratio & Defensive fallback
        if (targetPool.length === 0) {
            targetPool = [];
            for (var tf = 0; tf < targetArray.length; tf++) targetPool.push(tf);
            sourcePool = [];
            for (var sf = 0; sf < sourceArray.length; sf++) sourcePool.push(sf);
        }

        if (targetPool.length === 0) return -1;
        if (sourcePool.length <= 1 || targetPool.length <= 1) return targetPool[0];

        var sourcePos = 0;
        for (var k = 0; k < sourcePool.length; k++) {
            if (sourcePool[k] === idx) {
                sourcePos = k;
                break;
            }
        }

        var ratio = sourcePos / (sourcePool.length - 1);
        var targetPos = Math.round(ratio * (targetPool.length - 1));
        if (targetPos < 0) targetPos = 0;
        if (targetPos >= targetPool.length) targetPos = targetPool.length - 1;
        return targetPool[targetPos];
    }
    window.getTargetIdx = getTargetIdx;

    function findMatchingTableRows(span, targetSpan, isSource) {
        if (!span) return [];
        var rows = [];

        // 1. Check exact word or lemma match in table rows
        var spanText = (span.getAttribute('data-lower-clean') || span.getAttribute('data-lemma') || span.textContent || '').trim().toLowerCase();
        var allRows = document.querySelectorAll('#lemma-table tbody tr');
        if (spanText) {
            for (var i = 0; i < allRows.length; i++) {
                var tr = allRows[i];
                var lemCell = tr.querySelector('.col-lemma');
                var infCell = tr.querySelector('.col-inflected');
                var transCell = tr.querySelector('.col-translation');

                var lem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';
                var inf = infCell ? (infCell.textContent || '').trim().toLowerCase() : '';
                var trText = transCell ? (transCell.textContent || '').trim().toLowerCase() : '';

                if (isSource) {
                    if ((lem && lem === spanText) || (inf && inf === spanText)) {
                        rows.push(tr);
                    }
                } else {
                    if (trText && (trText === spanText || trText.indexOf(spanText) !== -1)) {
                        rows.push(tr);
                    }
                }
            }
        }
        if (rows.length > 0) return rows;

        // 2. Direct attribute match on span
        var rId = span.getAttribute('data-row-id');
        var tOrd = span.getAttribute('data-token-order');

        if (rId !== null && rId !== '') {
            var r = document.querySelector('#lemma-table tbody tr[data-row-id="' + rId + '"]');
            if (r) return [r];
        }
        if (tOrd !== null && tOrd !== '') {
            var r = document.querySelector('#lemma-table tbody tr[data-token-order="' + tOrd + '"]');
            if (r) return [r];
        }

        // 3. Match via targetSpan in opposite container
        if (targetSpan) {
            var targetText = (targetSpan.getAttribute('data-lower-clean') || targetSpan.getAttribute('data-lemma') || targetSpan.textContent || '').trim().toLowerCase();
            if (targetText) {
                for (var j = 0; j < allRows.length; j++) {
                    var tr2 = allRows[j];
                    var lemCell2 = tr2.querySelector('.col-lemma');
                    var infCell2 = tr2.querySelector('.col-inflected');
                    var transCell2 = tr2.querySelector('.col-translation');

                    var lem2 = lemCell2 ? (lemCell2.textContent || '').trim().toLowerCase() : '';
                    var inf2 = infCell2 ? (infCell2.textContent || '').trim().toLowerCase() : '';
                    var trText2 = transCell2 ? (transCell2.textContent || '').trim().toLowerCase() : '';

                    if (!isSource) {
                        if ((lem2 && lem2 === targetText) || (inf2 && inf2 === targetText)) {
                            rows.push(tr2);
                        }
                    } else {
                        if (trText2 && (trText2 === targetText || trText2.indexOf(targetText) !== -1)) {
                            rows.push(tr2);
                        }
                    }
                }
            }
            if (rows.length > 0) return rows;

            var trRId = targetSpan.getAttribute('data-row-id');
            var trTOrd = targetSpan.getAttribute('data-token-order');
            if (trRId !== null && trRId !== '') {
                var r = document.querySelector('#lemma-table tbody tr[data-row-id="' + trRId + '"]');
                if (r) return [r];
            }
            if (trTOrd !== null && trTOrd !== '') {
                var r = document.querySelector('#lemma-table tbody tr[data-token-order="' + trTOrd + '"]');
                if (r) return [r];
            }
        }

        return rows;
    }

    function getLemmaForSpan(span, matchingRows) {
        if (matchingRows && matchingRows.length > 0) {
            var lemCell = matchingRows[0].querySelector('.col-lemma');
            if (lemCell && lemCell.textContent.trim()) {
                return lemCell.textContent.trim();
            }
        }
        if (span) {
            var l = span.getAttribute('data-lemma');
            if (l) return l.trim();
            var lc = span.getAttribute('data-lower-clean');
            if (lc) return lc.trim();
            return (span.textContent || '').trim();
        }
        return '';
    }

    function highlightSameLemmaPeers(lemNorm) {
        if (!lemNorm) return;
        var sourceSpans = document.querySelectorAll('#source-container span.word');
        sourceSpans.forEach(function (s) {
            var sLem = (s.getAttribute('data-lemma') || s.getAttribute('data-lower-clean') || s.textContent || '').trim().toLowerCase();
            if (sLem === lemNorm) {
                s.classList.add('lemma-peer-highlight');
            }
        });

        var rows = document.querySelectorAll('#lemma-table tbody tr');
        rows.forEach(function (r) {
            var lemCell = r.querySelector('.col-lemma');
            var rLem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';
            if (rLem === lemNorm) {
                r.classList.add('lemma-peer-highlight');
            }
        });
    }

    function selectTableRow(row) {
        if (!row) return;
        row.classList.add('selected', 'kw-row-selected');
        row.setAttribute('data-selected', '1');
        var cb = row.querySelector('.kw-tag-checkbox');
        if (cb) cb.checked = true;

        var rId = row.getAttribute('data-row-id');
        var lemCell = row.querySelector('.col-lemma');
        var lem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';

        window.AppState = window.AppState || {};
        window.AppState.selected_ids = window.AppState.selected_ids || [];
        window.AppState.selectedLemmas = window.AppState.selectedLemmas || {};

        if (lem) window.AppState.selectedLemmas[lem] = true;
        if (rId !== null) {
            var strId = String(rId);
            var numId = parseInt(rId, 10);
            if (window.AppState.selected_ids.indexOf(strId) === -1 && window.AppState.selected_ids.indexOf(numId) === -1) {
                window.AppState.selected_ids.push(numId);
            }
        }
    }

    function deselectTableRow(row) {
        if (!row) return;
        row.classList.remove('selected', 'kw-row-selected');
        row.setAttribute('data-selected', '0');
        var cb = row.querySelector('.kw-tag-checkbox');
        if (cb) cb.checked = false;

        var rId = row.getAttribute('data-row-id');
        var lemCell = row.querySelector('.col-lemma');
        var lem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';

        window.AppState = window.AppState || {};
        window.AppState.selected_ids = window.AppState.selected_ids || [];
        window.AppState.selectedLemmas = window.AppState.selectedLemmas || {};

        if (lem) delete window.AppState.selectedLemmas[lem];
        if (rId !== null) {
            var strId = String(rId);
            var numId = parseInt(rId, 10);
            window.AppState.selected_ids = window.AppState.selected_ids.filter(function (id) {
                return id !== strId && id !== numId;
            });
        }
    }

    function handleTokenMouseOver(e) {
        if (window.__selectableTextMode || document.body.classList.contains('text-selection-mode-active')) return;
        var span = e.target && e.target.closest ? e.target.closest('span.word, span.hl-mvp') : null;
        if (!span) return;

        if (!isMvpIndexBuilt || sourceSpansArray.length === 0 || transSpansArray.length === 0) {
            buildLcIndex();
        }

        span.classList.add('hl-mvp-hover');

        var idxStr = span.getAttribute('data-mvp-idx');
        var isSource = (span.getAttribute('data-mvp-type') === 'source') || (span.closest('#source-container') !== null);
        var targetSpan = null;
        if (idxStr !== null && idxStr !== '') {
            var idx = parseInt(idxStr, 10);
            var targetIdx = getTargetIdx(idx, isSource);
            targetSpan = isSource ? transSpansArray[targetIdx] : sourceSpansArray[targetIdx];
            if (targetSpan) {
                targetSpan.classList.add('hl-mvp-hover');
            }
        }

        var matchingRows = findMatchingTableRows(span, targetSpan, isSource);
        matchingRows.forEach(function (tr) {
            tr.classList.add('hl-mvp-hover');
        });

        var isSameLemma = !!(window.AppState && (window.AppState.highlightSameLemma || (window.AppState.config && window.AppState.config.highlight_same_lemma)));
        if (isSameLemma) {
            var lemma = getLemmaForSpan(span, matchingRows);
            if (lemma) {
                var lemNorm = lemma.trim().toLowerCase();
                highlightSameLemmaPeers(lemNorm);
            }
        }
    }

    function handleTokenMouseOut(e) {
        if (window.__selectableTextMode || document.body.classList.contains('text-selection-mode-active')) return;
        var span = e.target && e.target.closest ? e.target.closest('span.word, span.hl-mvp') : null;
        if (!span) return;

        var hoveredSpans = document.querySelectorAll('#source-container .hl-mvp-hover, #translation-container .hl-mvp-hover');
        hoveredSpans.forEach(function (el) {
            el.classList.remove('hl-mvp-hover');
        });

        var hoveredRows = document.querySelectorAll('#lemma-table tbody tr.hl-mvp-hover');
        hoveredRows.forEach(function (tr) {
            tr.classList.remove('hl-mvp-hover');
        });

        var peerSpans = document.querySelectorAll('.lemma-peer-highlight');
        peerSpans.forEach(function (el) {
            el.classList.remove('lemma-peer-highlight');
        });
    }

    function handleTokenClick(e) {
        if (window.__selectableTextMode || document.body.classList.contains('text-selection-mode-active')) return;
        var span = e.target && e.target.closest ? e.target.closest('span.word, span.hl-mvp') : null;
        if (!span) return;

        if (!isMvpIndexBuilt || sourceSpansArray.length === 0 || transSpansArray.length === 0) {
            buildLcIndex();
        }

        var isSource = (span.getAttribute('data-mvp-type') === 'source') || (span.closest('#source-container') !== null);
        var idxStr = span.getAttribute('data-mvp-idx');
        var idx = (idxStr !== null && idxStr !== '') ? parseInt(idxStr, 10) : -1;
        var targetIdx = (idx !== -1) ? getTargetIdx(idx, isSource) : -1;
        var srcSpan = isSource ? span : (targetIdx !== -1 ? sourceSpansArray[targetIdx] : null);
        var transSpan = isSource ? (targetIdx !== -1 ? transSpansArray[targetIdx] : null) : span;

        window.mvpBookmarks = window.mvpBookmarks || [];

        var existingBIdx = -1;
        for (var i = 0; i < window.mvpBookmarks.length; i++) {
            var b = window.mvpBookmarks[i];
            if (b.srcSpan === span || b.transSpan === span || (srcSpan && b.srcSpan === srcSpan) || (transSpan && b.transSpan === transSpan)) {
                existingBIdx = i;
                break;
            }
        }

        var matchingRows = findMatchingTableRows(span, isSource ? transSpan : srcSpan, isSource);

        if (existingBIdx !== -1) {
            window.mvpBookmarks.splice(existingBIdx, 1);
            if (isSource) {
                matchingRows.forEach(function (row) {
                    deselectTableRow(row);
                });
            }
            window.refreshBookmarkClasses();
            window.updateRowStyles();
            if (typeof window.updateBidirectionalHighlights === 'function') {
                window.updateBidirectionalHighlights();
            }
            return;
        }

        var isAlreadySelectedInTable = false;
        for (var r = 0; r < matchingRows.length; r++) {
            var tr = matchingRows[r];
            if (tr.classList.contains('kw-row-selected') || tr.classList.contains('selected') || tr.getAttribute('data-selected') === '1') {
                isAlreadySelectedInTable = true;
                break;
            }
        }

        if (isSource && isAlreadySelectedInTable) {
            matchingRows.forEach(function (row) {
                deselectTableRow(row);
            });
            window.refreshBookmarkClasses();
            window.updateRowStyles();
            if (typeof window.updateBidirectionalHighlights === 'function') {
                window.updateBidirectionalHighlights();
            }
            return;
        }

        var mvpScript = document.getElementById('hl-mvp-script');
        var mvpN = 3;
        var mvpRainbow = true;
        if (mvpScript) {
            var bAttr = mvpScript.getAttribute('data-bookmarks');
            if (bAttr) mvpN = parseInt(bAttr, 10) || 3;
            var rAttr = mvpScript.getAttribute('data-rainbow');
            if (rAttr !== null) mvpRainbow = (rAttr === '1' || rAttr === 'true');
        } else if (window.AppState && window.AppState.config) {
            if (window.AppState.config.hover_highlight_bookmarks !== undefined) {
                mvpN = parseInt(window.AppState.config.hover_highlight_bookmarks, 10) || 3;
            }
            if (window.AppState.config.hover_highlight_rainbow !== undefined) {
                mvpRainbow = Boolean(window.AppState.config.hover_highlight_rainbow);
            }
        }

        while (window.mvpBookmarks.length >= mvpN) {
            window.mvpBookmarks.shift();
        }

        var slot = 0;
        if (mvpRainbow) {
            var usedSlots = {};
            for (var k = 0; k < window.mvpBookmarks.length; k++) {
                if (window.mvpBookmarks[k].slot !== undefined) {
                    usedSlots[window.mvpBookmarks[k].slot] = true;
                }
            }
            for (var s = 0; s < Math.max(mvpN, 8); s++) {
                if (!usedSlots[s]) {
                    slot = s;
                    break;
                }
            }
        }

        var primaryRowId = (matchingRows.length > 0) ? matchingRows[0].getAttribute('data-row-id') : undefined;

        window.mvpBookmarks.push({
            idx: (isSource ? 's' : 't') + (idx !== -1 ? idx : window.mvpBookmarks.length),
            srcSpan: srcSpan,
            transSpan: transSpan,
            rowId: primaryRowId,
            slot: slot
        });

        if (isSource) {
            matchingRows.forEach(function (row) {
                selectTableRow(row);
            });
        }

        window.refreshBookmarkClasses();
        window.updateRowStyles();
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
    }

    function initTokenDelegation() {
        var sc = document.getElementById('source-container');
        var tc = document.getElementById('translation-container');
        if (sc && !sc.__kw_delegated) {
            sc.__kw_delegated = true;
            sc.addEventListener('mouseover', handleTokenMouseOver);
            sc.addEventListener('mouseout', handleTokenMouseOut);
            sc.addEventListener('click', handleTokenClick);
        }
        if (tc && !tc.__kw_delegated) {
            tc.__kw_delegated = true;
            tc.addEventListener('mouseover', handleTokenMouseOver);
            tc.addEventListener('mouseout', handleTokenMouseOut);
            tc.addEventListener('click', handleTokenClick);
        }
    }
    window.initTokenDelegation = initTokenDelegation;
    window.wireMvpEvents = function () {
        initTokenDelegation();
        window.__mvpInitialized = true;
    };

    window.isTokenPinnedInMVP = function (span) {
        if (!span || !window.mvpBookmarks || window.mvpBookmarks.length === 0) return false;
        for (var i = 0; i < window.mvpBookmarks.length; i++) {
            if (window.mvpBookmarks[i].srcSpan === span || window.mvpBookmarks[i].transSpan === span) return true;
        }
        return false;
    };

    window.pinTokenToMVP = function (span) {
        if (!span) return false;
        handleTokenClick({ target: span });
        return true;
    };

    window.unpinTokenFromMVP = function (span) {
        if (!span || !window.mvpBookmarks || window.mvpBookmarks.length === 0) return false;
        for (var i = 0; i < window.mvpBookmarks.length; i++) {
            if (window.mvpBookmarks[i].srcSpan === span || window.mvpBookmarks[i].transSpan === span) {
                window.mvpBookmarks.splice(i, 1);
                window.refreshBookmarkClasses();
                window.updateRowStyles();
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                }
                return true;
            }
        }
        return false;
    };

    function setSelectableTextMode(enabled, persistent) {
        window.__selectableTextMode = Boolean(enabled);
        if (persistent !== undefined) {
            window.__persistentSelectableMode = Boolean(persistent);
        }
        if (enabled) {
            document.body.classList.add('text-selection-mode-active');
        } else {
            document.body.classList.remove('text-selection-mode-active');
        }
    }
    window.setSelectableTextMode = setSelectableTextMode;

    window.addEventListener('keydown', function (e) {
        if (e.key === 'Alt' || e.keyCode === 18) {
            setSelectableTextMode(true);
        }
    });

    window.addEventListener('keyup', function (e) {
        if (e.key === 'Alt' || e.keyCode === 18) {
            if (!window.__persistentSelectableMode) {
                setSelectableTextMode(false);
            }
        }
    });

    window.addEventListener('blur', function () {
        if (!window.__persistentSelectableMode) {
            setSelectableTextMode(false);
        }
    });

    window.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' || e.keyCode === 27) {
            if (document.querySelector('#lemma-table td.editing, #lemma-table input.cell-editor')) {
                return;
            }
            if (window.mvpBookmarks && window.mvpBookmarks.length > 0) {
                window.clearMVPBookmarks();
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                }
            } else {
                if (window.AppState) {
                    window.AppState.selected_ids = [];
                    window.AppState.selectedLemmas = {};
                }
                var selRows = document.querySelectorAll('#lemma-table tbody tr.kw-row-selected, #lemma-table tbody tr.selected');
                selRows.forEach(function (r) {
                    r.classList.remove('selected', 'kw-row-selected');
                    r.setAttribute('data-selected', '0');
                });
                var cbs = document.querySelectorAll('.kw-tag-checkbox:checked');
                cbs.forEach(function (cb) { cb.checked = false; });
                if (typeof window.updateRowStyles === 'function') {
                    window.updateRowStyles();
                }
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                }
            }
        }
    });

    window.getSelectedRowsArray = getSelectedRowIds;
    window.getSelectedRowIds = getSelectedRowIds;

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

    window.updateBidirectionalHighlights = function () {
        var sourceSpans = document.querySelectorAll('#source-container span.word');
        sourceSpans.forEach(function (span) {
            span.classList.remove('highlight-orange-active', 'highlight-purple-active', 'active-subtoken', 'lemma-peer-highlight');
        });

        var selectedIds = (typeof window.getSelectedRowIds === 'function') ? window.getSelectedRowIds() : [];
        var selectedLemmas = (window.AppState && window.AppState.selectedLemmas) ? window.AppState.selectedLemmas : {};
        var isSameLemma = !!(window.AppState && (window.AppState.highlightSameLemma || (window.AppState.config && window.AppState.config.highlight_same_lemma)));

        var activeLemmas = {};

        var rows = document.querySelectorAll('#lemma-table tbody tr');
        rows.forEach(function (row) {
            var rId = row.getAttribute('data-row-id');
            var tOrd = row.getAttribute('data-token-order');
            var lemCell = row.querySelector('.col-lemma');
            var lem = lemCell ? (lemCell.textContent || '').trim().toLowerCase() : '';
            var isSel = row.classList.contains('kw-row-selected') ||
                        row.classList.contains('selected') ||
                        row.getAttribute('data-selected') === '1' ||
                        (rId !== null && (selectedIds.indexOf(parseInt(rId, 10)) !== -1 || selectedIds.indexOf(String(rId)) !== -1)) ||
                        (lem && selectedLemmas[lem]);

            if (isSel) {
                if (lem) activeLemmas[lem] = true;
                sourceSpans.forEach(function (span) {
                    var sRId = span.getAttribute('data-row-id');
                    var sTOrd = span.getAttribute('data-token-order');
                    var sOrd = span.getAttribute('data-order');
                    var sWIdx = span.getAttribute('data-word-idx');
                    var sLem = (span.getAttribute('data-lemma') || span.getAttribute('data-lower-clean') || span.textContent || '').trim().toLowerCase();

                    var matches = (sRId && sRId === rId) ||
                                  (sTOrd && sTOrd === tOrd) ||
                                  (sOrd && (sOrd === tOrd || sOrd === rId)) ||
                                  (sWIdx && (sWIdx === tOrd || sWIdx === rId)) ||
                                  (lem && sLem && lem === sLem);

                    if (matches) {
                        if (span.classList.contains('highlight-purple')) {
                            span.classList.add('highlight-purple-active');
                        } else {
                            span.classList.add('highlight-orange-active');
                        }
                    }
                });
            }
        });

        var bookmarks = window.mvpBookmarks || [];
        bookmarks.forEach(function (b) {
            if (b.srcSpan) {
                if (b.srcSpan.classList.contains('highlight-purple')) {
                    b.srcSpan.classList.add('highlight-purple-active');
                } else {
                    b.srcSpan.classList.add('highlight-orange-active');
                }
            }
        });

        if (isSameLemma) {
            for (var lemKey in activeLemmas) {
                if (activeLemmas.hasOwnProperty(lemKey)) {
                    sourceSpans.forEach(function (span) {
                        var sLem = (span.getAttribute('data-lemma') || span.getAttribute('data-lower-clean') || span.textContent || '').trim().toLowerCase();
                        if (sLem === lemKey && !span.classList.contains('highlight-orange-active') && !span.classList.contains('highlight-purple-active')) {
                            span.classList.add('lemma-peer-highlight');
                        }
                    });
                }
            }
        }

        if (typeof window.refreshBookmarkClasses === 'function') {
            window.refreshBookmarkClasses();
        }
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
        tokenizeSource();
        tokenizeTranslation();
        buildLcIndex();
        initTokenDelegation();
        if (typeof window.rebindTableRows === 'function') {
            window.rebindTableRows();
        }
        var hasMulti = !!document.getElementById('sentence-cards') || (window.AppState && window.AppState.sentenceCards && window.AppState.sentenceCards.length > 1);
        if (hasMulti && window.WorkspaceTabs && typeof window.WorkspaceTabs.init === 'function') {
            window.WorkspaceTabs.init();
        }
        window.__mvpInitialized = true;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
