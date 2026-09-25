"""SEC-02 regression: the legacy /api/import-export surface is admin-gated.

Backup export contains the full evidence tables and backup restore replaces
their rows - both are administrator capabilities (docs/SECURITY.md). The
blueprint is also registered in AUTH_ADMIN_BLUEPRINTS, making every mutating
method on it admin-only.
"""
import io
import zipfile
import json

import pytest


class TestImportExportAuthz:
    def test_anon_gets_401(self, app, client):
        assert client.get("/api/import-export/backup/export").status_code == 401
        assert client.post("/api/import-export/backup/import").status_code in (401, 403)

    def test_viewer_cannot_export_backup(self, app, client_factory):
        c = client_factory("viewer")
        assert c.get("/api/import-export/backup/export").status_code == 403

    def test_viewer_cannot_restore_backup(self, app, client_factory):
        c = client_factory("viewer")
        resp = c.post("/api/import-export/backup/import", data={
            "file": (io.BytesIO(b"not-a-zip"), "backup.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_analyst_cannot_restore_backup(self, app, client_factory):
        c = client_factory("analyst")
        resp = c.post("/api/import-export/backup/import", data={
            "file": (io.BytesIO(b"not-a-zip"), "backup.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_admin_can_export_and_validate_backup(self, app, client_factory):
        c = client_factory("admin")
        resp = c.get("/api/import-export/backup/export?include_data=true")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.data
        z = zipfile.ZipFile(io.BytesIO(payload))
        assert "backup.json" in z.namelist()
        backup = json.loads(z.read("backup.json"))
        # System tables must never appear in backups (SEC-07: no credential
        # or session material; restore must not clobber system state).
        for banned in ("users", "sessions", "audit_log", "schema_migrations",
                       "jobs", "job_events"):
            assert banned not in backup["tables"], banned
        assert "words" in backup["tables"]

        # Validate-only restore of the exported backup succeeds for admin.
        resp = c.post("/api/import-export/backup/import", data={
            "file": (io.BytesIO(payload), "backup.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["success"] is True
