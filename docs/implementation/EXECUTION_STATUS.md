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
| 6 | PG migration verification | POSTGRESQL-VERIFIED for m0016-m0021 (bootstrap and tests); **server start-up upgrade path fixed after step 12** (it never ran without the marker file) - `test_startup_upgrade_path.py` (6) and a `run_web.py` control run | constraints fire by name; duplicate, NULL, ON CONFLICT, rollback, cascade and downgrade tests in the step 2/4/8 files | - | - | repeat for every new migration |
| 7 | Phase 1 detection | UNIT-TESTED + POSTGRESQL-VERIFIED (storage) | `test_temporal_intel.py` (84): en/ar/he/fa/hr, three calendars, digits, orientation, relative expressions, Croatian ordinals, per-call clock, confidence rules, verbatim sentence, truncation. Storage: `test_signal_ingestion.py` (20) | `core/detection/`, detector `temporal-1.1.0`, m0017 + m0018 | confidence is ordinal and uncalibrated. Pre-1.1 rows have NULL provenance until re-detected. The day-first convention is per document script. More in [TEMPORAL_SIGNALS.md](TEMPORAL_SIGNALS.md) | - |
| 8 | Ingestion / re-detection | POSTGRESQL-VERIFIED | `test_signal_ingestion.py` (20): ingest step 10, contained failure, redetect scopes, signals API authz; stored sentence equals the stored text slice | `services/detection/`, `contents_db_service.py` step 10, `Api/routes/signals.py` | not runtime-verified against a live server and real files | runtime check in step 24 |
| 8a | Future-date notifications from stored signals | POSTGRESQL-VERIFIED | `test_signal_notifications.py` (6) + notification accuracy/volume guards (16): metadata carries method, confidence and sentence | `notification_service.py`, `Api/routes/notifications.py` | stats/upcoming use the local date while the scan uses UTC. No rule engine yet: every future date is notified, subject only to the existing toggles | replaced by the step 11 rule engine |
| 9 | Phase 2 DB gazetteer + place signals | POSTGRESQL-VERIFIED (**re-established after a packaging defect**: until the startup-fix commit, `data/gazetteer/` was git-ignored and never committed, so m0019 failed on every clone; the earlier verification held only in the build sandbox. The seed was rebuilt from committed inputs (`2026-09-28.2`: 223 places, 1 155 names; the lost `.1` had 1 153), see *Defects found in the field*) | `test_place_intel.py` (47): normalisation, scripts, exact/uppercase/possessive, Hebrew prefixes, Arabic proclitics, Croatian inflection, ambiguity kept (Tripoli, Georgia, الجزائر, عمان), homographs low, offsets into the original, all 119 legacy names still detected, `build_seed.py --check`; `test_gazetteer_packaging.py` (2): seed and inputs exist and are not git-ignored. `test_places_pg.py` (20): seed load + fingerprint from stored rows, idempotent sync, retire-not-delete, gazetteer change -> new detector version -> stale re-detection converges, named CHECKs, ingestion offsets, place failure recorded without blocking temporal, `path_geo_mentions` view (legacy rows until superseded; ambiguous/low excluded), signals/redetect/places APIs incl. 400/401/404, scan no longer overwrites `paths.coordinates`. `test_migration_upgrade_path.py` (5): 0015->0019 with legacy geo rows, downgrade restores the table and CHECK | `database/migrations/m0019_gazetteer.py`, `data/gazetteer/` (Wikidata CC0, [SOURCES.md](../../data/gazetteer/SOURCES.md)), `core/geo/names.py`, `core/detection/place_intel.py`, `services/geo/gazetteer.py`, `services/detection/detectors.py`, `Api/routes/places.py`; `Api/services/geo_gazetteer.py` deleted | 223 places only; gazetteer matching, not NER; no context disambiguation (homographs curated); small Croatian paradigm; multi-word possessives missed; romanised endonyms classified exonym; 15 places (74 names) `unclassified`; `curation.json` re-authored after the original was lost; Persian display forms may lack ZWNJ (transcription limit, matching unaffected - SOURCES.md); scan endpoint contract changed (job, no coordinates). [PLACE_SIGNALS.md](PLACE_SIGNALS.md) | runtime check in step 24; Signal Explorer (step 10) |
| 10 | Horizon + Signal Explorer | RUNTIME-VERIFIED (API and page module on a live production-mode server; not ACCEPTED: no real browser, acceptance is step 28) | `test_signal_explorer_pg.py` (22): buckets, undated and coverage counts, facets, NULL values, literal `%`, paging, saved-search isolation, scope in SQL, timeout, detail, 400/401/404, capped/unloadable menus, one REPEATABLE READ snapshot per response, READ ONLY enforced. `test_signal_query_parsing.py` (54). `test_translation_coverage.py` (+3, `/signals` in ar/he/fa; the template is on the audited list). Live server: `tools/verify/runtime_check_signals.py` (25 checks, 0 failures) and `tools/verify/signals_page_runtime.mjs` (the shipped page module against the live server, 21 checks, 0 failures). Perf: `tools/perf/signal_query_perf.py`, 240 000 signals, 111-570 ms median | `services/detection/signal_query.py`, `signal_store.py` (row mapping), `Api/routes/signals.py` (3 APIs + page), `templates/Signals/signals.html`, `static/js/pages/signals-page.js`, registry `signal_horizon` (`g h`), catalogs ar/he/fa/hr/en + pot; no migration | No real-browser rendering (Playwright download blocked). Translations not reviewed by native speakers. Horizon is temporal-only. `evidence_text` is an unindexed `ILIKE` on the evidence sentence. 10x corpus not measured. Notification stats used the local date vs UTC here (fixed in step 11). [HORIZON.md](HORIZON.md) | step 11 rule engine consumes the same filter model |
| 11 | Rule engine | RUNTIME-VERIFIED (API and job on a live production-mode server; management UI added in step 12 on `/monitoring`, runtime-verified there; rules still have no dry-run of their own) | `test_rule_model.py` (37), `test_rule_engine_pg.py` (21: baseline, dedup incl. detector-version change, demotion/deactivation disable + notify nothing, suppression, threshold+expiry, cooldown, digest, grouping, overflow, derived priority, rollback on failed delivery, busy lock, versions), `test_rules_api.py` (5: owner-only notifications vs another analyst and an admin on every read/write path, audit, job triggers), `test_migration_upgrade_path.py` (0015->0020, CHECKs, downgrade never widens alerts). Live server: `tools/verify/runtime_check_rules.py` (38 checks, 0 failures). Perf: `tools/perf/rule_engine_perf.py`, 202 000 dated signals: steady state 1.7 s broad / 22 ms narrow | `database/migrations/m0020_monitoring_rules.py`, `services/monitoring/{rule_model,rules,rule_engine}.py`, `core/monitoring/priority.py`, `core/monitoring/notification_service.py` (recipient, `insert_alerts`, UTC), `notification_display.py`, `Api/routes/rules.py`, `Api/routes/notifications*.py`, JobManager `rule_evaluation` | No management page; no dry-run; cost scales with matching signals (full anti-join, stated); only JobManager jobs trigger; resume does not re-baseline; ledger unbounded until retention; notification display strings (all types) not in catalogs (pre-existing). Future-date priority bands replaced by derived priority. [RULE_ENGINE.md](RULE_ENGINE.md) | step 12 scenarios + dry-run + UI |
| 12 | Scenarios + dry-run | RUNTIME-VERIFIED (API, job worker and page module on a live production-mode server; not ACCEPTED: no real browser, acceptance is step 28) | `test_scenario_model.py` (13), `test_scenario_engine_pg.py` (22: three strategies, document conditions, absence (`none`), dry-run writes nothing and its notification prediction equals what the first evaluation delivers, invalid ids block activation, 24 h freshness, edit -> new version + draft, baseline, append-only outcomes with previous outcome, leaving the population, trigger refuses UPDATE/DELETE, owner/content deletion cascades, owner demotion disables and notifies nothing else, 49 + overflow cap, SQL priority = Python rule, rollback on failed delivery, failed dry-run recorded), `test_scenarios_api.py` (7: activation gate, owner-only notifications vs another analyst and an admin, edit rights, audit, post-ingestion trigger, page per role), `test_monitoring_page.py` (2), m0021 in `test_migration_upgrade_path.py` (CHECKs, trigger, downgrade keeps rule alerts, re-upgrade), `test_notification_display.py::test_every_display_string_is_in_every_catalog`. Live server: `tools/verify/runtime_check_scenarios.py` (63 checks, 0 failures; runtime DB upgraded 0020 -> 0021 by the production bootstrap) and `tools/verify/monitoring_page_runtime.mjs` (31 checks, 0 failures). Perf: `tools/perf/scenario_engine_perf.py`, 20 000 documents / 243 000 signals: dry-run 713 ms, steady state 795 ms, narrow 47 ms | `database/migrations/m0021_scenarios.py`, `services/monitoring/{scenario_model,scenarios,scenario_engine}.py`, `services/detection/signal_query.py` (`_where` shared), `core/monitoring/notification_{service,display}.py`, `notifications-page.js`, JobManager `scenario_evaluation` / `scenario_dry_run`, `Api/routes/scenarios.py`, `/monitoring` page (`templates/Monitoring/monitoring.html`, `static/js/pages/monitoring-page.js`, registry `monitoring`, `g m`), catalogs ar/he/fa/hr/en + pot | Concept document absent: 8 semantic decisions stated in SCENARIOS.md (document unit; no suppression/cooldown/digest for scenarios; `notify` the only action; ...). Every evaluation re-decides the whole population (no incremental path); no scheduler (step 20); JSON editor, no form builder; DOM stub not a browser (the harness shims compound `tag[attr]` selectors the stub lacks); translations not native-reviewed; outcome history unbounded until retention; the runtime population is 1 document; list shape differs from rules (`items` vs `evaluations`). [SCENARIOS.md](SCENARIOS.md) | step 13 registry |
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
| Step 10 Horizon + Signal Explorer | 3 010 | 30 | 97 | identical to Phase 1 (compared with `comm`) |
| Step 11 Rule engine | 3 077 | 30 | 97 | identical to step 10 (compared with `comm`) |
| Step 12 Scenarios + `/monitoring` | 3 123 | 30 | 97 | identical to step 11 (compared with `comm`); generated docs regenerated with their own commands before the run |
| Field fixes: start-up upgrade + gazetteer packaging | 3 128 | 30 | 97 | 29 OCR (no Tesseract) + select2 licensing. The first run had 31: `test_reference_docs_are_current`, stale because of the two new public start-up functions; regenerated with `tools/docs/generate_reference.py` (diff: 2 lines). The step-12 failure list was lost when the sandbox restarted, so the comparison is by category, not `comm`; the list is now kept in [REGRESSION_FAILURES.txt](REGRESSION_FAILURES.txt) |

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

