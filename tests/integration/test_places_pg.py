"""Phase 2 integration: database gazetteer and place-mention signals.

Executed against PostgreSQL through migration 0019 (applied by the session
bootstrap), the real ingestion path, the real JobManager and the HTTP API.
"""

from __future__ import annotations

import copy
import uuid
from datetime import date

import psycopg2
import psycopg2.errors
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]

TEXT = (
    "Talks opened in Zagreb and moved to Tripoli.\n"
    "المحادثات في القاهرة ثم وبيروت.\n"
    "השיחות עברו ובירושלים.\n"
    "Sastanak u Rijeci. Turkey objected.\n"
)


@pytest.fixture(scope="module")
def tenant(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                    " CURRENT_DATE) RETURNING id", (f"geo_side_{_U}",))
        side_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id", (f"geo_src_{_U}",))
        source_id = cur.fetchone()[0]
    conn.close()
    return {"source_id": source_id, "side_id": side_id}


_N = iter(range(10_000))


def _store(tenant, raw_text=TEXT):
    from database.services.contents_db_service import ContentDBService

    marker = f"{_U}geo{next(_N)}"
    return ContentDBService().process_full_document(
        hash_value=f"{marker:0<64}"[:64], source_id=tenant["source_id"],
        side_id=tenant["side_id"], file_name=f"{marker}.txt", file_path=f"/tmp/geo/{marker}.txt",
        file_size=100, file_type="txt", file_status="Read", file_date=date(2026, 1, 1),
        content_words=["talks", marker], raw_text=raw_text, attempts=1)


def _rows(pg_db, sql, params=()):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


# --- gazetteer load --------------------------------------------------------

def test_migration_loaded_the_seed_and_the_fingerprint_describes_the_rows(pg_db):
    from services.geo.gazetteer import compute_fingerprint, current_load, load_seed_file

    seed = load_seed_file()
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            load = current_load(cur)
            assert load["loaded_by"] == "migration:0019"
            assert load["content_sha256"] == seed["content_sha256"]
            assert (load["place_count"], load["name_count"]) == (seed["place_count"],
                                                                 seed["name_count"])
            fingerprint, places, names = compute_fingerprint(cur)
            assert fingerprint == load["fingerprint"]
            cur.execute("SELECT count(*) FROM geo_places WHERE NOT retired")
            assert cur.fetchone()[0] == places == 223
            cur.execute("SELECT language, count(*) FROM geo_place_names GROUP BY 1 ORDER BY 1")
            assert {r[0] for r in cur.fetchall()} == {"ar", "en", "fa", "he", "hr"}
    finally:
        conn.close()


