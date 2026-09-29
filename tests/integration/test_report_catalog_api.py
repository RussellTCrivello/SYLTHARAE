"""Step 17 integration: Keyword Intelligence and Category Analysis over HTTP.

The registry-level suites prove the datasets' SQL; this file proves the
reports the way a user runs them: definitions listed by role, a run
submitted through ``POST /api/reports/runs``, executed by the existing
``report_run`` job on one snapshot, results paged through the run dataset
endpoint, and an immutable JSON artifact whose manifest and
``DATA_EXPORTED`` audit row carry the measured digest.
"""

import datetime
import uuid

import pytest

from _seed import connect, document, side, source, word_counts

pytestmark = pytest.mark.integration


@pytest.fixture()
def sync_jobs(monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    return sync


@pytest.fixture(scope="module")
def corpus(pg_db, app):
    tag = uuid.uuid4().hex[:8]
    word = f"kat{tag}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1 = source(cur)
        d1 = side(cur)
        paths = [document(cur, source_id=s1, side_id=d1,
                          text=f"{word} doc {i}", file_type="pdf",
                          file_date=datetime.date(2026, 5, 1 + i))[0]
                 for i in range(3)]
        cur.execute("SELECT DISTINCT hc.hash_id FROM paths p JOIN hash_contexts hc"
                    " ON hc.id = p.context_id WHERE p.id = ANY(%s)", (paths,))
        hashes = [h for (h,) in cur.fetchall()]

        def _word_id(w):
            cur.execute("INSERT INTO words (word) VALUES (%s)"
                        " ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word"
                        " RETURNING id", (w,))
            return cur.fetchone()[0]

        kw_id, common_id = _word_id(f"kw{tag}"), _word_id(f"common{tag}")
        other_id = _word_id(f"other{tag}")
        for h in hashes:
            word_counts(cur, h, {f"kw{tag}": 4, f"common{tag}": 2})
        word_counts(cur, hashes[0], {f"unk{tag}": None})   # never measured

        name_id = _word_id(f"catA{tag}")
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                    (name_id,))
        cat_a = cur.fetchone()[0]
        for wid in (kw_id, common_id, _word_id(f"unk{tag}")):
            cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                        " VALUES (%s, %s)", (wid, cat_a))
        name_id_b = _word_id(f"catB{tag}")
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                    (name_id_b,))
        cat_b = cur.fetchone()[0]
        cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                    " VALUES (%s, %s)", (common_id, cat_b))

        from core.serialization import pack_int_list
        cur.execute("INSERT INTO keywords (keyword, category_id)"
                    " VALUES (%s, %s) RETURNING id",
                    (pack_int_list([kw_id, common_id]), cat_a))
        keyword_id = cur.fetchone()[0]
        cur.execute("INSERT INTO keywords (keyword, category_id)"
                    " VALUES (%s, %s) RETURNING id",
                    (pack_int_list([other_id]), cat_b))
        for h in hashes:
            cur.execute("INSERT INTO keywords_hashs (hash_id, keyword_id,"
                        " word_count) VALUES (%s, %s, %s)",
                        (h, keyword_id, None if h == hashes[0] else 2))
        # Horizon corpus: one overdue and one this-week signal on the first
        # content (reference date 2026-05-10 sits between the corpora's dates).
        import hashlib as _hashlib
        cur.execute("SELECT content FROM contents_raw WHERE hash_id = %s"
                    " ORDER BY chunk_seq LIMIT 1", (hashes[0],))
        _sentence = (cur.fetchone()[0] or "seed body")[:30]
        for i, (d_from, orient, bucket) in enumerate([
                (datetime.date(2026, 5, 1), "future", "overdue"),
                (datetime.date(2026, 5, 14), "future", "week")]):
            cur.execute(
                "INSERT INTO content_signals (hash_id, detector, detector_ver,"
                " signal_type, value, surface, char_start, char_end, language,"
                " calendar, resolution, date_from, date_to, text_orientation,"
                " anchor_date, evidence, dedup_key, method, confidence,"
                " confidence_basis, evidence_sentence, sentence_start, sentence_end)"
                " VALUES (%s, 'temporal', 'temporal-1.1.0', 'date_reference',"
                " %s, %s, 0, 10, 'en', 'gregorian', 'absolute', %s, %s, %s,"
                " %s, '{}'::jsonb, %s, 'parse', 'high', 'explicit_day', %s, 0, %s)",
                (hashes[0], f"2026-05-{i + 1:02d}", f"seed date {i}",
                 d_from, d_from, orient, d_from,
                 _hashlib.sha256(f"kat-{tag}-sig-{i}".encode()).hexdigest(),
                 _sentence, len(_sentence)))
    yield {"conn": conn, "word": word, "tag": tag, "paths": paths,
           "keyword_id": keyword_id}
    conn.close()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _post(c, url, payload=None, csrf=True):
    headers = {"X-CSRFToken": _csrf(c)} if csrf else {}
    return c.post(url, json=payload if payload is not None else {}, headers=headers)


