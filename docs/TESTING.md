# Testing

SYLTHARAE is tested in layers. All of them run from the repository root in
a virtual environment that has the `dev` extra installed:

    pip install -r requirements.txt
    pip install -e ".[dev,server]"

## Layers

| Layer | Location | Needs | What it proves |
|---|---|---|---|
| **Unit** | `tests/unit/` | nothing external | Pure logic: parsers, readers, format detection, archive safety, state machines, settings, security helpers, the WSGI launcher, the documentation generators. |
| **Front end** | `tests/js/*.mjs`, run by `tests/unit/test_action_*`, `test_frontend_*` and similar | Node.js 18+ on `PATH` | ES modules behave correctly against a DOM stub. These tests are skipped when `node` is missing. |
| **Integration** | `tests/integration/` (marker `integration`) | a disposable PostgreSQL (see below) | Migrations, repositories, ingestion into a real database, search after ingestion, jobs, interface coverage, schema reference. |
| **Security** | `tests/security/` (marker `security`) | disposable PostgreSQL | Authentication, role policy, CSRF, rate limiting, import/export authorisation, error hygiene, and regressions for every audit finding. |
| **End to end** | `tests/e2e/` (marker `e2e`) | disposable PostgreSQL | Full workflows through the Flask test client: pages, operations, ingest → search. |
| **Live smoke** | `tools/smoke/live_smoke.py` | a running server + PostgreSQL | The real production stack over HTTP: setup wizard, Waitress, proxy headers, HSTS, CSRF, ingestion, search, export, users, logout, restart, repeat. |

## The disposable database

`tests/conftest.py` provides the `pg_db` fixture (plus `admin_client`,
`viewer_client` and `client_factory`). It finds a server in this order:

1. **`pgserver`** (a pip-installable PostgreSQL) in a temporary directory,
   with trust authentication. It needs no setup: `pip install pgserver`.
2. **The environment**: if `DB_HOST` is set, it uses `DB_HOST`, `DB_PORT`,
   `DB_USER` and `DB_PASSWORD`. That role must be able to `CREATE DATABASE`.

If neither is available, database-backed tests are **skipped**, not failed,
and the skip reason says why, unless `REQUIRE_POSTGRES=1` is set, as CI does:
then they fail. The fixture creates a fresh database, runs the
real bootstrap (`database/bootstrap.py`, all migrations), and resets every
cached settings or pool singleton so nothing leaks from earlier imports.

Tests that must not depend on what other tests did to the shared database
create their own. `test_schema_reference.py`, for example, migrates a
dedicated database, because `test_migration_0007` legitimately downgrades
and re-upgrades a migration, which reorders columns.

## Running

    python -m pytest tests/unit                          # fast, no database
    python -m pytest tests/integration tests/security    # needs PostgreSQL
    python -m pytest tests/e2e
    python -m pytest -m security                         # by marker
    python -m pytest tests/unit/test_serve.py -k waitress

The project sets `addopts = "-q"`. To see a per-test outcome list, use
`-rA` and count the outcomes:

    python -m pytest -rA tests/unit | grep -E "^(PASSED|FAILED|ERROR) tests/" | cut -d' ' -f1 | sort | uniq -c

Filter on `^(FAILED|ERROR) tests/`: captured log lines also start with
`ERROR`.

## Generated documents are tested

Several documents are generated from the code, and a test fails when the
committed copy is stale:

| Document | Regenerate with |
|---|---|
| `docs/INTERFACE_REGISTRY.md` | `python -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md` |
| `docs/REGISTRY_EVIDENCE.md` | `python -m core.interfaces.evidence --write docs/REGISTRY_EVIDENCE.md` |
| `docs/endpoint_inventory.json` and the evidence part B | `INFORAXIS_WRITE_EVIDENCE=1 python -m pytest tests/integration/test_interface_coverage.py -k "evidence or inventory"` |
| `docs/EXPERIENCE_CONTRACT.md` | `python -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md` |
| `docs/SCREEN_INSPECTOR_EVIDENCE.md` | `python -m core.experience.audit docs/SCREEN_INSPECTOR_EVIDENCE.md` |
| `docs/ACTION_SURFACE_AUDIT.md` | `python -m core.experience.action_audit docs/ACTION_SURFACE_AUDIT.md` |
| `docs/COMPONENT_LIBRARY.md` | `python -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md` |
| `docs/reference/` (modules, routes) | `python tools/docs/generate_reference.py` (`--check` to verify) |
| `docs/reference/DATABASE_SCHEMA.md` | `python tools/docs/generate_schema.py` with `DB_*` pointing at a freshly migrated database, or `INFORAXIS_WRITE_EVIDENCE=1 python -m pytest tests/integration/test_schema_reference.py` |

