"""Notification query indexes for accuracy at scale.

Every notification read path added by the accuracy work is now a targeted
SQL query instead of a full scan of ``alerts``:

* ``idx_alerts_metadata_hash``
    the scan endpoint's dedup preload
    (``type = 'similar_files' AND metadata->>'hash' ...``) becomes an
    index scan instead of a sequential scan of every alert.
* ``idx_alerts_dismissed_created``
    the main list query
    (``dismissed = FALSE ORDER BY created_at DESC``) walks the index in
    order, so the first page reads O(page) rows, not O(table).
* ``idx_alerts_type_dismissed_created``
    typed pages (``type IN (...) AND dismissed = FALSE ORDER BY created_at
    DESC``) use the same trick with the type as the leading column.
* ``idx_alerts_type_event_date``
    upcoming-event counts and the future-date scan guard
    (``type = ... AND event_date BETWEEN/= ...``).

Without these, exact COUNT/pagination queries degrade into sequential
scans as the alerts table grows - which is precisely the volume scenario
the notification system must stay bounded in.

Numbered 0014: versions 0011-0013 are already taken on this lineage
(content identity, translation overrides, relationship deduplication).
"""

version = "0014"
name = "alerts_notification_indexes"

SQL_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_alerts_metadata_hash "
    "ON alerts ((metadata->>'hash')) WHERE type = 'similar_files'",
    "CREATE INDEX IF NOT EXISTS idx_alerts_dismissed_created "
    "ON alerts (dismissed, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_type_dismissed_created "
    "ON alerts (type, dismissed, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_type_event_date "
    "ON alerts (type, event_date)",
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        for index_name in (
            "idx_alerts_metadata_hash",
            "idx_alerts_dismissed_created",
            "idx_alerts_type_dismissed_created",
            "idx_alerts_type_event_date",
        ):
            cur.execute(f"DROP INDEX IF EXISTS {index_name}")
