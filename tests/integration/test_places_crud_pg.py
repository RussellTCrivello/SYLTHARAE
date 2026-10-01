"""Phase 2 / manageability integration: CRUD operations on geo_places.

Verifies admin-managed place creation, editing, retirement, and deletion,
strict role authorization (viewer/analyst 403, admin only), audit logging,
validation, and searchability of user-managed places.
"""

from __future__ import annotations

import uuid
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def clean_user_places(pg_db):
    yield
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM geo_place_names WHERE source = 'user'")
        cur.execute("DELETE FROM geo_places WHERE source = 'user'")
        # Curation tests append immutable revisions. Remove test-only curation
        # rows after restoring the canonical seed so other migration tests
        # still observe the migration's initial history row.
        cur.execute("DELETE FROM geo_gazetteer_loads WHERE stats->>'kind' = 'admin_curation'")
    conn.close()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _audit_detail(pg_db, action, resource):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT detail FROM audit_log WHERE action = %s AND resource = %s"
                        " ORDER BY id DESC LIMIT 1", (action, resource))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def test_places_crud_authorization(client, client_factory):
    """Anonymous gets 401/302, viewer/analyst gets 403, admin allowed."""
    viewer = client_factory("viewer")
    analyst = client_factory("analyst")

    payload = {"label": "Auth Test City", "feature_type": "city", "country_codes": ["NL"]}

    # Anonymous
    assert client.post("/api/places", json=payload).status_code in (401, 302)
    assert client.put("/api/places/wikidata:Q1435", json={"label": "New"}).status_code in (401, 302)
    assert client.delete("/api/places/wikidata:Q1435").status_code in (401, 302)

    # Viewer
    v_csrf = _csrf(viewer)
    assert viewer.post("/api/places", json=payload, headers={"X-CSRFToken": v_csrf}).status_code == 403
    assert viewer.put("/api/places/wikidata:Q1435", json={"label": "New"}, headers={"X-CSRFToken": v_csrf}).status_code == 403
    assert viewer.delete("/api/places/wikidata:Q1435", headers={"X-CSRFToken": v_csrf}).status_code == 403

    # Analyst
    a_csrf = _csrf(analyst)
    assert analyst.post("/api/places", json=payload, headers={"X-CSRFToken": a_csrf}).status_code == 403
    assert analyst.put("/api/places/wikidata:Q1435", json={"label": "New"}, headers={"X-CSRFToken": a_csrf}).status_code == 403
    assert analyst.delete("/api/places/wikidata:Q1435", headers={"X-CSRFToken": a_csrf}).status_code == 403