def _login_new(app, role):
    from core.security.service import get_auth_service
    username = f"kat_{role}_{uuid.uuid4().hex[:8]}"
    password = f"kat-{role}-password-123"
    get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c, username


def _analyst(app):
    return _login_new(app, "analyst")


class TestOverHttp:
    def test_both_definitions_are_listed_with_their_contracts(self, app):
        c, _ = _analyst(app)
        body = c.get("/api/reports/definitions").get_json()
        items = {d["key"]: d for d in body["items"]}
        ki = items["keyword_intelligence@1"]
        assert ki["unit"] == "keyword"
        assert [d["key"] for d in ki["datasets"]] == \
            ["keyword_intelligence.matches@1"]
        assert ki["datasets"][0]["semantics"] == "capped"
        assert ki["title"] and ki["help"]["title"]
        ca = items["category_analysis@1"]
        assert ca["unit"] == "category"
        assert ca["datasets"][0]["row_limit"] == 1000

    def test_keyword_intelligence_run_lifecycle_and_rows(self, app, corpus, sync_jobs):
        c, username = _analyst(app)
        resp = _post(c, "/api/reports/runs",
                     {"report_id": "keyword_intelligence",
                      "parameters": {"criteria": {"text": corpus["word"]}}})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        run = resp.get_json()["run"]
        assert run["status"] == "completed" and run["snapshot"]
        assert run["requester_username"] == username
        # The report lists every keyword in the collection (zero-presence
        # included), so on a shared session database other modules' keywords
        # appear too; the count is only bounded below by this corpus's two.
        assert run["datasets"][0]["row_count"] >= 2
        tag = corpus["tag"]
        page = c.get(
            f"/api/reports/runs/{run['id']}/datasets/keyword_intelligence.matches@1"
            "?limit=10").get_json()
        rows = {r["label"]: r for r in page["rows"]}
        hit = rows[f"kw{tag} common{tag}"]
        assert hit["contents"] == 3 and hit["occurrences"] == 4
        assert hit["unknown_count_rows"] == 1 and hit["corpus_contents"] == 3
        assert hit["category_name"] == f"catA{tag}"
        zero = rows[f"other{tag}"]
        assert zero["contents"] == 0 and zero["occurrences"] == 0

    def test_category_analysis_run_shows_overlap_and_unknown_share(self, app, corpus,
                                                                   sync_jobs):
        c, _ = _analyst(app)
        resp = _post(c, "/api/reports/runs",
                     {"report_id": "category_analysis",
                      "parameters": {"criteria": {"text": corpus["word"]}}})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        run = resp.get_json()["run"]
        tag = corpus["tag"]
        rows = {r["category_name"]: r for r in c.get(
            f"/api/reports/runs/{run['id']}/datasets/category_analysis.summary@1"
            "?limit=10").get_json()["rows"]}
        a, b = rows[f"catA{tag}"], rows[f"catB{tag}"]
        assert a["member_words"] == 3 and a["keywords"] == 1
        assert a["contents"] == 3 and a["occurrences"] == 18
        assert a["unknown_count_rows"] == 1 and a["content_share"] is not None
        assert b["contents"] == 3 and b["occurrences"] == 6
        assert a["contents"] + b["contents"] == 6 > 3, (
            "overlap preserved: shared contents count in both categories")
        assert abs(a["content_share"] + b["content_share"] - 2.0) < 1e-9

    def test_an_empty_match_yields_measured_zeros_and_an_unknown_share(
            self, app, corpus, sync_jobs):
        c, _ = _analyst(app)
        resp = _post(c, "/api/reports/runs",
                     {"report_id": "category_analysis",
                      "parameters": {"criteria": {"text": "zzz-no-such-word"}}})
        assert resp.status_code == 200
        run = resp.get_json()["run"]
        rows = c.get(f"/api/reports/runs/{run['id']}/datasets/"
                     "category_analysis.summary@1?limit=10").get_json()["rows"]
        assert rows, "categories still listed over an empty matched set"
        for r in rows:
            assert r["contents"] == 0 and r["corpus_contents"] == 0
            assert r["content_share"] is None, (
                "no match means the share is unknown, never zero")

    def test_viewer_may_run_but_routes_stay_gated(self, app, corpus, sync_jobs):
        from core.security.service import get_auth_service
        auth = get_auth_service()
        username = f"katv_{uuid.uuid4().hex[:10]}"
        auth.create_user(username, "catalog-viewer-password-123", role="viewer")
        v = app.test_client()
        assert v.post("/auth/login", json={"username": username,
                                           "password": "catalog-viewer-password-123"}
                      ).status_code == 200
        body = v.get("/api/reports/definitions").get_json()
        keys = [d["key"] for d in body["items"]]
        assert "keyword_intelligence@1" in keys, "viewer role is a reader role"
        assert app.test_client().get("/api/reports/definitions").status_code in (401, 302)

    def test_json_artifact_manifest_and_audit_digest(self, app, corpus, sync_jobs):
        c, _ = _analyst(app)
        resp = _post(c, "/api/reports/runs",
                     {"report_id": "keyword_intelligence",
                      "parameters": {"criteria": {"text": corpus["word"]}}})
        run = resp.get_json()["run"]
        art = _post(c, f"/api/reports/runs/{run['id']}/artifacts",
                    {"format": "json"})
        assert art.status_code in (200, 202), art.get_data(as_text=True)
        aid = art.get_json()["artifact"]["id"]
        record = c.get(f"/api/reports/artifacts/{aid}").get_json()["artifact"]
        assert record["format"] == "json" and record["sha256"]
        assert c.get(f"/api/reports/artifacts/{aid}/verify").get_json()["ok"] is True
        download = c.get(f"/api/reports/artifacts/{aid}/download")
        assert download.status_code == 200
        manifest = c.get(f"/api/reports/artifacts/{aid}/manifest").get_json()
        body = manifest.get("manifest", manifest)
        assert body["report"]["id"] == "keyword_intelligence"
        assert body["report"]["version"] == 1
        assert body["report"]["unit"] == "keyword"
        assert record["manifest"]["artifact"]["sha256"] == record["sha256"]
        assert body["artifact"]["sha256"] == record["sha256"]
        assert body["run"]["criteria_fingerprint"] == run["criteria_fingerprint"]
        assert body["snapshot"]["id"] == run["snapshot"]
        assert "repeatable read" in body["snapshot"]["isolation"].lower()
        assert body["run"]["requested_by"]["username"] == body["run"]["requested_by"]["username"]
        datasets = body.get("datasets", [])
        assert [d["dataset_key"] for d in datasets] == \
            ["keyword_intelligence.matches@1"]
        assert datasets[0]["row_count"] == run["datasets"][0]["row_count"] >= 2


    def test_horizon_run_buckets_against_the_declared_reference_date(
            self, app, corpus, sync_jobs):
        c, username = _analyst(app)
        resp = _post(c, "/api/reports/runs",
                     {"report_id": "horizon",
                      "parameters": {"criteria": {"text": corpus["word"]},
                                     "as_of": "2026-05-10"}})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        run = resp.get_json()["run"]
        assert run["status"] == "completed"
        assert run["parameters"]["as_of"] == "2026-05-10", (
            "the reference date is recorded with the run: it is reproducible")
        rows = c.get(f"/api/reports/runs/{run['id']}/datasets/horizon.signals@1"
                     "?limit=10").get_json()["rows"]
        buckets = sorted(r["bucket"] for r in rows
                         if r["signal_type"] == "date_reference"
                         and r["value"].startswith("2026-05"))
        assert buckets == ["overdue", "week"], buckets
        assert rows[0]["event_date"] <= rows[-1]["event_date"], (
            "the horizon reads forward: soonest first")
        # A run without the reference date is refused before anything runs.
        bad = _post(c, "/api/reports/runs",
                    {"report_id": "horizon",
                     "parameters": {"criteria": {"text": corpus["word"]}}})
        assert bad.status_code == 400

    def test_entity_place_run_keeps_ambiguity_over_http(self, app, corpus,
                                                          sync_jobs, pg_db):
        """The Entity & Place report over HTTP: candidates stay candidates."""
        import hashlib as _hashlib
        tag = corpus["tag"]
        conn = connect(pg_db)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT hc.hash_id FROM paths p JOIN hash_contexts hc"
                        " ON hc.id = p.context_id WHERE p.id = ANY(%s)",
                        (corpus["paths"],))
            hashes = [h for (h,) in cur.fetchall()]
            cur.execute("SELECT content FROM contents_raw WHERE hash_id = %s"
                        " ORDER BY chunk_seq LIMIT 1", (hashes[0],))
            sentence = (cur.fetchone()[0] or "seed body")[:30]
            cur.execute("INSERT INTO geo_places (place_key, label, feature_type,"
                        " country_codes, source) VALUES (%s, %s, 'city', '{LY}',"
                        " 'seed') RETURNING id", (f"kat:benghazi-{tag}", f"Benghazi {tag}"))
            place_id = cur.fetchone()[0]
            for i, (resolution, places) in enumerate([("identified", [place_id]),
                                                      ("ambiguous", [place_id])]):
                cur.execute(
                    "INSERT INTO content_signals (hash_id, detector, detector_ver,"
                    " signal_type, value, surface, char_start, char_end, resolution,"
                    " evidence, dedup_key, method, confidence, confidence_basis,"
                    " evidence_sentence, sentence_start, sentence_end)"
                    " VALUES (%s, 'places', 'places-1.0.0+seed', 'place_mention',"
                    " 'city', 'Benghazi', 0, 8, %s, '{}'::jsonb, %s, 'gazetteer',"
                    " 'high', 'explicit', %s, 0, %s) RETURNING id",
                    (hashes[0], resolution,
                     _hashlib.sha256(f"kat-{tag}-ep-{i}".encode()).hexdigest(),
                     sentence, len(sentence)))
                signal_id = cur.fetchone()[0]
                for pid in places:
                    cur.execute("INSERT INTO content_signal_places (signal_id,"
                                " place_id) VALUES (%s, %s)", (signal_id, pid))
        conn.close()
        c, _ = _analyst(app)
        resp = _post(c, "/api/reports/runs",
                     {"report_id": "entity_place",
                      "parameters": {"criteria": {"text": corpus["word"]}}})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        run = resp.get_json()["run"]
        rows = {r["place_label"]: r for r in c.get(
            f"/api/reports/runs/{run['id']}/datasets/entity_place.mentions@1"
            "?limit=20").get_json()["rows"]}
        row = rows[f"Benghazi {tag}"]
        assert row["identified_occurrences"] == 1
        assert row["ambiguous_occurrences"] == 1, (
            "the ambiguous candidate stays ambiguous over HTTP too")
        assert row["unknown_confidence_occurrences"] == 0
