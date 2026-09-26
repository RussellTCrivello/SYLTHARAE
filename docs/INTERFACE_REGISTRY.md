# Interface Registry

The Interface Registry (`core/interfaces/`) is the single source of truth for
every user-facing screen in SYLTHARAE: its identifier, domain, route, icon,
lifecycle status, required role, owning endpoints, help text and keyboard
shortcut. Navigation, the command palette, page help, keyboard shortcuts and
the per-role visibility rules are all *derived* from it; nothing else is
allowed to define a screen.

The table at the end of this document is generated. Do not edit it by hand:

    python3 -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md

`tests/unit/test_interface_docs.py` fails when the table is out of date.

## The contract is frozen

The shape of an interface record is **frozen**. Adding a field, renaming one,
or changing what a field means is a breaking change for every consumer
(navigation, shortcuts, help, visibility, the evidence report) and needs a
deliberate migration of all of them together.

Adding a new *screen* is not a contract change. To add one:

1. **Register** it in `core/interfaces/registry.py` with `_if(...)`: a unique
   `interface_id`, a `Domain`, the Flask endpoint that renders it, a bare
   Bootstrap icon name (`bi-folder`, never `bi bi-folder`), a lifecycle
   status and, when it is not for everyone, a `required_role`.
2. **Navigation** picks it up automatically: `core/interfaces/navigation.py`
   builds the sidebar and the command palette from the registry, filtered by
   role and lifecycle status.
3. A **shortcut** (`keyboard_shortcut`) is optional; the validator rejects a
   malformed sequence (`invalid_shortcut`).
4. Point its **help** at a topic of the help tree (`help_topic`); the
   validator rejects unknown and duplicated topics.
5. **Visibility** is enforced twice: the registry hides the entry from roles
   that may not use it, and the route itself is protected by the default-deny
   authentication layer (`core/security/flask_ext.py`). Hiding a link is never
   the access control.

`validate_registry()` (`core/interfaces/validation.py`) checks every record:
unique ids, known domains and roles, bare icon names, existing endpoints,
help topics, shortcuts, setting references, and feature dependencies (unknown,
self-referencing or cyclic). A page that renders HTML but is not registered
fails `tests/integration/test_interface_coverage.py`
(`unowned_user_interfaces`).

## Lifecycle

The meaning of each status is a value in `core/interfaces/lifecycle.py`, and
`Interface.navigable` defers to it (`policy(self.status).navigable`).

| Status | Navigable | Meaning |
| --- | --- | --- |
| `ACTIVE` | yes | Supported and shown to every permitted role. |
| `EXPERIMENTAL` | only while its feature flag is on | Not finished; an experimental record without a flag is a validation error. |
| `DEPRECATED` | yes | Still works and is still supported, marked with a Deprecated badge, and scheduled for removal. |
| `RETIRED` | no | No longer part of the product; a stored setting for it is kept but cannot bring it back. |

## Legacy metadata

Before the registry, page metadata was scattered across templates and a
`INTERFACE_METADATA` dictionary. The remaining legacy identifiers are listed
in `LEGACY_INTERFACE_IDS` and resolve through `resolve_interface_id()`; see
`docs/REGISTRY_EVIDENCE.md` for what is still legacy.

## Generated reference

<!-- BEGIN GENERATED REGISTRY TABLE -->
- Interfaces: **26** (features declared separately: **1**)
- Endpoints owned: **67**
- With a keyboard shortcut: **12**; with a help topic: **24**
- By domain: ADMINISTRATION 1, ANALYZE 3, CLASSIFY 2, DISCOVER 9, INGEST 2, INTERNAL 1, OPERATE 2, REPORT 2, SETTINGS 3, WORK 1
- By status: ACTIVE 25, DEPRECATED 1
- By kind: INTERNAL 1, PAGE 24, SECTION 1


### WORK (1)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dashboard` | Dashboard | WORK | `index` | — | any | on | — | work/dashboard | g w | ACTIVE |

