# Retention (step 21)

Retention prunes what grows: finished jobs and their event streams, the
rule engine's deduplication memory and evaluation log, scenario outcome
history, notifications, report runs with their stored datasets and
artifacts, the revision log behind the Change report, and the audit log.
The directive for this step is exact: *"Retention — prune aged
jobs/runs/artifacts/notifications/ledger/audit with explicit, audited
policy; settings-declared days; never delete silently."*

## The policy table

One declarative policy per growing area, in
`services/retention/model.py` (`POLICIES`). Each names its table, the
timestamp that decides its age, the status guard that keeps *live* rows
out, a default, and hard bounds:

| Area | Table | Age column | Guard | Default |
| --- | --- | --- | --- | --- |
| `jobs` | `jobs` | `created_at` | status not QUEUED/RUNNING/PAUSED/CANCELLING | 90 days |
| `rule_ledger` | `rule_subject_ledger` | `first_matched_at` | - | 180 days |
| `rule_evaluations` | `rule_evaluations` | `evaluated_at` | - | 180 days |
| `scenario_outcomes` | `scenario_outcomes` | `recorded_at` | - | **keep forever** |
| `notifications` | `alerts` | `created_at` | - | **keep forever** |
| `report_artifacts` | `report_artifacts` | `created_at` | - | **keep forever** |
| `report_runs` | `report_runs` | `finished_at` | status completed/failed/refused/cancelled **and** `finished_at` not null | 365 days |
| `path_revisions` | `path_revisions` | `changed_at` | - | **keep forever** |
| `audit_log` | `audit_log` | `created_at` | - | **keep forever** |

Decisions worth stating:

* **`0` days means keep forever** - an explicit, visible value, never the
  absence of one, and never "not measured = zero". The default for every
  area whose deletion would destroy evidence: append-only scenario
  outcomes, notifications (read or unread), the artifact bytes and
  manifests (their verifiability is gone with them), the revision log
  that explains old changes, and the audit log itself.
* **A run that never finished is state to reconcile, not litter** - the
  `report_runs` guard keeps unfinished and running rows whatever their
  age. The same logic keeps queued/running/paused/cancelling jobs.
* **The ledger prune is a re-baseline**: after `rule_subject_ledger`
  rows are pruned, subjects that still match are treated as first
  matches again and notified again. That consequence is stated on the
  page's edit dialog (`deletes` text), not discovered.
* Bounds: `0`, or 1..3650 days (`MAX_DAYS`). Booleans are refused
  (`isinstance(True, int)` is a Python trap, not a policy).

## Settings-declared days

Each policy's days live in the settings file as
`retention.<area>_days`, declared in `settings/settings_models.py`
(`RetentionSettings` category + `SETTING_DEFINITIONS`, INTEGER, 0..3650,
defaults matching `POLICIES`). The settings definition validates at the
UI edge; the model re-validates at the service edge - a hand-edited
settings file cannot smuggle in a policy (`effective_days` falls back to
the default for anything that is not an int).

## Never delete silently

* Every applied area writes exactly one `retention.applied` audit row:
  actor, `resource = retention:<area>`, detail `{days, deleted, batches}`.
  Even a zero-deletion apply is recorded.
* Every policy change writes `retention.policy_changed`
  (`{area, days}`), and every run-now writes `retention.run_requested`
  (`{area, job_id}`).
* The `/retention` page shows, per area, what the policy deletes
  (including the consequences: artifact bytes and manifests gone; ledger
  re-baseline), how many rows it would delete *right now* (a live COUNT,
  not an assumption), the table size, and the oldest row's timestamp.

## How deletion runs

`services/retention/service.py`:

* set-based, bounded work only: each area deletes in batches of 5 000
  rows picked by `ctid` (no primary-key assumption on any table), one
  transaction per batch, committed per batch - a stopped run leaves the
  earlier batches applied and the audit row states exactly how far it
  got;
* statement timeouts: 30 s for the COUNT/overview reads, 120 s per
  delete batch;
* cancellation is checked before every batch (the job's
  `cancellation_requested`), and progress is reported per batch;
* the deletion predicate is always
  `age_column < NOW() - make_interval(days => %s)` plus the status
  guard - one shape, reviewed once, table/columns/guard are fixed
  literals from `POLICIES`, days is a bound parameter.

## Scheduling and authorization

