"""Evaluate monitoring rules into notifications.

One evaluation of one rule is **one transaction** (READ COMMITTED). The
transaction takes ``pg_try_advisory_xact_lock(RULE_LOCK_CLASS, rule_id)``, so
two evaluations of the same rule never interleave; a second one records
``skipped_busy`` and stops instead of waiting. Steps:

1. **Owner check.** The owner is re-read from ``users`` *now*. An owner who
   is inactive or no longer analyst/admin gets the rule ``disabled`` (with
   the reason), the evaluation is recorded as ``owner_revoked``, one
   ``rule_status`` notification tells the owner why - and nothing is matched
   or notified.
2. **Scope.** The criteria are compiled by the single criteria compiler
   with the owner's access scope (``core.criteria.access.scope_for``), so a
   rule can only ever match content its owner may read, as of this
   evaluation. The scope is recorded with the evaluation.
3. **Match** - one set-based statement: every signal satisfying the rule is
   inserted into ``rule_subject_ledger`` with ``ON CONFLICT DO NOTHING``.
   The ledger's primary key ``(rule_id, subject_key)`` is the deduplication:
   whatever was matched before is not matched again. ``subject_key`` does
   not use ``content_signals.dedup_key``, which contains the detector
   version - re-detection with a new detector version would re-notify the
   whole corpus. It hashes ``(hash_id, detector, signal_type, value,
   char_start, char_end, anchor_date)`` instead (``content:<hash_id>`` for
   ``unit = content``). A detector upgrade that *changes* a finding (another
   value or span) is a new subject - that is a new finding.
   New subjects enter as ``baseline`` (first evaluation of a version, unless
   ``notify_existing``), ``suppressed`` (rule suppressed now) or ``pending``.
4. **Deliver.** Pending subjects are notified when every gate is open, in
   this order, each recorded in ``counts`` when it holds delivery back:
   threshold (enough pending subjects inside the window; older pending ones
   become ``expired``), cooldown (time since the last notification), digest
   interval. Delivery writes the notifications with ``insert_alerts`` in the
   same transaction and marks the subjects ``notified`` with the alert id.
   Immediate delivery writes one notification per group, at most
   ``MAX_ALERTS_PER_EVALUATION``; when there are more groups the last
   notification is an explicit overflow summary covering all the rest -
   nothing is dropped silently.
5. The evaluation row gets its counts, and the transaction commits. On any
   error the whole transaction is rolled back (no half-delivered
   evaluation) and a ``failed`` evaluation is recorded in a new one.

Scaling limit (stated, not hidden): step 3 re-reads every signal the rule
matches, then lets the primary key discard known ones. Its cost grows with
the number of matching signals, not with what is new. An id watermark would
make it incremental, but ``content_signals`` ids are assigned before commit,
so a watermark can skip rows committed out of order; the full anti-join is
correct under concurrency. Measured cost: tools/perf/rule_engine_perf.py.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import psycopg2.extras

from core.criteria.access import scope_for
from core.monitoring import priority as priority_rules
from core.monitoring.notification_service import (
    Notification, NotificationPriority, NotificationType, insert_alerts)
from services.detection import signal_query
from services.monitoring import rules as rule_store
from services.monitoring.rule_model import CONFIDENCE_LEVELS, definition_from_stored

logger = logging.getLogger(__name__)

#: First key of the two-key advisory lock ("RULE" in ASCII).
RULE_LOCK_CLASS = 0x52554C45
MAX_ALERTS_PER_EVALUATION = 50
MAX_SUBJECTS_PER_ALERT = 20
MAX_GROUPS_IN_DIGEST = 20
EVIDENCE_SENTENCE_LIMIT = 300
TRIGGERS = ("manual", "ingestion", "redetection", "all_rules", "schedule")
STATEMENT_TIMEOUT_MS = 120_000

_CONF_RANK_SQL = ("CASE s.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2"
                  " WHEN 'low' THEN 1 ELSE 0 END")
_SIGNAL_SUBJECT_SQL = (
    "'signal:' || encode(sha256(convert_to(jsonb_build_array(s.hash_id, s.detector,"
    " s.signal_type, s.value, s.char_start, s.char_end, s.anchor_date)::text, 'UTF8')), 'hex')")
def _evidence_sql(a: str) -> str:
    """The evidence kept for a subject, built from alias ``a``'s columns."""
    return (
        f"jsonb_build_object('signal_type', {a}.signal_type, 'value', {a}.value,"
        f" 'surface', {a}.surface, 'detector', {a}.detector, 'detector_ver', {a}.detector_ver,"
        f" 'language', {a}.language, 'calendar', {a}.calendar, 'resolution', {a}.resolution,"
        f" 'method', {a}.method, 'text_orientation', {a}.text_orientation,"
        f" 'char_start', {a}.char_start, 'char_end', {a}.char_end,"
        f" 'evidence_sentence', left({a}.evidence_sentence, {EVIDENCE_SENTENCE_LIMIT}),"
        f" 'evidence_sentence_truncated',"
        f" COALESCE(length({a}.evidence_sentence) > {EVIDENCE_SENTENCE_LIMIT}, FALSE))")


