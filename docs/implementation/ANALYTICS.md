# Analytical engine (step 16)

Analyses turn a run's dataset rows into measures, and measures into a
five-voice narrative, without generating free text and without querying the
database a second time. This step builds the engine: the measures, the
sourced thresholds, the versioned templates, storage with the run and display
in every surface. It ships one complete analysis, **keyness**
(`term_keyness@1`), which proves the path end to end. The remaining catalog
reports (step 17) reuse the same pieces.

## Where it sits

```
definition (core/reporting)          run (m0022, one REPEATABLE READ snapshot)
  datasets ----------------------->    report_run_datasets (rows)
  analyses (core/analytics) ------->    report_run_analyses (m0026): measures, rows,
                                        narrative = template references
                                            |
                          API (rendered in the caller's language) / page /
                          JSON + HTML artifacts / every manifest
```

| Need | Existing infrastructure used | What was added |
| --- | --- | --- |
| Data for an analysis | the run's datasets, read in the run's single snapshot, with limits and access scope applied before retrieval | nothing is queried again. Analyses are pure functions of the stored rows |
| Versioning and meaning pins | `definitions.lock.json` and `python -m core.reporting.lock` | an `analyses` section. A report's fingerprint covers its analyses only when it has some, so `search_results@1` keeps its released fingerprint (tested) |
| Translation | the catalog gate `validate_translations`, extended to every template msgid, analysis title and enum choice label | 43 msgids in ar/he/fa/hr |
| Storage discipline | `report_run_datasets` write-once pattern (m0022) | `report_run_analyses` (m0026), same trigger pattern |
| Unknown ≠ zero | the "not measurable" state of the reporting model | per-analysis `state` + `reason`, enforced by CHECK |

New files: `core/analytics/{measures,thresholds,narrative,kinds}.py`,
`database/migrations/m0026_report_run_analyses.py` and
`tools/perf/keyness_perf.py`.

Changed: `core/reporting/{model,registry,lock,datasets,definitions,render}.py`,
`services/reporting/{runs,artifacts}.py`, `Api/routes/reports.py`,
`templates/Reports/reports.html` and `static/js/pages/reports-page.js`.

## Measures (`core/analytics/measures.py`)

These are pure functions over counts. None of them reads the database, the
clock or the environment. When a measure is undefined for its input, the
function returns `None` rather than 0, and callers report that as *not
measurable*. Every expected value in `tests/unit/test_analytics_measures.py`
was computed by hand from the published definition.

| Measure | Definition | Oracle in tests |
| --- | --- | --- |
| Log-likelihood G² | Read & Cressie form used by Rayson & Garside (2000) and the UCREL calculator. G² = 2(a ln(a/E1) + b ln(b/E2)), where E1 = c(a+b)/(c+d) and E2 = d(a+b)/(c+d). A zero cell contributes 0 | a=100, b=50, c=10,000, d=20,000 gives 100 ln 2 |
| Log Ratio | Hardie (2014): log2((a/c)/(b/d)). A zero frequency is replaced by 0.5 | the same counts give exactly 2 |
| TF-IDF | tf · ln(N/df) (Spärck Jones 1972) | 3 · ln 10 |
| HHI | sum of squared percentage shares, 0–10,000 | shares 50/30/20 give 3,800 |
| Vocabulary profile | tokens, types, type/token ratio, hapax | TTR 0.6 |
| Cohen's κ | (po − pe)/(1 − pe) | table 20/5/10/15 gives κ = 0.4 |
| Mann-Kendall | S, Var(S) with tie correction, Z with continuity correction | the series 1..10 gives S = 45, Var = 125 |
| Theil-Sen | median of pairwise slopes | slope 1, robust to one outlier |
| logDice | Rychlý (2008): 14 + log2(2f_xy/(f_x+f_y)) | 13 for 10/20/20 |
| Jaccard, coverage | overlap; measured / total with the unmeasured counted | an empty population has no share (not 0 %) |

Only keyness is wired into a report in this step. The other measures are
implemented and oracle-tested so that the catalog reports (step 17) use a
verified implementation rather than a new one. They are **not** yet exposed in
any report; see Limitations.

## Thresholds (`core/analytics/thresholds.py`)

