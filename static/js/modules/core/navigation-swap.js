/**
 * Static sidebar navigation (owner requirement: "the sidebar must stay
 * fixed without refreshing").
 *
 * The application is server-rendered: every navigation used to redraw the
 * whole page, sidebar included. This module intercepts in-application link
 * clicks and back/forward navigation, fetches the target page, and swaps
 * ONLY the main content area - the sidebar element is never replaced, so
 * it keeps its scroll position, its collapsed/expanded state and its focus
 * between navigations. The sidebar's open/closed state is also persisted
 * per session and restored on a real load (first visit, full reload,
 * fallback), so even then it does not visibly reset.
 *
 * Discipline:
 * - Only same-origin GET navigations of plain links are swapped. Forms,
 *   downloads, new tabs/targets, modified clicks (ctrl/shift/...), external
 *   URLs and opted-out links (data-swap="off") take the normal path.
 * - Page scripts: the page's classic scripts are re-created so their
 *   functions exist for the new content's declarative handlers; the module
 *   scripts the app ships re-run their per-page init through the universal
 *   initializer (ES modules are cached, so re-appending them is a no-op -
 *   initializePage() is invoked explicitly instead).
 * - Failure fallback: any fetch/parse error falls back to a full
 *   navigation. The swap is an enhancement, never a dependency.
 */

(function () {
    'use strict';

    const MAIN_ID = 'mainContent';
    const STATE_KEY = 'syltherae.sidebar-state';

    // ---------------------------------------------------------------- state

    function persistSidebarState() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        try {
            sessionStorage.setItem(STATE_KEY, JSON.stringify({
                scroll: sidebar.scrollTop,
                open: sidebar.classList.contains('show'),
                at: Date.now()
            }));
        } catch (e) { /* storage unavailable: nothing to restore later */ }
    }

    function restoreSidebarState() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        let state = null;
        try {
            state = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null');
        } catch (e) { state = null; }
        if (!state) return;
        if (typeof state.scroll === 'number') sidebar.scrollTop = state.scroll;
        const backdrop = document.getElementById('sidebarBackdrop');
        if (state.open && window.innerWidth < 992 && backdrop) {
            sidebar.classList.add('show');
            backdrop.classList.add('show');
        }
    }

    // ------------------------------------------------------------- helpers

    function sameOriginLink(anchor) {
        if (!anchor || anchor.target && anchor.target !== '_self') return null;
        if (anchor.hasAttribute('download')) return null;
        if (anchor.closest('[data-swap="off"]')) return null;
        const href = anchor.getAttribute('href');
        if (!href || href.startsWith('#')) return null;
        let url;
        try {
            url = new URL(anchor.href, window.location.href);
        } catch (e) { return null; }
        if (url.origin !== window.location.origin) return null;
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
        return url;
    }

    function updateSidebarActiveState() {
        const links = document.querySelectorAll('.sidebar-nav-link');
        const path = window.location.pathname;
        let matched = null;
        for (const link of links) {
            link.classList.remove('active');
            link.removeAttribute('aria-current');
            try {
                const hrefPath = new URL(link.getAttribute('href') || '',
                                         window.location.origin).pathname;
                if (path === hrefPath) matched = link;
            } catch (e) { /* keep looking */ }
        }
        if (matched) {
            matched.classList.add('active');
            matched.setAttribute('aria-current', 'page');
        }
    }

    function runPageScripts(newDoc) {
        // Classic scripts re-execute when re-created; module scripts are
        // cached by URL (re-appending is a no-op), so the page modules'
        // init is invoked explicitly afterwards.
        const scripts = Array.from(newDoc.querySelectorAll('script'));
        for (const old of scripts) {
            const script = document.createElement('script');
            for (const attr of old.attributes) {
                script.setAttribute(attr.name, attr.value);
            }
            script.text = old.textContent;
            document.body.appendChild(script);
        }
        import('/static/js/pages/universal-initializer.js')
            .then(function (mod) {
                if (mod && typeof mod.initializePage === 'function') {
                    return mod.initializePage();
                }
            })
            .catch(function () { /* initializer unavailable: declarative
                                     handlers still work (document-level
                                     delegation) */ });
    }

    function swapFromResponse(url, html, push) {
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        const nextMain = parsed.getElementById(MAIN_ID);
        const currentMain = document.getElementById(MAIN_ID);
        if (!nextMain || !currentMain) return false;

        // The sidebar element is deliberately NOT touched: it keeps its
        // scroll position, its state and any focus inside it.
        const imported = document.importNode(nextMain, true);
        currentMain.replaceChildren(...imported.childNodes);

        document.title = parsed.title || document.title;
        updateSidebarActiveState();
        if (push) {
            try { window.history.pushState({ swap: true }, '', url); } catch (e) { /* */ }
        }
        persistSidebarState();
        currentMain.removeAttribute('aria-busy');

        runPageScripts(parsed);
        currentMain.scrollIntoView({ block: 'start' });
        return true;
    }

    let inFlight = null;

    async function navigateTo(url, push) {
        if (inFlight) inFlight.abort = true;
        const token = { abort: false };
        inFlight = token;
        const main = document.getElementById(MAIN_ID);
        if (main) main.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(url, {
                headers: { 'X-Navigate': 'swap' },
                credentials: 'same-origin',
                redirect: 'follow'
            });
            if (token.abort) return;
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const html = await response.text();
            if (token.abort) return;
            if (!swapFromResponse(url, html, push)) {
                window.location.href = url;   // unexpected shape: full load
                return;
            }
        } catch (error) {
            if (token.abort) return;
            window.location.href = url;       // fallback: full navigation
        }
    }

    function onClick(event) {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = event.target.closest('a');
        if (!anchor) return;
        const url = sameOriginLink(anchor);
        if (!url) return;
        event.preventDefault();
        persistSidebarState();
        navigateTo(url.href, true);
    }

    function onPopState() {
        persistSidebarState();
        navigateTo(window.location.href, false);
    }

    if (!window.__navigationSwapInstalled) {
        window.__navigationSwapInstalled = true;
        document.addEventListener('click', onClick, true);
        window.addEventListener('popstate', onPopState);
        document.addEventListener('DOMContentLoaded', restoreSidebarState);
        // Persist before a full load tears the page down (fallback paths).
        window.addEventListener('pagehide', persistSidebarState);
    }
})();
