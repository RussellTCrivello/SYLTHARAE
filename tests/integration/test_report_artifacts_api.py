"""Step 15 integration: report artifacts over HTTP.

Real users over HTTP, PostgreSQL, the ``report_run`` and ``report_artifact``
jobs through the JobManager (synchronous). Properties: a file is created from
a completed run (a job; the same request again returns the same file without
a job); the downloaded bytes' SHA-256 equals the recorded digest, the
header, the manifest's and the DATA_EXPORTED record's measured digest; the
manifest file's SHA-256 is ``manifest_sha256``; DATA_EXPORTED carries the
report, run, criteria and query fingerprints, format, scope, count and
truncation; creation is audited; visibility follows the run; viewers cannot
create files; CSRF applies; the offline verifier accepts the pair and
rejects a modified file.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from _seed import connect, document, side, source
from test_reports_api import _login_new, _post, sync_jobs  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def corpus(pg_db, app):
    word = f"zfile{_U}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1, d1 = source(cur), side(cur)
        for i in range(4):
            document(cur, source_id=s1, side_id=d1, text=f"file {word} {i}", file_type="pdf",
                     file_date=datetime.date(2026, 5, 1 + i))
    conn.close()
    return {"word": word}


def _run(client, corpus):
    resp = _post(client, "/api/reports/runs", {"report_id": "search_results",
                                               "parameters": {"criteria": {"text": corpus["word"]}}})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    run = resp.get_json()["run"]
    assert run["status"] == "completed"
    return run


def _audit(pg_db, action, audit_id=None, resource=None):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            if audit_id is not None:
                cur.execute("SELECT username, resource, detail FROM audit_log WHERE id = %s",
                            (int(audit_id),))
            else:
                cur.execute("SELECT username, resource, detail FROM audit_log WHERE action = %s"
                            " AND resource = %s ORDER BY id", (action, resource))
            return cur.fetchall()
    finally:
        conn.close()


def test_create_download_verify_and_disclose(app, corpus, sync_jobs, pg_db, tmp_path):
    analyst, username = _login_new(app, "analyst")
    run = _run(analyst, corpus)
    rid = run["id"]
    listed = analyst.get(f"/api/reports/runs/{rid}/artifacts").get_json()
    assert listed["items"] == []
    assert {f["format"] for f in listed["formats"]} == {"csv", "html", "json", "xlsx"}
    assert {u["format"] for u in listed["unavailable"]} == {"pdf", "svg"}

    resp = _post(analyst, f"/api/reports/runs/{rid}/artifacts", {"format": "xlsx"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["existing"] is False and body["job"]["status"] == "COMPLETED"
    artifact = body["artifact"]
    again = _post(analyst, f"/api/reports/runs/{rid}/artifacts", {"format": "xlsx"}).get_json()
    assert again["existing"] is True and again["job"] is None
    assert again["artifact"]["id"] == artifact["id"]
    assert len(_audit(pg_db, "report.artifact", resource=f"report_run:{rid}")) == 1

    aid = artifact["id"]
    record = analyst.get(f"/api/reports/artifacts/{aid}").get_json()["artifact"]
    assert record["manifest"]["snapshot"]["id"] == run["snapshot"]
    assert analyst.get(f"/api/reports/artifacts/{aid}/verify").get_json()["ok"] is True

    download = analyst.get(f"/api/reports/artifacts/{aid}/download")
    assert download.status_code == 200
    content = download.get_data()
    digest = hashlib.sha256(content).hexdigest()
    assert digest == artifact["sha256"] == download.headers["X-Artifact-SHA256"]
    assert digest == record["manifest"]["artifact"]["sha256"]
    assert download.headers["Content-Disposition"] == \
        f'attachment; filename="{artifact["filename"]}"'
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    [(who, resource, detail)] = _audit(pg_db, None, audit_id=download.headers["X-Disclosure-Audit-Id"])
    assert who == username and resource == f"export:report_artifact:report_run:{rid}"
    assert detail["artifact"]["sha256"] == digest and detail["artifact"]["bytes"] == len(content)
    assert detail["format"] == "xlsx" and detail["row_count"] == 5 and detail["truncated"] is False
    assert detail["criteria_fingerprint"] == run["criteria_fingerprint"]
    assert detail["query_fingerprint"] == [d["query_fingerprint"] for d in run["datasets"]]
    assert detail["report"] == "search_results@1" and detail["report_run_id"] == rid
    assert detail["snapshot"] == run["snapshot"] and detail["artifact_id"] == aid

    manifest_resp = analyst.get(f"/api/reports/artifacts/{aid}/manifest")
    manifest_bytes = manifest_resp.get_data()
    assert hashlib.sha256(manifest_bytes).hexdigest() == artifact["manifest_sha256"] \
        == manifest_resp.headers["X-Manifest-SHA256"]
    [(_, resource, detail)] = _audit(pg_db, None,
                                     audit_id=manifest_resp.headers["X-Disclosure-Audit-Id"])
    assert resource == f"export:report_manifest:report_run:{rid}"

    # Offline, independent verification of the downloaded pair.
    (tmp_path / "a.xlsx").write_bytes(content)
    (tmp_path / "a.manifest.json").write_bytes(manifest_bytes)
    tool = [sys.executable, str(ROOT / "tools/verify/verify_artifact.py"),
            str(tmp_path / "a.xlsx"), str(tmp_path / "a.manifest.json")]
    ok = subprocess.run(tool + ["--manifest-sha256", artifact["manifest_sha256"]],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "VERIFIED" in ok.stdout
    (tmp_path / "a.xlsx").write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    bad = subprocess.run(tool, capture_output=True, text=True)
    assert bad.returncode == 1 and "MISMATCH" in bad.stdout
    (tmp_path / "a.xlsx").write_bytes(content)
    wrong = subprocess.run(tool + ["--manifest-sha256", "0" * 64], capture_output=True, text=True)
    assert wrong.returncode == 1


def test_visibility_roles_and_validation(app, corpus, sync_jobs, client_factory):
    analyst, _ = _login_new(app, "analyst")
    run = _run(analyst, corpus)
    rid = run["id"]
    aid = _post(analyst, f"/api/reports/runs/{rid}/artifacts",
                {"format": "csv", "dataset_key": "search_results.matches@1"}
                ).get_json()["artifact"]["id"]
    other, _ = _login_new(app, "analyst")
    for url in (f"/api/reports/runs/{rid}/artifacts", f"/api/reports/artifacts/{aid}",
                f"/api/reports/artifacts/{aid}/verify", f"/api/reports/artifacts/{aid}/download",
                f"/api/reports/artifacts/{aid}/manifest"):
        assert other.get(url).status_code == 404, url
    assert _post(other, f"/api/reports/runs/{rid}/artifacts",
                 {"format": "json"}).status_code == 404
    admin, _ = _login_new(app, "admin")
    assert admin.get(f"/api/reports/artifacts/{aid}/download").status_code == 200
    viewer = client_factory("viewer")
    assert _post(viewer, f"/api/reports/runs/{rid}/artifacts",
                 {"format": "json"}).status_code == 403
    for payload, code in (({"format": "pdf"}, 400), ({"format": "csv"}, 400),
                          ({"format": "json", "extra": 1}, 400),
                          ({"format": "json", "dataset_key": "search_results.count@1"}, 400)):
        resp = _post(analyst, f"/api/reports/runs/{rid}/artifacts", payload)
        assert resp.status_code == code, (payload, resp.get_data(as_text=True))
        assert resp.get_json()["error"]["code"] == "VALIDATION_FAILED"
    assert analyst.get("/api/reports/artifacts/999999999").status_code == 404
    assert app.test_client().get(f"/api/reports/artifacts/{aid}/download").status_code in (401, 302)


def test_csrf_applies_to_artifact_creation(app, corpus, sync_jobs, monkeypatch):
    analyst, _ = _login_new(app, "analyst")
    run = _run(analyst, corpus)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    resp = _post(analyst, f"/api/reports/runs/{run['id']}/artifacts", {"format": "json"},
                 csrf=False)
    assert resp.status_code == 400