Every threshold records its value, its meaning, its source and a caveat
wherever applying it here departs from the context it came from.

| Threshold | Value | Source | Caveat |
| --- | --- | --- | --- |
| G² p<0.05 / 0.01 / 0.001 | 3.84 / 6.63 / 10.83 | Rayson & Garside (2000); UCREL calculator | |
| G² p<0.0001 (the level used for findings) | 15.13 | Rayson, Berridge & Francis (2004), JADT, pp. 926–936 | every term is tested, so only this level counts as a finding |
| Log Ratio "twice as frequent" | 1.0 | Hardie (2014), CASS | an effect size, not a test |
| HHI highly concentrated | 1,800 | U.S. DOJ/FTC Merger Guidelines (2023), §2.1 | defined for market shares; applied here by analogy only |
| κ bands | 0.81 / 0.61 / 0.41 / 0.21 | Landis & Koch (1977) | the authors call them arbitrary |
| Mann-Kendall significance | abs(Z) > 1.96 | Mann (1945); Kendall (1975) | normal approximation from n ≥ 10 (Gilbert 1987) |

The keyness SQL counts significant terms using the literal value `15.13`. A
unit test pins that literal to `LL_P0001.value`, so neither can change alone.
Threshold values are part of each analysis fingerprint.

## Narrative (`core/analytics/narrative.py`)

Every analysis speaks in exactly five voices, in a fixed order:

1. **Measure**
2. **Finding**
3. **Confidence**, which cites the threshold applied and its source
4. **Consequence**, which says what the result implies and what it does not
5. **Caveat**, which states the measure's limits and a next step

A `TemplateSet` is reviewed code: a set id, a version, and sentence keys
`<voice>.<variant>` mapped to msgids with `%(name)s` placeholders. The kind's
code chooses one sentence per voice by fixed rules and supplies the measured
parameters. `compose` refuses the following:

* a missing voice or a wrong order;
* an unknown sentence;
* a missing or extra parameter (an unused value would be invisible);
* a parameter that is not text or a number.

**What is stored** is template references only: set, version, fingerprint,
and for each voice its key, msgid and parameters. Prose is never stored. The
text is rendered when displayed:

* the API renders through the caller's catalog, with numbers formatted by
  Babel;
* the HTML/JSON artifacts render in English until the multilingual renderer
  of step 18, and the JSON also keeps the references.

The same stored record therefore reads in English and in Arabic. This is
tested over HTTP: an identical `narrative`, a different text, and the
parameters intact.

**Consequence sentences are generic.** They say what the class of finding
means for the reader's work, and that the documents must be read before
drawing conclusions. They never claim a cause.

## Keyness (`term_keyness@1`)

*Which words do the selected documents use more (or less) often than the rest
of the collection the reader may see?*

**Corpora.**
- *Target*: the distinct contents selected by the criteria, compiled and
  access-scoped by the single criteria compiler.
- *Reference*: every other visible content. It is taken from `paths` →
  `hash_contexts` and scoped on `rpt_hc.source_id`, the same universe and the
  same scope column the compiler uses.
- A content with several paths or contexts counts once (tested with a content
  that has two contexts).

**Counts.** Counts come from `words_hashs.word_count`. Rows with a NULL count
are **unknown**: they are left out of every sum and counted separately in
`unknown_count_rows`, and the Caveat voice then says how many were excluded.

**Datasets.**
- `term_keyness.ranked@1` is `top_n` with 200 rows. It returns the terms in
  the requested direction (`over` or `under`), ranked by G² descending and
  then by term, which is unique. Terms with equal relative frequency belong to
  neither direction.
- `term_keyness.totals@1` is `exact` with 1 row. It holds the corpus sizes,
  the content counts, the unknown rows and the **exact** number of significant
  terms over the whole vocabulary, so the narrative never reports a count that
  was cut off by the top-N.

