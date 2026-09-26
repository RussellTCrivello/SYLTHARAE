# SYLTHARAE documentation

Start with the task you have.

## Install and run

| Document | Contents |
|---|---|
| [INSTALL.md](INSTALL.md) | Requirements, the setup wizard, `.env`, air-gapped installs, production hardening (Step 10), troubleshooting E1–E7 |
| [OPERATIONS.md](OPERATIONS.md) | Production serving with Waitress (systemd and Windows service), health checks, logs, backups, upgrades, rollback, recovery, maintenance |
| [CONFIGURATION.md](CONFIGURATION.md) | Every environment variable the code reads, with defaults; runtime settings; reserved variables |
| [SECURITY.md](SECURITY.md) | Threat model, authentication and roles, request protections, file and archive safety, deployment checklist, reporting |

## Understand the system

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Context, layout, start-up, request lifecycle, ingestion pipeline and jobs (Mermaid diagrams), front end, i18n, extension recipes |
| [DOMAIN_MODEL.md](DOMAIN_MODEL.md) | Source → Side → Hash → Path → Content → Analysis; content identity versus occurrence |
| [DATABASE.md](DATABASE.md) | Tables by area, identity constraints, migrations, backups |
| [PRODUCTIZATION_MAP.md](PRODUCTIZATION_MAP.md) | Capability → screen → API → service → storage → tests → docs; legacy parts |

## Change the system

| Document | Contents |
|---|---|
| [DEVELOPMENT.md](DEVELOPMENT.md) | Setup, where things go, quality gates, commit and release conventions |
| [TESTING.md](TESTING.md) | Test layers, the disposable database, running tests, generated-document checks, the live smoke test |
| [../AUDIT_REPORT.md](../AUDIT_REPORT.md) | The v2.1.1 audit: findings, fixes, residual items, test evidence |
| [../CHANGELOG.md](../CHANGELOG.md) | Release history |

## Generated references (do not edit by hand)

| Document | Generated from |
|---|---|
| [INTERFACE_REGISTRY.md](INTERFACE_REGISTRY.md), [REGISTRY_EVIDENCE.md](REGISTRY_EVIDENCE.md) | `core/interfaces/` |
| [endpoint_inventory.json](endpoint_inventory.json) | the Flask URL map |
| [reference/](reference/README.md) | Python modules and HTTP routes (`tools/docs/generate_reference.py`) |
| [reference/DATABASE_SCHEMA.md](reference/DATABASE_SCHEMA.md) | a freshly migrated database (`tools/docs/generate_schema.py`) |
| [EXPERIENCE_CONTRACT.md](EXPERIENCE_CONTRACT.md), [SCREEN_INSPECTOR_EVIDENCE.md](SCREEN_INSPECTOR_EVIDENCE.md), [ACTION_SURFACE_AUDIT.md](ACTION_SURFACE_AUDIT.md) | `core/experience/` |
| [COMPONENT_LIBRARY.md](COMPONENT_LIBRARY.md) | `core/frontend/` |

[TESTING.md](TESTING.md#generated-documents-are-tested) lists the regeneration
commands.
