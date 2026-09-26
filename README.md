# SYLTHARAE

SYLTHARAE is a multidimensional information intelligence platform. It
discovers, reads, extracts, classifies, indexes, analyses, preserves and
connects information across large, mixed collections of files: documents,
spreadsheets, e-mail and mailboxes, archives, images (with OCR), audio,
video, e-books, databases and diagrams.

It is **self-hosted and offline-capable**. It runs on one machine with
PostgreSQL, and nothing leaves your network.

## What it does

* **Ingests** folders and uploads, including nested archives (zip, tar, 7z,
  rar). Extraction is safe: depth, size, ratio and path checks.
* **Identifies each file by content**, not by its extension, and extracts
  text and forensic metadata (office macros, embedded files, signatures,
  PDF structure).
* **De-duplicates** by content hash. A file seen many times is stored and
  indexed once, and every occurrence keeps its own source, side and path
  ([domain model](docs/DOMAIN_MODEL.md)).
* **Searches** full text and keywords across everything ingested, with
  filters and CSV or Excel export.
* **Organises** content with smart categories, analyst categories,
  keywords, sources and sides, and supports review, notifications and
  analytics.
* **Runs long work as jobs** with live progress, pause, cancel and retry,
  and survives restarts.
* **Speaks five languages** (English, Arabic, Hebrew, Persian, Croatian),
  with right-to-left layouts.
* **Secured by default**: sign-in, roles (admin, analyst, viewer), CSRF, rate
  limits, lockout, security headers and an audit log ([security](docs/SECURITY.md)).

## Quick start

Requirements: Python 3.10+ and PostgreSQL 14+.

    git clone https://github.com/RussellTCrivello/SYLTHARAE.git
    cd SYLTHARAE
    python -m venv .venv && . .venv/bin/activate     # Windows: setup.bat does all of this
    pip install -r requirements.txt
    python run_web.py

Open <http://localhost:5000>. The **setup wizard** checks the database
connection, writes `.env`, creates the schema and your administrator
account. Windows users can run `setup.bat` and then `start.bat`.

**For production**, serve with Waitress behind an HTTPS reverse proxy.
Never use the development server there:

    pip install -e ".[server]"
    WSGI_SERVER=waitress TRUSTED_PROXY_COUNT=1 python run_web.py

The full procedure, including air-gapped installs, is in
[docs/INSTALL.md](docs/INSTALL.md), and day-2 operations (service units,
backups, upgrades, rollback) are in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Documentation

| Document | For |
|---|---|
| [INSTALL](docs/INSTALL.md) | Installing, first run, production hardening, troubleshooting |
| [OPERATIONS](docs/OPERATIONS.md) | Serving, health checks, logs, backups, upgrades, rollback, recovery |
| [CONFIGURATION](docs/CONFIGURATION.md) | Every environment variable and where runtime settings live |
| [SECURITY](docs/SECURITY.md) | Threat model, authentication, roles, protections, reporting |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | How it fits together, with diagrams |
| [DOMAIN_MODEL](docs/DOMAIN_MODEL.md), [DATABASE](docs/DATABASE.md) | Identity model, tables, migrations |
| [DEVELOPMENT](docs/DEVELOPMENT.md), [TESTING](docs/TESTING.md) | Contributing, quality gates, test layers, live smoke test |
| [PRODUCTIZATION_MAP](docs/PRODUCTIZATION_MAP.md) | Capability → screen → API → service → storage → tests |
| [AUDIT_REPORT](AUDIT_REPORT.md) | The v2.1.1 audit and the v2.2.0 follow-up: findings, fixes, residual items, test evidence |
| [CHANGELOG](CHANGELOG.md) | Release history |

[docs/README.md](docs/README.md) indexes every document.

### Generated documents

These are produced from the code and checked by the test suite, so they
cannot drift. [TESTING.md](docs/TESTING.md#generated-documents-are-tested)
explains how to regenerate them.

* [Interface registry](docs/INTERFACE_REGISTRY.md) and
  [registry evidence](docs/REGISTRY_EVIDENCE.md): every screen, its route,
  role and shortcuts
* [API and module reference](docs/reference/README.md) and the
  [endpoint inventory](docs/endpoint_inventory.json)
* [Database schema](docs/reference/DATABASE_SCHEMA.md): read from a freshly
  migrated database
* [Experience contract](docs/EXPERIENCE_CONTRACT.md),
  [component library](docs/COMPONENT_LIBRARY.md),
  [screen inspector evidence](docs/SCREEN_INSPECTOR_EVIDENCE.md),
  [action surface audit](docs/ACTION_SURFACE_AUDIT.md)

## Licence

SYLTHARAE is free software under the GNU Affero General Public License,
version 3 or later ([LICENSE](LICENSE)). If you run a modified copy for other
people over a network, you must offer them your source. Set `SOURCE_CODE_URL`
for the **Source code** link every page shows. Third-party files keep their
own licences ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). See
[docs/LICENSING.md](docs/LICENSING.md).

## Project status

Current release: see [CHANGELOG.md](CHANGELOG.md) and `version.py`. Report
security issues through a private GitHub security advisory, not in public
issues ([SECURITY.md](docs/SECURITY.md)).
