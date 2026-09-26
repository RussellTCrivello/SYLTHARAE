# Security

SYLTHARAE holds evidence: documents, mailboxes and the words inside them.
The design goal is that a person sees and changes only what their role
allows, that nothing uploaded can escape the processing sandbox, and that
the server never reveals more about itself than it must.

To report a vulnerability, open a private security advisory on the GitHub
repository rather than a public issue.

## 1. Threat model

| Actor | Assumed capability | Main controls |
| --- | --- | --- |
| Anonymous network user | Can reach the port | Default-deny authentication; only the allow-list below is public; rate limits; the setup wizard closes after installation. |
| Authenticated viewer | Valid session | Read-only; admin-only reads (settings, diagnostics) refused. |
| Authenticated analyst | Valid session | May ingest and edit data, not configure the system or manage users. |
| Malicious file | Arrives by upload or ingestion | Archive limits and path validation, content-based type detection, parsers run on copies, no execution of content, output escaped in the UI. |
| Cross-site attacker | Lures a signed-in user | CSRF tokens on every mutation, `SameSite=Lax`, CSP, `frame-ancestors 'none'`. |

Out of scope: an attacker with shell access to the server or the database,
and a compromised administrator account.

## 2. Authentication

* **Passwords** are hashed with Werkzeug **scrypt** (memory-hard, salted)
  (`core/security/passwords.py`). Minimum length `PASSWORD_MIN_LENGTH`
  (default 12, allowed 8-128).
* **Sessions** are opaque: a 256-bit random token is stored in the signed
  Flask session cookie, and only its **SHA-256** is stored in `sessions`. A
  session expires after `SECURITY_SESSION_HOURS` (12) or
  `SECURITY_SESSION_IDLE_HOURS` (6) of inactivity, and can be revoked
  (sign-out, password change, admin action, recovery script).
