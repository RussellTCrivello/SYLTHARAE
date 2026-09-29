"""Step 16 against PostgreSQL: analyses computed from a run's datasets.

* the keyness datasets give the hand-computed oracle on a corpus isolated by
  access scope (a content with two contexts counts once; NULL counts are
  unknown, not zero; the reference corpus is access-scoped);
* SQL G2/Log Ratio (used to rank) equal the reference implementation;
* a run of ``term_keyness@2`` (the active version) stores its analysis (m0026) with the registry
  fingerprint, five voices and template version; the stored narrative is
  template references and renders deterministically;
* not-measurable is stored as such, with its reason;
* a disagreement between SQL and the reference fails the run and stores
  nothing;
* the table is write-once, its CHECKs hold, and it follows its run on delete;
* JSON/HTML artifacts carry the analysis and every manifest lists it.
"""

from __future__ import annotations

import json
import math
import uuid
from types import SimpleNamespace

import psycopg2
import pytest

from core.analytics import measures
from core.analytics.narrative import render, source_ngettext
from core.criteria.compiler import AccessScope
from core.reporting import REGISTRY
from services.reporting import artifacts, runs

from _seed import connect, document, side, source, word_counts

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
RANKED = REGISTRY.dataset("term_keyness.ranked@1")
TOTALS = REGISTRY.dataset("term_keyness.totals@1")
REPORT = REGISTRY.report("term_keyness")


def _user(cur, role, name):
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)"
                " RETURNING id", (f"{name}_{_U}", role))
    uid = cur.fetchone()[0]
    return SimpleNamespace(id=uid, role=role, username=f"{name}_{_U}",
                           has_role=lambda *roles: role in roles)


@pytest.fixture(scope="module")
def world(pg_db, app):
    """Target: one content (two paths in two sources) whose text holds the
    marker; reference: one content in s2. Counts chosen so that
    c = 10,000 and d = 20,000 and alpha is 100 vs 50 (G2 = 100 ln 2)."""
    marker = f"zkey{_U}"
    alpha, filler, rare = f"alpha{_U}", f"filler{_U}", f"rare{_U}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1, s2 = source(cur), source(cur)
        d1 = side(cur)
        _, target_hash, _ = document(cur, source_id=s1, side_id=d1, text=f"{marker} target")
        document(cur, source_id=s2, side_id=d1, text=f"{marker} target", hash_id=target_hash)
        word_counts(cur, target_hash, {alpha: 100, filler: 9_900})
        _, ref_hash, _ = document(cur, source_id=s2, side_id=d1, text="reference only")
        word_counts(cur, ref_hash, {alpha: 50, filler: 19_940, rare: 10, f"unknown{_U}": None})
        analyst = _user(cur, "analyst", "an_analyst")
    yield {"conn": conn, "marker": marker, "alpha": alpha, "filler": filler, "rare": rare,
           "s1": s1, "s2": s2, "analyst": analyst, "target_hash": target_hash}
    conn.close()


def _values(world, direction="over"):
    return REPORT.normalize_parameters({"criteria": {"text": world["marker"]},
                                        "direction": direction})


