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
 *     in the header (`aria-sort`) and the indicator on the active column; a
 *     third click returns the list to its default order;
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
 *     failed list looks the same as the server-drawn one;
 *   * an `append` table grows in place: the Load More control fetches the
 *     next batch of *this view* - same search, sort and filters, one page
 *     on - and appends only the new rows. The page never reloads, the rows
 *     already read stay exactly where they are, and their selection state
 *     with them.
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

        if (direction == null) {
            syncSortIndicators(table, null, null);
        }

        if (mode === 'client') {
            if (direction == null) return;
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
        // A third click clears the sort: the request goes out without sort
        // parameters, which every list route reads as its default order.
        const url = new URL(global.location.href);
        if (direction == null) {
            url.searchParams.delete(config.sortParam || 'sort');
            url.searchParams.delete(config.orderParam || 'order');
        } else {
            url.searchParams.set(config.sortParam || 'sort', key);
            url.searchParams.set(config.orderParam || 'order', direction);
        }
        url.searchParams.set(config.pageParam || 'page', '1');
        url.searchParams.delete('cursor');
        navigateToView(url.toString());
    }

    function navigateToView(url) {
        if (typeof global.swapNavigate === 'function') {
            return global.swapNavigate(url);
        }
        // A compatibility path for standalone use outside the application
        // shell. In the app, navigation-swap.js is present on every page.
        global.location.assign(url);
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
    // Column visibility: the reader chooses which columns stand
    // ------------------------------------------------------------------

    function applyColumnVisibility(table) {
        const hidden = hiddenColumns.get(table.id);
        const allRows = table.querySelectorAll('thead tr, tbody tr');
        allRows.forEach((row) => {
            if (!row.cells) return;
            Array.from(row.cells).forEach((cell, index) => {
                if (hidden && hidden.has(index)) {
                    cell.setAttribute('hidden', '');
                } else {
                    cell.removeAttribute('hidden');
                }
            });
        });
    }

    function wireColumnsMenu(table) {
        const menu = (wrapperOf(table) || table).querySelector('[data-ut-columns-menu]');
        if (!menu) return;
        menu.addEventListener('change', (event) => {
            const check = event.target.closest('.ut-columns-check');
            if (!check) return;
            const index = parseInt(check.getAttribute('data-ut-column-index'), 10);
            if (Number.isNaN(index)) return;
            let hidden = hiddenColumns.get(table.id);
            if (!hidden && !check.checked) {
                hidden = new Set();
                hiddenColumns.set(table.id, hidden);
            }
            if (!hidden) return;
            if (check.checked) {
                hidden.delete(index);
                if (!hidden.size) hiddenColumns.delete(table.id);
            } else {
                hidden.add(index);
            }
            applyColumnVisibility(table);
        });
    }

    // ------------------------------------------------------------------
    // Column filters: the control lives in the header, where the column is
    // ------------------------------------------------------------------

    function closeFilterPopovers(scope) {
        scope.querySelectorAll('[data-ut-filter-pop]:not([hidden])')
            .forEach((pop) => {
                pop.hidden = true;
                const btn = pop.parentElement
                    && pop.parentElement.querySelector('[data-ut-filter-btn]');
                if (btn) btn.setAttribute('aria-expanded', 'false');
            });
    }

    /** The view re-asked with the column's decision: same everything, the
     *  filter parameters changed, back on the first page. */
    function navigateWithFilter(table, changes) {
        const url = new URL(global.location.href);
        changes.forEach(([name, value]) => {
            if (value === null || value === undefined || value === '') {
                url.searchParams.delete(name);
            } else {
                url.searchParams.set(name, value);
            }
        });
        url.searchParams.set('page', '1');
        navigateToView(url.toString());
    }

    /** A client-mode table filters the rows it has, in place. Every active
     *  column filter is registered here and all of them are re-evaluated
     *  together on each change, so a values filter and a text search (or
     *  several of either) combine with AND - one filter can never silently
     *  lift another's rows back in. */
    const clientFilters = new Map();   // table -> Map(key -> filter state)

    function clientFilterKey(config) {
        return config.param || config.row_attr || '';
    }

    function cellValue(row, config) {
        const cell = row.querySelector('[' + config.row_attr + ']');
        return cell ? (cell.getAttribute(config.row_attr) || '').toLowerCase() : null;
    }

    function rowPassesFilter(row, state) {
        const value = cellValue(row, state.config);
        if (value === null) return true;   // the column is not on this row
        if (state.kind === 'text') {
            return !state.text || value.indexOf(state.text) >= 0;
        }
        if (!state.values || !state.values.length) return true;
        const chosen = state.values.indexOf(value) >= 0;
        return state.hide ? !chosen : chosen;
    }

    function evaluateClientFilters(table) {
        const active = clientFilters.get(table);
        table.querySelectorAll('tbody tr').forEach((row) => {
            if (row.hasAttribute(STATE_ATTR)) return;
            let passes = true;
            if (active) {
                for (const state of active.values()) {
                    if (!rowPassesFilter(row, state)) {
                        passes = false;
                        break;
                    }
                }
            }
            row.classList.toggle('ut-row-filtered', !passes);
        });
    }

    function setClientFilter(table, config, state) {
        let active = clientFilters.get(table);
        if (!active) {
            active = new Map();
            clientFilters.set(table, active);
        }
        const key = clientFilterKey(config);
        if (state === null) active.delete(key);
        else active.set(key, state);
        evaluateClientFilters(table);
    }

    function applyColumnFilter(table, pop, mode) {
        let config;
        try {
            config = JSON.parse(pop.getAttribute('data-ut-filter-config') || '{}');
        } catch (_error) {
            config = {};
        }
        const kind = config.kind;
        const changes = [];

        if (kind === 'values') {
            const chosen = Array.from(pop.querySelectorAll('.ut-filter-check:checked'))
                .map((check) => (check.getAttribute('value') || '').toLowerCase())
                .filter(Boolean);
            const joined = chosen.join(',');
            const client = configOf(table).mode === 'client';
            if (mode === 'clear') {
                changes.push([config.param, null], [config.exclude_param, null]);
                if (client) setClientFilter(table, config, null);
            } else if (mode === 'hide') {
                changes.push([config.exclude_param, joined], [config.param, null]);
                if (client) {
                    setClientFilter(table, config,
                        { kind: 'values', config, values: chosen, hide: true });
                }
            } else {   // show only the chosen values
                changes.push([config.param, joined], [config.exclude_param, null]);
                if (client) {
                    setClientFilter(table, config,
                        { kind: 'values', config, values: chosen, hide: false });
                }
            }
        } else if (kind === 'number') {
            const min = pop.querySelector('[data-ut-filter-min]');
            const max = pop.querySelector('[data-ut-filter-max]');
            changes.push(
                [config.min_param, min ? min.value.trim() : null],
                [config.max_param, max ? max.value.trim() : null]);
        } else if (kind === 'date') {
            const from = pop.querySelector('[data-ut-filter-from]');
            const to = pop.querySelector('[data-ut-filter-to]');
            changes.push(
                [config.from_param, from ? from.value.trim() : null],
                [config.to_param, to ? to.value.trim() : null]);
        } else if (kind === 'text') {
            const input = pop.querySelector('[data-ut-filter-text]');
            if (mode === 'clear' && input) input.value = '';   // clear means clear
            const value = input ? input.value.trim() : '';
            changes.push([config.param, value || null]);
            if (configOf(table).mode === 'client') {
                setClientFilter(table, config,
                    value ? { kind: 'text', config, text: value.toLowerCase() } : null);
            }
        }

        if (configOf(table).mode === 'client') {
            return;   // a client table never re-asks: the rows are filtered here
        }
        navigateWithFilter(table, changes);
    }

    function wireColumnFilters(table, scope) {
        // Enter in a column's search field applies that column's filter.
        scope.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            const input = event.target.closest('[data-ut-filter-text]');
            if (!input) return;
            const pop = input.closest('[data-ut-filter-pop]');
            const apply = pop && pop.querySelector('[data-ut-filter-apply]');
            if (apply) {
                event.preventDefault();
                apply.click();
            }
        });
        scope.addEventListener('click', (event) => {
            const btn = event.target.closest('[data-ut-filter-btn]');
            const pop = event.target.closest('[data-ut-filter-pop]');
            if (btn) {
                const target = btn.parentElement
                    && btn.parentElement.querySelector('[data-ut-filter-pop]');
                if (!target) return;
                const opening = target.hidden;
                closeFilterPopovers(scope);
                target.hidden = !opening;
                btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
                return;
            }
            if (pop) {
                let mode = null;
                if (event.target.closest('[data-ut-filter-only]')) mode = 'only';
                else if (event.target.closest('[data-ut-filter-hide]')) mode = 'hide';
                else if (event.target.closest('[data-ut-filter-clear]')) mode = 'clear';
                else if (event.target.closest('[data-ut-filter-apply]')) mode = 'apply';
                if (!mode) return;
                applyColumnFilter(table, pop, mode === 'apply' ? 'only' : mode);
                if (mode !== 'clear') pop.hidden = true;
                return;
            }
            // A click anywhere else in the table closes a standing popover.
            closeFilterPopovers(scope);
        });
    }

    // ------------------------------------------------------------------
    // Progressive loading: the table grows in place
    // ------------------------------------------------------------------

    /** Columns the reader hid, per table id: a set of cell indexes. */
    const hiddenColumns = new Map();

    /** Registered per-table load-more handlers: table id -> function(done). */
    const loadMoreHandlers = new Map();
    /** Tables mid-fetch, so a double click cannot ask for a batch twice. */
    const loadMoreBusy = new WeakSet();

    function loadMoreBlock(table) {
        return table.closest('[' + WRAPPER_ATTR + ']')
            ?.querySelector('[data-ut-load-more]') || null;
    }

    /** The next page of THIS view, as the same URL one page on. Everything
     *  the reader chose - search, sort, filters, batch size - rides along in
     *  the query string; only the page number moves. */
    function nextPageUrl(table, config) {
        const url = new URL(global.location.href);
        const pageParam = config.pageParam || 'page';
        const current = parseInt(loadMoreBlock(table)?.getAttribute('data-page'), 10)
            || parseInt(url.searchParams.get(pageParam), 10) || 1;
        url.searchParams.set(pageParam, String(current + 1));
        url.searchParams.delete('cursor');
        return url.toString();
    }

    function adoptAppendedRows(table, doc, config) {
        // The server rendered the next batch as a whole page; this table's
        // body is the only part we take. Nothing else on the page moves.
        const incoming = table.id
            ? doc.querySelector('table#' + CSS.escape(table.id) + ' tbody')
            : doc.querySelector('[' + WRAPPER_ATTR + '] table tbody');
        const body = table.tBodies[0];
        if (!incoming || !body) return 0;
        const fragment = document.createDocumentFragment();
        Array.from(incoming.rows).forEach((row) => fragment.appendChild(
            global.document.importNode(row, true)));
        const count = fragment.children.length;
        // A state row (empty/filtered) must never be adopted; the list has
        // rows, so any standing state row goes first.
        body.querySelectorAll('[' + STATE_ATTR + ']').forEach((node) => node.remove());
        applyColumnVisibility(table);
        body.appendChild(fragment);
        return count;
    }

    function updateLoadMoreBlock(table, appended, config) {
        const block = loadMoreBlock(table);
        if (!block) return;
        const shown = parseInt(block.getAttribute('data-shown'), 10) || 0;
        const total = parseInt(block.getAttribute('data-total'), 10) || 0;
        const page = parseInt(block.getAttribute('data-page'), 10) || 1;
        const totalPages = parseInt(block.getAttribute('data-total-pages'), 10) || 1;
        const nowShown = Math.min(total, shown + appended);
        block.setAttribute('data-shown', String(nowShown));
        block.setAttribute('data-page', String(page + 1));
        // The wrapper's counters, toolbar and block alike: "Showing X of Y"
        // must move with every batch, not only the block's own copy.
        const wrapper = wrapperOf(table);
        if (wrapper) {
            wrapper.querySelectorAll('[data-ut-shown]').forEach((node) => {
                node.textContent = global.UnifiedTable.fmt.number(nowShown);
            });
            wrapper.querySelectorAll('[data-ut-total]').forEach((node) => {
                node.textContent = global.UnifiedTable.fmt.number(total);
            });
        }
        if (page + 1 >= totalPages + 1 || nowShown >= total) {
            block.hidden = true;
        }
        if (wrapper) {
            wrapper.dispatchEvent(new CustomEvent('ut:loadmore', {
                bubbles: true,
                detail: { table: table.id, appended: appended, shown: nowShown, total: total }
            }));
        }
    }

    function requestNextBatch(table) {
        const block = loadMoreBlock(table);
        if (!block || loadMoreBusy.has(table)) return;
        if (typeof loadMoreHandlers.get(table.id) === 'function') {
            // The page owns its loading (an API table): hand over, it reports
            // back through appendRows.
            loadMoreHandlers.get(table.id)(table);
            return;
        }
        loadMoreBusy.add(table);
        block.setAttribute('data-loading', '');
        const config = configOf(table);
        fetch(nextPageUrl(table, config), {
            headers: { 'X-Requested-With': 'fetch' },
            credentials: 'same-origin',
        })
            .then((response) => {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.text();
            })
            .then((html) => {
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const appended = adoptAppendedRows(table, doc, config);
                updateLoadMoreBlock(table, appended, config);
            })
            .catch(() => {
                delete block.dataset.loading;
                const error = block.querySelector('[data-ut-load-error]');
                if (error) error.hidden = false;
            })
            .then(() => {
                loadMoreBusy.delete(table);
                delete block.dataset.loading;
            });
    }

    // ------------------------------------------------------------------
    // Event wiring (delegated on the table: re-rendered rows need no rebinding)
    // ------------------------------------------------------------------

    function enhance(table) {
        if (!table || enhanced.has(table)) return;
        enhanced.add(table);

        // One click scope for everything the table owns: the header sorting
        // AND the Load More control. The scope is the wrapper, not the table,
        // because the control is the table's sibling inside it - a listener
        // on the table alone can never hear it.
        const scope = wrapperOf(table) || table;

        scope.addEventListener('click', (event) => {
            // A click inside a column's filter control is not a sort: the
            // control lives in the same header as the sort button.
            if (event.target.closest('[data-ut-filter-btn], [data-ut-filter-pop]')) return;
            const button = event.target.closest('.ut-sort-btn');
            const header = event.target.closest('th.ut-sortable');
            const target = button ? button.closest('th') || header : header;
            if (!target) return;
            const key = target.getAttribute(SORT_ATTR);
            if (!key) return;
            const type = target.getAttribute('data-ut-type') || 'text';
            const isActive = target.classList.contains('ut-sorted');
            // Three states for every column: default -> the column's first
            // direction (numbers and dates lead with descending) -> the flip
            // -> default. The active header knows which state it shows.
            const first = firstDirectionFor(type);
            let direction;
            if (!isActive) {
                direction = first;
            } else if (target.classList.contains(first === 'asc' ? 'ut-sorted-asc' : 'ut-sorted-desc')) {
                direction = flipped(first);
            } else if (target.classList.contains(first === 'asc' ? 'ut-sorted-desc' : 'ut-sorted-asc')) {
                direction = null;   // back to the list's own order
            } else {
                direction = flipped(first);
            }
            applySort(table, key, direction);
        });

        scope.addEventListener('click', (event) => {
            const control = event.target.closest('.ut-sort-btn, [data-ut-load-more], [data-ut-load-more] *');
            if (!control) return;
            if (control.closest('[data-ut-load-more]') && !control.closest('.ut-sort-btn')) {
                const error = control.closest('[data-ut-load-more]').querySelector('[data-ut-load-error]');
                if (error) error.hidden = true;
                requestNextBatch(table);
            }
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

        wireColumnFilters(table, scope);
        wireColumnsMenu(table);
        applyColumnVisibility(table);
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
        /** Reflect the server's confirmed sort after a fetch. A null key
         *  clears the indicators: the list is back in its default order. */
        setSort(tableId, key, direction) {
            const table = tableOf(tableId);
            if (table) syncSortIndicators(table, key, direction);
        },
        /** The page's own answer to Load More (an API-fed table). */
        onLoadMore(tableId, handler) {
            if (typeof handler === 'function') {
                loadMoreHandlers.set(tableId, handler);
            } else {
                loadMoreHandlers.delete(tableId);
            }
        },
        /** A page that fetched its own next batch hands the rows over here:
         *  one adoption, counter and visibility updated. */
        appendRows(tableId, rows, total) {
            const table = tableOf(tableId);
            if (!table) return 0;
            const settings = rows && typeof rows === 'object' && !Array.isArray(rows)
                ? rows : { html: rows, total: total };
            const body = table.tBodies[0];
            if (!body) return 0;
            const html = Array.isArray(settings.html) ? settings.html.join('') : String(settings.html || '');
            body.querySelectorAll('[' + STATE_ATTR + ']').forEach((node) => node.remove());
            // The page's new total counts FIRST: the counter update below must
            // see the size of the list this batch belongs to, not the stale one.
            if (typeof settings.total === 'number') {
                const block = loadMoreBlock(table);
                if (block) block.setAttribute('data-total', String(settings.total));
            }
            body.insertAdjacentHTML('beforeend', html);
            const appended = Array.isArray(settings.html) ? settings.html.length
                : (body.rows.length - (parseInt(loadMoreBlock(table)?.getAttribute('data-shown'), 10) || 0));
            syncSelection(table);
            updateLoadMoreBlock(table, Math.max(0, appended), configOf(table));
            return Math.max(0, appended);
        },
        /** A page that fetched its own FIRST page (an API table) reports the
         *  real shown/total here: the Load More block's counters and
         *  visibility follow the same rules as a server-rendered batch. */
        syncLoadMore(tableId, shown, total, pageSize) {
            const table = tableOf(tableId);
            if (!table) return;
            const block = loadMoreBlock(table);
            if (!block) return;
            const size = Math.max(1, pageSize
                || (parseInt(block.getAttribute('data-page-size'), 10) || 0));
            block.setAttribute('data-shown', String(Math.max(0, shown)));
            block.setAttribute('data-total', String(Math.max(0, total)));
            const counter = block.querySelector('[data-ut-shown]');
            if (counter) counter.textContent = global.UnifiedTable.fmt.number(Math.max(0, shown));
            const totalEl = block.querySelector('[data-ut-total]');
            if (totalEl) totalEl.textContent = global.UnifiedTable.fmt.number(Math.max(0, total));
            const page = parseInt(block.getAttribute('data-page'), 10) || 1;
            const totalPages = Math.max(1, Math.ceil(Math.max(0, total) / size));
            block.setAttribute('data-total-pages', String(totalPages));
            block.hidden = !(page < totalPages && shown < total);
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
