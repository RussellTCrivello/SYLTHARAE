"""The Comprehensive Intelligence report (step 23) over PostgreSQL: one
run, one snapshot, three sections. The overview row is verified against
hand-counted seeds, each section keeps its own state (a section that
cannot be measured is not zero), the run is deterministic, and a listing
that contradicts the overview's matched-set size fails the run."""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import uuid
from types import SimpleNamespace

import pytest

from core.reporting import REGISTRY
from core.reporting.registry import ReportRegistry
from services.reporting import runs

from _seed import connect, document, side, source, word_counts
from test_reports_api import sync_jobs  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_TOKEN = f"zcomp{_U}"


def _user(cur, role, name):
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)"
                " RETURNING id", (f"{name}_{_U}", role))
    uid = cur.fetchone()[0]
    return SimpleNamespace(id=uid, role=role, username=f"{name}_{_U}",
                           has_role=lambda *roles: role in roles)


@pytest.fixture(scope="module")
def world(pg_db, app):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1 = source(cur)
        s2 = source(cur)
        d1 = side(cur)
        p1, h1, _ = document(cur, source_id=s1, side_id=d1,
                             text=f"{_TOKEN} alpha report", file_type="pdf",
                             file_date=datetime.date(2026, 1, 5))
        p2, h2, _ = document(cur, source_id=s1, side_id=d1,
                             text=f"{_TOKEN} beta brief", file_type="txt",
                             file_date=datetime.date(2026, 1, 10))
        p3, h3, _ = document(cur, source_id=s2, side_id=d1,
                             text=f"{_TOKEN} gamma memo", file_type="pdf",
                             file_date=datetime.date(2026, 2, 15))
        # Words: kw on h1 and h3, common everywhere, plus one unknown count.
        cur.execute("INSERT INTO words (word) VALUES (%s)"
                    " ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word"
                    " RETURNING id", (f"zkw{_U}",))
        kw_word = cur.fetchone()[0]
        word_counts(cur, h1, {f"zkw{_U}": 3, "zshare": 1})
        word_counts(cur, h2, {"zshare": 2})
        word_counts(cur, h3, {f"zkw{_U}": 1, "zshare": 1, "zunknown": None})
        cur.execute("INSERT INTO categorys (word_id)"
                    " SELECT id FROM words WHERE word = %s RETURNING id",
                    (f"zkw{_U}",))
        cat_a = cur.fetchone()[0]
        cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                    " VALUES (%s, %s)", (kw_word, cat_a))
        from core.serialization import pack_int_list
        cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)"
                    " RETURNING id", (pack_int_list([kw_word]), cat_a))
        keyword_id = cur.fetchone()[0]
        for h, count in ((h1, 3), (h3, 1)):
            cur.execute("INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)"
                        " VALUES (%s, %s, %s)", (h, keyword_id, count))
        # One durable temporal signal on the first content.
        body = f"{_TOKEN} alpha report"
        cur.execute(
            "INSERT INTO content_signals (hash_id, detector, detector_ver,"
            " signal_type, value, surface, char_start, char_end, language,"
            " calendar, resolution, date_from, date_to, text_orientation,"
            " anchor_date, evidence, dedup_key, method, confidence,"
            " confidence_basis, evidence_sentence, sentence_start,"
            " sentence_end)"
            " VALUES (%s, 'temporal', 'temporal-1.1.0', 'date_reference',"
            " '2026-02-01', '2026-02-01', 3, 20, 'en', 'gregorian', 'absolute',"
            " %s, %s, 'future', %s, %s::jsonb, %s, 'parse', 'high',"
            " 'explicit_day', %s, 0, %s)",
            (h1, datetime.date(2026, 2, 1), datetime.date(2026, 2, 1),
             datetime.date(2026, 2, 1), '{"normalized": "seed"}',
             hashlib.sha256(f"{_U}-sig".encode()).hexdigest(), body, len(body)))
        analyst = _user(cur, "analyst", "comp_analyst")
    yield {"conn": conn, "analyst": analyst, "paths": [p1, p2, p3],
           "hashes": [h1, h2, h3], "pg_db": pg_db, "app": app}
    conn.close()


def _params():
    return {"criteria": {"text": _TOKEN}}


def _run(world, registry=REGISTRY):
    conn = world["conn"]
    run = runs.submit_run(conn, user=world["analyst"], report_id="comprehensive",
                          parameters=_params(), registry=registry)
    out = runs.execute_run(conn, run["id"], job_id=None, registry=registry)
    return run, out


def _sections(conn, run_id):
    return runs.run_analyses(conn, run_id)


