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
 * - Failure handling: navigation errors are rendered inside the main content
 *   with a retry action. Network failures and malformed page responses never
 *   force a full-page refresh; only an authentication redirect leaves the shell.
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

    function localizedMessage(key, fallback) {
        try {
            const source = (window.translations && window.translations[key]) || fallback;
            if (typeof window.t !== 'function') return source;
            const translated = window.t(source);
            return translated && translated !== source ? translated : source;
        } catch (_error) {
            return fallback;
        }
    }

    function showNavigationStatus(main, kind, targetUrl, push) {
        if (!main) return;
        let status = main.querySelector('[data-navigation-swap-status]');
        if (!status) {
            status = document.createElement('div');
            status.setAttribute('data-navigation-swap-status', '');
            main.insertBefore(status, main.firstChild);
        }
        status.className = kind === 'error'
            ? 'alert alert-danger d-flex flex-wrap align-items-center justify-content-between gap-2'
            : 'alert alert-info';
        status.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        status.textContent = '';
        const message = document.createElement('span');
        message.textContent = kind === 'error'
            ? localizedMessage('navigationLoadFailed', 'Error loading data')
            : localizedMessage('navigationLoading', 'Loading...');
        status.appendChild(message);
        if (kind === 'error') {
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn-sm btn-outline-danger';
            retry.textContent = localizedMessage('retry', 'Retry');
            retry.addEventListener('click', function () {
                navigateTo(targetUrl, Boolean(push), false);
            }, { once: true });
            status.appendChild(retry);
        }
    }

    function syncSidebarFromDocument(newDoc) {
        const currentSidebar = document.getElementById('sidebar');
        const nextSidebar = newDoc.getElementById('sidebar');
        if (!currentSidebar || !nextSidebar) return false;

        // Keep the shell, scroll position, mobile-open state and any listeners
        // on #sidebar itself. Only synchronize its content after an explicit
        // settings change (ordinary page swaps never touch the sidebar).
        ['.sidebar-logo', 'nav'].forEach((selector) => {
            const current = currentSidebar.querySelector(selector);
            const next = nextSidebar.querySelector(selector);
            if (current && next) {
                const imported = document.importNode(next, true);
                current.replaceWith(imported);
            }
        });
        updateSidebarActiveState();
        return true;
    }

    function translatedLabel(value) {
        if (typeof value !== 'string') return '';
        try {
            return typeof window.t === 'function' ? (window.t(value) || value) : value;
        } catch (_error) {
            return value;
        }
    }

    function syncSidebarFromEntries(entries) {
        const sidebar = document.getElementById('sidebar');
        const nav = sidebar && sidebar.querySelector('nav');
        if (!nav || !Array.isArray(entries)) return false;

        const list = document.createElement('ul');
        list.className = 'sidebar-nav';
        let currentDomain = null;
        for (const entry of entries) {
            if (!entry || entry.hidden) continue;
            if (entry.domain !== currentDomain) {
                currentDomain = entry.domain;
                const section = document.createElement('li');
                section.className = 'sidebar-nav-item';
                const label = document.createElement('span');
                label.className = 'sidebar-section-label';
                label.textContent = translatedLabel(entry.domain_label || entry.domain || '');
                section.appendChild(label);
                list.appendChild(section);
            }

            let destination;
            try {
                destination = new URL(entry.url, window.location.origin);
            } catch (_error) {
                continue;
            }
            if (destination.origin !== window.location.origin
                || !['http:', 'https:'].includes(destination.protocol)) continue;

            const item = document.createElement('li');
            item.className = 'sidebar-nav-item';
            const link = document.createElement('a');
            link.setAttribute('href', destination.pathname + destination.search + destination.hash);
            link.className = 'sidebar-nav-link';
            link.setAttribute('data-interface', entry.interface_id || '');
            if (entry.route) link.setAttribute('data-endpoint', entry.route);
            if (entry.description) {
                link.title = translatedLabel(entry.description)
                    + (entry.note ? ' — ' + translatedLabel(entry.note) : '');
            }
            if (entry.shortcut) link.setAttribute('data-shortcut', entry.shortcut);
            const icon = document.createElement('i');
            icon.className = 'bi ' + (entry.icon || 'bi-circle');
            icon.setAttribute('aria-hidden', 'true');
            link.appendChild(icon);
            const text = document.createElement('span');
            text.textContent = translatedLabel(entry.label || entry.interface_id || '');
            link.appendChild(text);
            if (entry.badge) {
                const badge = document.createElement('span');
                badge.className = 'sidebar-nav-badge';
                badge.textContent = translatedLabel(entry.badge);
                link.appendChild(badge);
            }
            item.appendChild(link);
            list.appendChild(item);
        }
        nav.replaceChildren(list);
        updateSidebarActiveState();
        return true;
    }

    function runPageScripts(newDoc) {
        // Only the page's own scripts re-run: classic scripts from the BODY
        // are re-created so their functions exist for the new content's
        // declarative handlers. Shell scripts (the head: declarative-events,
        // this module, csrf/locale fixtures) already ran on the full load -
        // re-running them would pile up timers and document listeners - and
        // module scripts are cached by URL (re-appending is a no-op), so the
        // page modules' init is invoked explicitly afterwards.
        const scripts = Array.from(newDoc.querySelectorAll('script'))
            .filter((old) => !newDoc.head.contains(old))
            .filter((old) => (old.getAttribute('type') || '') !== 'module');
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
                    // Tell the initializer this visit arrived by swap: a
                    // self-running page module must re-import fresh, not
                    // reuse its already-run cached instance.
                    window.__swapReinit = Date.now();
                    return mod.initializePage().finally(function () {
                        delete window.__swapReinit;
                    });
                }
            })
            .catch(function () { /* initializer unavailable: declarative
                                     handlers still work (document-level
                                     delegation) */ });
    }

    function swapFromResponse(url, html, push, refreshSidebar) {
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        const nextMain = parsed.getElementById(MAIN_ID);
        const currentMain = document.getElementById(MAIN_ID);
        if (!nextMain || !currentMain) return false;

        // Ordinary route changes leave the sidebar subtree alone. Settings
        // changes may opt in to synchronizing its content, while retaining
        // the outer sidebar node and its scroll/open state.
        if (refreshSidebar) syncSidebarFromDocument(parsed);
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

    async function navigateTo(url, push, refreshSidebar) {
        if (inFlight) inFlight.abort();
        const controller = new AbortController();
        inFlight = controller;
        const main = document.getElementById(MAIN_ID);
        if (main) {
            main.setAttribute('aria-busy', 'true');
            showNavigationStatus(main, 'loading', url, push);
        }
        try {
            const response = await fetch(url, {
                headers: { 'X-Navigate': 'swap' },
                credentials: 'same-origin',
                redirect: 'follow',
                signal: controller.signal
            });
            if (controller.signal.aborted) return false;
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const html = await response.text();
            if (controller.signal.aborted) return false;
            if (!swapFromResponse(url, html, push, refreshSidebar)) {
                const destination = new URL(response.url || url, window.location.href);
                if (destination.pathname.startsWith('/auth/')) {
                    // An expired session must reach the standalone login page
                    // so the old authenticated sidebar cannot remain visible.
                    window.location.href = destination.href;
                    return false;
                }
                throw new Error('The destination did not contain the application shell');
            }
            return true;
        } catch (error) {
            if (controller.signal.aborted || error.name === 'AbortError') return false;
            if (main) {
                main.removeAttribute('aria-busy');
                showNavigationStatus(main, 'error', url, push);
            }
            return false;
        } finally {
            if (inFlight === controller) inFlight = null;
        }
    }

    async function refreshSidebarNavigation() {
        try {
            const response = await fetch('/api/preferences/navigation', {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin'
            });
            if (!response.ok) return false;
            const data = await response.json();
            if (!data || data.success !== true) return false;
            return syncSidebarFromEntries(data.entries);
        } catch (_error) {
            return false;
        }
    }

    async function refreshSidebarBranding() {
        try {
            const response = await fetch(window.location.href, {
                headers: { 'X-Navigate': 'swap' },
                credentials: 'same-origin',
                redirect: 'follow'
            });
            if (!response.ok) return false;
            const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
            const current = document.querySelector('#sidebar .sidebar-logo');
            const next = parsed.querySelector('#sidebar .sidebar-logo');
            if (!current || !next) return false;
            current.replaceWith(document.importNode(next, true));
            return true;
        } catch (_error) {
            return false;
        }
    }

    function onClick(event) {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const target = event.target && event.target.nodeType === 1
            ? event.target : (event.target && event.target.parentElement);
        const anchor = target && target.closest ? target.closest('a') : null;
        if (!anchor) return;
        const url = sameOriginLink(anchor);
        if (!url) return;
        event.preventDefault();
        persistSidebarState();
        navigateTo(url.href, true, false);
    }

    function onPopState() {
        persistSidebarState();
        navigateTo(window.location.href, false, false);
    }

    if (!window.__navigationSwapInstalled) {
        window.__navigationSwapInstalled = true;
        document.addEventListener('click', onClick, true);
        window.addEventListener('popstate', onPopState);
        document.addEventListener('DOMContentLoaded', restoreSidebarState);
        window.addEventListener('pagehide', persistSidebarState);
        // All ordinary client-side destinations, including table sort/filter
        // and keyboard navigation, share this one request/lifecycle path.
        window.swapNavigate = function (url) { return navigateTo(url, true, false); };
        window.refreshMainContent = function () {
            return navigateTo(window.location.href, false, false);
        };
        window.refreshSidebarNavigation = refreshSidebarNavigation;
        window.refreshSidebarBranding = refreshSidebarBranding;
        window.refreshApplicationView = function () {
            return navigateTo(window.location.href, false, true);
        };
    }
})();
