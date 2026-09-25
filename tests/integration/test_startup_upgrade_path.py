"""Regression tests for the startup schema-upgrade path.

Deployment defect found during PR #11 acceptance: migrations ran only via
the first-install setup wizard, so an EXISTING installation that pulled
new code containing a new migration started against a stale schema
(``relation "analyst_categories" does not exist``). ``initialize_system``
now applies pending migrations on every (non-first) startup.
"""

import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def _connect(pg_db):
    return psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )


def test_startup_applies_pending_migrations_to_existing_install(pg_db, app):
    """Simulate the field condition exactly: a database at version 0008
    (pre-PR#11) that just received the new code. The startup upgrade hook
    must bring the schema current, and a second startup must be a no-op."""
    from core.initialization import upgrade_database_schema

    # Rewind the disposable database to the pre-0009 state.
    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DROP TABLE IF EXISTS analyst_categorization_log,"
                " analyst_file_categories CASCADE"
            )
            cur.execute("DROP TABLE IF EXISTS analyst_categories CASCADE")
            cur.execute("DELETE FROM schema_migrations WHERE version = '0009'")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('analyst_categories')")
            assert cur.fetchone()[0] is None, "rewind failed"
    finally:
        conn.close()

    applied = upgrade_database_schema()
    assert "0009" in applied, f"upgrade did not apply m0009: {applied}"

    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            for table in ("analyst_categories", "analyst_file_categories",
                          "analyst_categorization_log"):
                cur.execute("SELECT to_regclass(%s)", (table,))
                assert cur.fetchone()[0] is not None, f"{table} missing after upgrade"
            # The migration is recorded so it will never re-run.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = '0009'")
            assert cur.fetchone() is not None
    finally:
        conn.close()

    # Idempotent: restarting an up-to-date system applies nothing.
    assert upgrade_database_schema() == []


def test_startup_upgrade_tolerates_unreachable_database(monkeypatch):
    """A database that cannot be reached must not crash startup: the hook
    logs a warning and returns an empty list (the setup gate / health
    checks surface the real problem)."""
    import settings.config as settings_config
    from core.initialization import upgrade_database_schema

    class _Unreachable:
        host = "127.0.0.1"
        port = 1  # nothing listens here; refused immediately
        user = "nobody"
        password = "nobody"
        database = "does_not_exist"

    monkeypatch.setattr(
        settings_config, "get_database_config", lambda: _Unreachable()
    )
    assert upgrade_database_schema() == []
