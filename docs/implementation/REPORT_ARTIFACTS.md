# Report artifacts and manifests (step 15)

The third object of the reporting model: definition (code, step 13) -> run
(m0022, step 14) -> **artifact** (m0023, this step). An artifact is the bytes
of one rendering of one completed run in one format, stored with a
provenance manifest and a SHA-256 checksum, handed out only as an audited
download.

## Why these pieces, and not new ones

| Need | Existing infrastructure used | What was added |
| --- | --- | --- |
| Background work | JobManager | job type `report_artifact` (one branch in `_run_builtin`) |
| Data of the artifact | the run's stored datasets (`report_run_datasets`), read in the run's single snapshot | nothing is queried again: an artifact describes its run's snapshot only |
| Export audit | the fail-closed `DATA_EXPORTED` hook (`core/security/disclosure.py`) | downloads enrich it with report, run, snapshot, criteria/query fingerprints, count, truncation, artifact id and digests |
| Spreadsheet safety | `spreadsheet_safe_text` (search export) | reused for CSV and XLSX |
| XLSX | `openpyxl` (already a dependency) | no new dependency |
| Visibility | `runs._fetch` (requester or administrator, role still allowed) | reused: a file is visible exactly when its run is |

New: `database/migrations/m0023_report_artifacts.py`,
`core/reporting/render.py` (pure renderers), `services/reporting/artifacts.py`
(manifest, store, read, verify), six routes in `Api/routes/reports.py`, the
Files section of `/reports`, `tools/verify/verify_artifact.py` (offline
verifier), `tools/verify/runtime_check_artifacts.py`,
`tools/perf/report_artifact_perf.py`.

## Table (m0023)

`report_artifacts`: `run_id` (FK, `ON DELETE CASCADE`), `format`,
`dataset_key` (the one dataset a single-table format renders; NULL when every
dataset is included), `renderer_version`, `filename`, `media_type`,
`byte_size`, `sha256`, `content` (BYTEA), `manifest` (JSONB),
`manifest_sha256`, `created_by` (`ON DELETE SET NULL`) / `creator_username` /
`creator_role`, `job_id`, `created_at`.

Enforced by PostgreSQL (each tested by name in
`test_migration_upgrade_path.py::test_m0023_*`):

* `ck_report_artifacts_digest`: `sha256 = encode(sha256(content), 'hex')`. A
  row whose recorded checksum does not describe its bytes cannot exist.
* `ck_report_artifacts_size`: `byte_size = octet_length(content)`.
* `ck_report_artifacts_filename`: `[A-Za-z0-9][A-Za-z0-9._-]*`, no `..`.
  File names are built from server-side identifiers only.
* `ck_report_artifacts_format`, `ck_report_artifacts_manifest` (object, hex
  digest).
* `uq_report_artifacts_rendering` on (run, format, dataset, renderer
  version): rendering is deterministic, so one copy per rendering.
* Trigger `trg_report_artifacts_immutable`: no change except `created_by`
  becoming NULL when the account is deleted.

**Storage decision.** Bytes live in PostgreSQL, not under `APP_DATA_DIR`. The
bytes, their digest and their manifest are written in one transaction, so
there are no orphan files and no manifest without bytes. They are covered by
the same backup as the run they describe. No filesystem path is ever derived
from a request, so there is no traversal surface. The cost is database size.
It is bounded per artifact (`MAX_ARTIFACT_BYTES` = 64 MiB; a larger rendering
fails the job with that reason and stores nothing), and in total by retention
(step 21, not started).

## Renderers (`core/reporting/render.py`)

| Format | Renderer | Content |
| --- | --- | --- |
| `json` | `report-json/1` | canonical JSON (sorted keys): report, run provenance, every dataset with columns, rows and a completeness sentence |
| `html` | `report-html/1` | self-contained page: provenance table, per dataset a completeness sentence (shortened lists say so), the declared column labels, rows; every value HTML-escaped and wrapped in `<bdi>`; a `default-src 'none'` CSP meta tag |
| `xlsx` | `report-xlsx/1` | a Provenance sheet (run, snapshot, fingerprints, and per dataset semantics, limit, rows, truncated, completeness, query fingerprint) and one sheet per dataset |
| `csv` | `report-csv/1` | one dataset (`dataset_key` required), UTF-8 with BOM, CRLF |

