"""Add paths.error_message (application writes it; schema lacked it).

The re-analysis endpoint updates ``paths.error_message`` to clear or set
processing error state, but the column was never part of the schema - every
successful re-analysis raised UndefinedColumn. Added as its own migration so
existing databases are upgraded safely (nullable, no backfill needed).
"""

version = "0005"
name = "paths_error_message"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_name = 'paths' AND column_name = 'error_message'"
        )
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE paths ADD COLUMN error_message TEXT NULL")
