/**
 * Monitoring page: scenarios and rules.
 *
 * A view over the server contract only (Api/routes/scenarios.py,
 * Api/routes/rules.py, GET /api/jobs/<id>). The browser never decides an
 * outcome, a count or a status: it sends the definition the user typed, and
 * renders what the server answers - including its error code and message,
 * verbatim. Dry-runs and evaluations are background jobs; the page polls the
 * job and then re-reads the recorded result. Names and labels typed by users
 * are inserted with textContent only, with dir="auto".
 */

import { getCSRFTokenAsync } from '../modules/core/utils.js';

const DATA = JSON.parse(document.getElementById('monitoring-page-data').textContent);
const L = JSON.parse(document.getElementById('monitoring-page-labels').textContent);
const PAGE_SIZE = Math.min(20, DATA.max_page_size);

const state = {
    tab: 'scenarios',
    scenario: null,          // the scenario shown in the detail panel
    rule: null,
    editing: null,           // {kind: 'scenario'|'rule', id: number|null}
    outcomeOffset: 0,
    scenarioEvalOffset: 0,
    ruleEvalOffset: 0,
};

// ---------------------------------------------------------------------------
// Small helpers
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

/** User-entered text: direction decided by its own content. */
function userText(value) {
    return el('bdi', { dir: 'auto', text: value ?? '' });
}

