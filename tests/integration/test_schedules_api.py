"""Step 20 integration: the schedules API over HTTP.

Real users logged in over HTTP, PostgreSQL, and a synchronous JobManager
(``run now`` really creates and executes the job). Properties: a schedule
is created, edited, paused, resumed, run now and deleted by its owner or
an administrator - and not by another analyst (absent and not-permitted
look the same, the rules convention); evaluation schedules are
administrative; payloads are validated against the registry at creation
and edit; every management change is audited.
"""

from __future__ import annotations

import uuid

import pytest

from _seed import connect
from test_reports_api import sync_jobs as sync_jobs_fixture  # noqa: F401, F811

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _post(c, url, payload=None, csrf=True):
    headers = {"X-CSRFToken": _csrf(c)} if csrf else {}
    return c.post(url, json=payload if payload is not None else {}, headers=headers)


def _login_new(app, role):
    from core.security.service import get_auth_service

    username, password = f"sch_{role}_{uuid.uuid4().hex[:6]}", f"sch-{role}-password-123"
    get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c, username


_REPORT_PAYLOAD = {"report_id": "search_results",
                   "parameters": {"criteria": {"text": "anything"}}}


def _audit(pg_db, action, resource):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM audit_log"
                        " WHERE action = %s AND resource = %s ORDER BY id",
                        (action, resource))
            return cur.fetchall()
    finally:
        conn.close()


def test_a_report_schedule_lives_over_http(app, pg_db, sync_jobs_fixture):  # noqa: F811 (fixture import registers the fixture)
    analyst, _ = _login_new(app, "analyst")

    created = _post(analyst, "/api/schedules",
                    {"schedule_type": "report_run", "name": f"nightly {_U}",
                     "interval_minutes": 720, "payload": _REPORT_PAYLOAD})
    assert created.status_code == 201, created.get_data(as_text=True)
    schedule = created.get_json()["schedule"]
    assert schedule["schedule_type"] == "report_run"
    assert schedule["enabled"] is True and schedule["last_job_id"] is None
    assert schedule["payload"]["report_id"] == "search_results"
    sid = schedule["id"]
    assert _audit(pg_db, "schedule.created", f"job_schedule:{sid}")

    # the owner and an administrator see it; another analyst does not
    mine = analyst.get("/api/schedules").get_json()
    assert [s["id"] for s in mine["items"]] == [sid]
    other, _ = _login_new(app, "analyst")
    assert other.get(f"/api/schedules/{sid}").status_code == 404
    assert other.get("/api/schedules?all=1").status_code == 403
    admin, _ = _login_new(app, "admin")
    every = admin.get("/api/schedules?all=1").get_json()
    assert sid in [s["id"] for s in every["items"]]

    # edit the interval; the payload is revalidated
    resp = analyst.put(f"/api/schedules/{sid}",
                       json={"interval_minutes": 1440},
                       headers={"X-CSRFToken": _csrf(analyst)})
    assert resp.status_code == 200
    assert resp.get_json()["schedule"]["interval_minutes"] == 1440
    bad = analyst.put(f"/api/schedules/{sid}",
                      json={"payload": {"report_id": "ghost"}},
                      headers={"X-CSRFToken": _csrf(analyst)})
    assert bad.status_code == 400
    assert bad.get_json()["error"]["code"] == "VALIDATION_FAILED"

    # run now: the same fire path as the scheduler, synchronously executed
    now_resp = _post(analyst, f"/api/schedules/{sid}/run_now")
    assert now_resp.status_code == 200, now_resp.get_data(as_text=True)
    result = now_resp.get_json()["result"]
    assert result["status"] == "fired" and result["job_id"]
    jobs = analyst.get(f"/api/schedules/{sid}/jobs").get_json()
    assert [j["job_id"] for j in jobs["items"]] == [result["job_id"]]
    record = sync_jobs_fixture.repo.get(result["job_id"])
    assert record["job_type"] == "report_run"
    assert record["options"]["schedule_id"] == sid
    fresh = analyst.get(f"/api/schedules/{sid}").get_json()["schedule"]
    assert fresh["last_job_id"] == result["job_id"]

    # pause / resume / delete
    assert _post(analyst, f"/api/schedules/{sid}/pause").status_code == 200
    paused = analyst.get(f"/api/schedules/{sid}").get_json()["schedule"]
    assert paused["enabled"] is False and paused["disabled_reason"]
    assert _post(analyst, f"/api/schedules/{sid}/resume").status_code == 200
    assert _post(analyst, f"/api/schedules/{sid}/run_now").status_code == 200, \
        "run now works on a resumed schedule"
    deleted = analyst.delete(f"/api/schedules/{sid}",
                             headers={"X-CSRFToken": _csrf(analyst)})
    assert deleted.status_code == 200
    assert analyst.get(f"/api/schedules/{sid}").status_code == 404
    assert _audit(pg_db, "schedule.deleted", f"job_schedule:{sid}")


def test_evaluation_schedules_are_administrative(app, pg_db):
    analyst, _ = _login_new(app, "analyst")
    refused = _post(analyst, "/api/schedules",
                    {"schedule_type": "rule_evaluation", "name": f"rules {_U}",
                     "interval_minutes": 60, "payload": {}})
    assert refused.status_code == 403
    assert refused.get_json()["error"]["code"] == "FORBIDDEN"

    admin, _ = _login_new(app, "admin")
    ok = _post(admin, "/api/schedules",
               {"schedule_type": "rule_evaluation", "name": f"rules {_U}",
                "interval_minutes": 60, "payload": {}})
    assert ok.status_code == 201, ok.get_data(as_text=True)
    sid = ok.get_json()["schedule"]["id"]
    # the owner of an evaluation schedule is an administrator; a later
    # demotion is caught at the next fire, never silently ignored
    assert _post(admin, f"/api/schedules/{sid}/pause").status_code == 200
    assert _post(admin, f"/api/schedules/{sid}/resume").status_code == 200


def test_the_page_renders_for_its_roles(app, pg_db):
    analyst, _ = _login_new(app, "analyst")
    page = analyst.get("/schedules")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'id="schedulesTable"' in body
    assert 'id="scheduleDialog"' in body, "the management dialog is on the page"
    viewer, _ = _login_new(app, "viewer")
    vpage = viewer.get("/schedules")
    assert vpage.status_code == 200
    assert 'id="scheduleDialog"' not in vpage.get_data(as_text=True), \
        "no write controls for a viewer (the API refuses their writes anyway)"
    assert _post(viewer, "/api/schedules",
                 {"schedule_type": "report_run", "name": "x",
                  "interval_minutes": 60, "payload": _REPORT_PAYLOAD}).status_code \
        in (401, 403)


def test_unknown_fields_and_types_are_refused(app, pg_db):
    analyst, _ = _login_new(app, "analyst")
    bad = _post(analyst, "/api/schedules",
                {"schedule_type": "report_run", "name": f"x {_U}",
                 "interval_minutes": 60, "payload": _REPORT_PAYLOAD,
                 "junk": 1})
    assert bad.status_code == 400
    wrong_type = _post(analyst, "/api/schedules",
                       {"schedule_type": "report_run", "name": f"y {_U}",
                        "interval_minutes": 2, "payload": _REPORT_PAYLOAD})
    assert wrong_type.status_code == 400
    assert "interval_minutes" in wrong_type.get_json()["error"]["message"]
