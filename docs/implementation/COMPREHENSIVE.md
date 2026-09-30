# Comprehensive Intelligence report (step 23)

The last catalog family. One report, `comprehensive@1`, over the matched
set, in **sections**: what was selected (Matched Set Overview), how its
terms differ from the rest of the visible collection (Distinctive Terms,
the step-16/17 keyness section), and how far the stored keywords and
categories reach into it (Keyword and Category Reach). Each section keeps
its own five voices, its own state, and its own template set; a section
that cannot be measured says so instead of reporting zeros.

## New pieces

| Piece | What it is | Discipline |
| --- | --- | --- |
| `comprehensive.overview@1` | one **exact** dataset row: contents, files, sources, keywords and categories present in the matched contents, first/last ingestion dates (`paths.date_creation`), first/last event dates (`content_signals.date_from`), unknown word-count rows | one query in the run's single snapshot; no cap, so no truncation exists to hide; NULL dates are unknown, never zero |
| `composition@1` (kind `composition`) | the opening section: exact counts over the matched set; compares nothing, so it can overstate nothing | record format 2 (plural sentences through the catalogs' Plural-Forms); dates are parameters, not prose |
| `coverage@1` (kind `coverage`) | the supporting section: how many matched contents contain each stored keyword/category, widest first, shares against the same matched set | **cross-checks** each listing's own `corpus_contents` against the overview's exact count and fails the run on disagreement (the same discipline keyness applies to its G2 columns); no concentration index is computed over overlapping category shares |
| `comprehensive@1` | the report: `composition@1`, `term_keyness@2`, `coverage@1` over five datasets (overview, keyness ranked/totals, keyword listing, category listing) | all datasets run in one REPEATABLE READ snapshot; nothing is queried twice |

## Honesty rules pinned by tests

* The overview is a single measured row: `semantics="exact"`, `row_limit=1`;
  an exact dataset returning more than its limit fails the run - there is
  no silent truncation to state.
* Empty presence is a **measurement** (a listed zero), never a missing row;
  absent dates are **unknown** (NULL columns), never zero.
* A capped listing that disagrees with the overview's matched-set size
  fails the run (`coverage: ... disagrees with the overview`).
* Keyness inside the Comprehensive report keeps the step-16 contract: when
  the matched set is the whole visible collection it reports
  `not_measurable: no_reference` rather than a manufactured comparison.
* Both listings empty **with** presence counted is a contradiction and
  fails the run; both empty with zero presence is consistent and measured.

## Surfaces

* `/reports` lists `comprehensive@1` with its three sections (the
  definitions API and page render analyses generically; no page change).
* The JSON artifact includes every dataset and the three sections with
  their voices; CSV renders one dataset at a time, as for every report.
* The Reports Dashboard counts the new family in its catalog tile; runs
  and artifacts appear in the dashboard tables like any other family.

## Evidence

* `tests/unit/test_analytics_composition_coverage.py` (16): composition
  oracle + voice order + record-format-2 plurals + unknown-count caveat +
  hostile dates stay parameters + determinism + not-measurable state;
  coverage oracle + cross-check failure on both listings + zero presence
  measured + contradiction failure + truncation in measures + input
  contract + the report declares exactly the three sections.
* `tests/unit/test_report_registry.py` (extended): the shipped registry
  validates the new dataset/analyses/report (structure, translations in
  pot + en/ar/he/fa/hr, fingerprint lock, SQL guards).
* `tests/integration/test_reports_comprehensive_pg.py` (4, PostgreSQL):
  one run records three honest sections with a hand-counted overview row
  (3 contents, 3 files, 2 sources, 1 keyword, 1 category, ingest/event
  spans, 1 unknown word count); determinism (fingerprints, query
  fingerprints, stored sections); a listing that contradicts the overview
  fails the run; over HTTP the run renders its three sections in the
  caller's language with the voices in order.
* `tests/integration/test_report_registry_pg.py`: every registered
  dataset - now including `comprehensive.overview@1` - executes with its
  declared columns, types and order on a real database (187 registry +
  coverage tests green after the addition).
* `python -m core.reporting.lock --write` pinned 4 new entries
  (`comprehensive.overview@1`, `comprehensive@1`, `composition@1`,
  `coverage@1`); `--check` reports 0 problems.

## Limitations (not hidden)

* The reference corpus for the keyness section is the whole visible
  collection minus the matched set, as everywhere; a reader whose scope
  sees everything gets `no_reference`, not a fake comparison.
* Coverage reads the whole-collection keyword/category listings capped at
  1000 rows each; a collection with more keywords than the cap measures
  the reach of the widest 1000 and states the truncation in its measures.
* Ingestion dates measure when files were collected, not when anything
  happened; event dates come from the durable temporal signals.
* Translations are not native-reviewed (standing limitation).
