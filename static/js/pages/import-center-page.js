/**
 * Import center: backup validation, restore and bulk-import staging.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #import-center-page-data.
var IMPORT_CENTER_PAGE_DATA = JSON.parse(document.getElementById('import-center-page-data').textContent || '{}');

(function () {
  const csrfHeader = {'X-CSRFToken': IMPORT_CENTER_PAGE_DATA.csrfToken};
  const jsonHeaders = {'Content-Type': 'application/json', ...csrfHeader};
  let pendingImport = null;

  async function loadSelects() {
    const [s, d] = await Promise.all([
      fetch('/api/input/sources').then(r => r.json()),
      fetch('/api/input/sides').then(r => r.json())
    ]);
    fill('biSource', s.sources || []);
    fill('biSide', d.sides || []);
  }
  function esc(v) { const d = document.createElement('div'); d.textContent = String(v); return d.innerHTML; }
  function fill(id, rows) {
    document.getElementById(id).innerHTML =
      '<option value="">' + IMPORT_CENTER_PAGE_DATA.select + '</option>' +
      rows.map(r => `<option value="${esc(r.name)}">${esc(r.name)}</option>`).join('');
  }
  loadSelects();

  function showPreview(preview, pending) {
    const box = document.getElementById('previewBox');
    pendingImport = pending;
    box.classList.remove('text-muted');
    box.innerHTML = Object.entries(preview).map(([k, v]) => {
      if (v === null || v === undefined) return '';
      if (typeof v === 'object') return '';
      return `<div><strong>${esc(k.replace(/_/g,' '))}:</strong> ${esc(v)}</div>`;
    }).join('') + '<div class="text-muted mt-2">' + IMPORT_CENTER_PAGE_DATA.reviewCarefullyImportsRunAsJobs + '</div>';
    document.getElementById('confirmRow').classList.remove('d-none');
  }

  function showMsg(text, ok) {
    // Server messages can echo user input (paths, names): text, never HTML.
    const box = document.createElement('div');
    box.className = `alert ${ok ? 'alert-success' : 'alert-danger'} py-2`;
    box.textContent = text;
    document.getElementById('importMsg').replaceChildren(box);
  }

  async function postJson(url, body) {
    const r = await fetch(url, {method:'POST', headers: jsonHeaders, body: JSON.stringify(body)});
    return r.json();
  }

  // --- domain import -------------------------------------------------
  document.getElementById('diValidate').addEventListener('click', async () => {
    const d = await postJson('/api/import/validate', {type:'domain_import', data_file: v('diFile') || null});
    if (d.success) showPreview({...(d.preview||{}), note: 'validated'}, null);
    else showMsg(d.error ? d.error.message : IMPORT_CENTER_PAGE_DATA.validationFailed, false);
  });
  document.getElementById('diPreview').addEventListener('click', async () => {
    const d = await postJson('/api/import/preview', {type:'domain_import', data_file: v('diFile') || null});
    if (d.success) { showPreview(d.stats || d.job.statistics || {status: d.job.status, note:'dry run job completed'}, {type:'domain_import', data_file: v('diFile') || null}); }
    else showMsg(d.error ? d.error.message : IMPORT_CENTER_PAGE_DATA.previewFailed, false);
  });
  document.getElementById('diStart').addEventListener('click', () => startJob({type:'domain_import', data_file: v('diFile') || null}));

  // --- backup restore ------------------------------------------------
  document.getElementById('buValidate').addEventListener('click', async () => {
    const f = document.getElementById('buFile').files[0];
    if (!f) { showMsg(IMPORT_CENTER_PAGE_DATA.chooseABackupFileFirst, false); return; }
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch('/api/import/jobs', {method:'POST', headers: csrfHeader, body: fd});
    const d = await r.json();
    if (d.success) { showMsg(IMPORT_CENTER_PAGE_DATA.backupStagedStartingValidationJob, true); window.location = '/operations/jobs/' + d.job.job_id; }
    else showMsg(d.error ? d.error.message : IMPORT_CENTER_PAGE_DATA.stagingFailed, false);
  });
  document.getElementById('buStart').addEventListener('click', async () => {
    const f = document.getElementById('buFile').files[0];
    if (!f) { showMsg(IMPORT_CENTER_PAGE_DATA.chooseABackupFileFirst, false); return; }
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch('/api/import/jobs?type=backup_import', {method:'POST', headers: csrfHeader, body: fd});
    const d = await r.json();
    if (d.success) window.location = '/operations/jobs/' + d.job.job_id;
    else showMsg(d.error ? d.error.message : IMPORT_CENTER_PAGE_DATA.restoreJobFailedToStart, false);
  });

  // --- server batch --------------------------------------------------
  function pathList() {
    return document.getElementById('biPaths').value.split('\n').map(s => s.trim()).filter(Boolean);
  }
  document.getElementById('biValidate').addEventListener('click', async () => {
    const d = await postJson('/api/import/validate', {type:'batch_import', file_paths: pathList()});
    if (d.success) showPreview(d.preview, {type:'batch_import', file_paths: pathList(),
      source: v('biSource'), side: v('biSide')});
    else showMsg(d.error ? d.error.message : 'Validation failed', false);
  });
  document.getElementById('biStart').addEventListener('click', () => {
    if (!v('biSource') || !v('biSide')) { showMsg(IMPORT_CENTER_PAGE_DATA.selectASourceAndASide, false); return; }
    startJob({type:'batch_import', file_paths: pathList(), source: v('biSource'), side: v('biSide')});
  });

  // --- confirm ---------------------------------------------------------
  document.getElementById('btnConfirmStart').addEventListener('click', () => {
    if (!pendingImport) { showMsg(IMPORT_CENTER_PAGE_DATA.nothingToStartRunAPreview, false); return; }
    startJob(pendingImport);
  });
  document.getElementById('btnClearPreview').addEventListener('click', () => {
    document.getElementById('previewBox').innerHTML = IMPORT_CENTER_PAGE_DATA.cleared;
    document.getElementById('confirmRow').classList.add('d-none');
    pendingImport = null;
  });

  async function startJob(body) {
    const d = await postJson('/api/import/jobs', body);
    if (d.success && d.job) window.location = '/operations/jobs/' + d.job.job_id;
    else showMsg((d.error && d.error.message) || IMPORT_CENTER_PAGE_DATA.importJobCouldNotBeCreated, false);
  }

  function v(id){ return document.getElementById(id).value.trim(); }
})();
