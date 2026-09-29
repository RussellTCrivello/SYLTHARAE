"""Step 11 integration: the rules API and who sees rule notifications.

Real users logged in over HTTP, PostgreSQL, the real ingestion path, the
rule_evaluation job (run synchronously). The central property: a rule's
notifications are addressed to its owner and no other user - administrators
included - can list, open, count, mark or dismiss them.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_N = iter(range(10_000))


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _post(c, url, payload=None):
    return c.post(url, json=payload if payload is not None else {},
                  headers={"X-CSRFToken": _csrf(c)})


def _put(c, url, payload):
    return c.put(url, json=payload, headers={"X-CSRFToken": _csrf(c)})


def _delete(c, url):
    return c.delete(url, headers={"X-CSRFToken": _csrf(c)})


@pytest.fixture()
def sync_jobs(monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    return sync


@pytest.fixture(scope="module")
def tenant(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                    " CURRENT_DATE) RETURNING id", (f"ra_side_{_U}",))
        side = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id", (f"ra_src_{_U}",))
        source = cur.fetchone()[0]
    conn.close()
    return {"source_id": source, "side_id": side, "pg_db": pg_db}


def _store(tenant, text):
    from database.services.contents_db_service import ContentDBService

    marker = f"{_U}ra{next(_N)}"
    return ContentDBService().process_full_document(
        hash_value=hashlib.sha256(marker.encode()).hexdigest(), source_id=tenant["source_id"],
        side_id=tenant["side_id"], file_name=f"{marker}.txt", file_path=f"/tmp/ra/{marker}.txt",
        file_size=100, file_type="txt", file_status="Read", file_date=date(2026, 1, 1),
        content_words=["ra", marker], raw_text=text, attempts=1)["hash_id"]


def _login_new(app, role):
    from core.security.service import get_auth_service

    username, password = f"ra_{role}_{uuid.uuid4().hex[:6]}", f"ra-{role}-password-123"
    get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c


def _definition(tenant, **extra):
    d = {"criteria": {"sources": [tenant["source_id"]]},
         "signals": {"signal_types": ["date_reference"]}}
    d.update(extra)
    return d


@pytest.fixture()
def owned_alert(app, tenant, sync_jobs):
    """An analyst's rule that has delivered one notification."""
    owner = _login_new(app, "analyst")
    resp = _post(owner, "/api/rules", {"name": f"watch {uuid.uuid4().hex[:6]}",
                                       "definition": _definition(tenant)})
    assert resp.status_code == 201, resp.get_json()
    rule = resp.get_json()["rule"]
    assert _post(owner, f"/api/rules/{rule['id']}/evaluate").status_code == 200   # baseline
    _store(tenant, f"The board meets on {next(_N) % 20 + 1} December 2026.")
    resp = _post(owner, f"/api/rules/{rule['id']}/evaluate")
    job = resp.get_json()["job"]
    assert resp.status_code == 200 and job["status"] == "COMPLETED", job
    listed = owner.get("/api/notifications?type=rule_match").get_json()["notifications"]
    mine = [n for n in listed if n["rule_id"] == rule["id"]]
    assert len(mine) == 1 and mine[0]["addressed"] is True
    return owner, rule, mine[0]


