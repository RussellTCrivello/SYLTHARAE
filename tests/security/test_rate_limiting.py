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


@pytest.mark.usefixtures("rate_limited_app")
def test_a_rate_limited_sign_in_says_so_in_json(client):
    """The sign-in form parses the answer as JSON. The limiter's default 429 is
    an HTML page, so the form failed to parse it and told a locked-out user
    "An internal error occurred" (found by the browser smoke). The 429 is JSON
    for JSON callers and names the condition."""
    responses = [client.post("/auth/login", json={"username": "nobody", "password": "wrong"})
                 for _ in range(12)]
    limited = [r for r in responses if r.status_code == 429]
    assert limited, [r.status_code for r in responses]
    body = limited[-1].get_json()
    assert body == {"error": "Too many attempts. Wait a minute and try again.",
                    "code": "rate_limited"}


def test_a_rate_limited_page_request_gets_a_plain_answer(app):
    """A browser navigation is not an API caller: a plain answer, not JSON."""
    from werkzeug.exceptions import TooManyRequests

    with app.test_request_context("/sources", headers={"Accept": "text/html"}):
        response = app.make_response(app.handle_user_exception(TooManyRequests()))
    assert response.status_code == 429
    assert response.content_type.startswith("text/plain")
    assert "Too many attempts" in response.get_data(as_text=True)

    with app.test_request_context("/api/search", headers={"Accept": "text/html"}):
        response = app.make_response(app.handle_user_exception(TooManyRequests()))
    assert response.get_json()["code"] == "rate_limited", "API paths always answer JSON"


def test_the_sign_in_page_explains_a_rate_limit_it_cannot_parse():
    """Behind a proxy the 429 may still be HTML; the form keeps the status."""
    from pathlib import Path
    script = (Path(__file__).resolve().parents[2] / "static/js/pages/login-page.js").read_text(encoding="utf-8")
    assert "r.json().catch(" in script
    assert "res.status === 429 ? tr('Too many attempts. Wait a minute and try again.')" in script


def test_the_rate_limit_message_is_translated():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "translations"
    for language in ("ar", "fa", "he"):
        catalog = (root / language / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
        entry = catalog.split('msgid "Too many attempts. Wait a minute and try again."', 1)
        assert len(entry) == 2, language
        assert not entry[1].lstrip().startswith('msgstr ""'), f"{language}: untranslated"
