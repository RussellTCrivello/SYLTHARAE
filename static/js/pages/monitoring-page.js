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
    const defVal = record ? record.definition : DATA[`starter_${kind}`];
    document.getElementById(`${kind}Definition`).value = JSON.stringify(defVal, null, 2);
    clear(`${kind}Validation`);
    if (isScenario) {
        initScenarioVisualBuilder(defVal);
        switchEditorMode('visual');
    } else {
        initRuleVisualBuilder(defVal);
        switchRuleEditorMode('visual');
    }
    document.getElementById(`${kind}Name`).focus();
}

function editScenario(s) { openEditor('scenario', s); }

// ---------------------------------------------------------------------------
// Visual Scenario Rule Builder & Templates (Fields & Buttons, No JSON needed)
// ---------------------------------------------------------------------------

const SCENARIO_TEMPLATES = {
    urgent_event: {
        strategy: 'first_match',
        default_outcome: 'none',
        notify_existing: false,
        conditions: {
            dated_soon: { signals: { signal_types: ['date_reference'] }, min_confidence: 'medium', event_window_days: { from: 0, to: 30 } },
            dated: { signals: { signal_types: ['date_reference'] } }
        },
        cases: [
            { id: 'soon', when: { all: ['dated_soon'] }, outcome: 'review' },
            { id: 'later', when: { all: ['dated'] }, outcome: 'watch' }
        ],
        outcomes: {
            review: { label: 'Needs review', actions: ['notify'] },
            watch: { label: 'Watch', actions: [] },
            none: { label: 'No action', actions: [] }
        }
    },
    high_confidence: {
        strategy: 'first_match',
        default_outcome: 'none',
        notify_existing: false,
        conditions: {
            critical_date: { signals: { signal_types: ['date_reference'] }, min_confidence: 'high' }
        },
        cases: [
            { id: 'crit_case', when: { all: ['critical_date'] }, outcome: 'critical' }
        ],
        outcomes: {
            critical: { label: 'Critical Alert', actions: ['notify'] },
            none: { label: 'No action', actions: [] }
        }
    },
    place_mention: {
        strategy: 'first_match',
        default_outcome: 'none',
        notify_existing: false,
        conditions: {
            place_found: { signals: { signal_types: ['place_mention'] }, min_confidence: 'medium' }
        },
        cases: [
            { id: 'geo_case', when: { all: ['place_found'] }, outcome: 'geo_alert' }
        ],
        outcomes: {
            geo_alert: { label: 'Location Identified', actions: ['notify'] },
            none: { label: 'No action', actions: [] }
        }
    },
    cross_check: {
        strategy: 'first_match',
        default_outcome: 'none',
        notify_existing: false,
        conditions: {
            has_date: { signals: { signal_types: ['date_reference'] }, min_confidence: 'medium' },
            has_place: { signals: { signal_types: ['place_mention'] }, min_confidence: 'medium' }
        },
        cases: [
            { id: 'both_case', when: { all: ['has_date', 'has_place'] }, outcome: 'escalate' }
        ],
        outcomes: {
            escalate: { label: 'Cross-Signal Escalation', actions: ['notify'] },
            none: { label: 'No action', actions: [] }
        }
    }
};

function switchEditorMode(mode) {
    const isVisual = mode === 'visual';
    const visualBox = document.getElementById('scenarioVisualBuilder');
    const jsonBox = document.getElementById('scenarioJsonContainer');
    const btnV = document.getElementById('btnModeVisual');
    const btnJ = document.getElementById('btnModeJson');
    if (visualBox && jsonBox) {
        visualBox.classList.toggle('d-none', !isVisual);
        jsonBox.classList.toggle('d-none', isVisual);
    }
    if (btnV && btnJ) {
        btnV.classList.toggle('active', isVisual);
        btnV.classList.toggle('btn-primary', isVisual);
        btnV.classList.toggle('btn-outline-primary', !isVisual);
        btnJ.classList.toggle('active', !isVisual);
        btnJ.classList.toggle('btn-secondary', !isVisual);
        btnJ.classList.toggle('btn-outline-secondary', isVisual);
    }
    if (isVisual) {
        try {
            const raw = document.getElementById('scenarioDefinition').value;
            const def = JSON.parse(raw);
            initScenarioVisualBuilder(def);
        } catch (_) {}
    } else {
        syncVisualToJson();
    }
}

