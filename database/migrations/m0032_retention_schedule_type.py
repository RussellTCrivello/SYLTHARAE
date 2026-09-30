"""Retention joins the schedulable job types (step 21).

Why: retention prunes aged rows (jobs, rule ledger and evaluations,
scenario outcomes, notifications, report runs and artifacts, path
revisions, audit log) with an explicit, audited policy. Like a report or
an evaluation, "prune on an interval" is exactly what the step-20
schedules exist for - so ``retention`` becomes a schedule type: an
administrator schedules it, every fire re-checks their current role
(the scheduler already does this), and the policy itself lives in the
settings (`retention.<area>_days`, 0 = keep forever).
"""

version = "0032"
name = "retention_schedule_type"

SQL_STATEMENTS = [
    """
    ALTER TABLE job_schedules
        DROP CONSTRAINT IF EXISTS ck_job_schedules_type
    """,
    """
    ALTER TABLE job_schedules
        ADD CONSTRAINT ck_job_schedules_type
        CHECK (schedule_type IN ('report_run', 'rule_evaluation',
                                 'scenario_evaluation', 'retention'))
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        # Existing retention schedules are disabled with a stated reason, not
        # deleted; the restored check is NOT VALID so their rows may survive
        # (the old schema simply does not accept *new* retention schedules).
        cur.execute(
            "UPDATE job_schedules SET enabled = FALSE,"
            " disabled_reason = 'retention schedules are not supported by this"
            " schema version' WHERE schedule_type = 'retention'")
        cur.execute(
            "ALTER TABLE job_schedules"
            " DROP CONSTRAINT IF EXISTS ck_job_schedules_type")
        cur.execute(
            "ALTER TABLE job_schedules"
            " ADD CONSTRAINT ck_job_schedules_type"
            " CHECK (schedule_type IN ('report_run', 'rule_evaluation',"
            " 'scenario_evaluation')) NOT VALID")
