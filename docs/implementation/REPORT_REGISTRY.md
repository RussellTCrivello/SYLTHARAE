# Report registry (step 13)

`core/reporting/` holds the declarations every later reporting step builds
on: **what a report is**, **which data it reads**, **who may run it** and
**what its numbers mean**. It executes nothing. The runner (step 14) binds and
runs the declared SQL inside the existing JobManager; artifacts and manifests
are step 15.

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
   compiles `AccessScope.allowed_source_ids` into SQL. Such a dataset may
   not add its own `{scope}`.
2. `scope_column` (`alias.column`): `{scope}` becomes `TRUE`, `FALSE` or
   `col = ANY(%s)`.
3. `unscoped_reason`: a written reason why nothing source-scoped is read
   (for example gazetteer reference data).

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

This is the registry's reference definition: it needs no analytics and reuses
the compiler end to end. The other nine catalog reports (step 17) are added
only when their datasets exist and are verified.

## Evidence

* `tests/unit/test_report_registry.py` (111 tests): the shipped registry
  passes all three validations and the CLI; each validation rule, SQL rule,
  parameter rule and lock rule has a negative control; fingerprint
  stability and sensitivity; binding keeps hostile input in parameters.
* `tests/integration/test_report_registry_pg.py` (6 tests, PostgreSQL): every
  registered dataset executes with its declared column names and order;
  column type OIDs match the declaration; no NULL in non-nullable columns;
  the listing equals the compiler's own count; order is total (a date tie is
  broken by id); the access scope restricts rows in SQL; the `row_limit + 1`
  overflow row is visible; a hostile phrase is data. Negative control run
  by hand: declaring `file_date` as `text` fails on OID 1082.

## Limitations

* **Nullability is checked empirically.** Tests assert that no NULL appears
  in non-nullable columns of the seeded result; they do not derive
  nullability from the plan. Every column of the two current datasets is
  NOT NULL through foreign keys (see comments in `datasets.py`), so there is
  no nullable column to negative-control yet.
* **The compiler's semantics are not in the dataset fingerprint.** The
  fingerprint pins the declared SQL (including `CANONICAL_FROM`, which is
  interpolated). A change inside `compile_criteria` is not pinned here; each
  run will record the criteria fingerprint (step 15). A compiler version
  constant does not exist yet.
* **No API.** A definitions listing and run submission arrive with the
  runner (step 14), which is their first consumer.
* **Translations are self-authored**, not native-reviewed.
* **The existing Arabic string for "Side"** (reused as a column label) is
  `الجوانب` ("sides", plural). It predates this step and is left unchanged
  because other pages share it; it is added to the Arabic defect list for
  step 18.
