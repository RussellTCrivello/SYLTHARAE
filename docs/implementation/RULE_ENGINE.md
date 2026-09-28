# Monitoring rules (step 11)

A monitoring rule watches the stored signals (step 7-9) of the content its
owner may read and turns *new* matches into notifications addressed to the
owner. It extends what exists - the criteria compiler (step 3), the Signal
Explorer filter model (step 10), the `alerts` table and notification service,
the JobManager - and adds no second query language, job framework or
notification store.

| Part | Where |
|---|---|
| Schema | `database/migrations/m0020_monitoring_rules.py` |
| Definition model (strict parser, fingerprint) | `services/monitoring/rule_model.py` |
| Storage, lifecycle, permissions | `services/monitoring/rules.py` |
| Evaluation | `services/monitoring/rule_engine.py` |
| Derived priority | `core/monitoring/priority.py` |
| Addressed notifications | `core/monitoring/notification_service.py` (`recipient_user_id`, `visibility_clause`, `insert_alerts`), `notification_display.py` |
| API | `Api/routes/rules.py`; notification routes filter by recipient |
| Job | JobManager type `rule_evaluation` (`services/jobs/manager.py`) |

## Why the existing notification layer had to change

`alerts` had no recipient: every notification was shown to every user, and
read/dismiss state was shared. A rule matches what *its owner* may read, so
its notifications must reach the owner only. m0020 adds
`alerts.recipient_user_id` (NULL = system-wide, which every existing row
stays) and every read and write path applies
`recipient_user_id IS NULL OR recipient_user_id = <me>` **in SQL**:
the list, single notification, paginated list, stats, upcoming events, mark
read and dismiss. An administrator does not see another user's rule
notifications either. The service's in-memory cache now holds system-wide
notifications only; a user's addressed notifications are read from the
database each time, so one user's volume can never push another user's
notifications out of a shared list.

A rule notification cannot be written without a recipient: `insert_alerts`
refuses it, and the CHECK `ck_alerts_rule_addressed` refuses it in the
database. Downgrading m0020 deletes addressed notifications instead of
turning them into notifications every user would see.

## Definition

The full key reference is in the `rule_model.py` docstring. In short:

```json
{"criteria": {"sources": [3]},                         // Criteria (step 3)
 "signals": {"signal_types": ["date_reference"]},      // SignalFilter (step 10)
 "unit": "signal",                                     // or "content"
 "min_confidence": "medium",
 "event_window_days": {"from": 0, "to": 30},           // relative to the evaluation date
 "threshold": {"count": 3, "window_hours": 24},
 "cooldown_minutes": 60,
 "group_by": "content",                                // none | content | value
 "delivery": {"mode": "digest", "interval_hours": 6},  // or {"mode": "immediate"}
 "notify_existing": false}
```

Every key is validated and unknown keys are refused (`bogus`, `priority`, ...).
A rule needs at least one condition. A rule built from a saved search takes a
**snapshot** of its criteria (`saved_search_id` stays as provenance), so later
edits to the search do not silently change what the rule watches.
`saved_searches.monitor_enabled` (m0016, previously never set) is now derived:
true exactly when an active or paused rule was built from the search, updated
in the same transaction.

**Versions.** Only the semantic fields enter the SHA-256 fingerprint of the
canonical JSON; the name does not. A definition change that alters the
fingerprint creates a new version (history in `monitoring_rule_versions`,
never overwritten). The first evaluation of a new version records a new
baseline.

## Distinct mechanisms

