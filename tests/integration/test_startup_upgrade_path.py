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


# --- start-up without the initialisation marker (field report, step 12) -----
#
# The marker is written only when config.json exists. An installation
# configured through .env never gets one, took the "first startup" branch on
# every start, and never had its schema upgraded: after pulling m0020 the
# server logged `column "recipient_user_id" does not exist`.

def _no_marker_no_config(monkeypatch, tmp_path):
    import core.initialization as init

    monkeypatch.chdir(tmp_path)
    # the `app` fixture points the marker at a session directory where it
    # exists; this scenario needs an installation without one
    monkeypatch.setattr(init, "INIT_MARKER_FILE", tmp_path / ".system_initialized")
    monkeypatch.setattr(init, "load_config_from_json", lambda *a, **k: {})
    assert not init.is_system_initialized()
    return init


def test_startup_without_marker_or_config_upgrades_an_installed_database(
        pg_db, app, monkeypatch, tmp_path):
    from database.migrations import m0021_scenarios

    conn = _connect(pg_db)
    try:
        m0021_scenarios.downgrade(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = '0021'")
        conn.commit()
    finally:
        conn.close()

    init = _no_marker_no_config(monkeypatch, tmp_path)
    init.ensure_system_initialized()

    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('scenario_outcomes')")
            assert cur.fetchone()[0] is not None, "startup did not apply m0021"
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = '0021'")
            assert cur.fetchone() is not None
    finally:
        conn.close()
    assert not (tmp_path / ".system_initialized").exists(), \
        "no config.json: the marker is still not written (unchanged behaviour)"


def test_startup_without_marker_never_creates_an_uninstalled_database(
        pg_db, app, monkeypatch, tmp_path):
    import settings.config as settings_config

    name = "syltharae_setup_pending_db"

    class _Pending:
        host, port, user = pg_db["host"], pg_db["port"], pg_db["user"]
        password, database = pg_db["password"], name

    monkeypatch.setattr(settings_config, "get_database_config", lambda: _Pending())
    init = _no_marker_no_config(monkeypatch, tmp_path)
    assert init.installed_schema_present(init._schema_db_config()) is False
    init.ensure_system_initialized()

    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname="postgres")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            assert cur.fetchone() is None, "startup created a database before setup"
    finally:
        conn.close()


def test_a_failing_migration_is_reported_as_a_failure_not_as_unreachable(
        monkeypatch, capsys):
    import database.bootstrap as bootstrap
    from core.initialization import upgrade_database_schema

    def _fail(cfg):
        try:
            raise FileNotFoundError("places_seed.json")
        except FileNotFoundError as exc:
            raise RuntimeError("Migration 0019 (gazetteer) failed: SeedIntegrityError") from exc

    monkeypatch.setattr(bootstrap, "bootstrap_database", _fail)
    assert upgrade_database_schema() == []
    out = capsys.readouterr().out
    assert "[ERROR] Schema upgrade FAILED" in out and "0019" in out
    assert "places_seed.json" in out and "unreachable" not in out


def test_the_app_fixture_does_not_mark_the_checkout(app):
    """The suite must not leave `.system_initialized` in the working tree.

    A marker left behind by the tests made a later `run_web.py` from the same
    checkout take the marker branch, masking the missing-upgrade defect.
    """
    import os
    from pathlib import Path

    import core.initialization as init

    assert init.INIT_MARKER_FILE.is_absolute()
    assert init.INIT_MARKER_FILE.exists()
    assert init.INIT_MARKER_FILE.resolve().parent != Path(os.getcwd()).resolve()
    assert init.INIT_MARKER_FILE.resolve().parent != Path(__file__).resolve().parents[2]
