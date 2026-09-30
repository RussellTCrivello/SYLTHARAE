# Repository state inventory (directive step 1)

Audited on the working branch, starting from `main` at `0521aa5`
(migrations up to `m0015`). "Existing" is what the repository contained
before this work; "Now" is the state after the commits listed in
[EXECUTION_STATUS.md](EXECUTION_STATUS.md). Every "Now" entry cites its
verification.

| Area | Existing (at 0521aa5) | Required by the concepts | Now | Status |
| --- | --- | --- | --- | --- |
| Search | `Api/services/search_service.py` with private filter SQL; advanced search, boolean expressions, categories, analyst categories | One criteria model and one parameterised, permission-aware compiler reused everywhere | `core/criteria/` (model, fingerprint, compiler); search uses the same predicate builders; parity on PostgreSQL (`test_criteria_spine_pg.py`, 14) | Done (Phase 0) |
| Saved searches | `data/saved_searches.json`; by-id routes let any user read/rename/delete any search | PostgreSQL, owned, fingerprinted, monitor-capable | m0016 `saved_searches`, lossless idempotent import, ownership (404), fingerprint (`test_saved_searches_pg.py`, 13) | Done; monitoring evaluation is Phase 3 |
| Detection | `core/monitoring/future_events.py`: English only, `date.today()` at construction, relative dates estimated from the clock, nothing stored | Multilingual en/ar/he/fa/hr, three calendars, digits, orientation, evidence, versioned, stored per content | `core/detection/`, m0017, ingestion step 10, re-detection job, API; legacy analyzer removed ([TEMPORAL_SIGNALS.md](TEMPORAL_SIGNALS.md)) | Done (Phase 1), limitations listed |
| Places | Static English gazetteer `Api/services/geo_gazetteer.py` (119 places), `path_geo_mentions` (m0015), case-sensitive literal match; scan overwrote `paths.coordinates` (GPS) | DB multilingual gazetteer (endonym/exonym/historical/variant), Hebrew prefixes, ambiguity kept | m0019 gazetteer (223 places, 1,153 names, Wikidata CC0); `places` detector on the Phase 1 signal spine (ingestion, re-detection, API); m0015 table kept behind the `path_geo_mentions` view; static list deleted; scan no longer writes coordinates (`PLACE_SIGNALS.md`) | Extended (m0015 superseded in place, not duplicated) |
| Notifications | `alerts` table, `NotificationService` (in-memory queue + batch flush), duplicate and future-date scans, per-file analyze | Rule engine: dedup, cooldown, grouping, suppression, thresholds, digest, computed priority, authz at evaluation | Future dates now come from stored signals; everything else unchanged | Partial - rule engine not started (Phase 3) |
| Jobs | `services/jobs/manager.py` JobManager (persisted, events, cancel/pause, synchronous mode for tests) | Reports and re-detection run on the existing framework | `signal_redetection` job type added (`test_redetect_api_is_admin_only_validated_and_uses_the_job_manager`) | Reused |
| Reporting | none (no registry, runner, artifacts) | Registry, runner (`job_type="report"`, one REPEATABLE READ snapshot), manifests, catalog of 10 reports | none | Not started (steps 13-15, 17, 23) |
| Analytics | `Api/routes/analytics.py`: path analysis and classification pages only; no keyness/TF-IDF/collocation/kappa code | Analytics engine + deterministic 5-voice narrative | none | Not started (step 16) |
| Exports | Search exports (CSV/XLSX/JSON...) without a disclosure record | `DATA_EXPORTED` audit with actor, criteria fingerprint, format, scope, count, artifact SHA-256; fail closed | `core/security/disclosure.py`, `audit_strict`, 503 on audit failure (`test_disclosure_audit.py`, 6) | Done for search exports |
| Scheduling | `job_schedules` (m0031), `services/scheduling/` (model/store/scheduler thread), `/schedules` page + `/api/schedules` (report_run / rule_evaluation / scenario_evaluation on an interval, fired through the JobManager; every fire re-checks the owner and the payload, at-most-once claims, no burst on resume or downtime) | Scheduled reports/monitors | m0031 | UNIT+PG+HTTP-VERIFIED (step 20, [SCHEDULING.md](SCHEDULING.md)) |
| Retention | `services/retention/` (policy table + batched, audited apply), 9 settings `retention.<area>_days` (0 = keep forever), `/retention` page + `/api/retention` (admin), `retention` schedule type (m0032), JobManager `retention` job | Scheduled pruning of every growing area: jobs, rule ledger/evaluations, scenario outcomes, notifications, report runs/artifacts, path revisions, audit log | m0032 | UNIT+PG+HTTP-VERIFIED (step 21, [RETENTION.md](RETENTION.md)) |
| UI | Server-rendered pages + JS; settings page; notifications page | Signal Explorer, Horizon; Reports Dashboard (`/reports/dashboard`: measured overview of the catalog, runs, artifacts and schedules + the step-18 language picker on artifact requests) | Settings: obsolete future-events toggle removed | Signal Explorer done (step 10); dashboard done (step 22, [REPORTS_DASHBOARD.md](REPORTS_DASHBOARD.md)); sidebar static across navigations + per-user order/visibility from Settings (owner request, D11/D12: m0033 + `/api/preferences/navigation` + navigation-swap.js) |
| Security | Middleware default-deny: GET = authenticated, mutations = analyst/admin, admin blueprints; CSRF; rate limits; audit log | Authz on every new surface; scope enforced in SQL | Saved-search IDOR fixed; signal reads scope-checked in SQL; re-detection admin-only | Maintained |
| Tests | 2 743 passed / 35 failed / 97 skipped at baseline | Regression proof per phase | 2 848 passed / 30 failed / 97 skipped; failures identical to baseline minus 5 fixed stale-doc tests | See EXECUTION_STATUS.md |

## Migration sequence

`m0001`-`m0015` released. This work adds `m0016_saved_searches` and
`m0017_content_signals`. The concept documents each proposed a *different*
`m0016` (`m0016_content_signals`, `m0016_reporting`); neither was created
under that number. `tests/unit/test_migration_sequence.py` fails on a
duplicate, a gap or a filename/version mismatch, and every migration from
0016 on must ship a `downgrade`.

## Pre-existing failures (not introduced by this work)

* 29 OCR tests - no Tesseract in the verification environment
  (`test_ocr_engines`, `test_ocr_matrix`, `test_ocr_selfcheck`,
  `test_pdf_text_layer`, `test_embedded_images`, `test_date_and_orientation`).
* `test_licensing.py::test_every_vendored_file_is_in_the_notices` -
  `static/dist/{js,css}/select2.min.*` are listed in
  `THIRD_PARTY_NOTICES.md` but were never committed.

(The first report counted 28 OCR failures; the per-file count is 29. The
failure set itself has been identical at every comparison.)

## Found during the audit, recorded for later steps

* `Api/` has no `__init__.py`, so `setuptools` package discovery in
  `pyproject.toml` omits `Api.*` from a wheel (step 27, packaging audit).
* `data/settings.json` (gitignored) points at a stale test database; it
  only produces harmless connection-pool errors in test logs.
* `core/monitoring/notification_integration.py` has no callers.
