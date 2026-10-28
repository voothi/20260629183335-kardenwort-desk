(function (root) {
    'use strict';
    var status = root.querySelector('.kw-interactive-status');
    var controls = root.querySelectorAll('input.kw-tag-checkbox, .kw-export-btn');
    var groups = new Map();
    var pending = new Map();
    var state = 'idle';
    var exportRequested = false;
    var metadata;

    function project(message) {
        root.dataset.state = state;
        status.textContent = message || '\u00a0';
        controls.forEach(function (control) {
            control.disabled = state === 'unavailable' || state === 'failed' || state === 'exporting';
        });
        groups.forEach(function (group) {
            var selected = pending.has(group.key) ? pending.get(group.key) : group.selected;
            group.rows.forEach(function (row) {
                var element = row.element;
                element.classList.toggle('kw-row-selected', selected);
                element.dataset.selected = selected ? '1' : '0';
                var checkbox = element.querySelector('.kw-tag-checkbox');
                if (checkbox) checkbox.checked = selected;
            });
        });
    }

    function validTarget(target) {
        return Number.isInteger(target.sentence_idx) && target.sentence_idx > 0 &&
            Number.isInteger(target.token_order) && target.token_order >= 0 &&
            Number.isInteger(target.row_id) && target.row_id >= 0;
    }

    function targets(group) {
        var unique = new Map();
        group.rows.forEach(function (row) {
            if (!row.targets.length) throw new Error('Article identity unavailable; reopen the article.');
            row.targets.forEach(function (target) {
                if (!validTarget(target)) throw new Error('Article identity unavailable; reopen the article.');
                unique.set(target.sentence_idx + ':' + target.token_order, target);
            });
        });
        return Array.from(unique.values());
    }

    async function command(path, payload) {
        var response = await fetch(metadata.server_url + path, {
            method: 'POST', credentials: 'omit',
            headers: {'Content-Type': 'application/json', 'X-API-Token': metadata.api_token},
            body: JSON.stringify(Object.assign({
                session_zid: metadata.session_zid, language: metadata.language,
                fingerprint: metadata.fingerprint
            }, payload))
        });
        var result = await response.json();
        if (!response.ok || result.status !== 'success') {
            var code = /^[A-Z_]+$/.test(result.error_code || '') ? result.error_code : 'HTTP_' + response.status;
            throw new Error('Action failed (' + code + '); reopen the article.');
        }
        if (!result.data || result.data.status === 'error' || result.data.status === 'skipped') {
            throw new Error('Action was not acknowledged; reopen the article.');
        }
        return result.data;
    }

    async function save(key, selected) {
        var deltas = targets(groups.get(key)).map(function (target) {
            return Object.assign({}, target, {column: metadata.selected_column, value: selected ? '1' : '0'});
        });
        var acknowledgment = await command('/session/save', {deltas: deltas});
        if (!acknowledgment.fingerprint || acknowledgment.session_zid !== metadata.session_zid) {
            throw new Error('Invalid selection acknowledgment; reopen the article.');
        }
        metadata.fingerprint = acknowledgment.fingerprint;
        groups.get(key).selected = selected;
        if (pending.get(key) === selected) pending.delete(key);
    }

    async function exportSelection() {
        var indices = new Set();
        groups.forEach(function (group) {
            if (group.selected) targets(group).forEach(function (target) { indices.add(target.row_id); });
        });
        if (!indices.size) return 'Select a lemma before exporting.';
        var acknowledgment = await command('/session/export', {
            selected_row_ids: Array.from(indices), include_article_state: true
        });
        var current = acknowledgment.article_state;
        if (!current || current.session_zid !== metadata.session_zid || !current.fingerprint || !current.shared_selection) {
            throw new Error('Export state unavailable; reopen the article.');
        }
        metadata.fingerprint = current.fingerprint;
        groups.forEach(function (group) { group.selected = current.shared_selection[group.key] === true; });
        return 'Export acknowledged.';
    }

    async function drain() {
        if (state !== 'idle') return;
        try {
            while (pending.size) {
                state = 'tagging';
                project('Saving selection...');
                var entry = pending.entries().next().value;
                await save(entry[0], entry[1]);
            }
            if (exportRequested) {
                exportRequested = false;
                state = 'exporting';
                project('Exporting...');
                status.textContent = await exportSelection();
            }
            state = 'idle';
            project(status.textContent === 'Saving selection...' ? 'Selection saved.' : status.textContent);
        } catch (error) {
            state = 'failed';
            exportRequested = false;
            project(error instanceof TypeError ? 'Transport failed; reopen the article.' : error.message);
        }
    }

    function supportedBackend(url) {
        return /^https?:$/.test(url.protocol) &&
            /^(localhost|127\.0\.0\.1|\[::1\])$/.test(url.hostname) && !url.username && !url.password;
    }

    function readMetadata() {
        metadata = JSON.parse(root.querySelector('[data-kw-article]').textContent);
        var url = new URL(metadata.server_url);
        var identity = ['api_token', 'session_zid', 'language', 'fingerprint', 'selected_column'];
        var complete = identity.every(function (name) {
            return typeof metadata[name] === 'string' && metadata[name].trim().length > 0;
        });
        if (typeof fetch !== 'function' || !complete || !supportedBackend(url)) {
            throw new Error('Authenticated article transport unavailable.');
        }
    }

    function initialize() {
        readMetadata();
        var elements = new Map();
        root.querySelectorAll('tr[data-row-id]').forEach(function (element) {
            elements.set(Number(element.dataset.rowId), element);
        });
        metadata.rows.forEach(function (row) {
            if (!row.lemma) throw new Error('Article lemma identity unavailable.');
            row.element = elements.get(row.display_id);
            if (!row.element) throw new Error('Article row unavailable.');
            var group = groups.get(row.lemma) || {key: row.lemma, selected: false, rows: []};
            group.selected = group.selected || row.selected;
            group.rows.push(row);
            groups.set(row.lemma, group);
        });
        groups.forEach(targets);
        root.addEventListener('change', function (event) {
            if (!event.target.matches('input.kw-tag-checkbox')) return;
            var row = metadata.rows[Number(event.target.dataset.rowId)];
            pending.set(row.lemma, event.target.checked);
            project('Selection pending...');
            void drain();
        });
        root.querySelector('.kw-export-btn').addEventListener('click', function () {
            exportRequested = true;
            void drain();
        });
        project('');
    }

    try {
        initialize();
    } catch (error) {
        state = 'unavailable';
        project('Article actions unavailable; verify identity and authenticated transport.');
    }
})(document.currentScript.parentElement);
