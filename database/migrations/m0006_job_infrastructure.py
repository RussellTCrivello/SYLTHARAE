"""Persistent job infrastructure (OPS: unified frontend operations).

ADDITIVE ONLY: creates two new tables (`jobs`, `job_events`). No existing
table, column, or row is touched. The frontend job system (services/jobs)
persists job state here so long-running ingestion/import operations survive
restarts, expose real progress, support cooperative cancellation and
crash recovery.
"""

version = "0006"
name = "job_infrastructure"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id                 TEXT PRIMARY KEY,
                job_type               TEXT NOT NULL,
                status                 TEXT NOT NULL DEFAULT 'QUEUED',
                progress               INTEGER NOT NULL DEFAULT 0,
                current_phase          TEXT,
                current_item           TEXT,
                source                 TEXT,
                options                JSONB DEFAULT '{}'::jsonb,
                stats                  JSONB DEFAULT '{}'::jsonb,
                errors                 JSONB DEFAULT '[]'::jsonb,
                warnings               JSONB DEFAULT '[]'::jsonb,
                result_summary         JSONB,
                created_by             TEXT,
                created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
                started_at             TIMESTAMPTZ,
                completed_at           TIMESTAMPTZ,
                cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
                pause_requested        BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events (
                event_id   BIGSERIAL PRIMARY KEY,
                job_id     TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload    JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_events_job"
            " ON job_events (job_id, event_id)"
        )
    conn.commit()
