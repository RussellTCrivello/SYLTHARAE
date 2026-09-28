"""Measure the audit log viewer's queries on a large synthetic audit_log.

    python tools/perf/audit_log_perf.py <socket-dir> [rows] [--extra-sql FILE]

Creates (or recreates) a database ``audit_perf`` on the PostgreSQL server
listening in <socket-dir>, creates ``audit_log`` with the statements of the
real migrations (m0003, then any later migration that touches it), inserts
<rows> synthetic entries (default 1,000,000) in one set-based INSERT, runs
ANALYZE and times ``core.security.audit_query.list_entries`` for each filter
shape the page can send. Each figure is the median of 5 runs after one
warm-up; the plan's top node and the index it used are printed alongside.

Distribution: 12 actions (DATA_EXPORTED ~25 %, a rare one at 0.01 %),
200 user names (one rare: 0.005 %), resources ``kind:N`` with 6 kinds,
timestamps spread over 2 years.
"""

import importlib
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main(socket_dir, rows=1_000_000):
    import psycopg2

    from core.security import audit_query as aq

    admin = psycopg2.connect(host=socket_dir, user="postgres", dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS audit_perf")
        cur.execute("CREATE DATABASE audit_perf")
    admin.close()

    conn = psycopg2.connect(host=socket_dir, user="postgres", dbname="audit_perf")
    m0003 = importlib.import_module("database.migrations.m0003_auth_tables")
    with conn.cursor() as cur:
        for sql in m0003.SQL_STATEMENTS:
            if "audit_log" in sql:
                cur.execute(sql)
        for mod in sorted(p.stem for p in (ROOT / "database" / "migrations").glob("m0*.py")):
            m = importlib.import_module(f"database.migrations.{mod}")
            for sql in getattr(m, "AUDIT_LOG_STATEMENTS", ()):
                cur.execute(sql)
        t0 = time.perf_counter()
        cur.execute("""
            INSERT INTO audit_log (user_id, username, action, resource, detail, ip_address, created_at)
            SELECT CASE WHEN g %% 20000 = 0 THEN 999 ELSE g %% 199 + 1 END,
                   CASE WHEN g %% 20000 = 0 THEN 'rare_user' ELSE 'user_' || (g %% 199) END,
                   CASE WHEN g %% 10000 = 0 THEN 'rare.action'
                        WHEN g %% 4 = 0 THEN 'DATA_EXPORTED'
                        ELSE (ARRAY['login.success','report.run','report.artifact','rule.updated',
                                    'scenario.updated','login.failed','password.change',
                                    'rule.created','scenario.created','scenario.dry_run'])[g %% 10 + 1]
                   END,
                   (ARRAY['export:report_artifact:report_run:','export:report_manifest:report_run:',
                          'report_run:','rule:','scenario:','export:search:'])[g %% 6 + 1] || (g %% 5000),
                   jsonb_build_object('n', g, 'sha256', md5(g::text) || md5((g + 1)::text)),
                   '10.0.' || (g %% 250) || '.' || (g %% 200),
                   TIMESTAMPTZ '2028-01-01' + (g * (INTERVAL '2 years' / %s))
            FROM generate_series(1, %s) AS g""", (rows, rows))
        cur.execute("ANALYZE audit_log")
    conn.commit()
    print(f"inserted {rows:,} rows in {time.perf_counter() - t0:.1f} s")
    with conn.cursor() as cur:
        cur.execute("SELECT min(id), max(id) FROM audit_log")
        lo, hi = cur.fetchone()
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_log' ORDER BY 1")
        print("indexes:", ", ".join(r[0] for r in cur.fetchall()))
    conn.commit()

    cases = [
        ("first page, no filter", {}),
        ("deep page (before_id = mid)", {"before_id": str((lo + hi) // 2)}),
        ("action = DATA_EXPORTED (25 %)", {"action": "DATA_EXPORTED"}),
        ("action = rare.action (0.01 %)", {"action": "rare.action"}),
        ("username = user_7 (0.5 %)", {"username": "user_7"}),
        ("username = rare_user (0.005 %)", {"username": "rare_user"}),
        ("user_id = 999 (0.005 %)", {"user_id": "999"}),
        ("resource prefix export:report_manifest (17 %)", {"resource": "export:report_manifest"}),
        ("resource prefix report_run:4999 (no match)", {"resource": "report_run:4999"}),
        ("resource prefix rule:4999 (rare, matches)", {"resource": "rule:4999"}),
        ("window: one day", {"since": "2029-01-01T00:00:00Z", "until": "2029-01-02T00:00:00Z"}),
        ("window: one day + DATA_EXPORTED", {"since": "2029-01-01T00:00:00Z",
                                             "until": "2029-01-02T00:00:00Z", "action": "DATA_EXPORTED"}),
        ("empty result (future window)", {"since": "2999-01-01T00:00:00Z"}),
    ]
    print(f"\n{'case':50} {'median ms':>10} {'rows':>5}  plan")
    for name, args in cases:
        filters = aq.parse_filters(dict(args, limit="50"))
        aq.list_entries(conn, filters)
        times = []
        for _ in range(5):
            t = time.perf_counter()
            try:
                page = aq.list_entries(conn, filters)
                times.append((time.perf_counter() - t) * 1000)
            except aq.AuditQueryError as exc:
                times.append(float("inf"))
                page = {"items": [], "error": exc.code}
        plan = explain(conn, filters)
        shown = "TIMEOUT" if page.get("error") else f"{statistics.median(times):10.1f}"
        print(f"{name:50} {shown:>10} {len(page['items']):5}  {plan}")
    aq.actions(conn)
    times = []
    for _ in range(5):
        t = time.perf_counter()
        names = aq.actions(conn)["items"]
        times.append((time.perf_counter() - t) * 1000)
    print(f"{'actions menu (loose index scan)':50} {statistics.median(times):10.1f} {len(names):5}")
    conn.close()


def explain(conn, filters):
    """The plan nodes (with index names) of the query list_entries issues."""
    import psycopg2.extras

    from core.security import audit_query as aq

    captured = {}
    real = aq._read

    def spy(c, sql, params):
        captured["sql"], captured["params"] = sql, params
        return real(c, sql, params)

    aq._read = spy
    try:
        aq.list_entries(conn, filters)
    finally:
        aq._read = real
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("EXPLAIN (FORMAT JSON) " + captured["sql"], captured["params"])
        plan = cur.fetchone()[0][0]["Plan"]
    conn.rollback()
    nodes = []

    def walk(node):
        label = node["Node Type"] + (f" {node['Index Name']}" if "Index Name" in node else "")
        nodes.append(label)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return " > ".join(nodes)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000)