### DISCOVER (9)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `file_library` | File Library | DISCOVER | `files.files_list` | `files.file_detail`, `files.file_content_lazy`, `files.file_content_page`, `files.file_full_content`, `files.file_search_all_pages`, `files.file_chart_data`, `files.file_types_page`, `files.delete_file`, `files.bulk_delete_files`, `files.bulk_export_files` | any | on | — | discover/file-library | g f | ACTIVE |
| `search` | Search | DISCOVER | `search_page` | `search_advanced`, `search_enhanced_page`, `saved_searches_page`, `search_advanced_api` | any | on | — | discover/search | g s | ACTIVE |
| `sources` | Sources | DISCOVER | `sources_list` | `source_add`, `source_detail`, `source_edit`, `source_categories_keywords` | any | on | — | discover/sources | — | ACTIVE |
| `sides` | Sides | DISCOVER | `sides_list` | `side_add`, `side_detail`, `side_edit`, `side_categories_keywords` | any | on | — | discover/sides | — | ACTIVE |
| `keywords` | Keywords | DISCOVER | `keywords_list` | `keyword_detail`, `keywords_add` | any | on | — | discover/keywords | — | ACTIVE |
| `words` | Words | DISCOVER | `words_list` | `word_detail`, `words_add` | any | on | — | discover/words | — | ACTIVE |
| `categories` | Categories | DISCOVER | `categories_list` | `category_words`, `category_add` | any | on | — | discover/categories | — | ACTIVE |
| `email_words` | Email Words | DISCOVER | `email_words` | — | any | on | — | discover/email-words | — | ACTIVE |
| `notifications` | Notifications | DISCOVER | `notifications_page` | — | any | on | — | discover/notifications | g n | ACTIVE |

### INGEST (2)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `input_ingestion` | Input / Ingestion | INGEST | `operations_input_page` | `files.upload_page`, `files.get_active_tasks`, `files.upload_progress`, `files.api_cancel_task`, `files.pause_task`, `files.resume_task` | any | on | — | ingest/input | g i | ACTIVE |
| `import_center` | Import Center | INGEST | `operations_import_page` | — | any | on | — | ingest/import-center | — | ACTIVE |

### ANALYZE (3)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `archives` | File Management and Analysis | ANALYZE | `archives_page` | — | any | on | `file_library` | analyze/archives | g a | ACTIVE |
| `path_analysis` | Path Analysis | ANALYZE | `path_analysis_page` | — | any | on | `file_library` | analyze/path-analysis | g p | ACTIVE |
| `batch_analysis` | Batch Analysis | ANALYZE | `analysis_batch` | `analysis_batch_process` | any | on | `file_library` | analyze/batch | g b | ACTIVE |

### CLASSIFY (2)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `analyst_categorization` | Analyst Categories | CLASSIFY | `analyst_categorization_page` | — | any | on | `file_library` | classify/analyst-categories | — | ACTIVE |
| `classification` | Classification | CLASSIFY | `file_classification_page` | — | any | on | `file_library`, `words` | classify/classification | — | ACTIVE |

### REPORT (2)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `comprehensive_dashboard` | Comprehensive Dashboard | REPORT | `comprehensive_dashboard` | — | any | on | `file_library` | report/detailed-dashboard | — | ACTIVE |
| `charts_dashboard` | Charts Dashboard | REPORT | `charts_dashboard` | — | any | on | `file_library` | report/charts | — | ACTIVE |

### OPERATE (2)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `jobs` | Jobs | OPERATE | `operations_jobs_page` | `operations_job_detail_page` | any | on | — | operate/jobs | g j | ACTIVE |
| `import_export_console` | Import/Export | OPERATE | `import_export_page` | — | admin | on | — | — | — | DEPRECATED |

### ADMINISTRATION (1)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `users` | User Management | ADMINISTRATION | `users_page` | — | admin | on | — | administration/users | g u | ACTIVE |

### SETTINGS (3)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `settings` | Settings | SETTINGS | `settings_page_direct` | `settings_api.settings_page` | admin | on | — | settings/overview | g , | ACTIVE |
| `interface_manager` | Interface Manager | SETTINGS | — | — | admin | on | `settings` | settings/interfaces | — | ACTIVE |
| `translation_manager` | Translation Management | SETTINGS | `translations.translation_management_page` | — | admin | on | — | settings/translations | g t | ACTIVE |

### INTERNAL (1)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `concurrency_monitor` | Concurrency Monitor | INTERNAL | `concurrency.dashboard` | `concurrency.get_metrics`, `concurrency.get_threads`, `concurrency.get_processes`, `concurrency.get_pools`, `concurrency.get_async_tasks` | admin | on | — | — | — | ACTIVE |

### Features (not interfaces)

| ID | Name | Description | Default |
| --- | --- | --- | --- |
| `page_tips` | Page Tips & Documentation | Explanatory tips at the top of each page, describing the elements on it, how to use them and how to add data. | on |
<!-- END GENERATED REGISTRY TABLE -->