function when(iso) {
    if (!iso) return L.never;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function statusBadge(status) {
    const tone = { active: 'bg-success', paused: 'bg-secondary', draft: 'bg-info text-dark',
        disabled: 'bg-danger', archived: 'bg-light text-dark' }[status] || 'bg-secondary';
    return el('span', { class: `badge ${tone}`, text: L[`status_${status}`] || status });
}

function clear(id) {
    const node = document.getElementById(id);
    node.replaceChildren();
    return node;
}

function show(id, visible) {
    document.getElementById(id).classList.toggle('d-none', !visible);
}

/** Flatten a counts object into "key: value" text (nested objects inline). */
function countsText(counts) {
    if (!counts || typeof counts !== 'object') return '';
    return Object.entries(counts).map(([k, v]) => {
        if (v && typeof v === 'object') {
            const inner = Object.entries(v).map(([ik, iv]) => `${ik} ${iv}`).join(', ');
            return `${k}: ${inner || '0'}`;
        }
        return `${k}: ${v}`;
    }).join(' · ');
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
    const box = document.getElementById('monitoringError');
    const message = box.querySelector('.state-message, p') || box;
    message.textContent = error.code ? `${error.message} (${error.code})` : error.message;
    box.classList.remove('d-none');
}

function hideError() {
    document.getElementById('monitoringError').classList.add('d-none');
}

function notice(text) {
    const box = document.getElementById('monitoringNotice');
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

// ---------------------------------------------------------------------------
// Jobs: poll GET /api/jobs/<id> until it finishes
// ---------------------------------------------------------------------------

const FINISHED = new Set(['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED']);

async function followJob(job, targetId) {
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

// ---------------------------------------------------------------------------
// Pager (same shape as the Signals page)
// ---------------------------------------------------------------------------

function pager(id, total, offset, onMove) {
    const nav = clear(id);
    if (!total) return;
    const to = Math.min(offset + PAGE_SIZE, total);
    nav.append(el('span', { class: 'small text-muted', text: fmt(L.page, { from: offset + 1, to, total }) }));
    const buttons = el('div', { class: 'btn-group btn-group-sm' });
    const prev = el('button', { type: 'button', class: 'btn btn-outline-secondary', text: L.previous });
    const next = el('button', { type: 'button', class: 'btn btn-outline-secondary', text: L.next });
    prev.disabled = offset === 0;
    next.disabled = to >= total;
    prev.addEventListener('click', () => onMove(Math.max(0, offset - PAGE_SIZE)));
    next.addEventListener('click', () => onMove(offset + PAGE_SIZE));
    buttons.append(prev, next);
    nav.append(buttons);
}

function emptyRow(tbodyId, columns, text) {
    clear(tbodyId).append(el('tr', {}, el('td', { colspan: columns, class: 'text-muted', text })));
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

function listQuery(prefix) {
    const params = new URLSearchParams();
    const all = document.getElementById(`${prefix}AllUsers`);
    if (all && all.checked) params.set('all', '1');
    if (document.getElementById(`${prefix}Archived`).checked) params.set('include_archived', '1');
    return params.toString();
}

async function loadScenarios() {
    const body = await guarded(() => api('GET', `/api/scenarios?${listQuery('scenario')}`));
    if (!body) return;
    const summary = fmt(L.count_scenarios, { n: body.count })
        + (body.truncated ? ` ${fmt(L.list_truncated, { n: body.limit })}` : '');
    document.getElementById('scenarioSummary').textContent = summary;
    if (!body.scenarios.length) {
        emptyRow('scenarioRows', 6, L.no_scenarios);
        return;
    }
    const rows = clear('scenarioRows');
    for (const s of body.scenarios) {
        const name = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0 text-start' }, userText(s.name));
        name.addEventListener('click', () => openScenario(s.id));
        rows.append(el('tr', { 'data-scenario-id': s.id },
            el('td', {}, name), el('td', {}, statusBadge(s.status)), el('td', { text: s.version }),
            el('td', { text: L[`strategy_${s.definition.strategy}`] || s.definition.strategy }),
            el('td', { text: when(s.last_evaluated_at) }),
            el('td', {}, userText(s.owner_username || String(s.owner_user_id)))));
    }
}

function actionButton(label, tone, onClick) {
    const b = el('button', { type: 'button', class: `btn btn-sm ${tone}`, text: label });
    b.addEventListener('click', onClick);
    return b;
}

function scenarioActions(s) {
    const box = clear('scenarioActions');
    if (!DATA.can_write || s.status === 'archived') return;
    const mine = s.owner_user_id === DATA.user_id;
    const base = `/api/scenarios/${s.id}`;
    const transition = (path) => guarded(async () => {
        const body = await api('POST', `${base}/${path}`);
        await afterScenarioChange(body.scenario);
    });
    if (mine) box.append(actionButton(L.action_edit, 'btn-outline-secondary', () => editScenario(s)));
    box.append(actionButton(L.action_dry_run, 'btn-outline-primary', () => runScenarioJob(s, 'dry-run')));
    if (s.status === 'draft') box.append(actionButton(L.action_activate, 'btn-success', () => transition('activate')));
    if (s.status === 'active') {
        box.append(actionButton(L.action_evaluate, 'btn-outline-primary', () => runScenarioJob(s, 'evaluate')));
        box.append(actionButton(L.action_pause, 'btn-outline-secondary', () => transition('pause')));
    }
    if (s.status === 'paused' || s.status === 'disabled') {
        box.append(actionButton(L.action_resume, 'btn-outline-success', () => transition('resume')));
    }
    box.append(actionButton(L.action_archive, 'btn-outline-danger', () => {
        // eslint-disable-next-line no-alert
        if (window.confirm(L.confirm_archive)) {
            guarded(async () => afterScenarioChange((await api('DELETE', base)).scenario));
        }
    }));
}

async function afterScenarioChange(scenario) {
    state.scenario = scenario;
    await loadScenarios();
    await openScenario(scenario.id);
}

async function runScenarioJob(s, path) {
    await guarded(async () => {
        const body = await api('POST', `/api/scenarios/${s.id}/${path}`);
        await followJob(body.job, 'scenarioJob');
        await afterScenarioChange((await api('GET', `/api/scenarios/${s.id}`)).scenario);
    });
}

async function openScenario(id) {
    const body = await guarded(() => api('GET', `/api/scenarios/${id}`));
    if (!body) return;
    const s = body.scenario;
    // A finished job's line stays while the same scenario is re-read.
    if (!state.scenario || state.scenario.id !== s.id) document.getElementById('scenarioJob').textContent = '';
    state.scenario = s;
    state.outcomeOffset = 0;
    state.scenarioEvalOffset = 0;
    show('scenarioEditor', false);
    show('scenarioDetail', true);
    clear('scenarioDetailTitle').append(userText(s.name), ' ', statusBadge(s.status));
    const meta = clear('scenarioDetailMeta');
    meta.append(`${L.version} ${s.version} · ${L.owner} `, userText(s.owner_username || String(s.owner_user_id)),
        ` · ${s.baselined ? L.baselined : L.not_baselined}`);
    meta.append(el('br'), `${L.fingerprint}: `, el('code', { text: s.definition_fingerprint }));
    if (s.disabled_reason) {
        meta.append(el('br'), fmt(L.disabled_reason, { reason: L[`reason_${s.disabled_reason}`] || s.disabled_reason }));
    }
    document.getElementById('scenarioDefinitionView').textContent = JSON.stringify(s.definition, null, 2);
    scenarioActions(s);
    await Promise.all([loadDryRun(s), loadOutcomes(), loadScenarioEvaluations()]);
}

function table(headers, rows) {
    const t = el('table', { class: 'table table-sm mb-2' });
    t.append(el('thead', {}, el('tr', {}, ...headers.map((h) => el('th', { text: h })))));
    const tb = el('tbody');
    for (const r of rows) tb.append(el('tr', {}, ...r.map((c) => el('td', {}, c))));
    t.append(tb);
    return t;
}

function topList(title, block) {
    if (!block || !block.total) return null;
    const items = block.items.map((i) => el('li', {}, userText(i.name ?? String(i.id)), ` — ${i.n}`));
    return el('div', { class: 'col-md-4' },
        el('div', { class: 'fw-semibold small', text: title }),
        el('ul', { class: 'small mb-1' }, ...items),
        block.truncated ? el('div', { class: 'small text-muted', text: fmt(L.dry_top, { n: block.items.length, t: block.total }) }) : null);
}

async function loadDryRun(s) {
    const box = clear('dryRunReport');
    const body = await guarded(() => api('GET', `/api/scenarios/${s.id}/dry-runs?limit=1`));
    if (!body) return;
    const run = body.items[0];
    if (!run) {
        box.append(el('p', { class: 'text-muted small', text: L.no_dry_run }));
        return;
    }
    const head = el('p', { class: 'small mb-1' },
        el('strong', { text: L[`dry_status_${run.status}`] || run.status }), ` · ${when(run.evaluated_at)}`);
    box.append(head);
    if (run.scenario_version !== s.version) {
        box.append(el('p', { class: 'small text-warning', text: fmt(L.dry_stale, { v: run.scenario_version }) }));
    }
    if (run.error) box.append(el('pre', { class: 'small text-danger', dir: 'ltr', text: run.error }));
    const r = run.report;
    if (!r) return;
    const v = r.validation || { errors: [], warnings: [] };
    if (v.errors.length) {
        box.append(el('div', { class: 'small text-danger fw-semibold', text: L.errors }),
            el('ul', { class: 'small text-danger' }, ...v.errors.map((e) => el('li', { text: e }))));
    }
    if (v.warnings.length) {
        box.append(el('div', { class: 'small fw-semibold', text: L.warnings }),
            el('ul', { class: 'small' }, ...v.warnings.map((w) => el('li', { text: w }))));
    }
    box.append(el('p', { class: 'small mb-1', text: fmt(L.dry_population, { n: r.population }) }));
    const act = r.notifications_on_activation;
    box.append(el('p', { class: 'small mb-1' },
        `${fmt(L.dry_on_activation, { n: act.notifications })} (${L[`dry_basis_${act.basis}`] || act.basis})`,
        act.overflow_summary ? ` ${fmt(L.dry_overflow, { n: act.overflow_contents })}` : ''));
    const vol = r.estimated_volume;
    box.append(el('p', { class: 'small mb-1', title: vol.basis, text: fmt(L.dry_volume, { per_day: vol.per_day }) }));
    const cases = (s.definition.cases || []).map((c) => [
        el('code', { text: c.id }), String(r.case_matches[c.id] ?? 0), String(r.decided_by_case[c.id] ?? 0),
        userText(((s.definition.outcomes || {})[c.outcome] || {}).label || c.outcome)]);
    box.append(table([L.dry_case, L.dry_matches, L.dry_decides, L.dry_outcome], cases));
    const outcomes = Object.entries(s.definition.outcomes || {}).map(([oid, o]) => [
        userText(o.label || oid), String(r.outcomes[oid] ?? 0)]);
    box.append(table([L.dry_outcome, L.dry_documents], outcomes));
    box.append(el('p', { class: 'small mb-1', text: fmt(L.dry_non_default, { n: r.non_default_contents }) }));
    const lists = [topList(L.dry_sources, r.sources), topList(L.dry_categories, r.categories),
        topList(L.dry_analyst_categories, r.analyst_categories)].filter(Boolean);
    if (lists.length) {
        box.append(el('div', { class: 'small fw-semibold', text: L.dry_breakdown }), el('div', { class: 'row' }, ...lists));
    }
    box.append(el('p', { class: 'small text-muted mb-0',
        text: fmt(L.dry_snapshot, { t: when(r.snapshot.at), d: r.reference_date }) }));
}

function outcomeLabels(ids) {
    const outcomes = (state.scenario && state.scenario.definition.outcomes) || {};
    return (ids || []).map((o) => (outcomes[o] && outcomes[o].label) || o).join(', ');
}

async function loadOutcomes() {
    const s = state.scenario;
    const body = await guarded(() => api('GET',
        `/api/scenarios/${s.id}/outcomes?limit=${PAGE_SIZE}&offset=${state.outcomeOffset}`));
    if (!body) return;
    if (!body.items.length) emptyRow('outcomeRows', 7, L.no_outcomes);
    else {
        const rows = clear('outcomeRows');
        for (const o of body.items) {
            const left = o.evidence && o.evidence.left_population;
            rows.append(el('tr', {},
                // path_id: an occurrence the viewer may read (null: none left).
                el('td', {}, o.path_id
                    ? el('a', { href: `/file/${o.path_id}` }, userText(o.file_name || fmt(L.document, { id: o.hash_id })))
                    : userText(fmt(L.document, { id: o.hash_id }))),
                el('td', {}, userText(outcomeLabels(o.outcomes)), left ? el('div', { class: 'small text-muted', text: L.left_population }) : null),
                el('td', {}, userText(o.previous_outcomes ? outcomeLabels(o.previous_outcomes) : L.none)),
                el('td', { class: 'small', text: (o.matched_cases || []).join(', ') }),
                el('td', { text: L[`delivery_${o.delivery}`] || o.delivery }),
                el('td', { text: o.priority || '' }),
                el('td', { text: when(o.recorded_at) })));
        }
    }
    pager('outcomePager', body.total, state.outcomeOffset, (off) => { state.outcomeOffset = off; loadOutcomes(); });
}

function evaluationRows(tbodyId, items, versionKey) {
    if (!items.length) {
        emptyRow(tbodyId, 5, L.no_evaluations);
        return;
    }
    const rows = clear(tbodyId);
    for (const e of items) {
        rows.append(el('tr', {},
            el('td', { text: when(e.evaluated_at) }), el('td', { text: e[versionKey] }),
            el('td', { text: e.trigger }),
            el('td', {}, L[`eval_${e.status}`] || e.status, e.error ? el('div', { class: 'small text-danger', text: e.error }) : null),
            el('td', { class: 'small', text: countsText(e.counts) })));
    }
}

async function loadScenarioEvaluations() {
    const s = state.scenario;
    const body = await guarded(() => api('GET',
        `/api/scenarios/${s.id}/evaluations?limit=${PAGE_SIZE}&offset=${state.scenarioEvalOffset}`));
    if (!body) return;
    evaluationRows('scenarioEvalRows', body.items, 'scenario_version');
    pager('scenarioEvalPager', body.total, state.scenarioEvalOffset,
        (off) => { state.scenarioEvalOffset = off; loadScenarioEvaluations(); });
}

// ---------------------------------------------------------------------------
// Editors (scenario and rule share the JSON handling)
// ---------------------------------------------------------------------------

function readDefinition(textareaId, validationId) {
    const out = clear(validationId);
    try {
        return JSON.parse(document.getElementById(textareaId).value);
    } catch (error) {
        out.append(el('div', { class: 'text-danger small', text: fmt(L.invalid_json, { error: error.message }) }));
        return undefined;
    }
}

function openEditor(kind, record) {
    const isScenario = kind === 'scenario';
    state.editing = { kind, id: record ? record.id : null };
    show(isScenario ? 'scenarioDetail' : 'ruleDetail', false);
    show(isScenario ? 'scenarioEditor' : 'ruleEditor', true);
    document.getElementById(`${kind}EditorTitle`).textContent = record
        ? L[`edit_${kind}`] : L[`new_${kind}`];
    document.getElementById(`${kind}Name`).value = record ? record.name : '';
    document.getElementById(`${kind}Definition`).value = JSON.stringify(
        record ? record.definition : DATA[`starter_${kind}`], null, 2);
    clear(`${kind}Validation`);
    document.getElementById(`${kind}Name`).focus();
}

function editScenario(s) { openEditor('scenario', s); }

async function validateScenario() {
    const definition = readDefinition('scenarioDefinition', 'scenarioValidation');
    if (definition === undefined) return;
    const body = await guarded(() => api('POST', '/api/scenarios/validate', { definition }));
    if (!body) return;
    const out = clear('scenarioValidation');
    if (body.valid) out.append(el('div', { class: 'text-success small', text: fmt(L.valid, { fp: body.definition_fingerprint.slice(0, 12) }) }));
    if (body.errors.length) out.append(el('ul', { class: 'text-danger small' }, ...body.errors.map((e) => el('li', { text: e }))));
    if (body.warnings.length) {
        out.append(el('div', { class: 'small fw-semibold', text: L.warnings }),
            el('ul', { class: 'small' }, ...body.warnings.map((w) => el('li', { text: w }))));
    }
}

async function saveEditor(kind) {
    const definition = readDefinition(`${kind}Definition`, `${kind}Validation`);
    if (definition === undefined) return;
    const name = document.getElementById(`${kind}Name`).value;
    const base = kind === 'scenario' ? '/api/scenarios' : '/api/rules';
    const { id } = state.editing;
    try {
        hideError();
        const body = id
            ? await api('PUT', `${base}/${id}`, { name, definition })
            : await api('POST', base, { name, definition });
        const record = body[kind];
        show(`${kind}Editor`, false);
        notice(kind === 'scenario' && id && record.status === 'draft'
            ? fmt(L.saved_draft, { v: record.version }) : L.saved);
        if (kind === 'scenario') await afterScenarioChange(record);
        else await afterRuleChange(record);
    } catch (error) {
        // Definition errors belong next to the definition.
        clear(`${kind}Validation`).append(el('div', { class: 'text-danger small', text: error.message }));
    }
}

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

async function loadRules() {
    const body = await guarded(() => api('GET', `/api/rules?${listQuery('rule')}`));
    if (!body) return;
    document.getElementById('ruleSummary').textContent = fmt(L.count_rules, { n: body.count })
        + (body.truncated ? ` ${fmt(L.list_truncated, { n: body.limit })}` : '');
    if (!body.rules.length) {
        emptyRow('ruleRows', 6, L.no_rules);
        return;
    }
    const rows = clear('ruleRows');
    for (const r of body.rules) {
        const name = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0 text-start' }, userText(r.name));
        name.addEventListener('click', () => openRule(r.id));
        rows.append(el('tr', { 'data-rule-id': r.id },
            el('td', {}, name), el('td', {}, statusBadge(r.status)), el('td', { text: r.version }),
            el('td', { text: r.baselined ? L.yes : L.no }), el('td', { text: when(r.last_evaluated_at) }),
            el('td', {}, userText(r.owner_username || String(r.owner_user_id)))));
    }
}

async function afterRuleChange(rule) {
    state.rule = rule;
    await loadRules();
    await openRule(rule.id);
}

function ruleActions(r) {
    const box = clear('ruleActions');
    if (!DATA.can_write || r.status === 'archived') return;
    const base = `/api/rules/${r.id}`;
    const post = (path, payload) => guarded(async () => afterRuleChange((await api('POST', `${base}/${path}`, payload)).rule));
    if (r.owner_user_id === DATA.user_id) box.append(actionButton(L.action_edit, 'btn-outline-secondary', () => openEditor('rule', r)));
    if (r.status === 'active') {
        box.append(actionButton(L.action_evaluate, 'btn-outline-primary', () => guarded(async () => {
            const body = await api('POST', `${base}/evaluate`);
            await followJob(body.job, 'ruleJob');
            await afterRuleChange((await api('GET', base)).rule);
        })));
        box.append(actionButton(L.action_suppress, 'btn-outline-secondary', () => post('suppress', { minutes: 60 })));
        box.append(actionButton(L.action_pause, 'btn-outline-secondary', () => post('pause')));
    }
    if (r.status === 'paused' || r.status === 'disabled') box.append(actionButton(L.action_resume, 'btn-outline-success', () => post('resume')));
    box.append(actionButton(L.action_archive, 'btn-outline-danger', () => {
        // eslint-disable-next-line no-alert
        if (window.confirm(L.confirm_archive)) guarded(async () => afterRuleChange((await api('DELETE', base)).rule));
    }));
}

async function openRule(id) {
    const body = await guarded(() => api('GET', `/api/rules/${id}`));
    if (!body) return;
    const r = body.rule;
    if (!state.rule || state.rule.id !== r.id) document.getElementById('ruleJob').textContent = '';
    state.rule = r;
    state.ruleEvalOffset = 0;
    show('ruleEditor', false);
    show('ruleDetail', true);
    clear('ruleDetailTitle').append(userText(r.name), ' ', statusBadge(r.status));
    const meta = clear('ruleDetailMeta');
    meta.append(`${L.version} ${r.version} · ${L.owner} `, userText(r.owner_username || String(r.owner_user_id)),
        ` · ${r.baselined ? L.baselined : L.not_baselined}`);
    if (r.suppressed) meta.append(el('br'), fmt(L.suppressed_until, { t: when(r.suppressed_until) }));
    if (r.disabled_reason) meta.append(el('br'), fmt(L.disabled_reason, { reason: L[`reason_${r.disabled_reason}`] || r.disabled_reason }));
    document.getElementById('ruleDefinitionView').textContent = JSON.stringify(r.definition, null, 2);
    ruleActions(r);
    await loadRuleEvaluations();
}

async function loadRuleEvaluations() {
    const r = state.rule;
    const body = await guarded(() => api('GET',
        `/api/rules/${r.id}/evaluations?limit=${PAGE_SIZE}&offset=${state.ruleEvalOffset}`));
    if (!body) return;
    evaluationRows('ruleEvalRows', body.evaluations, 'rule_version');
    pager('ruleEvalPager', body.total, state.ruleEvalOffset,
        (off) => { state.ruleEvalOffset = off; loadRuleEvaluations(); });
}

// ---------------------------------------------------------------------------
// Tabs and wiring
// ---------------------------------------------------------------------------

function selectTab(tab) {
    state.tab = tab;
    for (const [name, button, panel] of [['scenarios', 'tabScenarios', 'panelScenarios'], ['rules', 'tabRules', 'panelRules']]) {
        const active = name === tab;
        const b = document.getElementById(button);
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', active ? 'true' : 'false');
        show(panel, active);
    }
    if (tab === 'rules') loadRules();
    else loadScenarios();
}

function on(id, event, handler) {
    const node = document.getElementById(id);
    if (node) node.addEventListener(event, handler);
}

function init() {
    on('tabScenarios', 'click', () => selectTab('scenarios'));
    on('tabRules', 'click', () => selectTab('rules'));
    on('scenarioNew', 'click', () => openEditor('scenario', null));
    on('ruleNew', 'click', () => openEditor('rule', null));
    on('scenarioCancel', 'click', () => show('scenarioEditor', false));
    on('ruleCancel', 'click', () => show('ruleEditor', false));
    on('scenarioValidate', 'click', validateScenario);
    on('scenarioEditor', 'submit', (e) => { e.preventDefault(); saveEditor('scenario'); });
    on('ruleEditor', 'submit', (e) => { e.preventDefault(); saveEditor('rule'); });
    for (const id of ['scenarioAllUsers', 'scenarioArchived']) on(id, 'change', loadScenarios);
    for (const id of ['ruleAllUsers', 'ruleArchived']) on(id, 'change', loadRules);
    if (!DATA.can_write) notice(L.read_only);
    selectTab('scenarios');
}

init();
