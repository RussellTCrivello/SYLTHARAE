"""Persist detector output in ``content_signals`` / ``content_signal_runs``.

One implementation used by both paths that produce signals:

* **ingestion** (step 10 of ``ContentDBService.process_document``), inside
  the document's own transaction and a savepoint, and
* **re-detection** (job type ``signal_redetection``), which re-reads stored
  text so a new detector version can be applied to existing content.

Semantics: for a given (content, detector) the stored signals are *replaced*
atomically by the latest run, so a detector upgrade never leaves two
versions' signals side by side. ``ON CONFLICT (dedup_key) DO NOTHING`` makes
concurrent runs on the same content safe. The run row is always written -
including for content with no text and for failed runs - so "not analysed"
can never be mistaken for "nothing found".
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2.extras

from core.criteria.compiler import AccessScope
from core.detection import temporal_intel

logger = logging.getLogger(__name__)

TRIGGER_INGESTION = "ingestion"
TRIGGER_REDETECTION = "redetection"

_INSERT = (
    "INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value, surface,"
    " char_start, char_end, language, calendar, resolution, date_from, date_to,"
    " text_orientation, anchor_date, evidence, dedup_key, method, confidence,"
    " confidence_basis, evidence_sentence, sentence_start, sentence_end) VALUES %s"
    " ON CONFLICT (dedup_key) DO NOTHING RETURNING id"
)

_UPSERT_RUN = (
    "INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status, anchor_date,"
    " chars_total, chars_scanned, signal_count, trigger, job_id, error, ran_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
    " ON CONFLICT (hash_id, detector) DO UPDATE SET detector_ver = EXCLUDED.detector_ver,"
    " status = EXCLUDED.status, anchor_date = EXCLUDED.anchor_date,"
    " chars_total = EXCLUDED.chars_total, chars_scanned = EXCLUDED.chars_scanned,"
    " signal_count = EXCLUDED.signal_count, trigger = EXCLUDED.trigger,"
    " job_id = EXCLUDED.job_id, error = EXCLUDED.error, ran_at = NOW()"
)


def _row(hash_id: int, sig: temporal_intel.TemporalSignal) -> Tuple:
    return (hash_id, temporal_intel.DETECTOR_NAME, sig.detector_ver, sig.signal_type, sig.value,
            sig.surface, sig.char_start, sig.char_end, sig.language, sig.calendar,
            sig.resolution, sig.date_from, sig.date_to, sig.text_orientation, sig.anchor_date,
            psycopg2.extras.Json(sig.evidence), sig.dedup_key(hash_id), sig.method,
            sig.confidence, sig.confidence_basis, sig.sentence, sig.sentence_start,
            sig.sentence_end)


def store_detection(cur, hash_id: int, result: temporal_intel.DetectionResult, *,
                    trigger: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    """Replace this content's temporal signals with ``result`` and record the run."""
    cur.execute("DELETE FROM content_signals WHERE hash_id = %s AND detector = %s",
                (hash_id, temporal_intel.DETECTOR_NAME))
    replaced = cur.rowcount
    inserted = 0
    if result.signals:
        rows = [_row(hash_id, s) for s in result.signals]
        returned = psycopg2.extras.execute_values(cur, _INSERT, rows, page_size=500, fetch=True)
        inserted = len(returned)
    status = "truncated" if result.truncated else "complete"
    cur.execute(_UPSERT_RUN, (hash_id, temporal_intel.DETECTOR_NAME, result.detector_ver, status,
                              result.anchor_date, result.chars_total, result.chars_scanned,
                              inserted, trigger, job_id, None))
    return {"hash_id": hash_id, "status": status, "signals": inserted,
            "duplicates_skipped": len(result.signals) - inserted, "replaced": replaced,
            "truncated": result.truncated}


def record_run(cur, hash_id: int, *, status: str, trigger: str, error: Optional[str] = None,
               anchor_date: Optional[datetime.date] = None, job_id: Optional[str] = None,
               chars_total: Optional[int] = None) -> None:
    """Record a run that produced no signals by design (no text) or failed."""
    if status == "failed" and not error:
        raise ValueError("a failed run must record its error")
    if status == "no_text":
        cur.execute("DELETE FROM content_signals WHERE hash_id = %s AND detector = %s",
                    (hash_id, temporal_intel.DETECTOR_NAME))
    cur.execute(_UPSERT_RUN, (hash_id, temporal_intel.DETECTOR_NAME,
                              temporal_intel.DETECTOR_VERSION, status, anchor_date, chars_total,
                              0 if chars_total is not None else None, 0, trigger, job_id,
                              error[:2000] if error else None))


#: Why no stored date is used as the anchor for relative expressions.
NO_ANCHOR_REASON = (
    "no authored document date is recorded for content: contents.content_date is "
    "the first date *mentioned in the text* (pipeline _extract_date_from_text), and "
    "paths.file_date is a per-occurrence filesystem date - neither is when the "
    "document was written, so relative expressions are left unresolved")


