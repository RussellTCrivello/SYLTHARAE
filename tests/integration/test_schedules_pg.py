"""Step 20 against PostgreSQL: scheduled jobs.

Executed on the migrated schema (m0031) with a real synchronous JobManager.
Properties:

* the table refuses what it must, by constraint name: unknown schedule
  types, intervals outside the bounds, empty names, duplicate names per
  owner, negative failure counts;
* the claim is at-most-once: one ``tick`` takes a due schedule exactly
  once and moves ``next_run_at`` forward by the schedule's own interval -
  a second claim finds nothing, downtime fires once, never a burst;
* authorization is re-decided at every fire: a demoted or deactivated
  owner's schedule is disabled with the stated reason and no job is
  created; a payload that no longer validates disables the schedule the
  same way. Every fire and every auto-disable is audited;
* a report fire is exactly a manual run: ``submit_run`` as the owner (the
  run's visibility follows the owner) and one ``report_run`` job through
  the JobManager, which completes against the real registry;
* resuming moves the next fire a full interval ahead and clears the
  failure count; deleting the owner deletes the schedules.
"""

from __future__ import annotations

import datetime
import uuid

import psycopg2
import psycopg2.extras
import pytest

from services.jobs.manager import JobManager
from services.scheduling import store
from services.scheduling.model import ScheduleError
from services.scheduling.scheduler import Scheduler

from _seed import connect, document, side, source

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


def _user(cur, role, name):
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)"
                " RETURNING id, username, role, is_active",
                (f"{name}_{_U}", role))
    row = cur.fetchone()
    return {"id": row[0], "username": row[1], "role": row[2], "is_active": row[3]}


@pytest.fixture(scope="module")
def world(pg_db):
    word = f"sch{_U}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1, d1 = source(cur), side(cur)
        for i in range(1, 4):
            document(cur, source_id=s1, side_id=d1, text=f"{word} number {i}",
                     file_name=f"{word}-{i}.txt", file_date=datetime.date(2026, 3, i))
        analyst = _user(cur, "analyst", "sch_analyst")
        admin = _user(cur, "admin", "sch_admin")
        viewer = _user(cur, "viewer", "sch_viewer")
    conn.commit()
    yield {"conn": conn, "pg_db": pg_db, "word": word,
           "analyst": analyst, "admin": admin, "viewer": viewer}
    conn.close()


@pytest.fixture()
def sync_jobs(monkeypatch):
    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance",
                        classmethod(lambda cls: sync))
    return sync


@pytest.fixture()
def scheduler():
    from Api.utils.utils import get_connection

    return Scheduler(get_connection=get_connection)


def _audit(world, action, resource):
    conn = connect(world["pg_db"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, detail FROM audit_log"
                        " WHERE action = %s AND resource = %s ORDER BY id",
                        (action, resource))
            return cur.fetchall()
    finally:
        conn.close()


def _set_role(world, user_id, role, active=True):
    with world["conn"].cursor() as cur:
        cur.execute("UPDATE users SET role = %s, is_active = %s WHERE id = %s",
                    (role, active, user_id))
    world["conn"].commit()


class TestTheSchema:
    def test_the_constraints_fire_by_name(self, world):
        conn = world["conn"]
        base = ("INSERT INTO job_schedules (schedule_type, name, owner_user_id,"
                " payload, interval_minutes) VALUES (%s, %s, %s, '{}'::jsonb, %s)")
        owner = world["analyst"]["id"]
        cases = {
            "ck_job_schedules_type": ("nonsense", "x1", 60),
            "ck_job_schedules_interval": ("report_run", "x2", 4),
            "ck_job_schedules_name": ("report_run", "   ", 60),
            "ck_job_schedules_failures": None,   # separate UPDATE below
        }
        for constraint, args in cases.items():
            if args is None:
                continue
            with pytest.raises(psycopg2.errors.CheckViolation, match=constraint):
                with conn.cursor() as cur:
                    cur.execute(base, (args[0], args[1], owner, args[2]))
            conn.rollback()
        with pytest.raises(psycopg2.errors.UniqueViolation,
                           match="uq_job_schedules_owner_name"):
            with conn.cursor() as cur:
                cur.execute(base, ("report_run", "dup", owner, 60))
                cur.execute(base, ("report_run", "dup", owner, 60))
        conn.rollback()
        with pytest.raises(psycopg2.errors.CheckViolation,
                           match="ck_job_schedules_failures"):
            with conn.cursor() as cur:
                cur.execute(base, ("report_run", "neg", owner, 60))
                cur.execute("UPDATE job_schedules SET consecutive_failures = -1"
                            " WHERE name = 'neg'")
        conn.rollback()

    def test_the_same_name_is_allowed_for_two_owners(self, world):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="report_run",
                                    name="nightly", owner_user_id=world["analyst"]["id"],
                                    payload={"report_id": "search_results",
                                             "parameters": {"criteria": {"text": "x"}}},
                                    interval_minutes=720)
        row2 = store.create_schedule(conn, schedule_type="report_run",
                                     name="nightly", owner_user_id=world["admin"]["id"],
                                     payload={"report_id": "search_results",
                                              "parameters": {"criteria": {"text": "x"}}},
                                     interval_minutes=720)
        assert row["id"] != row2["id"]
        # cleanup keeps later fixtures' name-space free
        store.delete_schedule(conn, row["id"])
        store.delete_schedule(conn, row2["id"])


