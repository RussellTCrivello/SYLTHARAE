"""Shared pytest fixtures.

Provides:
* ``pg_db``      - a disposable PostgreSQL server + fresh application database
                   (per-session) with the full migration bootstrap applied.
* ``app``        - the real Flask application wired to the disposable database.
* ``client``     - Flask test client.
* ``admin_session`` - an authenticated admin client session.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Isolated application data root for tests (Phase 19).
os.environ.setdefault("APP_DATA_DIR", str(PROJECT_ROOT / ".test_runtime"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-not-for-production-0123456789")
os.environ.setdefault("FLASK_ENV", "development")


def _probe(settings: dict, timeout: int = 5):
    """Can we actually connect with these settings? ``(ok, reason)``.

    Resolving a server address is not the same as reaching one. A wrong port
    or host silently connects to *something else* - on a workstation that
    usually means the application's own PostgreSQL, which answers with
    "fe_sendauth: no password supplied" or an authentication failure. Probing
    turns that into a precise answer instead of a wall of identical tracebacks
    from tests that never had a chance to run.
    """
    try:
        import psycopg2
    except Exception as exc:  # pragma: no cover - dependency missing
        return False, f"psycopg2 unavailable ({exc})"
    try:
        # Probe the *maintenance* database. The test database does not exist
        # until the bootstrap creates it, so probing it would fail on a
        # perfectly healthy server ("database ... does not exist") and skip
        # the whole suite.
        conn = psycopg2.connect(
            dbname="postgres",
            user=settings.get("user") or "postgres",
            password=settings.get("password") or "",
            host=settings.get("host") or "localhost",
            port=int(settings.get("port") or 5432),
            connect_timeout=timeout,
        )
        conn.close()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _pgserver_settings(server, db_name: str):
    """Settings for a pgserver instance, including the raw URI for diagnosis."""
    uri = server.get_uri()
    return pg_connection_settings(server, db_name), uri


def _candidate_databases(db_name: str):
    """Every reachable PostgreSQL this suite may use, best candidate first.

    Ordered so the disposable server wins when it works, and an explicitly
    configured server is honoured when it does not:

      1. pgserver's own instance (hermetic, trust authentication);
      2. ``DB_HOST``/``DB_PORT``/``DB_USER``/``DB_PASSWORD`` from the
         environment;
      3. the application's configured database (``get_database_config``).

    ``bootstrap_database`` creates and migrates a *test-named* database, so
    pointing the suite at an existing server does not touch application data.
    """
    candidates = []

    try:
        import pgserver

        data_dir = pathlib.Path(tempfile.mkdtemp(prefix="pgdata_"))
        server = pgserver.get_server(str(data_dir))
        settings, uri = _pgserver_settings(server, db_name)
        candidates.append((settings, "pgserver", server, uri))
    except Exception as exc:
        candidates.append((None, "pgserver", None,
                           f"could not start a disposable server: "
                           f"{type(exc).__name__}: {exc}"))

    if os.environ.get("DB_HOST"):
        candidates.append((
            {
                "host": os.environ.get("DB_HOST"),
                "port": int(os.environ.get("DB_PORT") or 5432),
                "user": os.environ.get("DB_USER") or "postgres",
                "password": os.environ.get("DB_PASSWORD") or "",
                "database": db_name,
            },
            "environment (DB_HOST/DB_PORT/DB_USER)", None, "from environment",
        ))

    try:
        from settings import get_database_config

        configured = get_database_config()
        candidates.append((
            {
                "host": getattr(configured, "host", None),
                "port": int(getattr(configured, "port", 5432) or 5432),
                "user": getattr(configured, "user", None) or "postgres",
                "password": getattr(configured, "password", "") or "",
                "database": db_name,
            },
            "application configuration", None, "from settings",
        ))
    except Exception:
        pass

    return candidates


@pytest.fixture(scope="session")
def pg_settings(tmp_path_factory):
    """A reachable PostgreSQL, found by probing rather than assuming.

    Skips - once, with the reason - when nothing is reachable, instead of
    letting every database test fail individually with the same error.
    """
    db_name = f"file_analysis_test_{os.getpid()}"
    attempts = []
    for settings, source, server, detail in _candidate_databases(db_name):
        if settings is None:
            attempts.append(f"{source}: {detail}")
            continue
        ok, reason = _probe(settings)
        if ok:
            pytest.pg_server_instance = server  # reused by the pg_server fixture
            return settings
        attempts.append(
            f"{source}: {settings.get('host')}:{settings.get('port')} -> {reason}"
            + (f" (uri: {detail})" if detail != "from environment" else ""))

    pytest.skip(
        "No reachable PostgreSQL for the integration suite. Tried:\n  - "
        + "\n  - ".join(attempts)
        + "\nPoint the suite at a server with DB_HOST/DB_PORT/DB_USER/DB_PASSWORD "
          "(a test-named database is created on it; application data is not touched)."
    )


@pytest.fixture(scope="session")
def pg_server(pg_settings):
    """The disposable server, when one could be reached (else the suite skips)."""
    server = getattr(pytest, "pg_server_instance", None)
    if server is None:
        pytest.skip("no disposable PostgreSQL server available in this environment")
    return server


def pg_connection_settings(server, db_name: str) -> dict:
    """Connection settings for the disposable PostgreSQL server.

    pgserver describes the server differently per platform, and the earlier
    code assumed the Linux/POSIX shape:

        POSIX   postgresql://postgres:@/postgres?host=/run/pgdata   (unix socket)
        Windows postgresql://postgres:@localhost:5432/postgres      (TCP)

    Taking ``parsed.path`` as a fallback host therefore handed the *database
    name* (``/postgres``) to psycopg2 as a socket directory.  On Windows that
    makes every database test fail with "connection to server on socket
    /postgres/.sPGSQL.5432 failed: Network is down" - not a code defect, but it
    hides every real defect behind it, so the parsing has to be right.

    Resolution order:
      1. an explicit ``host`` query parameter (the POSIX socket directory),
      2. the host in the URI authority (``localhost`` on Windows),
      3. ``localhost`` as a last resort - never a path component, which is
         never a host.
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(server.get_uri())
    query = urllib.parse.parse_qs(parsed.query)
    host = query.get("host", [None])[0] or parsed.hostname or "localhost"
    port = parsed.port or 5432
    return {
        "host": host,
        "port": int(port),
        "user": urllib.parse.unquote(parsed.username or "postgres"),
        "password": urllib.parse.unquote(parsed.password or ""),
        "database": db_name,
    }


