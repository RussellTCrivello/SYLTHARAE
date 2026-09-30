"""Step 21 against PostgreSQL: applying retention really deletes.

Executed on the migrated schema (m0032) against the real tables.
Properties:

* ``retention`` is a valid schedule type by constraint name; unknown types
  are still refused;
* a policy deletes exactly the aged, eligible rows - old finished jobs
  with their event stream (cascade), old terminal report runs with their
  artifacts - and never the live ones (queued/running/paused/cancelling
  jobs, unfinished or running runs), whatever their age;
* ``0`` days keeps forever: old rows survive a default keep-forever policy;
* deletion is batched: five rows with a batch of two is three committed
  batches, all five rows gone;
* every applied area writes one ``retention.applied`` audit row with the
  actor, the days, the deleted count and the batch count;
* the overview answers for every area: effective days, eligible rows now,
  table size, oldest row;
* a fired retention schedule produces exactly one synchronous
  ``retention`` job, which prunes an aged job;
* m0032's downgrade disables retention schedules with a stated reason and
  restores a three-type check that refuses *new* retention schedules
  (``NOT VALID``: the existing rows survive); the upgrade re-widens it.
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg2
import psycopg2.extras
import pytest

from database.migrations.m0032_retention_schedule_type import (
    downgrade as _m0032_down,
    upgrade as _m0032_up)
from services.jobs.manager import JobManager
from services.retention import service
from services.retention.model import AREAS
from services.scheduling import store

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_FINGERPRINT = hashlib.sha256(b"retention-test").hexdigest()
_LIVE = ("QUEUED", "RUNNING", "PAUSED", "CANCELLING")


def _user(cur, role, name):
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)"
                " RETURNING id", (f"{name}_{_U}", role))
    return cur.fetchone()[0]


@pytest.fixture(scope="module", autouse=True)
def _default_policies():
    """The settings file persists across test modules; this module starts
    from the declared default policies."""
    from services.retention.model import POLICIES, setting_key
    from settings.settings_adapter import get_settings

    for area, policy in POLICIES.items():
        get_settings().set_setting(setting_key(area), policy["default_days"])


@pytest.fixture(scope="module")
def world(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        admin = _user(cur, "admin", "ret_admin")
    conn.commit()
    yield {"conn": conn, "pg_db": pg_db, "admin": admin}
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

    from services.scheduling.scheduler import Scheduler

    return Scheduler(get_connection=get_connection)


# ---------------------------------------------------------------------------
# Seed helpers (every row tagged with the module's _U so assertions survive
# a shared session database)
# ---------------------------------------------------------------------------

def _job(cur, status, age_days, *, prefix, n_events=2):
    job_id = f"{prefix}{uuid.uuid4().hex[:10]}"
    cur.execute(
        "INSERT INTO jobs (job_id, job_type, status, created_by, created_at,"
        " completed_at) VALUES (%s, 'report_run', %s, 'ret-test',"
        " NOW() - make_interval(days => %s),"
        " CASE WHEN %s = ANY(%s) THEN NULL"
        " ELSE NOW() - make_interval(days => %s) END) RETURNING job_id",
        (job_id, status, age_days, status, list(_LIVE), age_days))
    job_id = cur.fetchone()[0]
    for _ in range(n_events):
        cur.execute("INSERT INTO job_events (job_id, event_type, payload)"
                    " VALUES (%s, 'PROGRESS', '{}'::jsonb)", (job_id,))
    return job_id


def _job_count(pg_db, prefix):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs WHERE job_id LIKE %s", (prefix,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _alert(cur, age_days):
    cur.execute(
        "INSERT INTO alerts (type, priority, title, message, created_at)"
        " VALUES ('retention-test', 'low', %s, 'seeded',"
        " CURRENT_TIMESTAMP - make_interval(days => %s)) RETURNING id",
        (f"retention {_U} alert {uuid.uuid4().hex[:8]}", age_days))
    return cur.fetchone()[0]


def _alerts_by_title(pg_db):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM alerts WHERE title LIKE %s",
                        (f"retention {_U}%",))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _run(cur, status, age_days, *, finished=True, artifact=False):
    cur.execute(
        "INSERT INTO report_runs (report_id, report_version, definition_fingerprint,"
        " parameters, parameters_fingerprint, requester_username, requester_role,"
        " generator_version, status, error, requested_at, finished_at)"
        " VALUES ('search_results', 1, %s, '{}'::jsonb, %s, 'ret-test', 'admin',"
        " 'test', %s, CASE WHEN %s = 'failed' THEN 'seeded' END,"
        " NOW() - make_interval(days => %s),"
        " CASE WHEN %s THEN NOW() - make_interval(days => %s) END) RETURNING id",
        (_FINGERPRINT, _FINGERPRINT, status, status, age_days, finished, age_days))
    run_id = cur.fetchone()[0]
    if artifact:
        digest = hashlib.sha256(b"abc").hexdigest()
        cur.execute(
            "INSERT INTO report_artifacts (run_id, format, renderer_version,"
            " filename, media_type, byte_size, sha256, content, manifest,"
            " manifest_sha256, creator_username, creator_role)"
            " VALUES (%s, 'csv', 'test', 'a.csv', 'text/csv', 3, %s, %s,"
            " '{}'::jsonb, %s, 'ret-test', 'admin')",
            (run_id, digest, psycopg2.Binary(b"abc"), digest))
    return run_id


def _rule_with_history(cur, admin, age_days):
    """One rule, one aged evaluation, one aged ledger row."""
    cur.execute(
        "INSERT INTO monitoring_rules (owner_user_id, name, definition,"
        " definition_fingerprint, status) VALUES (%s, %s, '{}'::jsonb, %s,"
        " 'active') RETURNING id",
        (admin, f"ret-rule-{_U}-{uuid.uuid4().hex[:6]}", _FINGERPRINT))
    rule_id = cur.fetchone()[0]
    cur.execute(
        'INSERT INTO rule_evaluations (rule_id, rule_version, "trigger", status,'
        " definition_fingerprint, reference_date, evaluated_at)"
        " VALUES (%s, 1, 'schedule', 'completed', %s, CURRENT_DATE,"
        " NOW() - make_interval(days => %s)) RETURNING id",
        (rule_id, _FINGERPRINT, age_days))
    evaluation_id = cur.fetchone()[0]
    cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (hashlib.sha256(f"ret-hash-{_U}-{evaluation_id}".encode())
                 .hexdigest(),))
    hash_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO rule_subject_ledger (rule_id, subject_key, hash_id,"
        " rule_version, state, group_key, first_matched_at, matched_evaluation_id,"
        " state_changed_at) VALUES (%s, %s, %s, 1, 'baseline', %s,"
        " NOW() - make_interval(days => %s), %s,"
        " NOW() - make_interval(days => %s))",
        (rule_id, f"subj-{_U}-{evaluation_id}", hash_id, f"g-{_U}", age_days,
         evaluation_id, age_days))
    return rule_id, evaluation_id


def _apply(world, area, **kwargs):
    conn = connect(world["pg_db"])
    try:
        return service.apply_area(conn, area, actor="ret-test", **kwargs)
    finally:
        conn.close()


def _audit(world, action, resource):
    conn = connect(world["pg_db"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, detail FROM audit_log"
                        " WHERE action = %s AND resource = %s ORDER BY id DESC",
                        (action, resource))
            return cur.fetchall()
    finally:
        conn.close()


def _seeded(cur):
    """Context manager: a transaction that seeds rows and commits."""
    return cur  # documentation shim; tests use `with conn, conn.cursor()`


# ---------------------------------------------------------------------------
# The schema (m0032)
# ---------------------------------------------------------------------------

class TestTheSchema:
    def test_retention_is_a_valid_schedule_type_by_constraint_name(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT conname FROM pg_constraint WHERE conrelid"
                            " = 'job_schedules'::regclass AND contype = 'c'"
                            " AND pg_get_constraintdef(oid) LIKE '%schedule_type%'")
                names = [r[0] for r in cur.fetchall()]
                assert "ck_job_schedules_type" in names
                cur.execute(
                    "INSERT INTO job_schedules (schedule_type, name, owner_user_id,"
                    " payload, interval_minutes) VALUES ('retention', %s, %s,"
                    " '{}'::jsonb, 1440) RETURNING id",
                    (f"ret-sched-{_U}-{uuid.uuid4().hex[:6]}", world["admin"]))
                schedule_id = cur.fetchone()[0]
                cur.execute("DELETE FROM job_schedules WHERE id = %s", (schedule_id,))
        finally:
            conn.close()

    def test_unknown_types_are_still_refused(self, world):
        conn = connect(world["pg_db"])
        try:
            with pytest.raises(psycopg2.errors.CheckViolation) as excinfo:
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO job_schedules (schedule_type, name,"
                        " owner_user_id, payload, interval_minutes)"
                        " VALUES ('everything', %s, %s, '{}'::jsonb, 1440)",
                        (f"bad-sched-{_U}-{uuid.uuid4().hex[:6]}", world["admin"]))
            assert "ck_job_schedules_type" in str(excinfo.value)
        finally:
            conn.rollback()
            conn.close()

    def test_downgrade_disables_retention_schedules_and_refuses_new_ones(
            self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO job_schedules (schedule_type, name, owner_user_id,"
                    " payload, interval_minutes) VALUES ('retention', %s, %s,"
                    " '{}'::jsonb, 1440) RETURNING id",
                    (f"down-sched-{_U}", world["admin"]))
                schedule_id = cur.fetchone()[0]
            conn.commit()
            with conn, conn.cursor() as cur:
                _m0032_down(conn)
            with conn, conn.cursor() as cur:
                cur.execute("SELECT enabled, disabled_reason FROM job_schedules"
                            " WHERE id = %s", (schedule_id,))
                enabled, reason = cur.fetchone()
                assert enabled is False
                assert "retention" in (reason or "")
                with pytest.raises(psycopg2.errors.CheckViolation):
                    cur.execute(
                        "INSERT INTO job_schedules (schedule_type, name,"
                        " owner_user_id, payload, interval_minutes)"
                        " VALUES ('retention', %s, %s, '{}'::jsonb, 1440)",
                        (f"down2-sched-{_U}", world["admin"]))
        finally:
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM job_schedules WHERE name LIKE %s",
                            (f"down%sched-{_U}%",))
                _m0032_up(conn)
            conn.commit()
            conn.close()


# ---------------------------------------------------------------------------
# The jobs area
# ---------------------------------------------------------------------------

class TestTheJobsArea:
    def test_old_finished_jobs_are_deleted_with_their_events(self, world):
        prefix = f"retjob{_U}a"
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                for status in ("COMPLETED", "FAILED", "CANCELLED"):
                    _job(cur, status, 400, prefix=prefix)
            conn.commit()
            assert _job_count(world["pg_db"], prefix + "%") == 3
            result = _apply(world, "jobs", days=90)
            assert result["deleted"] >= 3
            assert _job_count(world["pg_db"], prefix + "%") == 0
            with conn.cursor() as cur:
                # the event stream went with them (ON DELETE CASCADE)
                cur.execute("SELECT count(*) FROM job_events je JOIN jobs j"
                            " ON j.job_id = je.job_id WHERE j.job_id LIKE %s",
                            (prefix + "%",))
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()

    def test_live_jobs_survive_whatever_their_age(self, world):
        prefix = f"retjob{_U}b"
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                for status in _LIVE:
                    _job(cur, status, 400, prefix=prefix)
            conn.commit()
            _apply(world, "jobs", days=90)
            assert _job_count(world["pg_db"], prefix + "%") == 4
        finally:
            conn.close()

    def test_deletion_is_batched_and_counted(self, world):
        prefix = f"retjob{_U}c"
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                for _ in range(5):
                    _job(cur, "COMPLETED", 400, prefix=prefix, n_events=0)
            conn.commit()
            result = _apply(world, "jobs", days=90, batch=2)
            assert result["deleted"] >= 5
            assert result["batches"] >= 3
            assert _job_count(world["pg_db"], prefix + "%") == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The report_runs area
# ---------------------------------------------------------------------------

class TestTheReportRunsArea:
    def test_old_terminal_runs_are_deleted_and_artifacts_go_with_them(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                run_id = _run(cur, "failed", 400, finished=True, artifact=True)
            conn.commit()
            result = _apply(world, "report_runs", days=365)
            assert result["deleted"] >= 1
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM report_runs WHERE id = %s",
                            (run_id,))
                assert cur.fetchone()[0] == 0
                cur.execute("SELECT count(*) FROM report_artifacts WHERE run_id = %s",
                            (run_id,))
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()

    def test_unfinished_and_running_runs_survive_whatever_their_age(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                running = _run(cur, "running", 400, finished=False)
                queued = _run(cur, "queued", 400, finished=False)
            conn.commit()
            _apply(world, "report_runs", days=365)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM report_runs WHERE id = ANY(%s)",
                            ([running, queued],))
                assert cur.fetchone()[0] == 2
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Keep forever
# ---------------------------------------------------------------------------

class TestKeepForever:
    def test_zero_days_deletes_nothing_even_when_the_rows_are_ancient(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                _alert(cur, 4000)
                _alert(cur, 4000)
            conn.commit()
            before = _alerts_by_title(world["pg_db"])
            assert before >= 2
            result = _apply(world, "notifications")  # default: keep forever
            assert result["deleted"] == 0
            assert result["kept_forever"] is True
            assert _alerts_by_title(world["pg_db"]) == before
        finally:
            conn.close()

    def test_the_overview_shows_zero_eligible_for_keep_forever_areas(self, world):
        conn = connect(world["pg_db"])
        try:
            overview = {o["area"]: o for o in service.overview(conn)}
        finally:
            conn.close()
        assert set(overview) == set(AREAS)
        for area in ("scenario_outcomes", "notifications", "report_artifacts",
                     "path_revisions", "audit_log"):
            assert overview[area]["days"] == 0, area
            assert overview[area]["eligible"] == 0, area


# ---------------------------------------------------------------------------
# The rule areas
# ---------------------------------------------------------------------------

class TestTheRuleAreas:
    def test_the_ledger_is_pruned_by_its_own_policy(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                _rule_with_history(cur, world["admin"], 400)
            conn.commit()
            result = _apply(world, "rule_ledger", days=180)
            assert result["deleted"] >= 1
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM rule_subject_ledger"
                            " WHERE subject_key LIKE %s", (f"subj-{_U}%",))
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()

    def test_aged_evaluations_are_pruned_and_the_ledger_cascades(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                _rule_with_history(cur, world["admin"], 400)
            conn.commit()
            result = _apply(world, "rule_evaluations", days=180)
            assert result["deleted"] >= 1
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM rule_subject_ledger"
                            " WHERE subject_key LIKE %s", (f"subj-{_U}%",))
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The overview and the audit row
# ---------------------------------------------------------------------------

class TestOverviewAndAudit:
    def test_the_overview_answers_for_every_area(self, world):
        prefix = f"retjob{_U}d"
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                _job(cur, "COMPLETED", 400, prefix=prefix, n_events=0)
            conn.commit()
            overview = {o["area"]: o for o in service.overview(conn)}
        finally:
            conn.close()
        assert overview["jobs"]["eligible"] >= 1
        assert overview["jobs"]["total"] >= 1
        assert overview["jobs"]["oldest"] is not None
        assert overview["jobs"]["days"] == 90  # the declared default
        for area, entry in overview.items():
            assert entry["description"] and entry["deletes"], area
            assert entry["total"] >= entry["eligible"], area

    def test_notifications_oldest_is_measured_not_assumed(self, world):
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                _alert(cur, 2000)
            conn.commit()
            overview = {o["area"]: o for o in service.overview(conn)}
        finally:
            conn.close()
        # measured: the table holds 2000-day-old rows; the policy still says
        # keep forever, so nothing is eligible - visible, not hidden
        assert overview["notifications"]["oldest"] is not None
        assert overview["notifications"]["days"] == 0
        assert overview["notifications"]["eligible"] == 0

    def test_every_applied_area_writes_an_audit_row_with_its_counts(self, world):
        prefix = f"retjob{_U}e"
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                for _ in range(3):
                    _job(cur, "COMPLETED", 400, prefix=prefix, n_events=0)
            conn.commit()
        finally:
            conn.close()
        result = _apply(world, "jobs", days=90, batch=2)
        rows = _audit(world, "retention.applied", "retention:jobs")
        assert rows, "no retention.applied audit row was written"
        username, detail = rows[0]
        assert username == "ret-test"
        assert detail["days"] == 90
        assert detail["deleted"] >= 3
        assert detail["batches"] >= 2
        assert result["batches"] >= 2


# ---------------------------------------------------------------------------
# The retention job and the schedule fire
# ---------------------------------------------------------------------------

class TestTheRetentionJob:
    def test_run_retention_applies_the_named_areas_and_sums(self, world):
        prefix = f"retjob{_U}f"
        conn = connect(world["pg_db"])
        try:
            with conn, conn.cursor() as cur:
                _job(cur, "COMPLETED", 400, prefix=prefix, n_events=0)
                _alert(cur, 4000)
            conn.commit()
        finally:
            conn.close()
        from Api.utils.utils import get_connection

        result = service.run_retention(get_connection, areas=["jobs"],
                                       actor="ret-test")
        assert set(result["areas"]) == {"jobs"}
        assert result["deleted"] >= 1
        assert result["cancelled"] is False
        assert _job_count(world["pg_db"], prefix + "%") == 0

    def test_a_fired_retention_schedule_is_one_sync_retention_job(
            self, world, sync_jobs, scheduler):
        """End to end: an administrator's retention schedule is claimed by a
        tick and produces exactly one ``retention`` job, which prunes an aged
        finished job - and the schedule records it."""
        conn = world["conn"]
        prefix = f"retjob{_U}g"
        with conn, conn.cursor() as cur:
            _job(cur, "COMPLETED", 400, prefix=prefix, n_events=0)
        conn.commit()
        row = store.create_schedule(conn, schedule_type="retention",
                                    name=f"ret fire {_U}",
                                    owner_user_id=world["admin"],
                                    payload={}, interval_minutes=60)
        with conn.cursor() as cur:
            cur.execute("UPDATE job_schedules SET next_run_at = NOW()"
                        " - interval '1 minute' WHERE id = %s", (row["id"],))
        conn.commit()
        claimed = store.claim_due(conn, limit=10)
        assert [s["id"] for s in claimed if s["id"] == row["id"]], \
            "the retention schedule was not claimed"
        outcome = scheduler.fire([s for s in claimed if s["id"] == row["id"]][0])
        assert outcome["status"] == "fired", outcome
        job = sync_jobs.repo.get(outcome["job_id"])
        assert job["job_type"] == "retention"
        assert job["status"] == "COMPLETED"
        assert job["source"] == "schedules:retention"
        assert _job_count(world["pg_db"], prefix + "%") == 0
        fresh = store.get_schedule(conn, row["id"])
        assert fresh["last_job_id"] == outcome["job_id"]
        store.delete_schedule(conn, row["id"])
