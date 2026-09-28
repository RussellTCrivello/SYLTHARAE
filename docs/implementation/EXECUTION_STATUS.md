# Execution status (Master Execution, Remediation & Verification Directive)

"Done" means: code, integration, migration executed on PostgreSQL 16, API
where needed, authorization, tests, full regression, documentation,
failure behaviour verified, limitations written down, no competing
implementation left. Anything short of that is "Partial" or "Not started".

Verification environment: Python 3.11, PostgreSQL 16.2 (`pgserver`),
no Tesseract. Full regression command:

```
python -m pytest -p no:cacheprovider -o addopts="" -q -rf tests
```

## Steps

| # | Step | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Repository state audit | Done | [STATE_INVENTORY.md](STATE_INVENTORY.md) |
| 2 | Migration-number reconciliation | Done | m0016 saved_searches, m0017 content_signals; `test_migration_sequence.py`; fresh + upgrade-from-0015 + downgrade + re-upgrade on PG (`test_migration_upgrade_path.py`, `test_bootstrap_migrations.py`) |
| 3 | Phase 0 criteria model + compiler | Done for search, saved searches, exports | [CRITERIA_SPINE.md](CRITERIA_SPINE.md); monitor/report/dashboard consumers must use it when built |
| 4 | Saved searches to PostgreSQL | Done | `test_saved_searches_pg.py`; `run_url` in the saved-search page template still to be re-verified |
| 5 | `DATA_EXPORTED` audit event | Done for search exports | `test_disclosure_audit.py` |
| 6 | PostgreSQL migration verification | Done for 0016, 0017 | constraints, indexes, duplicate/NULL/ON CONFLICT/rollback/cascade tests on PG |
| 7 | Phase 1 multilingual detection | Done, with listed limitations | [TEMPORAL_SIGNALS.md](TEMPORAL_SIGNALS.md); `test_temporal_intel.py` (73) |
| 8 | Ingestion / re-detection integration | Done | `test_signal_ingestion.py` (20), `test_signal_notifications.py` (6) |
| 9 | Phase 2 gazetteer | Not started | must extend m0015 `path_geo_mentions` / `geo_gazetteer.py` |
| 10 | Horizon + Signal Explorer | Not started | API for it exists (`GET /api/content/<id>/signals`) |
| 11 | Rule engine + notifications | Not started | future-date notifications already read stored signals |
| 12 | Scenarios | Not started | |
| 13-15 | Report registry, runner, artifacts | Not started | |
| 16 | Analytics engine + narrative | Not started | |
| 17 | Report catalog | Not started | |
| 18 | Multilingual rendering | Not started | |
| 19 | `report_baselines` | Not started | |
| 20 | Scheduling | Not started | |
| 21 | Retention | Not started | |
| 22 | Reports dashboard | Not started | |
| 23 | Comprehensive report | Not started | |
| 24-28 | Regression, security, performance, packaging/offline audits, acceptance | Ongoing per phase; final audits not started | |

## Regression record

| Point | Passed | Failed | Skipped | Failure set |
| --- | --- | --- | --- | --- |
| Baseline (`0521aa5`) | 2 743 | 35 | 97 | 29 OCR + 5 stale generated docs + 1 licensing |
| After Phase 0 (`a6060b5`) | not recorded | 30 | 97 | baseline minus the 5 stale-doc failures |
| After Phase 1 | 2 848 | 30 | 97 | identical to Phase 0: 29 OCR (no Tesseract) + select2 licensing |

No failure was introduced. The generated references (`DATABASE_SCHEMA.md`,
`HTTP_ROUTES.md`, `endpoint_inventory.json`, `REGISTRY_EVIDENCE.md`,
python API pages) were regenerated with the repository's own tools for the
new migration and routes.

## Pre-existing defects fixed along the way

| Defect | Fix | Test |
| --- | --- | --- |
| Saved searches by id readable/editable/deletable by any user (IDOR) | owner check, 404 | `test_saved_searches_pg.py` |
| A real database error in any "optional" ingestion step rolled back the whole document (savepoint re-raised `TransactionAbortedError` after a successful rollback) | `ContainedStatementError` once the savepoint is restored | `test_a_database_error_in_the_raw_text_step_is_contained` |
| Notification settings read through a non-existent `get_setting()`; the exception was swallowed so the toggles never applied | `settings.get(category, key, default)` | `test_signal_notifications.py` (future-date gate), `notification_integration.py` corrected |
| Future dates estimated from the wall clock, English only | module removed; stored signals | `test_signal_notifications.py` |
