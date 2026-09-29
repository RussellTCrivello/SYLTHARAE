"""Step 11 integration: monitoring rules evaluated on PostgreSQL.

Documents go through the real ingestion path (signals are detected once, at
ingestion); rules are stored and evaluated by services/monitoring. Every
evaluation gets an explicit clock, so thresholds, cooldowns and digests are
tested without sleeping. The database is shared with other modules: every
rule here is restricted to this module's own source by its criteria.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

import psycopg2
import psycopg2.extras
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_N = iter(range(100_000))

IMMINENT = "The review will be held on 5 October 2026."          # high confidence, in the week
LATER = "The summit will convene on 20 November 2026."            # high confidence, later


@pytest.fixture(scope="module")
def world(pg_db):
    conn = connect(pg_db)
    ids = {}
    with conn, conn.cursor() as cur:
        for key in ("a", "b"):
            cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                        " CURRENT_DATE) RETURNING id", (f"re_side_{key}_{_U}",))
            ids[f"side_{key}"] = cur.fetchone()[0]
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                        (f"re_src_{key}_{_U}",))
            ids[f"source_{key}"] = cur.fetchone()[0]
    conn.close()
    ids["pg_db"] = pg_db
    return ids


def store(world, text, key="a"):
    from database.services.contents_db_service import ContentDBService

    marker = f"{_U}re{next(_N)}"
    return ContentDBService().process_full_document(
        # A digest of the marker: zero-padding it would make marker "..1" and
        # marker "..10" the same hash (the second store would be a duplicate).
        hash_value=hashlib.sha256(marker.encode()).hexdigest(), source_id=world[f"source_{key}"],
        side_id=world[f"side_{key}"], file_name=f"{marker}.txt",
        file_path=f"/tmp/re/{marker}.txt", file_size=100, file_type="txt",
        file_status="Read", file_date=dt.date(2026, 1, 1), content_words=["re", marker],
        raw_text=text, attempts=1)["hash_id"]


@pytest.fixture()
def db(world):
    conn = connect(world["pg_db"])
    yield conn
    conn.rollback()
    conn.close()


def user(db, role="analyst", active=True):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role, is_active)"
                    " VALUES (%s, 'x', %s, %s) RETURNING id",
                    (f"re_{role}_{uuid.uuid4().hex[:8]}", role, active))
        uid = cur.fetchone()[0]
    db.commit()
    return uid


def make_rule(db, world, owner, definition=None, key="a", name=None, **extra):
    from services.monitoring import rules

    d = {"criteria": {"sources": [world[f"source_{key}"]]},
         "signals": {"signal_types": ["date_reference"]}}
    d.update(definition or {})
    return rules.create_rule(db, owner_id=owner, is_admin=False,
                             name=name or f"rule {uuid.uuid4().hex[:6]}", definition=d, **extra)


def evaluate(db, rule, now=NOW, **kw):
    from services.monitoring.rule_engine import evaluate_rule

    return evaluate_rule(db, rule["id"], now=now, **kw)


def ledger(db, rule):
    with db.cursor() as cur:
        cur.execute("SELECT state, count(*) FROM rule_subject_ledger WHERE rule_id = %s"
                    " GROUP BY state", (rule["id"],))
        out = dict(cur.fetchall())
    db.rollback()
    return out


def alerts(db, rule, type_="rule_match"):
    with db.cursor() as cur:
        cur.execute("SELECT id, priority, recipient_user_id, metadata FROM alerts"
                    " WHERE rule_id = %s AND type = %s ORDER BY id", (rule["id"], type_))
        out = cur.fetchall()
    db.rollback()
    return out


def rule_row(db, rule):
    with db.cursor() as cur:
        cur.execute("SELECT status, disabled_reason, version, baselined_version"
                    " FROM monitoring_rules WHERE id = %s", (rule["id"],))
        out = cur.fetchone()
    db.rollback()
    return out


# --- baseline, delivery, dedup ------------------------------------------------

def test_first_evaluation_is_the_baseline_and_later_matches_are_notified_once(db, world):
    owner = user(db)
    store(world, IMMINENT, key="a")
    rule = make_rule(db, world, owner)
    first = evaluate(db, rule)
    assert first["status"] == "completed"
    assert first["counts"]["new_baseline"] >= 1 and first["counts"]["notifications"] == 0
    assert alerts(db, rule) == []

    store(world, LATER, key="a")
    second = evaluate(db, rule)
    assert second["counts"]["new_pending"] == 1 and second["counts"]["notifications"] == 1
    [(alert_id, priority, recipient, meta)] = alerts(db, rule)
    assert recipient == owner
    assert meta["subject_count"] == 1 and meta["subjects"][0]["surface"] == "20 November 2026"
    assert meta["subjects"][0]["evidence_sentence"] == LATER
    assert meta["rule_version"] == 1 and meta["evaluation_id"] == second["evaluation_id"]
    assert len(meta["criteria_fingerprint"]) == 64

    third = evaluate(db, rule)                # nothing new: the ledger deduplicates
    assert third["counts"]["new_subjects"] == 0 and third["counts"]["notifications"] == 0
    assert len(alerts(db, rule)) == 1
    states = ledger(db, rule)
    assert states.get("notified") == 1 and states.get("baseline", 0) >= 1


def test_notify_existing_notifies_the_baseline(db, world):
    owner = user(db)
    store(world, IMMINENT, key="b")
    rule = make_rule(db, world, owner, {"notify_existing": True}, key="b")
    out = evaluate(db, rule)
    assert out["counts"]["notifications"] >= 1 and "new_baseline" not in out["counts"]


def test_redetection_under_a_new_detector_version_does_not_renotify(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    h = store(world, "The hearing is on 9 October 2026.")
    assert evaluate(db, rule)["counts"]["notifications"] == 1
    # Same findings, new detector version: new dedup_key and new ids.
    with db.cursor() as cur:
        cur.execute("UPDATE content_signals SET detector_ver = detector_ver || '-next',"
                    " dedup_key = encode(sha256(convert_to(dedup_key || 'next', 'UTF8')), 'hex')"
                    " WHERE hash_id = %s", (h,))
        assert cur.rowcount >= 1
    db.commit()
    again = evaluate(db, rule)
    assert again["counts"]["new_subjects"] == 0 and again["counts"]["notifications"] == 0


# --- authorisation at evaluation time -----------------------------------------

def test_demoted_owner_disables_the_rule_and_nothing_is_matched(db, world):
    from services.monitoring import rules

    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    store(world, "The vote is on 6 October 2026.")
    with db.cursor() as cur:
        cur.execute("UPDATE users SET role = 'viewer' WHERE id = %s", (owner,))
    db.commit()
    out = evaluate(db, rule)
    assert out["status"] == "owner_revoked"
    assert rule_row(db, rule)[:2] == ("disabled", "owner_role")
    assert alerts(db, rule) == [] and ledger(db, rule).get("pending") is None
    [(_, _, recipient, meta)] = alerts(db, rule, "rule_status")
    assert recipient == owner and meta["disabled_reason"] == "owner_role"
    # A disabled rule is not evaluated, and cannot be resumed while demoted.
    assert evaluate(db, rule)["status"] == "not_active"
    with pytest.raises(rules.RuleError) as exc:
        rules.set_status(db, rule["id"], "resume", user_id=owner, is_admin=False)
    assert exc.value.status == 409
    with db.cursor() as cur:
        cur.execute("UPDATE users SET role = 'analyst' WHERE id = %s", (owner,))
    db.commit()
    assert rules.set_status(db, rule["id"], "resume", user_id=owner,
                            is_admin=False)["status"] == "active"


def test_deactivated_owner_disables_the_rule(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner)
    with db.cursor() as cur:
        cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (owner,))
    db.commit()
    assert evaluate(db, rule)["status"] == "owner_revoked"
    assert rule_row(db, rule)[:2] == ("disabled", "owner_inactive")


def test_viewer_cannot_own_a_rule(db, world):
    from services.monitoring import rules

    with pytest.raises(rules.RuleError) as exc:
        make_rule(db, world, user(db, "viewer"))
    assert exc.value.status == 403


def test_evaluation_records_the_owner_scope_and_criteria(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner)
    out = evaluate(db, rule)
    with db.cursor() as cur:
        cur.execute("SELECT owner_role, access_scope, criteria_fingerprint, reference_date,"
                    " rule_version FROM rule_evaluations WHERE id = %s", (out["evaluation_id"],))
        role, scope, fp, ref, version = cur.fetchone()
    db.rollback()
    assert role == "analyst" and scope["user_id"] == owner and scope["role"] == "analyst"
    assert scope["allowed_source_ids"] is None and len(fp) == 64
    assert ref == dt.date(2026, 10, 1) and version == 1


def test_a_rule_only_matches_content_its_criteria_select(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner, key="a", definition={"notify_existing": True})
    evaluate(db, rule)
    store(world, "The ceremony is on 7 October 2026.", key="b")
    assert evaluate(db, rule)["counts"]["new_subjects"] == 0


# --- suppression, threshold, cooldown, digest ---------------------------------

def test_suppressed_matches_are_recorded_and_never_notified(db, world):
    from services.monitoring import rules

    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    rules.suppress(db, rule["id"], minutes=60, user_id=owner, is_admin=False, now=NOW)
    store(world, "The launch is on 8 October 2026.")
    out = evaluate(db, rule, now=NOW + dt.timedelta(minutes=5))
    assert out["counts"]["new_suppressed"] == 1 and out["counts"]["notifications"] == 0
    after = evaluate(db, rule, now=NOW + dt.timedelta(minutes=90))    # suppression over
    assert after["counts"]["notifications"] == 0 and ledger(db, rule)["suppressed"] == 1


def test_threshold_waits_for_enough_matches_inside_the_window(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner, {"threshold": {"count": 3, "window_hours": 24}})
    evaluate(db, rule)
    store(world, "Talks on 10 October 2026. Talks on 11 October 2026.")
    out = evaluate(db, rule)
    assert out["counts"]["held_by"] == "threshold" and out["counts"]["pending_before_delivery"] == 2
    # 30 hours later the first two fall out of the window, unnotified.
    store(world, "Talks on 12 October 2026.")
    later = evaluate(db, rule, now=NOW + dt.timedelta(hours=30))
    assert later["counts"]["expired_threshold_window"] == 2
    assert later["counts"]["held_by"] == "threshold"
    store(world, "Talks on 13 October 2026. Talks on 14 October 2026.")
    final = evaluate(db, rule, now=NOW + dt.timedelta(hours=31))
    assert final["counts"]["notified"] == 3
    assert ledger(db, rule)["expired"] == 2


def test_cooldown_defers_notifications_without_losing_them(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner, {"cooldown_minutes": 60})
    evaluate(db, rule)
    store(world, "Session on 15 October 2026.")
    first = evaluate(db, rule)
    assert first["counts"].get("notifications") == 1, first
    store(world, "Session on 16 October 2026.")
    held = evaluate(db, rule, now=NOW + dt.timedelta(minutes=10))
    assert held["counts"]["held_by"] == "cooldown"
    released = evaluate(db, rule, now=NOW + dt.timedelta(minutes=61))
    assert released["counts"]["notified"] == 1 and len(alerts(db, rule)) == 2


def test_digest_sends_one_summary_per_interval(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner, {"delivery": {"mode": "digest", "interval_hours": 6}})
    evaluate(db, rule)
    store(world, "Forum on 17 October 2026.")
    store(world, "Forum on 18 October 2026.")
    out = evaluate(db, rule)
    assert out["counts"]["notifications"] == 1 and out["counts"]["notified"] == 2
    [(_, _, _, meta)] = alerts(db, rule)
    assert meta["delivery"] == "digest" and meta["subject_count"] == 2
    store(world, "Forum on 19 October 2026.")
    assert evaluate(db, rule, now=NOW + dt.timedelta(hours=1))["counts"]["held_by"] \
        == "digest_interval"
    assert evaluate(db, rule, now=NOW + dt.timedelta(hours=7))["counts"]["notified"] == 1


# --- grouping, overflow, priority ---------------------------------------------

def test_group_by_content_sends_one_notification_per_document(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner, {"group_by": "content"})
    evaluate(db, rule)
    store(world, "Visit on 21 October 2026. Visit on 22 October 2026.")
    out = evaluate(db, rule)
    assert out["counts"]["notified"] == 2 and out["counts"]["notifications"] == 1
    [(_, _, _, meta)] = alerts(db, rule)
    assert meta["subject_count"] == 2 and meta["group_key"].startswith("content:")


def test_overflow_is_an_explicit_summary_not_a_silent_drop(db, world, monkeypatch):
    from services.monitoring import rule_engine

    monkeypatch.setattr(rule_engine, "MAX_ALERTS_PER_EVALUATION", 2)
    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    store(world, " ".join(f"Item on {d} October 2026." for d in (23, 24, 25, 26, 27)))
    out = evaluate(db, rule)
    assert out["counts"]["notifications"] == 2 and out["counts"]["notified"] == 5
    assert out["counts"]["overflow_subjects"] == 4
    metas = [a[3] for a in alerts(db, rule)]
    assert [m["delivery"] for m in metas] == ["immediate", "overflow"]
    assert metas[1]["subject_count"] == 4 and metas[1]["group_count"] == 4
    assert ledger(db, rule).get("pending") is None


def test_priority_is_derived_never_critical(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    store(world, "The review will be held on 4 October 2026.")     # high + in the week
    store(world, "The summit will convene on 30 November 2026.")   # high, not imminent
    evaluate(db, rule)
    by_surface = {a[3]["subjects"][0]["surface"]: (a[1], a[3]["priority_basis"])
                  for a in alerts(db, rule)}
    assert by_surface["4 October 2026"][0] == "high"
    assert by_surface["4 October 2026"][1]["high_and_imminent"] is True
    assert by_surface["30 November 2026"][0] == "medium"
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM alerts WHERE rule_id IS NOT NULL"
                    " AND priority = 'critical'")
        assert cur.fetchone()[0] == 0
    db.rollback()


def test_imminence_sql_and_python_agree(db):
    from core.monitoring.priority import imminent_sql, is_imminent

    ref = dt.date(2026, 10, 1)
    cases = [(None, None), (ref, None), (ref - dt.timedelta(3), ref), (ref - dt.timedelta(3),
             ref - dt.timedelta(1)), (ref + dt.timedelta(6), None), (ref + dt.timedelta(7), None),
             (ref - dt.timedelta(1), ref + dt.timedelta(30))]
    with db.cursor() as cur:
        for f, t in cases:
            cur.execute(f"SELECT {imminent_sql('f', 't')} FROM (SELECT %s::date AS f,"
                        " %s::date AS t) x", (ref, ref + dt.timedelta(7), f, t))
            assert bool(cur.fetchone()[0]) == is_imminent(f, t, ref), (f, t)
    db.rollback()


# --- transactions, concurrency, versions --------------------------------------

def test_a_failed_delivery_rolls_back_the_whole_evaluation(db, world, monkeypatch):
    from services.monitoring import rule_engine

    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    store(world, "Deadline on 28 October 2026.")

    def boom(cur, notifications):
        raise RuntimeError("alert store unavailable")

    monkeypatch.setattr(rule_engine, "insert_alerts", boom)
    out = evaluate(db, rule)
    assert out["status"] == "failed" and "alert store unavailable" in out["error"]
    assert ledger(db, rule).get("pending") is None        # the match was rolled back too
    with db.cursor() as cur:
        cur.execute("SELECT status, error FROM rule_evaluations WHERE id = %s",
                    (out["evaluation_id"],))
        assert cur.fetchone()[0] == "failed"
    db.rollback()
    monkeypatch.undo()
    assert evaluate(db, rule)["counts"]["notified"] == 1   # retried from scratch


def test_a_concurrent_evaluation_is_skipped_not_interleaved(db, world):
    from services.monitoring.rule_engine import RULE_LOCK_CLASS

    owner = user(db)
    rule = make_rule(db, world, owner)
    other = connect(world["pg_db"])
    try:
        with other.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (RULE_LOCK_CLASS, rule["id"]))
        assert evaluate(db, rule)["status"] == "skipped_busy"
    finally:
        other.rollback()
        other.close()
    assert evaluate(db, rule)["status"] == "completed"


def test_a_definition_change_is_a_new_version_and_rebaselines(db, world):
    from services.monitoring import rules

    owner = user(db)
    rule = make_rule(db, world, owner)
    evaluate(db, rule)
    renamed = rules.update_rule(db, rule["id"], user_id=owner, is_admin=False,
                                name="renamed " + _U)
    assert renamed["version"] == 1
    changed = rules.update_rule(db, rule["id"], user_id=owner, is_admin=False,
                                definition={"signals": {"signal_types": ["date_reference"],
                                                        "confidence": ["high"]}})
    assert changed["version"] == 2
    assert changed["definition"]["criteria"]["sources"] == [world["source_a"]]   # kept
    assert [v["version"] for v in rules.list_versions(db, rule["id"])] == [1, 2]
    assert rule_row(db, rule)[3] == 1                       # not yet baselined at v2
    store(world, "Recess on 29 October 2026.")
    out = evaluate(db, rule)
    assert out["counts"]["notifications"] == 0              # re-baselined, not notified
    assert rule_row(db, rule)[3] == 2
    other = user(db)
    with pytest.raises(rules.RuleError) as exc:
        rules.update_rule(db, rule["id"], user_id=other, is_admin=True, name="hijack")
    assert exc.value.status == 403


def test_content_unit_keeps_the_strongest_evidence(db, world):
    owner = user(db)
    rule = make_rule(db, world, owner, {"unit": "content", "notify_existing": False})
    evaluate(db, rule)
    h = store(world, "Maybe on 03/04/2026. The review will be held on 31 October 2026.")
    evaluate(db, rule)
    [(_, _, _, meta)] = alerts(db, rule)
    subject = meta["subjects"][0]
    assert subject["subject_key"] == f"content:{h}" and subject["confidence"] == "high"
    assert meta["unit"] == "content"


def test_saved_search_monitor_flag_follows_its_rules(db, world):
    from services.monitoring import rules

    owner = user(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO saved_searches (owner_user_id, name, query, criteria,"
                    " criteria_fingerprint, criteria_schema_version) VALUES"
                    " (%s, %s, '', %s, %s, 1) RETURNING id",
                    (owner, f"ss {_U}", psycopg2.extras.Json({"sources": [world["source_a"]]}),
                     "0" * 64))
        sid = cur.fetchone()[0]
    db.commit()

    def flag():
        with db.cursor() as cur:
            cur.execute("SELECT monitor_enabled FROM saved_searches WHERE id = %s", (sid,))
            v = cur.fetchone()[0]
        db.rollback()
        return v

    rule = rules.create_rule(db, owner_id=owner, is_admin=False, name="from search",
                             definition={"signals": {"signal_types": ["date_reference"]}},
                             saved_search_id=sid)
    assert rule["definition"]["criteria"]["sources"] == [world["source_a"]]   # snapshot
    assert flag() is True
    rules.set_status(db, rule["id"], "archive", user_id=owner, is_admin=False)
    assert flag() is False
    # Another user's private search cannot be used.
    with pytest.raises(rules.RuleError) as exc:
        rules.create_rule(db, owner_id=user(db), is_admin=False, name="x",
                          definition={}, saved_search_id=sid)
    assert exc.value.status == 404

