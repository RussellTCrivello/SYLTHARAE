"""Remove the pg_trgm dependency (DB-07 - Option B).

Decision (documented in docs/DATABASE.md): no runtime query path uses trigram
similarity; the extension and its GIN index were installed by the legacy
bootstrap but provided no runtime benefit while adding an operational
dependency that is unavailable on minimal PostgreSQL installs.  This
migration removes the index (if present) and the extension (if present),
making the application database portable to any PostgreSQL >= 13.
"""

version = "0004"
name = "remove_pg_trgm"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_words_word_gin'"
        )
        if cur.fetchone():
            cur.execute("DROP INDEX IF EXISTS idx_words_word_gin")
        cur.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
        )
        if cur.fetchone():
            # Only drop when no other object in this database depends on it.
            cur.execute("DROP EXTENSION IF EXISTS pg_trgm")
    conn.commit()