def test_sync_seed_is_idempotent_and_retires_instead_of_deleting(pg_db):
    from services.geo.gazetteer import current_detector_version, load_seed_file, sync_seed

    seed = load_seed_file()
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM geo_gazetteer_loads")
            loads = cur.fetchone()[0]
            again = sync_seed(cur, seed, loaded_by="test")
            assert again["changed"] is False
            assert again["places_added"] == again["places_updated"] == 0
            assert again["names_added"] == again["names_updated"] == again["names_removed"] == 0
            cur.execute("SELECT count(*) FROM geo_gazetteer_loads")
            assert cur.fetchone()[0] == loads, "an unchanged seed records no new load"
            before = current_detector_version(cur)

            edited = copy.deepcopy(seed)
            dropped = edited["places"].pop()
            edited["places"][0]["names"] = edited["places"][0]["names"][1:]
            import hashlib
            import json
            payload = json.dumps(edited["places"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"))
            edited["content_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
            changed = sync_seed(cur, edited, loaded_by="test")
            assert changed["changed"] and changed["places_retired"] == 1
            assert changed["names_removed"] == 1
            cur.execute("SELECT retired FROM geo_places WHERE place_key = %s",
                        (dropped["place_key"],))
            assert cur.fetchone() == (True,), "a place leaving the seed is retired, not deleted"
            assert current_detector_version(cur) != before, \
                "a gazetteer change must change the place detector version"
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("sql,params,constraint", [
    ("INSERT INTO geo_places (place_key, label, feature_type, source) VALUES"
     " ('wikidata:Qx1', 'X', 'village', 't')", (), "ck_geo_places_feature"),
    ("INSERT INTO geo_places (place_key, label, feature_type, source, country_codes) VALUES"
     " ('wikidata:Qx2', 'X', 'city', 't', '{hr}')", (), "ck_geo_places_countries"),
    ("INSERT INTO geo_places (place_key, label, feature_type, source, latitude, longitude)"
     " VALUES ('wikidata:Qx3', 'X', 'city', 't', 95, 10)", (), "ck_geo_places_lat"),
    ("INSERT INTO geo_places (place_key, label, feature_type, source, latitude) VALUES"
     " ('wikidata:Qx4', 'X', 'city', 't', 45)", (), "ck_geo_places_point_pair"),
    ("INSERT INTO geo_places (place_key, label, feature_type, source) VALUES"
     " ('no key', 'X', 'city', 't')", (), "ck_geo_places_key"),
    ("INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
     " source) SELECT id, 'x', 'x', 'he', 'Latn', 'exonym', 't' FROM geo_places LIMIT 1",
     (), "ck_geo_place_names_script"),
    ("INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
     " source) SELECT id, 'x', 'x', 'en', 'Latn', 'nickname', 't' FROM geo_places LIMIT 1",
     (), "ck_geo_place_names_type"),
    ("INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type,"
     " source) SELECT id, 'x', 'x', 'de', 'Latn', 'exonym', 't' FROM geo_places LIMIT 1",
     (), "ck_geo_place_names_language"),
])
def test_gazetteer_constraints_fire_by_name(pg_db, sql, params, constraint):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.CheckViolation) as info:
                cur.execute(sql, params)
            assert info.value.diag.constraint_name == constraint
    finally:
        conn.rollback()
        conn.close()


def test_duplicate_name_for_a_place_is_rejected(pg_db):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute("INSERT INTO geo_place_names (place_id, name, match_key, language,"
                            " script, name_type, source) SELECT place_id, name, match_key,"
                            " language, script, name_type, 'dup' FROM geo_place_names LIMIT 1")
    finally:
        conn.rollback()
        conn.close()


# --- ingestion ---------------------------------------------------------------

def test_ingestion_stores_place_signals_with_candidates_and_offsets(pg_db, tenant):
    from services.geo.gazetteer import current_detector_version

    result = _store(tenant)
    assert result["success"] and not result["warnings"], result
    assert result["place_signals"]["status"] == "complete"
    hash_id = result["hash_id"]
    stored = "".join(r[0] for r in _rows(
        pg_db, "SELECT content FROM contents_raw WHERE hash_id = %s ORDER BY chunk_seq",
        (hash_id,)))
    rows = _rows(pg_db, "SELECT s.id, s.value, s.surface, s.char_start, s.char_end,"
                        " s.resolution, s.confidence, s.method, s.language, s.detector_ver,"
                        " s.evidence_sentence, s.sentence_start, s.sentence_end"
                        " FROM content_signals s WHERE s.hash_id = %s AND s.detector = 'places'"
                        " ORDER BY s.char_start", (hash_id,))
    by_surface = {r[2]: r for r in rows}
    assert set(by_surface) == {"Zagreb", "Tripoli", "القاهرة", "بيروت", "ירושלים", "Rijeci",
                               "Turkey"}
    for r in rows:
        assert stored[r[3]:r[4]] == r[2], "offsets address the stored text"
        assert stored[r[11]:r[12]] == r[10] and r[11] <= r[3] < r[4] <= r[12]
    conn = connect(pg_db)
    with conn.cursor() as cur:
        version = current_detector_version(cur)
    conn.close()
    assert {r[9] for r in rows} == {version}
    assert by_surface["Tripoli"][5:7] == ("ambiguous", "low")
    assert by_surface["Turkey"][5:7] == ("identified", "low")
    assert by_surface["ירושלים"][7] == "he.prefix" and by_surface["Rijeci"][7] == "hr.inflection"
    candidates = _rows(pg_db, "SELECT s.surface, count(*) FROM content_signal_places csp"
                              " JOIN content_signals s ON s.id = csp.signal_id"
                              " WHERE s.hash_id = %s GROUP BY s.surface", (hash_id,))
    counts = dict(candidates)
    assert counts["Tripoli"] == 2 and all(v == 1 for k, v in counts.items() if k != "Tripoli")
    run = _rows(pg_db, "SELECT status, signal_count, detector_ver, trigger FROM"
                       " content_signal_runs WHERE hash_id = %s AND detector = 'places'",
                (hash_id,))
    assert run == [("complete", len(rows), version, "ingestion")]


def test_place_signal_constraints_and_provenance_protection(pg_db, tenant):
    hash_id = _store(tenant)["hash_id"]
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            # 'identified' is reserved for place mentions (no dates, so only
            # the new CHECK can fire here).
            with pytest.raises(psycopg2.errors.CheckViolation) as info:
                cur.execute("INSERT INTO content_signals (hash_id, detector, detector_ver,"
                            " signal_type, value, surface, char_start, char_end, resolution,"
                            " evidence, dedup_key) VALUES (%s, 'temporal', 'temporal-1.1.0',"
                            " 'date_reference', 'v', 'x', 0, 1, 'identified', '{}', %s)",
                            (hash_id, "e" * 64))
            assert info.value.diag.constraint_name == "ck_content_signals_identified_place"
        conn.rollback()
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                cur.execute("DELETE FROM geo_places WHERE place_key = 'wikidata:Q1435'")
        conn.rollback()
        with conn.cursor() as cur:  # deleting content cascades to its candidates
            cur.execute("SELECT count(*) FROM content_signal_places csp JOIN content_signals s"
                        " ON s.id = csp.signal_id WHERE s.hash_id = %s", (hash_id,))
            assert cur.fetchone()[0] > 0
            cur.execute("DELETE FROM content_signals WHERE hash_id = %s", (hash_id,))
            cur.execute("SELECT count(*) FROM content_signal_places csp WHERE NOT EXISTS"
                        " (SELECT 1 FROM content_signals s WHERE s.id = csp.signal_id)")
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback()
        conn.close()


def test_a_place_detector_failure_is_recorded_and_does_not_block_temporal(
        pg_db, tenant, monkeypatch):
    from services.geo import gazetteer

    monkeypatch.setattr(gazetteer, "current_load", lambda cur: None)
    result = _store(tenant, raw_text="Meeting in Zagreb on 5 October 2026.")
    assert result["success"]
    assert result["signals"]["status"] == "complete"
    assert result["place_signals"] == {"status": "failed"}
    assert any("place_signals" in w for w in result["warnings"])
    run = _rows(pg_db, "SELECT status, error, detector_ver FROM content_signal_runs"
                       " WHERE hash_id = %s AND detector = 'places'", (result["hash_id"],))
    assert run[0][0] == "failed" and "GazetteerUnavailable" in run[0][1]
    assert run[0][2] == "places-1.0.0", "a run that could not load a gazetteer stays stale"


# --- legacy view ---------------------------------------------------------------

def test_path_geo_mentions_view_serves_identified_places_and_legacy_rows(pg_db, tenant):
    from Api.utils.utils import get_connection
    from services.detection.redetection import run_redetection

    fresh = _store(tenant)["hash_id"]
    view = _rows(pg_db, "SELECT place_name, country, mention_count, provenance, place_key"
                        " FROM path_geo_mentions WHERE hash_id = %s ORDER BY place_name",
                 (fresh,))
    names = [r[0] for r in view]
    assert "Zagreb" in names and "Tripoli" not in names, "ambiguous mentions are not in the view"
    assert "Turkey" not in names, "low-confidence (homograph) mentions are not in the view"
    zagreb = [r for r in view if r[0] == "Zagreb"][0]
    assert zagreb[1] == "Croatia" and zagreb[3] == "signals" and zagreb[4] == "wikidata:Q1435"

    # A pre-0019 row for content the place detector has not analysed shows through...
    legacy = _store(tenant, raw_text="nothing to see")["hash_id"]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM content_signal_runs WHERE hash_id = %s AND detector = 'places'",
                    (legacy,))
        cur.execute("INSERT INTO path_geo_mentions_m0015 (hash_id, place_name, country, latitude,"
                    " longitude, mention_count) VALUES (%s, 'Lagos', 'Nigeria', 6.5, 3.4, 2)",
                    (legacy,))
    conn.close()
    assert _rows(pg_db, "SELECT place_name, provenance FROM path_geo_mentions WHERE hash_id = %s",
                 (legacy,)) == [("Lagos", "legacy_m0015")]
    # ...and is superseded once the detector has run on that content.
    run_redetection(get_connection, scope="hash_ids", hash_ids=[legacy], detectors=["places"])
    assert _rows(pg_db, "SELECT count(*) FROM path_geo_mentions WHERE hash_id = %s",
                 (legacy,)) == [(0,)]


def test_stale_places_are_redetected_when_the_gazetteer_version_changes(pg_db, tenant):
    from Api.utils.utils import get_connection
    from services.detection.redetection import run_redetection

    hash_id = _store(tenant)["hash_id"]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE content_signal_runs SET detector_ver = 'places-1.0.0+g000000000000'"
                    " WHERE hash_id = %s AND detector = 'places'", (hash_id,))
    conn.close()
    result = run_redetection(get_connection, scope="stale", detectors=["places"])
    assert result.stats["by_detector"]["places"]["runs"] >= 1
    rows = _rows(pg_db, "SELECT detector_ver, trigger FROM content_signal_runs"
                        " WHERE hash_id = %s AND detector = 'places'", (hash_id,))
    assert rows[0][0] == result.versions["places"] and rows[0][1] == "redetection"
    # The database is shared with other test modules, whose content may never
    # have had a temporal run: a stale pass over all detectors may process
    # those - but must not re-run places anywhere and must not fail.
    again = run_redetection(get_connection, scope="stale")
    assert again.stats["by_detector"]["places"]["runs"] == 0
    assert again.stats["failed"] == 0
    final = run_redetection(get_connection, scope="stale")
    assert final.stats["processed"] == 0, "stale selection converges across detectors"


