# SYLTHARAE audit report: v2.1.0 to v2.1.1, and the v2.2.0 follow-up

The sections up to "Manual actions that remained at v2.1.1" are the v2.1.1
audit, kept as the baseline. What v2.2.0 changed, verified and left open is in
[v2.2.0 follow-up](#v220-follow-up).

**Scope:** the whole repository at tag `v2.1.0` (commit `13b7ace`). That covers
the backend (Flask app, routes, services, ingestion engine, readers, jobs),
the front end (templates and ES modules), the database (migrations
m0001–m0014, constraints, identity model), security, dependencies and
supply chain, installation, configuration and operations.

**Result:** 18 findings were fixed on branch `arena/01a0daf1-syltharae`,
each with a regression test that fails on the old code. The residual items
are listed in their own section with a severity and a justification. The
live production-stack smoke test passes. Details are below.

## Method

| Activity | Tooling / evidence |
|---|---|
| Static analysis | `ruff` (E, F, W, B), `bandit`, `radon`, `vulture` over all first-party code |
| Dependency audit | `pip-audit -r requirements.txt`, plus a **clean-venv install** of `requirements.txt` followed by `pip check` |
| Manual review | Authentication and authorisation (`core/security/`), every route module for input handling, SQL construction, archive and path safety, the ingestion pipeline and identity model, the job state machine, configuration loading, and front-end rendering (`innerHTML`, CSRF on `fetch`) |
| Tests | the unit, integration, security and e2e suites against a disposable PostgreSQL 16.2 (`pgserver`); **v2.1.0 re-run in the identical environment** as the baseline |
| Live smoke | `tools/smoke/live_smoke.py` against a `git archive` of HEAD served by **Waitress**, `FLASK_ENV=production`, `TRUSTED_PROXY_COUNT=1`, behind a simulated TLS proxy: install, ingest, search, users, logout, **restart**, repeat |

Environment: Python 3.11.2, Linux x86-64, PostgreSQL 16.2, Node 22 (front-end
tests). Every finding below was confirmed against the code or a running
server, not inferred from tool output alone.

## Fixed findings

Severity reflects impact in the documented deployment: a self-hosted
application behind a TLS reverse proxy, used by authenticated staff.

| ID | Severity | Finding | Fix | Commit |
|---|---|---|---|---|
| AUDIT-SQLI-01 | **High** | `/api/archives/geolocation` interpolated the request's `sort_dir` into `ORDER BY`. | Allowlist `asc`/`desc`. | `d5773fe` |
| AUDIT-SETUP-01 | **High** | The setup probes `system-check` and `test-database` stayed **anonymous after installation**, which allowed unauthenticated reconnaissance and database-connection probing. | 401 for anonymous users and 403 for non-admins once the system is initialised. Confirmed live. | `d5773fe` |
| AUDIT-AUTH-01 | **High** | `must_change_password` was recorded but not enforced: an account holding an admin-issued temporary password could use the whole API. | The auth hook gates such sessions to the change-password flow. Confirmed live: 403 on search until the password is changed, then 200. | `1816b0a` |
| AUDIT-XSS-01 | **Medium** | The dashboard and path-analysis pages rendered database values (file names, words, paths) into `innerHTML` unescaped. File names come from ingested evidence, so they are attacker-controlled. | Escape everything on render. | `d5773fe` |
| AUDIT-CSRF-01 | **Medium** | Four state-changing `fetch` calls omitted `X-CSRFToken`, so those actions always failed with 400. Protection held, but the features were broken. | Send the token. | `d5773fe` |
| AUDIT-ARCH-01 | **Medium** | 7z members were size-checked only *after* extraction, so a 7z bomb could fill the disk before the limit applied. | Check the declared sizes before writing, as the zip and tar paths already did. | `d5773fe` |
| AUDIT-DEP-01 | **Medium** | `PyPDF2` (unmaintained, known CVEs) and a `Pillow` floor with known CVEs. `libpff-python` was a hard requirement whose C build fails on most hosts, which broke clean installs. | `pypdf>=6`, `Pillow>=12.3`; `libpff` moved to the optional `pst` extra. | `d5773fe` |
| AUDIT-HSTS-01 | **Medium** | HSTS depended on an `app.config` key that Flask never sets, so it was **never sent**. | Send it when `FLASK_ENV=production` and the request is HTTPS. It is never sent on plain HTTP. | `d5773fe` |
| AUDIT-PROXY-01 | **Medium** | Behind a reverse proxy, every client appeared as the proxy's address: one shared rate limit, lockouts hitting everyone, and a useless audit trail. | Opt-in `TRUSTED_PROXY_COUNT` enables `ProxyFix`. | `d5773fe` |
| AUDIT-OPS-01 | **Medium** | No production WSGI server: production ran on Flask's development server. | Opt-in Waitress (`WSGI_SERVER=waitress`, `apps/web/serve.py`), keeping the forwarded headers so ProxyFix stays the single point of proxy trust. HSTS verified live under Waitress. | `611d3f5` |
| AUDIT-HEALTH-01 | **Medium** | *(Found by the live smoke test.)* After the setup wizard wrote the database settings, `/health` reported `degraded` (it connected to the pre-install `localhost`) **until the process was restarted**, so a fresh install looked unhealthy to monitors and load balancers. The cause: `database.get_db_config` read the cached settings object and bypassed the env-over-file rule (ARCH-02). | Delegate to `settings.config.get_db_config`. Confirmed live: `healthy` 4 s after install. | `508ffb0` |
| AUDIT-CONF-01 | **Low** | `.env.example` and the installer documented `DB_POOL_MIN/MAX_CONNECTIONS` and `LOG_LEVEL`, but the runtime read other names or hard-coded INFO, so the settings were **silently ignored**. Five more documented variables are read by nothing. | Accept both pool names and apply `LOG_LEVEL`. The unread variables are labelled *reserved* in `.env.example`, with the full list in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). | `34e54fd` |
| AUDIT-DB-01 | **Low** | `ContentDBService.hash_exists(side_id=…)` called a repository attribute that does not exist (`AttributeError` on every call). | Route the side-scoped check through `DedupService.hash_exists_with_live_path(hash, source, side)`. | `d5773fe` |
| AUDIT-PY-01 | **Low** | The `/api/paths` error path referenced an undefined logger, so any error became a 500. | Define it. | `d5773fe` |
| AUDIT-PY-02 | **Low** | Duplicate dict keys in `search_algorithms` silently dropped entries. | Deduplicate. | `d5773fe` |
| AUDIT-DATE-01 | **Low** | `date.today()` defaults were evaluated once at import, so dates were stale in a long-running server. | Evaluate per call. | `d5773fe` |
| AUDIT-UI-01 | **Low** | A registry icon written as `bi bi-person-check` failed validation. | Bare icon name. | `d5773fe` |
| AUDIT-LINT-01 | **Low** | An unreachable duplicate `except Exception` in `StoragePipeline._store_file_sync` (B025): an older copy without the `db_hub` null guard. A latent undefined `Optional` (F821). Forensic MD5/SHA-1 without `usedforsecurity=False` (B324), which makes `hashlib` **raise on FIPS-mode Python**. | Remove the dead handler, import the name, flag the digests (values unchanged). | `bd38341`, `65a1307` |

