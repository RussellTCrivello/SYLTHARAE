# Report runs (step 14)

A run executes one registered report definition (`core/reporting`, step 13)
for one requester, as a background job, and records what was computed, for
whom, from which data. This step stores the datasets and shows them; files made
from a completed run (formats, manifest, SHA-256, `DATA_EXPORTED`) are step
15, described in [REPORT_ARTIFACTS.md](REPORT_ARTIFACTS.md).

## Why these pieces, and not new ones

| Need | Existing infrastructure used | Why it was enough / what was added |
| --- | --- | --- |
| Background execution, progress, cancellation, stale recovery | `services/jobs/manager.py` (JobManager) | New built-in job type `report_run`, dispatched like `scenario_dry_run`. No second job framework. |
| One consistent read | The pattern of `scenario_engine.dry_run` / `signal_query` (`REPEATABLE READ, READ ONLY`, `pg_current_snapshot()`) | Same pattern; the whole run (every dataset) is one transaction. |
| SQL, parameters, access scope | `Dataset.bind` + the criteria compiler (step 3/13) | The runner executes exactly the bound query; nothing is composed here. |
| Requester eligibility | `users.role` / `is_active`, re-read like `owner_eligibility` | Re-read inside the snapshot. |
| Saved searches | `saved_searches` + `can_read` (step 4) | Criteria can be taken from one; the run keeps `saved_search_id`. |
| Audit | `get_auth_service().audit` | `report.run` on submission. |

New: migration `m0022_report_runs`, `services/reporting/runs.py`,
`Api/routes/reports.py`, the `/reports` page.

## Tables (m0022)

`report_runs`: `report_id`, `report_version`, `definition_fingerprint` (the
lock-pinned fingerprint at submission), `parameters` (normalised JSON) and
`parameters_fingerprint`, `criteria_fingerprint`, `saved_search_id`,
`requested_by` / `requester_username` / `requester_role` (role at submission;
re-checked at execution), `job_id`, `status`
(`queued`/`running`/`completed`/`failed`/`refused`/`cancelled`),
`refusal_reason`, `error`, `snapshot` (`pg_current_snapshot()::text`),
`snapshot_at`, `isolation_level`, `generator_version` (`report-runner/1`),
`requested_at`/`started_at`/`finished_at`.

`report_run_datasets`: per dataset `dataset_key`, `dataset_fingerprint`,
`query_fingerprint` (SHA-256 of the executed SQL text and bound values),
`semantics`, `row_limit`, `row_count`, `truncated`, `columns` (declared name,
type, nullability, label), `rows` (JSON array).

Constraints (all checked by name in `test_migration_upgrade_path.py`):
a completed run names its snapshot and times; `refused` iff a reason;
`failed` needs an error; fingerprints are 64 lowercase hex; `exact` datasets
are never `truncated`; `row_count <= row_limit`; the stored array length
equals `row_count`. Triggers: a terminal run cannot change and results are
written once. The one accepted change to a terminal run is
`requested_by`/`saved_search_id` becoming NULL when the user or saved search
is deleted (`ON DELETE SET NULL`); the username stays. That exception was
found by the migration test: without it, deleting a user with finished runs
failed.

## Execution

1. **Submit** (`submit_run`, request thread). Lookup by id (and version, or
   the active one). The role must be allowed by the report and every dataset;
   otherwise 404, the same as absent. Parameters are normalised by the
   definition (unknown names refused). Every dataset is bound once (pure) so
   an invalid request fails here with 400 and no job exists. Then the
   `queued` row is written, `report.run` is audited and the job is created.
   If the job cannot be created the run becomes `failed` with that reason.
2. **Execute** (`execute_run` in the job). `queued -> running` (the job id is
   recorded). The definition fingerprint must still equal the recorded one
   (a deployment between submit and run fails the run instead of computing
   something else). One transaction, `REPEATABLE READ, READ ONLY`,
   `statement_timeout` 120 s: record the snapshot; re-read the requester
   (deleted / inactive / role no longer allowed -> `refused`, nothing read);
   for each dataset bind with the requester's scope, check the criteria
   fingerprint against the recorded one, execute, fetch at most
   `row_limit + 1` rows, verify the cursor's columns are exactly the declared
   ones and that non-nullable columns have values; `exact` overflow fails the
   run (no partial result is stored), `capped`/`top_n` overflow keeps
   `row_limit` rows and sets `truncated`. The results are written in a second
   transaction, once. Cancellation is checked between datasets.
3. **Read**. A run is visible to its requester and administrators while the
   reader's role is still allowed by the report (a retired definition's runs:
   administrators only). A run left `queued`/`running` by a job that ended
   without recording a result (process death) is reconciled to `failed` when
   read. Rows are paged in SQL from the stored array (`limit` <= 500).

