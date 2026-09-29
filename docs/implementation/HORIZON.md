# Horizon and Signal Explorer (step 10)

Status: **RUNTIME-VERIFIED** (API and page module against a live production-mode
server; see [Verification](#verification)). Not ACCEPTED: acceptance is the
end-to-end check in step 28, and no real browser has rendered the page (see
[Limitations](#limitations)).

## What it is

A read-only view over the signals that ingestion and re-detection stored
(`content_signals`, [TEMPORAL_SIGNALS.md](TEMPORAL_SIGNALS.md),
[PLACE_SIGNALS.md](PLACE_SIGNALS.md)). Nothing here runs a detector: signals
are detected once and consumed from storage.

* **Horizon** groups resolved temporal signals by where their date falls
  relative to an explicit reference date.
* **Signal Explorer** lists every stored signal (temporal and places), with
  exact facet counts, filters, paging and a per-signal detail view.

| Layer | Code |
| --- | --- |
| Query service | `services/detection/signal_query.py` (filter parsing, `explore`, `horizon`, `signal_detail`) |
| Row mapping | `services/detection/signal_store.py` (`SIGNAL_COLUMNS`, `signal_rows_to_dicts`) |
| API | `Api/routes/signals.py`: `GET /api/signals`, `GET /api/signals/horizon`, `GET /api/signals/<id>` |
| Page | `GET /signals` -> `templates/Signals/signals.html` + `static/js/pages/signals-page.js`; registry entry `signal_horizon`, shortcut `g h` |
| Translations | `translations/{ar,he,fa,hr,en}/LC_MESSAGES/messages.po`, `translations/messages.pot` |

No migration: the step reads the m0017/m0018/m0019 tables and their indexes.

## Semantics

The authoritative definitions are in the module docstring of
`services/detection/signal_query.py`; in short, for reference date R and a
signal whose resolved range is [from, to]:

| Bucket | Rule |
| --- | --- |
| `overdue` | to < R and the sentence is future-oriented |
| `past` | to < R otherwise (counted; listed only when asked for) |
| `week` | to >= R and from < R + 7 (ranges in progress included) |
| `month` | R + 7 <= from < R + 30 |
| `quarter` | R + 30 <= from < R + 90 |
| `later` | from >= R + 90 |

* The bands are the 7/30/90-day rolling windows the future-date
  notifications already use, not calendar weeks or months.
* **The reference date is always explicit.** When `reference_date` is
  omitted, the server uses the current UTC date and the response says so
  (`reference_date_source`).
* **Undated is not zero.** Ambiguous and unresolved references are counted
  under `undated` and never placed in a bucket (`03/04/2026` stays ambiguous).
* **Not measured is not zero.** `coverage` counts matching content that has
  never been analysed, was analysed by an older detector version, or failed.
  The page states this in words.
* **The unit is the signal** (one stored detection). `contents` counts
  distinct content hashes. Every response carries the exact `total`, and
  nothing is capped silently.
* **NULL values stay visible.** Stored NULLs are filterable and shown as
  `unrecorded` (confidence, orientation) or `unspecified` (language). They
  are not merged into another value.
* **Places stay ambiguous.** Place signals keep every candidate place. The
  detail view lists them ("Ambiguous between: ...").

## Filters and criteria

* **Signal dimensions:** `detector`, `signal_type`, `resolution`,
  `confidence`, `language`, `calendar`, `method`, `orientation`, `place_key`,
  `hash_id`, `evidence_text` (a literal substring: `%` and `_` are escaped),
  `event_from` and `event_to`.
* **Parsing is strict.** Unknown enum values, malformed identifiers and
  inverted windows are 400s, never ignored.
  * Identifiers must be positive ASCII integers: the rule Werkzeug's
    `<int:>` converter applies. This was tightened during this step, see
    [Defects found](#defects-found).
  * Each filter takes at most 100 values. `evidence_text` is limited to 200
    characters. Pages hold 1-200 rows.
* **Document criteria** are handled by the one criteria compiler
  (`core/criteria`). They are the parameters `source_id`, `side_id`,
  `category_id`, `analyst_category_id`, `keyword_id`, `file_type`, `text`,
  `keyword_logic` and `doc_date_*`, or a `saved_search_id`.
  * The compiled query is applied as
    `EXISTS (<canonical FROM> WHERE hc.hash_id = s.hash_id AND ...)`. There
    is no second filter language.
  * A saved search can't be combined with explicit document filters. Which
    one should win would be a guess, so the request is refused.
  * A saved search the caller cannot read is a 404, the same answer as an
    absent one.
* **Determinism.** Filters are sorted and de-duplicated. Every order ends
  with `id`. The `query_fingerprint` is a SHA-256 of the kind, filter,
  criteria, reference date, buckets and sort. Equal requests give equal
  fingerprints and identical results.

## Authorisation

* Anonymous requests to the three APIs get 401, and requests to `/signals`
  get 302 or 401.
* Every authenticated role may read. Writes (re-detection) stay
  administrator-only.
* The caller's `AccessScope` is compiled into the SQL, before any row is
  read. A signal is visible only when at least one file occurrence of its
  content is visible. Detail views list only visible occurrences.
* Absent and not permitted give the same 404.
* **One snapshot per response.** Each API request reads inside a single
  `REPEATABLE READ, READ ONLY` transaction with a 15 s statement timeout.
  * The count, page, facets, buckets and coverage therefore agree even while
    ingestion commits.
  * PostgreSQL rejects any write.
  * The response reports the consistency it actually obtained
    (`read_consistency: "repeatable_read_snapshot"`). A service caller that
    passes a cursor already in a transaction gets `caller_transaction`: the
    isolation cannot be changed mid-transaction, and the response doesn't
    claim otherwise.
* A timeout is reported as an error and is never swallowed
  (`test_a_statement_timeout_is_reported_not_swallowed`).

## Page

The page is a view over the API contract only.
* The browser never loads the corpus, computes a bucket or filters rows. It
  builds a query string and renders the answer.
* Document text is inserted with `textContent` inside `<bdi dir="auto">`, so
  Arabic, Hebrew and Persian evidence keeps its direction.
* There is no inline script or style (CSP without `unsafe-inline`).
* The two tables use the shared `data_table` component. The error panel is
  the shared `state_panel` from `components/states.html`, into which the
  script writes the server's message. The component-adoption audit
  (`docs/COMPONENT_LIBRARY.md`) therefore counts no new hand-written table
  or error markup.

Every string is translated in ar, he, fa and hr. The page is on the audited
list of `tests/integration/test_translation_coverage.py`, so a string added
without translations fails that test.
* The translations were written for this step and have not been reviewed by
  native speakers. The owner should have them reviewed.
* In the Croatian catalog, the "Signal" column header is rendered as
  "Otkriveni signal". The literal "Signal" would equal the source string,
  which the coverage test rejects.

The source and side menus list at most 1 000 entries each
(`FILTER_OPTION_LIMIT`), ordered by name.
* The route reads one row more than the limit, so it knows whether a list
  was cut, and the page says so.
* If the lists can't be loaded, the page says so and the other filters keep
  working.
* The API itself accepts any identifier.

## Verification

Tests (PostgreSQL 16 through `pgserver`, via `tests/conftest.py`):

| Test file | Tests | Covers |
| --- | --- | --- |
| `tests/integration/test_signal_explorer_pg.py` | 22 | buckets for every dated reference, undated/coverage counts, facets, filters incl. NULL values, place keys, literal `%`, paging consistency, saved search owner isolation, scope applied in SQL, timeout reported, detail, 401/404/400, page contract data, capped and unloadable filter menus, one snapshot per API response, a row committed mid-read not seen, the read rejected as a write by PostgreSQL |
| `tests/unit/test_signal_query_parsing.py` | 54 | strict parsing: enums, patterns, positive ASCII ids, bounds, ISO dates, inverted windows, saved-search rules, canonical ordering and fingerprint equality |
| `tests/integration/test_translation_coverage.py` | +3 | `/signals` rendered in ar/he/fa: `lang`/`dir`, translated text, translated script labels; template strings in all four catalogs |

To run the check against a live server:

```
python tools/verify/runtime_provision.py /tmp/syltharae_runtime   # PG16 + migrations + 2 users + 2 ingested documents
export $(cat /tmp/syltharae_runtime/env.sh) AUTO_INSTALL=0 FLASK_PORT=5055   # env.sh: DB_* from env.json
python run_web.py &
python tools/verify/runtime_check_signals.py http://127.0.0.1:5055 /tmp/syltharae_runtime
node tools/verify/signals_page_runtime.mjs http://127.0.0.1:5055 /tmp/syltharae_runtime
```

Keep the state directory outside the repository. It holds a PostgreSQL data
directory.

`runtime_check_signals.py` checks the running server over HTTP only: a real
login with CSRF and the production security headers.
* It covers 401 before login, the page, and the CSP with no `unsafe-inline`.
* It checks bucket counts for documents ingested through
  `ContentDBService.process_full_document`, in Hebrew, Persian, Croatian and
  English.
* It checks determinism, facets, the ambiguous place and the detail view,
  the single snapshot, and that 400s carry a message naming the parameter.

`signals_page_runtime.mjs` runs the shipped `signals-page.js` unmodified in
the repository's DOM stub, built from the HTML the live server returned.
Every request goes to the live server.
* It covers the initial load, bucket clicks, the language filter, the
  Explorer tab, the detail panel and error display.

**Last run: 0 failures for both.** The API check makes 25 checks and reports
the counts overdue 1, week 2, month 1, quarter 1, later 2, past 0, ambiguous 1
and coverage 2/2. The page harness makes 21 checks with 8 requests.

## Performance (measured)

`python tools/perf/signal_query_perf.py <pgserver-dir> [contents]` loads a
synthetic corpus through the application bootstrap and times the service calls.

Setup: PostgreSQL 16.2 on a 2-vCPU sandbox, 20 000 contents, 240 000 signals.
Each figure is the median of 5 runs after one warm-up. Every call includes
the page, the exact count, and facets or buckets plus coverage.

| Case | Median ms | Max ms |
| --- | --- | --- |
| horizon, all content | 337 | 390 |
| horizon, one source | 135 | 138 |
| horizon, week, hijri + high | 158 | 162 |
| explorer, all content (facets) | 435 | 452 |
| explorer, one source | 111 | 115 |
| explorer, `evidence_text` | 570 | 577 |
| explorer, offset 10 000 | 328 | 349 |

These figures include the REPEATABLE READ snapshot. A run before the snapshot
was added measured the same numbers within 5 %.

Plan of the explorer page query: the document-criteria `EXISTS` runs as a
single hash semi-join over the whole set. There is no per-row loop, and a
top-N heapsort produces the 50 rows.
* Unfiltered requests scan `content_signals` sequentially, so the cost grows
  linearly with the signal count.
* 10x this corpus has not been measured. At that size, exact facets and
  `evidence_text` (unindexed `ILIKE`) are the first candidates for
  optimisation, for example a trigram index or keyset paging.
* The synthetic data measures query shape and index use, not detector
  behaviour.

## Defects found

| Defect | Evidence | Fix |
| --- | --- | --- |
| `hash_id`, document ids and `saved_search_id` were checked with `str.isdigit()`. With `²`, `int()` then failed and the 400 leaked Python's message (`invalid literal for int() with base 10`). With `٣` (Arabic-Indic three), the value was silently read as 3. `saved_search_id=0` went to the database and came back as 404. | live server, before the fix | `_positive_int`: one ASCII rule and a message naming the parameter. Unit tests plus the runtime check. |
| The page swallowed a failure to load the source/side menus: they were logged and shown empty. The 1 000-entry cap was also silent. | code review | the status is now explicit (`options_status`), with a notice. Two tests. |
| The statements of one response ran in separate READ COMMITTED snapshots. The pool's health check (`SELECT 1`, autocommit off, in `database.connect()`) leaves every pooled connection inside a transaction, so a `SET TRANSACTION` issued later would have come too late. `total`, facets and page could disagree while ingestion commits. | `read_consistency` reported `caller_transaction` on the API path | the route ends the health-check transaction, and `_begin_read` opens a `REPEATABLE READ, READ ONLY` snapshot and reports it. Three tests plus the runtime check. The shared pool is unchanged. Other code that opens `SET TRANSACTION` on a pooled connection meets the same trap; the reporting runner (step 14) must account for it. |
| A harness bug, not a product bug: the page harness chose the Zagreb row because it shares the evidence sentence with Tripoli. | harness output | the harness now matches the signal cell only |

## Limitations

* **No real browser.** Layout, CSS, keyboard focus and real bidi rendering
  have not been exercised: the Playwright Chromium download is blocked in
  this environment. The Node harness checks behaviour and DOM output only.
* Horizon covers temporal signals only. `detector=places` is refused there
  and accepted in the Explorer.
* `evidence_text` searches the stored evidence sentence, not the full
  document.
* The coverage notice concerns the date detector, the one Horizon depends
  on. Place coverage appears in the run status of each content.
* Future-date notification statistics use the server's local date, while
  Horizon uses UTC. This will be unified in step 11 (rule engine).
* The screen has no experience declaration. `docs/ACTION_SURFACE_AUDIT.md`
  counts it among the screens nobody has described yet, which went from 19 to
  20; 5 of 25 screens are described.
* Child categories are refused, as everywhere in the criteria compiler
  (open question 1 in [EXECUTION_STATUS.md](EXECUTION_STATUS.md)).