class TestTheStore:
    def test_create_sets_the_first_fire_one_interval_ahead(self, world):
        conn = world["conn"]
        before = datetime.datetime.now(datetime.timezone.utc)
        row = store.create_schedule(conn, schedule_type="report_run",
                                    name=f"weekly {_U}",
                                    owner_user_id=world["analyst"]["id"],
                                    payload={"report_id": "search_results",
                                             "parameters": {"criteria": {"text": "x"}}},
                                    interval_minutes=10080)
        gap = _parse(row["next_run_at"]) - before
        assert datetime.timedelta(minutes=10079) < gap < datetime.timedelta(minutes=10081)
        assert row["enabled"] is True and row["last_job_id"] is None
        store.delete_schedule(conn, row["id"])

    def test_invalid_payloads_are_refused_before_the_database(self, world):
        with pytest.raises(ScheduleError, match="no registered report"):
            store.create_schedule(world["conn"], schedule_type="report_run",
                                  name="bad", owner_user_id=world["analyst"]["id"],
                                  payload={"report_id": "ghost"}, interval_minutes=60)

    def test_pause_and_resume(self, world):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="rule_evaluation",
                                    name=f"pause {_U}",
                                    owner_user_id=world["admin"]["id"],
                                    payload={}, interval_minutes=30)
        paused = store.update_schedule(conn, row["id"], enabled=False)
        assert paused["enabled"] is False and paused["disabled_reason"]
        resumed = store.update_schedule(conn, row["id"], enabled=True)
        assert resumed["enabled"] is True and resumed["disabled_reason"] is None
        assert resumed["consecutive_failures"] == 0
        # resume fires a full interval from now, never immediately
        before = datetime.datetime.now(datetime.timezone.utc)
        gap = _parse(resumed["next_run_at"]) - before
        assert datetime.timedelta(minutes=29) < gap < datetime.timedelta(minutes=31)
        store.delete_schedule(conn, row["id"])

    def test_deleting_the_owner_deletes_the_schedules(self, world):
        conn = world["conn"]
        with conn, conn.cursor() as cur:
            temp = _user(cur, "analyst", f"sch_temp{uuid.uuid4().hex[:4]}")
        row = store.create_schedule(conn, schedule_type="report_run",
                                    name="doomed", owner_user_id=temp["id"],
                                    payload={"report_id": "search_results",
                                             "parameters": {"criteria": {"text": "x"}}},
                                    interval_minutes=60)
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (temp["id"],))
        assert store.get_schedule(conn, row["id"]) is None