class RuleEvaluationError(RuntimeError):
    pass


@dataclass
class RuleEvaluationResult:
    """Job result (the shape JobManager reads)."""
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False
    evaluations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"result_summary": {**self.stats, "evaluations": self.evaluations[:100],
                                   "cancelled": self.cancelled}}


def _group_key_sql(definition) -> str:
    if definition.group_by == "content":
        return "'content:' || s.hash_id"
    if definition.group_by == "value":
        return "'value:' || md5(s.signal_type || ':' || s.value)"
    return None  # = subject_key


def _match_sql(definition, conds: List[str]) -> str:
    """The INSERT ... SELECT that records new subjects. Parameters, in order:
    rule_id, rule_version, state, now, evaluation_id, now, then ``conds``',
    then rule_id again.

    Known subjects are filtered out (``NOT EXISTS`` on the ledger's primary
    key) *before* their evidence is built - building it for every matching
    signal and letting ``ON CONFLICT`` discard it cost ~2 s per evaluation at
    200k matches (tools/perf/rule_engine_perf.py). ``ON CONFLICT DO NOTHING``
    stays: it is what makes two concurrent evaluations safe."""
    where = " AND ".join(conds) if conds else "TRUE"
    group = _group_key_sql(definition)
    if definition.unit == "content":
        candidates = (
            "SELECT DISTINCT ON (s.hash_id) 'content:' || s.hash_id AS subject_key,"  # nosec B608 # fixed fragments from reviewed code and signal_query._where; values are bound parameters
            " 'content:' || s.hash_id AS group_key, s.*,"
            " count(*) OVER (PARTITION BY s.hash_id) AS matching_signals"
            f" FROM content_signals s WHERE {where}"
            f" ORDER BY s.hash_id, {_CONF_RANK_SQL} DESC, s.date_from ASC NULLS LAST, s.id ASC")
        evidence = (f"{_evidence_sql('m')} || jsonb_build_object('matching_signals',"
                    " m.matching_signals)")
    else:
        candidates = (
            f"SELECT {_SIGNAL_SUBJECT_SQL} AS subject_key,"  # nosec B608 # fixed fragments from reviewed code and signal_query._where; values are bound parameters
            f" {group or _SIGNAL_SUBJECT_SQL} AS group_key, s.*"
            f" FROM content_signals s WHERE {where}")
        evidence = _evidence_sql("m")
    return (
        "INSERT INTO rule_subject_ledger (rule_id, subject_key, hash_id, signal_id,"  # nosec B608 # fixed fragments from reviewed code and signal_query._where; values are bound parameters
        " rule_version, state, group_key, confidence, event_date_from, event_date_to,"
        " evidence, first_matched_at, matched_evaluation_id, state_changed_at)"
        " SELECT %s, m.subject_key, m.hash_id, m.id, %s, %s, m.group_key,"
        f" m.confidence, m.date_from, m.date_to, {evidence}, %s, %s, %s"
        f" FROM ({candidates}) m"
        " WHERE NOT EXISTS (SELECT 1 FROM rule_subject_ledger known"
        " WHERE known.rule_id = %s AND known.subject_key = m.subject_key)"
        " ON CONFLICT (rule_id, subject_key) DO NOTHING")