The job state diagram in `docs/ARCHITECTURE.md` is checked against
`services/jobs/job_state.TRANSITIONS` by `tests/unit/test_architecture_diagrams.py`.

## Live smoke test

`tools/smoke/live_smoke.py` drives a **running** server over HTTP the way a
browser behind a TLS-terminating reverse proxy does. It sends
`X-Forwarded-Proto: https`, a public `X-Forwarded-Host`, a same-origin
`Referer` and the CSRF token. Run it against the production configuration:

    # 1. A throwaway PostgreSQL (any server works; pgserver is quickest)
    python -c "import pgserver; print(pgserver.get_server('/tmp/syl-pg', cleanup_mode=None).get_uri())"

    # 2. A clean checkout, served as production would be
    git archive HEAD | tar -x -C /tmp/syl-app && cd /tmp/syl-app
    APP_DATA_DIR=/tmp/syl-data FLASK_ENV=production WSGI_SERVER=waitress \
      TRUSTED_PROXY_COUNT=1 FLASK_PORT=5055 AUTO_INSTALL=0 python run_web.py

    # 3. In another shell
    export SMOKE_ADMIN_PASSWORD='choose-a-strong-one' SMOKE_EVIDENCE=/tmp/syl-evidence \
           SMOKE_DB_HOST=/tmp/syl-pg SMOKE_DB_PASSWORD=unused-with-trust
    python tools/smoke/live_smoke.py install
    python tools/smoke/live_smoke.py run1
    #    restart the server (Ctrl-C, then start it again without FLASK_ENV,
    #    so production mode must come from the wizard's .env)
    python tools/smoke/live_smoke.py run2

It generates `memo.txt`, `ledger.csv`, `minutes.docx`, `bundle.zip` (a
duplicate of the memo and a nested `inner/notes.txt`) and `scan.png`, a
rendered page whose text exists only as pixels. All of them contain the
keyword *zephyrine*. The script then checks:

* before install, `/health` redirects to `/setup`; the wizard's
  test-database and install steps work, and a second install is refused (409);
* `/health` is `healthy` immediately after install;
* HSTS is sent on proxied HTTPS and not on plain HTTP;
* the session cookie is `Secure`, `HttpOnly`, `SameSite=Lax`; the CSP allows
  no inline script; `nosniff`, framing and referrer headers are present; no
  server software version is disclosed; a static asset is served;
* anonymous API calls get 401 and a bad password gets 401;
* CSRF: a missing token and a cross-site `Referer` are rejected;
* source and side creation, and the `INGESTION_ROOTS` allowlist (`/etc` is
  rejected);
* the ingestion job completes; re-submitting the folder stores nothing new
  and reports duplicates; search finds the keyword in the text, CSV, DOCX,
  nested-archive and scanned files;
* OCR: the scan's details show derived OCR provenance - engine, and a
  confidence in (0, 1];
* an upload is staged, ingested and found; analysis (statistics, file) works;
  the original downloads byte-identical; the event stream answers promptly;
  notifications, settings export and search export work;
* users: an admin creates an analyst; the analyst is gated until they change
  their temporary password; a change without the current password is refused
  (`current_password_required`); after the change the analyst can search, is
  denied admin settings, the old password is refused and the new one signs in;
* logout invalidates the session.

### Through a real reverse proxy

