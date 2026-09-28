"""Step 14 integration: the reports API over HTTP.

Real users logged in over HTTP, PostgreSQL, the ``report_run`` job through
the JobManager (run synchronously). Properties: definitions are filtered by
role; a run is created, executed and readable by its requester and
administrators only; invalid requests are refused before anything runs;
criteria can come from a saved search the requester can read (and from no
other); submission is audited; CSRF applies; rows are paged in SQL.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from _seed import connect, document, side, source

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

    username, password = f"ra_{role}_{uuid.uuid4().hex[:6]}", f"ra-{role}-password-123"
    user = get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c, username


@pytest.fixture()
def sync_jobs(monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    return sync


@pytest.fixture(scope="module")
def corpus(pg_db, app):
    word = f"zapi{_U}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1, d1 = source(cur), side(cur)
        paths = [document(cur, source_id=s1, side_id=d1, text=f"api {word} {i}",
                          file_type="pdf", file_date=datetime.date(2026, 4, 1 + i))[0]
                 for i in range(7)]
    conn.close()
    return {"word": word, "paths": paths}


def _criteria(corpus):
    return {"criteria": {"text": corpus["word"]}}


def test_definitions_are_role_filtered_and_translated(app, client_factory):
    viewer = client_factory("viewer")
    body = viewer.get("/api/reports/definitions").get_json()
    assert body["success"] is True
    keys = [d["key"] for d in body["items"]]
    assert "search_results@1" in keys
    sr = next(d for d in body["items"] if d["key"] == "search_results@1")
    assert sr["unit"] == "path"
    assert [p["name"] for p in sr["parameters"]] == ["criteria"]
    assert [(d["key"], d["semantics"], d["row_limit"]) for d in sr["datasets"]] == [
        ("search_results.matches@1", "capped", 5000), ("search_results.count@1", "exact", 1)]
    assert sr["help"]["title"]
    assert app.test_client().get("/api/reports/definitions").status_code in (401, 302)


def test_run_lifecycle_visibility_and_audit(app, corpus, sync_jobs, pg_db):
    analyst, username = _login_new(app, "analyst")
    resp = _post(analyst, "/api/reports/runs",
                 {"report_id": "search_results", "parameters": _criteria(corpus)})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    run, job = body["run"], body["job"]
    assert run["status"] == "completed", run
    assert run["job_id"] == job["job_id"]
    assert job["status"] == "COMPLETED"
    assert run["requester_username"] == username and run["snapshot"]
    assert [d["row_count"] for d in run["datasets"]] == [7, 1]

    rid = run["id"]
    got = analyst.get(f"/api/reports/runs/{rid}").get_json()["run"]
    assert got["id"] == rid and got["criteria_fingerprint"] == run["criteria_fingerprint"]
    page = analyst.get(f"/api/reports/runs/{rid}/datasets/search_results.matches@1"
                       "?limit=3&offset=2").get_json()
    assert page["row_count"] == 7 and page["truncated"] is False and len(page["rows"]) == 3
    full = analyst.get(f"/api/reports/runs/{rid}/datasets/search_results.matches@1"
                       "?limit=500").get_json()["rows"]
    assert page["rows"] == full[2:5]
    assert sorted(r["path_id"] for r in full) == sorted(corpus["paths"])
    assert analyst.get(f"/api/reports/runs/{rid}/datasets/search_results.matches@1"
                       "?limit=x").status_code == 400
    assert analyst.get(f"/api/reports/runs/{rid}/datasets/nope@1").status_code == 404

    listed = analyst.get("/api/reports/runs").get_json()
    assert [r["id"] for r in listed["items"]] == [rid] and listed["total"] == 1
    assert [(d["dataset_key"], d["row_count"]) for d in listed["items"][0]["datasets"]] == [
        ("search_results.matches@1", 7), ("search_results.count@1", 1)]
    assert analyst.get("/api/reports/runs?status=bogus").status_code == 400
    assert analyst.get("/api/reports/runs?all=1").status_code == 403

    other, _ = _login_new(app, "analyst")
    assert other.get(f"/api/reports/runs/{rid}").status_code == 404
    assert other.get(f"/api/reports/runs/{rid}/datasets/search_results.count@1"
                     ).status_code == 404
    assert other.get("/api/reports/runs").get_json()["items"] == []
    admin, _ = _login_new(app, "admin")
    assert admin.get(f"/api/reports/runs/{rid}").status_code == 200
    assert rid in [r["id"] for r in admin.get("/api/reports/runs?all=1&limit=200"
                                              ).get_json()["items"]]

    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, detail FROM audit_log WHERE action = 'report.run'"
                        " AND resource = %s", (f"report_run:{rid}",))
            rows = cur.fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0][0] == username
    assert "search_results@1" in str(rows[0][1])


def test_viewers_are_read_only_and_told_so(app, corpus, sync_jobs):
    """SEC-02 is not loosened: creating a run is a write."""
    viewer, _ = _login_new(app, "viewer")
    [sr] = [d for d in viewer.get("/api/reports/definitions").get_json()["items"]
            if d["key"] == "search_results@1"]
    assert sr["can_run"] is False
    resp = _post(viewer, "/api/reports/runs",
                 {"report_id": "search_results", "parameters": _criteria(corpus)})
    assert resp.status_code == 403
    assert viewer.get("/api/reports/runs").get_json()["total"] == 0
    analyst, _ = _login_new(app, "analyst")
    assert all(d["can_run"] for d in analyst.get("/api/reports/definitions").get_json()["items"])


def test_invalid_requests_are_refused_before_any_job(app, corpus, sync_jobs):
    analyst, _ = _login_new(app, "analyst")
    cases = [
        ({"report_id": "search_results", "parameters": _criteria(corpus), "x": 1}, 400),
        ({"report_id": "no_such_report"}, 404),
        ({"report_id": "search_results", "parameters": {}}, 400),
        ({"report_id": "search_results", "parameters": {"criteria": {"bogus_field": 1}}}, 400),
        ({"report_id": "search_results", "version": 2}, 404),
    ]
    for payload, status in cases:
        resp = _post(analyst, "/api/reports/runs", payload)
        assert resp.status_code == status, (payload, resp.get_data(as_text=True))
        assert resp.get_json()["success"] is False
    assert _post(analyst, "/api/reports/runs", ["not", "an", "object"]).status_code == 400
    assert analyst.get("/api/reports/runs").get_json()["total"] == 0
    assert analyst.get("/api/reports/runs").get_json()["total"] == 0


def test_csrf_applies_to_run_submission(app, corpus, sync_jobs, monkeypatch):
    analyst, _ = _login_new(app, "analyst")
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    resp = _post(analyst, "/api/reports/runs", {"report_id": "search_results",
                                                "parameters": _criteria(corpus)}, csrf=False)
    assert resp.status_code in (400, 403)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    assert analyst.get("/api/reports/runs").get_json()["total"] == 0


def test_criteria_from_a_saved_search_and_only_a_readable_one(app, corpus, sync_jobs):
    owner, _ = _login_new(app, "analyst")
    resp = _post(owner, "/api/search/saved", {"name": f"rep {_U}", "query": corpus["word"],
                                              "filters": {}})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    sid = resp.get_json()["search_id"]
    run = _post(owner, "/api/reports/runs", {"report_id": "search_results",
                                             "saved_search_id": sid}).get_json()["run"]
    assert run["status"] == "completed" and run["saved_search_id"] == sid
    assert run["datasets"][1]["row_count"] == 1
    count = owner.get(f"/api/reports/runs/{run['id']}/datasets/search_results.count@1"
                      ).get_json()["rows"][0]["matched"]
    assert count == 7
    both = _post(owner, "/api/reports/runs", {"report_id": "search_results",
                                              "saved_search_id": sid,
                                              "parameters": _criteria(corpus)})
    assert both.status_code == 400
    stranger, _ = _login_new(app, "analyst")
    assert _post(stranger, "/api/reports/runs", {"report_id": "search_results",
                                                 "saved_search_id": sid}).status_code == 404
    assert _post(owner, "/api/reports/runs", {"report_id": "search_results",
                                              "saved_search_id": "1"}).status_code == 400


def test_job_creation_failure_marks_the_run_failed(app, corpus, monkeypatch):
    from services.jobs.manager import JobManager

    class Broken:
        synchronous = True

        def create_job(self, *a, **k):
            raise RuntimeError("queue down")

    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: Broken()))
    analyst, _ = _login_new(app, "analyst")
    resp = _post(analyst, "/api/reports/runs",
                 {"report_id": "search_results", "parameters": _criteria(corpus)})
    assert resp.status_code == 500
    [run] = analyst.get("/api/reports/runs").get_json()["items"]
    assert run["status"] == "failed" and run["error"] == "the report job could not be created"


def test_reports_page_renders_for_every_role(client_factory):
    for role in ("viewer", "analyst", "admin"):
        resp = client_factory(role).get("/reports")
        assert resp.status_code == 200, role
        assert b'id="reportsPage"' in resp.data
