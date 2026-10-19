// @ts-check

/**
 * @file WorkspaceTabs.js
 * Autonomous typed vanilla client workspace tab controller managing sentence switching,
 * tab strip navigation, dynamic vocabulary row binding, and sentence isolation.
 */

/**
 * @typedef {Object} WordItem
 * @property {number|string} row_id
 * @property {number|string} [token_order]
 * @property {string} [word]
 * @property {string} [lemma]
 * @property {string} [translation]
 * @property {boolean} [is_pending]
 * @property {string|null} [pending_provider]
 * @property {string} [inflected]
 * @property {string} [pos]
 * @property {string} [gender]
 * @property {string} [ipa]
 * @property {string} [morphology]
 * @property {string} [provenance]
 * @property {string} [highlight_class]
 * @property {string|number|boolean} [selected]
 * @property {string|null} [row_html]
 * @property {Array<string|number>} [all_row_ids]
 * @property {number} [sentence_idx]
 * @property {string} [sentence_text]
 * @property {string} [inflected_tooltip]
 * @property {Object} [occurrence_data]
 * @property {string} [dynamic_tds]
 */

/**
 * @typedef {Object} WorkspaceCard
 * @property {number} seq_num
 * @property {number} sentence_idx
 * @property {Array<WordItem>} [words]
 * @property {string} [translated_text]
 * @property {Array<string>} [selected_ids]
 * @property {string} [tsv_filename]
 * @property {string} [zid]
 * @property {number} [index]
 * @property {string} [label]
 */

/**
 * @typedef {Object} TabNavigationState
 * @property {number|null} activeTabSeq
 * @property {number} activeSentenceIdx
 * @property {Array<WorkspaceCard>} cards
 * @property {string} masterSourceHtml
 */

/**
 * @typedef {Object} SentenceSyncItem
 * @property {number} [sentence_index]
 * @property {string} [sentence_destination]
 * @property {string} [translation]
 */

/**
 * @typedef {Array<SentenceSyncItem|string> | Object<string, string|SentenceSyncItem>} SentenceSyncPayload
 */

