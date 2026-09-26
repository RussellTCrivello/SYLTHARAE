# Changelog

## v2.2.0 — 2026-09-26 — licence, strict CSP and production deployment

SYLTHARAE is now AGPL-3.0-or-later, runs under a strict Content Security
Policy, and ships a validated production deployment: nginx for TLS in front of
Waitress, a systemd unit, and start-up checks that refuse unsafe settings. The
release was verified end to end through HTTPS/nginx → Waitress → Flask →
PostgreSQL, before and after a restart ([docs/TESTING.md](docs/TESTING.md#live-smoke-test)).
Read [Upgrading from v2.1.1](#upgrading-from-v211) first.

### Deployment
- **Shipped configurations**: [`deploy/nginx/syltharae.conf`](deploy/nginx/syltharae.conf) (HTTP→HTTPS redirect, TLS 1.2/1.3, HTTP/2, overwritten `X-Forwarded-*` headers, 2 GB uploads, unbuffered event streams, no version disclosure) and [`deploy/systemd/syltharae.service`](deploy/systemd/syltharae.service) (sandboxed; `systemd-analyze security` rates it 3.2, "OK"). Tests run `nginx -t` on the config and check the unit ([docs/OPERATIONS.md](docs/OPERATIONS.md)).
- **Start-up checks** (`apps/web/deployment.py`): `run_web.py` refuses to start, with exit status 2 and a message saying what to change, when `FLASK_DEBUG` is on in production, `TRUSTED_PROXY_COUNT` is not a whole number (it used to be ignored silently), or `FLASK_PORT` is invalid. It warns about the development server in production, missing Waitress, a trusted proxy that clients can bypass, a missing TLS proxy and a short secret key. `verify_readiness.py` reports the same, and its `--json` output is now valid JSON.
- `run_web.py` no longer suggests Gunicorn or uWSGI: background jobs run in the server process, so one process serves one database.
- **nginx keeps HTTP/2 connections open for the session** (DEPLOY-H2-GOAWAY): at nginx's default of 1000 requests per connection, the browser lost the scripts it had just requested whenever nginx closed the connection, so roughly every 20th page loaded with controls that did nothing. The shipped site sets `keepalive_requests 100000` and `keepalive_time 1h`.
- The systemd unit lists every file the application writes in its install directory (DEPLOY-RW-01).

### Licence
- **SYLTHARAE is licensed under the GNU AGPL-3.0-or-later** ([LICENSE](LICENSE), [docs/LICENSING.md](docs/LICENSING.md)). The PDF reader's PyMuPDF dependency is AGPL. `pyproject.toml` declared `Proprietary`; it now declares `AGPL-3.0-or-later`, and wheels carry the licence and the notices. The build needs setuptools 77 or newer.
- Every page, including the sign-in page, links to the source code, as section 13 requires for network use. If you run a modified copy, set `SOURCE_CODE_URL` to your source ([CONFIGURATION](docs/CONFIGURATION.md)).
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists the vendored MIT libraries and the OFL fonts. The Inter and Noto Sans Arabic fonts shipped without the licence text the OFL requires; it is added.
- `tools/licenses/check_licenses.py` fails when a Python dependency's licence is incompatible with AGPL-3.0 or has not been reviewed. Every installed package is compatible (115 at release).

### Security
- **No inline script: the CSP is `script-src 'self'`** (RES-CSP-01). `'unsafe-inline'` is gone, so injected markup can no longer run script. The 599 inline handler attributes are now `data-on-<event>` attributes with the same expressions, run by `static/js/modules/core/declarative-events.js`, a restricted interpreter that never uses `eval`. The 17 inline `<script>` blocks are now static files that read server values from `application/json` blocks. The 403/404/500 pages no longer use `javascript:` links. See [docs/SECURITY.md](docs/SECURITY.md).
- **Changing your password requires the current password** (RES-AUTH-02). A signed-in session was enough before, so anyone at an unattended browser or holding a stolen session could take the account over. Wrong guesses count towards the sign-in lockout, and a lockout ends every session of the account. The endpoint is rate limited like sign-in, and a successful change signs out the account's other sessions. API clients must now send `current_password` along with `new_password`.
- **The audit log records the real client address** (AUDIT-PROXY-02). The sign-in, sign-out and password records took the address from the first `X-Forwarded-For` value, which any client can write. They now use the address `TRUSTED_PROXY_COUNT` establishes, like the rate limiter. The live smoke forges the header on every request and checks the log.
- **No cleartext administrator password in `.env`** (INSTALL-ENV-01). The setup wizard wrote `APP_ADMIN_PASSWORD` there, where it outlived every password change. It also left out `WSGI_SERVER` and `TRUSTED_PROXY_COUNT`, so a restart from `.env` alone fell back to the development server with no proxy trust. Both are now written.
- The administrator created by the setup wizard is audited with the wizard user's address; it was empty.
- A rate-limited sign-in now says "Too many attempts. Wait a minute and try again." instead of "An internal error occurred". The limiter's 429 page is replaced by JSON for JSON and API callers.
- **Ordinary browsing no longer hits the rate limit** (RATE-01). The reads every page makes on load - theme, interface strings, notification count - were refused under the default limits: the notification badge alone polls every 30 seconds per tab, so five open tabs used up the hourly budget. They now have the bounded interactive limit (600/minute) that the file and job pages already used.
- Static files are sent `Cache-Control: no-cache` (revalidated with ETag) instead of a contradictory `no-cache, max-age=31536000, immutable` (CACHE-02). Browsers already revalidated; the one-year part would have kept old JavaScript after an upgrade had it been honoured.

### Fixes
- The sidebar showed "Version 2.0.0" on every release: nothing supplied the version to templates. It now shows the running version, and so does the sign-in page.
- File page: the **Full screen** button did nothing, because its function was module-scoped. The metadata tab's **Save Changes** button saved nothing but announced "Metadata saved successfully!", and no route stores a file's name or notes. That button and the Notes box are removed, and File Name is shown read-only.
- **`/api/analysis/*` answered 500 on every request** (ANALYSIS-01): the routes called engine methods that never existed. `file`, `statistics` and `batch` now work; unknown files are 404, and engine errors no longer reach the client. `sentiment`, `topics` and `entities` were never implemented and now answer 501.
- PDF reading uses `import pymupdf`; the `fitz` name now prints a deprecation notice. The minimum is `PyMuPDF>=1.24.3`.
- **OCR with Tesseract works as designed** (OCR-TESS-01). When Tesseract was installed, images and scanned PDF pages bypassed the OCR engine layer: no confidence or word boxes were stored, turned pages were read as junk, small text was lost, a missing language pack silently became English, and a failure to read word data was reported as "no confidence". Now every image, scanned PDF page and image embedded in an Office document goes through the engine layer (`core/ocr`), with Tesseract preferred and RapidOCR as the fallback on hosts without it ([ARCHITECTURE.md §5.1](docs/ARCHITECTURE.md#51-ocr-engine-layer)):
  - each recognised word carries its confidence (0-1) and bounding box in the source image's pixels. The stored confidence is their mean, and `null` only with a recorded reason;
  - rotated and upside-down pages are read the right way up (each orientation is read and scored; a confident reading is checked against the page turned 180 degrees);
  - small text is enlarged and compared with the original; images up to 40,000 pixels always get this search, larger ones when their characters measure as text;
  - scanned PDF pages are rendered at the scan's own resolution (at least 144, at most 300 dpi, within a 16-megapixel budget) instead of a fixed 144 dpi, which discarded detail;
  - very large images are read without being refused or enlarged (enlargement stays within a 16-megapixel budget);
  - each requested language is also read alone, and a confidence that cannot be read is recorded with the reason (`confidence_error`) instead of silently as none;
  - a missing language pack is logged, and the file records it in `extraction_provenance.ocr.missing_languages` (OCR-LANG-02). It used to be in the log only.
  The stored OCR provenance is described in [DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md). The 17 OCR tests CI failed, and what fixed each, are in [AUDIT_REPORT.md](AUDIT_REPORT.md#v220-follow-up).
- Files list: removed the "quick preview" eye button. It called a placeholder that only logged to the console.

### Documentation and tooling
- **Continuous integration** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): every push and pull request runs the documented install, all test suites (with PostgreSQL, Node.js and nginx required, so nothing passes by skipping: `REQUIRE_POSTGRES=1`), the package build, ruff's correctness rules, bandit against [its baseline](tools/security/bandit-baseline.json), `pip-audit` and the licence check.
- `tools/smoke/live_smoke.py` runs through a real TLS reverse proxy when `SMOKE_BASE_URL` is `https://`, and checks more: cookie flags, security headers, uploads and downloads, duplicates, analysis, event streams, re-login after a password change, the audit log's client address, HTTP/2 and the TLS floor.
- The install instructions upgrade `setuptools` with `pip`: the copy a new virtual environment ships can carry known vulnerabilities.
- `tools/smoke/browser_smoke.mjs` signs in with a real Chrome/Chromium, crawls every page and fails on any CSP violation, page error, unreachable handler, same-origin 4xx/5xx response or request that never completed ([docs/TESTING.md](docs/TESTING.md#browser-smoke-test)).
- **OCR self-check** (`tools/ci/ocr_selfcheck.py`): reports each engine, its version and languages, and how it reads upright, turned and small sample text through the ingestion reader, with a remediation for each problem. CI runs it with `--require tesseract` before the tests; operators can run it after changing Tesseract. It classifies each sample as success, low confidence, wrong text, no text, invocation failed or unavailable ([docs/OPERATIONS.md](docs/OPERATIONS.md#ocr-self-check), installation in [docs/INSTALL.md](docs/INSTALL.md#ocr-engines-and-language-packs)).
- CI annotations list every skipped test with its reason, so a skip is never only a count.
- The live smoke test also checks OCR through the deployed stack, and its install phase honours `SMOKE_DB_PORT` and `SMOKE_DB_USER`.
- The generated component and Inspector counts dropped (for example, hand-written badge chips 70→68) because template-only scans no longer see code that moved into `static/js`. The code moved; it was not migrated. The action-surface audit now counts button-state code in the login and install-wizard scripts, which it could not see while that code was inline.

### Upgrading from v2.1.1
This release has no database migrations, but it is **not** a drop-in upgrade:
existing deployments must review and update their configuration, the nginx
site in particular.

- **Update your nginx configuration.** Compare it with `deploy/nginx/syltharae.conf`:
  - the proxy must *set* `X-Forwarded-For` to `$remote_addr`. Appending (`$proxy_add_x_forwarded_for`) passes on whatever the client sent;
  - with HTTP/2, add `keepalive_requests 100000;` and `keepalive_time 1h;` to the TLS server block, or pages intermittently load without their scripts (DEPLOY-H2-GOAWAY);
  - then run `nginx -t` and reload.
- **Remove `APP_ADMIN_PASSWORD` from `.env`** if the setup wizard wrote it. It is read only while no account exists.
- **Put the serving settings in `.env`** (`WSGI_SERVER=waitress`, `TRUSTED_PROXY_COUNT`) if you set them only in a shell, so that a restart keeps them.
- **Check that the server still starts.** A setting that used to be ignored now stops it (exit status 2), with a message that says what to change: for example a non-numeric `TRUSTED_PROXY_COUNT`.
- **systemd**: if you based a unit on v2.1.1's, compare it with `deploy/systemd/syltharae.service` (every path it writes is listed under `ReadWritePaths`).
- **OCR**: after upgrading, run `python tools/ci/ocr_selfcheck.py --require tesseract` if you use Tesseract. Files ingested earlier keep the text and provenance recorded at the time. They are not re-read: re-submitting identical files is recognised as a duplicate, and reprocessing is registered but not built (`files.reprocess`; see REPROCESS-01 in [AUDIT_REPORT.md](AUDIT_REPORT.md#residual-items-at-v220)).
- API clients: `POST /auth/change-password` requires `current_password`; `/api/analysis/batch` takes only `{"file_ids": [...]}`.
- Database migrations: none.

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
