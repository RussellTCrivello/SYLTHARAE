"""Upgrade-path verification for the migrations added by this work (>= 0016).

The session database proves the *fresh* path (bootstrap applies everything).
This module proves the *upgrade* path an existing installation takes: a
database at the last released version (0015) holding real rows is migrated
to head by the production runner, keeps its data, and the new migrations
reverse cleanly and re-apply. Executed against PostgreSQL - not parsed.
"""

import uuid

import psycopg2
import psycopg2.errors
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

    # Reverse newest-first down to (and including) 0017; 0016 stays.
    for migration in reversed([m for m in discover_migrations() if m.version >= "0017"]):
        migration.module.downgrade(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (migration.version,))
        conn.commit()
    tables = _tables(conn)
    assert "content_signals" not in tables and "content_signal_runs" not in tables
    assert "saved_searches" in tables
    assert current_version(conn) == "0016"
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM hashs WHERE id = %s", (seeded["hash_id"],))
        assert cur.fetchone()[0] == 1
    assert run_migrations(conn)[0] == "0017"


def test_m0018_keeps_pre_1_1_signals_and_enforces_complete_provenance(legacy_db):
    """0018 on a database holding temporal-1.0.0 signals: they survive with
    NULL provenance ("not recorded"); new rows are all-or-nothing and the
    sentence must contain the signal."""
    conn, seeded = legacy_db
    for migration in discover_migrations():
        if migration.version > "0017":
            break
        if migration.version > LAST_RELEASED:
            migration.module.upgrade(conn)
            with conn.cursor() as cur:
                cur.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                            (migration.version, migration.name))
            conn.commit()
    insert = ("INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value,"
              " surface, char_start, char_end, resolution, evidence, dedup_key{cols})"
              " VALUES (%s, 'temporal', %s, 'date_reference', 'v', 'Oct 2026', 10, 18,"
              " 'unresolved', '{{}}', %s{vals})")
    with conn.cursor() as cur:
        cur.execute(insert.format(cols="", vals=""), (seeded["hash_id"], "temporal-1.0.0", "b" * 64))
    conn.commit()

    assert run_migrations(conn)[0] == "0018"
    with conn.cursor() as cur:
        cur.execute("SELECT confidence, evidence_sentence FROM content_signals"
                    " WHERE dedup_key = %s", ("b" * 64,))
        assert cur.fetchone() == (None, None)
    cols = ", method, confidence, confidence_basis, evidence_sentence, sentence_start, sentence_end"
    vals = ", %s, %s, %s, %s, %s, %s"
    good = ("month_year", "high", "month_name", "Due Oct 2026.", 6, 19)
    bad_cases = {
        "ck_content_signals_confidence": ("month_year", "certain", "month_name", "Due Oct 2026.", 6, 19),
        "ck_content_signals_provenance_complete": ("month_year", "high", None, "Due Oct 2026.", 6, 19),
        "ck_content_signals_sentence_contains": ("month_year", "high", "month_name", "Due Oct", 12, 19),
    }
    for constraint, row in bad_cases.items():
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.CheckViolation) as exc:
                cur.execute(insert.format(cols=cols, vals=vals),
                            (seeded["hash_id"], "temporal-1.1.0", "c" * 64, *row))
            assert exc.value.diag.constraint_name == constraint
        conn.rollback()
    with conn.cursor() as cur:
        cur.execute(insert.format(cols=cols, vals=vals),
                    (seeded["hash_id"], "temporal-1.1.0", "d" * 64, *good))
    conn.commit()

    # Reverse newest-first down to (and including) 0018.
    for migration in reversed([m for m in discover_migrations() if m.version >= "0018"]):
        migration.module.downgrade(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (migration.version,))
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM content_signals WHERE hash_id = %s", (seeded["hash_id"],))
        assert cur.fetchone()[0] == 2, "downgrade drops columns, not signals"
    conn.commit()
    assert run_migrations(conn)[0] == "0018"



