/**
 * Job detail: live status, progress and controls for one job.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #job-detail-page-data.
var JOB_DETAIL_PAGE_DATA = JSON.parse(document.getElementById('job-detail-page-data').textContent || '{}');

(function () {
  const JOB_ID = JOB_DETAIL_PAGE_DATA.jobId;
  const csrf = {'X-CSRFToken': JOB_DETAIL_PAGE_DATA.csrfToken};
  const badge = (s) => (window.InforaxisStatus ? window.InforaxisStatus.badge(s) : '<span class="badge bg-secondary">' + String(s == null ? '' : s) + '</span>');
  let lastEventId = 0;
  let errorsCache = [], warningsCache = [];

  async function refresh() {
    const r = await fetch(`/api/jobs/${JOB_ID}`);
    const d = await r.json();
    if (!d.success) { document.getElementById('jStatus').textContent = JOB_DETAIL_PAGE_DATA.notFound; return; }
    const j = d.job;
    document.getElementById('jType').textContent = (j.job_type||'').replace(/_/g,' ');
    document.getElementById('jStatus').innerHTML = badge(j.status);
    document.getElementById('jPhase').textContent = j.current_phase || '—';
    document.getElementById('jItem').textContent = j.current_item || '—';
    document.getElementById('jSource').textContent = j.source || '—';
    document.getElementById('jUser').textContent = j.created_by || '—';
    document.getElementById('jStart').textContent = j.started_at || '—';
    document.getElementById('jEnd').textContent = j.completed_at || '—';

    const st = j.statistics || {};

    // PROGRESS: live counters. `Discovered` is the dynamic total (top level +
    // everything materialised out of containers), so it is shown next to the
    // initial top-level count and the nested difference.
    const done = st.files_processed ?? 0;
    const total = st.files_discovered ?? 0;
    const bar = document.getElementById('jBar');
    const pct = Number(j.progress) || 0;
    bar.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', String(pct));
    // Stop the "working" stripes once the job is no longer running.
    const isActive = ['QUEUED','RUNNING','PAUSED','CANCELLING'].includes(j.status);
    bar.classList.toggle('progress-bar-animated', isActive);
    bar.classList.toggle('progress-bar-striped', isActive);

    document.getElementById('jPercent').textContent = pct + '%';
    document.getElementById('jCounters').textContent =
      `${done} / ${total} units of work` +
      ((st.files_in_progress ?? 0) ? ` · ${st.files_in_progress} running` : '') +
      ((st.files_pending ?? 0) ? ` · ${st.files_pending} queued` : '');

    const nested = st.files_nested ?? 0;
    document.getElementById('jNested').innerHTML = nested > 0
      ? `<i class="bi bi-files me-1"></i>${nested} nested object(s) discovered from ` +
        `${st.files_initial ?? (total - nested)} top-level file(s)` +
        ((st.containers_opened ?? 0) ? ` · ${st.containers_opened} container(s) expanded` : '')
      : '';

    const cells = [
      ['Discovered', st.files_discovered], ['Top level', st.files_initial],
      ['Nested', st.files_nested], ['Processed', st.files_processed],
      ['Succeeded', st.files_succeeded], ['Failed', st.files_failed],
      ['Skipped', st.files_skipped], ['Unsupported', st.files_unsupported],
      ['Retryable', st.files_retryable], ['Running', st.files_in_progress],
      ['Queued', st.files_pending], ['Duplicates', st.duplicates],
    ];
    document.getElementById('jStats').innerHTML = cells.map(([k, v]) => `
      <div class="col-4 col-md-2"><div class="border rounded p-2">
        <div class="fs-5">${v ?? '—'}</div><div class="text-muted small">${k}</div>
      </div></div>`).join('');
    document.getElementById('jResult').textContent =
      j.result_summary ? JSON.stringify(j.result_summary, null, 1) : '—';

    // errors & warnings
    const er = await fetch(`/api/jobs/${JOB_ID}/errors`).then(r => r.json());
    errorsCache = er.errors || []; warningsCache = er.warnings || [];
    renderErrors();

    // actions by state
    const actions = [];
    if (['RUNNING','CANCELLING','QUEUED'].includes(j.status))
      actions.push(`<button class="btn btn-outline-danger" data-on-click="act('cancel')">Cancel Job</button>`);
    if (j.status === 'RUNNING')
      actions.push(`<button class="btn btn-outline-warning" data-on-click="act('pause')">Pause</button>`);
    if (j.status === 'PAUSED')
      actions.push(`<button class="btn btn-outline-success" data-on-click="act('resume')">Resume</button>`);
    if (['FAILED','CANCELLED','COMPLETED_WITH_WARNINGS'].includes(j.status))
      actions.push(`<button class="btn btn-outline-secondary" data-on-click="act('retry')">Retry</button>`);
    document.getElementById('jActions').innerHTML =
      actions.join('') || '<span class="text-muted small">No actions available</span>';

    loadEvents();
  }

  function renderErrors() {
    const q = (document.getElementById('errFilter').value || '').toLowerCase();
    const items = [
      ...errorsCache.filter(e => !q || (e||'').toLowerCase().includes(q)).map(e => `<div class="text-danger mb-1"><i class="bi bi-x-circle-fill me-1" aria-hidden="true"></i>${escapeHtml(e)}</div>`),
      ...warningsCache.filter(w => !q || (w||'').toLowerCase().includes(q)).map(w => `<div class="text-warning mb-1"><i class="bi bi-exclamation-triangle-fill me-1" aria-hidden="true"></i>${escapeHtml(w)}</div>`),
    ];
    document.getElementById('jErrors').innerHTML =
      items.join('') || '<span class="text-muted"><i class="bi bi-check-circle me-1" aria-hidden="true"></i>No errors or warnings</span>';
  }
  document.getElementById('errFilter').addEventListener('input', renderErrors);

  async function loadEvents() {
    const r = await fetch(`/api/jobs/${JOB_ID}/events?after_id=${lastEventId}&limit=500`);
    const d = await r.json();
    const evts = d.events || [];
    if (!evts.length && lastEventId === 0) return;
    const body = document.getElementById('jEvents');
    if (lastEventId === 0) body.innerHTML = '';
    evts.forEach(e => {
      lastEventId = Math.max(lastEventId, e.event_id);
      const row = document.createElement('tr');
      row.innerHTML = `<td class="text-nowrap">${(e.created_at||'').slice(11,19)}</td>
        <td class="text-nowrap">${e.event_type}</td>
        <td class="text-break">${escapeHtml(JSON.stringify(e.payload||{}))}</td>`;
      body.prepend(row);
    });
    document.getElementById('evtCount').textContent = `${lastEventId} events`;
  }
  function escapeHtml(s){ const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  window.act = async function (action) {
    const r = await fetch(`/api/jobs/${JOB_ID}/${action}`, {method:'POST', headers: csrf});
    const d = await r.json();
    if (!d.success) alert((d.error && d.error.message) || `Cannot ${action} this job`);
    refresh();
  };

  refresh();
  setInterval(refresh, 2000);
})();