| Mechanism | What it does | Where it is recorded |
|---|---|---|
| Deduplication | a subject is matched once per rule, ever | ledger primary key `(rule_id, subject_key)` |
| Baseline | what already matches when a version starts is not notified (unless `notify_existing`) | ledger state `baseline` |
| Suppression | a temporary state (`suppressed_until`): matches are recorded and **never** notified, even after it ends | ledger state `suppressed` |
| Pause | the rule is not evaluated; matches that appear meanwhile are picked up on resume | rule status `paused` |
| Threshold | notify only when `count` un-notified matches exist (within `window_hours`); older ones expire un-notified | ledger `pending` -> `expired`; `counts.held_by = threshold` |
| Cooldown | hold further notifications for N minutes after one; held matches are delivered afterwards, not lost | `counts.held_by = cooldown` |
| Digest | at most one summary notification per interval | `last_digest_at`, notification `delivery = digest` |
| Grouping | one notification per group (subject, content, or signal value) | notification `group_key` |
| Volume cap | at most 50 notifications per evaluation; beyond that, one explicit overflow summary covers every remaining group | notification `delivery = overflow`, `counts.overflow_subjects` |
| Confidence threshold | `min_confidence`; a signal without recorded confidence never passes | SQL condition |

**Subject identity.** `content_signals.dedup_key` contains the detector
version, so re-detection with a new detector version would have made every
signal "new". The ledger's `subject_key` hashes
`(hash_id, detector, signal_type, value, char_start, char_end, anchor_date)`
(`content:<hash_id>` for `unit = content`). Re-detection that finds the same
thing does not re-notify, which is tested. A detector upgrade that *changes*
a finding (another value or span) produces a new subject, which is a new
finding.

## Authorisation at evaluation time

Each evaluation re-reads the owner from `users`:

- **Owner inactive, or no longer analyst/admin.** The rule becomes `disabled`
  with `disabled_reason` (`owner_inactive` / `owner_role`). The evaluation is
  logged as `owner_revoked`, one `rule_status` notification tells the owner
  why, and nothing is matched or notified. A disabled rule cannot be resumed
  until the owner is eligible again.
- **Otherwise.** The criteria are compiled by the single compiler with
  `scope_for(owner)`. The scope and the owner's role are stored with the
  evaluation.

Today `scope_for` is unrestricted for every role, because the schema has no
per-source access control (CRITERIA_SPINE.md). When one is added it applies
to rules through the same call, with no change here.

Who may do what: owners create/edit (active analyst or admin). Owners and
admins pause/resume/archive/suppress/evaluate. Admins list every rule (`?all=1`)
but cannot change another user's definition. Other users get 404. Rule changes
are written to `audit_log` (`rule.created`, `rule.updated`, `rule.pause`, ...).

## Priority

