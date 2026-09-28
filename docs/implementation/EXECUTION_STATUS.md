# Execution status - implementation ledger

Governed by the *Master Implementation Execution Directive*. Each row's
status is the highest rung of the evidence ladder that has actually been
reached, backed by a command or test that anyone can re-run:

`CLAIMED -> INSPECTED -> UNIT-TESTED -> INTEGRATION-TESTED ->
POSTGRESQL-VERIFIED -> RUNTIME-VERIFIED -> ACCEPTED`

* **POSTGRESQL-VERIFIED**: the tests go through the real Flask app (test
  client) and real migrations on PostgreSQL 16.2, not mocks.
* **RUNTIME-VERIFIED**: exercised against a running server process. **No
  row has reached it yet.**
* **ACCEPTED**: requires the step 28 end-to-end acceptance. **No row has
  reached it yet.**
* **NOT STARTED**: no code exists for that step.

Environment: Python 3.11, PostgreSQL 16.2 (`pgserver`), no Tesseract.
Regression command:

```
python -m pytest -p no:cacheprovider -o addopts="" -q -rf tests
```

## Ledger

| # | Item | Status | Evidence (re-runnable) | Code / migration | Known limitations | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Repo state audit | INSPECTED | [STATE_INVENTORY.md](STATE_INVENTORY.md); commits `a6060b5`, `2a50e24` and the Phase 0/1 test files re-run under this directive: 180 passed | - | - | re-audit before Phase 2 |
| 2 | Migration-number reconciliation | POSTGRESQL-VERIFIED | `test_migration_sequence.py` (2); `test_bootstrap_migrations.py` (6); `test_migration_upgrade_path.py` (4): fresh, upgrade from 0015, downgrade newest-first, re-upgrade | m0016, m0017, m0018; one number each | - | the next migration is m0019 |
| 3 | Criteria model + compiler | POSTGRESQL-VERIFIED for search, saved searches and search export | `test_criteria_spine.py` (36), `test_criteria_spine_pg.py` (14) | `core/criteria/` | `include_child_categories` is refused because `categorys` has no hierarchy (**open question**). Monitor/report/dashboard do not use it yet because they are not built | rules and reports must compile through it |
| 4 | Saved searches in PostgreSQL | POSTGRESQL-VERIFIED | `test_saved_searches_pg.py` (14), including owner isolation (IDOR fix) and the page's Run URL | m0016, `saved_searches_repository.py` | the legacy JSON file is imported idempotently once per process (`ON CONFLICT (legacy_source, legacy_id) DO NOTHING`); unmappable entries are kept with `import_notes`, not dropped | - |
| 5 | `DATA_EXPORTED` on every export | POSTGRESQL-VERIFIED | `test_disclosure_audit.py` (7): every export route's record, actor and SHA-256 are checked; fail-closed 503; `test_original_file_view.py` (19) | `core/security/disclosure.py`; 13 routes declare kind/scope/format/row_count | backup `row_count` is null with a reason (the service does not count). An inline preview is not a disclosure (documented in `SECURITY.md`) | - |
| 6 | PG migration verification | POSTGRESQL-VERIFIED for m0016-m0018 | constraints fire by name; duplicate, NULL, ON CONFLICT, rollback, cascade and downgrade tests in the step 2/4/8 files | - | - | repeat for every new migration |
| 7 | Phase 1 detection | UNIT-TESTED + POSTGRESQL-VERIFIED (storage) | `test_temporal_intel.py` (84): en/ar/he/fa/hr, three calendars, digits, orientation, relative expressions, Croatian ordinals, per-call clock, confidence rules, verbatim sentence, truncation. Storage: `test_signal_ingestion.py` (20) | `core/detection/`, detector `temporal-1.1.0`, m0017 + m0018 | confidence is ordinal and uncalibrated. Pre-1.1 rows have NULL provenance until re-detected. The day-first convention is per document script. More in [TEMPORAL_SIGNALS.md](TEMPORAL_SIGNALS.md) | - |
| 8 | Ingestion / re-detection | POSTGRESQL-VERIFIED | `test_signal_ingestion.py` (20): ingest step 10, contained failure, redetect scopes, signals API authz; stored sentence equals the stored text slice | `services/detection/`, `contents_db_service.py` step 10, `Api/routes/signals.py` | not runtime-verified against a live server and real files | runtime check in step 24 |
| 8a | Future-date notifications from stored signals | POSTGRESQL-VERIFIED | `test_signal_notifications.py` (6) + notification accuracy/volume guards (16): metadata carries method, confidence and sentence | `notification_service.py`, `Api/routes/notifications.py` | stats/upcoming use the local date while the scan uses UTC. No rule engine yet: every future date is notified, subject only to the existing toggles | replaced by the step 11 rule engine |
| 9 | Phase 2 DB gazetteer + place signals | POSTGRESQL-VERIFIED | `test_place_intel.py` (47): normalisation, scripts, exact/uppercase/possessive, Hebrew prefixes, Arabic proclitics, Croatian inflection, ambiguity kept (Tripoli, Georgia, الجزائر, عمان), homographs low, offsets into the original, all 119 legacy names still detected, `build_seed.py --check`. `test_places_pg.py` (20): seed load + fingerprint from stored rows, idempotent sync, retire-not-delete, gazetteer change -> new detector version -> stale re-detection converges, named CHECKs, ingestion offsets, place failure recorded without blocking temporal, `path_geo_mentions` view (legacy rows until superseded; ambiguous/low excluded), signals/redetect/places APIs incl. 400/401/404, scan no longer overwrites `paths.coordinates`. `test_migration_upgrade_path.py` (5): 0015->0019 with legacy geo rows, downgrade restores the table and CHECK | `database/migrations/m0019_gazetteer.py`, `data/gazetteer/` (Wikidata CC0, [SOURCES.md](../../data/gazetteer/SOURCES.md)), `core/geo/names.py`, `core/detection/place_intel.py`, `services/geo/gazetteer.py`, `services/detection/detectors.py`, `Api/routes/places.py`; `Api/services/geo_gazetteer.py` deleted | 223 places only; gazetteer matching, not NER; no context disambiguation (homographs curated); small Croatian paradigm; multi-word possessives missed; romanised endonyms classified exonym; 17 places `unclassified`; scan endpoint contract changed (job, no coordinates). [PLACE_SIGNALS.md](PLACE_SIGNALS.md) | runtime check in step 24; Signal Explorer (step 10) |
| 10 | Horizon + Signal Explorer | NOT STARTED | the read API exists (`GET /api/content/<id>/signals`) | - | - | after 9 |
| 11 | Rule engine | NOT STARTED | - | - | - | - |
| 12 | Scenarios + dry-run | NOT STARTED | - | - | - | - |
| 13 | Report registry | NOT STARTED | - | - | - | - |
| 14 | Runner (JobManager, REPEATABLE READ) | NOT STARTED | - | - | - | - |
| 15 | Artifacts + manifest, datasets | NOT STARTED | - | - | - | - |
| 16 | Analytics + narrative | NOT STARTED | - | - | - | - |
| 17 | Report catalog | NOT STARTED | - | - | - | - |
| 18 | Multilingual rendering / formats | NOT STARTED | - | - | - | - |
| 19 | `report_baselines`, Latest/Change | NOT STARTED | - | - | - | - |
| 20 | Scheduling | NOT STARTED | - | - | - | - |
| 21 | Retention | NOT STARTED | - | - | - | - |
| 22 | Reports dashboard | NOT STARTED | - | - | - | - |
| 23 | Comprehensive report | NOT STARTED | - | - | - | - |
| 24 | Full regression | Run for each commit (table below); the final run is pending | - | - | 30 pre-existing failures | - |
| 25-28 | Security, performance, packaging/offline, acceptance audits | NOT STARTED | - | - | - | - |