def test_places_create_and_read_back(client_factory, pg_db):
    """Admin creates a custom place with multilingual names and reads it back."""
    admin = client_factory("admin")
    csrf = _csrf(admin)

    place_key = f"user:custom_city_{_U}"
    payload = {
        "place_key": place_key,
        "label": f"New Metropolis {_U}",
        "feature_type": "city",
        "country_codes": ["NL"],
        "latitude": 52.37,
        "longitude": 4.89,
        "names": [
            {"name": f"New Metropolis {_U}", "language": "en", "name_type": "endonym"},
            {"name": f"Nieuwe Metropool {_U}", "language": "hr", "name_type": "exonym"},
        ],
    }

    resp = admin.post("/api/places", json=payload, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["place"]["place_key"] == place_key
    assert body["place"]["source"] == "user"
    assert body["place"]["latitude"] == 52.37
    place_audit = _audit_detail(pg_db, "places.create", f"place:{place_key}")
    assert place_audit and place_audit["label"] == f"New Metropolis {_U}"
    assert place_audit["target_type"] == "place" and place_audit["target_id"] == place_key

    # Read back via individual GET
    get_resp = admin.get(f"/api/places/{place_key}")
    assert get_resp.status_code == 200
    p = get_resp.get_json()["place"]
    assert p["label"] == f"New Metropolis {_U}"
    assert p["feature_type"] == "city"
    assert p["country_codes"] == ["NL"]
    assert len(p["names"]) >= 2

    # Search via list endpoint
    search_resp = admin.get(f"/api/places?q={place_key[:12]}")
    assert search_resp.status_code == 200
    items = search_resp.get_json()["data"]
    # Should find by key or label
    all_resp = admin.get(f"/api/places?country=NL&feature_type=city")
    assert any(x["place_key"] == place_key for x in all_resp.get_json()["data"])


def test_places_update_and_retire(client_factory):
    """Admin updates place coordinates and can toggle retirement."""
    admin = client_factory("admin")
    csrf = _csrf(admin)

    place_key = f"user:update_test_{_U}"
    resp = admin.post("/api/places", json={
        "place_key": place_key,
        "label": f"Update City {_U}",
        "feature_type": "city",
        "country_codes": ["US"],
        "latitude": 40.71,
        "longitude": -74.00,
    }, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201

    # Update coordinates and label
    up_resp = admin.put(f"/api/places/{place_key}", json={
        "label": f"Updated City {_U}",
        "latitude": 40.75,
        "longitude": -73.98,
    }, headers={"X-CSRFToken": csrf})
    assert up_resp.status_code == 200
    updated = up_resp.get_json()["place"]
    assert updated["label"] == f"Updated City {_U}"
    assert updated["latitude"] == 40.75

    # Retire the place
    retire_resp = admin.put(f"/api/places/{place_key}", json={
        "retired": True,
    }, headers={"X-CSRFToken": csrf})
    assert retire_resp.status_code == 200
    assert retire_resp.get_json()["place"]["retired"] is True

    # Retired place should be excluded from active search
    list_active = admin.get("/api/places?status=active").get_json()["data"]
    assert not any(x["place_key"] == place_key for x in list_active)

    # But included when asking for retired
    list_retired = admin.get("/api/places?status=retired").get_json()["data"]
    assert any(x["place_key"] == place_key for x in list_retired)

    # Reactivate
    react_resp = admin.put(f"/api/places/{place_key}", json={
        "retired": False,
    }, headers={"X-CSRFToken": csrf})
    assert react_resp.status_code == 200
    assert react_resp.get_json()["place"]["retired"] is False


def test_places_delete(client_factory):
    """Admin can delete a user-created place without signals."""
    admin = client_factory("admin")
    csrf = _csrf(admin)

    place_key = f"user:delete_test_{_U}"
    resp = admin.post("/api/places", json={
        "place_key": place_key,
        "label": f"Delete City {_U}",
        "feature_type": "city",
        "country_codes": ["FR"],
    }, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201

    del_resp = admin.delete(f"/api/places/{place_key}", headers={"X-CSRFToken": csrf})
    assert del_resp.status_code == 200
    assert del_resp.get_json()["success"] is True

    # Verify 404 after deletion
    assert admin.get(f"/api/places/{place_key}").status_code == 404

    # Deleting non-existent place is 404
    assert admin.delete("/api/places/nonexistent:key", headers={"X-CSRFToken": csrf}).status_code == 404


def test_places_create_validation(client_factory):
    """Invalid parameters return 400 with a clear error message."""
    admin = client_factory("admin")
    csrf = _csrf(admin)

    # Missing label
    assert admin.post("/api/places", json={"feature_type": "city"}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid feature type
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "galaxy"}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid latitude
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "city", "latitude": 120.0}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid longitude
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "city", "longitude": -200.0}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid country code
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "city", "country_codes": ["XYZ"]}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Duplicate place_key -> 409
    p1 = admin.post("/api/places", json={"place_key": f"user:dup_{_U}", "label": "Dup 1", "feature_type": "city"}, headers={"X-CSRFToken": csrf})
    assert p1.status_code == 201
    p2 = admin.post("/api/places", json={"place_key": f"user:dup_{_U}", "label": "Dup 2", "feature_type": "city"}, headers={"X-CSRFToken": csrf})
    assert p2.status_code == 409