def _conditions(definition, compiled, reference_date: datetime.date):
    conds, params = signal_query._where(definition.signals, compiled)
    if definition.min_confidence:
        floor = CONFIDENCE_LEVELS.index(definition.min_confidence)
        conds.append("s.confidence = ANY(%s)")
        params.append(list(CONFIDENCE_LEVELS[floor:]))
    if definition.event_window_days:
        lo, hi = definition.event_window_days
        conds.append("s.date_to >= %s AND s.date_from <= %s")
        params.extend([reference_date + datetime.timedelta(days=lo),
                       reference_date + datetime.timedelta(days=hi)])
    return conds, params


def _record(cur, rule: Dict[str, Any], *, status: str, trigger: str, job_id, now,
            reference_date, owner_role=None, scope=None, criteria_fingerprint=None,
            counts=None, error=None, finished=True) -> int:
    cur.execute(
        "INSERT INTO rule_evaluations (rule_id, rule_version, trigger, job_id, status,"
        " owner_role, access_scope, criteria_fingerprint, definition_fingerprint,"
        " reference_date, evaluated_at, counts, error, finished_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " CASE WHEN %s THEN clock_timestamp() END) RETURNING id",
        (rule["id"], rule["version"], trigger, job_id, status, owner_role,
         psycopg2.extras.Json(scope) if scope is not None else None, criteria_fingerprint,
         rule["definition_fingerprint"], reference_date, now,
         psycopg2.extras.Json(counts or {}), error, finished))
    return cur.fetchone()["id"]


def _scope_json(scope) -> Dict[str, Any]:
    return {"user_id": scope.user_id, "role": scope.role,
            "allowed_source_ids": (list(scope.allowed_source_ids)
                                   if scope.allowed_source_ids is not None else None),
            "suppressed_source_count": scope.suppressed_source_count}


def _subjects(cur, rule_id: int, group_key: Optional[str]) -> List[Dict[str, Any]]:
    cur.execute(
        "SELECT subject_key, hash_id, signal_id, confidence, event_date_from, event_date_to,"  # nosec B608 # fixed fragments from reviewed code and signal_query._where; values are bound parameters
        " evidence, first_matched_at FROM rule_subject_ledger"
        " WHERE rule_id = %s AND state = 'pending'"
        + (" AND group_key = %s" if group_key is not None else "")
        + " ORDER BY first_matched_at, subject_key LIMIT %s",
        (rule_id, group_key, MAX_SUBJECTS_PER_ALERT) if group_key is not None
        else (rule_id, MAX_SUBJECTS_PER_ALERT))
    out = []
    for r in cur.fetchall():
        out.append({"subject_key": r["subject_key"], "hash_id": r["hash_id"],
                    "signal_id": r["signal_id"], "confidence": r["confidence"],
                    "event_date_from": rule_store._iso(r["event_date_from"]),
                    "event_date_to": rule_store._iso(r["event_date_to"]),
                    "first_matched_at": rule_store._iso(r["first_matched_at"]),
                    **{k: r["evidence"].get(k) for k in (
                        "signal_type", "value", "surface", "language", "calendar",
                        "evidence_sentence", "evidence_sentence_truncated",
                        "matching_signals")}})
    return out


_SUMMARY_COLUMNS = (
    " count(*) AS n, min(first_matched_at) AS first_at,"
    " max(CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1"
    " ELSE 0 END) AS best,"
    " count(*) FILTER (WHERE confidence IS NULL) AS unrecorded,"
    " bool_or(confidence = 'high' AND "
    + priority_rules.imminent_sql("event_date_from", "event_date_to") + ") AS high_imminent,"
    " min(event_date_from) AS earliest"
    " FROM rule_subject_ledger WHERE rule_id = %s AND state = 'pending'")
