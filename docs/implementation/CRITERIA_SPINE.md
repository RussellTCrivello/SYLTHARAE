# The criteria spine (Phase 0)

One definition of "which records", used by every subsystem that selects
records: interactive search, saved searches, exports, and — as they are
built — monitoring rules, reports and dashboards. Code: `core/criteria/`.

## Why it exists

Before this work, "the set of documents matching X" was implemented once in
`Api/services/search_service.py` and re-described informally elsewhere
(`saved_searches.json` stored the page's raw filter dictionary). A report or
monitor built on that would have been a *second* query language that drifts
from search. The spine makes drift a test failure instead.

## The three pieces

| Piece | Module | Contract |
|---|---|---|
| Canonical object | `core/criteria/model.py` | `Criteria` — frozen, validated, canonicalised. `fingerprint()` = SHA-256 of `canonical_json()`. `from_dict()` is strict: unknown keys, bad ids, impossible dates and unsupported features raise `CriteriaError` with a reason. |
| Shared SQL vocabulary | `core/criteria/sql.py` | The predicate builders (`text_match_clause`, `parse_boolean_expression`, `filter_predicates`, `escape_like`, …). **Interactive search imports these** (`search_service` aliases them), so search and the compiler cannot disagree about what a filter means. |
| Compiler | `core/criteria/compiler.py` | `compile_criteria(criteria, scope)` → `CompiledQuery` (`count_sql()`, `ids_sql(limit, offset)`, `explain()`). Parameterised only; `AccessScope` is **required** and compiled into the `WHERE` clause; ordering always ends with `p.id ASC`. |

## Fields

`text` (boolean expression: `AND`/`OR`/`NOT`, quotes, parentheses — the
search box grammar), `phrases`, `keywords` + `keyword_logic` (`OR`/`AND`/`NOT`
over `keywords_hashs`), `categories`, `include_child_categories` (**refused**:
the schema has no category hierarchy), `analyst_categories`, `analyst_scope`
(`all`/`uncategorized`/`categorized`), `sources`, `sides`, `file_types`,
`file_statuses` (`None` = unfiltered, `[]` = matches nothing — they are
different and fingerprint differently), `date_field`
(`file_date`/`content_date`/`context_date`), `date_from`/`date_to`,
`unit` (`path` = each occurrence; `hash` = each distinct content once),
`hide_duplicates`, `case_sensitive`, `whole_word`, `sort`, `options`
(fuzzy/expansion/BM25/threshold — interactive ranking aids), `schema_version`.

## Deliberate differences, stated not hidden

`CompiledQuery.explain()` prints these for every compiled query:

* **Ranking is search-only.** Relevance order exists only in interactive
  search; compiled queries order `relevance` as `p.file_date DESC, p.id ASC`.
* **Fuzzy matching and query expansion are interactive-only.** Compiled
  matching is literal. A monitor that silently matched fuzzily would alert on
  things the analyst never searched for.
* **`hide_duplicates` is honoured through `unit='hash'`** in compiled queries.
* **Scope defaults.** A legacy saved search without a scope maps to
  `uncategorized` — the FR-2.1 default the page actually replays
  (`f.scope || 'uncategorized'`). Unknown scopes never widen to `all`. One
  normaliser serves search and criteria (`normalize_analyst_scope`).

## Permission model

`AccessScope(user_id, role, allowed_source_ids)`. `allowed_source_ids=None`
means unrestricted (today every authenticated role can read every source —
the installation has no per-source ACL); a tuple restricts, and an empty
tuple compiles to `1=0`. Omitting the scope raises: there is no default that
reads everything. When per-source ACLs are introduced, only the scope
constructor changes; every consumer is already filtered before retrieval.

## Saved searches (directive step 4)

`saved_searches` (m0016) replaces `data/saved_searches.json`. Each row keeps
the page's `query` + `filters` verbatim (the UI replays them) **and** the
canonical `criteria` + `criteria_fingerprint`, so a saved search is
monitor-capable. The legacy file is imported once (idempotent by file
SHA-256 and by `(legacy_source, legacy_id)`), failures are recorded in
`saved_search_imports.failures`, and the file is left in place as a backup.
Legacy entries without an owner (saved before per-user accounts) are
imported unowned and visible to administrators only. The by-id routes now
return 404 for another user's search (previously any authenticated user
could read, rename or delete any saved search).

## Disclosure register (directive step 5)

`core/security/disclosure.py` writes `DATA_EXPORTED` to `audit_log` for
every attachment response, centrally, fail-closed (see `docs/SECURITY.md`).
Search exports add `criteria_fingerprint` — the same value a saved search
with the same conditions carries (pinned by
`test_export_and_saved_search_share_one_fingerprint`).

## Verification

```
python -m pytest tests/unit/test_criteria_spine.py            # model + compiler, no DB
python -m pytest tests/integration/test_criteria_spine_pg.py  # parity with search on PostgreSQL
python -m pytest tests/integration/test_saved_searches_pg.py  # m0016, import, ownership
python -m pytest tests/integration/test_disclosure_audit.py   # DATA_EXPORTED
python -m pytest tests/integration/test_migration_upgrade_path.py
```

## Known limitations

* Per-source access control does not exist in the product; `AccessScope` is
  ready for it but every role is currently unrestricted.
* Search history (`data/search_history.json`) is still file-backed; it is not
  a selection definition and was out of scope for this step.
* A legacy-file entry deleted after import *would* be re-imported if an
  operator hand-edited the (no longer written) JSON file so its SHA changed
  and removed the database row; the unchanged file is never re-imported.
