
(function() {
    window.reserveTextRegions = function() {
        ['source-container', 'translation-container'].forEach(function(id) {
            var element = document.getElementById(id);
            if (element) element.style.removeProperty('--kw-text-region-size');
        });
    };
    window.AppState = window.AppState || {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
    window.AppState.rows = window.AppState.rows || {};

    window.escapeHtml = function(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    var SERVER_URL = window.location.origin || '';
    var SESSION_ZID = '';
    var API_TOKEN = '';
    var LANGUAGE = '';
    var TARGET_LANG = '';
    var FINGERPRINT = '';
    var SERVER_ENABLED = false;
    var IS_OFFLINE = false;

    /** @param {string} id @returns {HTMLElement} */
    function requireElement(id) {
        var element = document.getElementById(id);
        if (!element) throw new Error('Required UI element missing: ' + id);
        return element;
    }

    function getSelectedRowIds() {
         var selected = [];
        var seen = {};

        var checkedBoxes = (document.querySelectorAll('.kw-tag-checkbox:checked'));
        checkedBoxes.forEach(function (cb) {
            var rId = cb.getAttribute('data-row-id');
            if (rId === null || rId === '') {
                rId = cb.getAttribute('data-token-order');
            }
            if (rId !== null && rId !== '') {
                var num = parseInt(String(rId), 10);
                if (!isNaN(num) && !seen[num]) {
                    seen[num] = true;
                    selected.push(num);
                }
            }
        });

        var selectedRows = (document.querySelectorAll('#lemma-table tbody tr.kw-row-selected, #lemma-table tbody tr.selected, #lemma-table tbody tr[data-selected="1"]'));
        selectedRows.forEach(function (row) {
            var rId = row.getAttribute('data-row-id');
            if (rId === null || rId === '') {
                rId = row.getAttribute('data-token-order');
            }
            if (rId !== null && rId !== '') {
                var num = parseInt(String(rId), 10);
                if (!isNaN(num) && !seen[num]) {
                    seen[num] = true;
                    selected.push(num);
                }
            }
        });

        if (selected.length === 0 && window.AppState && Array.isArray(window.AppState.selected_ids)) {
            window.AppState.selected_ids.forEach(function (id) {
                var num = parseInt(String(id), 10);
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

    /** @param {string} msg @param {boolean} isError */
    function setStatusMessage(msg, isError) {
        var statusEl = document.getElementById('kw-interactive-status');
        if (statusEl) {
            statusEl.textContent = msg;
            statusEl.style.color = isError ? 'var(--status-error, #f85149)' : 'var(--status-success, #3fb950)';
            statusEl.style.display = msg ? 'inline-block' : 'none';
        }
    }

    /** @param {string} status */
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
        if (window.toggleRowSelection && window.notifyAHKSelection) {
            window.toggleRowSelection(rowId, newStatus);
            window.notifyAHKSelection();
            return;
        }
        var rowEl = checkboxEl.closest ? checkboxEl.closest('tr') : (checkboxEl.parentElement && checkboxEl.parentElement.parentElement);
        if (rowEl) {
            if (newStatus) {
                rowEl.classList.add('kw-row-selected');
            } else {
                rowEl.classList.remove('kw-row-selected');
            }
        }
        setStatusMessage('Saving...', false);

        var payload = {
            session_zid: getSessionZid() || SESSION_ZID,
            language: getSessionLang() || LANGUAGE,
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
        var btn = (document.getElementById('kw-export-btn'));
        if (btn) btn.disabled = true;
        setStatusMessage('Exporting...', false);

        var payload = {
            session_zid: getSessionZid() || SESSION_ZID,
            language: getSessionLang() || LANGUAGE,
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

    /** @param {string} sessionZid */
    function initSSE(sessionZid) {
        if (!window.SSEBridge) throw new Error('Required SSE bridge is unavailable');
        window.SSEBridge.connect(sessionZid, SERVER_URL);
    }
    window.initSSE = initSSE;

    // Tokenization Helpers
     var sourceSpansArray = [];
     var transSpansArray = [];
    var isMvpIndexBuilt = false;

    window.sourceSpansArray = sourceSpansArray;
    window.transSpansArray = transSpansArray;
    window.mvpBookmarks = window.mvpBookmarks || [];
    window.__selectableTextMode = false;
    window.__persistentSelectableMode = false;

    /** @param {string} text */
    function tokenizeText(text) {
        var rx = /([a-zA-Z0-9'\u00C0-\u024F\u0400-\u04FF\u0500-\u052F\u1E00-\u1EFF]+)/g;
        return text.split(rx);
    }
    window.tokenizeText = tokenizeText;

    /** @param {unknown} str */
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

    function synchronizeOverviewTranslation() {
        if (!window.WorkspaceTabs || !window.WorkspaceTabs.getCards) return;
        var cards = window.WorkspaceTabs.getCards() || [];
        for (var i = 0; i < cards.length; i++) {
            if (cards[i].sentence_idx === 0) {
                cards[i].translated_text = window.AppState.translatedText || '';
                return;
            }
        }
    }

    function activeSentenceTranslation() {
        return window.WorkspaceTabs && window.WorkspaceTabs.getActiveSentenceIdx && window.WorkspaceTabs.getActiveSentenceIdx() > 0;
    }

    function translationPending(container) {
        return container.querySelector('[data-pending="true"], .skeleton-loader') || container.classList.contains('skeleton-loader');
    }

    function translationFailed() {
        return window.AppState.textTranslationFailed || window.AppState.textTranslationStatus === 'failed';
    }

    function translationTerminal(stage) {
        return stage === 'finished' || window.AppState.isFinished || translationFailed();
    }

    function canonicalTranslationHtml(text) {
        if (window.WorkspaceTabs && window.WorkspaceTabs.getTranslationHtml) return window.WorkspaceTabs.getTranslationHtml(text);
        var clean = (text || '').trim();
        if (clean.indexOf('<div') !== -1 || clean.indexOf('</div') !== -1) {
            var probe = document.createElement('div');
            probe.innerHTML = clean;
            clean = (probe.textContent || '').trim();
        }
        return escapeHtml(clean);
    }

    function translationReplacementRequested(text, pending, stage) {
        if (text) return true;
        if (!pending && !translationFailed()) return false;
        return translationTerminal(stage);
    }

    function projectGlobalTranslation(container, stage) {
        var text = window.AppState.translatedText;
        if (!translationReplacementRequested(text, translationPending(container), stage)) return false;
        var changed = window.TranslationView.project(canonicalTranslationHtml(text), '0');
        applyTextProvenance(container);
        return changed;
    }

    function stateTextProvenance() {
        var state = window.AppState;
        return state ? (state.textProvenance || state.text_provenance || state.provenance) : null;
    }

    function scriptTextProvenance() {
        var script = document.getElementById('sentence-provenance');
        return script ? (script.textContent || script.innerText || '').trim() : '';
    }

    function liveTextProvenance() {
        if (!window.AppState.translatedText || window.AppState.textTranslationFailed) return '';
        var element = document.getElementById('text-base-provider');
        var provider = element ? (element.textContent || element.innerText || '').trim() : 'google';
        return 'live:' + (provider || 'google');
    }

    function applyTextProvenance(container) {
        if (!container) throw new Error('Missing translation container');
        var provenance = stateTextProvenance() || container.getAttribute('data-provenance') || scriptTextProvenance() || liveTextProvenance();
        if (provenance) projectTextProvenance(container, provenance);
    }

    function projectTextProvenance(container, provenance) {
        container.setAttribute('data-provenance', String(provenance));
        var title = formatProvenanceTooltip(provenance);
        if (!title) return;
        container.setAttribute('title', String(title));
        var words = container.querySelectorAll('span.word, div');
        for (var i = 0; i < words.length; i++) {
            words[i].setAttribute('data-provenance', String(provenance));
            words[i].setAttribute('title', String(title));
        }
    }
    window.applyTextProvenance = applyTextProvenance;

    function isTextRegionNode(node) {
        while (node) {
            if (node.id === 'source-container' || node.id === 'translation-container') return true;
            node = node.parentNode;
        }
        return false;
    }

    function tokenizeSource() {
        var sc = document.getElementById('source-container');
        if (!sc) return;
        if ( (sc.querySelector('span.word'))) return;
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
                var wordCls = ['word', 'highlight-orange'].join(' ');
                html += '<span class="' + wordCls + '" data-word="' + escapeHtml(part) + '" data-word-idx="' + wordIdx + '" data-order="' + wordIdx + '" data-token-order="' + wordIdx + '" data-lower-clean="' + escapeHtml(lc) + '" data-line-idx="0" data-sentence-idx="1">' + escapeHtml(part) + '</span>';
                wordIdx++;
            } else {
                html += escapeHtml(part);
            }
        }
        sc.innerHTML = html;
    }
    window.tokenizeSource = tokenizeSource;

    /** @param {string=} text @param {VocabularyRow[]=} words */
    function tokenizeTranslation(text, words) {
        var tc = document.getElementById('translation-container');
        if (!tc) return;
        if ( (tc.querySelector('[data-pending="true"]')) ||  (tc.querySelector('.skeleton-loader')) || tc.classList.contains('skeleton-loader')) {
            return;
        }
        if (typeof text === 'string' && text) {
            tc.innerHTML = text;
        }

        var sc = document.getElementById('source-container');
        var maxSourceSent = 1;
        if (sc) {
            var scChunks = (sc.querySelectorAll('.kw-sentence-chunk, span.word[data-sentence-idx]'));
            for (var scIdx = 0; scIdx < scChunks.length; scIdx++) {
                var sVal = parseInt(String(scChunks[scIdx].getAttribute('data-sentence-idx')), 10);
                if (!isNaN(sVal) && sVal > maxSourceSent) maxSourceSent = sVal;
            }
        }
        if (maxSourceSent === 1 && window.AppState && window.AppState.sentenceCards && window.AppState.sentenceCards.length > 1) {
            maxSourceSent = window.AppState.sentenceCards.length - 1;
        }
        var isSingleChildTab = (tc.getAttribute('data-sentence-idx') && parseInt(String(tc.getAttribute('data-sentence-idx')), 10) > 0);
        var hasMultiSent = (!isSingleChildTab && maxSourceSent > 1);

        var divs = tc.getElementsByTagName('div');
        if (divs.length === 0) {
            var tcSentIdx = tc.getAttribute('data-sentence-idx') || '1';
            var tcLineIdx = tc.getAttribute('data-line-idx') || '0';
            var firstChild = (tc.firstChild);
            if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                var childSpans = tc.getElementsByTagName('span');
                for (var j = 0; j < childSpans.length; j++) {
                    var span = childSpans[j];
                    if (span.classList && span.classList.contains('word')) {
                        if (!span.classList.contains('hl-mvp')) span.classList.add('hl-mvp');
                        if (!span.getAttribute('data-line-idx')) span.setAttribute('data-line-idx', String(tcLineIdx));
                        if (!span.getAttribute('data-sentence-idx')) span.setAttribute('data-sentence-idx', String(tcSentIdx));
                        if (!span.getAttribute('data-word')) span.setAttribute('data-word', String((span.textContent || '').trim()));
                        if (!span.getAttribute('data-order')) span.setAttribute('data-order', String(j));
                    }
                }
            } else {
                var rawText = tc.textContent || tc.innerText || '';
                var parts = tokenizeText(rawText);
                var html = '';
                var curSent = hasMultiSent ? 1 : (parseInt(String(tcSentIdx), 10) || 1);
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
            var firstChild = (div.firstChild);
            if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                var childSpans = div.getElementsByTagName('span');
                for (var j = 0; j < childSpans.length; j++) {
                    var span = childSpans[j];
                    if (span.classList && span.classList.contains('word')) {
                        if (!span.classList.contains('hl-mvp')) span.classList.add('hl-mvp');
                        if (!span.getAttribute('data-line-idx')) span.setAttribute('data-line-idx', '0');
                        if (!span.getAttribute('data-sentence-idx')) span.setAttribute('data-sentence-idx', '1');
                        if (!span.getAttribute('data-word')) span.setAttribute('data-word', String((span.textContent || '').trim()));
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
                div.setAttribute('data-sentence-idx', String(divSentIdx));
                div.setAttribute('data-line-idx', String(i));
                var firstChild = (div.firstChild);
                if (firstChild && firstChild.nodeType === 1 && firstChild.tagName === 'SPAN' && firstChild.classList && firstChild.classList.contains('word')) {
                    var childSpans = div.getElementsByTagName('span');
                    for (var j = 0; j < childSpans.length; j++) {
                        var span = childSpans[j];
                        if (span.classList && span.classList.contains('word')) {
                            if (!span.classList.contains('hl-mvp')) span.classList.add('hl-mvp');
                            if (!span.getAttribute('data-line-idx')) span.setAttribute('data-line-idx', String(i));
                            if (!span.getAttribute('data-sentence-idx')) span.setAttribute('data-sentence-idx', String(divSentIdx));
                            if (!span.getAttribute('data-word')) span.setAttribute('data-word', String((span.textContent || '').trim()));
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

        applyTextProvenance(tc);

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
            var srcSpans = (sc.querySelectorAll('span.word'));
            if (srcSpans.length > 0) isMvpIndexBuilt = true;
            for (var i = 0; i < srcSpans.length; i++) {
                var span = srcSpans[i];
                var chunk = (span.closest ? span.closest('.kw-sentence-chunk') : span.parentElement);
                if (chunk && chunk.classList && chunk.classList.contains('kw-sentence-chunk') && chunk.style && chunk.style.display === 'none') {
                    continue;
                }
                span.setAttribute('data-mvp-idx', String(sourceSpansArray.length));
                span.setAttribute('data-mvp-type', 'source');
                var sentIdx = span.getAttribute('data-sentence-idx');
                if (!sentIdx && chunk && chunk.getAttribute) {
                    sentIdx = chunk.getAttribute('data-sentence-idx');
                    if (sentIdx) span.setAttribute('data-sentence-idx', String(sentIdx));
                }
                sourceSpansArray.push(span);
            }
        }
        var tc = document.getElementById('translation-container');
        if (tc) {
            var tcSentIdx = tc.getAttribute('data-sentence-idx');
            var transSpans = (tc.querySelectorAll('span.word, span.hl-mvp'));
            for (var j = 0; j < transSpans.length; j++) {
                var span = transSpans[j];
                var chunk = (span.closest ? span.closest('div') : span.parentElement);
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
                        span.setAttribute('data-sentence-idx', String(spanSentIdx));
                    }
                }
                transSpansArray.push(span);
            }
        }
        window.sourceSpansArray = sourceSpansArray;
        window.transSpansArray = transSpansArray;
    }
    window.buildLcIndex = buildLcIndex;

    function wireActionToolbar() {
        var btnSame = (document.getElementById('kw-btn-same-lemma'));
        if (btnSame && !btnSame.__kw_bound) {
            btnSame.__kw_bound = true;
            btnSame.addEventListener('click', function (e) {
                e.preventDefault();
                if (typeof window.toggleHighlightSameLemma === 'function') {
                    window.toggleHighlightSameLemma();
                }
            });
            if (window.AppState && window.AppState.highlightSameLemma) {
                btnSame.classList.add('active');
            }
        }

        var btnFilter = (document.getElementById('kw-btn-filter-selected'));
        if (btnFilter && !btnFilter.__kw_bound) {
            btnFilter.__kw_bound = true;
            btnFilter.addEventListener('click', function (e) {
                e.preventDefault();
                if (typeof window.toggleFilterSelectedOnly === 'function') {
                    window.toggleFilterSelectedOnly();
                }
            });
            if (window.AppState && window.AppState.filterSelectedOnly) {
                btnFilter.classList.add('btn-filter-active');
                var table = document.getElementById('lemma-table');
                if (table) table.classList.add('kw-filter-selected-only');
            }
        }

        var btnSave = (document.getElementById('kw-btn-save'));
        if (btnSave && !btnSave.__kw_bound) {
            btnSave.__kw_bound = true;
            btnSave.addEventListener('click', function (e) {
                e.preventDefault();
                window.onSaveClick();
            });
        }

        var btnUpdate = (document.getElementById('kw-btn-update'));
        if (btnUpdate && !btnUpdate.__kw_bound) {
            btnUpdate.__kw_bound = true;
            btnUpdate.addEventListener('click', function (e) {
                e.preventDefault();
                window.onUpdateClick();
            });
        }

        var btnRetext = (document.getElementById('kw-btn-retext'));
        if (btnRetext && !btnRetext.__kw_bound) {
            btnRetext.__kw_bound = true;
            btnRetext.addEventListener('click', function (e) {
                e.preventDefault();
                window.onRetextClick();
            });
        }

        var btnReword = (document.getElementById('kw-btn-reword'));
        if (btnReword && !btnReword.__kw_bound) {
            btnReword.__kw_bound = true;
            btnReword.addEventListener('click', function (e) {
                e.preventDefault();
                window.onRewordClick();
            });
        }

        var btnExport = (document.getElementById('kw-btn-export'));
        if (btnExport && !btnExport.__kw_bound) {
            btnExport.__kw_bound = true;
            btnExport.addEventListener('click', function (e) {
                e.preventDefault();
                window.onSendToAnkiClick();
            });
        }

        var btnHand = (document.getElementById('kw-btn-hand-tool'));
        if (btnHand && !btnHand.__kw_bound) {
            btnHand.__kw_bound = true;
            btnHand.addEventListener('click', function (e) {
                e.preventDefault();
                window.onHandToolClick();
            });
        }

        var btnDelete = (document.getElementById('kw-btn-delete'));
        if (btnDelete && !btnDelete.__kw_bound) {
            btnDelete.__kw_bound = true;
            btnDelete.addEventListener('click', function (e) {
                e.preventDefault();
                window.onDeleteClick();
            });
        }

        var btnLangYes = (document.getElementById('kw-btn-lang-yes'));
        if (btnLangYes && !btnLangYes.__kw_bound) {
            btnLangYes.__kw_bound = true;
            btnLangYes.addEventListener('click', function (e) {
                e.preventDefault();
                window.onLangYes();
            });
        }

        var btnLangNo = (document.getElementById('kw-btn-lang-no'));
        if (btnLangNo && !btnLangNo.__kw_bound) {
            btnLangNo.__kw_bound = true;
            btnLangNo.addEventListener('click', function (e) {
                e.preventDefault();
                window.onLangNo();
            });
        }

        var btnLangCancel = (document.getElementById('kw-btn-lang-cancel'));
        if (btnLangCancel && !btnLangCancel.__kw_bound) {
            btnLangCancel.__kw_bound = true;
            btnLangCancel.addEventListener('click', function (e) {
                e.preventDefault();
                window.onLangCancel();
            });
        }
    }
    window.wireActionToolbar = wireActionToolbar;

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
                throw new Error('Malformed #kardenwort-bootstrap JSON: ' + (err instanceof Error ? err.message : String(err)));
            }
        }

        var sessionPayload = null;
        if (rawSession && rawSession !== '{}') {
            try {
                sessionPayload = JSON.parse(rawSession);
            } catch (err) {
                console.error('Error parsing #session-data JSON:', err);
                throw new Error('Malformed #session-data JSON: ' + (err instanceof Error ? err.message : String(err)));
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
                window.AppState = window.AppState || {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
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
                window.AppState = window.AppState || {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
                window.AppState.config = window.__CONFIG__;
            }

            if (window.AppStore && typeof window.AppStore.dispatch === 'function') {
                window.AppStore.dispatch({ type: 'INIT_SESSION', payload: bootstrapPayload || session });
                window.__CONFIG__ = window.AppStore.getState().config;
            }

            SESSION_ZID = session.session_zid || session.zid || '';
            API_TOKEN = session.api_token || '';
            LANGUAGE = session.language || (bootstrapPayload && (bootstrapPayload.language || bootstrapPayload.lang)) || '';
            TARGET_LANG = session.target_lang || session.target_language || (bootstrapPayload && (bootstrapPayload.target_language || bootstrapPayload.target_lang)) || '';
            FINGERPRINT = session.fingerprint || '';
            SERVER_ENABLED = Boolean(session.server_enabled);
            if (session.server_url) {
                SERVER_URL = session.server_url;
            }

            if (session.sentence_translation || session.translated_text) {
                window.AppState.translatedText = session.sentence_translation || session.translated_text;
                window.AppState.translated_text = window.AppState.translatedText || undefined;
            }
            if (session.source_text) {
                window.AppState.sourceText = session.source_text;
                window.AppState.source_text = session.source_text;
            }

            var sLangEl = document.getElementById('session-lang');
            if (sLangEl && LANGUAGE && !sLangEl.textContent.trim()) {
                sLangEl.textContent = LANGUAGE;
            }
            var sTargetLangEl = document.getElementById('session-target-lang');
            if (sTargetLangEl && TARGET_LANG && !sTargetLangEl.textContent.trim()) {
                sTargetLangEl.textContent = TARGET_LANG;
            }

            // Hydrate Header
            var titleEl = document.getElementById('session-title');
            if (titleEl && SESSION_ZID) {
                titleEl.textContent = 'Kardenwort Desk - ' + SESSION_ZID;
            }
            var badgeEl = document.getElementById('lang-badge');
            if (badgeEl) {
                badgeEl.textContent = (LANGUAGE || '') + ' \u2192 ' + (TARGET_LANG || '');
            }

            // Check mismatch
            var mismEl = document.getElementById('mismatch-info');
            var isMismatch = false;
            if (mismEl && (mismEl.textContent || '').trim() && (mismEl.textContent || '').trim() !== '{}') {
                try {
                    var mParsed = JSON.parse(mismEl.textContent || '{}');
                    if (mParsed && mParsed.is_mismatch) isMismatch = true;
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }
            if (!isMismatch && bootstrapPayload && bootstrapPayload.mismatch_info && bootstrapPayload.mismatch_info.is_mismatch) isMismatch = true;
            if (!isMismatch && session && session.mismatch_info && session.mismatch_info.is_mismatch) isMismatch = true;

            // Hydrate Source
            var srcEl = document.getElementById('source-container');
            if (isMismatch) {
                if (srcEl) srcEl.textContent = '';
            } else if (srcEl && !srcEl.children.length && !(srcEl.textContent || '').trim() && session.source_text) {
                srcEl.textContent = session.source_text;
            }

            // Hydrate Translation
            var transEl = document.getElementById('translation-container');
            if (isMismatch) {
                if (transEl) transEl.textContent = '';
            } else if (transEl && !transEl.children.length && !(transEl.textContent || '').trim() && session.sentence_translation) {
                transEl.textContent = session.sentence_translation;
            }

            // Hydrate Table
            var tbody = (document.querySelector('#lemma-table tbody'));
            if (isMismatch) {
                if (tbody) tbody.innerHTML = '';
            } else if (tbody && !tbody.children.length && session.data_rows && session.data_rows.length) {
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

                var hydrationBody = tbody;
                session.data_rows.forEach(/** @param {string[]} row @param {number} idx */ function (row, idx) {
                    var tr = document.createElement('tr');
                    tr.setAttribute('data-row-id', String(idx));
                    tr.setAttribute('data-token-order', String(idx));
                    var isInitSel = (window.AppState && window.AppState.selected_ids && window.AppState.selected_ids.indexOf(idx) !== -1);
                    tr.setAttribute('data-selected', String(isInitSel ? '1' : '0'));
                    tr.setAttribute('data-filter-hidden', String(isInitSel ? '0' : '1'));
                    if (isInitSel) {
                        tr.classList.add('selected', 'kw-row-selected');
                    }

                    if (SERVER_ENABLED) {
                        var tdTag = document.createElement('td');
                        tdTag.className = 'kw-tag-control';
                        var cb = document.createElement('input');
                        cb.type = 'checkbox';
                        cb.className = 'kw-tag-checkbox';
                        cb.setAttribute('data-row-id', String(idx));
                        cb.setAttribute('data-token-order', String(idx));
                        cb.onchange = function () { window.kwToggleTag(cb, idx); };
                        tdTag.appendChild(cb);
                        tr.appendChild(tdTag);
                    }

                    /** @param {string} cls @param {string} colName @param {string|Node} text @param {boolean} isEditable */
                    function makeCell(cls, colName, text, isEditable) {
                        var td = document.createElement('td');
                        td.className = (isEditable ? 'editable ' : '') + cls;
                        if (colName) td.setAttribute('data-col', String(colName));
                        var scrollDiv = document.createElement('div');
                        scrollDiv.className = 'scrollable-cell';
                        var nodeVal = text;
                        if (nodeVal && typeof nodeVal === 'object' && nodeVal.nodeType) {
                            scrollDiv.appendChild(nodeVal);
                        } else if (typeof text === 'string' && text.indexOf('skeleton-loader') !== -1) {
                            if (text.indexOf('kw-skeleton-cell') === -1) {
                                scrollDiv.innerHTML = text.replace('class="skeleton-loader', 'class="skeleton-loader kw-skeleton-cell');
                            } else {
                                scrollDiv.innerHTML = text;
                            }
                        } else {
                            scrollDiv.textContent = String(text || '');
                        }
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

                    hydrationBody.appendChild(tr);
                });
            }

            // Synchronize thead tag header with actual checkboxes
            var theadTr = (document.querySelector('#lemma-table thead tr'));
            if (theadTr) {
                var existingTagTh = (theadTr.querySelector('.kw-tag-header'));
                var hasCheckboxes = !! (document.querySelector('#lemma-table tbody .kw-tag-control, #lemma-table tbody .kw-tag-checkbox'));
                var shouldHaveTagHeader = SERVER_ENABLED && hasCheckboxes;
                if (!shouldHaveTagHeader && existingTagTh) {
                    if (existingTagTh.parentNode) existingTagTh.parentNode.removeChild(existingTagTh);
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

            if (typeof tokenizeSource === 'function') tokenizeSource();
            if (typeof tokenizeTranslation === 'function') tokenizeTranslation();
            if (typeof buildLcIndex === 'function') buildLcIndex();
            if (typeof wireMvpEvents === 'function') wireMvpEvents();
            if (typeof wireActionToolbar === 'function') wireActionToolbar();
            if (typeof window.rebindTokenSpans === 'function') window.rebindTokenSpans();
            if (typeof window.rebindTableRows === 'function') window.rebindTableRows();
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

    function isNativeAhkHost() {
        return window.location.protocol === 'file:' ||
               typeof window.ahkCall !== 'undefined' ||
               (window.external && typeof window.external.ahkCall !== 'undefined') ||
               !!(document.documentMode || window.ActiveXObject);
    }
    function checkAhkHost() {
        if (isNativeAhkHost()) {
            if (document.body && document.body.classList) {
                document.body.classList.add('kw-ahk-native-host');
            }
        }
    }
    checkAhkHost();

    /** @template {keyof HTMLElementEventMap} K @param {EventTarget} el @param {K} type @param {(this: HTMLElement, event: HTMLElementEventMap[K]) => void} fn */
    function addEvent(el, type, fn) {
        if (el.addEventListener) {
            el.addEventListener(type,  (fn), false);
        } else {
            var legacy = (el);
            if (legacy.attachEvent) legacy.attachEvent('on' + type,  (fn));
            else legacy[ ('on' + type)] = (fn);
        }
    }

    window.forceRepaint = function() {
        if (document.body) {
            return document.body.offsetHeight;
        }
        return 0;
    };

    window.__mvpInitialized = true;

    /** @param {Element|null|undefined} el @param {string} name */
    function removeClass(el, name) {
        if (el && el.classList) {
            el.classList.remove(name);
        }
    }
    /** @param {Element|null|undefined} el @param {string} name */
    function addClass(el, name) {
        if (el && el.classList) {
            el.classList.add(name);
        }
    }
    window.addClass = addClass;
    window.removeClass = removeClass;
    window.addEvent = addEvent;

    /** @param {Node|null} el */
    function isAttached(el) {
        while (el) {
            if (el === document.documentElement) return true;
            el = el.parentNode;
        }
        return false;
    }

    /** @param {string} providerName */
    function formatProviderSkeletonLabel(providerName) {
        if (!providerName) return "Loading...";
        var name = String(providerName).trim().toLowerCase();
        var map = {
            'argos': 'Argos...',
            'google': 'Google...',
            'deepl': 'DeepL...',
            'intellifiller': 'IntelliFiller...'
        };
        if (map[name]) return map[name];
        return name.charAt(0).toUpperCase() + name.slice(1) + '...';
    }

    /** @param {unknown} prov */
    function formatProvenanceTooltip(prov) {
        if (!prov) return "";
        var original = String(prov).trim();
        var p = original;
        if (p.indexOf('live:') === 0) {
            p = p.substring(5);
        } else if (p.indexOf('provider:') === 0) {
            p = p.substring(9);
        }
        var pLower = p.toLowerCase();
        if (pLower === 'argos') return 'Translated via Argos (offline)';
        if (pLower === 'google') return 'Translated via Google';
        if (pLower === 'deepl') return 'Translated via DeepL';
        if (pLower === 'lingva') return 'Translated via Lingva';
        if (pLower === 'intellifiller' || pLower === 'ai') return 'Translated via IntelliFiller (AI)';
        if (pLower === 'corpus:wordfill' || pLower === 'wordfill:corpus') {
            return 'Pre-filled from Corpus (WordFill)';
        }
        if (pLower.indexOf('corpus:wordfill:') === 0) {
            return 'Pre-filled from Corpus (ZID: ' + original.substring(16) + ')';
        }
        if (pLower === 'cached:sqlite') {
            return 'Loaded from session cache (SQLite)';
        }
        if (pLower === 'cached:file') {
            return 'Loaded from translation file cache';
        }
        if (pLower.indexOf('fallback:') === 0) {
            var pFb = original.substring(9);
            return 'Translated via fallback (' + pFb + ')';
        }
        if (original.indexOf('live:') === 0 || original.indexOf('provider:') === 0) {
            return 'Translated via ' + p.charAt(0).toUpperCase() + p.slice(1);
        }
        return original;
    }
    window.formatProvenanceTooltip = formatProvenanceTooltip;

    /** @param {unknown} translation @param {unknown=} provenance */
    function formatTranslationCellTooltip(translation, provenance) {
        var cleanTrans = String(translation || '').replace(/<[^>]+>/g, '').trim();
        if (!cleanTrans || cleanTrans.indexOf('skeleton-loader') !== -1) {
            return '';
        }
        var pTitle = formatProvenanceTooltip(provenance);
        if (pTitle) {
            return cleanTrans + ' (' + pTitle + ')';
        }
        return cleanTrans;
    }
    window.formatTranslationCellTooltip = formatTranslationCellTooltip;

    /** @param {string} text */
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

    /** @param {string} sentenceText @param {string} inflectedVal @param {number|string=} tokenOrder @param {string=} lemma */
    function formatInflectedTooltip(sentenceText, inflectedVal, tokenOrder, lemma) {
        if (!sentenceText) return (inflectedVal || "").trim();
        if (!inflectedVal) return sentenceText.trim();
        var targetIndices = {};
        if (tokenOrder !== undefined && tokenOrder !== null) {
            var tStr = String(tokenOrder).trim();
            if (tStr.indexOf('+') !== -1) {
                var parts = tStr.split('+');
                for (var i = 0; i < parts.length; i++) {
                    var num = parseInt(String(parts[i].trim()), 10);
                    if (!isNaN(num)) targetIndices[num] = true;
                }
            }
        }
        var targetWords = {};
        var lemmaClean = (lemma && String(lemma).trim()) ? String(lemma).trim().toLowerCase() : null;
        var rawComponents = String(inflectedVal || "").split(',');
        for (var c = 0; c < rawComponents.length; c++) {
            var comp = rawComponents[c].trim();
            if (!comp) continue;
            var compWords = (comp.toLowerCase().match(/[\p{L}\p{N}]+/gu) || comp.toLowerCase().match(/[a-zA-Z0-9äöüÄÖÜßа-яА-ЯёЁ]+/g) || []);
            var compFiltered = [];
            for (var j = 0; j < compWords.length; j++) {
                if (compWords[j] && !/^\d{14}$/.test(compWords[j])) {
                    compFiltered.push(compWords[j]);
                }
            }
            var isCompSlug = (comp.indexOf('+') === -1) && (comp.indexOf('-') !== -1 || comp.indexOf('_') !== -1 || /\b\d{14}\b/.test(comp));
            if (isCompSlug && lemmaClean) {
                var exactMatches = [];
                for (var m = 0; m < compFiltered.length; m++) {
                    if (compFiltered[m] === lemmaClean) exactMatches.push(compFiltered[m]);
                }
                if (exactMatches.length > 0) {
                    for (var e = 0; e < exactMatches.length; e++) targetWords[exactMatches[e]] = true;
                } else {
                    var stemMatches = [];
                    for (var s = 0; s < compFiltered.length; s++) {
                        var fw = compFiltered[s];
                        if (fw.length >= 3 && (fw.indexOf(lemmaClean) === 0 || lemmaClean.indexOf(fw) === 0)) {
                            stemMatches.push(fw);
                        }
                    }
                    if (stemMatches.length > 0) {
                        for (var sm = 0; sm < stemMatches.length; sm++) targetWords[stemMatches[sm]] = true;
                    } else {
                        for (var f = 0; f < compFiltered.length; f++) targetWords[compFiltered[f]] = true;
                    }
                }
            } else {
                for (var fwIdx = 0; fwIdx < compFiltered.length; fwIdx++) {
                    targetWords[compFiltered[fwIdx]] = true;
                }
            }
        }
        if (lemmaClean) {
            targetWords[lemmaClean] = true;
        }

        var tokens = [];
        try {
            tokens = sentenceText.split(/([\p{L}\p{N}]+)/u);
        } catch (e) {
            tokens = sentenceText.split(/([a-zA-Z0-9äöüÄÖÜßа-яА-ЯёЁ]+)/);
        }
        if (tokens.length === 1 && sentenceText.indexOf(' ') !== -1) {
            tokens = sentenceText.split(/([a-zA-Z0-9äöüÄÖÜßа-яА-ЯёЁ]+)/);
        }
        var wordIdx = 0;
        var hasIndices = Object.keys(targetIndices).length > 0;
        for (var k = 0; k < tokens.length; k++) {
            var tok = tokens[k];
            var isWord = false;
            try {
                isWord = /[\p{L}\p{N}]/u.test(tok);
            } catch (e) {
                isWord = /[a-zA-Z0-9äöüÄÖÜßа-яА-ЯёЁ]/.test(tok);
            }
            if (isWord) {
                var shouldBold = false;
                if (hasIndices && targetIndices.hasOwnProperty(wordIdx)) {
                    shouldBold = true;
                } else if (targetWords.hasOwnProperty(tok.toLowerCase())) {
                    shouldBold = true;
                }
                if (shouldBold) {
                    tokens[k] = toUnicodeBold(tok);
                }
                wordIdx++;
            }
        }
        var boldSentence = tokens.join('').trim();
        var boldHeader = toUnicodeBold(String(inflectedVal).trim());
        if (boldHeader && boldSentence) {
            return boldHeader + '\n' + boldSentence;
        }
        return boldSentence || boldHeader;
    }

    function getGrammarConfig() {
        if (typeof window !== 'undefined' && window.__CONFIG__ && window.__CONFIG__.grammar) {
            return window.__CONFIG__.grammar;
        }
        return null;
    }

    /** @param {string} lemma @param {string=} gender @param {string=} lang @param {string=} pos */
    function formatLemmaTooltip(lemma, gender, lang, pos) {
        if (!lemma) return "";
        var cleanLemma = String(lemma).trim();
        if (!cleanLemma) return "";
        var currentLang = (lang || (typeof getSessionLang === 'function' ? getSessionLang() : '') || (window.AppConfig ? window.AppConfig.language : '')).toLowerCase();
        if (currentLang === 'de') {
            var lowerLemma = cleanLemma.toLowerCase();
            var grammar = getGrammarConfig();
            var articles = (grammar && (grammar.german_articles)) || [
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
                var posParts = posStr.split(',').map(function(p) {
                    return p.trim().replace(/\.+$/, '');
                }).filter(Boolean);
                var nounTagsList = (grammar && (grammar.noun_tags)) || ["n", "noun", "propn", "subst"];
                var nonNounTagsList = (grammar && (grammar.non_noun_tags)) || [
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
                var pronouns = (grammar && (grammar.german_pronouns)) || [
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

    /** @param {string} posVal @returns {string} */
    function formatPosTooltip(posVal) {
        if (!posVal) return "";
        var pClean = String(posVal).trim();
        if (!pClean) return "";
        if (pClean.indexOf(',') !== -1) {
            var parts = pClean.split(',').map(function(p) { return formatPosTooltip(p.trim()); }).filter(Boolean);
            return parts.join(', ');
        }
        var grammar = getGrammarConfig();
        if (grammar) {
            if (grammar.pos_full_name_map && grammar.pos_full_name_map[pClean]) return grammar.pos_full_name_map[pClean];
            var posMap = grammar.pos_full_name_map;
            if (posMap) {
                if (posMap[pClean.toUpperCase()]) return posMap[pClean.toUpperCase()];
                if (posMap[pClean.toLowerCase()]) return posMap[pClean.toLowerCase()];
                var pStripped = pClean.replace(/\.+$/, '').toLowerCase();
                if (posMap[pStripped]) return posMap[pStripped];
            }
        }
        return pClean;
    }

    /** @param {string|undefined} lemma @param {string} genderCode */
    function detectGermanGenderSuffix(lemma, genderCode) {
        if (!lemma) return null;
        var w = String(lemma).trim().toLowerCase();
        if (!w) return null;

        var grammar = getGrammarConfig() || {};
        var mascSuffixes = grammar.suffixes_masculine || ["partner", "ismus", "ling", "mann", "ant", "ent", "ist", "or"];
        var femSuffixes = grammar.suffixes_feminine || ["schaft", "heit", "keit", "ung", "ion", "tät", "enz", "anz", "ik", "ei", "in"];
        var neutSuffixes = grammar.suffixes_neuter || ["chen", "lein", "ment", "tum", "um"];
        var excludedEnt = grammar.suffix_excluded_ent || ['moment', 'element', 'talent', 'patent', 'experiment', 'instrument', 'dokument', 'parlament', 'prozent'];
        var excludedIn = grammar.suffix_excluded_in || ["kinn", "sinn", "benzin", "ruin", "urin", "rubin", "cousin", "magazin", "berlin", "dublin"];

        if (genderCode === 'm') {
            for (var i = 0; i < mascSuffixes.length; i++) {
                var suf = mascSuffixes[i];
                if (suf === 'ent' && excludedEnt.some(function(ex) { return w.endsWith(ex); })) continue;
                var minLen = (suf === 'mann' || suf === 'or') ? 4 : (suf === 'partner' ? 7 : (suf === 'ismus' ? 6 : 5));
                if (w.length >= minLen && w.endsWith(suf)) {
                    return '-' + suf;
                }
            }
        } else if (genderCode === 'f') {
            for (var j = 0; j < femSuffixes.length; j++) {
                var sufF = femSuffixes[j];
                if (sufF === 'in') {
                    if (w.length > 4 && excludedIn.indexOf(w) === -1 && w.endsWith('in')) {
                        return '-in';
                    }
                } else {
                    var minLenF = (sufF === 'ik' || sufF === 'ei') ? 4 : (sufF === 'schaft' ? 6 : 5);
                    if (w.length >= minLenF && w.endsWith(sufF)) {
                        return '-' + sufF;
                    }
                }
            }
        } else if (genderCode === 'n') {
            for (var k = 0; k < neutSuffixes.length; k++) {
                var sufN = neutSuffixes[k];
                var minLenN = (sufN === 'tum' || sufN === 'um') ? 4 : 5;
                if (w.length >= minLenN && w.endsWith(sufN)) {
                    return '-' + sufN;
                }
            }
        }
        return null;
    }

    /** @param {unknown} provenance */
    function isAiProvenance(provenance) {
        if (!provenance) return false;
        var pStr = String(provenance).trim().toLowerCase();
        return pStr.indexOf('intellifiller') !== -1 || pStr === 'ai' || pStr === 'live:ai';
    }

    /** @param {string} genderVal @param {string=} lemma @param {string=} provenance */
    function formatGenderTooltip(genderVal, lemma, provenance) {
        if (!genderVal) return "";
        var gClean = String(genderVal).trim().replace(/\.+$/, '').toLowerCase();
        var grammar = getGrammarConfig() || {};
        var genderMap = grammar.gender_full_name_map || {
            "m": "Masculine", "masc": "Masculine", "masculine": "Masculine", "der": "Masculine",
            "f": "Feminine", "fem": "Feminine", "feminine": "Feminine", "die": "Feminine",
            "n": "Neuter", "neut": "Neuter", "neuter": "Neuter", "das": "Neuter"
        };
        if (!genderMap[gClean]) return "";
        var genderName = genderMap[gClean];

        var gCode = "";
        if (gClean === 'm' || gClean === 'masc' || gClean === 'masculine' || gClean === 'der') gCode = 'm';
        else if (gClean === 'f' || gClean === 'fem' || gClean === 'feminine' || gClean === 'die') gCode = 'f';
        else if (gClean === 'n' || gClean === 'neut' || gClean === 'neuter' || gClean === 'das') gCode = 'n';

        var aiFlag = isAiProvenance(provenance);
        var suffix = detectGermanGenderSuffix(lemma, gCode);

        if (aiFlag && suffix) {
            return genderName + " (AI: IntelliFiller · Suffix: " + suffix + ")";
        } else if (suffix) {
            return genderName + " (Suffix: " + suffix + ")";
        } else if (aiFlag) {
            return genderName + " (AI: IntelliFiller)";
        }
        return genderName;
    }

    /** @param {string} classVal @param {string} role */
    function formatClassificationTooltip(classVal, role) {
        if (!classVal) return "";
        var valStr = String(classVal).trim();
        if (!valStr) return "";
        if (valStr.indexOf(':') !== -1) {
            var parts = valStr.split(':');
            var prefix = parts[0].trim();
            var level = parts.slice(1).join(':').trim();
            return (prefix.charAt(0).toUpperCase() + prefix.slice(1)) + ': ' + level;
        }
        if (role) {
            var r = String(role).trim();
            if (r.toLowerCase().indexOf('classification') === 0) {
                r = r.substring(14).trim();
            }
            if (r) {
                return (r.charAt(0).toUpperCase() + r.slice(1)) + ': ' + valStr;
            }
        }
        return valStr;
    }

    /** @param {string} genderVal */
    function formatGenderBadge(genderVal) {
        if (!genderVal) return "";
        var g = String(genderVal).trim();
        if (g.indexOf('<span') !== -1) {
            var m = g.match(/\bkw-gender-(m|f|n)\b/i);
            if (m) {
                var code = m[1].toLowerCase();
                return '<span class="kw-gender kw-gender-' + code + '">' + code + '</span>';
            }
            return "";
        }
        var gLower = g.replace(/\.+$/, '').toLowerCase();
        if (gLower === 'm' || gLower === 'masc' || gLower === 'masculine' || gLower === 'der') {
            return '<span class="kw-gender kw-gender-m">m</span>';
        } else if (gLower === 'f' || gLower === 'fem' || gLower === 'feminine' || gLower === 'die') {
            return '<span class="kw-gender kw-gender-f">f</span>';
        } else if (gLower === 'n' || gLower === 'neut' || gLower === 'neuter' || gLower === 'das') {
            return '<span class="kw-gender kw-gender-n">n</span>';
        }
        return "";
    }

    window.toUnicodeBold = toUnicodeBold;
    window.formatInflectedTooltip = formatInflectedTooltip;
    window.formatLemmaTooltip = formatLemmaTooltip;
    window.formatPosTooltip = formatPosTooltip;
    window.formatGenderTooltip = formatGenderTooltip;
    window.detectGermanGenderSuffix = detectGermanGenderSuffix;
    window.formatClassificationTooltip = formatClassificationTooltip;
    window.formatGenderBadge = formatGenderBadge;
    window.formatProviderSkeletonLabel = formatProviderSkeletonLabel;

    function getTextBaseProvider() {
        var el = document.getElementById('text-base-provider');
        if (el) {
            var val = (el.textContent || el.innerText || '').trim();
            if (val) return val;
        }
        if (window.AppState && (window.AppState.activeTextProvider || window.AppState.active_text_provider)) {
            return window.AppState.activeTextProvider || window.AppState.active_text_provider;
        }
        return 'google';
    }

    function getLemmaBaseProvider() {
        var el = document.getElementById('lemma-base-provider');
        if (el) {
            var val = (el.textContent || el.innerText || '').trim();
            if (val) return val;
        }
        if (window.AppState && (window.AppState.activeLemmaProvider || window.AppState.active_lemma_provider)) {
            return window.AppState.activeLemmaProvider || window.AppState.active_lemma_provider;
        }
        return 'google';
    }

    /** @param {string|null} taskType @param {string} newProvider */
    function updateActiveProvider(taskType, newProvider) {
        if (!newProvider) return;
        var provStr = String(newProvider).trim();
        if (provStr.indexOf('live:') === 0) {
            provStr = provStr.substring(5).trim();
        }
        if (!provStr || provStr.toLowerCase() === 'default') return;
        var pLower = provStr.toLowerCase();
        var label = formatProviderSkeletonLabel(pLower);
        var capProv = pLower.charAt(0).toUpperCase() + pLower.slice(1);
        if (pLower === 'deepl') capProv = 'DeepL';
        else if (pLower === 'intellifiller') capProv = 'IntelliFiller';

        var tType = taskType ? String(taskType).trim().toLowerCase() : null;
        var isText = (!tType || tType === 'text' || tType === 'sentence' || tType === 'all');
        var isLemma = (!tType || tType === 'lemma' || tType === 'word' || tType === 'all');

        if (!window.AppState) window.AppState = {rows: {}, selectedLemmas: {}, activeTokenSelections: []};

        // 1. Synchronize base provider script tags and AppState
        if (isText) {
            var textEl = document.getElementById('text-base-provider');
            if (textEl) textEl.textContent = pLower;
            window.AppState.activeTextProvider = pLower;
            window.AppState.active_text_provider = pLower;
        }
        if (isLemma) {
            var lemmaEl = document.getElementById('lemma-base-provider');
            if (lemmaEl) lemmaEl.textContent = pLower;
            window.AppState.activeLemmaProvider = pLower;
            window.AppState.active_lemma_provider = pLower;
        }
        window.AppState.activeProvider = pLower;
        window.AppState.active_provider = pLower;

        // 2. Dynamically synchronize text translation skeletons
        var updateSkeletonHtml = /** @param {string|undefined} html */ function(html) {
            if (!html || html.indexOf('skeleton-loader') === -1) return html;
            return html.replace(/(<span[^>]*class="[^"]*skeleton-loader[^"]*"[^>]*>)([^]*?)(<[/]span>)/gi, function(match, openTag, innerText, closeTag) {
                var newOpenTag = openTag;
                if (newOpenTag.indexOf('title=') !== -1) {
                    newOpenTag = newOpenTag.replace(/title="[^"]*"/i, 'title="' + escapeHtml(label) + '"');
                } else {
                    newOpenTag = newOpenTag.replace(/<span/i, '<span title="' + escapeHtml(label) + '"');
                }
                if (newOpenTag.indexOf('data-tooltip=') !== -1) {
                    newOpenTag = newOpenTag.replace(/data-tooltip="[^"]*"/i, 'data-tooltip="' + escapeHtml(capProv) + '"');
                }
                return newOpenTag + escapeHtml(label) + closeTag;
            });
        };

        if (isText) {
            var tc = document.getElementById('translation-container');
            if (tc) {
                var isPendingTc = tc.classList.contains('skeleton-loader') ||
                                  tc.getAttribute('data-pending') === 'true' ||
                                   (tc.querySelector('.skeleton-loader')) !== null ||
                                   (tc.querySelector('[data-pending="true"]')) !== null;
                if (isPendingTc) {
                    var childSkels = (tc.querySelectorAll('.skeleton-loader, [data-pending="true"]'));
                    if (childSkels.length > 0) {
                        for (var i = 0; i < childSkels.length; i++) {
                            childSkels[i].textContent = label;
                            childSkels[i].setAttribute('title', String(label));
                            childSkels[i].setAttribute('data-tooltip', String(capProv));
                        }
                    } else if (tc.classList.contains('skeleton-loader') || tc.getAttribute('data-pending') === 'true') {
                        tc.textContent = label;
                        tc.setAttribute('title', String(label));
                        tc.setAttribute('data-tooltip', String(capProv));
                    }
                }
            }
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
                var cards = window.WorkspaceTabs.getCards();
                if (cards && cards.length > 0) {
                    for (var c = 0; c < cards.length; c++) {
                        if (cards[c].translated_text && String(cards[c].translated_text).indexOf('skeleton-loader') !== -1) {
                            cards[c].translated_text = updateSkeletonHtml(cards[c].translated_text);
                        }
                    }
                }
            }
        }

        // 3. Dynamically synchronize lemma translation skeletons in table rows
        if (isLemma) {
            var tableSkels = (document.querySelectorAll('td.col-translation .skeleton-loader, td.col-translation[data-pending="true"] .skeleton-loader, td[data-col="WordDestination"] .skeleton-loader, td[data-col="word_translation"] .skeleton-loader'));
            for (var k = 0; k < tableSkels.length; k++) {
                var tSk = tableSkels[k];
                tSk.textContent = label;
                tSk.setAttribute('title', String(label));
                tSk.setAttribute('data-tooltip', String(capProv));
            }
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
                var wCards = window.WorkspaceTabs.getCards();
                if (wCards && wCards.length > 0) {
                    for (var wc = 0; wc < wCards.length; wc++) {
                        if (wCards[wc].words) {
                            for (var wi = 0; wi < (wCards[wc].words || []).length; wi++) {
                                var wordObj = (wCards[wc].words || [])[wi];
                                if (wordObj.is_pending) {
                                    wordObj.pending_provider = pLower;
                                    wordObj.row_html = null;
                                } else if (wordObj.translation && wordObj.translation.indexOf('skeleton-loader') !== -1) {
                                    wordObj.translation = updateSkeletonHtml(wordObj.translation);
                                    wordObj.row_html = null;
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    window.getTextBaseProvider = getTextBaseProvider;
    window.getLemmaBaseProvider = getLemmaBaseProvider;
    window.updateActiveProvider = updateActiveProvider;

     var mvpBookmarks = [];
     var sourceSpansArray = [];
     var transSpansArray = [];
    var mvpN = 3;
    var isMvpIndexBuilt = false;
    var mvpRainbowMode = true;
    var mvpHighlightEnabled = true;

    var scriptEl = (document.getElementById('hl-mvp-script'));
    if (scriptEl) {
        var dr = scriptEl.getAttribute('data-rainbow');
        if (dr !== null) mvpRainbowMode = (dr === '1' || dr === 'true');
        var db = scriptEl.getAttribute('data-bookmarks');
        if (db) mvpN = parseInt(String(db), 10);
        var de = scriptEl.getAttribute('data-enabled');
        if (de !== null) mvpHighlightEnabled = (de === '1' || de === 'true');
    }
    if (window.__mvpBookmarks) mvpN = parseInt(String(window.__mvpBookmarks), 10);
    if (isNaN(mvpN) || mvpN < 1) mvpN = 3;

    function refreshBookmarkClasses() {
        for (var i = 0; i < sourceSpansArray.length; i++) {
            var span = sourceSpansArray[i];
            removeClass(span, 'hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                removeClass(span, 'hl-mvp-pin-' + k);
            }
        }
        for (var j = 0; j < transSpansArray.length; j++) {
            var span = transSpansArray[j];
            removeClass(span, 'hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                removeClass(span, 'hl-mvp-pin-' + k);
            }
        }
        var tRows = (document.querySelectorAll('#lemma-table tbody tr[data-row-id], #lemma-table tr[data-row-id]'));
        for (var rIdx = 0; rIdx < tRows.length; rIdx++) {
            var rEl = tRows[rIdx];
            removeClass(rEl, 'hl-mvp-pin');
            for (var k = 0; k < 8; k++) {
                removeClass(rEl, 'hl-mvp-pin-' + k);
            }
        }
        for (var m = 0; m < mvpBookmarks.length; m++) {
            var b = mvpBookmarks[m];
             var mappedRowIds = [];
            if (b.srcSpan) {
                addClass(b.srcSpan, 'hl-mvp-pin');
                if (mvpRainbowMode && b.slot !== undefined) {
                    addClass(b.srcSpan, 'hl-mvp-pin-' + b.slot);
                }
                var td = (typeof window.findTokenData === 'function') ? window.findTokenData(b.srcSpan) : null;
                if (!td && window.findTokenDataByVisualIdx) {
                    var wIdxAttr = b.srcSpan.getAttribute('data-word-idx');
                    if (wIdxAttr !== null) td = window.findTokenDataByVisualIdx(wIdxAttr);
                }
                if (td) {
                    if (td.atomic_row_ids && td.atomic_row_ids.length > 0) {
                        mappedRowIds = mappedRowIds.concat(td.atomic_row_ids);
                    } else if (td.row_ids && td.row_ids.length > 0) {
                        mappedRowIds = mappedRowIds.concat(td.row_ids);
                    } else if (td.atomic_id !== undefined && td.atomic_id !== null) {
                        mappedRowIds.push(td.atomic_id);
                    }
                }
            }
            if (b.transSpan) {
                addClass(b.transSpan, 'hl-mvp-pin');
                if (mvpRainbowMode && b.slot !== undefined) {
                    addClass(b.transSpan, 'hl-mvp-pin-' + b.slot);
                }
            }
            if (mappedRowIds.length > 0) {
                for (var rIdx = 0; rIdx < tRows.length; rIdx++) {
                    var rEl = tRows[rIdx];
                    var rIdStr = String(rEl.getAttribute('data-row-id'));
                    var rIdInt = parseInt(String(rIdStr), 10);
                    var matched = (mappedRowIds.indexOf(rIdInt) !== -1 || mappedRowIds.indexOf(rIdStr) !== -1);
                    if (!matched && window.getRowGroupIds) {
                        var pList = window.getRowGroupIds(rEl);
                        for (var p = 0; p < pList.length; p++) {
                            if (mappedRowIds.indexOf(parseInt(String(pList[p]), 10)) !== -1 || mappedRowIds.indexOf(pList[p]) !== -1) {
                                matched = true;
                                break;
                            }
                        }
                    }
                    if (matched) {
                        addClass(rEl, 'hl-mvp-pin');
                        if (mvpRainbowMode && b.slot !== undefined) {
                            addClass(rEl, 'hl-mvp-pin-' + b.slot);
                        }
                    }
                }
            }
        }
    }
    window.refreshBookmarkClasses = refreshBookmarkClasses;

    /** @param {number} idx @param {boolean} isSource */
    function getTargetIdx(idx, isSource) {
        var sourceArray = isSource ? sourceSpansArray : transSpansArray;
        var targetArray = isSource ? transSpansArray : sourceSpansArray;
        if (!targetArray || targetArray.length === 0) return -1;
        var span = sourceArray[idx];
        if (!span) return -1;

        var spanSentIdx = span.getAttribute('data-sentence-idx');
        var spanLineIdx = span.getAttribute('data-line-idx') || "0";

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

        // If no matching sentence target pool exists (or child tab context isolation):
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
            var l = sourceArray[sourceSentPool[i]].getAttribute('data-line-idx') || "0";
            distinctSourceLines[l] = true;
        }
        var distinctTargetLines = {};
        for (var j = 0; j < targetSentPool.length; j++) {
            var l2 = targetArray[targetSentPool[j]].getAttribute('data-line-idx') || "0";
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
                if ((sourceArray[sIdx].getAttribute('data-line-idx') || "0") === spanLineIdx) {
                    subSourceLinePool.push(sIdx);
                }
            }
            for (var ti = 0; ti < targetSentPool.length; ti++) {
                var tIdx = targetSentPool[ti];
                if ((targetArray[tIdx].getAttribute('data-line-idx') || "0") === spanLineIdx) {
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

    function wireMvpEvents() {
        if (!mvpHighlightEnabled) return;
        var ensureIndex = function() {
            if (!isMvpIndexBuilt) buildLcIndex();
        };
        var handleMouseOver = /** @this {HTMLElement} */ function() {
            if (window.__selectableTextMode) return;
            ensureIndex();
            addClass(this, 'hl-mvp-hover');
            var idxStr = this.getAttribute('data-mvp-idx');
            if (idxStr !== null && idxStr !== '') {
                var idx = parseInt(String(idxStr), 10);
                var isSource = (this.getAttribute('data-mvp-type') === 'source');
                var targetIdx = getTargetIdx(idx, isSource);
                var targetSpan = isSource ? transSpansArray[targetIdx] : sourceSpansArray[targetIdx];
                if (targetSpan) {
                    addClass(targetSpan, 'hl-mvp-hover');
                }
            }
            var rowId = null;
            if (typeof window.findTokenData === 'function') {
                var td = window.findTokenData(this);
                if (td && td.row_ids && td.row_ids.length > 0) {
                    rowId = td.row_ids[0];
                }
            }
            if (rowId === null || rowId === undefined) {
                rowId = this.getAttribute('data-row-id') || this.getAttribute('data-word-idx') || this.getAttribute('data-token-order');
            }
            if (rowId !== null && rowId !== undefined) {
                var tr = (document.querySelector('#lemma-table tbody tr[data-row-id="' + rowId + '"]'));
                if (tr) addClass(tr, 'hl-mvp-hover');
            }
            if (window.AppState && window.AppState.highlightSameLemma) {
                var wordText = (this.getAttribute('data-word') || this.textContent || '').trim().toLowerCase();
                var wordLemma = '';
                if (typeof window.findTokenData === 'function') {
                    var td = window.findTokenData(this);
                    if (td && td.lemma) wordLemma = String(td.lemma).trim().toLowerCase();
                }
                var targetLemmas = {};
                if (wordLemma) targetLemmas[wordLemma] = true;
                if (wordText) targetLemmas[wordText] = true;
                var allWords = (document.querySelectorAll('#source-container span.word'));
                for (var w = 0; w < allWords.length; w++) {
                    var otherSpan = allWords[w];
                    if (otherSpan === this) continue;
                    var otherText = (otherSpan.getAttribute('data-word') || otherSpan.textContent || '').trim().toLowerCase();
                    var otherLemma = '';
                    if (typeof window.findTokenData === 'function') {
                        var otd = window.findTokenData(otherSpan);
                        if (otd && otd.lemma) otherLemma = String(otd.lemma).trim().toLowerCase();
                    }
                    if ((otherLemma && targetLemmas[otherLemma]) || (otherText && targetLemmas[otherText])) {
                        addClass(otherSpan, 'lemma-peer-highlight');
                    }
                }
            }
        };
        var handleMouseOut = /** @this {HTMLElement} */ function() {
            if (window.__selectableTextMode) return;
            ensureIndex();
            removeClass(this, 'hl-mvp-hover');
            var idxStr = this.getAttribute('data-mvp-idx');
            if (idxStr !== null && idxStr !== '') {
                var idx = parseInt(String(idxStr), 10);
                var isSource = (this.getAttribute('data-mvp-type') === 'source');
                var targetIdx = getTargetIdx(idx, isSource);
                var targetSpan = isSource ? transSpansArray[targetIdx] : sourceSpansArray[targetIdx];
                if (targetSpan) {
                    removeClass(targetSpan, 'hl-mvp-hover');
                }
            }
            var rowId = null;
            if (typeof window.findTokenData === 'function') {
                var td = window.findTokenData(this);
                if (td && td.row_ids && td.row_ids.length > 0) {
                    rowId = td.row_ids[0];
                }
            }
            if (rowId === null || rowId === undefined) {
                rowId = this.getAttribute('data-row-id') || this.getAttribute('data-word-idx') || this.getAttribute('data-token-order');
            }
            if (rowId !== null && rowId !== undefined) {
                var tr = (document.querySelector('#lemma-table tbody tr[data-row-id="' + rowId + '"]'));
                if (tr) removeClass(tr, 'hl-mvp-hover');
            }
            if (window.AppState && window.AppState.highlightSameLemma) {
                if (typeof window.updateBidirectionalHighlights === 'function') {
                    window.updateBidirectionalHighlights();
                } else {
                    var peers = (document.querySelectorAll('#source-container span.word.lemma-peer-highlight'));
                    for (var p = 0; p < peers.length; p++) {
                        removeClass(peers[p], 'lemma-peer-highlight');
                    }
                }
            }
        };
        var handleClick = /** @param {MouseEvent} e @this {HTMLElement} */ function(e) {
            if (window.__selectableTextMode) return;
            ensureIndex();
            e = (e || window.event);
            var btn = (e.button !== undefined) ? e.button : e.which;
            if (btn !== 0 && btn !== 1) return;
            var isSource = (this.getAttribute('data-mvp-type') === 'source');
            if (isSource) return;

            var idxStr = this.getAttribute('data-mvp-idx');
            if (idxStr === null || idxStr === '') return;
            var idx = parseInt(String(idxStr), 10);
            var isSource = (this.getAttribute('data-mvp-type') === 'source');
            var bKey = (isSource ? 's' : 't') + idxStr;
            var bIdx = -1;
            for (var i = 0; i < mvpBookmarks.length; i++) {
                if (mvpBookmarks[i].srcSpan === this || mvpBookmarks[i].transSpan === this || mvpBookmarks[i].idx === bKey) { bIdx = i; break; }
            }
            if (bIdx !== -1) {
                var entry = mvpBookmarks[bIdx];
                if (entry.srcSpan) removeClass(entry.srcSpan, 'hl-mvp-hover');
                if (entry.transSpan) removeClass(entry.transSpan, 'hl-mvp-hover');
                var sSpan = entry.srcSpan || (isSource ? this : null);
                if (sSpan && typeof window.findTokenData === 'function') {
                    var sTd = window.findTokenData(sSpan);
                    if (sTd) {
                        var sRowIds = (sTd.atomic_row_ids !== undefined && sTd.atomic_row_ids !== null)
                            ? sTd.atomic_row_ids.slice()
                            : ((sTd.row_ids && sTd.row_ids.slice) ? sTd.row_ids.slice() : []);
                        sRowIds.forEach(function(id) { window.toggleRowSelection(id, false); });
                        if (typeof window.removeTokenFromActiveSelections === 'function') window.removeTokenFromActiveSelections(sSpan);
                        window.syncSelectionsToCardsAndState();
                    }
                }
                mvpBookmarks.splice(bIdx, 1);
            } else {
                var targetIdx = getTargetIdx(idx, isSource);
                var srcSpan = isSource ? sourceSpansArray[idx] : sourceSpansArray[targetIdx];
                var transSpan = isSource ? transSpansArray[targetIdx] : transSpansArray[idx];
                while (mvpBookmarks.length >= mvpN) {
                    var oldest = (mvpBookmarks.shift());
                    if (oldest.srcSpan) removeClass(oldest.srcSpan, 'hl-mvp-hover');
                    if (oldest.transSpan) removeClass(oldest.transSpan, 'hl-mvp-hover');
                }
                var slot = 0;
                if (mvpRainbowMode) {
                    var usedSlots = {};
                    for (var i = 0; i < mvpBookmarks.length; i++) {
                        if (mvpBookmarks[i].slot !== undefined) {
                            usedSlots[mvpBookmarks[i].slot] = true;
                        }
                    }
                    for (var s = 0; s < mvpN; s++) {
                        if (!usedSlots[s]) {
                            slot = s;
                            break;
                        }
                    }
                }
                mvpBookmarks.push({ idx: bKey, srcSpan: srcSpan, transSpan: transSpan, slot: slot });
                var activeSrcSpan = srcSpan || (isSource ? this : null);
                if (activeSrcSpan && typeof window.findTokenData === 'function') {
                    var sTd = window.findTokenData(activeSrcSpan);
                    if (sTd) {
                        var sRowIds = (sTd.atomic_row_ids !== undefined && sTd.atomic_row_ids !== null)
                            ? sTd.atomic_row_ids.slice()
                            : ((sTd.row_ids && sTd.row_ids.slice) ? sTd.row_ids.slice() : []);
                        sRowIds.forEach(function(id) { window.toggleRowSelection(id, true); });
                        if (typeof window.addTokenToActiveSelections === 'function') window.addTokenToActiveSelections(activeSrcSpan);
                        window.syncSelectionsToCardsAndState();
                    }
                }
                // Fallback row selection if sTd was not found
                if (isSource) {
                    var rowId = null;
                    if (typeof window.findTokenData === 'function') {
                        var td = window.findTokenData(this);
                        if (td && td.row_ids && td.row_ids.length > 0) rowId = td.row_ids[0];
                    }
                    if (rowId === null || rowId === undefined) {
                        rowId = this.getAttribute('data-row-id') || this.getAttribute('data-word-idx') || this.getAttribute('data-token-order') || 0;
                    }
                    var rStr = String(rowId);
                    window.toggleRowSelection(rStr, true);
                    var tr = (document.querySelector('#lemma-table tbody tr[data-row-id="' + rStr + '"]'));
                    if (tr) addClass(tr, 'kw-row-selected');
                }
            }
            refreshBookmarkClasses();
        };

        var sc = document.getElementById('source-container');
        if (sc) {
            var srcSpans = sc.getElementsByTagName('span');
            for (var i = 0; i < srcSpans.length; i++) {
                var span = srcSpans[i];
                if (span.classList && span.classList.contains('word')) {
                    if (span.getAttribute('data-mvp-wired')) continue;
                    span.setAttribute('data-mvp-wired', '1');
                    addEvent(span, 'mouseover', handleMouseOver);
                    addEvent(span, 'mouseout', handleMouseOut);
                }
            }
        }
        var tc = document.getElementById('translation-container');
        if (tc) {
            var transSpans = tc.getElementsByTagName('span');
            for (var j = 0; j < transSpans.length; j++) {
                var span = transSpans[j];
                if (span.classList && (span.classList.contains('hl-mvp') || span.classList.contains('word'))) {
                    if (span.getAttribute('data-mvp-wired')) continue;
                    span.setAttribute('data-mvp-wired', '1');
                    addEvent(span, 'mouseover', handleMouseOver);
                    addEvent(span, 'mouseout', handleMouseOut);
                    addEvent(span, 'click', handleClick);
                }
            }
        }
    }
    window.wireMvpEvents = wireMvpEvents;

    /** @param {HTMLElement} span */
    function isTokenPinnedInMVP(span) {
        if (!span || !mvpBookmarks || mvpBookmarks.length === 0) return false;
        for (var i = 0; i < mvpBookmarks.length; i++) {
            if (mvpBookmarks[i].srcSpan === span || mvpBookmarks[i].transSpan === span) return true;
        }
        if (typeof window.findRelatedTokenSpans === 'function') {
            var rel = window.findRelatedTokenSpans(span);
            for (var r = 0; r < rel.length; r++) {
                for (var i = 0; i < mvpBookmarks.length; i++) {
                    if (mvpBookmarks[i].srcSpan === rel[r] || mvpBookmarks[i].transSpan === rel[r]) return true;
                }
            }
        }
        return false;
    }

    /** @param {HTMLElement} span */
    function unpinTokenFromMVP(span) {
        if (!span || !mvpBookmarks || mvpBookmarks.length === 0) return false;
        var targets = [span];
        if (typeof window.findRelatedTokenSpans === 'function') {
            targets = targets.concat(window.findRelatedTokenSpans(span));
        }
        var removed = false;
        for (var i = mvpBookmarks.length - 1; i >= 0; i--) {
            var b = mvpBookmarks[i];
            if ((b.srcSpan && targets.indexOf(b.srcSpan) !== -1) || (b.transSpan && targets.indexOf(b.transSpan) !== -1)) {
                if (b.srcSpan) removeClass(b.srcSpan, 'hl-mvp-hover');
                if (b.transSpan) removeClass(b.transSpan, 'hl-mvp-hover');
                mvpBookmarks.splice(i, 1);
                removed = true;
            }
        }
        if (removed) {
            refreshBookmarkClasses();
        }
        return removed;
    }

    /** @param {HTMLElement} span */
    function pinTokenToMVP(span) {
        if (!span || !mvpHighlightEnabled) return false;
        if (!isMvpIndexBuilt) buildLcIndex();
        var idxStr = span.getAttribute('data-mvp-idx');
        if (idxStr === null || idxStr === '') return false;
        var idx = parseInt(String(idxStr), 10);
        var isSource = (span.getAttribute('data-mvp-type') === 'source');
        var bKey = (isSource ? 's' : 't') + idxStr;

        for (var i = 0; i < mvpBookmarks.length; i++) {
            if (mvpBookmarks[i].srcSpan === span || mvpBookmarks[i].transSpan === span || mvpBookmarks[i].idx === bKey) {
                return false;
            }
        }
        if (typeof window.findRelatedTokenSpans === 'function') {
            var related = window.findRelatedTokenSpans(span);
            for (var r = 0; r < related.length; r++) {
                for (var i = 0; i < mvpBookmarks.length; i++) {
                    if (mvpBookmarks[i].srcSpan === related[r] || mvpBookmarks[i].transSpan === related[r]) return false;
                }
            }
        }

        var targetIdx = getTargetIdx(idx, isSource);
        var srcSpan = isSource ? sourceSpansArray[idx] : sourceSpansArray[targetIdx];
        var transSpan = isSource ? transSpansArray[targetIdx] : transSpansArray[idx];

        while (mvpBookmarks.length >= mvpN) {
            var oldest = (mvpBookmarks.shift());
            if (oldest.srcSpan) removeClass(oldest.srcSpan, 'hl-mvp-hover');
            if (oldest.transSpan) removeClass(oldest.transSpan, 'hl-mvp-hover');
        }

        var slot = 0;
        if (mvpRainbowMode) {
            var usedSlots = {};
            for (var i = 0; i < mvpBookmarks.length; i++) {
                if (mvpBookmarks[i].slot !== undefined) {
                    usedSlots[mvpBookmarks[i].slot] = true;
                }
            }
            for (var s = 0; s < mvpN; s++) {
                if (!usedSlots[s]) {
                    slot = s;
                    break;
                }
            }
        }

        mvpBookmarks.push({ idx: bKey, srcSpan: srcSpan, transSpan: transSpan, slot: slot });
        refreshBookmarkClasses();
        return true;
    }

    window.isTokenPinnedInMVP = isTokenPinnedInMVP;
    window.unpinTokenFromMVP = unpinTokenFromMVP;
    window.pinTokenToMVP = pinTokenToMVP;

    window.clearMVPBookmarks = function() {
        var cleared = false;
        if (mvpBookmarks && mvpBookmarks.length > 0) {
            cleared = true;
            for (var i = 0; i < mvpBookmarks.length; i++) {
                var entry = mvpBookmarks[i];
                try {
                    if (entry.srcSpan && isAttached(entry.srcSpan)) {
                        removeClass(entry.srcSpan, 'hl-mvp-hover');
                    }
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                try {
                    if (entry.transSpan && isAttached(entry.transSpan)) {
                        removeClass(entry.transSpan, 'hl-mvp-hover');
                    }
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }
            mvpBookmarks = [];
            refreshBookmarkClasses();
        }
        return cleared;
    };

    window.getBookmarkIndices = function() {
        var indices = [];
        if (mvpBookmarks) {
            for (var i = 0; i < mvpBookmarks.length; i++) {
                indices.push(mvpBookmarks[i].idx);
            }
        }
        return indices.join(',');
    };

    window.restoreBookmarksByIndices = function(indicesStr) {
        mvpBookmarks = [];
        if (!indicesStr) {
            refreshBookmarkClasses();
            return;
        }
        var indices = indicesStr.split(',');
        if (!isMvpIndexBuilt) buildLcIndex();
        for (var i = 0; i < indices.length; i++) {
            var bKey = indices[i];
            if (bKey === '') continue;
            var prefix = bKey.charAt(0);
            var idx, isSource;
            if (prefix === 's' || prefix === 't') {
                idx = parseInt(String(bKey.substring(1)), 10);
                isSource = (prefix === 's');
            } else {
                idx = parseInt(String(bKey), 10);
                isSource = true;
            }
            var targetIdx = getTargetIdx(idx, isSource);
            var srcSpan = isSource ? sourceSpansArray[idx] : sourceSpansArray[targetIdx];
            var transSpan = isSource ? transSpansArray[targetIdx] : transSpansArray[idx];
            var slot = 0;
            if (mvpRainbowMode) {
                var usedSlots = {};
                for (var m = 0; m < mvpBookmarks.length; m++) {
                    if (mvpBookmarks[m].slot !== undefined) {
                        usedSlots[mvpBookmarks[m].slot] = true;
                    }
                }
                for (var s = 0; s < mvpN; s++) {
                    if (!usedSlots[s]) {
                        slot = s;
                        break;
                    }
                }
            }
            mvpBookmarks.push({ idx: bKey, srcSpan: srcSpan, transSpan: transSpan, slot: slot });
        }
        refreshBookmarkClasses();
    };

    window.__selectableTextMode = false;
    window.__persistentSelectableMode = false;

    window.setSelectableTextMode = function(active, isPersistent) {
        if (isPersistent !== undefined) {
            window.__persistentSelectableMode = !!isPersistent;
        }
        window.__selectableTextMode = !!active;
        if (active) {
            if (document.body && document.body.classList) document.body.classList.add('text-selection-mode-active');
        } else {
            if (document.body && document.body.classList) document.body.classList.remove('text-selection-mode-active');
        }
    };

    addEvent(window, 'keydown', function(e) {
        e = (e || window.event);
        if (e.key === 'Alt' || e.keyCode === 18) {
            if (!window.__selectableTextMode) {
                window.setSelectableTextMode(true, false);
            }
        }
    });

    addEvent(window, 'keyup', function(e) {
        e = (e || window.event);
        if (e.key === 'Alt' || e.keyCode === 18) {
            if (!window.__persistentSelectableMode) {
                window.setSelectableTextMode(false, false);
            }
        }
    });

    addEvent(window, 'blur', function() {
        if (!window.__persistentSelectableMode) {
            window.setSelectableTextMode(false, false);
        }
    });

    window.rebindMVPBookmarks = function() {
        tokenizeTranslation();
        buildLcIndex();
        wireMvpEvents();
        if (mvpBookmarks && mvpBookmarks.length > 0) {
            for (var i = 0; i < mvpBookmarks.length; i++) {
                var entry = mvpBookmarks[i];
                var bKey = entry.idx;
                if (!bKey) continue;
                var prefix = bKey.charAt(0);
                var idx, isSource;
                if (prefix === 's' || prefix === 't') {
                    idx = parseInt(String(bKey.substring(1)), 10);
                    isSource = (prefix === 's');
                } else {
                    idx = parseInt(String(bKey), 10);
                    isSource = true;
                }
                var targetIdx = getTargetIdx(idx, isSource);
                entry.srcSpan = isSource ? sourceSpansArray[idx] : sourceSpansArray[targetIdx];
                entry.transSpan = isSource ? transSpansArray[targetIdx] : transSpansArray[idx];
            }
            refreshBookmarkClasses();
        }
    };

    var isInitialized = false;
    function init() {
        if (isInitialized) return;
        isInitialized = true;
        if (window.invalidateStreamMembership) window.invalidateStreamMembership();
        window.__deskInstanceId = (window.__deskInstanceId || 0) + 1;
        var myDeskInstanceId = window.__deskInstanceId;
        checkAhkHost();
        if (window.SourceViewer && typeof window.SourceViewer.mount === 'function') {
            window.SourceViewer.mount('#source-container');
        }
        if (window.TranslationView && typeof window.TranslationView.mount === 'function') {
            window.TranslationView.mount('#translation-container');
        }
        if (window.LemmaTable && typeof window.LemmaTable.mount === 'function') {
            window.LemmaTable.mount('#lemma-table');
        }
        if (typeof hydrateSession === 'function') {
            hydrateSession();
        }

         var tableRows = [];
         var selectedRowIdsMap = {};
         var selectedOrphanWordIdxsMap = {};
         var initialHighlights = {};
        var hasHighlightCol = false;
         var lastClickedRowId = null;
         var focusedRowId = null;
         var deltas = [];
         var acknowledgedValues = {};
        var membershipRevision = 0;
         var historyStack = [];
        var historyIndex = -1;
         var touchedCells = {};
         var lastClickedCell = null;
         var lastHoveredCell = null;
        var isDragSelecting = false;
        var dragStartX = 0;
        var dragStartY = 0;
        var dragStartRowId = null;
        var dragLastRowId = null;
        var dragStartVisualIdx = -1;
        var dragLastVisualIdx = -1;
        var lastClickedVisualIdx = -1;
        var isShiftClick = false;
        var isCtrlKey = false;
         var mousedownTargetRow = null;
        var dragSelectMode = true;
        var isTokenDragSelecting = false;
        var tokenDragMode = true;
        var dragOccurred = false;
        var justFinishedDrag = false;
        var tokenDragStartIdx = -1;
        var tokenDragLastIdx = -1;
         var initialSelectedMap = null;
         var mousedownTargetSpan = null;
        var isRmbDragFlipping = false;
        var rmbFlipMode = true;
         var initialFlippedMap = null;

        var pos_display_order_json;
        var POS_DISPLAY_ORDER_MAP = ((window.__CONFIG__ && window.__CONFIG__.pos_display_order) || {"art.": 0, "pron.": 1, "det.": 2, "prep.": 3});
        if (!POS_DISPLAY_ORDER_MAP || POS_DISPLAY_ORDER_MAP['art.'] === undefined) {
            POS_DISPLAY_ORDER_MAP = (window.__CONFIG__ && window.__CONFIG__.pos_display_order) || {"art.": 0, "pron.": 1, "det.": 2, "prep.": 3};
        }
        window.POS_DISPLAY_ORDER_MAP = POS_DISPLAY_ORDER_MAP;
         var tokenMap = [];
        try {
            var tokenMapEl = (document.getElementById('token-map'));
            if (!tokenMapEl) throw new Error('Missing token-map element');
            var jsonStr = tokenMapEl.text || tokenMapEl.textContent || tokenMapEl.innerHTML || "[]";
            tokenMap = JSON.parse(jsonStr);
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        var isProgressive = false;
        try {
            var progEl = document.getElementById('display-mode');
            if (progEl && (progEl.textContent || progEl.innerText).trim() === 'progressive') {
                isProgressive = true;
            }
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        var initialSameLemma = (window.__CONFIG__ && window.__CONFIG__.highlight_same_lemma) ? true : false;
        try {
            var lsVal = localStorage.getItem('kw_highlight_same_lemma');
            if (lsVal !== null) initialSameLemma = (lsVal === 'true');
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        var initialFilterSelectedOnly = (window.__CONFIG__ && window.__CONFIG__.filter_selected_only) ? true : false;
        try {
            var lsFilterVal = localStorage.getItem('kw_filter_selected_only');
            if (lsFilterVal !== null) initialFilterSelectedOnly = (lsFilterVal === 'true');
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        var existingState = window.AppState || {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
        window.AppState = existingState;
        if (!existingState.rows) existingState.rows = {};
        existingState.selectedLemmas = {};
        var selectionCards = document.getElementById('sentence-cards');
        var initialCards = selectionCards ? JSON.parse(selectionCards.textContent || '[]') : [];
        initialCards.forEach(function(card) {
            (card.words || []).forEach(function(word) {
                if (String(word.selected) === '1') existingState.selectedLemmas[String(word.lemma || '').trim().toLowerCase()] = true;
            });
        });
        existingState.activeTokenSelections = [];
        existingState.highlightSameLemma = initialSameLemma;
        if (existingState.sourceText === undefined) existingState.sourceText = null;
        if (existingState.translatedText === undefined) existingState.translatedText = null;
        if (existingState.textTranslationStatus === undefined) existingState.textTranslationStatus = null;
        if (existingState.textTranslationFailed === undefined) existingState.textTranslationFailed = false;
        if (existingState.textProvenance === undefined) existingState.textProvenance = null;
        if (existingState.stage === undefined) existingState.stage = null;
        if (existingState.isFinished === undefined) existingState.isFinished = false;
        existingState.filterSelectedOnly = initialFilterSelectedOnly;
        existingState.applyDeltas = function(data) {
            if (data.shared_selection && window.applySharedSelection) window.applySharedSelection(data.shared_selection);
                if (!data) return;

                var isTerminal = (data.stage === 'finished' || data.status === 'failed' || data.status === 'partial_persisted');
                if (isTerminal) {
                    window.AppState.isFinished = true;
                    try {
                        if (document.body) {
                            document.body.setAttribute('data-worker-status', 'finished');
                        }
                    } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                }

                if (data.stage) {
                    window.AppState.stage = data.stage;
                }
                var rawProv = data.provider || data.provider_name || data.active_provider;
                var rawTextProv = data.active_text_provider || data.activeTextProvider;
                var rawLemmaProv = data.active_lemma_provider || data.activeLemmaProvider;
                if (data.status) {
                    if (!rawTextProv && (data.status.active_text_provider || data.status.activeTextProvider)) {
                        rawTextProv = data.status.active_text_provider || data.status.activeTextProvider;
                    }
                    if (!rawLemmaProv && (data.status.active_lemma_provider || data.status.activeLemmaProvider)) {
                        rawLemmaProv = data.status.active_lemma_provider || data.status.activeLemmaProvider;
                    }
                    if (!rawProv && (data.status.active_provider || data.status.activeProvider)) {
                        rawProv = data.status.active_provider || data.status.activeProvider;
                    }
                }
                if (typeof window.updateActiveProvider === 'function') {
                    if (rawTextProv) window.updateActiveProvider('text', rawTextProv);
                    if (rawLemmaProv) window.updateActiveProvider('lemma', rawLemmaProv);
                    if (rawProv && !rawTextProv && !rawLemmaProv) window.updateActiveProvider(null, rawProv);
                } else if (rawProv) {
                    var provStr = String(rawProv).trim();
                    if (provStr.indexOf('live:') === 0) {
                        provStr = provStr.substring(5).trim();
                    }
                    if (provStr && provStr.toLowerCase() !== 'default') {
                        var pLower = provStr.toLowerCase();
                        var capProv = pLower.charAt(0).toUpperCase() + pLower.slice(1);
                        if (pLower === 'deepl') capProv = 'DeepL';
                        else if (pLower === 'intellifiller') capProv = 'IntelliFiller';
                        var label = capProv + '...';
                        var skels = (document.querySelectorAll('.skeleton-loader'));
                        for (var ski = 0; ski < skels.length; ski++) {
                            var skEl = skels[ski];
                            skEl.textContent = label;
                            skEl.setAttribute('title', String(label));
                            skEl.setAttribute('data-tooltip', String(capProv));
                        }
                    }
                }
                if (data.textTranslationStatus !== undefined) {
                    window.AppState.textTranslationStatus = data.textTranslationStatus;
                } else if (data.text_translation_status !== undefined) {
                    window.AppState.textTranslationStatus = data.text_translation_status;
                }
                if (data.textTranslationFailed !== undefined) {
                    window.AppState.textTranslationFailed = Boolean(data.textTranslationFailed);
                } else if (data.text_translation_failed !== undefined) {
                    window.AppState.textTranslationFailed = Boolean(data.text_translation_failed);
                } else if (window.AppState.textTranslationStatus === 'failed') {
                    window.AppState.textTranslationFailed = true;
                }

                var incomingTextProv = data.textProvenance !== undefined ? data.textProvenance : (data.text_provenance !== undefined ? data.text_provenance : (data.provenance !== undefined ? data.provenance : undefined));
                if (incomingTextProv !== undefined && incomingTextProv !== null) {
                    var currentTextProv = window.AppState.textProvenance;
                    var isCurrentLive = currentTextProv && typeof currentTextProv === 'string' && currentTextProv.indexOf('live:') === 0;
                    if (!(isCurrentLive && incomingTextProv === 'cached:sqlite')) {
                        window.AppState.textProvenance = incomingTextProv;
                    }
                }

                var updated = false;

                var src = (data.sourceText !== undefined && data.sourceText !== "") ? data.sourceText : ((data.source_text !== undefined && data.source_text !== "") ? data.source_text : null);
                if (src !== null) {
                    window.AppState.sourceText = src;

                }

                var trans = (data.translatedText !== undefined && data.translatedText !== "") ? data.translatedText : ((data.translated_text !== undefined && data.translated_text !== "") ? data.translated_text : ((data.Translation !== undefined && data.Translation !== "") ? data.Translation : null));
                if (trans === null && data.rows && data.rows["0"]) {
                    var r0 = data.rows["0"];
                    if (r0.Translation) trans = r0.Translation;
                    else if (r0.SentenceDestination) trans = r0.SentenceDestination;
                    else if (r0.sentence_destination) trans = r0.sentence_destination;
                }
                if (trans !== null) {
                    window.AppState.translatedText = trans;
                    if (window.AppView.renderTranslatedText(data.stage)) updated = true;
                } else if (document.getElementById('translation-container') && ( (requireElement('translation-container').querySelector('[data-pending="true"]')) || window.AppState.textTranslationFailed || data.stage === 'translated_text' || isTerminal)) {
                    if (window.AppView.renderTranslatedText(data.stage)) updated = true;
                }

                if (data.sentences && window.WorkspaceTabs && window.WorkspaceTabs.updateSentences) {
                    window.WorkspaceTabs.updateSentences(data.sentences);
                }

                var incomingRowProv = data.row_provenances || data.rowProvenances;
                if (incomingRowProv) {
                    for (var rKey in incomingRowProv) {
                        if (incomingRowProv.hasOwnProperty(rKey)) {
                            var pVal = incomingRowProv[rKey];
                            if (!window.AppState.rows[rKey]) window.AppState.rows[rKey] = {};
                            window.AppState.rows[rKey].provenance = pVal;
                            for (var existingId in window.AppState.rows) {
                                var exRow = window.AppState.rows[existingId];
                                if (exRow && (String(exRow.token_order) === String(rKey) || String(existingId) === String(rKey))) {
                                    exRow.provenance = pVal;
                                }
                            }
                        }
                    }
                }

                var rowsData = null;
                if (data.rows) {
                    rowsData = data.rows;
                } else if (data.stage) {
                    rowsData = data.rows;
                } else {
                    rowsData = data;
                }

                if (rowsData) {
                    for (var rowId in rowsData) {
                        if (rowsData.hasOwnProperty(rowId)) {
                            if (!window.AppState.rows[rowId]) window.AppState.rows[rowId] = {};
                            var delta = rowsData[rowId];
                            Object.assign(window.AppState.rows[rowId], delta);
                            if (delta.provenance) {
                                window.AppState.rows[rowId].provenance = delta.provenance;
                            } else if (delta._provenance) {
                                window.AppState.rows[rowId].provenance = delta._provenance;
                            } else if (delta.transProvenance) {
                                window.AppState.rows[rowId].provenance = delta.transProvenance;
                            }
                        }
                    }
                    for (var rowId in rowsData) {
                        if (rowsData.hasOwnProperty(rowId)) {
                            if (window.AppView.renderRow(rowId, data.stage)) {
                                updated = true;
                            }
                        }
                    }
                }
                /** @param {Record<string,VocabularyRow>} rowsData @param {UpdatePayload} rawPayload */
                function applyCardProgressiveUpdate(rowsData, rawPayload) {
                    if (!rowsData) return;
                    var wCards = (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function')
                        ? window.WorkspaceTabs.getCards()
                        : (typeof getCardModels === 'function' ? getCardModels() : null);
                    if (!wCards || wCards.length === 0) return;
                    var deltasByTokenOrder = {};
                    var deltasByRowId = {};
                    for (var rk in rowsData) {
                        if (rowsData.hasOwnProperty(rk)) {
                            var d = rowsData[rk];
                            if (d) {
                                if (d.token_order !== undefined && d.token_order !== null && String(d.token_order) !== "") {
                                    deltasByTokenOrder[String(d.token_order)] = d;
                                } else if (rk !== undefined && rk !== null && String(rk) !== "") {
                                    deltasByTokenOrder[String(rk)] = d;
                                }
                                if (d.row_id !== undefined && d.row_id !== null && String(d.row_id) !== "") {
                                    deltasByRowId[String(d.row_id)] = d;
                                }
                                deltasByRowId[String(rk)] = d;
                            }
                        }
                    }
                    for (var ci = 0; ci < wCards.length; ci++) {
                        var cWords = wCards[ci].words;
                        if (cWords) {
                            for (var cwi = 0; cwi < cWords.length; cwi++) {
                                var item = cWords[cwi];
                                if (item.token_order !== undefined && item.token_order !== null && item.row_id !== undefined && item.row_id !== null) {
                                    var sTok = String(item.token_order);
                                    var sRow = String(item.row_id);
                                    if (deltasByTokenOrder[sTok] && !deltasByRowId[sRow]) {
                                        deltasByRowId[sRow] = deltasByTokenOrder[sTok];
                                    }
                                    if (deltasByRowId[sRow] && !deltasByTokenOrder[sTok]) {
                                        deltasByTokenOrder[sTok] = deltasByRowId[sRow];
                                    }
                                }
                            }
                        }
                    }
                    var activeCard = (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function')
                        ? window.WorkspaceTabs.getActiveCard()
                        : null;
                    var activeSentIdx = (activeCard && activeCard.sentence_idx !== undefined && activeCard.sentence_idx !== null)
                        ? String(activeCard.sentence_idx)
                        : null;
                    var isGlobalUpdate = Boolean(rawPayload && (rawPayload.sentences || rawPayload.stage === 'translating' || rawPayload.stage === 'finished' || rawPayload.is_finished || rawPayload.is_global || rawPayload.all_sentences));
                    for (var wc = 0; wc < wCards.length; wc++) {
                        var cardObj = wCards[wc];
                        var cardSentIdx = (cardObj && cardObj.sentence_idx !== undefined && cardObj.sentence_idx !== null)
                            ? String(cardObj.sentence_idx)
                            : (cardObj && cardObj.index !== undefined ? String(cardObj.index) : String(wc));
                        if (!isGlobalUpdate && activeSentIdx !== null && activeSentIdx !== "0" && cardSentIdx !== "0" && cardSentIdx !== activeSentIdx) {
                            continue;
                        }
                        var cardWords = cardObj.words;
                        if (cardWords) {
                            for (var ww = 0; ww < cardWords.length; ww++) {
                                var cw = cardWords[ww];
                                var matchingDelta = null;
                                if (cw.token_order !== undefined && cw.token_order !== null && deltasByTokenOrder[String(cw.token_order)]) {
                                    matchingDelta = deltasByTokenOrder[String(cw.token_order)];
                                } else if (cw.row_id !== undefined && cw.row_id !== null && deltasByRowId[String(cw.row_id)]) {
                                    var candDelta = deltasByRowId[String(cw.row_id)];
                                    if (candDelta) {
                                        var candLem = (candDelta.lemma || '').trim().toLowerCase();
                                        var cwLem = (cw.lemma || '').trim().toLowerCase();
                                        var candOrd = (candDelta.token_order !== undefined && candDelta.token_order !== null) ? String(candDelta.token_order) : null;
                                        if ((candOrd && String(cw.token_order) === candOrd) || (candLem && cwLem && candLem === cwLem) || !candLem) {
                                            matchingDelta = candDelta;
                                        }
                                    }
                                }
                                if (matchingDelta) {
                                    var mTrans = (matchingDelta.trans !== undefined && matchingDelta.trans !== "") ? matchingDelta.trans : ((matchingDelta.WordDestination !== undefined && matchingDelta.WordDestination !== "") ? matchingDelta.WordDestination : matchingDelta.word_translation);
                                    if (mTrans !== undefined && mTrans !== "") cw.translation = mTrans;
                                    if (matchingDelta.lemma) cw.lemma = matchingDelta.lemma;
                                    if (matchingDelta.ipa) cw.ipa = matchingDelta.ipa;
                                    var mMorph = (matchingDelta.morph !== undefined && matchingDelta.morph !== "") ? matchingDelta.morph : ((matchingDelta.morphology !== undefined && matchingDelta.morphology !== "") ? matchingDelta.morphology : matchingDelta.WordSourceMorphologyAI);
                                    if (mMorph !== undefined && mMorph !== "") cw.morphology = mMorph;
                                    if (matchingDelta.pos !== undefined) cw.pos = matchingDelta.pos;
                                    else if (matchingDelta.WordSourcePOS !== undefined) cw.pos = matchingDelta.WordSourcePOS;
                                    if (matchingDelta.gender !== undefined) cw.gender = matchingDelta.gender;
                                    else if (matchingDelta.WordSourceGender !== undefined) cw.gender = matchingDelta.WordSourceGender;
                                    var newProv = (matchingDelta.provenance !== undefined && matchingDelta.provenance !== null && String(matchingDelta.provenance).trim() !== "") ? matchingDelta.provenance : (cw.provenance || (window.AppState && window.AppState.rows && ((cw.token_order !== undefined && window.AppState.rows[String(cw.token_order)] && window.AppState.rows[String(cw.token_order)].provenance) || (cw.row_id !== undefined && window.AppState.rows[String(cw.row_id)] && window.AppState.rows[String(cw.row_id)].provenance))) || "");
                                    if (newProv) {
                                        cw.provenance = newProv;
                                    }
                                    cw.row_html = null;
                                }
                                if (cw.all_row_ids && cw.all_row_ids.length > 1) {
                                    var anyOverlap = false;
                                    for (var chk = 0; chk < cw.all_row_ids.length; chk++) {
                                        if (deltasByRowId.hasOwnProperty(String(cw.all_row_ids[chk]))) {
                                            anyOverlap = true;
                                            break;
                                        }
                                    }
                                    if (anyOverlap) {
                                        var rolledTrans = [];
                                        var parentLem = (cw.lemma || '').trim().toLowerCase();
                                        for (var aidx = 0; aidx < cw.all_row_ids.length; aidx++) {
                                            var subId = String(cw.all_row_ids[aidx]);
                                            var subDelta = deltasByRowId[subId];
                                            var subState = (window.AppState && window.AppState.rows) ? window.AppState.rows[subId] : null;
                                            var subLem = ((subDelta && subDelta.lemma) || (subState && subState.lemma) || '').trim().toLowerCase();
                                            if (subLem && parentLem && subLem !== parentLem) {
                                                continue;
                                            }
                                             var subTr = "";
                                            if (subDelta) {
                                                subTr = (subDelta.trans !== undefined && subDelta.trans !== "") ? subDelta.trans : ((subDelta.WordDestination !== undefined && subDelta.WordDestination !== "") ? subDelta.WordDestination : subDelta.word_translation);
                                            }
                                            if (!subTr && subState) {
                                                subTr = (subState.trans !== undefined && subState.trans !== "") ? subState.trans : ((subState.WordDestination !== undefined && subState.WordDestination !== "") ? subState.WordDestination : subState.word_translation);
                                            }
                                            if (!subTr) {
                                                for (var oc = 0; oc < wCards.length; oc++) {
                                                    var oWords = wCards[oc].words;
                                                    if (oWords) {
                                                        for (var ow = 0; ow < oWords.length; ow++) {
                                                            if (String(oWords[ow].row_id) === subId && oWords[ow].translation) {
                                                                subTr = oWords[ow].translation;
                                                                break;
                                                            }
                                                        }
                                                    }
                                                    if (subTr) break;
                                                }
                                            }
                                            if (subTr && subTr !== "[FAILED]" && subTr.indexOf('skeleton-loader') === -1 && subTr !== '-' && subTr !== '--') {
                                                if (rolledTrans.indexOf(subTr) === -1) rolledTrans.push(subTr);
                                            }
                                        }
                                        if (rolledTrans.length > 0) {
                                            cw.translation = rolledTrans.join(', ');
                                            cw.row_html = null;
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (window.WorkspaceTabs && typeof window.WorkspaceTabs.rebindActiveTab === 'function') {
                        window.WorkspaceTabs.rebindActiveTab();
                    }
                }
                window.applyCardProgressiveUpdate = applyCardProgressiveUpdate;
                applyCardProgressiveUpdate(rowsData, data);
                if (window.AppState.isFinished) {
                    window.SourceViewer.project({sourceText: window.AppState.sourceText || ''}, {payload: {}});
                    var skels = (document.querySelectorAll('td .skeleton-loader, #translation-container .skeleton-loader, #source-container .skeleton-loader, .skeleton-loader[data-pending="true"], [data-pending="true"]'));
                    if (skels.length > 0) {
                        for (var i = 0; i < skels.length; i++) {
                            var skel = skels[i];
                            if (isTextRegionNode(skel)) continue;
                            skel.classList.remove('skeleton-loader');
                            skel.removeAttribute('data-pending');
                            var p = (skel.parentNode);
                            var tr = skel.closest ? skel.closest('tr') : (function( node) {
                                while (node && node.nodeName !== 'TR') { node = node.parentElement; }
                                return node;
                            })(skel);
                            var cleanupRowId = tr ? (tr.getAttribute('data-row-id') || tr.getAttribute('data-token-order')) : null;
                            if (cleanupRowId !== null && cleanupRowId !== undefined) {
                                var skelTarget = (p && p.classList.contains('scrollable-cell') ? p : skel);
                                var rStateS = window.AppState.rows[cleanupRowId];
                                var rTransS = rStateS ? (rStateS.trans || rStateS.WordDestination || rStateS.word_translation || '') : '';
                                if (rTransS && rTransS !== '') {
                                    if (typeof setCellText === 'function') setCellText(skelTarget, rTransS);
                                    else skelTarget.textContent = rTransS;
                                } else {
                                    if (skelTarget === p) {
                                        skelTarget.innerHTML = '';
                                    } else if (skel.parentNode) {
                                        skel.parentNode.removeChild(skel);
                                    }
                                }

                            } else if (skel.parentNode) {
                                skel.parentNode.removeChild(skel);
                            }
                        }
                        updated = true;
                    }

                    // Comprehensive scan for any remaining blank/empty translation cells across all table rows:
                    var allRows = (document.querySelectorAll('tr[data-row-id]'));
                    for (var r = 0; r < allRows.length; r++) {
                        var rowEl = allRows[r];
                        var rId = rowEl.getAttribute('data-row-id') || rowEl.getAttribute('data-token-order');
                        var tOrd = rowEl.getAttribute('data-token-order');
                        var tdsEl = rowEl.getElementsByTagName('td');
                        if (tdsEl.length >= 3 && rId !== null && rId !== undefined) {
                            var transTd = (rowEl.querySelector('td[data-col="WordDestination"]')) || tdsEl[2];
                            var transDiv = (transTd.querySelector('.scrollable-cell')) || transTd;
                            var transContent = (transDiv.textContent || transDiv.innerText || "").trim();
                            if (!transContent || transContent === '...' || transContent === 'Loading...' ||  (transDiv.querySelector('.skeleton-loader'))) {
                                var rState = window.AppState.rows[rId] || (tOrd ? window.AppState.rows[tOrd] : null);
                                var rTrans = rState ? (rState.trans || rState.WordDestination || rState.word_translation || '') : '';
                                if (rTrans && rTrans !== '') {
                                    if (!transTd.classList.contains('editing')) {
                                        if (typeof setCellText === 'function') setCellText(transDiv, rTrans);
                                        else transDiv.textContent = rTrans;
                                        var rProv = rState ? (rState.provenance || rState._provenance || rState.transProvenance) : null;
                                        if (rProv) {
                                            transTd.setAttribute('data-provenance', String(rProv));
                                            var pTitle = formatTranslationCellTooltip(rTrans, rProv);
                                            if (pTitle) {
                                                transTd.setAttribute('title', String(pTitle));
                                                if (transDiv !== transTd) {
                                                    transDiv.setAttribute('data-provenance', String(rProv));
                                                    transDiv.setAttribute('title', String(pTitle));
                                                }
                                            }
                                        }
                                    }
                                } else {
                                    if (!transTd.classList.contains('editing')) {
                                        transDiv.innerHTML = '';
                                    }
                                }
                                updated = true;
                            } else {
                                var rState = window.AppState.rows[rId] || (tOrd ? window.AppState.rows[tOrd] : null);
                                var rProv = rState ? (rState.provenance || rState._provenance || rState.transProvenance) : null;
                                if (rProv && !transTd.classList.contains('dirty') && !transTd.classList.contains('editing')) {
                                    if (! (transDiv.querySelector('.skeleton-loader'))) {
                                        transTd.setAttribute('data-provenance', String(rProv));
                                        var pTitle = formatTranslationCellTooltip(transDiv.textContent || transTd.textContent, rProv);
                                        if (pTitle) {
                                            transTd.setAttribute('title', String(pTitle));
                                            if (transDiv !== transTd) {
                                                transDiv.setAttribute('data-provenance', String(rProv));
                                                transDiv.setAttribute('title', String(pTitle));
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                if (updated) {
                    if (window.rebindMVPBookmarks) window.rebindMVPBookmarks();

                    if (typeof applyOccurrenceView === 'function') {
                        applyOccurrenceView();
                    } else if (typeof window.applyOccurrenceView === 'function') {
                        window.applyOccurrenceView();
                    }
                    if (typeof updateBidirectionalHighlights === 'function') {
                        updateBidirectionalHighlights();
                    } else if (typeof window.updateBidirectionalHighlights === 'function') {
                        window.updateBidirectionalHighlights();
                    }

                    if (window.forceRepaint) window.forceRepaint();
                }

                // Signal AHK only after all rendering is complete so that "Ready"
                // in the window title reflects the window being fully populated.
                if (isTerminal && window.ahkCall) {
                    try {
                        window.ahkCall('finished', '');
                    } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                }
            };

        /** @param {HTMLElement} el @param {string} text */
        function setCellText(el, text) {
            if (!el) return;
            el.innerHTML = text ? String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : "";
        }

        window.AppView = {
            renderTranslatedText: function(globalStage) {
                var container = document.getElementById('translation-container');
                if (!container) return false;
                synchronizeOverviewTranslation();
                if (activeSentenceTranslation()) {
                    window.WorkspaceTabs.updateActiveTabTranslation();
                    return false;
                }
                return projectGlobalTranslation(container, globalStage);
            },
            renderRow: /** @param {number|string} rowId @param {string=} globalStage */ function(rowId, globalStage) {
                 var targetVal;
                var updated = false;
                var rowData = window.AppState.rows[rowId];
                if (!rowData) return false;
                var tr = null;
                if (rowData.token_order !== undefined && rowData.token_order !== null && String(rowData.token_order) !== "") {
                    tr = (document.querySelector('tr[data-token-order="' + rowData.token_order + '"]'));
                } else {
                    tr = (document.querySelector('tr[data-row-id="' + rowId + '"]'));
                }
                if (!tr) {
                    tr = (document.querySelector('tr[data-row-id="' + rowId + '"]'));
                }
                if (!tr) {
                    var allTrs = (document.querySelectorAll('tr[data-all-row-ids]'));
                    for (var at = 0; at < allTrs.length; at++) {
                        var parts = (allTrs[at].getAttribute('data-all-row-ids') || '').split(',').map(function(s) { return s.trim(); });
                        if (parts.indexOf(String(rowId)) !== -1) {
                            tr = allTrs[at];
                            break;
                        }
                    }
                }
                if (!tr) return false;
                var tds = tr.getElementsByTagName('td');
                var infCell = (tr.querySelector('td.col-inflected, td[data-col="WordSourceInflectedForm"]')) || (tds.length >= 5 ? tds[0] : null);
                var lemCell = (tr.querySelector('td.col-lemma, td[data-col="WordSource"]')) || (tds.length >= 5 ? tds[1] : (tds.length > 0 ? tds[0] : null));
                var transTd = (tr.querySelector('td.col-translation, td[data-col="WordDestination"]')) || (tds.length >= 3 ? tds[2] : (tds.length > 1 ? tds[1] : null));
                var ipaCell = (tr.querySelector('td.col-ipa, td[data-col="WordSourceIpaAI"], td[data-col="WordSourceIpa"]')) || (tds.length > 3 ? tds[3] : null);
                var morphCell = (tr.querySelector('td.col-morphology, td[data-col="WordSourceMorphologyAI"], td[data-col="WordSourceMorphology"]')) || (tds.length > 4 ? tds[4] : null);
                if (tds.length >= 1) {
                    if (infCell && !infCell.classList.contains('dirty') && rowData.hasOwnProperty('inflected') && rowData.inflected !== undefined && rowData.inflected !== "") {
                        var div = (infCell.querySelector('.scrollable-cell'));
                        var val = rowData.inflected || "";
                        var oldVal = div ? (div.textContent || div.innerText) : (infCell.classList.contains('editing') ? null : (infCell.textContent || infCell.innerText));
                        var existingTitle = infCell.getAttribute('data-orig-title') || infCell.getAttribute('title') || (div ? (div.getAttribute('data-orig-title') || div.getAttribute('title')) : '');
                        var sentText = rowData.sentence_source || rowData.SentenceSource || (window.AppState ? window.AppState.sourceText : "") || (document.getElementById('source-container') ? requireElement('source-container').textContent : "");
                        var infTip = rowData.inflected_tooltip || ((oldVal === val && existingTitle) ? existingTitle : formatInflectedTooltip(sentText || '', val, rowData.token_order || rowId, rowData.lemma || ''));
                        var unrolledHtml = val ? String(val).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : "";
                        infCell.setAttribute('data-orig-html', String(unrolledHtml));
                        if (infTip) {
                            infCell.setAttribute('data-orig-title', String(infTip));
                            if (div && div !== infCell) div.setAttribute('data-orig-title', String(infTip));
                            infCell.setAttribute('title', String(infTip));
                            if (div) div.setAttribute('title', String(infTip));
                        } else {
                            infCell.removeAttribute('data-orig-title');
                            if (div && div !== infCell) div.removeAttribute('data-orig-title');
                            infCell.removeAttribute('title');
                            if (div) div.removeAttribute('title');
                        }
                        if (oldVal !== val) {
                            if (div) setCellText(div, val);
                            else if (!infCell.classList.contains('editing')) setCellText(infCell, val);
                            updated = true;
                        }
                    }
                    if (lemCell && !lemCell.classList.contains('dirty') && rowData.hasOwnProperty('lemma') && rowData.lemma !== undefined) {
                        var div = (lemCell.querySelector('.scrollable-cell'));
                        var val = rowData.lemma || "";
                        if (globalStage === 'translated_lemmas' || globalStage === 'translated' || globalStage === 'finished') {
                            if (div) setCellText(div, val);
                            else if (!lemCell.classList.contains('editing')) setCellText(lemCell, val);
                            updated = true;
                        } else {
                            var oldVal = div ? (div.textContent || div.innerText) : (lemCell.classList.contains('editing') ? null : (lemCell.textContent || lemCell.innerText));
                            if (oldVal !== val) {
                                if (div) setCellText(div, val);
                                else if (!lemCell.classList.contains('editing')) setCellText(lemCell, val);
                                updated = true;
                            }
                        }
                        var activeLang = (typeof getSessionLang === 'function' ? getSessionLang() : '') || (window.AppConfig ? window.AppConfig.language : '');
                        var rawG = rowData.gender !== undefined ? rowData.gender : (rowData.WordSourceGender || "");
                        var curPosCell = (tr.querySelector('td.col-pos, td[data-col="WordSourcePos"], td[data-col="pos"]'));
                        var rawPos = rowData.pos !== undefined ? rowData.pos : (rowData.WordSourcePos !== undefined ? rowData.WordSourcePos : (curPosCell ? (curPosCell.textContent || curPosCell.innerText || "").trim() : ""));
                        var lemTip = formatLemmaTooltip(val, rawG, activeLang, rawPos);
                        if (lemTip) {
                            lemCell.setAttribute('title', String(lemTip));
                            if (div && div !== lemCell) div.setAttribute('title', String(lemTip));
                        } else {
                            lemCell.removeAttribute('title');
                            if (div && div !== lemCell) div.removeAttribute('title');
                        }
                        var curGenderCell = (tr.querySelector('td.col-gender, td[data-col="WordSourceGender"]'));
                        if (curGenderCell) {
                            var curGenVal = rawG || (curGenderCell.textContent || curGenderCell.innerText || "").trim();
                            var curProv = rowData.provenance || rowData._provenance || rowData.transProvenance;
                            if (!curProv) {
                                var curTransCell = (tr.querySelector('td.col-translation, td[data-col="WordDestination"]'));
                                if (curTransCell) curProv = curTransCell.getAttribute('data-provenance') || undefined;
                            }
                            var updatedGenTip = formatGenderTooltip(curGenVal, val, curProv);
                            if (updatedGenTip) curGenderCell.setAttribute('title', String(updatedGenTip));
                            else curGenderCell.removeAttribute('title');
                        }
                    }
                    var hasTransProp = rowData.hasOwnProperty('trans') || rowData.hasOwnProperty('WordDestination') || rowData.hasOwnProperty('word_translation');
                    var rowProv = rowData.provenance || rowData._provenance || rowData.transProvenance;
                    if (!rowProv && rowData.token_order !== undefined && rowData.token_order !== null) {
                        for (var rk in window.AppState.rows) {
                            if (window.AppState.rows[rk] && String(window.AppState.rows[rk].token_order) === String(rowData.token_order)) {
                                if (window.AppState.rows[rk].provenance) {
                                    rowProv = window.AppState.rows[rk].provenance;
                                    break;
                                }
                            }
                        }
                    }
                    /** @param {string} effectiveVal */
                    function applyCellProvenance(effectiveVal) {
                        if (!transTd) return;
                        var existingProv = transTd.getAttribute('data-provenance') || ( (transTd.querySelector('.scrollable-cell')) ?  (transTd.querySelector('.scrollable-cell')).getAttribute('data-provenance') : null);
                        var effProv = rowProv || existingProv || (effectiveVal && effectiveVal.indexOf('skeleton-loader') === -1 ? 'cached:sqlite' : null);
                        var transTip = formatTranslationCellTooltip(effectiveVal, effProv);
                        if (effProv && effectiveVal && effectiveVal.indexOf('skeleton-loader') === -1) {
                            transTd.setAttribute('data-provenance', String(effProv));
                            if (transTip) transTd.setAttribute('title', String(transTip));
                            else transTd.removeAttribute('title');
                            var scrollDiv = (transTd.querySelector('.scrollable-cell'));
                            if (scrollDiv) {
                                scrollDiv.setAttribute('data-provenance', String(effProv));
                                if (transTip) scrollDiv.setAttribute('title', String(transTip));
                                else scrollDiv.removeAttribute('title');
                            }
                        } else if (transTip) {
                            transTd.removeAttribute('data-provenance');
                            transTd.setAttribute('title', String(transTip));
                            var scrollDiv = (transTd.querySelector('.scrollable-cell'));
                            if (scrollDiv) {
                                scrollDiv.removeAttribute('data-provenance');
                                scrollDiv.setAttribute('title', String(transTip));
                            }
                        } else {
                            transTd.removeAttribute('data-provenance');
                            transTd.removeAttribute('title');
                            var scrollDiv = (transTd.querySelector('.scrollable-cell'));
                            if (scrollDiv) {
                                scrollDiv.removeAttribute('data-provenance');
                                scrollDiv.removeAttribute('title');
                            }
                        }
                    }
                    if (transTd && !transTd.classList.contains('dirty') && hasTransProp) {
                        var div = (transTd.querySelector('.scrollable-cell')) || transTd;
                        var val = (rowData.trans !== undefined && rowData.trans !== "") ? rowData.trans : ((rowData.WordDestination !== undefined && rowData.WordDestination !== "") ? rowData.WordDestination : ((rowData.word_translation !== undefined) ? rowData.word_translation : (rowData.trans || "")));
                        var allRowIdsAttr = tr.getAttribute('data-all-row-ids');
                        if (allRowIdsAttr) {
                            var midParts = allRowIdsAttr.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
                            if (midParts.length > 1) {
                                var rolledTrans = [];
                                var parentLem = (rowData.lemma || (lemCell ? (lemCell.textContent || lemCell.innerText) : '') || '').trim().toLowerCase();
                                for (var mp = 0; mp < midParts.length; mp++) {
                                    var mRow = window.AppState.rows[midParts[mp]];
                                    if (mRow) {
                                        var mLem = (mRow.lemma || '').trim().toLowerCase();
                                        if (mLem && parentLem && mLem !== parentLem) {
                                            continue;
                                        }
                                        var mTr = (mRow.trans !== undefined && mRow.trans !== "") ? mRow.trans : ((mRow.WordDestination !== undefined && mRow.WordDestination !== "") ? mRow.WordDestination : mRow.word_translation);
                                        if (mTr && mTr !== "[FAILED]" && mTr.indexOf('skeleton-loader') === -1 && mTr !== '-' && mTr !== '--') {
                                            if (rolledTrans.indexOf(mTr) === -1) rolledTrans.push(mTr);
                                        }
                                    }
                                }
                                if (rolledTrans.length > 0) {
                                    val = rolledTrans.join(', ');
                                }
                            }
                        }
                        var hasSkeleton = (div.querySelector('.skeleton-loader')) !== null;
                        var isTerm = (globalStage === 'finished' || window.AppState.isFinished);
                        if (hasSkeleton && val === "" && !isTerm) {
                            // Skeletons remain active until real translations arrive or terminal stage
                        } else if (val !== "" || globalStage === 'translated' || globalStage === 'translated_words' || globalStage === 'translated_lemmas' || isTerm) {
                            var oldVal = transTd.classList.contains('editing') ? null : (div.textContent || div.innerText);
                            targetVal = val;
                            if (isTerm && targetVal === "" && window.AppState.lastError && oldVal && oldVal.indexOf('skeleton-loader') === -1) {
                                targetVal = oldVal;
                            }
                            if (targetVal === "" && isTerm) {
                                if (!transTd.classList.contains('editing')) div.innerHTML = "";
                            } else {
                                if (!transTd.classList.contains('editing')) setCellText(div, targetVal);
                                applyCellProvenance(targetVal);
                            }
                            updated = true;
                        } else {
                            var oldVal = transTd.classList.contains('editing') ? null : (div.textContent || div.innerText);
                            var shouldUpdate = (oldVal !== val) || hasSkeleton;
                            if (shouldUpdate) {
                                if (val === "" && isTerm) {
                                    if (!transTd.classList.contains('editing')) div.innerHTML = "";
                                    updated = true;
                                } else if (val !== "") {
                                    if (!transTd.classList.contains('editing')) setCellText(div, val);
                                    applyCellProvenance(val);
                                    updated = true;
                                }
                            } else if (val !== "" && !transTd.classList.contains('editing')) {
                                applyCellProvenance(val);
                            }
                        }
                        if (transTd) {
                            var unrolledTrans = ((typeof targetVal !== 'undefined' && targetVal !== undefined) ? targetVal : val) || "";
                            if (unrolledTrans && unrolledTrans.indexOf('skeleton-loader') === -1) {
                                var unrolledTransHtml = unrolledTrans ? String(unrolledTrans).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : "";
                                transTd.setAttribute('data-orig-html', String(unrolledTransHtml));
                                var curTransTip = transTd.getAttribute('title') || (div ? div.getAttribute('title') : '');
                                if (curTransTip) {
                                    transTd.setAttribute('data-orig-title', String(curTransTip));
                                    if (div && div !== transTd) div.setAttribute('data-orig-title', String(curTransTip));
                                } else {
                                    transTd.removeAttribute('data-orig-title');
                                    if (div && div !== transTd) div.removeAttribute('data-orig-title');
                                }
                            }
                        }
                    } else if (transTd && !transTd.classList.contains('dirty') && !transTd.classList.contains('editing')) {
                        var div = (transTd.querySelector('.scrollable-cell')) || transTd;
                        var curText = (div.textContent || div.innerText || '').trim();
                        if (curText && curText !== '...' && curText !== 'Loading...' && ! (div.querySelector('.skeleton-loader'))) {
                            applyCellProvenance(curText);
                        }
                    }
                    if (ipaCell && !ipaCell.classList.contains('dirty') && rowData.hasOwnProperty('ipa') && rowData.ipa !== undefined) {
                        var div = (ipaCell.querySelector('.scrollable-cell')) || ipaCell;
                        var val = rowData.ipa || "";
                        var hasSkeleton = (div.querySelector('.skeleton-loader')) !== null;
                        var isTerm = (globalStage === 'finished' || window.AppState.isFinished);
                        if (hasSkeleton && val === "" && !isTerm) {
                            // Skeletons remain active until real translations arrive or terminal stage
                        } else if (globalStage === 'translated_words' || isTerm) {
                            if (!ipaCell.classList.contains('editing')) setCellText(div, val);
                            if (val && val.indexOf('skeleton-loader') === -1) {
                                ipaCell.setAttribute('title', String(val));
                                if (div && div !== ipaCell) div.setAttribute('title', String(val));
                            } else {
                                ipaCell.removeAttribute('title');
                                if (div && div !== ipaCell) div.removeAttribute('title');
                            }
                            updated = true;
                        } else if (val !== "") {
                            var oldVal = ipaCell.classList.contains('editing') ? null : (div.textContent || div.innerText);
                            if (oldVal !== val || hasSkeleton) {
                                if (!ipaCell.classList.contains('editing')) setCellText(div, val);
                                if (val.indexOf('skeleton-loader') === -1) {
                                    ipaCell.setAttribute('title', String(val));
                                    if (div && div !== ipaCell) div.setAttribute('title', String(val));
                                } else {
                                    ipaCell.removeAttribute('title');
                                    if (div && div !== ipaCell) div.removeAttribute('title');
                                }
                                updated = true;
                            }
                        } else {
                            ipaCell.removeAttribute('title');
                            if (div && div !== ipaCell) div.removeAttribute('title');
                        }
                    }
                    if (morphCell && !morphCell.classList.contains('dirty') && (rowData.hasOwnProperty('morph') || rowData.hasOwnProperty('morphology') || rowData.hasOwnProperty('WordSourceMorphologyAI')) && (rowData.morph !== undefined || rowData.morphology !== undefined || rowData.WordSourceMorphologyAI !== undefined)) {
                        var div = (morphCell.querySelector('.scrollable-cell')) || morphCell;
                        var val = (rowData.morph !== undefined && rowData.morph !== "") ? rowData.morph : ((rowData.morphology !== undefined && rowData.morphology !== "") ? rowData.morphology : (rowData.WordSourceMorphologyAI || ""));
                        var hasSkeleton = (div.querySelector('.skeleton-loader')) !== null;
                        var isTerm = (globalStage === 'finished' || window.AppState.isFinished);
                        if (hasSkeleton && val === "" && !isTerm) {
                            // Skeletons remain active until real translations arrive or terminal stage
                        } else if (globalStage === 'translated_words' || isTerm) {
                            if (!morphCell.classList.contains('editing')) div.innerHTML = val;
                            if (val && val.indexOf('skeleton-loader') === -1) {
                                morphCell.setAttribute('title', String(val));
                                if (div && div !== morphCell) div.setAttribute('title', String(val));
                            } else {
                                morphCell.removeAttribute('title');
                                if (div && div !== morphCell) div.removeAttribute('title');
                            }
                            updated = true;
                        } else if (val !== "") {
                            var oldVal = morphCell.classList.contains('editing') ? null : div.innerHTML;
                            if (oldVal !== val || hasSkeleton) {
                                if (!morphCell.classList.contains('editing')) div.innerHTML = val;
                                if (val.indexOf('skeleton-loader') === -1) {
                                    morphCell.setAttribute('title', String(val));
                                    if (div && div !== morphCell) div.setAttribute('title', String(val));
                                } else {
                                    morphCell.removeAttribute('title');
                                    if (div && div !== morphCell) div.removeAttribute('title');
                                }
                                updated = true;
                            }
                        } else {
                            morphCell.removeAttribute('title');
                            if (div && div !== morphCell) div.removeAttribute('title');
                        }
                        if (val && val.indexOf('skeleton-loader') === -1) {
                            var unrolledMorphHtml = val ? String(val).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : "";
                            morphCell.setAttribute('data-orig-html', String(unrolledMorphHtml));
                            morphCell.setAttribute('data-orig-title', String(val));
                            if (div && div !== morphCell) div.setAttribute('data-orig-title', String(val));
                        } else {
                            morphCell.removeAttribute('data-orig-html');
                            morphCell.removeAttribute('data-orig-title');
                            if (div && div !== morphCell) div.removeAttribute('data-orig-title');
                        }
                    }
                    if (rowData.hasOwnProperty('classifications') && rowData.classifications !== undefined) {
                        for (var class_name in rowData.classifications) {
                            if (rowData.classifications.hasOwnProperty(class_name)) {
                                var val = rowData.classifications[class_name] || "";
                                var cell = (tr.querySelector('td[data-col="' + class_name + '"]'));
                                if (cell) {
                                    var div = (cell.querySelector('.scrollable-cell'));
                                    var displayVal = val;
                                    var spanClass = "";
                                    if (val.indexOf(':') !== -1) {
                                        var parts = val.split(':', 2);
                                        var possiblePrefix = parts[0].trim();
                                        if (possiblePrefix.length <= 5 && possiblePrefix.indexOf('/') === -1 && possiblePrefix.indexOf('\\\\') === -1) {
                                            displayVal = parts[1].trim();
                                            spanClass = "level-" + possiblePrefix.toLowerCase();
                                        }
                                    }
                                    var innerHtml = spanClass ? '<span class="' + spanClass + '">' + displayVal + '</span>' : displayVal;
                                    var oldHtml = div ? div.innerHTML : cell.innerHTML;
                                    if (oldHtml !== innerHtml) {
                                        if (div) div.innerHTML = innerHtml;
                                        else cell.innerHTML = innerHtml;
                                        updated = true;
                                    }
                                    var clsTip = formatClassificationTooltip(val, class_name);
                                    if (clsTip) {
                                        cell.setAttribute('title', String(clsTip));
                                    }
                                }
                            }
                        }
                    }
                    var hasPosProp = rowData.hasOwnProperty('pos') || rowData.hasOwnProperty('WordSourcePOS');
                    if (hasPosProp) {
                        var posCell = (tr.querySelector('td.col-pos, td[data-col="WordSourcePOS"]'));
                        if (posCell && !posCell.classList.contains('dirty')) {
                            var div = (posCell.querySelector('.scrollable-cell')) || posCell;
                            var rawPos = (rowData.pos !== undefined && rowData.pos !== null) ? rowData.pos : (rowData.WordSourcePOS || "");
                            var normPos = rawPos;
                            if (typeof rawPos === 'string') {
                                var pClean = rawPos.trim();
                                var pUpper = pClean.toUpperCase();
                                var posMap = {
                                    "NOUN": "n.", "PROPN": "n.", "VERB": "v.", "AUX": "v.", "ADJ": "adj.",
                                    "ADV": "adv.", "ADP": "prep.", "PREP": "prep.", "PRON": "pron.",
                                    "CCONJ": "conj.", "SCONJ": "conj.", "CONJ": "conj.", "NUM": "num.",
                                    "DET": "art.", "ART": "art.", "PART": "part.", "INTJ": "intj."
                                };
                                if (posMap[pUpper]) {
                                    normPos = posMap[pUpper];
                                } else {
                                    var pLower = pClean.toLowerCase();
                                    if (pLower === "noun") normPos = "n.";
                                    else if (pLower === "verb") normPos = "v.";
                                    else if (["n.", "v.", "adj.", "adv.", "prep.", "conj.", "pron.", "art.", "num.", "part.", "intj."].indexOf(pLower) !== -1) normPos = pLower;
                                    else normPos = pClean;
                                }
                            }
                            var oldPosVal = (div.textContent || div.innerText || '').trim();
                            if (oldPosVal !== normPos) {
                                if (!posCell.classList.contains('editing')) setCellText(div, normPos);
                                updated = true;
                            }
                            var posTip = formatPosTooltip(rawPos || normPos);
                            if (posTip) {
                                posCell.setAttribute('title', String(posTip));
                            }
                            var unrolledPosHtml = normPos ? String(normPos).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : "";
                            posCell.setAttribute('data-orig-html', String(unrolledPosHtml));
                            if (posTip) {
                                posCell.setAttribute('data-orig-title', String(posTip));
                                if (div && div !== posCell) div.setAttribute('data-orig-title', String(posTip));
                            } else {
                                posCell.removeAttribute('data-orig-title');
                                if (div && div !== posCell) div.removeAttribute('data-orig-title');
                            }
                        }
                    }
                    var hasGenderProp = rowData.hasOwnProperty('gender') || rowData.hasOwnProperty('WordSourceGender');
                    if (hasGenderProp) {
                        var genderCell = (tr.querySelector('td.col-gender, td[data-col="WordSourceGender"]'));
                        if (genderCell && !genderCell.classList.contains('dirty')) {
                            var div = (genderCell.querySelector('.scrollable-cell')) || genderCell;
                            var rawGender = (rowData.gender !== undefined && rowData.gender !== null) ? rowData.gender : (rowData.WordSourceGender || "");
                            var targetGenderHtml = formatGenderBadge(rawGender);
                            var oldGenderHtml = div.innerHTML;
                            if (oldGenderHtml !== targetGenderHtml) {
                                if (!genderCell.classList.contains('editing')) div.innerHTML = targetGenderHtml;
                                updated = true;
                            }
                            var rowLemma = (rowData.lemma !== undefined && rowData.lemma !== null) ? rowData.lemma : (rowData.WordSource || "");
                            if (!rowLemma) {
                                var lemmaCell = (tr.querySelector('td.col-lemma, td[data-col="WordSource"]'));
                                if (lemmaCell) {
                                    var lemDiv = (lemmaCell.querySelector('.scrollable-cell')) || lemmaCell;
                                    rowLemma = (lemDiv.textContent || lemDiv.innerText || '').trim();
                                }
                            }
                            var effProv = rowData.provenance || rowData._provenance || rowData.transProvenance;
                            if (!effProv) {
                                var transCell = (tr.querySelector('td.col-translation, td[data-col="WordDestination"]'));
                                if (transCell) {
                                    effProv = transCell.getAttribute('data-provenance') || ( (transCell.querySelector('[data-provenance]')) ?  (transCell.querySelector('[data-provenance]')).getAttribute('data-provenance') : null) || undefined;
                                }
                            }
                            if (!effProv && rowData.token_order !== undefined && rowData.token_order !== null && window.AppState && window.AppState.rows) {
                                for (var rk in window.AppState.rows) {
                                    if (window.AppState.rows[rk] && String(window.AppState.rows[rk].token_order) === String(rowData.token_order)) {
                                        if (window.AppState.rows[rk].provenance) {
                                            effProv = window.AppState.rows[rk].provenance;
                                            break;
                                        }
                                    }
                                }
                            }
                            var genTip = formatGenderTooltip(rawGender, rowLemma, effProv);
                            if (genTip) {
                                genderCell.setAttribute('title', String(genTip));
                            } else {
                                genderCell.removeAttribute('title');
                            }
                        }
                    }
                }
                if (tr.hasAttribute('data-occ') && window.AppState && window.AppState.activeTokenSelections && window.AppState.activeTokenSelections.length > 0) {
                    if (typeof applyOccurrenceRow === 'function') {
                        applyOccurrenceRow(tr);
                    } else if (typeof window.applyOccurrenceRow === 'function') {
                        window.applyOccurrenceRow(tr);
                    }
                }
                return updated;
            }
        };

        window.receiveUpdate = function(data) {
            if (window.AppStore) window.AppStore.applyDeltas(data);
            else {
                if (!window.AppState.applyDeltas) throw new Error('Required delta merger is unavailable');
                window.AppState.applyDeltas(data);
            }
        };

        try {
            var spEl = document.getElementById('sentence-provenance');
            var initialSentenceProv = spEl ? (spEl.textContent || spEl.innerText || "").trim() : "";
            if (initialSentenceProv) {
                window.AppState.textProvenance = initialSentenceProv;
                var tcInit = document.getElementById('translation-container');
                if (tcInit) applyTextProvenance(tcInit);
            }
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        try {
            var initRows = (document.querySelectorAll('tr[data-row-id]'));
            for (var ir = 0; ir < initRows.length; ir++) {
                var rEl = initRows[ir];
                var rId = rEl.getAttribute('data-row-id');
                var tOrd = rEl.getAttribute('data-token-order');
                var transTd = (rEl.querySelector('td.col-translation, td[data-col="WordDestination"]'));
                var rProv = transTd ? transTd.getAttribute('data-provenance') : null;
                if (rProv) {
                    if (rId) {
                        if (!window.AppState.rows[rId]) window.AppState.rows[rId] = {};
                        window.AppState.rows[rId].provenance = rProv;
                    }
                    if (tOrd && tOrd !== rId) {
                        if (!window.AppState.rows[tOrd]) window.AppState.rows[tOrd] = {};
                        window.AppState.rows[tOrd].provenance = rProv;
                    }
                }
            }
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

         var _cachedSentenceCards = null;
        function getCardModels() {
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
                var c = window.WorkspaceTabs.getCards();
                if (c && c.length > 0) return c;
            }
            if (window._kwSentenceCards && window._kwSentenceCards.length > 0) {
                return window._kwSentenceCards;
            }
            if (_cachedSentenceCards && _cachedSentenceCards.length > 0) {
                return _cachedSentenceCards;
            }
            var scriptEl = document.getElementById('sentence-cards');
            if (scriptEl) {
                try {
                    _cachedSentenceCards = JSON.parse(scriptEl.textContent || scriptEl.innerText || '[]');
                } catch(e) {
                    _cachedSentenceCards = [];
                }
                return _cachedSentenceCards;
            }
            return [];
        }
        window.getCardModels = getCardModels;

        function countPendingSkeletons() {
            var domCount = (document.querySelectorAll('.skeleton-loader, [data-pending="true"]')).length;
            var cards = getCardModels();
            if (!cards || cards.length === 0) {
                return domCount;
            }
            var count = domCount;
            var activeSeq = null;
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveTabSeq === 'function') {
                activeSeq = window.WorkspaceTabs.getActiveTabSeq();
            } else if (window._kwActiveTabSeq !== undefined && window._kwActiveTabSeq !== null) {
                activeSeq = window._kwActiveTabSeq;
            } else {
                var activeChip = (document.querySelector('.kw-tab-chip.active'));
                if (activeChip) {
                    var parsed = parseInt(String(activeChip.getAttribute('data-tab-seq')), 10);
                    if (!isNaN(parsed)) activeSeq = parsed;
                }
            }
            if (activeSeq === null && cards.length > 0) {
                activeSeq = (cards[0].seq_num !== undefined) ? cards[0].seq_num : 1;
            }

            var isFinished = !!(window.AppState && (window.AppState.isFinished === true || window.AppState.stage === 'finished' || (window.AppState.status && ((typeof window.AppState.status === 'object' && window.AppState.status.isFinished === true) || window.AppState.status === 'finished'))));
            var isFailed = !!(window.AppState && (window.AppState.textTranslationFailed || window.AppState.textTranslationStatus === 'failed'));

            var tc = document.getElementById('translation-container');
            var tcHasSkel = !!(tc && (tc.classList.contains('skeleton-loader') || tc.getAttribute('data-pending') === 'true' ||  (tc.querySelector('.skeleton-loader, [data-pending="true"]'))));

            for (var i = 0; i < cards.length; i++) {
                var card = cards[i];
                var isActive = (activeSeq !== null) ? (card.seq_num === activeSeq) : false;
                if (!isActive) {
                    if (card.words && card.words.length > 0) {
                        for (var w = 0; w < card.words.length; w++) {
                            var word = card.words[w];
                            var hasTransSkel = word.translation && (word.translation.indexOf('skeleton-loader') !== -1 || word.translation.indexOf('data-pending') !== -1);
                            var hasRowSkel = word.row_html && (word.row_html.indexOf('skeleton-loader') !== -1 || word.row_html.indexOf('data-pending') !== -1);
                            if (hasTransSkel || hasRowSkel) {
                                count += 1;
                            }
                        }
                    }
                    if (card.sentence_idx > 0 && !card.translated_text && !isFinished && !isFailed) {
                        count += 1;
                    }
                } else {
                    if (card.sentence_idx > 0 && !card.translated_text && !isFinished && !isFailed && !tcHasSkel) {
                        count += 1;
                    }
                }
            }
            return count;
        }
        window.countPendingSkeletons = countPendingSkeletons;

        /** @param {string} mode */
        function normalizeCardSkeletons(mode) {
            var cards = getCardModels();
            if (cards && cards.length > 0) {
                for (var c = 0; c < cards.length; c++) {
                    var card = cards[c];
                    if (card.words && card.words.length > 0) {
                        for (var w = 0; w < card.words.length; w++) {
                            var word = card.words[w];
                            var hasTransSkel = word.translation && (word.translation.indexOf('skeleton-loader') !== -1 || word.translation.indexOf('data-pending') !== -1);
                            var hasRowSkel = word.row_html && (word.row_html.indexOf('skeleton-loader') !== -1 || word.row_html.indexOf('data-pending') !== -1);
                            if (hasTransSkel || hasRowSkel) {
                                word.row_html = null;
                                var rIdStr = String(word.row_id !== undefined && word.row_id !== null ? word.row_id : '');
                                var tOrdStr = String(word.token_order !== undefined && word.token_order !== null ? word.token_order : rIdStr);
                                var appRow = (window.AppState && window.AppState.rows) ? ((tOrdStr && window.AppState.rows[tOrdStr]) || (rIdStr && window.AppState.rows[rIdStr])) : null;
                                var resolvedTrans = appRow ? (appRow.trans !== undefined && appRow.trans !== "" ? appRow.trans : (appRow.WordDestination !== undefined && appRow.WordDestination !== "" ? appRow.WordDestination : (appRow.word_translation || ''))) : '';
                                if (appRow && resolvedTrans && resolvedTrans.indexOf('skeleton-loader') === -1 && resolvedTrans !== '[FAILED]') {
                                    word.translation = resolvedTrans;
                                    if (appRow.lemma) word.lemma = appRow.lemma;
                                    if (appRow.ipa) word.ipa = appRow.ipa;
                                    var updatedMorph = (appRow.morphology !== undefined && appRow.morphology !== "") ? appRow.morphology : ((appRow.morph !== undefined && appRow.morph !== "") ? appRow.morph : appRow.WordSourceMorphologyAI);
                                    if (updatedMorph) word.morphology = updatedMorph;
                                    if (appRow.pos !== undefined) word.pos = appRow.pos;
                                    else if (appRow.WordSourcePOS !== undefined) word.pos = appRow.WordSourcePOS;
                                    if (appRow.gender !== undefined) word.gender = appRow.gender;
                                    else if (appRow.WordSourceGender !== undefined) word.gender = appRow.WordSourceGender;
                                    if (appRow.provenance) word.provenance = appRow.provenance;
                                } else if (hasTransSkel) {
                                    word.translation = '';
                                }
                            }
                        }
                    }
                    if (card.translated_text && (card.translated_text.indexOf('skeleton-loader') !== -1 || card.translated_text.indexOf('data-pending') !== -1)) {
                        card.translated_text = '';
                    }
                }
            }
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.rebindActiveTab === 'function') {
                window.WorkspaceTabs.rebindActiveTab();
            }
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.updateActiveTabTranslation === 'function') {
                window.WorkspaceTabs.updateActiveTabTranslation();
            }
        }
        window.normalizeCardSkeletons = normalizeCardSkeletons;

        var _lastEnsureRestartTime = 0;
         var _ensureDebounceTimer = null;
        var lastManualStatusAt = null;
        var statusRead = null;

        function readSessionStatus(session, options) {
            if (statusRead && statusRead.session === session) return statusRead.promise;
            var pending = {session: session, promise: null};
            statusRead = pending;
            pending.promise = fetchSessionStatus(session, options).finally(function() {
                if (statusRead === pending) statusRead = null;
            });
            return pending.promise;
        }

        function fetchSessionStatus(session, options) {
            var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            if (controller) options.signal = controller.signal;
            var token = getApiToken();
            if (token) options.headers['X-API-Token'] = token;
            var timeout;
            return new Promise(function(resolve, reject) {
                timeout = setTimeout(function() {
                    if (controller) controller.abort();
                    reject(new Error('Session status request timed out'));
                }, 4000);
                fetch('/session/status?zid=' + encodeURIComponent(session), options).then(function(response) {
                    return response.json().then(function(data) { return {ok: response.ok, status: response.status, data: data}; });
                }).then(resolve, reject);
            }).finally(function() {
                clearTimeout(timeout);
            });
        }
        /** @param {string} reason */
        function ensureWatchdogActive(reason) {
            var isWebMode = (window.location.protocol !== 'file:') || (document.body && document.body.getAttribute('data-web-mode') === 'true');
            var sessZidEl = document.getElementById('session-zid');
            var curZid = sessZidEl ? (sessZidEl.textContent || sessZidEl.innerText || "").trim() : "";
            if (!curZid) {
                var params = new URLSearchParams(window.location.search);
                curZid = params.get('session_zid') || params.get('zid') || "";
            }
            if (!curZid && document.body && document.body.getAttribute('data-zid')) {
                curZid = document.body.getAttribute('data-zid') || '';
            }
            if (!isWebMode || !curZid) return;

            var isRunning = !!(window._kwSkeletonPollTimer || (window._kwEvtSource && window._kwEvtSource.readyState !== 2));
            if (isRunning && !window._kwWatchdogResolved) {
                if (_ensureDebounceTimer) clearTimeout(_ensureDebounceTimer);
                _ensureDebounceTimer = setTimeout(function() {
                    _ensureDebounceTimer = null;
                    if (typeof window._kwPollSessionStatus === 'function') {
                        window._kwPollSessionStatus();
                    }
                }, 150);
                return;
            }

            var pending = (typeof window.countPendingSkeletons === 'function') ? window.countPendingSkeletons() : 0;
            if (pending === 0) return;

            var isFinished = !!(window.AppState && (window.AppState.isFinished === true || window.AppState.stage === 'finished' || (window.AppState.status && ((typeof window.AppState.status === 'object' && window.AppState.status.isFinished === true) || window.AppState.status === 'finished'))));
            if (isFinished) {
                if (typeof window.normalizeCardSkeletons === 'function') {
                    window.normalizeCardSkeletons('finished');
                }
                return;
            }

            var now = Date.now();
            if (now - _lastEnsureRestartTime < 5000) {
                return;
            }
            _lastEnsureRestartTime = now;
            if (typeof window.initWatchdog === 'function') {
                window.initWatchdog();
            }
        }
        window.ensureWatchdogActive = ensureWatchdogActive;

        // Progressive Skeleton Auto-Resolution Hook (SSE + Short-interval Watchdog Polling + 30s Safety Timeout)
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
            if (window._kwSseSafetyTimer) {
                clearTimeout(window._kwSseSafetyTimer);
                window._kwSseSafetyTimer = null;
            }
            if (window._kwEvtSource) {
                try { if (window._kwEvtSource) window._kwEvtSource.close(); } catch(e) { console.debug("[EventSource] Close failed:", e); }
                window._kwEvtSource = null;
            }

            var isWebMode = (window.location.protocol !== 'file:') || (document.body && document.body.getAttribute('data-web-mode') === 'true');
            var sessZidEl = document.getElementById('session-zid');
            var curZid = sessZidEl ? (sessZidEl.textContent || sessZidEl.innerText || "").trim() : "";
            if (!curZid) {
                var params = new URLSearchParams(window.location.search);
                curZid = params.get('session_zid') || params.get('zid') || "";
            }
            if (!curZid && document.body && document.body.getAttribute('data-zid')) {
                curZid = document.body.getAttribute('data-zid') || '';
            }

            var hasSkeletons = countPendingSkeletons() > 0;

            if (isWebMode && curZid && hasSkeletons) {
                var maxBudgetMs = 30000; // 30-second initial safety budget
                var totalActiveElapsedMs = 0;
                var lastVisibleTime = (!document.hidden) ? Date.now() : null;
                var pollIntervalMs = 1000;
                var isPolling = false;
                var resolved = false;

                var getActiveElapsedMs = function() {
                    if (typeof window._kwGetActiveElapsedMs === 'function' && window._kwGetActiveElapsedMs !== getActiveElapsedMs) {
                        return window._kwGetActiveElapsedMs();
                    }
                    var elapsed = totalActiveElapsedMs;
                    if (!document.hidden && lastVisibleTime) {
                        elapsed += (Date.now() - lastVisibleTime);
                    }
                    return elapsed;
                };

                window._kwWatchdogMaxBudgetMs = maxBudgetMs;
                window._kwGetActiveElapsedMs = getActiveElapsedMs;
                window._kwAdvanceActiveElapsedMs = function(ms) { totalActiveElapsedMs += ms; };

                var pauseWatchdogTimer = function() {
                    if (lastVisibleTime) {
                        totalActiveElapsedMs += (Date.now() - lastVisibleTime);
                        lastVisibleTime = null;
                    }
                    if (window._kwWatchdogMaxTimer) {
                        clearTimeout(window._kwWatchdogMaxTimer);
                        window._kwWatchdogMaxTimer = null;
                    }
                };

                var resumeWatchdogTimer = function() {
                    if (resolved) return;
                    if (document.hidden) return;
                    if (!lastVisibleTime) {
                        lastVisibleTime = Date.now();
                    }
                    if (window._kwWatchdogMaxTimer) {
                        clearTimeout(window._kwWatchdogMaxTimer);
                        window._kwWatchdogMaxTimer = null;
                    }
                    var currentElapsed = getActiveElapsedMs();
                    var remaining = maxBudgetMs - currentElapsed;
                    if (remaining <= 0) {
                        var pendings = countPendingSkeletons();
                        if (pendings > 0) {
                            enterHeartbeatFallback();
                            cleanupOrphanSkeletons();
                        } else {
                            closeEvtSource();
                            stopPolling();
                            cleanupOrphanSkeletons();
                        }
                    } else {
                        window._kwWatchdogMaxTimer = setTimeout(function() {
                            if (!resolved) {
                                var pendings = countPendingSkeletons();
                                if (pendings > 0) {
                                    enterHeartbeatFallback();
                                    cleanupOrphanSkeletons();
                                } else {
                                    closeEvtSource();
                                    stopPolling();
                                    cleanupOrphanSkeletons();
                                }
                            }
                        }, remaining);
                    }
                };

                var WORKER_STALE_THRESHOLD_SECONDS = 10;

                var executeCleanSkeletonStrip = function() {
                    if (resolved) return;
                    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
                        return;
                    }
                    var pendings = (document.querySelectorAll('.skeleton-loader, [data-pending="true"]'));
                    if (pendings.length > 0) {
                        for (var i = 0; i < pendings.length; i++) {
                            var el = pendings[i];
                            el.classList.remove("skeleton-loader");
                            el.removeAttribute("data-pending");
                        }
                    }
                    if (typeof window.normalizeCardSkeletons === 'function') {
                        window.normalizeCardSkeletons('timeout');
                    }
                    if (typeof window.showToast === 'function') {
                        window.showToast("Background loading timed out. Restored table editing.", "warning");
                    }
                    if (typeof fetch !== 'undefined' && curZid) {
                        var recoveryUrl = "/session/status?zid=" + encodeURIComponent(curZid);
                        fetch(recoveryUrl, { method: 'GET', headers: { 'Accept': 'application/json' } })
                            .then(function(res) { if (res.ok) return res.json(); })
                            .then(function(resObj) {
                                var data = (resObj && resObj.data) ? resObj.data : resObj;
                                if (data && (data.rows || data.translatedText || data.translated_text || data.sentences) && window.receiveUpdate) {
                                    window.receiveUpdate(data);
                                }
                            })
                            .catch(function(err) { console.debug("[Async] Non-fatal operation caught:", err); });
                    }
                };

                var enterHeartbeatFallback = function() {
                    if (resolved) return;
                    var remaining = countPendingSkeletons();
                    if (remaining === 0) return;
                    window._kwIsHeartbeat = true;
                    pollIntervalMs = 3000;
                    if (window._kwSkeletonPollTimer) {
                        clearInterval(window._kwSkeletonPollTimer);
                        window._kwSkeletonPollTimer = null;
                    }
                    window._kwSkeletonPollTimer = setInterval(pollSessionStatus, 3000);
                };
                window._kwEnterHeartbeatFallback = enterHeartbeatFallback;

                var cleanupOrphanSkeletons = function() {
                    if (resolved) return;
                    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
                        enterHeartbeatFallback();
                        return;
                    }
                    if (isWebMode && curZid && typeof fetch !== 'undefined') {
                        var statusUrl = "/session/status?zid=" + encodeURIComponent(curZid);
                        var cController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
                        var cTimeoutId = cController ? setTimeout(function() {
                            try { if (cController) cController.abort(); } catch(e) { console.debug("[AbortController] Abort failed:", e); }
                        }, 4000) : null;
                        var cOpts = { method: 'GET', headers: { 'Accept': 'application/json' } };
                        if (cController) cOpts.signal = cController.signal;

                        fetch(statusUrl, cOpts)
                            .then(function(res) {
                                if (res.ok) return res.json();
                                throw new Error("status fetch failed");
                            })
                            .then(function(resObj) {
                                if (resolved) return;
                                var sData = (resObj && resObj.data) ? resObj.data : resObj;
                                var isBusy = sData ? (
                                    (sData.stage === 'translating' || (sData.status && sData.status.stage === 'translating') || sData.worker_status === 'running') &&
                                    sData.is_finished !== true &&
                                    (!sData.status || sData.status.is_finished !== true)
                                ) : false;
                                if (isBusy) {
                                    // Backend is actively translating (e.g. Argos ML translation in progress):
                                    // Suppress timeout, reset watchdog budget, and continue polling.
                                    totalActiveElapsedMs = 0;
                                    lastVisibleTime = Date.now();
                                    maxBudgetMs += 15000;
                                    window._kwWatchdogMaxBudgetMs = maxBudgetMs;
                                    window._kwIsHeartbeat = false;
                                    pollIntervalMs = 1000;
                                    resumeWatchdogTimer();
                                    startWatchdogPolling(true);
                                    return;
                                }

                                var isDone = sData ? (sData.is_finished === true || sData.stage === 'finished' || (sData.status && (sData.status.is_finished === true || sData.status === 'finished'))) : false;
                                if (isDone) {
                                    resolved = true;
                                    window._kwWatchdogResolved = true;
                                    closeEvtSource();
                                    stopPolling();
                                    if (window._kwWatchdogMaxTimer) {
                                        clearTimeout(window._kwWatchdogMaxTimer);
                                        window._kwWatchdogMaxTimer = null;
                                    }
                                    if ((sData.rows || sData.translatedText || sData.translated_text || sData.sentences) && window.receiveUpdate) {
                                        window.receiveUpdate(sData);
                                    }
                                    if (typeof window.normalizeCardSkeletons === 'function') {
                                        window.normalizeCardSkeletons('finished');
                                    }
                                    if (window.onSessionReload) {
                                        window.onSessionReload();
                                    }
                                    return;
                                }

                                executeCleanSkeletonStrip();
                            })
                            .catch(function() {
                                if (typeof navigator !== 'undefined' && navigator.onLine === false) {
                                    enterHeartbeatFallback();
                                    return;
                                }
                                executeCleanSkeletonStrip();
                            })
                            .finally(function() {
                                if (cTimeoutId) clearTimeout(cTimeoutId);
                            });
                    } else {
                        executeCleanSkeletonStrip();
                    }
                };
                window.cleanupOrphanSkeletons = cleanupOrphanSkeletons;

                var stopPolling = function() {
                    if (window._kwSkeletonPollTimer) {
                        clearInterval(window._kwSkeletonPollTimer);
                        window._kwSkeletonPollTimer = null;
                    }
                };

                var closeEvtSource = function() {
                    if (window._kwEvtSource) {
                        try { if (window._kwEvtSource) window._kwEvtSource.close(); } catch(e) { console.debug("[EventSource] Close failed:", e); }
                        window._kwEvtSource = null;
                    }
                    if (window._kwSseSafetyTimer) {
                        clearTimeout(window._kwSseSafetyTimer);
                        window._kwSseSafetyTimer = null;
                    }
                    if (window._kwSseReconnectTimer) {
                        clearTimeout(window._kwSseReconnectTimer);
                        window._kwSseReconnectTimer = null;
                    }
                };

                var pollSessionStatus = function() {
                    if (document.hidden) {
                        return;
                    }
                    if (isSavePending()) return;
                    if (resolved) {
                        stopPolling();
                        return;
                    }
                    if (lastManualStatusAt !== null && Date.now() - lastManualStatusAt < pollIntervalMs) return;
                    var currentElapsed = getActiveElapsedMs();
                    if (currentElapsed >= maxBudgetMs) {
                        var remainingPendings = countPendingSkeletons();
                        if (remainingPendings > 0 && !window._kwIsHeartbeat) {
                            enterHeartbeatFallback();
                            cleanupOrphanSkeletons();
                            return;
                        } else if (remainingPendings === 0) {
                            stopPolling();
                            cleanupOrphanSkeletons();
                            return;
                        }
                    }
                    if (isPolling) return;
                    isPolling = true;

                    var fetchOpts = { method: 'GET', headers: { 'Accept': 'application/json' } };

                    readSessionStatus(curZid, fetchOpts)
                        .then(function(res) {
                            if (res.ok) return res.data;
                            throw new Error("status endpoint error");
                        })
                        .then(function(resObj) {
                            var data = (resObj && resObj.data) ? resObj.data : resObj;
                            if (data && typeof window.updateActiveProvider === 'function') {
                                var sTextP = data.active_text_provider || data.activeTextProvider;
                                var sLemmaP = data.active_lemma_provider || data.activeLemmaProvider;
                                var sGenP = data.active_provider || data.activeProvider;
                                if (data.status) {
                                    sTextP = sTextP || data.status.active_text_provider || data.status.activeTextProvider;
                                    sLemmaP = sLemmaP || data.status.active_lemma_provider || data.status.activeLemmaProvider;
                                    sGenP = sGenP || data.status.active_provider || data.status.activeProvider;
                                }
                                if (sTextP) window.updateActiveProvider('text', sTextP);
                                if (sLemmaP) window.updateActiveProvider('lemma', sLemmaP);
                                if (sGenP && !sTextP && !sLemmaP) window.updateActiveProvider(null, sGenP);
                            }
                            if (data) {
                                var isBusy = ((data.stage === 'translating' || (data.status && data.status.stage === 'translating') || data.worker_status === 'running') &&
                                    data.is_finished !== true &&
                                    (!data.status || data.status.is_finished !== true));
                                if (isBusy) {
                                    totalActiveElapsedMs = 0;
                                    lastVisibleTime = Date.now();
                                    if (window._kwIsHeartbeat) {
                                        window._kwIsHeartbeat = false;
                                        pollIntervalMs = 1000;
                                        startWatchdogPolling(true);
                                    }
                                }
                            }
                            if (data && (data.rows || data.translatedText || data.translated_text || data.sentences || data.is_finished || data.stage === 'finished' || (data.status && (data.status.is_finished || data.status === 'finished')))) {
                                if ((data.rows || data.translatedText || data.translated_text || data.sentences) && window.receiveUpdate) {
                                    window.receiveUpdate(data);
                                }
                                var remaining = countPendingSkeletons();
                                var isDone = (data.is_finished === true || data.stage === 'finished' || (data.status && (data.status.is_finished === true || data.status === 'finished' || data.status.stage === 'finished')));
                                if (isDone || remaining === 0) {
                                    resolved = true;
                                    window._kwWatchdogResolved = true;
                                    if (window._kwWatchdogMaxTimer) {
                                        clearTimeout(window._kwWatchdogMaxTimer);
                                        window._kwWatchdogMaxTimer = null;
                                    }
                                    closeEvtSource();
                                    stopPolling();
                                    if (isDone && typeof window.normalizeCardSkeletons === 'function') {
                                        window.normalizeCardSkeletons('finished');
                                    }
                                    if (window.onSessionReload) {
                                        window.onSessionReload();
                                    }
                                }
                            }
                        })
                        .catch(function(err) {
                            if (typeof navigator !== 'undefined' && navigator.onLine === false) {
                                enterHeartbeatFallback();
                                return;
                            }
                            var renderCheckUrl = "/?session_zid=" + encodeURIComponent(curZid);
                            var rcController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
                            var rcTimeoutId = rcController ? setTimeout(function() {
                                try { if (rcController) rcController.abort(); } catch(e) { console.debug("[AbortController] Abort failed:", e); }
                            }, 4000) : null;
                            var rcOpts = { method: 'GET' };
                            if (rcController) rcOpts.signal = rcController.signal;

                            fetch(renderCheckUrl, rcOpts)
                                .then(function(res) { return res.text(); })
                                .then(function(htmlText) {
                                    if (htmlText && htmlText.indexOf('skeleton-loader') === -1 && countPendingSkeletons() === 0) {
                                        resolved = true;
                                        if (window._kwWatchdogMaxTimer) {
                                            clearTimeout(window._kwWatchdogMaxTimer);
                                            window._kwWatchdogMaxTimer = null;
                                        }
                                        closeEvtSource();
                                        stopPolling();
                                        if (window.onSessionReload) {
                                            window.onSessionReload();
                                        }
                                    }
                                })
                                .catch(function(err) { console.debug("[Async] Non-fatal operation caught:", err); })
                                .finally(function() {
                                    if (rcTimeoutId) clearTimeout(rcTimeoutId);
                                });
                        })
                        .finally(function() {
                            isPolling = false;
                        });
                };

                var startWatchdogPolling = /** @param {boolean=} restart */ function(restart) {
                    if (resolved) return;
                    if (typeof fetch === 'undefined') return;
                    var remaining = countPendingSkeletons();
                    if (remaining === 0) return;
                    if (restart && window._kwSkeletonPollTimer) {
                        clearInterval(window._kwSkeletonPollTimer);
                        window._kwSkeletonPollTimer = null;
                    }
                    if (!window._kwSkeletonPollTimer) {
                        window._kwSkeletonPollTimer = setInterval(pollSessionStatus, pollIntervalMs);
                        setTimeout(pollSessionStatus, 300);
                    }
                };

                if (!document.hidden) {
                    resumeWatchdogTimer();
                }

                // Visibility-aware listener: pause polling & watchdog timer when hidden, resume & probe on tab activation
                var onVisibilityChange = function() {
                    if (document.hidden) {
                        pauseWatchdogTimer();
                        stopPolling();
                    } else {
                        resumeWatchdogTimer();
                        var remaining = countPendingSkeletons();
                        if (remaining > 0 && !resolved) {
                            startWatchdogPolling();
                            if (typeof window._kwPollSessionStatus === 'function') {
                                window._kwPollSessionStatus();
                            } else {
                                pollSessionStatus();
                            }
                        }
                    }
                };
                window._kwWatchdogOnVisibility = onVisibilityChange;

                if (!window._kwVisibilityListenerAttached) {
                    window._kwVisibilityListenerAttached = true;
                    document.addEventListener('visibilitychange', function() {
                        if (typeof window._kwWatchdogOnVisibility === 'function') {
                            window._kwWatchdogOnVisibility();
                        }
                    });
                }

                // Start concurrent watchdog polling if visible
                if (!document.hidden) {
                    startWatchdogPolling();
                }

                var sseBackoffMs = 1000;
                var connectSse = /** @param {boolean=} forceReconnect */ function(forceReconnect) {
                    if (resolved) return;
                    if (typeof EventSource === 'undefined') return;
                    if (window._kwEvtSource) {
                        if (!forceReconnect && window._kwEvtSource.readyState !== 2) {
                            return;
                        }
                        try { if (window._kwEvtSource) window._kwEvtSource.close(); } catch(e) { console.debug("[EventSource] Close failed:", e); }
                        window._kwEvtSource = null;
                    }
                    if (forceReconnect) {
                        sseBackoffMs = 1000;
                        if (window._kwSseReconnectTimer) {
                            clearTimeout(window._kwSseReconnectTimer);
                            window._kwSseReconnectTimer = null;
                        }
                    }
                    try {
                        var sseUrl = "/events?zid=" + encodeURIComponent(curZid);
                        var evtSource = new EventSource(sseUrl);
                        window._kwEvtSource = evtSource;

                        if (evtSource.addEventListener) {
                            evtSource.addEventListener('provider_failover', function(e) {
                                try {
                                    var failoverData = JSON.parse(e.data);
                                    if (failoverData && typeof window.updateActiveProvider === 'function') {
                                        var tType = failoverData.task_type || failoverData.task;
                                        var nProv = failoverData.new_provider || failoverData.to || failoverData.provider;
                                        if (nProv) {
                                            window.updateActiveProvider(tType, nProv);
                                        }
                                    }
                                } catch(err) { console.debug("[Diagnostic] Handled exception:", err); }
                            });
                        }

                        if (window._kwSseSafetyTimer) {
                            clearTimeout(window._kwSseSafetyTimer);
                            window._kwSseSafetyTimer = null;
                        }
                        window._kwSseSafetyTimer = setTimeout(function() {
                            closeEvtSource();
                        }, maxBudgetMs);

                        evtSource.onopen = function() {
                            sseBackoffMs = 1000;
                        };

                        evtSource.onerror = function(err) {
                            closeEvtSource();
                            if (resolved) return;
                            var remaining = countPendingSkeletons();
                            if (remaining === 0) return;
                            if (!window._kwSseReconnectTimer) {
                                var delay = sseBackoffMs;
                                sseBackoffMs = Math.min(sseBackoffMs * 2, 16000);
                                window._kwSseReconnectTimer = setTimeout(function() {
                                    window._kwSseReconnectTimer = null;
                                    if (!resolved && countPendingSkeletons() > 0) {
                                        connectSse(true);
                                    }
                                }, delay);
                            }
                        };

                        evtSource.onmessage = function(e) {
                            sseBackoffMs = 1000;
                            try {
                                var parsed = JSON.parse(e.data);
                                if (parsed && parsed.type === 'provider_failover' && typeof window.updateActiveProvider === 'function') {
                                    var pfType = parsed.task_type || parsed.task;
                                    var pfProv = parsed.new_provider || parsed.to || parsed.provider;
                                    if (pfProv) {
                                        window.updateActiveProvider(pfType, pfProv);
                                    }
                                }
                                if (parsed && (parsed.type === 'stage' || parsed.type === 'update' || parsed.rows || parsed.stage || parsed.is_finished)) {
                                    if (window.receiveUpdate) {
                                        window.receiveUpdate(parsed);
                                    }
                                    var remaining = countPendingSkeletons();
                                    var isDone = (parsed.is_finished === true || parsed.stage === 'finished' || (parsed.status && (parsed.status.is_finished === true || parsed.status === 'finished' || parsed.status.stage === 'finished')));
                                    if (isDone || remaining === 0) {
                                        resolved = true;
                                        window._kwWatchdogResolved = true;
                                        if (window._kwWatchdogMaxTimer) {
                                            clearTimeout(window._kwWatchdogMaxTimer);
                                            window._kwWatchdogMaxTimer = null;
                                        }
                                        closeEvtSource();
                                        stopPolling();
                                        if (isDone && typeof window.normalizeCardSkeletons === 'function') {
                                            window.normalizeCardSkeletons('finished');
                                        }
                                        if (window.onSessionReload) {
                                            window.onSessionReload();
                                        }
                                    }
                                }
                            } catch(err) { console.debug("[Diagnostic] Handled exception:", err); }
                        };
                    } catch(sseErr) {
                        closeEvtSource();
                    }
                };

                // Channel: EventSource (SSE) running concurrently with watchdog polling
                connectSse();

                window.startPolling = startWatchdogPolling;
                window._kwStartWatchdogPolling = startWatchdogPolling;
                window._kwPollSessionStatus = pollSessionStatus;
                window._kwConnectSse = connectSse;

                if (!window._kwNetworkListenersAttached) {
                    window._kwNetworkListenersAttached = true;
                    window.addEventListener('online', function() {
                        var remaining = countPendingSkeletons();
                        if (remaining > 0) {
                            if (typeof window._kwStartWatchdogPolling === 'function') {
                                window._kwStartWatchdogPolling(true);
                            }
                            if (typeof window._kwPollSessionStatus === 'function') {
                                window._kwPollSessionStatus();
                            }
                            if (typeof window._kwConnectSse === 'function') {
                                window._kwConnectSse(true);
                            }
                        }
                    });

                     var focusDebounceTimer = null;
                    window.addEventListener('focus', function() {
                        var remaining = countPendingSkeletons();
                        if (remaining > 0) {
                            if (focusDebounceTimer) clearTimeout(focusDebounceTimer);
                            focusDebounceTimer = setTimeout(function() {
                                if (typeof window._kwStartWatchdogPolling === 'function') {
                                    window._kwStartWatchdogPolling(false);
                                }
                                if (typeof window._kwPollSessionStatus === 'function') {
                                    window._kwPollSessionStatus();
                                }
                                if (typeof window._kwConnectSse === 'function') {
                                    window._kwConnectSse(false);
                                }
                            }, 150);
                        }
                    });
                }
            }
        }
        window.initWatchdog = initWatchdog;
        try {
            initWatchdog();
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        var workerLaunched = false;
        try {
            var wlEl = document.getElementById('worker-launched');
            if (wlEl && (wlEl.textContent || wlEl.innerText).trim() === 'true') {
                workerLaunched = true;
            }
        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

        var sourceContainer = document.getElementById('source-container');
        var spans = sourceContainer ? sourceContainer.getElementsByTagName('span') : [];
         var tokenSpans = [];
        for (var i = 0; i < spans.length; i++) {
            var sp = spans[i];
            if (sp.classList.contains('word')) {
                tokenSpans.push(sp);
                if (sp.classList.contains('flipped')) {
                    sp.classList.remove('flipped');
                    var orig = sp.getAttribute('data-original-text');
                    if (orig) sp.textContent = orig;
                }
            }
        }
        var lemmaTable = document.getElementById('lemma-table');
        if (lemmaTable) {
            var tbodies = lemmaTable.getElementsByTagName('tbody');
            var rowsContainer = tbodies.length > 0 ? tbodies[0] : lemmaTable;
            var allRows = rowsContainer.getElementsByTagName('tr');
            for (var i = 0; i < allRows.length; i++) {
                if (allRows[i].getAttribute('data-row-id') !== null) {
                    tableRows.push(allRows[i]);
                }
            }
        }

        /** @param {number|string|null|undefined} rowId */
        function findTableRowById(rowId) {
            if (rowId === undefined || rowId === null) return null;
            var rIdStr = String(rowId);
            var rows = (typeof tableRows !== 'undefined' && tableRows && tableRows.length > 0)
                ? tableRows
                :  (document.querySelectorAll('#lemma-table tbody tr[data-row-id], #lemma-table tr[data-row-id]'));
            for (var k = 0; k < rows.length; k++) {
                var tr = rows[k];
                if (getRowGroupIds(tr).indexOf(rIdStr) !== -1) {
                    return tr;
                }
            }
            return null;
        }

        /** @param {HTMLElement|null|undefined} tr */
        function getRowLemma(tr) {
            if (!tr) return '';
            var lemCell = (tr.querySelector('.col-lemma'));
            if (!lemCell) lemCell = (tr.querySelector('[data-col="WordSource"]'));
            if (!lemCell) return '';
            var txt = (lemCell.textContent || lemCell.innerText || '').trim();
            return txt.toLowerCase();
        }

        /** @param {number|string|null|undefined} rowId */
        function getLemmaByRowId(rowId) {
            if (rowId === undefined || rowId === null) return '';
            var strId = String(rowId);
            var tr = findTableRowById(strId);
            if (tr) {
                var l = getRowLemma(tr);
                if (l) return l;
            }
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
                var allC = window.WorkspaceTabs.getCards();
                if (allC && allC.length > 0) {
                    for (var c = 0; c < allC.length; c++) {
                        var words = allC[c].words;
                        if (words) {
                            for (var w = 0; w < words.length; w++) {
                                var wObj = words[w];
                                if (String(wObj.row_id) === strId) return (wObj.lemma || '').trim().toLowerCase();
                                if (wObj.all_row_ids) {
                                    for (var a = 0; a < wObj.all_row_ids.length; a++) {
                                        if (String(wObj.all_row_ids[a]) === strId) return (wObj.lemma || '').trim().toLowerCase();
                                    }
                                }
                            }
                        }
                    }
                }
            }
            return '';
        }

        function syncSelectionsToCardsAndState() {
            if (!window.AppState) window.AppState = {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
            if (!window.AppState.selectedLemmas) window.AppState.selectedLemmas = {};
            var selMap = window.AppState.selectedLemmas;

            // 1. Update current tableRows and selectedRowIdsMap
            var rows = (typeof tableRows !== 'undefined' && tableRows && tableRows.length > 0)
                ? tableRows
                :  (document.querySelectorAll('#lemma-table tbody tr[data-row-id], #lemma-table tr[data-row-id]'));
            if (rows && rows.length > 0) {
                for (var i = 0; i < rows.length; i++) {
                    var r = rows[i];
                    var rLem = getRowLemma(r);
                    var rIdStr = String(r.getAttribute('data-row-id'));
                    var pIds = getRowGroupIds(r);
                    if (rLem && selMap[rLem]) {
                        for (var p = 0; p < pIds.length; p++) selectedRowIdsMap[pIds[p]] = true;
                    } else if (rLem && !selMap[rLem]) {
                        for (var p = 0; p < pIds.length; p++) delete selectedRowIdsMap[pIds[p]];
                    }
                }
            }

            var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
            cards.forEach(function(card) {
                 var cardSelections = [];
                card.selected_ids = cardSelections;
                (card.words || []).forEach(function(word) {
                    var lemma = String(word.lemma || '').trim().toLowerCase();
                    if (selMap[lemma]) cardSelections.push(String(word.row_id));
                });
            });
        }

        function selectionOverlay() {
            var pending = JSON.parse(window.getDeltas());
            if (saveState && saveState.phase === 'saving') pending = pending.concat(saveState.snapshot);
            if (nativeSaveSnapshot) pending = pending.concat(nativeSaveSnapshot.deltas);
            var pendingIds = {};
            pending.forEach(function(delta) {
                if (delta.column === 'DeskSelected') pendingIds[String(delta.row_id)] = true;
            });
            var overlay = {};
            if (!Object.keys(pendingIds).length) return overlay;
            function project(rowIds, lemma) {
                if (!lemma) return;
                rowIds.forEach(function(rowId) {
                    var id = String(rowId);
                    if (!pendingIds[id]) return;
                    overlay[lemma] = !!window.AppState.selectedLemmas[lemma];
                    delete pendingIds[id];
                });
            }
            tableRows.forEach(function(row) {
                project(getRowGroupIds(row), getRowLemma(row));
            });
            if (!Object.keys(pendingIds).length) return overlay;
            var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
            cards.forEach(function(card) {
                (card.words || []).forEach(function(word) {
                    project([word.row_id].concat(word.all_row_ids || []), String(word.lemma || '').trim().toLowerCase());
                });
            });
            return overlay;
        }

        window.applySharedSelection = function(membership) {
            if (!membership) return;
            membershipRevision++;
            var overlay = selectionOverlay();
            var state = window.AppStore.getState();
             var membershipProjection = {};
            Object.keys(membership).forEach(function(lemma) {
                if (membership[lemma]) membershipProjection[lemma] = true;
            });
            Object.keys(overlay).forEach(function(lemma) {
                if (overlay[lemma]) membershipProjection[lemma] = true;
                else delete membershipProjection[lemma];
            });
            var changed = Object.keys(state.selectedLemmas).some(function(lemma) { return !membershipProjection[lemma]; }) ||
                Object.keys(membershipProjection).some(function(lemma) { return !state.selectedLemmas[lemma]; });
            state.selectedLemmas = membershipProjection;
            var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
            cards.forEach(function(card) {
                (card.words || []).forEach(function(word) {
                    var lemma = String(word.lemma || '').trim().toLowerCase();
                    if (!Object.prototype.hasOwnProperty.call(overlay, lemma)) word.selected = membership[lemma] ? '1' : '0';
                });
            });
            tableRows.forEach(function(row) {
                var lemma = getRowLemma(row);
                if (!Object.prototype.hasOwnProperty.call(overlay, lemma)) initialHighlights[String(row.getAttribute('data-row-id'))] = !!membership[lemma];
            });
            syncSelectionsToCardsAndState();
            if (!changed) { updateToolbarState(); return; }
            updateRowStyles();
            updateBidirectionalHighlights();
            updateToolbarState();
        };

        if (window.stopSelectionBridge) window.stopSelectionBridge();
        var selectionBridge = {phase: 'stopped', timer: null};
        function stopSelectionBridge() {
            if (selectionBridge.phase === 'stopped') return true;
            var subscription = selectionBridge;
            selectionBridge = {phase: 'stopped', timer: null};
            clearInterval(subscription.timer);
            var body = JSON.stringify({session_zid: subscription.session, client_id: subscription.consumer, close: true});
            try {
                selectionReadResult(JSON.parse(window.ahkCall('get_shared_selection', body)));
                return true;
            } catch (err) {
                console.error('[Selection] Native subscription disposal failed', err);
                return false;
            }
        }
        function pollSelectionBridge(subscription) {
            if (selectionBridge !== subscription) return;
            if (!isCurrentSaveSession(subscription)) {
                stopSelectionBridge();
                return;
            }
            try {
                var response = JSON.parse(window.ahkCall('get_shared_selection', JSON.stringify({session_zid: subscription.session,
                    client_id: subscription.consumer, accept_updates: !window.isDirty()})));
                if (selectionBridge !== subscription) return;
                var membership = selectionReadResult(response);
                response = response.data || response;
                (response.events || []).forEach(function(event) { window.receiveUpdate(event); });
                window.applySharedSelection(membership);
            } catch (err) {
                console.error('[Selection] Native synchronization failed', err);
            }
        }
        window.stopSelectionBridge = stopSelectionBridge;
        function validateNativeSubscription(consumer, interval) {
            if (typeof consumer !== 'string' || !consumer.trim()) throw new Error('Native consumer identity is required');
            if (typeof interval !== 'number' || !isFinite(interval) || interval <= 0) throw new Error('Invalid native polling interval');
        }
        window.startSelectionBridge = function(consumer, interval) {
            if (interval === undefined) interval = 200;
            validateNativeSubscription(consumer, interval);
            if (selectionBridge.consumer === consumer && selectionBridge.session === getSessionZid()) return;
            stopSelectionBridge();
            var subscription = {phase: 'running', consumer: consumer, session: getSessionZid(), timer: null};
            selectionBridge = subscription;
            subscription.timer = setInterval(function() { pollSelectionBridge(subscription); }, interval);
            pollSelectionBridge(subscription);
        };
        addEvent(window, 'pagehide', stopSelectionBridge);
        addEvent(window, 'beforeunload', stopSelectionBridge);

        /** @param {string} lemma @param {boolean} isSelected */
        function setLemmaSelected(lemma, isSelected) {
            if (!lemma) return;
            var norm = String(lemma).trim().toLowerCase();
            if (!norm) return;
            if (!window.AppState) window.AppState = {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
            if (!window.AppState.selectedLemmas) window.AppState.selectedLemmas = {};
            window.AppStore.dispatch({type: 'SET_LEMMA_SELECTION', payload: {lemma: norm, selected: isSelected}});
        }
        window.getRowLemma = getRowLemma;
        window.getLemmaByRowId = getLemmaByRowId;
        window.setLemmaSelected = setLemmaSelected;
        window.syncSelectionsToCardsAndState = syncSelectionsToCardsAndState;

        var COMPOUND_JOINER_SET = new Set((window.__CONFIG__ || {}).compound_joiners || "-_.:#@");

        /** @param {string} txt */
        function isJoinerText(txt) {
            if (!txt || txt.length === 0) return false;
            for (var i = 0; i < txt.length; i++) {
                if (!COMPOUND_JOINER_SET.has(txt[i])) return false;
            }
            return true;
        }

        /** @param {Node|null} node */
        function isCompoundDelimiterNode(node) {
            if (!node) return false;
            if (node.nodeType === 3) { // Text node
                var txt = node._origVal !== undefined ? node._origVal : (node.nodeValue || node.textContent || "");
                return isJoinerText(txt);
            }
            if (node.nodeType === 1) { // Element node (e.g. <span class="sentence-delimiter">-</span>)
                if ( (node).classList &&  (node).classList.contains('sentence-delimiter')) {
                    var txt = node._origVal !== undefined ? node._origVal : (node.textContent || "");
                    return isJoinerText(txt);
                }
            }
            return false;
        }

        /** @param {HTMLElement} span */
        function findCompoundSiblingSpans(span) {
            if (!span || !span.classList || !span.classList.contains('word')) return span ? [span] : [];

            var compoundId = span.getAttribute('data-compound-id');
            var startSpan = span;
            var curr = span;
            while (curr) {
                var prevNode = (curr.previousSibling);
                if (isCompoundDelimiterNode(prevNode)) {
                    var prevSpan = ((prevNode && prevNode.previousSibling));
                    if (prevSpan && prevSpan.nodeType === 1 && prevSpan.classList && prevSpan.classList.contains('word')) {
                        startSpan = prevSpan;
                        curr = prevSpan;
                        continue;
                    }
                } else if (prevNode && prevNode.nodeType === 1 && prevNode.classList && prevNode.classList.contains('word')) {
                    var prevCompoundId = prevNode.getAttribute('data-compound-id');
                    if (compoundId && prevCompoundId && compoundId === prevCompoundId) {
                        startSpan = prevNode;
                        curr = prevNode;
                        continue;
                    }
                }
                break;
            }

            var group = [startSpan];
            curr = startSpan;
            while (curr) {
                var nextNode = (curr.nextSibling);
                if (isCompoundDelimiterNode(nextNode)) {
                    var nextSpan = ((nextNode && nextNode.nextSibling));
                    if (nextSpan && nextSpan.nodeType === 1 && nextSpan.classList && nextSpan.classList.contains('word')) {
                        group.push(nextSpan);
                        curr = nextSpan;
                        continue;
                    }
                } else if (nextNode && nextNode.nodeType === 1 && nextNode.classList && nextNode.classList.contains('word')) {
                    var nextCompoundId = nextNode.getAttribute('data-compound-id');
                    var currCompoundId = curr.getAttribute('data-compound-id');
                    if (currCompoundId && nextCompoundId && currCompoundId === nextCompoundId) {
                        group.push(nextNode);
                        curr = nextNode;
                        continue;
                    }
                }
                break;
            }

            return group;
        }
        window.isCompoundDelimiterNode = isCompoundDelimiterNode;
        window.findCompoundSiblingSpans = findCompoundSiblingSpans;

        /** @param {string} pos */
        function isNonVerbalPartOfSpeech(pos) {
            if (!pos) return false;
            var p = String(pos).trim().toLowerCase();
            if (p === 'art.' || p === 'art' || p === 'artikel' || p === 'article' ||
                p === 'pron.' || p === 'pron' || p === 'pronoun' || p === 'pronomen' ||
                p === 'det.' || p === 'det' || p === 'determiner' ||
                p === 'noun' || p === 'noun.' || p === 'n.' || p === 'subst.' || p === 'subst' || p === 'substantiv' ||
                p === 'adv.' || p === 'adv' || p === 'adverb' ||
                p === 'adj.' || p === 'adj' || p === 'adjective' || p === 'adjektiv') {
                return true;
            }
            if (p.indexOf('art') === 0 || p.indexOf('pron') === 0 || p.indexOf('det') === 0 || p.indexOf('noun') === 0 || p.indexOf('subst') === 0) {
                return true;
            }
            return false;
        }

        /** @param {HTMLElement} span */
        function getWordPOS(span) {
            if (!span) return "";
            var td = findTokenData(span);
            if (td && td.pos) return td.pos;
            if (td && td.row_ids && td.row_ids.length > 0) {
                for (var i = 0; i < td.row_ids.length; i++) {
                    var tr = findTableRowById(td.row_ids[i]);
                    if (tr) {
                        var posCell = (tr.querySelector('td.col-pos'));
                        if (posCell) {
                            var text = (posCell.textContent || posCell.innerText || "").trim();
                            if (text) return text;
                        }
                    }
                }
            }
            return "";
        }
        window.getWordPOS = getWordPOS;

        /** @param {HTMLElement} span */
        function findSeparablePartnerSpans(span) {
            if (!span || !span.classList || !span.classList.contains('word')) return span ? [span] : [];
            var td = findTokenData(span);
            if (!td || !td.row_ids || td.row_ids.length === 0) return [span];

            var spanPos = (td && td.pos) ? td.pos : getWordPOS(span);
            if (isNonVerbalPartOfSpeech(spanPos)) {
                return [span];
            }

            var isSpanSeparableCandidate = span.classList.contains('highlight-purple') ||
                (td && td.token_order && String(td.token_order).indexOf('+') !== -1);

            if (!isSpanSeparableCandidate) {
                var isMultiToken = false;
                for (var r = 0; r < td.row_ids.length; r++) {
                    var tr = findTableRowById(td.row_ids[r]);
                    if (tr) {
                        var infCell = (tr.querySelector('td.col-inflected'));
                        var infText = infCell ? (infCell.textContent || infCell.innerText || '').trim() : '';
                        if (infText.indexOf(' ') !== -1 || infText.indexOf('+') !== -1) {
                            isMultiToken = true;
                            break;
                        }
                    }
                }
                if (!isMultiToken) {
                    return [span];
                }
            }

            var partners = [span];
            var targetRowIds = td.row_ids;
            for (var k = 0; k < tokenSpans.length; k++) {
                var otherSpan = tokenSpans[k];
                if (otherSpan === span) continue;
                var otd = findTokenData(otherSpan);
                if (!otd || !otd.row_ids || otd.row_ids.length === 0) continue;

                var otherPos = (otd && otd.pos) ? otd.pos : getWordPOS(otherSpan);
                if (isNonVerbalPartOfSpeech(otherPos)) continue;

                var isOtherSeparableCandidate = otherSpan.classList.contains('highlight-purple') ||
                    (otd && otd.token_order && String(otd.token_order).indexOf('+') !== -1);

                if (!isOtherSeparableCandidate) {
                    var otherMulti = false;
                    for (var r = 0; r < otd.row_ids.length; r++) {
                        var tr = findTableRowById(otd.row_ids[r]);
                        if (tr) {
                            var infCell = (tr.querySelector('td.col-inflected'));
                            var infText = infCell ? (infCell.textContent || infCell.innerText || '').trim() : '';
                            if (infText.indexOf(' ') !== -1 || infText.indexOf('+') !== -1) {
                                otherMulti = true;
                                break;
                            }
                        }
                    }
                    if (!otherMulti) continue;
                }

                var shared = false;
                var spanSent = span.getAttribute('data-sentence-idx');
                var otherSent = otherSpan.getAttribute('data-sentence-idx');
                var sameSent = (spanSent && otherSent)
                    ? (spanSent === otherSent)
                    : (otd.sentence_idx === undefined || td.sentence_idx === undefined || String(otd.sentence_idx) === String(td.sentence_idx));
                if (sameSent) {
                    for (var r = 0; r < targetRowIds.length; r++) {
                        if (otd.row_ids.indexOf(targetRowIds[r]) !== -1) {
                            shared = true;
                            break;
                        }
                    }
                }
                if (shared && partners.indexOf(otherSpan) === -1) {
                    partners.push(otherSpan);
                }
            }
            return partners;
        }

        /** @param {HTMLElement} span */
        function findRelatedTokenSpans(span) {
            var sepPartners = findSeparablePartnerSpans(span);
            return sepPartners.length > 0 ? sepPartners : [span];
        }

        /** @param {HTMLElement} tr */
        function getRowGroupIds(tr) {
            if (!tr) return [];
            var ids = [String(tr.getAttribute('data-row-id'))];
            var allAttr = tr.getAttribute('data-all-row-ids');
            if (allAttr) {
                var parts = allAttr.split(',');
                for (var i = 0; i < parts.length; i++) {
                    var p = parts[i].trim();
                    if (p && ids.indexOf(p) === -1) ids.push(p);
                }
            }
            return ids;
        }
        /** @param {HTMLElement} tr */
        function getExtraRowIds(tr) { return getRowGroupIds(tr).slice(1); }
        window.getRowGroupIds = getRowGroupIds;
        window.getExtraRowIds = getExtraRowIds;

        var LEGACY_OVERVIEW_CUTOFF_ZID = '20261002000000';
        function isLegacyOverviewContext() {
            if (!window.WorkspaceTabs || typeof window.WorkspaceTabs.getActiveCard !== 'function') return false;
            var activeCard = window.WorkspaceTabs.getActiveCard();
            var isOverviewTab = !!(activeCard && activeCard.index === 0);
            var cardZid = '';
            if (activeCard && activeCard.zid) {
                cardZid = String(activeCard.zid);
            } else if (typeof SESSION_ZID !== 'undefined' && SESSION_ZID && SESSION_ZID !== '__SESSION_ZID__') {
                cardZid = String(SESSION_ZID);
            } else if (window.__CONFIG__ && window.__CONFIG__.zid) {
                cardZid = String(window.__CONFIG__.zid);
            }
            return !!(isOverviewTab && cardZid && cardZid < LEGACY_OVERVIEW_CUTOFF_ZID);
        }
        window.isLegacyOverviewContext = isLegacyOverviewContext;

        /** @param {Record<string,boolean>} ctx */
        function decideTokenClickAction(ctx) {
            var isTableSelectedToken = !!(ctx.isRowSelected && !ctx.isRowBackedByActiveToken);
            var shouldDeselect = ctx.hasActiveTokens
                ? !!(ctx.isInActiveSelections || ctx.isTokenPinned || isTableSelectedToken || ctx.isOrphanSelected)
                : !!(ctx.isVisuallyActive || ctx.isRowSelected || ctx.isLemmaSelected || ctx.isInActiveSelections || ctx.isTokenPinned || ctx.isOrphanSelected);
            return { shouldDeselect: shouldDeselect, isTableSelectedToken: isTableSelectedToken };
        }
        window.decideTokenClickAction = decideTokenClickAction;

        /** @param {HTMLElement|null} span @returns {Token|null} */
        function findTokenData(span) {
            if (!span) return null;
            var wordIdx = parseInt(String(span.getAttribute('data-word-idx') || span.getAttribute('data-order') || span.getAttribute('data-token-order')), 10);
            var spanText = (span.getAttribute('data-word') || span.textContent || '').trim().toLowerCase();
            var rawSent = parseInt(String(span.getAttribute('data-sentence-idx')), 10);
            var spanSent = isNaN(rawSent) ? null : rawSent;

            /** @param {VocabularyRow} wObj @param {WorkspaceCard} card */
            function resolveSent(wObj, card) {
                if (spanSent !== null) return spanSent;
                var w = parseInt(String(wObj.sentence_idx), 10);
                if (!isNaN(w)) return w;
                return (card && card.sentence_idx > 0) ? card.sentence_idx : null;
            }

            for (var i = 0; i < tokenMap.length; i++) {
                var t = tokenMap[i];
                if (t.visual_idx === wordIdx || t.token_order === wordIdx) {
                    return t;
                }
            }
            if (window.AppState && window.AppState.sentenceCards) {
                var cards = window.AppState.sentenceCards;
                for (var c = 0; c < cards.length; c++) {
                    var words = cards[c].words || [];
                    for (var w = 0; w < words.length; w++) {
                        var wObj = words[w];
                        var wOrder = wObj.token_order !== undefined ? wObj.token_order : wObj.row_id;
                        if (wOrder === wordIdx || wObj.row_id === wordIdx) {
                            return {
                                text: wObj.word,
                                lemma: wObj.lemma,
                                visual_idx: wOrder,
                                token_order: wOrder,
                                row_ids: [wObj.row_id],
                                atomic_row_ids: [wObj.row_id],
                                is_word: true,
                                sentence_idx: resolveSent(wObj, cards[c])
                            };
                        }
                    }
                }
                if (spanText) {
                    var candidates = [];
                    var seenRowIds = {};
                    for (var c = 0; c < cards.length; c++) {
                        var words = cards[c].words || [];
                        for (var w = 0; w < words.length; w++) {
                            var wObj = words[w];
                            if (wObj.word && wObj.word.trim().toLowerCase() === spanText) {
                                if (spanSent === null || String(wObj.sentence_idx) === String(spanSent)) {
                                    if (!seenRowIds[wObj.row_id]) {
                                        seenRowIds[wObj.row_id] = true;
                                        var wOrder = wObj.token_order !== undefined ? wObj.token_order : wObj.row_id;
                                        candidates.push({
                                            text: wObj.word,
                                            lemma: wObj.lemma,
                                            visual_idx: wOrder,
                                            token_order: wOrder,
                                            row_ids: [wObj.row_id],
                                            atomic_row_ids: [wObj.row_id],
                                            is_word: true,
                                            sentence_idx: resolveSent(wObj, cards[c])
                                        });
                                    }
                                }
                            }
                        }
                    }
                    if (candidates.length === 1) {
                        return candidates[0];
                    }
                }
            }
            if (spanText) {
                var rows = (document.querySelectorAll('#lemma-table tbody tr[data-row-id]'));
                var matchedRows = [];
                for (var r = 0; r < rows.length; r++) {
                    var tr = rows[r];
                    var rId = parseInt(String(tr.getAttribute('data-row-id')), 10);
                    var srcInfCell = (tr.querySelector('td[data-col="WordSourceInflectedForm"]'));
                    var srcCell = (tr.querySelector('td[data-col="WordSource"]'));
                    var t1 = (srcInfCell ? (srcInfCell.textContent || '') : '').trim().toLowerCase();
                    var t2 = (srcCell ? (srcCell.textContent || '') : '').trim().toLowerCase();
                    if (t1 === spanText || t2 === spanText) {
                        matchedRows.push({
                            text: spanText,
                            lemma: t2 || t1,
                            visual_idx: wordIdx,
                            token_order: wordIdx,
                            row_ids: [rId],
                            atomic_row_ids: [rId],
                            is_word: true,
                            sentence_idx: spanSent
                        });
                    }
                }
                if (matchedRows.length === 1) {
                    return matchedRows[0];
                }
            }
            return null;
        }

        /** @param {number|string} wordIdx */
        function findTokenDataByVisualIdx(wordIdx) {
            wordIdx = parseInt(String(wordIdx), 10);
            for (var i = 0; i < tokenMap.length; i++) {
                if (tokenMap[i].visual_idx === wordIdx) {
                    return tokenMap[i];
                }
            }
            return null;
        }

        /** @param {number} visualIdx */
        function isTokenInActiveSelections(visualIdx) {
            if (!window.AppState || !window.AppState.activeTokenSelections) return false;
            visualIdx = parseInt(String(visualIdx), 10);
            for (var i = 0; i < window.AppState.activeTokenSelections.length; i++) {
                if (window.AppState.activeTokenSelections[i].visual_idx === visualIdx) {
                    return true;
                }
            }
            return false;
        }

        /** @param {string} rowIdStr @param {Object<string, boolean>} excludedVisualIdxsMap */
        function isRowStillNeededByOtherActiveTokens(rowIdStr, excludedVisualIdxsMap) {
            if (!window.AppState || !window.AppState.activeTokenSelections) return false;
            var targetRowIdInt = parseInt(String(rowIdStr), 10);

            var relevantRowIds = {};
            relevantRowIds[String(rowIdStr)] = true;
            if (!isNaN(targetRowIdInt)) relevantRowIds[String(targetRowIdInt)] = true;

            var rows = (typeof tableRows !== 'undefined' && tableRows && tableRows.length > 0)
                ? tableRows
                :  (document.querySelectorAll('#lemma-table tbody tr[data-row-id], #lemma-table tr[data-row-id]'));
            if (rows && rows.length > 0) {
                for (var i = 0; i < rows.length; i++) {
                    var tr = rows[i];
                    var trGroup = getRowGroupIds(tr);
                    var containsTarget = false;
                    for (var g = 0; g < trGroup.length; g++) {
                        if (trGroup[g] === String(rowIdStr) || (!isNaN(targetRowIdInt) && parseInt(String(trGroup[g]), 10) === targetRowIdInt)) {
                            containsTarget = true;
                            break;
                        }
                    }
                    if (containsTarget) {
                        for (var g = 0; g < trGroup.length; g++) {
                            relevantRowIds[trGroup[g]] = true;
                        }
                    }
                }
            }

            for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                var item = window.AppState.activeTokenSelections[a];
                if (excludedVisualIdxsMap && excludedVisualIdxsMap[String(item.visual_idx)]) continue;
                var td = findTokenDataByVisualIdx ? findTokenDataByVisualIdx(Number(item.visual_idx)) : null;
                if (!td) {
                    var s = (document.querySelector('span[data-word-idx="' + item.visual_idx + '"]'));
                    if (s) td = findTokenData(s);
                }
                var atomics = td ? ((td.atomic_row_ids !== undefined) ? td.atomic_row_ids : (td.row_ids || [])) : [];
                for (var r = 0; r < atomics.length; r++) {
                    if (relevantRowIds[String(atomics[r])]) {
                        return true;
                    }
                }
            }
            return false;
        }
        window.isRowStillNeededByOtherActiveTokens = isRowStillNeededByOtherActiveTokens;

        /** @param {string} lemmaStr @param {Object<string, boolean>} excludedVisualIdxsMap */
        function isLemmaStillNeededByOtherActiveTokens(lemmaStr, excludedVisualIdxsMap) {
            if (!lemmaStr || !window.AppState || !window.AppState.activeTokenSelections) return false;
            var normLemma = String(lemmaStr).trim().toLowerCase();
            if (!normLemma) return false;
            for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                var item = window.AppState.activeTokenSelections[a];
                if (excludedVisualIdxsMap && excludedVisualIdxsMap[String(item.visual_idx)]) continue;
                var td = findTokenDataByVisualIdx ? findTokenDataByVisualIdx(Number(item.visual_idx)) : null;
                if (!td) {
                    var s = (document.querySelector('span[data-word-idx="' + item.visual_idx + '"]'));
                    if (s) td = findTokenData(s);
                }
                if (td) {
                    var atomics = (td.atomic_row_ids !== undefined) ? td.atomic_row_ids : (td.row_ids || []);
                    for (var r = 0; r < atomics.length; r++) {
                        var l = getLemmaByRowId(atomics[r]);
                        if (l && l.trim().toLowerCase() === normLemma) {
                            return true;
                        }
                    }
                }
            }
            return false;
        }
        window.isLemmaStillNeededByOtherActiveTokens = isLemmaStillNeededByOtherActiveTokens;

        function saveActiveTokensToStorage() {
            try {
                if (typeof sessionStorage === 'undefined' || !sessionStorage) return;
                var sZid = (typeof getSessionZid === 'function') ? getSessionZid() : (window.__CONFIG__ && window.__CONFIG__.zid ? window.__CONFIG__.zid : '');
                if (!sZid) return;
                var key = 'kw_active_tokens_' + sZid;
                if (window.AppState && window.AppState.activeTokenSelections && window.AppState.activeTokenSelections.length > 0) {
                    sessionStorage.setItem(key, JSON.stringify(window.AppState.activeTokenSelections));
                } else {
                    sessionStorage.removeItem(key);
                }
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        }

        function restoreActiveTokensFromStorage() {
            try {
                if (typeof sessionStorage === 'undefined' || !sessionStorage) return;
                var sZid = (typeof getSessionZid === 'function') ? getSessionZid() : (window.__CONFIG__ && window.__CONFIG__.zid ? window.__CONFIG__.zid : '');
                if (!sZid) return;
                var key = 'kw_active_tokens_' + sZid;
                var raw = sessionStorage.getItem(key);
                if (raw) {
                    var items = JSON.parse(raw);
                    if (Array.isArray(items) && items.length > 0) {
                        if (!window.AppState) window.AppState = {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
                        window.AppState.activeTokenSelections = items;
                        if (!window.AppState.selectedLemmas) window.AppState.selectedLemmas = {};
                        for (var i = 0; i < items.length; i++) {
                            var it = items[i];
                            if (it.lemma) {
                                var normLem = String(it.lemma).trim().toLowerCase();
                                if (normLem) {
                                    window.AppState.selectedLemmas[normLem] = true;
                                }
                            }
                            if (it.atomic_id !== null && it.atomic_id !== undefined) {
                                selectedRowIdsMap[String(it.atomic_id)] = true;
                            }
                            if (it.atomic_row_ids && Array.isArray(it.atomic_row_ids)) {
                                for (var r = 0; r < it.atomic_row_ids.length; r++) {
                                    if (it.atomic_row_ids[r] !== null && it.atomic_row_ids[r] !== undefined) {
                                        selectedRowIdsMap[String(it.atomic_row_ids[r])] = true;
                                    }
                                }
                            }
                            if (it.row_ids && Array.isArray(it.row_ids)) {
                                for (var r = 0; r < it.row_ids.length; r++) {
                                    if (it.row_ids[r] !== null && it.row_ids[r] !== undefined) {
                                        selectedRowIdsMap[String(it.row_ids[r])] = true;
                                    }
                                }
                            }
                        }
                    }
                }
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        }

        function clearTokenSelections() {
            if (window.AppState) {
                window.AppState.activeTokenSelections = [];
            }
            saveActiveTokensToStorage();
        }

        /** @param {number|string} rowId @param {string} lemma */
        function pruneActiveTokensByRowOrLemma(rowId, lemma) {
            if (!window.AppState || !window.AppState.activeTokenSelections || window.AppState.activeTokenSelections.length === 0) return;
            var rStr = (rowId !== undefined && rowId !== null) ? String(rowId) : null;
            var lStr = (lemma !== undefined && lemma !== null) ? String(lemma).trim().toLowerCase() : null;
            window.AppState.activeTokenSelections = window.AppState.activeTokenSelections.filter(function(item) {
                if (lStr && item.lemma && String(item.lemma).trim().toLowerCase() === lStr) return false;
                if (rStr) {
                    if (item.atomic_id !== undefined && String(item.atomic_id) === rStr) return false;
                    if (item.row_id !== undefined && String(item.row_id) === rStr) return false;
                    if (item.row_ids && item.row_ids.some(function(id) { return String(id) === rStr; })) return false;
                    if (item.atomic_row_ids && item.atomic_row_ids.some(function(id) { return String(id) === rStr; })) return false;
                }
                return true;
            });
            if (typeof saveActiveTokensToStorage === 'function') {
                saveActiveTokensToStorage();
            }
        }
        window.pruneActiveTokensByRowOrLemma = pruneActiveTokensByRowOrLemma;

        /** @param {HTMLElement} span */
        function addTokenToActiveSelections(span) {
            if (!window.AppState) window.AppState = {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
            if (!window.AppState.activeTokenSelections) window.AppState.activeTokenSelections = [];
            var relatedSpans = findRelatedTokenSpans(span);
            for (var i = 0; i < relatedSpans.length; i++) {
                var s = relatedSpans[i];
                var td = findTokenData(s);
                var vIdx = td ? td.visual_idx : parseInt(String(s.getAttribute('data-word-idx')), 10);
                if (isNaN(Number(vIdx))) continue;
                var exists = false;
                for (var j = 0; j < window.AppState.activeTokenSelections.length; j++) {
                    if (window.AppState.activeTokenSelections[j].visual_idx === vIdx) {
                        exists = true;
                        break;
                    }
                }
                if (!exists) {
                    var sIdx = (td && td.sentence_idx !== undefined && !isNaN(Number(td.sentence_idx))) ? td.sentence_idx : null;
                    if (sIdx === null) {
                        var chunkEl = (s.closest && s.closest('.kw-sentence-chunk, [data-sentence-idx]')) || s.parentElement;
                        if (chunkEl && chunkEl.getAttribute) {
                            var cSent = parseInt(String(chunkEl.getAttribute('data-sentence-idx')), 10);
                            if (!isNaN(cSent)) sIdx = cSent;
                        }
                    }
                    if (sIdx === null) {
                        var sAttr = s.getAttribute('data-sentence-idx');
                        if (sAttr) {
                            var sNum = parseInt(String(sAttr), 10);
                            if (!isNaN(sNum)) sIdx = sNum;
                        }
                    }
                    if (sIdx === null && window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
                        var ac = window.WorkspaceTabs.getActiveCard();
                        if (ac && ac.sentence_idx !== undefined && !isNaN(ac.sentence_idx)) sIdx = ac.sentence_idx;
                    }
                    var tOrder = (td && td.token_order !== undefined) ? td.token_order : (s.getAttribute('data-token-order') || '');
                    var aId = (td && td.atomic_id !== undefined) ? td.atomic_id : ((td && td.atomic_row_ids && td.atomic_row_ids.length > 0) ? td.atomic_row_ids[0] : ((td && td.row_ids && td.row_ids.length > 0) ? td.row_ids[0] : null));
                    var lem = (td && td.lemma) ? td.lemma : (getWordLemma(s) || '');
                    var rIds = (td && td.row_ids) ? td.row_ids.slice() : ((aId !== null && aId !== undefined) ? [aId] : []);
                    window.AppState.activeTokenSelections.push({
                        visual_idx: vIdx,
                        sentence_idx: sIdx,
                        token_order: tOrder,
                        atomic_id: aId,
                        lemma: lem,
                        row_ids: rIds
                    });
                }
            }
            saveActiveTokensToStorage();
        }

        /** @param {HTMLElement} span */
        function removeTokenFromActiveSelections(span) {
            if (!window.AppState || !window.AppState.activeTokenSelections) return;
            var relatedSpans = findRelatedTokenSpans(span);
            var vIdxsToRemove = {};
            for (var i = 0; i < relatedSpans.length; i++) {
                var s = relatedSpans[i];
                var td = findTokenData(s);
                var vIdx = td ? td.visual_idx : parseInt(String(s.getAttribute('data-word-idx')), 10);
                if (!isNaN(Number(vIdx))) vIdxsToRemove[String(vIdx)] = true;
            }
            window.AppState.activeTokenSelections = window.AppState.activeTokenSelections.filter(function(item) {
                return !vIdxsToRemove[String(item.visual_idx)];
            });
            saveActiveTokensToStorage();
        }

        window.findTokenDataByVisualIdx = findTokenDataByVisualIdx;
        window.isTokenInActiveSelections = isTokenInActiveSelections;
        window.clearTokenSelections = clearTokenSelections;
        window.addTokenToActiveSelections = addTokenToActiveSelections;
        window.removeTokenFromActiveSelections = removeTokenFromActiveSelections;
        window.saveActiveTokensToStorage = saveActiveTokensToStorage;
        window.restoreActiveTokensFromStorage = restoreActiveTokensFromStorage;

        /** @param {HTMLElement} span */
        function getRawRowTranslations(span) {
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return [];
            }
            var translations = [];
            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = findTableRowById(rowId);
                if (tr) {
                    var tds = tr.getElementsByTagName('td');
                    for (var m = 0; m < tds.length; m++) {
                        if (tds[m].getAttribute('data-col') === 'WordDestination') {
                            if ( (tds[m].querySelector('.skeleton-loader')) ||  (tds[m].querySelector('[data-pending="true"]')) || tds[m].classList.contains('skeleton-loader')) {
                                continue;
                            }
                            var trans = tds[m].textContent || tds[m].innerText || "";
                            trans = trans.trim();
                            if (trans.endsWith('...') && !tds[m].hasAttribute('data-provenance')) {
                                continue;
                            }
                            if (trans && translations.indexOf(trans) === -1) {
                                translations.push(trans);
                            }
                        }
                    }
                }
            }
            return translations;
        }

        /** @param {string} lemma @param {string} spanClean */
        function isMatchingSubTokenLemma(lemma, spanClean) {
            if (!lemma || !spanClean) return false;
            var l = lemma.toLowerCase().trim();
            var s = spanClean.toLowerCase().trim();
            if (l === s) return true;
            if (l.indexOf(s) !== -1 || s.indexOf(l) !== -1) return true;
            if (l.length >= 4 && s.length >= 4) {
                var stemLen = Math.min(l.length, s.length, 5);
                if (l.substring(0, stemLen) === s.substring(0, stemLen)) {
                    return true;
                }
            }
            return false;
        }

        /** @param {HTMLElement} span @param {HTMLElement[]} group */
        function getSpanSpecificTranslation(span, group) {
            if (!group) group = findCompoundSiblingSpans(span);
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return "";
            }

            if (!group || group.length <= 1) {
                var raw = getRawRowTranslations(span);
                return raw.join(' ');
            }

            var spanClean = (span.getAttribute('data-lower-clean') || span.getAttribute('data-original-text') || span.textContent || "").trim().toLowerCase();

            var otherSpansClean = [];
            if (group && group.length > 1) {
                for (var i = 0; i < group.length; i++) {
                    if (group[i] !== span) {
                        var otherClean = (group[i].getAttribute('data-lower-clean') || group[i].getAttribute('data-original-text') || group[i].textContent || "").trim().toLowerCase();
                        if (otherClean) otherSpansClean.push(otherClean);
                    }
                }
            }

            var specificTranslations = [];
            var compoundTranslations = [];

            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = findTableRowById(rowId);
                if (!tr) continue;

                var tds = tr.getElementsByTagName('td');
                var lemma = "";
                var trans = "";
                for (var m = 0; m < tds.length; m++) {
                    var col = tds[m].getAttribute('data-col');
                    if (col === '{lemma_col_name}' || col === 'WordSource') {
                        lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                    } else if (col === 'WordDestination') {
                        if ( (tds[m].querySelector('.skeleton-loader')) ||  (tds[m].querySelector('[data-pending="true"]')) || tds[m].classList.contains('skeleton-loader')) {
                            trans = "";
                        } else {
                            var tStr = (tds[m].textContent || tds[m].innerText || "").trim();
                            if (tStr.endsWith('...') && !tds[m].hasAttribute('data-provenance')) {
                                trans = "";
                            } else {
                                trans = tStr;
                            }
                        }
                    }
                }

                if (!trans) continue;

                var lemmaClean = lemma.toLowerCase();
                var isFullCompoundLemma = (lemmaClean.indexOf('-') !== -1 || lemmaClean.indexOf('_') !== -1 || lemmaClean.indexOf(' ') !== -1 || lemmaClean.indexOf('/') !== -1 || lemmaClean.indexOf('.') !== -1 || lemmaClean.indexOf(':') !== -1);

                if (isFullCompoundLemma) {
                    if (compoundTranslations.indexOf(trans) === -1) {
                        compoundTranslations.push(trans);
                    }
                } else {
                    var matchesThis = isMatchingSubTokenLemma(lemmaClean, spanClean);
                    var matchesOther = false;
                    if (group && group.length > 1) {
                        for (var o = 0; o < otherSpansClean.length; o++) {
                            if (isMatchingSubTokenLemma(lemmaClean, otherSpansClean[o])) {
                                matchesOther = true;
                                break;
                            }
                        }
                    }

                    if (matchesThis && !matchesOther) {
                        if (specificTranslations.indexOf(trans) === -1) {
                            specificTranslations.push(trans);
                        }
                    }
                }
            }

            if (specificTranslations.length > 0) {
                return specificTranslations.join(' ');
            }

            // 2. If compound is a group and there is a shared multi-part translation
            if (group.length > 1) {
                var spanIdx = -1;
                for (var i = 0; i < group.length; i++) {
                    if (group[i] === span) {
                        spanIdx = i;
                        break;
                    }
                }

                var rowTranslations = getRawRowTranslations(span);

                if (rowTranslations.length === group.length && spanIdx >= 0 && spanIdx < rowTranslations.length) {
                    return rowTranslations[spanIdx];
                }

                if (rowTranslations.length === 1) {
                    var singleTrans = rowTranslations[0];
                    var parts = singleTrans.indexOf(',') !== -1 ? singleTrans.split(',') : (singleTrans.indexOf('_') !== -1 ? singleTrans.split('_') : []);
                    parts = parts.map(function(p) { return p.trim(); }).filter(function(p) { return p.length > 0; });
                    if (parts.length === group.length && spanIdx >= 0 && spanIdx < parts.length) {
                        return parts[spanIdx];
                    }
                    if (spanIdx === 0) {
                        return singleTrans;
                    } else {
                        return "";
                    }
                }
            }

            var raw = getRawRowTranslations(span);
            return raw.join(' ');
        }

        /** @param {HTMLElement} span */
        function getWordTranslation(span) {
            var group = findCompoundSiblingSpans(span);
            if (group.length > 1) {
                var t = getSpanSpecificTranslation(span, group);
                if (t) return t;
            }

            var raw = getRawRowTranslations(span);
            return raw.join(' ');
        }

        /** @param {HTMLElement} span @param {boolean} toTranslation @param {boolean=} forceCompound */
        function flipWord(span, toTranslation, forceCompound) {
            if (!span) return;
            var group = findCompoundSiblingSpans(span);
            var targets = [span];

            if (forceCompound === true) {
                targets = group;
            } else if (forceCompound === false) {
                targets = [span];
            } else {
                // Default logic: flip full compound ONLY if the compound is already fully selected
                if (group.length > 1) {
                    var allSelected = true;
                    for (var g = 0; g < group.length; g++) {
                        var gSpan = group[g];
                        var hasHighlight = gSpan.classList.contains('highlight-orange-active') || gSpan.classList.contains('highlight-purple-active');
                        var gTd = findTokenData(gSpan);
                        var compoundRowSelected = false;
                        if (gTd && gTd.compound_row_ids) {
                            for (var c = 0; c < gTd.compound_row_ids.length; c++) {
                                if (selectedRowIdsMap.hasOwnProperty(String(gTd.compound_row_ids[c]))) {
                                    compoundRowSelected = true;
                                    break;
                                }
                            }
                        }
                        if (!hasHighlight && !compoundRowSelected) {
                            allSelected = false;
                            break;
                        }
                    }
                    if (allSelected) {
                        targets = group;
                    }
                }
            }

            for (var i = 0; i < targets.length; i++) {
                var s = targets[i];
                if (!s.getAttribute('data-original-text')) {
                    s.setAttribute('data-original-text', String(s.textContent || s.innerText || ""));
                }
                var isFlipped = s.classList.contains('flipped');

                if (toTranslation) {
                    if (!isFlipped) {
                        var trans = getSpanSpecificTranslation(s, group);
                        if (trans !== undefined && trans !== null && trans.trim().length > 0 && !trans.trim().endsWith('...')) {
                            s.classList.add('flipped');
                            s.textContent = trans;
                        } else {
                            s.textContent = s.getAttribute('data-original-text');
                        }
                    }
                } else {
                    if (isFlipped) {
                        s.classList.remove('flipped');
                        s.textContent = s.getAttribute('data-original-text');
                    }
                }
                s.style.display = '';
            }

            // Manage delimiters between sibling spans in the compound group
            for (var j = 0; j < group.length - 1; j++) {
                var currSpan = group[j];
                var nextSpan = (group[j + 1]);
                var delimNode = currSpan.nextSibling;
                if (delimNode && isCompoundDelimiterNode(delimNode)) {
                    if (delimNode._origVal === undefined) {
                        delimNode._origVal = delimNode.nodeValue || "";
                    }
                    delimNode.nodeValue = delimNode._origVal;
                }
            }
        }

        var audioLmbPlay = Boolean(window.__CONFIG__ && window.__CONFIG__.audio_lmb_play);
        var audioLmbSource = ((window.__CONFIG__ && window.__CONFIG__.audio_lmb_source) || "lemma");
        var audioLmbChainMode = ((window.__CONFIG__ && window.__CONFIG__.audio_lmb_chain_mode) || "auto");
        var audioSourceRangeMode = ((window.__CONFIG__ && window.__CONFIG__.audio_source_range_mode) || "word");
        var audioTableRangeMode = ((window.__CONFIG__ && window.__CONFIG__.audio_table_range_mode) || "cell");
        var audioRmbPlay = Boolean(window.__CONFIG__ && window.__CONFIG__.audio_rmb_play);
        var audioRmbChainMode = ((window.__CONFIG__ && window.__CONFIG__.audio_rmb_chain_mode) || "auto");
        var audioRmbSource = ((window.__CONFIG__ && window.__CONFIG__.audio_rmb_source) || "word_translation");
        var audioAnkiTtsCli = ((window.__CONFIG__ && window.__CONFIG__.audio_anki_tts_cli) || "");
        var audioPythonExe = ((window.__CONFIG__ && window.__CONFIG__.audio_python_exe) || "");

        /** @param {string} text @returns {string} */
        function sanitizeSpokenText(text) {
            if (!text) return "";
            var s = String(text);
            if (s.indexOf('|||') !== -1) {
                var parts = s.split('|||');
                var cleanParts = [];
                for (var i = 0; i < parts.length; i++) {
                    var p = sanitizeSpokenText(parts[i]);
                    if (p) cleanParts.push(p);
                }
                return cleanParts.join(' ||| ');
            }
            // Strip 14-digit ZIDs
            s = s.replace(/(^|[^0-9])[0-9]{14}(?![0-9])/g, '$1 ');
            // Replace path separators, colons, and underscore delimiters with spaces
            s = s.replace(/[\/\\_:]/g, ' ');
            // Strip standalone numeric tokens
            s = s.replace(/(^|\s)[0-9]+(?=\s|$)/g, ' ');
            return s.replace(/\s+/g, ' ').trim();
        }

        /** @param {string} text @param {string} lang */
        function playAudio(text, lang) {
            if (!text || !lang) return;
            var clean = sanitizeSpokenText(text);
            if (!clean) return;
            if (window.ahkCall && audioAnkiTtsCli && audioPythonExe) {
                var sanitizedText = clean.replace(/\r?\n|\r/g, ' ');
                var escapedText = sanitizedText.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
                try {
                    window.ahkCall('play', audioPythonExe + "\\n" + audioAnkiTtsCli + "\\n" + lang + "\\n" + escapedText);
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            } else if (typeof fetch === 'function') {
                var headers = { 'Content-Type': 'application/json' };
                if (typeof getApiToken === 'function') {
                    var tok = getApiToken();
                    if (tok) {
                        headers['X-API-Token'] = tok;
                    }
                } else if (typeof API_TOKEN !== 'undefined' && API_TOKEN && API_TOKEN !== '__API_TOKEN__') {
                    headers['X-API-Token'] = API_TOKEN;
                }
                fetch('/api/v1/audio/play', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({ text: clean, language: lang })
                }).catch(function(e) {
                    console.warn('[Audio] Background audio playback endpoint failed:', e);
                });
            }
        }
        window.playAudio = playAudio;

        /** @param {HTMLElement} tr @param {string} sourceMode */
        function getTableRowWordsToPlay(tr, sourceMode) {
            if (!tr) return "";
            var tds = tr.getElementsByTagName('td');
            var lemma = "";
            var inflection = "";
            for (var m = 0; m < tds.length; m++) {
                var col = tds[m].getAttribute('data-col');
                if (col === '{lemma_col_name}' || col === 'WordSource') {
                    lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                } else if (col === '{inflected_col_name}' || col === 'WordSourceInflectedForm') {
                    inflection = (tds[m].textContent || tds[m].innerText || "").trim();
                }
            }
            var textToPlay = (sourceMode === 'inflection' && inflection) ? inflection : lemma;
            if (!textToPlay) textToPlay = lemma || inflection;
            return sanitizeSpokenText(textToPlay);
        }

        /** @param {string} rawInflection @param {string} tokenText */
        function getCleanConstituentInflection(rawInflection, tokenText) {
            if (!rawInflection) return "";
            var parts = rawInflection.split(',');
            parts = parts.map(function(p) { return p.trim(); }).filter(function(p) { return p.length > 0; });
            if (parts.length <= 1) {
                return parts.length === 1 ? parts[0] : rawInflection.trim();
            }
            var cleanToken = (tokenText || "").trim().toLowerCase();
            var filtered = parts.filter(function(p) {
                return p.trim().toLowerCase() !== cleanToken;
            });
            if (filtered.length > 0) {
                return filtered[0];
            }
            return parts[0];
        }

        /** @param {HTMLElement} span @param {string} sourceMode @param {HTMLElement[]} group */
        function getSpanSpecificSpokenWord(span, sourceMode, group) {
            if (!span) return "";
            var spanClean = (span.getAttribute('data-lower-clean') || span.getAttribute('data-original-text') || span.textContent || "").trim().toLowerCase();
            var spanText = (span.getAttribute('data-original-text') || span.textContent || span.innerText || "").trim();
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return spanText;
            }

            var bestRowLemma = "";
            var bestRowInflected = "";
            var exactMatchFound = false;

            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = findTableRowById(rowId);
                if (!tr) continue;

                var tds = tr.getElementsByTagName('td');
                var lemma = "";
                var inflected = "";
                for (var m = 0; m < tds.length; m++) {
                    var col = tds[m].getAttribute('data-col');
                    if (col === '{lemma_col_name}' || col === 'WordSource') {
                        lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                    } else if (col === '{inflected_col_name}' || col === 'WordSourceInflectedForm') {
                        inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                    }
                }

                var lemmaClean = lemma.toLowerCase();
                var inflectedClean = inflected.toLowerCase();
                var inflectedParts = inflectedClean.split(',').map(function(f) { return f.trim(); });

                if (lemmaClean === spanClean || inflectedClean === spanClean || inflectedParts.indexOf(spanClean) !== -1) {
                    bestRowLemma = lemma;
                    bestRowInflected = inflected;
                    exactMatchFound = true;
                    break;
                }

                if (!exactMatchFound && spanClean.length >= 4 && lemmaClean.length >= 4) {
                    var stemLen = Math.min(spanClean.length, lemmaClean.length, 5);
                    if (spanClean.substring(0, stemLen) === lemmaClean.substring(0, stemLen)) {
                        bestRowLemma = lemma;
                        bestRowInflected = inflected;
                        exactMatchFound = true;
                    }
                }

                if (!bestRowLemma && lemma) {
                    bestRowLemma = lemma;
                    bestRowInflected = inflected;
                }
            }

            if (sourceMode === 'inflection') {
                if (bestRowInflected) {
                    if (bestRowInflected.toLowerCase() !== spanClean && (bestRowInflected.indexOf('-') !== -1 || bestRowInflected.indexOf('_') !== -1 || bestRowInflected.indexOf(' ') !== -1 || bestRowInflected.indexOf('/') !== -1 || bestRowInflected.indexOf('\\\\') !== -1)) {
                        return spanText;
                    }
                    var cleanInf = getCleanConstituentInflection(bestRowInflected, spanClean);
                    if (cleanInf.toLowerCase() !== spanClean && !exactMatchFound) {
                        return spanText;
                    }
                    return cleanInf || bestRowLemma || spanText;
                }
                return spanText;
            } else {
                if (bestRowLemma.toLowerCase() !== spanClean && !exactMatchFound) {
                    return spanText;
                }
                if (bestRowLemma.toLowerCase() !== spanClean && (bestRowLemma.indexOf('-') !== -1 || bestRowLemma.indexOf('_') !== -1 || bestRowLemma.indexOf('/') !== -1 || bestRowLemma.indexOf('\\\\') !== -1 || bestRowLemma.indexOf(' ') !== -1)) {
                    return spanText;
                }
                return bestRowLemma || spanText;
            }
        }

        /** @param {HTMLElement} s @param {string} sourceMode @param {string=} chainMode */
        function getSingleTokenWordsToPlay(s, sourceMode, chainMode) {
            if (!s) return "";
             var words = [];
            var tokenData = findTokenData(s);
            var spanClean = (s.getAttribute('data-lower-clean') || s.getAttribute('data-original-text') || s.textContent || "").trim().toLowerCase();
            var spanText = (s.getAttribute('data-original-text') || s.textContent || s.innerText || "").trim();
            var isPurpleConstruct = s.classList && s.classList.contains('highlight-purple');

            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                var sanitized = sanitizeSpokenText(spanText);
                if (sanitized) words.push(sanitized);
            } else if (isPurpleConstruct) {
                for (var j = 0; j < tokenData.row_ids.length; j++) {
                    var rowId = tokenData.row_ids[j];
                    var tr = findTableRowById(rowId);
                    if (!tr) continue;
                    var tds = tr.getElementsByTagName('td');
                    var lemma = "";
                    var inflected = "";
                    for (var m = 0; m < tds.length; m++) {
                        var col = tds[m].getAttribute('data-col');
                        if (col === '{lemma_col_name}' || col === 'WordSource') {
                            lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                        } else if (col === '{inflected_col_name}' || col === 'WordSourceInflectedForm') {
                            inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                        }
                    }
                    var term = (sourceMode === 'inflection' && inflected) ? inflected : lemma;
                    if (!term) term = lemma || inflected || spanText;
                    term = sanitizeSpokenText(term);
                    if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                        words.push(term);
                    }
                }
            } else {
                // Check for direct-match rows (e.g. contractions like "isn't" -> [be, not], or sub-token "camel" -> [camel])
                var directMatchRows = [];
                for (var j = 0; j < tokenData.row_ids.length; j++) {
                    var rowId = tokenData.row_ids[j];
                    var tr = findTableRowById(rowId);
                    if (!tr) continue;

                    var tds = tr.getElementsByTagName('td');
                    var lemma = "";
                    var inflected = "";
                    for (var m = 0; m < tds.length; m++) {
                        var col = tds[m].getAttribute('data-col');
                        if (col === '{lemma_col_name}' || col === 'WordSource') {
                            lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                        } else if (col === '{inflected_col_name}' || col === 'WordSourceInflectedForm') {
                            inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                        }
                    }

                    var lemmaClean = lemma.toLowerCase();
                    var inflectedClean = inflected.toLowerCase();
                    var inflectedParts = inflectedClean.split(',').map(function(f) { return f.trim(); });

                    if (lemmaClean === spanClean || inflectedClean === spanClean || inflectedParts.indexOf(spanClean) !== -1) {
                        directMatchRows.push({ rowId: rowId, lemma: lemma, inflected: inflected });
                    }
                }

                if (directMatchRows.length > 0) {
                    for (var d = 0; d < directMatchRows.length; d++) {
                        var rowObj = directMatchRows[d];
                        if (sourceMode === 'inflection') {
                            var cleanInflection = getCleanConstituentInflection(rowObj.inflected, spanClean);
                            if (cleanInflection && cleanInflection.toLowerCase() !== spanClean && (cleanInflection.indexOf('-') !== -1 || cleanInflection.indexOf('_') !== -1 || cleanInflection.indexOf(' ') !== -1 || cleanInflection.indexOf('/') !== -1 || cleanInflection.indexOf('\\\\') !== -1)) {
                                cleanInflection = (rowObj.lemma && rowObj.lemma.toLowerCase() === spanClean) ? rowObj.lemma : spanText;
                            }
                            var term = sanitizeSpokenText(cleanInflection || rowObj.lemma || spanText);
                            if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                                words.push(term);
                            }
                        } else {
                            var term = sanitizeSpokenText(rowObj.lemma || spanText);
                            if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                                words.push(term);
                            }
                        }
                    }
                } else {
                    var group = findCompoundSiblingSpans(s);
                    if ((!group || group.length <= 1) && tokenData.row_ids.length > 0) {
                        for (var j = 0; j < tokenData.row_ids.length; j++) {
                            var rowId = tokenData.row_ids[j];
                            var tr = findTableRowById(rowId);
                            if (!tr) continue;
                            var tds = tr.getElementsByTagName('td');
                            var lemma = "";
                            var inflected = "";
                            for (var m = 0; m < tds.length; m++) {
                                var col = tds[m].getAttribute('data-col');
                                if (col === '{lemma_col_name}' || col === 'WordSource') {
                                    lemma = (tds[m].textContent || tds[m].innerText || "").trim();
                                } else if (col === '{inflected_col_name}' || col === 'WordSourceInflectedForm') {
                                    inflected = (tds[m].textContent || tds[m].innerText || "").trim();
                                }
                            }
                            var term = (sourceMode === 'inflection' && inflected) ? inflected : lemma;
                            if (!term) term = lemma || inflected || spanText;
                            term = sanitizeSpokenText(term);
                            if (term && (words.length === 0 || words[words.length - 1] !== term)) {
                                words.push(term);
                            }
                        }
                    }
                    if (words.length === 0) {
                        var term = getSpanSpecificSpokenWord(s, sourceMode, [s]);
                        term = sanitizeSpokenText(term);
                        if (term) words.push(term);
                    }
                }
            }

            var delim = (chainMode === 'separate' || chainMode === 'per_word') ? ' ||| ' : ' ';
            return words.join(delim);
        }

        /** @param {HTMLElement} targetSpan @param {string} sourceMode */
        function getCompoundWordsToPlay(targetSpan, sourceMode) {
            if (!targetSpan) return "";
            var group = findCompoundSiblingSpans(targetSpan);
            if (!group || group.length === 0) group = [targetSpan];

             var words = [];
            for (var i = 0; i < group.length; i++) {
                var s = group[i];
                var term = getSingleTokenWordsToPlay(s, sourceMode);
                if (term) {
                    words.push(term);
                }
            }
            return words.join(' ');
        }

        /** @param {HTMLElement} span */
        function getWordLemma(span) {
            return getSingleTokenWordsToPlay(span, 'lemma');
        }

        /** @param {HTMLElement} span */
        function getWordInflectedForm(span) {
            return getSingleTokenWordsToPlay(span, 'inflection');
        }

        window.findTokenData = findTokenData;
        window.findTokenDataByVisualIdx = findTokenDataByVisualIdx;
        window.findTableRowById = findTableRowById;
        window.getRawRowTranslations = getRawRowTranslations;
        window.getWordTranslation = getWordTranslation;
        window.getWordLemma = getWordLemma;
        window.getWordInflectedForm = getWordInflectedForm;

        /** @param {HTMLElement} span */
        function wireTokenSpan(span) {
            if (!span || span.__kw_token_instance === myDeskInstanceId) return;
            span.__kw_token_instance = myDeskInstanceId;
                addEvent(span, 'mousedown', function(e) {
                    if (window.__selectableTextMode) return;
                    if (window.__deskInstanceId && myDeskInstanceId !== window.__deskInstanceId) return;
                    e = (e || window.event);

                    if (e.button === 0) { // LMB
                        var lt = document.getElementById('lemma-table');
                        if (lt) {
                            lt.classList.add('kw-table-dragging');
                        }
                        var clickedTokenData = findTokenData(span);
                        if (!clickedTokenData) return;

                        var targetRowIds = (clickedTokenData.atomic_row_ids !== undefined && clickedTokenData.atomic_row_ids !== null)
                            ? clickedTokenData.atomic_row_ids.slice()
                            : ((clickedTokenData.row_ids && clickedTokenData.row_ids.slice) ? clickedTokenData.row_ids.slice() : []);

                        isTokenDragSelecting = true;
                        dragOccurred = false;
                        mousedownTargetSpan = span;

                        tokenDragStartIdx = -1;
                        tokenDragLastIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                tokenDragStartIdx = k;
                                tokenDragLastIdx = k;
                                break;
                            }
                        }

                        var isVisuallyActive = (span.classList && (
                            span.classList.contains('highlight-orange-active') ||
                            span.classList.contains('highlight-purple-active') ||
                            span.classList.contains('active-subtoken')
                        ));
                        var isTokenPinned = (typeof isTokenPinnedInMVP === 'function') ? isTokenPinnedInMVP(span) : (span.classList && span.classList.contains('hl-mvp-pin'));
                        if (typeof findRelatedTokenSpans === 'function') {
                            var relSpans = findRelatedTokenSpans(span);
                            for (var r = 0; r < relSpans.length; r++) {
                                var rsp = relSpans[r];
                                if (rsp.classList) {
                                    if (rsp.classList.contains('highlight-orange-active') ||
                                        rsp.classList.contains('highlight-purple-active') ||
                                        rsp.classList.contains('active-subtoken')) {
                                        isVisuallyActive = true;
                                    }
                                    if (rsp.classList.contains('hl-mvp-pin')) {
                                        isTokenPinned = true;
                                    }
                                }
                            }
                        }

                        var isInActiveSelections = (clickedTokenData && isTokenInActiveSelections(Number(clickedTokenData.visual_idx)));
                        var hasActiveTokens = (window.AppState && window.AppState.activeTokenSelections && window.AppState.activeTokenSelections.length > 0);

                        var isRowSelected = false;
                        if (targetRowIds && targetRowIds.length > 0) {
                            for (var t = 0; t < targetRowIds.length; t++) {
                                var tidStr = String(targetRowIds[t]);
                                if (selectedRowIdsMap.hasOwnProperty(tidStr)) {
                                    isRowSelected = true;
                                    break;
                                }
                                for (var trIdx = 0; trIdx < tableRows.length; trIdx++) {
                                    var tr = tableRows[trIdx];
                                    if (String(tr.getAttribute('data-row-id')) === tidStr) {
                                        var pList = getExtraRowIds(tr);
                                        for (var p = 0; p < pList.length; p++) {
                                            if (selectedRowIdsMap.hasOwnProperty(pList[p])) {
                                                isRowSelected = true;
                                                break;
                                            }
                                        }
                                        break;
                                    }
                                }
                                if (isRowSelected) break;
                            }
                        }

                        var isRowBackedByActiveToken = false;
                        if (hasActiveTokens && targetRowIds && targetRowIds.length > 0) {
                            var clickedLemma = (clickedTokenData && clickedTokenData.lemma) ? String(clickedTokenData.lemma).trim().toLowerCase() : '';
                            if (!clickedLemma && typeof getWordLemma === 'function') {
                                var wl = getWordLemma(span);
                                if (wl) clickedLemma = String(wl).trim().toLowerCase();
                            }

                            var targetMergedRowIds = {};
                            for (var t = 0; t < targetRowIds.length; t++) {
                                targetMergedRowIds[String(targetRowIds[t])] = true;
                            }
                            for (var trIdx = 0; trIdx < tableRows.length; trIdx++) {
                                var tr = tableRows[trIdx];
                                var pList = getRowGroupIds(tr);
                                var intersects = false;
                                for (var p = 0; p < pList.length; p++) {
                                    if (targetMergedRowIds[pList[p]]) {
                                        intersects = true;
                                        break;
                                    }
                                }
                                if (intersects) {
                                    for (var p = 0; p < pList.length; p++) {
                                        targetMergedRowIds[pList[p]] = true;
                                    }
                                }
                            }

                            for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                                var activeItem = window.AppState.activeTokenSelections[a];
                                if (clickedLemma && activeItem.lemma && String(activeItem.lemma).trim().toLowerCase() === clickedLemma) {
                                    isRowBackedByActiveToken = true;
                                    break;
                                }
                                var itemRowIds = activeItem.row_ids;
                                if (!itemRowIds) {
                                    var atd = (typeof findTokenDataByVisualIdx === 'function') ? findTokenDataByVisualIdx(Number(activeItem.visual_idx)) : null;
                                    if (atd) {
                                        itemRowIds = (atd.atomic_row_ids !== undefined) ? atd.atomic_row_ids : (atd.row_ids || []);
                                    } else if (activeItem.atomic_id !== null && activeItem.atomic_id !== undefined) {
                                        itemRowIds = [activeItem.atomic_id];
                                    }
                                }
                                if (itemRowIds && itemRowIds.length > 0) {
                                    for (var ir = 0; ir < itemRowIds.length; ir++) {
                                        if (targetMergedRowIds[String(itemRowIds[ir])]) {
                                            isRowBackedByActiveToken = true;
                                            break;
                                        }
                                    }
                                }
                                if (isRowBackedByActiveToken) break;
                            }
                        }

                        var isLegacyOverview = isLegacyOverviewContext();
                        var isLemmaSelected = false;
                        if (!hasActiveTokens && isLegacyOverview) {
                            var tokLem = (typeof getWordLemma === 'function' ? getWordLemma(span) : null) || (clickedTokenData && clickedTokenData.lemma);
                            if (tokLem && window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas[String(tokLem).trim().toLowerCase()]) {
                                isLemmaSelected = true;
                            }
                        }

                        var isOrphanSelected = false;
                        var wIdx = clickedTokenData ? clickedTokenData.visual_idx : null;
                        if (wIdx !== undefined && wIdx !== null && selectedOrphanWordIdxsMap.hasOwnProperty(String(wIdx))) {
                            isOrphanSelected = true;
                        }

                        var clickDecision = decideTokenClickAction({
                            hasActiveTokens: hasActiveTokens,
                            isVisuallyActive: isVisuallyActive,
                            isRowSelected: isRowSelected,
                            isRowBackedByActiveToken: isRowBackedByActiveToken,
                            isLemmaSelected: isLemmaSelected,
                            isInActiveSelections: isInActiveSelections,
                            isTokenPinned: isTokenPinned,
                            isOrphanSelected: isOrphanSelected
                        });
                        var isTableSelectedToken = clickDecision.isTableSelectedToken;
                        var shouldDeselect = clickDecision.shouldDeselect;

                        if (shouldDeselect) {
                            tokenDragMode = false;
                            if (typeof unpinTokenFromMVP === 'function') {
                                unpinTokenFromMVP(span);
                            }
                        } else {
                            tokenDragMode = true;
                            if (typeof pinTokenToMVP === 'function') {
                                pinTokenToMVP(span);
                            }
                        }

                        if (targetRowIds.length === 0) {
                            if (wIdx !== undefined && wIdx !== null) {
                                if (shouldDeselect) {
                                    delete selectedOrphanWordIdxsMap[String(wIdx)];
                                } else {
                                    selectedOrphanWordIdxsMap[String(wIdx)] = true;
                                }
                            }
                        }

                        initialSelectedMap = {};
                        for (var key in selectedRowIdsMap) {
                            if (selectedRowIdsMap.hasOwnProperty(key)) {
                                initialSelectedMap[key] = selectedRowIdsMap[key];
                            }
                        }

                        var relatedSpans = findRelatedTokenSpans(span);
                        var vIdxsToRemove = {};
                        for (var i = 0; i < relatedSpans.length; i++) {
                            var rSpan = relatedSpans[i];
                            var rTd = findTokenData(rSpan);
                            var rvIdx = rTd ? rTd.visual_idx : parseInt(String(rSpan.getAttribute('data-word-idx')), 10);
                            if (!isNaN(Number(rvIdx))) vIdxsToRemove[String(rvIdx)] = true;
                        }

                        var currentTableRows = (tableRows && tableRows.length > 0)
                            ? tableRows
                            :  (document.querySelectorAll('#lemma-table tbody tr[data-row-id], #lemma-table tr[data-row-id]'));
                        for (var j = 0; j < targetRowIds.length; j++) {
                            var tid = String(targetRowIds[j]);
                            if (tokenDragMode) {
                                selectedRowIdsMap[tid] = true;
                                for (var trIdx = 0; trIdx < currentTableRows.length; trIdx++) {
                                    var tr = currentTableRows[trIdx];
                                    var pList = getRowGroupIds(tr);
                                    if (pList.indexOf(tid) !== -1) {
                                        selectedRowIdsMap[pList[0]] = true;
                                    }
                                }
                                var rLem = getLemmaByRowId(tid);
                                if (rLem) setLemmaSelected(rLem, true);
                            } else {
                                if (!isRowStillNeededByOtherActiveTokens(tid, vIdxsToRemove)) {
                                    delete selectedRowIdsMap[tid];
                                    for (var trIdx = 0; trIdx < currentTableRows.length; trIdx++) {
                                        var tr = currentTableRows[trIdx];
                                        var pList = getRowGroupIds(tr);
                                        if (pList.indexOf(tid) !== -1) {
                                            for (var p = 0; p < pList.length; p++) {
                                                delete selectedRowIdsMap[pList[p]];
                                            }
                                        }
                                    }
                                }
                                var rLem = getLemmaByRowId(tid);
                                if (rLem && !isLemmaStillNeededByOtherActiveTokens(rLem, vIdxsToRemove)) {
                                    setLemmaSelected(rLem, false);
                                }
                            }
                        }
                        if (tokenDragMode) {
                            addTokenToActiveSelections(span);
                        } else {
                            removeTokenFromActiveSelections(span);
                            lastClickedRowId = null;
                        }
                        syncSelectionsToCardsAndState();
                        updateRowStyles();
                        updateBidirectionalHighlights();
                        if (typeof refreshBookmarkClasses === 'function') {
                            refreshBookmarkClasses();
                        }

                        if (e.preventDefault) {
                            e.preventDefault();
                        } else {
                            e.returnValue = false;
                        }
                    } else if (e.button === 2) { // RMB
                        isRmbDragFlipping = true;
                        dragOccurred = false;
                        mousedownTargetSpan = span;

                        tokenDragStartIdx = -1;
                        tokenDragLastIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                tokenDragStartIdx = k;
                                tokenDragLastIdx = k;
                                break;
                            }
                        }

                        rmbFlipMode = !span.classList.contains('flipped');
                        flipWord(span, rmbFlipMode);

                        initialFlippedMap = [];
                        for (var k = 0; k < tokenSpans.length; k++) {
                            initialFlippedMap.push(tokenSpans[k].classList.contains('flipped'));
                        }

                        if (e.preventDefault) {
                            e.preventDefault();
                        } else {
                            e.returnValue = false;
                        }
                    }
                });

                addEvent(span, 'mouseover', function(e) {
                    if (window.__selectableTextMode) return;
                    if (window.__deskInstanceId && myDeskInstanceId !== window.__deskInstanceId) return;
                    e = (e || window.event);
                    if (isTokenDragSelecting) {
                        if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                            isTokenDragSelecting = false;
                            notifyAHKSelection();
                            return;
                        }
                        if (!dragOccurred) {
                            dragOccurred = true;
                            if (mousedownTargetSpan && tokenDragMode && typeof unpinTokenFromMVP === 'function') {
                                unpinTokenFromMVP(mousedownTargetSpan);
                            }
                        }

                        var currIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                currIdx = k;
                                break;
                            }
                        }
                        if (currIdx === -1 || tokenDragStartIdx === -1) return;

                        selectedRowIdsMap = {};
                        for (var key in initialSelectedMap) {
                            if (initialSelectedMap.hasOwnProperty(key)) {
                                selectedRowIdsMap[key] = initialSelectedMap[key];
                            }
                        }

                        var minIdx = Math.min(tokenDragStartIdx, currIdx);
                        var maxIdx = Math.max(tokenDragStartIdx, currIdx);

                        tokenDragLastIdx = currIdx;
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            var td = findTokenData(s);
                            if (td) {
                                var atomics = (td.atomic_row_ids !== undefined)
                                    ? td.atomic_row_ids
                                    : (td.row_ids || []);
                                for (var j = 0; j < atomics.length; j++) {
                                    if (tokenDragMode) {
                                        selectedRowIdsMap[String(atomics[j])] = true;
                                    } else {
                                        delete selectedRowIdsMap[String(atomics[j])];
                                    }
                                    var rLem = getLemmaByRowId(atomics[j]);
                                    if (rLem) setLemmaSelected(rLem, tokenDragMode);
                                }
                            }
                            if (tokenDragMode) {
                                addTokenToActiveSelections(s);
                            } else {
                                removeTokenFromActiveSelections(s);
                            }
                        }

                        var evaluatedGroups = [];
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            if (evaluatedGroups.indexOf(s) !== -1) continue;
                            var grp = findCompoundSiblingSpans(s);
                            for (var g = 0; g < grp.length; g++) {
                                evaluatedGroups.push(grp[g]);
                            }

                            var allInDragRange = true;
                            for (var g = 0; g < grp.length; g++) {
                                var grpSpan = grp[g];
                                var grpIdx = -1;
                                for (var idx = 0; idx < tokenSpans.length; idx++) {
                                    if (tokenSpans[idx] === grpSpan) {
                                        grpIdx = idx;
                                        break;
                                    }
                                }
                                if (grpIdx < minIdx || grpIdx > maxIdx) {
                                    allInDragRange = false;
                                    break;
                                }
                            }

                            if (allInDragRange) {
                                for (var g = 0; g < grp.length; g++) {
                                    var gTd = findTokenData(grp[g]);
                                    if (gTd && gTd.compound_row_ids) {
                                        for (var j = 0; j < gTd.compound_row_ids.length; j++) {
                                            var cRowId = String(gTd.compound_row_ids[j]);
                                            if (tokenDragMode) {
                                                selectedRowIdsMap[cRowId] = true;
                                            } else {
                                                delete selectedRowIdsMap[cRowId];
                                            }
                                            var crLem = getLemmaByRowId(cRowId);
                                            if (crLem) setLemmaSelected(crLem, tokenDragMode);
                                        }
                                    }
                                }
                            }
                        }
                        syncSelectionsToCardsAndState();
                        updateRowStyles();
                        updateBidirectionalHighlights();
                    } else if (isRmbDragFlipping) {
                        if (e.isTrusted && e.buttons !== undefined && (e.buttons & 2) === 0) {
                            isRmbDragFlipping = false;
                            return;
                        }
                        var currIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                currIdx = k;
                                break;
                            }
                        }
                        if (currIdx === -1 || tokenDragStartIdx === -1) return;
                        if (currIdx === tokenDragStartIdx && !dragOccurred) return;
                        dragOccurred = true;
                        tokenDragLastIdx = currIdx;

                        var minIdx = Math.min(tokenDragStartIdx, currIdx);
                        var maxIdx = Math.max(tokenDragStartIdx, currIdx);

                        for (var k = 0; k < tokenSpans.length; k++) {
                            var s = tokenSpans[k];
                            var inRange = (k >= minIdx && k <= maxIdx);
                            var shouldFlip = inRange ? rmbFlipMode : (initialFlippedMap && initialFlippedMap[k]);
                            flipWord(s, !!shouldFlip, false);
                        }
                    }
                });
        }

        function rebindTokenSpans() {
            var sc = document.getElementById('source-container');
            var spans = sc ? sc.getElementsByTagName('span') : [];
            tokenSpans = [];
            for (var i = 0; i < spans.length; i++) {
                var sp = spans[i];
                if (sp.classList.contains('word')) {
                    tokenSpans.push(sp);
                    wireTokenSpan(sp);
                }
            }
        }
        window.rebindTokenSpans = rebindTokenSpans;
        rebindTokenSpans();

        var sourceContainer = document.getElementById('source-container');
        if (sourceContainer) {
            addEvent(sourceContainer, 'contextmenu', /** @param {MouseEvent} e */ function(e) {
                if (window.__selectableTextMode) return;
                e = (e || window.event);
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                return false;
            });
        }

        var lemmaTable = document.getElementById('lemma-table');
     var tableRows = [];
    function detectHasHighlightCol() {
        if (window.__CONFIG__ && window.__CONFIG__.has_highlight_col !== undefined) {
            return !!(window.__CONFIG__.has_highlight_col === true || window.__CONFIG__.has_highlight_col === 'true');
        }
        var w = window;
        if (w.AppState && w.AppState.bootstrap) {
            var b = w.AppState.bootstrap;
            if (b.ui_config && b.ui_config.has_highlight_col !== undefined) {
                return !!b.ui_config.has_highlight_col;
            }
            if (b.headers && Array.isArray(b.headers)) {
                for (var i = 0; i < b.headers.length; i++) {
                    var h = String(b.headers[i]).toLowerCase();
                    if (h === 'deskselected' || h === 'selected' || h === 'highlight') return true;
                }
            }
        }
        var bEl = document.getElementById('kardenwort-bootstrap');
        if (bEl && bEl.textContent) {
            try {
                var bData = JSON.parse(bEl.textContent);
                if (bData.ui_config && bData.ui_config.has_highlight_col !== undefined) {
                    return !!bData.ui_config.has_highlight_col;
                }
                if (bData.headers && Array.isArray(bData.headers)) {
                    for (var k = 0; k < bData.headers.length; k++) {
                        var hk = String(bData.headers[k]).toLowerCase();
                        if (hk === 'deskselected' || hk === 'selected' || hk === 'highlight') return true;
                    }
                }
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        }
        var sessEl = document.getElementById('session-data');
        if (sessEl && sessEl.textContent) {
            try {
                var sData = JSON.parse(sessEl.textContent);
                if (sData.headers && Array.isArray(sData.headers)) {
                    for (var j = 0; j < sData.headers.length; j++) {
                        var hj = String(sData.headers[j]).toLowerCase();
                        if (hj === 'deskselected' || hj === 'selected' || hj === 'highlight') return true;
                    }
                }
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        }
        return false;
    }
    var hasHighlightCol = detectHasHighlightCol();

    /** @param {HTMLElement} row */
    function wireTableRowEvents(row) {
        addEvent(row, 'mousedown', function(e) {
            if (window.__selectableTextMode) return;
            e = (e || window.event);
            var target = (e.target || e.srcElement);
            if (target && target.tagName === 'INPUT') {
                return;
            }
            if (e.button !== 0 && e.button !== 2) {
                return;
            }
            if (e.button === 0) {
                if (window.AppState) window.AppState.activeTokenSelections = [];
                if (typeof saveActiveTokensToStorage === 'function') {
                    saveActiveTokensToStorage();
                }
            }
            var rowIdStr = String(row.getAttribute('data-row-id'));
            var rowId = parseInt(String(rowIdStr), 10);
            var constituentIds = getRowGroupIds(row);

            var rowVisualIdx = -1;
            for (var rIdx = 0; rIdx < tableRows.length; rIdx++) {
                if (tableRows[rIdx] === row) {
                    rowVisualIdx = rIdx;
                    break;
                }
            }

            var lt = document.getElementById('lemma-table');
            var isFilterActive = lt && lt.classList.contains('kw-filter-selected-only');

            if (e.button === 0) { // LMB
                if (lt) {
                    lt.classList.add('kw-table-dragging');
                }
                isDragSelecting = true;
                dragOccurred = false;
                dragStartX = (e && e.clientX !== undefined) ? e.clientX : 0;
                dragStartY = (e && e.clientY !== undefined) ? e.clientY : 0;
                mousedownTargetRow = row;
                isShiftClick = !!(e.shiftKey && (lastClickedVisualIdx !== -1 || lastClickedRowId !== null));
                isCtrlKey = !!(e.ctrlKey || e.metaKey);

                if (isShiftClick) {
                    var sVisualIdx = (lastClickedVisualIdx !== -1) ? lastClickedVisualIdx : rowVisualIdx;
                    dragStartVisualIdx = sVisualIdx;
                    dragLastVisualIdx = rowVisualIdx;
                    dragStartRowId = lastClickedRowId;
                    dragLastRowId = rowId;
                    dragSelectMode = true;

                    initialSelectedMap = {};
                    for (var key in selectedRowIdsMap) {
                        if (selectedRowIdsMap.hasOwnProperty(key)) {
                            initialSelectedMap[key] = selectedRowIdsMap[key];
                        }
                    }

                    var start = Math.min(sVisualIdx, rowVisualIdx);
                    var end = Math.max(sVisualIdx, rowVisualIdx);
                    for (var rIdx = start; rIdx <= end; rIdx++) {
                        if (rIdx >= 0 && rIdx < tableRows.length) {
                            var tr = tableRows[rIdx];
                            var pIds = getRowGroupIds(tr);
                            for (var p = 0; p < pIds.length; p++) {
                                selectedRowIdsMap[pIds[p]] = true;
                            }
                            var trLem = getRowLemma(tr);
                            if (trLem) setLemmaSelected(trLem, true);
                        }
                    }
                    syncSelectionsToCardsAndState();
                } else if (isCtrlKey) {
                    dragStartVisualIdx = rowVisualIdx;
                    dragLastVisualIdx = rowVisualIdx;
                    dragStartRowId = rowId;
                    dragLastRowId = rowId;
                    var isCurrentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr) ||
                        constituentIds.some(function(cid) { return selectedRowIdsMap.hasOwnProperty(cid); });
                    dragSelectMode = !isCurrentlySelected;

                    initialSelectedMap = {};
                    for (var key in selectedRowIdsMap) {
                        if (selectedRowIdsMap.hasOwnProperty(key)) {
                            initialSelectedMap[key] = selectedRowIdsMap[key];
                        }
                    }

                    for (var cIdx = 0; cIdx < constituentIds.length; cIdx++) {
                        var cId = constituentIds[cIdx];
                        if (dragSelectMode) {
                            selectedRowIdsMap[cId] = true;
                        } else {
                            delete selectedRowIdsMap[cId];
                        }
                    }
                    var rowLem = getRowLemma(row);
                    if (rowLem) setLemmaSelected(rowLem, dragSelectMode);
                    if (!dragSelectMode) {
                        pruneActiveTokensByRowOrLemma(rowIdStr, rowLem);
                        for (var cIdx = 0; cIdx < constituentIds.length; cIdx++) {
                            pruneActiveTokensByRowOrLemma(constituentIds[cIdx], rowLem);
                        }
                    }
                    syncSelectionsToCardsAndState();
                } else {
                    dragStartVisualIdx = rowVisualIdx;
                    dragLastVisualIdx = rowVisualIdx;
                    dragStartRowId = rowId;
                    dragLastRowId = rowId;
                    var isCurrentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr) ||
                        constituentIds.some(function(cid) { return selectedRowIdsMap.hasOwnProperty(cid); });
                    dragSelectMode = !isCurrentlySelected;

                    initialSelectedMap = {};
                    for (var key in selectedRowIdsMap) {
                        if (selectedRowIdsMap.hasOwnProperty(key)) {
                            initialSelectedMap[key] = selectedRowIdsMap[key];
                        }
                    }

                    for (var cIdx = 0; cIdx < constituentIds.length; cIdx++) {
                        var cId = constituentIds[cIdx];
                        if (dragSelectMode) {
                            selectedRowIdsMap[cId] = true;
                        } else {
                            delete selectedRowIdsMap[cId];
                        }
                    }
                    var rowLem = getRowLemma(row);
                    if (rowLem) setLemmaSelected(rowLem, dragSelectMode);
                    if (!dragSelectMode) {
                        pruneActiveTokensByRowOrLemma(rowIdStr, rowLem);
                        for (var cIdx = 0; cIdx < constituentIds.length; cIdx++) {
                            pruneActiveTokensByRowOrLemma(constituentIds[cIdx], rowLem);
                        }
                    }
                    syncSelectionsToCardsAndState();
                }

                lastClickedVisualIdx = rowVisualIdx;
                lastClickedRowId = dragSelectMode ? rowId : null;
                focusedRowId = dragSelectMode ? rowId : null;
                updateRowStyles();
                updateBidirectionalHighlights();
                notifyAHKSelection();
            } else if (e.button === 2) { // RMB
                if (audioRmbPlay) {
                    var sourceLang = getSessionLang();
                    var textToPlay = getTableRowWordsToPlay(row, audioLmbSource);
                    if (textToPlay) {
                        playAudio(textToPlay, sourceLang);
                    }
                }
            }

            e = (e || window.event);
            if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
            return false;
        });

        addEvent(row, 'mouseover', function(e) {
            if (window.__selectableTextMode) return;
            e = (e || window.event);
            var lt = document.getElementById('lemma-table');
            var isFilterActive = lt && lt.classList.contains('kw-filter-selected-only');

            if (isDragSelecting) {
                if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                    if (lt) {
                        lt.classList.remove('kw-table-dragging');
                    }
                    isDragSelecting = false;
                    dragStartVisualIdx = -1;
                    dragLastVisualIdx = -1;
                    updateRowStyles();
                    notifyAHKSelection();
                    return;
                }
                if (!dragOccurred) {
                    var curX = (e && e.clientX !== undefined) ? e.clientX : 0;
                    var curY = (e && e.clientY !== undefined) ? e.clientY : 0;
                    var hasCoords = (dragStartX !== 0 || dragStartY !== 0 || curX !== 0 || curY !== 0);
                    if (hasCoords) {
                        var dx = curX - dragStartX;
                        var dy = curY - dragStartY;
                        if (Math.sqrt(dx * dx + dy * dy) < 5) {
                            return;
                        }
                    } else if (row === mousedownTargetRow) {
                        return;
                    }
                    dragOccurred = true;
                }
                var rowId = parseInt(String(row.getAttribute('data-row-id')), 10);
                dragLastRowId = rowId;

                var currentVisualIdx = -1;
                for (var rIdx = 0; rIdx < tableRows.length; rIdx++) {
                    if (tableRows[rIdx] === row) {
                        currentVisualIdx = rIdx;
                        break;
                    }
                }
                if (currentVisualIdx !== -1) {
                    dragLastVisualIdx = currentVisualIdx;
                }

                // Reset to the state before the current drag gesture started
                selectedRowIdsMap = {};
                for (var key in initialSelectedMap) {
                    if (initialSelectedMap.hasOwnProperty(key)) {
                        selectedRowIdsMap[key] = initialSelectedMap[key];
                    }
                }

                // Apply the visual drag selection range from dragStartVisualIdx to currentVisualIdx
                var sIdx = (dragStartVisualIdx !== -1) ? dragStartVisualIdx : currentVisualIdx;
                var eIdx = (currentVisualIdx !== -1) ? currentVisualIdx : sIdx;
                var start = Math.min(sIdx, eIdx);
                var end = Math.max(sIdx, eIdx);

                for (var rIdx = start; rIdx <= end; rIdx++) {
                    if (rIdx >= 0 && rIdx < tableRows.length) {
                        var tr = tableRows[rIdx];
                        if (isFilterActive && tr.getAttribute('data-filter-hidden') === '1') {
                            continue;
                        }
                        var trId = String(tr.getAttribute('data-row-id'));
                        var isContainer = !!(window.WorkspaceTabs && window.WorkspaceTabs.getCards);
                        var pIds = isContainer ? [trId] : getRowGroupIds(tr);
                        for (var p = 0; p < pIds.length; p++) {
                            if (dragSelectMode) {
                                selectedRowIdsMap[pIds[p]] = true;
                            } else {
                                delete selectedRowIdsMap[pIds[p]];
                            }
                        }
                        var trLem = getRowLemma(tr);
                        if (trLem) setLemmaSelected(trLem, dragSelectMode);
                    }
                }
                syncSelectionsToCardsAndState();
                focusedRowId = rowId;
                updateRowStyles();
                updateBidirectionalHighlights();
            }
        });

        var tds = row.getElementsByTagName('td');
        for (var j = 0; j < tds.length; j++) {
            if (tds[j].classList.contains('editable')) {
                (function(cell) {
                    addEvent(cell, 'click', function(e) {
                        if (window.__selectableTextMode) return;
                        lastClickedCell = cell;
                    });
                    addEvent(cell, 'mouseover', function(e) {
                        if (window.__selectableTextMode) return;
                        lastHoveredCell = cell;
                    });
                    addEvent(cell, 'mouseout', function(e) {
                        if (window.__selectableTextMode) return;
                        if (lastHoveredCell === cell) {
                            lastHoveredCell = null;
                        }
                    });
                    addEvent(cell, 'dblclick', function() {
                        if (window.__selectableTextMode) return;
                        makeEditable(cell);
                    });
                })(tds[j]);
            }
        }
    }

    function acceptRenderedSelection(rows, selectedIds) {
        if (!selectedIds) return;
        var ids = selectedIds.map(String);
        rows.forEach(function(row) {
            window.setLemmaSelected(getRowLemma(row), ids.indexOf(row.getAttribute('data-row-id')) !== -1);
        });
    }

    function rebindTableRows(selectedIds) {
        var hasCardsElement = !!document.getElementById('sentence-cards');
        var isContainerMode = !!(document.getElementById('delivery-mode') && (requireElement('delivery-mode').textContent || '').trim() === 'container');
        var cardsList = (hasCardsElement || isContainerMode) && (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function')
            ? window.WorkspaceTabs.getCards()
            : null;
        if (!cardsList && (hasCardsElement || isContainerMode)) {
            var cardsScript = document.getElementById('sentence-cards');
            if (cardsScript && cardsScript.textContent) {
                try { cardsList = JSON.parse(cardsScript.textContent); } catch (e) { console.error('[Bootstrap] Failed to parse #sentence-cards JSON:', e); throw e; }
            }
        }
        selectedRowIdsMap = {};
        var sharedState = window.AppStore.getState();
        sharedState.selectedLemmas = sharedState.selectedLemmas || {};
        if (typeof restoreActiveTokensFromStorage === 'function') restoreActiveTokensFromStorage();
        var lt = document.getElementById('lemma-table');
        tableRows = [];
        if (lt) {
            var tbodies = lt.getElementsByTagName('tbody');
            var rowsContainer = tbodies.length > 0 ? tbodies[0] : lt;
            var allRows = rowsContainer.getElementsByTagName('tr');
            for (var i = 0; i < allRows.length; i++) {
                if (allRows[i].getAttribute('data-row-id') !== null) {
                    tableRows.push(allRows[i]);
                }
            }
        }
        acceptRenderedSelection(tableRows, selectedIds);
        for (var i = 0; i < tableRows.length; i++) {
            var r = tableRows[i];
            var rIdStr = String(r.getAttribute('data-row-id'));
            var isHl = (r.getAttribute('data-selected') === '1' || r.classList.contains('selected') || r.classList.contains('kw-row-selected'));
            if (!initialHighlights.hasOwnProperty(rIdStr)) {
                initialHighlights[rIdStr] = isHl;
            }
            if (isHl) {
                var pIds = getRowGroupIds(r);
                for (var p = 0; p < pIds.length; p++) {
                    selectedRowIdsMap[pIds[p]] = true;
                }
                var rLem = getRowLemma(r);
                if (rLem) setLemmaSelected(rLem, true);
            } else {
                var rLem = getRowLemma(r);
                if (rLem && window.AppState && window.AppState.selectedLemmas && window.AppState.selectedLemmas[rLem]) {
                    selectedRowIdsMap[rIdStr] = true;
                }
            }
            if (!r.__kw_wired) {
                r.__kw_wired = true;
                wireTableRowEvents(r);
            }
        }
        syncSelectionsToCardsAndState();
        updateRowStyles();
        updateBidirectionalHighlights();
        if (typeof refreshBookmarkClasses === 'function') {
            refreshBookmarkClasses();
        }
    }
    window.rebindTableRows = rebindTableRows;
    rebindTableRows();

    /** @param {MouseEvent} e */
    function handleMouseUp(e) {
            if (window.__deskInstanceId && myDeskInstanceId !== window.__deskInstanceId) return;
            e = (e || window.event);
            var lt = document.getElementById('lemma-table');
            if (lt) {
                lt.classList.remove('kw-table-dragging');
            }
            var isMouseUpCtrl = !!(e && (e.ctrlKey || e.metaKey));
            var activeCtrl = isCtrlKey || isMouseUpCtrl;
            var needNotify = false;
            if (isDragSelecting || isTokenDragSelecting || isRmbDragFlipping) {
                if (dragOccurred) {
                    justFinishedDrag = true;
                    setTimeout(function() {
                        justFinishedDrag = false;
                    }, 50);
                }

                if (isDragSelecting && audioLmbPlay) {
                    if (dragSelectMode) {
                        var sourceLang = getSessionLang();
                        if (!dragOccurred && !isShiftClick) {
                            var targetRow = mousedownTargetRow;
                            if (targetRow) {
                                var rowIdStr = String(targetRow.getAttribute('data-row-id'));
                                if (selectedRowIdsMap[rowIdStr]) {
                                    var singleText = getTableRowWordsToPlay(targetRow, audioLmbSource);
                                    if (singleText) {
                                        playAudio(singleText, sourceLang);
                                    }
                                }
                            }
                        } else {
                            if (audioTableRangeMode === 'all' || activeCtrl) {
                                var sIdx = (dragStartVisualIdx !== -1 && dragStartVisualIdx !== null) ? dragStartVisualIdx : 0;
                                var eIdx = (dragLastVisualIdx !== -1 && dragLastVisualIdx !== null) ? dragLastVisualIdx : sIdx;
                                var minRow = Math.min(sIdx, eIdx);
                                var maxRow = Math.max(sIdx, eIdx);
                                var rowWords = [];
                                for (var r = minRow; r <= maxRow; r++) {
                                    if (r >= 0 && r < tableRows.length) {
                                        var tr = tableRows[r];
                                        var pIds = getRowGroupIds(tr);
                                        var isSel = pIds.some(function(cid) { return selectedRowIdsMap.hasOwnProperty(cid); });
                                        if (isSel) {
                                            var term = getTableRowWordsToPlay(tr, audioLmbSource);
                                            if (term) {
                                                rowWords.push(term);
                                            }
                                        }
                                    }
                                }
                                if (rowWords.length > 0) {
                                    if (audioLmbChainMode === 'separate' || audioLmbChainMode === 'per_word') {
                                        playAudio(rowWords.join(' ||| '), sourceLang);
                                    } else {
                                        var joinedText = rowWords.join(' ');
                                        if (joinedText) {
                                            playAudio(joinedText, sourceLang);
                                        }
                                    }
                                }
                            }
                        }
                    }
                } else if (isTokenDragSelecting && tokenDragMode && audioLmbPlay && tokenDragStartIdx !== -1) {
                    var minIdx = (tokenDragLastIdx !== -1) ? Math.min(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    var maxIdx = (tokenDragLastIdx !== -1) ? Math.max(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    if (dragOccurred && minIdx !== maxIdx) {
                        var shouldPlay = (audioSourceRangeMode === 'all' || activeCtrl);
                        if (shouldPlay) {
                            var dragWords = [];
                            for (var k = minIdx; k <= maxIdx; k++) {
                                var term = getSingleTokenWordsToPlay(tokenSpans[k], audioLmbSource, audioLmbChainMode);
                                if (term) {
                                    dragWords.push(term);
                                }
                            }
                            if (dragWords.length > 0) {
                                var sourceLang = getSessionLang();
                                if (audioLmbChainMode === 'separate' || audioLmbChainMode === 'per_word') {
                                    playAudio(dragWords.join(' ||| '), sourceLang);
                                } else {
                                    var dragText = dragWords.join(' ');
                                    if (dragText) {
                                        playAudio(dragText, sourceLang);
                                    }
                                }
                            }
                        }
                    } else {
                        var targetSpan = mousedownTargetSpan || (tokenDragStartIdx !== -1 ? tokenSpans[tokenDragStartIdx] : null);
                        if (targetSpan) {
                            var singleText = getSingleTokenWordsToPlay(targetSpan, audioLmbSource, audioLmbChainMode);
                            if (singleText) {
                                var sourceLang = getSessionLang();
                                playAudio(singleText, sourceLang);
                            }
                        }
                    }
                } else if (isRmbDragFlipping && rmbFlipMode && audioRmbPlay && tokenDragStartIdx !== -1) {
                    var minIdx = (tokenDragLastIdx !== -1) ? Math.min(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    var maxIdx = (tokenDragLastIdx !== -1) ? Math.max(tokenDragStartIdx, tokenDragLastIdx) : tokenDragStartIdx;
                    var targetLang = getSessionTargetLang();

                    if (dragOccurred && minIdx !== maxIdx) {
                        var dragTranslations = [];
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            var grp = findCompoundSiblingSpans(s);
                            var rawTrans = (grp && grp.length > 1)
                                ? [getSpanSpecificTranslation(s, grp)]
                                : getRawRowTranslations(s);
                            if (!rawTrans || rawTrans.length === 0) {
                                var wt = getWordTranslation(s);
                                if (wt) rawTrans = [wt];
                            }
                            var delim = (audioRmbChainMode === 'separate' || audioRmbChainMode === 'per_word') ? ' ||| ' : ' ';
                            var cleanParts = [];
                            for (var t = 0; t < rawTrans.length; t++) {
                                var c = sanitizeSpokenText(rawTrans[t]);
                                if (c) cleanParts.push(c);
                            }
                            if (cleanParts.length > 0) {
                                dragTranslations.push(cleanParts.join(delim));
                            }
                        }
                        if (dragTranslations.length > 0) {
                            if (audioRmbChainMode === 'separate' || audioRmbChainMode === 'per_word') {
                                playAudio(dragTranslations.join(' ||| '), targetLang);
                            } else {
                                var dragText = dragTranslations.join(' ');
                                if (dragText) {
                                    playAudio(dragText, targetLang);
                                }
                            }
                        }
                    } else {
                        var targetSpan = mousedownTargetSpan || (tokenDragStartIdx !== -1 ? tokenSpans[tokenDragStartIdx] : null);
                        if (targetSpan) {
                            var grp = findCompoundSiblingSpans(targetSpan);
                            var rawTrans = (grp && grp.length > 1)
                                ? [getSpanSpecificTranslation(targetSpan, grp)]
                                : getRawRowTranslations(targetSpan);
                            if (!rawTrans || rawTrans.length === 0) {
                                var wt = getWordTranslation(targetSpan);
                                if (wt) rawTrans = [wt];
                            }
                            var delim = (audioRmbChainMode === 'separate' || audioRmbChainMode === 'per_word') ? ' ||| ' : ' ';
                            var cleanParts = [];
                            for (var t = 0; t < rawTrans.length; t++) {
                                var c = sanitizeSpokenText(rawTrans[t]);
                                if (c) cleanParts.push(c);
                            }
                            if (cleanParts.length > 0) {
                                playAudio(cleanParts.join(delim), targetLang);
                            }
                        }
                    }
                }

                isDragSelecting = false;
                isTokenDragSelecting = false;
                isRmbDragFlipping = false;
                updateRowStyles();
                updateBidirectionalHighlights();
                needNotify = true;
            }
            mousedownTargetSpan = null;
            mousedownTargetRow = null;
            dragStartX = 0;
            dragStartY = 0;
            dragStartRowId = null;
            dragLastRowId = null;
            dragStartVisualIdx = -1;
            dragLastVisualIdx = -1;
            isShiftClick = false;
            isCtrlKey = false;
            tokenDragStartIdx = -1;
            tokenDragLastIdx = -1;
            if (needNotify) {
                notifyAHKSelection();
            }
        }
        if (window.__deskHandleMouseUp) {
            if (document.removeEventListener) {
                document.removeEventListener('mouseup', window.__deskHandleMouseUp);
                window.removeEventListener('mouseup', window.__deskHandleMouseUp);
            }
        }
        window.__deskHandleMouseUp = handleMouseUp;
        addEvent(document, 'mouseup', handleMouseUp);
        addEvent(window, 'mouseup', handleMouseUp);

        addEvent(document, 'contextmenu', /** @param {MouseEvent} e */ function(e) {
            if (window.__selectableTextMode) return;
            if (justFinishedDrag) {
                e = (e || window.event);
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                return false;
            }
        });

        addEvent(document, 'keydown', function(e) {
            e = (e || window.event);
            var keyCode = e.keyCode || e.which;
            var isSaveKey = (keyCode === 83 || (e.key && (e.key.toLowerCase() === 's' || e.key.toLowerCase() === 'ы')) || e.code === 'KeyS');
            if ((e.ctrlKey || e.metaKey) && isSaveKey) { // Ctrl+S
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                var saveBtn = (document.getElementById('kw-btn-save'));
                if (saveBtn && !saveBtn.disabled) {
                    saveBtn.click();
                } else if (typeof window.onSaveClick === 'function') {
                    window.onSaveClick();
                }
                return false;
            }

            if (keyCode === 116 || e.key === 'F5') { // F5 -> update
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                var updateBtn = (document.getElementById('kw-btn-update'));
                if (updateBtn && !updateBtn.disabled) {
                    updateBtn.click();
                } else if (typeof window.onUpdateClick === 'function') {
                    window.onUpdateClick();
                }
                return false;
            }

            var activeEl = document.activeElement;
            if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) return;

            if (e.ctrlKey && (keyCode === 65 || e.key === 'a' || e.key === 'A')) { // Ctrl+A or Ctrl+Shift+A
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (e.shiftKey) {
                    if (typeof window.clearAllSelectionsAndNotify === 'function') window.clearAllSelectionsAndNotify();
                    else if (typeof window.clearAllSelections === 'function') window.clearAllSelections();
                    return;
                }
                if (typeof window.selectAllRows === 'function') {
                    window.selectAllRows();
                    return;
                }
                if (typeof tableRows !== 'undefined' && tableRows.length > 0) {
                    for (var i = 0; i < tableRows.length; i++) {
                        var rowId = String(tableRows[i].getAttribute('data-row-id'));
                        selectedRowIdsMap[rowId] = true;
                        if (!window.WorkspaceTabs || !window.WorkspaceTabs.getCards) {
                            var extra = getExtraRowIds(tableRows[i]);
                            for (var p = 0; p < extra.length; p++) {
                                selectedRowIdsMap[extra[p]] = true;
                            }
                        }
                    }
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    if (typeof notifyAHKSelection !== 'undefined') {
                        notifyAHKSelection();
                    }
                }
                return;
            }
            if (keyCode === 27) { // Escape key
                try {
                    if (window.getSelection) {
                        var sel = window.getSelection();
                        if (sel && sel.removeAllRanges) {
                            sel.removeAllRanges();
                        }
                    } else if (document.selection && document.selection.empty) {
                        document.selection.empty();
                    }
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                if (window.clearMVPBookmarks) {
                    var bCleared = window.clearMVPBookmarks();
                    if (bCleared) {
                        return;
                    }
                }
                clearAllSelections();
                lastClickedVisualIdx = -1;
                lastClickedRowId = null;
                updateBidirectionalHighlights();
                notifyAHKSelection();
                return;
            }
            if (keyCode === 40 || keyCode === 38) { // ArrowDown or ArrowUp
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (tableRows.length === 0) return;

                var visibleRows = [];
                var lt = document.getElementById('lemma-table');
                var isFilterActive = lt && lt.classList.contains('kw-filter-selected-only');
                for (var vr = 0; vr < tableRows.length; vr++) {
                    var rEl = tableRows[vr];
                    var isHidden = false;
                    if (isFilterActive && rEl.getAttribute('data-selected') === '0') {
                        isHidden = true;
                    } else if (rEl.style.display === 'none') {
                        isHidden = true;
                    } else if (rEl.offsetParent === null) {
                        isHidden = true;
                    }
                    if (!isHidden) {
                        visibleRows.push(rEl);
                    }
                }
                if (visibleRows.length === 0) return;

                var currentVisIdx = -1;
                for (var v = 0; v < visibleRows.length; v++) {
                    var vRowId = parseInt(String(visibleRows[v].getAttribute('data-row-id')), 10);
                    if (vRowId === focusedRowId) {
                        currentVisIdx = v;
                        break;
                    }
                }

                var nextVisIdx = 0;
                if (currentVisIdx === -1) {
                    nextVisIdx = (keyCode === 38) ? (visibleRows.length - 1) : 0;
                } else {
                    if (keyCode === 40) {
                        nextVisIdx = Math.min(currentVisIdx + 1, visibleRows.length - 1);
                    } else {
                        nextVisIdx = Math.max(currentVisIdx - 1, 0);
                    }
                }

                var nextRow = visibleRows[nextVisIdx];
                if (nextRow) {
                    focusedRowId = parseInt(String(nextRow.getAttribute('data-row-id')), 10);
                    updateRowFocus();
                }
            } else if (keyCode === 46) { // Delete
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.deleteSelectedRows) {
                    window.deleteSelectedRows();
                }
            } else if (keyCode === 32) { // Space
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (focusedRowId !== null) {
                    var fRow = (typeof findTableRowById === 'function') ? findTableRowById(focusedRowId) : ((focusedRowId >= 0 && focusedRowId < tableRows.length) ? tableRows[focusedRowId] : null);
                    var fRowId = (fRow && fRow.getAttribute('data-row-id')) || String(focusedRowId);
                    var cIds = (!window.WorkspaceTabs || !window.WorkspaceTabs.getCards) ? (fRow ? getRowGroupIds(fRow) : [fRowId]) : [fRowId];
                    var isCurrentlySelected = selectedRowIdsMap.hasOwnProperty(fRowId);
                    for (var c = 0; c < cIds.length; c++) {
                        if (isCurrentlySelected) {
                            delete selectedRowIdsMap[cIds[c]];
                        } else {
                            selectedRowIdsMap[cIds[c]] = true;
                        }
                    }
                    var fLem = fRow ? getRowLemma(fRow) : '';
                    if (fLem) setLemmaSelected(fLem, !isCurrentlySelected);
                    if (isCurrentlySelected) {
                        pruneActiveTokensByRowOrLemma(fRowId, fLem);
                    }
                    syncSelectionsToCardsAndState();
                    lastClickedRowId = isCurrentlySelected ? null : parseInt(String(fRowId), 10);
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    notifyAHKSelection();
                }
            } else if (keyCode === 113) { // F2
                var cellToEdit = null;
                if (lastHoveredCell && lastHoveredCell.parentElement) {
                    var rId = parseInt(String(lastHoveredCell.parentElement.getAttribute('data-row-id')));
                    if (rId === focusedRowId) {
                        cellToEdit = lastHoveredCell;
                    }
                }
                if (!cellToEdit && lastClickedCell && lastClickedCell.parentElement) {
                    var rId = parseInt(String(lastClickedCell.parentElement.getAttribute('data-row-id')));
                    if (rId === focusedRowId) {
                        cellToEdit = lastClickedCell;
                    }
                }
                if (!cellToEdit && focusedRowId !== null) {
                    var activeRow = null;
                    for (var k = 0; k < tableRows.length; k++) {
                        if (tableRows[k].getAttribute('data-row-id') === String(focusedRowId)) {
                            activeRow = tableRows[k];
                            break;
                        }
                    }
                    if (activeRow) {
                        var tds = activeRow.getElementsByTagName('td');
                        for (var k = 0; k < tds.length; k++) {
                            if (tds[k].classList.contains('editable')) {
                                cellToEdit = tds[k];
                                break;
                            }
                        }
                    }
                }
                if (cellToEdit) {
                    makeEditable(cellToEdit);
                }
            }
        });

        addEvent(document, 'click', function(e) {
            if (justFinishedDrag) {
                justFinishedDrag = false;
                return;
            }
        });

        function clearAllSelections() {
            selectedRowIdsMap = {};
            selectedOrphanWordIdxsMap = {};
            if (window.AppState) {
                window.AppState.selectedLemmas = {};
                window.AppState.activeTokenSelections = [];
            }
            if (typeof saveActiveTokensToStorage === 'function') {
                saveActiveTokensToStorage();
            }
            if (window.clearMVPBookmarks) window.clearMVPBookmarks();
            syncSelectionsToCardsAndState();
            lastClickedRowId = null;
            updateRowStyles();
            updateBidirectionalHighlights();
        }
        window.clearAllSelections = clearAllSelections;

        window.clearAllSelectionsAndNotify = function() {
            clearAllSelections();
            notifyAHKSelection();
            if (window.forceRepaint) window.forceRepaint();
        };

        /** @param {number|string} rowId @param {boolean=} forceState */
        function toggleRowSelection(rowId, forceState) {
            var rIdStr = String(rowId);
            var lem = getLemmaByRowId(rowId);
            var willSelect;
            if (forceState !== undefined) {
                willSelect = Boolean(forceState);
            } else {
                willSelect = !selectedRowIdsMap.hasOwnProperty(rIdStr);
            }
            if (lem) {
                setLemmaSelected(lem, willSelect);
                syncSelectionsToCardsAndState();
            } else {
                if (willSelect) {
                    selectedRowIdsMap[rIdStr] = true;
                } else {
                    delete selectedRowIdsMap[rIdStr];
                }
            }
            updateRowStyles();
            updateBidirectionalHighlights();
        }
        window.toggleRowSelection = toggleRowSelection;
        window.getFocusedRowId = function() { return focusedRowId; };

        function updateEmptySelectionState() {
            var isAnyDragging = (typeof isDragSelecting !== 'undefined' && isDragSelecting) ||
                                (typeof isTokenDragSelecting !== 'undefined' && isTokenDragSelecting);
            var lt = document.getElementById('lemma-table');
            var isDragging = isAnyDragging || (lt && lt.classList.contains('kw-table-dragging'));
            var emptyRow = document.getElementById('kw-empty-selection-row');

            if (isDragging) {
                if (emptyRow && emptyRow.parentNode) {
                    emptyRow.parentNode.removeChild(emptyRow);
                }
                return;
            }

            if (!lt) return;
            var tbody = (lt.querySelector('tbody'));
            if (!tbody) return;

            var isFilterActive = !!(window.AppState && window.AppState.filterSelectedOnly);

            if (!isFilterActive) {
                if (emptyRow && emptyRow.parentNode) {
                    emptyRow.parentNode.removeChild(emptyRow);
                }
                return;
            }

            var selectedRows = (tbody.querySelectorAll('tr[data-selected="1"]'));
            if (selectedRows.length === 0) {
                if (!emptyRow) {
                    emptyRow = document.createElement('tr');
                    emptyRow.id = 'kw-empty-selection-row';
                    emptyRow.className = 'kw-empty-selection-row kw-empty-visible';
                    emptyRow.innerHTML = (
                        '<td colspan="100" class="kw-empty-selection-cell">' +
                        '<span>No items selected. Click words in the text above to select them, or ' +
                        '<button type="button" id="kw-btn-empty-clear-filter" class="kw-empty-clear-filter-btn">show all words</button>.</span>' +
                        '</td>'
                    );
                    tbody.appendChild(emptyRow);
                } else {
                    emptyRow.classList.add('kw-empty-visible');
                    emptyRow.style.display = '';
                    if (emptyRow.parentNode !== tbody) {
                        tbody.appendChild(emptyRow);
                    }
                }
            } else {
                if (emptyRow && emptyRow.parentNode) {
                    emptyRow.parentNode.removeChild(emptyRow);
                }
            }
        }
        window.updateEmptySelectionState = updateEmptySelectionState;

        function updateRowStyles() {
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
                var activeC = window.WorkspaceTabs.getActiveCard();
                if (activeC) {
                    activeC.selected_ids = [];
                    for (var sk in selectedRowIdsMap) {
                        if (selectedRowIdsMap.hasOwnProperty(sk)) {
                            activeC.selected_ids.push(sk);
                        }
                    }
                }
            }
            if (typeof tableRows === 'undefined' || !tableRows || tableRows.length === 0) {
                var lt = document.getElementById('lemma-table');
                if (lt) {
                    var tbodies = lt.getElementsByTagName('tbody');
                    var rowsContainer = tbodies.length > 0 ? tbodies[0] : lt;
                    var trs = rowsContainer.getElementsByTagName('tr');
                    var freshRows = [];
                    for (var r = 0; r < trs.length; r++) {
                        if (trs[r].getAttribute('data-row-id') !== null) {
                            freshRows.push(trs[r]);
                        }
                    }
                    tableRows = freshRows;
                } else {
                    return;
                }
            }
            for (var i = 0; i < tableRows.length; i++) {
                var row = tableRows[i];
                var rowIdStr = String(row.getAttribute('data-row-id'));
                var isSel = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                if (!isSel) {
                    var cIds = getExtraRowIds(row);
                    for (var c = 0; c < cIds.length; c++) {
                        if (selectedRowIdsMap.hasOwnProperty(cIds[c])) {
                            isSel = true;
                            break;
                        }
                    }
                }
                if (isSel) {
                    row.classList.add('selected');
                    row.classList.add('kw-row-selected');
                    row.setAttribute('data-selected', '1');
                } else {
                    row.classList.remove('selected');
                    row.classList.remove('kw-row-selected');
                    row.setAttribute('data-selected', '0');
                }
                var isAnyDragging = isDragSelecting || isTokenDragSelecting;
                if (!isAnyDragging) {
                    row.setAttribute('data-filter-hidden', String(isSel ? '0' : '1'));
                }
            }
            if (typeof updateToolbarState === 'function') {
                updateToolbarState();
            }
            if (typeof updateEmptySelectionState === 'function') {
                updateEmptySelectionState();
            }
            updateRowFocus();
        }

        function updateRowFocus() {
            for (var i = 0; i < tableRows.length; i++) {
                var row = tableRows[i];
                var rowId = parseInt(String(row.getAttribute('data-row-id')), 10);
                if (rowId === focusedRowId) {
                    row.style.outline = '1px solid #58a6ff';
                    row.classList.add('focused-row');
                    if (row.scrollIntoView) {
                        row.scrollIntoView({ block: 'nearest' });
                    }
                } else {
                    row.style.outline = 'none';
                    row.classList.remove('focused-row');
                }
            }
        }

        /** @param {HTMLElement} tr */
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
            try { occ = JSON.parse(tr.getAttribute('data-occ') || 'null'); } catch (e) { occ = null; }
            var picked = [];
            if (occ) {
                var keys = [];
                for (var s = 0; s < sels.length; s++) {
                    var k = String(sels[s].visual_idx);
                    if (occ.hasOwnProperty(k) && keys.indexOf(k) === -1) keys.push(k);
                }
                keys.sort(function(a, b) { return parseInt(String(a), 10) - parseInt(String(b), 10); });
                for (var q = 0; q < keys.length; q++) picked.push(occ[keys[q]]);
            }
            for (var cls in cellMap) {
                if (!cellMap.hasOwnProperty(cls)) continue;
                var td = (tr.querySelector('td.' + cls));
                if (!td) continue;
                var cell = (td.querySelector('.scrollable-cell')) || td;
                if (!td.hasAttribute('data-orig-html')) td.setAttribute('data-orig-html', String(cell.innerHTML));
                if (!td.hasAttribute('data-orig-title')) td.setAttribute('data-orig-title', String(td.getAttribute('title') || ''));
                if (cell !== td && !cell.hasAttribute('data-orig-title')) cell.setAttribute('data-orig-title', String(cell.getAttribute('title') || ''));

                if (picked.length === 0) {
                    if (cell.innerHTML !== td.getAttribute('data-orig-html')) cell.innerHTML = td.getAttribute('data-orig-html') || '';
                    var origTdTitle = td.getAttribute('data-orig-title');
                    if (origTdTitle) td.setAttribute('title', String(origTdTitle)); else td.removeAttribute('title');
                    if (cell !== td) {
                        var origCellTitle = cell.getAttribute('data-orig-title');
                        if (origCellTitle) cell.setAttribute('title', String(origCellTitle)); else cell.removeAttribute('title');
                    }
                    continue;
                }
                var vals = [];
                for (var p = 0; p < picked.length; p++) {
                    var v = (picked[p][cellMap[cls]] || '').trim();
                    if (v && vals.indexOf(v) === -1) vals.push(v);
                }
                if (vals.length === 0) {
                    cell.innerHTML = td.getAttribute('data-orig-html') || '';
                    var origTdTitle = td.getAttribute('data-orig-title');
                    if (origTdTitle) td.setAttribute('title', String(origTdTitle)); else td.removeAttribute('title');
                    if (cell !== td) {
                        var origCellTitle = cell.getAttribute('data-orig-title');
                        if (origCellTitle) cell.setAttribute('title', String(origCellTitle)); else cell.removeAttribute('title');
                    }
                } else {
                    var newTooltip = '';
                    if (cls === 'col-pos') {
                        var posOrder = POS_DISPLAY_ORDER_MAP;
                        vals.sort(function(a, b) {
                            var oa = (posOrder[a] !== undefined) ? posOrder[a] : 99;
                            var ob = (posOrder[b] !== undefined) ? posOrder[b] : 99;
                            return oa - ob;
                        });
                        cell.textContent = vals.join(', ');
                        newTooltip = vals.map(function(p) {
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
                            var pTitle = (typeof formatProvenanceTooltip === 'function') ? formatProvenanceTooltip(prov) : ((window.formatProvenanceTooltip) ? window.formatProvenanceTooltip(prov) : prov);
                            newTooltip += ' (' + (pTitle || prov) + ')';
                        }
                    } else if (cls === 'col-inflected') {
                        cell.textContent = vals.join(', ');
                        var formsStr = vals.join(', ');
                        var boldHeader = (typeof toUnicodeBold === 'function') ? toUnicodeBold(formsStr) : (window.toUnicodeBold ? window.toUnicodeBold(formsStr) : formsStr);
                        var sentList = [];
                        for (var q = 0; q < picked.length; q++) {
                            if (picked[q].inflected_tooltip) {
                                var rawTip = picked[q].inflected_tooltip;
                                var sentPart = (rawTip.indexOf('\n') !== -1) ? rawTip.slice(rawTip.indexOf('\n') + 1).trim() : rawTip.trim();
                                if (sentPart && sentList.indexOf(sentPart) === -1) {
                                    sentList.push(sentPart);
                                }
                            }
                        }
                        if (sentList.length > 1) {
                            var numbered = sentList.map(function(s, idx) { return '[' + (idx + 1) + '] ' + s; });
                            newTooltip = boldHeader ? (boldHeader + '\n' + numbered.join('\n')) : numbered.join('\n');
                        } else if (sentList.length === 1) {
                            newTooltip = boldHeader ? (boldHeader + '\n' + sentList[0]) : sentList[0];
                        } else {
                            newTooltip = boldHeader || formsStr;
                        }
                    }

                    if (newTooltip) {
                        td.setAttribute('title', String(newTooltip));
                        if (cell !== td) cell.setAttribute('title', String(newTooltip));
                    }
                }
            }
        }
        window.applyOccurrenceRow = applyOccurrenceRow;

        function applyOccurrenceView() {
            var rows = (document.querySelectorAll('tr[data-occ]'));
            for (var r = 0; r < rows.length; r++) {
                applyOccurrenceRow(rows[r]);
            }
        }
        window.applyOccurrenceView = applyOccurrenceView;

        function updateBidirectionalHighlights() {
            try { applyOccurrenceView(); } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            var tokByVidx = {},  spanByVidx = {},  sentByRowId = {};
            for (var m = 0; m < tokenMap.length; m++) {
                var t = tokenMap[m];
                tokByVidx[String(t.visual_idx)] = t;
                if (t.row_ids && t.sentence_idx !== undefined && t.sentence_idx !== null) {
                    for (var r = 0; r < t.row_ids.length; r++) {
                        var k = String(t.row_ids[r]);
                        if (!sentByRowId.hasOwnProperty(k)) sentByRowId[k] = t.sentence_idx;
                    }
                }
            }
            for (var i = 0; i < tokenSpans.length; i++) {
                var span = tokenSpans[i];
                var sk = String(span.getAttribute('data-word-idx'));
                if (!spanByVidx.hasOwnProperty(sk)) spanByVidx[sk] = span;
                try {
                    span.classList.remove('highlight-orange-active');
                    span.classList.remove('highlight-purple-active');
                    span.classList.remove('active-subtoken');
                    span.classList.remove('lemma-peer-highlight');
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }

            var isLegacyOverview = isLegacyOverviewContext();
            var hasActiveTokens = !isLegacyOverview && (window.AppState && window.AppState.activeTokenSelections && window.AppState.activeTokenSelections.length > 0);
            var isSameLemmaOn = !!(window.AppState && window.AppState.highlightSameLemma);

            var selLemmas = (window.AppState && window.AppState.selectedLemmas) ? window.AppState.selectedLemmas : {};
            var activeTargetRowIds = {};
            var primaryTargetRowIds = {};
            for (var rId in selectedRowIdsMap) {
                if (!selectedRowIdsMap.hasOwnProperty(rId)) continue;
                var rowId = parseInt(String(rId), 10);
                if (!isNaN(rowId)) {
                    activeTargetRowIds[rowId] = true;
                }
            }
            var curTableRows = (typeof tableRows !== 'undefined' && tableRows && tableRows.length > 0)
                ? tableRows
                :  (document.querySelectorAll('#lemma-table tbody tr[data-row-id], #lemma-table tr[data-row-id]'));
            for (var i = 0; i < curTableRows.length; i++) {
                var tr = curTableRows[i];
                var trId = String(tr.getAttribute('data-row-id'));
                var trSelected = tr.classList.contains('selected') || tr.classList.contains('kw-row-selected') || tr.getAttribute('data-selected') === '1' || selectedRowIdsMap.hasOwnProperty(trId);
                if (trSelected) {
                    var trIdInt = parseInt(String(trId), 10);
                    var primarySentIdx = null;
                    if (!isNaN(trIdInt)) {
                        activeTargetRowIds[trIdInt] = true;
                        primaryTargetRowIds[trIdInt] = true;
                        primarySentIdx = sentByRowId.hasOwnProperty(String(trIdInt)) ? sentByRowId[String(trIdInt)] : null;
                    }
                    var extraIds = getExtraRowIds(tr);
                    for (var p = 0; p < extraIds.length; p++) {
                        var pInt = parseInt(String(extraIds[p]), 10);
                        if (!isNaN(pInt)) {
                            activeTargetRowIds[pInt] = true;
                            var pSentIdx = sentByRowId.hasOwnProperty(String(pInt)) ? sentByRowId[String(pInt)] : null;
                            if (primarySentIdx === null || pSentIdx === null || String(pSentIdx) === String(primarySentIdx)) {
                                primaryTargetRowIds[pInt] = true;
                            }
                        }
                    }
                }
            }
            if (lastClickedRowId !== null && lastClickedRowId !== undefined) {
                var lcInt = parseInt(String(lastClickedRowId), 10);
                if (!isNaN(lcInt) && activeTargetRowIds.hasOwnProperty(lcInt)) {
                    primaryTargetRowIds[lcInt] = true;
                }
            }

            var activeVisualIdxs = {};
            var activeLemmas = {};
            var activeTokenRowIds = {};
            var isCompoundRow = /** @param {number|string} rVal @param {Token|null} tokObj */ function(rVal, tokObj) {
                if (!tokObj) return false;
                var cRows = tokObj.compound_row_ids;
                if (cRows && cRows.length > 0) {
                    var rStr = String(rVal);
                    for (var c = 0; c < cRows.length; c++) {
                        if (String(cRows[c]) === rStr) return true;
                    }
                }
                return false;
            };
            if (hasActiveTokens) {
                var addLem = /** @param {string} lVal */ function(lVal) {
                    if (lVal) {
                        var c = String(lVal).trim().toLowerCase();
                        if (c) activeLemmas[c] = true;
                    }
                };
                var markActiveRow = /** @param {number|string} rVal */ function(rVal) {
                    if (rVal !== undefined && rVal !== null) {
                        activeTokenRowIds[String(rVal)] = true;
                    }
                };
                var addRowLem = /** @param {number|string} rVal */ function(rVal) {
                    if (rVal !== undefined && rVal !== null) {
                        var rStr = String(rVal);
                        activeTokenRowIds[rStr] = true;
                        if (typeof getLemmaByRowId === 'function') {
                            var rL = getLemmaByRowId(rVal);
                            if (rL) addLem(rL);
                        }
                    }
                };

                for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                    var item = window.AppState.activeTokenSelections[a];
                    if (item.visual_idx !== undefined && item.visual_idx !== null) {
                        activeVisualIdxs[String(item.visual_idx)] = true;
                    }
                    if (item.lemma) {
                        addLem(item.lemma);
                    }
                    if (item.atomic_id !== undefined && item.atomic_id !== null) {
                        addRowLem(item.atomic_id);
                    }
                    if (item.atomic_row_ids) {
                        for (var r = 0; r < item.atomic_row_ids.length; r++) {
                            addRowLem(item.atomic_row_ids[r]);
                        }
                    }
                    var vTok = tokByVidx[String(item.visual_idx)];
                    if (item.row_ids) {
                        for (var r = 0; r < item.row_ids.length; r++) {
                            var rId = String(item.row_ids[r]);
                            markActiveRow(rId);
                            if (!isCompoundRow(rId, item) && !isCompoundRow(rId, vTok)) {
                                var rL = (typeof getLemmaByRowId === 'function') ? getLemmaByRowId(rId) : '';
                                if (rL) addLem(rL);
                            }
                        }
                    }
                    if (item.row_id !== undefined && item.row_id !== null) {
                        markActiveRow(item.row_id);
                        if (!isCompoundRow(item.row_id, item) && !isCompoundRow(item.row_id, vTok)) {
                            var rL = (typeof getLemmaByRowId === 'function') ? getLemmaByRowId(item.row_id) : '';
                            if (rL) addLem(rL);
                        }
                    }

                    if (vTok) {
                        if (vTok.lemma) {
                            addLem(vTok.lemma);
                        }
                        if (vTok.atomic_row_ids) {
                            for (var r = 0; r < vTok.atomic_row_ids.length; r++) {
                                addRowLem(vTok.atomic_row_ids[r]);
                            }
                        }
                        if (vTok.row_ids) {
                            for (var r = 0; r < vTok.row_ids.length; r++) {
                                var rId = String(vTok.row_ids[r]);
                                markActiveRow(rId);
                                if (!isCompoundRow(rId, item) && !isCompoundRow(rId, vTok)) {
                                    var rL = (typeof getLemmaByRowId === 'function') ? getLemmaByRowId(rId) : '';
                                    if (rL) addLem(rL);
                                }
                            }
                        }
                    }
                }
                for (var i = 0; i < curTableRows.length; i++) {
                    var tr = curTableRows[i];
                    var trGroup = getRowGroupIds(tr);
                    var hasActiveInTr = false;
                    for (var g = 0; g < trGroup.length; g++) {
                        if (activeTokenRowIds[trGroup[g]]) {
                            hasActiveInTr = true;
                            break;
                        }
                    }
                    if (hasActiveInTr) {
                        for (var g = 0; g < trGroup.length; g++) {
                            activeTokenRowIds[trGroup[g]] = true;
                        }
                    }
                }
            } else {
                if (window.AppState) window.AppState.activeLemmas = {};
            }
            if (window.AppState) window.AppState.activeLemmas = activeLemmas;
            for (var i = 0; i < tokenMap.length; i++) {
                var token = tokenMap[i];
                var isOrphanSelected = (typeof selectedOrphanWordIdxsMap !== 'undefined' && selectedOrphanWordIdxsMap && selectedOrphanWordIdxsMap.hasOwnProperty(String(token.visual_idx)));
                var tokRowIds = [];
                if (token.atomic_row_ids && token.atomic_row_ids.length > 0) {
                    for (var tri = 0; tri < token.atomic_row_ids.length; tri++) {
                        tokRowIds.push(token.atomic_row_ids[tri]);
                    }
                }
                if (token.row_ids && token.row_ids.length > 0) {
                    for (var tri = 0; tri < token.row_ids.length; tri++) {
                        if (tokRowIds.indexOf(token.row_ids[tri]) === -1) {
                            tokRowIds.push(token.row_ids[tri]);
                        }
                    }
                }
                if (tokRowIds.length === 0 && !isOrphanSelected) continue;

                var hasMatchingRow = false;
                var isDirectRowMatch = false;
                var isTargetedActiveTokenMatch = false;
                var matchedRowId = null;

                var tokClean = (token.lower_clean || token.text || '').trim().toLowerCase();
                var tokLem = (token.lemma || tokClean).trim().toLowerCase();
                var isLemmaActive = false;
                if (hasActiveTokens) {
                    if (tokLem && activeLemmas.hasOwnProperty(tokLem)) {
                        isLemmaActive = true;
                    } else {
                        for (var cr = 0; cr < tokRowIds.length; cr++) {
                            if (isCompoundRow(tokRowIds[cr], token)) continue;
                            if (typeof getLemmaByRowId === 'function') {
                                var candLem = getLemmaByRowId(tokRowIds[cr]);
                                if (candLem && activeLemmas.hasOwnProperty(String(candLem).trim().toLowerCase())) {
                                    isLemmaActive = true;
                                    break;
                                }
                            }
                        }
                    }
                }

                var isTokenRowSelected = false;
                if (isOrphanSelected || tokRowIds.length === 0) {
                    isTokenRowSelected = true;
                } else {
                    for (var tri = 0; tri < tokRowIds.length; tri++) {
                        var trid = tokRowIds[tri];
                        if (selectedRowIdsMap.hasOwnProperty(String(trid)) || activeTargetRowIds.hasOwnProperty(parseInt(String(trid), 10))) {
                            isTokenRowSelected = true;
                            break;
                        }
                        if (typeof getLemmaByRowId === 'function') {
                            var rLem = getLemmaByRowId(trid);
                            if (rLem && selLemmas && selLemmas[String(rLem).trim().toLowerCase()]) {
                                isTokenRowSelected = true;
                                break;
                            }
                        }
                    }
                    if (!isTokenRowSelected && tokLem && selLemmas && selLemmas[tokLem]) {
                        isTokenRowSelected = true;
                    }
                }

                if (hasActiveTokens && (isOrphanSelected || !!activeVisualIdxs[String(token.visual_idx)])) {
                    if (isTokenRowSelected) {
                        hasMatchingRow = true;
                        isTargetedActiveTokenMatch = true;
                    }
                } else if (tokRowIds.length > 0) {
                    for (var r = 0; r < tokRowIds.length; r++) {
                        var rid = tokRowIds[r];
                        if (primaryTargetRowIds.hasOwnProperty(rid)) {
                            if (!isLemmaActive) {
                                hasMatchingRow = true;
                                isDirectRowMatch = true;
                                matchedRowId = rid;
                                break;
                            } else {
                                var tSpan = spanByVidx[String(token.visual_idx)];
                                var isSameOccurrence = false;
                                for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                                    var atItem = window.AppState.activeTokenSelections[a];
                                    var aSpan = spanByVidx[String(atItem.visual_idx)];
                                    if (!aSpan || !tSpan) continue;

                                    var tSent = (tSpan && tSpan.getAttribute('data-sentence-idx')) || (token.sentence_idx !== undefined ? String(token.sentence_idx) : '');
                                    var aSent = (aSpan && aSpan.getAttribute('data-sentence-idx')) || (atItem.sentence_idx !== undefined ? String(atItem.sentence_idx) : '');
                                    var sameSentence = (tSent && aSent) ? (tSent === aSent) : true;
                                    if (!sameSentence) continue;

                                    var tokAtomics = (token.atomic_row_ids && token.atomic_row_ids.length > 0) ? token.atomic_row_ids : (token.row_ids || []);
                                    var sharesIdenticalAtomic = false;
                                    if (atItem.atomic_id !== null && atItem.atomic_id !== undefined && tokAtomics.indexOf(atItem.atomic_id) !== -1) {
                                        var atClean = (atItem.lower_clean || atItem.text || '').trim().toLowerCase();
                                        if (!atClean && aSpan) {
                                            atClean = (aSpan.getAttribute('data-lower-clean') || aSpan.textContent || '').trim().toLowerCase();
                                        }
                                        if (tokClean && atClean && tokClean === atClean) {
                                            var aTd = (typeof findTokenDataByVisualIdx === 'function') ? findTokenDataByVisualIdx(Number(atItem.visual_idx)) : null;
                                            var tGrp = (tSpan && typeof findCompoundSiblingSpans === 'function') ? findCompoundSiblingSpans(tSpan) : null;
                                            var aGrp = (aSpan && typeof findCompoundSiblingSpans === 'function') ? findCompoundSiblingSpans(aSpan) : null;
                                            var hasCompoundContext = (token.compound_row_ids && token.compound_row_ids.length > 0) ||
                                                (aTd && aTd.compound_row_ids && aTd.compound_row_ids.length > 0) ||
                                                (atItem.compound_row_ids && atItem.compound_row_ids.length > 0) ||
                                                (tSpan && tSpan.hasAttribute('data-compound-id')) ||
                                                (aSpan && aSpan.hasAttribute('data-compound-id')) ||
                                                (tGrp && tGrp.length > 1) ||
                                                (aGrp && aGrp.length > 1);
                                            if (hasCompoundContext) {
                                                sharesIdenticalAtomic = true;
                                            }
                                        }
                                    }

                                    var sameCompoundId = (tSpan.hasAttribute('data-compound-id') && aSpan.hasAttribute('data-compound-id'))
                                        ? (tSpan.getAttribute('data-compound-id') === aSpan.getAttribute('data-compound-id'))
                                        : false;

                                    var isCompoundSibling = false;
                                    if (typeof findCompoundSiblingSpans === 'function') {
                                        var grp = findCompoundSiblingSpans(aSpan);
                                        if (grp && grp.indexOf(tSpan) !== -1) {
                                            isCompoundSibling = true;
                                        }
                                    }
                                    var isRelatedPartner = false;
                                    if (typeof findRelatedTokenSpans === 'function') {
                                        var relGrp = findRelatedTokenSpans(aSpan);
                                        if (relGrp && relGrp.indexOf(tSpan) !== -1) {
                                            isRelatedPartner = true;
                                        }
                                    }

                                    if (sharesIdenticalAtomic || sameCompoundId || isCompoundSibling || isRelatedPartner) {
                                        isSameOccurrence = true;
                                        break;
                                    }
                                }

                                if (isSameOccurrence) {
                                    if (isTokenRowSelected) {
                                        hasMatchingRow = true;
                                        isTargetedActiveTokenMatch = true;
                                    }
                                    break;
                                } else {
                                    hasMatchingRow = true;
                                    isDirectRowMatch = false;
                                    isTargetedActiveTokenMatch = false;
                                    matchedRowId = rid;
                                    break;
                                }
                            }
                        } else if (activeTargetRowIds.hasOwnProperty(rid)) {
                            if (!isLemmaActive) {
                                hasMatchingRow = true;
                                isDirectRowMatch = false;
                                matchedRowId = rid;
                                break;
                            }
                        }
                    }
                }

                if (!hasMatchingRow && selLemmas) {
                    var tokRowIds = (token.atomic_row_ids && token.atomic_row_ids.length > 0)
                        ? token.atomic_row_ids
                        : (token.row_ids || []);
                    for (var r = 0; r < tokRowIds.length; r++) {
                        var rLem = getLemmaByRowId(tokRowIds[r]);
                        if (rLem && selLemmas[rLem]) {
                            hasMatchingRow = true;
                            matchedRowId = tokRowIds[r];
                            break;
                        }
                    }
                    if (!hasMatchingRow && token.lemma && selLemmas[String(token.lemma).trim().toLowerCase()]) {
                        hasMatchingRow = true;
                    }
                }

                if (hasMatchingRow) {
                    var span = spanByVidx[String(token.visual_idx)] || null;
                    if (span) {
                        try {
                            if (isDirectRowMatch || isTargetedActiveTokenMatch) {
                                if (isTokenRowSelected) {
                                    if (span.classList.contains('highlight-purple')) {
                                        span.classList.add('highlight-purple-active');
                                    } else if (span.classList.contains('highlight-orange')) {
                                        span.classList.add('highlight-orange-active');
                                    }
                                    if (isOrphanSelected) {
                                        span.classList.add('active-subtoken');
                                    }
                                }
                            } else if (isLegacyOverview) {
                                if (span.classList.contains('highlight-purple')) {
                                    span.classList.add('highlight-purple-active');
                                } else if (span.classList.contains('highlight-orange')) {
                                    span.classList.add('highlight-orange-active');
                                }
                                if (isOrphanSelected) {
                                    span.classList.add('active-subtoken');
                                }
                            } else if (isSameLemmaOn) {
                                span.classList.add('lemma-peer-highlight');
                            }
                        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                    }
                }
            }

            if (selLemmas && isLegacyOverview) {
                for (var highlightSpanIndex = 0; highlightSpanIndex < tokenSpans.length; highlightSpanIndex++) {
                    var s = tokenSpans[highlightSpanIndex];
                    var sLem = (getWordLemma(s) || '').trim().toLowerCase();
                    if (sLem && selLemmas[sLem]) {
                        try {
                            if (s.classList.contains('highlight-purple')) {
                                s.classList.add('highlight-purple-active');
                            } else if (s.classList.contains('highlight-orange')) {
                                s.classList.add('highlight-orange-active');
                            }
                        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                    }
                }
            }

            if (hasActiveTokens && isSameLemmaOn) {
                for (var highlightSpanIndex = 0; highlightSpanIndex < tokenSpans.length; highlightSpanIndex++) {
                    var s = tokenSpans[highlightSpanIndex];
                    var vIdxStr = String(s.getAttribute('data-word-idx'));
                    if (activeVisualIdxs[vIdxStr]) continue;
                    if (s.classList.contains('highlight-orange-active') || s.classList.contains('highlight-purple-active')) continue;
                    var sLem = (getWordLemma(s) || s.getAttribute('data-lower-clean') || '').trim().toLowerCase();
                    if (!sLem) {
                        var td = tokByVidx[vIdxStr] || (typeof findTokenData === 'function' ? findTokenData(s) : null);
                        if (td && td.lemma) sLem = String(td.lemma).trim().toLowerCase();
                    }
                    var sMatchesLemma = (sLem && activeLemmas[sLem]);
                    if (!sMatchesLemma) {
                        var td = tokByVidx[vIdxStr] || (typeof findTokenData === 'function' ? findTokenData(s) : null);
                        if (td) {
                            var tdRows = (td.atomic_row_ids && td.atomic_row_ids.length > 0)
                                ? td.atomic_row_ids
                                : (td.row_ids || []).filter(function(r) { return !isCompoundRow(r, td); });
                            for (var highlightRowIndex = 0; highlightRowIndex < tdRows.length; highlightRowIndex++) {
                                if (typeof getLemmaByRowId === 'function') {
                                    var tdLem = getLemmaByRowId(tdRows[highlightRowIndex]);
                                    if (tdLem && activeLemmas[String(tdLem).trim().toLowerCase()]) {
                                        sMatchesLemma = true;
                                        break;
                                    }
                                }
                            }
                        }
                    }
                    if (sMatchesLemma) {
                        try {
                            s.classList.add('lemma-peer-highlight');
                        } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                    }
                }
            }

            if (mousedownTargetSpan && mousedownTargetSpan.classList &&
                (mousedownTargetSpan.classList.contains('highlight-orange-active') || mousedownTargetSpan.classList.contains('highlight-purple-active'))) {
                try {
                    var isCompoundSubtoken = typeof findCompoundSiblingSpans === 'function' && findCompoundSiblingSpans(mousedownTargetSpan).length > 1;
                    if (isCompoundSubtoken) {
                        mousedownTargetSpan.classList.add('active-subtoken');
                    }
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }
        }
        window.updateBidirectionalHighlights = updateBidirectionalHighlights;

        /** @param {boolean=} extractTokenOrders */
        function getSelectedRowsArray(extractTokenOrders) {
            if (extractTokenOrders) {
                return getSelectedTokenOrdersArray();
            }
            var arr = [];
            for (var k in selectedRowIdsMap) {
                if (selectedRowIdsMap.hasOwnProperty(k)) {
                    var num = parseInt(String(k), 10);
                    if (!isNaN(num) && arr.indexOf(num) === -1) {
                        arr.push(num);
                    }
                }
            }
            var selRows = (document.querySelectorAll('#lemma-table tbody tr.kw-row-selected, #lemma-table tbody tr.selected, #lemma-table tbody tr[data-selected="1"]'));
            for (var r = 0; r < selRows.length; r++) {
                var rId = selRows[r].getAttribute('data-row-id');
                if (rId !== null && rId !== '') {
                    var num = parseInt(String(rId), 10);
                    if (!isNaN(num) && arr.indexOf(num) === -1) {
                        arr.push(num);
                    }
                }
            }
            return arr;
        }

        function getSelectedTokenOrdersArray() {
            var arr = [];
            for (var k in selectedRowIdsMap) {
                if (selectedRowIdsMap.hasOwnProperty(k)) {
                    var tr = (document.querySelector('tr[data-row-id="' + k + '"]'));
                    if (tr) {
                        var tOrd = tr.getAttribute('data-token-order');
                        if (tOrd !== null && tOrd !== undefined && tOrd !== '') {
                            var parsed = parseInt(String(tOrd), 10);
                            arr.push(!isNaN(parsed) ? parsed : tOrd);
                            continue;
                        }
                    }
                    var kNum = parseInt(String(k), 10);
                    arr.push(!isNaN(kNum) ? kNum : k);
                }
            }
            return arr;
        }
        window.getSelectedRowsArray = getSelectedRowsArray;
        window.getSelectedTokenOrdersArray = getSelectedTokenOrdersArray;

        function notifyAHKSelection() {
            scheduleSelectionSave();
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
                var curCard = window.WorkspaceTabs.getActiveCard();
                if (curCard) {
                    curCard.selected_ids = [];
                    for (var k in selectedRowIdsMap) {
                        if (selectedRowIdsMap.hasOwnProperty(k)) curCard.selected_ids.push(k);
                    }
                }
            }
            if (typeof updateToolbarState === 'function') {
                updateToolbarState();
            }
            if (window.ahkCall) {
                try {
                    window.ahkCall('selection', getSelectedRowsArray().join(','));
                    window.ahkCall('dirty', window.isDirty() ? 'true' : 'false');
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }
        }
        window.notifyAHKSelection = notifyAHKSelection;

        /** @param {HTMLElement} cell */
        function makeEditable(cell) {
            if (cell.getElementsByTagName('input').length > 0) return;

            var scrollDiv = (cell.querySelector('.scrollable-cell'));
            var originalValue = scrollDiv ? (scrollDiv.textContent || scrollDiv.innerText) : (cell.textContent || cell.innerText || "");
            var colName = cell.getAttribute('data-col');
            var trParent = cell.parentElement;
            if (!trParent || !colName) throw new Error('Editable cell requires a row and column');
            var rowId = trParent.getAttribute('data-row-id');
            var tokenOrder = trParent.getAttribute('data-token-order');
            var tokenOrderVal = (tokenOrder !== null && tokenOrder !== '') ? parseInt(String(tokenOrder), 10) : parseInt(String(rowId), 10);
            var sentenceIdx = trParent.getAttribute('data-sentence-idx');
            var sentIdxVal = (sentenceIdx !== null && sentenceIdx !== '') ? parseInt(String(sentenceIdx), 10) : 1;

            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'edit-input cell-editor';
            input.value = originalValue;
            input.style.width = '100%';
            input.style.boxSizing = 'border-box';
            input.style.background = '#161a22';
            input.style.color = '#e3e6eb';
            input.style.border = '1px solid rgba(255, 255, 255, 0.1)';
            input.style.borderRadius = '4px';
            input.style.padding = '4px';

            cell.innerHTML = '';
            if (!cell.classList.contains('editing')) {
                cell.classList.add('editing');
            }
            cell.appendChild(input);
            input.focus();
            try {
                input.select();
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }

            window.cancelActiveEdit = function() {
                window.cancelActiveEdit = null;
                cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(originalValue));
                if (colName === 'WordDestination' || cell.classList.contains('col-translation')) {
                    var curProv = cell.getAttribute('data-provenance');
                    var origTip = formatTranslationCellTooltip(originalValue, curProv);
                    if (origTip) {
                        cell.setAttribute('title', String(origTip));
                        div.setAttribute('title', String(origTip));
                    } else {
                        cell.removeAttribute('title');
                        div.removeAttribute('title');
                    }
                } else if (colName === 'WordSource' || cell.classList.contains('col-lemma')) {
                    var curGenCell = trParent ?  (trParent.querySelector('td.col-gender, td[data-col="WordSourceGender"], td[data-col="gender"]')) : null;
                    var curGen = curGenCell ? (curGenCell.textContent || curGenCell.innerText || "").trim() : "";
                    var curPosCell = trParent ?  (trParent.querySelector('td.col-pos, td[data-col="WordSourcePos"], td[data-col="pos"]')) : null;
                    var curPos = curPosCell ? (curPosCell.textContent || curPosCell.innerText || "").trim() : "";
                    var activeLang = (typeof getSessionLang === 'function' ? getSessionLang() : '') || (window.AppConfig ? window.AppConfig.language : '');
                    var origTip = formatLemmaTooltip(originalValue, curGen, activeLang, curPos);
                    if (origTip) {
                        cell.setAttribute('title', String(origTip));
                        div.setAttribute('title', String(origTip));
                    } else {
                        cell.removeAttribute('title');
                        div.removeAttribute('title');
                    }
                } else if (colName === 'WordSourceInflectedForm' || cell.classList.contains('col-inflected')) {
                    var lemCell = trParent ?  (trParent.querySelector('td.col-lemma, td[data-col="WordSource"]')) : null;
                    var lemVal = lemCell ? (lemCell.textContent || lemCell.innerText || "").trim() : "";
                    var cardSentenceText = (window.AppState ? window.AppState.sourceText : '') || '';
                    var origTip = formatInflectedTooltip(cardSentenceText, originalValue, tokenOrderVal, lemVal);
                    if (origTip) {
                        cell.setAttribute('title', String(origTip));
                        div.setAttribute('title', String(origTip));
                    } else {
                        cell.removeAttribute('title');
                        div.removeAttribute('title');
                    }
                }
                cell.appendChild(div);
                cell.classList.remove('editing');
                if (window.forceRepaint) window.forceRepaint();
            };

            function commit() {
                if (!window.cancelActiveEdit) return;
                window.cancelActiveEdit = null;
                var newValue = input.value;
                cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(newValue));
                if (colName === 'WordDestination' || cell.classList.contains('col-translation')) {
                    var curProv = cell.getAttribute('data-provenance');
                    var newTip = formatTranslationCellTooltip(newValue, curProv);
                    if (newTip) {
                        cell.setAttribute('title', String(newTip));
                        div.setAttribute('title', String(newTip));
                    } else {
                        cell.removeAttribute('title');
                        div.removeAttribute('title');
                    }
                } else if (colName === 'WordSource' || cell.classList.contains('col-lemma')) {
                    var curGenCell = trParent ?  (trParent.querySelector('td.col-gender, td[data-col="WordSourceGender"], td[data-col="gender"]')) : null;
                    var curGen = curGenCell ? (curGenCell.textContent || curGenCell.innerText || "").trim() : "";
                    var curPosCell = trParent ?  (trParent.querySelector('td.col-pos, td[data-col="WordSourcePos"], td[data-col="pos"]')) : null;
                    var curPos = curPosCell ? (curPosCell.textContent || curPosCell.innerText || "").trim() : "";
                    var activeLang = (typeof getSessionLang === 'function' ? getSessionLang() : '') || (window.AppConfig ? window.AppConfig.language : '');
                    var newTip = formatLemmaTooltip(newValue, curGen, activeLang, curPos);
                    if (newTip) {
                        cell.setAttribute('title', String(newTip));
                        div.setAttribute('title', String(newTip));
                    } else {
                        cell.removeAttribute('title');
                        div.removeAttribute('title');
                    }
                } else if (colName === 'WordSourceInflectedForm' || cell.classList.contains('col-inflected')) {
                    var lemCell = trParent ?  (trParent.querySelector('td.col-lemma, td[data-col="WordSource"]')) : null;
                    var lemVal = lemCell ? (lemCell.textContent || lemCell.innerText || "").trim() : "";
                    var cardSentenceText = (window.AppState ? window.AppState.sourceText : '') || '';
                    var newTip = formatInflectedTooltip(cardSentenceText, newValue, tokenOrderVal, lemVal);
                    if (newTip) {
                        cell.setAttribute('title', String(newTip));
                        div.setAttribute('title', String(newTip));
                    } else {
                        cell.removeAttribute('title');
                        div.removeAttribute('title');
                    }
                }
                cell.appendChild(div);
                cell.classList.remove('editing');
                if (newValue !== originalValue) {
                    var action = {
                        type: 'edit',
                        rowId: parseInt(String(rowId), 10),
                        tokenOrder: tokenOrderVal,
                        sentenceIdx: sentIdxVal,
                        column: String(colName),
                        oldValue: originalValue,
                        newValue: newValue,
                        cell: cell
                    };
                    pushHistory(action);
                    rebuildDeltas();
                    touchedCells[rowId + '_' + colName] = true;
                    if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
                }
                if (window.forceRepaint) window.forceRepaint();
            }
            window.commitActiveEdit = commit;

            addEvent(input, 'keydown', function(e) {
                e = (e || window.event);
                var keyCode = e.keyCode;
                if (e.ctrlKey && keyCode === 65) { // Ctrl+A
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    input.select();
                } else if (keyCode === 13) { // Enter
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    commit();
                } else if (keyCode === 27) { // Escape
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    if (window.cancelActiveEdit) window.cancelActiveEdit();
                } else if (keyCode === 9) { // Tab
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    commit();

                    var tds = document.getElementsByTagName('td');
                    var editables = [];
                    for (var k = 0; k < tds.length; k++) {
                        if (tds[k].classList.contains('editable')) {
                            editables.push(tds[k]);
                        }
                    }

                    var idx = -1;
                    for (var k = 0; k < editables.length; k++) {
                        if (editables[k] === cell) {
                            idx = k;
                            break;
                        }
                    }
                    var nextIdx = e.shiftKey ? idx - 1 : idx + 1;
                    if (nextIdx >= 0 && nextIdx < editables.length) {
                        makeEditable(editables[nextIdx]);
                    }
                }
            });

            addEvent(input, 'blur', function() {
                commit();
            });
        }
        window.makeEditable = makeEditable;

        window.getSelectedRows = function() {
            return JSON.stringify(getSelectedRowsArray());
        };

        window.setSelectedRows = function(rowsJsonStr) {
            try {
                var arr = JSON.parse(rowsJsonStr);
                selectedRowIdsMap = {};
                selectedOrphanWordIdxsMap = {};
                if (window.AppState) window.AppState.selectedLemmas = {};
                for (var i = 0; i < arr.length; i++) {
                    var rId = String(arr[i]);
                    selectedRowIdsMap[rId] = true;
                    var lem = getLemmaByRowId(rId);
                    if (lem) setLemmaSelected(lem, true);
                }
                syncSelectionsToCardsAndState();
                updateRowStyles();
                updateBidirectionalHighlights();
                if (window.forceRepaint) window.forceRepaint();
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        };

        window.deleteSelectedRows = function() {
             var selected = getSelectedRowsArray();
            if (selected.length === 0) return;
            var tOrders = [];
            var sIndices = [];
            for (var i = 0; i < selected.length; i++) {
                var rId = selected[i];
                var tOrd = rId;
                var sIdx = 1;
                for (var k = 0; k < tableRows.length; k++) {
                    if (parseInt(String(tableRows[k].getAttribute('data-row-id')), 10) === rId) {
                        var toAttr = tableRows[k].getAttribute('data-token-order');
                        if (toAttr !== null && toAttr !== '') tOrd = parseInt(String(toAttr), 10);
                        var siAttr = tableRows[k].getAttribute('data-sentence-idx');
                        if (siAttr !== null && siAttr !== '') sIdx = parseInt(String(siAttr), 10);
                        break;
                    }
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

        /** @param {HistoryAction} action */
        function pushHistory(action) {
            recordUserIntent(action, true, true);
            historyStack.splice(historyIndex + 1);
            historyStack.push(action);
            historyIndex++;
        }

        /** @param {HistoryAction} action @param {string} value */
        function projectEditedValue(action, value) {
            action.cell.innerHTML = '';
            var div = document.createElement('div');
            div.className = 'scrollable-cell';
            div.textContent = value;
            action.cell.appendChild(div);
            if (action.column === 'WordDestination') {
                var title = formatTranslationCellTooltip(value, action.cell.getAttribute('data-provenance'));
                action.cell.setAttribute('title', String(title));
                div.setAttribute('title', String(title));
            }
        }

        /** @param {HistoryAction} action */
        function applyAction(action) {
            recordUserIntent(action, true, false);
            if (action.type === 'edit') {
                projectEditedValue(action, action.newValue);
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    for (var i = 0; i < tableRows.length; i++) {
                        if (parseInt(String(tableRows[i].getAttribute('data-row-id'))) === rId) {
                            tableRows[i].setAttribute('data-deleted', 'true');
                            tableRows[i].style.display = 'none';
                            break;
                        }
                    }
                }
                clearAllSelections();
            }
        }

        /** @param {HistoryAction} action */
        function revertAction(action) {
            recordUserIntent(action, false, false);
            if (action.type === 'edit') {
                projectEditedValue(action, action.oldValue);
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    for (var i = 0; i < tableRows.length; i++) {
                        if (parseInt(String(tableRows[i].getAttribute('data-row-id'))) === rId) {
                            tableRows[i].removeAttribute('data-deleted');
                            tableRows[i].style.display = '';
                            break;
                        }
                    }
                }
            }
        }

        window.undo = function() {
            if (historyIndex < 0) return;
            var action = historyStack[historyIndex];
            historyIndex--;
            revertAction(action);
            rebuildDeltas();
            if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
        };

        window.redo = function() {
            if (historyIndex >= historyStack.length - 1) return;
            historyIndex++;
            var action = historyStack[historyIndex];
            applyAction(action);
            rebuildDeltas();
            if (window.triggerAutoSaveOnEdit) window.triggerAutoSaveOnEdit();
        };

        /** @param {Delta} delta */
        function deltaIdentity(delta) {
            return [delta.sentence_idx, delta.token_order, delta.column].join(':');
        }

        /** @param {HistoryAction} action */
        function historyValues(action) {
            if (action.type === 'edit') return [{row_id: action.rowId, token_order: action.tokenOrder,
                sentence_idx: action.sentenceIdx, column: action.column, value: action.newValue, original: action.oldValue, element: action.cell}];
            return action.rowIds.map(function(id, index) {
                return {row_id: id, token_order: action.tokenOrders[index], sentence_idx: action.sentenceIndices[index],
                    column: '_delete', value: true, original: false,
                    element:  (document.querySelector('#lemma-table tr[data-row-id="' + id + '"]'))};
            });
        }

        /** @param {HistoryAction} action @param {boolean} forward @param {boolean} newAction */
        function recordUserIntent(action, forward, newAction) {
            historyValues(action).forEach(function(value) {
                var key = deltaIdentity(value);
                var baseline = acknowledgedValues[key];
                if (!baseline) {
                    var descriptor = {row_id: value.row_id, token_order: value.token_order, sentence_idx: value.sentence_idx, column: value.column, value: value.value};
                    baseline = {value: value.original, current: value.original, delta: descriptor};
                    acknowledgedValues[key] = baseline;
                }
                if (newAction && baseline.current === baseline.value) baseline.value = value.original;
                baseline.current = forward ? value.value : value.original;
            });
        }

        /** @param {Delta[]} snapshot @param {SaveResponse|null|undefined} response */
        function acknowledgeValues(snapshot, response) {
            snapshot.forEach(function(delta) {
                if (delta.column !== 'DeskSelected') acknowledgedValues[deltaIdentity(delta)].value = delta.value;
            });
            var restored = (response && response.deleted_rows) || [];
            restored.forEach(function(item) {
                var key = deltaIdentity(item);
                if (acknowledgedValues[key]) acknowledgedValues[key].delta.restore = item.restore;
            });
            acknowledgeSelections(snapshot);
            rebuildDeltas();
        }

        /** @param {Delta} delta */
        function markDirtyDelta(delta) {
            if (delta.column === '_delete') return;
            var row = (document.querySelector('#lemma-table tr[data-row-id="' + delta.row_id + '"]'));
            if (!row) return;
            var cell = (row.querySelector('td[data-col="' + delta.column + '"]'));
            if (cell) cell.classList.add('dirty');
        }

        function notifyDirtyState() {
            if (window.ahkCall) {
                try { window.ahkCall('dirty', deltas.length > 0 ? 'true' : 'false'); }
                catch (err) { console.error('[Save] Native dirty-state notification failed', err); }
            }
            updateToolbarState();
            window.forceRepaint();
        }

        function rebuildDeltas() {
            deltas = [];
            Array.prototype.forEach.call( (document.querySelectorAll('#lemma-table td.dirty')), function(cell) {
                cell.classList.remove('dirty');
            });
            Object.keys(acknowledgedValues).forEach(function(key) {
                var baseline = acknowledgedValues[key];
                if (baseline.current !== baseline.value) deltas.push(Object.assign({}, baseline.delta, {value: baseline.current}));
            });
            deltas.forEach(markDirtyDelta);
            notifyDirtyState();
        }

        window.getDeltas = function() {
            var mergedDeltas = [];
            for (var i = 0; i < deltas.length; i++) {
                mergedDeltas.push(deltas[i]);
            }
            if (hasHighlightCol) {
                if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
                    var wCards = window.WorkspaceTabs.getCards();
                    if (wCards && wCards.length > 0) {
                        var activeC = (typeof window.WorkspaceTabs.getActiveCard === 'function') ? window.WorkspaceTabs.getActiveCard() : null;
                        if (activeC) {
                            activeC.selected_ids = [];
                            for (var sk in selectedRowIdsMap) {
                                if (selectedRowIdsMap.hasOwnProperty(sk)) activeC.selected_ids.push(sk);
                            }
                        }
                        var seenWordDeltas = {};
                        for (var c = 0; c < wCards.length; c++) {
                            var card = wCards[c];
                            var curSelMap = {};
                            if (card.selected_ids) {
                                for (var s = 0; s < card.selected_ids.length; s++) curSelMap[String(card.selected_ids[s])] = true;
                            }
                            if (card.words) {
                                for (var w = 0; w < card.words.length; w++) {
                                    var cw = card.words[w];
                                    var rIdStr = String(cw.row_id);
                                    var isCur = curSelMap.hasOwnProperty(rIdStr);
                                    var isInit = (cw.selected === '1' || cw.selected === 1);
                                    var sIdxVal = (cw.sentence_idx !== undefined && cw.sentence_idx !== null && String(cw.sentence_idx) !== '') ? parseInt(String(cw.sentence_idx), 10) : (card.sentence_idx !== undefined ? card.sentence_idx : 1);
                                    var deltaKey = sIdxVal + '_' + rIdStr;
                                    if (isCur !== isInit && !seenWordDeltas[deltaKey]) {
                                        seenWordDeltas[deltaKey] = true;
                                        var tOrdVal = (cw.token_order !== undefined && cw.token_order !== null && String(cw.token_order) !== '') ? parseInt(String(cw.token_order), 10) : parseInt(String(rIdStr), 10);
                                        mergedDeltas.push({
                                            row_id: parseInt(String(rIdStr), 10),
                                            token_order: tOrdVal,
                                            sentence_idx: sIdxVal,
                                            column: 'DeskSelected',
                                            value: isCur ? '1' : ''
                                        });
                                    }
                                }
                            }
                        }
                        return JSON.stringify(mergedDeltas);
                    }
                }
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    var tOrdStr = row.getAttribute('data-token-order');
                    var tOrdVal = (tOrdStr !== null && tOrdStr !== '') ? parseInt(String(tOrdStr), 10) : parseInt(String(rowIdStr), 10);
                    var sIdxStr = row.getAttribute('data-sentence-idx');
                    var sIdxVal = (sIdxStr !== null && sIdxStr !== '') ? parseInt(String(sIdxStr), 10) : 1;
                    var currentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                    var initiallySelected = initialHighlights[rowIdStr] || false;
                    if (currentlySelected !== initiallySelected) {
                        mergedDeltas.push({
                            row_id: parseInt(String(rowIdStr), 10),
                            token_order: tOrdVal,
                            sentence_idx: sIdxVal,
                            column: 'DeskSelected',
                            value: currentlySelected ? '1' : ''
                        });
                    }
                }
            }
            return JSON.stringify(mergedDeltas);
        };

        window.clearDirty = function() {
            acknowledgedValues = {};
            historyStack = [];
            historyIndex = -1;
            deltas = [];
            if (hasHighlightCol) {
                if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function') {
                    var wCards = window.WorkspaceTabs.getCards();
                    if (wCards && wCards.length > 0) {
                        var activeC = (typeof window.WorkspaceTabs.getActiveCard === 'function') ? window.WorkspaceTabs.getActiveCard() : null;
                        if (activeC) {
                            activeC.selected_ids = [];
                            for (var sk in selectedRowIdsMap) {
                                if (selectedRowIdsMap.hasOwnProperty(sk)) activeC.selected_ids.push(sk);
                            }
                        }
                        for (var c = 0; c < wCards.length; c++) {
                            var card = wCards[c];
                            var curSelMap = {};
                            if (card.selected_ids) {
                                for (var s = 0; s < card.selected_ids.length; s++) curSelMap[String(card.selected_ids[s])] = true;
                            }
                            if (card.words) {
                                for (var w = 0; w < card.words.length; w++) {
                                    var cw = card.words[w];
                                    var isCur = curSelMap.hasOwnProperty(String(cw.row_id));
                                    cw.selected = isCur ? '1' : '0';
                                    initialHighlights[String(cw.row_id)] = isCur;
                                }
                            }
                        }
                    }
                }
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    initialHighlights[rowIdStr] = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                }
            }
            var tds = document.getElementsByTagName('td');
            for (var k = 0; k < tds.length; k++) {
                tds[k].classList.remove('dirty');
            }
            if (window.ahkCall) {
                try {
                    window.ahkCall('dirty', 'false');
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }
            if (typeof updateToolbarState === 'function') updateToolbarState();
        };

        window.isDirty = function() {
            if (nativeSaveSnapshot) return true;
            if (saveState && (saveState.phase === 'reconciling' || saveState.phase === 'reconcile_failed')) return true;
            return JSON.parse(window.getDeltas()).length > 0;
        };

        window.editFocusedCell = function() {
            var cellToEdit = null;
            if (lastHoveredCell && lastHoveredCell.parentElement) {
                var rId = parseInt(String(lastHoveredCell.parentElement.getAttribute('data-row-id')));
                if (rId === focusedRowId) {
                    cellToEdit = lastHoveredCell;
                }
            }
            if (!cellToEdit && lastClickedCell && lastClickedCell.parentElement) {
                var rId = parseInt(String(lastClickedCell.parentElement.getAttribute('data-row-id')));
                if (rId === focusedRowId) {
                    cellToEdit = lastClickedCell;
                }
            }
            if (!cellToEdit && focusedRowId !== null) {
                for (var k = 0; k < tableRows.length; k++) {
                    if (tableRows[k].getAttribute('data-row-id') === String(focusedRowId)) {
                        var tds = tableRows[k].getElementsByTagName('td');
                        for (var j = 0; j < tds.length; j++) {
                            if (tds[j].classList.contains('editable')) {
                                cellToEdit = tds[j];
                                break;
                            }
                        }
                        break;
                    }
                }
            }
            if (cellToEdit) {
                makeEditable(cellToEdit);
            }
            if (window.forceRepaint) window.forceRepaint();
        };

        window.selectAllInActiveEdit = function() {
            var el = (document.activeElement);
            if (el && el.tagName === 'INPUT') {
                el.select();
            }
        };

        window.selectAllRows = function() {
            if (typeof tableRows === 'undefined' || !tableRows || tableRows.length === 0) {
                var lt = document.getElementById('lemma-table');
                if (lt) {
                    var trs = (lt.querySelectorAll('tbody tr[data-row-id]'));
                    var fresh = [];
                    for (var r = 0; r < trs.length; r++) fresh.push(trs[r]);
                    tableRows = fresh;
                }
            }
            if (typeof tableRows !== 'undefined' && tableRows.length > 0) {
                for (var i = 0; i < tableRows.length; i++) {
                    var rowId = String(tableRows[i].getAttribute('data-row-id'));
                    selectedRowIdsMap[rowId] = true;
                    var lem = getRowLemma(tableRows[i]);
                    if (lem) setLemmaSelected(lem, true);
                }
                syncSelectionsToCardsAndState();
                updateRowStyles();
                updateBidirectionalHighlights();
                if (typeof notifyAHKSelection !== 'undefined') {
                    notifyAHKSelection();
                }
            }
        };

        window.copySelection = function() {
            try {
                document.execCommand('copy');
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        };

        function updateToolbarState() {
            var saveBtn = (document.getElementById('kw-btn-save'));
            if (saveBtn) {
                saveBtn.disabled = isSavePending() || typeof window.isDirty !== 'function' || !window.isDirty();
            }
        }
        window.updateToolbarState = updateToolbarState;

        function isSavePending() {
            return !!(nativeSaveSnapshot || (saveState && saveState.promise));
        }

        /** @param {HTMLButtonElement|null} btn @param {boolean} isLoading @param {string} loadingText @param {string} defaultText */
        function setButtonLoading(btn, isLoading, loadingText, defaultText) {
            if (typeof btn === 'string') {
                btn = (document.getElementById(btn));
            }
            if (!btn) return;
            if (isLoading) {
                if (!btn.hasAttribute('data-default-text')) {
                    btn.setAttribute('data-default-text', String(defaultText || btn.textContent || btn.innerText || ""));
                }
                btn.disabled = true;
                btn.textContent = loadingText || "Loading...";
                if (btn._recoveryTimer) {
                    clearTimeout(btn._recoveryTimer);
                }
                btn._recoveryTimer = setTimeout(function() {
                    if (btn.id === 'kw-btn-save' && isSavePending()) {
                        btn._recoveryTimer = null;
                        window.showToast('Save is still pending acknowledgment.', 'warning');
                        return;
                    }
                    btn.disabled = false;
                    var original = btn.getAttribute('data-default-text') || defaultText;
                    if (original) {
                        btn.textContent = original;
                    }
                    if (btn.id === 'kw-btn-save') {
                        updateToolbarState();
                    }
                    btn._recoveryTimer = null;
                    if (typeof window.showToast === 'function') {
                        window.showToast("Action timed out. Restored toolbar controls.", "warning");
                    }
                }, 25000);
            } else {
                if (btn._recoveryTimer) {
                    clearTimeout(btn._recoveryTimer);
                    btn._recoveryTimer = null;
                }
                btn.disabled = false;
                var original = btn.getAttribute('data-default-text') || defaultText;
                if (original) {
                    btn.textContent = original;
                }
                if (btn.id === 'kw-btn-save') {
                    updateToolbarState();
                }
            }
        }
        window.setButtonLoading = setButtonLoading;

        /** @param {ErrorResponse|Error|string|null|undefined} resObj @param {string=} fallback */
        function formatErrorWithTrace(resObj, fallback) {
            fallback = fallback || "Operation failed";
            if (!resObj) return fallback;
            if (typeof resObj === 'string') return resObj;
            if (resObj instanceof Error) return resObj.message || String(resObj);

            var data = (resObj && resObj.data) ? resObj.data : resObj;
            if (typeof data === 'string') return data;

            var msg = data.message || data.error || (data.data && (data.data.message || data.data.error)) || "";
            var trace = data.error_trace || (data.data && data.data.error_trace) || "";
            var traceId = data.trace_id || (data.data && data.data.trace_id) || "";

            var parts = [];
            if (msg) parts.push(msg);
            else if (!trace && !traceId) parts.push(fallback);

            if (traceId) {
                parts.push("[Trace: " + traceId + "]");
            }
            if (trace && trace !== msg) {
                parts.push(trace);
            }
            return parts.length > 0 ? parts.join(" - ") : fallback;
        }
        window.formatErrorWithTrace = formatErrorWithTrace;

        window.showToast = function(msg, type, durationMs, onClick) {
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
                setTimeout(function() {
                    if (toast.parentNode) toast.parentNode.removeChild(toast);
                }, 250);
            }

            if (typeof onClick === 'function') {
                toast.style.cursor = 'pointer';
                toast.onclick = function(e) {
                    onClick(e);
                    dismiss();
                };
            } else {
                toast.onclick = dismiss;
            }
            container.appendChild(toast);
            setTimeout(dismiss, durationMs);
        };

        function getDeliveryMode() {
            var el = document.getElementById('delivery-mode');
            if (el) {
                var t = (el.textContent || el.innerText || '').trim();
                if (t) return t.toLowerCase();
            }
            if (window.AppState && window.AppState.deliveryMode) return String(window.AppState.deliveryMode).toLowerCase();
            if (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.delivery_mode) return String(window.AppState.bootstrap.delivery_mode).toLowerCase();
            var bootEl = document.getElementById('kardenwort-bootstrap');
            if (bootEl) {
                try {
                    var b = JSON.parse(bootEl.textContent || '{}');
                    if (b && b.delivery_mode) return String(b.delivery_mode).toLowerCase();
                } catch(e) {
                    console.error('[getDeliveryMode] Malformed bootstrap JSON:', e);
                }
            }
            var legacyEl = document.getElementById('web-tab-mode');
            if (legacyEl) {
                var lt = (legacyEl.textContent || legacyEl.innerText || '').trim().toLowerCase();
                if (lt === 'tabs') return 'multi_window';
                if (lt === 'container') return 'container';
            }
            return 'container';
        }

        function getSessionZid() {
            var delMode = getDeliveryMode();
            if (delMode !== 'container') {
                if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
                    var c = window.WorkspaceTabs.getActiveCard();
                    if (c && c.zid) return c.zid;
                }
            }
            var el = document.getElementById('session-zid');
            var zidVal = el ? (el.textContent || el.innerText || "").trim() : "";
            if (!zidVal) {
                try {
                    var urlParams = new URLSearchParams(window.location.search);
                    zidVal = urlParams.get('zid') || urlParams.get('session_zid') || "";
                } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            }
            if (!zidVal && SESSION_ZID) {
                zidVal = SESSION_ZID;
            }
            if (!zidVal && window.AppState) {
                if (window.AppState.bootstrap && window.AppState.bootstrap.session_data && (window.AppState.bootstrap.session_data.session_zid || window.AppState.bootstrap.session_data.zid)) {
                    zidVal = window.AppState.bootstrap.session_data.session_zid || window.AppState.bootstrap.session_data.zid || '';
                } else if (window.AppState.session_data && (window.AppState.session_data.session_zid || window.AppState.session_data.zid)) {
                    zidVal = window.AppState.session_data.session_zid || window.AppState.session_data.zid || '';
                } else if (window.AppState.session_zid || window.AppState.zid) {
                    zidVal = window.AppState.session_zid || window.AppState.zid || '';
                }
            }
            if (!zidVal) {
                if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
                    var c = window.WorkspaceTabs.getActiveCard();
                    if (c && c.zid) return c.zid;
                }
            }
            return zidVal;
        }

        function getSessionLang() {
            if (window.AppState && window.AppState.bootstrap && (window.AppState.bootstrap.language || window.AppState.bootstrap.lang)) {
                return window.AppState.bootstrap.language || window.AppState.bootstrap.lang;
            }
            if (window.AppState && window.AppState.language) return window.AppState.language;
            if (typeof LANGUAGE !== 'undefined' && LANGUAGE) return LANGUAGE;
            var el = document.getElementById('session-lang');
            var l = el ? (el.textContent || el.innerText || "").trim() : "";
            if (l) return l;
            return "";
        }
        window.getSessionLang = getSessionLang;

        function getSessionTargetLang() {
            if (window.AppState && window.AppState.bootstrap && (window.AppState.bootstrap.target_language || window.AppState.bootstrap.target_lang)) {
                return window.AppState.bootstrap.target_language || window.AppState.bootstrap.target_lang;
            }
            if (window.AppState && (window.AppState.target_lang || window.AppState.target_language)) {
                return window.AppState.target_lang || window.AppState.target_language;
            }
            if (typeof TARGET_LANG !== 'undefined' && TARGET_LANG) return TARGET_LANG;
            var el = document.getElementById('session-target-lang');
            var l = el ? (el.textContent || el.innerText || "").trim() : "";
            if (l) return l;
            return "";
        }
        window.getSessionTargetLang = getSessionTargetLang;

        function getApiToken() {
            if (typeof API_TOKEN !== 'undefined' && API_TOKEN && API_TOKEN !== '__API_TOKEN__') {
                return API_TOKEN;
            }
            if (typeof window.API_TOKEN !== 'undefined' && window.API_TOKEN) {
                return window.API_TOKEN;
            }
            if (window.AppState && window.AppState.bootstrap && window.AppState.bootstrap.session_data && window.AppState.bootstrap.session_data.api_token) {
                return window.AppState.bootstrap.session_data.api_token;
            }
            try {
                var params = new URLSearchParams(window.location.search);
                var tok = params.get('token') || params.get('api_token') || "";
                if (tok) {
                    try {
                        sessionStorage.setItem('kw_api_token', tok);
                        localStorage.setItem('kw_api_token', tok);
                    } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
                    return tok;
                }
                var stored = sessionStorage.getItem('kw_api_token') || localStorage.getItem('kw_api_token');
                if (stored) return stored;
            } catch(e) {
                return "";
            }
            return "";
        }
        window.getApiToken = getApiToken;

        /** @param {number|string} rowId @param {HTMLButtonElement} btnElement */
        function retryRow(rowId, btnElement) {
            var curZid = getSessionZid();
            if (!curZid) {
                var sessZidEl = document.getElementById('session-zid');
                curZid = sessZidEl ? (sessZidEl.textContent || sessZidEl.innerText || "").trim() : "";
            }
            if (!curZid) {
                if (typeof window.showToast === 'function') window.showToast("Session ZID missing.", "error");
                return;
            }

            var tr = (document.querySelector('tr[data-row-id="' + rowId + '"]'));
             var cellDiv = null;
            if (tr) {
                var cell = (tr.querySelector('td.col-translation, td[data-col="WordDestination"], td[data-col="word_translation"]')) || tr.getElementsByTagName('td')[2];
                if (cell) {
                    cellDiv = (cell.querySelector('.scrollable-cell')) || cell;
                }
            }
            if (btnElement) {
                btnElement.disabled = true;
            }
            if (cellDiv) {
                cellDiv.innerHTML = '<span class="skeleton-loader" data-pending="true" title="Retrying..."></span>';
            }

            var token = getApiToken ? getApiToken() : "";
            var headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            };
            if (token) headers['X-API-Token'] = token;

            var retryPayload = {
                session_zid: curZid,
                row_ids: [parseInt(String(rowId), 10)]
            };
            var trElem = (document.querySelector('tr[data-row-id="' + rowId + '"]'));
            if (trElem) {
                var tOrdAttr = trElem.getAttribute('data-token-order');
                if (tOrdAttr !== null && tOrdAttr !== undefined && tOrdAttr !== '') {
                    var parsedTOrd = parseInt(String(tOrdAttr), 10);
                    retryPayload.token_orders = [!isNaN(parsedTOrd) ? parsedTOrd : tOrdAttr];
                }
            }

            fetch('/session/retry', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(retryPayload)
            })
            .then(function(res) {
                if (!res.ok) throw new Error("Retry failed with status " + res.status);
                return res.json();
            })
            .then(function(resObj) {
                var data = (resObj && resObj.data) ? resObj.data : resObj;
                if (data && data.rows && window.receiveUpdate) {
                    window.receiveUpdate(data);
                } else if (data && data.status === 'success') {
                    fetch('/session/status?zid=' + encodeURIComponent(curZid), { method: 'GET', headers: { 'Accept': 'application/json' } })
                        .then(function(r) { if (r.ok) return r.json(); })
                        .then(function(d) {
                            var statusData = (d && d.data) ? d.data : d;
                            if (statusData && statusData.rows && window.receiveUpdate) {
                                window.receiveUpdate(statusData);
                            }
                        }).catch(function(err) { console.debug("[Async] Non-fatal operation caught:", err); });
                }
            })
            .catch(function(err) {
                if (cellDiv) {
                    cellDiv.innerHTML = '';
                }
                if (typeof window.showToast === 'function') {
                    window.showToast("Retry failed: " + (err instanceof Error ? err.message : String(err)), "error");
                }
            });
        }
        window.retryRow = retryRow;

        /** @param {string=} sessionZid */
        function retrySession(sessionZid) {
            var curZid = sessionZid || getSessionZid();
            if (!curZid) return;
            window._kwRetryToastShown = false;
            var token = getApiToken ? getApiToken() : "";
            var headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            };
            if (token) headers['X-API-Token'] = token;

            if (typeof window.showToast === 'function') {
                window.showToast("Retrying unresolved translations...", "info");
            }

            fetch('/session/retry', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    session_zid: curZid
                })
            })
            .then(function(res) {
                if (!res.ok) throw new Error("Batch retry failed with status " + res.status);
                return res.json();
            })
            .then(function(resObj) {
                var data = (resObj && resObj.data) ? resObj.data : resObj;
                if (data && window.receiveUpdate) {
                    window.receiveUpdate(data);
                }
                if (typeof window.showToast === 'function') {
                    window.showToast("Retry complete", "success");
                }
            })
            .catch(function(err) {
                if (typeof window.showToast === 'function') {
                    window.showToast("Retry failed: " + (err instanceof Error ? err.message : String(err)), "error");
                }
                if (typeof window.cleanupOrphanSkeletons === 'function') {
                    window.cleanupOrphanSkeletons();
                }
            });
        }
        window.retrySession = retrySession;

        var saveState = {phase: 'idle', promise: null};
        var streamRecovery = null;
         var selectionSaveTimer = null;
         var nativeSaveSnapshot = null;

        window.captureSaveSnapshot = function() {
            if (saveState.promise) throw new Error('A save is already pending');
            if (nativeSaveSnapshot) throw new Error('A native save is already pending');
            statusRead = null;
            nativeSaveSnapshot = {deltas: JSON.parse(window.getDeltas()), revision: membershipRevision, session: getSessionZid()};
            return JSON.stringify(nativeSaveSnapshot.deltas);
        };
        window.abortSaveSnapshot = function() { nativeSaveSnapshot = null; };
        window.acknowledgeSaveSnapshot = function(serialized, response) {
            var snapshot = matchingNativeSnapshot(serialized);
            var acknowledgment = response ? JSON.parse(response) : null;
            if (response && !successfulSaveResponse({ok: true}, acknowledgment)) throw new Error('Native save failed; changes retained');
            var needsRead = nativeSnapshotNeedsRead(snapshot, response);
            acknowledgeValues(snapshot.deltas, acknowledgment && (acknowledgment.data || acknowledgment));
            nativeSaveSnapshot = null;
            if (needsRead) reconcileNativeSelections(snapshot.session);
            notifyDirtyState();
            return window.isDirty();
        };

        function matchingNativeSnapshot(serialized) {
            var snapshot = nativeSaveSnapshot;
            if (!snapshot || JSON.stringify(snapshot.deltas) !== serialized) {
                throw new Error('Save acknowledgment does not match the submitted snapshot');
            }
            if (!isCurrentSaveSession(snapshot)) nativeSaveSnapshot = null;
            assertCurrentSave(snapshot);
            return snapshot;
        }

        function nativeSnapshotNeedsRead(snapshot, response) {
            return saveState.phase === 'reconcile_failed' || (response && membershipRevision !== snapshot.revision);
        }

        function reconcileNativeSelections(session) {
            saveState.phase = 'reconciling';
            try {
                var response = JSON.parse(window.ahkCall('get_shared_selection', JSON.stringify({session_zid: session, accept_updates: false})));
                assertCurrentSave({session: session});
                window.applySharedSelection(selectionReadResult(response));
                saveState.phase = 'idle';
            } catch (err) {
                saveState.phase = 'reconcile_failed';
                throw err;
            }
        }

        function assertCurrentSave(context) {
            if (!isCurrentSaveSession(context)) {
                throw new Error('Save response belongs to an inactive session or mount');
            }
        }

        function isCurrentSaveSession(context) {
            return window.__deskInstanceId === myDeskInstanceId && getSessionZid() === context.session;
        }

        function successfulSaveResponse(res, data) {
            return res.ok && (data.ok || data.status === 'success');
        }

        function selectionReadResult(response) {
            var data = response.data || response;
            if (response.ok === false || response.status === 'error' || !data.shared_selection) {
                throw new Error(formatErrorWithTrace(response, 'Selection reconciliation failed'));
            }
            return data.shared_selection;
        }

        function reconcileSelections(context) {
            assertCurrentSave(context);
            saveState.phase = 'reconciling';
            return readSharedSelections(context).then(function(membership) {
                assertCurrentSave(context);
                window.applySharedSelection(membership);
            }).catch(function(err) {
                if (isCurrentSaveSession(context)) saveState.phase = 'reconcile_failed';
                throw err;
            });
        }

        function readSharedSelections(context) {
            var body = JSON.stringify({session_zid: context.session, accept_updates: false});
            var request;
            if (window.ahkCall && (document.documentMode || typeof fetch === 'undefined')) {
                request = Promise.resolve().then(function() {
                    return JSON.parse(window.ahkCall('get_shared_selection', body));
                });
            } else {
                var endpoint = (context.serverUrl || SERVER_URL).replace(/\/+$/, '') + '/internal/selection';
                var headers = {'Content-Type': 'application/json'};
                var token = getApiToken();
                if (token) headers['X-API-Token'] = token;
                request = fetch(endpoint, {method: 'POST', headers: headers, body: body}).then(function(res) {
                    if (!res.ok) throw new Error('Selection reconciliation failed with status ' + res.status);
                    return res.json();
                });
            }
            return request.then(selectionReadResult);
        }

        function updateSyncFeedback(status, message) {
            var header = document.getElementById('session-header');
            var indicator = document.getElementById('sse-status-indicator');
            if (!indicator) {
                indicator = document.createElement('span');
                indicator.id = 'sse-status-indicator';
                indicator.className = 'kw-sse-status';
                if (!header) indicator.style.display = 'none';
                (header || document.body).appendChild(indicator);
            }
            if (status) {
                indicator.setAttribute('data-sync', status);
            } else {
                indicator.removeAttribute('data-sync');
            }
            if (message) {
                indicator.setAttribute('data-sync-message', message);
                indicator.title = message;
            } else {
                indicator.removeAttribute('data-sync-message');
            }
        }

        function assertCurrentRecovery(recovery) {
            assertCurrentSave(recovery.context);
            if (recovery !== streamRecovery || recovery.phase === 'cancelled') {
                throw new Error('Membership recovery was cancelled');
            }
            var bridge = window.SSEBridge;
            if (!bridge || bridge.getActiveConnectionId() !== recovery.context.connectionId ||
                    bridge.getActiveZid() !== recovery.context.session) {
                throw new Error('Membership recovery belongs to an inactive connection');
            }
        }

        function invalidateStreamMembership() {
            if (!streamRecovery) return;
            streamRecovery.phase = 'cancelled';
            clearTimeout(streamRecovery.timer);
            streamRecovery.cancel(new Error('Membership recovery belongs to an inactive connection'));
        }
        window.invalidateStreamMembership = invalidateStreamMembership;

        function readStreamMembership(recovery, attempt) {
            assertCurrentRecovery(recovery);
            var revision = membershipRevision;
            return readSharedSelections(recovery.context).then(function(membership) {
                assertCurrentRecovery(recovery);
                if (membershipRevision !== revision) throw new Error('Membership changed during synchronization');
                window.applySharedSelection(membership);
            }).catch(function(err) {
                assertCurrentRecovery(recovery);
                if (attempt >= 3) throw err;
                return new Promise(function(resolve) {
                    recovery.timer = setTimeout(resolve, attempt * 500);
                }).then(function() { return readStreamMembership(recovery, attempt + 1); });
            });
        }

        function finishStreamRecovery(recovery, error) {
            assertCurrentRecovery(recovery);
            recovery.phase = error ? 'failed' : 'synced';
            if (error) {
                updateSyncFeedback('failed', error.message);
                window.showToast('Membership synchronization incomplete: ' + error.message, 'warning');
                throw error;
            }
            updateSyncFeedback('synced', 'Membership synchronized');
        }

        /** @param {{session?: string, connectionId?: number, serverUrl?: string}} [context] @returns {Promise<void>} */
        function reconcileStreamMembership(context) {
            context = context || {};
            context.session = context.session || getSessionZid();
            if (isPendingStreamRecovery(context)) return streamRecovery.promise;
            invalidateStreamMembership();
            var recovery = {context: context, phase: 'syncing', promise: null, cancel: null, timer: null};
            streamRecovery = recovery;
            var cancellation = new Promise(function(resolve, reject) { recovery.cancel = reject; });
            // Capture only the earlier core save, never a later save waiting on this recovery.
            var precedingSave = saveState.promise || Promise.resolve();
            var operation = precedingSave.then(function() {
                assertCurrentRecovery(recovery);
                updateSyncFeedback('syncing', 'Synchronizing membership...');
                return readStreamMembership(recovery, 1);
            }).then(function() { finishStreamRecovery(recovery, null); }, function(err) {
                finishStreamRecovery(recovery, err);
            });
            recovery.promise = Promise.race([operation, cancellation]);
            return recovery.promise;
        }
        window.reconcileStreamMembership = reconcileStreamMembership;

        function isPendingStreamRecovery(context) {
            return streamRecovery && streamRecovery.phase === 'syncing' &&
                streamRecovery.context.session === context.session &&
                streamRecovery.context.connectionId === context.connectionId;
        }

        function resumeMountedStream() {
            var bridge = window.SSEBridge;
            if (!bridge || bridge.getActiveZid() !== getSessionZid()) return;
            var indicator = document.getElementById('sse-status-indicator');
            if (!indicator || indicator.getAttribute('data-status') !== 'connected') return;
            reconcileStreamMembership({session: bridge.getActiveZid(), connectionId: bridge.getActiveConnectionId(),
                serverUrl: bridge.getServerUrl()}).catch(function(error) {
                console.debug('[SSE] Mounted recovery stopped:', error.message);
            });
        }

        /** @param {Delta[]} snapshot */
        function acknowledgeSelections(snapshot) {
            var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
            snapshot.filter(function(d) { return d.column === 'DeskSelected'; }).forEach(function(d) {
                cards.forEach(function(card) {
                    (card.words || []).forEach(function(word) {
                        var sentence = word.sentence_idx !== undefined ? word.sentence_idx : card.sentence_idx;
                        if (Number(sentence) === Number(d.sentence_idx) && Number(word.token_order) === Number(d.token_order)) {
                            word.selected = d.value === '1' ? '1' : '0';
                        }
                    });
                });
                if (!cards.length) initialHighlights[String(d.row_id)] = d.value === '1';
            });
        }

        /** @param {Delta[]} snapshot */
        function saveSnapshot(snapshot, context) {
            assertCurrentSave(context);
            var token = getApiToken();
            var headers = {'Content-Type': 'application/json'};
            if (token) headers['X-API-Token'] = token;
            var body = JSON.stringify({session_zid: context.session, language: getSessionLang(), token: token, deltas: snapshot});
            var request;
            if (window.ahkCall && (document.documentMode || typeof fetch === 'undefined')) {
                request = Promise.resolve().then(function() {
                    if (!window.ahkCall) throw new Error('Native bridge is no longer attached');
                    var data = JSON.parse(window.ahkCall('persist_selection', body));
                    return {ok: data.ok !== false && !data.error, json: function() { return Promise.resolve(data); }};
                });
            } else {
                request = fetch('/session/save', {
                method: 'POST', headers: headers,
                body: body
                });
            }
            return request.then(function(res) {
                return res.json().then(function(data) {
                    if (!successfulSaveResponse(res, data)) throw new Error(formatErrorWithTrace(data, 'Save failed'));
                    assertCurrentSave(context);
                    var needsRead = membershipRevision !== context.revision && snapshot.some(function(d) { return d.column === 'DeskSelected'; });
                    if (needsRead) saveState.phase = 'reconciling';
                    acknowledgeValues(snapshot, data.data || data);
                    if (needsRead) return reconcileSelections(context);
                });
            });
        }

        /** @param {boolean} selectionOnly @returns {Promise<void>} */
        function persistPending(selectionOnly) {
            if (nativeSaveSnapshot) return Promise.reject(new Error('A native save is already pending'));
            if (saveState.promise) {
                return saveState.promise.then(function() { return persistPending(selectionOnly); });
            }
            if (streamRecovery && streamRecovery.phase === 'cancelled') {
                return Promise.reject(new Error('Membership recovery belongs to an inactive connection'));
            }
            if (streamRecovery && streamRecovery.phase !== 'synced') {
                return streamRecovery.promise.then(function() { return persistPending(selectionOnly); });
            }
            var snapshot = JSON.parse(window.getDeltas());
            if (selectionOnly) snapshot = snapshot.filter(function(d) { return d.column === 'DeskSelected'; });
            var retryRead = saveState.phase === 'reconcile_failed';
            if (!snapshot.length && !retryRead) return Promise.resolve();
            if (!getSessionZid()) return Promise.reject(new Error('Session ZID missing'));
            if (!getSessionLang()) return Promise.reject(new Error('Session language missing'));
            // A command after this save needs a status read started after the write.
            statusRead = null;
            var button = (document.getElementById('kw-btn-save'));
            setButtonLoading(button, true, 'Saving...', 'Save (Ctrl+S)');
            var context = {session: getSessionZid(), revision: membershipRevision};
            saveState = {phase: 'saving', snapshot: snapshot, promise: Promise.resolve().then(function() {
                return retryRead ? reconcileSelections(context) : saveSnapshot(snapshot, context);
            }).finally(function() {
                saveState = {phase: saveState.phase === 'reconcile_failed' ? 'reconcile_failed' : 'idle', promise: null};
                if (window.__deskInstanceId !== myDeskInstanceId || getSessionZid() !== context.session) return;
                setButtonLoading(button, false, '', 'Save (Ctrl+S)');
                updateToolbarState();
            })};
            return saveState.promise.then(function() {
                // Acknowledgment may expose a newer reversal of a submitted value.
                if (selectionOnly || retryRead) return persistPending(selectionOnly);
            });
        }

        function scheduleSelectionSave() {
            if (selectionSaveTimer) clearTimeout(selectionSaveTimer);
            selectionSaveTimer = setTimeout(function() {
                selectionSaveTimer = null;
                persistPending(true).catch(function(err) {
                    window.showToast('Selection save failed: ' + formatErrorWithTrace(err, 'Network error'), 'error');
                });
            }, 300);
        }

        window.flushPendingSaves = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            return persistPending(false).then(function() {
                if (window.isDirty()) return window.flushPendingSaves();
            });
        };
        window.onSaveClick = function(quiet) {
            if (window.commitActiveEdit) window.commitActiveEdit();
            return persistPending(false).then(function() {
                if (!quiet) window.showToast('Edits saved successfully', 'success');
                return true;
            }).catch(function(err) {
                window.showToast('Save failed: ' + formatErrorWithTrace(err, 'Network error'), 'error');
                return false;
            });
        };

         var autoSaveDebounceTimer = null;
        window.triggerAutoSaveOnEdit = function() {
            var uiCfg = window.__CONFIG__ || {};
            if (!uiCfg.auto_save_on_edit) return;
            if (autoSaveDebounceTimer) {
                clearTimeout(autoSaveDebounceTimer);
            }
            autoSaveDebounceTimer = setTimeout(function() {
                if (window.isDirty && window.isDirty() && typeof window.onSaveClick === 'function') {
                    window.onSaveClick(true);
                }
            }, 300);
        };

        function performSilentAutoSaveOnClose() {
            var uiCfg = window.__CONFIG__ || {};
            if (!uiCfg.auto_save_on_close) return;
            if (typeof window.commitActiveEdit === 'function') {
                try { window.commitActiveEdit(); } catch(e) { console.error('[AutoSave] commitActiveEdit failed:', e); }
            }
            if (!window.isDirty || !window.isDirty()) return;
            if (typeof fetch === 'undefined') return;
            var sZid = (typeof getSessionZid === 'function') ? getSessionZid() : null;
            if (!sZid) return;
            var deltasJson = [];
            try {
                deltasJson = JSON.parse(window.getDeltas());
            } catch(e) {
                console.error('[AutoSave] Failed to parse deltas JSON:', e);
                return;
            }
            if (!deltasJson || deltasJson.length === 0) return;

            var tok = (typeof getApiToken === 'function') ? getApiToken() : '';
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                deltas: deltasJson,
                language: (typeof getSessionLang === 'function') ? getSessionLang() : ''
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
            } catch(e) {
                if (navigator && navigator.sendBeacon) {
                    try {
                        var blob = new Blob([bodyStr], { type: 'application/json' });
                        navigator.sendBeacon('/session/save', blob);
                    } catch(e2) { console.debug("[Diagnostic] Handled exception:", e2); }
                }
            }
        }

        if (typeof window !== 'undefined' && window.addEventListener) {
            window.addEventListener('pagehide', performSilentAutoSaveOnClose);
            window.addEventListener('visibilitychange', function() {
                if (document.visibilityState === 'hidden') {
                    performSilentAutoSaveOnClose();
                }
            });
        }

        window.onUpdateClick = function(forceReload) {
            if (window.commitActiveEdit) window.commitActiveEdit();
            var sZid = getSessionZid();
            var isForce = (forceReload === true);
            if (isForce || !sZid || typeof fetch === 'undefined') {
                if (window.onSessionReload) window.onSessionReload();
                else if (window.location && window.location.reload) window.location.reload();
                return;
            }
            var updateBtn = (document.getElementById('kw-btn-update'));
            setButtonLoading(updateBtn, true, "Updating...", "Update");

            var tok = getApiToken();
            var headers = { 'Accept': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var postHeaders = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
            if (tok) postHeaders['X-API-Token'] = tok;

            // Check if sentence translation or any lemma translation is missing or in error/skeleton state
            var tc = document.getElementById('translation-container');
            var tcText = tc ? (tc.textContent || tc.innerText || "").trim() : "";
            var needsRetext = false;
            if (!tcText || tcText === 'Loading' || tcText === 'Loading translation' || tcText === 'Processed' || tcText.indexOf('...') !== -1 || (tc && (tc.classList.contains('skeleton-loader') ||  (tc.querySelector('.skeleton-loader'))))) {
                needsRetext = true;
            }
            if (window.AppState && (!window.AppState.translatedText || window.AppState.translatedText.trim() === '')) {
                needsRetext = true;
            }

            var untranslatedRowIds = [];
            var allRows = (document.querySelectorAll('#lemma-table tbody tr[data-row-id]'));
            for (var r = 0; r < allRows.length; r++) {
                var rowEl = allRows[r];
                var rId = rowEl.getAttribute('data-row-id') || rowEl.getAttribute('data-token-order');
                var transTd = (rowEl.querySelector('td[data-col="WordDestination"]')) ||  (rowEl.querySelector('.col-translation'));
                if (!transTd) {
                    var tds = rowEl.getElementsByTagName('td');
                    if (tds.length >= 3) transTd = tds[2];
                }
                var transCell = transTd ? ( (transTd.querySelector('.scrollable-cell')) || transTd) : null;
                var transContent = transCell ? (transCell.textContent || transCell.innerText || "").trim() : "";
                if (!transContent || transContent === 'Loading' || transContent.indexOf('...') !== -1 || (transCell && (transCell.classList.contains('skeleton-loader') ||  (transCell.querySelector('.skeleton-loader'))))) {
                    if (rId !== null && rId !== undefined) {
                        untranslatedRowIds.push(rId);
                    }
                }
            }

            if (needsRetext) {
                var retextBody = { session_zid: sZid, language: getSessionLang() };
                if (tok) retextBody.token = tok;
                fetch('/session/retext', {
                    method: 'POST',
                    headers: postHeaders,
                    body: JSON.stringify(retextBody)
                }).catch(function(e) { return null; });
            }
            if (untranslatedRowIds.length > 0) {
                var rewordBody = { session_zid: sZid, row_ids: untranslatedRowIds, language: getSessionLang() };
                if (tok) rewordBody.token = tok;
                fetch('/session/reword', {
                    method: 'POST',
                    headers: postHeaders,
                    body: JSON.stringify(rewordBody)
                }).catch(function(e) { return null; });
            }

            var proceedWithStatus = function() {
                lastManualStatusAt = Date.now();
                readSessionStatus(sZid, { method: 'GET', headers: headers })
                    .then(function(resObj) {
                        setButtonLoading(updateBtn, false, "", "Update");
                        var payload = (resObj && resObj.data && resObj.data.data) ? resObj.data.data : ((resObj && resObj.data) ? resObj.data : resObj);
                        var hasRows = payload && payload.rows && typeof payload.rows === 'object' && Object.keys(payload.rows).length > 0;
                        var hasTrans = payload && (payload.translatedText || payload.translated_text);
                        var hasSentences = payload && payload.sentences && payload.sentences.length > 0;
                        var isFinished = payload && (payload.status === 'ok' || payload.status === 'success' || payload.stage === 'finished');
                        if (resObj.ok && payload && (hasRows || hasTrans || hasSentences) && window.receiveUpdate) {
                            window.receiveUpdate(payload);
                            window.showToast("Session updated", "success");
                        } else if (resObj.ok && isFinished) {
                            window.showToast("Session updated", "success");
                        } else {
                            if (window.onSessionReload) window.onSessionReload();
                            else if (window.location && window.location.reload) window.location.reload();
                        }
                        if (needsRetext || untranslatedRowIds.length > 0 || (payload && (payload.is_finished === false || payload.stage === 'translating'))) {
                            if (window.initWatchdog) window.initWatchdog();
                            else if (window.startPolling) window.startPolling();
                        }
                    })
                    .catch(function(err) {
                        setButtonLoading(updateBtn, false, "", "Update");
                        if (window.onSessionReload) window.onSessionReload();
                        else if (window.location && window.location.reload) window.location.reload();
                    }).finally(function() {
                        lastManualStatusAt = Date.now();
                    });
            };

            // Provider recovery can remain pending; status and its watchdog must stay usable.
            proceedWithStatus();
        };

        window.onRetextClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            if (typeof fetch === 'undefined') {
                window.showToast("Retext unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing.", "error");
                return;
            }
            var sLang = getSessionLang();
            if (!sLang) {
                window.showToast("Session language missing.", "error");
                return;
            }
            var retextBtn = (document.getElementById('kw-btn-retext'));
            setButtonLoading(retextBtn, true, "Retexting...", "Re-text");

            window.showToast("Retexting session...", "info");
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                language: getSessionLang()
            };
            if (tok) bodyPayload.token = tok;

            fetch('/session/retext', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                setButtonLoading(retextBtn, false, "", "Re-text");
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success' || (resObj.data.data && (resObj.data.data.ok || resObj.data.data.status === 'success')) || resObj.data.retext_started)) {
                    var payload = (resObj.data && resObj.data.data) ? resObj.data.data : resObj.data;
                    if (!payload || !payload.sentences || typeof payload.sentences !== 'object' || Array.isArray(payload.sentences)) {
                        window.showToast("Retext failed: invalid sentence translation payload from server", "error");
                        return;
                    }
                    window.showToast("Retext completed", "success");
                    var tText = payload ? (payload.translatedText || payload.translated_text) : null;
                    var textProv = (payload && (payload.text_provenance || payload.textProvenance || payload.provenance)) || null;
                    var activeTextProv = (payload && (payload.active_text_provider || payload.active_provider)) || null;

                    if (window.AppState) {
                        if (textProv) {
                            window.AppState.textProvenance = textProv;
                            window.AppState.text_provenance = textProv;
                        }
                        if (activeTextProv) {
                            window.AppState.activeTextProvider = activeTextProv;
                            window.AppState.active_text_provider = activeTextProv;
                        }
                    }
                    if (typeof window.updateActiveProvider === 'function' && activeTextProv) {
                        window.updateActiveProvider('text', activeTextProv);
                    }

                    if (tText) {
                        var tc = document.getElementById('translation-container');
                        if (tc) {
                            tc.classList.remove('skeleton-loader');
                            tc.removeAttribute('data-pending');
                        }
                        if (window.AppState) window.AppState.translatedText = tText;

                        var wCards = (window.WorkspaceTabs && typeof window.WorkspaceTabs.getCards === 'function')
                            ? window.WorkspaceTabs.getCards()
                            : (window._kwSentenceCards || null);
                        if (wCards && wCards.length > 0) {
                            var pSents = payload.sentences;
                            var isZeroBasedSents = (pSents[0] !== undefined || pSents['0'] !== undefined);

                            for (var c = 0; c < wCards.length; c++) {
                                var cardSentIdx = wCards[c].sentence_idx;
                                if (cardSentIdx === 0) {
                                    wCards[c].translated_text = tText;
                                } else if (cardSentIdx > 0) {
                                    var sKey = isZeroBasedSents ? (cardSentIdx - 1) : cardSentIdx;
                                    var sentTrans = pSents[sKey] !== undefined ? pSents[sKey] : pSents[String(sKey)];
                                    if (sentTrans && typeof sentTrans === 'string') {
                                        wCards[c].translated_text = sentTrans;
                                    }
                                }
                            }
                            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.updateSentences === 'function') {
                                window.WorkspaceTabs.updateSentences(payload.sentences);
                            } else if (window.WorkspaceTabs && typeof window.WorkspaceTabs.updateActiveTabTranslation === 'function') {
                                window.WorkspaceTabs.updateActiveTabTranslation();
                            }
                        } else {
                            if (tc) {
                                tc.innerHTML = tText;
                            }
                        }
                    }
                    if (window.receiveUpdate && tText) {
                        var retextUpdate = {
                            stage: "translated_text",
                            status: "success",
                            translatedText: tText,
                            translated_text: tText,
                            sentences: payload.sentences,
                            rows: (payload && payload.rows) ? payload.rows : null,
                            text_provenance: textProv,
                            textProvenance: textProv,
                            active_text_provider: activeTextProv,
                            active_provider: activeTextProv
                        };
                        window.receiveUpdate(retextUpdate);
                    } else if (window.onUpdateClick) {
                        window.onUpdateClick();
                    }

                    if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveSentenceIdx === 'function' && window.WorkspaceTabs.getActiveSentenceIdx() > 0) {
                        if (typeof window.WorkspaceTabs.updateActiveTabTranslation === 'function') {
                            window.WorkspaceTabs.updateActiveTabTranslation();
                        }
                    }
                } else {
                    window.showToast("Retext failed: " + formatErrorWithTrace(resObj, "Server error"), "error");
                }
            })
            .catch(function(err) {
                setButtonLoading(retextBtn, false, "", "Re-text");
                window.showToast("Retext error: " + formatErrorWithTrace(err, "Network error"), "error");
            });
        };

        window.onRewordClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            var rows = [];
            var tokenOrders = [];
            var targetCoordinates = [];

            var tableSelectedRows = [];
            if (typeof tableRows !== 'undefined' && tableRows) {
                for (var i = 0; i < tableRows.length; i++) {
                    var tr = tableRows[i];
                    var trId = String(tr.getAttribute('data-row-id'));
                    var isTrSel = tr.classList.contains('selected') || tr.classList.contains('kw-row-selected') || tr.getAttribute('data-selected') === '1' || (typeof selectedRowIdsMap !== 'undefined' && selectedRowIdsMap.hasOwnProperty(trId));
                    if (isTrSel) {
                        var grp = getRowGroupIds(tr);
                        for (var p = 0; p < grp.length; p++) {
                            var pInt = parseInt(String(grp[p]), 10);
                            if (!isNaN(pInt) && tableSelectedRows.indexOf(pInt) === -1) tableSelectedRows.push(pInt);
                        }
                    }
                }
            }
            if (tableSelectedRows.length === 0) {
                var selArr = (typeof getSelectedRowsArray === 'function') ? getSelectedRowsArray() : [];
                for (var s = 0; s < selArr.length; s++) {
                    var sInt = parseInt(String(selArr[s]), 10);
                    if (!isNaN(sInt) && tableSelectedRows.indexOf(sInt) === -1) tableSelectedRows.push(sInt);
                }
            }

            var hasActiveTokens = (window.AppState && window.AppState.activeTokenSelections && window.AppState.activeTokenSelections.length > 0);
            var activeTokenRows = [];
            var activeHasMatchingSelection = false;
            if (hasActiveTokens) {
                for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                    var item = window.AppState.activeTokenSelections[a];
                    var itemRowIds = (item.atomic_row_ids && item.atomic_row_ids.length > 0)
                        ? item.atomic_row_ids
                        : ((item.row_ids && item.row_ids.length > 0)
                            ? item.row_ids
                            : ((item.atomic_id !== undefined && item.atomic_id !== null) ? [item.atomic_id] : []));
                    if (itemRowIds.length === 0 && item.visual_idx !== undefined && item.visual_idx !== null) {
                        var td = findTokenDataByVisualIdx(Number(item.visual_idx));
                        itemRowIds = td ? (td.atomic_row_ids || td.row_ids || []) : [];
                    }
                    for (var rIdx = 0; rIdx < itemRowIds.length; rIdx++) {
                        var rInt = parseInt(String(itemRowIds[rIdx]), 10);
                        if (!isNaN(rInt)) {
                            if (activeTokenRows.indexOf(rInt) === -1) activeTokenRows.push(rInt);
                            if (typeof selectedRowIdsMap !== 'undefined' && selectedRowIdsMap.hasOwnProperty(String(rInt))) {
                                activeHasMatchingSelection = true;
                            }
                        }
                    }
                }
            }

            if (hasActiveTokens && activeTokenRows.length > 0 && activeHasMatchingSelection) {
                // 1. Token-scoped (targeted) selection
                for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                    var item = window.AppState.activeTokenSelections[a];
                    if (item.visual_idx !== undefined && item.visual_idx !== null) {
                        targetCoordinates.push({
                            visual_idx: item.visual_idx,
                            sentence_idx: item.sentence_idx,
                            token_order: item.token_order,
                            atomic_id: item.atomic_id,
                            lemma: item.lemma
                        });
                    }
                    if (item.token_order !== undefined && item.token_order !== null && String(item.token_order).trim() !== '') {
                        if (tokenOrders.indexOf(item.token_order) === -1) {
                            tokenOrders.push(item.token_order);
                        }
                    }
                }
                rows = activeTokenRows;
            } else {
                // 2. Row-scoped selection (from #lemma-table directly): unroll data-all-row-ids
                if (typeof tableRows !== 'undefined' && tableRows) {
                    for (var i = 0; i < tableRows.length; i++) {
                        var tr = tableRows[i];
                        var trId = String(tr.getAttribute('data-row-id'));
                        var isTrSel = tr.classList.contains('selected') || tr.classList.contains('kw-row-selected') || tr.getAttribute('data-selected') === '1' || (typeof selectedRowIdsMap !== 'undefined' && selectedRowIdsMap.hasOwnProperty(trId));
                        if (isTrSel) {
                            var grp = getRowGroupIds(tr);
                            for (var p = 0; p < grp.length; p++) {
                                var pInt = parseInt(String(grp[p]), 10);
                                if (!isNaN(pInt) && rows.indexOf(pInt) === -1) rows.push(pInt);
                            }
                        }
                    }
                }
                if (rows.length === 0) {
                    rows = getSelectedRowsArray();
                }
                tokenOrders = (typeof getSelectedTokenOrdersArray === 'function') ? getSelectedTokenOrdersArray() : [];
            }
            if (!rows.length && !tokenOrders.length) {
                window.showToast("Please select rows to re-word.", "warning");
                return;
            }
            if (typeof fetch === 'undefined') {
                window.showToast("Reword unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing.", "error");
                return;
            }
            var sLang = getSessionLang();
            if (!sLang) {
                window.showToast("Session language missing.", "error");
                return;
            }
            var rewordBtn = (document.getElementById('kw-btn-reword'));
            setButtonLoading(rewordBtn, true, "Rewording...", "Re-word");

            window.showToast("Re-wording " + (tokenOrders.length || rows.length) + " rows...", "info");
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var actCard = (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function')
                ? window.WorkspaceTabs.getActiveCard()
                : null;
            var curSentIdx = (actCard && actCard.sentence_idx !== undefined && actCard.sentence_idx !== null)
                ? actCard.sentence_idx
                : 0;
            var bodyPayload = {
                session_zid: sZid,
                row_ids: rows,
                token_orders: tokenOrders,
                language: getSessionLang(),
                sentence_idx: curSentIdx
            };
            if (targetCoordinates.length > 0) {
                bodyPayload.target_coordinates = targetCoordinates;
                bodyPayload.visual_indices = targetCoordinates.map(function(c) { return c.visual_idx; });
            }
            if (tok) bodyPayload.token = tok;

            fetch('/session/reword', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                setButtonLoading(rewordBtn, false, "", "Re-word");
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success' || (resObj.data.data && (resObj.data.data.ok || resObj.data.data.status === 'success')) || resObj.data.reprocess_started)) {
                    window.showToast("Re-word completed for " + rows.length + " rows", "success");
                    var payload = (resObj.data && resObj.data.data) ? resObj.data.data : resObj.data;
                    if (window.receiveUpdate && payload && payload.rows) {
                        window.receiveUpdate(payload);
                    } else if (window.onUpdateClick) {
                        window.onUpdateClick();
                    }
                } else {
                    window.showToast("Re-word failed: " + formatErrorWithTrace(resObj, "Server error"), "error");
                }
            })
            .catch(function(err) {
                setButtonLoading(rewordBtn, false, "", "Re-word");
                window.showToast("Re-word error: " + formatErrorWithTrace(err, "Network error"), "error");
            });
        };
        window.onSelectedClick = window.onRewordClick;

        window.onSendToAnkiClick = function() {
            if (window.commitActiveEdit) window.commitActiveEdit();
            var rows = [];
            var tokenOrders = [];

            var tableSelectedRows = [];
            if (typeof tableRows !== 'undefined' && tableRows) {
                for (var i = 0; i < tableRows.length; i++) {
                    var tr = tableRows[i];
                    var trId = String(tr.getAttribute('data-row-id'));
                    var isTrSel = tr.classList.contains('selected') || tr.classList.contains('kw-row-selected') || tr.getAttribute('data-selected') === '1' || (typeof selectedRowIdsMap !== 'undefined' && selectedRowIdsMap.hasOwnProperty(trId));
                    if (isTrSel) {
                        var grp = getRowGroupIds(tr);
                        for (var p = 0; p < grp.length; p++) {
                            var pInt = parseInt(String(grp[p]), 10);
                            if (!isNaN(pInt) && tableSelectedRows.indexOf(pInt) === -1) tableSelectedRows.push(pInt);
                        }
                    }
                }
            }
            if (tableSelectedRows.length === 0) {
                var selArr = (typeof getSelectedRowsArray === 'function') ? getSelectedRowsArray() : [];
                for (var s = 0; s < selArr.length; s++) {
                    var sInt = parseInt(String(selArr[s]), 10);
                    if (!isNaN(sInt) && tableSelectedRows.indexOf(sInt) === -1) tableSelectedRows.push(sInt);
                }
            }

            var hasActiveTokens = (window.AppState && window.AppState.activeTokenSelections && window.AppState.activeTokenSelections.length > 0);
            var activeTokenRows = [];
            var activeHasMatchingSelection = false;
            if (hasActiveTokens) {
                for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                    var item = window.AppState.activeTokenSelections[a];
                    var itemRowIds = (item.atomic_row_ids && item.atomic_row_ids.length > 0)
                        ? item.atomic_row_ids
                        : ((item.row_ids && item.row_ids.length > 0)
                            ? item.row_ids
                            : ((item.atomic_id !== undefined && item.atomic_id !== null) ? [item.atomic_id] : []));
                    if (itemRowIds.length === 0 && item.visual_idx !== undefined && item.visual_idx !== null) {
                        var td = findTokenDataByVisualIdx(Number(item.visual_idx));
                        itemRowIds = td ? (td.atomic_row_ids || td.row_ids || []) : [];
                    }
                    for (var rIdx = 0; rIdx < itemRowIds.length; rIdx++) {
                        var rInt = parseInt(String(itemRowIds[rIdx]), 10);
                        if (!isNaN(rInt)) {
                            if (activeTokenRows.indexOf(rInt) === -1) activeTokenRows.push(rInt);
                            if (typeof selectedRowIdsMap !== 'undefined' && selectedRowIdsMap.hasOwnProperty(String(rInt))) {
                                activeHasMatchingSelection = true;
                            }
                        }
                    }
                }
            }

            if (hasActiveTokens && activeTokenRows.length > 0 && activeHasMatchingSelection) {
                // 1. Token-scoped (targeted) selection
                for (var a = 0; a < window.AppState.activeTokenSelections.length; a++) {
                    var item = window.AppState.activeTokenSelections[a];
                    if (item.token_order !== undefined && item.token_order !== null && String(item.token_order).trim() !== '') {
                        if (tokenOrders.indexOf(item.token_order) === -1) {
                            tokenOrders.push(item.token_order);
                        }
                    }
                }
                rows = activeTokenRows;
            } else {
                // 2. Row-scoped selection (from #lemma-table directly): unroll data-all-row-ids
                if (typeof tableRows !== 'undefined' && tableRows) {
                    for (var i = 0; i < tableRows.length; i++) {
                        var tr = tableRows[i];
                        var trId = String(tr.getAttribute('data-row-id'));
                        var isTrSel = tr.classList.contains('selected') || tr.classList.contains('kw-row-selected') || tr.getAttribute('data-selected') === '1' || (typeof selectedRowIdsMap !== 'undefined' && selectedRowIdsMap.hasOwnProperty(trId));
                        if (isTrSel) {
                            var grp = getRowGroupIds(tr);
                            for (var p = 0; p < grp.length; p++) {
                                var pInt = parseInt(String(grp[p]), 10);
                                if (!isNaN(pInt) && rows.indexOf(pInt) === -1) rows.push(pInt);
                            }
                        }
                    }
                }
                if (rows.length === 0) {
                    rows = getSelectedRowsArray();
                }
                tokenOrders = (typeof getSelectedTokenOrdersArray === 'function') ? getSelectedTokenOrdersArray() : [];
            }

            // Card-scoped filtering in container mode
            if (window.WorkspaceTabs && typeof window.WorkspaceTabs.getActiveCard === 'function') {
                var activeCard = window.WorkspaceTabs.getActiveCard();
                var activeSentIdx = (activeCard && activeCard.sentence_idx !== undefined) ? activeCard.sentence_idx : 0;
                if (activeSentIdx > 0 && activeCard && activeCard.words) {
                    var cardRowIds = {};
                    var cardTokenOrders = {};
                    for (var w = 0; w < activeCard.words.length; w++) {
                        var wObj = activeCard.words[w];
                        cardRowIds[String(wObj.row_id)] = true;
                        if (wObj.all_row_ids) {
                            for (var a = 0; a < wObj.all_row_ids.length; a++) {
                                cardRowIds[String(wObj.all_row_ids[a])] = true;
                            }
                        }
                        if (wObj.token_order !== undefined && wObj.token_order !== null) {
                            cardTokenOrders[String(wObj.token_order)] = true;
                        }
                    }
                    rows = rows.filter(function(rId) { return cardRowIds.hasOwnProperty(String(rId)); });
                    tokenOrders = tokenOrders.filter(function(tOrd) { return cardTokenOrders.hasOwnProperty(String(tOrd)); });
                }
            }

            if (!rows.length && !tokenOrders.length) {
                window.showToast("Please select rows to export.", "warning");
                return;
            }
            if (typeof fetch === 'undefined') {
                window.showToast("Export unavailable in this environment.", "warning");
                return;
            }
            var sZid = getSessionZid();
            if (!sZid) {
                window.showToast("Session ZID missing.", "error");
                return;
            }
            var sLang = getSessionLang();
            if (!sLang) {
                window.showToast("Session language missing.", "error");
                return;
            }
            var exportBtn = (document.getElementById('kw-btn-export'));
            setButtonLoading(exportBtn, true, "Sending...", "Send to Anki");

            window.showToast("Exporting to Anki...", "info");
            var tok = getApiToken();
            var headers = { 'Content-Type': 'application/json' };
            if (tok) headers['X-API-Token'] = tok;
            var bodyPayload = {
                session_zid: sZid,
                row_ids: rows,
                selected_row_ids: rows,
                token_orders: tokenOrders,
                language: getSessionLang()
            };
            if (tok) bodyPayload.token = tok;

            fetch('/session/export', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(bodyPayload)
            })
            .then(function(res) {
                return res.json().then(function(data) { return { status: res.status, ok: res.ok, data: data }; });
            })
            .then(function(resObj) {
                setButtonLoading(exportBtn, false, "", "Send to Anki");
                if (resObj.ok && (resObj.data.ok || resObj.data.status === 'success' || resObj.data.import_complete || resObj.data.import_started)) {
                    var cardCount = rows.length;
                    var cardWord = cardCount === 1 ? "card" : "cards";
                    window.showToast("✓ " + cardCount + " " + cardWord + " exported to Anki", "success");
                } else {
                    window.showToast("Export failed: " + formatErrorWithTrace(resObj, "Server error"), "error");
                }
            })
            .catch(function(err) {
                setButtonLoading(exportBtn, false, "", "Send to Anki");
                window.showToast("Export error: " + formatErrorWithTrace(err, "Network error"), "error");
            });
        };

        window.onHandToolClick = function() {
            var newMode = !window.__persistentSelectableMode;
            window.setSelectableTextMode(newMode, newMode);
            var handBtn = (document.getElementById('kw-btn-hand-tool'));
            if (handBtn) {
                if (newMode) handBtn.classList.add('active');
                else handBtn.classList.remove('active');
            }
            window.showToast(newMode ? "Text selection mode active" : "Hand tool / Table interaction active", "info", 1500);
        };

        window.onDeleteClick = function() {
            var rows = getSelectedRowsArray();
            if (!rows.length) {
                window.showToast("Please select rows to delete.", "warning");
                return;
            }
            window.deleteSelectedRows();
            updateToolbarState();
            window.showToast("Deleted " + rows.length + " rows (Ctrl+Z to undo)", "info", 2000);
        };

        var LANGUAGE_NAME_MAP = {
            "en": "English",
            "de": "German",
            "ru": "Russian",
            "fr": "French",
            "es": "Spanish",
            "it": "Italian",
            "zh": "Chinese",
            "ja": "Japanese",
            "pt": "Portuguese",
            "nl": "Dutch",
            "pl": "Polish",
            "uk": "Ukrainian"
        };

        /** @param {string} code */
        function getLanguageName(code) {
            if (!code) return "";
            return LANGUAGE_NAME_MAP[code.toLowerCase()] || code.toUpperCase();
        }

        function getTheme() {
            if (document.body && document.body.classList) {
                if (document.body.classList.contains('theme-light')) return 'light';
                if (document.body.classList.contains('theme-white')) return 'white';
            }
            return 'dark';
        }

        function getTextMode() {
            var el = document.getElementById('text-mode');
            return el ? (el.textContent || el.innerText || 'single').trim() : 'single';
        }

        window.showLanguageVerificationModal = function(info) {
            info = info || {};
            var detCode = info.detected_language || "";
            var expCode = info.expected_language || getSessionLang() || "";
            var detName = info.detected_name || getLanguageName(detCode) || (detCode ? detCode.toUpperCase() : "");
            var expName = info.expected_name || getLanguageName(expCode) || (expCode ? expCode.toUpperCase() : "");
            var detCodeDisplay = info.detected_name ? detCode : detCode.toUpperCase();
            var expCodeDisplay = info.expected_name ? expCode : expCode.toUpperCase();
            var detLabel = detName + (detCodeDisplay ? " (" + detCodeDisplay + ")" : "");
            var expLabel = expName + (expCodeDisplay ? " (" + expCodeDisplay + ")" : "");
            var promptMsg = "The text appears to be " + detLabel + ", but the active profile is " + expLabel + ".\n\nSwitch language to " + detName + "?";

            var modal = document.getElementById('kw-lang-modal');
            var bodyEl = document.getElementById('kw-lang-modal-body');
            if (bodyEl) bodyEl.textContent = promptMsg;
            if (modal) {
                modal.style.display = 'flex';
                window.__langMismatchInfo = info;
                var yesBtn = (document.getElementById('kw-btn-lang-yes'));
                if (yesBtn) yesBtn.focus();
            }
        };

        window.hideLanguageVerificationModal = function() {
            var modal = document.getElementById('kw-lang-modal');
            if (modal) modal.style.display = 'none';
        };

        /** @param {boolean} loading */
        function setLangModalLoading(loading) {
            var yesBtn = (document.getElementById('kw-btn-lang-yes'));
            var noBtn = (document.getElementById('kw-btn-lang-no'));
            var cancelBtn = (document.getElementById('kw-btn-lang-cancel'));
            if (yesBtn) yesBtn.disabled = !!loading;
            if (noBtn) noBtn.disabled = !!loading;
            if (cancelBtn) cancelBtn.disabled = !!loading;
        }

        /** @param {string|Array<string|ChildSession>} children */
        function extractChildSessions(children) {
             var results = [];
            if (!children) return results;
            if (typeof children === 'string') {
                try { children = JSON.parse(children); } catch(e) { console.error('[extractChildSessions] Failed to parse children JSON:', e); return results; }
            }
            if (!children || !Array.isArray(children)) return results;
            var curSeq = null;
            for (var i = 0; i < children.length; i++) {
                var item = children[i];
                if (typeof item === 'string') {
                    if (item === '--seq-num' && i + 1 < children.length) {
                        var sequenceArgument = children[i + 1];
                        if (typeof sequenceArgument !== 'string') throw new Error('Invalid child --seq-num argument');
                        curSeq = sequenceArgument;
                        i++;
                    } else if (item === '--restore' && i + 1 < children.length) {
                        var curTsv = children[i + 1];
                        i++;
                        if (typeof curTsv === 'string' && curTsv) {
                            var zidMatch = curTsv.match(/(\d{14})/) || curTsv.match(/\d{14}/);
                            var childZid = zidMatch ? (zidMatch[1] || zidMatch[0]) : null;
                            if (childZid) {
                                results.push({ zid: childZid, seq_num: curSeq || '1' });
                            }
                            curSeq = null;
                        }
                    } else {
                        var zidMatch = item.match(/(\d{14})/) || item.match(/\d{14}/);
                        if (zidMatch) {
                            results.push({ zid: zidMatch[1] || zidMatch[0], seq_num: '1' });
                        }
                    }
                } else if (typeof item === 'object' && item !== null) {
                    var z = item.zid || item.session_zid;
                    if (z) {
                        results.push({ zid: z, seq_num: item.seq_num || '1' });
                    }
                }
            }
            return results;
        }

        function getWebTabMode() {
            return getDeliveryMode() === 'container' ? 'container' : 'tabs';
        }

        /** @param {ChildSession[]} children */
        function spawnChildTabs(children) {
            if (getDeliveryMode() === 'container') {
                return;
            }
            var childSessions = extractChildSessions(children);
            if (!childSessions || childSessions.length === 0) return;
            var tok = getApiToken();
            var tokenQuery = tok ? '&token=' + encodeURIComponent(tok) : '';
             var urls = [];
            for (var i = 0; i < childSessions.length; i++) {
                var c = childSessions[i];
                var childUrl = '/session/render?session_zid=' + encodeURIComponent(c.zid) + '&seq_num=' + encodeURIComponent(c.seq_num) + '&bypass_lang_check=true' + tokenQuery;
                urls.push(childUrl);
            }
            if (urls.length === 0) return;

            var fallbackOpen = function() {
                for (var j = 0; j < urls.length; j++) {
                    try {
                        window.open(urls[j], '_blank');
                    } catch(e) {
                        console.error("Failed spawning child tab", e);
                    }
                }
            };

            if (typeof fetch !== 'undefined') {
                var headers = { 'Content-Type': 'application/json' };
                if (tok) headers['X-API-Token'] = tok;
                var bodyPayload = { urls: urls, children: childSessions };
                if (tok) bodyPayload.token = tok;
                fetch('/api/v1/spawn-tabs', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(bodyPayload)
                })
                .then(function(res) {
                    if (!res.ok) {
                        fallbackOpen();
                    }
                })
                .catch(function(e) {
                    console.warn("Server spawn-tabs failed, falling back to window.open", e);
                    fallbackOpen();
                });
            } else {
                fallbackOpen();
            }
        }

        /* WorkspaceTabs component dynamically bundled from web/js/components/WorkspaceTabs.js */
        /* WorkspaceTabs loaded from WorkspaceTabs.js */

        window.onLangYes = function() {
            var yesBtn = (document.getElementById('kw-btn-lang-yes'));
            if (yesBtn && yesBtn.disabled) return;

            var info = window.__langMismatchInfo || {};
            var detLang = info.detected_language;
            if (!detLang) {
                window.hideLanguageVerificationModal();
                return;
            }
            var sZid = getSessionZid() || info.session_zid || "";
            var srcContainer = document.getElementById('source-container');
            var srcText = info.text || info.source_text || (srcContainer ? (srcContainer.textContent || srcContainer.innerText || "") : "");
            var detName = info.detected_name || getLanguageName(detLang) || detLang;
            var effMode = getTextMode();
            var tok = getApiToken();
            var tokenQuery = tok ? '&token=' + encodeURIComponent(tok) : '';

            var cardTsv = "";
            var tsvPathEl = document.getElementById('tsv-path');
            if (tsvPathEl) {
                var fullPath = (tsvPathEl.textContent || tsvPathEl.innerText || '').trim();
                cardTsv = fullPath.split(/[/\\\\]/).pop() || '';
                if (cardTsv && sZid && detLang) {
                    cardTsv = cardTsv.replace(new RegExp('\\.[a-z]{2}\\.tsv$', 'i'), '.' + detLang + '.tsv');
                }
            }
            if (!cardTsv && sZid && detLang) {
                cardTsv = sZid + '.' + detLang + '.tsv';
            }
            document.title = cardTsv ? ("Kardenwort - " + detLang + " (" + effMode + ") - " + cardTsv + " - Ready") : ("Kardenwort - " + detLang + " (" + effMode + ") - Ready");
            window.showToast("Switching language to " + detName + "...", "info");
            setLangModalLoading(true);

            if (typeof fetch !== 'undefined') {
                var headers = { 'Content-Type': 'application/json' };
                if (tok) headers['X-API-Token'] = tok;

                var langPayload = { language: detLang };
                if (tok) langPayload.token = tok;

                fetch('/api/v1/set-language', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(langPayload)
                }).catch(function(e) {
                    console.warn("Failed to synchronize language with controller:", e);
                });

                var renderPayload = {
                    session_zid: sZid,
                    zid: sZid,
                    language: detLang,
                    text: srcText,
                    bypass_lang_check: true,
                    theme: getTheme(),
                    text_mode: effMode
                };
                if (tok) renderPayload.token = tok;

                fetch('/api/v1/render', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(renderPayload)
                })
                .then(function(res) {
                    return res.json().then(function(data) { return { ok: res.ok, status: res.status, data: data }; });
                })
                .then(function(resObj) {
                    setLangModalLoading(false);
                    window.hideLanguageVerificationModal();
                    if (resObj.ok && resObj.data) {
                        var childrenList = resObj.data.children || (resObj.data.data && resObj.data.data.children) || [];
                        if (childrenList && childrenList.length > 0) {
                            spawnChildTabs(childrenList);
                        }
                        var html = resObj.data.html || (resObj.data.html_b64 ? decodeURIComponent(escape(atob(resObj.data.html_b64))) : null);
                        if (html) {
                            document.open();
                            document.write(html);
                            document.close();
                            return;
                        }
                    }
                    window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(detLang || '') + '&bypass_lang_check=true' + tokenQuery;
                })
                .catch(function(err) {
                    setLangModalLoading(false);
                    window.showToast("Language switch failed: " + (err instanceof Error ? err.message : String(err)), "error");
                });
            } else {
                setLangModalLoading(false);
                window.hideLanguageVerificationModal();
                window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(detLang || '') + '&bypass_lang_check=true' + tokenQuery;
            }
        };

        window.onLangNo = function() {
            var noBtn = (document.getElementById('kw-btn-lang-no'));
            if (noBtn && noBtn.disabled) return;
            if (typeof window.hideLanguageVerificationModal === 'function') {
                window.hideLanguageVerificationModal();
            }

            var info = window.__langMismatchInfo || {};
            var expLang = info.expected_language || getSessionLang() || "en";
            var sZid = getSessionZid() || info.session_zid || "";
            var srcContainer = document.getElementById('source-container');
            var srcText = info.text || info.source_text || (srcContainer ? (srcContainer.textContent || srcContainer.innerText || "") : "");
            var expName = info.expected_name || getLanguageName(expLang) || expLang;
            var effMode = getTextMode();
            var tok = getApiToken();
            var tokenQuery = tok ? '&token=' + encodeURIComponent(tok) : '';

            var cardTsv = "";
            var tsvPathEl = document.getElementById('tsv-path');
            if (tsvPathEl) {
                var fullPath = (tsvPathEl.textContent || tsvPathEl.innerText || '').trim();
                cardTsv = fullPath.split(/[/\\\\]/).pop() || '';
            }
            if (!cardTsv && sZid && expLang) {
                cardTsv = sZid + '.' + expLang + '.tsv';
            }
            document.title = cardTsv ? ("Kardenwort - " + expLang + " (" + effMode + ") - " + cardTsv + " - Ready") : ("Kardenwort - " + expLang + " (" + effMode + ") - Ready");
            window.showToast("Processing in " + expName + "...", "info");
            setLangModalLoading(true);

            if (typeof fetch !== 'undefined') {
                var headers = { 'Content-Type': 'application/json' };
                if (tok) headers['X-API-Token'] = tok;

                var renderPayload = {
                    session_zid: sZid,
                    zid: sZid,
                    language: expLang,
                    text: srcText,
                    bypass_lang_check: true,
                    theme: getTheme(),
                    text_mode: effMode
                };
                if (tok) renderPayload.token = tok;

                fetch('/api/v1/render', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(renderPayload)
                })
                .then(function(res) {
                    return res.json().then(function(data) { return { ok: res.ok, status: res.status, data: data }; });
                })
                .then(function(resObj) {
                    setLangModalLoading(false);
                    window.hideLanguageVerificationModal();
                    if (resObj.ok && resObj.data) {
                        var childrenList = resObj.data.children || (resObj.data.data && resObj.data.data.children) || [];
                        if (childrenList && childrenList.length > 0) {
                            spawnChildTabs(childrenList);
                        }
                        var html = resObj.data.html || (resObj.data.html_b64 ? decodeURIComponent(escape(atob(resObj.data.html_b64))) : null);
                        if (html) {
                            document.open();
                            document.write(html);
                            document.close();
                            return;
                        }
                        return;
                    }
                    if (!resObj.ok) {
                        window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(expLang) + '&bypass_lang_check=true' + tokenQuery;
                    }
                })
                .catch(function(err) {
                    setLangModalLoading(false);
                    window.showToast("Render failed: " + (err instanceof Error ? err.message : String(err)), "error");
                });
            } else {
                setLangModalLoading(false);
                window.hideLanguageVerificationModal();
                window.location.href = '/session/render?session_zid=' + encodeURIComponent(sZid) + '&language=' + encodeURIComponent(expLang) + '&bypass_lang_check=true' + tokenQuery;
            }
        };

        window.onLangCancel = function() {
            window.hideLanguageVerificationModal();
            window.showToast("Language verification cancelled.", "info");
        };

        /** @param {boolean} active */
        function setHighlightSameLemma(active) {
            if (window.AppStore) window.AppStore.dispatch({type: 'TOGGLE_HIGHLIGHT_SAME_LEMMA', payload: !!active});
            if (window.AppState) {
                window.AppState.highlightSameLemma = !!active;
            }
            if (window.__CONFIG__) {
                window.__CONFIG__.highlight_same_lemma = !!active;
            }
            try {
                localStorage.setItem('kw_highlight_same_lemma', String(!!active));
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            var btn = (document.getElementById('kw-btn-same-lemma'));
            if (btn) {
                if (active) {
                    addClass(btn, 'active');
                } else {
                    removeClass(btn, 'active');
                }
            }
            if (typeof updateBidirectionalHighlights === 'function') {
                updateBidirectionalHighlights();
            }
            try {
                fetch('/api/v1/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-API-Token': getApiToken() },
                    body: JSON.stringify({ section: 'rendering', key: 'highlight_same_lemma', value: String(!!active) })
                }).catch(function(err) { console.debug("[Async] Non-fatal operation caught:", err); });
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        }
        window.setHighlightSameLemma = setHighlightSameLemma;

        function toggleHighlightSameLemma() {
            var cur = (window.AppState && window.AppState.highlightSameLemma) ? true : false;
            var next = !cur;
            setHighlightSameLemma(next);
            if (typeof window.showToast === 'function') {
                window.showToast(next ? "Lemma highlighting enabled" : "Lemma highlighting disabled", "info");
            }
        }
        window.toggleHighlightSameLemma = toggleHighlightSameLemma;

        var btnSameLemma = (document.getElementById('kw-btn-same-lemma'));
        if (btnSameLemma) {
            if (window.AppState && window.AppState.highlightSameLemma) {
                addClass(btnSameLemma, 'active');
            } else {
                removeClass(btnSameLemma, 'active');
            }
            if (!btnSameLemma.__kw_bound) {
                btnSameLemma.__kw_bound = true;
                addEvent(btnSameLemma, 'click', function() {
                    toggleHighlightSameLemma();
                });
            }
        }

        /** @param {boolean} active */
        function setFilterSelectedOnly(active) {
            if (window.AppStore) window.AppStore.dispatch({type: 'TOGGLE_FILTER_SELECTED', payload: !!active});
            if (window.AppState) {
                window.AppState.filterSelectedOnly = !!active;
            }
            if (window.__CONFIG__) {
                window.__CONFIG__.filter_selected_only = !!active;
            }
            try {
                localStorage.setItem('kw_filter_selected_only', String(!!active));
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
            var lemmaTable = document.getElementById('lemma-table');
            if (lemmaTable) {
                if (active) {
                    addClass(lemmaTable, 'kw-filter-selected-only');
                } else {
                    removeClass(lemmaTable, 'kw-filter-selected-only');
                }
            }
            var filterBtn = (document.getElementById('kw-btn-filter-selected'));
            if (filterBtn) {
                if (active) {
                    addClass(filterBtn, 'btn-filter-active');
                } else {
                    removeClass(filterBtn, 'btn-filter-active');
                }
            }
            if (typeof updateEmptySelectionState === 'function') {
                updateEmptySelectionState();
            }
            try {
                fetch('/api/v1/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-API-Token': getApiToken() },
                    body: JSON.stringify({ section: 'rendering', key: 'filter_selected_only', value: String(!!active) })
                }).catch(function(err) { console.debug("[Async] Non-fatal operation caught:", err); });
            } catch(e) { console.debug("[Diagnostic] Handled exception:", e); }
        }
        window.setFilterSelectedOnly = setFilterSelectedOnly;

        function toggleFilterSelectedOnly() {
            var cur = (window.AppState && window.AppState.filterSelectedOnly) ? true : false;
            var next = !cur;
            setFilterSelectedOnly(next);
            if (typeof window.showToast === 'function') {
                window.showToast(next ? "Selected rows filter enabled" : "Selected rows filter disabled", "info");
            }
        }
        window.toggleFilterSelectedOnly = toggleFilterSelectedOnly;

        var btnFilterSelected = (document.getElementById('kw-btn-filter-selected'));
        if (btnFilterSelected) {
            if (window.AppState && window.AppState.filterSelectedOnly) {
                addClass(btnFilterSelected, 'btn-filter-active');
                var lemmaTable = document.getElementById('lemma-table');
                if (lemmaTable) {
                    addClass(lemmaTable, 'kw-filter-selected-only');
                }
                if (typeof updateEmptySelectionState === 'function') {
                    updateEmptySelectionState();
                }
            } else {
                removeClass(btnFilterSelected, 'btn-filter-active');
            }
            if (!btnFilterSelected.__kw_bound) {
                btnFilterSelected.__kw_bound = true;
                addEvent(btnFilterSelected, 'click', function() {
                    toggleFilterSelectedOnly();
                });
            }
        }

        addEvent(document, 'click', /** @param {MouseEvent} e */ function(e) {
            e = (e || window.event);
            var target = (e.target || e.srcElement);
            if (target && (target.id === 'kw-btn-empty-clear-filter' || (target.closest && target.closest('#kw-btn-empty-clear-filter')))) {
                if (typeof window.setFilterSelectedOnly === 'function') {
                    window.setFilterSelectedOnly(false);
                }
            }
        });

        if (typeof wireActionToolbar === 'function') {
            wireActionToolbar();
        } else if (typeof window.wireActionToolbar === 'function') {
            window.wireActionToolbar();
        }

        addEvent(document, 'keydown', function(e) {
            e = (e || window.event);
            var target = (e.target || e.srcElement);
            var isInput = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA');

            var langModal = document.getElementById('kw-lang-modal');
            var isModalOpen = langModal && langModal.style.display !== 'none' && !langModal.classList.contains('hidden');
            if (isModalOpen) {
                if (e.key === 'Escape' || e.keyCode === 27) {
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    window.onLangCancel();
                    return false;
                }
                if (e.key === 'Enter' || e.keyCode === 13) {
                    var focused = document.activeElement;
                    if (focused === document.getElementById('kw-btn-lang-no')) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.onLangNo();
                        return false;
                    } else if (focused === document.getElementById('kw-btn-lang-cancel')) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.onLangCancel();
                        return false;
                    } else {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.onLangYes();
                        return false;
                    }
                }
            }

            // Ctrl+1 through Ctrl+9 or Alt+1 through Alt+9: Switch workspace tab
            if (!isInput && (e.ctrlKey || e.altKey || e.metaKey)) {
                var digit = -1;
                if (e.key >= '1' && e.key <= '9') {
                    digit = parseInt(String(e.key), 10);
                } else if (e.keyCode >= 49 && e.keyCode <= 57) {
                    digit = e.keyCode - 48;
                }
                if (digit !== -1 && window.WorkspaceTabs) {
                    var allCards = window.WorkspaceTabs.getCards();
                    if (allCards && allCards.length >= digit) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.WorkspaceTabs.switchToTab(digit);
                        return false;
                    }
                }
            }

            // Ctrl+PageDown / Ctrl+] -> Next tab, Ctrl+PageUp / Ctrl+[ -> Prev tab
            if (!isInput && (e.ctrlKey || e.metaKey)) {
                if (e.key === 'PageDown' || e.keyCode === 34 || e.key === ']' || e.keyCode === 221) {
                    if (window.WorkspaceTabs && window.WorkspaceTabs.getCards().length > 1) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.WorkspaceTabs.nextTab();
                        return false;
                    }
                }
                if (e.key === 'PageUp' || e.keyCode === 33 || e.key === '[' || e.keyCode === 219) {
                    if (window.WorkspaceTabs && window.WorkspaceTabs.getCards().length > 1) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.WorkspaceTabs.prevTab();
                        return false;
                    }
                }
            }

            // Non-input [ / ] quick tab cycling
            if (!isInput && !e.ctrlKey && !e.altKey && !e.metaKey) {
                if (e.key === ']' || e.keyCode === 221) {
                    if (window.WorkspaceTabs && window.WorkspaceTabs.getCards().length > 1) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.WorkspaceTabs.nextTab();
                        return false;
                    }
                }
                if (e.key === '[' || e.keyCode === 219) {
                    if (window.WorkspaceTabs && window.WorkspaceTabs.getCards().length > 1) {
                        if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                        window.WorkspaceTabs.prevTab();
                        return false;
                    }
                }
            }

            // Delete key -> Delete selected rows when not typing in input
            if ((e.key === 'Delete' || e.keyCode === 46) && !isInput) {
                if (getSelectedRowsArray().length > 0) {
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    window.onDeleteClick();
                    return false;
                }
            }

            // Ctrl+Z -> Undo (when not in input)
            if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.keyCode === 90) && !isInput) {
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                window.undo();
                updateToolbarState();
                return false;
            }

            // Ctrl+Y or Ctrl+Shift+Z -> Redo (when not in input)
            if (((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.keyCode === 89) || ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'z' || e.keyCode === 90))) && !isInput) {
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                window.redo();
                updateToolbarState();
                return false;
            }
        });

        var mismatchScript = document.getElementById('mismatch-info');
        if (mismatchScript) {
            try {
                var mData = JSON.parse(mismatchScript.textContent || mismatchScript.innerText || 'null');
                if (mData && (mData.is_mismatch || mData.detected_language)) {
                    window.showLanguageVerificationModal(mData);
                }
            } catch(e) {
                console.error('[Bootstrap] Failed to parse #mismatch-info JSON:', e);
            }
        }

         (['onUpdateClick', 'onRetextClick', 'onRewordClick', 'onSendToAnkiClick']).forEach(function(name) {
            var command = window[name];
            if (typeof command !== 'function') return;
            window[name] = function() {
                var args = arguments;
                return window.flushPendingSaves().then(function() {
                    return command.apply(window,  (Array.prototype.slice.call(args)));
                }).catch(function(err) {
                    window.showToast('Save failed; action stopped: ' + formatErrorWithTrace(err, 'Network error'), 'error');
                });
            };
        });

        var bootstrapScript = document.getElementById('kardenwort-bootstrap');
        if (bootstrapScript) {
            try {
                var bData = JSON.parse(bootstrapScript.textContent || bootstrapScript.innerText || '{}');
                if (bData && typeof bData === 'object') {
                    window.__CONFIG__ = window.__CONFIG__ || bData.ui_config || {};
                    window.AppState = window.AppState || {rows: {}, selectedLemmas: {}, activeTokenSelections: []};
                    window.AppState.config = window.__CONFIG__;
                    window.AppState.bootstrap = bData;
                    if (bData.sentence_cards) {
                        window.AppState.sentenceCards = bData.sentence_cards;
                        window.AppState.sentence_cards = bData.sentence_cards;
                    }
                }
            } catch(e) {
                console.error('[Bootstrap] Failed to parse #kardenwort-bootstrap JSON:', e);
                throw e;
            }
        }

        updateToolbarState();

        if (window.WorkspaceTabs) {
            try {
                window.WorkspaceTabs.init();
            } catch(e) {
                console.error('[Init] WorkspaceTabs initialization failed:', e);
                throw e;
            }
        }

        if (window.rebindMVPBookmarks) {
            window.rebindMVPBookmarks();
        }
        resumeMountedStream();
    }

    /** @param {WheelEvent} e */
    function handleHorizontalScroll(e) {
        e = (e || window.event);
        if (e.shiftKey || e.altKey) {
            var delta = e.wheelDelta ? -e.wheelDelta : (e.detail ? e.detail * 40 : 0);

            var target = (e.target || e.srcElement);
            var scrollCell = null;
            var curr = target;
            while (curr) {
                if (curr.classList && curr.classList.contains('scrollable-cell')) {
                    scrollCell = curr;
                    break;
                }
                curr = curr.parentElement;
            }

            if (scrollCell) {
                scrollCell.scrollLeft += delta;
            } else {
                var scrollEl = document.documentElement || document.body;
                scrollEl.scrollLeft += delta;
            }

            if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
            return false;
        }
    }
    addEvent(document, 'mousewheel', handleHorizontalScroll);
    addEvent(document, 'DOMMouseScroll', handleHorizontalScroll);

    if (document.addEventListener) {
        document.addEventListener('DOMContentLoaded', function() {
            if (!isInitialized) {
                init();
            }
            if (typeof hydrateSession === 'function') {
                hydrateSession();
            }
            if (typeof window.rebindTableRows === 'function') {
                window.rebindTableRows();
            }
            if (typeof window.rebindTokenSpans === 'function') {
                window.rebindTokenSpans();
            }
        }, false);
    }

    if (window.addEventListener) {
        window.addEventListener('DOMContentLoaded', init, false);
        window.addEventListener('load', init, false);
    } else if (window.attachEvent) {
        window.attachEvent('onload', init);
    } else {
        window.onload = init;
    }
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        init();
    }
})();
