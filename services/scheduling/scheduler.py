"""The scheduler: a tick loop that fires due schedules through JobManager.

One daemon thread (started by the web entry point; tests drive ``tick()``
directly). One tick does three things, in order:

1. ``refresh_job_statuses`` - copy each schedule's last job status onto the
   schedule (set-based; a FAILED job counts one consecutive failure);
2. ``claim_due`` - take due schedules atomically (at-most-once);
3. for each claimed schedule, re-decide authorization *now* and enqueue the
   matching JobManager job:

   * the owner's current role and activity are read fresh (the same
     eligibility read the rule engine uses for its own owners);
   * a ``report_run`` payload is revalidated against the registry - the run
     is created by ``runs.submit_run`` as the owner, so visibility and the
     audit trail are exactly a manual run's;
   * an owner who lost the required role, or a payload that no longer
     validates, disables the schedule with the stated reason. Nothing runs
     as somebody who may no longer run it.

Fires and auto-disables are audited with ``username='scheduler'`` (there is
no HTTP request on this path).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

TICK_SECONDS_DEFAULT = 15
CLAIM_LIMIT_DEFAULT = 5


def tick_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("SCHEDULER_TICK_SECONDS")
                              or TICK_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        return float(TICK_SECONDS_DEFAULT)


def claim_limit() -> int:
    try:
        return max(1, int(os.environ.get("SCHEDULER_CLAIM_LIMIT")
                          or CLAIM_LIMIT_DEFAULT))
    except (TypeError, ValueError):
        return CLAIM_LIMIT_DEFAULT


def enabled_by_config() -> bool:
    """Off unless explicitly switched on: tests and import-only processes
    never grow a background thread by accident."""
    return os.environ.get("SYLTHARAE_SCHEDULER", "") == "1"


class PermanentRefusal(Exception):
    """The schedule can never run again as it stands (payload invalid)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Scheduler:
    """Runs ``tick`` in a daemon loop. One process per database (the same
    single-process model as the JobManager workers), so no leader election
    is needed; the claim statement is still SKIP LOCKED safe."""

    _instance: Optional["Scheduler"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "Scheduler":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = Scheduler()
            return cls._instance

    def __init__(self, get_connection: Optional[Callable] = None) -> None:
        if get_connection is None:
            from Api.utils.utils import get_connection
            get_connection = get_connection
        self._get_connection = get_connection
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ------------------------------------------------------
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-scheduler",
                                        daemon=True)
        self._thread.start()
        logger.info("Job scheduler started (tick %.0fs)", tick_seconds())
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            self._stop.wait(tick_seconds())

    # -- one tick -------------------------------------------------------
    def tick(self) -> Dict[str, int]:
        """Fire what is due. Returns ``{"refreshed", "claimed", "fired",
        "disabled", "failed"}`` - measured, not assumed."""
        from services.scheduling.store import claim_due, refresh_job_statuses

        stats = {"refreshed": 0, "claimed": 0, "fired": 0, "disabled": 0,
                 "failed": 0}
        with self._get_connection() as conn:
            stats["refreshed"] = refresh_job_statuses(conn)
            due = claim_due(conn, limit=claim_limit())
        stats["claimed"] = len(due)
        for schedule in due:
            outcome = self.fire(schedule)
            stats["fired" if outcome["status"] == "fired" else
                  ("disabled" if outcome["status"] == "disabled" else "failed")] += 1
        return stats

    # -- one fire -------------------------------------------------------
    def fire(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        """One claimed schedule: revalidate, enqueue, record. Never raises."""
        from services.scheduling.store import owner_of

        schedule_id = schedule["id"]
        try:
            with self._get_connection() as conn:
                owner = owner_of(conn, schedule_id)
            if owner is None:
                # Deleted between claim and fire (owner cascade): nothing to do.
                return {"status": "gone", "schedule_id": schedule_id}
            return self._checked_fire(schedule, owner)
        except Exception as exc:
            # A transient failure must not lose the schedule: the claim
            # already advanced next_run_at, so record the failure and let
            # the next interval try again.
            logger.exception("schedule %s failed to enqueue", schedule_id)
            try:
                from services.scheduling.store import set_fire_outcome

                with self._get_connection() as conn:
                    set_fire_outcome(conn, schedule_id, job_id=None, status=None,
                                     enqueue_failed=True)
            except Exception:
                logger.exception("schedule %s failure recording failed too",
                                 schedule_id)
            return {"status": "error", "schedule_id": schedule_id,
                    "error": exc.__class__.__name__}

    def fire_now(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        """Run a schedule immediately (Run now) without moving its cadence.

        The same revalidation as a timed fire: the owner's current role and
        activity, and the payload against the registry. A schedule that can
        no longer run is disabled and reported, not run anyway."""
        from services.scheduling.store import owner_of

        schedule = dict(schedule, _run_now=True)

        try:
            with self._get_connection() as conn:
                owner = owner_of(conn, schedule["id"])
            if owner is None:
                return {"status": "gone", "schedule_id": schedule["id"]}
            return self._checked_fire(schedule, owner)
        except Exception as exc:
            logger.exception("schedule %s run_now failed", schedule["id"])
            try:
                from services.scheduling.store import set_fire_outcome

                with self._get_connection() as conn:
                    set_fire_outcome(conn, schedule["id"], job_id=None,
                                     status=None, enqueue_failed=True)
            except Exception:
                logger.exception("schedule %s failure recording failed too",
                                 schedule["id"])
            return {"status": "error", "schedule_id": schedule["id"],
                    "error": exc.__class__.__name__}

    def _checked_fire(self, schedule: Dict[str, Any],
                      owner: Dict[str, Any]) -> Dict[str, Any]:
        from services.scheduling.model import owner_eligible
        from services.scheduling.store import set_fire_outcome

        schedule_id = schedule["id"]
        eligible, reason = owner_eligible(owner["schedule_type"],
                                          owner["role"], owner["is_active"])
        if not eligible:
            self._disable(schedule_id, reason, owner)
            return {"status": "disabled", "schedule_id": schedule_id,
                    "reason": reason}
        try:
            job_id = self._enqueue(schedule, owner)
        except PermanentRefusal as refusal:
            self._disable(schedule_id, refusal.reason, owner)
            return {"status": "disabled", "schedule_id": schedule_id,
                    "reason": refusal.reason}
        with self._get_connection() as conn:
            set_fire_outcome(conn, schedule_id, job_id=job_id, status="QUEUED")
        self._audit("schedule.fired", None,
                    resource=f"job_schedule:{schedule_id}",
                    detail={"job_id": job_id, "name": owner["name"],
                            "schedule_type": owner["schedule_type"],
                            "trigger": "run_now" if schedule.get("_run_now")
                            else "schedule"})
        return {"status": "fired", "schedule_id": schedule_id, "job_id": job_id}

    def _disable(self, schedule_id: int, reason: str, owner: Dict[str, Any]) -> None:
        from services.scheduling.store import set_fire_outcome

        with self._get_connection() as conn:
            set_fire_outcome(conn, schedule_id, job_id=None, status=None,
                             disable_reason=reason)
        self._audit("schedule.disabled", None,
                    resource=f"job_schedule:{schedule_id}",
                    detail={"reason": reason, "name": owner.get("name"),
                            "schedule_type": owner.get("schedule_type")})
        logger.warning("schedule %s (%s) disabled: %s",
                       schedule_id, owner.get("name"), reason)

    # -- the enqueues ----------------------------------------------------
    def _enqueue(self, schedule: Dict[str, Any], owner: Dict[str, Any]) -> str:
        """Create the JobManager job for one fire. Raises on refusal."""
        from services.jobs.manager import JobManager
        from services.scheduling.model import REPORT_RUN, describe

        schedule_type = schedule["schedule_type"]
        manager = JobManager.get_instance()
        created_by = owner["username"] or "system"
        common = {"schedule_id": schedule["id"],
                  "schedule_name": schedule["name"],
                  "triggered_by": "schedule"}
        if schedule_type == REPORT_RUN:
            # Exactly the API's path: submit_run authorizes and validates as
            # the owner (role, registry, parameters) and records the run;
            # the job only executes what submit_run accepted.
            from services.reporting import runs as report_runs

            payload = schedule["payload"] or {}

            class _Owner:
                pass

            principal = _Owner()
            principal.id = owner["id"]
            principal.username = owner["username"]
            principal.role = owner["role"]
            principal.has_role = lambda *roles: principal.role in roles
            with self._get_connection() as conn:
                try:
                    run = report_runs.submit_run(
                        conn, user=principal,
                        report_id=payload.get("report_id"),
                        version=payload.get("version"),
                        parameters=payload.get("parameters"))
                except report_runs.ReportRunError as exc:
                    if exc.code in ("VALIDATION_FAILED", "NOT_FOUND"):
                        # Permanent: the report/version/parameters the
                        # schedule stored no longer validate. Disable, do
                        # not churn.
                        raise PermanentRefusal("definition_invalid") from None
                    raise
            options = dict(common, run_id=run["id"])
            job = manager.create_job(
                "report_run", source=f"schedules:{describe(REPORT_RUN, payload)}",
                options=options, created_by=created_by)
            return job["job_id"]
        if schedule_type == "rule_evaluation":
            job = manager.create_job(
                "rule_evaluation", source="schedules:all_rules",
                options=dict(common, rule_ids=None, trigger="schedule"),
                created_by=created_by)
            return job["job_id"]
        job = manager.create_job(
            "scenario_evaluation", source="schedules:all_scenarios",
            options=dict(common, scenario_ids=None, trigger="schedule"),
            created_by=created_by)
        return job["job_id"]

    # -- audit -----------------------------------------------------------
    def _audit(self, action: str, user_id: Optional[int], *,
               resource: Optional[str], detail: Optional[Dict[str, Any]]) -> None:
        try:
            from core.security.service import get_auth_service

            get_auth_service().audit(action, user_id=user_id, username="scheduler",
                                     resource=resource, detail=detail)
        except Exception:
            logger.exception("scheduler audit write failed for %s", action)
