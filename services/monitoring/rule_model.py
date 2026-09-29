"""Monitoring-rule definitions: strict parsing, canonical form, fingerprint.

A rule is *what to watch* plus *how to deliver it*. It adds no filter
language of its own:

* ``criteria`` - document conditions, the canonical ``Criteria`` of the
  single compiler (core/criteria). A rule created from a saved search takes a
  **snapshot** of that search's criteria; the search id is kept only as
  provenance. Editing or deleting the search later does not change what the
  rule watches - a rule whose conditions change silently could not explain
  its past notifications.
* ``signals`` - signal conditions, the ``SignalFilter`` of the Signal
  Explorer (services/detection/signal_query), parsed by the same parser.
* the rule's own conditions and delivery settings, below.

Every key is validated; unknown keys are refused, not ignored. Only the
semantic fields enter the fingerprint - the rule's *name* does not, so
renaming a rule never creates a new version.

========================  ==================================================
``unit``                  ``signal``: each matching signal is a subject.
                          ``content``: each content with at least one
                          matching signal is one subject (its strongest
                          signal is kept as the evidence).
``min_confidence``        ``low`` / ``medium`` / ``high``: only signals with
                          at least this recorded confidence. A signal whose
                          confidence was not recorded never passes a
                          minimum - unknown is not treated as good enough.
``event_window_days``     ``{"from": a, "to": b}``: the signal's event range
                          must overlap [R + a, R + b], R being the reference
                          date of the evaluation. Undated signals never
                          match a window.
``threshold``             ``{"count": n, "window_hours": h}``: notify only
                          once n un-notified matches exist; with ``h``, only
                          matches from the last h hours count, and older
                          ones expire un-notified (recorded as ``expired``).
``cooldown_minutes``      after a notification, hold further ones for this
                          long. Held matches are delivered afterwards, not
                          lost.
``group_by``              ``none`` (one notification per subject),
                          ``content`` or ``value`` (one per content / per
                          signal value).
``delivery``              ``{"mode": "immediate"}`` or
                          ``{"mode": "digest", "interval_hours": h}`` (at
                          most one summary notification per h hours).
``notify_existing``       ``false`` (default): what already matches when the
                          rule (or a new version of it) starts is recorded
                          as the ``baseline`` and not notified - a new rule
                          does not flood its owner with the whole corpus.
                          ``true``: notify it.
========================  ==================================================

Suppression is not part of the definition: it is a temporary state of the
rule (``suppressed_until``), set and lifted without creating a version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from werkzeug.datastructures import MultiDict

from core.criteria.model import Criteria, CriteriaError, from_dict, sha256_hex
from services.detection import signal_query
from services.detection.signal_query import SignalFilter, SignalQueryError

RULE_SCHEMA_VERSION = 1

UNITS = ("signal", "content")
GROUP_BY = ("none", "content", "value")
DELIVERY_MODES = ("immediate", "digest")
CONFIDENCE_LEVELS = ("low", "medium", "high")

#: Input limits that keep a definition's effect bounded. They are sanity
#: limits on what a person may type, not statistical thresholds.
MAX_WINDOW_DAYS = 3650
MAX_THRESHOLD_COUNT = 10_000
MAX_THRESHOLD_WINDOW_HOURS = 24 * 366
MAX_COOLDOWN_MINUTES = 60 * 24 * 30
MAX_DIGEST_HOURS = 24 * 31
MAX_NAME_LENGTH = 200

_DEFINITION_KEYS = {"criteria", "signals", "unit", "min_confidence", "event_window_days",
                    "threshold", "cooldown_minutes", "group_by", "delivery",
                    "notify_existing", "schema_version"}

#: SignalFilter fields and the request-argument name each is parsed from.
_SIGNAL_ARGS = {
    "detectors": "detector", "signal_types": "signal_type", "resolutions": "resolution",
    "confidence": "confidence", "languages": "language", "calendars": "calendar",
    "methods": "method", "orientations": "orientation", "place_keys": "place_key",
    "hash_ids": "hash_id",
}
_SIGNAL_SCALARS = {"evidence_text": "evidence_text", "event_from": "event_from",
                   "event_to": "event_to"}


class RuleDefinitionError(ValueError):
    """A definition that cannot be accepted as written (HTTP 400)."""


def _int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuleDefinitionError(f"{name} must be an integer")
    if not lo <= value <= hi:
        raise RuleDefinitionError(f"{name} must be between {lo} and {hi}")
    return value


def _choice(value: Any, name: str, allowed) -> str:
    if value not in allowed:
        raise RuleDefinitionError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def _object(value: Any, name: str, keys) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleDefinitionError(f"{name} must be an object")
    unknown = sorted(set(value) - set(keys))
    if unknown:
        raise RuleDefinitionError(f"unknown {name} field(s): {', '.join(unknown)}")
    return value


def parse_signal_filter(payload: Any) -> SignalFilter:
    """A ``SignalFilter`` from its canonical JSON form, through the Explorer's
    own parser (so a rule accepts exactly what the Explorer accepts)."""
    if payload in (None, {}):
        return SignalFilter()
    payload = _object(payload, "signals", list(_SIGNAL_ARGS) + list(_SIGNAL_SCALARS))
    args = MultiDict()
    for key, arg in _SIGNAL_ARGS.items():
        values = payload.get(key)
        if values in (None, []):
            continue
        if not isinstance(values, list):
            raise RuleDefinitionError(f"signals.{key} must be a list")
        for v in values:
            if isinstance(v, bool) or not isinstance(v, (str, int)):
                raise RuleDefinitionError(f"signals.{key} values must be strings")
            args.add(arg, str(v))
    for key, arg in _SIGNAL_SCALARS.items():
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise RuleDefinitionError(f"signals.{key} must be a string")
        args.add(arg, value)
    try:
        return signal_query.parse_filter(args)
    except SignalQueryError as exc:
        raise RuleDefinitionError(f"signals: {exc}")


def _criteria_is_empty(criteria: Criteria) -> bool:
    neutral = {"unit", "sort", "schema_version"}
    mine = {k: v for k, v in criteria.to_dict().items() if k not in neutral}
    empty = {k: v for k, v in Criteria().to_dict().items() if k not in neutral}
    return mine == empty


@dataclass(frozen=True)
class RuleDefinition:
    criteria: Criteria = field(default_factory=Criteria)
    signals: SignalFilter = field(default_factory=SignalFilter)
    unit: str = "signal"
    min_confidence: Optional[str] = None
    event_window_days: Optional[Tuple[int, int]] = None
    threshold_count: int = 1
    threshold_window_hours: Optional[int] = None
    cooldown_minutes: int = 0
    group_by: str = "none"
    delivery: str = "immediate"
    digest_interval_hours: Optional[int] = None
    notify_existing: bool = False

    def canonical(self) -> Dict[str, Any]:
        """The stored, fingerprinted form. Key order is irrelevant (the
        fingerprint uses canonical JSON); every field is always present."""
        return {
            "schema_version": RULE_SCHEMA_VERSION,
            "criteria": self.criteria.to_dict(),
            "signals": self.signals.canonical(),
            "unit": self.unit,
            "min_confidence": self.min_confidence,
            "event_window_days": ({"from": self.event_window_days[0],
                                   "to": self.event_window_days[1]}
                                  if self.event_window_days else None),
            "threshold": {"count": self.threshold_count,
                          "window_hours": self.threshold_window_hours},
            "cooldown_minutes": self.cooldown_minutes,
            "group_by": self.group_by,
            "delivery": {"mode": self.delivery, "interval_hours": self.digest_interval_hours},
            "notify_existing": self.notify_existing,
        }

    def fingerprint(self) -> str:
        return sha256_hex(self.canonical())

    @property
    def has_condition(self) -> bool:
        return (not _criteria_is_empty(self.criteria)
                or self.signals != SignalFilter()
                or self.min_confidence is not None
                or self.event_window_days is not None)


def parse_definition(payload: Any, *, criteria_override: Optional[Criteria] = None
                     ) -> RuleDefinition:
    """Strictly parse a definition.

    ``criteria_override`` is the snapshot of a saved search; it replaces the
    ``criteria`` key, which must then be absent (which one wins would be a
    guess).
    """
    if payload is None:
        payload = {}
    payload = _object(payload, "definition", _DEFINITION_KEYS)
    version = payload.get("schema_version", RULE_SCHEMA_VERSION)
    if version != RULE_SCHEMA_VERSION:
        raise RuleDefinitionError(f"unsupported rule schema_version {version!r}")

    if criteria_override is not None:
        if payload.get("criteria") not in (None, {}):
            raise RuleDefinitionError("a rule built from a saved search cannot also"
                                      " carry its own criteria")
        criteria = criteria_override
    else:
        raw = payload.get("criteria")
        if raw is not None and not isinstance(raw, dict):
            raise RuleDefinitionError("criteria must be an object")
        try:
            criteria = from_dict(raw or {})
        except CriteriaError as exc:
            raise RuleDefinitionError(f"criteria: {exc}")

    signals = parse_signal_filter(payload.get("signals"))
    unit = _choice(payload.get("unit", "signal"), "unit", UNITS)
    min_conf = payload.get("min_confidence")
    if min_conf is not None:
        _choice(min_conf, "min_confidence", CONFIDENCE_LEVELS)

    window = None
    if payload.get("event_window_days") is not None:
        w = _object(payload["event_window_days"], "event_window_days", ("from", "to"))
        if "from" not in w or "to" not in w:
            raise RuleDefinitionError("event_window_days needs both from and to")
        lo = _int(w["from"], "event_window_days.from", -MAX_WINDOW_DAYS, MAX_WINDOW_DAYS)
        hi = _int(w["to"], "event_window_days.to", -MAX_WINDOW_DAYS, MAX_WINDOW_DAYS)
        if lo > hi:
            raise RuleDefinitionError("event_window_days.from must not be after .to")
        window = (lo, hi)

    threshold = _object(payload.get("threshold") or {}, "threshold", ("count", "window_hours"))
    count = _int(threshold.get("count", 1), "threshold.count", 1, MAX_THRESHOLD_COUNT)
    window_hours = threshold.get("window_hours")
    if window_hours is not None:
        window_hours = _int(window_hours, "threshold.window_hours", 1, MAX_THRESHOLD_WINDOW_HOURS)
        if count == 1:
            raise RuleDefinitionError("threshold.window_hours only applies when"
                                      " threshold.count is above 1")

    cooldown = _int(payload.get("cooldown_minutes", 0), "cooldown_minutes", 0,
                    MAX_COOLDOWN_MINUTES)
    group_by = _choice(payload.get("group_by", "none"), "group_by", GROUP_BY)
    if unit == "content" and group_by == "value":
        raise RuleDefinitionError("group_by 'value' needs unit 'signal': a content"
                                  " subject has no single value")

    delivery = _object(payload.get("delivery") or {}, "delivery", ("mode", "interval_hours"))
    mode = _choice(delivery.get("mode", "immediate"), "delivery.mode", DELIVERY_MODES)
    interval = delivery.get("interval_hours")
    if mode == "digest":
        if interval is None:
            raise RuleDefinitionError("a digest needs delivery.interval_hours")
        interval = _int(interval, "delivery.interval_hours", 1, MAX_DIGEST_HOURS)
    elif interval is not None:
        raise RuleDefinitionError("delivery.interval_hours only applies to a digest")

    notify_existing = payload.get("notify_existing", False)
    if not isinstance(notify_existing, bool):
        raise RuleDefinitionError("notify_existing must be true or false")

    if window is not None and signals.signal_types and not (
            set(signals.signal_types) & {"date_reference", "relative_reference"}):
        raise RuleDefinitionError("event_window_days needs dated signals; the selected"
                                  " signal types carry no dates")

    definition = RuleDefinition(
        criteria=criteria, signals=signals, unit=unit, min_confidence=min_conf,
        event_window_days=window, threshold_count=count, threshold_window_hours=window_hours,
        cooldown_minutes=cooldown, group_by=group_by, delivery=mode,
        digest_interval_hours=interval, notify_existing=notify_existing)
    if not definition.has_condition:
        # A rule without any condition would notify on every signal the
        # corpus ever produces.
        raise RuleDefinitionError("a rule needs at least one condition (criteria,"
                                  " signal filters, min_confidence or event_window_days)")
    return definition


def definition_from_stored(stored: Dict[str, Any]) -> RuleDefinition:
    """Rebuild a definition from its stored canonical form (same parser)."""
    return parse_definition(stored)


def validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise RuleDefinitionError("name is required")
    name = " ".join(name.split())
    if len(name) > MAX_NAME_LENGTH:
        raise RuleDefinitionError(f"name is limited to {MAX_NAME_LENGTH} characters")
    return name