function onTemplateSelect() {
    const sel = document.getElementById('scenarioTemplateSelect');
    if (!sel || !sel.value || sel.value === 'custom') return;
    const tpl = SCENARIO_TEMPLATES[sel.value];
    if (tpl) {
        initScenarioVisualBuilder(tpl);
        syncVisualToJson();
    }
}

function initScenarioVisualBuilder(def) {
    const box = document.getElementById('scenarioVisualBuilder');
    if (!box || !def) return;

    const stratEl = document.getElementById('visualStrategy');
    if (stratEl) stratEl.value = def.strategy || 'first_match';

    const defOutEl = document.getElementById('visualDefaultOutcome');
    if (defOutEl) defOutEl.value = def.default_outcome || 'none';

    const notifyEl = document.getElementById('visualNotifyExisting');
    if (notifyEl) notifyEl.checked = Boolean(def.notify_existing);

    const condContainer = document.getElementById('visualConditionsContainer');
    if (condContainer) {
        clear('visualConditionsContainer');
        const conditions = def.conditions || {};
        for (const [name, cond] of Object.entries(conditions)) {
            addVisualConditionRow(name, cond);
        }
    }

    const casesContainer = document.getElementById('visualCasesContainer');
    if (casesContainer) {
        clear('visualCasesContainer');
        const cases = def.cases || [];
        const outcomes = def.outcomes || {};
        for (const c of cases) {
            const outInfo = outcomes[c.outcome] || { label: c.outcome, actions: [] };
            addVisualCaseRow(c, outInfo);
        }
    }
}

function addVisualConditionRow(name = '', cond = {}) {
    const container = document.getElementById('visualConditionsContainer');
    if (!container) return;

    const condName = name || `cond_${container.children.length + 1}`;
    const sigTypes = cond.signals?.signal_types || ['date_reference'];
    const minConf = cond.min_confidence || 'medium';
    const winDays = cond.event_window_days?.to !== undefined ? cond.event_window_days.to : '';

    const row = el('div', { class: 'row g-1 mb-2 align-items-center p-2 border rounded bg-white visual-cond-row' },
        el('div', { class: 'col-md-3' },
            el('label', { class: 'small text-muted d-block' }, 'Condition ID'),
            el('input', { type: 'text', class: 'form-control form-control-sm v-cond-id', value: condName, required: true })),
        el('div', { class: 'col-md-3' },
            el('label', { class: 'small text-muted d-block' }, 'Signal Type'),
            el('select', { class: 'form-select form-select-sm v-cond-sig' },
                el('option', { value: 'date_reference', ...(sigTypes.includes('date_reference') ? { selected: true } : {}) }, 'Date Reference (temporal)'),
                el('option', { value: 'place_mention', ...(sigTypes.includes('place_mention') ? { selected: true } : {}) }, 'Place Mention (places)'))),
        el('div', { class: 'col-md-3' },
            el('label', { class: 'small text-muted d-block' }, 'Min Confidence'),
            el('select', { class: 'form-select form-select-sm v-cond-conf' },
                el('option', { value: 'low', ...(minConf === 'low' ? { selected: true } : {}) }, 'Low'),
                el('option', { value: 'medium', ...(minConf === 'medium' ? { selected: true } : {}) }, 'Medium'),
                el('option', { value: 'high', ...(minConf === 'high' ? { selected: true } : {}) }, 'High'))),
        el('div', { class: 'col-md-2' },
            el('label', { class: 'small text-muted d-block' }, 'Window (Days)'),
            el('input', { type: 'number', class: 'form-control form-control-sm v-cond-window', placeholder: 'Any', value: String(winDays) })),
        el('div', { class: 'col-md-1 text-end' },
            el('label', { class: 'small text-muted d-block' }, ' '),
            el('button', { type: 'button', class: 'btn btn-sm btn-outline-danger py-0 px-2' },
                el('i', { class: 'bi bi-x-lg' }))));

    const removeBtn = row.querySelector('.btn-outline-danger');
    if (removeBtn) {
        removeBtn.addEventListener('click', () => {
            row.remove();
            syncVisualToJson();
        });
    }

    for (const input of row.querySelectorAll('input, select')) {
        input.addEventListener('input', syncVisualToJson);
        input.addEventListener('change', syncVisualToJson);
    }

    container.appendChild(row);
}

