/**
 * "Jump to page" for server-rendered (template) pagination.
 *
 * Wires every ``.unified-pagination-jump-btn[data-endpoint]`` to its
 * ``#jumpToPageInput-<endpoint>``: the page number is clamped to
 * 1..data-total-pages and set as ``?page=`` on the current URL. Included by
 * the unified_pagination macro after its markup; safe to run more than once
 * (each control is wired once).
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
(function () {
    document.querySelectorAll('.unified-pagination-jump-btn[data-endpoint]').forEach(function (jumpBtn) {
        if (jumpBtn.hasAttribute('data-jump-wired')) return;
        var jumpInput = document.getElementById('jumpToPageInput-' + jumpBtn.getAttribute('data-endpoint'));
        if (!jumpInput) return;
        jumpBtn.setAttribute('data-jump-wired', '');

        var handleJump = function () {
            var targetPage = parseInt(jumpInput.value);
            var totalPages = parseInt(jumpInput.getAttribute('data-total-pages')) || 1;

            if (isNaN(targetPage) || targetPage < 1) {
                targetPage = 1;
            } else if (targetPage > totalPages) {
                targetPage = totalPages;
            }

            // Update current URL with new page number. The swap keeps
            // the shell (and the sidebar with it) on screen; only the
            // main content re-renders - a full load here used to redraw
            // the sidebar on every jump.
            var url = new URL(window.location.href);
            url.searchParams.set('page', targetPage);
            if (window.swapNavigate) { window.swapNavigate(url.toString()); }
            else { window.location.href = url.toString(); }
        };

            jumpBtn.addEventListener('click', handleJump);
            jumpInput.addEventListener('keypress', function (e) {
                if (e.key === 'Enter') {
                    handleJump();
                }
            });
        });

        // Wire "Show More" buttons across all paginated views
        document.querySelectorAll('.unified-pagination-show-more-btn').forEach(function (showMoreBtn) {
            if (showMoreBtn.hasAttribute('data-show-more-wired')) return;
            showMoreBtn.setAttribute('data-show-more-wired', '');

            showMoreBtn.addEventListener('click', function () {
                // If the page already has a unified-table Load More control, delegate to it
                var utLoadMoreBtn = document.querySelector('[data-ut-load-more] button');
                if (utLoadMoreBtn && !utLoadMoreBtn.closest('[data-ut-load-more]').hidden) {
                    utLoadMoreBtn.click();
                    return;
                }

                var targetPage = parseInt(showMoreBtn.getAttribute('data-next-page'), 10) || 2;
                var totalPages = parseInt(showMoreBtn.getAttribute('data-total-pages'), 10) || 1;
                var url = new URL(window.location.href);
                url.searchParams.set('page', targetPage);
                showMoreBtn.disabled = true;
                var icon = showMoreBtn.querySelector('i');
                if (icon) icon.className = 'spinner-border spinner-border-sm me-1';

                fetch(url.toString(), {
                    headers: { 'X-Requested-With': 'fetch' },
                    credentials: 'same-origin',
                })
                    .then(function (res) {
                        if (!res.ok) throw new Error('HTTP ' + res.status);
                        return res.text();
                    })
                    .then(function (html) {
                        var doc = new DOMParser().parseFromString(html, 'text/html');
                        var incomingRows = doc.querySelectorAll('.unified-table tbody tr, table.table tbody tr, table tbody tr');
                        var targetBody = document.querySelector('.unified-table tbody, table.table tbody, table tbody');
                        if (targetBody && incomingRows.length) {
                            incomingRows.forEach(function (row) {
                                targetBody.appendChild(document.importNode(row, true));
                            });
                        }
                        var newNextPage = targetPage + 1;
                        showMoreBtn.setAttribute('data-next-page', String(newNextPage));
                        showMoreBtn.setAttribute('data-current-page', String(targetPage));
                        if (newNextPage > totalPages) {
                            var wrap = showMoreBtn.closest('.unified-pagination-show-more');
                            if (wrap) wrap.style.display = 'none';
                        }
                        var container = showMoreBtn.closest('.unified-pagination-container');
                        if (container) {
                            container.setAttribute('data-current-page', String(targetPage));
                        }
                    })
                    .catch(function (err) {
                        console.error('Show More failed:', err);
                    })
                    .finally(function () {
                        showMoreBtn.disabled = false;
                        if (icon) icon.className = 'bi bi-arrow-down-circle me-1';
                    });
            });
        });

        if (!window.__utLoadMoreSyncWired) {
            window.__utLoadMoreSyncWired = true;
            document.addEventListener('ut:loadmore', function (e) {
                var detail = e.detail || {};
                var total = detail.total || 0;
                var shown = detail.shown || 0;
                if (shown >= total) {
                    document.querySelectorAll('.unified-pagination-show-more-btn').forEach(function (btn) {
                        var wrap = btn.closest('.unified-pagination-show-more');
                        if (wrap) wrap.style.display = 'none';
                    });
                }
            });
        }
    })();