@pytest.fixture(scope="session")
def pg_db(pg_settings, tmp_path_factory):
    """Create a fresh application database and run the migration bootstrap."""
    cfg = dict(pg_settings)
    db_name = cfg["database"]
    host_dir = cfg["host"]

    os.environ["DB_HOST"] = str(cfg["host"])
    os.environ["DB_PORT"] = str(cfg["port"])
    os.environ["DB_USER"] = str(cfg["user"])
    os.environ["DB_PASSWORD"] = str(cfg["password"])
    os.environ["DB_NAME"] = db_name

    # Reset ALL cached settings/config singletons from any earlier import.
    # NOTE: settings_adapter caches an adapter under `_interface_manager`
    # which wraps the manager; it must be cleared too, otherwise a stale
    # manager built before DB_* env vars were set keeps serving localhost.
    import settings.settings_manager as _sm
    import settings.settings_adapter as _sa
    import settings.config as _sc
    for mod, attrs in (
        (_sm, ("_settings_manager",)),
        (_sa, ("_interface_manager",)),
        (_sc, ("_config",)),
    ):
        for attr in attrs:
            if hasattr(mod, attr):
                try:
                    setattr(mod, attr, None)
                except Exception:
                    pass

    # The same staleness applies one layer up: Api.utils.utils keeps a
    # module-level ``DatabaseHub`` (and its query cache) built from whatever
    # configuration existed on first use. If anything touched it before the
    # DB_* variables above were set, every later search keeps talking to the
    # default server ("connection to server at localhost:5432 failed:
    # Connection refused") even though the fixtures are pointed somewhere else.
    # Clearing it here is what makes search results reflect the test database.
    try:
        import Api.utils.utils as _api_utils

        for attr in ("_query_executor", "_query_cache"):
            if hasattr(_api_utils, attr):
                try:
                    setattr(_api_utils, attr, None)
                except Exception:
                    pass
    except Exception:
        pass

    # Drop any pooled connections opened against the previous configuration.
    try:
        from database.database.database import reset_connection_pools

        reset_connection_pools()
    except Exception:
        pass

    from database.bootstrap import bootstrap_database

    cfg = {
        "host": host_dir,
        "port": 5432,
        "user": "postgres",
        "password": "",
        "database": db_name,
    }
    report = bootstrap_database(cfg)
    assert report["database_created"] is True
    assert len(report["applied_migrations"]) >= 4

    yield cfg


