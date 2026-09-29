"""Notification priority, derived from the evidence - never assigned by hand.

A person cannot choose a notification's priority: neither the rule API nor
the notification API accepts one. Priority is computed from what was matched:

==========  =============================================================
``high``    at least one match that is *both* high-confidence *and*
            imminent: its event range overlaps [R, R + 7), R being the
            reference date (the Horizon's ``week`` band, same test)
``medium``  otherwise, at least one high- or medium-confidence match
``low``     otherwise - only low-confidence matches, or matches whose
            confidence was not recorded (unknown is not promoted)
==========  =============================================================

The test is made per match: a high-confidence match dated next year and a
low-confidence match dated tomorrow do not add up to ``high``.

``critical`` is never produced. It remains readable in the
``NotificationPriority`` enum only because rows written before this rule
(future-date alerts under the old fixed day bands) may carry it.

Sources for the numbers: the 7-day window is the ``week`` band of the
Horizon (``services/detection/signal_query.BUCKETS``), so "imminent" in a
notification and "this week" on the Horizon are the same set of dates. (The
fixed bands it replaced used ``days_until <= 7`` - eight days - for
CRITICAL.) It is a product rule, stated here, not a threshold taken from the
literature. The confidence levels are
the detector's own (``core/detection/temporal_intel.CONFIDENCE_LEVELS``),
whose rules are documented in docs/implementation/TEMPORAL_SIGNALS.md.

The result is returned together with the facts it was derived from
(``basis``), which are stored with the notification so the priority can be
explained and re-derived.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, Optional, Tuple

PRIORITY_RULE_VERSION = "priority-1"

#: The Horizon's ``week`` band: a date in [R, R + 7) is imminent.
IMMINENT_DAYS = 7

_RANK = {"high": 3, "medium": 2, "low": 1}


def is_imminent(date_from: Optional[datetime.date], date_to: Optional[datetime.date],
                reference_date: datetime.date) -> bool:
    """The event range overlaps [R, R + IMMINENT_DAYS). Undated is never
    imminent. (SQL twin: ``imminent_sql`` - the two are tested to agree.)"""
    if date_from is None:
        return False
    end = date_to or date_from
    return end >= reference_date and date_from < reference_date + datetime.timedelta(
        days=IMMINENT_DAYS)


def imminent_sql(from_col: str, to_col: str) -> str:
    """SQL form of ``is_imminent``; takes two parameters: R and R + 7 days."""
    return (f"({from_col} IS NOT NULL AND COALESCE({to_col}, {from_col}) >= %s"
            f" AND {from_col} < %s)")


def summarise(matches: Iterable[Tuple[Optional[str], Optional[datetime.date],
                                      Optional[datetime.date]]],
              reference_date: datetime.date) -> Dict[str, Any]:
    """Reduce ``(confidence, date_from, date_to)`` triples to the facts
    ``priority_from_summary`` needs. Evaluations compute the same summary in
    SQL for large groups."""
    summary = {"high_and_imminent": False, "highest_confidence": None,
               "unrecorded_confidence": 0, "earliest_event_date": None, "matches": 0}
    best = 0
    for confidence, date_from, date_to in matches:
        summary["matches"] += 1
        rank = _RANK.get(confidence, 0)
        if rank == 0:
            summary["unrecorded_confidence"] += 1
        best = max(best, rank)
        if rank == _RANK["high"] and is_imminent(date_from, date_to, reference_date):
            summary["high_and_imminent"] = True
        if date_from is not None and (summary["earliest_event_date"] is None
                                      or date_from < summary["earliest_event_date"]):
            summary["earliest_event_date"] = date_from
    summary["highest_confidence"] = next((k for k, v in _RANK.items() if v == best), None)
    return summary


def priority_from_summary(summary: Dict[str, Any], reference_date: datetime.date
                          ) -> Tuple[str, Dict[str, Any]]:
    """Return ``(priority, basis)``; ``basis`` is stored with the notification."""
    best = _RANK.get(summary.get("highest_confidence"), 0)
    if summary.get("high_and_imminent"):
        priority = "high"
    elif best >= _RANK["medium"]:
        priority = "medium"
    else:
        priority = "low"
    earliest = summary.get("earliest_event_date")
    basis = {
        "rule": PRIORITY_RULE_VERSION,
        "high_and_imminent": bool(summary.get("high_and_imminent")),
        "highest_confidence": summary.get("highest_confidence"),
        "unrecorded_confidence": int(summary.get("unrecorded_confidence") or 0),
        "matches": int(summary.get("matches") or 0),
        "earliest_event_date": earliest.isoformat() if earliest else None,
        "reference_date": reference_date.isoformat(),
        "imminent_days": IMMINENT_DAYS,
    }
    return priority, basis


def derive_priority(matches: Iterable[Tuple[Optional[str], Optional[datetime.date],
                                            Optional[datetime.date]]],
                    reference_date: datetime.date) -> Tuple[str, Dict[str, Any]]:
    """``(priority, basis)`` for ``(confidence, date_from, date_to)`` matches."""
    return priority_from_summary(summarise(matches, reference_date), reference_date)
