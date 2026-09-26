/**
 * Install wizard: system check, database setup and first run.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #install-wizard-page-data.
var INSTALL_WIZARD_PAGE_DATA = JSON.parse(document.getElementById('install-wizard-page-data').textContent || '{}');

(function() {
    'use strict';

    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const HEADERS = {'Content-Type': 'application/json', 'X-CSRFToken': CSRF};

    let currentStep = 1;
    const TOTAL_STEPS = 5;

    // ── DOM refs ──
    const btnBack = document.getElementById('btn-back');
    const btnNext = document.getElementById('btn-next');
    const panels = document.querySelectorAll('.step-panel');
    const pills  = document.querySelectorAll('.step-pill');
    const stepCounter = document.getElementById('step-counter');
    const progressFill = document.getElementById('stepper-progress-fill');

    // ── Inline error helper (Step 3) ──
    function showStepError(msg) {
        const box = document.getElementById('step3-error');
        document.getElementById('step3-error-msg').textContent = msg;
        box.classList.add('show');
    }
    function clearStepError() {
        document.getElementById('step3-error').classList.remove('show');
    }
    document.getElementById('admin_username').addEventListener('input', clearStepError);
    document.getElementById('admin_password').addEventListener('input', clearStepError);
    document.getElementById('admin_password2').addEventListener('input', clearStepError);

    // ── Password visibility toggles (presentation only) ──
    document.querySelectorAll('.pw-toggle').forEach(function(btn) {
        btn.addEventListener('click', function() {
            const input = document.getElementById(btn.dataset.target);
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.innerHTML = show
                ? '<i class="bi bi-eye-slash" aria-hidden="true"></i>'
                : '<i class="bi bi-eye" aria-hidden="true"></i>';
            btn.setAttribute('aria-label', show ? btn.dataset.hideLabel : btn.dataset.showLabel);
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        });
    });

    // Keep the browser's native length validation, the strength hint and the
    // custom cross-check in step 3 aligned with the password rule the operator
    // has chosen on this same step.
    const minPasswordField = document.getElementById('sec_pw_min');
    function syncPasswordMinimum() {
        const minLength = Number(minPasswordField.value) || 12;
        ['admin_password', 'admin_password2'].forEach((id) => {
            document.getElementById(id).minLength = minLength;
        });
        const hint = document.getElementById('pw-hint');
        const icon = document.createElement('i');
        icon.className = 'bi bi-shield-lock';
        icon.setAttribute('aria-hidden', 'true');
        hint.replaceChildren(icon, document.createTextNode(' Minimum ' + minLength + ' characters'));
    }
    minPasswordField.addEventListener('input', syncPasswordMinimum);
    minPasswordField.addEventListener('change', syncPasswordMinimum);
    syncPasswordMinimum();

    // ── Navigation ──
    function goTo(n) {
        if (n < 1 || n > TOTAL_STEPS + 1) return;
        panels.forEach(p => p.classList.remove('active'));
        pills.forEach(p => { p.classList.remove('active','done'); });
        if (n <= TOTAL_STEPS) {
            document.getElementById('step-' + n).classList.add('active');
        }
        for (let i = 1; i <= TOTAL_STEPS; i++) {
            const pill = document.querySelector(`.step-pill[data-step="${i}"]`);
            if (i < n) pill.classList.add('done');
            else if (i === n) pill.classList.add('active');
            if (i === n) pill.setAttribute('aria-current', 'step');
            else pill.removeAttribute('aria-current');
        }
        currentStep = n;
        // Progress read-outs (presentation only)
        if (stepCounter) {
            stepCounter.textContent = n > TOTAL_STEPS
                ? 'Complete'
                : 'Step ' + n + ' of ' + TOTAL_STEPS;
        }
        if (progressFill) {
            progressFill.style.width =
                (((Math.min(n, TOTAL_STEPS) - 1) / (TOTAL_STEPS - 1)) * 100) + '%';
        }
        btnBack.disabled = (n === 1 || n === TOTAL_STEPS + 1);
        if (n === TOTAL_STEPS) {
            btnNext.style.display = 'none';
            btnBack.style.display = 'none';
        } else if (n === TOTAL_STEPS + 1) {
            btnNext.style.display = 'none';
            btnBack.style.display = 'none';
            document.getElementById('step-done').classList.add('active');
        } else {
            btnNext.style.display = '';
            btnBack.style.display = '';
            btnNext.innerHTML = n === TOTAL_STEPS - 1
                ? '<i class="bi bi-rocket-takeoff"></i> Install'
                : 'Next <i class="bi bi-arrow-right"></i>';
        }
    }

    btnBack.addEventListener('click', () => goTo(currentStep - 1));
    btnNext.addEventListener('click', () => {
        clearStepError();
        if (!validateStep(currentStep)) return;
        if (currentStep === 4) { runInstallation(); return; }
        if (currentStep === 1) { runSystemCheck().then(() => goTo(2)); return; }
        goTo(currentStep + 1);
    });

    // ── Validation ──
    function validateStep(n) {
        const panel = document.getElementById('step-' + n);
        if (!panel) return false;

        // The wizard is not a form, so native required/min/max constraints do
        // not run on their own. Check them explicitly before changing steps and
        // let the browser focus and explain the first invalid control.
        const invalid = Array.from(panel.querySelectorAll('input, select, textarea'))
            .find((field) => !field.checkValidity());
        if (invalid) {
            invalid.reportValidity();
            return false;
        }

        if (n === 3) {
            const pw = document.getElementById('admin_password').value;
            const pw2 = document.getElementById('admin_password2').value;
            const minLen = parseInt(document.getElementById('sec_pw_min').value, 10);
            if (pw !== pw2) {
                showStepError('Passwords do not match.');
                document.getElementById('admin_password2').focus();
                return false;
            }
            const idleHours = Number(document.getElementById('sec_idle').value);
            const sessionHours = Number(document.getElementById('sec_session').value);
            if (idleHours > sessionHours) {
                showStepError('Idle timeout cannot exceed the session lifetime.');
                document.getElementById('sec_idle').focus();
                return false;
            }
            const ratePerMinute = Number(document.getElementById('sec_rate_min').value);
            const ratePerHour = Number(document.getElementById('sec_rate_hour').value);
            if (ratePerMinute > ratePerHour) {
                showStepError('The per-minute rate limit cannot exceed the per-hour rate limit.');
                document.getElementById('sec_rate_min').focus();
                return false;
            }
            if (pw.length < minLen) {
                showStepError('Admin password must be at least ' + minLen + ' characters.');
                document.getElementById('admin_password').focus();
                return false;
            }
        }
        return true;
    }

    // ── Step 1: System check ──
    async function runSystemCheck() {
        const alertRegion = document.getElementById('step1-alert');
        alertRegion.replaceChildren();
        const items = {
            python:    document.getElementById('chk-python'),
            packages:  document.getElementById('chk-packages'),
            postgresql:document.getElementById('chk-postgresql'),
            disk:      document.getElementById('chk-disk'),
            os:        document.getElementById('chk-os'),
        };
        for (const el of Object.values(items)) {
            el.className = 'check-item';
            el.querySelector('.icon').innerHTML = '<i class="bi bi-arrow-repeat icon-spin"></i>';
            el.querySelector('.detail').textContent = 'Checking…';
        }
        try {
            const r = await fetch('/api/setup/system-check');
            const d = await r.json();
            const c = d.checks;

            setCheck(items.python, c.python.ok, c.python.version, c.python.message);
            setCheck(items.packages, c.packages.ok,
                c.packages.ok ? 'All imported' : c.packages.missing.join(', '),
                c.packages.message);
            setCheck(items.postgresql, c.postgresql.ok,
                c.postgresql.ok ? 'Running' : 'Not available',
                c.postgresql.message);
            setCheck(items.disk, c.disk.ok,
                c.disk.free_mb > 0 ? c.disk.free_mb + ' MB free' : '',
                c.disk.message);
            setCheck(items.os, true, c.os.name + ' ' + c.os.arch, '');

            if (!d.all_ok) {
                document.getElementById('step1-alert').innerHTML =
                    '<div class="alert alert-warning" role="alert">' +
                    '<i class="bi bi-exclamation-triangle-fill"></i><div>' +
                    '<strong>Some checks failed.</strong> You can still proceed, but ' +
                    'you may need to install missing components before the database step ' +
                    'will succeed. See INSTALL.md for help.</div></div>';
            }
        } catch(e) {
            renderInstallAlert(
                alertRegion, 'danger', 'bi-wifi-off', 'Could not reach the server:',
                e && e.message ? e.message : 'Please try again.',
                INSTALL_WIZARD_PAGE_DATA.retrySystemCheck, () => runSystemCheck());
        }
    }

    function setCheck(el, ok, detail, message) {
        el.className = 'check-item ' + (ok ? 'ok' : 'fail');
        el.querySelector('.icon').innerHTML = ok
            ? '<i class="bi bi-check-circle-fill"></i>'
            : '<i class="bi bi-x-circle-fill"></i>';
        el.querySelector('.detail').textContent = detail || message || '';
    }

    // ── Step 2: Test database ──
    document.getElementById('btn-test-db').addEventListener('click', async () => {
        const btn = document.getElementById('btn-test-db');
        const result = document.getElementById('db-test-result');
        result.replaceChildren();
        result.className = 'test-result';
        const fields = ['db_host', 'db_port', 'db_user', 'db_name', 'db_password']
            .map((id) => document.getElementById(id));
        const invalid = fields.find((field) => !field.checkValidity());
        if (invalid) {
            invalid.reportValidity();
            return;
        }

        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        btn.innerHTML = '<i class="bi bi-arrow-repeat icon-spin" aria-hidden="true"></i> Testing…';
        try {
            const r = await fetch('/api/setup/test-database', {
                method: 'POST', headers: HEADERS,
                body: JSON.stringify({
                    host: document.getElementById('db_host').value,
                    port: document.getElementById('db_port').value,
                    user: document.getElementById('db_user').value,
                    password: document.getElementById('db_password').value,
                    database: document.getElementById('db_name').value,
                })
            });
            const d = await r.json();
            if (d.ok) {
                let extra = '';
                if (d.database_exists) extra = ` · ${d.table_count} existing tables`;
                showDbTest('ok', d.message + extra);
            } else {
                showDbTest('fail', d.message || 'Database connection failed.');
            }
        } catch(e) {
            showDbTest('fail', 'Network error: ' + (e && e.message ? e.message : 'Please try again.'));
        } finally {
            btn.disabled = false;
            btn.removeAttribute('aria-busy');
            btn.innerHTML = '<i class="bi bi-lightning-charge-fill" aria-hidden="true"></i> Test Connection';
        }
    });

    function showDbTest(type, msg) {
        const el = document.getElementById('db-test-result');
        el.className = 'test-result ' + type;
        el.innerHTML = '<i class="bi bi-' + (type === 'ok' ? 'check-circle-fill' : 'x-circle-fill') + '"></i><span></span>';
        el.querySelector('span').textContent = msg;
    }

    // ── Password strength indicator ──
    document.getElementById('admin_password').addEventListener('input', function() {
        const pw = this.value;
        const minLength = Number(minPasswordField.value) || 12;
        let score = 0;
        if (pw.length >= minLength) score++;
        if (pw.length >= minLength + 4) score++;
        if (/[A-Z]/.test(pw)) score++;
        if (/[0-9]/.test(pw)) score++;
        if (/[^A-Za-z0-9]/.test(pw)) score++;
        const pct = Math.min(100, score * 20);
        const colors = ['#ef4444','#f59e0b','#f59e0b','#10b981','#10b981','#10b981'];
        const bar = document.getElementById('pw-strength');
        bar.style.width = pct + '%';
        bar.style.background = colors[score] || '#e0e0e0';
        const hint = document.getElementById('pw-hint');
        const label = pw.length === 0 ? 'Minimum ' + minLength + ' characters' :
            (score < 2 ? 'Weak' : score < 4 ? 'Fair' : 'Strong');
        const icon = pw.length === 0 ? 'bi-shield-lock' :
            (score < 2 ? 'bi-shield-exclamation' : score < 4 ? 'bi-shield-plus' : 'bi-shield-check');
        const iconNode = document.createElement('i');
        iconNode.className = 'bi ' + icon;
        iconNode.setAttribute('aria-hidden', 'true');
        hint.replaceChildren(iconNode, document.createTextNode(' ' + label));
    });

    function renderInstallAlert(target, tone, iconName, heading, message, actionLabel, onAction) {
        const alert = document.createElement('div');
        alert.className = 'alert alert-' + tone;
        alert.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
        const icon = document.createElement('i');
        icon.className = 'bi ' + iconName;
        icon.setAttribute('aria-hidden', 'true');
        const content = document.createElement('div');
        const strong = document.createElement('strong');
        strong.textContent = heading;
        content.append(strong, document.createTextNode(' ' + String(message == null ? '' : message)));
        if (actionLabel && typeof onAction === 'function') {
            const action = document.createElement('button');
            action.type = 'button';
            action.className = 'btn btn-sm btn-outline-secondary mt-2';
            action.textContent = actionLabel;
            action.addEventListener('click', onAction);
            content.appendChild(action);
        }
        alert.append(icon, content);
        target.replaceChildren(alert);
    }

    // ── Step 5: Run installation ──
    async function runInstallation() {
        goTo(5);
        const installPanel = document.getElementById('step-5');
        installPanel.setAttribute('aria-busy', 'true');
        const container = document.getElementById('install-progress');
        const resultEl = document.getElementById('install-result');
        container.innerHTML = ''; resultEl.innerHTML = '';

        // These names are the exact phases returned by core.installer. Keep
        // the visual progress list honest: the service reports database/schema
        // work and administrator creation as aggregate steps, not substeps.
        const stepNames = [
            'Write configuration', 'Database & schema', 'Administrator',
            'Runtime directories', 'Finalize'
        ];
        const stepIcons = {
            'Write configuration': 'bi-file-earmark-code',
            'Database & schema':   'bi-database-gear',
            'Administrator':       'bi-person-badge',
            'Runtime directories': 'bi-folder2-open',
            'Finalize':            'bi-stars'
        };
        for (const name of stepNames) {
            container.innerHTML += '<div class="install-step pending" data-name="' + name + '" role="listitem">' +
                '<span class="isp-lead"><i class="bi ' + (stepIcons[name] || 'bi-gear') + '" aria-hidden="true"></i></span>' +
                '<span class="isp-name">' + name + '</span>' +
                '<span class="isp-icon"><i class="bi bi-arrow-repeat icon-spin" aria-hidden="true"></i></span></div>';
        }

        // Animate running state on first item
        const items = container.querySelectorAll('.install-step');
        items[0].className = 'install-step running';
        items[0].querySelector('.isp-icon').innerHTML = '<i class="bi bi-arrow-repeat icon-spin"></i>';

        try {
            const body = {
                db_host:   document.getElementById('db_host').value,
                db_port:   document.getElementById('db_port').value,
                db_user:   document.getElementById('db_user').value,
                db_password: document.getElementById('db_password').value,
                db_name:   document.getElementById('db_name').value,
                admin_username: document.getElementById('admin_username').value.trim(),
                admin_password: document.getElementById('admin_password').value,
                environment: document.getElementById('app_env').value,
                flask_port: document.getElementById('app_port').value,
                flask_host: document.getElementById('app_host').value,
                max_workers: document.getElementById('app_workers').value,
                file_processing_timeout: document.getElementById('app_timeout').value,
                log_level: document.getElementById('app_loglevel').value,
                ingestion_roots: document.getElementById('app_ingestion').value,
                max_failed_logins: document.getElementById('sec_max_failed').value,
                lockout_minutes: document.getElementById('sec_lockout').value,
                session_hours: document.getElementById('sec_session').value,
                session_idle_hours: document.getElementById('sec_idle').value,
                password_min_length: document.getElementById('sec_pw_min').value,
                rate_limit_per_minute: document.getElementById('sec_rate_min').value,
                rate_limit_per_hour: document.getElementById('sec_rate_hour').value,
            };

            const r = await fetch('/api/setup/install', {
                method: 'POST', headers: HEADERS,
                body: JSON.stringify(body),
            });
            const d = await r.json();

            // Animate results
            for (let i = 0; i < stepNames.length; i++) {
                const match = (d.steps || []).find(s => s.name === stepNames[i]);
                const el = items[i];
                if (match) {
                    el.className = 'install-step ' + (match.ok ? 'ok' : 'err');
                    el.querySelector('.isp-icon').innerHTML = match.ok
                        ? '<i class="bi bi-check-circle-fill"></i>'
                        : '<i class="bi bi-x-circle-fill"></i>';
                    if (match.detail) {
                        const chip = document.createElement('span');
                        chip.className = 'isp-detail';
                        chip.textContent = match.detail;
                        el.appendChild(chip);
                    }
                } else {
                    el.className = 'install-step pending';
                    el.querySelector('.isp-icon').innerHTML = '<i class="bi bi-skip-forward"></i>';
                }
                // Stagger animation
                await new Promise(resolve => setTimeout(resolve, 200));
            }
            installPanel.setAttribute('aria-busy', 'false');

            if (d.ok) {
                renderInstallAlert(
                    resultEl, 'success', 'bi-check-circle-fill',
                    'Installation complete!', 'Redirecting to login…', null, null);
                // Do not keep submitted secrets in the live DOM after install.
                document.getElementById('db_password').value = '';
                document.getElementById('admin_password').value = '';
                document.getElementById('admin_password2').value = '';

                setTimeout(() => {
                    // Use the same navigation routine as the rest of the
                    // wizard so the step count, progress rail and aria-current
                    // all agree that setup is complete.
                    goTo(TOTAL_STEPS + 1);
                    const summary = document.createElement('div');
                    summary.className = 'summary-card';
                    const addSummaryRow = (iconName, label, value) => {
                        const row = document.createElement('div');
                        row.className = 'summary-row';
                        const iconWrap = document.createElement('span');
                        iconWrap.className = 'sr-icon';
                        const icon = document.createElement('i');
                        icon.className = 'bi ' + iconName;
                        icon.setAttribute('aria-hidden', 'true');
                        iconWrap.appendChild(icon);
                        const copy = document.createElement('div');
                        const labelNode = document.createElement('span');
                        labelNode.className = 'sr-label';
                        labelNode.textContent = label;
                        const valueNode = document.createElement('span');
                        valueNode.className = 'sr-value';
                        valueNode.textContent = String(value == null ? '' : value);
                        copy.append(labelNode, valueNode);
                        row.append(iconWrap, copy);
                        summary.appendChild(row);
                    };
                    const configuredPort = Number(body.flask_port);
                    const visiblePort = Number(window.location.port || 0);
                    const address = !visiblePort || configuredPort === visiblePort
                        ? window.location.origin
                        : window.location.protocol + '//' + window.location.hostname + ':' + configuredPort;
                    addSummaryRow('bi-database-fill-check', 'Database', 'Created and migrated');
                    addSummaryRow('bi-person-badge', 'Admin', body.admin_username);
                    addSummaryRow('bi-globe2', 'Environment', body.environment);
                    addSummaryRow('bi-window-desktop', 'Address', address);

                    const loginLink = document.createElement('a');
                    loginLink.href = '/';
                    loginLink.className = 'cta';
                    const loginIcon = document.createElement('i');
                    loginIcon.className = 'bi bi-box-arrow-in-right';
                    loginIcon.setAttribute('aria-hidden', 'true');
                    loginLink.append(loginIcon, document.createTextNode(' Go to Login'));
                    const completion = document.getElementById('completion-summary');
                    completion.replaceChildren(summary, loginLink);
                    document.getElementById('redirect-note').style.display = '';
                    // Auto-redirect after 5s
                    setTimeout(() => { window.location.href = '/'; }, 5000);
                }, 1500);
            } else {
                renderInstallAlert(
                    resultEl, 'danger', 'bi-x-circle-fill', 'Installation failed.',
                    d.error || 'Check the step results above for details.',
                    INSTALL_WIZARD_PAGE_DATA.reviewSettings, () => goTo(4));
            }
        } catch(e) {
            installPanel.setAttribute('aria-busy', 'false');
            renderInstallAlert(
                resultEl, 'danger', 'bi-wifi-off', 'Network error:',
                e && e.message ? e.message : 'Please try again.',
                INSTALL_WIZARD_PAGE_DATA.reviewSettings, () => goTo(4));
        }
    }

    // ── Auto-run system check on page load ──
    runSystemCheck();

})();
