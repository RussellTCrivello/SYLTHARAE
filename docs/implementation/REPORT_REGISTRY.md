# Report registry (step 13)

`core/reporting/` holds the declarations every later reporting step builds
on: **what a report is**, **which data it reads**, **who may run it** and
**what its numbers mean**. It executes nothing. The runner (step 14) binds and
runs the declared SQL inside the existing JobManager
([REPORT_RUNS.md](REPORT_RUNS.md)); artifacts and manifests are step 15
([REPORT_ARTIFACTS.md](REPORT_ARTIFACTS.md)).

## Why a new package, not an extension

Before this step the repository had no report registry, runner, dataset
model or artifact store (`STATE_INVENTORY.md`, row "Reporting"). The pieces
it reuses instead of duplicating:

| Need | Reused |
| --- | --- |
| Matching set for criteria-driven data | `core.criteria.compile_criteria` (the one compiler), including its `AccessScope` predicate and total ordering |
| Roles | `core.security.service.ALL_ROLES` |
| Criteria validation and fingerprints | `core.criteria.model.from_dict`, `sha256_hex` |
| Translation catalogs | `translations/*` (babel `read_po`) |

## Model

| Object | Declares |
| --- | --- |
| `ReportDefinition` | `report_id`, `version`, `status` (active/superseded), title/description msgids, `help_topic`, `unit`, `roles`, `parameters`, `datasets` (`id@version`) |
| `Dataset` | `dataset_id`, `version`, description, `unit`, `semantics`, `row_limit`, `columns` (name, type, nullability, label msgid), parameterised `sql`, `sql_params`, `roles`, and **one** access mechanism |
| `Parameter` | name, type (`criteria`/`integer`/`date`/`enum`/`boolean`/`text`), label msgid, required/default, choices, bounds, max length |
| `HelpTopic` | topic, title msgid, summary msgid |

### SQL rules (enforced at construction)