`retention` is a fourth schedule type (m0032 widens
`ck_job_schedules_type`): an administrator creates a retention schedule
on the `/schedules` page and it fires through the JobManager like any
report or evaluation - which means **authorization is re-decided at
every fire** by the step-20 scheduler (a demoted or deactivated owner's
schedule is disabled with a stated reason and no job is created), and
claims are at-most-once.

The job body (`JobManager` job type `retention`) applies every enabled
area, honouring cancellation between areas and batches. Run-now from the
`/retention` page creates the same job (`source = retention:manual`),
so both paths produce jobs, events, and the same audit rows.

m0032's downgrade disables retention schedules with a stated reason and
restores a three-type check `NOT VALID` - existing rows survive, the old
schema simply refuses *new* retention schedules. (The first draft
re-added a validating check, which fails whenever any retention schedule
exists; the PG test caught it before it could ship.)

## Management (fully UI-manageable)

`/retention` (registry `retention`, Domain ADMINISTRATION,
`operate/retention` help topic, administrators only) - the same unified
table component as every other list:

* one row per area: area, what it is, effective days (`keep forever`
  badge for 0), would-delete-now count, rows in table, oldest;
* header-driven sorting with the asc→desc→default cycle, column
  visibility and filtering from the column headers;
* Edit opens the policy dialog: the consequence text, the days input
  (0..3650), server-side validation with the refusal message shown;
* Run now per enabled row, and "Run enabled policies" for all - each
  behind a confirmation that says deletion is audited; the fired job is
  reported (status line: the job is on the Jobs page);
* retention schedules are created/edited/run/deleted on `/schedules`
  like any other schedule (the "What runs" dropdown gained
  *Prune by the retention policies*, administrators only).

## Evidence

* `tests/unit/test_retention_model.py` (23): the policy table's defaults
  and guards (evidence-destroying areas default to keep forever), bounds
  (0 / 1..3650, booleans and strings refused), effective-days fallbacks,
  the settings declarations match `POLICIES` (defaults and bounds),
  retention is a schedulable administrative type, `configured_days`
  reads the stored setting and cannot be smuggled.
* `tests/integration/test_retention_pg.py` (17, PostgreSQL): the m0032
  constraint by name (accepts `retention`, refuses `everything`);
  old finished jobs deleted **with their job_events cascade**, live jobs
  (all four live statuses) survive at 400 days; old terminal report runs
  deleted with their artifacts, running/unfinished runs survive;
  keep-forever deletes nothing even at 4 000 days and the overview shows
  zero eligible; batched deletion counted (5 rows / batch 2 = 3 batches);
  the ledger pruned by its own policy; aged evaluations pruned and the
  ledger cascading; the overview answers for every area (eligible, total,
  oldest measured); one `retention.applied` audit row per apply with
  actor/days/deleted/batches; a fired retention schedule produces exactly
  one synchronous `retention` job that prunes and records
  `last_job_id`; the downgrade disables with a reason, refuses new
  retention schedules, and the upgrade re-widens.
* `tests/integration/test_retention_api.py` (4, HTTP): viewer 403 on the
  page and every API; admin overview covers all areas; a days change is
  validated (400 with `VALIDATION_FAILED` for 4000/-1/bool/string/missing/
  unknown area), audited (`retention.policy_changed` with the actor), and
  effective in the next overview; run-now creates the real job (synchronous
  completion, `options.areas`/`options.actor`), prunes the aged job,
  spares the running one, and audits both the request and the applied
  deletion; unknown-area run refused 400.
* Route reference: 421 routes (`/retention` page, `/api/retention`
  overview/PUT/run).

## Limitations (not hidden)

* Retention deletes rows, not files on disk - the areas in scope are all
  database tables; uploaded originals are out of scope (no policy
  deletes evidence files).
* The audit row counts rows, not bytes; artifact deletion's *consequence*
  (verifiability gone) is stated in text and audited, but the freed
  storage is not metered.
* A keep-forever area is visible but never auto-pruned; if an
  installation sets a finite days on, say, the audit log, that is an
  explicit, audited policy change - the tool does not second-guess it.
* Notifications pruned per policy are gone for every recipient (the
  `alerts` table is shared); there is no per-recipient retention.
* Statement timeout on a batch (120 s) fails the job honestly; earlier
  batches stay applied and are counted by the audit row. There is no
  automatic retry.
* The overview's counts are live COUNTs per request; on very large
  tables the page load carries that cost (30 s timeout → honest 503-style
  failure, not a silent zero).
