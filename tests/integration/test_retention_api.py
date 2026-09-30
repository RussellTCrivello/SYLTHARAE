"""Step 21 integration: the retention API over HTTP.

Real users, real PostgreSQL, a synchronous JobManager. Properties:

* retention is administrative: a viewer gets the same absent response as
  a forbidden one (the rules convention) on the page and on every API;
* the overview answers for every policy area with its effective days;
* a days change is validated twice (the model refuses booleans and
  out-of-bounds values before anything is stored), audited, and effective
  immediately in the overview;
* run-now goes through the real ``retention`` job: an aged finished job is
  pruned, a live one survives, and both the request and the applied
  deletion are audited with the actor's name.
"""

from __future__ import annotations

import uuid

import pytest

from _seed import connect
from test_reports_api import sync_jobs as sync_jobs_fixture  # noqa: F401, F811

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module", autouse=True)
def _default_policies():
    """The settings file persists across tests and sessions; every test in
    this module starts from the declared default policies."""
    from services.retention.model import POLICIES, setting_key
    from settings.settings_adapter import get_settings

    for area, policy in POLICIES.items():
        get_settings().set_setting(setting_key(area), policy["default_days"])


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _login_new(app, role):
    from core.security.service import get_auth_service

    username, password = f"ret_{role}_{uuid.uuid4().hex[:6]}", "retention-password-123"
    get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c, username


def _old_job(pg_db, status="COMPLETED", age_days=400):
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (job_id, job_type, status, created_by,"
                " created_at, completed_at) VALUES (%s, 'report_run', %s,"
                " 'ret-api', NOW() - make_interval(days => %s),"
                " NOW() - make_interval(days => %s)) RETURNING job_id",
                (f"retapi{_U}{uuid.uuid4().hex[:8]}", status, age_days, age_days))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _job_gone(pg_db, job_id):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs WHERE job_id = %s", (job_id,))
            return cur.fetchone()[0] == 0
    finally:
        conn.close()


def _audit(pg_db, action, resource):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, detail FROM audit_log"
                        " WHERE action = %s AND resource = %s ORDER BY id DESC",
                        (action, resource))
            return cur.fetchall()
    finally:
        conn.close()


def test_retention_is_administrative_over_http(app, pg_db):
    viewer, _ = _login_new(app, "viewer")
    assert viewer.get("/retention").status_code == 403
    assert viewer.get("/api/retention").status_code == 403
    assert viewer.put("/api/retention/jobs", json={"days": 30},
                      headers={"X-CSRFToken": _csrf(viewer)}).status_code == 403
    assert viewer.post("/api/retention/run", json={},
                       headers={"X-CSRFToken": _csrf(viewer)}).status_code == 403

    admin, _ = _login_new(app, "admin")
    page = admin.get("/retention")
    assert page.status_code == 200
    overview = admin.get("/api/retention")
    assert overview.status_code == 200
    body = overview.get_json()
    areas = {item["area"] for item in body["items"]}
    assert {"jobs", "rule_ledger", "rule_evaluations", "report_runs",
            "notifications", "report_artifacts"} <= areas
    days = {item["area"]: item["days"] for item in body["items"]}
    assert days["jobs"] == 90 and days["report_runs"] == 365
    assert days["audit_log"] == 0  # keep forever is explicit, visible


def test_a_days_change_is_validated_audited_and_effective(app, pg_db):
    admin, username = _login_new(app, "admin")

    ok = admin.put("/api/retention/jobs", json={"days": 30},
                   headers={"X-CSRFToken": _csrf(admin)})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    assert ok.get_json() == {"success": True, "area": "jobs", "days": 30,
                             "kept_forever": False}
    assert _audit(pg_db, "retention.policy_changed", "retention")
    assert _audit(pg_db, "retention.policy_changed", "retention")[0][0] == username

    overview = admin.get("/api/retention").get_json()["items"]
    assert {i["area"]: i["days"] for i in overview}["jobs"] == 30

    # keep forever is a first-class value, not the absence of one
    forever = admin.put("/api/retention/jobs", json={"days": 0},
                        headers={"X-CSRFToken": _csrf(admin)})
    assert forever.status_code == 200
    assert forever.get_json()["kept_forever"] is True
    overview = admin.get("/api/retention").get_json()["items"]
    assert {i["area"]: i["days"] for i in overview}["jobs"] == 0

    for bad in ({"days": 4000}, {"days": -1}, {"days": True}, {"days": "30"},
                {}, {"days": None}):
        refused = admin.put("/api/retention/jobs", json=bad,
                            headers={"X-CSRFToken": _csrf(admin)})
        assert refused.status_code == 400, bad
        assert refused.get_json()["error"]["code"] == "VALIDATION_FAILED"
    unknown = admin.put("/api/retention/everything", json={"days": 30},
                        headers={"X-CSRFToken": _csrf(admin)})
    assert unknown.status_code == 400


def test_run_now_prunes_through_the_real_job(app, pg_db, sync_jobs_fixture):  # noqa: F811 (fixture import registers the fixture)
    admin, username = _login_new(app, "admin")
    aged = _old_job(pg_db, "COMPLETED", 400)
    live = _old_job(pg_db, "RUNNING", 400)

    # the policy first (the settings file persists across tests in the
    # session, and an earlier test may have set keep-forever)
    policy = admin.put("/api/retention/jobs", json={"days": 30},
                       headers={"X-CSRFToken": _csrf(admin)})
    assert policy.status_code == 200

    fired = admin.post("/api/retention/run", json={"area": "jobs"},
                       headers={"X-CSRFToken": _csrf(admin)})
    assert fired.status_code == 200, fired.get_data(as_text=True)
    job_id = fired.get_json()["job_id"]
    record = sync_jobs_fixture.repo.get(job_id)
    assert record["job_type"] == "retention"
    assert record["status"] == "COMPLETED"
    assert record["options"]["areas"] == ["jobs"]
    assert record["options"]["actor"] == username

    assert _job_gone(pg_db, aged), "the aged finished job was not pruned"
    assert not _job_gone(pg_db, live), "the running job must survive"

    requested = _audit(pg_db, "retention.run_requested", "retention")
    assert requested and requested[0][0] == username
    applied = _audit(pg_db, "retention.applied", "retention:jobs")
    assert applied and applied[0][0] == username
    assert applied[0][1]["deleted"] >= 1


def test_run_now_refuses_an_unknown_area(app, pg_db):
    admin, _ = _login_new(app, "admin")
    refused = admin.post("/api/retention/run", json={"area": "everything"},
                         headers={"X-CSRFToken": _csrf(admin)})
    assert refused.status_code == 400
    assert refused.get_json()["error"]["code"] == "VALIDATION_FAILED"
