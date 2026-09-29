# Detection page: signal detection coverage, run history, re-detection

This is item M2 of the Manageability wave in [EXECUTION_STATUS.md](EXECUTION_STATUS.md).
Administrators can see which detector version analysed which content, with
what outcome, and can queue re-detection from the interface instead of the
API.

* Page: `/signals/detection`, registry interface `detection`.
  * Domain Operate, shortcut `g d`, help topic `operate/detection`.
  * It depends on `signal_horizon` and `jobs`.
* APIs, all administrator-only:
  * `GET /api/signals/detection/status`
  * `GET /api/signals/detection/runs`
  * `POST /api/signals/redetect`: already existed. It is now audited.

## Why these pieces, and not new ones

* **`content_signal_runs` (m0017) is the only record read.** It already holds
  one row per (content, detector). Each row has:
  * the detector version and the status (`complete`, `truncated`, `no_text`
    or `failed`, with the error only when failed);
  * the character counts and the signal count;
  * the trigger (`ingestion` or `redetection`), the job id and the time.

  No table was added.
* **Re-detection is the existing `signal_redetection` job** on the existing
  JobManager (step 8). The page queues it through the existing POST and
  follows it through `/api/jobs/<id>`. There is no second job framework.
* **Stale means exactly what the job selects.**
  * The page's stale count uses the job's predicate
    (`redetection.HAS_TEXT` and `_stale_clause`).
  * `HAS_TEXT` was factored out of the job's SQL for this, with no change in
    behaviour.
  * One test checks that the count shown equals what a real `stale` job then
    processes.
  * A second test checks that the one-pass rewrite (below) equals the job's
    own selection SQL on every run state.
* **The redetect POST was not audited.** Re-detection replaces stored signals,
  so each request is now recorded as `signals.redetect`, with
  `{scope, detectors, hash_id_count}` and the job id as resource. It can be
  read in the Audit Log viewer (M1).

## Contract

`GET /api/signals/detection/status` returns:
* `analysable_contents` (contents with stored text) and `stale_any_detector`;
* per detector:
  * `current_version` and `current_version_available`;
  * `at_current_version` and `at_older_versions`, as counts by status;
  * `by_version` (version, status, runs, last run);
  * `never_analysed` and `stale`.

All of these come from one `REPEATABLE READ, READ ONLY` snapshot and are exact.
The response carries `snapshot` and `measured_at` and says `exact: true`.
* "Never analysed" is its own count. It is not reported as zero signals.
* A detector with no determinable version (places with no gazetteer loaded)
  reports `current_version: null`. All of its content counts as stale, as in
  the job.
* The statement timeout is 15 s. Exceeding it returns 503 `QUERY_TIMEOUT`,
  never partial counts.

`GET /api/signals/detection/runs` returns the runs newest first
(`ran_at DESC, hash_id DESC, detector`). Each run has one file of its content
(the lowest `paths.id`, reached through `hash_contexts`) and `path_count`.
* Filters: `detector`, `status`, `trigger`, `hash_id` and `version`.
  * `version` takes `current`, `older` or an exact version.
  * `current` and `older` need a detector, because versions differ per
    detector. If that detector's version is unavailable the response is 409
    `VERSION_UNAVAILABLE`.
* Paging uses `limit` (1–200) and `offset` (up to 100,000). The server fetches
  `limit + 1` rows to set `has_more`.
  * `total` is `null`, with a `total_reason`: exact totals are in the coverage
    table.
  * A larger offset is refused, not clipped.
* Unknown or invalid parameters return 400, with a message naming the
  parameter.

`POST /api/signals/redetect` is unchanged apart from the audit entry. It
takes:
* `scope`: `stale`, `all` or `hash_ids`, with at most 10,000 ids;
* `detectors`: optional; the default is all detectors.

It returns 202 with the job.

The page (`static/js/pages/detection-page.js`) is a client of these APIs only:
* Every number it shows is the server's. The page computes no count.
* Coverage has a per-detector "Re-detect stale" button. It queues exactly
  `{scope: "stale", detectors: [d]}`.
