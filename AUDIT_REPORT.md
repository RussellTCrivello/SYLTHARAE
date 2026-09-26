# SYLTHARAE audit report: v2.1.0 to v2.1.1

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

## Manual actions that remain

* **Licence.** The repository has no LICENSE file. Choosing one is the
  owner's decision.
* **CSP hardening (RES-CSP-01)** and **current-password check (RES-AUTH-02)**
  are planned for the next minor release.
* **Production cut-over** is per deployment: install the `server` extra, set
  `WSGI_SERVER=waitress` and `TRUSTED_PROXY_COUNT`, and put TLS in front
  ([OPERATIONS.md](docs/OPERATIONS.md)).