#: Per group (append GROUP BY ...) and over every pending subject. Both take
#: R, R + 7 days and the rule id.
_GROUP_SUMMARY_SQL = "SELECT group_key," + _SUMMARY_COLUMNS
_TOTAL_SUMMARY_SQL = "SELECT" + _SUMMARY_COLUMNS

_RANK_LABEL = {3: "high", 2: "medium", 1: "low", 0: None}


def _summary_from_row(row) -> Dict[str, Any]:
    return {"high_and_imminent": bool(row["high_imminent"]),
            "highest_confidence": _RANK_LABEL[row["best"] or 0],
            "unrecorded_confidence": row["unrecorded"], "earliest_event_date": row["earliest"],
            "matches": row["n"]}


def _deliver(cur, rule, definition, evaluation_id, now, reference_date, base_meta,
             counts) -> int:
    """Write the notifications for every pending subject. Returns how many."""
    imminent = (reference_date, reference_date + datetime.timedelta(
        days=priority_rules.IMMINENT_DAYS))
    alerts: List[tuple] = []   # (notification, group_keys or None = every other pending)

    def make(summary_row, meta_extra, subjects):
        summary = _summary_from_row(summary_row)
        prio, basis = priority_rules.priority_from_summary(summary, reference_date)
        meta = {**base_meta, **meta_extra, "subject_count": summary_row["n"],
                "subjects": subjects,
                "subjects_truncated": summary_row["n"] > len(subjects),
                "priority_basis": basis}
        return Notification(
            id=None, type=NotificationType.RULE_MATCH, priority=NotificationPriority(prio),
            title=rule["name"], message=f"{summary_row['n']} new matches", file_id=None,
            file_name=None, file_path=None, event_date=summary["earliest_event_date"],
            metadata=meta, created_at=now, recipient_user_id=rule["owner_user_id"],
            rule_id=rule["id"])

    if definition.delivery == "digest":
        cur.execute(_TOTAL_SUMMARY_SQL, (*imminent, rule["id"]))
        total = cur.fetchone()
        cur.execute(_GROUP_SUMMARY_SQL + " GROUP BY group_key ORDER BY first_at, group_key"
                    " LIMIT %s", (*imminent, rule["id"], MAX_GROUPS_IN_DIGEST + 1))
        groups = cur.fetchall()
        cur.execute("SELECT count(DISTINCT group_key) AS g FROM rule_subject_ledger"
                    " WHERE rule_id = %s AND state = 'pending'", (rule["id"],))
        group_count = cur.fetchone()["g"]
        meta = {"delivery": "digest", "group_count": group_count,
                "groups": [{"group_key": g["group_key"], "subject_count": g["n"]}
                           for g in groups[:MAX_GROUPS_IN_DIGEST]],
                "groups_truncated": group_count > MAX_GROUPS_IN_DIGEST}
        alerts.append((make(total, meta, _subjects(cur, rule["id"], None)), None))
    else:
        cur.execute(_GROUP_SUMMARY_SQL + " GROUP BY group_key ORDER BY first_at, group_key"
                    " LIMIT %s", (*imminent, rule["id"], MAX_ALERTS_PER_EVALUATION + 1))
        groups = cur.fetchall()
        overflow = len(groups) > MAX_ALERTS_PER_EVALUATION
        individual = groups[:MAX_ALERTS_PER_EVALUATION - 1] if overflow else groups
        for g in individual:
            subjects = _subjects(cur, rule["id"], g["group_key"])
            label = None
            if definition.group_by == "value" and subjects:
                label = subjects[0]["value"]
            elif definition.group_by == "none" and subjects:
                label = subjects[0]["surface"]
            meta = {"delivery": "immediate", "group_key": g["group_key"],
                    "group_label": label}
            alerts.append((make(g, meta, subjects), [g["group_key"]]))
        if overflow:
            done = [g["group_key"] for g in individual]
            cur.execute(_TOTAL_SUMMARY_SQL + " AND NOT (group_key = ANY(%s))",
                        (*imminent, rule["id"], done))
            rest = cur.fetchone()
            cur.execute("SELECT count(DISTINCT group_key) AS g FROM rule_subject_ledger"
                        " WHERE rule_id = %s AND state = 'pending' AND NOT (group_key = ANY(%s))",
                        (rule["id"], done))
            meta = {"delivery": "overflow", "group_count": cur.fetchone()["g"],
                    "limit": MAX_ALERTS_PER_EVALUATION}
            cur.execute(
                "SELECT subject_key, hash_id, signal_id, confidence, event_date_from,"
                " event_date_to, evidence, first_matched_at FROM rule_subject_ledger"
                " WHERE rule_id = %s AND state = 'pending' AND NOT (group_key = ANY(%s))"
                " ORDER BY first_matched_at, subject_key LIMIT %s",
                (rule["id"], done, MAX_SUBJECTS_PER_ALERT))
            sample = [{"subject_key": r["subject_key"], "hash_id": r["hash_id"],
                       "signal_id": r["signal_id"], "confidence": r["confidence"],
                       "value": r["evidence"].get("value"),
                       "surface": r["evidence"].get("surface")} for r in cur.fetchall()]
            alerts.append((make(rest, meta, sample), ("__rest__", done)))
            counts["overflow_subjects"] = rest["n"]

    notifications = [a[0] for a in alerts]
    for n in notifications:
        n.metadata["evaluation_id"] = evaluation_id
    insert_alerts(cur, notifications)
    for notification, keys in alerts:
        base = ("UPDATE rule_subject_ledger SET state = 'notified', notified_evaluation_id = %s,"
                " alert_id = %s, state_changed_at = %s WHERE rule_id = %s AND state = 'pending'")
        args = [evaluation_id, notification.id, now, rule["id"]]
        if keys is None:
            cur.execute(base, args)
        elif isinstance(keys, tuple):
            cur.execute(base + " AND NOT (group_key = ANY(%s))", args + [keys[1]])
        else:
            cur.execute(base + " AND group_key = ANY(%s)", args + [keys])
        counts["notified"] = counts.get("notified", 0) + cur.rowcount
    return len(notifications)