def anchor_for(cur, hash_id: int) -> Tuple[Optional[datetime.date], Optional[str]]:
    """The document's own (authored) date for ``hash_id`` - currently none.

    Returns ``(None, reason)``. Anchoring "tomorrow" on a date merely
    mentioned in the text would fabricate a resolution (a memo citing 1990
    would put "tomorrow" in 1990). When extractors record an authored date
    (e.g. an e-mail ``Date`` header) this is the single place to read it.
    """
    return None, NO_ANCHOR_REASON


def stored_text(cur, hash_id: int) -> Optional[str]:
    """The stored display text (``contents_raw`` chunks in order), or None."""
    cur.execute("SELECT content FROM contents_raw WHERE hash_id = %s ORDER BY chunk_seq",
                (hash_id,))
    rows = cur.fetchall()
    if not rows:
        return None
    return "".join(r[0] for r in rows)


def detect_and_store(cur, hash_id: int, text: Optional[str], anchor_date: Optional[datetime.date],
                     *, trigger: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    if text is None or not text.strip():
        record_run(cur, hash_id, status="no_text", trigger=trigger, anchor_date=anchor_date,
                   job_id=job_id, chars_total=len(text or ""))
        return {"hash_id": hash_id, "status": "no_text", "signals": 0}
    result = temporal_intel.detect(text, anchor_date=anchor_date)
    return store_detection(cur, hash_id, result, trigger=trigger, job_id=job_id)


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


def signals_for(cur, hash_id: int, reference_date: datetime.date, *,
                scope: AccessScope) -> Dict[str, Any]:
    """Signals and run status for one content, with clock orientation computed
    against ``reference_date`` (explicit; never an implicit clock).

    Authorization happens here, before any signal is read: the content must
    exist and - when ``scope`` restricts sources - appear in at least one
    allowed source. Both failures raise the same ``LookupError`` so callers
    cannot distinguish "absent" from "not yours".
    """
    if not isinstance(reference_date, datetime.date):
        raise ValueError("reference_date is required")
    if not isinstance(scope, AccessScope):
        raise TypeError("an AccessScope is required")
    if scope.allowed_source_ids is None:
        cur.execute("SELECT 1 FROM hashs WHERE id = %s", (hash_id,))
    elif not scope.allowed_source_ids:
        raise LookupError(hash_id)
    else:
        cur.execute("SELECT 1 FROM hash_contexts WHERE hash_id = %s"
                    " AND source_id = ANY(%s) LIMIT 1",
                    (hash_id, list(scope.allowed_source_ids)))
    if cur.fetchone() is None:
        raise LookupError(hash_id)
    cur.execute("SELECT detector_ver, status, anchor_date, chars_total, chars_scanned,"
                " signal_count, trigger, job_id, error, ran_at FROM content_signal_runs"
                " WHERE hash_id = %s AND detector = %s",
                (hash_id, temporal_intel.DETECTOR_NAME))
    run = cur.fetchone()
    cur.execute("SELECT signal_type, value, surface, char_start, char_end, language, calendar,"
                " resolution, date_from, date_to, text_orientation, anchor_date, evidence,"
                " detector_ver, method, confidence, confidence_basis, evidence_sentence,"
                " sentence_start, sentence_end FROM content_signals WHERE hash_id = %s AND detector = %s"
                " ORDER BY char_start, char_end, signal_type, value",
                (hash_id, temporal_intel.DETECTOR_NAME))
    signals: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        sig = temporal_intel.TemporalSignal(
            signal_type=row[0], value=row[1], surface=row[2], char_start=row[3],
            char_end=row[4], language=row[5], calendar=row[6], resolution=row[7],
            date_from=row[8], date_to=row[9], text_orientation=row[10], anchor_date=row[11],
            evidence=row[12] or {}, detector_ver=row[13], method=row[14],
            confidence=row[15], confidence_basis=row[16], sentence=row[17],
            sentence_start=row[18], sentence_end=row[19])
        item = sig.to_dict()
        item["clock_orientation"] = (sig.clock_orientation(reference_date)
                                     if sig.signal_type != temporal_intel.SIGNAL_ORIENTATION
                                     else None)
        signals.append(item)
    status = "not_analysed"
    run_info = None
    if run:
        status = run[1]
        run_info = {"detector_ver": run[0], "status": run[1],
                    "anchor_date": run[2].isoformat() if run[2] else None,
                    "chars_total": run[3], "chars_scanned": run[4], "signal_count": run[5],
                    "trigger": run[6], "job_id": run[7], "error": run[8],
                    "ran_at": run[9].isoformat() if run[9] else None,
                    "current_version": run[0] == temporal_intel.DETECTOR_VERSION}
    return {"hash_id": hash_id, "status": status, "run": run_info,
            "reference_date": reference_date.isoformat(), "signals": signals}