## Regression record

| Point | Passed | Failed | Skipped | Failure set |
| --- | --- | --- | --- | --- |
| Baseline (`0521aa5`) | 2 743 | 35 | 97 | 29 OCR + 5 stale generated docs + 1 licensing |
| Phase 0 (`a6060b5`) | not recorded | 30 | 97 | baseline minus the 5 stale-doc failures |
| Phase 1 (`2a50e24`) | 2 848 | 30 | 97 | 29 OCR (no Tesseract) + select2 licensing |
| Disclosure + provenance (`294104e`) | 2 863 | 30 | 97 | identical to Phase 1 (compared with `comm`) |
| Phase 2 gazetteer / place signals | 2 931 | 30 | 97 | identical to Phase 1 (compared with `comm`) |

On the `294104e` run a 31st failure first appeared:
`test_screen_inspector.py::...test_the_document_is_what_the_product_now_says`.
It was caused by this work: the edit to `Api/blueprints/files.py` moved a
line that `docs/SCREEN_INSPECTOR_EVIDENCE.md` cites. The doc was regenerated
with the repository's own command (`python -m core.experience.audit
docs/SCREEN_INSPECTOR_EVIDENCE.md`); the diff is that one line number. The
test was not changed.

The first Phase 2 run had 32 failures. Both extra failures came from this work:
`test_reference_docs_are_current` because generated reference docs were
stale (fixed by regenerating with `tools/docs/generate_reference.py`), and
`test_stale_places_are_redetected_when_the_gazetteer_version_changes`, which
passed alone but failed in the full run. Diagnosis: in the shared test
database, other modules' content had never had a temporal run, so a stale
pass over all detectors correctly processed it (0 failures, and a third pass
converged to 0). The test asserted convergence one pass too early. It now
asserts that the second pass re-runs no place detection and fails nothing,
and that the next pass processes 0 - stricter than before, and independent of
test order.

## Pre-existing defects fixed

| Defect | Fix | Test |
| --- | --- | --- |
| Any user could read, edit or delete a saved search by id (IDOR) | owner check, 404 | `test_saved_searches_pg.py` |
| A DB error in an optional ingestion step rolled back the whole document | `ContainedStatementError` after savepoint restore | `test_a_database_error_in_the_raw_text_step_is_contained` |
| Evidence-sentence lookup was a linear scan per signal (O(signals x sentences)); 15 s for a dense 1 MB document | `textspan.SentenceIndex` (binary search), shared by both detectors: 2.1-2.5 s | `tools/perf/detector_throughput.py`; detector tests unchanged |
| The geolocation scan overwrote `paths.coordinates` (file GPS) with the most-mentioned place, and resolved ambiguous names to one place | scan delegates to the places detector; coordinates untouched; ambiguity stored | `test_legacy_scan_endpoint_delegates_and_never_writes_coordinates` |
| Notification settings were read through a non-existent `get_setting()`; the error was swallowed | `settings.get(category, key, default)` | `test_signal_notifications.py` |
| Future dates were estimated from the wall clock, English only | removed; stored signals | `test_signal_notifications.py` |
| 12 export routes wrote `DATA_EXPORTED` without scope, format or row count; `/api/settings/export` wrote none | per-route `note_disclosure`, `force` for the JSON body | `test_every_export_route_writes_a_complete_record` |

## Open questions for the owner

1. **Child categories.** Criteria accept `include_child_categories`, but
   `categorys(id, word_id)` has no parent column. It is refused, not
   ignored. Should a hierarchy be added (a migration), or should the option
   be removed from the contract?
