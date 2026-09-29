"""Report artifacts: the immutable outputs of a completed report run (step 15).

The third object of the reporting model (REPORT_REGISTRY.md): definition
(code) -> run (m0022) -> **artifact** (this migration). An artifact is the
bytes of one rendering of one completed run in one format, together with the
provenance manifest that says exactly what those bytes are.

``report_artifacts``:

* ``run_id`` - the completed run rendered. Artifacts are rendered from the
  run's stored datasets (the rows read in the run's single snapshot), never
  by querying again, so an artifact describes the run's snapshot and nothing
  else.
* ``format``, ``dataset_key`` (the one dataset a single-table format such as
  CSV renders; NULL for formats that carry every dataset), ``renderer_version``
  (the renderer that produced the bytes), ``filename`` (server-generated,
  restricted to a safe character set by CHECK), ``media_type``.
* ``content`` (BYTEA) with ``byte_size`` and ``sha256``. The database itself
  checks ``byte_size = octet_length(content)`` and
  ``sha256 = encode(sha256(content), 'hex')``: a row whose recorded digest
  does not describe its bytes cannot exist.
* ``manifest`` (JSONB, the provenance manifest) and ``manifest_sha256`` (the
  SHA-256 of the manifest's canonical JSON serialisation, recomputed and
  checked by the application).
* ``created_by`` / ``creator_username`` / ``creator_role``, ``job_id``,
  ``created_at``.

One artifact per (run, format, dataset, renderer version): rendering is
deterministic, so a second request for the same thing returns the existing
artifact instead of storing a copy. Artifacts are immutable (trigger); the
single accepted change is ``created_by`` becoming NULL when the account is
deleted. Deleting the run deletes its artifacts (retention, step 21).

Why BYTEA and not files under APP_DATA_DIR: the bytes, their digest and
their manifest are written in one transaction (no orphan files, no manifest
without bytes), are covered by the same backup as the run they describe,
and no filesystem path is ever derived from a request (no traversal surface).
The size is bounded by the application (``MAX_ARTIFACT_BYTES``).

Numbered 0023: 0022 is report runs.
"""

version = "0023"
name = "report_artifacts"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS report_artifacts (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id BIGINT NOT NULL REFERENCES report_runs(id) ON DELETE CASCADE,
        format VARCHAR(16) NOT NULL,
        dataset_key VARCHAR(120) NULL,
        renderer_version VARCHAR(40) NOT NULL,
        filename VARCHAR(200) NOT NULL,
        media_type VARCHAR(120) NOT NULL,
        byte_size BIGINT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        content BYTEA NOT NULL,
        manifest JSONB NOT NULL,
        manifest_sha256 CHAR(64) NOT NULL,
        created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        creator_username VARCHAR(64) NOT NULL,
        creator_role VARCHAR(16) NOT NULL,
        job_id VARCHAR(32) NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_report_artifacts_format CHECK (format ~ '^[a-z0-9]{2,16}$'),
        CONSTRAINT ck_report_artifacts_filename CHECK (
            filename ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$' AND filename !~ '\\.\\.'),
        CONSTRAINT ck_report_artifacts_size CHECK (byte_size = octet_length(content)),
        CONSTRAINT ck_report_artifacts_digest CHECK (sha256 = encode(sha256(content), 'hex')),
        CONSTRAINT ck_report_artifacts_manifest CHECK (
            jsonb_typeof(manifest) = 'object' AND manifest_sha256 ~ '^[0-9a-f]{64}$')
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_report_artifacts_rendering ON report_artifacts"
    " (run_id, format, COALESCE(dataset_key, ''), renderer_version)",
    "CREATE INDEX IF NOT EXISTS idx_report_artifacts_run ON report_artifacts (run_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_report_artifacts_creator"
    " ON report_artifacts (created_by, created_at DESC)",
    """
    CREATE OR REPLACE FUNCTION report_artifacts_immutable() RETURNS trigger AS $$
    BEGIN
        -- The one accepted change: the creator's account was deleted
        -- (ON DELETE SET NULL). Everything else about an artifact is final.
        IF NEW.created_by IS NULL AND OLD.created_by IS NOT NULL
           AND (to_jsonb(NEW) - 'created_by') = (to_jsonb(OLD) - 'created_by') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'report artifact % is immutable', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_report_artifacts_immutable ON report_artifacts",
    """
    CREATE TRIGGER trg_report_artifacts_immutable
        BEFORE UPDATE ON report_artifacts
        FOR EACH ROW EXECUTE FUNCTION report_artifacts_immutable()
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS report_artifacts")
        cur.execute("DROP FUNCTION IF EXISTS report_artifacts_immutable()")