# --- API -------------------------------------------------------------------------

def test_signals_api_returns_place_candidates_and_validates_detector(pg_db, tenant,
                                                                     client_factory, client):
    hash_id = _store(tenant)["hash_id"]
    viewer = client_factory("viewer")
    body = viewer.get(f"/api/content/{hash_id}/signals?detector=places").get_json()
    assert body["success"] and {s["detector"] for s in body["signals"]} == {"places"}
    assert body["runs"]["places"]["status"] == "complete"
    assert body["runs"]["places"]["current_version"] is True
    assert "temporal" not in body["runs"]
    tripoli = [s for s in body["signals"] if s["surface"] == "Tripoli"][0]
    assert {p["place_key"] for p in tripoli["places"]} == {"wikidata:Q3579", "wikidata:Q168954"}
    both = viewer.get(f"/api/content/{hash_id}/signals").get_json()
    detectors = {s["detector"] for s in both["signals"]}
    assert "places" in detectors and detectors <= {"temporal", "places"}
    assert set(both["runs"]) == {"temporal", "places"}
    assert viewer.get(f"/api/content/{hash_id}/signals?detector=ner").status_code == 400
    assert client.get(f"/api/content/{hash_id}/signals?detector=places").status_code in (401, 302)


def test_redetect_api_accepts_a_detector_list(pg_db, tenant, client_factory, admin_client,
                                              monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    hash_id = _store(tenant)["hash_id"]

    def post(c, payload):
        return c.post("/api/signals/redetect", json=payload, headers={"X-CSRFToken": _csrf(c)})

    for bad in ({"scope": "stale", "detectors": ["ner"]}, {"scope": "stale", "detectors": []},
                {"scope": "stale", "detectors": "places"}):
        assert post(admin_client, bad).status_code == 400, bad
    assert post(client_factory("viewer"), {"scope": "stale", "detectors": ["places"]}
                ).status_code == 403
    before = _rows(pg_db, "SELECT ran_at FROM content_signal_runs WHERE hash_id = %s"
                          " AND detector = 'temporal'", (hash_id,))
    resp = post(admin_client, {"scope": "hash_ids", "hash_ids": [hash_id],
                               "detectors": ["places"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    job = resp.get_json()["job"]
    assert job["status"] == "COMPLETED"
    assert job["result_summary"]["detectors"] == ["places"]
    assert _rows(pg_db, "SELECT trigger, job_id FROM content_signal_runs WHERE hash_id = %s"
                        " AND detector = 'places'", (hash_id,)) == [("redetection", job["job_id"])]
    assert _rows(pg_db, "SELECT ran_at FROM content_signal_runs WHERE hash_id = %s"
                        " AND detector = 'temporal'", (hash_id,)) == before, \
        "only the requested detector ran"


def test_legacy_scan_endpoint_delegates_and_never_writes_coordinates(
        pg_db, tenant, client_factory, monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    result = _store(tenant)
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE paths SET coordinates = '45.000000, 16.000000' WHERE id = %s",
                    (result["path_id"],))
    conn.close()
    analyst = client_factory("analyst")
    resp = analyst.post("/api/file-analysis/geolocation/scan?force=true",
                        headers={"X-CSRFToken": _csrf(analyst)})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["summary"]["coordinates_written"] == 0
    assert body["summary"]["hashes_scanned"] >= 1 and body["job"]["status"] == "COMPLETED"
    assert _rows(pg_db, "SELECT coordinates FROM paths WHERE id = %s",
                 (result["path_id"],)) == [("45.000000, 16.000000",)], \
        "GPS metadata in paths.coordinates must survive a place scan"


def test_places_api_search_detail_and_validation(pg_db, tenant, client_factory, client):
    hash_id = _store(tenant)["hash_id"]
    viewer = client_factory("viewer")
    status = viewer.get("/api/gazetteer").get_json()
    assert status["loaded"] and status["detector_ver"].startswith("places-1.0.0+g")

    found = viewer.get("/api/places?q=zag").get_json()
    assert [p["place_key"] for p in found["data"]] == ["wikidata:Q1435"]
    arabic = viewer.get("/api/places?q=القاه&language=ar").get_json()
    assert [p["place_key"] for p in arabic["data"]] == ["wikidata:Q85"]
    croatian = viewer.get("/api/places?country=HR&feature_type=city&per_page=5").get_json()
    assert croatian["pagination"]["total"] >= 9 and len(croatian["data"]) == 5
    labels = [p["label"] for p in croatian["data"]]
    assert labels == sorted(labels), "stable ordering"

    detail = viewer.get("/api/places/wikidata:Q1435").get_json()["place"]
    assert detail["label"] == "Zagreb" and detail["mentions"]["identified"]["contents"] >= 1
    assert {n["language"] for n in detail["names"]} == {"en", "ar", "he", "fa", "hr"}
    tripoli = viewer.get("/api/places/wikidata:Q3579").get_json()["place"]["mentions"]
    assert tripoli["identified"]["mentions"] == 0 and tripoli["ambiguous_candidate"]["mentions"] >= 1

    for bad in ("/api/places?language=de", "/api/places?feature_type=village",
                "/api/places?country=HRV", "/api/places?per_page=0", "/api/places?page=x"):
        assert viewer.get(bad).status_code == 400, bad
    assert viewer.get("/api/places/wikidata:Q0").status_code == 404
    assert client.get("/api/places").status_code in (401, 302)
    assert hash_id
