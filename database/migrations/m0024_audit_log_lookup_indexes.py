"""0024: lookup indexes for reading the audit log (Audit Log viewer).

The viewer (core/security/audit_query.py) lists entries newest first with
``ORDER BY id DESC LIMIT n`` under an optional filter. With only m0003's
single-column indexes, PostgreSQL answers a filter on a rare user name, user
id or resource by walking the primary key backwards and discarding rows
until it has n matches - measured by ``tools/perf/audit_log_perf.py`` at
1,000,000 entries: 172 ms (rare user name), 169 ms (rare user id), 141 ms
(rare resource prefix), growing linearly with the log.

* ``(username, id DESC)`` and ``(user_id, id DESC)`` return one person's
  entries already in page order: the page is an index range scan.
* ``resource text_pattern_ops`` serves the viewer's ``LIKE 'prefix%'``
  (a plain b-tree does not, unless the database collation is C).

m0003's ``idx_audit_log_user_id`` is kept: it is released schema and other
code may rely on it. Only indexes are added; no data changes. Downgrade
drops exactly these three.
"""

version = "0024"
name = "audit_log_lookup_indexes"

INDEXES = (
    ("idx_audit_log_username_id", "audit_log (username, id DESC)"),
    ("idx_audit_log_user_id_id", "audit_log (user_id, id DESC)"),
    ("idx_audit_log_resource_prefix", "audit_log (resource text_pattern_ops)"),
)

SQL_STATEMENTS = [f"CREATE INDEX IF NOT EXISTS {name_} ON {target}" for name_, target in INDEXES]

#: Read by tools/perf/audit_log_perf.py to build the same indexes.
AUDIT_LOG_STATEMENTS = SQL_STATEMENTS


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in SQL_STATEMENTS:
            cur.execute(statement)


def downgrade(conn) -> None:
    with conn.cursor() as cur:
        for index_name, _ in INDEXES:
            cur.execute(f"DROP INDEX IF EXISTS {index_name}")
