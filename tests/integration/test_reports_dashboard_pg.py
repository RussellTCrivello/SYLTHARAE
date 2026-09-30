"""Step 22 against PostgreSQL: the dashboard's measured aggregates.

`run_status_counts` and `artifact_summary` must count exactly the runs
``list_runs`` would show: requester-scoped for a non-admin (and only runs
whose report definition their role can still read), everything for an
administrator who asks for all. An absent status is a measured zero.
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg2

import pytest

from services.reporting import runs as store

from _seed import connect

pytestmark = pytest.mark.integration

_FINGERPRINT = hashlib.sha256(b"dashboard-test").hexdigest()
_VISIBLE = ("search_results", 1)  # declared and readable by every role
_RETIRED = ("ghost_report", 1)    # not in the registry: administrators only


class _User:
    def __init__(self, id, username, role):
        self.id = id
        self.username = username
        self.role = role

    def has_role(self, *roles):
        return self.role in roles


@pytest.fixture(scope="module")
def world(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES"
                    " (%s, 'x', 'analyst') RETURNING id",
                    (f"dash_analyst_{uuid.uuid4().hex[:8]}",))
        analyst_id = cur.fetchone()[0]
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES"
                    " (%s, 'x', 'admin') RETURNING id, username",
                    (f"dash_admin_{uuid.uuid4().hex[:8]}",))
        admin_id, admin_name = cur.fetchone()
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES"
                    " (%s, 'x', 'analyst') RETURNING id, username",
                    (f"dash_other_{uuid.uuid4().hex[:8]}",))
        other_id, other_name = cur.fetchone()
    conn.commit()

    def user(row, role):
        return _User(row[0] if isinstance(row, tuple) else row,
                     None, role)

    analyst = _User(analyst_id, "analyst", "analyst")
    admin = _User(admin_id, admin_name, "admin")
    other = _User(other_id, other_name, "analyst")

    def role_of(who):
        return who.role

    def run(cur, who, status, *, report=_VISIBLE, artifact=False):
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
            " CASE WHEN %s = 'completed' THEN NOW() END)"
            " RETURNING id",
            (report[0], report[1], _FINGERPRINT, _FINGERPRINT, who.id,
             f"u{who.id}", role_of(who), status, status, status, status, status))
        run_id = cur.fetchone()[0]
        if artifact:
            content = b"csv-bytes"
            digest = hashlib.sha256(content).hexdigest()
            cur.execute(
                "INSERT INTO report_artifacts (run_id, format, renderer_version,"
                " filename, media_type, byte_size, sha256, content, manifest,"
                " manifest_sha256, creator_username, creator_role)"
                " VALUES (%s, 'csv', 'test', 'a.csv', 'text/csv', %s, %s, %s,"
                " '{}'::jsonb, %s, 'dash', %s)",
                (run_id, len(content), digest, psycopg2.Binary(content),
                 digest, role_of(who)))
        return run_id

    with conn, conn.cursor() as cur:
        run(cur, analyst, "completed", artifact=True)
        run(cur, analyst, "failed")
        run(cur, analyst, "refused")
        run(cur, other, "completed")
        run(cur, admin, "completed")
        run(cur, analyst, "completed", report=_RETIRED)
    conn.commit()
    yield {"conn": conn, "pg_db": pg_db, "analyst": analyst,
           "admin": admin, "other": other}
    conn.close()


def test_counts_cover_exactly_the_visible_runs(world):
    result = store.run_status_counts(world["conn"], user=world["analyst"])
    assert result["scope"] == "mine"
    assert result["counts"] == {"queued": 0, "running": 0, "completed": 1,
                                "failed": 1, "refused": 1, "cancelled": 0}
    assert result["total"] == 3


def test_a_retired_definition_is_admin_only_in_the_counts(world):
    # the analyst's fourth run (ghost_report) is history of a retired
    # definition: list_runs would not show it, so the counts must not either
    result = store.run_status_counts(world["conn"], user=world["analyst"])
    assert result["total"] == 3
    admin_all = store.run_status_counts(world["conn"], user=world["admin"],
                                        all_users=True)
    assert admin_all["scope"] == "all"
    assert admin_all["counts"]["completed"] >= 4  # 3 visible + the retired one
    assert admin_all["total"] >= 5


def test_a_non_admin_cannot_count_everyones_runs(world):
    from services.reporting.runs import ReportRunError

    with pytest.raises(ReportRunError) as excinfo:
        store.run_status_counts(world["conn"], user=world["analyst"],
                                all_users=True)
    assert excinfo.value.status == 403


def test_artifacts_are_counted_over_the_visible_runs(world):
    mine = store.artifact_summary(world["conn"], user=world["analyst"])
    assert mine["scope"] == "mine"
    assert mine["total"] == 1
    assert mine["by_format"] == {"csv": 1}
    everyone = store.artifact_summary(world["conn"], user=world["admin"],
                                      all_users=True)
    assert everyone["total"] >= 1
