"""The retention policy table: what may be pruned, and what never is.

One declarative policy per growing area. Each policy names its table, the
timestamp that decides its age, the status guard that keeps *live* rows
(a queued job, a running report) out of retention, a default of days, and
hard bounds. ``0`` days means **keep forever** - an explicit setting, the
default for every area whose deletion would destroy evidence (scenario
outcomes, notifications, artifact bytes and manifests, the change log,
the audit log) - never a silent assumption.

The settings keys (``retention.<area>_days``) are declared in
``settings/settings_models.py`` with the same bounds; this model remains
the authority and re-validates everything the service is about to do.
"""

from __future__ import annotations

from typing import Any, Dict

MIN_ENABLED_DAYS = 1
MAX_DAYS = 3650
KEEP_FOREVER = 0

DELETE = "delete"

POLICIES: Dict[str, Dict[str, Any]] = {
    "jobs": {
        "table": "jobs",
        "age_column": "created_at",
        # A queued, running, paused or cancelling job is live work: it is
        # never pruned, whatever its age.
        "status_guard": ("status NOT IN ('QUEUED', 'RUNNING', 'PAUSED',"
                         " 'CANCELLING')"),
        "default_days": 90,
        "deletes": "finished jobs and their events (job_events cascade)",
        "description": "Finished JobManager jobs and their event streams.",
    },
    "rule_ledger": {
        "table": "rule_subject_ledger",
        "age_column": "first_matched_at",
        "status_guard": None,
        "default_days": 180,
        "deletes": "matched-subject ledger rows; pruning re-baselines the"
                   " rule: subjects that still match are notified again",
        "description": "The rule engine's matched-subject ledger (its"
                       " deduplication memory).",
    },
    "rule_evaluations": {
        "table": "rule_evaluations",
        "age_column": "evaluated_at",
        "status_guard": None,
        "default_days": 180,
        "deletes": "evaluation log rows (the per-rule history page reads"
                   " this)",
        "description": "Monitoring-rule evaluation log rows.",
    },
    "scenario_outcomes": {
        "table": "scenario_outcomes",
        "age_column": "recorded_at",
        "status_guard": None,
        "default_days": KEEP_FOREVER,
        "deletes": "outcome history rows (append-only evidence; the Change"
                   " view of a scenario reads them)",
        "description": "Scenario outcome history (append-only evidence).",
    },
    "notifications": {
        "table": "alerts",
        "age_column": "created_at",
        "status_guard": None,
        "default_days": KEEP_FOREVER,
        "deletes": "notification rows for every recipient, read or unread",
        "description": "Notifications (alerts) for every recipient.",
    },
    "report_artifacts": {
        "table": "report_artifacts",
        "age_column": "created_at",
        "status_guard": None,
        "default_days": KEEP_FOREVER,
        "deletes": "artifact rows including their bytes and manifests - the"
                   " files and their verifiability are gone, stated here and"
                   " in the audit row",
        "description": "Stored report artifacts (bytes + manifest).",
    },
    "report_runs": {
        "table": "report_runs",
        "age_column": "finished_at",
        # A run that never finished is state to reconcile, not litter.
        "status_guard": ("status IN ('completed', 'failed', 'refused',"
                         " 'cancelled') AND finished_at IS NOT NULL"),
        "default_days": 365,
        "deletes": "run rows with their datasets and any remaining artifacts"
                   " (foreign keys cascade)",
        "description": "Completed report runs, their stored datasets and"
                       " remaining artifacts.",
    },
    "path_revisions": {
        "table": "path_revisions",
        "age_column": "changed_at",
        "status_guard": None,
        "default_days": KEEP_FOREVER,
        "deletes": "the Change report's removed/modified history: after"
                   " pruning, old changes are no longer explainable",
        "description": "The append-only revision log behind the Change"
                       " report.",
    },
    "audit_log": {
        "table": "audit_log",
        "age_column": "created_at",
        "status_guard": None,
        "default_days": KEEP_FOREVER,
        "deletes": "audit history, including export disclosures - deleting"
                   " it weakens the tamper-evidence trail and is a declared,"
                   " audited decision",
        "description": "The audit log (export disclosures included).",
    },
}

AREAS = tuple(sorted(POLICIES))


class RetentionError(ValueError):
    """A refused retention request (message is safe to show)."""


def policy_for(area: Any) -> Dict[str, Any]:
    if not isinstance(area, str) or area not in POLICIES:
        raise RetentionError(
            f"area must be one of: {', '.join(AREAS)}")
    return POLICIES[area]


def validate_days(area: str, days: Any) -> int:
    """``0`` = keep forever; otherwise 1..3650. Booleans are not days."""
    policy_for(area)
    if isinstance(days, bool) or not isinstance(days, int):
        raise RetentionError("days must be a whole number of days")
    if days == KEEP_FOREVER:
        return KEEP_FOREVER
    if not (MIN_ENABLED_DAYS <= days <= MAX_DAYS):
        raise RetentionError(
            f"days must be {KEEP_FOREVER} (keep forever) or between"
            f" {MIN_ENABLED_DAYS} and {MAX_DAYS}")
    return days


def effective_days(area: str, configured: Any) -> int:
    """The setting stored in the settings file, re-validated here: a hand-
    edited file cannot smuggle in an unvalidated policy."""
    default = POLICIES[area]["default_days"]
    days = configured if configured is not None else default
    if isinstance(days, bool) or not isinstance(days, int):
        return default
    return days


def setting_key(area: str) -> str:
    return f"retention.{area}_days"


def describe_deletes(area: str) -> str:
    return POLICIES[area]["deletes"]