class TestTheClaim:
    def test_a_claim_is_at_most_once_and_moves_the_interval(self, world):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="rule_evaluation",
                                    name=f"claim {_U}",
                                    owner_user_id=world["admin"]["id"],
                                    payload={}, interval_minutes=240)
        # make it due now
        with conn.cursor() as cur:
            cur.execute("UPDATE job_schedules SET next_run_at = NOW()"
                        " WHERE id = %s", (row["id"],))
        conn.commit()
        claimed = store.claim_due(conn)
        assert [s["id"] for s in claimed] == [row["id"]]
        assert claimed[0]["last_run_at"] is not None
        # the next fire moved one interval ahead: a second claim finds nothing
        until = _parse(claimed[0]["next_run_at"])
        assert (until - datetime.datetime.now(datetime.timezone.utc)) > \
            datetime.timedelta(minutes=239)
        assert store.claim_due(conn) == []
        store.delete_schedule(conn, row["id"])

    def test_a_paused_schedule_is_never_claimed(self, world):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="rule_evaluation",
                                    name=f"quiet {_U}",
                                    owner_user_id=world["admin"]["id"],
                                    payload={}, interval_minutes=60)
        store.update_schedule(conn, row["id"], enabled=False)
        with conn.cursor() as cur:
            cur.execute("UPDATE job_schedules SET next_run_at = NOW() - interval"
                        " '1 hour' WHERE id = %s", (row["id"],))
        conn.commit()
        assert store.claim_due(conn) == []
        store.delete_schedule(conn, row["id"])

    def test_job_statuses_are_reflected_and_failures_counted(self, world):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="rule_evaluation",
                                    name=f"status {_U}",
                                    owner_user_id=world["admin"]["id"],
                                    payload={}, interval_minutes=60)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (job_id, job_type, status, options)"
                " VALUES (%s, 'rule_evaluation', 'FAILED', %s)",
                (f"J{_U}A", psycopg2.extras.Json({"schedule_id": str(row["id"])})))
            cur.execute("UPDATE job_schedules SET last_job_id = %s,"
                        " last_status = 'QUEUED' WHERE id = %s",
                        (f"J{_U}A", row["id"]))
        conn.commit()
        assert store.refresh_job_statuses(conn) >= 1
        fresh = store.get_schedule(conn, row["id"])
        assert fresh["last_status"] == "FAILED"
        assert fresh["consecutive_failures"] == 1
        # a good run resets the count
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = 'COMPLETED' WHERE job_id = %s",
                        (f"J{_U}A",))
        conn.commit()
        store.refresh_job_statuses(conn)
        assert store.get_schedule(conn, row["id"])["consecutive_failures"] == 0
        store.delete_schedule(conn, row["id"])


def _parse(iso):
    """The store speaks ISO strings at its API edge (like every other
    service in this codebase); tests compare moments."""
    return datetime.datetime.fromisoformat(iso)


def _make_due(conn, schedule_id):
    """Creation sets the first fire one interval ahead; a fire test fast-
    forwards the schedule so the tick takes it now."""
    with conn.cursor() as cur:
        cur.execute("UPDATE job_schedules SET next_run_at = NOW() - interval"
                    " '1 second' WHERE id = %s", (schedule_id,))
    conn.commit()


