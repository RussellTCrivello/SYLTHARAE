# Repository State Audit — 2026-09-30

This is a re-audit of the actual `arena/01a0f34c-syltharae` checkout before
starting the current navigation/table work. Sections 1–6 are the initial
snapshot, taken before the test toolchain was provisioned. **Section 7 records
follow-up execution evidence that supersedes the initial environment blockers**;
it does not upgrade the broader application directive to complete.

## 1. Repository and Git state

- Required branch: `arena/01a0f34c-syltharae`.
- Starting `HEAD`: `320bd0844635ad445aaeb2151df956c2c0cca2d3`, the PR #4 merge
  commit (the local history is grafted/shallow; only that commit was visible to
  `git log` during the audit).
- The starting worktree and index were clean (`git status --short --branch`,
  `git diff --stat`, and `git diff --cached --stat`).
- No files were changed until the source inventory and test/tool availability
  checks below were complete.

## 2. Inventory and evidence ladder

| Area | Inspected state in this checkout | Verification available now | Status |
| --- | --- | --- | --- |
| Criteria/query spine | `core/criteria/` and search integration are present; PostgreSQL integration test `tests/integration/test_criteria_spine_pg.py` exists. | The recorded ledger previously reports PostgreSQL verification; it was not re-run in this sandbox. | INSPECTED; historical PostgreSQL claim not revalidated |
| Saved searches / exports | Migration `m0016_saved_searches.py`, repository/API, disclosure audit service and export routes/tests exist. | No pytest or PostgreSQL tools available. | INSPECTED; historical PostgreSQL claim not revalidated |
| Detection / signal chain | Migrations `m0017_content_signals.py` and `m0018_signal_provenance.py`, detection, ingestion, signal and place services/routes exist. | Unit/integration test files exist, but pytest/PostgreSQL are unavailable. | INSPECTED; historical results not revalidated |
| Gazetteer / places | `m0019_gazetteer.py`, geographic services, `/api/places`, Settings UI and PostgreSQL CRUD tests exist. | No PostgreSQL server/client or pytest in this sandbox. | INSPECTED; historical PostgreSQL claim not revalidated |
| Monitoring / scenarios | `m0020`/`m0021`, rules/scenarios services/routes, visual monitoring page and tests exist. | Runtime/browser/DB tests unavailable here. | INSPECTED; historical results not revalidated |
| Reporting / analytics | Reporting registry/runs/artifacts, analytics, schedules and retention code and migrations through `m0032` are present. | Relevant tests and verification scripts exist; no pytest/PostgreSQL available. | INSPECTED; historical results not revalidated |
| Sidebar preferences | `m0033_user_navigation_prefs.py`, service, authenticated API, settings UI, and registry-backed sidebar exist. | Standalone JS table test runs; browser and PostgreSQL verification unavailable. | INSPECTED; current defect found and addressed in this work |
| Tables and list UIs | `components/table.html`, `unified-table.js`, file-library wrapper, list templates, and progressive pagination exist. Seven principal record lists use the shared `record_table`; several other screens use `data_table` or specialized views. | `node tests/js/unified_table_smoke.mjs`: 33/33 after this change. Full Python/browser coverage unavailable. | PARTIAL; not one universal behavior surface across every requested interface |
| Security | Authentication/authorization middleware, route-level policies, disclosure auditing and security tests are present. | Security tests were not run because pytest is missing; no live DB to test authorization data scopes. | INSPECTED; SECURITY VERIFIED is not claimed for this re-audit |
| Documentation | Architecture, database, testing, interface registry, phase ledger and table-gap notes exist. | Documentation was inspected; historical status rows are not equivalent to current rerun evidence. | INSPECTED |

## 3. Migration inventory

The checkout contains migration modules `m0001_initial_schema.py` through
`m0033_user_navigation_prefs.py`. A direct filename audit confirmed 33
contiguous module numbers (`0001..0033`). Importing the runner to inspect
migration objects could not run because `psycopg2` is not installed.
`database/migration_runner.py` discovers versions and fails on duplicate
versions; `tests/unit/test_migration_sequence.py` checks contiguous numbering
and downgrade functions for later migrations.
The navigation preferences schema has a per-user/interface primary key, a
positive-position check, and a cascading user foreign key.

**Database state is not verified in this audit.** No `DB_*`, `DATABASE_URL`, or
`PG*` environment variables were present; `psql`, `postgres`, `pg_ctl`, and
`pg_isready` were unavailable. Migrations were not executed. Existing ledger
claims for migration testing remain historical evidence, not a new execution
result.

## 4. Test baseline and environment blockers

Commands executed:

- `python -m pytest -q -o addopts='' tests/unit/test_navigation_prefs.py tests/integration/test_navigation_prefs_api.py tests/unit/test_progressive_tables.py tests/js/unified_table_smoke.mjs` — **not run**; `/usr/bin/python: No module named pytest`.
- `node tools/smoke/navigation_smoke.mjs` — **not run**; Node cannot resolve
  `puppeteer-core`; no Chromium executable was found.
