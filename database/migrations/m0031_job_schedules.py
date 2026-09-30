"""Scheduled jobs (step 20).

Why: rules evaluate after JobManager ingestion jobs and on manual trigger,
content stored by other paths waits for the next manual evaluation, and a
report only runs when somebody presses the button. A schedule says "run
this job every N minutes" - a report run as its owner, or a periodic
evaluation of every active rule / scenario - and is manageable through the
Schedules page like every other configured object (create, edit, delete,
pause, resume, run now).

The scheduler never invents authorization: each fire re-reads the owner's
current role and activity (the same eligibility read the rule engine uses)
and a report schedule additionally revalidates its payload against the
report registry. An owner who lost the required role, or a report that no
longer exists in that version, disables the schedule with a stated reason -
nothing runs as somebody who may no longer run it.

Timing is interval-based and at-most-once: claiming a due schedule advances
``next_run_at`` before enqueueing, so downtime produces one catch-up fire,
never a burst.
"""

version = "0031"
name = "job_schedules"

SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS job_schedules (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        schedule_type VARCHAR(32) NOT NULL,
        name VARCHAR(200) NOT NULL,
        owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        interval_minutes INTEGER NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        disabled_reason VARCHAR(64) NULL,
        next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_run_at TIMESTAMPTZ NULL,
        last_job_id TEXT NULL,
        last_status VARCHAR(32) NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_job_schedules_owner_name UNIQUE (owner_user_id, name),
        CONSTRAINT ck_job_schedules_type
            CHECK (schedule_type IN ('report_run', 'rule_evaluation', 'scenario_evaluation')),
        CONSTRAINT ck_job_schedules_name CHECK (length(btrim(name)) > 0),
        CONSTRAINT ck_job_schedules_interval CHECK (interval_minutes BETWEEN 5 AND 525600),
        CONSTRAINT ck_job_schedules_failures CHECK (consecutive_failures >= 0)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_schedules_due
        ON job_schedules (next_run_at) WHERE enabled
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_schedules_owner
        ON job_schedules (owner_user_id)
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS job_schedules")
