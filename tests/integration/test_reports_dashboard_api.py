"""Step 22 integration: the Reports Dashboard over HTTP.

The page and its measured API for real roles: an analyst counts their own
runs only, an administrator may widen to everyone, a viewer gets the page
(read-only catalog) but cannot widen, and the artifacts request carries the
language the page's picker chose (the step-18 deferral landed here).
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg2
import pytest

from _seed import connect
from test_reports_api import sync_jobs as sync_jobs_fixture  # noqa: F401, F811

pytestmark = pytest.mark.integration

_FINGERPRINT = hashlib.sha256(b"dashboard-api").hexdigest()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _login_new(app, role):
    from core.security.service import get_auth_service

    username, password = (f"dash_{role}_{uuid.uuid4().hex[:6]}",
                          "dashboard-password-123")
    get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c, username


def _user_id(pg_db, username):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _seed_run(pg_db, user_id, username, role, status, *, report=("search_results", 1),
              artifact=False):
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            # the requester must exist (requested_by references users)
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            found = cur.fetchone()
            if found:
                user_id = found[0]
            else:
                cur.execute("INSERT INTO users (username, password_hash, role)"
                            " VALUES (%s, 'x', %s) RETURNING id", (username, role))
                user_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO report_runs (report_id, report_version,"
                " definition_fingerprint, parameters, parameters_fingerprint,"
                " requested_by, requester_username, requester_role,"
                " generator_version, status, error, refusal_reason, requested_at,"
                " started_at, finished_at, snapshot, snapshot_at)"
                " VALUES (%s, %s, %s, '{}'::jsonb, %s, %s, %s, %s, 'test', %s,"
                " CASE WHEN %s = 'failed' THEN 'seeded' END,"
                " CASE WHEN %s = 'refused' THEN 'seeded' END,"
                " NOW(), NOW(), NOW(),"
                " CASE WHEN %s = 'completed' THEN '1:1:' END,"
                " CASE WHEN %s = 'completed' THEN NOW() END) RETURNING id",
                (report[0], report[1], _FINGERPRINT, _FINGERPRINT, user_id,
                 username, role, status, status, status, status, status))
            run_id = cur.fetchone()[0]
            if artifact:
                content = b"dashboard-csv"
                digest = hashlib.sha256(content).hexdigest()
                cur.execute(
                    "INSERT INTO report_artifacts (run_id, format,"
                    " renderer_version, filename, media_type, byte_size, sha256,"
                    " content, manifest, manifest_sha256, creator_username,"
                    " creator_role)"
                    " VALUES (%s, 'csv', 'test', 'd.csv', 'text/csv', %s, %s, %s,"
                    " '{}'::jsonb, %s, %s, %s)",
                    (run_id, len(content), digest, psycopg2.Binary(content),
                     digest, username, role))
        conn.commit()
        return run_id
    finally:
        conn.close()



def test_the_dashboard_page_and_api_per_role(app, pg_db):
    analyst, analyst_name = _login_new(app, "analyst")
    page = analyst.get("/reports/dashboard")
    assert page.status_code == 200
    assert b"runsTable" in page.data and b"catalogTable" in page.data

    body = analyst.get("/api/reports/dashboard").get_json()
    assert body["success"] is True
    assert body["runs"]["scope"] == "mine"
    assert body["catalog"]["versions"] >= 1
    assert {"ar", "en", "he", "fa", "hr"} <= set(body["languages"])

    # the analyst's own run is counted; a stranger's is not
    _seed_run(pg_db, _user_id(pg_db, analyst_name), analyst_name, "analyst",
              "completed")
    _seed_run(pg_db, None, f"stranger-{uuid.uuid4().hex[:8]}",
              "analyst", "completed")
    body = analyst.get("/api/reports/dashboard").get_json()
    assert body["runs"]["counts"]["completed"] == 1
    assert body["runs"]["total"] == 1

    admin, _ = _login_new(app, "admin")
    widened = admin.get("/api/reports/dashboard?all=1").get_json()
    assert widened["runs"]["scope"] == "all"
    assert widened["runs"]["counts"]["completed"] >= 2
    # the admin's own narrow view does not include the analyst's run
    narrow = admin.get("/api/reports/dashboard").get_json()
    assert narrow["runs"]["scope"] == "mine"

    viewer, _ = _login_new(app, "viewer")
    assert viewer.get("/reports/dashboard").status_code == 200
    refused = viewer.get("/api/reports/dashboard?all=1")
    assert refused.status_code == 403
    assert refused.get_json()["error"]["code"] == "FORBIDDEN"
    viewer_body = viewer.get("/api/reports/dashboard").get_json()
    assert viewer_body["catalog"]["can_run"] is False


def test_the_language_picker_flows_into_the_artifact(app, pg_db,
                                                     sync_jobs_fixture):  # noqa: F811 (fixture import registers the fixture)
    analyst, analyst_name = _login_new(app, "analyst")
    run_id = _seed_run(pg_db, _user_id(pg_db, analyst_name), analyst_name,
                       "analyst", "completed")

    # the page carries the picker and the language list
    page = analyst.get("/reports")
    assert page.status_code == 200
    assert b'id="fileLanguage"' in page.data
    assert b'"languages"' in page.data

    # the picker's choice is the artifact request's language: the same run
    # and format in another language is a different file
    headers = {"X-CSRFToken": _csrf(analyst)}
    first = analyst.post(f"/api/reports/runs/{run_id}/artifacts",
                         json={"format": "json", "language": "en"},
                         headers=headers)
    assert first.status_code == 200, first.get_data(as_text=True)
    assert first.get_json()["artifact"]["language"] == "en"
    second = analyst.post(f"/api/reports/runs/{run_id}/artifacts",
                          json={"format": "json", "language": "ar"},
                          headers=headers)
    assert second.status_code == 200
    artifact = second.get_json()["artifact"]
    assert artifact["language"] == "ar"
    assert artifact["id"] != first.get_json()["artifact"]["id"]

    listing = analyst.get(f"/api/reports/runs/{run_id}/artifacts").get_json()
    languages = sorted(a["language"] for a in listing["items"])
    assert languages == ["ar", "en"]
