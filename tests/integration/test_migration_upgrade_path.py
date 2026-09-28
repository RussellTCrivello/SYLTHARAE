"""Upgrade-path verification for the migrations added by this work (>= 0016).

The session database proves the *fresh* path (bootstrap applies everything).
This module proves the *upgrade* path an existing installation takes: a
database at the last released version (0015) holding real rows is migrated
to head by the production runner, keeps its data, and the new migrations
reverse cleanly and re-apply. Executed against PostgreSQL - not parsed.
"""

import uuid

import psycopg2
import pytest

from database.migration_runner import (
    applied_versions,
    current_version,
    discover_migrations,
    run_migrations,
)

pytestmark = pytest.mark.integration

LAST_RELEASED = "0015"


@pytest.fixture()
def legacy_db(pg_db):
    """A new database migrated only to LAST_RELEASED, with data in it."""
    name = f"upgrade_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                             password=pg_db["password"], dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=name)
    try:
        # Apply exactly what the runner would have applied for a 0015 install.
        run_migrations_until(conn, LAST_RELEASED)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash, role)"
                        " VALUES ('legacy_admin', 'x', 'admin') RETURNING id")
            user_id = cur.fetchone()[0]
            cur.execute("INSERT INTO audit_log (user_id, username, action)"
                        " VALUES (%s, 'legacy_admin', 'login.success')", (user_id,))
            cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id", ("f" * 64,))
            hash_id = cur.fetchone()[0]
        conn.commit()
        yield conn, {"user_id": user_id, "hash_id": hash_id}
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def run_migrations_until(conn, last_version):
    from database.migration_runner import _ensure_tracking_table

    _ensure_tracking_table(conn)
    for migration in discover_migrations():
        if migration.version > last_version:
            break
        migration.module.upgrade(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (migration.version, migration.name))
        conn.commit()


def _tables(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public'")
        return {r[0] for r in cur.fetchall()}


def _new_migrations():
    return [m for m in discover_migrations() if m.version > LAST_RELEASED]


def test_upgrade_from_last_release_preserves_data_and_reverses(legacy_db):
    conn, seeded = legacy_db
    assert current_version(conn) == LAST_RELEASED
    before_tables = _tables(conn)
    assert "saved_searches" not in before_tables

    # dry run applies nothing
    assert run_migrations(conn, dry_run=True) == []
    assert current_version(conn) == LAST_RELEASED

    applied = run_migrations(conn)
    new_versions = [m.version for m in _new_migrations()]
    assert applied == new_versions and applied, "upgrade applied the new chain in order"
    after_tables = _tables(conn)
    assert {"saved_searches", "saved_search_imports"} <= after_tables
    assert {"content_signals", "content_signal_runs"} <= after_tables

    with conn.cursor() as cur:
        cur.execute("SELECT username FROM users WHERE id = %s", (seeded["user_id"],))
        assert cur.fetchone()[0] == "legacy_admin"
        cur.execute("SELECT count(*) FROM audit_log WHERE user_id = %s", (seeded["user_id"],))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT hash FROM hashs WHERE id = %s", (seeded["hash_id"],))
        assert cur.fetchone()[0] == "f" * 64

    # Running again is a no-op (idempotent upgrade).
    assert run_migrations(conn) == []

    # Reverse the new chain, newest first; released tables and data survive.
    for migration in reversed(_new_migrations()):
        migration.module.downgrade(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (migration.version,))
        conn.commit()
    assert current_version(conn) == LAST_RELEASED
    assert _tables(conn) == before_tables
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM users WHERE id = %s", (seeded["user_id"],))
        assert cur.fetchone()[0] == 1

    # And forward again.
    assert run_migrations(conn) == new_versions
    assert applied_versions(conn)[-1] == new_versions[-1]


def test_a_failing_migration_rolls_back_and_names_itself(legacy_db, monkeypatch):
    conn, _ = legacy_db
    first_new = _new_migrations()[0]
    original = first_new.module.upgrade

    def half_then_fail(c):
        original(c)  # creates the objects...
        raise psycopg2.DataError("simulated failure after DDL")

    monkeypatch.setattr(first_new.module, "upgrade", half_then_fail)
    with pytest.raises(RuntimeError) as info:
        run_migrations(conn)
    assert first_new.version in str(info.value)
    assert current_version(conn) == LAST_RELEASED
    assert "saved_searches" not in _tables(conn), "partial DDL was not rolled back"


def test_m0017_attaches_to_released_content_and_reverses_alone(legacy_db):
    """Signals key on pre-existing hashs rows; 0017 downgrades without 0016."""
    conn, seeded = legacy_db
    run_migrations(conn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,"
            " value, surface, char_start, char_end, resolution, date_from, date_to,"
            " evidence, dedup_key) VALUES (%s, 'temporal', 'temporal-1.0.0', 'date',"
            " 'gregorian:2026-10-05', '5 October 2026', 0, 14, 'absolute',"
            " '2026-10-05', '2026-10-05', '{}', %s)", (seeded["hash_id"], "a" * 64))
        cur.execute(
            "INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status,"
            " signal_count, trigger) VALUES (%s, 'temporal', 'temporal-1.0.0',"
            " 'complete', 1, 'redetection')", (seeded["hash_id"],))
    conn.commit()
    with conn.cursor() as cur:  # content deletion cascades to its signals
        cur.execute("SAVEPOINT s")
        cur.execute("DELETE FROM hashs WHERE id = %s", (seeded["hash_id"],))
        cur.execute("SELECT count(*) FROM content_signals")
        assert cur.fetchone()[0] == 0
        cur.execute("ROLLBACK TO SAVEPOINT s")
    conn.commit()

    m17 = next(m for m in discover_migrations() if m.version == "0017")
    m17.module.downgrade(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM schema_migrations WHERE version = '0017'")
    conn.commit()
    tables = _tables(conn)
    assert "content_signals" not in tables and "content_signal_runs" not in tables
    assert "saved_searches" in tables
    assert current_version(conn) == "0016"
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM hashs WHERE id = %s", (seeded["hash_id"],))
        assert cur.fetchone()[0] == 1
    assert run_migrations(conn) == ["0017"]

