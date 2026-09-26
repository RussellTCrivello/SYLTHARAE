# Productization map

This map connects each **product capability** to the code that delivers it:
screen → HTTP API → service → storage, plus the tests that guard it and the
document that explains it. Use it to find where to change something, or what
a change will touch.

It is a *map*, not an inventory. The authoritative, generated lists are:

| What | Source of truth | Generated document |
|---|---|---|
| Screens (id, route, role, lifecycle, shortcuts) | `core/interfaces/registry.py` | [INTERFACE_REGISTRY.md](INTERFACE_REGISTRY.md), [REGISTRY_EVIDENCE.md](REGISTRY_EVIDENCE.md) |
| HTTP endpoints | the Flask URL map | [endpoint_inventory.json](endpoint_inventory.json), [reference/](reference/README.md) |
| UI components and actions | `core/frontend/`, `core/experience/` | [COMPONENT_LIBRARY.md](COMPONENT_LIBRARY.md), [EXPERIENCE_CONTRACT.md](EXPERIENCE_CONTRACT.md), [ACTION_SURFACE_AUDIT.md](ACTION_SURFACE_AUDIT.md) |
| Database tables and columns | `database/migrations/` | [reference/DATABASE_SCHEMA.md](reference/DATABASE_SCHEMA.md) |

When this page and a generated document disagree, the generated document is
right. Please fix this page.

## Capabilities

