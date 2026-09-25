"""Phase 2 schema: extraction provenance, archive lineage, processing status.

Approved changes (see PHASE2_SCHEMA_PROPOSALS.md):

A1  paths.extraction_provenance JSONB - per-extractor provenance so recognised
    text is never indistinguishable from authored text, and so 2B-2E metadata
    has somewhere to live without a migration each.

B   paths.parent_path_id + paths.hierarchy_path - archive/child lineage. Until
    now hierarchy_path was accepted by the pipeline and built by the CLI but
    appeared nowhere in database/, so lineage was persisted nowhere at all.
    ON DELETE SET NULL: deleting an archive must not destroy extracted
    children, which are independently indexed records.

C   paths.processing_status (+ status_detail, attempts, status_updated_at).
    file_status is VARCHAR(10) CHECK IN ('Read','Unread') and cannot express
    failed / unsupported / partially processed; 'unsupported' alone is 11
    characters. processing_status is ADDED, not substituted - file_status is
    kept and derived, because rewriting it would touch 57 references across 12
    project files plus 44 in templates and JS.

Every statement is idempotent, guarded by an information_schema check in the
style of m0005, so re-running bootstrap against an upgraded database is safe.
The only existing row rewritten is the backfill of processing_status from
file_status, which is a restatement of information already stored.
"""

version = "0007"
name = "provenance_hierarchy_status"


#: Legal processing states (§10).
PROCESSING_STATES = (
    "discovered",
    "queued",
    "processing",
    "processed",
    "partially_processed",
    "failed",
    "unsupported",
    "skipped",
    "retrying",
)

#: file_status -> processing_status for pre-existing rows.
_BACKFILL = {
    "Read": "processed",
    "Unread": "discovered",
}


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


def _constraint_exists(cur, table: str, constraint: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.table_constraints"
        " WHERE table_name = %s AND constraint_name = %s",
        (table, constraint),
    )
    return cur.fetchone() is not None


def _index_exists(cur, index: str) -> bool:
    cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", (index,))
    return cur.fetchone() is not None


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        # ---------- A1: extraction provenance ----------
        if not _column_exists(cur, "paths", "extraction_provenance"):
            cur.execute(
                "ALTER TABLE paths ADD COLUMN extraction_provenance JSONB NULL"
            )
            cur.execute(
                "COMMENT ON COLUMN paths.extraction_provenance IS"
                " 'Per-extractor provenance: {extractor: {engine, version,"
                " confidence, derived, method, input_variant, extracted_at}}."
                " NULL means ingested before provenance was captured.'"
            )

        # ---------- B: archive lineage ----------
        if not _column_exists(cur, "paths", "parent_path_id"):
            cur.execute("ALTER TABLE paths ADD COLUMN parent_path_id INTEGER NULL")
        if not _column_exists(cur, "paths", "hierarchy_path"):
            cur.execute("ALTER TABLE paths ADD COLUMN hierarchy_path TEXT NULL")
        if not _constraint_exists(cur, "paths", "fk_paths_parent"):
            cur.execute(
                "ALTER TABLE paths ADD CONSTRAINT fk_paths_parent"
                " FOREIGN KEY (parent_path_id) REFERENCES paths(id)"
                " ON DELETE SET NULL"
            )
        if not _index_exists(cur, "idx_paths_parent_path_id"):
            cur.execute(
                "CREATE INDEX idx_paths_parent_path_id ON paths (parent_path_id)"
            )

        # ---------- C: processing status ----------
        if not _column_exists(cur, "paths", "processing_status"):
            states = ", ".join(f"'{s}'" for s in PROCESSING_STATES)
            # 24 chars: 'partially_processed' is 19.
            cur.execute(
                "ALTER TABLE paths ADD COLUMN processing_status VARCHAR(24)"
                f" NOT NULL DEFAULT 'discovered' CHECK (processing_status IN ({states}))"
            )
        if not _column_exists(cur, "paths", "status_detail"):
            cur.execute("ALTER TABLE paths ADD COLUMN status_detail TEXT NULL")
        if not _column_exists(cur, "paths", "attempts"):
            cur.execute(
                "ALTER TABLE paths ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        if not _column_exists(cur, "paths", "status_updated_at"):
            cur.execute(
                "ALTER TABLE paths ADD COLUMN status_updated_at TIMESTAMPTZ NULL"
            )
        if not _index_exists(cur, "idx_paths_processing_status"):
            cur.execute(
                "CREATE INDEX idx_paths_processing_status"
                " ON paths (processing_status)"
            )

        # Backfill existing rows from file_status. Idempotent: only rows still
        # at the column default are touched, and the mapping is a restatement
        # of data already stored rather than new information.
        for legacy, target in _BACKFILL.items():
            cur.execute(
                "UPDATE paths SET processing_status = %s, status_updated_at = NOW()"
                " WHERE file_status = %s AND processing_status = 'discovered'",
                (target, legacy),
            )


def downgrade(conn) -> None:
    """Best-effort reversal.

    file_status is never altered, so the pre-migration behaviour is fully
    intact after this runs. Only the columns added here are lost.
    """
    statements = [
        "DROP INDEX IF EXISTS idx_paths_processing_status",
        "DROP INDEX IF EXISTS idx_paths_parent_path_id",
        "ALTER TABLE paths DROP CONSTRAINT IF EXISTS fk_paths_parent",
        "ALTER TABLE paths DROP COLUMN IF EXISTS status_updated_at",
        "ALTER TABLE paths DROP COLUMN IF EXISTS attempts",
        "ALTER TABLE paths DROP COLUMN IF EXISTS status_detail",
        "ALTER TABLE paths DROP COLUMN IF EXISTS processing_status",
        "ALTER TABLE paths DROP COLUMN IF EXISTS hierarchy_path",
        "ALTER TABLE paths DROP COLUMN IF EXISTS parent_path_id",
        "ALTER TABLE paths DROP COLUMN IF EXISTS extraction_provenance",
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