function addVisualCaseRow(cas = {}, outInfo = {}) {
    const container = document.getElementById('visualCasesContainer');
    if (!container) return;

    const caseId = cas.id || `case_${container.children.length + 1}`;
    const whenConds = cas.when?.all || (cas.when?.any || []);
    const whenText = whenConds.join(', ');
    const outcomeLabel = outInfo.label || cas.outcome || 'Review';
    const doNotify = (outInfo.actions || []).includes('notify');

    const row = el('div', { class: 'row g-1 mb-2 align-items-center p-2 border rounded bg-white visual-case-row' },
        el('div', { class: 'col-md-3' },
            el('label', { class: 'small text-muted d-block' }, 'Case ID'),
            el('input', { type: 'text', class: 'form-control form-control-sm v-case-id', value: caseId, required: true })),
        el('div', { class: 'col-md-3' },
            el('label', { class: 'small text-muted d-block' }, 'Trigger Condition(s)'),
            el('input', { type: 'text', class: 'form-control form-control-sm v-case-when', value: whenText, placeholder: 'e.g. dated_soon' })),
        el('div', { class: 'col-md-3' },
            el('label', { class: 'small text-muted d-block' }, 'Outcome Label'),
            el('input', { type: 'text', class: 'form-control form-control-sm v-case-outcome', value: outcomeLabel, placeholder: 'e.g. Urgent' })),
        el('div', { class: 'col-md-2' },
            el('label', { class: 'small text-muted d-block' }, 'Action'),
            el('div', { class: 'form-check form-switch pt-1' },
                el('input', { class: 'form-check-input v-case-notify', type: 'checkbox', ...(doNotify ? { checked: true } : {}) }),
                el('label', { class: 'form-check-label small' }, 'Notify'))),
        el('div', { class: 'col-md-1 text-end' },
            el('label', { class: 'small text-muted d-block' }, ' '),
            el('button', { type: 'button', class: 'btn btn-sm btn-outline-danger py-0 px-2' },
                el('i', { class: 'bi bi-x-lg' }))));

    const removeBtn = row.querySelector('.btn-outline-danger');
    if (removeBtn) {
        removeBtn.addEventListener('click', () => {
            row.remove();
            syncVisualToJson();
        });
    }

    for (const input of row.querySelectorAll('input, select')) {
        input.addEventListener('input', syncVisualToJson);
        input.addEventListener('change', syncVisualToJson);
    }

    container.appendChild(row);
}

function syncVisualToJson() {
    const textarea = document.getElementById('scenarioDefinition');
    if (!textarea) return;

    const strat = document.getElementById('visualStrategy')?.value || 'first_match';
    const defOutcome = (document.getElementById('visualDefaultOutcome')?.value || 'none').trim();
    const notifyExisting = Boolean(document.getElementById('visualNotifyExisting')?.checked);

    const conditions = {};
    const condRows = document.querySelectorAll('.visual-cond-row');
    for (const r of condRows) {
        const id = (r.querySelector('.v-cond-id')?.value || '').trim();
        if (!id) continue;
        const sigType = r.querySelector('.v-cond-sig')?.value || 'date_reference';
        const conf = r.querySelector('.v-cond-conf')?.value || 'medium';
        const win = r.querySelector('.v-cond-window')?.value;
        const cObj = {
            signals: { signal_types: [sigType] },
            min_confidence: conf
        };
        if (win !== '' && win !== undefined && !isNaN(Number(win))) {
            cObj.event_window_days = { from: 0, to: Number(win) };
        }
        conditions[id] = cObj;
    }

    const cases = [];
    const outcomes = {};
    outcomes[defOutcome] = { label: 'Default No Action', actions: [] };

    const caseRows = document.querySelectorAll('.visual-case-row');
    for (const r of caseRows) {
        const id = (r.querySelector('.v-case-id')?.value || '').trim();
        if (!id) continue;
        const whenRaw = (r.querySelector('.v-case-when')?.value || '').trim();
        const whenList = whenRaw ? whenRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
        const outLbl = (r.querySelector('.v-case-outcome')?.value || '').trim() || id;
        const outId = outLbl.toLowerCase().replace(/[^a-z0-9_]+/g, '_').slice(0, 30) || 'out';
        const doNotify = Boolean(r.querySelector('.v-case-notify')?.checked);

        cases.push({
            id: id,
            when: { all: whenList.length > 0 ? whenList : Object.keys(conditions).slice(0, 1) },
            outcome: outId
        });
        outcomes[outId] = {
            label: outLbl,
            actions: doNotify ? ['notify'] : []
        };
    }

    const def = {
        strategy: strat,
        default_outcome: defOutcome,
        notify_existing: notifyExisting,
        conditions: conditions,
        cases: cases,
        outcomes: outcomes
    };

    textarea.value = JSON.stringify(def, null, 2);
}