**Scoped reference corpus (model change).** A criteria dataset may now also
declare `scope_column` for rows outside its criteria. Using `{scope}` without
declaring it is still refused ("the compiler already applies the access
scope"). A reason cannot be combined with criteria.

**Cross-check.** SQL computes G² and Log Ratio only to *rank* terms. For every
returned row, the analysis recomputes both with the reference implementation
and fails the run when they differ (relative tolerance 1e-9). It also fails the
run when a listed term is in the wrong direction, or when more significant
terms are listed than the exact count allows. A failing run stores nothing,
neither dataset rows nor analyses (tested).

**Not measurable** (stated, never zeros):
- `no_target`: nothing visible matches;
- `no_target_tokens`: the matches have no counted words;
- `no_reference`: the matches are the whole visible collection, or the
  reference has no counted words.

## Storage (m0026)

`report_run_analyses` has these columns:

* `run_id`: FK with `ON DELETE CASCADE`;
* `position` and `analysis_key`: PK (run, position) and UNIQUE (run, key);
* `analysis_fingerprint`;
* `kind`;
* `state` and `reason`;
* `inputs`: role → dataset key;
* `measures`;
* `rows`;
* `narrative`;
* `template_set` and `template_version`.

Enforced by PostgreSQL (tested by name in
`test_migration_upgrade_path.py::test_m0026_*` and
`test_report_analyses_pg.py`):

* `ck_report_run_analyses_state`: `measured` | `not_measurable`;
* `ck_report_run_analyses_reason`: a not-measurable row names its reason, and
  a measured row has none;
* `ck_report_run_analyses_voices`: the narrative holds exactly 5 voices;
* `ck_report_run_analyses_json`, `_fingerprint` and `_template`;
* the `trg_report_run_analyses_write_once` trigger refuses any UPDATE.

Analyses are written in the same transaction that completes the run.

## Versions changed by this step

| Identity | Before | Now | Why |
| --- | --- | --- | --- |
| runner | `report-runner/1` | `report-runner/2` | runs compute and store analyses |
| manifest | `report-manifest/1` | `report-manifest/2` | lists the run's analyses: key, fingerprint, state, template set/version/fingerprint, and whether this artifact includes them |
| JSON / HTML renderers | `report-json/1`, `report-html/1` | `/2` | carry an analyses section |
| CSV / XLSX | `/1` | unchanged | they render datasets only; the manifest says `included: false` |

Artifacts already stored keep their recorded versions and still verify.

## Surfaces

* `GET /api/reports/definitions` lists each report's analyses (key,
  translated title, kind) and the translated enum choice labels.
* `GET /api/reports/runs/<id>` returns the stored analyses together with
  `text`, which holds the five voices rendered in the caller's language.
* **`/reports` page:** the run detail has an **Analyses** section. For each
  analysis it shows the title, its state (measured or not measurable), the
  analysis key and template set, then the five voices, each labelled and
  inserted as text with `dir="auto"`. The report description lists the
  analyses, and the direction menu shows translated labels.
* **JSON/HTML artifacts** carry the analysis, and every manifest lists it.

## Performance (measured)

`python tools/perf/keyness_perf.py <pg-dir> <contents> 100` builds a synthetic
vocabulary: 20,000 words drawn with a strong skew, 100 distinct words per
content, and one NULL-count row per 100 contents.

Measured on the sandbox (a shared cloud VM with pgserver's PostgreSQL and
default settings), on 2026-09-28:

| Corpus | `words_hashs` rows | ranked query, narrow / broad | totals query, narrow / broad | full run, narrow (median of 3) |
| --- | --- | --- | --- | --- |
| 20,000 contents | 1.94 M | 0.87 s / 1.46 s | 1.99 s / 2.16 s | 2.41 s |
| 100,000 contents | 9.71 M | 7.2 s / 7.2 s | 12.1 s / 11.0 s | 13.3 s |

*Narrow* selects 1 of 20 sources (about 5 % of the contents); *broad* selects
10 of 20. The query timings come from `EXPLAIN ANALYZE`. The full-run timing
covers submit and execution: both datasets in one snapshot, the analysis and
its cross-check, and storage.

Five times the rows costs about 5.5 times the time. The selection size barely
matters, because both selections aggregate the same visible corpus.

**Extrapolation (not measured):** at this rate a single dataset statement
would reach the 120 s timeout at roughly 80–100 M `words_hashs` rows, which
is about 1 M contents at 100 distinct words each. On other hardware the
figures differ. Rerun the script on the target machine before relying on them.

