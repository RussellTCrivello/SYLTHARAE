"""Measure report runs on the synthetic perf corpus (step 14).

    python tools/perf/report_run_perf.py <pgserver-dir> [contents]

Uses the corpus of tools/perf/signal_query_perf.py (``syltharae_perf``,
``contents`` files). Times, with the real runner (services/reporting/runs):

1. a broad ``search_results@1`` run (criteria ``{}``: every file) - submit
   and execute, median of 3. The listing is capped at 5,000 rows, so the
   run must record truncation and the exact count must equal the corpus;
2. a narrow run (one source), median of 3;
3. reading a 100-row page of the stored 5,000-row listing at the start and
   at the end (the page is sliced in SQL from the stored JSONB), median of 5;
4. the stored size of the listing (``pg_column_size``).

Synthetic data measures query shape, storage and paging cost, not the
content of a real archive. Each rerun adds its own runs.
"""

import statistics
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(pg_dir: str, contents: int = 20_000) -> None:
    import psycopg2

    from signal_query_perf import load_corpus

    cfg, first_source = load_corpus(pg_dir, contents)

    def connect():
        return psycopg2.connect(host=cfg["host"], port=cfg["port"], user="postgres",
                                dbname=cfg["database"])

    from database.migration_runner import run_migrations
    from services.reporting import runs

    conn = connect()
    run_migrations(conn)          # the perf corpus may predate m0022
    with conn, conn.cursor() as cur:
        name = f"perf_{uuid.uuid4().hex[:6]}"
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x',"
                    " 'analyst') RETURNING id", (name,))
        uid = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM paths")
        paths = cur.fetchone()[0]
    user = SimpleNamespace(id=uid, role="analyst", username=name,
                           has_role=lambda *roles: "analyst" in roles)
    print(f"corpus: {paths} files")

    def run(criteria):
        started = time.perf_counter()
        sub = runs.submit_run(conn, user=user, report_id="search_results",
                              parameters={"criteria": criteria})
        out = runs.execute_run(conn, sub["id"])
        return (time.perf_counter() - started) * 1000, sub["id"], out

    timings, last = [], None
    for _ in range(3):
        ms, run_id, out = run({})
        timings.append(ms)
        last = (run_id, out)
    run_id, out = last
    listing, count = out["datasets"]
    matched = runs.dataset_rows(conn, run_id, "search_results.count@1", user=user)["rows"][0]["matched"]
    print(f"1. broad run: median {statistics.median(timings):.0f} ms (runs "
          f"{[round(t) for t in timings]}), status={out['status']}, listing rows="
          f"{listing['row_count']} truncated={listing['truncated']}, exact count={matched}")
    assert out["status"] == "completed" and listing["truncated"] and matched == paths

    timings = []
    for _ in range(3):
        ms, narrow_id, nout = run({"sources": [first_source]})
        timings.append(ms)
    print(f"2. narrow run (one source): median {statistics.median(timings):.0f} ms, listing rows="
          f"{nout['datasets'][0]['row_count']} truncated={nout['datasets'][0]['truncated']}")

    for label, offset in (("start", 0), ("end", listing["row_count"] - 100)):
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            page = runs.dataset_rows(conn, run_id, "search_results.matches@1", user=user,
                                     limit=100, offset=offset)
            timings.append((time.perf_counter() - started) * 1000)
        assert len(page["rows"]) == 100
        print(f"3. 100-row page at the {label} (offset {offset}): median "
              f"{statistics.median(timings):.1f} ms")

    with conn.cursor() as cur:
        cur.execute("SELECT pg_column_size(rows) FROM report_run_datasets WHERE run_id = %s"
                    " AND dataset_key = 'search_results.matches@1'", (run_id,))
        size = cur.fetchone()[0]
    conn.rollback()
    print(f"4. stored listing: {size / 1024:.0f} KiB for {listing['row_count']} rows")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20_000)
