/**
 * Unified table — the one behaviour every list table shares.
 *
 * Seven list interfaces (File Library, Keywords, Words, Categories, Words in
 * Category, Sources, Sides) render their records through the `record_table`
 * component (templates/components/table.html) and drive it with this module
 * and nothing else. What used to be a sort dropdown beside the table, a
 * bespoke header script and a different renderer per page is now built into
 * the table itself:
 *
 *   * clicking a sortable header sorts the table - with the direction shown
 *     in the header (`aria-sort`) and the indicator on the active column;
 *   * in `client` mode the loaded rows are reordered in place (one reflow,
 *     via a document fragment - the rows are moved, not rebuilt, so their
 *     listeners and identity survive);
 *   * in `server` mode the page is told (`UnifiedTable.onSort`) or the URL is
 *     rewritten with the configured sort/order parameters, resetting to the
 *     first page;
 *   * the header checkbox and the row checkboxes agree with each other, and a
 *     chosen row is marked (`tr.ut-selected`) so "selected" is visible where
 *     the selection is;
 *   * pages that re-render their own bodies do it through `renderRows`, so a
 *     page of rows is one insertion, and through `states`, so an empty or
 *     failed list looks the same as the server-drawn one.
 *
 * This file renders no markup of its own beyond those states and never talks
 * to the network: sorting, selection and rendering here, business in the
 * pages. CSP-safe: no eval, no inline script, no Function constructor.
 */
