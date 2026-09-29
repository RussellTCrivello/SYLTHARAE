/**
 * The table toolbar's export menu: this view, every row of it, the reader's
 * chosen columns, as CSV or Excel - plus print.
 *
 * One listener serves every table on the page. The download is a plain link
 * to /api/export/<interface> that carries the view's own query string (the
 * search, sort and filters the table shows), so what lands on disk is the
 * view on screen with the page numbers removed.
 */
(function () {
    'use strict';

    // Paging coordinates are the one thing an export must not inherit:
    // the export is the whole view, not one page of it.
    const PARAMS_TO_DROP = ['page', 'per_page', 'limit', 'cursor', 'start_position'];

    function chosenColumns(menu) {
        return Array.from(menu.querySelectorAll('.ut-export-column-check:checked'))
            .map(function (box) { return box.getAttribute('data-export-column'); })
            .filter(Boolean);
    }

    function exportUrl(menu, format) {
        const params = new URLSearchParams(window.location.search);
        PARAMS_TO_DROP.forEach(function (name) { params.delete(name); });
        params.set('format', format);
        params.set('columns', chosenColumns(menu).join(','));
        return '/api/export/' + encodeURIComponent(menu.getAttribute('data-export-interface'))
            + '?' + params.toString();
    }

    function onTableClick(event) {
        const target = event.target.closest('[data-export-download], [data-export-print]');
        if (!target) return;
        const menu = target.closest('[data-export-interface]');
        if (!menu) return;
        event.preventDefault();
        if (target.hasAttribute('data-export-print')) {
            window.print();
            return;
        }
        window.location.href = exportUrl(menu, target.getAttribute('data-export-download'));
    }

    document.addEventListener('click', onTableClick);
})();