(function () {
    'use strict';

    var WorkspaceTabs = (function () {
        /** @type {Array<WorkspaceCard>} */
        var cards = [];
        /** @type {number|null} */
        var activeTabSeq = null;
        /** @type {number} */
        var activeSentenceIdx = 0;
        /** @type {string} */
        var masterSourceHtml = "";
        /** @type {Object<string, boolean>} */
        var selectedRowIdsMap = {};

        /**
         * @param {string|null|undefined} str
         * @returns {string}
         */
        function escapeHtml(str) {
            var w = /** @type {any} */ (window);
            if (typeof w.escapeHtml === 'function') {
                return w.escapeHtml(str);
            }
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        /**
         * @param {HTMLElement|Element|Window|Document|null} el
         * @param {string} type
         * @param {EventListenerOrEventListenerObject|Function} fn
         */
        function addEvent(el, type, fn) {
            if (!el) return;
            var w = /** @type {any} */ (window);
            if (typeof w.addEvent === 'function') {
                w.addEvent(el, type, fn);
            } else if (el.addEventListener) {
                // @ts-ignore
                el.addEventListener(type, fn, false);
            } else if (/** @type {any} */ (el).attachEvent) {
                /** @type {any} */ (el).attachEvent('on' + type, fn);
            } else {
                // @ts-ignore
                el['on' + type] = fn;
            }
        }

        /**
         * @param {Element|null} el
         * @param {string} cls
         */
        function addClass(el, cls) {
            if (!el) return;
            var w = /** @type {any} */ (window);
            if (typeof w.addClass === 'function') {
                w.addClass(el, cls);
            } else if (el.classList) {
                el.classList.add(cls);
            } else {
                var cur = el.className || '';
                var reg = new RegExp('(\\s|^)' + cls + '(\\s|$)');
                if (!reg.test(cur)) el.className = (cur + ' ' + cls).trim();
            }
        }

        /**
         * @param {Element|null} el
         * @param {string} cls
         */
        function removeClass(el, cls) {
            if (!el) return;
            var w = /** @type {any} */ (window);
            if (typeof w.removeClass === 'function') {
                w.removeClass(el, cls);
            } else if (el.classList) {
                el.classList.remove(cls);
            } else {
                var cur = el.className || '';
                var reg = new RegExp('(\\s|^)' + cls + '(\\s|$)');
                el.className = cur.replace(reg, ' ').trim();
            }
        }

        /**
         * @returns {string}
         */
        function getTextMode() {
            var w = /** @type {any} */ (window);
            if (typeof w.getTextMode === 'function') return w.getTextMode();
            var el = document.getElementById('text-mode');
            if (el) return (el.textContent || el.innerText || '').trim();
            if (w.AppState && w.AppState.bootstrap) {
                if (w.AppState.bootstrap.text_mode) return w.AppState.bootstrap.text_mode;
                if (w.AppState.bootstrap.session_data && w.AppState.bootstrap.session_data.text_mode) {
                    return w.AppState.bootstrap.session_data.text_mode;
                }
            }
            if (w.__CONFIG__ && w.__CONFIG__.text_mode) {
                return w.__CONFIG__.text_mode;
            }
            var bootEl = document.getElementById('kardenwort-bootstrap');
            if (bootEl) {
                try {
                    var parsed = JSON.parse(bootEl.textContent || '');
                    if (parsed.text_mode) return parsed.text_mode;
                    if (parsed.session_data && parsed.session_data.text_mode) return parsed.session_data.text_mode;
                } catch(e) {
                    console.error('[Kardenwort] getTextMode: error parsing kardenwort-bootstrap JSON:', e);
                }
            }
            return 'single';
        }

        /**
         * @returns {string}
         */
        function getTextBaseProvider() {
            var w = /** @type {any} */ (window);
            if (typeof w.getTextBaseProvider === 'function') return w.getTextBaseProvider();
            var el = document.getElementById('text-base-provider');
            if (el) return (el.textContent || el.innerText || '').trim();
            if (w.AppState && w.AppState.bootstrap) {
                if (w.AppState.bootstrap.text_base_provider || w.AppState.bootstrap.provider) {
                    return w.AppState.bootstrap.text_base_provider || w.AppState.bootstrap.provider;
                }
                if (w.AppState.bootstrap.ui_config && w.AppState.bootstrap.ui_config.text_base_provider) {
                    return w.AppState.bootstrap.ui_config.text_base_provider;
                }
            }
            if (w.__CONFIG__ && (w.__CONFIG__.text_base_provider || w.__CONFIG__.provider)) {
                return w.__CONFIG__.text_base_provider || w.__CONFIG__.provider;
            }
            var bootEl = document.getElementById('kardenwort-bootstrap');
            if (bootEl) {
                try {
                    var parsed = JSON.parse(bootEl.textContent || '');
                    if (parsed.text_base_provider || parsed.provider) return parsed.text_base_provider || parsed.provider;
                    if (parsed.ui_config && parsed.ui_config.text_base_provider) return parsed.ui_config.text_base_provider;
                } catch(e) {
                    console.error('[Kardenwort] getTextBaseProvider: error parsing kardenwort-bootstrap JSON:', e);
                }
            }
            return '';
        }

        /**
         * @returns {string}
         */
        function getSessionLang() {
            var w = /** @type {any} */ (window);
            if (typeof w.getSessionLang === 'function') return w.getSessionLang();
            var el = document.getElementById('session-lang');
            if (el) {
                var elText = (el.textContent || el.innerText || '').trim();
                if (elText) return elText;
            }
            if (w.AppState && w.AppState.bootstrap) {
                var bootLang = w.AppState.bootstrap.language || w.AppState.bootstrap.lang;
                if (bootLang) return bootLang;
                if (w.AppState.bootstrap.session_data && (w.AppState.bootstrap.session_data.language || w.AppState.bootstrap.session_data.lang)) {
                    return w.AppState.bootstrap.session_data.language || w.AppState.bootstrap.session_data.lang;
                }
            }
            if (w.AppState && w.AppState.language) {
                return w.AppState.language;
            }
            if (w.__CONFIG__ && (w.__CONFIG__.language || w.__CONFIG__.lang)) {
                return w.__CONFIG__.language || w.__CONFIG__.lang;
            }
            var bootEl = document.getElementById('kardenwort-bootstrap');
            if (bootEl) {
                try {
                    var parsed = JSON.parse(bootEl.textContent || '');
                    if (parsed.language || parsed.lang) return parsed.language || parsed.lang;
                    if (parsed.session_data && (parsed.session_data.language || parsed.session_data.lang)) {
                        return parsed.session_data.language || parsed.session_data.lang;
                    }
                } catch(e) {
                    console.error('[Kardenwort] getSessionLang: error parsing kardenwort-bootstrap JSON:', e);
                }
            }
            console.error('[Kardenwort] getSessionLang: unable to resolve session language from bootstrap or session-lang');
            throw new Error('Missing session language in bootstrap state');
        }

        /**
         * @param {string} provider
         * @returns {string}
         */
        function formatProviderSkeletonLabel(provider) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatProviderSkeletonLabel === 'function') {
                return w.formatProviderSkeletonLabel(provider);
            }
            if (!provider) return "Loading...";
            var name = String(provider).trim().toLowerCase();
            /** @type {Object<string, string>} */
            var map = {
                'argos': 'Argos...',
                'google': 'Google...',
                'deepl': 'DeepL...',
                'intellifiller': 'IntelliFiller...'
            };
            if (map[name]) return map[name];
            return name.charAt(0).toUpperCase() + name.slice(1) + '...';
        }

        /**
         * @param {string} prov
         * @returns {string}
         */
        function formatProvenanceTooltip(prov) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatProvenanceTooltip === 'function') {
                return w.formatProvenanceTooltip(prov);
            }
            return prov || '';
        }

        /**
         * @param {string} translation
         * @param {string} provenance
         * @returns {string}
         */
        function formatTranslationCellTooltip(translation, provenance) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatTranslationCellTooltip === 'function') {
                return w.formatTranslationCellTooltip(translation, provenance);
            }
            return translation || '';
        }

        /**
         * @param {string} sentenceText
         * @param {string} inflectedVal
         * @param {any} tokenOrder
         * @param {string} lemma
         * @returns {string}
         */
        function formatInflectedTooltip(sentenceText, inflectedVal, tokenOrder, lemma) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatInflectedTooltip === 'function') {
                return w.formatInflectedTooltip(sentenceText, inflectedVal, tokenOrder, lemma);
            }
            return (inflectedVal || sentenceText || '').trim();
        }

        /**
         * @param {string} lemma
         * @param {string} gender
         * @param {string} activeLang
         * @param {string} pos
         * @returns {string}
         */
        function formatLemmaTooltip(lemma, gender, activeLang, pos) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatLemmaTooltip === 'function') {
                return w.formatLemmaTooltip(lemma, gender, activeLang, pos);
            }
            return (lemma || '').trim();
        }

        /**
         * @param {string} pos
         * @returns {string}
         */
        function formatPosTooltip(pos) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatPosTooltip === 'function') {
                return w.formatPosTooltip(pos);
            }
            return pos || '';
        }

        /**
         * @param {string} gender
         * @param {string} lemma
         * @param {string} prov
         * @returns {string}
         */
        function formatGenderTooltip(gender, lemma, prov) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatGenderTooltip === 'function') {
                return w.formatGenderTooltip(gender, lemma, prov);
            }
            return gender || '';
        }

        /**
         * @param {string|undefined} gender
         * @returns {string}
         */
        function formatGenderBadge(gender) {
            var w = /** @type {any} */ (window);
            if (typeof w.formatGenderBadge === 'function') {
                return w.formatGenderBadge(gender);
            }
            return escapeHtml(gender || '');
        }

        function updateEmptySelectionState() {
            var w = /** @type {any} */ (window);
            if (typeof w.updateEmptySelectionState === 'function') {
                w.updateEmptySelectionState();
            }
        }

        function syncSelectionsToCardsAndState() {
            var appState = /** @type {any} */ (window).AppState;
            if (!appState) return;
            var selMap = appState.selectedLemmas || {};
            if (cards && cards.length > 0) {
                for (var c = 0; c < cards.length; c++) {
                    var card = cards[c];
                    card.selected_ids = [];
                    if (card.words) {
                        for (var widx = 0; widx < card.words.length; widx++) {
                            var wObj = card.words[widx];
                            var wLem = (wObj.lemma || '').trim().toLowerCase();
                            if (wLem && selMap[wLem]) {
                                wObj.selected = "1";
                                var rId = String(wObj.row_id);
                                if (card.selected_ids.indexOf(rId) === -1) card.selected_ids.push(rId);
                            } else {
                                wObj.selected = "0";
                            }
                        }
                    }
                }
            }
        }

        /**
         * @param {string|undefined|null} tText
         * @returns {string}
         */
        function getTranslationHtml(tText) {
            var w = /** @type {any} */ (window);
            if (tText && typeof tText === 'string' && tText.indexOf('skeleton-loader') !== -1) {
                return tText;
            }
            if (tText && tText.trim() && tText.indexOf('skeleton-loader') === -1 && tText !== '[FAILED]' && (typeof tText !== 'string' || tText.indexOf('[Error') !== 0)) {
                var clean = tText.trim();
                /** @type {Array<string>} */
                var lines = [];
                if (clean.indexOf('<div') !== -1 || clean.indexOf('</div') !== -1 || clean.indexOf('<p') !== -1 || clean.indexOf('<br') !== -1) {
                    var tmp = document.createElement('div');
                    tmp.innerHTML = clean;
                    var childDivs = tmp.querySelectorAll('div, p');
                    if (childDivs.length > 0) {
                        for (var cd = 0; cd < childDivs.length; cd++) {
                            lines.push((childDivs[cd].textContent || /** @type {HTMLElement} */ (childDivs[cd]).innerText || '').trim());
                        }
                    }
                    if (lines.length === 0) {
                        var brClean = tmp.innerHTML.replace(/<br\s*[/]?>/gi, String.fromCharCode(10));
                        tmp.innerHTML = brClean;
                        clean = (tmp.textContent || tmp.innerText || '').trim();
                    }
                }
                if (lines.length === 0) {
                    lines = clean.replace(new RegExp(String.fromCharCode(13), 'g'), '').split(String.fromCharCode(10));
                }
                var isSingle = (typeof getTextMode === 'function' && getTextMode() === 'single');
                if (isSingle) {
                    var srcContainer = document.getElementById('source-container');
                    var srcText = srcContainer ? (srcContainer.textContent || /** @type {HTMLElement} */ (srcContainer).innerText || '') : '';
                    if (srcText.indexOf(String.fromCharCode(10)) !== -1 || srcText.indexOf(String.fromCharCode(13)) !== -1) {
                        isSingle = false;
                    }
                }
                if (isSingle) {
                    var validLines = lines.map(function(line) { return line.trim(); }).filter(function(line) { return line.length > 0; });
                    return escapeHtml(validLines.join(' '));
                }
                if (lines.length > 1) {
                    return lines.map(function(line) {
                        return '<div>' + (line ? escapeHtml(line) : '&nbsp;') + '</div>';
                    }).join('');
                }
                return escapeHtml(clean);
            }
            var isFailed = (w.AppState && (w.AppState.textTranslationFailed || w.AppState.textTranslationStatus === 'failed'));
            if (w.AppState && (w.AppState.isFinished || isFailed)) {
                return '';
            }
            var pLabel = formatProviderSkeletonLabel(getTextBaseProvider());
            return '<span class="skeleton-loader kw-skeleton-cell" data-pending="true" title="' + escapeHtml(pLabel) + '">' + escapeHtml(pLabel) + '</span>';
        }

        /**
         * @param {Array<WorkspaceCard>} [explicitCards]
         */
        function init(explicitCards) {
            var w = /** @type {any} */ (window);

            var sessZidEl = document.getElementById('session-zid');
            var curZid = sessZidEl ? (sessZidEl.textContent || /** @type {HTMLElement} */ (sessZidEl).innerText || "").trim() : "";
            if (!curZid && w.AppState && w.AppState.bootstrap) {
                curZid = w.AppState.bootstrap.session_zid || w.AppState.bootstrap.zid || (w.AppState.bootstrap.session_data && (w.AppState.bootstrap.session_data.session_zid || w.AppState.bootstrap.session_data.zid)) || "";
            }
            if (!curZid && document.body && document.body.getAttribute('data-zid')) {
                curZid = document.body.getAttribute('data-zid') || "";
            }

            var tabTabBar = document.getElementById('workspace-tab-bar') || document.getElementById('kw-workspace-tab-bar');
            var scriptEl = document.getElementById('sentence-cards');
            var rootEl = tabTabBar || scriptEl || document.body;

            if (w._kwWorkspaceTabsInitialized && w._kwWorkspaceTabsInitializedEl === rootEl && (!curZid || w._kwWorkspaceTabsInitializedZid === curZid) && !explicitCards) {
                updateNavButtons();
                return;
            }
            if (curZid && w._kwWorkspaceTabsInitializedZid && w._kwWorkspaceTabsInitializedZid !== curZid) {
                w._cachedSentenceCards = null;
            }
            w._kwWorkspaceTabsInitialized = true;
            w._kwWorkspaceTabsInitializedEl = rootEl;
            w._kwWorkspaceTabsInitializedZid = curZid;

            var scriptEl = document.getElementById('sentence-cards');
            var cached = explicitCards || w._cachedSentenceCards;
            if (explicitCards && explicitCards.length > 0) {
                cards = explicitCards;
            } else if (scriptEl) {
                try {
                    cards = (cached && cached.length > 0) ? cached : JSON.parse(scriptEl.textContent || scriptEl.innerText || '[]');
                } catch(e) {
                    cards = [];
                }
            } else if (cached && cached.length > 0) {
                cards = cached;
            } else if (w.AppState && w.AppState.sentenceCards) {
                cards = w.AppState.sentenceCards;
            } else if (w.AppState && w.AppState.bootstrap && w.AppState.bootstrap.sentence_cards) {
                cards = w.AppState.bootstrap.sentence_cards;
            }
            w._cachedSentenceCards = cards;
            w._kwSentenceCards = cards;

            var tabTabBar = document.getElementById('workspace-tab-bar') || document.getElementById('kw-workspace-tab-bar');
            var track = document.getElementById('kw-tab-track');
            if (tabTabBar && !tabTabBar.querySelector('.kw-tab-chip') && cards && cards.length > 1) {
                var dock = tabTabBar.querySelector('.kw-tab-dock-top');
                if (!dock) {
                    dock = document.createElement('div');
                    dock.className = 'kw-tab-dock-top';
                    tabTabBar.appendChild(dock);
                }
                var pBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('kw-tab-prev'));
                if (!pBtn) {
                    pBtn = /** @type {HTMLButtonElement} */ (document.createElement('button'));
                    pBtn.type = 'button';
                    pBtn.id = 'kw-tab-prev';
                    pBtn.className = 'kw-tab-nav';
                    pBtn.setAttribute('aria-label', 'Previous tab');
                    pBtn.disabled = true;
                    pBtn.innerHTML = '&lsaquo;';
                    dock.appendChild(pBtn);
                }
                if (!track) {
                    track = document.createElement('div');
                    track.className = 'kw-tab-track';
                    track.id = 'kw-tab-track';
                    dock.appendChild(track);
                }
                var nBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('kw-tab-next'));
                if (!nBtn) {
                    nBtn = /** @type {HTMLButtonElement} */ (document.createElement('button'));
                    nBtn.type = 'button';
                    nBtn.id = 'kw-tab-next';
                    nBtn.className = 'kw-tab-nav';
                    nBtn.setAttribute('aria-label', 'Next tab');
                    nBtn.disabled = true;
                    nBtn.innerHTML = '&rsaquo;';
                    dock.appendChild(nBtn);
                }
                if (!track.querySelector('.kw-tab-chip')) {
                    var chipsHtml = [];
                    for (var cIdx = 0; cIdx < cards.length; cIdx++) {
                        var cardItem = cards[cIdx];
                        var sSeq = cardItem.seq_num !== undefined ? cardItem.seq_num : (cIdx + 1);
                        var sIdx = cardItem.sentence_idx !== undefined ? cardItem.sentence_idx : cIdx;
                        var sLabel = cardItem.label;
                        if (!sLabel) {
                            sLabel = (sIdx === 0) ? (cards.length > 1 && cards[0].sentence_idx === 0 ? '[1]' : '1') : String(sIdx);
                        }
                        var sTitle = 'Ctrl+' + Math.min(sSeq, 9) + ': Sentence ' + (sIdx > 0 ? sIdx : 'All');
                        var activeCls = (cIdx === 0) ? ' active' : '';
                        chipsHtml.push(
                            '<button type="button" class="kw-tab-chip' + activeCls + '" data-tab-seq="' + sSeq + '" data-sentence-idx="' + sIdx + '" title="' + escapeHtml(sTitle) + '">' +
                            escapeHtml(sLabel) +
                            '</button>'
                        );
                    }
                    track.innerHTML = chipsHtml.join('');
                }
            }

            var srcContainer = document.getElementById('source-container');
            if (srcContainer && !masterSourceHtml) masterSourceHtml = srcContainer.innerHTML;

            var tabButtons = document.querySelectorAll('.kw-tab-chip');
            for (var i = 0; i < tabButtons.length; i++) {
                if (/** @type {any} */ (tabButtons[i])._kwBound) continue;
                /** @type {any} */ (tabButtons[i])._kwBound = true;
                (function(btn) {
                    addEvent(btn, 'click', function() {
                        var seq = parseInt(btn.getAttribute('data-tab-seq') || '1', 10);
                        switchToTab(seq);
                    });
                })(tabButtons[i]);
            }

            var prevBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('kw-tab-prev'));
            var nextBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('kw-tab-next'));
            if (!track) track = document.getElementById('kw-tab-track');
            if (prevBtn && track && !/** @type {any} */ (prevBtn)._kwBound) {
                /** @type {any} */ (prevBtn)._kwBound = true;
                addEvent(prevBtn, 'click', function(e) {
                    // @ts-ignore
                    if (e && e.preventDefault) e.preventDefault();
                    if (track) track.scrollLeft -= 140;
                    updateNavButtons();
                });
            }
            if (nextBtn && track && !/** @type {any} */ (nextBtn)._kwBound) {
                /** @type {any} */ (nextBtn)._kwBound = true;
                addEvent(nextBtn, 'click', function(e) {
                    // @ts-ignore
                    if (e && e.preventDefault) e.preventDefault();
                    if (track) track.scrollLeft += 140;
                    updateNavButtons();
                });
            }
            if (track) {
                addEvent(track, 'scroll', updateNavButtons);
                if (tabTabBar && !/** @type {any} */ (tabTabBar)._kwWheelBound) {
                    /** @type {any} */ (tabTabBar)._kwWheelBound = true;
                    /**
                     * @param {any} [e]
                     */
                    var handleTabWheel = function(e) {
                        e = e || window.event;
                        var delta = e.deltaY || e.deltaX || (e.wheelDelta ? -e.wheelDelta : (e.detail ? e.detail * 40 : 0));
                        if (delta !== 0) {
                            if (e.preventDefault) e.preventDefault();
                            if (track) track.scrollLeft += delta;
                            updateNavButtons();
                            return false;
                        }
                    };
                    addEvent(tabTabBar, 'wheel', handleTabWheel);
                    addEvent(tabTabBar, 'mousewheel', handleTabWheel);
                    addEvent(tabTabBar, 'DOMMouseScroll', handleTabWheel);
                }
                addEvent(window, 'resize', updateNavButtons);
                updateNavButtons();
            }

            var initialSeq = 1;
            var activeChip = document.querySelector('.kw-tab-chip.active');
            if (activeChip) {
                var parsedSeq = parseInt(activeChip.getAttribute('data-tab-seq') || '', 10);
                if (!isNaN(parsedSeq)) {
                    initialSeq = parsedSeq;
                }
            }
            activeTabSeq = initialSeq;
            w._kwActiveTabSeq = activeTabSeq;
            if (!w.AppState) w.AppState = {};
            if (!w.AppState.selectedLemmas) w.AppState.selectedLemmas = {};
            if (cards && cards.length > 0) {
                for (var c = 0; c < cards.length; c++) {
                    var card = cards[c];
                    if (card.words) {
                        for (var widx = 0; widx < card.words.length; widx++) {
                            var wObj = card.words[widx];
                            var isWSelected = (wObj.selected === "1" || wObj.selected === 1 || (card.selected_ids && card.selected_ids.indexOf(String(wObj.row_id)) !== -1));
                            if (isWSelected) {
                                var wLem = (wObj.lemma || '').trim().toLowerCase();
                                if (wLem) w.AppState.selectedLemmas[wLem] = true;
                            }
                        }
                    }
                }
            }
            syncSelectionsToCardsAndState();
            if (cards && cards.length > 0) {
                switchToTab(initialSeq, true);
            }

            if (typeof w.ensureWatchdogActive === 'function') {
                w.ensureWatchdogActive('tab-init');
            }

            var isWebMode = (window.location.protocol !== 'file:') || (document.body && document.body.getAttribute('data-web-mode') === 'true');
            var sessZidEl = document.getElementById('session-zid');
            var curZid = sessZidEl ? (sessZidEl.textContent || /** @type {HTMLElement} */ (sessZidEl).innerText || "").trim() : "";
            if (!curZid && w.AppState && w.AppState.bootstrap) {
                curZid = w.AppState.bootstrap.session_zid || w.AppState.bootstrap.zid || (w.AppState.bootstrap.session_data && (w.AppState.bootstrap.session_data.session_zid || w.AppState.bootstrap.session_data.zid)) || "";
            }
            if (!curZid) {
                var params = new URLSearchParams(window.location.search);
                curZid = params.get('session_zid') || params.get('zid') || "";
            }
            if (!curZid && document.body && document.body.getAttribute('data-zid')) {
                curZid = document.body.getAttribute('data-zid') || "";
            }

            var pendingCount = (typeof w.countPendingSkeletons === 'function') ? w.countPendingSkeletons() : 0;
            if (isWebMode && curZid && cards && cards.length > 1 && pendingCount === 0) {
                if (typeof fetch !== 'undefined') {
                    var probeUrl = "/session/status?zid=" + encodeURIComponent(curZid);
                    fetch(probeUrl, { method: 'GET', headers: { 'Accept': 'application/json' } })
                        .then(function(res) { if (res.ok) return res.json(); throw new Error("probe failed"); })
                        .then(function(resObj) {
                            var sData = (resObj && resObj.data) ? resObj.data : resObj;
                            if (sData) {
                                var isBusy = ((sData.stage === 'translating' || (sData.status && sData.status.stage === 'translating') || sData.worker_status === 'running') &&
                                    sData.is_finished !== true &&
                                    (!sData.status || sData.status.is_finished !== true));
                                if (isBusy) {
                                    if (typeof w.initWatchdog === 'function') {
                                        w.initWatchdog();
                                    }
                                }
                                if ((sData.rows || sData.translatedText || sData.translated_text || sData.sentences) && w.receiveUpdate) {
                                    w.receiveUpdate(sData);
                                }
                            }
                        })
                        .catch(function(err) {
                            console.warn('[Kardenwort] Initial status fetch error in WorkspaceTabs.init:', err);
                        });
                }
            }
        }

        function updateNavButtons() {
            var prevBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('kw-tab-prev'));
            var nextBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('kw-tab-next'));
            var track = document.getElementById('kw-tab-track');
            if (!track || !prevBtn || !nextBtn) return;
            var sl = track.scrollLeft;
            var maxScroll = track.scrollWidth - track.clientWidth;
            if (maxScroll <= 1) {
                prevBtn.disabled = true;
                nextBtn.disabled = true;
            } else {
                prevBtn.disabled = (sl <= 1);
                nextBtn.disabled = (sl >= maxScroll - 1);
            }
        }

        /**
         * @returns {number|null}
         */
        function getActiveTabSeq() {
            return activeTabSeq;
        }

        /**
         * @returns {WorkspaceCard|null}
         */
        function getActiveCard() {
            if (!cards || cards.length === 0) return null;
            for (var i = 0; i < cards.length; i++) {
                if (cards[i].seq_num === activeTabSeq) return cards[i];
            }
            return cards[0] || null;
        }

        /**
         * @returns {Array<WorkspaceCard>}
         */
        function getCards() {
            return cards;
        }

        /**
         * @param {HTMLElement|Element|null} tbody
         * @param {Array<WordItem>} words
         * @param {Object<string, boolean>} [selectedMap]
         */
        function bindCardWordsToTbody(tbody, words, selectedMap) {
            if (!tbody || !words) return;
            var w = /** @type {any} */ (window);
            /** @type {Array<string>} */
            var htmlParts = [];
            for (var i = 0; i < words.length; i++) {
                var wordObj = words[i];
                var rIdStr = String(wordObj.row_id);
                var tOrdStr = String(wordObj.token_order !== undefined && wordObj.token_order !== null ? wordObj.token_order : rIdStr);
                var wLemNorm = (wordObj.lemma || '').trim().toLowerCase();
                var isSel = (w.AppState && w.AppState.selectedLemmas && w.AppState.selectedLemmas[wLemNorm]) ||
                            !!(selectedMap && selectedMap.hasOwnProperty(rIdStr));
                if (isSel) {
                    if (selectedMap) selectedMap[rIdStr] = true;
                    if (wordObj.all_row_ids) {
                        for (var a = 0; a < wordObj.all_row_ids.length; a++) {
                            if (selectedMap) selectedMap[String(wordObj.all_row_ids[a])] = true;
                        }
                    }
                }
                var appRow = (w.AppState && w.AppState.rows) ? ((tOrdStr && w.AppState.rows[tOrdStr]) || w.AppState.rows[rIdStr]) : null;
                if (appRow) {
                    var appLem = (appRow.lemma || '').trim().toLowerCase();
                    var itemLem = (wordObj.lemma || '').trim().toLowerCase();
                    var appOrd = (appRow.token_order !== undefined && appRow.token_order !== null) ? String(appRow.token_order) : null;
                    var isMatchingRow = (appOrd && appOrd === tOrdStr) || (appLem && itemLem && appLem === itemLem) || !appLem;
                    if (isMatchingRow) {
                        var updatedTrans = (appRow.trans !== undefined && appRow.trans !== "") ? appRow.trans : ((appRow.WordDestination !== undefined && appRow.WordDestination !== "") ? appRow.WordDestination : appRow.word_translation);
                        if (wordObj.all_row_ids && wordObj.all_row_ids.length > 1 && w.AppState && w.AppState.rows) {
                            /** @type {Array<string>} */
                            var rolledTrans = [];
                            var parentLem = (wordObj.lemma || (appRow ? appRow.lemma : '') || '').trim().toLowerCase();
                            for (var aidx = 0; aidx < wordObj.all_row_ids.length; aidx++) {
                                var subRow = w.AppState.rows[String(wordObj.all_row_ids[aidx])];
                                if (subRow) {
                                    var subLem = (subRow.lemma || '').trim().toLowerCase();
                                    if (subLem && parentLem && subLem !== parentLem) {
                                        continue;
                                    }
                                    var subTr = (subRow.trans !== undefined && subRow.trans !== "") ? subRow.trans : ((subRow.WordDestination !== undefined && subRow.WordDestination !== "") ? subRow.WordDestination : subRow.word_translation);
                                    if (subTr && subTr !== "[FAILED]" && subTr.indexOf('skeleton-loader') === -1 && subTr !== '-' && subTr !== '--' && subTr.indexOf('[Error') !== 0) {
                                        if (rolledTrans.indexOf(subTr) === -1) rolledTrans.push(subTr);
                                    }
                                }
                            }
                            if (rolledTrans.length > 0) {
                                updatedTrans = rolledTrans.join(', ');
                            }
                        }
                        if (updatedTrans !== undefined && updatedTrans !== "") {
                            if (updatedTrans === '[FAILED]' || (typeof updatedTrans === 'string' && updatedTrans.indexOf('[Error') === 0)) {
                                if (wordObj.is_pending || (wordObj.translation && wordObj.translation.indexOf('skeleton-loader') !== -1)) {
                                    // Preserve existing skeleton loader during intermediate failover
                                } else if (!w.AppState || !w.AppState.isFinished) {
                                    var lemmaProv = (typeof w.getLemmaBaseProvider === 'function') ? w.getLemmaBaseProvider() : (getTextBaseProvider() || 'google');
                                    wordObj.is_pending = true;
                                    wordObj.pending_provider = lemmaProv;
                                    wordObj.translation = '';
                                } else {
                                    wordObj.is_pending = false;
                                    wordObj.translation = '';
                                }
                            } else {
                                wordObj.is_pending = false;
                                wordObj.pending_provider = null;
                                wordObj.translation = updatedTrans;
                            }
                        }
                        if (appRow.lemma !== undefined && appRow.lemma !== "") wordObj.lemma = appRow.lemma;
                        if (appRow.ipa !== undefined && appRow.ipa !== "") wordObj.ipa = appRow.ipa;
                        var updatedMorph = (appRow.morphology !== undefined && appRow.morphology !== "") ? appRow.morphology : ((appRow.morph !== undefined && appRow.morph !== "") ? appRow.morph : appRow.WordSourceMorphologyAI);
                        if (updatedMorph !== undefined && updatedMorph !== "") wordObj.morphology = updatedMorph;
                        var updatedPos = (appRow.pos !== undefined) ? appRow.pos : appRow.WordSourcePOS;
                        if (updatedPos !== undefined) wordObj.pos = updatedPos;
                        var updatedGender = (appRow.gender !== undefined) ? appRow.gender : appRow.WordSourceGender;
                        if (updatedGender !== undefined) wordObj.gender = updatedGender;
                        if (appRow.provenance) wordObj.provenance = appRow.provenance;
                        if (updatedTrans || appRow.provenance || appRow.ipa || updatedMorph || appRow.lemma || updatedPos !== undefined || updatedGender !== undefined) {
                            wordObj.row_html = null;
                        }
                    }
                }
                if (wordObj.row_html && (wordObj.row_html.indexOf('skeleton-loader') !== -1 || wordObj.row_html.indexOf('data-pending') !== -1)) {
                    if (!wordObj.is_pending && wordObj.translation && wordObj.translation.indexOf('skeleton-loader') === -1) {
                        wordObj.row_html = null;
                    }
                }
                if (wordObj.row_html) {
                    var rowHtml = wordObj.row_html;
                    if (isSel) {
                        rowHtml = rowHtml.replace('data-selected="0"', 'data-selected="1"');
                        if (rowHtml.indexOf('kw-row-selected') === -1) {
                            rowHtml = rowHtml.replace('class="', 'class="selected kw-row-selected ');
                        }
                    } else {
                        rowHtml = rowHtml.replace('data-selected="1"', 'data-selected="0"');
                        rowHtml = rowHtml.replace('selected kw-row-selected ', '');
                        rowHtml = rowHtml.replace('selected kw-row-selected', '');
                        rowHtml = rowHtml.replace('kw-row-selected ', '');
                        rowHtml = rowHtml.replace('kw-row-selected', '');
                    }
                    htmlParts.push(rowHtml);
                } else {
                    var selAttr = isSel ? '1' : '0';
                    var hlClass = wordObj.highlight_class || 'highlight-orange';
                    if (isSel) hlClass += ' selected kw-row-selected';
                    var effectiveProv = wordObj.provenance || (appRow && appRow.provenance) || (w.AppState && w.AppState.rows && ((tOrdStr && w.AppState.rows[tOrdStr] && w.AppState.rows[tOrdStr].provenance) || (rIdStr && w.AppState.rows[rIdStr] && w.AppState.rows[rIdStr].provenance))) || (w.AppState && w.AppState.rowProvenances && (w.AppState.rowProvenances[tOrdStr] || w.AppState.rowProvenances[rIdStr])) || '';
                    if (effectiveProv && !wordObj.provenance) {
                        wordObj.provenance = effectiveProv;
                    }
                    if (wordObj.translation === '[FAILED]' || (typeof wordObj.translation === 'string' && wordObj.translation.indexOf('[Error') === 0)) {
                        if (!w.AppState || !w.AppState.isFinished) {
                            var lemmaProv2 = (typeof w.getLemmaBaseProvider === 'function') ? w.getLemmaBaseProvider() : (getTextBaseProvider() || 'google');
                            wordObj.is_pending = true;
                            wordObj.pending_provider = lemmaProv2;
                            wordObj.translation = '';
                        } else {
                            wordObj.is_pending = false;
                            wordObj.translation = '';
                        }
                    }
                    var isSkel = wordObj.is_pending || (typeof wordObj.translation === 'string' && wordObj.translation.indexOf('skeleton-loader') !== -1);
                    var transDisplay = '';
                    if (isSkel) {
                        var effProv = wordObj.pending_provider || (typeof w.getLemmaBaseProvider === 'function' ? w.getLemmaBaseProvider() : (getTextBaseProvider() || 'google'));
                        var lLabel2 = formatProviderSkeletonLabel(effProv);
                        transDisplay = '<span class="skeleton-loader kw-skeleton-cell" data-pending="true" title="' + escapeHtml(lLabel2) + '">' + escapeHtml(lLabel2) + '</span>';
                    } else {
                        transDisplay = escapeHtml(wordObj.translation || '');
                    }
                    var transClean = isSkel ? '' : (wordObj.translation || '').trim();
                    var transTooltip = (!isSkel && transClean) ? formatTranslationCellTooltip(transClean, effectiveProv) : '';
                    var provData = effectiveProv ? (' data-provenance="' + escapeHtml(effectiveProv) + '"') : '';
                    var provTitle = transTooltip ? (' title="' + escapeHtml(transTooltip) + '"') : '';
                    var provAttr = provData + provTitle;
                    var allIdsAttr = (wordObj.all_row_ids && wordObj.all_row_ids.length > 0) ? (' data-all-row-ids="' + escapeHtml(wordObj.all_row_ids.join(',')) + '"') : '';
                    var activeLang = (w.AppConfig ? w.AppConfig.language : 'de') || 'de';
                    var cardSentenceText = (wordObj.sentence_text || (w.AppState ? w.AppState.sourceText : '') || '');
                    var infTooltip = wordObj.inflected_tooltip || (appRow && appRow.inflected_tooltip) || formatInflectedTooltip(cardSentenceText, wordObj.inflected || '', wordObj.token_order || rIdStr, wordObj.lemma || '');
                    var lemTooltip = formatLemmaTooltip(wordObj.lemma || '', wordObj.gender || '', activeLang, wordObj.pos || '');
                    var ipaTooltip = (wordObj.ipa && wordObj.ipa.indexOf('skeleton-loader') === -1) ? wordObj.ipa : '';
                    var morphTooltip = (wordObj.morphology && wordObj.morphology.indexOf('skeleton-loader') === -1) ? wordObj.morphology : '';
                    var posTooltip = formatPosTooltip(wordObj.pos || '');
                    var genTooltip = formatGenderTooltip(wordObj.gender || '', wordObj.lemma || '', effectiveProv || wordObj.provenance || '');

                    var infTitleAttr = infTooltip ? (' title="' + escapeHtml(infTooltip) + '"') : '';
                    var lemTitleAttr = lemTooltip ? (' title="' + escapeHtml(lemTooltip) + '"') : '';
                    var ipaTitleAttr = ipaTooltip ? (' title="' + escapeHtml(ipaTooltip) + '"') : '';
                    var morphTitleAttr = morphTooltip ? (' title="' + escapeHtml(morphTooltip) + '"') : '';
                    var posTitleAttr = posTooltip ? (' title="' + escapeHtml(posTooltip) + '"') : '';
                    var genTitleAttr = genTooltip ? (' title="' + escapeHtml(genTooltip) + '"') : '';

                    var occAttr = (wordObj.occurrence_data && typeof wordObj.occurrence_data === 'object' && Object.keys(wordObj.occurrence_data).length > 0)
                        ? (' data-occ="' + escapeHtml(JSON.stringify(wordObj.occurrence_data)) + '"')
                        : '';

                    htmlParts.push(
                        '<tr data-row-id="' + escapeHtml(rIdStr) + '"' + allIdsAttr + ' data-token-order="' + escapeHtml(String(wordObj.token_order || rIdStr)) + '"' + occAttr + ' data-sentence-idx="' + escapeHtml(String(wordObj.sentence_idx || '1')) + '" data-selected="' + selAttr + '" class="' + hlClass + '">' +
                        '<td class="editable col-inflected" data-col="WordSourceInflectedForm"' + infTitleAttr + '><div class="scrollable-cell">' + escapeHtml(wordObj.inflected || '') + '</div></td>' +
                        '<td class="editable col-lemma" data-col="WordSource"' + lemTitleAttr + '><div class="scrollable-cell">' + escapeHtml(wordObj.lemma || '') + '</div></td>' +
                        '<td class="editable col-translation" data-col="WordDestination"' + provAttr + '><div class="scrollable-cell"' + provAttr + '>' + transDisplay + '</div></td>' +
                        '<td class="col-ipa" data-col="WordSourceIPA"' + ipaTitleAttr + '><div class="scrollable-cell">' + (wordObj.ipa || '') + '</div></td>' +
                        '<td class="col-morphology" data-col="WordSourceMorphologyAI"' + morphTitleAttr + '><div class="scrollable-cell">' + (wordObj.morphology || '') + '</div></td>' +
                        '<td class="col-pos" data-col="WordSourcePOS"' + posTitleAttr + '><div class="scrollable-cell">' + (wordObj.pos || '') + '</div></td>' +
                        '<td class="col-gender" data-col="WordSourceGender"' + genTitleAttr + '><div class="scrollable-cell">' + formatGenderBadge(wordObj.gender) + '</div></td>' +
                        (wordObj.dynamic_tds || '') +
                        '</tr>'
                    );
                }
            }
            tbody.innerHTML = htmlParts.join('');
            if (w.AppState && w.AppState.filterSelectedOnly) {
                var lt = document.getElementById('lemma-table');
                if (lt) addClass(lt, 'kw-filter-selected-only');
            }
            if (typeof w.rebindTableRows === 'function') {
                w.rebindTableRows();
            }
            if (typeof updateEmptySelectionState === 'function') {
                updateEmptySelectionState();
            }
        }

        function updateActiveTabTranslation() {
            var w = /** @type {any} */ (window);
            var transContainer = document.getElementById('translation-container');
            if (!transContainer || !cards || cards.length === 0) return;
            for (var c = 0; c < cards.length; c++) {
                if (cards[c].seq_num === activeTabSeq) {
                    var tText = cards[c].translated_text;
                    if ((!tText || !tText.trim()) && w.AppState && (w.AppState.translatedText || w.AppState.translated_text)) {
                        var rawText = w.AppState.translatedText || w.AppState.translated_text;
                        if (cards.length === 1 || activeSentenceIdx === 0) {
                            tText = rawText;
                        } else {
                            /** @type {Array<string>} */
                            var lines = [];
                            if (rawText.indexOf('<div') !== -1 || rawText.indexOf('</div') !== -1) {
                                var tmpDiv = document.createElement('div');
                                tmpDiv.innerHTML = rawText;
                                var childDivs = tmpDiv.querySelectorAll('div');
                                if (childDivs.length > 0) {
                                    for (var cd = 0; cd < childDivs.length; cd++) {
                                        lines.push((childDivs[cd].textContent || /** @type {HTMLElement} */ (childDivs[cd]).innerText || '').trim());
                                    }
                                }
                            }
                            if (lines.length === 0) {
                                lines = rawText.replace(new RegExp(String.fromCharCode(13), 'g'), '').split(String.fromCharCode(10));
                            }
                            var cleanLines = lines.map(function(l) { return l.trim(); }).filter(function(l) { return l.length > 0; });
                            var sIdx0 = (cards[c].sentence_idx !== undefined ? cards[c].sentence_idx : activeSentenceIdx) - 1;
                            if (sIdx0 >= 0 && sIdx0 < cleanLines.length) {
                                tText = cleanLines[sIdx0];
                            } else if (cleanLines.length === 1 && cards.length === 2) {
                                tText = cleanLines[0];
                            }
                        }
                    }

                    var isSingleMode = (typeof getTextMode === 'function' && getTextMode() === 'single');
                    var srcContainer = document.getElementById('source-container');
                    var srcText = srcContainer ? (srcContainer.textContent || /** @type {HTMLElement} */ (srcContainer).innerText || '') : '';
                    if (srcText.indexOf(String.fromCharCode(10)) !== -1 || srcText.indexOf(String.fromCharCode(13)) !== -1) {
                        isSingleMode = false;
                    }
                    if (activeSentenceIdx === 0 && !isSingleMode) {
                        var filledCount = 0;
                        var maxSentenceIdx = 0;
                        for (var sc = 0; sc < cards.length; sc++) {
                            var scCard = cards[sc];
                            if (!scCard) continue;
                            if (scCard.sentence_idx > maxSentenceIdx) {
                                maxSentenceIdx = scCard.sentence_idx;
                            }
                            var scTrans = scCard.translated_text;
                            if (scCard.sentence_idx > 0 && scTrans && scTrans.trim()) {
                                filledCount++;
                            }
                        }
                        if (filledCount > 1) {
                            /** @type {Array<string>} */
                            var slots = new Array(maxSentenceIdx);
                            for (var si = 0; si < maxSentenceIdx; si++) {
                                slots[si] = "";
                            }
                            for (var sc2 = 0; sc2 < cards.length; sc2++) {
                                var sc2Card = cards[sc2];
                                if (!sc2Card) continue;
                                var cIdx = sc2Card.sentence_idx;
                                if (cIdx > 0 && cIdx <= maxSentenceIdx) {
                                    slots[cIdx - 1] = (sc2Card.translated_text || "").trim();
                                }
                            }
                            tText = slots.join(String.fromCharCode(10));
                        }
                    }
                    tText = tText || '';
                    cards[c].translated_text = tText;
                    if (tText && tText.trim() && tText.indexOf('skeleton-loader') === -1) {
                        transContainer.classList.remove('skeleton-loader');
                        transContainer.removeAttribute('data-pending');
                    }
                    if (activeSentenceIdx > 0) {
                        transContainer.setAttribute('data-sentence-idx', String(activeSentenceIdx));
                        transContainer.setAttribute('data-line-idx', '0');
                    } else {
                        transContainer.removeAttribute('data-sentence-idx');
                        transContainer.removeAttribute('data-line-idx');
                    }
                    transContainer.innerHTML = getTranslationHtml(tText);
                    var tabProv = (w.AppState ? (w.AppState.textProvenance || w.AppState.text_provenance || w.AppState.provenance) : null) || transContainer.getAttribute('data-provenance');
                    if (tabProv) {
                        var tabTitle = formatProvenanceTooltip(tabProv);
                        if (tabTitle) {
                            transContainer.setAttribute('title', tabTitle);
                            var tabWords = transContainer.querySelectorAll('span.word, div');
                            for (var tw = 0; tw < tabWords.length; tw++) {
                                tabWords[tw].setAttribute('data-provenance', tabProv);
                                tabWords[tw].setAttribute('title', tabTitle);
                            }
                        }
                    }
                    if (tText && tText.trim()) {
                        if (typeof w.tokenizeTranslation === 'function') {
                            w.tokenizeTranslation();
                        }
                        if (typeof w.buildLcIndex === 'function') {
                            w.buildLcIndex();
                        }
                        if (typeof w.wireMvpEvents === 'function') {
                            w.wireMvpEvents();
                        }
                        if (typeof w.updateBidirectionalHighlights === 'function') {
                            w.updateBidirectionalHighlights();
                        }
                    }
                    break;
                }
            }
        }

        /**
         * @param {number} seqOrSentIdx
         * @param {boolean} [isImmediate]
         */
        function switchToTab(seqOrSentIdx, isImmediate) {
            if (!cards || cards.length === 0) return;
            var w = /** @type {any} */ (window);
            /** @type {WorkspaceCard|null} */
            var targetCard = null;
            for (var i = 0; i < cards.length; i++) {
                if (cards[i].seq_num === seqOrSentIdx) {
                    targetCard = cards[i];
                    break;
                }
            }
            if (!targetCard) {
                for (var j = 0; j < cards.length; j++) {
                    if (cards[j].sentence_idx === seqOrSentIdx) {
                        targetCard = cards[j];
                        break;
                    }
                }
            }
            if (!targetCard) return;

            if (typeof w.clearMVPBookmarks === 'function') {
                w.clearMVPBookmarks();
            }

            // 1. Ensure selection state is synchronized across all cards and AppState
            syncSelectionsToCardsAndState();

            activeTabSeq = targetCard.seq_num;
            activeSentenceIdx = targetCard.sentence_idx;
            w._kwActiveTabSeq = activeTabSeq;

            if (w.AppState && w.AppState.activeTokenSelections && w.AppState.activeTokenSelections.length > 0) {
                if (activeSentenceIdx > 0) {
                    w.AppState.activeTokenSelections = w.AppState.activeTokenSelections.filter(
                        /** @param {any} item */
                        function(item) {
                        var sIdx = item.sentence_idx;
                        if (sIdx === undefined || sIdx === null || isNaN(sIdx)) {
                            if (typeof w.findTokenDataByVisualIdx === 'function') {
                                var td = w.findTokenDataByVisualIdx(item.visual_idx);
                                if (td && td.sentence_idx !== undefined) sIdx = td.sentence_idx;
                            }
                        }
                        return sIdx !== undefined && sIdx !== null && !isNaN(sIdx) && String(sIdx) === String(activeSentenceIdx);
                    });
                }
                if (typeof w.saveActiveTokensToStorage === 'function') {
                    w.saveActiveTokensToStorage();
                }
            }

            var activeBtn = null;
            var tabButtons = document.querySelectorAll('.kw-tab-chip');
            for (var i2 = 0; i2 < tabButtons.length; i2++) {
                var bSeq = parseInt(tabButtons[i2].getAttribute('data-tab-seq') || '', 10);
                if (bSeq === activeTabSeq) {
                    addClass(tabButtons[i2], 'active');
                    activeBtn = tabButtons[i2];
                } else {
                    removeClass(tabButtons[i2], 'active');
                }
            }
            if (activeBtn) {
                if (activeBtn.scrollIntoView) {
                    try {
                        if (isImmediate) {
                            activeBtn.scrollIntoView({ behavior: 'auto', inline: 'nearest', block: 'nearest' });
                        } else {
                            activeBtn.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' });
                        }
                    } catch(e) {
                        activeBtn.scrollIntoView(false);
                    }
                }
            }
            updateNavButtons();

            var srcContainer = document.getElementById('source-container');
            var transContainer = document.getElementById('translation-container');

            if (activeSentenceIdx === 0) {
                if (srcContainer) {
                    var chunks = srcContainer.querySelectorAll('.kw-sentence-chunk');
                    if (chunks.length > 0) {
                        for (var c = 0; c < chunks.length; c++) {
                            /** @type {HTMLElement} */ (chunks[c]).style.display = '';
                        }
                    } else {
                        var spans = srcContainer.querySelectorAll('[data-sentence-idx]');
                        for (var s = 0; s < spans.length; s++) {
                            /** @type {HTMLElement} */ (spans[s]).style.display = '';
                        }
                    }
                }
                if (transContainer) {
                    updateActiveTabTranslation();
                }
            } else {
                if (srcContainer) {
                    var sentStr = String(activeSentenceIdx);
                    var chunks2 = srcContainer.querySelectorAll('.kw-sentence-chunk');
                    if (chunks2.length > 0) {
                        for (var c2 = 0; c2 < chunks2.length; c2++) {
                            var chunkSentIdx = chunks2[c2].getAttribute('data-sentence-idx');
                            if (chunkSentIdx && chunkSentIdx !== sentStr) {
                                /** @type {HTMLElement} */ (chunks2[c2]).style.display = 'none';
                            } else {
                                /** @type {HTMLElement} */ (chunks2[c2]).style.display = '';
                            }
                        }
                    } else {
                        var spans2 = srcContainer.querySelectorAll('[data-sentence-idx]');
                        for (var s2 = 0; s2 < spans2.length; s2++) {
                            var spanSentIdx = spans2[s2].getAttribute('data-sentence-idx');
                            if (spanSentIdx && spanSentIdx !== sentStr) {
                                /** @type {HTMLElement} */ (spans2[s2]).style.display = 'none';
                            } else {
                                /** @type {HTMLElement} */ (spans2[s2]).style.display = '';
                            }
                        }
                    }
                }
                if (transContainer) {
                    updateActiveTabTranslation();
                }
            }

            // Synchronize target card's selections from global AppState.selectedLemmas
            selectedRowIdsMap = {};
            if (w.selectedRowIdsMap) w.selectedRowIdsMap = selectedRowIdsMap;
            syncSelectionsToCardsAndState();

            // Dynamic viewport binding: bind active card's words into #lemma-table tbody
            var tbody = document.querySelector('#lemma-table tbody');
            if (tbody && targetCard.words && targetCard.words.length > 0) {
                bindCardWordsToTbody(tbody, targetCard.words, selectedRowIdsMap);
            } else if (tbody && targetCard.words && targetCard.words.length === 0) {
                tbody.innerHTML = '<tr class="kw-empty-table-row"><td colspan="10" class="kw-empty-cell" style="text-align: center; padding: 24px; color: var(--color-fg-muted, #8b949e); font-style: italic;">No vocabulary words in this section</td></tr>';
                if (typeof w.rebindTableRows === 'function') {
                    w.rebindTableRows();
                }
            } else {
                var tableRows = document.querySelectorAll('#lemma-table tbody tr');
                var sentStr2 = String(activeSentenceIdx);
                for (var r = 0; r < tableRows.length; r++) {
                    if (activeSentenceIdx === 0) {
                        /** @type {HTMLElement} */ (tableRows[r]).style.display = '';
                    } else {
                        var rowSentIdx = tableRows[r].getAttribute('data-sentence-idx');
                        /** @type {HTMLElement} */ (tableRows[r]).style.display = (rowSentIdx && rowSentIdx === sentStr2) ? '' : 'none';
                    }
                }
                if (typeof w.rebindTableRows === 'function') {
                    w.rebindTableRows();
                }
            }

            if (typeof w.tokenizeTranslation === 'function') w.tokenizeTranslation();
            if (typeof w.buildLcIndex === 'function') w.buildLcIndex();
            if (typeof w.updateBidirectionalHighlights === 'function') w.updateBidirectionalHighlights();
            if (typeof w.updateRowStyles === 'function') w.updateRowStyles();
            if (typeof w.refreshBookmarkClasses === 'function') {
                w.refreshBookmarkClasses();
            }
            if (w.AppState && w.AppState.filterSelectedOnly) {
                var lt = document.getElementById('lemma-table');
                if (lt) addClass(lt, 'kw-filter-selected-only');
                var fb = document.getElementById('kw-btn-filter-selected');
                if (fb) addClass(fb, 'btn-filter-active');
                if (typeof updateEmptySelectionState === 'function') {
                    updateEmptySelectionState();
                }
            } else {
                var lt = document.getElementById('lemma-table');
                if (lt) removeClass(lt, 'kw-filter-selected-only');
                var fb = document.getElementById('kw-btn-filter-selected');
                if (fb) removeClass(fb, 'btn-filter-active');
            }
            var sb = document.getElementById('kw-btn-same-lemma');
            if (sb && w.AppState) {
                if (w.AppState.highlightSameLemma) {
                    addClass(sb, 'active');
                } else {
                    removeClass(sb, 'active');
                }
            }
            updateFavicon(activeTabSeq);
            updateTitle(targetCard);
            if (typeof w.updateToolbarState === 'function') w.updateToolbarState();
            if (w.forceRepaint) w.forceRepaint();

            if (!isImmediate && typeof w.ensureWatchdogActive === 'function') {
                w.ensureWatchdogActive('tab-switch');
            }
        }

        /**
         * @param {WorkspaceCard} targetCard
         */
        function updateTitle(targetCard) {
            var w = /** @type {any} */ (window);
            var lang = '';
            try {
                lang = getSessionLang();
            } catch (e) {
                lang = 'unknown';
            }
            var tMode = getTextMode() || 'single';
            var cardTsv = (targetCard && targetCard.tsv_filename) ? targetCard.tsv_filename : '';
            if (!cardTsv) {
                if (targetCard && targetCard.zid) {
                    cardTsv = targetCard.zid + '.' + lang + '.tsv';
                } else {
                    var tsvPathEl = document.getElementById('tsv-path');
                    if (tsvPathEl) {
                        var fullPath = (tsvPathEl.textContent || tsvPathEl.innerText || '').trim();
                        cardTsv = fullPath.split(/[/\\]/).pop() || '';
                    } else if (w.AppState && w.AppState.bootstrap && w.AppState.bootstrap.tsv_filename) {
                        cardTsv = w.AppState.bootstrap.tsv_filename;
                    }
                }
            }
            document.title = 'Kardenwort - ' + lang + ' (' + tMode + ') - ' + cardTsv + ' - Ready';
        }

        /**
         * @param {number|null} seq
         */
        function updateFavicon(seq) {
            var num = (seq !== null && seq >= 1 && seq <= 99) ? seq : 1;
            var iconUrl = '/assets/numbers/' + num + '.ico';
            var links = document.querySelectorAll("link[rel*='icon']");
            if (links && links.length > 0) {
                for (var k = 0; k < links.length; k++) {
                    /** @type {HTMLLinkElement} */ (links[k]).href = iconUrl;
                }
            } else {
                var link = document.createElement('link');
                link.rel = 'icon';
                link.type = 'image/x-icon';
                link.href = iconUrl;
                var head = document.getElementsByTagName('head')[0];
                if (head) head.appendChild(link);
            }
        }

        function nextTab() {
            if (!cards || cards.length === 0) return;
            var curIdx = 0;
            for (var i = 0; i < cards.length; i++) {
                if (cards[i].seq_num === activeTabSeq) {
                    curIdx = i;
                    break;
                }
            }
            var nextIdx = (curIdx + 1) % cards.length;
            switchToTab(cards[nextIdx].seq_num);
        }

        function prevTab() {
            if (!cards || cards.length === 0) return;
            var curIdx = 0;
            for (var i = 0; i < cards.length; i++) {
                if (cards[i].seq_num === activeTabSeq) {
                    curIdx = i;
                    break;
                }
            }
            var prevIdx = (curIdx - 1 + cards.length) % cards.length;
            switchToTab(cards[prevIdx].seq_num);
        }

        /**
         * @param {SentenceSyncPayload} sents
         */
        function updateSentences(sents) {
            if (!sents || !cards || cards.length === 0) return;
            var w = /** @type {any} */ (window);
            if (Array.isArray(sents)) {
                for (var i = 0; i < sents.length; i++) {
                    var s = sents[i];
                    var sIdx = (typeof s === 'object' && s !== null && s.sentence_index !== undefined) ? s.sentence_index : (i + 1);
                    var sDst = (typeof s === 'string') ? s : ((s && (s.sentence_destination || s.translation)) || "");
                    for (var c = 0; c < cards.length; c++) {
                        if (cards[c].sentence_idx === sIdx) {
                            if (cards[c].translated_text !== sDst) {
                                cards[c].translated_text = sDst;
                            }
                        }
                    }
                }
            } else if (typeof sents === 'object') {
                var sentsObj = /** @type {Object<string, any>} */ (sents);
                var isZeroBased = (sentsObj[0] !== undefined || sentsObj['0'] !== undefined);
                for (var kRaw in sentsObj) {
                    if (Object.prototype.hasOwnProperty.call(sentsObj, kRaw)) {
                        if (kRaw === 'PADDED' || kRaw === 'FULL_TEXT') continue;
                        var num = parseInt(kRaw, 10);
                        if (isNaN(num)) continue;
                        var sIdx2 = isZeroBased ? (num + 1) : num;
                        if (sIdx2 <= 0) continue;
                        var sDst2 = typeof sentsObj[kRaw] === 'string' ? sentsObj[kRaw] : ((sentsObj[kRaw] && (sentsObj[kRaw].sentence_destination || sentsObj[kRaw].translation)) || "");
                        for (var c2 = 0; c2 < cards.length; c2++) {
                            if (cards[c2].sentence_idx === sIdx2) {
                                if (cards[c2].translated_text !== sDst2) {
                                    cards[c2].translated_text = sDst2;
                                }
                            }
                        }
                    }
                }
            }
            var isSingleMode = (typeof getTextMode === 'function' && getTextMode() === 'single');
            var srcContainer = document.getElementById('source-container');
            var srcText = srcContainer ? (srcContainer.textContent || /** @type {HTMLElement} */ (srcContainer).innerText || '') : '';
            if (srcText.indexOf(String.fromCharCode(10)) !== -1 || srcText.indexOf(String.fromCharCode(13)) !== -1) {
                isSingleMode = false;
            }
            for (var c3 = 0; c3 < cards.length; c3++) {
                var c3Card = cards[c3];
                if (!c3Card) continue;
                if (c3Card.sentence_idx === 0) {
                    var filledCount = 0;
                    var maxSentenceIdx = 0;
                    for (var sc = 0; sc < cards.length; sc++) {
                        var scItem = cards[sc];
                        if (!scItem) continue;
                        if (scItem.sentence_idx > maxSentenceIdx) {
                            maxSentenceIdx = scItem.sentence_idx;
                        }
                        var scTrans = scItem.translated_text;
                        if (scItem.sentence_idx > 0 && scTrans && scTrans.trim()) {
                            filledCount++;
                        }
                    }
                    var c3Trans = c3Card.translated_text;
                    if (filledCount > 1 && !isSingleMode) {
                        /** @type {Array<string>} */
                        var slots = new Array(maxSentenceIdx);
                        for (var si = 0; si < maxSentenceIdx; si++) {
                            slots[si] = "";
                        }
                        for (var sc2 = 0; sc2 < cards.length; sc2++) {
                            var sc2Item = cards[sc2];
                            if (!sc2Item) continue;
                            var cIdx = sc2Item.sentence_idx;
                            if (cIdx > 0 && cIdx <= maxSentenceIdx) {
                                slots[cIdx - 1] = (sc2Item.translated_text || "").trim();
                            }
                        }
                        c3Card.translated_text = slots.join(String.fromCharCode(10));
                    } else if (filledCount === 1 && !isSingleMode && (!c3Trans || !c3Trans.trim())) {
                        for (var sc3 = 0; sc3 < cards.length; sc3++) {
                            var sc3Item = cards[sc3];
                            if (!sc3Item) continue;
                            var sc3Trans = sc3Item.translated_text;
                            if (sc3Item.sentence_idx > 0 && sc3Trans && sc3Trans.trim()) {
                                c3Card.translated_text = sc3Trans.trim();
                                break;
                            }
                        }
                    } else if (w.AppState && w.AppState.translatedText) {
                        c3Card.translated_text = w.AppState.translatedText;
                    }
                    break;
                }
            }
            for (var c4 = 0; c4 < cards.length; c4++) {
                if (cards[c4].words) {
                    var cWords = /** @type {Array<WordItem>} */ (cards[c4].words);
                    for (var wIdx = 0; wIdx < cWords.length; wIdx++) {
                        cWords[wIdx].row_html = null;
                    }
                }
            }
            updateActiveTabTranslation();
        }

        function rebindActiveTab() {
            var tb = document.querySelector('#lemma-table tbody');
            /** @type {WorkspaceCard|null} */
            var targetCard = null;
            for (var i = 0; i < cards.length; i++) {
                if (cards[i].seq_num === activeTabSeq) {
                    targetCard = cards[i];
                    break;
                }
            }
            if (tb && targetCard && targetCard.words && targetCard.words.length > 0) {
                for (var wIdx = 0; wIdx < targetCard.words.length; wIdx++) {
                    var wItem = targetCard.words[wIdx];
                    if (wItem && wItem.row_html && (wItem.row_html.indexOf('skeleton-loader') !== -1 || wItem.row_html.indexOf('data-pending') !== -1)) {
                        if (!wItem.is_pending && wItem.translation && wItem.translation.indexOf('skeleton-loader') === -1) {
                            wItem.row_html = null;
                        }
                    }
                }
                bindCardWordsToTbody(tb, targetCard.words, selectedRowIdsMap);
            }
        }

        // Public instance API
        return {
            init: init,
            switchToTab: switchToTab,
            nextTab: nextTab,
            prevTab: prevTab,
            getActiveTabSeq: getActiveTabSeq,
            getActiveCard: getActiveCard,
            getActiveSentenceIdx: function() { return activeSentenceIdx; },
            getCards: getCards,
            getTranslationHtml: getTranslationHtml,
            updateActiveTabTranslation: updateActiveTabTranslation,
            updateSentences: updateSentences,
            rebindActiveTab: rebindActiveTab,
            bindCardWordsToTbody: bindCardWordsToTbody,
            syncSelectionsToCardsAndState: syncSelectionsToCardsAndState
        };
    })();

    // Expose on global window object
    /** @type {any} */ (window).WorkspaceTabs = WorkspaceTabs;
    /** @type {any} */ (window).bindCardWordsToTbody = WorkspaceTabs.bindCardWordsToTbody;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = WorkspaceTabs;
    }
})();