With an `https://` `SMOKE_BASE_URL` the script assumes the shipped nginx
site (`deploy/nginx/syltharae.conf`) is in front and adds proxy checks: the
proxy headers are *forged* on purpose and must be overwritten (a forged
`X-Forwarded-Host` does not break the request; the audit log records the
real client address, not a forged `X-Forwarded-For`), plain HTTP answers
301 to HTTPS without HSTS, HTTP/2 is negotiated over TLS 1.3, and TLS 1.0/1.1
are refused. Point `REQUESTS_CA_BUNDLE` at the certificate so it is verified,
not ignored:

    # nginx serving deploy/nginx/syltharae.conf (server_name, certificate
    # paths and ACME root adapted) in front of Waitress on 127.0.0.1:5000
    export SMOKE_BASE_URL=https://localhost SMOKE_HTTP_URL=http://localhost \
           REQUESTS_CA_BUNDLE=/path/to/cert.pem SMOKE_DB_HOST=localhost \
           SMOKE_DB_PORT=5432 SMOKE_DB_USER=postgres SMOKE_DB_PASSWORD=...

The proxy that *drops* the host name - sign-in then fails with 400 "CSRF
token is missing or invalid" - cannot be produced through a correct nginx, so
it is covered by `tests/security/test_proxy_deployment.py` and by step 2 of
the deployment verification in [OPERATIONS.md](OPERATIONS.md).

Result on this release (commit f55b6ab; nginx 1.28.0 with OpenSSL 3.5.8,
Waitress 3.0.2, Python 3.11, PostgreSQL 16.2 with scram-sha-256, Tesseract
5.3.4): the app ran from `git archive` in a fresh virtual environment
installed per [INSTALL.md](INSTALL.md). install 4/4, run1 55/55; restarted
with `FLASK_ENV`, `WSGI_SERVER` and `TRUSTED_PROXY_COUNT` removed from the
environment so they had to come from the wizard's `.env`: 0 ERROR log lines,
run2 55/55. The browser smoke then passed 81/81 twice in a row. nginx logged
no 5xx; every 4xx was one of the checks' deliberate negative requests, and
the 499s were the crawler navigating away mid-load.

Defects this procedure found have been fixed with regression tests:
`/health` reported `degraded` after install until a restart
(AUDIT-HEALTH-01); the schema-reference test depended on test order; the
install phase ignored `SMOKE_DB_PORT`/`SMOKE_DB_USER` (SMOKE-DB-01); nginx's
default `keepalive_requests` cut HTTP/2 connections mid-page
(DEPLOY-H2-GOAWAY); and page-boot reads hit the hourly rate limit (RATE-01).
See [AUDIT_REPORT.md](../AUDIT_REPORT.md).

## Browser smoke test

`tools/smoke/browser_smoke.mjs` checks what the server-side tests cannot:
that the pages still work in a real browser under the Content-Security-Policy,
which runs no inline script ([SECURITY.md](SECURITY.md)). Run it against the
same running server as the live smoke test, after `install`:

    npm install --prefix /tmp/smoke-tools puppeteer-core     # once
    NODE_PATH=/tmp/smoke-tools/node_modules CHROME_PATH=/usr/bin/chromium \
      SMOKE_ADMIN_PASSWORD='choose-a-strong-one' \
      SMOKE_BASE_URL=http://localhost:5055 node tools/smoke/browser_smoke.mjs

Any Chrome or Chromium works. Use `localhost`, because browsers keep `Secure`
session cookies there without TLS. Through the HTTPS proxy, pin a
self-signed certificate rather than ignoring errors:
`CHROME_ARGS="--ignore-certificate-errors-spki-list=<base64 SHA-256 of the key>"`
with `SMOKE_BASE_URL=https://localhost`. The script signs in and crawls every
same-origin page linked from the dashboard. It never follows delete, export,
download or logout links. On each page it fails on:

* a CSP violation, or a console message saying the browser refused something;
* an uncaught page error;
* `data-on-*` handlers on a page that did not load the declarative runtime;
* a handler whose function is not reachable from `window` after the page's
  scripts, modules included, have run. Such a control does nothing on click;
* a same-origin response of 400 or above, or a request that never completed
  (`net::ERR_ABORTED` from navigating away is expected and ignored). A script
  that fails to load throws nothing, so this is how a dropped module shows.

It then dispatches real clicks and a failing image to confirm that handlers
run with `this` bound, bubble to ancestors, honour `return false` and run
`data-on-error`. It does not click arbitrary controls, because some delete
data. Exit status is non-zero on any failure.

## OCR tests