The first step-10 run had 33 failures. All 3 extra failures came from the new
page's template.
* `test_the_audit_counts_the_hand_written_markup` failed because the page
  hand-wrote two tables and an error box. The pinned count was **not**
  raised: the page was moved onto the shared `data_table` and `state_panel`
  components, and the pin holds at 15.
* The component-library and action-surface documents were stale. They were
  regenerated with their own commands. Their diffs show only adoption rising
  (tables 1 -> 2, states 2 -> 3) and one more registered, undescribed screen.

## Defects found in the field (reported from the owner's Windows run after step 12)

Startup logged `column "recipient_user_id" does not exist` against a
database that had not received m0020. Two independent defects, plus the test
fixture that hid the first one:

| Defect | Origin | Root cause | Fix | Evidence |
| --- | --- | --- | --- | --- |
| Server start-up never upgraded the schema of an installation without `.system_initialized` | pre-existing (`0521aa5`); exposed by m0020 | `ensure_system_initialized` upgraded only in the marker branch; the marker is written only when `config.json` exists, so `.env`-configured installs took the "first startup" branch on every start | `upgrade_installed_schema()` also runs in that branch, only when the critical tables exist (never creates a database); the marker behaviour is unchanged | control run: `run_web.py` with the `02c9350` start-up code against a 0019 database reproduces the error and leaves it at 0019; with the fix the first start prints `[OK] Applied schema migrations 0020, 0021 ... (schema now at version 0021)`, the second `up to date`. `test_startup_without_marker_or_config_upgrades_an_installed_database`, `..._never_creates_an_uninstalled_database` |
| A failing migration at start-up was reported as "database unreachable" | pre-existing | every exception was logged as a skipped check | `[ERROR] Schema upgrade FAILED on <target>: <migration error - cause>`; unreachable stays a warning | `test_a_failing_migration_is_reported_as_a_failure_not_as_unreachable` |
| The gazetteer seed and all its inputs were never committed; m0019 failed on every clone (`SeedIntegrityError`) | **introduced by this work (step 9)** | `.gitignore` ignores `data/`; the files existed only in the sandbox, so the step 9 PostgreSQL verification was environment-dependent | `.gitignore` re-includes `data/gazetteer/`; seed rebuilt from a new Wikidata query (see `data/gazetteer/SOURCES.md`), `SEED_VERSION` `2026-09-28.2` | fresh `runtime_provision.py` bootstrap applies 0001-0021 from a clean tree; `test_gazetteer_packaging.py` fails against the old `.gitignore` (checked) |
| The test suite left `.system_initialized` in the checkout | pre-existing (`tests/conftest.py` `app` fixture) | the marker path is CWD-relative and the fixture wrote it deliberately | the fixture points `INIT_MARKER_FILE` at a session temporary directory | `test_the_app_fixture_does_not_mark_the_checkout`; a full run leaves no marker |