- `python` import of `database.migration_runner.discover_migrations()` —
  **blocked** because `psycopg2` is not installed; the independent filename
  audit confirmed the 33 migration filenames are contiguous.
- `node tests/js/unified_table_smoke.mjs` — **passed, 33/33** after navigation
  routing assertions were added.
- `node tests/js/action_toolbar_smoke.mjs` — **passed, 80/80**; its harness
  intentionally exercises expected error paths and prints diagnostic traces.
- `node --check` on every modified JavaScript file — **passed**.
- `git diff --check` — **passed**.

The historical full-suite result in `EXECUTION_STATUS.md` is recorded there as
prior evidence. It is not represented here as a new baseline or regression run.

## 5. Discrepancies discovered in the navigation claims

The existing sidebar link interceptor and stable `#sidebar` shell were present,
but the claim that navigation no longer refreshes the shell was incomplete:

1. `unified-table.js` used `location.assign()` for server-side header sorting
   and column filtering. Those frequent table interactions bypassed the shell
   swap.
2. `file-nav.js` used `location.assign()` for Alt+Left/Alt+Right file movement.
3. Seven list templates still used `location.reload()` for their Refresh
   buttons; file copy/rename and three modal success paths had similar reload
   fallbacks.
4. A failed navigation fetch forced a full-page navigation instead of leaving
   an in-content failure/retry state.
5. Sidebar preferences were persisted by Settings, but the UI said they would
   apply on the next navigation. Since normal route swaps intentionally retain
   the sidebar, saved ordering/visibility did not update the currently mounted
   sidebar.
6. The settings editor presented a flat list even though the sidebar is
   domain-grouped and the server only reorders entries within a domain. Up/Down
   could appear to cross group boundaries while the sidebar could not.

These are source-level findings. Browser-level proof is blocked until the
browser smoke dependencies and an authenticated running server are available.
This change is not a claim that every native navigation in the repository is
gone: login/logout, language-switch reloads, downloads, legacy file-card
navigation, and compatibility fallbacks still need classification against the
full route inventory. They must not be presented as browser-verified here.

## 6. First incomplete implementation stage

The core intelligence/reporting sequence has broad implementation and
historical verification records, but the current sandbox cannot revalidate its
PostgreSQL/runtime acceptance. For the user's current request, the first
concrete defect was the shell navigation bypass described above; the first
implementation step was to route those actions through the shared content-swap
lifecycle and make sidebar preference changes visible immediately without
replacing the outer sidebar node.

The initial snapshot could not verify browser behavior. Follow-up browser tests
now verify navigation and selected export workflows; see §7. Continue the
unified table backlog in dependency order. Column-selectable export and both
content/original ZIP export have been demonstrated in a browser for the tested
File Library/file-type paths, but not across every applicable interface. Do not
infer full acceptance of continuous browsing, all sorting/filtering/visibility,
or common file operations across every result/list surface from a shared table
macro or the tested flows alone.

## 7. Follow-up verification (2026-09-30)

The test dependencies were available in the checkout's `.venv`, and browser
dependencies were available under `/tmp`. A disposable Flask app on port 5055
used a temporary PostgreSQL cluster and data directory outside the repository;
bootstrap applied **33 migrations**. Test credentials and synthetic documents
were outside the checkout. The app and cluster were stopped after the browser
runs; no production database or user data was touched.

### Current execution evidence

| Area | Executed verification | Result / boundary |
| --- | --- | --- |
| Migration bootstrap | Fresh disposable PostgreSQL-backed Flask bootstrap | 33 migrations applied; not a production-database upgrade claim |
| Sidebar/navigation | `tools/smoke/navigation_smoke.mjs` in Chromium | ALL PASS: client navigation/history/back, failed-navigation retry, stable sidebar element and scroll, 32 sidebar links, preference reorder/save/reload/reset, jobs-page lifecycle, and page-2 progressive load without reload; zero page/console errors after excluding only the smoke's deliberate aborted-fetch messages |
| Action lifecycle | `REQUIRE_POSTGRES=1 ./.venv/bin/pytest -q tests/unit/test_action_lifecycle_components.py tests/unit/test_progressive_tables.py` | 52 passed; the action harness was invoked by its test wrapper with rendered confirmation-dialog HTML (the earlier bare invocation without `--dialog-html` was not a test) |
| Table export in browser | Chromium on `/files`; selected only `name,id`, mocked the native save handle, clicked CSV, and inspected actual returned data | PASS: authenticated request to `/api/export/file_library`, selected columns and chosen filename `reader-selected-columns.csv`, page/per-page parameters omitted, returned CSV includes file name/id and excludes unselected path, zero console errors. Repeated pre-final smoke attempts caused repeated equivalent test audit rows; the successful trace is the acceptance evidence. |
| Content/original exports | Chromium on `/files/types`; opened a type's shared documents panel, selected a row, chose names in the archive dialog, exported text and originals | PASS: both authenticated POSTs returned 200, `X-Export-Mode` was correct, included=1/skipped=0, filenames respected, and ZIP payloads were opened and compared to the seeded text/original bytes; zero page/console errors. This also exposed and fixed a panel-only `updateBulkToolbar()` reference; the page-only handler is now omitted from panel rows and regression-asserted. |
| Audit / authorization | Latest `tests/security` batch; live browser-generated `audit_log` read-back on the structured export | 131 security tests passed. Browser exports created `DATA_EXPORTED` rows with filename, scope, format, row count and measured SHA-256. The targeted browser traces did not exhaust every interface or authorization role. |
| Other UI smoke tests | 11 standalone `tests/js/*_smoke.mjs` scripts (excluding the action harness, now covered above) | All passed; includes 80 action-toolbar checks, 61 content formatter, 54 declarative-event, 33 unified-table, 21 file selection and 39 report-page checks. |
| Regression batches | `REQUIRE_POSTGRES=1 ./.venv/bin/pytest -q tests/unit`, `tests/integration`, `tests/security`, and `tests/e2e`, run as separate commands on the current tree | All four commands exited 0. Collection totals plus observed skip markers: **4,127 passed, 3 skipped, 0 failed** (2,943 unit / 2 skipped; 1,034 integration / 1 skipped; 131 security; 22 E2E). The full integration run includes the new archive-page test. This is a set of separate completed suite runs, not a single omnibus invocation. |

