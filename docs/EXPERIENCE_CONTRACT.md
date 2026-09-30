# Experience Contract

The Experience Contract describes how each screen is *presented*: its title,
navigation entry, help topic, keyboard shortcut and the translation keys its
strings need. It is not a second registry. `core/experience/contract.py`
joins two sources and says which one every part came from:

* the **Interface Registry** (`core/interfaces/`) says what a screen is;
* the **declarations** (`core/experience/declarations.py`) say what somebody
  has described about how it is presented.

Where nothing is declared, the contract still answers with the registry's own
words and marks the screen `declared = False`, so "described" and "derived"
stay distinguishable in the audit.

The reference below is generated. Every count in it comes from the code:

    python3 -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md

## Rules

* A screen's strings are looked up by translation key; a key without a
  translation in a supported locale is reported, never silently replaced.
* User-visible errors use the shared `client_error` shape; exception text
  (`str(e)`) is logged, not shown.
* The contract API (`/api/experience/...`) is read-only.

## Generated reference

<!-- BEGIN GENERATED EXPERIENCE CONTRACT -->
### What this build declares

- Contracts: **33** (described: **5**, derived from the registry only: **28**)
- Definitions: **34** actions, **27** columns, **6** filters, **0** fields, **5** states
- Translation keys the screens need: **245**
- With help: **31**; with a shortcut: **17**; with a navigation entry: **33**

### Every screen

| Interface | Domain | Declared | Title key | Columns | Filters | Actions | States | Help |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dashboard` | WORK | derived | `screen.dashboard.title` | — | — | — | — | yes |
| `file_library` | DISCOVER | yes | `screen.file_library.title` | 7 | 4 | 12 | 1 | yes |
| `search` | DISCOVER | derived | `screen.search.title` | — | — | — | — | yes |
| `sources` | DISCOVER | yes | `screen.sources.title` | 8 | — | 4 | 1 | yes |
| `sides` | DISCOVER | yes | `screen.sides.title` | 5 | — | 4 | 1 | yes |
| `keywords` | DISCOVER | yes | `screen.keywords.title` | 4 | 2 | 6 | 1 | yes |
| `words` | DISCOVER | yes | `screen.words.title` | 3 | — | 7 | 1 | yes |
| `categories` | DISCOVER | derived | `screen.categories.title` | — | — | — | — | yes |
| `email_words` | DISCOVER | derived | `screen.email_words.title` | — | — | — | — | yes |
| `analyst_categorization` | CLASSIFY | derived | `screen.analyst_categorization.title` | — | — | — | — | yes |
| `notifications` | DISCOVER | derived | `screen.notifications.title` | — | — | — | — | yes |
| `signal_horizon` | DISCOVER | derived | `screen.signal_horizon.title` | — | — | — | — | yes |
| `monitoring` | DISCOVER | derived | `screen.monitoring.title` | — | — | — | — | yes |
| `input_ingestion` | INGEST | derived | `screen.input_ingestion.title` | — | — | — | — | yes |
| `import_center` | INGEST | derived | `screen.import_center.title` | — | — | — | — | yes |
| `archives` | ANALYZE | derived | `screen.archives.title` | — | — | — | — | yes |
| `path_analysis` | ANALYZE | derived | `screen.path_analysis.title` | — | — | — | — | yes |
| `batch_analysis` | ANALYZE | derived | `screen.batch_analysis.title` | — | — | — | — | yes |
| `classification` | CLASSIFY | derived | `screen.classification.title` | — | — | — | — | yes |
| `comprehensive_dashboard` | REPORT | derived | `screen.comprehensive_dashboard.title` | — | — | — | — | yes |
| `reports` | REPORT | derived | `screen.reports.title` | — | — | — | — | yes |
| `retention` | ADMINISTRATION | derived | `screen.retention.title` | — | — | — | — | yes |
| `schedules` | REPORT | derived | `screen.schedules.title` | — | — | — | — | yes |
| `charts_dashboard` | REPORT | derived | `screen.charts_dashboard.title` | — | — | — | — | yes |
| `detection` | OPERATE | derived | `screen.detection.title` | — | — | — | — | yes |
| `jobs` | OPERATE | derived | `screen.jobs.title` | — | — | 1 | — | yes |
| `import_export_console` | OPERATE | derived | `screen.import_export_console.title` | — | — | — | — | — |
| `users` | ADMINISTRATION | derived | `screen.users.title` | — | — | — | — | yes |
| `audit_log` | ADMINISTRATION | derived | `screen.audit_log.title` | — | — | — | — | yes |
| `settings` | SETTINGS | derived | `screen.settings.title` | — | — | — | — | yes |
| `interface_manager` | SETTINGS | derived | `screen.interface_manager.title` | — | — | — | — | yes |
| `translation_manager` | SETTINGS | derived | `screen.translation_manager.title` | — | — | — | — | yes |
| `concurrency_monitor` | INTERNAL | derived | `screen.concurrency_monitor.title` | — | — | — | — | — |

### Translation coverage, measured

Counted from the catalogs the build ships - the Babel catalogs under `translations/` and the JavaScript UI packs under `static/js/i18n/locales` - against the strings in the message template. `translated` excludes strings that are identical to the source, which are counted as `fallback`.

| Language | Catalog entries | Source strings | Translated | Fallback | Missing | Coverage | Of which translated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ar` | 3675 | 3314 | 3305 | 9 | 0 | 100.0% | 99.7% |
| `fa` | 3675 | 3314 | 3307 | 7 | 0 | 100.0% | 99.8% |
| `he` | 3675 | 3314 | 3301 | 13 | 0 | 100.0% | 99.6% |
| `hr` | 3431 | 3314 | 3232 | 22 | 60 | 98.2% | 97.5% |