The renderers are pure functions of the run document. Generation time and
the generating user belong to the manifest, never to the content. XLSX
document properties and ZIP entry times are set to the run's snapshot time.
The same run therefore renders to the same bytes, later and for another
user. This is tested with a 2-second pause, which is longer than ZIP's
timestamp resolution, and measured at 5,000 rows.

NULL: JSON `null`; HTML shows a marked "(none)". CSV and XLSX cannot tell an
empty cell from an empty string, and the manifest's
`artifact.notes.null_representation` says so. The JSON artifact of the same
run is the exact form. Text a spreadsheet would evaluate (`= + - @`) gets a
leading apostrophe in CSV and XLSX. The number of guarded cells, and of C0
control characters replaced for XLSX, are reported in `artifact.notes`.

PDF and SVG charts are refused with the reason "step 18". They are not
approximated: authoritative PDF must come from the server-side multilingual
renderer, which is not built yet.

## Manifest (`report-manifest/1`)

The manifest records:

* `artifact`: format, dataset, filename, media type, renderer version, bytes,
  sha256 and notes.
* `report`: id, version, key, title, unit and the lock-pinned definition
  fingerprint.
* `run`: id, parameters and their fingerprint, **criteria** and their
  fingerprint, the saved search, the requester (name and role), timing, job
  and runner version.
* `snapshot`: the `pg_current_snapshot()` identity, when it was taken, and the
  isolation level.
* `datasets`: key, dataset fingerprint, query fingerprint, semantics, row
  limit, row count, truncated and columns.
* `row_count` and `truncated` for what this file contains.
* `generated`: when, by whom (id, name and role) and the job.

`manifest_sha256` is the SHA-256 of the manifest's canonical JSON. The
downloaded manifest file is exactly those bytes, so `sha256sum` reproduces
the value.

## Execution

1. `POST /api/reports/runs/<id>/artifacts {format, dataset_key?}` (analyst or
   admin, the run readable by the caller and `completed`).
   * If the same rendering exists, it answers 200 with that file and no job.
   * Otherwise the request is audited (`report.artifact`), a job is created
     and the answer is 202.
   * Refusals: 400 for a bad format or dataset, 403 for viewers, 404 when the
     run is absent or not permitted, 409 when the run is not completed.
2. Job `report_artifact`:
   * The creator is re-read from `users`. A deleted or inactive account, or a
     role that may not create files, means refused, and nothing is rendered.
   * Run visibility is re-checked for the creator.
   * The document is built from stored rows only, then rendered, measured and
     bounded.
   * The manifest is built and digested.
   * The row is inserted with `ON CONFLICT DO NOTHING`, so a concurrent
     identical request yields the existing file.
3. Download (`/download`, `/manifest`):
   * Both digests are recomputed from the stored row first. A mismatch
     answers `500 INTEGRITY_FAILED` and nothing is sent.
   * `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`,
     `Cache-Control: no-store`, `X-Artifact-SHA256`, `X-Manifest-SHA256`.
   * The `DATA_EXPORTED` record measures the digest of the bytes actually
     sent (fail-closed).

## API

| Route | Who | Notes |
| --- | --- | --- |
| `GET /api/reports/runs/<id>/artifacts` | run readers | files, available formats (single-dataset flag), unavailable formats with the reason |
| `POST /api/reports/runs/<id>/artifacts` | analyst, admin | see above |
| `GET /api/reports/artifacts/<id>` | run readers | record and manifest |
| `GET /api/reports/artifacts/<id>/verify` | run readers | recomputed checks: content sha256, size, manifest sha256, manifest names content |
| `GET /api/reports/artifacts/<id>/download` | run readers | bytes, `DATA_EXPORTED` (`export:report_artifact:report_run:<id>`) |
| `GET /api/reports/artifacts/<id>/manifest` | run readers | canonical manifest, `DATA_EXPORTED` (`export:report_manifest:...`) |

## UI (`/reports`, run detail, Files)

The section offers the formats the server lists. A dataset selector appears
for CSV only, and unavailable formats are shown with the server's reason.
**Create file** follows the job and reports the outcome, including the job's
error text. Each file row shows the format and renderer, the dataset, the
file name, the size, the SHA-256 (with the manifest digest as a tooltip),
the creation time and the creator. Actions are Download, Manifest and
Verify; Verify names any failed checks. Files can be made from completed
runs only. Strings are in en/ar/he/fa/hr.