* Only `%s` placeholders; no `%(name)s`; one statement (no `;`).
* The only substitutions are the slots `{where}`, `{order}` (both from the
  criteria compiler) and `{scope}` (from the caller's `AccessScope`). Any
  other `{...}` is refused.
* `sql_params` lists, in `%s` order, report parameter names and the tokens
  `@criteria` (the compiled WHERE's values), `@scope` and `@limit`.
  The `%s` count must match.
* The SQL must end in `LIMIT %s`, bound to `@limit`, the last token. The
  binder supplies `row_limit + 1`.

### Access before retrieval

Every dataset declares exactly one of:

1. `criteria_param`: WHERE and ORDER BY come from `compile_criteria`, which
   compiles `AccessScope.allowed_source_ids` into SQL. Such a dataset adds
   `{scope}` only when it also declares `scope_column`, and only for rows
   it reads *outside* its criteria (step 16: the keyness reference corpus).
   An undeclared `{scope}` is refused.
2. `scope_column` (`alias.column`): `{scope}` becomes `TRUE`, `FALSE` or
   `col = ANY(%s)`.
3. `unscoped_reason`: a written reason why nothing source-scoped is read
   (for example gazetteer reference data). It cannot be combined with 1 or 2.

### Analyses (step 16)

A report may list analyses (`ReportDefinition.analyses`, keys `id@version`,
declared in `core/reporting/definitions.py` as `core.analytics.kinds.Analysis`).
`validate()` checks the following:

* every listed analysis is registered and every registered one is used;
* each input is a dataset of the same report, with the semantics and columns
  the kind requires;
* every kind parameter is declared by the report.

Every template msgid, analysis title and enum choice label passes the
translation gate. The lock pins analyses in their own section. A report's
fingerprint covers its analyses only when it has some, so reports without
analyses keep their released fingerprints. See [ANALYTICS.md](ANALYTICS.md).

### Row-limit semantics

| `semantics` | Meaning | Runner obligation (step 14/15) |
| --- | --- | --- |
| `exact` | All rows or failure | fetched `row_limit + 1` rows means **fail**, never truncate |
| `capped` | At most `row_limit` rows, in declared order | extra row means `truncated = true` in the manifest |
| `top_n` | A ranked prefix of length `row_limit` | manifest states "top N" |

`capped` and `top_n` must contain `ORDER BY`.

## Validation (`ReportRegistry`)

`validate()` returns *every* problem:

* unique report and dataset `id@version`, and unique help topics;
* one active version per report, with versions contiguous from 1 (a released
  version may not disappear);
* every referenced dataset is registered, and every registered dataset is
  used;
* every dataset parameter is declared by the report, with compatible
  criteria/plain typing, and every report parameter is read;
* the report unit equals its primary dataset's unit;
* roles are known to `core.security`, and the report's roles are a subset of
  every dataset's roles;
* help topics are declared and used.

`validate_translations()` checks that every msgid is in `messages.pot`,
present in `en`, and in `ar`/`he`/`fa`/`hr` present, non-empty, not fuzzy,
different from the msgid, with identical placeholders.

`validate_lock()` compares every `id@version` fingerprint with
`core/reporting/definitions.lock.json`. It reports a changed fingerprint
("changed meaning: declare a new version"), an unpinned entry and a pinned
entry that vanished.

### Fingerprints

`Dataset.semantic()` covers id, version, unit, semantics, row limit, column
name/type/nullability, whitespace-normalised SQL, `sql_params`, roles,
parameters and the access mechanism. `ReportDefinition.semantic()` covers id,
version, unit, roles, parameter specs, and each dataset key *with its
fingerprint*, so editing a dataset changes every report that uses it.
Excluded, because presentation is not meaning: descriptions, titles and
labels.

```
python -m core.reporting.lock --check   # all validations; exit 1 on any problem
python -m core.reporting.lock --write   # pin new id@version only; refuses rewrites
```

The lock file is read by the check and the tests only. The running
application never reads it, so packaging does not depend on it. It is
tracked, so it ships in the `git archive` release anyway.

## Registered now

| Report | Unit | Roles | Datasets |
| --- | --- | --- | --- |
| `search_results@1` | path | admin, analyst, viewer | `search_results.matches@1` (capped 5000, criteria-compiled order), `search_results.count@1` (exact) |
| `keyword_intelligence@1` | keyword | admin, analyst, viewer | `keyword_intelligence.matches@1` (capped 1000, most contents first) |
| `category_analysis@1` | category | admin, analyst, viewer | `category_analysis.summary@1` (capped 1000, most contents first) |
| `horizon@1` | signal | admin, analyst, viewer | `horizon.signals@1` (capped 5000, event date soonest first; `as_of` reference date is a required recorded parameter) |
| `entity_place@1` | place | admin, analyst, viewer | `entity_place.mentions@1` (capped 2000, most identified first) |
| `relationship@1` | context | admin, analyst, viewer | `relationship.contexts@1` (capped 5000, most cross-posted first) |

`search_results@1` is the registry's reference definition: it needs no
analytics and reuses the compiler end to end. The two step-17 families read
the content-identity stores the compiler itself uses: Keyword Intelligence
aggregates `keywords_hashs` (one row per keyword, its decoded pattern, its
category, distinct matched contents, summed counts with the never-measured
counts carried separately) and Category Analysis aggregates
`words_hashs` x `words_categorys` (one row per category; categories overlap
when a word belongs to several, so contents count in every category that
applies, shares are per category against the same matched set - their sum
may exceed 100% - and the share is NULL over an empty matched set: unknown,
never zero). The Horizon family lists resolved temporal signals over the
matched contents, bucketed by the Signal Explorer's *own* bucket definition -
`core/detection/horizon.py`, imported by both the explorer query and the
dataset, never restated - against the run's declared reference date (`as_of`),
which is recorded with the run so the bucketing is reproducible; undated
signals and purely past mentions stay out of the horizon exactly as on the
page. The Entity & Place family reads the gazetteer candidates
(`content_signal_places`: one candidate = identified, several = ambiguous,
kept and never picked), splitting identified from ambiguous per place -
ambiguity is never resolved in SQL; unresolved mentions (no candidates) are
out of per-place attribution by declaration; unprovenanced confidence is
carried as unknown; retired places are marked, not hidden. The Relationship
family counts *contexts* - the (hash_id, source_id, side_id) triple of
`hash_contexts`: multiplicity inside a context stays a column (`paths`),
repeated identical triples never become extra rows, a different source or
side is a different context, and the cross-posting measure counts distinct
sibling contexts and sibling sources within the matched set. The remaining
catalog families (Latest/Change, Scenario Outcome, Comprehensive) are added
only when their datasets exist and are verified.

## Evidence

* `tests/unit/test_report_registry.py` (111 tests): the shipped registry
  passes all three validations and the CLI; each validation rule, SQL rule,
  parameter rule and lock rule has a negative control; fingerprint
  stability and sensitivity; binding keeps hostile input in parameters.
* `tests/integration/test_report_registry_pg.py` (10 tests, PostgreSQL): every
  registered dataset executes with its declared column names and order;
  column type OIDs match the declaration; no NULL in non-nullable columns;
  the listing equals the compiler's own count; order is total (a date tie is
  broken by id); the access scope restricts rows in SQL; the `row_limit + 1`
  overflow row is visible; a hostile phrase is data. Negative control run
  by hand: declaring `file_date` as `text` fails on OID 1082. The step-17
  additions assert: every keyword listed over the matched set including the
  zero-presence one (a measured zero), the never-measured count carried in
  `unknown_count_rows`, the decoded pattern in stored word order,
  distinct-content counts with overlap preserved (contents count in every
  category that applies; per-category shares summing beyond 100%), the empty
  matched set yielding measured zeros and a NULL share, the access scope
  narrowing both new datasets before retrieval, and the capped overflow row.
* `tests/unit/test_report_catalog_keyword_category.py` (14): the two
  definitions' contracts (units, roles, datasets, capped semantics, the
  nullable share, access through the criteria compiler, hostile phrase bound
  as data, content-level stores only) and the full-registry validation.