Priority is derived and nobody can set it (`core/monitoring/priority.py`):
- **high**: a match that is *both* high-confidence *and* imminent (its event
  range overlaps [R, R+7), the Horizon's week band);
- **medium**: otherwise, a high- or medium-confidence match;
- **low**: everything else, including matches whose confidence was not
  recorded.

`critical` is never produced. The decision is made per match, and the basis
is stored with every notification (`metadata.priority_basis`). The same rule
replaces the fixed day bands of the future-date notifications. Those bands
made any date within 8 days CRITICAL whatever the evidence, and no test
pinned them. The 7-day window is a stated product rule, not a literature
threshold.

## Evaluation

One transaction per rule (READ COMMITTED, `statement_timeout` 120 s), with
`pg_try_advisory_xact_lock(0x52554C45, rule_id)`. A concurrent evaluation of
the same rule records `skipped_busy` and returns instead of waiting. The steps
are the owner check, the scope, one set-based `INSERT ... SELECT ... WHERE NOT
EXISTS ... ON CONFLICT DO NOTHING` into the ledger, and then delivery. Delivery
writes the notifications in the same transaction (`insert_alerts`) and marks
the subjects `notified` with their alert id. Any error rolls back the whole
evaluation (no half-delivered state) and a `failed` evaluation is recorded in
a new transaction, with the error. Every evaluation row keeps the rule
version, both fingerprints, the reference date, the trigger, the job id and
per-outcome counts.

**Triggers.**
- `POST /api/rules/<id>/evaluate`;
- `POST /api/rules/evaluate` (admin, all active rules);
- automatically after a JobManager `ingestion`, `batch_import` or
  `signal_redetection` job, only when active rules exist and never for a
  cancelled job. A failure to enqueue becomes a warning on the finished job.

Periodic evaluation is step 20 (scheduling).

## API

See the table in `Api/routes/rules.py`. Errors use the step-10 envelope
`{"success": false, "error": {"code", "message", "request_id"}}` with
400 / 403 / 404 / 409.

## Evidence

| Level | Command | Result |
|---|---|---|
| UNIT-TESTED | `pytest tests/unit/test_rule_model.py` | 37 passed: round trip + fingerprint stability, every semantic field changes the fingerprint, 21 refused definitions, saved-search snapshot, priority table incl. the per-match rule, rule notification rendering, unaddressed write refused |
| POSTGRESQL-VERIFIED | `pytest tests/integration/test_rule_engine_pg.py` | 21 passed on PG 16 with real ingestion: baseline, dedup, no re-notification after a detector-version change, demotion and deactivation disable the rule and notify nothing, scope recorded, criteria restrict, suppression, threshold with expiry, cooldown without loss, digest interval, content grouping, overflow summary, derived priority, SQL/Python imminence agreement, full rollback on a failed delivery, busy lock, versioning + re-baseline, content unit, saved-search flag |
| POSTGRESQL-VERIFIED | `pytest tests/integration/test_migration_upgrade_path.py tests/integration/test_bootstrap_migrations.py` | 0015 -> 0020 upgrade keeps existing alerts system-wide; 6 named CHECKs and the unique name index fire; ledger ON CONFLICT; downgrade deletes addressed alerts only; re-upgrade |
| INTEGRATION-TESTED | `pytest tests/integration/test_rules_api.py` | 5 passed over HTTP: owner-only notifications (list, detail, paginated, stats, read, dismiss) against another analyst and an admin; rule visibility/edit rights; validation, versions, evaluation log, suppression, archive; audit log; job triggers incl. the failure warning |
| RUNTIME-VERIFIED | `python tools/verify/runtime_check_rules.py http://127.0.0.1:5055 /tmp/syltharae_runtime` | 38 checks, 0 failures against the production-mode server and its background job worker; runtime database upgraded 0019 -> 0020 by the production bootstrap |
| Measured | `python tools/perf/rule_engine_perf.py <pg-dir> 20000` | 242 000 signals (202 000 dated): baseline of a broad rule 8.5 s (201 000 ledger rows); steady state with nothing new median 1.7 s (3.0 s before known subjects were filtered ahead of evidence building); 1 000 new signals 1.9 s -> 50 notifications + overflow of 951; narrow rule 22 ms |

## Limitations (not hidden)

- **No rule-management page.** Rules are managed through the API. Rule
  notifications appear on the existing Notifications page (own icons). A UI
  belongs with the dry-run of step 12, which it needs.
- **No dry-run.** Match and notification counts before activation are step 12.
- **Cost scales with matches, not with what is new.** Each evaluation re-reads
  every signal the rule matches and anti-joins the ledger: 1.7 s for a rule
  matching 201 000 signals, evaluated rule by rule. An id watermark would be
  incremental but is unsafe (ids are assigned before commit, so rows can
  commit out of order). A stored subject-key column on `content_signals` would
  remove the per-row hashing; not done. 10x corpus not measured.
- **Only JobManager jobs trigger evaluation.** Content stored by other paths
  waits for the next manual or scheduled evaluation (scheduling: step 20).
- **Resume does not re-baseline.** Matches that appeared while a rule was
  paused or disabled are delivered on resume, bounded by the 50-notification
  cap and the overflow summary.
- **The ledger grows with every match** (retention: step 21).
- **Notification display strings are not in the gettext catalogs.** This
  pre-existing gap covers all types (duplicates, similarity, future dates,
  rules): titles and messages show in English in every language. The
  translation-coverage test does not scan `translate(...)` calls in Python.
- **Legacy getters.** `database/database/repository/alerts_repo.py` and
  `ContentDBService.get_alerts_*` read `alerts` without the recipient filter.
  No route calls them (checked with grep); they are legacy code that must not
  be wired to a route unchanged.
- **Behaviour change.** A database error in mark-read/dismiss now returns 500
  instead of being swallowed into a 404.
