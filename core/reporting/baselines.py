"""Per-user Latest/Change baselines: how far each reader has read.

The Latest and Change reports must distinguish three things that look
similar and are not (reporting requirement: Latest and Change baselines):

* *recently created* - the document arrived recently; a property of the
  data, the same for every reader;
* *previously seen*  - this reader's baseline already covers it;
* *new since the last view* - the row exists now with an id beyond the
  furthest this reader had seen on this view before.

This module owns that memory: one row per (reader, view) in
``report_baselines`` (migration 0027). A *view* is a set of criteria plus
the parameters that shape the listing; its identity is the SHA-256 of the
criteria fingerprint and those parameters - two readers of the same view
have independent baselines, and one reader's two views never mix.

Discipline:

* every statement is parameterized; the hash is computed here, never
  interpolated;
* ``record`` is monotonic: a baseline never moves backwards, so a stale
  client cannot make a reader un-see rows (the upsert keeps the greater
  ``last_max_id`` and the newer ``last_seen_at``);
* "never viewed" is an absent row - the caller distinguishes it from
  "viewed and saw nothing" (a baseline with ``last_max_id = 0``), and
  neither is ever read as a zero measurement.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple

from Api.utils import execute_query


def baseline_key(criteria_fingerprint: str, params: Dict[str, Any] = ()) -> str:
    """The view's identity: SHA-256 over the fingerprint and the sorted,
    JSON-shaped parameters that differentiate views of the same criteria."""
    import json

    canonical = json.dumps(
        {"fingerprint": criteria_fingerprint,
         "params": [(str(k), repr(v)) for k, v in sorted(dict(params).items())]},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_baseline(user_id: int, key: str) -> Optional[Dict[str, Any]]:
    """The reader's progress on this view, or ``None`` when never viewed."""
    row = execute_query(
        "SELECT last_seen_at, last_max_id FROM report_baselines "
        "WHERE user_id = %s AND criteria_hash = %s",
        (user_id, key),
        fetch="one",
    )
    if not row:
        return None
    return {"last_seen_at": row[0], "last_max_id": int(row[1] or 0)}


def record_baseline(user_id: int, key: str, max_id: int,
                    conn=None) -> Dict[str, Any]:
    """Advance the reader's baseline to ``max_id`` - never backwards.

    A concurrent second session with a smaller id must not un-see rows the
    first session already recorded, so the upsert keeps the greater id and
    the newer timestamp. Returns the row as it now stands. ``conn``, when
    given, is used instead of the pooled helper (the report runner advances
    the baseline on its own connection, after the run's read-only snapshot
    transaction has closed).
    """
    sql = (
        "INSERT INTO report_baselines (user_id, criteria_hash, last_seen_at,"
        " last_max_id) VALUES (%s, %s, NOW(), %s)"
        " ON CONFLICT (user_id, criteria_hash) DO UPDATE"
        " SET last_seen_at = NOW(),"
        "     last_max_id = GREATEST(report_baselines.last_max_id,"
        " EXCLUDED.last_max_id)"
        " RETURNING last_seen_at, last_max_id")
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, key, int(max_id)))
            row = cur.fetchone()
    else:
        row = execute_query(sql, (user_id, key, int(max_id)), fetch="one")
    return {"last_seen_at": row[0], "last_max_id": int(row[1] or 0)}