The plan (`EXPLAIN ANALYZE`, 20,000 contents) spends about 94 % of the time in
one aggregation: summing `words_hashs` over every visible content, which is
the reference corpus. Target selection, the visible set and the per-term
arithmetic take about 100 ms together. The cost therefore grows with the
**corpus**, not the selection, and runs in the background job under the
run's 120 s statement timeout. When the timeout is exceeded, the run fails
with that message; it is never partial.

## Verification

| Evidence | Command | Result |
| --- | --- | --- |
| Measures and thresholds (oracles) | `pytest tests/unit/test_analytics_measures.py` | 23 passed |
| Templates and keyness (units: all states, cross-check failures, determinism) | `pytest tests/unit/test_analytics_kinds.py` | 22 passed |
| Registry wiring, fingerprints, lock, reference scope | `pytest tests/unit/test_report_analyses_registry.py` | 18 passed |
| PostgreSQL: dataset oracle, scope, runs, not-measurable, failure path, write-once, CHECKs, cascade, artifacts | `pytest tests/integration/test_report_analyses_pg.py` | 8 passed |
| Migration m0026: upgrade from 0015, constraints, trigger, cascade, downgrade, idempotent | `pytest tests/integration/test_migration_upgrade_path.py -k m0026` | passed |
| HTTP: definitions, run, Arabic rendering of the same record | `pytest tests/integration/test_reports_api.py -k keyness` | passed |
| Page: voices as text in order, states, hidden when absent, choice labels | `node tests/js/reports_page_smoke.mjs` | 39 ok (8 new) |
| Registry, translations, lock | `python -m core.reporting.lock --check` | 0 problems |
| Live server, fresh database, real ingestion (0001-0026) | `runtime_provision.py /tmp/rt16`, `run_web.py`, then `runtime_check_keyness.py http://127.0.0.1:5055 /tmp/rt16` | 23 passed, 0 failed: G² = 2 ln 2 and Log Ratio 1 for the only over-used term; the tie excluded; `no_reference` when everything is selected; the Arabic view of the same record; JSON artifact with voices; manifest/2 marks the analysis included (JSON) or not (CSV) |
| Full regression | `pytest tests` | 3,571 passed, 29 failed, 97 skipped. The 29 are the recorded OCR set (no Tesseract), 0 new; one non-reproduced anomalous run is recorded in [EXECUTION_STATUS.md](EXECUTION_STATUS.md) |
| Steps 14/15 unchanged on the live server | `runtime_check_reports.py`, `runtime_check_artifacts.py`, `reports_page_runtime.mjs` | 0 failures (the page check's expected generator was updated from `report-runner/1` to `/2`, an exact match, as the version bump requires) |

## Limitations

* **Only keyness is wired into a report.** TF-IDF, collocation (logDice),
  concentration (HHI), vocabulary profile, similarity (Jaccard), trends
  (Mann-Kendall with Theil-Sen), agreement (κ) and coverage are implemented
  and oracle-tested in `measures`, but no analysis kind or report uses them
  yet. They belong to the catalog reports of step 17, whose datasets must
  exist first.
  - Collocation additionally needs `words_hashs.position_indexer`, whose
    encoding has not been inspected.
  - No similarity at scale exists: the O(n²) comparison is prohibited, and a
    set-based candidate strategy is required first.
* **Keyness compares stored word forms.** It does not lemmatise, does not map
  synonyms, and does not separate languages. A selection in another language
  than the collection shows that language's function words, and the Caveat
  voice says so.
* **Access scope.** The reference corpus is scoped exactly as the criteria
  are. Today `scope_for` leaves every role unrestricted (carried open item),
  so the reference is the whole collection. When per-source access arrives,
  the same code restricts it (tested with explicit scopes).
* **Rendering language.** HTML/JSON artifacts render the narrative in English;
  RTL and multilingual artifact rendering is step 18. The API and page render
  in the caller's language.
* **Cost grows with the corpus** (see Performance). There is no precomputed
  per-word total. Both keyness datasets aggregate the same word rows, so a run
  does that work twice.
* **Word-count data quality.** `words_hashs.word_count` is nullable. NULL rows
  are excluded and counted, never treated as 0.
