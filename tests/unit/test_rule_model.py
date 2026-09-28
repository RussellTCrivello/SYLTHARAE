"""Step 11 unit tests: rule definitions and derived priority (no database)."""

from __future__ import annotations

import datetime as dt

import pytest

from core.monitoring.priority import (
    IMMINENT_DAYS, derive_priority, is_imminent, priority_from_summary, summarise)
from services.monitoring.rule_model import (
    RuleDefinitionError, definition_from_stored, parse_definition, validate_name)

R = dt.date(2026, 10, 1)
BASE = {"criteria": {"sources": [3]}}


def test_definition_round_trips_with_a_stable_fingerprint():
    d = parse_definition({**BASE, "signals": {"signal_types": ["date_reference"],
                                              "confidence": ["medium", "high", "high"]},
                          "event_window_days": {"from": -7, "to": 30},
                          "threshold": {"count": 3, "window_hours": 24},
                          "delivery": {"mode": "digest", "interval_hours": 6},
                          "group_by": "content", "cooldown_minutes": 15})
    again = definition_from_stored(d.canonical())
    assert again == d and again.fingerprint() == d.fingerprint()
    assert d.signals.confidence == ("high", "medium")          # sorted, de-duplicated
    # Key order and defaults spelled out do not change the fingerprint.
    explicit = parse_definition({"unit": "signal", "group_by": "none", **BASE,
                                 "threshold": {"count": 1}, "delivery": {"mode": "immediate"},
                                 "notify_existing": False, "cooldown_minutes": 0})
    assert explicit.fingerprint() == parse_definition(BASE).fingerprint()


def test_every_semantic_field_changes_the_fingerprint():
    base = parse_definition(BASE).fingerprint()
    variants = [{"unit": "content"}, {"min_confidence": "low"},
                {"event_window_days": {"from": 0, "to": 1}}, {"threshold": {"count": 2}},
                {"cooldown_minutes": 1}, {"group_by": "value"}, {"notify_existing": True},
                {"delivery": {"mode": "digest", "interval_hours": 1}},
                {"signals": {"languages": ["ar"]}}, {"criteria": {"sources": [4]}}]
    prints = {parse_definition({**BASE, **v}).fingerprint() for v in variants}
    assert len(prints) == len(variants) and base not in prints


@pytest.mark.parametrize("payload, message", [
    ({}, "at least one condition"),
    ({**BASE, "extra": 1}, "unknown definition field"),
    ({**BASE, "signals": {"colour": ["red"]}}, "unknown signals field"),
    ({**BASE, "signals": {"confidence": ["sure"]}}, "confidence must be one of"),
    ({**BASE, "signals": {"signal_types": "date_reference"}}, "must be a list"),
    ({**BASE, "unit": "file"}, "unit must be one of"),
    ({**BASE, "min_confidence": "unrecorded"}, "min_confidence must be one of"),
    ({**BASE, "event_window_days": {"from": 5, "to": 1}}, "must not be after"),
    ({**BASE, "event_window_days": {"from": 0}}, "needs both"),
    ({**BASE, "event_window_days": {"from": 0, "to": 99999}}, "between"),
    ({**BASE, "threshold": {"count": 0}}, "between"),
    ({**BASE, "threshold": {"count": True}}, "must be an integer"),
    ({**BASE, "threshold": {"count": 1, "window_hours": 5}}, "only applies"),
    ({**BASE, "cooldown_minutes": -1}, "between"),
    ({**BASE, "delivery": {"mode": "digest"}}, "needs delivery.interval_hours"),
    ({**BASE, "delivery": {"mode": "immediate", "interval_hours": 2}}, "only applies"),
    ({**BASE, "unit": "content", "group_by": "value"}, "needs unit 'signal'"),
    ({**BASE, "notify_existing": "yes"}, "true or false"),
    ({**BASE, "schema_version": 9}, "unsupported rule schema_version"),
    ({**BASE, "signals": {"signal_types": ["place_mention"]},
      "event_window_days": {"from": 0, "to": 7}}, "needs dated signals"),
    ({"criteria": {"keywords": ["gas"]}}, "criteria: keywords"),
])
def test_invalid_definitions_are_refused_with_a_reason(payload, message):
    with pytest.raises(RuleDefinitionError, match=message):
        parse_definition(payload)


def test_saved_search_criteria_cannot_be_combined_with_inline_criteria():
    from core.criteria.model import from_dict

    snapshot = from_dict({"sources": [9]})
    d = parse_definition({"signals": {"signal_types": ["date_reference"]}},
                         criteria_override=snapshot)
    assert d.criteria == snapshot
    with pytest.raises(RuleDefinitionError, match="cannot also"):
        parse_definition(BASE, criteria_override=snapshot)


