# Gazetteer seed: sources and provenance

`places_seed.json` is **generated**: do not edit it by hand. To rebuild it, run
`python tools/gazetteer/build_seed.py`. `--check` verifies that the committed seed
matches its inputs, and the test suite runs that check.

| Input | What it is |
|---|---|
| `tools/gazetteer/titles.tsv` | The 223 English Wikipedia titles queried, each with a curated feature type. |
| `tools/gazetteer/wikidata_query.rq` | The SPARQL query. `TITLES` is replaced by one batch of titles. |
| `raw/wdqs_batch{0,1,2}.txt` | The Wikidata Query Service output for the three title batches (75 + 75 + 73 rows). |
| `curation.json` | Reviewed corrections and additions. Every entry carries a reason. |

## Licence

Wikidata structured data is **CC0-1.0**, so no attribution is required. It is
recorded anyway, in each place's `source` and `source_title` fields.

## Retrieval (seed version 2026-09-28.2)

* **Date:** retrieved 2026-09-28 from `https://query.wikidata.org/sparql`.
* **Raw row format:**
  * one item per line: `@@title\|QID\|Point(lon lat)\|country codes\|en\|ar\|he\|fa\|hr\|native@lang/...`;
  * the `\|` field separators are copied literally from the service output;
  * the per-row `~` terminator has been removed;
  * an empty field is kept as `\|\|`.
* **Retrieval path:** the build sandbox could not reach WDQS directly, so each
  batch's GET URL was fetched through a page-to-text renderer. The rows were
  transcribed from that rendering.
  * One row that the renderer split across two chunks (Oakland, Zagreb) was
    re-joined.
  * **Known fidelity limit:** the renderer may drop invisible format
    characters, in particular U+200C ZERO WIDTH NON-JOINER in Persian labels
    (for example, `تلآویو` for Tel Aviv). Name matching is unaffected, because
    `core.geo.names.match_key` strips ZWNJ, but the stored display form can
    differ from Wikidata's. Arabic presentation-form characters (for example, in
    Aleppo's native label) are kept as delivered.

## History: why this is version .2

The first seed (`2026-09-28.1`: 223 places, 1,153 names) and all of its inputs
were never committed, because `.gitignore` ignored `data/`. Every clone
therefore lacked the seed, and migration m0019 failed on a fresh database
(`SeedIntegrityError`).

The seed was rebuilt the same day from a new query. `curation.json` was
re-authored, because the original was lost. The result is 223 places and
1,155 names. The differences from `.1` are not individually recoverable. Known
Wikidata changes: Sarajevo (Q11194) and Palestine (Q219060) now carry a
country code, so they no longer need curation.
