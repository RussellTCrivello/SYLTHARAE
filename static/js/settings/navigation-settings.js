/**
 * Sidebar navigation preferences (Settings → Navigation).
 *
 * The user reorders and shows/hides their own sidebar entries with buttons:
 * Up / Down move an entry, the checkbox shows or hides it, Save sends the
 * whole desired state to PUT /api/preferences/navigation, Reset returns to
 * the declared navigation. Nothing here is JSON the user has to write.
 *
 * The hidden entries stay in the list (greyed out) so they can be brought
 * back without losing their place.
 */
(function () {
    'use strict';

    let entries = [];

    function t(key, fallback) {
        var island = document.getElementById('settings-page-data');
        if (island) {
            try {
                var data = JSON.parse(island.textContent);
                if (data && data.translations && data.translations[key]) {
                    return data.translations[key];
                }
            } catch (err) { /* fall through to the English fallback */ }
        }
        return fallback;
    }

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        if (window.__csrfToken) return window.__csrfToken;
        return document.cookie.split('; ').reduce(function (acc, pair) {
            return acc || (pair.indexOf('csrf_token=') === 0 ? pair.split('=')[1] : acc);
        }, '');
    }

    function setStatus(message) {
        const status = document.getElementById('navigationPrefsStatus');
        if (status) status.textContent = message || '';
    }

    function rowFor(entry) {
        const tr = document.createElement('tr');
        tr.setAttribute('data-interface', entry.interface_id);
        if (entry.hidden) tr.classList.add('text-muted');

        const name = document.createElement('td');
        const icon = document.createElement('i');
        icon.className = 'bi ' + (entry.icon || 'bi-circle') + ' me-2';
        icon.setAttribute('aria-hidden', 'true');
        name.appendChild(icon);
        name.appendChild(document.createTextNode(entry.label || entry.interface_id));
        tr.appendChild(name);

        const group = document.createElement('td');
        group.className = 'text-center';
        group.textContent = entry.domain_label || entry.domain || '';
        tr.appendChild(group);

        const position = document.createElement('td');
        position.className = 'text-center';
        const up = document.createElement('button');
        up.type = 'button';
        up.className = 'btn btn-sm btn-outline-secondary me-1';
        up.setAttribute('data-move', 'up');
        up.setAttribute('aria-label', t('moveUp', 'Move up'));
        up.setAttribute('title', t('moveUp', 'Move up'));
        up.appendChild(document.createTextNode('↑'));
        const down = document.createElement('button');
        down.type = 'button';
        down.className = 'btn btn-sm btn-outline-secondary';
        down.setAttribute('data-move', 'down');
        down.setAttribute('aria-label', t('moveDown', 'Move down'));
        down.setAttribute('title', t('moveDown', 'Move down'));
        down.appendChild(document.createTextNode('↓'));
        position.appendChild(up);
        position.appendChild(down);
        tr.appendChild(position);

        const shown = document.createElement('td');
        shown.className = 'text-center';
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.className = 'form-check-input';
        check.setAttribute('data-show', '1');
        check.checked = !entry.hidden;
        check.setAttribute('aria-label', t('showInSidebar', 'Show in sidebar'));
        shown.appendChild(check);
        tr.appendChild(shown);

        return tr;
    }

    function render() {
        const body = document.getElementById('navigationPrefsBody');
        if (!body) return;
        body.textContent = '';
        for (const entry of entries) {
            body.appendChild(rowFor(entry));
        }
    }

    function move(interfaceId, direction) {
        const index = entries.findIndex(function (e) { return e.interface_id === interfaceId; });
        const target = index + direction;
        if (index < 0 || target < 0 || target >= entries.length) return;
        const swapped = entries[index];
        entries[index] = entries[target];
        entries[target] = swapped;
        render();
        setStatus('');
    }

    function payload() {
        return entries.map(function (entry, index) {
            return { interface_id: entry.interface_id,
                     hidden: !entry.visibleNow,
                     position: index + 1 };
        });
    }

    function readDomState() {
        const body = document.getElementById('navigationPrefsBody');
        if (!body) return;
        for (const row of body.querySelectorAll('tr[data-interface]')) {
            const entry = entries.find(function (e) {
                return e.interface_id === row.getAttribute('data-interface');
            });
            if (!entry) continue;
            const check = row.querySelector('input[data-show]');
            entry.visibleNow = check ? check.checked : !entry.hidden;
        }
    }

    async function save() {
        readDomState();
        try {
            const response = await fetch('/api/preferences/navigation', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRFToken': csrfToken() },
                credentials: 'same-origin',
                body: JSON.stringify({ entries: payload() })
            });
            const body = await response.json();
            if (!response.ok || !body.success) {
                setStatus((body.error && body.error.message) || t('saveFailed', 'Saving failed.'));
                return;
            }
            setStatus(t('saved', 'Sidebar saved. It applies on your next navigation.'));
        } catch (error) {
            setStatus(t('saveFailed', 'Saving failed.'));
        }
    }

    async function reset() {
        try {
            const response = await fetch('/api/preferences/navigation', {
                method: 'DELETE',
                headers: { 'X-CSRFToken': csrfToken() },
                credentials: 'same-origin'
            });
            const body = await response.json();
            if (!response.ok || !body.success) {
                setStatus((body.error && body.error.message) || t('saveFailed', 'Saving failed.'));
                return;
            }
            await load();
            setStatus(t('resetDone', 'Back to the default sidebar.'));
        } catch (error) {
            setStatus(t('saveFailed', 'Saving failed.'));
        }
    }

    async function load() {
        const body = document.getElementById('navigationPrefsBody');
        if (!body) return;
        try {
            const response = await fetch('/api/preferences/navigation', {
                headers: { 'X-CSRFToken': csrfToken() },
                credentials: 'same-origin'
            });
            const data = await response.json();
            if (!data.success) throw new Error('unavailable');
            entries = data.entries.map(function (entry) {
                return Object.assign({}, entry, { visibleNow: !entry.hidden });
            });
            render();
            setStatus('');
        } catch (error) {
            body.textContent = '';
            const cell = document.createElement('td');
            cell.colSpan = 4;
            cell.className = 'text-muted';
            cell.textContent = t('loadFailed', 'The sidebar preferences could not be loaded.');
            const row = document.createElement('tr');
            row.appendChild(cell);
            body.appendChild(row);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        load();
        const body = document.getElementById('navigationPrefsBody');
        if (body) {
            body.addEventListener('click', function (event) {
                const button = event.target.closest('button[data-move]');
                if (!button) return;
                const row = button.closest('tr[data-interface]');
                if (!row) return;
                move(row.getAttribute('data-interface'),
                     button.getAttribute('data-move') === 'up' ? -1 : 1);
            });
        }
        const saveButton = document.getElementById('navigationPrefsSave');
        if (saveButton) saveButton.addEventListener('click', save);
        const resetButton = document.getElementById('navigationPrefsReset');
        if (resetButton) resetButton.addEventListener('click', reset);
    });
})();
