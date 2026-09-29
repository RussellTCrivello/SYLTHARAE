"""Step 14 against PostgreSQL: report runs (services/reporting/runs.py).

Executed on the migrated schema with real rows. Properties:

* a run reads every dataset from ONE snapshot - a row committed by another
  connection between two dataset reads is in neither;
* the snapshot, isolation level, fingerprints and generator are recorded;
* identical requests give identical parameter, criteria and query
  fingerprints and identical rows;
* ``capped`` overflow keeps row_limit rows and records truncation; ``exact``
  overflow fails the run and stores no partial result;
* the requester is re-read inside the snapshot: deactivated or demoted means
  ``refused`` and nothing read;
* a definition changed between submit and run fails the run; so do columns
  that differ from the declaration;
* a run whose job ended without a result is reconciled to ``failed``.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from types import SimpleNamespace

import psycopg2
import pytest

from core.reporting import REGISTRY
from core.reporting.model import ReportDefinition
from core.reporting.registry import ReportRegistry
from services.reporting import runs

from _seed import connect, document, side, source

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


def _user(cur, role, name):
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)"
                " RETURNING id", (f"{name}_{_U}", role))
    uid = cur.fetchone()[0]
    return SimpleNamespace(id=uid, role=role, username=f"{name}_{_U}",
                           has_role=lambda *roles: role in roles)


@pytest.fixture(scope="module")
def world(pg_db, app):
    word = f"zrun{_U}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1 = source(cur)
        d1 = side(cur)
        paths = [document(cur, source_id=s1, side_id=d1, text=f"run {word} item {i}",
                          file_type="pdf", file_date=datetime.date(2026, 2, 1 + i))[0]
                 for i in range(5)]
        analyst = _user(cur, "analyst", "rr_analyst")
        admin = _user(cur, "admin", "rr_admin")
    yield {"conn": conn, "word": word, "paths": paths, "source": s1, "side": d1,
           "analyst": analyst, "admin": admin, "pg_db": pg_db}
    conn.close()


def _params(world):
    return {"criteria": {"text": world["word"]}}


def _run(world, user=None, registry=REGISTRY, report_id="search_results", **kw):
    conn = world["conn"]
    run = runs.submit_run(conn, user=user or world["analyst"], report_id=report_id,
                          parameters=_params(world), registry=registry)
    out = runs.execute_run(conn, run["id"], job_id=None, registry=registry, **kw)
    return run, out


def _variant(**changes):
    """A registry whose search_results@1 listing is changed as given."""
    matches = dataclasses.replace(REGISTRY.dataset("search_results.matches@1"), **changes)
    datasets = tuple(matches if d.key == matches.key else d for d in REGISTRY.datasets)
    return ReportRegistry(reports=REGISTRY.reports, datasets=datasets,
                          help_topics=REGISTRY.help_topics)


def test_completed_run_records_snapshot_fingerprints_and_rows(world):
    run, out = _run(world)
    assert out["status"] == "completed", out
    got = runs.get_run(world["conn"], run["id"], user=world["analyst"])
    assert got["status"] == "completed"
    assert got["snapshot"] and got["snapshot_at"] and got["finished_at"]
    assert got["isolation_level"] == "repeatable read, read only"
    assert got["generator_version"] == runs.GENERATOR_VERSION
    assert got["definition_fingerprint"] == runs.definition_fingerprint(
        REGISTRY.report("search_results"))
    assert got["requester_role"] == "analyst"
    assert [d["dataset_key"] for d in got["datasets"]] == [
        "search_results.matches@1", "search_results.count@1"]
    listing, count = got["datasets"]
    assert (listing["row_count"], listing["truncated"], listing["semantics"]) == (5, False, "capped")
    page = runs.dataset_rows(world["conn"], run["id"], "search_results.count@1",
                             user=world["analyst"])
    assert page["rows"] == [{"matched": 5}]
    full = runs.dataset_rows(world["conn"], run["id"], "search_results.matches@1",
                             user=world["analyst"], limit=500)["rows"]
    assert sorted(r["path_id"] for r in full) == sorted(world["paths"])
    page = runs.dataset_rows(world["conn"], run["id"], "search_results.matches@1",
                             user=world["analyst"], limit=2, offset=1)
    assert page["rows"] == full[1:3]                  # paged in SQL, stored order kept
    assert all(isinstance(r["file_date"], str) and len(r["file_date"]) == 10
               for r in full)                         # ISO 8601, not a date object


def test_identical_requests_are_deterministic(world):
    a, _ = _run(world)
    b, _ = _run(world)
    ga = runs.get_run(world["conn"], a["id"], user=world["analyst"])
    gb = runs.get_run(world["conn"], b["id"], user=world["analyst"])
    for key in ("definition_fingerprint", "parameters_fingerprint", "criteria_fingerprint",
                "parameters"):
        assert ga[key] == gb[key], key
    assert [d["query_fingerprint"] for d in ga["datasets"]] == \
           [d["query_fingerprint"] for d in gb["datasets"]]
    rows = [runs.dataset_rows(world["conn"], r["id"], "search_results.matches@1",
                              user=world["analyst"], limit=500)["rows"] for r in (a, b)]
    assert rows[0] == rows[1] and rows[0]


def test_every_dataset_reads_the_same_snapshot(world, monkeypatch):
    """A matching document committed by another connection after the first
    dataset was read is in neither dataset: count and listing agree."""
    original = runs._read_dataset
    other = connect(world["pg_db"])
    inserted = []

    def read_then_insert(cur, dataset, bound):
        result = original(cur, dataset, bound)
        if not inserted:
            with other, other.cursor() as ocur:
                inserted.append(document(ocur, source_id=world["source"], side_id=world["side"],
                                         text=f"late {world['word']}", file_type="pdf",
                                         file_date=datetime.date(2026, 3, 1))[0])
        return result

    monkeypatch.setattr(runs, "_read_dataset", read_then_insert)
    try:
        run, out = _run(world)
    finally:
        other.close()
    assert out["status"] == "completed" and inserted
    count = runs.dataset_rows(world["conn"], run["id"], "search_results.count@1",
                              user=world["analyst"])["rows"][0]["matched"]
    listing = runs.dataset_rows(world["conn"], run["id"], "search_results.matches@1",
                                user=world["analyst"], limit=500)["rows"]
    assert count == len(listing)
    assert inserted[0] not in [r["path_id"] for r in listing]
    # The document is committed: a new run sees it.
    later, _ = _run(world)
    assert runs.dataset_rows(world["conn"], later["id"], "search_results.count@1",
                             user=world["analyst"])["rows"][0]["matched"] == count + 1


def test_capped_overflow_is_recorded_as_truncation(world):
    registry = _variant(row_limit=2)
    run, out = _run(world, registry=registry)
    assert out["status"] == "completed"
    got = runs.get_run(world["conn"], run["id"], user=world["analyst"], registry=registry)
    listing, count = got["datasets"]
    assert (listing["row_count"], listing["row_limit"], listing["truncated"]) == (2, 2, True)
    assert count["truncated"] is False
    total = runs.dataset_rows(world["conn"], run["id"], "search_results.count@1",
                              user=world["analyst"], registry=registry)["rows"][0]["matched"]
    assert total > listing["row_count"]       # the exact count says how many were not shown


def test_exact_overflow_fails_the_run_without_partial_results(world):
    registry = _variant(semantics="exact", row_limit=2)
    run, out = _run(world, registry=registry)
    assert out["status"] == "failed"
    assert "exact but returned more than its limit of 2 rows" in out["error"]
    got = runs.get_run(world["conn"], run["id"], user=world["analyst"], registry=registry)
    assert got["status"] == "failed" and got["datasets"] == [] and got["snapshot"] is None


def test_requester_is_revalidated_inside_the_snapshot(world):
    conn = world["conn"]
    with conn, conn.cursor() as cur:
        user = _user(cur, "analyst", f"rr_gone{uuid.uuid4().hex[:4]}")
    run = runs.submit_run(conn, user=user, report_id="search_results",
                          parameters=_params(world))
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET is_active = false WHERE id = %s", (user.id,))
    out = runs.execute_run(conn, run["id"])
    assert (out["status"], out["refusal_reason"]) == ("refused", "requester_inactive")
    got = runs.get_run(conn, run["id"], user=world["admin"])
    assert got["datasets"] == [] and got["refusal_reason"] == "requester_inactive"
    assert got["snapshot"]       # the refusal is itself a statement about that moment

    # Demoted to a role the report no longer allows.
    analysts_only = ReportRegistry(
        reports=tuple(dataclasses.replace(r, roles=("admin", "analyst")) for r in REGISTRY.reports),
        datasets=REGISTRY.datasets, help_topics=REGISTRY.help_topics)
    with conn, conn.cursor() as cur:
        demoted = _user(cur, "analyst", f"rr_demoted{uuid.uuid4().hex[:4]}")
    run = runs.submit_run(conn, user=demoted, report_id="search_results",
                          parameters=_params(world), registry=analysts_only)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET role = 'viewer' WHERE id = %s", (demoted.id,))
    out = runs.execute_run(conn, run["id"], registry=analysts_only)
    assert (out["status"], out["refusal_reason"]) == ("refused", "requester_role")


def test_definition_changed_after_submission_fails(world):
    conn = world["conn"]
    run = runs.submit_run(conn, user=world["analyst"], report_id="search_results",
                          parameters=_params(world))
    out = runs.execute_run(conn, run["id"], registry=_variant(row_limit=4999))
    assert out["status"] == "failed" and "definition fingerprint differs" in out["error"]


def test_columns_that_differ_from_the_declaration_fail(world):
    ds = REGISTRY.dataset("search_results.matches@1")
    registry = _variant(sql=ds.sql.replace("p.file_type,", "p.file_type AS kind,"))
    run, out = _run(world, registry=registry)
    assert out["status"] == "failed" and "declared" in out["error"]


def test_cancelled_before_reading(world):
    run, out = _run(world, cancel_cb=lambda: True)
    assert out["status"] == "cancelled"
    assert runs.get_run(world["conn"], run["id"], user=world["analyst"])["datasets"] == []


def test_invalid_requests_are_refused_at_submission(world):
    conn = world["conn"]
    cases = [
        (dict(report_id="nope"), "NOT_FOUND"),
        (dict(report_id="search_results", parameters={"criteria": {"text": "x"}, "extra": 1}),
         "VALIDATION_FAILED"),
        (dict(report_id="search_results", parameters={}), "VALIDATION_FAILED"),
        (dict(report_id="search_results", parameters={"criteria": "x"}), "VALIDATION_FAILED"),
        (dict(report_id="search_results", version=9), "NOT_FOUND"),
        (dict(report_id="search_results", version="1"), "VALIDATION_FAILED"),
    ]
    with conn, conn.cursor() as cur:
        viewer = _user(cur, "viewer", f"rr_viewer{uuid.uuid4().hex[:4]}")
    with pytest.raises(runs.ReportRunError) as info:
        runs.submit_run(conn, user=viewer, report_id="search_results", parameters=_params(world))
    assert (info.value.code, info.value.status) == ("FORBIDDEN", 403)
    for kwargs, code in cases:
        with pytest.raises(runs.ReportRunError) as info:
            runs.submit_run(conn, user=world["analyst"], **kwargs)
        assert info.value.code == code, kwargs


def test_orphaned_run_is_reconciled_to_failed(world):
    from services.jobs.manager import JobManager

    conn = world["conn"]
    run = runs.submit_run(conn, user=world["analyst"], report_id="search_results",
                          parameters=_params(world))
    job = JobManager(synchronous=True).repo.create({
        "job_id": uuid.uuid4().hex[:12].upper(), "job_type": "report_run", "status": "FAILED",
        "progress": 0, "source": "test", "options": {"run_id": run["id"]}, "created_by": "t"})
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE report_runs SET status = 'running', started_at = NOW(), job_id = %s"
                    " WHERE id = %s", (job["job_id"], run["id"]))
    got = runs.get_run(conn, run["id"], user=world["analyst"])
    assert got["status"] == "failed"
    assert got["error"] == "the report job ended without recording a result"


def test_visibility_owner_admin_and_not_others(world):
    conn = world["conn"]
    run, _ = _run(world)
    with conn, conn.cursor() as cur:
        stranger = _user(cur, "analyst", f"rr_stranger{uuid.uuid4().hex[:4]}")
    with pytest.raises(runs.ReportRunError) as info:
        runs.get_run(conn, run["id"], user=stranger)
    assert info.value.status == 404
    with pytest.raises(runs.ReportRunError):
        runs.dataset_rows(conn, run["id"], "search_results.count@1", user=stranger)
    assert runs.get_run(conn, run["id"], user=world["admin"])["id"] == run["id"]
    mine = runs.list_runs(conn, user=stranger)
    assert mine["items"] == [] and mine["total"] == 0
    with pytest.raises(runs.ReportRunError) as info:
        runs.list_runs(conn, user=stranger, all_users=True)
    assert info.value.status == 403
    everyone = runs.list_runs(conn, user=world["admin"], all_users=True, limit=200)
    assert run["id"] in [r["id"] for r in everyone["items"]]


def test_latest_run_records_and_advances_the_readers_baseline(world):
    """The Latest report is read per viewer: the run records what the
    reader had seen as its snapshot found it, advances the baseline to the
    furthest row actually shown (never backwards), and the rows stored with
    the run keep the classification they were read with."""
    conn = world["conn"]

    def _dataset_rows(run_id, user):
        page = runs.dataset_rows(conn, run_id, "latest.entries@1", user=user)
        return page["baseline"], page["rows"]

    # First run: this reader has never viewed this view - an absent
    # baseline, never a zero.
    run1, out1 = _run(world, report_id="latest")
    assert out1["status"] == "completed", out1
    base1, rows1 = _dataset_rows(run1["id"], world["analyst"])
    assert base1["baseline_recorded"] is True
    assert base1["last_max_id_before"] is None, (
        "never viewed is an absent row, not zero")
    assert base1["advanced_to"] == max(r["path_id"] for r in rows1)
    assert rows1 and all(r["view_state"] == "new_since_last_view" for r in rows1)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT last_max_id FROM report_baselines"
                    " WHERE user_id = %s AND criteria_hash = %s",
                    (world["analyst"].id, base1["criteria_hash"]))
        assert cur.fetchone()[0] == base1["advanced_to"]

    # Second run: same view, same reader - everything previously seen, and
    # the run records the baseline it actually used.
    run2, out2 = _run(world, report_id="latest")
    assert out2["status"] == "completed", out2
    base2, rows2 = _dataset_rows(run2["id"], world["analyst"])
    assert base2["last_max_id_before"] == base1["advanced_to"]
    assert rows2 and all(r["view_state"] == "previously_seen" for r in rows2)

    # The rows stored with the first run keep what they said then: a later
    # baseline never rewrites a completed run (reproducibility).
    base1b, rows1b = _dataset_rows(run1["id"], world["analyst"])
    assert all(r["view_state"] == "new_since_last_view" for r in rows1b)
    assert base1b["advanced_to"] == base1["advanced_to"]

    # A new document arrives; the next run shows exactly it as new and
    # advances this reader to it.
    with conn, conn.cursor() as cur:
        fresh_pid = document(cur, source_id=world["source"], side_id=world["side"],
                             text=f"run {world['word']} fresh after baseline")[0]
    run3, out3 = _run(world, report_id="latest")
    assert out3["status"] == "completed", out3
    base3, rows3 = _dataset_rows(run3["id"], world["analyst"])
    states = {r["path_id"]: r["view_state"] for r in rows3}
    assert states[fresh_pid] == "new_since_last_view"
    assert sum(1 for v in states.values() if v == "new_since_last_view") == 1, (
        "only the arrival is new; nothing else is re-marked")
    assert base3["advanced_to"] == fresh_pid

    # Reader isolation: another reader's first view of the same view is
    # all new - progress is per reader, never shared.
    run4, out4 = _run(world, user=world["admin"], report_id="latest")
    assert out4["status"] == "completed", out4
    base4, rows4 = _dataset_rows(run4["id"], world["admin"])
    assert base4["last_max_id_before"] is None
    assert all(r["view_state"] == "new_since_last_view" for r in rows4)


def test_change_run_advances_one_watermark_for_all_its_datasets(world):
    """The Change report's three datasets are one view: one baseline read,
    one advance. A recorded modification shows exactly once; the second
    run marks it seen; same-day additions stay visible (the ingestion
    event's resolution is a date); and an empty view still records that it
    was viewed - the id watermark holds, the time watermark moves."""
    conn = world["conn"]

    def _blocks(run_id, user):
        return {key: runs.dataset_rows(conn, run_id, key, user=user)
                for key in ("change.added@1", "change.modified@1",
                            "change.removed@1")}

    from core.criteria.access import scope_for

    # A reader of their own: the view watermark is shared by Latest and
    # Change when the view is the same (one baseline per distinct view,
    # not per report), and earlier tests in this module ran Latest.
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rr_change_{_U}",))
        reader = SimpleNamespace(id=cur.fetchone()[0], role="analyst",
                                 username=f"rr_change_{_U}",
                                 has_role=lambda *roles: False)

    bound = REGISTRY.dataset("search_results.count@1").bind(
        {"criteria": {"text": world["word"]}}, scope_for(world["analyst"]))
    with conn.cursor() as cur:
        cur.execute(bound.sql, bound.params)
        matched = cur.fetchone()[0]
    conn.rollback()

    run1, out1 = _run(world, user=reader, report_id="change")
    assert out1["status"] == "completed", out1
    b1 = _blocks(run1["id"], reader)
    keys = {b["baseline"]["criteria_hash"] for b in b1.values()}
    assert len(keys) == 1, "the three states share one view watermark"
    assert b1["change.added@1"]["row_count"] == matched, (
        "first view: the whole matched set is added")
    assert b1["change.modified@1"]["row_count"] == 0
    assert b1["change.removed@1"]["row_count"] == 0
    base1 = b1["change.added@1"]["baseline"]
    assert base1["last_max_id_before"] is None, "never viewed is not zero"
    assert base1["baseline_recorded"] is True
    assert base1["advanced_to"] == max(
        r["path_id"] for r in b1["change.added@1"]["rows"]), (
        "advanced to the furthest row actually shown")

    # A recorded modification after the watermark, through the single
    # writer the operations use.
    from services.changes import record_path_revision

    record_path_revision(world["paths"][0], "modified",
                         old_values={"file_name": "before.txt"},
                         new_values={"file_name": "after.txt"})
    run2, out2 = _run(world, user=reader, report_id="change")
    assert out2["status"] == "completed", out2
    b2 = _blocks(run2["id"], reader)
    assert b2["change.modified@1"]["row_count"] == 1
    row = b2["change.modified@1"]["rows"][0]
    assert row["path_id"] == world["paths"][0]
    assert row["previous_value"] == "before.txt"
    assert row["current_value"] == "after.txt"
    assert b2["change.modified@1"]["baseline"]["last_max_id_before"] == \
        base1["advanced_to"]

    # Seen is seen: the modification does not re-mark; same-day additions
    # remain visible (date granularity is the ingestion event's own).
    run3, out3 = _run(world, user=reader, report_id="change")
    assert out3["status"] == "completed", out3
    b3 = _blocks(run3["id"], reader)
    assert b3["change.modified@1"]["row_count"] == 0
    assert b3["change.added@1"]["row_count"] == matched

    # An empty view (criteria matching nothing = a different view key) is
    # still a view: recorded, with the id watermark kept and the time
    # watermark moved - the reader is not re-shown anything for it.
    run4 = runs.submit_run(conn, user=reader, report_id="change",
                           parameters={"criteria": {"text": f"zqq_none_{_U}"}})
    out4 = runs.execute_run(conn, run4["id"])
    assert out4["status"] == "completed", out4
    b4 = _blocks(run4["id"], reader)
    assert b4["change.added@1"]["row_count"] == 0
    base4 = b4["change.added@1"]["baseline"]
    assert base4["baseline_recorded"] is True
    assert base4["last_max_id_before"] is None
    assert base4["advanced_to"] == 0, "viewed and saw nothing - not a zero measurement"


def test_scenario_outcome_submit_refuses_before_retrieval(world):
    """The declared access control is enforced at submission: the owner's
    run completes; another reader is refused with no run created; a
    scenario that does not exist is a validation error."""
    conn = world["conn"]
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rr_sc_owner_{_U}",))
        owner = SimpleNamespace(id=cur.fetchone()[0], role="analyst",
                                username=f"rr_sc_owner_{_U}",
                                has_role=lambda *roles: False)
        cur.execute("INSERT INTO users (username, password_hash, role)"
                    " VALUES (%s, 'x', 'analyst') RETURNING id",
                    (f"rr_sc_other_{_U}",))
        other = SimpleNamespace(id=cur.fetchone()[0], role="analyst",
                                username=f"rr_sc_other_{_U}",
                                has_role=lambda *roles: False)
        cur.execute(
            "INSERT INTO scenarios (owner_user_id, name, version, definition,"
            " definition_fingerprint, status)"
            " VALUES (%s, %s, 1, '{}'::jsonb,"
            " encode(sha256(%s::bytea), 'hex')::char(64), 'paused')"
            " RETURNING id",
            (owner.id, f"runs_scenario_{_U}", f"runs_scenario_{_U}".encode()))
        scenario_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO scenario_evaluations (scenario_id, scenario_version,"
            " kind, trigger, status, definition_fingerprint, reference_date,"
            " evaluated_at, counts)"
            " VALUES (%s, 1, 'evaluation', 'manual', 'completed',"
            " encode(sha256(%s::bytea), 'hex')::char(64), CURRENT_DATE,"
            " NOW(), '{}'::jsonb) RETURNING id",
            (scenario_id, f"runs_eval_{_U}".encode()))
        evaluation_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO scenario_outcomes (scenario_id, evaluation_id,"
            " scenario_version, hash_id, outcomes, matched_cases, delivery,"
            " recorded_at) SELECT %s, %s, 1, h.id, '{watch}', '{c}',"
            " 'recorded', NOW() FROM hashs h LIMIT 1",
            (scenario_id, evaluation_id))

    def _submit(user, sid):
        return runs.submit_run(conn, user=user, report_id="scenario_outcome",
                               parameters={"scenario_id": sid})

    run = _submit(owner, scenario_id)
    out = runs.execute_run(conn, run["id"])
    assert out["status"] == "completed", out
    page = runs.dataset_rows(conn, run["id"], "scenario.outcomes@1", user=owner)
    assert page["row_count"] == 1
    assert page["baseline"] is None, (
        "outcome history is not a per-reader view - no baseline block")
    row = page["rows"][0]
    assert row["outcomes"] == ["watch"] and row["delivery"] == "recorded"

    with conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM report_runs")
        runs_before = cur.fetchone()[0]
    try:
        _submit(other, scenario_id)
        refused = None
    except runs.ReportRunError as exc:
        refused = exc
    assert refused is not None and refused.status == 403, (
        "a non-owner is refused at submission")
    assert "do not have access" in str(refused)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM report_runs")
        assert cur.fetchone()[0] == runs_before, (
            "refused before anything is recorded - no run to hide")

    try:
        _submit(owner, scenario_id + 1)
        missing = None
    except runs.ReportRunError as exc:
        missing = exc
    assert missing is not None and missing.status == 400, (
        "a scenario that does not exist is a validation error, not an "
        "empty report")
