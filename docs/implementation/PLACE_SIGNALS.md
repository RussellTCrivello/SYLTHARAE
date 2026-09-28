# Place signals and the database gazetteer (Phase 2)

Status and evidence are tracked in `EXECUTION_STATUS.md` (item 9). This page
describes what exists, how it fits the Phase 1 signal spine, and what it does
**not** do.

## What it is - and is not

Gazetteer matching. A place signal means *this span of the stored text equals,
after normalisation, a name the gazetteer records for these places*. It is
not named-entity recognition: no context model decides whether "Jordan" is a
country or a person. The detector's answer to uncertainty is to say so -
`ambiguous` with every candidate, or `low` confidence for curated homographs
- never to pick.

## Components

| Piece | Path |
|---|---|
| Seed data (Wikidata, CC0) + provenance | `data/gazetteer/` (`SOURCES.md`, `raw/`, `curation.json`, `places_seed.json`) |
| Seed build / check | `tools/gazetteer/build_seed.py`, `tools/gazetteer/wikidata_query.rq`, `tools/gazetteer/titles.tsv` |
| Schema | `database/migrations/m0019_gazetteer.py` |
| Loader, fingerprint, detector index | `services/geo/gazetteer.py` (`sync_seed` is the only writer) |
| Name normalisation / tokens / script | `core/geo/names.py` |
| Detector (pure) | `core/detection/place_intel.py` |
| Shared signal shape, evidence sentence | `core/detection/signal_model.py`, `core/detection/textspan.py` |
| Detector registry | `services/detection/detectors.py` |
| Storage, ingestion, re-detection | `services/detection/signal_store.py`, `ContentDBService._detect_signals` (step 10), `services/detection/redetection.py` |
| API | `Api/routes/signals.py`, `Api/routes/places.py`, `Api/routes/file_analysis.py` (compat) |

No second store, job type or ingestion path: place signals are
`content_signals` rows (`detector = 'places'`, `signal_type = 'place_mention'`)
produced by the same store, the same step 10 and the same `signal_redetection`
job as temporal signals.

## Schema (m0019)

* `geo_places(place_key UNIQUE, label, feature_type ∈ city|country|region,
  country_codes CHAR(2)[], latitude/longitude (pair, range-checked), source,
  source_title, seed_version, retired)`.
* `geo_place_names(place_id, name, match_key, language ∈ en|ar|he|fa|hr,
  script ∈ Latn|Arab|Hebr (must agree with language), name_type ∈
  endonym|exonym|historical|variant|unclassified, homograph, source, note)`,
  `UNIQUE (place_id, language, match_key)`, index on `match_key`.
* `geo_gazetteer_loads` - one row per seed load that changed anything: seed
  version and SHA-256, fingerprint computed **from the stored rows**, counts,
  who applied it.
* `content_signal_places(signal_id → content_signals ON DELETE CASCADE,
  place_id → geo_places ON DELETE RESTRICT)` - the candidates. Places are
  retired, never deleted, so stored evidence cannot be orphaned.
* `content_signals.resolution` gains `identified`, which the CHECK
  `ck_content_signals_identified_place` allows only for `place_mention`.
* **Deviation from the spec's four name types:** `unclassified` exists
  because 15 places record no native (P1705) label (17 in the lost seed `.1`), so endonym/exonym cannot
  be decided from the source. Claiming one would be invented data.

### `path_geo_mentions` compatibility

The m0015 table (written by the replaced regex scan) is renamed
`path_geo_mentions_m0015`; `path_geo_mentions` is now a view with the same
reading columns (`hash_id, place_name, country, latitude, longitude,
mention_count`) plus `place_key` and `provenance`:

* `signals` rows: identified place signals with `high`/`medium` confidence,
  grouped per content and place. Ambiguous and homograph (`low`) mentions
  are **not** in the view - it can represent only one place per mention;
  the signals API carries them.
* `legacy_m0015` rows: old rows for content the place detector has not yet
  analysed, so an upgrade loses nothing until re-detection supersedes them.

Consumers (`Api/routes/api.py` file details, `Api/routes/file_analysis.py`
geolocation places/rows/overview) read the view unchanged.

## Detector version and staleness

`detector_ver = "places-1.0.0+g" + fingerprint[:12]`. The fingerprint is a
SHA-256 over the active gazetteer rows, so editing the gazetteer changes the
version and `signal_redetection` with scope `stale` re-analyses exactly the
affected content. A run that could not load a gazetteer records `failed` with
the base version `places-1.0.0` and stays stale. The loaded index is cached
per fingerprint; each detection costs two small queries (table present,
latest load row); names are re-read only when the fingerprint changes.

## Matching

Both names and text go through `core.geo.names.match_key` (NFKC; drop
harakat, tatweel, niqqud, bidi controls, ZWNJ/ZWJ; unify alef forms,
ى/ي/ی, ك/ک, ة/ه; geresh → `'`; dashes → `-`). **Latin case and diacritics
are kept**: `Split` ≠ `split`, `Šibenik` ≠ `Sibenik` (diacritic-less
spellings exist only as curated variants).

