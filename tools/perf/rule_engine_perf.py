"""Measure monitoring-rule evaluation on the synthetic perf corpus.

    python tools/perf/rule_engine_perf.py <pgserver-dir> [contents]

Uses the corpus of tools/perf/signal_query_perf.py (``syltharae_perf``:
``contents`` contents x 10 dated signals + 2 place signals). Times, with the
real engine (services/monitoring/rule_engine.evaluate_rule):

1. a broad rule's first evaluation (every matching signal enters the ledger
   as the baseline),
2. its steady-state re-evaluation when nothing is new (median of 5) - the
   cost of the full anti-join documented in rule_engine.py,
3. an evaluation after 1,000 new signals arrive (grouped by content; the
   per-evaluation notification cap and overflow summary apply),
4. a narrow rule (one source, high confidence, 30-day window) steady state,

and prints the plan of the match statement. Synthetic data measures query
shape and index use, not detector behaviour.
"""

import datetime
import statistics
import sys
import time
import uuid
from pathlib import Path

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

    from services.monitoring import rule_engine, rules

    conn = connect()
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x',"
                    " 'analyst') RETURNING id", (f"perf_{uuid.uuid4().hex[:6]}",))
        owner = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM content_signals WHERE signal_type = 'date_reference'")
        dated = cur.fetchone()[0]
    print(f"corpus: {dated} dated signals")
    now = datetime.datetime(2026, 10, 1, 12, tzinfo=datetime.timezone.utc)

    def timed(rule, when):
        started = time.perf_counter()
        out = rule_engine.evaluate_rule(conn, rule["id"], now=when)
        return (time.perf_counter() - started) * 1000, out

    broad = rules.create_rule(conn, owner_id=owner, is_admin=False,
                              name=f"perf broad {uuid.uuid4().hex[:6]}",
                              definition={"signals": {"signal_types": ["date_reference"]},
                                          "group_by": "content"})
    ms, out = timed(broad, now)
    print(f"1. baseline evaluation: {ms:.0f} ms, counts={out['counts']}")
    runs = []
    for i in range(5):
        ms, out = timed(broad, now + datetime.timedelta(minutes=i + 1))
        runs.append(ms)
    print(f"2. steady state, nothing new: median {statistics.median(runs):.0f} ms"
          f" (runs {[round(r) for r in runs]}), counts={out['counts']}")

    with conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value,
                surface, char_start, char_end, language, calendar, resolution, date_from,
                date_to, evidence, dedup_key, method, confidence, confidence_basis,
                evidence_sentence, sentence_start, sentence_end)
            SELECT h.id, 'temporal', 'temporal-1.1.0', 'date_reference', %s || h.id,
                'new date', 900, 908, 'en', 'gregorian', 'absolute', DATE '2026-10-03',
                DATE '2026-10-03', '{}', md5('new' || h.id || now()) || md5(h.id || 'new'),
                'day_month_year', 'high', 'basis', 'new date', 900, 908
            FROM hashs h ORDER BY h.id LIMIT 1000""", (f"new-{uuid.uuid4().hex[:8]}-",))
        # A distinct value per run: the same (content, detector, type, value,
        # span) as an earlier run would be a known subject, not a new one.
    ms, out = timed(broad, now + datetime.timedelta(minutes=10))
    print(f"3. after 1,000 new signals: {ms:.0f} ms, counts={out['counts']}")

    narrow = rules.create_rule(conn, owner_id=owner, is_admin=False,
                               name=f"perf narrow {uuid.uuid4().hex[:6]}",
                               definition={"criteria": {"sources": [first_source]},
                                           "signals": {"signal_types": ["date_reference"]},
                                           "min_confidence": "high",
                                           "event_window_days": {"from": 0, "to": 30}})
    timed(narrow, now)
    runs = [timed(narrow, now + datetime.timedelta(minutes=i + 1))[0] for i in range(5)]
    print(f"4. narrow rule steady state: median {statistics.median(runs):.0f} ms"
          f" (runs {[round(r) for r in runs]})")

    # The match statement's plan (broad rule), run inside a rolled-back transaction.
    from core.criteria.access import scope_for
    from services.detection import signal_query
    from services.monitoring.rule_model import definition_from_stored
    from types import SimpleNamespace

    rule = rules.fetch_rule(conn.cursor(cursor_factory=__import__(
        "psycopg2.extras").extras.RealDictCursor), broad["id"])
    definition = definition_from_stored(rule["definition"])
    compiled = signal_query._compile(definition.criteria,
                                     scope_for(SimpleNamespace(id=owner, role="analyst")))
    conds, params = rule_engine._conditions(definition, compiled, now.date())
    with conn.cursor() as cur:
        cur.execute("SELECT max(id) FROM rule_evaluations WHERE rule_id = %s", (broad["id"],))
        eid = cur.fetchone()[0]
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + rule_engine._match_sql(
            definition, conds), [broad["id"], rule["version"], "pending", now, eid, now] + params
            + [broad["id"]])
        print("\nplan of the match statement (broad rule, nothing new):")
        for (line,) in cur.fetchall():
            print("  " + line)
    conn.rollback()
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20_000)