**Consequence for earlier claims.** The RUNTIME-VERIFIED runs of steps 10-12
started `run_web.py` from a checkout carrying the marker left by the tests, and
against a database that `tools/verify/runtime_provision.py` had already
migrated through `bootstrap_database`. They verified the features, but **not**
start-up on a fresh clone or an existing installation, which was broken.
Start-up is now covered by the control run above. The packaging audit
(step 27) must include a clean-clone start.

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
| Signal read APIs parsed ids with `str.isdigit()`: `²` leaked Python's `int()` message in a 400, `٣` was read as 3, `saved_search_id=0` returned 404 | `_positive_int` (ASCII, > 0) with a message naming the parameter | `test_signal_query_parsing.py`, runtime check |
| One Horizon/Explorer response ran as several READ COMMITTED statements: the pool's `SELECT 1` health check leaves pooled connections inside a transaction, so totals, facets and page could disagree during ingestion | the route ends that transaction; `REPEATABLE READ, READ ONLY` snapshot, reported as `read_consistency` | `test_every_api_response_is_read_from_one_snapshot`, `test_the_snapshot_excludes_rows_committed_during_the_read` |
| `/signals` swallowed a failure to load the source/side menus (shown empty) and capped them at 1 000 silently | explicit `options_status` with a visible notice | `test_capped_filter_lists_say_they_are_capped`, `test_unloadable_filter_lists_are_reported_not_left_empty` |
| `alerts` had no recipient: every notification, and its read/dismissed state, was shared by all users | `recipient_user_id` (m0020), filtered in SQL on every read and write path; the cache holds system-wide rows only | `test_notifications_are_visible_to_their_recipient_only` (`test_rules_api.py`), runtime check |
| Future-date notifications were CRITICAL for any date within 8 days, regardless of evidence | derived priority (confidence + imminence per match), basis stored; never `critical` | `test_rule_model.py` priority tests, `test_signal_notifications.py` |
| Notification stats and upcoming events used the server's local date against UTC-stored dates | `utc_today()` | `test_notification_service.py` |
| Mark-read/dismiss swallowed database errors into 404 "not found" | the error propagates (500), 404 only for a missing or foreign notification | `test_notification_service.py` |
| Arabic notification strings (baseline): 5 msgstrs dropped their placeholder and meant something else (e.g. "Similar Files Detected: %(file_name)s" -> "filtering similar files"); duplicate-file and rule display strings were in no catalog | corrected / added (self-authored); guard test over every `translate()` string of `notification_display.py` | `test_every_display_string_is_in_every_catalog` |
| Test data: zero-padded markers in 5 integration modules collided as hash ids once the shared test database grew | sha256 of the marker | the modules pass in any order in the full run |

## Defects found, not fixed

| Defect | Found by | Why not fixed now |
| --- | --- | --- |
| Arabic catalog (baseline `0521aa5`): 4 msgstrs drop `{operation}` / `{category}` / `{word}` (e.g. "Files in "{category}" Category" -> "Files"), 4 are truncated at an escaped quote (end in a literal backslash). he/fa/hr only drop the English plural `{s}`, which is correct for those languages | catalog-wide placeholder scan during step 12 | word-list/category screens, outside step 12; belongs to step 18 (multilingual rendering), where they can be fixed and checked on the screens that use them |

## Open questions for the owner

1. **Child categories.** Criteria accept `include_child_categories`, but
   `categorys(id, word_id)` has no parent column. It is refused, not
   ignored. Should a hierarchy be added (a migration), or should the option
   be removed from the contract?