// ---------------------------------------------------------------------------
// Visual Monitoring Rule Builder & Templates (Fields & Buttons, No JSON needed)
// ---------------------------------------------------------------------------

const RULE_TEMPLATES = {
    urgent_temporal: {
        unit: 'signal',
        signals: { signal_types: ['date_reference'] },
        min_confidence: 'medium',
        event_window_days: { from: 0, to: 30 },
        threshold: { count: 1 },
        cooldown_minutes: 60,
        group_by: 'none',
        delivery: { mode: 'immediate' },
        notify_existing: false
    },
    place_mention: {
        unit: 'signal',
        signals: { signal_types: ['place_mention'] },
        min_confidence: 'medium',
        threshold: { count: 1 },
        cooldown_minutes: 120,
        group_by: 'none',
        delivery: { mode: 'immediate' },
        notify_existing: false
    },
    high_confidence: {
        unit: 'content',
        min_confidence: 'high',
        threshold: { count: 1 },
        cooldown_minutes: 30,
        group_by: 'content',
        delivery: { mode: 'immediate' },
        notify_existing: false
    },
    activity_digest: {
        unit: 'signal',
        min_confidence: 'low',
        threshold: { count: 1 },
        cooldown_minutes: 0,
        group_by: 'content',
        delivery: { mode: 'digest', interval_hours: 24 },
        notify_existing: false
    }
};

function switchRuleEditorMode(mode) {
    const isVisual = mode === 'visual';
    const visualBox = document.getElementById('ruleVisualBuilder');
    const jsonBox = document.getElementById('ruleJsonContainer');
    const btnV = document.getElementById('btnRuleModeVisual');
    const btnJ = document.getElementById('btnRuleModeJson');
    if (visualBox && jsonBox) {
        visualBox.classList.toggle('d-none', !isVisual);
        jsonBox.classList.toggle('d-none', isVisual);
    }
    if (btnV && btnJ) {
        btnV.classList.toggle('active', isVisual);
        btnV.classList.toggle('btn-primary', isVisual);
        btnV.classList.toggle('btn-outline-primary', !isVisual);
        btnJ.classList.toggle('active', !isVisual);
        btnJ.classList.toggle('btn-primary', !isVisual);
        btnJ.classList.toggle('btn-outline-secondary', isVisual);
    }
    if (isVisual) {
        try {
            const def = JSON.parse(document.getElementById('ruleDefinition').value || '{}');
            initRuleVisualBuilder(def);
        } catch (_e) { /* keep current visual state */ }
    } else {
        syncRuleVisualToJson();
    }
}

function onRuleTemplateSelect() {
    const sel = document.getElementById('ruleTemplateSelect');
    if (!sel || !sel.value || sel.value === 'custom') return;
    const tpl = RULE_TEMPLATES[sel.value];
    if (tpl) {
        initRuleVisualBuilder(tpl);
        syncRuleVisualToJson();
    }
}

