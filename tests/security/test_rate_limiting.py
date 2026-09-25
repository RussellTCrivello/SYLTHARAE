"""API-01 regression: login brute force is rate limited (429)."""
import pytest


@pytest.fixture()
def rate_limited_app(app):
    from core.security.rate_limit import limiter

    prev_enabled = limiter.enabled
    limiter.enabled = True
    try:
        limiter.reset()
    except Exception:
        pass
    try:
        yield app
    finally:
        limiter.enabled = prev_enabled
        try:
            limiter.reset()
        except Exception:
            pass


@pytest.mark.usefixtures("rate_limited_app")
def test_login_brute_force_rate_limited(client, admin_credentials):
    username, password = admin_credentials
    codes = []
    for _ in range(12):
        resp = client.post(
            "/auth/login",
            json={"username": "nobody", "password": "wrong-password"},
        )
        codes.append(resp.status_code)
    assert 429 in codes, f"expected a 429 among {codes}"
    # Before the limit tripped, invalid credentials must have been 401s.
    assert codes[0] == 401


@pytest.mark.usefixtures("rate_limited_app")
def test_job_progress_polling_is_not_throttled_during_a_run(admin_client):
    """The panel that explains a failure must survive the run it reports on.

    The Jobs page polls the job's status and error endpoints while a run is in
    progress. Under the default 60/minute limit every poll of the error
    endpoint answered 429 for the whole duration of a production ingest (the
    log shows one 429 per poll), so the operator could not see *why* files
    failed - including the archive that reported no decoder.

    These read-only endpoints therefore carry their own, higher limit. This
    test polls them past the default budget and asserts none is refused.
    """
    for _ in range(75):
        assert admin_client.get("/api/jobs/NOPE/errors").status_code == 404
        assert admin_client.get("/api/jobs/NOPE").status_code == 404


@pytest.mark.usefixtures("rate_limited_app")
def test_the_default_limit_still_applies_to_other_endpoints(admin_client):
    """Polling endpoints are the exception, not the rule."""
    codes = [admin_client.get("/api/jobs").status_code for _ in range(75)]
    assert 429 in codes, f"expected the default limit to still apply: {set(codes)}"


# ---------------------------------------------------------------------------
# Interactive read endpoints (data an open page fetches over and over)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("rate_limited_app")
def test_archives_section_pagination_is_not_throttled_while_browsing(admin_client):
    """Browsing a section must not rate-limit itself out of existence.

    Reported from the running application: jumping through the archives
    explorer produced

        GET /api/archives/keywords?limit=10&sort_by=file_count&... 429

    for the sections themselves. Two things multiply here - a section page
    load fetches several endpoints at once, and a jump to page N walks the
    cursors from page 1 - so the 60/minute default refused the operator's own
    browsing. These endpoints carry an explicit, still-bounded limit.
    """
    for _ in range(75):
        resp = admin_client.get("/api/archives/keywords?limit=10")
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]


@pytest.mark.usefixtures("rate_limited_app")
def test_the_archives_explorer_can_load_every_section_in_one_burst(admin_client):
    """A single page load fetches all sections; that burst must fit too."""
    endpoints = (
        "/api/archives/categories?limit=10",
        "/api/archives/keywords?limit=10",
        "/api/archives/titles?limit=10",
        "/api/archives/sources?limit=10",
        "/api/archives/sides?limit=10",
        "/api/archives/hashs?limit=10",
        "/api/archives/geolocation?limit=10",
    )
    for _ in range(10):  # ten page changes' worth of section loads
        for endpoint in endpoints:
            assert admin_client.get(endpoint).status_code == 200, endpoint


@pytest.mark.usefixtures("rate_limited_app")
def test_the_expensive_search_endpoint_keeps_the_strict_default(admin_client):
    """Read-only is not the same as cheap: search is not in the browsing set."""
    codes = [
        admin_client.get("/api/archives/search?q=anything").status_code
        for _ in range(75)
    ]
    assert 429 in codes, f"expected the strict default on search: {set(codes)}"


@pytest.mark.usefixtures("rate_limited_app")
def test_the_file_details_modal_call_is_not_throttled_per_file(admin_client, pg_db):
    """Opening files one after another in the pop-up is normal review work."""
    for file_id in range(1, 76):
        status = admin_client.get(f"/api/file/{file_id}/details").status_code
        assert status in (200, 404), status
        assert status != 429