* **Cookies** are `HttpOnly`, `SameSite=Lax`, and `Secure` when
  `FLASK_ENV=production` - which means production must be served over HTTPS
  ([INSTALL.md Step 10](INSTALL.md#step-10---production-hardening)).
* **Lockout**: `SECURITY_MAX_FAILED_LOGINS` (5) failures lock the account for
  `SECURITY_LOCKOUT_MINUTES` (15). The login endpoint is limited to 10
  requests per minute per client.
* **Temporary passwords** (the generated first-run password, accounts created
  or reset by an administrator, recovery passwords) set
  `must_change_password`. Until it is changed the account can reach only the
  change-password flow, `/auth/me`, sign-out and the home page; every API
  answers `403 password_change_required` (AUDIT-AUTH-01).
* **First administrator**: see [INSTALL.md Step 7](INSTALL.md#step-7---the-initial-administrator).
  `/auth/first-admin` works only while the `users` table is empty.
* **Recovery**: `scripts/reset_admin_password.py` (local shell access
  required; audited) - [INSTALL.md E11](INSTALL.md#troubleshooting).

## 3. Authorisation

Enforced server-side for every request by `core/security/flask_ext.py`:

| Request | Allowed |
| --- | --- |
| Endpoint in `PUBLIC_ENDPOINTS` (login page and action, logout, `/auth/me`, first-admin while no users exist, `/api/csrf-token`, favicon, `/health`, the i18n catalogue, the setup wizard) | everyone |
| `GET`/`HEAD` on anything else | any authenticated user |
| `GET` on settings, concurrency and error-dashboard blueprints, or the settings page | admin (except `/api/settings/theme`) |
| `POST`/`PUT`/`PATCH`/`DELETE` | analyst or admin |
| Mutations on admin blueprints (`settings`, `settings_api`, `setup`, `concurrency`, `error_dashboard`, `translations`, `import_export`) | admin (the language switch and `/auth/change-password` are open to every user because they only affect the caller) |

View-level decorators (`@roles_required`, `@admin_required`) can tighten
this and never loosen it. Unauthenticated API calls get `401` JSON; pages
redirect to the login page. The effective policy of every route is listed
in [reference/HTTP_ROUTES.md](reference/HTTP_ROUTES.md).

Backup export and restore (`/api/import-export/*`) contain or replace every
evidence table and are therefore admin-only (SEC-02).

The **setup wizard** is public only until installation: after that `/setup`
redirects home, `/api/setup/install` answers 409, and the system check and
database probe require an administrator (AUDIT-SETUP-01).

Hiding a navigation entry from a role is a convenience, never the control:
the Interface Registry's `required_role` and the route policy are enforced
separately, and `tests/security/test_interface_visibility.py` checks both.

## 4. Input handling

* **SQL.** All values are bound parameters (psycopg2). Identifiers that must
  vary (sort columns, directions, table names) are chosen from whitelists
  (`core/sql_safety.py`); a client value never reaches the SQL text
  (AUDIT-SQLI-01 fixed the one place that did).
* **Server paths.** Ingestion of server-side folders is confined to the
  `INGESTION_ROOTS` allow-list (`core/path_safety.py`, SEC-06), resolved after
  symlinks, with native Windows drive and UNC paths handled. With no roots
  configured, server-path ingestion is **off**; files uploaded through the
  application (staged under `APP_DATA_DIR/uploads`) remain ingestible.
* **Uploads.** Size-limited (`OPERATIONS_MAX_UPLOAD_MB`, default 2048;
  chunked uploads up to `OPERATIONS_MAX_CHUNKED_UPLOAD_MB`), staged under the
  data directory with generated names.
* **Archives** (`core/archive_safety.py`). Every member name is validated
  (no absolute paths, `..`, drive letters, Windows device names or links
  pointing outside); extraction enforces depth (8), member count (10,000),
  total size (2 GiB), per-member size (512 MiB), compression ratio (500:1)
  and a time limit. For 7z, the declared sizes are checked **before** any
  byte is written (AUDIT-ARCH-01). Each extraction goes to a fresh empty
  directory.
* **File contents** are identified by their bytes (`core/formats/`), never
  executed, and parsed by libraries in-process. XML inside Office
  packages, e-books, diagrams and image metadata is parsed with the standard
  library's `xml.etree.ElementTree`, which does not fetch external entities;
  its expat (2.4.1 or newer, as bundled with current Python releases) limits
  entity expansion.

## 5. Browser protections

* **CSRF**: Flask-WTF on every POST/PUT/PATCH/DELETE. Pages read the token
  from `<meta name="csrf-token">` via `window.CSRF` and send `X-CSRFToken`.
  A test fails when a page's mutating `fetch` omits it (AUDIT-CSRF-01).
* **XSS**: Jinja2 autoescaping on the server; in JavaScript, data goes into
  the DOM through `textContent` or `escapeHtml`/`escapeAttribute`
  (`static/js/modules/core/utils.js`). File names, words, paths and every
  other database value are attacker-controlled - they come from ingested
  files (AUDIT-XSS-01).
* **Headers** on every response: `Content-Security-Policy` (below),
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, a restrictive
  `Permissions-Policy`, `Cross-Origin-Opener-Policy: same-origin`, and
  `Strict-Transport-Security` in production over HTTPS (AUDIT-HSTS-01).
  Pages and `/api/*` responses are `no-store`.
* **Content-Security-Policy.** Every page is served with:

  `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'`

  `script-src 'self'` means the browser runs no inline script, no `eval`
  and no `javascript:` URL, so markup injected despite the escaping above
  cannot execute (RES-CSP-01, resolved in 2.2.0):
  * Event handlers are `data-on-<event>="expression"` attributes (for
    example `data-on-click="applyFilters()"`), run by
    `static/js/modules/core/declarative-events.js`. It parses the expression
    with a small interpreter - calls, member access, literals, `this`,
    `event`, `return false` and `.style` assignment - and never uses `eval`
    or `Function`. Because it still lets markup call page functions, it
    refuses `eval`, `Function`, string timers, `fetch`, `constructor`,
    `__proto__`, HTML-writing sinks and similar names, both by name and by
    identity. Output escaping is still the primary control.
  * Page scripts are static files; server values reach them through
    `<script type="application/json" id="…-page-data">` blocks, which the
    browser does not execute.
  * `tests/unit/test_csp_no_inline_script.py` fails on any inline handler,
    inline `<script>`, `javascript:` URL or string evaluation in a template,
    a script or a served page. `tools/smoke/browser_smoke.mjs` loads every
    page in a real browser under this policy
    ([TESTING.md](TESTING.md#browser-smoke-test)).
  * `style-src` still allows `'unsafe-inline'`: templates use `style`
    attributes, and Settings applies administrator-authored custom CSS.
    Injected CSS cannot run script, so this is an accepted residual.
* The original-file viewer opts into `X-Frame-Options: SAMEORIGIN` for the
  one response that must be framed by the application itself.

## 6. Secrets and configuration

* `FLASK_SECRET_KEY` signs sessions and CSRF tokens. If unset, a random key is
  generated into `.flask_secret_key` (git-ignored) on first start.
* Database credentials live in `.env` (git-ignored) or the environment. The
  settings API never returns the database password.
* `verify_readiness.py` scans for committed credentials and refuses debug
  mode in production; `run_web.py` refuses `FLASK_DEBUG=true` with
  `FLASK_ENV=production`.
* Errors returned to clients are generic (`client_error`, SEC-08); details
  are logged server-side only.

## 7. Audit trail

Security-relevant actions are written to `audit_log` (user, action,
resource, detail, client address, time): `login.success`, `login.failed`,
`login.locked`, `logout`, `password.change`, `user.create`, `user.update`,
`user.delete`, `user.password_reset`, `user.password_recovery`,
`user.first_admin_created`, `bootstrap.initial_admin_created`,
`setup.initial_admin_created`, `jobs.cancel`. Behind a reverse proxy set
`TRUSTED_PROXY_COUNT` so the recorded address is the client's, not the
proxy's (AUDIT-PROXY-01).

## 8. Dependencies

`requirements.txt` pins minimum versions that include known security fixes
(for example `pypdf>=6.0` instead of the unmaintained PyPDF2, and
`Pillow>=12.3`). Check regularly:

```bash
pip install pip-audit && pip-audit -r requirements.txt
```

Front-end libraries are vendored under `static/` (no CDN), so an offline
installation loads nothing from the internet.

## 9. Deployment checklist

- [ ] HTTPS in front of the app, `TRUSTED_PROXY_COUNT` set to the number of proxies
- [ ] `FLASK_ENV=production`, `FLASK_DEBUG=false`, long random `FLASK_SECRET_KEY`
- [ ] Initial admin password changed; `initial_admin_password.txt` gone
- [ ] Every person has their own account with the narrowest role
- [ ] `INGESTION_ROOTS` limited to the evidence folders
- [ ] Database role limited to its own database; PostgreSQL not exposed publicly
- [ ] `RATELIMIT_STORAGE_URI` set when running more than one process
- [ ] Backups of PostgreSQL and `APP_DATA_DIR`, and a tested restore
- [ ] `python verify_readiness.py` passes

## 10. Security tests

`tests/security/` covers authentication and authorisation per role, CSRF,
rate limiting, import/export gating, interface visibility, settings error
hygiene, job security and the audit remediations
(`test_audit_remediation.py`). `tests/unit/test_audit_remediation_static.py`
holds the static front-end checks (escaping, CSRF headers).
