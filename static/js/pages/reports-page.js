/**
 * Reports page: run registered reports and read their results.
 *
 * A view over the server contract only (Api/routes/reports.py, GET
 * /api/search/saved, GET /api/jobs/<id>[/errors]). Files (artifacts) are
 * rendered by the server; the page only asks for one and links to the
 * download, which the server audits (DATA_EXPORTED). The browser never computes a
 * count, a status or a result: it sends the report id and the parameters the
 * user gave, follows the job, and renders what the server recorded -
 * including its error code and message verbatim, the snapshot, the
 * fingerprints and each dataset's limit semantics. Rows are read a page at a
 * time from the server (never the whole result). Every value is inserted
 * with textContent; text cells and user-typed names get dir="auto". NULL is
 * shown as "none", never as an empty cell or zero.
 */

import { getCSRFTokenAsync } from '../modules/core/utils.js';

const DATA = JSON.parse(document.getElementById('reports-page-data').textContent);
const L = JSON.parse(document.getElementById('reports-page-labels').textContent);
const RUN_PAGE = DATA.list_limit;
const ROW_PAGE = Math.min(DATA.row_page, DATA.max_row_page);

const state = {
    definitions: [],
    savedSearches: null,     // loaded once, on first need
    readers: [],             // one read(payload) per parameter of the shown report
    runOffset: 0,
    run: null,               // the run shown in the detail panel
    datasetKey: null,
    rowOffset: 0,
    formats: [],             // [{format, single_dataset}] from the artifacts list
    language: 'en',          // the artifact language (part of its identity)
};

// ---------------------------------------------------------------------------
// Small helpers (same shapes as the Monitoring page)
// ---------------------------------------------------------------------------

