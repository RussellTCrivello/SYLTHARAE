"""Job state machine (unified operations job system).

States and the allowed transitions between them. The state machine is pure
data so it can be unit-tested without a database.
"""
from typing import Optional

# Terminal states
QUEUED = "QUEUED"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
CANCELLING = "CANCELLING"
CANCELLED = "CANCELLED"
COMPLETED = "COMPLETED"
COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
FAILED = "FAILED"

ALL_STATES = (
    QUEUED, RUNNING, PAUSED, CANCELLING, CANCELLED,
    COMPLETED, COMPLETED_WITH_WARNINGS, FAILED,
)

TERMINAL_STATES = {CANCELLED, COMPLETED, COMPLETED_WITH_WARNINGS, FAILED}

# Allowed transitions: from -> set of to
TRANSITIONS = {
    QUEUED: {RUNNING, CANCELLING, CANCELLED, FAILED},
    RUNNING: {PAUSED, CANCELLING, CANCELLED, COMPLETED,
              COMPLETED_WITH_WARNINGS, FAILED},
    PAUSED: {RUNNING, CANCELLING, CANCELLED},
    CANCELLING: {CANCELLED, FAILED},
}

# Event types emitted to job_events / SSE (spec section 10)
EVENT_TYPES = (
    "JOB_CREATED", "JOB_STARTED", "PHASE_STARTED", "FILE_DISCOVERED",
    "FILE_PROCESSING", "FILE_COMPLETED", "FILE_SKIPPED", "FILE_FAILED",
    "ARCHIVE_EXTRACTED", "TEXT_EXTRACTED", "OCR_COMPLETED",
    "DUPLICATE_DETECTED", "DATABASE_WRITE", "WARNING", "ERROR",
    "PHASE_COMPLETED", "JOB_COMPLETED", "JOB_CANCELLED",
)


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def transition(current: str, new: str) -> str:
    """Return ``new`` if the transition is legal, else raise ValueError."""
    if not can_transition(current, new):
        raise ValueError(f"Illegal job state transition: {current} -> {new}")
    return new


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def final_state_for(has_errors: bool, has_warnings: bool) -> str:
    """Choose COMPLETED / COMPLETED_WITH_WARNINGS / FAILED by outcome."""
    if has_errors:
        return FAILED
    if has_warnings:
        return COMPLETED_WITH_WARNINGS
    return COMPLETED


def describe_status_row(row: dict, now=None) -> Optional[str]:
    """Human description helper for API serialization (unused by engine)."""
    if row is None:
        return None
    return str(row.get("status", ""))
