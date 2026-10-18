// @ts-check

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
    window.getSelectedRowsArray = getSelectedRowIds;
    window.getSelectedRows = function () {
        return JSON.stringify(getSelectedRowIds());
    };

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
        // R25, Task 7.9: Guard against local file:/// filesystem URI errors
        if (window.location.protocol === 'file:' && !SERVER_URL) return;

        try {
            // R5, Task 7.5: Active connection guard: only single EventSource per session
            if (window._kwEvtSource) {
                try { window._kwEvtSource.close(); } catch (e) { console.warn('[Kardenwort] Error closing active EventSource:', e); }
                window._kwEvtSource = null;
                window.__sseActiveZid = null;
            }
            if (sseSource) {
                try { sseSource.close(); } catch (e) { console.warn('[Kardenwort] Error closing local EventSource:', e); }
                sseSource = null;
            }

            // R25, Task 7.9: Construct safe URL with SERVER_URL
            var base = (SERVER_URL || '').replace(/\/+$/, '');
            var sseUrl = (base ? base : '') + '/events?zid=' + encodeURIComponent(sessionZid);
            sseSource = new EventSource(sseUrl);
            window._kwEvtSource = sseSource;
            window.__sseActiveZid = sessionZid;

            sseSource.onopen = function () {
                setSseStatus('connected');
            };
            sseSource.onerror = function () {
                setSseStatus('error');
            };

            // R12, Task 7.6: Wire sseSource.onmessage to process progressive updates
            sseSource.onmessage = function (e) {
                try {
                    var data = JSON.parse(e.data);
                    if (!data) return;

                    if (data.type === 'provider_failover') {
                        if (typeof window.updateActiveProvider === 'function') {
                            var tType = data.task_type || data.task;
                            var nProv = data.new_provider || data.to || data.provider;
                            if (nProv) window.updateActiveProvider(tType, nProv);
                        }
                    } else if (data.type === 'tag_updated') {
                        var cb = document.querySelector('.kw-tag-checkbox[data-token-order="' + data.row_id + '"]');
                        if (cb && cb.checked !== data.status) {
                            cb.checked = data.status;
                            var r = cb.closest ? cb.closest('tr') : cb.parentElement.parentElement;
                            if (r) {
                                if (data.status) r.classList.add('kw-row-selected');
                                else r.classList.remove('kw-row-selected');
                            }
                        }
                    } else if (data.type === 'update' || data.stage === 'translated_text' || data.rows || data.stage === 'enrichment' || data.is_finished) {
                        if (typeof window.receiveUpdate === 'function') {
                            window.receiveUpdate(data);
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
    window.initSSE = initSSE;

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
                throw new Error('Malformed #kardenwort-bootstrap JSON: ' + (err.message || String(err)));
            }
        }

        var sessionPayload = null;
        if (rawSession && rawSession !== '{}') {
            try {
                sessionPayload = JSON.parse(rawSession);
            } catch (err) {
                console.error('Error parsing #session-data JSON:', err);
                throw new Error('Malformed #session-data JSON: ' + (err.message || String(err)));
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

            if (window.AppStore && typeof window.AppStore.dispatch === 'function') {
                window.AppStore.dispatch({ type: 'INIT_SESSION', payload: bootstrapPayload || session });
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
    window.hydrateSession = hydrateSession;

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
    window.escapeHtml = escapeHtml;

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

    // ----------------------------------------------------
    // Grammar & Tooltip Helpers (R11, R17)
    // ----------------------------------------------------

    function getGrammarConfig() {
        if (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.grammar_config) {
            return window.AppState.bootstrap.grammar_config;
        }
        if (window.kardenwort_grammar) {
            return window.kardenwort_grammar;
        }
        return {};
    }

    function formatLemmaTooltip(lemma, gender, lang, pos) {
        if (!lemma) return "";
        var cleanLemma = String(lemma).trim();
        if (!cleanLemma) return "";
        var currentLang = (lang || (window.AppConfig ? window.AppConfig.language : 'de') || getSessionLang() || 'de').toLowerCase();
        if (currentLang === 'de') {
            var lowerLemma = cleanLemma.toLowerCase();
            var grammar = getGrammarConfig();
            var articles = (grammar && (grammar.german_articles || grammar.germanArticles)) || [
                "der", "die", "das", "ein", "eine", "einer", "eines", "einem", "einen",
                "des", "dem", "den", "kein", "keine", "keiner", "keines", "keinem", "keinen"
            ];
            for (var i = 0; i < articles.length; i++) {
                var art = articles[i];
                if (lowerLemma === art || lowerLemma.indexOf(art + " ") === 0) {
                    return cleanLemma;
                }
            }

            if (pos) {
                var posStr = String(pos).toLowerCase();
                var posParts = posStr.split(',').map(function (p) {
                    return p.trim().replace(/\.+$/, '');
                }).filter(Boolean);
                var nounTagsList = (grammar && (grammar.noun_tags || grammar.nounTags)) || ["n", "noun", "propn", "subst"];
                var nonNounTagsList = (grammar && (grammar.non_noun_tags || grammar.nonNounTags)) || [
                    "art", "det", "pron", "v", "verb", "aux",
                    "adv", "prep", "adp", "conj", "cconj", "sconj",
                    "intj", "num", "part", "adj"
                ];
                var hasNoun = false;
                var hasNonNoun = false;
                for (var j = 0; j < posParts.length; j++) {
                    if (nounTagsList.indexOf(posParts[j]) !== -1) hasNoun = true;
                    if (nonNounTagsList.indexOf(posParts[j]) !== -1) hasNonNoun = true;
                }
                if (hasNonNoun && !hasNoun) {
                    return cleanLemma;
                }
            } else {
                if (/^[a-zäöüß]/.test(cleanLemma)) {
                    return cleanLemma;
                }
                var pronouns = (grammar && (grammar.german_pronouns || grammar.germanPronouns)) || [
                    "er", "sie", "es", "wir", "ihr", "du", "ich",
                    "mich", "dich", "ihn", "ihm", "ihnen", "uns", "euch",
                    "mir", "dir", "sich", "mein", "dein", "sein", "unser", "euer"
                ];
                if (pronouns.indexOf(lowerLemma) !== -1) {
                    return cleanLemma;
                }
            }

            var g = (gender ? String(gender).trim().replace(/\.+$/, '').toLowerCase() : "");
            if (g === 'm' || g === 'masc' || g === 'masculine' || g === 'der') return 'der ' + cleanLemma;
            if (g === 'f' || g === 'fem' || g === 'feminine' || g === 'die') return 'die ' + cleanLemma;
            if (g === 'n' || g === 'neut' || g === 'neuter' || g === 'das') return 'das ' + cleanLemma;
        }
        return cleanLemma;
    }
    window.formatLemmaTooltip = formatLemmaTooltip;

    function formatPosTooltip(posVal) {
        if (!posVal) return "";
        var pClean = String(posVal).trim();
        if (!pClean) return "";
        if (pClean.indexOf(',') !== -1) {
            var parts = pClean.split(',').map(function (p) { return formatPosTooltip(p.trim()); }).filter(Boolean);
            return parts.join(', ');
        }
        var grammar = getGrammarConfig();
        if (grammar) {
            if (grammar.posMap && grammar.posMap[pClean]) return grammar.posMap[pClean];
            if (grammar.pos_full_name_map && grammar.pos_full_name_map[pClean]) return grammar.pos_full_name_map[pClean];
            var posMap = grammar.posMap || grammar.pos_full_name_map;
            if (posMap) {
                if (posMap[pClean.toUpperCase()]) return posMap[pClean.toUpperCase()];
                if (posMap[pClean.toLowerCase()]) return posMap[pClean.toLowerCase()];
                var pStripped = pClean.replace(/\.+$/, '').toLowerCase();
                if (posMap[pStripped]) return posMap[pStripped];
            }
        }
        return pClean;
    }
    window.formatPosTooltip = formatPosTooltip;

    function formatGenderTooltip(gender, lemma, prov) {
        if (!gender) return "";
        var gClean = String(gender).trim().toLowerCase().replace(/\.+$/, '');
        var gNames = {
            'm': 'Masculine (der)',
            'masc': 'Masculine (der)',
            'masculine': 'Masculine (der)',
            'f': 'Feminine (die)',
            'fem': 'Feminine (die)',
            'feminine': 'Feminine (die)',
            'n': 'Neuter (das)',
            'neut': 'Neuter (das)',
            'neuter': 'Neuter (das)'
        };
        var res = gNames[gClean] || gender;
        if (prov) {
            var pTitle = formatProvenanceTooltip(prov);
            if (pTitle) res += ' (' + pTitle + ')';
        }
        return res;
    }
    window.formatGenderTooltip = formatGenderTooltip;

    function formatProvenanceTooltip(prov) {
        if (!prov) return "";
        var p = String(prov).trim();
        if (p.indexOf('live:') === 0) p = p.substring(5);
        else if (p.indexOf('provider:') === 0) p = p.substring(9);
        var pLower = p.toLowerCase();
        if (pLower === 'argos') return 'Translated via Argos (offline)';
        if (pLower === 'google') return 'Translated via Google';
        if (pLower === 'deepl') return 'Translated via DeepL';
        if (pLower === 'lingva') return 'Translated via Lingva';
        if (pLower === 'intellifiller' || pLower === 'ai') return 'Translated via IntelliFiller (AI)';
        if (pLower === 'corpus:wordfill' || pLower === 'wordfill:corpus') return 'Pre-filled from Corpus (WordFill)';
        if (pLower.indexOf('corpus:wordfill:') === 0) return 'Pre-filled from Corpus (ZID: ' + prov.substring(16) + ')';
        if (pLower === 'cached:sqlite') return 'Loaded from session cache (SQLite)';
        if (pLower === 'cached:file') return 'Loaded from translation file cache';
        if (pLower.indexOf('fallback:') === 0) return 'Translated via fallback (' + prov.substring(9) + ')';
        if (prov.indexOf('live:') === 0 || prov.indexOf('provider:') === 0) return 'Translated via ' + p.charAt(0).toUpperCase() + p.slice(1);
        return prov;
    }
    window.formatProvenanceTooltip = formatProvenanceTooltip;

    function formatTranslationCellTooltip(translation, provenance) {
        var cleanTrans = (translation || '').replace(/<[^>]+>/g, '').trim();
        if (!cleanTrans || cleanTrans.indexOf('skeleton-loader') !== -1) return '';
        var pTitle = formatProvenanceTooltip(provenance);
        if (pTitle) return cleanTrans + ' (' + pTitle + ')';
        return cleanTrans;
    }
    window.formatTranslationCellTooltip = formatTranslationCellTooltip;

    function toUnicodeBold(text) {
        if (!text) return "";
        var res = "";
        for (var i = 0; i < text.length; i++) {
            var code = text.charCodeAt(i);
            if (code >= 65 && code <= 90) {
                res += String.fromCodePoint(0x1D5D4 + (code - 65));
            } else if (code >= 97 && code <= 122) {
                res += String.fromCodePoint(0x1D5EE + (code - 97));
            } else if (code >= 48 && code <= 57) {
                res += String.fromCodePoint(0x1D7EC + (code - 48));
            } else {
                res += text[i];
            }
        }
        return res;
    }
    window.toUnicodeBold = toUnicodeBold;

    function formatInflectedTooltip(sentenceText, inflectedVal, tokenOrder, lemma) {
        if (!sentenceText) return (inflectedVal || "").trim();
        if (!inflectedVal) return sentenceText.trim();
        var targetWords = {};
        var lemmaClean = (lemma && String(lemma).trim()) ? String(lemma).trim().toLowerCase() : null;
        if (lemmaClean) targetWords[lemmaClean] = true;
        var rawComponents = String(inflectedVal || "").split(',');
        for (var c = 0; c < rawComponents.length; c++) {
            var comp = rawComponents[c].trim().toLowerCase();
            if (comp) targetWords[comp] = true;
        }

        var tokens = sentenceText.split(/([a-zA-Z0-9äöüÄÖÜßа-яА-ЯёЁ]+)/);
        for (var k = 0; k < tokens.length; k++) {
            var tok = tokens[k];
            if (/[a-zA-Z0-9äöüÄÖÜßа-яА-ЯёЁ]/.test(tok)) {
                if (targetWords[tok.toLowerCase()]) {
                    tokens[k] = toUnicodeBold(tok);
                }
            }
        }
        var boldSentence = tokens.join('').trim();
        var boldHeader = toUnicodeBold(String(inflectedVal).trim());
        if (boldHeader && boldSentence) {
            return boldHeader + '\n' + boldSentence;
        }
        return boldSentence || boldHeader;
    }
    window.formatInflectedTooltip = formatInflectedTooltip;

    function updateCellTooltip(cell, newValue) {
        if (!cell) return;
        var tr = cell.parentElement;
        var div = cell.querySelector('.scrollable-cell');
        var colName = cell.getAttribute('data-col');
        if (colName === 'WordDestination' || cell.classList.contains('col-translation')) {
            var prov = cell.getAttribute('data-provenance');
            var tip = formatTranslationCellTooltip(newValue, prov);
            if (tip) {
                cell.setAttribute('title', tip);
                if (div) div.setAttribute('title', tip);
            } else {
                cell.removeAttribute('title');
                if (div) div.removeAttribute('title');
            }
        } else if (colName === 'WordSource' || cell.classList.contains('col-lemma')) {
            var curGenCell = tr ? tr.querySelector('td.col-gender, td[data-col="WordSourceGender"], td[data-col="gender"]') : null;
            var curGen = curGenCell ? (curGenCell.textContent || '').trim() : '';
            var curPosCell = tr ? tr.querySelector('td.col-pos, td[data-col="WordSourcePos"], td[data-col="pos"]') : null;
            var curPos = curPosCell ? (curPosCell.textContent || '').trim() : '';
            var activeLang = getSessionLang();
            var tip = formatLemmaTooltip(newValue, curGen, activeLang, curPos);
            if (tip) {
                cell.setAttribute('title', tip);
                if (div) div.setAttribute('title', tip);
            } else {
                cell.removeAttribute('title');
                if (div) div.removeAttribute('title');
            }
        } else if (colName === 'WordSourceInflectedForm' || cell.classList.contains('col-inflected')) {
            var lemCell = tr ? tr.querySelector('td.col-lemma, td[data-col="WordSource"]') : null;
            var lemVal = lemCell ? (lemCell.textContent || '').trim() : '';
            var cardSent = (window.AppState ? window.AppState.sourceText : '') || '';
            var tokenOrder = tr ? tr.getAttribute('data-token-order') : null;
            var tokenOrderVal = (tokenOrder !== null && tokenOrder !== '') ? parseInt(tokenOrder, 10) : 0;
            var tip = formatInflectedTooltip(cardSent, newValue, tokenOrderVal, lemVal);
            if (tip) {
                cell.setAttribute('title', tip);
                if (div) div.setAttribute('title', tip);
            } else {
                cell.removeAttribute('title');
                if (div) div.removeAttribute('title');
            }
        }
    }

    function applyOccurrenceRow(tr) {
        if (!tr || !tr.hasAttribute('data-occ')) return;
        var sels = (window.AppState && window.AppState.activeTokenSelections) ? window.AppState.activeTokenSelections : [];
        var cellMap = { 'col-inflected': 'form', 'col-translation': 'trans', 'col-morphology': 'morph', 'col-pos': 'pos' };
        var posNameMap = {
            'art.': 'Article',
            'pron.': 'Pronoun',
            'det.': 'Determiner',
            'prep.': 'Preposition',
            'n.': 'Noun',
            'v.': 'Verb',
            'adj.': 'Adjective',
            'adv.': 'Adverb',
            'conj.': 'Conjunction'
        };
        var occ = null;
        try { occ = JSON.parse(tr.getAttribute('data-occ')); } catch (e) { occ = null; }
        var picked = [];
        if (occ) {
            var keys = [];
            for (var s = 0; s < sels.length; s++) {
                var k = String(typeof sels[s] === 'object' ? sels[s].visual_idx : sels[s]);
                if (occ.hasOwnProperty(k) && keys.indexOf(k) === -1) keys.push(k);
            }
            keys.sort(function (a, b) { return parseInt(a, 10) - parseInt(b, 10); });
            for (var q = 0; q < keys.length; q++) picked.push(occ[keys[q]]);
        }
        for (var cls in cellMap) {
            if (!cellMap.hasOwnProperty(cls)) continue;
            var td = tr.querySelector('td.' + cls);
            if (!td) continue;
            var cell = td.querySelector('.scrollable-cell') || td;
            if (!td.hasAttribute('data-orig-html')) td.setAttribute('data-orig-html', cell.innerHTML);
            if (!td.hasAttribute('data-orig-title')) td.setAttribute('data-orig-title', td.getAttribute('title') || '');
            if (cell !== td && !cell.hasAttribute('data-orig-title')) cell.setAttribute('data-orig-title', cell.getAttribute('title') || '');

            if (picked.length === 0) {
                var rId = tr.getAttribute('data-row-id');
                var rData = (window.AppState && window.AppState.rows && rId !== null) ? window.AppState.rows[rId] : null;
                if (rData && cls === 'col-translation' && rData.provenance) {
                    var prov = td.getAttribute('data-provenance') || rData.provenance;
                    var pTitle = formatProvenanceTooltip(prov);
                    var tVal = rData.trans || cell.textContent || '';
                    var fullTip = tVal + ' (' + pTitle + ')';
                    td.setAttribute('title', fullTip);
                    if (cell !== td) cell.setAttribute('title', fullTip);
                } else {
                    if (cell.innerHTML !== td.getAttribute('data-orig-html')) cell.innerHTML = td.getAttribute('data-orig-html');
                    var origTdTitle = td.getAttribute('data-orig-title');
                    if (origTdTitle) td.setAttribute('title', origTdTitle); else td.removeAttribute('title');
                    if (cell !== td) {
                        var origCellTitle = cell.getAttribute('data-orig-title');
                        if (origCellTitle) cell.setAttribute('title', origCellTitle); else cell.removeAttribute('title');
                    }
                }
                continue;
            }
            var vals = [];
            for (var p = 0; p < picked.length; p++) {
                var v = (picked[p][cellMap[cls]] || '').trim();
                if (v && vals.indexOf(v) === -1) vals.push(v);
            }
            if (vals.length > 0) {
                var newTooltip = '';
                if (cls === 'col-pos') {
                    cell.textContent = vals.join(', ');
                    newTooltip = vals.map(function (p) {
                        var lower = p.toLowerCase();
                        return posNameMap[lower] || p;
                    }).join(', ');
                } else if (cls === 'col-morphology') {
                    cell.textContent = vals.join('; ');
                    newTooltip = vals.join('; ');
                } else if (cls === 'col-translation') {
                    cell.textContent = vals.join(', ');
                    var prov = td.getAttribute('data-provenance');
                    newTooltip = vals.join(', ');
                    if (prov) {
                        var pTitle = formatProvenanceTooltip(prov);
                        newTooltip += ' (' + (pTitle || prov) + ')';
                    }
                } else if (cls === 'col-inflected') {
                    cell.textContent = vals.join(', ');
                    var formsStr = vals.join(', ');
                    var boldHeader = toUnicodeBold(formsStr);
                    newTooltip = boldHeader || formsStr;
                }
                if (newTooltip) {
                    td.setAttribute('title', newTooltip);
                    if (cell !== td) cell.setAttribute('title', newTooltip);
                }
            }
        }
    }
    window.applyOccurrenceRow = applyOccurrenceRow;

    function applyOccurrenceView() {
        var rows = document.querySelectorAll('#lemma-table tbody tr[data-occ]');
        rows.forEach(function (r) {
            applyOccurrenceRow(r);
        });
    }
    window.applyOccurrenceView = applyOccurrenceView;

    // ----------------------------------------------------
    // Toast, Session Helpers, & Repaint
    // ----------------------------------------------------

    window.forceRepaint = function () {
        return document.body ? document.body.offsetHeight : 0;
    };

    window.showToast = function (msg, type, durationMs, onClick) {
        type = type || 'info';
        durationMs = durationMs || 3000;
        var container = document.getElementById('kw-toast-container');
        if (!container) return;
        var toast = document.createElement('div');
        toast.className = 'kw-toast kw-toast-' + type;

        var icon = 'i';
        if (type === 'success') icon = '✓';
        else if (type === 'warning') icon = '!';
        else if (type === 'error') icon = '✕';

        toast.innerHTML = '<span class="kw-toast-icon">' + icon + '</span><span class="kw-toast-msg">' + escapeHtml(msg) + '</span>';

        function dismiss() {
            if (toast.classList.contains('kw-toast-hiding')) return;
            toast.classList.add('kw-toast-hiding');
            setTimeout(function () {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 250);
        }

        if (typeof onClick === 'function') {
            toast.style.cursor = 'pointer';
            toast.onclick = function (e) {
                onClick(e);
                dismiss();
            };
        } else {
            toast.onclick = dismiss;
        }
        container.appendChild(toast);
        setTimeout(dismiss, durationMs);
    };

    function setButtonLoading(btn, isLoading, loadingText, defaultText) {
        if (typeof btn === 'string') {
            btn = document.getElementById(btn);
        }
        if (!btn) return;
        if (isLoading) {
            if (!btn.hasAttribute('data-default-text')) {
                btn.setAttribute('data-default-text', defaultText || btn.textContent || btn.innerText || '');
            }
            btn.disabled = true;
            btn.textContent = loadingText || 'Loading...';
            if (btn._recoveryTimer) {
                clearTimeout(btn._recoveryTimer);
            }
            btn._recoveryTimer = setTimeout(function () {
                btn.disabled = false;
                var original = btn.getAttribute('data-default-text') || defaultText;
                if (original) btn.textContent = original;
                if (btn.id === 'kw-btn-save') {
                    updateToolbarState();
                }
                btn._recoveryTimer = null;
                window.showToast('Action timed out. Restored toolbar controls.', 'warning');
            }, 25000);
        } else {
            if (btn._recoveryTimer) {
                clearTimeout(btn._recoveryTimer);
                btn._recoveryTimer = null;
            }
            btn.disabled = false;
            var original = btn.getAttribute('data-default-text') || defaultText;
            if (original) btn.textContent = original;
            if (btn.id === 'kw-btn-save') {
                updateToolbarState();
            }
        }
    }
    window.setButtonLoading = setButtonLoading;

    function getSessionZid() {
        if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
            var c = window.WorkspaceTabs.getActiveCard();
            if (c && c.zid) return c.zid;
        }
        var el = document.getElementById('session-zid');
        var zidVal = el ? (el.textContent || el.innerText || '').trim() : '';
        if (!zidVal) {
            try {
                if (window.location && window.location.search) {
                    var urlParams = new URLSearchParams(window.location.search);
                    zidVal = urlParams.get('zid') || urlParams.get('session_zid') || '';
                }
            } catch (e) {
                console.warn('[Kardenwort] URLSearchParams parsing error in getSessionZid:', e);
            }
        }
        if (!zidVal && window.AppState && window.AppState.bootstrap) {
            zidVal = window.AppState.bootstrap.session_zid || window.AppState.bootstrap.zid || '';
        }
        if (!zidVal && SESSION_ZID) {
            zidVal = SESSION_ZID;
        }
        return zidVal;
    }
    window.getSessionZid = getSessionZid;

    function getSessionLang() {
        var el = document.getElementById('session-lang');
        var langVal = el ? (el.textContent || el.innerText || '').trim() : '';
        if (!langVal && window.AppState && window.AppState.language) {
            langVal = window.AppState.language;
        }
        if (!langVal && window.AppState && window.AppState.bootstrap) {
            langVal = window.AppState.bootstrap.language || '';
        }
        return langVal || LANGUAGE || 'de';
    }
    window.getSessionLang = getSessionLang;

    function getApiToken() {
        if (typeof API_TOKEN !== 'undefined' && API_TOKEN && API_TOKEN !== '__API_TOKEN__') {
            return API_TOKEN;
        }
        if (typeof window.API_TOKEN !== 'undefined' && window.API_TOKEN) {
            return window.API_TOKEN;
        }
        try {
            if (window.location && window.location.search) {
                var params = new URLSearchParams(window.location.search);
                var tok = params.get('token') || params.get('api_token') || '';
                if (tok) return tok;
            }
            var stored = sessionStorage.getItem('kw_api_token') || localStorage.getItem('kw_api_token');
            if (stored) return stored;
        } catch (e) {
            console.warn('[Kardenwort] Storage/URL error in getApiToken:', e);
        }
        return '';
    }
    window.getApiToken = getApiToken;

    function notifyAHKSelection() {
        if (typeof window.ahkCall === 'function') {
            try {
                var selIds = getSelectedRowIds();
                window.ahkCall('selection', selIds.join(','));
                window.ahkCall('dirty', (typeof window.isDirty === 'function' && window.isDirty()) ? 'true' : 'false');
            } catch (e) {
                console.error('[Kardenwort] Failed to notify AutoHotkey host via ahkCall:', e);
            }
        }
    }
    window.notifyAHKSelection = notifyAHKSelection;

    // ----------------------------------------------------
    // History Stack, Undo/Redo, Deltas, & Save Handlers (R17, R34, R35)
    // ----------------------------------------------------

    var historyStack = [];
    var historyIndex = -1;
    var deltas = [];

    function pushHistory(action) {
        historyStack.splice(historyIndex + 1);
        historyStack.push(action);
        historyIndex++;
    }

    function applyAction(action) {
        if (action.type === 'edit') {
            var div = action.cell.querySelector('.scrollable-cell');
            if (div) div.textContent = action.newValue;
            else action.cell.textContent = action.newValue;
            updateCellTooltip(action.cell, action.newValue);
        } else if (action.type === 'delete') {
            for (var j = 0; j < action.rowIds.length; j++) {
                var rId = action.rowIds[j];
                var tr = document.querySelector('#lemma-table tbody tr[data-row-id="' + rId + '"]');
                if (tr) tr.style.display = 'none';
            }
            if (typeof window.clearAllSelections === 'function') {
                window.clearAllSelections();
            }
        }
    }

    function revertAction(action) {
        if (action.type === 'edit') {
            var div = action.cell.querySelector('.scrollable-cell');
            if (div) div.textContent = action.oldValue;
            else action.cell.textContent = action.oldValue;
            updateCellTooltip(action.cell, action.oldValue);
        } else if (action.type === 'delete') {
            for (var j = 0; j < action.rowIds.length; j++) {
                var rId = action.rowIds[j];
                var tr = document.querySelector('#lemma-table tbody tr[data-row-id="' + rId + '"]');
                if (tr) tr.style.display = '';
            }
        }
    }

    function rebuildDeltas() {
        deltas = [];
        var dirtyCells = document.querySelectorAll('#lemma-table tbody td.dirty');
        dirtyCells.forEach(function (td) {
            td.classList.remove('dirty');
        });

        for (var i = 0; i <= historyIndex; i++) {
            var action = historyStack[i];
            if (action.type === 'edit') {
                var found = false;
                for (var k = 0; k < deltas.length; k++) {
                    if (deltas[k].row_id === action.rowId && deltas[k].column === action.column) {
                        deltas[k].value = action.newValue;
                        deltas[k].token_order = action.tokenOrder;
                        deltas[k].sentence_idx = action.sentenceIdx;
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    deltas.push({
                        row_id: action.rowId,
                        token_order: action.tokenOrder,
                        sentence_idx: action.sentenceIdx,
                        column: action.column,
                        value: action.newValue
                    });
                }
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    var tOrd = (action.tokenOrders && action.tokenOrders[j] !== undefined) ? action.tokenOrders[j] : rId;
                    var sIdx = (action.sentenceIndices && action.sentenceIndices[j] !== undefined) ? action.sentenceIndices[j] : 1;
                    deltas.push({
                        row_id: rId,
                        token_order: tOrd,
                        sentence_idx: sIdx,
                        column: '_delete',
                        value: true
                    });
                }
            }
        }

        for (var dIdx = 0; dIdx < deltas.length; dIdx++) {
            var d = deltas[dIdx];
            if (d.column !== '_delete') {
                var tr = document.querySelector('#lemma-table tbody tr[data-row-id="' + d.row_id + '"]');
                if (tr) {
                    var cell = tr.querySelector('td[data-col="' + d.column + '"]');
                    if (!cell) {
                        if (d.column === 'WordDestination') cell = tr.querySelector('td.col-translation');
                        else if (d.column === 'WordSource') cell = tr.querySelector('td.col-lemma');
                        else if (d.column === 'WordSourceInflectedForm') cell = tr.querySelector('td.col-inflected');
                    }
                    if (cell) cell.classList.add('dirty');
                }
            }
        }

        if (window.AppState) {
            window.AppState.deltas = deltas;
        }

        updateToolbarState();
        if (typeof window.ahkCall === 'function') {
            try {
                window.ahkCall('dirty', deltas.length > 0 ? 'true' : 'false');
            } catch (e) {
                console.error('[Kardenwort] Failed to notify AHK of dirty state:', e);
            }
        }
        if (window.forceRepaint) window.forceRepaint();
    }

    window.undo = function () {
        if (historyIndex < 0) return;
        var action = historyStack[historyIndex];
        historyIndex--;
        revertAction(action);
        rebuildDeltas();
        if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
    };

    window.redo = function () {
        if (historyIndex >= historyStack.length - 1) return;
        historyIndex++;
        var action = historyStack[historyIndex];
        applyAction(action);
        rebuildDeltas();
        if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
    };

    window.isDirty = function () {
        return deltas.length > 0;
    };

    window.getDeltas = function () {
        return JSON.stringify(deltas);
    };

    window.clearDirty = function () {
        historyStack = [];
        historyIndex = -1;
        deltas = [];
        var dirtyCells = document.querySelectorAll('#lemma-table tbody td.dirty');
        dirtyCells.forEach(function (td) {
            td.classList.remove('dirty');
        });
        if (window.AppState) {
            window.AppState.deltas = [];
        }
        updateToolbarState();
        if (typeof window.ahkCall === 'function') {
            try {
                window.ahkCall('dirty', 'false');
            } catch (e) {
                console.error('[Kardenwort] Failed to notify AHK of clean state:', e);
            }
        }
    };

    window.deleteSelectedRows = function () {
        var selected = getSelectedRowIds();
        if (selected.length === 0) return;
        var tOrders = [];
        var sIndices = [];
        for (var i = 0; i < selected.length; i++) {
            var rId = selected[i];
            var tr = document.querySelector('#lemma-table tbody tr[data-row-id="' + rId + '"]');
            var tOrd = rId;
            var sIdx = 1;
            if (tr) {
                var toAttr = tr.getAttribute('data-token-order');
                if (toAttr !== null && toAttr !== '') tOrd = parseInt(toAttr, 10);
                var siAttr = tr.getAttribute('data-sentence-idx');
                if (siAttr !== null && siAttr !== '') sIdx = parseInt(siAttr, 10);
            }
            tOrders.push(tOrd);
            sIndices.push(sIdx);
        }
        var action = {
            type: 'delete',
            rowIds: selected,
            tokenOrders: tOrders,
            sentenceIndices: sIndices
        };
        pushHistory(action);
        applyAction(action);
        rebuildDeltas();
        if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
    };

    function updateToolbarState() {
        var saveBtn = document.getElementById('kw-btn-save');
        if (saveBtn) {
            saveBtn.disabled = !window.isDirty();
        }
    }
    window.updateToolbarState = updateToolbarState;

    window.onSaveClick = function (quiet) {
        if (window.commitActiveEdit) window.commitActiveEdit();
        if (!window.isDirty()) {
            if (!quiet) window.showToast("No changes to save.", "info");
            return;
        }
        var sZid = getSessionZid();
        if (!sZid) {
            if (!quiet) window.showToast("Session ZID missing, cannot save.", "error");
            return;
        }
        var deltasJson = [];
        try {
            deltasJson = JSON.parse(window.getDeltas());
        } catch (e) {
            console.error('[Kardenwort] Failed to parse deltas JSON during save:', e);
        }

        var saveBtn = document.getElementById('kw-btn-save');
        setButtonLoading(saveBtn, true, "Saving...", "Save (Ctrl+S)");

        var tok = getApiToken();
        var headers = { 'Content-Type': 'application/json' };
        if (tok) headers['X-API-Token'] = tok;
        var bodyPayload = {
            session_zid: sZid,
            deltas: deltasJson,
            language: getSessionLang()
        };
        if (tok) bodyPayload.token = tok;

        fetch('/session/save', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(bodyPayload)
        })
        .then(function (res) {
            return res.json().then(function (data) { return { status: res.status, ok: res.ok, data: data }; });
        })
        .then(function (resObj) {
            setButtonLoading(saveBtn, false, "", "Save (Ctrl+S)");
            if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success')) {
                window.clearDirty();
                updateToolbarState();
                window.showToast("Edits saved successfully", "success");
            } else {
                updateToolbarState();
                var msg = (resObj.data && (resObj.data.message || resObj.data.error)) || "Server error";
                window.showToast("Save failed: " + msg, "error");
            }
        })
        .catch(function (err) {
            setButtonLoading(saveBtn, false, "", "Save (Ctrl+S)");
            updateToolbarState();
            window.showToast("Save error: " + (err.message || String(err)), "error");
        });
    };

    var autoSaveDebounceTimer = null;
    window.triggerAutoSaveOnEdit = function () {
        var uiCfg = (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.ui_config) || window.__CONFIG__ || {};
        if (!uiCfg.auto_save_on_edit) return;
        if (autoSaveDebounceTimer) {
            clearTimeout(autoSaveDebounceTimer);
        }
        autoSaveDebounceTimer = setTimeout(function () {
            if (window.isDirty && window.isDirty() && typeof window.onSaveClick === 'function') {
                window.onSaveClick(true);
            }
        }, 300);
    };

    function performSilentAutoSaveOnClose() {
        var uiCfg = (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.ui_config) || window.__CONFIG__ || {};
        if (!uiCfg.auto_save_on_close && !uiCfg.auto_save_on_edit) return;
        if (typeof window.commitActiveEdit === 'function') {
            try {
                window.commitActiveEdit();
            } catch (e) {
                console.warn('[Kardenwort] Error committing active edit before close:', e);
            }
        }
        if (!window.isDirty || !window.isDirty()) return;
        var sZid = getSessionZid();
        if (!sZid) return;
        var deltasJson = [];
        try {
            deltasJson = JSON.parse(window.getDeltas());
        } catch (e) {
            console.error('[Kardenwort] Failed to parse deltas JSON on page close:', e);
        }
        if (!deltasJson || deltasJson.length === 0) return;

        var tok = getApiToken();
        var headers = { 'Content-Type': 'application/json' };
        if (tok) headers['X-API-Token'] = tok;
        var bodyPayload = {
            session_zid: sZid,
            deltas: deltasJson,
            language: getSessionLang()
        };
        if (tok) bodyPayload.token = tok;
        var bodyStr = JSON.stringify(bodyPayload);

        try {
            fetch('/session/save', {
                method: 'POST',
                headers: headers,
                body: bodyStr,
                keepalive: true
            });
        } catch (e) {
            if (navigator && navigator.sendBeacon) {
                try {
                    var blob = new Blob([bodyStr], { type: 'application/json' });
                    navigator.sendBeacon('/session/save', blob);
                } catch (e2) {
                    console.error('[Kardenwort] sendBeacon save failed:', e2);
                }
            }
        }
    }
    window.addEventListener('pagehide', performSilentAutoSaveOnClose);
    window.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            performSilentAutoSaveOnClose();
        }
    });

    // ----------------------------------------------------
    // Progressive Watchdog, Skeleton Cleanup & Updates (R12, R40, R43)
    // ----------------------------------------------------

    function countPendingSkeletons() {
        var skels = document.querySelectorAll('.skeleton-loader, [data-pending="true"]');
        return skels.length;
    }
    window.countPendingSkeletons = countPendingSkeletons;

    function cleanupOrphanSkeletons() {
        var skels = document.querySelectorAll('.skeleton-loader, [data-pending="true"]');
        skels.forEach(function (el) {
            el.classList.remove('skeleton-loader');
            el.removeAttribute('data-pending');
        });
        if (typeof window.normalizeCardSkeletons === 'function') {
            window.normalizeCardSkeletons('timeout');
        }
    }
    window.cleanupOrphanSkeletons = cleanupOrphanSkeletons;

    function initWatchdog() {
        window._kwWatchdogResolved = false;
        if (window._kwSkeletonPollTimer) {
            clearInterval(window._kwSkeletonPollTimer);
            window._kwSkeletonPollTimer = null;
        }
        if (window._kwWatchdogMaxTimer) {
            clearTimeout(window._kwWatchdogMaxTimer);
            window._kwWatchdogMaxTimer = null;
        }

        var isWebMode = (window.location.protocol !== 'file:') || (document.body && document.body.getAttribute('data-web-mode') === 'true') || Boolean(SERVER_URL);
        var curZid = getSessionZid();
        var hasSkeletons = countPendingSkeletons() > 0;

        if (isWebMode && curZid && hasSkeletons) {
            var maxBudgetMs = 30000;
            var totalActiveElapsedMs = 0;
            var lastVisibleTime = (!document.hidden) ? Date.now() : null;

            var getActiveElapsedMs = function () {
                var elapsed = totalActiveElapsedMs;
                if (!document.hidden && lastVisibleTime) {
                    elapsed += (Date.now() - lastVisibleTime);
                }
                return elapsed;
            };

            var pauseWatchdogTimer = function () {
                if (lastVisibleTime) {
                    totalActiveElapsedMs += (Date.now() - lastVisibleTime);
                    lastVisibleTime = null;
                }
                if (window._kwWatchdogMaxTimer) {
                    clearTimeout(window._kwWatchdogMaxTimer);
                    window._kwWatchdogMaxTimer = null;
                }
            };

            var resumeWatchdogTimer = function () {
                if (window._kwWatchdogResolved) return;
                if (document.hidden) return;
                if (!lastVisibleTime) lastVisibleTime = Date.now();
                if (window._kwWatchdogMaxTimer) {
                    clearTimeout(window._kwWatchdogMaxTimer);
                    window._kwWatchdogMaxTimer = null;
                }
                var remaining = maxBudgetMs - getActiveElapsedMs();
                if (remaining <= 0) {
                    cleanupOrphanSkeletons();
                    if (typeof window.showToast === 'function') {
                        window.showToast("Background loading timed out. Restored table editing.", "warning");
                    }
                } else {
                    window._kwWatchdogMaxTimer = setTimeout(function () {
                        if (!window._kwWatchdogResolved) {
                            cleanupOrphanSkeletons();
                            if (typeof window.showToast === 'function') {
                                window.showToast("Background loading timed out. Restored table editing.", "warning");
                            }
                        }
                    }, remaining);
                }
            };

            resumeWatchdogTimer();

            var pollStatus = function () {
                if (document.hidden || window._kwWatchdogResolved) return;
                var sUrl = (SERVER_URL || '') + '/session/status?zid=' + encodeURIComponent(curZid);
                fetch(sUrl, { headers: { 'Accept': 'application/json' } })
                .then(function (res) { if (res.ok) return res.json(); })
                .then(function (resObj) {
                    if (!resObj) return;
                    var payload = (resObj && resObj.data) ? resObj.data : resObj;
                    if (payload && typeof window.receiveUpdate === 'function') {
                        window.receiveUpdate(payload);
                    }
                    var isDone = payload ? (payload.is_finished === true || payload.stage === 'finished' || (payload.status && payload.status.is_finished)) : false;
                    if (isDone || countPendingSkeletons() === 0) {
                        window._kwWatchdogResolved = true;
                        if (window._kwSkeletonPollTimer) {
                            clearInterval(window._kwSkeletonPollTimer);
                            window._kwSkeletonPollTimer = null;
                        }
                        if (window._kwWatchdogMaxTimer) {
                            clearTimeout(window._kwWatchdogMaxTimer);
                            window._kwWatchdogMaxTimer = null;
                        }
                    }
                })
                .catch(function (pollErr) {
                    console.warn('[Kardenwort] Watchdog status poll failed:', pollErr);
                });
            };

            window._kwSkeletonPollTimer = setInterval(pollStatus, 1500);

            if (!window._kwWatchdogVisibilityBound) {
                window._kwWatchdogVisibilityBound = true;
                document.addEventListener('visibilitychange', function () {
                    if (document.visibilityState === 'hidden') {
                        pauseWatchdogTimer();
                    } else {
                        resumeWatchdogTimer();
                        pollStatus();
                    }
                });
            }
        }
    }
    window.initWatchdog = initWatchdog;
    window.ensureWatchdogActive = function () {
        if (countPendingSkeletons() > 0 && !window._kwSkeletonPollTimer) {
            initWatchdog();
        }
    };

    // Task 7.11 (R40): window.receiveUpdate(payload)
    window.receiveUpdate = function (payload) {
        if (!payload) return;
        if (window.AppStore && typeof window.AppStore.applyDeltas === 'function') {
            window.AppStore.applyDeltas(payload);
        }
        var data = (payload.data && typeof payload.data === 'object' && !payload.stage) ? payload.data : payload;

        // 1. Process sentence translation update
        var tText = data.translatedText || data.translated_text;
        if (tText) {
            window.AppState = window.AppState || {};
            window.AppState.translatedText = tText;
            window.AppState.translated_text = tText;
            var tc = document.getElementById('translation-container');
            if (tc) {
                tc.classList.remove('skeleton-loader');
                tc.removeAttribute('data-pending');
                tc.innerHTML = tText;
                tokenizeTranslation();
            }
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.updateActiveTabTranslation === 'function') {
                var actCard = window.WorkspaceTabs.getActiveCard();
                if (actCard) {
                    actCard.translated_text = tText;
                }
                window.WorkspaceTabs.updateActiveTabTranslation();
            }
        }

        // 2. Process multi-sentence distribution (R51, Task 7.18)
        if (data.sentences && window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
            var cards = window.WorkspaceTabs.getCards();
            var pSents = data.sentences;
            var isZeroBased = (pSents[0] !== undefined || pSents['0'] !== undefined);
            for (var c = 0; c < cards.length; c++) {
                var sIdx = cards[c].sentence_idx;
                if (sIdx === 0 && tText) {
                    cards[c].translated_text = tText;
                } else if (sIdx > 0) {
                    var k = isZeroBased ? (sIdx - 1) : sIdx;
                    var val = pSents[k] !== undefined ? pSents[k] : pSents[String(k)];
                    if (val && typeof val === 'string') cards[c].translated_text = val;
                }
            }
            window.WorkspaceTabs.updateActiveTabTranslation();
        }

        // 3. Process table row updates
        var rows = data.rows;
        if (rows && typeof rows === 'object') {
            for (var rowId in rows) {
                if (!rows.hasOwnProperty(rowId)) continue;
                var rData = rows[rowId];
                var tr = document.querySelector('#lemma-table tbody tr[data-row-id="' + rowId + '"]');
                if (!tr) continue;

                if (rData.trans || rData.word_translation || rData.WordDestination) {
                    var val = rData.trans || rData.word_translation || rData.WordDestination;
                    var cell = tr.querySelector('td.col-translation, td[data-col="WordDestination"]');
                    if (cell) {
                        cell.classList.remove('skeleton-loader');
                        cell.removeAttribute('data-pending');
                        var sDiv = cell.querySelector('.scrollable-cell') || cell;
                        sDiv.textContent = val;
                        if (rData.provenance) cell.setAttribute('data-provenance', rData.provenance);
                        updateCellTooltip(cell, val);
                    }
                }
                if (rData.lemma || rData.WordSource) {
                    var valLem = rData.lemma || rData.WordSource;
                    var cellLem = tr.querySelector('td.col-lemma, td[data-col="WordSource"]');
                    if (cellLem) {
                        cellLem.classList.remove('skeleton-loader');
                        cellLem.removeAttribute('data-pending');
                        var sDivLem = cellLem.querySelector('.scrollable-cell') || cellLem;
                        sDivLem.textContent = valLem;
                        updateCellTooltip(cellLem, valLem);
                    }
                }
                if (rData.ipa || rData.WordSourceIPA) {
                    var valIpa = rData.ipa || rData.WordSourceIPA;
                    var cellIpa = tr.querySelector('td.col-ipa, td[data-col="WordSourceIPA"]');
                    if (cellIpa) {
                        cellIpa.classList.remove('skeleton-loader');
                        var sDivIpa = cellIpa.querySelector('.scrollable-cell') || cellIpa;
                        sDivIpa.textContent = valIpa;
                    }
                }
                if (rData.morph || rData.morphology || rData.WordSourceMorphologyAI) {
                    var valMorph = rData.morph || rData.morphology || rData.WordSourceMorphologyAI;
                    var cellMorph = tr.querySelector('td.col-morphology, td[data-col="WordSourceMorphologyAI"]');
                    if (cellMorph) {
                        cellMorph.classList.remove('skeleton-loader');
                        var sDivMorph = cellMorph.querySelector('.scrollable-cell') || cellMorph;
                        sDivMorph.textContent = valMorph;
                    }
                }
                if (rData.pos || rData.WordSourcePOS) {
                    var valPos = rData.pos || rData.WordSourcePOS;
                    var cellPos = tr.querySelector('td.col-pos, td[data-col="WordSourcePOS"]');
                    if (cellPos) {
                        cellPos.classList.remove('skeleton-loader');
                        var sDivPos = cellPos.querySelector('.scrollable-cell') || cellPos;
                        sDivPos.textContent = valPos;
                        var tipPos = formatPosTooltip(valPos);
                        if (tipPos) cellPos.setAttribute('title', tipPos);
                    }
                }
                if (rData.gender || rData.WordSourceGender) {
                    var valGen = rData.gender || rData.WordSourceGender;
                    var cellGen = tr.querySelector('td.col-gender, td[data-col="WordSourceGender"]');
                    if (cellGen) {
                        cellGen.classList.remove('skeleton-loader');
                        var sDivGen = cellGen.querySelector('.scrollable-cell') || cellGen;
                        sDivGen.textContent = valGen;
                    }
                }
                if (rData.classifications) {
                    for (var colName in rData.classifications) {
                        if (!rData.classifications.hasOwnProperty(colName)) continue;
                        var classVal = rData.classifications[colName];
                        var cTd = tr.querySelector('td[data-col="' + colName + '"]');
                        if (cTd) {
                            var levelClass = 'level-' + String(classVal).toLowerCase().split(':')[0];
                            cTd.innerHTML = '<span class="' + levelClass + '">' + escapeHtml(classVal) + '</span>';
                        }
                    }
                }
            }
        }

        if (data.is_finished === true || data.stage === 'finished') {
            cleanupOrphanSkeletons();
        }

        buildLcIndex();
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
    };

    // ----------------------------------------------------
    // Audio Pronunciation Engine (R46, Task 7.16)
    // ----------------------------------------------------

    function sanitizeSpokenText(text) {
        if (!text) return "";
        var s = String(text).replace(/<[^>]+>/g, ' ');
        s = s.replace(/[\{\}\(\)\[\]"'\`]/g, ' ');
        s = s.replace(/(^|\s)[0-9]+(?=\s|$)/g, ' ');
        return s.replace(/\s+/g, ' ').trim();
    }

    function playAudio(text, lang) {
        if (!text) return;
        lang = lang || getSessionLang() || 'de';
        var clean = sanitizeSpokenText(text);
        if (!clean) return;

        var uiCfg = (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.ui_config) || window.__CONFIG__ || {};
        var pythonExe = uiCfg.audio_python_exe || window.__audioPythonExe || '';
        var ttsCli = uiCfg.audio_anki_tts_cli || window.__audioAnkiTtsCli || '';

        if (window.ahkCall && ttsCli && pythonExe) {
            var sanitizedText = clean.replace(/\r?\n|\r/g, ' ');
            var escapedText = sanitizedText.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
            try {
                window.ahkCall('play', pythonExe + "\n" + ttsCli + "\n" + lang + "\n" + escapedText);
            } catch (e) {
                console.error('[Kardenwort] ahkCall play failed:', e);
            }
        } else if (typeof fetch === 'function') {
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var url = (SERVER_URL || '') + '/api/v1/audio/play';
            fetch(url, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ text: clean, language: lang })
            }).catch(function (e) {
                console.warn('[Kardenwort] /api/v1/audio/play failed, attempting /session/play fallback:', e);
                fetch((SERVER_URL || '') + '/session/play', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({ text: clean, language: lang })
                }).catch(function (fallbackErr) {
                    console.error('[Kardenwort] Both audio playback endpoints failed:', fallbackErr);
                });
            });
        }
    }
    window.playAudio = playAudio;

    // ----------------------------------------------------
    // Filter Selected Only & Same Lemma Controls (R8, R39, Tasks 7.1, 7.10)
    // ----------------------------------------------------

    function setFilterSelectedOnly(active) {
        window.AppState = window.AppState || {};
        window.AppState.filterSelectedOnly = Boolean(active);
        if (window.__CONFIG__) {
            window.__CONFIG__.filter_selected_only = Boolean(active);
        }
        var btn = document.getElementById('kw-btn-filter-selected');
        var table = document.getElementById('lemma-table');
        if (btn) {
            if (active) btn.classList.add('btn-filter-active');
            else btn.classList.remove('btn-filter-active');
        }
        if (table) {
            if (active) table.classList.add('kw-filter-selected-only');
            else table.classList.remove('kw-filter-selected-only');
        }
        updateEmptySelectionState();
        try {
            fetch((SERVER_URL || '') + '/api/v1/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ section: 'rendering', key: 'filter_selected_only', value: String(Boolean(active)) })
            }).catch(function (err) {
                console.warn('[Kardenwort] Persisting filter_selected_only config failed:', err);
            });
        } catch (e) {
            console.warn('[Kardenwort] Dispatching filter_selected_only config fetch error:', e);
        }
    }
    window.setFilterSelectedOnly = setFilterSelectedOnly;

    function toggleFilterSelectedOnly() {
        var cur = Boolean(window.AppState && window.AppState.filterSelectedOnly);
        var next = !cur;
        setFilterSelectedOnly(next);
        window.showToast(next ? "Selected rows filter enabled" : "Selected rows filter disabled", "info");
    }
    window.toggleFilterSelectedOnly = toggleFilterSelectedOnly;

    function updateEmptySelectionState() {
        var table = document.getElementById('lemma-table');
        if (!table) return;
        var tbody = table.querySelector('tbody');
        if (!tbody) return;
        var existingEmptyRow = tbody.querySelector('tr.kw-empty-selection-row');

        var isFilter = Boolean(window.AppState && window.AppState.filterSelectedOnly);
        var selCount = getSelectedRowIds().length;

        if (isFilter && selCount === 0) {
            if (!existingEmptyRow) {
                var emptyTr = document.createElement('tr');
                emptyTr.className = 'kw-empty-selection-row kw-empty-visible';
                emptyTr.innerHTML = '<td colspan="8" class="kw-empty-selection-cell">No rows selected. <a href="#" id="kw-btn-empty-clear-filter" class="kw-empty-clear-filter-btn" style="color: var(--accent-color, #58a6ff); text-decoration: underline;">Show all rows</a></td>';
                tbody.appendChild(emptyTr);
                var clearLink = emptyTr.querySelector('#kw-btn-empty-clear-filter');
                if (clearLink) {
                    clearLink.addEventListener('click', function (e) {
                        e.preventDefault();
                        setFilterSelectedOnly(false);
                    });
                }
            } else {
                existingEmptyRow.classList.add('kw-empty-visible');
            }
        } else {
            if (existingEmptyRow) {
                existingEmptyRow.classList.remove('kw-empty-visible');
                if (existingEmptyRow.parentNode) {
                    existingEmptyRow.parentNode.removeChild(existingEmptyRow);
                }
            }
        }
    }
    window.updateEmptySelectionState = updateEmptySelectionState;

    function setHighlightSameLemma(active) {
        window.AppState = window.AppState || {};
        window.AppState.highlightSameLemma = Boolean(active);
        if (window.__CONFIG__) {
            window.__CONFIG__.highlight_same_lemma = Boolean(active);
        }
        var btn = document.getElementById('kw-btn-same-lemma');
        if (btn) {
            if (active) btn.classList.add('active');
            else btn.classList.remove('active');
        }
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
        try {
            fetch((SERVER_URL || '') + '/api/v1/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ section: 'rendering', key: 'highlight_same_lemma', value: String(Boolean(active)) })
            }).catch(function (err) {
                console.warn('[Kardenwort] Persisting highlight_same_lemma config failed:', err);
            });
        } catch (e) {
            console.warn('[Kardenwort] Dispatching highlight_same_lemma config fetch error:', e);
        }
    }
    window.setHighlightSameLemma = setHighlightSameLemma;

    function toggleHighlightSameLemma() {
        var cur = Boolean(window.AppState && window.AppState.highlightSameLemma);
        var next = !cur;
        setHighlightSameLemma(next);
        window.showToast(next ? "Lemma highlighting enabled" : "Lemma highlighting disabled", "info");
    }
    window.toggleHighlightSameLemma = toggleHighlightSameLemma;

    // ----------------------------------------------------
    // Action Toolbar Actions (R8, R21, R51, R52, Tasks 7.1, 7.7, 7.18, 7.19)
    // ----------------------------------------------------

    window.onUpdateClick = function (forceReload) {
        if (window.commitActiveEdit) window.commitActiveEdit();
        var sZid = getSessionZid();
        if (forceReload || !sZid) {
            if (window.location && window.location.reload) window.location.reload();
            return;
        }
        var updateBtn = document.getElementById('kw-btn-update');
        setButtonLoading(updateBtn, true, "Updating...", "Update");

        var tok = getApiToken();
        var headers = { 'Accept': 'application/json' };
        if (tok) headers['X-API-Token'] = tok;

        fetch((SERVER_URL || '') + '/session/status?zid=' + encodeURIComponent(sZid), { headers: headers })
        .then(function (res) {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.json();
        })
        .then(function (resObj) {
            setButtonLoading(updateBtn, false, "", "Update");
            var payload = (resObj && resObj.data) ? resObj.data : resObj;
            if (payload && typeof window.receiveUpdate === 'function') {
                window.receiveUpdate(payload);
                window.showToast("Session updated", "success");
            }
            if (countPendingSkeletons() > 0) {
                initWatchdog();
            }
        })
        .catch(function (err) {
            setButtonLoading(updateBtn, false, "", "Update");
            window.showToast("Update failed: " + err.message, "error");
        });
    };

    window.onRetextClick = function () {
        if (window.commitActiveEdit) window.commitActiveEdit();
        var sZid = getSessionZid();
        if (!sZid) {
            window.showToast("Session ZID missing.", "error");
            return;
        }
        var retextBtn = document.getElementById('kw-btn-retext');
        setButtonLoading(retextBtn, true, "Retexting...", "Re-text");
        window.showToast("Retexting session...", "info");

        var tok = getApiToken();
        var headers = { 'Content-Type': 'application/json' };
        if (tok) headers['X-API-Token'] = tok;

        var body = { session_zid: sZid, language: getSessionLang() };
        if (tok) body.token = tok;

        fetch((SERVER_URL || '') + '/session/retext', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(body)
        })
        .then(function (res) {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.json();
        })
        .then(function (resObj) {
            setButtonLoading(retextBtn, false, "", "Re-text");
            var payload = (resObj && resObj.data) ? resObj.data : resObj;
            window.showToast("Retext completed", "success");
            if (payload && typeof window.receiveUpdate === 'function') {
                window.receiveUpdate(payload);
            }
        })
        .catch(function (err) {
            setButtonLoading(retextBtn, false, "", "Re-text");
            window.showToast("Retext error: " + err.message, "error");
        });
    };

    window.onRewordClick = function () {
        if (window.commitActiveEdit) window.commitActiveEdit();
        var sZid = getSessionZid();
        if (!sZid) {
            window.showToast("Session ZID missing.", "error");
            return;
        }
        var selectedRows = getSelectedRowIds();
        var activeTokens = (window.AppState && window.AppState.activeTokenSelections) ? window.AppState.activeTokenSelections : [];
        if (selectedRows.length === 0 && activeTokens.length === 0) {
            window.showToast("Please select rows to re-word.", "warning");
            return;
        }
        var rewordBtn = document.getElementById('kw-btn-reword');
        setButtonLoading(rewordBtn, true, "Rewording...", "Re-word");

        var targetCoordinates = [];
        activeTokens.forEach(function (t) {
            if (t.visual_idx !== undefined && t.visual_idx !== null) {
                targetCoordinates.push({
                    visual_idx: t.visual_idx,
                    sentence_idx: t.sentence_idx || 1,
                    token_order: t.token_order || 0
                });
            }
        });

        var tok = getApiToken();
        var headers = { 'Content-Type': 'application/json' };
        if (tok) headers['X-API-Token'] = tok;

        var body = {
            session_zid: sZid,
            row_ids: selectedRows,
            token_orders: selectedRows,
            language: getSessionLang(),
            target_coordinates: targetCoordinates
        };
        if (tok) body.token = tok;

        fetch((SERVER_URL || '') + '/session/reword', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(body)
        })
        .then(function (res) {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.json();
        })
        .then(function (resObj) {
            setButtonLoading(rewordBtn, false, "", "Re-word");
            var payload = (resObj && resObj.data) ? resObj.data : resObj;
            window.showToast("Re-word completed for " + selectedRows.length + " rows", "success");
            if (payload && typeof window.receiveUpdate === 'function') {
                window.receiveUpdate(payload);
            }
        })
        .catch(function (err) {
            setButtonLoading(rewordBtn, false, "", "Re-word");
            window.showToast("Re-word error: " + err.message, "error");
        });
    };

    window.onSendToAnkiClick = function () {
        if (window.commitActiveEdit) window.commitActiveEdit();
        var selected = getSelectedRowIds();
        if (selected.length === 0) {
            window.showToast("Please select rows to export.", "warning");
            return;
        }
        var sZid = getSessionZid();
        if (!sZid) {
            window.showToast("Session ZID missing.", "error");
            return;
        }
        var exportBtn = document.getElementById('kw-btn-export');
        setButtonLoading(exportBtn, true, "Sending...", "Send to Anki");
        window.showToast("Exporting to Anki...", "info");

        var tok = getApiToken();
        var headers = { 'Content-Type': 'application/json' };
        if (tok) headers['X-API-Token'] = tok;

        var body = {
            session_zid: sZid,
            row_ids: selected,
            selected_row_ids: selected,
            language: getSessionLang()
        };
        if (tok) body.token = tok;

        fetch((SERVER_URL || '') + '/api/v1/export', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(body)
        })
        .then(function (res) {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.json();
        })
        .then(function (resObj) {
            setButtonLoading(exportBtn, false, "", "Send to Anki");
            var count = selected.length;
            var word = count === 1 ? "card" : "cards";
            window.showToast("✓ " + count + " " + word + " exported to Anki", "success");
        })
        .catch(function (err) {
            fetch((SERVER_URL || '') + '/session/export', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(body)
            })
            .then(function (res2) {
                if (!res2.ok) throw new Error('HTTP ' + res2.status);
                return res2.json();
            })
            .then(function () {
                setButtonLoading(exportBtn, false, "", "Send to Anki");
                var count = selected.length;
                var word = count === 1 ? "card" : "cards";
                window.showToast("✓ " + count + " " + word + " exported to Anki", "success");
            })
            .catch(function (err2) {
                setButtonLoading(exportBtn, false, "", "Send to Anki");
                window.showToast("Export error: " + err2.message, "error");
            });
        });
    };

    window.onHandToolClick = function () {
        var newMode = !window.__persistentSelectableMode;
        setSelectableTextMode(newMode, newMode);
        var handBtn = document.getElementById('kw-btn-hand-tool');
        if (handBtn) {
            if (newMode) handBtn.classList.add('active');
            else handBtn.classList.remove('active');
        }
        window.showToast(newMode ? "Text selection mode active" : "Hand tool / Table interaction active", "info", 1500);
    };

    window.onDeleteClick = function () {
        var rows = getSelectedRowIds();
        if (!rows.length) {
            window.showToast("Please select rows to delete.", "warning");
            return;
        }
        window.deleteSelectedRows();
        window.showToast("Deleted " + rows.length + " rows (Ctrl+Z to undo)", "info", 2000);
    };

    // ----------------------------------------------------
    // Language Verification Modal Handlers (R8, Task 7.2)
    // ----------------------------------------------------

    window.showLanguageVerificationModal = function (info) {
        info = info || {};
        var detCode = info.detected_language || "";
        var expCode = info.expected_language || getSessionLang() || "";
        var promptMsg = "The text appears to be " + (detCode ? detCode.toUpperCase() : "different") +
                        ", but the active profile is " + (expCode ? expCode.toUpperCase() : "unknown") +
                        ".\n\nSwitch language?";
        var modal = document.getElementById('kw-lang-modal');
        var bodyEl = document.getElementById('kw-lang-modal-body');
        if (bodyEl) bodyEl.textContent = promptMsg;
        if (modal) {
            modal.style.display = 'flex';
            window.__langMismatchInfo = info;
            var yesBtn = document.getElementById('kw-btn-lang-yes');
            if (yesBtn) yesBtn.focus();
        }
    };

    window.hideLanguageVerificationModal = function () {
        var modal = document.getElementById('kw-lang-modal');
        if (modal) modal.style.display = 'none';
    };

    window.onLangYes = function () {
        var info = window.__langMismatchInfo || {};
        var detLang = info.detected_language;
        if (!detLang) {
            window.hideLanguageVerificationModal();
            return;
        }
        var sZid = getSessionZid();
        window.hideLanguageVerificationModal();
        window.showToast("Switching language to " + detLang + "...", "info");
        try {
            fetch((SERVER_URL || '') + '/api/v1/set-language', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ language: detLang, session_zid: sZid })
            }).then(function () {
                window.location.reload();
            }).catch(function () {
                window.location.reload();
            });
        } catch (e) {
            window.location.reload();
        }
    };

    window.onLangNo = function () {
        window.hideLanguageVerificationModal();
        window.showToast("Proceeding with active language...", "info");
    };

    window.onLangCancel = function () {
        window.hideLanguageVerificationModal();
        window.showToast("Language verification cancelled.", "info");
    };

    function wireActionToolbar() {
        var btnSame = document.getElementById('kw-btn-same-lemma');
        if (btnSame && !btnSame.__kw_bound) {
            btnSame.__kw_bound = true;
            btnSame.addEventListener('click', function (e) {
                e.preventDefault();
                toggleHighlightSameLemma();
            });
            if (window.AppState && window.AppState.highlightSameLemma) {
                btnSame.classList.add('active');
            }
        }

        var btnFilter = document.getElementById('kw-btn-filter-selected');
        if (btnFilter && !btnFilter.__kw_bound) {
            btnFilter.__kw_bound = true;
            btnFilter.addEventListener('click', function (e) {
                e.preventDefault();
                toggleFilterSelectedOnly();
            });
            if (window.AppState && window.AppState.filterSelectedOnly) {
                btnFilter.classList.add('btn-filter-active');
                var table = document.getElementById('lemma-table');
                if (table) table.classList.add('kw-filter-selected-only');
            }
        }

        var btnSave = document.getElementById('kw-btn-save');
        if (btnSave && !btnSave.__kw_bound) {
            btnSave.__kw_bound = true;
            btnSave.addEventListener('click', function (e) {
                e.preventDefault();
                window.onSaveClick();
            });
        }

        var btnUpdate = document.getElementById('kw-btn-update');
        if (btnUpdate && !btnUpdate.__kw_bound) {
            btnUpdate.__kw_bound = true;
            btnUpdate.addEventListener('click', function (e) {
                e.preventDefault();
                window.onUpdateClick();
            });
        }

        var btnRetext = document.getElementById('kw-btn-retext');
        if (btnRetext && !btnRetext.__kw_bound) {
            btnRetext.__kw_bound = true;
            btnRetext.addEventListener('click', function (e) {
                e.preventDefault();
                window.onRetextClick();
            });
        }

        var btnReword = document.getElementById('kw-btn-reword');
        if (btnReword && !btnReword.__kw_bound) {
            btnReword.__kw_bound = true;
            btnReword.addEventListener('click', function (e) {
                e.preventDefault();
                window.onRewordClick();
            });
        }

        var btnExport = document.getElementById('kw-btn-export');
        if (btnExport && !btnExport.__kw_bound) {
            btnExport.__kw_bound = true;
            btnExport.addEventListener('click', function (e) {
                e.preventDefault();
                window.onSendToAnkiClick();
            });
        }

        var btnHand = document.getElementById('kw-btn-hand-tool');
        if (btnHand && !btnHand.__kw_bound) {
            btnHand.__kw_bound = true;
            btnHand.addEventListener('click', function (e) {
                e.preventDefault();
                window.onHandToolClick();
            });
        }

        var btnDelete = document.getElementById('kw-btn-delete');
        if (btnDelete && !btnDelete.__kw_bound) {
            btnDelete.__kw_bound = true;
            btnDelete.addEventListener('click', function (e) {
                e.preventDefault();
                window.onDeleteClick();
            });
        }

        var btnLangYes = document.getElementById('kw-btn-lang-yes');
        if (btnLangYes && !btnLangYes.__kw_bound) {
            btnLangYes.__kw_bound = true;
            btnLangYes.addEventListener('click', function (e) {
                e.preventDefault();
                window.onLangYes();
            });
        }

        var btnLangNo = document.getElementById('kw-btn-lang-no');
        if (btnLangNo && !btnLangNo.__kw_bound) {
            btnLangNo.__kw_bound = true;
            btnLangNo.addEventListener('click', function (e) {
                e.preventDefault();
                window.onLangNo();
            });
        }

        var btnLangCancel = document.getElementById('kw-btn-lang-cancel');
        if (btnLangCancel && !btnLangCancel.__kw_bound) {
            btnLangCancel.__kw_bound = true;
            btnLangCancel.addEventListener('click', function (e) {
                e.preventDefault();
                window.onLangCancel();
            });
        }
    }
    window.wireActionToolbar = wireActionToolbar;

    // ----------------------------------------------------
    // Inline Editing & Table State Machine (R17, R36, R37, R49)
    // ----------------------------------------------------

    var lastClickedCell = null;
    var lastHoveredCell = null;
    var focusedRowId = null;
    var isDragSelecting = false;
    var dragOccurred = false;
    var dragStartX = 0;
    var dragStartY = 0;
    var dragStartVisualIdx = -1;
    var lastClickedVisualIdx = -1;
    var lastClickedRowId = null;
    var dragSelectMode = true;

    function makeEditable(cell) {
        if (!cell) return;
        if (cell.querySelector('input')) return;

        var scrollDiv = cell.querySelector('.scrollable-cell');
        var originalValue = scrollDiv ? (scrollDiv.textContent || scrollDiv.innerText || '') : (cell.textContent || cell.innerText || '');
        var colName = cell.getAttribute('data-col') || (cell.classList.contains('col-translation') ? 'WordDestination' : (cell.classList.contains('col-lemma') ? 'WordSource' : (cell.classList.contains('col-inflected') ? 'WordSourceInflectedForm' : '')));
        var trParent = cell.parentElement;
        var rowId = trParent ? trParent.getAttribute('data-row-id') : null;
        var rowIdVal = (rowId !== null && rowId !== '') ? parseInt(rowId, 10) : 0;
        var tokenOrder = trParent ? trParent.getAttribute('data-token-order') : null;
        var tokenOrderVal = (tokenOrder !== null && tokenOrder !== '') ? parseInt(tokenOrder, 10) : rowIdVal;
        var sentenceIdx = trParent ? trParent.getAttribute('data-sentence-idx') : null;
        var sentIdxVal = (sentenceIdx !== null && sentenceIdx !== '') ? parseInt(sentenceIdx, 10) : 1;

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'edit-input cell-editor';
        input.value = originalValue;
        input.style.width = '100%';
        input.style.boxSizing = 'border-box';
        input.style.background = 'var(--bg-primary, #0d1117)';
        input.style.color = 'var(--text-primary, #c9d1d9)';
        input.style.border = '1px solid var(--border-color, #30363d)';
        input.style.borderRadius = '4px';
        input.style.padding = '4px';
        input.style.fontSize = 'inherit';
        input.style.fontFamily = 'inherit';

        cell.innerHTML = '';
        cell.classList.add('editing');
        cell.appendChild(input);
        input.focus();
        if (typeof input.select === 'function') {
            try {
                input.select();
            } catch (e) {
                console.warn('[Kardenwort] input.select() failed:', e);
            }
        }

        window.cancelActiveEdit = function () {
            window.cancelActiveEdit = null;
            window.commitActiveEdit = null;
            cell.innerHTML = '';
            var div = document.createElement('div');
            div.className = 'scrollable-cell';
            div.textContent = originalValue;
            cell.appendChild(div);
            cell.classList.remove('editing');
            updateCellTooltip(cell, originalValue);
            if (window.forceRepaint) window.forceRepaint();
        };

        function commit() {
            if (!window.cancelActiveEdit) return;
            window.cancelActiveEdit = null;
            window.commitActiveEdit = null;
            var newValue = input.value;
            cell.innerHTML = '';
            var div = document.createElement('div');
            div.className = 'scrollable-cell';
            div.textContent = newValue;
            cell.appendChild(div);
            cell.classList.remove('editing');
            updateCellTooltip(cell, newValue);

            if (newValue !== originalValue) {
                var action = {
                    type: 'edit',
                    rowId: rowIdVal,
                    tokenOrder: tokenOrderVal,
                    sentenceIdx: sentIdxVal,
                    column: colName,
                    oldValue: originalValue,
                    newValue: newValue,
                    cell: cell
                };
                pushHistory(action);
                rebuildDeltas();
                if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
            }
            if (window.forceRepaint) window.forceRepaint();
        }
        window.commitActiveEdit = commit;

        input.addEventListener('keydown', function (e) {
            var keyCode = e.keyCode || e.which;
            if (e.ctrlKey && (keyCode === 65 || e.key === 'a')) {
                e.preventDefault();
                input.select();
            } else if (keyCode === 13 || e.key === 'Enter') {
                e.preventDefault();
                commit();
            } else if (keyCode === 27 || e.key === 'Escape') {
                e.preventDefault();
                if (window.cancelActiveEdit) window.cancelActiveEdit();
            } else if (keyCode === 9 || e.key === 'Tab') {
                e.preventDefault();
                commit();

                var editables = Array.prototype.slice.call(document.querySelectorAll('#lemma-table tbody tr:not([style*="display: none"]) td.editable'));
                var idx = editables.indexOf(cell);
                if (idx !== -1) {
                    var nextIdx = e.shiftKey ? idx - 1 : idx + 1;
                    if (nextIdx >= 0 && nextIdx < editables.length) {
                        makeEditable(editables[nextIdx]);
                    }
                }
            }
        });

        input.addEventListener('blur', function () {
            commit();
        });
    }
    window.makeEditable = makeEditable;

    function editFocusedCell() {
        var cellToEdit = null;
        if (lastHoveredCell) {
            var rId = parseInt(lastHoveredCell.parentElement.getAttribute('data-row-id'), 10);
            if (rId === focusedRowId) cellToEdit = lastHoveredCell;
        }
        if (!cellToEdit && lastClickedCell) {
            var rId = parseInt(lastClickedCell.parentElement.getAttribute('data-row-id'), 10);
            if (rId === focusedRowId) cellToEdit = lastClickedCell;
        }
        if (!cellToEdit && focusedRowId !== null) {
            var row = document.querySelector('#lemma-table tbody tr[data-row-id="' + focusedRowId + '"]');
            if (row) {
                var editables = row.querySelectorAll('td.editable');
                if (editables.length > 0) cellToEdit = editables[0];
            }
        }
        if (cellToEdit) {
            makeEditable(cellToEdit);
        }
    }
    window.editFocusedCell = editFocusedCell;

    function updateRowFocus() {
        var rows = document.querySelectorAll('#lemma-table tbody tr');
        rows.forEach(function (row) {
            var rId = row.getAttribute('data-row-id');
            var parsedId = (rId !== null && rId !== '') ? parseInt(rId, 10) : null;
            if (parsedId !== null && parsedId === focusedRowId) {
                row.style.outline = '1px solid #58a6ff';
                row.classList.add('focused-row');
                if (typeof row.scrollIntoView === 'function') {
                    row.scrollIntoView({ block: 'nearest' });
                }
            } else {
                row.style.outline = 'none';
                row.classList.remove('focused-row');
            }
        });
    }
    window.updateRowFocus = updateRowFocus;

    function selectAllRows() {
        var rows = document.querySelectorAll('#lemma-table tbody tr');
        window.AppState = window.AppState || {};
        window.AppState.selected_ids = window.AppState.selected_ids || [];
        window.AppState.selectedLemmas = window.AppState.selectedLemmas || {};

        rows.forEach(function (row) {
            selectTableRow(row);
        });

        if (typeof window.syncSelectionsToCardsAndState === 'function') {
            window.syncSelectionsToCardsAndState();
        }
        window.updateRowStyles();
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
        notifyAHKSelection();
    }
    window.selectAllRows = selectAllRows;

    function clearAllSelections() {
        window.AppState = window.AppState || {};
        window.AppState.selected_ids = [];
        window.AppState.selectedLemmas = {};
        window.AppState.activeTokenSelections = [];

        var rows = document.querySelectorAll('#lemma-table tbody tr');
        rows.forEach(function (row) {
            row.classList.remove('selected', 'kw-row-selected');
            row.setAttribute('data-selected', '0');
            var cb = row.querySelector('.kw-tag-checkbox');
            if (cb) cb.checked = false;
        });

        if (window.clearMVPBookmarks) window.clearMVPBookmarks();
        if (typeof window.syncSelectionsToCardsAndState === 'function') {
            window.syncSelectionsToCardsAndState();
        }
        lastClickedRowId = null;
        lastClickedVisualIdx = -1;
        window.updateRowStyles();
        if (typeof window.updateBidirectionalHighlights === 'function') {
            window.updateBidirectionalHighlights();
        }
        notifyAHKSelection();
    }
    window.clearAllSelections = clearAllSelections;
    window.clearAllSelectionsAndNotify = clearAllSelections;

    window.setSelectedRows = function (rowsJsonStr) {
        try {
            var arr = typeof rowsJsonStr === 'string' ? JSON.parse(rowsJsonStr) : rowsJsonStr;
            window.AppState = window.AppState || {};
            window.AppState.selected_ids = [];
            window.AppState.selectedLemmas = {};

            var idMap = {};
            for (var i = 0; i < arr.length; i++) {
                var num = parseInt(arr[i], 10);
                if (!isNaN(num)) {
                    idMap[num] = true;
                    idMap[String(num)] = true;
                    window.AppState.selected_ids.push(num);
                }
            }

            var rows = document.querySelectorAll('#lemma-table tbody tr');
            rows.forEach(function (row) {
                var rId = row.getAttribute('data-row-id');
                var isSel = (rId !== null && (idMap[rId] || idMap[parseInt(rId, 10)]));
                if (isSel) {
                    selectTableRow(row);
                } else {
                    deselectTableRow(row);
                }
            });

            if (typeof window.syncSelectionsToCardsAndState === 'function') {
                window.syncSelectionsToCardsAndState();
            }
            window.updateRowStyles();
            if (typeof window.updateBidirectionalHighlights === 'function') {
                window.updateBidirectionalHighlights();
            }
            notifyAHKSelection();
            if (window.forceRepaint) window.forceRepaint();
        } catch (e) {
            console.error('[Kardenwort] setSelectedRows error:', e);
        }
    };

    function syncSelectionsToCardsAndState() {
        if (window.WorkspaceTabs && typeof window.WorkspaceTabs.syncSelectionsToCardsAndState === 'function') {
            window.WorkspaceTabs.syncSelectionsToCardsAndState();
            return;
        }
        var appState = window.AppState;
        if (!appState) return;
        var cards = appState.sentenceCards || (appState.bootstrap && appState.bootstrap.sentence_cards);
        if (cards && cards.length > 0) {
            var selIds = getSelectedRowIds();
            var activeCard = (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') ? window.WorkspaceTabs.getActiveCard() : null;
            if (activeCard) {
                activeCard.selected_ids = selIds;
            }
        }
    }
    window.syncSelectionsToCardsAndState = syncSelectionsToCardsAndState;

    // Single consolidated keydown listener
    window.addEventListener('keydown', function (e) {
        var target = e.target;
        var isInput = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
        var keyCode = e.keyCode || e.which;

        // Language modal key trapping (R8, Task 7.2)
        var langModal = document.getElementById('kw-lang-modal');
        var isModalOpen = langModal && langModal.style.display !== 'none';
        if (isModalOpen) {
            if (keyCode === 27 || e.key === 'Escape') {
                e.preventDefault();
                window.onLangCancel();
                return;
            }
            if (keyCode === 13 || e.key === 'Enter') {
                e.preventDefault();
                window.onLangYes();
                return;
            }
        }

        // Escape: cancel edit, or clear bookmark pins, or clear selections
        if (keyCode === 27 || e.key === 'Escape') {
            if (window.cancelActiveEdit) {
                e.preventDefault();
                window.cancelActiveEdit();
                return;
            }
            if (window.mvpBookmarks && window.mvpBookmarks.length > 0) {
                e.preventDefault();
                window.clearMVPBookmarks();
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                }
                return;
            }
            clearAllSelections();
            return;
        }

        // If typing in input, do not intercept table navigation or action shortcuts
        if (isInput) return;

        // F5: Update / re-render (R14, Task 7.4)
        if (keyCode === 116 || e.key === 'F5') {
            e.preventDefault();
            if (typeof window.onUpdateClick === 'function') {
                window.onUpdateClick();
            }
            return;
        }

        // Ctrl+Shift+A: Deselect all rows (R14, Task 7.4)
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && (keyCode === 65 || e.key === 'a' || e.key === 'A')) {
            e.preventDefault();
            clearAllSelections();
            return;
        }

        // Ctrl+S: Save
        if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (keyCode === 83 || e.key === 's' || e.key === 'S')) {
            e.preventDefault();
            var saveBtn = document.getElementById('kw-btn-save');
            if (saveBtn && !saveBtn.disabled) {
                saveBtn.click();
            } else if (typeof window.onSaveClick === 'function') {
                window.onSaveClick();
            }
            return;
        }

        // Ctrl+A: Select all rows
        if ((e.ctrlKey || e.metaKey) && (keyCode === 65 || e.key === 'a' || e.key === 'A')) {
            e.preventDefault();
            selectAllRows();
            return;
        }

        // Ctrl+Z: Undo (single stroke)
        if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (keyCode === 90 || e.key === 'z' || e.key === 'Z')) {
            e.preventDefault();
            window.undo();
            return;
        }

        // Ctrl+Y or Ctrl+Shift+Z: Redo (single stroke)
        if (((e.ctrlKey || e.metaKey) && (keyCode === 89 || e.key === 'y' || e.key === 'Y')) ||
            ((e.ctrlKey || e.metaKey) && e.shiftKey && (keyCode === 90 || e.key === 'z' || e.key === 'Z'))) {
            e.preventDefault();
            window.redo();
            return;
        }

        // Delete: Delete selected rows
        if (keyCode === 46 || e.key === 'Delete') {
            if (getSelectedRowIds().length > 0) {
                e.preventDefault();
                window.deleteSelectedRows();
                return;
            }
        }

        // ArrowUp / ArrowDown: Row traversal
        if (keyCode === 38 || keyCode === 40 || e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            var rows = Array.prototype.slice.call(document.querySelectorAll('#lemma-table tbody tr:not([style*="display: none"])'));
            if (rows.length === 0) return;

            var currentIdx = -1;
            for (var v = 0; v < rows.length; v++) {
                var rId = parseInt(rows[v].getAttribute('data-row-id'), 10);
                if (rId === focusedRowId) {
                    currentIdx = v;
                    break;
                }
            }

            var nextIdx = 0;
            if (currentIdx === -1) {
                nextIdx = (keyCode === 38 || e.key === 'ArrowUp') ? (rows.length - 1) : 0;
            } else {
                if (keyCode === 40 || e.key === 'ArrowDown') {
                    nextIdx = Math.min(currentIdx + 1, rows.length - 1);
                } else {
                    nextIdx = Math.max(currentIdx - 1, 0);
                }
            }

            var nextRow = rows[nextIdx];
            if (nextRow) {
                focusedRowId = parseInt(nextRow.getAttribute('data-row-id'), 10);
                updateRowFocus();
            }
            return;
        }

        // Space: Toggle selection of focused row
        if (keyCode === 32 || e.key === ' ') {
            e.preventDefault();
            if (focusedRowId !== null) {
                var fRow = document.querySelector('#lemma-table tbody tr[data-row-id="' + focusedRowId + '"]');
                if (fRow) {
                    var isCurrentlySelected = fRow.classList.contains('kw-row-selected') || fRow.classList.contains('selected') || fRow.getAttribute('data-selected') === '1';
                    if (isCurrentlySelected) {
                        deselectTableRow(fRow);
                    } else {
                        selectTableRow(fRow);
                    }
                    if (typeof window.syncSelectionsToCardsAndState === 'function') {
                        window.syncSelectionsToCardsAndState();
                    }
                    window.updateRowStyles();
                    if (typeof window.updateBidirectionalHighlights === 'function') {
                        window.updateBidirectionalHighlights();
                    }
                    notifyAHKSelection();
                }
            }
            return;
        }

        // F2: Edit focused cell
        if (keyCode === 113 || e.key === 'F2') {
            e.preventDefault();
            editFocusedCell();
            return;
        }
    });

    window.addEventListener('mouseup', function (e) {
        if (isDragSelecting) {
            var table = document.getElementById('lemma-table');
            if (table) table.classList.remove('kw-table-dragging');
            isDragSelecting = false;
            if (typeof window.syncSelectionsToCardsAndState === 'function') {
                window.syncSelectionsToCardsAndState();
            }
            window.updateRowStyles();
            if (typeof window.updateBidirectionalHighlights === 'function') {
                window.updateBidirectionalHighlights();
            }
            notifyAHKSelection();
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
        var table = document.getElementById('lemma-table');
        var rows = document.querySelectorAll('#lemma-table tbody tr');

        rows.forEach(function (row) {
            if (row.__kw_bound) return;
            row.__kw_bound = true;

            row.addEventListener('mousedown', function (e) {
                if (window.__selectableTextMode) return;
                var target = e.target;
                if (target && (target.tagName === 'INPUT' || target.closest('.editing'))) return;
                if (e.button !== 0) return;

                if (table) table.classList.add('kw-table-dragging');
                isDragSelecting = true;
                dragOccurred = false;
                dragStartX = e.clientX;
                dragStartY = e.clientY;

                var allRows = Array.prototype.slice.call(document.querySelectorAll('#lemma-table tbody tr'));
                var rowVisualIdx = allRows.indexOf(row);
                var rIdStr = row.getAttribute('data-row-id');
                var rowId = (rIdStr !== null && rIdStr !== '') ? parseInt(rIdStr, 10) : rowVisualIdx;

                if (e.shiftKey && lastClickedVisualIdx !== -1) {
                    var start = Math.min(lastClickedVisualIdx, rowVisualIdx);
                    var end = Math.max(lastClickedVisualIdx, rowVisualIdx);
                    for (var k = start; k <= end; k++) {
                        if (allRows[k]) selectTableRow(allRows[k]);
                    }
                    if (typeof window.syncSelectionsToCardsAndState === 'function') {
                        window.syncSelectionsToCardsAndState();
                    }
                    window.updateRowStyles();
                    if (typeof window.updateBidirectionalHighlights === 'function') {
                        window.updateBidirectionalHighlights();
                    }
                    notifyAHKSelection();
                } else if (e.ctrlKey || e.metaKey) {
                    var isCur = row.classList.contains('kw-row-selected') || row.classList.contains('selected') || row.getAttribute('data-selected') === '1';
                    dragSelectMode = !isCur;
                    if (dragSelectMode) selectTableRow(row);
                    else deselectTableRow(row);

                    dragStartVisualIdx = rowVisualIdx;
                    lastClickedVisualIdx = rowVisualIdx;
                    lastClickedRowId = dragSelectMode ? rowId : null;
                    focusedRowId = rowId;
                    updateRowFocus();

                    if (typeof window.syncSelectionsToCardsAndState === 'function') {
                        window.syncSelectionsToCardsAndState();
                    }
                    window.updateRowStyles();
                    if (typeof window.updateBidirectionalHighlights === 'function') {
                        window.updateBidirectionalHighlights();
                    }
                    notifyAHKSelection();
                } else {
                    var isCur = row.classList.contains('kw-row-selected') || row.classList.contains('selected') || row.getAttribute('data-selected') === '1';
                    dragSelectMode = !isCur;
                    if (dragSelectMode) selectTableRow(row);
                    else deselectTableRow(row);

                    dragStartVisualIdx = rowVisualIdx;
                    lastClickedVisualIdx = rowVisualIdx;
                    lastClickedRowId = dragSelectMode ? rowId : null;
                    focusedRowId = rowId;
                    updateRowFocus();

                    if (typeof window.syncSelectionsToCardsAndState === 'function') {
                        window.syncSelectionsToCardsAndState();
                    }
                    window.updateRowStyles();
                    if (typeof window.updateBidirectionalHighlights === 'function') {
                        window.updateBidirectionalHighlights();
                    }
                    notifyAHKSelection();
                }
            });

            row.addEventListener('mouseover', function (e) {
                if (window.__selectableTextMode) return;
                if (!isDragSelecting) return;
                if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                    if (table) table.classList.remove('kw-table-dragging');
                    isDragSelecting = false;
                    return;
                }
                if (!dragOccurred) {
                    var dx = e.clientX - dragStartX;
                    var dy = e.clientY - dragStartY;
                    if (Math.sqrt(dx * dx + dy * dy) < 5) return;
                    dragOccurred = true;
                }

                var allRows = Array.prototype.slice.call(document.querySelectorAll('#lemma-table tbody tr'));
                var rowVisualIdx = allRows.indexOf(row);
                if (rowVisualIdx === -1 || dragStartVisualIdx === -1) return;

                var start = Math.min(dragStartVisualIdx, rowVisualIdx);
                var end = Math.max(dragStartVisualIdx, rowVisualIdx);
                for (var k = start; k <= end; k++) {
                    if (allRows[k]) {
                        if (dragSelectMode) selectTableRow(allRows[k]);
                        else deselectTableRow(allRows[k]);
                    }
                }
                window.updateRowStyles();
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                }
            });

            var editables = row.querySelectorAll('td.editable');
            editables.forEach(function (cell) {
                cell.addEventListener('click', function () {
                    lastClickedCell = cell;
                });
                cell.addEventListener('mouseover', function () {
                    lastHoveredCell = cell;
                });
                cell.addEventListener('dblclick', function (e) {
                    if (window.__selectableTextMode) return;
                    e.preventDefault();
                    makeEditable(cell);
                });
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
        if (window.SourceViewer && typeof window.SourceViewer.mount === 'function') {
            window.SourceViewer.mount('#source-container');
        }
        if (window.TranslationView && typeof window.TranslationView.mount === 'function') {
            window.TranslationView.mount('#translation-container');
        }
        if (window.LemmaTable && typeof window.LemmaTable.mount === 'function') {
            window.LemmaTable.mount('#lemma-table');
        }
        hydrateSession();
        tokenizeSource();
        tokenizeTranslation();
        buildLcIndex();
        initTokenDelegation();
        if (typeof window.rebindTableRows === 'function') {
            window.rebindTableRows();
        }
        wireActionToolbar();
        updateToolbarState();
        initWatchdog();

        // Audio RMB contextmenu listener (R46, Task 7.16)
        var handleRmbAudio = function (e) {
            var uiCfg = (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.ui_config) || window.__CONFIG__ || {};
            if (!uiCfg.audio_rmb_play) return;
            var target = e.target;
            var span = target && target.closest ? target.closest('#source-container span.word, #translation-container span.word') : null;
            var row = target && target.closest ? target.closest('#lemma-table tbody tr') : null;
            if (span) {
                e.preventDefault();
                var isSource = span.closest('#source-container') !== null;
                var lang = isSource ? getSessionLang() : (TARGET_LANG || 'ru');
                var text = span.getAttribute('data-word') || span.textContent;
                playAudio(text, lang);
            } else if (row) {
                e.preventDefault();
                var lemCell = row.querySelector('.col-lemma');
                var transCell = row.querySelector('.col-translation');
                var isTrans = (uiCfg.audio_rmb_source === 'word_translation');
                var text = isTrans ? (transCell ? transCell.textContent.trim() : '') : (lemCell ? lemCell.textContent.trim() : '');
                var lang = isTrans ? (TARGET_LANG || 'ru') : getSessionLang();
                if (text) playAudio(text, lang);
            }
        };
        var sc = document.getElementById('source-container');
        var tc = document.getElementById('translation-container');
        var lt = document.getElementById('lemma-table');
        if (sc) sc.addEventListener('contextmenu', handleRmbAudio);
        if (tc) tc.addEventListener('contextmenu', handleRmbAudio);
        if (lt) lt.addEventListener('contextmenu', handleRmbAudio);

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