function fmt(template, values) {
    return template.replace(/%\((\w+)\)s/g, (_, k) => (values[k] ?? ''));
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of children) {
        if (child === null || child === undefined) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function userText(value) {
    return el('bdi', { dir: 'auto', text: value ?? '' });
}

function when(iso) {
    if (!iso) return L.never;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function clear(id) {
    const node = document.getElementById(id);
    node.replaceChildren();
    return node;
}

function show(id, visible) {
    document.getElementById(id).classList.toggle('d-none', !visible);
}

const STATUS_TONE = {
    completed: 'bg-success', failed: 'bg-danger', refused: 'bg-warning text-dark',
    cancelled: 'bg-secondary', running: 'bg-info text-dark', queued: 'bg-light text-dark',
};

function statusBadge(status) {
    return el('span', { class: `badge ${STATUS_TONE[status] || 'bg-secondary'}`,
        text: L[`status_${status}`] || status });
}

// ---------------------------------------------------------------------------
// HTTP: the server's structured error is kept, never flattened
// ---------------------------------------------------------------------------

class ApiError extends Error {
    constructor(status, body) {
        const err = body && body.error;
        const message = (err && (err.message || err.code)) || (typeof err === 'string' ? err : '')
            || `${L.request_failed} (HTTP ${status})`;
        super(message);
        this.status = status;
        this.code = err && err.code;
    }
}

async function api(method, url, payload) {
    const headers = { Accept: 'application/json' };
    const init = { method, credentials: 'same-origin', headers };
    if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        headers['X-CSRFToken'] = await getCSRFTokenAsync();
        init.body = JSON.stringify(payload ?? {});
    }
    const resp = await fetch(url, init);
    const body = await resp.json().catch(() => null);
    if (!resp.ok || (body && body.success === false)) throw new ApiError(resp.status, body);
    return body;
}

function showError(error) {
    const box = document.getElementById('reportsError');
    const message = box.querySelector('.state-message, p') || box;
    message.textContent = error.code ? `${error.message} (${error.code})` : error.message;
    box.classList.remove('d-none');
}

function hideError() {
    document.getElementById('reportsError').classList.add('d-none');
}

function notice(text) {
    const box = document.getElementById('reportsNotice');
    box.className = text ? 'alert alert-info py-2 small' : 'd-none';
    box.textContent = text || '';
}

async function guarded(fn) {
    hideError();
    try {
        return await fn();
    } catch (error) {
        showError(error);
        return undefined;
    }
}

const FINISHED = new Set(['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED']);

async function followJob(job, targetId = 'reportJob') {
    const target = document.getElementById(targetId);
    let current = job;
    for (let i = 0; !FINISHED.has(current.status) && i < DATA.job_poll_limit; i += 1) {
        target.textContent = fmt(L.job_running, { id: current.job_id, status: current.status });
        await new Promise((resolve) => setTimeout(resolve, DATA.job_poll_ms));
        const body = await api('GET', `/api/jobs/${encodeURIComponent(current.job_id)}`);
        current = body.job || body;
    }
    target.textContent = FINISHED.has(current.status)
        ? fmt(L.job_done, { id: current.job_id, status: current.status })
        : fmt(L.job_timeout, { id: current.job_id });
    return current;
}

function pager(id, total, offset, size, onMove) {
    const nav = clear(id);
    if (!total) return;
    const to = Math.min(offset + size, total);
    nav.append(el('span', { class: 'small text-muted', text: fmt(L.page, { from: offset + 1, to, total }) }));
    const buttons = el('div', { class: 'btn-group btn-group-sm' });
    const prev = el('button', { type: 'button', class: 'btn btn-outline-secondary', text: L.previous });
    const next = el('button', { type: 'button', class: 'btn btn-outline-secondary', text: L.next });
    prev.disabled = offset === 0;
    next.disabled = to >= total;
    prev.addEventListener('click', () => onMove(Math.max(0, offset - size)));
    next.addEventListener('click', () => onMove(offset + size));
    buttons.append(prev, next);
    nav.append(buttons);
}

function emptyRow(tbodyId, columns, text) {
    clear(tbodyId).append(el('tr', {}, el('td', { colspan: columns, class: 'text-muted', text })));
}

// ---------------------------------------------------------------------------
// Definitions and the parameter form
// ---------------------------------------------------------------------------

function currentDefinition() {
    const key = document.getElementById('reportSelect').value;
    return state.definitions.find((d) => d.key === key) || null;
}

function semanticsText(ds) {
    return fmt(L[`semantics_${ds.semantics}`] || ds.semantics, { limit: ds.row_limit });
}

async function savedSearches() {
    if (state.savedSearches === null) {
        const body = await api('GET', '/api/search/saved');
        state.savedSearches = body.searches || [];
    }
    return state.savedSearches;
}

/**
 * Each parameter builder returns {node, read}: ``read(payload)`` adds the
 * parameter to the request body from the builder's own inputs (closures, no
 * selector lookups), or throws an Error with a message for the user.
 */
async function criteriaInput(param) {
    const id = (suffix) => `param_${param.name}_${suffix}`;
    const wrap = el('fieldset', { class: 'mb-2', 'data-param': param.name });
    wrap.append(el('legend', { class: 'form-label fs-6 mb-1', text: param.label }));
    const radios = {};
    const radio = (value, label) => {
        radios[value] = el('input', { class: 'form-check-input', type: 'radio', name: id('source'),
            id: id(value), value });
        return el('div', { class: 'form-check form-check-inline' }, radios[value],
            el('label', { class: 'form-check-label small', for: id(value), text: label }));
    };
    wrap.append(el('div', { class: 'small mb-1', text: L.criteria_source }),
        radio('saved', L.from_saved), radio('json', L.from_json));

    const select = el('select', { class: 'form-select form-select-sm mt-1', id: `${id('saved')}_select` });
    const searches = await savedSearches();
    select.append(el('option', { value: '', text: L.choose }));
    for (const s of searches) {
        // <option> holds text only; the name is set as text, never as markup.
        select.append(el('option', { value: String(s.id), text: s.name }));
    }
    const textarea = el('textarea', { class: 'form-control form-control-sm font-monospace mt-1',
        id: `${id('json')}_text`, rows: '5', dir: 'ltr', spellcheck: 'false' });
    textarea.value = '{}';
    // Node.append(null) would insert the text "null": append real nodes only.
    wrap.append(select);
    if (!searches.length) wrap.append(el('div', { class: 'form-text', text: L.no_saved_searches }));
    wrap.append(textarea);

    const useSaved = () => radios.saved.checked;
    const sync = () => {
        select.classList.toggle('d-none', !useSaved());
        textarea.classList.toggle('d-none', useSaved());
    };
    radios.saved.checked = searches.length > 0;
    radios.json.checked = !radios.saved.checked;
    radios.saved.addEventListener('change', sync);
    radios.json.addEventListener('change', sync);
    sync();
    return {
        node: wrap,
        read(payload) {
            if (useSaved()) {
                // An empty choice sends nothing: the server says the parameter is required.
                if (select.value) payload.saved_search_id = Number(select.value);
                return;
            }
            try {
                payload.parameters[param.name] = JSON.parse(textarea.value || '{}');
            } catch (_) {
                throw new Error(L.criteria_invalid);
            }
        },
    };
}

function plainInput(param) {
    const id = `param_${param.name}`;
    const wrap = el('div', { class: 'mb-2', 'data-param': param.name });
    let input;
    if (param.type === 'enum') {
        input = el('select', { class: 'form-select form-select-sm', id });
        if (!param.required) input.append(el('option', { value: '', text: L.none }));
        param.choices.forEach((choice, i) => input.append(
            el('option', { value: choice, text: (param.choice_labels || [])[i] || choice })));
        if (param.default !== null) input.value = param.default;
    } else if (param.type === 'boolean') {
        input = el('input', { class: 'form-check-input ms-2', type: 'checkbox', id });
        input.checked = param.default === true;
    } else {
        const type = { integer: 'number', date: 'date' }[param.type] || 'text';
        input = el('input', { class: 'form-control form-control-sm', type, id, dir: type === 'text' ? 'auto' : null,
            min: param.minimum, max: param.maximum, maxlength: param.max_length, required: param.required });
        if (param.default !== null && param.default !== undefined) input.value = String(param.default);
    }
    wrap.append(el('label', { class: 'form-label', for: id, text: param.label }), input);
    return {
        node: wrap,
        read(payload) {
            if (param.type === 'boolean') payload.parameters[param.name] = input.checked;
            else if (input.value !== '') payload.parameters[param.name] = input.value;
        },
    };
}

async function renderDefinition() {
    const def = currentDefinition();
    const about = clear('reportAbout');
    const params = clear('reportParameters');
    state.readers = [];
    if (!def) return;
    about.append(el('p', { class: 'mb-1', text: def.description }));
    if (def.help) about.append(el('p', { class: 'text-muted mb-1', text: def.help.summary }));
    about.append(el('div', { class: 'text-muted', text: fmt(L.unit, { unit: def.unit }) }));
    const list = el('ul', { class: 'mb-0 text-muted' });
    for (const ds of def.datasets) {
        list.append(el('li', {}, el('code', { text: ds.key }), ` - ${semanticsText(ds)}`));
    }
    about.append(el('div', { class: 'mt-1', text: L.datasets }), list);
    if ((def.analyses || []).length) {
        const alist = el('ul', { class: 'mb-0 text-muted' });
        for (const a of def.analyses) alist.append(el('li', {}, `${a.title} `, el('code', { text: a.key })));
        about.append(el('div', { class: 'mt-1', text: L.analyses }), alist);
    }
    for (const param of def.parameters) {
        const built = param.type === 'criteria' ? await criteriaInput(param) : plainInput(param);
        state.readers.push(built.read);
        params.append(built.node);
    }
    document.getElementById('reportRun').disabled = !def.can_run;
}

/** The request body, or throws an Error with a message for the user. */
function readForm(def) {
    const payload = { report_id: def.report_id, version: def.version, parameters: {} };
    for (const read of state.readers) read(payload);
    return payload;
}

async function loadDefinitions() {
    const body = await api('GET', '/api/reports/definitions');
    state.definitions = body.items || [];
    const select = clear('reportSelect');
    for (const def of state.definitions) {
        select.append(el('option', { value: def.key, text: `${def.title} (v${def.version})` }));
    }
    if (state.definitions.length) select.value = state.definitions[0].key;
    if (!state.definitions.length) {
        notice(L.no_reports);
        document.getElementById('reportRun').disabled = true;
        return;
    }
    await renderDefinition();
}

async function submitRun() {
    const def = currentDefinition();
    if (!def) return;
    let payload;
    try {
        payload = readForm(def);
    } catch (error) {
        showError(error);
        return;
    }
    const button = document.getElementById('reportRun');
    button.disabled = true;
    try {
        const body = await api('POST', '/api/reports/runs', payload);
        if (body.job && !FINISHED.has(body.job.status)) await followJob(body.job);
        await loadRuns();
        await openRun(body.run.id);
    } finally {
        button.disabled = !def.can_run;
    }
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

function rowsSummary(run) {
    if (!run.datasets || !run.datasets.length) return L.none;
    return run.datasets.map((d) => `${d.row_count}${d.truncated ? '+' : ''}`).join(' · ');
}

async function loadRuns() {
    const params = new URLSearchParams({ limit: String(RUN_PAGE), offset: String(state.runOffset) });
    const all = document.getElementById('runAllUsers');
    if (all && all.checked) params.set('all', '1');
    const status = document.getElementById('runStatus').value;
    if (status) params.set('status', status);
    const body = await api('GET', `/api/reports/runs?${params}`);
    if (!body.items.length) {
        emptyRow('runRows', 5, status ? L.no_runs_filtered : L.no_runs);
    } else {
        const tbody = clear('runRows');
        for (const run of body.items) {
            const open = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0', text: run.title || run.report_key });
            open.addEventListener('click', () => guarded(() => openRun(run.id)));
            tbody.append(el('tr', {},
                el('td', { text: when(run.requested_at) }),
                el('td', {}, open, el('span', { class: 'text-muted small ms-1', text: `#${run.id}` })),
                el('td', {}, statusBadge(run.status)),
                el('td', { text: rowsSummary(run) }),
                el('td', {}, userText(run.requester_username))));
        }
    }
    pager('runPager', body.total, state.runOffset, RUN_PAGE, (offset) => {
        state.runOffset = offset;
        guarded(loadRuns);
    });
}

function outcomeText(run) {
    switch (run.status) {
    case 'completed': return fmt(L.completed_at, { when: when(run.snapshot_at) });
    case 'failed': return fmt(L.failed, { error: run.error || '' });
    case 'refused': return fmt(L.refused, { reason: L[`refusal_${run.refusal_reason}`] || run.refusal_reason });
    case 'cancelled': return L.cancelled;
    default: return L.pending;
    }
}

function provenance(run) {
    const dl = clear('runProvenance');
    const add = (label, value, mono) => {
        dl.append(el('dt', { class: 'col-sm-4', text: label }),
            el('dd', { class: `col-sm-8 ${mono ? 'font-monospace text-break' : ''}`, dir: mono ? 'ltr' : null },
                value === null || value === undefined || value === '' ? L.none : value));
    };
    add(L.prov_report, run.report_key, true);
    add(L.prov_definition, run.definition_fingerprint, true);
    add(L.prov_parameters, JSON.stringify(run.parameters), true);
    add(L.prov_parameters_fp, run.parameters_fingerprint, true);
    add(L.prov_criteria_fp, run.criteria_fingerprint, true);
    add(L.prov_saved_search, run.saved_search_id === null ? null : `#${run.saved_search_id}`);
    add(L.prov_requester, userText(run.requester_username));
    add(L.prov_role, run.requester_role);
    add(L.prov_snapshot, run.snapshot, true);
    add(L.prov_isolation, run.isolation_level);
    add(L.prov_generator, run.generator_version, true);
    add(L.prov_job, run.job_id, true);
    add(L.prov_requested, when(run.requested_at));
    add(L.prov_started, when(run.started_at));
    add(L.prov_finished, when(run.finished_at));
    for (const ds of run.datasets || []) add(`${L.prov_query_fp} ${ds.dataset_key}`, ds.query_fingerprint, true);
}

function analyses(run) {
    // The narrative is rendered by the server from reviewed templates in the
    // reader's language; the page shows it verbatim, voice by voice.
    const list = clear('runAnalysisList');
    const items = run.analyses || [];
    show('runAnalyses', items.length > 0);
    for (const a of items) {
        const section = el('section', { class: 'mb-3' });
        const stateText = a.state === 'measured' ? L.analysis_measured : L.analysis_not_measurable;
        section.append(
            el('h4', { class: 'fs-6 mb-1', text: `${a.title} · ${stateText}` }),
            el('div', { class: 'small text-muted mb-1', dir: 'ltr',
                text: fmt(L.analysis_meta, { key: a.analysis_key, templates: `${a.template_set}@${a.template_version}` }) }));
        const dl = el('dl', { class: 'row small mb-0' });
        for (const voice of a.text || []) {
            dl.append(el('dt', { class: 'col-sm-2', text: L[`voice_${voice.voice}`] || voice.voice }),
                el('dd', { class: 'col-sm-10', dir: 'auto', text: voice.text }));
        }
        section.append(dl);
        list.append(section);
    }
}

async function openRun(id) {
    const body = await api('GET', `/api/reports/runs/${encodeURIComponent(id)}`);
    const run = body.run;
    state.run = run;
    show('runDetail', true);
    const title = clear('runDetailTitle');
    title.append(`${run.title || run.report_key} #${run.id} `, statusBadge(run.status));
    const outcome = clear('runOutcome');
    const tone = { completed: 'text-success', failed: 'text-danger', refused: 'text-warning' }[run.status] || 'text-muted';
    outcome.append(el('div', { class: tone, text: outcomeText(run) }));
    provenance(run);
    analyses(run);
    const select = clear('runDatasetSelect');
    for (const ds of run.datasets || []) {
        select.append(el('option', { value: ds.dataset_key, text: `${ds.dataset_key} (${ds.row_count})` }));
    }
    select.disabled = !(run.datasets || []).length;
    state.datasetKey = run.datasets && run.datasets.length ? run.datasets[0].dataset_key : null;
    state.rowOffset = 0;
    if (state.datasetKey) await loadRows();
    else {
        clear('runDataHead');
        clear('runDatasetNote');
        clear('runDataPager');
        emptyRow('runDataRows', 1, L.no_rows);
    }
    await loadFiles();
    const url = new URL(window.location.href);
    url.searchParams.set('run', String(run.id));
    window.history.replaceState(null, '', url);
}

function cell(value, column) {
    if (value === null || value === undefined) return el('td', { class: 'text-muted fst-italic', text: L.none });
    if (column.type === 'boolean') return el('td', { text: value ? L.yes : L.no });
    if (column.type === 'json') return el('td', { class: 'font-monospace small', dir: 'ltr', text: JSON.stringify(value) });
    if (column.type === 'timestamptz') return el('td', { text: when(value) });
    if (column.type === 'text') return el('td', {}, userText(value));
    return el('td', { dir: 'ltr', text: String(value) });
}

async function loadRows() {
    const run = state.run;
    const key = state.datasetKey;
    const params = new URLSearchParams({ limit: String(ROW_PAGE), offset: String(state.rowOffset) });
    const page = await api('GET',
        `/api/reports/runs/${encodeURIComponent(run.id)}/datasets/${encodeURIComponent(key)}?${params}`);
    const head = clear('runDataHead');
    for (const column of page.columns) head.append(el('th', { text: column.label || column.name }));
    const note = clear('runDatasetNote');
    const ds = { semantics: page.semantics, row_limit: page.row_limit };
    note.append(el('div', { class: 'text-muted', text: semanticsText(ds) }));
    if (page.truncated) {
        note.append(el('div', { class: 'text-warning fw-semibold',
            text: fmt(L[`truncated_${page.semantics}`] || L.truncated_capped, { limit: page.row_limit }) }));
    } else {
        note.append(el('div', { class: 'text-success', text: fmt(L.complete, { n: page.row_count }) }));
    }
    if (!page.rows.length) {
        emptyRow('runDataRows', Math.max(1, page.columns.length), L.no_rows);
    } else {
        const tbody = clear('runDataRows');
        for (const row of page.rows) {
            tbody.append(el('tr', {}, ...page.columns.map((c) => cell(row[c.name], c))));
        }
    }
    pager('runDataPager', page.row_count, state.rowOffset, ROW_PAGE, (offset) => {
        state.rowOffset = offset;
        guarded(loadRows);
    });
}

// ---------------------------------------------------------------------------
// Files (artifacts): made on the server, downloaded as attachments
// ---------------------------------------------------------------------------

function fileDatasetSync() {
    const format = document.getElementById('fileFormat').value;
    const spec = state.formats.find((f) => f.format === format);
    const select = clear('fileDataset');
    if (spec && spec.single_dataset) {
        for (const ds of state.run.datasets || []) {
            select.append(el('option', { value: ds.dataset_key, text: ds.dataset_key }));
        }
        select.disabled = false;
        select.value = (state.run.datasets || [])[0]?.dataset_key || '';
    } else {
        select.append(el('option', { value: '', text: L.all_datasets }));
        select.disabled = true;
        select.value = '';
    }
}

function fileRow(file) {
    const base = `/api/reports/artifacts/${encodeURIComponent(file.id)}`;
    const verify = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', text: L.verify });
    verify.addEventListener('click', () => guarded(async () => {
        const result = await api('GET', `${base}/verify`);
        const box = clear('fileVerify');
        const failed = Object.entries(result.checks || {}).filter(([, ok]) => !ok).map(([k]) => k);
        box.append(el('span', { class: result.ok ? 'text-success' : 'text-danger',
            text: result.ok ? L.verified_ok : fmt(L.verified_bad, { checks: failed.join(', ') }) }));
    }));
    const actions = el('td', { class: 'text-nowrap' },
        el('a', { class: 'btn btn-outline-primary btn-sm me-1', href: `${base}/download`, download: file.filename, text: L.download }),
        el('a', { class: 'btn btn-outline-secondary btn-sm me-1', href: `${base}/manifest`, download: `${file.filename}.manifest.json`, text: L.manifest }),
        verify);
    return el('tr', {},
        el('td', { dir: 'ltr', text: `${file.format} (${file.renderer_version})` }),
        el('td', { dir: 'ltr', text: file.dataset_key || L.all_datasets }),
        el('td', { dir: 'ltr', text: file.language || 'en' }),
        el('td', { dir: 'ltr', class: 'text-break', text: file.filename }),
        el('td', { text: fmt(L.bytes, { n: file.byte_size }) }),
        el('td', { dir: 'ltr', class: 'font-monospace small text-break', title: `manifest ${file.manifest_sha256}`, text: file.sha256 }),
        el('td', {}, when(file.created_at), ' ', userText(file.creator_username)),
        actions);
}

async function loadFiles() {
    const run = state.run;
    show('runFiles', true);
    clear('fileVerify');
    clear('fileJob');
    const completed = run.status === 'completed';
    document.getElementById('fileControls').classList.toggle('d-none', !(completed && DATA.can_run));
    if (!completed) {
        clear('fileUnavailable').append(L.files_completed_only);
        emptyRow('fileRows', 8, L.no_files);
        return;
    }
    const body = await api('GET', `/api/reports/runs/${encodeURIComponent(run.id)}/artifacts`);
    state.formats = body.formats || [];
    // Reloading the list (after a file is made) keeps what the user chose.
    const previous = { format: document.getElementById('fileFormat').value,
        dataset: document.getElementById('fileDataset').value };
    const select = clear('fileFormat');
    for (const f of state.formats) select.append(el('option', { value: f.format, text: f.format.toUpperCase() }));
    const keep = state.formats.some((f) => f.format === previous.format);
    select.value = keep ? previous.format : (state.formats.length ? state.formats[0].format : '');
    fileDatasetSync();
    const datasets = document.getElementById('fileDataset');
    if (keep && !datasets.disabled && (state.run.datasets || []).some((d) => d.dataset_key === previous.dataset)) {
        datasets.value = previous.dataset;
    }
    const unavailable = clear('fileUnavailable');
    for (const u of body.unavailable || []) {
        unavailable.append(el('div', { text: fmt(L.unavailable, { format: u.format.toUpperCase(), reason: u.reason }) }));
    }
    if (!body.items.length) {
        emptyRow('fileRows', 8, L.no_files);
        return;
    }
    const tbody = clear('fileRows');
    for (const file of body.items) tbody.append(fileRow(file));
}

function syncLanguageSelect() {
    // The languages the renderers ship (step 18); the choice becomes part of
    // the file's identity - same run+format+dataset in another language is a
    // different file, and the manifest states the language.
    const select = document.getElementById('fileLanguage');
    if (!select || !Array.isArray(DATA.languages) || !DATA.languages.length) return;
    const previous = state.language;
    const fresh = clear('fileLanguage');
    for (const code of DATA.languages) {
        fresh.append(el('option', { value: code, text: code }));
    }
    select.value = DATA.languages.includes(previous) ? previous : 'en';
    state.language = select.value;
}

async function createFile() {
    const run = state.run;
    const format = document.getElementById('fileFormat').value;
    const payload = { format };
    const spec = state.formats.find((f) => f.format === format);
    if (spec && spec.single_dataset) payload.dataset_key = document.getElementById('fileDataset').value;
    const languageSelect = document.getElementById('fileLanguage');
    state.language = (languageSelect && languageSelect.value) || state.language || 'en';
    payload.language = state.language;
    const button = document.getElementById('fileCreate');
    button.disabled = true;
    try {
        const body = await api('POST', `/api/reports/runs/${encodeURIComponent(run.id)}/artifacts`, payload);
        if (body.existing) {
            await loadFiles();
            clear('fileJob').append(L.file_existing);
            return;
        }
        const job = body.job ? await followJob(body.job, 'fileJob') : null;
        await loadFiles();
        if (job) {
            clear('fileJob').append(job.status === 'COMPLETED'
                ? L.file_created : fmt(L.job_done, { id: job.job_id, status: job.status }));
            if (job.status === 'FAILED') {
                const detail = await api('GET', `/api/jobs/${encodeURIComponent(job.job_id)}/errors`);
                if (detail.errors && detail.errors.length) showError(new Error(detail.errors.join('; ')));
            }
        }
    } finally {
        button.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function on(id, event, handler) {
    const node = document.getElementById(id);
    if (node) node.addEventListener(event, handler);
}

async function init() {
    on('reportSelect', 'change', () => guarded(renderDefinition));
    on('reportForm', 'submit', (e) => { e.preventDefault(); guarded(submitRun); });
    on('runRefresh', 'click', () => guarded(loadRuns));
    on('runStatus', 'change', () => { state.runOffset = 0; guarded(loadRuns); });
    on('runAllUsers', 'change', () => { state.runOffset = 0; guarded(loadRuns); });
    on('runDatasetSelect', 'change', (e) => {
        state.datasetKey = e.target.value;
        state.rowOffset = 0;
        guarded(loadRows);
    });
    on('fileFormat', 'change', fileDatasetSync);
    on('fileLanguage', 'change', (e) => { state.language = e.target.value || 'en'; });
    on('fileCreate', 'click', () => guarded(createFile));
    if (!DATA.can_run) notice(L.read_only);
    syncLanguageSelect();
    await guarded(loadDefinitions);
    await guarded(loadRuns);
    const requested = new URLSearchParams(window.location.search).get('run');
    if (requested && /^\d+$/.test(requested)) await guarded(() => openRun(Number(requested)));
}

init();