def test_m0019_keeps_legacy_geo_mentions_through_upgrade_and_downgrade(legacy_db):
    """0019 replaces path_geo_mentions with a view: pre-existing rows written
    by the old scan stay readable through it, and downgrade restores the
    table with its rows and the m0017 resolution CHECK."""
    conn, seeded = legacy_db
    with conn.cursor() as cur:
        cur.execute("INSERT INTO path_geo_mentions (hash_id, place_name, country, latitude,"
                    " longitude, mention_count) VALUES (%s, 'Lagos', 'Nigeria', 6.5, 3.4, 3)",
                    (seeded["hash_id"],))
    conn.commit()
    applied = run_migrations(conn)
    assert applied[-1] >= "0019"
    with conn.cursor() as cur:
        cur.execute("SELECT place_name, country, mention_count, provenance FROM path_geo_mentions"
                    " WHERE hash_id = %s", (seeded["hash_id"],))
        assert cur.fetchall() == [("Lagos", "Nigeria", 3, "legacy_m0015")]
        cur.execute("SELECT table_type FROM information_schema.tables"
                    " WHERE table_name = 'path_geo_mentions'")
        assert cur.fetchone()[0] == "VIEW"
        cur.execute("SELECT count(*), bool_and(NOT retired) FROM geo_places")
        assert cur.fetchone() == (223, True)
    for migration in reversed([m for m in discover_migrations() if m.version >= "0019"]):
        migration.module.downgrade(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (migration.version,))
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT table_type FROM information_schema.tables"
                    " WHERE table_name = 'path_geo_mentions'")
        assert cur.fetchone()[0] == "BASE TABLE"
        cur.execute("SELECT place_name, mention_count FROM path_geo_mentions WHERE hash_id = %s",
                    (seeded["hash_id"],))
        assert cur.fetchall() == [("Lagos", 3)]
        cur.execute("SELECT to_regclass('geo_places'), to_regclass('content_signal_places')")
        assert cur.fetchone() == (None, None)
        with pytest.raises(psycopg2.errors.CheckViolation) as info:
            cur.execute("INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,"
                        " value, surface, char_start, char_end, resolution, evidence, dedup_key)"
                        " VALUES (%s, 'places', 'p', 'place_mention', 'v', 'x', 0, 1,"
                        " 'identified', '{}', %s)", (seeded["hash_id"], "9" * 64))
        assert info.value.diag.constraint_name == "ck_content_signals_resolution"
    conn.rollback()
    assert run_migrations(conn)[0] == "0019"


