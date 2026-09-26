# Changelog

## v2.1.0 — 2026-09-25 — first production release

First operational release of SYLTHARAE.

### Notifications
- The notification service reads and writes in a thread-safe way: refresh swaps in the new data in one step (under an `RLock`), so concurrent refreshes can no longer double the rows held in memory.
- Batch flush uses a multi-row `INSERT` and gives rows with the same timestamp distinct `created_at` values. If a batch fails, its rows go back on the queue instead of being lost.
- `mark_as_read` / `dismiss` confirm the change with `RETURNING id`.
- `get_stats()` and `get_upcoming_events()` are computed in SQL, plus the rows still waiting to be written. Counts match the `alerts` table exactly.
- Titles and messages are formatted in one shared module (`core/monitoring/notification_display.py`).
- Notifications page: filtering, search, sorting (whitelisted columns), pagination and summary counts all run in SQL. `per_page` is capped at 1000. Source and side are resolved through `paths → hash_contexts`.
- Scan endpoint: responses carry real database ids (the response is built after the flush). A dismissed duplicate stays dismissed, and each hash gets at most one notification.
- Migration `m0014_alerts_notification_indexes` adds the matching `alerts` indexes.

### Readers and detection
- New `DiagramFileReader` for draw.io / diagrams.net files (`.drawio`, `.dio`), in both plain mxfile XML and ZIP (`file.xml` + `metadata.xml`) form.
- The format catalogue has a `diagram` family. A ZIP-form `.drawio` goes to the diagram reader, not the archive reader.

### Pipeline, keywords and logging
- Keyword-path relationship inserts go through `KeywordOperations.insert_keyword_path_relationships`, which reports how many rows it wrote.
- The storage pipeline retries only connection and retryable errors; other errors fail immediately.
- Progress and summaries are logged instead of printed one by one. stdout is line-buffered for the CLI and the web app, and werkzeug is set to WARNING.

### Frontend
- The notifications page shows exact summary counts. The saved-searches prompt is properly awaited.