def test_comprehensive_run_records_three_honest_sections(world):
    run, out = _run(world)
    assert out["status"] == "completed", out
    conn = world["conn"]
    got = runs.get_run(conn, run["id"], user=world["analyst"])
    assert got["isolation_level"] == "repeatable read, read only"
    assert [d["dataset_key"] for d in got["datasets"]] == list(
        REGISTRY.report("comprehensive").datasets)
    overview = got["datasets"][0]
    assert overview["semantics"] == "exact" and overview["truncated"] is False
    overview_rows = runs.dataset_rows(conn, run["id"], "comprehensive.overview@1",
                                      user=world["analyst"])["rows"]
    # Hand-counted seeds: 3 contents, 3 files, 2 sources, 1 keyword,
    # 1 category, ingest span, event span, one unknown word count.
    assert overview_rows == [{
        "contents": 3, "paths": 3, "sources": 2, "keywords": 1,
        "categories": 1, "first_ingest": datetime.date.today().isoformat(),
        "last_ingest": datetime.date.today().isoformat(),
        "first_event": "2026-02-01",
        "last_event": "2026-02-01", "unknown_count_rows": 1}]
    sections = {a["analysis_key"]: a for a in _sections(conn, run["id"])}
    assert set(sections) == {"composition@1", "term_keyness@2", "coverage@1"}
    comp = sections["composition@1"]
    assert comp["state"] == "measured" and comp["reason"] is None
    assert comp["measures"] == {"contents": 3, "paths": 3, "sources": 2,
                                "keywords": 1, "categories": 1,
                                "unknown_count_rows": 1}
    keyness = sections["term_keyness@2"]
    # The reference corpus is the whole visible collection minus the matched
    # set. Whether this run can measure depends on what else the session
    # database holds, so the expected state is derived from the database -
    # the section must match reality either way, never manufacture one.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT hc.hash_id FROM paths p"
            " JOIN hash_contexts hc ON hc.id = p.context_id"
            " EXCEPT SELECT DISTINCT hc2.hash_id FROM paths p2"
            " JOIN hash_contexts hc2 ON hc2.id = p2.context_id"
            " JOIN contents_raw cr ON cr.hash_id = hc2.hash_id"
            " WHERE cr.content LIKE %s) any_reference", (f"%{_TOKEN}%",))
        reference = cur.fetchone()[0]
    if reference == 0:
        assert keyness["state"] == "not_measurable" \
            and keyness["reason"] == "no_reference"
    else:
        assert keyness["state"] == "measured"
    cov = sections["coverage@1"]
    assert cov["state"] == "measured"
    assert cov["measures"]["contents"] == 3
    # The listings cover the whole collection's keywords/categories, so on
    # a shared session database they can list more than this test's own
    # seed; the seeded keyword must be among them, widest.
    assert cov["measures"]["keyword_rows"] >= 1
    assert cov["measures"]["category_rows"] >= 1
    # The widest keyword is the seeded one: 2 of the 3 matched contents.
    top = [s for v in cov["narrative"]["voices"] if v["voice"] == "finding"
           for s in v["sentences"] if s["key"] == "finding.top_keyword"]
    assert top and top[0]["params"]["keyword_contents"] == 2
    assert top[0]["params"]["keyword_share"] == pytest.approx(66.67)


def test_comprehensive_run_is_deterministic(world):
    a, _ = _run(world)
    b, _ = _run(world)
    conn = world["conn"]
    ga = runs.get_run(conn, a["id"], user=world["analyst"])
    gb = runs.get_run(conn, b["id"], user=world["analyst"])
    for key in ("definition_fingerprint", "criteria_fingerprint"):
        assert ga[key] == gb[key], key
    assert [d["query_fingerprint"] for d in ga["datasets"]] == \
           [d["query_fingerprint"] for d in gb["datasets"]]
    sa, sb = _sections(conn, a["id"]), _sections(conn, b["id"])
    assert sa == sb


def test_a_listing_that_contradicts_the_overview_fails_the_run(world):
    overview = REGISTRY.dataset("comprehensive.overview@1")
    tampered = dataclasses.replace(
        overview, sql=overview.sql.replace("COUNT(*) FROM rpt_base",
                                           "COUNT(*) + 1 FROM rpt_base"))
    datasets = tuple(tampered if d.key == overview.key else d
                     for d in REGISTRY.datasets)
    variant = ReportRegistry(reports=REGISTRY.reports, datasets=datasets,
                             help_topics=REGISTRY.help_topics,
                             analyses=REGISTRY.analyses)
    run, out = _run(world, registry=variant)
    assert out["status"] == "failed", out
    conn = world["conn"]
    with conn.cursor() as cur:
        cur.execute("SELECT status, error FROM report_runs WHERE id = %s",
                    (run["id"],))
        status, error = cur.fetchone()
    assert status == "failed"
    assert "disagrees with the overview" in (error or "")


def test_comprehensive_over_http(app, world, sync_jobs):  # noqa: F811
    from tests.integration.test_reports_api import _login_new, _post
    client, _ = _login_new(app, "analyst")
    response = _post(client, "/api/reports/runs",
                     {"report_id": "comprehensive", "parameters": _params()})
    # 202 with the async JobManager; 200 when sync_jobs runs it inline.
    assert response.status_code in (200, 202), response.get_data(as_text=True)
    run_id = response.get_json()["run"]["id"]
    detail = client.get(f"/api/reports/runs/{run_id}")
    assert detail.status_code == 200
    body = detail.get_json()["run"]
    assert body["status"] == "completed"
    keys = [a["analysis_key"] for a in body.get("analyses", ())]
    assert keys == ["composition@1", "term_keyness@2", "coverage@1"]
    # The narrative renders in the caller's language, voices in order.
    comp = body["analyses"][0]
    assert [v["voice"] for v in comp["text"]] == ["measure", "finding",
                                                  "confidence", "consequence",
                                                  "caveat"]
    assert "Contents:" in comp["text"][0]["text"]
    # The reach section names the seeded keyword with its share.
    cov = body["analyses"][2]
    assert any("zkw" in v["text"] for v in cov["text"])
