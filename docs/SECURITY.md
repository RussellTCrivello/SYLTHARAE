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
* **Lockout**: `SECURITY_MAX_FAILED_LOGINS` (5) wrong passwords lock the
  account for `SECURITY_LOCKOUT_MINUTES` (15). Both sign-in and password
  changes count. The sign-in and change-password endpoints are each limited
  to 10 requests per minute per client, and answer `429` JSON
  (`code: rate_limited`) when the limit is hit. Every other endpoint has the
  default limits (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_HOUR`), except the
  read-only calls interactive pages repeat - the theme, interface strings and
  notification count each page loads, archive sections, reader chunks, job
  polling - which have a bounded 600 per minute (`INTERACTIVE_READ_LIMIT` in
  `core/security/rate_limit.py`), so ordinary browsing is not refused.
* **Changing your own password** (`POST /auth/change-password`) requires the
  current password, so an unattended browser or a stolen session cannot take
  the account over (RES-AUTH-02, fixed in 2.2.0):
  * A wrong current password counts towards the lockout. If it locks the
    account, every session of that account ends, including the one that
    tried.
  * A successful change signs out the account's other sessions and keeps the
    current one.
  * The new password must differ from the current one and meet
    `PASSWORD_MIN_LENGTH`.
  * `password.change`, `password.change_failed` and `password.change_locked`
    are written to the audit log.
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

**Saved searches are owned.** `/api/search/saved/<id>` (GET/PUT/DELETE)
answers `404` unless the caller owns the search, or is an administrator and
the search is *unowned* (legacy entries saved before per-user accounts). A
`404` rather than `403` avoids confirming that another user's id exists.
Previously any authenticated user could read, rename or delete any saved
search by id (`tests/integration/test_saved_searches_pg.py`).

**Signals, Horizon and Signal Explorer** (`/api/signals*`, `/signals`,
[implementation/HORIZON.md](implementation/HORIZON.md)) are readable by any
authenticated role.
* The caller's `AccessScope` is compiled into the SQL before any row is read.
  A signal is visible only through a visible file occurrence of its content.
* An absent signal and one the caller may not see get the same `404`. A
  saved search the caller does not own is also a `404`.
* Each response is read inside one `REPEATABLE READ, READ ONLY` transaction
  with a 15 s statement timeout, so PostgreSQL itself rejects writes on that
  path.
* Re-detection (`POST /api/signals/redetect`) is admin-only.

**Monitoring rules and notifications** (`/api/rules*`,
[implementation/RULE_ENGINE.md](implementation/RULE_ENGINE.md)):
* A notification with a `recipient_user_id` is visible only to that user,
  administrators included. The filter is part of the SQL of every read and of
  mark-read/dismiss; a notification addressed to someone else is a `404`.
  A rule notification without a recipient is refused (code and CHECK).
* Only an active analyst or admin may create or edit a rule, and only their
  own. Admins may pause, resume, archive, suppress or evaluate any rule, but
  not change its definition. Other users' rules are a `404`.
* Each evaluation re-reads the owner. An inactive or demoted owner's rule is
  disabled and notifies nothing; the criteria are compiled with the owner's
  `AccessScope`, stored with the evaluation.
* Priority is derived and cannot be supplied by a caller (400).
* Rule changes are written to `audit_log`.

**Scenarios** (`/api/scenarios*`, page `/monitoring`,
[implementation/SCENARIOS.md](implementation/SCENARIOS.md)) follow the same
model: owner-addressed notifications only; own definitions only (admins may
dry-run, activate, pause, resume, archive and evaluate any scenario, not edit
it - 403); others' scenarios are a `404`; the owner is re-read on every
evaluation and an ineligible owner's scenario is disabled before anything is
evaluated; population and document conditions are compiled under the owner's
`AccessScope`. Activation is refused (409) without a passed dry-run of the
current definition. Outcome history is append-only (trigger). Every change,
dry-run and activation is written to `audit_log`.

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
* **Query parameters of read APIs** are parsed strictly
  (`services/detection/signal_query.py`).
  * Unknown enum values, malformed identifiers and inverted date windows get
    `400`. They are never ignored, which would widen the result.
  * Identifiers must be positive ASCII decimal integers. `str.isdigit()` had
    accepted superscripts, whose `int()` error leaked into the response, and
    other scripts' digits.
  * Free-text filters are length-limited and matched literally: `%` and `_`
    are escaped for `ILIKE`.
  * Every list is bounded (100 values; pages of at most 200 rows).
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
  Pages and `/api/*` responses are `no-store`; static files are `no-cache`
  (revalidated by ETag).
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
    Injected CSS cannot run script, so this is an accepted residual
    (RES-CSP-02 in [AUDIT_REPORT.md](../AUDIT_REPORT.md#residual-items-at-v220)).
* The original-file viewer opts into `X-Frame-Options: SAMEORIGIN` for the
  one response that must be framed by the application itself.

## 6. Secrets and configuration

* `FLASK_SECRET_KEY` signs sessions and CSRF tokens. If unset, a random key is
  generated into `.flask_secret_key` (git-ignored) on first start.
* Database credentials live in `.env` (git-ignored) or the environment. The
  settings API never returns the database password.
* `verify_readiness.py` scans for committed credentials and refuses debug
  mode in production. `run_web.py` refuses to start (exit status 2) with
  `FLASK_DEBUG=true` in production, a non-numeric `TRUSTED_PROXY_COUNT` or an
  invalid port, and warns about unsafe production settings
  ([OPERATIONS.md](OPERATIONS.md#start-up-checks)).
* The setup wizard's `.env` (mode 0600) holds the database password and the
  secret key but not the administrator's password (INSTALL-ENV-01): the
  account is created in the database, and `APP_ADMIN_PASSWORD` is read only
  while no account exists.
* Errors returned to clients are generic (`client_error`, SEC-08); details
  are logged server-side only.

## 7. Audit trail

Security-relevant actions are written to `audit_log` (user, action,
resource, detail, client address, time): `login.success`, `login.failed`,
`login.locked`, `logout`, `password.change`, `user.create`, `user.update`,
`user.delete`, `user.password_reset`, `user.password_recovery`,
`user.first_admin_created`, `bootstrap.initial_admin_created`,
`setup.initial_admin_created`, `jobs.cancel`, `password.change_failed`,
`password.change_locked`, `DATA_EXPORTED`. Behind a reverse proxy set `TRUSTED_PROXY_COUNT` so
the recorded address is the client's, not the proxy's (AUDIT-PROXY-01).

The address is always the one `TRUSTED_PROXY_COUNT` establishes
(`request.remote_addr`), the same one rate limiting uses. The sign-in,
sign-out and password records used to take the first `X-Forwarded-For` value
themselves, which any client can write (AUDIT-PROXY-02). The live smoke test
sends a forged `X-Forwarded-For` on every request and checks that it never
reaches the log.

### Disclosure register (`DATA_EXPORTED`)

Every response that hands the client a file (`Content-Disposition:
attachment`) - search exports, bulk file exports, keyword and category
exports, backups, original-file downloads - writes one `DATA_EXPORTED`
record: actor and role, time, endpoint, filename, format, and the SHA-256
and size of the bytes actually sent. Routes that know more add it (search
exports add scope, row count, total, truncation, the criteria fingerprint
and the full request fingerprint). The response carries
`X-Disclosure-Audit-Id`.

Coverage is enforced centrally (`core/security/disclosure.py`, an
`after_request` hook), so a new export route cannot be added unrecorded. The
hook **fails closed**: if the record cannot be written the export is replaced
by `503 disclosure_audit_failed` and nothing is sent. The digest is computed
before gzip compression. Bodies larger than 64 MiB that are streamed from
disk are recorded with `"sha256": null` and the reason, never a guessed
value. Inline previews (`as_attachment=False`) are not disclosures and are
not recorded. Tests: `tests/integration/test_disclosure_audit.py`.

Every record states `kind`, `scope`, `format` and `row_count`. Each export
route declares what it knows; a count a route cannot know is `null` with
`row_count_reason` (never `0`), and a route that declares nothing still gets
`format` from the file and `row_count: null` with the reason "not declared by
the route".

| Route | kind | scope | row_count (unit) |
| --- | --- | --- | --- |
| `POST /api/search/export` | `search_export` | criteria scope | rows (+ total, truncated, fingerprints) |
| `GET /api/analyst/export` | `analyst_categorizations` | `all` / `filtered` (+ filters) | assignments |
| `GET /api/sources/<id>/export` | `source_record` | `source:<id>` | 1 (source) |
| `GET /api/keywords/export` | `keywords_export` | `all` / `selection` | keywords |
| `POST /files/export` | `bulk_text_export` / `bulk_originals_export` | `selection` (+ file ids) | files included (+ requested, skipped) |
| `POST /api/files/names/export` | `file_names_export` | `all` / `selected` / `type:<t>` | files |
| `POST /api/files/excerpt/export` | `excerpt_<kind>` | `file:<id>` | 1 (excerpt) |
| `POST /api/files/extract-contacts/export` | `contacts_export` | `selection` (+ file ids) | occurrences |
| `POST /api/files/first-pages/export` | `first_pages_export` | `selection` (+ file ids) | files |
| `GET /api/files/<id>/export` | `file_text_export` | `file:<id>` | 1 (file) |
| `GET /api/file/<id>/original/content?download=1` | `original_file` | `file:<id>` | 1 (file) |
| `GET /api/import-export/backup/export` | `database_backup` | `tables:<list or evidence>` | `null` - the backup service does not count rows |
| `GET /api/import-export/settings/export` | `settings_export` | `settings:search,display,system` | 1 |
| `GET /api/settings/export` | `settings_export` | `settings:all` | 1 - JSON body, recorded explicitly (`force`) because it has no attachment header |

`test_every_export_route_writes_a_complete_record` calls each of these
against PostgreSQL and checks the record, the actor and the SHA-256 of the
bytes received.

## 8. Dependencies

`requirements.txt` pins minimum versions that include known security fixes
(for example `pypdf>=6.0` instead of the unmaintained PyPDF2, and
`Pillow>=12.3`). Check regularly:

```bash
pip install pip-audit && pip-audit -r requirements.txt
```

CI runs `pip-audit` on every push, and bandit against
[`tools/security/bandit-baseline.json`](../tools/security/bandit-baseline.json):
the baseline holds only the documented residuals RES-SQL-01 (B608),
RES-XML-01 (B314) and RES-BIND-01 (B104)
([AUDIT_REPORT.md](../AUDIT_REPORT.md#residual-items-at-v220)). A medium- or
high-severity finding fails the build when its file has no baseline entry for
that test.

**Limit of the baseline gate (verified 2026-09-28).** bandit matches baseline
entries by file, test id, severity, confidence and message, not by line or
count. A file that already has one B608 entry can gain further B608 sites
without failing CI: a scratch file with one baselined B608 site and two added
ones passes `bandit -b` with exit 0. New string-built SQL in the baselined files
(for example `Api/utils/utils.py`, `Api/services/search_service.py`,
`core/monitoring/notification_service.py`) must therefore be reviewed by hand,
as below. A count-aware gate is a follow-up, not built.

### Bandit triage of the intelligence layer (2026-09-28)

The CI bandit step failed on the intelligence-layer branch (PR #3) with 109
findings: 108 B608 and 1 B108. Main (`b6d80fb`) was already failing with 17
findings. They are 16 sites in `Api/routes/file_analysis.py` and 1 in
`Api/services/search_export.py` that are missing from the baseline. The
baseline was **not** regenerated. Every site was read and handled one by one:

*Defects fixed*

| Finding | Fix | Test |
| --- | --- | --- |
| `SearchService` defined `_query_match_patterns`, `_matching_fields`, `_find_matching_lines`, `_build_order_by`, `simple_search` and `advanced_search` twice. This was a broken splice in `a6060b5`: the first `advanced_search` was the start of the Phase 0 edit joined to the tail of `full_text_search`. Python keeps the last definition, so the first copies were ~850 lines of dead code. The live copies are the ones every test and field run executed | dead block removed (behaviour unchanged); the analyst-category filter now also uses the shared `filter_predicates` (identical SQL and parameters) | `test_no_method_is_defined_twice`, `test_search_uses_the_shared_analyst_category_predicate` |
| Nine validators added by the branch were anchored with `$`, which in Python also matches before a trailing newline: `"abc\n"` passed `scenario_model._ID_RE`, whose value later reaches SQL composition (not exploitable: `_lit()` re-checks, and a newline is only whitespace in an identifier) | `\Z` in `kinds._ID`, `criteria.model._ISO_DATE`, `reporting.model._ID`/`_HELP_TOPIC`, `audit_query._ID`, `signal_query._METHOD_RE`/`_PLACE_KEY_RE`/`_POSITIVE_INT_RE`, `scenario_model._ID_RE` | `test_anchored_validators_match_the_whole_value` (9), `test_scenario_identifiers_reject_a_trailing_newline` |
| `NotificationService._set_flag` writes `column` into `UPDATE alerts SET {column}` and guarded it with `assert`, which `python -O` removes | explicit check raising `ValueError` | `test_set_flag_rejects_an_unknown_column_without_relying_on_assert` |
| The horizon wrote the reference date into the SQL text (`DATE '<iso>'`). This was the one value in the branch's SQL that was not a parameter (safe in practice: only a `date` has `isoformat()`) | the bucket expression reads `ref.r` from `CROSS JOIN (SELECT %s::date AS r) ref`; the date is bound | `test_horizon_binds_the_reference_date`; `test_signal_explorer_pg.py` horizon tests unchanged |
| `_fetch_notifications` interpolated `LIMIT {int(limit)}` | bound parameter | `test_notification_service.py` |
| B108: `tools/verify/runtime_provision.py` defaulted to the fixed path `/tmp/syltharae_runtime` in the shared temp directory | a fresh private `tempfile.mkdtemp()` when no directory is given | - (developer tool; the documented commands pass a directory) |

*Reviewed and justified inline.* The other 110 sites compose only module
constants (column lists, `CANONICAL_FROM`), fixed fragments with `%s`
placeholders, whitelisted identifiers (sort maps, facet tuples, validated
scenario identifiers) and the shared criteria compiler's output. Every value is
a bound parameter. Each one carries `# nosec B608 # <reason specific to the
file>`. The reason follows a second `#` because bandit reads every word after
`nosec` as a test id: the `- reason` form produced about 1 700 "Test in
comment" warnings, while this form keeps the id enforced (a wrong id does not
suppress) and adds none. `bandit --ignore-nosec` confirms that every one of the
110 comments lies inside a real finding's line range, so none is superfluous.
This includes the 17 sites that were already failing on main (reviewed: constant
fragments, `%s` lists and column literals passed by the routes; request values
are parameters) and 9 sites in the baselined `notification_service.py`.

Result: `bandit -q -c pyproject.toml -r . -ll -b tools/security/bandit-baseline.json`
exits 0 (0 findings). The whole CI static job was re-run locally with the CI
install (`pip install -e ".[pdf,office,ocr,ebook,audio,media,server,dev]"`,
Python 3.11): ruff E9/F63/F7/F82 passes, pip-audit reports "No known
vulnerabilities found", and the licence check reports 116 compatible, 0
incompatible, 0 unknown.

Front-end libraries are vendored under `static/` (no CDN), so an offline
installation loads nothing from the internet.

## 9. Deployment checklist

- [ ] HTTPS in front of the app (`deploy/nginx/syltharae.conf`), `TRUSTED_PROXY_COUNT` set to the number of proxies, `FLASK_HOST=127.0.0.1`
- [ ] [Deployment verification](OPERATIONS.md#deployment-verification) done: redirect, sign-in, HSTS, client addresses in the audit log
- [ ] `FLASK_ENV=production`, `FLASK_DEBUG=false`, long random `FLASK_SECRET_KEY`
- [ ] Initial admin password changed; `initial_admin_password.txt` gone
- [ ] Every person has their own account with the narrowest role
- [ ] `INGESTION_ROOTS` limited to the evidence folders
- [ ] Database role limited to its own database; PostgreSQL not exposed publicly
- [ ] One server process per database (the rate-limit counters live in it)
- [ ] Backups of PostgreSQL and `APP_DATA_DIR`, and a tested restore
- [ ] `python verify_readiness.py` passes

## 10. Security tests

`tests/security/` covers authentication and authorisation per role, CSRF,
rate limiting, import/export gating, interface visibility, settings error
hygiene, job security and the audit remediations
(`test_audit_remediation.py`), the password change (`test_change_password.py`)
and the reverse-proxy contract (`test_proxy_deployment.py`: sign-in through a
proxy, the 400 when a proxy drops the host name, the audit address, HSTS
conditions, forwarded headers ignored without a trusted proxy).
`tests/unit/test_audit_remediation_static.py` holds the static front-end
checks (escaping, CSRF headers). `tools/smoke/live_smoke.py` and
`browser_smoke.mjs` check the same through a real nginx, over HTTPS
([TESTING.md](TESTING.md)).
