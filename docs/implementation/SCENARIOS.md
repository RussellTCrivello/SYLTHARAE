# Scenarios (step 12)

A **scenario** classifies every document ("content", keyed on `hash_id`) of
its **population** into **outcomes**, through ordered **cases** over named
**conditions**, with a **mandatory default outcome** and an explicit
**strategy**. Outcomes carry **actions** (today: `notify`). A scenario can
only be activated after a **dry-run** of its exact current definition has
passed, and its results are **never overwritten**.

It extends the step-11 monitoring layer rather than adding a second one:
the same owner model and permission checks (`services/monitoring/rules.py`),
the same addressed notifications in `alerts`, the same derived priority
(`core/monitoring/priority.py`), the same job framework (JobManager), the
same criteria compiler (`core/criteria`) and signal filter
(`services/detection/signal_query.SignalFilter`).

| Piece | Where |
|---|---|
| Tables | `database/migrations/m0021_scenarios.py`: `scenarios`, `scenario_versions`, `scenario_evaluations` (evaluations *and* dry-runs, `kind`), append-only `scenario_outcomes`; `alerts.scenario_id`, `alerts.scenario_evaluation_id` |
| Definition | `services/monitoring/scenario_model.py`: strict parser, canonical JSON, SHA-256 fingerprint, warnings |
| Lifecycle, permissions, reads | `services/monitoring/scenarios.py` |
| Decision SQL, evaluation, dry-run, job bodies | `services/monitoring/scenario_engine.py` |
| Jobs | `services/jobs/manager.py`: `scenario_evaluation`, `scenario_dry_run`; enqueued after ingestion / re-detection jobs when an active scenario exists |
| API | `Api/routes/scenarios.py` (table in the module docstring) |
| UI | `/monitoring` (registry `monitoring`, `g m`): `templates/Monitoring/monitoring.html`, `static/js/pages/monitoring-page.js` - scenarios **and** the step-11 rules |
| Notifications | `core/monitoring/notification_service.py` (`scenario_outcome`, `scenario_status`), `notification_display.py`, `notifications-page.js` |

## Why these concepts had no existing home

Inspected before building (see EXECUTION_STATUS step 12): no scenario, case
or outcome model existed; step 11 rules answer "tell me when a new matching
signal appears" - one condition, one delivery, no classification, no
absence, no default, no strategy, no outcome history. Extending the rule
definition with cases would have made every rule a degenerate scenario and
changed the fingerprint of every stored rule; a separate definition that
**reuses the rule parsers** (`parse_signal_filter`, the criteria parser,
`validate_name`, the confidence levels, window limits) was the smaller
change.

## Definition

```json
{"criteria": {"sources": [3]},
 "conditions": {
    "soon":     {"signals": {"signal_types": ["date_reference"]},
                 "min_confidence": "medium", "event_window_days": {"from": 0, "to": 30},
                 "min_count": 1},
    "in_press": {"criteria": {"categories": [12]}}},
 "cases": [
    {"id": "escalate", "when": {"all": ["soon"], "none": ["in_press"]}, "outcome": "review"},
    {"id": "watch",    "when": {"any": ["soon", "in_press"]},           "outcome": "watch"}],
 "outcomes": {"review": {"label": "Needs review", "actions": ["notify"]},
              "watch":  {"label": "Watch",        "actions": []},
              "none":   {"label": "No action",    "actions": []}},
 "default_outcome": "none",
 "strategy": "first_match",
 "notify_existing": false}
```

| Element | Semantics |
|---|---|
| `criteria` | The population: documents with a file occurrence matching the canonical Criteria, compiled by the single compiler **under the owner's access scope**. Empty: every document the owner may read. |
| signal condition | Holds when at least `min_count` of the document's signals match the filter. A signal with **unrecorded confidence never passes** a `min_confidence`; an **undated** signal never matches an `event_window_days` (relative to the evaluation's reference date). "Any signal at all" is refused as a condition. |
| document condition | `{"criteria": {...}}`: holds when the document has an occurrence matching those criteria (same compiler, same scope). |
| `when` | `all`: every listed condition holds; `any`: at least one; `none`: none holds - **absence is explicit**. A case needs at least one listed condition; a case that requires and excludes the same condition is refused ("can never match"). |
| `strategy` | `first_match`: the first matching case in list order decides. `highest_priority`: the matching case with the highest `priority` decides - priorities must be **unique**, so the choice never depends on a hidden tie-break. `all_matching`: every matching case contributes its outcome (a document can hold several). |
| `default_outcome` | **Mandatory**: the outcome of a document no case matches. It may **not** carry actions: the default applies to most of the corpus. |
| case `priority` | Orders cases for `highest_priority` only; refused with other strategies. It is **not** the notification priority. |
| `actions` | `notify`: when a document **enters** the outcome, notify the owner. Every outcome is recorded whatever its actions. |
| `notify_existing` | `false`: what the first evaluation of a version finds is the **baseline** (recorded, not notified). |

