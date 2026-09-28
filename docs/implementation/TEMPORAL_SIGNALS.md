# Temporal signals (Phase 1)

Multilingual date and orientation detection, stored per content and
consumed by notifications. It replaces `core/monitoring/future_events.py`
(removed), which was English-only, read the wall clock at construction and
turned "next week" into a concrete date.

## Pieces

| Piece | Path | Role |
| --- | --- | --- |
| Calendars | `core/detection/calendars.py` | Jalali (exact, 33-year arithmetic) and Hijri (tabular, returned as a +/-2 day range) to Gregorian |
| Detector | `core/detection/temporal_intel.py` | `detect(text, anchor_date=None, languages=None)` - pure, deterministic, never reads the clock |
| Store | `services/detection/signal_store.py` | Writes `content_signals` / `content_signal_runs`; `signals_for(cur, hash_id, reference_date, scope=)` reads with authorization |
| Re-detection | `services/detection/redetection.py` | `run_redetection` (job) and `redetect_one`; one transaction per content |
| Ingestion | `ContentDBService.process_full_document`, step 10 | Detects on the stored, sanitised display text inside its own savepoint |
| Job type | `services/jobs/manager.py` `signal_redetection` | Existing JobManager; no second job framework |
| API | `Api/routes/signals.py` | `GET /api/content/<hash_id>/signals`, `POST /api/signals/redetect` |
| Consumer | `Api/routes/notifications.py` (`analyze-file`, `scan`) | FUTURE_DATE notifications from stored signals |

## What is detected

| Capability | Languages / forms | Test |
| --- | --- | --- |
| Day-month-year with month names | en, ar (MSA + Levantine names), he (with ה/ב/ל/מ prefixes), fa, hr (nominative and genitive, ordinal dot) | `test_the_same_day_in_every_language` |
| Numeric dates | ISO, `/`, `-`, `.`; Croatian `15. 3. 2026.` | `test_unambiguous_numeric_dates_resolve` |
| Digits | ASCII, Arabic-Indic, Persian; offsets always address the original text | `test_digits_are_normalised_but_offsets_quote_the_original` |
| Calendars | Gregorian; Hijri with `هـ`/AH marker or Hijri month name; Jalali with `ه.ش`/Persian month name | `test_hijri_*`, `test_jalali_*` |
| Month precision | "October 2026", "رمضان 1447" as whole-month ranges | `test_month_precision_is_a_whole_month_range` |
| Relative expressions | tomorrow/yesterday, in N days/weeks/months/years, N ago, next/last week/month/year in all five languages | `test_relative_expressions_in_every_language` |
| Orientation cues | lexical future/present/past cues per language (e.g. `will`, `سوف`, `יתקיים`, `خواهد`, `će`) | `test_orientation_cues_in_every_language` |

### Ambiguity is kept, never guessed

* `03/04/2026` in Latin-script text is `ambiguous` with both readings in
  `evidence.alternatives`. Arabic-script documents use day-first and say so
  in `evidence.convention`.
* A 13xx/14xx year without a marker is `ambiguous` between Hijri and Jalali.
* A date without a year is `unresolved` ("year not stated; not guessed").
* A relative expression without a document date is `unresolved`. Its value
  says what was written: `rel:+1d` for days; `rel:offset:+1m` ("in 1 month",
  a day) vs `rel:period:+1m` ("next month", a whole month).

### Clock

Stored signals never depend on when they were detected. Past/present/future
relative to a clock is computed at read time with an explicit
`reference_date` (`TemporalSignal.clock_orientation`). `None` means unknown -
unresolved, or an ambiguous date whose readings straddle the reference -
and is never treated as "not in the future". The API echoes the reference
date and whether it came from the request or the server's UTC date.

### No document anchor, deliberately

Relative expressions resolve only against the document's own authored date.
The schema has none: `contents.content_date` is the first date *mentioned*
in the text (`pipeline/storage_pipeline.py`), and `paths.file_date` is a
per-copy filesystem date. Anchoring on either would fabricate dates, so
`signal_store.anchor_for` returns no anchor with the stated reason, and
production relative expressions are stored `unresolved`. The resolution code
path is implemented and tested (`test_relative_expression_resolves_only_against_the_document_date`,
`test_next_week_uses_the_languages_week`); it activates once an extractor
records an authored date (for example an e-mail `Date:` header) and
`anchor_for` reads it.

