# Testing

SYLTHARAE is tested in layers. All of them run from the repository root in
a virtual environment that has the `dev` extra installed:

    pip install -r requirements.txt
    pip install -e ".[dev,server]"

## Layers

| Layer | Location | Needs | What it proves |
|---|---|---|---|
| **Unit** | `tests/unit/` | nothing external | Pure logic: parsers, readers, format detection, archive safety, state machines, settings, security helpers, the WSGI launcher, the documentation generators. |
| **Front end** | `tests/js/*.mjs`, run by `tests/unit/test_action_*`, `test_frontend_*` and similar | Node.js 18+ on `PATH` | ES modules behave correctly against a DOM stub. These tests are skipped when `node` is missing. |
| **Integration** | `tests/integration/` (marker `integration`) | a disposable PostgreSQL (see below) | Migrations, repositories, ingestion into a real database, search after ingestion, jobs, interface coverage, schema reference. |
| **Security** | `tests/security/` (marker `security`) | disposable PostgreSQL | Authentication, role policy, CSRF, rate limiting, import/export authorisation, error hygiene, and regressions for every audit finding. |
| **End to end** | `tests/e2e/` (marker `e2e`) | disposable PostgreSQL | Full workflows through the Flask test client: pages, operations, ingest → search. |
| **Live smoke** | `tools/smoke/live_smoke.py` | a running server + PostgreSQL | The real production stack over HTTP: setup wizard, Waitress, proxy headers, HSTS, CSRF, ingestion, search, export, users, logout, restart, repeat. |

## The disposable database

`tests/conftest.py` provides the `pg_db` fixture (plus `admin_client`,
`viewer_client` and `client_factory`). It finds a server in this order:

1. **`pgserver`** (a pip-installable PostgreSQL) in a temporary directory,
   with trust authentication. It needs no setup: `pip install pgserver`.
2. **The environment**: if `DB_HOST` is set, it uses `DB_HOST`, `DB_PORT`,
   `DB_USER` and `DB_PASSWORD`. That role must be able to `CREATE DATABASE`.

If neither is available, database-backed tests are **skipped**, not failed,
and the skip reason says why. The fixture creates a fresh database, runs the
real bootstrap (`database/bootstrap.py`, all migrations), and resets every
cached settings or pool singleton so nothing leaks from earlier imports.

Tests that must not depend on what other tests did to the shared database
create their own. `test_schema_reference.py`, for example, migrates a
dedicated database, because `test_migration_0007` legitimately downgrades
and re-upgrades a migration, which reorders columns.

## Running

    python -m pytest tests/unit                          # fast, no database
    python -m pytest tests/integration tests/security    # needs PostgreSQL
    python -m pytest tests/e2e
    python -m pytest -m security                         # by marker
    python -m pytest tests/unit/test_serve.py -k waitress

The project sets `addopts = "-q"`. To see a per-test outcome list, use
`-rA` and count the outcomes:

    python -m pytest -rA tests/unit | grep -E "^(PASSED|FAILED|ERROR) tests/" | cut -d' ' -f1 | sort | uniq -c

Filter on `^(FAILED|ERROR) tests/`: captured log lines also start with
`ERROR`.

## Generated documents are tested

Several documents are generated from the code, and a test fails when the
committed copy is stale:

| Document | Regenerate with |
|---|---|
| `docs/INTERFACE_REGISTRY.md` | `python -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md` |
| `docs/REGISTRY_EVIDENCE.md` | `python -m core.interfaces.evidence --write docs/REGISTRY_EVIDENCE.md` |
| `docs/endpoint_inventory.json` and the evidence part B | `INFORAXIS_WRITE_EVIDENCE=1 python -m pytest tests/integration/test_interface_coverage.py -k "evidence or inventory"` |
| `docs/EXPERIENCE_CONTRACT.md` | `python -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md` |
| `docs/SCREEN_INSPECTOR_EVIDENCE.md` | `python -m core.experience.audit docs/SCREEN_INSPECTOR_EVIDENCE.md` |
| `docs/ACTION_SURFACE_AUDIT.md` | `python -m core.experience.action_audit docs/ACTION_SURFACE_AUDIT.md` |
| `docs/COMPONENT_LIBRARY.md` | `python -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md` |
| `docs/reference/` (modules, routes) | `python tools/docs/generate_reference.py` (`--check` to verify) |
| `docs/reference/DATABASE_SCHEMA.md` | `python tools/docs/generate_schema.py` with `DB_*` pointing at a freshly migrated database, or `INFORAXIS_WRITE_EVIDENCE=1 python -m pytest tests/integration/test_schema_reference.py` |

