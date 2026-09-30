"""The schedule model: what may be scheduled and what it must carry.

Pure validation and vocabulary - no database, no threads. The store and the
scheduler both speak this language, and the unit suite pins it.

Three job types are schedulable in step 20, each mapping onto an existing
JobManager job (no second job framework):

* ``report_run``          - runs one registered report's parameters as the
  schedule's owner (the run is created and executed by the owner, so its
  visibility follows the owner exactly as a manual run's does);
* ``rule_evaluation``     - evaluates every active monitoring rule
  (administrators only, like the manual "evaluate all");
* ``scenario_evaluation`` - evaluates every active scenario (same privilege).

Authorization is re-decided at every fire: the owner's current role and
activity are read from the database, and a report schedule's payload is
revalidated against the registry. A schedule whose owner lost the required
role - or whose report/version/parameters no longer validate - is disabled
with a stated reason instead of running anyway.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 525600  # one year

REPORT_RUN = "report_run"
RULE_EVALUATION = "rule_evaluation"
SCENARIO_EVALUATION = "scenario_evaluation"
RETENTION = "retention"

SCHEDULE_TYPES = (REPORT_RUN, RULE_EVALUATION, SCENARIO_EVALUATION, RETENTION)

#: The role an owner must hold *at each fire* for the schedule to run.
#: Reports run as their owner (analyst or administrator); "evaluate every
#: rule/scenario" is the same privilege as the manual all-rules trigger.
REQUIRED_ROLE = {REPORT_RUN: "analyst", RULE_EVALUATION: "admin",
                 SCENARIO_EVALUATION: "admin", RETENTION: "admin"}

DISABLE_REASONS = {
    "paused": "paused by the owner or an administrator",
    "owner_inactive": "the owner is deactivated",
    "owner_role": "the owner no longer holds the required role",
    "owner_missing": "the owner no longer exists",
    "definition_invalid": "the scheduled report/version/parameters no longer validate",
}


class ScheduleError(ValueError):
    """A refused schedule definition (message is safe to show)."""


def validate_interval(minutes: Any) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ScheduleError("interval_minutes must be a whole number of minutes")
    if not (MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES):
        raise ScheduleError(
            f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and "
            f"{MAX_INTERVAL_MINUTES}")
    return minutes


def validate_payload(schedule_type: str, payload: Any) -> Dict[str, Any]:
    """Validate ``payload`` for ``schedule_type``; returns the stored shape.

    ``report_run`` payload: ``{"report_id": str, "version": int|null,
    "parameters": object|null}`` - validated against the report registry
    (unknown report, unknown version and unknown/invalid parameters are all
    refused here, at creation and edit time, not silently stored).
    Evaluation payloads carry nothing: they mean "every active rule /
    scenario", the same set the manual all-rules trigger evaluates.
    """
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleError(
            f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ScheduleError("payload must be an object")
    if schedule_type == REPORT_RUN:
        return _validate_report_payload(payload)
    extra = sorted(set(payload) - {"note"})
    if extra:
        raise ScheduleError(f"unknown payload field(s): {', '.join(extra)}")
    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        raise ScheduleError("payload.note must be a string")
    return {"note": note} if note else {}


def _validate_report_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    unknown = sorted(set(payload) - {"report_id", "version", "parameters", "note"})
    if unknown:
        raise ScheduleError(f"unknown payload field(s): {', '.join(unknown)}")
    report_id = payload.get("report_id")
    if not isinstance(report_id, str) or not report_id.strip():
        raise ScheduleError("payload.report_id is required")
    version = payload.get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ScheduleError("payload.version must be a whole number or null")
    parameters = payload.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise ScheduleError("payload.parameters must be an object")
    from core.reporting.registry import ReportNotFound
    from core.reporting.registry import REGISTRY

    try:
        definition = REGISTRY.report(report_id.strip(), version)
    except ReportNotFound:
        raise ScheduleError(
            f"payload.report_id: no registered report/version "
            f"{report_id.strip()!r}"
            + (f" at version {version}" if version is not None else "")) from None
    try:
        definition.normalize_parameters(parameters)
    except Exception as exc:
        raise ScheduleError(f"payload.parameters: {exc}") from None
    stored: Dict[str, Any] = {"report_id": report_id.strip()}
    if version is not None:
        stored["version"] = version
    if parameters:
        stored["parameters"] = parameters
    if payload.get("note"):
        stored["note"] = payload["note"]
    return stored


def required_role(schedule_type: str) -> str:
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleError(
            f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")
    return REQUIRED_ROLE[schedule_type]


def owner_eligible(schedule_type: str, role: Optional[str],
                   is_active: bool) -> Tuple[bool, Optional[str]]:
    """Whether an owner with this role/activity may still run the schedule."""
    if not is_active:
        return False, "owner_inactive"
    needed = required_role(schedule_type)
    if needed == "admin":
        return (True, None) if role == "admin" else (False, "owner_role")
    return (True, None) if role in ("admin", "analyst") else (False, "owner_role")


def describe(schedule_type: str, payload: Dict[str, Any]) -> str:
    """One line for the page: what the schedule runs."""
    if schedule_type == REPORT_RUN:
        version = payload.get("version")
        return (f"{payload.get('report_id', '?')}"
                + (f"@{version}" if version else " (latest version)"))
    if schedule_type == RULE_EVALUATION:
        return "every active monitoring rule"
    if schedule_type == RETENTION:
        return "every enabled retention policy"
    return "every active scenario"
