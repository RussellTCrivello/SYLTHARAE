/**
 * Operations widget: active-job summary, refreshed every 3 seconds.
 *
 * This widget is page-scoped. navigation-swap emits a disposal event before
 * replacing its DOM so polling does not survive route changes or multiply
 * each time a dashboard is revisited.
 */
(function () {
  const widget = document.getElementById('opsWidget');
  const dataNode = document.getElementById('operations-widget-data');
  if (!widget || !dataNode) return;

  // A dashboard can be visited again without a full document load. Stop any
  // previous instance before binding this route's DOM.
  if (window.__operationsWidgetState) {
    window.__operationsWidgetState.dispose();
  }

  let data = {};
  try {
    data = JSON.parse(dataNode.textContent || '{}');
  } catch (_error) { /* fall back to the untranslated keys below */ }

  const state = {
    timer: null,
    controller: new AbortController(),
    disposed: false,
    dispose: null,
  };
  const activeEl = widget.querySelector('#opsActive');
  const rowsEl = widget.querySelector('#opsRows');
  const T_ACTIVE = data.active || 'active';
  const T_NO_OPS = data.noOperationsYetStartAnIngestion || 'No operations yet - start an ingestion.';

  function isCurrent() {
    return !state.disposed && widget.isConnected
      && window.__operationsWidgetState === state;
  }

  state.dispose = function () {
    if (state.disposed) return;
    state.disposed = true;
    if (state.timer !== null) window.clearInterval(state.timer);
    state.controller.abort();
    window.removeEventListener('syltharae:before-page-swap', state.dispose);
    if (window.__operationsWidgetState === state) {
      window.__operationsWidgetState = null;
    }
  };
  window.__operationsWidgetState = state;
  window.addEventListener('syltharae:before-page-swap', state.dispose, { once: true });

  const badge = (status) => (window.InforaxisStatus
    ? window.InforaxisStatus.badge(status)
    : '<span class="badge bg-secondary">' + String(status == null ? '' : status) + '</span>');

  async function refreshOps() {
    if (!isCurrent()) {
      state.dispose();
      return;
    }
    try {
      const summaryResponse = await fetch('/api/jobs/summary', {
        signal: state.controller.signal,
        credentials: 'same-origin',
      });
      if (!summaryResponse.ok) return;
      const summary = await summaryResponse.json();
      if (!isCurrent()) return;
      if (summary.success && activeEl) {
        activeEl.textContent = `${summary.active} ${T_ACTIVE} · ` +
          Object.entries(summary.counts || {})
            .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`)
            .join(' · ');
      }

      const jobsResponse = await fetch('/api/jobs?limit=5', {
        signal: state.controller.signal,
        credentials: 'same-origin',
      });
      if (!jobsResponse.ok || !isCurrent()) return;
      const jobs = await jobsResponse.json();
      if (!isCurrent() || !rowsEl) return;
      const rows = (jobs.jobs || []).slice(0, 5);
      rowsEl.innerHTML = rows.length ? rows.map((job) => `
        <tr>
          <td><a href="/operations/jobs/${job.job_id}"><code>${job.job_id}</code></a></td>
          <td>${(job.job_type || '').replace(/_/g, ' ')}</td>
          <td>${badge(job.status)}</td>
          <td style="min-width:90px;">
            <div class="progress" style="height:6px;">
              <div class="progress-bar" role="progressbar"
                   aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress || 0}"
                   style="width:${job.progress || 0}%; transition: width .3s ease;"></div>
            </div>
          </td>
          <td class="text-muted">
            ${job.progress || 0}%${(job.statistics && job.statistics.files_discovered != null)
              ? ` (${(job.statistics.files_processed ?? 0)}/${job.statistics.files_discovered})`
              : ''}
          </td>
        </tr>`).join('')
        : `<tr><td class="text-muted">${T_NO_OPS}</td></tr>`;
    } catch (_error) {
      // Polling is non-critical; route disposal intentionally aborts it.
    }
  }

  refreshOps();
  state.timer = window.setInterval(refreshOps, 3000);
})();
