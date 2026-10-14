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
        var checkboxes = document.querySelectorAll('.kw-tag-checkbox');
        var selected = [];
        checkboxes.forEach(function (cb) {
            if (cb.checked) {
                var tOrd = cb.getAttribute('data-token-order');
                var rId = cb.getAttribute('data-row-id');
                selected.push(parseInt((tOrd !== null && tOrd !== '') ? tOrd : rId, 10));
            }
        });
        return selected;
    }

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
        var dataEl = document.getElementById('session-data');
        if (!dataEl) return;
        var raw = (dataEl.textContent || '').trim();
        if (!raw || raw === '{}') return;

        try {
            var session = JSON.parse(raw);
            SESSION_ZID = session.session_zid || session.zid || '';
            API_TOKEN = session.api_token || '';
            LANGUAGE = session.language || 'de';
            TARGET_LANG = session.target_lang || 'ru';
            FINGERPRINT = session.fingerprint || '';
            SERVER_ENABLED = Boolean(session.server_enabled);
            if (session.server_url) {
                SERVER_URL = session.server_url;
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
            var srcEl = document.querySelector('#source-container .kw-source-text');
            if (srcEl && !srcEl.children.length && !srcEl.textContent.trim() && session.source_text) {
                srcEl.textContent = session.source_text;
            }

            // Hydrate Translation
            var transEl = document.querySelector('#translation-container .kw-translation');
            if (transEl && !transEl.children.length && !transEl.textContent.trim() && session.sentence_translation) {
                transEl.textContent = session.sentence_translation;
            }

            // Hydrate Table
            var tbody = document.querySelector('#lemma-table tbody');
            if (tbody && !tbody.children.length && session.data_rows && session.data_rows.length) {
                var headers = session.headers || [];
                var colLemma = headers.indexOf('WordSource');
                var colDest = headers.indexOf('WordDestination');
                var colInf = headers.indexOf('WordSourceInflectedForm');
                var colPos = headers.indexOf('WordSourcePOS');

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

                    var tdInf = document.createElement('td');
                    tdInf.className = 'col-inflected';
                    tdInf.textContent = (colInf !== -1 && row[colInf]) ? row[colInf] : '';
                    tr.appendChild(tdInf);

                    var tdLem = document.createElement('td');
                    tdLem.className = 'col-lemma';
                    tdLem.textContent = (colLemma !== -1 && row[colLemma]) ? row[colLemma] : (row[0] || '');
                    tr.appendChild(tdLem);

                    var tdPos = document.createElement('td');
                    tdPos.className = 'col-pos';
                    tdPos.textContent = (colPos !== -1 && row[colPos]) ? row[colPos] : '';
                    tr.appendChild(tdPos);

                    var tdDest = document.createElement('td');
                    tdDest.className = 'col-translation';
                    tdDest.textContent = (colDest !== -1 && row[colDest]) ? row[colDest] : (row[1] || '');
                    tr.appendChild(tdDest);

                    tbody.appendChild(tr);
                });
            }

            if (SERVER_ENABLED && SESSION_ZID) {
                initSSE(SESSION_ZID);
            }
        } catch (e) {
            console.error('Error hydrating session data:', e);
        }
    }

    function init() {
        hydrateSession();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
