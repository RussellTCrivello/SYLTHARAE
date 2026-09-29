/**
 * Runtime check of the Monitoring page module against a running server.
 *
 *   node tools/verify/monitoring_page_runtime.mjs <base-url> <state-dir>
 *
 * Run after runtime_check_scenarios.py (it saves the page the live server
 * rendered, the owner's session and the ids of its two scenarios). The
 * shipped static/js/pages/monitoring-page.js is executed unmodified in the
 * repository's DOM stub (tests/js/_dom_stub.mjs), built from that live HTML;
 * every fetch goes to the live server with the session, and every write
 * carries the page's CSRF token as a browser would. What is checked is what
 * a reader would see after each action.
 *
 * The stub is not a browser: layout, CSS and real bidi rendering are not
 * exercised (no headless browser is available in this environment).
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { FakeElement, installDom, treeFromHtml } from '../../tests/js/_dom_stub.mjs';

const [base, stateDir] = process.argv.slice(2);
const html = readFileSync(path.join(stateDir, 'evidence', 'monitoring_page.html'), 'utf8');
const cookie = readFileSync(path.join(stateDir, 'monitoring_session.txt'), 'utf8').trim();
const ids = JSON.parse(readFileSync(path.join(stateDir, 'evidence', 'monitoring_ids.json'), 'utf8'));
const failures = [];
const check = (ok, message) => {
    console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`);
    if (!ok) failures.push(message);
};

// --- the few standard DOM APIs the stub does not provide ----------------------
FakeElement.prototype.append = function append(...nodes) {
    for (const node of nodes) this.appendChild(node instanceof FakeElement ? node : document.createTextNode(node));
};

const { documentRoot, document } = installDom();
document.createTextNode = (text) => { const n = new FakeElement('#text'); n._text = String(text); return n; };
globalThis.Node = FakeElement;
const body = html.replace(/<!DOCTYPE[^>]*>/i, '').replace(/<!--[\s\S]*?-->/g, '');
documentRoot.appendChild(treeFromHtml(body));
// The stub matches `tag` or `[attr="v"]` but not the compound `tag[attr="v"]`
// (how the shared CSRF helper finds <meta name="csrf-token">); standard
// semantics for that one form.
const nativeQuery = document.querySelector;
document.querySelector = (selector) => {
    const m = /^(\w+)\[([\w-]+)="([^"]*)"\]$/.exec(selector);
    if (!m) return nativeQuery(selector);
    return documentRoot.querySelectorAll(m[1]).find((n) => n.getAttribute(m[2]) === m[3]) || null;
};
globalThis.window = globalThis.window || globalThis;
globalThis.window.confirm = () => true;

const requests = [];
const nativeFetch = globalThis.fetch;
globalThis.fetch = (url, options = {}) => {
    requests.push(`${options.method || 'GET'} ${url}`);
    return nativeFetch(new URL(url, base), { ...options, headers: { ...(options.headers || {}), Cookie: cookie } });
};

const byId = (id) => document.getElementById(id);
const rows = (id) => byId(id).children.filter((r) => r.tagName === 'TR');
const text = (id) => byId(id).textContent.replace(/\s+/g, ' ').trim();
const hidden = (id) => byId(id).classList.contains('d-none');
async function settle(quietMs = 400) {
    for (let i = 0; i < 400; i += 1) {
        const before = requests.length;
        await new Promise((resolve) => setTimeout(resolve, quietMs));
        if (requests.length === before) return;
    }
}
function button(containerId, label) {
    return byId(containerId).querySelectorAll('button').find((b) => b.textContent.trim() === label);
}
function openRow(tbodyId, attr, id) {
    const row = rows(tbodyId).find((r) => r.getAttribute(attr) === String(id));
    row.querySelector('button').dispatch('click');
}

await import(pathToFileURL(path.resolve('static/js/pages/monitoring-page.js')).href);
await settle();

check(requests.some((r) => r.startsWith('GET /api/scenarios?')), 'initial load asks the scenarios API');
const listed = rows('scenarioRows').map((r) => r.getAttribute('data-scenario-id'));
check(listed.includes(String(ids.baseline)) && listed.includes(String(ids.notify)),
    `both runtime scenarios listed (${listed.join(', ')})`);
check(/scenarios/.test(text('scenarioSummary')), `summary: ${text('scenarioSummary')}`);
check(hidden('monitoringError'), 'no error shown');

// The baselined scenario: report, outcome history, evaluation log.
openRow('scenarioRows', 'data-scenario-id', ids.baseline);
await settle();
check(!hidden('scenarioDetail'), 'detail panel opened');
check(text('scenarioDetailTitle').includes('Active'), `status shown: ${text('scenarioDetailTitle')}`);
const report = text('dryRunReport');
check(/Passed/.test(report) && /Population: 1 documents/.test(report), 'dry-run report: passed, population');
check(/Notifications on activation: 0 \(existing matches become the baseline\)/.test(report),
    'dry-run report: the baseline explanation');
check(/Measured on one consistent snapshot/.test(report), 'dry-run report: the snapshot');
const outcomeRows = rows('outcomeRows');
check(outcomeRows.length === 1 && outcomeRows[0].textContent.includes('Urgent')
    && outcomeRows[0].textContent.includes('baseline'), `outcome row: ${outcomeRows[0]?.textContent}`);
const link = outcomeRows[0]?.querySelector('a');
check(Boolean(link && /^\/file\/\d+$/.test(link.getAttribute('href'))), `outcome links to its file (${link?.getAttribute('href')})`);
check(rows('scenarioEvalRows').length >= 2, `evaluation log rows (${rows('scenarioEvalRows').length})`);
check(byId('scenarioDefinitionView').textContent.includes('"default_outcome": "none"'), 'current definition shown');
const bdi = byId('scenarioDetailTitle').querySelectorAll('bdi');
check(bdi.length > 0 && bdi.every((b) => b.getAttribute('dir') === 'auto'), 'user-entered names are isolated with dir="auto"');

// The edited scenario: version 2 is a draft and its dry-run is stale.
openRow('scenarioRows', 'data-scenario-id', ids.notify);
await settle();
check(/This dry-run was of version 1/.test(text('dryRunReport')), 'a stale dry-run is flagged');
button('scenarioActions', 'Activate').dispatch('click');
await settle();
check(!hidden('monitoringError') && /DRY_RUN_REQUIRED/.test(text('monitoringError')),
    `activation refusal surfaced verbatim: ${text('monitoringError')}`);
button('scenarioActions', 'Dry-run').dispatch('click');
await settle(900);
check(requests.some((r) => r === `POST /api/scenarios/${ids.notify}/dry-run`), 'dry-run requested');
check(requests.some((r) => r.startsWith('GET /api/jobs/')), 'the job was followed');
check(/finished: COMPLETED/.test(text('scenarioJob')), `the finished job stays reported: ${text('scenarioJob')}`);
check(!/This dry-run was of version/.test(text('dryRunReport')) && /Passed/.test(text('dryRunReport')),
    'the new dry-run is of the current version');
button('scenarioActions', 'Activate').dispatch('click');
await settle();
check(hidden('monitoringError') && text('scenarioDetailTitle').includes('Active'), 'activated after its dry-run');

// Editor: invalid JSON, validation, save.
byId('scenarioNew').dispatch('click');
check(!hidden('scenarioEditor') && byId('scenarioDefinition').value.includes('"strategy": "first_match"'),
    'the editor starts from the starter definition');
byId('scenarioDefinition').value = '{ not json';
byId('scenarioValidate').dispatch('click');
await settle();
check(/not valid JSON/.test(text('scenarioValidation')), 'invalid JSON is reported before any request');
byId('scenarioNew').dispatch('click');
byId('scenarioValidate').dispatch('click');
await settle();
check(/^Valid\. Fingerprint [0-9a-f]{12}/.test(text('scenarioValidation')), `validation: ${text('scenarioValidation')}`);
const def = JSON.parse(byId('scenarioDefinition').value);
delete def.default_outcome;
byId('scenarioDefinition').value = JSON.stringify(def);
byId('scenarioName').value = `ui draft ${Date.now()}`;
byId('scenarioEditor').dispatch('submit');
await settle();
check(/default_outcome is required/.test(text('scenarioValidation')), 'a refused save is shown next to the definition');
byId('scenarioNew').dispatch('click');
byId('scenarioName').value = `ui draft ${Date.now()}`;
byId('scenarioEditor').dispatch('submit');
await settle();
check(requests.some((r) => r === 'POST /api/scenarios'), 'save posts the scenario');
check(!hidden('scenarioDetail') && text('scenarioDetailTitle').includes('Draft'), 'the saved scenario opens as a draft');
check(/not been dry-run yet/.test(text('dryRunReport')), 'a new scenario says it has no dry-run');

// Rules tab.
byId('tabRules').dispatch('click');
await settle();
check(requests.some((r) => r.startsWith('GET /api/rules?')), 'rules tab asks the rules API');
check(!hidden('panelRules') && hidden('panelScenarios'), 'rules panel shown, scenarios hidden');
check(/rules/.test(text('ruleSummary')), `rules summary: ${text('ruleSummary')}`);

console.log(`\n${failures.length} failure(s); ${requests.length} requests to the live server`);
process.exit(failures.length ? 1 : 0);
