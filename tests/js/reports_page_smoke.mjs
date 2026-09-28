// Runs the real Reports page module (static/js/pages/reports-page.js)
// against a minimal DOM and a fake server, and checks what it renders:
// server/user text is inserted as text (never markup), NULL is shown as the
// "none" label, a truncated dataset says so, a failed run shows the server's
// error verbatim, and a viewer's Run button is disabled with the reason.
import fs from 'node:fs';
import vm from 'node:vm';

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
let innerHtmlWrites = 0;

function makeDom() {
  const byId = {};
  function textNode(v) {
    return { nodeType: 3, _text: String(v), get textContent() { return this._text; },
             get outerHTML() { return esc(this._text); } };
  }
  function el(tag = 'div') {
    const e = {
      nodeType: 1, tagName: tag.toUpperCase(), children: [], listeners: {}, attrs: {},
      className: '', value: '', checked: false, disabled: false, dataset: {},
      classList: {
        _set: new Set(),
        add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
        toggle(c, on) { if (on === undefined ? !this._set.has(c) : on) this._set.add(c); else this._set.delete(c); },
        contains(c) { return this._set.has(c); },
      },
      setAttribute(k, v) {
        this.attrs[k] = String(v);
        if (k === 'id') byId[v] = this;
        if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(v);
        if (k === 'checked') this.checked = true;
      },
      addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
      append(...c) {
        for (const x of c) {
          // A browser inserts the text "null"/"undefined" for these: a page bug.
          if (x === null || x === undefined) throw new Error(`append(${x}) would insert the text "${x}"`);
          this.children.push(typeof x === 'string' ? textNode(x) : x);
        }
      },
      replaceChildren(...c) { this.children = []; this.append(...c); },
      get textContent() { return this.children.map((c) => c.textContent).join(''); },
      set textContent(v) { this.children = [textNode(v)]; },
      get innerHTML() { return this.children.map((c) => c.outerHTML).join(''); },
      set innerHTML(v) { innerHtmlWrites += 1; },
      get outerHTML() {
        const a = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${esc(v)}"`).join('');
        return `<${tag}${a} class="${this.className}">${this.innerHTML}</${tag}>`;
      },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
      querySelectorAll(sel) {
        const alts = sel.split(',').map((s) => s.trim());
        const match = (n) => n.nodeType === 1 && alts.some((s) => {
          if (s.startsWith('#')) return n.attrs.id === s.slice(1);
          const m = s.match(/^(\w+)(?:\[type=(\w+)\])?$/);
          if (m) return n.tagName === m[1].toUpperCase() && (!m[2] || n.attrs.type === m[2]);
          if (s.startsWith('[data-param]')) return 'param' in n.dataset;
          return false;
        });
        const out = [];
        const walk = (n) => { for (const c of n.children || []) { if (match(c)) out.push(c); walk(c); } };
        walk(this);
        return out;
      },
    };
    return e;
  }
  const document = {
    getElementById(id) { if (!byId[id]) { byId[id] = el(); byId[id].attrs.id = id; } return byId[id]; },
    createElement: el,
    createTextNode: textNode,
    querySelectorAll(sel) {
      const m = sel.match(/^#(\w+) (.*)$/);
      return m ? document.getElementById(m[1]).querySelectorAll(m[2]) : [];
    },
  };
  return { document, byId };
}

const LABELS = JSON.parse(fs.readFileSync('templates/Reports/reports.html', 'utf8')
  .match(/id="reports-page-labels">\{\{ (\{[\s\S]*?\}) \|tojson \}\}/)[1]
  .replace(/_\((['"])((?:\\.|(?!\1).)*)\1\)/g, (_, q, s) => JSON.stringify(s.replace(/\\'/g, "'")))
  .replace(/,\s*\}$/, '}'));

async function run({ data, routes, search = '?run=5' }) {
  const { document, byId } = makeDom();
  document.getElementById('reports-page-data').textContent = JSON.stringify(data);
  document.getElementById('reports-page-labels').textContent = JSON.stringify(LABELS);
  document.getElementById('reportsError').classList.add('d-none');
  const calls = [];
  const fetchImpl = (url, init) => {
    calls.push([init && init.method, url]);
    const hit = routes.find(([re]) => re.test(url));
    const body = hit ? hit[1](url, init) : { success: false, error: { message: 'no route' } };
    return Promise.resolve({ ok: body.success !== false, status: body.success === false ? 400 : 200,
                             json: () => Promise.resolve(body) });
  };
  const href = `http://x/reports${search}`;
  const ctx = {
    document, fetch: fetchImpl, console, JSON, Promise, Object, String, Number, Math, Set, Error,
    URL, URLSearchParams, setTimeout,
    window: { location: { href, search }, history: { replaceState() {} } },
    Node: function Node() {},
    getCSRFTokenAsync: async () => 'tok',
  };
  // `child instanceof Node` in el(): make stub nodes count as Nodes.
  ctx.Node = function Node() {};
  Object.defineProperty(ctx.Node, Symbol.hasInstance, { value: (v) => v && typeof v === 'object' && 'nodeType' in v });
  vm.createContext(ctx);
  const src = fs.readFileSync('static/js/pages/reports-page.js', 'utf8').replace(/^import .*$/m, '');
  vm.runInContext(src, ctx);
  await new Promise((r) => setTimeout(r, 50));
  return { byId, calls };
}

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}