* `tests/integration/test_report_catalog_api.py` (6, over HTTP): both
  definitions listed with their contracts; runs submitted through
  `POST /api/reports/runs` execute on one snapshot with the expected rows
  (decoded pattern, overlap, unknown share); viewer role reads but may not
  write; the JSON artifact, its manifest (report id/version/unit, criteria
  fingerprint, snapshot, per-dataset row counts) and the offline
  verification agree.
* Horizon (step 17): `test_report_catalog_horizon.py` (11): the bucket SQL is
  the imported core definition, temporal signals only, undated and past out
  of scope, `as_of` a required bound date parameter, provenance columns
  declared nullable. PG: the corpus seeds overdue/week/month/quarter/later
  signals plus a past mention and an undated signal - the five horizon
  buckets land exactly, order is soonest-first, and a later reference date
  moves the buckets without dropping rows. Over HTTP: the run records
  `as_of`, buckets come back as declared, and a run without the reference
  date is refused (400) before anything executes.
* Entity & Place (step 17): `test_report_catalog_entity_place.py` (11): the
  dataset reads candidates not resolutions, identified/ambiguous are
  distinct columns, unprovenanced confidence carried, place signals only.
  PG (registry suite, 19): an ambiguous mention leaves both candidate
  places with identical ambiguous counts and neither gains an identified
  count; the unprovenanced mention lands in `unknown_confidence_occurrences`;
  the unresolved mention appears in no row; the retired place is marked.
  Over HTTP: identified 1 / ambiguous 1 for the same place.
* Relationship (step 17): `test_report_catalog_relationship.py` (8): rows
  come from `hash_contexts`, sibling sources exclude the row's own source,
  contexts capped and criteria-scoped. PG (registry suite, 21): one content
  on two sources yields exactly two context rows with the path multiplicity
  as a column (2 + 1); single-context contents have zero siblings; ordered
  most cross-posted first. The suite's own first draft caught the dataset
  driving from paths (duplicate rows per context) - fixed to drive from
  contexts before landing. Over HTTP: sibling contexts/sources over a run.

## Limitations

* **Nullability is checked empirically.** Tests assert that no NULL appears
  in non-nullable columns of the seeded result; they do not derive
  nullability from the plan. Every column of `search_results` and
  `keyword_intelligence.matches` is NOT NULL through foreign keys (see
  comments in `datasets.py`); `category_analysis.summary.content_share` is
  the first declared-nullable column (NULL over an empty matched set) and is
  covered by an executed assertion rather than a negative control.
* **The compiler's semantics are not in the dataset fingerprint.** The
  fingerprint pins the declared SQL (including `CANONICAL_FROM`, which is
  interpolated). A change inside `compile_criteria` is not pinned here; each
  run records the criteria fingerprint and each dataset's query fingerprint
  (step 14), so a changed compilation is visible as a different query
  fingerprint. A compiler version constant does not exist yet.
* **API.** The definitions listing and run submission arrived with the
  runner (step 14, `Api/routes/reports.py`).
* **Translations are self-authored**, not native-reviewed.
* **The existing Arabic string for "Side"** (reused as a column label) is
  `الجوانب` ("sides", plural). It predates this step and is left unchanged
  because other pages share it; it is added to the Arabic defect list for
  step 18.

## Runs

Execution of registered definitions (step 14) is described in
[REPORT_RUNS.md](REPORT_RUNS.md): the `report_run` job, one
`REPEATABLE READ, READ ONLY` snapshot per run, stored datasets with their
fingerprints and truncation, the `/reports` page and its API.