**Test-suite defect TEST-ORDER-01**, found
during the baseline run. `test_schema_reference` failed whenever it ran after
`test_migration_0007`, which downgrades and re-upgrades a migration on the
shared database; PostgreSQL appends re-added columns, so the column order
changed. The test now migrates a dedicated database (`b3d6f02`). The failure
was reproduced deterministically before the fix.

## Residual items (not changed, with justification)

*Historical: the residuals as they stood at v2.1.1. Their current status is
defined only in [Residual items at v2.2.0](#residual-items-at-v220), the
single authoritative table; RES-CSP-01 and RES-AUTH-02 below are resolved.*

| ID | Severity | Item | Why it stays / what to do |
|---|---|---|---|
| RES-CSP-01 | Medium | The CSP allows `'unsafe-inline'` for scripts: 15 templates contain executable inline `<script>` blocks and 27 use `on*=` handlers. A further 36 templates hold `application/json` data blocks, which CSP does not execute and which are fine. XSS is addressed at the source (AUDIT-XSS-01, escaping on render), but the CSP is not a second line of defence yet. | Moving all of them into `static/js/pages/*` modules touches most screens, which is too broad for a patch release. New code must not add inline script ([DEVELOPMENT.md](docs/DEVELOPMENT.md)). Target: v2.2. |
| RES-AUTH-02 | Low | `/auth/change-password` does not ask for the current password. It needs an authenticated session and a CSRF token, and it falls under the default API rate limit (60/min, 600/h), not a stricter one. | Someone holding a live session could lock out its owner. Adding the check changes the first-login flow (temporary password) and the UI; planned. |
| RES-SQL-01 | Low | bandit reports 158 B608 "string-built SQL" sites. The audit reviewed the request-facing ones: one was exploitable (AUDIT-SQLI-01, fixed). The rest compose constant identifiers, `%s` placeholder lists or values that pass through `core/sql_safety.py`. | Not every one of the 158 sites was proven individually. The rule for new code is parameters only, with identifiers through `core/sql_safety.py`. |
| RES-XML-01 | Low | bandit B314: `xml.etree` parses untrusted XML (draw.io, e-books, OOXML parts, SVG). | **Verified mitigated** with the bundled expat 2.5.0: a billion-laughs document is rejected by expat's amplification limit, and external entities (XXE) are not resolved. This relies on expat ≥ 2.4.1, which current Python builds ship. `defusedxml` would be belt-and-braces. |
| RES-BIND-01 | Info | bandit B104: `FLASK_HOST` defaults to `0.0.0.0`. | Intentional for LAN deployments. The docs recommend `127.0.0.1` behind a same-host proxy ([CONFIGURATION.md](docs/CONFIGURATION.md)). |
| RES-B110-01 | Info | bandit B110/B112: 132 `try/except/pass` or `continue` blocks. | Mostly best-effort cleanup and optional-feature probes. Not individually changed, to avoid behaviour churn. |
| RES-LINT-02 | Info | ruff (E, F, W, B, excluding tests): 191 F401 unused imports, 38 F841 unused variables, 17 B904, 17 B007, 8 B905, 2 F811. Also B023 at `core/detect_binanry_utils.py:324-326`, a false positive (the closure runs inside the loop iteration). | Style and dead code with no behavioural effect. A mass auto-fix across 471 files would hide real changes in review. Clean up per module when it is next edited. |
| RES-ARCH-02 | Low | `Api/utils/utils.py` is loaded twice under two module names (`Api.utils_module` from the package `__init__`, and `Api.utils.utils` from `services/jobs/repository.py`), so each copy keeps its own `DatabaseHub` singleton and pool. | Tested: both copies keep working after a database-settings change. It wastes connections but is not a failure. Consolidate imports when the package is next changed. |
| RES-LOG-01 | Low | On the very first start, before the setup wizard has run, several components log ERROR stack traces while trying `localhost:5432`. | Cosmetic, and it stops once setup completes (the post-restart log has 0 errors). Touches many modules. |
| RES-DEP-02 | Info | `rapidocr-onnxruntime` depends on the full `opencv-python`, which needs `libGL`. On headless Linux, `import cv2` fails and OCR, image and video readers degrade. | Documented workaround: swap in `opencv-python-headless` ([INSTALL.md](docs/INSTALL.md), [DEVELOPMENT.md](docs/DEVELOPMENT.md)). Windows and desktop hosts are unaffected. |
| RES-PST-01 | Info | PST mailboxes need the optional `libpff-python` (the `pst` extra), whose build needs a C toolchain. | Without it, PST files are recorded with an explanatory error rather than skipped silently. |
| RES-OPS-02 | Info | Waitress is opt-in (`WSGI_SERVER=waitress`); the default remains the built-in server, so existing `start.bat` workflows keep working. | Production docs require Waitress and never recommend the development server ([OPERATIONS.md](docs/OPERATIONS.md)). |

## Test evidence

Same environment for both columns: Python 3.11.2, PostgreSQL 16.2
(`pgserver`), Node 22, and `opencv-python-headless` (see RES-DEP-02).
Counts come from `pytest -rA`.

| Suite | v2.1.0 (baseline, `13b7ace`) | v2.1.1 (`e96b173`) |
|---|---|---|
| Unit (`tests/unit`) | 1757 passed, 21 failed, 11 errors, 2 skipped | **1855 passed, 0 failed**, 2 skipped |
| Integration + security + e2e | 696 passed, 7 failed | **722 passed, 0 failed** |
| `pip-audit -r requirements.txt` | known CVEs (PyPDF2, Pillow floor) | **no known vulnerabilities** |
| Clean-venv `pip install -r requirements.txt` + `pip check` | fails (`libpff-python` C build) | **succeeds** in about 50 s; `pip check` clean |
| ruff critical rules (E9, F63, F7, F82) | 1 (F821) | **0** |

The two skips are expected: `pyzipper` is optional, and one screen-inspector
check has no markup bindings to inspect yet.

### Classification of baseline failures

| Class | Baseline failures | Resolution |
|---|---|---|
| **PRE-EXISTING**: generated documents missing or stale (interface registry, evidence, endpoint inventory, component library, experience contract, screen inspector, action surface, productization map, domain model) | 19 unit failures and 11 unit errors, 2 integration | Generated with the project's own generators; missing documents written |
| **PRE-EXISTING**: registry icon `bi bi-person-check` (`invalid_icon`) | 1 unit, 1 integration | AUDIT-UI-01 |
| **PRE-EXISTING**: phantom repository call (`.hashs_repo`) | 1 unit | AUDIT-DB-01 |
| **PRE-EXISTING**: stale compiled `.mo` catalogs (`ar`, `he`, `fa`, `hr`) | 4 integration | Catalogs recompiled |
| **ENVIRONMENTAL**: `import cv2` fails without `libGL` (full `opencv-python` pulled in by RapidOCR) | about 80 unit failures in an earlier run without the headless build | Documented workaround (RES-DEP-02); zero with it applied |
| **FAIL** introduced on this branch | 1: `test_schema_reference` order dependence (TEST-ORDER-01) | Fixed in `b3d6f02` |
| **BLOCKED** | none | none |

### Live smoke test

`tools/smoke/live_smoke.py` ran against a `git archive` of the release commit,
served by Waitress 3.0.2 with `FLASK_ENV=production` and
`TRUSTED_PROXY_COUNT=1`, behind a simulated TLS proxy, on a new database:

| Phase | Result |
|---|---|
| install (wizard: DB test, install, second install refused) | 4/4 |
| run1 (health, HSTS only on HTTPS, 401s, CSRF ×3, allowlist, ingest, search ×5, notifications, exports, users and password gate, role denial, logout) | 29/29 |
| **restart** without `FLASK_ENV` in the environment, so production mode had to come from the wizard's `.env`, then run2 | 29/29; 0 ERROR lines in the server log |
| re-ingest the same folder into the same source and side | 0 stored, 4 duplicates (idempotent) |

Search for the keyword found all five files: text, CSV, DOCX, the ZIP's
duplicate memo and the nested `inner/notes.txt`. The duplicate inside the
ZIP is stored as a second occurrence (`bundle.zip::copy_of_memo.txt`,
parent = the ZIP) of the same content hash as `memo.txt`, as the identity
model requires.

## Manual actions that remained at v2.1.1

The first two were closed in v2.2.0; for the third, v2.2.0 ships the
nginx and systemd configurations and start-up checks (see below).

* **Licence.** The repository had no LICENSE file. Choosing one was the
  owner's decision.
* **CSP hardening (RES-CSP-01)** and **current-password check (RES-AUTH-02)**
  were planned for the next minor release.
* **Production cut-over** is per deployment: install the `server` extra, set
  `WSGI_SERVER=waitress` and `TRUSTED_PROXY_COUNT`, and put TLS in front
  ([OPERATIONS.md](docs/OPERATIONS.md)).

## v2.2.0 follow-up

**Scope:** everything from `v2.1.1` (`8481875`) to the v2.2.0 release on
branch `arena/01a0daf1-syltharae`: the licence, the strict CSP, the password
change, the production deployment (nginx, Waitress, systemd), CI, the OCR
failures CI exposed, and a DOCX data-loss defect the final test matrix
exposed. The v2.1.1 findings above were not re-audited.

### Fixed in v2.2.0

Each fix has a regression test that fails on the code before it, unless the
Verification column says otherwise.

| ID | Severity | Finding | Fix | Verification | Commit |
|---|---|---|---|---|---|
| LIC-01 | **High** (distribution) | No licence; `pyproject.toml` said `Proprietary` while PyMuPDF (AGPL) is a hard dependency. | AGPL-3.0-or-later chosen by the owner; LICENSE, metadata, source offer on every page (section 13), third-party notices, dependency licence gate (115 compatible). | `tests/unit/test_licensing.py`; CI `static` job | `978c596`, `5c2aa8f` |
| DOCX-DROP-01 | The DOCX reader silently dropped paragraphs. | High | **Resolved** | `3d403ed`. | `test_docx_reader_completeness.py`; live 200 of 200 after the upgrade. | None. |
| RES-CSP-01 | Medium | CSP allowed `'unsafe-inline'` scripts. | `script-src 'self'`: 599 inline handlers became `data-on-*` run by a non-`eval` interpreter; 17 inline scripts became static files. | `test_csp_no_inline_script.py`; browser smoke 81/81 through nginx | `730c20e`, `9562a5f`, `32103a0` |
| RES-AUTH-02 | Low → fixed | Password change did not require the current password. | Current password required; wrong guesses count towards lockout, lockout ends all sessions, rate limited, other sessions signed out on success; UI and translations. | `test_change_password.py` against a disposable PostgreSQL; live: refused without it (`current_password_required`), old password refused, re-login | `74ddeac`, `3bb9f19` |
| AUDIT-PROXY-02 | Medium | Audit log took the client address from the first `X-Forwarded-For` value, which clients control. | Uses the `TRUSTED_PROXY_COUNT` address, like the rate limiter. | `test_proxy_deployment.py`; live: forged header, log shows `::1` | `ad59557` |
| INSTALL-ENV-01 | Medium | The wizard wrote the admin password to `.env` in clear, and omitted `WSGI_SERVER`/`TRUSTED_PROXY_COUNT`. | Neither password written; serving settings persisted. | Installer tests; live restart with those variables removed from the environment | `84057de` |
| ANALYSIS-01 | Medium | Every `/api/analysis/*` call answered 500. | `file`, `statistics`, `batch` work; unimplemented ones answer 501. | Route tests; live analysis checks | `28182cf` |
| SETUP-AUDIT-IP | Low | Wizard-created admin audited with an empty address. | Wizard user's address. | Setup test | `5fca28e` |
| NO-CI | Medium (process) | No CI. | Every push runs install, all suites with PostgreSQL/Node/nginx required, build, ruff, bandit baseline, pip-audit, licences, OCR self-check. | `.github/workflows/ci.yml` | `6dd88db`, `4680613`, `51e320b`, `81a546a`, `f33ba20`, `8d8f5b3` |
| DEPLOY-01 | Medium | No shipped proxy/service configuration; unsafe settings ignored silently. | `deploy/nginx`, `deploy/systemd`, start-up checks (exit 2 on unsafe settings). | `test_deploy_configs.py`, `test_deploy_nginx.py` (real `nginx -t`) | `1d7a801`, `74f137b` |
| RATE-01 | Medium | Reads every page makes hit the default limit: theme (60/min), then i18n catalog and notification stats (600/h; the badge polls every 30 s per tab). | `INTERACTIVE_READ_LIMIT` (600/min, still bounded) on those reads. | `test_rate_limiting.py` (both parts fail before); browser smoke fails on any 4xx | `a556f40`, `08e3996` |
| DEPLOY-H2-GOAWAY | Medium | nginx closed each HTTP/2 connection after 1000 requests (default `keepalive_requests`); scripts requested in that instant never reached nginx and browsers did not retry them, so pages loaded with dead controls every ~20 page views (nginx ticket #2155). | `keepalive_requests 100000; keepalive_time 1h;`. | `$connection_requests` logging proved it (all connections ended at 1000; the lost requests absent from the log); the unmodified crawl failed 5 of 5 times before (two crawls with extra per-page polling passed: the boundary fell elsewhere); 3 complete crawls after, no dropped request; config test | `a93cdec` |
| CACHE-02 | Low | Static assets sent `no-cache, max-age=31536000, public, immutable` - contradictory; the one-year half would pin old JavaScript after upgrades if honoured. | `no-cache` + ETag revalidation. | `test_http_caching.py` (static fails before; API/page cases cover the untested CACHE-01) | `01553c8` |
| OCR-TESS-01 | **High** (data quality) | With tesseract, OCR bypassed the engine layer: no confidence or boxes, no rotation or small-text handling, silent English fallback, word-data failures swallowed (`except Exception: return []`). | Engine routing; visible `confidence_error`; orientation by scored readings incl. 180-degree check; small-text size search; per-language reading; scan-resolution PDF rendering; original-image re-read. | 17-failure matrix below; OCR self-check | `ba34829`, `27fa440`, `8e262b6`, `0ece38a` |
| I18N-HR-01, REFDOC-DRIFT, TEST-RAR-ENV | Low | Missing Croatian strings; generated reference docs out of date; a RAR test depended on the host. | Translations; regenerated with the generator; simulated decoder absence. | Doc staleness check; i18n tests | `3bb9f19`, `20eb55f`, `a70c9c0`, `6de57a4` |
| SMOKE-DB-01 | Low (tooling) | The smoke's install phase ignored `SMOKE_DB_PORT`/`SMOKE_DB_USER`. | Sends them. Smoke also gained a live OCR check and fails on any same-origin 4xx or dropped request. | Live run on a password-protected cluster | `0edb95f` |
| DEPLOY-RW-01 (doc) | Low | The systemd unit's list of files written to the install directory was incomplete. | Complete list; test derives it from code constants. Relocation is a follow-up (residuals). | `test_deploy_configs.py` (fails on the old comment) | `b7c1027` |
| OCR-LANG-02 | Low | A file read without a requested language model recorded that only in the run's log: `missing_ocr_languages` was never persisted, so such files could not be found afterwards. | `extraction_provenance.ocr.missing_languages` (image; union over a PDF's OCR pages). | `test_ocr_engines.py::TestStoredOcrProvenance` (2 fail before), `test_ocr_provenance_docs.py` (fails before) | `44e6eed` |
| SELFCHECK-REM-01 | Low (diagnostics) | Self-check remediations sent operators wrong ways: `not_selected` named a setting `OCR_ENGINE` that does not exist; `low_confidence` claimed ingestion would "flag" such text (it stores the confidence, nothing else); `invocation_failed` pointed at an error "below" printed above. | Texts state the fixed engine order, what is stored, and where the error is. | `test_ocr_selfcheck.py::test_remediations_name_only_real_settings` (fails before); induced failures: missing `heb` (exit 1, `missing_languages`), broken binary (exit 1, `invocation_failed` with the engine's error) | `9922312`, `44e6eed`, `aa04b73` |
| CI-SKIP-01 | Low (process) | CI reported skipped tests as a count only (4 on `0ece38a`); job logs and artifacts sit in blob storage that is not always reachable. | One annotation lists every skipped test with its reason. | `test_junit_annotations.py` | `7c685e6` |
| DOCX-DROP-01 | **High** (data loss) | The DOCX reader remembered the paragraphs it had used by `id()` of python-docx proxies, which are rebuilt and freed on every access. A reused address made a different paragraph look used, so it was skipped: no log line, job `COMPLETED`, text absent from storage, search and display. 12-18 % of the paragraphs of a 40-section document, a different set each read; in the code since v2.1.0. Found through an intermittent failure of `test_docx_raw_text_preserves_styles_and_tables` (four-paragraph fixture) in the full local matrix; it passed in isolation and in CI. | `Document.iter_inner_content()`: one `Paragraph` or `Table` per body element, in body order. Upgrade step for stored text: DOCX-DROP-02. | `test_docx_reader_completeness.py`: 4 of 6 fail before (67, 70 and 45 of 440 body elements dropped), 6 of 6 pass after. Live, through nginx and Waitress: the deployed candidate stored 164 of 200 paragraphs; after upgrading, a new upload stored 200 of 200. | `3d403ed` |
| CI-SKIP-02 | Low (test coverage) | CI's new skip listing showed `test_encrypted_zip_is_reported_as_encrypted` skipped in every run: `pyzipper`, which only builds its AES-encrypted fixture, was never declared, so encrypted-ZIP detection was never exercised in CI. | `pyzipper` in the `dev` extra; the test imports it directly, so a missing test dependency fails instead of skipping. | The test runs and passes; CI skip listing. | `8dbbdee` |
| ARCHIVE-PATH-01 | Low (distribution) | The release-archive audit found `tools/scalability/setup_env.sh` (a measurement script from the original upload) defaulting to one machine's virtualenv, `/home/user/.venv`. No other shipped file carried a machine path; example paths in UI placeholders are not read from. | Defaults to `<repo>/.venv-scalability`, which `.gitignore` now covers. | `test_no_machine_paths.py` scans the shipped files for the checkout's own path, home-directory virtualenvs and sandbox hosts; before the fix it reported that one line. | `00906c4` |

### OCR root-cause record (OCR-TESS-01)

**Original condition.** `core/ocr` (the engine layer: confidence, word
boxes, provenance) was written for RapidOCR. The image and PDF readers
checked for the Tesseract binary first and, when present, called pytesseract
directly on a legacy branch that bypassed the layer. Every host with
Tesseract, the preferred engine and CI's configuration, therefore ran code
that:

* stored no confidence and no word boxes;
* had no orientation handling (Tesseract's OSD declines short text);
* lost small text in the shared binarisation;
* replaced a missing language pack with English, silently;
* turned any failure to read word data into an empty list
  (`except Exception: return []`).

Development hosts without Tesseract used the layer, which is why local runs
passed and CI, the first run with Tesseract installed, failed 17 tests.

**Consequences.**

* Scanned documents ingested with Tesseract carried text with no confidence,
  so the file details API stored `null` and comparisons with it raised
  `TypeError`.
* Turned pages were indexed as junk (`T2GS 1SAINOILVLOY` for a line read
  upside down), so their words could not be found.
* Small print read as nothing.
* Hebrew or Arabic text on a host without those packs was read with the
  English model and nobody was told.
* A failing `image_to_data` was indistinguishable from a page without
  words.

**Correction.**

* Every reader goes through the engine layer (`ba34829`).
* Orientation is chosen by reading and scoring each orientation
  (`_reading_score`), and a confident reading is checked against the page
  turned 180 degrees (`27fa440`).
* Small text gets a bounded size search, and PDF pages are rendered at
  their scan's resolution (`8e262b6`); images up to 40,000 px always get
  the size search (`0ece38a`).
* Each language also reads alone.
* A missing confidence is recorded with its reason (`confidence_error`),
  and a missing language pack with its codes (`missing_languages`,
  `44e6eed`).
* The CI self-check gate fails early, with a stage and a remediation, when
  Tesseract or a language pack is missing.

[ARCHITECTURE.md §5.1](docs/ARCHITECTURE.md#51-ocr-engine-layer) describes
the result.

**Additional findings while correcting it.**

* The combined `heb+eng+ara` model misreads mixed text, hence per-language
  readings.
* Mean confidence cannot separate a line from itself upside down (0.82-0.85
  inverted against 0.96 upright), hence the 180-degree check.
* A fixed 144 dpi render discarded scan pixels.
* CI's font renderer measures 7 px glyphs at 5.0 px, below the glyph gate
  (`test_low_resolution_recovers_via_retry`).
* `missing_ocr_languages` was never persisted (OCR-LANG-02).
* Three self-check remediations were wrong (SELFCHECK-REM-01).
* Files OCR'd before the fix cannot be re-read in place (REPROCESS-01).

**Verification.**

* The 17 CI failures, one by one, are in the table below.
* Old code (`81a546a`), OCR group: 16 failed on 5.3.4 and 17 on 5.5.1.
* Each fix commit's new tests fail on its parent.
* The release candidate's OCR group, all three engine configurations, and
  the self-check on each are recorded in
  [Final OCR matrix](#final-ocr-matrix).
* CI on `0ece38a`: 2741 tests, 0 failures, 0 errors, 4 skipped; self-check
  5/5 `success` at 0.95-0.96 on Ubuntu's Tesseract 5.3.4.

### CI OCR failures: disposition of all 17

CI on `81a546a` (Ubuntu 24.04, tesseract 5.3.4) failed 17 tests. The old
implementation was re-run locally with the same engine version (tesserocr
2.7.1 bundling 5.3.4, `tessdata_fast` 4.1.0 = Ubuntu's models) and with 5.5.1;
HEAD was run with 5.3.4, 5.5.1 and RapidOCR only. No assertion was weakened.

| Test | Failure on `81a546a` | Root cause | Class | Change | Verification |
|---|---|---|---|---|---|
| `test_ocr_matrix::TestOrdinaryText::test_extracted_with_full_provenance` | `KeyError: 'ocr_confidence'` | Legacy pytesseract branch never recorded confidence | App defect | Engine routing (`ba34829`) | Fails old 5.3.4/5.5.1; passes on the release candidate with all three engine configurations |
| `test_ocr_matrix::TestMultiPagePdf::test_every_page_is_ocrd_in_order` | `KeyError: 'ocr_confidence'` | Same, PDF page path | App defect | `ba34829` | Same |
| `test_ocr_engines::TestImageReaderOcr::test_printed_text_is_extracted_with_provenance` | `KeyError: 'ocr_confidence'` | Same | App defect | `ba34829` | Same |
| `test_ocr_engines::TestImageReaderOcr::test_blocks_carry_confidence_and_bbox` | `KeyError: 'bbox'` | Legacy branch returned no word boxes | App defect | `ba34829` (boxes mapped back after rotation/scale) | Same |
| `test_file_details_api::test_ocr_provenance_is_visible` | `'<' not supported between float and None` | Confidence stored as `None` silently | App defect | `ba34829` (+ `confidence_error` when missing) | Same (real PostgreSQL) |
| `test_provenance_persisted::test_ocr_image_records_engine_and_derived` | same `TypeError` | Same, persisted row | App defect | `ba34829` | Same (real PostgreSQL) |
| `test_embedded_images::test_docx_embedded_image_carries_ocr_provenance` | provenance lacked OCR fields | Embedded images went through the legacy branch | App defect | `ba34829` | Same |
| `test_ocr_matrix::TestRotation::test_rotated_text_is_still_read[90]`, `[180]`, `[270]` | junk text (`ce`, `T2GS 1SAINOILVLOY`, `BS)`) | No rotation handling; OSD refuses short text | App defect | Scored orientation search (`ba34829`), 180-degree check (`27fa440`) | Same |
| `test_date_and_orientation::test_standalone_rotated_image_text_extraction[90]`, `[180]`, `[270]` | `9`, `1105-0103`, `:` | Same; `[270]` then also needed the original-image re-read on 5.3.4 | App defect | `ba34829`, `27fa440`, `8e262b6` | Same |
| `test_date_and_orientation::test_pdf_page_rotated_image_text_extraction[90]`, `[270]` | `PDFROTATED_80_¥744`, `POFROTATED_270_?744` | Same, plus pages rendered below scan resolution (fixed 2x discarded 20% of the pixels) | App defect | `ba34829`, `8e262b6` (`ocr_render_zoom`) | Same |
| `test_ocr_matrix::TestResolution::test_low_resolution_recovers_via_retry` | reader read nothing | Binarisation destroys 7 px text (old); on CI's renderer (DejaVu 2.37-8) marks measure 5.0 px, below the glyph gate | App defect + engine/renderer difference | Size search (`ba34829`), always for images <= 40k px (`0ece38a`) | Fails old; CI's measurement forced locally: exact at 0.905 |
| `test_ocr_matrix::TestResolution::test_very_large_image_is_not_refused` | failed in CI only | Not reproduced locally on the old code with 5.3.4 or 5.5.1; the test is unchanged. Most likely CI's renderer on the legacy branch - unconfirmed | Environment (not reproduced) | Superseded by `ba34829` | Passed in CI from `8d8f5b3` on; passes on the release candidate with all three engine configurations |

`test_pdf_page_rotated_image_text_extraction[180]` failed in CI only after
`ba34829` (5.3.4 read the right orientation at 1.7% confidence); it is fixed
by the render resolution in `8e262b6` and passes on all three engines.

**Fail-before evidence.** Old code (`81a546a`), OCR group: 16 failed on 5.3.4
(the 17 above minus `very_large`), 17 on 5.5.1 (plus `pdf_page[180]`).
Per fix: `ba34829` - 17 new tests fail on its parent; `27fa440` -
`TestUpsideDown` fails on its parent; `8e262b6` - 10 fail on its parent under
5.3.4; `0ece38a` - the small-image test fails on its parent; `44e6eed` - 3
fail on its parent.

### Final OCR matrix

Release-candidate code (`aa04b73`; later commits change documentation only). Tesseract 5.3.4 and 5.5.1 are
the libtesseract builds bundled by tesserocr 2.7.1 and 2.9.2, driven through
the `tesseract` command line pytesseract uses, with Ubuntu's models
(`tessdata_fast`: `ara`, `eng`, `heb`, `osd`). CI runs Ubuntu 24.04's own
5.3.4 package. RapidOCR only means no `tesseract` on `PATH`.

The OCR group is the ten files listed in
[TESTING.md](docs/TESTING.md#ocr-tests): seven unit files, and three
integration files against a disposable PostgreSQL.

| Configuration | OCR group | Self-check |
|---|---|---|
| Tesseract 5.3.4 | 205 passed, 0 failed, 2 skipped | `--require tesseract`: exit 0, 5/5 `success`, confidence 0.95-0.96 |
| Tesseract 5.5.1 | 205 passed, 0 failed, 2 skipped | `--require tesseract`: exit 0, 5/5 `success`, confidence 0.96 |
| RapidOCR 1.4.4 only | 207 passed, 0 failed, 0 skipped | `--require rapidocr`: exit 0, 5/5 `success`, confidence 0.97-0.99 |

The two skips under Tesseract are engine-specific by design, not skipped
for an environment problem: `test_fallback_engine_models_are_latin_and_chinese_only`
and `test_hebrew_is_not_reliably_supported_by_the_fallback` state limits of
the RapidOCR fallback, and they run (and pass) in the RapidOCR
configuration.

Induced failures, self-check on 5.3.4:

| Induced condition | Exit | Classification |
|---|---|---|
| `heb` model removed | 1 | `[FAIL] languages: missing_languages: heb`, with the install command |
| A binary that exits 1 | 1 | `invocation_failed` on every sample, carrying the engine's `Error opening data file` |

### Live production verification

Browser → nginx 1.28.0 (HTTPS, HTTP/2) → Waitress 3.0.2 → Flask →
PostgreSQL 16.2 (scram-sha-256), from `git archive` of `f55b6ab` installed in
a fresh virtual environment per INSTALL.md (`pip check`: only the documented
`opencv-python` line). install 4/4, run1 55/55, restart with the serving
settings only in `.env`, run2 55/55, browser 81/81 twice. No 5xx; every 4xx
was a deliberate negative check. Details: [TESTING.md](docs/TESTING.md#live-smoke-test).

### Residual items at v2.2.0

The single authoritative residual table: every open, accepted or carried item
of the release, and those resolved in it. Statuses: **Resolved** (fixed in
v2.2.0), **Accepted** (known, kept by decision), **Carried** (unchanged since
v2.1.1, still justified), **Open** (a planned follow-up). Release impact
says whether it blocks v2.2.0 and why.

| ID | Description | Severity | Status | Remediation | Verification | Release impact |
|---|---|---|---|---|---|---|
| RES-CSP-01 | The CSP allowed `'unsafe-inline'` scripts. | Medium | **Resolved** | `script-src 'self'` (`730c20e`, `9562a5f`, `32103a0`). | `test_csp_no_inline_script.py`; browser smoke under the policy through nginx. | None. |
| RES-CSP-02 | `style-src` still allows `'unsafe-inline'`: templates use `style` attributes, and Settings applies administrator-authored custom CSS ([SECURITY.md](docs/SECURITY.md) §5). | Low | Accepted | Injected CSS cannot run script. Follow-up: move `style` attributes to classes and serve the custom CSS as a stylesheet, then drop it. | `test_csp_no_inline_script.py` fails if SECURITY.md quotes a policy other than the one served. | Not blocking: no script execution path. |
| RES-AUTH-02 | Password change did not require the current password. | Low | **Resolved** | `74ddeac`, `3bb9f19`. | `test_change_password.py` (disposable PostgreSQL); live smoke. | None. |
| LIC-01 | No licence; metadata said `Proprietary` while PyMuPDF is AGPL. | High | **Resolved** | AGPL-3.0-or-later (`978c596`, `5c2aa8f`). | `test_licensing.py`; CI licence gate. | None. |
| RES-SQL-01 | 158 bandit B608 "string-built SQL" sites; the request-facing ones were reviewed at v2.1.1 (one fixed, AUDIT-SQLI-01). | Low | Carried | Parameters only in new code; identifiers through `core/sql_safety.py`. | CI bandit baseline fails on any new site. | Not blocking: reviewed; new sites are gated. |
| RES-XML-01 | bandit B314: `xml.etree` parses untrusted XML. | Low | Carried | Follow-up: `defusedxml`. | Verified at v2.1.1: expat rejects a billion-laughs document and resolves no external entities. | Not blocking: mitigated by expat >= 2.4.1. |
| RES-BIND-01 | bandit B104: `FLASK_HOST` defaults to `0.0.0.0`. | Info | Carried | The documented production configuration sets `127.0.0.1`. | Start-up warns `proxy_bypassable` when a trusted proxy is configured while the app accepts outside connections (`test_deploy_configs.py`). | Not blocking: intended for LAN use; warned. |
| RES-B110-01 | Best-effort `try/except/pass` blocks (bandit B110/B112). | Info | Carried | Changed per module when it is next edited. | Reported by bandit (low severity). | Not blocking. |
| RES-LINT-02 | Unused imports and variables. | Info | Carried | Per module when next edited. | CI enforces ruff's correctness rules. | Not blocking: no behavioural effect. |
| RES-ARCH-02 | `Api/utils/utils.py` loaded under two names (two pools). | Low | Carried | Consolidate the imports. | v2.1.1: both copies keep working after a settings change. | Not blocking: wastes connections only. |
| RES-LOG-01 | Before installation, start-up logs ERROR traces for the unconfigured database. | Low | Carried | Touches many modules. | Production gate: 0 ERROR lines after installation ([Live production verification](#live-production-verification)). | Not blocking: cosmetic, pre-install only. |
| RES-DEP-02 | RapidOCR's `opencv-python` needs `libGL` on headless Linux. | Info | Carried | Documented headless OpenCV swap ([INSTALL.md](docs/INSTALL.md)). | Clean-install gate. | Not blocking. |
| RES-PST-01 | PST needs the optional `pst` extra (C toolchain). | Info | Carried | Optional extra. | Without it PST files record an explanatory error. | Not blocking. |
| RES-OPS-02 | Waitress is opt-in (`WSGI_SERVER=waitress`). | Info | Carried | Production docs and the wizard's `.env` select it. | Restart gate: Waitress from `.env` alone. | Not blocking. |
| DEPLOY-RW-01 | The service writes to its install directory (settings store, search history, instance lock, `.env`). | Low | Open | Move this state under `APP_DATA_DIR`, with a migration for existing installs; then `ReadOnlyPaths=/opt/syltharae`. | `test_deploy_configs.py` checks that `ReadWritePaths` lists exactly what the code writes. | Not blocking: access is granted to exactly those paths. |
| DEPLOY-H2-02 | After `keepalive_time` (1 h) of continuous use nginx still sends one GOAWAY; a page loading in that instant could lose a script (a reload fixes it). | Info | Accepted | Disabling HTTP/2 is the only complete remedy and costs the event-stream connection budget. | Real nginx: many requests on one connection without GOAWAY ([Live production verification](#live-production-verification)). | Not blocking: once per hour per connection, recoverable. |
| REPROCESS-01 | `files.reprocess` is **intentionally reserved**. Trace: the action registry declares it `visibility="not_built"` with no execution; the permission exists; no route, service or job performs it; the file surface binds it only so it is left out with reason `not_built`; the detail page does not offer it. `/api/analysis/file/<id>/reprocess` answers 401 because authentication is default-deny, not because a route exists. Consequences: files OCR'd before v2.2.0 keep their old text (identical files re-submitted are duplicates), and four file-view warnings say a file "may need reprocessing" without a way to do it. | Low | Open | A reprocess job that re-reads a file in place and replaces its text and provenance; then the warnings can link to it. | `test_action_registry.py` (`not_built` is hidden and disabled), `test_action_lifecycle_components.py` (not offered on the detail page), `test_security_regressions.py` (the path is denied). | Not blocking: nothing claims it works; documented in the upgrade note. |
| DOCX-DROP-02 | Text stored for `.docx` files by v2.1.x (or earlier) may lack paragraphs (DOCX-DROP-01). Upgrading does not re-read it, and importing the same file again does not either: identical files share their stored text. | Medium | Open (operator action) | [Upgrade step](CHANGELOG.md#upgrading-from-v211): delete every copy of the document, then import it again. Automatic re-extraction would need `files.reprocess` (REPROCESS-01). | Live on the upgraded stack: the old file still had 164 of 200 paragraphs; a byte-identical re-import also showed 164; after deleting both copies, the re-import stored 200 of 200 (20 of 20 tables). | Not blocking: new ingestion is complete; the recovery is documented and was performed. |
| ANALYSIS-02 | `sentiment`, `topics`, `entities` answer 501 (never implemented). | Info | Open | Implement or remove. | Route tests. | Not blocking: explicit 501. |
| OCR-ARABIC-01 | The self-check has no Arabic sample: rendering one needs Pillow with raqm, which the CI image lacks. | Info | Open | A raqm-enabled image or a pre-rendered Arabic fixture. | The self-check verifies that the `ara` model is installed. | Not blocking. |
| OCR-UI-01 | The file details view shows OCR engine, confidence and "derived" only. Rotation, missing languages and confidence errors are in `GET /api/file/<id>/details`. | Info | Open | Show the remaining provenance fields in the view (new translatable strings). | `test_file_details_api.py` asserts the API fields. | Not blocking: data stored and returned. |
| CI-PY-01 | CI tests Python 3.11 only; 3.10 (the declared minimum) is untested. | Low | Open | A 3.10 matrix entry. | None yet. | Not blocking: 3.11 is the tested and recommended version. |
| CI-NODE20-01 | GitHub warns that the pinned actions (`checkout@v4`, `setup-python@v5`, `setup-node@v4`, `upload-artifact@v4`) target Node.js 20 and are forced onto Node.js 24. | Info | Open | Move to action releases built for Node.js 24 once verified. | CI annotations on `0ece38a` (warning only; jobs succeed). | Not blocking. |