Unknown keys are refused, not ignored. Limits: 20 conditions, 30 cases, 20
outcomes, `min_count` <= 10 000, case priority 1-1000, window +/-3650 days.
The fingerprint covers every semantic field; the name is not semantic.
Condition and outcome **key order** does not change the fingerprint; **case
order** does (it is semantic for `first_match`). Tested in
`tests/unit/test_scenario_model.py`.

## Lifecycle

```
draft --dry-run passed + activate--> active <--pause/resume--> paused
  ^                                    |
  +------ definition changed ----------+            (any) --archive--> archived
active --owner lost the privilege--> disabled --resume (owner eligible)--> active
```

* **Activation requires a passed dry-run of the current definition** -
  same fingerprint *and* version, no older than 24 h (`DRY_RUN_MAX_AGE`);
  otherwise `409 DRY_RUN_REQUIRED`. The dry-run id is stored
  (`activated_dry_run_id`); a CHECK makes an active scenario without one
  impossible.
* **A new definition** of an active or paused scenario is a new version and
  returns it to `draft`: the new logic has not been dry-run, so it may not
  notify anyone. Renaming is not a semantic change. Every version is kept in
  `scenario_versions`.
* **Permissions** (as rules): an active analyst or administrator creates
  and edits **their own** scenarios; the owner or an administrator may
  dry-run, activate, pause, resume, archive and evaluate; administrators
  cannot edit another user's definition (403); everyone else gets 404
  (absent and not permitted are indistinguishable). Viewers cannot write.
  Every change is audited (`scenario.created`, `scenario.updated`,
  `scenario.dry_run`, `scenario.activate`, ...).

## Evaluation

One SQL statement (`decision_sql`) decides every document of the
population - there is no per-document or per-case Python loop:
population (`pop`) -> one pass over those documents' signals with a
boolean per condition and FILTER aggregates (`sx`/`sig`) -> one boolean per
condition (`f`) -> one per case (`d`) -> outcomes by strategy with the
evidence of the decisive case(s) (`out`).

An evaluation (`scenario_evaluation` job; `POST /api/scenarios/<id>/evaluate`,
or automatically after ingestion / re-detection jobs) runs in one READ
COMMITTED transaction under a per-scenario advisory lock (a concurrent run
is recorded as `skipped_busy`):

1. **The owner is re-read.** An owner who is inactive or whose role no
   longer allows scenarios **disables** the scenario (`owner_revoked`,
   `disabled_reason`), the owner is told (`scenario_status`), and nothing is
   evaluated or notified.
2. **Outcomes are appended**, never updated: a row only when a document's
   outcome set differs from its latest row, with the matched cases, the
   previous outcomes, the derived priority and its basis, and the evidence.
   Documents that **left the population** get a row returning them to the
   default (`evidence.left_population`). A document with no row has only
   ever had the default. A trigger refuses UPDATE and DELETE; the only
   deletions are FK cascades when the scenario, its owner or the document
   itself is deleted (tested: user deletion and dedup's orphan-content
   deletion).
3. **Notifications** go to the owner only, for documents *entering* an
   outcome with `notify`: at most 49 individual notifications per
   evaluation, highest derived priority first, then **one explicit overflow
   summary** with the count of the rest (`delivery = overflow` on their
   outcome rows). Nothing is dropped silently. The first evaluation of a
   version is the baseline unless `notify_existing`.
4. A failure (SQL error, delivery failure) **rolls the whole evaluation
   back** and is recorded as `failed` with the error.

Each evaluation records the version, owner role, access scope, criteria and
definition fingerprints, reference date and counts.

## Dry-run

`POST /api/scenarios/<id>/dry-run` (`scenario_dry_run` job) runs **the same
statement** as evaluation in a **REPEATABLE READ, READ ONLY** transaction -
so it measures exactly what activation would do, on one consistent snapshot
whose identity (`pg_current_snapshot()`) and time are in the report. It
writes only its own record. The report:

| Directive item | Report field |
|---|---|
| match count | `population`, `case_matches` (per case), `decided_by_case`, `outcomes` (documents per outcome), `non_default_contents`, `would_record` |
| notification count | `notifications_on_activation` (`notifications`, `individual`, `overflow_summary`, `overflow_contents`, `basis`: `baseline` / `nothing_entering` / `notify_existing`), `entering_notify_priorities` |
| categories / sources | `sources`, `categories`, `analyst_categories`: documents with a non-default outcome per source / category, top 20 **with the total and a `truncated` flag** |
| volume | `estimated_volume`: `per_day`, `contents`, `window_days` (30) and its **stated basis** (documents now in a notify outcome whose first occurrence was ingested in the last 30 days, / 30; assumes arrivals continue at that rate) |
| validation | `validation`: `errors` (ids in the definition that do not exist -> status `invalid`, activation refused) and `warnings` (unused conditions, unreachable outcomes, no notify action, cases matching nothing now, empty population, notification cap reached) |

`test_scenario_engine_pg.py` and the live-server check assert that the
dry-run's `notifications_on_activation` equals what the first evaluation
then delivers.

