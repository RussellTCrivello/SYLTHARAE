# Audit Log viewer

Part of the manageability wave the owner asked for: the tables and records
the system keeps should be manageable from the interface. The audit log is
where the chain INGEST → … → EXPORT → AUDIT ends. Every file that leaves the
system (`DATA_EXPORTED`, with its SHA-256), every report run and file, rule
and scenario changes, sign-ins and user administration are written to it.
Until now the only way to read it was SQL.

## Why these pieces, and not new ones

* **Table:** `audit_log` (m0003) is the only audit store. It is written by
  `AuthService.audit` / `audit_strict` and by the fail-closed disclosure hook.
  The viewer reads it and writes nothing. It has no second store and no copy.
* **Query:** `core/security/audit_query.py` is small and read-only. It has
  one parameterised statement per request, runs in `REPEATABLE READ, READ ONLY`
  with a 5 s statement timeout, and uses keyset paging by id.
  * The criteria compiler (`core/criteria`) was not reused. It compiles
    *document* criteria (sources, keywords, dates of files), and audit entries
    are not documents.
* **Routes:** `Api/routes/audit.py` uses the same error envelope and
  `admin_required` as the other admin routes. It is limited to
  `INTERACTIVE_READ_LIMIT`.
* **Page:** `templates/auth/audit.html` (next to User Management) and
  `static/js/pages/audit-page.js`. It is registered in `core/interfaces` as
  `audit_log` (ADMINISTRATION, admin only, shortcut `g l`, help topic
  `administration/audit`).

## Contract

| Route | Returns |
|---|---|
| `GET /admin/audit` | the page |
| `GET /api/audit` | `items`, `limit`, `has_more`, `next_before_id`, `total: null`, `total_reason` |
| `GET /api/audit/actions` | `items` (distinct action names, sorted), `capped`, `cap` (500) |
| `GET /api/audit/<id>` | `entry`, or 404 |

