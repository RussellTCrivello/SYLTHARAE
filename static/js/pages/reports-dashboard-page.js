/**
 * Reports Dashboard page (step 22) — the reporting system at a glance.
 *
 * Everything on this page is a view over an existing API: the measured
 * overview (`/api/reports/dashboard`), the catalog
 * (`/api/reports/definitions`), the runs (`/api/reports/runs`, Load More)
 * and the schedules (`/api/schedules`). No second runner, no second list.
 *
 * CSP-safe: no eval, no inline script, no Function constructor.
 */

import { getCSRFTokenAsync } from '../modules/core/utils.js';

const DATA = JSON.parse(document.getElementById('reports-dashboard-page-data').textContent);
const L = JSON.parse(document.getElementById('reports-dashboard-labels').textContent);

const CATALOG_BODY = document.getElementById('catalogTableBody');
const RUNS_BODY = document.getElementById('runsTableBody');
const SCHEDULES_BODY = document.getElementById('schedulesTableBody');
const RUN_STATUS = document.getElementById('dashRunStatus');
const ALL_USERS = document.getElementById('dashAllUsers');

const RUNS_PAGE = 50;
const state = { runsTotal: 0, runsShown: 0 };

class ApiError extends Error {
    constructor(status, body) {
        super((body && body.error && body.error.message) || `HTTP ${status}`);
        this.code = body && body.error && body.error.code;
    }
}

async function api(method, url) {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') headers['X-CSRFToken'] = await getCSRFTokenAsync();
    const resp = await fetch(url, {
        method, credentials: 'same-origin', headers,
    });
    const body = await resp.json().catch(() => null);
    if (!resp.ok || (body && body.success === false)) throw new ApiError(resp.status, body);
    return body;
}

function showError(error) {
    const box = document.getElementById('dashError');
    const message = box.querySelector('.state-message') || box;
    message.textContent = (error && error.message) || L.load_failed;
    box.classList.remove('d-none');
}

