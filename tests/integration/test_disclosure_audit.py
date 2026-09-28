"""DATA_EXPORTED: every file that leaves the system is recorded (directive step 5).

The record must name the actor, the time, the criteria fingerprint, the
format, the scope, the row count and the SHA-256 of the bytes actually sent;
an export whose record cannot be written must be refused, not sent.
"""

import hashlib
import uuid

import pytest

from _seed import connect, document, side, source

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def token(pg_db, app):
    tag = f"zqexport{uuid.uuid4().hex[:8]}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s, d = source(cur), side(cur)
        document(cur, source_id=s, side_id=d, text=f"exportable {tag} one", file_type="txt")
        document(cur, source_id=s, side_id=d, text=f"exportable {tag} two", file_type="pdf")
    conn.close()
    return tag


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _audit_rows(pg_db, action="DATA_EXPORTED", since_id=0):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT id, username, resource, detail, created_at FROM audit_log"
                    " WHERE action = %s AND id > %s ORDER BY id", (action, since_id))
        rows = cur.fetchall()
    conn.close()
    return rows


def _max_audit_id(pg_db):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(max(id), 0) FROM audit_log")
        value = cur.fetchone()[0]
    conn.close()
    return value


def _export(client, payload):
    return client.post("/api/search/export", json=payload,
                       headers={"X-CSRFToken": _csrf(client)})


def test_search_export_writes_a_complete_disclosure_record(pg_db, admin_client, token):
    before = _max_audit_id(pg_db)
    payload = {"query": token, "export_scope": "filtered", "analyst_scope": "all",
               "format": "csv", "use_fuzzy": False, "use_expansion": False}
    resp = _export(admin_client, payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_data()
    assert "attachment" in resp.headers["Content-Disposition"]

    rows = _audit_rows(pg_db, since_id=before)
    assert len(rows) == 1, "exactly one disclosure record per export"
    audit_id, username, resource, detail, created_at = rows[0]
    assert str(audit_id) == resp.headers["X-Disclosure-Audit-Id"]
    assert username == "testadmin" and created_at is not None
    assert resource == "export:search_export:filtered"
    assert detail["kind"] == "search_export"
    assert detail["format"] == "csv" and detail["scope"] == "filtered"
    assert detail["row_count"] == 2 and detail["total"] == 2 and detail["truncated"] is False
    assert detail["actor_role"] == "admin"
    assert detail["artifact"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert detail["artifact"]["bytes"] == len(body)
    assert len(detail["criteria_fingerprint"]) == 64
    assert len(detail["query_fingerprint"]) == 64
    assert detail["filename"] and detail["filename"].endswith(".csv")


def test_export_and_saved_search_share_one_fingerprint(pg_db, admin_client, token):
    """The register can answer "which exports disclosed what this search watches"."""
    before = _max_audit_id(pg_db)
    common = {"use_fuzzy": False, "use_expansion": False, "use_bm25": True}
    assert _export(admin_client, {"query": token, "export_scope": "filtered",
                                  "analyst_scope": "all", "format": "json",
                                  **common}).status_code == 200
    exported_fp = _audit_rows(pg_db, since_id=before)[0][3]["criteria_fingerprint"]
    saved = admin_client.post(
        "/api/search/saved", headers={"X-CSRFToken": _csrf(admin_client)},
        json={"name": "fp check", "query": token,
              "filters": {"scope": "all", "sort_by": "relevance", "sort_order": "desc",
                          "options": common}})
    assert saved.status_code == 201
    assert saved.get_json()["criteria_fingerprint"] == exported_fp


def test_export_is_refused_when_the_disclosure_cannot_be_recorded(
        pg_db, admin_client, token, monkeypatch):
    from core.security.service import AuthService

    def broken(self, *args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(AuthService, "audit_strict", broken)
    before = _max_audit_id(pg_db)
    resp = _export(admin_client, {"query": token, "export_scope": "filtered",
                                  "analyst_scope": "all", "format": "csv"})
    assert resp.status_code == 503
    assert resp.get_json()["code"] == "disclosure_audit_failed"
    assert "Content-Disposition" not in resp.headers
    assert token not in resp.get_data(as_text=True)
    assert _audit_rows(pg_db, since_id=before) == []


def test_routes_without_enrichment_are_still_recorded(pg_db, admin_client):
    """Coverage is central: an export route that never calls note_disclosure
    (here the analyst-categorisation CSV) is recorded all the same."""
    before = _max_audit_id(pg_db)
    resp = admin_client.get("/api/analyst/export")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    rows = _audit_rows(pg_db, since_id=before)
    assert len(rows) == 1
    detail = rows[0][3]
    assert detail["endpoint"] and detail["kind"] == detail["endpoint"]
    assert detail["artifact"]["sha256"] == hashlib.sha256(resp.get_data()).hexdigest()


def test_ordinary_responses_are_not_disclosures(pg_db, admin_client):
    before = _max_audit_id(pg_db)
    assert admin_client.get("/api/search/saved").status_code == 200
    assert admin_client.get("/api/search/export/columns").status_code == 200
    assert _audit_rows(pg_db, since_id=before) == []


def test_refused_exports_are_not_recorded_as_disclosures(pg_db, admin_client, token):
    before = _max_audit_id(pg_db)
    resp = _export(admin_client, {"query": token, "export_scope": "bogus"})
    assert resp.status_code == 400
    assert _audit_rows(pg_db, since_id=before) == []