def _read(conn, dataset, values, scope):
    bound = dataset.bind(values, scope)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(bound.sql, bound.params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.rollback()
    return rows


def _scope(world, *sources):
    return AccessScope(allowed_source_ids=tuple(sources or (world["s1"], world["s2"])))


# ------------------------------------------------------------------ datasets

def test_keyness_datasets_match_the_hand_computed_oracle(world):
    conn, scope = world["conn"], _scope(world)
    [tot] = _read(conn, TOTALS, _values(world), scope)
    assert tot == {"target_tokens": 10_000, "reference_tokens": 20_000,
                   "target_contents": 1, "reference_contents": 1,
                   "unknown_count_rows": 1, "significant_terms": 1}
    rows = _read(conn, RANKED, _values(world), scope)
    assert [r["term"] for r in rows] == [world["alpha"]], rows
    top = rows[0]
    assert (top["target_freq"], top["reference_freq"]) == (100, 50)
    assert top["g2"] == pytest.approx(100 * math.log(2), rel=1e-12)
    assert top["log_ratio"] == pytest.approx(2.0, rel=1e-12)


def test_under_direction_lists_the_other_side_with_equal_values(world):
    conn, scope = world["conn"], _scope(world)
    rows = _read(conn, RANKED, _values(world, "under"), scope)
    assert {r["term"] for r in rows} == {world["filler"], world["rare"]}
    for r in rows:
        a, b = r["target_freq"], r["reference_freq"]
        assert r["g2"] == pytest.approx(measures.log_likelihood(a, b, 10_000, 20_000), rel=1e-9)
        assert r["log_ratio"] == pytest.approx(measures.log_ratio(a, b, 10_000, 20_000), rel=1e-9)
    assert [r["g2"] for r in rows] == sorted((r["g2"] for r in rows), reverse=True)
    # rare: a = 0 -> Log Ratio uses 0.5: log2((0.5/10000)/(10/20000)).
    rare = next(r for r in rows if r["term"] == world["rare"])
    assert rare["log_ratio"] == pytest.approx(math.log2((0.5 / 10_000) / (10 / 20_000)))


def test_the_reference_corpus_is_access_scoped(world):
    conn = world["conn"]
    # Only s1 visible: the target content is visible (its s1 path), the
    # reference content (s2 only) is not - nothing to compare with.
    [tot] = _read(conn, TOTALS, _values(world), _scope(world, world["s1"]))
    assert tot["target_contents"] == 1 and tot["reference_contents"] == 0
    assert tot["reference_tokens"] == 0 and tot["significant_terms"] == 0
    assert _read(conn, RANKED, _values(world), _scope(world, world["s1"])) == []
    [none] = _read(conn, TOTALS, _values(world), AccessScope(allowed_source_ids=()))
    assert none["target_contents"] == 0 and none["reference_contents"] == 0


# ------------------------------------------------------------------ runs

def _run(world, direction="over", registry=REGISTRY, version=None):
    conn = world["conn"]
    run = runs.submit_run(conn, user=world["analyst"], report_id="term_keyness",
                          version=version,
                          parameters={"criteria": {"text": world["marker"]},
                                      "direction": direction}, registry=registry)
    return run, runs.execute_run(conn, run["id"], job_id=None, registry=registry)


def test_a_run_stores_its_analysis(world):
    run, out = _run(world)
    assert out["status"] == "completed", out
    assert out["analyses"] == [{"analysis_key": "term_keyness@2", "state": "measured",
                                "reason": None}]
    got = runs.get_run(world["conn"], run["id"], user=world["analyst"])
    assert got["generator_version"] == "report-runner/2"
    [a] = got["analyses"]
    assert a["analysis_fingerprint"] == REGISTRY.analysis_fingerprints()["term_keyness@2"]
    assert a["template_set"] == "keyness" and a["template_version"] == 2
    assert a["inputs"] == {"ranked": "term_keyness.ranked@1", "totals": "term_keyness.totals@1"}
    assert [v["voice"] for v in a["narrative"]["voices"]] == [
        "measure", "finding", "confidence", "consequence", "caveat"]
    assert a["narrative"]["format"] == 2
    sentences = [s for v in a["narrative"]["voices"] for s in v["sentences"]]
    assert all("msgid" in s and "params" in s and "text" not in s
               for s in sentences), "stored as references, never prose"
    assert all("text" not in v for v in a["narrative"]["voices"])
    assert any("msgid_plural" in s for s in sentences), "counts are plural sentences"
    # Every stored row agrees with the stored totals under the reference
    # implementation - the run's own evidence is self-consistent.
    c, d = a["measures"]["target_tokens"], a["measures"]["reference_tokens"]
    for row in a["rows"]:
        assert row["g2"] == pytest.approx(
            measures.log_likelihood(row["target_freq"], row["reference_freq"], c, d), rel=1e-12)
    assert world["alpha"] in [r["term"] for r in a["rows"]]
    # Rendering from the record is deterministic.
    assert render(a["narrative"], lambda s: s, ngettext=source_ngettext) == render(
        a["narrative"], lambda s: s, ngettext=source_ngettext)


def test_a_run_with_nothing_to_compare_stores_not_measurable(world):
    conn = world["conn"]
    run = runs.submit_run(conn, user=world["analyst"], report_id="term_keyness",
                          parameters={"criteria": {"text": f"absent{_U}"}})
    out = runs.execute_run(conn, run["id"], job_id=None)
    assert out["status"] == "completed", out
    [a] = runs.get_run(conn, run["id"], user=world["analyst"])["analyses"]
    assert a["state"] == "not_measurable" and a["reason"] == "no_target"
    assert a["rows"] == [] and "significant_terms" not in a["measures"]


def test_a_disagreement_fails_the_run_and_stores_nothing(world, monkeypatch):
    from core.analytics import kinds

    real = measures.log_likelihood
    monkeypatch.setattr(kinds.measures, "log_likelihood",
                        lambda *a: (real(*a) or 0) + 1.0)
    run, out = _run(world)
    assert out["status"] == "failed" and "disagree" in out["error"], out
    with world["conn"].cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM report_run_analyses WHERE run_id = %s),"
                    " (SELECT count(*) FROM report_run_datasets WHERE run_id = %s)",
                    (run["id"], run["id"]))
        assert cur.fetchone() == (0, 0)
    world["conn"].rollback()


# ------------------------------------------------------------------ database

