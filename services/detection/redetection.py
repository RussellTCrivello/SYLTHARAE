"""Re-detection: apply the current detector to already-stored content.

Runs as the existing JobManager job type ``signal_redetection`` - not a
second job framework. Each content is its own transaction: one bad document
is recorded as a ``failed`` run with its error and the job continues; the
job finishes ``completed_with_warnings`` (never a silent success) when any
content failed.

Selection (``scope``):

* ``stale``   - content never analysed, analysed by an older detector
  version, or whose last run failed (the default);
* ``all``     - every content with stored text;
* ``hash_ids``- an explicit list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.detection import temporal_intel
from services.detection import signal_store

logger = logging.getLogger(__name__)

SCOPES = ("stale", "all", "hash_ids")
BATCH = 200


@dataclass
class RedetectionResult:
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"result_summary": {
            "detector_ver": temporal_intel.DETECTOR_VERSION, **self.stats,
            "warnings": len(self.warnings), "cancelled": self.cancelled}}


def _select_sql(scope: str):
    if scope == "all":
        return ("SELECT DISTINCT hash_id FROM contents_raw WHERE hash_id > %s"
                " ORDER BY hash_id LIMIT %s", ())
    if scope == "stale":
        return ("SELECT h.id FROM hashs h WHERE h.id > %s AND ("
                " EXISTS (SELECT 1 FROM contents_raw r WHERE r.hash_id = h.id)"
                " OR EXISTS (SELECT 1 FROM contents c WHERE c.hash_id = h.id)) AND NOT EXISTS ("
                "  SELECT 1 FROM content_signal_runs r WHERE r.hash_id = h.id"
                "  AND r.detector = %s AND r.detector_ver = %s AND r.status <> 'failed')"
                " ORDER BY h.id LIMIT %s",
                (temporal_intel.DETECTOR_NAME, temporal_intel.DETECTOR_VERSION))
    raise ValueError(f"scope must be one of {SCOPES}")


def run_redetection(connection_factory: Callable, *, scope: str = "stale",
                    hash_ids: Optional[Sequence[int]] = None, job_id: Optional[str] = None,
                    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
                    cancel_cb: Optional[Callable[[], bool]] = None) -> RedetectionResult:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if scope == "hash_ids":
        ids = sorted({int(h) for h in (hash_ids or [])})
        if not ids:
            raise ValueError("scope 'hash_ids' requires a non-empty hash_ids list")
    result = RedetectionResult(stats={"scope": scope, "processed": 0, "complete": 0,
                                      "truncated": 0, "no_text": 0, "failed": 0,
                                      "signals": 0})

    def batches():
        if scope == "hash_ids":
            for i in range(0, len(ids), BATCH):
                yield ids[i:i + BATCH]
            return
        sql, extra = _select_sql(scope)
        last = 0
        while True:
            conn = connection_factory()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (last, *extra, BATCH))
                    chunk = [r[0] for r in cur.fetchall()]
                conn.rollback()
            finally:
                _release(conn)
            if not chunk:
                return
            yield chunk
            last = chunk[-1]

    for chunk in batches():
        for hash_id in chunk:
            if cancel_cb and cancel_cb():
                result.cancelled = True
                return result
            outcome = redetect_one(connection_factory, hash_id, job_id)
            result.stats["processed"] += 1
            result.stats[outcome["status"]] = result.stats.get(outcome["status"], 0) + 1
            result.stats["signals"] += outcome.get("signals", 0)
            if outcome["status"] == "failed":
                result.warnings.append(f"hash {hash_id}: {outcome['error']}")
            elif outcome["status"] == "truncated":
                result.warnings.append(f"hash {hash_id}: text longer than "
                                       f"{temporal_intel.MAX_SCAN_CHARS} characters; scanned "
                                       "up to the limit")
        if progress_cb:
            progress_cb({"current_phase": "Re-detecting signals",
                         "files_done": result.stats["processed"],
                         "stats": dict(result.stats)})
    return result


def _release(conn) -> None:
    for name in ("release", "close"):
        fn = getattr(conn, name, None)
        if fn:
            try:
                fn()
            except Exception:
                pass
            return


def redetect_one(connection_factory: Callable, hash_id: int,
                 job_id: Optional[str] = None) -> Dict[str, Any]:
    """Detect and store signals for one content in its own transaction.

    Never raises for a detection/database failure: the failure is recorded
    as a ``failed`` run (and logged) and returned as ``{"status": "failed"}``.
    """
    conn = connection_factory()
    try:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM hashs WHERE id = %s", (hash_id,))
                if cur.fetchone() is None:
                    conn.rollback()
                    return {"status": "failed", "error": "content does not exist"}
                anchor, _reason = signal_store.anchor_for(cur, hash_id)
                text = signal_store.stored_text(cur, hash_id)
                outcome = signal_store.detect_and_store(
                    cur, hash_id, text, anchor, trigger=signal_store.TRIGGER_REDETECTION,
                    job_id=job_id)
            conn.commit()
            return outcome
        except Exception as exc:
            conn.rollback()
            message = f"{exc.__class__.__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}"
            logger.error("Re-detection failed for hash %s: %s", hash_id, message)
            try:
                with conn.cursor() as cur:
                    signal_store.record_run(cur, hash_id, status="failed",
                                            trigger=signal_store.TRIGGER_REDETECTION,
                                            error=message, job_id=job_id)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("Could not record the failed run for hash %s", hash_id)
            return {"status": "failed", "error": message}
    finally:
        _release(conn)
