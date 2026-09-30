"""Per-user sidebar navigation preferences (m0033) over PostgreSQL and
over HTTP: the table's shape and behavior, the API's authorization and
validation, and the sidebar actually rendering the user's order and
hidden entries."""

from __future__ import annotations

import uuid

import psycopg2
import pytest

from _seed import connect
from test_reports_api import _csrf, _login_new, _post, sync_jobs  # noqa: F401

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


def _put(client, payload, csrf=True):
    headers = {"X-CSRFToken": _csrf(client)} if csrf else {}
    return client.put("/api/preferences/navigation", json=payload, headers=headers)


# ------------------------------------------------------------------ m0033

def test_the_table_shape_constraints_and_cascade(pg_db):
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'user_navigation_prefs'"
                " ORDER BY ordinal_position")
            shape = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            assert shape["user_id"][0] == "integer" and shape["user_id"][1] == "NO"
            assert shape["interface_id"][0] == "character varying"
            assert shape["hidden"] == ("boolean", "NO")
            assert shape["position"] == ("integer", "YES")
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid)"
                " FROM pg_constraint WHERE conrelid = 'user_navigation_prefs'::regclass"
                " ORDER BY conname")
            constraints = dict(cur.fetchall())
            assert "ck_user_navigation_prefs_position" in constraints
    # (constraint definition quoted; strip quotes before comparing)
            definition = constraints["ck_user_navigation_prefs_position"] \
                .replace('"', '')
            assert "CHECK" in definition and "position IS NULL" in definition \
                and "position >= 1" in definition
            # The primary key is (user_id, interface_id): one row per entry.
            assert "user_navigation_prefs_pkey" in constraints
            assert "user_id" in constraints["user_navigation_prefs_pkey"]

            cur.execute("INSERT INTO users (username, password_hash, role)"
                        " VALUES (%s, 'x', 'analyst') RETURNING id",
                        (f"navp_{_U}",))
            uid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO user_navigation_prefs (user_id, interface_id,"
                " hidden, position) VALUES (%s, 'reports', TRUE, 1)", (uid,))
            # A second row for the same interface is the same row (PK).
            cur.execute(
                "INSERT INTO user_navigation_prefs (user_id, interface_id,"
                " hidden, position) VALUES (%s, 'reports', FALSE, NULL)"
                " ON CONFLICT (user_id, interface_id) DO UPDATE"
                " SET hidden = EXCLUDED.hidden, position = EXCLUDED.position,"
                " updated_at = NOW()", (uid,))
            cur.execute("SELECT hidden, position FROM user_navigation_prefs"
                        " WHERE user_id = %s", (uid,))
            assert cur.fetchall() == [(False, None)]
            # position >= 1 fires by name.
            with pytest.raises(psycopg2.errors.CheckViolation, match="position"):
                cur.execute(
                    "INSERT INTO user_navigation_prefs"
                    " (user_id, interface_id, hidden, position)"
                    " VALUES (%s, 'retention', FALSE, 0)", (uid,))
            conn.rollback()
            # Deleting the user cascades.
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            cur.execute("SELECT COUNT(*) FROM user_navigation_prefs"
                        " WHERE user_id = %s", (uid,))
            assert cur.fetchone()[0] == 0
            # Downgrade drops the table cleanly (then it is re-applied by a
            # fresh bootstrap; here we only prove the statement runs).
            from database.migrations import m0033_user_navigation_prefs as m
            m.downgrade(conn)
            cur.execute("SELECT COUNT(*) FROM information_schema.tables"
                        " WHERE table_name = 'user_navigation_prefs'")
            assert cur.fetchone()[0] == 0
            m.upgrade(conn)
    finally:
        conn.close()


# -------------------------------------------------------------------- HTTP

def test_navigation_prefs_http_and_sidebar_render(app, pg_db, sync_jobs):  # noqa: F811
    client, username = _login_new(app, "analyst")

    # The catalog: every sidebar entry the registry shows this analyst.
    listing = client.get("/api/preferences/navigation")
    assert listing.status_code == 200
    entries = listing.get_json()["entries"]
    assert entries and all({"interface_id", "label", "domain", "hidden",
                            "position", "icon"} <= set(e) for e in entries)
    ids = [e["interface_id"] for e in entries]
    assert "reports" in ids

    # CSRF: the PUT without the token is refused (the suite keeps the CSRF
    # extension off by default; this test turns it on for itself).
    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    no_token = _put(client, {"entries": []}, csrf=False)
    assert no_token.status_code in (400, 403)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)

    # Hide Reports and move Scheduling ahead of its domain neighbours.
    schedules = next((e for e in entries if e["interface_id"] == "schedules"), None)
    updates = [{"interface_id": "reports", "hidden": True, "position": None}]
    if schedules:
        updates.append({"interface_id": "schedules", "hidden": False, "position": 1})
    put = _put(client, {"entries": updates})
    assert put.status_code == 200, put.get_data(as_text=True)
    assert put.get_json()["success"] is True

    # The sidebar renders the choice: no Reports entry, Scheduling first
    # in its group.
    page = client.get("/reports")   # an analyst-visible page, shell included
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-interface="reports"' not in html
    if schedules:
        assert html.index('data-interface="schedules"') < \
            html.index('data-endpoint="retention_page"')

    # The editor catalog still lists the hidden interface, so the choice
    # can be undone from Settings (hide must never be a one-way door).
    after = client.get("/api/preferences/navigation").get_json()["entries"]
    reports_entry = next((e for e in after
                          if e["interface_id"] == "reports"), None)
    assert reports_entry is not None and reports_entry["hidden"] is True

    # Unknown interface ids are refused before anything is written.
    bad = _put(client, {"entries": [{"interface_id": "no_such_interface",
                                     "hidden": True, "position": None}]})
    assert bad.status_code == 400
    assert "unknown interface" in bad.get_json()["error"]["message"]

    # Reset returns the declared sidebar.
    reset = client.delete("/api/preferences/navigation")
    assert reset.status_code == 200
    page = client.get("/reports")
    assert 'data-interface="reports"' in page.get_data(as_text=True)

    # The change was audited.
    with connect(pg_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_log"
                        " WHERE action = 'navigation.preferences'"
                        " AND username = %s", (username,))
            assert cur.fetchone()[0] >= 1


def test_the_write_gate_and_per_user_independence(app, pg_db, sync_jobs):  # noqa: F811
    viewer, viewer_name = _login_new(app, "viewer")
    analyst, analyst_name = _login_new(app, "analyst")

    # SEC-02: every mutating method is analyst/admin. A viewer reads their
    # catalog but cannot write preferences - the global policy tightens
    # this endpoint, and no endpoint may loosen it.
    put = _put(viewer, {"entries": [{"interface_id": "settings_page",
                                     "hidden": True, "position": None}]})
    assert put.status_code == 403
    assert viewer.get("/api/preferences/navigation").status_code == 200

    # Preferences are rows of ONE user: the analyst's choice does not
    # exist for anyone else.
    entries = analyst.get("/api/preferences/navigation").get_json()["entries"]
    first = entries[0]["interface_id"]
    assert _put(analyst, {"entries": [{"interface_id": first,
                                       "hidden": False, "position": 1}]
                          }).status_code == 200
    with connect(pg_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT u.username, COUNT(*) FROM user_navigation_prefs p"
                        " JOIN users u ON u.id = p.user_id"
                        " WHERE u.username IN (%s, %s) GROUP BY u.username",
                        (viewer_name, analyst_name))
            rows = dict(cur.fetchall())
    assert rows == {analyst_name: 1}