function initRuleVisualBuilder(def) {
    if (!def) return;
    const unitEl = document.getElementById('ruleVisualUnit');
    if (unitEl) unitEl.value = def.unit || 'signal';

    const sigTypeEl = document.getElementById('ruleVisualSignalType');
    if (sigTypeEl) {
        const sigType = def.signals?.signal_type || (def.signals?.signal_types && def.signals.signal_types[0]) || '';
        sigTypeEl.value = sigType;
    }

    const confEl = document.getElementById('ruleVisualMinConfidence');
    if (confEl) confEl.value = def.min_confidence || '';

    const threshEl = document.getElementById('ruleVisualThreshold');
    if (threshEl) threshEl.value = (def.threshold && def.threshold.count) || 1;

    const coolEl = document.getElementById('ruleVisualCooldown');
    if (coolEl) coolEl.value = def.cooldown_minutes !== undefined ? def.cooldown_minutes : 60;

    const groupEl = document.getElementById('ruleVisualGroupBy');
    if (groupEl) groupEl.value = def.group_by || 'none';

    const delivEl = document.getElementById('ruleVisualDelivery');
    const digestContainer = document.getElementById('ruleVisualDigestHoursContainer');
    const digestHoursEl = document.getElementById('ruleVisualDigestHours');
    const mode = (def.delivery && def.delivery.mode) || 'immediate';
    if (delivEl) delivEl.value = mode;
    if (digestContainer) digestContainer.style.display = mode === 'digest' ? '' : 'none';
    if (digestHoursEl) digestHoursEl.value = (def.delivery && def.delivery.interval_hours) || 24;

    const critEl = document.getElementById('ruleVisualCriteriaSearch');
    if (critEl) critEl.value = (def.criteria && (def.criteria.text || def.criteria.search || (def.criteria.keywords && def.criteria.keywords[0]))) || '';

    const winToggle = document.getElementById('ruleVisualEventWindowToggle');
    const winContainer = document.getElementById('ruleVisualEventWindowContainer');
    const winFrom = document.getElementById('ruleVisualWindowFrom');
    const winTo = document.getElementById('ruleVisualWindowTo');
    const hasWin = Boolean(def.event_window_days);
    if (winToggle) winToggle.checked = hasWin;
    if (winContainer) winContainer.style.display = hasWin ? '' : 'none';
    if (winFrom) winFrom.value = def.event_window_days?.from !== undefined ? def.event_window_days.from : 0;
    if (winTo) winTo.value = def.event_window_days?.to !== undefined ? def.event_window_days.to : 30;

    const notifyExistingEl = document.getElementById('ruleVisualNotifyExisting');
    if (notifyExistingEl) notifyExistingEl.checked = Boolean(def.notify_existing);
}

