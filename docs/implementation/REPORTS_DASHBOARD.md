# Reports Dashboard (step 22)

One page that answers "what is the reporting system doing right now":
the report versions your role can read, the runs by measured outcome, the
artifacts made from them, and the schedules that fire next. It is a view
over the existing reporting, scheduling and jobs APIs — no second runner,
no second list, no new query language.

## What it shows

| Tile | Source | Honesty rule |
| --- | --- | --- |
| Report versions | `visible_definitions(role)` — the registry, role-filtered | the catalog is the declaration, counted as declared |
| Runs (+ per-status breakdown) | `run_status_counts` — one `GROUP BY status` over exactly the runs the Runs list would show | an absent status is a **measured zero** (the GROUP BY answered); "not measured = zero" never happens because the count is always taken, never assumed |
| Files (+ per-format breakdown) | `artifact_summary` — one grouped join over the visible runs | the same visibility as `GET /api/reports/runs` |
| Enabled schedules + next fire | the schedules store (own, or everyone's for an admin) | next fire is `MIN(next_run_at)` of enabled schedules, measured |

Every count is set-based SQL with a statement timeout and the same
requester/definition scoping as the runs list: a non-admin counts their
own runs over report versions their role can still read (a retired
definition's history is administrator-only, exactly like `list_runs`);
`?all=1` widens to everyone for administrators and is refused (403)
otherwise. Definition filtering happens **in SQL, before counting** —
never after rendering.

## The three tables

* **Report catalog** — family, version, dataset count, analyses, what it
  answers, and an Open link to `/reports`. Client-side sortable
  (the registry is small and declared).
* **Runs** — the unified table in server mode: run id, report@version,
  status badge, requester, requested at, dataset count (with a
  `shortened` badge when any stored dataset was capped), and a deep link
  to `/reports?run=<id>` (the Reports page opens that run's detail).
  **Load More** appends the next batch to the table in place — the page
  does not reload, filters and selection survive — through the shared
  `UnifiedTable.onLoadMore` / `appendRows` contract, and the new
  `syncLoadMore(tableId, shown, total, pageSize)` lets an API-fed first
  page drive the same counters a server-rendered batch gets.
* **Schedules** — name, what runs, interval, next fire, enabled/paused,
  Manage link to `/schedules`.

The status filter and the administrator's **All users** switch re-fetch
the runs page and the tiles together; the tiles, the counter in the
table toolbar and the Load More block always agree (the component's
counter update now covers every `data-ut-shown`/`data-ut-total` in the
wrapper, not only the block's copy).

## The language picker (the step-18 deferral, landed)

The Reports page's Files section gained a **Language** select
(`/reports`, fed from `core.reporting.i18n.available_languages`). The
chosen language rides the artifact request (`POST .../artifacts
{format, dataset_key?, language}`) and becomes part of the file's
identity — the same run and format in another language is a *different*
file, each reuse/refusal decided by the stored `(run, format, dataset,
renderer, language)` identity, and each manifest states its language.
The file table now shows a Language column. The backend validation and
storage were step 18; this step adds the frontend that was deferred.

## Interface registry

`reports_dashboard` ("Reports Dashboard", Domain REPORT, route
`reports_dashboard_page`, help topic `report/dashboard`, dependencies
`reports`/`jobs`/`schedules`), keyboard-shortcut-less (recorded in the
registry test's declared absences). The legacy Analysis dashboards
(`/dashboard/comprehensive`, `/dashboard/charts`) are untouched — they
are the old app's pages, and the document-operations wave (D8) owns
their file-type side panel work.

## Evidence

* `tests/integration/test_reports_dashboard_pg.py` (4, PostgreSQL):
  counts cover exactly the visible runs (a stranger's run is invisible);
  a retired definition's run is admin-only in the counts and included in
  `?all=1`; a non-admin counting everyone is 403; artifacts counted over
  the visible runs with the per-format split.
* `tests/integration/test_reports_dashboard_api.py` (2, HTTP): the page
  renders for analyst and viewer with the unified tables; the API
  answers with catalog/runs/artifacts/schedules/languages, analyst scope
  `mine` excluding a stranger's run, admin `?all=1` widening to `all`,
  viewer widening 403 `FORBIDDEN`, and the viewer catalog read-only
  (`can_run: false`); the language picker is on the Reports page, its
  list carries the shipped languages, and `POST`ing `{format, language}`
  yields two distinct artifacts (`en`, `ar`) listed with their languages.
* The shared component change is pinned by `tests/unit/test_progressive_tables.py`
  (32) and `tests/js/unified_table_smoke.mjs` (25/25); translation
  coverage (46) holds for the dashboard template added to the audited
  list; route reference regenerated (423 routes).

## Limitations (not hidden)

* The dashboard counts; it does not chart. The charts remain the legacy
  Charts Dashboard's concern until the document-operations wave rebuilds
  that page (D8) — the step-23 Comprehensive report is the analytical
  deep dive, not this overview.
* Schedule next-fire is per enabled schedule measured at request time; a
  paused schedule contributes "never" to the tile, not its hypothetical
  date.
* Artifact counts are row counts (files), not bytes; retention owns
  storage honesty (step 21).
* The catalog table is client-sorted; the runs table sorts server-side
  through the same component contract.