function escapeCell(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function td(value, html) {
    return `<td data-ut-value="${escapeCell(value == null ? '' : value)}">${html}</td>`;
}

function fmtWhen(iso) {
    if (!iso) return escapeCell(L.never);
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? escapeCell(iso) : d.toLocaleString();
}

function allUsersParam() {
    return (DATA.is_admin && ALL_USERS && ALL_USERS.checked) ? '?all=1' : '';
}

// -- tiles -----------------------------------------------------------------

function setTile(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
}

function renderTiles(body) {
    setTile('tileVersions', String(body.catalog.versions));
    setTile('tileFamilies', `${body.catalog.families} ${L.families}`);
    setTile('tileRuns', String(body.runs.total));
    const c = body.runs.counts;
    setTile('tileRunsBreakdown',
        `${c.completed} ${L.completed} · ${c.failed} ${L.failed} · ${c.running} ${L.running}`);
    setTile('tileArtifacts', String(body.artifacts.total));
    const formats = Object.entries(body.artifacts.by_format)
        .map(([f, n]) => `${f} ${n}`).join(' · ');
    setTile('tileArtifactsBreakdown', formats || '—');
    setTile('tileSchedules', `${body.schedules.enabled} ${L.schedules_enabled} ${body.schedules.total}`);
    setTile('tileNextFire', `${L.next_fire}: ${fmtWhen(body.schedules.next_fire)}`);
}

async function loadOverview() {
    const body = await api('GET', `/api/reports/dashboard${allUsersParam()}`);
    renderTiles(body);
}

// -- catalog -----------------------------------------------------------------

function catalogRow(d) {
    return '<tr>'
        + td(d.report_id, `<strong>${escapeCell(d.report_id)}</strong>`)
        + td(d.version, escapeCell(String(d.version)))
        + td(d.datasets.length, escapeCell(String(d.datasets.length)))
        + td(d.analyses.length, escapeCell(String(d.analyses.length)))
        + td(d.description, escapeCell(d.description))
        + td('', `<a class="btn btn-sm btn-outline-primary" href="/reports">${escapeCell(L.open)}</a>`)
        + '</tr>';
}

async function loadCatalog() {
    const body = await api('GET', '/api/reports/definitions');
    const rows = (body.items || []).map(catalogRow);
    if (window.UnifiedTable) {
        window.UnifiedTable.renderRows(CATALOG_BODY,
            rows.length ? rows : [window.UnifiedTable.states.empty(6, {
                message: 'No reports are declared for your role.' })]);
    }
}

// -- runs (server table with Load More) ---------------------------------------

function statusBadge(status) {
    const tone = { completed: 'success', failed: 'danger', refused: 'warning',
        cancelled: 'secondary', running: 'primary', queued: 'secondary' }[status]
        || 'secondary';
    return `<span class="badge text-bg-${tone}">${escapeCell(status)}</span>`;
}

function runRow(run) {
    const datasets = run.datasets || [];
    const shortened = datasets.some((d) => d.truncated);
    const datasetCell = escapeCell(String(datasets.length))
        + (shortened ? ` <span class="badge text-bg-warning">${escapeCell(L.truncated)}</span>` : '');
    const report = `${escapeCell(run.report_id)}@${escapeCell(String(run.report_version))}`;
    const requester = escapeCell(run.requester_username || '—')
        + (allUsersParam() ? ` <span class="text-muted small">(${escapeCell(L.all_scope)})</span>` : '');
    return '<tr>'
        + td(run.id, `<code>${escapeCell(run.id)}</code>`)
        + td(run.report_id, report)
        + td(run.status, statusBadge(run.status))
        + td(run.requester_username || '', requester)
        + td(run.requested_at || '', fmtWhen(run.requested_at))
        + td(datasets.length, datasetCell)
        + td('', `<a class="btn btn-sm btn-outline-primary" title="${escapeCell(L.open)}"
            href="/reports?run=${encodeURIComponent(run.id)}"><i class="bi bi-box-arrow-up-right"></i></a>`)
        + '</tr>';
}

function runsUrl(offset, limit) {
    const params = new URLSearchParams();
    if (allUsersParam()) params.set('all', '1');
    if (RUN_STATUS && RUN_STATUS.value) params.set('status', RUN_STATUS.value);
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    return `/api/reports/runs?${params.toString()}`;
}

async function loadRunsFirstPage() {
    if (window.UnifiedTable) {
        window.UnifiedTable.renderRows(RUNS_BODY,
            [window.UnifiedTable.states.loading(7, '...')]);
    }
    const body = await api('GET', runsUrl(0, RUNS_PAGE));
    state.runsTotal = body.total || 0;
    const rows = (body.items || []).map(runRow);
    state.runsShown = rows.length;
    if (window.UnifiedTable) {
        window.UnifiedTable.renderRows(RUNS_BODY,
            rows.length ? rows : [window.UnifiedTable.states.empty(7, {
                message: 'No runs yet.' })]);
        window.UnifiedTable.syncLoadMore('runsTable', state.runsShown,
            state.runsTotal, RUNS_PAGE);
    }
}

async function loadMoreRuns() {
    const body = await api('GET', runsUrl(state.runsShown, RUNS_PAGE));
    const rows = (body.items || []).map(runRow);
    state.runsShown += rows.length;
    state.runsTotal = body.total || state.runsTotal;
    if (window.UnifiedTable) {
        window.UnifiedTable.appendRows('runsTable', { html: rows, total: state.runsTotal });
    }
}

// -- schedules -----------------------------------------------------------------

function scheduleRow(row) {
    const status = row.enabled
        ? `<span class="badge text-bg-success">${escapeCell('enabled')}</span>`
        : `<span class="badge text-bg-secondary">${escapeCell('paused')}</span>`;
    return '<tr>'
        + td(row.name || '', `<strong>${escapeCell(row.name || '')}</strong>`)
        + td(row.schedule_type || '', escapeCell(row.schedule_type || ''))
        + td(row.interval_minutes, escapeCell(String(row.interval_minutes)))
        + td(row.next_run_at || '', fmtWhen(row.next_run_at))
        + td(row.enabled ? 'enabled' : 'paused', status)
        + td('', `<a class="btn btn-sm btn-outline-secondary" title="${escapeCell(L.manage)}"
            href="/schedules"><i class="bi bi-gear"></i></a>`)
        + '</tr>';
}

async function loadSchedules() {
    const body = await api('GET', `/api/schedules${allUsersParam()}`);
    const rows = (body.items || []).map(scheduleRow);
    if (window.UnifiedTable) {
        window.UnifiedTable.renderRows(SCHEDULES_BODY,
            rows.length ? rows : [window.UnifiedTable.states.empty(6, {
                message: 'No schedules.' })]);
    }
}

// -- wiring ----------------------------------------------------------------------

async function refresh(clearStatus) {
    document.getElementById('dashError').classList.add('d-none');
    if (!clearStatus) { /* keep for future status line */ }
    await Promise.all([loadOverview(), loadCatalog(), loadRunsFirstPage(),
        loadSchedules()]);
}

document.addEventListener('DOMContentLoaded', () => {
    if (RUN_STATUS) {
        RUN_STATUS.addEventListener('change', () => { loadRunsFirstPage().catch(showError); });
    }
    if (ALL_USERS) {
        ALL_USERS.addEventListener('change', () => { refresh().catch(showError); });
    }
    if (window.UnifiedTable && typeof window.UnifiedTable.onLoadMore === 'function') {
        window.UnifiedTable.onLoadMore('runsTable', () => { loadMoreRuns().catch(showError); });
    }
    refresh().catch(showError);
});

const page = { refresh };
Object.assign(window, { refresh: page.refresh });
export default page;
