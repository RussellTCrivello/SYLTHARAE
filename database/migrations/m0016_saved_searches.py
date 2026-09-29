"""Saved searches move from ``data/saved_searches.json`` into PostgreSQL.

Why: a JSON file cannot be joined against new content, indexed, paginated,
permission-filtered in SQL, or kept transactionally consistent with the
notifications a monitoring rule produces. Saved searches are the substrate
for monitoring (a saved search with ``monitor_enabled``), so they must live
in the database.

Shape
-----
``saved_searches`` keeps the page's legacy definition verbatim (``query`` +
``filters``) so every existing API consumer and the Advanced Search "Run"
link keep working unchanged, and stores the canonical ``criteria`` beside it
with its SHA-256 ``criteria_fingerprint`` and ``criteria_schema_version``.

``owner_user_id`` is nullable on purpose: saved searches written before
API-04 were recorded with ``user_id = None``. They are imported as
*unowned* (visible to administrators only, who may reassign them) rather
than dropped or silently handed to someone.

``saved_search_imports`` is the ledger of legacy JSON imports: one row per
distinct file content (SHA-256), so the import is idempotent and a deleted
search can never be resurrected by re-reading the old file. Entries that
could not be imported are recorded in ``failures`` - never silently lost.

The import itself is performed by
``Api/services/saved_searches_repository.py::import_legacy_json`` (migrations
must not depend on application data paths).
"""

version = "0016"
name = "saved_searches"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS saved_searches (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_user_id INTEGER NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        query TEXT NOT NULL DEFAULT '',
        filters JSONB NOT NULL DEFAULT '{}'::jsonb,
        criteria JSONB NOT NULL,
        criteria_fingerprint CHAR(64) NOT NULL,
        criteria_schema_version INTEGER NOT NULL,
        monitor_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_used_at TIMESTAMPTZ NULL,
        legacy_source VARCHAR(64) NULL,
        legacy_id INTEGER NULL,
        import_notes JSONB NULL,
        CONSTRAINT ck_saved_searches_name_nonempty CHECK (length(btrim(name)) > 0),
        CONSTRAINT ck_saved_searches_fingerprint_hex
            CHECK (criteria_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_saved_searches_legacy_pair
            CHECK ((legacy_source IS NULL) = (legacy_id IS NULL)),
        CONSTRAINT uq_saved_searches_legacy UNIQUE (legacy_source, legacy_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_saved_searches_owner_created"
    " ON saved_searches (owner_user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_saved_searches_fingerprint"
    " ON saved_searches (criteria_fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_saved_searches_monitor"
    " ON saved_searches (id) WHERE monitor_enabled",
    """
    CREATE TABLE IF NOT EXISTS saved_search_imports (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_file TEXT NOT NULL,
        file_sha256 CHAR(64) NOT NULL,
        entries_found INTEGER NOT NULL CHECK (entries_found >= 0),
        entries_imported INTEGER NOT NULL CHECK (entries_imported >= 0),
        entries_already_present INTEGER NOT NULL CHECK (entries_already_present >= 0),
        failures JSONB NOT NULL DEFAULT '[]'::jsonb,
        imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_saved_search_imports_sha UNIQUE (file_sha256)
    )
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS saved_search_imports")
        cur.execute("DROP TABLE IF EXISTS saved_searches")