def test_rule_notifications_reach_only_their_owner(app, admin_client, owned_alert):
    owner, rule, alert = owned_alert
    other = _login_new(app, "analyst")
    for c in (other, admin_client):
        listed = c.get("/api/notifications?limit=1000").get_json()["notifications"]
        assert alert["id"] not in {n["id"] for n in listed}
        assert c.get(f"/api/notifications/{alert['id']}").status_code == 404
        page = c.get("/api/notifications/paginated?read_status=all&per_page=1000").get_json()
        assert alert["id"] not in {n["id"] for n in page["notifications"]}
        assert _post(c, f"/api/notifications/{alert['id']}/read").status_code == 404
        assert _post(c, f"/api/notifications/{alert['id']}/dismiss").status_code == 404
    # A user without rules counts no rule notifications.
    assert "rule_match" not in other.get("/api/notifications/stats").get_json()[
        "stats"]["by_type"]
    assert owner.get("/api/notifications/stats").get_json()["stats"]["by_type"][
        "rule_match"] == 1
    # Nothing above changed the owner's notification.
    body = owner.get(f"/api/notifications/{alert['id']}").get_json()["notification"]
    assert body["read"] is False and body["dismissed"] is False
    assert body["title"].endswith(rule["name"]) and body["priority"] in ("high", "medium", "low")
    page = owner.get("/api/notifications/paginated?read_status=all&per_page=1000").get_json()
    assert alert["id"] in {n["id"] for n in page["notifications"]}
    assert _post(owner, f"/api/notifications/{alert['id']}/read").status_code == 200
    assert owner.get(f"/api/notifications/{alert['id']}").get_json()["notification"]["read"]


def test_rule_visibility_and_edit_rights(app, admin_client, viewer_client, owned_alert):
    owner, rule, _ = owned_alert
    other = _login_new(app, "analyst")
    assert other.get(f"/api/rules/{rule['id']}").status_code == 404
    assert _put(other, f"/api/rules/{rule['id']}", {"name": "x"}).status_code == 404
    assert _post(other, f"/api/rules/{rule['id']}/pause").status_code == 404
    # Administrators see and can pause every rule, but not edit what it reads.
    assert admin_client.get(f"/api/rules/{rule['id']}").status_code == 200
    everyone = admin_client.get("/api/rules?all=1").get_json()["rules"]
    assert rule["id"] in {r["id"] for r in everyone}
    assert _put(admin_client, f"/api/rules/{rule['id']}", {"name": "x"}).status_code == 403
    assert other.get("/api/rules?all=1").status_code == 403
    # Viewers cannot create rules; their own list is empty.
    assert _post(viewer_client, "/api/rules", {"name": "v", "definition": {}}).status_code == 403
    assert viewer_client.get("/api/rules").get_json()["rules"] == []
    assert _post(other, "/api/rules/evaluate").status_code == 403
    paused = _post(admin_client, f"/api/rules/{rule['id']}/pause")
    assert paused.status_code == 200 and paused.get_json()["rule"]["status"] == "paused"
    assert _post(owner, f"/api/rules/{rule['id']}/evaluate").status_code == 409
    assert _post(owner, f"/api/rules/{rule['id']}/resume").get_json()["rule"]["status"] \
        == "active"