(function (global) {
    'use strict';

    const WRAPPER_ATTR = 'data-unified-table';
    const SORT_ATTR = 'data-ut-sort';
    const VALUE_ATTR = 'data-ut-value';
    const STATE_ATTR = 'data-ut-state';
    const SELECTED_CLASS = 'ut-selected';

    /** Registered per-table server-sort handlers: table id -> function(key, dir). */
    const sortHandlers = new Map();
    /** Initialised wrappers, so `init` is idempotent. */
    const enhanced = new WeakSet();

    // ------------------------------------------------------------------
    // Small shared helpers
    // ------------------------------------------------------------------

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function tableOf(target) {
        const element = typeof target === 'string' ? document.getElementById(target) : target;
        if (!element) return null;
        return element.tagName === 'TABLE' ? element : element.querySelector('table');
    }

    function wrapperOf(table) {
        return table ? table.closest('[' + WRAPPER_ATTR + ']') : null;
    }

    function configOf(table) {
        const wrapper = wrapperOf(table);
        if (!wrapper) return {};
        try {
            return JSON.parse(wrapper.getAttribute(WRAPPER_ATTR) || '{}') || {};
        } catch (error) {
            console.warn('UnifiedTable: unreadable table config', error);
            return {};
        }
    }

    function columnsOf(table) {
        const head = table.tHead;
        return head ? Array.from(head.rows[0] ? head.rows[0].cells : []) : [];
    }

    function sortableColumnIndex(table, key) {
        return columnsOf(table).findIndex(
            (th) => th.classList.contains('ut-sortable') && th.getAttribute(SORT_ATTR) === key);
    }

    // ------------------------------------------------------------------
    // Sorting
    // ------------------------------------------------------------------

    /** The first direction a column takes. Text reads top-down A→Z; a count
     *  or a date is usually asked for "most first", so it starts descending. */
    function firstDirectionFor(type) {
        return (type === 'number' || type === 'date') ? 'desc' : 'asc';
    }

    function flipped(direction) {
        return direction === 'asc' ? 'desc' : 'asc';
    }

    function cellValue(row, index, type) {
        const cell = row.cells[index];
        if (!cell) return null;
        const raw = cell.hasAttribute(VALUE_ATTR)
            ? cell.getAttribute(VALUE_ATTR)
            : (cell.textContent || '').trim();
        if (type === 'number') {
            const value = parseFloat(String(raw).replace(/[^\d.eE+-]/g, ''));
            return Number.isFinite(value) ? value : null;
        }
        if (type === 'date') {
            const time = Date.parse(String(raw));
            return Number.isNaN(time) ? null : time;
        }
        return raw;
    }

    function compareValues(a, b, type, direction) {
        const bothNil = (a == null && b == null);
        if (bothNil) return 0;
        if (a == null) return 1;          // nothing sorts after something
        if (b == null) return -1;
        let result;
        if (type === 'number' || type === 'date') {
            result = a < b ? -1 : (a > b ? 1 : 0);
        } else {
            // Numeric collation so "file 2" precedes "file 10" in a text column.
            result = String(a).localeCompare(String(b), undefined,
                { numeric: true, sensitivity: 'base' });
        }
        return direction === 'desc' ? -result : result;
    }

    /** Reorder the loaded rows in place. One fragment, one reflow; rows keep
     *  their identity, listeners and selection state. */
    function sortRowsInPlace(table, key, direction) {
        const index = sortableColumnIndex(table, key);
        if (index < 0 || !table.tBodies.length) return false;
        const type = (columnsOf(table)[index].getAttribute('data-ut-type')) || 'text';
        const body = table.tBodies[0];
        const rows = Array.from(body.rows).filter((row) => !row.hasAttribute(STATE_ATTR));
        if (rows.length < 2) return true;

        const keyed = rows.map((row) => ({ row, value: cellValue(row, index, type) }));
        keyed.sort((a, b) => compareValues(a.value, b.value, type, direction));

        const fragment = document.createDocumentFragment();
        keyed.forEach((entry) => fragment.appendChild(entry.row));
        body.insertBefore(fragment, body.querySelector('[' + STATE_ATTR + ']'));
        return true;
    }

    /** Reflect a sort decision in the headers: indicator, class, aria-sort. */
    function syncSortIndicators(table, activeKey, activeDir) {
        columnsOf(table).forEach((th) => {
            if (!th.classList.contains('ut-sortable')) return;
            const key = th.getAttribute(SORT_ATTR);
            const icon = th.querySelector('.ut-sort-btn .bi, .ut-sort-btn i');
            const isActive = key === activeKey;
            th.classList.toggle('ut-sorted', isActive);
            th.classList.toggle('ut-sorted-asc', isActive && activeDir === 'asc');
            th.classList.toggle('ut-sorted-desc', isActive && activeDir === 'desc');
            if (isActive) {
                th.setAttribute('aria-sort', activeDir === 'asc' ? 'ascending' : 'descending');
            } else {
                th.removeAttribute('aria-sort');
            }
            if (icon) {
                icon.className = isActive
                    ? (activeDir === 'asc' ? 'bi bi-sort-up' : 'bi bi-sort-down')
                    : 'bi bi-arrow-down-up';
            }
        });
    }

    function applySort(table, key, direction) {
        const config = configOf(table);
        const mode = config.mode || 'server';

        if (mode === 'client') {
            sortRowsInPlace(table, key, direction);
            syncSortIndicators(table, key, direction);
            const wrapper = wrapperOf(table);
            if (wrapper) {
                wrapper.dispatchEvent(new CustomEvent('ut:sort', {
                    bubbles: true,
                    detail: { table: table.id, key, direction }
                }));
            }
            return;
        }

        syncSortIndicators(table, key, direction);
        const handler = sortHandlers.get(table.id);
        if (typeof handler === 'function') {
            handler(key, direction);
            return;
        }
        // Default server behaviour: this table's list, re-asked in the new
        // order, back on the first page, with every other choice preserved.
        const url = new URL(global.location.href);
        url.searchParams.set(config.sortParam || 'sort', key);
        url.searchParams.set(config.orderParam || 'order', direction);
        url.searchParams.set(config.pageParam || 'page', '1');
        url.searchParams.delete('cursor');
        global.location.assign(url.toString());
    }

    // ------------------------------------------------------------------
    // Selection: the header checkbox and the rows agree
    // ------------------------------------------------------------------

    function rowChecks(table) {
        if (!table.tBodies.length) return [];
        return Array.from(table.tBodies[0].querySelectorAll('input[type="checkbox"]'))
            .filter((check) => check.closest('td'));
    }

    function checkAllOf(table) {
        return columnsOf(table)
            .map((th) => th.querySelector('input[type="checkbox"]'))
            .find(Boolean);
    }

    function syncSelection(table) {
        const checks = rowChecks(table);
        const master = checkAllOf(table);
        const chosen = checks.filter((check) => check.checked);

        checks.forEach((check) => {
            const row = check.closest('tr');
            if (row) row.classList.toggle(SELECTED_CLASS, check.checked);
        });

        if (master) {
            const total = checks.length;
            const count = chosen.length;
            master.checked = total > 0 && count === total;
            master.indeterminate = count > 0 && count < total;
        }
    }

    // ------------------------------------------------------------------
    // Event wiring (delegated on the table: re-rendered rows need no rebinding)
    // ------------------------------------------------------------------

    function enhance(table) {
        if (!table || enhanced.has(table)) return;
        enhanced.add(table);

        table.addEventListener('click', (event) => {
            const button = event.target.closest('.ut-sort-btn');
            const header = event.target.closest('th.ut-sortable');
            const target = button ? button.closest('th') || header : header;
            if (!target) return;
            const key = target.getAttribute(SORT_ATTR);
            if (!key) return;
            const type = target.getAttribute('data-ut-type') || 'text';
            const isActive = target.classList.contains('ut-sorted');
            const direction = isActive
                ? flipped(target.classList.contains('ut-sorted-asc') ? 'asc' : 'desc')
                : firstDirectionFor(type);
            applySort(table, key, direction);
        });

        table.addEventListener('change', (event) => {
            const target = event.target;
            if (!target || target.type !== 'checkbox') return;
            if (target.classList.contains('ut-check-all')) {
                // The page's own `data-on-change` on this box, if it has one,
                // hears the same event through the document delegate and does
                // whatever else the page needs (toolbar counts); the rows are
                // flipped once, here, so both paths see the same state.
                rowChecks(table).forEach((check) => {
                    check.checked = target.checked;
                });
                syncSelection(table);
                return;
            }
            if (target.closest('td')) syncSelection(table);
        });

        syncSelection(table);
    }

    function init(root) {
        const scope = root || document;
        scope.querySelectorAll('[' + WRAPPER_ATTR + '] table').forEach(enhance);
    }

    // ------------------------------------------------------------------
    // Rendering: one body, one insertion; states that match the server's
    // ------------------------------------------------------------------

    /** Replace (or append to) a table body with pre-rendered row HTML.
     *  `rows` is an array of `<tr>…</tr>` strings: joined and inserted once,
     *  so a page of a hundred rows costs one reflow. */
    function renderRows(target, rows, options) {
        const body = typeof target === 'string'
            ? (tableOf(target) || {}).tBodies && tableOf(target).tBodies[0]
            : target;
        if (!body) return;
        const settings = options || {};
        const html = Array.isArray(rows) ? rows.join('') : String(rows || '');
        if (settings.replace !== false) {
            body.innerHTML = html;
        } else {
            body.insertAdjacentHTML('beforeend', html);
        }
        const table = body.closest('table');
        if (table) syncSelection(table);
    }

    /** The stand-in rows for an empty, loading or failed list - the same
     *  markup the server renders through the states component, so a list
     *  looks the same however it was filled. Sort skips these rows. */
    function stateRow(columns, inner, state) {
        return '<tr ' + STATE_ATTR + '="' + state + '">' +
            '<td colspan="' + columns + '" class="ut-state-cell">' + inner + '</td></tr>';
    }

    const states = {
        empty(columns, options) {
            const opts = options || {};
            const icon = opts.filtered ? 'bi-funnel' : (opts.icon || 'bi-inbox');
            const state = opts.filtered ? 'filtered' : 'empty';
            const inner = '<div class="empty-state" data-state="' + state + '" role="status">' +
                '<i class="bi ' + icon + '" aria-hidden="true"></i>' +
                (opts.message ? '<p>' + escapeHtml(opts.message) + '</p>' : '') +
                (opts.actionLabel
                    ? (opts.actionHref
                        ? '<a class="btn btn-primary" href="' + escapeHtml(opts.actionHref) + '">' +
                          '<i class="bi bi-plus-circle me-2" aria-hidden="true"></i>' +
                          escapeHtml(opts.actionLabel) + '</a>'
                        : '<button type="button" class="btn btn-primary"' +
                          (opts.actionOnclick ? ' data-on-click="' + escapeHtml(opts.actionOnclick) + '"' : '') + '>' +
                          '<i class="bi bi-plus-circle me-2" aria-hidden="true"></i>' +
                          escapeHtml(opts.actionLabel) + '</button>')
                    : '') +
                '</div>';
            return stateRow(columns, inner, state);
        },
        loading(columns, message) {
            const inner = '<div class="empty-state" data-state="loading" role="status">' +
                '<span class="spinner-border text-primary" role="presentation" aria-hidden="true"></span>' +
                (message ? '<p>' + escapeHtml(message) + '</p>' : '') +
                '</div>';
            return stateRow(columns, inner, 'loading');
        },
        error(columns, message, retryOnclick) {
            const inner = '<div class="empty-state" data-state="error" role="alert">' +
                '<i class="bi bi-exclamation-octagon text-danger" aria-hidden="true"></i>' +
                '<p>' + escapeHtml(message || 'Something went wrong.') + '</p>' +
                (retryOnclick
                    ? '<button type="button" class="btn btn-primary" data-on-click="' +
                      escapeHtml(retryOnclick) + '"><i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Try again</button>'
                    : '') +
                '</div>';
            return stateRow(columns, inner, 'error');
        }
    };

    // ------------------------------------------------------------------
    // Formatting: the same numbers, bytes and dates in every cell
    // ------------------------------------------------------------------

    const numberFormat = new Intl.NumberFormat();
    const fmt = {
        escapeHtml,
        number(value) {
            const parsed = typeof value === 'number' ? value : parseFloat(value);
            return Number.isFinite(parsed) ? numberFormat.format(parsed) : '0';
        },
        bytes(value) {
            const size = typeof value === 'number' ? value : parseFloat(value);
            if (!Number.isFinite(size)) return '—';
            if (size >= 1073741824) return (size / 1073741824).toFixed(1) + ' GB';
            if (size >= 1048576) return (size / 1048576).toFixed(1) + ' MB';
            if (size >= 1024) return Math.round(size / 1024) + ' KB';
            return Math.max(0, Math.round(size)) + ' B';
        },
        date(value) {
            if (!value) return '—';
            const parsed = value instanceof Date ? value : new Date(value);
            if (Number.isNaN(parsed.getTime())) return String(value);
            const month = String(parsed.getMonth() + 1).padStart(2, '0');
            const day = String(parsed.getDate()).padStart(2, '0');
            return parsed.getFullYear() + '-' + month + '-' + day;
        },
        badge(text, tone, icon) {
            return '<span class="ut-badge ut-badge-' + escapeHtml(tone || 'neutral') + '">' +
                (icon ? '<i class="bi ' + escapeHtml(icon) + '" aria-hidden="true"></i>' : '') +
                escapeHtml(text) + '</span>';
        },
        stars(value, outOf) {
            const max = outOf || 5;
            const filled = Math.max(0, Math.min(max, Math.round((Number(value) || 0) * max)));
            let html = '<span class="ut-stars" aria-hidden="true">';
            for (let i = 0; i < max; i += 1) {
                html += '<i class="bi ' + (i < filled ? 'bi-star-fill' : 'bi-star ut-stars-off') + '"></i>';
            }
            html += '</span>';
            return html;
        }
    };

    // ------------------------------------------------------------------
    // Public surface
    // ------------------------------------------------------------------

    global.UnifiedTable = {
        init,
        enhance,
        renderRows,
        states,
        fmt,
        /** The page's answer to a header sort on a server-sorted table. */
        onSort(tableId, handler) {
            if (typeof handler === 'function') {
                sortHandlers.set(tableId, handler);
            } else {
                sortHandlers.delete(tableId);
            }
        },
        /** Reflect the server's confirmed sort after a fetch. */
        setSort(tableId, key, direction) {
            const table = tableOf(tableId);
            if (table) syncSortIndicators(table, key, direction);
        },
        /** Re-read selection after the page replaced rows itself. */
        refresh(tableId) {
            const table = tableOf(tableId);
            if (table) syncSelection(table);
        },
        /** For a page that sorts its own data before rendering: the header
         *  order still follows this call. */
        sortClientSide(tableId, key, direction) {
            const table = tableOf(tableId);
            if (!table) return false;
            const done = sortRowsInPlace(table, key, direction);
            if (done) syncSortIndicators(table, key, direction);
            return done;
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => init());
    } else {
        init();
    }
}(window));