## Database (m0017)

`content_signals` - one row per signal, keyed on `hash_id` (content, not
path: identical bytes in two contexts are analysed once).

* `dedup_key` CHAR(64) UNIQUE: SHA-256 over content id, detector version,
  type, value, offsets and anchor, JSON-encoded so NULL never equals the
  string "None". Inserts use `ON CONFLICT (dedup_key) DO NOTHING`.
* CHECK constraints: hex key, type shape, non-empty value, offset order,
  calendar and resolution vocabularies, date range paired and ordered,
  resolved <=> has range, `document_relative` => anchor, orientation
  vocabulary.
* `evidence` JSONB: pattern, context window, digit script, calendar method,
  alternatives, reasons.
* `ON DELETE CASCADE` from `hashs`.

`content_signal_runs` - one row per (content, detector): version, status
(`complete`, `truncated`, `no_text`, `failed`), characters scanned/total,
signal count, trigger (`ingestion`/`redetection`), job id, error. `failed`
requires an error; `truncated` requires scanned < total. "No signals found"
(`complete`, 0) and "never analysed" (no row) are different states.

## Failure behaviour

* Detection at ingestion is an optional step: a failure keeps the document
  (path `processing_status = partially_processed`, status detail naming
  `signals`, warning "signals step failed") and records a `failed`
  run naming the underlying driver error.
* Display text that was not stored (step 6 failed) records a `failed` run -
  signals would otherwise carry offsets into text that does not exist.
* Text longer than `MAX_SCAN_CHARS` is scanned up to the limit and recorded
  `truncated` with both counts.
* Re-detection records per-content failures and continues; the job reports
  `processed / complete / failed` counts and honours cancellation.

### Defect fixed on the way: "contained" steps were not contained

A real PostgreSQL error inside any optional ingestion step (display text,
keywords, title - and signals) rolled back the **whole document**. After
`ROLLBACK TO SAVEPOINT` succeeded, `TransactionScope.savepoint` re-raised the
driver's `TransactionAbortedError` ("the transaction is aborted") although
the transaction was healthy again, and every step handler re-raises that type
by design. The scope now raises `ContainedStatementError` (a `QueryError`)
once the savepoint is restored; `TransactionAbortedError` still escapes when
the savepoint itself cannot be restored. Earlier tests injected Python
exceptions, which never put the connection into the aborted state; the new
tests use `SELECT 1 / 0` (`test_a_database_error_in_the_raw_text_step_is_contained`,
`test_a_database_error_in_detection_is_contained_and_recorded`).

## Notifications

`analyze-file` and `scan` create FUTURE_DATE notifications only from stored
signals whose resolved range lies wholly after the reference date. Ambiguous
and unresolved signals create nothing. The scan reports
`contents_without_current_signals` - content not evaluated because it has no
run by the current detector version (fix with `POST /api/signals/redetect`).
`analyze-file` analyses such content on demand. Both honour
`notifications.future_dates_enabled`; the obsolete
`notifications.future_events_enabled` toggle (verb-tense analyzer) was
removed from the settings page and is ignored if present in a settings file.

The priority bands (7/30/90 days) are carried over unchanged from the
previous implementation; they are product defaults, not sourced thresholds,
and are replaced by computed priority in Phase 3.

## Verification

```
python -m pytest tests/unit/test_temporal_intel.py              # 73 detector tests, no DB
python -m pytest tests/integration/test_signal_ingestion.py     # ingestion, m0017 constraints, re-detection job, API
python -m pytest tests/integration/test_signal_notifications.py # notifications from stored signals
python -m pytest tests/integration/test_migration_upgrade_path.py tests/integration/test_bootstrap_migrations.py
```

## Known limitations

* Not detected: spelled-out numbers ("the fifth of October"), times of day,
  weekday names, the Hebrew calendar.
* Orientation is lexical, not parsed: "will" as a noun is a false future
  cue; Arabic future verbs prefixed with `سـ` are not cues (too many false
  positives without morphology).
* Hijri conversion is tabular; the stored range (+/-2 days) covers
  sighting-based variation but is not an authority for a specific country.
* Relative expressions are never resolved in production (no authored-date
  metadata, see above).
* No UI yet; the Signal Explorer/Horizon views are directive step 10.
* `core/monitoring/notification_integration.py` has no callers (dead code);
  its settings reads were corrected, removal is left to Phase 3.