### What remains unverified

- D1 is **PARTIAL**, not accepted: browser evidence covers the shared table export
  on `/files` and the text/original batch export in one file-type panel, plus
  direct database/audit test coverage. Browser flows remain to be exercised for
  search results, keyword/category panels, alternate formats and export failure
  behavior across interfaces.
- D2–D10 remain in dependency order; the recent export runs do not prove all
  interfaces use one authoritative table/file-operation path or satisfy every
  copy/rename/open/highlight requirement.
- The existing 16-link intelligence-chain evidence and the new navigation/
  export browser evidence are separate. No complete end-to-end browser workflow
  spanning criteria → ingestion/signals → monitoring/reports/artifacts/export/
  audit across every authorized interface was run in this follow-up.
- Performance, packaging, multilingual screen rendering, failure/retry behavior
  outside the exercised paths, and broader role-by-role authorization still
  require the remaining ordered acceptance work.

## 8. Later intelligence-chain and regression recheck (2026-09-30 local)

The previously recorded chain script was not valid notification evidence: its
rule event was dated outside the evaluation window and the NOTIFY link passed
unconditionally. After confirming that the live PostgreSQL run had zero alerts,
`tools/verify/final_chain_acceptance.py` was hardened to create a future event
inside a 30-day window, run `term_keyness@2` on persisted selected/reference
documents, require a completed analysis with nonzero measured corpora and terms,
verify the report's stored target path, and validate job/run completion,
artifact digests, downloaded bytes, and the persisted disclosure-audit detail.
Rule acceptance now requires a completed evaluation with new subjects plus an
alert linked to the authenticated recipient, rule/evaluation, target content
hash and evidence sentence; content-level alerts correctly have no `file_id`.

The first strengthened rerun on the reused PostgreSQL cluster passed
**16/16 links**, but was not treated as clean-database evidence. After the
script also began checking artifact metadata/verification HTTP status and the
audit actor's user ID, a new disposable app was started and bootstrapped from
scratch with **33 migrations**. Only the temporary admin existed before the
chain. This clean run passed **16/16 links**: one selected content, one
reference content, five returned keyness terms, one completed evaluation, one
persisted alert linked to the authenticated recipient/rule/evaluation and target
hash, a report dataset containing the target path, a verified HTML artifact,
a matching downloaded SHA-256, and a read-back `DATA_EXPORTED` audit record.
All synthetic data and credentials were outside the checkout. The disposable
app and cluster were stopped after the run.

Current regression checks were separate executions:

- `tests/unit` excluding six OCR-engine-dependent files: **2,761 passed, 2
  skipped**. The OCR tests cannot be completed here because Tesseract is absent
  and RapidOCR cannot load `libGL.so.1`. `pyzipper` was installed into the
  disposable `.venv` after the initial run exposed that missing test-only
  dependency; it is not a repository dependency change.
- `tests/integration`: **949 passed, 85 skipped**; `tests/security`: **131
  passed**; `tests/e2e`: **22 passed**.
- Eleven standalone JavaScript smoke scripts passed, including 33/33 unified
  table and 80/80 action-toolbar checks. The rendered action-lifecycle wrapper
  passed 52 tests.
- An initial unfiltered unit run had 52 failures: OCR/environment failures and
  22 UI/table/record assertions that were absent in the no-OCR full rerun and
  focused UI run. This order-sensitive difference has not been explained; do
  not call the unfiltered unit suite accepted until it is triaged.
- A new browser run was blocked: Chromium is not present in the sandbox. The
  temporary `puppeteer-core` install succeeded, but browser download failed with
  TLS `ECONNRESET` from `googlechromelabs.github.io`. Historical browser checks
  remain historical; search-results and keyword/category panel exports and
  failure paths are still not browser-verified.

Overall remains **IN PROGRESS**. D1 is partial; D2-D10 and wider authorization,
multilingual rendering, performance, packaging and end-to-end UI checks remain
open. No completion claim is made.