The job state diagram in `docs/ARCHITECTURE.md` is checked against
`services/jobs/job_state.TRANSITIONS` by `tests/unit/test_architecture_diagrams.py`.

## Live smoke test

`tools/smoke/live_smoke.py` drives a **running** server over HTTP the way a
browser behind a TLS-terminating reverse proxy does. It sends
`X-Forwarded-Proto: https`, a public `X-Forwarded-Host`, a same-origin
`Referer` and the CSRF token. Run it against the production configuration:

    # 1. A throwaway PostgreSQL (any server works; pgserver is quickest)
    python -c "import pgserver; print(pgserver.get_server('/tmp/syl-pg', cleanup_mode=None).get_uri())"

    # 2. A clean checkout, served as production would be
    git archive HEAD | tar -x -C /tmp/syl-app && cd /tmp/syl-app
    APP_DATA_DIR=/tmp/syl-data FLASK_ENV=production WSGI_SERVER=waitress \
      TRUSTED_PROXY_COUNT=1 FLASK_PORT=5055 AUTO_INSTALL=0 python run_web.py

    # 3. In another shell
    export SMOKE_ADMIN_PASSWORD='choose-a-strong-one' SMOKE_EVIDENCE=/tmp/syl-evidence \
           SMOKE_DB_HOST=/tmp/syl-pg SMOKE_DB_PASSWORD=unused-with-trust
    python tools/smoke/live_smoke.py install
    python tools/smoke/live_smoke.py run1
    #    restart the server (Ctrl-C, then start it again without FLASK_ENV,
    #    so production mode must come from the wizard's .env)
    python tools/smoke/live_smoke.py run2

It generates `memo.txt`, `ledger.csv`, `minutes.docx` and `bundle.zip`, which
contains a duplicate of the memo and a nested `inner/notes.txt`. All of them
contain the keyword *zephyrine*. The script then checks:

* before install, `/health` redirects to `/setup`; the wizard's
  test-database and install steps work, and a second install is refused (409);
* `/health` is `healthy` immediately after install;
* HSTS is sent on proxied HTTPS and not on plain HTTP;
* anonymous API calls get 401 and a bad password gets 401;
* CSRF: a missing token, a cross-site `Referer` and a proxy that drops the
  host are all rejected;
* source and side creation, and the `INGESTION_ROOTS` allowlist (`/etc` is
  rejected);
* the ingestion job completes; search finds the keyword in the text, CSV,
  DOCX and nested-archive files; notifications, settings export and search
  export work;
* users: an admin creates an analyst; the analyst is gated until they change
  their temporary password, can then search, and is denied admin settings;
* logout invalidates the session.

Result on this release (Python 3.11, PostgreSQL 16.2, Waitress 3.0.2):
install 4/4, run1 29/29, and after a restart run2 29/29. Re-ingesting the same
folder into the same source and side stored 0 new files and reported 4
duplicates. The ZIP's copy of the memo is recorded as a second occurrence of
the same content hash.

Two defects this procedure found have been fixed with regression tests:
`/health` reported `degraded` after install until a restart
(AUDIT-HEALTH-01), and the schema-reference test depended on test order. See
[AUDIT_REPORT.md](../AUDIT_REPORT.md).

## Adding tests

* Put the test in the lowest layer that can prove the behaviour.
* Every bug fix gets a regression test that **fails on the old code**. Check
  this by stashing the fix and running the test.
* Tests must not depend on order. If a test changes shared state (schema,
  settings, environment), restore it or use a dedicated resource.
* Never assert against numbers restated in prose. Assert against the
  generated sources instead.