class TestTheFire:
    def test_a_report_fire_is_a_real_run_as_the_owner(self, world, sync_jobs,
                                                      scheduler):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="report_run",
                                    name=f"report fire {_U}",
                                    owner_user_id=world["analyst"]["id"],
                                    payload={"report_id": "search_results",
                                             "parameters": {"criteria":
                                                            {"text": world["word"]}}},
                                    interval_minutes=60)
        _make_due(conn, row["id"])
        claimed = store.claim_due(conn, limit=1)
        assert claimed and claimed[0]["id"] == row["id"]
        outcome = scheduler.fire(claimed[0])
        assert outcome["status"] == "fired", outcome
        # the job ran synchronously and completed; the run exists, is the
        # owner's, and the schedule records both
        fresh = store.get_schedule(conn, row["id"])
        assert fresh["last_job_id"] == outcome["job_id"]
        assert fresh["last_status"] in ("COMPLETED", "QUEUED")
        rc = connect(world["pg_db"])
        with rc.cursor() as cur:
            cur.execute("SELECT requested_by, status FROM report_runs"
                        " WHERE job_id = %s", (outcome["job_id"],))
            found = cur.fetchall()
        rc.close()
        assert found and found[0][1] == "completed"
        assert found[0][0] == world["analyst"]["id"], \
            "the run belongs to the schedule's owner, not to the scheduler"
        audits = _audit(world, "schedule.fired", f"job_schedule:{row['id']}")
        assert audits and audits[-1][0] == "scheduler"
        store.delete_schedule(conn, row["id"])

    def test_a_demoted_owner_disables_the_schedule_and_creates_nothing(
            self, world, sync_jobs, scheduler):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="report_run",
                                    name=f"demoted {_U}",
                                    owner_user_id=world["analyst"]["id"],
                                    payload={"report_id": "search_results",
                                             "parameters": {"criteria": {"text": "x"}}},
                                    interval_minutes=60)
        _set_role(world, world["analyst"]["id"], "viewer")
        try:
            _make_due(conn, row["id"])
            claimed = store.claim_due(conn, limit=1)
            outcome = scheduler.fire(claimed[0])
            assert outcome["status"] == "disabled"
            assert outcome["reason"] == "owner_role"
            fresh = store.get_schedule(conn, row["id"])
            assert fresh["enabled"] is False
            assert "role" in fresh["disabled_reason"]
            assert fresh["last_job_id"] is None, "no job was created"
            assert _audit(world, "schedule.disabled",
                          f"job_schedule:{row['id']}")
            assert not _audit(world, "schedule.fired",
                              f"job_schedule:{row['id']}")
        finally:
            _set_role(world, world["analyst"]["id"], "analyst")
        store.delete_schedule(conn, row["id"])

    def test_a_deactivated_owner_disables_the_schedule(self, world, sync_jobs,
                                                       scheduler):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="rule_evaluation",
                                    name=f"off owner {_U}",
                                    owner_user_id=world["admin"]["id"],
                                    payload={}, interval_minutes=60)
        _set_role(world, world["admin"]["id"], "admin", active=False)
        try:
            _make_due(conn, row["id"])
            claimed = store.claim_due(conn, limit=1)
            outcome = scheduler.fire(claimed[0])
            assert outcome == {"status": "disabled", "schedule_id": row["id"],
                               "reason": "owner_inactive"}
        finally:
            _set_role(world, world["admin"]["id"], "admin", active=True)
        store.delete_schedule(conn, row["id"])

    def test_a_payload_that_no_longer_validates_disables_the_schedule(
            self, world, sync_jobs, scheduler):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="report_run",
                                    name=f"ghost {_U}",
                                    owner_user_id=world["analyst"]["id"],
                                    payload={"report_id": "search_results",
                                             "parameters": {"criteria": {"text": "x"}}},
                                    interval_minutes=60)
        # Simulate the definition drifting away from what the schedule
        # stored (a retired report/version): the payload in the row no
        # longer validates against the registry.
        with conn.cursor() as cur:
            cur.execute("UPDATE job_schedules SET payload = %s WHERE id = %s",
                        (psycopg2.extras.Json({"report_id": "ghost_report"}),
                         row["id"]))
        conn.commit()
        _make_due(conn, row["id"])
        claimed = store.claim_due(conn, limit=1)
        outcome = scheduler.fire(claimed[0])
        assert outcome["status"] == "disabled"
        assert outcome["reason"] == "definition_invalid"
        assert store.get_schedule(conn, row["id"])["enabled"] is False
        store.delete_schedule(conn, row["id"])

    def test_a_rule_evaluation_fire_uses_the_schedule_trigger(
            self, world, sync_jobs, scheduler):
        conn = world["conn"]
        row = store.create_schedule(conn, schedule_type="rule_evaluation",
                                    name=f"rules fire {_U}",
                                    owner_user_id=world["admin"]["id"],
                                    payload={}, interval_minutes=60)
        _make_due(conn, row["id"])
        claimed = store.claim_due(conn, limit=1)
        outcome = scheduler.fire(claimed[0])
        assert outcome["status"] == "fired"
        assert sync_jobs.repo.get(outcome["job_id"])["job_type"] == "rule_evaluation"
        options = sync_jobs.repo.get(outcome["job_id"])["options"]
        assert options["trigger"] == "schedule"
        assert str(options["schedule_id"]) == str(row["id"])
        store.delete_schedule(conn, row["id"])