* The re-detect form has local checks. They only avoid a pointless request;
  the server still validates everything, and its refusals are shown verbatim
  with their code.
  * Ids must be whole numbers, and a wrong one is named.
  * At least one detector must be chosen.
  * `all` needs a confirmation checkbox.
* A queued job is followed to a terminal status, then coverage and runs
  reload. While the job has no `result_summary`, the line gives only the job
  and its status. It never shows "0 analysed" for a count it does not know.
  The runtime check found this defect, and it was fixed.
* Run rows are text: a failed run's error is never markup. `NULL` counts are
  shown as "none". Each row links its file (`/file/<path_id>`) and its job.

## Measured performance (`tools/perf/detection_admin_perf.py`)

The corpus had 500,000 contents, each with a context, a file and a stored text
chunk, and 760,000 runs:
* temporal: 80 % current, 10 % older, 2 % failed, 8 % never analysed;
* places: 60 %, all stamped with a non-current gazetteer load.

Each figure is the median of 5 runs after a warm-up.

**Coverage (`detection_status`).**

| Shape | Time |
| --- | --- |
| First version: six `count(*) … NOT EXISTS` queries, each re-scanning every content | 3,983 ms |
| One pass with correlated `FILTER (WHERE NOT EXISTS …)` | 3,316 ms |
| One pass with semi-join `IN (…) OR IN (…)` for `HAS_TEXT` | > 30,000 ms (cancelled) |
| **Shipped:** one pass, one `LEFT JOIN` per detector | **1,095 ms** |

The LEFT JOIN form equals the job's `NOT EXISTS` predicate only because the
primary key is `(hash_id, detector)`, so there is at most one run per pair.
`test_coverage_counts_equal_the_jobs_selection_on_every_run_state` checks it
against the job's own selection SQL on seven run states, in three version
modes. Dropping the `failed` condition makes that test fail (mutation
checked).

The cost is linear in the number of analysable contents. At about 7 million
contents it would reach the 15 s timeout and return 503; it would not return
wrong counts.

**Run listing (`list_runs`, limit 50).** Two changes:
* The page is taken first, and files are looked up only for the paged rows.
  Before, the file lookup ran for every matching run.
* m0025 adds `(ran_at DESC, hash_id DESC, detector)`.

| Filter | Before m0025 | After m0025 | Plan after |
| --- | --- | --- | --- |
| none, first page | 58.8 ms | 1.5 ms | index scan `idx_content_signal_runs_ran_at` |
| offset 100,000 | 132.2 ms | 13.5 ms | same index |
| detector=temporal | 56.6 ms | 1.5 ms | same index |
| temporal + status=failed (2 %) | 51.1 ms | 1.8 ms | same index |
| temporal + version=older (10 %) | 50.1 ms | 1.8 ms | same index |
| trigger=redetection (0.5 %) | 51.6 ms | 2.9 ms | same index |
| hash_id | 3.9 ms | 0.7 ms | primary key |
| status=truncated (0 runs) | – | 30.7 ms | seq scan (no row to stop at) |
| places + status=failed (0 runs) | – | 36.0 ms | index walked to the end |

A filter matching nothing still costs a scan, linear in the table (about
0.05 ms per 1,000 runs). No status or trigger index was added for that rare,
bounded case.

## Evidence

| Evidence | Command | Result |
| --- | --- | --- |
| PostgreSQL service and API | `pytest tests/integration/test_detection_admin.py` | 23 passed |
| m0025 | `pytest tests/integration/test_migration_upgrade_path.py -k m0025` | Passes: definition, released indexes kept, downgrade drops exactly it, upgrade idempotent. A fresh bootstrap applied 0001–0025 (runtime provisioning log) |
| Page module | `node tests/js/detection_page_smoke.mjs` | 28 checks; wrapper `tests/unit/test_detection_page_js.py` pins the count |
| Live API | `python tools/verify/runtime_check_detection.py <base> <state>` | 47 passed, 0 failed |
| Live page | `node tools/verify/detection_page_runtime.mjs <base> <state>` | 17 passed, 0 failed, 7 live requests |
| Performance | `python tools/perf/detection_admin_perf.py <pgserver-dir> 500000` | Figures above |

