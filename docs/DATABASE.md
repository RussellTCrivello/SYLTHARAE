# Database

SYLTHARAE stores everything in one PostgreSQL database (13 or newer; only
`plpgsql`, no extensions). This document explains the design; the exact
columns, keys and indexes are generated in
[reference/DATABASE_SCHEMA.md](reference/DATABASE_SCHEMA.md) from a migrated
database, and the domain concepts are in [DOMAIN_MODEL.md](DOMAIN_MODEL.md).

## 1. Identity: one content, many contexts

The core of the schema is three levels of identity (migration `m0011`,
enforced by `m0013`), defined in exactly one place in code -
`database/services/dedup_service.py`:

| Level | Table | Unique on | Meaning |
| --- | --- | --- | --- |
| Content | `hashs` | `hash` (SHA-256 of the bytes) | The bytes themselves, stored once. |
| Context | `hash_contexts` | `(hash_id, source_id, side_id)` | That content as it belongs to one source and side. |
| Occurrence | `paths` | row | One physical encounter: a file on disk, an archive member, an attachment. |

Everything extracted from the bytes hangs off the **content** (`hashs.id`)
and is therefore stored once, however many copies are ingested: `contents`,
`contents_raw`, `titles_content`, `words_hashs`, `keywords_hashs`. Everything
about *where* a copy was found hangs off the **occurrence** (`paths`): name,
path, size, dates, coordinates, provenance, parent in an archive, processing
status.

```
sources ─┐                       ┌─ contents / contents_raw
         ├─ hash_contexts ── hashs ┼─ titles_content
sides ───┘        │                ├─ words_hashs ── words ── words_categorys ── categorys
                  │                └─ keywords_hashs ── keywords ── categorys
                paths ─┬─ paths (parent_path_id: archive hierarchy)
                       ├─ alerts
                       └─ analyst_file_categories ── analyst_categories
```

## 2. Table groups

| Group | Tables | Written by |
| --- | --- | --- |
| Provenance | `sources`, `sides` | Sources and Sides pages, domain import. |
| Identity and occurrences | `hashs`, `hash_contexts`, `paths` | Ingestion (`StoragePipeline` → `ContentDBService`). |
| Extracted content | `contents` (word-id index form), `contents_raw` (faithful text in chunks, `m0010`), `titles_content` | Ingestion. |
| Search index | `words`, `words_hashs` (counts and positions per content), `punctuation` | Ingestion. |
| System taxonomy | `categorys`, `words_categorys`, `keywords`, `keywords_hashs` | Domain import and categorisation; the automatic ("smart") classification. |
| Analyst taxonomy | `analyst_categories`, `analyst_file_categories`, `analyst_categorization_log` | Analysts, manually (`m0009`); kept fully separate from the system taxonomy. |
| Operations | `jobs`, `job_events` | Job system (`m0006`). |
| Notifications | `alerts` | Monitoring and scans (`m0014` indexes). |
| Security | `users`, `sessions`, `audit_log` | Authentication (`m0003`). |
| Localisation | `translation_overrides` | Translation Management page (`m0012`). |
| Schema | `schema_migrations` | Migration runner. |

The historical table names (`hashs`, `categorys`, `sides`) are kept as-is:
renaming them would touch every query for no functional gain.

## 3. Migrations

`database/migration_runner.py` is the only way the schema changes. Each
`database/migrations/mNNNN_*.py` defines `version`, `name`, `upgrade(conn)`
(and optionally `downgrade`). Pending migrations run in order, each in its
own transaction, recorded in `schema_migrations`. They run automatically on
server start (`core.init`), from the setup wizard and from `install.py`.

| Version | Purpose |
| --- | --- |
| 0001 | Initial schema, created in dependency order. |
| 0002 | Performance indexes, each with a stated purpose (dedup, file-name and path lookups, type filters, word lookups). |
| 0003 | `users`, `sessions`, `audit_log`. |
| 0004 | Removes `pg_trgm` (see §4). |
| 0005 | `paths.error_message`, which the application already wrote. |
| 0006 | `jobs`, `job_events` (additive). |
| 0007 | `paths.extraction_provenance` (JSONB), `parent_path_id` and `hierarchy_path` (archive lineage), `processing_status`/`status_detail`/`attempts`. |
| 0008 | `paths.file_size` widened to `BIGINT` (files over 2 GiB). |
| 0009 | Analyst categorisation tables. |
| 0010 | `contents_raw`: structured raw text for faithful display. |
| 0011 | Content identity: `hashs` unique on `hash`, new `hash_contexts`, derived data moved to the content; historical duplicates consolidated, never deleted blindly. |
| 0012 | `translation_overrides`. |
| 0013 | Merges duplicate `hash_contexts` and enforces `(hash, source, side)` uniqueness. |
| 0014 | Targeted indexes for notification queries on `alerts`. |

To inspect or apply by hand:

```python
from database.migration_runner import migration_status, run_migrations
# with a psycopg2 connection `conn`:
migration_status(conn)          # every migration, applied or pending
run_migrations(conn, dry_run=True)
```

Rules: never edit a migration that has been released; add a new one. Keep
migrations idempotent (`IF NOT EXISTS`), free of import-time state, and
additive where possible. After adding one, regenerate the schema reference
(`python3 tools/docs/generate_schema.py`).

## 4. Decisions

* **No `pg_trgm` (DB-07).** No query uses trigram similarity; the extension
  and its GIN index came from a legacy bootstrap, cost write throughput, and
  are unavailable on minimal PostgreSQL installs. `m0004` removes both, so
  the database runs on any PostgreSQL 13 or newer.
* **Word index and raw text are separate.** `contents` holds the word-id
  form used for search; it lowercases and re-joins words, which destroys
  layout. `contents_raw` keeps the extractor's structure (sheets, slides,
  message parts) in ordered chunks for display.
* **Sizes are `BIGINT`** (`m0008`); evidence files over 2 GiB are normal.
* **Integrity in the database.** Foreign keys, `CHECK` constraints (for
  example `file_status IN ('Read','Unread')`, `file_size >= 0`) and unique
  constraints enforce the model even if application code is wrong.

## 5. Connections and transactions

`database/database/database.py` keeps a psycopg2 connection pool
(`DB_MIN_CONNECTIONS`/`DB_MAX_CONNECTIONS`, tuned at runtime by the resource
coordinator). Repositories (`database/database/repository/`) take a
transaction scope; `ContentDBService.process_full_document()` stores one
document in one transaction. Transient errors (pool exhausted, connection
lost, serialization failures) are retried by the storage pipeline;
permanent ones are recorded on the path (`processing_status`,
`status_detail`, `error_message`).

## 6. Maintenance

* **Backup:** `pg_dump -Fc -f syltharae.dump analysis` (plus `APP_DATA_DIR`).
  **Restore:** `pg_restore -d analysis --clean syltharae.dump`. The admin-only
  backup export in the UI (`/api/import-export`) is a convenience, not a
  substitute. See [OPERATIONS.md](OPERATIONS.md).
* **Vacuum/analyse:** autovacuum is sufficient; run `VACUUM (ANALYZE)` after
  very large imports or deletions.
* **Size:** `words_hashs` and `contents_raw` grow fastest; monitor with
  `SELECT relname, pg_size_pretty(pg_total_relation_size(oid)) FROM pg_class
  WHERE relkind = 'r' ORDER BY pg_total_relation_size(oid) DESC LIMIT 10;`.
