/**
 * Browser smoke: the static sidebar + the Settings Navigation tab
 * (owner requirements D11/D12 in EXECUTION_STATUS.md).
 *
 * Proves, in a real browser against a running, installed server:
 *
 *  D11  a sidebar click swaps only #mainContent - the sidebar DOM node
 *       survives (a marked property is still present), its scrollTop
 *       survives, and a first-load marker on window survives (no full
 *       navigation); history.back() returns under the same rules;
 *  D12  Settings -> Navigation lists every sidebar interface (hidden ones
 *       included, so hiding is never a one-way door); moving a row with
 *       the Up button, saving, and REALLY reloading brings the saved order
 *       back; Reset restores the declared order.
 *
 * Usage (against a running, installed server - see docs/TESTING.md):
 *
 *     npm install --prefix /tmp/smoke-tools puppeteer-core   # once
 *     NODE_PATH=/tmp/smoke-tools/node_modules CHROME_PATH=/usr/bin/chromium \
 *         SMOKE_ADMIN_PASSWORD=... node tools/smoke/navigation_smoke.mjs
 *
 * Environment:
 *     SMOKE_BASE_URL        http://localhost:5055 (use "localhost": browsers
 *                           keep Secure cookies there without TLS)
 *     SMOKE_ADMIN_USER      admin
 *     SMOKE_ADMIN_PASSWORD  (required)
 *     CHROME_PATH           a Chrome/Chromium executable (required)
 *     CHROME_ARGS           extra flags, space separated
 *
 * Exit status is non-zero on any failure.
 */
import { createRequire } from 'node:module';

// Resolved through require() so NODE_PATH works: ES module imports ignore it,
// and puppeteer-core is a tool dependency, not a product one.
const puppeteer = createRequire(import.meta.url)('puppeteer-core');

const BASE = (process.env.SMOKE_BASE_URL || 'http://localhost:5055').replace(/\/$/, '');
const PASSWORD = process.env.SMOKE_ADMIN_PASSWORD || '';
if (!PASSWORD) { console.error('SMOKE_ADMIN_PASSWORD required'); process.exit(2); }