**PostgreSQL service and API tests (23).**
* The stale count equals what a real `stale` job processes, and nothing is
  stale afterwards.
* Older-version and failed runs are counted and listed.
* A failed run's error comes back as text.
* The run list names a file and counts the others. A run whose content has no
  file is still listed.
* Paging is exact.
* Ten invalid filters are refused.
* An unavailable version gives 409.
* The coverage-equals-job check runs in 3 version modes.
* Admin access works and non-admins are refused.
* The redetect audit detail is correct, and the run records the job id.

**Page module smoke test (28 checks).**
* Coverage shows the server's numbers, and an unavailable version is marked.
* The per-detector action sends exactly stale for that detector, with the
  CSRF token.
* Jobs are followed and the page reloads.
* A job without a summary states no counts.
* Bad ids, no detector and an unconfirmed `all` never reach the server.
* Server errors are shown verbatim.
* An empty history is distinguished from an empty filtered result.
* Paging works, and no `innerHTML` is used.
* Four mutations are caught: all detectors sent, a browser-derived stale
  count, the error inserted as markup, and zeros shown for an unknown
  summary.

**Live API check (47).** It ran against a server started on a freshly
provisioned database whose documents came through the real ingestion path.
* Before sign-in the APIs return 401. Analysts and viewers get 403 on the
  page, the APIs and the POST. A POST without a CSRF token is refused.
* The page has the production CSP.
* The coverage buckets add up, and every ingested document has a run per
  detector naming its file.
* Invalid filters give 400 naming the parameter. Paging one run at a time
  returns each run once.
* **Re-detection after a simulated upgrade.** The check makes one content
  older-version and one never-analysed; this is its only direct DB write.
  * Coverage then shows stale 2, and `version=older` lists exactly that
    content.
  * The stale job processed exactly 2, and nothing was stale afterwards.
  * A listed-id job rewrote that run with trigger `redetection` and its job
    id.
  * Both requests are in the audit log with the right detail.

**Live page check (17).** It runs the shipped module in the DOM stub, built
from the live HTML, with every request going to the live server.
* Coverage equals the API, and run rows link `/file/<id>`.
* A filter narrows on the server.
* A bad id is refused locally.
* A listed-id re-detection submitted through the form is followed to
  COMPLETED, showing the server's counts. Coverage reloads, and the server has
  the rewritten run.

To run the live checks, follow the recipe in [HORIZON.md](HORIZON.md)
(`runtime_provision.py`, then `run_web.py`). Then run
`runtime_check_detection.py`, then `detection_page_runtime.mjs`. Keep the
state directory outside the repository. The API check logs in three users; a
second run within a minute hits the login rate limit (10 per minute) and
fails with 429.

## Limitations

* **Coverage cost.** Coverage is one pass over all analysable contents, about
  1.1 s at 500,000. A sustained multi-million-content corpus would need an
  incrementally maintained summary. That is not built, and past the 15 s
  timeout the page shows 503, not guessed numbers.
* **Empty filters scan.** A run filter that matches nothing scans the table
  (31–36 ms at 760,000 runs).
* **No cancellation from this page.** Re-detection jobs are cancelled from the
  existing job page, which the page links.
* **No preview.** Re-detection has no dry run. The stale count per detector is
  the preview for `stale`; `all` needs an explicit confirmation.
* **Replacement is permanent.** Re-detection replaces a content's signals, and
  the previous signals are not kept; this is step 8 behaviour. The run row
  records the version that produced the current signals.
* **One file per run.** Only the lowest `paths.id` is linked; the others are
  counted.
* **No real browser.** The page was checked in the repository's DOM stub, not
  a real browser. Layout, CSS and real bidi rendering are not exercised.
* **Translations are unreviewed.** The 52 new strings in ar, he, fa and hr
  were written without native-speaker review. The translation coverage test
  checks presence and placeholders, not quality.
