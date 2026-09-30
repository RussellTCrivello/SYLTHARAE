# Scheduling (step 20)

What was built, what it guarantees, and what it does not do yet.

## What a schedule is

A `job_schedules` row (migration **m0031**; m0032 added `retention`)
says: *run this job every N minutes*. Four job types are schedulable,
each mapping onto an **existing JobManager job** — the scheduler creates
no second job framework, no second queue, no second execution path:

| `schedule_type`        | Job it enqueues       | Who may own one   | What one fire does                                             |
|------------------------|-----------------------|-------------------|----------------------------------------------------------------|
| `report_run`           | `report_run`          | analyst, admin    | `runs.submit_run` **as the schedule's owner** (same validation, same visibility, same audit trail as a manual run), then one `report_run` job |
| `rule_evaluation`      | `rule_evaluation`     | admin             | evaluates every active monitoring rule (`trigger='schedule'`)  |
| `scenario_evaluation`  | `scenario_evaluation` | admin             | evaluates every active scenario (`trigger='schedule'`)         |
| `retention` (step 21)  | `retention`           | admin             | applies every enabled retention policy — batched, counted, audited ([RETENTION.md](RETENTION.md)) |

The `report_run` payload (`report_id`, `version`, `parameters`) is
validated against the report registry **at creation and edit time** — an
unknown report/version or invalid parameters are refused, never stored —
and **again at every fire**.

## Management (fully UI-manageable)

The **Schedules page** (`/schedules`, registry interface `schedules`)
manages everything through the unified table component: create, edit,
pause, resume, **run now**, delete, and the per-schedule job history.
Administrators additionally see every user's schedules (`All users`
switch). Rules and scenarios are managed on `/monitoring` (step 12);
schedules join them as fully manageable configuration — nothing about a
schedule is a static file or a code-only constant.

API (all audited, error envelope as elsewhere):

```
GET    /api/schedules            your schedules (?all=1: administrators)
POST   /api/schedules            {schedule_type, name, interval_minutes, payload}
GET    /api/schedules/<id>       one (owner or administrator; else 404)
PUT    /api/schedules/<id>       name / interval / payload / enabled
DELETE /api/schedules/<id>       delete
POST   /api/schedules/<id>/pause      stop firing (reason 'paused …')
POST   /api/schedules/<id>/resume     fire again one interval from now
POST   /api/schedules/<id>/run_now    fire immediately, cadence unchanged
GET    /api/schedules/<id>/jobs       the jobs this schedule created
```

## Authorization is re-decided at every fire

The hard rule: **nothing runs as somebody who may no longer run it.**

* The owner's current `role` and `is_active` are read from the database at
  each fire (the same eligibility read the rule engine uses for its own
  owners). A demoted or deactivated owner's schedule is **disabled with the
  reason stated** (`owner_role`, `owner_inactive`) and no job is created.
* A `report_run` fire goes through `runs.submit_run` as the owner — the
  same path as the API — so the run re-checks the role, the registry and
  the parameters. A payload that no longer validates (report retired,
  parameters changed meaning) disables the schedule
  (`definition_invalid`) instead of churning failed runs.
* An owner who lost the privilege cannot re-arm the schedule either: the
  resume route refuses (409) unless the owner is eligible again or an
  administrator resumes.
* Fires and auto-disables are audited (`schedule.fired`,
  `schedule.disabled`, username `scheduler`); every management change is
  audited with the acting user (`schedule.created/updated/deleted/paused/
  resumed/run_now`).

## Timing model: interval, at-most-once, no burst

* `interval_minutes` between 5 and 525 600 (a year), checked by
  `ck_job_schedules_interval`.
* Creation sets the first fire **one interval ahead** — never immediately.
* The tick claims due schedules with one `UPDATE … FOR UPDATE SKIP LOCKED`
  statement that advances `next_run_at` by each row's own interval and
  commits (at-most-once). A crash between claim and enqueue costs one fire;
  a long downtime fires **once**, never once per missed interval.
* Resume moves the next fire a full interval ahead and resets the failure
  count — a long pause never fires a burst.
* Each tick also copies the last job's status onto the schedule (one
  set-based statement); a `FAILED` job counts one `consecutive_failures`,
  any other terminal status resets it. Transient enqueue failures are
  recorded (`last_status='enqueue_failed'`) and the next interval retries.

## Runtime

One daemon thread in the web process (the JobManager's single-process
model: exactly one process per database). `run_web.py` enables it by
default (`SYLTHARAE_SCHEDULER=0` opts out); tests and import-only
processes never grow the thread by accident. The tick interval is
`SCHEDULER_TICK_SECONDS` (default 15) and up to `SCHEDULER_CLAIM_LIMIT`
(default 5) schedules fire per tick.

## Evidence

| Level | Command | Result |
|---|---|---|
| UNIT-TESTED | `pytest tests/unit/test_schedule_model.py` | 11 passed: the three types map to existing jobs, roles per type, interval bounds, payload strictness (unknown report/version/parameters refused with the registry's reason), evaluation payloads carry nothing, owner eligibility incl. deactivation, descriptions |
| POSTGRESQL-VERIFIED | `pytest tests/integration/test_schedules_pg.py` | 14 passed on a real database: CHECKs and the unique (owner, name) index fire **by name**; first fire one interval ahead; pause/resume semantics; owner-deletion cascade; the claim is at-most-once and moves the interval (a second claim finds nothing); paused schedules are never claimed; job statuses reflected with `consecutive_failures` counted and reset; a report fire is a real run **as the owner** (completed through the synchronous JobManager, run requested by the owner); demoted owner → disabled `owner_role`, no job, audited; deactivated owner → `owner_inactive`; a payload that no longer validates → `definition_invalid`; a rule fire carries `trigger='schedule'` and the schedule id |
| INTEGRATION-TESTED | `pytest tests/integration/test_schedules_api.py` | 4 passed over HTTP: full lifecycle (create → list scoping → edit → run now with a synchronous job → pause → resume → run now → delete) with audit rows; evaluation schedules administrative (analyst 403, admin 201); the page renders with the management dialog for analysts/admins and without it for viewers (API refuses their writes anyway); unknown fields and a below-bounds interval refused 400 |

## Limitations (not hidden)

* **Interval-only.** No cron expressions and no "daily at 09:00"; an
  interval of 1440 minutes approximates daily. A wall-clock schedule needs
  a timezone policy (whose midnight?) and is deliberately not guessed at.
* **No notification on auto-disable.** A schedule disabled by owner
  demotion is stated on the page (`disabled_reason`) and audited, but the
  owner is not notified the way a rule owner is. The rule path notifies
  because notifications are that subsystem's output; for schedules it is
  future work, not an omission hidden in the code.
* **No per-schedule artifact.** A scheduled `report_run` produces the run;
  rendering an artifact on schedule (and its retention) is the Reports
  Dashboard / retention steps (22 / 21).
* **Failure counting is informational.** `consecutive_failures` is shown
  and reset, but no automatic disable-on-N-failures exists — a permanently
  broken payload is caught at fire time, and a flaky environment should
  keep retrying.
* **The scheduler thread is single-process.** The JobManager already
  requires exactly one web process per database; the claim statement is
  `SKIP LOCKED`, so a second process would not double-fire, but the
  deployment rule does not change.