| Method | Rule | Confidence (unique, non-homograph) |
|---|---|---|
| `exact` | whole tokens; multi-word names across spaces/hyphens only (punctuation breaks a name) | high |
| `latin.uppercase` | ALL-CAPS token sequence equals the name uppercased | medium |
| `en.possessive` | `London's` (single-word names) | medium |
| `he.prefix` | ≤3-letter proclitic cluster from ו ה ב כ ל מ ש (ו only first, ה only last); offsets cover the name, prefix in `evidence.prefix` | medium |
| `ar.proclitic` | و/ف then ب/ك/ل, and ل + ال → للـ | medium |
| `hr.inflection` | Croatian case endings of single-word hr names (see below) | medium |

Affix methods apply only when no exact match starts at that token (so `بغداد`
is never read as ب + `غداد`). Leftmost-longest; equal-length names at the same
span merge into one signal whose candidates are all their places.

Confidence rules (`place_intel.CONFIDENCE_RULES`, ordinal, not probabilities):
`exact_unique` → high, `affix_unique` → medium, `homograph` → low,
`ambiguous` → low.

Croatian paradigm: masculine consonant stems `-a -u -om` (`-em` after
palatals), neuter `-o/-e` → `-a -u -om/-em`, feminine `-a` → `-e -i -u -om`
with sibilarisation k→c, g→z, h→s before `-i` (`Rijeka` → `Rijeci`). It is a
small paradigm, not a morphological analyser.

## Evidence

Every signal stores `method`, `confidence`, `confidence_basis`, the verbatim
evidence sentence with offsets (shared `textspan.quote_sentence`), and
`evidence`: `pattern`, `normalized`, `gazetteer` (fingerprint prefix),
`candidates` (key, label, feature type, country codes), `matched_names`
(name, language, name type, homograph), `languages`, optional `prefix` /
`suffix` / `lemma` / `homograph_notes`, `context`, and the note
"gazetteer match, not named-entity recognition". `language` is set only when
all matched names share one language (`Zagreb` is both en and hr → NULL,
languages in evidence).

## API

* `GET /api/content/<hash_id>/signals[?detector=places]` - place signals
  carry `places` (candidate list); `runs` has one entry per detector.
* `POST /api/signals/redetect` - optional `"detectors": ["places"]`.
* `GET /api/gazetteer`, `GET /api/places?q=&language=&feature_type=&country=`,
  `GET /api/places/<place_key>` - read-only; mention counts keep
  `identified` and `ambiguous_candidate` apart and are computed in the
  caller's `AccessScope` (`core/criteria/access.py`, shared with the signals
  and notification routes).
* `POST /api/file-analysis/geolocation/scan[?force=true]` - **contract
  change**: now queues a `signal_redetection` job for the places detector and
  returns `{job, summary}`; `summary.coordinates_written` is always 0.

## Behaviour changes from the replaced scan (deliberate)

1. `paths.coordinates` is no longer written from text. It holds GPS
   coordinates from file metadata (`ContentDBService` path insert,
   `reader_file/readers/read_img_fast.py` `location`), and the old scan
   overwrote them with the most-mentioned place. Values already written by
   old scans are left as they are - the database does not record which
   values came from the scan, so they cannot be told apart safely.
2. Ambiguous names are no longer resolved to one place.
3. Detection runs at ingestion; it no longer waits for a manual scan.
4. The static 119-entry English list (`Api/services/geo_gazetteer.py`) is
   deleted; every one of its names is detected from the new seed
   (`test_every_legacy_gazetteer_name_is_still_detected`); six are now
   explicitly ambiguous or low confidence.

## Measured performance

Single process in this sandbox, 1 MB of text, `place_intel.detect` /
`temporal_intel.detect` timed by `tools/perf/detector_throughput.py`:

| Text | Signals | Time |
|---|---|---|
| typical prose (one place per ~1 KB) | 959 | 0.69 s |
| dense mixed en/ar/he/fa/hr (a place every ~24 chars) | 42,306 | 2.5-2.8 s |
| temporal detector, same dense text | 3,846 | 2.2 s |
| gazetteer index build (1,153 names; measured on seed `.1`, current `.2` has 1,155) | - | 12 ms |

Before the shared `SentenceIndex` (binary search), evidence-sentence lookup
was a linear scan per signal - O(signals × sentences) - in both detectors:
15 s for the dense megabyte. That was a pre-existing defect in the temporal
detector, fixed for both.

## Limitations

* Gazetteer coverage is 223 places; anything else is simply not detected
  (absence of a signal is not evidence of absence of a place).
* No context disambiguation; homographs rely on a curated list.
* Croatian morphology is a small paradigm; Persian/Arabic nisba adjectives
  (`عراقي`) and Hebrew construct forms are not matched.
* Multi-word possessives (`New York's`) and names split by line-break
  hyphenation are missed.
* The view shows only identified high/medium mentions.
* No per-source ACL exists yet; `AccessScope` is unrestricted for every role
  and is passed down so a future ACL is applied in SQL.