def test_m0020_addresses_alerts_and_downgrade_never_widens_them(legacy_db):
    """0020 adds recipients to ``alerts``: existing alerts stay system-wide;
    a rule alert must have a recipient; downgrade removes addressed alerts
    rather than turning them into alerts every user would see."""
    conn, seeded = legacy_db
    with conn.cursor() as cur:
        cur.execute("INSERT INTO alerts (type, priority, title, message)"
                    " VALUES ('info', 'low', 'legacy', 'kept') RETURNING id")
        legacy_alert = cur.fetchone()[0]
    conn.commit()
    assert run_migrations(conn)[-1] >= "0020"
    uid = seeded["user_id"]
    with conn.cursor() as cur:
        cur.execute("SELECT recipient_user_id, rule_id FROM alerts WHERE id = %s", (legacy_alert,))
        assert cur.fetchone() == (None, None)
        cur.execute("INSERT INTO monitoring_rules (owner_user_id, name, definition,"
                    " definition_fingerprint) VALUES (%s, 'r', '{}', %s) RETURNING id",
                    (uid, "a" * 64))
        rule_id = cur.fetchone()[0]
        conn.commit()
        checks = [
            ("INSERT INTO alerts (type, priority, title, message, rule_id)"
             " VALUES ('rule_match', 'low', 't', 'm', %s)", (rule_id,), "ck_alerts_rule_addressed"),
            ("UPDATE monitoring_rules SET status = 'disabled' WHERE id = %s", (rule_id,),
             "ck_monitoring_rules_disabled_reason"),
            ("UPDATE monitoring_rules SET status = 'active', disabled_reason = 'x' WHERE id = %s",
             (rule_id,), "ck_monitoring_rules_disabled_reason"),
            ("UPDATE monitoring_rules SET baselined_version = 2 WHERE id = %s", (rule_id,),
             "ck_monitoring_rules_baselined"),
            ("UPDATE monitoring_rules SET definition_fingerprint = %s WHERE id = %s",
             ("Z" * 64, rule_id), "ck_monitoring_rules_fingerprint"),
            ("INSERT INTO rule_evaluations (rule_id, rule_version, trigger, status,"
             " definition_fingerprint, reference_date, evaluated_at)"
             " VALUES (%s, 1, 'manual', 'failed', %s, CURRENT_DATE, NOW())",
             (rule_id, "a" * 64), "ck_rule_evaluations_error"),
        ]
        for sql, params, constraint in checks:
            with pytest.raises(psycopg2.errors.CheckViolation) as info:
                cur.execute(sql, params)
            assert info.value.diag.constraint_name == constraint
            conn.rollback()
        # One live rule name per owner (case-insensitive); archived names are free.
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("INSERT INTO monitoring_rules (owner_user_id, name, definition,"
                        " definition_fingerprint) VALUES (%s, 'R', '{}', %s)", (uid, "a" * 64))
        conn.rollback()
        cur.execute("UPDATE monitoring_rules SET status = 'archived' WHERE id = %s", (rule_id,))
        cur.execute("INSERT INTO monitoring_rules (owner_user_id, name, definition,"
                    " definition_fingerprint) VALUES (%s, 'R', '{}', %s)", (uid, "a" * 64))
        # Ledger dedup: the second insert of a subject is a no-op.
        cur.execute("INSERT INTO rule_evaluations (rule_id, rule_version, trigger, status,"
                    " definition_fingerprint, reference_date, evaluated_at)"
                    " VALUES (%s, 1, 'manual', 'completed', %s, CURRENT_DATE, NOW()) RETURNING id",
                    (rule_id, "a" * 64))
        eid = cur.fetchone()[0]
        for _ in range(2):
            cur.execute("INSERT INTO rule_subject_ledger (rule_id, subject_key, hash_id,"
                        " rule_version, state, group_key, first_matched_at,"
                        " matched_evaluation_id, state_changed_at) VALUES (%s, 'signal:x', %s, 1,"
                        " 'pending', 'g', NOW(), %s, NOW())"
                        " ON CONFLICT (rule_id, subject_key) DO NOTHING",
                        (rule_id, seeded["hash_id"], eid))
        cur.execute("SELECT count(*) FROM rule_subject_ledger WHERE rule_id = %s", (rule_id,))
        assert cur.fetchone()[0] == 1
        with pytest.raises(psycopg2.errors.CheckViolation) as info:
            cur.execute("UPDATE rule_subject_ledger SET state = 'notified' WHERE rule_id = %s",
                        (rule_id,))
        assert info.value.diag.constraint_name == "ck_rule_ledger_notified"
        conn.rollback()
        cur.execute("INSERT INTO alerts (type, priority, title, message, recipient_user_id,"
                    " rule_id) VALUES ('rule_match', 'low', 't', 'm', %s, %s)", (uid, rule_id))
    conn.commit()
    for migration in reversed([m for m in discover_migrations() if m.version >= "0020"]):
        migration.module.downgrade(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (migration.version,))
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM alerts ORDER BY id")
        assert cur.fetchall() == [(legacy_alert,)]          # the addressed alert is gone
        cur.execute("SELECT to_regclass('monitoring_rules'), to_regclass('rule_subject_ledger')")
        assert cur.fetchone() == (None, None)
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'alerts'"
                    " AND column_name IN ('recipient_user_id', 'rule_id', 'rule_evaluation_id')")
        assert cur.fetchone()[0] == 0
    conn.rollback()
    assert run_migrations(conn)[0] == "0020"