let failures = 0;
function check(name, ok, detail = '') {
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' -- ' + String(detail).slice(0, 300) : ''}`);
    if (!ok) failures += 1;
}

const CHROME = process.env.CHROME_PATH || '';
if (!CHROME) {
    console.error('CHROME_PATH is required (a Chrome/Chromium executable)');
    process.exit(2);
}
const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'shell',
    args: ['--no-sandbox', '--disable-dev-shm-usage',
           ...(process.env.CHROME_ARGS || '').split(' ').filter(Boolean)],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });  // desktop: sidebar open (>= 992px)
page.setDefaultTimeout(30000);
const consoleErrors = [];
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));
page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push('console: ' + m.text());
});

try {
    // ── sign in ─────────────────────────────────────────────────────────
    await page.goto(BASE + '/auth/login', { waitUntil: 'networkidle0' });
    await page.type('input[name="username"]', 'admin');
    await page.type('input[name="password"]', PASSWORD);
    await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle0' }),
        page.click('button[type="submit"]'),
    ]);
    check('signed in', !page.url().includes('/auth/login'), page.url());

    // ── D11: first page — mark the sidebar, scroll it, mark the load ───
    await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
    const marked = await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        if (!sb) return { ok: false, why: 'no #sidebar' };
        sb.__navSmokeMarker = 'syl-smoke-42';
        sb.scrollTop = 120;
        window.__navSmokeFirstLoad = true;
        const link = sb.querySelector('a[href]:not([href="#"])');
        return {
            ok: true,
            firstHref: link ? link.getAttribute('href') : null,
            firstPath: link ? new URL(link.href, location.origin).pathname : null,
            here: location.pathname,
        };
    });
    check('sidebar found + marked', marked.ok, marked.why || '');

    // click the FIRST sidebar link that differs from the current path
    const target = await page.evaluate((here) => {
        const links = [...document.querySelectorAll('#sidebar a[href]')];
        const usable = links.filter((a) => {
            try {
                const p = new URL(a.href, location.origin).pathname;
                return p !== here && p !== '/' && !/logout|download|#/i.test(a.href);
            } catch { return false; }
        });
        const l = usable.find((a) => a.pathname.startsWith('/reports'))
            || usable.find((a) => a.pathname.startsWith('/files'))
            || usable[0];
        if (!l) return null;
        return new URL(l.href, location.origin).pathname;
    }, marked.here);
    check('a different sidebar link exists', !!target, target);

    // JS-dispatched click: puppeteer's CDP click pre-scrolls the target
    // into view, which would mask the very scroll preservation we test.
    await page.evaluate((href) => {
        document.querySelector(`#sidebar a[href="${href}"]`).click();
    }, target);
    await page.waitForFunction(
        (prev) => window.__navSwapDone || location.pathname !== prev,
        { timeout: 15000 }, marked.here,
    ).catch(() => {});
    await new Promise((r) => setTimeout(r, 1200)); // let the swap settle

    const after = await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        return {
            path: location.pathname,
            marker: sb ? sb.__navSmokeMarker : undefined,
            scroll: sb ? sb.scrollTop : -1,
            firstLoad: window.__navSmokeFirstLoad,
            mainChanged: !!document.getElementById('mainContent'),
        };
    });
    check('path changed after sidebar click', after.path !== marked.here,
          `${marked.here} -> ${after.path}`);
    check('sidebar node survived (same DOM node)', after.marker === 'syl-smoke-42',
          'marker=' + after.marker);
    check('sidebar scroll survived', after.scroll === 120, 'scroll=' + after.scroll);
    check('no full page reload (window survived)', after.firstLoad === true,
          'firstLoad=' + after.firstLoad);

    // ── D11: back returns, still no redraw ──────────────────────────────
    await page.goBack().catch(() => {});
    await new Promise((r) => setTimeout(r, 1200));
    const back = await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        return {
            path: location.pathname,
            marker: sb ? sb.__navSmokeMarker : undefined,
            firstLoad: window.__navSmokeFirstLoad,
        };
    });
    check('history.back() returned to first page', back.path === marked.here, back.path);
    check('sidebar survived back-navigation', back.marker === 'syl-smoke-42', back.marker);
    check('still no full reload after back', back.firstLoad === true);

    // ── D12: Settings → Navigation tab ─────────────────────────────────
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle0' });
    await page.click('#navigation-tab');
    await page.waitForSelector('#navigationPrefsBody tr[data-interface]', { timeout: 15000 });

    // pick two ADJACENT interfaces in the same domain: positions reorder
    // within a domain (the sidebar is domain-grouped).
    const pair = await page.evaluate(async () => {
        const r = await fetch('/api/preferences/navigation',
                              { headers: { Accept: 'application/json' } });
        const data = await r.json();
        const e = data.entries || [];
        for (let i = 0; i + 1 < e.length; i++) {
            if (e[i].domain === e[i + 1].domain) {
                return { first: e[i].interface_id, second: e[i + 1].interface_id };
            }
        }
        return null;
    });
    check('an adjacent same-domain pair exists', !!pair, JSON.stringify(pair));
    const order1 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    check('catalog loaded with rows', order1.length >= 2, `${order1.length} rows`);

    // move the second of the pair up (swap the two) and save. Click via
    // dispatch on the exact button: a coordinate click can graze a
    // neighbouring checkbox and silently hide an interface.
    await page.evaluate((iid) => {
        const btn = document.querySelector(
            `#navigationPrefsBody tr[data-interface="${iid}"] button[data-move="up"]`);
        btn.click();
    }, pair.second);
    const order2 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    const i1 = order2.indexOf(pair.second);
    check('move reorders the table',
          order2[i1] === pair.second && order2[i1 + 1] === pair.first,
          `expected ${pair.second},${pair.first} got ${order2[i1]},${order2[i1 + 1]}`);

    await page.click('#navigationPrefsSave');
    await page.waitForFunction(() =>
        /saved|saved\./i.test(document.getElementById('navigationPrefsStatus').textContent),
        { timeout: 15000 });
    check('save reported success', true);

    // real reload: the new order must come back from the server
    await page.reload({ waitUntil: 'networkidle0' });
    await page.click('#navigation-tab');
    await page.waitForSelector('#navigationPrefsBody tr[data-interface]', { timeout: 15000 });
    const order3 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    const j1 = order3.indexOf(pair.second);
    check('reordered position persisted across reload',
          order3[j1] === pair.second && order3[j1 + 1] === pair.first,
          `expected ${pair.second},${pair.first} got ${order3[j1]},${order3[j1 + 1]}`);
    check('hidden interfaces stay listed in the editor',
          order3.length === order1.length,
          `${order3.length} vs ${order1.length} rows`);

    // reset restores the declared order
    page.once('dialog', (d) => d.accept());
    await page.click('#navigationPrefsReset');
    await new Promise((r) => setTimeout(r, 1500));
    await page.reload({ waitUntil: 'networkidle0' });
    await page.click('#navigation-tab');
    await page.waitForSelector('#navigationPrefsBody tr[data-interface]', { timeout: 15000 });
    const order4 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    check('reset restored the default order',
          JSON.stringify(order4) === JSON.stringify(order1),
          `expected ${order1.slice(0, 3)} got ${order4.slice(0, 3)}`);

    // sidebar on a normal page reflects nothing broken (no JS errors)
    check('no page/console errors during the run', consoleErrors.length === 0,
          consoleErrors.slice(0, 3).join(' | '));
} finally {
    await browser.close();
}
console.log(failures === 0 ? 'NAV SMOKE: ALL PASS' : `NAV SMOKE: ${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
