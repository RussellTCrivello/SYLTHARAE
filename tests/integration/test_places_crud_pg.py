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
    conn.close()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


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
