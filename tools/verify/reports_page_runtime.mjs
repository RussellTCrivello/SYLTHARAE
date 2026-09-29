/**
 * Runtime check of the Reports page module against a running server (steps 14-15).
 *
 *   node tools/verify/reports_page_runtime.mjs <base-url> <state-dir>
 *
 * Run after runtime_check_reports.py (it saves the page the live server
 * rendered, the owner's session and the ids of two runs). The shipped
 * static/js/pages/reports-page.js is executed unmodified in the repository's
 * DOM stub (tests/js/_dom_stub.mjs), built from that live HTML; every fetch
 * goes to the live server with the session, and every write carries the
 * page's CSRF token as a browser would. What is checked is what a reader
 * would see after each action, including a new run submitted through the
 * form and followed as a background job.
 *
 * The stub is not a browser: layout, CSS and real bidi rendering are not
 * exercised (no headless browser is available in this environment).
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { FakeElement, installDom, treeFromHtml } from '../../tests/js/_dom_stub.mjs';

const [base, stateDir] = process.argv.slice(2);
const html = readFileSync(path.join(stateDir, 'evidence', 'reports_page.html'), 'utf8');
const cookie = readFileSync(path.join(stateDir, 'reports_session.txt'), 'utf8').trim();
const ids = JSON.parse(readFileSync(path.join(stateDir, 'evidence', 'reports_ids.json'), 'utf8'));
const failures = [];
const check = (ok, message) => {
    console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`);
    if (!ok) failures.push(message);
};

// --- the few standard DOM APIs the stub does not provide ----------------------
FakeElement.prototype.append = function append(...nodes) {
    for (const node of nodes) {
        if (node === null || node === undefined) throw new Error(`append(${node}) would insert the text "${node}"`);
        this.appendChild(node instanceof FakeElement ? node : document.createTextNode(node));
    }
};

const { documentRoot, document } = installDom();
document.createTextNode = (text) => { const n = new FakeElement('#text'); n._text = String(text); return n; };
globalThis.Node = FakeElement;
const body = html.replace(/<!DOCTYPE[^>]*>/i, '').replace(/<!--[\s\S]*?-->/g, '');
documentRoot.appendChild(treeFromHtml(body));
const nativeQuery = document.querySelector;
document.querySelector = (selector) => {
    const m = /^(\w+)\[([\w-]+)="([^"]*)"\]$/.exec(selector);
    if (!m) return nativeQuery(selector);
    return documentRoot.querySelectorAll(m[1]).find((n) => n.getAttribute(m[2]) === m[3]) || null;
};
globalThis.window = globalThis.window || globalThis;
globalThis.window.location = { href: `${base}/reports`, search: '' };
globalThis.window.history = { replaceState(_s, _t, url) { globalThis.window.location.href = String(url); } };

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
function openRun(runId) {
    const row = rows('runRows').find((r) => r.textContent.includes(`#${runId}`));
    row.querySelector('button').dispatch('click');
}

await import(pathToFileURL(path.resolve('static/js/pages/reports-page.js')).href);
await settle();

check(requests.includes('GET /api/reports/definitions'), 'initial load asks for the definitions');
check(byId('reportSelect').value === 'search_results@1', `report selected: ${byId('reportSelect').value}`);
check(/Counts: path/.test(text('reportAbout')) && /search_results\.count@1/.test(text('reportAbout')),
    `unit and datasets explained: ${text('reportAbout').slice(0, 120)}`);
check(byId('reportRun').disabled === false, 'an analyst may run');
const listed = rows('runRows').map((r) => r.textContent);
check(listed.some((t) => t.includes(`#${ids.first}`)) && listed.some((t) => t.includes(`#${ids.second}`)),
    `both runtime runs listed (${listed.length} rows)`);
check(hidden('reportsError'), 'no error shown');

// Open the first run: outcome, provenance, rows, the exact count.
openRun(ids.first);
await settle();
check(!hidden('runDetail'), 'run detail opened');
check(text('runDetailTitle').includes(`#${ids.first}`) && text('runDetailTitle').includes('Completed'),
    `title: ${text('runDetailTitle')}`);
check(/Completed\. Snapshot taken/.test(text('runOutcome')), `outcome: ${text('runOutcome')}`);
check(/Database snapshot\s*\d+:\d+:/.test(text('runProvenance')) && /repeatable read, read only/.test(text('runProvenance')),
    'provenance shows the snapshot and isolation');
check(/report-runner\/2/.test(text('runProvenance')), 'provenance shows the generator');
check(rows('runDataRows').length === 2, `two matching files listed (${rows('runDataRows').length})`);
check(/Complete: 2 rows\./.test(text('runDatasetNote')), `completeness stated: ${text('runDatasetNote')}`);
const header = byId('runDataHead').textContent;
check(/File name/.test(header) && /Source/.test(header), `declared column labels as headers: ${header}`);
byId('runDatasetSelect').value = 'search_results.count@1';
byId('runDatasetSelect').dispatch('change', { target: byId('runDatasetSelect') });
await settle();
check(rows('runDataRows').length === 1 && rows('runDataRows')[0].textContent.trim() === '2',
    `the exact count dataset: ${rows('runDataRows')[0]?.textContent}`);

// A new run through the form (criteria as JSON), followed as a job.
const before = rows('runRows').length;
const jsonRadio = byId('param_criteria_json');
const savedRadio = byId('param_criteria_saved');
savedRadio.checked = false;
jsonRadio.checked = true;
byId('param_criteria_json_text').value = JSON.stringify({ sources: ids.sources });
byId('reportForm').dispatch('submit', { preventDefault() {} });
await settle(900);
check(requests.includes('POST /api/reports/runs'), 'the form posts the run');
check(requests.some((r) => r.startsWith('GET /api/jobs/')), 'the job was followed');
check(/finished: COMPLETED/.test(text('reportJob')), `the finished job stays reported: ${text('reportJob')}`);
check(rows('runRows').length === before + 1, 'the new run is listed');
check(/Completed/.test(text('runDetailTitle')) && !text('runDetailTitle').includes(`#${ids.first} `),
    `the new run is opened: ${text('runDetailTitle')}`);

// Files (step 15): make an HTML file of the new run as a job, download it,
// check its SHA-256 against what the page shows, verify it, ask again.
check(!hidden('runFiles') && !hidden('fileControls'), 'files section offered for the completed run');
const formats = byId('fileFormat').children.map((o) => o.getAttribute('value'));
check(formats.join() === 'csv,html,json,xlsx', `formats from the server: ${formats}`);
check(/Not available yet: PDF/.test(text('fileUnavailable')), `unavailable formats stated: ${text('fileUnavailable')}`);
check(/No files have been made/.test(text('fileRows')), 'no files yet');
byId('fileFormat').value = 'html';
byId('fileFormat').dispatch('change');
check(byId('fileDataset').disabled === true, 'html covers every dataset (no dataset choice)');
byId('fileCreate').dispatch('click');
await settle(900);
check(requests.some((r) => /^POST \/api\/reports\/runs\/\d+\/artifacts$/.test(r)), 'the page asks for the file');
check(/File created\./.test(text('fileJob')), `file job reported: ${text('fileJob')}`);
const fileRows = rows('fileRows');
check(fileRows.length === 1 && /html/.test(fileRows[0].textContent), `one file listed: ${fileRows[0]?.textContent}`);
const shownSha = fileRows[0].children[4].textContent.trim();
const download = fileRows[0].children[6].children[0].getAttribute('href');
const response = await nativeFetch(new URL(download, base), { headers: { Cookie: cookie } });
const bytes = Buffer.from(await response.arrayBuffer());
const { createHash } = await import('node:crypto');
const measured = createHash('sha256').update(bytes).digest('hex');
check(response.status === 200 && measured === shownSha, `downloaded bytes hash to the shown SHA-256 (${measured.slice(0, 12)})`);
check(/^attachment; filename="report_search_results_v1_run\d+\.html"$/.test(response.headers.get('content-disposition') || ''),
    `downloaded as an attachment: ${response.headers.get('content-disposition')}`);
check(Boolean(response.headers.get('x-disclosure-audit-id')), 'the download was recorded (DATA_EXPORTED)');
fileRows[0].children[6].children[2].dispatch('click');
await settle();
check(/^Verified:/.test(text('fileVerify')), `verification shown: ${text('fileVerify')}`);
byId('fileCreate').dispatch('click');
await settle();
check(/already exists/.test(text('fileJob')) && rows('fileRows').length === 1, `repeat request: ${text('fileJob')}`);

// Invalid JSON: reported before any request.
const posts = requests.filter((r) => r === 'POST /api/reports/runs').length;
byId('param_criteria_json_text').value = '{ not json';
byId('reportForm').dispatch('submit', { preventDefault() {} });
await settle();
check(!hidden('reportsError') && /not valid JSON/.test(text('reportsError')), `invalid JSON: ${text('reportsError')}`);
check(requests.filter((r) => r === 'POST /api/reports/runs').length === posts, 'no request for invalid JSON');

// A server refusal is surfaced verbatim.
byId('param_criteria_json_text').value = JSON.stringify({ bogus_field: 1 });
byId('reportForm').dispatch('submit', { preventDefault() {} });
await settle();
check(/VALIDATION_FAILED/.test(text('reportsError')), `server refusal verbatim: ${text('reportsError')}`);

// Status filter asks the server.
byId('runStatus').value = 'failed';
byId('runStatus').dispatch('change');
await settle();
check(requests.some((r) => r.includes('/api/reports/runs?') && r.includes('status=failed')), 'status filter is server-side');
check(/No runs match this filter\./.test(text('runRows')), `no failed runs: ${text('runRows')}`);

console.log(`\n${failures.length} failure(s); ${requests.length} requests to the live server`);
process.exit(failures.length ? 1 : 0);
