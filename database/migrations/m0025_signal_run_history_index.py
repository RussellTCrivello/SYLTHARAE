"""0025: page-order index for the detection run history (Detection page).

``services/detection/detection_admin.list_runs`` lists
``content_signal_runs`` newest first (``ORDER BY ran_at DESC, hash_id DESC,
detector``) under optional filters. m0017 indexes only the primary key
(hash_id, detector) and (detector, detector_ver), so every page was a full
scan plus sort - measured by ``tools/perf/detection_admin_perf.py`` at
760,000 runs: 50-71 ms per first page, 132 ms at offset 100,000, growing
linearly with the table.

``(ran_at DESC, hash_id DESC, detector)`` is exactly the page order: the
first page is an index range scan and a filtered page walks the index until
it has its rows. Only an index is added; no data changes. Downgrade drops
exactly it.
"""

version = "0025"
name = "signal_run_history_index"

INDEXES = (
    ("idx_content_signal_runs_ran_at",
     "content_signal_runs (ran_at DESC, hash_id DESC, detector)"),
)

SQL_STATEMENTS = [f"CREATE INDEX IF NOT EXISTS {name_} ON {target}" for name_, target in INDEXES]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        for index_name, _ in INDEXES:
            cur.execute(f"DROP INDEX IF EXISTS {index_name}")