def test_names_are_normalised_and_bounded():
    assert validate_name("  Gas   supply \n watch ") == "Gas supply watch"
    for bad in ("", "   ", None, 5, "x" * 201):
        with pytest.raises(RuleDefinitionError):
            validate_name(bad)


# --- priority -------------------------------------------------------------

def test_imminence_is_the_horizon_week_band():
    assert is_imminent(R, None, R)
    assert is_imminent(R + dt.timedelta(IMMINENT_DAYS - 1), None, R)
    assert not is_imminent(R + dt.timedelta(IMMINENT_DAYS), None, R)
    assert is_imminent(R - dt.timedelta(10), R, R)                 # ongoing range
    assert not is_imminent(R - dt.timedelta(10), R - dt.timedelta(1), R)
    assert not is_imminent(None, None, R)


@pytest.mark.parametrize("matches, expected", [
    ([("high", R + dt.timedelta(2), None)], "high"),
    ([("high", R + dt.timedelta(60), None)], "medium"),
    ([("medium", R, None)], "medium"),
    ([("low", R, None)], "low"),
    ([(None, R, None)], "low"),                                    # unknown is not promoted
    ([("high", None, None)], "medium"),                            # undated
    # Per match: high-but-distant plus low-but-imminent is not "high".
    ([("high", R + dt.timedelta(300), None), ("low", R + dt.timedelta(1), None)], "medium"),
    ([], "low"),
])
def test_priority_is_derived_from_the_matches(matches, expected):
    priority, basis = derive_priority(matches, R)
    assert priority == expected and priority != "critical"
    assert basis["rule"] == "priority-1" and basis["reference_date"] == R.isoformat()
    assert basis["matches"] == len(matches)


def test_priority_basis_records_what_it_was_derived_from():
    priority, basis = derive_priority([("high", R + dt.timedelta(3), R + dt.timedelta(4)),
                                       (None, R + dt.timedelta(40), None)], R)
    assert priority == "high"
    assert basis == {"rule": "priority-1", "high_and_imminent": True,
                     "highest_confidence": "high", "unrecorded_confidence": 1, "matches": 2,
                     "earliest_event_date": "2026-10-04", "reference_date": "2026-10-01",
                     "imminent_days": 7}
    assert priority_from_summary(summarise([], R), R)[0] == "low"


# --- rule notifications ----------------------------------------------------

def _rule_notification(type_, metadata, recipient=7):
    from core.monitoring.notification_service import (
        Notification, NotificationPriority, NotificationType)

    return Notification(id=None, type=NotificationType(type_),
                        priority=NotificationPriority.MEDIUM, title="Gas watch", message="m",
                        file_id=None, file_name=None, file_path=None, event_date=None,
                        metadata=metadata, created_at=dt.datetime(2026, 10, 1),
                        recipient_user_id=recipient, rule_id=3)


def _t(msg, **kw):
    return msg % kw if kw else msg


def test_rule_notifications_are_rendered_from_their_metadata():
    from core.monitoring.notification_display import display_payload, format_title_message

    cases = [
        ({"rule_name": "Gas watch", "subject_count": 2, "delivery": "immediate",
          "group_label": "5 October 2026"}, ("Rule: Gas watch", "2 new matches: 5 October 2026")),
        ({"rule_name": "Gas watch", "subject_count": 9, "delivery": "digest"},
         ("Rule digest: Gas watch", "9 new matches since the last digest")),
        ({"rule_name": "Gas watch", "subject_count": 40, "group_count": 12,
          "delivery": "overflow"},
         ("Rule: Gas watch", "40 further matches in 12 groups (notification limit per"
                             " evaluation reached)")),
    ]
    for metadata, expected in cases:
        assert format_title_message(_rule_notification("rule_match", metadata), _t) == expected
    status = _rule_notification("rule_status", {"rule_name": "Gas watch",
                                                "disabled_reason": "owner_role"})
    title, message = format_title_message(status, _t)
    assert title == "Rule disabled: Gas watch" and "role" in message
    payload = display_payload(status, _t)
    assert payload["addressed"] is True and payload["rule_id"] == 3


def test_rule_notifications_cannot_be_written_without_a_recipient():
    from core.monitoring.notification_service import insert_alerts

    class NoCursor:
        def execute(self, *a):
            raise AssertionError("nothing may be written")

    with pytest.raises(ValueError, match="addressed"):
        insert_alerts(NoCursor(), [_rule_notification("rule_match", {}, recipient=None)])