def evaluate_rule(conn, rule_id: int, *, now: Optional[datetime.datetime] = None,
                  trigger: str = "manual", job_id: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate one rule. Returns ``{"rule_id", "status", "evaluation_id",
    "counts"[, "error"]}``. A failure is recorded and returned, not raised,
    so one broken rule does not stop the others; it is also logged."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reference_date = now.astimezone(datetime.timezone.utc).date()
    conn.rollback()   # pooled connections may arrive inside a transaction
    rule = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
            cur.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
                        (RULE_LOCK_CLASS, rule_id))
            got = cur.fetchone()["got"]
            rule = rule_store.fetch_rule(cur, rule_id, for_update=got)
            if rule is None:
                conn.rollback()
                raise LookupError(f"monitoring rule {rule_id} does not exist")
            common = dict(trigger=trigger, job_id=job_id, now=now,
                          reference_date=reference_date)
            if not got:
                eid = _record(cur, rule, status="skipped_busy", **common)
                conn.commit()
                return {"rule_id": rule_id, "status": "skipped_busy", "evaluation_id": eid,
                        "counts": {}}
            if rule["status"] != "active":
                eid = _record(cur, rule, status="not_active", **common,
                              counts={"rule_status": rule["status"]})
                conn.commit()
                return {"rule_id": rule_id, "status": "not_active", "evaluation_id": eid,
                        "counts": {"rule_status": rule["status"]}}

            # 1. The owner, as of now.
            eligible, reason, role = rule_store.owner_eligibility(cur, rule["owner_user_id"])
            if not eligible:
                cur.execute("UPDATE monitoring_rules SET status = 'disabled',"
                            " disabled_reason = %s, updated_at = %s WHERE id = %s",
                            (reason, now, rule_id))
                rule_store.sync_monitor_flag(cur, rule["saved_search_id"])
                eid = _record(cur, rule, status="owner_revoked", owner_role=role, **common,
                              counts={"disabled_reason": reason})
                insert_alerts(cur, [Notification(
                    id=None, type=NotificationType.RULE_STATUS,
                    priority=NotificationPriority.MEDIUM, title=rule["name"],
                    message=f"Rule disabled: {reason}", file_id=None, file_name=None,
                    file_path=None, event_date=None,
                    metadata={"rule_id": rule_id, "rule_name": rule["name"],
                              "disabled_reason": reason, "evaluation_id": eid},
                    created_at=now, recipient_user_id=rule["owner_user_id"],
                    rule_id=rule_id)])
                conn.commit()
                return {"rule_id": rule_id, "status": "owner_revoked", "evaluation_id": eid,
                        "counts": {"disabled_reason": reason}}

            # 2. The owner's scope, compiled by the single compiler.
            definition = definition_from_stored(rule["definition"])
            if definition.fingerprint() != rule["definition_fingerprint"]:
                raise RuleEvaluationError("stored definition does not match its fingerprint")
            scope = scope_for(SimpleNamespace(id=rule["owner_user_id"], role=role))
            compiled = signal_query._compile(definition.criteria, scope)
            conds, params = _conditions(definition, compiled, reference_date)

            counts: Dict[str, Any] = {}
            eid = _record(cur, rule, status="completed", owner_role=role,
                          scope=_scope_json(scope),
                          criteria_fingerprint=compiled.criteria_fingerprint,
                          finished=False, **common)

            baseline_run = rule["baselined_version"] < rule["version"]
            if baseline_run:
                # Pending subjects of an older version met conditions that no
                # longer apply.
                cur.execute("UPDATE rule_subject_ledger SET state = 'expired',"
                            " state_changed_at = %s WHERE rule_id = %s AND state = 'pending'"
                            " AND rule_version < %s", (now, rule_id, rule["version"]))
                counts["expired_version"] = cur.rowcount
            suppressed = bool(rule["suppressed_until"] and rule["suppressed_until"] > now)
            if baseline_run and not definition.notify_existing:
                state = "baseline"
            elif suppressed:
                state = "suppressed"
            else:
                state = "pending"

            # 3. Match (set-based, deduplicated by the primary key).
            cur.execute(_match_sql(definition, conds),
                        [rule_id, rule["version"], state, now, eid, now] + params + [rule_id])
            counts["new_subjects"] = cur.rowcount
            counts["new_" + state] = cur.rowcount

            # 4. Deliver.
            alerts_written = 0
            if definition.threshold_window_hours:
                cur.execute("UPDATE rule_subject_ledger SET state = 'expired',"
                            " state_changed_at = %s WHERE rule_id = %s AND state = 'pending'"
                            " AND first_matched_at < %s",
                            (now, rule_id,
                             now - datetime.timedelta(hours=definition.threshold_window_hours)))
                counts["expired_threshold_window"] = cur.rowcount
            cur.execute("SELECT count(*) AS n FROM rule_subject_ledger"
                        " WHERE rule_id = %s AND state = 'pending'", (rule_id,))
            pending = cur.fetchone()["n"]
            counts["pending_before_delivery"] = pending
            held = None
            if pending == 0:
                held = None
            elif suppressed:
                held = "suppressed"
            elif pending < definition.threshold_count:
                held = "threshold"
            elif (definition.cooldown_minutes and rule["last_notified_at"] and now
                  < rule["last_notified_at"] + datetime.timedelta(
                      minutes=definition.cooldown_minutes)):
                held = "cooldown"
            elif (definition.delivery == "digest" and rule["last_digest_at"] and now
                  < rule["last_digest_at"] + datetime.timedelta(
                      hours=definition.digest_interval_hours)):
                held = "digest_interval"
            if held:
                counts["held_by"] = held
            elif pending:
                base_meta = {
                    "rule_id": rule_id, "rule_name": rule["name"],
                    "rule_version": rule["version"],
                    "definition_fingerprint": rule["definition_fingerprint"],
                    "criteria_fingerprint": compiled.criteria_fingerprint,
                    "reference_date": reference_date.isoformat(),
                    "unit": definition.unit, "group_by": definition.group_by,
                }
                alerts_written = _deliver(cur, rule, definition, eid, now, reference_date,
                                          base_meta, counts)
            counts["notifications"] = alerts_written

            cur.execute(
                "UPDATE monitoring_rules SET last_evaluated_at = %s, baselined_version = version,"
                " last_notified_at = CASE WHEN %s THEN %s ELSE last_notified_at END,"
                " last_digest_at = CASE WHEN %s THEN %s ELSE last_digest_at END"
                " WHERE id = %s",
                (now, alerts_written > 0, now,
                 alerts_written > 0 and definition.delivery == "digest", now, rule_id))
            cur.execute("UPDATE rule_evaluations SET counts = %s, finished_at = clock_timestamp()"
                        " WHERE id = %s", (psycopg2.extras.Json(counts), eid))
        conn.commit()
        return {"rule_id": rule_id, "status": "completed", "evaluation_id": eid,
                "counts": counts}
    except LookupError:
        raise
    except Exception as exc:
        conn.rollback()
        logger.exception("rule %s evaluation failed", rule_id)
        message = f"{type(exc).__name__}: {exc}"[:2000]
        if rule is None:
            raise
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            eid = _record(cur, rule, status="failed", trigger=trigger, job_id=job_id, now=now,
                          reference_date=reference_date, error=message)
        conn.commit()
        return {"rule_id": rule_id, "status": "failed", "evaluation_id": eid, "counts": {},
                "error": message}


