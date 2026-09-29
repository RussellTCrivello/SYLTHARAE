"""Report runs: one execution of a registered report definition (step 14).

The reporting model has three objects (REPORT_REGISTRY.md): the *definition*
(code, ``core/reporting``), the *run* (this migration) and the *artifact*
(step 15). A run records everything needed to say what was computed, for
whom, from which data:

* ``report_runs`` - the definition identity (``report_id``, version and its
  pinned fingerprint), the normalised parameters and their fingerprint, the
  criteria fingerprint when the report takes criteria, the saved search
  the criteria were taken from (when they were), the requester (id,
  username and role *at run time* - the id is kept NULL-able so deleting a
  user does not erase the record, the username stays), the JobManager job,
  the status, the PostgreSQL snapshot (``pg_current_snapshot()`` text and
  its timestamp) every dataset was read from, the isolation level, the
  generator version and timing. A completed run must name its snapshot
  (CHECK). Once a run is terminal it is immutable (trigger); the single
  exception is ``requested_by`` / ``saved_search_id`` becoming NULL when
  the account or saved search is deleted.
* ``report_run_datasets`` - per dataset of the run: key and fingerprint, the
  SHA-256 of the executed SQL text and bound values (query fingerprint), the
  declared limit semantics, the row limit, the row count, whether rows were
  cut off, the declared columns and the rows. ``exact`` datasets can never be
  truncated (CHECK: an overflowing exact dataset fails the run instead). The
  rows are written once (trigger refuses UPDATE).

Numbered 0022: 0021 is scenarios.
"""

version = "0022"
name = "report_runs"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS report_runs (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        report_id VARCHAR(100) NOT NULL,
        report_version INTEGER NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL,
        parameters JSONB NOT NULL,
        parameters_fingerprint CHAR(64) NOT NULL,
        criteria_fingerprint CHAR(64) NULL,
        saved_search_id INTEGER NULL REFERENCES saved_searches(id) ON DELETE SET NULL,
        requested_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        requester_username VARCHAR(64) NOT NULL,
        requester_role VARCHAR(16) NOT NULL,
        job_id VARCHAR(32) NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'queued',
        refusal_reason VARCHAR(64) NULL,
        error TEXT NULL,
        snapshot TEXT NULL,
        snapshot_at TIMESTAMPTZ NULL,
        isolation_level VARCHAR(40) NULL,
        generator_version VARCHAR(40) NOT NULL,
        requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at TIMESTAMPTZ NULL,
        finished_at TIMESTAMPTZ NULL,
        CONSTRAINT ck_report_runs_version CHECK (report_version >= 1),
        CONSTRAINT ck_report_runs_status CHECK (status IN
            ('queued', 'running', 'completed', 'failed', 'refused', 'cancelled')),
        CONSTRAINT ck_report_runs_completed CHECK (status <> 'completed' OR (
            snapshot IS NOT NULL AND snapshot_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL)),
        CONSTRAINT ck_report_runs_refused CHECK ((status = 'refused') = (refusal_reason IS NOT NULL)),
        CONSTRAINT ck_report_runs_failed CHECK (status <> 'failed' OR error IS NOT NULL),
        CONSTRAINT ck_report_runs_fingerprints CHECK (
            definition_fingerprint ~ '^[0-9a-f]{64}$'
            AND parameters_fingerprint ~ '^[0-9a-f]{64}$'
            AND (criteria_fingerprint IS NULL OR criteria_fingerprint ~ '^[0-9a-f]{64}$'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_report_runs_requester"
    " ON report_runs (requested_by, requested_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_report_runs_report"
    " ON report_runs (report_id, requested_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_report_runs_job ON report_runs (job_id)",
    "CREATE INDEX IF NOT EXISTS idx_report_runs_saved_search ON report_runs (saved_search_id)"
    " WHERE saved_search_id IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS report_run_datasets (
        run_id BIGINT NOT NULL REFERENCES report_runs(id) ON DELETE CASCADE,
        position SMALLINT NOT NULL,
        dataset_key VARCHAR(120) NOT NULL,
        dataset_fingerprint CHAR(64) NOT NULL,
        query_fingerprint CHAR(64) NOT NULL,
        semantics VARCHAR(8) NOT NULL,
        row_limit INTEGER NOT NULL,
        row_count INTEGER NOT NULL,
        truncated BOOLEAN NOT NULL,
        columns JSONB NOT NULL,
        rows JSONB NOT NULL,
        PRIMARY KEY (run_id, position),
        CONSTRAINT uq_report_run_datasets_key UNIQUE (run_id, dataset_key),
        CONSTRAINT ck_report_run_datasets_semantics CHECK (semantics IN ('exact', 'capped', 'top_n')),
        CONSTRAINT ck_report_run_datasets_counts CHECK (
            row_limit >= 1 AND row_count >= 0 AND row_count <= row_limit),
        CONSTRAINT ck_report_run_datasets_exact CHECK (NOT (semantics = 'exact' AND truncated)),
        CONSTRAINT ck_report_run_datasets_rows CHECK (
            jsonb_typeof(rows) = 'array' AND jsonb_array_length(rows) = row_count),
        CONSTRAINT ck_report_run_datasets_fingerprints CHECK (
            dataset_fingerprint ~ '^[0-9a-f]{64}$' AND query_fingerprint ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE OR REPLACE FUNCTION report_runs_terminal_immutable() RETURNS trigger AS $$
    BEGIN
        -- The one change a terminal run accepts: a referenced account or
        -- saved search was deleted (ON DELETE SET NULL). Each of the two
        -- references is unchanged or cleared; every other column unchanged.
        IF (NEW.requested_by IS NOT DISTINCT FROM OLD.requested_by OR NEW.requested_by IS NULL)
           AND (NEW.saved_search_id IS NOT DISTINCT FROM OLD.saved_search_id
                OR NEW.saved_search_id IS NULL)
           AND (to_jsonb(NEW) - 'requested_by' - 'saved_search_id')
               = (to_jsonb(OLD) - 'requested_by' - 'saved_search_id')
           AND to_jsonb(NEW) <> to_jsonb(OLD) THEN
            RETURN NEW;
        END IF;
        IF OLD.status IN ('completed', 'failed', 'refused', 'cancelled') THEN
            RAISE EXCEPTION 'report run % is % and cannot be changed', OLD.id, OLD.status
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_report_runs_terminal_immutable ON report_runs",
    """
    CREATE TRIGGER trg_report_runs_terminal_immutable
        BEFORE UPDATE ON report_runs
        FOR EACH ROW EXECUTE FUNCTION report_runs_terminal_immutable()
    """,
    """
    CREATE OR REPLACE FUNCTION report_run_datasets_write_once() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'report run results are written once and never updated'
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_report_run_datasets_write_once ON report_run_datasets",
    """
    CREATE TRIGGER trg_report_run_datasets_write_once
        BEFORE UPDATE ON report_run_datasets
        FOR EACH ROW EXECUTE FUNCTION report_run_datasets_write_once()
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS report_run_datasets")
        cur.execute("DROP FUNCTION IF EXISTS report_run_datasets_write_once()")
        cur.execute("DROP TABLE IF EXISTS report_runs")
        cur.execute("DROP FUNCTION IF EXISTS report_runs_terminal_immutable()")
