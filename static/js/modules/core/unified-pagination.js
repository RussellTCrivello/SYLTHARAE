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
})();