Values are stored as JSON: dates and timestamps as ISO 8601, `numeric` as its
exact decimal string, NULL as JSON null (the page shows "none"; an empty
string stays empty).

## API

| Route | Who | Notes |
| --- | --- | --- |
| `GET /reports` | any signed-in user | the page |
| `GET /api/reports/definitions` | any signed-in user | reports the role may read; parameters, datasets (semantics, limit, columns), unit, help, `can_run` |
| `POST /api/reports/runs` | analyst, admin | `{report_id, version?, parameters?, saved_search_id?}`; 202 + job; 400 invalid, 403 viewer, 404 unknown/not permitted, 500 job not created (run marked failed) |
| `GET /api/reports/runs` | any signed-in user | own runs; `?all=1` administrators only (403 otherwise); `report_id`, `status`, `limit` <= 200, `offset`; each item carries its dataset summaries |
| `GET /api/reports/runs/<id>` | requester, admin | run and dataset summaries |
| `GET /api/reports/runs/<id>/datasets/<key>` | requester, admin | a page of rows |

## Page (`/reports`, registry `reports`, shortcut `g r`)

Report picker with unit, datasets and their limit semantics; parameter
inputs generated from the definition (criteria: a saved search or JSON);
run history with server-side status filter and, for administrators, all
users; run detail with the outcome (completed with snapshot time, failed with
the server's error, refused with the reason, cancelled), provenance
(fingerprints, parameters, snapshot, isolation, generator, job, times, query
fingerprints), a dataset selector, the declared column labels, completeness
or shortening stated, paged rows. Everything is inserted as text
(`tests/js/reports_page_smoke.mjs`: no `innerHTML` assignment at all). Strings
in en/ar/he/fa/hr.

## Decision needed: can viewers run reports?

`search_results@1` declares `viewer` among its roles (step 13). The platform
policy SEC-02 (`core/security/flask_ext.py`) makes every mutating request
analyst/admin, and states that endpoints may tighten but never loosen it.
Creating a run is a write, so the policy was **not** loosened: viewers see
the report (`can_run: false`), the page tells them they cannot run it, and
`POST /api/reports/runs` answers 403 (checked in the service too). If viewers
should run reports, the product owner must decide to add the route to
`AUTH_ANY_USER_WRITE_PATHS`; the runner's own checks would then apply as they
do for analysts.

## Evidence

| What | Command / file | Result |
| --- | --- | --- |
| Migration on PostgreSQL: upgrade path, every CHECK by name, triggers, user and saved-search deletion, downgrade alone, re-apply | `tests/integration/test_migration_upgrade_path.py::test_m0022_*`, `test_bootstrap_migrations.py` | passed |
| Runner on PostgreSQL (12) | `tests/integration/test_report_runs_pg.py` | passed; the one-snapshot test fails when the isolation is changed to READ COMMITTED (mutation checked) |
| HTTP (8) | `tests/integration/test_reports_api.py` | passed |
| Page module (16 checks) | `tests/js/reports_page_smoke.mjs` via `tests/unit/test_reports_page_js.py` | passed |
| Live server, fresh `runtime_provision.py` database (applies 0001-0022) | `tools/verify/runtime_check_reports.py` | 25 checks, 0 failures |
| Shipped page module against the live server | `tools/verify/reports_page_runtime.mjs` | 25 checks, 0 failures |
| Audit rows on the live database | `SELECT ... FROM audit_log WHERE action = 'report.run'` | one row per submitted run |
| Performance, 20,000 files | `tools/perf/report_run_perf.py /tmp/perfpg 20000` | broad run (5,000 stored, truncated, exact count 20,000) median 98 ms; narrow 26 ms; 100-row page 2.0-2.2 ms at start and end; listing stored in 52 KiB |

The live server was started without `config.json`; the setup gate
(`.system_initialized`) was created for the run as the setup wizard would
and removed afterwards. Start-up on a clean clone is step 27.

## Limitations

- No real browser (Playwright download blocked): layout, CSS and real bidi
  rendering of `/reports` are not verified; the page runs in a DOM stub.
- Translations are not reviewed by native speakers.
- Only `search_results@1` is registered; the other catalog reports need their
  datasets and analytics (steps 16-17).
- Artifacts and downloads: see [REPORT_ARTIFACTS.md](REPORT_ARTIFACTS.md)
  (step 15). No scheduling (20); no retention of runs (21): rows are kept
  until then.
- Stored rows are bounded by each dataset's `row_limit` (at most 100,000 by
  the model's ceiling); a large capped dataset is stored in full up to that
  limit.
- A retried `report_run` job (Job Center "retry") fails with "not queued":
  a run is executed once; request a new run instead.
- Rejected submissions (400/403/404) are not audited as runs (no run exists);
  they are visible in the server log only.