_WEB_APP_PREPARED = False
_WEB_APP = None


def prepare_web_app():
    """Apply the test-time app configuration *before any request is dispatched*.

    Flask refuses ``add_url_rule`` once the application has handled a request
    ("The setup method 'add_url_rule' can no longer be called"), and the ``app``
    fixture is not guaranteed to run first: the readiness login check and any
    other in-process check drive the same singleton application. Doing this at
    session start - rather than lazily in the fixture - is what makes the suite
    order-independent; the fixture keeps only the state that genuinely needs a
    live database (the initialisation marker).

    Idempotent: importing twice, or calling from more than one place, must not
    double-register the route.
    """
    global _WEB_APP_PREPARED, _WEB_APP
    if _WEB_APP_PREPARED:
        return _WEB_APP

    from apps.web.app import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False  # API-level tests fetch tokens explicitly

    # API-01: per-route limits would trip the ~30 login fixtures; individual
    # rate-limit tests re-enable the limiter explicitly.
    from core.security.rate_limit import limiter as _limiter
    _limiter.enabled = False

    if "_test/sec08/boom" not in {r.rule for r in flask_app.url_map.iter_rules()}:
        def _boom():
            raise RuntimeError("SECRET postgresql://user:pass@host/db leaked")

        flask_app.add_url_rule("/_test/sec08/boom", view_func=_boom, endpoint="_sec08_boom")

    _WEB_APP = flask_app
    _WEB_APP_PREPARED = True
    return flask_app


@pytest.fixture(scope="session", autouse=True)
def _prepared_web_app():
    """Prepare the Flask app before the first test, whatever it is."""
    prepare_web_app()
    yield


@pytest.fixture(scope="session")
def app(pg_db):
    """The real Flask application against the disposable database."""
    flask_app = prepare_web_app()

    # The setup gate in Api/routes/setup.py redirects EVERY request to /setup
    # unless a filesystem marker exists and the critical tables are present.
    # pg_db bootstraps the tables, so only the marker is missing - which made
    # every authenticated fixture fail with 302 and blocked the API surface
    # from being tested at all. Mark it and leave it: the file is gitignored,
    # the gate's table check stays authoritative per database, and unlinking
    # it at teardown raced parallel xdist workers whose sessions outlived
    # ours (their in-flight requests 302'ed to /setup mid-suite).
    from core.initialization import INIT_MARKER_FILE, mark_system_initialized

    mark_system_initialized()

    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def admin_credentials(pg_db):
    """Ensure an initial admin exists; return (username, password)."""
    from core.security.service import get_auth_service

    auth = get_auth_service()
    username = "testadmin"
    password = "test-admin-password-123"
    if auth.get_user_by_username(username) is None:
        auth.create_user(username, password, role="admin")
    return username, password


@pytest.fixture()
def admin_client(app, client, admin_credentials):
    """A test client authenticated as administrator."""
    username, password = admin_credentials
    resp = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


@pytest.fixture()
def viewer_client(app, admin_credentials):
    """A test client authenticated as a read-only user.

    Uses its OWN client instance: sharing one client across roles would let
    the later login overwrite the session cookie.
    """
    from core.security.service import get_auth_service

    auth = get_auth_service()
    username = "testviewer"
    password = "test-viewer-password-123"
    if auth.get_user_by_username(username) is None:
        auth.create_user(username, password, role="viewer")
    c = app.test_client()
    resp = c.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return c


@pytest.fixture()
def client_factory(app, client, admin_credentials):
    """Factory for clients authenticated with an arbitrary role."""
    from core.security.service import get_auth_service

    def _make(role: str):
        auth = get_auth_service()
        username = f"testrole_{role}"
        password = f"test-{role}-password-123"
        if auth.get_user_by_username(username) is None:
            auth.create_user(username, password, role=role)
        c = app.test_client()
        resp = c.post("/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200
        return c

    return _make