OCR depends on the host's engine (Tesseract is a system package), so its
tests are written to hold on every supported engine. They separate:

* **app-level guarantees**, asserted on every engine: the expected words are
  found, confidence is populated and in 0-1, provenance is complete, turned
  text is read, nothing is invented from noise, failures are recorded;
* **engine-specific behaviour**, asserted only where it applies. Exact text
  is compared only where the fixture and engine make it deterministic.

| File | Covers |
|---|---|
| `tests/unit/test_ocr_matrix.py` | The OCR matrix on the real engine: ordinary text with full provenance; high, low (7 px, recovered by the size search) and very large images; rotation 90/180/270; multi-page and mixed PDFs; languages (installed-language filtering, the fallback's limits); blank, solid and noise images; corrupt input; no engine; engine failure and retry; PDF render resolution. |
| `tests/unit/test_ocr_engines.py` | The engine layer with scripted engines: confidence scaling and malformed values, word boxes mapped back through rotation and enlargement, `_reading_score`, the upside-down check, per-language selection, search cost bounds, stored provenance (including `confidence_error` and `missing_languages`). |
| `tests/unit/test_date_and_orientation.py` | Turned text in standalone images and in PDF pages (90/180/270), on the real engine. |
| `tests/unit/test_embedded_images.py` | Images embedded in DOCX files become child objects, are OCR'd, and carry OCR provenance. |
| `tests/unit/test_pdf_text_layer.py` | A scanned PDF page keeps its text layer when OCR cannot run (`text_layer_fallback`, `ocr_required_engine_unavailable`). |
| `tests/unit/test_ocr_selfcheck.py` | The self-check's classifications, its `--require` exit status, and that its remediations name only documented settings. |
| `tests/unit/test_ocr_provenance_docs.py` | [DOMAIN_MODEL.md §1.5](DOMAIN_MODEL.md) lists exactly the OCR provenance fields the pipeline stores. |
| `tests/integration/test_ocr_searchable.py`, `test_provenance_persisted.py`, `test_file_details_api.py` | Against PostgreSQL: OCR'd text is searchable, the provenance row is persisted, and the file details API returns it. |

Run them against each engine you support. PATH decides which Tesseract is
used; without one, RapidOCR is:

    python -m pytest -rA tests/unit/test_ocr_matrix.py tests/unit/test_ocr_engines.py \
        tests/unit/test_ocr_selfcheck.py tests/unit/test_date_and_orientation.py \
        tests/unit/test_embedded_images.py tests/unit/test_ocr_provenance_docs.py \
        tests/unit/test_pdf_text_layer.py tests/integration/test_ocr_searchable.py \
        tests/integration/test_provenance_persisted.py tests/integration/test_file_details_api.py

Results on this release:

| Engine | Passed | Failed | Skipped |
|---|---|---|---|
| Tesseract 5.3.4 | 205 | 0 | 2 |
| Tesseract 5.5.1 | 205 | 0 | 2 |
| RapidOCR only | 207 | 0 | 0 |

The two skips under Tesseract state limits of the RapidOCR fallback; they
run in the RapidOCR configuration.
[AUDIT_REPORT.md](../AUDIT_REPORT.md#final-ocr-matrix) records how each
engine was provided and what the self-check reported on it.

**CI gate.** The CI job installs Ubuntu's `tesseract-ocr` with the `eng`,
`heb` and `ara` packs. Then:

1. It runs `python tools/ci/ocr_selfcheck.py --require tesseract --languages heb,eng,ara`
   *before* the tests. A missing engine or language pack fails the job
   there, with the failing stage and a remediation, instead of as scattered
   test failures.
2. The report is kept as the `ocr-selfcheck` artifact, and each sample
   appears as an annotation.
3. The suite runs with `REQUIRE_POSTGRES=1`.
4. `tools/ci/junit_annotations.py` annotates the totals, every skipped test
   with its reason, and every failure with its message.

## Adding tests

* Put the test in the lowest layer that can prove the behaviour.
* Every bug fix gets a regression test that **fails on the old code**. Check
  this by stashing the fix and running the test.
* Tests must not depend on order. If a test changes shared state (schema,
  settings, environment), restore it or use a dedicated resource.
* Never assert against numbers restated in prose. Assert against the
  generated sources instead.