def test_place_names_and_immutable_load_history_are_browsable(client_factory):
    """Authenticated readers can browse names and inspect immutable revisions."""
    viewer = client_factory("viewer")
    names = viewer.get("/api/place-names?place_key=wikidata:Q1435&source=seed&per_page=5")
    assert names.status_code == 200, names.get_data(as_text=True)
    name_rows = names.get_json()["data"]
    assert name_rows and all(row["place_key"] == "wikidata:Q1435" for row in name_rows)
    assert all(row["editable"] is False for row in name_rows)

    history = viewer.get("/api/gazetteer/loads?per_page=5")
    assert history.status_code == 200, history.get_data(as_text=True)
    body = history.get_json()
    assert body["immutable"] is True
    assert body["data"]
    detail = viewer.get(f"/api/gazetteer/loads/{body['data'][0]['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["load"]["immutable"] is True

    # No mutation endpoint exists for append-only load records (with valid CSRF).
    admin = client_factory("admin")
    assert admin.delete(f"/api/gazetteer/loads/{body['data'][0]['id']}",
                        headers={"X-CSRFToken": _csrf(admin)}).status_code == 405


def test_admin_curates_place_names_with_fingerprint_revision_and_audit(client_factory, pg_db):
    """Curated aliases are searchable, auditable, versioned, and seed aliases stay protected."""
    from services.geo.gazetteer import current_detector_version

    admin = client_factory("admin")
    viewer = client_factory("viewer")
    csrf = _csrf(admin)
    before_status = admin.get("/api/gazetteer").get_json()
    before_version = before_status["detector_ver"]

    place_key = "wikidata:Q1435"
    seed_names = admin.get(f"/api/place-names?place_key={place_key}&source=seed&per_page=1").get_json()["data"]
    seed_id = seed_names[0]["id"]
    assert viewer.put(f"/api/place-names/{seed_id}", json={"name": "Not allowed"},
                      headers={"X-CSRFToken": _csrf(viewer)}).status_code == 403
    assert admin.put(f"/api/place-names/{seed_id}", json={"name": "Changed seed alias"},
                     headers={"X-CSRFToken": csrf}).status_code == 403
    assert admin.put(f"/api/places/{place_key}", json={"label": "Changed seed label"},
                     headers={"X-CSRFToken": csrf}).status_code == 403

    alias = f"Zagreb Curated {_U}"
    created = admin.post("/api/place-names", json={
        "place_key": place_key,
        "name": alias,
        "language": "en",
        "name_type": "variant",
        "homograph": True,
        "note": "curated integration test alias",
    }, headers={"X-CSRFToken": csrf})
    assert created.status_code == 201, created.get_data(as_text=True)
    name_id = created.get_json()["id"]
    assert created.get_json()["gazetteer_revision"]["changed"] is True
    create_audit = _audit_detail(pg_db, "places.name.create", f"place_name:{name_id}")
    assert create_audit and create_audit["name"] == alias
    assert create_audit["target_type"] == "place_name"

    # Place-list alias previews are bounded but expose the exact total.
    for index in range(5):
        extra = admin.post("/api/place-names", json={
            "place_key": place_key,
            "name": f"Zagreb Variant {index} {_U}",
            "language": "en",
            "name_type": "variant",
        }, headers={"X-CSRFToken": csrf})
        assert extra.status_code == 201, extra.get_data(as_text=True)
    place_result = admin.get(f"/api/places?q={place_key}&per_page=20").get_json()
    place_preview = next(item for item in place_result["data"] if item["place_key"] == place_key)
    assert len(place_preview["names"]) == 5
    assert place_preview["names_total"] > len(place_preview["names"])
    assert place_preview["names_preview_truncated"] is True

    after_create = admin.get("/api/gazetteer").get_json()
    assert after_create["detector_ver"] != before_version

    # Notes are detector-visible for homographs, even if the alias spelling is unchanged.
    note_only = admin.put(f"/api/place-names/{name_id}", json={
        "note": "updated homograph evidence note",
    }, headers={"X-CSRFToken": csrf})
    assert note_only.status_code == 200, note_only.get_data(as_text=True)
    assert note_only.get_json()["gazetteer_revision"]["changed"] is True
    after_note = admin.get("/api/gazetteer").get_json()
    assert after_note["detector_ver"] != after_create["detector_ver"]

    search = admin.get(f"/api/place-names?q={_U}&source=user&language=en")
    assert search.status_code == 200
    rows = search.get_json()["data"]
    assert any(row["id"] == name_id and row["place_key"] == place_key for row in rows)

    changed_alias = f"Zagreb Curated Updated {_U}"
    updated = admin.put(f"/api/place-names/{name_id}", json={
        "name": changed_alias, "language": "en", "name_type": "historical",
        "homograph": False, "note": "revised curator note",
    }, headers={"X-CSRFToken": csrf})
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.get_json()["gazetteer_revision"]["changed"] is True
    update_audit = _audit_detail(pg_db, "places.name.update", f"place_name:{name_id}")
    assert update_audit and update_audit["name"] == changed_alias
    assert update_audit["previous"]["name"] == alias
    version_after_update = admin.get("/api/gazetteer").get_json()["detector_ver"]
    assert version_after_update != after_note["detector_ver"]

    # Seed refresh is idempotent and does not erase user-curated aliases.
    from services.geo.gazetteer import load_seed_file, sync_seed
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            result = sync_seed(cur, load_seed_file(), loaded_by="integration-test")
            assert result["changed"] is False
            cur.execute("SELECT 1 FROM geo_place_names WHERE id = %s AND name = %s AND source = 'user'",
                        (name_id, changed_alias))
            assert cur.fetchone() == (1,)
        conn.rollback()
    finally:
        conn.close()

    deleted = admin.delete(f"/api/place-names/{name_id}", headers={"X-CSRFToken": csrf})
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert deleted.get_json()["gazetteer_revision"]["changed"] is True
    delete_audit = _audit_detail(pg_db, "places.name.delete", f"place_name:{name_id}")
    assert delete_audit and delete_audit["name"] == changed_alias
    assert delete_audit["place_key"] == place_key
    assert admin.get("/api/gazetteer").get_json()["detector_ver"] != version_after_update

    history = admin.get("/api/gazetteer/loads?per_page=100&sort=id&order=desc").get_json()["data"]
    curation = [item for item in history if item["loaded_by"].startswith("admin:")]
    assert len(curation) >= 3
    inspected = admin.get(f"/api/gazetteer/loads/{curation[0]['id']}").get_json()["load"]
    assert inspected["immutable"] is True
    assert inspected["stats"]["kind"] == "admin_curation"


def test_place_search_sort_pagination_and_mutation_authorization(client_factory):
    admin = client_factory("admin")
    viewer = client_factory("viewer")
    csrf = _csrf(admin)
    viewer_csrf = _csrf(viewer)
    key = f"user:sort_test_{_U}"
    made = admin.post("/api/places", json={
        "place_key": key, "label": f"Sortable City {_U}", "feature_type": "city",
        "country_codes": ["NL"], "names": [{"name": f"Sortable City {_U}", "language": "en"}],
    }, headers={"X-CSRFToken": csrf})
    assert made.status_code == 201, made.get_data(as_text=True)

    page = viewer.get(f"/api/places?q={_U}&sort=place_key&order=desc&per_page=1")
    assert page.status_code == 200
    assert page.get_json()["pagination"]["per_page"] == 1
    assert page.get_json()["sort"] == "place_key" and page.get_json()["order"] == "desc"
    assert viewer.get(f"/api/places?sort=not_a_column").status_code == 400
    assert viewer.get("/api/places?status=unknown").status_code == 400
    assert viewer.get("/api/places?country=ÉÉ").status_code == 400

    assert viewer.post("/api/place-names", json={"place_key": key, "name": "Denied"},
                       headers={"X-CSRFToken": viewer_csrf}).status_code == 403
    assert viewer.delete(f"/api/place-names/999999", headers={"X-CSRFToken": viewer_csrf}).status_code == 403

    for invalid_name in ("---", "Zagreb القاهرة"):
        response = admin.post("/api/place-names", json={
            "place_key": "wikidata:Q1435", "name": invalid_name, "language": "en",
        }, headers={"X-CSRFToken": csrf})
        assert response.status_code == 400, response.get_data(as_text=True)
    mismatch = admin.post("/api/place-names", json={
        "place_key": "wikidata:Q1435", "name": "القاهرة", "language": "en",
    }, headers={"X-CSRFToken": csrf})
    assert mismatch.status_code == 400