def active_rule_ids(conn) -> List[int]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM monitoring_rules WHERE status = 'active' ORDER BY id")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.rollback()


def run_rule_evaluation(get_connection: Callable, *, rule_ids: Optional[List[int]] = None,
                        trigger: str = "all_rules", job_id: Optional[str] = None,
                        now: Optional[datetime.datetime] = None,
                        progress_cb: Optional[Callable] = None,
                        cancel_cb: Optional[Callable[[], bool]] = None) -> RuleEvaluationResult:
    """Job body (type ``rule_evaluation``): the given rules, or every active
    rule, each in its own transaction on its own connection."""
    result = RuleEvaluationResult()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if rule_ids is None:
        with get_connection() as conn:
            rule_ids = active_rule_ids(conn)
    by_status: Dict[str, int] = {}
    notifications = 0
    for i, rule_id in enumerate(rule_ids):
        if cancel_cb and cancel_cb():
            result.cancelled = True
            break
        with get_connection() as conn:
            try:
                outcome = evaluate_rule(conn, rule_id, now=now, trigger=trigger, job_id=job_id)
            except LookupError as exc:
                outcome = {"rule_id": rule_id, "status": "missing", "error": str(exc)}
        by_status[outcome["status"]] = by_status.get(outcome["status"], 0) + 1
        notifications += (outcome.get("counts") or {}).get("notifications", 0)
        result.evaluations.append(outcome)
        if outcome["status"] in ("failed", "missing"):
            result.errors.append(f"rule {rule_id}: {outcome['error']}")
        elif outcome["status"] in ("owner_revoked", "skipped_busy"):
            result.warnings.append(f"rule {rule_id}: {outcome['status']}")
        if progress_cb:
            progress_cb({"percent": int((i + 1) * 100 / max(len(rule_ids), 1)),
                         "current_phase": "Evaluating monitoring rules",
                         "files_processed": i + 1, "files_total": len(rule_ids)})
    result.stats = {"rules": len(rule_ids), "evaluated": len(result.evaluations),
                    "by_status": by_status, "notifications": notifications,
                    "trigger": trigger, "evaluated_at": now.isoformat()}
    return result