const DEFINITION = {
  report_id: 'search_results', version: 1, key: 'search_results@1', title: 'Search Results Report',
  description: 'desc', unit: 'path', help: { title: 't', summary: 's' }, can_run: true,
  parameters: [{ name: 'criteria', type: 'criteria', label: 'Search criteria', required: true, default: null,
                 choices: [], minimum: null, maximum: null, max_length: null }],
  datasets: [{ key: 'search_results.matches@1', semantics: 'capped', row_limit: 2, columns: [] }],
};
const COLUMNS = [{ name: 'path_id', type: 'integer', nullable: false, label: 'File ID' },
                 { name: 'file_name', type: 'text', nullable: false, label: 'File name' },
                 { name: 'note', type: 'text', nullable: true, label: 'Note' }];
const RUN = {
  id: 5, report_key: 'search_results@1', title: 'Search Results Report', status: 'completed',
  requester_username: HOSTILE, parameters: { criteria: { text: HOSTILE } }, snapshot: '100:100:',
  snapshot_at: '2026-09-28T10:00:00+00:00', saved_search_id: null, criteria_fingerprint: 'a'.repeat(64),
  datasets: [{ dataset_key: 'search_results.matches@1', row_count: 2, truncated: true, semantics: 'capped',
               query_fingerprint: 'b'.repeat(64) }],
};
const baseRoutes = (run) => [
  [/\/api\/reports\/definitions$/, () => ({ success: true, items: [DEFINITION] })],
  [/\/api\/search\/saved$/, () => ({ success: true, searches: [{ id: 3, name: HOSTILE }] })],
  [/\/api\/reports\/runs\?/, () => ({ success: true, items: [run], total: 1 })],
  [/\/api\/reports\/runs\/5\/datasets\//, () => ({
    success: true, dataset_key: 'search_results.matches@1', semantics: 'capped', row_limit: 2,
    row_count: 2, truncated: true, columns: COLUMNS,
    rows: [{ path_id: 1, file_name: HOSTILE, note: null }, { path_id: 2, file_name: 'b', note: '' }] })],
  [/\/api\/reports\/runs\/5$/, () => ({ success: true, run })],
];

// 1. A completed, truncated run with hostile text everywhere.
{
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes: baseRoutes(RUN) });
  const html = ['runRows', 'runProvenance', 'runDataRows', 'reportParameters', 'runDetailTitle']
    .map((id) => byId[id].innerHTML).join('\n');
  check('hostile text is rendered', html.includes('&lt;img'), html.slice(0, 300));
  check('hostile text never becomes markup', !html.includes('<img'), html);
  check('no innerHTML assignment', innerHtmlWrites === 0, innerHtmlWrites);
  const cells = byId.runDataRows.children[0].children;
  check('NULL shown as the none label', cells[2].textContent === LABELS.none, cells[2].textContent);
  check('empty string is not shown as none', byId.runDataRows.children[1].children[2].textContent === '');
  check('truncation stated', byId.runDatasetNote.textContent.includes(LABELS.truncated_capped.split(':')[0]),
        byId.runDatasetNote.textContent);
  check('rows column shows truncation mark', byId.runRows.children[0].children[3].textContent === '2+');
  check('detail panel shown', !byId.runDetail.classList.contains('d-none'));
}

// 2. A failed run: the server's error verbatim.
{
  const failed = { ...RUN, status: 'failed', error: `boom ${HOSTILE}`, datasets: [], snapshot: null };
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes: baseRoutes(failed) });
  check('failure text verbatim', byId.runOutcome.textContent === LABELS.failed.replace('%(error)s', `boom ${HOSTILE}`),
        byId.runOutcome.textContent);
  check('failure not markup', !byId.runOutcome.innerHTML.includes('<img'));
  check('no rows for a failed run', byId.runDataRows.textContent === LABELS.no_rows, byId.runDataRows.textContent);
}

// 3. A viewer: Run disabled, reason shown.
{
  const viewerDef = { ...DEFINITION, can_run: false };
  const routes = baseRoutes(RUN).map(([re, f]) => (String(re).includes('definitions')
    ? [re, () => ({ success: true, items: [viewerDef] })] : [re, f]));
  const { byId } = await run({ data: { can_run: false, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes, search: '' });
  check('run disabled for viewers', byId.reportRun && byId.reportRun.disabled === true);
  check('read-only reason shown', byId.reportsNotice.textContent === LABELS.read_only, byId.reportsNotice.textContent);
}

// 4. Submitting a saved search sends its id, not criteria.
{
  const posted = [];
  const routes = [[/\/api\/reports\/runs$/, (url, init) => { posted.push(JSON.parse(init.body));
    return { success: true, run: RUN, job: { job_id: 'J', status: 'COMPLETED' } }; }], ...baseRoutes(RUN)];
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes, search: '' });
  byId.param_criteria_saved_select.value = '3';
  byId.param_criteria_saved.checked = true;
  for (const f of byId.reportForm.listeners.submit || []) f({ preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));
  check('one run posted', posted.length === 1, posted);
  check('saved search id sent', posted[0] && posted[0].saved_search_id === 3 && !('criteria' in posted[0].parameters),
        JSON.stringify(posted[0]));
  check('report identity sent', posted[0] && posted[0].report_id === 'search_results' && posted[0].version === 1);
}

process.exit(failures ? 1 : 0);