Defect found by the live page check and fixed: after a file was made, the
list reload reset the format selector to the first format. A second click
would then have made a CSV instead of repeating the request. The selection
is now kept. `tests/js/reports_page_smoke.mjs` has a check that fails on the
old code.

## Offline verification

```
python tools/verify/verify_artifact.py report_x.xlsx report_x.xlsx.manifest.json --manifest-sha256 <value shown by the application>
```

The verifier uses the standard library only and is independent of the
application. It checks:

* the file's SHA-256 and size against the manifest;
* that the manifest is canonical;
* with the flag, that the manifest's SHA-256 matches the application's
  record.

It exits 0 for `VERIFIED` and 1 for `MISMATCH`.

## Evidence

| What | Command / file | Result |
| --- | --- | --- |
| Migration on PostgreSQL: every CHECK by name, unique rendering, immutability, creator deletion, cascade, downgrade alone, re-apply; m0022's downgrade now goes newest-first | `test_migration_upgrade_path.py::test_m0023_*`, `::test_m0022_*`, `test_bootstrap_migrations.py` | 15 passed |
| Artifacts on PostgreSQL (8): every format stored with digest + manifest, determinism and idempotence, DB refuses mismatched digests/unsafe names/changes, creator deletion, tampered manifest detected and not sent, refusals, hostile and Arabic text in every format, truncation stated in content and manifest | `tests/integration/test_report_artifacts_pg.py` | 8 passed. Mutations checked: without ZIP time pinning the determinism test fails; without HTML escaping the hostile-text test fails |
| HTTP (3): create, repeat, download digest equals record, header, manifest and the `DATA_EXPORTED` measured digest; manifest digest; audit fields; offline verifier accepts, rejects a flipped byte and a wrong manifest digest; visibility; viewer 403; validation; CSRF | `tests/integration/test_report_artifacts_api.py` | 3 passed |
| Page module (31 checks incl. 15 for files) | `tests/js/reports_page_smoke.mjs` | passed |
| Live server, fresh database (0001-0023) | `tools/verify/runtime_check_artifacts.py` | 60 checks, 0 failures: 5 files made by background jobs, each downloaded, digest checked 3 ways, verified offline and by the server |
| Audit read-back from PostgreSQL | `audit_log` ids 11-20 | 10 `DATA_EXPORTED` rows (5 files + 5 manifests), each with format, row count, truncation, criteria fingerprint, snapshot `792:792:` and the measured SHA-256 of the bytes sent. 5 `report.artifact` rows. `sha256 = encode(sha256(content),'hex')` true for all 5 stored artifacts |
| Shipped page against the live server | `tools/verify/reports_page_runtime.mjs` | 38 checks, 0 failures (13 new: make an HTML file as a job, download it, its SHA-256 equals the one shown, attachment + audit id, Verify, repeat) |
| Performance, 20,000 files, 5,000-row listing | `tools/perf/report_artifact_perf.py /tmp/perfpg 20000` | create json 53 ms (759 KiB), html 67 ms (1,038 KiB), xlsx 492 ms (151 KiB), csv 53 ms (302 KiB); repeat request 0.9 ms; read + verify for download 2.0 ms; deterministic at 5,000 rows |

The server was started without `config.json`. The setup marker was created
for the run and removed afterwards. Logging in about 12 times within a minute
hit the login rate limit (429), which is the platform's protection working;
the check was rerun after the window.

## Limitations

* PDF, SVG charts and localised (RTL) rendering are step 18. HTML artifacts
  are rendered with `lang="en" dir="ltr"`, English labels and `<bdi>`
  isolation of values. Arabic, Hebrew and Persian values survive every format
  (tested), but the documents are not localised.
* CSV/XLSX cannot distinguish NULL from empty text. This is stated in every
  manifest; the JSON artifact is exact.
* Artifacts are kept until retention exists (step 21). Their size is bounded
  per artifact only.
* The browser download is a normal link. There is no in-browser digest
  check; the SHA-256 is shown and the offline verifier checks it.
* A job refused at render time (the creator was deactivated between request
  and job) creates no row. The reason is in the job's errors, which the page
  shows.
* No real browser was available; the page ran in the DOM stub.
