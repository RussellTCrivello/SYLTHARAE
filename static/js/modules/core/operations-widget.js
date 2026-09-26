/**
 * Operations widget: active-job summary, refreshed every 3 seconds.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #operations-widget-data.
var OPERATIONS_WIDGET_DATA = JSON.parse(document.getElementById('operations-widget-data').textContent || '{}');

(function () {
  const T_ACTIVE = OPERATIONS_WIDGET_DATA.active;
  const T_NO_OPS = OPERATIONS_WIDGET_DATA.noOperationsYetStartAnIngestion;
  const badge = (s) => (window.InforaxisStatus ? window.InforaxisStatus.badge(s) : '<span class="badge bg-secondary">' + String(s == null ? '' : s) + '</span>');
  async function refreshOps() {
    try {
      const sum = await fetch('/api/jobs/summary').then(r => r.json());
      if (sum.success) {
        document.getElementById('opsActive').textContent =
          `${sum.active} ${T_ACTIVE} · ` +
          Object.entries(sum.counts).map(([k, v]) => `${k.replace(/_/g,' ')}: ${v}`).join(' · ');
      }
      const jobs = await fetch('/api/jobs?limit=5').then(r => r.json());
      const rows = (jobs.jobs || []).slice(0, 5);
      document.getElementById('opsRows').innerHTML = rows.length ? rows.map(j => `
        <tr>
          <td><a href="/operations/jobs/${j.job_id}"><code>${j.job_id}</code></a></td>
          <td>${(j.job_type||'').replace(/_/g,' ')}</td>
          <td>${badge(j.status)}</td>
          <td style="min-width:90px;">
            <div class="progress" style="height:6px;">
              <!-- Transition on width so each poll animates instead of
                   snapping; the bar reflects the live, dynamically growing
                   workload (nested objects included). -->
              <div class="progress-bar" role="progressbar"
                   aria-valuemin="0" aria-valuemax="100" aria-valuenow="${j.progress||0}"
                   style="width:${j.progress||0}%; transition: width .3s ease;"></div>
            </div>
          </td>
          <td class="text-muted">
            ${j.progress||0}%${(j.statistics && j.statistics.files_discovered != null)
              ? ` (${(j.statistics.files_processed ?? 0)}/${j.statistics.files_discovered})`
              : ''}
          </td>
        </tr>`).join('')
        : `<tr><td class="text-muted">${T_NO_OPS}</td></tr>`;
    } catch (e) { /* widget is non-critical */ }
  }
  refreshOps();
  setInterval(refreshOps, 3000);
})();
