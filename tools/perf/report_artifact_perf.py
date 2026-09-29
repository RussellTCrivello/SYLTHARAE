"""Measure report artifacts on the synthetic perf corpus (step 15).

    python tools/perf/report_artifact_perf.py <pgserver-dir> [contents]

Uses the corpus of tools/perf/signal_query_perf.py. Runs one broad
``search_results@1`` report (5,000 stored rows, capped) with the real runner,
then, with the real artifact service (services/reporting/artifacts):

1. creates every format (json, html, xlsx, csv of the listing) - render,
   digest, manifest and insert - and reports time and size per format;
2. a repeated request for the same rendering (must return the existing
   artifact without rendering), median of 5;
3. reading one artifact for download including SHA-256 re-verification,
   median of 5;
4. rendering the same run document twice in memory and comparing bytes
   (determinism at 5,000 rows).

Synthetic data measures rendering and storage cost, not a real archive.
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
    import psycopg2.extras

    from signal_query_perf import load_corpus

    cfg, _ = load_corpus(pg_dir, contents)

    def connect():
        return psycopg2.connect(host=cfg["host"], port=cfg["port"], user="postgres",
                                dbname=cfg["database"])

    from core.reporting import render
    from database.migration_runner import run_migrations
    from services.reporting import artifacts, runs

    conn = connect()
    run_migrations(conn)
    with conn, conn.cursor() as cur:
        name = f"perf_{uuid.uuid4().hex[:6]}"
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x',"
                    " 'analyst') RETURNING id", (name,))
        uid = cur.fetchone()[0]
    user = SimpleNamespace(id=uid, role="analyst", username=name,
                           has_role=lambda *roles: "analyst" in roles)
    sub = runs.submit_run(conn, user=user, report_id="search_results",
                          parameters={"criteria": {}})
    out = runs.execute_run(conn, sub["id"])
    assert out["status"] == "completed"
    rid = sub["id"]
    print(f"run {rid}: listing rows={out['datasets'][0]['row_count']} "
          f"truncated={out['datasets'][0]['truncated']}")

    made = {}
    for fmt, key in (("json", None), ("html", None), ("xlsx", None),
                     ("csv", "search_results.matches@1")):
        started = time.perf_counter()
        result = artifacts.create_artifact(conn, run_id=rid, fmt=fmt, dataset_key=key,
                                           creator_id=uid)
        ms = (time.perf_counter() - started) * 1000
        assert result["status"] == "created", result
        made[fmt] = result["artifact"]
        print(f"1. {fmt}: {ms:.0f} ms, {result['artifact']['byte_size'] / 1024:.0f} KiB")

    timings = []
    for _ in range(5):
        started = time.perf_counter()
        plan = artifacts.request_artifact(conn, rid, user=user, fmt="xlsx")
        timings.append((time.perf_counter() - started) * 1000)
        assert "existing" in plan
    print(f"2. repeated request (existing, no rendering): median {statistics.median(timings):.1f} ms")

    timings = []
    for _ in range(5):
        started = time.perf_counter()
        row = artifacts.artifact_for_download(conn, made["xlsx"]["id"], user=user)
        timings.append((time.perf_counter() - started) * 1000)
    print(f"3. read + verify xlsx for download ({len(row['content']) / 1024:.0f} KiB): median "
          f"{statistics.median(timings):.1f} ms")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT " + runs._RUN_COLUMNS + " FROM report_runs WHERE id = %s", (rid,))  # nosec B608 # developer benchmark; _RUN_COLUMNS is a constant
        run_row = cur.fetchone()
    document = artifacts.run_document(conn, run_row)
    conn.rollback()
    same = all(render.render(document, f, "search_results.matches@1" if f == "csv" else None).content
               == render.render(document, f, "search_results.matches@1" if f == "csv" else None).content
               for f in render.RENDERERS)
    print(f"4. deterministic at {out['datasets'][0]['row_count']} rows: {same}")
    assert same
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20_000)