def test_the_superseded_version_still_runs_and_stores_its_released_record(world):
    """NARR-01 superseded term_keyness@1; it must stay runnable on request and
    store exactly the record shape it was released with (format 1: one
    sentence per voice, no plurals), and both formats render."""
    run, out = _run(world, version=1)
    assert out["status"] == "completed", out
    assert out["analyses"][0]["analysis_key"] == "term_keyness@1"
    [a] = runs.get_run(world["conn"], run["id"], user=world["analyst"])["analyses"]
    assert a["template_version"] == 1 and "format" not in a["narrative"]
    assert all(set(v) == {"voice", "key", "msgid", "params"} for v in a["narrative"]["voices"])
    assert a["analysis_fingerprint"] == REGISTRY.analysis_fingerprints()["term_keyness@1"]
    v1_text = render(a["narrative"], lambda s: s)          # format 1 needs no ngettext
    run2, _ = _run(world)                                   # the active version
    [b] = runs.get_run(world["conn"], run2["id"], user=world["analyst"])["analyses"]
    assert b["template_version"] == 2
    v2_text = render(b["narrative"], lambda s: s, ngettext=source_ngettext)
    # Same measures, different wording: only the narrative changed.
    assert a["measures"] == b["measures"] and a["rows"] == b["rows"]
    assert [t["voice"] for t in v1_text] == [t["voice"] for t in v2_text]
    assert v1_text != v2_text


def test_analyses_are_write_once_checked_and_follow_their_run(world):
    run, out = _run(world)
    conn = world["conn"]
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.IntegrityConstraintViolation, match="written once"):
            cur.execute("UPDATE report_run_analyses SET state = 'measured' WHERE run_id = %s",
                        (run["id"],))
    conn.rollback()
    voices = json.dumps({"voices": [{}] * 5})
    for state, reason, narrative, message in (
            ("not_measurable", None, voices, "ck_report_run_analyses_reason"),
            ("measured", "x", voices, "ck_report_run_analyses_reason"),
            ("guessed", None, voices, "ck_report_run_analyses_state"),
            ("measured", None, json.dumps({"voices": [{}] * 4}), "ck_report_run_analyses_voices")):
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.CheckViolation, match=message):
                cur.execute(
                    "INSERT INTO report_run_analyses (run_id, position, analysis_key,"
                    " analysis_fingerprint, kind, state, reason, inputs, measures, rows,"
                    " narrative, template_set, template_version) VALUES"
                    " (%s, 9, 'x@1', %s, 'keyness', %s, %s, '{}', '{}', '[]', %s, 'k', 1)",
                    (run["id"], "a" * 64, state, reason, narrative))
        conn.rollback()
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("INSERT INTO report_run_analyses (run_id, position, analysis_key,"
                        " analysis_fingerprint, kind, state, reason, inputs, measures, rows,"
                        " narrative, template_set, template_version) VALUES"
                        " (%s, 9, 'term_keyness@2', %s, 'keyness', 'measured', NULL, '{}',"
                        " '{}', '[]', %s, 'k', 1)", (run["id"], "a" * 64, voices))
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM report_runs WHERE id = %s", (run["id"],))
        cur.execute("SELECT count(*) FROM report_run_analyses WHERE run_id = %s", (run["id"],))
        assert cur.fetchone()[0] == 0
    conn.rollback()      # keep the run for nobody else; nothing committed


# ------------------------------------------------------------------ artifacts

def test_artifacts_carry_the_analysis_and_manifests_list_it(world):
    run, out = _run(world)
    conn = world["conn"]
    found = {}
    for fmt, key in (("json", None), ("html", None), ("csv", "term_keyness.ranked@1")):
        plan = artifacts.request_artifact(conn, run["id"], user=world["analyst"], fmt=fmt,
                                          dataset_key=key)
        assert "create" in plan, plan
        made = artifacts.create_artifact(conn, run_id=run["id"], fmt=fmt, dataset_key=key,
                                         creator_id=world["analyst"].id)
        assert made["status"] == "created", made
        row = artifacts.artifact_for_download(conn, made["artifact"]["id"], user=world["analyst"])
        found[fmt] = row
        manifest = row["manifest"]
        assert manifest["manifest_version"] == "report-manifest/3"
        [listed] = manifest["analyses"]
        assert listed["analysis_key"] == "term_keyness@2" and listed["state"] == "measured"
        assert listed["included"] is (fmt in ("json", "html"))
        assert listed["template_version"] == 2 and len(listed["template_fingerprint"]) == 64
    body = json.loads(bytes(found["json"]["content"]))
    assert body["format"] == "report-json/3"
    [a] = body["analyses"]
    assert [t["voice"] for t in a["text"]] == ["measure", "finding", "confidence",
                                               "consequence", "caveat"]
    assert a["narrative"]["voices"][0]["sentences"][0]["msgid"], \
        "references kept next to the text"
    page = bytes(found["html"]["content"]).decode("utf-8")
    assert "Distinctive terms" in page and "<dt>Finding</dt>" in page
    assert world["alpha"] in page