| Capability | Screen(s) | API | Service / engine | Storage | Tests | Docs |
|---|---|---|---|---|---|---|
| **Install and first run** | `/setup` wizard | `/api/setup/*` | `core/installer.py`, `Api/routes/setup.py` | `.env`, migrations | `test_setup_route_validation.py`, `test_readiness_login_check.py` | [INSTALL.md](INSTALL.md) |
| **Sign-in, users, roles** | Login, change password, User Management | `/auth/*`, `/api/auth/users*` | `core/security/` (`service.py`, `flask_ext.py`, `passwords.py`) | `users`, `sessions`, `audit_log` | `test_auth_service.py`, `test_passwords_and_errors.py`, `test_admin_password_recovery.py`, `tests/security/` | [SECURITY.md](SECURITY.md) |
| **Ingest from disk or upload** | Input / Operations | `/api/input/jobs`, `/api/input/uploads`, `/api/input/sources`, `/api/input/sides` | `services/ingesting/`, `pipeline/integrated_reader.py`, `pipeline/storage_pipeline.py` | `hashs`, `paths`, `contents`, `sources`, `sides` | `test_search_after_ingest.py`, `test_concurrent_document_ingest.py`, `test_archive*` | [ARCHITECTURE.md §5](ARCHITECTURE.md#5-ingestion-pipeline), [DOMAIN_MODEL.md](DOMAIN_MODEL.md) |
| **Format reading and detection** | (inside ingestion) | none | `reader_file/readers/*`, `reader_file/main_specify_method.py`, `core/formats/`, `core/ocr/` | `contents`, `contents_raw`, `paths.metadata` | `test_reader_matrix.py`, `test_reader_routing.py`, `test_format_detection.py` | [ARCHITECTURE.md §11](ARCHITECTURE.md#11-extending-syltharae) |
| **Archives (safe extraction, nesting)** | Archives | `/api/archives*` | `core/archive_safety.py`, `reader_file/readers/read_archive.py` | `paths.parent_path_id`, `hash_contexts` | `test_archive*`, `test_rar_without_decoder_ingest.py` | [SECURITY.md](SECURITY.md) |
| **Deduplication and identity** | (all views) | none | `database/services/dedup_service.py`, `core/hashing.py` | `hashs` (content), `paths` (occurrence per source and side) | `test_dedup_delete_reingest.py`, `test_large_file_dedup.py`, `test_relationship_deduplication.py` | [DOMAIN_MODEL.md](DOMAIN_MODEL.md) |
| **Jobs (progress, pause, retry, recovery)** | Jobs / Operations | `/api/jobs/*`, `/api/jobs/stream` (SSE) | `services/jobs/manager.py`, `services/jobs/repository.py` | `jobs`, `job_events` | `test_job_service.py`, `test_job_system.py`, `tests/security/test_jobs_security.py` | [ARCHITECTURE.md §6](ARCHITECTURE.md#6-jobs), [OPERATIONS.md](OPERATIONS.md) |
| **Search and export** | Search | `/api/search`, `/api/search/export*`, `/api/search/saved*` | `Api/services/search_service.py`, `Api/services/search_export.py` | `words`, `words_hashs`, `keywords*` | `test_search*`, `test_search_export_authority.py` | [reference/](reference/README.md) |
| **Categorisation and keywords** | Categories, Keywords | `/api/categor*`, `/api/keywords*` | `classification/`, `Api/routes/keywords.py` | `categorys`, `words_categorys`, `analyst_*` | `test_analyst_categorization.py`, `test_analyst_categorization_audit.py` | [DOMAIN_MODEL.md](DOMAIN_MODEL.md) |
| **Notifications and alerts** | Notifications | `/api/notifications*` | `core/monitoring/notification_service.py` | `alerts` | `test_notification_*.py` | [OPERATIONS.md](OPERATIONS.md) |
| **Settings and compute policy** | Settings, Operations | `/api/settings/*` | `settings/`, `core/compute/` | `data/settings.json` | `tests/security/test_settings_error_hygiene.py`, `test_compute*` | [CONFIGURATION.md](CONFIGURATION.md) |
| **Backup, import and export** | Import / Export | `/api/import-export/*` | `Api/routes/import_export.py` | all tables | `tests/security/test_import_export_authz.py` | [OPERATIONS.md §4](OPERATIONS.md#4-backups) |
| **Health and monitoring** | none | `/health` | `Api/routes/health.py`, `core/monitoring/` | none | `tests/security/test_audit_remediation.py`, `test_env_config_names.py` | [OPERATIONS.md §2](OPERATIONS.md#2-health-checks) |
| **Translations and RTL** | Translation management | `/api/translations*` | Babel, `Api/services/translation*` | `translation_overrides`, `translations/` | `test_translation_*.py`, `test_analyst_i18n.py` | [ARCHITECTURE.md §8](ARCHITECTURE.md#8-internationalisation) |
| **Production serving** | none | none | `apps/web/serve.py` (Waitress), `run_web.py` | none | `tests/unit/test_serve.py` | [OPERATIONS.md §1](OPERATIONS.md#1-production-serving) |

Test file names are relative to `tests/unit/`, `tests/integration/` or
`tests/security/` (a `*` names a family of files). Run
`pytest --collect-only -q tests` to list the individual tests.

## Legacy and transitional parts

These parts still exist and still work, but they are not where new work should go:

| Part | Status | Replacement / note |
|---|---|---|
| Legacy interface ids (`LEGACY_INTERFACE_IDS` in `core/interfaces/registry.py`) | Resolved to canonical ids by `resolve_interface_id()` | Use the canonical ids in [INTERFACE_REGISTRY.md](INTERFACE_REGISTRY.md). What is still legacy is listed in [REGISTRY_EVIDENCE.md](REGISTRY_EVIDENCE.md). |
| Legacy JSON routes outside `/api/` (for example `/analysis/batch/process`) | Kept for compatibility; authenticated and role-checked like everything else | Prefer the `/api/*` equivalents. `core/interfaces/inventory.py` lists them explicitly. |
| Command-line adapters (`run_cli.py`, `run_import.py`, `apps/cli/`, `apps/importing/`) | Deprecated; they call the same services as the web UI | The web UI and `/api/input/jobs`. |
| `Api/utils/utils.py` loaded twice (`Api.utils_module` via `Api/utils/__init__.py`, and `Api.utils.utils` by `services/jobs/repository.py`) | Works. Each copy keeps its own `DatabaseHub` singleton (AUDIT-ARCH-02, low severity) | Import through `Api.utils` only; consolidate when that package is next changed. |
| Flask built-in server | Default for development | `WSGI_SERVER=waitress` in production ([OPERATIONS.md](OPERATIONS.md)). |
| Reserved `.env` names (`MAX_WORKERS`, `FILE_CHUNK_SIZE`, …) | Written by the installer, not read | Settings page; see [CONFIGURATION.md](CONFIGURATION.md#reserved-variables-written-not-read). |
| Outlook PST reading | Needs the optional `libpff-python` (fails to build on many hosts) | Without it, PST files are recorded with an explanatory error rather than skipped silently. |