## UI

`/monitoring` has two tabs:

* **Scenarios**: list (status, version, strategy, last evaluation, owner;
  administrators may list every user's), a JSON definition editor with
  **Validate** (canonical fingerprint, errors, warnings) and Save; the
  detail panel shows the latest dry-run report (flagged when it is of an
  older version), the actions the status allows (Dry-run, Activate, Pause,
  Resume, Evaluate now, Archive), the paged outcome history (with previous
  outcome, cases, delivery, priority, a link to a readable occurrence) and
  the evaluation log. Server errors are shown verbatim with their code
  (e.g. `DRY_RUN_REQUIRED`).
* **Rules** (the step-11 API, which had no UI): list, JSON editor, Evaluate
  now, Suppress 1 hour, Pause, Resume, Archive, evaluation log.

Write controls are hidden from viewers as a convenience; the API refuses
their writes regardless. User-entered names are isolated with `dir="auto"`.
Every string is in the ar/he/fa/hr catalogs (the template is on the
translation coverage audit).

## Decisions taken without the concept document

The Intelligence/Detection/Notification concept document was not in the
repository (EXECUTION_STATUS step 1). These are decisions, stated so they
can be reviewed, not inferred requirements:

1. The scenario **unit is the document** (`hash_id`), not the signal:
   outcomes classify documents; signals are evidence.
2. `all_matching` gives a document **several outcomes**; `first_match` and
   `highest_priority` exactly one.
3. **Suppression, cooldown, thresholds, grouping and digests do not apply to
   scenarios** (they are rule delivery mechanisms). Scenario notification
   volume is bounded by "notify on *entering* an outcome" and the per-
   evaluation cap with overflow. If scenarios need them, they belong in a
   later version of the definition.
4. **Only `notify`** exists as an action. Other actions (e-mail, webhooks,
   tagging) were not specified and are not invented.
5. A dry-run older than **24 h** no longer permits activation.
6. The default outcome **cannot notify**.
7. Case priorities must be **unique** under `highest_priority`.
8. **Child categories are not expanded** (as everywhere in the criteria
   model; open question carried from step 3).

## Evidence

| What | Result |
|---|---|
| `tests/unit/test_scenario_model.py` | 13 passed: canonical round trip, fingerprint stability and sensitivity, mandatory silent default, three strategies, unique priorities, case/condition/outcome references, strict conditions, unknown keys, warnings, helper errors surfaced as scenario errors |
| `tests/integration/test_scenario_engine_pg.py` | 22 passed on PostgreSQL 16.2 through the real ingestion path: each strategy, document conditions and `any`, dry-run writes nothing, notify_existing counts, unknown ids -> invalid + activation blocked, activation freshness, edit returns to draft, drafts never evaluated, baseline then notification with provenance, appended changes with previous outcome, leaving the population, append-only trigger, owner and content deletion cascades, owner demotion disables, overflow cap, SQL priority = Python rule, rollback on failed delivery, failed dry-run recorded, job body |
| `tests/integration/test_scenarios_api.py` | 7 passed over HTTP with real logins: activation gate, owner-only notifications (another analyst and an admin), visibility/edit rights, validation, audit trail, post-ingestion trigger contract, page contract per role |
| `tests/unit/test_monitoring_page.py` | 2 passed: the editors' starter definitions parse |
| `tests/integration/test_migration_upgrade_path.py` | m0021 on an upgraded database: every named CHECK, append-only trigger, downgrade of 0021 alone removes scenario alerts and keeps rule alerts, re-upgrade |
| `tools/verify/runtime_check_scenarios.py` | 63 checks, 0 failures against the live production-mode server (its job worker included) |
| `tools/verify/monitoring_page_runtime.mjs` | 31 checks, 0 failures: the shipped page module against the live server in the DOM stub |
| `tools/perf/scenario_engine_perf.py` | 20 000 documents, 243 000 signals: broad dry-run 713 ms; baseline evaluation 1.04 s; steady state median 795 ms; 1 000 documents changing outcome 933 ms (49 notifications + 1 overflow summary for 951); narrow (one source) dry-run 42 ms, steady state 47 ms |

## Limitations (not hidden)

* **Cost scales with the population**: every evaluation re-decides the whole
  population in one statement (0.8 s for 20 000 documents). There is no
  incremental evaluation; a 10x corpus was not measured.
* Only JobManager jobs (ingestion, re-detection) trigger evaluations;
  there is no scheduler yet (step 20).
* The definition editor is JSON; there is no form-based case builder.
* The UI was verified in a DOM stub against the live server, not in a real
  browser (the Playwright download is blocked here); layout and real bidi
  rendering are unverified.
* The ar/he/fa/hr translations were written for this step and have not been
  reviewed by native speakers.
* Outcome history grows without bound until retention (step 21).
* Dry-run volume is an estimate with a stated basis, not a forecast.
* API lists use `items` for scenario pages while the step-11 rules
  evaluation log uses `evaluations` (published contract, left unchanged).