function syncRuleVisualToJson() {
    const textarea = document.getElementById('ruleDefinition');
    if (!textarea) return;

    const unit = document.getElementById('ruleVisualUnit')?.value || 'signal';
    const sigType = document.getElementById('ruleVisualSignalType')?.value;
    const conf = document.getElementById('ruleVisualMinConfidence')?.value;
    const threshCount = parseInt(document.getElementById('ruleVisualThreshold')?.value, 10) || 1;
    const cooldown = parseInt(document.getElementById('ruleVisualCooldown')?.value, 10);
    const groupBy = document.getElementById('ruleVisualGroupBy')?.value || 'none';
    const delivMode = document.getElementById('ruleVisualDelivery')?.value || 'immediate';
    const digestHours = parseInt(document.getElementById('ruleVisualDigestHours')?.value, 10) || 24;
    const critSearch = (document.getElementById('ruleVisualCriteriaSearch')?.value || '').trim();
    const hasEventWin = Boolean(document.getElementById('ruleVisualEventWindowToggle')?.checked);
    const winFrom = parseInt(document.getElementById('ruleVisualWindowFrom')?.value, 10);
    const winTo = parseInt(document.getElementById('ruleVisualWindowTo')?.value, 10);
    const notifyExisting = Boolean(document.getElementById('ruleVisualNotifyExisting')?.checked);

    const def = {
        unit: unit,
        group_by: groupBy,
        threshold: { count: threshCount },
        cooldown_minutes: isNaN(cooldown) ? 60 : cooldown,
        delivery: delivMode === 'digest' ? { mode: 'digest', interval_hours: digestHours } : { mode: 'immediate' },
        notify_existing: notifyExisting
    };

    if (conf) {
        def.min_confidence = conf;
    }
    if (sigType) {
        def.signals = { signal_types: [sigType] };
    }
    if (hasEventWin) {
        def.event_window_days = { from: isNaN(winFrom) ? 0 : winFrom, to: isNaN(winTo) ? 30 : winTo };
    }
    if (critSearch) {
        def.criteria = { text: critSearch };
    }

    textarea.value = JSON.stringify(def, null, 2);
}

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
        emptyRow('ruleRows', 7, L.no_rules);
        return;
    }
    const rows = clear('ruleRows');
    for (const r of body.rules) {
        const name = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0 text-start' }, userText(r.name));
        name.addEventListener('click', () => openRule(r.id));

        const actionsTd = el('td');
        if (DATA.can_write && r.status !== 'archived') {
            const btnGroup = el('div', { class: 'btn-group btn-group-sm' });
            if (r.owner_user_id === DATA.user_id) {
                const editBtn = el('button', { type: 'button', class: 'btn btn-outline-secondary', title: L.action_edit }, el('i', { class: 'bi bi-pencil me-1' }), L.action_edit);
                editBtn.addEventListener('click', (e) => { e.stopPropagation(); openEditor('rule', r); });
                btnGroup.append(editBtn);
            }
            if (r.status === 'active') {
                const evalBtn = el('button', { type: 'button', class: 'btn btn-outline-primary', title: L.action_evaluate }, el('i', { class: 'bi bi-play me-1' }), L.action_evaluate);
                evalBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    guarded(async () => {
                        const res = await api('POST', `/api/rules/${r.id}/evaluate`);
                        await followJob(res.job, 'ruleJob');
                        await afterRuleChange((await api('GET', `/api/rules/${r.id}`)).rule);
                    });
                });
                btnGroup.append(evalBtn);
            }
            actionsTd.append(btnGroup);
        }

        rows.append(el('tr', { 'data-rule-id': r.id },
            el('td', {}, name), el('td', {}, statusBadge(r.status)), el('td', { text: r.version }),
            el('td', { text: r.baselined ? L.yes : L.no }), el('td', { text: when(r.last_evaluated_at) }),
            el('td', {}, userText(r.owner_username || String(r.owner_user_id))),
            actionsTd));
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
    on('btnModeVisual', 'click', () => switchEditorMode('visual'));
    on('btnModeJson', 'click', () => switchEditorMode('json'));
    on('scenarioTemplateSelect', 'change', onTemplateSelect);
    on('btnAddVisualCondition', 'click', () => addVisualConditionRow());
    on('btnAddVisualCase', 'click', () => addVisualCaseRow());
    on('visualStrategy', 'change', syncVisualToJson);
    on('visualDefaultOutcome', 'input', syncVisualToJson);
    on('visualNotifyExisting', 'change', syncVisualToJson);
    on('ruleNew', 'click', () => openEditor('rule', null));
    on('btnRuleModeVisual', 'click', () => switchRuleEditorMode('visual'));
    on('btnRuleModeJson', 'click', () => switchRuleEditorMode('json'));
    on('ruleTemplateSelect', 'change', onRuleTemplateSelect);
    on('ruleVisualUnit', 'change', syncRuleVisualToJson);
    on('ruleVisualSignalType', 'change', syncRuleVisualToJson);
    on('ruleVisualMinConfidence', 'change', syncRuleVisualToJson);
    on('ruleVisualThreshold', 'input', syncRuleVisualToJson);
    on('ruleVisualCooldown', 'input', syncRuleVisualToJson);
    on('ruleVisualGroupBy', 'change', syncRuleVisualToJson);
    on('ruleVisualDelivery', 'change', () => {
        const mode = document.getElementById('ruleVisualDelivery')?.value;
        const digestContainer = document.getElementById('ruleVisualDigestHoursContainer');
        if (digestContainer) digestContainer.style.display = mode === 'digest' ? '' : 'none';
        syncRuleVisualToJson();
    });
    on('ruleVisualDigestHours', 'input', syncRuleVisualToJson);
    on('ruleVisualCriteriaSearch', 'input', syncRuleVisualToJson);
    on('ruleVisualEventWindowToggle', 'change', () => {
        const toggle = document.getElementById('ruleVisualEventWindowToggle');
        const container = document.getElementById('ruleVisualEventWindowContainer');
        if (container) container.style.display = toggle?.checked ? '' : 'none';
        syncRuleVisualToJson();
    });
    on('ruleVisualWindowFrom', 'input', syncRuleVisualToJson);
    on('ruleVisualWindowTo', 'input', syncRuleVisualToJson);
    on('ruleVisualNotifyExisting', 'change', syncRuleVisualToJson);
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