Filters on `/api/audit`:
* `action` and `username` are exact matches, and `user_id` is a whole number.
* `resource` is a **prefix**. `%`, `_` and `\` are matched literally.
* `since` is inclusive and `until` is exclusive. Both are ISO 8601; a
  timestamp without a zone is read as UTC.
* `before_id` is the keyset cursor.
* `limit` is 1-200 (default 50).

Unknown parameters, bad numbers, bad timestamps and `since >= until` are
refused with 400 and a message that names the parameter. A statement timeout
is 503 `QUERY_TIMEOUT` with advice to narrow the query. It is never shown as
"no entries".

**No silent truncation.** Each page asks for `limit + 1` rows and reports
`has_more`. The total is not counted, because counting an unbounded log is the
unbounded query the page must not issue. The response says `total: null` with
the reason, and the page says "The total is not counted." The action menu
states its cap if there are more than 500 names.

**Who can see it:** administrators only, for both the page and the API.
Analysts and viewers get 403 and anonymous requests get 401. Reading the log is
not an export and is not audited per request.

## m0024: lookup indexes (measured, not assumed)

The page lists entries newest first with `ORDER BY id DESC LIMIT n`. With
only m0003's single-column indexes, a rare user, user id or resource was
answered by walking the primary key backwards and discarding rows. m0024
adds three indexes and removes none:
* `(username, id DESC)`;
* `(user_id, id DESC)`;
* `resource text_pattern_ops`, which serves `LIKE 'prefix%'` under any
  collation.

m0003's `idx_audit_log_user_id` is kept, because it is released schema. The
downgrade drops exactly the three new indexes.

The action menu uses a loose index scan (a recursive CTE over
`idx_audit_log_action`) instead of `SELECT DISTINCT`. A test pins that the two
produce the same list.

`python tools/perf/audit_log_perf.py <socket-dir> 1000000` was run on
PostgreSQL 16 (2 vCPU sandbox) with 1,000,000 synthetic entries. Each figure
is the median of 5 runs after a warm-up, for a page of 50:

| Filter | Before m0024 | After | Plan after |
|---|---:|---:|---|
| none, first page | 1.0 ms | 0.9 ms | PK index scan |
| deep page (`before_id` = middle) | 1.1 | 1.4 | PK index scan |
| `action` common (25 %) | 1.1 | 1.4 | PK index scan |
| `action` rare (0.01 %) | 1.1 | 1.4 | bitmap on `idx_audit_log_action` |
| `username` 0.5 % | 2.1 | 1.1 | `idx_audit_log_username_id` |
| `username` rare (0.005 %) | **171.8** | **1.0** | `idx_audit_log_username_id` |
| `user_id` rare (0.005 %) | **168.7** | **1.1** | `idx_audit_log_user_id_id` |
| `resource` prefix common (17 %) | 1.1 | 1.1 | PK index scan |
| `resource` prefix rare, no match | **141.4** | **0.5** | `idx_audit_log_resource_prefix` |
| `resource` prefix rare, 50 matches | - | 1.1 | `idx_audit_log_resource_prefix` |
| one-day window | 1.1 | 1.3 | `idx_audit_log_created_at` |
| action menu | **101.4** (`DISTINCT`) | **0.7** (loose scan) | recursive index probes |

The perf tool builds `audit_log` from the real migration statements (m0003
plus `AUDIT_LOG_STATEMENTS` in m0024). It works in its own database, which is
dropped afterwards.

## Evidence

| Kind | Command | Result |
|---|---|---|
| PG + API tests | `pytest tests/integration/test_audit_log_viewer.py` | 23 passed |
| Migration | `pytest tests/integration/test_migration_upgrade_path.py -k m0024` | passed. Definitions checked by name, m0003 indexes and the legacy entry kept, downgrade drops exactly three, re-upgrade idempotent |
| Page smoke | `node tests/js/audit_page_smoke.mjs` (pinned by `tests/unit/test_audit_page_js.py`) | 19 checks |
| Mutations | markup insertion of the user name; ignoring `has_more` | both killed |
| Live start-up upgrade | server restarted on the step-15 runtime database (0023) | log: `Applied schema migrations 0024`, all three indexes present |
| Live API | `python tools/verify/runtime_check_audit.py http://127.0.0.1:5055 /tmp/rt15` | 37 PASS / 0 fail |
| Live page | `node tools/verify/audit_page_runtime.mjs http://127.0.0.1:5055 /tmp/rt15` | 19 PASS / 0 fail, 9 requests |
| Perf | `tools/perf/audit_log_perf.py`, 1,000,000 entries | table above |

What the live API check verifies:
* The 10 `DATA_EXPORTED` ids recorded by the step-15 artifact check are all
  listed.
* For each of the 5 file downloads, the digest in the audit detail equals the
  SHA-256 of the file saved to disk during that check.
* Keyset paging by 3 returns all 35 `DATA_EXPORTED` entries exactly once
  (12 pages).
* A `%` in the prefix is literal.
* The production CSP has no `unsafe-inline` for scripts.

The live page harness runs the shipped `audit-page.js` unmodified against the
live server. It covers:
* Older/Newer paging on the server's cursor;
* the action filter, and the note switching to "oldest matching entries";
* clicking a resource to filter on it;
* the detail JSON carrying the digest;
* a refused window shown verbatim;
* Clear.

## Limitations

* **No export of the log from the page.** An audit export would itself be a
  `DATA_EXPORTED` disclosure and needs a retention and redaction decision
  (step 21). It remains SQL-only.
* **No full-text search in `detail`.** You can filter on action, user,
  resource and time, but not on JSON keys or values.
* **Times are shown in the reader's browser locale.** The window inputs are
  read as the reader's local time and sent as UTC. This is labelled on the
  detail but not on the list.
* **The log's integrity is not proven by the viewer.** `audit_log` rows are
  not hash-chained, so an administrator with database access can alter them.
  Signing or hash-chaining is not implemented.
* **Tamper-evidence has limits.** Report artifacts are checked against their
  DB-held digest (step 15), which the audit entry repeats. Both are in the
  same database.
* **No real browser was used.** The page harness is the repository's DOM
  stub, so layout and bidi rendering were not exercised.
* **Translations** (ar, he, fa, hr; 28-29 new strings each) have not been
  reviewed by a native speaker.
