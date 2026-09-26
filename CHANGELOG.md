# Changelog

## Unreleased

### Security
- **No inline script: the CSP is `script-src 'self'`** (RES-CSP-01). `'unsafe-inline'` is gone, so injected markup can no longer run script. The 599 inline handler attributes are now `data-on-<event>` attributes with the same expressions, run by `static/js/modules/core/declarative-events.js`, a restricted interpreter that never uses `eval`. The 17 inline `<script>` blocks are now static files that read server values from `application/json` blocks. The 403/404/500 pages no longer use `javascript:` links. See [docs/SECURITY.md](docs/SECURITY.md).
- A rate-limited sign-in now says "Too many attempts. Wait a minute and try again." instead of "An internal error occurred". The limiter's 429 page is replaced by JSON for JSON and API callers.

### Fixes
- File page: the **Full screen** button did nothing, because its function was module-scoped. The metadata tab's **Save Changes** button saved nothing but announced "Metadata saved successfully!", and no route stores a file's name or notes. That button and the Notes box are removed, and File Name is shown read-only.
- Files list: removed the "quick preview" eye button. It called a placeholder that only logged to the console.

### Documentation and tooling
- `tools/smoke/browser_smoke.mjs` signs in with a real Chrome/Chromium, crawls every page and fails on any CSP violation, page error or unreachable handler ([docs/TESTING.md](docs/TESTING.md#browser-smoke-test)).
- The generated component and Inspector counts dropped (for example, hand-written badge chips 70→68) because template-only scans no longer see code that moved into `static/js`. The code moved; it was not migrated. The action-surface audit now counts button-state code in the login and install-wizard scripts, which it could not see while that code was inline.

## v2.1.1 — 2026-09-25 — security and production-readiness patch

A full audit of v2.1.0; [AUDIT_REPORT.md](AUDIT_REPORT.md) has the details and
the test evidence. Every fix ships with a regression test that fails on
v2.1.0. Defaults are unchanged and nothing needs migrating. The only new
capability, the Waitress server, is opt-in.

### Security
- **Setup probes locked after install** (AUDIT-SETUP-01): `/api/setup/system-check` and `test-database` were still anonymous after installation. They now return 401 for anonymous users and 403 for non-admins.
- **Temporary passwords are enforced** (AUDIT-AUTH-01): an account with an admin-issued temporary password can only reach the change-password flow until it has changed it.
- **SQL injection** fixed in `/api/archives/geolocation` sorting (AUDIT-SQLI-01).
- **Stored XSS** fixed on the dashboard and path-analysis pages, which rendered file names and words unescaped (AUDIT-XSS-01).
- **HSTS is actually sent** in production over HTTPS (AUDIT-HSTS-01). It is never sent on plain HTTP.
- **7z bombs** are rejected from the declared sizes, before anything is written (AUDIT-ARCH-01).
- **Real client IPs behind a proxy**: set `TRUSTED_PROXY_COUNT` (AUDIT-PROXY-01). Rate limits, lockouts and the audit log previously saw only the proxy.
- **Dependencies**: `PyPDF2` replaced by `pypdf>=6`, `Pillow>=12.3` (AUDIT-DEP-01). `pip-audit` finds no known vulnerabilities in `requirements.txt`.
- Forensic MD5/SHA-1 digests are marked `usedforsecurity=False`, so they work on FIPS-mode Python.

### Operations
- **Production WSGI server**: `pip install -e ".[server]"` and set `WSGI_SERVER=waitress` (with `WAITRESS_THREADS`, default 32) (AUDIT-OPS-01). Production must not run on the built-in server; see [docs/OPERATIONS.md](docs/OPERATIONS.md).
- **`/health` is healthy right after installation** (AUDIT-HEALTH-01). It used to report `degraded` until the first restart.
- **Configuration is honoured** (AUDIT-CONF-01): `LOG_LEVEL` now takes effect, and `DB_POOL_MIN/MAX_CONNECTIONS` are accepted. Variables that nothing reads are labelled *reserved* in `.env.example`.
- `libpff-python` (PST) is now the optional `pst` extra, so `pip install -r requirements.txt` works on a clean machine.

### Fixes
- `ContentDBService.hash_exists(side_id=…)` raised `AttributeError` on every call (AUDIT-DB-01).
- `/api/paths` errors returned 500 because of an undefined logger (AUDIT-PY-01).
- Duplicate dict keys in the search algorithms dropped entries (AUDIT-PY-02).
- `date.today()` defaults no longer freeze at import time (AUDIT-DATE-01).
- Four actions failed with 400 because their requests omitted the CSRF token (AUDIT-CSRF-01).
- Removed an unreachable duplicate error handler in the storage pipeline.

### Documentation and tooling
- New guides: [README](README.md), [OPERATIONS](docs/OPERATIONS.md), [CONFIGURATION](docs/CONFIGURATION.md), [SECURITY](docs/SECURITY.md), [ARCHITECTURE](docs/ARCHITECTURE.md) (Mermaid diagrams), [DOMAIN_MODEL](docs/DOMAIN_MODEL.md), [DATABASE](docs/DATABASE.md), [INSTALL](docs/INSTALL.md), [DEVELOPMENT](docs/DEVELOPMENT.md), [TESTING](docs/TESTING.md), [PRODUCTIZATION_MAP](docs/PRODUCTIZATION_MAP.md) and a [docs index](docs/README.md).
- Generated references: modules and HTTP routes (`tools/docs/generate_reference.py`) and the database schema (`tools/docs/generate_schema.py`). Tests fail when they go stale.
- `tools/smoke/live_smoke.py`: an end-to-end smoke test against a running production-configured server.
- New tests check every documentation link and anchor, the job state diagram against the code, and version consistency.
- The schema-reference test no longer depends on test order.

### Upgrading from v2.1.0
1. Back up the database ([OPERATIONS.md §4](docs/OPERATIONS.md#4-backups)).
2. `git pull` (or unpack the release), then `pip install -r requirements.txt`. For production, also run `pip install -e ".[server]"`.
3. Optional, recommended in production: add `WSGI_SERVER=waitress` and, behind a proxy, `TRUSTED_PROXY_COUNT=1` to `.env`.
4. Restart. There are no new migrations.

Users who still hold a temporary password will be asked to change it at their next sign-in.

## v2.1.0 — 2026-09-25 — first production release

First operational release of SYLTHARAE.

### Notifications
- The notification service reads and writes in a thread-safe way: refresh swaps in the new data in one step (under an `RLock`), so concurrent refreshes can no longer double the rows held in memory.
- Batch flush uses a multi-row `INSERT` and gives rows with the same timestamp distinct `created_at` values. If a batch fails, its rows go back on the queue instead of being lost.
- `mark_as_read` / `dismiss` confirm the change with `RETURNING id`.
- `get_stats()` and `get_upcoming_events()` are computed in SQL, plus the rows still waiting to be written. Counts match the `alerts` table exactly.
- Titles and messages are formatted in one shared module (`core/monitoring/notification_display.py`).
- Notifications page: filtering, search, sorting (whitelisted columns), pagination and summary counts all run in SQL. `per_page` is capped at 1000. Source and side are resolved through `paths → hash_contexts`.
- Scan endpoint: responses carry real database ids (the response is built after the flush). A dismissed duplicate stays dismissed, and each hash gets at most one notification.
- Migration `m0014_alerts_notification_indexes` adds the matching `alerts` indexes.

### Readers and detection
- New `DiagramFileReader` for draw.io / diagrams.net files (`.drawio`, `.dio`), in both plain mxfile XML and ZIP (`file.xml` + `metadata.xml`) form.
- The format catalogue has a `diagram` family. A ZIP-form `.drawio` goes to the diagram reader, not the archive reader.

### Pipeline, keywords and logging
- Keyword-path relationship inserts go through `KeywordOperations.insert_keyword_path_relationships`, which reports how many rows it wrote.
- The storage pipeline retries only connection and retryable errors; other errors fail immediately.
- Progress and summaries are logged instead of printed one by one. stdout is line-buffered for the CLI and the web app, and werkzeug is set to WARNING.

### Frontend
- The notifications page shows exact summary counts. The saved-searches prompt is properly awaited.
