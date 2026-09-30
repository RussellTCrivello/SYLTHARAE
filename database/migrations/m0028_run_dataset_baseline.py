"""Where a run records what its reader had seen (reporting: Latest).

Why: the Latest report reads per viewer - every row says whether this
reader had already seen it. That reading is only honest if the run also
records the baseline it used: ``last_max_id`` as the snapshot found it
(before) and the value the reader's baseline was advanced to (after).
Without the before-value on the run, a later re-run's different
classification would be indistinguishable from a definitional change, and
the run could not be interpreted after the fact.

Shape
-----
``report_run_datasets.baseline`` is that per-dataset record, ``NULL`` for
every dataset that is not read per viewer:

``criteria_hash``    the view key the run used (the same SHA-256 the
                     dataset bound into its own SQL).
``last_max_id_before`` how far the reader had read *as this run found
                     it* - ``None`` when the reader had never viewed this
                     view (absent row, never a zero).
``advanced_to``      the maximum progress id over the rows the run
                     actually showed, after the monotonic advance; ``None``
                     when the advance could not be recorded (the failure is
                     named in ``baseline_recorded`` - the reader may re-see
                     rows, which is the safe direction, but the run says
                     so rather than pretending).

Plain ``ALTER TABLE ... ADD COLUMN ... DEFAULT NULL``: every existing row
describes a non-viewer dataset and stays ``NULL``; no backfill exists or
is needed.
"""

version = "0028"
name = "report_run_datasets_baseline"

SQL_STATEMENTS = [
    """
    ALTER TABLE report_run_datasets
        ADD COLUMN IF NOT EXISTS baseline JSONB DEFAULT NULL
    """,
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE report_run_datasets DROP COLUMN IF EXISTS baseline")