def test_rule_api_validation_versions_and_evaluation_log(app, tenant, sync_jobs):
    owner = _login_new(app, "analyst")
    bad = [
        {"name": "", "definition": _definition(tenant)},
        {"name": "n", "definition": {}},
        {"name": "n", "definition": _definition(tenant, bogus=1)},
        {"name": "n", "definition": _definition(tenant), "priority": "critical"},
        {"name": "n", "definition": _definition(tenant, threshold={"count": 0})},
    ]
    for payload in bad:
        resp = _post(owner, "/api/rules", payload)
        assert resp.status_code == 400, (payload, resp.get_json())
        assert resp.get_json()["error"]["code"] == "VALIDATION_FAILED"
    created = _post(owner, "/api/rules", {"name": "Dup", "definition": _definition(tenant)})
    rule = created.get_json()["rule"]
    assert created.status_code == 201 and rule["version"] == 1 and rule["baselined"] is False
    assert _post(owner, "/api/rules", {"name": "dup",
                                       "definition": _definition(tenant)}).status_code == 409
    changed = _put(owner, f"/api/rules/{rule['id']}",
                   {"definition": {"signals": {"signal_types": ["date_reference"]},
                                   "min_confidence": "high"}})
    assert changed.get_json()["rule"]["version"] == 2
    versions = owner.get(f"/api/rules/{rule['id']}/versions").get_json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["definition"]["min_confidence"] == "high"
    job = _post(owner, f"/api/rules/{rule['id']}/evaluate").get_json()["job"]
    assert job["status"] == "COMPLETED"
    log = owner.get(f"/api/rules/{rule['id']}/evaluations").get_json()
    assert log["total"] == 1 and log["evaluations"][0]["status"] == "completed"
    assert log["evaluations"][0]["rule_version"] == 2
    assert log["evaluations"][0]["trigger"] == "manual"
    assert owner.get(f"/api/rules/{rule['id']}/evaluations?limit=0").status_code == 400
    suppressed = _post(owner, f"/api/rules/{rule['id']}/suppress", {"minutes": 30})
    assert suppressed.get_json()["rule"]["suppressed"] is True
    assert _post(owner, f"/api/rules/{rule['id']}/suppress",
                 {"minutes": -1}).status_code == 400
    archived = _delete(owner, f"/api/rules/{rule['id']}")
    assert archived.get_json()["rule"]["status"] == "archived"
    assert rule["id"] not in {r["id"] for r in owner.get("/api/rules").get_json()["rules"]}
    # The name is free again once the rule is archived.
    assert _post(owner, "/api/rules", {"name": "Dup",
                                       "definition": _definition(tenant)}).status_code == 201


def test_rule_changes_are_audited(app, tenant, pg_db):
    owner = _login_new(app, "analyst")
    rule = _post(owner, "/api/rules", {"name": "audited",
                                       "definition": _definition(tenant)}).get_json()["rule"]
    _post(owner, f"/api/rules/{rule['id']}/pause")
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT action FROM audit_log WHERE resource = %s ORDER BY id",
                    (f"monitoring_rule:{rule['id']}",))
        assert [r[0] for r in cur.fetchall()] == ["rule.created", "rule.pause"]
    conn.close()


def test_content_jobs_trigger_rule_evaluation_only_when_rules_exist(app, tenant, sync_jobs,
                                                                   monkeypatch):
    from services.monitoring import rule_engine

    owner = _login_new(app, "analyst")
    assert _post(owner, "/api/rules", {"name": "trigger",
                                       "definition": _definition(tenant)}).status_code == 201
    calls = []
    real = rule_engine.run_rule_evaluation
    monkeypatch.setattr(rule_engine, "run_rule_evaluation",
                        lambda *a, **k: calls.append(k["trigger"]) or real(*a, **k))

    class Result:
        def __init__(self, cancelled=False):
            self.cancelled, self.errors, self.warnings, self.stats = cancelled, [], [], {}

    sync_jobs._enqueue_rule_evaluation({"job_type": "signal_redetection", "job_id": "J1"},
                                       Result())
    sync_jobs._enqueue_rule_evaluation({"job_type": "batch_import", "job_id": "J2"}, Result())
    sync_jobs._enqueue_rule_evaluation({"job_type": "domain_import", "job_id": "J3"}, Result())
    sync_jobs._enqueue_rule_evaluation({"job_type": "ingestion", "job_id": "J4"},
                                       Result(cancelled=True))
    assert calls == ["redetection", "ingestion"]
    # No active rule: no job at all.
    monkeypatch.setattr(rule_engine, "active_rule_ids", lambda conn: [])
    sync_jobs._enqueue_rule_evaluation({"job_type": "ingestion", "job_id": "J5"}, Result())
    assert calls == ["redetection", "ingestion"]
    # An enqueue failure is a warning on the job that ran, not silence.
    def broken(conn):
        raise RuntimeError("db down")
    monkeypatch.setattr(rule_engine, "active_rule_ids", broken)
    result = Result()
    sync_jobs._enqueue_rule_evaluation({"job_type": "ingestion", "job_id": "J6"}, result)
    assert result.warnings and "not evaluated" in result.warnings[0]