### Coverage per screen

The strings a screen's contract asks for, and how many of them a language actually translates. Looked up by semantic key first and by the English source string second, because the catalogs still key by source string today; `by key` is the part of the migration that has happened.

| Interface | Keys | ar | fa | he | hr |
| --- | --- | --- | --- | --- | --- |
| `dashboard` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `file_library` | 32 | 90.6% (0 by key) | 90.6% (0 by key) | 90.6% (0 by key) | 90.6% (0 by key) |
| `search` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `sources` | 19 | 94.7% (0 by key) | 94.7% (0 by key) | 94.7% (0 by key) | 94.7% (0 by key) |
| `sides` | 16 | 93.8% (0 by key) | 93.8% (0 by key) | 93.8% (0 by key) | 93.8% (0 by key) |
| `keywords` | 21 | 71.4% (0 by key) | 71.4% (0 by key) | 71.4% (0 by key) | 71.4% (0 by key) |
| `words` | 19 | 78.9% (0 by key) | 73.7% (0 by key) | 73.7% (0 by key) | 73.7% (0 by key) |
| `categories` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `email_words` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `analyst_categorization` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `notifications` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `signal_horizon` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `monitoring` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `input_ingestion` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `import_center` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `archives` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `path_analysis` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `batch_analysis` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `classification` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `comprehensive_dashboard` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `reports` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `retention` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `schedules` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `charts_dashboard` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `detection` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `jobs` | 7 | 71.4% (0 by key) | 71.4% (0 by key) | 71.4% (0 by key) | 71.4% (0 by key) |
| `import_export_console` | 3 | 66.7% (0 by key) | 66.7% (0 by key) | 66.7% (0 by key) | 66.7% (0 by key) |
| `users` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `audit_log` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `settings` | 5 | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) | 80.0% (0 by key) |
| `interface_manager` | 5 | 0.0% (0 by key) | 0.0% (0 by key) | 0.0% (0 by key) | 0.0% (0 by key) |
| `translation_manager` | 5 | 100.0% (0 by key) | 100.0% (0 by key) | 100.0% (0 by key) | 100.0% (0 by key) |
| `concurrency_monitor` | 3 | 0.0% (0 by key) | 0.0% (0 by key) | 0.0% (0 by key) | 0.0% (0 by key) |

### Screens nobody has described yet

These have a contract derived from the registry, so they work: they have an identity, a navigation entry, a lifecycle and a help topic. What they do not have is a description of what they offer - columns, filters, actions, states - because nobody has decided it. The list is the remaining work, not a defect.

`dashboard`, `search`, `categories`, `email_words`, `analyst_categorization`, `notifications`, `signal_horizon`, `monitoring`, `input_ingestion`, `import_center`, `archives`, `path_analysis`, `batch_analysis`, `classification`, `comprehensive_dashboard`, `reports`, `retention`, `schedules`, `charts_dashboard`, `detection`, `jobs`, `import_export_console`, `users`, `audit_log`, `settings`, `translation_manager`

### Contract validation

Every contract passes the declarative checks: no SQL, no imports, no calls, no authorisation decisions, destructive actions carry a confirmation, bulk actions require a selection, and no key holds two different source strings.

_Generated from 33 contracts, 3314 source strings and 5 catalogs._
<!-- END GENERATED EXPERIENCE CONTRACT -->
