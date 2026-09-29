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


def record_baseline(user_id: int, key: str, max_id: int) -> Dict[str, Any]:
    """Advance the reader's baseline to ``max_id`` - never backwards.

    A concurrent second session with a smaller id must not un-see rows the
    first session already recorded, so the upsert keeps the greater id and
    the newer timestamp. Returns the row as it now stands.
    """
    row = execute_query(
        """
        INSERT INTO report_baselines (user_id, criteria_hash, last_seen_at, last_max_id)
        VALUES (%s, %s, NOW(), %s)
        ON CONFLICT (user_id, criteria_hash) DO UPDATE
        SET last_seen_at = NOW(),
            last_max_id = GREATEST(report_baselines.last_max_id, EXCLUDED.last_max_id)
        RETURNING last_seen_at, last_max_id
        """,
        (user_id, key, int(max_id)),
        fetch="one",
    )
    return {"last_seen_at": row[0], "last_max_id": int(row[1] or 0)}


def classify_rows(rows: Tuple[Dict[str, Any], ...], baseline: Optional[Dict[str, Any]],
                  created_within_days: int = 30,
                  now_id_floor: int = 0) -> Tuple[Dict[str, Any], ...]:
    """Attach ``view_state`` to each row of a Latest listing.

    ``rows`` carry ``path_id`` and, when the row has it, ``date_creation``.
    The states are exactly the three the report must distinguish:

    * ``new_since_last_view`` - beyond the reader's recorded progress
      (everything, when the reader has no baseline yet - first view of this
      view is new by definition, and is *not* read as "zero seen");
    * ``previously_seen``     - at or before the recorded progress;
    * ``recently_created``    - ingested within the window, regardless of
      reading progress (a recently-created row the reader already saw is
      both; ``recently_created`` is reported alongside, not instead).

    Pure function: no database access, no baseline mutation.
    """
    import datetime

    seen_floor = baseline["last_max_id"] if baseline else None
    today = datetime.date.today()
    window_start = today - datetime.timedelta(days=created_within_days)
    classified = []
    for row in rows:
        item = dict(row)
        path_id = int(item.get("path_id") or 0)
        item["view_state"] = (
            "previously_seen"
            if seen_floor is not None and path_id <= seen_floor
            else "new_since_last_view")
        created = item.get("date_creation")
        if created:
            if hasattr(created, "date"):
                created = created.date()
            if isinstance(created, datetime.date) and created >= window_start:
                item["recently_created"] = True
            else:
                item["recently_created"] = False
        classified.append(item)
    return tuple(classified)
